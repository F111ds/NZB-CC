package com.gathin.tvhome;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.util.AttributeSet;
import android.util.TypedValue;
import android.view.View;
import android.view.animation.LinearInterpolator;

import androidx.annotation.Nullable;

import java.util.List;

/**
 * NZB-CC: the news ticker's text, scrolling right to left at a slow, steady speed and fading out at both ends.
 * Drawn straight on the canvas (no TextView marquee, which needs focus and runs at a fixed speed). It only moves
 * while started: Home stops it whenever it isn't on screen.
 */
public class TickerView extends View {
    private static final float SPEED_DP_PER_S = 38f;
    private static final String SEP = "   •   ";

    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint dotPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private String[] parts = new String[0];
    private float textWidth, offset;
    private ValueAnimator anim;
    private boolean wanted;
    private final float density;

    public TickerView(Context context, @Nullable AttributeSet attrs) {
        super(context, attrs);
        density = getResources().getDisplayMetrics().density;
        paint.setColor(0xE6FFFFFF);
        paint.setTextSize(TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, 12, getResources().getDisplayMetrics()));
        paint.setTypeface(UiFont.get(context, R.string.font_regular));
        dotPaint.set(paint);
        dotPaint.setColor(0x59FFFFFF);
        setHorizontalFadingEdgeEnabled(true);
        setFadingEdgeLength(Math.round(24 * density));
        setWillNotDraw(false);
    }

    void setHeadlines(List<String> headlines) {
        parts = headlines.toArray(new String[0]);
        textWidth = 0;
        for (String p : parts) textWidth += paint.measureText(p) + paint.measureText(SEP);
        offset = 0;
        invalidate();
        if (wanted) start();
    }

    void start() {
        wanted = true;
        if (parts.length == 0 || textWidth <= 0) return;
        if (anim != null && anim.isRunning()) return;
        float pxPerMs = SPEED_DP_PER_S * density / 1000f;
        anim = ValueAnimator.ofFloat(0f, 1f);
        anim.setDuration(Math.max(1000L, (long) (textWidth / pxPerMs)));
        anim.setInterpolator(new LinearInterpolator());
        anim.setRepeatCount(ValueAnimator.INFINITE);
        anim.addUpdateListener(a -> {
            offset = (float) a.getAnimatedValue() * textWidth;
            invalidate();
        });
        anim.start();
    }

    void stop() {
        wanted = false;
        if (anim != null) anim.cancel();
        anim = null;
    }

    @Override
    protected void onDetachedFromWindow() {
        if (anim != null) anim.cancel();
        anim = null;
        super.onDetachedFromWindow();
    }

    @Override
    protected float getLeftFadingEdgeStrength() {
        return 1f;
    }

    @Override
    protected float getRightFadingEdgeStrength() {
        return 1f;
    }

    @Override
    protected void onDraw(Canvas canvas) {
        if (parts.length == 0 || textWidth <= 0) return;
        float baseline = getHeight() / 2f - (paint.descent() + paint.ascent()) / 2f;
        float sep = paint.measureText(SEP);
        // Starts just inside the right edge and repeats end to end, so the line never runs out.
        float x = getPaddingLeft() + Math.round(16 * density) - offset;
        while (x < getWidth()) {
            for (String p : parts) {
                float w = paint.measureText(p);
                if (x + w > 0 && x < getWidth()) canvas.drawText(p, x, baseline, paint);
                x += w;
                if (x + sep > 0 && x < getWidth()) canvas.drawText(SEP, x, baseline, dotPaint);
                x += sep;
                if (x >= getWidth()) break;
            }
        }
    }
}
