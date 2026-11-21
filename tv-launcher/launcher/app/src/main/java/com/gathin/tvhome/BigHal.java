package com.gathin.tvhome;

import android.animation.ValueAnimator;
import android.content.Context;
import android.content.Intent;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.SweepGradient;
import android.graphics.Typeface;
import android.text.TextUtils;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * NZB-CC: HAL's big eye at the top of the HAL-style home, under the top line, with his latest line from the PC
 * beside it. The eye does HAL's moving (glances, watching the selection along the posters, blinks, the glow), a
 * slow ring of light turns round it, and it can be selected (Up from the posters): OK opens his monitor screens.
 * His icon is no longer in the dock.
 */
final class BigHal {
    interface Host {
        void onBigHalDown();
        void onBigHalUp();
    }

    private final Context ctx;
    private final float density;
    final FrameLayout view;
    final View eye;
    private final View ring;
    private final LinearLayout words;
    private final TextView line, hint;
    private final int eyePx, ringPx;
    private ValueAnimator spin;
    private float spinDeg;

    BigHal(Context ctx, FrameLayout root, int insertAt, Host host) {
        this.ctx = ctx;
        density = ctx.getResources().getDisplayMetrics().density;
        eyePx = dp(76);
        ringPx = Math.round(eyePx * 1.5f);
        view = new FrameLayout(ctx);
        view.setClipChildren(false);
        view.setClipToPadding(false);
        view.setVisibility(View.GONE);

        ring = new View(ctx) {
            final Paint arc = new Paint(Paint.ANTI_ALIAS_FLAG), track = new Paint(Paint.ANTI_ALIAS_FLAG);
            final RectF box = new RectF();

            @Override
            protected void onDraw(Canvas c) {
                float w = getWidth(), h = getHeight(), sw = dp(3);
                box.set(sw, sw, w - sw, h - sw);
                track.setStyle(Paint.Style.STROKE);
                track.setStrokeWidth(dp(1));
                track.setColor(0x26FFC8B4);
                c.drawOval(box, track);
                if (arc.getShader() == null) {
                    arc.setShader(new SweepGradient(w / 2f, h / 2f, new int[]{0x00FF8C5A, 0x00FF8C5A, 0xCCFF8C5A},
                            new float[]{0f, 0.72f, 1f}));
                    arc.setStyle(Paint.Style.STROKE);
                    arc.setStrokeWidth(sw);
                    arc.setStrokeCap(Paint.Cap.ROUND);
                }
                c.save();
                c.rotate(spinDeg, w / 2f, h / 2f);
                c.drawArc(box, 0, 359, false, arc);
                c.restore();
            }
        };
        FrameLayout.LayoutParams rl = new FrameLayout.LayoutParams(ringPx, ringPx, Gravity.TOP | Gravity.CENTER_HORIZONTAL);
        view.addView(ring, rl);

        eye = new View(ctx);
        eye.setId(View.generateViewId());
        HalEyeDrawable d = new HalEyeDrawable();
        eye.setBackground(d);
        eye.setFocusable(true);
        eye.setFocusableInTouchMode(false);
        eye.setContentDescription("HAL 9000");
        // the drawable's own glow needs a little room round the eye
        int eyeBox = Math.round(eyePx / 0.76f);
        FrameLayout.LayoutParams el = new FrameLayout.LayoutParams(eyeBox, eyeBox, Gravity.TOP | Gravity.CENTER_HORIZONTAL);
        el.topMargin = (ringPx - eyeBox) / 2;
        view.addView(eye, el);

        words = new LinearLayout(ctx);
        words.setOrientation(LinearLayout.VERTICAL);
        TextView kicker = text(9, 0xFFFF8A66, R.string.font_medium);
        kicker.setText("HAL 9000");
        kicker.setLetterSpacing(0.3f);
        words.addView(kicker);
        line = text(13, 0xE6FFE6DC, R.string.font_regular);
        line.setMaxLines(2);
        line.setEllipsize(TextUtils.TruncateAt.END);
        line.setMaxWidth(dp(330));
        LinearLayout.LayoutParams ll = new LinearLayout.LayoutParams(-2, -2);
        ll.topMargin = dp(6);
        words.addView(line, ll);
        hint = text(9, 0xBFFFDCCD, R.string.font_regular);
        hint.setText("OK  ›  PC, network, downloads and more");
        hint.setLetterSpacing(0.06f);
        hint.setVisibility(View.INVISIBLE);
        LinearLayout.LayoutParams hl = new LinearLayout.LayoutParams(-2, -2);
        hl.topMargin = dp(6);
        words.addView(hint, hl);
        view.addView(words, new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.START));

        eye.setOnFocusChangeListener((v, has) -> {
            HalEyeDrawable.focused = has;
            eye.animate().scaleX(has ? 1.08f : 1f).scaleY(has ? 1.08f : 1f).setDuration(200).start();
            hint.setVisibility(has ? View.VISIBLE : View.INVISIBLE);
        });
        eye.setOnClickListener(v -> {
            Intent i = new Intent(ctx, HalActivity.class);
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            ctx.startActivity(i);
        });
        eye.setOnKeyListener((v, code, e) -> {
            if (e.getAction() != KeyEvent.ACTION_DOWN) return code == KeyEvent.KEYCODE_DPAD_UP || code == KeyEvent.KEYCODE_DPAD_DOWN
                    || code == KeyEvent.KEYCODE_DPAD_LEFT || code == KeyEvent.KEYCODE_DPAD_RIGHT;
            if (code == KeyEvent.KEYCODE_DPAD_DOWN) {
                host.onBigHalDown();
                return true;
            }
            if (code == KeyEvent.KEYCODE_DPAD_UP) {
                host.onBigHalUp();
                return true;
            }
            return code == KeyEvent.KEYCODE_DPAD_LEFT || code == KeyEvent.KEYCODE_DPAD_RIGHT;
        });
        root.addView(view, insertAt, new FrameLayout.LayoutParams(-1, -2, Gravity.TOP));
    }

    /** Places the block with its top at y; the text starts right of the ring. */
    void layout(int rootW, int y) {
        view.setY(y);
        FrameLayout.LayoutParams wl = (FrameLayout.LayoutParams) words.getLayoutParams();
        int left = rootW / 2 + ringPx / 2 + dp(18);
        int top = ringPx / 2 - dp(22);
        if (wl.leftMargin != left || wl.topMargin != top) {
            wl.leftMargin = left;
            wl.topMargin = top;
            words.setLayoutParams(wl);
        }
    }

    int height() {
        return ringPx;
    }

    void setShown(boolean on) {
        view.setVisibility(on ? View.VISIBLE : View.GONE);
    }

    boolean shown() {
        return view.getVisibility() == View.VISIBLE;
    }

    /** HAL's latest line (greeting and quote from the PC); "" when the PC doesn't answer. */
    void setLine(String text) {
        line.setText(text == null || text.isEmpty() ? "I can't reach the PC right now." : text);
    }

    /** The ring turns slowly (one turn in 9 seconds) while Home is on screen with animations on. */
    void motion(boolean on) {
        if (on) {
            if (spin == null) {
                spin = ValueAnimator.ofFloat(0f, 360f);
                spin.setDuration(9000);
                spin.setRepeatCount(ValueAnimator.INFINITE);
                spin.setInterpolator(new android.view.animation.LinearInterpolator());
                spin.addUpdateListener(a -> {
                    spinDeg = (float) a.getAnimatedValue();
                    ring.invalidate();
                });
            }
            if (spin.isPaused()) spin.resume();
            else if (!spin.isStarted()) spin.start();
        } else if (spin != null) {
            spin.pause();
        }
    }

    boolean focus() {
        return shown() && eye.requestFocus();
    }

    /** The eye's centre and size on screen (root coordinates), for HAL's wake-up. */
    float[] eyeCentre(FrameLayout root) {
        android.graphics.Rect r = new android.graphics.Rect(0, 0, eye.getWidth(), eye.getHeight());
        root.offsetDescendantRectToMyCoords(eye, r);
        return new float[]{r.exactCenterX(), r.exactCenterY(), r.width()};
    }

    private int dp(float v) {
        return Math.round(v * density);
    }

    private TextView text(float sp, int color, int font) {
        TextView t = new TextView(ctx);
        t.setTextColor(color);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTypeface(UiFont.get(ctx, font));
        t.setIncludeFontPadding(false);
        return t;
    }
}
