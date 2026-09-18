"""Synced lyrics, for the karaoke page.

Where they come from: lrclib.net, which needs no account and no key and answers
in about 200 ms. One request per track, never per second.

**This is the only part of PicoPanel that talks to the internet.** What leaves
the machine is the artist, the track title and its length - enough to identify
the song and nothing else. It is off until you turn it on, because a feature
that quietly tells a stranger what you are listening to is not a feature.

The format back is LRC: a timestamp and a line, over and over.

    [00:26.78]Karma police
    [00:31.00]Arrest this man

Which is already most of the work, since the board runs its own clock on the
track position. The PC only has to say which line is current and when the next
one starts; the board switches on the millisecond rather than whenever the next
heartbeat happens to land.
"""

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://lrclib.net/api/get"
UA = "PicoPanel (https://github.com/Tudor077/PicoPanel)"
TIMEOUT = 6.0

# A track with no lyrics must not be asked about again every time it comes
# round. A track that failed for some other reason is worth one more try later.
MISS_S = 3600.0
ERROR_S = 60.0

_LINE = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")


def parse_lrc(text):
    """LRC -> [(start seconds, line)], in order, blanks kept.

    A blank line is meaningful here: it is the gap between verses, and showing
    nothing during the gap is right.
    """
    out = []
    for raw in (text or "").splitlines():
        stamps = list(_LINE.finditer(raw))
        if not stamps:
            continue
        words = raw[stamps[-1].end():].strip()
        for m in stamps:                    # one line can carry several times
            mins = int(m.group(1))
            secs = float(m.group(2).replace(":", "."))
            out.append((mins * 60 + secs, words))
    out.sort(key=lambda p: p[0])
    return out


def line_at(lines, pos_s):
    """Index of the line being sung at pos_s, or -1 before the first one."""
    lo, hi, found = 0, len(lines) - 1, -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if lines[mid][0] <= pos_s:
            found = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return found


class Lyrics:
    """Fetches in the background and hands out what it has.

    Never blocks the caller: `get` returns what is cached, and starts a fetch if
    that track has not been asked about. The audio thread runs the panel's
    heartbeat, and a network call on it would stall the volume.
    """

    def __init__(self, enabled=False):
        self.enabled = enabled
        self.status = ""
        self._lock = threading.Lock()
        self._cache = {}        # key -> [(t, line)] or None for "none exist"
        self._when = {}         # key -> when that answer was recorded
        self._busy = set()

    @staticmethod
    def _key(artist, title, dur):
        # A tuple, not a joined string: no separator to pick, and nothing to go
        # wrong if a title contains whatever that separator was.
        return ((artist or "").strip().lower(),
                (title or "").strip().lower(),
                int(dur or 0))

    def get(self, artist, title, dur):
        """[(t, line)] for that track, [] if there are none, None while looking."""
        if not self.enabled or not title:
            return []
        key = self._key(artist, title, dur)
        with self._lock:
            if key in self._cache:
                got = self._cache[key]
                age = time.time() - self._when.get(key, 0)
                if got is not None:
                    return got
                if age < (MISS_S if got is None else ERROR_S):
                    return []
            if key in self._busy:
                return None
            self._busy.add(key)
        threading.Thread(target=self._fetch, name="lyrics",
                         args=(key, artist, title, dur), daemon=True).start()
        return None

    def _fetch(self, key, artist, title, dur):
        lines, ok = [], False
        try:
            q = urllib.parse.urlencode({
                "artist_name": artist or "",
                "track_name": title or "",
                "duration": int(dur or 0),
            })
            req = urllib.request.Request(API + "?" + q, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                doc = json.loads(r.read())
            if doc.get("instrumental"):
                self.status = "instrumental: no words to show"
            lines = parse_lrc(doc.get("syncedLyrics"))
            ok = True
            self.status = ("%d lines for %s" % (len(lines), title) if lines
                           else "no synced lyrics for %s" % title)
        except urllib.error.HTTPError as e:
            ok = (e.code == 404)            # 404 means "we don't have it"
            self.status = ("no lyrics for %s" % title if ok
                           else "lrclib said %s" % e.code)
        except Exception as e:
            self.status = "%s: %s" % (type(e).__name__, e)
        with self._lock:
            self._cache[key] = lines if ok else None
            self._when[key] = time.time()
            self._busy.discard(key)


if __name__ == "__main__":
    import sys
    L = Lyrics(enabled=True)
    artist = sys.argv[1] if len(sys.argv) > 1 else "Radiohead"
    title = sys.argv[2] if len(sys.argv) > 2 else "Karma Police"
    dur = int(sys.argv[3]) if len(sys.argv) > 3 else 264
    while True:
        got = L.get(artist, title, dur)
        if got is not None:
            break
        time.sleep(0.1)
    print(L.status)
    for t, words in got[:8]:
        print("  %6.2f  %s" % (t, words))
    for pos in (0, 27, 32, 40):
        i = line_at(got, pos)
        cur = got[i][1] if i >= 0 else "(before the first line)"
        nxt = got[i + 1] if i + 1 < len(got) else None
        print("at %3ds -> %-28r next at %s" % (pos, cur, nxt[0] if nxt else "-"))
