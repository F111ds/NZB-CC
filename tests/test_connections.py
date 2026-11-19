"""The app's Connections page: SABnzbd, indexers and Kodi, written into nzb_manager_config.json.

Uses a temporary config file and fake SABnzbd / indexer / Kodi servers on 127.0.0.1.
"""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))

import json
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import mobile_server as m

tmp = Path(tempfile.mkdtemp())
m.CONFIG_FILE = tmp / "nzb_manager_config.json"
m.SETTINGS_FILE = tmp / "mobile_settings.json"
original = {
    "sab_url": "http://127.0.0.1:8080", "sab_api_key": "0123456789abcdef0123456789abcdef",
    "indexers": [{"name": "NZBGeek", "url": "https://api.nzbgeek.info/", "api_key": "geekkey12345", "enabled": True, "caps": {"x": 1}}],
    "kodi_url": "http://192.168.68.62:8080", "kodi_username": "kodi", "kodi_password": "secretpw",
    "media_server_type": "Jellyfin", "window_geometry": "1374x1073+220+0", "search_history": ["a"],
}
m.CONFIG_FILE.write_text(json.dumps(original, indent=4), encoding="utf-8")

# ---- what the phone sees: no keys ----
v = m.connections_view()
blob = json.dumps(v)
for secret in ("0123456789abcdef0123456789abcdef", "geekkey12345", "secretpw"):
    assert secret not in blob, secret
assert v["sab"] == {"url": "http://127.0.0.1:8080", "key_set": True}
assert v["indexers"] == [{"index": 0, "name": "NZBGeek", "url": "https://api.nzbgeek.info/", "enabled": True, "key_set": True}]
assert v["kodi"] == {"url": "http://192.168.68.62:8080", "username": "kodi", "password_set": True}
print("view ok")

# ---- save: blank keys keep the saved ones, other fields untouched, backup kept ----
r = m.save_connections({
    "sab_url": "192.168.68.60:8080", "sab_api_key": "",
    "indexers": [{"index": 0, "name": "NZBGeek", "url": "https://api.nzbgeek.info", "enabled": False, "api_key": ""},
                 {"index": None, "name": "DrunkenSlug", "url": "https://api.drunkenslug.com", "enabled": True, "api_key": "slug_key-123"}],
    "kodi_url": "http://192.168.68.62:8080", "kodi_username": "kodi", "kodi_password": "",
})
assert r["ok"]
saved = json.loads(m.CONFIG_FILE.read_text(encoding="utf-8"))
assert saved["sab_url"] == "http://192.168.68.60:8080" and saved["sab_api_key"] == original["sab_api_key"]
assert saved["indexers"][0]["api_key"] == "geekkey12345" and saved["indexers"][0]["enabled"] is False
assert saved["indexers"][0]["caps"] == {"x": 1}                       # fields the app doesn't know about survive
assert saved["indexers"][1] == {"name": "DrunkenSlug", "url": "https://api.drunkenslug.com", "enabled": True, "api_key": "slug_key-123"}
assert saved["kodi_password"] == "secretpw"
for k in ("media_server_type", "window_geometry", "search_history"):
    assert saved[k] == original[k], k
bak = json.loads((tmp / "nzb_manager_config.json.bak").read_text(encoding="utf-8"))
assert bak == original
# remove an indexer, change a key, clear the Kodi password
m.save_connections({"indexers": [{"index": 1, "name": "DrunkenSlug", "url": "https://api.drunkenslug.com", "enabled": True, "api_key": ""}],
                    "sab_api_key": "ffffffffffffffffffffffffffffffff", "clear_kodi_password": True})
saved = json.loads(m.CONFIG_FILE.read_text(encoding="utf-8"))
assert [x["name"] for x in saved["indexers"]] == ["DrunkenSlug"] and saved["indexers"][0]["api_key"] == "slug_key-123"
assert saved["sab_api_key"] == "ffffffffffffffffffffffffffffffff" and saved["kodi_password"] == ""
assert m.get_config()["sab_api_key"] == "ffffffffffffffffffffffffffffffff"   # picked up at once
# bad input is refused and changes nothing
before = m.CONFIG_FILE.read_text(encoding="utf-8")
for bad in ({"sab_api_key": "bad key!"}, {"indexers": [{"name": "", "url": "x"}]}, {"indexers": [{"name": "A", "url": "ftp://x y"}]},
            {"sab_url": "http://user:pw@host"}, {"indexers": "nope"}, {"kodi_password": "a\nb"},
            {"indexers": [{"name": "A", "url": "https://a.b", "api_key": "short"}]}):
    try:
        m.save_connections(bad)
        raise AssertionError(bad)
    except ValueError:
        pass
assert m.CONFIG_FILE.read_text(encoding="utf-8") == before
print("save ok")


# ---- tests against fake servers ----
class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype="application/json"):
        b = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/api?mode=queue"):
            ok = "apikey=ffffffffffffffffffffffffffffffff" in self.path
            self._send(json.dumps({"queue": {}} if ok else {"status": False, "error": "API Key Incorrect"}))
        elif self.path.startswith("/api?t=caps"):
            ok = "apikey=slug_key-123" in self.path
            self._send('<?xml version="1.0"?><caps><server title="x"/></caps>' if ok else '<error code="100" description="Incorrect user credentials"/>', "text/xml")
        else:
            self._send("<html>hello</html>", "text/html")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(n)
        if self.headers.get("Authorization"):
            self._send('{"id":1,"jsonrpc":"2.0","result":"pong"}')
        else:
            self.send_response(401)
            self.end_headers()


srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
assert m.test_connection({"what": "sab", "url": base})["ok"] is True                      # saved key
r = m.test_connection({"what": "sab", "url": base, "key": "0000000000000000"})
assert r["ok"] is False and "API Key Incorrect" in r["message"]
assert m.test_connection({"what": "indexer", "index": 0, "url": base})["ok"] is True       # saved key of indexer 0
r = m.test_connection({"what": "indexer", "url": base, "key": "wrongkey123"})
assert r["ok"] is False and "Incorrect user credentials" in r["message"]
assert m.test_connection({"what": "kodi", "url": base, "username": "kodi", "password": "pw"})["ok"] is True
assert m.test_connection({"what": "kodi", "url": base, "username": "", "password": ""})["ok"] is False   # 401
assert m.test_connection({"what": "sab", "url": "http://127.0.0.1:1"})["ok"] is False
try:
    m.test_connection({"what": "nothing"})
    raise AssertionError("unknown test")
except ValueError:
    pass
print("tests ok")

# ---- over HTTP: needs the access code ----
m._token_bytes = b"t" * 43
http_srv = ThreadingHTTPServer(("127.0.0.1", 0), m.MobileHandler)
threading.Thread(target=http_srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{http_srv.server_address[1]}"


def call(path, body=None, tok="t" * 43):
    req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Mobile-Token": tok, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


assert call("/api/connections", tok="wrong")[0] == 401
code, d = call("/api/connections")
assert code == 200 and d["sab"]["key_set"] and "ffffffff" not in json.dumps(d)
code, d = call("/api/connections", {"kodi_username": "osmc"})
assert code == 200 and json.loads(m.CONFIG_FILE.read_text())["kodi_username"] == "osmc"
assert call("/api/connections", {"sab_api_key": "nope!"})[0] == 400
code, d = call("/api/connections/test", {"what": "sab", "url": base})
assert code == 200 and d["ok"] is True
http_srv.shutdown()
srv.shutdown()
print("http ok")
print("\nALL CONNECTIONS TESTS PASSED")
