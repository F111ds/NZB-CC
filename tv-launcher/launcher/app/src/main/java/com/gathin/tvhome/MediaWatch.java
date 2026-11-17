package com.gathin.tvhome;

import android.content.ComponentName;
import android.media.MediaMetadata;
import android.media.session.MediaController;
import android.media.session.MediaSession;
import android.media.session.MediaSessionManager;
import android.media.session.PlaybackState;
import android.os.Handler;
import android.os.Looper;
import android.service.notification.NotificationListenerService;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * NZB-CC cinema preview: notes what other apps are playing (their media sessions) for the Continue watching row.
 * It reads no notification: notification access is only what Android asks of an app that watches media sessions.
 * Granted over adb by try-cinema.ps1:
 * {@code cmd notification allow_listener com.gathin.tvhome.preview/com.gathin.tvhome.MediaWatch}
 */
public final class MediaWatch extends NotificationListenerService {

    private final Handler main = new Handler(Looper.getMainLooper());
    private final Map<MediaSession.Token, Watcher> watching = new HashMap<>();
    private MediaSessionManager sessions;
    private final MediaSessionManager.OnActiveSessionsChangedListener changed = this::track;

    @Override
    public void onListenerConnected() {
        sessions = (MediaSessionManager) getSystemService(MEDIA_SESSION_SERVICE);
        if (sessions == null) return;
        ComponentName me = new ComponentName(this, MediaWatch.class);
        try {
            sessions.addOnActiveSessionsChangedListener(changed, me, main);
            track(sessions.getActiveSessions(me));
        } catch (SecurityException ignored) {
            // Notification access was taken back.
        }
    }

    @Override
    public void onListenerDisconnected() {
        if (sessions != null) {
            try {
                sessions.removeOnActiveSessionsChangedListener(changed);
            } catch (Exception ignored) {
                // already gone
            }
        }
        for (Watcher w : watching.values()) w.stop();
        watching.clear();
    }

    private void track(List<MediaController> list) {
        if (list == null) return;
        java.util.Set<MediaSession.Token> live = new java.util.HashSet<>();
        for (MediaController c : list) {
            MediaSession.Token t = c.getSessionToken();
            live.add(t);
            if (watching.containsKey(t)) continue;
            Watcher w = new Watcher(c);
            watching.put(t, w);
            w.noteNow();
        }
        for (java.util.Iterator<Map.Entry<MediaSession.Token, Watcher>> it = watching.entrySet().iterator(); it.hasNext(); ) {
            Map.Entry<MediaSession.Token, Watcher> e = it.next();
            if (!live.contains(e.getKey())) {
                e.getValue().noteNow();   // the last position before the app let go
                e.getValue().stop();
                it.remove();
            }
        }
    }

    private final class Watcher extends MediaController.Callback {
        private final MediaController c;

        Watcher(MediaController c) {
            this.c = c;
            c.registerCallback(this, main);
        }

        void noteNow() {
            try {
                ContinueStore.note(MediaWatch.this, c.getPackageName(), c.getMetadata(), c.getPlaybackState());
            } catch (Exception ignored) {
                // a session that went away mid-read
            }
        }

        void stop() {
            try {
                c.unregisterCallback(this);
            } catch (Exception ignored) {
                // already gone
            }
        }

        @Override
        public void onPlaybackStateChanged(PlaybackState state) {
            noteNow();
        }

        @Override
        public void onMetadataChanged(MediaMetadata metadata) {
            noteNow();
        }

        @Override
        public void onSessionDestroyed() {
            stop();
        }
    }
}
