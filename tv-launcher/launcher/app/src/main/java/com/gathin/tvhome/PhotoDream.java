package com.gathin.tvhome;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.os.Handler;
import android.os.Looper;
import android.service.dreams.DreamService;
import android.util.DisplayMetrics;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextClock;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * NZB-CC screensaver: photos from a folder on the PC (NZB Command Center's phone server, GET /api/tv/photos),
 * one after another with a slow zoom and a cross-fade, and the clock, date and weather in the corner.
 *
 * <p>Each photo shown is kept on the TV (the last {@link #CACHE_MAX}), so the screensaver still has pictures
 * while the PC is off. With none at all it shows the clock on black.
 */
public class PhotoDream extends DreamService {
    static final long SHOW_MS = 15_000L;
    private static final long FADE_MS = 1_500L;
    private static final int CACHE_MAX = 60;

    private final Handler main = new Handler(Looper.getMainLooper());
    private ExecutorService io;
    private HomeExtras extras;
    private File cache;
    /** Two layers that cross-fade, each a blurred fill behind the photo itself. */
    private FrameLayout[] frames;
    private ImageView[] fills, photos;
    private int front;
    private TextView caption;
    private final List<String[]> queue = new ArrayList<>();   // {id, caption}
    private int next;
    private boolean running;
    private int screenW, screenH;

    @Override
    protected void attachBaseContext(android.content.Context base) {
        super.attachBaseContext(LauncherLocale.wrap(base));
    }

    @Override
    public void onAttachedToWindow() {
        super.onAttachedToWindow();
        setInteractive(false);   // any key wakes the TV back to where it was
        setFullscreen(true);
        setScreenBright(true);
        extras = HomeExtras.load(this);
        cache = new File(getFilesDir(), "dream_photos");
        DisplayMetrics dm = getResources().getDisplayMetrics();
        screenW = Math.max(dm.widthPixels, dm.heightPixels);
        screenH = Math.min(dm.widthPixels, dm.heightPixels);
        setContentView(build());
        ClockFormat.apply(getWindow().getDecorView(), Config.load(this).clockFormat);
    }

    private int dp(float v) {
        return Math.round(TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics()));
    }

    private View build() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        frames = new FrameLayout[2];
        fills = new ImageView[2];
        photos = new ImageView[2];
        for (int i = 0; i < 2; i++) {
            FrameLayout layer = new FrameLayout(this);
            layer.setAlpha(0f);
            ImageView fill = new ImageView(this);
            fill.setScaleType(ImageView.ScaleType.CENTER_CROP);
            // A tiny copy stretched over the screen reads as a blur; darkened so the photo stands out.
            fill.setColorFilter(new android.graphics.PorterDuffColorFilter(0xFF6E6E6E, android.graphics.PorterDuff.Mode.MULTIPLY));
            layer.addView(fill, new FrameLayout.LayoutParams(-1, -1));
            ImageView photo = new ImageView(this);
            layer.addView(photo, new FrameLayout.LayoutParams(-1, -1));
            root.addView(layer, new FrameLayout.LayoutParams(-1, -1));
            frames[i] = layer;
            fills[i] = fill;
            photos[i] = photo;
        }
        // A soft shade at the bottom so the clock reads over a bright photo.
        View shade = new View(this);
        shade.setBackground(new GradientDrawable(GradientDrawable.Orientation.BOTTOM_TOP,
                new int[]{0xB3000000, 0x00000000}));
        root.addView(shade, new FrameLayout.LayoutParams(-1, dp(220), Gravity.BOTTOM));

        LinearLayout corner = new LinearLayout(this);
        corner.setOrientation(LinearLayout.VERTICAL);
        FrameLayout.LayoutParams clp = new FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM | Gravity.START);
        clp.setMargins(dp(56), 0, 0, dp(44));
        root.addView(corner, clp);

        TextClock clock = new TextClock(this);
        clock.setFormat12Hour("h:mm");
        clock.setFormat24Hour("HH:mm");
        clock.setTextColor(Color.WHITE);
        clock.setTextSize(TypedValue.COMPLEX_UNIT_SP, 56);
        clock.setTypeface(UiFont.get(this, R.string.font_light));
        clock.setIncludeFontPadding(false);
        clock.setShadowLayer(dp(8), 0, 0, 0x99000000);
        corner.addView(clock);

        TextClock date = new TextClock(this);
        String pattern = android.text.format.DateFormat.getBestDateTimePattern(java.util.Locale.getDefault(), "EEEEdMMMM");
        date.setFormat12Hour(pattern);
        date.setFormat24Hour(pattern);
        date.setTextColor(0xE6FFFFFF);
        date.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        date.setTypeface(UiFont.get(this, R.string.font_regular));
        date.setShadowLayer(dp(6), 0, 0, 0x99000000);
        LinearLayout.LayoutParams dlp = new LinearLayout.LayoutParams(-2, -2);
        dlp.topMargin = dp(6);
        corner.addView(date, dlp);

        String pill = getSharedPreferences("weather_now", MODE_PRIVATE).getString("pill", "");
        if (!pill.isEmpty()) {
            TextView weather = new TextView(this);
            weather.setText(pill.trim().replaceAll("\\s+", " "));
            weather.setTextColor(0xCCFFFFFF);
            weather.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
            weather.setTypeface(UiFont.get(this, R.string.font_regular));
            weather.setShadowLayer(dp(6), 0, 0, 0x99000000);
            LinearLayout.LayoutParams wlp = new LinearLayout.LayoutParams(-2, -2);
            wlp.topMargin = dp(4);
            corner.addView(weather, wlp);
        }

        caption = new TextView(this);
        caption.setTextColor(0x99FFFFFF);
        caption.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        caption.setTypeface(UiFont.get(this, R.string.font_regular));
        caption.setShadowLayer(dp(6), 0, 0, 0x99000000);
        caption.setSingleLine(true);
        caption.setMaxWidth(dp(420));
        caption.setEllipsize(android.text.TextUtils.TruncateAt.END);
        FrameLayout.LayoutParams plp = new FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM | Gravity.END);
        plp.setMargins(0, 0, dp(56), dp(48));
        root.addView(caption, plp);
        caption.setVisibility(View.GONE);   // NZB-CC: no file name or date over the photos
        return root;
    }

    @Override
    public void onDreamingStarted() {
        super.onDreamingStarted();
        running = true;
        io = Executors.newSingleThreadExecutor();
        io.execute(this::loadList);
    }

    @Override
    public void onDreamingStopped() {
        stop();
        super.onDreamingStopped();
    }

    @Override
    public void onDetachedFromWindow() {
        stop();
        super.onDetachedFromWindow();
    }

    private void stop() {
        running = false;
        main.removeCallbacksAndMessages(null);
        if (io != null) io.shutdownNow();
        io = null;
    }

    // ---- photos ---------------------------------------------------------------------------------

    /** The PC's list, shuffled; without the PC, the photos kept from earlier. */
    private void loadList() {
        List<String[]> list = new ArrayList<>();
        if (!extras.kodiServer.isEmpty()) {
            try {
                JSONObject j = new JSONObject(new String(get("/api/tv/photos", 8000), StandardCharsets.UTF_8));
                JSONArray a = j.getJSONArray("items");
                for (int i = 0; i < a.length(); i++) {
                    JSONObject o = a.getJSONObject(i);
                    String id = o.optString("id", "");
                    if (!id.matches("[0-9a-f]{16}")) continue;
                    list.add(new String[]{id, o.optString("name", "") + "  ·  " + o.optString("date", "")});
                }
            } catch (Exception ignored) {
                list.clear();   // the PC is off or has no photos: fall back to the ones kept here
            }
        }
        if (list.isEmpty()) {
            File[] kept = cache.listFiles((d, n) -> n.endsWith(".img"));
            if (kept != null) for (File f : kept) list.add(new String[]{f.getName().replace(".img", ""), ""});
        }
        Collections.shuffle(list);
        main.post(() -> {
            queue.clear();
            queue.addAll(list);
            next = 0;
            if (running && !queue.isEmpty()) showNext();
        });
    }

    private void showNext() {
        if (!running || queue.isEmpty() || io == null) return;
        String[] item = queue.get(next % queue.size());
        next++;
        try {
            io.execute(() -> {
                Bitmap bm = load(item[0]);
                main.post(() -> {
                    if (!running) return;
                    if (bm != null) show(bm, item[1]);
                    main.postDelayed(this::showNext, bm != null ? SHOW_MS : 500);
                });
            });
        } catch (java.util.concurrent.RejectedExecutionException ignored) {
            // stopping
        }
    }

    private void show(Bitmap bm, String text) {
        FrameLayout old = frames[front];
        final int oldIndex = front;
        front = 1 - front;
        FrameLayout now = frames[front];
        ImageView photo = photos[front], fill = fills[front];
        // A photo close to the screen's shape fills it edge to edge; a tall or square one is shown whole, centred,
        // with a blurred, darker copy of itself filling the sides (nothing cut off).
        float ratio = bm.getWidth() / (float) Math.max(1, bm.getHeight());
        float screen = screenW / (float) Math.max(1, screenH);
        boolean fills_ = ratio >= screen * 0.85f && ratio <= screen * 1.3f;
        photo.setScaleType(fills_ ? ImageView.ScaleType.CENTER_CROP : ImageView.ScaleType.FIT_CENTER);
        photo.setImageBitmap(bm);
        if (fills_) {
            fill.setImageDrawable(null);
        } else {
            int w = 48, h = Math.max(1, Math.round(48 / ratio));
            fill.setImageBitmap(Bitmap.createScaledBitmap(bm, w, h, true));
        }
        photo.setScaleX(1f);
        photo.setScaleY(1f);
        now.setAlpha(0f);
        // Fade in over FADE_MS while drifting slowly closer for the whole turn (separate animators: one
        // ViewPropertyAnimator would give both the same duration). A small zoom, so the edges stay in view.
        android.animation.ObjectAnimator.ofFloat(now, View.ALPHA, 0f, 1f).setDuration(FADE_MS).start();
        float to = fills_ ? 1.05f : 1.03f;
        android.animation.AnimatorSet zoom = new android.animation.AnimatorSet();
        zoom.playTogether(android.animation.ObjectAnimator.ofFloat(photo, View.SCALE_X, 1f, to),
                android.animation.ObjectAnimator.ofFloat(photo, View.SCALE_Y, 1f, to));
        zoom.setDuration(SHOW_MS + FADE_MS);
        zoom.setInterpolator(new android.view.animation.LinearInterpolator());
        zoom.start();
        android.animation.ObjectAnimator out = android.animation.ObjectAnimator.ofFloat(old, View.ALPHA, old.getAlpha(), 0f);
        out.setDuration(FADE_MS);
        out.addListener(new android.animation.AnimatorListenerAdapter() {
            @Override
            public void onAnimationEnd(android.animation.Animator animation) {
                if (front != oldIndex) {
                    photos[oldIndex].setImageDrawable(null);
                    fills[oldIndex].setImageDrawable(null);
                }
            }
        });
        out.start();
        caption.setText(text);
    }

    /** The photo from the TV's cache, or fetched from the PC and kept. Decoded no larger than the screen needs. */
    private Bitmap load(String id) {
        File f = new File(cache, id + ".img");
        if (!f.isFile() && !extras.kodiServer.isEmpty()) {
            try {
                write(f, get("/api/tv/photo?id=" + id, 20000));
                trimCache();
            } catch (Exception ignored) {
                return null;
            }
        }
        if (!f.isFile()) return null;
        BitmapFactory.Options o = new BitmapFactory.Options();
        o.inJustDecodeBounds = true;
        BitmapFactory.decodeFile(f.getPath(), o);
        if (o.outWidth <= 0) {
            //noinspection ResultOfMethodCallIgnored
            f.delete();
            return null;
        }
        int sample = 1;
        while (o.outWidth / (sample * 2) >= screenW && o.outHeight / (sample * 2) >= screenH) sample *= 2;
        BitmapFactory.Options d = new BitmapFactory.Options();
        d.inSampleSize = sample;
        //noinspection ResultOfMethodCallIgnored
        f.setLastModified(System.currentTimeMillis());
        Bitmap bm = BitmapFactory.decodeFile(f.getPath(), d);
        return bm == null ? null : upright(bm, f);
    }

    /** Turns a photo the right way up when the camera saved it sideways with an orientation note (EXIF). */
    private static Bitmap upright(Bitmap bm, File f) {
        int degrees;
        try {
            int o = new android.media.ExifInterface(f.getPath()).getAttributeInt(
                    android.media.ExifInterface.TAG_ORIENTATION, android.media.ExifInterface.ORIENTATION_NORMAL);
            degrees = o == android.media.ExifInterface.ORIENTATION_ROTATE_90 ? 90
                    : o == android.media.ExifInterface.ORIENTATION_ROTATE_180 ? 180
                    : o == android.media.ExifInterface.ORIENTATION_ROTATE_270 ? 270 : 0;
        } catch (Exception e) {
            degrees = 0;   // not a JPEG with EXIF (PNG, WebP): as it is
        }
        if (degrees == 0) return bm;
        android.graphics.Matrix m = new android.graphics.Matrix();
        m.postRotate(degrees);
        Bitmap turned = Bitmap.createBitmap(bm, 0, 0, bm.getWidth(), bm.getHeight(), m, true);
        if (turned != bm) bm.recycle();
        return turned;
    }

    private void trimCache() {
        File[] files = cache.listFiles((d, n) -> n.endsWith(".img"));
        if (files == null || files.length <= CACHE_MAX) return;
        Arrays.sort(files, (a, b) -> Long.compare(a.lastModified(), b.lastModified()));
        for (int i = 0; i < files.length - CACHE_MAX; i++) //noinspection ResultOfMethodCallIgnored
            files[i].delete();
    }

    private byte[] get(String path, int timeoutMs) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(extras.kodiServer + path).openConnection();
        c.setConnectTimeout(Math.min(timeoutMs, 4000));
        c.setReadTimeout(timeoutMs);
        c.setRequestProperty("X-Mobile-Token", extras.kodiToken);
        try {
            if (c.getResponseCode() / 100 != 2) throw new IOException("HTTP " + c.getResponseCode());
            try (InputStream in = c.getInputStream()) {
                return HomeExtras.readAll(in);
            }
        } finally {
            c.disconnect();
        }
    }

    private static void write(File f, byte[] data) throws IOException {
        File dir = f.getParentFile();
        if (dir != null && !dir.isDirectory() && !dir.mkdirs()) throw new IOException("no folder");
        File tmp = new File(f.getPath() + ".tmp");
        try (OutputStream out = new FileOutputStream(tmp)) {
            out.write(data);
        }
        if (!tmp.renameTo(f)) throw new IOException("rename failed");
    }
}
