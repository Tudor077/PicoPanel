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
