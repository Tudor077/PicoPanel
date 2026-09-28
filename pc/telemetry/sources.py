"""The telemetry sources, one per game.

Each source keeps its own thread and the last snapshot it received. The hub
starts them all and picks whichever spoke most recently, so there's nothing to
switch when you change games.
"""

import http.client
import json
import math
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .model import Telemetry


class RevRange:
    """Learns the tacho's full scale and the redline by itself.

    Ported from CorsaConnect (server/src/server.rs). The idea, briefly:

    The redline is NOT "the highest rpm seen" - that creeps up slowly and is
    always behind. It's the limiter: the point where, with the throttle floored,
    the rpm stops climbing at all. That's the rev cut, and it's unmistakable.

    Until we find it the tacho stays BIG with no red zone. Otherwise a freshly
    spawned car at idle would have its needle pinned to a redline invented out
    of 800 rpm.

    Two reasons to relearn:
      - it revved past the supposed limiter -> it was too low, look higher
      - a gap in the stream (radial menu, changed car, reloaded the map) ->
        probably a different car, start over

    LIMIT: an engine that never passes RPM_MIN never learns a redline, and that
    is exactly right - the threshold exists so idle isn't mistaken for the
    limiter. Trucks fall in that category, but they don't need it: the ETS2
    source gets its full scale straight from the SDK.
    """

    THROTTLE_MIN = 0.85
    RPM_MIN = 3000.0
    HOLD_FRAMES = 30
    OVERSHOOT = 100.0
    GAP_S = 1.5
    FLOOR = 9000.0

    def __init__(self):
        self.reset()
        self.last_seen = 0.0

    def reset(self):
        self.peak = 0.0
        self.since_peak = 0
        self.limiter = 0.0

    def update(self, rpm, throttle, now=None):
        """Returns (rpm_max, redline). redline = 0 until it has been learned."""
        now = time.time() if now is None else now

        if self.last_seen and (now - self.last_seen) > self.GAP_S:
            self.reset()
        self.last_seen = now

        if rpm > self.peak:
            self.peak = rpm
            self.since_peak = 0
            if self.peak > self.limiter + self.OVERSHOOT:
                self.limiter = 0.0
        else:
            self.since_peak += 1

        if (self.limiter == 0.0
                and throttle > self.THROTTLE_MIN
                and self.peak > self.RPM_MIN
                and self.since_peak >= self.HOLD_FRAMES):
            self.limiter = self.peak

        if self.limiter > 0.0:
            return self.limiter * 1.08, self.limiter
        return max(self.FLOOR, self.peak * 1.05), 0.0


class JsonPoller:
    """A JSON endpoint on the local machine, polled over one kept-alive
    connection.

    Two things make this fast, and both had to be measured to be believed.

    1. The connection is reused. A fresh TCP connection per request gives about
       240 requests a second against a local stub; a reused one about 3200.

    2. It connects by ADDRESS, not by the name "localhost". On Windows that name
       resolves to ::1 before 127.0.0.1, and a game that listens only on IPv4
       - which is most of them - leaves the IPv6 attempt to time out. Measured
       here: 1002 ms per request through "localhost", 0.7 ms through
       "127.0.0.1". Same server, same code, fourteen hundred times slower.
       That one second per request was the lag on the panel.

    So we resolve the name once, keep the candidates IPv4-first, and pin the one
    that answers. Nothing is assumed: if a game really does listen on IPv6 only,
    the IPv4 candidate fails fast and the IPv6 one is used and pinned instead.

    The connection is reopened on any error, so a game that closes it, restarts,
    or was never running costs one failed attempt and nothing more.
    """

    CONNECT_TIMEOUT = 0.35

    def __init__(self, host, port, timeout=1.0):
        self.host, self.port, self.timeout = host, port, timeout
        self._conn = None
        self._addrs = None
        self._pinned = None

    def _candidates(self):
        """Every address the name resolves to, IPv4 first, without duplicates."""
        if self._addrs is not None:
            return self._addrs
        try:
            infos = socket.getaddrinfo(self.host, self.port,
                                       type=socket.SOCK_STREAM)
        except OSError:
            self._addrs = [self.host]
            return self._addrs
        v4 = [i[4][0] for i in infos if i[0] == socket.AF_INET]
        v6 = [i[4][0] for i in infos if i[0] == socket.AF_INET6]
        out, seen = [], set()
        for a in v4 + v6:
            if a not in seen:
                seen.add(a)
                out.append(a)
        self._addrs = out or [self.host]
        return self._addrs

    def close(self):
        if self._conn:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None

    def _connect(self):
        """A live connection, or None. Tries the pinned address first."""
        order = self._candidates()
        if self._pinned:
            order = [self._pinned] + [a for a in order if a != self._pinned]
        for addr in order:
            try:
                sock = socket.create_connection((addr, self.port),
                                                timeout=self.CONNECT_TIMEOUT)
            except OSError:
                continue
            sock.settimeout(self.timeout)
            conn = http.client.HTTPConnection(addr, self.port,
                                              timeout=self.timeout)
            conn.sock = sock
            self._pinned = addr
            return conn
        self._pinned = None
        return None

    def get(self, path):
        """Parsed JSON, or None. Never raises."""
        for attempt in (1, 2):
            try:
                if self._conn is None:
                    self._conn = self._connect()
                    if self._conn is None:
                        return None
                self._conn.request("GET", path)
                return json.loads(self._conn.getresponse().read())
            except Exception:
                self.close()
                if attempt == 2:
                    return None
        return None


class Source:
    """The shared contract. start() must not throw when the game isn't there -
    a source with no game should simply stay quiet."""

    name = "?"

    def __init__(self):
        self._last = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.status = "starting..."

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._guarded, daemon=True)
        self._thread.start()

    def _guarded(self):
        """Catches any exception from the source's thread and puts it in status.

        Without this, an error in a thread just prints to stderr and the thread
        dies - and the app runs under pythonw, with no console, so none of it is
        visible. The source merely looks quiet, and you go hunting in the game
        that "isn't sending".
        """
        try:
            self._run()
        except Exception as e:
            self.status = "crashed: %s: %s" % (type(e).__name__, e)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)

    def latest(self):
        with self._lock:
            return self._last

    def _put(self, tel):
        with self._lock:
            self._last = tel

    def _run(self):
        raise NotImplementedError


class OutGaugeSource(Source):
    """BeamNG: Options -> Others -> OutGauge -> on, IP 127.0.0.1, the port below.
    LFS and a few rally sims speak the same protocol.

    The packet is 92 bytes, or 96 if you set an OutGauge ID - that last field is
    optional, which is why we accept both lengths.
    """

    name = "OutGauge"

    _FMT = "<I4sHBBfffffffIIfff16s16s"
    _SIZE = struct.calcsize(_FMT)

    def __init__(self, port=4444, label="BeamNG"):
        super().__init__()
        self.port = port
        self.label = label

    @classmethod
    def decode(cls, data):
        """Raw packet -> Telemetry. None if the length is wrong."""
        if len(data) not in (cls._SIZE, cls._SIZE + 4):
            return None
        f = struct.unpack(cls._FMT, data[: cls._SIZE])
        (_time, car, _flags, gear, _plid, speed, rpm, turbo, engtemp,
         fuel, _oilp, _oilt, _dash, show, thr, brk, _clu, d1, _d2) = f

        g = -1 if gear == 0 else (gear - 1)

        blk = (1 if (show & 32) else 0) | (2 if (show & 64) else 0)

        return Telemetry(
            src="",
            kind="car",
            blinkers=blk,
            speed_kmh=speed * 3.6,
            rpm=rpm,
            gear=g,
            fuel_pct=fuel * 100.0,
            throttle=thr,
            brake=brk,
            turbo_bar=turbo,
            engine_c=engtemp,
            text=d1.split(b"\x00")[0].decode("ascii", "ignore"),
        )

    def _run(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(0.5)
            sock.bind(("0.0.0.0", self.port))
        except OSError as e:
            self.status = f"can't listen on {self.port}: {e}"
            return
        self.status = f"listening on UDP {self.port}"
        revs = RevRange()
        learned = False
        while not self._stop.is_set():
            try:
                data, _addr = sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                break
            tel = self.decode(data)
            if not tel:
                continue
            tel.rpm_max, tel.redline = revs.update(tel.rpm, tel.throttle)
            if tel.redline and not learned:
                learned = True
                self.status = f"redline learned: {tel.redline:.0f} rpm"
            elif not tel.redline and learned:
                learned = False
                self.status = "relearning the rev range (different car?)"
            tel.src = self.label
            self._put(tel)
        sock.close()


class Ets2HttpSource(Source):
    """Reads from the ETS2 telemetry server (the JSON API on localhost).

    The game exposes nothing out of the box - you need the SDK plugin plus the
    server that publishes it over HTTP. See the README.

    JSON rather than shared memory, on purpose: if something in the schema
    changes you see it immediately and fix it in one line. With binary offsets a
    mismatch passes in silence and you end up reading the speed out of some
    other field.
    """

    name = "ETS2"

    IDLE_PERIOD = 3.0

    def __init__(self, host="localhost", port=25555,
                 path="/api/ets2/telemetry", hz=60):
        super().__init__()
        self.host, self.port, self.path = host, port, path
        self.url = "http://%s:%d%s" % (host, port, path)
        self.period = 1.0 / hz

    @staticmethod
    def decode(doc, label="ETS2"):
        truck = doc.get("truck") or {}
        game = doc.get("game") or {}
        if not game.get("connected", False):
            return None

        cap = truck.get("fuelCapacity") or 0
        fuel = truck.get("fuel") or 0
        pct = (fuel / cap * 100.0) if cap else -1.0

        nav = doc.get("navigation") or {}
        job = doc.get("job") or {}
        text = job.get("destinationCity") or nav.get("nextRestStopTime") or ""

        rpm_max = truck.get("engineRpmMax") or 0.0

        def _lamp(side):
            v = truck.get("blinker%sOn" % side)
            return truck.get("blinker%sActive" % side) if v is None else v

        blk = (1 if _lamp("Left") else 0) | (2 if _lamp("Right") else 0)

        return Telemetry(
            src=label,
            kind="car",
            blinkers=blk,
            speed_kmh=abs(truck.get("speed") or 0.0),
            rpm=truck.get("engineRpm") or 0.0,
            rpm_max=rpm_max,
            redline=rpm_max * 0.90 if rpm_max else 0.0,
            gear=truck.get("gear") or 0,
            fuel_pct=pct,
            throttle=truck.get("gameThrottle") or 0.0,
            brake=truck.get("gameBrake") or 0.0,
            engine_c=truck.get("waterTemperature") or 0.0,
            text=text,
        )

    def _run(self):
        poll = JsonPoller(self.host, self.port)
        self.status = f"polling {self.url}"
        warned = False
        wait = self.IDLE_PERIOD
        while not self._stop.is_set():
            doc = poll.get(self.path)
            if doc is None:
                wait = self.IDLE_PERIOD
                if not warned:
                    self.status = "the telemetry server isn't answering"
                    warned = True
            else:
                wait = self.period
                tel = self.decode(doc)
                if tel:
                    self._put(tel)
                    self.status = "connected"
                    warned = False
                else:
                    self.status = "server up, game not connected"
            self._stop.wait(wait)
        poll.close()


class MsfsSource(Source):
    """Through SimConnect.  pip install SimConnect

    UNTESTED: I don't have the simulator installed to check the variable names
    live. The structure is the standard one; if a name has changed you'll see it
    in the status and fix it in the table below.
    """

    name = "MSFS"

    VARS = {
        "speed_kmh": ("AIRSPEED_INDICATED", 1.852),
        "rpm": ("GENERAL_ENG_RPM:1", 1.0),
        "alt_m": ("PLANE_ALTITUDE", 0.3048),
        "fuel_pct": ("FUEL_TOTAL_QUANTITY_WEIGHT", None),
    }

    def __init__(self, hz=30):
        super().__init__()
        self.period = 1.0 / hz

    def _run(self):
        self.status = "looking for the simulator..."
        try:
            from SimConnect import SimConnect, AircraftRequests
        except ImportError:
            self.status = "package missing: pip install SimConnect"
            return
        sm = aq = None
        while not self._stop.is_set() and aq is None:
            try:
                sm = SimConnect()
                aq = AircraftRequests(sm, _time=0)
            except Exception as e:
                self.status = f"the simulator isn't answering ({e})"
                self._stop.wait(5.0)
        if aq is None:
            return

        self.status = "connected"
        while not self._stop.is_set():
            try:
                ias = aq.get("AIRSPEED_INDICATED") or 0.0
                rpm = aq.get("GENERAL_ENG_RPM:1") or 0.0
                alt = aq.get("PLANE_ALTITUDE") or 0.0
                pct = aq.get("FUEL_TOTAL_QUANTITY") or 0.0
                cap = aq.get("FUEL_TOTAL_CAPACITY") or 0.0
                vs = aq.get("VERTICAL_SPEED") or 0.0
                hdg = aq.get("PLANE_HEADING_DEGREES_MAGNETIC") or 0.0
                c1 = aq.get("COM_ACTIVE_FREQUENCY:1") or 0.0
                c2 = aq.get("COM_ACTIVE_FREQUENCY:2") or 0.0
                sqk = aq.get("TRANSPONDER_CODE:1") or 0

                ap = []
                if aq.get("AUTOPILOT_MASTER"):
                    ap.append("AP")
                    if aq.get("AUTOPILOT_HEADING_LOCK"):
                        ap.append("HDG")
                    if aq.get("AUTOPILOT_ALTITUDE_LOCK"):
                        ap.append("ALT")
                    if aq.get("AUTOPILOT_NAV1_LOCK"):
                        ap.append("NAV")
                    if aq.get("AUTOPILOT_APPROACH_HOLD"):
                        ap.append("APR")

                hdg_deg = math.degrees(hdg) if hdg < 7.0 else hdg

                self._put(Telemetry(
                    src="MSFS",
                    kind="air",
                    speed_kmh=ias * 1.852,
                    kts=ias,
                    vspeed_fpm=vs * 60.0,
                    alt_ft=alt,
                    hdg=hdg_deg,
                    rpm=rpm,
                    rpm_max=2700,
                    gear=0,
                    fuel_pct=(pct / cap * 100.0) if cap else -1.0,
                    alt_m=alt * 0.3048,
                    com1=("%.3f" % c1) if c1 else "",
                    com2=("%.3f" % c2) if c2 else "",
                    squawk=("%04o" % int(sqk)) if sqk else "",
                    ap_text=" ".join(ap),
                ))
            except Exception as e:
                self.status = f"read error: {e}"
            self._stop.wait(self.period)


class HttpIngestSource(Source):
    """Listens for JSON POSTs and takes them as telemetry.

    The accepted fields are exactly the model's: src, speed_kmh, rpm, rpm_max,
    gear, fuel_pct, alt_m, text. Anything else is ignored.

    This is also the only realistic route for Roblox: HttpService isn't allowed
    to send to localhost or private addresses, so you need a public tunnel
    pointing at this port. See the README.
    """

    name = "HTTP"

    def __init__(self, port=8099, bind="127.0.0.1"):
        super().__init__()
        self.port = port
        self.bind = bind
        self._srv = None

    @staticmethod
    def decode(doc):
        if not isinstance(doc, dict):
            return None
        t = Telemetry(src=str(doc.get("src", "HTTP")))
        for key in ("speed_kmh", "rpm", "rpm_max", "alt_m", "fuel_pct"):
            if key in doc:
                try:
                    setattr(t, key, float(doc[key]))
                except (TypeError, ValueError):
                    pass
        if "gear" in doc:
            try:
                t.gear = int(doc["gear"])
            except (TypeError, ValueError):
                pass
        if "text" in doc:
            t.text = str(doc["text"])
        return t

    def _run(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(n)
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")
                try:
                    tel = outer.decode(json.loads(raw))
                except json.JSONDecodeError:
                    return
                if tel:
                    outer._put(tel)

            def log_message(self, *a):
                pass

        try:
            self._srv = ThreadingHTTPServer((self.bind, self.port), Handler)
        except OSError as e:
            self.status = f"can't listen on {self.port}: {e}"
            return
        self.status = f"listening for POSTs on http://{self.bind}:{self.port}/"
        self._srv.serve_forever(poll_interval=0.2)

    def stop(self):
        if self._srv:
            self._srv.shutdown()
        super().stop()


class DemoSource(Source):
    name = "DEMO"

    def _run(self):
        self.status = "generating fake data"
        t0 = time.time()
        while not self._stop.is_set():
            t = time.time() - t0
            speed = 60 + 55 * math.sin(t / 7.0)
            rpm = 900 + abs(speed) * 14
            self._put(Telemetry(
                src="DEMO",
                speed_kmh=max(0.0, speed),
                rpm=rpm,
                rpm_max=2700,
                redline=2400,
                gear=max(1, min(12, int(abs(speed) / 10) + 1)),
                fuel_pct=50 + 40 * math.sin(t / 30.0),
                throttle=max(0.0, math.sin(t / 7.0)),
                brake=max(0.0, -math.sin(t / 7.0)),
                engine_c=85 + 5 * math.sin(t / 11.0),
                turbo_bar=max(0.0, 1.2 * math.sin(t / 7.0)),
                blinkers=[0, 1, 2, 3][int(t / 5) % 4],
                text="test, no game",
            ))
            self._stop.wait(0.1)


class CorsaSource(Source):
    """Receives the packets CorsaConnect already sends to the phone.

    Why it exists: OutGauge is a single-listener protocol - the game sends to
    exactly one address:port, and whoever binds 4444 first gets it. If
    CorsaConnect is running too, one of us ends up with no data.

    Rather than fight over the port, CorsaConnect keeps 4444 and sends us a
    copy. That turns out better than raw OutGauge: its packet already carries
    the learned redline, plus the slide and impact from MotionSim.

    Turn it on in CorsaConnect's launcher, in the PICOPANEL card (or with
    CORSACONNECT_MIRROR=127.0.0.1:5051 in the environment). Without it,
    CorsaConnect behaves exactly as before.
    """

    name = "Corsa"

    _FMT = "<2sBb11fHI16s16s"
    _SIZE = struct.calcsize(_FMT)
    _VERSION = 7

    def __init__(self, port=5051):
        super().__init__()
        self.port = port

    @classmethod
    def decode(cls, data):
        if len(data) != cls._SIZE:
            return None
        f = struct.unpack(cls._FMT, data)
        (magic, ver, gear, speed, rpm, fuel, turbo, etemp, thr, brk,
         _slip, _impact, rpm_max, redline, _flags, show, d1, _d2) = f
        if magic != b"CT":
            return None
        if ver != cls._VERSION:
            return None

        g = -1 if gear == 0 else (gear - 1)
        blk = (1 if (show & 32) else 0) | (2 if (show & 64) else 0)

        return Telemetry(
            src="Corsa",
            kind="car",
            speed_kmh=speed,
            rpm=rpm,
            rpm_max=rpm_max,
            redline=redline,
            gear=g,
            fuel_pct=fuel * 100.0,
            throttle=thr,
            brake=brk,
            turbo_bar=turbo,
            engine_c=etemp,
            blinkers=blk,
            text=d1.split(b"\x00")[0].decode("ascii", "ignore"),
        )

    def _run(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(0.5)
            sock.bind(("0.0.0.0", self.port))
        except OSError as e:
            self.status = "can't listen on %d: %s" % (self.port, e)
            return
        self.status = "waiting for the mirror on UDP %d" % self.port
        seen = False
        while not self._stop.is_set():
            try:
                data, _addr = sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                break
            tel = self.decode(data)
            if not tel:
                continue
            if not seen:
                seen = True
                self.status = "receiving from CorsaConnect"
            self._put(tel)
        sock.close()


class WarThunderSource(Source):
    """Reads the local HTTP telemetry the game serves on port 8111.

    No plugin and no setting: War Thunder starts that server by itself whenever
    the game runs. Two endpoints matter here:

      /indicators  the instrument panel. Works for ground vehicles AND aircraft,
                   but with completely different field sets.
      /state       the flight model. Rich numbers, with units in the key names
                   ("IAS, km/h"), and only meaningful in an aircraft.

    TANK OR PLANE IS DECIDED FROM THE DATA, not from anything you set. The two
    field sets barely overlap, so the vehicle gives itself away: an artificial
    horizon and a variometer mean a cockpit, a gear count and a crew roster mean
    a turret. Switching vehicle in the game switches the pages on the panel with
    no help from you.

    A hangar or a menu answers with valid=false, which we treat as "no vehicle"
    rather than as zeroes - otherwise the panel would show a parked 0 km/h as if
    you were driving.
    """

    name = "WarThunder"

    AIR_KEYS = ("aviahorizon_pitch", "aviahorizon_roll", "vario",
                "altitude_10k", "compass")
    GROUND_KEYS = ("gear_num", "crew_total", "driver_state", "stabilizer",
                   "driving_direction_mode")

    def __init__(self, host="localhost", port=8111, hz=60):
        super().__init__()
        self.host, self.port = host, port
        self.period = 1.0 / hz
        self.idle_period = 3.0

    @staticmethod
    def classify(ind, state=None):
        """'air', 'car', or None when no vehicle is in play."""
        if not isinstance(ind, dict) or not ind.get("valid"):
            return None
        if any(k in ind for k in WarThunderSource.AIR_KEYS):
            return "air"
        if any(k in ind for k in WarThunderSource.GROUND_KEYS):
            return "car"
        if isinstance(state, dict) and state.get("valid"):
            return "air"
        return "car"

    @staticmethod
    def _f(d, *keys, default=0.0):
        """First key that is present and numeric. The state endpoint spells its
        keys with units, and the spelling has changed between patches, so every
        lookup takes a list of names rather than one."""
        for k in keys:
            v = d.get(k)
            if isinstance(v, (int, float)):
                return float(v)
        return default

    @classmethod
    def decode(cls, ind, state=None, peak_rpm=0.0):
        kind = cls.classify(ind, state)
        if kind is None:
            return None
        st = state if isinstance(state, dict) else {}

        rpm = cls._f(ind, "rpm") or cls._f(st, "RPM 1", "RPM throttle 1, %")
        peak = max(peak_rpm, rpm)
        rpm_max = math.ceil(max(peak, 1000.0) / 500.0) * 500.0

        if kind == "car":
            gear = int(cls._f(ind, "gear"))
            if ind.get("driving_direction_mode") is False and gear != 0:
                gear = -abs(gear)
            return Telemetry(
                src="WT",
                kind="wtgnd",
                speed_kmh=abs(cls._f(ind, "speed")),
                rpm=rpm,
                rpm_max=rpm_max,
                gear=gear,
                fuel_pct=-1.0,
                engine_c=cls._f(ind, "water_temperature", "oil_temperature"),
                crew="%d/%d" % (int(cls._f(ind, "crew_current")),
                                int(cls._f(ind, "crew_total"))),
                text=str(ind.get("type", ""))[:24],
            )

        fuel = cls._f(st, "Mfuel, kg")
        fuel0 = cls._f(st, "Mfuel0, kg")
        ias = cls._f(st, "IAS, km/h") or cls._f(ind, "speed")

        return Telemetry(
            src="WT",
            kind="wtair",
            speed_kmh=ias,
            kts=ias / 1.852,
            alt_ft=cls._f(st, "H, m", default=cls._f(ind, "altitude_hour")) * 3.28084,
            vspeed_fpm=cls._f(st, "Vy, m/s", default=cls._f(ind, "vario")) * 196.85,
            hdg=cls._f(ind, "compass"),
            rpm=rpm,
            rpm_max=rpm_max,
            gear=0,
            fuel_pct=(fuel / fuel0 * 100.0) if fuel0 else -1.0,
            throttle=cls._f(st, "throttle 1, %") / 100.0,
            engine_c=cls._f(st, "water temp 1, C", default=cls._f(ind, "water_temperature")),
            gforce=cls._f(st, "Ny"),
            aoa=cls._f(st, "AoA, deg"),
            text=str(ind.get("type", ""))[:24],
        )

    def _run(self):
        poll = JsonPoller(self.host, self.port)
        self.status = "polling %s:%d" % (self.host, self.port)
        wait = self.idle_period
        peak = 0.0
        last_kind = None
        while not self._stop.is_set():
            ind = poll.get("/indicators")
            if ind is None:
                wait = self.idle_period
                self.status = "not running"
                last_kind = None
                self._stop.wait(wait)
                continue

            kind = self.classify(ind)
            st = poll.get("/state") if kind != "car" else None
            tel = self.decode(ind, st, peak)
            wait = self.period
            if tel is None:
                self.status = "game running, no vehicle (hangar?)"
            else:
                peak = max(peak, tel.rpm)
                if tel.kind != last_kind:
                    last_kind = tel.kind
                    peak = tel.rpm
                    self.status = "%s: %s" % (
                        "aircraft" if tel.kind == "wtair" else "ground vehicle",
                        tel.text or "?")
                self._put(tel)
            self._stop.wait(wait)
        poll.close()


ALL = {
    "outgauge": OutGaugeSource,
    "warthunder": WarThunderSource,
    "corsa": CorsaSource,
    "ets2": Ets2HttpSource,
    "msfs": MsfsSource,
    "http": HttpIngestSource,
    "demo": DemoSource,
}
