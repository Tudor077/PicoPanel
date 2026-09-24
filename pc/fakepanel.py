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
USR_HOLD_MS = 1000       # USER held this long = arm / disarm HID
USR_DOUBLE_MS = 250      # window for the second press ('w' changes it on the board)

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

# The PG= number is an index into this, and it has to match the firmware's own
# enum exactly - the app looks pages up by number. MUSIC and the eight pages
# you draw yourself arrived after this file was first written, which is why it
# had nine of them and the app could not make sense of anything it said.
PAGE_NAME = ["PANEL", "GAME", "MUSIC",
             "MINE 1", "MINE 2", "MINE 3", "MINE 4",
             "MINE 5", "MINE 6", "MINE 7", "MINE 8",
             "HID", "SWITCHES", "ENCODER", "BUTTONS", "PCF8574", "I2C", "INFO"]
PAGES = PAGE_NAME                      # kept for anything reading the old name
(P_OVERVIEW, P_GAME, P_MUSIC, P_CUSTOM1, _C2, _C3, _C4, _C5, _C6, _C7, _C8,
 P_HID, P_SW, P_ENC, P_BTN, P_PCF, P_I2C, P_INFO) = range(len(PAGE_NAME))
CUS_FIRST, CUS_SLOTS, CUS_FACES = P_CUSTOM1, 8, 4
CUS_BYTES = SCREEN_W * SCREEN_H // 8
MUSIC_SUBS = 2


def cus_slot(page):
    """Which of your own pages that number is, or None."""
    i = page - CUS_FIRST
    return i if 0 <= i < CUS_SLOTS else None

# btn[].name in the firmware - two characters, so they fit the 17px boxes.
BTN_NAMES = ["UP", "DN", "LF", "RT", "MD", "ST", "EN"]

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

    def text(self, x, y, s, color=1, size=1):
        """Adafruit_GFX's classic 5x7 font: 6px advance, y is the glyph top."""
        for ch in str(s).upper():
            glyph = FONT.get(ch)
            if glyph is not None:
                for col in range(5):
                    bits = glyph[col]
                    for row in range(7):
                        if bits & (1 << row):
                            for sx in range(size):
                                for sy in range(size):
                                    self.pixel(x + col * size + sx,
                                               y + row * size + sy, color)
            x += 6 * size
            if x >= self.w:
                break

    def rect(self, x, y, w, h, fill=False, color=1):
        for dx in range(w):
            for dy in range(h):
                edge = dx in (0, w - 1) or dy in (0, h - 1)
                if fill or edge:
                    self.pixel(x + dx, y + dy, color)

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
        self._last_dir = 0
        self.total = 0
        self.errors = 0
        self.btn = [False] * 7            # UP DWN LFT RHT MID SET ENC_SW
        self.pcf = [False] * 8            # A1..A4 B1..B4
        self.page = 0
        self.media = False       # layer two, latched by double-tapping USER
        self.game_sub = 0
        self.music_sub = 0
        # The rotation, and where we are in it. The app sends this with "%pg"
        # on every connect; until then it is every page, the way the board
        # comes up before anyone has told it otherwise.
        self.page_list = list(range(len(PAGE_NAME)))
        self.page_slot = 0
        # Your own pages: the finished picture for each face, how many faces
        # each has, and which one is showing. The app sends the pictures with
        # "%cv=<slot>,<face>,<base64>" - the same 512 bytes it sends the board.
        self.cus = [[None] * CUS_FACES for _ in range(CUS_SLOTS)]
        self.cus_subs = [1] * CUS_SLOTS
        self.cus_sub = [0] * CUS_SLOTS
        self.hid_lock = 0        # pages that hold USER while HID is armed
        self.aod = 0             # pages that keep the screen lit
        self.hdr_style = 0
        self._said_page = None   # what the last !PAGE said, so it is said once
        self._usr_down = False
        self._usr_press_at = 0.0
        self._usr_stage1 = False
        self._usr_last_short = 0.0
        # What the panel looked like before the last short press - see user_up.
        self._before = (0, 0, 0, 0, [0] * CUS_SLOTS)
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
            f" PG={self.page}.{self.game_sub}"
            f" ORD={','.join(str(i) for i in self.page_list)}"
            f" HL={self._hl_text()}"
            f" M={'1' if self.media else '0'}"
            f" KNOB={'vol' if (self.media or self.page == P_MUSIC) else 'pad'}"
            f" GM={self.game_src} GL={self.game_lines}"
            f" NP=- AU=-"
            f" HID={'1' if self.hid else '0'} OLED=ok SLP={self.sleep_state()}"
            f" FPS={60 if up % 20 > 1 else 59} RND=14800us I2C=400k P=16ms"
        )

    def _hl_text(self):
        held = [str(i) for i in range(len(PAGE_NAME)) if self.hid_lock >> i & 1]
        return ",".join(held) if held else "-"

    # ---------------------------------------------------------- which face
    def page_face(self):
        """(which face is showing, how many this page has) - what !PAGE says."""
        sl = cus_slot(self.page)
        if self.page == P_GAME:
            return self.game_sub, (3 if self.game_src != "-" else 1)
        if self.page == P_MUSIC:
            return self.music_sub, MUSIC_SUBS
        if sl is not None:
            return self.cus_sub[sl], self.cus_subs[sl]
        return 0, 1

    def announce_page(self):
        """The board says where it is on every change, and the app needs it:
        it is how it knows which of your pages to send a picture for. Said on
        change rather than in the report, because the report can be switched
        off and this has to work regardless."""
        cur, tot = self.page_face()
        shift = 9 if self.sleep_state() else 0
        now = (self.page, cur, tot, shift)
        if now != self._said_page:
            self._said_page = now
            self._line("!PAGE %d %d %d %d" % now)

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
        """The panel's own pages, as PicoPanel.ino draws them.

        Transcribed from render(): header() then the per-page draw function.
        Coordinates, box sizes and labels are the firmware's, not invented -
        what you see here is what the 128x32 actually shows.
        """
        s = self.screen
        s.clear()
        sl = cus_slot(self.page)
        if sl is not None:
            # A page of yours arrives as a finished picture and is copied
            # straight into the screen, header and all - the same memcpy the
            # firmware does. Nothing else is drawn over it.
            frame = self.cus[sl][self.cus_sub[sl]]
            if frame:
                s.buf[:] = bytearray(frame[:len(s.buf)])
            else:
                s.text(0, 13, "no layout yet")
                s.text(0, 23, "build one in the app")
            return s
        self._header()
        if self.page == P_MUSIC:
            self._draw_music()
        elif self.page == P_OVERVIEW:
            self._draw_overview()
        elif self.page == P_GAME:
            self._draw_game()
        elif self.page == P_SW:
            self._draw_sw_page()
        elif self.page == P_ENC:
            self._draw_enc()
        elif self.page == P_BTN:
            self._draw_btn_page()
        elif self.page == P_PCF:
            self._draw_pcf()
        elif self.page == P_HID:
            self._draw_hid()
        else:
            # HID, I2C and INFO read live hardware the emulator has no model
            # of; drawing a guess would be worse than saying so.
            # I2C and INFO read live bus state there is no model for.
            s.text(0, 13, "not emulated")
            s.text(0, 23, PAGE_NAME[self.page] + " needs hw")
        return s

    def _draw_music(self):
        """What is playing, or the fact that nothing is. The real page is built
        from what the PC sends about the track; the emulator has no media
        session behind it, so it says so rather than inventing a song."""
        s = self.screen
        if self.music_sub == 1:
            s.text(0, 13, "karaoke is off")
            s.text(0, 23, "turn it on in the app")
        else:
            s.text(0, 13, "nothing playing")
            s.text(0, 23, "(emulated)")

    def _header(self):
        """A filled white bar with black text - inverted, unlike every other page."""
        s = self.screen
        s.rect(0, 0, SCREEN_W, 9, fill=True)
        s.text(2, 1, PAGE_NAME[self.page], color=0)
        if self.hid:
            mark = "LOCK " if self.hid_lock >> self.page & 1 else "HID "
        else:
            mark = ""
        mark += "M " if self.media else ""
        cur, tot = self.slot_of(self.page) + 1, len(self.page_list)
        face, faces = self.page_face()
        if faces > 1:
            # On a page with faces the header counts those, not the rotation:
            # while you are walking them, which one you are on is the thing you
            # cannot otherwise tell.
            cur, tot = face + 1, faces
        buf = "{}{}/{}".format(mark, cur, tot)
        s.text(SCREEN_W - 2 - 6 * len(buf), 1, buf, color=0)

    def _btn_row(self, y):
        """Seven 17x10 boxes, filled when pressed - drawBtnRow()."""
        s = self.screen
        w, h = 17, 10
        for i, name in enumerate(BTN_NAMES):
            x = i * (w + 1)
            pressed = self.btn[i]
            s.rect(x, y, w, h, fill=pressed)
            s.text(x + 3, y + 2, name, color=0 if pressed else 1)

    def _draw_overview(self):
        s = self.screen
        s.text(0, 11, "S2:{} S3:{} E:{}".format(
            self.sw1 or "?", self.sw2 or "?", self.enc))
        self._btn_row(21)

    def _draw_sw_page(self):
        # drawSwRow(): "<name> <pos>/<expect>  c<common>  v<seen>"
        s = self.screen
        s.text(0, 11, "SW2 {}/3  c1  v3".format(self.sw1 or "?"))
        s.text(0, 21, "SW3 {}/5  c1  v5".format(self.sw2 or "?"))

    def _draw_enc(self):
        s = self.screen
        s.text(2, 13, str(self.enc), size=2)
        direction = "CW" if self._last_dir > 0 else ("CCW" if self._last_dir < 0 else "--")
        s.text(60, 12, "A1 B0 {}".format(direction))
        s.text(60, 22, "T{} E{}".format(self.total, self.errors))

    def _draw_btn_page(self):
        s = self.screen
        self._btn_row(11)
        s.text(0, 24, " ".join("0" for _ in BTN_NAMES))

    def _draw_pcf(self):
        # Eight 15x10 boxes numbered 1..8 - drawPcf().
        s = self.screen
        for i in range(8):
            x = i * 16
            on = self.pcf[i]
            s.rect(x, 11, 15, 10, fill=on)
            s.text(x + 5, 13, str(i + 1), color=0 if on else 1)
        s.text(0, 23, "0x20 raw 0x{:02X}".format(
            sum(0 if v else 1 << i for i, v in enumerate(self.pcf))))

    def _draw_hid(self):
        s = self.screen
        s.text(0, 11, "{}{}  SW1={} SW2={}".format(
            "ARMED" if self.hid else "off",
            " MEDIA" if self.media else "",
            self.sw1 or "?", self.sw2 or "?"))

    def _draw_game(self):
        s = self.screen
        if self.game_src == "-":
            s.text(0, 13, "no game")
            s.text(0, 23, "waiting for serial")
            return
        phase = (time.monotonic() - self.t0) * 1.7
        speed = int(60 + 55 * math.sin(phase))
        s.text(2, 12, str(speed), size=2)
        s.text(56, 12, "KMH")
        gear = 2 + speed // 40
        s.text(104, 12, "G{}".format(gear))
        width = max(2, int(126 * (0.5 + 0.5 * math.sin(phase * 1.3))))
        s.rect(0, 24, 128, 7)
        s.rect(1, 25, width - 1, 5, fill=True)

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
        step = self.rng.choice((0, 0, 1, 1, -1))
        if step:
            self._last_dir = step
        self.enc = max(-99, min(99, self.enc + step))
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

    # -------------------------------------------------------- the USER button
    def user_down(self):
        """Press. Nothing happens yet: the page changes on RELEASE, because the
        button also has a long press and the page would otherwise jump every
        time you armed HID."""
        self._usr_down = True
        self._usr_press_at = time.monotonic()
        self._usr_stage1 = False
        self.poke()

    def user_service(self):
        """The long press fires while the button is still held, not on release."""
        if not self._usr_down or self._usr_stage1:
            return
        if (time.monotonic() - self._usr_press_at) * 1000 >= USR_HOLD_MS:
            self._usr_stage1 = True
            self.hid = not self.hid
            self._line("HID {}".format("armed" if self.hid else "disarmed"))

    def user_up(self):
        self._usr_down = False
        self.poke()
        if self._usr_stage1:
            return                      # the hold was the action
        now = time.monotonic()
        gap = int((now - self._usr_last_short) * 1000) if self._usr_last_short else 0
        if self._usr_last_short and gap < USR_DOUBLE_MS:
            # Second press: put back whatever the first one moved, and toggle.
            # It used to step a page back on the assumption that the first
            # press had stepped one forward - but on a sub-page it had walked
            # a face instead, so two quick presses left you a page BACK from
            # where you started. The board had the same fault and lost it the
            # same way: remember, then restore.
            (self.page, self.page_slot, self.game_sub, self.music_sub,
             self.cus_sub) = self._before
            self._usr_last_short = 0.0
            self.media = not self.media
            self._line("[usr] double press at {} ms -> layer = {}".format(
                gap, "MEDIA" if self.media else "gamepad"))
            return
        if gap:
            self._line("[usr] single press, {} ms after the previous one "
                       "(window {})".format(gap, USR_DOUBLE_MS))
        self._usr_last_short = now
        # Noted BEFORE the press does anything, because it is what the next one
        # puts back if it comes quickly enough to be half of a double.
        self._before = (self.page, self.page_slot, self.game_sub,
                        self.music_sub, list(self.cus_sub))
        self.short_press()

    # ------------------------------------------------------------- the inputs
    # ------------------------------------------------------------ the walk
    def slot_of(self, page):
        """Where a page sits in the rotation, or 0 if it has been left out."""
        try:
            return self.page_list.index(page)
        except ValueError:
            return 0

    def goto(self, page):
        if 0 <= page < len(PAGE_NAME):
            self.page = page
            self.page_slot = self.slot_of(page)

    def step(self, by=1):
        if not self.page_list:
            return
        self.page_slot = (self.page_slot + by) % len(self.page_list)
        self.page = self.page_list[self.page_slot]

    def short_press(self):
        """What USER does: walk the faces of this page, then leave it.

        The order is the firmware's - GAME's own sub-pages while HID is armed,
        then MUSIC's two, then the faces of a page of yours - and a page that
        holds HID does not let go until you disarm.
        """
        self.poke()
        if self.hid and self.game_src != "-":
            self.goto(P_GAME)
            self.game_sub = (self.game_sub + 1) % 3
            return
        self.game_sub = 0
        if self.page == P_MUSIC and self.music_sub + 1 < MUSIC_SUBS:
            self.music_sub += 1
            return
        self.music_sub = 0
        sl = cus_slot(self.page)
        if sl is not None:
            if self.cus_sub[sl] + 1 < self.cus_subs[sl]:
                self.cus_sub[sl] += 1
                return
            self.cus_sub[sl] = 0
        if self.hid and self.hid_lock >> self.page & 1:
            self._line("[usr] this page holds HID - disarm to move on")
            return
        self.step(1)

    # --------------------------------------------------------- the % commands
    def percent(self, text):
        """The PC's settings line: "%pg=1,2;%rs=0", and the rest.

        Tokens with no "=" carry nothing and are answered first - the same trap
        the firmware has, where a handler keyed on "key=value" never sees them.
        """
        for tok in text.split(";"):
            tok = tok.strip().lstrip("%")
            if not tok:
                continue
            if "=" not in tok:
                if tok == "wk":
                    self.poke()
                elif tok == "up":
                    self.short_press()
                elif tok == "sv":
                    self._line("[store] saved %d pages in 44 ms"
                               % len(self.page_list))
                continue
            key, _, val = tok.partition("=")
            key = key.lower()
            if key == "pg":
                want = [int(v) for v in val.split(",")
                        if v.strip().isdigit() and int(v) < len(PAGE_NAME)]
                if want:
                    self.page_list = want
                    self.page_slot = min(self.page_slot, len(want) - 1)
                    self.page = self.page_list[self.page_slot]
            elif key == "gp":
                self.goto(int(val) if val.isdigit() else 0)
            elif key == "gs":
                self.game_sub = max(0, min(2, int(val or 0)))
            elif key == "ms":
                self.music_sub = max(0, min(MUSIC_SUBS - 1, int(val or 0)))
            elif key == "cs":
                sl = cus_slot(self.page)
                if sl is not None and val.isdigit():
                    self.cus_sub[sl] = max(0, min(self.cus_subs[sl] - 1,
                                                  int(val)))
            elif key == "cn":
                bits = val.split(",")
                if len(bits) == 2 and bits[0].isdigit() and bits[1].isdigit():
                    sl = int(bits[0])
                    if 0 <= sl < CUS_SLOTS:
                        self.cus_subs[sl] = max(1, min(CUS_FACES, int(bits[1])))
                        self.cus_sub[sl] = min(self.cus_sub[sl],
                                               self.cus_subs[sl] - 1)
            elif key == "cv":
                bits = val.split(",", 2)
                if len(bits) == 3 and bits[0].isdigit() and bits[1].isdigit():
                    sl, face = int(bits[0]), int(bits[1])
                    if 0 <= sl < CUS_SLOTS and 0 <= face < CUS_FACES:
                        try:
                            raw = base64.b64decode(bits[2])
                        except Exception:
                            raw = b""
                        if raw:
                            self.cus[sl][face] = raw[:CUS_BYTES]
            elif key in ("ao", "hl"):
                mask = 0
                for v in val.split(","):
                    if v.strip().isdigit():
                        mask |= 1 << int(v)
                if key == "ao":
                    self.aod = mask
                else:
                    self.hid_lock = mask
            elif key == "hs":
                self.hdr_style = int(val or 0)
            elif key == "rs":
                pass                       # the rev counter's shape: GAME only
            elif key == "fl":
                self.poke()
                self._line("[alarm] %s" % val.partition(",")[2])
            # %al, %tm, %na, %ic, %au, %ts, %ky and the rest are accepted and
            # ignored: they change nothing this emulator can show.

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
        if text.startswith("%"):
            self.percent(text)
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
        elif c in "yY":
            self.media = not self.media
            self._line("layer = {}".format("MEDIA" if self.media else "gamepad"))
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
        self.user_service()
        self.announce_page()
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
