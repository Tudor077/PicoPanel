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
import settings

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

PAGE_NAMES = ["PANEL", "GAME", "HID", "SWITCHES", "ENCODER",
              "BUTTONS", "PCF8574", "I2C", "INFO"]
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
        self.geometry("880x660")
        self.minsize(760, 560)

        self.cfg = settings.load()
        self.q = queue.Queue()

        # Per-application volume. The board asks ('!AUD n'), this answers with
        # the name and level to show ('%au=..'). Created before the link so the
        # reader thread always has somewhere to hand its requests.
        self.audio = audio.AudioBridge(
            send=lambda line: self.link.send(line, echo=False),
            log=lambda msg: self.q.put(("err", msg)))
        self.link = Link(self.q, on_audio=self.audio.request)
        self.hub = Hub()
        self.hub.yield_outgauge = bool(self.cfg.get("yield_outgauge"))
        self.last_tel = None
        self.tel_count = 0
        self.tel_time = time.time()
        self.last_send = 0.0
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

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, **pad)

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

        ttk.Separator(right).pack(fill="x", pady=8)
        self.auto_var = tk.BooleanVar(value=autostart.is_enabled())
        ttk.Checkbutton(right, text="Start at logon", variable=self.auto_var,
                        command=self._toggle_autostart).pack(padx=6, pady=2)

        self.yield_var = tk.BooleanVar(value=bool(self.cfg.get("yield_outgauge")))
        ttk.Checkbutton(right, text="Leave OutGauge to CorsaConnect",
                        variable=self.yield_var,
                        command=self._toggle_yield).pack(padx=6, pady=2)

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

    def _quit(self):
        self._stop_send.set()
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
        often the window redraws."""
        period = 1.0 / SEND_HZ
        while not self._stop_send.is_set():
            tel = self.hub.best()
            if tel is not None and self.link.open:
                if self.link.send(tel.to_line(), echo=False):
                    self.hub.sent += 1
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

        if not self.mirror_var.get():
            # Frames arrived without us asking - the board was already mirroring
            # (someone typed 'o' on it). Follow what's actually happening.
            self.mirror_var.set(True)
        self.mirror_lbl.configure(text=f"{w}x{h} live", foreground="#0a6")

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
    App(hidden="--hidden" in sys.argv).mainloop()
