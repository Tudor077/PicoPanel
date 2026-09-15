# panel.py - the desktop app for PicoPanel

Both directions travel over the same serial link:

| Direction | What goes across |
|---|---|
| Pico -> PC | telemetry, ~5 times a second: buttons, switches, encoder, page, HID state, OLED state — and the screen's frame buffer while the mirror is on |
| PC -> Pico | game telemetry at 60 Hz, plus console commands from the buttons or typed by hand |

## Starting it

```
python panel.py
```

The board finds itself by USB VID `0x2E8A`, so it doesn't matter which COM port
it landed on — and it moves on every reflash anyway.

Needs `pyserial`. If it's missing: `pip install pyserial`. `pywin32` and
`Pillow` are optional and only add the tray icon.

**Close the Arduino IDE's Serial Monitor first.** The port is exclusive: while
the IDE holds it, this app can't open it (and arduino-cli can't flash either).

## What you see

- lamps for the eight expander buttons (A1..A4, B1..B4) and for the d-pad
- the position of both slide switches, under the names on the board: SW1 = 3
  positions, SW2 = 5 positions
- the encoder's value as a bar, plus the total and the error count
- which page the OLED is showing, and whether the screen is answering
- the HID state, with a button to arm and disarm it
- **Mirror the OLED** — the panel's own screen, live, scaled 4x. The board
  sends the frame buffer itself, so this can't drift out of step with what the
  panel actually shows.

At the bottom is the raw log: everything that isn't telemetry (replies to
commands, startup messages) lands there. What you send appears with `>>>`.

## Sharing OutGauge with CorsaConnect

OutGauge has exactly one listener, and
[CorsaConnect](https://github.com/Tudor077/CorsaConnect) wants UDP 4444 too.
Tick **Leave OutGauge to CorsaConnect** and we stop binding it; CorsaConnect
then sends us a copy of the enriched telemetry on 5051, which is better than the
raw packet — the redline it learned, the slide and the crash impact are already
in there.

## An implementation detail

Reading sits on its own thread and posts to the window through a `queue.Queue`.
Tkinter isn't thread-safe — any widget touched from the reader thread can hang
or corrupt the window. The thread only fills the queue; the window drains it
every 50 ms from its own loop.

## About the switch names

The firmware reports them as `SW2` and `SW3`, after the object names in the code
and in `Panou.kicad_sch`. The app shows them as **SW1** (3 positions) and
**SW2** (5 positions), which are the names you actually use. The translation
happens in exactly one place, in `parse_telemetry()`.
