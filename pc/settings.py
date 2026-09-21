"""Settings that survive a restart.

A JSON file in %LOCALAPPDATA%\\PicoPanel, not next to the executable: the exe
may sit in a folder you can't write to, and a setting that can't be saved is
worse than one that doesn't exist.
"""

import json
import os

# The shape of this file. Page numbers are the board's own enum, so a change
# there - going from four drawable pages to eight - renumbers everything after
# them, and an order written before that would put HID where SWITCHES is.
SCHEMA = 2


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

    # False: the app's channel in the Windows mixer, always. That is a real
    # per-application volume - Spotify moves, nothing else does - and it never
    # reaches into another program's window.
    #
    # True moves the slider inside the app instead, where it has one. It works,
    # but it is the more complicated of the two and it is off by choice, not
    # because it failed. The checkbox in the app turns it back on.
    "app_own_volume": False,

    # Synced lyrics on the KARAOKE page. OFF by default, and it is the only
    # thing in this program that talks to the internet: it asks lrclib.net for
    # the words, sending the artist, the title and the length. Nothing else,
    # and only once per track - but it is your listening, so you turn it on.
    "karaoke": False,

    # The board's own settings. It keeps these in its own flash now and comes
    # up with them on a charger, with no app anywhere; what is here is still
    # what wins while the app is running, and is sent on every connect.
    "page_order": None,     # None = the board's own order, all of them
    "rpm_style": 0,         # 0 a bar, 1 a needle
    "i2c_khz": 400,

    # The widgets you laid out, one list per face: {"0": [...], "0.1": [...]}.
    # Face 0 is filed under the bare slot number, which is the key it had
    # before pages had faces - so a settings file written back then reads with
    # every layout exactly where it was.
    # The board never sees these - it only ever gets the finished picture.
    "layouts": {},

    # How many faces each of your pages has: {"0": 3}. Missing means one.
    # USER walks them without leaving the page, the way it walks GAME's - two
    # faces of one page are one page, and having to go all the way round the
    # rotation to see the other half of a thing you laid out is not navigation.
    "custom_subs": {},

    # What you have called your own pages, by slot: {"0": "TURBO"}. The board
    # calls them MINE 1..8 and always will - the name is drawn into the picture
    # the app sends, so it is the app's to keep.
    "page_names": {},

    # Which of them draw the board's header bar - the name and the counter, in
    # the same nine rows the board uses for its own pages. On by default: a
    # page with no name on it is the odd one out in the rotation.
    "page_headers": {},

    # Which of the board's eight drawable pages you have actually made. The
    # slots all exist in the firmware; this says which ones are yours, so the
    # list shows the pages you built and not eight empty ones.
    "custom_pages": [0],

    # Alarms: [{"at": "07:30", "text": "GET UP", "on": true}]. Handed to the
    # board, which keeps them in its own flash and rings them itself over
    # whatever page is up - an alarm you only see on the right page is not one,
    # and one that needs the app running is not one either.
    "alarms": [],

    # Listen for the phone's alarms on port 8787. Off by default: it opens a
    # port on the network, and a program that starts listening without being
    # asked is one you have to trust further than this deserves.
    "phone_bridge": False,

    # How a changed header arrives: "classic" down from the top, the way the
    # board's own header comes back; "wipe" in from the left like the splash;
    # "type" a letter at a time; "none" at all.
    "anim_style": "classic",

    # Pages that keep the screen lit while they are showing, by number. The
    # panel dims after twenty idle seconds and goes dark after a minute, which
    # is right for a panel you glance at and wrong for one you are watching.
    "aod_pages": [],

    # Pages that hold the USER button while the gamepad is armed, by number.
    # The GAME page has always done it; this is that made yours. Disarming is
    # the way out and the only one - a second way out is a way to leave by
    # accident, which is the thing being prevented.
    "hid_pages": [],

    # Named page arrangements: {"Driving": [1, 3, 0], ...}. Yours, not the
    # board's - it only ever hears the one list that is currently in force.
    "page_presets": {},

    # Which shape this file is in - see _migrate below.
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

    FIRST, OLD_N, NEW_N = 3, 4, 8      # custom pages: where they start, how many
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

    # Which of your own pages existed before there was a word for it: the ones
    # in the rotation, and any you had drawn on. Not the empty spares - those
    # were only ever there because the firmware had four of them.
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
        stored = None   # missing or broken: fall back to the defaults
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
