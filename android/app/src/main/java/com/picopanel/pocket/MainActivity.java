package com.picopanel.pocket;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup.LayoutParams;
import android.widget.Button;
import android.widget.EditText;
import android.widget.GridLayout;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/**
 * PicoPanel Pocket - the panel's companion, on the phone itself.
 *
 * Two sources, one parser. The emulator produces the same lines the board
 * produces, so nothing below handleLine() knows or cares which is running, and
 * the app is fully usable with no hardware attached.
 *
 * The UI is built in code rather than XML on purpose: it is one screen, it
 * keeps the resource surface to almost nothing, and it means the build has
 * fewer ways to fail on someone else's machine.
 */
public class MainActivity extends Activity implements UsbCdc.Listener {

    private static final int GROUND = Color.rgb(0x0C, 0x11, 0x16);
    private static final int SURFACE = Color.rgb(0x14, 0x1B, 0x23);
    private static final int LINE = Color.rgb(0x27, 0x32, 0x3E);
    private static final int INK = Color.rgb(0xE4, 0xEB, 0xF3);
    private static final int MUTED = Color.rgb(0x85, 0x98, 0xAA);
    private static final int AMBER = Color.rgb(0xE8, 0x91, 0x2D);
    private static final int CYAN = Color.rgb(0x6F, 0xC3, 0xD9);
    private static final int OK = Color.rgb(0x3F, 0xAE, 0x7A);
    private static final int BAD = Color.rgb(0xD9, 0x5C, 0x52);

    private static final String[] COMMANDS = {"o", "u", "y", "?", "i", "p", "n", "r"};
    private static final String[] COMMAND_LABELS =
        {"mirror", "arm HID", "media layer", "help", "I2C scan", "pins", "encoder", "reset"};

    private final Handler ui = new Handler(Looper.getMainLooper());
    private final FakePanel emu = new FakePanel();
    private UsbCdc usb;
    private boolean usbMode = false;

    private OledView oled;
    private TextView status, encValue, encMeta, logView, statPage, statHid, statGame, statFps;
    private Button modeEmu, modeUsb;
    private AlarmRelay relay;
    private final Button[] sw1 = new Button[3], sw2 = new Button[5];
    private final View[] pcfLamps = new View[8], btnLamps = new View[7];
    private final TextView[] pcfText = new TextView[8], btnText = new TextView[7];
    private ScrollView logScroll;
    private TextView userHint;
    private long usbUserDown = 0, usbUserLast = 0;
    private final android.text.SpannableStringBuilder logBuf =
        new android.text.SpannableStringBuilder();
    private int logLines = 0;
    private boolean manual = false;

    private final Runnable tick = new Runnable() {
        @Override public void run() {
            if (!usbMode) emu.service();
            ui.postDelayed(this, 50);
        }
    };

    @Override protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        setContentView(buildUi());
        usb = new UsbCdc(this, this);
        emu.setSink(line -> ui.post(() -> handleLine(line)));
        log("[emulator] simulated panel running - no hardware attached", CYAN);
        ui.post(tick);
    }

    @Override protected void onDestroy() {
        ui.removeCallbacks(tick);
        if (usb != null) usb.close();
        super.onDestroy();
    }

    // ------------------------------------------------------------------ data
    private void handleLine(String line) {
        if (Protocol.isFrame(line)) {
            Protocol.Frame f = Protocol.parseFrame(line);
            if (f != null) oled.setFrame(f);
            return;
        }
        Protocol.Report r = Protocol.parse(line);
        if (r != null) apply(r); else log(line, INK);
    }

    private void apply(Protocol.Report r) {
        for (int i = 0; i < sw1.length; i++) paintPosition(sw1[i], r.sw1 == i + 1);
        for (int i = 0; i < sw2.length; i++) paintPosition(sw2[i], r.sw2 == i + 1);
        encValue.setText((r.enc > 0 ? "+" : "") + r.enc);
        encMeta.setText("total " + r.total + "   errors " + r.errors);
        for (int i = 0; i < pcfLamps.length; i++)
            paintLamp(pcfLamps[i], pcfText[i], i < r.pcf.length && r.pcf[i]);
        for (int i = 0; i < btnLamps.length; i++)
            paintLamp(btnLamps[i], btnText[i], i < r.btn.length && r.btn[i]);
        statPage.setText(r.page >= 0 ? r.pageName() : "-");
        boolean armed = "1".equals(r.hid);
        statHid.setText(armed ? "ARMED" : "0".equals(r.hid) ? "OFF" : "n/a");
        statHid.setTextColor(armed ? OK : "0".equals(r.hid) ? BAD : MUTED);
        statGame.setText(r.game == null || "-".equals(r.game) ? "none" : r.game);
        statFps.setText(r.fps >= 0 ? String.valueOf(r.fps) : "-");
    }

    private void send(String text) {
        log(">>> " + text, AMBER);
        if (usbMode && usb.isOpen()) usb.send(text); else emu.command(text);
    }

    // --------------------------------------------------------------- USB side
    @Override public void onLine(String line) { ui.post(() -> handleLine(line)); }

    @Override public void onStatus(String message, boolean connected) {
        ui.post(() -> {
            log("[usb] " + message, connected ? CYAN : BAD);
            usbMode = connected;
            status.setText(connected ? "USB - panel" : "Emulator");
            status.setTextColor(connected ? CYAN : OK);
            paintMode(modeEmu, !connected);
            paintMode(modeUsb, connected);
            if (!connected) oled.clear("PICOPANEL");
            userHint.setText(connected
                ? "Hold 1 s to arm HID, double-tap for the media layer. Changing the "
                + "page needs the real USER button on the board."
                : "Tap for the next page \u00b7 twice for the media layer \u00b7 "
                + "hold 1 s to arm HID.");
        });
    }

    // ------------------------------------------------------------------- view
    private View buildUi() {
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setBackgroundColor(GROUND);
        col.setPadding(dp(16), dp(16), dp(16), dp(28));

        LinearLayout head = new LinearLayout(this);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        TextView brand = label("PICOPANEL POCKET", 19, INK);
        brand.setLayoutParams(new LinearLayout.LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f));
        status = label("Emulator", 13, OK);
        head.addView(brand);
        head.addView(status);
        col.addView(head);

        col.addView(heading("Panel screen - SSD1306 128x32"));
        oled = new OledView(this);
        oled.setLayoutParams(new LinearLayout.LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT));
        LinearLayout bezel = card();
        bezel.addView(oled);
        col.addView(bezel);

        // The USER button sits under the screen, because that is what it drives.
        LinearLayout userRow = new LinearLayout(this);
        userRow.setOrientation(LinearLayout.HORIZONTAL);
        userRow.setGravity(Gravity.CENTER_VERTICAL);
        final Button userBtn = actionButton("USER", null, 0f);
        userBtn.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        userBtn.setBackground(rounded(Color.rgb(0x1B, 0x24, 0x2E), AMBER));
        userBtn.setPadding(dp(22), dp(12), dp(22), dp(12));
        userBtn.setOnTouchListener((v, ev) -> {
            switch (ev.getActionMasked()) {
                case android.view.MotionEvent.ACTION_DOWN:
                    userBtn.setBackground(rounded(AMBER, AMBER));
                    userBtn.setTextColor(Color.rgb(0x1A, 0x12, 0x06));
                    onUserDown();
                    return true;
                case android.view.MotionEvent.ACTION_UP:
                case android.view.MotionEvent.ACTION_CANCEL:
                    userBtn.setBackground(rounded(Color.rgb(0x1B, 0x24, 0x2E), AMBER));
                    userBtn.setTextColor(INK);
                    if (ev.getActionMasked() == android.view.MotionEvent.ACTION_UP) {
                        onUserUp();
                        v.performClick();   // keeps accessibility services in the loop
                    }
                    return true;
                default:
                    return false;
            }
        });
        userRow.addView(userBtn);
        userHint = label("Tap for the next page \u00b7 twice for the media layer \u00b7 "
                       + "hold 1 s to arm HID.", 12, MUTED);
        LinearLayout.LayoutParams hintLp =
            new LinearLayout.LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f);
        hintLp.setMargins(dp(10), 0, 0, 0);
        userHint.setLayoutParams(hintLp);
        userRow.addView(userHint);
        col.addView(userRow);

        col.addView(heading("Switches"));
        LinearLayout swCard = card();
        swCard.addView(label("SW1 - 3 position", 12, MUTED));
        swCard.addView(track(sw1, true));
        TextView l2 = label("SW2 - 5 position", 12, MUTED);
        l2.setPadding(0, dp(12), 0, 0);
        swCard.addView(l2);
        swCard.addView(track(sw2, false));
        col.addView(swCard);

        col.addView(heading("Encoder"));
        LinearLayout encCard = card();
        encValue = label("0", 34, CYAN);
        encMeta = label("total 0   errors 0", 12, MUTED);
        encCard.addView(encValue);
        encCard.addView(encMeta);
        LinearLayout encBtns = new LinearLayout(this);
        encBtns.setOrientation(LinearLayout.HORIZONTAL);
        encBtns.addView(actionButton("CCW", v -> nudge(-1), 1f));
        encBtns.addView(actionButton("CW", v -> nudge(1), 1f));
        encCard.addView(encBtns);
        col.addView(encCard);

        LinearLayout stats = new LinearLayout(this);
        stats.setOrientation(LinearLayout.HORIZONTAL);
        statPage = statCell(stats, "PAGE");
        statHid = statCell(stats, "HID");
        statGame = statCell(stats, "GAME");
        statFps = statCell(stats, "FPS");
        col.addView(stats);

        col.addView(heading("Buttons A / B - PCF8574"));
        col.addView(lampGrid(pcfLamps, pcfText, Protocol.PCF_NAMES, 4, true));
        col.addView(heading("D-pad - header J3"));
        col.addView(lampGrid(btnLamps, btnText, Protocol.BTN_NAMES, 4, false));
        TextView hint = label("Tap a lamp to press it on the emulated panel.", 12, MUTED);
        hint.setPadding(0, dp(6), 0, 0);
        col.addView(hint);

        col.addView(heading("Serial console - 115200"));
        HorizontalScrollView chipScroll = new HorizontalScrollView(this);
        chipScroll.setHorizontalScrollBarEnabled(false);
        LinearLayout chips = new LinearLayout(this);
        chips.setOrientation(LinearLayout.HORIZONTAL);
        for (int i = 0; i < COMMANDS.length; i++) {
            final String c = COMMANDS[i];
            chips.addView(actionButton(c + "  " + COMMAND_LABELS[i], v -> send(c), 0f));
        }
        chipScroll.addView(chips);
        col.addView(chipScroll);

        LinearLayout entryRow = new LinearLayout(this);
        entryRow.setOrientation(LinearLayout.HORIZONTAL);
        final EditText entry = new EditText(this);
        entry.setHint("a command letter, or $src=DEMO;spd=88");
        entry.setHintTextColor(MUTED);
        entry.setTextColor(INK);
        entry.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        entry.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        entry.setBackground(rounded(SURFACE, LINE));
        entry.setPadding(dp(10), dp(10), dp(10), dp(10));
        entry.setLayoutParams(new LinearLayout.LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f));
        entryRow.addView(entry);
        entryRow.addView(actionButton("SEND", v -> {
            String t = entry.getText().toString().trim();
            if (!t.isEmpty()) { send(t); entry.setText(""); }
        }, 0f));
        col.addView(entryRow);

        logScroll = new ScrollView(this);
        logScroll.setBackground(rounded(Color.rgb(0x0B, 0x0F, 0x14), LINE));
        logScroll.setLayoutParams(new LinearLayout.LayoutParams(LayoutParams.MATCH_PARENT, dp(190)));
        logView = new TextView(this);
        logView.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11);
        logView.setTextColor(Color.rgb(0x9F, 0xB3, 0xC4));
        logView.setTypeface(android.graphics.Typeface.MONOSPACE);
        logView.setPadding(dp(10), dp(10), dp(10), dp(10));
        logScroll.addView(logView);
        LinearLayout.LayoutParams lp =
            new LinearLayout.LayoutParams(LayoutParams.MATCH_PARENT, dp(190));
        lp.topMargin = dp(10);
        logScroll.setLayoutParams(lp);
        col.addView(logScroll);

        // ---- the alarms on this phone, on the panel across the room.
        // Android hands out the next one and nothing else: a timestamp and who
        // set it. No notification access, nobody's messages.
        col.addView(heading("Alarms - tell the desktop app"));
        LinearLayout relayRow = new LinearLayout(this);
        relayRow.setOrientation(LinearLayout.HORIZONTAL);
        final EditText host = new EditText(this);
        host.setHint("192.168.1.20:8787");
        host.setHintTextColor(MUTED);
        host.setTextColor(INK);
        host.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        host.setInputType(InputType.TYPE_CLASS_TEXT
                          | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        host.setBackground(rounded(SURFACE, LINE));
        host.setPadding(dp(10), dp(10), dp(10), dp(10));
        host.setText(getPreferences(MODE_PRIVATE).getString("relayHost", ""));
        host.setLayoutParams(new LinearLayout.LayoutParams(0,
                LayoutParams.WRAP_CONTENT, 1f));
        relayRow.addView(host);
        relay = new AlarmRelay(this, this::log);
        relayRow.addView(actionButton("SEND MY ALARMS", v -> {
            String h = host.getText().toString().trim();
            getPreferences(MODE_PRIVATE).edit().putString("relayHost", h).apply();
            if (relay.isRunning()) {
                relay.stop();
                log("alarm relay off");
            } else {
                relay.start(h);
                log("alarm relay on - " + h);
            }
        }, 0f));
        col.addView(relayRow);
        col.addView(label("The app's Settings tab shows the address, next to "
                          + "\"Take alarms from the phone\". Only the next "
                          + "alarm's time and who set it leave this phone.",
                          12, MUTED));

        col.addView(heading("Source"));
        LinearLayout modes = new LinearLayout(this);
        modes.setOrientation(LinearLayout.HORIZONTAL);
        modeEmu = actionButton("EMULATOR", v -> { if (usbMode) usb.close(); }, 1f);
        modeUsb = actionButton("USB PANEL", v -> usb.connect(), 1f);
        modes.addView(modeEmu);
        modes.addView(modeUsb);
        col.addView(modes);
        paintMode(modeEmu, true);
        paintMode(modeUsb, false);
        TextView usbNote = label(
            "Plug the panel in with a USB-C OTG cable and tap USB PANEL. "
          + "It is found by the RP2040 vendor id 0x2E8A, so the port it lands on "
          + "does not matter.", 12, MUTED);
        usbNote.setPadding(0, dp(8), 0, 0);
        col.addView(usbNote);

        ScrollView root = new ScrollView(this);
        root.setBackgroundColor(GROUND);
        root.setFillViewport(true);
        root.addView(col);
        return root;
    }

    // ------------------------------------------------------------- UI helpers
    private int dp(int v) {
        return Math.round(TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v,
            getResources().getDisplayMetrics()));
    }

    private TextView label(String text, int sp, int color) {
        TextView t = new TextView(this);
        t.setText(text);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(color);
        return t;
    }

    private TextView heading(String text) {
        TextView t = label(text.toUpperCase(java.util.Locale.ROOT), 11, MUTED);
        t.setLetterSpacing(0.14f);
        t.setPadding(0, dp(18), 0, dp(8));
        return t;
    }

    private GradientDrawable rounded(int fill, int stroke) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(fill);
        g.setCornerRadius(dp(8));
        g.setStroke(dp(1), stroke);
        return g;
    }

    private LinearLayout card() {
        LinearLayout c = new LinearLayout(this);
        c.setOrientation(LinearLayout.VERTICAL);
        c.setBackground(rounded(SURFACE, LINE));
        c.setPadding(dp(12), dp(12), dp(12), dp(12));
        return c;
    }

    private Button actionButton(String text, View.OnClickListener onClick, float weight) {
        Button b = new Button(this);
        b.setText(text);
        b.setAllCaps(false);
        b.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        b.setTextColor(INK);
        b.setBackground(rounded(Color.rgb(0x1B, 0x24, 0x2E), LINE));
        b.setOnClickListener(onClick);
        LinearLayout.LayoutParams lp = weight > 0
            ? new LinearLayout.LayoutParams(0, LayoutParams.WRAP_CONTENT, weight)
            : new LinearLayout.LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT);
        lp.setMargins(dp(3), dp(6), dp(3), 0);
        b.setLayoutParams(lp);
        return b;
    }

    private LinearLayout track(Button[] into, boolean first) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        for (int i = 0; i < into.length; i++) {
            final int pos = i + 1;
            Button b = actionButton(String.valueOf(pos), v -> {
                if (usbMode) return;
                if (first) emu.sw1 = pos; else emu.sw2 = pos;
                goManual();
            }, 1f);
            into[i] = b;
            row.addView(b);
        }
        return row;
    }

    private void paintPosition(Button b, boolean on) {
        b.setBackground(rounded(on ? AMBER : Color.rgb(0x1B, 0x24, 0x2E), on ? AMBER : LINE));
        b.setTextColor(on ? Color.rgb(0x1A, 0x12, 0x06) : MUTED);
    }

    private GridLayout lampGrid(View[] lamps, TextView[] texts, String[] names,
                                int columns, boolean pcf) {
        GridLayout grid = new GridLayout(this);
        grid.setColumnCount(columns);
        for (int i = 0; i < names.length; i++) {
            final int index = i;
            LinearLayout cell = new LinearLayout(this);
            cell.setOrientation(LinearLayout.VERTICAL);
            cell.setGravity(Gravity.CENTER);
            cell.setBackground(rounded(Color.rgb(0x1B, 0x24, 0x2E), LINE));
            cell.setPadding(dp(8), dp(10), dp(8), dp(10));
            TextView t = label(names[i], 12, MUTED);
            t.setGravity(Gravity.CENTER);
            cell.addView(t);
            GridLayout.LayoutParams lp = new GridLayout.LayoutParams();
            lp.width = 0;
            lp.columnSpec = GridLayout.spec(i % columns, 1f);
            lp.setMargins(dp(3), dp(3), dp(3), dp(3));
            cell.setLayoutParams(lp);
            cell.setOnClickListener(v -> {
                if (usbMode) return;
                if (pcf) emu.pcf[index] = !emu.pcf[index]; else emu.btn[index] = !emu.btn[index];
                goManual();
            });
            lamps[i] = cell;
            texts[i] = t;
            grid.addView(cell);
        }
        return grid;
    }

    private void paintLamp(View cell, TextView text, boolean on) {
        cell.setBackground(rounded(on ? Color.rgb(0x3A, 0x2A, 0x12) : Color.rgb(0x1B, 0x24, 0x2E),
                                   on ? AMBER : LINE));
        text.setTextColor(on ? AMBER : MUTED);
    }

    private TextView statCell(LinearLayout row, String name) {
        LinearLayout cell = new LinearLayout(this);
        cell.setOrientation(LinearLayout.VERTICAL);
        cell.setBackground(rounded(SURFACE, LINE));
        cell.setPadding(dp(8), dp(8), dp(8), dp(8));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f);
        lp.setMargins(dp(3), dp(10), dp(3), 0);
        cell.setLayoutParams(lp);
        TextView caption = label(name, 9, MUTED);
        caption.setLetterSpacing(0.11f);
        TextView value = label("-", 14, INK);
        cell.addView(caption);
        cell.addView(value);
        row.addView(cell);
        return value;
    }

    private void paintMode(Button b, boolean active) {
        b.setBackground(rounded(active ? SURFACE : Color.rgb(0x14, 0x1B, 0x23), active ? AMBER : LINE));
        b.setTextColor(active ? INK : MUTED);
    }

    /**
     * The USER button. In emulator mode it drives FakePanel's own state machine,
     * with the real 250 ms double window and 1 s hold. Against a real panel it
     * cannot change the page - no serial command does, USER is a physical button
     * on the board - but the hold and the double press have console equivalents
     * ('u' and 'y'), so those are sent instead.
     */
    private void onUserDown() {
        if (usbMode) usbUserDown = System.currentTimeMillis(); else emu.userDown();
    }

    private void onUserUp() {
        if (!usbMode) { emu.userUp(); return; }
        long now = System.currentTimeMillis();
        if (now - usbUserDown >= 1000) { send("u"); return; }
        long gap = usbUserLast > 0 ? now - usbUserLast : 0;
        if (usbUserLast > 0 && gap < 250) { usbUserLast = 0; send("y"); return; }
        usbUserLast = now;
        log("USER can't change the page over serial - press it on the board.", BAD);
    }

    private void nudge(int delta) {
        if (usbMode) return;
        emu.enc += delta;
        emu.total++;
        goManual();
    }

    private void goManual() {
        emu.demo = false;
        if (!manual) {
            manual = true;
            log("[emulator] demo motion stopped - you're driving it now", CYAN);
        }
    }

    /** One argument, in the ordinary colour - what the alarm relay reports. */
    private void log(String text) {
        log(text, MUTED);
    }

    private void log(String text, int color) {
        int start = logBuf.length();
        logBuf.append(text).append('\n');
        logBuf.setSpan(new android.text.style.ForegroundColorSpan(color), start, logBuf.length(),
                       android.text.Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        if (++logLines > 250) {
            int cut = android.text.TextUtils.indexOf(logBuf, '\n');
            if (cut >= 0) { logBuf.delete(0, cut + 1); logLines--; }
        }
        logView.setText(logBuf);
        logScroll.post(() -> logScroll.fullScroll(View.FOCUS_DOWN));
    }
}
