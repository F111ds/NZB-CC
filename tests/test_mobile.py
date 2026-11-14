import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]  # the project folder
_sys.path.insert(0, str(ROOT))
import json, sys, tempfile, threading, time, types, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import mobile_server as m

# ---------- redirects ----------
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        port = self.server.server_address[1]
        if self.path == "/same":
            self.send_response(302); self.send_header("Location", f"http://127.0.0.1:{port}/nzb"); self.end_headers()
        elif self.path == "/other":
            self.send_response(302); self.send_header("Location", f"http://localhost:{port}/nzb"); self.end_headers()
        else:
            self.send_response(200); self.end_headers(); self.wfile.write(b"<nzb></nzb>")
srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
assert m.http_fetch(base + "/same", timeout=5, max_bytes=1000) == b"<nzb></nzb>"   # same host: allowed
try:
    m.http_fetch(base + "/other", timeout=5, max_bytes=1000); raise AssertionError("private redirect allowed")
except m.UpstreamError as e:
    assert "private" in str(e), e
h = m._SafeRedirectHandler()
req = types.SimpleNamespace(full_url="https://indexer.example/getnzb?id=1")
try:
    h.redirect_request(req, None, 302, "", {}, "http://indexer.example/x"); raise AssertionError("downgrade allowed")
except m.UpstreamError as e:
    assert "HTTPS" in str(e)
srv.shutdown()

# ---------- one bad indexer key doesn't stop the server ----------
cfg = {"sab_url": "127.0.0.1:8080", "sab_api_key": "plainsabkey123",
       "indexers": [{"name": "Good", "url": "https://good.example", "api_key": "goodkey12345"},
                    {"name": "Undecryptable", "url": "https://bad.example", "api_key": "dpapi:AAAAAAAA"},
                    {"name": "Damaged", "url": "https://dmg.example", "api_key": "dpapi:%%%not-base64"}]}
p = Path(tempfile.mkdtemp()) / "nzb_manager_config.json"; p.write_text(json.dumps(cfg))
c = m.load_config(p)
by = {ix["name"]: ix for ix in c["indexers"]}
assert by["Good"]["api_key"] == "goodkey12345" and not by["Good"]["key_error"]
assert by["Undecryptable"]["key_error"] and by["Damaged"]["key_error"], by
try:
    m.search_one(by["Damaged"], "x", "", 5); raise AssertionError
except m.UpstreamError as e:
    assert "decrypted" in str(e)

# ---------- redaction swapped atomically ----------
assert isinstance(m._KNOWN_SECRETS, frozenset) and "goodkey12345" in m._KNOWN_SECRETS
assert "goodkey12345" not in m.redact("err goodkey12345 / apikey=abc")
assert "apikey=***" in m.redact("apikey=abc")

# ---------- batch add runs in parallel, keeps order, reports errors ----------
sent = {}
def fake_add(rid, cat, prio):
    time.sleep(0.5)
    if rid == "bad": raise m.UpstreamError("indexer down")
    if rid == "boom": raise KeyError("x")
    return {"id": rid}
fake = types.SimpleNamespace(_add_one=fake_add, _limited=lambda *a, **k: False,
                             send_json=lambda st, d: sent.update(status=st, data=d))
t0 = time.time()
m.MobileHandler._post_add_batch(fake, {"ids": ["a", "bad", "b", "boom", "c", "d"]})
dt = time.time() - t0
assert [x["id"] for x in sent["data"]["added"]] == ["a", "b", "c", "d"]
errs = {e["id"]: e["error"] for e in sent["data"]["errors"]}
assert errs["bad"] == "indexer down" and "Unexpected" in errs["boom"]
assert dt < 1.6, dt   # was ~3 s one-by-one
print(f"batch of 6 took {dt:.1f}s")

# ---------- page script still parses ----------
script = re.search(r"<script>(.*)</script>", m.PAGE, re.S).group(1)
Path(tempfile.gettempdir(), "mobile_page.js").write_text(script, encoding="utf-8")
print("MOBILE TESTS PASSED")
