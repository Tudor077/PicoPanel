package com.picopanel.pocket;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/**
 * Start relaying again after a restart, if it was on.
 *
 * A phone reboots overnight far more often than people think - an update, a
 * flat battery - and an alarm relay that quietly stopped at 3am is worse than
 * one that was never switched on, because you would have believed it.
 */
public class BootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context ctx, Intent intent) {
        if (!RelayService.isEnabled(ctx)) return;
        String host = ctx.getSharedPreferences(RelayService.PREFS,
                                               Context.MODE_PRIVATE)
                         .getString(RelayService.KEY_HOST, "");
        if (host == null || host.isEmpty()) return;
        RelayService.enable(ctx, host, true);
    }
}
