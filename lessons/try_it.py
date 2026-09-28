"""Lesson 1 - draw something on the panel.

Run it:

    python lessons/try_it.py

It draws whatever is in my_widget() onto a real 128x32 panel screen, saves
lessons/out.png and opens it. No board, no app, no emulator, no rebuild.

Keep it open while you work:

    python lessons/try_it.py --watch

Then every time you save this file it redraws by itself. Edit, Ctrl+S, look.
"""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "pc"))

from PIL import Image
import widgets as WG


# ===========================================================================
#  YOUR BIT. Everything above and below is plumbing.
# ===========================================================================
#
#  d     is the pen. It draws on the panel.
#  x, y  is where you are. x goes right 0..127, y goes down 0..31.
#        y = 0 is the TOP, which trips everyone up once.
#  fill=1  means lit.  fill=0  means dark.  There is no third colour.
#
#  Four things the pen can do:
#
#     d.line([x1, y1, x2, y2], fill=1)                 a straight line
#     d.rectangle([x1, y1, x2, y2], outline=1)         a box
#     d.rectangle([x1, y1, x2, y2], fill=1)            a filled box
#     d.ellipse([x1, y1, x2, y2], outline=1)           a circle
#     d.text((x, y), "HELLO", font=WG._font(9), fill=1)
#
#  Both corners are INSIDE the shape, so a box 10 wide at x=0 ends at x=9.
#  That is why the real code keeps writing "- 1".

def my_widget(d, data):
    d.rectangle([2, 2, 60, 29], outline=1)
    d.text((8, 9), "HELLO", font=WG._font(14), fill=1)
    d.ellipse([80, 8, 103, 31], outline=1)


# ===========================================================================
#  Plumbing from here down. You do not have to read it yet.
# ===========================================================================

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out.png")

FAKE = {
    "speed_kmh": 148, "rpm": 6200, "rpm_max": 8000, "redline": 7000,
    "gear": 4, "fuel_pct": 62, "throttle": 0.8, "brake": 0.0,
    "np_title": "Midnight City", "np_pct": 41, "np_playing": 1,
    "enc": 37, "enc_total": 412, "sw1": 2, "sw2": 4, "hid": 1,
}


def draw_once(scale=6):
    img = Image.new("1", (WG.W, WG.H), 0)
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    d.fontmode = "1"
    my_widget(d, FAKE)

    big = Image.new("RGB", (WG.W * scale, WG.H * scale), (10, 12, 14))
    src, dst = img.load(), Image.new("RGB", (WG.W, WG.H), (10, 12, 14))
    dpx = dst.load()
    lit = 0
    for y in range(WG.H):
        for x in range(WG.W):
            if src[x, y]:
                dpx[x, y] = (230, 242, 255)
                lit += 1
    big.paste(dst.resize((WG.W * scale, WG.H * scale), Image.NEAREST), (0, 0))
    big.save(OUT)
    return lit


def main():
    watch = "--watch" in sys.argv
    me = os.path.abspath(__file__)
    stamp = 0
    opened = False
    while True:
        try:
            lit = draw_once()
            print("%s  drawn, %d pixels lit  ->  %s"
                  % (time.strftime("%H:%M:%S"), lit, OUT))
        except Exception as e:
            print("%s  it did not draw: %s: %s"
                  % (time.strftime("%H:%M:%S"), type(e).__name__, e))
        if not opened:
            opened = True
            try:
                os.startfile(OUT)
            except Exception:
                subprocess.run(["cmd", "/c", "start", "", OUT], check=False)
        if not watch:
            return
        stamp = os.path.getmtime(me)
        while os.path.getmtime(me) == stamp:
            time.sleep(0.3)
        time.sleep(0.2)


if __name__ == "__main__":
    main()
