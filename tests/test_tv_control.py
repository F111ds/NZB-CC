"""Phone app: 'TV on + Kodi' and 'Open Kodi' against a fake Sony TV (IP control API) and a fake Kodi."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))
import json, tempfile, threading, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import mobile_server as m

PSK = "sonykey42"
TV = {"power": "standby", "kodi_running": False, "wake_at": None, "calls": [], "ignore_power_api": False}

class FakeTV(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        if self.path == "/sony/IRCC":
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()
            TV["calls"].append((self.path, "IRCC", body))
            if self.headers.get("X-Auth-PSK") != PSK:
                self.send_response(403); self.end_headers(); return
            assert "SOAPACTION" in {k.upper() for k in self.headers.keys()} and "X_SendIRCC" in body
            if "AAAAAQAAAAEAAAAuAw==" in body and TV["power"] != "active" and not TV["wake_at"]:
                TV["wake_at"] = time.time() + 1.0
            self.send_response(200); self.end_headers(); self.wfile.write(b"<ok/>"); return
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        TV["calls"].append((self.path, req["method"], req.get("params")))
        if self.headers.get("X-Auth-PSK") != PSK:
            self.send_response(403); self.end_headers(); self.wfile.write(b'{"error":[403,"Forbidden"]}'); return
        if TV["wake_at"] and time.time() >= TV["wake_at"]:
            TV["power"], TV["wake_at"] = "active", None
        meth, res, err = req["method"], None, None
        if meth == "getPowerStatus":
            res = [{"status": TV["power"]}]
        elif meth == "setPowerStatus":
            if req["params"][0]["status"] and not TV["ignore_power_api"]:
                TV["wake_at"] = time.time() + 1.5   # takes a moment to wake
            res = []
        elif meth == "getNetworkSettings":
            res = [[{"netif": "eth0", "hwAddr": "AA:BB:CC:DD:EE:FF", "ipAddrV4": "127.0.0.1"}]]
        elif meth == "getApplicationList":
            res = [[{"title": "YouTube", "uri": "com.sony.dtv.youtube"},
                    {"title": "Kodi", "uri": "com.sony.dtv.org.xbmc.kodi.org.xbmc.kodi.Splash"}]]
        elif meth == "setActiveApp":
            if TV["power"] != "active":
                err = [7, "Illegal State"]
            else:
                assert req["params"][0]["uri"].endswith("kodi.Splash"), req
                threading.Timer(1.0, lambda: TV.update(kodi_running=True)).start()
                res = []
        body = {"id": req["id"], "error": err} if err else {"id": req["id"], "result": res}
        b = json.dumps(body).encode(); self.send_response(200); self.end_headers(); self.wfile.write(b)

class FakeKodi(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if not TV["kodi_running"]:   # Kodi not open: refuse the connection like a closed app would
            self.close_connection = True; self.connection.close(); return
        b = json.dumps({"jsonrpc": "2.0", "id": 1, "result": "pong" if req["method"] == "JSONRPC.Ping" else "OK"}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(b)

def serve(h):
    s = ThreadingHTTPServer(("127.0.0.1", 0), h); threading.Thread(target=s.serve_forever, daemon=True).start(); return s
tv, kodi = serve(FakeTV), serve(FakeKodi)

tmp = Path(tempfile.mkdtemp())
cfg = {"sab_url": "127.0.0.1:1", "sab_api_key": "plainsabkey123", "kodi_url": f"127.0.0.1:{kodi.server_address[1]}", "indexers": []}
(tmp / "nzb_manager_config.json").write_text(json.dumps(cfg))
m.CONFIG_FILE = tmp / "nzb_manager_config.json"
m.SETTINGS_FILE = tmp / "mobile_settings.json"
m._cfg_state.update(mtime=None, data=None)
m._token_bytes = b"testtoken"
m.TV_WAKE_SECONDS, m.TV_KODI_SECONDS = 10, 10
m.send_wol = lambda mac: TV["calls"].append(("WOL", "WOL", mac))   # never broadcast on the real network

app = serve(m.MobileHandler)
base = f"http://127.0.0.1:{app.server_address[1]}"
def api(path, body=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Mobile-Token": "testtoken", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

# Not set up yet: the address defaults to the Kodi host, buttons refuse politely
st, t = api("/api/tv")
assert st == 200 and not t["configured"] and t["auto_host"] == "127.0.0.1", t
assert api("/api/tv/on", {})[0] == 400 and api("/api/tv/kodi", {})[0] == 400

# Bad inputs
assert api("/api/tv/settings", {"host": "8.8.8.8", "psk": PSK})[0] == 400        # public address
assert api("/api/tv/settings", {"host": "bad host!", "psk": PSK})[0] == 400
assert api("/api/tv/settings", {"host": "192.168.1.5:99999", "psk": PSK})[0] == 400  # port out of range
# a name is only accepted if it resolves to the home network
real_gai = m.socket.getaddrinfo
fake_dns = {"sony-tv.com": "93.184.216.34", "bravia.lan": "192.168.1.40"}
m.socket.getaddrinfo = lambda h, *a, **k: [(2, 1, 6, "", (fake_dns[h], 0))] if h in fake_dns else real_gai(h, *a, **k)
assert api("/api/tv/settings", {"host": "sony-tv.com", "psk": PSK})[0] == 400      # typo'd public name refused
assert m.validate_tv_host("bravia.lan") == "bravia.lan"
assert not m.tv_host_is_local("sony-tv.com")
m.socket.getaddrinfo = real_gai
host = f"127.0.0.1:{tv.server_address[1]}"

# Wrong key: saved, but the TV refuses it and the view says so
st, t = api("/api/tv/settings", {"host": host, "psk": "wrongkey"})
assert st == 200 and t["configured"] and "Pre-Shared Key" in t.get("error", ""), t

# Right key: TV reported in standby; key never echoed
st, t = api("/api/tv/settings", {"host": host, "psk": PSK})
assert st == 200 and t["power"] == "standby" and t["psk_set"] and PSK not in json.dumps(t), t
assert PSK in m._EXTRA_SECRETS

# Open Kodi while the TV is off -> told to use TV on + Kodi
st, d = api("/api/tv/kodi", {})
assert st == 400 and "TV on + Kodi" in d["error"], d

# TV on + Kodi: wakes the TV, opens Kodi, waits until Kodi answers
st, d = api("/api/tv/on", {"kodi": True})
assert st == 200 and d["started"], d
assert api("/api/tv/on", {"kodi": True})[1]["started"] is False      # second tap while running: ignored
t0 = time.time()
while time.time() - t0 < 25:
    job = api("/api/tv/job")[1]["job"]
    if job["state"] in ("done", "error"): break
    time.sleep(0.5)
assert job["state"] == "done" and job["message"] == "Kodi is ready.", job
assert TV["power"] == "active" and TV["kodi_running"]
methods = [c[1] for c in TV["calls"]]
assert "setPowerStatus" in methods and "setActiveApp" in methods
s = m.load_settings()
assert s["tv_mac"] == "AA:BB:CC:DD:EE:FF" and s["tv_kodi_uri"].endswith("kodi.Splash"), s

# Open Kodi with the TV already on: one quick call, app list not fetched again (uri remembered)
before = methods.count("getApplicationList")
TV["calls"].clear()
st, d = api("/api/tv/kodi", {})
assert st == 200 and d["ok"], d
assert [c[1] for c in TV["calls"]].count("getApplicationList") == 0 and before == 1
assert any(c[1] == "setActiveApp" for c in TV["calls"])

# Already on when TV on + Kodi is tapped: no power command sent
TV["calls"].clear()
api("/api/tv/on", {"kodi": True})
t0 = time.time()
while time.time() - t0 < 15:
    job = api("/api/tv/job")[1]["job"]
    if job["state"] in ("done", "error"): break
    time.sleep(0.3)
assert job["state"] == "done" and "setPowerStatus" not in [c[1] for c in TV["calls"]], (job, TV["calls"])

# MAC is learned from Open Kodi alone (fresh settings, TV already on)
m.update_settings(lambda st: st.update(tv_mac=""))
api("/api/tv/kodi", {}); time.sleep(1)
assert m.load_settings()["tv_mac"] == "AA:BB:CC:DD:EE:FF"

# A TV that ignores the power API but wakes from the remote's IR code
TV.update(power="standby", kodi_running=False, ignore_power_api=True); TV["calls"].clear()
assert api("/api/tv/on", {"kodi": True})[1]["started"]
t0 = time.time()
while time.time() - t0 < 25:
    job = api("/api/tv/job")[1]["job"]
    if job["state"] in ("done", "error"): break
    time.sleep(0.4)
assert job["state"] == "done" and any(c[1] == "IRCC" for c in TV["calls"]), (job, TV["calls"][:5])
TV["ignore_power_api"] = False

# A TV that's unreachable when off: clear, helpful error
saved = m.load_settings()
api("/api/tv/settings", {"host": "127.0.0.1:1"})
m.TV_WAKE_SECONDS = 4
api("/api/tv/on", {"kodi": True})
t0 = time.time()
while time.time() - t0 < 30:
    job = api("/api/tv/job")[1]["job"]
    if job["state"] in ("done", "error"): break
    time.sleep(0.5)
assert job["state"] == "error" and "Remote start" in job["message"], job
api("/api/tv/settings", {"host": host}); m.TV_WAKE_SECONDS = 10

# Manual MAC: pre-filled value coming back unchanged is not "manual"; a typed one is, and bad ones are refused
cur = m.load_settings()["tv_mac"]
api("/api/tv/settings", {"host": host, "mac": cur}); assert not m.load_settings()["tv_mac_manual"]
assert api("/api/tv/settings", {"host": host, "mac": "nope"})[0] == 400
api("/api/tv/settings", {"host": host, "mac": "11-22-33-44-55-66"})
s2 = m.load_settings(); assert s2["tv_mac"] == "11:22:33:44:55:66" and s2["tv_mac_manual"], s2

# Changing to a different TV forgets the learned MAC / app address
api("/api/tv/settings", {"host": "192.168.1.77"})
s = m.load_settings()
assert s["tv_mac"] == "" and s["tv_kodi_uri"] == "" and s["tv_psk"], s

# The page contains the buttons
with urllib.request.urlopen(base + "/", timeout=5) as r:
    page = r.read().decode()
assert 'id="tvon"' in page and 'id="tvkodi"' in page and "TV on + Kodi" in page and "Open Kodi" in page

print("TV CONTROL TESTS PASSED")
