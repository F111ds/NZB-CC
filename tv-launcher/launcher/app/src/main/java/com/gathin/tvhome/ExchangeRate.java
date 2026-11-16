package com.gathin.tvhome;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Handler;

import org.json.JSONArray;
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
 * NZB-CC: the dollar-to-peso rate under the clock ("US$1 = 18.42 MXN ▲ 0.06").
 *
 * <p>With an AllRatesToday key ("fx_key" in config.json) the rate is the live mid-market rate, read every
 * "fx_refresh_minutes" (10 by default) while Home is on screen. Without a key, or when the key stops answering
 * (out of free requests, revoked), it is the daily reference rate from Frankfurter (European Central Bank; no
 * account or key), read every three hours, with open.er-api.com as the last fallback.
 *
 * <p>The arrow is the change since the last daily close before today (Frankfurter), so a live rate moves against
 * yesterday's close. The last rate is kept, so Home shows it at once and while offline.
 */
final class ExchangeRate {
    static final long DAILY_REFRESH_MS = 3 * 3600 * 1000L;
    /** After the live source refuses the key (401/403/429), use the daily rate for this long before trying it again. */
    private static final long LIVE_PAUSE_MS = 24 * 3600 * 1000L;
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
    /** The last daily close before today, and when it was read (Frankfurter). */
    private double prevClose = Double.NaN;
    private String prevDay = "";
    private long closeReadAt;
    private long livePausedUntil;

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
            prevClose = Double.longBitsToDouble(p.getLong("prev_close", Double.doubleToLongBits(Double.NaN)));
            prevDay = p.getString("prev_day", "");
            closeReadAt = p.getLong("close_at", 0);
        }
        livePausedUntil = p.getLong("live_paused_until", 0);
    }

    private String pair() {
        return extras.fxFrom + ">" + extras.fxTo;
    }

    private boolean liveOn() {
        return !extras.fxKey.isEmpty() && System.currentTimeMillis() >= livePausedUntil;
    }

    private long refreshMs() {
        return liveOn() ? extras.fxRefreshMinutes * 60_000L : DAILY_REFRESH_MS;
    }

    boolean hasRate() {
        return !Double.isNaN(rate) && System.currentTimeMillis() - readAt < 7 * 86400_000L;
    }

    /** "US$1 = 18.42 MXN  ▲ 0.06" (the arrow part only with a change). */
    CharSequence line() {
        if (!hasRate()) return "";
        String from = "USD".equals(extras.fxFrom) ? "US$1" : "1 " + extras.fxFrom;
        String num = String.format(Locale.US, "%.2f", rate);
        String text = from + " = " + num + " " + extras.fxTo;
        boolean arrowOn = !Double.isNaN(change) && Math.abs(change) >= 0.005;
        String arrow = arrowOn ? String.format(Locale.US, "%s %.2f", change > 0 ? "▲" : "▼", Math.abs(change)) : "";
        android.text.SpannableString s = new android.text.SpannableString(arrowOn ? text + "   " + arrow : text);
        int at = from.length() + 3;
        s.setSpan(new android.text.style.ForegroundColorSpan(0xFFFFFFFF), at, at + num.length(),
                android.text.Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);   // the rate itself in white
        if (!arrowOn) return s;
        s.setSpan(new android.text.style.ForegroundColorSpan(change > 0 ? 0xFF7BE3A0 : 0xFFFF8A80),
                text.length() + 3, s.length(), android.text.Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        return s;
    }

    void refreshIfStale() {
        if (busy || System.currentTimeMillis() - readAt < refreshMs()) return;
        busy = true;
        try {
            io.execute(this::read);
        } catch (java.util.concurrent.RejectedExecutionException e) {
            busy = false;
        }
    }

    private void read() {
        long now = System.currentTimeMillis();
        String today = LocalDate.now().toString();
        double r = Double.NaN, c = Double.NaN;
        String d = "";

        // 1. The daily closes (Frankfurter): the rate without a key, and yesterday's close for the arrow. Read at
        //    most every three hours, so the live rate doesn't cost a second request each time.
        double dailyRate = Double.NaN, dailyChange = Double.NaN;
        String dailyDay = "";
        double pc = prevClose;
        String pd = prevDay;
        long pcAt = closeReadAt;
        boolean needDaily = !liveOn() || Double.isNaN(pc) || now - pcAt >= DAILY_REFRESH_MS || pd.compareTo(today) >= 0;
        if (needDaily) {
            try {
                // The last ten days: the newest two business days give the rate and its change.
                String start = LocalDate.now().minusDays(10).toString();
                JSONObject j = get("https://api.frankfurter.app/" + start + "..?from=" + extras.fxFrom + "&to=" + extras.fxTo, null);
                JSONObject rates = j.getJSONObject("rates");
                List<String> days = new ArrayList<>();
                for (Iterator<String> it = rates.keys(); it.hasNext(); ) days.add(it.next());
                Collections.sort(days);
                if (!days.isEmpty()) {
                    dailyDay = days.get(days.size() - 1);
                    dailyRate = rates.getJSONObject(dailyDay).getDouble(extras.fxTo);
                    if (days.size() > 1) dailyChange = dailyRate - rates.getJSONObject(days.get(days.size() - 2)).getDouble(extras.fxTo);
                    // The newest close before today, for the live rate's arrow.
                    for (int i = days.size() - 1; i >= 0; i--) {
                        if (days.get(i).compareTo(today) < 0) {
                            pd = days.get(i);
                            pc = rates.getJSONObject(pd).getDouble(extras.fxTo);
                            pcAt = now;
                            break;
                        }
                    }
                }
            } catch (Exception ignored) {
                dailyRate = Double.NaN;
            }
        }

        // 2. The live rate (AllRatesToday), when there is a key and it hasn't been refused lately.
        long pauseUntil = livePausedUntil;
        if (liveOn()) {
            try {
                JSONArray a = getArray("https://allratestoday.com/api/v1/rates?source=" + extras.fxFrom
                        + "&target=" + extras.fxTo, extras.fxKey);
                double live = a.getJSONObject(0).getDouble("rate");
                if (live > 0) {
                    r = live;
                    c = Double.isNaN(pc) ? Double.NaN : live - pc;
                    d = today;
                }
            } catch (HttpRefused e) {
                pauseUntil = now + LIVE_PAUSE_MS;   // out of requests or a bad key: daily rates until tomorrow
            } catch (Exception ignored) {
                // offline or a hiccup: the daily rate below, and the live one again next time
            }
        }

        // 3. No live rate: the daily one, then open.er-api.com.
        if (Double.isNaN(r) && !Double.isNaN(dailyRate)) {
            r = dailyRate;
            c = dailyChange;
            d = dailyDay;
        }
        if (Double.isNaN(r) && !needDaily) {
            // The live read failed and Frankfurter was skipped: try it now rather than going straight to the fallback.
            try {
                String start = LocalDate.now().minusDays(10).toString();
                JSONObject j = get("https://api.frankfurter.app/" + start + "..?from=" + extras.fxFrom + "&to=" + extras.fxTo, null);
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
        }
        if (Double.isNaN(r)) {
            try {
                JSONObject j = get("https://open.er-api.com/v6/latest/" + extras.fxFrom, null);
                r = j.getJSONObject("rates").getDouble(extras.fxTo);
                c = Double.NaN;
                d = today;
            } catch (Exception ignored) {
                r = Double.NaN;
            }
        }

        final double fr = r, fc = c, fpc = pc;
        final String fd = d, fpd = pd;
        final long at = System.currentTimeMillis(), fpcAt = pcAt, fPause = pauseUntil;
        SharedPreferences.Editor e = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putLong("live_paused_until", fPause);
        if (!Double.isNaN(fr)) {
            e.putString("pair", pair())
                    .putLong("rate", Double.doubleToLongBits(fr)).putLong("change", Double.doubleToLongBits(fc))
                    .putString("day", fd).putLong("at", at)
                    .putLong("prev_close", Double.doubleToLongBits(fpc)).putString("prev_day", fpd)
                    .putLong("close_at", fpcAt);
        }
        e.apply();
        main.post(() -> {
            busy = false;
            livePausedUntil = fPause;
            prevClose = fpc;
            prevDay = fpd;
            closeReadAt = fpcAt;
            if (Double.isNaN(fr)) {
                readAt = Math.max(readAt, System.currentTimeMillis() - refreshMs() + 15 * 60 * 1000L);   // retry in 15 min
                return;
            }
            rate = fr;
            change = fc;
            day = fd;
            readAt = at;
            onChange.run();
        });
    }

    /** The server answered but refused the request (bad key, out of requests). */
    private static final class HttpRefused extends Exception {
        HttpRefused(int code) {
            super("HTTP " + code);
        }
    }

    private static String fetch(String url, String bearer) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(8000);
        c.setReadTimeout(8000);
        if (bearer != null) c.setRequestProperty("Authorization", "Bearer " + bearer);
        try {
            int code = c.getResponseCode();
            if (code == 401 || code == 403 || code == 429) throw new HttpRefused(code);
            try (InputStream in = c.getInputStream()) {
                return new String(HomeExtras.readAll(in), StandardCharsets.UTF_8);
            }
        } finally {
            c.disconnect();
        }
    }

    private static JSONObject get(String url, String bearer) throws Exception {
        return new JSONObject(fetch(url, bearer));
    }

    private static JSONArray getArray(String url, String bearer) throws Exception {
        return new JSONArray(fetch(url, bearer));
    }

    void shutdown() {
        io.shutdownNow();
    }
}
