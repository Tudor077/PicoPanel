#!/usr/bin/env python3
"""The phone's launcher icon, from the same drawing as the desktop one.

    python android/tools/make_icon.py

There used to be a second icon here - a hand-drawn vector that looked roughly
like the panel - and the two drifted, which is the trouble with drawing the
same thing twice. This renders `pc/icon.py` into the mipmaps Android wants, so
there is one drawing and the phone shows it.

Adaptive icons are 108dp with only the middle 72dp guaranteed to survive the
launcher's mask, so the drawing is scaled to sit inside that and the rest is
transparent. The background is the flat colour the desktop icon's body uses.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "pc"))

import icon as ICON                                   # noqa: E402
from PIL import Image                                 # noqa: E402

# dp -> px for each bucket. The foreground is 108dp square.
BUCKETS = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}

# How much of the 108dp the drawing takes. 72/108 is the guaranteed-visible
# circle; a hair under it keeps the rim off the edge of the mask.
SAFE = 68.0 / 108.0


def main():
    res = os.path.join(ROOT, "android", "app", "src", "main", "res")
    art = ICON.draw(512)
    for name, scale in BUCKETS.items():
        side = int(round(108 * scale))
        inner = int(round(side * SAFE))
        img = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        art_s = art.resize((inner, inner), Image.LANCZOS)
        off = (side - inner) // 2
        img.paste(art_s, (off, off), art_s)
        out = os.path.join(res, "mipmap-" + name)
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, "ic_launcher_foreground.png")
        img.save(path)
        print("%-28s %dx%d" % (os.path.relpath(path, ROOT), side, side))

    # The legacy square icon, for anything that asks for the bitmap directly.
    for name, scale in BUCKETS.items():
        side = int(round(48 * scale))
        out = os.path.join(res, "mipmap-" + name, "ic_launcher.png")
        ICON.draw(512).resize((side, side), Image.LANCZOS).save(out)
        ICON.draw(512).resize((side, side), Image.LANCZOS).save(
            os.path.join(res, "mipmap-" + name, "ic_launcher_round.png"))
    print("legacy ic_launcher.png / _round.png written too")


if __name__ == "__main__":
    main()
