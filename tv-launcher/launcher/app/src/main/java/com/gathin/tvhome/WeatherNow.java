package com.gathin.tvhome;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Handler;

import org.json.JSONObject;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * The weather under the clock: "QUERÉTARO CITY · PARTLY CLOUDY" over "22° · H 24° L 10°", from
 * Open-Meteo (no account or key). The last reading is kept, so Home shows it at once after a restart;
 * it is read again every 30 minutes while Home is on screen.
 */
final class WeatherNow {
    static final long REFRESH_MS = 30 * 60 * 1000L;
    private static final String PREFS = "weather_now";

    private final Context ctx;
    private final HomeExtras extras;
    private final Handler main;
    private final Runnable onChange;
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private volatile boolean busy;

    /** "☁ 22° · H 24° L 10°" */
    String pill = "";
    long readAt;

    WeatherNow(Context ctx, HomeExtras extras, Handler main, Runnable onChange) {
        this.ctx = ctx.getApplicationContext();
        this.extras = extras;
        this.main = main;
        this.onChange = onChange;
        SharedPreferences p = this.ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        if (p.getString("where", "").equals(where())) {
            pill = p.getString("pill", "");
            readAt = p.getLong("at", 0);
        }
    }

    boolean hasReading() {
        return !pill.isEmpty() && System.currentTimeMillis() - readAt < 6 * 3600 * 1000L;
    }

    /** Reads the weather again when the last reading is older than REFRESH_MS. */
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
        try {
            String url = String.format(Locale.US,
                    "https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
                            + "&current=temperature_2m,weather_code,is_day&daily=temperature_2m_max,temperature_2m_min"
                            + "&timezone=auto&forecast_days=1&temperature_unit=%s",
                    extras.weatherLat, extras.weatherLon, extras.fahrenheit ? "fahrenheit" : "celsius");
            HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
            c.setConnectTimeout(8000);
            c.setReadTimeout(8000);
            JSONObject j;
            try (InputStream in = c.getInputStream()) {
                j = new JSONObject(new String(HomeExtras.readAll(in), StandardCharsets.UTF_8));
            } finally {
                c.disconnect();
            }
            JSONObject cur = j.getJSONObject("current");
            JSONObject day = j.getJSONObject("daily");
            long now = Math.round(cur.getDouble("temperature_2m"));
            long hi = Math.round(day.getJSONArray("temperature_2m_max").getDouble(0));
            long lo = Math.round(day.getJSONArray("temperature_2m_min").getDouble(0));
            String text = icon(cur.optInt("weather_code", -1), cur.optInt("is_day", 1) == 1)
                    + "  " + now + "°  ·  H " + hi + "°  L " + lo + "°";
            long at = System.currentTimeMillis();
            ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                    .putString("where", where()).putString("pill", text).putLong("at", at).apply();
            main.post(() -> {
                pill = text;
                readAt = at;
                onChange.run();
            });
        } catch (Exception e) {
            // Offline or a bad answer: keep the last reading, and try again on the next refresh.
            main.post(() -> readAt = Math.max(readAt, System.currentTimeMillis() - REFRESH_MS + 5 * 60 * 1000L));
        } finally {
            busy = false;
        }
    }

    private String where() {
        return String.format(Locale.US, "%.3f,%.3f,%s", extras.weatherLat, extras.weatherLon, extras.fahrenheit ? "f" : "c");
    }

    /** A small symbol for a WMO weather code. */
    static String icon(int code, boolean day) {
        if (code == 0 || code == 1) return day ? "☀" : "☾";
        if (code == 2) return "⛅";
        if (code == 3 || code == 45 || code == 48) return "☁";
        if (code >= 71 && code <= 77 || code == 85 || code == 86) return "❄";
        if (code >= 95) return "⚡";
        if (code >= 51) return "☂";
        return "☁";
    }

    /** WMO weather interpretation codes, as Open-Meteo sends them. */
    static String condition(int code, boolean day) {
        switch (code) {
            case 0: return day ? "Sunny" : "Clear";
            case 1: return day ? "Mostly sunny" : "Mostly clear";
            case 2: return "Partly cloudy";
            case 3: return "Overcast";
            case 45: case 48: return "Fog";
            case 51: case 53: case 55: return "Drizzle";
            case 56: case 57: return "Freezing drizzle";
            case 61: return "Light rain";
            case 63: return "Rain";
            case 65: return "Heavy rain";
            case 66: case 67: return "Freezing rain";
            case 71: return "Light snow";
            case 73: return "Snow";
            case 75: return "Heavy snow";
            case 77: return "Snow grains";
            case 80: case 81: return "Showers";
            case 82: return "Heavy showers";
            case 85: case 86: return "Snow showers";
            case 95: return "Thunderstorms";
            case 96: case 99: return "Thunderstorms with hail";
            default: return "Weather";
        }
    }

    void shutdown() {
        io.shutdownNow();
    }
}
