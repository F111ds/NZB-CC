"""Watch on the phone: home-only check, Jellyfin matching, stream sessions, the /watch relay and saved positions.

Uses fake Kodi / Jellyfin functions, and a fake Jellyfin HTTP server on 127.0.0.1 for the relay.
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

import ast
from collections import Counter

import mobile_server as m

# Two functions with the same name: the later one silently replaces the earlier (this bit us once).
_defs = Counter(n.name for n in ast.parse((ROOT / "mobile_server.py").read_text(encoding="utf-8")).body if isinstance(n, ast.FunctionDef))
assert not [k for k, v in _defs.items() if v > 1], [k for k, v in _defs.items() if v > 1]

tmp = Path(tempfile.mkdtemp())
m.WATCH_FILE = tmp / "watch_positions.json"

# ---- home only ----
for ip, home in (("192.168.68.20", True), ("10.0.0.5", True), ("127.0.0.1", True), ("::ffff:192.168.1.9", True),
                 ("100.101.102.103", False), ("fd7a:115c:a1e0::1", False), ("8.8.8.8", False), ("junk", False)):
    assert m.client_at_home(ip) is home, ip
print("home ok")

# ---- playlists never carry the key ----
pl = ("#EXTM3U\nmain.m3u8?DeviceId=x&api_key=SECRET123&PlaySessionId=p\n"
      "abc/Subtitles/3/subtitles.m3u8?SegmentLength=3&api_key=SECRET123\n/Videos/JF1/hls1/main/0.mp4?ApiKey=SECRET123\n")
out = m._strip_jf_keys(pl, "JF1", "SID")
assert "SECRET123" not in out, out
assert "main.m3u8?DeviceId=x&PlaySessionId=p" in out and "subtitles.m3u8?SegmentLength=3\n" in out
assert "/watch/SID/hls1/main/0.mp4\n" in out
print("playlist ok")

# ---- positions ----
k = "movie:7"
assert m.watch_save_position(k, 4360, 7865) is False and m.watch_positions()[k]["pos"] == 4360
assert m.watch_save_position(k, 7800, 7865) is True and k not in m.watch_positions()      # finished: forgotten
m.watch_save_position(k, 10, 7865)
assert k not in m.watch_positions()                                                      # barely started: not kept
print("positions ok")

# ---- matching a Kodi movie / episode in Jellyfin ----
KODI = {
    ("movie", 7): {"title": "Glass Meridian", "year": 2024, "file": "smb://hal/Movies/Glass Meridian (2024)/Glass.Meridian.2024.2160p.mkv",
                   "runtime": 7865, "resume": {"position": 1200}, "plot": "A map.", "mpaa": "Rated PG-13", "playcount": 0},
    ("movie", 8): {"title": "Nowhere", "year": 2001, "file": "/x/nowhere.mkv", "runtime": 100, "resume": {}, "playcount": 1},
    ("episode", 31): {"title": "Tide", "showtitle": "Night Harbor", "season": 2, "episode": 5, "file": "/tv/nh.s02e05.mkv",
                      "runtime": 2700, "resume": {}, "playcount": 0},
}
SRC = {"Id": "MS1", "Path": "Z:\\Movies\\Glass Meridian (2024)\\Glass.Meridian.2024.2160p.mkv", "MediaStreams": [
    {"Type": "Video", "Codec": "hevc", "Height": 2160, "Width": 3840, "VideoRange": "HDR"},
    {"Type": "Audio", "Index": 1, "DisplayTitle": "English - Dolby TrueHD + Atmos 7.1", "IsDefault": True},
    {"Type": "Audio", "Index": 2, "DisplayTitle": "Commentary"},
    {"Type": "Subtitle", "Index": 3, "DisplayTitle": "English - SRT", "IsTextSubtitleStream": True},
    {"Type": "Subtitle", "Index": 4, "DisplayTitle": "English - PGS", "IsTextSubtitleStream": False}]}
calls = []


def fake_kodi(method, params=None, timeout=6):
    calls.append((method, params))
    if method == "VideoLibrary.GetMovieDetails":
        return {"moviedetails": KODI[("movie", params["movieid"])]}
    if method == "VideoLibrary.GetEpisodeDetails":
        return {"episodedetails": KODI[("episode", params["episodeid"])]}
    return "OK"


def fake_jf(path, params=None, **kw):
    if path.endswith("/Items") and params.get("IncludeItemTypes") == "Movie":
        return {"Items": [{"Id": "WRONG", "Name": "Glass Meridian", "ProductionYear": 1990, "MediaSources": [{"Path": "/old.avi"}]},
                          {"Id": "JF1", "Name": "Glass Meridian", "ProductionYear": 2024, "MediaSources": [SRC]}]}
    if path.endswith("/Items") and params.get("IncludeItemTypes") == "Series":
        return {"Items": [{"Id": "S9", "Name": "Night Harbor"}]}
    if path == "/Shows/S9/Episodes":
        return {"Items": [{"Id": "E4", "IndexNumber": 4, "ParentIndexNumber": 2},
                          {"Id": "E5", "IndexNumber": 5, "ParentIndexNumber": 2, "MediaSources": [{"Id": "MS5", "MediaStreams": [{"Type": "Video", "Codec": "mpeg4"}]}]}]}
    return {}


m.kodi_call = fake_kodi
m.kodi_configured = lambda: True
m.jf_request = fake_jf
m.jf_user_id = lambda: "U1"
m.jellyfin_configured = lambda: True
m.jellyfin_conf = lambda: {"url": "http://127.0.0.1:8096", "key": "k" * 32, "source": "desktop"}

it = m.watch_item("movie", 7, "192.168.68.20", "192.168.68.60:8765")
assert it["matched"] and it["home"] and it["poster"] == "j:JF1" and it["direct"] is True
assert it["chips"] == ["4K HDR", "Atmos", "Subs"] and it["mpaa"] == "PG-13" and it["start"] == 1200   # Kodi's resume
assert [a["index"] for a in it["audio"]] == [1, 2] and it["subs"][1]["label"].endswith("(burned in)")
assert it["web"] == "http://192.168.68.60:8096/web/#/details?id=JF1"                               # not localhost
m.watch_save_position("movie:7", 4360, 7865)
assert m.watch_item("movie", 7, "100.70.1.1")["start"] == 4360 and m.watch_item("movie", 7, "100.70.1.1")["home"] is False
assert m.watch_item("movie", 8, "192.168.68.20")["matched"] is False                                 # Jellyfin hasn't got it
ep = m.watch_item("episode", 31, "192.168.68.20")
assert ep["matched"] and ep["poster"] == "j:E5" and ep["direct"] is False and ep["show"] == "Night Harbor"
print("match ok")

# ---- starting a stream ----
try:
    m.watch_start({"kind": "movie", "id": 7}, "100.70.1.1")
    raise AssertionError("away from home")
except ValueError:
    pass
r = m.watch_start({"kind": "movie", "id": 7, "quality": "best"}, "192.168.68.20")
assert r["direct"] and r["url"] == f"/watch/{r['sid']}/master.m3u8"
p = m._watch_sessions[r["sid"]]["params"]
assert p["AllowVideoStreamCopy"] == "true" and p["AudioStreamIndex"] == "1" and p["MediaSourceId"] == "MS1" and "SubtitleStreamIndex" not in p
r2 = m.watch_start({"kind": "movie", "id": 7, "quality": "best", "sub": 4}, "192.168.68.20")     # picture subtitles: burned in
p = m._watch_sessions[r2["sid"]]["params"]
assert r["sid"] not in m._watch_sessions and len(m._watch_sessions) == 1                         # one stream at a time
assert r2["direct"] is False and p["SubtitleMethod"] == "Encode" and p["VideoCodec"] == "h264"
r3 = m.watch_start({"kind": "movie", "id": 7, "quality": "720", "sub": 3, "audio": 2}, "192.168.68.20")
p = m._watch_sessions[r3["sid"]]["params"]
assert p["MaxWidth"] == "1280" and p["SubtitleMethod"] == "Hls" and p["AudioStreamIndex"] == "2"
for bad in ({"kind": "movie", "id": 7, "quality": "8k"}, {"kind": "movie", "id": 7, "sub": 99}, {"kind": "movie", "id": 7, "audio": 9},
            {"kind": "song", "id": 1}, {"kind": "movie", "id": 0}):
    try:
        m.watch_start(bad, "192.168.68.20")
        raise AssertionError(bad)
    except ValueError:
        pass
try:
    m.watch_start({"kind": "movie", "id": 8}, "192.168.68.20")
    raise AssertionError("no match")
except m.UpstreamError:
    pass
print("start ok")

# ---- progress and the TV ----
calls.clear()
assert m.watch_progress({"kind": "movie", "id": 7, "pos": 5000, "dur": 7865, "final": True})["kodi"] is True
assert calls[-1] == ("VideoLibrary.SetMovieDetails", {"movieid": 7, "resume": {"position": 5000, "total": 7865}})
m.watch_progress({"kind": "episode", "id": 31, "pos": 2690, "dur": 2700, "final": True})
assert calls[-1] == ("VideoLibrary.SetEpisodeDetails", {"episodeid": 31, "resume": {"position": 0, "total": 2700}, "playcount": 1})
calls.clear()
m.watch_progress({"kind": "movie", "id": 7, "pos": 5100, "dur": 7865})                        # not final: Kodi left alone
assert calls == [] and m.watch_positions()["movie:7"]["pos"] == 5100
m.watch_to_tv({"kind": "movie", "id": 7, "pos": 5200, "dur": 7865, "sid": r3["sid"]})
assert calls[-1] == ("Player.Open", {"item": {"movieid": 7}, "options": {"resume": True}}) and r3["sid"] not in m._watch_sessions
print("progress ok")


# ---- the relay, over HTTP, against a fake Jellyfin ----
class FakeJF(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        FakeJF.seen.append((self.path, self.headers.get("X-Emby-Token"), self.headers.get("Range")))
        if "master.m3u8" in self.path:
            body = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nmain.m3u8?PlaySessionId=p&api_key=" + b"k" * 32 + b"\n"
            ctype = "application/vnd.apple.mpegurl"
        elif self.path.startswith("/Videos/JF1/hls1/main/0.mp4"):
            body, ctype = b"\x00" * 600_000, "video/mp4"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


jf_srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeJF)
threading.Thread(target=jf_srv.serve_forever, daemon=True).start()
m.jellyfin_conf = lambda: {"url": f"http://127.0.0.1:{jf_srv.server_address[1]}", "key": "k" * 32, "source": "desktop"}
m._token_bytes = b"t" * 43
srv = ThreadingHTTPServer(("127.0.0.1", 0), m.MobileHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"


def get(path, headers=None):
    req = urllib.request.Request(base + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def post(path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"X-Mobile-Token": "t" * 43, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


code, body, hdrs = get("/app/")
assert code == 200 and "media-src 'self' blob:" in hdrs["Content-Security-Policy"]
s = post("/api/watch/start", {"kind": "movie", "id": 7})
code, body, hdrs = get(s["url"])
assert code == 200 and b"k" * 32 not in body and b"main.m3u8?PlaySessionId=p" in body, body
path0, key_sent, _ = FakeJF.seen[-1]
assert path0.startswith("/Videos/JF1/master.m3u8?") and "AllowVideoStreamCopy=true" in path0 and key_sent == "k" * 32
code, body, hdrs = get(s["url"].replace("master.m3u8", "hls1/main/0.mp4?runtimeTicks=0&api_key=evil"), {"Range": "bytes=0-"})
assert code == 200 and len(body) == 600_000 and hdrs["Content-Type"] == "video/mp4"
assert "api_key" not in FakeJF.seen[-1][0] and FakeJF.seen[-1][2] == "bytes=0-"
assert get("/watch/" + "x" * 32 + "/master.m3u8")[0] == 404                          # unknown session
assert get(s["url"].replace("master.m3u8", "../../System/Info"))[0] == 404
assert get(s["url"].replace("master.m3u8", "nothere.ts"))[0] == 404                  # Jellyfin's own 404 passed on
assert post("/api/watch/stop", {"sid": s["sid"]})["ok"] and get(s["url"])[0] == 404  # stopped: gone
code, body, _ = get("/api/watch/item?kind=movie&id=7", {"X-Mobile-Token": "t" * 43})
assert code == 200 and json.loads(body)["title"] == "Glass Meridian"
assert get("/api/watch/item?kind=movie&id=7")[0] == 401
srv.shutdown()
jf_srv.shutdown()
print("relay ok")
print("\nALL WATCH TESTS PASSED")
