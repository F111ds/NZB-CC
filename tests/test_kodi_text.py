"""Phone app "Type on TV": sends text into the text box open in Kodi."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))
import json, tempfile, threading, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import mobile_server as m

state = {"window": 10000}
calls = []


class FakeKodi(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        calls.append((req["method"], req.get("params")))
        res = {"currentwindow": {"id": state["window"], "label": "x"}} if req["method"] == "GUI.GetProperties" else "OK"
        b = json.dumps({"jsonrpc": "2.0", "id": 1, "result": res}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(b)


def serve(h):
    s = ThreadingHTTPServer(("127.0.0.1", 0), h); threading.Thread(target=s.serve_forever, daemon=True).start(); return s


kodi = serve(FakeKodi)
tmp = Path(tempfile.mkdtemp())
(tmp / "nzb_manager_config.json").write_text(json.dumps({
    "sab_url": "127.0.0.1:1", "sab_api_key": "plainsabkey123", "indexers": [],
    "kodi_url": f"127.0.0.1:{kodi.server_address[1]}"}))
m.CONFIG_FILE = tmp / "nzb_manager_config.json"
m.SETTINGS_FILE = tmp / "mobile_settings.json"
m._cfg_state.update(mtime=None, data=None)
m._token_bytes = b"testtoken"
app = serve(m.MobileHandler)
base = f"http://127.0.0.1:{app.server_address[1]}"


def api(path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"X-Mobile-Token": "testtoken", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())


# No text box open: refused with a clear message, nothing typed.
st, d = api("/api/kodi/text", {"text": "hello"})
assert st == 400 and "No text box is open" in d["error"], d
assert not any(c[0] == "Input.SendText" for c in calls)

# Keyboard open: typed and confirmed (default), or typed only.
state["window"] = 10103
st, d = api("/api/kodi/text", {"text": "http://192.168.68.60:8096"})
assert st == 200 and d == {"ok": True, "window": "keyboard"}, d
assert calls[-1] == ("Input.SendText", {"text": "http://192.168.68.60:8096", "done": True}), calls[-1]
st, d = api("/api/kodi/text", {"text": "1234", "done": False})
assert st == 200 and calls[-1] == ("Input.SendText", {"text": "1234", "done": False})

# Numeric dialog counts too; pasted line breaks become spaces; too long is refused.
state["window"] = 10109
assert api("/api/kodi/text", {"text": "line one\nline two"})[0] == 200
assert calls[-1][1]["text"] == "line one line two"
assert api("/api/kodi/text", {"text": "x" * 501})[0] == 400

assert 'id="kodytype"' in m.PAGE and "/api/kodi/text" in m.PAGE
app.shutdown(); kodi.shutdown()
print("kodi text OK")
