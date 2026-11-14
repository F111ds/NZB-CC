"""Second review fixes: bounded episode-gap detection, and a Home tab that a slow or
switched-off Kodi can't stall."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))
import json, socket, tempfile, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import mobile_server as m

# 1. Gap detection ignores date-numbered / stray huge episode numbers (and never builds huge lists).
assert m.episode_gaps([1, 2, 4, 7]) == [3, 5, 6]
t0 = time.monotonic()
assert m.episode_gaps([1, 20241113]) == []
assert m.episode_gaps(range(1, 600)) == []
assert m.episode_gaps([1, 2, 90]) == []                       # one stray number, not 87 missing episodes
assert time.monotonic() - t0 < 0.5
assert m.episode_gaps([1, 3]) == [2] and m.episode_gaps([5]) == [1, 2, 3, 4]

DELAY = 2.0


def reply(h, body):
    b = json.dumps(body).encode()
    h.send_response(200); h.send_header("Content-Type", "application/json"); h.send_header("Content-Length", str(len(b))); h.end_headers(); h.wfile.write(b)


class SlowKodi(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        time.sleep(DELAY)
        reply(self, {"jsonrpc": "2.0", "id": 1, "result": []})     # nothing playing


class SlowSab(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        time.sleep(DELAY / 2)
        reply(self, {"queue": {"slots": []}, "history": {"slots": []}})


def serve(h):
    s = ThreadingHTTPServer(("127.0.0.1", 0), h); threading.Thread(target=s.serve_forever, daemon=True).start(); return s
kodi, sab = serve(SlowKodi), serve(SlowSab)

tmp = Path(tempfile.mkdtemp())
cfg = {"sab_url": f"127.0.0.1:{sab.server_address[1]}", "sab_api_key": "plainsabkey123",
       "kodi_url": f"127.0.0.1:{kodi.server_address[1]}", "indexers": []}
cfg_path = tmp / "nzb_manager_config.json"
cfg_path.write_text(json.dumps(cfg))
m.CONFIG_FILE, m.SETTINGS_FILE = cfg_path, tmp / "mobile_settings.json"
m._cfg_state.update(mtime=None, data=None)
m._token_bytes = b"testtoken"
app = serve(m.MobileHandler)


def home():
    req = urllib.request.Request(f"http://127.0.0.1:{app.server_address[1]}/api/home", headers={"X-Mobile-Token": "testtoken"})
    t = time.monotonic()
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()), time.monotonic() - t


# 2a. A slow Kodi is asked at the same time as a slow SABnzbd (2 calls x DELAY/2), not after it.
data, took = home()
assert data["kodi"]["online"] and data["sab_ok"] and "media" not in data, data
assert took < DELAY * 1.7, f"Home took {took:.1f}s; Kodi and SABnzbd were asked one after the other"

# 2b. A switched-off Kodi is remembered for a few seconds instead of re-tried on every poll.
def dead_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
cfg["kodi_url"] = f"127.0.0.1:{dead_port()}"
cfg_path.write_text(json.dumps(cfg)); m._cfg_state.update(mtime=None, data=None)
data, _ = home()
assert data["kodi"] == {"configured": True, "online": False, "error": "Can't reach Kodi — is it on?"}, data["kodi"]
assert m.backend_recently_down("kodi|" + m.get_config()["kodi"]["url"])
real_fetch, fetched = m.http_fetch, []
m.http_fetch = lambda url, **kw: fetched.append(url) or real_fetch(url, **kw)
data, _ = home()
m.http_fetch = real_fetch
assert not any("/jsonrpc" in u for u in fetched), fetched   # skipped while remembered as down
assert data["kodi"]["error"] == "Can't reach Kodi — is it on?"

# ...and tried again once the window has passed; a success clears the memory.
cfg["kodi_url"] = f"127.0.0.1:{kodi.server_address[1]}"
cfg_path.write_text(json.dumps(cfg)); m._cfg_state.update(mtime=None, data=None)
m.mark_backend("kodi|" + m.get_config()["kodi"]["url"], "Can't reach Kodi — is it on?")
m.BACKEND_RETRY_SECONDS = 0.1
time.sleep(0.2)
key = "kodi|" + m.get_config()["kodi"]["url"]
with m._down_lock:
    m._backend_down[key] = (time.monotonic() - 1, "old")                          # expired entry
data, _ = home()
assert data["kodi"]["online"] and not m.backend_recently_down(key), data["kodi"]
print("REVIEW 2 FIX TESTS PASSED")
