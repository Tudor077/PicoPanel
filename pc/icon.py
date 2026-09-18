"""The program's icon, drawn rather than shipped as a file.

One drawing, used by the tray and by the executable, so the thing in the
notification area and the thing in Explorer cannot drift apart.

It is drawn large and scaled down, never drawn small: the 16 px version of a
shape that was composed at 16 px is a mess, while a 16 px LANCZOS reduction of a
well-composed 256 keeps the weight of each element.

What survives at 16 px decided the design. Only two shapes do - the lit screen
and the knob - so only those two are allowed to be large, and everything that
would have been a fine detail at 256 was left out instead. The pointer on the
knob is a notch cut out of its edge rather than a line drawn on its face,
because a line disappears below 32 px and a notch changes the outline.
"""

from PIL import Image, ImageDraw

RIM = (142, 153, 173)
BODY = (34, 37, 44)
BODY_TOP = (52, 57, 68)
SCREEN = (7, 9, 11)
LIT = (61, 220, 132)
HOT = (255, 122, 69)
KNOB = (206, 213, 224)
KNOB_DK = (108, 117, 132)
BTN = (150, 160, 178)


def draw(size=256):
    k = size / 256.0

    def r(*v):
        return [x * k for x in v]

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # A bright rim all the way round: the silhouette then reads on a dark
    # taskbar and on white paper, which is the whole job of an icon.
    d.rounded_rectangle(r(8, 20, 248, 236), radius=42 * k, fill=RIM)
    d.rounded_rectangle(r(15, 27, 241, 229), radius=36 * k, fill=BODY_TOP)
    d.rounded_rectangle(r(15, 33, 241, 229), radius=36 * k, fill=BODY)

    # The screen, as large as it can be with the knob still fitting under it.
    d.rounded_rectangle(r(32, 46, 224, 148), radius=11 * k, fill=SCREEN)

    # The rev bar, which is what this panel is always drawing. The last one is
    # hot: a single warm note is what stops it reading as a generic chart.
    base = 132
    for i in range(5):
        h = 20 + i * 17
        x0 = 48 + i * 36
        d.rectangle(r(x0, base - h, x0 + 26, base), fill=HOT if i == 4 else LIT)

    d.ellipse(r(148, 152, 228, 232), fill=KNOB)
    d.ellipse(r(162, 166, 214, 218), fill=KNOB_DK)
    d.pieslice(r(148, 152, 228, 232), -104, -76, fill=BODY)     # the notch

    d.ellipse(r(42, 172, 86, 216), fill=BTN)
    return img


def save_ico(path, sizes=(256, 64, 48, 32, 16)):
    """A .ico with every size Windows asks for, each reduced from the big one."""
    big = draw(256)
    big.save(path, format="ICO", sizes=[(s, s) for s in sizes])
    return path


def save_png(path, size=32):
    draw(256).resize((size, size), Image.LANCZOS).save(path)
    return path


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "picopanel.ico"
    print("wrote", save_ico(out))
