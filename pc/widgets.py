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
from dataclasses import dataclass
from dataclasses import field as dc_field

try:
    from PIL import Image, ImageDraw, ImageFont
    HAVE_PIL = True
except Exception:                                   # pragma: no cover
    HAVE_PIL = False

W, H = 128, 32

FIELDS = [
    ("speed_kmh", "Speed"),         ("rpm", "RPM"),
    ("rpm_max", "RPM full scale"),  ("redline", "Redline"),
    ("gear", "Gear"),               ("fuel_pct", "Fuel %"),
    ("throttle", "Throttle"),       ("brake", "Brake"),
    ("turbo_bar", "Turbo bar"),     ("engine_c", "Engine C"),
    ("kts", "Airspeed kt"),         ("vspeed_fpm", "Vertical speed"),
    ("alt_ft", "Altitude ft"),      ("hdg", "Heading"),
    ("gforce", "G"),                ("aoa", "Angle of attack"),
    ("blink_l", "Left signal"),     ("blink_r", "Right signal"),
    ("src", "Source name"),         ("text", "Vehicle / text"),
    ("np_title", "Track title"),    ("np_artist", "Artist"),
    ("clock", "Clock"),             ("none", "(nothing)"),
    ("joy_x", "Stick X"),           ("joy_y", "Stick Y"),
    ("joy_z", "Stick Z"),           ("joy_r", "Stick R"),
    ("np_pct", "How far through %"), ("np_pos", "Seconds played"),
    ("np_dur", "Track length s"),    ("np_playing", "Playing"),
    ("enc", "Encoder"),             ("enc_total", "Encoder total"),
    ("sw1", "Switch 1"),            ("sw2", "Switch 2"),
    ("fps", "Panel FPS"),           ("hid", "HID armed"),
]
FIELD_LABEL = dict(FIELDS)

_TEXTY = ["src", "text", "np_title", "np_artist", "clock"]
_NUMERIC = ["speed_kmh", "rpm", "rpm_max", "redline", "gear", "fuel_pct",
            "throttle", "brake", "turbo_bar", "engine_c", "kts", "vspeed_fpm",
            "alt_ft", "hdg", "gforce", "aoa", "blink_l", "blink_r",
            "np_pct", "np_pos", "np_dur", "np_playing",
            "enc", "enc_total", "sw1", "sw2", "fps", "hid"]
_AXES = ["joy_x", "joy_y", "joy_z", "joy_r"]
_SCALED = ["rpm", "speed_kmh", "fuel_pct", "throttle", "brake", "turbo_bar",
           "engine_c", "kts", "alt_ft", "gforce", "aoa",
           "np_pct", "enc", "sw1", "sw2"]

AUTO_SIZE = {"value", "label"}
USES_W = {"bar", "vbar", "dial", "box", "line", "axis", "dz", "btn", "btnrow",
          "knob", "disc", "switch", "blinker"}
AUTO_ALARM = {"alarm"}
USES_H = USES_W | {"lamp"}

KIND_FIELDS = {
    "value": _NUMERIC + _TEXTY,
    "label": _TEXTY + ["none"],
    "bar":   _SCALED,
    "vbar":  _SCALED,
    "dial":  _SCALED,
    "lamp":  _NUMERIC + _AXES,
    "box":   ["none"],
    "line":  ["none"],
    "axis":  _AXES + ["throttle", "brake", "gforce", "aoa"],
    "btn":    ["none"],
    "btnrow": ["none"],
    "knob":   ["enc_total", "enc", "fuel_pct", "np_pct", "throttle"],
    "disc":   ["none"],
    "switch": ["none"],
    "alarm":  ["none"],
    "blinker": ["none"],
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
    field_y: str = "none"
    label: str = ""
    size: int = 14
    opts: dict = dc_field(default_factory=dict)

    def __post_init__(self):
        """A copy of opts, always, whoever handed it over.

        The shelf builds widgets with Widget(kind=k, **defaults), and defaults
        is the very dict written in PALETTE - so every Switch dropped on a page
        held a reference to ONE opts, the same one, for the life of the
        program. Put a three-position switch and a five-position switch on the
        same page and they were the same switch: changing which one showed
        changed the other, and the shelf's own icon with it.

        Guarded here rather than at the two call sites, because the next one
        would not know to do it either.
        """
        self.opts = dict(self.opts or {})

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


def _num(data, key, default=0.0):
    try:
        v = data.get(key)
        return default if v is None else float(v)
    except Exception:
        return default


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
            return time.strftime("%H:%M")
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


BAYER4 = (0, 8, 2, 10,
          12, 4, 14, 6,
          3, 11, 1, 9,
          15, 7, 13, 5)

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
    """Two axes at once: where you are, and whether you are dead centre.

    No deadzone: the tolerance is one pixel, the smallest this screen has. Each
    axis answers for itself - the vertical line appears when X is centred, the
    horizontal when Y is - so being centred in one and not the other is a thing
    you can see rather than a box you are somewhere inside. Both, and it is a
    full crosshair.
    """
    x0, y0 = w.x, w.y
    x1, y1 = w.x + w.w - 1, w.y + w.h - 1
    d.rectangle([x0, y0, x1, y1], outline=1)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0

    d.line([cx, y0 + 1, cx, y0 + 2], fill=1)
    d.line([cx, y1 - 2, cx, y1 - 1], fill=1)
    d.line([x0 + 1, cy, x0 + 2, cy], fill=1)
    d.line([x1 - 2, cy, x1 - 1, cy], fill=1)

    ax = _axis(data, w.field)
    ay = _axis(data, w.field_y)
    px = cx + ax * (w.w / 2.0 - 2)
    py = cy + ay * (w.h / 2.0 - 2)

    if round(px) == round(cx):
        d.line([cx, y0 + 3, cx, y1 - 3], fill=1)
    if round(py) == round(cy):
        d.line([x0 + 3, cy, x1 - 3, cy], fill=1)

    d.rectangle([px - 1, py - 1, px + 1, py + 1], fill=1, outline=1)


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
    if text and ch >= 8:
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


DEFAULT_PER_TURN = 20


def _d_knob(d, w, data):
    """The encoder as a knob, with the mark where the real shaft is.

    Pointed at the running total, it turns as the real one turns: one detent,
    one eighteenth of a circle, so a quarter turn of yours is a quarter turn of
    this. Pointed at anything with a top and a bottom - the value, a fuel
    gauge - it sweeps 300 degrees between them instead, because that has ends
    and a full circle does not.
    """
    import math
    r = max(3, min(w.w, w.h) // 2 - 1)
    cx, cy = w.x + w.w // 2, w.y + w.h // 2
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=1)
    turning = w.field in ("enc_total",) or w.opts.get("turn")
    if turning:
        per = int(w.opts.get("per_turn", DEFAULT_PER_TURN)) or DEFAULT_PER_TURN
        a = math.radians(-90 + 360.0 * (int(_num(data, w.field)) % per) / per)
        d.point((cx, cy - r), fill=1)
    else:
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


def _arrow(d, tip, cy, half, left, on):
    """One blinker arrow, the triangle the GAME page draws: the tip at `tip`,
    `half` rows above and below the middle. Filled while lit, outlined while
    not - an arrow that vanishes is one you cannot place in the editor."""
    back = tip + (half + 2) * (1 if left else -1)
    pts = [(tip, cy), (back, cy - half), (back, cy + half)]
    if on:
        d.polygon(pts, fill=1, outline=1)
    else:
        d.polygon(pts, fill=0, outline=1)


def _d_blinker(d, w, data):
    """The turn signals: left, right, or both at the two ends of the box.

    Lit exactly when the game's lamp is, so it blinks by itself, and both
    at once are the hazards. The same bits the board's own GAME page reads.
    """
    which = w.opts.get("which", "both")
    blk = int(_num(data, "blinkers"))
    half = max(1, (min(w.h, 15) - 1) // 2)
    cy = w.y + w.h // 2
    if which in ("left", "both"):
        _arrow(d, w.x, cy, half, True, blk & 1)
    if which in ("right", "both"):
        _arrow(d, w.x + w.w - 1, cy, half, False, blk & 2)


def _alarm_parts(w, data):
    """(text, draw it at all). Nothing pending means nothing on the glass -
    except in the editor, where a widget you cannot see is a widget you cannot
    place."""
    left = data.get("alarm_in")
    if left is None:
        if not data.get("_editing"):
            return "", False
        left = 3 * 3600 + 42 * 60
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
    "axis": _d_dz, "dz": _d_dz,
    "btn": _d_btn, "btnrow": _d_btnrow, "knob": _d_knob, "disc": _d_disc,
    "switch": _d_switch, "alarm": _d_alarm, "blinker": _d_blinker,
}

CHOICES = {
    "btn": [(n, n) for n in PCF_NAMES + PAD_NAMES],
    "switch": [("sw1", "Switch 1 (3 positions)"),
               ("sw2", "Switch 2 (5 positions)")],
    "blinker": [("both", "Both sides"), ("left", "Left"), ("right", "Right")],
}

TWO_FIELD = {"axis", "dz"}

USES_SIZE = {"value", "label", "alarm"}
USES_LABEL = {"value", "label", "lamp"}

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
    ("knob",  "Knob",       dict(w=22, h=22, field="enc_total")),
    ("disc",  "Record",     dict(w=22, h=22, field="none")),
    ("switch", "Switch",    dict(w=40, h=10, field="none",
                                 opts={"which": "sw1"})),
    ("alarm", "Alarm in",   dict(w=44, h=11, size=11, field="none")),
    ("blinker", "Signals",  dict(w=128, h=13, field="none",
                                 opts={"which": "both"})),
]

ABOUT = {
    "value": "a number, as big as you like",
    "label": "a line of text, or a field that is text",
    "bar": "fills left to right - speed, revs, fuel, the track",
    "vbar": "the same, standing up",
    "dial": "a needle over a scale of ticks",
    "lamp": "a dot that lights while the field is not zero - brake, HID",
    "box": "an empty frame, for grouping",
    "line": "a rule, for dividing",
    "axis": "two axes at once - the axis line shows when you are dead centre",
    "btn": "one button of the panel, lit while it is held",
    "btnrow": "all eight expander buttons, as the PANEL page has them",
    "knob": "the encoder, as a knob - on the total it turns as the real one "
            "does, a detent at a time",
    "disc": "the record from MUSIC, turning while something plays",
    "switch": "a slide switch, with the position it is in filled",
    "alarm": "how long until the next alarm - gone from the panel when there "
             "is none, still here so you can place it",
    "blinker": "the turn-signal arrows, blinking with the car's own - both "
               "at once is the hazards",
}


HEADER_H = 9


def bell(d, x, y, fill=1):
    """Five pixels wide, drawn rather than written: the panel's font has no
    such glyph and a second font for one picture would be absurd."""
    d.line([x + 1, y, x + 3, y], fill=fill)
    d.line([x, y + 1, x, y + 4], fill=fill)
    d.line([x + 4, y + 1, x + 4, y + 4], fill=fill)
    d.line([x - 1, y + 5, x + 5, y + 5], fill=fill)
    d.point((x + 2, y + 6), fill=fill)


def header_image(text, right="", alarm=""):
    """The whole bar as its own little image, so it can arrive as a whole.

    Drawn apart from the page because a wipe needs something to wipe IN - you
    cannot reveal part of a thing you are drawing straight onto the screen.
    """
    im = Image.new("1", (W, HEADER_H), 0)
    d = ImageDraw.Draw(im)
    d.fontmode = "1"
    header_bar(d, text, right, 0, alarm, bar=True)
    return im


def header_bar(d, text, right="", shift=0, alarm="", bar=True):
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

    on_bar = bar and shift < HEADER_H
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


def bounds(w, data):
    """(dx, dy, width, height) of what the widget actually draws.

    The offset matters. PIL's text() puts the glyphs down from the origin with
    the font's own bearing, so a line of text starts a pixel or two right of
    and below where it was asked for - and a box measured from the origin sat
    that far left of and above the letters. Which is exactly how it felt: a
    hitbox a few pixels out of true.
    """
    if not HAVE_PIL or (w.kind not in AUTO_SIZE and w.kind not in AUTO_ALARM):
        return 0, 0, w.w, w.h
    try:
        d = ImageDraw.Draw(Image.new("1", (1, 1)))
        if w.kind in AUTO_ALARM:
            txt = _alarm_parts(w, dict(data, _editing=1))[0] or "0h00"
            box = d.textbbox((10, -1), txt, font=_font(w.size))
            x0, y0 = min(0, int(box[0])), min(1, int(box[1]))
            x1, y1 = max(6, int(box[2])), max(8, int(box[3]))
            return x0, y0, x1 - x0, y1 - y0
        txt = _text_of(w, data) or " "
        box = d.textbbox((0, 0), txt, font=_font(w.size))
        dx, dy = int(box[0]), int(box[1])
        tw, th = int(box[2]) - dx, int(box[3]) - dy
        if w.kind == "value" and w.label:
            cap = d.textbbox((0, 0), w.label, font=_font(9))
            dx = min(dx, int(cap[0]))
            tw = max(tw, int(cap[2]) - dx)
            th, dy = th + 9 + (dy - int(cap[1])), int(cap[1])
        return dx, dy, max(2, tw), max(2, th)
    except Exception:
        return 0, 0, w.w, w.h


def measure(w, data):
    """Just the size, for everything that only wants that."""
    b = bounds(w, data)
    return b[2], b[3]


def wipe(im, reveal):
    """A left-to-right reveal, with the dither edge the splash uses.

    Whole columns, then four columns of thinning dither at the front: one bit
    per pixel has no half-lit, so a soft edge is a pattern - the same one the
    firmware wipes PANEL in with at boot.
    """
    if reveal >= 1.0:
        return im
    if reveal <= 0.0:
        return Image.new("1", im.size, 0)
    out = Image.new("1", im.size, 0)
    cut = int(round(im.width * reveal))
    if cut > 0:
        out.paste(im.crop((0, 0, cut, im.height)), (0, 0))
    px, src = out.load(), im.load()
    for x in range(max(0, cut), min(im.width, cut + 4)):
        level = 12 - 3 * (x - cut)
        for y in range(im.height):
            if src[x, y] and BAYER4[((y & 3) << 2) | (x & 3)] < level:
                px[x, y] = 1
    return out


def render(widgets, data, header=None):
    """The whole 128x32 page as a PIL image, or None without PIL.

    `header` is ("NAME", "3/11", shift, "6h12", reveal): the name, where the
    page sits in the rotation, how far the board's own bar has slid away (so
    yours retracts with it), the countdown to the next alarm (which stays after
    the bar has gone), and how much of the bar has arrived - 0 to 1, for the
    wipe that plays when any of it changes. None means the whole screen is
    yours. The widgets are drawn AFTER it, so a layout
    made before the header existed still shows rather than disappearing under
    a bar nobody has moved it out of yet.
    """
    if not HAVE_PIL:
        return None
    img = Image.new("1", (W, H), 0)
    d = ImageDraw.Draw(img)
    d.fontmode = "1"
    if header:
        try:
            name = header[0]
            right = header[1] if len(header) > 1 else ""
            shift = header[2] if len(header) > 2 else 0
            alarm = header[3] if len(header) > 3 else ""
            reveal = header[4] if len(header) > 4 else 1.0
            if shift < HEADER_H:
                bar = header_image(name, right, alarm)
                img.paste(wipe(bar, reveal), (0, -shift))
            elif alarm:
                badge = Image.new("1", (W, HEADER_H), 0)
                bd = ImageDraw.Draw(badge)
                bd.fontmode = "1"
                header_bar(bd, name, right, 0, alarm, bar=False)
                img.paste(wipe(badge, reveal), (0, 0))
        except Exception:
            pass
    for w in widgets:
        fn = DRAW.get(w.kind)
        if not fn:
            continue
        try:
            fn(d, w, data)
        except Exception:
            pass
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
