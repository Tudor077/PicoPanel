package com.picopanel.pocket;

import java.util.Locale;
import java.util.Random;

/**
 * A PicoPanel with no PicoPanel.
 *
 * Emits byte-for-byte what the firmware emits, so the app cannot tell the
 * difference and neither can anything downstream of the parser. This is what
 * makes the app usable, testable and demonstrable with nothing plugged in.
 *
 * Mirrors pc/fakepanel.py; the two are kept deliberately in step.
 */
public final class FakePanel {

    public interface Sink { void line(String text); }

    private static final double REPORT_HZ = 5, MIRROR_HZ = 10;

    // enum Page { P_OVERVIEW = 0, P_GAME, P_HID, P_SW, P_ENC, P_BTN, P_PCF,
    //             P_I2C, P_INFO };  -- PG= is an index into Protocol.PAGES.
    private static final int P_OVERVIEW = 0, P_GAME = 1, P_SW = 3,
                             P_ENC = 4, P_BTN = 5, P_PCF = 6;
    /** btn[].name in the firmware - two characters, to fit the 17px boxes. */
    private static final String[] BTN_LABELS = {"UP", "DN", "LF", "RT", "MD", "ST", "EN"};

    public int sw1 = 2, sw2 = 3;           // firmware SW2 (3 pos), SW3 (5 pos)
    public int enc = 0, total = 0, errors = 0;
    private int lastDir = 0;
    public final boolean[] btn = new boolean[7];
    public final boolean[] pcf = new boolean[8];
    public int page = 0;
    public boolean hid = true, mirror = true, demo = true;
    public String gameSrc = "-";
    public int gameLines = 0;

    private final Screen screen = new Screen();
    private final Random rng = new Random();
    private final long t0 = System.nanoTime();
    private double nextReport = 0, nextFrame = 0, nextDemo = 0;
    private Sink sink;

    public void setSink(Sink s) { sink = s; }
    private void emit(String s) { if (sink != null) sink.line(s); }
    private double now() { return (System.nanoTime() - t0) / 1e9; }

    public String reportLine() {
        StringBuilder b = new StringBuilder(), p = new StringBuilder();
        for (boolean v : btn) b.append(v ? '1' : '0');
        for (boolean v : pcf) p.append(v ? '1' : '0');
        return String.format(Locale.ROOT,
            "SW2=%d SW3=%d ENC=%d TOT=%d BTN=%s ERR=%d PCF=%s PG=%d GM=%s GL=%d "
          + "HID=%s OLED=ok FPS=60 RND=14800us I2C=400k P=16ms",
            sw1, sw2, enc, total, b, errors, p, page, gameSrc, gameLines, hid ? "1" : "0");
    }

    /**
     * The panel's own pages, as PicoPanel.ino draws them.
     *
     * Transcribed from render(): header() then the per-page draw function.
     * Every coordinate, box size and label here is the firmware's - this is
     * what the real 128x32 shows, not an approximation of it.
     */
    public Screen draw() {
        screen.clear();
        header();
        switch (page) {
            case P_OVERVIEW: drawOverview(); break;
            case P_GAME:     drawGame();     break;
            case P_SW:       drawSwPage();   break;
            case P_ENC:      drawEnc();      break;
            case P_BTN:      drawBtnPage();  break;
            case P_PCF:      drawPcf();      break;
            default:
                // HID, I2C and INFO read live hardware the emulator has no
                // model of; a guess would be worse than saying so.
                screen.text(0, 13, "not emulated");
                screen.text(0, 23, Protocol.PAGES[page] + " needs hw");
        }
        return screen;
    }

    /** A filled white bar with black text - inverted, unlike every other page. */
    private void header() {
        screen.rect(0, 0, screen.width, 9, true);
        screen.text(2, 1, Protocol.PAGES[page], false, 1);
        String mark = hid ? "HID " : "";
        int cur = page + 1, tot = Protocol.PAGES.length;
        if (page == P_GAME && !"-".equals(gameSrc)) { cur = 1; tot = 3; }
        String buf = mark + cur + "/" + tot;
        screen.text(screen.width - 2 - 6 * buf.length(), 1, buf, false, 1);
    }

    /** Seven 17x10 boxes, filled when pressed - drawBtnRow(). */
    private void btnRow(int y) {
        final int w = 17, h = 10;
        for (int i = 0; i < BTN_LABELS.length; i++) {
            int x = i * (w + 1);
            boolean pressed = btn[i];
            screen.rect(x, y, w, h, pressed);
            screen.text(x + 3, y + 2, BTN_LABELS[i], !pressed, 1);
        }
    }

    private void drawOverview() {
        screen.text(0, 11, "S2:" + pos(sw1) + " S3:" + pos(sw2) + " E:" + enc);
        btnRow(21);
    }

    private void drawSwPage() {
        // drawSwRow(): "<name> <pos>/<expect>  c<common>  v<seen>"
        screen.text(0, 11, "SW2 " + pos(sw1) + "/3  c1  v3");
        screen.text(0, 21, "SW3 " + pos(sw2) + "/5  c1  v5");
    }

    private void drawEnc() {
        screen.text(2, 13, String.valueOf(enc), true, 2);
        String dir = lastDir > 0 ? "CW" : lastDir < 0 ? "CCW" : "--";
        screen.text(60, 12, "A1 B0 " + dir);
        screen.text(60, 22, "T" + total + " E" + errors);
    }

    private void drawBtnPage() {
        btnRow(11);
        StringBuilder counts = new StringBuilder();
        for (int i = 0; i < BTN_LABELS.length; i++) counts.append("0 ");
        screen.text(0, 24, counts.toString());
    }

    /** Eight 15x10 boxes numbered 1..8 - drawPcf(). */
    private void drawPcf() {
        int raw = 0;
        for (int i = 0; i < 8; i++) {
            int x = i * 16;
            screen.rect(x, 11, 15, 10, pcf[i]);
            screen.text(x + 5, 13, String.valueOf(i + 1), !pcf[i], 1);
            if (!pcf[i]) raw |= 1 << i;
        }
        screen.text(0, 23, String.format(Locale.ROOT, "0x20 raw 0x%02X", raw));
    }

    private void drawGame() {
        if ("-".equals(gameSrc)) {
            screen.text(0, 13, "no game");
            screen.text(0, 23, "waiting for serial");
            return;
        }
        double t = now();
        int spd = (int) Math.round(60 + 55 * Math.sin(t * 1.7));
        screen.text(2, 12, String.valueOf(spd), true, 2);
        screen.text(56, 12, "KMH");
        screen.text(104, 12, "G" + (2 + Math.abs(spd) / 40));
        int w = Math.max(2, (int) Math.round(126 * (0.5 + 0.5 * Math.sin(t * 2.2))));
        screen.rect(0, 24, 128, 7, false);
        screen.rect(1, 25, w - 1, 5, true);
    }

    private static String pos(int p) { return p > 0 ? String.valueOf(p) : "?"; }

    public String frameLine() {
        Screen s = draw();
        return "!FB " + s.width + " " + s.height + " " + base64(s.buf);
    }

    /** One line from the app: a command letter, or a '$' telemetry line. */
    public void command(String text) {
        if (text == null) return;
        text = text.trim();
        if (text.isEmpty()) return;
        if (text.startsWith("$")) {
            gameLines++;
            for (String tok : text.substring(1).split(";"))
                if (tok.startsWith("src=")) gameSrc = tok.length() > 4 ? tok.substring(4) : "-";
            page = 1;
            return;
        }
        char c = Character.toLowerCase(text.charAt(0));
        switch (c) {
            case 'o': mirror = !mirror; emit("screen mirror " + (mirror ? "ON" : "OFF")); break;
            case 'u': hid = !hid; emit("HID " + (hid ? "armed" : "disarmed")); break;
            case 'p': emit("pins: SW1=" + sw1 + " SW2=" + sw2 + " ENC=" + enc
                           + " TOT=" + total + " ERR=" + errors); break;
            case 'i': emit("I2C scan: 0x3C SSD1306, 0x20 PCF8574   (emulated)"); break;
            case 'n': emit("encoder: 20 detents/turn, filtered, mode=edge (emulated)"); break;
            case 'r': total = 0; errors = 0; emit("counters reset"); break;
            case '?':
                emit("i b k c x  I2C: rescan, bus test, recovery, speed, re-init");
                emit("d v h 1 2 0  display: visual test, raw cmd, height, address");
                emit("a e t l g n  encoder: calibrate, mode, trace, log, jump, status");
                emit("p m r s f  pins, switch matrix, reset, reporting, render period");
                emit("u j y w  arm HID, mapping, media layer, double-press window");
                emit("o  mirror the screen to the app");
                break;
            default: emit("'" + text.charAt(0) + "' acknowledged (emulated, no hardware to act on)");
        }
    }

    private void demoStep() {
        if (!demo) return;
        int[] steps = {0, 0, 1, 1, -1};
        int step = steps[rng.nextInt(steps.length)];
        if (step != 0) lastDir = step;
        enc = Math.max(-99, Math.min(99, enc + step));
        total++;
        double r = rng.nextDouble();
        if (r < 0.22) { int i = rng.nextInt(8); pcf[i] = !pcf[i]; }
        else if (r < 0.34) { int i = rng.nextInt(7); btn[i] = !btn[i]; }
        else if (r < 0.40) { sw1 = 1 + rng.nextInt(3); }
        else if (r < 0.46) { sw2 = 1 + rng.nextInt(5); }
    }

    /** Produce whatever the board would have sent by now. Call it often. */
    public void service() {
        double n = now();
        if (n >= nextDemo) { nextDemo = n + 0.35; demoStep(); }
        if (n >= nextReport) { nextReport = n + 1 / REPORT_HZ; emit(reportLine()); }
        if (mirror && n >= nextFrame) { nextFrame = n + 1 / MIRROR_HZ; emit(frameLine()); }
    }

    private static final char[] B64 =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/".toCharArray();

    static String base64(byte[] d) {
        StringBuilder sb = new StringBuilder((d.length + 2) / 3 * 4);
        for (int i = 0; i < d.length; i += 3) {
            int b0 = d[i] & 0xFF;
            int b1 = i + 1 < d.length ? d[i + 1] & 0xFF : 0;
            int b2 = i + 2 < d.length ? d[i + 2] & 0xFF : 0;
            sb.append(B64[b0 >> 2]).append(B64[((b0 & 3) << 4) | (b1 >> 4)]);
            sb.append(i + 1 < d.length ? B64[((b1 & 15) << 2) | (b2 >> 6)] : '=');
            sb.append(i + 2 < d.length ? B64[b2 & 63] : '=');
        }
        return sb.toString();
    }
}
