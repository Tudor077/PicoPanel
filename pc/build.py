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
        "--windowed",
        "--name", NAME,
        "--icon", icon,
        "--workpath", os.path.join(tmp, "work"),
        "--distpath", os.path.join(tmp, "dist"),
        "--specpath", tmp,
        "--hidden-import", "win32com.client",
        "--hidden-import", "serial.tools.list_ports",
        "--hidden-import", "serial.serialwin32",
        "--hidden-import", "fakepanel",
        "--hidden-import", "audio",
        "--hidden-import", "pycaw.pycaw",
        "--hidden-import", "comtypes.gen",
        "--hidden-import", "psutil",
        "--hidden-import", "win32gui",
        "--hidden-import", "win32process",
        "--hidden-import", "nowplaying",
        "--hidden-import", "winrt.windows.media.control",
        "--hidden-import", "winrt.windows.foundation",
        "--hidden-import", "winrt.system",
        "--hidden-import", "telemetry.text",
        "--hidden-import", "textstrip",
        "--hidden-import", "icon",
        "--hidden-import", "lyrics",
        "--hidden-import", "widgets",
        "--hidden-import", "editor",
        "--hidden-import", "single",
        "--hidden-import", "theme",
        "--hidden-import", "pagelist",
        "--hidden-import", "sticks",
        "--hidden-import", "phone",
        "--hidden-import", "PIL.ImageFont",
        "--hidden-import", "PIL.ImageDraw",
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
