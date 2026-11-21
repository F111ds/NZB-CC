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
    private final TextView line, hint, status;
    private final int eyePx, ringPx;
    private ValueAnimator spin;
    private float spinDeg;

    BigHal(Context ctx, FrameLayout root, int insertAt, Host host) {
        this.ctx = ctx;
        density = ctx.getResources().getDisplayMetrics().density;
        eyePx = dp(98);
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
        ring.setVisibility(View.GONE);   // the polished home: just the chrome eye
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
        el.topMargin = 0;
        view.addView(eye, el);

        words = new LinearLayout(ctx);
        words.setOrientation(LinearLayout.VERTICAL);
        TextView kicker = text(8, 0xFFFF8A66, R.string.font_medium);
        kicker.setText("HAL 9000");
        kicker.setLetterSpacing(0.3f);
        words.addView(kicker);
        line = text(14, 0xD9FFEBE4, R.string.font_regular);
        line.setMaxLines(2);
        line.setEllipsize(TextUtils.TruncateAt.END);
        line.setMaxWidth(dp(380));
        LinearLayout.LayoutParams ll = new LinearLayout.LayoutParams(-2, -2);
        ll.topMargin = dp(6);
        words.addView(line, ll);
        hint = text(9.5f, 0x99FFE1D4, R.string.font_regular);
        hint.setText("OK  ›  PC, network, downloads and more");
        hint.setLetterSpacing(0.06f);

        LinearLayout.LayoutParams hl = new LinearLayout.LayoutParams(-2, -2);
        hl.topMargin = dp(6);
        hint.setVisibility(View.GONE);   // not shown: OK on his eye still opens his screens
        words.addView(hint, hl);
        // the status line: a coloured dot and all well, or what needs looking at
        status = text(10, 0xFF9FF5C4, R.string.font_medium);
        status.setSingleLine(true);
        status.setEllipsize(TextUtils.TruncateAt.END);
        status.setMaxWidth(dp(400));
        status.setCompoundDrawablePadding(dp(7));
        LinearLayout.LayoutParams sl = new LinearLayout.LayoutParams(-2, -2);
        sl.topMargin = dp(9);
        words.addView(status, sl);
        setStatus(new String[]{"checking", "", "", "0"});   // until HAL's first reading
        view.addView(words, new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.START));

        eye.setOnFocusChangeListener((v, has) -> {
            HalEyeDrawable.focused = has;
            eye.animate().scaleX(has ? 1.08f : 1f).scaleY(has ? 1.08f : 1f).setDuration(200).start();
            hint.setTextColor(has ? 0xE6FFE1D4 : 0x99FFE1D4);
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
        int box = Math.round(eyePx / 0.76f);
        int left = rootW / 2 + eyePx / 2 + dp(24);
        words.measure(View.MeasureSpec.UNSPECIFIED, View.MeasureSpec.UNSPECIFIED);
        int top = box / 2 - words.getMeasuredHeight() / 2;
        if (wl.leftMargin != left || wl.topMargin != top) {
            wl.leftMargin = left;
            wl.topMargin = top;
            words.setLayoutParams(wl);
        }
    }

    int height() {
        return Math.round(eyePx / 0.76f);
    }

    /** The eye as it shows (its rim), without the room for its glow. */
    int visibleHeight() {
        return eyePx;
    }

    int glowMargin() {
        return (height() - eyePx) / 2;
    }

    void setShown(boolean on) {
        view.setVisibility(on ? View.VISIBLE : View.GONE);
    }

    boolean shown() {
        return view.getVisibility() == View.VISIBLE;
    }

    /** HAL's latest line (greeting and quote from the PC); "" when the PC doesn't answer. */
    void setLine(String text) {
        if (text == null || text.isEmpty()) {
            line.setText("I can't reach the PC right now.");
            return;
        }
        // the greeting in bold white, then the rest
        int cut = text.indexOf('.');
        android.text.SpannableString s = new android.text.SpannableString(text);
        if (cut > 0 && cut < text.length() - 1) {
            s.setSpan(new android.text.style.StyleSpan(Typeface.BOLD), 0, cut + 1, 0);
            s.setSpan(new android.text.style.ForegroundColorSpan(Color.WHITE), 0, cut + 1, 0);
        }
        line.setText(s);
    }

    /** {level, title, detail, count} from EmberHome.halStatus: green all well, amber something to check, red a problem. */
    void setStatus(String[] s) {
        String level = s[0];
        int n = 0;
        try {
            n = Integer.parseInt(s[3]);
        } catch (NumberFormatException ignored) {
            n = 0;
        }
        int dot, col;
        String head, tail = "";
        if ("checking".equals(level)) {
            dot = 0xFF8A8F98;
            col = 0x99FFFFFF;
            head = "Checking the house\u2026";
        } else if (level.isEmpty()) {
            dot = 0xFF8A8F98;
            col = 0xB3FFFFFF;
            head = "Can't reach the PC";
        } else if ("ok".equals(level) || n == 0) {
            dot = 0xFF5EEA9B;
            col = 0xFF9FF5C4;
            head = "All systems functioning normally";
        } else {
            boolean crit = "critical".equals(level);
            dot = crit ? 0xFFFF5A4A : 0xFFFBBF24;
            col = crit ? 0xFFFF8A7A : 0xFFFFD27A;
            head = s[1];
            tail = n > 1 ? "  \u00B7  +" + (n - 1) + " more" : (s[2].isEmpty() ? "" : "  \u00B7  " + s[2]);
        }
        android.text.SpannableStringBuilder b = new android.text.SpannableStringBuilder(head);
        b.setSpan(new android.text.style.ForegroundColorSpan(col), 0, head.length(), 0);
        if (!tail.isEmpty()) {
            int at = b.length();
            b.append(tail);
            b.setSpan(new android.text.style.ForegroundColorSpan(0xB3FFEBE4), at, b.length(), 0);
            b.setSpan(new android.text.style.StyleSpan(Typeface.NORMAL), at, b.length(), 0);
        }
        status.setText(b);
        android.graphics.drawable.GradientDrawable d = new android.graphics.drawable.GradientDrawable();
        d.setShape(android.graphics.drawable.GradientDrawable.OVAL);
        d.setColor(dot);
        d.setSize(dp(7), dp(7));
        status.setCompoundDrawablesRelativeWithIntrinsicBounds(d, null, null, null);
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

    /**
     * HAL's eye is redrawn about 25 times a second while Home is on screen, from here: its own redraw loop depends on
     * the TV telling the drawable it is visible, and on animations being on, neither of which every TV does.
     */
    private boolean ticking;
    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            if (!ticking) return;
            eye.invalidate();
            eye.postDelayed(this, 40);
        }
    };

    void live(boolean on) {
        if (on == ticking) return;
        ticking = on;
        eye.removeCallbacks(tick);
        if (on) eye.post(tick);
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
