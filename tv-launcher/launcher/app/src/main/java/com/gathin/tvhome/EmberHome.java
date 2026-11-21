package com.gathin.tvhome;

import android.animation.ValueAnimator;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.LayerDrawable;
import android.graphics.drawable.StateListDrawable;
import android.graphics.drawable.TransitionDrawable;
import android.os.Handler;
import android.text.TextUtils;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.animation.LinearInterpolator;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * NZB-CC "Cinema" home (design A, in HAL's red Ember style): the Kodi item you're on fills the top of the screen,
 * its background art on the right, behind its title, year, genres, story, time left and Resume / Start over; the
 * posters and the apps share one row along the bottom (laid out by MainActivity.layoutKodiRow), and a small HAL
 * pill in the top bar says whether all is well on the PC (OK opens HAL).
 *
 * <p>Only shown while the Kodi row has items; without them the home screen is laid out as before.
 */
final class EmberHome {
    static final int ACCENT = 0xFFFF5A1F;
    private static final int KICKER = 0xFFFF8A66;
    private static final long HAL_EVERY_MS = 2 * 60 * 1000L;

    interface Host {
        /** Up from the buttons. */
        void onEmberUp();
        /** Down from the buttons. */
        void onEmberDown();
    }

    private final Context ctx;
    private final float density;
    private final FrameLayout layer;
    private final ImageView backdrop;
    /** Holds the art at its place and clips it, so its slow drift never shows past the fade. */
    private final FrameLayout artBox;
    private final View scrim, glow;
    private final LinearLayout info;
    private final TextView kicker, title, meta, plot, left;
    private final View progressFill;
    private final FrameLayout progressTrack;
    final TextView resume, restart;
    private final KodiRow row;
    private final Host host;
    private final Handler main;
    private KodiRow.Item item;
    private String shownArt = "";
    private ValueAnimator drift;

    // HAL's pill in the top bar
    private final HomeExtras extras;
    private TextView halPill;
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private long halReadAt;
    private boolean halBusy;

    EmberHome(Context ctx, FrameLayout root, int insertAt, KodiRow row, HomeExtras extras, Handler main, Host host) {
        this.ctx = ctx;
        this.row = row;
        this.host = host;
        this.extras = extras;
        this.main = main;
        density = ctx.getResources().getDisplayMetrics().density;

        layer = new FrameLayout(ctx);
        layer.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        layer.setClipChildren(false);
        layer.setVisibility(View.GONE);
        // Its own black, so the art's fade meets the same black on every side (the sky backdrop stays behind it).
        layer.setBackgroundColor(0xFF050203);

        glow = new View(ctx);
        GradientDrawable g = new GradientDrawable();
        g.setGradientType(GradientDrawable.RADIAL_GRADIENT);
        g.setColors(new int[]{0x38FF3C14, 0x00FF3C14});
        g.setGradientRadius(dp(420));
        g.setGradientCenter(0.2f, 1.0f);
        glow.setBackground(g);

        artBox = new FrameLayout(ctx);
        artBox.setClipChildren(true);
        backdrop = new ImageView(ctx);
        backdrop.setScaleType(ImageView.ScaleType.CENTER_CROP);
        backdrop.setAlpha(0f);
        artBox.addView(backdrop, new FrameLayout.LayoutParams(-1, -1));
        layer.addView(artBox, new FrameLayout.LayoutParams(0, 0, Gravity.TOP | Gravity.END));

        // Fades the art into the black: from the left (behind the text), from the foot and a little from the top.
        scrim = new View(ctx);
        GradientDrawable fromLeft = new GradientDrawable(GradientDrawable.Orientation.LEFT_RIGHT,
                new int[]{0xFF000000, 0xD9000000, 0x00000000, 0x00000000});
        GradientDrawable fromFoot = new GradientDrawable(GradientDrawable.Orientation.BOTTOM_TOP,
                new int[]{0xFF000000, 0x00000000, 0x00000000});
        GradientDrawable fromTop = new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,
                new int[]{0xB3000000, 0x00000000, 0x00000000, 0x00000000});
        scrim.setBackground(new LayerDrawable(new Drawable[]{fromLeft, fromFoot, fromTop}));
        layer.addView(scrim, new FrameLayout.LayoutParams(0, 0, Gravity.TOP | Gravity.END));
        layer.addView(glow, new FrameLayout.LayoutParams(-1, -1));   // HAL's red glow over the art, from the bottom left

        info = new LinearLayout(ctx);
        info.setOrientation(LinearLayout.VERTICAL);
        info.setClipChildren(false);
        info.setClipToPadding(false);

        kicker = text(9, KICKER, R.string.font_medium);
        kicker.setLetterSpacing(0.28f);
        kicker.setAllCaps(true);
        info.addView(kicker);

        title = new TextView(ctx);
        title.setTextColor(Color.WHITE);
        title.setTypeface(fraunces());
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 62);
        title.setIncludeFontPadding(false);
        title.setLetterSpacing(-0.02f);
        title.setMaxLines(2);
        title.setEllipsize(TextUtils.TruncateAt.END);
        title.setLineSpacing(0, 0.92f);
        LinearLayout.LayoutParams tlp = new LinearLayout.LayoutParams(-1, -2);
        tlp.topMargin = dp(9);
        info.addView(title, tlp);

        meta = text(13, 0xCCFFE6DC, R.string.font_regular);
        meta.setSingleLine(true);
        meta.setEllipsize(TextUtils.TruncateAt.END);
        LinearLayout.LayoutParams mlp = new LinearLayout.LayoutParams(-2, -2);
        mlp.topMargin = dp(12);
        info.addView(meta, mlp);

        plot = text(12, 0xB3FFE6DC, R.string.font_light);
        plot.setMaxLines(2);
        plot.setEllipsize(TextUtils.TruncateAt.END);
        plot.setLineSpacing(0, 1.25f);
        LinearLayout.LayoutParams plp = new LinearLayout.LayoutParams(dp(330), -2);
        plp.topMargin = dp(10);
        info.addView(plot, plp);

        LinearLayout prog = new LinearLayout(ctx);
        prog.setOrientation(LinearLayout.HORIZONTAL);
        prog.setGravity(Gravity.CENTER_VERTICAL);
        progressTrack = new FrameLayout(ctx);
        GradientDrawable tr = new GradientDrawable();
        tr.setColor(0x2EFFFFFF);
        tr.setCornerRadius(dp(2));
        progressTrack.setBackground(tr);
        progressFill = new View(ctx);
        GradientDrawable fl = new GradientDrawable(GradientDrawable.Orientation.LEFT_RIGHT, new int[]{0xFFD4140A, ACCENT});
        fl.setCornerRadius(dp(2));
        progressFill.setBackground(fl);
        progressTrack.addView(progressFill, new FrameLayout.LayoutParams(0, -1));
        prog.addView(progressTrack, new LinearLayout.LayoutParams(dp(150), dp(3)));
        left = text(10, 0xFFFFC6B0, R.string.font_medium);
        left.setLetterSpacing(0.04f);
        LinearLayout.LayoutParams llp = new LinearLayout.LayoutParams(-2, -2);
        llp.leftMargin = dp(10);
        prog.addView(left, llp);
        LinearLayout.LayoutParams prlp = new LinearLayout.LayoutParams(-2, -2);
        prlp.topMargin = dp(14);
        info.addView(prog, prlp);

        LinearLayout btns = new LinearLayout(ctx);
        btns.setOrientation(LinearLayout.HORIZONTAL);
        btns.setClipChildren(false);
        btns.setClipToPadding(false);
        resume = button("▶  Resume", true);
        restart = button("Start over", false);
        btns.addView(resume, new LinearLayout.LayoutParams(-2, dp(36)));
        LinearLayout.LayoutParams rlp = new LinearLayout.LayoutParams(-2, dp(36));
        rlp.leftMargin = dp(9);
        btns.addView(restart, rlp);
        LinearLayout.LayoutParams blp = new LinearLayout.LayoutParams(-2, -2);
        blp.topMargin = dp(16);
        info.addView(btns, blp);

        FrameLayout.LayoutParams ilp = new FrameLayout.LayoutParams(dp(420), -2, Gravity.TOP | Gravity.START);
        ilp.leftMargin = dp(48);
        layer.addView(info, ilp);

        root.addView(layer, insertAt, new FrameLayout.LayoutParams(-1, -1));

        resume.setOnClickListener(v -> {
            if (item != null) row.play(item, false);
        });
        restart.setOnClickListener(v -> {
            if (item != null) row.play(item, true);
        });
        View.OnKeyListener keys = (v, code, e) -> {
            if (e.getAction() != KeyEvent.ACTION_DOWN) return false;
            if (code == KeyEvent.KEYCODE_DPAD_UP) {
                host.onEmberUp();
                return true;
            }
            if (code == KeyEvent.KEYCODE_DPAD_DOWN) {
                host.onEmberDown();
                return true;
            }
            if (code == KeyEvent.KEYCODE_DPAD_LEFT) {
                if (v == restart) resume.requestFocus();
                return true;
            }
            if (code == KeyEvent.KEYCODE_DPAD_RIGHT) {
                if (v == resume && restart.getVisibility() == View.VISIBLE) restart.requestFocus();
                return true;
            }
            return false;
        };
        resume.setOnKeyListener(keys);
        restart.setOnKeyListener(keys);
    }

    // ---- what's shown -------------------------------------------------------------------------

    boolean shown() {
        return layer.getVisibility() == View.VISIBLE;
    }

    void setShown(boolean on) {
        layer.setVisibility(on ? View.VISIBLE : View.GONE);
        if (!on && drift != null) drift.cancel();
        if (on && drift != null && !drift.isStarted()) drift.start();
    }

    /** The item the big picture is about (the poster with focus, or the one watched last). */
    void show(KodiRow.Item it) {
        if (it == null) return;
        boolean same = item != null && item.id.equals(it.id);
        item = it;
        kicker.setText(it.partway() ? "Continue watching · Kodi" : it.progress >= 0.97f ? "Watch again · Kodi" : "Kodi");
        title.setText(it.title);
        int len = it.title.length();
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, len > 28 ? 36 : len > 18 ? 46 : 62);
        meta.setText(metaLine(it));
        plot.setText(it.plot);
        plot.setVisibility(it.plot.isEmpty() ? View.GONE : View.VISIBLE);
        boolean partway = it.partway();
        int mins = it.minutesLeft();
        ((View) progressTrack.getParent()).setVisibility(partway ? View.VISIBLE : View.GONE);
        progressFill.getLayoutParams().width = Math.round(dp(150) * Math.min(1f, it.progress));
        progressFill.requestLayout();
        left.setText(mins > 0 ? KodiRow.Item.duration(mins) + " left" : "");
        resume.setText(partway ? "▶  Resume" : "▶  Play");
        restart.setVisibility(partway ? View.VISIBLE : View.GONE);
        if (!same) setArt(it);
    }

    static String metaLine(KodiRow.Item it) {
        StringBuilder b = new StringBuilder();
        if (it.isEpisode()) {
            if (it.season > 0 || it.episode > 0) b.append(String.format(java.util.Locale.US, "S%d E%d", it.season, it.episode));
            if (!it.episodeTitle.isEmpty()) b.append(b.length() > 0 ? "  ·  " : "").append(it.episodeTitle);
        } else if (it.year > 0) {
            b.append(it.year);
        }
        if (!it.genres.isEmpty()) b.append(b.length() > 0 ? "  ·  " : "").append(it.genres);
        if (it.runtime > 0) b.append(b.length() > 0 ? "  ·  " : "").append(KodiRow.Item.duration(it.runtime));
        if (!it.quality.isEmpty()) b.append(b.length() > 0 ? "  ·  " : "").append(it.quality);
        return b.toString();
    }

    /** The background art (the poster, softened, when Kodi has none), cross-faded from the last one. */
    private void setArt(KodiRow.Item it) {
        boolean wide = it.backdrop != null && it.backdrop.isFile();
        File f = wide ? it.backdrop : it.poster;
        String key = f == null ? "" : f.getPath();
        if (key.equals(shownArt)) return;
        shownArt = key;
        Bitmap bm = null;
        if (f != null && f.isFile()) {
            BitmapFactory.Options o = new BitmapFactory.Options();
            o.inJustDecodeBounds = true;
            BitmapFactory.decodeFile(f.getPath(), o);
            // Wide art at about the size it shows; a poster shrunk to a few dozen pixels, which the stretch blurs.
            int want = wide ? Math.round(ctx.getResources().getDisplayMetrics().widthPixels * 0.68f) : dp(24);
            int sample = 1;
            while (o.outWidth / (sample * 2) >= want) sample *= 2;
            o = new BitmapFactory.Options();
            o.inSampleSize = sample;
            bm = BitmapFactory.decodeFile(f.getPath(), o);
        }
        backdrop.setScaleType(wide ? ImageView.ScaleType.CENTER_CROP : ImageView.ScaleType.FIT_XY);
        Drawable next = bm == null ? new ColorDrawable(Color.TRANSPARENT)
                : new android.graphics.drawable.BitmapDrawable(ctx.getResources(), bm);
        Drawable prev = backdrop.getDrawable();
        if (prev == null || backdrop.getAlpha() == 0f) {
            backdrop.setImageDrawable(next);
            backdrop.animate().alpha(wide ? 1f : 0.75f).setDuration(500).start();
        } else {
            if (prev instanceof TransitionDrawable) prev = ((TransitionDrawable) prev).getDrawable(1);
            TransitionDrawable t = new TransitionDrawable(new Drawable[]{prev, next});
            t.setCrossFadeEnabled(true);
            backdrop.setImageDrawable(t);
            t.startTransition(450);
            backdrop.animate().alpha(wide ? 1f : 0.75f).setDuration(450).start();
        }
    }

    /** Places the art and the text: the text's foot sits at bottomY (just above the bottom row's label). */
    void layout(int rootW, int rootH, int topY, int bottomY, boolean motion) {
        int artW = Math.round(rootW * 0.68f), artH = Math.round(rootH * 0.76f);
        for (View v : new View[]{artBox, scrim}) {
            FrameLayout.LayoutParams lp = (FrameLayout.LayoutParams) v.getLayoutParams();
            if (lp.width != artW || lp.height != artH) {
                lp.width = artW;
                lp.height = artH;
                v.setLayoutParams(lp);
            }
        }
        info.measure(View.MeasureSpec.makeMeasureSpec(dp(420), View.MeasureSpec.EXACTLY),
                View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED));
        // From the top, so the title stays put from one poster to the next; pulled up only if it would reach the row.
        int y = Math.max(0, Math.min(topY, bottomY - info.getMeasuredHeight()));
        info.setY(y);
        // Mild motion: the art drifts slowly (a slow push in and back), only where animations are on.
        if (motion && drift == null) {
            drift = ValueAnimator.ofFloat(0f, 1f);
            drift.setDuration(26_000L);
            drift.setRepeatMode(ValueAnimator.REVERSE);
            drift.setRepeatCount(ValueAnimator.INFINITE);
            drift.setInterpolator(new android.view.animation.AccelerateDecelerateInterpolator());
            drift.addUpdateListener(a -> {
                float f = (float) a.getAnimatedValue();
                backdrop.setScaleX(1f + 0.05f * f);
                backdrop.setScaleY(1f + 0.05f * f);
                backdrop.setTranslationX(-dp(14) * f);
            });
            backdrop.setPivotX(artW * 0.6f);
            backdrop.setPivotY(artH * 0.4f);
            if (shown()) drift.start();
        }
    }

    void pause() {
        if (drift != null) drift.pause();
    }

    void resumeMotion() {
        if (drift != null && shown()) {
            if (drift.isPaused()) drift.resume();
            else if (!drift.isStarted()) drift.start();
        }
    }

    boolean focusButtons() {
        if (!shown()) return false;
        return resume.requestFocus();
    }

    // ---- HAL's pill ---------------------------------------------------------------------------

    /** Adds the HAL pill to the top bar (before the first button); OK opens HAL. */
    void addHalPill(ViewGroup bar, int styleRes) {
        if (extras.kodiServer.isEmpty() || halPill != null) return;
        halPill = new TextView(ctx, null, 0, styleRes);
        halPill.setId(View.generateViewId());
        halPill.setSingleLine(true);
        halPill.setFocusable(true);
        halPill.setText("HAL");
        GradientDrawable dot = new GradientDrawable(GradientDrawable.Orientation.TL_BR, new int[]{0xFFFFD27A, 0xFFFF2A12, 0xFF5A0402});
        dot.setGradientType(GradientDrawable.RADIAL_GRADIENT);
        dot.setGradientRadius(dp(7));
        dot.setShape(GradientDrawable.OVAL);
        dot.setStroke(dp(1.5f), 0xFF8A9098);
        dot.setSize(dp(14), dp(14));
        halPill.setCompoundDrawablesRelativeWithIntrinsicBounds(dot, null, null, null);
        halPill.setCompoundDrawableTintList(null);
        halPill.setContentDescription("HAL 9000");
        halPill.setOnClickListener(v -> {
            Intent i = new Intent(ctx, HalActivity.class);
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            ctx.startActivity(i);
        });
        ViewGroup.MarginLayoutParams lp = new LinearLayout.LayoutParams(-2, dp(34));
        lp.setMarginStart(dp(8));
        bar.addView(halPill, 0, lp);
        refreshHal();
    }

    View halPill() {
        return halPill;
    }

    void refreshHal() {
        if (halPill == null || halBusy || System.currentTimeMillis() - halReadAt < HAL_EVERY_MS) return;
        halBusy = true;
        try {
            io.execute(() -> {
                String level = null;
                int issues = 0;
                try {
                    JSONObject j = new JSONObject(new String(get("/hal/api/status", 8000), StandardCharsets.UTF_8));
                    JSONObject hal = j.optJSONObject("hal");
                    level = hal == null ? "ok" : hal.optString("level", "ok");
                    JSONArray is = j.optJSONArray("issues");
                    for (int i = 0; is != null && i < is.length(); i++) {
                        String l = is.getJSONObject(i).optString("level", "");
                        if (l.equals("warning") || l.equals("critical")) issues++;
                    }
                } catch (Exception ignored) {
                    level = null;   // the PC is off or asleep
                }
                final String lv = level;
                final int n = issues;
                main.post(() -> {
                    halBusy = false;
                    halReadAt = System.currentTimeMillis();
                    setHal(lv, n);
                });
            });
        } catch (java.util.concurrent.RejectedExecutionException e) {
            halBusy = false;
        }
    }

    private void setHal(String level, int issues) {
        if (halPill == null) return;
        android.text.SpannableStringBuilder s = new android.text.SpannableStringBuilder("HAL  ·  ");
        String word;
        int color;
        if (level == null) {
            word = "PC offline";
            color = 0x99FFFFFF;
        } else if (level.equals("critical")) {
            word = issues > 1 ? issues + " problems" : "problem";
            color = 0xFFFF5A4A;
        } else if (level.equals("warning")) {
            word = issues > 1 ? issues + " warnings" : "warning";
            color = 0xFFFBBF24;
        } else {
            word = "all normal";
            color = 0xFF7EF0B8;
        }
        int at = s.length();
        s.append(word);
        s.setSpan(new android.text.style.ForegroundColorSpan(color), at, s.length(), android.text.Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        halPill.setText(s);
    }

    private byte[] get(String path, int timeoutMs) throws java.io.IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(extras.kodiServer + path).openConnection();
        c.setConnectTimeout(Math.min(timeoutMs, 4000));
        c.setReadTimeout(timeoutMs);
        c.setRequestProperty("X-Mobile-Token", extras.kodiToken);
        try {
            if (c.getResponseCode() / 100 != 2) throw new java.io.IOException("HTTP " + c.getResponseCode());
            try (InputStream in = c.getInputStream()) {
                return HomeExtras.readAll(in);
            }
        } finally {
            c.disconnect();
        }
    }

    void shutdown() {
        io.shutdownNow();
        if (drift != null) drift.cancel();
    }

    // ---- building blocks ----------------------------------------------------------------------

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

    private Typeface fraunces() {
        try {
            Typeface t = ctx.getResources().getFont(R.font.fraunces_light);
            if (t != null) return t;
        } catch (Exception ignored) {
            // fall back to the system serif
        }
        return Typeface.create("serif", Typeface.NORMAL);
    }

    /** A pill button: Resume is HAL red and glows; focus lights it with a warm ring and grows it a little. */
    private TextView button(String label, boolean primary) {
        TextView b = text(13, Color.WHITE, R.string.font_medium);
        b.setText(label);
        b.setGravity(Gravity.CENTER);
        b.setPadding(dp(18), 0, dp(20), 0);
        b.setFocusable(true);
        b.setFocusableInTouchMode(false);
        b.setTypeface(UiFont.get(ctx, R.string.font_medium), Typeface.BOLD);
        StateListDrawable s = new StateListDrawable();
        s.addState(new int[]{android.R.attr.state_focused}, pill(primary ? new int[]{0xFFFF7A48, 0xFFE0200F} : new int[]{0x59FFFFFF, 0x40FFFFFF},
                0xFFFFD2BC, dp(2)));
        s.addState(new int[]{}, pill(primary ? new int[]{0xFFE8502A, 0xFFB8120A} : new int[]{0x26FFFFFF, 0x1AFFFFFF},
                primary ? 0x66FFB08A : 0x33FFFFFF, dp(1)));
        b.setBackground(s);
        if (primary) {
            b.setElevation(dp(8));
            if (android.os.Build.VERSION.SDK_INT >= 28) {
                b.setOutlineSpotShadowColor(0xFFFF3C14);
                b.setOutlineAmbientShadowColor(0xFFFF3C14);
            }
        }
        b.setOnFocusChangeListener((v, has) -> v.animate().scaleX(has ? 1.06f : 1f).scaleY(has ? 1.06f : 1f)
                .setDuration(140).setInterpolator(new LinearInterpolator()).start());
        return b;
    }

    private GradientDrawable pill(int[] colors, int stroke, int strokeW) {
        GradientDrawable d = new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM, colors);
        d.setCornerRadius(dp(18));
        d.setStroke(strokeW, stroke);
        return d;
    }
}
