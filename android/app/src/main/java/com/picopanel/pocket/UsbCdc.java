package com.picopanel.pocket;

import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.hardware.usb.UsbConstants;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbDeviceConnection;
import android.hardware.usb.UsbEndpoint;
import android.hardware.usb.UsbInterface;
import android.hardware.usb.UsbManager;

import java.nio.charset.StandardCharsets;
import java.util.HashMap;

/**
 * CDC-ACM over Android's USB host API, written out rather than pulled in.
 *
 * The RP2040 with Philhower's core and the Pico SDK USB stack enumerates as a
 * plain CDC serial device, which is two interfaces: a control interface
 * (class 0x02) that takes SET_LINE_CODING and SET_CONTROL_LINE_STATE, and a
 * data interface (class 0x0A) carrying one bulk endpoint each way. That is all
 * this needs, so there is no reason to take a library dependency for it.
 *
 * DTR is the part people miss: until SET_CONTROL_LINE_STATE raises it, the
 * board's USB stack considers the port closed and says nothing at all.
 */
public final class UsbCdc {

    public static final int RP2040_VID = 0x2E8A;
    private static final String ACTION_PERMISSION = "com.picopanel.pocket.USB_PERMISSION";
    private static final int BAUD = 115200;

    public interface Listener {
        void onLine(String line);
        void onStatus(String message, boolean connected);
    }

    private final Context ctx;
    private final UsbManager manager;
    private final Listener listener;

    private UsbDeviceConnection conn;
    private UsbInterface dataIface;
    private UsbEndpoint epIn, epOut;
    private Thread reader;
    private volatile boolean running;
    private BroadcastReceiver receiver;

    public UsbCdc(Context context, Listener listener) {
        this.ctx = context.getApplicationContext();
        this.manager = (UsbManager) ctx.getSystemService(Context.USB_SERVICE);
        this.listener = listener;
    }

    /** @return a panel on the bus, or null. Matched by vendor id like the PC app. */
    public UsbDevice find() {
        HashMap<String, UsbDevice> list = manager.getDeviceList();
        for (UsbDevice d : list.values()) if (d.getVendorId() == RP2040_VID) return d;
        // Anything exposing a CDC data interface will do, in case it enumerates
        // under a different vendor id after a firmware change.
        for (UsbDevice d : list.values())
            for (int i = 0; i < d.getInterfaceCount(); i++)
                if (d.getInterface(i).getInterfaceClass() == UsbConstants.USB_CLASS_CDC_DATA) return d;
        return null;
    }

    /** Asks for permission if needed, then opens. Status arrives on the listener. */
    public void connect() {
        UsbDevice dev = find();
        if (dev == null) {
            listener.onStatus("No panel found. Check the OTG cable.", false);
            return;
        }
        if (manager.hasPermission(dev)) {
            open(dev);
            return;
        }
        if (receiver == null) {
            receiver = new BroadcastReceiver() {
                @Override public void onReceive(Context c, Intent intent) {
                    if (!ACTION_PERMISSION.equals(intent.getAction())) return;
                    UsbDevice d = intent.getParcelableExtra(UsbManager.EXTRA_DEVICE);
                    if (intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false) && d != null) {
                        open(d);
                    } else {
                        listener.onStatus("Permission refused for the panel.", false);
                    }
                }
            };
            IntentFilter f = new IntentFilter(ACTION_PERMISSION);
            if (android.os.Build.VERSION.SDK_INT >= 33) {
                ctx.registerReceiver(receiver, f, Context.RECEIVER_NOT_EXPORTED);
            } else {
                ctx.registerReceiver(receiver, f);
            }
        }
        int flags = android.os.Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0;
        manager.requestPermission(dev,
            PendingIntent.getBroadcast(ctx, 0, new Intent(ACTION_PERMISSION).setPackage(ctx.getPackageName()), flags));
    }

    private void open(UsbDevice dev) {
        UsbInterface control = null, data = null;
        for (int i = 0; i < dev.getInterfaceCount(); i++) {
            UsbInterface itf = dev.getInterface(i);
            if (itf.getInterfaceClass() == UsbConstants.USB_CLASS_COMM) control = itf;
            else if (itf.getInterfaceClass() == UsbConstants.USB_CLASS_CDC_DATA) data = itf;
        }
        if (data == null) { listener.onStatus("That device has no CDC data interface.", false); return; }

        UsbEndpoint in = null, out = null;
        for (int i = 0; i < data.getEndpointCount(); i++) {
            UsbEndpoint ep = data.getEndpoint(i);
            if (ep.getType() != UsbConstants.USB_ENDPOINT_XFER_BULK) continue;
            if (ep.getDirection() == UsbConstants.USB_DIR_IN) in = ep; else out = ep;
        }
        if (in == null || out == null) { listener.onStatus("No bulk endpoints on the panel.", false); return; }

        UsbDeviceConnection c = manager.openDevice(dev);
        if (c == null) { listener.onStatus("Could not open the panel.", false); return; }
        if (control != null) c.claimInterface(control, true);
        if (!c.claimInterface(data, true)) {
            c.close();
            listener.onStatus("The data interface is busy - another app holds it.", false);
            return;
        }

        int ctrlIndex = control != null ? control.getId() : 0;
        // SET_LINE_CODING: 115200 8N1. Some stacks refuse it; CDC still runs.
        byte[] coding = {
            (byte) (BAUD & 0xFF), (byte) ((BAUD >> 8) & 0xFF),
            (byte) ((BAUD >> 16) & 0xFF), (byte) ((BAUD >> 24) & 0xFF),
            0, 0, 8
        };
        c.controlTransfer(0x21, 0x20, 0, ctrlIndex, coding, coding.length, 200);
        // SET_CONTROL_LINE_STATE: DTR | RTS. Without this the board stays silent.
        c.controlTransfer(0x21, 0x22, 0x03, ctrlIndex, null, 0, 200);

        conn = c; dataIface = data; epIn = in; epOut = out;
        running = true;
        reader = new Thread(this::readLoop, "usb-cdc-reader");
        reader.setDaemon(true);
        reader.start();
        listener.onStatus("Connected to the panel.", true);
    }

    private void readLoop() {
        byte[] buf = new byte[epIn.getMaxPacketSize()];
        StringBuilder line = new StringBuilder();
        while (running) {
            int n = conn.bulkTransfer(epIn, buf, buf.length, 250);
            if (n < 0) continue;                       // a timeout, not a failure
            for (int i = 0; i < n; i++) {
                char ch = (char) (buf[i] & 0xFF);
                if (ch == '\n') {
                    String s = line.toString().trim();
                    line.setLength(0);
                    if (!s.isEmpty()) listener.onLine(s);
                    // A runaway line means we lost framing; don't grow forever.
                } else if (line.length() < 4096) {
                    if (ch != '\r') line.append(ch);
                } else {
                    line.setLength(0);
                }
            }
        }
    }

    public boolean isOpen() { return conn != null; }

    public void send(String text) {
        if (conn == null) return;
        byte[] data = (text + "\n").getBytes(StandardCharsets.US_ASCII);
        conn.bulkTransfer(epOut, data, data.length, 500);
    }

    public void close() {
        running = false;
        if (reader != null) { try { reader.join(400); } catch (InterruptedException ignored) {} reader = null; }
        if (conn != null) {
            if (dataIface != null) conn.releaseInterface(dataIface);
            conn.close();
            conn = null;
        }
        if (receiver != null) {
            try { ctx.unregisterReceiver(receiver); } catch (IllegalArgumentException ignored) {}
            receiver = null;
        }
        listener.onStatus("Disconnected.", false);
    }
}
