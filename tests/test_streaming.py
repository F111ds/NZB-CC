"""Usenet streaming through AltMount: Stream on a result, the job, Play on TV, telling Usenet from the PC.

Uses a fake AltMount on 127.0.0.1 (its /live and /api/nzb/streams) and fake Kodi / indexer functions.
"""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))

import hashlib
import json
import re
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import mobile_server as m

tmp = Path(tempfile.mkdtemp())
m.SETTINGS_FILE = tmp / "mobile_settings.json"
m.STREAMS_FILE = tmp / "streamed.json"
KEY = "altmountapikey1234567890abcdef12"

# ---- release names ----
assert m.release_title_year("Glass.Meridian.2025.1080p.WEB-DL.DDP5.1") == ("Glass Meridian", "2025")
assert m.release_title_year("Stop.Making.Sense.1984.1080p.BluRay.FLAC.5.1.x264-HANDJOB.mkv") == ("Stop Making Sense", "1984")
assert m.release_title_year("Blade Runner 2049 (2017) 2160p") == ("Blade Runner 2049", "2017")
assert m.release_title_year("Night.Harbor.S02E06.1080p.WEB")[0] == "Night Harbor"
print("names ok")

# ---- what AltMount's failures mean ----
assert m.stream_problem(404, "Stremio endpoint: Stremio integration is disabled in configuration")[0] == "setup"
assert m.stream_problem(401, "Invalid X-Api-Key")[0] == "setup"
assert m.stream_problem(500, "NZB processing failed: archive is compressed (not stored)")[0] == "archive"
assert m.stream_problem(500, "RAR archive is encrypted")[0] == "archive"
assert m.stream_problem(500, "too many missing segments")[0] == "missing"
assert m.stream_problem(408, "NZB processing timed out")[0] == "slow"
assert m.stream_problem(400, "Episode not specified")[0] == "pack"
assert m.altmount_error_text(500, {"success": False, "error": {"code": "X", "message": "NZB processing failed", "details": "archive is compressed"}}) \
    == "NZB processing failed: archive is compressed"
print("problems ok")

# ---- not set up: no Stream buttons, a clear message ----
assert not m.altmount_configured()
try:
    m.stream_start("whatever")
    raise AssertionError("should refuse")
except ValueError as exc:
    assert "Connections" in str(exc)


# ---- a fake AltMount ----
class FakeAltMount(BaseHTTPRequestHandler):
    mode = "ok"
    seen = []

    def log_message(self, *a):
        pass

    def _json(self, code, data):
        raw = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/live":
            self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
        else:
            self._json(404, {})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        FakeAltMount.seen.append((self.path, self.headers.get("X-Api-Key"), body))
        if self.headers.get("X-Api-Key") != KEY:
            return self._json(401, {"success": False, "error": {"code": "UNAUTHORIZED", "message": "Invalid X-Api-Key"}})
        if FakeAltMount.mode == "off":
            return self._json(404, {"success": False, "error": {"code": "NOT_FOUND", "message": "Stremio endpoint not found",
                                                                "details": "Stremio integration is disabled in configuration"}})
        if b'name="file"' not in body:
            return self._json(400, {"success": False, "error": {"code": "BAD_REQUEST", "message": "No file provided"}})
        if FakeAltMount.mode == "compressed":
            return self._json(500, {"success": False, "error": {"code": "INTERNAL", "message": "NZB processing failed",
                                                                "details": "archive is compressed (not stored)"}})
        host = self.headers.get("Host")
        dk = hashlib.sha256(KEY.encode()).hexdigest()
        url = lambda p: f"http://{host}/api/files/stream?path={urllib.parse.quote(p, safe='')}&download_key={dk}"
        return self._json(200, {"streams": [
            {"url": url("/stremio/Glass.Meridian.2025.1080p/Sample/glass-sample.mkv"), "name": "glass-sample.mkv"},
            {"url": url("/stremio/Glass.Meridian.2025.1080p/Glass.Meridian.2025.1080p.mkv"), "name": "Glass.Meridian.2025.1080p.mkv"}],
            "_queue_item_id": 7, "_queue_status": "streamable"})


srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeAltMount)
threading.Thread(target=srv.serve_forever, daemon=True).start()
AM = f"http://localhost:{srv.server_address[1]}"

# ---- Connections: Test tells you what's missing ----
assert m.altmount_test("", KEY)["ok"] is False
assert "isn't answering" in m.altmount_test("http://127.0.0.1:1", KEY)["message"]
assert "API key" in m.altmount_test(AM, "")["message"]
assert "didn't accept" in m.altmount_test(AM, "wrongkey12345678")["message"]
FakeAltMount.mode = "off"
assert "Stremio Integration is off" in m.altmount_test(AM, KEY)["message"]
FakeAltMount.mode = "ok"
r = m.altmount_test(AM, KEY)
assert r["ok"] is True, r
print("test button ok")

# ---- saved like the other phone settings (key encrypted on Windows, never sent back) ----
m.update_settings(lambda st: st.update(altmount_url=AM, altmount_key=m._dpapi_protect(KEY)))
assert m.altmount_configured() and m.altmount_config() == (AM, KEY)

# ---- the stream address: the key is AltMount's download key, the path last, an address the viewer can reach ----
u = m.altmount_stream_url("/stremio/A B/A B.mkv", "192.168.68.10")
assert u.startswith(f"http://192.168.68.10:{srv.server_address[1]}/api/files/stream?download_key=" + hashlib.sha256(KEY.encode()).hexdigest()), u
assert u.endswith(".mkv") and KEY not in u and "%2Fstremio%2FA%20B%2FA%20B.mkv" in u, u
assert m.altmount_stream_url("/x.mkv").startswith("http://localhost:")        # nobody to swap for: as it is
assert m.is_stream_file(u) and not m.is_stream_file("smb://HAL9000/Movies/Short Circuit (1986)/Short Circuit.mkv")
assert not m.is_stream_file("D:\\Movies\\Short Circuit.mkv") and not m.is_stream_file("")

# ---- the PC's address for the TV: the one on the TV's network, even with a VPN and virtual adapters ----
ips = ["192.168.35.1", "192.168.235.1", "10.5.0.2", "10.100.0.2", "192.168.68.60"]
assert m._lan_ip_towards("192.168.68.62", ips) == "192.168.68.60"
# the real lookup of Kodi's address (the connection settings keep it as {"kodi": {"url": ...}})
_gc = m.get_config
m.get_config = lambda: {"kodi": {"url": "http://192.168.68.62:8080", "username": "", "password": ""}}
assert m._kodi_host() == "192.168.68.62"
m.get_config = lambda: {"kodi": {"url": ""}}
assert m._kodi_host() == ""
m.get_config = _gc
_li = m._local_ipv4s
m._local_ipv4s = lambda: ["192.168.35.1", "10.5.0.2", "10.100.0.2", "192.168.68.60"]
assert m._home_lan_ip() == "192.168.68.60"
m._local_ipv4s = _li
print("lan address ok")

# ---- Stream: the job, then Play on TV ----
RESULT = {"title": "Glass.Meridian.2025.1080p.WEB-DL.DDP5.1", "size_bytes": 7_800_000_000, "category": "2000"}
m.get_cached_result = lambda rid: dict(RESULT) if rid == "res1" else None
m.download_indexer_nzb = lambda item: (b"<?xml version='1.0'?><nzb></nzb>", "NZBGeek")


def wait_job(job_id, secs=10):
    end = time.time() + secs
    while time.time() < end:
        j = m.stream_status(job_id)
        if j["state"] != "working":
            return j
        time.sleep(0.05)
    raise AssertionError("job never finished")


try:
    m.stream_start("expired")
    raise AssertionError("should refuse")
except ValueError as exc:
    assert "expired" in str(exc)

job = m.stream_start("res1")
assert job["state"] == "working" and job["title"] == "Glass Meridian" and job["year"] == "2025" and job["res"] == "1080p"
done = wait_job(job["id"])
assert done["state"] == "ready", done
path, key_sent, body = FakeAltMount.seen[-1]
assert path == "/api/nzb/streams" and key_sent == KEY and b"Glass.Meridian.2025.1080p.WEB-DL.DDP5.1.nzb" in body
saved = m.load_streams()
assert len(saved) == 1 and saved[0]["id"] == done["stream_id"] and saved[0]["title"] == "Glass Meridian"
assert saved[0]["path"].endswith("Glass.Meridian.2025.1080p.mkv"), saved[0]       # not the sample
view = m.streams_view()
assert view["configured"] and view["items"][0]["title"] == "Glass Meridian" and "path" not in view["items"][0]
print("stream job ok")

calls = []
m.kodi_call = lambda method, params=None, timeout=6: calls.append((method, params)) or "pong"
m._tv_wait_for_kodi = lambda wait: None
m._kodi_host = lambda: "192.168.68.62"
m._lan_ip_towards = lambda host: "192.168.68.10" if host == "192.168.68.62" else ""
m.tv_configured = lambda settings=None: False
_real_kodi = m.kodi_call
m.kodi_call = lambda method, params=None, timeout=6: calls.append((method, params)) or ([{"playerid": 1, "type": "video"}] if method == "Player.GetActivePlayers" else "pong")
r = m.stream_to_tv(done["stream_id"])
assert r["ok"] and r["title"] == "Glass Meridian" and not r["started_tv"]
opened = [p for meth, p in calls if meth == "Player.Open"][0]["item"]["file"]
assert opened.startswith("http://192.168.68.10:") and opened.endswith("Glass.Meridian.2025.1080p.mkv") and KEY not in opened, opened
assert m.load_streams()[0].get("played")
ph = m.stream_phone_url(done["stream_id"], "hal9000:8765")
assert ph["url"].startswith(f"http://hal9000:{srv.server_address[1]}/api/files/stream?"), ph
# Kodi says OK but never starts: say so, with the address it was given
m.kodi_call = lambda method, params=None, timeout=6: [] if method == "Player.GetActivePlayers" else "pong"
_sleep = m.time.sleep
m.time.sleep = lambda s: None
try:
    m.stream_to_tv(done["stream_id"])
    raise AssertionError("should report it")
except m.UpstreamError as exc:
    assert "couldn't open the stream from 192.168.68.10:" in str(exc), exc
m.time.sleep = _sleep
try:
    m.stream_to_tv("nosuchstream1")
    raise AssertionError("should refuse")
except ValueError:
    pass
print("play on tv ok")

# ---- Kodi playing it: source "usenet", with the title we kept; a file from the PC is "pc" ----
def fake_kodi(file, label):
    def call(method, params=None, timeout=6):
        if method == "Player.GetActivePlayers":
            return [{"playerid": 1, "type": "video"}]
        if method == "Player.GetItem":
            return {"item": {"label": label, "title": "", "file": file, "type": "unknown"}}
        if method == "Player.GetProperties":
            return {"percentage": 23.0, "time": {"hours": 0, "minutes": 30, "seconds": 20},
                    "totaltime": {"hours": 2, "minutes": 11, "seconds": 0}, "speed": 1}
        return {}
    return call


m.kodi_call = fake_kodi(opened, "Glass.Meridian.2025.1080p.mkv")
p = m._kodi_now_playing()
assert p["source"] == "usenet" and p["title"] == "Glass Meridian" and p["sub"] == "2025", p
m.kodi_call = fake_kodi("smb://HAL9000/Movies/Short Circuit (1986)/Short Circuit.mkv", "Short Circuit")
p = m._kodi_now_playing()
assert p["source"] == "pc" and p["title"] == "Short Circuit", p
# however Kodi reports the address, HAL gets the movie's name (never the address)
raw_path = saved[0]["path"]
variants = [
    opened,                                                                     # as sent
    opened + "|User-Agent=Kodi",                                                # Kodi's header options on the end
    opened.replace(urllib.parse.quote(raw_path, safe=""), raw_path),            # unescaped
    f"http://192.168.68.60:8090/webdav{raw_path}",                              # a WebDAV address
]
for v in variants:
    assert m.is_stream_file(v), v
    assert m.stream_title(v, v) == ("Glass Meridian", "2025"), (v, m.stream_title(v, v))
# not in "Streamed lately" (e.g. started from AltMount's own page): the name from the file, not the address
odd = "http://192.168.68.60:8090/api/files/stream?download_key=abc&path=%2Fcomplete%2FDeadpool.2016.1080p.DSNP.WEB-DL%2FDeadpool.2016.1080p.DSNP.WEB-DL.mkv"
assert m.stream_title(odd, "stream?download_key=abc&path=...") == ("Deadpool", "2016"), m.stream_title(odd, "x")
# no name anywhere in what Kodi reports: the stream last sent to the TV (Glass Meridian, played above)
assert m.stream_title("http://x:8090/api/files/stream?download_key=abc", "stream?download_key=abc") == ("Glass Meridian", "2025")
_ls = m.load_streams
m.load_streams = lambda: []
assert m.stream_title("http://x:8090/api/files/stream?download_key=abc", "http://x:8090/api/files/stream?download_key=abc")[0] == "a stream from Usenet"
m.load_streams = _ls
m.kodi_call = fake_kodi(odd, "stream?download_key=abc&path=%2Fcomplete%2FDeadpool")
p = m._kodi_now_playing()
assert p["source"] == "usenet" and p["title"] == "Deadpool" and p["sub"] == "2016", p
print("source ok")

# ---- a release that can't be streamed: why, so the phone can offer another or Download ----
FakeAltMount.mode = "compressed"
bad = wait_job(m.stream_start("res1")["id"])
assert bad["state"] == "failed" and bad["kind"] == "archive" and "compressed" in bad["message"], bad
FakeAltMount.mode = "off"
bad = wait_job(m.stream_start("res1")["id"])
assert bad["state"] == "failed" and bad["kind"] == "setup" and "Enable Integration" in bad["message"], bad
FakeAltMount.mode = "ok"
m.update_settings(lambda st: st.update(altmount_url="http://127.0.0.1:1"))
bad = wait_job(m.stream_start("res1")["id"])
assert bad["state"] == "failed" and bad["kind"] == "offline" and "running" in bad["message"], bad
m.update_settings(lambda st: st.update(altmount_url=AM))
print("failures ok")

# ---- cancel: the result is dropped ----
slow = threading.Event()
m.download_indexer_nzb = lambda item: (slow.wait(5), (b"<nzb/>", "x"))[1]
j = m.stream_start("res1")
m.stream_cancel(j["id"])
slow.set()
time.sleep(0.3)
assert m.stream_status(j["id"])["state"] == "cancelled"
print("cancel ok")

# ---- the app's page has the new pieces, and HAL says it's a stream ----
import app_page
page = app_page.APP_PAGE
for bit in ("data-stream=", "/api/stream/status?job=", "/api/stream/tv", "STREAMED LATELY", "srcChip(p.source)", "data-ctest=\"altmount\"",
            "streaming from Usenet", "vlc-x-callback://"):
    assert bit in page, bit
assert "streaming from Usenet" in (ROOT / "hal_server.py").read_text(encoding="utf-8")

srv.shutdown()
print("ALL STREAMING TESTS PASSED")
