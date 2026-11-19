"""Tests for the HAL 9000 phone server (hal_server.py).

Never touches real settings or services: every file path points at a temporary folder, services
are mock HTTP servers on 127.0.0.1, the UPS is a fake NUT server, and shutdown is replaced by a stub.
"""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))

import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import hal_server as hs
import system_monitor as sm

tmp = Path(tempfile.mkdtemp())
hs.NZB_CONFIG_FILE = tmp / "nzb_manager_config.json"
hs.PHONE_SETTINGS_FILE = tmp / "mobile_settings.json"
hs.ACCESS_KEY_FILE = tmp / "mobile_access_key.txt"
hs.SETTINGS_FILE = tmp / "hal_settings.json"
hs.STATE_FILE = tmp / "hal_state.json"
G = 1024 ** 3

# ---------------- HAL's voice ----------------
assert hs.greeting("Dave", 9) == "Good morning, Dave."
assert hs.greeting("Dave", 15) == "Good afternoon, Dave."
assert hs.greeting("Dave", 23) == "Good evening, Dave."
q = hs.hal_quote([], "Frank", now=0)
assert "Frank" in q or q in [x.format(name="Frank") for x in hs.QUOTES_OK]
crit = [{"level": "critical", "title": "SABnzbd has stopped answering"}]
for t in range(0, 3 * 180 * 3, 180):
    assert "SABnzbd has stopped answering" in hs.hal_quote(crit, "Dave", now=t)
one = [{"level": "warning", "title": "Drive Z: is 91% full"}]
assert any("drive Z: is 91% full" in hs.hal_quote(one, "Dave", now=t) or "except one small thing" in hs.hal_quote(one, "Dave", now=t)
           or "Drive Z: is 91% full" in hs.hal_quote(one, "Dave", now=t) for t in (0,))
many = one + [{"level": "warning", "title": "x"}]
assert "2" in hs.hal_quote(many, "Dave", now=0)
assert "small thing" in hs.hal_quote([{"level": "info", "title": "Windows wants a restart"}], "Dave", now=0)
print("voice ok")

# ---------------- helpers ----------------
route = """===========================================================================
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0     192.168.68.1    192.168.68.60     25
"""
assert hs.parse_route_print(route) == "192.168.68.1"
assert hs.parse_route_print("nothing") is None
proc = "Iface\tDestination\tGateway \tFlags\n eth0\t00000000\t0144A8C0\t0003\n"
assert hs.parse_proc_route(proc) == "192.168.68.1"
assert hs.fmt_dur(30) == "30 s" and hs.fmt_dur(600) == "10 min" and hs.fmt_dur(7200) == "2 h 0 min" and hs.fmt_dur(None) == "—"
print("helpers ok")

# ---------------- drive forecast ----------------
now = 1_000_000_000.0
filling = [[now - 6 * 86400 + i * 3600, 500 * G - i * 2 * G] for i in range(145)]   # 48 GB a day
dl, rate = hs.days_left(filling, filling[-1][1], now=filling[-1][0])
assert abs(rate / G - 48) < 0.5, rate / G
assert abs(dl - filling[-1][1] / rate) < 0.01
steady = [[now - i * 3600, 500 * G] for i in range(40)][::-1]
assert hs.days_left(steady, 500 * G, now=now)[0] is None
assert hs.days_left(filling[-2:], 1, now=now) == (None, None)   # not enough history
print("forecast ok")

# ---------------- settings ----------------
s = hs.load_settings()
assert s["name"] == "Dave" and s["auto_restart"] is False
s2 = hs.validate_settings({"name": "Frank <b>", "cpu_temp_alert": 500, "auto_restart": 1, "nonsense": 3,
                           "ntfy_url": "https://ntfy.sh/hal-test/", "ignored_services": ["prowlarr"]}, s)
assert s2["name"] == "Frank b" and s2["cpu_temp_alert"] == 110 and s2["auto_restart"] is True and "nonsense" not in s2
assert s2["ntfy_url"] == "https://ntfy.sh/hal-test" and s2["ignored_services"] == ["prowlarr"]
for bad in ({"ntfy_url": "ftp://x/y"}, {"nut_host": "bad host;rm"}, {"cpu_alert": "hot"}):
    try:
        hs.validate_settings(bad, s)
        raise AssertionError(bad)
    except ValueError:
        pass
hs.SETTINGS_FILE.write_text(json.dumps({"name": "Frank", "cpu_alert": "x", "push": "yes", "auto_restart": True}))
s3 = hs.load_settings()
assert s3["name"] == "Frank" and s3["cpu_alert"] == 90 and s3["push"] is True and s3["auto_restart"] is True
hs.SETTINGS_FILE.unlink()
print("settings ok")

# ---------------- arr config ----------------
arr = tmp / "arr"
(arr / "Sonarr" / "data").mkdir(parents=True)
(arr / "Sonarr" / "data" / "config.xml").write_text(
    "<Config><Port>18989</Port><UrlBase>/sonarr</UrlBase><ApiKey>abcdefabcdefabcdefabcdefabcdefab</ApiKey></Config>")
hs.ARR_DIRS[:] = [arr]
c = hs.arr_config("Sonarr")
assert c == {"url": "http://127.0.0.1:18989/sonarr", "key": "abcdefabcdefabcdefabcdefabcdefab"}, c
assert hs.arr_config("Radarr") is None
print("arr config ok")


# ---------------- NUT (fake upsd) ----------------
class FakeNut(threading.Thread):
    def __init__(self, status="OL"):
        super().__init__(daemon=True)
        self.status = status
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]

    def run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            f = conn.makefile("rwb")
            for line in f:
                cmd = line.decode().strip()
                if cmd == "LIST UPS":
                    f.write(b'BEGIN LIST UPS\nUPS apc "APC Back-UPS"\nEND LIST UPS\n')
                elif cmd == "LIST VAR apc":
                    rows = {"battery.charge": "81", "battery.runtime": "1440", "ups.load": "34", "ups.status": self.status,
                            "input.voltage": "0.0", "output.voltage": "230.0", "ups.realpower.nominal": "865",
                            "ups.mfr": "APC", "ups.model": "Back-UPS Pro 1500", "ups.test.result": "Done and passed"}
                    f.write(b"BEGIN LIST VAR apc\n" + b"".join(f'VAR apc {k} "{v}"\n'.encode() for k, v in rows.items()) + b"END LIST VAR apc\n")
                elif cmd.startswith("LIST VAR"):
                    f.write(b"ERR UNKNOWN-UPS\n")
                elif cmd == "LOGOUT":
                    f.write(b"OK Goodbye\n")
                    f.flush()
                    break
                f.flush()
            conn.close()


nut = FakeNut("OB DISCHRG")
nut.start()
raw = hs.nut_query("127.0.0.1", nut.port)
assert raw["_name"] == "apc" and raw["battery.charge"] == "81"
ups = hs.ups_from_nut(raw)
assert ups["on_battery"] and not ups["low"] and ups["charge"] == 81 and ups["runtime_s"] == 1440
assert ups["name"] == "APC Back-UPS Pro 1500" and abs(ups["watts"] - 0.34 * 865) < 0.1
try:
    hs.nut_query("127.0.0.1", nut.port, "nope")
    raise AssertionError("unknown UPS accepted")
except ValueError:
    pass


class Batt:
    percent, secsleft, power_plugged = 64.0, 900, False


b = hs.ups_from_battery(Batt())
assert b["on_battery"] and b["charge"] == 64 and b["runtime_s"] == 900 and hs.ups_from_battery(None) is None
print("ups ok")

# ---------------- demo HAL: issues, status, power events ----------------
pushes = []
hs.send_push = lambda settings, title, msg, level="warning": pushes.append((title, level)) or True
shutdowns = []
sm.schedule_shutdown = lambda restart, delay_s=60: (shutdowns.append((restart, delay_s)) or True, "ok")
sm.cancel_shutdown = lambda: (shutdowns.append("cancel") or True, "cancelled")

hal = hs.make_demo_hal()
st = hal.status()
assert st["hal"]["level"] == "warning", st["hal"]
keys = {i["key"]: i for i in st["issues"]}
assert "full:Z:" in keys and keys["full:Z:"]["level"] == "warning" and "9 days" in keys["full:Z:"]["detail"], keys
assert "reboot" in keys and keys["reboot"]["level"] == "info"
assert {x["id"] for x in st["systems"]} == {"services", "network", "storage", "hardware", "security", "power"}
z = next(d for d in st["drives"] if d["mount"] == "Z:")
assert z["label"] == "Media" and z["disk"]["name"] == "Seagate IronWolf 4TB" and 8 < z["days_left"] < 11
assert all(sv["state"] == "up" for sv in st["services"])
json.dumps(st)  # serialisable
assert len(hal.history("24h")["cpu"]) == 144 and len(hal.history("7d")["cpu"]) == 168 and len(hal.history("1h")["cpu"]) == 60

# power cut -> critical event + push; auto shutdown at low battery; power back -> cancel
hal.settings["ups_shutdown"] = True
base_ups = dict(hal.ups)
hal._apply_ups({**base_ups, "on_battery": True, "charge": 80.0, "runtime_s": 1400})
assert hal.events[0]["title"].startswith("Power cut") and pushes[-1][1] == "critical"
assert "emergency power" in hal.events[0]["msg"]
assert not shutdowns
hal._apply_ups({**base_ups, "on_battery": True, "charge": 20.0, "runtime_s": 600})
assert shutdowns == [(False, 60)] and hal.ups_shutdown_sent
assert "My mind is going" in hal.events[0]["msg"]
hal._apply_ups({**base_ups, "on_battery": True, "charge": 19.0, "runtime_s": 500})
assert shutdowns == [(False, 60)]   # only once
hal._apply_ups({**base_ups, "on_battery": False, "charge": 21.0})
assert "cancel" in shutdowns and not hal.ups_shutdown_sent
assert any(e["title"] == "Power is back" for e in list(hal.events)[:3])
print("demo + power ok")

# ---------------- evaluate: announce after sustain, then recovery ----------------
hal2 = hs.make_demo_hal()
hal2.events.clear()
pushes.clear()
hal2.evaluate()
assert any(e["title"].startswith("Drive Z:") for e in hal2.events) and pushes   # warning, no sustain
n = len(hal2.events)
hal2.evaluate()
assert len(hal2.events) == n   # not repeated
hal2.services["sabnzbd"].update({"up": False, "since": time.time() - 600})
hal2.evaluate()
assert hal2.events[0]["title"] == "SABnzbd has stopped answering" and hal2.events[0]["level"] == "critical"
assert hal2.status()["hal"]["level"] == "critical"
hal2.services["sabnzbd"].update({"up": True})
hal2.evaluate()
assert hal2.events[0]["title"] == "SABnzbd is answering again" and hal2.events[0]["level"] == "ok"
# CPU needs 60 s before it is announced
hal2.monitor.snapshot_orig = hal2.monitor.snapshot
hot = hal2.monitor.snapshot()
hot["cpu"]["total"] = 99
hal2.monitor.snapshot = lambda: hot
hal2.evaluate()
assert not any(e["title"].startswith("The CPU") for e in hal2.events)
hal2.pending["cpu"] -= 61
hal2.evaluate()
assert hal2.events[0]["title"] == "The CPU is working flat out"
# the router going down replaces "internet down": no false "back to normal" in between
assert hs.recovery_title("full:Z:", {"title": "Drive Z: is 92% full"}) == "Drive Z: has room again"
assert hs.recovery_title("dev:ix:a", {"title": "NZBGeek indexer isn't answering"}) == "NZBGeek indexer is answering again"
hal2.announced["internet"] = {"title": "The internet connection is down"}
hal2.net.update({"gw_fails": 3})
n = len(hal2.events)
hal2.evaluate()
assert hal2.events[0]["title"] == "I can't reach the router" and not any("network is back" in e["title"] for e in list(hal2.events)[:3])
hal2.net.update({"gw_fails": 0})
hal2.evaluate()
assert hal2.events[0]["title"] == "The network is back to normal"
print("evaluate ok")


# ---------------- real service probing against mock services ----------------
class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/api?mode=queue"):
            body = json.dumps({"queue": {"paused": False, "kbpersec": "39117", "noofslots_total": 3, "timeleft": "0:12:00"}}).encode()
        elif self.path.startswith("/sonarr/api/v3/queue"):
            body = json.dumps({"totalRecords": 2}).encode()
        elif self.path.startswith("/sonarr/api/v3/health"):
            body = json.dumps([{"type": "warning", "message": "x"}]).encode()
        elif self.path == "/secret":
            self.send_response(401)
            self.end_headers()
            return
        else:
            body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


mock = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
threading.Thread(target=mock.serve_forever, daemon=True).start()
mport = mock.server_address[1]
assert hs.http_probe(f"http://127.0.0.1:{mport}/secret")[0] is True     # 401 = alive
dead = socket.socket()
dead.bind(("127.0.0.1", 0))
dead_port = dead.getsockname()[1]
dead.close()
assert hs.http_probe(f"http://127.0.0.1:{dead_port}/", timeout=1)[0] is False
assert hs.tcp_probe("127.0.0.1", mport) is not None and hs.tcp_probe("127.0.0.1", dead_port, 1) is None

hs.NZB_CONFIG_FILE.write_text(json.dumps({"sab_url": f"http://127.0.0.1:{mport}", "sab_api_key": "plainkey123"}))
sab = next(x for x in hs.SERVICES if x["id"] == "sabnzbd")
assert hs.service_details(sab, f"http://127.0.0.1:{mport}") == "downloading 38.2 MB/s · 3 in queue · 0:12:00 left"
(arr / "Sonarr" / "data" / "config.xml").write_text(
    f"<Config><Port>{mport}</Port><UrlBase>/sonarr</UrlBase><ApiKey>abcdefabcdefabcdefabcdefabcdefab</ApiKey></Config>")
son = next(x for x in hs.SERVICES if x["id"] == "sonarr")
assert hs.service_base_url(son, hs.DEFAULT_SETTINGS) == f"http://127.0.0.1:{mport}/sonarr"
assert hs.service_details(son, f"http://127.0.0.1:{mport}/sonarr") == "2 downloading · 1 health warning"

saved = list(hs.SERVICES)
hs.SERVICES[:] = [dict(sab), dict(son), {"id": "radarr", "name": "Radarr", "port": dead_port, "path": "/ping", "procs": [], "svc": [], "task": ""}]
hs.service_base_url_orig = hs.service_base_url
hs.service_base_url = lambda svc, settings: f"http://127.0.0.1:{dead_port}" if svc["id"] == "radarr" else hs.service_base_url_orig(svc, settings)
h3 = hs.Hal(settings=dict(hs.DEFAULT_SETTINGS), monitor=hs.DemoMonitor(), start_threads=False)
h3.check_services()
h3.check_services()
assert h3.service_state("sabnzbd") == "up" and "38.2 MB/s" in h3.services["sabnzbd"]["detail"]
assert h3.service_state("radarr") == "off"      # never seen: not installed here, not an alarm
h3.seen.add("radarr")
assert h3.service_state("radarr") == "down"
h3.settings["ignored_services"] = ["radarr"]
assert h3.service_state("radarr") == "ignored"
hs.SERVICES[:] = saved
hs.service_base_url = hs.service_base_url_orig
print("services ok")

# ---------------- icon ----------------
png = hs.icon(180)
assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 2000
print("icon ok")

# ---------------- the page's script parses ----------------
page = hs.PAGE
script = page.split("<script>")[1].split("</script>")[0]
node = shutil.which("node")
if node:
    js = tmp / "page.js"
    js.write_text(script)
    r = subprocess.run([node, "--check", str(js)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    print("page script ok")
else:
    print("node not found: skipped page script check")
assert "__PAGE_PLACEHOLDER__" not in page

# ---------------- HTTP: gate, auth, endpoints ----------------
token = hs.load_or_create_access_token()
hs._token_bytes = token.encode()
hs.HAL = hs.make_demo_hal()
srv = ThreadingHTTPServer(("127.0.0.1", 0), hs.HalHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]


def call(path, body=None, tok=token):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Mobile-Token": tok, "Content-Type": "application/json"} if tok else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


code, body, hdr = call("/", tok=None)
assert code == 200 and b"HAL 9000" in body and b"__VERSION__" not in body and "frame-ancestors 'none'" in hdr["Content-Security-Policy"]
assert call("/manifest.webmanifest", tok=None)[0] == 200 and call("/icon-512.png", tok=None)[1][:4] == b"\x89PNG"
assert call("/api/status", tok=None)[0] == 401
code, body, _ = call("/api/status")
st = json.loads(body)
assert code == 200 and st["hal"]["quote"] and st["version"] == hs.VERSION
assert json.loads(call("/api/history?range=24h")[1])["range"] == "24h"
assert call("/api/history?range=forever")[0] == 400
ev = json.loads(call("/api/events")[1])["events"]
assert ev and hs.HAL.unread == 0
code, body, _ = call("/api/settings", {"name": "Frank", "ups_shutdown": True})
assert code == 200 and hs.HAL.settings["name"] == "Frank" and json.loads(hs.SETTINGS_FILE.read_text())["ups_shutdown"] is True
assert json.loads(call("/api/settings")[1])["settings"]["name"] == "Frank"
assert "Demo" in json.loads(call("/api/action", {"action": "restart"})[1])["message"]
assert call("/api/service/restart", {"id": "sabnzbd"})[0] == 200
assert call("/api/nope")[0] == 404
# a real (non-demo) HAL refuses restart without confirm and unknown actions
real = hs.Hal(settings=dict(hs.DEFAULT_SETTINGS), monitor=hs.DemoMonitor(), start_threads=False)
real.monitor.__class__ = type("Mon", (hs.DemoMonitor,), {})
assert "can't do that" in hs.run_action(real, "restart", {})["message"]
try:
    hs.run_action(real, "self_destruct", {})
    raise AssertionError("unknown action ran")
except ValueError:
    pass
# wrong codes lock the client out
for i in range(hs.AUTH_MAX_FAILURES):
    c = call("/api/status", tok="wrong")[0]
    assert c == 401
code, body, _ = call("/api/status", tok="wrong")
assert code == 429 and b"serve no purpose" in body
assert call("/api/status")[0] == 429   # even the right code waits
hs.AUTH_FAILURES.clear("127.0.0.1")
assert call("/api/status")[0] == 200
# stray characters around the right code are ignored; a wrong code says what's different
assert call("/api/status", tok=" " + token + "\\n")[0] == 200
code, body, _ = call("/api/status", tok=token[:-2])
msg = json.loads(body)["error"]
assert code == 401 and f"{len(token) - 2} characters" in msg and "starts the same way" in msg, msg
assert "starts differently" in json.loads(call("/api/status", tok="x" * len(token))[1])["error"]
hs.AUTH_FAILURES.clear("127.0.0.1")
# pairing PIN: shown on the PC page, works once, wrong PINs refused
code, body, _ = call("/pair", tok=None)
assert code == 200 and token.encode() in body
pin = hs.PAIRING.pin
assert pin.encode()[:3] in body and len(pin) == 6
bad = "000000" if pin != "000000" else "111111"
assert call("/api/pair", {"pin": bad}, tok=None)[0] == 401
code, body, _ = call("/api/pair", {"pin": pin[:3] + " " + pin[3:]}, tok=None)
assert code == 200 and json.loads(body)["token"] == token
assert call("/api/pair", {"pin": pin}, tok=None)[0] == 401     # used up
hs.PAIRING.current()
hs.PAIRING.expires = time.time() - 1
assert not hs.PAIRING.use(hs.PAIRING.pin)                       # expired
hs.AUTH_FAILURES.clear("127.0.0.1")
# network gate
assert hs.is_client_allowed("192.168.68.20") and hs.is_client_allowed("100.101.1.2") and hs.is_client_allowed("::1")
assert not hs.is_client_allowed("8.8.8.8") and not hs.is_client_allowed("garbage")
srv.shutdown()
print("http ok")

# ---------------- key file in any encoding ----------------
kf = tmp / "key_bom.txt"
code43 = "e2WjcBVmh6Nl5T7HCTTIokfP_N9ZohLmQ20Tq1OLt5c"
kf.write_bytes(b"\xef\xbb\xbf" + code43.encode() + b"\r\n")
assert hs.load_or_create_access_token(kf) == code43
kf.write_bytes((code43 + "\r\n").encode("utf-16"))
assert hs.load_or_create_access_token(kf) == code43
assert hs.clean_code(" " + code43 + "\\n\r\n") == code43
print("key file ok")

# ---------------- a key file holding a typed-out "\\n" (as on the owner's PC) ----------------
weird = code43 + "\\n"                     # 45 characters: ...Lt5c\n
hs._token_bytes = weird.encode()
srv2 = ThreadingHTTPServer(("127.0.0.1", 0), hs.HalHandler)
threading.Thread(target=srv2.serve_forever, daemon=True).start()
port = srv2.server_address[1]
assert call("/api/status", tok=weird)[0] == 200           # exactly as in the file (what the PIN hands out)
assert call("/api/status", tok=code43)[0] == 200          # without the \n
assert call("/api/status", tok=code43 + "\\n ")[0] == 200
code, body, _ = call("/api/status", tok=code43[:-1])
assert code == 401 and "has 43" in json.loads(body)["error"]
hs.AUTH_FAILURES.clear("127.0.0.1")
hs.PAIRING.current()
code, body, _ = call("/api/pair", {"pin": hs.PAIRING.pin}, tok=None)
assert call("/api/status", tok=json.loads(body)["token"])[0] == 200   # PIN, then signed in
srv2.shutdown()
hs._token_bytes = token.encode()
print("typed-out \\n key ok")

# ---------------- state is saved and loaded back ----------------
h4 = hs.Hal(settings=dict(hs.DEFAULT_SETTINGS), monitor=hs.DemoMonitor(), start_threads=False)
h4.add_event("warning", "Test event")
h4.seen.add("jellyfin")
h4.save()
h5 = hs.Hal(settings=dict(hs.DEFAULT_SETTINGS), monitor=hs.DemoMonitor(), start_threads=False)
assert h5.events[0]["title"] == "Test event" and "jellyfin" in h5.seen
demo = hs.make_demo_hal()
before = hs.STATE_FILE.read_text()
demo.save()
assert hs.STATE_FILE.read_text() == before   # demo never writes
print("state ok")

mock.shutdown()

# ---------------- Windows events: plain-English, drive letters, summary ----------------
disks = {"2": {"name": "Seagate IronWolf 4TB", "letters": ["Z:"]}, "0": {"name": "Samsung 990", "letters": ["C:"]}}
raw = {"events": [
    {"t": "2026-11-18T10:00:00Z", "lvl": 2, "src": "disk", "id": 7, "cat": "drives", "msg": "The device, \\Device\\Harddisk2\\DR2, has a bad block."},
    {"t": "2026-11-18T10:00:00Z", "lvl": 2, "src": "disk", "id": 7, "cat": "drives", "msg": "duplicate"},
    {"t": "2026-11-18T09:00:00Z", "lvl": 3, "src": "disk", "id": 153, "cat": "drives", "msg": "The IO operation at logical block address 0x1 for Disk 2 (PDO name: \\Device\\00000034) was retried."},
    {"t": "2026-11-18T08:00:00Z", "lvl": 2, "src": "Ntfs", "id": 55, "cat": "drives", "msg": "A corruption was discovered in the file system structure on volume Z:."},
    {"t": "2026-11-17T08:00:00Z", "lvl": 2, "src": "Service Control Manager", "id": 7031, "cat": "services", "msg": "The Jellyfin Server service terminated unexpectedly."},
    {"t": "2026-11-17T07:00:00Z", "lvl": 2, "src": "Application Error", "id": 1000, "cat": "apps", "msg": "Faulting application name: Sonarr.exe, version: 4.0"},
    {"t": "2026-11-16T07:00:00Z", "lvl": 1, "src": "Microsoft-Windows-Kernel-Power", "id": 41, "cat": "crashes", "msg": "The system has rebooted without cleanly shutting down first."},
    {"t": "2026-11-16T07:00:00Z", "lvl": 1, "src": "Microsoft-Windows-Kernel-Power", "id": 41, "cat": "other", "msg": "same, from the catch-all"},
    {"t": "2026-11-15T07:00:00Z", "lvl": 2, "src": "Microsoft-Windows-WHEA-Logger", "id": 17, "cat": "hardware", "msg": "A corrected hardware error has occurred. Component: PCI Express Root Port Error Source: AER"},
    {"t": "2026-11-15T06:00:00Z", "lvl": 1, "src": "Microsoft-Windows-Windows Defender", "id": 1116, "cat": "security", "msg": "Malware detected"},
    {"t": "2026-11-14T06:00:00Z", "lvl": 2, "src": "SomethingNew", "id": 9, "cat": "other", "msg": "First sentence here. Second sentence."},
    {"t": "", "lvl": 2, "src": "x", "id": 1, "cat": "other", "msg": "no time"},
]}
evs = hs.parse_winevents(raw, disks)
titles = [e["title"] for e in evs]
assert titles[0] == "Bad block on Drive Z: (Seagate IronWolf 4TB)" and evs[0]["level"] == "critical" and evs[0]["serious"], titles
assert "Drive Z: (Seagate IronWolf 4TB) had to retry" in titles and "File system damage on Drive Z:" in titles
assert "chkdsk Z: /f" in next(e for e in evs if e["id"] == 55)["explain"]
assert "Service crashed: Jellyfin Server" in titles and "Sonarr.exe crashed" in titles
assert titles.count("The PC shut down unexpectedly") == 1                 # catch-all duplicate dropped
assert next(e for e in evs if e["cat"] == "hardware")["who"] == "PCI Express Root Port"
assert "Defender found a threat" in titles and "First sentence here." in titles
assert len(evs) == 9 and all(evs[i]["t"] >= evs[i + 1]["t"] for i in range(len(evs) - 1))
now_ = evs[0]["t"] + 3600
sm_ = hs.event_summary(evs, now_)
assert sm_["cats"]["drives"] == 3 and sm_["serious_drives"] == {"Drive Z: (Seagate IronWolf 4TB)": 3}, sm_
assert hs.event_summary(evs, now_ + 40 * 86400)["total"] == 0
# five retries alone flag a drive; one doesn't
retry = lambda i: {"t": f"2026-11-18T0{i}:00:00Z", "lvl": 3, "src": "disk", "id": 153, "cat": "drives", "msg": "for Disk 2 was retried."}
one = hs.parse_winevents({"events": [retry(1)]}, disks)
assert hs.event_summary(one, one[0]["t"])["serious_drives"] == {}
five = hs.parse_winevents({"events": [retry(i) for i in range(1, 6)]}, disks)
assert list(hs.event_summary(five, five[0]["t"])["serious_drives"].values()) == [5]
# demo: issue raised, events API answers
dh = hs.make_demo_hal()
assert any(i["key"].startswith("evdrive:") and i["tab"] == "events" for i in dh.status()["issues"])
v = dh.winevents_view()
assert v["windows"] and v["summary"]["cats"]["drives"] == 6 and v["events"] and "drives" in v["cats"]
json.dumps(v)
print("windows events ok")

# ---------------- HAL inside the NZB phone server (/hal) ----------------
import mobile_server as ms
assert ms._hal_mod is hs and issubclass(ms.MobileHandler, hs.HalRoutes)
ms._token_bytes = token.encode()
hs._token_bytes = token.encode()
hs.HAL = hs.make_demo_hal()
msrv = ThreadingHTTPServer(("127.0.0.1", 0), ms.MobileHandler)
threading.Thread(target=msrv.serve_forever, daemon=True).start()
port = msrv.server_address[1]
ms._hal_state.update(running=False, note="turned off (--no-hal)")
code, body, _ = call("/hal/", tok=None)
assert code == 503 and b"turned off" in body
assert call("/hal/api/status")[0] == 503
ms._hal_state.update(running=True, note="running")
req = urllib.request.Request(f"http://127.0.0.1:{port}/hal")
class NoRedir(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None
try:
    urllib.request.build_opener(NoRedir).open(req, timeout=5)
    raise AssertionError("no redirect")
except urllib.error.HTTPError as e:
    assert e.code == 302 and e.headers["Location"] == "/hal/"
code, body, _ = call("/hal/", tok=None)
assert code == 200 and b"const B='/hal'" in body and b'href="/hal/manifest.webmanifest"' in body and b"__BASE__" not in body
assert f"localhost:{port}/hal/pair".encode() in body
man = json.loads(call("/hal/manifest.webmanifest", tok=None)[1])
assert man["start_url"] == "/hal/" and man["scope"] == "/hal/" and man["icons"][0]["src"] == "/hal/icon.png"
assert call("/hal/icon-512.png", tok=None)[1][:4] == b"\x89PNG"
assert call("/hal/api/status", tok=None)[0] == 401
code, body, _ = call("/hal/api/status")
assert code == 200 and json.loads(body)["version"] == hs.VERSION
assert call("/hal/api/settings", {"name": "Frank"})[0] == 200 and hs.HAL.settings["name"] == "Frank"
assert f":{port}/hal/".encode() in call("/hal/pair", tok=None)[1]
hs.PAIRING.current()
code, body, _ = call("/hal/api/pair", {"pin": hs.PAIRING.pin}, tok=None)
assert code == 200 and json.loads(body)["token"] == token
assert call("/", tok=None)[0] == 200                         # the NZB app is still there
assert call("/api/nope")[0] in (401, 404)                    # NZB routes keep their own sign-in
args = ms.parse_args(["--no-hal"])
assert args.no_hal and "--no-hal" in ms._autostart_args(args) and ms.start_embedded_hal(args).startswith("off")
assert "--hal-port" in ms._autostart_args(ms.parse_args(["--hal-port", "9001"]))
msrv.shutdown()
print("hal inside the phone server ok")


# ---------------- Hide 24 h (snooze) ----------------
sh = hs.make_demo_hal()
sh.demo = False
hs.STATE_FILE = tmp / "snooze_state.json"
st = sh.status()
keys = [i["key"] for i in st["issues"]]
assert "full:Z:" in keys and st["hal"]["level"] == "warning"
assert sh.snooze(["full:Z:", "nope"], 24) == 1
st = sh.status()
assert "full:Z:" not in [i["key"] for i in st["issues"]]
hid = st["snoozed"]
assert [i["key"] for i in hid] == ["full:Z:"] and hid[0]["until"] > time.time() + 23 * 3600
for t in range(0, 180 * 12, 180):          # every line HAL might pick: none mentions the hidden alert
    assert "92% full" not in hs.hal_quote([i for i in st["issues"]], "Dave", now=t)
storage = next(x for x in st["systems"] if x["id"] == "storage")
# no phone alert while hidden
sh.events.clear(); sh.announced.clear(); sh.pending.clear(); pushes.clear()
sh.evaluate()
assert not any(e["title"].startswith("Drive Z: is") for e in sh.events)
# saved, and loaded back
saved = json.loads(hs.STATE_FILE.read_text())
assert "full:Z:" in saved["snoozed"]
# time's up and still there: shows again, and the phone hears about it again
sh.snoozed["full:Z:"]["until"] = time.time() - 1
sh.evaluate()
assert "full:Z:" not in sh.snoozed and any(e["title"].startswith("Drive Z: is") for e in sh.events)
# gets worse: shows again at once
sh.snooze(["full:Z:"])
sh.snoozed["full:Z:"]["level"] = "info"          # pretend it was milder when hidden
assert "full:Z:" in [i["key"] for i in sh.status()["issues"]] and "full:Z:" not in sh.snoozed
# goes away for a while: snooze dropped, so it shows if it comes back
sh.snooze(["full:Z:"])
sh.apply_snoozes([], time.time())
assert "full:Z:" in sh.snoozed                    # a short gap isn't "gone"
sh.apply_snoozes([], time.time() + sh.SNOOZE_GRACE + 1)
assert "full:Z:" not in sh.snoozed
# unsnooze
sh.snooze(["full:Z:"]); assert sh.unsnooze(["full:Z:"]) == 1 and "full:Z:" in [i["key"] for i in sh.status()["issues"]]
# over HTTP
hs.HAL = sh
srv3 = ThreadingHTTPServer(("127.0.0.1", 0), hs.HalHandler)
threading.Thread(target=srv3.serve_forever, daemon=True).start()
port = srv3.server_address[1]
hs._token_bytes = token.encode()
code, body, _ = call("/api/snooze", {"keys": ["full:Z:"], "hours": 24})
r = json.loads(body)
assert code == 200 and r["ok"] and "keep quiet" in r["message"]
assert json.loads(call("/api/status")[1])["snoozed"][0]["key"] == "full:Z:"
code, body, _ = call("/api/snooze", {"keys": ["full:Z:"], "off": True})
assert code == 200 and json.loads(call("/api/status")[1])["snoozed"] == []
assert call("/api/snooze", {"keys": "full:Z:"})[0] == 400
assert json.loads(call("/api/snooze", {"keys": ["gone:X"]})[1])["ok"] is False
srv3.shutdown()
print("snooze ok")

print("\nALL HAL TESTS PASSED")
