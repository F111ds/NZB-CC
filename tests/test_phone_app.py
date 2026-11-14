"""Phone app (mobile_server v3): Home and Kodi endpoints against fake SABnzbd + Kodi servers."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))
import json, tempfile, threading, time, urllib.request, urllib.error, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import mobile_server as m

calls = []

class FakeSab(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        mode = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("mode", [""])[0]
        now = int(time.time())
        if mode == "queue":
            d = {"queue": {"paused": False, "status": "Downloading", "kbpersec": "49356", "noofslots_total": 2, "diskspace2": "1843.2",
                 "slots": [{"nzo_id": "SABnzbd_nzo_1", "filename": "Some.Movie.2160p", "status": "Downloading", "percentage": "64",
                            "size": "22.0 GB", "sizeleft": "7.9 GB", "timeleft": "0:02:44"}]}}
        elif mode == "history":
            d = {"history": {"slots": [{"status": "Completed", "completed": now - 60}, {"status": "Completed", "completed": now - 120},
                                       {"status": "Failed", "completed": now - 30}, {"status": "Completed", "completed": now - 86400 * 3}]}}
        else:
            d = {"status": True}
        b = json.dumps(d).encode(); self.send_response(200); self.end_headers(); self.wfile.write(b)

JPEG = b"\xff\xd8\xff\xe0fakejpeg"
class FakeKodi(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        calls.append(("GET", self.path, self.headers.get("Authorization")))
        body = JPEG if self.path.startswith("/image/image%3A%2F%2F") else b"<html>nope</html>"
        if "notimage" in self.path: body = b"<html>nope</html>"
        self.send_response(200); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        calls.append((req["method"], req.get("params"), self.headers.get("Authorization")))
        meth = req["method"]
        res = {
            "Player.GetActivePlayers": [{"playerid": 1, "type": "video"}],
            "Player.GetItem": {"item": {"title": "The Martian", "year": 2015, "type": "movie", "id": 5, "art": {"poster": "image://p5.jpg/"}}},
            "VideoLibrary.GetMovieDetails": {"moviedetails": {"movieid": 5, "title": "The Martian", "year": 2015, "plot": "Stranded.",
                "art": {"poster": "image://p5.jpg/", "fanart": "image://f5.jpg/"}, "genre": ["Sci-Fi"], "mpaa": "Rated PG-13",
                "ratings": {"imdb": {"rating": 8.04, "votes": 900000}, "themoviedb": {"rating": 7.7}}, "imdbnumber": "tt3659388",
                "cast": [{"name": "Matt Damon", "role": "Mark"}] * 12, "director": ["Ridley Scott"]}},
            "Player.GetProperties": {"percentage": 50.0, "time": {"hours": 1, "minutes": 12, "seconds": 40},
                                     "totaltime": {"hours": 2, "minutes": 24, "seconds": 0}, "speed": 1},
            "VideoLibrary.GetMovies": {"limits": {"total": 412}, "movies": [{"movieid": 7, "title": "Arrival", "year": 2016, "runtime": 6960, "playcount": 1, "resume": {"position": 0}}]},
            "VideoLibrary.GetEpisodes": {"limits": {"total": 3001}},
            "VideoLibrary.GetRecentlyAddedMovies": {"movies": [{"movieid": 9, "title": "Oppenheimer", "year": 2023, "runtime": 10800, "playcount": 0, "resume": {"position": 2472.5}, "dateadded": "2026-11-13 21:04:11"}]},
        }.get(meth, "OK")
        b = json.dumps({"jsonrpc": "2.0", "id": 1, "result": res}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(b)

def serve(h):
    s = ThreadingHTTPServer(("127.0.0.1", 0), h); threading.Thread(target=s.serve_forever, daemon=True).start(); return s
sab, kodi = serve(FakeSab), serve(FakeKodi)

tmp = Path(tempfile.mkdtemp())
cfg = {"sab_url": f"127.0.0.1:{sab.server_address[1]}", "sab_api_key": "plainsabkey123",
       "kodi_url": f"127.0.0.1:{kodi.server_address[1]}", "kodi_username": "kodi", "kodi_password": "kodipass99",
       "indexers": [{"name": "Good", "url": "https://good.example", "api_key": "goodkey12345"}]}
(tmp / "nzb_manager_config.json").write_text(json.dumps(cfg))
m.CONFIG_FILE = tmp / "nzb_manager_config.json"
m._cfg_state.update(mtime=None, data=None)
m._token_bytes = b"testtoken"
assert m.normalize_kodi_url("192.168.1.5") == "http://192.168.1.5:8080"
assert m.normalize_kodi_url("http://h:9090/jsonrpc") == "http://h:9090"

app = serve(m.MobileHandler)
base = f"http://127.0.0.1:{app.server_address[1]}"
def api(path, body=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Mobile-Token": "testtoken", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

st, home = api("/api/home")
assert st == 200 and home["sab_ok"], home
assert home["current"]["name"] == "Some.Movie.2160p" and home["total"] == 2
assert home["done_today"] == 2 and home["failed_today"] == 1, home
assert home["kodi"]["online"] and home["kodi"]["playing"]["title"] == "The Martian"
assert home["kodi"]["playing"]["time"] == 4360 and home["kodi"]["playing"]["total"] == 8640
assert "kodipass99" in m._KNOWN_SECRETS and "kodipass99" not in json.dumps(home)

st, k = api("/api/kodi")
assert st == 200 and k["counts"] == {"movies": 412, "episodes": 3001}, k
assert k["recent"][0] == {"id": 9, "title": "Oppenheimer", "year": 2023, "runtime": 10800, "resume": 2472, "watched": False, "added": "2026-11-13 21:04:11", "poster": ""}
with urllib.request.urlopen(base + "/manifest.webmanifest", timeout=5) as r:
    man = json.loads(r.read()); assert man["display"] == "standalone" and man["start_url"] == "/"
st, mv = api("/api/kodi/movies"); assert mv["movies"][0]["title"] == "Arrival" and mv["movies"][0]["watched"]

assert home["kodi"]["playing"]["poster"] == "image://p5.jpg/" and home["kodi"]["playing"]["movieid"] == 5
st, d = api("/api/kodi/movie?id=5")
assert st == 200 and d["plot"] == "Stranded." and d["fanart"] == "image://f5.jpg/" and d["mpaa"] == "PG-13", d
assert d["imdb_rating"] == 8.0 and d["imdb_url"] == "https://www.imdb.com/title/tt3659388/" and len(d["cast"]) == 8
assert api("/api/kodi/movie?id=0")[0] == 400
def raw(path):
    req = urllib.request.Request(base + path, headers={"X-Mobile-Token": "testtoken"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r: return r.status, r.headers.get("Content-Type"), r.read()
    except urllib.error.HTTPError as e: return e.code, None, e.read()
st, ct, body = raw("/api/kodi/art?u=" + urllib.parse.quote("image://p5.jpg/"))
assert st == 200 and ct == "image/jpeg" and body == JPEG, (st, ct)
assert calls[-1][1] == "/image/image%3A%2F%2Fp5.jpg%2F" and calls[-1][2].startswith("Basic ")
assert raw("/api/kodi/art?u=" + urllib.parse.quote("http://evil.example/x.jpg"))[0] == 400   # only Kodi artwork refs
assert raw("/api/kodi/art?u=" + urllib.parse.quote("image://notimage/"))[0] != 200          # non-image refused
assert raw("/api/kodi/art?u=" + urllib.parse.quote("image://p5.jpg/"))[0] == 200
req = urllib.request.Request(base + "/api/kodi/art?u=x"); 
try: urllib.request.urlopen(req, timeout=5); raise AssertionError("art without code")
except urllib.error.HTTPError as e: assert e.code == 401
calls.clear()
assert api("/api/kodi/play", {"movieid": 9, "resume": True})[0] == 200
assert calls[-1][:2] == ("Player.Open", {"item": {"movieid": 9}, "options": {"resume": True}})
assert calls[-1][2].startswith("Basic ")
assert api("/api/kodi/control", {"action": "playpause", "playerid": 1})[0] == 200 and calls[-1][0] == "Player.PlayPause"
assert api("/api/kodi/scan", {})[0] == 200 and calls[-1][0] == "VideoLibrary.Scan"
assert api("/api/kodi/play", {"movieid": "x"})[0] == 400
assert api("/api/kodi/control", {"action": "shutdown", "playerid": 1})[0] == 400

# Kodi off: home still works, Kodi tab says offline
kodi.shutdown(); kodi.server_close()
st, home = api("/api/home"); assert st == 200 and home["sab_ok"] and home["kodi"] == {"configured": True, "online": False, "error": "Can't reach Kodi — is it on?"}, home["kodi"]
st, k = api("/api/kodi"); assert st == 200 and not k["online"]
# Kodi not configured
cfg["kodi_url"] = ""; time.sleep(0.05); (tmp / "nzb_manager_config.json").write_text(json.dumps(cfg))
import os; os.utime(m.CONFIG_FILE, None); m._cfg_state.update(mtime=None, data=None)
assert api("/api/kodi")[1] == {"configured": False}
# SAB off: home reports it without failing
sab.shutdown(); sab.server_close()
st, home = api("/api/home"); assert st == 200 and not home["sab_ok"] and home["sab_error"], home
print("PHONE APP TESTS PASSED")
