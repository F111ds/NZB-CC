"""Tests for: Media Server background audit + refresh batching, Kodi poll guard / scan max-wait /
IPv6, Media Inspector parsing / TV duplicates / cache pruning / subtitle cache, mobile lockout
bounds, and the main app's watch-folder auto-send."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))

import tempfile
import threading
import time
import types
from pathlib import Path

import kodi_client as kc
import media_inspector as mi
import media_server as ms
import mobile_server as mob

# ---------------- media_server: pure audit function ----------------
movies = [{"title": "Heat", "year": "1995", "file": "/data/movies/Heat (1995)/Heat.1995.mkv"}]
episodes = [
    {"showtitle": "Show A", "season": 1, "episode": 1, "file": "/data/tv/Show A/Season 01/S01E01.mkv"},
    {"showtitle": "Ghost", "season": 1, "episode": 1, "file": "/data/tv/Ghost/Season 01/S01E01.mkv"},
]
disk = [
    {"path": r"D:\Media\Movies\Heat (1995)\Heat.1995.mkv", "title": "Heat"},
    {"path": r"D:\Media\TV\Show A\Season 01\S01E01.mkv", "title": "A1"},
    {"path": r"D:\Media\TV\Show B\Season 01\S01E01.mkv", "title": "B1"},
]
missing, orphaned = ms.compute_audit(movies, episodes, disk, "")
assert [m["title"] for m in missing] == ["B1"], missing
assert [o["title"] for o in orphaned] == ["Ghost — S01E01"], orphaned
assert "folder_exists" in missing[0]
print("compute_audit ok")

# ---------------- kodi_client: URL + scan max wait ----------------
assert kc.normalize_kodi_url("http://[fe80::1]") == "http://[fe80::1]:8080"
assert kc.normalize_kodi_url("192.168.1.50") == "http://192.168.1.50:8080"
assert kc.normalize_kodi_url("http://host:9090/jsonrpc") == "http://host:9090"

fake = types.SimpleNamespace(
    _closing=False, _client=types.SimpleNamespace(configured=True),
    _cfg={"kodi_auto_scan": True, "kodi_notify_on_complete": False},
    _pending_lock=threading.Lock(), _pending_names=[], _pending_since=None, _scan_timer=None,
    _flush_completed=lambda: None,
)
kc.KodiPanel.notify_download_completed(fake, "a")
first_delay = fake._scan_timer.interval
fake._pending_since -= kc.SCAN_MAX_WAIT_S - 5  # pretend the first completion was ~3 min ago
kc.KodiPanel.notify_download_completed(fake, "b")
assert first_delay == kc.SCAN_DEBOUNCE_S and fake._scan_timer.interval <= 5.0, fake._scan_timer.interval
fake._scan_timer.cancel()
print("kodi url + max wait ok")

# ---------------- media_inspector: parsing, TV duplicates, subtitle cache, cache pruning ----------------
P = mi.parse_release_identity
r = P("One.Piece.S01E107.1080p.WEB")
assert r["media_type"] == "TV" and r["episode"] == 107, r
r = P("Show.Name.S02E03E04.720p.HDTV")
assert r["season"] == 2 and r["episode"] == 3 and r["display_title"].startswith("Show Name"), r
r = P("Anime.Show.1x105.1080p")
assert r["episode"] == 105, r

def item(path, media_type, **kw):
    it = mi.MediaItem(path=Path(path), filename=Path(path).name, folder="", media_type=media_type,
                      display_title="", year="", movie_key=kw.pop("movie_key", ""), size_bytes=1, mtime_ns=0)
    for k, v in kw.items():
        setattr(it, k, v)
    return it

items = [
    item("a/Show.S01E01.720p.mkv", "TV", tv_ep_key="tv::show::s01e01"),
    item("b/Show.S01E01.1080p.mkv", "TV", tv_ep_key="tv::show::s01e01"),
    item("c/Show.S01E02.mkv", "TV", tv_ep_key="tv::show::s01e02"),
    item("d/Heat.1995.mkv", "Movie", movie_key="heat::1995::"),
]
groups = mi.annotate_duplicate_movies(items)
assert list(groups) == ["tv::show::s01e01"] and items[0].is_duplicate and not items[2].is_duplicate

tmp = Path(tempfile.mkdtemp())
(tmp / "Movie.2020.mkv").write_bytes(b"x")
(tmp / "Movie.2020.en.srt").write_text("1")
mi._subtitle_files_in.cache_clear()
subs = mi._find_external_subtitles(tmp / "Movie.2020.mkv")
assert [s["language"] for s in subs] == ["en"]
(tmp / "Movie.2020.de.srt").write_text("1")
assert len(mi._find_external_subtitles(tmp / "Movie.2020.mkv")) == 1   # cached within a scan
mi._subtitle_files_in.cache_clear()
assert len(mi._find_external_subtitles(tmp / "Movie.2020.mkv")) == 2   # next scan sees it
calls = []
orig = mi.os.scandir
mi.os.scandir = lambda p: calls.append(p) or orig(p)
mi._subtitle_files_in.cache_clear()
for _ in range(50):
    mi._find_external_subtitles(tmp / "Movie.2020.mkv")
mi.os.scandir = orig
assert len(calls) == 1, len(calls)

panel = types.SimpleNamespace(_probe_cache={
    str(tmp / "Movie.2020.mkv"): {"data": {}},
    str(tmp / "Deleted.Film.2019.mkv"): {"data": {}},
    r"Z:\Elsewhere\Keep.mkv": {"data": {}},
})
removed = mi.MediaInspectorPanel._prune_cache(panel, [tmp], [tmp / "Movie.2020.mkv"])
assert removed == 1 and str(tmp / "Deleted.Film.2019.mkv") not in panel._probe_cache
assert r"Z:\Elsewhere\Keep.mkv" in panel._probe_cache   # outside the scanned roots: untouched
print("media inspector parsing / dupes / subtitles / pruning ok")

# ---------------- mobile_server: bounded lockout tracker ----------------
ft = mob.FailureTracker(8, 300)
for i in range(1200):
    ft.record(f"10.0.{i // 250}.{i % 250}")
for _ in range(50):
    ft.record("10.9.9.9")
assert len(ft._fails["10.9.9.9"]) <= 16 and ft.blocked_for("10.9.9.9") > 0
ft._fails = {f"k{i}": __import__("collections").deque([time.monotonic() - 1000]) for i in range(1500)}
ft.record("fresh")
assert len(ft._fails) < 10
print("mobile lockout bounded ok")

# ---------------- media_server panel: batched refreshes + background sync ----------------
import customtkinter as ctk
import nzb_command_center as ncc

app_tmp = Path(tempfile.mkdtemp())
ncc.CONFIG_FILE = app_tmp / "c.json"; ncc.STATS_FILE = app_tmp / "s.json"; ncc.CAPS_CACHE_FILE = app_tmp / "caps.json"
ncc.DEFAULT_CONFIG["indexers"] = []; ncc.DEFAULT_CONFIG["nzb_folder"] = str(app_tmp)
ncc.PIL_AVAILABLE = False
app = ncc.NzbManagerApp(); app.withdraw()
app.after_cancel(app._auto_refresh_after_id)

def pump(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        app.update(); time.sleep(0.02)
        if cond():
            return True
    return False

msp = app.media_server_panel
msp.server_url, msp.server_token = "http://x", "t"
refreshes = []
main_thread = threading.current_thread()
msp.client = types.SimpleNamespace(
    refresh_libraries=lambda: refreshes.append(1),
    get_movies=lambda: movies, get_tv_shows=lambda: [], get_episodes=lambda: episodes,
)
msp.REFRESH_DELAY_MS = 300
for n in ("one", "two", "three"):
    msp.notify_download_completed(n)
assert pump(lambda: len(refreshes) == 1, 5) and not msp._pending_refresh_names
pump(lambda: False, 0.6)
assert len(refreshes) == 1, "completions must be batched into one refresh"

disk_thread = []
msp.get_disk_items = lambda: disk_thread.append(threading.current_thread()) or disk
msp._sync_library()
assert pump(lambda: msp._audit_missing != [] or msp._audit_orphaned != [], 10)
assert disk_thread and disk_thread[0] is not main_thread, "disk list must be read off the UI thread"
assert [m["title"] for m in msp._audit_missing] == ["B1"]
print("media server batching + background sync ok")

# ---------------- main app: watch folder auto-send ----------------
watch = Path(tempfile.mkdtemp())
(watch / "already.here.nzb").write_text("<nzb></nzb>")
app.folder_entry.delete(0, "end"); app.folder_entry.insert(0, str(watch))
app.config_data["sab_api_key"] = "x"
sent = []
app.sab = types.SimpleNamespace(add_file=lambda p, category="*": sent.append(p.name))
app._refresh_sab_all = lambda *a, **k: None
app.WATCH_INTERVAL_MS, app.WATCH_SETTLE_S = 200, 0.3
app.auto_archive_var.set(True)
app.watch_var.set(True); app._on_watch_toggled()
NZB = ('<?xml version="1.0"?><nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">'
       '<file subject="a"><segments><segment bytes="1">x</segment></segments></file></nzb>')
(watch / "new.release.nzb").write_text(NZB)
(watch / "broken.nzb").write_text("not xml at all")
assert pump(lambda: "new.release.nzb" in sent, 10)
pump(lambda: False, 1.0)
assert sent == ["new.release.nzb"], sent                       # existing + invalid files not sent
assert (watch / "sent").exists() and not (watch / "new.release.nzb").exists()  # archived
log = app.log_box.get("1.0", "end")
assert "Auto-send failed for broken.nzb" in log and "not a valid NZB" in log
# typing another folder must not send that folder's files
other = Path(tempfile.mkdtemp()); (other / "elsewhere.nzb").write_text(NZB)
app.folder_entry.delete(0, "end"); app.folder_entry.insert(0, str(other))
pump(lambda: False, 1.0)
assert "elsewhere.nzb" not in sent
app.watch_var.set(False); app._on_watch_toggled()
app._on_close()
print("watch folder ok")
print("ROUND 3 TESTS PASSED")
