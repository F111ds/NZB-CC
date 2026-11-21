package com.gathin.tvhome;

import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.ColorFilter;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.graphics.RadialGradient;
import android.graphics.Rect;
import android.graphics.Shader;
import android.graphics.drawable.Animatable;
import android.graphics.drawable.Drawable;
import android.os.SystemClock;

import androidx.annotation.NonNull;

/**
 * NZB-CC: HAL's eye on his dock tile, drawn like on his own screen (a metal rim, a black ring and the red lens),
 * that now and then glances around (the light moves inside the lens) and glows brighter while his tile has focus.
 * Redrawn about 20 times a second while it is on screen.
 */
final class HalEyeDrawable extends Drawable implements Animatable, Runnable {
    /** The tile with focus is HAL's (set by MainActivity). */
    static volatile boolean focused;
    /** Where along the poster row the selection is (-1 left .. 1 right), or NaN when it isn't on the row. */
    static volatile float watchX = Float.NaN;

    private static final int SIZE = 192;
    private final Paint rim = new Paint(Paint.ANTI_ALIAS_FLAG), ring = new Paint(Paint.ANTI_ALIAS_FLAG),
            lens = new Paint(Paint.ANTI_ALIAS_FLAG), halo = new Paint(Paint.ANTI_ALIAS_FLAG),
            shade = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final long start = SystemClock.uptimeMillis();
    private boolean running;
    private float glow;   // 0 at rest, 1 focused (eased)
    private int rimFor = -1;
    private float gx, gy;   // where the light is (eased)
    private float nextBlink = 6f;
    private final java.util.Random rnd = new java.util.Random();

    @Override
    public void draw(@NonNull Canvas c) {
        Rect b = getBounds();
        float s = Math.min(b.width(), b.height());
        if (s <= 0) return;
        float cx = b.exactCenterX(), cy = b.exactCenterY(), r = s / 2f * 0.76f;   // room for the glow inside the bounds
        float t = (SystemClock.uptimeMillis() - start) / 1000f;
        glow += ((focused ? 1f : 0f) - glow) * 0.18f;
        if (rimFor != Math.round(r)) {
            rimFor = Math.round(r);
            // a polished chrome rim: bright and dark bands round it, like a lens bezel
            android.graphics.SweepGradient chrome = new android.graphics.SweepGradient(cx, cy,
                    new int[]{0xFFF4F6F8, 0xFF7C828A, 0xFF2A2D32, 0xFFD8DCE0, 0xFF5C6168, 0xFF1E2024, 0xFFE9ECEF, 0xFFF4F6F8},
                    new float[]{0f, 0.15f, 0.3f, 0.48f, 0.62f, 0.78f, 0.92f, 1f});
            android.graphics.Matrix m = new android.graphics.Matrix();
            m.setRotate(200, cx, cy);
            chrome.setLocalMatrix(m);
            rim.setShader(chrome);
            ring.setColor(0xFF0B0B0D);
        }
        // the glow round the eye: brighter with focus
        float hr = r * (1.18f + 0.12f * glow);
        halo.setShader(new RadialGradient(cx, cy, hr, new int[]{argb(0.35f + 0.4f * glow, 0xFF2A12), 0x00FF2A12},
                null, Shader.TileMode.CLAMP));
        c.drawCircle(cx, cy, hr, halo);
        c.drawCircle(cx, cy, r, rim);
        c.drawCircle(cx, cy, r * 0.86f, ring);
        // He watches the selection move along the posters (looking up at the row), else glances around.
        float wx = watchX;
        float[] g = Float.isNaN(wx) ? HalGaze.at(t) : new float[]{wx * 0.9f, -0.75f};
        gx += (g[0] - gx) * 0.25f;
        gy += (g[1] - gy) * 0.25f;
        float lr = r * 0.74f;
        // the light travels well across the lens so the glances read from the couch
        float lx = cx + gx * lr * 0.34f, ly = cy + gy * lr * 0.26f;
        float bright = 0.85f + 0.15f * glow;
        lens.setShader(new RadialGradient(lx, ly, lr * (1f + 0.08f * glow),
                new int[]{0xFFFFFBE0, 0xFFFFD27A, 0xFFFF5A1F, 0xFFD4140A, 0xFF5A0402, 0xFF120000, 0xFF000000},
                new float[]{0f, 0.06f * bright, 0.16f * bright, 0.34f, 0.6f, 0.88f, 1f}, Shader.TileMode.CLAMP));
        c.save();
        android.graphics.Path clip = new android.graphics.Path();
        clip.addCircle(cx, cy, lr, android.graphics.Path.Direction.CW);
        c.clipPath(clip);
        c.drawCircle(cx, cy, lr, lens);
        // Now and then he blinks: the lens dims and comes back (every 6 to 14 seconds, 0.6 s long).
        if (t > nextBlink + 0.6f) nextBlink = t + 6f + rnd.nextFloat() * 8f;
        if (t >= nextBlink) {
            float bl = (float) Math.sin((t - nextBlink) / 0.6f * Math.PI);
            if (bl > 0) {
                shade.setColor(Color.argb(Math.round(190 * bl), 0, 0, 0));
                c.drawCircle(cx, cy, lr, shade);
            }
        }
        c.restore();
        if (running) scheduleSelf(this, SystemClock.uptimeMillis() + 50);
    }

    private static int argb(float a, int rgb) {
        return (Math.round(Math.max(0f, Math.min(1f, a)) * 255) << 24) | (rgb & 0xFFFFFF);
    }

    @Override
    public void run() {
        invalidateSelf();
    }

    @Override
    public void start() {
        if (running) return;
        running = true;
        invalidateSelf();
    }

    @Override
    public void stop() {
        running = false;
        unscheduleSelf(this);
    }

    @Override
    public boolean isRunning() {
        return running;
    }

    @Override
    public boolean setVisible(boolean visible, boolean restart) {
        boolean changed = super.setVisible(visible, restart);
        if (visible) start();
        else stop();
        return changed;
    }

    @Override
    public int getIntrinsicWidth() {
        return SIZE;
    }

    @Override
    public int getIntrinsicHeight() {
        return SIZE;
    }

    @Override
    public void setAlpha(int alpha) {
    }

    @Override
    public void setColorFilter(ColorFilter cf) {
    }

    @Override
    public int getOpacity() {
        return PixelFormat.TRANSLUCENT;
    }
}
