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
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.TextUtils;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewOutlineProvider;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.File;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * NZB-CC Continue preview: the "Continue" tile in the dock opens this panel over the home screen. It lists what
 * you were part-way through in Netflix, Prime Video and other apps (ContinueStore): a picture, a thin progress
 * line, and "title · time left" with a dot in the app's colour. OK opens it, hold OK to take it off the row, Back
 * closes the panel.
 *
 * <p>Sizes are the 1920 x 1080 mock-up's (A2), scaled to the screen.
 */
public final class ContinueActivity extends Activity {

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private float scale;
    private LinearLayout row;
    private TextView empty;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        scale = getResources().getDisplayMetrics().widthPixels / 1920f;
        setContentView(build());
    }

    @Override
    protected void onResume() {
        super.onResume();
        load();
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        io.shutdownNow();
    }

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

    private View build() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(0x66000014);   // dims Home behind the panel
        root.setOnClickListener(v -> finish());

        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setClipChildren(false);
        panel.setClipToPadding(false);
        panel.setPadding(px(40), px(36), px(40), px(40));
        GradientDrawable bg = round(0xE6141840, px(36));
        bg.setStroke(px(2), 0x38FFFFFF);
        panel.setBackground(bg);
        panel.setClickable(true);   // a click on the panel is not a click on the dim area

        TextView title = text(34, Color.WHITE, true);
        title.setText("Continue watching");
        panel.addView(title);

        empty = text(24, 0xFFC9CCF2, false);
        empty.setText("Nothing yet. Watch a few minutes of something in Netflix or Prime Video and it shows up here.");
        empty.setPadding(0, px(26), 0, px(6));
        empty.setVisibility(View.GONE);
        panel.addView(empty);

        HorizontalScrollView hs = new HorizontalScrollView(this);
        hs.setHorizontalScrollBarEnabled(false);
        hs.setClipChildren(false);
        hs.setClipToPadding(false);
        hs.setPadding(px(6), px(26), px(6), px(6));
        row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setClipChildren(false);
        hs.addView(row);
        // Four cards wide, as in the mock-up; more scroll.
        panel.addView(hs, new LinearLayout.LayoutParams(px(4 * 360 + 3 * 32 + 12), -2));

        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(-2, -2, Gravity.CENTER_HORIZONTAL | Gravity.TOP);
        lp.topMargin = px(330);
        root.addView(panel, lp);
        return root;
    }

    private void load() {
        try {
            io.execute(() -> {
                List<ContinueStore.Item> list = ContinueStore.load(this);
                handler.post(() -> fill(list));
            });
        } catch (java.util.concurrent.RejectedExecutionException ignored) {
            // closing
        }
    }

    private void fill(List<ContinueStore.Item> list) {
        row.removeAllViews();
        for (ContinueStore.Item it : list) row.addView(card(it));
        empty.setVisibility(list.isEmpty() ? View.VISIBLE : View.GONE);
        ((View) row.getParent()).setVisibility(list.isEmpty() ? View.GONE : View.VISIBLE);
        if (row.getChildCount() > 0) row.getChildAt(0).requestFocus();
    }

    private View card(ContinueStore.Item it) {
        LinearLayout cell = new LinearLayout(this);
        cell.setOrientation(LinearLayout.VERTICAL);
        cell.setFocusable(true);
        cell.setClipChildren(false);
        LinearLayout.LayoutParams clp = new LinearLayout.LayoutParams(px(360), -2);
        clp.rightMargin = px(32);
        cell.setLayoutParams(clp);

        FrameLayout frame = new FrameLayout(this);
        frame.setBackground(round(tone(it.pkg), px(18)));
        frame.setOutlineProvider(new ViewOutlineProvider() {
            @Override
            public void getOutline(View view, Outline outline) {
                outline.setRoundRect(0, 0, view.getWidth(), view.getHeight(), px(18));
            }
        });
        frame.setClipToOutline(true);
        ImageView img = new ImageView(this);
        img.setScaleType(ImageView.ScaleType.CENTER_CROP);
        frame.addView(img, new FrameLayout.LayoutParams(-1, -1));
        loadArt(it.art, img);
        float p = it.progress();
        if (p > 0f) {
            FrameLayout track = new FrameLayout(this);
            track.setBackgroundColor(0x33FFFFFF);
            View fill = new View(this);
            fill.setBackgroundColor(Color.WHITE);
            track.addView(fill, new FrameLayout.LayoutParams(Math.round(px(360) * Math.min(1f, p)), -1));
            frame.addView(track, new FrameLayout.LayoutParams(-1, px(5), Gravity.BOTTOM));
        }
        View ring = new View(this);
        GradientDrawable rd = new GradientDrawable();
        rd.setCornerRadius(px(18));
        rd.setStroke(px(4), Color.WHITE);
        ring.setBackground(rd);
        ring.setAlpha(0f);
        frame.addView(ring, new FrameLayout.LayoutParams(-1, -1));
        cell.addView(frame, new LinearLayout.LayoutParams(px(360), px(203)));

        LinearLayout caption = new LinearLayout(this);
        caption.setOrientation(LinearLayout.HORIZONTAL);
        caption.setGravity(Gravity.CENTER_VERTICAL);
        caption.setPadding(0, px(14), 0, 0);
        View dot = new View(this);
        dot.setBackground(round(dotColor(it.pkg), px(6)));
        LinearLayout.LayoutParams dlp = new LinearLayout.LayoutParams(px(12), px(12));
        dlp.rightMargin = px(10);
        caption.addView(dot, dlp);
        TextView name = text(24, Color.WHITE, true);
        name.setText(it.title);
        name.setSingleLine(true);
        name.setEllipsize(TextUtils.TruncateAt.END);
        caption.addView(name, new LinearLayout.LayoutParams(0, -2, 1f));
        if (it.dur > 0) {
            TextView left = text(24, 0xFFC9CCF2, false);
            left.setText(" · " + KodiRow.Item.duration(it.minutesLeft()) + " left");
            left.setSingleLine(true);
            caption.addView(left);
        }
        cell.addView(caption);

        cell.setOnFocusChangeListener((v, has) -> {
            frame.animate().scaleX(has ? 1.04f : 1f).scaleY(has ? 1.04f : 1f).setDuration(120).start();
            ring.animate().alpha(has ? 1f : 0f).setDuration(120).start();
        });
        cell.setOnClickListener(v -> open(it));
        cell.setOnLongClickListener(v -> {
            new AlertDialog.Builder(this)
                    .setTitle(it.title)
                    .setItems(new CharSequence[]{"Open in " + appName(it.pkg), "Remove from Continue watching"}, (d, which) -> {
                        if (which == 0) {
                            open(it);
                        } else {
                            try {
                                io.execute(() -> {
                                    ContinueStore.forget(this, it);
                                    handler.post(this::load);
                                });
                            } catch (java.util.concurrent.RejectedExecutionException ignored) {
                                // closing
                            }
                        }
                    })
                    .show();
            return true;
        });
        return cell;
    }

    /** Watch Next items reopen the exact episode; noted ones open the app, whose own row has it first. */
    private void open(ContinueStore.Item it) {
        Intent target = null;
        if (!it.intentUri.isEmpty()) {
            try {
                target = Intent.parseUri(it.intentUri, Intent.URI_INTENT_SCHEME);
            } catch (Exception ignored) {
                target = null;
            }
        }
        if (target == null) {
            PackageManager pm = getPackageManager();
            target = pm.getLeanbackLaunchIntentForPackage(it.pkg);
            if (target == null) target = pm.getLaunchIntentForPackage(it.pkg);
        }
        if (target == null) return;
        try {
            startActivity(target.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            finish();
        } catch (Exception ignored) {
            // the app went away
        }
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

    private static int dotColor(String pkg) {
        String p = pkg.toLowerCase(Locale.ROOT);
        if (p.contains("netflix")) return 0xFFE50914;
        if (p.contains("amazon")) return 0xFF3B8BFF;
        if (p.contains("youtube")) return 0xFFFF3B30;
        return 0xFFC9CCF2;
    }

    /** The plain tile colour when an app shares no picture. */
    private static int tone(String pkg) {
        String p = pkg.toLowerCase(Locale.ROOT);
        if (p.contains("netflix")) return 0xFF5A2A3A;
        if (p.contains("amazon")) return 0xFF1F3F6E;
        return 0xFF2E3360;
    }

    private Bitmap decode(File f, int targetW) {
        BitmapFactory.Options o = new BitmapFactory.Options();
        o.inJustDecodeBounds = true;
        BitmapFactory.decodeFile(f.getPath(), o);
        int sample = 1;
        while (o.outWidth / (sample * 2) >= targetW) sample *= 2;
        o = new BitmapFactory.Options();
        o.inSampleSize = sample;
        return BitmapFactory.decodeFile(f.getPath(), o);
    }

    /** A file kept by ContinueStore, or a web or content address (Watch Next art), read off the main thread. */
    private void loadArt(String art, ImageView into) {
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
                        if (f.isFile()) b = decode(f, px(360));
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

    @Override
    public boolean dispatchKeyEvent(KeyEvent e) {
        // Nothing focused (an empty panel): OK or Back closes it.
        if (e.getAction() == KeyEvent.ACTION_UP && getCurrentFocus() == null
                && (e.getKeyCode() == KeyEvent.KEYCODE_DPAD_CENTER || e.getKeyCode() == KeyEvent.KEYCODE_ENTER)) {
            finish();
            return true;
        }
        return super.dispatchKeyEvent(e);
    }
}
