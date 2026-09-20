#!/usr/bin/env python3
"""
build.py - packs PicoPanel into a single .exe.

    python build.py

The result lands at Projects\\PicoPanel\\PicoPanel.exe. The intermediate files
stay in the temp folder, not in the project.

Why --onefile: this is a program you start once and forget about in the
tray, so an extra second at startup doesn't matter. In exchange, one file
means the Startup shortcut can't break because a DLL next to it vanished.
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NAME = "PicoPanel"
OUT = os.path.join(ROOT, NAME + ".exe")


def make_icon(path):
    """The same drawing the tray uses - see icon.py."""
    import icon
    return icon.save_ico(path)


def main():
    tmp = os.path.join(tempfile.gettempdir(), "picopanel_build")
    os.makedirs(tmp, exist_ok=True)
    icon = make_icon(os.path.join(tmp, "picopanel.ico"))

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--windowed",                 # no console: it's a tray app
        "--name", NAME,
        "--icon", icon,
        "--workpath", os.path.join(tmp, "work"),
        "--distpath", os.path.join(tmp, "dist"),
        "--specpath", tmp,
        # Imports static analysis can't see: win32com.client is only loaded
        # inside autostart.enable(), and the Windows serial backend is picked
        # at runtime from the platform name.
        "--hidden-import", "win32com.client",
        "--hidden-import", "serial.tools.list_ports",
        "--hidden-import", "serial.serialwin32",
        # Imported inside Link.connect(), only when you pick the emulator
        # port. Named explicitly so the exe can never ship without it.
        "--hidden-import", "fakepanel",
        # Per-application volume. pycaw reaches the mixer through COM
        # interfaces that comtypes builds at run time, so static analysis sees
        # nothing; without these the exe starts and silently has no audio.
        "--hidden-import", "audio",
        "--hidden-import", "pycaw.pycaw",
        "--hidden-import", "comtypes.gen",
        "--hidden-import", "psutil",
        "--hidden-import", "win32gui",
        "--hidden-import", "win32process",
        # What's playing comes from the Windows media sessions, through the
        # winrt projection - also built at run time, also invisible to static
        # analysis.
        "--hidden-import", "nowplaying",
        "--hidden-import", "winrt.windows.media.control",
        "--hidden-import", "winrt.windows.foundation",
        "--hidden-import", "winrt.system",
        "--hidden-import", "telemetry.text",
        # Titles are drawn here with a real font and sent as pixels, which is
        # how Cyrillic reaches a panel whose own font is ASCII.
        "--hidden-import", "textstrip",
        "--hidden-import", "icon",
        # Synced lyrics for the karaoke page.
        "--hidden-import", "lyrics",
        # The widget library and its editor.
        "--hidden-import", "widgets",
        "--hidden-import", "editor",
        "--hidden-import", "PIL.ImageFont",
        "--hidden-import", "PIL.ImageDraw",
        # UI Automation, for an app's own volume slider. comtypes builds the
        # wrapper at run time into a writable cache, which works frozen too -
        # verified - but the import itself is still invisible to the analyser.
        "--hidden-import", "appvolume",
        "--hidden-import", "comtypes.client",
        os.path.join(HERE, "panel.py"),
    ]
    print(" ".join(cmd), "\n")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit("PyInstaller failed")

    built = os.path.join(tmp, "dist", NAME + ".exe")
    shutil.copy2(built, OUT)
    mb = os.path.getsize(OUT) / 1048576
    print(f"\ndone: {OUT}  ({mb:.1f} MB)")


if __name__ == "__main__":
    main()
