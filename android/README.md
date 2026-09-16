# PicoPanel Pocket

The panel's companion, on the phone itself. The phone is the USB host, so no PC
is involved: plug the panel in with a USB-C OTG cable and the app talks CDC
serial to it directly.

**It also runs with no hardware at all.** The app opens in emulator mode with a
simulated panel already moving — lamps, switches, encoder and the mirrored OLED.
That is not a mockup: `FakePanel` emits byte-for-byte what `PicoPanel.ino`
emits, so the parser, the frame decoder and the whole UI are exercised exactly
as they are against the real board.

## Getting it onto a phone

The `Android APK` workflow builds it on every push and attaches a
debug-signed APK to a prerelease. Open the release on the phone, tap the
`.apk`, allow installs from your browser once, done.

## What it shows

| | |
|---|---|
| **Panel screen** | the real frame buffer, pixel for pixel, at the panel's own 128×32 |
| **SW1 / SW2** | 3- and 5-position slide switches, current position lit |
| **Encoder** | value, running total, error count |
| **Buttons A/B** | the eight PCF8574 buttons |
| **D-pad** | UP DWN LFT RHT MID SET and the encoder click |
| **Console** | the firmware's command letters, plus anything typed by hand |

In emulator mode every lamp, switch position and encoder button is tappable —
tap one and the demo motion stops so you're driving it.

## Building it yourself

Open `android/` in Android Studio and press Run, or:

```sh
cd android && gradle assembleDebug
```

No dependencies: framework widgets only, no AndroidX, nothing to resolve beyond
the Android Gradle Plugin. `minSdk` is 26, `compileSdk` 34.

## The parts

| File | Android? | What |
|---|---|---|
| `Protocol.java` | no | the wire format: report lines, `!FB` frames, base64 |
| `Screen.java` | no | the 1-bit frame buffer and the 5×7 font |
| `FakePanel.java` | no | the emulated board |
| `UsbCdc.java` | yes | CDC-ACM over Android's USB host API, no library |
| `OledView.java` | yes | draws the frame buffer |
| `MainActivity.java` | yes | the screen, built in code |

The first three have no `android.*` imports on purpose, so they compile and can
be tested on a plain JVM.

## Notes from the wire

**DTR is not optional.** Until `SET_CONTROL_LINE_STATE` raises it, the board's
USB stack treats the port as closed and sends nothing at all. A silent panel
with a healthy connection is almost always this.

**`SW2`/`SW3` are the firmware's names.** The board reports the 3-position
switch as `SW2` and the 5-position as `SW3`, after the objects in the sketch.
Like `panel.py`, the app shows them as SW1 and SW2 — the names on the board —
and translates in exactly one place, in `Protocol.parse()`.

**The phone has no game telemetry.** The sims run on the PC, so the GAME page
stays idle here unless you feed it by hand: send a line like
`$src=DEMO;spd=88;rpm=1450;gear=4` from the console and the page comes to life.
