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
    private boolean cinemaLayout = true;
    /**
     * Centred home: the art shows richly, shaded only behind the top line and on the floor, where the posters stand
     * on a soft horizon line with a faint sheen below it (setFloor gives its height).
     */
    private final FloorDrawable floor = new FloorDrawable();

    private final class FloorDrawable extends Drawable {
        float floorY = -1;
        /** HAL's own backdrop (no movie art): deep red-black space, faint stars, his glow at halY. */
        boolean hal;
        float halY = -1;
        private final android.graphics.Paint base = new android.graphics.Paint(), halGlow = new android.graphics.Paint(),
                lowGlow = new android.graphics.Paint(), star = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
        private float[] stars;
        private float madeHalY = -2;
        private final android.graphics.Paint shade = new android.graphics.Paint(), sheen = new android.graphics.Paint(),
                line = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
        private int madeH = -1;
        private float madeFloor = -2;

        @Override
        public void draw(android.graphics.Canvas c) {
            android.graphics.Rect b = getBounds();
            int h = b.height(), w = b.width();
            if (h <= 0) return;
            if (madeH != h || madeFloor != floorY) {
                madeH = h;
                madeFloor = floorY;
                shade.setShader(new android.graphics.LinearGradient(0, 0, 0, h,
                        hal ? new int[]{0x59000000, 0x00000000, 0x00000000, 0x00000000, 0x99000000}
                                : new int[]{0xC7040208, 0x40040208, 0x26040208, 0x8C040208, 0xF0030105},
                        new float[]{0f, 0.25f, 0.45f, 0.66f, 1f}, android.graphics.Shader.TileMode.CLAMP));
                if (hal) {
                    base.setShader(new android.graphics.LinearGradient(0, 0, 0, h, new int[]{0xFF0B0306, 0xFF120408, 0xFF06020A},
                            new float[]{0f, 0.45f, 1f}, android.graphics.Shader.TileMode.CLAMP));
                    lowGlow.setShader(new android.graphics.RadialGradient(w / 2f, h * 1.2f, w * 0.75f,
                            0x73781440, 0x00781440, android.graphics.Shader.TileMode.CLAMP));
                    java.util.Random r = new java.util.Random(2001);
                    stars = new float[160 * 3];
                    for (int i = 0; i < 160; i++) {
                        stars[i * 3] = r.nextFloat() * w;
                        stars[i * 3 + 1] = r.nextFloat() * h * 0.66f;
                        stars[i * 3 + 2] = (0.3f + r.nextFloat() * 1.4f) * density / 2f;
                    }
                }
                if (floorY > 0) {
                    sheen.setShader(new android.graphics.LinearGradient(0, floorY, 0, floorY + h * 0.3f, 0x1AFFAA8C, 0x00FFAA8C,
                            android.graphics.Shader.TileMode.CLAMP));
                    line.setShader(new android.graphics.LinearGradient(w * 0.1f, 0, w * 0.9f, 0,
                            new int[]{0x00FFBEA0, 0x73FFBEA0, 0x00FFBEA0}, null, android.graphics.Shader.TileMode.CLAMP));
                }
            }
            if (hal) {
                if (madeHalY != halY) {
                    madeHalY = halY;
                    float gy = halY > 0 ? halY : h * 0.23f;
                    halGlow.setShader(new android.graphics.RadialGradient(w / 2f, gy, w * 0.4f,
                            0x4DFF280F, 0x00FF280F, android.graphics.Shader.TileMode.CLAMP));
                }
                c.drawRect(b, base);
                c.drawRect(b, lowGlow);
                for (int i = 0; stars != null && i < stars.length; i += 3) {
                    star.setColor(Color.argb(40 + (i * 37) % 110, 255, 215, 205));
                    c.drawCircle(stars[i], stars[i + 1], Math.max(0.6f, stars[i + 2]), star);
                }
                c.drawRect(b, halGlow);
            }
            c.drawRect(b, shade);
            if (floorY > 0) {
                c.drawRect(0, floorY, w, h, sheen);
                line.setStrokeWidth(dp(1));
                c.drawLine(w * 0.1f, floorY, w * 0.9f, floorY, line);
            }
        }

        @Override
        public void setAlpha(int a) {
        }

        @Override
        public void setColorFilter(android.graphics.ColorFilter cf) {
        }

        @Override
        public int getOpacity() {
            return android.graphics.PixelFormat.TRANSLUCENT;
        }
    }

    /** HAL's own backdrop behind everything instead of the movie's art (the HAL-style home with his big eye). */
    void useHalBackdrop(boolean on) {
        floor.hal = on;
        floor.madeH = -1;
        artBox.setVisibility(on ? View.GONE : View.VISIBLE);
        if (on) backdrop.setImageDrawable(null);
        floor.invalidateSelf();
    }

    /** Where HAL's eye is (root y): his glow on the backdrop is centred there. */
    void setHalY(float y) {
        if (floor.halY == y) return;
        floor.halY = y;
        floor.invalidateSelf();
        scrim.invalidate();
    }

    /** The posters' foot (root y): the floor's horizon line. */
    void setFloor(int y) {
        if (floor.floorY == y) return;
        floor.floorY = y;
        floor.invalidateSelf();
        scrim.invalidate();
    }
    /** Mild motion (animations on): the embers and the glow breathing, with the art's drift. */
    private final Motes motes;
    private ValueAnimator breathe;

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
        motes = new Motes(ctx);
        motes.setVisibility(View.GONE);
        layer.addView(motes, new FrameLayout.LayoutParams(-1, -1));   // embers drifting up behind the row

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

    void setCinemaLayout(boolean on) {
        cinemaLayout = on;
        info.setVisibility(on ? View.VISIBLE : View.GONE);
        if (!on) applyGlow();   // the glow from the bottom middle, under the dock, as on HAL's screen
        if (!on) scrim.setBackground(floor);
    }

    boolean shown() {
        return layer.getVisibility() == View.VISIBLE;
    }

    void setShown(boolean on) {
        layer.setVisibility(on ? View.VISIBLE : View.GONE);
        if (on) resumeMotion();
        else pause();
    }

    /** The item the big picture is about (the poster with focus, or the one watched last). */
    void show(KodiRow.Item it) {
        if (it == null) return;
        boolean same = item != null && item.id.equals(it.id);
        item = it;
        if (!cinemaLayout) tintTo(posterColor(it.poster));
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
        if (!same && !floor.hal) setArt(it);
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
        // Centred home: the art a little wider than the screen, so it can slide sideways (parallax) with no edge.
        int artW = cinemaLayout ? Math.round(rootW * 0.68f) : rootW + dp(80), artH = cinemaLayout ? Math.round(rootH * 0.76f) : rootH;
        for (View v : new View[]{artBox, scrim}) {
            FrameLayout.LayoutParams lp = (FrameLayout.LayoutParams) v.getLayoutParams();
            int grav = cinemaLayout ? Gravity.TOP | Gravity.END : Gravity.TOP | Gravity.CENTER_HORIZONTAL;
            if (lp.gravity != grav) {
                lp.gravity = grav;
                v.setLayoutParams(lp);
            }
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
        if (motion && breathe == null && !cinemaLayout && !floor.hal) {   // HAL's backdrop: still, no embers
            // HAL's glow breathes (about every 6 seconds) and a few embers drift up behind the posters.
            breathe = ValueAnimator.ofFloat(0.72f, 1f);
            breathe.setDuration(3000L);
            breathe.setRepeatMode(ValueAnimator.REVERSE);
            breathe.setRepeatCount(ValueAnimator.INFINITE);
            breathe.setInterpolator(new android.view.animation.AccelerateDecelerateInterpolator());
            breathe.addUpdateListener(a -> glow.setAlpha((float) a.getAnimatedValue()));
            motes.setVisibility(View.VISIBLE);
            if (shown()) {
                breathe.start();
                motes.run(true);
            }
        }
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
        if (breathe != null) breathe.pause();
        motes.run(false);
    }

    void resumeMotion() {
        if (!shown()) return;
        for (ValueAnimator a : new ValueAnimator[]{drift, breathe}) {
            if (a == null) continue;
            if (a.isPaused()) a.resume();
            else if (!a.isStarted()) a.start();
        }
        if (breathe != null) motes.run(true);
    }

    /** Parallax: the art slides gently against the selection (index of count along the poster row). */
    void parallax(int index, int count) {
        if (cinemaLayout || count < 1) return;
        float centre = (count - 1) / 2f;
        float x = (index - centre) * -dp(16);
        if (breathe == null) artBox.setTranslationX(x);
        else artBox.animate().translationX(x).setDuration(700)
                .setInterpolator(new android.view.animation.DecelerateInterpolator()).start();
    }

    /**
     * HAL wakes up (the TV was switched on): the screen starts black, his eye lights at (x, y) and a soft red pulse
     * spreads from it, then Home fades in. About 2.4 seconds; nothing is waited for (keys work at once).
     */
    void playWake(FrameLayout root, float x, float y, float eyeSize) {
        final View cover = new View(ctx) {
            final HalEyeDrawable eye = new HalEyeDrawable();
            final android.graphics.Paint pulse = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
            final android.graphics.Paint black = new android.graphics.Paint();
            float t;

            {
                setTag(this);
                eye.setCallback(this);
            }

            @Override
            protected boolean verifyDrawable(Drawable who) {
                return who == eye || super.verifyDrawable(who);
            }

            @Override
            protected void onDraw(android.graphics.Canvas c) {
                t = getAlpha() >= 0 ? (float) getTag(R.id.root) : 0f;
                float cover = 1f - smooth((t - 1.1f) / 1.2f);
                black.setColor(Color.argb(Math.round(255 * cover), 0, 0, 0));
                c.drawRect(0, 0, getWidth(), getHeight(), black);
                float pr = smooth((t - 0.7f) / 1.3f);
                if (t > 0.7f && pr < 1f) {
                    float rr = dp(20) + pr * Math.max(getWidth(), getHeight()) * 1.1f;
                    pulse.setShader(new android.graphics.RadialGradient(x, y, rr,
                            new int[]{Color.argb(Math.round(140 * (1 - pr)), 255, 50, 20), Color.argb(0, 255, 40, 15)},
                            new float[]{0f, 1f}, android.graphics.Shader.TileMode.CLAMP));
                    c.drawCircle(x, y, rr, pulse);
                }
                // his eye, lighting up from dark, over the black until Home shows
                float lit = smooth(t / 0.9f);
                int s = Math.round(eyeSize);
                eye.setBounds(Math.round(x - s / 2f), Math.round(y - s / 2f), Math.round(x + s / 2f), Math.round(y + s / 2f));
                c.saveLayerAlpha(0, 0, getWidth(), getHeight(), Math.round(255 * (0.15f + 0.85f * lit) * cover));
                eye.draw(c);
                c.restore();
            }
        };
        cover.setTag(R.id.root, 0f);
        root.addView(cover, new FrameLayout.LayoutParams(-1, -1));
        ValueAnimator a = ValueAnimator.ofFloat(0f, 2.4f);
        a.setDuration(2400);
        a.setInterpolator(new android.view.animation.LinearInterpolator());
        a.addUpdateListener(v -> {
            cover.setTag(R.id.root, v.getAnimatedValue());
            cover.invalidate();
        });
        a.addListener(new android.animation.AnimatorListenerAdapter() {
            @Override
            public void onAnimationEnd(android.animation.Animator an) {
                root.removeView(cover);
            }
        });
        a.start();
    }

    private static float smooth(float x) {
        x = Math.max(0f, Math.min(1f, x));
        return x * x * (3f - 2f * x);
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

    /** HAL's latest line (greeting and quote), for the big eye on the HAL-style home; "" when the PC is off. */
    java.util.function.Consumer<String> halLine;

    void refreshHal() {
        if ((halPill == null && halLine == null) || halBusy || System.currentTimeMillis() - halReadAt < HAL_EVERY_MS) return;
        halBusy = true;
        try {
            io.execute(() -> {
                String level = null, said = "";
                int issues = 0;
                try {
                    JSONObject j = new JSONObject(new String(get("/hal/api/status", 8000), StandardCharsets.UTF_8));
                    JSONObject hal = j.optJSONObject("hal");
                    level = hal == null ? "ok" : hal.optString("level", "ok");
                    if (hal != null) said = (hal.optString("greeting", "") + " " + hal.optString("quote", "")).trim();
                    JSONArray is = j.optJSONArray("issues");
                    for (int i = 0; is != null && i < is.length(); i++) {
                        String l = is.getJSONObject(i).optString("level", "");
                        if (l.equals("warning") || l.equals("critical")) issues++;
                    }
                } catch (Exception ignored) {
                    level = null;   // the PC is off or asleep
                }
                final String lv = level, line = said;
                final int n = issues;
                main.post(() -> {
                    halBusy = false;
                    halReadAt = System.currentTimeMillis();
                    setHal(lv, n);
                    if (halLine != null) halLine.accept(line);
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

    /**
     * A handful of soft embers rising slowly with a little sway, redrawn about 30 times a second; their halo takes
     * the colour of the poster you're on. Also the burst of sparks when a download is ready.
     */
    private final class Motes extends View {
        private static final int COUNT = 16, BURST = 28;
        private final float[] x = new float[COUNT], speed = new float[COUNT], offset = new float[COUNT],
                size = new float[COUNT], sway = new float[COUNT], phase = new float[COUNT];
        private final float[] bx = new float[BURST], by = new float[BURST], bvx = new float[BURST], bvy = new float[BURST],
                bsize = new float[BURST];
        private long burstAt = -1;
        private final android.graphics.Paint core = new android.graphics.Paint(android.graphics.Paint.FILTER_BITMAP_FLAG),
                halo = new android.graphics.Paint(android.graphics.Paint.FILTER_BITMAP_FLAG);
        private final android.graphics.RectF dst = new android.graphics.RectF();
        private Bitmap coreDot, haloDot;
        private boolean running;
        private int haloColor = -1;
        private final long start = android.os.SystemClock.uptimeMillis();

        Motes(Context c) {
            super(c);
            setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
            java.util.Random r = new java.util.Random(9000);
            for (int i = 0; i < COUNT; i++) {
                x[i] = r.nextFloat();
                speed[i] = 0.025f + r.nextFloat() * 0.03f;   // a trip up the screen takes 20 to 40 seconds
                offset[i] = r.nextFloat();
                size[i] = dp(5 + r.nextFloat() * 9);
                sway[i] = dp(10 + r.nextFloat() * 20);
                phase[i] = r.nextFloat() * 6.28f;
            }
        }

        void run(boolean on) {
            if (running == on) return;
            running = on;
            if (on) postInvalidateOnAnimation();
        }

        void burst(float cx, float cy) {
            java.util.Random r = new java.util.Random();
            for (int i = 0; i < BURST; i++) {
                double a = r.nextDouble() * Math.PI * 2;
                float v = dp(120 + r.nextFloat() * 260);
                bx[i] = cx;
                by[i] = cy;
                bvx[i] = (float) Math.cos(a) * v;
                bvy[i] = (float) Math.sin(a) * v - dp(60);
                bsize[i] = dp(3 + r.nextFloat() * 6);
            }
            burstAt = android.os.SystemClock.uptimeMillis();
            postInvalidateOnAnimation();
        }

        private Bitmap sprite(int[] colors, float[] stops) {
            int n = 64;
            Bitmap bm = Bitmap.createBitmap(n, n, Bitmap.Config.ARGB_8888);
            android.graphics.Canvas c = new android.graphics.Canvas(bm);
            android.graphics.Paint p = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
            p.setShader(new android.graphics.RadialGradient(n / 2f, n / 2f, n / 2f, colors, stops,
                    android.graphics.Shader.TileMode.CLAMP));
            c.drawCircle(n / 2f, n / 2f, n / 2f, p);
            return bm;
        }

        @Override
        protected void onDraw(android.graphics.Canvas c) {
            int w = getWidth(), h = getHeight();
            if (w == 0 || h == 0) return;
            if (coreDot == null) {
                coreDot = sprite(new int[]{0xFFFFF0DC, 0x66FFE0C0, 0x00FFE0C0}, new float[]{0f, 0.18f, 0.4f});
                haloDot = sprite(new int[]{0xFFFFFFFF, 0x99FFFFFF, 0x00FFFFFF}, new float[]{0f, 0.35f, 1f});
            }
            if (haloColor != tintNow) {
                haloColor = tintNow;
                halo.setColorFilter(new android.graphics.PorterDuffColorFilter(tintNow | 0xFF000000,
                        android.graphics.PorterDuff.Mode.SRC_IN));
            }
            long now = android.os.SystemClock.uptimeMillis();
            float t = (now - start) / 1000f;
            for (int i = 0; i < COUNT; i++) {
                float p = (offset[i] + t * speed[i]) % 1f;
                float cy = h + dp(20) - p * (h + dp(60));
                float cx = x[i] * w + (float) Math.sin(t * 0.6f + phase[i]) * sway[i];
                int a = Math.round(200 * (float) Math.sin(p * Math.PI));
                dot(c, cx, cy, size[i], a);
            }
            boolean bursting = burstAt >= 0 && now - burstAt < 1400;
            if (bursting) {
                float bt = (now - burstAt) / 1000f;
                for (int i = 0; i < BURST; i++) {
                    float drag = (float) (1 - Math.exp(-2.2 * bt)) / 2.2f;   // slows as it goes
                    float px = bx[i] + bvx[i] * drag, py = by[i] + bvy[i] * drag + dp(90) * bt * bt;
                    dot(c, px, py, bsize[i] * (1f - bt / 1.6f), Math.round(255 * Math.max(0f, 1f - bt / 1.4f)));
                }
            } else {
                burstAt = -1;
            }
            if (running || bursting) postInvalidateDelayed(bursting ? 16 : 33);
        }

        private void dot(android.graphics.Canvas c, float cx, float cy, float r, int alpha) {
            halo.setAlpha(alpha);
            dst.set(cx - r, cy - r, cx + r, cy + r);
            c.drawBitmap(haloDot, null, dst, halo);
            core.setAlpha(alpha);
            c.drawBitmap(coreDot, null, dst, core);
        }
    }

    // ---- colour: the poster's, a little warmer in the evening and cooler in the morning --------

    /** The ember colour on screen now (RGB), eased towards tintTarget. */
    private int tintNow = 0xFF3C14, tintTarget = 0xFF3C14;
    private ValueAnimator tintAnim;
    private static final java.util.Map<String, Integer> posterColors = new java.util.HashMap<>();

    /** Sparks fly from (x, y) in root coordinates: a download is ready. */
    void burst(float x, float y) {
        if (!shown() || breathe == null) return;
        motes.setVisibility(View.VISIBLE);
        motes.burst(x, y);
    }

    private void tintTo(int rgb) {
        rgb = timeOfDay(rgb) & 0xFFFFFF;
        if (rgb == tintTarget) return;
        tintTarget = rgb;
        final int from = tintNow, to = rgb;
        if (tintAnim != null) tintAnim.cancel();
        tintAnim = ValueAnimator.ofFloat(0f, 1f);
        tintAnim.setDuration(breathe == null ? 0 : 900);
        tintAnim.addUpdateListener(a -> {
            tintNow = (Integer) new android.animation.ArgbEvaluator().evaluate(a.getAnimatedFraction(),
                    from | 0xFF000000, to | 0xFF000000) & 0xFFFFFF;
            applyGlow();
        });
        tintAnim.start();
    }

    private void applyGlow() {
        if (cinemaLayout) return;
        GradientDrawable g = new GradientDrawable();
        g.setGradientType(GradientDrawable.RADIAL_GRADIENT);
        g.setColors(new int[]{0x55000000 | tintNow, 0x00000000 | tintNow});
        g.setGradientRadius(dp(560));
        g.setGradientCenter(0.5f, 1.08f);
        glow.setBackground(g);
        motes.invalidate();
    }

    /** Evening (5 pm to 11 pm) leans to deep orange, morning (6 to 11 am) a little towards cool blue. */
    static int timeOfDay(int rgb) {
        int hour = java.util.Calendar.getInstance().get(java.util.Calendar.HOUR_OF_DAY);
        int toward;
        float k;
        if (hour >= 17 && hour < 23) {
            toward = 0xFF4A10;
            k = 0.22f;
        } else if (hour >= 6 && hour < 11) {
            toward = 0x8AA8FF;
            k = 0.2f;
        } else {
            return rgb;
        }
        return mix(rgb, toward, k);
    }

    static int mix(int a, int b, float k) {
        int r = Math.round(((a >> 16) & 255) * (1 - k) + ((b >> 16) & 255) * k);
        int g = Math.round(((a >> 8) & 255) * (1 - k) + ((b >> 8) & 255) * k);
        int bl = Math.round((a & 255) * (1 - k) + (b & 255) * k);
        return (r << 16) | (g << 8) | bl;
    }

    /**
     * A poster's colour: the average of its bright, saturated pixels (dull and dark ones count little), made bright
     * and rich enough to glow. HAL red when there is none.
     */
    static int posterColor(File poster) {
        if (poster == null || !poster.isFile()) return 0xFF3C14;
        Integer cached = posterColors.get(poster.getPath() + poster.lastModified());
        if (cached != null) return cached;
        int out = 0xFF3C14;
        try {
            BitmapFactory.Options o = new BitmapFactory.Options();
            o.inJustDecodeBounds = true;
            BitmapFactory.decodeFile(poster.getPath(), o);
            int sample = 1;
            while (o.outWidth / (sample * 2) >= 32) sample *= 2;
            o = new BitmapFactory.Options();
            o.inSampleSize = sample;
            Bitmap bm = BitmapFactory.decodeFile(poster.getPath(), o);
            if (bm != null) {
                double sr = 0, sg = 0, sb = 0, sw = 0;
                float[] hsv = new float[3];
                for (int yy = 0; yy < bm.getHeight(); yy++) {
                    for (int xx = 0; xx < bm.getWidth(); xx++) {
                        int c = bm.getPixel(xx, yy);
                        Color.colorToHSV(c, hsv);
                        double w = hsv[1] * hsv[1] * hsv[2];
                        sr += Color.red(c) * w;
                        sg += Color.green(c) * w;
                        sb += Color.blue(c) * w;
                        sw += w;
                    }
                }
                bm.recycle();
                if (sw > 0.5) {
                    Color.colorToHSV(Color.rgb((int) (sr / sw), (int) (sg / sw), (int) (sb / sw)), hsv);
                    hsv[1] = Math.max(0.65f, hsv[1]);
                    hsv[2] = 1f;
                    out = Color.HSVToColor(hsv) & 0xFFFFFF;
                }
            }
        } catch (Exception ignored) {
            // HAL red
        }
        posterColors.put(poster.getPath() + poster.lastModified(), out);
        return out;
    }

    void shutdown() {
        io.shutdownNow();
        if (drift != null) drift.cancel();
        if (breathe != null) breathe.cancel();
        motes.run(false);
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
