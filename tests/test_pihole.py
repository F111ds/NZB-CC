"""HAL's Pi-hole watcher: finding it, Pi-hole 6 and 5, password handling, alerts, pause/resume and the settings API.

Uses fake Pi-hole 6 and Pi-hole 5 servers on 127.0.0.1 with made-up test passwords.
"""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))

import json
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import hal_server as hs

tmp = Path(tempfile.mkdtemp())
hs.SETTINGS_FILE = tmp / "hal_settings.json"
hs.STATE_FILE = tmp / "hal_state.json"
PW6 = "fake-test-password"
TOK5 = "fake-test-token"


class PH6(BaseHTTPRequestHandler):
    """Fake Pi-hole 6: one session at a time."""
    sid, logins, blocking, timer, calls = "", 0, True, None, []

    def log_message(self, *a):
        pass

    def _j(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _authed(self):
        return PH6.sid and self.headers.get("X-FTL-SID") == PH6.sid

    def do_GET(self):
        PH6.calls.append(("GET", self.path))
        if self.path == "/api/auth":
            return self._j(401, {"session": {"valid": False}})
        if not self._authed():
            return self._j(401, {"error": {"key": "unauthorized"}})
        if self.path == "/api/stats/summary":
            return self._j(200, {"queries": {"total": 84213, "blocked": 18204, "percent_blocked": 21.62},
                                 "clients": {"active": 14}, "gravity": {"domains_being_blocked": 412806, "last_update": int(time.time()) - 86400}})
        if self.path == "/api/dns/blocking":
            return self._j(200, {"blocking": "enabled" if PH6.blocking else "disabled", "timer": PH6.timer})
        if self.path.startswith("/api/stats/top_domains"):
            return self._j(200, {"domains": [{"domain": "ads.x.com", "count": 50}]})
        if self.path.startswith("/api/stats/top_clients"):
            return self._j(200, {"clients": [{"name": "", "ip": "192.168.68.20", "count": 900}]})
        self._j(404, {})

    def do_POST(self):
        b = self._body()
        PH6.calls.append(("POST", self.path))
        if self.path == "/api/auth":
            if b.get("password") == PW6:
                PH6.logins += 1
                PH6.sid = f"sid{PH6.logins}"
                return self._j(200, {"session": {"valid": True, "sid": PH6.sid, "validity": 1800}})
            return self._j(401, {"session": {"valid": False}})
        if not self._authed():
            return self._j(401, {})
        if self.path == "/api/dns/blocking":
            PH6.blocking, PH6.timer = b["blocking"], b.get("timer")
            return self._j(200, {"blocking": "enabled" if PH6.blocking else "disabled", "timer": PH6.timer})
        self._j(404, {})

    def do_DELETE(self):
        PH6.calls.append(("DELETE", self.path))
        if self._authed():
            PH6.sid = ""
        self.send_response(204)
        self.end_headers()


class PH5(BaseHTTPRequestHandler):
    status, calls = "enabled", []

    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query, keep_blank_values=True)
        PH5.calls.append(self.path)
        if u.path == "/api/auth":
            self.send_response(404)
            self.end_headers()
            return
        if "versions" in q:
            body = {"core_current": "v5.18"}
        elif q.get("auth", [""])[0] != TOK5:
            body = []
        elif "summaryRaw" in q:
            body = {"dns_queries_today": 1000, "ads_blocked_today": 250, "ads_percentage_today": 25.0, "unique_clients": 6,
                    "domains_being_blocked": 99000, "status": PH5.status, "gravity_last_updated": {"absolute": int(time.time()) - 20 * 86400}}
        elif "topItems" in q:
            body = {"top_queries": {}, "top_ads": {"ads.y.com": 77}}
        elif "topClients" in q:
            body = {"top_sources": {"tv|192.168.68.30": 400}}
        elif "disable" in q:
            PH5.status = "disabled"
            body = {"status": "disabled"}
        elif "enable" in q:
            PH5.status = "enabled"
            body = {"status": "enabled"}
        else:
            body = []
        b = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b)


def serve(h):
    s = ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, f"http://127.0.0.1:{s.server_address[1]}"


s6, base6 = serve(PH6)
s5, base5 = serve(PH5)

# ---- addresses ----
assert hs.pihole_base("192.168.68.53") == "http://192.168.68.53"
assert hs.pihole_base("http://pi.hole/admin/") == "http://pi.hole"
assert hs.pihole_base("https://10.0.0.2:8443/x") == "https://10.0.0.2:8443" and hs.pihole_base("") == ""
for bad in ("ftp://x", "http://u:p@x", "http://a b"):
    try:
        hs.pihole_base(bad)
        raise AssertionError(bad)
    except ValueError:
        pass
assert hs.pihole_detect(base6) == 6 and hs.pihole_detect(base5) == 5 and hs.pihole_detect("http://127.0.0.1:1", 1) == 0
print("detect ok")

# ---- Pi-hole 6 ----
p = hs.PiHole(base6, PW6, 6)
sm = p.summary()
assert sm["blocked"] == 18204 and sm["percent"] == 21.6 and sm["clients"] == 14 and sm["blocking"] is True
assert sm["top_blocked"] == [["ads.x.com", 50]] and sm["top_clients"] == [["192.168.68.20", 900]]
p.summary()
assert PH6.logins == 1                                     # one session, reused
PH6.sid = "expired"                                        # Pi-hole forgot the session: sign in again, once
p.summary()
assert PH6.logins == 2
p.set_blocking(False, 300)
assert PH6.blocking is False and PH6.timer == 300
p.set_blocking(True)
assert PH6.blocking is True and PH6.timer is None
p.logout()
assert ("DELETE", "/api/auth") in PH6.calls and p.sid == ""
try:
    hs.PiHole(base6, "wrong", 6).summary()
    raise AssertionError("bad password")
except hs.PiHoleAuthError as e:
    assert "didn't accept" in str(e)
print("v6 ok")

# ---- Pi-hole 5 ----
p5 = hs.PiHole(base5, TOK5, 5)
sm = p5.summary()
assert sm["blocked"] == 250 and sm["top_blocked"] == [["ads.y.com", 77]] and sm["top_clients"] == [["tv", 400]]
p5.set_blocking(False, 1800)
assert PH5.status == "disabled" and any("disable=1800" in c for c in PH5.calls)
p5.set_blocking(True)
assert PH5.status == "enabled"
try:
    hs.PiHole(base5, "", 5).summary()
    raise AssertionError("no token")
except hs.PiHoleAuthError:
    pass
print("v5 ok")

# ---- HAL ----
events = []
hal = hs.Hal(settings=dict(hs.DEFAULT_SETTINGS), monitor=hs.DemoMonitor(), start_threads=False)
hal.add_event = lambda *a, **k: events.append(a)
hs.dns_servers = lambda: []
hal.check_pihole()
assert hal.ph == {} and not any(x["id"] == "pihole" for x in hal.status()["systems"])   # none found: nothing shown
hal.settings["pihole_url"] = base6                                                       # no password yet
hal.check_pihole()
v = hal.pihole_view()
assert v["found"] and v["need_password"] and not v["ok"]
assert [i["key"] for i in hal.compute_issues({}, []) if i["key"].startswith("pihole")] == ["pihole_pw"]
assert any(x["id"] == "pihole" and x["detail"] == "Needs its password" for x in hal.status()["systems"])

# settings API: password stored (encrypted on Windows), blank keeps it
r = hs.pihole_settings(hal, {"url": base6, "password": PW6})
assert r["ok"] and hal.settings["pihole_password"]
time.sleep(0.5)
hal.check_pihole()
v = hal.pihole_view()
assert v["ok"] and v["blocked"] == 18204 and v["version"] == 6 and v["host"] == "127.0.0.1"
assert any(x["id"] == "pihole" and x["detail"] == "18,204 blocked today · 22%" for x in hal.status()["systems"])
assert hs.pihole_settings(hal, {"action": "test", "url": base6})["ok"]               # saved password used
assert not hs.pihole_settings(hal, {"action": "test", "url": base6, "password": "nope"})["ok"]
assert "Pi-hole 5" in hs.pihole_settings(hal, {"action": "test", "url": base5, "password": TOK5})["message"]
try:
    hs.pihole_settings(hal, {"url": base6, "password": "a\nb"})
    raise AssertionError("newline")
except ValueError:
    pass

# pause / resume from the phone (run_action does nothing in demo mode: swap the monitor)
hal.monitor = type("RealishMonitor", (hs.DemoMonitor,), {})()
r = hs.run_action(hal, "pihole_pause", {"minutes": 5})
assert r["ok"] and PH6.blocking is False and PH6.timer == 300, r
time.sleep(0.4)
hal.check_pihole()
assert hal.pihole_view()["timer"] and "Paused" in [x for x in hal.status()["systems"] if x["id"] == "pihole"][0]["detail"]
assert not [i for i in hal.compute_issues({}, []) if i["key"] == "pihole_off"]        # a timed pause isn't an alert
try:
    hs.run_action(hal, "pihole_pause", {"minutes": 999})
    raise AssertionError("minutes")
except ValueError:
    pass
PH6.timer = None                                                    # turned off for good on the Pi
hal.check_pihole()
assert [i["key"] for i in hal.compute_issues({}, []) if i["key"].startswith("pihole")] == ["pihole_off"]
assert hs.run_action(hal, "pihole_resume", {})["ok"] and PH6.blocking is True

# Pi-hole goes away: critical after two missed checks
s6.shutdown()
s6.server_close()
hal.check_pihole()
assert hal.ph["fails"] == 1 and not [i for i in hal.compute_issues({}, []) if i["key"] == "pihole"]
hal.check_pihole()
iss = [i for i in hal.compute_issues({}, []) if i["key"] == "pihole"]
assert iss and iss[0]["level"] == "critical" and any(e[1] == "Pi-hole isn't answering" for e in events)
assert [x for x in hal.status()["systems"] if x["id"] == "pihole"][0]["detail"] == "Not answering"

# the general settings page can't change the Pi-hole fields
new = hs.validate_settings({"pihole_url": "http://elsewhere", "pihole_password": "x", "name": "Dave"}, hal.settings)
assert new["pihole_url"] == base6 and new["pihole_password"] == hal.settings["pihole_password"]
print("hal ok")

# ---- demo has a Pi-hole for screenshots ----
d = hs.make_demo_hal()
assert any(x["id"] == "pihole" for x in d.status()["systems"]) and d.status()["pihole"]["blocked"] == 18204
s5.shutdown()
print("\nALL PI-HOLE TESTS PASSED")
