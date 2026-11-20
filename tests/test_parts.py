"""mobile_server.py's page and icons live in mobile_page.py and mobile_icons.py.

Checks they're picked up, and that the server still loads (with a placeholder page and a plain icon, and
says what's missing) when someone updates only mobile_server.py.
"""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))

import subprocess
import sys

import app_page
import mobile_icons
import mobile_page
import mobile_server as m

assert m.MISSING_PARTS == []
assert m.PAGE is mobile_page.PAGE and len(m.PAGE) > 100_000
assert m.ICON_PNG[:8] == b"\x89PNG\r\n\x1a\n" and len(m.ICON_PNG) > 5000
assert m.KODI_ICON_PNG[:4] == b"\x89PNG" and m.KODI_ICON_MASKABLE_PNG[:4] == b"\x89PNG"
assert "activeTab='kodi'" in m.KODI_PAGE and "<title>Kodi</title>" in m.KODI_PAGE
assert m.APP_PAGE is app_page.APP_PAGE
print("parts found ok")

# ---- the all-in-one app at /app ----
import json, re, shutil, tempfile, threading, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
tok = "t" * 43
m._token_bytes = tok.encode()
srv = ThreadingHTTPServer(("127.0.0.1", 0), m.MobileHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
def get(path, headers=None):
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, headers=headers or {}), timeout=10) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers
class NoRedir(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None
try:
    urllib.request.build_opener(NoRedir).open(base + "/app", timeout=5)
    raise AssertionError("no redirect")
except urllib.error.HTTPError as e:
    assert e.code == 302 and e.headers["Location"] == "/app/"
code, body, hdr = get("/app/")
assert code == 200 and b"<title>HAL 9000</title>" in body and b'href="/app/manifest.webmanifest"' in body
assert "frame-ancestors 'none'" in hdr["Content-Security-Policy"] and "frame-src 'self'" in hdr["Content-Security-Policy"]
man = json.loads(get("/app/manifest.webmanifest")[1])
assert man["start_url"] == "/app/" and man["scope"] == "/app/" and man["id"] == "/app/"
assert get("/app/icon.png")[1][:4] == b"\x89PNG" and get("/app/icon-512.png")[1][:4] == b"\x89PNG"
assert get("/api/home")[0] == 401                       # the app's data still needs the code
# the page's script parses, and every API it calls exists in the server
script = re.search(r"<script>(.*)</script>", app_page.APP_PAGE, re.S).group(1)
# review fixes: the queue card is the queue (no Queue tab), HAL's pages open inside the app, TV volume
assert "['queue','Queue']" not in script and "getSeg='upcoming'" in script
assert "function openHal(" in script and "location.href='/hal/" not in script and "hal:'close'" not in script.replace("hal==='close'", "")
assert "'/hal/api/tv/key'" in script and 'data-tvkey="mute"' in script
node = shutil.which("node")
if node:
    js = _Path(tempfile.mkdtemp()) / "app.js"
    js.write_text(script)
    r = subprocess.run([node, "--check", str(js)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
src = (ROOT / "mobile_server.py").read_text(encoding="utf-8")
for ep in set(re.findall(r"'(/api/[a-z/_]+)", script)):
    if ep.endswith("/"):
        continue
    assert f'"{ep}"' in src, ep
for ep in ("/api/queue/pause", "/api/queue/resume"):
    assert ep in src
srv.shutdown()
print("app ok")

# Without the parts: the server module still loads and builds both pages.
code = r"""
import sys, importlib.abc
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name in ("mobile_page", "mobile_icons", "app_page"):
            raise ImportError("blocked for the test: " + name)
sys.meta_path.insert(0, Block())
sys.path.insert(0, sys.argv[1])
import mobile_server as m
assert sorted(m.MISSING_PARTS) == ["app_page.py", "mobile_icons.py", "mobile_page.py"], m.MISSING_PARTS
assert "app_page.py" in m.APP_PAGE
assert "mobile_page.py" in m.PAGE and "update_all.ps1" in m.PAGE
assert m.ICON_PNG[:4] == b"\x89PNG"
assert "<title>Kodi</title>" in m.KODI_PAGE
print("ok")
"""
r = subprocess.run([sys.executable, "-c", code, str(ROOT)], capture_output=True, text=True, timeout=120)
assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stdout + r.stderr
print("missing parts ok")
print("\nALL PARTS TESTS PASSED")
