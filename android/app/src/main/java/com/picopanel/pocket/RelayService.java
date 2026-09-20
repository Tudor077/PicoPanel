package com.picopanel.pocket;

import android.app.AlarmManager;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;

/**
 * Keeps telling the desktop app about this phone's next alarm, with the app
 * closed and the phone in a pocket.
 *
 * The question this answers: the panel sits on the PC, the alarms are on the
 * phone, and nothing on the PC can ask the phone anything - there is no server
 * there to ask. So the phone has to speak first, and something on the phone has
 * to be awake to do it.
 *
 * Android tells any app when the next alarm changes, through
 * ACTION_NEXT_ALARM_CLOCK_CHANGED - but since Android 8 that no longer reaches
 * a receiver declared in the manifest, only one registered by a running
 * process. Hence a foreground service, with the quietest notification the
 * system allows. There is no way around it: an app with nothing running cannot
 * be told anything.
 *
 * A heartbeat every fifteen minutes on top, because a broadcast that is missed
 * - doze, a reboot, a phone that was off - would otherwise leave the panel
 * counting down to an alarm that has been moved.
 */
public class RelayService extends Service {

    public static final String PREFS = "PicoPanelPocket";
    public static final String KEY_HOST = "relayHost";
    public static final String KEY_ON = "relayOn";

    private static final String CHANNEL = "relay";
    private static final int NOTE_ID = 42;
    private static final long HEARTBEAT_MS = 15 * 60_000L;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private AlarmRelay relay;
    private BroadcastReceiver watcher;

    public static void enable(Context ctx, String host, boolean on) {
        SharedPreferences p = ctx.getSharedPreferences(PREFS, MODE_PRIVATE);
        p.edit().putString(KEY_HOST, host).putBoolean(KEY_ON, on).apply();
        Intent i = new Intent(ctx, RelayService.class);
        if (on) {
            if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i);
            else ctx.startService(i);
        } else {
            ctx.stopService(i);
        }
    }

    public static boolean isEnabled(Context ctx) {
        return ctx.getSharedPreferences(PREFS, MODE_PRIVATE)
                  .getBoolean(KEY_ON, false);
    }

    @Override public void onCreate() {
        super.onCreate();
        relay = new AlarmRelay(this, msg -> { });     // nobody to show it to
        startForeground(NOTE_ID, note("Telling the panel about your alarms"));

        // The system says when the next alarm changes - which is the whole
        // reason this is a service and not a timer.
        watcher = new BroadcastReceiver() {
            @Override public void onReceive(Context c, Intent i) {
                relay.push();
            }
        };
        IntentFilter f = new IntentFilter(AlarmManager.ACTION_NEXT_ALARM_CLOCK_CHANGED);
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(watcher, f, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(watcher, f);
        }
        handler.post(beat);
    }

    private final Runnable beat = new Runnable() {
        @Override public void run() {
            String host = getSharedPreferences(PREFS, MODE_PRIVATE)
                              .getString(KEY_HOST, "");
            relay.start(host);        // idempotent: sets the address and pushes
            relay.push();
            handler.postDelayed(this, HEARTBEAT_MS);
        }
    };

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        handler.removeCallbacks(beat);
        handler.post(beat);
        return START_STICKY;          // come back if the system kills us
    }

    @Override public void onDestroy() {
        handler.removeCallbacks(beat);
        if (watcher != null) {
            try {
                unregisterReceiver(watcher);
            } catch (IllegalArgumentException ignored) {
            }
            watcher = null;
        }
        if (relay != null) relay.stop();
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent intent) {
        return null;
    }

    private Notification note(String text) {
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationManager nm = getSystemService(NotificationManager.class);
            NotificationChannel ch = new NotificationChannel(
                    CHANNEL, "Alarm relay", NotificationManager.IMPORTANCE_MIN);
            ch.setShowBadge(false);
            nm.createNotificationChannel(ch);
        }
        PendingIntent open = PendingIntent.getActivity(
                this, 0, new Intent(this, MainActivity.class),
                PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL)
                : new Notification.Builder(this);
        return b.setContentTitle("PicoPanel")
                .setContentText(text)
                .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
                .setContentIntent(open)
                .setOngoing(true)
                .build();
    }
}
