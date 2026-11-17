"""Phone app Jellyfin tab: endpoints against a fake Jellyfin server."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))
import json, tempfile, threading, urllib.request, urllib.error, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import mobile_server as m

KEY = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
SHOW = "aaaaaaaa1111"
UID = "bbbbbbbb2222"
PNG = b"\x89PNG\r\n\x1a\nfakepng"
calls = []


def ep(season, num, virtual=False, aired="2020-01-01", played=False):
    return {"Id": f"e{season}{num:02d}xxxxxx", "Type": "Episode", "Name": f"Ep {num}", "SeriesName": "Harbor Lights",
            "SeriesId": SHOW, "ParentIndexNumber": season, "IndexNumber": num, "PremiereDate": aired + "T00:00:00Z",
            "LocationType": "Virtual" if virtual else "FileSystem", "UserData": {"Played": played}, "RunTimeTicks": 3e10}


EPISODES = [ep(1, n, played=True) for n in range(1, 4)] + [ep(2, 1), ep(2, 2), ep(2, 4), ep(2, 5, virtual=True),
                                                          ep(2, 6, virtual=True, aired="2999-01-01")]
MOVIES = [
    {"Id": "a1aaaaaa", "Type": "Movie", "Name": "The Long Road", "ProductionYear": 2019, "Height": 1080,
     "MediaSources": [{"Size": 9e9, "MediaStreams": [{"Type": "Video", "Height": 1080}]}], "UserData": {"Played": True}},
    {"Id": "a2aaaaaa", "Type": "Movie", "Name": "Red Canyon", "ProductionYear": 2001, "Height": 720},
    {"Id": "a3aaaaaa", "Type": "Movie", "Name": "Greenhouse", "ProductionYear": 1999, "Height": 480},
]


class FakeJf(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def reply(self, obj, ctype="application/json"):
        b = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(200); self.send_header("Content-Type", ctype); self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        calls.append(("POST", self.path, self.headers.get("X-Emby-Token")))
        if self.headers.get("X-Emby-Token") != KEY:
            self.send_response(401); self.end_headers(); return
        self.send_response(204); self.end_headers()

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        qs = urllib.parse.parse_qs(u.query)
        calls.append(("GET", self.path, self.headers.get("X-Emby-Token")))
        if self.headers.get("X-Emby-Token") != KEY:
            self.send_response(401); self.end_headers(); self.wfile.write(b"bad key"); return
        p = u.path
        if p == "/System/Info":
            return self.reply({"ServerName": "HAL9000", "Version": "10.10.3"})
        if p == "/Users":
            return self.reply([{"Id": "x", "Policy": {}}, {"Id": UID, "Policy": {"IsAdministrator": True}}])
        if p == "/Sessions":
            return self.reply([
                {"Id": "idle1"},
                {"Id": "sess1", "UserName": "dad", "DeviceName": "Living Room TV", "Client": "Jellyfin Android TV",
                 "NowPlayingItem": EPISODES[3] | {"RunTimeTicks": 36000000000},
                 "PlayState": {"PositionTicks": 9000000000, "IsPaused": False, "PlayMethod": "Transcode"}}])
        if p == "/Items/Counts":
            return self.reply({"MovieCount": 3, "SeriesCount": 1, "EpisodeCount": 6})
        if p == f"/Users/{UID}/Items":
            types = qs.get("IncludeItemTypes", [""])[0]
            if "SearchTerm" in qs:
                term = qs["SearchTerm"][0].lower()
                pool = MOVIES + [{"Id": SHOW, "Type": "Series", "Name": "Harbor Lights", "RecursiveItemCount": 6}]
                return self.reply({"Items": [x for x in pool if term in x["Name"].lower()]})
            if types == "Episode":
                start = int(qs["StartIndex"][0])
                return self.reply({"Items": EPISODES[start:], "TotalRecordCount": len(EPISODES)})
            if types == "Movie":
                return self.reply({"Items": MOVIES})
            if types == "Movie,Episode":
                return self.reply({"Items": [MOVIES[0], EPISODES[0]]})
        if p == f"/Users/{UID}/Items/{SHOW}":
            return self.reply({"Name": "Harbor Lights", "ProductionYear": 2021, "Overview": "Boats.", "Genres": ["Drama"]})
        if p == f"/Shows/{SHOW}/Episodes":
            return self.reply({"Items": EPISODES})
        if p == "/Shows/NextUp":
            return self.reply({"Items": [EPISODES[3]]})
        if p.startswith("/Items/") and p.endswith("/Images/Primary"):
            return self.reply(PNG, "image/png")
        self.send_response(404); self.end_headers()


def serve(h):
    s = ThreadingHTTPServer(("127.0.0.1", 0), h); threading.Thread(target=s.serve_forever, daemon=True).start(); return s


jfsrv = serve(FakeJf)
JF_URL = f"http://127.0.0.1:{jfsrv.server_address[1]}"
tmp = Path(tempfile.mkdtemp())
cfg = {"sab_url": "127.0.0.1:1", "sab_api_key": "plainsabkey123", "indexers": [],
       "media_server_type": "Plex", "media_server_url": JF_URL, "media_server_token": KEY}
(tmp / "nzb_manager_config.json").write_text(json.dumps(cfg))
m.CONFIG_FILE = tmp / "nzb_manager_config.json"
m.SETTINGS_FILE = tmp / "mobile_settings.json"
m._cfg_state.update(mtime=None, data=None)
m._token_bytes = b"testtoken"

assert m.normalize_jellyfin_url("192.168.1.5") == "http://192.168.1.5:8096"
assert m.normalize_jellyfin_url("https://jf.example.com/") == "https://jf.example.com"
assert m.normalize_jellyfin_url("http://h:9000/x?y#z") == "http://h:9000/x"

app = serve(m.MobileHandler)
base = f"http://127.0.0.1:{app.server_address[1]}"


def api(path, body=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Mobile-Token": "testtoken", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            ct = r.headers.get("Content-Type", "")
            data = r.read()
            return r.status, (json.loads(data) if "json" in ct else data)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# Desktop set to Plex -> Jellyfin not configured.
st, d = api("/api/jellyfin")
assert st == 200 and d == {"configured": False}, d
assert api("/api/jellyfin/search?q=road")[0] == 503

# Desktop set to Jellyfin -> used.
cfg["media_server_type"] = "Jellyfin"
(tmp / "nzb_manager_config.json").write_text(json.dumps(cfg))
st, d = api("/api/jellyfin")
assert st == 200 and d["online"] and d["source"] == "desktop" and d["server"] == "HAL9000" and d["version"] == "10.10.3", d
assert d["counts"] == {"movies": 3, "shows": 1, "episodes": 6}
s0 = d["sessions"]
assert len(s0) == 1 and s0[0]["title"] == "Harbor Lights" and s0[0]["sub"] == "S02E01 · Ep 1", s0
assert s0[0]["pct"] == 25.0 and s0[0]["method"] == "Transcoding" and s0[0]["poster"] == "j:" + SHOW
assert [x["title"] for x in d["recent"]] == ["The Long Road", "Ep 1"] and d["recent"][1]["show_id"] == SHOW
assert KEY not in json.dumps(d)

# Playback control and scan.
assert api("/api/jellyfin/control", {"session": "sess1", "action": "playpause"})[0] == 200
assert calls[-1][:2] == ("POST", "/Sessions/sess1/Playing/PlayPause") and calls[-1][2] == KEY
assert api("/api/jellyfin/control", {"session": "sess1", "action": "shutdown"})[0] == 400
assert api("/api/jellyfin/control", {"session": "../System", "action": "stop"})[0] == 400
assert api("/api/jellyfin/scan", {})[0] == 200 and calls[-1][1] == "/Library/Refresh"

# Search with quality.
st, d = api("/api/jellyfin/search?q=road")
assert st == 200 and d["items"][0]["quality"] == "1080p" and d["items"][0]["size"] == 9e9 and d["items"][0]["watched"]
st, d = api("/api/jellyfin/search?q=harbor")
assert d["items"][0]["type"] == "Series" and d["items"][0]["episodes"] == 6

# Show: gap E03 + aired virtual E05 are missing; unaired E06 is not; virtual eps aren't listed as owned.
st, d = api("/api/jellyfin/show?id=" + SHOW)
assert st == 200 and d["title"] == "Harbor Lights" and d["episodes"] == 6 and d["watched"] == 3, d
s2 = [x for x in d["seasons"] if x["season"] == 2][0]
assert s2["missing"] == [3, 5] and [e["episode"] for e in s2["episodes"]] == [1, 2, 4], s2
assert d["next"] == {"season": 2, "episode": 1, "title": "Ep 1"}
assert api("/api/jellyfin/show?id=../../etc")[0] == 400

st, d = api("/api/jellyfin/missing")
assert st == 200 and d["missing"] == [{"show": "Harbor Lights", "show_id": SHOW, "season": 2, "missing": [3, 5]}], d

st, d = api("/api/jellyfin/upgrades")
assert [(x["title"], x["quality"]) for x in d["movies"]] == [("Greenhouse", "SD"), ("Red Canyon", "720p")], d

st, body = api("/api/jellyfin/art?id=a1aaaaaa")
assert st == 200 and body == PNG
assert api("/api/jellyfin/art?id=http://evil")[0] == 400

# Connect from the phone: validation, then a phone connection overrides the desktop one.
assert api("/api/jellyfin/connect", {"url": JF_URL, "key": "short"})[0] == 400
st, d = api("/api/jellyfin/connect", {"url": JF_URL, "key": "f" * 32})
assert st == 502 and "ffffffff" not in json.dumps(d), d                      # wrong key: refused, key not echoed
assert api("/api/jellyfin/connect", {"url": "http://8.8.8.8:8096", "key": KEY})[0] == 400   # public address refused
st, d = api("/api/jellyfin/connect", {"url": JF_URL, "key": KEY})
assert st == 200 and d["source"] == "phone" and d["online"], d
saved = json.loads(m.SETTINGS_FILE.read_text())
assert saved["jellyfin_url"] == JF_URL
cfg["media_server_url"] = "http://127.0.0.1:1"
(tmp / "nzb_manager_config.json").write_text(json.dumps(cfg))
assert api("/api/jellyfin")[1]["online"]                                     # phone connection wins
st, d = api("/api/jellyfin/forget", {})
assert d["source"] == "desktop" and not d["online"]                          # back to the desktop one (now unreachable)
assert "error" in d

# Redaction: the key never appears in an error.
assert KEY not in m.redact(f"failed with {KEY}")

# Page wiring.
for needle in ('id="tabjellyfin"', 'id="jellyfin"', "refreshJellyfin", "/api/jellyfin/art?id=", "data-jact", "openJfShow"):
    assert needle in m.PAGE, needle

app.shutdown(); jfsrv.shutdown()
print("jellyfin OK")
