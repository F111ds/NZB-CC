import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]  # the project folder
_sys.path.insert(0, str(ROOT))
import gzip, json, sys, tempfile, threading, types, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import nzb_command_center as ncc

NZB = b'<?xml version="1.0"?><nzb xmlns="http://www.newzbin.com/DTD/2003/nzb"><file subject="a"><segments><segment bytes="10">x</segment></segments></file></nzb>'
ROUTES = {
    "/gz": gzip.compress(NZB), "/plain": NZB, "/trunc": NZB[:-10],
    "/jsonerr": b'{"error":"Daily download limit reached"}', "/empty": b"",
    "/search_json": json.dumps({"channel": {"item": [{"title": "Movie.2020.1080p", "link": "http://x/1",
                                 "enclosure": {"@attributes": {"url": "http://x/1", "length": "100"}}}]}}).encode(),
    "/search_jsonerr": b'{"error":"Incorrect user credentials"}',
    "/search_nserr": b'<?xml version="1.0"?><x:error xmlns:x="urn:y" code="100" description="Bad key"/>',
    "/big": b"<nzb>" + b"x" * 5000,
}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        body = ROUTES[self.path.split("?")[0]]
        self.send_response(200); self.end_headers(); self.wfile.write(body)
srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
F = ncc.NewznabClient.fetch_nzb_bytes

assert F(base + "/gz") == NZB, "gzip not decompressed"
assert F(base + "/plain") == NZB
for path, expect in [("/trunc", "truncated"), ("/jsonerr", "not an NZB"), ("/empty", "empty")]:
    try: F(base + path); raise AssertionError(path + " accepted")
    except RuntimeError as e: assert expect in str(e), (path, e)
ncc.MAX_NZB_BYTES = 1000
try: F(base + "/big"); raise AssertionError("size cap ignored")
except RuntimeError as e: assert "safety limit" in str(e), e
ncc.MAX_NZB_BYTES = 50 * 1024 * 1024

def client(path):
    c = ncc.NewznabClient(base + path, "k"); c.base_url = base + path; return c
res = client("/search_json").search("movie")
assert len(res) == 1 and res[0]["title"] == "Movie.2020.1080p"
for path, expect in [("/search_jsonerr", "Incorrect user credentials"), ("/search_nserr", "Bad key")]:
    try: client(path).search("x"); raise AssertionError(path)
    except RuntimeError as e: assert expect in str(e), (path, e)
srv.shutdown()
print("download/search tests passed")

# ---------- real app on a temp config (no network: no SAB key, no indexers) ----------
tmp = Path(tempfile.mkdtemp())
ncc.CONFIG_FILE = tmp / "cfg.json"; ncc.STATS_FILE = tmp / "stats.json"; ncc.CAPS_CACHE_FILE = tmp / "caps.json"
ncc.DEFAULT_CONFIG["indexers"] = []; ncc.DEFAULT_CONFIG["nzb_folder"] = str(tmp)
app = ncc.NzbManagerApp(); app.withdraw()
for _ in range(20): app.update(); time.sleep(0.02)

# library matching cached between filter changes
calls = []
orig = ncc.match_release_to_library
ncc.match_release_to_library = lambda *a, **k: calls.append(1)
app.current_search_results = [dict(r, uid=str(i)) for i, r in enumerate(res * 3)]
for it in app.current_search_results: it["uid"] = str(id(it))
app._apply_filters_and_render(reset_page=True); first = len(calls)
app._apply_filters_and_render(reset_page=False); app._apply_filters_and_render(reset_page=False)
assert first == 3 and len(calls) == 3, (first, len(calls))
app.library_index = ncc.build_library_index([]); app._apply_filters_and_render()
assert len(calls) == 6, "not recomputed after index change"
ncc.match_release_to_library = orig

def activity():
    return app.log_box.get("1.0", "end") if hasattr(app, "log_box") else ""
# UI errors are logged, not swallowed
app._post_ui(lambda: 1 / 0)
for _ in range(10): app.update(); time.sleep(0.05)
assert "Internal UI error" in activity(), "UI error not logged"

# archive failure after a successful upload is a warning, not a failure
(tmp / "a.nzb").write_bytes(NZB); (tmp / "b.nzb").write_bytes(NZB)
app._scan_local_folder()
for v in app.local_row_vars.values(): v.set(True)
uploaded = []
app.sab = types.SimpleNamespace(add_file=lambda p, category="*": uploaded.append(p.name))
app.auto_archive_var.set(True)
def bad_archive(p): raise PermissionError("sent/ is read-only")
app._archive_sent_nzb = bad_archive
app._refresh_sab_all = lambda *a, **k: None
app._upload_selected_batch_nzbs()
for _ in range(40): app.update(); time.sleep(0.05)
log = activity()
assert sorted(uploaded) == ["a.nzb", "b.nzb"]
assert "queued, but archiving failed" in log, log[-500:]
assert "Uploaded 2/2" in app.status_bar.cget("text"), app.status_bar.cget("text")
print("archive warning:", [l for l in log.splitlines() if "archiving failed" in l][0])

# config write failure is reported, not raised
ncc.CONFIG_FILE = tmp  # a directory: writing it fails
app._on_kodi_config_changed({"kodi_url": "x"})
for _ in range(5): app.update()
assert "Could not save Kodi settings" in activity()
app._on_close()
print("APP TESTS PASSED")
