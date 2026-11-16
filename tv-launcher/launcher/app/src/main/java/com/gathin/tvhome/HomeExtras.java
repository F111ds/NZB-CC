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
 * "kodi_row": true,                     the Kodi row above the dock
 * "kodi_row_server": "http://192.168.1.10:8765",   NZB Command Center's phone server on the PC
 * "kodi_row_token": "...",              its access code (mobile_access_key.txt)
 * "kodi_row_size": 12,
 * "dock_tile_scale": 0.75,              the app tiles at 75 % of their usual size (1 in the centred home)
 * "centered_home": true,                the centred layout (square icon dock, posters across the middle)
 * "dock_cell_dp": 92,                   one dock cell's width in the centred home
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
    /** The dock and grid tiles at this share of their usual size, 0.6 to 1 (smaller leaves the Kodi row more room). */
    final float dockTileScale;
    /** The centred home: posters across the middle, the selected movie under them, a small centred dock of square
     *  icons, a smaller clock line with the weather in the top bar. */
    final boolean centered;
    /** Width of one dock cell in the centred home, in dp: the dock is that times the columns, centred. */
    final int dockCellDp;

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
        kodiRowSize = Math.max(4, Math.min(30, j.optInt("kodi_row_size", 12)));
        kodiRow = j.optBoolean("kodi_row", true);
        centered = j.optBoolean("centered_home", true);
        dockTileScale = (float) Math.max(0.6, Math.min(1.0, j.optDouble("dock_tile_scale", centered ? 1.0 : 0.75)));
        dockCellDp = Math.max(70, Math.min(160, j.optInt("dock_cell_dp", 92)));
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
