package com.gathin.tvhome;

import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.Outline;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Handler;
import android.util.Base64;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewOutlineProvider;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * The Kodi row above the dock: what was watched last in Kodi (movies and episodes), each poster with a bar
 * showing how far in you are; one press resumes it.
 *
 * <p>The list comes from NZB Command Center's phone server on the PC (GET /api/tv/watched), which keeps
 * Kodi's list while Kodi is closed. When the PC doesn't answer, Kodi on this TV is asked directly (only
 * works while it runs). Either way the last list and its posters are saved here, so the row is on screen
 * at once after the TV is switched on. Anything without a poster is left out.
 *
 * <p>OK opens Kodi and resumes the item: the PC waits for Kodi to start and tells it what to play
 * (POST /api/tv/play); without the PC, this app does the same through Kodi's own JSON-RPC.
 */
final class KodiRow {
    static final long REFRESH_MS = 5 * 60 * 1000L;
    private static final long PLAY_WAIT_MS = 45_000L;

    interface Host {
        void onKodiItemFocused(Item item);
        /** Down from the row. */
        void onKodiRowDown();
        /** Up from the row. */
        void onKodiRowUp();
        /** Focus left the row. */
        void onKodiRowLeft();
        /** The list changed (it may have become empty or non-empty). */
        void onKodiRowChanged();

        /** OK held on a poster: the options menu (resume, play from the start, mark as watched, remove). */
        void onKodiItemMenu(Item item, View anchor);
    }

    static final class Item {
        String id = "", title = "", quality = "", file = "", posterUrl = "", artRef = "", episodeTitle = "";
        int year, runtime, movieid, episodeid, season, episode, remaining;
        boolean isNew, watched;
        /** How far in, 0 to 1 (1 when finished). */
        float progress;
        File poster;

        boolean isEpisode() {
            return episodeid > 0;
        }

        String meta() {
            StringBuilder b = new StringBuilder();
            if (isEpisode()) {
                if (season > 0 || episode > 0) b.append(String.format(Locale.US, "S%d E%d", season, episode));
                if (!episodeTitle.isEmpty()) b.append(b.length() > 0 ? " · " : "").append(episodeTitle);
            } else if (year > 0) {
                b.append(year);
            }
            boolean partway = partway();
            int left = minutesLeft();
            if (partway && left > 0) {
                b.append(b.length() > 0 ? " · " : "").append(duration(left)).append(" left");
                if (runtime > 0) b.append(" of ").append(duration(runtime));
            } else if (runtime > 0) {
                b.append(b.length() > 0 ? " · " : "").append(duration(runtime));
            }
            if (!partway && progress >= 0.97f) b.append(b.length() > 0 ? " · " : "").append("Watched");
            if (!quality.isEmpty()) b.append(b.length() > 0 ? " · " : "").append(quality);
            b.append(b.length() > 0 ? " · " : "").append(partway ? "OK to resume in Kodi" : "OK to play in Kodi");
            return b.toString();
        }

        boolean partway() {
            return progress > 0f && progress < 0.97f;
        }

        /** Minutes still to watch: Kodi's own count, else worked out from the running time and the bar. */
        int minutesLeft() {
            if (!partway()) return 0;
            if (remaining > 0) return remaining;
            return runtime > 0 ? Math.max(1, Math.round(runtime * (1f - progress))) : 0;
        }

        static String duration(int minutes) {
            return (minutes / 60 > 0 ? (minutes / 60) + "h " : "") + String.format(Locale.US, "%02dm", minutes % 60);
        }
    }

    private final Context ctx;
    private final HomeExtras extras;
    private final Handler main;
    private final Host host;
    private final float density;
    private final File dir, posters, saved;
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final ExecutorService player = Executors.newSingleThreadExecutor();
    private volatile boolean busy;
    private long readAt;

    final RecyclerView list;
    private final Adapter adapter = new Adapter();
    private List<Item> items = new ArrayList<>();
    private int lastIndex;
    private int posterW, posterH;

    KodiRow(Context ctx, HomeExtras extras, Handler main, RecyclerView list, Host host) {
        this.ctx = ctx;
        this.extras = extras;
        this.main = main;
        this.list = list;
        this.host = host;
        density = ctx.getResources().getDisplayMetrics().density;
        dir = new File(ctx.getFilesDir(), "kodi_row");
        posters = new File(dir, "posters");
        saved = new File(dir, "row.json");
        posterH = Math.round(110 * density);
        posterW = posterH * 2 / 3;
        list.setLayoutManager(new LinearLayoutManager(ctx, LinearLayoutManager.HORIZONTAL, false));
        list.setItemAnimator(null);
        list.setAdapter(adapter);
        items = load();
    }

    boolean enabled() {
        return extras.kodiRow && (!extras.kodiServer.isEmpty() || !extras.kodiLocalUrl.isEmpty());
    }

    boolean hasItems() {
        return enabled() && !items.isEmpty();
    }

    int count() {
        return items.size();
    }

    /** Sizes the posters to the height the row has on screen. */
    void setPosterHeight(int px) {
        int h = Math.max(Math.round(60 * density), px);
        if (Math.abs(h - posterH) < 2) return;
        posterH = h;
        posterW = h * 2 / 3;
        adapter.notifyDataSetChanged();
    }

    /** Focus on the row: the poster that had it last. False when the row has nothing to focus. */
    boolean focus() {
        if (!hasItems()) return false;
        int i = Math.max(0, Math.min(lastIndex, items.size() - 1));
        RecyclerView.ViewHolder vh = list.findViewHolderForAdapterPosition(i);
        if (vh != null && vh.itemView.requestFocus()) return true;
        list.scrollToPosition(i);
        list.post(() -> {
            RecyclerView.ViewHolder h = list.findViewHolderForAdapterPosition(i);
            if (h != null) h.itemView.requestFocus();
        });
        return true;
    }

    // ---- data ---------------------------------------------------------------------------------

    private volatile boolean freshNext;

    /** Home came back (from Kodi, say): read the list now, with the PC reading Kodi right away too. */
    void refreshNow() {
        if (!enabled() || busy || System.currentTimeMillis() - readAt < 15_000L) return;
        freshNext = true;
        readAt = 0;
        refreshIfStale();
    }

    void refreshIfStale() {
        if (!enabled() || busy || System.currentTimeMillis() - readAt < REFRESH_MS) return;
        busy = true;
        try {
            io.execute(this::read);
        } catch (java.util.concurrent.RejectedExecutionException e) {
            busy = false;
        }
    }

    private void read() {
        List<Item> fresh = null;
        try {
            if (!extras.kodiServer.isEmpty()) fresh = readFromPc();
        } catch (Exception ignored) {
            fresh = null;
        }
        if (fresh == null) {
            try {
                fresh = readFromKodi();
            } catch (Exception ignored) {
                fresh = null;
            }
        }
        final List<Item> result = fresh;
        if (result != null) save(result);
        main.post(() -> {
            busy = false;
            readAt = System.currentTimeMillis();
            if (result != null) {
                items = result;
                adapter.notifyDataSetChanged();
                host.onKodiRowChanged();
            }
        });
    }

    private List<Item> readFromPc() throws Exception {
        boolean fresh = freshNext;
        freshNext = false;
        JSONObject j = new JSONObject(new String(http("GET", extras.kodiServer + "/api/tv/watched" + (fresh ? "?fresh=1" : ""),
                null, true, fresh ? 20000 : 8000), StandardCharsets.UTF_8));
        JSONArray a = j.getJSONArray("items");
        List<Item> out = new ArrayList<>();
        for (int i = 0; i < a.length() && out.size() < extras.kodiRowSize; i++) {
            JSONObject o = a.getJSONObject(i);
            Item it = fromJson(o);
            if (it.id.isEmpty()) continue;
            it.posterUrl = o.optString("poster", "");
            it.poster = posterFile(it.id);
            if (!it.posterUrl.isEmpty() && !it.poster.isFile()) {
                try {
                    writeFile(it.poster, http("GET", extras.kodiServer + it.posterUrl, null, true, 10000));
                } catch (IOException ignored) {
                    // no poster this time: left out of the row until it has one
                }
            }
            if (it.poster.isFile() && !hiddenIds().contains(it.id)) out.add(it);
        }
        return out;
    }

    /** Kodi on this TV, asked directly: movies and episodes started or finished, the last one played first. */
    private List<Item> readFromKodi() throws Exception {
        JSONObject seen = new JSONObject().put("or", new JSONArray()
                .put(new JSONObject().put("field", "inprogress").put("operator", "true").put("value", ""))
                .put(new JSONObject().put("field", "playcount").put("operator", "greaterthan").put("value", "0")));
        JSONObject sort = new JSONObject().put("method", "lastplayed").put("order", "descending");
        JSONObject limits = new JSONObject().put("start", 0).put("end", extras.kodiRowSize * 3);
        JSONObject movies = rpc("VideoLibrary.GetMovies", new JSONObject()
                .put("properties", new JSONArray().put("title").put("year").put("runtime").put("file").put("art")
                        .put("playcount").put("resume").put("lastplayed"))
                .put("filter", seen).put("sort", sort).put("limits", limits), 6000);
        JSONObject episodes = rpc("VideoLibrary.GetEpisodes", new JSONObject()
                .put("properties", new JSONArray().put("title").put("showtitle").put("season").put("episode")
                        .put("runtime").put("file").put("art").put("playcount").put("resume").put("lastplayed"))
                .put("filter", seen).put("sort", sort).put("limits", limits), 6000);
        List<Object[]> all = new ArrayList<>();   // {lastplayed, item}
        JSONArray m = movies.optJSONArray("movies");
        for (int i = 0; m != null && i < m.length(); i++) {
            JSONObject o = m.getJSONObject(i);
            Item it = new Item();
            it.movieid = o.optInt("movieid");
            it.id = "m" + it.movieid;
            it.title = o.optString("title", "Untitled");
            it.year = o.optInt("year");
            JSONObject art = o.optJSONObject("art");
            it.artRef = art == null ? "" : art.optString("poster", "");
            fillProgress(it, o);
            all.add(new Object[]{o.optString("lastplayed", ""), it});
        }
        JSONArray e = episodes.optJSONArray("episodes");
        for (int i = 0; e != null && i < e.length(); i++) {
            JSONObject o = e.getJSONObject(i);
            Item it = new Item();
            it.episodeid = o.optInt("episodeid");
            it.id = "e" + it.episodeid;
            it.title = o.optString("showtitle", "");
            it.episodeTitle = o.optString("title", "");
            if (it.title.isEmpty()) it.title = it.episodeTitle;
            it.season = o.optInt("season");
            it.episode = o.optInt("episode");
            JSONObject art = o.optJSONObject("art");
            it.artRef = art == null ? "" : art.optString("tvshow.poster", art.optString("season.poster", ""));
            fillProgress(it, o);
            all.add(new Object[]{o.optString("lastplayed", ""), it});
        }
        all.sort((a, b) -> ((String) b[0]).compareTo((String) a[0]));   // "yyyy-MM-dd HH:mm:ss" sorts as text
        List<Item> out = new ArrayList<>();
        for (Object[] pair : all) {
            if (out.size() >= extras.kodiRowSize) break;
            Item it = (Item) pair[1];
            it.poster = posterFile(it.id);
            if (!it.artRef.isEmpty() && !it.poster.isFile()) {
                try {
                    writeFile(it.poster, http("GET", extras.kodiLocalUrl + "/image/"
                            + URLEncoder.encode(it.artRef, "UTF-8"), null, false, 10000));
                } catch (IOException ignored) {
                    // left out until the poster can be fetched
                }
            }
            if (it.poster.isFile() && !hiddenIds().contains(it.id)) out.add(it);
        }
        return out;
    }

    private static void fillProgress(Item it, JSONObject o) {
        it.file = o.optString("file", "");
        it.watched = o.optInt("playcount") > 0;
        JSONObject resume = o.optJSONObject("resume");
        double pos = resume == null ? 0 : resume.optDouble("position", 0);
        double total = resume == null ? 0 : resume.optDouble("total", 0);
        if (total <= 0) total = o.optInt("runtime");
        it.runtime = (int) Math.round(total / 60);
        if (pos > 0 && total > 0) {
            it.progress = (float) Math.min(1, pos / total);
            it.remaining = (int) Math.round((total - pos) / 60);
        } else {
            it.progress = it.watched ? 1f : 0f;
        }
    }

    private static Item fromJson(JSONObject o) {
        Item it = new Item();
        it.id = o.optString("id", "").replaceAll("[^A-Za-z0-9_]", "");
        it.title = o.optString("title", "Untitled");
        it.year = o.optInt("year");
        it.runtime = o.optInt("runtime");
        it.quality = o.optString("quality", "");
        it.isNew = o.optBoolean("new");
        it.watched = o.optBoolean("watched");
        it.movieid = o.optInt("movieid");
        it.episodeid = o.optInt("episodeid");
        it.season = o.optInt("season");
        it.episode = o.optInt("episode");
        it.episodeTitle = o.optString("episode_title", "");
        it.progress = (float) Math.max(0, Math.min(1, o.optDouble("progress", it.watched ? 1 : 0)));
        it.remaining = o.optInt("remaining");
        it.file = o.optString("file", "");
        if ("null".equals(it.file)) it.file = "";
        if ("null".equals(it.episodeTitle)) it.episodeTitle = "";
        if ("null".equals(it.quality)) it.quality = "";
        return it;
    }

    private File posterFile(String id) {
        return new File(posters, id + ".img");
    }

    private void save(List<Item> list) {
        try {
            JSONArray a = new JSONArray();
            Set<String> keep = new HashSet<>();
            for (Item it : list) {
                keep.add(it.id + ".img");
                a.put(new JSONObject().put("id", it.id).put("title", it.title).put("year", it.year)
                        .put("runtime", it.runtime).put("quality", it.quality).put("new", it.isNew)
                        .put("watched", it.watched).put("movieid", it.movieid).put("file", it.file)
                        .put("episodeid", it.episodeid).put("season", it.season).put("episode", it.episode)
                        .put("episode_title", it.episodeTitle).put("progress", it.progress).put("remaining", it.remaining));
            }
            writeFile(saved, new JSONObject().put("items", a).toString().getBytes(StandardCharsets.UTF_8));
            File[] old = posters.listFiles();
            if (old != null) for (File f : old) if (!keep.contains(f.getName())) //noinspection ResultOfMethodCallIgnored
                f.delete();
        } catch (Exception ignored) {
            // the row on screen is still right; it is saved again next time
        }
    }

    private List<Item> load() {
        List<Item> out = new ArrayList<>();
        try (InputStream in = new java.io.FileInputStream(saved)) {
            JSONArray a = new JSONObject(new String(HomeExtras.readAll(in), StandardCharsets.UTF_8)).getJSONArray("items");
            for (int i = 0; i < a.length(); i++) {
                Item it = fromJson(a.getJSONObject(i));
                if (it.id.isEmpty()) continue;
                it.poster = posterFile(it.id);
                if (it.poster.isFile() && out.size() < extras.kodiRowSize && !hiddenIds().contains(it.id)) out.add(it);
            }
        } catch (Exception ignored) {
            // nothing saved yet
        }
        return out;
    }

    private static void writeFile(File f, byte[] data) throws IOException {
        File parent = f.getParentFile();
        if (parent != null && !parent.isDirectory() && !parent.mkdirs()) throw new IOException("no folder");
        File tmp = new File(f.getPath() + ".tmp");
        try (OutputStream out = new FileOutputStream(tmp)) {
            out.write(data);
        }
        if (!tmp.renameTo(f)) throw new IOException("rename failed");
    }

    private byte[] http(String method, String url, byte[] body, boolean pc, int timeoutMs) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(Math.min(timeoutMs, 4000));
        c.setReadTimeout(timeoutMs);
        c.setRequestMethod(method);
        if (pc) {
            c.setRequestProperty("X-Mobile-Token", extras.kodiToken);
        } else if (!extras.kodiLocalUser.isEmpty() || !extras.kodiLocalPassword.isEmpty()) {
            String auth = extras.kodiLocalUser + ":" + extras.kodiLocalPassword;
            c.setRequestProperty("Authorization", "Basic "
                    + Base64.encodeToString(auth.getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP));
        }
        try {
            if (body != null) {
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                try (OutputStream out = c.getOutputStream()) {
                    out.write(body);
                }
            }
            int code = c.getResponseCode();
            if (code / 100 != 2) {
                String msg = "";
                try (InputStream err = c.getErrorStream()) {
                    if (err != null) msg = new JSONObject(new String(HomeExtras.readAll(err), StandardCharsets.UTF_8))
                            .optString("error", "");
                } catch (Exception ignored) {
                    // no readable reason
                }
                throw new HttpError(code, msg);
            }
            try (InputStream in = c.getInputStream()) {
                return HomeExtras.readAll(in);
            }
        } finally {
            c.disconnect();
        }
    }

    static final class HttpError extends IOException {
        final int code;

        HttpError(int code, String msg) {
            super(msg == null || msg.isEmpty() ? "HTTP " + code : msg);
            this.code = code;
        }
    }

    private JSONObject rpc(String method, JSONObject params, int timeoutMs) throws Exception {
        JSONObject req = new JSONObject().put("jsonrpc", "2.0").put("id", 1).put("method", method);
        if (params != null) req.put("params", params);
        JSONObject res = new JSONObject(new String(http("POST", extras.kodiLocalUrl + "/jsonrpc",
                req.toString().getBytes(StandardCharsets.UTF_8), false, timeoutMs), StandardCharsets.UTF_8));
        if (res.has("error")) throw new IOException(res.getJSONObject("error").optString("message", "Kodi error"));
        Object r = res.opt("result");
        return r instanceof JSONObject ? (JSONObject) r : new JSONObject();
    }

    // ---- playing ------------------------------------------------------------------------------

    void play(Item it) {
        play(it, false);
    }

    /** Opens Kodi and plays the item: where it was left, or from the beginning with fromStart. */
    void play(Item it, boolean fromStart) {
        Intent launch = ctx.getPackageManager().getLaunchIntentForPackage(extras.kodiPackage);
        if (launch == null) launch = ctx.getPackageManager().getLeanbackLaunchIntentForPackage(extras.kodiPackage);
        if (launch == null) {
            Toast.makeText(ctx, "Kodi isn't installed on this TV.", Toast.LENGTH_LONG).show();
            return;
        }
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        boolean partway = it.progress > 0f && it.progress < 0.97f;
        Toast.makeText(ctx, (partway ? "Resuming “" : "Starting “") + it.title + "” in Kodi…", Toast.LENGTH_SHORT).show();
        ctx.startActivity(launch);
        final Context app = ctx.getApplicationContext();
        player.execute(() -> {
            String problem = null;
            boolean done = false;
            if (!extras.kodiServer.isEmpty()) {
                try {
                    http("POST", extras.kodiServer + "/api/tv/play",
                            new JSONObject().put("id", it.id).put("from_start", fromStart).toString()
                                    .getBytes(StandardCharsets.UTF_8), true, 60_000);
                    done = true;
                } catch (HttpError e) {
                    // The PC answered: Kodi didn't start in time, or it can't find the movie yet.
                    problem = e.getMessage();
                    done = e.code != 401 && e.code != 403 && e.code != 404;
                } catch (Exception e) {
                    problem = null;   // the PC is off: play it from here
                }
            }
            if (!done) {
                try {
                    playHere(it, fromStart);
                    problem = null;
                } catch (Exception e) {
                    if (problem == null) problem = e.getMessage();
                }
            }
            if (problem != null) {
                final String msg = problem;
                main.post(() -> Toast.makeText(app, "Couldn't play “" + it.title + "”: " + msg, Toast.LENGTH_LONG).show());
            }
        });
    }

    private void playHere(Item it, boolean fromStart) throws Exception {
        JSONObject item;
        if (it.episodeid > 0) item = new JSONObject().put("episodeid", it.episodeid);
        else if (it.movieid > 0) item = new JSONObject().put("movieid", it.movieid);
        else if (!it.file.isEmpty()) item = new JSONObject().put("file", it.file);
        else throw new IOException("Kodi doesn't have it yet, and the PC didn't answer");
        long deadline = System.currentTimeMillis() + PLAY_WAIT_MS;
        while (true) {
            try {
                rpc("JSONRPC.Ping", null, 3000);
                break;
            } catch (Exception e) {
                if (System.currentTimeMillis() > deadline) throw new IOException("Kodi didn't start in time");
                Thread.sleep(1500);
            }
        }
        rpc("Player.Open", new JSONObject().put("item", item)
                .put("options", new JSONObject().put("resume", !fromStart && it.progress > 0f && it.progress < 0.97f)), 15000);
    }

    // ---- the hold-OK menu ---------------------------------------------------------------------

    /** Marks the item watched in Kodi (through the PC, else Kodi on this TV) and shows it finished at once. */
    void markWatched(Item it) {
        it.progress = 1f;
        it.remaining = 0;
        it.watched = true;
        adapter.notifyDataSetChanged();
        final Context app = ctx.getApplicationContext();
        final List<Item> snapshot = new ArrayList<>(items);
        io.execute(() -> {
            boolean ok = false;
            if (!extras.kodiServer.isEmpty()) {
                try {
                    http("POST", extras.kodiServer + "/api/tv/mark_watched",
                            new JSONObject().put("id", it.id).toString().getBytes(StandardCharsets.UTF_8), true, 15000);
                    ok = true;
                } catch (Exception ignored) {
                    // the PC is off: tell Kodi on this TV
                }
            }
            if (!ok) {
                try {
                    JSONObject done = new JSONObject().put("playcount", 1)
                            .put("resume", new JSONObject().put("position", 0).put("total", 0));
                    if (it.episodeid > 0) {
                        rpc("VideoLibrary.SetEpisodeDetails", done.put("episodeid", it.episodeid), 6000);
                        ok = true;
                    } else if (it.movieid > 0) {
                        rpc("VideoLibrary.SetMovieDetails", done.put("movieid", it.movieid), 6000);
                        ok = true;
                    }
                } catch (Exception ignored) {
                    ok = false;
                }
            }
            save(snapshot);
            if (!ok) main.post(() -> Toast.makeText(app, "Couldn't reach Kodi to mark “" + it.title + "” watched.",
                    Toast.LENGTH_LONG).show());
        });
    }

    /** Takes the item off this row for good (remembered on the TV); it stays in Kodi as it is. */
    void remove(Item it) {
        Set<String> hidden = hiddenIds();
        hidden.add(it.id);
        ctx.getSharedPreferences("kodi_row", Context.MODE_PRIVATE).edit().putStringSet("hidden", hidden).apply();
        List<Item> next = new ArrayList<>(items);
        next.remove(it);
        items = next;
        adapter.notifyDataSetChanged();
        final List<Item> snapshot = new ArrayList<>(items);
        io.execute(() -> save(snapshot));
        host.onKodiRowChanged();
        readAt = 0;   // read again, so the next item moves up into the row
        refreshIfStale();
    }

    private Set<String> hiddenIds() {
        return new HashSet<>(ctx.getSharedPreferences("kodi_row", Context.MODE_PRIVATE)
                .getStringSet("hidden", new HashSet<>()));
    }

    void shutdown() {
        io.shutdownNow();
        player.shutdown();
    }

    // ---- cards --------------------------------------------------------------------------------

    private final class Adapter extends RecyclerView.Adapter<Holder> {
        @NonNull
        @Override
        public Holder onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
            return new Holder(build());
        }

        @Override
        public void onBindViewHolder(@NonNull Holder h, int position) {
            h.bind(items.get(position), position);
        }

        @Override
        public int getItemCount() {
            return items.size();
        }
    }

    private int dp(float v) {
        return Math.round(v * density);
    }

    private FrameLayout build() {
        FrameLayout cell = new FrameLayout(ctx);
        cell.setFocusable(true);
        cell.setFocusableInTouchMode(false);
        cell.setClipChildren(false);

        FrameLayout card = new FrameLayout(ctx);
        card.setId(View.generateViewId());
        GradientDrawable bg = new GradientDrawable(GradientDrawable.Orientation.TL_BR,
                new int[]{0xFF1E2B40, 0xFF0B111C});
        bg.setCornerRadius(dp(8));
        card.setBackground(bg);
        card.setOutlineProvider(new ViewOutlineProvider() {
            @Override
            public void getOutline(View view, Outline outline) {
                outline.setRoundRect(0, 0, view.getWidth(), view.getHeight(), dp(8));
            }
        });
        card.setClipToOutline(true);
        card.setElevation(dp(4));

        TextView title = new TextView(ctx);
        title.setTextColor(Color.WHITE);
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setAllCaps(true);
        title.setMaxLines(3);
        title.setGravity(Gravity.BOTTOM | Gravity.START);
        title.setPadding(dp(7), dp(7), dp(7), dp(8));
        card.addView(title, new FrameLayout.LayoutParams(-1, -1));

        ImageView img = new ImageView(ctx);
        img.setScaleType(ImageView.ScaleType.CENTER_CROP);
        card.addView(img, new FrameLayout.LayoutParams(-1, -1));

        TextView badge = new TextView(ctx);
        badge.setText("NEW");
        badge.setTextColor(Color.WHITE);
        badge.setTextSize(TypedValue.COMPLEX_UNIT_SP, 8);
        badge.setTypeface(Typeface.DEFAULT_BOLD);
        badge.setPadding(dp(4), dp(1), dp(4), dp(1));
        GradientDrawable bb = new GradientDrawable();
        bb.setColor(0xFF17B2E7);
        bb.setCornerRadius(dp(3));
        badge.setBackground(bb);
        FrameLayout.LayoutParams blp = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.END);
        blp.setMargins(0, dp(5), dp(5), 0);
        card.addView(badge, blp);

        TextView seen = new TextView(ctx);
        seen.setText("✓");
        seen.setTextColor(Color.WHITE);
        seen.setTextSize(TypedValue.COMPLEX_UNIT_SP, 9);
        seen.setGravity(Gravity.CENTER);
        GradientDrawable sb = new GradientDrawable();
        sb.setShape(GradientDrawable.OVAL);
        sb.setColor(0x59000000);   // NZB-CC: softer
        seen.setBackground(sb);
        seen.setTextSize(TypedValue.COMPLEX_UNIT_SP, 8);
        seen.setElevation(dp(1));
        FrameLayout.LayoutParams slp = new FrameLayout.LayoutParams(dp(13), dp(13), Gravity.TOP | Gravity.END);
        slp.setMargins(0, dp(5), dp(5), 0);
        card.addView(seen, slp);

        // How far in: a thin track along the poster's foot, filled to the share watched.
        FrameLayout track = new FrameLayout(ctx);
        track.setBackgroundColor(0x59000000);
        View fill = new View(ctx);
        fill.setBackgroundColor(0xFF17B2E7);
        track.addView(fill, new FrameLayout.LayoutParams(0, -1, Gravity.START));
        card.addView(track, new FrameLayout.LayoutParams(-1, dp(3), Gravity.BOTTOM));

        // Time left, on the poster just above the bar; shown while the poster has focus.
        TextView left = new TextView(ctx);
        left.setTextColor(Color.WHITE);
        left.setTextSize(TypedValue.COMPLEX_UNIT_SP, 10);
        left.setTypeface(UiFont.get(ctx, R.string.font_medium));
        left.setPadding(dp(6), dp(2), dp(6), dp(2));
        GradientDrawable lb = new GradientDrawable();
        lb.setColor(0xCC000000);
        lb.setCornerRadius(dp(9));
        left.setBackground(lb);
        left.setAlpha(0f);
        FrameLayout.LayoutParams llp = new FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM | Gravity.END);
        llp.setMargins(0, 0, dp(6), dp(10));
        card.addView(left, llp);

        View ring = new View(ctx);
        GradientDrawable rd = new GradientDrawable();
        rd.setCornerRadius(dp(8));
        rd.setStroke(dp(2.5f), Color.WHITE);
        rd.setColor(Color.TRANSPARENT);
        ring.setBackground(rd);
        ring.setAlpha(0f);
        card.addView(ring, new FrameLayout.LayoutParams(-1, -1));

        cell.addView(card);
        cell.setTag(new View[]{card, title, img, badge, seen, ring, track, fill, left});
        return cell;
    }

    private final class Holder extends RecyclerView.ViewHolder {
        final View card, ring, badge, seen, track, fill;
        final TextView left;
        final TextView title;
        final ImageView img;
        Item item;

        Holder(FrameLayout cell) {
            super(cell);
            View[] v = (View[]) cell.getTag();
            card = v[0];
            title = (TextView) v[1];
            img = (ImageView) v[2];
            badge = v[3];
            seen = v[4];
            ring = v[5];
            track = v[6];
            fill = v[7];
            left = (TextView) v[8];
            cell.setOnFocusChangeListener((view, has) -> {
                float s = has ? 1.06f : 1f;   // NZB-CC: the bigger posters grow less, clear of the heading
                card.animate().scaleX(s).scaleY(s).translationZ(has ? dp(6) : 0).setDuration(140).start();
                ring.animate().alpha(has ? 1f : 0f).setDuration(140).start();
                left.animate().alpha(has && left.getText().length() > 0 ? 1f : 0f).setDuration(140).start();
                if (has && item != null) {
                    lastIndex = getBindingAdapterPosition();
                    host.onKodiItemFocused(item);
                } else if (!has) {
                    list.post(() -> {
                        if (!list.hasFocus()) host.onKodiRowLeft();
                    });
                }
            });
            cell.setOnClickListener(view -> {
                if (item != null) play(item);
            });
            cell.setOnLongClickListener(view -> {
                if (item != null) host.onKodiItemMenu(item, card);
                return true;
            });
            cell.setOnKeyListener((view, code, e) -> {
                if (e.getAction() != KeyEvent.ACTION_DOWN) return code == KeyEvent.KEYCODE_DPAD_UP
                        || code == KeyEvent.KEYCODE_DPAD_DOWN;
                int pos = getBindingAdapterPosition();
                if (code == KeyEvent.KEYCODE_DPAD_DOWN) {
                    host.onKodiRowDown();
                    return true;
                }
                if (code == KeyEvent.KEYCODE_DPAD_UP) {
                    host.onKodiRowUp();
                    return true;
                }
                // The row's ends: stay put instead of focus escaping to the clock or the dock.
                if (code == KeyEvent.KEYCODE_DPAD_LEFT && pos == 0) return true;
                return code == KeyEvent.KEYCODE_DPAD_RIGHT && pos == items.size() - 1;
            });
        }

        void bind(Item it, int position) {
            item = it;
            ViewGroup.MarginLayoutParams lp = new ViewGroup.MarginLayoutParams(posterW, posterH);
            int gap = dp(12);
            lp.setMargins(gap / 2, dp(8), gap / 2, dp(8));
            itemView.setLayoutParams(new RecyclerView.LayoutParams(lp));
            card.setLayoutParams(new FrameLayout.LayoutParams(posterW, posterH));
            card.setPivotX(posterW / 2f);
            card.setPivotY(posterH / 2f);
            title.setText(it.title);
            badge.setVisibility(View.GONE);
            seen.setVisibility(it.progress >= 0.97f ? View.VISIBLE : View.GONE);
            track.setVisibility(it.progress > 0f ? View.VISIBLE : View.GONE);
            int minutes = it.minutesLeft();
            left.setText(minutes > 0 ? Item.duration(minutes) + " left" : "");
            left.setAlpha(itemView.hasFocus() && minutes > 0 ? 1f : 0f);
            fill.getLayoutParams().width = Math.round(posterW * Math.min(1f, it.progress));
            fill.requestLayout();
            Bitmap bm = null;
            if (it.poster != null && it.poster.isFile()) {
                BitmapFactory.Options o = new BitmapFactory.Options();
                o.inJustDecodeBounds = true;
                BitmapFactory.decodeFile(it.poster.getPath(), o);
                int sample = 1;
                while (o.outHeight / (sample * 2) >= posterH) sample *= 2;
                o = new BitmapFactory.Options();
                o.inSampleSize = sample;
                o.inPreferredConfig = Bitmap.Config.RGB_565;
                bm = BitmapFactory.decodeFile(it.poster.getPath(), o);
            }
            img.setImageBitmap(bm);
            img.setVisibility(bm == null ? View.GONE : View.VISIBLE);
            itemView.setContentDescription(it.title);
        }
    }
}
