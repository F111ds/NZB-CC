"""Tests for System Command Center: parsers, GPU merging, alerts, settings, cleanup/scan helpers,
and a smoke test that opens every page of the window with simulated Windows data.

Never touches real settings: the config path points at a temporary folder. Nothing is killed,
deleted or changed outside temporary folders.
"""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))

import json
import os
import tempfile
import threading
import time
from pathlib import Path

import system_monitor as sm

tmp = Path(tempfile.mkdtemp())
sm.CONFIG_FILE = tmp / "scc.json"
G = 1024 ** 3

# ---------------- formatting ----------------
assert sm.fmt_bytes(None) == "—"
assert sm.fmt_bytes(512) == "512 B"
assert sm.fmt_bytes(1536) == "1.5 KB"
assert sm.fmt_bytes(3 * G) == "3.0 GB"
assert sm.fmt_rate(2 * 1024 * 1024) == "2.0 MB/s"
assert sm.fmt_duration(59) == "0m"
assert sm.fmt_duration(3 * 86400 + 4 * 3600 + 120) == "3d 4h 2m"
assert sm.fmt_duration(None) == "—"
assert sm.fmt_temp(None) == "—" and sm.fmt_temp(66.6) == "67°C"
assert sm.safe_num("[N/A]") is None and sm.safe_num("[Not Supported]") is None
assert sm.safe_num(" 47 ") == 47.0 and sm.safe_num("12,5") == 12.5 and sm.safe_num("66 C") == 66.0
assert sm.as_list(None) == [] and sm.as_list({"a": 1}) == [{"a": 1}] and sm.as_list([1]) == [1]
print("format ok")

# ---------------- dates ----------------
assert sm.parse_date("/Date(1700000000000)/") == 1700000000.0
assert abs(sm.parse_date("2024-03-02T10:11:12.1234567+01:00") - 1709370672.123456) < 0.01
assert sm.parse_date({"value": "/Date(1000)/", "DateTime": "x"}) == 1.0
assert sm.parse_date("garbage") is None and sm.parse_date("") is None
assert sm.fmt_ago(time.time() - 3 * 86400 - 10) == "3 days ago"
print("dates ok")

# ---------------- PowerShell JSON ----------------
assert sm.parse_json_output('WARNING: something\n{"a": 1}\n') == {"a": 1}
assert sm.parse_json_output("") is None
assert sm.parse_json_output("{broken\n") is None
assert sm.run_powershell_json("1") is None or sm.IS_WINDOWS  # off Windows it's a harmless None
print("json ok")

# ---------------- nvidia-smi ----------------
line = "0, NVIDIA GeForce RTX 4080, 566.36, 87, 31, 9400, 16376, 68, 245.50, 320.00, 47, 2715, 11501, 3105, 11501, P2, 4, 16"
row = sm.parse_nvidia_smi_line(line, sm.NVSMI_FIELDS)
assert row["index"] == 0 and row["name"] == "NVIDIA GeForce RTX 4080" and row["driver_version"] == "566.36"
assert row["utilization.gpu"] == 87 and row["memory.total"] == 16376 and row["pstate"] == "P2"
na = sm.parse_nvidia_smi_line("1, Tesla T4, 550.1, 0, 0, 1, 15360, 40, [N/A], [N/A], [N/A], 300, 5000, 1590, 5001, P8",
                              sm.NVSMI_FIELDS[:16])
assert na["power.draw"] is None and na["fan.speed"] is None and na["temperature.gpu"] == 40
assert sm.parse_nvidia_smi_line("garbage", sm.NVSMI_FIELDS) is None
print("nvidia-smi ok")

# ---------------- GPU counters ----------------
L = "0x00000000_0x0000D1A2"
eng = [f"pid_100_luid_{L}_phys_0_eng_0_engtype_3D=40",
       f"pid_200_luid_{L}_phys_0_eng_0_engtype_3D=25",       # same engine, other process: sums to 65
       f"pid_200_luid_{L}_phys_0_eng_3_engtype_VideoDecode=12",
       f"pid_300_luid_0x00000000_0x0000BEEF_phys_0_eng_0_engtype_3D=5",
       "not a counter=50", f"pid_1_luid_{L}_phys_0_eng_9_engtype_Copy=oops"]
adapters, per_proc = sm.parse_gpu_engines(eng)
a = adapters[L.lower()]
assert a["util"] == 65 and a["engines"]["3D"] == 65 and a["engines"]["VideoDecode"] == 12
assert per_proc == {100: 40, 200: 25, 300: 5}
over, _ = sm.parse_gpu_engines([f"pid_1_luid_{L}_phys_0_eng_0_engtype_3D=80", f"pid_2_luid_{L}_phys_0_eng_0_engtype_3D=70"])
assert over[L.lower()]["util"] == 100  # clamped
mem = sm.parse_gpu_memory([f"luid_{L}_phys_0|{9 * G}|{G}", "bad line"])
assert mem[L.lower()] == {"dedicated": 9 * G, "shared": G}
assert sm.luid_from_qword(0x0000D1A2) == "0x00000000_0x0000d1a2"
assert sm.luid_from_qword((5 << 32) | 0xABC) == "0x00000005_0x00000abc"
assert sm.guess_vendor("AMD Radeon RX 7900 XTX") == "AMD" and sm.guess_vendor("NVIDIA GeForce") == "NVIDIA"
print("gpu counters ok")

# ---------------- sensors ----------------
sens = sm.parse_sensor_lines(["Temperature|/amdcpu/0|Core (Tctl/Tdie)|71.5", "Temperature|/amdcpu/0|CCD1|65",
                              "Temperature|/gpu-amd/0|GPU Core|58", "Temperature|/nvme/0|Temperature|999",  # impossible: dropped
                              "Fan|/lpc/nct/0|CPU Fan|1200", "Power|/amdcpu/0|Package|88", "junk"])
assert len(sens["temps"]) == 3 and sens["fans"][0]["value"] == 1200 and sens["power"][0]["value"] == 88
assert sm.pick_cpu_temp(sens["temps"]) == 71.5
assert sm.pick_cpu_temp([{"hw": "/intelcpu/0", "name": "Core #1", "value": 50}, {"hw": "/intelcpu/0", "name": "Core #2", "value": 55}]) == 55
assert sm.pick_cpu_temp([]) is None
assert sm.pick_gpu_sensor(sens["temps"], "AMD") == 58 and sm.pick_gpu_sensor(sens["temps"], "Intel") is None
print("sensors ok")

# LibreHardwareMonitor web server (data.json), shaped like an ASUS Z390 board with a Nuvoton chip
def _s(text, value, typ=None, sid=None):
    d = {"Text": text, "Value": value, "Min": "", "Max": "", "Children": []}
    if typ:
        d["Type"] = typ
    if sid:
        d["SensorId"] = sid
    return d
lhm_old = {"Text": "Sensor", "Children": [{"Text": "HAL9000", "Children": [
    {"Text": "ASUS ROG STRIX Z390-E", "ImageURL": "images_icon/mainboard.png", "Children": [
        {"Text": "Nuvoton NCT6798D", "ImageURL": "images_icon/chip.png", "Children": [
            {"Text": "Voltages", "Children": [_s("Vcore", "1.120 V")]},
            {"Text": "Temperatures", "Children": [_s("CPU Core", "45.0 °C"), _s("Temperature #1", "35,5 °C")]},
            {"Text": "Fans", "Children": [_s("Fan #1", "1006 RPM"), _s("Fan #3", "0 RPM")]}]}]},
    {"Text": "NVIDIA GeForce RTX 2080", "ImageURL": "images_icon/nvidia.png", "Children": [
        {"Text": "Temperatures", "Children": [_s("GPU Core", "52.0 °C")]}]}]}]}
lhm = sm.parse_lhm_json(lhm_old)
assert [t["name"] for t in lhm["temps"]] == ["CPU Core", "Temperature #1", "GPU Core"]
assert lhm["temps"][1]["value"] == 35.5 and lhm["temps"][0]["label"] == "Nuvoton NCT6798D"
assert lhm["fans"][0]["value"] == 1006 and len(lhm["fans"]) == 2
assert sm.pick_cpu_temp(lhm["temps"]) == 45.0          # CPU temp listed under the motherboard chip
assert sm.pick_gpu_sensor(lhm["temps"], "NVIDIA") == 52.0
lhm_new = {"Text": "Sensor", "Children": [{"Text": "PC", "Children": [
    {"Text": "Intel Core i9-9900K", "Children": [{"Text": "Temperatures", "Children": [
        _s("CPU Package", "61.0 °C", "Temperature", "/intelcpu/0/temperature/8"),
        _s("CPU Core #1", "58.0 °C", "Temperature", "/intelcpu/0/temperature/0")]},
        {"Text": "Powers", "Children": [_s("CPU Package", "88.1 W", "Power", "/intelcpu/0/power/0")]}]}]}]}
lhm2 = sm.parse_lhm_json(lhm_new)
assert lhm2["temps"][0]["hw"] == "/intelcpu/0" and sm.pick_cpu_temp(lhm2["temps"]) == 61.0
assert lhm2["power"][0]["value"] == 88.1
assert sm.parse_lhm_json(None) == {"temps": [], "fans": [], "power": []}

import http.server
class _LHM(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(lhm_old).encode("utf-8")
        self.send_response(200 if self.path == "/data.json" else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass
srv = http.server.HTTPServer(("127.0.0.1", 0), _LHM)
threading.Thread(target=srv.serve_forever, daemon=True).start()
got = sm.fetch_lhm_http(srv.server_address[1])
assert got and sm.pick_cpu_temp(got["temps"]) == 45.0
srv.shutdown()
assert sm.fetch_lhm_http(srv.server_address[1], timeout=0.5) is None   # server gone: quietly None
assert sm.fetch_lhm_http("nope") is None and sm.fetch_lhm_http(0) is None

# monitor: web-server sensors are used when WMI has none, and WMI wins when it has data
mon = sm.SystemMonitor(settings=dict(sm.DEFAULT_SETTINGS), enable_external=False)
mon._http_sensors, mon._http_time = lhm, time.monotonic()
mon._apply_sensors()
assert mon.snapshot()["sensor_source"] == "LibreHardwareMonitor" and "web server" in mon.sources["Temperatures"]
mon.sample_fast()
assert mon.snapshot()["cpu"]["temp"] == 45.0
mon._wmi_sensors, mon._wmi_src = sm.parse_sensor_lines(["Temperature|/amdcpu/0|Core (Tctl/Tdie)|70"]), "LibreHardwareMonitor"
mon._apply_sensors()
assert "WMI" in mon.sources["Temperatures"] and mon.snapshot()["sensors"]["temps"][0]["value"] == 70
mon._wmi_sensors, mon._http_sensors = None, None
mon._apply_sensors()
assert mon.sources["Temperatures"].startswith("not found") and "Remote Web Server" in mon.sources["Temperatures"]
print("lhm web server ok")

# ---------------- ping / network / power ----------------
win_en = "Pinging 1.1.1.1 with 32 bytes of data:\r\nReply from 1.1.1.1: bytes=32 time=14ms TTL=57\r\n"
win_de = "Antwort von 1.1.1.1: Bytes=32 Zeit<1ms TTL=57"
linux = "64 bytes from 1.1.1.1: icmp_seq=1 ttl=57 time=13.7 ms"
timeout = "Request timed out.\r\n\r\nPing statistics for 1.1.1.1:\r\n    Packets: Sent = 1, Received = 0, Lost = 1 (100% loss),"
assert sm.parse_ping_ms(win_en) == 14 and sm.parse_ping_ms(win_de) == 1 and sm.parse_ping_ms(linux) == 13.7
assert sm.parse_ping_ms(timeout) is None
assert sm.ping_once("bad host; rm -rf /") is None  # rejected before running anything
plans = sm.parse_powercfg_list("Existing Power Schemes (* Active)\n-----\n"
                               "Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced) *\n"
                               "Power Scheme GUID: 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c  (High performance)\n")
assert plans == [{"guid": "381b4222-f694-41f0-9685-ff5bb260df2e", "name": "Balanced", "active": True},
                 {"guid": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c", "name": "High performance", "active": False}]
assert sm.set_power_plan("not-a-guid")[0] is False
wlan = sm.parse_netsh_wlan("    Name                   : Wi-Fi\n    SSID                   : HomeNet\n    BSSID                  : aa:bb\n"
                           "    Radio type             : 802.11ax\n    Receive rate (Mbps)    : 1201\n    Signal                 : 86%\n")
assert wlan == {"ssid": "HomeNet", "radio": "802.11ax", "rx_rate": 1201.0, "signal": 86.0}
assert sm.startup_entry_enabled(None) and sm.startup_entry_enabled(bytes([2] + [0] * 11))
assert not sm.startup_entry_enabled(bytes([3] + [0] * 11)) and sm.startup_entry_enabled(bytes([6]))
print("network/power ok")

# ---------------- inventory normalisation ----------------
inv = sm.normalize_inventory({
    "disks": {"id": "0", "name": "Samsung 990", "media": "Unspecified", "bus": "17", "health": "0"},
    "parts": [{"disk": "0", "letter": "C"}, {"disk": "0", "letter": "\u0000"}],
    "volumes": [{"letter": "C", "label": "Windows", "health": "Healthy"}],
    "firewall": [{"name": "Public", "enabled": "True"}, {"name": "Private", "enabled": "False"}, {"name": "Domain", "enabled": "1"}],
    "av": "Windows Defender",
})
d = inv["disks"][0]
assert d["bus_name"] == "NVMe" and d["media_name"] == "SSD" and d["health_name"] == "Healthy"
assert sm.media_type_name("3") == "HDD" and sm.media_type_name("SSD") == "SSD" and sm.bus_type_name("7") == "USB"
assert sm.health_name("2") == "Unhealthy" and sm.health_name("Warning") == "Warning"
assert [p["letter"] for p in inv["parts"]] == ["C"]
assert [f["on"] for f in inv["firewall"]] == [True, False, True]
assert inv["av"] == ["Windows Defender"] and inv["hotfix"] == []
print("inventory ok")

# ---------------- alerts ----------------
fired = []
eng_ = sm.AlertEngine(on_alert=fired.append)
assert eng_.check("cpu", True, "warning", "t", "m", sustain=30, now=1000) is None   # just started
assert eng_.check("cpu", True, "warning", "t", "m", sustain=30, now=1029) is None
assert eng_.check("cpu", True, "warning", "t", "m", sustain=30, now=1031) is not None
assert eng_.check("cpu", True, "warning", "t", "m", sustain=30, now=1100) is None   # cooldown
eng_.check("cpu", False, "warning", "t", "m", now=1101)                           # condition cleared
assert eng_.check("cpu", True, "warning", "t", "m", sustain=0, cooldown=900, now=1200) is None  # still cooling down
assert eng_.check("cpu", True, "warning", "t", "m", sustain=0, cooldown=900, now=1932) is not None
assert len(fired) == 2 and eng_.unread == 2
snap = {"cpu": {"total": 99, "temp": 95}, "mem": {"percent": 50, "available": 8 * G},
        "gpus": [{"key": "g", "name": "RTX", "temp": 90, "vram_used": 15.8 * G, "vram_total": 16 * G}],
        "volumes": [{"mount": "D:", "percent": 96.0, "free": 40 * G}],
        "inventory": {"disks": [{"id": "1", "name": "WD", "health_name": "Warning", "temp": "65"}]},
        "battery": {"percent": 9, "plugged": False}}
e2 = sm.AlertEngine()
s = dict(sm.DEFAULT_SETTINGS, alert_sustain_s=0)
e2.evaluate(snap, s, now=5000)
keys = {a["key"] for a in e2.items}
assert {"cpu", "cpu_temp", "gpu_temp:g", "vram:g", "vol:D:", "health:1", "disktemp:1", "battery"} <= keys, keys
assert "ram" not in keys
e2.clear()
assert not e2.items and e2.unread == 0
print("alerts ok")

# ---------------- settings ----------------
st = sm.load_settings()
assert st == sm.DEFAULT_SETTINGS
sm.CONFIG_FILE.write_text(json.dumps({"cpu_alert": 75, "toasts": "yes", "gpu_temp_alert": 80.5, "unknown": 1, "ping_host": "8.8.8.8"}))
st = sm.load_settings()
assert st["cpu_alert"] == 75 and st["toasts"] is True and st["gpu_temp_alert"] == 80.5 and "unknown" not in st
assert st["ping_host"] == "8.8.8.8"
st["ram_alert"] = 70
sm.save_settings(st)
assert sm.load_settings()["ram_alert"] == 70
sm.CONFIG_FILE.write_text("{not json")
assert sm.load_settings() == sm.DEFAULT_SETTINGS
print("settings ok")

# ---------------- temp cleanup & space scan (temporary folders only) ----------------
junk = tmp / "junk"
(junk / "sub").mkdir(parents=True)
old_f, new_f = junk / "old.tmp", junk / "sub" / "new.tmp"
old_f.write_bytes(b"x" * 5000)
new_f.write_bytes(b"y" * 100)
(junk / "sub" / "old2.tmp").write_bytes(b"z" * 300)
past = time.time() - 3 * 86400
os.utime(old_f, (past, past))
os.utime(junk / "sub" / "old2.tmp", (past, past))
dry = sm.clean_temp_files(dry_run=True, folders=[junk])
assert dry == {"bytes": 5300, "files": 2, "failed": 0}, dry
assert old_f.exists()
done = sm.clean_temp_files(dry_run=False, folders=[junk])
assert done["bytes"] == 5300 and not old_f.exists() and new_f.exists()

tree = tmp / "tree"
(tree / "big").mkdir(parents=True)
(tree / "small").mkdir()
(tree / "big" / "a.bin").write_bytes(b"0" * 40000)
(tree / "big" / "b.bin").write_bytes(b"0" * 2000)
(tree / "small" / "c.bin").write_bytes(b"0" * 10)
(tree / "loose.txt").write_bytes(b"0" * 5)
res = sm.scan_folder(str(tree))
assert res["total"] == 42015 and res["count"] == 4
assert os.path.basename(res["children"][0][0]) == "big" and res["children"][0][1] == 42000
assert res["files"][0][0] == 40000
cancel = threading.Event(); cancel.set()
assert sm.scan_folder(str(tree), cancel=cancel)["cancelled"]
assert sm.scan_folder(str(tmp / "missing"))["error"]
print("cleanup/scan ok")

# ---------------- safety: process protection ----------------
assert sm.kill_process(0)[0] is False and sm.kill_process(os.getpid())[0] is False
print("process protection ok")

# ---------------- monitor: one sample of everything, no background threads ----------------
m = sm.SystemMonitor(settings=dict(sm.DEFAULT_SETTINGS, toasts=False, ping_enabled=False), enable_external=False)
m.sample_fast(); time.sleep(0.3); m.sample_fast()
m.sample_processes(); m.sample_volumes()
snap = m.snapshot()
assert 0 <= snap["cpu"]["total"] <= 100 and len(snap["cpu"]["per_core"]) == m.cpu_count
assert snap["mem"]["total"] > 0 and snap["processes"] and all(p["pid"] != 0 for p in snap["processes"])
assert isinstance(snap["volumes"], list) and len(m.hist("cpu")) == 2

# GPU merge: DirectX names + counters + nvidia-smi; Microsoft Basic Render is hidden
LN, LI, LM = "0x00000000_0x0000d1a2", "0x00000000_0x0000c0de", "0x00000000_0x00000bad"
m._dxgi = {LN: {"name": "NVIDIA GeForce RTX 4080", "vendor": "NVIDIA", "vram": 16 * G},
           LI: {"name": "Intel(R) UHD Graphics 770", "vendor": "Intel", "vram": 128 * 1024 ** 2},
           LM: {"name": "Microsoft Basic Render Driver", "vendor": "Microsoft", "vram": 0}}
ad, pp = sm.parse_gpu_engines([f"pid_9_luid_{LN}_phys_0_eng_0_engtype_3D=50", f"pid_8_luid_{LI}_phys_0_eng_0_engtype_3D=3",
                               f"pid_7_luid_{LM}_phys_0_eng_0_engtype_3D=1"])
m._gpu_counters = {"adapters": ad, "per_process": pp, "mem": sm.parse_gpu_memory([f"luid_{LN}_phys_0|{9 * G}|0"]), "ok": True}
m._nvsmi = {0: row}
gpus = m._merge_gpus([])
assert [g["vendor"] for g in gpus] == ["NVIDIA", "Intel"], gpus
n = gpus[0]
assert n["util"] == 87 and n["temp"] == 68 and n["vram_total"] == 16376 * 1024 * 1024 and n["pcie"] == "Gen 4 x16"
assert n["engines"]["3D"] == 50 and "nvidia-smi" in n["source"]
m._dxgi = {}
m._gpu_counters = {"adapters": {}, "per_process": {}, "mem": {}, "ok": False}
only_nv = m._merge_gpus([])
assert len(only_nv) == 1 and only_nv[0]["key"] == "nv0" and only_nv[0]["util"] == 87
print("monitor ok")

# ---------------- UI smoke test (needs Tk and a display) ----------------
try:
    import tkinter
    tkinter.Tk().destroy()
    have_display = True
except Exception as exc:  # noqa: BLE001
    have_display = False
    print(f"UI smoke test skipped ({exc.__class__.__name__}: no display)")

if have_display:
    import system_command_center as scc
    m2 = sm.SystemMonitor(settings=dict(sm.DEFAULT_SETTINGS, toasts=False, ping_enabled=False), enable_external=False)
    m2.sample_fast(); m2.sample_processes(); m2.sample_volumes()
    m2._dxgi = {LN: {"name": "NVIDIA GeForce RTX 4080", "vendor": "NVIDIA", "vram": 16 * G}}
    ad_nv, _ = sm.parse_gpu_engines([f"pid_9_luid_{LN}_phys_0_eng_0_engtype_3D=50"])
    m2._gpu_counters = {"adapters": ad_nv, "per_process": {snap["processes"][0]["pid"]: 12.0}, "mem": {}, "ok": True}
    m2._nvsmi = {0: row}
    with m2._lock:
        m2._state["inventory"] = sm.normalize_inventory({
            "os": {"caption": "Microsoft Windows 11 Pro", "installed": "2024-03-02T10:11:12"},
            "cpu": {"name": "AMD Ryzen 7 7800X3D 8-Core Processor", "cores": 8, "threads": 16, "maxmhz": 4200},
            "ram": [{"slot": "A2", "size": 16 * G, "speed": 6000}], "video": [{"name": "RTX 4080", "w": 2560, "h": 1440, "hz": 144}],
            "disks": [{"id": "0", "name": "Samsung 990", "media": "4", "bus": "17", "health": "0", "temp": 41, "wear": 2, "hours": 900}],
            "defender": {"av": True, "rtp": True, "sig": "2026-01-01T00:00:00"},
            "firewall": [{"name": "Public", "enabled": "True"}], "hotfix": [{"id": "KB1", "date": "2026-01-01T00:00:00"}]})
        m2._state["sensors"] = sens
        m2._state["sensor_source"] = "LibreHardwareMonitor"
        m2._state["power_plans"] = plans
    m2.sample_fast()
    app = scc.CommandCenterApp(monitor=m2, start_monitor=False)
    app.withdraw()
    snap2 = m2.snapshot()
    assert snap2["gpus"][0]["name"] == "NVIDIA GeForce RTX 4080"
    for key, _label, _icon, _cls in scc.CommandCenterApp.PAGES:
        app.show_page(key)
        app.update()
        app.pages[key].refresh(snap2)
        app.update()
        assert app.current == key
    assert app.page_title.cget("text") == "Settings"
    gpu_page = app.pages["gpu"]
    assert list(gpu_page.cards) == [snap2["gpus"][0]["key"]]

    procs_page = app.pages["processes"]
    procs_page._render(snap2["processes"])
    assert len(procs_page.tree.get_children()) == len(snap2["processes"])
    procs_page.group.set(True)
    procs_page._force()
    names = {p["name"] for p in snap2["processes"]}
    assert len(procs_page.tree.get_children()) == len(names)
    procs_page.group.set(False)
    procs_page.search_entry.insert(0, snap2["processes"][0]["name"])
    procs_page._force()
    assert 1 <= len(procs_page.tree.get_children()) < len(snap2["processes"]) or len(names) == 1
    procs_page._sort_by("name")
    first = procs_page.tree.get_children()[0]
    assert procs_page.tree.item(first, "text").lower() == min(
        p["name"].lower() for p in snap2["processes"] if snap2["processes"][0]["name"].lower() in p["name"].lower())

    # alerts badge + page
    m2.alerts.check("x", True, "warning", "Test", "msg")
    app.show_page("dashboard")
    app._refresh_chrome(m2.snapshot())
    assert app.nav["alerts"].badge.cget("text") == "1"
    app.show_page("alerts")
    app.pages["alerts"].refresh(m2.snapshot())
    assert m2.alerts.unread == 0

    # status messages and background actions report back on the UI thread
    app.run_action("Demo", lambda: (True, "all good"))
    deadline = time.time() + 5
    while "all good" not in app.status.cget("text") and time.time() < deadline:
        app.update()
        time.sleep(0.02)
    assert "all good" in app.status.cget("text") and any("Demo: all good" in line for line in app.log_lines)
    app.run_bg(lambda: 1 / 0)
    deadline = time.time() + 5
    while "went wrong" not in app.status.cget("text") and time.time() < deadline:
        app.update()
        time.sleep(0.02)
    assert "went wrong" in app.status.cget("text")
    app.on_close()
    assert json.loads(sm.CONFIG_FILE.read_text())["last_page"] == "alerts"
    print("ui ok")

print("ALL SYSTEM CENTER TESTS PASSED")
