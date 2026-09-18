"""Text the panel's own font cannot draw, drawn here instead and sent as pixels.

The board has a 5x7 ASCII font and nothing else, so a Russian title could only
ever be transliterated. The PC, meanwhile, has every font Windows ships. So the
PC renders the line to a one-bit strip and sends the strip; the board scrolls it
exactly as it scrolls text, and never needs to know what an alphabet is.

Tahoma at 9 px with antialiasing off is what this uses. It was picked by looking
at the alternatives on this screen's terms - it is hinted for small sizes, and
at 1 bit per pixel that is the whole game. Segoe UI and Arial both come out
blurrier at this size; Consolas is wider for no gain.

The strip is column-major, one byte per column, bit 0 at the top - the same
shape as the SSD1306's own memory, so the firmware blits it without shuffling
bits about.
"""

import base64
import os

try:
    from PIL import Image, ImageDraw, ImageFont
    HAVE_PIL = True
except Exception:                                   # pragma: no cover
    HAVE_PIL = False

HEIGHT = 8                  # one page of the display
SIZE = 9                    # Tahoma at 9 px fills 7 of those 8 rows

# Wider than this and the marquee is a chore to read anyway, and the line stops
# fitting the board's buffer. Titles get cut before they get here.
MAX_W = 250

# Tahoma first: Latin, Cyrillic and Greek, and hinted for exactly this size.
# The rest are there for scripts it lacks - a missing glyph is a hollow box, so
# a stack is worth having even if it is rarely used.
FONTS = ["tahoma.ttf", "segoeui.ttf", "arial.ttf"]

_cache = {}


def _font():
    if not HAVE_PIL:
        return None
    if "f" in _cache:
        return _cache["f"]
    root = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for name in FONTS:
        try:
            _cache["f"] = ImageFont.truetype(os.path.join(root, name), SIZE)
            return _cache["f"]
        except Exception:
            continue
    _cache["f"] = None
    return None


def render(text):
    """(width, bytes) for that text, or None if it can't be drawn.

    One byte per column, bit 0 the top row.
    """
    text = (text or "").strip()
    if not text or not HAVE_PIL:
        return None
    f = _font()
    if f is None:
        return None
    try:
        probe = Image.new("1", (1, 1))
        box = ImageDraw.Draw(probe).textbbox((0, 0), text, font=f)
        w = min(MAX_W, max(1, box[2] - box[0] + 1))
        img = Image.new("1", (w, HEIGHT), 0)
        d = ImageDraw.Draw(img)
        d.fontmode = "1"                 # 1-bit screen: antialiasing is mud
        d.text((-box[0], -box[1]), text, font=f, fill=1)
    except Exception:
        return None

    px = img.load()
    out = bytearray(w)
    for x in range(w):
        col = 0
        for y in range(HEIGHT):
            if px[x, y]:
                col |= 1 << y
        out[x] = col
    # An all-blank strip means the font had nothing for any of it. Say so, so
    # the caller can fall back to the transliteration rather than show a gap.
    if not any(out):
        return None
    return w, bytes(out)


def line(kind, text):
    """The '%ts=' line for the board, or None.

    kind: 1 = the title, 2 = the artist.
    """
    got = render(text)
    if not got:
        return None
    w, data = got
    return "%%ts=%d;w=%d;d=%s" % (kind, w, base64.b64encode(data).decode("ascii"))


if __name__ == "__main__":
    import sys
    text = sys.argv[1] if len(sys.argv) > 1 else "\u041f\u044b\u044f\u043b\u0430 - \u0410\u0418\u0413\u0415\u041b"
    got = render(text)
    if not got:
        raise SystemExit("nothing to draw")
    w, data = got
    print("%d px wide, %d bytes, line is %d chars"
          % (w, len(data), len(line(1, text))))
    for y in range(HEIGHT):
        print("|" + "".join("#" if data[x] & (1 << y) else " "
                            for x in range(w)) + "|")
