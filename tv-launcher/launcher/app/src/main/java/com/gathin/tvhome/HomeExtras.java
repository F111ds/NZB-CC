package com.gathin.tvhome;

import android.content.Context;

import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

/**
 * Settings for this build's own additions (the weather panel and the Kodi row), read from the same
 * config.json a theme uses. Every key is optional:
 *
 * <pre>
 * "weather": true,                      the weather under the clock
 * "weather_place": "Querétaro City",    the name shown
 * "weather_lat": 20.5888, "weather_lon": -100.3899,
 * "weather_unit": "c",                  "f" for Fahrenheit
 * "kodi_row": true,                     the Kodi row (recently watched) above the dock
 * "kodi_row_server": "http://192.168.1.10:8765",   NZB Command Center's phone server on the PC
 * "kodi_row_token": "...",              its access code (mobile_access_key.txt)
 * "kodi_row_size": 5,                   posters in the row (recently watched)
 * "home_apps": ["com.netflix.ninja", "Prime Video", "YouTube", "Kodi"],   only these on Home, in order
 *                                     (package names or labels; [] shows every app; default: these four)
 * "fx": true, "fx_from": "USD", "fx_to": "MXN",   the exchange rate under the clock
 * "fx_key": "art_live_...",            an AllRatesToday key: the live rate instead of the daily one
 * "fx_refresh_minutes": 10,            how often the live rate is read while Home is on screen (5 to 720)
 * "news": true, "news_feeds": ["https://feeds.bbci.co.uk/news/world/rss.xml", "https://feeds.npr.org/1001/rss.xml",
 *                                "https://mexiconewsdaily.com/feed/"],
 *                                     the scrolling headlines above the Kodi row
 * "dock_tile_scale": 0.5,               the app tiles at half their usual size, in a compact centred row
 * "kodi_package": "org.xbmc.kodi",
 * "kodi_local_url": "http://127.0.0.1:8080", "kodi_local_user": "kodi", "kodi_local_password": ""
 * </pre>
 */
final class HomeExtras {
    final boolean weather;
    final String weatherPlace;
    final double weatherLat, weatherLon;
    final boolean fahrenheit;

    final boolean kodiRow;
    final String kodiServer, kodiToken, kodiPackage, kodiLocalUrl, kodiLocalUser, kodiLocalPassword;
    final int kodiRowSize;
    /** The dock and grid tiles at this share of their usual size, 0.35 to 1 (smaller leaves the Kodi row more room). */
    final float dockTileScale;
    /** The dock shows each app's icon alone on the glass, no tile ("dock_icons_only", on by default). */
    final boolean dockIconsOnly;
    /** The exchange rate under the clock ("fx": true), from fx_from to fx_to (USD to MXN by default). */
    final boolean fx;
    /** The news ticker above the Kodi row ("news": true) and its RSS feeds (BBC News World and NPR News by default). */
    final boolean news;
    final java.util.List<String> newsFeeds = new java.util.ArrayList<>();
    final String fxFrom, fxTo;
    /** AllRatesToday key for the live rate ("" uses the daily rate); minutes between live reads. */
    final String fxKey;
    final int fxRefreshMinutes;
    /** The only apps on Home, in order (package names or labels); empty shows every app. */
    final java.util.List<String> homeApps = new java.util.ArrayList<>();

    private HomeExtras(JSONObject j) {
        weather = j.optBoolean("weather", true);
        weatherPlace = j.optString("weather_place", "Querétaro City").trim();
        weatherLat = j.optDouble("weather_lat", 20.5888);
        weatherLon = j.optDouble("weather_lon", -100.3899);
        fahrenheit = "f".equalsIgnoreCase(j.optString("weather_unit", "c"));

        kodiServer = trimSlash(j.optString("kodi_row_server", ""));
        kodiToken = j.optString("kodi_row_token", "").trim();
        kodiPackage = j.optString("kodi_package", "org.xbmc.kodi").trim();
        kodiLocalUrl = trimSlash(j.optString("kodi_local_url", "http://127.0.0.1:8080"));
        kodiLocalUser = j.optString("kodi_local_user", "kodi");
        kodiLocalPassword = j.optString("kodi_local_password", "");
        kodiRowSize = Math.max(1, Math.min(30, j.optInt("kodi_row_size", 5)));
        kodiRow = j.optBoolean("kodi_row", true);
        org.json.JSONArray home = j.optJSONArray("home_apps");
        if (home == null) {
            homeApps.add("com.netflix.ninja|Netflix");
            homeApps.add("com.amazon.amazonvideo.livingroom|Prime Video");
            homeApps.add("com.google.android.youtube.tv|YouTube");
            homeApps.add(kodiPackage + "|Kodi");
        } else {
            for (int i = 0; i < home.length(); i++) {
                String s = home.optString(i, "").trim();
                if (!s.isEmpty()) homeApps.add(s);
            }
        }
        fx = j.optBoolean("fx", true);
        news = j.optBoolean("news", false);   // off: the owner found it too busy; "news": true brings it back
        org.json.JSONArray nf = j.optJSONArray("news_feeds");
        if (nf == null) {
            newsFeeds.add("https://feeds.bbci.co.uk/news/world/rss.xml");
            newsFeeds.add("https://feeds.npr.org/1001/rss.xml");
            newsFeeds.add("https://mexiconewsdaily.com/feed/");
        } else {
            for (int i = 0; i < nf.length(); i++) {
                String u = nf.optString(i, "").trim();
                if (u.startsWith("https://") || u.startsWith("http://")) newsFeeds.add(u);
            }
        }
        String from = j.optString("fx_from", "USD").trim().toUpperCase(java.util.Locale.ROOT);
        String to = j.optString("fx_to", "MXN").trim().toUpperCase(java.util.Locale.ROOT);
        fxFrom = from.matches("[A-Z]{3}") ? from : "USD";
        fxTo = to.matches("[A-Z]{3}") ? to : "MXN";
        fxKey = j.optString("fx_key", "").trim();
        fxRefreshMinutes = Math.max(5, Math.min(720, j.optInt("fx_refresh_minutes", 10)));
        dockIconsOnly = j.optBoolean("dock_icons_only", true);
        dockTileScale = (float) Math.max(0.35, Math.min(1.0, j.optDouble("dock_tile_scale", 0.5)));
    }

    private static String trimSlash(String s) {
        s = s == null ? "" : s.trim();
        while (s.endsWith("/")) s = s.substring(0, s.length() - 1);
        return s;
    }

    static HomeExtras load(Context ctx) {
        JSONObject j = new JSONObject();
        File dir = ctx.getExternalFilesDir(null);
        if (dir != null) {
            File f = new File(dir, Config.FILE_NAME);
            if (f.isFile() && f.length() < 512 * 1024) {
                try (InputStream in = new FileInputStream(f)) {
                    j = new JSONObject(new String(readAll(in), StandardCharsets.UTF_8));
                } catch (Exception ignored) {
                    // A damaged config.json never breaks Home: the defaults apply.
                }
            }
        }
        return new HomeExtras(j);
    }

    static byte[] readAll(InputStream in) throws IOException {
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        byte[] buf = new byte[16384];
        int n;
        while ((n = in.read(buf)) > 0) {
            out.write(buf, 0, n);
            if (out.size() > 8 * 1024 * 1024) throw new IOException("too large");
        }
        return out.toByteArray();
    }
}
