#!/usr/bin/env python3
"""
fakepanel.py - a PicoPanel with no PicoPanel.

Emits exactly the byte stream the real board emits, so panel.py (and the
Android app) can be used, developed and demoed with nothing plugged in.

Two ways to use it:

    python fakepanel.py           watch the stream the board would send
    python fakepanel.py --art     ...and draw the mirrored screen as text

    # inside panel.py: pick the EMULATOR entry in the port list.
    # FakeSerial duck-types pyserial, so Link._reader() cannot tell.

What it reproduces, from serialReport() and mirrorService() in PicoPanel.ino:

    SW2=2 SW3=4 ENC=7 TOT=112 BTN=0000000 ERR=0 PCF=00100000 PG=0 GM=- GL=0 \
HID=1 OLED=ok FPS=30 RND=14800us I2C=400k P=16ms
    !FB 128 32 <base64 of a 512-byte SSD1306 page buffer>

BTN bits are UP DWN LFT RHT MID SET ENC_SW, in that order, because that is the
order of `enum { B_UP = 0, B_DWN, ... }`. PCF bits are A1..A4 then B1..B4.

The panel it simulates runs a little demo of itself: the encoder turns, buttons
blink, the switches move every few seconds. Enough that every lamp in the app
lights up at some point without anyone touching hardware.
"""

import base64
import math
import random
import sys
import time

SCREEN_W = 128
SCREEN_H = 32
REPORT_HZ = 5.0          # the board reports ~5x/second
MIRROR_HZ = 10.0         # and mirrors at ~10 fps while 'o' is on
SLEEP_DIM_S = 10.0       # screen dims after 10 s idle, dark after 20 s
SLEEP_OFF_S = 20.0

# 5x7 glyphs, one byte per column, bit 0 = top row. Same shapes the Adafruit
# GFX default font uses, so the emulated screen reads like the real one.
FONT = {
    " ": (0x00, 0x00, 0x00, 0x00, 0x00), "!": (0x00, 0x00, 0x5F, 0x00, 0x00),
    "%": (0x23, 0x13, 0x08, 0x64, 0x62), "*": (0x14, 0x08, 0x3E, 0x08, 0x14),
    "+": (0x08, 0x08, 0x3E, 0x08, 0x08), "-": (0x08, 0x08, 0x08, 0x08, 0x08),
    ".": (0x00, 0x60, 0x60, 0x00, 0x00), "/": (0x20, 0x10, 0x08, 0x04, 0x02),
    "0": (0x3E, 0x51, 0x49, 0x45, 0x3E), "1": (0x00, 0x42, 0x7F, 0x40, 0x00),
    "2": (0x42, 0x61, 0x51, 0x49, 0x46), "3": (0x21, 0x41, 0x45, 0x4B, 0x31),
    "4": (0x18, 0x14, 0x12, 0x7F, 0x10), "5": (0x27, 0x45, 0x45, 0x45, 0x39),
    "6": (0x3C, 0x4A, 0x49, 0x49, 0x30), "7": (0x01, 0x71, 0x09, 0x05, 0x03),
    "8": (0x36, 0x49, 0x49, 0x49, 0x36), "9": (0x06, 0x49, 0x49, 0x29, 0x1E),
    ":": (0x00, 0x36, 0x36, 0x00, 0x00), "?": (0x02, 0x01, 0x51, 0x09, 0x06),
    "A": (0x7E, 0x11, 0x11, 0x11, 0x7E), "B": (0x7F, 0x49, 0x49, 0x49, 0x36),
    "C": (0x3E, 0x41, 0x41, 0x41, 0x22), "D": (0x7F, 0x41, 0x41, 0x22, 0x1C),
    "E": (0x7F, 0x49, 0x49, 0x49, 0x41), "F": (0x7F, 0x09, 0x09, 0x01, 0x01),
    "G": (0x3E, 0x41, 0x49, 0x49, 0x7A), "H": (0x7F, 0x08, 0x08, 0x08, 0x7F),
    "I": (0x00, 0x41, 0x7F, 0x41, 0x00), "J": (0x20, 0x40, 0x41, 0x3F, 0x01),
    "K": (0x7F, 0x08, 0x14, 0x22, 0x41), "L": (0x7F, 0x40, 0x40, 0x40, 0x40),
    "M": (0x7F, 0x02, 0x04, 0x02, 0x7F), "N": (0x7F, 0x04, 0x08, 0x10, 0x7F),
    "O": (0x3E, 0x41, 0x41, 0x41, 0x3E), "P": (0x7F, 0x09, 0x09, 0x09, 0x06),
    "Q": (0x3E, 0x41, 0x51, 0x21, 0x5E), "R": (0x7F, 0x09, 0x19, 0x29, 0x46),
    "S": (0x46, 0x49, 0x49, 0x49, 0x31), "T": (0x01, 0x01, 0x7F, 0x01, 0x01),
    "U": (0x3F, 0x40, 0x40, 0x40, 0x3F), "V": (0x1F, 0x20, 0x40, 0x20, 0x1F),
    "W": (0x7F, 0x20, 0x18, 0x20, 0x7F), "X": (0x63, 0x14, 0x08, 0x14, 0x63),
    "Y": (0x03, 0x04, 0x78, 0x04, 0x03), "Z": (0x61, 0x51, 0x49, 0x45, 0x43),
}

PAGES = ["PANEL", "GAME", "HID", "SWITCHES", "ENCODER",
         "BUTTONS", "PCF8574", "I2C", "INFO"]

HELP = [
    "i b k c x  I2C: rescan, bus test, recovery, speed, re-init",
    "d v h 1 2 0  display: visual test, raw cmd, height, address",
    "a e t l g n  encoder: calibrate, mode, trace, log, jump, status",
    "p m r s f  pins, switch matrix, reset, reporting, render period",
    "u j y w  arm HID, mapping, media layer, double-press window",
    "o  mirror the screen to the PC app",
]


class Screen:
    """A 128x32 1-bit framebuffer in SSD1306 page order."""

    def __init__(self, w=SCREEN_W, h=SCREEN_H):
        self.w, self.h = w, h
        self.buf = bytearray(w * h // 8)

    def clear(self):
        for i in range(len(self.buf)):
            self.buf[i] = 0

    def pixel(self, x, y, on=True):
        if not (0 <= x < self.w and 0 <= y < self.h):
            return
        i = x + (y >> 3) * self.w
        bit = 1 << (y & 7)
        if on:
            self.buf[i] |= bit
        else:
            self.buf[i] &= ~bit & 0xFF

    def text(self, x, y, s):
        for ch in str(s).upper():
            glyph = FONT.get(ch)
            if glyph is not None:
                for col in range(5):
                    bits = glyph[col]
                    for row in range(7):
                        if bits & (1 << row):
                            self.pixel(x + col, y + row)
            x += 6
            if x >= self.w:
                break

    def rect(self, x, y, w, h, fill=False):
        for dx in range(w):
            for dy in range(h):
                edge = dx in (0, w - 1) or dy in (0, h - 1)
                if fill or edge:
                    self.pixel(x + dx, y + dy)

    def to_art(self):
        rows = []
        for y in range(self.h):
            base, bit = (y >> 3) * self.w, 1 << (y & 7)
            rows.append("".join("#" if self.buf[base + x] & bit else "."
                                for x in range(self.w)))
        return "\n".join(rows)


class FakePanel:
    """The board's behaviour: state, the demo that moves it, and the output."""

    def __init__(self, seed=None, quiet_demo=False):
        self.rng = random.Random(seed)
        self.t0 = time.monotonic()
        self.quiet_demo = quiet_demo      # True = inputs only move when poked

        self.sw1 = 2                      # firmware calls it SW2: 3 positions
        self.sw2 = 3                      # firmware calls it SW3: 5 positions
        self.enc = 0
        self.total = 0
        self.errors = 0
        self.btn = [False] * 7            # UP DWN LFT RHT MID SET ENC_SW
        self.pcf = [False] * 8            # A1..A4 B1..B4
        self.page = 0
        self.hid = True
        self.mirror = False
        self.game_src = "-"
        self.game_lines = 0
        self.last_input = self.t0

        self.screen = Screen()
        self.out = bytearray()
        self._next_report = 0.0
        self._next_frame = 0.0
        self._next_demo = 0.0
        self._blank_sent = False
        self._emit(b"PicoPanel ready.  '?' for the command list.\r\n")
        self._emit(b"[emulator] no hardware attached - this panel is simulated\r\n")

    # ---------------------------------------------------------------- output
    def _emit(self, data):
        self.out += data if isinstance(data, bytes) else data.encode("ascii", "ignore")

    def _line(self, text):
        self._emit(text + "\r\n")

    def poke(self):
        self.last_input = time.monotonic()

    # ------------------------------------------------------------ the report
    def report_line(self):
        btn = "".join("1" if b else "0" for b in self.btn)
        pcf = "".join("1" if b else "0" for b in self.pcf)
        up = time.monotonic() - self.t0
        return (
            f"SW2={self.sw1} SW3={self.sw2} ENC={self.enc} TOT={self.total}"
            f" BTN={btn} ERR={self.errors} PCF={pcf}"
            f" PG={self.page} GM={self.game_src} GL={self.game_lines}"
            f" HID={'1' if self.hid else '0'} OLED=ok"
            f" FPS={60 if up % 20 > 1 else 59} RND=14800us I2C=400k P=16ms"
        )

    # ------------------------------------------------------------- the screen
    def sleep_state(self):
        idle = time.monotonic() - self.last_input
        if self.game_src != "-":
            return 0                       # never sleeps while a game feeds it
        if idle > SLEEP_OFF_S:
            return 2
        if idle > SLEEP_DIM_S:
            return 1
        return 0

    def draw(self):
        s = self.screen
        s.clear()
        name = PAGES[self.page]
        if self.page == 0:
            s.text(0, 0, f"{name}   {'HID' if self.hid else 'hid'}")
            s.text(0, 8, f"SW1:{self.sw1}  SW2:{self.sw2}")
            s.text(0, 16, f"ENC:{self.enc:+d} T:{self.total}")
            lamps = "".join("*" if b else "." for b in self.pcf)
            s.text(0, 24, f"AB:{lamps}")
        elif self.page == 1:
            s.text(0, 0, f"GAME {self.game_src}")
            phase = (time.monotonic() - self.t0) * 1.7
            speed = int(60 + 55 * math.sin(phase))
            s.text(0, 10, f"{speed} KMH  G{2 + (speed // 40)}")
            width = max(1, int(126 * (0.5 + 0.5 * math.sin(phase * 1.3))))
            s.rect(0, 24, 128, 7)
            s.rect(1, 25, width - 1, 5, fill=True)
        else:
            s.text(0, 0, name)
            s.text(0, 12, f"PAGE {self.page} OF {len(PAGES) - 1}")
            s.text(0, 22, "EMULATED")
        return s

    def frame_line(self):
        state = self.sleep_state()
        if state == 2:
            # The board sends ONE blank frame when it sleeps, then goes quiet -
            # matching "Send one blank frame when the panel sleeps, not twenty
            # a second".
            if self._blank_sent:
                return None
            self._blank_sent = True
            blank = bytes(SCREEN_W * SCREEN_H // 8)
            return f"!FB {SCREEN_W} {SCREEN_H} {base64.b64encode(blank).decode()}"
        self._blank_sent = False
        s = self.draw()
        return (f"!FB {s.w} {s.h} "
                f"{base64.b64encode(bytes(s.buf)).decode()}")

    # --------------------------------------------------------------- the demo
    def _demo_step(self, now):
        """Move the inputs so every lamp in the app lights up eventually."""
        if self.quiet_demo:
            return
        self._next_demo = now + 0.35
        self.enc = max(-99, min(99, self.enc + self.rng.choice((0, 0, 1, 1, -1))))
        self.total += 1
        roll = self.rng.random()
        if roll < 0.22:
            i = self.rng.randrange(8)
            self.pcf[i] = not self.pcf[i]
            self.poke()
        elif roll < 0.34:
            i = self.rng.randrange(7)
            self.btn[i] = not self.btn[i]
            self.poke()
        elif roll < 0.40:
            self.sw1 = self.rng.randint(1, 3)
            self.poke()
        elif roll < 0.46:
            self.sw2 = self.rng.randint(1, 5)
            self.poke()

    # ------------------------------------------------------------- the inputs
    def command(self, text):
        """One line from the PC: a command letter, or a '$' telemetry line."""
        text = text.strip()
        if not text:
            return
        if text.startswith("$"):
            self.game_lines += 1
            for tok in text[1:].split(";"):
                if tok.startswith("src="):
                    self.game_src = tok[4:] or "-"
            return
        c = text[0]
        self.poke()
        if c in "oO":
            self.mirror = not self.mirror
            self._line(f"screen mirror {'ON' if self.mirror else 'OFF'}")
        elif c in "uU":
            self.hid = not self.hid
            self._line(f"HID {'armed' if self.hid else 'disarmed'}")
        elif c == "?":
            for row in HELP:
                self._line(row)
        elif c in "pP":
            self._line(f"pins: SW1={self.sw1} SW2={self.sw2} "
                       f"ENC={self.enc} TOT={self.total} ERR={self.errors}")
        elif c in "nN":
            self._line("encoder: 20 detents/turn, filtered, mode=edge (emulated)")
        elif c in "iI":
            self._line("I2C scan: 0x3C SSD1306, 0x20 PCF8574   (emulated)")
        elif c in "rR":
            self.total = 0
            self.errors = 0
            self._line("counters reset")
        elif c in "sS":
            self._line("reporting toggled (the emulator always reports)")
        else:
            self._line(f"'{c}' acknowledged (emulated, no hardware to act on)")

    # ----------------------------------------------------------------- ticking
    def service(self, now=None):
        """Produce whatever the board would have sent by now."""
        now = time.monotonic() if now is None else now
        if now >= self._next_demo:
            self._demo_step(now)
        if now >= self._next_report:
            self._next_report = now + 1.0 / REPORT_HZ
            self._line(self.report_line())
        if self.mirror and now >= self._next_frame:
            self._next_frame = now + 1.0 / MIRROR_HZ
            line = self.frame_line()
            if line:
                self._line(line)


class FakeSerial:
    """A pyserial-shaped object wrapping FakePanel.

    panel.py's Link only ever calls is_open / read / write / close, so this is
    all it takes to make the app talk to a panel that isn't there.
    """

    def __init__(self, port=None, baudrate=115200, timeout=0.2, **kw):
        self.port = port or "EMULATOR"
        self.baudrate = baudrate
        self.timeout = timeout if timeout is not None else 0.2
        self.is_open = True
        self.panel = FakePanel()
        self._rx = bytearray()

    def read(self, size=1):
        deadline = time.monotonic() + self.timeout
        while True:
            self.panel.service()
            if self.panel.out:
                take = bytes(self.panel.out[:size])
                del self.panel.out[:size]
                return take
            if time.monotonic() >= deadline:
                return b""
            time.sleep(0.01)

    def write(self, data):
        self._rx += data
        while b"\n" in self._rx:
            raw, _, rest = self._rx.partition(b"\n")
            self._rx = bytearray(rest)
            self.panel.command(raw.decode("ascii", "ignore"))
        return len(data)

    @property
    def in_waiting(self):
        return len(self.panel.out)

    def close(self):
        self.is_open = False

    def __repr__(self):
        return f"<FakeSerial {self.port} (emulated PicoPanel)>"


def main(argv):
    art = "--art" in argv
    seconds = 6.0
    panel = FakePanel()
    panel.command("o")                       # mirror on, so frames flow
    end = time.monotonic() + seconds
    shown = 0
    while time.monotonic() < end:
        panel.service()
        while b"\n" in panel.out:
            raw, _, rest = bytes(panel.out).partition(b"\n")
            panel.out = bytearray(rest)
            line = raw.decode("ascii", "ignore").rstrip("\r")
            if line.startswith("!FB "):
                if art and shown < 2:
                    shown += 1
                    _, w, h, b64 = line.split(" ", 3)
                    print(f"--- frame {w}x{h} ---")
                    print(panel.screen.to_art())
                else:
                    print(line[:72] + f"... ({len(line)} chars)")
            else:
                print(line)
        time.sleep(0.02)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
