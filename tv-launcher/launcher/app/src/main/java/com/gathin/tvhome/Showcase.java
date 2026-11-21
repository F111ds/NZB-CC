package com.gathin.tvhome;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.LayerDrawable;
import android.os.Handler;
import android.text.TextUtils;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextClock;
import android.widget.TextView;

import java.io.File;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Supplier;

/**
 * NZB-CC idle showcase: after a few minutes on Home with no buttons pressed, the posters and buttons fade away and
 * the art of what's in Recently watched fills the screen at full brightness, one item about every 20 seconds (a
 * slow push in, a cross-fade to the next), with its title, how much is left, and a large clock with the date and
 * the weather. Any button brings Home back (and does nothing else).
 */
final class Showcase {
    private static final long TURN_MS = 20_000L;

    private final Context ctx;
    private final float density;
    private final Handler main;
    private final FrameLayout view;
    private final ImageView[] art = new ImageView[2];
    private final TextView kicker, title, meta, when, hint;
    private final LinearLayout dots;
    private final Supplier<String> weather;
    private final boolean motion;
    private List<KodiRow.Item> items = new ArrayList<>();
    private int index, front;
    private ValueAnimator push;
    private final Runnable next = this::advance;

    Showcase(Context ctx, FrameLayout root, Handler main, Supplier<String> weather, boolean motion) {
        this.ctx = ctx;
        this.main = main;
        this.weather = weather;
        this.motion = motion;
        density = ctx.getResources().getDisplayMetrics().density;
        view = new FrameLayout(ctx);
        view.setBackgroundColor(Color.BLACK);
        view.setVisibility(View.GONE);
        view.setAlpha(0f);
        view.setClickable(true);
        view.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);
        for (int i = 0; i < 2; i++) {
            art[i] = new ImageView(ctx);
            art[i].setScaleType(ImageView.ScaleType.CENTER_CROP);
            art[i].setAlpha(0f);
            view.addView(art[i], new FrameLayout.LayoutParams(-1, -1));
        }
        View shade = new View(ctx);
        shade.setBackground(new LayerDrawable(new Drawable[]{
                new GradientDrawable(GradientDrawable.Orientation.BOTTOM_TOP, new int[]{0xD9000000, 0x00000000, 0x00000000}),
                new GradientDrawable(GradientDrawable.Orientation.LEFT_RIGHT, new int[]{0x99000000, 0x00000000, 0x00000000}),
                new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM, new int[]{0x66000000, 0x00000000, 0x00000000})}));
        view.addView(shade, new FrameLayout.LayoutParams(-1, -1));

        LinearLayout info = new LinearLayout(ctx);
        info.setOrientation(LinearLayout.VERTICAL);
        kicker = text(9, 0xFFFF8A66, R.string.font_medium);
        kicker.setLetterSpacing(0.3f);
        kicker.setAllCaps(true);
        info.addView(kicker);
        title = new TextView(ctx);
        title.setTextColor(Color.WHITE);
        title.setTypeface(fraunces());
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 60);
        title.setIncludeFontPadding(false);
        title.setLetterSpacing(-0.02f);
        title.setMaxLines(2);
        title.setEllipsize(TextUtils.TruncateAt.END);
        LinearLayout.LayoutParams tl = new LinearLayout.LayoutParams(-2, -2);
        tl.topMargin = dp(8);
        info.addView(title, tl);
        meta = text(13, 0xCCFFEBE1, R.string.font_regular);
        LinearLayout.LayoutParams ml = new LinearLayout.LayoutParams(-2, -2);
        ml.topMargin = dp(12);
        info.addView(meta, ml);
        dots = new LinearLayout(ctx);
        dots.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout.LayoutParams dl = new LinearLayout.LayoutParams(-2, dp(5));
        dl.topMargin = dp(18);
        info.addView(dots, dl);
        FrameLayout.LayoutParams il = new FrameLayout.LayoutParams(dp(620), -2, Gravity.BOTTOM | Gravity.START);
        il.leftMargin = dp(48);
        il.bottomMargin = dp(52);
        view.addView(info, il);

        LinearLayout clock = new LinearLayout(ctx);
        clock.setOrientation(LinearLayout.VERTICAL);
        clock.setGravity(Gravity.END);
        TextClock time = new TextClock(ctx);
        time.setFormat12Hour("h:mm");
        time.setFormat24Hour("HH:mm");
        time.setTextColor(Color.WHITE);
        time.setTextSize(TypedValue.COMPLEX_UNIT_SP, 48);
        time.setTypeface(UiFont.get(ctx, R.string.font_light));
        time.setLetterSpacing(-0.03f);
        time.setIncludeFontPadding(false);
        clock.addView(time);
        when = text(12, 0xB3FFDCCD, R.string.font_regular);
        when.setLetterSpacing(0.2f);
        when.setAllCaps(true);
        LinearLayout.LayoutParams wl = new LinearLayout.LayoutParams(-2, -2);
        wl.topMargin = dp(6);
        clock.addView(when, wl);
        FrameLayout.LayoutParams cl = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.END);
        cl.topMargin = dp(36);
        cl.rightMargin = dp(48);
        view.addView(clock, cl);

        hint = text(11, 0x8CFFFFFF, R.string.font_regular);
        hint.setText("Press any button");
        FrameLayout.LayoutParams hl = new FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM | Gravity.END);
        hl.rightMargin = dp(48);
        hl.bottomMargin = dp(40);
        view.addView(hint, hl);

        root.addView(view, new FrameLayout.LayoutParams(-1, -1));
    }

    boolean showing() {
        return view.getVisibility() == View.VISIBLE;
    }

    /** Fades in over Home with these items (nothing happens without any). */
    void show(List<KodiRow.Item> list) {
        if (list == null || list.isEmpty() || showing()) return;
        items = new ArrayList<>(list.subList(0, Math.min(5, list.size())));
        index = 0;
        dots.removeAllViews();
        for (int i = 0; i < items.size(); i++) {
            View d = new View(ctx);
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(dp(5), dp(5));
            lp.rightMargin = dp(5);
            dots.addView(d, lp);
        }
        view.setVisibility(View.VISIBLE);
        view.bringToFront();
        view.animate().alpha(1f).setDuration(900).start();
        fill(true);
        main.postDelayed(next, TURN_MS);
    }

    void hide() {
        main.removeCallbacks(next);
        if (push != null) push.cancel();
        if (!showing()) return;
        view.animate().alpha(0f).setDuration(300).withEndAction(() -> {
            view.setVisibility(View.GONE);
            for (ImageView a : art) a.setImageDrawable(null);   // the big bitmaps go
        }).start();
    }

    private void advance() {
        if (!showing() || items.isEmpty()) return;
        index = (index + 1) % items.size();
        fill(false);
        main.postDelayed(next, TURN_MS);
    }

    private void fill(boolean first) {
        KodiRow.Item it = items.get(index);
        kicker.setText(it.partway() && it.minutesLeft() > 0
                ? "Continue watching · " + KodiRow.Item.duration(it.minutesLeft()) + " left"
                : it.progress >= 0.97f ? "Watch again" : "Recently watched");
        title.setText(it.title);
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, it.title.length() > 22 ? 44 : 60);
        meta.setText(EmberHome.metaLine(it));
        String w = weather == null ? null : weather.get();
        java.text.SimpleDateFormat f = new java.text.SimpleDateFormat("EEEE · MMM d", java.util.Locale.getDefault());
        when.setText(f.format(new java.util.Date()) + (w == null || w.isEmpty() ? "" : " · " + w));
        for (int i = 0; i < dots.getChildCount(); i++) {
            View d = dots.getChildAt(i);
            boolean on = i == index;
            GradientDrawable g = new GradientDrawable();
            g.setColor(on ? EmberHome.ACCENT : 0x4DFFFFFF);
            g.setCornerRadius(dp(3));
            d.setBackground(g);
            LinearLayout.LayoutParams lp = (LinearLayout.LayoutParams) d.getLayoutParams();
            lp.width = dp(on ? 16 : 5);
            d.setLayoutParams(lp);
        }
        int back = first ? front : 1 - front;
        ImageView a = art[back];
        a.setImageBitmap(load(it));
        a.setScaleX(1f);
        a.setScaleY(1f);
        a.setAlpha(0f);
        a.animate().alpha(1f).setDuration(first ? 900 : 1400).start();
        if (!first) art[front].animate().alpha(0f).setDuration(1400).start();
        front = back;
        if (push != null) push.cancel();
        if (motion) {
            push = ValueAnimator.ofFloat(1f, 1.08f);
            push.setDuration(TURN_MS + 1500);
            push.addUpdateListener(v -> {
                float s = (float) v.getAnimatedValue();
                a.setScaleX(s);
                a.setScaleY(s);
            });
            push.start();
        }
    }

    private Bitmap load(KodiRow.Item it) {
        File f = it.backdrop != null && it.backdrop.isFile() ? it.backdrop : it.poster;
        if (f == null || !f.isFile()) return null;
        BitmapFactory.Options o = new BitmapFactory.Options();
        o.inJustDecodeBounds = true;
        BitmapFactory.decodeFile(f.getPath(), o);
        int want = ctx.getResources().getDisplayMetrics().widthPixels;
        int sample = 1;
        while (o.outWidth / (sample * 2) >= want) sample *= 2;
        o = new BitmapFactory.Options();
        o.inSampleSize = sample;
        try {
            return BitmapFactory.decodeFile(f.getPath(), o);
        } catch (OutOfMemoryError e) {
            return null;
        }
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
        t.setSingleLine(true);
        return t;
    }

    private Typeface fraunces() {
        try {
            Typeface t = ctx.getResources().getFont(R.font.fraunces_light);
            if (t != null) return t;
        } catch (Exception ignored) {
            // the system serif
        }
        return Typeface.create("serif", Typeface.NORMAL);
    }
}
