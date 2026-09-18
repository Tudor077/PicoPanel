/* ============================================================================
   PicoPanel - test / diagnostic firmware for the "Panou" board (YD-RP2040)
   ----------------------------------------------------------------------------
   The pinout below matches the board as it is actually wired. Change the
   wiring and this block has to change with it.

     U1  RaspberryPi_Pico footprint (the real board = YD-RP2040)
     J1  ER_OLEDM0.91_1x-I2C  -> OLED SSD1306 128x32, address 0x3C
     J2  Conn_01x04           -> 3V3 / GND / SDA / SCL (I2C brought out)
         The 8-button PCF8574 expander goes here. It isn't in the schematic;
         it's found by itself on 0x20..0x27 and is optional - everything works
         without it.
     J3  Conn_01x08           -> external buttons: SET MID RHT LFT DWN UP + GND
     SW1 RotaryEncoder_Switch -> A / B / push (C and S2 to GND)
         Alps EC11, 20 detents per turn, 2 quarter-steps per detent.
         FILTER ADDED ON THE BOARD: on each of A and B, a 1 kOhm pull-up to
         3V3 plus 100 nF to ground (tau = 100 us).
         Switch S1 has no filter and doesn't need one - it's debounced in
         software.
     SW2 SW_SP3T  (4 pins)    -> 1P3T slide switch
     SW3 SW_DP5T  (12 pins)   -> 2P5T slide switch, poles wired in parallel
     C1  3V3 decoupling

     GPIO4  SDA          GPIO16 ENC_A        GPIO19 UP
     GPIO5  SCL          GPIO17 ENC_B        GPIO20 DWN
     GPIO6..9   SW2_1..4 GPIO18 ENC_SW       GPIO21 LFT
     GPIO10..15 SW3_1..6                     GPIO26 RHT
                                             GPIO27 MID
     GPIO25 onboard blue LED                 GPIO28 SET

   CHANGE FROM THE ORIGINAL WIRING: the RST line (J3 pin 1 -> U1 pin 33 =
   AGND) is gone. It was wrong (AGND is not reset) and unused anyway. J3 pin 1
   is left free.

   HOW SW2 AND SW3 ARE READ
   EVERY switch pin goes to a GPIO - none is tied to ground,
   so they can't be read like ordinary DIP switches. The firmware scans them as
   a matrix instead: one pin at a time is driven OUTPUT LOW while the rest are
   read with internal pull-ups. The pins that fall LOW are the ones the wiper
   is shorting together.

   The common pin is found by INTERSECTION, not by statistics: it is the one pin
   present in the shorted pair whatever the position, so once the switch has
   been through two different positions exactly one candidate is left, for good.
   Until then we go with the obvious convention (pin 1 = common) and correct
   ourselves.

   The number of positions is a property of the switch: SW2 = SW_SP3T -> 3,
   SW3 = SW_DP5T -> 5 (the 'expect' field). The display shows "2/3", the current
   position out of the total, and the 'v' next to it counts how many distinct
   positions have been seen so far. A '?' after the common pin number means it
   is still a guess, not confirmed.

   NAVIGATION
     LFT / RHT ........ previous / next page
     encoder .......... demo value (0..100)
     UP / DWN ......... +10 / -10
     MID .............. rescan I2C + re-init the OLED
     SET .............. reset counters
     USER ............. tap = page; twice = media; hold 1s = HID

   DEBUG COMMANDS on the Serial Monitor (115200) - type the letter + Enter.
   Type '?' for the list. They exist for the "the screen won't work" case.

   REQUIREMENTS (Arduino IDE)
     Boards Manager : "Raspberry Pi Pico/RP2040/RP2350" by Earle PHILHOWER
                      -> Tools > Board > "VCC-GND YD RP2040"
     Tools          : USB Stack = "Pico SDK"  (the default)
     Library Manager: "Adafruit SSD1306" + "Adafruit GFX Library"

     WATCH OUT: the board list has TWO "Raspberry Pi Pico" entries. The one
     from "Arduino Mbed OS RP2040 Boards" has no Keyboard/Mouse/Joystick, so
     the diagnostics still run but HID is missing entirely and the screen tells
     you "HID unavailable (Mbed core)". HID needs Philhower. USB Stack set to
     "Adafruit TinyUSB" doesn't work on Philhower either: that core refuses
     Keyboard/Mouse/Joystick outright.
   ========================================================================== */

#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ------------------------------------------------------------------- HID ---
// USB keyboard / mouse / gamepad exist only on the Philhower core, with
// Tools > USB Stack = "Pico SDK". On the Mbed core those libraries are missing,
// so the sketch still compiles there - just without the HID part.
#if defined(ARDUINO_ARCH_MBED)
  #define HID_AVAILABLE 0
#else
  #define HID_AVAILABLE 1
  #include <Keyboard.h>
  #include <Mouse.h>
  #include <Joystick.h>
#endif

// ----------------------------------------------------------------- pinout ---
#define PIN_SDA     4
#define PIN_SCL     5

#define PIN_ENC_A   16
#define PIN_ENC_B   17
#define PIN_ENC_SW  18

#define PIN_UP      19
#define PIN_DWN     20
#define PIN_LFT     21
#define PIN_RHT     26
#define PIN_MID     27
#define PIN_SET     28

// YD-RP2040 (VCC-GND Studio) - NOT a standard Pico:
//   GPIO23 = onboard WS2812 RGB LED
//   GPIO24 = onboard USER button
//   GPIO25 = onboard blue LED  <-- output only, can't be read
// The schematic uses none of them as an input, so there's no conflict.
#define PIN_STATUS_LED 25

// The onboard USER button, next to BOOT. It isn't part of the panel's own
// wiring - it belongs to the YD-RP2040 board. We use it to change
// the page on screen, which leaves EVERY panel input free for the PC.
#define PIN_USR_KEY    24

// The onboard blue LED. Off by default: up close it's blinding. Set this to 1
// to get the signalling back (lit = HID armed, slow blink = all well, fast
// blink = no OLED found).
#define STATUS_LED_ENABLED 0

// Screen sleep. After OLED_DIM_MS with nothing touched it dims, after
// OLED_OFF_MS it goes dark. Any input wakes it. Set OLED_DIM_MS to 0 and it
// never sleeps.
#define OLED_DIM_MS        10000UL    // 10 seconds
#define OLED_OFF_MS        20000UL    // 20 seconds
#define OLED_CONTRAST_ON      0x8F    // the library's default
#define OLED_CONTRAST_DIM     0x01    // how faint once dimmed
// While game telemetry is flowing the screen never sleeps. Set this to 0 if
// you want it to sleep anyway.
#define OLED_AWAKE_ON_GAME       1
// First step of going idle: the header retracts and the content moves up into
// its place. Then comes dimming, then the panel switches off entirely.
#define OLED_RETRACT_MS     5000UL

// --------------------------------------------------------------- config ----
#define DEBOUNCE_MS       12    // button debounce
#define SW_DEBOUNCE_MS    40    // slide switch debounce
#define SCAN_SETTLE_US    60    // settling time during the matrix scan
// The encoder decoder. 4 / 2 / 1 = classic, counting quarter-steps.
//
// MEASURED on this panel's encoder, on data with ZERO errors: 2 quarters per
// detent. Detents land alternately on 11 and 00 - the stops split 47% / 47%
// between the two, and the side states 01 and 10 show up in only 2% of cases.
//
// CAREFUL when you measure: that distribution only holds with errors = 0. With
// edges being dropped the stops appear to pile onto a single state (here it
// read 70% on 11) and lead you to the wrong conclusion of 4 quarters per
// detent. Fix the signal first, interpret afterwards. The automatic 'a'
// calibration is even more misleading: it counts edges, so when edges go
// missing it simply reports fewer.
//
// 0 = DETENT MODE, for encoders with 4 quarters per detent: it doesn't count
// quarters at all, it takes the direction from the first falling edge after
// leaving rest and emits one step on the way back. Immune to dropped
// mid-transitions, but WRONG here: with 2 quarters per detent the rest state
// alternates, so a trip from rest back to the same rest means two detents.
// See encDetentStep().
//
// 'e' cycles the mode live: 0 -> 4 -> 2 -> 1 -> 0.
#define ENC_MODE_DEFAULT  2
#define ENC_BURST_MS      80    // quiet time after which a detent counts as done
                                // (well past the ~2 ms chatter of an EC11, but
                                //  short enough that two quick clicks don't get
                                //  glued into one measurement)
#define ENC_MIN           0
#define ENC_MAX           100
#define SERIAL_HZ         5     // reports per second on Serial

#define SCREEN_W       128
#define OLED_H_DEFAULT 32       // ER_OLEDM0.91 = 128x32. 'h' switches to 64.

#if defined(ARDUINO_ARCH_MBED)
  #define CORE_NAME "Mbed"
#else
  #define CORE_NAME "Philhower"
#endif

/* ============================================================================
   TYPES AND GLOBALS
   Every struct MUST be defined before the first function: the Arduino IDE
   inserts its generated prototypes at exactly that point, and those prototypes
   refer to the types below.
   ========================================================================== */

static const uint8_t SW2_PINS[] = { 6, 7, 8, 9 };
static const uint8_t SW3_PINS[] = { 10, 11, 12, 13, 14, 15 };

// -------------------------------------------------------------- HID map ----
// The types and tables live UP HERE, not next to the HID functions: the Arduino
// IDE inserts its generated prototypes right after the includes, and those
// refer to 'Action'. Declared further down, the build fails with "does not name
// a type".
// What the board can ask the PC app to do. Not HID - a line on the serial
// link the app already reads - so it works with HID disarmed.
enum { APP_UP = 1, APP_DN, APP_PREV, APP_NEXT, APP_MUTE, APP_HOME };
void audioAsk(uint8_t code);
static bool audioPage();        // defined further down, with the pages it reads

#if HID_AVAILABLE

// Consumer Control codes, from tinyusb/src/class/hid/hid.h
#define CC_PLAY_PAUSE 0x00CD
#define CC_SCAN_NEXT  0x00B5
#define CC_SCAN_PREV  0x00B6
#define CC_MUTE       0x00E2
#define CC_VOL_UP     0x00E9
#define CC_VOL_DN     0x00EA
#define CC_STOP       0x00B7

enum ActKind : uint8_t {
  AK_NONE = 0, AK_KEY, AK_COMBO, AK_CC, AK_CLICK, AK_WHEEL, AK_PAD, AK_TEXT,
  AK_APP        // per-application volume, carried out by the PC app
};

struct Action {
  uint8_t     kind;
  uint16_t    a;      // key / media code / button number / scroll steps
  uint16_t    b;      // modifier, for COMBO
  const char *str;    // the text for TEXT, otherwise a label to display
};

#define NOTHING      { AK_NONE,  0,                       0,                       "-" }
#define KEY(k)       { AK_KEY,   (uint16_t)(k),           0,                       #k }
#define COMBO(m, k)  { AK_COMBO, (uint16_t)(k),           (uint16_t)(m),           #m "+" #k }
#define MEDIA(c)     { AK_CC,    (uint16_t)(c),           0,                       #c }
#define CLICK(b)     { AK_CLICK, (uint16_t)(b),           0,                       "click " #b }
#define SCROLL(d)    { AK_WHEEL, (uint16_t)(int16_t)(d),  0,                       "scroll " #d }
#define PAD(n)       { AK_PAD,   (uint16_t)(n),           0,                       "pad " #n }
#define TEXT(s)      { AK_TEXT,  0,                       0,                       (s) }

// Windows has no "volume of YouTube" - it has one mixer channel per
// process. Only the PC can reach those, so the board just asks, and the PC
// app answers with the name and level to display. The board never learns
// which apps exist: a new one appears on the panel with no firmware change.
// The codes themselves live outside this fence: asking is not HID.
#define APP(c, lbl)  { AK_APP,   (uint16_t)(c),           0,                       lbl }

/* ------------------------------------------------------------------------ *
 *  >>>>>>>>>>>>>>>>>>>>  MAP YOUR CONTROLS HERE  <<<<<<<<<<<<<<<<<<<<<<<<<  *
 * ------------------------------------------------------------------------ */

// Default: EVERYTHING on gamepad buttons, nothing on the keyboard. Windows sees
// them in "Set up USB game controllers" as buttons 1..19 and games can bind them
// directly. They collide with nothing you type.
Action mapBtnA[4] = { PAD(1),  PAD(2),  PAD(3),  PAD(4)  };
Action mapBtnB[4] = { PAD(5),  PAD(6),  PAD(7),  PAD(8)  };

Action mapSw1[3]  = { PAD(9),  PAD(10), PAD(11) };
Action mapSw2[5]  = { PAD(12), PAD(13), PAD(14), PAD(15), PAD(16) };

Action mapEncCW    = PAD(17);
Action mapEncCCW   = PAD(18);
Action mapEncClick = PAD(19);

// LAYER TWO - while the USER button is held, the buttons and the encoder send
// these instead of the ones above. That gives you media keys without spending
// any button from the gamepad map.
Action mapShiftA[4] = { MEDIA(CC_PLAY_PAUSE), MEDIA(CC_SCAN_NEXT),
                        MEDIA(CC_SCAN_PREV),  MEDIA(CC_MUTE) };
// The media layer's four spare buttons pick WHICH app the knob turns.
Action mapShiftB[4] = { APP(APP_PREV, "app prev"), APP(APP_NEXT, "app next"),
                        APP(APP_MUTE, "app mute"), APP(APP_HOME, "app Windows") };

// The knob moves the SELECTED app, not everything at once. With the PC app
// not running there is nobody to ask, so it falls back to the plain media keys
// and behaves exactly as it did before - see actFire().
Action mapShiftEncCW    = APP(APP_UP, "app vol+");
Action mapShiftEncCCW   = APP(APP_DN, "app vol-");
Action mapShiftEncClick = MEDIA(CC_PLAY_PAUSE);

// The panel's d-pad. While a slot is NOTHING, that button stays on page
// navigation in diagnostics mode. Put something in it and it switches to HID.
//                     UP     DWN    LFT    RHT    MID    SET
Action mapPanel[6] = { NOTHING, NOTHING, NOTHING, NOTHING, NOTHING, NOTHING };

/* ------------------------------------------------------------------------ */

static const char *LBL_A[4]     = { "A1", "A2", "A3", "A4" };
static const char *LBL_B[4]     = { "B1", "B2", "B3", "B4" };
static const char *LBL_SW1[3]   = { "SW1_1", "SW1_2", "SW1_3" };
static const char *LBL_SW2[5]   = { "SW2_1", "SW2_2", "SW2_3", "SW2_4", "SW2_5" };
static const char *LBL_PANEL[6] = { "UP", "DWN", "LFT", "RHT", "MID", "SET" };
static const char *LBL_SA[4]    = { "^A1", "^A2", "^A3", "^A4" };
static const char *LBL_SB[4]    = { "^B1", "^B2", "^B3", "^B4" };


bool        hidArmed   = false;
uint32_t    hidActions = 0;
const char *hidLast    = "-";
bool        padUsed    = false;   // is there at least one PAD() in the tables?

#endif  // HID_AVAILABLE

// State of the USER button. It lives OUTSIDE the HID fence: usrUpdate() uses it
// on the Mbed core too, where everything else above doesn't exist.
/* ---- the audio target, as reported by the PC app -------------------------
   The board holds no list of applications. It shows one name, one level, and
   its place in the list; the PC owns all of it and re-sends on every change.
   If the PC goes quiet the panel says so instead of showing a stale number. */
/* ---- what's playing, also from the PC app --------------------------------
   The board is told the position ONCE and then keeps the clock itself: a media
   session only reports where it has got to when something happens to it, so a
   bar driven straight off those reports would jump every few seconds and stand
   still in between. See npNowMs(). */
/* The title as PIXELS, drawn on the PC.

   The board's font is ASCII and always will be - 5x7 leaves no room for a
   second alphabet, and the wire would need a code page. So for anything that
   isn't plain Latin the PC renders the line with a real font and sends the
   strip, and the board scrolls pixels instead of characters. Cyrillic, Greek,
   Japanese: the board never has to know.

   One byte per column, bit 0 the top row - the same shape as the panel's own
   memory. Width 0 means "nothing sent", and the ASCII text below is used. */
// The title rarely fits. Each line that might slide keeps its own place in the
// slide, so the title and the artist don't march in lockstep.
struct Marquee { uint16_t hash; uint32_t t0; };
// 0 title text, 1 artist text, 2 title strip, 3 artist strip.
static Marquee marq[4];

#define NP_BMP_MAX 250
uint8_t  npTitleBmp[NP_BMP_MAX];
uint16_t npTitleW    = 0;
uint8_t  npArtBmp[NP_BMP_MAX];
uint16_t npArtW      = 0;

char     npTitle[44]  = "";
char     npArtist[24] = "";
int32_t  npPosMs      = 0;      // as last told, in milliseconds
uint32_t npPosAt      = 0;      // millis() when we were told
int32_t  npDur        = 0;      // seconds, 0 = unknown
bool     npPlaying    = false;
char     npKind       = 's';    // 's' = a record with a disc, 'y' = a video
uint32_t npSeen       = 0;
#define NP_STALE_MS 6000UL

char     audName[14] = "";
int16_t  audVol      = -1;      // 0..100, -1 = not known
bool     audMute     = false;
// 'a' = the app's own slider is what moves, 'm' = its channel in the Windows
// mixer. Shown on screen, because the whole point was which one moves.
char     audSrc      = 'm';
uint8_t  audIdx      = 0;       // which target, 1-based for display
uint8_t  audCount    = 0;
uint32_t audSeen     = 0;
uint32_t audTouch    = 0;       // when the volume last changed, or you arrived
#define AUD_STALE_MS 4000UL
#define AUD_SHOW_MS  2500UL     // how long the MUSIC page shows it over the artist

bool usrLevel   = false;    // held down right now, after debounce
bool mediaLayer = false;    // layer two, LATCHED: stays until you toggle it back

// ---------------------------------------------------------------- buttons --
struct Button {
  uint8_t     pin;
  const char *name;
  bool        level;        // true = held (after debounce)
  bool        lastRaw;
  uint32_t    tEdge;
  bool        evPress;
  bool        evRelease;
  uint32_t    tPress;
  uint32_t    heldMs;
  uint32_t    count;
};

enum { B_UP = 0, B_DWN, B_LFT, B_RHT, B_MID, B_SET, B_SW, B_COUNT };

Button btn[] = {
  { PIN_UP,     "UP", false, false, 0, false, false, 0, 0, 0 },
  { PIN_DWN,    "DN", false, false, 0, false, false, 0, 0, 0 },
  { PIN_LFT,    "LF", false, false, 0, false, false, 0, 0, 0 },
  { PIN_RHT,    "RT", false, false, 0, false, false, 0, 0, 0 },
  { PIN_MID,    "MD", false, false, 0, false, false, 0, 0, 0 },
  { PIN_SET,    "ST", false, false, 0, false, false, 0, 0, 0 },
  { PIN_ENC_SW, "EN", false, false, 0, false, false, 0, 0, 0 },
};

// ------------------------------------- I2C button expander (PCF8574) --------
// The variant without the "A": addresses 0x20..0x27, so it doesn't clash with
// the OLED at 0x3C. (A PCF8574A would sit at 0x38..0x3F and WOULD land on top
// of the screen.)
//
// The chip's pins are quasi-bidirectional: write 0xFF once and they become
// inputs with a weak internal pull-up (~100 uA). Buttons to ground just work.
//
// Polarity is NOT assumed: at detection we remember the resting state and treat
// any bit that differs from it as pressed. Works to ground, to positive, even
// mixed. Hold a button down exactly at power-up and that one comes out inverted
// - 'r' takes the reference again.
#define PCF_SCAN_LOW   0x20
#define PCF_SCAN_HIGH  0x27
#define PCF_POLL_MS    10          // 100 Hz; a single byte read, negligible

struct PcfBtn {
  bool     level;
  bool     lastRaw;
  uint32_t tEdge;
  uint32_t count;
};

PcfBtn   pcfBtn[8];
uint8_t  pcfAddr  = 0;
bool     pcfOK    = false;
uint8_t  pcfIdle  = 0xFF;          // the resting state, taken at detection
uint8_t  pcfRaw   = 0xFF;
uint32_t pcfFails = 0;

// ------------------------------------------------ slide switches (matrix) ---
// Each switch is a group of pins with no common ground. See the explanation in
// the file header.
#define SW_MAX_PINS 8

struct SwGroup {
  const char    *name;
  const uint8_t *pins;
  uint8_t        n;                  // how many pins the group has
  uint8_t        expect;             // how many positions the switch has
  uint8_t        reverse;            // 1 = number the positions the other way
  uint8_t        row[SW_MAX_PINS];   // row[i] = mask of the pins shorted to i
  uint8_t        candMask;           // pins that could still be the common one
  uint8_t        seenThrows;         // positions seen at least once
  uint8_t        pairs;              // pairs found on the last scan
  int8_t         common;             // index of the common pin (-1 = unknown)
  int8_t         active;             // index of the pin the wiper is touching
  int8_t         pos;                // reported position (1..expect, 0 = nothing yet)
  int8_t         posCand;            // candidate, for debounce
  uint32_t       tEdge;
  uint32_t       changes;
};

// 'expect' comes from the switches themselves: SW2 = SW_SP3T (3 positions),
// SW3 = SW_DP5T (5 positions, with the two poles wired in parallel).
// candMask starts with every pin marked as a possible common.
// SW2 is mounted the other way round from its pin order, so its
// numbering is flipped: position 1 becomes 3, 3 becomes 1, 2 stays put. Set
// reverse to 0 if you change the switch's orientation on the board.
SwGroup sw2 = { "SW2", SW2_PINS, 4, 3, 1, {0}, 0x0F, 0, 0, -1, -1, 0, 0, 0, 0 };
SwGroup sw3 = { "SW3", SW3_PINS, 6, 5, 0, {0}, 0x3F, 0, 0, -1, -1, 0, 0, 0, 0 };

// Position number of a throw, accounting for the common pin and the orientation.
// Has the PC app spoken lately? Everything per-app depends on it being there.
static bool audFresh() { return audSeen && (millis() - audSeen) < AUD_STALE_MS; }
static bool npFresh()  { return npSeen  && (millis() - npSeen)  < NP_STALE_MS;  }
static bool audShowing() { return audTouch && (millis() - audTouch) < AUD_SHOW_MS; }

// Where the track has got to, right now. Runs forward from the last report
// while it's playing, and stands still when it isn't.
static int32_t npNowMs() {
  int32_t p = npPosMs;
  if (npPlaying) p += (int32_t)(millis() - npPosAt);
  if (p < 0) p = 0;
  if (npDur > 0 && p > npDur * 1000) p = npDur * 1000;
  return p;
}

int8_t swPosNumber(const SwGroup &g, int8_t throwIdx) {
  int8_t p = (throwIdx > g.common) ? throwIdx : (int8_t)(throwIdx + 1);
  if (g.reverse) p = (int8_t)(g.expect + 1 - p);
  return p;
}

// ---------------------------------------------------------------- encoder --
// Quadrature decoder on a transition table; +-1 per quarter-step.
//
// Why the quarters are NOT simply summed and divided by 4: a mechanical
// encoder's contacts chatter right at the thresholds, and the remainder of that
// division stays in the accumulator and jumps out on the next click - that's
// where "it skips two steps at once" and "it won't move one at a time" come
// from. Instead, this decoder emits a step ONLY when the encoder lands back on
// a rest position (a detent), and clears the accumulator there. The result: at
// most one step per detent, and the noise between detents disappears by itself.
static const int8_t ENC_TABLE[16] = {
   0, -1,  1,  0,
   1,  0,  0, -1,
  -1,  0,  0,  1,
   0,  1, -1,  0
};

volatile uint8_t  encPhase  = 0x03;   // last state read, (A<<1)|B
volatile int8_t   encAcc    = 0;      // quarters accumulated in this detent
volatile int32_t  encRaw    = 0;      // total quarters (calibration only)
volatile int32_t  encSteps  = 0;      // detents emitted, not yet consumed
volatile uint32_t encErrors = 0;

uint8_t encMode = ENC_MODE_DEFAULT;   // 0 = detent mode; else quarters/detent
// Detent mode (encMode == 0): the state of the current excursion, from leaving
// rest until coming back to it.
volatile int8_t  encExcDir  = 0;      // direction, from the first falling edge
volatile uint8_t encExcMask = 0;      // which states we visited (one bit each)
volatile uint32_t encGuessed = 0;     // steps where the direction was guessed
volatile int8_t encLastDir = 0;       // last direction emitted (for guessing)
bool    encGuess   = true;            // 'g' - recover antipodal jumps
uint8_t encRest = 0x03;               // the rest state, auto-detected
uint8_t encRestVotes[4] = { 0, 0, 0, 0 };  // how often it stopped in each state
bool    encRestSure = false;               // do we have a clear rest state?
int8_t  encLastBurst  = 0;            // quarters measured on the last detent
bool    encTrace      = false;        // 't' - log every detent
bool    encCalActive  = false;        // 'a' - calibration running
uint8_t encCalCount   = 0;
uint8_t encCalSizes[8];

// Raw transition log ('l'): the real waveform of one click. The ISR only fills
// the buffer; printing happens from loop().
#define ENC_LOG_N 48
volatile uint32_t encLogUs[ENC_LOG_N];
volatile uint8_t  encLogState[ENC_LOG_N];
volatile int8_t   encLogD[ENC_LOG_N];
volatile uint8_t  encLogTrig[ENC_LOG_N];   // which pin fired the interrupt
volatile uint8_t  encLogHead = 0;
volatile bool     encLogOn   = false;

// How many times each channel changed level, derived from the signal (not from
// the interrupt). In a healthy turn the two counters stay neck and neck. If one
// falls well behind, that channel is dropping edges.
volatile uint32_t encChgA = 0;
volatile uint32_t encChgB = 0;
volatile uint8_t  encTrig = '?';

// ------------------------------------------------------------------ state --
enum Page { P_OVERVIEW = 0, P_GAME, P_MUSIC, P_HID, P_SW, P_ENC, P_BTN, P_PCF, P_I2C,
            P_INFO, P_COUNT };

const char *PAGE_NAME[P_COUNT] =
  { "PANEL", "GAME", "MUSIC", "HID", "SWITCHES", "ENCODER", "BUTTONS", "PCF8574",
    "I2C", "INFO" };

uint8_t  page       = P_OVERVIEW;
int32_t  encValue   = 50;
int32_t  encTotal   = 0;
int8_t   encDir     = 0;
uint32_t tLastDet   = 0;
bool     inverted   = false;
bool     streamOn   = true;

uint8_t  i2cFound[16];
uint8_t  i2cCount  = 0;
bool     bootPuSDA = false;      // measured at boot, BEFORE Wire.begin()
bool     bootPuSCL = false;
uint32_t i2cHz     = 400000;
uint32_t frames = 0, fps = 0;
uint32_t renderUs = 0;          // how long the last frame took, microseconds
// 25 ms = 40 FPS. 16 ms (62 FPS) was tried and does NOT hold at 400 kHz: one
// frame costs ~15 ms, so the bus would be busy 15 ms out of every 16 and the
// button expander would almost never get a turn. The core isn't the limit, the
// bus is - there's one of it and both cores share it.
//
// For 62 FPS you need I2C at 1 MHz, where a frame drops to ~7 ms and 9 ms per
// period stay free. The 'c' command raises the speed; if the screen looks clean
// there, put 16 here.
uint16_t renderMs = 25;         // target period; 'f' cycles it

Adafruit_SSD1306 *oled = NULL;
uint8_t  oledH     = OLED_H_DEFAULT;
bool     oledOK    = false;
uint8_t  oledAddr  = 0x3C;
int8_t   forceAddr = -1;         // -1 = auto-detect; otherwise 0x3C / 0x3D

/* ==========================================================================
   THE I2C BUS LOCK

   Since rendering moved to the second core, two masters can want the bus at
   once: core1 pushes a frame to the screen while core0 reads the button
   expander or runs a diagnostic command. Two overlapping transfers on the same
   I2C peripheral means scrambled data and, usually, a stuck bus.

   The lock is recursive because these functions call each other (tryOledInit
   also scans), and the guard is a stack object: that way it's released on the
   mid-function returns too, of which there are many here.
   ========================================================================== */
#include <pico/mutex.h>

auto_init_recursive_mutex(i2cMux);

struct I2CGuard {
  I2CGuard()  { recursive_mutex_enter_blocking(&i2cMux); }
  ~I2CGuard() { recursive_mutex_exit(&i2cMux); }
};
#define I2C_GUARD I2CGuard _i2cGuard_

// The variant that does NOT wait. Nothing on core0 is allowed to queue up behind
// the bus: core1 holds it while it draws a frame, and a frame takes ~15 ms. If
// core0 waited there, serial, USB and HID would all wait with it - exactly the
// coupling that moving rendering to the other core was meant to break. We skip
// this round and try the next one; nothing on core0 is urgent enough to be
// worth blocking for.
struct I2CTry {
  bool ok;
  I2CTry()  { ok = recursive_mutex_try_enter(&i2cMux, NULL); }
  ~I2CTry() { if (ok) recursive_mutex_exit(&i2cMux); }
};

/* ==========================================================================
   BIT-BANGED I2C (diagnostics only)

   Why it exists: Adafruit_SSD1306::begin() does NOT check whether the screen
   answers - it sends the init sequence into thin air and always returns true.
   The only real proof the screen is there is an ACK on the bus. On top of that,
   on the Mbed core a zero-length I2C transfer (the classic way to scan) fails
   at every address, so we need a second, independent opinion.

   The lines are driven open-drain: a pin is either pulled to ground or released.
   HIGH is never forced, so this stays safe even if you land on the wrong wire.
   ========================================================================== */
static inline void bbRelease(uint8_t p) { pinMode(p, INPUT_PULLUP); }
static inline void bbPull(uint8_t p)    { pinMode(p, OUTPUT); digitalWrite(p, LOW); }
#define BB_DLY 6

bool bbProbe(uint8_t sda, uint8_t scl, uint8_t addr) {
  bbRelease(sda); bbRelease(scl); delayMicroseconds(BB_DLY);
  if (!digitalRead(sda) || !digitalRead(scl)) return false;  // bus stuck

  bbPull(sda); delayMicroseconds(BB_DLY);                    // START
  bbPull(scl); delayMicroseconds(BB_DLY);

  uint8_t b = (uint8_t)(addr << 1);                          // address + write bit
  for (uint8_t i = 0; i < 8; i++) {
    if (b & 0x80) bbRelease(sda); else bbPull(sda);
    delayMicroseconds(BB_DLY);
    bbRelease(scl); delayMicroseconds(BB_DLY);
    bbPull(scl);    delayMicroseconds(BB_DLY);
    b = (uint8_t)(b << 1);
  }

  bbRelease(sda); delayMicroseconds(BB_DLY);                 // read the ACK
  bbRelease(scl); delayMicroseconds(BB_DLY);
  bool ack = (digitalRead(sda) == LOW);
  bbPull(scl); delayMicroseconds(BB_DLY);

  bbPull(sda);    delayMicroseconds(BB_DLY);                 // STOP
  bbRelease(scl); delayMicroseconds(BB_DLY);
  bbRelease(sda); delayMicroseconds(BB_DLY);
  return ack;
}

// A powered I2C module has pull-ups on its own board. If the pin stays HIGH even
// with the internal pull-down active, there's an external pull-up - so the
// module is both connected AND powered.
bool hasExternalPullup(uint8_t p) {
  pinMode(p, INPUT_PULLDOWN);
  delayMicroseconds(200);
  bool high = digitalRead(p);
  pinMode(p, INPUT_PULLUP);
  return high;
}

void wireRestart() {
  Wire.begin();
  Wire.setClock(i2cHz);
}

/* ==========================================================================
   INPUTS
   ========================================================================== */
static inline bool readRaw(uint8_t pin) {   // every button goes to GND
  return digitalRead(pin) == LOW;
}

void buttonsUpdate() {
  uint32_t now = millis();
  for (uint8_t i = 0; i < B_COUNT; i++) {
    bool raw = readRaw(btn[i].pin);
    if (raw != btn[i].lastRaw) {
      btn[i].lastRaw = raw;
      btn[i].tEdge   = now;
    } else if (btn[i].level != raw && (now - btn[i].tEdge) >= DEBOUNCE_MS) {
      btn[i].level = raw;
      if (raw) {
        btn[i].evPress = true;
        btn[i].tPress  = now;
        btn[i].count++;
      } else {
        btn[i].evRelease = true;
        btn[i].heldMs    = now - btn[i].tPress;
      }
    }
  }
}

bool tookPress(uint8_t i) {                 // read and consume the event
  if (btn[i].evPress) { btn[i].evPress = false; return true; }
  return false;
}

bool tookRelease(uint8_t i) {
  if (btn[i].evRelease) { btn[i].evRelease = false; return true; }
  return false;
}

uint32_t heldFor(uint8_t i) {               // ms it has been held down
  return btn[i].level ? (millis() - btn[i].tPress) : 0;
}

// Matrix scan: each pin in turn becomes OUTPUT LOW while the rest stay inputs
// with pull-ups. Only ever one output at a time, so a short between two pins can
// never cause a driver conflict.
void swScan(SwGroup &g) {
  for (uint8_t i = 0; i < g.n; i++) g.row[i] = 0;

  for (uint8_t i = 0; i < g.n; i++) {
    pinMode(g.pins[i], OUTPUT);
    digitalWrite(g.pins[i], LOW);
    delayMicroseconds(SCAN_SETTLE_US);
    for (uint8_t j = 0; j < g.n; j++) {
      if (j == i) continue;
      if (digitalRead(g.pins[j]) == LOW) {
        g.row[i] |= (uint8_t)(1u << j);
        g.row[j] |= (uint8_t)(1u << i);
      }
    }
    pinMode(g.pins[i], INPUT_PULLUP);
    delayMicroseconds(SCAN_SETTLE_US);
  }

  // The pairs found, and which pins take part in them.
  uint8_t involved = 0;
  g.pairs = 0;
  for (uint8_t i = 0; i < g.n; i++) {
    if (g.row[i]) involved |= (uint8_t)(1u << i);
    for (uint8_t j = (uint8_t)(i + 1); j < g.n; j++)
      if (g.row[i] & (1u << j)) g.pairs++;
  }

  // No connection at all: the wiper is between positions, or the switch isn't
  // soldered. We keep the last stable position instead of reporting "unknown" -
  // otherwise it would flicker on every move from one position to the next.
  if (involved == 0) return;

  // The common pin is the only one present in EVERY position. We find it by
  // intersection, not statistically: once the switch has been through two
  // different positions the candidate set narrows to exactly one pin, for good.
  uint8_t narrowed = (uint8_t)(g.candMask & involved);
  g.candMask = narrowed ? narrowed : involved;   // contradiction -> start over

  int8_t com = -1;
  uint8_t nCand = 0;
  for (uint8_t i = 0; i < g.n; i++)
    if (g.candMask & (1u << i)) { nCand++; if (com < 0) com = (int8_t)i; }
  // While several candidates remain we follow the obvious convention, where
  // pin 1 is the common. It corrects itself on the first change of position.
  if (nCand > 1 && (g.candMask & 0x01)) com = 0;

  if (com != g.common) g.seenThrows = 0;   // new common -> renumber from scratch
  g.common = com;

  // The other end of the pair that contains the common pin.
  g.active = -1;
  if (com >= 0) {
    for (uint8_t j = 0; j < g.n; j++)
      if (j != (uint8_t)com && (g.row[com] & (1u << j))) { g.active = (int8_t)j; break; }
  }
  if (g.active < 0) return;                // still unknown: keep what we had

  int8_t pos = swPosNumber(g, g.active);
  g.seenThrows |= (uint8_t)(1u << g.active);

  uint32_t now = millis();
  if (pos != g.posCand) { g.posCand = pos; g.tEdge = now; }
  else if (pos != g.pos && (now - g.tEdge) >= SW_DEBOUNCE_MS) {
    g.pos = pos;
    g.changes++;
  }
}

uint8_t swSeenCount(const SwGroup &g) {
  uint8_t c = 0, v = g.seenThrows;
  while (v) { c = (uint8_t)(c + (v & 1)); v = (uint8_t)(v >> 1); }
  return c;
}

bool swCommonSure(const SwGroup &g) {
  return g.candMask && (g.candMask & (uint8_t)(g.candMask - 1)) == 0;  // a single bit
}

// Processing of a single transition. Kept out of the ISR so it can be called
// from the loop too: see encISR().
/* ---------------------------------------------------------------------------
   DETENT MODE (encMode == 0) - the decoder this board uses by default.

   Why it exists: the RC filter on A and B is asymmetric. When a contact closes,
   the capacitor discharges THROUGH THE CONTACT (a few ohms) - an instant
   falling edge. When it opens, it charges through the pull-up resistor - a
   rising edge delayed by ~1.2 * tau. Between detents the EC11's mechanism
   snaps, and the mid transitions can be tens of microseconds apart: less than
   the delay on those rising edges. So they overlap, and the middle transition
   is lost.

   The fix: stop relying on them. Everything we need is in the falling edges.
     - direction = the first edge after leaving rest (a contact CLOSES: towards
       01 one way, towards 10 the other) - fast and certain
     - proof it was a whole detent and not a wobble = we reached the state
       opposite rest, OR we visited both side states
     - emission = on the return to rest, exactly one step

   Result: at most one step per detent, immune to lost mid transitions, and no
   extra parts needed on the board.
   ------------------------------------------------------------------------- */
void encDetentStep(uint8_t prev, uint8_t s, int8_t d) {
  const uint8_t anti     = (uint8_t)(encRest ^ 0x03);
  const uint8_t sideMask = (uint8_t)(0x0F & ~(1u << encRest) & ~(1u << anti));

  if (s == encRest) {                         // back on a detent
    bool done = (encExcMask & (1u << anti)) ||
                ((encExcMask & sideMask) == sideMask);
    if (done) {
      int8_t dir = encExcDir;
      if (dir == 0) dir = d;                  // second chance: the return edge
      if (dir == 0) { dir = encLastDir; if (dir) encGuessed++; }
      if      (dir > 0) { encSteps++; encLastDir =  1; }
      else if (dir < 0) { encSteps--; encLastDir = -1; }
    }
    encExcDir  = 0;
    encExcMask = 0;
    return;
  }

  if (prev == encRest) { encExcDir = 0; encExcMask = 0; }   // a new excursion
  if (encExcDir == 0 && d != 0) encExcDir = d;
  encExcMask |= (uint8_t)(1u << s);
}

void encStep(uint8_t s) {
  uint8_t prev    = encPhase;
  int8_t  d       = ENC_TABLE[(prev << 2) | s];
  uint8_t changed = (uint8_t)(prev ^ s);
  if (changed & 0x02) encChgA++;
  if (changed & 0x01) encChgB++;
  encTrig  = (changed == 0x03) ? '2' : ((changed & 0x02) ? 'A' : 'B');
  encPhase = s;

  if (encLogOn) {                             // raw log, for the 'l' command
    uint8_t h = encLogHead;
    if (h < ENC_LOG_N) {
      encLogUs[h]    = micros();
      encLogState[h] = s;
      encLogD[h]     = d;
      encLogTrig[h]  = encTrig;
      encLogHead     = (uint8_t)(h + 1);
    }
  }

  if (d == 0) encErrors++; else encRaw += d;

  if (encMode == 0) { encDetentStep(prev, s, d); return; }

  // Antipodal jump (11 <-> 00): the rising edges the filter delayed overlapped
  // and the middle transition was lost. We know FOR SURE two quarter-steps went
  // by, but not which way - that information was precisely in the middle state.
  // At speed, though, you don't reverse direction mid-turn, so we go with the
  // last known direction. Each such step is counted separately in encGuessed,
  // so you can see how often it happens.
  if (d == 0 && encGuess && encLastDir != 0) {
    encAcc = (int8_t)(encAcc + 2 * encLastDir);
    encRaw += 2 * encLastDir;
    encGuessed++;
  }

  if (d == 0) {
    // Both bits changed: an edge was lost (noise, or turning too fast). We
    // can't know the direction, but we do NOT return here - if we landed
    // straight on rest, the accumulator so far is still valid and the step has
    // to be emitted, or that click is lost.
  } else {
    // Direction change: the accumulator starts over. Otherwise the remainder
    // left from the previous direction has to be spent before counting the
    // other way begins, and the first click back is lost.
    if ((encAcc > 0 && d < 0) || (encAcc < 0 && d > 0)) encAcc = d;
    else                                                encAcc = (int8_t)(encAcc + d);
  }

  // Rule 1: a full cycle. Once enough quarters have piled up in the SAME
  // direction the detent is consumed on the spot, without waiting for the rest
  // state. That keeps fast turning working even when rest is badly detected, or
  // when you fly past it too quickly for it to be recognised.
  // After emitting, the accumulator goes to ZERO rather than losing one cycle:
  // a remainder left here adds onto the next click and breaks the timing.
  const int8_t cyc = (int8_t)encMode;         // quarters per detent
  if (encAcc >= cyc)       { encSteps++; encAcc = 0; encLastDir =  1; }
  else if (encAcc <= -cyc) { encSteps--; encAcc = 0; encLastDir = -1; }

  if (encMode == 1) { encAcc = 0; return; }   // encoder without detents

  // Rule 2: the rest position - only if we actually know where it is. With the
  // votes scattered this rule would emit steps halfway through a turn and do
  // more harm than good, so it stays out of the way.
  if (!encRestSure) return;

  // Whatever is left in the accumulator at a detent is either a detent that
  // lost an edge (emit it) or chatter (clear it).
  bool atRest = (s == encRest) ||
                (encMode == 2 && s == (uint8_t)(encRest ^ 0x03));
  if (!atRest) return;

  int8_t thr = (encMode == 4) ? 2 : 1;        // the threshold allows one lost edge
  if      (encAcc >=  thr) { encSteps++; encLastDir =  1; }
  else if (encAcc <= -thr) { encSteps--; encLastDir = -1; }
  encAcc = 0;                                 // at rest, wipe any noise
}

// The interrupt re-reads the pins until the state settles. Without that loop,
// two edges arriving microseconds apart (chattering contacts, or a fast turn)
// look like a single event and the middle transition is lost - which is exactly
// how a real detent ends up counted as zero. The SAME function is attached to
// both pins, on purpose.
//
// Don't split it into two functions, one per pin: on the mbed core, with two
// distinct functions, the second attach leaves the first one mute - measured on
// the board, GPIO16 stopped producing interrupts entirely even though
// digitalRead() on it still worked. One function on both pins works. If you
// want to know which channel changed, derive it from the signal (see
// encChgA/encChgB in encStep).
void encISR() {
  for (uint8_t guard = 0; guard < 8; guard++) {
    uint8_t s = (uint8_t)((digitalRead(PIN_ENC_A) << 1) | digitalRead(PIN_ENC_B));
    if (s == encPhase) return;                // settled, we're done
    encStep(s);
  }
}

int32_t encTakeSteps() {                      // detents since the last call
  noInterrupts();
  int32_t st = encSteps;
  encSteps   = 0;
  interrupts();
  return st;
}

// Learns the rest state by vote, not from the last stop.
//
// The previous version simply took the last state the encoder stopped in. Stop
// it once BETWEEN two detents - which is exactly what happens when you spin it
// and let go - and the wrong state became "rest", after which everything
// decoded badly until the next clean stop. A vote ignores accidental stops:
// real detents show up far more often than anything else.
void encNoteRest(uint8_t ph) {
  ph &= 0x03;
  // On saturation we halve everything, keeping the proportions while leaving
  // room for new data. Without this all four states reach the maximum and the
  // distribution stops saying anything.
  encRestVotes[ph]++;
  if (encRestVotes[ph] >= 200)
    for (uint8_t i = 0; i < 4; i++) encRestVotes[i] = (uint8_t)(encRestVotes[i] >> 1);

  uint16_t total = 0;
  uint8_t  best  = 0;
  for (uint8_t i = 0; i < 4; i++) {
    total += encRestVotes[i];
    if (encRestVotes[i] > encRestVotes[best]) best = i;
  }
  encRest = best;

  // An encoder with 4 quarters per detent always stops in the same state. One
  // with 2 quarters alternates between two opposite states (00 and 11), so
  // there we count the pair. We only trust it when one state (or the pair)
  // gathers at least 70% of the stops, after a minimum of 8 stops.
  uint16_t good = encRestVotes[best];
  if (encMode == 2) good = (uint16_t)(good + encRestVotes[best ^ 0x03]);
  encRestSure = (total >= 8) && (good * 10 >= total * 7);
}

// Measures how many quarter-steps a detent produces, and learns the rest state.
// Called from loop(), never from the ISR.
void encHousekeeping() {
  static int32_t  lastRaw   = 0;
  static int32_t  burstBase = 0;
  static bool     burstOpen = false;
  static bool     restNoted = false;          // already voted for this stop?
  static uint32_t tQuiet    = 0;

  uint32_t now = millis();

  // Safety net: if the pins no longer match the last state the interrupt saw,
  // an edge was missed. We process it now, from the loop. Without this, a lost
  // return to rest would leave the excursion open forever.
  noInterrupts();
  uint8_t live = (uint8_t)((digitalRead(PIN_ENC_A) << 1) | digitalRead(PIN_ENC_B));
  if (live != encPhase) encStep(live);
  int32_t raw = encRaw;
  uint8_t ph  = encPhase;
  interrupts();

  if (raw != lastRaw) {                       // still moving
    if (!burstOpen) { burstOpen = true; burstBase = lastRaw; }
    lastRaw   = raw;
    tQuiet    = now;
    restNoted = false;                        // a new stop will deserve a vote
    return;
  }
  if (now - tQuiet < ENC_BURST_MS) return;

  if (!burstOpen) {
    // Long silence: the current position is almost certainly a detent.
    // ONE vote per stop - otherwise, for as long as the encoder sits still,
    // this branch would vote thousands of times a second and saturate the
    // counter.
    if (!restNoted && now - tQuiet > 300) { encNoteRest(ph); restNoted = true; }
    return;
  }

  // The detent is over: measure how big it was.
  int32_t burst = raw - burstBase;
  burstOpen = false;
  encNoteRest(ph);
  restNoted = true;                           // this stop's vote is cast
  int8_t sz = (int8_t)(burst < 0 ? -burst : burst);
  encLastBurst = sz;

  if (encTrace) {
    Serial.print(F("detent: ")); Serial.print(sz);
    Serial.print(F(" quarters, "));
    Serial.print(burst > 0 ? F("CW ") : (burst < 0 ? F("CCW") : F("-- ")));
    Serial.print(F("  rest=")); Serial.print((encRest >> 1) & 1); Serial.print(encRest & 1);
    Serial.print(F("  mode=")); Serial.print(encMode);
    Serial.print(F("  errors=")); Serial.println(encErrors);
  }

  if (encCalActive && sz > 0) {
    if (encCalCount < sizeof(encCalSizes)) encCalSizes[encCalCount++] = (uint8_t)sz;
    Serial.print(F("  calibration ")); Serial.print(encCalCount);
    Serial.print(F("/6: ")); Serial.print(sz); Serial.println(F(" quarters"));
    if (encCalCount >= 6) {
      // median, so one missed or doubled detent doesn't count
      for (uint8_t i = 1; i < encCalCount; i++)
        for (uint8_t j = i; j && encCalSizes[j - 1] > encCalSizes[j]; j--) {
          uint8_t t = encCalSizes[j]; encCalSizes[j] = encCalSizes[j - 1]; encCalSizes[j - 1] = t;
        }
      uint8_t med = encCalSizes[encCalCount / 2];
      encMode = (med >= 3) ? 4 : ((med == 2) ? 2 : 1);
      encCalActive = false;
      Serial.print(F("  >> median ")); Serial.print(med);
      Serial.print(F(" quarters/detent -> mode ")); Serial.println(encMode);
      Serial.println(F("  If it steps one at a time now, set ENC_MODE_DEFAULT"
                       " to this value in the sketch."));
    }
  }
}

// Hardware I2C probe. On Mbed the zero-length transaction isn't supported, so
// there's a fallback that reads one byte instead.
bool i2cProbe(uint8_t a) {
  Wire.beginTransmission(a);
  if (Wire.endTransmission() == 0) return true;
#if defined(ARDUINO_ARCH_MBED)
  return Wire.requestFrom((int)a, 1) == 1;
#else
  return false;
#endif
}

void i2cScan() {
  I2C_GUARD;
  i2cCount = 0;
  for (uint8_t a = 1; a < 127 && i2cCount < sizeof(i2cFound); a++)
    if (i2cProbe(a)) i2cFound[i2cCount++] = a;
}

/* ==========================================================================
   PCF8574 - 8 buttons on I2C
   ========================================================================== */
bool pcfDetect() {
  I2C_GUARD;
  pcfOK = false;
  for (uint8_t a = PCF_SCAN_LOW; a <= PCF_SCAN_HIGH; a++) {
    if (!i2cProbe(a)) continue;
    Wire.beginTransmission(a);
    Wire.write((uint8_t)0xFF);               // all pins to input
    if (Wire.endTransmission() != 0) continue;
    delay(2);
    if (Wire.requestFrom((int)a, 1) != 1) continue;
    pcfIdle = (uint8_t)Wire.read();
    pcfRaw  = pcfIdle;
    pcfAddr = a;
    pcfOK   = true;
    for (uint8_t i = 0; i < 8; i++) {
      pcfBtn[i].level = false; pcfBtn[i].lastRaw = false;
      pcfBtn[i].tEdge = 0;     pcfBtn[i].count   = 0;
    }
    return true;
  }
  return false;
}

void pcfUpdate() {
  if (!pcfOK) return;
  static uint32_t tNext = 0;
  uint32_t now = millis();
  if ((int32_t)(now - tNext) < 0) return;

  I2CTry lk;
  if (!lk.ok) return;            // bus busy with a frame: try again later
  tNext = now + PCF_POLL_MS;

  if (Wire.requestFrom((int)pcfAddr, 1) != 1) { pcfFails++; return; }
  pcfRaw = (uint8_t)Wire.read();

  uint8_t pressed = (uint8_t)(pcfRaw ^ pcfIdle);   // any bit that differs from rest
  for (uint8_t i = 0; i < 8; i++) {
    bool raw = (pressed >> i) & 1;
    if (raw != pcfBtn[i].lastRaw) {
      pcfBtn[i].lastRaw = raw;
      pcfBtn[i].tEdge   = now;
    } else if (pcfBtn[i].level != raw && (now - pcfBtn[i].tEdge) >= DEBOUNCE_MS) {
      pcfBtn[i].level = raw;
      if (raw) pcfBtn[i].count++;
    }
  }
}

/* ==========================================================================
   OLED
   ========================================================================== */
void oledAlloc() {
  // delete on the concrete type (not through a base class), so the right
  // destructor runs and the buffer is freed; the compiler's
  // -Wdelete-non-virtual-dtor warning doesn't apply here.
  if (oled) { delete oled; oled = NULL; }
  // The I2C speed goes HERE, in the constructor - not through Wire.setClock().
  // Adafruit_SSD1306 re-applies its own wireClk before every transfer (the
  // TRANSACTION_START macro), so any setClock() from outside is overwritten on
  // the next frame. Measured: with setClock() alone the frame stayed at ~15 ms
  // at both 100 kHz and 1 MHz - proof that none of it was getting through.
  oled = new Adafruit_SSD1306(SCREEN_W, oledH, &Wire, -1, i2cHz, i2cHz);
}

// Try to initialise the screen; can be called again at any time (the 'x'
// command). We establish it's really there with a bit-banged ACK first and only
// then initialise - otherwise oledOK would always be true and the status LED
// would be lying.
bool tryOledInit() {
  I2C_GUARD;
  Wire.end();
  delay(2);
  bool at3C, at3D;
  if (forceAddr >= 0) {
    at3C = (forceAddr == 0x3C);
    at3D = (forceAddr == 0x3D);
  } else {
    at3C = bbProbe(PIN_SDA, PIN_SCL, 0x3C);
    at3D = at3C ? false : bbProbe(PIN_SDA, PIN_SCL, 0x3D);
  }
  wireRestart();

  oledOK = at3C || at3D;
  if (!oledOK) return false;

  oledAddr = at3C ? 0x3C : 0x3D;
  oledAlloc();
  oled->begin(SSD1306_SWITCHCAPVCC, oledAddr);
  oled->setTextColor(SSD1306_WHITE);
  oled->setTextSize(1);
  oled->clearDisplay();
  oled->display();
  return true;
}

/* ==========================================================================
   DRAWING (128x32 by default)
   ========================================================================== */
/* --------------------------------------------------------------------------
   Edges for the 8 expander buttons.

   PcfBtn only keeps a level, without the press events Button has. We work them
   out here once per cycle so the menu and HID both see exactly the same thing.

   All 8 go straight to the PC: pages are changed with the onboard USER button,
   so no panel button is stolen by the menu any more.
   -------------------------------------------------------------------------- */
bool pcfEv[8] = { false, false, false, false, false, false, false, false };

void pcfEvents() {
  static bool prev[8] = { false, false, false, false, false, false, false, false };
  for (uint8_t i = 0; i < 8; i++) {
    bool lv  = pcfOK ? pcfBtn[i].level : false;
    pcfEv[i] = lv && !prev[i];
    prev[i]  = lv;
  }
}

static inline bool pcfPressed(uint8_t i) { return pcfEv[i]; }
static inline bool pcfHeld(uint8_t i)    { return pcfOK && pcfBtn[i].level; }

/* ==========================================================================
   HID - the whole mapping, in one place

   Every panel input has exactly one slot in the tables below. To change what it
   sends you change the slot - you don't go digging through the rest of the code.

   THE INPUTS, under the names you use for them:
     A1..A4, B1..B4    the 8 expander buttons (P0..P3 and P4..P7)
     SW1_1..SW1_3      the 3-position switch
     SW2_1..SW2_5      the 5-position switch
     ENC_CW / ENC_CCW  the encoder turned one way or the other
     ENC_CLICK         the encoder's own button
     UP..SET           the panel d-pad, if you fit it (NOTHING by default)

   MIND THE NAMES: on the board the switches are labelled SW2 and SW3, and in
   the code the objects are sw2 and sw3. The tables here use YOUR names:
     mapSw1[3] = the 3-position switch = object sw2 in code = SW2 on the board
     mapSw2[5] = the 5-position switch = object sw3 in code = SW3 on the board

   WHAT YOU CAN PUT IN A SLOT:
     NOTHING                      sends nothing
     KEY('a')                     a key; KEY_RETURN, KEY_ESC, KEY_F1 work too
     COMBO(KEY_LEFT_CTRL, 'c')    modifier + key
     MEDIA(CC_VOL_UP)             a media key
     CLICK(MOUSE_LEFT)            a mouse click
     SCROLL(+1)                   scroll wheel (+ up, - down)
     PAD(3)                       gamepad button 3
     TEXT("hello")                types a whole string

   WHY PAD() AND NOT KEYS:
     PAD(n) is a gamepad button - a dedicated input that Windows shows in "Set
     up USB game controllers" and any game can bind. It isn't a key, so it
     collides with nothing you type and never fires a shortcut by accident.
     There are 32 buttons available; we use 19.

     If you do want something keyboard-ish that gets in nobody's way, KEY(KEY_F13)
     through KEY_F24 are real keys that no program uses by default - easy to bind
     in AutoHotkey, OBS or games.

   HOW SWITCH POSITIONS BEHAVE:
     with PAD(n), the gamepad button stays HELD for as long as the switch sits
     in that position - which is how any game reads a selector correctly.
     with anything else it's sent ONCE, the moment you reach the position.
     Otherwise a key would repeat forever while you stayed there.

   Buttons and the encoder send once per press, or per detent. The exception is
   PAD(n), where the button follows the press, so you can hold it.
   ========================================================================== */
#if HID_AVAILABLE


// The encoder and its click have no duration: a detent is an event, not a press
// you hold. For the PC to still see them as button presses we send a short
// pulse - pressed now, released automatically after PAD_PULSE_MS.
#define PAD_PULSE_MS 25
uint32_t padPulseEnd[33] = { 0 };      // index 1..32; 0 = no pulse running

static void padPulse(uint8_t n) {
  if (n < 1 || n > 32) return;
  Joystick.button(n, true);
  uint32_t due = millis() + PAD_PULSE_MS;
  padPulseEnd[n] = due ? due : 1;      // 0 means "no pulse", so we avoid it
}

static void padPulseService() {
  uint32_t now = millis();
  for (uint8_t n = 1; n <= 32; n++)
    if (padPulseEnd[n] && (int32_t)(now - padPulseEnd[n]) >= 0) {
      Joystick.button(n, false);
      padPulseEnd[n] = 0;
    }
}

void hidReleaseAll() {
  for (uint8_t n = 1; n <= 32; n++) padPulseEnd[n] = 0;
  Keyboard.releaseAll();
  Mouse.release(MOUSE_LEFT);
  Mouse.release(MOUSE_RIGHT);
  Mouse.release(MOUSE_MIDDLE);
  for (uint8_t i = 1; i <= 32; i++) Joystick.button(i, false);
  Joystick.send_now();
}

void hidDisarm(const char *why) {
  if (!hidArmed) return;
  hidArmed = false;
  hidReleaseAll();
  Serial.print(F("*** HID DISARMED (")); Serial.print(why); Serial.println(F(") ***"));
}

void hidSetArmed(bool on) {
  if (on == hidArmed) return;
  if (on) {
    hidArmed   = true;
    hidActions = 0;
    hidLast    = "-";
    Serial.println(F("*** HID ARMED - the panel now types into the PC ***"));
    Serial.println(F("    to stop: hold USER for 1s, or the 'u' command"));
  } else {
    hidDisarm("the 'u' command");
  }
}

// on = true on press / on entering a position; false only for PAD, so it can be
// released as well. Every other kind ignores on == false.
/* Ask the PC app to move a volume.

   Deliberately not HID: it is a line on the serial link the app already reads,
   which is why the volume works with HID disarmed. The media-key fallback below
   IS HID, so it stays behind the arming - a disarmed panel does not type into
   the PC, and that rule is worth more than a knob that works in every case. */
void audioAsk(uint8_t code) {
  Serial.print(F("!AUD "));
  Serial.println(code);
  audTouch = millis();            // show it on the screen without waiting
#if HID_AVAILABLE
  if (!audFresh() && hidArmed) {
    uint16_t cc = 0;
    if      (code == APP_UP)   cc = CC_VOL_UP;
    else if (code == APP_DN)   cc = CC_VOL_DN;
    else if (code == APP_MUTE) cc = CC_MUTE;
    if (cc) { Keyboard.consumerPress(cc); delay(5); Keyboard.consumerRelease(); }
  }
#endif
}

void actFire(const Action &act, bool on, const char *label) {
  switch (act.kind) {
    case AK_NONE:
      return;
    case AK_PAD:
      Joystick.button((uint8_t)act.a, on);
      if (!on) return;                       // a release isn't worth reporting
      break;
    case AK_KEY:
      if (!on) return;
      Keyboard.write((uint8_t)act.a);
      break;
    case AK_COMBO:
      if (!on) return;
      Keyboard.press((uint8_t)act.b);
      Keyboard.press((uint8_t)act.a);
      delay(5);
      Keyboard.release((uint8_t)act.a);
      Keyboard.release((uint8_t)act.b);
      break;
    case AK_CC:
      if (!on) return;
      Keyboard.consumerPress(act.a);
      delay(5);
      Keyboard.consumerRelease();
      break;
    case AK_CLICK:
      if (!on) return;
      Mouse.click((uint8_t)act.a);
      break;
    case AK_WHEEL:
      if (!on) return;
      Mouse.move(0, 0, (signed char)(int16_t)act.a);
      break;
    case AK_TEXT:
      if (!on || !act.str) return;
      Keyboard.print(act.str);
      break;
    case AK_APP:
      if (!on) return;
      audioAsk((uint8_t)act.a);
      break;
    default:
      return;
  }
  hidActions++;
  hidLast = label;
}

// A momentary event (an encoder detent, a click). For PAD we send a pulse,
// because otherwise the button would stay held forever: a detent has no
// "release".
void actTap(const Action &act, const char *label) {
  if (act.kind == AK_PAD) {
    padPulse((uint8_t)act.a);
    hidActions++;
    hidLast = label;
    return;
  }
  actFire(act, true, label);
}

void hidScanTables() {
  padUsed = false;
  for (uint8_t i = 0; i < 4; i++) if (mapBtnA[i].kind == AK_PAD) padUsed = true;
  for (uint8_t i = 0; i < 4; i++) if (mapBtnB[i].kind == AK_PAD) padUsed = true;
  for (uint8_t i = 0; i < 3; i++) if (mapSw1[i].kind  == AK_PAD) padUsed = true;
  for (uint8_t i = 0; i < 5; i++) if (mapSw2[i].kind  == AK_PAD) padUsed = true;
  for (uint8_t i = 0; i < 6; i++) if (mapPanel[i].kind == AK_PAD) padUsed = true;
  if (mapEncCW.kind == AK_PAD || mapEncCCW.kind == AK_PAD || mapEncClick.kind == AK_PAD)
    padUsed = true;
}

// A button: with PAD we follow the level (so it can be held), otherwise we send
// on the press.
static void hidButton(const Action &act, bool held, bool pressed, const char *label) {
  if (act.kind == AK_PAD) actFire(act, held, label);
  else if (pressed)       actFire(act, true, label);
}

// A switch: with PAD every position follows the current state; otherwise it's
// sent once, on entering the position.
static void hidSwitch(Action *map, uint8_t n, int8_t pos, int8_t &last,
                      const char **labels) {
  for (uint8_t i = 0; i < n; i++)
    if (map[i].kind == AK_PAD)
      actFire(map[i], pos == (int8_t)(i + 1), labels[i]);

  if (pos != last) {
    if (pos >= 1 && pos <= (int8_t)n && map[pos - 1].kind != AK_PAD)
      actFire(map[pos - 1], true, labels[pos - 1]);
    last = pos;
  }
}

void hidUpdate(int32_t det) {
  if (!hidArmed) return;

  padPulseService();      // close out any pulses that have expired

  // the 8 buttons: A1..A4 = P0..P3, B1..B4 = P4..P7
  // the layer is latched: toggled by double-tapping USER, not by holding it
  // On an audio page the four B buttons and the knob belong to the volume,
  // whatever the layer says. Releasing on the way in matters: a gamepad button
  // held as you change page would stay pressed for ever.
  bool audio = audioPage();
  static bool audioWas = false;
  if (audio != audioWas) { audioWas = audio; hidReleaseAll(); }

  bool shift = mediaLayer;
  Action      *A  = shift ? mapShiftA : mapBtnA;
  Action      *B  = shift ? mapShiftB : mapBtnB;
  const char **LA = shift ? LBL_SA    : LBL_A;
  const char **LB = shift ? LBL_SB    : LBL_B;

  for (uint8_t i = 0; i < 4; i++)
    hidButton(A[i], pcfHeld(i),     pcfPressed(i),     LA[i]);
  if (!audio)
    for (uint8_t i = 0; i < 4; i++)
      hidButton(B[i], pcfHeld(i + 4), pcfPressed(i + 4), LB[i]);

  // the switches. sw2 in code = your SW1 (3 positions); sw3 = SW2 (5 positions)
  static int8_t lastSw1 = -1, lastSw2 = -1;
  hidSwitch(mapSw1, 3, sw2.pos, lastSw1, LBL_SW1);
  hidSwitch(mapSw2, 5, sw3.pos, lastSw2, LBL_SW2);

  // the d-pad, if it's fitted and mapped
  for (uint8_t i = 0; i < 6; i++) {
    if (mapPanel[i].kind == AK_NONE) continue;      // leave it to page navigation
    hidButton(mapPanel[i], btn[i].level, tookPress(i), LBL_PANEL[i]);
    tookRelease(i);
  }

  // the encoder's button: treated like the A/B buttons, so with PAD() it stays
  // held for as long as you hold it instead of being a short pulse
  hidButton(shift ? mapShiftEncClick : mapEncClick,
            btn[B_SW].level, tookPress(B_SW), shift ? "^ENC" : "ENC_CLICK");
  tookRelease(B_SW);

  // the encoder: one action per detent, but at most a few per cycle so a sharp
  // spin doesn't flood the host
  if (det && !audio) {
    int32_t mag = (det > 0) ? det : -det;
    if (mag > 4) mag = 4;
    for (int32_t i = 0; i < mag; i++)
      actTap(det > 0 ? (shift ? mapShiftEncCW  : mapEncCW)
                     : (shift ? mapShiftEncCCW : mapEncCCW),
             det > 0 ? (shift ? "^CW" : "ENC_CW") : (shift ? "^CCW" : "ENC_CCW"));
  }

  // one gamepad report at ~50 Hz, and only if the gamepad is used at all
  if (padUsed) {
    static uint32_t tPad = 0;
    uint32_t now = millis();
    if (now - tPad >= 20) { tPad = now; Joystick.send_now(); }
  }
}

static void printAct(const char *label, const Action &act) {
  Serial.print(F("    "));
  Serial.print(label);
  for (uint8_t k = (uint8_t)strlen(label); k < 10; k++) Serial.print(' ');
  switch (act.kind) {
    case AK_NONE:  Serial.println(F("-"));                                    break;
    case AK_TEXT:  Serial.print(F("text \"")); Serial.print(act.str);
                   Serial.println('"');                                       break;
    default:       Serial.println(act.str);                                   break;
  }
}

void printHidStatus() {
  Serial.println(F("--- HID ---"));
  Serial.print(F("  state  : ")); Serial.println(hidArmed ? F("ARMED") : F("disarmed"));
  Serial.print(F("  sent   : ")); Serial.println(hidActions);
  Serial.print(F("  last   : ")); Serial.println(hidLast);
  Serial.print(F("  SW1=")); if (sw2.pos) Serial.print(sw2.pos); else Serial.print('?');
  Serial.print(F("  SW2=")); if (sw3.pos) Serial.print(sw3.pos); else Serial.print('?');
  Serial.print(F("  gamepad=")); Serial.println(padUsed ? F("yes") : F("no"));

  Serial.println(F("  buttons:"));
  for (uint8_t i = 0; i < 4; i++) printAct(LBL_A[i], mapBtnA[i]);
  for (uint8_t i = 0; i < 4; i++) printAct(LBL_B[i], mapBtnB[i]);
  Serial.println(F("  3-position switch:"));
  for (uint8_t i = 0; i < 3; i++) printAct(LBL_SW1[i], mapSw1[i]);
  Serial.println(F("  5-position switch:"));
  for (uint8_t i = 0; i < 5; i++) printAct(LBL_SW2[i], mapSw2[i]);
  Serial.println(F("  encoder:"));
  printAct("ENC_CW",    mapEncCW);
  printAct("ENC_CCW",   mapEncCCW);
  printAct("ENC_CLICK", mapEncClick);
  Serial.print(F("  layer two (double-tap USER)"));
  Serial.println(mediaLayer ? F("  <-- ACTIVE") : F(""));
  for (uint8_t i = 0; i < 4; i++) printAct(LBL_SA[i], mapShiftA[i]);
  printAct("^CW",  mapShiftEncCW);
  printAct("^CCW", mapShiftEncCCW);
  printAct("^ENC", mapShiftEncClick);
  bool anyPanel = false;
  for (uint8_t i = 0; i < 6; i++) if (mapPanel[i].kind != AK_NONE) anyPanel = true;
  if (anyPanel) {
    Serial.println(F("  d-pad:"));
    for (uint8_t i = 0; i < 6; i++) printAct(LBL_PANEL[i], mapPanel[i]);
  }
}

#else   // ------------------------------------------------ Mbed core, no HID

void printHidStatus() {
  Serial.println(F("HID unavailable: the Mbed core has no Keyboard/Mouse/Joystick."));
  Serial.println(F("Switch to the Philhower core, with USB Stack = Pico SDK."));
}

#endif

/* --------------------------------------------------------------------------
   Game telemetry, sent by the PC over serial.

   The board doesn't know and doesn't care which game the data comes from. The
   PC normalises everything into a small set of fields and sends one line
   starting with '$':

     $src=ETS2;spd=88;rpm=1450;rpmmax=2500;rl=2250;gear=6;fuel=62;tmp=88

   rl = the redline. The PC works it out by itself (see RevRange in
   telemetry/sources.py): for games that don't report one, it infers it from the
   rpm where the engine stops climbing with the throttle floored. While it's 0
   the rev bar has no red mark - better none than an invented one.

   Missing fields keep their previous value, so you can send only what changed.
   All of them are optional.

   Why '$' and not a letter: the console uses SINGLE-character commands, and any
   letter picked as a prefix would collide with one of them ('g' is already the
   encoder's guessing toggle, for instance).

   If nothing arrives for GAME_STALE_MS the page says "no game". Otherwise you'd
   be left with the last speed on screen after closing the game and think it was
   still running.
   -------------------------------------------------------------------------- */
#define GAME_STALE_MS 2000

char     gameSrc[12]  = "";
char     gameTxt[26]  = "";
int32_t  gameSpd      = 0;
int32_t  gameRpm      = 0;
int32_t  gameRpmMax   = 0;
int32_t  gameGear     = 0;      // 0 = N, negative = reverse
int32_t  gameFuel     = -1;     // percent; -1 = unknown
int32_t  gameRedline  = 0;      // the limiter, learned by the PC; 0 = unknown
int32_t  gameThr      = 0;      // throttle 0..100
int32_t  gameBrk      = 0;      // brake 0..100
int32_t  gameTmp      = 0;      // engine temperature, degrees C
int32_t  gameAlt      = 0;

// The game's KIND picks the page set, not its name: 'c' = car, 'a' = aircraft.
// That way a new sim needs no change here, it just has to send knd.
// 'c' road vehicle, 'a' airliner-style aircraft, 'w' War Thunder aircraft,
// 'g' War Thunder ground vehicle. War Thunder gets its own two because it has
// no radios, transponder or autopilot, and does have G, angle of attack and a
// crew roster - on the shared aircraft pages half of them stayed blank.
char     gameKind     = 'c';
uint8_t  gameSub      = 0;      // sub-page within the current set
int32_t  gameBlink    = 0;      // bit0 left, bit1 right
int32_t  gameTurbo    = 0;      // bar x10
int32_t  gameKts      = 0;
int32_t  gameVs       = 0;      // feet/minute
int32_t  gameAltFt    = 0;
int32_t  gameHdg      = -1;
char     gameC1[10]   = "";
char     gameC2[10]   = "";
char     gameSqk[6]   = "";
char     gameAp[18]   = "";
char     gameCrew[8]  = "";
int32_t  gameG        = 0;      // x10
int32_t  gameAoa      = 0;

static uint8_t gameSubCount() {
  uint8_t n;
  switch (gameKind) {
    case 'a': n = 4; break;   // radios, transponder, autopilot
    case 'w': n = 3; break;   // no radios in War Thunder
    case 'g': n = 2; break;   // a tank has little to show
    default:  n = 3; break;
  }
  // And one more on the end: the volume. With HID armed, in a game, USER walks
  // these and nothing else - so without this there is no way to reach the
  // volume at all without disarming first.
  return (uint8_t)(n + 1);
}

// Is the LAST sub-page - the volume one - the one being shown?
static bool gameAudioSub() {
  // With a game sending, the volume is the last of its sub-pages. With NOTHING
  // sending, the page says "no game" and has only one other thing worth
  // showing, so the volume is sub-page 1.
  //
  // This case was missing and it made the knob look broken: on the GAME page
  // with no telemetry there was no volume sub-page at all, so the knob stayed a
  // gamepad button and turning it did nothing you could hear.
  if (!gameFresh()) return gameSub == 1;
  return gameSub == (uint8_t)(gameSubCount() - 1);
}
uint32_t gameSeen     = 0;      // millis at the last line received
uint32_t gameLines    = 0;

static void gameSetField(char *key, char *val) {
  if      (!strcasecmp(key, "src"))    { strncpy(gameSrc, val, sizeof(gameSrc) - 1); gameSrc[sizeof(gameSrc) - 1] = 0; }
  else if (!strcasecmp(key, "txt"))    { strncpy(gameTxt, val, sizeof(gameTxt) - 1); gameTxt[sizeof(gameTxt) - 1] = 0; }
  else if (!strcasecmp(key, "spd"))    gameSpd    = atol(val);
  else if (!strcasecmp(key, "rpm"))    gameRpm    = atol(val);
  else if (!strcasecmp(key, "rpmmax")) gameRpmMax = atol(val);
  else if (!strcasecmp(key, "gear"))   gameGear   = atol(val);
  else if (!strcasecmp(key, "fuel"))   gameFuel    = atol(val);
  else if (!strcasecmp(key, "rl"))     gameRedline = atol(val);
  else if (!strcasecmp(key, "thr"))    gameThr     = atol(val);
  else if (!strcasecmp(key, "brk"))    gameBrk     = atol(val);
  else if (!strcasecmp(key, "tmp"))    gameTmp     = atol(val);
  else if (!strcasecmp(key, "alt"))    gameAlt    = atol(val);
  else if (!strcasecmp(key, "blk"))    gameBlink  = atol(val);
  else if (!strcasecmp(key, "tur"))    gameTurbo  = atol(val);
  else if (!strcasecmp(key, "kts"))    gameKts    = atol(val);
  else if (!strcasecmp(key, "vs"))     gameVs     = atol(val);
  else if (!strcasecmp(key, "aft"))    gameAltFt  = atol(val);
  else if (!strcasecmp(key, "hdg"))    gameHdg    = atol(val);
  else if (!strcasecmp(key, "c1"))     { strncpy(gameC1,  val, sizeof(gameC1) - 1);  gameC1[sizeof(gameC1) - 1] = 0; }
  else if (!strcasecmp(key, "c2"))     { strncpy(gameC2,  val, sizeof(gameC2) - 1);  gameC2[sizeof(gameC2) - 1] = 0; }
  else if (!strcasecmp(key, "sqk"))    { strncpy(gameSqk, val, sizeof(gameSqk) - 1); gameSqk[sizeof(gameSqk) - 1] = 0; }
  else if (!strcasecmp(key, "ap"))     { strncpy(gameAp,   val, sizeof(gameAp) - 1);   gameAp[sizeof(gameAp) - 1] = 0; }
  else if (!strcasecmp(key, "crew"))   { strncpy(gameCrew, val, sizeof(gameCrew) - 1); gameCrew[sizeof(gameCrew) - 1] = 0; }
  else if (!strcasecmp(key, "g"))      gameG   = atol(val);
  else if (!strcasecmp(key, "aoa"))    gameAoa = atol(val);
  else if (!strcasecmp(key, "knd")) {
    // Full names, not first letters: "air" and "wtair" both start with a
    // vowel's worth of ambiguity, and a wrong guess draws the wrong pages.
    char k = 'c';
    if      (!strcasecmp(val, "air"))   k = 'a';
    else if (!strcasecmp(val, "wtair")) k = 'w';
    else if (!strcasecmp(val, "wtgnd")) k = 'g';
    if (k != gameKind) { gameKind = k; gameSub = 0; }   // the set changed
  }
}

/* '%' lines come from the PC app and are NOT telemetry: they must not make
   the panel think a game is running. Hence a prefix of their own.
       %au=2/7;nm=Spotify;vl=64;mu=0                                          */
// The other direction from b64Emit(). Returns how many bytes came out.
static uint16_t b64Decode(const char *in, uint8_t *out, uint16_t cap) {
  uint32_t acc = 0;
  uint8_t  bits = 0;
  uint16_t n = 0;
  for (; *in; in++) {
    char c = *in;
    int8_t v;
    if      (c >= 'A' && c <= 'Z') v = c - 'A';
    else if (c >= 'a' && c <= 'z') v = c - 'a' + 26;
    else if (c >= '0' && c <= '9') v = c - '0' + 52;
    else if (c == '+')             v = 62;
    else if (c == '/')             v = 63;
    else                           continue;      // '=' padding, or a stray
    acc = (acc << 6) | (uint8_t)v;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      if (n < cap) out[n++] = (uint8_t)(acc >> bits);
    }
  }
  return n;
}

void audParse(char *s) {
  // Which strip the "d=" in this line belongs to. Set by "ts=" earlier in the
  // same line; the PC always sends them in that order.
  uint8_t  tsKind = 0;
  uint16_t tsW    = 0;
  char *save = NULL;
  for (char *tok = strtok_r(s, ";", &save); tok; tok = strtok_r(NULL, ";", &save)) {
    char *eq = strchr(tok, '=');
    if (!eq) continue;
    *eq = 0;
    char *key = tok, *val = eq + 1;
    // A CHANGE is what earns the screen, not the arrival of another identical
    // heartbeat - otherwise the level would sit over the artist for ever. This
    // also catches a change you made somewhere else, in Windows' own mixer.
    if (!strcasecmp(key, "au")) {
      uint8_t idx = (uint8_t)atoi(val);
      char *slash = strchr(val, '/');
      if (idx != audIdx) audTouch = millis();
      audIdx = idx;
      audCount = slash ? (uint8_t)atoi(slash + 1) : 0;
      audSeen = millis();
    } else if (!strcasecmp(key, "nm")) {
      strncpy(audName, val, sizeof(audName) - 1);
      audName[sizeof(audName) - 1] = 0;
    } else if (!strcasecmp(key, "vl")) {
      int16_t v = (int16_t)atoi(val);
      if (v != audVol) audTouch = millis();
      audVol = v;
    } else if (!strcasecmp(key, "mu")) {
      bool m = (atoi(val) != 0);
      if (m != audMute) audTouch = millis();
      audMute = m;
      audSeen = millis();
    } else if (!strcasecmp(key, "sr")) {
      audSrc = (val[0] == 'a') ? 'a' : 'm';
    }
    // ---- a rendered line of text: "%ts=1;w=63;d=<base64>"
    else if (!strcasecmp(key, "ts")) {
      tsKind = (uint8_t)atoi(val);
    } else if (!strcasecmp(key, "w")) {
      tsW = (uint16_t)atoi(val);
    } else if (!strcasecmp(key, "d")) {
      uint8_t *dst = (tsKind == 2) ? npArtBmp : npTitleBmp;
      uint16_t got = b64Decode(val, dst, NP_BMP_MAX);
      if (got > tsW) got = tsW;              // trust the declared width
      if (tsKind == 2) { npArtW = got;   marq[3].t0 = millis(); marq[3].hash = 0; }
      else             { npTitleW = got; marq[2].t0 = millis(); marq[2].hash = 0; }
      tsKind = 0;
      tsW = 0;
    }
    // ---- what's playing
    else if (!strcasecmp(key, "np")) {
      if (atoi(val) == 0) {
        npTitle[0] = 0; npArtist[0] = 0; npDur = 0;
        npTitleW = 0;   npArtW = 0;       // and their pixels, or they'd linger
      }
      npSeen = millis();
    } else if (!strcasecmp(key, "st")) {
      npPlaying = (atoi(val) != 0);
    } else if (!strcasecmp(key, "sk")) {
      npKind = (val[0] == 'y') ? 'y' : 's';
    } else if (!strcasecmp(key, "ps")) {
      npPosMs = (int32_t)atol(val) * 1000;
      npPosAt = millis();
    } else if (!strcasecmp(key, "du")) {
      npDur = (int32_t)atol(val);
    } else if (!strcasecmp(key, "ti")) {
      // A different title arriving without a strip to match means the strip
      // belongs to the last song. Drop it rather than show the wrong words.
      if (strcmp(npTitle, val)) npTitleW = 0;
      strncpy(npTitle, val, sizeof(npTitle) - 1);
      npTitle[sizeof(npTitle) - 1] = 0;
    } else if (!strcasecmp(key, "ar")) {
      if (strcmp(npArtist, val)) npArtW = 0;
      strncpy(npArtist, val, sizeof(npArtist) - 1);
      npArtist[sizeof(npArtist) - 1] = 0;
    }
  }
}

void gameParse(char *s) {
  gameSeen = millis();
  gameLines++;
  char *save = NULL;
  for (char *tok = strtok_r(s, ";", &save); tok; tok = strtok_r(NULL, ";", &save)) {
    char *eq = strchr(tok, '=');
    if (!eq) continue;
    *eq = 0;
    gameSetField(tok, eq + 1);
  }
}

static bool gameFresh() {
  return gameSeen && (millis() - gameSeen) < GAME_STALE_MS;
}

/* A page whose job is the volume. While one is up, the four B buttons and the
   knob move the volume instead of doing whatever they normally do - and they do
   it with HID disarmed, because none of it is HID.

   Gating on the page rather than on a layer is the point: you get the volume
   when you are looking at it, and never while you are only playing. */
static bool audioPage() {
  return page == P_MUSIC || (page == P_GAME && gameAudioSub());
}

/* Called every loop, before HID gets a look in. pcfPressed() reads a flag that
   pcfEvents() recomputes once per turn, so reading it here as well takes
   nothing away from anyone. */
static void audioPageUpdate(int32_t det) {
  if (!audioPage()) return;
  if (pcfPressed(4)) audioAsk(APP_PREV);
  if (pcfPressed(5)) audioAsk(APP_NEXT);
  if (pcfPressed(6)) audioAsk(APP_MUTE);
  if (pcfPressed(7)) audioAsk(APP_HOME);
  if (det) {
    int32_t mag = (det > 0) ? det : -det;
    if (mag > 4) mag = 4;              // a hard spin shouldn't flood the app
    for (int32_t i = 0; i < mag; i++) audioAsk(det > 0 ? APP_UP : APP_DN);
  }
}

/* --------------------------------------------------------------------------
   Screen sleep.

   Two stages: first the contrast drops, then the panel goes fully dark with
   DISPLAYOFF. That second stage really does cut the current through the OLED
   and saves the pixels from burning in - a screen showing the same page for
   hours leaves its mark permanently.

   Waking isn't tied to the USER button alone: any button, any switch position,
   any encoder detent and any serial command resets the clock. Otherwise you'd
   press something, it would happen, and you wouldn't see it.

   The first USER press while the screen sleeps ONLY wakes it - it doesn't also
   change the page. Otherwise you'd lose the page you were on every time.
   -------------------------------------------------------------------------- */
uint8_t  oledSleep = 0;        // 0 = awake, 1 = dimmed, 2 = off

// Top edge of the content area. FIXED: only the header animates, the pages
// stay exactly where they are. Sliding the content up as well was tried and
// undone - everything jumped at once and you lost your place on the page.
// It stays a named origin rather than a scattered literal 11 so the layouts
// read as "first row, second row" instead of magic numbers.
const uint8_t gTop = 10;
uint32_t tLastAct  = 0;

// Called from core0 (a button, activity, serial). It does NOT touch the bus: it
// only resets the clock and leaves a note. The I2C part is done by core1, in
// oledSleepService - otherwise a simple button press would have to wait for the
// frame currently being drawn.
volatile bool oledWakeReq = false;

void oledWake() {
  tLastAct = millis();
  if (oledSleep != 0) oledWakeReq = true;
}

void oledSleepService() {
  I2C_GUARD;
  if (!oledOK || !oled) return;

  // The wake core0 asked for, done here because we're the ones holding the bus.
  if (oledWakeReq) {
    oledWakeReq = false;
    if (oledSleep == 2) oled->ssd1306_command(SSD1306_DISPLAYON);
    if (oledSleep != 0) {
      oled->ssd1306_command(SSD1306_SETCONTRAST);
      oled->ssd1306_command(OLED_CONTRAST_ON);
    }
    oledSleep = 0;
  }

  if (OLED_DIM_MS == 0) return;

#if OLED_AWAKE_ON_GAME
  // A game sending data means you're looking at the screen. We hold the clock at
  // zero while telemetry is fresh, and wake it if it had managed to fall asleep.
  // When the game stops, telemetry goes stale after GAME_STALE_MS and the usual
  // countdown starts from there.
  if (gameFresh()) {
    // Keep the panel lit, but do NOT touch tLastAct. That clock measures YOUR
    // activity, and the header retract reads the same clock - telemetry
    // resetting it sixty times a second would pin the header open for the
    // whole drive. Only the sleep state is held back here.
    if (oledSleep != 0) oledWakeReq = true;
    return;
  }
#endif

  uint32_t idle = millis() - tLastAct;
  if (oledSleep == 0 && idle >= OLED_DIM_MS) {
    oled->ssd1306_command(SSD1306_SETCONTRAST);
    oled->ssd1306_command(OLED_CONTRAST_DIM);
    oledSleep = 1;
  } else if (oledSleep == 1 && idle >= OLED_OFF_MS) {
    oled->ssd1306_command(SSD1306_DISPLAYOFF);
    oledSleep = 2;
  }
}

// Anything that moved on the panel counts as activity. We look at STATES, not
// events, so we don't fight tookPress()/pcfPressed() over consuming them.
void activityWatch() {
  static uint32_t last = 0xFFFFFFFF;
  uint32_t fp = (uint32_t)encTotal;
  for (uint8_t i = 0; i < B_COUNT; i++) if (btn[i].level)    fp += 1UL << (i + 1);
  for (uint8_t i = 0; i < 8; i++)       if (pcfBtn[i].level) fp += 1UL << (i + 9);
  fp += (uint32_t)(sw2.pos + 1) << 18;
  fp += (uint32_t)(sw3.pos + 1) << 22;
  if (fp != last) {
    last = fp;
    oledWake();
  }
}

/* --------------------------------------------------------------------------
   The onboard USER button (GP24) - changes the page on screen.

   Polarity is NOT assumed. There's no schematic for the YD-RP2040 board, and
   the button could be wired to ground or to positive. So at startup we read the
   pin once and remember whatever we find as the resting state; pressed means
   anything that differs from it. The only case that comes out backwards is
   holding the button down at the exact moment of power-up - the 'r' command
   takes the reference again.

     short press           -> next page
     TWO short presses     -> toggle the media layer
     held for 1 second     -> arms / disarms HID, on the spot

   The window for the second press is usrDoubleMs, changed with 'w'. On every
   single press the board prints how long it has been since the previous one -
   that's how you see whether your "double" lands inside the window or not.

   Everything lives on this button. ENC_SW stays a plain gamepad button.
   (The 'y' command and the button in the PC app do the same thing, for when you
   want to switch from the computer.)

   Two variants were tried and thrown away:
     - "two short presses = media layer": two page presses made quickly one
       after the other look exactly the same, so it toggled by accident.
     - "1 s = media, 3 s = HID": reaching HID meant passing THROUGH media, and
       letting go at around 2 s left the layer flipped the wrong way. Two nested
       actions on one button can't be made safe.

   That's why they sit on different buttons now: they can't be mixed up at all.

   If the screen was asleep, the press that wakes it does NOT also change page.

   The page changes on RELEASE, not on press. There's no other way once the
   button also has a long press: if the page jumped the instant you pressed,
   every arming would change your page on the way.

   There used to be a long press that took you back to the first page. It was
   removed - it was far too easy to hold exactly that long by accident, and the
   page looked like it changed and then jumped back on its own.
   -------------------------------------------------------------------------- */
#define USR_HOLD_MS   1000    // USER held this long = arm / disarm HID
// The double-press window for USER. Adjustable with the 'w' command, because
// "how fast you press twice" can't be guessed from outside - it depends on the
// finger and on the button. The board tells you after each press how long it
// has been since the previous one, so you pick it from a measurement rather
// than out of thin air.
uint16_t usrDoubleMs = 250;

bool     usrIdle     = true;      // the level read at power-up
// usrLevel and mediaLayer are declared up top: hidUpdate() and header() read them
bool     usrLastRaw  = false;
uint32_t usrEdge     = 0;
uint32_t usrPressAt  = 0;
bool     usrWokeOled = false;    // this press was the one that woke the screen
bool     usrStage1   = false;    // this press has already toggled HID
uint32_t usrCount    = 0;

void usrLearnIdle() {
  pinMode(PIN_USR_KEY, INPUT_PULLUP);
  delay(2);
  usrIdle    = (digitalRead(PIN_USR_KEY) != LOW);
  usrLevel   = false;
  usrLastRaw = false;
}

void usrUpdate() {
  uint32_t now = millis();
  bool raw = ((digitalRead(PIN_USR_KEY) != LOW) != usrIdle);   // != rest = pressed

  if (raw != usrLastRaw) {
    usrLastRaw = raw;
    usrEdge    = now;
    return;
  }
  if (usrLevel == raw || (now - usrEdge) < DEBOUNCE_MS) {
    if (!usrLevel) return;
    if (!usrStage1 && (now - usrPressAt) >= USR_HOLD_MS) {
      usrStage1 = true;
#if HID_AVAILABLE
      hidSetArmed(!hidArmed);
#else
      Serial.println(F("HID unavailable: you're running on the Mbed core."));
#endif
    }
    return;
  }

  usrLevel = raw;
  if (raw) {
    usrPressAt  = now;
    usrStage1   = false;
    usrWokeOled = (oledSleep != 0);   // noted BEFORE waking it
    usrCount++;
    oledWake();
    return;
  }

  // Released. If the long stage fired while holding, that was the action.
  if (usrStage1) return;
  if (usrWokeOled) return;            // the press only woke the screen

  static uint32_t lastShort = 0;
  uint32_t gap = lastShort ? (now - lastShort) : 0;

  if (lastShort && gap < usrDoubleMs) {
    // Second press: step the page back (the first one moved it on) and toggle.
    page = (uint8_t)((page + P_COUNT - 1) % P_COUNT);
    lastShort = 0;
    mediaLayer = !mediaLayer;
    Serial.printf("[usr] double press at %lu ms -> layer = %s\n",
                  (unsigned long)gap, mediaLayer ? "MEDIA" : "gamepad");
    return;
  }

  if (gap)
    Serial.printf("[usr] single press, %lu ms after the previous one (window %u)\n",
                  (unsigned long)gap, usrDoubleMs);
  lastShort = now;

#if HID_AVAILABLE
  // In game, with HID armed, the button flips through the game's sub-pages
  // rather than the diagnostic ones: those are what you look at while driving or
  // flying. To get out, disarm.
  if (hidArmed && gameFresh()) {
    page = P_GAME;
    gameSub = (uint8_t)((gameSub + 1) % gameSubCount());
    return;
  }
  // No game, but still on its page: one press shows the volume, the next moves
  // on. Without this the volume was two pages away and nothing said so.
  if (hidArmed && page == P_GAME && gameSub == 0) {
    gameSub = 1;
    return;
  }
  gameSub = 0;
#endif
  page = (uint8_t)((page + 1) % P_COUNT);
}


/* ==========================================================================
   MUSIC - what's playing, and how far in.

   Two layouts, because two different things are being shown. A track from a
   music player gets the spinning record and the artist's name. A browser tab
   gets neither: there is no record and no artist, only a video with a title
   long enough to want the whole width.
   ========================================================================== */

static uint16_t strHash(const char *s) {
  uint16_t h = 0;
  while (*s) h = (uint16_t)(h * 31u + (uint8_t)*s++);
  return h;
}

// Draws s at (x,y) inside a window w wide. If it doesn't fit it slides right to
// left and repeats, after standing still long enough for you to read the start.
// Text drawn left of x is simply drawn there: GFX clips at the screen edge, and
// whoever needs the space back paints over it afterwards.
static void drawScroll(const char *s, int16_t x, int16_t y, int16_t w, uint8_t slot) {
  int16_t tw = (int16_t)(6 * strlen(s));
  if (tw <= w) { oled->setCursor(x, y); oled->print(s); return; }

  // GFX wraps by default, and a title is always wider than the screen: left on,
  // the overflow lands on the NEXT line and writes over the artist and the bar.
  // It cost a confusing screenshot to find.
  oled->setTextWrap(false);

  uint16_t h = strHash(s);
  if (marq[slot].hash != h) { marq[slot].hash = h; marq[slot].t0 = millis(); }

  const int16_t  GAP  = 18;      // blank between the end and the repeat
  const uint32_t LEAD = 1200;    // stand still this long before moving off
  const uint32_t PXMS = 28;      // one pixel every this many ms (~36 px/s)

  uint32_t el  = millis() - marq[slot].t0;
  int16_t  off = 0;
  if (el > LEAD) off = (int16_t)(((el - LEAD) / PXMS) % (uint32_t)(tw + GAP));

  oled->setCursor(x - off, y);
  oled->print(s);
  oled->setCursor(x - off + tw + GAP, y);   // the copy chasing it
  oled->print(s);
  oled->setTextWrap(true);                  // as every other page expects it
}

/* The pixel version of drawScroll. Same timing, so a line that arrives as a
   strip slides exactly like one that arrives as text.

   Only the lit pixels are drawn - about a quarter of the area - so a full line
   is a couple of hundred drawPixel calls, which costs nothing next to the 15 ms
   the frame spends on the I2C bus. And because it only ever touches columns
   inside the window, nothing spills over the disc: no painting out afterwards.
*/
static void drawStrip(const uint8_t *bmp, uint16_t w,
                      int16_t x, int16_t y, int16_t win, uint8_t slot) {
  if (!w) return;
  const int16_t GAP = 18;
  const uint32_t LEAD = 1200, PXMS = 28;

  int16_t off = 0;
  int32_t total = (int32_t)w + GAP;
  if ((int16_t)w > win) {
    uint32_t el = millis() - marq[slot].t0;
    if (el > LEAD) off = (int16_t)(((el - LEAD) / PXMS) % (uint32_t)total);
  }

  for (int16_t sx = 0; sx < win; sx++) {
    int32_t src = (int32_t)sx + off;
    if ((int16_t)w > win) {
      src %= total;
      if (src >= (int32_t)w) continue;          // the gap between repeats
    } else if (src >= (int32_t)w) {
      break;
    }
    uint8_t col = bmp[src];
    if (!col) continue;
    for (uint8_t r = 0; r < 8; r++)
      if (col & (1 << r)) oled->drawPixel(x + sx, y + r, SSD1306_WHITE);
  }
}

static void printMmSs(int32_t sec) {
  if (sec < 0) sec = 0;
  oled->print(sec / 60);
  oled->print(':');
  int32_t r = sec % 60;
  if (r < 10) oled->print('0');
  oled->print(r);
}

// The whole track faded, the played part solid. No frame around it: an outline
// would cost two of the three pixels the bar has.
static void drawNpBar(int16_t x, int16_t y, int16_t w) {
  const int16_t h = 3;
  ditherRect(x, y, w, h, 5);
  if (npDur > 0) {
    int32_t p = npNowMs() / 1000;
    if (p > npDur) p = npDur;
    int16_t fw = (int16_t)((int32_t)w * p / npDur);
    if (fw > 0) oled->fillRect(x, y, fw, h, SSD1306_WHITE);
  }
}

// A record. The three marks are the point of it: a bare circle looks identical
// from one frame to the next, and the disc would seem to be standing still.
static void drawDisc(int16_t cx, int16_t cy, int16_t r) {
  static uint32_t tPrev  = 0;
  static uint32_t spinMs = 0;         // time spent PLAYING, so pause freezes it
  uint32_t now = millis();
  uint32_t dt  = now - tPrev;
  tPrev = now;
  if (dt > 250) dt = 250;             // back from a sleeping screen: don't leap
  if (npPlaying) spinMs += dt;

  int32_t ang = (int32_t)((spinMs * 72u / 1000u) % 360u);   // 72 deg/s, 12 rpm

  oled->drawCircle(cx, cy, r, SSD1306_WHITE);
  oled->fillCircle(cx, cy, 2, SSD1306_WHITE);
  for (uint8_t k = 0; k < 3; k++) {
    float a = (float)(ang + k * 120) * 0.01745329f;
    float c = cosf(a), sn = sinf(a);
    oled->drawLine((int16_t)(cx + c * 4),       (int16_t)(cy + sn * 4),
                   (int16_t)(cx + c * (r - 2)), (int16_t)(cy + sn * (r - 2)),
                   SSD1306_WHITE);
  }
}

// The target and its level, on one line. withIndex adds "5/6", which only
// fits where the line has the full width to itself.
static void drawAudioLine(int16_t x, int16_t y, bool withIndex) {
  oled->setTextWrap(false);
  oled->setCursor(x, y);
  if (!audFresh()) {
    oled->print(F("PC app not running"));
  } else {
    oled->print(audName[0] ? audName : "?");
    if (withIndex && audCount) {
      oled->print(' ');
      oled->print(audIdx);
      oled->print('/');
      oled->print(audCount);
    }
    oled->print(' ');
    if (audMute)          oled->print(F(" MUTE"));
    else if (audVol >= 0) { oled->print(' '); oled->print(audVol); oled->print('%'); }
    // Only worth saying when it is NOT the app's own slider - and never for
    // Windows, which is the mixer by definition. Silence means "the slider you
    // can see in the app is the one moving".
    if (audSrc == 'm' && audIdx != 1 && !audMute) oled->print(F(" mix"));
  }
  oled->setTextWrap(true);
}

// The volume on its own page: name, place in the list, and a bar you can read
// from across the room.
void drawAudioPicker() {
  oled->setTextSize(1);
  drawAudioLine(0, gTop + 1, true);
  if (!audFresh()) {
    oled->setCursor(0, gTop + 12);
    oled->print(F("start it to choose"));
    return;
  }
  if (audMute || audVol < 0) return;
  const int16_t y = gTop + 12, h = 8;
  ditherRect(0, y, SCREEN_W, h, 5);
  int16_t fw = (int16_t)((int32_t)SCREEN_W * audVol / 100);
  if (fw > 0) oled->fillRect(0, y, fw, h, SSD1306_WHITE);
}

void drawMusic() {
  oled->setTextSize(1);

  if (!npFresh() || !npTitle[0]) {
    oled->setCursor(0, gTop + 2);
    oled->print(npFresh() ? F("nothing playing") : F("PC app not running"));
    // Nothing playing is no reason to hide the volume: this page is where you
    // come to change it, and the knob works here whether or not there's music.
    drawAudioLine(0, gTop + 13, true);
    return;
  }

  if (npKind == 'y') {
    // A video: title across the whole width, then the time, then the bar.
    // Pixels if the PC sent them - that is the only way a Russian title is
    // readable rather than spelled out in Latin - and the text otherwise.
    if (npTitleW) drawStrip(npTitleBmp, npTitleW, 0, gTop + 1, SCREEN_W, 2);
    else          drawScroll(npTitle,            0, gTop + 1, SCREEN_W, 0);

    // Touch the volume and it takes this line for a couple of seconds. The
    // time comes back on its own: a volume you changed is what you want to see
    // right then, and not a moment longer.
    if (audShowing()) {
      drawAudioLine(0, gTop + 11, true);
    } else {
      oled->setCursor(0, gTop + 11);
      printMmSs(npNowMs() / 1000);
      if (npDur > 0) { oled->print(F(" / ")); printMmSs(npDur); }
      if (!npPlaying) oled->print(F("  ||"));
    }

    drawNpBar(0, gTop + 19, SCREEN_W);
    return;
  }

  const int16_t r  = 10;
  const int16_t cx = r + 1, cy = gTop + 11;
  const int16_t tx = 2 * r + 5;               // text starts clear of the disc
  const int16_t tw = SCREEN_W - tx;

  if (npTitleW) drawStrip(npTitleBmp, npTitleW, tx, gTop + 1, tw, 2);
  else          drawScroll(npTitle,            tx, gTop + 1, tw, 0);

  if (audShowing())     drawAudioLine(tx, gTop + 11, false);
  else if (npArtW)      drawStrip(npArtBmp, npArtW, tx, gTop + 11, tw, 3);
  else if (npArtist[0]) drawScroll(npArtist,        tx, gTop + 11, tw, 1);

  // Both lines slide off to the left, over where the disc goes. Paint the
  // column out and put the disc on top - cheaper than clipping by hand, and
  // GFX has no clip rectangle to ask for.
  oled->fillRect(0, gTop, tx, 22, SSD1306_BLACK);
  drawDisc(cx, cy, r);

  drawNpBar(tx, gTop + 19, tw);
}

void drawHid() {
#if HID_AVAILABLE
  // Cut, don't fold. "ARMED MEDIA  SW1=1 SW2=?" is 144 px wide on a 128 px
  // screen, and with wrapping on the tail landed on the line below - on top of
  // the volume target. Shorter labels so it fits, and no wrapping so a future
  // long line is merely clipped instead of wrecking the row under it.
  oled->setTextWrap(false);
  oled->setCursor(0, 11);
  oled->print(hidArmed ? F("ARM") : F("off"));
  if (mediaLayer) oled->print(F(" MEDIA"));
  oled->print(F(" S1="));
  if (sw2.pos) oled->print(sw2.pos); else oled->print('?');
  oled->print(F(" S2="));
  if (sw3.pos) oled->print(sw3.pos); else oled->print('?');
  // The last action and its counter were taken out of here: with nothing pressed
  // they read "- n=0", permanent noise for rarely useful information. They're
  // still there under the 'j' command on serial.

  // Second line: which app the knob is pointing at. Only worth the space while
  // the media layer is on - that is the only time the knob moves a volume.
  oled->setCursor(0, 21);
  if (!mediaLayer) {
    oled->print(F("USER x2 = media"));
  } else if (!audFresh()) {
    oled->print(F("vol: all (no app)"));
  } else {
    oled->print(audName[0] ? audName : "?");
    if (audCount) {
      oled->print(' ');
      oled->print(audIdx);
      oled->print('/');
      oled->print(audCount);
    }
    // A bar on the right is read at a glance; the number is for when you care
    // about the exact value.
    const int16_t bx = 84, bw = SCREEN_W - bx - 1;
    if (audMute) {
      oled->setCursor(bx + 6, 21);
      oled->print(F("MUTE"));
    } else if (audVol >= 0) {
      oled->drawRect(bx, 21, bw, 8, SSD1306_WHITE);
      int16_t fill = (int16_t)((int32_t)(bw - 2) * audVol / 100);
      if (fill > 0) oled->fillRect(bx + 1, 22, fill, 6, SSD1306_WHITE);
    }
  }
  oled->setTextWrap(true);              // as every other page expects it
#else
  oled->setCursor(0, 11); oled->print(F("HID unavailable"));
  oled->setCursor(0, 21); oled->print(F("(Mbed core)"));
#endif
}

// shift = how far the bar has already slid up, 0 (fully shown) to 9 (gone).
// The bar shrinks and the text walks off the top edge; GFX clips whatever ends
// up above y=0, so we can just draw at negative coordinates.
void header(int shift) {
  int h = 9 - shift;
  if (h > 0) oled->fillRect(0, 0, SCREEN_W, h, SSD1306_WHITE);
  oled->setTextSize(1);            // never inherited from the previous page
  oled->setTextColor(SSD1306_BLACK);
  oled->setCursor(2, 1 - shift);
  oled->print(PAGE_NAME[page]);
  // The latched layer shows in the header on every page: if it stays on and you
  // can't see it, you press a button and wonder why it skipped a track.
  const char *hidMark = "";
#if HID_AVAILABLE
  if (hidArmed) hidMark = "HID ";
#endif
  // On the GAME page we count the game's sub-pages, not the panel's: you can't
  // navigate the panel from there anyway, and two counters side by side used to
  // overlap (the right-hand block of the header starts at x=72 when HID is
  // armed, right on top of where drawGame drew the second one).
  unsigned cur = (unsigned)(page + 1), tot = (unsigned)P_COUNT;
  if (page == P_GAME && gameFresh()) {
    cur = (unsigned)(gameSub + 1);
    tot = (unsigned)gameSubCount();
  }
  char buf[16];
  snprintf(buf, sizeof(buf), "%s%s%u/%u", hidMark, mediaLayer ? "M " : "",
           cur, tot);
  oled->setCursor(SCREEN_W - 2 - 6 * (int)strlen(buf), 1 - shift);
  oled->print(buf);
  oled->setTextColor(SSD1306_WHITE);
}

void drawBtnRow(int y) {
  const int w = 17, h = 10;
  for (uint8_t i = 0; i < B_COUNT; i++) {
    int x = i * (w + 1);
    if (btn[i].level) {
      oled->fillRect(x, y, w, h, SSD1306_WHITE);
      oled->setTextColor(SSD1306_BLACK);
    } else {
      oled->drawRect(x, y, w, h, SSD1306_WHITE);
      oled->setTextColor(SSD1306_WHITE);
    }
    oled->setCursor(x + 3, y + 2);
    oled->print(btn[i].name);
    oled->setTextColor(SSD1306_WHITE);
  }
}

void drawOverview() {
  oled->setCursor(0, gTop + 1);
  oled->print(F("S2:"));
  if (sw2.pos) oled->print(sw2.pos); else oled->print('?');
  oled->print(F(" S3:"));
  if (sw3.pos) oled->print(sw3.pos); else oled->print('?');
  oled->print(F(" E:"));  oled->print(encValue);
  drawBtnRow(gTop + 11);
}

void drawSwRow(SwGroup &g, int y) {
  oled->setCursor(0, y);
  oled->print(g.name);
  oled->print(' ');
  if (g.pos) oled->print(g.pos); else oled->print('?');
  oled->print('/');
  oled->print(g.expect);
  oled->print(F("  c"));
  if (g.common >= 0) {
    oled->print(g.common + 1);
    if (!swCommonSure(g)) oled->print('?');   // still a guess, not confirmed
  } else {
    oled->print('?');
  }
  oled->print(F("  v"));                      // positions seen so far
  oled->print(swSeenCount(g));
}

void drawSwPage() {
  drawSwRow(sw2, 11);
  drawSwRow(sw3, 21);
}

void drawEnc() {
  char v[8];
  snprintf(v, sizeof(v), "%ld", (long)encValue);
  oled->setTextSize(2);
  oled->setCursor(2, gTop + 3);
  oled->print(v);
  oled->setTextSize(1);

  oled->setCursor(60, gTop + 2);
  oled->print(F("A"));  oled->print(digitalRead(PIN_ENC_A));
  oled->print(F(" B")); oled->print(digitalRead(PIN_ENC_B));
  oled->print(' ');
  if      (encDir > 0) oled->print(F("CW"));
  else if (encDir < 0) oled->print(F("CCW"));
  else                 oled->print(F("--"));

  oled->setCursor(60, gTop + 12);
  oled->print(F("m"));  oled->print(encMode);
  oled->print('/');     oled->print(encLastBurst);
  oled->print(F(" e")); oled->print(encErrors);
  if (btn[B_SW].level) oled->print(F(" *"));
}

void drawBtnPage() {
  drawBtnRow(gTop + 1);
  oled->setCursor(0, gTop + 14);
  for (uint8_t i = 0; i < B_COUNT; i++) { oled->print(btn[i].count); oled->print(' '); }
}

void drawPcf() {
  if (!pcfOK) {
    oled->setCursor(0, gTop + 3);
    oled->print(F("None on 0x20-0x27"));
    oled->setCursor(0, gTop + 13);
    oled->print(F("'i' = try again"));
    return;
  }
  const int w = 15, h = 10;
  for (uint8_t i = 0; i < 8; i++) {
    int x = i * 16;
    if (pcfBtn[i].level) {
      oled->fillRect(x, gTop + 1, w, h, SSD1306_WHITE);
      oled->setTextColor(SSD1306_BLACK);
    } else {
      oled->drawRect(x, gTop + 1, w, h, SSD1306_WHITE);
      oled->setTextColor(SSD1306_WHITE);
    }
    oled->setCursor(x + 5, gTop + 3);
    oled->print(i + 1);
    oled->setTextColor(SSD1306_WHITE);
  }
  oled->setCursor(0, gTop + 13);
  oled->print(F("0x"));   oled->print(pcfAddr, HEX);
  oled->print(F(" raw 0x"));
  if (pcfRaw < 16) oled->print('0');
  oled->print(pcfRaw, HEX);
  if (pcfFails) { oled->print(F(" e")); oled->print(pcfFails); }
}

void drawI2C() {
  oled->setCursor(0, gTop + 1);
  oled->print(F("Found: ")); oled->print(i2cCount);
  oled->print(F(" @"));       oled->print(i2cHz / 1000); oled->print(F("k"));
  oled->setCursor(0, gTop + 11);
  for (uint8_t i = 0; i < i2cCount && i < 6; i++) {
    oled->print(F("0x"));
    if (i2cFound[i] < 16) oled->print('0');
    oled->print(i2cFound[i], HEX);
    oled->print(' ');
  }
}

void drawInfo() {
  uint32_t s = millis() / 1000;
  oled->setCursor(0, gTop + 1);
  oled->print(s / 3600); oled->print(':');
  if ((s / 60) % 60 < 10) oled->print('0'); oled->print((s / 60) % 60); oled->print(':');
  if (s % 60 < 10) oled->print('0'); oled->print(s % 60);
  oled->print(F("  fps ")); oled->print(fps);

  oled->setCursor(0, gTop + 11);
  oled->print(F("0x"));    oled->print(oledAddr, HEX);
  oled->print(F(" 128x")); oled->print(oledH);
  oled->print(' ');        oled->print(F(CORE_NAME));
}

/* --------------------------------------------------------------------------
   Gradients on a 1-bit screen.

   The SSD1306 has no grey levels: a pixel is on or off. The impression of a
   gradient comes from DENSITY - how many pixels in an area are lit. Two recipes,
   both classics:

   ORDERED DITHER (Bayer 4x4) - pixels scattered by a threshold matrix. It gives
   17 levels and looks smooth even over small areas. It's what the rev bar uses.

   BEN-DAY DOTS - circles on a grid, radius following the level. That's the look
   of printed comics. On 128x32 a 4 px cell leaves room for only three dot sizes,
   so it comes out coarse - lovely as style, poor as precise information.

   Why Bayer and not error diffusion (Floyd-Steinberg): diffusion depends on
   neighbouring pixels already computed, so you can't draw an arbitrary area
   straight into the buffer, and it "crawls" from frame to frame when the value
   changes slightly. A threshold matrix is stable: the same level always draws
   the same pattern.
   -------------------------------------------------------------------------- */
static const uint8_t BAYER4[16] = {
   0,  8,  2, 10,
  12,  4, 14,  6,
   3, 11,  1,  9,
  15,  7, 13,  5
};

// level: 0 = empty, 16 = full
static void ditherRect(int x, int y, int w, int h, uint8_t level) {
  if (level == 0) return;
  if (level >= 16) { oled->fillRect(x, y, w, h, SSD1306_WHITE); return; }
  for (int j = 0; j < h; j++)
    for (int i = 0; i < w; i++)
      if (BAYER4[((j & 3) << 2) | (i & 3)] < level)
        oled->drawPixel(x + i, y + j, SSD1306_WHITE);
}

// Density rising from left to right: an actual gradient.
static void ditherRamp(int x, int y, int w, int h, uint8_t from, uint8_t to) {
  if (w <= 0) return;
  for (int i = 0; i < w; i++) {
    int lv = (int)from + ((int)to - (int)from) * i / (w > 1 ? w - 1 : 1);
    for (int j = 0; j < h; j++)
      if (BAYER4[((j & 3) << 2) | (i & 3)] < lv)
        oled->drawPixel(x + i, y + j, SSD1306_WHITE);
  }
}

// Comic-book dots. cell = the grid's side, in pixels.
static void halftoneRect(int x, int y, int w, int h, uint8_t level, int cell = 4) {
  if (level == 0) return;
  int r = (level * (cell / 2 + 1)) / 16;      // radius, 0..cell/2
  if (r <= 0) return;
  for (int cy = 0; cy + cell <= h + cell - 1; cy += cell)
    for (int cx = 0; cx + cell <= w + cell - 1; cx += cell) {
      int px = x + cx + cell / 2, py = y + cy + cell / 2;
      if (px > x + w - 1 || py > y + h - 1) continue;
      if (r == 1) oled->drawPixel(px, py, SSD1306_WHITE);
      else        oled->fillCircle(px, py, r - 1, SSD1306_WHITE);
    }
}

// The turn-signal arrow. Only drawn while lit - it blinks in the game anyway, so
// it blinks on the screen by itself.
static void blinkArrow(bool left, bool on) {
  if (!on) return;
  if (left) oled->fillTriangle(0, gTop + 7, 8, gTop + 1, 8, gTop + 13, SSD1306_WHITE);
  else      oled->fillTriangle(SCREEN_W - 1, gTop + 7, SCREEN_W - 9, gTop + 1,
                               SCREEN_W - 9, gTop + 13, SSD1306_WHITE);
}

static void drawRpmBar(int y) {
  const int BH = 6;
  if (gameRpmMax <= 0) return;
  bool over = (gameRedline > 0 && gameRpm >= gameRedline);
  if (over && (millis() % 300) < 150) {
    oled->fillRect(0, y, SCREEN_W, BH, SSD1306_WHITE);
    return;
  }
  int w = (int)((int64_t)gameRpm * SCREEN_W / gameRpmMax);
  if (w < 0) w = 0;
  if (w > SCREEN_W) w = SCREEN_W;
  oled->drawRect(0, y, SCREEN_W, BH, SSD1306_WHITE);
  ditherRamp(1, y + 1, w > 2 ? w - 2 : 0, BH - 2, 3, 16);
  if (gameRedline > 0 && gameRedline < gameRpmMax) {
    int rx = (int)((int64_t)gameRedline * SCREEN_W / gameRpmMax);
    if (rx > SCREEN_W - 1) rx = SCREEN_W - 1;
    oled->drawFastVLine(rx, y - 3, BH + 3, SSD1306_WHITE);
  }
}

// ---- cars: 3 pages ----------------------------------------------------
static void drawCarPage() {
  switch (gameSub) {
    case 0: {
      // Big speed, flanked by the turn-signal arrows. The arrows sit at the
      // screen edges, so the speed starts at x=11 and the gear stops at x=110 -
      // which is why it isn't perfectly centred, but they never overlap.
      blinkArrow(true,  gameBlink & 1);
      blinkArrow(false, gameBlink & 2);

      oled->setTextSize(2);
      oled->setCursor(11, gTop + 1);
      oled->print(gameSpd);

      oled->setTextSize(1);
      oled->setCursor(11 + 36, gTop + 8);
      oled->print(F("km/h"));

      oled->setTextSize(2);
      oled->setCursor(98, gTop + 1);
      if      (gameGear  < 0) oled->print('R');
      else if (gameGear == 0) oled->print('N');
      else if (gameGear < 10) oled->print(gameGear);
      else { oled->setTextSize(1); oled->setCursor(98, gTop + 5); oled->print(gameGear); }

      drawRpmBar((oledH >= 64) ? 44 : 26);
      break;
    }
    case 1:
      oled->setTextSize(1);
      oled->setCursor(0, gTop + 1);
      oled->print(F("FUEL "));
      if (gameFuel >= 0) { oled->print(gameFuel); oled->print('%'); } else oled->print('?');
      oled->setCursor(68, gTop + 1);
      oled->print(F("TEMP "));
      if (gameTmp) { oled->print(gameTmp); oled->print('C'); } else oled->print('?');

      oled->setCursor(0, gTop + 11);
      oled->print(F("TURBO "));
      oled->print(gameTurbo / 10); oled->print('.'); oled->print(gameTurbo % 10);
      oled->print('b');
      oled->setCursor(68, gTop + 11);
      oled->print(F("RPM ")); oled->print(gameRpm);
      break;
    default:
      oled->setTextSize(1);
      oled->setCursor(0, gTop + 1);
      oled->print(F("THR ")); oled->print(gameThr); oled->print('%');
      oled->setCursor(56, gTop + 1);
      oled->print(F("BRAKE ")); oled->print(gameBrk); oled->print('%');

      oled->setCursor(0, gTop + 11);
      if (gameRedline > 0) { oled->print(F("REDLINE ")); oled->print(gameRedline); }
      else                   oled->print(F("redline not learned"));
      break;
  }
}

// ---- aircraft: 4 pages ------------------------------------------------
static void drawAirPage() {
  oled->setTextSize(1);
  switch (gameSub) {
    case 0:
      // The order asked for: airspeed, vertical speed, altitude.
      oled->setTextSize(2);
      oled->setCursor(0, gTop + 1);
      oled->print(gameKts);
      oled->setTextSize(1);
      oled->print(F("kt"));

      oled->setCursor(62, gTop + 1);
      oled->print(F("VS "));
      if (gameVs > 0) oled->print('+');
      oled->print(gameVs);

      oled->setCursor(62, gTop + 11);
      oled->print(F("ALT ")); oled->print(gameAltFt);
      break;
    case 1:
      oled->setCursor(0, gTop + 1);
      oled->print(F("COM1 ")); oled->print(gameC1[0] ? gameC1 : "---");
      oled->setCursor(0, gTop + 11);
      oled->print(F("COM2 ")); oled->print(gameC2[0] ? gameC2 : "---");
      if (gameSqk[0]) { oled->setCursor(92, gTop + 11); oled->print(gameSqk); }
      break;
    case 2:
      oled->setTextSize(2);
      oled->setCursor(0, gTop + 1);
      oled->print(gameSqk[0] ? gameSqk : "----");
      oled->setTextSize(1);
      oled->setCursor(62, gTop + 1);
      if (gameHdg >= 0) { oled->print(F("HDG ")); oled->print(gameHdg); }
      oled->setCursor(62, gTop + 11);
      oled->print(F("ALT ")); oled->print(gameAltFt);
      break;
    default:
      oled->setCursor(0, gTop + 1);
      oled->print(F("AUTOPILOT"));
      oled->setCursor(0, gTop + 11);
      oled->print(gameAp[0] ? gameAp : "off");
      break;
  }
}


// ---- War Thunder aircraft: 3 pages ------------------------------------
static void drawWtAirPage() {
  oled->setTextSize(1);
  switch (gameSub) {
    case 0:
      oled->setTextSize(2);
      oled->setCursor(0, gTop + 1);
      oled->print(gameKts);
      oled->setTextSize(1);
      oled->print(F("kt"));
      oled->setCursor(62, gTop + 1);
      oled->print(F("VS "));
      if (gameVs > 0) oled->print('+');
      oled->print(gameVs);
      oled->setCursor(62, gTop + 11);
      oled->print(F("ALT ")); oled->print(gameAltFt);
      break;
    case 1:
      // What a pilot in this game actually watches: how hard he's pulling and
      // how close the wing is to letting go.
      oled->setTextSize(2);
      oled->setCursor(0, gTop + 1);
      oled->print(gameG / 10); oled->print('.'); oled->print(gameG % 10);
      oled->setTextSize(1);
      oled->print(F("G"));
      oled->setCursor(62, gTop + 1);
      oled->print(F("AoA ")); oled->print(gameAoa);
      oled->setCursor(62, gTop + 11);
      oled->print(F("THR ")); oled->print(gameThr); oled->print('%');
      break;
    default:
      oled->setCursor(0, gTop + 1);
      oled->print(F("FUEL "));
      if (gameFuel >= 0) { oled->print(gameFuel); oled->print('%'); } else oled->print('?');
      oled->setCursor(62, gTop + 1);
      oled->print(F("TEMP ")); oled->print(gameTmp); oled->print('C');
      oled->setCursor(0, gTop + 11);
      if (gameHdg >= 0) { oled->print(F("HDG ")); oled->print(gameHdg); }
      oled->setCursor(62, gTop + 11);
      oled->print(F("RPM ")); oled->print(gameRpm);
      break;
  }
}

// ---- War Thunder ground: 2 pages --------------------------------------
static void drawWtGndPage() {
  if (gameSub == 0) {
    oled->setTextSize(2);
    oled->setCursor(0, gTop + 1);
    oled->print(gameSpd);
    oled->setTextSize(1);
    oled->setCursor(44, gTop + 1);
    oled->print(F("km/h"));

    oled->setCursor(44, gTop + 11);
    if (gameCrew[0]) { oled->print(F("CREW ")); oled->print(gameCrew); }

    oled->setTextSize(2);
    oled->setCursor(SCREEN_W - 12, gTop + 1);
    if      (gameGear  < 0) oled->print('R');
    else if (gameGear == 0) oled->print('N');
    else if (gameGear < 10) oled->print(gameGear);
    else { oled->setTextSize(1); oled->setCursor(SCREEN_W - 12, gTop + 5); oled->print(gameGear); }

    drawRpmBar((oledH >= 64) ? 44 : 26);
  } else {
    oled->setTextSize(1);
    oled->setCursor(0, gTop + 1);
    oled->print(F("RPM "));  oled->print(gameRpm);
    oled->setCursor(68, gTop + 1);
    oled->print(F("TEMP ")); oled->print(gameTmp); oled->print('C');
    oled->setCursor(0, gTop + 11);
    oled->print(gameTxt[0] ? gameTxt : "-");
  }
}

void drawGame() {
  if (!gameFresh()) {
    if (gameAudioSub()) { drawAudioPicker(); return; }
    oled->setCursor(0, gTop + 2);
    oled->print(F("no game"));
    oled->setCursor(0, gTop + 12);
    oled->print(F("USER = volume"));
    return;
  }

  // The source and the sub-page number, in the header. The source is cut to 6
  // characters: the right of the header already holds the HID/M marks and the
  // page counter.
  oled->setTextColor(SSD1306_BLACK);
  oled->setTextSize(1);
  oled->setCursor(28, 1);
  char src6[7];
  strncpy(src6, gameSrc, 6);
  src6[6] = 0;
  oled->print(src6);
  oled->setTextColor(SSD1306_WHITE);

  if (gameSub >= gameSubCount()) gameSub = 0;
  if (gameAudioSub()) { drawAudioPicker(); return; }
  switch (gameKind) {
    case 'a': drawAirPage();    break;
    case 'w': drawWtAirPage();  break;
    case 'g': drawWtGndPage();  break;
    default:  drawCarPage();    break;
  }

  oled->setTextSize(1);            // don't leave size 2 behind us
}

/* --------------------------------------------------------------------------
   SCREEN MIRROR - the OLED, live, in the PC app.

   The panel usually sits where you can't comfortably look at it: behind a
   wheel, under a desk, inside a rig. So the PC app can show exactly what the
   128x32 panel shows, scaled up.

   What goes over the wire is the frame buffer itself, not a description of it.
   A "mimic" that redrew the pages on the PC would be a second implementation of
   every screen, and the two would drift apart the first time one of them
   changed. This can't drift: it's the same bytes the SSD1306 is given.

   One line per frame, base64 so it stays printable and can't be confused with
   the telemetry lines going the other way:

     !FB 128 32 <684 characters>

   512 bytes per frame, 684 once encoded. At 20 frames a second that's ~14 KB/s
   - nothing for USB CDC, where the baud rate is a fiction anyway. It stays off
   until the PC asks for it with 'o'.

   THE FRAME IS A SNAPSHOT, not the live buffer. The first version read straight
   out of the OLED's buffer while core1 was drawing into it, and the result was
   visible: catch it between clearDisplay() and the last drawPixel() and you
   mirror a half-erased frame. It looked like glitching on the PC while the
   panel itself was perfectly fine.

   So core1 copies the finished frame here, right after it has pushed it to the
   screen, and core0 sends that. The mutex is held only for the two 512-byte
   copies (microseconds), never across the serial write - otherwise a slow host
   would stall rendering.
   -------------------------------------------------------------------------- */
bool     mirrorOn = false;
uint16_t mirrorMs = 50;               // 20 frames a second

// The handover. Big enough for a 128x64 panel, so 'h' can't overflow it.
static uint8_t mirrorSnap[SCREEN_W * 64 / 8];
static size_t  mirrorSnapLen   = 0;
static bool    mirrorSnapFresh = false;
auto_init_mutex(mirrorMux);

// Called from core1 with a frame it has just finished drawing.
void mirrorCapture() {
  if (!mirrorOn || !oled) return;
  size_t n = (size_t)SCREEN_W * oledH / 8;
  if (n > sizeof(mirrorSnap)) return;
  mutex_enter_blocking(&mirrorMux);
  memcpy(mirrorSnap, oled->getBuffer(), n);
  mirrorSnapLen   = n;
  mirrorSnapFresh = true;
  mutex_exit(&mirrorMux);
}

static const char B64[] =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

// Three bytes in, four characters out, straight onto Serial - no buffer of our
// own, because 684 characters of it would be a lot of RAM to hold for nothing.
static void b64Emit(const uint8_t *data, size_t n) {
  for (size_t i = 0; i < n; i += 3) {
    uint32_t v = (uint32_t)data[i] << 16;
    if (i + 1 < n) v |= (uint32_t)data[i + 1] << 8;
    if (i + 2 < n) v |= data[i + 2];
    Serial.write(B64[(v >> 18) & 63]);
    Serial.write(B64[(v >> 12) & 63]);
    Serial.write(i + 1 < n ? B64[(v >> 6) & 63] : '=');
    Serial.write(i + 2 < n ? B64[v & 63] : '=');
  }
}

void mirrorService() {
  static uint32_t tNext = 0;
  static uint8_t  frame[sizeof(mirrorSnap)];
  if (!mirrorOn || !oledOK || !oled) return;
  uint32_t now = millis();
  if ((int32_t)(now - tNext) < 0) return;

  size_t n = (size_t)SCREEN_W * oledH / 8;

  // A dark panel is a dark mirror. render() returns early while the screen is
  // off, so no snapshot is coming - we send a blank frame on purpose rather
  // than leaving the last picture frozen on the PC. Once, though: a sleeping
  // panel has nothing new to say, and repeating it 20 times a second would
  // spend bandwidth on an unchanging black rectangle.
  static bool blankSent = false;
  bool blank = (oledSleep == 2);
  if (blank) {
    if (blankSent) return;
    blankSent = true;
  } else {
    blankSent = false;
  }
  if (!blank) {
    mutex_enter_blocking(&mirrorMux);
    bool have = mirrorSnapFresh;
    if (have) {
      n = mirrorSnapLen;
      memcpy(frame, mirrorSnap, n);
      mirrorSnapFresh = false;
    }
    mutex_exit(&mirrorMux);
    // Nothing new drawn since the last frame we sent: say nothing. Repeating
    // ourselves would only spend bandwidth to redraw an identical picture.
    if (!have) return;
  }
  tNext = now + mirrorMs;

  Serial.print(F("!FB "));
  Serial.print(SCREEN_W);
  Serial.print(' ');
  Serial.print(oledH);
  Serial.print(' ');
  if (blank) {
    for (size_t i = 0; i < (n + 2) / 3 * 4; i++) Serial.write('A');   // all zeroes
  } else {
    b64Emit(frame, n);
  }
  Serial.println();
}

void render() {
  I2C_GUARD;
  if (!oledOK || !oled) return;
  if (oledSleep == 2) return;        // panel off: no point in the I2C traffic
  uint32_t t0 = micros();
  oled->clearDisplay();
  // EVERY frame starts from the same state. Adafruit_GFX keeps text size and
  // colour between calls, so a page that exited with setTextSize(2) made the
  // next frame's header draw huge. You could see it on the GAME page: with the
  // gear under 10 it drew big, and the title grew along with it.
  oled->setTextSize(1);
  oled->setTextColor(SSD1306_WHITE);

  // The header retracts after OLED_RETRACT_MS of quiet and the content moves up
  // into its place. On 128x32 that bar is 9 of 32 pixels - most of a third of
  // the screen spent on a title you already know.
  //
  // It stays while game telemetry is flowing: there the header carries the
  // source and the sub-page, which is exactly what you want while driving.
  // This applies in game mode too: while driving you aren't pressing anything,
  // so after OLED_RETRACT_MS the header goes and the gauges get the whole
  // screen. Press USER and it comes back for another five seconds, with the
  // sub-page counter, which is when you actually want to read it.
  // Only the bar slides, and it slides one pixel per frame - the 9 px trip
  // takes about a quarter of a second at 40 FPS. Frame-based rather than
  // time-based on purpose: the travel is then the same handful of frames
  // whatever the refresh rate, and it never jumps two pixels because a frame
  // ran late. The content below does not move.
  // Arriving on an audio page counts as touching it: you want to see WHICH app
  // the knob is on before you turn it, not after.
  static bool audioPageWas = false;
  bool audioPageNow = audioPage();
  if (audioPageNow && !audioPageWas) audTouch = millis();
  audioPageWas = audioPageNow;

  static uint8_t hdrShift = 0;                 // 0 = fully shown, 9 = gone
  uint8_t target = ((millis() - tLastAct) < OLED_RETRACT_MS) ? 0 : 9;
  if      (hdrShift < target) hdrShift++;
  else if (hdrShift > target) hdrShift--;

  if (hdrShift < 9) header(hdrShift);
  switch (page) {
    case P_OVERVIEW: drawOverview(); break;
    case P_SW:       drawSwPage();   break;
    case P_ENC:      drawEnc();      break;
    case P_BTN:      drawBtnPage();  break;
    case P_PCF:      drawPcf();       break;
    case P_I2C:      drawI2C();      break;
    case P_INFO:     drawInfo();     break;
    case P_HID:      drawHid();      break;
    case P_MUSIC:    drawMusic();    break;
    case P_GAME:     drawGame();     break;
  }
  oled->display();
  mirrorCapture();                   // hand core0 a frame that's actually done
  renderUs = micros() - t0;
  frames++;
}

/* ==========================================================================
   SERIAL DEBUG
   ========================================================================== */
void printI2CReport() {
  Serial.print(F("I2C scan on SDA=GPIO")); Serial.print(PIN_SDA);
  Serial.print(F(" SCL=GPIO"));           Serial.print(PIN_SCL);
  Serial.print(F(" @"));                  Serial.print(i2cHz / 1000);
  Serial.print(F("kHz -> "));             Serial.print(i2cCount);
  Serial.println(F(" device(s)"));
  for (uint8_t i = 0; i < i2cCount; i++) {
    Serial.print(F("  found at 0x"));
    if (i2cFound[i] < 16) Serial.print('0');
    Serial.println(i2cFound[i], HEX);
  }

  // A second scan, bit-banged, independent of the I2C peripheral. The two
  // methods check each other: if one finds something and the other doesn't, we
  // know which of them to trust on this core.
  Wire.end();
  delay(2);
  uint8_t bbCount = 0;
  Serial.print(F("  bit-banged check: "));
  for (uint8_t a = 0x08; a <= 0x77; a++) {
    if (bbProbe(PIN_SDA, PIN_SCL, a)) {
      Serial.print(F("0x")); Serial.print(a, HEX); Serial.print(' ');
      bbCount++;
    }
  }
  if (!bbCount) Serial.print(F("nothing"));
  Serial.println();
  wireRestart();

  if (i2cCount == 0 && bbCount == 0)
    Serial.println(F("  NOTHING on the bus: check 3V3, GND, SDA(GPIO4), SCL(GPIO5)."));
  Serial.print(F("OLED: "));
  if (oledOK) {
    Serial.print(F("initialised at 0x")); Serial.print(oledAddr, HEX);
    Serial.print(F(", 128x"));            Serial.println(oledH);
  } else {
    Serial.println(F("NOT INITIALISED"));
  }

  Serial.print(F("PCF8574: "));
  if (pcfOK) {
    Serial.print(F("0x"));           Serial.print(pcfAddr, HEX);
    Serial.print(F(", rest 0x"));    Serial.print(pcfIdle, HEX);
    Serial.print(F(", raw 0x"));     Serial.print(pcfRaw, HEX);
    if (pcfFails) { Serial.print(F(", failed reads ")); Serial.print(pcfFails); }
    Serial.println();
  } else {
    Serial.println(F("not found on 0x20-0x27"));
  }
}

// Raw dump of every pin in use - handy for finding a miswired lead.
void printPinReport() {
  Serial.println(F("--- raw pin state (1 = HIGH, i.e. not pressed) ---"));
  for (uint8_t i = 0; i < sw2.n; i++) {
    Serial.print(F("  SW2_")); Serial.print(i + 1);
    Serial.print(F(" GPIO"));  Serial.print(sw2.pins[i]);
    Serial.print(F(" = "));    Serial.println(digitalRead(sw2.pins[i]));
  }
  for (uint8_t i = 0; i < sw3.n; i++) {
    Serial.print(F("  SW3_")); Serial.print(i + 1);
    Serial.print(F(" GPIO"));  Serial.print(sw3.pins[i]);
    Serial.print(F(" = "));    Serial.println(digitalRead(sw3.pins[i]));
  }
  for (uint8_t i = 0; i < B_COUNT; i++) {
    Serial.print(F("  "));     Serial.print(btn[i].name);
    Serial.print(F(" GPIO"));  Serial.print(btn[i].pin);
    Serial.print(F(" = "));    Serial.println(digitalRead(btn[i].pin));
  }
  Serial.print(F("  ENC_A GPIO")); Serial.print(PIN_ENC_A);
  Serial.print(F(" = "));          Serial.println(digitalRead(PIN_ENC_A));
  Serial.print(F("  ENC_B GPIO")); Serial.print(PIN_ENC_B);
  Serial.print(F(" = "));          Serial.println(digitalRead(PIN_ENC_B));
}

// The distribution of stops tells you straight away what kind of encoder you
// have: one dominant state = 4 quarters per detent; two opposite states at
// roughly half and half = 2 quarters per detent. It's a more trustworthy test
// than measuring detent length, because it doesn't depend on how fast you turn.
void printEncStatus() {
  noInterrupts();
  int8_t   acc = encAcc;
  uint32_t er  = encErrors;
  interrupts();

  Serial.println(F("--- encoder state ---"));
  Serial.print(F("  mode = "));
  if (encMode == 0) Serial.print(F("DETENT (rest -> rest)"));
  else { Serial.print(encMode); Serial.print(F(" quarters/detent")); }
  Serial.print(F(",  accumulator = ")); Serial.print(acc);
  Serial.print(F(",  errors = "));      Serial.println(er);
  {
    noInterrupts(); uint32_t g = encGuessed; interrupts();
    Serial.print(F("  steps with a guessed direction = ")); Serial.print(g);
    Serial.println(encGuess ? F("  (recovery on, 'g')")
                            : F("  (recovery OFF, 'g')"));
  }
  Serial.print(F("  last detent measured = ")); Serial.print(encLastBurst);
  Serial.println(F(" quarters"));

  noInterrupts();
  uint32_t ia = encChgA, ib = encChgB;
  interrupts();
  Serial.print(F("  level changes: A(GPIO")); Serial.print(PIN_ENC_A);
  Serial.print(F(") = "));                         Serial.print(ia);
  Serial.print(F("   B(GPIO"));                    Serial.print(PIN_ENC_B);
  Serial.print(F(") = "));                         Serial.println(ib);
  if (ia + ib >= 8) {
    if (ia == 0 || ib == 0)
      Serial.println(F("  >> ONE CHANNEL IS DEAD: wire, solder joint or wrong pin."));
    else if (ia > ib * 3 || ib > ia * 3)
      Serial.println(F("  >> the channels are badly unbalanced: one is dropping edges."));
  }

  uint16_t total = 0;
  for (uint8_t i = 0; i < 4; i++) total += encRestVotes[i];
  Serial.println(F("  where the encoder stopped (A B):"));
  for (uint8_t i = 0; i < 4; i++) {
    Serial.print(F("    ")); Serial.print((i >> 1) & 1); Serial.print(' '); Serial.print(i & 1);
    Serial.print(F("   ")); Serial.print(encRestVotes[i]);
    if (total) {
      Serial.print(F("  (")); Serial.print((uint16_t)(encRestVotes[i] * 100UL / total));
      Serial.print(F("%)"));
    }
    if (i == encRest) Serial.print(F("   <- chosen as rest"));
    Serial.println();
  }
  Serial.print(F("  confidence in rest: "));
  Serial.println(encRestSure ? F("YES") : F("NO (only the cycle rule is used)"));

  if (total >= 8) {
    uint16_t p1 = (uint16_t)(encRestVotes[encRest] * 100UL / total);
    uint16_t p2 = (uint16_t)(encRestVotes[encRest ^ 0x03] * 100UL / total);
    Serial.print(F("  >> "));
    if (p1 >= 70)
      Serial.println(F("one state dominates -> encoder with 4 quarters/detent, mode 4"));
    else if (p1 + p2 >= 80 && p2 >= 25)
      Serial.println(F("two opposite states, roughly half and half -> 2 quarters/detent,"
                       " try mode 2 (press 'e')"));
    else
      Serial.println(F("stops are scattered: either it often stops between detents,"
                       " or edges are being lost. Run 'l'."));
  } else {
    Serial.println(F("  >> too few stops to draw a conclusion;"
                     " turn it a few clicks and try again."));
  }
}

// Prints the raw log: every transition with the time since the previous one.
// That shows you the rest state directly, how many transitions one click has,
// and whether there's chatter (several transitions tens of microseconds apart).
void encDumpLog() {
  noInterrupts();
  encLogOn = false;
  uint8_t n = encLogHead;
  interrupts();

  Serial.print(F("--- encoder log: ")); Serial.print(n);
  Serial.println(F(" transitions ---"));
  if (n == 0) {
    Serial.println(F("  nothing recorded"));
    return;
  }
  Serial.println(F("   #   dt(us)  irq   A B   delta   acc"));
  int16_t acc = 0;
  for (uint8_t i = 0; i < n; i++) {
    uint32_t dt = (i == 0) ? 0 : (encLogUs[i] - encLogUs[i - 1]);
    acc = (int16_t)(acc + encLogD[i]);
    Serial.print(F("  "));
    if (i < 10) Serial.print(' ');
    Serial.print(i);
    Serial.print(F("  "));
    if (dt < 10)      Serial.print(F("     "));
    else if (dt < 100)   Serial.print(F("    "));
    else if (dt < 1000)  Serial.print(F("   "));
    else if (dt < 10000) Serial.print(F("  "));
    else                 Serial.print(' ');
    Serial.print(dt);
    Serial.print(F("    "));
    Serial.write((char)encLogTrig[i]);
    Serial.print(F("    "));
    Serial.print((encLogState[i] >> 1) & 1); Serial.print(' ');
    Serial.print(encLogState[i] & 1);
    Serial.print(F("    "));
    if (encLogD[i] > 0)      Serial.print(F("+1"));
    else if (encLogD[i] < 0) Serial.print(F("-1"));
    else                     Serial.print(F("!!"));   // impossible transition
    Serial.print(F("    "));
    Serial.println(acc);
  }
  Serial.println(F("  '!!' = both bits changed at once: an edge was lost."));
  Serial.print(F("  detected rest = "));
  Serial.print((encRest >> 1) & 1); Serial.println(encRest & 1);
}

void printSwMatrix(SwGroup &g) {
  Serial.print(F("--- ")); Serial.print(g.name);
  Serial.print(F(" connectivity matrix, pairs=")); Serial.print(g.pairs);
  Serial.print(F(", common="));
  if (g.common >= 0) {
    Serial.print(g.common + 1);
    Serial.print(F(" (GPIO")); Serial.print(g.pins[g.common]); Serial.print(')');
  } else {
    Serial.print(F("unknown"));
  }
  Serial.print(swCommonSure(g) ? F(" CONFIRMED") : F(" assumed"));
  Serial.print(F(", position "));
  if (g.pos) Serial.print(g.pos); else Serial.print('?');
  Serial.print('/'); Serial.println(g.expect);

  Serial.print(F("   positions seen: ")); Serial.print(swSeenCount(g));
  Serial.print(F(" of ")); Serial.print(g.expect); Serial.print(F("  ->"));
  for (uint8_t j = 0; j < g.n; j++) {
    if ((int8_t)j == g.common) continue;
    Serial.print(' ');
    Serial.print(swPosNumber(g, (int8_t)j));
    Serial.print((g.seenThrows & (1u << j)) ? '.' : '?');
  }
  if (g.reverse) Serial.print(F("   (numbering reversed)"));
  Serial.println();

  for (uint8_t i = 0; i < g.n; i++) {
    Serial.print(F("   pin ")); Serial.print(i + 1);
    Serial.print(F(" GPIO"));   Serial.print(g.pins[i]);
    if ((int8_t)i == g.common) Serial.print(F("  [common]"));
    Serial.print(F("  shorted to:"));
    bool any = false;
    for (uint8_t j = 0; j < g.n; j++)
      if (g.row[i] & (1u << j)) { Serial.print(' '); Serial.print(j + 1); any = true; }
    if (!any) Serial.print(F(" -"));
    Serial.println();
  }

  if (g.pairs == 0)
    Serial.println(F("   >> no connection RIGHT NOW: the wiper is between positions,"
                     " or the switch isn't soldered."));
  else if (g.pairs > 1)
    Serial.println(F("   >> several pairs at once: either the switch shorts positions"
                     " as it passes, or the two poles don't act as one."));

  if (!swCommonSure(g))
    Serial.println(F("   >> the common pin is still only assumed. Move the switch"
                     " through at least two different positions and it settles itself."));
  if (swSeenCount(g) < g.expect)
    Serial.println(F("   >> not every position has been seen yet. The ones marked '?'"
                     " above have never been touched."));
}

void printBusCheck() {
  I2C_GUARD;
  Serial.println(F("--- I2C bus electrical test ---"));

  // Release the pins from the I2C peripheral, otherwise pinMode() has no effect
  // on them and the measurement would always say "NO", whatever is connected.
  Wire.end();
  delay(5);
  bool pSDA = hasExternalPullup(PIN_SDA);
  bool pSCL = hasExternalPullup(PIN_SCL);

  Serial.print(F("  GPIO")); Serial.print(PIN_SDA); Serial.print(F(" (SDA): external pull-up = "));
  Serial.print(pSDA ? F("YES") : F("NO"));
  Serial.print(F("   (at boot: ")); Serial.print(bootPuSDA ? F("YES") : F("NO")); Serial.println(')');
  Serial.print(F("  GPIO")); Serial.print(PIN_SCL); Serial.print(F(" (SCL): external pull-up = "));
  Serial.print(pSCL ? F("YES") : F("NO"));
  Serial.print(F("   (at boot: ")); Serial.print(bootPuSCL ? F("YES") : F("NO")); Serial.println(')');

  if (!pSDA && !pSCL)
    Serial.println(F("  >> No pull-ups at all: the module is NOT connected or NOT"
                     " powered. Check 3V3 and GND before anything else."));
  else if (pSDA != pSCL)
    Serial.println(F("  >> Only one wire has a pull-up: SDA or SCL is probably loose"
                     " or swapped. On J2 the order is 3V3, GND, SDA, SCL."));
  else
    Serial.println(F("  >> Both have pull-ups: the module is powered and wired."));

  // Look for the screen on other pins too, in case the wires are on another GPIO.
  static const uint8_t CAND[] = { 0, 1, 2, 3, 4, 5, 6, 7 };
  Serial.println(F("  looking for 0x3C/0x3D on every free pin pair..."));
  uint8_t hits = 0;
  for (uint8_t i = 0; i < sizeof(CAND); i++) {
    for (uint8_t j = 0; j < sizeof(CAND); j++) {
      if (i == j) continue;
      for (uint8_t a = 0x3C; a <= 0x3D; a++) {
        if (bbProbe(CAND[i], CAND[j], a)) {
          Serial.print(F("  >> ANSWER 0x")); Serial.print(a, HEX);
          Serial.print(F(" with SDA=GPIO")); Serial.print(CAND[i]);
          Serial.print(F(" SCL=GPIO"));    Serial.println(CAND[j]);
          hits++;
        }
      }
    }
  }
  if (!hits) Serial.println(F("  >> Nothing on any pair. Power, or a dead module."));

  // the candidate pins were left as inputs with pull-ups; put them back to work
  for (uint8_t i = 0; i < sw2.n; i++) pinMode(sw2.pins[i], INPUT_PULLUP);
  wireRestart();
}

// If a slave is stuck holding SDA low, we free it with 9 clock pulses followed
// by a STOP.
void busRecover() {
  I2C_GUARD;
  Serial.println(F("--- bus recovery (9 SCL pulses) ---"));
  Wire.end();
  delay(2);
  bbRelease(PIN_SDA); bbRelease(PIN_SCL);
  delayMicroseconds(BB_DLY);
  Serial.print(F("  before: SDA=")); Serial.print(digitalRead(PIN_SDA));
  Serial.print(F(" SCL="));           Serial.println(digitalRead(PIN_SCL));
  for (uint8_t i = 0; i < 9; i++) {
    bbPull(PIN_SCL);    delayMicroseconds(BB_DLY);
    bbRelease(PIN_SCL); delayMicroseconds(BB_DLY);
  }
  bbPull(PIN_SDA);    delayMicroseconds(BB_DLY);   // STOP
  bbRelease(PIN_SCL); delayMicroseconds(BB_DLY);
  bbRelease(PIN_SDA); delayMicroseconds(BB_DLY);
  Serial.print(F("  after:  SDA=")); Serial.print(digitalRead(PIN_SDA));
  Serial.print(F(" SCL="));           Serial.println(digitalRead(PIN_SCL));
  if (!digitalRead(PIN_SDA))
    Serial.println(F("  >> SDA still low: a short to ground, or a dead module."));
  wireRestart();
}

// Sends raw SSD1306 commands through Wire and reports the error code. If the
// panel answers here but still shows nothing, the problem is in the screen or
// its power, not on the bus.
void oledRawTest() {
  I2C_GUARD;
  Serial.println(F("--- raw SSD1306 commands ---"));
  const uint8_t addr = (forceAddr >= 0) ? (uint8_t)forceAddr : oledAddr;
  const uint8_t cmds[3]     = { 0xAE, 0xA5, 0xAF };
  const char   *descr[3]    = { "display OFF", "all pixels on (ignores RAM)", "display ON" };
  for (uint8_t i = 0; i < 3; i++) {
    Wire.beginTransmission(addr);
    Wire.write((uint8_t)0x00);            // control byte: a command follows
    Wire.write(cmds[i]);
    uint8_t err = Wire.endTransmission();
    Serial.print(F("  0x"));  Serial.print(cmds[i], HEX);
    Serial.print(' ');        Serial.print(descr[i]);
    Serial.print(F(" -> endTransmission=")); Serial.print(err);
    Serial.println(err == 0 ? F(" (ACK)") : F(" (NO ACK)"));
    delay(700);
  }
  Serial.println(F("  If you saw the screen fully lit, the panel is alive."));
  Wire.beginTransmission(addr);
  Wire.write((uint8_t)0x00);
  Wire.write((uint8_t)0xA4);              // back to the contents of RAM
  Wire.endTransmission();
  if (oledOK && oled) oled->display();
}

// Visual test: fill, checkerboard, border. Shows whether the configured height
// is wrong.
void oledVisualTest() {
  I2C_GUARD;
  if (oledOK && oled) {
    // 1. smooth gradient, left -> right
    oled->clearDisplay();
    oled->setTextSize(1);
    oled->setTextColor(SSD1306_WHITE);
    oled->setCursor(0, 0);
    oled->print(F("gradient (Bayer)"));
    ditherRamp(0, 10, SCREEN_W, 20, 0, 16);
    oled->display();
    delay(1600);

    // 2. the 17 levels, so you can see the grain of each one
    oled->clearDisplay();
    oled->setCursor(0, 0);
    oled->print(F("17 levels"));
    for (uint8_t lv = 0; lv <= 16; lv++)
      ditherRect(lv * 7, 10, 7, 20, lv);
    oled->display();
    delay(1600);

    // 3. comic-book dots
    oled->clearDisplay();
    oled->setCursor(0, 0);
    oled->print(F("Ben-Day dots"));
    for (uint8_t k = 0; k < 8; k++)
      halftoneRect(k * 16, 10, 16, 20, (uint8_t)(k * 16 / 7));
    oled->display();
    delay(1800);
  }
  Serial.println(F("--- screen visual test ---"));
  if (!oledOK || !oled) {
    Serial.println(F("  OLED not initialised. Try 'x', then 'i'."));
    return;
  }
  oled->clearDisplay();
  oled->fillRect(0, 0, SCREEN_W, oledH, SSD1306_WHITE);
  oled->display(); Serial.println(F("  all white"));      delay(800);

  oled->clearDisplay();
  for (int y = 0; y < oledH; y += 4)
    for (int x = ((y / 4) % 2) * 4; x < SCREEN_W; x += 8)
      oled->fillRect(x, y, 4, 4, SSD1306_WHITE);
  oled->display(); Serial.println(F("  checkerboard"));   delay(800);

  oled->clearDisplay();
  oled->drawRect(0, 0, SCREEN_W, oledH, SSD1306_WHITE);
  oled->setTextSize(1);
  oled->setTextColor(SSD1306_WHITE);
  oled->setCursor(6, 8);  oled->print(F("TEST 128x")); oled->print(oledH);
  oled->setCursor(6, 18); oled->print(F("whole border?"));
  oled->display(); Serial.println(F("  border + text"));
  Serial.println(F("  If the border is cut off or the image looks doubled,"
                   " the height is wrong: press 'h'."));
  delay(1200);
  oled->invertDisplay(true);
  delay(500);
  oled->invertDisplay(inverted);
}

void printHelp() {
  Serial.println(F("--- commands (type the letter + Enter) ---"));
  Serial.println(F("  SCREEN"));
  Serial.println(F("    i = rescan I2C + OLED status"));
  Serial.println(F("    b = bus electrical test + hunt for the screen on other pins"));
  Serial.println(F("    k = bus recovery (9 SCL pulses)"));
  Serial.println(F("    c = cycle I2C speed 400k <-> 100k (long cables)"));
  Serial.println(F("    x = re-initialise the OLED"));
  Serial.println(F("    1 = force 0x3C   2 = force 0x3D   0 = auto-detect"));
  Serial.println(F("    h = toggle screen height 32 <-> 64"));
  Serial.println(F("    d = visual test (white / checkerboard / border)"));
  Serial.println(F("    o = mirror the screen to the PC app (frame buffer, 20 fps)"));
  Serial.println(F("    v = raw SSD1306 commands, shows whether the panel ACKs"));
  Serial.println(F("  ENCODER"));
  Serial.println(F("    a = auto-calibrate (turn 6 clicks the same way)"));
  Serial.println(F("    e = change mode: detent -> 4 -> 2 -> 1 quarters -> detent"));
  Serial.println(F("    t = trace: how many quarters each detent produced"));
  Serial.println(F("    l = raw log: press, turn one click, press again"));
  Serial.println(F("    g = antipodal jump recovery on/off"));
  Serial.println(F("    n = encoder status + what kind of encoder it looks like"));
  Serial.println(F("  INPUTS"));
  Serial.println(F("    p = raw state of every pin"));
  Serial.println(F("    m = SW2 / SW3 connectivity matrix"));
  Serial.println(F("    r = reset counters and the encoder value"));
  Serial.println(F("    s = start/stop the automatic report"));
  Serial.println(F("    f = render period: 16 -> 10 -> 25 ms"));
  Serial.println(F("  HID"));
  Serial.println(F("    u = arm / disarm keyboard+mouse+gamepad"));
  Serial.println(F("    j = HID status + the full map of every input"));
  Serial.println(F("  MENU (works with HID armed too)"));
  Serial.println(F("    short press on USER          = next page"));
  Serial.println(F("    the screen sleeps after 20s; any press wakes it"));
  Serial.println(F("    USER held 1s                 = turn HID on / off"));
  Serial.println(F("    USER pressed twice           = toggle the media layer"));
  Serial.println(F("    (or the 'y' command, or the button in the app)"));
  Serial.println(F("    w = double-press window:"));
  Serial.println(F("        50 / 100 / 200 / 250 / 400 / 600 / 800 / 1200 ms"));
  Serial.println(F("    all 8 buttons + the encoder stay with the PC"));
  Serial.println(F("  PER-APP VOLUME (media layer, needs the PC app)"));
  Serial.println(F("    knob      = louder / quieter, SELECTED app only"));
  Serial.println(F("    ^B1 ^B2   = previous / next app"));
  Serial.println(F("    ^B3       = mute that app   ^B4 = back to Windows"));
  Serial.println(F("    board->PC : !AUD <code>"));
  Serial.println(F("    PC->board : %au=2/7;nm=..;vl=..;mu=0;sr=a|m"));
  Serial.println(F("    sr=a the app's own slider moves, sr=m its mixer channel"));
  Serial.println(F("  MUSIC page"));
  Serial.println(F("    %np=1;st=1;sk=s|y;ps=<sec>;du=<sec>;ti=<title>;ar=<artist>"));
  Serial.println(F("    sk=s draws the disc and the artist, sk=y the time instead"));
  Serial.println(F("    %ts=1|2;w=<px>;d=<base64>  a line drawn on the PC, one"));
  Serial.println(F("    byte per column - how Cyrillic gets on a 5x7 ASCII panel"));
  Serial.println(F("    on the MUSIC page - and on the last GAME sub-page - the"));
  Serial.println(F("    B buttons and the knob move the volume WITHOUT HID armed"));
  Serial.println(F("  GAME TELEMETRY"));
  Serial.println(F("    a line starting with '$', key=value fields split by ';'"));
  Serial.println(F("    $src=ETS2;spd=88;rpm=1450;rpmmax=2500;rl=2250;gear=6;fuel=62"));
  Serial.println(F("    common  : src knd(car|air) spd rpm rpmmax rl gear fuel"));
  Serial.println(F("              thr brk tmp alt txt"));
  Serial.println(F("    car     : blk(1=left 2=right 3=hazards) tur(bar x10)"));
  Serial.println(F("    aircraft: kts vs aft hdg c1 c2 sqk ap"));
  Serial.println(F("    ? = this help"));
}

void handleSerial() {
  // Telemetry lines start with '$' and are gathered to the end of the line. The
  // rest of the console stays on SINGLE-character commands, as before.
  static char tBuf[224];
  static uint8_t tLen = 0;
  static bool tCap = false;
  // A 250 px strip is 336 base64 characters plus 13 of header - 349, which fit
  // in 352 only by luck. One more field on that line and it would have been cut
  // in silence, and a cut base64 string draws garbage.
  static char aBuf[420];
  static uint8_t aLen = 0;
  static bool aCap = false;

  while (Serial.available()) {
    char c = Serial.read();

    if (tCap) {
      if (c == '\n' || c == '\r') {
        tBuf[tLen] = 0;
        if (tLen) gameParse(tBuf);
        tLen = 0;
        tCap = false;
      } else if (tLen < sizeof(tBuf) - 1) {
        tBuf[tLen++] = c;
      }
      continue;                 // telemetry does NOT wake the screen: it would
    }                           // stream non-stop while the game is running

    if (aCap) {
      if (c == '\n' || c == '\r') {
        aBuf[aLen] = 0;
        if (aLen) audParse(aBuf);
        aLen = 0;
        aCap = false;
      } else if (aLen < sizeof(aBuf) - 1) {
        aBuf[aLen++] = c;
      }
      continue;                 // like telemetry, this must not wake the screen
    }

    if (c == '$') { tCap = true; tLen = 0; continue; }
    if (c == '%') { aCap = true; aLen = 0; continue; }

    if (c != '\r' && c != '\n') oledWake();
    switch (c) {
      case 'i': case 'I':
        i2cScan();
        if (!oledOK) tryOledInit();          // it may have been rewired since
        if (!pcfOK)  pcfDetect();
        printI2CReport();
        break;
      case 'b': case 'B': printBusCheck(); break;
      case 'k': case 'K': busRecover();    break;
      case 'c': case 'C':
        // 100k -> 400k -> 1M. Above 400 kHz is outside the SSD1306 spec, but
        // these panels usually take 1 MHz too; if yours doesn't you'll see it
        // straight away (a screen full of garbage) and you come back with 'c'.
        i2cHz = (i2cHz == 100000) ? 400000 : ((i2cHz == 400000) ? 1000000 : 100000);
        Wire.setClock(i2cHz);
        tryOledInit();          // rebuild the screen: its speed comes from the ctor
        Serial.print(F("I2C speed = ")); Serial.print(i2cHz / 1000);
        Serial.println(F(" kHz"));
        break;
      case 'x': case 'X':
        Serial.println(tryOledInit() ? F("OLED re-initialised OK") : F("OLED still not found"));
        oledSleep = 0;            // re-init resets the contrast anyway
        oledWake();
        break;
      case '1': forceAddr = 0x3C; Serial.println(F("address forced to 0x3C")); tryOledInit(); break;
      case '2': forceAddr = 0x3D; Serial.println(F("address forced to 0x3D")); tryOledInit(); break;
      case '0': forceAddr = -1;   Serial.println(F("address: auto-detect"));    tryOledInit(); break;
      case 'h': case 'H':
        oledH = (oledH == 32) ? 64 : 32;
        Serial.print(F("screen height = 128x")); Serial.println(oledH);
        tryOledInit();
        break;
      case 'd': case 'D': oledVisualTest(); break;
      case 'v': case 'V': oledRawTest();    break;
      case 'a': case 'A':
        encCalActive = true;
        encCalCount  = 0;
        encErrors    = 0;
        Serial.println(F("--- encoder calibration ---"));
        Serial.println(F("  Turn SLOWLY, 6 clicks the same way, one click at a time."));
        break;
      case 'e': case 'E':
        encMode = (encMode == 0) ? 4 : ((encMode == 4) ? 2 : ((encMode == 2) ? 1 : 0));
        noInterrupts();
        encAcc = 0; encSteps = 0; encExcDir = 0; encExcMask = 0;
        interrupts();
        if (encMode == 0) Serial.println(F("DETENT mode (rest -> rest)"));
        else { Serial.print(F("classic mode, ")); Serial.print(encMode);
               Serial.println(F(" quarters per detent")); }
        break;
      case 't': case 'T':
        encTrace = !encTrace;
        Serial.println(encTrace ? F("encoder trace ON") : F("encoder trace OFF"));
        break;
      case 'n': case 'N': printEncStatus(); break;
      case 'g': case 'G':
        encGuess = !encGuess;
        Serial.println(encGuess
          ? F("antipodal jump recovery ON")
          : F("antipodal jump recovery OFF"));
        break;
      case 'l': case 'L':
        if (encLogOn) {
          printEncStatus();
          encDumpLog();
        } else {
          noInterrupts(); encLogHead = 0; encLogOn = true; interrupts();
          Serial.println(F("--- log started ---"));
          Serial.println(F("  Turn EXACTLY ONE CLICK, then press 'l' again."));
        }
        break;
      case 'p': case 'P': printPinReport(); break;
      case 'm': case 'M': printSwMatrix(sw2); printSwMatrix(sw3); break;
      case 'r': case 'R':
        encValue = 50; encTotal = 0; encErrors = 0;
        for (uint8_t i = 0; i < B_COUNT; i++) btn[i].count = 0;
        sw2.candMask = 0x0F; sw2.seenThrows = 0;   // relearn common and positions
        sw3.candMask = 0x3F; sw3.seenThrows = 0;
        if (pcfOK) {                               // take the rest reference again
          pcfIdle = pcfRaw;
          for (uint8_t i = 0; i < 8; i++) { pcfBtn[i].count = 0; pcfBtn[i].level = false; }
          pcfFails = 0;
        }
        for (uint8_t i = 0; i < 4; i++) encRestVotes[i] = 0;
        noInterrupts(); encChgA = 0; encChgB = 0; interrupts();
        usrLearnIdle();
        usrCount = 0;
        Serial.print(F("counters reset; USER rest = "));
        Serial.println(usrIdle ? F("HIGH") : F("LOW"));
        break;
      case 's': case 'S':
        streamOn = !streamOn;
        Serial.println(streamOn ? F("reporting ON") : F("reporting OFF"));
        break;
      case 'w': case 'W': {
        // The steps, in order. Edit the list if you want other values.
        static const uint16_t STEPS[] = { 50, 100, 200, 250, 400, 600, 800, 1200 };
        const uint8_t N = sizeof(STEPS) / sizeof(STEPS[0]);
        uint8_t i = 0;
        while (i < N && STEPS[i] != usrDoubleMs) i++;
        usrDoubleMs = STEPS[(i + 1) % N];
        Serial.print(F("double-press window = "));
        Serial.print(usrDoubleMs); Serial.println(F(" ms"));
        break;
      }
      case 'y': case 'Y':
        // The same toggle as holding ENC_SW, but from the console: works even if
        // the encoder isn't wired, and from the PC app.
        mediaLayer = !mediaLayer;
        Serial.print(F("layer = "));
        Serial.println(mediaLayer ? F("MEDIA") : F("gamepad"));
        break;
      case 'u': case 'U':
#if HID_AVAILABLE
        hidSetArmed(!hidArmed);
#else
        Serial.println(F("HID unavailable on the Mbed core"));
#endif
        break;
      case 'j': case 'J': printHidStatus(); break;
      case 'o': case 'O':
        mirrorOn = !mirrorOn;
        // Announced on its own line so the app can pick the state up from the
        // log as well as from the frames that follow.
        Serial.print(F("screen mirror "));
        Serial.println(mirrorOn ? F("ON") : F("OFF"));
        break;
      case 'f': case 'F':
        // no 8 ms: there the frame takes longer than the period, so you gain
        // nothing and the core never gets a break
        renderMs = (renderMs == 16) ? 10 : ((renderMs == 10) ? 25 : 16);
        Serial.print(F("render period = ")); Serial.print(renderMs);
        Serial.print(F(" ms (target ")); Serial.print(1000 / renderMs);
        Serial.println(F(" FPS)"));
        break;
      case '?': printHelp(); break;
      default: break;                        // ignore \r, \n and anything else
    }
  }
}

void serialReport() {
  Serial.print(F("SW2="));  if (sw2.pos) Serial.print(sw2.pos); else Serial.print('?');
  Serial.print(F(" SW3=")); if (sw3.pos) Serial.print(sw3.pos); else Serial.print('?');
  Serial.print(F(" ENC=")); Serial.print(encValue);
  Serial.print(F(" TOT=")); Serial.print(encTotal);
  Serial.print(F(" BTN="));
  for (uint8_t i = 0; i < B_COUNT; i++) Serial.print(btn[i].level ? '1' : '0');
  Serial.print(F(" ERR="));  Serial.print(encErrors);
  if (pcfOK) {
    Serial.print(F(" PCF="));
    for (uint8_t i = 0; i < 8; i++) Serial.print(pcfBtn[i].level ? '1' : '0');
  }
  Serial.print(F(" PG=")); Serial.print(page);
  Serial.print(F(" GM=")); Serial.print(gameFresh() ? gameSrc : "-");
  Serial.print(F(" GL=")); Serial.print(gameLines);   // lines received, total
  // Which app the knob would move, so you can tell from here whether the PC
  // app is answering at all - the same reason GM= and GL= are in this line.
  // Just a flag: '-' nobody telling us, 0 idle, 1 playing. The title itself
  // would double the length of a line that goes out five times a second.
  Serial.print(F(" NP="));
  if (!npFresh())        Serial.print('-');
  else if (!npTitle[0])  Serial.print('0');
  else                   Serial.print(npPlaying ? '1' : 'p');
  Serial.print(F(" AU="));
  if (!audFresh()) Serial.print('-');
  else {
    Serial.print(audName[0] ? audName : "?");
    Serial.print(':');
    if (audMute) Serial.print(F("mute")); else Serial.print(audVol);
    Serial.print(audSrc == 'a' ? F("app") : F("mix"));
  }
#if HID_AVAILABLE
  Serial.print(F(" HID=")); Serial.print(hidArmed ? '1' : '0');
#else
  Serial.print(F(" HID=-"));
#endif
  Serial.print(F(" OLED=")); Serial.print(oledOK ? F("ok") : F("-"));
  Serial.print(F(" FPS=")); Serial.print(fps);
  Serial.print(F(" RND=")); Serial.print(renderUs);
  Serial.print(F("us I2C=")); Serial.print(i2cHz / 1000);
  Serial.print(F("k P=")); Serial.print(renderMs);
  Serial.println(F("ms"));
}

/* ==========================================================================
   SETUP / LOOP

   How the cores are split:
     core0 - buttons, encoder, switches, HID, serial, telemetry
     core1 - the screen, and nothing else

   Why: a frame costs ~15 ms on I2C at 400 kHz, and Wire is blocking. With
   everything on one core, at 60 FPS rendering took over 90% of the loop, the
   USB stack stopped being serviced and the board dropped off the bus - measured,
   it really happened. Moved to core1, that same 90% touches neither USB nor
   button latency.
   ========================================================================== */
#define RENDER_ON_CORE1 1
void setup() {
  pinMode(PIN_STATUS_LED, OUTPUT);
  digitalWrite(PIN_STATUS_LED, STATUS_LED_ENABLED ? HIGH : LOW);

  Serial.begin(115200);
  // USB CDC needs ~1-2s to enumerate; without this the first messages are lost.
  // We wait, but we don't block if no monitor is open.
  uint32_t tWait = millis();
  while (!Serial && (millis() - tWait) < 2500) { delay(10); }
  Serial.println();
  Serial.println(F("=== PicoPanel (Panou board) - starting ==="));
  Serial.print(F("Core: ")); Serial.println(F(CORE_NAME));
#if !HID_AVAILABLE
  // A missing HID must not pass in silence: on Mbed everything compiles and
  // looks fine, it's just that half the firmware isn't there.
  Serial.println(F("!!! HID UNAVAILABLE - you're on the Mbed core."));
  Serial.println(F("    Tools > Board > VCC-GND YD RP2040 (the rp2040 package by"));
  Serial.println(F("    Earle Philhower), and Tools > USB Stack = Pico SDK."));
  Serial.println(F("    Diagnostics work; keyboard/mouse/gamepad don't."));
#endif

  for (uint8_t i = 0; i < sw2.n;   i++) pinMode(sw2.pins[i], INPUT_PULLUP);
  for (uint8_t i = 0; i < sw3.n;   i++) pinMode(sw3.pins[i], INPUT_PULLUP);
  for (uint8_t i = 0; i < B_COUNT; i++) pinMode(btn[i].pin,  INPUT_PULLUP);
  pinMode(PIN_ENC_A, INPUT_PULLUP);
  pinMode(PIN_ENC_B, INPUT_PULLUP);
  usrLearnIdle();

#if defined(ARDUINO_ARCH_MBED)
  // Mbed core: Wire's pins are fixed in the variant (Pico = GPIO4/GPIO5), so
  // there is no setSDA/setSCL. We only check they match how it's wired.
  #if defined(PIN_WIRE_SDA) && defined(PIN_WIRE_SCL)
    #if (PIN_WIRE_SDA != PIN_SDA) || (PIN_WIRE_SCL != PIN_SCL)
      #error "The Mbed core's I2C pins aren't GPIO4/GPIO5. Switch to the Philhower core."
    #endif
  #endif
#else
  Wire.setSDA(PIN_SDA);
  Wire.setSCL(PIN_SCL);
#endif

  // Measure the external pull-ups NOW, while the pins are still free: after
  // Wire.begin() the I2C peripheral takes them and pinMode() can't read them.
  bootPuSDA = hasExternalPullup(PIN_SDA);
  bootPuSCL = hasExternalPullup(PIN_SCL);

  wireRestart();

  i2cScan();
  tryOledInit();
  pcfDetect();
  printI2CReport();
  printHelp();

  if (oledOK) {
    oled->clearDisplay();
    oled->setTextColor(SSD1306_WHITE);
    oled->setTextSize(2);
    oled->setCursor(4, 2);  oled->print(F("PANEL"));
    oled->setTextSize(1);
    oled->setCursor(4, 22); oled->print(F("YD-RP2040  0x"));
    oled->print(oledAddr, HEX);
    oled->display();
    delay(900);
  } else {
    Serial.println(F("WARNING: no OLED on 0x3C / 0x3D. Carrying on over Serial only."));
    Serial.println(F("  Suggested order: 'b' (power/wiring) -> 'k' (bus recovery)"
                     " -> 'c' (100 kHz) -> 'x' (re-init) -> 'v' (panel ACK)."));
  }

#if HID_AVAILABLE
  // The USB descriptors change here: the board presents itself to the host as a
  // composite device (serial + keyboard + mouse + gamepad). HID stays DISARMED
  // until you ask for it with 'u', so nothing reaches the PC on its own.
  Keyboard.begin();
  Mouse.begin();
  Joystick.begin();
  Joystick.useManualSend(true);
  hidScanTables();
#endif

  // the encoder's initial state, so it doesn't produce a false jump; the
  // position we find at power-up is, by definition, a detent
  encPhase = (uint8_t)((digitalRead(PIN_ENC_A) << 1) | digitalRead(PIN_ENC_B));
  encRest  = encPhase;
  attachInterrupt(digitalPinToInterrupt(PIN_ENC_A), encISR, CHANGE);
  attachInterrupt(digitalPinToInterrupt(PIN_ENC_B), encISR, CHANGE);

  // first scans, so there's already a valid position at startup
  for (uint8_t i = 0; i < 3; i++) { swScan(sw2); swScan(sw3); }
  sw2.pos = sw2.posCand;  sw2.changes = 0;
  sw3.pos = sw3.posCand;  sw3.changes = 0;

  Serial.print(F("USER button on GP")); Serial.print(PIN_USR_KEY);
  Serial.print(F(": rest = ")); Serial.println(usrIdle ? F("HIGH") : F("LOW"));
  Serial.println(F("Ready. USER = page, encoder = value, '?' = commands."));
}

void loop() {
  static uint32_t tRender = 0, tSerial = 0, tFps = 0, tScan = 0;
  uint32_t now = millis();

  handleSerial();
  buttonsUpdate();
  pcfUpdate();
  pcfEvents();

  // The matrix scan flips pins to outputs for a few tens of microseconds;
  // there's no point doing it more often than 50 Hz.
  if (now - tScan >= 20) { tScan = now; swScan(sw2); swScan(sw3); }

  // --- encoder ---
  encHousekeeping();
  int32_t det = encTakeSteps();
  if (det) {
    encTotal += det;
    encDir    = (det > 0) ? 1 : -1;
    tLastDet  = now;
    encValue += det;
    if (encValue < ENC_MIN) encValue = ENC_MIN;
    if (encValue > ENC_MAX) encValue = ENC_MAX;
  } else if (now - tLastDet > 600) {
    encDir = 0;
  }

  // Pages change with the onboard USER button, so no panel input is stolen by
  // the menu: every one of them stays with the PC.
  usrUpdate();
  // Before HID: on an audio page these inputs are the volume's, and hidUpdate()
  // is told to leave them alone.
  audioPageUpdate(det);
  activityWatch();
#if !RENDER_ON_CORE1
  oledSleepService();              // on core1 when rendering lives there
#endif

#if HID_AVAILABLE
  // On a layer change we release everything held over USB. A gamepad button
  // held on layer one would stay pressed forever if the layer changed under it.
  static bool shiftWas = false;
  if (mediaLayer != shiftWas) {
    shiftWas = mediaLayer;
    if (hidArmed) hidReleaseAll();
  }

  hidUpdate(det);
#endif

  // --- navigation ---
  if (tookPress(B_RHT)) page = (uint8_t)((page + 1) % P_COUNT);
  if (tookPress(B_LFT)) page = (uint8_t)((page + P_COUNT - 1) % P_COUNT);

  if (tookPress(B_UP))  { encValue += 10; if (encValue > ENC_MAX) encValue = ENC_MAX; }
  if (tookPress(B_DWN)) { encValue -= 10; if (encValue < ENC_MIN) encValue = ENC_MIN; }

  if (tookPress(B_MID)) { i2cScan(); if (!oledOK) tryOledInit(); printI2CReport(); }
  if (tookPress(B_SET)) {
    encValue = 50; encTotal = 0; encErrors = 0;
    for (uint8_t i = 0; i < B_COUNT; i++) btn[i].count = 0;
  }

  // ENC_SW is ONLY a gamepad button, handled in hidUpdate(). It has no long
  // press and toggles nothing: everything to do with the menu and the layers
  // lives on the USER button. Here we just consume the events left over when
  // HID is disarmed and hidUpdate() returns early.
  tookPress(B_SW);
  tookRelease(B_SW);

  // --- status LED (onboard blue) ---
  // Off via STATUS_LED_ENABLED. HID's state is on the OLED's HID page, which
  // doesn't blind you.
#if STATUS_LED_ENABLED
#if HID_AVAILABLE
  if (hidArmed) {
    digitalWrite(PIN_STATUS_LED, (now % 1000) < 900);
  } else
#endif
  {
    static uint32_t tBlink = 0;
    static bool     ledOn  = false;
    uint32_t period = oledOK ? 1000 : 150;
    if (now - tBlink >= period) { tBlink = now; ledOn = !ledOn; digitalWrite(PIN_STATUS_LED, ledOn); }
  }
#endif

  // --- rendering ~40 Hz ---
#if !RENDER_ON_CORE1
  if (now - tRender >= renderMs) { tRender = now; render(); }
#else
  (void)tRender;                   // rendering lives on core1
#endif
  mirrorService();
  if (now - tFps    >= 1000) { tFps = now; fps = frames; frames = 0; }
  if (streamOn && now - tSerial >= (1000 / SERIAL_HZ)) { tSerial = now; serialReport(); }
}


#if RENDER_ON_CORE1
/* --------------------------------------------------------------------------
   The second core: the screen only.

   It has no input loop of its own and never touches HID. It reads the state
   core0 writes (buttons, encoder, telemetry) and draws it. A read can catch a
   value a microsecond after it changed - for a screen that doesn't matter, the
   next frame shows it correctly anyway.
   -------------------------------------------------------------------------- */
void setup1() {
  // Let core0 finish initialising: until the OLED object exists and the bus is
  // up, there's nothing to draw.
  delay(1500);
}

void loop1() {
  static uint32_t tRender1 = 0;
  uint32_t now = millis();

  oledSleepService();

  if (now - tRender1 >= renderMs) {
    tRender1 = now;
    render();
  } else {
    // Without this, core1 spins between frames with the bus in hand and takes
    // the lock from under core0 on every attempt.
    delay(1);
  }
}
#endif
