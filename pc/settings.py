"""Settings that survive a restart.

A JSON file in %LOCALAPPDATA%\\PicoPanel, not next to the executable: the exe
may sit in a folder you can't write to, and a setting that can't be saved is
worse than one that doesn't exist.
"""

import json
import os

SCHEMA = 2


DEFAULTS = {
    "yield_outgauge": False,

    "audio_target": None,

    "unicode_titles": True,

    "app_own_volume": False,

    "karaoke": False,

    "page_order": None,
    "rpm_style": 0,
    "i2c_khz": 400,

    "layouts": {},

    "custom_subs": {},

    "page_names": {},

    "page_headers": {},

    "custom_pages": [0],

    "alarms": [],

    "phone_bridge": False,

    "anim_style": "classic",

    "aod_pages": [],

    "hid_pages": [],

    "page_presets": {},

    "schema": SCHEMA,
}


def _migrate(s, was):
    """Bring an older settings file up to the current numbering.

    `was` is the version read from the FILE, not from the merged settings: the
    defaults carry the current version, so taking it from the merge would say
    every old file was already up to date - which it duly did, once.

    Done on load and written straight back, so it happens once. The alternative
    is asking everyone to redo their page order, which is the sort of thing that
    makes people stop dragging pages about.
    """
    try:
        was = int(was or 1)
    except (TypeError, ValueError):
        was = 1
    if was >= SCHEMA:
        return s

    FIRST, OLD_N, NEW_N = 3, 4, 8
    shift = NEW_N - OLD_N

    def fix(pg):
        pg = int(pg)
        return pg + shift if pg >= FIRST + OLD_N else pg

    if isinstance(s.get("page_order"), list):
        s["page_order"] = [fix(p) for p in s["page_order"]]
    if isinstance(s.get("page_presets"), dict):
        s["page_presets"] = {k: [fix(p) for p in v]
                             for k, v in s["page_presets"].items()
                             if isinstance(v, list)}

    have = {i for i in range(OLD_N)
            if (s.get("layouts") or {}).get(str(i))}
    for p in (s.get("page_order") or []):
        if FIRST <= p < FIRST + OLD_N:
            have.add(p - FIRST)
    s["custom_pages"] = sorted(have) or [0]

    s["schema"] = SCHEMA
    save(s)
    return s


def _path():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "PicoPanel")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "settings.json")


def load():
    try:
        with open(_path(), encoding="utf-8") as f:
            stored = json.load(f)
    except (OSError, json.JSONDecodeError):
        stored = None
    s = dict(DEFAULTS)
    if not isinstance(stored, dict):
        return s
    s.update(stored)
    return _migrate(s, stored.get("schema"))


def save(s):
    try:
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
        return True, _path()
    except OSError as e:
        return False, str(e)
