package com.gathin.tvhome;

import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Handler;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * NZB-CC: the "Ready to watch" note. While Home is on screen, asks NZB Command Center's phone server once a minute
 * which downloads SABnzbd finished (GET /api/tv/finished) and hands each new one to Home to show. The newest one
 * seen is remembered, so nothing is announced twice and the first look announces nothing old.
 */
final class DownloadNotes {
    static final long CHECK_MS = 60_000L;
    private static final String PREFS = "download_notes";

    interface Listener {
        void onDownloadReady(String title, Bitmap poster);
    }

    private final Context ctx;
    private final HomeExtras extras;
    private final Handler main;
    private final Listener listener;
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private volatile boolean busy;
    private long checkedAt;

    DownloadNotes(Context ctx, HomeExtras extras, Handler main, Listener listener) {
        this.ctx = ctx.getApplicationContext();
        this.extras = extras;
        this.main = main;
        this.listener = listener;
    }

    void checkIfDue() {
        if (busy || extras.kodiServer.isEmpty() || System.currentTimeMillis() - checkedAt < CHECK_MS) return;
        busy = true;
        checkedAt = System.currentTimeMillis();
        try {
            io.execute(this::check);
        } catch (java.util.concurrent.RejectedExecutionException e) {
            busy = false;
        }
    }

    private void check() {
        try {
            SharedPreferences p = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
            long seen = p.getLong("newest", -1);
            JSONArray a = new JSONObject(new String(get("/api/tv/finished"), StandardCharsets.UTF_8)).getJSONArray("items");
            long newest = seen;
            List<JSONObject> fresh = new ArrayList<>();
            for (int i = 0; i < a.length(); i++) {
                JSONObject o = a.getJSONObject(i);
                long done = o.optLong("completed");
                newest = Math.max(newest, done);
                if (seen >= 0 && done > seen) fresh.add(o);
            }
            if (newest != seen) p.edit().putLong("newest", newest).apply();
            // Oldest first, at most three, so a burst of finished episodes doesn't keep notes coming for a minute.
            for (int i = Math.min(fresh.size(), 3) - 1; i >= 0; i--) {
                JSONObject o = fresh.get(i);
                String title = o.optString("title", "");
                if (o.optInt("year") > 0 && "movie".equals(o.optString("kind"))) title += " (" + o.optInt("year") + ")";
                Bitmap poster = null;
                String url = o.optString("poster", "");
                if (url.startsWith("/api/tv/poster?id=")) {
                    try {
                        byte[] raw = get(url);
                        BitmapFactory.Options opt = new BitmapFactory.Options();
                        opt.inSampleSize = 4;
                        poster = BitmapFactory.decodeByteArray(raw, 0, raw.length, opt);
                    } catch (Exception ignored) {
                        poster = null;   // the note shows without one
                    }
                }
                final String t = title;
                final Bitmap b = poster;
                if (!t.isEmpty()) main.post(() -> listener.onDownloadReady(t, b));
            }
        } catch (Exception ignored) {
            // the PC is off or SABnzbd isn't answering: try again next minute
        } finally {
            busy = false;
        }
    }

    private byte[] get(String path) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(extras.kodiServer + path).openConnection();
        c.setConnectTimeout(4000);
        c.setReadTimeout(10000);
        c.setRequestProperty("X-Mobile-Token", extras.kodiToken);
        try (InputStream in = c.getInputStream()) {
            return HomeExtras.readAll(in);
        } finally {
            c.disconnect();
        }
    }

    void shutdown() {
        io.shutdownNow();
    }
}
