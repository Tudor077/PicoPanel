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
    """Aceeasi iconita ca in tray, desenata mai mare ca sa arate bine si in
    Explorer, unde Windows o cere la 256 px."""
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
        "--windowed",                 # fara consola: e aplicatie de tray
        "--name", NAME,
        "--icon", icon,
        "--workpath", os.path.join(tmp, "work"),
        "--distpath", os.path.join(tmp, "dist"),
        "--specpath", tmp,
        # Importuri pe care analiza statica nu le vede: win32com.client e
        # incarcat abia in autostart.enable(), iar backend-ul serial pentru
        # Windows se alege la rulare, dupa numele platformei.
        "--hidden-import", "win32com.client",
        "--hidden-import", "serial.tools.list_ports",
        "--hidden-import", "serial.serialwin32",
        os.path.join(HERE, "panel.py"),
    ]
    print(" ".join(cmd), "\n")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit("PyInstaller a esuat")

    built = os.path.join(tmp, "dist", NAME + ".exe")
    shutil.copy2(built, OUT)
    mb = os.path.getsize(OUT) / 1048576
    print(f"\ndone: {OUT}  ({mb:.1f} MB)")


if __name__ == "__main__":
    main()
