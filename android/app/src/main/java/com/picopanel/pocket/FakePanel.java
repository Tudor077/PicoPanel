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

    public int sw1 = 2, sw2 = 3;           // firmware SW2 (3 pos), SW3 (5 pos)
    public int enc = 0, total = 0, errors = 0;
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

    public Screen draw() {
        double t = now();
        screen.clear();
        if (page == 0) {
            screen.text(0, 0, "PANEL   " + (hid ? "HID" : "---"));
            screen.text(0, 8, "SW1:" + sw1 + "  SW2:" + sw2);
            screen.text(0, 16, "ENC:" + (enc < 0 ? "-" : "+") + Math.abs(enc) + " T:" + total);
            StringBuilder lamps = new StringBuilder("AB:");
            for (boolean v : pcf) lamps.append(v ? '*' : '.');
            screen.text(0, 24, lamps.toString());
        } else if (page == 1) {
            screen.text(0, 0, "GAME " + gameSrc);
            int spd = (int) Math.round(60 + 55 * Math.sin(t * 1.7));
            screen.text(0, 10, spd + " KMH  G" + (2 + Math.abs(spd) / 40));
            int w = Math.max(2, (int) Math.round(126 * (0.5 + 0.5 * Math.sin(t * 2.2))));
            screen.rect(0, 24, 128, 7, false);
            screen.rect(1, 25, w - 1, 5, true);
        } else {
            screen.text(0, 0, Protocol.PAGES[page]);
            screen.text(0, 12, "PAGE " + page + " OF " + (Protocol.PAGES.length - 1));
            screen.text(0, 22, "EMULATED");
        }
        return screen;
    }

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
        enc = Math.max(-99, Math.min(99, enc + steps[rng.nextInt(steps.length)]));
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
