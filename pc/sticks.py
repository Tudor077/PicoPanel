"""The axes of whatever stick, wheel or pad is plugged in.

Windows' own joystick API, through ctypes - no dependency, and it sees anything
with a driver, which is what you want when the question is "is my wheel
centred". The alternative was pygame or a DirectInput wrapper: a package the
size of the rest of the app, for six numbers.

Which device? The one that moved last. A machine here has two - the panel is
itself a gamepad - and asking which is which in a settings box would be a
question with no good answer. Wiggle the thing you mean and the widget follows
it; until something moves, it is the lowest-numbered one.

The axes come out as -1..+1 with 0 in the middle, because the question this
exists to answer is how far from the middle something is.
"""

import ctypes
import ctypes.wintypes as wt
import time

# A hair over one frame of the 15 Hz page sender, so a burst of calls inside
# one frame costs one poll. Each poll is a few microseconds anyway - this is
# to keep it that way when a page has four of these on it.
CACHE_S = 0.05

# How far from the middle counts as moved, on a -1..+1 axis. Sticks rest a
# pixel or two off centre and jitter there; this is well under any deadzone
# worth drawing.
MOVED = 0.02

JOYERR_NOERROR = 0
JOY_RETURNALL = 0xFF


class JOYCAPS(ctypes.Structure):
    _fields_ = [("wMid", wt.WORD), ("wPid", wt.WORD), ("szPname", wt.WCHAR * 32),
                ("wXmin", wt.UINT), ("wXmax", wt.UINT), ("wYmin", wt.UINT),
                ("wYmax", wt.UINT), ("wZmin", wt.UINT), ("wZmax", wt.UINT),
                ("wNumButtons", wt.UINT), ("wPeriodMin", wt.UINT),
                ("wPeriodMax", wt.UINT), ("wRmin", wt.UINT), ("wRmax", wt.UINT),
                ("wUmin", wt.UINT), ("wUmax", wt.UINT), ("wVmin", wt.UINT),
                ("wVmax", wt.UINT), ("wCaps", wt.UINT), ("wMaxAxes", wt.UINT),
                ("wNumAxes", wt.UINT), ("wMaxButtons", wt.UINT),
                ("szRegKey", wt.WCHAR * 32), ("szOEMVxD", wt.WCHAR * 260)]


class JOYINFOEX(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("dwFlags", wt.DWORD),
                ("dwXpos", wt.DWORD), ("dwYpos", wt.DWORD), ("dwZpos", wt.DWORD),
                ("dwRpos", wt.DWORD), ("dwUpos", wt.DWORD), ("dwVpos", wt.DWORD),
                ("dwButtons", wt.DWORD), ("dwButtonNumber", wt.DWORD),
                ("dwPOV", wt.DWORD), ("dwReserved1", wt.DWORD),
                ("dwReserved2", wt.DWORD)]


def _span(lo, hi, v):
    """A raw axis reading as -1..+1."""
    if hi <= lo:
        return 0.0
    return max(-1.0, min(1.0, (float(v) - lo) / (hi - lo) * 2.0 - 1.0))


class Sticks:
    """Poll on demand; nothing runs when nothing asks."""

    def __init__(self):
        try:
            self.mm = ctypes.WinDLL("winmm")
        except OSError:
            self.mm = None          # not Windows, or no winmm: no axes, no fuss
        self.caps = {}              # id -> JOYCAPS, for the axis ranges
        self._scanned = 0.0
        self._last = {}             # id -> the axes we saw last time
        self._moved = {}            # id -> when it last actually moved
        self._at = 0.0
        self._out = {"joy_x": 0.0, "joy_y": 0.0, "joy_z": 0.0, "joy_r": 0.0,
                     "joy_id": None}

    # ------------------------------------------------------------------ scan
    def _scan(self, now):
        """Which sticks exist. Re-asked every few seconds: one can be plugged
        in while the app is running, and it should simply start working."""
        if self.mm is None or now - self._scanned < 5.0:
            return
        self._scanned = now
        found = {}
        for i in range(16):
            caps = JOYCAPS()
            if self.mm.joyGetDevCapsW(i, ctypes.byref(caps),
                                      ctypes.sizeof(caps)) != JOYERR_NOERROR:
                continue
            info = JOYINFOEX()
            info.dwSize = ctypes.sizeof(info)
            info.dwFlags = JOY_RETURNALL
            if self.mm.joyGetPosEx(i, ctypes.byref(info)) != JOYERR_NOERROR:
                continue            # a slot with a driver but nothing in it
            found[i] = caps
        self.caps = found

    # ------------------------------------------------------------------ read
    def read(self):
        """The chosen stick's axes, as a dict to fold into the widget data."""
        now = time.time()
        if now - self._at < CACHE_S:
            return self._out
        self._at = now
        self._scan(now)
        if self.mm is None or not self.caps:
            self._out = {"joy_x": 0.0, "joy_y": 0.0, "joy_z": 0.0,
                         "joy_r": 0.0, "joy_id": None}
            return self._out

        axes = {}
        for i, caps in self.caps.items():
            info = JOYINFOEX()
            info.dwSize = ctypes.sizeof(info)
            info.dwFlags = JOY_RETURNALL
            if self.mm.joyGetPosEx(i, ctypes.byref(info)) != JOYERR_NOERROR:
                continue
            a = (_span(caps.wXmin, caps.wXmax, info.dwXpos),
                 _span(caps.wYmin, caps.wYmax, info.dwYpos),
                 _span(caps.wZmin, caps.wZmax, info.dwZpos),
                 _span(caps.wRmin, caps.wRmax, info.dwRpos))
            axes[i] = a
            was = self._last.get(i)
            if was is not None and any(abs(n - o) > MOVED for n, o in zip(a, was)):
                self._moved[i] = now
            self._last[i] = a

        if not axes:
            return self._out
        pick = max(axes, key=lambda i: (self._moved.get(i, 0.0), -i))
        x, y, z, r = axes[pick]
        self._out = {"joy_x": x, "joy_y": y, "joy_z": z, "joy_r": r,
                     "joy_id": pick}
        return self._out
