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
 * boxes for the PC (with its drives), the network, Uptime Kuma and the Raspberry Pi, an alert bar only when something
 * needs you, and HAL's record. HAL says his line
 * (or the message) when the screen opens; OK on the remote makes him talk again; Back closes it.
 */
public class HalActivity extends Activity {
    private static final long REFRESH_MS = 30_000L;
    private static final long LINE_MS = 3 * 60_000L;
    private static final long DRIFT_MS = 180_000L;

    private final Handler main = new Handler(Looper.getMainLooper());
    private ExecutorService io;
    private HomeExtras extras;
    private boolean running;

    private LinearLayout page;
    private EyeView eye;
    private TextView quote, weather, weatherSub, fx, record, alertTitle, alertMore;
    private LinearLayout alertBar;
    /** The four boxes: PC, network, Uptime Kuma, Raspberry Pi. */
    private Box pcBox, netBox, kumaBox, piBox;

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
        page.setPadding(dp(48), dp(24), dp(48), dp(24));
        page.setGravity(Gravity.CENTER_VERTICAL);
        root.addView(page, new FrameLayout.LayoutParams(-1, -1));

        // Top: eye, clock and date; weather and the exchange rate on the right.
        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);
        page.addView(top, new LinearLayout.LayoutParams(-1, -2));
        eye = new EyeView(this);
        top.addView(eye, new LinearLayout.LayoutParams(dp(92), dp(92)));
        LinearLayout clockBox = new LinearLayout(this);
        clockBox.setOrientation(LinearLayout.VERTICAL);
        LinearLayout.LayoutParams cbl = new LinearLayout.LayoutParams(0, -2, 1f);
        cbl.leftMargin = dp(24);
        top.addView(clockBox, cbl);
        TextClock clock = new TextClock(this);
        clock.setFormat12Hour("h:mm a");
        clock.setFormat24Hour("HH:mm");
        clock.setTextSize(TypedValue.COMPLEX_UNIT_SP, 60);
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
        weather = text(36, Color.WHITE, light);
        weather.setGravity(Gravity.END);
        weather.setSingleLine(true);
        side.addView(weather);
        weatherSub = text(12, 0xFFA1A1AA, UiFont.get(this, R.string.font_regular));
        weatherSub.setGravity(Gravity.END);
        weatherSub.setSingleLine(true);
        LinearLayout.LayoutParams wsl = new LinearLayout.LayoutParams(-2, -2);
        wsl.topMargin = dp(5);
        side.addView(weatherSub, wsl);
        fx = text(13, 0xFFA1A1AA, mono);
        fx.setGravity(Gravity.END);
        LinearLayout.LayoutParams fl = new LinearLayout.LayoutParams(-2, -2);
        fl.topMargin = dp(10);
        side.addView(fx, fl);

        quote = text(16, 0xFFD4D4D8, Typeface.create(regular, Typeface.ITALIC));
        quote.setLineSpacing(0, 1.15f);
        quote.setMaxLines(1);
        quote.setEllipsize(TextUtils.TruncateAt.END);
        quote.setText("“I'm reading the PC, Dave.”");
        LinearLayout.LayoutParams ql = new LinearLayout.LayoutParams(-1, -2);
        ql.topMargin = dp(20);
        page.addView(quote, ql);

        // Only when something needs you: the first alert, and how many more.
        alertBar = new LinearLayout(this);
        alertBar.setOrientation(LinearLayout.HORIZONTAL);
        alertBar.setGravity(Gravity.CENTER_VERTICAL);
        alertBar.setPadding(dp(14), dp(9), dp(14), dp(9));
        LinearLayout.LayoutParams abl = new LinearLayout.LayoutParams(-1, -2);
        abl.topMargin = dp(14);
        page.addView(alertBar, abl);
        alertTitle = text(12.5f, 0xFFFDE68A, regular);
        alertTitle.setSingleLine(true);
        alertTitle.setEllipsize(TextUtils.TruncateAt.END);
        alertBar.addView(alertTitle, new LinearLayout.LayoutParams(0, -2, 1f));
        alertMore = text(9.5f, 0xFFA1A1AA, mono);
        alertBar.addView(alertMore);
        alertBar.setVisibility(View.GONE);

        // The four boxes, side by side, rows spread evenly.
        LinearLayout boxes = new LinearLayout(this);
        boxes.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout.LayoutParams bxl = new LinearLayout.LayoutParams(-1, dp(232));
        bxl.topMargin = dp(22);
        page.addView(boxes, bxl);
        pcBox = new Box("THE PC", 1.15f, boxes, false);
        netBox = new Box("NETWORK", 1f, boxes, true);
        kumaBox = new Box("UPTIME KUMA", 1f, boxes, true);
        piBox = new Box("RASPBERRY PI", 1f, boxes, true);

        record = text(9.5f, 0xFF71717A, mono);
        record.setSingleLine(true);
        record.setEllipsize(TextUtils.TruncateAt.END);
        LinearLayout.LayoutParams rl2 = new LinearLayout.LayoutParams(-1, -2);
        rl2.topMargin = dp(16);
        page.addView(record, rl2);
        showLocal();
        return root;
    }

    /** Weather and the exchange rate, as Home last read them (kept on the TV). */
    private void showLocal() {
        // "☀  23°  ·  H 27°  L 14°" (Home's pill) -> "☀ 23°" big, and "High 27° · Low 14° · Sunny" under it.
        SharedPreferences w = getSharedPreferences("weather_now", MODE_PRIVATE);
        String pill = w.getString("pill", "").trim();
        String now = w.getString("now", "").trim();
        String[] parts = pill.split("\\s*\u00b7\\s*", 2);
        weather.setText(parts[0].replaceAll("\\s+", " "));
        String hl = parts.length > 1 ? parts[1].replaceAll("\\s+", " ").replace("H ", "High ").replace(" L ", " \u00b7 Low ") : "";
        String cond = now.split("\\s*\u00b7\\s*")[0];
        weatherSub.setText(hl.isEmpty() ? cond : cond.isEmpty() ? hl : hl + " \u00b7 " + cond);
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

        JSONObject sc = j.optJSONObject("screen");
        if (sc != null) {
            JSONObject pc = sc.optJSONObject("pc");
            if (pc != null) {
                pcBox.rows(pc.optJSONArray("rows"), pc.optString("level"), null);
                pcBox.drives(3, pc.optJSONArray("drives"));
            }
            JSONObject net = sc.optJSONObject("net");
            if (net != null) netBox.rows(net.optJSONArray("rows"), net.optString("level"), "ALL UP");
            JSONObject km = sc.optJSONObject("kuma");
            if (km != null) kumaBox.monitors(km);
            JSONObject pi = sc.optJSONObject("pi");
            if (pi != null) piBox.rows(pi.optJSONArray("rows"), pi.optString("level"), null);
            record.setText(sc.optString("record", "").isEmpty() ? "" : "HAL'S RECORD   " + sc.optString("record"));
            JSONArray al = sc.optJSONArray("alerts");
            if (al != null && al.length() > 0) {
                JSONObject a = al.optJSONObject(0);
                boolean crit = "critical".equals(a.optString("level"));
                int col = crit ? 0xFFFF6A4D : 0xFFFBBF24;
                SpannableString s = new SpannableString("●  " + a.optString("title") + "   " + a.optString("detail"));
                int titleEnd = 3 + a.optString("title").length();
                s.setSpan(new ForegroundColorSpan(col), 0, titleEnd, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                s.setSpan(new android.text.style.StyleSpan(Typeface.BOLD), 0, titleEnd, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                alertTitle.setText(s);
                alertMore.setText(al.length() > 1 ? "+" + (al.length() - 1) + " more in the app" : "");
                GradientDrawable bg = new GradientDrawable();
                bg.setColor(crit ? 0x1AFF2A1F : 0x14FBBF24);
                bg.setStroke(dp(1), crit ? 0x66FF6A4D : 0x59FBBF24);
                bg.setCornerRadius(dp(9));
                alertBar.setBackground(bg);
                alertBar.setVisibility(View.VISIBLE);
            } else {
                alertBar.setVisibility(View.GONE);
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

    // ---- the boxes ------------------------------------------------------------------------------

    private static int levelColor(String level) {
        return "critical".equals(level) ? 0xFFFF6A4D : "warning".equals(level) ? 0xFFFBBF24 : "info".equals(level) ? 0xFF60A5FA : 0xFF34D399;
    }

    /** One box: a title with a status in the corner, and six rows sharing the height evenly. */
    private class Box {
        final LinearLayout body;
        final TextView status;
        final LinearLayout[] rows = new LinearLayout[6];
        final TextView[] labels = new TextView[6], values = new TextView[6];
        final String okWord;

        Box(String title, float weight, LinearLayout parent, boolean gap) {
            okWord = "OK";
            LinearLayout box = new LinearLayout(HalActivity.this);
            box.setOrientation(LinearLayout.VERTICAL);
            box.setPadding(dp(15), dp(13), dp(15), dp(10));
            GradientDrawable bg = card();
            bg.setStroke(Math.max(1, dp(0.5f)), 0xFF17171C);
            box.setBackground(bg);
            LinearLayout.LayoutParams bl = new LinearLayout.LayoutParams(0, -1, weight);
            if (gap) bl.leftMargin = dp(11);
            parent.addView(box, bl);
            LinearLayout head = new LinearLayout(HalActivity.this);
            head.setOrientation(LinearLayout.HORIZONTAL);
            TextView k = key(title);
            head.addView(k, new LinearLayout.LayoutParams(0, -2, 1f));
            status = key("");
            head.addView(status);
            box.addView(head, new LinearLayout.LayoutParams(-1, -2));
            body = new LinearLayout(HalActivity.this);
            body.setOrientation(LinearLayout.VERTICAL);
            LinearLayout.LayoutParams bdl = new LinearLayout.LayoutParams(-1, 0, 1f);
            bdl.topMargin = dp(4);
            box.addView(body, bdl);
            Typeface regular = UiFont.get(HalActivity.this, R.string.font_regular);
            for (int i = 0; i < 6; i++) {
                if (i > 0) {
                    View line = new View(HalActivity.this);
                    line.setBackgroundColor(0xFF1A1A20);
                    body.addView(line, new LinearLayout.LayoutParams(-1, Math.max(1, dp(0.5f))));
                }
                LinearLayout r = new LinearLayout(HalActivity.this);
                r.setOrientation(LinearLayout.HORIZONTAL);
                r.setGravity(Gravity.CENTER_VERTICAL);
                body.addView(r, new LinearLayout.LayoutParams(-1, 0, 1f));
                TextView l = text(12.5f, 0xFFF4F4F5, regular);
                l.setSingleLine(true);
                r.addView(l, new LinearLayout.LayoutParams(-2, -2));
                View spring = new View(HalActivity.this);
                r.addView(spring, new LinearLayout.LayoutParams(0, 1, 1f));
                TextView v = text(10.5f, 0xFFA1A1AA, Typeface.MONOSPACE);
                v.setSingleLine(true);
                v.setEllipsize(TextUtils.TruncateAt.END);
                r.addView(v);
                rows[i] = r;
                labels[i] = l;
                values[i] = v;
            }
        }

        void setStatus(String level, String okText, String problemText) {
            boolean ok = level == null || level.isEmpty() || "ok".equals(level);
            status.setText("● " + (ok ? okText : problemText));
            status.setTextColor(levelColor(ok ? "ok" : level));
        }

        /** rows: [[label, value, level], ...] */
        void rows(JSONArray a, String level, String okText) {
            int problems = 0;
            for (int i = 0; i < 6; i++) {
                JSONArray row = a == null ? null : a.optJSONArray(i);
                rows[i].setVisibility(row == null ? View.INVISIBLE : View.VISIBLE);
                if (row == null) continue;
                String lv = row.optString(2, "ok");
                labels[i].setText(row.optString(0, ""));
                values[i].setText(row.optString(1, ""));
                values[i].setTextColor("ok".equals(lv) ? 0xFFA1A1AA : levelColor(lv));
                if (!"ok".equals(lv) && !"info".equals(lv)) problems++;
            }
            setStatus(level, okText == null ? okWord : okText, problems <= 1 ? "1 ALERT" : problems + " ALERTS");
        }

        /** The Drives row: a small bar and % per drive. */
        void drives(int index, JSONArray ds) {
            LinearLayout r = rows[index];
            while (r.getChildCount() > 2) r.removeViewAt(2);
            values[index].setText("");
            if (ds == null) return;
            for (int i = 0; i < ds.length(); i++) {
                JSONObject d = ds.optJSONObject(i);
                boolean warn = d.optBoolean("warn");
                int col = warn ? 0xFFFBBF24 : 0xFF34D399;
                TextView m = text(9.5f, 0xFFA1A1AA, Typeface.MONOSPACE);
                m.setText(d.optString("mount"));
                LinearLayout.LayoutParams ml = new LinearLayout.LayoutParams(-2, -2);
                ml.leftMargin = dp(i == 0 ? 0 : 9);
                r.addView(m, ml);
                FrameLayout bar = new FrameLayout(HalActivity.this);
                GradientDrawable track = new GradientDrawable();
                track.setColor(0xFF1F1F25);
                track.setCornerRadius(dp(2));
                bar.setBackground(track);
                View fill = new View(HalActivity.this);
                GradientDrawable fd = new GradientDrawable();
                fd.setColor(col);
                fd.setCornerRadius(dp(2));
                fill.setBackground(fd);
                int w = dp(22);
                bar.addView(fill, new FrameLayout.LayoutParams(Math.max(1, Math.round(w * Math.min(100, d.optInt("pct")) / 100f)), -1));
                LinearLayout.LayoutParams barl = new LinearLayout.LayoutParams(w, dp(4));
                barl.leftMargin = dp(3);
                r.addView(bar, barl);
                TextView pc = text(9.5f, warn ? 0xFFFBBF24 : 0xFFA1A1AA, Typeface.MONOSPACE);
                pc.setText(d.optInt("pct") + "%");
                LinearLayout.LayoutParams pl = new LinearLayout.LayoutParams(-2, -2);
                pl.leftMargin = dp(3);
                r.addView(pc, pl);
            }
        }

        /** Uptime Kuma: each monitor with a coloured dot and its response time. */
        void monitors(JSONObject km) {
            JSONArray ms = km.optJSONArray("monitors");
            boolean ok = km.optBoolean("ok");
            for (int i = 0; i < 6; i++) {
                JSONObject m = ms == null ? null : ms.optJSONObject(i);
                if (!ok && i == 0) {
                    rows[0].setVisibility(View.VISIBLE);
                    labels[0].setText(km.optBoolean("found") ? "Uptime Kuma" : "Not set up");
                    values[0].setText(km.optString("head"));
                    values[0].setTextColor(0xFFFBBF24);
                    continue;
                }
                rows[i].setVisibility(m == null || !ok ? View.INVISIBLE : View.VISIBLE);
                if (m == null || !ok) continue;
                String st = m.optString("status");
                int dot = "up".equals(st) ? 0xFF34D399 : "down".equals(st) ? 0xFFFF6A4D : 0xFFFBBF24;
                SpannableString s = new SpannableString("●  " + m.optString("name"));
                s.setSpan(new ForegroundColorSpan(dot), 0, 1, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                labels[i].setText(s);
                values[i].setText("up".equals(st) ? (m.isNull("ms") ? "up" : m.optInt("ms") + " ms") : st);
                values[i].setTextColor("up".equals(st) ? 0xFFA1A1AA : dot);
            }
            String lv = km.optString("level", "ok");
            status.setText("● " + km.optString("head").toUpperCase(Locale.US));
            status.setTextColor(ok ? levelColor(lv) : 0xFFFBBF24);
        }
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
