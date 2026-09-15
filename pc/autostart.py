"""Starting automatically at logon, through a shortcut in the Startup folder.

The Startup folder, not the registry's Run key: a shortcut is visible, and you
can move or delete it from Explorer like any other file, and Task Manager lists
it by name. A hidden registry entry is exactly the kind of thing you forget and
then wonder, a year later, what is starting it.

We launch with pythonw.exe, not python.exe - that's all it takes to stop the
black console window from appearing.
"""

import os
import sys

NAME = "PicoPanel.lnk"


def startup_dir():
    return os.path.join(os.environ["APPDATA"],
                        r"Microsoft\Windows\Start Menu\Programs\Startup")


def link_path():
    return os.path.join(startup_dir(), NAME)


def is_frozen():
    """Are we running from the PyInstaller .exe?"""
    return bool(getattr(sys, "frozen", False))


def pythonw():
    """The console-less interpreter, next to the current one."""
    exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return exe if os.path.exists(exe) else sys.executable


def app_script():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "panel.py"))


def target_args():
    """What the Startup shortcut should actually launch.

    From the .exe we launch the executable itself. From source we launch pythonw
    with the script - putting sys.executable here as well would make the
    shortcut call python.exe and pop up the black window at every logon.
    """
    if is_frozen():
        return sys.executable, "--hidden", os.path.dirname(sys.executable)
    return pythonw(), '"%s" --hidden' % app_script(), os.path.dirname(app_script())


def is_enabled():
    return os.path.exists(link_path())


def enable():
    """Creates the shortcut. Returns (ok, message)."""
    try:
        import win32com.client
    except ImportError:
        return False, "pywin32 is missing (pip install pywin32)"
    try:
        target, args, workdir = target_args()
        sh = win32com.client.Dispatch("WScript.Shell")
        lnk = sh.CreateShortCut(link_path())
        lnk.TargetPath = target
        lnk.Arguments = args
        lnk.WorkingDirectory = workdir
        lnk.Description = "PicoPanel - panel telemetry and control"
        lnk.Save()
        return True, link_path()
    except Exception as e:
        return False, str(e)


def disable():
    try:
        if os.path.exists(link_path()):
            os.remove(link_path())
        return True, "deleted"
    except OSError as e:
        return False, str(e)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "on":
        print(enable())
    elif cmd == "off":
        print(disable())
    else:
        t, a, w = target_args()
        print(f"start at logon: {'YES' if is_enabled() else 'no'}")
        print(f"  mode     : {'exe' if is_frozen() else 'source'}")
        print(f"  shortcut : {link_path()}")
        print(f"  would run: {t} {a}")
