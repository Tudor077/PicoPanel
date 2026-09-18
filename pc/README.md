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
  still one button away. With no game sending anything, that page says `no game`
  and one press of USER still reaches the volume - without this the knob looked
  broken, because on the GAME page with no telemetry there was no volume
  sub-page at all and the knob stayed a gamepad button.

Anywhere else the buttons and the knob go back to being whatever they normally
are, and the media layer's `^B1..^B4` still work as before.

The selected app and its level show on both those pages and on the HID page, and
in the status line as `AU=Spotify:68`.

### When the knob "does nothing"

It is almost always because the knob is not the volume at that moment - on an
ordinary page with HID armed it is gamepad button 17/18, as it should be. The
status line says which, so there is no need to guess:

```
PG=1.0 M=0 KNOB=pad      the GAME page, no media layer: a gamepad button
PG=2.0 M=0 KNOB=vol      the MUSIC page: the volume
PG=1.1 M=0 KNOB=vol      the GAME page's volume sub-page
PG=5.0 M=1 KNOB=vol      anywhere, with the media layer latched on
```

`PG` is the page and its sub-page, `M` the latched media layer. The route that
does not depend on the page at all is the media layer: tap USER twice and the
knob is the volume wherever you are.

`Spotify 5/6` means Spotify is the fifth of six things the knob can point at.
The list is `Windows` - the master - followed by every application that
currently has a channel, in alphabetical order. The number is there so you know
how far round the loop you are.

**You usually don't have to select anything.** Until you press one of those
buttons, the knob follows whatever is playing - open Spotify and the knob is on
Spotify. The moment you do pick one, the choice is yours and is remembered
across restarts, in `%LOCALAPPDATA%\PicoPanel\settings.json`.

### Which volume actually moves

The app's channel in the Windows mixer, by default. That is a real
per-application volume - set Spotify to 45% and Windows, BeamNG, Discord and
Steam all stay at 100 - and it never reaches into another program's window.

**Move the app's own volume slider** switches to the slider inside the app
instead, where it has one, and the panel then says `app` after the level. It
works and it is verified; it is off by default because it is the more
complicated of the two and the simple one is enough.

How: Chromium publishes an accessibility tree, and Spotify's slider is in it as
`Change volume` with a RangeValue pattern. Setting it needs no focus change and
no token, and takes 3-40 ms. Two other routes were considered and rejected -
posted keystrokes, which Chromium ignores unless you focus the window, and the
Spotify Web API, which works but wants a registered app, an OAuth login and
Premium.

Two things fell out of measuring it:

* **Spotify snaps to ten steps.** Ask for 25% and it sets 30. So a detent moves
  by 10 there, not by 4, and the level is tracked from what we asked for rather
  than read back - the app updates its reported value a beat late, and reading
  between detents made a quick spin stall.
* **Never enumerate windows through UIA.** Walking the tree for top-level
  windows asks every application on the desktop to describe itself and waits
  for the slow ones: 7.2 seconds, against 12 ms for the same list out of
  `EnumWindows`. The handles come from `EnumWindows` and go into
  `ElementFromHandle`, which talks to one process instead of all of them.

Anything that publishes no slider - Discord, a game, the Windows master - uses
its mixer channel, which is a genuine per-application volume in its own right:
setting Spotify to 45% that way left Windows, BeamNG, Discord and Steam all at
100.

### It must not reach into other programs while you're playing

Reaching across processes makes the other application build and keep an
accessibility tree, and doing that behind a game can throw you back to the
desktop. Two rules keep it quiet:

* **Nothing happens in the background.** The panel's heartbeat used to read the
  level over UI Automation twice a second - two cross-process calls into Spotify
  every second, for ever, keeping Chromium's accessibility tree awake behind
  whatever you were playing. Now the level is remembered, and the app is only
  asked when there is nothing remembered or when you actually turn the knob.
  Measured: 0 calls in ten idle seconds, 12 for six turns of the knob.
A second guard - refusing to touch the app while a fullscreen game was in front
- was written and then removed. It blocked the volume in exactly the place you
most want it, leaving the mixer channel sitting at 100 and moving four percent
at a time, which is nothing you can hear. That was a definite regression bought
against a cause that was only ever guessed at; the polling was the one that had
been measured.

If reaching into the app does turn out to be a problem on its own, **Move the
app's own volume slider** turns the whole path off and leaves only the mixer
channel, which never touches another program's window.

A browser only exposes the YouTube player's slider while that tab is the active
one, so YouTube usually lands on the mixer channel. It is tried first either
way.

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

### Cyrillic, and every other alphabet

The board has a 5x7 ASCII font and that is not going to change - there is no
room in 5x7 for a second alphabet, and the wire would need a code page. So for
anything that isn't plain Latin the **PC draws the line itself**, with a real
font, and sends the pixels. The board scrolls a strip exactly as it scrolls
text and never has to know what an alphabet is.

Tahoma at 9 px, antialiasing off. It was chosen by rendering the alternatives at
this size and looking: Tahoma is hinted for small sizes, which at one bit per
pixel is the whole game. Segoe UI and Arial come out blurrier, Consolas wider
for no gain.

The strip is one byte per column, bit 0 at the top - the same shape as the
panel's own memory - and base64 on the wire, so the protocol stays ASCII. A
rendered title is about 250 bytes and is sent only when the track changes, not
on every heartbeat. `Король и Шут` comes to 63 px, narrower than the 72 px its
transliteration needs in the panel font.

**Titles in their own alphabet** in the app turns it off, and then the board's
own font draws the transliteration below. Worth having: `Korol i Shut` is easier
to read if you don't read Cyrillic.

### When it falls back to Latin letters

The panel draws the GFX built-in font, which is ASCII. Anything else used to be
deleted character by character - fine for a stray typographic dash, a disaster
for a title written entirely in Cyrillic: it came out empty, and an empty title
was taken for silence. The page said "nothing playing" over a track that was
playing.

Now letters that have a Latin form get one, in `telemetry/text.py`:

| | |
|---|---|
| `Пыяла` | `Pyyala` |
| `Король и Шут` | `Korol i Shut` |
| `Незалежність` | `Nezalezhnist` |
| `Café del Mar` | `Cafe del Mar` |

Japanese, Chinese and emoji have no Latin form, so they still come out empty -
but empty no longer means silence. The page falls back to the artist's name, or
to `(untitled)`, and the disc, the time and the bar carry on working.

The same rules run over game telemetry, where they were quietly eating city
names: ETS2's `Kraków` used to reach the panel as `Krakw`.

## Karaoke: MUSIC's second sub-page

Not a page of its own. It started as one and that was one more thing to walk
past on the way round - the words and the track are the same subject. USER
alternates the two while you are on MUSIC, armed or not, and the header counts
them: `MUSIC 2/2`.

A white band sits at a fixed height and the words slide up through it: the line
being sung is black inside the band, the one coming is plain below. When the
line turns over, both climb by ten pixels over a quarter of a second - the rate
the header retracts at - and the next line arrives in the band.

The band belongs to the PAGE, not to a line. Attaching it to a line was tried
and it has to jump from one to the other at some point in the slide, and there
is no point in the slide where that doesn't flash.

Two earlier attempts are worth recording. The next line drawn at half density on
the Bayer grid was unreadable, because the progress bar's background used that
same grid - the faint text and the faint bar merged into one field of dots, and
at seven pixels tall there is no room to throw half of them away anyway. And
there is no progress bar here at all now: it was two lines of words competing
with a third thing for twenty-two rows.

Both lines are rendered here with a real font and sent as pixels, like the
titles, so a Russian song is a Russian song.

**The screen stays on** while the words are up and something is playing. Every
other page sleeps after twenty idle seconds, which is right for a page that
never changes on its own; this one changes by itself, and a karaoke display that
goes dark mid-verse is no use.

**The board decides when the line turns over.** It is handed both lines with the
millisecond each begins, and compares that against the clock it already runs for
the progress bar. Sending "the current line" twice a second instead would have
left every change up to half a second late, which on a sung line you can see.

A blank line between verses is sent as a blank, not skipped - otherwise the
panel keeps singing the last line through the instrumental.

### It is off, and this is why

This is the only part of PicoPanel that talks to the internet. Tick **Karaoke**
in the app and it asks [lrclib.net](https://lrclib.net) for the words, sending
the artist, the title and the track length - enough to identify the song and
nothing else, once per track, never per second. lrclib needs no account and no
key.

Until you tick it, nothing leaves the machine and the page says so.

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
