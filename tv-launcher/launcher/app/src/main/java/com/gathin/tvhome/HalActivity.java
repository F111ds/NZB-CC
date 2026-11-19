package com.gathin.tvhome;

import android.animation.ValueAnimator;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
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
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.speech.tts.Voice;
import android.text.SpannableString;
import android.text.Spanned;
import android.text.TextUtils;
import android.text.style.ForegroundColorSpan;
import android.util.Base64;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.FrameLayout;
import android.widget.ImageView;
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
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * NZB-CC: HAL 9000's screen, on demand. Opened from the HAL tile on Home, or by the PC (the phone app's
 * "Show HAL on the TV": adb am start, with an optional message to say in the extra "say_b64").
 *
 * <p>HAL's eye, the clock, weather and the exchange rate, HAL's line (a different one every few minutes), what just
 * arrived (posters) and what's coming up tonight, HAL's record, and live readings from the PC. HAL says his line
 * (or the message) when the screen opens; OK on the remote makes him talk again; Back closes it.
 */
public class HalActivity extends Activity {
    private static final long REFRESH_MS = 30_000L;
    private static final long HOME_REFRESH_MS = 120_000L;
    private static final long LINE_MS = 3 * 60_000L;
    private static final long DRIFT_MS = 180_000L;

    private final Handler main = new Handler(Looper.getMainLooper());
    private ExecutorService io;
    private HomeExtras extras;
    private boolean running;

    private LinearLayout page;
    private EyeView eye;
    private TextView quote, weather, fx, record, recordSub;
    private LinearLayout posters, upcoming;
    private TextView[][] tiles;

    private TextToSpeech tts;
    private boolean ttsReady, speaking;
    private String pendingSay = "";      // to say once the voice is ready
    private String lastSay = "";
    private String shownQuote = "";
    private JSONArray lines;             // HAL's lines from the PC, shown in turn
    private int lineAt;
    private String currentSay = "";      // what OK repeats
    private String voiceKey = "";

    @Override
    protected void attachBaseContext(Context base) {
        super.attachBaseContext(LauncherLocale.wrap(base));
    }

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON | WindowManager.LayoutParams.FLAG_FULLSCREEN);
        extras = HomeExtras.load(this);
        setContentView(build());
        ClockFormat.apply(getWindow().getDecorView(), Config.load(this).clockFormat);
        takeMessage(getIntent());
        tts = new TextToSpeech(this, status -> {
            if (status != TextToSpeech.SUCCESS || tts == null) return;
            applyVoice(null);
            tts.setOnUtteranceProgressListener(new Talk());
            ttsReady = true;
            main.post(() -> {
                if (!pendingSay.isEmpty()) say(pendingSay);
            });
        });
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        takeMessage(intent);
    }

    /** A message from the phone (say_b64), else HAL's own line once it has come in from the PC. */
    private void takeMessage(Intent intent) {
        String msg = "";
        String b64 = intent == null ? null : intent.getStringExtra("say_b64");
        if (b64 != null && !b64.isEmpty()) {
            try {
                msg = new String(Base64.decode(b64, Base64.DEFAULT), StandardCharsets.UTF_8).trim();
            } catch (IllegalArgumentException ignored) {
                msg = "";
            }
        }
        if (!msg.isEmpty()) {
            shownQuote = msg;
            quote.setText("“" + msg + "”");
            say(msg);
        } else {
            pendingSay = "__line__";   // HAL's line, as soon as the PC has answered
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        running = true;
        io = Executors.newSingleThreadExecutor();
        eye.start();
        refresh();
        refreshHome();
        main.postDelayed(this::nextLine, LINE_MS);
        main.postDelayed(this::drift, DRIFT_MS);
    }

    @Override
    protected void onPause() {
        running = false;
        main.removeCallbacksAndMessages(null);
        eye.stop();
        if (io != null) {
            final ExecutorService old = io;
            io = null;
            // Tell the PC the screen is closed (the phone app shows "Not showing"), then stop.
            old.execute(() -> {
                try {
                    get("/hal/api/brief?from=tv&closed=1", 3000);
                } catch (Exception ignored) {
                    // the PC may be off
                }
            });
            old.shutdown();
        }
        if (tts != null) tts.stop();
        speaking = false;
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        if (tts != null) {
            try {
                tts.shutdown();
            } catch (Exception ignored) {
                // gone
            }
            tts = null;
        }
        super.onDestroy();
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_DPAD_CENTER || keyCode == KeyEvent.KEYCODE_ENTER
                || keyCode == KeyEvent.KEYCODE_NUMPAD_ENTER || keyCode == KeyEvent.KEYCODE_BUTTON_A) {
            if (speaking && tts != null) tts.stop();
            say(currentSay.isEmpty() ? "__line__" : currentSay);
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    // ---- layout ---------------------------------------------------------------------------------

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

    private TextView key(String s) {
        TextView k = text(9, 0xFF71717A, Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        k.setLetterSpacing(0.18f);
        k.setText(s);
        return k;
    }

    private GradientDrawable card() {
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(0xFF0B0B0E);
        bg.setCornerRadius(dp(12));
        return bg;
    }

    private View build() {
        Typeface light = UiFont.get(this, R.string.font_light), regular = UiFont.get(this, R.string.font_regular);
        Typeface mono = Typeface.MONOSPACE;
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(56), dp(30), dp(56), dp(26));
        root.addView(page, new FrameLayout.LayoutParams(-1, -1));

        // Top: eye, clock and date; weather and the exchange rate on the right.
        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);
        page.addView(top, new LinearLayout.LayoutParams(-1, -2));
        eye = new EyeView(this);
        top.addView(eye, new LinearLayout.LayoutParams(dp(120), dp(120)));
        LinearLayout clockBox = new LinearLayout(this);
        clockBox.setOrientation(LinearLayout.VERTICAL);
        LinearLayout.LayoutParams cbl = new LinearLayout.LayoutParams(0, -2, 1f);
        cbl.leftMargin = dp(34);
        top.addView(clockBox, cbl);
        TextClock clock = new TextClock(this);
        clock.setFormat12Hour("h:mm a");
        clock.setFormat24Hour("HH:mm");
        clock.setTextSize(TypedValue.COMPLEX_UNIT_SP, 70);
        clock.setTextColor(Color.WHITE);
        clock.setTypeface(light);
        clock.setIncludeFontPadding(false);
        clockBox.addView(clock);
        TextClock date = new TextClock(this);
        String pattern = android.text.format.DateFormat.getBestDateTimePattern(Locale.getDefault(), "EEEEdMMMM");
        date.setFormat12Hour(pattern);
        date.setFormat24Hour(pattern);
        date.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        date.setTextColor(0xFFA1A1AA);
        date.setTypeface(regular);
        LinearLayout.LayoutParams dl = new LinearLayout.LayoutParams(-2, -2);
        dl.topMargin = dp(6);
        clockBox.addView(date, dl);
        LinearLayout side = new LinearLayout(this);
        side.setOrientation(LinearLayout.VERTICAL);
        side.setGravity(Gravity.END);
        top.addView(side, new LinearLayout.LayoutParams(-2, -2));
        weather = text(26, Color.WHITE, light);
        weather.setGravity(Gravity.END);
        side.addView(weather);
        fx = text(13, 0xFFA1A1AA, mono);
        fx.setGravity(Gravity.END);
        LinearLayout.LayoutParams fl = new LinearLayout.LayoutParams(-2, -2);
        fl.topMargin = dp(10);
        side.addView(fx, fl);

        quote = text(17, 0xFFD4D4D8, Typeface.create(regular, Typeface.ITALIC));
        quote.setLineSpacing(0, 1.15f);
        quote.setMaxLines(2);
        quote.setEllipsize(TextUtils.TruncateAt.END);
        quote.setText("“I'm reading the PC, Dave.”");
        LinearLayout.LayoutParams ql = new LinearLayout.LayoutParams(-1, -2);
        ql.topMargin = dp(18);
        page.addView(quote, ql);

        // Middle: just arrived (posters) | coming up tonight and HAL's record.
        LinearLayout mid = new LinearLayout(this);
        mid.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout.LayoutParams ml = new LinearLayout.LayoutParams(-1, 0, 1f);
        ml.topMargin = dp(16);
        page.addView(mid, ml);
        LinearLayout left = new LinearLayout(this);
        left.setOrientation(LinearLayout.VERTICAL);
        mid.addView(left, new LinearLayout.LayoutParams(0, -1, 1.2f));
        left.addView(key("JUST ARRIVED"));
        posters = new LinearLayout(this);
        posters.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout.LayoutParams pl = new LinearLayout.LayoutParams(-1, -2);
        pl.topMargin = dp(8);
        left.addView(posters, pl);
        LinearLayout right = new LinearLayout(this);
        right.setOrientation(LinearLayout.VERTICAL);
        LinearLayout.LayoutParams rl = new LinearLayout.LayoutParams(0, -1, 1f);
        rl.leftMargin = dp(28);
        mid.addView(right, rl);
        right.addView(key("COMING UP TONIGHT"));
        upcoming = new LinearLayout(this);
        upcoming.setOrientation(LinearLayout.VERTICAL);
        LinearLayout.LayoutParams ul = new LinearLayout.LayoutParams(-1, -2);
        ul.topMargin = dp(4);
        right.addView(upcoming, ul);
        View gap = new View(this);
        right.addView(gap, new LinearLayout.LayoutParams(-1, 0, 1f));
        LinearLayout rec = new LinearLayout(this);
        rec.setOrientation(LinearLayout.HORIZONTAL);
        rec.setGravity(Gravity.CENTER_VERTICAL);
        rec.setPadding(dp(14), dp(10), dp(14), dp(10));
        rec.setBackground(card());
        right.addView(rec, new LinearLayout.LayoutParams(-1, -2));
        LinearLayout recText = new LinearLayout(this);
        recText.setOrientation(LinearLayout.VERTICAL);
        rec.addView(recText, new LinearLayout.LayoutParams(0, -2, 1f));
        recText.addView(key("HAL'S RECORD"));
        recordSub = text(10, 0xFF71717A, mono);
        recordSub.setText("ads blocked");
        LinearLayout.LayoutParams rsl = new LinearLayout.LayoutParams(-2, -2);
        rsl.topMargin = dp(4);
        recText.addView(recordSub, rsl);
        record = text(22, Color.WHITE, Typeface.create(mono, Typeface.BOLD));
        record.setText("—");
        rec.addView(record);

        // Bottom: the six readings.
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout.LayoutParams rowl = new LinearLayout.LayoutParams(-1, -2);
        rowl.topMargin = dp(16);
        page.addView(row, rowl);
        String[] keys = {"CPU", "MEMORY", "INTERNET", "PI-HOLE", "UPTIME KUMA", "DRIVES"};
        tiles = new TextView[keys.length][2];
        for (int i = 0; i < keys.length; i++) {
            LinearLayout t = new LinearLayout(this);
            t.setOrientation(LinearLayout.VERTICAL);
            t.setPadding(dp(14), dp(11), dp(14), dp(11));
            t.setBackground(card());
            LinearLayout.LayoutParams tl = new LinearLayout.LayoutParams(0, dp(88), 1f);
            if (i > 0) tl.leftMargin = dp(10);
            row.addView(t, tl);
            t.addView(key(keys[i]));
            TextView v = text(21, Color.WHITE, Typeface.create(mono, Typeface.BOLD));
            v.setSingleLine(true);
            v.setText("—");
            LinearLayout.LayoutParams vl = new LinearLayout.LayoutParams(-2, -2);
            vl.topMargin = dp(8);
            t.addView(v, vl);
            TextView s = text(10, 0xFF71717A, mono);
            s.setSingleLine(true);
            s.setEllipsize(TextUtils.TruncateAt.END);
            LinearLayout.LayoutParams sl = new LinearLayout.LayoutParams(-2, -2);
            sl.topMargin = dp(5);
            t.addView(s, sl);
            tiles[i][0] = v;
            tiles[i][1] = s;
        }
        showLocal();
        return root;
    }

    /** Weather and the exchange rate, as Home last read them (kept on the TV). */
    private void showLocal() {
        String pill = getSharedPreferences("weather_now", MODE_PRIVATE).getString("pill", "");
        weather.setText(pill.trim().replaceAll("\\s+", " "));
        SharedPreferences p = getSharedPreferences("exchange_rate", MODE_PRIVATE);
        String pair = extras.fxFrom + ">" + extras.fxTo;
        double rate = Double.longBitsToDouble(p.getLong("rate", Double.doubleToLongBits(Double.NaN)));
        double change = Double.longBitsToDouble(p.getLong("change", Double.doubleToLongBits(Double.NaN)));
        if (!p.getString("pair", "").equals(pair) || Double.isNaN(rate)) {
            fx.setText("");
            return;
        }
        String text = extras.fxFrom + " → " + extras.fxTo + "  " + String.format(Locale.US, "%.2f", rate);
        if (Double.isNaN(change) || Math.abs(change) < 0.005) {
            fx.setText(text);
            return;
        }
        String arrow = String.format(Locale.US, "  %s %.2f", change > 0 ? "▲" : "▼", Math.abs(change));
        SpannableString s = new SpannableString(text + arrow);
        s.setSpan(new ForegroundColorSpan(change > 0 ? 0xFF34D399 : 0xFFFF8A80), text.length(), s.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        fx.setText(s);
    }

    // ---- data -----------------------------------------------------------------------------------

    private void run(Runnable r) {
        try {
            if (io != null) io.execute(r);
        } catch (java.util.concurrent.RejectedExecutionException ignored) {
            // closing
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

    private void refresh() {
        if (!running) return;
        run(() -> {
            JSONObject j = null;
            try {
                if (!extras.kodiServer.isEmpty())
                    j = new JSONObject(new String(get("/hal/api/brief?from=tv", 10000), StandardCharsets.UTF_8));
            } catch (Exception ignored) {
                j = null;
            }
            final JSONObject data = j;
            main.post(() -> {
                if (!running) return;
                show(data);
                main.postDelayed(this::refresh, REFRESH_MS);
            });
        });
    }

    /** Just arrived and coming up tonight, from the phone server's Home (with posters). */
    private void refreshHome() {
        if (!running) return;
        run(() -> {
            JSONObject j = null;
            try {
                if (!extras.kodiServer.isEmpty())
                    j = new JSONObject(new String(get("/api/home", 15000), StandardCharsets.UTF_8));
            } catch (Exception ignored) {
                j = null;
            }
            final JSONObject home = j;
            final Bitmap[] art = new Bitmap[3];
            JSONArray arrived = home == null ? null : home.optJSONArray("arrived");
            for (int i = 0; arrived != null && i < Math.min(3, arrived.length()); i++) {
                String ref = arrived.optJSONObject(i).optString("poster", "");
                if (!ref.startsWith("t:")) continue;
                try {
                    byte[] b = get("/api/tv/poster?id=" + java.net.URLEncoder.encode(ref.substring(2), "UTF-8"), 15000);
                    BitmapFactory.Options o = new BitmapFactory.Options();
                    o.inSampleSize = 2;
                    art[i] = BitmapFactory.decodeByteArray(b, 0, b.length, o);
                } catch (Exception ignored) {
                    art[i] = null;
                }
            }
            main.post(() -> {
                if (!running) return;
                if (home != null) showHome(home, art);
                main.postDelayed(this::refreshHome, HOME_REFRESH_MS);
            });
        });
    }

    private static String ago(String iso) {
        if (iso == null || iso.length() < 10) return "";
        try {
            SimpleDateFormat f = new SimpleDateFormat(iso.length() >= 19 ? "yyyy-MM-dd HH:mm:ss" : "yyyy-MM-dd", Locale.US);
            Date d = f.parse(iso.replace('T', ' ').substring(0, Math.min(19, iso.length())));
            if (d == null) return "";
            long h = (System.currentTimeMillis() - d.getTime()) / 3_600_000L;
            if (h < 1) return "just now";
            if (h < 24) return h + " h ago";
            long days = h / 24;
            return days == 1 ? "yesterday" : days + " days ago";
        } catch (Exception e) {
            return "";
        }
    }

    private void showHome(JSONObject home, Bitmap[] art) {
        posters.removeAllViews();
        JSONArray arrived = home.optJSONArray("arrived");
        Typeface regular = UiFont.get(this, R.string.font_regular);
        for (int i = 0; arrived != null && i < Math.min(3, arrived.length()); i++) {
            JSONObject a = arrived.optJSONObject(i);
            LinearLayout box = new LinearLayout(this);
            box.setOrientation(LinearLayout.VERTICAL);
            LinearLayout.LayoutParams bl = new LinearLayout.LayoutParams(dp(84), -2);
            if (i > 0) bl.leftMargin = dp(12);
            posters.addView(box, bl);
            ImageView img = new ImageView(this);
            img.setScaleType(ImageView.ScaleType.CENTER_CROP);
            img.setClipToOutline(true);
            GradientDrawable ph = card();
            ph.setColor(0xFF1A1F2B);
            img.setBackground(ph);
            if (art[i] != null) img.setImageBitmap(art[i]);
            box.addView(img, new LinearLayout.LayoutParams(dp(84), dp(126)));
            TextView t = text(11, Color.WHITE, regular);
            t.setSingleLine(true);
            t.setEllipsize(TextUtils.TruncateAt.END);
            t.setText(a.optString("title", ""));
            LinearLayout.LayoutParams tl = new LinearLayout.LayoutParams(-1, -2);
            tl.topMargin = dp(6);
            box.addView(t, tl);
            TextView s = text(9, 0xFF71717A, Typeface.MONOSPACE);
            s.setSingleLine(true);
            s.setText(ago(a.optString("added", "")));
            LinearLayout.LayoutParams sl = new LinearLayout.LayoutParams(-1, -2);
            sl.topMargin = dp(3);
            box.addView(s, sl);
        }
        if (posters.getChildCount() == 0) {
            TextView none = text(12, 0xFF71717A, regular);
            none.setText("Nothing new yet, Dave.");
            posters.addView(none);
        }

        upcoming.removeAllViews();
        JSONArray up = home.optJSONArray("upcoming");
        String today = new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(new Date());
        int shown = 0;
        for (int i = 0; up != null && i < up.length() && shown < 3; i++) {
            JSONObject u = up.optJSONObject(i);
            String when = u.optString("when", "");
            String local = when;
            String time = "";
            if (when.length() > 10) {   // an episode's air time, in UTC
                try {
                    SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US);
                    f.setTimeZone(TimeZone.getTimeZone("UTC"));
                    Date d = f.parse(when.substring(0, 19));
                    if (d != null) {
                        local = new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(d);
                        time = android.text.format.DateFormat.getTimeFormat(this).format(d);
                    }
                } catch (Exception ignored) {
                    // keep the date as it is
                }
            }
            if (!local.startsWith(today)) continue;
            String title = u.optString("title", "");
            if ("episode".equals(u.optString("kind"))) title += String.format(Locale.US, " S%d E%d", u.optInt("season"), u.optInt("episode"));
            if (time.isEmpty()) time = u.optString("release", "").toLowerCase(Locale.US);
            LinearLayout r = new LinearLayout(this);
            r.setOrientation(LinearLayout.HORIZONTAL);
            r.setPadding(0, dp(7), 0, dp(7));
            TextView a = text(13, Color.WHITE, regular);
            a.setSingleLine(true);
            a.setEllipsize(TextUtils.TruncateAt.END);
            a.setText(title);
            r.addView(a, new LinearLayout.LayoutParams(0, -2, 1f));
            TextView b = text(10, 0xFF71717A, Typeface.MONOSPACE);
            b.setText(time);
            r.addView(b);
            upcoming.addView(r);
            shown++;
        }
        if (shown == 0) {
            TextView none = text(12, 0xFF71717A, regular);
            none.setPadding(0, dp(7), 0, 0);
            none.setText("Nothing scheduled for tonight.");
            upcoming.addView(none);
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
        showLocal();
        if (j == null) {
            eye.setLevel("");
            if (!speaking) quote.setText("“I can't reach the PC right now. I'll keep trying.”");
            return;
        }
        eye.setLevel(j.optString("level", ""));
        lines = j.optJSONArray("lines");
        if (lineAt == 0 || shownQuote.isEmpty()) setLine(j.optString("say", ""), j.optString("greeting", "") + " " + j.optString("quote", ""));

        int white = Color.WHITE, amber = 0xFFFBBF24, red = 0xFFFF6A4D, green = 0xFF34D399;
        tile(0, pct(j, "cpu"), j.isNull("cpu_temp") ? "" : Math.round(j.optDouble("cpu_temp")) + "°C", white);
        if (j.isNull("mem_total")) tile(1, pct(j, "ram"), "", white);
        else {
            double used = j.optDouble("mem_used") / 1073741824.0, total = j.optDouble("mem_total") / 1073741824.0;
            tile(1, pct(j, "ram"), String.format(Locale.US, "%.1f of %.0f GB", used, total), white);
        }
        if (j.optBoolean("internet_down")) tile(2, "DOWN", "no internet", red);
        else tile(2, j.isNull("ping") ? "—" : Math.round(j.optDouble("ping")) + " ms",
                j.isNull("net_down") ? "" : size(j.optDouble("net_down")) + "/s down", white);
        if (j.isNull("ads")) tile(3, "—", "not connected", white);
        else tile(3, String.format(Locale.getDefault(), "%,d", j.optLong("ads")),
                j.optBoolean("ads_on", true) ? "blocked today" : "blocking paused", j.optBoolean("ads_on", true) ? white : amber);
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
        JSONObject rec = j.optJSONObject("record");
        if (rec != null && rec.has("ads")) {
            record.setText(String.format(Locale.getDefault(), "%,d", rec.optLong("ads")));
            String since = rec.optString("since", "");
            try {
                Date d = new SimpleDateFormat("yyyy-MM-dd", Locale.US).parse(since);
                if (d != null) recordSub.setText("ads blocked since " + new SimpleDateFormat("MMMM", Locale.getDefault()).format(d));
            } catch (Exception ignored) {
                recordSub.setText("ads blocked");
            }
        }
        if (ttsReady) applyVoice(j.optJSONObject("voice"));
        if ("__line__".equals(pendingSay) && !currentSay.isEmpty()) say("__line__");
    }

    /** Shows a line, and remembers how to say it (OK repeats it). */
    private void setLine(String say, String text) {
        shownQuote = text.trim();
        currentSay = say.isEmpty() ? shownQuote : say;
        if (!speaking) quote.setText("“" + shownQuote + "”");
    }

    /** A different HAL line every few minutes (the PC sends a handful). */
    private void nextLine() {
        if (!running) return;
        if (lines != null && lines.length() > 0) {
            lineAt = (lineAt + 1) % lines.length();
            JSONObject l = lines.optJSONObject(lineAt);
            if (l != null) setLine(l.optString("say", ""), l.optString("text", ""));
        }
        main.postDelayed(this::nextLine, LINE_MS);
    }

    // ---- talking --------------------------------------------------------------------------------

    private void applyVoice(JSONObject cfg) {
        if (tts == null) return;
        float pitch = cfg != null ? (float) cfg.optDouble("pitch", 0.55) : 0.55f;
        float rate = cfg != null ? (float) cfg.optDouble("rate", 0.72) : 0.72f;
        String[] prefer = {"en-gb-x-rjs", "en-gb-x-gbd", "en-gb-x-gbb", "en-us-x-iom", "en-us-x-tpd", "en-us-x-iol", "en-au-x-aud", "en-au-x-aub"};
        JSONArray pa = cfg != null ? cfg.optJSONArray("prefer") : null;
        if (pa != null && pa.length() > 0) {
            prefer = new String[pa.length()];
            for (int i = 0; i < pa.length(); i++) prefer[i] = pa.optString(i, "").toLowerCase(Locale.US);
        }
        String key = pitch + "|" + rate + "|" + String.join(",", prefer);
        if (key.equals(voiceKey)) return;
        voiceKey = key;
        tts.setPitch(Math.max(0.3f, Math.min(1.5f, pitch)));
        tts.setSpeechRate(Math.max(0.4f, Math.min(1.5f, rate)));
        try {
            Voice best = null;
            int bestScore = Integer.MIN_VALUE;
            for (Voice v : tts.getVoices()) {
                Locale l = v.getLocale();
                if (l == null || !"en".equals(l.getLanguage())) continue;
                if (v.getFeatures() != null && v.getFeatures().contains(TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED)) continue;
                String n = v.getName().toLowerCase(Locale.US);
                int s = 0;
                for (int i = 0; i < prefer.length; i++) {
                    if (!prefer[i].isEmpty() && n.startsWith(prefer[i])) {
                        s += 1000 - i * 10;
                        break;
                    }
                }
                if (n.contains("male") && !n.contains("female")) s += 100;
                if (!v.isNetworkConnectionRequired()) s += 5;
                if ("GB".equals(l.getCountry())) s += 3;
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

    /** Says a message, or HAL's own line ("__line__"); waits for the voice and the PC if they aren't ready. */
    private void say(String what) {
        if ("__line__".equals(what)) {
            if (currentSay.isEmpty()) {
                pendingSay = "__line__";
                return;
            }
            what = currentSay;
            quote.setText("“" + shownQuote + "”");
        }
        if (!ttsReady || tts == null) {
            pendingSay = what;
            return;
        }
        pendingSay = "";
        lastSay = what;
        Bundle b = new Bundle();
        b.putInt(TextToSpeech.Engine.KEY_PARAM_STREAM, android.media.AudioManager.STREAM_MUSIC);
        b.putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, 1f);
        speaking = true;
        reveal(0f);
        if (tts.speak(what, TextToSpeech.QUEUE_FLUSH, b, "hal") != TextToSpeech.SUCCESS) speaking = false;
    }

    private void reveal(float fraction) {
        String q = "“" + shownQuote + "”";
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
                eye.pulse();
                reveal(f);
            });
        }

        @Override
        public void onDone(String id) {
            main.post(() -> {
                speaking = false;
                quote.setText("“" + shownQuote + "”");
            });
        }

        @Override
        public void onError(String id) {
            onDone(id);
        }
    }

    // ---- burn-in care: everything drifts a little ---------------------------------------------------

    private void drift() {
        if (!running) return;
        page.animate().translationX(dp((float) (Math.random() * 30 - 15))).translationY(dp((float) (Math.random() * 16 - 8))).setDuration(4000).start();
        main.postDelayed(this::drift, DRIFT_MS);
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
            if (breathing != null) breathing.cancel();
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
