"""The application's OWN volume - the slider inside Spotify, not the mixer.

Windows' mixer gives every process a channel, and that channel really is a
per-application volume. But it is a second attenuation stacked on top: turn
Spotify down that way and Spotify's own slider still reads full. If what you
want is the slider in the app, you have to move the slider in the app.

There are three ways to do that and only one of them is any good:

  * Keystrokes. Spotify and every browser are Chromium windows, and Chromium
    ignores posted key messages - it wants real input, which means focusing the
    window. A volume knob that pulls you out of the game is not a volume knob.
  * The Spotify Web API. Moves the real slider, needs a registered app, an OAuth
    login and Premium.
  * UI Automation. Chromium publishes its accessibility tree, and Spotify's
    volume slider is in it as "Change volume" with a RangeValue pattern. We set
    the value straight through that: no focus change, no token, 3-40 ms.

The third is what this does. Measured on Spotify: the slider moves, focus stays
where it was, and the app snaps the value to ten steps of 10% - which is why a
detent here moves by ten, not by four.

Anything the app does not expose - Discord, a game - has no slider to move, and
the caller falls back to the mixer channel.

One cost worth knowing: asking for the accessibility tree makes Chromium build
and maintain one. It is not free for the app, though nothing measurable showed
up here.
"""

import threading
import time

try:
    import comtypes
    import comtypes.client
    import psutil
    import win32api
    import win32con
    import win32gui
    import win32process
    HAVE_UIA = True
except Exception:                                   # pragma: no cover
    HAVE_UIA = False

# Spotify's slider snaps to ten steps. Four percent a detent would leave the
# knob doing nothing most of the time.
STEP = 10

# Finding the slider costs about 40 ms, so the element is kept. An app with no
# slider at all is only re-checked this often, or every detent would pay for a
# search that is going to fail again.
RETRY_S = 5.0


# The shell's own windows cover the whole screen and are never maximised - the
# desktop itself is one of them - so they would read as a game every time you
# clicked on the wallpaper.
_SHELL = {"explorer.exe", "textinputhost.exe", "shellexperiencehost.exe",
          "searchhost.exe", "startmenuexperiencehost.exe",
          "applicationframehost.exe", "lockapp.exe"}


def _fullscreen_in_front(procname):
    """Is a DIFFERENT application filling the screen right now?

    Reaching into another process' UI is a cross-process call that makes it
    build and maintain an accessibility tree. Do that behind a game and you get
    thrown back to the desktop. If the window in front IS the app we are about
    to touch, there is nothing to interrupt and this says no.
    """
    if not HAVE_UIA:
        return False
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return False
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        front = psutil.Process(pid).name().lower()
        if front == (procname or "").lower():
            return False
        if front in _SHELL:
            return False
        # Maximised is not fullscreen. A maximised window's rect overhangs the
        # monitor by the border width, so measuring area alone calls every
        # maximised browser a game; IsZoomed tells them apart. A borderless
        # fullscreen game is a RESTORED window sized to the screen, and so is
        # an exclusive one.
        # (GetWindowPlacement, because this pywin32 has no IsZoomed.)
        if win32gui.GetWindowPlacement(hwnd)[1] == win32con.SW_SHOWMAXIMIZED:
            return False
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        mon = win32api.GetMonitorInfo(
            win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST))
        ml, mt, mr, mb = mon["Monitor"]
        # Clipped to the monitor, so overhang can't inflate the count either.
        w = max(0, min(r, mr) - max(l, ml))
        h = max(0, min(b, mb) - max(t, mt))
        screen = max(1, (mr - ml)) * max(1, (mb - mt))
        # 98%, not 100%: a borderless window is often a pixel or two out.
        return w * h >= screen * 0.98
    except Exception:
        return False


class InAppVolume:
    """Per-application volume through the app's own UI.

    UI Automation objects belong to the thread that made them, so everything
    here is created lazily on whichever thread calls first and never handed
    across. In the app that is the audio thread, and only that one.
    """

    def __init__(self):
        self.error = "" if HAVE_UIA else "comtypes is missing"
        self._lock = threading.RLock()
        self._uia = None
        self._uia_thread = None
        self._mod = None
        self._cache = {}        # procname -> RangeValue pattern, or None
        self._tried = {}        # procname -> when we last looked
        self._want = {}         # procname -> the level we last asked for

    # -- setting up -------------------------------------------------------
    def _client(self):
        """The UIA client for this thread, or None."""
        if not HAVE_UIA:
            return None
        me = threading.get_ident()
        if self._uia is not None and self._uia_thread == me:
            return self._uia
        try:
            try:
                comtypes.CoInitialize()
            except Exception:
                pass
            comtypes.client.GetModule("UIAutomationCore.dll")
            import comtypes.gen.UIAutomationClient as mod
            self._mod = mod
            self._uia = comtypes.client.CreateObject(
                mod.CUIAutomation, interface=mod.IUIAutomation)
            self._uia_thread = me
            # A fresh client means the cached elements came from the old one.
            self._cache.clear()
            return self._uia
        except Exception as e:
            self.error = "%s: %s" % (type(e).__name__, e)
            return None

    # -- finding the slider ----------------------------------------------
    def _windows_of(self, procname):
        """UIA elements for that app's visible top-level windows.

        The handles come from EnumWindows, NOT from walking the UIA tree. That
        walk asks every application on the desktop to describe itself and waits
        for the slow ones: measured at 7.2 SECONDS here against 12 ms for the
        same list out of EnumWindows. ElementFromHandle then costs nothing,
        because it talks to one process instead of all of them.
        """
        uia = self._uia
        hwnds = []

        def cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd) or not win32gui.GetWindowText(hwnd):
                return
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if psutil.Process(pid).name().lower() == procname:
                    hwnds.append(hwnd)
            except Exception:
                pass

        try:
            win32gui.EnumWindows(cb, None)
        except Exception:
            return []

        out = []
        for hwnd in hwnds:
            try:
                el = uia.ElementFromHandle(hwnd)
                if el:
                    out.append(el)
            except Exception:
                pass
        return out

    def _find(self, procname):
        """The RangeValue pattern of that app's volume slider, or None."""
        uia = self._client()
        if uia is None:
            return None
        mod = self._mod
        sliders = uia.CreatePropertyCondition(mod.UIA_ControlTypePropertyId,
                                              mod.UIA_SliderControlTypeId)
        for win in self._windows_of(procname):
            try:
                found = win.FindAll(mod.TreeScope_Descendants, sliders)
            except Exception:
                continue
            for i in range(found.Length):
                el = found.GetElement(i)
                try:
                    name = (el.CurrentName or "").lower()
                except Exception:
                    continue
                # "Change volume" in Spotify, "Volume" in a web player. Matching
                # on the word rather than a fixed string means an app we have
                # never seen works without being added to a list.
                if "volume" not in name:
                    continue
                try:
                    rv = el.GetCurrentPattern(mod.UIA_RangeValuePatternId)
                    rv = rv.QueryInterface(mod.IUIAutomationRangeValuePattern)
                    rv.CurrentValue                  # prove it answers
                    return rv
                except Exception:
                    continue
        return None

    def _pattern(self, procname, force=False):
        with self._lock:
            procname = (procname or "").lower()
            if not procname:
                return None
            if not force and procname in self._cache:
                rv = self._cache[procname]
                if rv is not None:
                    return rv
                if time.time() - self._tried.get(procname, 0) < RETRY_S:
                    return None            # no slider, and we looked recently
            rv = self._find(procname)
            self._cache[procname] = rv
            self._tried[procname] = time.time()
            return rv

    # -- the interface the mixer uses -------------------------------------
    def available(self, procname):
        return self._pattern(procname) is not None

    def get(self, procname, live=False):
        """0..100, or None if this app has no slider of its own.

        By default this answers from what we last set or read. It used to go and
        ask every time, which sounds harmless until you count it: the panel's
        heartbeat runs twice a second, so that was two cross-process calls a
        second into Spotify FOR EVER, keeping Chromium's accessibility tree
        awake behind whatever you were playing. Now the app is only asked when
        there is nothing remembered, or when somebody actually turns the knob.
        """
        key = (procname or "").lower()
        if not live:
            with self._lock:
                if key in self._want:
                    return self._want[key]
        if _fullscreen_in_front(procname):
            return None                    # not while a game owns the screen
        for force in (False, True):        # a stale element gets one re-find
            rv = self._pattern(procname, force)
            if rv is None:
                return None
            try:
                v = int(round(rv.CurrentValue * 100))
                with self._lock:
                    self._want[key] = v
                return v
            except Exception:
                with self._lock:
                    self._cache.pop((procname or "").lower(), None)
        return None

    def set(self, procname, pct):
        """Set 0..100. Returns what we asked for, or None."""
        pct = max(0, min(100, int(pct)))
        if _fullscreen_in_front(procname):
            return None                    # the mixer channel will take it
        for force in (False, True):
            rv = self._pattern(procname, force)
            if rv is None:
                return None
            try:
                rv.SetValue(pct / 100.0)
                with self._lock:
                    self._want[(procname or "").lower()] = pct
                return pct
            except Exception:
                with self._lock:
                    self._cache.pop((procname or "").lower(), None)
        return None

    def nudge(self, procname, steps):
        """Move by whole slider steps. Returns the new level, or None.

        Driven from what we last ASKED for, not from what the app reports. Two
        reasons: the app snaps to its own grid, and it updates the reported
        value a beat late - so reading back between detents would make a quick
        spin crawl or stall. If the app's value has drifted more than one step
        from our intention, somebody moved the slider by hand and we start from
        theirs instead.
        """
        key = (procname or "").lower()
        # A real read here, once per turn of the knob: this is the moment it is
        # worth knowing whether somebody moved the slider by hand.
        cur = self.get(procname, live=True)
        if cur is None:
            return None
        with self._lock:
            want = self._want.get(key)
        if want is None or abs(cur - want) > STEP:
            want = cur
        return self.set(procname, want + steps * STEP)


if __name__ == "__main__":
    import sys
    v = InAppVolume()
    who = sys.argv[1] if len(sys.argv) > 1 else "spotify.exe"
    t0 = time.perf_counter()
    lvl = v.get(who)
    print("%s: %s  (first lookup %.0f ms)"
          % (who, "no slider of its own" if lvl is None else "%d%%" % lvl,
             (time.perf_counter() - t0) * 1000))
    if lvl is not None:
        t0 = time.perf_counter()
        for _ in range(3):
            v.get(who)
        print("cached reads: %.1f ms each" % ((time.perf_counter() - t0) * 1000 / 3))
    if v.error:
        print("error:", v.error)
