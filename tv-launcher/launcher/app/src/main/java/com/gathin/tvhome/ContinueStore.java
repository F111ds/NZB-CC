package com.gathin.tvhome;

import android.content.Context;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.media.MediaMetadata;
import android.media.session.PlaybackState;
import android.net.Uri;
import android.os.SystemClock;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * NZB-CC cinema preview: what you were part-way through in other apps (Netflix, Prime Video, ...), for the
 * Continue watching row. Two sources:
 *
 * <ol>
 * <li>Android's shared Watch Next list, for the apps that fill it in (an exact episode to reopen);</li>
 * <li>a note kept here of what each app says is playing (its media session: title, picture, position), made by
 *     {@link MediaWatch}, because many apps on Android TV 9 never fill in Watch Next.</li>
 * </ol>
 *
 * Music, short clips (under ten minutes), Kodi (it has its own row) and anything watched to the end are left out.
 */
final class ContinueStore {

    static final class Item {
        String pkg = "", title = "", sub = "", art = "", intentUri = "";
        long pos, dur, at;
        /** From Watch Next (true) or from the media session note (false). */
        boolean watchNext;

        float progress() {
            return dur > 0 ? Math.max(0f, Math.min(1f, pos / (float) dur)) : 0f;
        }

        int minutesLeft() {
            return dur > 0 ? (int) Math.max(1, Math.round((dur - pos) / 60000.0)) : 0;
        }

        String key() {
            return pkg + "|" + title.toLowerCase(Locale.ROOT).trim();
        }
    }

    private static final int KEEP = 30;
    private static final long MIN_DURATION_MS = 10 * 60_000L;
    private static final String[] SKIP = {"music", "spotify", "audible", "podcast", "radio", "soundcloud",
            "org.xbmc.kodi", "com.gathin.tvhome"};
    private static final Object LOCK = new Object();
    private static final Map<String, Long> lastWrite = new HashMap<>();

    private ContinueStore() {
    }

    private static File dir(Context c) {
        File d = new File(c.getFilesDir(), "continue");
        if (!d.isDirectory()) d.mkdirs();
        return d;
    }

    static boolean skipped(String pkg) {
        String p = pkg == null ? "" : pkg.toLowerCase(Locale.ROOT);
        if (p.isEmpty()) return true;
        for (String s : SKIP) if (p.contains(s)) return true;
        return false;
    }

    /** A media session update from {@link MediaWatch}: keep or refresh the note for that title. */
    static void note(Context c, String pkg, MediaMetadata m, PlaybackState s) {
        if (m == null || skipped(pkg)) return;
        String title = text(m, MediaMetadata.METADATA_KEY_DISPLAY_TITLE);
        if (title.isEmpty()) title = text(m, MediaMetadata.METADATA_KEY_TITLE);
        if (title.isEmpty()) return;
        long dur = m.getLong(MediaMetadata.METADATA_KEY_DURATION);
        if (dur > 0 && dur < MIN_DURATION_MS) return;   // trailers, previews, clips
        long pos = s == null ? 0 : s.getPosition();
        if (s != null && s.getState() == PlaybackState.STATE_PLAYING && s.getLastPositionUpdateTime() > 0) {
            pos += (long) ((SystemClock.elapsedRealtime() - s.getLastPositionUpdateTime()) * s.getPlaybackSpeed());
        }
        if (pos < 0) pos = 0;
        boolean playing = s != null && s.getState() == PlaybackState.STATE_PLAYING;

        Item it = new Item();
        it.pkg = pkg;
        it.title = title;
        it.sub = text(m, MediaMetadata.METADATA_KEY_DISPLAY_SUBTITLE);
        if (it.sub.isEmpty()) it.sub = text(m, MediaMetadata.METADATA_KEY_ARTIST);
        it.pos = pos;
        it.dur = dur;
        it.at = System.currentTimeMillis();

        String key = it.key();
        synchronized (LOCK) {
            // While playing, a note every 20 s is plenty; a pause or stop is written at once.
            Long last = lastWrite.get(key);
            if (playing && last != null && it.at - last < 20_000L) return;
            lastWrite.put(key, it.at);

            File artFile = new File(dir(c), "art_" + Integer.toHexString(key.hashCode()) + ".jpg");
            if (!artFile.isFile()) {
                Bitmap b = bitmap(m);
                if (b != null) saveArt(b, artFile);
            }
            if (artFile.isFile()) {
                it.art = artFile.getPath();
            } else {
                String uri = text(m, MediaMetadata.METADATA_KEY_DISPLAY_ICON_URI);
                if (uri.isEmpty()) uri = text(m, MediaMetadata.METADATA_KEY_ART_URI);
                if (uri.isEmpty()) uri = text(m, MediaMetadata.METADATA_KEY_ALBUM_ART_URI);
                it.art = uri;
            }

            List<Item> list = loadNotes(c);
            for (int i = list.size() - 1; i >= 0; i--) if (list.get(i).key().equals(key)) list.remove(i);
            boolean finished = it.dur > 0 && it.pos >= it.dur * 0.95;
            if (!finished) list.add(0, it);
            while (list.size() > KEEP) list.remove(list.size() - 1);
            saveNotes(c, list);
        }
    }

    /** Both sources, newest first, one card per title. Reads the disk: call it off the main thread. */
    static List<Item> load(Context c) {
        List<Item> out = new ArrayList<>(readWatchNext(c));
        synchronized (LOCK) {
            out.addAll(loadNotes(c));
        }
        Collections.sort(out, (a, b) -> Long.compare(b.at, a.at));
        List<Item> unique = new ArrayList<>();
        java.util.Set<String> seen = new java.util.HashSet<>();
        for (Item it : out) if (seen.add(it.key())) unique.add(it);
        return unique;
    }

    /** Takes a title off the row (the card's menu). */
    static void forget(Context c, Item gone) {
        synchronized (LOCK) {
            List<Item> list = loadNotes(c);
            for (int i = list.size() - 1; i >= 0; i--) if (list.get(i).key().equals(gone.key())) list.remove(i);
            saveNotes(c, list);
        }
    }

    // ---- Watch Next ---------------------------------------------------------------------------

    private static List<Item> readWatchNext(Context c) {
        List<Item> out = new ArrayList<>();
        Uri uri = Uri.parse("content://android.media.tv/watch_next_program");
        try (Cursor row = c.getContentResolver().query(uri, null, null, null, null)) {
            if (row == null) return out;
            while (row.moveToNext()) {
                int type = (int) number(row, "watch_next_type", -1);
                if (type != 0 && type != 1) continue;   // WATCH_NEXT_TYPE_CONTINUE, WATCH_NEXT_TYPE_NEXT
                if (number(row, "browsable", 1) == 0) continue;
                Item it = new Item();
                it.watchNext = true;
                it.pkg = field(row, "package_name");
                if (skipped(it.pkg)) continue;
                it.title = field(row, "title");
                if (it.title.isEmpty()) continue;
                String season = field(row, "season_display_number"), episode = field(row, "episode_display_number");
                String epTitle = field(row, "episode_title");
                StringBuilder sub = new StringBuilder();
                if (!season.isEmpty() || !episode.isEmpty()) sub.append("S").append(season).append(" E").append(episode);
                if (!epTitle.isEmpty()) sub.append(sub.length() > 0 ? " · " : "").append(epTitle);
                it.sub = sub.toString();
                it.pos = number(row, "last_playback_position_millis", 0);
                it.dur = number(row, "duration_millis", 0);
                it.at = number(row, "last_engagement_time_utc_millis", 0);
                it.intentUri = field(row, "intent_uri");
                it.art = field(row, "poster_art_uri");
                if (it.art.isEmpty()) it.art = field(row, "thumbnail_uri");
                out.add(it);
            }
        } catch (Exception ignored) {
            // No access on this TV (SecurityException) or no TV provider: the media session note still works.
        }
        return out;
    }

    private static String field(Cursor row, String col) {
        int i = row.getColumnIndex(col);
        if (i < 0 || row.isNull(i)) return "";
        String s = row.getString(i);
        return s == null ? "" : s.trim();
    }

    private static long number(Cursor row, String col, long fallback) {
        int i = row.getColumnIndex(col);
        if (i < 0 || row.isNull(i)) return fallback;
        try {
            return row.getLong(i);
        } catch (Exception e) {
            return fallback;
        }
    }

    // ---- the note file ------------------------------------------------------------------------

    private static List<Item> loadNotes(Context c) {
        List<Item> out = new ArrayList<>();
        File f = new File(dir(c), "notes.json");
        if (!f.isFile()) return out;
        try (InputStream in = new FileInputStream(f)) {
            JSONArray a = new JSONArray(new String(HomeExtras.readAll(in), StandardCharsets.UTF_8));
            for (int i = 0; i < a.length(); i++) {
                JSONObject o = a.getJSONObject(i);
                Item it = new Item();
                it.pkg = o.optString("pkg", "");
                it.title = o.optString("title", "");
                it.sub = o.optString("sub", "");
                it.art = o.optString("art", "");
                it.intentUri = o.optString("intent", "");
                it.pos = o.optLong("pos", 0);
                it.dur = o.optLong("dur", 0);
                it.at = o.optLong("at", 0);
                if (!it.pkg.isEmpty() && !it.title.isEmpty()) out.add(it);
            }
        } catch (Exception ignored) {
            // A damaged note starts the row over.
        }
        return out;
    }

    private static void saveNotes(Context c, List<Item> list) {
        JSONArray a = new JSONArray();
        try {
            for (Item it : list) {
                JSONObject o = new JSONObject();
                o.put("pkg", it.pkg);
                o.put("title", it.title);
                o.put("sub", it.sub);
                o.put("art", it.art);
                o.put("intent", it.intentUri);
                o.put("pos", it.pos);
                o.put("dur", it.dur);
                o.put("at", it.at);
                a.put(o);
            }
            File f = new File(dir(c), "notes.json");
            File tmp = new File(dir(c), "notes.json.tmp");
            try (FileOutputStream out = new FileOutputStream(tmp)) {
                out.write(a.toString().getBytes(StandardCharsets.UTF_8));
            }
            if (!tmp.renameTo(f)) {
                f.delete();
                tmp.renameTo(f);
            }
        } catch (Exception ignored) {
            // Best effort: the next update writes it again.
        }
    }

    private static String text(MediaMetadata m, String key) {
        try {
            CharSequence s = m.getText(key);
            return s == null ? "" : s.toString().trim();
        } catch (Exception e) {
            return "";
        }
    }

    private static Bitmap bitmap(MediaMetadata m) {
        for (String k : new String[]{MediaMetadata.METADATA_KEY_ART, MediaMetadata.METADATA_KEY_ALBUM_ART,
                MediaMetadata.METADATA_KEY_DISPLAY_ICON}) {
            try {
                Bitmap b = m.getBitmap(k);
                if (b != null && b.getWidth() > 0) return b;
            } catch (Exception ignored) {
                // next key
            }
        }
        return null;
    }

    private static void saveArt(Bitmap b, File f) {
        try {
            Bitmap small = b;
            if (b.getWidth() > 640) {
                int h = Math.max(1, Math.round(b.getHeight() * 640f / b.getWidth()));
                small = Bitmap.createScaledBitmap(b, 640, h, true);
            }
            try (FileOutputStream out = new FileOutputStream(f)) {
                small.compress(Bitmap.CompressFormat.JPEG, 85, out);
            }
        } catch (Exception ignored) {
            f.delete();
        }
    }
}
