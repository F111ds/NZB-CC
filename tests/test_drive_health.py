"""Phone app Drive Health: report building, temperature history, LibreHardwareMonitor temps, alerts, endpoints."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))
import json, tempfile, threading, time, urllib.request, urllib.error, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import mobile_server as m


def serve(h):
    s = ThreadingHTTPServer(("127.0.0.1", 0), h); threading.Thread(target=s.serve_forever, daemon=True).start(); return s


class FakeSab(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        mode = qs.get("mode", [""])[0]
        now = int(time.time())
        if mode == "get_config":
            d = {"config": {"misc": {"complete_dir": "/downloads/complete"}}}
        elif mode == "history":   # 7 GB finished over the week -> 1 GB/day
            d = {"history": {"slots": [{"status": "Completed", "completed": now - 3600, "bytes": 7 * 1024 ** 3}]}}
        elif mode == "server_stats":
            d = {"day": 1, "week": 2, "month": 3, "total": 4}
        else:
            d = {"status": True}
        b = json.dumps(d).encode(); self.send_response(200); self.end_headers(); self.wfile.write(b)


tmp = Path(tempfile.mkdtemp())
sab = serve(FakeSab)
(tmp / "nzb_manager_config.json").write_text(json.dumps({
    "sab_url": f"127.0.0.1:{sab.server_address[1]}", "sab_api_key": "plainsabkey123",
    "media_folders": ["/media/movies"], "indexers": []}))
m.CONFIG_FILE = tmp / "nzb_manager_config.json"
m.SETTINGS_FILE = tmp / "mobile_settings.json"
m.DRIVE_HISTORY_FILE = tmp / "drive_history.json"
m._cfg_state.update(mtime=None, data=None)
m._token_bytes = b"testtoken"

# ---------- LibreHardwareMonitor temperatures ----------
LHM = {"Text": "Sensor", "Children": [{"Text": "PC", "Children": [
    {"Text": "WDC WD20EFRX", "ImageURL": "images_icon/hdd.png", "Children": [
        {"Text": "Temperatures", "Children": [{"Text": "Temperature", "Value": "41,0 °C"}]}]},
    {"Text": "Intel Core", "ImageURL": "images_icon/cpu.png", "Children": [
        {"Text": "Temperatures", "Children": [{"Text": "Core", "Value": "70.0 °C"}]}]}]}]}
class FakeLhm(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        b = json.dumps(LHM).encode(); self.send_response(200); self.end_headers(); self.wfile.write(b)
lhm = serve(FakeLhm)
temps = m.lhm_drive_temps(lhm.server_address[1])
assert temps == {"wdc wd20efrx": 41.0}, temps          # drive only, decimal comma handled
assert m.lhm_drive_temps(1) == {}                      # nothing listening -> no temps, no error

# ---------- report ----------
m._drive_inventory = lambda: {
    "disks": [
        {"id": "0", "name": "Samsung SSD 980 1TB", "media": "4", "bus": "17", "size": 1e12, "health": "0",
         "temp": 67, "wear": 6, "hours": 9800, "model": "Samsung SSD 980", "serial": "S64DNX0R123456Z",
         "firmware": "3B4QFXO7", "temp_max": 71, "read_errors": 0, "write_errors": 0, "starts": 1200,
         "read_lat": 12, "smart": True, "sector": 512},
        {"id": "1", "name": "WDC WD20EFRX", "media": "3", "bus": "11", "size": 2e12, "health": "1",
         "temp": None, "hours": 21412, "spindle": 5400},
        {"id": "2", "name": "USB HDD", "media": "3", "bus": "7", "size": 4e12, "health": "0", "spindle": 4294967295},
    ],
    "parts": [{"disk": "0", "letter": "/"}],
    "volumes": [{"letter": "/", "label": "System", "fs": "NTFS", "health": "Healthy"}],
    "dinfo": [{"id": "0", "style": "GPT", "boot": True, "system": True, "parts": 4}],
    # Letters not on a listed physical disk: a NAS share as G:, a Storage Spaces volume as S:, and a
    # mapped drive (H:) only visible in the registry; "/" is already covered by disk 0.
    "logical": [{"letter": "G", "type": 4, "label": "", "fs": "NTFS", "remote": "\\\\nas\\media"},
                {"letter": "S", "type": 3, "label": "Pool", "fs": "ReFS", "remote": ""},
                {"letter": "/", "type": 3}],
    "mapped": [{"letter": "h", "remote": "\\\\nas\\backup"}, {"letter": "G", "remote": "\\\\nas\\media"}],
}
m.lhm_drive_temps = lambda port=m.LHM_PORT: {"wdc wd20efrx": 56.0}
m._drive_state.update(at=0.0, inv=None, sab_dir=("", 0.0))
r = m.drive_report(force=True)
by = {d["name"]: d for d in r["drives"]}
ssd, hdd = by["Samsung SSD 980 1TB"], by["WDC WD20EFRX"]
assert ssd["media"] == "SSD" and ssd["bus"] == "NVMe" and ssd["temp_limit"] == 70 and ssd["wear"] == 6
assert ssd["status"] == "warn" and "Running warm" in ssd["problems"][0], ssd       # 67 °C is within 4 of 70
assert ssd["total"] > 0 and ssd["letters"] == ["/"] and "System" in ssd["uses"] and "Finished downloads" in ssd["uses"]
assert ssd["days_left"] is not None and ssd["daily_bytes"] == 1024 ** 3, ssd     # 7 GB / 7 days
assert hdd["temp"] == 56 and hdd["rpm"] == 5400 and hdd["health"] == "Warning"   # temp from LHM, fuzzy name match
assert hdd["status"] == "bad" and any("Too hot" in p for p in hdd["problems"]) and hdd["wear"] is None
assert r["drives"][0] is hdd                                                     # worst first
assert r["totals"]["warn"] == 5 and r["temps_available"]   # 2 physical + 3 unreadable letters
assert len(hdd["history"]) == 1 and hdd["history"][0][1] == 56.0
x = ssd["details"]
assert x["model"] == "Samsung SSD 980" and x["serial"] == "…23456Z" and x["firmware"] == "3B4QFXO7", x   # only the end of the serial
assert x["style"] == "GPT" and x["boot"] and x["system"] and x["partitions"] == 4 and x["temp_max"] == 71
assert x["read_errors"] == 0 and x["starts"] == 1200 and x["read_lat"] == 12 and x["smart"] and x["write_fixed"] is None
assert ssd["volumes"][0]["label"] == "System" and ssd["volumes"][0]["fs"] == "NTFS" and ssd["volumes"][0]["total"] > 0
assert hdd["details"]["smart"] is False and hdd["volumes"] == []
assert by["USB HDD"]["rpm"] is None
other = {d["letters"][0]: d for d in r["drives"] if d["key"].startswith("vol:")}
assert sorted(other) == ["G", "H", "S"], sorted(other)                            # every letter shows, none twice
g = other["G"]
assert g["media"] == "Network" and g["name"] == "\\\\nas\\media" and g["details"]["kind"] == "network", g
assert g["status"] == "warn" and "network share offline" in g["problems"][0]     # can't be read from this test machine
assert other["S"]["label"] == "Pool" and other["S"]["media"] == "Local" and other["H"]["details"]["remote"] == "\\\\nas\\backup"                                               # 4294967295 = Windows doesn't know
assert m.drive_activity() == {}                                                   # not Windows: no live counters
r0 = m._io_rates({"t": 0, "read": 0, "write": 0, "busy": 0, "f": 1e7}, {"t": 2e7, "read": 4e6, "write": 2e6, "busy": 5e6, "f": 1e7})
assert r0 == {"read": 2e6, "write": 1e6, "busy": 25.0}, r0                        # 2 s apart: 2 MB/s, 1 MB/s, 25 % busy
assert m._io_rates({"t": 5, "read": 0, "write": 0, "busy": 0}, {"t": 5, "read": 1, "write": 1, "busy": 1}) == {}

# ---------- disks whose DeviceId isn't their disk number (the "G: SSD missing" bug) ----------
ds = [{"id": "0", "serial": "S64D NX0R 12_34", "name": "Samsung", "size": 1e12},
      {"id": "1", "serial": "WD-ABC", "name": "WD", "size": 2e12},
      {"id": "3", "serial": "", "name": "Crucial", "size": 5e11}]
di = [{"id": "0", "serial": "S64DNX0R1234"}, {"id": "2", "serial": "WD-ABC"},
      {"id": "1", "serial": "", "name": "Crucial", "size": 5e11 + 1e6}]
nums = m._match_disk_numbers(ds, di)
assert [nums[id(d)] for d in ds] == ["0", "2", "1"], nums      # by serial (spaces ignored), by serial, by name+size
assert m._match_disk_numbers(ds[:1], [])[id(ds[0])] == "0"      # nothing to match: DeviceId
saved_inv = m._drive_inventory
m._drive_inventory = lambda: {
    "disks": [{"id": "5", "name": "Crucial MX500", "serial": "2203E5F1", "media": "4", "bus": "11", "size": 5e11,
               "health": "0", "temp": 35}],
    "parts": [{"disk": "2", "letter": "/"}],                   # Windows calls it disk 2, not 5
    "volumes": [], "logical": [{"letter": "/", "type": 3}],
    "dinfo": [{"id": "2", "serial": "2203E5F1", "style": "GPT", "parts": 1}]}
m._drive_state.update(at=0.0, inv=None)
rg = m.drive_report(force=True)
assert len(rg["drives"]) == 1 and rg["drives"][0]["letters"] == ["/"] and rg["drives"][0]["media"] == "SSD", rg["drives"]
assert rg["drives"][0]["details"]["style"] == "GPT" and rg["drives"][0]["temp"] == 35      # a real disk, with health
m._drive_inventory = saved_inv
m._drive_state.update(at=0.0, inv=None)
r = m.drive_report(force=True)
by = {d["name"]: d for d in r["drives"]}
ssd, hdd = by["Samsung SSD 980 1TB"], by["WDC WD20EFRX"]

# ---------- temperature history: one point per 10 min, 24 h kept, saved to disk ----------
key = hdd["key"]
now = time.time()
m.record_drive_temps({key: 50}, now + 60)                 # too soon: ignored
assert len(m.drive_temp_history(key)) == 1
m.record_drive_temps({key: 50}, now + 700)
assert len(m.drive_temp_history(key)) == 2
m.record_drive_temps({key: 51}, now + 90000)              # a day later: the old points drop off
assert [p[1] for p in m.drive_temp_history(key)] == [51.0]
assert json.loads(m.DRIVE_HISTORY_FILE.read_text())[key][0][1] == 51.0

# ---------- alerts ----------
settings = m._default_settings()
settings["drive_low_gb"] = 0      # the test machine's own free space must not matter
sent = {}
alerts = m.drive_alerts(r, settings, sent, now)
titles = [a[0] for a in alerts]
assert any(t.startswith("Drive problem") for t in titles) and any(t.startswith("Drive too hot") for t in titles), titles
assert not any("Samsung" in t for t in titles), titles            # warm is not "too hot"
assert m.drive_alerts(r, settings, sent, now + 3600) == []         # not repeated within a day
assert len(m.drive_alerts(r, settings, sent, now + 90000)) == 2    # repeated after a day
low = dict(settings, drive_low_gb=10 ** 9)
assert any(a[0].startswith("Drive almost full") for a in m.drive_alerts(r, low, {}, now))
settings["notify_drive_hot"] = False
sent.clear()
assert [a[0].split(":")[0] for a in m.drive_alerts(r, settings, sent, now)] == ["Drive problem"]
hdd["temp"] = 40
m.drive_alerts(r, settings, sent, now)
assert not any(k.startswith("hot:") for k in sent)                 # cooled down -> forgotten, alerts again next time

# ---------- endpoints ----------
app = serve(m.MobileHandler)
base = f"http://127.0.0.1:{app.server_address[1]}"
def api(path, body=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Mobile-Token": "testtoken", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp: return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

st, d = api("/api/drives")
assert st == 200 and len(d["drives"]) == 6 and d["alerts"] == {"ready": False, "health": True, "hot": True, "low": True, "low_gb": 75}, d
assert d["temp_alert"] == 55 and d["nvme_temp_alert"] == 70
st, d = api("/api/drives/alerts", {"notify_drive_hot": False})
assert st == 200 and d["alerts"]["hot"] is False and d["alerts"]["health"] is True
assert json.loads(m.SETTINGS_FILE.read_text())["notify_drive_hot"] is False
saved = json.loads(m.SETTINGS_FILE.read_text()); saved["drive_low_gb"] = 100   # saved by an older version
m.SETTINGS_FILE.write_text(json.dumps(saved))
assert m.load_settings()["drive_low_gb"] == 75                                    # old default follows the new one
assert api("/api/drives/io") == (200, {"io": {}})
st, d = api("/api/drives/check", {})
assert st == 200 and d["checked"]
req = urllib.request.Request(base + "/api/drives")
try:
    urllib.request.urlopen(req, timeout=5); raise AssertionError("no access code accepted")
except urllib.error.HTTPError as e:
    assert e.code == 401

# A failed check keeps the last good answer.
def boom():
    raise RuntimeError("PowerShell missing")
m._drive_inventory = boom
r2 = m.drive_report(force=True)
assert len(r2["drives"]) == 6 and "Could not read drive health" in r2["problem"]

# ---------- page wiring ----------
page = m.PAGE
for needle in ('id="tabdrives"', 'openDrive', '/api/drives/io', 'id="drives"', 'id="homedrives"', "refreshDrives", "/api/drives/alerts", "data-dalert", "TEMPERATURES · 24 H"):
    assert needle in page, needle

app.shutdown(); sab.shutdown(); lhm.shutdown()
print("drive health OK")
