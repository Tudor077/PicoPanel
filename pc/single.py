"""One copy at a time.

The serial port is exclusive, so a second copy of the program cannot do
anything except sit there printing "access is denied" at the first one's port -
which is exactly what it did, twice, in a log that gave no hint that the cause
was another PicoPanel rather than a broken cable.

A named mutex is the smallest thing that actually prevents it. It is held by the
kernel for as long as the process lives, so it cannot be left behind by a crash
the way a lock file can.
"""

NAME = "Local\\PicoPanel-single-instance"

_handle = None


def claim():
    """True if we are the only copy. False means one is already running."""
    global _handle
    try:
        import win32event
        import winerror
    except Exception:                               # pragma: no cover
        return True                                 # can't tell: carry on
    try:
        _handle = win32event.CreateMutex(None, False, NAME)
        import win32api
        return win32api.GetLastError() != winerror.ERROR_ALREADY_EXISTS
    except Exception:
        return True


def wake_the_other():
    """Bring the copy that IS running to the front, so clicking the shortcut
    twice does the obvious thing instead of nothing."""
    try:
        import win32con
        import win32gui
    except Exception:                               # pragma: no cover
        return
    hits = []

    def cb(hwnd, _):
        if win32gui.IsWindow(hwnd) and win32gui.GetWindowText(hwnd) == "PicoPanel":
            hits.append(hwnd)

    try:
        win32gui.EnumWindows(cb, None)
        for hwnd in hits:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
