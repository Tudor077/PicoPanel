# panel.py - the desktop app for PicoPanel

Both directions travel over the same serial link:

| Direction | What goes across |
|---|---|
| Pico -> PC | telemetry, ~5 times a second: buttons, switches, encoder, page, HID state, OLED state, plus the screen's frame buffer while the mirror is on |
| PC -> Pico | game telemetry at 60 Hz, the audio target and what's playing twice a second, plus console commands from the buttons or typed by hand |

## One copy at a time

The serial port is exclusive, so a second copy can do nothing but sit there
failing to open it - which is exactly what it did, printing "access is denied"
in a log that gave no hint the cause was another PicoPanel rather than a broken
cable. Starting it again now raises the copy that is already running. A named
mutex, held by the kernel, so a crash cannot leave it behind the way a lock file
can.

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

## The look

One palette, in `theme.py`, for the whole window. The page list was dark with
cards and a warm accent while everything around it was whatever ttk does by
default on Windows, which is grey - and two designs in one window is worse than
either. The ttk theme is `clam`, because it is the only one that lets you colour
borders and troughs; vista and xpnative draw themselves from Windows and ignore
most of what you tell them.

Tk's own widgets - the log, the alarm list, every canvas - take no theme at all,
so they are handed the same colours by name. That is why there is a palette dict
rather than colours scattered through five files.

**Export settings** and **Import** are next to the options: the whole file -
pages, order, layouts and their faces, names, which pages stay lit, which hold
the gamepad, alarms - somewhere you can find it. Import asks first and then
restarts the app, because half of it is read at startup.

Checked rather than claimed: the export is driven with the file chooser
answered for it and the result read back, and all twenty keys come out
unchanged. Keep one before a reinstall and the panel comes back exactly as it
was.

## What you see

- lamps for the eight expander buttons (A1..A4, B1..B4) and for the d-pad
- the position of both slide switches, under the names on the board: SW1 = 3
  positions, SW2 = 5 positions
- the encoder's value as a bar, plus the total and the error count. **ENC** is
  what the knob is set to: 0 to 100, clamped at both ends, 50 at power-up, and
  what the volume and the d-pad's UP/DN move. **TOT** is every detent since the
  last reset, signed and unbounded - turn it a full revolution forward and back
  and ENC returns to where it was while TOT went 20 up and 20 down. TOT is what
  the drawn knob's angle comes from, because it never runs out of travel
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

### Silence is not an answer

The strips are only sent when the words change, which is right - each is a
couple of hundred bytes. But the board used to decide karaoke had gone away
when none had arrived for eight seconds, and a long instrumental sends nothing
at all: the page announced that karaoke was off over a song that was playing
perfectly well.

You cannot tell "nothing more is coming" from "nothing came". So the PC now
says which, on the line it sends every heartbeat anyway:

| | |
|---|---|
| `ka=0` | karaoke is switched off |
| `ka=1` | there are words - draw them |
| `ka=2` | this track has no synced lyrics |
| `ka=3` | still asking lrclib |

Each of the last three has its own message on the page, instead of one that
guessed.

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

## The Settings tab

What the board is doing right now stays on the Panel tab; what you want it to do
lives here. They were mixed together in one column of buttons, which is fine for
five things and useless for twelve.

**Pages on the panel.** USER walks a LIST, not a count, and this is the list -
as cards, each showing a picture of that page. A word is a poor way to choose
between fourteen screens.

The pictures are real: **Refresh pictures** walks every page, photographs it
through the board's mirror and puts the panel back where it was. About three
seconds for all fourteen. A mock-up would be a second drawing of every page, and
the moment the firmware changed one it would start lying.

Drag a card by its handle to reorder: the card comes out of the list and
follows the pointer, and a gap opens where it will land. It used to jump to the
new position the moment the pointer crossed a card - correct, and it felt like
the list was arguing with you rather than being held. Near the top or bottom
edge the list scrolls to meet you.

Clicking a card shows that page on the panel. Double-clicking one of **your
own** opens its editor in a window.

GAME and MUSIC have faces of their own - the sub-pages USER walks without
leaving the page - and those are shown indented underneath, with their own
pictures. How many there are is the board's to know, not the app's: GAME's
count depends on what is sending, because a tank has less to show than an
airliner. It says so in `!PAGE <page> <face> <faces>`.

The walk waits for the board to say it has arrived, and then for two whole
frames, rather than trusting a fixed delay. With a 130 ms delay, 7 of 15
pictures were of the previous page - measured - and the cards showed MUSIC
under GAME convincingly enough that nobody would have questioned them. A page
that does not answer in two seconds gets no picture at all, which is the honest
answer.

Arrangements can be named and kept: **Preset** saves the list you have, and
brings it back later. They live in the app's settings, not on the board - the
board only ever hears the one list currently in force.

### Pages of your own, as many as you make

**+ New page** makes one and opens its editor; the **+** on a card makes one
right below that card, where you were looking. **✕** throws one away, and asks
first if there is anything on it. They are pages like any other: they sit in the
rotation, they can be dragged about, they can be left out.

Eight is where it stops, because each one is a whole 512-byte frame of the
board's RAM. Only the ones you have made appear in the list - an empty `MINE 6`
nobody asked for is clutter, not a feature.

**The page stays when the app closes.** The picture only arrives while the app
is running, and the board used to blank it two and a half seconds later - so
the page existed only while something was watching it. The last frame sent now
stays on the panel until the board is unplugged.

**And across an unplug too, now** - which took two goes. Writing a frame into
the board's flash means parking the core that draws the screen, because nothing
may execute from flash while it is being erased. The first attempt used
`rp2040.idleOtherCore()`, which pushes a word into core1's FIFO and waits for
its interrupt handler to answer. It never answered: the write never finished,
the board went deaf on USB, would not take the 1200-baud reset, and had to be
recovered with BOOTSEL.

The second attempt does not ask anyone else's code to park core1. core0 raises a
flag; core1 sees it at the top of its own loop and goes and spins in a function
that lives in RAM, with interrupts off; only once it has said it has arrived
does anything get erased. Both ends give up rather than wait forever - if core1
has not parked in half a second the write is abandoned, and the worst case is a
save that did not happen instead of a board you cannot talk to.

That was proved on a sketch of its own before it went anywhere near the
firmware: fifty writes of both sectors, fifty verified, none abandoned, none
read back wrong, 50 ms of frozen screen each.

Each has its own layout, kept by the app. The board is sent the finished picture
for whichever one it is showing - the page works with every editor window
closed. A page you never look at is not worth three presses to get past.

The app also sends a picture for **every** page once on connect and once on the
way out, which is what makes the saved ones worth having: frames normally go
only to the page that is up, so a page you drew and never walked to had nothing
for the board to keep, and came back from a cold boot saying "no layout yet"
about a layout that exists.

**Rev counter: a bar or a needle.** The needle is a real dial - ticks rather than
a drawn arc, because an arc that size comes out as a smudge of stair-stepped
pixels and ticks give you a scale for the same cost. Six of them, not eleven:
eleven fit geometrically and came out as a ball of dots, and a scale you cannot
count is worse than none. The ones past the redline are longer, which is the
only way left to mark the danger end when there is no colour. Over the limiter
the needle blinks. The `km/h` label goes when the dial is on - a big number next
to a rev counter is the speed, and the dial needs the room.

**I2C speed, which is as close to vsync as this gets.** There is no vsync on
these modules: they bring out SDA and SCL and nothing else, no tearing-effect
line. What you can do is spend less time writing the frame, because the tear is
the panel showing a buffer that is half old and half new. Measured on this
board:

| | |
|---|---|
| 400 kHz | 18.1 ms a frame |
| 1 MHz | 9.0 ms a frame |

1 MHz is past what the SSD1306 promises, which is why it is a setting and not
the default: if yours dislikes it the screen fills with rubbish and you come
back here and set it down.

### Faces of your own

GAME and MUSIC have had faces from the start: USER walks them without leaving
the page, because they are one subject seen more than one way. There was never
a reason that should belong to the two pages the board happens to draw itself.

The **↳+** chip on a card adds a face, up to four. Each is its own layout in its
own editor window, and they are walked with USER exactly like GAME's - press
past the last one and you leave the page. The header counts faces instead of
the rotation while a page has more than one, which is what the board does on its
own two and for the same reason: while you are walking the faces of one page,
which face you are on is the thing you cannot otherwise tell. The **✕** on a
face row throws it away, and the ones after it shuffle down - leaving a hole
would mean "face 2 of 2" being the third one.

Four is where it stops. Every face is a whole 512-byte frame the board holds:
8 x 4 is 16 KB of its RAM and the same again in the bank it saves to, and that
bank is what the save costs in frozen screen - 102 ms, measured, up from 45.

Face 0 is filed under the bare slot number it has always had, so a settings file
written before any of this reads back with every layout exactly where it was.

### Pages that hold the gamepad

The GAME page has always done this: with HID armed, the USER button walks the
game's own sub-pages and will not leave, and to get out you disarm. Those are
the pages you look at while driving, and a press mid-corner should not cost you
the one you were reading.

It is now a property of any page rather than of one. The **HID** chip on a
card holds that page the same way; the panel says `LOCK` where it normally says
`HID`, so you can see which state you are in without remembering. Your own
pages say it too - their header is drawn by the app, so the app puts the same
two words on it.

Disarming is the way out and deliberately the only one. A second way out is a
way to leave by accident, which is the thing being prevented.

The list is kept on the board with the rest, so a panel on a charger still
holds the pages you said to hold - see below. `%hl=1,3` sets it, `HL=` in the
status line says what it is, and `%up` presses USER from the PC, which is how
any of this was checked at all: the menu is the one part of the board you
cannot otherwise reach over the wire.

### The double press that put back a page it never moved

Two quick presses on USER toggle the media layer. The first of them has already
done something by the time the second arrives, so the second undoes it - and it
undid the wrong thing. It always called `pageStep(-1)`, assuming the first press
had stepped a page.

Often it had not. In game with HID armed the first press walks the GAME page's
sub-pages; on MUSIC it walks the words; on a page that holds HID it does nothing
at all. In every one of those the undo took a page off as well, so two quick
presses on a sub-page left you a page *back* from where you started instead of
on the same sub-page with the layer flipped.

It now puts back what was actually there - page, slot and both sub-page numbers,
noted before the first press does anything - rather than guessing what moved.
That covers all four branches and the next one too.

### When it disappears on you

Two separate things, and only one of them is settled.

**The loop that ran the interface could be stopped by a single exception.** It
rescheduled itself at the end of its own body, so anything that escaped ended
it: the window stayed on screen and went dead - no frames, no telemetry, no
tray updates - and the only way back was to start the program again. It now
catches whatever comes out of one turn, writes it down, and takes the next
turn. Being wrong for a fiftieth of a second is a far smaller thing than being
dead until somebody notices.

**And Windows has recorded eleven access violations inside Tcl or Tk in a
month**, which is the C library going down and taking the process with it -
nothing Python can catch, and nothing written anywhere. That one is NOT fixed,
because it has not been reproduced: thirty-one open-and-close cycles, ninety
seconds of hunting for a Tk call from the wrong thread (there are none), and
two thousand image allocations watched to see which thread frees them (all the
right one) produced nothing.

What did come out of it: everything the program throws now lands in
`crash.log`, next to the settings, and `faulthandler` is armed, so an access
violation writes the Python stack of every thread before the process dies. The
next one will say what it was.

The mirror also stopped building two whole Tk images per frame and throwing
both away - forty a second, for a picture that changes in place perfectly well.
It is two images now, made once and written into. That is worth doing on its
own, and it happens to be the heaviest thing in the program that only runs
while the window is open, which is the description of the fault.

## What it costs while you are not looking

Measured on this machine, the app sitting in the tray with the board connected:
**5.5% of a core down to 1.8%**, and the memory it churns through in eight
seconds from 4.6 MB to 0.3.

Two things were running for nobody. The media session was read four times a
second - a cross-process call each time - to feed a page the panel was not on;
it now idles at one read every three seconds unless MUSIC is up, a page of
yours shows the track, or the knob has been touched in the last fifteen
seconds. And the volume line costs 15 ms to build, because it asks every
application in the mixer where its slider is; that went from twice a second,
for ever, to twice a second on the pages that actually show it and once every
three elsewhere.

## The editor: a page you lay out yourself

The board has a 5x7 font and about eight shapes. The PC has every font Windows
ships and a drawing library - so the widgets live there, and what crosses the
wire is the finished picture: 512 bytes in the exact layout of the panel's own
memory, which makes the board's side of it a `memcpy`.

**Alarms are not a widget.** A widget lives on a page, and an alarm you only
see if you happen to be on the right page is not an alarm. The app keeps the
clock and the list; at the minute, the board is told to flash - `%fl=8000,GET
UP` - and it inverts the whole screen twice a second with the words in the
middle, over whatever page is up, waking the panel first if it had gone to
sleep. It is a whole screen, not a box over the page: `ALARM` across the middle with
the name underneath, flipping black-on-white and white-on-black every 400 ms.
An alarm is not a footnote to whatever you happened to be looking at.

Set them in **Settings -> Alarms**; **Test** fires one now.

### What the panel remembers by itself

The rotation and the order, which pages stay lit, which hold the gamepad, the
two switches that change how things look, the alarms, and a picture for each of
your own pages. All of it
in the 64 KB the board reserves for a filesystem it does not have - two banks of
two sectors written alternately, so a power cut in the middle of a write costs
you that write and not the lot, and the newer of the two wins on the way back
in.

It is written when a setting settles (three seconds after the last change, so
dragging pages about is one write and not a dozen), when the app asks with
`%sv`, and when the app goes quiet for three seconds - which is the moment that
actually matters, because that is you closing it. Never on a timer, and never
when nothing differs from what is already there: a page with a clock on it sends
a new picture every second, and none of those is a reason to erase flash.

Verified on the hardware: the app killed, `[store] saved 9 pages in 45 ms` three
seconds later, then a power cycle with nothing on the PC talking to the board -
and it came back with all nine pages in order and all three of the drawn ones
showing what was drawn on them.

### What the board knows about time

The app used to be the only thing that knew when your alarm was: it kept the
clock and the list, and told the board to flash at the moment. Close it, or let
the PC go to sleep, and nothing happened at all.

The board holds the list now, and a clock - `%al=450,GET UP|495,TEA` in minutes
since midnight, and `%tm=<seconds since local midnight>`, sent on every connect
and again every five minutes because the board counts off its own crystal.
It rings by itself. Verified with the app shut and the port closed: alarm set
for 22:47, `[alarm] ONBOARD` on the wire at 22:47:01.

The list survives an unplug; the clock does not. There is no battery behind it,
so a panel that has just been plugged in holds your alarms but does not know
what time it is, and cannot ring until something tells it - which the app does
within a second of starting, and the whole of `%tm` is one number.

Verified: an alarm handed over and written down, the board power-cycled, and
then told nothing but the time. It rang at 20:21:00, on the minute, from its
own flash.

While the app is running its countdown wins, because it knows about the phone's
alarms as well as these; when it has been quiet for fifteen seconds the board
counts down to its own.

### The phone's alarms

Your alarms are on your phone. That is what actually wakes you, so the panel is
told about them rather than asking you to type them in twice - and two sources
for the same morning is the point, not an accident.

Tick **Take alarms from the phone** and the app listens on port 8787. It shows
the address; open it on the phone and there is a page you can set an alarm from,
or point something at it:

```
POST /alarm    {"at_ms": 1774500000000, "text": "Work"}
POST /alarm    {"in_s": 3600, "text": "Tea"}
DELETE /alarm
GET  /next     -> {"in_s": 5400, "text": "Work"}
```

The Pocket app on the phone does this by itself: **SEND MY ALARMS**, with the
address in the box. It keeps relaying with the app closed, which is the whole
point - the panel is on the desk and the phone is in a pocket. That costs a
notification, and there is no way around it: Android tells any app when the
next alarm changes, but since Android 8 only one that is already running, and
an app with nothing running cannot be told anything. It also pushes every
fifteen minutes in case a broadcast was missed, and starts itself again after
a reboot. Android hands out the next alarm through
`AlarmManager.getNextAlarmClock()` - no permission, no notification access - so
what leaves the phone is one timestamp and the name of the app that set it.

Off by default, and it binds to the LAN: there is no account and no password,
and the worst anybody on your wifi can do with it is flash the panel on your
desk. The soonest of the two lists wins, whichever it came from.

**A changed header arrives the way the panel's own does: down from the top.**
Rename a page, reorder it, have an alarm turn up - anything up there changing
and the whole bar slides in again, the same nine pixels the board slides its
own header, at the same speed. The bar, the name, the counter and the countdown
together, because they are one thing.

Renaming also wakes the panel. It had to: the board's header slides away after
twenty idle seconds, and while you are typing at the PC the board has been idle
the whole time - so the new title was arriving behind a bar that was not on
screen, which looks exactly like nothing happening. `%wk` is one command that
does nothing but say somebody is doing something meant to be seen.

The settings column scrolls, like the page list beside it - Screen, Options and
Alarms stacked are taller than the window at its smallest, and Alarms is the one
that fell off the bottom. The wheel works anywhere over it: Tk hands the wheel
to the innermost widget under the pointer, and a column of settings is a hundred
of them, so it is bound to all of them.

**Settings -> Screen -> How things appear** picks the style: *classic* down from
the top, *wipe* in from the left like the splash, *type* a letter at a time, or
*none*. Classic is the default because it is what the rest of the panel already
does. The countdown's own ticking never starts it over - a header that played
its arrival every sixty seconds would be a fidget.

**Each page has a name and a header.** The board calls the slot `MINE 3` and
always will - the name lives in the picture the app sends, so it is the app's
to keep. With the header on, the page wears the same bar the board's own pages
wear: the name on the left, where the page sits in the rotation on the right,
in the same nine rows. It is drawn in Tahoma 9, which is what the panel's
Cyrillic titles already use, so it sits beside the board's own text without
looking like a different machine.

It also *retracts* like the board's own: when the panel goes quiet the header
slides up and away, and comes back when you touch something. The board says
which of the two it is in the fourth number of `!PAGE`, because a page the PC
draws covers the whole screen - without that, yours would be the only header on
the panel that never went away.

The nine pixels in between are the app's to walk. Copying the board's reported
position meant copying it over a wire while it was already moving, at fifteen
frames a second - a quarter-second slide in three jerks. The board's number is
taken as a state, here or gone, and the bar walks between them at the board's
own rate, a pixel every 25 ms, with the page sent at fifty frames a second
while it moves and fifteen while it sits. Measured: every one of the nine
positions is drawn, both ways.

**The grid is the editor's, not the panel's.** Eight pixels, because that is
the band the display's memory is organised in and what every drawn row lines up
with anyway.

The middle of the screen is marked, and the line lights when the selected
widget's own middle lands on it - to the pixel, per axis, so being centred
across but not down is something you can see. It only tells you: nothing snaps.
A first version pulled the widget onto the centre from two pixels away, which
is a deadzone by another name - you could not put something one pixel off
centre if you wanted to.

**The box is where the ink is.** PIL puts text down from the origin with the
font's own bearing, so a line of letters starts a pixel or two right of and
below where it was asked for - and a hitbox measured from the origin sat that
far out of true, which is exactly how it felt. The editor now measures the ink,
offset and all, and both the outline and what you can grab sit on it.

**Size is not always yours to set.** Text is as wide as the text, so `w` and
`h` were two numbers you could turn all day for nothing on a Number or a Text.
They are not offered for those kinds now, and the box the editor picks up and
outlines is measured from what was actually drawn.

**Only what the kind can do.** The field list is per widget: a Lamp cannot be
pointed at the clock, a Bar cannot be pointed at a heading (there is no full
scale to fill towards), a Frame reads nothing at all. The size and the caption
disappear for the kinds that ignore them, rather than sitting there greyed out
inviting you to turn them and wonder what broke.

**Bars fill three ways.** Solid; the song bar's *faded track*, where the whole
length is dithered and the played part solid; and the rev counter's *fade in*,
thin at the start and solid by the end. The same 4x4 ordered dither the
firmware uses, the same sixteen numbers - a bar drawn here sits next to one
drawn there without shimmering.

The shelf runs down the side, one picture per widget. Pick one up and it follows
the pointer - a small borderless window with the icon in it - and drops where
you let go. Tk has no drag and drop of its own, and a ghost you can see is the
difference between dragging and clicking and hoping. A plain click, with no
drag at all, drops the widget in the middle of the page - one way that cannot
miss. The preview is not a drawing of what the
panel will show, it IS what the panel will show - `widgets.render` produces the
image in the editor and the bytes that go down the wire, so the two cannot
disagree. Checked by rendering a page here, sending it, reading the panel back
through the mirror and comparing: identical, byte for byte.

| Widget | |
|---|---|
| Number | a number, as big as you like |
| Text | a line of text, or a field that is text |
| Bar / Column | fills left to right, or standing up |
| Needle | a dial, ticks rather than an arc |
| Lamp | a dot that lights while the field is not zero - brake, HID, playing |
| Frame / Line | for dividing things up |
| Axis | two axes at once, with the centre line lit when you are dead on it |
| Button | one button of the panel, by name, lit while it is held |
| Button row | all eight expander buttons, as the PANEL page has them |
| Knob | the encoder, as a knob with a mark where the real shaft is |
| Record | the disc from MUSIC, turning while something plays |
| Switch | a slide switch, with the position it is in filled |
| Alarm in | how long until the next alarm - and nothing at all when there is none |

**Alarm in** is the one widget that hides itself. With nothing pending it draws
no pixels at all, so the page simply does not have it; in the editor it always
shows, because a widget you cannot see is a widget you cannot place.

You do not need it, though: **the countdown is in the header of every page
already**, a bell and the time left at the top right. It stays when the header
retracts, in white on the page instead of black on the bar - twenty idle
seconds is exactly when you want to know how long you have. The board draws it
for its own pages and counts the seconds off itself between updates; the app
draws the same thing, in the same shape, for yours. With HID armed and an alarm
pending there is not room for all three things up there, and the page's name is
the one that gets cut: it is the page you are looking at, and the app says so
as well.

The shelf says what each one is in a line, when the pointer is over it. That is
there because "Lamp" told nobody anything.

**Button** and **Switch** pick one of a list rather than a field - `A3` is not
telemetry, it is a choice - so they get a **Which** dropdown instead of
**Shows**.

**The clock has a shape.** 24-hour, 12-hour, seconds, the date, the weekday, or
the date and time together.

**Knob**, pointed at the running total, turns as the real one turns: this
panel's encoder has 20 detents in a revolution, so each is 18 degrees, and a
quarter turn of yours is a quarter turn of the drawing. That number is a
property of your encoder and not of this program, so it is a setting on the
widget. Pointed at the *value* instead - or at a fuel gauge, or a track
position - it sweeps 300 degrees between the ends, because those have ends and
a full circle does not.

The board's own ENCODER page draws the same knob, for the same reason: a number
climbing from 0 to 100 answers "how far have I turned it", which is a different
question from "where is it".

**Axis** is the one widget that reads two fields - it grows a second row in
the properties, for the up/down axis.

There is no deadzone: the tolerance is one pixel, the smallest this screen has.
Each axis answers for itself - the vertical line lights when X is dead centre,
the horizontal when Y is - so being centred in one and not the other is a thing
you can see, rather than a box you are somewhere inside. Both, and it is a full
crosshair. A first version had a deadzone box that filled, which made a wheel
look centred when it was nowhere near.

The panel's own state is all there too - the buttons, both switches, the
encoder, the frame rate, whether HID is armed - because the board reports every
bit of it five times a second anyway. So are the track's position, length and
whether it is playing, which is what the board's own MUSIC page is built out
of: a Bar pointed at **How far through %** with the *faded track* fill IS the
song bar.

The axes come from `sticks.py` - Windows' own joystick API through ctypes, no
dependency - as -1..+1 with zero in the middle. Which stick? The one that moved
last. There are usually two here, because the panel is itself a gamepad, and a
settings box asking which is which would be a question with no good answer:
wiggle the thing you mean. A poll of both devices measured 4 microseconds.

Each one in the palette shows a picture of ITSELF, drawn by the widget's own
code with made-up numbers - a bar half full, a needle pointing somewhere. Not a
hand-drawn glyph: an icon drawn separately can come to mean something the widget
no longer does, and this way a new widget gets an icon for free.

Adding one is a function and a line in `PALETTE`; nothing else in the program
has to hear about it. The screen is 128x32, though, and that is the real
constraint - three or four things fit, and a "gigantic library" would mostly be
widgets with nowhere to go.

Frames are sent only while the board is showing that page. It says so itself:
`!PAGE n` whenever the page changes. Guessing from the status line would not
work - that only goes out when reporting is switched on - and streaming ten
kilobytes a second at a page nobody is looking at would be silly.

## Looking after the screen

An OLED wears out the pixels that are lit, and this panel draws the same header
in the same place for hours. Three things guard against it.

**The picture moves, on the GAME page.** Every two minutes the whole frame
shifts by a pixel, round a three-by-three grid, the way a television shifts its
logo. It is done to the finished frame rather than to the drawing code - every
page would otherwise have to know about it, and one that forgot would sit still
while the rest moved. The pixel that falls off an edge is always the end of the
header bar or blank space, never anything you read.

Only on that page, because it is the only one held lit for hours; everywhere
else the panel goes dark after twenty idle seconds and there is nothing to burn.
A picture that twitches by a pixel while you are reading it just looks broken.
The cycle keeps turning either way, so coming back to the game page does not
find it parked where you left it.

**It sleeps unless you are looking.** Telemetry arriving used to hold the panel
lit whatever page was showing, so a game running in the background kept the
button test on screen for hours. Now that only applies while the GAME page is
actually up. The karaoke page is the other exception, and for the opposite
reason: it changes by itself, so there is something to watch.

**The boot screen fades out.** The name types itself a letter at a time, the
board underneath arrives in one piece, and the whole thing dissolves left to
right - a band of dither sweeping across, since on one bit per pixel that is the
only fade there is.

### Core1 waits to be told

The boot screen is drawn by core0, at the end of `setup()`. Core1 used to sleep
1500 ms and assume core0 was finished by then. It was not: `setup()` waits up to
2500 ms for the serial port alone, and then scans the bus and draws. Measured on
this board:

```
splash ran          2581 -> 4166 ms
setup() finished           4672 ms
core1's first frame        4673 ms
```

The old number would have had core1 drawing from 1500 ms - a second before the
boot screen even started - and then a hundred frames straight through it. Two
cores driving the same display, two I2C transactions interleaved, and what the
panel showed was rubbish while the mirror looked perfect, because core1's own
buffer was never the problem.

The single-frame splash it replaced got away with it. A number that happens to
be big enough is not a synchronisation, so core1 now waits for a flag.

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
