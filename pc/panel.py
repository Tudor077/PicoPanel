#!/usr/bin/env python3
"""
panel.py - the PicoPanel app: control panel + game telemetry.

One program, one owner of the serial port. There used to be two (panel + hub)
and they fought over COM at every start; now the telemetry runs in the same
process as the interface.

    pythonw panel.py              start normally, with a window
    pythonw panel.py --hidden     start hidden, tray icon only
    python  panel.py --console    with a console, for debugging

Needs:  pip install pyserial      (pywin32 and Pillow are optional; without
                                   them it runs without the tray icon)
"""

import base64
import os
import queue
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    sys.exit("pyserial is missing.  Run:  pip install pyserial")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from telemetry.sources import ALL

import audio
import autostart
import editor
import pagelist
import phone
import sticks
import widgets as WG
import settings
import single

try:
    from tray import Tray
except Exception:                 # pywin32 / Pillow missing, or an odd Windows
    Tray = None

RP2040_VID = 0x2E8A
BAUD = 115200
# The board's screen renders at 62 FPS, so there's a point in feeding it that
# often. On USB CDC the 115200 rate is a fiction - the transfer runs at USB
# speed, not at the baud rate - so 60 lines a second (~5 KB/s) loads nothing.
SEND_HZ = 60
STALE_S = 2.0

# The board's pages, in the order of its own enum - the app talks about them
# by number, so this list IS the protocol. It had fallen behind: MUSIC was
# added to the board and not here, so every label after it was one out.
PAGE_NAMES = ["PANEL", "GAME", "MUSIC",
              "MINE 1", "MINE 2", "MINE 3", "MINE 4",
              "MINE 5", "MINE 6", "MINE 7", "MINE 8",
              "HID", "SWITCHES", "ENCODER", "BUTTONS", "PCF8574", "I2C", "INFO"]
# The board keeps eight pages that the PC draws. They are pages like any other -
# they sit in the rotation and they can be dragged about - and which of them
# EXIST is up to you: the app makes one on a button and unmakes it on another,
# and only the ones you have made appear in the list. Eight is where it stops
# because each one is a whole frame of the board's RAM.
CUSTOM_FIRST, CUSTOM_N = 3, 8
# The two pages that have faces of their own, by name rather than by number:
# the numbers move every time the board grows a page, and have.
P_GAME, P_MUSIC = PAGE_NAMES.index("GAME"), PAGE_NAMES.index("MUSIC")

PANEL_BTN = ["UP", "DN", "LF", "RT", "MD", "ST", "EN"]

# The mirrored screen, drawn this many times larger than the real 128x32 panel.
# 4x is 512x128 on screen: readable across a desk without taking over the window.
MIRROR_SCALE = 4
MIRROR_LIT = "#e6f2ff"        # an OLED's slightly blue white
MIRROR_DARK = "#0b0f14"
PCF_BTN = ["A1", "A2", "A3", "A4", "B1", "B2", "B3", "B4"]


# ---------------------------------------------------------------------
def parse_telemetry(line):
    """The board's report line -> a dict. None if it isn't a report."""
    if "ENC=" not in line or "BTN=" not in line:
        return None
    out = {}
    for tok in line.strip().split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
    if "BTN" not in out:
        return None

    def num(key, default=None):
        try:
            return int(out[key])
        except (KeyError, ValueError):
            return default

    return {
        "sw1": num("SW2"),          # firmware SW2 = the 3-position switch
        "sw2": num("SW3"),
        "enc": num("ENC", 0),
        "tot": num("TOT", 0),
        "err": num("ERR", 0),
        "btn": [c == "1" for c in out.get("BTN", "")],
        "pcf": [c == "1" for c in out.get("PCF", "")],
        "pcf_present": "PCF" in out,
        "page": num("PG"),
        "hid": out.get("HID"),
        "game": out.get("GM"),
        "fps": num("FPS"),
        "oled": out.get("OLED") == "ok",
    }


def find_pico():
    for p in serial.tools.list_ports.comports():
        if p.vid == RP2040_VID:
            return p.device
    return None


EMULATOR_PORT = "EMULATOR"


def list_ports():
    # The emulator first, so the app is usable with no board on the desk.
    out = [f"{EMULATOR_PORT}  <- no hardware needed"]
    for p in serial.tools.list_ports.comports():
        out.append(f"{p.device}{'  <- Pico' if p.vid == RP2040_VID else ''}")
    return out


# ---------------------------------------------------------------------
class Link:
    """The serial link. Reading sits on its own thread and posts through the
    queue: Tkinter isn't safe from another thread."""

    def __init__(self, q, on_audio=None):
        self.q = q
        # Which page the board is showing. Kept here rather than posted through
        # the queue because the sender thread reads it, and the queue is drained
        # by the window - which runs four times a second when it is in the tray.
        self.board_page = -1
        # GAME and MUSIC have faces of their own, and GAME's count depends on
        # what is sending - a tank has less to show than an airliner - so the
        # board tells us rather than us guessing.
        self.board_sub = 0
        self.subs_seen = {}         # page -> how many sub-pages it said it has
        # How far the board's header has slid away, 0 shown to 9 gone. A page
        # the PC draws covers the whole screen, header included, so without
        # this its header would be the only one on the panel that never goes.
        self.board_hdr = 0
        # Called straight from the reader thread for '!AUD' lines. It only
        # enqueues, so the reader is never held up by COM calls.
        self.on_audio = on_audio
        self.ser = None
        self.thread = None
        self.stop = threading.Event()
        self.lock = threading.Lock()

    @property
    def open(self):
        return self.ser is not None and self.ser.is_open

    def connect(self, port):
        self.disconnect()
        try:
            if port == EMULATOR_PORT:
                # FakeSerial duck-types pyserial, so _reader() below cannot tell
                # the difference - which is the point: the emulator exercises
                # the same parsing path the board does.
                from fakepanel import FakeSerial
                self.ser = FakeSerial(port, BAUD, timeout=0.2)
            else:
                self.ser = serial.Serial(port, BAUD, timeout=0.2)
        except serial.SerialException as e:
            self.q.put(("err", f"can't open {port}: {e}"))
            self.ser = None
            return False
        self.stop.clear()
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()
        self.q.put(("info", f"connected to {port}"))
        self.q.put(("settings", None))   # the board forgets: tell it again
        return True

    def disconnect(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1.0)
            self.thread = None
        if self.ser:
            try:
                self.ser.close()
            except serial.SerialException:
                pass
            self.ser = None

    def send(self, text, echo=True):
        with self.lock:
            if not self.open:
                if echo:
                    self.q.put(("err", "not connected"))
                return False
            try:
                self.ser.write((text + "\n").encode("ascii", "ignore"))
                if echo:
                    self.q.put(("tx", text))
                return True
            except serial.SerialException as e:
                self.q.put(("err", f"write failed: {e}"))
                return False

    def _reader(self):
        buf = b""
        while not self.stop.is_set():
            try:
                chunk = self.ser.read(256)
            except (serial.SerialException, OSError, TypeError):
                self.q.put(("err", "the board disappeared"))
                self.q.put(("down", None))
                return
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", "replace").rstrip("\r")
                if not line:
                    continue
                if line.startswith("!FB "):
                    # A frame, not a log line - see mirrorService() in the
                    # firmware. Decoded here, on the reader thread, so the UI
                    # thread only ever gets finished bytes.
                    try:
                        _, w, h, b64 = line.split(" ", 3)
                        self.q.put(("fb", (int(w), int(h),
                                           base64.b64decode(b64))))
                    except (ValueError, base64.binascii.Error):
                        pass
                    continue
                if line.startswith("!PAGE "):
                    # "!PAGE 1 2 4": page, the face showing, how many it has.
                    # The older board said only the first number, and that one
                    # still means the same thing.
                    try:
                        bits = line.split()
                        self.board_page = int(bits[1])
                        if len(bits) >= 4:
                            self.board_sub = int(bits[2])
                            self.subs_seen[self.board_page] = max(1, int(bits[3]))
                        if len(bits) >= 5:
                            self.board_hdr = int(bits[4])
                    except (ValueError, IndexError):
                        pass
                    continue
                if line.startswith("!AUD "):
                    # The panel asking for a volume change. Handled off this
                    # thread - see AudioBridge - and deliberately NOT logged:
                    # a spin of the knob would fill the window with twenty
                    # identical lines.
                    try:
                        if self.on_audio:
                            self.on_audio(int(line.split()[1]))
                    except (ValueError, IndexError):
                        pass
                    continue
                tel = parse_telemetry(line)
                self.q.put(("tel", tel) if tel else ("rx", line))


class Hub:
    """The telemetry sources, in the same process as the interface."""

    def __init__(self):
        self.sources = []
        self.enabled = True
        self.sent = 0
        self.yield_outgauge = False

    def start(self):
        for key, cls in ALL.items():
            if key == "demo":
                continue                 # only started on request
            if key == "outgauge" and self.yield_outgauge:
                continue                 # the port is left free for somebody else
            s = cls()
            s.start()
            self.sources.append(s)

    def restart(self):
        self.stop()
        self.sources = []
        self.start()

    def add_demo(self):
        for s in self.sources:
            if s.name == "DEMO":
                return s
        s = ALL["demo"]()
        s.start()
        self.sources.append(s)
        return s

    def drop_demo(self):
        for s in list(self.sources):
            if s.name == "DEMO":
                s.stop()
                self.sources.remove(s)

    def best(self):
        """The source that spoke most recently, if it's still fresh."""
        best = None
        for s in self.sources:
            tel = s.latest()
            if tel is None or tel.age() > STALE_S:
                continue
            if best is None or tel.stamp > best.stamp:
                best = tel
        return best

    def stop(self):
        for s in self.sources:
            s.stop()


# ---------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self, hidden=False):
        super().__init__()
        self.title("PicoPanel")
        # The taskbar shows the WINDOW's icon, not the executable's - which is
        # why a perfectly good icon in the .exe still left Tk's default feather
        # sitting down there. Same drawing as the tray and the exe, written out
        # because Tk wants a file on disk.
        try:
            import icon as _icon
            _ico = os.path.join(tempfile.gettempdir(), "picopanel_win.ico")
            _icon.save_ico(_ico)
            self.iconbitmap(default=_ico)       # default: every window we open
            self.icon_path = _ico               # the editor window wants it too
        except Exception:
            pass                                # an icon is never worth a crash
        # Wide enough for a page card: the picture alone is 256 pixels, and
        # the whole point of the list is that you see the pages rather than
        # read their names.
        self.geometry("1000x700")
        self.minsize(900, 600)

        self.cfg = settings.load()
        self.q = queue.Queue()

        # Per-application volume. The board asks ('!AUD n'), this answers with
        # the name and level to show ('%au=..'). Created before the link so the
        # reader thread always has somewhere to hand its requests.
        self.audio = audio.AudioBridge(
            send=lambda line: self.link.send(line, echo=False),
            log=lambda msg: self.q.put(("err", msg)),
            selected=self.cfg.get("audio_target"),
            on_select=self._remember_audio_target)
        self.audio.unicode_titles = bool(self.cfg.get("unicode_titles", True))
        self.audio.mixer.set_use_inapp(bool(self.cfg.get("app_own_volume", True)))
        if self.audio.lyrics:
            self.audio.lyrics.enabled = bool(self.cfg.get("karaoke", False))
        # A stick, a wheel or a pad, if one is plugged in: the centring
        # widget's two axes. Polled only when a widget asks - measured at
        # 4 microseconds for both devices on this machine.
        self.sticks = sticks.Sticks()
        # The phone's alarms. Yours live on your phone - that is what actually
        # wakes you - so the panel is told about them rather than asking you to
        # type them in twice.
        self.phone = phone.Bridge(log=lambda m, t="info": self.q.put((t, m)))
        self.link = Link(self.q, on_audio=self.audio.request)
        self.hub = Hub()
        self.hub.yield_outgauge = bool(self.cfg.get("yield_outgauge"))
        self.last_tel = None
        self.tel_count = 0
        self.tel_time = time.time()
        self.last_send = 0.0
        self._fb_seq = 0        # frames arrived, so the walk can wait for new ones
        self._typing = {}       # header text that is still arriving
        self.hold_until = 0.0   # don't reconnect before this
        self.tray = None

        self._stop_send = threading.Event()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.hub.start()
        self.audio.start()
        port = find_pico()
        if port:
            self.port_var.set(port)
            self._connect()
        else:
            self._log("the board isn't connected - still looking", "info")

        self._start_tray()
        if hidden and self.tray:
            self.withdraw()
        elif hidden:
            self.iconify()          # no tray, at least minimise it

        # Sending to the board lives on its own thread, NOT in the UI loop. With
        # the window hidden in the tray that loop only turns 4 times a second so
        # it doesn't burn CPU for nothing - and it used to drag telemetry along
        # with it, so the screen got 4 packets a second and looked laggy.
        self._sender = threading.Thread(target=self._send_loop, daemon=True)
        self._sender.start()

        self.after(50, self._pump)
        self.after(1000, self._autoconnect)

    # -------------------------------------------------- construction
    def _build(self):
        pad = dict(padx=6, pady=4)

        top = ttk.Frame(self)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="Port:").pack(side="left")
        self.port_var = tk.StringVar()
        self.port_box = ttk.Combobox(top, textvariable=self.port_var, width=22)
        self.port_box.pack(side="left", padx=4)
        self._refresh_ports()
        ttk.Button(top, text="Rescan", command=self._refresh_ports).pack(side="left")
        self.conn_btn = ttk.Button(top, text="Connect", command=self._toggle)
        self.conn_btn.pack(side="left", padx=8)
        self.status = ttk.Label(top, text="disconnected")
        self.status.pack(side="left", padx=10)
        self.rate = ttk.Label(top, text="")
        self.rate.pack(side="right")

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, **pad)

        body = ttk.Frame(self.tabs)
        self.tabs.add(body, text="Panel")

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))

        # ---- game telemetry
        g = ttk.LabelFrame(left, text="Game telemetry")
        g.pack(fill="x")
        row = ttk.Frame(g)
        row.pack(fill="x", pady=4)
        self.game_lbl = ttk.Label(row, text="no game running",
                                  font=("", 11, "bold"), width=30)
        self.game_lbl.pack(side="left", padx=6)
        self.demo_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="test generator", variable=self.demo_var,
                        command=self._toggle_demo).pack(side="left", padx=6)
        self.sent_lbl = ttk.Label(row, text="")
        self.sent_lbl.pack(side="right", padx=6)
        self.game_det = ttk.Label(g, text="", font=("Consolas", 9))
        self.game_det.pack(anchor="w", padx=6, pady=(0, 6))

        # ---- the panel's screen, mirrored
        mf = ttk.LabelFrame(left, text="Panel screen")
        mf.pack(fill="x", pady=(6, 0))
        head = ttk.Frame(mf)
        head.pack(fill="x")
        self.mirror_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(head, text="Mirror the OLED", variable=self.mirror_var,
                        command=self._toggle_mirror).pack(side="left", padx=6)
        self.mirror_lbl = ttk.Label(head, text="off", foreground="#888")
        self.mirror_lbl.pack(side="left", padx=6)
        self.mirror_canvas = tk.Canvas(mf, width=128 * MIRROR_SCALE,
                                       height=32 * MIRROR_SCALE,
                                       highlightthickness=1,
                                       highlightbackground="#999",
                                       background="#0b0f14")
        self.mirror_canvas.pack(padx=6, pady=6)
        self._fb_img = None          # Tk drops an image that nothing references

        # ---- panel state
        st = ttk.LabelFrame(left, text="Panel state")
        st.pack(fill="both", expand=True, pady=(6, 0))
        self.lamps_pcf = self._lamp_row(st, "Buttons A / B", PCF_BTN)
        self.lamps_btn = self._lamp_row(st, "D-pad (not fitted)", PANEL_BTN)

        sw = ttk.Frame(st)
        sw.pack(fill="x", pady=6)
        self.sw1_lbl = ttk.Label(sw, text="SW1 (3 pos): ?", width=20)
        self.sw1_lbl.pack(side="left", padx=6)
        self.sw2_lbl = ttk.Label(sw, text="SW2 (5 pos): ?", width=20)
        self.sw2_lbl.pack(side="left", padx=6)

        enc = ttk.Frame(st)
        enc.pack(fill="x", pady=6)
        ttk.Label(enc, text="Encoder").pack(side="left", padx=6)
        self.enc_bar = ttk.Progressbar(enc, maximum=100, length=200)
        self.enc_bar.pack(side="left", padx=6)
        self.enc_lbl = ttk.Label(enc, text="0")
        self.enc_lbl.pack(side="left")

        info = ttk.Frame(st)
        info.pack(fill="x", pady=6)
        self.page_lbl = ttk.Label(info, text="Page: ?", width=20)
        self.page_lbl.pack(side="left", padx=6)
        self.oled_lbl = ttk.Label(info, text="OLED: ?", width=16)
        self.oled_lbl.pack(side="left")
        self.err_lbl = ttk.Label(info, text="Enc errors: 0")
        self.err_lbl.pack(side="left", padx=6)

        hid = ttk.Frame(st)
        hid.pack(fill="x", pady=8)
        self.hid_lbl = ttk.Label(hid, text="HID: ?", font=("", 11, "bold"), width=18)
        self.hid_lbl.pack(side="left", padx=6)
        ttk.Button(hid, text="Turn HID on / off",
                   command=lambda: self.link.send("u")).pack(side="left", padx=4)
        ttk.Button(hid, text="Show the mapping",
                   command=lambda: self.link.send("j")).pack(side="left", padx=4)
        ttk.Button(hid, text="Media layer",
                   command=lambda: self.link.send("y")).pack(side="left", padx=4)

        # ---- commands
        right = ttk.LabelFrame(body, text="Commands")
        right.pack(side="right", fill="y")
        for text, ch in [("Rescan I2C", "i"), ("Bus test", "b"),
                         ("Bus recovery", "k"), ("Re-init OLED", "x"),
                         ("Screen visual test", "d"), ("I2C speed", "c"),
                         ("Render period", "f"), ("Encoder status", "n"),
                         ("Pin states", "p"), ("Reset counters", "r"),
                         ("Reporting on/off", "s"), ("Help", "?")]:
            ttk.Button(right, text=text, width=24,
                       command=lambda c=ch: self.link.send(c)).pack(padx=6, pady=2)
        ttk.Separator(right).pack(fill="x", pady=6)
        self.entry = ttk.Entry(right, width=24)
        self.entry.pack(padx=6, pady=2)
        self.entry.bind("<Return>", self._send_entry)
        ttk.Button(right, text="Send", width=24,
                   command=self._send_entry).pack(padx=6, pady=2)

        self._build_settings()

        # No Widgets tab. It was one page of the eight, permanently open, from
        # before a page could be opened in its own window - and it invited you
        # to lay out slot 0 whether or not that page even existed.
        self.tabs.bind("<<NotebookTabChanged>>", self._preview_follow)

        logf = ttk.LabelFrame(self, text="Log")
        logf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(logf, height=10, wrap="none", state="disabled",
                           font=("Consolas", 9))
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("tx", foreground="#0a7")
        self.log.tag_configure("err", foreground="#c33")
        self.log.tag_configure("info", foreground="#888")

    def _lamp_row(self, parent, title, names):
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=4)
        ttk.Label(f, text=title, width=17).pack(side="left", padx=6)
        lamps = []
        for n in names:
            c = tk.Canvas(f, width=34, height=26, highlightthickness=0)
            c.pack(side="left", padx=2)
            rect = c.create_rectangle(1, 1, 33, 25, fill="#ddd", outline="#999")
            c.create_text(17, 13, text=n, font=("", 8))
            lamps.append((c, rect))
        return lamps

    @staticmethod
    def _set_lamps(lamps, states):
        for i, (c, rect) in enumerate(lamps):
            on = i < len(states) and states[i]
            c.itemconfigure(rect, fill="#2c6" if on else "#ddd")

    # -------------------------------------------------- tray
    def _start_tray(self):
        if Tray is None:
            self._log("no tray icon (pywin32 or Pillow is missing)", "info")
            return
        try:
            self.tray = Tray(self.q)
            self.tray.set_autostart(self.auto_var.get())
            self.tray.start()
        except Exception as e:
            self.tray = None
            self._log(f"tray icon unavailable: {e}", "info")

    def _on_close(self):
        """The X hides to the tray when there is one; otherwise it really does
        close. A telemetry relay makes sense running without a window."""
        if self.tray:
            self.withdraw()
        else:
            self._quit()

    def _toggle_unicode(self):
        """Real alphabets, or the board's own font with everything in Latin.

        Off is worth having: the transliteration is easier to read for someone
        who doesn't read Cyrillic, and the 5x7 font matches the rest of the
        panel. On is the default because a title should say what it says.
        """
        on = bool(self.uni_var.get())
        self.cfg["unicode_titles"] = on
        settings.save(self.cfg)
        self.audio.unicode_titles = on
        self._log("titles: %s" % ("their own alphabet" if on
                                  else "Latin letters, panel font"), "info")

    # ---------------------------------------------------------------- settings
    def _build_settings(self):
        """Everything that is a choice rather than a reading.

        The panel tab is what the board is doing right now; this is what you
        want it to do. They were mixed together in a column of buttons, which
        is fine for five and useless for twelve.
        """
        page = ttk.Frame(self.tabs)
        self.tabs.add(page, text="Settings")
        pad = dict(padx=8, pady=5)

        # ---- the rotation
        rot = ttk.LabelFrame(page, text="Pages on the panel")
        rot.pack(side="left", fill="both", **pad)


        self.pg_cards = pagelist.PageList(rot, self)
        self.pg_cards.pack(fill="both", expand=True, padx=8, pady=6)

        pre = ttk.Frame(rot)
        pre.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(pre, text="Preset").pack(side="left")
        self.preset_var = tk.StringVar()
        self.preset_box = ttk.Combobox(pre, textvariable=self.preset_var,
                                       width=14, state="readonly")
        self.preset_box.pack(side="left", padx=4)
        self.preset_box.bind("<<ComboboxSelected>>", self._preset_load)
        ttk.Button(pre, text="Save", width=6,
                   command=self._preset_save).pack(side="left", padx=2)
        ttk.Button(pre, text="Delete", width=7,
                   command=self._preset_del).pack(side="left", padx=2)
        self._preset_refresh()

        # ---- everything else, stacked down the right-hand side. Side by
        # side, the third panel fell off the edge of the window.
        rest = ttk.Frame(page)
        rest.pack(side="left", fill="both", expand=True, **pad)

        scr = ttk.LabelFrame(rest, text="Screen")
        scr.pack(fill="x")

        ttk.Label(scr, text="Rev counter").pack(anchor="w", padx=8, pady=(6, 0))
        self.rpm_var = tk.IntVar(value=int(self.cfg.get("rpm_style", 0)))
        for text, val in (("A bar across the bottom", 0), ("A needle", 1)):
            ttk.Radiobutton(scr, text=text, variable=self.rpm_var, value=val,
                            command=self._apply_rpm).pack(anchor="w", padx=20)

        ttk.Separator(scr).pack(fill="x", padx=8, pady=8)
        ttk.Label(scr, text="How things appear").pack(anchor="w", padx=8)
        ttk.Label(scr, wraplength=330, justify="left", foreground="#555",
                  text="When something in a page's header changes - its name, "
                       "where it sits in the rotation, an alarm turning up."
                  ).pack(anchor="w", padx=8, pady=(2, 2))
        self.anim_var = tk.StringVar(value=str(self.cfg.get("anim_style",
                                                            "classic")))
        for text, val in (("Classic - down from the top, like the panel's own",
                           "classic"),
                          ("Wipe - in from the left, like the splash", "wipe"),
                          ("Type - a letter at a time", "type"),
                          ("None - just there", "none")):
            ttk.Radiobutton(scr, text=text, variable=self.anim_var, value=val,
                            command=self._apply_anim).pack(anchor="w", padx=20)

        ttk.Separator(scr).pack(fill="x", padx=8, pady=8)
        ttk.Label(scr, text="I2C speed").pack(anchor="w", padx=8)
        ttk.Label(scr, wraplength=330, justify="left", foreground="#555",
                  text="There is no vsync on these modules - they bring out SDA "
                       "and SCL and nothing else. What you can do is spend less "
                       "time writing the frame, because the tear is the panel "
                       "showing a buffer that is half old and half new. Measured "
                       "on this board: 18.1 ms a frame at 400 kHz, 9.0 ms at 1 "
                       "MHz. 1 MHz is past what the SSD1306 promises - if yours "
                       "dislikes it you get rubbish on screen and you come back "
                       "here.").pack(anchor="w", padx=8, pady=(2, 4))
        self.i2c_var = tk.IntVar(value=int(self.cfg.get("i2c_khz", 400)))
        row = ttk.Frame(scr)
        row.pack(anchor="w", padx=20)
        for khz in (100, 400, 1000):
            ttk.Radiobutton(row, text="%d kHz" % khz, variable=self.i2c_var,
                            value=khz, command=self._apply_i2c).pack(side="left",
                                                                     padx=(0, 10))

        # ---- the rest
        opt = ttk.LabelFrame(rest, text="Options")
        opt.pack(fill="x", pady=(10, 0))
        self.auto_var = tk.BooleanVar(value=autostart.is_enabled())
        ttk.Checkbutton(opt, text="Start at logon", variable=self.auto_var,
                        command=self._toggle_autostart).pack(anchor="w", padx=8, pady=3)
        self.yield_var = tk.BooleanVar(value=bool(self.cfg.get("yield_outgauge")))
        ttk.Checkbutton(opt, text="Leave OutGauge to CorsaConnect",
                        variable=self.yield_var,
                        command=self._toggle_yield).pack(anchor="w", padx=8, pady=3)
        self.uni_var = tk.BooleanVar(value=bool(self.cfg.get("unicode_titles", True)))
        ttk.Checkbutton(opt, text="Titles in their own alphabet",
                        variable=self.uni_var,
                        command=self._toggle_unicode).pack(anchor="w", padx=8, pady=3)
        self.inapp_var = tk.BooleanVar(value=bool(self.cfg.get("app_own_volume", True)))
        ttk.Checkbutton(opt, text="Move the app's own volume slider",
                        variable=self.inapp_var,
                        command=self._toggle_inapp).pack(anchor="w", padx=8, pady=3)
        self.kar_var = tk.BooleanVar(value=bool(self.cfg.get("karaoke", False)))
        ttk.Checkbutton(opt, text="Karaoke (asks lrclib.net for lyrics)",
                        variable=self.kar_var,
                        command=self._toggle_karaoke).pack(anchor="w", padx=8, pady=3)

        # ---- alarms
        al = ttk.LabelFrame(rest, text="Alarms")
        al.pack(fill="x", pady=(10, 0))
        ttk.Label(al, wraplength=320, justify="left", foreground="#555",
                  text="The screen flashes and says so, whatever page is up - "
                       "so it is not a widget on a page you might not be "
                       "looking at. The app keeps the time; the board owns "
                       "the glass.").pack(anchor="w", padx=8, pady=(4, 2))
        self.al_box = tk.Listbox(al, height=4, activestyle="none")
        self.al_box.pack(fill="x", padx=8)
        row = ttk.Frame(al)
        row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text="at").pack(side="left")
        self.al_time = ttk.Entry(row, width=6)
        self.al_time.insert(0, "07:30")
        self.al_time.pack(side="left", padx=4)
        self.al_text = ttk.Entry(row, width=14)
        self.al_text.insert(0, "GET UP")
        self.al_text.pack(side="left", padx=4)
        ttk.Button(row, text="Add", width=5,
                   command=self._alarm_add).pack(side="left", padx=2)
        ttk.Button(row, text="Remove", width=7,
                   command=self._alarm_del).pack(side="left", padx=2)
        ttk.Button(row, text="Test", width=5,
                   command=lambda: self._alarm_fire(
                       {"text": self.al_text.get()})).pack(side="right")

        # ---- the phone
        ph = ttk.Frame(al)
        ph.pack(fill="x", padx=8, pady=(2, 6))
        self.phone_var = tk.BooleanVar(value=bool(self.cfg.get("phone_bridge")))
        ttk.Checkbutton(ph, text="Take alarms from the phone",
                        variable=self.phone_var,
                        command=self._toggle_phone).pack(anchor="w")
        self.phone_lbl = ttk.Label(ph, foreground="#555", wraplength=320,
                                   justify="left", text="")
        self.phone_lbl.pack(anchor="w", padx=20)
        self._alarm_refresh()
        if self.phone_var.get():
            self._toggle_phone()
        self._alarm_done = {}
        self._had_alarm = False
        self.after(3000, self._alarm_tick)

        self._pg_load()

    # ---- the page rotation ----------------------------------------------
    # ---- the pages -------------------------------------------------------
    def page_name(self, pg):
        """What to call it. Yours can be renamed; the board's cannot - those
        names are its own and appear in its header."""
        slot = self.slot_of(pg)
        if slot is not None:
            mine = (self.cfg.get("page_names") or {}).get(str(slot))
            if mine:
                return mine
        return PAGE_NAMES[pg] if 0 <= pg < len(PAGE_NAMES) else "?"

    def set_page_name(self, slot, name):
        names = dict(self.cfg.get("page_names") or {})
        name = (name or "").strip()
        if name:
            names[str(slot)] = name[:16]     # the header is 128 pixels wide
        else:
            names.pop(str(slot), None)       # empty: back to MINE n
        self.cfg["page_names"] = names
        settings.save(self.cfg)
        self.pg_cards.refill()

    def page_of(self, slot):
        """The page number of one of your slots."""
        return CUSTOM_FIRST + slot

    def header_of(self, slot):
        """Does that page wear the board's header bar? Yes unless told not."""
        return bool((self.cfg.get("page_headers") or {}).get(str(slot), True))

    def set_header(self, slot, on):
        h = dict(self.cfg.get("page_headers") or {})
        h[str(slot)] = bool(on)
        self.cfg["page_headers"] = h
        settings.save(self.cfg)

    def header_for(self, slot):
        """The two strings the header shows: the name, and where the page sits
        in the rotation - the same counter the board puts on its own pages."""
        if not self.header_of(slot):
            return None
        pg = CUSTOM_FIRST + slot
        order = self._pg_ids[0]
        where = "%d/%d" % (order.index(pg) + 1, len(order)) if pg in order else ""
        name = self.page_name(pg)
        badge = self.alarm_badge()
        # The third number is where the board's own header has slid to, so
        # yours slides with it and goes away when the panel goes quiet. The
        # fourth is the countdown, which does NOT go away with it: the board
        # keeps showing it on its own pages too.
        shift, reveal = self.link.board_hdr, 1.0
        p = self._progress(slot, name, where, bool(badge))
        style = self.cfg.get("anim_style", "classic")
        if p < 1.0 and style != "none":
            if style == "classic":
                # Down from the top, which is how the header comes back on
                # every other page. Clamped to 8 so the bar is always the
                # thing on screen, rather than blinking through the state
                # where it does not exist at all.
                if shift < WG.HEADER_H:
                    shift = max(shift, min(8, int(round(9 * (1 - p)))))
            elif style == "wipe":
                reveal = p
            elif style == "type":
                cut = lambda t: t[:max(0, int(len(t) * p + 0.001))]
                name, badge = cut(name), cut(badge)
        return (name, where, shift, badge, reveal)

    # How long it takes to arrive. The board slides its own header the nine
    # pixels in about this long, and two things moving at two speeds on one
    # screen would look like two machines.
    ANIM_S = 0.25

    def _progress(self, slot, *content):
        """0 to 1 since anything in the header last changed.

        The name, the page's place in the rotation, an alarm appearing. NOT
        the countdown's own ticking: a header that played its arrival every
        sixty seconds would be a fidget, not an animation.
        """
        st = self._typing.get(slot)
        now = time.time()
        if st is None or st[0] != content:
            st = (content, now)
            self._typing[slot] = st
        return min(1.0, (now - st[1]) / self.ANIM_S)

    def slot_of(self, pg):
        """Which of the pages you draw yourself this is, or None."""
        i = pg - CUSTOM_FIRST
        return i if 0 <= i < CUSTOM_N else None

    def custom_slots(self):
        """The ones you have actually made. A missing setting means the one
        page the board starts with, so an old config opens looking the same."""
        v = self.cfg.get("custom_pages")
        if not isinstance(v, list):
            return [0]
        out = set()
        for i in v:
            try:
                i = int(i)
            except (TypeError, ValueError):
                continue
            if 0 <= i < CUSTOM_N:
                out.add(i)
        return sorted(out)

    def page_exists(self, pg):
        """A board page always exists; one of yours only if you made it."""
        slot = self.slot_of(pg)
        return True if slot is None else slot in self.custom_slots()

    def pg_new(self, after=None):
        """Another page of your own, right after that one, and lay it out now.

        The editor opens by itself: a page you have just made and cannot see is
        a page you made by accident.
        """
        have = self.custom_slots()
        free = next((i for i in range(CUSTOM_N) if i not in have), None)
        if free is None:
            self._log("that is all eight - delete one to make another", "err")
            return
        self.cfg["custom_pages"] = have + [free]
        settings.save(self.cfg)
        pg = CUSTOM_FIRST + free
        order, rest = self._pg_ids
        if pg in rest:
            rest.remove(pg)
        if pg not in order:
            at = order.index(after) + 1 if after in order else len(order)
            order.insert(at, pg)
        self.pg_cards.refill()
        self.pg_apply()
        self.pg_open(pg)

    def pg_delete(self, pg):
        """Throw a page of yours away, and its layout with it."""
        slot = self.slot_of(pg)
        if slot is None:
            return                # the board's own pages are not ours to delete
        if self.layout_of(slot):
            from tkinter import messagebox
            if not messagebox.askyesno(
                    "Delete the page?",
                    "%s has %d widget%s on it. Delete it?"
                    % (self.page_name(pg), len(self.layout_of(slot)),
                       "" if len(self.layout_of(slot)) == 1 else "s"),
                    parent=self):
                return
        win = getattr(self, "_edit_wins", {}).pop(slot, None)
        if win is not None and win.winfo_exists():
            win.destroy()
        self.cfg["custom_pages"] = [i for i in self.custom_slots() if i != slot]
        for key in ("layouts", "page_names", "page_headers"):
            d = dict(self.cfg.get(key) or {})
            d.pop(str(slot), None)
            self.cfg[key] = d
        settings.save(self.cfg)
        order, rest = self._pg_ids
        if pg in order:
            order.remove(pg)
        if pg in rest:
            rest.remove(pg)
        self.pg_cards.shots.pop(pg, None)
        self.pg_cards.refill()
        self.pg_apply()

    def layout_of(self, slot):
        return list((self.cfg.get("layouts") or {}).get(str(slot)) or [])

    def set_layout(self, slot, dicts):
        lay = dict(self.cfg.get("layouts") or {})
        lay[str(slot)] = dicts
        self.cfg["layouts"] = lay
        settings.save(self.cfg)

    def subs_of(self, pg):
        """How many faces that page has, as the board last said. One until it
        has been there - the count is the board's to know, not ours."""
        return self.link.subs_seen.get(pg, 1)

    def pg_show(self, pg, sub=0):
        """Put that page on the panel, so the card you clicked is the thing you
        are looking at."""
        self.link.send("%%gp=%d" % pg, echo=False)
        key = "gs" if pg == P_GAME else ("ms" if pg == P_MUSIC else None)
        if key:
            # Always, including face 0. The board keeps whichever face it was
            # left on, so "show me GAME" with no sub said left it wherever it
            # happened to be - and the walk then waited two seconds for a page
            # it had never actually asked for.
            self.link.send("%%%s=%d" % (key, sub), echo=False)

    def pg_open(self, pg):
        """Double-click: lay this page out. Only your own pages have a layout -
        the rest are drawn by the board and there is nothing here to edit."""
        slot = self.slot_of(pg)
        if slot is None:
            return
        win = getattr(self, "_edit_wins", {}).get(slot)
        if win is not None and win.winfo_exists():
            win.lift()
            return
        if not hasattr(self, "_edit_wins"):
            self._edit_wins = {}
        self._edit_wins[slot] = editor.EditorWindow(self, slot, self.page_name(pg))
        self.pg_show(pg)          # and show it, so you can see what you draw

    def pg_toggle(self, pg):
        order, rest = self._pg_ids
        if pg in order:
            if len(order) < 2:
                return            # never leave the panel with nothing to show
            order.remove(pg)
            rest.append(pg)
        else:
            rest.remove(pg)
            order.append(pg)
        rest.sort()
        self.pg_cards.refill()
        self.pg_apply()

    def pg_apply(self):
        self._pg_apply()

    def _pg_load(self):
        live = [i for i in range(len(PAGE_NAMES)) if self.page_exists(i)]
        order = list(self.cfg.get("page_order") or
                     [i for i in live if self.slot_of(i) in (None, 0)])
        order = [i for i in order if i in live]
        rest = [i for i in live if i not in order]
        self._pg_ids = (order, rest)
        self.pg_cards.refill()

    def _pg_apply(self):
        order, rest = self._pg_ids
        self.cfg["page_order"] = order
        settings.save(self.cfg)
        if order:
            self.link.send("%pg=" + ",".join(str(i) for i in order), echo=False)

    # ---- alarms ----------------------------------------------------------
    def next_alarm(self):
        """(seconds from now, what for) for the soonest alarm, or None.

        Both lists at once: the ones set here, and whatever the phone last
        said. Two sources for the same thing is the point - it is the same
        morning either way.
        """
        best = None
        now = time.localtime()
        for a in self._alarms():
            if not a.get("on", True):
                continue
            try:
                hh, mm = [int(x) for x in str(a.get("at", "")).split(":")[:2]]
            except ValueError:
                continue
            when = time.mktime((now.tm_year, now.tm_mon, now.tm_mday,
                                hh, mm, 0, 0, 0, -1))
            left = when - time.time()
            if left < -60:
                left += 86400            # it has gone today; it is tomorrow's
            if best is None or left < best[0]:
                best = (max(0.0, left), a.get("text", ""))
        p = self.phone.pending()
        if p and (best is None or p[0] < best[0]):
            best = p
        return best

    def alarm_badge(self):
        """"6h12" or "12:34" - what the header shows, or "" for no alarm.

        The same shape the firmware draws for its own pages, because the two
        sit on the same screen a page apart and a countdown that changed style
        halfway round the rotation would look like two different things.
        """
        nxt = self.next_alarm()
        if not nxt:
            return ""
        left = int(nxt[0])
        if left >= 3600:
            return "%dh%02d" % (left // 3600, (left % 3600) // 60)
        return "%d:%02d" % (left // 60, left % 60)

    def _alarms(self):
        a = self.cfg.get("alarms")
        return a if isinstance(a, list) else []

    def _alarm_refresh(self):
        self.al_box.delete(0, "end")
        for a in self._alarms():
            self.al_box.insert("end", "%s   %s" % (a.get("at", "??:??"),
                                                   a.get("text", "")))

    def _alarm_add(self):
        at = (self.al_time.get() or "").strip()
        try:
            h, m = [int(x) for x in at.split(":")]
            if not (0 <= h < 24 and 0 <= m < 60):
                raise ValueError
        except ValueError:
            self._log("an alarm wants a time like 07:30", "err")
            return
        alarms = self._alarms() + [{"at": "%02d:%02d" % (h, m),
                                    "text": self.al_text.get().strip(),
                                    "on": True}]
        self.cfg["alarms"] = alarms
        settings.save(self.cfg)
        self._alarm_refresh()

    def _alarm_del(self):
        sel = list(self.al_box.curselection())
        if not sel:
            return
        alarms = [a for i, a in enumerate(self._alarms()) if i not in sel]
        self.cfg["alarms"] = alarms
        settings.save(self.cfg)
        self._alarm_refresh()

    def _toggle_phone(self):
        """Listen for the phone, or stop.

        Off by default: it opens a port on the network, and a program that
        starts listening without being asked is a program you have to trust
        more than this one deserves.
        """
        on = bool(self.phone_var.get())
        self.cfg["phone_bridge"] = on
        settings.save(self.cfg)
        if on and self.phone.start():
            url = "http://%s:%d" % (phone.lan_ip(), self.phone.port)
            self.phone_lbl.configure(
                text="Open %s on the phone - or point the Pocket app at it. It "
                     "posts the next alarm; the panel counts down to it and "
                     "flashes when it comes." % url)
        elif not on:
            self.phone.stop()
            self.phone_lbl.configure(text="")

    def _alarm_fire(self, alarm):
        """Tell the board to flash. The text goes through the same
        transliteration the titles use - the board's font is ASCII."""
        from telemetry import text as T
        msg = T.clean(T.translit(alarm.get("text") or "ALARM"), 20)
        if not self.link.open:
            self._log("alarm: the board is not connected", "err")
            return
        self.link.send("%%fl=%d,%s" % (8000, msg), echo=False)
        self._log("alarm: %s" % msg, "info")

    def _alarm_tick(self):
        """Once every few seconds, because an alarm is a minute wide.

        Fired at most once per minute per alarm: this runs more often than
        that, and a screen flashing every three seconds for a minute is not
        what anybody meant.
        """
        # The board counts down in its own header, on every page - so it is
        # told how long is left, rather than having to be on the right page to
        # show it. Every few seconds is plenty: it counts the rest off itself.
        try:
            nxt = self.next_alarm()
            if self.link.open:
                from telemetry import text as T
                if nxt:
                    self.link.send("%%na=%d,%s"
                                   % (int(nxt[0]), T.clean(T.translit(nxt[1]), 16)),
                                   echo=False)
                elif self._had_alarm:
                    self.link.send("%na=", echo=False)
            self._had_alarm = bool(nxt)
        except Exception as e:
            self._log("could not tell the board about the alarm: %s" % e, "err")

        try:
            # The phone's, when it comes due. Once - the bridge forgets it as
            # soon as it has been rung, so it cannot fire twice.
            p = self.phone.pending()
            if p and p[0] <= 1.0:
                self._alarm_fire({"text": p[1] or "PHONE"})
                self.phone.clear()
            stamp = time.strftime("%Y-%m-%d %H:%M")
            now = stamp[-5:]
            for i, a in enumerate(self._alarms()):
                if not a.get("on", True) or a.get("at") != now:
                    continue
                key = "%d@%s" % (i, a.get("at"))
                if self._alarm_done.get(key) == stamp:
                    continue
                self._alarm_done[key] = stamp
                self._alarm_fire(a)
        except Exception as e:
            self._log("alarm check failed: %s" % e, "err")
        self.after(3000, self._alarm_tick)

    # ---- presets ---------------------------------------------------------
    def _presets(self):
        p = self.cfg.get("page_presets")
        return p if isinstance(p, dict) else {}

    def _preset_refresh(self):
        names = sorted(self._presets())
        self.preset_box["values"] = names
        if self.preset_var.get() not in names:
            self.preset_var.set("")

    def _preset_load(self, _ev=None):
        order = self._presets().get(self.preset_var.get())
        if not order:
            return
        live = [i for i in range(len(PAGE_NAMES)) if self.page_exists(i)]
        order = [i for i in order if i in live]
        rest = [i for i in live if i not in order]
        self._pg_ids = (order, rest)
        self.pg_cards.refill()

    def _preset_save(self):
        from tkinter import simpledialog
        name = simpledialog.askstring("Preset", "Call this arrangement what?",
                                      parent=self)
        if not name:
            return
        p = dict(self._presets())
        p[name.strip()] = list(self._pg_ids[0])
        self.cfg["page_presets"] = p
        settings.save(self.cfg)
        self._preset_refresh()
        self.preset_var.set(name.strip())

    def _preset_del(self):
        name = self.preset_var.get()
        p = dict(self._presets())
        if name in p:
            del p[name]
            self.cfg["page_presets"] = p
            settings.save(self.cfg)
            self._preset_refresh()

    # ---- collecting the pictures -----------------------------------------
    def collect_shots(self, done):
        """Walk every page, photograph it, put the panel back.

        A chain of after() calls rather than a loop with sleeps: the frames
        arrive on the reader thread and are drained by this one, so a loop that
        waited here would wait for pictures it was itself preventing.
        """
        if not self.link.open:
            self._log("connect first - the pictures come from the board", "err")
            return
        self.mirror_var.set(True)
        self._shot_home = (self.link.board_page, self.link.board_sub)
        self._shot_late = 0
        self._shot_queue = [(i, 0) for i in range(len(PAGE_NAMES))
                            if self.page_exists(i)]
        self._shot_done = done
        self._shot_seq0 = self._fb_seq
        self.after(300, self._shot_mirror)

    def _shot_mirror(self, tries=0):
        """Frames have to be arriving before the walk can start.

        The board toggles its mirror with 'o' and takes no view on which way
        round it should be - so asking for it while it is already on turns it
        OFF, and the walk then photographs nothing at all while looking busy.
        Here we watch for frames instead of assuming, and ask again if none
        come.
        """
        if self._fb_seq != self._shot_seq0:
            self.after(50, self._shot_step)
            return
        if tries >= 3:
            self._log("the board is not sending frames - no pictures", "err")
            return
        self.link.send("o", echo=False)
        self._shot_seq0 = self._fb_seq
        self.after(600, lambda t=tries + 1: self._shot_mirror(t))

    def _shot_step(self):
        if not self._shot_queue:
            pg, sb = self._shot_home
            if pg is not None and pg >= 0:
                self.pg_show(pg, sb)
            if self._shot_late:
                self._log("%d page%s did not answer in time - no picture for "
                          "those" % (self._shot_late,
                                     "" if self._shot_late == 1 else "s"), "err")
            self._log("page pictures updated", "info")
            return
        pg, sb = self._shot_queue.pop(0)
        self.pg_show(pg, sb)
        self._shot_want = (pg, sb)
        self._shot_mark = None
        self._shot_until = time.time() + 2.0
        self.after(20, self._shot_wait)

    def _shot_wait(self):
        """Wait for the board to SAY it is there, then for two whole frames.

        A fixed delay was wrong and looked convincing: at 130 ms, half the
        pictures were of the page before - measured, 7 of 15 - so the cards
        showed MUSIC under GAME and nobody would have known which were true.
        The board announces its page; believing it beats guessing at it.

        Two frames, not one: the frame in flight when the page changed was
        drawn before it changed.
        """
        pg, sb = self._shot_want
        if (self.link.board_page == pg and self.link.board_sub == sb
                and self._shot_mark is None):
            self._shot_mark = self._fb_seq
        if self._shot_mark is not None and self._fb_seq >= self._shot_mark + 2:
            self._shot_grab(pg, sb)
        elif time.time() > self._shot_until:
            self._shot_late += 1
            self._shot_grab(pg, sb, late=True)   # no picture rather than a lie
        else:
            self.after(20, self._shot_wait)

    def _shot_grab(self, pg, sb, late=False):
        fb = None if late else getattr(self, "_last_fb", None)
        photo = None
        if fb:
            try:
                photo = pagelist.frame_photo(*fb)
            except Exception:
                photo = None
        try:
            self._shot_done((pg, sb), photo)
        except Exception:
            pass
        # Now that we have been there, the board has told us how many faces
        # that page has - so the rest of them go on the queue next, in place.
        if sb == 0:
            more = self.subs_of(pg)
            if more > 1:
                self._shot_queue[:0] = [(pg, i) for i in range(1, more)]
        self.after(30, self._shot_step)

    # ---- the screen ------------------------------------------------------
    def _apply_rpm(self):
        v = int(self.rpm_var.get())
        self.cfg["rpm_style"] = v
        settings.save(self.cfg)
        self.link.send("%%rs=%d" % v, echo=False)

    def _apply_anim(self):
        self.cfg["anim_style"] = self.anim_var.get()
        settings.save(self.cfg)
        self._typing = {}          # so the next frame plays it the new way

    def _apply_i2c(self):
        v = int(self.i2c_var.get())
        self.cfg["i2c_khz"] = v
        settings.save(self.cfg)
        self.link.send("%%ic=%d" % v, echo=False)

    def push_settings(self):
        """Send everything the board holds in RAM. Called on every connect,
        because the board forgets it all when it is unplugged."""
        order = self.cfg.get("page_order")
        if order:
            self.link.send("%pg=" + ",".join(str(i) for i in order), echo=False)
        self.link.send("%%rs=%d" % int(self.cfg.get("rpm_style", 0)), echo=False)
        self._had_alarm = False     # so the next tick tells it either way
        self.link.send("%%ic=%d" % int(self.cfg.get("i2c_khz", 400)), echo=False)

    def _toggle_karaoke(self):
        """Synced lyrics on the KARAOKE page.

        The only thing in this program that leaves the machine, which is why it
        starts off: it sends the artist, the title and the length to lrclib.net,
        once per track, and gets the words back. Nothing else goes, and nothing
        goes at all while this is unticked.
        """
        on = bool(self.kar_var.get())
        self.cfg["karaoke"] = on
        settings.save(self.cfg)
        if self.audio.lyrics:
            self.audio.lyrics.enabled = on
        self._log("karaoke: %s" % ("on - lyrics come from lrclib.net" if on
                                   else "off, nothing leaves this machine"), "info")

    def _toggle_inapp(self):
        """The app's own slider, or only its channel in the mixer.

        Off is the safe setting if anything odd happens in a game: the mixer
        never reaches into another program's window, so there is nothing that
        could pull you out of what you were doing.
        """
        on = bool(self.inapp_var.get())
        self.cfg["app_own_volume"] = on
        settings.save(self.cfg)
        self.audio.mixer.set_use_inapp(on)
        self._log("volume: %s" % ("the app's own slider where it has one" if on
                                  else "the Windows mixer channel only"), "info")

    def save_cfg(self):
        settings.save(self.cfg)

    def widget_data(self):
        """One flat dict of everything a widget can be pointed at.

        Telemetry and what's playing come from different places and are shaped
        differently; the widgets should not have to know that.
        """
        d = {}
        t = self.hub.best()
        if t is not None:
            for k in ("speed_kmh", "rpm", "rpm_max", "redline", "gear",
                      "fuel_pct", "throttle", "brake", "turbo_bar", "engine_c",
                      "kts", "vspeed_fpm", "alt_ft", "hdg", "gforce", "aoa",
                      "src", "text"):
                d[k] = getattr(t, k, None)
        d.update(self.sticks.read())
        # The panel's own state. It reports all of this five times a second
        # anyway - the board's pages are built out of it, so yours can be too.
        nxt = self.next_alarm()
        d["alarm_in"] = nxt[0] if nxt else None
        d["alarm_text"] = nxt[1] if nxt else ""
        t = self.last_tel
        if t:
            d["pcf"] = t.get("pcf") or []
            d["btn"] = t.get("btn") or []
            d["enc"] = t.get("enc") or 0
            d["enc_total"] = t.get("tot") or 0
            d["sw1"] = t.get("sw1") or 0
            d["sw2"] = t.get("sw2") or 0
            d["fps"] = t.get("fps") or 0
            d["hid"] = 1 if t.get("hid") == "1" else 0
        snap = self.audio.now.snapshot() if getattr(self.audio, "now", None) else None
        if snap:
            d["np_title"] = snap.get("title")
            d["np_artist"] = snap.get("artist")
            d["np_playing"] = 1 if snap.get("playing") else 0
            pos = float(snap.get("pos") or 0)
            dur = float(snap.get("dur") or 0)
            d["np_pos"], d["np_dur"] = pos, dur
            d["np_pct"] = (100.0 * pos / dur) if dur > 0 else 0.0
        return d

    def _remember_audio_target(self, label):
        """Called from the audio thread when you pick a different app.

        Saved so the choice survives a restart: hunting back down the list to
        Spotify every time you open the app would make the feature not worth
        using.
        """
        if self.cfg.get("audio_target") == label:
            return
        self.cfg["audio_target"] = label
        settings.save(self.cfg)

    def _quit(self):
        self._stop_send.set()
        self.phone.stop()
        self.audio.stop()
        self.hub.stop()
        self.link.disconnect()
        if self.tray:
            self.tray.stop()
        self.destroy()

    # -------------------------------------------------- actions
    def _refresh_ports(self):
        ports = list_ports()
        self.port_box["values"] = ports
        if not self.port_var.get() and ports:
            self.port_var.set(ports[0].split()[0])

    def _toggle(self):
        if self.link.open:
            self.link.disconnect()
            self._set_connected(False)
            self._log("disconnected", "info")
        else:
            self._connect()

    def _connect(self):
        port = self.port_var.get().split()[0] if self.port_var.get() else ""
        if not port:
            self._log("pick a port first", "err")
            return
        if self.link.connect(port):
            self._set_connected(True)

    def _autoconnect(self):
        """The board gets reflashed often and changes port; we pick it up again
        by ourselves."""
        if time.time() < self.hold_until:
            self.after(2000, self._autoconnect)
            return
        if not self.link.open:
            port = find_pico()
            if port:
                self.port_var.set(port)
                if self.link.connect(port):
                    self._set_connected(True)
                    self._refresh_ports()
        self.after(2000, self._autoconnect)

    def _set_connected(self, on):
        self.conn_btn.configure(text="Disconnect" if on else "Connect")
        self.status.configure(text="connected" if on else "disconnected")

    def _send_entry(self, _evt=None):
        text = self.entry.get().strip()
        if text:
            self.link.send(text)
            self.entry.delete(0, "end")

    def _toggle_demo(self):
        if self.demo_var.get():
            self.hub.add_demo()
            self._log("test generator started", "info")
        else:
            self.hub.drop_demo()
            self._log("test generator stopped", "info")

    def _toggle_yield(self):
        """There's only one OutGauge port and CorsaConnect wants it too. Whoever
        binds first gets it; with this ticked we step aside, and the telemetry
        arrives from CorsaConnect's mirror (UDP 5051), already enriched."""
        self.cfg["yield_outgauge"] = bool(self.yield_var.get())
        ok, msg = settings.save(self.cfg)
        self.hub.yield_outgauge = self.cfg["yield_outgauge"]
        self.hub.restart()
        if self.cfg["yield_outgauge"]:
            self._log("OutGauge port (4444) left free; waiting for the mirror on 5051",
                      "info")
            self._log("in CorsaConnect: tick 'Send it a copy' in the PICOPANEL card",
                      "info")
        else:
            self._log("listening directly on OutGauge 4444 again", "info")
        if not ok:
            self._log(f"can't save the setting: {msg}", "err")

    def _toggle_autostart(self):
        ok, msg = autostart.enable() if self.auto_var.get() else autostart.disable()
        self._log(f"start at logon: {msg}", "info" if ok else "err")
        if not ok:
            self.auto_var.set(autostart.is_enabled())
        if self.tray:
            self.tray.set_autostart(self.auto_var.get())

    def _log(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag or ())
        if int(self.log.index("end-1c").split(".")[0]) > 800:
            self.log.delete("1.0", "200.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    # -------------------------------------------------- loop
    def _pump(self):
        # Frames are coalesced: at 20 a second several can pile up between two
        # turns of this loop, and drawing the ones already superseded would cost
        # 4 ms each to produce a picture nobody ever sees.
        frame = None
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "tel":
                    self._apply(payload)
                elif kind == "fb":
                    frame = payload
                elif kind == "rx":
                    self._log(payload)
                elif kind == "tx":
                    self._log(f">>> {payload}", "tx")
                elif kind == "err":
                    self._log(payload, "err")
                elif kind == "info":
                    self._log(payload, "info")
                elif kind == "settings":
                    self.push_settings()
                elif kind == "down":
                    self.link.disconnect()
                    self._set_connected(False)
                elif kind == "show":
                    self.deiconify()
                    self.lift()
                    self.focus_force()
                elif kind == "autostart":
                    self.auto_var.set(payload)
                    self._toggle_autostart()
                elif kind == "release":
                    # The serial port is exclusive: while we hold it, not even
                    # arduino-cli can send the 1200-baud reset that puts the
                    # board into its bootloader. We let it go for a minute.
                    self.hold_until = time.time() + 60
                    self.link.disconnect()
                    self._set_connected(False)
                    self._log("port released for 60 s - you can flash now", "info")
                    if self.tray:
                        self.tray.set_tip("PicoPanel - port released for flashing")
                elif kind == "quit":
                    self._quit()
                    return
        except queue.Empty:
            pass

        if frame is not None:
            self._draw_frame(*frame)

        self._push_game()

        now = time.time()
        if now - self.tel_time >= 1.0:
            self.rate.configure(text=f"{self.tel_count} reports/s")
            self.tel_count = 0
            self.tel_time = now
            self._update_tip()

        # Hidden in the tray nobody is drawing anything - no point spinning 20
        # times a second to update invisible widgets. Telemetry to the board is
        # rate-limited separately, in _send_loop.
        #
        # With the mirror running we turn faster than the frames arrive: at 50 ms
        # a 20 fps stream would wait up to half a frame here and the screen would
        # visibly trail the panel.
        if self.state() != "normal":
            delay = 250
        elif self.mirror_var.get():
            delay = 20
        else:
            delay = 50
        self.after(delay, self._pump)

    def _send_loop(self):
        """The only place that sends telemetry. Its own rate, independent of how
        often the window redraws - which matters here, because the window runs
        at four hertz while it sits in the tray and the panel would crawl."""
        period = 1.0 / SEND_HZ
        next_page = 0.0
        page_period = 1.0 / editor.SEND_HZ
        while not self._stop_send.is_set():
            tel = self.hub.best()
            if tel is not None and self.link.open:
                if self.link.send(tel.to_line(), echo=False):
                    self.hub.sent += 1

            # Whichever of your pages is showing, and only while it is. The
            # picture is rendered from the stored layout rather than from an
            # editor window: the page has to work with every window closed.
            now = time.time()
            slot = self.slot_of(self.link.board_page) if self.link.open else None
            if slot is not None and now >= next_page:
                next_page = now + page_period
                try:
                    items = [WG.Widget.from_dict(d) for d in self.layout_of(slot)]
                    img = WG.render(items, self.widget_data(),
                                    header=self.header_for(slot))
                    if img is not None:
                        b = base64.b64encode(WG.to_frame(img)).decode()
                        self.link.send("%%cv=%d,%s" % (slot, b), echo=False)
                except Exception as e:
                    # Once. A page that quietly stops being sent looks exactly
                    # like a page nobody has drawn yet, and the board says so
                    # in good faith: "no layout yet".
                    if not getattr(self, "_cv_moaned", False):
                        self._cv_moaned = True
                        self.q.put(("err", "the page is not being sent: %s" % e))

            self._stop_send.wait(period)

    def _push_game(self):
        """Display only. Sending happens in _send_loop."""
        tel = self.hub.best()
        if tel is None:
            self.game_lbl.configure(text="no game running", foreground="#888")
            self.game_det.configure(text="")
            return
        self.game_lbl.configure(text=f"{tel.src}", foreground="#0a6")
        g = "R" if tel.gear < 0 else ("N" if tel.gear == 0 else str(tel.gear))
        det = (f"{tel.speed_kmh:6.1f} km/h   {tel.rpm:5.0f} rpm   gear {g}"
               f"   {tel.fuel_pct:3.0f}% fuel")
        if tel.redline:
            det += f"   redline {tel.redline:.0f}"
        else:
            det += "   redline: not learned yet"
        self.game_det.configure(text=det)
        self.sent_lbl.configure(text=f"{self.hub.sent} sent")

    def _toggle_mirror(self):
        """Ask the board to start or stop sending frames ('o' toggles there).

        We don't track the board's own state - if the two ever disagree, one
        more click sorts it out, and the label follows the frames that actually
        arrive rather than what we believe we asked for.
        """
        self.link.send("o")
        if not self.mirror_var.get():
            self.mirror_canvas.delete("all")
            self.mirror_lbl.configure(text="off", foreground="#888")

    def _draw_frame(self, w, h, data):
        """One SSD1306 frame buffer -> an image on the canvas.

        The buffer is in pages: one byte holds 8 pixels stacked vertically, so
        pixel (x, y) is bit y%8 of byte x + (y//8)*width. We build the image at
        the panel's real size and let Tk's own zoom() blow it up - scaling pixel
        by pixel in Python would be 65k iterations per frame instead of 4k.

        Tk's PhotoImage takes colours as a string of rows, which is the one
        format it accepts without an image library: PPM through data= isn't
        recognised, and PNG would mean hand-rolling a encoder for a 1-bit image.
        """
        need = w * h // 8
        if len(data) < need:
            return
        rows = []
        for y in range(h):
            base, bit = (y >> 3) * w, 1 << (y & 7)
            rows.append("{" + " ".join(
                MIRROR_LIT if data[base + x] & bit else MIRROR_DARK
                for x in range(w)) + "}")

        img = tk.PhotoImage(width=w, height=h)
        img.put(" ".join(rows))
        img = img.zoom(MIRROR_SCALE)
        self.mirror_canvas.configure(width=w * MIRROR_SCALE, height=h * MIRROR_SCALE)
        self.mirror_canvas.delete("all")
        self.mirror_canvas.create_image(0, 0, image=img, anchor="nw")
        self._fb_img = img           # keep it alive, or Tk shows nothing

        # Kept for the page cards: they are photographed by driving the board
        # from page to page. The counter is how the walk knows a frame is one
        # drawn AFTER the page changed rather than the one already in flight.
        self._last_fb = (w, h, data)
        self._fb_seq = getattr(self, "_fb_seq", 0) + 1

        if not self.mirror_var.get():
            # Frames arrived without us asking - the board was already mirroring
            # (someone typed 'o' on it). Follow what's actually happening.
            self.mirror_var.set(True)
        self.mirror_lbl.configure(text=f"{w}x{h} live", foreground="#0a6")

    def _preview_follow(self, _ev=None):
        """Turn the board's mirror on while the Settings tab is up, and put it
        back the way it was on the way out.

        The preview has to come from the board - a mock-up would be a second
        drawing of every page, and the moment the firmware changed one it would
        start lying."""
        try:
            on_settings = self.tabs.tab(self.tabs.select(), "text") == "Settings"
        except Exception:
            return
        if on_settings == getattr(self, "_prev_on", False):
            return
        self._prev_on = on_settings
        if on_settings:
            self._prev_was = bool(self.mirror_var.get())
            if not self._prev_was:
                self.mirror_var.set(True)
                self._toggle_mirror()
        elif not getattr(self, "_prev_was", False):
            self.mirror_var.set(False)
            self._toggle_mirror()

    def _update_tip(self):
        if not self.tray:
            return
        t = self.hub.best()
        if t is not None:
            self.tray.set_tip(f"PicoPanel - {t.src} {t.speed_kmh:.0f} km/h")
        elif self.link.open:
            self.tray.set_tip("PicoPanel - connected, no game")
        else:
            self.tray.set_tip("PicoPanel - board disconnected")

    def _apply(self, t):
        self.tel_count += 1
        self.last_tel = t
        self._set_lamps(self.lamps_pcf, t["pcf"])
        self._set_lamps(self.lamps_btn, t["btn"])
        self.sw1_lbl.configure(text=f"SW1 (3 pos): {t['sw1'] or '?'}")
        self.sw2_lbl.configure(text=f"SW2 (5 pos): {t['sw2'] or '?'}")
        self.enc_bar["value"] = max(0, min(100, t["enc"]))
        self.enc_lbl.configure(text=f"{t['enc']}   total {t['tot']}")
        pg = t["page"]
        name = PAGE_NAMES[pg] if pg is not None and 0 <= pg < len(PAGE_NAMES) else "?"
        self.page_lbl.configure(text=f"Page: {name}")
        fps = f"  {t['fps']} FPS" if t["fps"] is not None else ""
        self.oled_lbl.configure(text=f"OLED: {'ok' if t['oled'] else 'missing'}{fps}")
        self.err_lbl.configure(text=f"Enc errors: {t['err']}")
        hid = t["hid"]
        if hid == "1":
            self.hid_lbl.configure(text="HID: ARMED", foreground="#c60")
        elif hid == "0":
            self.hid_lbl.configure(text="HID: off", foreground="")
        else:
            self.hid_lbl.configure(text="HID: unavailable", foreground="#888")


if __name__ == "__main__":
    # The serial port is exclusive: a second copy can only sit there failing to
    # open it. Clicking the shortcut again now raises the one that IS running.
    if not single.claim():
        single.wake_the_other()
        sys.exit(0)
    App(hidden="--hidden" in sys.argv).mainloop()
