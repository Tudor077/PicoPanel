"""The shared telemetry model.

The board doesn't know which game the data came from. Every source translates
into the structure here, and the structure knows how to write itself as the line
the firmware expects.
"""

import time
from dataclasses import dataclass, field

from .text import clean as _text_clean

MAX_LINE = 200
MAX_TEXT = 25
MAX_SRC = 11


def _clean(s, limit):
    return _text_clean(s, limit)


@dataclass
class Telemetry:
    """One normalised snapshot. Unknown fields keep their neutral values and
    simply aren't sent."""

    src: str = ""
    speed_kmh: float = 0.0
    rpm: float = 0.0
    rpm_max: float = 0.0
    redline: float = 0.0
    gear: int = 0
    fuel_pct: float = -1.0
    throttle: float = 0.0
    brake: float = 0.0
    turbo_bar: float = 0.0
    engine_c: float = 0.0
    alt_m: float = 0.0
    text: str = ""

    kind: str = "car"

    blinkers: int = 0

    kts: float = 0.0
    vspeed_fpm: float = 0.0
    alt_ft: float = 0.0
    hdg: float = 0.0
    com1: str = ""
    com2: str = ""
    squawk: str = ""
    ap_text: str = ""

    gforce: float = 0.0
    aoa: float = 0.0
    crew: str = ""

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
        if self.kind == "wtgnd":
            if self.crew:
                parts.append(f"crew={_clean(self.crew, 7)}")
        elif self.kind == "wtair":
            parts.append(f"kts={int(round(self.kts))}")
            parts.append(f"vs={int(round(self.vspeed_fpm))}")
            parts.append(f"aft={int(round(self.alt_ft))}")
            if self.hdg:
                parts.append(f"hdg={int(round(self.hdg))}")
            parts.append(f"g={int(round(self.gforce * 10))}")
            parts.append(f"aoa={int(round(self.aoa))}")
        elif self.kind == "car":
            parts.append(f"blk={int(self.blinkers)}")
            if self.turbo_bar:
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
