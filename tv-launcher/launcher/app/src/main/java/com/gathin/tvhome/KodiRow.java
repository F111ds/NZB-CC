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
 * The Kodi row above the dock: the newest movies with their posters, one press to play.
 *
 * <p>The list comes from NZB Command Center's phone server on the PC (GET /api/tv/recent), which
 * knows the newest downloads and keeps Kodi's list while Kodi is closed. When the PC doesn't answer,
 * Kodi on this TV is asked directly (only works while it runs). Either way the last list and its
 * posters are saved here, so the row is on screen at once after the TV is switched on.
 *
 * <p>OK opens Kodi and plays the movie: the PC waits for Kodi to start and tells it what to play
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
    }

    static final class Item {
        String id = "", title = "", quality = "", file = "", posterUrl = "", artRef = "";
        int year, runtime, movieid;
        boolean isNew, watched;
        File poster;

        String meta() {
            StringBuilder b = new StringBuilder();
            if (year > 0) b.append(year);
            if (runtime > 0) b.append(b.length() > 0 ? " · " : "").append(runtime / 60 > 0 ? (runtime / 60) + "h " : "")
                    .append(String.format(Locale.US, "%02dm", runtime % 60));
            if (!quality.isEmpty()) b.append(b.length() > 0 ? " · " : "").append(quality);
            b.append(b.length() > 0 ? " · " : "").append("OK to play in Kodi");
            return b.toString();
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
        JSONObject j = new JSONObject(new String(http("GET", extras.kodiServer + "/api/tv/recent", null, true, 8000),
                StandardCharsets.UTF_8));
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
                    // The poster can come next time; the card shows the title meanwhile.
                }
            }
            out.add(it);
        }
        return out;
    }

    private List<Item> readFromKodi() throws Exception {
        JSONObject params = new JSONObject()
                .put("properties", new JSONArray().put("title").put("year").put("runtime").put("file").put("art")
                        .put("playcount").put("dateadded"))
                .put("limits", new JSONObject().put("start", 0).put("end", extras.kodiRowSize));
        JSONObject res = rpc("VideoLibrary.GetRecentlyAddedMovies", params, 6000);
        JSONArray movies = res.optJSONArray("movies");
        List<Item> out = new ArrayList<>();
        if (movies == null) return out;
        long weekAgo = System.currentTimeMillis() - 3 * 86400_000L;
        java.text.SimpleDateFormat f = new java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US);
        for (int i = 0; i < movies.length(); i++) {
            JSONObject m = movies.getJSONObject(i);
            Item it = new Item();
            it.movieid = m.optInt("movieid");
            it.id = "k" + it.movieid;
            it.title = m.optString("title", "Untitled");
            it.year = m.optInt("year");
            it.runtime = m.optInt("runtime") / 60;
            it.file = m.optString("file", "");
            it.watched = m.optInt("playcount") > 0;
            try {
                java.util.Date d = f.parse(m.optString("dateadded", ""));
                it.isNew = !it.watched && d != null && d.getTime() >= weekAgo;
            } catch (java.text.ParseException ignored) {
                it.isNew = false;
            }
            JSONObject art = m.optJSONObject("art");
            it.artRef = art == null ? "" : art.optString("poster", "");
            it.poster = posterFile(it.id);
            if (!it.artRef.isEmpty() && !it.poster.isFile()) {
                try {
                    writeFile(it.poster, http("GET", extras.kodiLocalUrl + "/image/"
                            + URLEncoder.encode(it.artRef, "UTF-8"), null, false, 10000));
                } catch (IOException ignored) {
                    // shown with its title until the poster can be fetched
                }
            }
            out.add(it);
        }
        return out;
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
        it.file = o.optString("file", "");
        if ("null".equals(it.file)) it.file = "";
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
                        .put("watched", it.watched).put("movieid", it.movieid).put("file", it.file));
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
                out.add(it);
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
        Intent launch = ctx.getPackageManager().getLaunchIntentForPackage(extras.kodiPackage);
        if (launch == null) launch = ctx.getPackageManager().getLeanbackLaunchIntentForPackage(extras.kodiPackage);
        if (launch == null) {
            Toast.makeText(ctx, "Kodi isn't installed on this TV.", Toast.LENGTH_LONG).show();
            return;
        }
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        Toast.makeText(ctx, "Starting “" + it.title + "” in Kodi…", Toast.LENGTH_SHORT).show();
        ctx.startActivity(launch);
        final Context app = ctx.getApplicationContext();
        player.execute(() -> {
            String problem = null;
            boolean done = false;
            if (!extras.kodiServer.isEmpty()) {
                try {
                    http("POST", extras.kodiServer + "/api/tv/play",
                            new JSONObject().put("id", it.id).toString().getBytes(StandardCharsets.UTF_8), true, 60_000);
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
                    playHere(it);
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

    private void playHere(Item it) throws Exception {
        JSONObject item;
        if (it.movieid > 0) item = new JSONObject().put("movieid", it.movieid);
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
        rpc("Player.Open", new JSONObject().put("item", item), 15000);
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
        sb.setColor(0x99000000);
        seen.setBackground(sb);
        FrameLayout.LayoutParams slp = new FrameLayout.LayoutParams(dp(15), dp(15), Gravity.TOP | Gravity.END);
        slp.setMargins(0, dp(5), dp(5), 0);
        card.addView(seen, slp);

        View ring = new View(ctx);
        GradientDrawable rd = new GradientDrawable();
        rd.setCornerRadius(dp(8));
        rd.setStroke(dp(2.5f), Color.WHITE);
        rd.setColor(Color.TRANSPARENT);
        ring.setBackground(rd);
        ring.setAlpha(0f);
        card.addView(ring, new FrameLayout.LayoutParams(-1, -1));

        cell.addView(card);
        cell.setTag(new View[]{card, title, img, badge, seen, ring});
        return cell;
    }

    private final class Holder extends RecyclerView.ViewHolder {
        final View card, ring, badge, seen;
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
            cell.setOnFocusChangeListener((view, has) -> {
                float s = has ? 1.1f : 1f;
                card.animate().scaleX(s).scaleY(s).translationZ(has ? dp(6) : 0).setDuration(140).start();
                ring.animate().alpha(has ? 1f : 0f).setDuration(140).start();
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
            lp.setMargins(position == 0 ? dp(6) : gap / 2, dp(8), gap / 2, dp(8));
            itemView.setLayoutParams(new RecyclerView.LayoutParams(lp));
            card.setLayoutParams(new FrameLayout.LayoutParams(posterW, posterH));
            card.setPivotX(posterW / 2f);
            card.setPivotY(posterH / 2f);
            title.setText(it.title);
            badge.setVisibility(it.isNew ? View.VISIBLE : View.GONE);
            seen.setVisibility(!it.isNew && it.watched ? View.VISIBLE : View.GONE);
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
