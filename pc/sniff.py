#!/usr/bin/env python3
"""
sniff.py - listens raw on the OutGauge port and says what arrives.

It answers one question: are packets coming in or not? If they are, it shows how
big they are and what's in them - which tells you straight away whether the
problem is in the game (not sending) or at our end (sending, but not decoded).

    python sniff.py            port 4444
    python sniff.py 4445       MotionSim, to see the game is sending something

STOP hub.py first: two programs can't listen on the same UDP port.
"""

import socket
import struct
import sys
import time

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4444
FMT = "<I4sHBBfffffffIIfff16s16s"
SIZE = struct.calcsize(FMT)          # 92

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    sock.bind(("0.0.0.0", PORT))
except OSError as e:
    sys.exit(f"can't listen on {PORT}: {e}\n(is hub.py running? stop it first)")
sock.settimeout(1.0)

print(f"listening on UDP :{PORT}  -  Ctrl+C to stop")
print(f"expecting {SIZE}-byte packets (OutGauge) or {SIZE + 4} (with the ID)\n")

n = 0
quiet = 0
t0 = time.time()
try:
    while True:
        try:
            data, addr = sock.recvfrom(512)
        except socket.timeout:
            quiet += 1
            if quiet in (3, 10, 30):
                print(f"[{quiet}s] nothing yet. Is the game paused or in a menu? "
                      f"OutGauge only sends while the simulation runs.")
            continue

        quiet = 0
        n += 1
        if n == 1 or n % 60 == 0:
            print(f"#{n:<5} {len(data):>3} bytes from {addr[0]}:{addr[1]}", end="")
            if len(data) in (SIZE, SIZE + 4):
                f = struct.unpack(FMT, data[:SIZE])
                car, gear, speed, rpm, thr = f[1], f[3], f[5], f[6], f[14]
                print(f"  car={car.decode('ascii','ignore').strip(chr(0))!r}"
                      f"  {speed * 3.6:6.1f} km/h  {rpm:5.0f} rpm"
                      f"  gear={gear}  thr={thr:.2f}")
            else:
                print("  <- UNEXPECTED LENGTH, this isn't OutGauge")
                print(f"      first bytes: {data[:16].hex(' ')}")
except KeyboardInterrupt:
    dt = time.time() - t0
    print(f"\n{n} packets in {dt:.0f}s"
          + (f"  ({n / dt:.0f}/s)" if dt > 0 and n else "  - NONE AT ALL"))
