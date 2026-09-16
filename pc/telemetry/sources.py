"""The telemetry sources, one per game.

Each source keeps its own thread and the last snapshot it received. The hub
starts them all and picks whichever spoke most recently, so there's nothing to
switch when you change games.
"""

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

    THROTTLE_MIN = 0.85       # "floored"
    RPM_MIN = 3000.0          # below this it's idle, not a limiter
    HOLD_FRAMES = 30          # how long the peak must sit still to be believed
    OVERSHOOT = 100.0         # how far past it we must go before relearning
    GAP_S = 1.5               # a silence that means "maybe a different car"
    FLOOR = 9000.0            # provisional scale while we know nothing

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
                self.limiter = 0.0      # it went past: that was too low
        else:
            self.since_peak += 1

        if (self.limiter == 0.0
                and throttle > self.THROTTLE_MIN
                and self.peak > self.RPM_MIN
                and self.since_peak >= self.HOLD_FRAMES):
            self.limiter = self.peak

        if self.limiter > 0.0:
            return self.limiter * 1.08, self.limiter
        # we don't know yet: big scale, no red
        return max(self.FLOOR, self.peak * 1.05), 0.0


class Source:
    """The shared contract. start() must not throw when the game isn't there -
    a source with no game should simply stay quiet."""

    name = "?"

    def __init__(self):
        self._last = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.status = "starting..."   # until its thread reports something

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


# ---------------------------------------------------------------------
# OutGauge (BeamNG.drive, Live for Speed, and any LFS-compatible sim)
# ---------------------------------------------------------------------
class OutGaugeSource(Source):
    """BeamNG: Options -> Others -> OutGauge -> on, IP 127.0.0.1, the port below.
    LFS and a few rally sims speak the same protocol.

    The packet is 92 bytes, or 96 if you set an OutGauge ID - that last field is
    optional, which is why we accept both lengths.
    """

    name = "OutGauge"

    # < = little-endian, no padding. See the OutGauge docs that ship with LFS.
    # The order: time, car[4], flags, gear, plid,
    #            speed, rpm, turbo, engTemp, fuel, oilPressure, oilTemp, <- 7 floats
    #            dashLights, showLights, throttle, brake, clutch,
    #            display1[16], display2[16]
    _FMT = "<I4sHBBfffffffIIfff16s16s"
    _SIZE = struct.calcsize(_FMT)          # 92

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

        # LFS numbers gears 0=reverse, 1=neutral, 2=first. We keep 0=neutral and
        # negative=reverse, so every game looks the same to the board.
        g = -1 if gear == 0 else (gear - 1)

        # showLights is LFS's mask of lit warning lights: bit 32 = left
        # indicator, 64 = right. (128 would be "either"; we don't use it, hazards
        # are recognised from both being lit at once.)
        blk = (1 if (show & 32) else 0) | (2 if (show & 64) else 0)

        return Telemetry(
            src="",                               # filled in by the caller
            kind="car",
            blinkers=blk,
            speed_kmh=speed * 3.6,                # OutGauge gives m/s
            rpm=rpm,
            gear=g,
            fuel_pct=fuel * 100.0,                # 0..1
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
            # 0.0.0.0, not 127.0.0.1: we also receive if the game sends to an
            # interface other than loopback. It costs nothing and removes a whole
            # category of "nothing is arriving".
            sock.bind(("0.0.0.0", self.port))
        except OSError as e:
            self.status = f"can't listen on {self.port}: {e}"
            return
        self.status = f"listening on UDP {self.port}"
        # OutGauge sends neither the rev range nor the redline - we learn them.
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


# ---------------------------------------------------------------------
# Euro Truck Simulator 2 / American Truck Simulator
# ---------------------------------------------------------------------
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

    # When the server doesn't answer we slow down: the app runs all the time, and
    # 10 failing attempts a second is a percent of CPU burnt for nothing while
    # you aren't playing. Once it answers we go back to full rate.
    IDLE_PERIOD = 3.0

    def __init__(self, url="http://localhost:25555/api/ets2/telemetry", hz=10):
        super().__init__()
        self.url = url
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

        # Trucks need no learning: the SDK gives the full scale directly. We put
        # the redline at 90% of it - on a truck the red zone really does start
        # well below the maximum, unlike a car.
        rpm_max = truck.get("engineRpmMax") or 0.0

        blk = ((1 if truck.get("blinkerLeftActive") else 0)
               | (2 if truck.get("blinkerRightActive") else 0))

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
        self.status = f"polling {self.url}"
        warned = False
        wait = self.IDLE_PERIOD
        while not self._stop.is_set():
            try:
                with urllib.request.urlopen(self.url, timeout=1.0) as r:
                    doc = json.loads(r.read())
                wait = self.period          # answering: full rate
                tel = self.decode(doc)
                if tel:
                    self._put(tel)
                    self.status = "connected"
                    warned = False
                else:
                    self.status = "server up, game not connected"
            except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
                wait = self.IDLE_PERIOD     # silent: back off
                if not warned:
                    self.status = "the telemetry server isn't answering"
                    warned = True
            self._stop.wait(wait)


# ---------------------------------------------------------------------
# Microsoft Flight Simulator
# ---------------------------------------------------------------------
class MsfsSource(Source):
    """Through SimConnect.  pip install SimConnect

    UNTESTED: I don't have the simulator installed to check the variable names
    live. The structure is the standard one; if a name has changed you'll see it
    in the status and fix it in the table below.
    """

    name = "MSFS"

    VARS = {
        "speed_kmh": ("AIRSPEED_INDICATED", 1.852),   # knots -> km/h
        "rpm": ("GENERAL_ENG_RPM:1", 1.0),
        "alt_m": ("PLANE_ALTITUDE", 0.3048),          # feet -> metres
        "fuel_pct": ("FUEL_TOTAL_QUANTITY_WEIGHT", None),
    }

    def __init__(self, hz=5):
        super().__init__()
        self.period = 1.0 / hz

    def _run(self):
        self.status = "looking for the simulator..."
        try:
            from SimConnect import SimConnect, AircraftRequests
        except ImportError:
            self.status = "package missing: pip install SimConnect"
            return
        # We retry slowly: if you start the simulator after the app, the source
        # has to catch on by itself. It used to give up for good on the first
        # attempt, leaving you without telemetry until you restarted everything.
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
                vs = aq.get("VERTICAL_SPEED") or 0.0          # ft/s
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

                # SimConnect gives angles in radians for some variables and in
                # degrees for others, depending on the version. Under 7 it can't
                # be a heading in degrees, so it's radians.
                hdg_deg = math.degrees(hdg) if hdg < 7.0 else hdg

                self._put(Telemetry(
                    src="MSFS",
                    kind="air",
                    speed_kmh=ias * 1.852,
                    kts=ias,
                    vspeed_fpm=vs * 60.0,                 # ft/s -> ft/min
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


# ---------------------------------------------------------------------
# Generic HTTP intake (Roblox through a tunnel, your own scripts, anything)
# ---------------------------------------------------------------------
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


# ---------------------------------------------------------------------
# A generator, so you can see the screen with no game at all
# ---------------------------------------------------------------------
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
                redline=2400,          # so the shift marker shows up too
                gear=max(1, min(12, int(abs(speed) / 10) + 1)),
                fuel_pct=50 + 40 * math.sin(t / 30.0),
                throttle=max(0.0, math.sin(t / 7.0)),
                brake=max(0.0, -math.sin(t / 7.0)),
                engine_c=85 + 5 * math.sin(t / 11.0),
                turbo_bar=max(0.0, 1.2 * math.sin(t / 7.0)),
                blinkers=[0, 1, 2, 3][int(t / 5) % 4],   # changes every 5 s
                text="test, no game",
            ))
            self._stop.wait(0.1)



# ---------------------------------------------------------------------
# CorsaConnect (its telemetry mirror)
# ---------------------------------------------------------------------
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

    # "CT" + version + gear, then 11 floats, flags, lights, two displays.
    # See TelemetryPacket::encode() in server/src/protocol.rs.
    _FMT = "<2sBb11fHI16s16s"
    _SIZE = struct.calcsize(_FMT)          # 86
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
            # We don't guess at an unknown format: better to stay quiet than to
            # show numbers read out of fields that have moved.
            return None

        # The same convention as OutGauge: 0 = reverse, 1 = neutral, 2 = first.
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



# ---------------------------------------------------------------------
# War Thunder
# ---------------------------------------------------------------------
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

    # Fields that only ever appear in a cockpit, and only ever in a hull.
    AIR_KEYS = ("aviahorizon_pitch", "aviahorizon_roll", "vario",
                "altitude_10k", "compass")
    GROUND_KEYS = ("gear_num", "crew_total", "driver_state", "stabilizer",
                   "driving_direction_mode")

    def __init__(self, base="http://localhost:8111", hz=10):
        super().__init__()
        self.base = base.rstrip("/")
        self.period = 1.0 / hz
        self.idle_period = 3.0

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def classify(ind, state=None):
        """'air', 'car', or None when no vehicle is in play."""
        if not isinstance(ind, dict) or not ind.get("valid"):
            return None
        if any(k in ind for k in WarThunderSource.AIR_KEYS):
            return "air"
        if any(k in ind for k in WarThunderSource.GROUND_KEYS):
            return "car"
        # Nothing decisive in the indicators: the flight model only answers
        # valid for an aircraft, so it breaks the tie.
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
        # War Thunder gives no redline, for either vehicle. We scale to the
        # highest revs seen, rounded up, and send no redline at all rather than
        # inventing one - the panel then draws the bar with no red mark.
        rpm_max = math.ceil(max(peak, 1000.0) / 500.0) * 500.0

        if kind == "car":
            gear = int(cls._f(ind, "gear"))
            # driving_direction_mode false means the box is in reverse; the gear
            # number itself stays positive, so the sign has to come from here.
            if ind.get("driving_direction_mode") is False and gear != 0:
                gear = -abs(gear)
            return Telemetry(
                src="WarThunder",
                kind="car",
                speed_kmh=abs(cls._f(ind, "speed")),
                rpm=rpm,
                rpm_max=rpm_max,
                gear=gear,
                fuel_pct=-1.0,               # not reported for ground vehicles
                engine_c=cls._f(ind, "water_temperature", "oil_temperature"),
                text=str(ind.get("type", ""))[:24],
            )

        fuel = cls._f(st, "Mfuel, kg")
        fuel0 = cls._f(st, "Mfuel0, kg")
        ias = cls._f(st, "IAS, km/h") or cls._f(ind, "speed")

        # The aircraft pages are laid out for aviation units, and War Thunder
        # answers in metric - so the conversions live here, not on the board.
        return Telemetry(
            src="WarThunder",
            kind="air",
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
            # No radios, transponder or autopilot exist in the game, so those
            # pages would be blank. We put the numbers a pilot actually watches
            # there instead.
            ap_text="G %.1f AoA %.0f" % (cls._f(st, "Ny"), cls._f(st, "AoA, deg")),
            text=str(ind.get("type", ""))[:24],
        )

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=1.0) as r:
            return json.loads(r.read())

    def _run(self):
        self.status = "polling " + self.base
        wait = self.idle_period
        peak = 0.0
        last_kind = None
        while not self._stop.is_set():
            try:
                ind = self._get("/indicators")
                kind = self.classify(ind)
                # /state is only worth a round trip in an aircraft.
                st = self._get("/state") if kind != "car" else None
                tel = self.decode(ind, st, peak)
                wait = self.period
                if tel is None:
                    self.status = "game running, no vehicle (hangar?)"
                else:
                    peak = max(peak, tel.rpm)
                    if tel.kind != last_kind:
                        last_kind = tel.kind
                        peak = tel.rpm          # new vehicle, new rev range
                        self.status = "%s: %s" % (
                            "aircraft" if tel.kind == "air" else "ground vehicle",
                            tel.text or "?")
                    self._put(tel)
            except (urllib.error.URLError, OSError, json.JSONDecodeError,
                    TimeoutError, ValueError):
                wait = self.idle_period      # not running: stop hammering it
                self.status = "not running"
                last_kind = None
            self._stop.wait(wait)


ALL = {
    "outgauge": OutGaugeSource,
    "warthunder": WarThunderSource,
    "corsa": CorsaSource,
    "ets2": Ets2HttpSource,
    "msfs": MsfsSource,
    "http": HttpIngestSource,
    "demo": DemoSource,
}
