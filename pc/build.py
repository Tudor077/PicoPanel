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
    """The same icon as in the tray, drawn larger so it also looks right in
    Explorer, where Windows asks for it at 256 px."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([16, 48, 240, 208], radius=24,
                        fill=(32, 34, 40), outline=(120, 130, 145), width=6)
    d.rectangle([48, 80, 208, 144], fill=(64, 196, 122))
    d.ellipse([48, 160, 80, 192], fill=(150, 160, 175))
    d.ellipse([104, 160, 136, 192], fill=(150, 160, 175))
    d.ellipse([160, 160, 192, 192], fill=(230, 120, 90))
    img.save(path, format="ICO",
             sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
    return path


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
