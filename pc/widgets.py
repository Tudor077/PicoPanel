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
    # A stick, a wheel or a pad, through Windows' own joystick API - see
    # sticks.py. These are signed: -1 hard over, 0 centred, +1 hard the other
    # way, which is what makes a centring widget possible at all.
    ("joy_x", "Stick X"),           ("joy_y", "Stick Y"),
    ("joy_z", "Stick Z"),           ("joy_r", "Stick R"),
    # What's playing, beyond the two strings: the board's own MUSIC page is
    # built out of these, so a page of yours can be too.
    ("np_pct", "Track position %"), ("np_pos", "Track seconds"),
    ("np_dur", "Track length"),     ("np_playing", "Playing"),
    # The panel itself. It reports all of this five times a second anyway.
    ("enc", "Encoder"),             ("enc_total", "Encoder total"),
    ("sw1", "Switch 1"),            ("sw2", "Switch 2"),
    ("fps", "Panel FPS"),           ("hid", "HID armed"),
]
FIELD_LABEL = dict(FIELDS)

# What each kind is allowed to be pointed at. A lamp showing the clock is not
# a feature nobody thought of, it is a list that could not be bothered to say
# no - and every wrong entry in a menu is a wrong thing somebody will try once
# and then distrust the rest.
_TEXTY = ["src", "text", "np_title", "np_artist", "clock"]
_NUMERIC = ["speed_kmh", "rpm", "rpm_max", "redline", "gear", "fuel_pct",
            "throttle", "brake", "turbo_bar", "engine_c", "kts", "vspeed_fpm",
            "alt_ft", "hdg", "gforce", "aoa",
            "np_pct", "np_pos", "np_dur", "np_playing",
            "enc", "enc_total", "sw1", "sw2", "fps", "hid"]
_AXES = ["joy_x", "joy_y", "joy_z", "joy_r"]
# A fill or a needle needs something with a top end. "How much of it" makes no
# sense for a heading or a vertical speed, which run in both directions and
# have no full scale to fill towards.
_SCALED = ["rpm", "speed_kmh", "fuel_pct", "throttle", "brake", "turbo_bar",
           "engine_c", "kts", "alt_ft", "gforce", "aoa",
           "np_pct", "enc", "sw1", "sw2"]

# Text sizes itself: its box is the box the letters come out as, so w and h
# were numbers you could turn all day for nothing. They are not offered now.
AUTO_SIZE = {"value", "label"}
USES_W = {"bar", "vbar", "dial", "box", "line", "axis", "dz", "btn", "btnrow",
          "knob", "disc", "switch"}
# The alarm sizes itself around the bell and the time left.
AUTO_ALARM = {"alarm"}
USES_H = USES_W | {"lamp"}

KIND_FIELDS = {
    "value": _NUMERIC + _TEXTY,       # a number, or a string, either reads
    "label": _TEXTY + ["none"],       # text only: a bare number is a Number
    "bar":   _SCALED,
    "vbar":  _SCALED,
    "dial":  _SCALED,
    "lamp":  _NUMERIC + _AXES,        # lit when it is not zero
    "box":   ["none"],                # decoration: it reads nothing at all
    "line":  ["none"],
    "axis":  _AXES + ["throttle", "brake", "gforce", "aoa"],
    "btn":    ["none"],              # which button is a choice, not a field
    "btnrow": ["none"],
    "knob":   ["enc", "enc_total", "fuel_pct", "np_pct", "throttle"],
    "disc":   ["none"],
    "switch": ["none"],
    "alarm":  ["none"],
}


def fields_for(kind):
    """The (key, label) pairs a kind may be pointed at, in the usual order."""
    ok = KIND_FIELDS.get(kind)
    if ok is None:
        return list(FIELDS)
    return [(k, lbl) for k, lbl in FIELDS if k in ok]

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
    field_y: str = "none"    # the second axis, for the kinds that have two
    label: str = ""
    size: int = 14           # text height, for the kinds that have text
    opts: dict = dc_field(default_factory=dict)

    def to_dict(self):
        return {"kind": self.kind, "x": self.x, "y": self.y, "w": self.w,
                "h": self.h, "field": self.field, "field_y": self.field_y,
                "label": self.label, "size": self.size, "opts": dict(self.opts)}

    @staticmethod
    def from_dict(d):
        return Widget(kind=d.get("kind", "value"), x=int(d.get("x", 0)),
                      y=int(d.get("y", 0)), w=int(d.get("w", 60)),
                      h=int(d.get("h", 16)), field=d.get("field", "speed_kmh"),
                      field_y=d.get("field_y", "none"),
                      label=d.get("label", ""), size=int(d.get("size", 14)),
                      opts=dict(d.get("opts") or {}))


# ---------------------------------------------------------------- values --
def _num(data, key, default=0.0):
    try:
        v = data.get(key)
        return default if v is None else float(v)
    except Exception:
        return default


# The clock is a field, so it needs a shape. %#I is Windows' "no leading
# zero" - this app is Windows-only, and "01:05 PM" looks like a mistake.
CLOCK_FMTS = [
    ("%H:%M", "24h  13:05"),
    ("%#I:%M %p", "12h  1:05 PM"),
    ("%H:%M:%S", "24h  13:05:09"),
    ("%d %b", "date  12 Mar"),
    ("%d/%m/%Y", "date  12/03/2026"),
    ("%a %d %b", "day   Wed 12 Mar"),
    ("%d %b %H:%M", "both  12 Mar 13:05"),
]
CLOCK_LABEL = dict(CLOCK_FMTS)


def _text_of(w, data):
    """What this widget's field reads as, already a string."""
    if w.field == "clock":
        try:
            return time.strftime(w.opts.get("fmt", "%H:%M"))
        except ValueError:
            return time.strftime("%H:%M")   # a format Windows dislikes
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
              "np_pct": 100, "enc": 100, "sw1": 3, "sw2": 5,
              "np_pos": _num(data, "np_dur", 0) or 1,
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


# The board's own 4x4 ordered dither, the same sixteen numbers. One bit per
# pixel has no half-lit, so a fade is a pattern - and it has to be THIS pattern,
# or a bar drawn here would shimmer next to one drawn there.
BAYER4 = (0, 8, 2, 10,
          12, 4, 14, 6,
          3, 11, 1, 9,
          15, 7, 13, 5)

# What a bar can be filled with. "track" is the song bar: the whole length
# faded, the played part solid. "ramp" is the rev counter: thin at the left,
# solid by the right-hand end.
FILLS = [("solid", "Solid"), ("track", "Faded track"), ("ramp", "Fade in")]
FILL_LABEL = dict(FILLS)
HAS_FILL = {"bar", "vbar"}


def _dither(d, x0, y0, x1, y1, level, lv2=None, along="x"):
    """A rectangle of dither. One level, or a ramp between two along an axis.

    The points go in one call: PIL will take a whole list, and a bar is a few
    hundred pixels fifteen times a second.
    """
    x0, y0 = int(x0), int(y0)
    x1, y1 = int(x1), int(y1)
    if x1 < x0 or y1 < y0:
        return
    span = (x1 - x0) if along == "x" else (y1 - y0)
    pts = []
    for j in range(y0, y1 + 1):
        for i in range(x0, x1 + 1):
            lv = level
            if lv2 is not None and span > 0:
                k = (i - x0) if along == "x" else (y1 - j)
                lv = level + (lv2 - level) * k // span
            if BAYER4[((j & 3) << 2) | (i & 3)] < lv:
                pts.append((i, j))
    if pts:
        d.point(pts, fill=1)


def _d_bar(d, w, data):
    d.rectangle([w.x, w.y, w.x + w.w - 1, w.y + w.h - 1], outline=1)
    # A two-pixel inset inside a four-pixel bar leaves nothing to fill, and a
    # thin bar is exactly what a progress bar wants to be.
    pad = 2 if w.h >= 8 else 1
    inner = w.w - 2 * pad
    n = int(inner * _fraction(w, data))
    style = w.opts.get("fill", "solid")
    top, bot = w.y + pad, w.y + w.h - 1 - pad
    if style == "track":
        _dither(d, w.x + pad, top, w.x + pad - 1 + inner, bot, 5)
        if n > 0:
            d.rectangle([w.x + pad, top, w.x + pad - 1 + n, bot], fill=1)
    elif style == "ramp":
        _dither(d, w.x + pad, top, w.x + pad - 1 + n, bot, 3, 16, "x")
    elif n > 0:
        d.rectangle([w.x + pad, top, w.x + pad - 1 + n, bot], fill=1)


def _d_vbar(d, w, data):
    d.rectangle([w.x, w.y, w.x + w.w - 1, w.y + w.h - 1], outline=1)
    pad = 2 if w.w >= 8 else 1
    inner = w.h - 2 * pad
    n = int(inner * _fraction(w, data))
    style = w.opts.get("fill", "solid")
    left, right = w.x + pad, w.x + w.w - 1 - pad
    bot = w.y + w.h - 1 - pad
    top = bot - n + 1
    if style == "track":
        _dither(d, left, w.y + pad, right, bot, 5)
        if n > 0:
            d.rectangle([left, top, right, bot], fill=1)
    elif style == "ramp":
        _dither(d, left, top, right, bot, 3, 16, "y")
    elif n > 0:
        d.rectangle([left, top, right, bot], fill=1)


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


def _axis(data, key):
    """A field as -1..+1. The stick axes already are; anything else is taken
    as it comes and clamped, so pointing this at throttle puts 0.6 to the
    right of the middle rather than pretending to be something it is not."""
    return max(-1.0, min(1.0, _num(data, key)))


def _d_dz(d, w, data):
    """Two axes at once: a box, the middle, and where you actually are.

    One bit per pixel, so "in the middle" cannot be a colour. The middle box
    FILLS when you are inside it and the dot goes hollow - a state you can read
    across a desk, instead of counting pixels against a crosshair.
    """
    x0, y0 = w.x, w.y
    x1, y1 = w.x + w.w - 1, w.y + w.h - 1
    d.rectangle([x0, y0, x1, y1], outline=1)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0

    # Ticks at the middle of each edge rather than a full crosshair: a cross
    # through the whole box and a dot on top of it are the same pixels.
    d.line([cx, y0 + 1, cx, y0 + 2], fill=1)
    d.line([cx, y1 - 2, cx, y1 - 1], fill=1)
    d.line([x0 + 1, cy, x0 + 2, cy], fill=1)
    d.line([x1 - 2, cy, x1 - 1, cy], fill=1)

    dz = float(w.opts.get("dz", 0.18))
    dx = max(1.0, (w.w / 2.0 - 2) * dz)
    dy = max(1.0, (w.h / 2.0 - 2) * dz)
    ax = _axis(data, w.field)
    ay = _axis(data, w.field_y)
    home = abs(ax) <= dz and abs(ay) <= dz
    if home:
        # Inside: the middle goes solid and there is no separate dot. A dot
        # drawn on top of a filled box punches a hole in it, and a hole in a
        # box looks exactly like an empty box - the two states have to differ
        # at a glance, which is the entire job of this widget.
        d.rectangle([cx - dx, cy - dy, cx + dx, cy + dy], fill=1, outline=1)
        return
    d.rectangle([cx - dx, cy - dy, cx + dx, cy + dy], outline=1)
    px = cx + ax * (w.w / 2.0 - 2)
    py = cy + ay * (w.h / 2.0 - 2)
    d.rectangle([px - 1, py - 1, px + 1, py + 1], fill=1, outline=1)


# ---- the panel's own furniture, as widgets ------------------------------
# Everything the board draws on its own pages, so a page of yours can have it
# too. The names are the ones printed on the panel.
PCF_NAMES = ["A1", "A2", "A3", "A4", "B1", "B2", "B3", "B4"]
PAD_NAMES = ["UP", "DN", "LF", "RT", "MD", "ST", "EN"]


def _pressed(data, which):
    """Is that button down? The expander ones first, then the d-pad."""
    if which in PCF_NAMES:
        seq, i = data.get("pcf") or [], PCF_NAMES.index(which)
    elif which in PAD_NAMES:
        seq, i = data.get("btn") or [], PAD_NAMES.index(which)
    else:
        return False
    return bool(i < len(seq) and seq[i])


def _cell(d, x, y, cw, ch, text, on):
    """One key: filled while it is held, outlined while it is not - which is
    how the board draws its own button row."""
    d.rectangle([x, y, x + cw - 1, y + ch - 1], fill=1 if on else 0, outline=1)
    if text:
        d.text((x + 2, y + max(0, (ch - 10) // 2)), text,
               font=_font(9 if ch >= 11 else 8), fill=0 if on else 1)


def _d_btn(d, w, data):
    """One button of the panel, by name."""
    which = w.opts.get("which", "A1")
    _cell(d, w.x, w.y, w.w, w.h, w.label or which, _pressed(data, which))


def _d_btnrow(d, w, data):
    """All eight expander buttons, as the PANEL page has them."""
    n = len(PCF_NAMES)
    cw = max(5, (w.w - (n - 1)) // n)
    for i, name in enumerate(PCF_NAMES):
        _cell(d, w.x + i * (cw + 1), w.y, cw, w.h, name if cw >= 14 else "",
              _pressed(data, name))


def _d_knob(d, w, data):
    """The encoder as a knob: a ring with a mark. 300 degrees of travel, like
    a real one - a full circle has no ends and no top."""
    import math
    r = max(3, min(w.w, w.h) // 2 - 1)
    cx, cy = w.x + w.w // 2, w.y + w.h // 2
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=1)
    a = math.radians(-240 + 300 * _fraction(w, data))
    d.line([cx + math.cos(a) * r * 0.35, cy + math.sin(a) * r * 0.35,
            cx + math.cos(a) * r * 0.95, cy + math.sin(a) * r * 0.95], fill=1)
    if w.label:
        d.text((w.x + w.w + 2, cy - 5), w.label, font=_font(9), fill=1)


def _d_disc(d, w, data):
    """The record from the MUSIC page. The three marks are the point of it: a
    bare circle looks identical from one frame to the next and would seem to
    be standing still."""
    import math
    r = max(4, min(w.w, w.h) // 2 - 1)
    cx, cy = w.x + w.w // 2, w.y + w.h // 2
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=1)
    d.ellipse([cx - 1, cy - 1, cx + 1, cy + 1], fill=1)
    turn = _num(data, "np_pos") if _num(data, "np_playing") else 0.0
    for k in range(3):
        a = math.radians(turn * 90 + k * 120)
        d.line([cx + math.cos(a) * r * 0.45, cy + math.sin(a) * r * 0.45,
                cx + math.cos(a) * (r - 1), cy + math.sin(a) * (r - 1)], fill=1)


def _d_switch(d, w, data):
    """A slide switch: one cell per position, the one it is in filled."""
    which = w.opts.get("which", "sw1")
    n = 3 if which == "sw1" else 5
    pos = int(_num(data, which))
    cw = max(3, (w.w - (n - 1)) // n)
    for i in range(n):
        _cell(d, w.x + i * (cw + 1), w.y, cw, w.h,
              str(i + 1) if cw >= 8 else "", pos == i + 1)


def _alarm_parts(w, data):
    """(text, draw it at all). Nothing pending means nothing on the glass -
    except in the editor, where a widget you cannot see is a widget you cannot
    place."""
    left = data.get("alarm_in")
    if left is None:
        if not data.get("_editing"):
            return "", False
        left = 3 * 3600 + 42 * 60          # a plausible something, to lay out
    left = max(0, int(left))
    if left >= 3600:
        txt = "%dh%02d" % (left // 3600, (left % 3600) // 60)
    else:
        txt = "%d:%02d" % (left // 60, left % 60)
    if w.opts.get("what") and data.get("alarm_text"):
        txt += " " + str(data["alarm_text"])
    return txt, True


def _d_alarm(d, w, data):
    """A bell and how long until it goes off.

    The same countdown the header carries, for when you want it somewhere
    else on a page of your own - bigger, or next to something.
    """
    txt, show = _alarm_parts(w, data)
    if not show:
        return
    bell(d, w.x + 1, w.y + 1, 1)
    d.text((w.x + 10, w.y - 1), txt, font=_font(w.size), fill=1)


def _d_box(d, w, data):
    d.rectangle([w.x, w.y, w.x + w.w - 1, w.y + w.h - 1], outline=1)


def _d_line(d, w, data):
    d.line([w.x, w.y, w.x + w.w - 1, w.y + w.h - 1], fill=1)


DRAW = {
    "value": _d_value, "label": _d_label, "bar": _d_bar, "vbar": _d_vbar,
    "dial": _d_dial, "lamp": _d_lamp, "box": _d_box, "line": _d_line,
    # "dz" was what this was called for an afternoon; a layout saved then
    # still names it that, and a saved page that stops drawing is not a
    # rename, it is a bug.
    "axis": _d_dz, "dz": _d_dz,
    "btn": _d_btn, "btnrow": _d_btnrow, "knob": _d_knob, "disc": _d_disc,
    "switch": _d_switch, "alarm": _d_alarm,
}

# Kinds that pick one of a list rather than a field - a dropdown in the editor.
CHOICES = {
    "btn": [(n, n) for n in PCF_NAMES + PAD_NAMES],
    "switch": [("sw1", "Switch 1 (3 positions)"),
               ("sw2", "Switch 2 (5 positions)")],
}

# The kinds that read two fields, so the editor knows when to offer a second.
TWO_FIELD = {"axis", "dz"}

# Which kinds actually use the text size and the caption. A spinbox that does
# nothing is worse than no spinbox: it invites you to turn it and then makes
# you wonder what you broke.
USES_SIZE = {"value", "label", "alarm"}
USES_LABEL = {"value", "label", "lamp"}

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
    ("axis",  "Axis",       dict(w=30, h=30, field="joy_x", field_y="joy_y")),
    ("btn",   "Button",     dict(w=18, h=11, field="none",
                                 opts={"which": "A1"})),
    ("btnrow", "Button row", dict(w=128, h=10, field="none")),
    ("knob",  "Knob",       dict(w=22, h=22, field="enc")),
    ("disc",  "Record",     dict(w=22, h=22, field="none")),
    ("switch", "Switch",    dict(w=40, h=10, field="none",
                                 opts={"which": "sw1"})),
    ("alarm", "Alarm in",   dict(w=44, h=11, size=11, field="none")),
]

# One line each, for the shelf. "Lamp" told nobody anything.
ABOUT = {
    "value": "a number, as big as you like",
    "label": "a line of text, or a field that is text",
    "bar": "fills left to right - speed, revs, fuel, the track",
    "vbar": "the same, standing up",
    "dial": "a needle over a scale of ticks",
    "lamp": "a dot that lights while the field is not zero - brake, HID",
    "box": "an empty frame, for grouping",
    "line": "a rule, for dividing",
    "axis": "two axes at once, with the deadzone marked",
    "btn": "one button of the panel, lit while it is held",
    "btnrow": "all eight expander buttons, as the PANEL page has them",
    "knob": "the encoder, as a knob with a mark",
    "disc": "the record from MUSIC, turning while something plays",
    "switch": "a slide switch, with the position it is in filled",
    "alarm": "how long until the next alarm - gone from the panel when there "
             "is none, still here so you can place it",
}


HEADER_H = 9            # the same nine rows the board's own header uses


def bell(d, x, y, fill=1):
    """Five pixels wide, drawn rather than written: the panel's font has no
    such glyph and a second font for one picture would be absurd."""
    d.line([x + 1, y, x + 3, y], fill=fill)
    d.line([x, y + 1, x, y + 4], fill=fill)
    d.line([x + 4, y + 1, x + 4, y + 4], fill=fill)
    d.line([x - 1, y + 5, x + 5, y + 5], fill=fill)
    d.point((x + 2, y + 6), fill=fill)


def header_bar(d, text, right="", shift=0, alarm=""):
    """The board's header, drawn here: a white bar, black text, the name on
    the left and the counter on the right.

    The board draws this for its own pages in a 5x7 font. A page of yours is
    one picture from here, so if it is to have a header, this has to draw it -
    in Tahoma 9, which is what the panel's Cyrillic titles already use and sits
    beside the board's own text without looking like a different machine.
    """
    f = _font(9)

    def wide(t):
        try:
            return d.textlength(t, font=f)
        except Exception:
            return 6 * len(t)

    # The countdown sits at the far right whether the bar is there or not: the
    # bar retracts after twenty idle seconds, which is exactly when you want to
    # see how long you have. Black on the bar, white on the page without it.
    on_bar = shift < HEADER_H
    if on_bar:
        d.rectangle([0, -shift, W - 1, HEADER_H - 1 - shift], fill=1)
        d.text((2, -1 - shift), text, font=f, fill=0)
    reserve = (wide(alarm) + 14) if alarm else 0
    if right and on_bar:
        d.text((W - 2 - wide(right) - reserve, -1 - shift), right, font=f, fill=0)
    if alarm:
        ink = 0 if on_bar else 1
        y = -shift if on_bar else 0
        x = W - 2 - wide(alarm)
        if not on_bar:
            d.rectangle([x - 10, y, W - 1, y + 8], fill=0)
        bell(d, x - 8, y + 1, ink)
        d.text((x, y - 1), alarm, font=f, fill=ink)


def measure(w, data):
    """How big the thing actually comes out.

    Text is as wide as the text, so the box the editor picks up and outlines
    is measured from what was drawn rather than from two numbers that changed
    nothing.
    """
    if not HAVE_PIL or (w.kind not in AUTO_SIZE and w.kind not in AUTO_ALARM):
        return w.w, w.h
    try:
        d = ImageDraw.Draw(Image.new("1", (1, 1)))
        if w.kind in AUTO_ALARM:
            txt = _alarm_parts(w, dict(data, _editing=1))[0]
            box = d.textbbox((0, 0), txt or "0h00", font=_font(w.size))
            return max(20, int(box[2]) + 11), max(11, int(box[3]))
        txt = _text_of(w, data) or " "
        box = d.textbbox((0, 0), txt, font=_font(w.size))
        tw, th = box[2], box[3]
        if w.kind == "value" and w.label:
            cap = d.textbbox((0, 0), w.label, font=_font(9))
            tw, th = max(tw, cap[2]), th + 9
        return max(2, int(tw)), max(2, int(th))
    except Exception:
        return w.w, w.h


def render(widgets, data, header=None):
    """The whole 128x32 page as a PIL image, or None without PIL.

    `header` is ("NAME", "3/11", shift, "6h12") to put the board's own header
    bar on top - shift being how far it has slid away, which the board reports
    so yours retracts with its own, and the last being the countdown to the
    next alarm, which stays after the bar has gone. None means the whole screen
    is yours. The widgets are drawn AFTER it, so a layout
    made before the header existed still shows rather than disappearing under
    a bar nobody has moved it out of yet.
    """
    if not HAVE_PIL:
        return None
    img = Image.new("1", (W, H), 0)
    d = ImageDraw.Draw(img)
    d.fontmode = "1"                 # 1-bit screen: antialiasing is mud
    if header:
        try:
            header_bar(d, header[0], header[1] if len(header) > 1 else "",
                       header[2] if len(header) > 2 else 0,
                       header[3] if len(header) > 3 else "")
        except Exception:
            pass
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
