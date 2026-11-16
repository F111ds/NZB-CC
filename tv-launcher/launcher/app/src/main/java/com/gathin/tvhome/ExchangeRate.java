package com.gathin.tvhome;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Handler;

import org.json.JSONObject;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Iterator;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * NZB-CC: the dollar-to-peso rate under the clock ("US$1 = 18.42 MXN ▲ 0.06"). A daily reference rate
 * (Frankfurter, from the European Central Bank; no account or key), with the change since the business day
 * before. Falls back to open.er-api.com (rate only) when Frankfurter doesn't answer. The last rate is kept, so
 * Home shows it at once and while offline; it is read again every three hours while Home is on screen.
 */
final class ExchangeRate {
    static final long REFRESH_MS = 3 * 3600 * 1000L;
    private static final String PREFS = "exchange_rate";

    private final Context ctx;
    private final HomeExtras extras;
    private final Handler main;
    private final Runnable onChange;
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private volatile boolean busy;

    double rate = Double.NaN, change = Double.NaN;
    String day = "";
    long readAt;

    ExchangeRate(Context ctx, HomeExtras extras, Handler main, Runnable onChange) {
        this.ctx = ctx.getApplicationContext();
        this.extras = extras;
        this.main = main;
        this.onChange = onChange;
        SharedPreferences p = this.ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        if (p.getString("pair", "").equals(pair())) {
            rate = Double.longBitsToDouble(p.getLong("rate", Double.doubleToLongBits(Double.NaN)));
            change = Double.longBitsToDouble(p.getLong("change", Double.doubleToLongBits(Double.NaN)));
            day = p.getString("day", "");
            readAt = p.getLong("at", 0);
        }
    }

    private String pair() {
        return extras.fxFrom + ">" + extras.fxTo;
    }

    boolean hasRate() {
        return !Double.isNaN(rate) && System.currentTimeMillis() - readAt < 7 * 86400_000L;
    }

    /** "US$1 = 18.42 MXN  ▲ 0.06" (the arrow part only with a change). */
    CharSequence line() {
        if (!hasRate()) return "";
        String from = "USD".equals(extras.fxFrom) ? "US$1" : "1 " + extras.fxFrom;
        String text = String.format(Locale.US, "%s = %.2f %s", from, rate, extras.fxTo);
        if (Double.isNaN(change) || Math.abs(change) < 0.005) return text;
        String arrow = String.format(Locale.US, "%s %.2f", change > 0 ? "▲" : "▼", Math.abs(change));
        android.text.SpannableString s = new android.text.SpannableString(text + "   " + arrow);
        s.setSpan(new android.text.style.ForegroundColorSpan(change > 0 ? 0xFF7BE3A0 : 0xFFFF8A80),
                text.length() + 3, s.length(), android.text.Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        return s;
    }

    void refreshIfStale() {
        if (busy || System.currentTimeMillis() - readAt < REFRESH_MS) return;
        busy = true;
        try {
            io.execute(this::read);
        } catch (java.util.concurrent.RejectedExecutionException e) {
            busy = false;
        }
    }

    private void read() {
        double r = Double.NaN, c = Double.NaN;
        String d = "";
        try {
            // The last ten days: the newest two business days give the rate and its change.
            String start = LocalDate.now().minusDays(10).toString();
            JSONObject j = get("https://api.frankfurter.app/" + start + "..?from=" + extras.fxFrom + "&to=" + extras.fxTo);
            JSONObject rates = j.getJSONObject("rates");
            List<String> days = new ArrayList<>();
            for (Iterator<String> it = rates.keys(); it.hasNext(); ) days.add(it.next());
            Collections.sort(days);
            if (!days.isEmpty()) {
                d = days.get(days.size() - 1);
                r = rates.getJSONObject(d).getDouble(extras.fxTo);
                if (days.size() > 1) c = r - rates.getJSONObject(days.get(days.size() - 2)).getDouble(extras.fxTo);
            }
        } catch (Exception ignored) {
            r = Double.NaN;
        }
        if (Double.isNaN(r)) {
            try {
                JSONObject j = get("https://open.er-api.com/v6/latest/" + extras.fxFrom);
                r = j.getJSONObject("rates").getDouble(extras.fxTo);
                c = Double.NaN;
                d = LocalDate.now().toString();
            } catch (Exception ignored) {
                r = Double.NaN;
            }
        }
        final double fr = r, fc = c;
        final String fd = d;
        long at = System.currentTimeMillis();
        if (!Double.isNaN(fr)) {
            ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString("pair", pair())
                    .putLong("rate", Double.doubleToLongBits(fr)).putLong("change", Double.doubleToLongBits(fc))
                    .putString("day", fd).putLong("at", at).apply();
        }
        main.post(() -> {
            busy = false;
            if (Double.isNaN(fr)) {
                readAt = Math.max(readAt, System.currentTimeMillis() - REFRESH_MS + 15 * 60 * 1000L);   // retry in 15 min
                return;
            }
            rate = fr;
            change = fc;
            day = fd;
            readAt = at;
            onChange.run();
        });
    }

    private static JSONObject get(String url) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(8000);
        c.setReadTimeout(8000);
        try (InputStream in = c.getInputStream()) {
            return new JSONObject(new String(HomeExtras.readAll(in), StandardCharsets.UTF_8));
        } finally {
            c.disconnect();
        }
    }

    void shutdown() {
        io.shutdownNow();
    }
}
