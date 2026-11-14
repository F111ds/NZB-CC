"""mobile_server review fixes: Kodi error reporting, paged movie list, per-NZB add limit,
background library index, and config-reload retry."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))
import json, os, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import mobile_server as m

MOVIES = [{"movieid": i, "title": f"Movie {i:05d}", "year": 2000 + i % 25} for i in range(1203)]
mode = {"status": 200, "slow_episodes": 0.0}
calls = []


class FakeKodi(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        calls.append(req["method"])
        if mode["status"] != 200:
            self.send_response(mode["status"]); self.end_headers(); self.wfile.write(b"no"); return
        params = req.get("params") or {}
        if req["method"] == "VideoLibrary.GetMovies":
            lim = params.get("limits") or {"start": 0, "end": len(MOVIES)}
            res = {"movies": MOVIES[lim["start"]:lim["end"]], "limits": {"total": len(MOVIES)}}
        elif req["method"] == "VideoLibrary.GetEpisodes":
            time.sleep(mode["slow_episodes"])
            res = {"episodes": [{"showtitle": "Severance", "season": 2, "episode": 10}]}
        else:
            res = "OK"
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": res}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


kodi = ThreadingHTTPServer(("127.0.0.1", 0), FakeKodi)
threading.Thread(target=kodi.serve_forever, daemon=True).start()

tmp = Path(tempfile.mkdtemp())
cfg_path = tmp / "nzb_manager_config.json"
cfg = {"sab_url": "127.0.0.1:1", "sab_api_key": "plainsabkey123", "kodi_url": f"127.0.0.1:{kodi.server_address[1]}", "indexers": []}
cfg_path.write_text(json.dumps(cfg))
m.CONFIG_FILE = cfg_path
m._cfg_state.update(mtime=None, data=None)

# 1. Paged movie list returns the whole library in several calls.
calls.clear()
movies = m.kodi_all_movies()
assert len(movies) == 1203 and movies[-1]["movieid"] == 1202, len(movies)
assert calls.count("VideoLibrary.GetMovies") == 3, calls

# 1. Errors are reported for what they are, not as "Kodi is off".
saved = m.MAX_KODI_BYTES
m.MAX_KODI_BYTES = 200
try:
    m.kodi_call("VideoLibrary.GetMovies"); raise AssertionError("expected size error")
except m.UpstreamError as exc:
    assert "safety limit" in str(exc) and "reach" not in str(exc), exc
m.MAX_KODI_BYTES = saved
mode["status"] = 500
try:
    m.kodi_call("JSONRPC.Ping"); raise AssertionError("expected HTTP error")
except m.UpstreamError as exc:
    assert str(exc).startswith("Kodi: HTTP 500"), exc
mode["status"] = 401
try:
    m.kodi_call("JSONRPC.Ping"); raise AssertionError("expected auth error")
except m.UpstreamError as exc:
    assert "username / password" in str(exc), exc
mode["status"] = 200

# 2. The add limiter counts every NZB, all-or-nothing.
lim = m.SlidingWindowLimiter(40, 60)
assert all(lim.allow("ip", 10) for _ in range(4))
assert not lim.allow("ip", 10) and not lim.allow("ip")
lim = m.SlidingWindowLimiter(5, 60)
assert lim.allow("ip", 3) and not lim.allow("ip", 3) and lim.allow("ip", 2) and not lim.allow("ip")

# 3. A background refresh returns at once and fills the index later.
mode["slow_episodes"] = 1.5
m._lib_state.update(ts=0.0, ok=False, movies={}, episodes=set(), shows=set())
t0 = time.monotonic()
idx = m.kodi_library_index(max_age=0, background=True)
assert time.monotonic() - t0 < 0.5 and not idx["ok"], "search waited for Kodi"
deadline = time.monotonic() + 10
while not m._lib_state["ok"] and time.monotonic() < deadline:
    time.sleep(0.05)
assert m._lib_state["ok"] and ("severance", 2, 10) in m._lib_state["episodes"]
assert m.library_match("Severance.S02E10.1080p", m._lib_state) == "episode"
mode["slow_episodes"] = 0.0

# 4. A failed reload is retried even if the fixed file keeps the same timestamp.
m.CONFIG_RETRY_SECONDS = 0.2
first = m.get_config()
assert first["sab_api_key"] == "plainsabkey123"
cfg_path.write_text("{ half-written")
broken_ns = cfg_path.stat().st_mtime_ns + 1_000_000_000
os.utime(cfg_path, ns=(broken_ns, broken_ns))
assert m.get_config() is first                      # keeps the previous settings
cfg["sab_api_key"] = "newsabkey456"
cfg_path.write_text(json.dumps(cfg))
os.utime(cfg_path, ns=(broken_ns, broken_ns))       # same timestamp as the broken version
assert m.get_config() is first                      # still within the retry delay
time.sleep(0.3)
assert m.get_config()["sab_api_key"] == "newsabkey456", "fixed config was never picked up"

kodi.shutdown()
print("REVIEW FIX TESTS PASSED")
