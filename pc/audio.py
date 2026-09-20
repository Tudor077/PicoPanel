"""Per-application volume, so the knob can turn down Spotify and nothing else.

Windows has no such thing as "the volume of YouTube". What it has is one mixer
channel per PROCESS - the same list the volume mixer shows. So:

    Spotify   -> the Spotify.exe channel
    YouTube   -> the browser's channel, while a YouTube tab is what it's playing

The second one is an honest approximation and worth being clear about: if the
same browser plays YouTube in one tab and something else in another, they share
a single channel and move together. Windows gives us nothing finer. We label the
channel "YouTube" only when a window of that browser actually says YouTube,
otherwise it keeps the browser's own name - so the panel never claims to be
controlling something it isn't.

Nothing here raises. A machine with no sound card, a session that vanished
between listing and setting, COM having a bad day - all of it comes back as an
empty list or a False, because this runs in the app's background thread and a
volume knob is not worth a crash.
"""

import queue
import threading
import time

try:
    import comtypes
    from ctypes import POINTER, cast
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume, ISimpleAudioVolume
    HAVE_AUDIO = True
except Exception:                                   # pragma: no cover
    HAVE_AUDIO = False

try:
    import psutil
    import win32gui
    import win32process
    HAVE_TITLES = True
except Exception:                                   # pragma: no cover
    HAVE_TITLES = False


# Browsers get the YouTube treatment; everything else is named after itself.
BROWSERS = {
    "opera.exe": "Opera", "opera_gx.exe": "OperaGX", "chrome.exe": "Chrome",
    "msedge.exe": "Edge", "firefox.exe": "Firefox", "brave.exe": "Brave",
    "vivaldi.exe": "Vivaldi", "zen.exe": "Zen", "librewolf.exe": "LibreWolf",
}

# A few processes whose own name is not what you'd call them.
PRETTY = {
    "spotify.exe": "Spotify", "discord.exe": "Discord", "steam.exe": "Steam",
    "vlc.exe": "VLC", "aimp.exe": "AIMP", "foobar2000.exe": "foobar",
    "musicbee.exe": "MusicBee", "itunes.exe": "iTunes",
    "beamng.drive.x64.exe": "BeamNG", "warthunder.exe": "WarThunder",
    "eurotrucks2.exe": "ETS2", "flightsimulator.exe": "MSFS",
    "cs2.exe": "CS2", "steamwebhelper.exe": "Steam",
}

# Channels that exist but are not things anyone wants to turn down: Windows'
# own plumbing. Leaving them in would push the apps you care about further
# along the list for no reason.
SKIP = {"svchost.exe", "audiodg.exe", "system", "sihost.exe",
        "textinputhost.exe", "applicationframehost.exe"}

MAX_NAME = 10               # what fits on the panel next to a percentage

# Enumerating the mixer costs a few milliseconds of COM calls. The knob asks
# far more often than apps appear and disappear, so we keep the answer briefly.
CACHE_S = 1.0


def _pretty(procname):
    low = procname.lower()
    if low in PRETTY:
        return PRETTY[low]
    if low in BROWSERS:
        return BROWSERS[low]
    base = procname[:-4] if low.endswith(".exe") else procname
    return base[:MAX_NAME]


def _browser_titles():
    """{process name: [window titles]} for browsers that have a visible window."""
    if not HAVE_TITLES:
        return {}
    out = {}

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            name = psutil.Process(pid).name().lower()
        except Exception:
            return
        if name in BROWSERS:
            out.setdefault(name, []).append(title)

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        return {}
    return out


class Target:
    """One thing the knob can point at: the whole system, or one app."""

    def __init__(self, key, label, procname=None):
        self.key = key              # "system", or the lower-case process name
        self.label = label          # what the panel shows
        self.procname = procname    # None for the system target

    def __repr__(self):
        return "Target(%s, %r)" % (self.key, self.label)


class Mixer:
    """The list of targets, and the volume of each.

    Thread-safe and self-initialising for COM: the panel app calls this from its
    serial thread, and COM wants initialising on whichever thread touches it.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._targets = [Target("system", "Windows")]
        self._when = 0.0
        self._com_ready = set()
        self.error = "" if HAVE_AUDIO else "pycaw is missing"
        # The app's OWN slider comes first wherever there is one - that is the
        # one you can see moving. The mixer channel is the fallback for
        # everything that doesn't publish one.
        try:
            import appvolume
            self.inapp = appvolume.InAppVolume()
        except Exception:
            self.inapp = None
        self._inapp_saved = self.inapp

    # -- COM housekeeping -------------------------------------------------
    def _com(self):
        me = threading.get_ident()
        if me not in self._com_ready:
            try:
                comtypes.CoInitialize()
            except Exception:
                pass
            self._com_ready.add(me)

    # -- enumeration ------------------------------------------------------
    def _sessions(self):
        """[(process name lower, ISimpleAudioVolume)] for everything playing."""
        out = []
        try:
            for s in AudioUtilities.GetAllSessions():
                if not s.Process:
                    continue                    # system sounds: no app to name
                try:
                    name = s.Process.name().lower()
                except Exception:
                    continue
                out.append((name, s.SimpleAudioVolume))
        except Exception as e:
            self.error = "%s: %s" % (type(e).__name__, e)
        return out

    def refresh(self, force=False):
        """Rebuild the target list. Cheap to call often - it caches."""
        if not HAVE_AUDIO:
            return self._targets
        with self._lock:
            if not force and time.time() - self._when < CACHE_S:
                return self._targets
            self._com()
            self._when = time.time()

            names = []
            for name, _vol in self._sessions():
                if name not in names and name not in SKIP:
                    names.append(name)
            # Sorted, so the order doesn't reshuffle every time the mixer is
            # read. The panel still tracks its target by NAME - see index_of -
            # because an app closing does move everything after it.
            names.sort()

            titles = _browser_titles()
            targets = [Target("system", "Windows")]
            for name in names:
                label = _pretty(name)
                if name in BROWSERS:
                    # Only claim "YouTube" when the browser itself says so.
                    for t in titles.get(name, []):
                        if "youtube" in t.lower():
                            label = "YouTube"
                            break
                targets.append(Target(name, label, name))
            self._targets = targets
            return targets

    def targets(self):
        return self.refresh()

    def find(self, index):
        t = self.refresh()
        if not t:
            return None
        return t[index % len(t)]

    def count(self):
        return len(self.refresh())

    def set_use_inapp(self, on):
        """Turn the app's own slider on or off without losing the object."""
        self.inapp = self._inapp_saved if on else None

    def source(self, index):
        """"app" if the app's own slider is what moves, "mix" if the channel."""
        t = self.find(index)
        if t is None or not t.procname or self.inapp is None:
            return "mix"
        try:
            return "app" if self.inapp.available(t.procname) else "mix"
        except Exception:
            return "mix"

    # -- reading and writing ---------------------------------------------
    def _endpoint(self):
        """The master volume of the default output.

        pycaw changed what GetSpeakers() hands back: newer versions wrap the
        device and expose .EndpointVolume ready-made, older ones return the raw
        IMMDevice you have to Activate yourself. We take whichever is there
        rather than pinning a version.
        """
        self._com()
        dev = AudioUtilities.GetSpeakers()
        ep = getattr(dev, "EndpointVolume", None)
        if ep is not None:
            return ep
        iface = dev.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
        return cast(iface, POINTER(IAudioEndpointVolume))

    def level(self, index):
        """(volume 0..100, muted) for that target, or (None, False)."""
        t = self.find(index)
        if t is None or not HAVE_AUDIO:
            return None, False
        try:
            if t.procname is None:
                ep = self._endpoint()
                return (int(round(ep.GetMasterVolumeLevelScalar() * 100)),
                        bool(ep.GetMute()))
            # The app's own slider first. Mute has no equivalent there, so it
            # always comes from the channel - and muting the channel is better
            # anyway: it is instant and it doesn't throw away the app's level.
            own = self.inapp.get(t.procname) if self.inapp else None
            # An app can hold several channels (browsers always do). They are
            # kept in step by every write here, so the first one speaks for all.
            for name, vol in self._sessions():
                if name == t.procname:
                    return (own if own is not None
                            else int(round(vol.GetMasterVolume() * 100)),
                            bool(vol.GetMute()))
            if own is not None:
                return own, False
        except Exception as e:
            self.error = "%s: %s" % (type(e).__name__, e)
        return None, False

    def set_level(self, index, pct):
        """Set that target to 0..100. Returns the level actually set, or None."""
        t = self.find(index)
        if t is None or not HAVE_AUDIO:
            return None
        pct = max(0, min(100, int(pct)))
        v = pct / 100.0
        try:
            if t.procname is None:
                self._endpoint().SetMasterVolumeLevelScalar(v, None)
                return pct
            if self.inapp is not None:
                got = self.inapp.set(t.procname, pct)
                if got is not None:
                    return got
            touched = False
            for name, vol in self._sessions():
                if name == t.procname:
                    vol.SetMasterVolume(v, None)    # every channel of that app
                    touched = True
            return pct if touched else None
        except Exception as e:
            self.error = "%s: %s" % (type(e).__name__, e)
            return None

    def nudge(self, index, steps, step_pct=4):
        """Move by `steps` detents. Returns the new level, or None.

        An app's own slider has its own grid - Spotify snaps to ten steps of
        10% - so that case is handed to appvolume, which moves by whole steps
        and tracks what it asked for rather than reading back a value the app
        updates a beat late.
        """
        t = self.find(index)
        if t is not None and t.procname and self.inapp is not None:
            got = self.inapp.nudge(t.procname, steps)
            if got is not None:
                return got
        cur, _muted = self.level(index)
        if cur is None:
            return None
        return self.set_level(index, cur + steps * step_pct)

    def toggle_mute(self, index):
        """Returns the new muted state, or None."""
        t = self.find(index)
        if t is None or not HAVE_AUDIO:
            return None
        try:
            if t.procname is None:
                ep = self._endpoint()
                new = not bool(ep.GetMute())
                ep.SetMute(new, None)
                return new
            new, touched = None, False
            for name, vol in self._sessions():
                if name == t.procname:
                    if new is None:
                        new = not bool(vol.GetMute())
                    vol.SetMute(new, None)
                    touched = True
            return new if touched else None
        except Exception as e:
            self.error = "%s: %s" % (type(e).__name__, e)
            return None

    def index_of(self, label_or_key):
        """Find a target again after the list has shifted under it.

        Apps come and go, so an index is not a stable handle - the one that
        pointed at Spotify points at Discord the moment something closes. The
        panel therefore remembers the NAME and looks it up here.
        """
        want = str(label_or_key).lower()
        ts = self.refresh()
        for i, t in enumerate(ts):
            if t.key.lower() == want or t.label.lower() == want:
                return i
        return None


# ---------------------------------------------------------------------
# The bridge to the panel
# ---------------------------------------------------------------------

# The codes the firmware sends. Kept in step with the enum in PicoPanel.ino.
APP_UP, APP_DN, APP_PREV, APP_NEXT, APP_MUTE, APP_HOME = 1, 2, 3, 4, 5, 6

# The panel treats the target as gone if it hears nothing for four seconds, and
# falls back to the plain media keys. Twice a second keeps it confident and also
# sets the pace at which a track change reaches the screen. It costs nothing
# next to the telemetry stream.
HEARTBEAT_S = 0.5

_LINE_UNSAFE = str.maketrans("", "", ";=\r\n")


class AudioBridge:
    """Turns the panel's requests into mixer changes, and reports back.

    Runs on its own thread. The point is that the serial reader must not sit
    waiting on COM: a spin of the knob is twenty requests, and each one costs a
    few milliseconds of mixer calls. Reading the board is more urgent than that.

    The selected target is remembered by NAME, never by position. Applications
    come and go - close Discord and every index after it shifts by one - and a
    knob that silently started controlling something else would be worse than
    one that did nothing.
    """

    def __init__(self, send, log=None, selected=None, on_select=None):
        self.send = send                # send(str) -> writes a line to the board
        self.log = log or (lambda *_: None)
        self.mixer = Mixer()
        # Replaced by the app with something that knows which page is up.
        self.wants = lambda: True
        self.volume_wants = lambda: True
        self.last_touch = 0.0
        # None means "nobody has chosen yet", and that is not the same as
        # Windows: until you pick one, the knob follows whatever is actually
        # playing, so opening Spotify puts the knob on Spotify. The moment you
        # press a button the choice becomes yours and is remembered.
        self.selected = selected
        self.on_select = on_select or (lambda _label: None)
        # What's playing rides the same heartbeat: same thread, same link, and
        # the panel's MUSIC page is never more than half a second behind.
        try:
            import nowplaying
            self.now = nowplaying.NowPlaying()
            self._np_line = nowplaying.to_line
        except Exception:
            self.now = None
            self._np_line = lambda _s: None
        # The board's font is ASCII, so anything else is drawn HERE and sent as
        # pixels. Only on a change: a rendered title is ~250 bytes and the
        # heartbeat runs twice a second.
        try:
            import textstrip
            self._strip = textstrip.line
            self._strip_fields = textstrip.fields
        except Exception:
            self._strip = lambda _k, _t: None
            self._strip_fields = lambda _t: None
        self._sent_strips = (None, None)
        self.unicode_titles = True      # the app overrides this from settings
        # Karaoke. Off until the app turns it on: it is the one thing here that
        # leaves the machine.
        try:
            import lyrics
            self.lyrics = lyrics.Lyrics(enabled=False)
            self._line_at = lyrics.line_at
        except Exception:
            self.lyrics = None
            self._line_at = None
        self._sent_lyric = None
        self._q = queue.Queue()
        self._stop = threading.Event()
        self._thread = None

    # -- lifecycle --------------------------------------------------------
    def start(self):
        if self._thread:
            return
        if self.now:
            self.now.start()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="audio")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self.now:
            self.now.stop()
        if self._thread:
            self._thread.join(timeout=1.5)
            self._thread = None

    def request(self, code):
        """Called from the serial reader. Returns at once."""
        # Touching the volume counts as looking at the music: the level is on
        # screen for a few seconds afterwards, and so is what is playing.
        self.last_touch = time.time()
        self._q.put(int(code))

    # -- the work ---------------------------------------------------------
    def _playing_index(self):
        """The mixer target of whatever is playing, if we can tell."""
        snap = self.now.snapshot() if self.now else None
        if not snap:
            return None
        # A browser's media session is named after the browser's package, which
        # looks nothing like its process. Its label is the way across.
        if snap.get("label") == "YouTube":
            i = self.mixer.index_of("YouTube")
            if i is not None:
                return i
        app = (snap.get("app") or "").lower()
        if app:
            i = self.mixer.index_of(app)
            if i is not None:
                return i
        return None

    def _index(self):
        """Where the target sits right now, 0 if it has gone."""
        if self.selected is None:
            i = self._playing_index()
            return 0 if i is None else i
        i = self.mixer.index_of(self.selected)
        return 0 if i is None else i

    def _pick(self, label):
        self.selected = label
        self.on_select(label)

    def _move(self, by):
        ts = self.mixer.targets()
        if not ts:
            return
        i = (self._index() + by) % len(ts)
        self._pick(ts[i].label)

    def _apply(self, code):
        if code == APP_HOME:
            self._pick("Windows")
        elif code == APP_PREV:
            self._move(-1)
        elif code == APP_NEXT:
            self._move(+1)
        elif code == APP_MUTE:
            self.mixer.toggle_mute(self._index())
        elif code == APP_UP:
            self.mixer.nudge(self._index(), +1)
        elif code == APP_DN:
            self.mixer.nudge(self._index(), -1)

    def status_line(self):
        ts = self.mixer.targets()
        i = self._index()
        label = ts[i].label if ts else "?"
        vol, muted = self.mixer.level(i)
        return "%%au=%d/%d;nm=%s;vl=%d;mu=%d;sr=%s" % (
            i + 1, len(ts),
            label.translate(_LINE_UNSAFE)[:MAX_NAME],
            -1 if vol is None else vol,
            1 if muted else 0,
            self.mixer.source(i))

    def _send_strips(self, snap):
        """The title and artist as pixels, when either has changed.

        Keyed on the ORIGINAL text, not the transliterated one: two different
        Russian titles can transliterate to the same thing, and the point of the
        strip is that it shows what was actually written.
        """
        if not self.unicode_titles:
            if self._sent_strips != ("", ""):
                # Turned off while a strip was on screen: clear it, or the board
                # would keep drawing pixels the setting says it shouldn't.
                self._sent_strips = ("", "")
                self.send("%ts=1;w=0;d=")
                self.send("%ts=2;w=0;d=")
            return
        want = ((snap or {}).get("raw_title") or "",
                (snap or {}).get("raw_artist") or "")
        if want == self._sent_strips:
            return
        self._sent_strips = want
        for kind, text in ((1, want[0]), (2, want[1])):
            line = self._strip(kind, text) if text else None
            if line:
                self.send(line)

    # 0 off, 1 words to show, 2 this track has none, 3 still asking.
    def _karaoke_state(self, snap):
        if not self.lyrics or not self.lyrics.enabled or not snap:
            return 0
        got = self.lyrics.get(snap.get("raw_artist"), snap.get("raw_title"),
                              snap.get("dur"))
        if got is None:
            return 3
        return 1 if got else 2

    def _send_lyrics(self, snap):
        """The line being sung and the one after, each with when it starts.

        Sent on a CHANGE of line, not on every heartbeat: each is a rendered
        strip of a couple of hundred bytes. The board does the switching from
        the timestamps, so it turns the line over on its own clock instead of
        waiting for the next of these.
        """
        if not self.lyrics or not self.lyrics.enabled or not snap:
            return
        lines = self.lyrics.get(snap.get("raw_artist"), snap.get("raw_title"),
                                snap.get("dur"))
        if not lines:
            return                       # still looking, or this track has none
        i = self._line_at(lines, float(snap.get("pos") or 0.0))
        key = (id(lines), i)
        if key == self._sent_lyric:
            return
        self._sent_lyric = key

        def strip(kind, at_s, text):
            body = self._strip_fields(text) if text else None
            if body is None:
                # A blank line between verses still has to be sent, or the board
                # would keep singing the last one through the instrumental.
                body = "w=0;d="
            return "%%ky=%d;at=%d;%s" % (kind, int(at_s * 1000), body)

        cur = lines[i] if i >= 0 else (0.0, "")
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        self.send(strip(1, cur[0], cur[1]))
        self.send(strip(2, nxt[0], nxt[1]) if nxt
                  else "%ky=2;at=0;w=0;d=")

    def wanted(self):
        """Is what's playing worth reading at all right now?

        `wants` is set by the app from the page the board is on. Without it
        this thread read the media session four times a second whatever was
        on screen - and rendered a title strip every time one changed - to
        feed a page nobody was looking at.
        """
        try:
            return bool(self.wants())
        except Exception:
            return True

    def _run(self):
        while not self._stop.is_set():
            try:
                code = self._q.get(timeout=HEARTBEAT_S)
            except queue.Empty:
                code = None             # the heartbeat: just re-state where we are
            if code is not None:
                # A fast spin queues up a pile of detents. Apply them all before
                # reporting, or the panel's bar crawls a step behind the knob.
                codes = [code]
                while len(codes) < 32:
                    try:
                        codes.append(self._q.get_nowait())
                    except queue.Empty:
                        break
                for c in codes:
                    try:
                        self._apply(c)
                    except Exception as e:
                        self.log("audio: %s: %s" % (type(e).__name__, e))
            want = self.wanted()
            if self.now:
                self.now.want(want)
            # The volume line costs 15 ms to build - a COM call per app in the
            # mixer - and it is twice a second for ever. On a page that does
            # not show it, once every three seconds is plenty: the board's own
            # copy is only there so it can be drawn the moment you arrive.
            hot = True
            try:
                hot = bool(self.volume_wants())
            except Exception:
                pass
            self._beat = getattr(self, "_beat", 0) + 1
            try:
                if hot or code is not None or self._beat % 6 == 0:
                    self.send(self.status_line())
                if self.now and want:
                    snap = self.now.snapshot()
                    line = self._np_line(snap)
                    if line:
                        # The karaoke state rides on this line, which goes out
                        # every heartbeat. The strips themselves only go when
                        # the words change - so through a long instrumental
                        # nothing was sent at all, and the board timed the
                        # words out and said karaoke was off over a song that
                        # was playing perfectly well. Silence is not an answer;
                        # this says so explicitly.
                        self.send(line + ";ka=%d" % self._karaoke_state(snap))
                    self._send_strips(snap)
                    self._send_lyrics(snap)
            except Exception:
                pass                    # the board went away; not our problem


if __name__ == "__main__":
    m = Mixer()
    for i, t in enumerate(m.targets()):
        vol, mute = m.level(i)
        print("%d  %-10s %-22s %s%s" % (i, t.label, t.key,
                                        "--" if vol is None else "%3d%%" % vol,
                                        "  [muted]" if mute else ""))
    if m.error:
        print("error:", m.error)
