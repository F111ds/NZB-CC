"""Fans that stop and the other temperatures worth watching (hal_server.extra_temps, FanWatch, alerts)."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))

import tempfile
from pathlib import Path

import hal_server as hs

tmp = Path(tempfile.mkdtemp())
hs.SETTINGS_FILE = tmp / "hal_settings.json"

# ---------------- which sensors count ----------------
temps = [
    {"hw": "/gpu-nvidia/0", "label": "NVIDIA GeForce RTX 4070", "name": "GPU Core", "value": 61},
    {"hw": "/gpu-nvidia/0", "label": "NVIDIA GeForce RTX 4070", "name": "GPU Hot Spot", "value": 104},
    {"hw": "/gpu-nvidia/0", "label": "NVIDIA GeForce RTX 4070", "name": "GPU Memory Junction", "value": 88},
    {"hw": "/lpc/nct6799d/0", "label": "Nuvoton NCT6799D", "name": "VRM MOS", "value": 71},
    {"hw": "/lpc/nct6799d/0", "label": "Nuvoton NCT6799D", "name": "CPU", "value": 60},
    {"hw": "/lpc/nct6799d/0", "label": "Nuvoton NCT6799D", "name": "Chipset", "value": 66},
    {"hw": "/nvme/0", "label": "Samsung SSD 990 PRO 1TB", "name": "Temperature", "value": 58},
    {"hw": "/nvme/0", "label": "Samsung SSD 990 PRO 1TB", "name": "Temperature 2", "value": 83},
    {"hw": "/nvme/1", "label": "WD Black SN850X", "name": "Composite Temperature", "value": 49},
    {"hw": "/amdcpu/0", "label": "AMD Ryzen 9 7950X", "name": "Core (Tctl/Tdie)", "value": 75},
]
x = {t["key"]: t for t in hs.extra_temps(temps)}
assert x["gpu_hot"]["value"] == 104 and x["gpu_mem"]["value"] == 88, x
assert x["vrm"]["value"] == 71 and x["chipset"]["value"] == 66
assert x["nvme:Samsung SSD 990 PRO 1TB"]["value"] == 83          # the hottest sensor of that drive (its controller)
assert x["nvme:WD Black SN850X"]["value"] == 49
assert not any(k.startswith("cpu") for k in x)                    # the CPU has its own check
assert hs.extra_temps([]) == [] and hs.extra_temps([{"hw": "/gpu-nvidia/0", "name": "GPU Hot Spot", "value": None}]) == []
print("extra temperatures ok")

# ---------------- fans ----------------
fw = hs.FanWatch()
t0 = 1000.0
def fans(cpu, case, gpu=0, pump=None):
    out = [{"hw": "/lpc/nct6799d/0", "name": "CPU Fan", "value": cpu}, {"hw": "/lpc/nct6799d/0", "name": "Fan #2", "value": case},
           {"hw": "/gpu-nvidia/0", "name": "GPU Fan 1", "value": gpu}, {"hw": "/lpc/nct6799d/0", "name": "Fan #5", "value": 0}]
    return out
# spinning for 31 minutes: now they are fans that normally spin
for m in range(0, 32):
    assert fw.update(fans(980, 720), t0 + m * 60) == []
# the case fan stops: an alert; the empty header (Fan #5) and the graphics card fan don't count
st = fw.update(fans(980, 0), t0 + 33 * 60)
assert [f["name"] for f in st] == ["Fan #2"] and 700 <= st[0]["typ"] <= 740, st
assert fw.stopped_names() == {"Fan #2"}
assert fw.update(fans(990, 0, gpu=0), t0 + 34 * 60)[0]["name"] == "Fan #2"
# it comes back: no alert
assert fw.update(fans(990, 715), t0 + 35 * 60) == [] and fw.stopped_names() == set()
# a fan that stops only briefly before 30 minutes of spinning never counts
fw2 = hs.FanWatch()
assert fw2.update(fans(980, 700), t0) == [] and fw2.update(fans(980, 0), t0 + 600) == []
# a fan whose curve lets it stop: stopped and restarted by itself twice, then left alone
fw3 = hs.FanWatch()
t = t0
for cycle in range(3):
    for m in range(31):
        fw3.update(fans(980, 700), t); t += 60
    got = fw3.update(fans(980, 0), t); t += 60
    assert bool(got) == (cycle < 2), (cycle, got)
print("fans ok")

# ---------------- alerts ----------------
h = hs.Hal(settings=dict(hs.DEFAULT_SETTINGS), monitor=hs.DemoMonitor(), start_threads=False)
snap = h.monitor.snapshot()
snap["sensors"] = {"temps": temps, "fans": fans(980, 720), "power": []}
iss = {i["key"]: i for i in h.compute_issues(snap, [])}
assert iss["vtemp:gpu_hot"]["level"] == "warning" and "hot spot" in iss["vtemp:gpu_hot"]["title"], iss.get("vtemp:gpu_hot")
assert iss["vtemp:nvme:Samsung SSD 990 PRO 1TB"]["level"] == "warning" and "990 PRO" in iss["vtemp:nvme:Samsung SSD 990 PRO 1TB"]["title"]
assert "vtemp:gpu_mem" not in iss and "vtemp:vrm" not in iss and "vtemp:chipset" not in iss
h.settings["gpu_hot_alert"] = 90
assert {i["key"]: i for i in h.compute_issues(snap, [])}["vtemp:gpu_hot"]["level"] == "critical"   # 10 °C over: critical
# a stopped fan
h.fan_watch.f.clear()
real_time = hs.time.time
try:
    for m in range(32):
        hs.time.time = lambda m=m: t0 + m * 60
        snap["sensors"]["fans"] = fans(980, 720); h.compute_issues(snap, [])
    hs.time.time = lambda: t0 + 33 * 60
    snap["sensors"]["fans"] = fans(980, 0)
    iss = {i["key"]: i for i in h.compute_issues(snap, [])}
    fk = [k for k in iss if k.startswith("fan:")]
    assert len(fk) == 1 and iss[fk[0]]["level"] == "critical" and iss[fk[0]]["title"] == "The Fan #2 has stopped", iss
    assert hs.recovery_title(fk[0], iss[fk[0]]) == "The Fan #2 is spinning again"
    h.settings["fan_alert"] = False
    assert not [k for k in (i["key"] for i in h.compute_issues(snap, [])) if k.startswith("fan:")]
finally:
    hs.time.time = real_time
assert hs.recovery_title("vtemp:gpu_hot", {"title": "The graphics card hot spot is running hot"}) == "The graphics card hot spot has cooled down"
# settings: the new limits load, are kept in range, and the switch is a switch
s = hs.validate_settings({"gpu_hot_alert": 500, "nvme_temp_alert": 75, "fan_alert": False}, dict(hs.DEFAULT_SETTINGS))
assert s["gpu_hot_alert"] == 120 and s["nvme_temp_alert"] == 75 and s["fan_alert"] is False, s
print("alerts ok")

# ---------------- the fans card and live readings ----------------
c = hs.tv_fan_card({"fans": [{"name": "CPU fan", "value": 980, "stopped": False}, {"name": "Fan #2", "value": 0, "stopped": True}],
                    "gpus": [{"fan": 30}]})
assert c["key"] == "FANS" and c["badge"] == "1 STOPPED", c
assert c["rows"][0] == ["CPU fan", "980 rpm", "ok"] and c["rows"][1] == ["Fan #2", "STOPPED", "critical"] and c["rows"][2] == ["Graphics card", "30%", "ok"]
assert hs.tv_fan_card({"fans": [{"name": "CPU fan", "value": 980}], "gpus": []})["badge"] == "1 SPINNING"
assert hs.tv_fan_card({"fans": [], "gpus": []}) is None
class _M:
    def hist(self, key):
        return list(range(121)) if key == "cpu" else [1.0, None, 3.0]

    def snapshot(self):
        return {"gpus": [{"key": "g0"}]}
hl = hs.Hal(settings=dict(hs.DEFAULT_SETTINGS), monitor=_M(), start_threads=False)
lv = hl.live()
assert len(lv["cpu"]) == 60 and lv["cpu"][-1] == 119.5 and lv["every_s"] == 2, lv["cpu"][-3:]
assert lv["net_down"] == [2.0]          # gaps dropped, then averaged in pairs
assert lv["disk"] == [4.0] and lv["gpu"] == [2.0] and lv["ping"] == [2.0] and lv["mem"] == [2.0], lv   # disk = read + write
grid = hs.tv_grids(h, h.status(), hs.tv_screen(h.status()), h.history("1h"))
row2 = [x["key"] for x in grid["The PC"][1]]
assert row2 == ["DRIVES", "FANS", "BUSIEST NOW"], row2
assert grid["The PC"][1][0]["drive_grid"] is False
assert any(x["key"] == "MOST BLOCKED" and x["badge"] == "LAST 24 H" for row in grid["Raspberry Pi"] for x in row)
print("fans card and live readings ok")


# ---------------- drive temperatures from LibreHardwareMonitor (the drive's own sensor) ----------------
import system_monitor as sm
lines = ["Temperature|/ssd/1|Temperature|32", "Temperature|/ssd/1|Airflow Temperature|68",
         "Temperature|/nvme/0|Temperature|49", "Temperature|/nvme/0|Temperature 2|61", "Temperature|/amdcpu/0|Core (Tctl/Tdie)|60"]
sens = sm.parse_sensor_lines(lines, ["/ssd/1|Samsung SSD 860 EVO 500GB", "/nvme/0|Samsung SSD 970 EVO Plus 500GB", "/amdcpu/0|AMD Ryzen"])
assert sens["temps"][0]["label"] == "Samsung SSD 860 EVO 500GB"
dt = sm.disk_temps(sens["temps"])
assert sorted((d["label"], d["value"]) for d in dt) == [("Samsung SSD 860 EVO 500GB", 32), ("Samsung SSD 970 EVO Plus 500GB", 49)], dt
assert sm.match_disk_temp("Samsung SSD 860 EVO 500GB", dt) == 32
assert sm.match_disk_temp("Samsung SSD 970 EVO Plus 500GB", dt) == 49
assert sm.match_disk_temp("WDC WD40EFRX", dt) is None
two = dt + [{"label": "Samsung SSD 860 EVO 500GB", "value": 35}]
assert sm.match_disk_temp("Samsung SSD 860 EVO 500GB", two) is None        # two of the same model: can't tell them apart
assert sm.parse_sensor_lines(["Temperature|/ssd/1|Temperature|32"])["temps"][0]["label"] == ""   # older feed without names
# in HAL's drive list: the sensor wins over Windows' figure
h2 = hs.Hal(settings=dict(hs.DEFAULT_SETTINGS), monitor=hs.DemoMonitor(), start_threads=False)
snap2 = h2.monitor.snapshot()
snap2["inventory"]["disks"][0]["temp"] = 68
nm = snap2["inventory"]["disks"][0]["name"]
snap2["sensors"] = {"temps": [{"hw": "/ssd/0", "label": nm, "name": "Temperature", "value": 32}], "fans": [], "power": []}
d0 = h2.drives(snap2)[0]["disk"]
assert d0["temp"] == 32 and d0["temp_src"] == "LibreHardwareMonitor", d0
snap2["sensors"] = {"temps": [], "fans": [], "power": []}
d0 = h2.drives(snap2)[0]["disk"]
assert d0["temp"] == 68 and d0["temp_src"] == "Windows", d0
print("drive temperatures from the drive's own sensor ok")


# ---------------- HWiNFO (shared memory layout from its SDK, and its Gadget registry values) ----------------
import struct
def _shm(sensors, readings):
    s_off, s_size, r_size = 44, 264 + 128, 316 + 64          # newer HWiNFO elements are bigger: we step by the given size
    r_off = s_off + s_size * len(sensors)
    buf = bytearray(r_off + r_size * len(readings))
    struct.pack_into("<IIIqIIIIII", buf, 0, 0x53695748, 2, 0, 0, s_off, s_size, len(sensors), r_off, r_size, len(readings))
    for i, name in enumerate(sensors):
        o = s_off + i * s_size
        struct.pack_into("<II", buf, o, i, 0)
        buf[o + 8:o + 8 + len(name)] = name.encode()
        buf[o + 136:o + 136 + len(name)] = name.encode()
    for i, (kind, si, label, value) in enumerate(readings):
        o = r_off + i * r_size
        struct.pack_into("<III", buf, o, kind, si, i)
        buf[o + 12:o + 12 + len(label)] = label.encode()
        buf[o + 140:o + 140 + len(label)] = label.encode()
        struct.pack_into("<d", buf, o + 284, value)
    return bytes(buf)
buf = _shm(["CPU [#0]: Intel Core i7-9700K: Enhanced", "GPU [#0]: NVIDIA GeForce RTX 4080", "ASUS ROG STRIX Z390-E (Nuvoton NCT6798D)",
            "S.M.A.R.T.: Samsung SSD 860 EVO 500GB (S3Z9NB0K123456)"],
           [(1, 0, "CPU Package", 53.0), (1, 1, "GPU Hot Spot Temperature", 61.5), (1, 1, "GPU Memory Junction Temperature", 58.0),
            (1, 2, "VRM", 47.0), (3, 2, "CPU", 1092.0), (3, 2, "Chassis1", 588.0), (1, 3, "Drive Temperature", 32.0), (2, 0, "Vcore", 1.2)])
hw = sm.parse_hwinfo_shm(buf)
assert [t["name"] for t in hw["temps"]] == ["CPU Package", "GPU Hot Spot Temperature", "GPU Memory Junction Temperature", "VRM", "Drive Temperature"], hw
assert hw["temps"][0]["hw"].startswith("/cpu/") and hw["temps"][1]["hw"].startswith("/gpu/") and hw["temps"][3]["hw"].startswith("/lpc/")
assert hw["temps"][4]["hw"].startswith("/ssd/hwinfo/") and hw["temps"][4]["label"] == "Samsung SSD 860 EVO 500GB"
assert [f["value"] for f in hw["fans"]] == [1092.0, 588.0]
assert sm.parse_hwinfo_shm(b"DEAD" + bytes(60)) is None and sm.parse_hwinfo_shm(b"") is None
assert sm.pick_cpu_temp(hw["temps"]) == 53.0
# HWiNFO fills the gaps in LibreHardwareMonitor's readings
lhm = {"temps": [{"hw": "/intelcpu/0", "label": "Intel Core i7-9700K", "name": "CPU Package", "value": 52.0}], "fans": [], "power": []}
mg = sm.merge_hwinfo(lhm, hw)
names = [t["name"] for t in mg["temps"]]
assert names == ["CPU Package", "GPU Hot Spot Temperature", "GPU Memory Junction Temperature", "VRM", "Drive Temperature"], names
assert mg["temps"][0]["value"] == 52.0 and len(mg["fans"]) == 2       # LibreHardwareMonitor's CPU kept, HWiNFO's fans used
assert sm.merge_hwinfo(lhm, None) is lhm
assert sm.match_disk_temp("Samsung SSD 860 EVO 500GB", sm.disk_temps(mg["temps"])) == 32.0
# both tools see the same drive: LibreHardwareMonitor's reading is used, and it isn't counted as two drives
both = mg["temps"] + [{"hw": "/ssd/1", "label": "Samsung SSD 860 EVO 500GB", "name": "Temperature", "value": 33.0}]
assert sm.match_disk_temp("Samsung SSD 860 EVO 500GB", sm.disk_temps(both)) == 33.0
ex = {x["key"]: x["value"] for x in hs.extra_temps(mg["temps"])}
assert ex["gpu_hot"] == 61.5 and ex["gpu_mem"] == 58.0 and ex["vrm"] == 47.0, ex
# Gadget values
gd = sm.parse_hwinfo_gadget({"Sensor0": "S.M.A.R.T.: Samsung SSD 860 EVO 500GB (S3Z9)", "Label0": "Drive Temperature", "Value0": "32 °C", "ValueRaw0": "32",
                             "Sensor1": "ASUS ROG STRIX Z390-E (Nuvoton NCT6798D)", "Label1": "CPU", "Value1": "1,092 RPM", "ValueRaw1": "1092"})
assert gd["temps"][0]["value"] == 32 and gd["temps"][0]["label"] == "Samsung SSD 860 EVO 500GB" and gd["fans"][0]["value"] == 1092, gd
assert sm.parse_hwinfo_gadget({}) is None
print("HWiNFO ok")


# ---------------- fan control through LibreHardwareMonitor (a fake web server that records what HAL asks) ----------------
import json as _json, threading as _th, urllib.parse as _up
from http.server import BaseHTTPRequestHandler as _BH, ThreadingHTTPServer as _TS
_lhm = {"pct": {"0": 45.0, "1": 35.0, "2": 35.0, "3": 60.0}, "sets": [], "refuse": False}
def _tree():
    def fan(i, name, rpm): return {"Text": name, "Value": f"{rpm} RPM", "SensorId": f"/lpc/nct6798d/0/fan/{i}", "Type": "Fan"}
    def ctl(i, name): return {"Text": name, "Value": f"{_lhm['pct'][str(i)]:.1f} %", "SensorId": f"/lpc/nct6798d/0/control/{i}", "Type": "Control"}
    board = {"Text": "Nuvoton NCT6798D", "Children": [
        {"Text": "Fans", "Children": [fan(0, "CPU Fan", 980), fan(1, "Chassis Fan #1", 720), fan(2, "Chassis Fan #2", 715), fan(3, "AIO Pump", 2400), fan(4, "Fan #5", 0)]},
        {"Text": "Controls", "Children": [ctl(0, "CPU Fan"), ctl(1, "Chassis Fan #1"), ctl(2, "Chassis Fan #2"), ctl(3, "AIO Pump"),
                                          {"Text": "Fan #5", "Value": "0.0 %", "SensorId": "/lpc/nct6798d/0/control/4", "Type": "Control"}]}]}
    gpu = {"Text": "NVIDIA GeForce RTX 4080", "Children": [{"Text": "Controls", "Children": [{"Text": "GPU Fan", "Value": "30.0 %", "SensorId": "/gpu-nvidia/0/control/0"}]},
                                                          {"Text": "Fans", "Children": [{"Text": "GPU Fan", "Value": "1100 RPM", "SensorId": "/gpu-nvidia/0/fan/0"}]}]}
    return {"Text": "Sensor", "Children": [{"Text": "HAL9000", "Children": [board, gpu]}]}
class _LH(_BH):
    def log_message(self, *a): pass
    def _go(self):
        u = _up.urlparse(self.path)
        if u.path == "/data.json":
            b = _json.dumps(_tree()).encode(); self.send_response(200); self.end_headers(); self.wfile.write(b); return
        if u.path == "/Sensor" and not _lhm["refuse"]:
            q = dict(_up.parse_qsl(u.query)); _lhm["sets"].append((q.get("id"), q.get("value")))
            idx = q["id"].rsplit("/", 1)[-1]
            if not _lhm.get("stuck") and _lhm.get("answer", b'{"result":"ok"}') == b'{"result":"ok"}':
                _lhm["pct"][idx] = 50.0 if q.get("value") == "null" else float(q["value"])
            self.send_response(200); self.end_headers(); self.wfile.write(_lhm.get("answer", b'{"result":"ok"}')); return
        self.send_response(404); self.end_headers()
    do_GET = do_POST = _go
_srv = _TS(("127.0.0.1", 0), _LH); _th.Thread(target=_srv.serve_forever, daemon=True).start()
fc = hs.FanControl(port=_srv.server_address[1])
fc.refresh()
v = fc.view()
assert v["available"] and [f["name"] for f in v["fans"]] == ["CPU Fan", "Chassis Fan #1", "Chassis Fan #2", "AIO Pump"], v   # no GPU, no empty header
tg = hs.fan_targets(fc.fans, "quiet")
assert tg == {"/lpc/nct6798d/0/control/0": 40, "/lpc/nct6798d/0/control/1": 35, "/lpc/nct6798d/0/control/2": 35}, tg   # CPU floor 40, pump untouched
assert set(hs.fan_targets(fc.fans, "max").values()) == {100}
assert hs.fan_targets([{"name": "Fan #1", "control_id": "a"}, {"name": "Fan #2", "control_id": "b"}], "quiet") == {"a": 40, "b": 40}   # no fan called CPU: 40% for all
r = fc.set_mode("cool")
assert r["ok"] and fc.mode == "cool" and ("/lpc/nct6798d/0/control/0", "70") in _lhm["sets"] and not any(s[0].endswith("control/3") for s in _lhm["sets"]), (r, _lhm["sets"])
# LibreHardwareMonitor restarted (its controls back on the BIOS curve): HAL puts the mode back
_lhm["pct"]["1"] = 50.0; _lhm["sets"].clear()
assert fc.watch("") == "" and ("/lpc/nct6798d/0/control/1", "70") in _lhm["sets"], _lhm["sets"]
# something runs hot: back to Auto by itself
_lhm["sets"].clear()
assert fc.watch("the CPU reached 88°C") == "the CPU reached 88°C" and fc.mode == "auto" and all(s[1] == "null" for s in _lhm["sets"]) and len(_lhm["sets"]) == 4
try:
    fc.set_mode("turbo"); raise AssertionError("unknown mode accepted")
except ValueError:
    pass
# refused: stays on Auto and says so
_lhm["refuse"] = True
r = fc.set_mode("max")
assert not r["ok"] and fc.mode == "auto" and "BIOS" in r["say"], r
_lhm["refuse"] = False
# LibreHardwareMonitor answers 200 but refuses inside: not taken as done
_lhm["answer"] = b'{"result":"fail","message":"Specified sensor can not be set"}'
r = fc.set_mode("cool")
assert not r["ok"] and fc.mode == "auto" and "can not be set" in r["say"], r
# an older one answers with its web page
_lhm["answer"] = b"<html>LibreHardwareMonitor</html>"
r = fc.set_mode("cool")
assert not r["ok"] and "update LibreHardwareMonitor" in r["say"], r
# says ok but the controller doesn't move
_lhm["answer"] = b'{"result":"ok"}'; _lhm["stuck"] = True
r = fc.set_mode("cool")
assert not r["ok"] and "didn't take" in r["say"] and fc.mode == "auto", r
_lhm["stuck"] = False
# took the power but the fans didn't speed up: a note after 8 s
r = fc.set_mode("max"); assert r["ok"], r
fc.check["at"] -= 9
fc.watch("")
assert "speeds didn't" in fc.view()["note"], fc.view()
fc.set_mode("auto")
# nothing answering
assert not hs.FanControl(port=1).set_mode("cool")["ok"]
# why it goes back to Auto
s0 = dict(hs.DEFAULT_SETTINGS)
assert hs.fan_hot_reason({"cpu": {"temp": 60}, "gpus": [{"temp": 50}], "sensors": {"temps": []}}, s0) == ""
assert "CPU" in hs.fan_hot_reason({"cpu": {"temp": 86}}, s0) and "graphics" in hs.fan_hot_reason({"gpus": [{"temp": 81}]}, s0)
assert "VRM" in hs.fan_hot_reason({"sensors": {"temps": [{"hw": "/lpc/x", "name": "VRM MOS", "value": 101}]}}, s0)
# HAL: set from the phone, an event logged, the TV card shows the modes
h3 = hs.Hal(settings=dict(hs.DEFAULT_SETTINGS), monitor=hs.DemoMonitor(), start_threads=False)
h3.fan_ctl = fc
out = h3.set_fans("normal", "phone")
assert out["ok"] and out["fans"]["mode"] == "normal" and h3.events[0]["title"] == "Fans: Normal", (out, h3.events[0])
st3 = h3.status()
assert st3["fanctl"]["mode"] == "normal"
card = hs.tv_fan_card(st3["vitals"], st3["fanctl"])
assert card["modes"] == ["AUTO", "QUIET", "NORMAL", "COOL", "MAX"] and card["mode"] == "NORMAL" and card["lit"] and card["badge"] == "NORMAL", card
assert hs.tv_fan_card(st3["vitals"], {"available": False})["badge"].endswith("SPINNING")
fc.set_mode("auto")
_srv.shutdown()
print("fan control ok")

print("\nALL FAN AND TEMPERATURE TESTS PASSED")
