package com.gathin.tvhome;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.Outline;
import android.graphics.Typeface;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.TextUtils;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewOutlineProvider;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextClock;
import android.widget.TextView;

import androidx.recyclerview.widget.RecyclerView;

import java.io.File;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * NZB-CC cinema preview: a cinematic home screen to try beside the launcher. A large "pick up where you left off"
 * item at the top (Resume / From the start), one Continue watching row for Netflix, Prime Video and Kodi, and
 * Kodi's Recently watched row underneath. It is opened like an app (the TV's app list, or try-cinema.ps1); Back
 * returns to the launcher, which this never replaces.
 *
 * <p>Sizes are the 1920 x 1080 mock-up's, scaled to the screen.
 */
public final class CinemaActivity extends Activity implements KodiRow.Host {

    private static final int BG = 0xFF0A0D18;
    private static final int SOFT = 0xFFD9DCF5;
    private static final int MUTED = 0xFFAEB2D6;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private float scale;
    private HomeExtras extras;
    private WeatherNow weatherNow;
    private ExchangeRate exchangeRate;
    private KodiRow kodiRow;

    private ImageView backdrop;
    private TextView heroChip, heroLabel, heroTitle, heroMeta, resume, fromStart, fxText, weatherText, continueEmpty;
    private View heroTrack, heroFill;
    private LinearLayout continueRow, dock;
    private RecyclerView kodiList;
    private View kodiHeader;

    /** What the hero shows, and what Resume opens: a Kodi item or another app's item. */
    private KodiRow.Item heroKodi;
    private ContinueStore.Item heroApp;
    private List<ContinueStore.Item> appItems = new ArrayList<>();
    private boolean resumed;

    private final Runnable minuteTick = new Runnable() {
        @Override
        public void run() {
            refresh();
            handler.postDelayed(this, 60_000L);
        }
    };

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        scale = getResources().getDisplayMetrics().widthPixels / 1920f;
        extras = HomeExtras.load(this);
        setContentView(build());
        if (extras.weather) weatherNow = new WeatherNow(this, extras, handler, this::updateTopBar);
        if (extras.fx) exchangeRate = new ExchangeRate(this, extras, handler, this::updateTopBar);
        kodiRow = new KodiRow(this, extras, handler, kodiList, this);
        kodiRow.setPosterHeight(px(270));
        updateTopBar();
        showDefaultHero();
        resume.post(resume::requestFocus);
    }

    @Override
    protected void onResume() {
        super.onResume();
        resumed = true;
        handler.removeCallbacks(minuteTick);
        handler.post(minuteTick);
        loadAppItems();
    }

    @Override
    protected void onPause() {
        super.onPause();
        resumed = false;
        handler.removeCallbacks(minuteTick);
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        io.shutdownNow();
        if (weatherNow != null) weatherNow.shutdown();
        if (exchangeRate != null) exchangeRate.shutdown();
        if (kodiRow != null) kodiRow.shutdown();
    }

    private void refresh() {
        if (!resumed) return;
        if (weatherNow != null) weatherNow.refreshIfStale();
        if (exchangeRate != null) exchangeRate.refreshIfStale();
        if (kodiRow != null) kodiRow.refreshIfStale();
    }

    // ---- layout -------------------------------------------------------------------------------

    private int px(float design) {
        return Math.round(design * scale);
    }

    private TextView text(float size, int color, boolean bold) {
        TextView t = new TextView(this);
        t.setTextSize(TypedValue.COMPLEX_UNIT_PX, px(size));
        t.setTextColor(color);
        t.setTypeface(bold ? Typeface.DEFAULT_BOLD : Typeface.create("sans-serif-medium", Typeface.NORMAL));
        t.setIncludeFontPadding(false);
        return t;
    }

    private static GradientDrawable round(int color, float radius) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(color);
        d.setCornerRadius(radius);
        return d;
    }

    private TextView chip(String label, int color, float size) {
        TextView c = text(size, Color.WHITE, true);
        c.setText(label);
        c.setLetterSpacing(0.1f);
        c.setPadding(px(14), px(5), px(14), px(5));
        c.setBackground(round(color, px(14)));
        return c;
    }

    private FrameLayout.LayoutParams at(int w, int h, float x, float y) {
        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(w, h);
        lp.leftMargin = px(x);
        lp.topMargin = px(y);
        return lp;
    }

    private View divider() {
        View v = new View(this);
        v.setBackgroundColor(0x4DFFFFFF);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(px(2), px(32));
        lp.leftMargin = px(22);
        lp.rightMargin = px(22);
        v.setLayoutParams(lp);
        return v;
    }

    private View build() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(BG);
        root.setClipChildren(false);

        backdrop = new ImageView(this);
        backdrop.setScaleType(ImageView.ScaleType.CENTER_CROP);
        backdrop.setAlpha(0.6f);
        FrameLayout.LayoutParams blp = new FrameLayout.LayoutParams(px(1240), px(700), Gravity.TOP | Gravity.END);
        root.addView(backdrop, blp);
        View side = new View(this);
        side.setBackground(new GradientDrawable(GradientDrawable.Orientation.LEFT_RIGHT,
                new int[]{BG, BG, 0xBF0A0D18, 0x000A0D18}));
        root.addView(side, at(-1, px(700), 0, 0));
        View fade = new View(this);
        fade.setBackground(new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM, new int[]{0x000A0D18, BG}));
        root.addView(fade, at(-1, px(240), 0, 460));

        root.addView(topBar(), at(-2, px(88), 96, 48));
        dock = new LinearLayout(this);
        dock.setOrientation(LinearLayout.HORIZONTAL);
        dock.setGravity(Gravity.CENTER_VERTICAL);
        dock.setPadding(px(22), 0, px(22), 0);
        GradientDrawable dbg = round(0x1FFFFFFF, px(44));
        dbg.setStroke(px(2), 0x47FFFFFF);
        dock.setBackground(dbg);
        fillDock();
        FrameLayout.LayoutParams dlp = new FrameLayout.LayoutParams(-2, px(88), Gravity.TOP | Gravity.END);
        dlp.topMargin = px(48);
        dlp.rightMargin = px(96);
        root.addView(dock, dlp);

        root.addView(hero(), at(px(1000), -2, 96, 190));

        LinearLayout cont = new LinearLayout(this);
        cont.setOrientation(LinearLayout.VERTICAL);
        cont.setClipChildren(false);
        cont.setClipToPadding(false);
        TextView ch = text(30, Color.WHITE, true);
        ch.setText("Continue watching");
        cont.addView(ch);
        continueEmpty = text(22, MUTED, false);
        continueEmpty.setText("Play something in Netflix or Prime Video and it shows up here.");
        continueEmpty.setPadding(0, px(18), 0, 0);
        cont.addView(continueEmpty);
        HorizontalScrollView hs = new HorizontalScrollView(this);
        hs.setHorizontalScrollBarEnabled(false);
        hs.setClipChildren(false);
        hs.setClipToPadding(false);
        hs.setPadding(0, px(14), px(96), px(8));
        continueRow = new LinearLayout(this);
        continueRow.setOrientation(LinearLayout.HORIZONTAL);
        continueRow.setClipChildren(false);
        hs.addView(continueRow);
        cont.addView(hs);
        root.addView(cont, at(-1, -2, 96, 596));

        LinearLayout kh = new LinearLayout(this);
        kh.setOrientation(LinearLayout.HORIZONTAL);
        kh.setGravity(Gravity.CENTER_VERTICAL);
        kh.addView(chip("KODI", 0xFF2F7DE1, 16));
        TextView kt = text(30, Color.WHITE, true);
        kt.setText("Recently watched");
        kt.setPadding(px(14), 0, 0, 0);
        kh.addView(kt);
        kodiHeader = kh;
        root.addView(kh, at(-2, -2, 96, 896));
        kodiList = new RecyclerView(this);
        kodiList.setClipToPadding(false);
        kodiList.setClipChildren(false);
        kodiList.setPadding(px(90), 0, px(96), 0);
        kodiList.setFocusable(false);
        root.addView(kodiList, at(-1, px(330), 0, 936));
        return root;
    }

    private View topBar() {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        TextClock time = new TextClock(this);
        time.setFormat12Hour("h:mm");
        time.setFormat24Hour("H:mm");
        time.setTextSize(TypedValue.COMPLEX_UNIT_PX, px(32));
        time.setTextColor(Color.WHITE);
        time.setTypeface(Typeface.DEFAULT_BOLD);
        bar.addView(time);
        TextClock ampm = new TextClock(this);
        ampm.setFormat12Hour(" a");
        ampm.setFormat24Hour("");
        ampm.setTextSize(TypedValue.COMPLEX_UNIT_PX, px(22));
        ampm.setTextColor(Color.WHITE);
        ampm.setTypeface(Typeface.DEFAULT_BOLD);
        bar.addView(ampm);
        bar.addView(divider());
        TextClock date = new TextClock(this);
        date.setFormat12Hour("EEE, MMM d");
        date.setFormat24Hour("EEE, MMM d");
        date.setTextSize(TypedValue.COMPLEX_UNIT_PX, px(32));
        date.setTextColor(SOFT);
        bar.addView(date);
        fxText = text(32, SOFT, false);
        weatherText = text(32, SOFT, false);
        bar.addView(divider());
        bar.addView(fxText);
        bar.addView(divider());
        bar.addView(weatherText);
        return bar;
    }

    private void updateTopBar() {
        boolean fx = exchangeRate != null && exchangeRate.hasRate();
        if (fx) fxText.setText(exchangeRate.line());
        fxText.setVisibility(fx ? View.VISIBLE : View.GONE);
        boolean w = weatherNow != null && weatherNow.hasReading();
        if (w) weatherText.setText(weatherNow.pill);
        weatherText.setVisibility(w ? View.VISIBLE : View.GONE);
        // The divider before each item goes with it.
        ViewGroup bar = (ViewGroup) fxText.getParent();
        int i = bar.indexOfChild(fxText);
        bar.getChildAt(i - 1).setVisibility(fx ? View.VISIBLE : View.GONE);
        int j = bar.indexOfChild(weatherText);
        bar.getChildAt(j - 1).setVisibility(w ? View.VISIBLE : View.GONE);
    }

    private View hero() {
        LinearLayout h = new LinearLayout(this);
        h.setOrientation(LinearLayout.VERTICAL);
        h.setClipChildren(false);
        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);
        heroChip = chip("KODI", 0xFF2F7DE1, 18);
        top.addView(heroChip);
        heroLabel = text(24, SOFT, false);
        heroLabel.setPadding(px(14), 0, 0, 0);
        top.addView(heroLabel);
        h.addView(top);

        heroTitle = text(92, Color.WHITE, true);
        heroTitle.setMaxLines(2);
        heroTitle.setEllipsize(TextUtils.TruncateAt.END);
        heroTitle.setLetterSpacing(-0.01f);
        heroTitle.setPadding(0, px(24), 0, 0);
        h.addView(heroTitle);
        heroMeta = text(26, SOFT, false);
        heroMeta.setPadding(0, px(22), 0, 0);
        h.addView(heroMeta);

        FrameLayout track = new FrameLayout(this);
        track.setBackground(round(0x33FFFFFF, px(4)));
        heroFill = new View(this);
        heroFill.setBackground(round(0xFF5BC0F8, px(4)));
        track.addView(heroFill, new FrameLayout.LayoutParams(0, -1));
        LinearLayout.LayoutParams tlp = new LinearLayout.LayoutParams(px(520), px(8));
        tlp.topMargin = px(22);
        h.addView(track, tlp);
        heroTrack = track;

        LinearLayout buttons = new LinearLayout(this);
        buttons.setOrientation(LinearLayout.HORIZONTAL);
        buttons.setClipChildren(false);
        resume = button("▶  Resume", true);
        resume.setOnClickListener(v -> openHero(false));
        fromStart = button("From the start", false);
        fromStart.setOnClickListener(v -> openHero(true));
        buttons.addView(resume);
        LinearLayout.LayoutParams flp = new LinearLayout.LayoutParams(-2, px(76));
        flp.leftMargin = px(18);
        buttons.addView(fromStart, flp);
        LinearLayout.LayoutParams blp = new LinearLayout.LayoutParams(-2, -2);
        blp.topMargin = px(30);
        h.addView(buttons, blp);
        return h;
    }

    /** A pill button: white with dark text when focused, glass otherwise. */
    private TextView button(String label, boolean primary) {
        TextView b = text(28, Color.WHITE, true);
        b.setText(label);
        b.setGravity(Gravity.CENTER);
        b.setPadding(px(40), 0, px(40), 0);
        b.setMinHeight(px(76));
        b.setFocusable(true);
        b.setLayoutParams(new LinearLayout.LayoutParams(-2, px(76)));
        View.OnFocusChangeListener style = (v, has) -> {
            GradientDrawable d = round(has ? Color.WHITE : 0x1FFFFFFF, px(38));
            if (!has) d.setStroke(px(2), 0x73FFFFFF);
            v.setBackground(d);
            ((TextView) v).setTextColor(has ? BG : Color.WHITE);
            v.animate().scaleX(has ? 1.05f : 1f).scaleY(has ? 1.05f : 1f).setDuration(120).start();
        };
        b.setOnFocusChangeListener(style);
        style.onFocusChange(b, false);
        b.setOnKeyListener((v, code, e) -> {
            if (e.getAction() != KeyEvent.ACTION_DOWN) return false;
            if (code == KeyEvent.KEYCODE_DPAD_DOWN) return focusContinue() || focusKodi();
            if (code == KeyEvent.KEYCODE_DPAD_UP) return dock.getChildCount() > 0 && dock.getChildAt(0).requestFocus();
            return false;
        });
        return b;
    }

    // ---- dock ---------------------------------------------------------------------------------

    private void fillDock() {
        PackageManager pm = getPackageManager();
        for (String entry : extras.homeApps) {
            String pkg = entry.contains("|") ? entry.substring(0, entry.indexOf('|')).trim() : entry.trim();
            Intent launch = launchIntent(pkg);
            if (launch == null) continue;
            Drawable icon;
            try {
                icon = pm.getApplicationBanner(pkg);
                if (icon == null) icon = pm.getApplicationIcon(pkg);
            } catch (PackageManager.NameNotFoundException e) {
                continue;
            }
            ImageView b = new ImageView(this);
            b.setImageDrawable(icon);
            b.setScaleType(ImageView.ScaleType.FIT_CENTER);
            b.setFocusable(true);
            b.setPadding(px(4), px(4), px(4), px(4));
            b.setContentDescription(entry.contains("|") ? entry.substring(entry.indexOf('|') + 1) : pkg);
            b.setOnClickListener(v -> start(launch));
            b.setOnFocusChangeListener((v, has) -> {
                GradientDrawable d = round(has ? 0x33FFFFFF : Color.TRANSPARENT, px(14));
                if (has) d.setStroke(px(3), Color.WHITE);
                v.setBackground(d);
                v.animate().scaleX(has ? 1.12f : 1f).scaleY(has ? 1.12f : 1f).setDuration(120).start();
            });
            b.setOnKeyListener((v, code, e) -> e.getAction() == KeyEvent.ACTION_DOWN
                    && code == KeyEvent.KEYCODE_DPAD_DOWN && resume.requestFocus());
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(px(104), px(64));
            lp.leftMargin = px(6);
            lp.rightMargin = px(6);
            dock.addView(b, lp);
        }
        // Settings, so the preview is never a dead end.
        TextView gear = text(30, Color.WHITE, true);
        gear.setText("⚙");
        gear.setGravity(Gravity.CENTER);
        gear.setFocusable(true);
        gear.setContentDescription("Settings");
        gear.setOnClickListener(v -> start(new Intent(Settings.ACTION_SETTINGS)));
        gear.setOnFocusChangeListener((v, has) -> {
            GradientDrawable d = round(has ? 0x33FFFFFF : Color.TRANSPARENT, px(14));
            if (has) d.setStroke(px(3), Color.WHITE);
            v.setBackground(d);
        });
        gear.setOnKeyListener((v, code, e) -> e.getAction() == KeyEvent.ACTION_DOWN
                && code == KeyEvent.KEYCODE_DPAD_DOWN && resume.requestFocus());
        LinearLayout.LayoutParams glp = new LinearLayout.LayoutParams(px(64), px(64));
        glp.leftMargin = px(6);
        dock.addView(gear, glp);
    }

    private Intent launchIntent(String pkg) {
        PackageManager pm = getPackageManager();
        Intent i = pm.getLeanbackLaunchIntentForPackage(pkg);
        if (i == null) i = pm.getLaunchIntentForPackage(pkg);
        return i;
    }

    private void start(Intent i) {
        try {
            startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        } catch (Exception ignored) {
            // the app went away
        }
    }

    // ---- hero ---------------------------------------------------------------------------------

    private void showDefaultHero() {
        if (kodiRow != null) {
            for (KodiRow.Item it : kodiRow.items()) {
                if (it.partway()) {
                    showHero(it);
                    return;
                }
            }
            if (!kodiRow.items().isEmpty()) {
                showHero(kodiRow.items().get(0));
                return;
            }
        }
        if (!appItems.isEmpty()) {
            showHero(appItems.get(0));
            return;
        }
        heroKodi = null;
        heroApp = null;
        heroChip.setVisibility(View.GONE);
        heroLabel.setText("Nothing to pick up yet");
        heroTitle.setText("Welcome back");
        heroMeta.setText("Watch something in Kodi, Netflix or Prime Video and it will wait for you here.");
        heroTrack.setVisibility(View.INVISIBLE);
        resume.setVisibility(View.GONE);
        fromStart.setVisibility(View.GONE);
        backdrop.setImageDrawable(null);
    }

    private void showHero(KodiRow.Item it) {
        heroKodi = it;
        heroApp = null;
        heroChip.setVisibility(View.VISIBLE);
        heroChip.setText("KODI");
        heroChip.setBackground(round(0xFF2F7DE1, px(14)));
        heroLabel.setText(it.partway() ? "Pick up where you left off" : "Recently watched");
        heroTitle.setText(it.title);
        String meta = it.meta();
        int cut = meta.lastIndexOf(" · OK to");
        heroMeta.setText(cut > 0 ? meta.substring(0, cut) : meta);
        setProgress(it.partway() ? it.progress : 0f);
        resume.setVisibility(View.VISIBLE);
        resume.setText(it.partway() ? "▶  Resume" : "▶  Play");
        fromStart.setVisibility(it.partway() ? View.VISIBLE : View.GONE);
        setBackdrop(it.poster);
    }

    private void showHero(ContinueStore.Item it) {
        heroApp = it;
        heroKodi = null;
        heroChip.setVisibility(View.VISIBLE);
        heroChip.setText(appName(it.pkg).toUpperCase(java.util.Locale.ROOT));
        heroChip.setBackground(round(appColor(it.pkg), px(14)));
        heroLabel.setText("Pick up where you left off");
        heroTitle.setText(it.title);
        StringBuilder m = new StringBuilder();
        if (!it.sub.isEmpty()) m.append(it.sub);
        if (it.dur > 0) m.append(m.length() > 0 ? " · " : "").append(KodiRow.Item.duration(it.minutesLeft())).append(" left");
        heroMeta.setText(m.toString());
        setProgress(it.progress());
        resume.setVisibility(View.VISIBLE);
        resume.setText(it.watchNext ? "▶  Resume" : "▶  Open in " + appName(it.pkg));
        fromStart.setVisibility(View.GONE);
        loadArt(it.art, backdrop);
    }

    private void setProgress(float p) {
        heroTrack.setVisibility(p > 0f ? View.VISIBLE : View.INVISIBLE);
        heroFill.getLayoutParams().width = Math.round(px(520) * Math.min(1f, p));
        heroFill.requestLayout();
    }

    private void setBackdrop(File poster) {
        Bitmap b = poster != null && poster.isFile() ? decode(poster, px(700)) : null;
        backdrop.setImageBitmap(b);
    }

    private void openHero(boolean fromStartPressed) {
        if (heroKodi != null) {
            kodiRow.play(heroKodi, fromStartPressed);
        } else if (heroApp != null) {
            openApp(heroApp);
        }
    }

    /** Watch Next items reopen the exact episode; noted ones open the app, whose own row has it first. */
    private void openApp(ContinueStore.Item it) {
        if (!it.intentUri.isEmpty()) {
            try {
                Intent i = Intent.parseUri(it.intentUri, Intent.URI_INTENT_SCHEME);
                startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                return;
            } catch (Exception ignored) {
                // fall through to the app itself
            }
        }
        Intent launch = launchIntent(it.pkg);
        if (launch != null) start(launch);
    }

    private String appName(String pkg) {
        if (pkg.contains("netflix")) return "Netflix";
        if (pkg.contains("amazon")) return "Prime Video";
        try {
            PackageManager pm = getPackageManager();
            return pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString();
        } catch (Exception e) {
            return pkg;
        }
    }

    private static int appColor(String pkg) {
        if (pkg.contains("netflix")) return 0xFFE50914;
        if (pkg.contains("amazon")) return 0xFF1F6FE0;
        if (pkg.contains("youtube")) return 0xFFCC0000;
        return 0xFF4A4F7A;
    }

    // ---- Continue watching row ----------------------------------------------------------------

    private void loadAppItems() {
        try {
            io.execute(() -> {
                List<ContinueStore.Item> list = ContinueStore.load(this);
                handler.post(() -> {
                    appItems = list;
                    fillContinue();
                    if (heroKodi == null && heroApp == null) showDefaultHero();
                });
            });
        } catch (java.util.concurrent.RejectedExecutionException ignored) {
            // closing
        }
    }

    /** Netflix, Prime Video and other apps' items, with Kodi's part-way items mixed in by time. */
    private void fillContinue() {
        View had = getCurrentFocus();
        boolean refocus = had != null && had.getParent() == continueRow;
        continueRow.removeAllViews();
        List<Object> cards = new ArrayList<>(appItems);
        if (kodiRow != null) {
            int at = Math.min(2, cards.size());
            for (KodiRow.Item k : kodiRow.items()) if (k.partway()) cards.add(Math.min(at++, cards.size()), k);
        }
        for (Object o : cards) continueRow.addView(card(o));
        continueEmpty.setVisibility(cards.isEmpty() ? View.VISIBLE : View.GONE);
        if (refocus) focusContinue();
    }

    private View card(Object o) {
        boolean kodi = o instanceof KodiRow.Item;
        KodiRow.Item k = kodi ? (KodiRow.Item) o : null;
        ContinueStore.Item a = kodi ? null : (ContinueStore.Item) o;

        LinearLayout cell = new LinearLayout(this);
        cell.setOrientation(LinearLayout.VERTICAL);
        cell.setFocusable(true);
        cell.setClipChildren(false);
        LinearLayout.LayoutParams clp = new LinearLayout.LayoutParams(px(320), -2);
        clp.rightMargin = px(26);
        cell.setLayoutParams(clp);

        FrameLayout frame = new FrameLayout(this);
        frame.setBackground(round(0xFF2A2F4F, px(16)));
        frame.setOutlineProvider(new ViewOutlineProvider() {
            @Override
            public void getOutline(View view, Outline outline) {
                outline.setRoundRect(0, 0, view.getWidth(), view.getHeight(), px(16));
            }
        });
        frame.setClipToOutline(true);
        ImageView img = new ImageView(this);
        img.setScaleType(ImageView.ScaleType.CENTER_CROP);
        frame.addView(img, new FrameLayout.LayoutParams(-1, -1));
        if (kodi) {
            Bitmap b = k.poster != null && k.poster.isFile() ? decode(k.poster, px(320)) : null;
            img.setImageBitmap(b);
        } else {
            loadArt(a.art, img);
        }
        String src = kodi ? "Kodi" : appName(a.pkg);
        TextView c = chip(src, kodi ? 0xFF2F7DE1 : appColor(a.pkg), 16);
        c.setLetterSpacing(0f);
        FrameLayout.LayoutParams chl = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.START);
        chl.setMargins(px(10), px(10), 0, 0);
        frame.addView(c, chl);
        int left = kodi ? k.minutesLeft() : a.minutesLeft();
        if (left > 0) {
            TextView l = text(18, Color.WHITE, true);
            l.setText(KodiRow.Item.duration(left) + " left");
            l.setPadding(px(10), px(3), px(10), px(3));
            l.setBackground(round(0x99000000, px(9)));
            FrameLayout.LayoutParams llp = new FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM | Gravity.END);
            llp.setMargins(0, 0, px(10), px(16));
            frame.addView(l, llp);
        }
        float p = kodi ? k.progress : a.progress();
        if (p > 0f) {
            FrameLayout track = new FrameLayout(this);
            track.setBackgroundColor(0x38FFFFFF);
            View fill = new View(this);
            fill.setBackgroundColor(kodi ? 0xFF5BC0F8 : (a.pkg.contains("netflix") ? 0xFFE50914 : 0xFF5BB3FF));
            track.addView(fill, new FrameLayout.LayoutParams(Math.round(px(320) * Math.min(1f, p)), -1));
            frame.addView(track, new FrameLayout.LayoutParams(-1, px(7), Gravity.BOTTOM));
        }
        View ring = new View(this);
        GradientDrawable rd = new GradientDrawable();
        rd.setCornerRadius(px(16));
        rd.setStroke(px(4), Color.WHITE);
        ring.setBackground(rd);
        ring.setAlpha(0f);
        frame.addView(ring, new FrameLayout.LayoutParams(-1, -1));
        cell.addView(frame, new LinearLayout.LayoutParams(px(320), px(180)));

        TextView t = text(22, Color.WHITE, true);
        String sub = kodi ? (k.isEpisode() ? String.format(java.util.Locale.US, "S%d E%d", k.season, k.episode) : "") : a.sub;
        t.setText(sub.isEmpty() ? (kodi ? k.title : a.title) : (kodi ? k.title : a.title) + " · " + sub);
        t.setSingleLine(true);
        t.setEllipsize(TextUtils.TruncateAt.END);
        t.setPadding(0, px(12), 0, 0);
        cell.addView(t);

        cell.setOnFocusChangeListener((v, has) -> {
            frame.animate().scaleX(has ? 1.05f : 1f).scaleY(has ? 1.05f : 1f).setDuration(120).start();
            ring.animate().alpha(has ? 1f : 0f).setDuration(120).start();
            if (has) {
                if (kodi) showHero(k);
                else showHero(a);
            }
        });
        cell.setOnClickListener(v -> {
            if (kodi) kodiRow.play(k, false);
            else openApp(a);
        });
        cell.setOnLongClickListener(v -> {
            if (kodi) {
                onKodiItemMenu(k, v);
            } else {
                new AlertDialog.Builder(this)
                        .setTitle(a.title)
                        .setItems(new CharSequence[]{"Open in " + appName(a.pkg), "Remove from this row"}, (d, which) -> {
                            if (which == 0) openApp(a);
                            else {
                                io.execute(() -> {
                                    ContinueStore.forget(this, a);
                                    handler.post(this::loadAppItems);
                                });
                            }
                        })
                        .show();
            }
            return true;
        });
        cell.setOnKeyListener((v, code, e) -> {
            if (e.getAction() != KeyEvent.ACTION_DOWN) return false;
            if (code == KeyEvent.KEYCODE_DPAD_UP) return resume.getVisibility() == View.VISIBLE && resume.requestFocus();
            if (code == KeyEvent.KEYCODE_DPAD_DOWN) return focusKodi();
            return false;
        });
        return cell;
    }

    private boolean focusContinue() {
        return continueRow.getChildCount() > 0 && continueRow.getChildAt(0).requestFocus();
    }

    private boolean focusKodi() {
        return kodiRow != null && kodiRow.focus();
    }

    // ---- pictures -----------------------------------------------------------------------------

    private static Bitmap decode(File f, int targetW) {
        BitmapFactory.Options o = new BitmapFactory.Options();
        o.inJustDecodeBounds = true;
        BitmapFactory.decodeFile(f.getPath(), o);
        int sample = 1;
        while (o.outWidth / (sample * 2) >= targetW) sample *= 2;
        o = new BitmapFactory.Options();
        o.inSampleSize = sample;
        return BitmapFactory.decodeFile(f.getPath(), o);
    }

    /** A file kept by ContinueStore, or a web address (Watch Next art), fetched off the main thread. */
    private void loadArt(String art, ImageView into) {
        into.setImageDrawable(null);
        if (art == null || art.isEmpty()) return;
        into.setTag(art);
        try {
            io.execute(() -> {
                Bitmap b = null;
                try {
                    if (art.startsWith("http://") || art.startsWith("https://")) {
                        HttpURLConnection c = (HttpURLConnection) new URL(art).openConnection();
                        c.setConnectTimeout(6000);
                        c.setReadTimeout(8000);
                        try (InputStream in = c.getInputStream()) {
                            b = BitmapFactory.decodeStream(in);
                        } finally {
                            c.disconnect();
                        }
                    } else if (art.startsWith("content://") || art.startsWith("android.resource://")) {
                        try (InputStream in = getContentResolver().openInputStream(android.net.Uri.parse(art))) {
                            b = BitmapFactory.decodeStream(in);
                        }
                    } else {
                        File f = new File(art);
                        if (f.isFile()) b = decode(f, px(640));
                    }
                } catch (Exception ignored) {
                    b = null;
                }
                final Bitmap fb = b;
                handler.post(() -> {
                    if (art.equals(into.getTag())) into.setImageBitmap(fb);
                });
            });
        } catch (java.util.concurrent.RejectedExecutionException ignored) {
            // closing
        }
    }

    // ---- KodiRow.Host -------------------------------------------------------------------------

    @Override
    public void onKodiItemFocused(KodiRow.Item item) {
        showHero(item);
    }

    @Override
    public void onKodiRowDown() {
        // the bottom row
    }

    @Override
    public void onKodiRowUp() {
        if (!focusContinue()) resume.requestFocus();
    }

    @Override
    public void onKodiRowLeft() {
        // nothing to do
    }

    @Override
    public void onKodiRowChanged() {
        kodiHeader.setVisibility(kodiRow.hasItems() ? View.VISIBLE : View.GONE);
        fillContinue();
        View f = getCurrentFocus();
        boolean inRows = f != null && (f.getParent() == continueRow || kodiList.hasFocus());
        if (!inRows) showDefaultHero();
    }

    @Override
    public void onKodiItemMenu(KodiRow.Item item, View anchor) {
        List<CharSequence> labels = new ArrayList<>();
        List<Runnable> acts = new ArrayList<>();
        labels.add(item.partway() ? "Resume" : "Play");
        acts.add(() -> kodiRow.play(item, false));
        if (item.partway()) {
            labels.add("Play from the start");
            acts.add(() -> kodiRow.play(item, true));
        }
        labels.add("Mark as watched");
        acts.add(() -> kodiRow.markWatched(item));
        labels.add("Remove from the row");
        acts.add(() -> kodiRow.remove(item));
        new AlertDialog.Builder(this)
                .setTitle(item.title)
                .setItems(labels.toArray(new CharSequence[0]), (d, which) -> acts.get(which).run())
                .show();
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent e) {
        // Nothing focused yet (first frame, or after a list rebuild): any arrow lands on Resume or a row.
        if (e.getAction() == KeyEvent.ACTION_DOWN && getCurrentFocus() == null) {
            int code = e.getKeyCode();
            if (code == KeyEvent.KEYCODE_DPAD_UP || code == KeyEvent.KEYCODE_DPAD_DOWN
                    || code == KeyEvent.KEYCODE_DPAD_LEFT || code == KeyEvent.KEYCODE_DPAD_RIGHT) {
                if ((resume.getVisibility() == View.VISIBLE && resume.requestFocus()) || focusContinue() || focusKodi()) return true;
            }
        }
        return super.dispatchKeyEvent(e);
    }
}
