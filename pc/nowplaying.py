"""What's playing right now, from Windows itself.

Every player that puts controls on the lock screen - Spotify, any browser, VLC -
registers a media session with Windows. That session carries the title, the
artist, whether it's playing, and where it has got to. We read it rather than
talking to each app: one piece of code, and a player we've never heard of works
on the first try.

The position needs a word of warning. A session reports its position only when
something happens to it, and stamps that report with the time. Read it naively
and a progress bar jumps once every few seconds and sits still in between. So we
hand the panel the reported position AND how old that report is, and the board
runs the clock forward itself between updates.
"""

import asyncio
import threading
import time

try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _Manager,
    )
    HAVE_MEDIA = True
except Exception:                                   # pragma: no cover
    HAVE_MEDIA = False

try:
    from audio import BROWSERS, _browser_titles
except Exception:                                   # pragma: no cover
    BROWSERS, _browser_titles = {}, lambda: {}

# Transliteration and the wire-safe character set both live in one place: the
# same rules apply to a track title and to a city name in a truck sim.
from telemetry.text import clean as _clean

MAX_TITLE = 40
MAX_ARTIST = 22

PLAYING = 4          # GlobalSystemMediaTransportControlsSessionPlaybackStatus

# Anything in the app id that means "a browser tab", and so a video rather than
# a record: no artwork, no album, and a title worth the full width.
_BROWSER_HINTS = tuple(sorted(
    {b[:-4] for b in BROWSERS} | {"opera", "chrome", "msedge", "edge",
                                  "firefox", "brave", "vivaldi"}))


def _secs(v):
    """winrt hands back a timedelta here and a TimeSpan there, depending on
    version. Take whichever turns up."""
    try:
        return float(v.total_seconds())
    except AttributeError:
        return getattr(v, "duration", 0) / 1e7
    except Exception:
        return 0.0


class NowPlaying:
    """Polls the media sessions on its own thread. `snapshot()` never blocks."""

    def __init__(self, hz=4.0):
        self.period = 1.0 / hz
        self._lock = threading.Lock()
        self._snap = None
        self._stop = threading.Event()
        self._thread = None
        self.status = "" if HAVE_MEDIA else "winrt-Windows.Media.Control is missing"

    def start(self):
        if self._thread or not HAVE_MEDIA:
            return
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="nowplaying")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def snapshot(self):
        with self._lock:
            return self._snap

    # -- the work ---------------------------------------------------------
    def _kind(self, app_id):
        low = (app_id or "").lower()
        if "spotify" in low:
            return "s"
        if any(h in low for h in _BROWSER_HINTS):
            return "y"
        return "s"

    async def _read(self, mgr):
        sessions = list(mgr.get_sessions())
        if not sessions:
            return None

        # The one that's actually playing wins. Failing that, take the first -
        # a paused player is still worth showing, so the page isn't empty the
        # moment you hit pause.
        chosen = None
        for s in sessions:
            try:
                if s.get_playback_info().playback_status == PLAYING:
                    chosen = s
                    break
            except Exception:
                continue
        if chosen is None:
            chosen = sessions[0]

        try:
            props = await chosen.try_get_media_properties_async()
            info = chosen.get_playback_info()
            tl = chosen.get_timeline_properties()
        except Exception:
            return None

        raw = str(props.title or "")
        title = _clean(raw, MAX_TITLE)
        artist = _clean(props.artist, MAX_ARTIST)
        if not title:
            if not raw.strip():
                return None              # genuinely nothing playing
            # Something IS playing, we just can't write its name in this font -
            # Japanese, Chinese, emoji. A page with a placeholder still gives
            # you the disc, the time and the bar; claiming silence gives you
            # nothing and is a lie besides.
            title, artist = (artist, "") if artist else ("(untitled)", "")

        pos = _secs(tl.position)
        dur = _secs(tl.end_time)
        # How stale that position is. The board adds this on, then keeps
        # counting - see npPosAt in the firmware.
        age = 0.0
        try:
            last = tl.last_updated_time
            if last is not None:
                import datetime
                now = datetime.datetime.now(datetime.timezone.utc)
                age = max(0.0, (now - last).total_seconds())
                if age > 3600:          # a nonsense stamp; don't "correct" by an hour
                    age = 0.0
        except Exception:
            age = 0.0

        app_id = chosen.source_app_user_model_id
        kind = self._kind(app_id)

        # Only call it YouTube when the browser itself says so - the same rule
        # the volume targets use.
        label = "browser" if kind == "y" else "player"
        if kind == "y":
            for titles in _browser_titles().values():
                for t in titles:
                    if "youtube" in t.lower():
                        label = "YouTube"
                        break

        return {
            "title": title,
            "artist": artist,
            "playing": info.playback_status == PLAYING,
            "pos": pos + age,
            "dur": dur,
            "kind": kind,
            "app": app_id,
            "label": label,
        }

    def _run(self):
        async def loop():
            mgr = None
            while not self._stop.is_set():
                try:
                    if mgr is None:
                        mgr = await _Manager.request_async()
                    snap = await self._read(mgr)
                    self.status = "playing: %s" % snap["title"] if snap else "nothing playing"
                except Exception as e:
                    mgr = None
                    snap = None
                    self.status = "%s: %s" % (type(e).__name__, e)
                with self._lock:
                    self._snap = snap
                await asyncio.sleep(self.period)

        try:
            asyncio.run(loop())
        except Exception as e:                      # pragma: no cover
            self.status = "crashed: %s: %s" % (type(e).__name__, e)


def to_line(snap):
    """The '%np=' line the firmware reads, or the one that says nothing plays."""
    if not snap:
        return "%np=0"
    return "%%np=1;st=%d;sk=%s;ps=%d;du=%d;ti=%s;ar=%s" % (
        1 if snap["playing"] else 0,
        snap["kind"],
        int(round(snap["pos"])),
        int(round(snap["dur"])),
        snap["title"],
        snap["artist"])


if __name__ == "__main__":
    np = NowPlaying()
    np.start()
    try:
        for _ in range(12):
            time.sleep(0.5)
            s = np.snapshot()
            print(to_line(s))
            if s:
                print("    %-9s %s" % (s["label"], s["app"]))
    finally:
        np.stop()
