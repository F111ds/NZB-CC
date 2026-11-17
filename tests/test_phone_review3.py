"""Phone app review fixes (3.9.6): keys never in logs or sent to other hosts, offline answers the page
can use, Windows file-sharing retries, UTF-8 PowerShell, cached Jellyfin settings, parallel Kodi."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))
import io, json, logging, os, tempfile, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import mobile_server as m

tmp = Path(tempfile.mkdtemp())
m.SETTINGS_FILE = tmp / "mobile_settings.json"
m.CONFIG_FILE = tmp / "nzb_manager_config.json"


def serve(h):
    s = ThreadingHTTPServer(("127.0.0.1", 0), h); threading.Thread(target=s.serve_forever, daemon=True).start(); return s


# ---- 1. An unusable URL never escapes as a raw error carrying the API key ----
KEY = "SECRETKEY1234567890"
m._KNOWN_SECRETS = frozenset([KEY])
try:
    m.http_fetch(f"http://127.0.0.1:1/get nzb/x.nzb?apikey={KEY}\x01", timeout=2, max_bytes=100)
    raise AssertionError("no error")
except m.UpstreamError as exc:
    assert KEY not in str(exc), exc
ix = {"url": "https://indexer.example/api", "api_key": KEY, "name": "Ix"}
u = m.build_download_url(ix, "/get nzb/x.nzb?id=5")
assert " " not in u and "/get%20nzb/x.nzb" in u and "apikey=" in u, u           # spaces encoded, not an error

# ... and nothing written to the log contains a key, not even a traceback.
buf = io.StringIO()
h = logging.StreamHandler(buf)
h.setFormatter(m._RedactingFormatter("%(levelname)s %(message)s"))
lg = logging.getLogger("redact-test"); lg.addHandler(h); lg.propagate = False
try:
    raise RuntimeError(f"failed fetching https://x/get?apikey={KEY}")
except RuntimeError:
    lg.exception("boom %s", KEY)
assert KEY not in buf.getvalue() and "***" in buf.getvalue(), buf.getvalue()

# ---- 2. Credentials don't follow a redirect to another host ----
seen = {}


class Target(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        seen["headers"] = dict(self.headers)
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")


target = serve(Target)


class Redirector(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        # "localhost" vs "127.0.0.1": a different host name for the same test machine
        self.send_response(302); self.send_header("Location", f"http://localhost:{target.server_address[1]}/next"); self.end_headers()


red = serve(Redirector)
real_public = m.host_is_public
m.host_is_public = lambda host: True          # let the test's "other host" through the private-address rule
try:
    body = m.http_fetch(f"http://127.0.0.1:{red.server_address[1]}/start", timeout=5, max_bytes=100,
                        headers={"Authorization": "Basic a29kaTpwYXNz", "X-Emby-Token": "jf-key-123456",
                                 "X-Auth-PSK": "tvpsk", "Accept": "text/plain"})
finally:
    m.host_is_public = real_public
assert body == b"ok"
low = {k.lower(): v for k, v in seen["headers"].items()}
assert "authorization" not in low and "x-emby-token" not in low and "x-auth-psk" not in low, low
assert low.get("accept") == "text/plain"                                          # harmless headers still go

# ---- 3/4. Offline Kodi / a refused TV key come back as "problem", which the page can show ----
page = m.PAGE
assert "d.problem||'Kodi is offline.'" in page and "tvInfo.problem" in page and "tvInfo.error" not in page
src = Path(m.__file__).read_text(encoding="utf-8")
assert '"online": False, "error"' not in src and 'view["error"]' not in src

# ---- 5. Settings: a briefly busy file is retried; reading and saving can't collide ----
calls = {"n": 0}
real_read = Path.read_text


def flaky(self, *a, **k):
    if self == m.SETTINGS_FILE and calls["n"] < 3:
        calls["n"] += 1
        raise PermissionError(32, "The process cannot access the file because it is being used by another process")
    return real_read(self, *a, **k)


m.SETTINGS_FILE.write_text(json.dumps({"tv_host": "192.168.68.62"}))
Path.read_text = flaky
try:
    assert m.load_settings()["tv_host"] == "192.168.68.62"                       # retried, not "defaults"
finally:
    Path.read_text = real_read
assert calls["n"] == 3

# A file that stays unreadable raises instead of returning defaults (which a save would write back).
Path.read_text = lambda self, *a, **k: (_ for _ in ()).throw(PermissionError(32, "busy")) if self == m.SETTINGS_FILE else real_read(self, *a, **k)
try:
    m.load_settings(); raise AssertionError("defaults returned for a busy file")
except PermissionError:
    pass
finally:
    Path.read_text = real_read
assert json.loads(m.SETTINGS_FILE.read_text())["tv_host"] == "192.168.68.62"     # untouched

# Damaged JSON: a copy is kept aside.
m.SETTINGS_FILE.write_text("{ not json")
assert m.load_settings()["tv_host"] == ""
assert (tmp / "mobile_settings.damaged.json").read_text() == "{ not json"

# Many threads saving and reading at once: no errors, last write wins.
m.SETTINGS_FILE.write_text("{}")
errs = []


def worker(i):
    try:
        for _ in range(20):
            m.update_settings(lambda st: st.update(tv_mac=f"AA:BB:CC:DD:EE:{i:02X}"))
            m.load_settings()
    except Exception as e:
        errs.append(e)


ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
[t.start() for t in ts]; [t.join() for t in ts]
assert not errs, errs

# A failing job in the background worker doesn't stop the others.
w = m.BackgroundWorker()
ran = []
w.check_drives = lambda settings, now: ran.append("drives")
real_due, real_run = m.due_saved_search, m.run_saved_search
m.due_saved_search = lambda settings, now: {"id": "x"}
m.run_saved_search = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("saved search broke"))
real_tv, real_watched = m.tv_refresh, m.tv_watched_refresh
m.tv_refresh = m.tv_watched_refresh = lambda: None
try:
    w._next_drives = 0
    w.tick(now=time.time())
finally:
    m.due_saved_search, m.run_saved_search = real_due, real_run
    m.tv_refresh, m.tv_watched_refresh = real_tv, real_watched
assert ran == ["drives"]

# ---- 6. PowerShell output is read as UTF-8 ----
assert "UTF8Encoding $false" in m.PS_UTF8 and "OutputEncoding" in m.PS_UTF8
assert 'encoding="utf-8"' in src.split("def run_powershell", 1)[1].split("def ", 1)[0]

# ---- 8. Jellyfin settings are worked out once until a file changes ----
m.SETTINGS_FILE.write_text("{}")
m.CONFIG_FILE.write_text(json.dumps({"media_server_type": "Jellyfin", "media_server_url": "192.168.68.60",
                                     "media_server_token": "a" * 32}))
count = {"n": 0}
real_uncached = m._jellyfin_conf_uncached


def counting():
    count["n"] += 1
    return real_uncached()


m._jellyfin_conf_uncached = counting
c1 = m.jellyfin_conf(); c2 = m.jellyfin_conf(); c3 = m.jellyfin_conf()
assert count["n"] == 1 and c1 == c2 == c3 and c1["url"] == "http://192.168.68.60:8096", (count, c1)
time.sleep(0.01)
m.CONFIG_FILE.write_text(json.dumps({"media_server_type": "Jellyfin", "media_server_url": "192.168.68.61:8096",
                                     "media_server_token": "b" * 32}))
assert m.jellyfin_conf()["url"] == "http://192.168.68.61:8096" and count["n"] == 2   # a change is picked up
m._jellyfin_conf_uncached = real_uncached

# ---- 9. Continue watching asks Kodi in parallel but keeps the order ----
shows = [{"tvshowid": i, "title": f"Show {i}", "lastplayed": f"2026-11-{20 - i:02d}", "episode": 10, "watchedepisodes": 2}
         for i in range(1, 9)]
real_call, real_eps = m.kodi_call, m.kodi_show_episodes
m.kodi_call = lambda method, params=None, timeout=6: {"tvshows": shows}
active = {"now": 0, "max": 0}
lock = threading.Lock()


def slow_eps(show_id):
    with lock:
        active["now"] += 1; active["max"] = max(active["max"], active["now"])
    time.sleep(0.1)
    with lock:
        active["now"] -= 1
    return [{"id": show_id * 100, "season": 1, "episode": 3, "title": "E3", "watched": False, "resume": 0,
             "runtime": 0, "aired": "", "file": ""}]


m.kodi_show_episodes = slow_eps
try:
    t0 = time.monotonic()
    cw = m.kodi_continue_watching(limit=6)
    took = time.monotonic() - t0
finally:
    m.kodi_call, m.kodi_show_episodes = real_call, real_eps
assert [c["show"]["id"] for c in cw] == [1, 2, 3, 4, 5, 6], [c["show"]["id"] for c in cw]
assert active["max"] > 1 and took < 0.5, (active, took)                          # in parallel, not 8 x 0.1 s

# ---- 10. The TV job and the TV files have their own locks ----
assert m._tv_lock is not m._tv_files_lock

# ---- 7. The page lets go of old posters ----
assert "function trimArt" in page and "URL.revokeObjectURL" in page and "trimArt();" in page

target.shutdown(); red.shutdown()
print("phone review 3 OK")
