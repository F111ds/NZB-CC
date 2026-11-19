package com.gathin.tvhome;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RadialGradient;
import android.graphics.Shader;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.service.dreams.DreamService;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.speech.tts.Voice;
import android.text.SpannableString;
import android.text.Spanned;
import android.text.style.ForegroundColorSpan;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextClock;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Calendar;
import java.util.Locale;
import java.util.Random;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * NZB-CC: the HAL 9000 screensaver. HAL's eye, the clock, what HAL is saying and live readings from the PC
 * (the phone server's /hal/api/brief, every 30 s). HAL says his line out loud through the TV when the
 * screensaver starts and then at most every 30 minutes, never between 10 PM and 8 AM; the eye brightens
 * with each word and the line types out as he speaks. Pick it in Settings, Screen saver, "HAL 9000".
 *
 * <p>Against burn-in, everything drifts a little every few minutes, and the screen dims late at night.
 */
public class HalDream extends DreamService {
    private static final long REFRESH_MS = 30_000L;
    private static final long DRIFT_MS = 180_000L;
    private static final long TALK_EVERY_MS = 30 * 60_000L;
    private static final int QUIET_FROM = 22, QUIET_TO = 8;

    private final Handler main = new Handler(Looper.getMainLooper());
    private final Random rnd = new Random();
    private ExecutorService io;
    private HomeExtras extras;
    private boolean running;

    private FrameLayout root;
    private LinearLayout page;
    private EyeView eye;
    private TextView quote, line;
    private TextView[][] tiles;   // {value, sub} per tile
    private TextView[] tileKeys;

    private TextToSpeech tts;
    private boolean ttsReady;
    private String lastSay = "", shownQuote = "";
    private boolean speaking;
    private boolean saidOnStart;
    private long lastTalkAt;
    private String pendingSay = "";

    @Override
    protected void attachBaseContext(Context base) {
        super.attachBaseContext(LauncherLocale.wrap(base));
    }

    @Override
    public void onAttachedToWindow() {
        super.onAttachedToWindow();
        setInteractive(false);   // any key wakes the TV back to where it was
        setFullscreen(true);
        setScreenBright(true);
        extras = HomeExtras.load(this);
        setContentView(build());
        ClockFormat.apply(getWindow().getDecorView(), Config.load(this).clockFormat);
    }

    private int dp(float v) {
        return Math.round(TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics()));
    }

    private TextView text(float sp, int color, Typeface tf) {
        TextView t = new TextView(this);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(color);
        t.setTypeface(tf);
        t.setIncludeFontPadding(false);
        return t;
    }

    private View build() {
        Typeface light = UiFont.get(this, R.string.font_light), regular = UiFont.get(this, R.string.font_regular);
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(60), dp(44), dp(60), dp(36));
        root.addView(page, new FrameLayout.LayoutParams(-1, -1));

        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);
        page.addView(top, new LinearLayout.LayoutParams(-1, -2));
        eye = new EyeView(this);
        top.addView(eye, new LinearLayout.LayoutParams(dp(180), dp(180)));
        LinearLayout clockBox = new LinearLayout(this);
        clockBox.setOrientation(LinearLayout.VERTICAL);
        LinearLayout.LayoutParams cbl = new LinearLayout.LayoutParams(-2, -2);
        cbl.leftMargin = dp(44);
        top.addView(clockBox, cbl);
        TextClock clock = new TextClock(this);
        clock.setFormat12Hour("h:mm a");
        clock.setFormat24Hour("HH:mm");
        clock.setTextSize(TypedValue.COMPLEX_UNIT_SP, 96);
        clock.setTextColor(Color.WHITE);
        clock.setTypeface(light);
        clock.setIncludeFontPadding(false);
        clockBox.addView(clock);
        TextClock date = new TextClock(this);
        String pattern = android.text.format.DateFormat.getBestDateTimePattern(Locale.getDefault(), "EEEEdMMMM");
        date.setFormat12Hour(pattern);
        date.setFormat24Hour(pattern);
        date.setTextSize(TypedValue.COMPLEX_UNIT_SP, 20);
        date.setTextColor(0xFFA1A1AA);
        date.setTypeface(regular);
        LinearLayout.LayoutParams dl = new LinearLayout.LayoutParams(-2, -2);
        dl.topMargin = dp(10);
        clockBox.addView(date, dl);

        quote = text(21, 0xFFA1A1AA, Typeface.create(regular, Typeface.ITALIC));
        quote.setLineSpacing(0, 1.15f);
        quote.setText("“I'm reading the PC, Dave.”");
        LinearLayout.LayoutParams ql = new LinearLayout.LayoutParams(-1, -2);
        ql.topMargin = dp(30);
        page.addView(quote, ql);

        line = text(15, 0xFFA1A1AA, regular);
        line.setSingleLine(true);
        line.setEllipsize(android.text.TextUtils.TruncateAt.END);
        LinearLayout.LayoutParams ll = new LinearLayout.LayoutParams(-1, -2);
        ll.topMargin = dp(18);
        page.addView(line, ll);

        View spring = new View(this);
        page.addView(spring, new LinearLayout.LayoutParams(-1, 0, 1f));

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        page.addView(row, new LinearLayout.LayoutParams(-1, -2));
        String[] keys = {"CPU", "MEMORY", "INTERNET", "PI-HOLE", "UPTIME KUMA", "DRIVES"};
        tiles = new TextView[keys.length][2];
        tileKeys = new TextView[keys.length];
        Typeface mono = Typeface.MONOSPACE;
        for (int i = 0; i < keys.length; i++) {
            LinearLayout t = new LinearLayout(this);
            t.setOrientation(LinearLayout.VERTICAL);
            t.setPadding(dp(16), dp(14), dp(16), dp(14));
            GradientDrawable bg = new GradientDrawable();
            bg.setColor(0xFF0B0B0E);
            bg.setCornerRadius(dp(14));
            t.setBackground(bg);
            LinearLayout.LayoutParams tl = new LinearLayout.LayoutParams(0, dp(110), 1f);
            if (i > 0) tl.leftMargin = dp(12);
            row.addView(t, tl);
            TextView k = text(10, 0xFF71717A, Typeface.create(mono, Typeface.BOLD));
            k.setLetterSpacing(0.18f);
            k.setText(keys[i]);
            t.addView(k);
            TextView v = text(25, Color.WHITE, Typeface.create(mono, Typeface.BOLD));
            v.setSingleLine(true);
            v.setText("—");
            LinearLayout.LayoutParams vl = new LinearLayout.LayoutParams(-2, -2);
            vl.topMargin = dp(10);
            t.addView(v, vl);
            TextView s = text(11, 0xFF71717A, mono);
            s.setSingleLine(true);
            s.setEllipsize(android.text.TextUtils.TruncateAt.END);
            LinearLayout.LayoutParams sl = new LinearLayout.LayoutParams(-2, -2);
            sl.topMargin = dp(6);
            t.addView(s, sl);
            tileKeys[i] = k;
            tiles[i][0] = v;
            tiles[i][1] = s;
        }
        return root;
    }

    @Override
    public void onDreamingStarted() {
        super.onDreamingStarted();
        running = true;
        io = Executors.newSingleThreadExecutor();
        eye.start();
        tts = new TextToSpeech(this, status -> {
            if (status != TextToSpeech.SUCCESS || tts == null) return;
            pickVoice();
            tts.setPitch(0.7f);
            tts.setSpeechRate(0.85f);
            tts.setOnUtteranceProgressListener(new Talk());
            ttsReady = true;
            android.util.Log.i("HalDream", "Text-to-speech ready: " + tts.getDefaultEngine() + ", voice " + (tts.getVoice() != null ? tts.getVoice().getName() : "?"));
            // The readings may have come in before the voice was ready: say them now.
            main.post(() -> {
                if (running && !pendingSay.isEmpty()) maybeTalk(pendingSay);
            });
        });
        refresh();
        main.postDelayed(this::drift, DRIFT_MS);
        dimForTime();
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
        if (eye != null) eye.stop();
        if (io != null) io.shutdownNow();
        io = null;
        if (tts != null) {
            try {
                tts.stop();
                tts.shutdown();
            } catch (Exception ignored) {
                // already gone
            }
            tts = null;
        }
        ttsReady = false;
    }

    // ---- data -----------------------------------------------------------------------------------

    private void refresh() {
        if (!running || io == null) return;
        try {
            io.execute(() -> {
                JSONObject j = null;
                try {
                    if (!extras.kodiServer.isEmpty())
                        j = new JSONObject(new String(get("/hal/api/brief", 10000), StandardCharsets.UTF_8));
                } catch (Exception ignored) {
                    j = null;   // PC off or HAL not running: keep what's on screen, say so
                }
                final JSONObject data = j;
                main.post(() -> {
                    if (!running) return;
                    show(data);
                    main.postDelayed(this::refresh, REFRESH_MS);
                });
            });
        } catch (java.util.concurrent.RejectedExecutionException ignored) {
            // stopping
        }
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

    private static String pct(JSONObject j, String k) {
        return j.isNull(k) ? "—" : Math.round(j.optDouble(k)) + "%";
    }

    private static String size(double bytes) {
        if (bytes <= 0) return "0 B";
        String[] u = {"B", "KB", "MB", "GB", "TB"};
        int i = 0;
        while (bytes >= 1024 && i < u.length - 1) {
            bytes /= 1024;
            i++;
        }
        return (bytes >= 100 || i == 0 ? String.valueOf(Math.round(bytes)) : String.format(Locale.US, "%.1f", bytes)) + " " + u[i];
    }

    private void tile(int i, String value, String sub, int color) {
        tiles[i][0].setText(value);
        tiles[i][0].setTextColor(color);
        tiles[i][1].setText(sub);
    }

    private void show(JSONObject j) {
        if (j == null) {
            eye.setLevel("");
            if (!speaking) quote.setText("“I can't reach the PC right now. I'll keep trying.”");
            line.setText("");
            return;
        }
        String level = j.optString("level", "");
        eye.setLevel(level);
        shownQuote = j.optString("greeting", "") + " " + j.optString("quote", "");
        if (!speaking) quote.setText("“" + shownQuote.trim() + "”");

        JSONArray alerts = j.optJSONArray("alerts");
        if (alerts != null && alerts.length() > 0) {
            StringBuilder b = new StringBuilder();
            for (int i = 0; i < alerts.length(); i++) {
                if (i > 0) b.append("     ");
                b.append("●  ").append(alerts.optJSONObject(i).optString("title"));
            }
            line.setText(b);
            line.setTextColor("critical".equals(level) ? 0xFFFF6A4D : 0xFFFBBF24);
        } else {
            String d = j.optString("downloading", "");
            line.setText(d.isEmpty() ? "" : "Downloading  ·  " + d.replaceFirst("^downloading ", ""));
            line.setTextColor(0xFFA1A1AA);
        }

        int white = Color.WHITE, amber = 0xFFFBBF24, red = 0xFFFF6A4D, green = 0xFF34D399;
        tile(0, pct(j, "cpu"), j.isNull("cpu_temp") ? "" : Math.round(j.optDouble("cpu_temp")) + "°C", white);
        tile(1, pct(j, "ram"), j.isNull("mem_total") ? "" : size(j.optDouble("mem_used")) + " of " + size(j.optDouble("mem_total")), white);
        if (j.optBoolean("internet_down")) tile(2, "DOWN", "no internet", red);
        else tile(2, j.isNull("ping") ? "—" : Math.round(j.optDouble("ping")) + " ms",
                j.isNull("net_down") ? "" : size(j.optDouble("net_down")) + "/s down", white);
        if (j.isNull("ads")) tile(3, "—", "not connected", white);
        else tile(3, String.format(Locale.getDefault(), "%,d", j.optLong("ads")),
                j.optBoolean("ads_on", true) ? "ads blocked today" : "blocking paused", j.optBoolean("ads_on", true) ? white : amber);
        if (j.isNull("kuma_down")) tile(4, "—", "not connected", white);
        else {
            int down = j.optInt("kuma_down");
            tile(4, down > 0 ? down + " down" : "all up", j.optInt("kuma_up") + " of " + j.optInt("kuma_total"), down > 0 ? red : green);
        }
        JSONObject f = j.optJSONObject("fullest");
        if (f == null) tile(5, "—", "", white);
        else {
            double p = f.optDouble("pct");
            tile(5, f.optString("mount") + " " + Math.round(p) + "%", size(f.optDouble("free")) + " free", p >= 90 ? amber : white);
        }
        maybeTalk(j.optString("say", ""));
    }

    // ---- talking --------------------------------------------------------------------------------

    private void pickVoice() {
        try {
            Voice best = null;
            int bestScore = Integer.MIN_VALUE;
            for (Voice v : tts.getVoices()) {
                Locale l = v.getLocale();
                if (l == null || !"en".equals(l.getLanguage()) || v.isNetworkConnectionRequired()) continue;
                // A voice listed but not downloaded says nothing at all.
                if (v.getFeatures() != null && v.getFeatures().contains(TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED)) continue;
                String n = v.getName().toLowerCase(Locale.US);
                int s = 0;
                if (n.contains("male") && !n.contains("female")) s += 10;
                if ("GB".equals(l.getCountry())) s += 4;
                if ("US".equals(l.getCountry())) s += 2;
                s += v.getQuality() / 100;
                if (s > bestScore) {
                    bestScore = s;
                    best = v;
                }
            }
            if (best == null || tts.setVoice(best) != TextToSpeech.SUCCESS) {
                if (tts.setLanguage(Locale.UK) < TextToSpeech.LANG_AVAILABLE) tts.setLanguage(Locale.US);
            }
        } catch (Exception e) {
            tts.setLanguage(Locale.US);
        }
    }

    private boolean quietNow() {
        int h = Calendar.getInstance().get(Calendar.HOUR_OF_DAY);
        return h >= QUIET_FROM || h < QUIET_TO;
    }

    /** HAL speaks each time the screensaver starts, then every 30 minutes while it keeps running; never in quiet hours. */
    private void maybeTalk(String say) {
        if (say.isEmpty()) return;
        if (!ttsReady) {
            pendingSay = say;
            return;
        }
        pendingSay = "";
        if (speaking || quietNow()) return;
        long now = System.currentTimeMillis();
        if (saidOnStart && now - lastTalkAt < TALK_EVERY_MS) return;
        saidOnStart = true;
        lastTalkAt = now;
        lastSay = say;
        Bundle b = new Bundle();
        b.putInt(TextToSpeech.Engine.KEY_PARAM_STREAM, android.media.AudioManager.STREAM_MUSIC);
        b.putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, 1f);
        speaking = true;
        reveal(0f);
        int r = tts.speak(say, TextToSpeech.QUEUE_FLUSH, b, "hal");
        android.util.Log.i("HalDream", "speak -> " + r);
        if (r != TextToSpeech.SUCCESS) speaking = false;
    }

    /** Shows the part of HAL's line he has said so far (the rest faint). */
    private void reveal(float fraction) {
        String q = "“" + shownQuote.trim() + "”";
        int upTo = Math.max(1, Math.min(q.length(), Math.round(q.length() * fraction)));
        SpannableString s = new SpannableString(q);
        s.setSpan(new ForegroundColorSpan(0xFFF4F4F5), 0, upTo, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        if (upTo < q.length()) s.setSpan(new ForegroundColorSpan(0x26F4F4F5), upTo, q.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        quote.setText(s);
    }

    private class Talk extends UtteranceProgressListener {
        @Override
        public void onStart(String id) {
            main.post(() -> eye.pulse());
        }

        @Override
        public void onRangeStart(String id, int start, int end, int frame) {
            final float f = lastSay.isEmpty() ? 1f : end / (float) lastSay.length();
            main.post(() -> {
                if (!running) return;
                eye.pulse();
                reveal(f);
            });
        }

        @Override
        public void onDone(String id) {
            main.post(() -> {
                speaking = false;
                if (running) quote.setText("“" + shownQuote.trim() + "”");
                quote.setTextColor(0xFFA1A1AA);
            });
        }

        @Override
        public void onError(String id) {
            onDone(id);
        }
    }

    // ---- burn-in care ---------------------------------------------------------------------------

    private void drift() {
        if (!running) return;
        page.animate().translationX(dp(rnd.nextInt(41) - 20)).translationY(dp(rnd.nextInt(25) - 12)).setDuration(4000).start();
        dimForTime();
        main.postDelayed(this::drift, DRIFT_MS);
    }

    private void dimForTime() {
        root.animate().alpha(quietNow() ? 0.55f : 1f).setDuration(2000).start();
    }

    // ---- HAL's eye ------------------------------------------------------------------------------

    /** The red lens with its rings; it breathes slowly, flares on each spoken word, and gets an amber or red ring. */
    static class EyeView extends View {
        private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
        private float glow = 1f, breath = 0f;
        private int ring = 0xFF3B3B42;
        private ValueAnimator breathing, flare;

        EyeView(Context c) {
            super(c);
            setLayerType(LAYER_TYPE_HARDWARE, null);
        }

        void setLevel(String level) {
            ring = "critical".equals(level) ? 0xFFFF2A1F : "warning".equals(level) ? 0xFFFBBF24 : 0xFF3B3B42;
            invalidate();
        }

        void start() {
            breathing = ValueAnimator.ofFloat(0f, 1f, 0f);
            breathing.setDuration(6000);
            breathing.setRepeatCount(ValueAnimator.INFINITE);
            breathing.addUpdateListener(a -> {
                breath = (float) a.getAnimatedValue();
                invalidate();
            });
            breathing.start();
        }

        void stop() {
            if (breathing != null) breathing.cancel();
            if (flare != null) flare.cancel();
        }

        void pulse() {
            if (flare != null) flare.cancel();
            flare = ValueAnimator.ofFloat(1.45f, 1f);
            flare.setDuration(220);
            flare.addUpdateListener(a -> {
                glow = (float) a.getAnimatedValue();
                invalidate();
            });
            flare.start();
        }

        private static int scale(int argb, float k) {
            int r = Math.min(255, Math.round(Color.red(argb) * k)), g = Math.min(255, Math.round(Color.green(argb) * k)),
                    b = Math.min(255, Math.round(Color.blue(argb) * k));
            return Color.argb(Color.alpha(argb), r, g, b);
        }

        @Override
        protected void onDraw(Canvas c) {
            float w = getWidth(), cx = w / 2f, cy = getHeight() / 2f, r = Math.min(w, getHeight()) / 2f * 0.86f;
            float k = glow * (1f + 0.15f * breath);
            // Glow around the eye
            p.setShader(new RadialGradient(cx, cy, r * 1.16f, new int[]{Color.argb(Math.round(Math.min(255, 110 * k)), 255, 42, 31), 0x00FF2A1F},
                    new float[]{0.6f, 1f}, Shader.TileMode.CLAMP));
            c.drawCircle(cx, cy, r * 1.16f, p);
            p.setShader(null);
            p.setColor(ring);
            c.drawCircle(cx, cy, r, p);
            p.setColor(0xFF0D0D0F);
            c.drawCircle(cx, cy, r * 0.95f, p);
            p.setColor(0xFF3A3A40);
            c.drawCircle(cx, cy, r * 0.92f, p);
            p.setColor(0xFF2A2A2E);
            c.drawCircle(cx, cy, r * 0.89f, p);
            float lens = r * 0.70f;
            p.setShader(new RadialGradient(cx, cy, lens,
                    new int[]{0xFFFFF6C8, scale(0xFFFFC14A, k), scale(0xFFFF3B1F, k), scale(0xFFC80E08, k), 0xFF5A0402, 0xFF140000, 0xFF000000},
                    new float[]{0f, 0.07f, 0.2f, 0.38f, 0.62f, 0.82f, 1f}, Shader.TileMode.CLAMP));
            c.drawCircle(cx, cy, lens, p);
            p.setShader(null);
        }
    }
}
