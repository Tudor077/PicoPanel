#!/usr/bin/env python3
"""
wtdump.py - shows what War Thunder is actually serving, and what we make of it.

    python wtdump.py            one snapshot
    python wtdump.py --watch    keep printing as you drive or fly
    python wtdump.py --save     also write the raw JSON to wt-sample.json

Start the game and get into a vehicle first. The hangar answers valid=false.

Why this exists: the telemetry source was written from the documented field
names, not from a running game. If a name has drifted between patches, this
prints the real keys next to what the source read from them, so the difference
is obvious instead of showing up as a zero on the panel.
"""

import json
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
from telemetry.sources import WarThunderSource as WT

BASE = "http://localhost:8111"


def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=1.5) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as e:
        return {"__error__": str(e)}


def snapshot(verbose=True):
    ind = get("/indicators")
    st = get("/state")

    if "__error__" in ind:
        print("no answer on %s - is the game running?  (%s)" % (BASE, ind["__error__"]))
        return None, ind, st

    kind = WT.classify(ind, st)
    tel = WT.decode(ind, st)

    if verbose:
        print("=" * 68)
        print("classified as:", kind or "no vehicle (hangar or menu)")
        print("  indicators keys:", ", ".join(sorted(ind.keys())[:18]))
        if isinstance(st, dict) and st.get("valid"):
            print("  state keys     :", ", ".join(sorted(st.keys())[:18]))
        print()
        if tel:
            print("  ->", tel.summary())
            print("  -> line to the board:")
            print("    ", tel.to_line())
        else:
            print("  -> nothing sent")
    return tel, ind, st


def main():
    watch = "--watch" in sys.argv
    save = "--save" in sys.argv

    tel, ind, st = snapshot()

    if save and "__error__" not in ind:
        with open("wt-sample.json", "w", encoding="utf-8") as f:
            json.dump({"indicators": ind, "state": st}, f, indent=2)
        print("\nraw JSON written to wt-sample.json")

    if not watch:
        return 0

    print("\nwatching - Ctrl+C to stop\n")
    try:
        while True:
            t, _, _ = snapshot(verbose=False)
            print("\r" + (t.summary() if t else "no vehicle") + " " * 20,
                  end="", flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
