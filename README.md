# PicoPanel

A control panel for sim racing and flight sims, built on an RP2040 board: eight
buttons, two slide switches and a rotary encoder that a PC sees as a plain USB
gamepad, plus a 128x32 OLED that shows live telemetry from whatever game you're
playing.

```
┌──────────────── THE PANEL (RP2040) ─────────────┐
│  8 buttons · 2 switches · encoder · OLED        │
│  USB HID: 19 gamepad buttons, no driver needed  │
└───────┬──────────────────────────────▲──────────┘
        │ panel state, 5x per second   │ telemetry, 60x per second
        │ screen mirror, on request    │ commands
        ▼                              │
┌──────────────── PC APP (Python) ─────┴──────────┐
│  BeamNG · ETS2/ATS · MSFS · CorsaConnect · HTTP │
│  one window: lamps, encoder, log, screen mirror │
└─────────────────────────────────────────────────┘
```

> **This is built for one specific panel: mine.** The pin numbers, the switch
> positions and the encoder's behaviour are all measured on the hardware in
> front of me, and the firmware header documents every assumption. If your board
> is wired differently everything still works, but you'll be editing the pin
> defines at the top of `PicoPanel.ino` first.
>
> Questions about the panel, the build, or anything else around it:
> **TikTok [@entity.077](https://www.tiktok.com/@entity.077)**.

## The controls

Exactly what's on the board, and what each one does:

| Control | What it is | Default job |
|---|---|---|
| **A1 A2 A3 A4** | four buttons on the PCF8574 I2C expander (P0..P3) | gamepad buttons 1-4 |
| **B1 B2 B3 B4** | four more on the same expander (P4..P7) | gamepad buttons 5-8 |
| **SW1** | 3-position slide switch | gamepad buttons 9-11, held while selected |
| **SW2** | 5-position slide switch | gamepad buttons 12-16, held while selected |
| **Encoder** | Alps EC11, 20 detents per turn | button 17 clockwise, 18 anticlockwise |
| **Encoder click** | the encoder's own push button | gamepad button 19 |
| **USER** | the button on the RP2040 board, next to BOOT | tap = next page · twice = media layer · hold 1s = arm/disarm HID |
| **D-pad** | UP DWN LFT RHT MID SET on header J3 | unmapped, so they drive the on-screen menu |
| **OLED** | SSD1306 128x32 on I2C 0x3C | the pages below |

Hold the **media layer** (double-tap USER) and A1-A4 become play/pause, next,
previous and mute, while the encoder becomes volume, without spending a single
gamepad button.

Everything is in one table at the top of `PicoPanel.ino`, under
`MAP YOUR CONTROLS HERE`. A slot can be `PAD(n)`, `KEY('a')`,
`COMBO(KEY_LEFT_CTRL, 'c')`, `MEDIA(CC_VOL_UP)`, `CLICK(MOUSE_LEFT)`,
`SCROLL(+1)`, `TEXT("hello")` or `NOTHING`.

**Why gamepad buttons and not keys:** Windows shows them in "Set up USB game
controllers" and any game binds them directly. They collide with nothing you
type and can't fire a shortcut by accident.

## The screen

Nine pages, changed with the USER button: **PANEL** (everything at a glance),
**GAME**, **HID**, **SWITCHES**, **ENCODER**, **BUTTONS**, **PCF8574**, **I2C**,
**INFO**. The screen dims after 10 seconds and goes dark after 20; any button,
switch or encoder click wakes it, and it never sleeps while a game is sending
telemetry.

The GAME page has its own sub-pages: speed, gear and a dithered rev bar with the
learned redline marked; then fuel, temperature, turbo and rpm; then throttle,
brake and the redline. A flight sim gets airspeed, vertical speed, altitude,
COM frequencies, squawk and autopilot modes instead. The PC only has to say
`knd=air`.

## The PC app

```sh
cd pc
pip install pyserial          # pywin32 + Pillow are optional, for the tray icon
python panel.py
```

The board is found by USB VID, so it doesn't matter which COM port it landed on
- and it gets found again by itself after every reflash. One window shows the
lamps for all eight buttons and the d-pad, both switch positions, the encoder,
the page, the HID state and a raw log of everything the board says.

**Mirror the OLED** ticks on the panel's own screen, live, scaled 4x in the
window. The board sends the actual frame buffer, not a description of it, so
what you see is what the panel shows, down to the pixel: there's no second
implementation of the pages to drift out of step.

Telemetry sources run in parallel and the freshest one wins, so you change games
without touching anything:

| Source | Game | Setup |
|---|---|---|
| OutGauge | BeamNG.drive, LFS | Options > Others > OutGauge, 127.0.0.1:4444 |
| Corsa | anything [CorsaConnect](https://github.com/Tudor077/CorsaConnect) feeds | tick **Send it a copy** in its PICOPANEL card |
| ETS2 | Euro / American Truck Sim | the SDK plugin plus its HTTP server |
| MSFS | Flight Simulator | `pip install SimConnect` |
| HTTP | anything of your own, Roblox included | POST JSON to `127.0.0.1:8099` |
| Demo | none, a generator | tick **test generator** to see the screen move |

**Sharing OutGauge:** the protocol has exactly one listener, so PicoPanel and
CorsaConnect used to lock each other out of UDP 4444. Tick **Leave OutGauge to
CorsaConnect** and CorsaConnect keeps the port and sends a copy on 5051 -
which arrives better than the raw packet, with the learned redline, the slide
and the crash impact already folded in.

## Building the firmware

Arduino IDE, with:

- **Boards Manager**: "Raspberry Pi Pico/RP2040/RP2350" by Earle Philhower
  → Tools > Board > **VCC-GND YD RP2040**
- **Tools > USB Stack**: `Pico SDK` (the default)
- **Library Manager**: `Adafruit SSD1306` + `Adafruit GFX Library`

There are **two** "Raspberry Pi Pico" entries in the board list. The one from
"Arduino Mbed OS RP2040 Boards" has no Keyboard/Mouse/Joystick: the sketch still
builds and the diagnostics still run, but HID is missing entirely and the screen
tells you so. HID needs Philhower.

Close the PC app before flashing: the serial port is exclusive, and while
anything holds it, not even the 1200-baud reset into the bootloader gets
through. The tray menu has **Release the port for 60s** for exactly this.

## The hardware

YD-RP2040 (VCC-GND Studio), which is not a stock Pico: GPIO23 is an onboard
WS2812, GPIO24 the USER button, GPIO25 the blue LED.

```
GPIO4  SDA          GPIO16 ENC_A        GPIO19 UP
GPIO5  SCL          GPIO17 ENC_B        GPIO20 DWN
GPIO6..9   SW1_1..4 GPIO18 ENC_SW       GPIO21 LFT
GPIO10..15 SW2_1..6                     GPIO26 RHT
                                        GPIO27 MID
GPIO24 USER button (onboard)            GPIO28 SET
GPIO25 blue LED (onboard)
```

Two details worth knowing, because they're the kind of thing that eats an
evening:

**The switches have no common ground.** Every pin goes to a GPIO, so they can't
be read like DIP switches. The firmware scans them as a matrix: one pin driven
low at a time, the rest read with pull-ups, and works out which pin is the
common one by intersection: it's the only pin present in the shorted pair
whatever the position, so after the switch has been through two positions
exactly one candidate remains.

**The encoder's filter is asymmetric.** The 1k/100nF filter on A and B makes
falling edges instant and rising edges late, so at speed the middle transition
of a detent disappears. The default decoder doesn't count quarter-steps at all:
it takes the direction from the first falling edge after leaving rest and emits
one step on the way back. The `n` command over serial tells you what kind of
encoder it thinks you have, and `l` dumps the raw waveform of a single click.

## Serial commands

115200 baud, one letter plus Enter. `?` lists them all.

| | |
|---|---|
| `i` `b` `k` `c` `x` | rescan I2C · bus electrical test · bus recovery · I2C speed · re-init OLED |
| `d` `v` `h` `1` `2` `0` | visual test · raw SSD1306 commands · 32/64 height · force address |
| `a` `e` `t` `l` `g` `n` | encoder: calibrate · mode · trace · raw log · jump recovery · status |
| `p` `m` `r` `s` `f` | pin states · switch matrix · reset counters · reporting · render period |
| `u` `j` `y` `w` | arm HID · show the mapping · media layer · double-press window |
| `o` | mirror the screen to the PC app |

Game telemetry comes in on the same link, as one line starting with `$`:

```
$src=ETS2;spd=88;rpm=1450;rpmmax=2500;rl=2250;gear=6;fuel=62;tmp=88
```

## How it's split

`core0` runs the buttons, the encoder, the switches, HID, serial and telemetry.
`core1` does nothing but draw the screen. A frame costs ~15 ms on I2C at 400 kHz
and `Wire` blocks, so with everything on one core the USB stack stopped being
serviced at 60 FPS and the board dropped off the bus. Moved across, the same
work touches neither USB nor button latency.
