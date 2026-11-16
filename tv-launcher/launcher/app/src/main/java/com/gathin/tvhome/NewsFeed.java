package com.gathin.tvhome;

import android.content.Context;
import android.os.Handler;
import android.util.Xml;

import org.json.JSONArray;
import org.xmlpull.v1.XmlPullParser;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * NZB-CC: headlines for the news ticker, from the sources' own free RSS feeds (BBC News World and NPR News by
 * default; no account or key). Merged newest first; the last list is kept so the ticker shows at once and while
 * offline. Read again every 20 minutes while Home is on screen.
 */
final class NewsFeed {
    static final long REFRESH_MS = 20 * 60 * 1000L;
    private static final int MAX = 24;
    private static final String PREFS = "news_feed";

    private final Context ctx;
    private final List<String> feeds;
    private final Handler main;
    private final Runnable onChange;
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private volatile boolean busy;

    List<String> headlines = new ArrayList<>();
    long readAt;

    NewsFeed(Context ctx, List<String> feeds, Handler main, Runnable onChange) {
        this.ctx = ctx.getApplicationContext();
        this.feeds = feeds;
        this.main = main;
        this.onChange = onChange;
        android.content.SharedPreferences p = this.ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        if (p.getString("feeds", "").equals(String.join("\n", feeds))) {
            headlines = parse(p.getString("titles", "[]"));
            readAt = p.getLong("at", 0);
        }
    }

    boolean hasHeadlines() {
        return !headlines.isEmpty() && System.currentTimeMillis() - readAt < 2 * 86400_000L;
    }

    void refreshIfStale() {
        if (busy || feeds.isEmpty() || System.currentTimeMillis() - readAt < REFRESH_MS) return;
        busy = true;
        try {
            io.execute(this::read);
        } catch (java.util.concurrent.RejectedExecutionException e) {
            busy = false;
        }
    }

    private void read() {
        List<Object[]> all = new ArrayList<>();   // {time, title}
        int ok = 0;
        for (String url : feeds) {
            try {
                all.addAll(fetch(url));
                ok++;
            } catch (Exception ignored) {
                // that source is down: the others still fill the ticker
            }
        }
        all.sort((a, b) -> Long.compare((Long) b[0], (Long) a[0]));
        List<String> titles = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (Object[] it : all) {
            String t = (String) it[1];
            if (seen.add(t.toLowerCase(Locale.ROOT))) titles.add(t);
            if (titles.size() >= MAX) break;
        }
        final boolean got = ok > 0 && !titles.isEmpty();
        long at = System.currentTimeMillis();
        if (got) {
            ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString("feeds", String.join("\n", feeds))
                    .putString("titles", new JSONArray(titles).toString()).putLong("at", at).apply();
        }
        main.post(() -> {
            busy = false;
            if (!got) {
                readAt = Math.max(readAt, System.currentTimeMillis() - REFRESH_MS + 5 * 60 * 1000L);   // retry in 5 min
                return;
            }
            headlines = titles;
            readAt = at;
            onChange.run();
        });
    }

    private static List<Object[]> fetch(String url) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(8000);
        c.setReadTimeout(10000);
        c.setRequestProperty("User-Agent", "Mozilla/5.0 (Linux; Android TV) TvHome");
        List<Object[]> out = new ArrayList<>();
        try (InputStream in = c.getInputStream()) {
            XmlPullParser p = Xml.newPullParser();
            p.setFeature(XmlPullParser.FEATURE_PROCESS_NAMESPACES, false);
            p.setInput(in, null);
            String title = null, date = null;
            boolean inItem = false;
            for (int e = p.getEventType(); e != XmlPullParser.END_DOCUMENT; e = p.next()) {
                if (e == XmlPullParser.START_TAG) {
                    String n = p.getName();
                    if ("item".equals(n) || "entry".equals(n)) {
                        inItem = true;
                        title = date = null;
                    } else if (inItem && "title".equals(n)) {
                        title = p.nextText();
                    } else if (inItem && ("pubDate".equals(n) || "updated".equals(n) || "published".equals(n))) {
                        date = p.nextText();
                    }
                } else if (e == XmlPullParser.END_TAG && ("item".equals(p.getName()) || "entry".equals(p.getName()))) {
                    inItem = false;
                    String t = clean(title);
                    if (!t.isEmpty()) out.add(new Object[]{time(date), t});
                }
            }
        } finally {
            c.disconnect();
        }
        return out;
    }

    static String clean(String s) {
        if (s == null) return "";
        s = s.replaceAll("<[^>]*>", "").replace("&amp;", "&").replace("&quot;", "\"").replace("&#39;", "'")
                .replace("&apos;", "'").replace("&lt;", "<").replace("&gt;", ">").replaceAll("\\s+", " ").trim();
        return s.length() > 160 ? s.substring(0, 157) + "…" : s;
    }

    private static long time(String s) {
        if (s == null) return 0;
        String[] patterns = {"EEE, dd MMM yyyy HH:mm:ss Z", "EEE, dd MMM yyyy HH:mm:ss zzz", "yyyy-MM-dd'T'HH:mm:ssXXX"};
        for (String pat : patterns) {
            try {
                Date d = new SimpleDateFormat(pat, Locale.US).parse(s.trim());
                if (d != null) return d.getTime();
            } catch (Exception ignored) {
                // next pattern
            }
        }
        return 0;
    }

    private static List<String> parse(String json) {
        List<String> out = new ArrayList<>();
        try {
            JSONArray a = new JSONArray(json);
            for (int i = 0; i < a.length(); i++) out.add(a.getString(i));
        } catch (Exception ignored) {
            // nothing saved yet
        }
        return out;
    }

    void shutdown() {
        io.shutdownNow();
    }
}
