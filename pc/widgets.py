"""The widget library, and the page it draws.

The board has a 5x7 font and about eight shapes. The PC has every font Windows
ships and a whole drawing library, so this is where the widgets live - and what
crosses the wire is the finished picture, 512 bytes in the exact layout of the
panel's own memory. The board's side of it is a memcpy.

A widget is a small dataclass: a kind, a place, a size, and the name of the
telemetry field it shows. Drawing one is a function in DRAW, keyed by kind. To
add a widget you write one function and one entry - nothing else in the program
has to hear about it.

The screen is 128x32. That is the whole design constraint: three or four things
fit, everything is 1-bit, and the smallest readable text is seven pixels tall.
A widget that needs more than that has no business being here.
"""

import time
# 'field' as an import and 'field' as an attribute of this very class do not
# coexist: inside the class body the attribute wins and the default_factory
# call becomes a string call.
from dataclasses import dataclass
from dataclasses import field as dc_field

try:
    from PIL import Image, ImageDraw, ImageFont
    HAVE_PIL = True
except Exception:                                   # pragma: no cover
    HAVE_PIL = False

W, H = 128, 32

# Everything a widget can be pointed at. The key is what the app sends us in a
# dict; the label is what the editor shows.
FIELDS = [
    ("speed_kmh", "Speed"),         ("rpm", "RPM"),
    ("rpm_max", "RPM full scale"),  ("redline", "Redline"),
    ("gear", "Gear"),               ("fuel_pct", "Fuel %"),
    ("throttle", "Throttle"),       ("brake", "Brake"),
    ("turbo_bar", "Turbo bar"),     ("engine_c", "Engine C"),
    ("kts", "Airspeed kt"),         ("vspeed_fpm", "Vertical speed"),
    ("alt_ft", "Altitude ft"),      ("hdg", "Heading"),
    ("gforce", "G"),                ("aoa", "Angle of attack"),
    ("src", "Source name"),         ("text", "Vehicle / text"),
    ("np_title", "Track title"),    ("np_artist", "Artist"),
    ("clock", "Clock"),             ("none", "(nothing)"),
]
FIELD_LABEL = dict(FIELDS)

_font_cache = {}


def _font(size):
    if not HAVE_PIL:
        return None
    if size in _font_cache:
        return _font_cache[size]
    import os
    root = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for name in ("tahoma.ttf", "segoeui.ttf", "arial.ttf"):
        try:
            _font_cache[size] = ImageFont.truetype(os.path.join(root, name), size)
            return _font_cache[size]
        except Exception:
            continue
    _font_cache[size] = ImageFont.load_default()
    return _font_cache[size]


@dataclass
class Widget:
    kind: str = "value"
    x: int = 0
    y: int = 0
    w: int = 60
    h: int = 16
    field: str = "speed_kmh"
    label: str = ""
    size: int = 14           # text height, for the kinds that have text
    opts: dict = dc_field(default_factory=dict)

    def to_dict(self):
        return {"kind": self.kind, "x": self.x, "y": self.y, "w": self.w,
                "h": self.h, "field": self.field, "label": self.label,
                "size": self.size, "opts": dict(self.opts)}

    @staticmethod
    def from_dict(d):
        return Widget(kind=d.get("kind", "value"), x=int(d.get("x", 0)),
                      y=int(d.get("y", 0)), w=int(d.get("w", 60)),
                      h=int(d.get("h", 16)), field=d.get("field", "speed_kmh"),
                      label=d.get("label", ""), size=int(d.get("size", 14)),
                      opts=dict(d.get("opts") or {}))


# ---------------------------------------------------------------- values --
def _num(data, key, default=0.0):
    try:
        v = data.get(key)
        return default if v is None else float(v)
    except Exception:
        return default


def _text_of(w, data):
    """What this widget's field reads as, already a string."""
    if w.field == "clock":
        return time.strftime(w.opts.get("fmt", "%H:%M"))
    if w.field in ("src", "text", "np_title", "np_artist"):
        return str(data.get(w.field) or "")
    if w.field == "gear":
        g = int(_num(data, "gear"))
        return "R" if g < 0 else ("N" if g == 0 else str(g))
    v = _num(data, w.field)
    dp = int(w.opts.get("dp", 0))
    return ("%%.%df" % dp) % v


def _fraction(w, data):
    """0..1 for the kinds that fill or point."""
    lo = float(w.opts.get("min", 0))
    hi = float(w.opts.get("max", 0)) or None
    if hi is None:
        # Sensible full scales, so a bar works the moment you drop it in
        # rather than after you have been into its settings.
        hi = {"rpm": _num(data, "rpm_max", 8000) or 8000,
              "speed_kmh": 260, "fuel_pct": 100, "throttle": 1, "brake": 1,
              "engine_c": 130, "turbo_bar": 2, "kts": 400,
              }.get(w.field, 100)
    v = _num(data, w.field)
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


# ----------------------------------------------------------------- draws --
def _d_value(d, w, data):
    f = _font(w.size)
    txt = _text_of(w, data)
    if w.label:
        small = _font(9)
        d.text((w.x, w.y), w.label, font=small, fill=1)
        d.text((w.x, w.y + 9), txt, font=f, fill=1)
    else:
        d.text((w.x, w.y), txt, font=f, fill=1)


def _d_label(d, w, data):
    d.text((w.x, w.y), w.label or _text_of(w, data), font=_font(w.size), fill=1)


def _d_bar(d, w, data):
    d.rectangle([w.x, w.y, w.x + w.w - 1, w.y + w.h - 1], outline=1)
    inner = w.w - 4
    n = int(inner * _fraction(w, data))
    if n > 0:
        d.rectangle([w.x + 2, w.y + 2, w.x + 1 + n, w.y + w.h - 3], fill=1)


def _d_vbar(d, w, data):
    d.rectangle([w.x, w.y, w.x + w.w - 1, w.y + w.h - 1], outline=1)
    inner = w.h - 4
    n = int(inner * _fraction(w, data))
    if n > 0:
        d.rectangle([w.x + 2, w.y + w.h - 2 - n, w.x + w.w - 3, w.y + w.h - 3],
                    fill=1)


def _d_dial(d, w, data):
    """A needle. Half a circle, ticks rather than an arc - an arc this size is
    a smudge of stair-stepped pixels and ticks give you a scale for free."""
    import math
    r = min(w.w // 2, w.h) - 1
    cx, cy = w.x + w.w // 2, w.y + w.h - 1
    for i in range(6):
        a = math.radians(180 - i * 36)
        d.line([cx + math.cos(a) * (r - 4), cy - math.sin(a) * (r - 4),
                cx + math.cos(a) * r,       cy - math.sin(a) * r], fill=1)
    a = math.radians(180 - 180 * _fraction(w, data))
    d.line([cx, cy, cx + math.cos(a) * (r - 5), cy - math.sin(a) * (r - 5)], fill=1)
    d.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=1)


def _d_lamp(d, w, data):
    """On when the field is non-zero. Filled when lit, outlined when not - on
    one bit per pixel that is the whole vocabulary."""
    on = abs(_num(data, w.field)) > 0.001
    box = [w.x, w.y, w.x + w.h - 1, w.y + w.h - 1]
    d.ellipse(box, fill=1 if on else 0, outline=1)
    if w.label:
        d.text((w.x + w.h + 3, w.y + max(0, (w.h - 9) // 2)), w.label,
               font=_font(9), fill=1)


def _d_box(d, w, data):
    d.rectangle([w.x, w.y, w.x + w.w - 1, w.y + w.h - 1], outline=1)


def _d_line(d, w, data):
    d.line([w.x, w.y, w.x + w.w - 1, w.y + w.h - 1], fill=1)


DRAW = {
    "value": _d_value, "label": _d_label, "bar": _d_bar, "vbar": _d_vbar,
    "dial": _d_dial, "lamp": _d_lamp, "box": _d_box, "line": _d_line,
}

# What the palette shows, and what a fresh one of each looks like.
PALETTE = [
    ("value", "Number",     dict(w=52, h=18, size=16)),
    ("label", "Text",       dict(w=60, h=10, size=10, field="src")),
    ("bar",   "Bar",        dict(w=100, h=8, field="rpm")),
    ("vbar",  "Column",     dict(w=10, h=26, field="fuel_pct")),
    ("dial",  "Needle",     dict(w=34, h=18, field="rpm")),
    ("lamp",  "Lamp",       dict(w=30, h=9, field="brake", label="BRK")),
    ("box",   "Frame",      dict(w=60, h=20, field="none")),
    ("line",  "Line",       dict(w=40, h=1, field="none")),
]


def render(widgets, data):
    """The whole 128x32 page as a PIL image, or None without PIL."""
    if not HAVE_PIL:
        return None
    img = Image.new("1", (W, H), 0)
    d = ImageDraw.Draw(img)
    d.fontmode = "1"                 # 1-bit screen: antialiasing is mud
    for w in widgets:
        fn = DRAW.get(w.kind)
        if not fn:
            continue
        try:
            fn(d, w, data)
        except Exception:
            pass                     # one bad widget must not blank the page
    return img


def to_frame(img):
    """PIL image -> the 512 bytes the panel keeps, one byte per column per
    eight-row band, which is why the board's side of this is a memcpy."""
    px = img.load()
    out = bytearray(W * H // 8)
    for page in range(H // 8):
        for x in range(W):
            b = 0
            for bit in range(8):
                if px[x, page * 8 + bit]:
                    b |= 1 << bit
            out[page * W + x] = b
    return bytes(out)
