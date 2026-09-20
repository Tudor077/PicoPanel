package com.picopanel.pocket;

import android.app.AlarmManager;
import android.content.Context;
import android.os.Handler;
import android.os.Looper;

import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Your alarms live here, on the phone. That is what actually wakes you, so the
 * panel should know about them rather than asking you to type them in twice.
 *
 * Android hands out the next one through {@link AlarmManager#getNextAlarmClock()}
 * - no permission, no notification access, no reading anybody's mail. It is one
 * timestamp and the name of whoever set it, which is exactly what the panel
 * needs and nothing more.
 *
 * It goes to the desktop app over plain HTTP on the local network:
 * {@code POST /alarm {"at_ms": ..., "text": "..."}}. See pc/phone.py.
 */
public class AlarmRelay {

    /** How often to look. An alarm is a minute wide; this is not a race. */
    private static final long EVERY_MS = 60_000L;
    private static final int TIMEOUT_MS = 4000;

    public interface Listener {
        void onRelay(String message);
    }

    private final Context ctx;
    private final Listener listener;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private String host = "";          // "192.168.1.20:8787"
    private boolean running = false;
    private long lastSent = -1;        // the timestamp we last told it about

    public AlarmRelay(Context ctx, Listener listener) {
        this.ctx = ctx;
        this.listener = listener;
    }

    public void start(String host) {
        this.host = host == null ? "" : host.trim();
        if (this.host.isEmpty()) {
            say("no address for the panel's app");
            return;
        }
        if (!this.host.contains(":")) this.host += ":8787";
        lastSent = -1;
        if (!running) {
            running = true;
            handler.post(tick);
        } else {
            handler.removeCallbacks(tick);
            handler.post(tick);
        }
    }

    public void stop() {
        running = false;
        handler.removeCallbacks(tick);
    }

    public boolean isRunning() {
        return running;
    }

    private final Runnable tick = new Runnable() {
        @Override public void run() {
            if (!running) return;
            push();
            handler.postDelayed(this, EVERY_MS);
        }
    };

    /** Ask Android what is set, and tell the panel - but only when it changes. */
    public void push() {
        long at = 0L;
        String who = "";
        try {
            AlarmManager am = (AlarmManager) ctx.getSystemService(Context.ALARM_SERVICE);
            AlarmManager.AlarmClockInfo info = am == null ? null : am.getNextAlarmClock();
            if (info != null) {
                at = info.getTriggerTime();
                if (info.getShowIntent() != null
                        && info.getShowIntent().getCreatorPackage() != null) {
                    who = shortName(info.getShowIntent().getCreatorPackage());
                }
            }
        } catch (Throwable t) {
            say("cannot read the next alarm: " + t);
            return;
        }
        if (at == lastSent) return;          // nothing new to say
        final long when = at;
        final String label = who;
        new Thread(() -> send(when, label), "alarm-relay").start();
        lastSent = at;
    }

    /** "com.google.android.deskclock" -> "DESKCLOCK". The panel has 20 columns. */
    private static String shortName(String pkg) {
        int dot = pkg.lastIndexOf('.');
        String s = dot >= 0 ? pkg.substring(dot + 1) : pkg;
        if (s.length() > 12) s = s.substring(0, 12);
        return s.toUpperCase();
    }

    private void send(long atMs, String text) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL("http://" + host + "/alarm").openConnection();
            c.setConnectTimeout(TIMEOUT_MS);
            c.setReadTimeout(TIMEOUT_MS);
            c.setDoOutput(true);
            c.setRequestMethod("POST");
            c.setRequestProperty("Content-Type", "application/json");
            JSONObject body = new JSONObject();
            if (atMs > 0) {
                body.put("at_ms", atMs);
                body.put("text", text);
            }                                  // an empty body means "none"
            byte[] raw = body.toString().getBytes("UTF-8");
            c.setFixedLengthStreamingMode(raw.length);
            OutputStream os = c.getOutputStream();
            os.write(raw);
            os.close();
            int code = c.getResponseCode();
            say(atMs > 0
                    ? "told the panel: " + android.text.format.DateFormat
                        .format("EEE HH:mm", atMs) + " (" + code + ")"
                    : "told the panel there is no alarm (" + code + ")");
        } catch (Throwable t) {
            lastSent = -1;                     // failed: say it again next time
            // The message, not just the class. "IOException" on its own is
            // what a blocked cleartext connection looks like, and it told
            // nobody anything.
            String why = t.getMessage();
            say("could not reach " + host + ": " + t.getClass().getSimpleName()
                + (why == null ? "" : " - " + why));
        } finally {
            if (c != null) c.disconnect();
        }
    }

    private void say(final String msg) {
        if (listener != null) handler.post(() -> listener.onRelay(msg));
    }
}
