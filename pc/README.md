# panel.py - the desktop app for PicoPanel

Both directions travel over the same serial link:

| Direction | What goes across |
|---|---|
| Pico -> PC | telemetry, ~5 times a second: buttons, switches, encoder, page, HID state, OLED state, plus the screen's frame buffer while the mirror is on |
| PC -> Pico | game telemetry at 60 Hz, the audio target and what's playing twice a second, plus console commands from the buttons or typed by hand |

## Starting it

```
python panel.py
```

The board finds itself by USB VID `0x2E8A`, so it doesn't matter which COM port
it landed on, and it moves on every reflash anyway.

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
- **Mirror the OLED**: the panel's own screen, live, scaled 4x. The board
  sends the frame buffer itself, so this can't drift out of step with what the
  panel actually shows.

At the bottom is the raw log: everything that isn't telemetry (replies to
commands, startup messages) lands there. What you send appears with `>>>`.

## Per-application volume

The knob can turn Spotify down without touching anything else:

| Control | What it does |
|---|---|
| the knob | louder / quieter, **the selected app only** |
| `B1` / `B2` | previous / next app |
| `B3` | mute that app |
| `B4` | back to `Windows`, the master volume |

**No arming needed.** None of this is HID - the board only asks the app, over
the serial link it already uses - so it is gated on the PAGE instead: you get it
while you are looking at it, and never while you are only playing. Two pages
qualify:

* the **MUSIC** page, and
* the **last sub-page of GAME**, which exists so that with HID armed in a game,
  where USER walks the game's own sub-pages and nothing else, the volume is
  still one button away.

Anywhere else the buttons and the knob go back to being whatever they normally
are, and the media layer's `^B1..^B4` still work as before.

The selected app and its level show on both those pages and on the HID page, and
in the status line as `AU=Spotify:68`.

`Spotify 5/6` means Spotify is the fifth of six things the knob can point at.
The list is `Windows` - the master - followed by every application that
currently has a channel, in alphabetical order. The number is there so you know
how far round the loop you are.

**You usually don't have to select anything.** Until you press one of those
buttons, the knob follows whatever is playing - open Spotify and the knob is on
Spotify. The moment you do pick one, the choice is yours and is remembered
across restarts, in `%LOCALAPPDATA%\PicoPanel\settings.json`.

### Why not the app's own volume slider

Because Windows will not let anyone do it, and the ways round it are worse than
the problem.

What this moves is the application's channel in Windows' mixer, which IS a
per-application volume: measured here, setting Spotify to 45% left Windows,
BeamNG, Discord, Steam and the browser all at 100. Nothing else moved. The only
thing it does not do is drag the slider inside Spotify's own window.

Moving that slider would mean one of:

* **Keystrokes to the app.** Spotify and every browser are Chromium windows
  (`Chrome_WidgetWin_1`), and Chromium ignores posted key messages - it wants
  real input. Which means focusing the window first. A volume knob that pulls
  you out of the game to press a key is not a volume knob.
* **The Spotify Web API.** `PUT /v1/me/player/volume` does move the real slider,
  on whichever device is playing. It needs an app registered with Spotify, an
  OAuth login, and a Premium account. Worth doing if you want it - it is just a
  bigger thing than a mixer call.

For YouTube there is no equivalent at all: the player takes arrow keys, and only
when the tab has focus.

### What "YouTube" means here

Windows has no volume for a web site. It has one mixer channel per *process* -
the list the volume mixer shows - so what actually moves is the browser's
channel. The app labels that channel `YouTube` only when a window of that
browser says YouTube, and otherwise leaves it under the browser's own name, so
the panel never claims to control something it isn't.

The consequence worth knowing: two tabs in the same browser share one channel.
YouTube and a Twitch stream in the same window move together.

### Without the app running

Nothing is lost. The board notices it has had no answer for four seconds and
the knob goes back to the ordinary media keys, which is what that layer did
before any of this existed.

Needs `pycaw` (which pulls in `comtypes`). `psutil` and `pywin32` are what read
the window titles; without them the browser keeps its own name instead of
becoming `YouTube`.

## The MUSIC page

A page of its own for what's playing, in two layouts, because two different
things are being shown.

**From a music player** - a record that turns while the track plays and stops
when you pause it, the title, the artist, and the progress bar. **From a
browser** - no record and no artist, because a video has neither: the title
across the full width, the time as `1:37 / 2:40`, and the bar.

A title too long for the space slides right to left and repeats, after standing
still for a moment so you can read the beginning.

The bar is the whole track dithered faintly with the played part solid, rather
than an outlined box - an outline would eat two of the three pixels it has.

### Where the numbers come from

Windows' own media sessions, the same ones behind the volume overlay, so any
player that puts controls on the lock screen works without being taught about.

One wrinkle worth recording: a session reports its position only when something
happens to it, and stamps the report with the time. Read that naively and the
bar jumps every few seconds and sits still in between. The app therefore sends
the reported position **plus how old that report is**, and the board runs the
clock forward itself - which is also why the bar keeps moving smoothly at 40
frames a second off two updates a second.

Needs `winrt-Windows.Media.Control`. Without it the page says the app isn't
running; everything else carries on.

## Sharing OutGauge with CorsaConnect

OutGauge has exactly one listener, and
[CorsaConnect](https://github.com/Tudor077/CorsaConnect) wants UDP 4444 too.
Tick **Leave OutGauge to CorsaConnect** and we stop binding it; CorsaConnect
then sends us a copy of the enriched telemetry on 5051, which is better than the
raw packet: the redline it learned, the slide and the crash impact are already
in there.

## An implementation detail

Reading sits on its own thread and posts to the window through a `queue.Queue`.
Tkinter isn't thread-safe: any widget touched from the reader thread can hang
or corrupt the window. The thread only fills the queue; the window drains it
every 50 ms from its own loop.

## About the switch names

The firmware reports them as `SW2` and `SW3`, after the object names in the code
and the labels on the board. The app shows them as **SW1** (3 positions) and
**SW2** (5 positions), which are the names you actually use. The translation
happens in exactly one place, in `parse_telemetry()`.
