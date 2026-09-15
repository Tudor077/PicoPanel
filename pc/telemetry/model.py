"""The shared telemetry model.

The board doesn't know which game the data came from. Every source translates
into the structure here, and the structure knows how to write itself as the line
the firmware expects.
"""

import re
import time
from dataclasses import dataclass, field

# The screen uses the GFX library's ASCII font: accented letters and anything
# above 0x7E come out as boxes. ';' and '=' are the protocol's separators, so
# they go too. We strip it all here, not on the board.
_UNSAFE = re.compile(r"[^A-Za-z0-9 .,_/+()\[\]:-]")

MAX_LINE = 200          # the firmware's buffer is 224; leave room.
                        # A full aircraft line goes past 170.
MAX_TEXT = 25           # gameTxt[26] on the board
MAX_SRC = 11            # gameSrc[12]


def _clean(s, limit):
    return _UNSAFE.sub("", str(s))[:limit].strip()


@dataclass
class Telemetry:
    """One normalised snapshot. Unknown fields keep their neutral values and
    simply aren't sent."""

    src: str = ""
    speed_kmh: float = 0.0
    rpm: float = 0.0
    rpm_max: float = 0.0        # the tacho's full-scale end
    redline: float = 0.0        # the limiter; 0 = not learned yet
    gear: int = 0               # 0 = neutral, negative = reverse
    fuel_pct: float = -1.0      # -1 = unknown
    throttle: float = 0.0       # 0..1
    brake: float = 0.0          # 0..1
    turbo_bar: float = 0.0
    engine_c: float = 0.0       # coolant temperature
    alt_m: float = 0.0
    text: str = ""

    # "car" or "air" - the board picks its page set from this, not from the
    # game's name. So a new flight sim needs no firmware change at all.
    kind: str = "car"

    # cars
    blinkers: int = 0           # bit0 = left, bit1 = right (3 = hazards)

    # aircraft
    kts: float = 0.0            # indicated airspeed, knots
    vspeed_fpm: float = 0.0     # vertical speed, feet/minute
    alt_ft: float = 0.0
    hdg: float = 0.0
    com1: str = ""
    com2: str = ""
    squawk: str = ""
    ap_text: str = ""           # autopilot modes, already formatted

    stamp: float = field(default_factory=time.time)

    def age(self):
        return time.time() - self.stamp

    def to_line(self):
        """The line the firmware eats, without the line ending."""
        parts = [f"src={_clean(self.src, MAX_SRC)}"]
        parts.append(f"spd={int(round(self.speed_kmh))}")
        parts.append(f"rpm={int(round(self.rpm))}")
        if self.rpm_max > 0:
            parts.append(f"rpmmax={int(round(self.rpm_max))}")
        if self.redline > 0:
            parts.append(f"rl={int(round(self.redline))}")
        parts.append(f"gear={int(self.gear)}")
        if self.fuel_pct >= 0:
            parts.append(f"fuel={int(round(self.fuel_pct))}")
        if self.throttle or self.brake:
            parts.append(f"thr={int(round(self.throttle * 100))}")
            parts.append(f"brk={int(round(self.brake * 100))}")
        if self.engine_c:
            parts.append(f"tmp={int(round(self.engine_c))}")
        if self.alt_m:
            parts.append(f"alt={int(round(self.alt_m))}")

        parts.append(f"knd={self.kind}")
        if self.kind == "car":
            parts.append(f"blk={int(self.blinkers)}")
            if self.turbo_bar:
                # x10: no floating point on the board for a number we display
                parts.append(f"tur={int(round(self.turbo_bar * 10))}")
        else:
            parts.append(f"kts={int(round(self.kts))}")
            parts.append(f"vs={int(round(self.vspeed_fpm))}")
            parts.append(f"aft={int(round(self.alt_ft))}")
            if self.hdg:
                parts.append(f"hdg={int(round(self.hdg))}")
            if self.com1:
                parts.append(f"c1={_clean(self.com1, 8)}")
            if self.com2:
                parts.append(f"c2={_clean(self.com2, 8)}")
            if self.squawk:
                parts.append(f"sqk={_clean(self.squawk, 5)}")
            if self.ap_text:
                parts.append(f"ap={_clean(self.ap_text, 16)}")
        if self.text:
            parts.append(f"txt={_clean(self.text, MAX_TEXT)}")

        line = "$" + ";".join(parts)
        if len(line) > MAX_LINE:
            # drop the free text first, then the rest if it still doesn't fit.
            # Better a shortened line than one the board cuts mid-field and
            # misreads.
            parts = [p for p in parts if not p.startswith("txt=")]
            line = "$" + ";".join(parts)
            line = line[:MAX_LINE]
        return line

    def summary(self):
        g = "R" if self.gear < 0 else ("N" if self.gear == 0 else str(self.gear))
        s = f"{self.src:<10} {self.speed_kmh:6.1f} km/h  {self.rpm:5.0f} rpm  gear {g}"
        if self.fuel_pct >= 0:
            s += f"  fuel {self.fuel_pct:3.0f}%"
        if self.alt_m:
            s += f"  alt {self.alt_m:.0f} m"
        return s
