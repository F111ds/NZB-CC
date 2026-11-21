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
hl = hs.Hal(settings=dict(hs.DEFAULT_SETTINGS), monitor=_M(), start_threads=False)
lv = hl.live()
assert len(lv["cpu"]) == 60 and lv["cpu"][-1] == 119.5 and lv["every_s"] == 2, lv["cpu"][-3:]
assert lv["net_down"] == [2.0]          # gaps dropped, then averaged in pairs
grid = hs.tv_grids(h, h.status(), hs.tv_screen(h.status()), h.history("1h"))
row2 = [x["key"] for x in grid["The PC"][1]]
assert row2 == ["DRIVES", "FANS", "BUSIEST NOW"], row2
assert grid["The PC"][1][0]["drive_grid"] is False
assert any(x["key"] == "MOST BLOCKED" and x["badge"] == "LAST 24 H" for row in grid["Raspberry Pi"] for x in row)
print("fans card and live readings ok")

print("\nALL FAN AND TEMPERATURE TESTS PASSED")
