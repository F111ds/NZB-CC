package com.gathin.tvhome;

import android.app.ActivityManager;
import android.content.Context;
import android.os.Debug;
import android.os.SystemClock;
import android.os.Handler;
import android.os.HandlerThread;
import android.view.FrameMetrics;
import android.view.Window;

import org.json.JSONObject;

import java.io.OutputStream;
import java.io.RandomAccessFile;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * NZB-CC: how the TV is coping with the HAL-style home (its animations), sent to the PC once a minute
 * (POST /api/tv/perf) so it can be checked from there: the TV's free memory, this app's memory and share of the
 * CPU, and while Home is on screen how smooth it draws (frames on time, and slow frames).
 */
final class PerfReport implements Window.OnFrameMetricsAvailableListener {
    private static final long EVERY_S = 60;

    private final Context app;
    private final HomeExtras extras;
    private ScheduledExecutorService timer;
    private long lastCpuTicks = -1, lastAt;
    // frames Home draws while it is on screen (the frame-timing thread)
    private boolean counting;
    private Window window;
    private HandlerThread metricsThread;
    private volatile int frames, slow, frozen;
    private volatile long homeMs;
    private long homeFrom;
    private volatile String screen = "away";

    PerfReport(Context ctx, HomeExtras extras) {
        app = ctx.getApplicationContext();
        this.extras = extras;
    }

    void start() {
        if (timer != null || extras.kodiServer.isEmpty()) return;
        timer = Executors.newSingleThreadScheduledExecutor();
        timer.scheduleWithFixedDelay(this::send, 20, EVERY_S, TimeUnit.SECONDS);
    }

    void stop() {
        if (timer != null) timer.shutdownNow();
        timer = null;
        homeShown(null);
        if (metricsThread != null) metricsThread.quitSafely();
        metricsThread = null;
    }

    /** Home on screen (resumed, its window given) or not (null): its frames are timed only while it is. */
    void homeShown(Window w) {
        boolean on = w != null;
        screen = on ? "home" : "away";
        if (on == counting) return;
        counting = on;
        try {
            if (on) {
                homeFrom = SystemClock.elapsedRealtime();
                if (metricsThread == null) {
                    metricsThread = new HandlerThread("frame-times");
                    metricsThread.start();
                }
                window = w;
                w.addOnFrameMetricsAvailableListener(this, new Handler(metricsThread.getLooper()));
            } else {
                homeMs += SystemClock.elapsedRealtime() - homeFrom;
                if (window != null) window.removeOnFrameMetricsAvailableListener(this);
                window = null;
            }
        } catch (RuntimeException ignored) {
            // frame timing unavailable on this TV: memory and CPU are still sent
        }
    }

    /** Each frame Home draws: over 34 ms is a visible stutter, over 200 ms a freeze. */
    @Override
    public void onFrameMetricsAvailable(Window w, FrameMetrics m, int dropped) {
        long ms = m.getMetric(FrameMetrics.TOTAL_DURATION) / 1_000_000L;
        frames++;
        if (ms > 34) slow++;
        if (ms > 200) frozen++;
        slow += dropped;
    }

    private void send() {
        try {
            ActivityManager am = (ActivityManager) app.getSystemService(Context.ACTIVITY_SERVICE);
            ActivityManager.MemoryInfo mi = new ActivityManager.MemoryInfo();
            am.getMemoryInfo(mi);
            Debug.MemoryInfo dm = new Debug.MemoryInfo();
            Debug.getMemoryInfo(dm);
            long now = SystemClock.elapsedRealtime();
            long ticks = cpuTicks();
            double cpu = -1;
            if (lastCpuTicks >= 0 && ticks >= 0 && now > lastAt) {
                // clock ticks are 1/100 s on Android: percent of one core
                cpu = (ticks - lastCpuTicks) * 1000.0 / (now - lastAt);
            }
            lastCpuTicks = ticks;
            lastAt = now;
            int f = frames, s = slow, z = frozen;
            frames = 0;
            slow = 0;
            frozen = 0;
            long hm = homeMs + (counting ? now - homeFrom : 0);
            homeMs = 0;
            if (counting) homeFrom = now;
            JSONObject j = new JSONObject()
                    .put("avail_mb", mi.availMem / (1024 * 1024))
                    .put("total_mb", mi.totalMem / (1024 * 1024))
                    .put("low_memory", mi.lowMemory)
                    .put("app_mb", dm.getTotalPss() / 1024)
                    .put("cpu", Math.round(cpu * 10) / 10.0)
                    .put("cores", Runtime.getRuntime().availableProcessors())
                    .put("frames", f).put("slow_frames", s).put("freezes", z)
                    .put("home_s", hm / 1000)
                    .put("screen", screen)
                    .put("version", BuildConfigVersion.name(app));
            post(j);
        } catch (Exception ignored) {
            // the PC is off or asleep: next minute
        }
    }

    /** This process's CPU time so far, in clock ticks (utime + stime from /proc/self/stat), or -1. */
    private static long cpuTicks() {
        try (RandomAccessFile f = new RandomAccessFile("/proc/self/stat", "r")) {
            String line = f.readLine();
            String[] p = line.substring(line.lastIndexOf(')') + 2).split(" ");
            return Long.parseLong(p[11]) + Long.parseLong(p[12]);
        } catch (Exception e) {
            return -1;
        }
    }

    private void post(JSONObject j) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(extras.kodiServer + "/api/tv/perf").openConnection();
        c.setConnectTimeout(4000);
        c.setReadTimeout(6000);
        c.setRequestMethod("POST");
        c.setDoOutput(true);
        c.setRequestProperty("Content-Type", "application/json");
        c.setRequestProperty("X-Mobile-Token", extras.kodiToken);
        try (OutputStream out = c.getOutputStream()) {
            out.write(j.toString().getBytes(StandardCharsets.UTF_8));
        }
        c.getResponseCode();
        c.disconnect();
    }

    /** The app's version name, for the report. */
    static final class BuildConfigVersion {
        static String name(Context c) {
            try {
                return c.getPackageManager().getPackageInfo(c.getPackageName(), 0).versionName;
            } catch (Exception e) {
                return "";
            }
        }
    }
}
