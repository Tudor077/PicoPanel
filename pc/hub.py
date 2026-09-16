#!/usr/bin/env python3
"""
hub.py - collects telemetry from several games and sends it to the panel.

You start the hub once and leave it. Every source listens in parallel; the one
that spoke most recently is the one sent to the board. So you change games and
there's nothing to switch here.

    python hub.py                 every source, without the demo
    python hub.py --only demo     just the generator, to see the screen
    python hub.py --only ets2
    python hub.py --list          which sources exist
    python hub.py --dry           don't open the serial port, just print

Needs:  pip install pyserial
"""

import argparse
import sys
import time

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    sys.exit("pyserial is missing.  Run:  pip install pyserial")

from telemetry.sources import ALL

RP2040_VID = 0x2E8A
BAUD = 115200
SEND_HZ = 10             # past this you gain nothing: the screen runs at 30 FPS
STALE_S = 2.0            # a source older than this no longer counts


def find_pico():
    for p in serial.tools.list_ports.comports():
        if p.vid == RP2040_VID:
            return p.device
    return None


class Board:
    """The serial link, with reconnection. If the board disappears (a reflash, a
    pulled cable) the hub doesn't die - it retries every 2 seconds."""

    def __init__(self, port=None, dry=False):
        self.want = port
        self.dry = dry
        self.ser = None
        self.next_try = 0.0

    def connect(self):
        if self.dry or self.ser or time.time() < self.next_try:
            return
        self.next_try = time.time() + 2.0
        port = self.want or find_pico()
        if not port:
            return
        try:
            self.ser = serial.Serial(port, BAUD, timeout=0.2)
            print(f"[serial] connected to {port}")
        except serial.SerialException as e:
            print(f"[serial] can't open {port}: {e}")
            print("         close the Serial Monitor or panel.py - the port is exclusive")
            self.ser = None

    def send(self, line):
        if self.dry:
            return True
        self.connect()
        if not self.ser:
            return False
        try:
            self.ser.write((line + "\n").encode("ascii", "ignore"))
            return True
        except serial.SerialException:
            print("[serial] disconnected")
            self.ser = None
            return False


def main():
    ap = argparse.ArgumentParser(description="Game telemetry -> PicoPanel")
    ap.add_argument("--only", action="append",
                    help="start only this source (can be repeated)")
    ap.add_argument("--port", help="serial port (default: find the board by VID)")
    ap.add_argument("--udp-port", type=int, default=4444, help="the OutGauge port")
    ap.add_argument("--http-port", type=int, default=8099, help="the HTTP intake port")
    ap.add_argument("--ets2-url",
                    default="http://localhost:25555/api/ets2/telemetry")
    ap.add_argument("--dry", action="store_true",
                    help="don't open the serial port, just print")
    ap.add_argument("--quiet", action="store_true", help="no once-a-second line")
    ap.add_argument("--list", action="store_true", help="list the sources")
    args = ap.parse_args()

    if args.list:
        for k, cls in ALL.items():
            print(f"  {k:<9} {cls.name}")
        return 0

    wanted = args.only or [k for k in ALL if k != "demo"]
    bad = [w for w in wanted if w not in ALL]
    if bad:
        sys.exit(f"unknown source: {', '.join(bad)}  (see --list)")

    sources = []
    for key in wanted:
        cls = ALL[key]
        if key == "outgauge":
            s = cls(port=args.udp_port)
        elif key == "http":
            s = cls(port=args.http_port)
        elif key == "ets2":
            # The source now takes host/port so it can keep one connection
            # alive; the --ets2-url flag is split here to stay compatible.
            from urllib.parse import urlparse
            u = urlparse(args.ets2_url)
            s = cls(host=u.hostname or 'localhost', port=u.port or 25555,
                    path=u.path or '/api/ets2/telemetry')
        else:
            s = cls()
        s.start()
        sources.append(s)

    print("sources started:")
    time.sleep(1.0)   # give the threads a moment to report
    for s in sources:
        print(f"  {s.name:<9} {s.status}")
    print("\nCtrl+C to stop.\n")

    board = Board(args.port, dry=args.dry)
    # Open the port NOW, not on the first telemetry packet. Otherwise, with no
    # game running, the hub sat silent and you couldn't even tell whether it had
    # found the board - it looked broken when it simply had nothing to send.
    if not args.dry:
        board.next_try = 0.0
        board.connect()
        if not board.ser:
            print("[serial] board not found yet - retrying in the background.")
    print()

    period = 1.0 / SEND_HZ
    last_print = 0.0
    sent = 0

    try:
        while True:
            # the freshest source wins; that's how you change games without
            # touching anything here
            best = None
            for s in sources:
                tel = s.latest()
                if tel is None or tel.age() > STALE_S:
                    continue
                if best is None or tel.stamp > best.stamp:
                    best = tel

            if best is not None:
                if board.send(best.to_line()):
                    sent += 1
                now = time.time()
                if not args.quiet and now - last_print >= 1.0:
                    last_print = now
                    print(f"\r{best.summary()}   [{sent} sent]", end="", flush=True)
            else:
                now = time.time()
                if not args.quiet and now - last_print >= 2.0:
                    last_print = now
                    print("\rno game running" + " " * 40, end="", flush=True)

            time.sleep(period)
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        for s in sources:
            s.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
