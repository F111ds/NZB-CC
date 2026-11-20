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
 * <p>HAL's eye in the middle with his line (and an alert under it when something needs you), boxes for the PC and the
 * network on the left and Uptime Kuma and the Raspberry Pi on the right, and what SABnzbd is doing along the bottom.
 * HAL says his line
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
    private TextView quote, alertTitle, alertMore, dlKey, dlTitle, dlInfo;
    private LinearLayout dlStrip;
    private LinearLayout details;
    private JSONArray detailPages;
    private int detailAt;
    private FrameLayout dlBar;
    private View dlFill;
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
        if (detailsOpen()) closeDetails();
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
        // Details: Down opens them, Left/Right go between the pages, Back (or Up) returns to HAL.
        if (detailsOpen()) {
            if (keyCode == KeyEvent.KEYCODE_BACK || keyCode == KeyEvent.KEYCODE_DPAD_UP) {
                closeDetails();
                return true;
            }
            if (keyCode == KeyEvent.KEYCODE_DPAD_RIGHT) {
                showDetail(detailAt + 1);
                return true;
            }
            if (keyCode == KeyEvent.KEYCODE_DPAD_LEFT) {
                showDetail(detailAt - 1);
                return true;
            }
        } else if (keyCode == KeyEvent.KEYCODE_DPAD_DOWN) {
            openDetails();
            return true;
        }
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
        TextView k = text(10, 0xFF8B8B94, Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
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
        Typeface regular = UiFont.get(this, R.string.font_regular);
        Typeface mono = Typeface.MONOSPACE;
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(40), dp(26), dp(40), dp(22));
        root.addView(page, new FrameLayout.LayoutParams(-1, -1));
        details = new LinearLayout(this);
        details.setOrientation(LinearLayout.VERTICAL);
        details.setPadding(dp(40), dp(24), dp(40), dp(16));
        details.setBackgroundColor(Color.BLACK);
        details.setVisibility(View.GONE);
        root.addView(details, new FrameLayout.LayoutParams(-1, -1));

        // Boxes left and right, HAL's eye in the middle.
        LinearLayout middle = new LinearLayout(this);
        middle.setOrientation(LinearLayout.HORIZONTAL);
        middle.setGravity(Gravity.CENTER_VERTICAL);
        page.addView(middle, new LinearLayout.LayoutParams(-1, 0, 1f));
        LinearLayout left = column(middle);
        LinearLayout centre = new LinearLayout(this);
        centre.setOrientation(LinearLayout.VERTICAL);
        centre.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams cl = new LinearLayout.LayoutParams(dp(250), -1);
        cl.leftMargin = dp(18);
        cl.rightMargin = dp(18);
        middle.addView(centre, cl);
        LinearLayout right = column(middle);

        eye = new EyeView(this);
        centre.addView(eye, new LinearLayout.LayoutParams(dp(196), dp(196)));
        quote = text(14.5f, 0xFFD4D4D8, Typeface.create(regular, Typeface.ITALIC));
        quote.setLineSpacing(0, 1.2f);
        quote.setGravity(Gravity.CENTER);
        quote.setMaxLines(5);
        quote.setEllipsize(TextUtils.TruncateAt.END);
        quote.setText("\u201cI'm reading the PC, Dave.\u201d");
        LinearLayout.LayoutParams ql = new LinearLayout.LayoutParams(-1, -2);
        ql.topMargin = dp(22);
        centre.addView(quote, ql);
        // Only when something needs you: the first alert under HAL's line, and how many more.
        alertBar = new LinearLayout(this);
        alertBar.setOrientation(LinearLayout.VERTICAL);
        alertBar.setGravity(Gravity.CENTER_HORIZONTAL);
        alertBar.setPadding(dp(12), dp(8), dp(12), dp(8));
        LinearLayout.LayoutParams abl = new LinearLayout.LayoutParams(-1, -2);
        abl.topMargin = dp(14);
        centre.addView(alertBar, abl);
        alertTitle = text(12, 0xFFFDE68A, regular);
        alertTitle.setGravity(Gravity.CENTER);
        alertTitle.setMaxLines(3);
        alertTitle.setEllipsize(TextUtils.TruncateAt.END);
        alertBar.addView(alertTitle, new LinearLayout.LayoutParams(-1, -2));
        alertMore = text(9, 0xFFA1A1AA, mono);
        alertMore.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams aml = new LinearLayout.LayoutParams(-1, -2);
        aml.topMargin = dp(4);
        alertBar.addView(alertMore, aml);
        alertBar.setVisibility(View.GONE);

        pcBox = new Box("THE PC", left, false);
        netBox = new Box("NETWORK", left, true);
        kumaBox = new Box("UPTIME KUMA", right, false);
        piBox = new Box("RASPBERRY PI", right, true);

        // Along the bottom: what SABnzbd is doing.
        dlStrip = new LinearLayout(this);
        dlStrip.setOrientation(LinearLayout.HORIZONTAL);
        dlStrip.setGravity(Gravity.CENTER_VERTICAL);
        dlStrip.setPadding(dp(14), dp(9), dp(14), dp(9));
        GradientDrawable dbg = card();
        dbg.setStroke(Math.max(1, dp(0.5f)), 0xFF17171C);
        dlStrip.setBackground(dbg);
        LinearLayout.LayoutParams dsl = new LinearLayout.LayoutParams(-1, -2);
        dsl.topMargin = dp(14);
        page.addView(dlStrip, dsl);
        dlKey = key("DOWNLOADS");
        dlStrip.addView(dlKey);
        dlTitle = text(13, 0xFFE4E4E7, regular);
        dlTitle.setSingleLine(true);
        dlTitle.setEllipsize(TextUtils.TruncateAt.END);
        LinearLayout.LayoutParams dtl = new LinearLayout.LayoutParams(-2, -2);
        dtl.leftMargin = dp(12);
        dlStrip.addView(dlTitle, dtl);
        dlBar = new FrameLayout(this);
        GradientDrawable track = new GradientDrawable();
        track.setColor(0xFF1F1F25);
        track.setCornerRadius(dp(3));
        dlBar.setBackground(track);
        dlFill = new View(this);
        GradientDrawable fd = new GradientDrawable();
        fd.setColor(0xFFFF6A4D);
        fd.setCornerRadius(dp(3));
        dlFill.setBackground(fd);
        dlBar.addView(dlFill, new FrameLayout.LayoutParams(0, -1));
        LinearLayout.LayoutParams dbl = new LinearLayout.LayoutParams(dp(110), dp(5));
        dbl.leftMargin = dp(12);
        dlStrip.addView(dlBar, dbl);
        View spring = new View(this);
        dlStrip.addView(spring, new LinearLayout.LayoutParams(0, 1, 1f));
        dlInfo = text(11, 0xFFA1A1AA, mono);
        dlInfo.setSingleLine(true);
        dlStrip.addView(dlInfo);
        dlBar.setVisibility(View.GONE);
        TextView hint = text(11, 0xFF8B8B94, mono);
        hint.setGravity(Gravity.CENTER);
        hint.setText("\u25BC  details     OK  HAL talks     BACK  close");
        LinearLayout.LayoutParams hl = new LinearLayout.LayoutParams(-1, -2);
        hl.topMargin = dp(8);
        page.addView(hint, hl);
        return root;
    }

    /** A column of two boxes beside HAL's eye. */
    private LinearLayout column(LinearLayout parent) {
        LinearLayout c = new LinearLayout(this);
        c.setOrientation(LinearLayout.VERTICAL);
        parent.addView(c, new LinearLayout.LayoutParams(0, -1, 1f));
        return c;
    }

    /** The downloads strip: what's downloading (with a bar), or that nothing is. */
    private void showDownloads(JSONObject d) {
        if (d == null) return;
        String st = d.optString("state");
        boolean busy = "downloading".equals(st);
        dlKey.setText(busy ? "DOWNLOADING" : "DOWNLOADS");
        dlTitle.setText(d.optString("title"));
        dlTitle.setTextColor("problem".equals(st) || "paused".equals(st) ? 0xFFFBBF24 : 0xFFE4E4E7);
        dlInfo.setText(d.optString("info"));
        dlBar.setVisibility(busy ? View.VISIBLE : View.GONE);
        if (busy) {
            final int pct = Math.max(0, Math.min(100, d.optInt("pct")));
            dlBar.post(() -> {
                FrameLayout.LayoutParams lp = (FrameLayout.LayoutParams) dlFill.getLayoutParams();
                lp.width = Math.round(dlBar.getWidth() * pct / 100f);
                dlFill.setLayoutParams(lp);
            });
        }
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

    private long lastOk;   // when the PC last answered

    private void show(JSONObject j) {
        if (j == null) {
            eye.setLevel("");
            if (!speaking) quote.setText("“I can't reach the PC right now. I'll keep trying.”");
            if (lastOk > 0) {
                long min = Math.max(1, (System.currentTimeMillis() - lastOk) / 60_000L);
                String seen = "LAST SEEN " + (min < 60 ? min + " MIN" : (min / 60) + " H") + " AGO";
                for (Box b : new Box[]{pcBox, netBox, kumaBox, piBox}) b.stale(seen);
                dlStrip.setAlpha(0.38f);
                dlKey.setText("DOWNLOADS");
                dlTitle.setText("Waiting for the PC");
                dlTitle.setTextColor(0xFFE4E4E7);
                dlInfo.setText(seen.toLowerCase(Locale.US));
                dlBar.setVisibility(View.GONE);
            }
            alertBar.setVisibility(View.GONE);
            return;
        }
        lastOk = System.currentTimeMillis();
        for (Box b : new Box[]{pcBox, netBox, kumaBox, piBox}) b.root.setAlpha(1f);
        dlStrip.setAlpha(1f);
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
            showDownloads(sc.optJSONObject("downloads"));
            JSONArray al = sc.optJSONArray("alerts");
            if (al != null && al.length() > 0) {
                JSONObject a = al.optJSONObject(0);
                boolean crit = "critical".equals(a.optString("level"));
                int col = crit ? 0xFFFF6A4D : 0xFFFBBF24;
                SpannableString s = new SpannableString("\u25CF  " + a.optString("title") + "\n" + a.optString("detail"));
                int titleEnd = 3 + a.optString("title").length();
                s.setSpan(new ForegroundColorSpan(col), 0, titleEnd, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                s.setSpan(new android.text.style.StyleSpan(Typeface.BOLD), 0, titleEnd, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                alertTitle.setText(s);
                alertMore.setText(al.length() > 1 ? "+" + (al.length() - 1) + " more in the app" : "");
                alertMore.setVisibility(al.length() > 1 ? View.VISIBLE : View.GONE);
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

    // ---- details: four pages of everything ------------------------------------------------------

    private static final long DETAILS_REFRESH_MS = 15_000L;
    private static final String[] TABS = {"THE PC", "NETWORK", "UPTIME KUMA", "RASPBERRY PI", "DOWNLOADS"};

    private int pageCount() {
        return detailPages != null && detailPages.length() > 0 ? Math.min(TABS.length, detailPages.length()) : TABS.length - 1;
    }

    private boolean detailsOpen() {
        return details != null && details.getVisibility() == View.VISIBLE;
    }

    private void openDetails() {
        details.setVisibility(View.VISIBLE);
        page.setVisibility(View.INVISIBLE);
        showDetail(detailAt);
        loadDetails();
    }

    private void closeDetails() {
        details.setVisibility(View.GONE);
        page.setVisibility(View.VISIBLE);
        main.removeCallbacks(detailsTick);
    }

    private final Runnable detailsTick = this::loadDetails;

    private void loadDetails() {
        main.removeCallbacks(detailsTick);
        if (!running || !detailsOpen()) return;
        run(() -> {
            JSONObject j = null;
            try {
                if (!extras.kodiServer.isEmpty())
                    j = new JSONObject(new String(get("/hal/api/tv/details", 15000), StandardCharsets.UTF_8));
            } catch (Exception ignored) {
                j = null;
            }
            final JSONObject data = j;
            main.post(() -> {
                if (!running || !detailsOpen()) return;
                if (data != null) {
                    detailPages = data.optJSONArray("pages");
                    showDetail(detailAt);
                }
                main.postDelayed(detailsTick, DETAILS_REFRESH_MS);
            });
        });
    }

    private void showDetail(int index) {
        int n = pageCount();
        detailAt = ((index % n) + n) % n;
        details.removeAllViews();
        Typeface light = UiFont.get(this, R.string.font_light), regular = UiFont.get(this, R.string.font_regular);
        JSONObject pg = detailPages == null ? null : detailPages.optJSONObject(detailAt);

        LinearLayout head = new LinearLayout(this);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        details.addView(head, new LinearLayout.LayoutParams(-1, -2));
        EyeView small = new EyeView(this);
        small.setLevel(pg == null ? "" : pg.optString("level"));
        head.addView(small, new LinearLayout.LayoutParams(dp(38), dp(38)));
        TextView title = text(26, Color.WHITE, light);
        title.setText(pg == null ? TABS[detailAt].charAt(0) + TABS[detailAt].substring(1).toLowerCase(Locale.US) : pg.optString("title"));
        LinearLayout.LayoutParams tl = new LinearLayout.LayoutParams(-2, -2);
        tl.leftMargin = dp(14);
        head.addView(title, tl);
        TextView status = key(pg == null ? "LOADING\u2026" : "\u25CF " + pg.optString("head"));
        status.setTextSize(TypedValue.COMPLEX_UNIT_SP, 10);
        status.setTextColor(pg == null ? 0xFF71717A : levelColor(pg.optString("level")));
        LinearLayout.LayoutParams sl = new LinearLayout.LayoutParams(-2, -2);
        sl.leftMargin = dp(14);
        head.addView(status, sl);
        View spring = new View(this);
        head.addView(spring, new LinearLayout.LayoutParams(0, 1, 1f));
        for (int i = 0; i < n; i++) {
            TextView tab = key(TABS[i]);
            tab.setTextSize(TypedValue.COMPLEX_UNIT_SP, 9.5f);
            tab.setPadding(dp(9), dp(5), dp(9), dp(5));
            if (i == detailAt) {
                tab.setTextColor(Color.WHITE);
                GradientDrawable on = new GradientDrawable();
                on.setColor(0xFF1A1A20);
                on.setCornerRadius(dp(6));
                tab.setBackground(on);
            }
            head.addView(tab);
        }

        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout.LayoutParams bl = new LinearLayout.LayoutParams(-1, 0, 1f);
        bl.topMargin = dp(16);
        details.addView(body, bl);
        JSONArray cols = pg == null ? null : pg.optJSONArray("cols");
        JSONArray grid = pg == null ? null : pg.optJSONArray("grid");
        if (grid != null && grid.length() > 0) {
            // Rows of cards (newer PC): each row shares the height, each card its row's width by "weight".
            body.setOrientation(LinearLayout.VERTICAL);
            bl.topMargin = dp(12);
            body.setLayoutParams(bl);
            for (int r = 0; r < grid.length(); r++) {
                JSONArray cards = grid.optJSONArray(r);
                if (cards == null) continue;
                LinearLayout row = new LinearLayout(this);
                row.setOrientation(LinearLayout.HORIZONTAL);
                LinearLayout.LayoutParams rl = new LinearLayout.LayoutParams(-1, 0, 1f);
                if (r > 0) rl.topMargin = dp(9);
                body.addView(row, rl);
                for (int c = 0; c < cards.length(); c++) {
                    JSONObject card = cards.optJSONObject(c);
                    if (card == null) continue;
                    boolean old = card.has("monitors") || card.has("queue");
                    LinearLayout.LayoutParams cl = new LinearLayout.LayoutParams(0, -1, (float) card.optDouble("weight", 1));
                    if (c > 0) cl.leftMargin = dp(9);
                    row.addView(old ? detailColumn(card) : gridCard(card), cl);
                }
            }
        } else if (cols == null) {
            TextView wait = text(14, 0xFF71717A, regular);
            wait.setGravity(Gravity.CENTER);
            wait.setText("I'm reading the details, Dave\u2026");
            body.addView(wait, new LinearLayout.LayoutParams(-1, -1));
        } else {
            for (int c = 0; c < cols.length(); c++) body.addView(detailColumn(cols.optJSONObject(c)), colParams(c));
        }

        TextView hint = text(11, 0xFF8B8B94, Typeface.MONOSPACE);
        hint.setGravity(Gravity.CENTER);
        hint.setText("\u25C0 \u25B6  other pages      OK  HAL talks      BACK  return to HAL");
        LinearLayout.LayoutParams hl = new LinearLayout.LayoutParams(-1, -2);
        hl.topMargin = dp(10);
        details.addView(hint, hl);
    }

    /** A card on a grid page: heading, big number with a small line after it, a graph that takes the spare height,
     *  core bars, rows (with an optional bar), drives (two to a line), hourly bars, chips, and a footer graph. */
    private View gridCard(JSONObject col) {
        Typeface light = UiFont.get(this, R.string.font_light), regular = UiFont.get(this, R.string.font_regular);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(12), dp(10), dp(12), dp(9));
        GradientDrawable bg = card();
        bg.setStroke(Math.max(1, dp(0.5f)), 0xFF1C1C22);
        box.setBackground(bg);
        box.addView(cardHead(col.optString("key"), col.optString("badge")), new LinearLayout.LayoutParams(-1, -2));
        if (!col.optString("big").isEmpty()) {
            LinearLayout line = new LinearLayout(this);
            line.setOrientation(LinearLayout.HORIZONTAL);
            line.setBaselineAligned(true);
            TextView big = text(29, Color.WHITE, light);
            big.setSingleLine(true);
            big.setText(col.optString("big"));
            line.addView(big);
            if (!col.optString("bigsub").isEmpty()) {
                TextView sub = text(11, 0xFFA1A1AA, Typeface.MONOSPACE);
                sub.setSingleLine(true);
                sub.setEllipsize(TextUtils.TruncateAt.END);
                sub.setText(col.optString("bigsub"));
                LinearLayout.LayoutParams sl = new LinearLayout.LayoutParams(-2, -2);
                sl.leftMargin = dp(7);
                line.addView(sub, sl);
            }
            LinearLayout.LayoutParams ll = new LinearLayout.LayoutParams(-1, -2);
            ll.topMargin = dp(6);
            box.addView(line, ll);
        }
        JSONArray graph = col.optJSONArray("graph");
        JSONArray hourly = col.optJSONArray("hourly");
        JSONArray footer = col.optJSONArray("footer_graph");
        boolean flexGraph = graph != null && graph.length() > 1;
        boolean flexHourly = !flexGraph && hourly != null && hourly.length() > 0;
        boolean rowsFill = !flexGraph && !flexHourly && (footer == null || footer.length() < 2);
        if (flexGraph) {
            LinearLayout.LayoutParams gl = new LinearLayout.LayoutParams(-1, 0, 1f);
            gl.topMargin = dp(7);
            box.addView(new GraphView(this, graph, colorOf(col.optString("graph_color"), 0xFFFF6A4D))
                    .second(col.optJSONArray("graph2"), colorOf(col.optString("graph2_color"), 0xFFA78BFA))
                    .fit(col.optBoolean("graph_fit")), gl);
            JSONArray legend = col.optJSONArray("legend");
            if (legend != null && legend.length() > 0) {
                LinearLayout lg = new LinearLayout(this);
                lg.setOrientation(LinearLayout.HORIZONTAL);
                for (int i = 0; i < legend.length(); i++) {
                    JSONArray it = legend.optJSONArray(i);
                    if (it == null) continue;
                    TextView sw = text(9.5f, colorOf(it.optString(1), 0xFF8B8B94), Typeface.MONOSPACE);
                    sw.setText("\u25A0 ");
                    lg.addView(sw);
                    TextView lt = text(9.5f, 0xFF8B8B94, Typeface.MONOSPACE);
                    lt.setText(it.optString(0) + "    ");
                    lg.addView(lt);
                }
                LinearLayout.LayoutParams lgl = new LinearLayout.LayoutParams(-2, -2);
                lgl.topMargin = dp(3);
                box.addView(lg, lgl);
            }
        }
        JSONArray cores = col.optJSONArray("cores");
        if (cores != null && cores.length() > 0) {
            LinearLayout.LayoutParams cl = new LinearLayout.LayoutParams(-1, dp(15));
            cl.topMargin = dp(5);
            box.addView(new CoresView(this, cores), cl);
        }
        JSONArray drives = col.optJSONArray("drives");
        if (drives != null && drives.length() > 0) {
            LinearLayout pair = null;
            for (int i = 0; i < drives.length(); i++) {
                if (i % 2 == 0) {
                    pair = new LinearLayout(this);
                    pair.setOrientation(LinearLayout.HORIZONTAL);
                    LinearLayout.LayoutParams pl = new LinearLayout.LayoutParams(-1, -2);
                    pl.topMargin = dp(8);
                    box.addView(pair, pl);
                }
                LinearLayout.LayoutParams dl = new LinearLayout.LayoutParams(0, -2, 1f);
                if (i % 2 == 1) dl.leftMargin = dp(14);
                pair.addView(driveView(drives.optJSONObject(i)), dl);
                if (i % 2 == 0 && i == drives.length() - 1) pair.addView(new View(this), new LinearLayout.LayoutParams(0, 1, 1f));
            }
        }
        JSONArray rows = col.optJSONArray("rows");
        for (int i = 0; rows != null && i < rows.length(); i++) {
            JSONArray row = rows.optJSONArray(i);
            if (row == null) continue;
            String lv = row.optString(2, "ok");
            LinearLayout r = new LinearLayout(this);
            r.setOrientation(LinearLayout.HORIZONTAL);
            r.setGravity(Gravity.CENTER_VERTICAL);
            TextView l = text(11.5f, 0xFFF4F4F5, regular);
            l.setSingleLine(true);
            l.setEllipsize(TextUtils.TruncateAt.END);
            l.setText(row.optString(0));
            r.addView(l, new LinearLayout.LayoutParams(0, -2, 1f));
            if (row.length() > 3) {
                final int pct = Math.max(0, Math.min(100, row.optInt(3)));
                LinearLayout.LayoutParams bpl = new LinearLayout.LayoutParams(dp(56), dp(4));
                bpl.leftMargin = dp(8);
                r.addView(barView(pct, "ok".equals(lv) ? 0xFF34D399 : levelColor(lv)), bpl);
            }
            TextView v = text(10.5f, "ok".equals(lv) ? 0xFFD4D4D8 : levelColor(lv), Typeface.MONOSPACE);
            v.setSingleLine(true);
            v.setText(row.optString(1));
            LinearLayout.LayoutParams vl = new LinearLayout.LayoutParams(-2, -2);
            vl.leftMargin = dp(8);
            r.addView(v, vl);
            addDivider(box, i > 0);
            box.addView(r, rowsFill ? new LinearLayout.LayoutParams(-1, 0, 1f) : rowParams());
        }
        if (hourly != null && hourly.length() > 0) {
            int color = colorOf(col.optString("hourly_color"), 0xFF2A9D74);
            LinearLayout.LayoutParams hl = flexHourly ? new LinearLayout.LayoutParams(-1, 0, 1f) : new LinearLayout.LayoutParams(-1, dp(46));
            hl.topMargin = dp(8);
            box.addView(new HourBars(this, hourly, color)
                    .part(col.optJSONArray("hourly2"), colorOf(col.optString("hourly2_color"), 0xFFFF6A4D)), hl);
            JSONArray labels = col.optJSONArray("hourly_labels");
            if (labels != null && labels.length() > 0) {
                // One label under each bar (days), or a few spread across (24 h ago ... now).
                boolean each = labels.length() == hourly.length();
                LinearLayout lr = new LinearLayout(this);
                lr.setOrientation(LinearLayout.HORIZONTAL);
                for (int i = 0; i < labels.length(); i++) {
                    TextView t = text(9.5f, 0xFF8B8B94, Typeface.MONOSPACE);
                    t.setText(labels.optString(i));
                    t.setSingleLine(true);
                    if (each) {
                        t.setGravity(Gravity.CENTER_HORIZONTAL);
                        lr.addView(t, new LinearLayout.LayoutParams(0, -2, 1f));
                    } else {
                        if (i > 0) lr.addView(new View(this), new LinearLayout.LayoutParams(0, 1, 1f));
                        lr.addView(t, new LinearLayout.LayoutParams(-2, -2));
                    }
                }
                LinearLayout.LayoutParams lrl = new LinearLayout.LayoutParams(-1, -2);
                lrl.topMargin = dp(3);
                box.addView(lr, lrl);
            }
            if (!col.optString("hourly_label").isEmpty()) {
                TextView lab = text(9.5f, 0xFF8B8B94, Typeface.MONOSPACE);
                lab.setText(col.optString("hourly_label"));
                LinearLayout.LayoutParams ll = new LinearLayout.LayoutParams(-2, -2);
                ll.topMargin = dp(3);
                box.addView(lab, ll);
            }
        }
        if (footer != null && footer.length() > 1) {
            LinearLayout.LayoutParams fh = new LinearLayout.LayoutParams(-1, -2);
            fh.topMargin = dp(10);
            box.addView(cardHead(col.optString("footer_key"), col.optString("footer_badge")), fh);
            LinearLayout.LayoutParams fl = new LinearLayout.LayoutParams(-1, 0, 1f);
            fl.topMargin = dp(6);
            box.addView(new GraphView(this, footer, colorOf(col.optString("footer_color"), 0xFF34D399)), fl);
        }
        JSONArray chips = col.optJSONArray("chips");
        if (chips != null && chips.length() > 0) {
            ChipFlow flow = new ChipFlow(this, dp(5));
            for (int i = 0; i < chips.length(); i++) {
                JSONArray ch = chips.optJSONArray(i);
                if (ch == null) continue;
                String a = ch.optString(0), b = ch.optString(1), c = ch.optString(2);
                SpannableString s = new SpannableString(a + b + c);
                s.setSpan(new ForegroundColorSpan(Color.WHITE), a.length(), a.length() + b.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                s.setSpan(new android.text.style.StyleSpan(Typeface.BOLD), a.length(), a.length() + b.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                TextView chip = text(10, 0xFFD4D4D8, Typeface.MONOSPACE);
                chip.setSingleLine(true);
                chip.setText(s);
                chip.setPadding(dp(6), dp(3), dp(6), dp(3));
                GradientDrawable cb = new GradientDrawable();
                cb.setColor(0xFF16161B);
                cb.setCornerRadius(dp(5));
                chip.setBackground(cb);
                flow.addView(chip);
            }
            LinearLayout.LayoutParams fl = new LinearLayout.LayoutParams(-1, -2);
            fl.topMargin = dp(7);
            box.addView(flow, fl);
        }
        return box;
    }

    private LinearLayout.LayoutParams rowParams() {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.topMargin = dp(3);
        lp.bottomMargin = dp(3);
        return lp;
    }

    private View cardHead(String key, String badge) {
        LinearLayout kh = new LinearLayout(this);
        kh.setOrientation(LinearLayout.HORIZONTAL);
        kh.addView(key(key), new LinearLayout.LayoutParams(0, -2, 1f));
        if (badge != null && !badge.isEmpty()) {
            TextView b = key(badge);
            String up = badge.toUpperCase(Locale.US);
            b.setTextColor(up.startsWith("CHECK") || up.startsWith("PAUSED") || up.startsWith("NOT ") || up.startsWith("WARM") ? 0xFFFBBF24
                    : up.startsWith("DOWN") || up.startsWith("HOT") ? 0xFFFF6A4D
                    : up.contains("HEALTHY") || up.startsWith("ALL") || up.startsWith("ONLINE") || up.startsWith("BLOCKING") || up.startsWith("COOL") ? 0xFF34D399
                    : 0xFF8B8B94);
            kh.addView(b);
        }
        return kh;
    }

    private static int colorOf(String hex, int fallback) {
        try {
            return hex == null || hex.isEmpty() ? fallback : Color.parseColor(hex);
        } catch (IllegalArgumentException e) {
            return fallback;
        }
    }

    private View barView(int pct, int color) {
        FrameLayout bar = new FrameLayout(this);
        GradientDrawable track = new GradientDrawable();
        track.setColor(0xFF1F1F25);
        track.setCornerRadius(dp(2));
        bar.setBackground(track);
        View fill = new View(this);
        GradientDrawable fd = new GradientDrawable();
        fd.setColor(color);
        fd.setCornerRadius(dp(2));
        fill.setBackground(fd);
        bar.addView(fill, new FrameLayout.LayoutParams(0, -1));
        bar.post(() -> {
            FrameLayout.LayoutParams lp = (FrameLayout.LayoutParams) fill.getLayoutParams();
            lp.width = Math.round(bar.getWidth() * pct / 100f);
            fill.setLayoutParams(lp);
        });
        return bar;
    }

    private View driveView(JSONObject d) {
        Typeface regular = UiFont.get(this, R.string.font_regular);
        boolean warn = d != null && d.optBoolean("warn");
        LinearLayout dv = new LinearLayout(this);
        dv.setOrientation(LinearLayout.VERTICAL);
        if (d == null) return dv;
        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        TextView name = text(11, 0xFFF4F4F5, regular);
        name.setSingleLine(true);
        name.setEllipsize(TextUtils.TruncateAt.END);
        name.setText(d.optString("mount") + " " + d.optString("name"));
        top.addView(name, new LinearLayout.LayoutParams(0, -2, 1f));
        TextView val = text(10, warn ? 0xFFFBBF24 : 0xFFD4D4D8, Typeface.MONOSPACE);
        val.setSingleLine(true);
        val.setText(d.optInt("pct") + "% \u00b7 " + d.optString("free") + " free");
        LinearLayout.LayoutParams vl = new LinearLayout.LayoutParams(-2, -2);
        vl.leftMargin = dp(6);
        top.addView(val, vl);
        dv.addView(top);
        LinearLayout.LayoutParams bl = new LinearLayout.LayoutParams(-1, dp(5));
        bl.topMargin = dp(4);
        dv.addView(barView(Math.max(0, Math.min(100, d.optInt("pct"))), warn ? 0xFFFBBF24 : 0xFF34D399), bl);
        TextView info = text(9, 0xFF8B8B94, Typeface.MONOSPACE);
        info.setSingleLine(true);
        info.setEllipsize(TextUtils.TruncateAt.END);
        info.setText(d.optString("info"));
        LinearLayout.LayoutParams il = new LinearLayout.LayoutParams(-1, -2);
        il.topMargin = dp(3);
        dv.addView(info, il);
        return dv;
    }

    /** Lays its children out left to right, wrapping onto the next line (the chips under a graph). */
    static class ChipFlow extends android.view.ViewGroup {
        private final int gap;

        ChipFlow(Context c, int gap) {
            super(c);
            this.gap = gap;
        }

        @Override
        protected void onMeasure(int wSpec, int hSpec) {
            int maxW = MeasureSpec.getSize(wSpec), x = 0, y = 0, lineH = 0;
            for (int i = 0; i < getChildCount(); i++) {
                View ch = getChildAt(i);
                ch.measure(MeasureSpec.makeMeasureSpec(maxW, MeasureSpec.AT_MOST), MeasureSpec.UNSPECIFIED);
                if (x > 0 && x + ch.getMeasuredWidth() > maxW) {
                    x = 0;
                    y += lineH + gap;
                    lineH = 0;
                }
                x += ch.getMeasuredWidth() + gap;
                lineH = Math.max(lineH, ch.getMeasuredHeight());
            }
            setMeasuredDimension(maxW, y + lineH);
        }

        @Override
        protected void onLayout(boolean changed, int l, int t, int r, int b) {
            int maxW = r - l, x = 0, y = 0, lineH = 0;
            for (int i = 0; i < getChildCount(); i++) {
                View ch = getChildAt(i);
                int w = ch.getMeasuredWidth(), h = ch.getMeasuredHeight();
                if (x > 0 && x + w > maxW) {
                    x = 0;
                    y += lineH + gap;
                    lineH = 0;
                }
                ch.layout(x, y, x + w, y + h);
                x += w + gap;
                lineH = Math.max(lineH, h);
            }
        }
    }

    /** One bar per hour (Pi-hole's queries over the day). */
    static class HourBars extends View {
        private final float[] vals;
        private final int color;
        private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);

        private float[] part;
        private int partColor;

        HourBars(Context c, JSONArray a, int color) {
            super(c);
            vals = new float[a.length()];
            for (int i = 0; i < vals.length; i++) vals[i] = (float) a.optDouble(i, 0);
            this.color = color;
        }

        /** The part of each bar drawn in a second color from the bottom (blocked out of all queries). */
        HourBars part(JSONArray a, int color) {
            if (a != null && a.length() == vals.length) {
                part = new float[a.length()];
                for (int i = 0; i < part.length; i++) part[i] = (float) a.optDouble(i, 0);
                partColor = color;
            }
            return this;
        }

        @Override
        protected void onDraw(Canvas c) {
            int n = vals.length;
            if (n == 0) return;
            float max = 1;
            for (float v : vals) max = Math.max(max, v);
            float gap = getResources().getDisplayMetrics().density * 2, w = (getWidth() - gap * (n - 1)) / n, h = getHeight();
            p.setColor(color);
            for (int i = 0; i < n; i++) {
                float bh = Math.max(h * 0.04f, h * vals[i] / max), x = i * (w + gap);
                p.setColor(color);
                c.drawRoundRect(x, h - bh, x + w, h, gap / 2, gap / 2, p);
                if (part != null && vals[i] > 0 && part[i] > 0) {
                    p.setColor(partColor);
                    c.drawRect(x, h - bh * Math.min(1f, part[i] / vals[i]), x + w, h, p);
                }
            }
        }
    }

    private LinearLayout.LayoutParams colParams(int index) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, -1, 1f);
        if (index > 0) lp.leftMargin = dp(11);
        return lp;
    }

    private View detailColumn(JSONObject col) {
        Typeface light = UiFont.get(this, R.string.font_light), regular = UiFont.get(this, R.string.font_regular);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(14), dp(12), dp(14), dp(8));
        GradientDrawable bg = card();
        bg.setStroke(Math.max(1, dp(0.5f)), 0xFF17171C);
        box.setBackground(bg);
        if (col == null) return box;
        LinearLayout kh = new LinearLayout(this);
        kh.setOrientation(LinearLayout.HORIZONTAL);
        kh.addView(key(col.optString("key")), new LinearLayout.LayoutParams(0, -2, 1f));
        if (!col.optString("badge").isEmpty()) {
            TextView badge = key(col.optString("badge"));
            badge.setTextColor(col.optString("badge").startsWith("CHECK") ? 0xFFFBBF24 : 0xFF34D399);
            kh.addView(badge);
        }
        box.addView(kh, new LinearLayout.LayoutParams(-1, -2));
        if (!col.optString("big").isEmpty()) {
            TextView big = text(32, Color.WHITE, light);
            big.setSingleLine(true);
            big.setText(col.optString("big"));
            LinearLayout.LayoutParams bgl = new LinearLayout.LayoutParams(-2, -2);
            bgl.topMargin = dp(8);
            box.addView(big, bgl);
        }
        if (!col.optString("bigsub").isEmpty()) {
            TextView sub = text(10, 0xFFA1A1AA, Typeface.MONOSPACE);
            sub.setText(col.optString("bigsub"));
            LinearLayout.LayoutParams sbl = new LinearLayout.LayoutParams(-2, -2);
            sbl.topMargin = dp(3);
            box.addView(sub, sbl);
        }
        JSONArray graph = col.optJSONArray("graph");
        if (graph != null && graph.length() > 1) {
            int color;
            try {
                color = Color.parseColor(col.optString("graph_color", "#FF6A4D"));
            } catch (IllegalArgumentException e) {
                color = 0xFFFF6A4D;
            }
            LinearLayout.LayoutParams gl = new LinearLayout.LayoutParams(-1, dp(62));
            gl.topMargin = dp(8);
            box.addView(new GraphView(this, graph, color), gl);
        }
        JSONArray cores = col.optJSONArray("cores");
        if (cores != null && cores.length() > 0) {
            LinearLayout.LayoutParams cl = new LinearLayout.LayoutParams(-1, dp(30));
            cl.topMargin = dp(8);
            box.addView(new CoresView(this, cores), cl);
        }
        JSONArray drives = col.optJSONArray("drives");
        for (int i = 0; drives != null && i < drives.length(); i++) {
            JSONObject d = drives.optJSONObject(i);
            boolean warn = d.optBoolean("warn");
            LinearLayout dv = new LinearLayout(this);
            dv.setOrientation(LinearLayout.VERTICAL);
            dv.setGravity(Gravity.CENTER_VERTICAL);
            LinearLayout top = new LinearLayout(this);
            top.setOrientation(LinearLayout.HORIZONTAL);
            TextView name = text(11.5f, 0xFFF4F4F5, regular);
            name.setSingleLine(true);
            name.setEllipsize(TextUtils.TruncateAt.END);
            name.setText(d.optString("mount") + "  " + d.optString("name"));
            top.addView(name, new LinearLayout.LayoutParams(0, -2, 1f));
            TextView val = text(10, warn ? 0xFFFBBF24 : 0xFFD4D4D8, Typeface.MONOSPACE);
            val.setText(d.optInt("pct") + "% \u00b7 " + d.optString("free") + " free");
            top.addView(val);
            dv.addView(top);
            FrameLayout bar = new FrameLayout(this);
            GradientDrawable track = new GradientDrawable();
            track.setColor(0xFF1F1F25);
            track.setCornerRadius(dp(3));
            bar.setBackground(track);
            View fill = new View(this);
            GradientDrawable fd = new GradientDrawable();
            fd.setColor(warn ? 0xFFFBBF24 : 0xFF34D399);
            fd.setCornerRadius(dp(3));
            fill.setBackground(fd);
            final int pct = Math.max(0, Math.min(100, d.optInt("pct")));
            bar.addView(fill, new FrameLayout.LayoutParams(0, -1));
            bar.post(() -> {
                FrameLayout.LayoutParams lp = (FrameLayout.LayoutParams) fill.getLayoutParams();
                lp.width = Math.round(bar.getWidth() * pct / 100f);
                fill.setLayoutParams(lp);
            });
            LinearLayout.LayoutParams brl = new LinearLayout.LayoutParams(-1, dp(5));
            brl.topMargin = dp(5);
            dv.addView(bar, brl);
            TextView info = text(9, 0xFF71717A, Typeface.MONOSPACE);
            info.setSingleLine(true);
            info.setEllipsize(TextUtils.TruncateAt.END);
            info.setText(d.optString("info"));
            LinearLayout.LayoutParams il = new LinearLayout.LayoutParams(-1, -2);
            il.topMargin = dp(4);
            dv.addView(info, il);
            addDivider(box, i > 0);
            box.addView(dv, new LinearLayout.LayoutParams(-1, 0, 1f));
        }
        JSONArray queue = col.optJSONArray("queue");
        for (int i = 0; queue != null && i < queue.length(); i++) {
            JSONObject q = queue.optJSONObject(i);
            if (q == null) continue;
            boolean paused = q.optBoolean("paused");
            final int pct = Math.max(0, Math.min(100, q.optInt("pct")));
            LinearLayout qv = new LinearLayout(this);
            qv.setOrientation(LinearLayout.VERTICAL);
            qv.setGravity(Gravity.CENTER_VERTICAL);
            LinearLayout top = new LinearLayout(this);
            top.setOrientation(LinearLayout.HORIZONTAL);
            TextView name = text(13, 0xFFF4F4F5, regular);
            name.setSingleLine(true);
            name.setEllipsize(TextUtils.TruncateAt.END);
            name.setText(q.optString("name"));
            top.addView(name, new LinearLayout.LayoutParams(0, -2, 1f));
            TextView val = text(10.5f, 0xFFD4D4D8, Typeface.MONOSPACE);
            val.setText(pct + "%");
            top.addView(val);
            qv.addView(top);
            FrameLayout bar = new FrameLayout(this);
            GradientDrawable track = new GradientDrawable();
            track.setColor(0xFF1F1F25);
            track.setCornerRadius(dp(3));
            bar.setBackground(track);
            View fill = new View(this);
            GradientDrawable fd = new GradientDrawable();
            fd.setColor(paused ? 0xFFFBBF24 : 0xFFFF6A4D);
            fd.setCornerRadius(dp(3));
            fill.setBackground(fd);
            bar.addView(fill, new FrameLayout.LayoutParams(0, -1));
            bar.post(() -> {
                FrameLayout.LayoutParams lp = (FrameLayout.LayoutParams) fill.getLayoutParams();
                lp.width = Math.round(bar.getWidth() * pct / 100f);
                fill.setLayoutParams(lp);
            });
            LinearLayout.LayoutParams qbl = new LinearLayout.LayoutParams(-1, dp(5));
            qbl.topMargin = dp(6);
            qv.addView(bar, qbl);
            TextView info = text(9.5f, 0xFF8B8B94, Typeface.MONOSPACE);
            info.setSingleLine(true);
            info.setEllipsize(TextUtils.TruncateAt.END);
            info.setText(q.optString("info"));
            LinearLayout.LayoutParams qil = new LinearLayout.LayoutParams(-1, -2);
            qil.topMargin = dp(5);
            qv.addView(info, qil);
            addDivider(box, i > 0);
            box.addView(qv, new LinearLayout.LayoutParams(-1, 0, 1f));
        }
        JSONArray mons = col.optJSONArray("monitors");
        for (int i = 0; mons != null && i < mons.length(); i++) {
            JSONObject m = mons.optJSONObject(i);
            String st = m.optString("status");
            int dot = "up".equals(st) ? 0xFF34D399 : "down".equals(st) ? 0xFFFF6A4D : 0xFFFBBF24;
            LinearLayout r = new LinearLayout(this);
            r.setOrientation(LinearLayout.HORIZONTAL);
            r.setGravity(Gravity.CENTER_VERTICAL);
            TextView name = text(12, 0xFFF4F4F5, regular);
            name.setSingleLine(true);
            name.setEllipsize(TextUtils.TruncateAt.END);
            SpannableString s = new SpannableString("\u25CF  " + m.optString("name"));
            s.setSpan(new ForegroundColorSpan(dot), 0, 1, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            name.setText(s);
            r.addView(name, new LinearLayout.LayoutParams(0, -2, 1.3f));
            TextView type = text(9, 0xFF71717A, Typeface.MONOSPACE);
            type.setText(m.optString("type"));
            r.addView(type, new LinearLayout.LayoutParams(0, -2, 0.45f));
            r.addView(new HeartView(this, m.optJSONArray("hist")), new LinearLayout.LayoutParams(0, dp(12), 1.4f));
            TextView ms = text(10.5f, "up".equals(st) ? 0xFFD4D4D8 : dot, Typeface.MONOSPACE);
            ms.setGravity(Gravity.END);
            ms.setText("up".equals(st) ? (m.isNull("ms") ? "up" : m.optInt("ms") + " ms") : st);
            r.addView(ms, new LinearLayout.LayoutParams(0, -2, 0.55f));
            addDivider(box, i > 0);
            box.addView(r, new LinearLayout.LayoutParams(-1, 0, 1f));
        }
        JSONArray rows = col.optJSONArray("rows");
        boolean first = (drives == null || drives.length() == 0) && (mons == null || mons.length() == 0) && (queue == null || queue.length() == 0);
        for (int i = 0; rows != null && i < rows.length(); i++) {
            JSONArray row = rows.optJSONArray(i);
            if (row == null) continue;
            String label = row.optString(0), value = row.optString(1), lv = row.optString(2, "ok");
            boolean heading = value.isEmpty();
            LinearLayout r = new LinearLayout(this);
            r.setOrientation(LinearLayout.HORIZONTAL);
            r.setGravity(Gravity.CENTER_VERTICAL);
            TextView l = heading ? key(label.toUpperCase(Locale.US)) : text(11.5f, 0xFFF4F4F5, regular);
            if (!heading) {
                l.setText(label);
                l.setSingleLine(true);
                l.setEllipsize(TextUtils.TruncateAt.END);
            }
            r.addView(l, new LinearLayout.LayoutParams(0, -2, 1f));
            TextView v = text(10.5f, "ok".equals(lv) ? 0xFFD4D4D8 : levelColor(lv), Typeface.MONOSPACE);
            v.setSingleLine(true);
            v.setText(value);
            r.addView(v);
            addDivider(box, !(first && i == 0) && !heading);
            box.addView(r, new LinearLayout.LayoutParams(-1, 0, 1f));
        }
        return box;
    }

    private void addDivider(LinearLayout box, boolean show) {
        if (!show) return;
        View line = new View(this);
        line.setBackgroundColor(0xFF1A1A20);
        box.addView(line, new LinearLayout.LayoutParams(-1, Math.max(1, dp(0.5f))));
    }

    /** A filled line graph of the last hour. */
    static class GraphView extends View {
        private final float[] vals;
        private final int color;
        private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final android.graphics.Path path = new android.graphics.Path();

        private float[] vals2;
        private int color2;
        private boolean fit;

        GraphView(Context c, JSONArray a, int color) {
            super(c);
            vals = new float[a.length()];
            for (int i = 0; i < vals.length; i++) vals[i] = (float) a.optDouble(i, 0);
            this.color = color;
        }

        /** A second line drawn over the first (upload over download), on the same scale. */
        GraphView second(JSONArray a, int color) {
            if (a != null && a.length() > 1) {
                vals2 = new float[a.length()];
                for (int i = 0; i < vals2.length; i++) vals2[i] = (float) a.optDouble(i, 0);
                color2 = color;
            }
            return this;
        }

        /** Scale to the values' own range (ping 9-16 ms) instead of from zero, so small changes show. */
        GraphView fit(boolean on) {
            fit = on;
            return this;
        }

        @Override
        protected void onDraw(Canvas c) {
            float lo = Float.MAX_VALUE, hi = 0;
            for (float v : vals) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
            if (vals2 != null) for (float v : vals2) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
            if (fit) {
                if (hi - lo < 1) hi = lo + 1;
                float pad = (hi - lo) * 0.25f;
                lo = Math.max(0, lo - pad);
                hi += pad;
            } else {
                lo = 0;
                hi = Math.max(1, hi) * 1.15f;
            }
            draw(c, vals, color, lo, hi);
            if (vals2 != null) draw(c, vals2, color2, lo, hi);
        }

        private void draw(Canvas c, float[] vals, int color, float lo, float hi) {
            float w = getWidth(), h = getHeight();
            path.reset();
            for (int i = 0; i < vals.length; i++) {
                float x = w * i / (vals.length - 1), y = h - h * (vals[i] - lo) / (hi - lo);
                if (i == 0) path.moveTo(x, y);
                else path.lineTo(x, y);
            }
            android.graphics.Path fill = new android.graphics.Path(path);
            fill.lineTo(w, h);
            fill.lineTo(0, h);
            fill.close();
            p.setStyle(Paint.Style.FILL);
            p.setColor((color & 0x00FFFFFF) | 0x22000000);
            c.drawPath(fill, p);
            p.setStyle(Paint.Style.STROKE);
            p.setStrokeWidth(Math.max(2f, getResources().getDisplayMetrics().density * 1.5f));
            p.setColor(color);
            c.drawPath(path, p);
        }
    }

    /** One small bar per CPU core. */
    static class CoresView extends View {
        private final float[] vals;
        private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);

        CoresView(Context c, JSONArray a) {
            super(c);
            vals = new float[a.length()];
            for (int i = 0; i < vals.length; i++) vals[i] = (float) a.optDouble(i, 0);
        }

        @Override
        protected void onDraw(Canvas c) {
            int n = vals.length;
            float gap = getResources().getDisplayMetrics().density * 2, w = (getWidth() - gap * (n - 1)) / n, h = getHeight();
            for (int i = 0; i < n; i++) {
                float bh = Math.max(h * 0.08f, h * Math.min(100, vals[i]) / 100f), x = i * (w + gap);
                p.setColor(0xFF1F1F25);
                c.drawRect(x, 0, x + w, h, p);
                p.setColor(vals[i] >= 90 ? 0xFFFBBF24 : 0xFF34D399);
                c.drawRect(x, h - bh, x + w, h, p);
            }
        }
    }

    /** An Uptime Kuma monitor's last checks: green up, red down, amber anything else. */
    static class HeartView extends View {
        private final String[] st;
        private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);

        HeartView(Context c, JSONArray a) {
            super(c);
            st = new String[a == null ? 0 : a.length()];
            for (int i = 0; i < st.length; i++) st[i] = a.optString(i);
        }

        @Override
        protected void onDraw(Canvas c) {
            int slots = 30;
            float gap = getResources().getDisplayMetrics().density, w = (getWidth() - gap * (slots - 1)) / slots, h = getHeight();
            int start = slots - st.length;
            for (int i = 0; i < slots; i++) {
                String s = i < start ? "" : st[i - start];
                p.setColor(s.isEmpty() ? 0xFF1F1F25 : "up".equals(s) ? 0xFF34D399 : "down".equals(s) ? 0xFFFF6A4D : 0xFFFBBF24);
                float x = i * (w + gap);
                c.drawRoundRect(x, 0, x + w, h, gap, gap, p);
            }
        }
    }

    // ---- the boxes ------------------------------------------------------------------------------

    private static int levelColor(String level) {
        return "critical".equals(level) ? 0xFFFF6A4D : "warning".equals(level) ? 0xFFFBBF24 : "info".equals(level) ? 0xFF60A5FA : 0xFF34D399;
    }

    /** One box: a title with a status in the corner, and five rows sharing the height evenly. */
    private static final int ROWS = 5;

    private class Box {
        final LinearLayout body, root;
        final TextView status;
        final LinearLayout[] rows = new LinearLayout[ROWS];
        final TextView[] labels = new TextView[ROWS], values = new TextView[ROWS];
        final String okWord;

        Box(String title, LinearLayout parent, boolean gap) {
            okWord = "OK";
            LinearLayout box = new LinearLayout(HalActivity.this);
            root = box;
            box.setOrientation(LinearLayout.VERTICAL);
            box.setPadding(dp(15), dp(13), dp(15), dp(10));
            GradientDrawable bg = card();
            bg.setStroke(Math.max(1, dp(0.5f)), 0xFF17171C);
            box.setBackground(bg);
            LinearLayout.LayoutParams bl = new LinearLayout.LayoutParams(-1, 0, 1f);
            if (gap) bl.topMargin = dp(12);
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
            for (int i = 0; i < ROWS; i++) {
                if (i > 0) {
                    View line = new View(HalActivity.this);
                    line.setBackgroundColor(0xFF1A1A20);
                    body.addView(line, new LinearLayout.LayoutParams(-1, Math.max(1, dp(0.5f))));
                }
                LinearLayout r = new LinearLayout(HalActivity.this);
                r.setOrientation(LinearLayout.HORIZONTAL);
                r.setGravity(Gravity.CENTER_VERTICAL);
                body.addView(r, new LinearLayout.LayoutParams(-1, 0, 1f));
                TextView l = text(12, 0xFFF4F4F5, regular);
                l.setSingleLine(true);
                r.addView(l, new LinearLayout.LayoutParams(-2, -2));
                View spring = new View(HalActivity.this);
                r.addView(spring, new LinearLayout.LayoutParams(0, 1, 1f));
                TextView v = text(11, 0xFFD4D4D8, Typeface.MONOSPACE);
                v.setSingleLine(true);
                v.setEllipsize(TextUtils.TruncateAt.END);
                r.addView(v);
                rows[i] = r;
                labels[i] = l;
                values[i] = v;
            }
        }

        /** The PC isn't answering: dim, and say how old the numbers are. */
        void stale(String seen) {
            root.setAlpha(0.38f);
            status.setText("\u25CF " + seen);
            status.setTextColor(0xFF8B8B94);
        }

        void setStatus(String level, String okText, String problemText) {
            boolean ok = level == null || level.isEmpty() || "ok".equals(level);
            status.setText("● " + (ok ? okText : problemText));
            status.setTextColor(levelColor(ok ? "ok" : level));
        }

        /** rows: [[label, value, level], ...] */
        void rows(JSONArray a, String level, String okText) {
            int problems = 0;
            for (int i = 0; i < ROWS; i++) {
                JSONArray row = a == null ? null : a.optJSONArray(i);
                rows[i].setVisibility(row == null ? View.INVISIBLE : View.VISIBLE);
                if (row == null) continue;
                String lv = row.optString(2, "ok");
                labels[i].setText(row.optString(0, ""));
                values[i].setText(row.optString(1, ""));
                values[i].setTextColor("ok".equals(lv) ? 0xFFD4D4D8 : levelColor(lv));
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
                ml.leftMargin = dp(i == 0 ? 12 : 8);
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
                int w = dp(ds.length() > 3 ? 16 : 20);
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
            for (int i = 0; i < ROWS; i++) {
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
                values[i].setTextColor("up".equals(st) ? 0xFFD4D4D8 : dot);
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
