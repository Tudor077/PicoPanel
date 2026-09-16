package com.picopanel.pocket;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.util.AttributeSet;
import android.view.View;

/**
 * The panel's own screen, drawn from the frame buffer the board sends.
 *
 * Pixels are drawn as rectangles rather than through a scaled Bitmap: at
 * 128x32 that is 4096 rects at ten frames a second, which is nothing, and it
 * keeps every pixel crisp at any width without filtering.
 */
public class OledView extends View {

    private static final int LIT = Color.rgb(0xE6, 0xF2, 0xFF);   // panel.py's MIRROR_LIT
    private static final int DARK = Color.rgb(0x0B, 0x0F, 0x14);  // panel.py's MIRROR_DARK

    private final Paint paint = new Paint();
    private Protocol.Frame frame;
    private String placeholder = "PICOPANEL";

    public OledView(Context c) { super(c); }
    public OledView(Context c, AttributeSet a) { super(c, a); }

    public void setFrame(Protocol.Frame f) { frame = f; invalidate(); }

    public void clear(String message) { frame = null; placeholder = message; invalidate(); }

    @Override protected void onMeasure(int wSpec, int hSpec) {
        int w = MeasureSpec.getSize(wSpec);
        setMeasuredDimension(w, Math.round(w * 32f / 128f));   // the panel's own ratio
    }

    @Override protected void onDraw(Canvas canvas) {
        paint.setColor(DARK);
        canvas.drawRect(0, 0, getWidth(), getHeight(), paint);
        Protocol.Frame f = frame;
        if (f == null) {
            paint.setColor(LIT);
            paint.setTextSize(getHeight() * 0.30f);
            paint.setAntiAlias(true);
            canvas.drawText(placeholder, getWidth() * 0.04f, getHeight() * 0.60f, paint);
            return;
        }
        float sx = getWidth() / (float) f.width, sy = getHeight() / (float) f.height;
        paint.setAntiAlias(false);
        paint.setColor(LIT);
        for (int y = 0; y < f.height; y++) {
            for (int x = 0; x < f.width; x++) {
                if (!f.pixel(x, y)) continue;
                canvas.drawRect(x * sx, y * sy, (x + 1) * sx, (y + 1) * sy, paint);
            }
        }
    }
}
