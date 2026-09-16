package com.picopanel.pocket;

/**
 * The PicoPanel wire protocol, exactly as PicoPanel.ino emits it.
 *
 * Deliberately free of android.* imports so it runs, and is tested, on a plain
 * JVM. The emulator and the real board both produce these lines, so everything
 * above this class is identical whether hardware is attached or not.
 *
 * A report, from serialReport():
 *   SW2=2 SW3=4 ENC=7 TOT=112 BTN=0000000 ERR=0 PCF=00100000 PG=0 GM=- GL=0
 *   HID=1 OLED=ok FPS=60 RND=14800us I2C=400k P=16ms
 *
 * A frame, from mirrorService():
 *   !FB 128 32 <base64 of the SSD1306 page buffer>
 */
public final class Protocol {

    /** BTN bit order, from `enum { B_UP = 0, B_DWN, ... }` in the firmware. */
    public static final String[] BTN_NAMES = {"UP", "DWN", "LFT", "RHT", "MID", "SET", "ENC"};
    /** PCF8574 pins P0..P7, as the board labels them. */
    public static final String[] PCF_NAMES = {"A1", "A2", "A3", "A4", "B1", "B2", "B3", "B4"};
    public static final String[] PAGES = {"PANEL", "GAME", "HID", "SWITCHES", "ENCODER",
                                          "BUTTONS", "PCF8574", "I2C", "INFO"};

    private Protocol() {}

    /** One decoded report line. Fields mirror panel.py's parse_telemetry(). */
    public static final class Report {
        public int sw1;            // firmware SW2 - the 3-position switch
        public int sw2;            // firmware SW3 - the 5-position switch
        public int enc, total, errors;
        public boolean[] btn = new boolean[0];
        public boolean[] pcf = new boolean[0];
        public boolean pcfPresent;
        public int page = -1;
        public String hid = "-";
        public String game = "-";
        public int fps = -1;
        public boolean oled;

        public String pageName() {
            return (page >= 0 && page < PAGES.length) ? PAGES[page] : String.valueOf(page);
        }
    }

    /** A decoded !FB frame. */
    public static final class Frame {
        public final int width, height;
        public final byte[] data;
        Frame(int w, int h, byte[] d) { width = w; height = h; data = d; }

        /** True if the pixel is lit. The buffer is in SSD1306 pages: one byte
         *  holds 8 pixels stacked vertically. */
        public boolean pixel(int x, int y) {
            if (x < 0 || y < 0 || x >= width || y >= height) return false;
            int i = x + (y >> 3) * width;
            return i < data.length && (data[i] & (1 << (y & 7))) != 0;
        }
    }

    public static boolean isFrame(String line) { return line.startsWith("!FB "); }

    /** @return the frame, or null if the line is malformed. */
    public static Frame parseFrame(String line) {
        if (!isFrame(line)) return null;
        String[] p = line.split(" ", 4);
        if (p.length < 4) return null;
        try {
            int w = Integer.parseInt(p[1]), h = Integer.parseInt(p[2]);
            if (w <= 0 || h <= 0 || w > 512 || h > 256) return null;
            byte[] data = base64Decode(p[3]);
            if (data == null || data.length < w * h / 8) return null;
            return new Frame(w, h, data);
        } catch (NumberFormatException e) {
            return null;
        }
    }

    /**
     * @return the report, or null if this line is something else (a log line,
     *         a reply to a command). The two-key test is panel.py's.
     */
    public static Report parse(String line) {
        if (!line.contains("ENC=") || !line.contains("BTN=")) return null;
        Report r = new Report();
        boolean sawBtn = false;
        for (String tok : line.trim().split("\\s+")) {
            int eq = tok.indexOf('=');
            if (eq <= 0) continue;
            String k = tok.substring(0, eq), v = tok.substring(eq + 1);
            switch (k) {
                case "SW2": r.sw1 = num(v, 0); break;
                case "SW3": r.sw2 = num(v, 0); break;
                case "ENC": r.enc = num(v, 0); break;
                case "TOT": r.total = num(v, 0); break;
                case "ERR": r.errors = num(v, 0); break;
                case "BTN": r.btn = bits(v); sawBtn = true; break;
                case "PCF": r.pcf = bits(v); r.pcfPresent = true; break;
                case "PG":  r.page = num(v, -1); break;
                case "GM":  r.game = v; break;
                case "HID": r.hid = v; break;
                case "FPS": r.fps = num(v, -1); break;
                case "OLED": r.oled = "ok".equals(v); break;
                default: break;   // RND/I2C/P carry units; nothing here needs them
            }
        }
        return sawBtn ? r : null;
    }

    private static boolean[] bits(String s) {
        boolean[] out = new boolean[s.length()];
        for (int i = 0; i < s.length(); i++) out[i] = s.charAt(i) == '1';
        return out;
    }

    /** '?' is what the firmware prints for a switch it cannot place. */
    private static int num(String s, int fallback) {
        try { return Integer.parseInt(s); } catch (NumberFormatException e) { return fallback; }
    }

    /**
     * Base64, hand-rolled: java.util.Base64 needs API 26 and android.util.Base64
     * cannot be called from a JVM test. Twenty lines buys both.
     */
    static byte[] base64Decode(String s) {
        int n = s.length();
        while (n > 0 && (s.charAt(n - 1) == '=' || s.charAt(n - 1) == '\n'
                      || s.charAt(n - 1) == '\r')) n--;
        int outLen = n * 3 / 4;
        byte[] out = new byte[outLen];
        int acc = 0, bits = 0, o = 0;
        for (int i = 0; i < n; i++) {
            int c = decodeChar(s.charAt(i));
            if (c < 0) return null;
            acc = (acc << 6) | c;
            bits += 6;
            if (bits >= 8) {
                bits -= 8;
                if (o < outLen) out[o++] = (byte) ((acc >> bits) & 0xFF);
            }
        }
        return out;
    }

    private static int decodeChar(char c) {
        if (c >= 'A' && c <= 'Z') return c - 'A';
        if (c >= 'a' && c <= 'z') return c - 'a' + 26;
        if (c >= '0' && c <= '9') return c - '0' + 52;
        if (c == '+') return 62;
        if (c == '/') return 63;
        return -1;
    }
}
