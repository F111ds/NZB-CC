"""Sonarr & Radarr on the phone (3.11): found from config.xml, coming up, look up, add, posters."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))
import json, tempfile, threading, urllib.parse
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import mobile_server as m

tmp = Path(tempfile.mkdtemp())
m.SETTINGS_FILE = tmp / "mobile_settings.json"
KEYS = {"radarr": "r" * 32, "sonarr": "s" * 32}
today = date.today()
posted = {}
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 50


def handler(app):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def reply(self, obj, status=200):
            raw = json.dumps(obj).encode()
            self.send_response(status); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(raw)

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path); q = dict(urllib.parse.parse_qsl(u.query)); p = u.path
            if self.headers.get("X-Api-Key") != KEYS[app]:
                return self.reply({"message": "Unauthorized"}, 401)
            if p.endswith("/MediaCover/7/poster-250.jpg"):
                self.send_response(200); self.end_headers(); self.wfile.write(PNG); return
            p = p.replace("/base", "")
            if p == "/api/v3/system/status": return self.reply({"version": "4.0.1" if app == "sonarr" else "5.2.0"})
            if p == "/api/v3/queue/status": return self.reply({"totalCount": 2})
            if p == "/api/v3/qualityprofile": return self.reply([{"id": 4, "name": "HD-1080p"}, {"id": 6, "name": "Any"}])
            if p == "/api/v3/rootfolder":
                return self.reply([{"path": r"Z:\sabnzb\Downloads\complete\Movies" if app == "radarr" else r"Z:\sabnzb\Downloads\complete\TV Shows", "freeSpace": 4 * 10**11}])
            if p == "/api/v3/calendar":
                assert q["start"] == today.isoformat() and q["unmonitored"] == "false", q
                if app == "sonarr":
                    return self.reply([{"seriesId": 1, "title": "Pilot", "seasonNumber": 2, "episodeNumber": 5, "hasFile": False,
                                        "airDateUtc": (today + timedelta(days=1)).isoformat() + "T02:00:00Z",
                                        "series": {"title": "Slow Horses", "network": "Apple TV+", "images": [
                                            {"coverType": "poster", "url": "/base/MediaCover/7/poster.jpg?lastWrite=1", "remoteUrl": "https://artworks.thetvdb.com/banners/p.jpg"}]}}])
                return self.reply([{"title": "Far Movie", "year": 2026, "hasFile": False, "inCinemas": (today - timedelta(days=60)).isoformat() + "T00:00:00Z",
                                    "digitalRelease": (today + timedelta(days=3)).isoformat() + "T00:00:00Z",
                                    "images": [{"coverType": "poster", "remoteUrl": "https://image.tmdb.org/t/p/original/abc.jpg"}]},
                                   {"title": "Old News", "inCinemas": "2001-01-01T00:00:00Z"}])
            if p in ("/api/v3/movie/lookup", "/api/v3/series/lookup"):
                term = q["term"]
                if app == "radarr":
                    items = [{"title": "Dune", "year": 2021, "tmdbId": 438631, "studio": "Legendary", "runtime": 155, "overview": "Spice.", "images": []},
                             {"title": "Dune Owned", "year": 1984, "tmdbId": 841, "id": 12, "hasFile": True}]
                else:
                    items = [{"title": "Severance", "year": 2022, "tvdbId": 371980, "network": "Apple TV+", "status": "continuing",
                              "seasons": [{"seasonNumber": 0}, {"seasonNumber": 1}, {"seasonNumber": 2}]}]
                if term.startswith(("tmdb:", "tvdb:")):
                    items = [x for x in items if str(x.get("tmdbId") or x.get("tvdbId")) == term[5:]]
                return self.reply(items)
            self.reply({"message": "nope"}, 404)

        def do_POST(self):
            if self.headers.get("X-Api-Key") != KEYS[app]:
                return self.reply({}, 401)
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            posted[app] = (self.path, body)
            self.reply({**body, "id": 99})
    return H


servers = {}
for app in ("radarr", "sonarr"):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler(app))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    servers[app] = srv

# ---- found from config.xml (no setup), with the URL base ----
inst = tmp / "MediaAutomation"
for app, name in m.ARR_APPS.items():
    d = inst / name / "data"; d.mkdir(parents=True)
    base = "<UrlBase>/base</UrlBase>" if app == "sonarr" else "<UrlBase></UrlBase>"
    (d / "config.xml").write_text(f"<Config><Port>{servers[app].server_address[1]}</Port>{base}<ApiKey>{KEYS[app]}</ApiKey></Config>")
m.ARR_EXTRA_DIRS[:] = [tmp / "nothing-here", inst]
m.arr_clear_cache()
assert m.arr_conf("sonarr")["url"].endswith("/base") and m.arr_conf("radarr")["key"] == KEYS["radarr"]
assert KEYS["sonarr"] not in m.redact(f"failed {KEYS['sonarr']}")                      # keys never reach the log

ov = m.arr_overview()
assert [a["name"] for a in ov["apps"]] == ["Radarr", "Sonarr"] and all(a["online"] and a["queue"] == 2 for a in ov["apps"]), ov

# ---- coming up: episodes and movies together, in date order; old cinema dates ignored ----
up = m.arr_upcoming(force=True)
assert [x["title"] for x in up] == ["Slow Horses", "Far Movie"], up
ep, mv = up
assert ep["season"] == 2 and ep["episode"] == 5 and ep["poster"] == "a:sonarr:https://artworks.thetvdb.com/banners/p.jpg"
assert mv["release"] == "Digital release" and mv["poster"].endswith("/t/p/w342/abc.jpg"), mv
assert m.home_upcoming() == up

# ---- posters: public TMDB/TVDB only, or the app's own MediaCover folder; nothing else ----
raw, ctype = m.arr_art("sonarr:/MediaCover/7/poster-250.jpg")
assert ctype == "image/png"
for bad in ("sonarr:/api/v3/system/status", "radarr:https://evil.example/x.jpg", "nope:/MediaCover/1/poster.jpg", "sonarr:http://image.tmdb.org/x.jpg"):
    try:
        m.arr_art(bad); raise AssertionError(bad)
    except ValueError:
        pass

# ---- look up ----
found = m.arr_lookup("radarr", "  dune  ")
assert [(x["title"], x["in_library"]) for x in found] == [("Dune", False), ("Dune Owned", True)], found
assert found[0]["extra"] == "Legendary · 2 h 35 min"
shows = m.arr_lookup("sonarr", "severance")
assert shows[0]["extra"] == "Apple TV+ · 2 seasons" and shows[0]["ext_id"] == 371980

# ---- add: the profile/folder chosen (or the first), monitored, searching when asked ----
r = m.arr_add("radarr", 438631, profile_id=6, search=True)
path, body = posted["radarr"]
assert path == "/api/v3/movie" and body["qualityProfileId"] == 6 and body["rootFolderPath"].endswith("Movies")
assert body["monitored"] and body["addOptions"]["searchForMovie"] is True and r["searching"] and r["title"] == "Dune"
m.arr_add("radarr", 438631, profile_id=12345, search=False)                              # unknown profile -> first one
assert posted["radarr"][1]["qualityProfileId"] == 4 and posted["radarr"][1]["addOptions"]["searchForMovie"] is False
assert m.arr_add("radarr", 841)["already"] is True

r = m.arr_add("sonarr", 371980, monitor="all", search=True)
path, body = posted["sonarr"]
assert path == "/base/api/v3/series" and body["addOptions"] == {"monitor": "all", "searchForMissingEpisodes": True, "searchForCutoffUnmetEpisodes": False}
assert body["seasonFolder"] and r["searching"]
r = m.arr_add("sonarr", 371980, monitor="bogus", search=True)                            # "future": nothing to search yet
assert posted["sonarr"][1]["addOptions"]["monitor"] == "future" and not r["searching"]
for bad in (("lidarr", 1), ("radarr", 0)):
    try:
        m.arr_add(*bad); raise AssertionError(bad)
    except ValueError:
        pass

# ---- not installed: says so, Home stays quiet ----
m.ARR_EXTRA_DIRS[:] = [tmp / "nothing-here"]
m.arr_clear_cache()
st = m.arr_overview()["apps"]
assert not any(a["configured"] for a in st) and m.home_upcoming() == []
try:
    m.arr_lookup("radarr", "x"); raise AssertionError("no error")
except m.ConfigError as exc:
    assert "wasn't found" in str(exc)

# ---- the page ----
page = m.PAGE
assert 'id="pvr"' in page and 'data-tab="pvr"' in page and "/api/arr/art?ref=" in page and 'id="homeup"' in page
assert m.VERSION == "3.16.0"

for s in servers.values():
    s.shutdown()
print("arr OK")
