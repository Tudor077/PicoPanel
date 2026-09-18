"""Settings that survive a restart.

A JSON file in %LOCALAPPDATA%\\PicoPanel, not next to the executable: the exe
may sit in a folder you can't write to, and a setting that can't be saved is
worse than one that doesn't exist.
"""

import json
import os

DEFAULTS = {
    # When True we don't bind the OutGauge port (4444) and leave it to somebody
    # else - in practice to CorsaConnect, which wants it anyway. Telemetry then
    # comes from its mirror, on 5051, already enriched.
    "yield_outgauge": False,

    # Which app the volume knob points at, by the name shown on the panel.
    # None means nobody has chosen: the knob then follows whatever is playing,
    # so opening Spotify puts it on Spotify without a single button press.
    "audio_target": None,

    # True: titles are drawn on the PC with a real font and sent as pixels, so
    # Cyrillic and the rest appear as written. False: the board's own 5x7 ASCII
    # font, with anything else spelled out in Latin letters.
    "unicode_titles": True,
}


def _path():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "PicoPanel")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "settings.json")


def load():
    s = dict(DEFAULTS)
    try:
        with open(_path(), encoding="utf-8") as f:
            s.update(json.load(f))
    except (OSError, json.JSONDecodeError):
        pass            # missing or broken: fall back to the defaults
    return s


def save(s):
    try:
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
        return True, _path()
    except OSError as e:
        return False, str(e)
