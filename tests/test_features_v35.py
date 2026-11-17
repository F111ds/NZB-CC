"""Phone app 3.5: TV shows, remote, smarter search, queue options, alerts, saved searches,
stats and autostart - against fake Kodi / SABnzbd / indexer / ntfy servers."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))
import json, os, tempfile, threading, time, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import mobile_server as m

log = {"kodi": [], "sab": [], "idx": [], "ntfy": []}
now = int(time.time())

EPISODES = [  # show 1: S01E01-E03 watched, E04 started, E06 (E05 missing); S02E02 (E01 missing); show 2 fully watched
    {"episodeid": 11, "tvshowid": 1, "showtitle": "Severance", "season": 1, "episode": 1, "playcount": 1, "title": "Good News"},
    {"episodeid": 12, "tvshowid": 1, "showtitle": "Severance", "season": 1, "episode": 2, "playcount": 1, "title": "Half Loop"},
    {"episodeid": 13, "tvshowid": 1, "showtitle": "Severance", "season": 1, "episode": 3, "playcount": 1, "title": "In Perpetuity"},
    {"episodeid": 14, "tvshowid": 1, "showtitle": "Severance", "season": 1, "episode": 4, "playcount": 0, "title": "The You You Are", "resume": {"position": 600}},
    {"episodeid": 16, "tvshowid": 1, "showtitle": "Severance", "season": 1, "episode": 6, "playcount": 0, "title": "Hide and Seek"},
    {"episodeid": 22, "tvshowid": 1, "showtitle": "Severance", "season": 2, "episode": 2, "playcount": 0, "title": "Goodbye"},
    {"episodeid": 31, "tvshowid": 2, "showtitle": "The Bear", "season": 1, "episode": 1, "playcount": 1, "title": "System"},
]
SHOWS = [{"tvshowid": 1, "title": "Severance", "year": 2022, "episode": 6, "watchedepisodes": 3, "lastplayed": "2026-11-13 20:00:00"},
         {"tvshowid": 2, "title": "The Bear", "year": 2022, "episode": 1, "watchedepisodes": 1, "lastplayed": "2026-11-01 20:00:00"}]


def reply(h, status, body, ctype="application/json"):
    b = body if isinstance(body, bytes) else json.dumps(body).encode()
    h.send_response(status); h.send_header("Content-Type", ctype); h.send_header("Content-Length", str(len(b))); h.end_headers(); h.wfile.write(b)


class Kodi(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        meth, p = req["method"], req.get("params") or {}
        log["kodi"].append((meth, p))
        res = "OK"
        if meth == "VideoLibrary.GetEpisodes":
            eps = [e for e in EPISODES if "tvshowid" not in p or e["tvshowid"] == p["tvshowid"]]
            lim = p.get("limits") or {"start": 0, "end": len(eps)}
            res = {"episodes": eps[lim["start"]:lim["end"]], "limits": {"total": len(eps)}}
        elif meth == "VideoLibrary.GetTVShows":
            lim = p.get("limits") or {"start": 0, "end": 99}
            res = {"tvshows": SHOWS[lim["start"]:lim["end"]], "limits": {"total": len(SHOWS)}}
        elif meth == "VideoLibrary.GetTVShowDetails":
            res = {"tvshowdetails": dict(SHOWS[0], plot="Work-life balance.")} if p["tvshowid"] == 1 else {}
        elif meth == "VideoLibrary.GetMovieDetails":
            res = {"moviedetails": {"movieid": 5, "title": "Dune", "year": 2021, "imdbnumber": "tt1160419",
                   "streamdetails": {"video": [{"width": 1920, "height": 800, "hdrtype": ""}]}}}
        elif meth == "Application.GetProperties":
            res = {"volume": 42, "muted": False}
        reply(self, 200, {"jsonrpc": "2.0", "id": 1, "result": res})


class Sab(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}
        log["sab"].append(qs)
        mode = qs.get("mode")
        if mode == "server_stats":
            return reply(self, 200, {"day": 5 * 2**30, "week": 40 * 2**30, "month": 200 * 2**30, "total": 9 * 2**40})
        if mode == "history":
            slots = Sab.history
            return reply(self, 200, {"history": {"slots": slots}})
        if mode == "get_cats":
            return reply(self, 200, {"categories": ["*", "movies", "tv"]})
        reply(self, 200, {"status": True})
Sab.history = [{"nzo_id": "SABnzbd_nzo_old", "name": "Old.Thing", "status": "Completed", "completed": now - 300, "bytes": 1000}]


class Indexer(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}
        log["idx"].append(qs)
        t = qs.get("t")
        if t == "tvsearch" and Indexer.no_tv:
            return reply(self, 200, b'<error code="203" description="Function not available"/>', "application/xml")
        titles = Indexer.titles if t != "movie" else ["Dune.2021.2160p.UHD.BluRay.x265", "Dune.2021.720p.WEB"]
        items = "".join(f'<item><title>{x}</title><link>http://127.0.0.1/get/{i}</link><pubDate>Thu, 13 Nov 2026 10:00:00 +0000</pubDate>'
                        f'<enclosure url="http://127.0.0.1/get/{i}" length="1000"/></item>' for i, x in enumerate(titles))
        reply(self, 200, f'<?xml version="1.0"?><rss><channel>{items}</channel></rss>'.encode(), "application/xml")
Indexer.no_tv = False
Indexer.titles = ["Severance.S01E05.1080p.WEB"]


class Ntfy(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        log["ntfy"].append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"]))), self.headers.get("Authorization")))
        reply(self, 200, {"id": "x"})


def serve(h):
    s = ThreadingHTTPServer(("127.0.0.1", 0), h); threading.Thread(target=s.serve_forever, daemon=True).start(); return s
kodi, sab, idx, ntfy, app = (serve(h) for h in (Kodi, Sab, Indexer, Ntfy, m.MobileHandler))
port = lambda s: s.server_address[1]

tmp = Path(tempfile.mkdtemp())
cfg = {"sab_url": f"127.0.0.1:{port(sab)}", "sab_api_key": "plainsabkey123", "kodi_url": f"127.0.0.1:{port(kodi)}",
       "indexers": [{"name": "Local", "url": f"http://127.0.0.1:{port(idx)}/api", "api_key": "idxkey12345"}]}
(tmp / "nzb_manager_config.json").write_text(json.dumps(cfg))
m.CONFIG_FILE = tmp / "nzb_manager_config.json"
m.SETTINGS_FILE = tmp / "mobile_settings.json"
# The background worker also saves the TV rows and drive history: keep those out of the project folder.
m.TV_STATE_FILE, m.TV_WATCHED_FILE, m.DRIVE_HISTORY_FILE = tmp / "tv_recent.json", tmp / "tv_watched.json", tmp / "drive_history.json"
m._cfg_state.update(mtime=None, data=None)
m._token_bytes = b"testtoken"
base = f"http://127.0.0.1:{port(app)}"


def api(path, body=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Mobile-Token": "testtoken", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())


# ---- TV shows: next episode, gaps, continue watching, missing ----------------------------
assert m.episode_gaps([1, 2, 4, 7]) == [3, 5, 6] and m.episode_gaps([]) == []
st, shows = api("/api/kodi/shows"); assert st == 200 and [s["title"] for s in shows["shows"]] == ["Severance", "The Bear"]
st, show = api("/api/kodi/show?id=1"); assert st == 200, show
assert show["next"]["id"] == 14 and show["next"]["resume"] == 600, show["next"]          # the started episode
assert [s["season"] for s in show["seasons"]] == [1, 2]
assert show["seasons"][0]["missing"] == [5] and show["seasons"][1]["missing"] == [1]
assert api("/api/kodi/show?id=99")[0] == 400 and api("/api/kodi/show?id=0")[0] == 400
eps = [m._kodi_episode(e) for e in EPISODES if e["tvshowid"] == 1]
for e in eps: e["resume"] = 0
assert m.next_episode(eps)["id"] == 14                                                     # first unwatched after last watched
st, k = api("/api/kodi"); assert st == 200 and k["counts"]["shows"] == 2
assert [c["show"]["title"] for c in k["continue"]] == ["Severance"] and k["continue"][0]["episode"]["id"] == 14  # The Bear is finished
st, miss = api("/api/kodi/missing")
assert miss["missing"] == [{"show_id": 1, "show": "Severance", "season": 1, "missing": [5]},
                           {"show_id": 1, "show": "Severance", "season": 2, "missing": [1]}], miss
assert api("/api/kodi/play", {"episodeid": 14, "resume": True})[0] == 200
assert log["kodi"][-1] == ("Player.Open", {"item": {"episodeid": 14}, "options": {"resume": True}})
assert api("/api/kodi/play", {"episodeid": 14, "movieid": 3})[0] == 400

# ---- Remote ------------------------------------------------------------------------------
for action, method, params in [("up", "Input.Up", {}), ("select", "Input.Select", {}), ("vol_up", "Application.SetVolume", {"volume": "increment"}),
                               ("mute", "Application.SetMute", {"mute": "toggle"}),
                               ("seek_fwd", "Player.Seek", {"playerid": 1, "value": {"seconds": 30}}),
                               ("subs_next", "Player.SetSubtitle", {"playerid": 1, "subtitle": "next", "enable": True}),
                               ("audio_next", "Player.SetAudioStream", {"playerid": 1, "stream": "next"})]:
    assert api("/api/kodi/control", {"action": action, "playerid": 1})[0] == 200, action
    assert log["kodi"][-1][0] == method and (log["kodi"][-1][1] or {}) == params, (action, log["kodi"][-1])
assert api("/api/kodi/control", {"action": "seek_pct", "playerid": 1, "value": 50})[0] == 200
assert api("/api/kodi/control", {"action": "up", "playerid": -1})[0] == 200               # navigation needs no player
st, err = api("/api/kodi/control", {"action": "playpause", "playerid": -1}); assert st == 400 and "Nothing is playing" in err["error"]
assert api("/api/kodi/control", {"action": "System.Shutdown", "playerid": 1})[0] == 400
assert api("/api/kodi/control", {"action": "seek_pct", "playerid": 1, "value": 150})[0] == 400
st, now_p = api("/api/kodi/now"); assert now_p["volume"] == {"level": 42, "muted": False}

# ---- Better copy: quality of the file Kodi has + IMDb search ------------------------------
st, mv = api("/api/kodi/movie?id=5"); assert mv["quality"] == "1080p" and mv["imdb_id"] == "tt1160419", mv
st, res = api("/api/search", {"query": "Dune 2021", "imdbid": "tt1160419", "category": "2000"})
assert st == 200 and res["mode"] == "movie" and log["idx"][-1]["t"] == "movie" and log["idx"][-1]["imdbid"] == "1160419"
assert api("/api/search", {"query": "x", "imdbid": "nope"})[0] == 400

# ---- Episode search, with fallback when the indexer lacks t=tvsearch ----------------------
assert m.search_plan("Severance S01E05") == {"t": "tvsearch", "q": "Severance", "season": 1, "ep": 5}
assert m.search_plan("The.Bear.S03") == {"t": "tvsearch", "q": "The.Bear", "season": 3}
assert m.search_plan("Dune 2021") is None
log["idx"].clear()
st, res = api("/api/search", {"query": "Severance S01E05"})
assert st == 200 and res["mode"] == "tvsearch" and len(res["results"]) == 1 and [q["t"] for q in log["idx"]] == ["tvsearch"]
Indexer.no_tv = True; log["idx"].clear()
st, res = api("/api/search", {"query": "Severance S01E05"})
assert st == 200 and len(res["results"]) == 1 and [q["t"] for q in log["idx"]] == ["tvsearch", "search"], log["idx"]
Indexer.no_tv = False

# ---- Queue options & history delete ------------------------------------------------------
assert api("/api/queue/item", {"id": "SABnzbd_nzo_1", "action": "priority", "value": "1"})[0] == 200
assert {k: log["sab"][-1][k] for k in ("mode", "name", "value", "value2")} == {"mode": "queue", "name": "priority", "value": "SABnzbd_nzo_1", "value2": "1"}
assert api("/api/queue/item", {"id": "SABnzbd_nzo_1", "action": "priority", "value": "9"})[0] == 400
assert api("/api/queue/item", {"id": "SABnzbd_nzo_1", "action": "category", "value": "tv"})[0] == 200
assert log["sab"][-1]["mode"] == "change_cat" and log["sab"][-1]["value2"] == "tv"
assert api("/api/queue/item", {"id": "SABnzbd_nzo_1", "action": "category", "value": ""})[0] == 200 and log["sab"][-1]["value2"] == "*"
assert api("/api/queue/item", {"id": "SABnzbd_nzo_1", "action": "category", "value": "nope"})[0] == 400
assert api("/api/history/delete", {"id": "SABnzbd_nzo_old"})[0] == 200
assert log["sab"][-1]["mode"] == "history" and log["sab"][-1]["name"] == "delete" and "del_files" not in log["sab"][-1]
assert api("/api/history/delete", {"id": "../x"})[0] == 400

st, home = api("/api/home"); assert "media" not in home and api("/api/media")[0] == 404

# ---- Stats ---------------------------------------------------------------------------------
Sab.history = [{"nzo_id": "a", "status": "Completed", "completed": now - 60, "bytes": 3000},
               {"nzo_id": "b", "status": "Failed", "completed": now - 120},
               {"nzo_id": "c", "status": "Completed", "completed": now - 86400 * 30, "bytes": 5}]
st, stats = api("/api/stats"); assert st == 200 and stats["totals"]["week"] == 40 * 2**30 and len(stats["days"]) == 7
today = stats["days"][-1]
if time.localtime(now - 120).tm_mday == time.localtime().tm_mday:  # skip the check in the two minutes after midnight
    assert (today["done"], today["failed"], today["bytes"]) == (1, 1, 3000), today
assert sum(d["done"] + d["failed"] for d in stats["days"][:-1]) == 0

# ---- Phone settings & ntfy alerts ------------------------------------------------------------
st, s = api("/api/settings"); assert st == 200 and s["ntfy_url"] == "" and s["notify_complete"]
assert api("/api/settings/test", {})[0] == 400                                                 # nothing set up yet
assert api("/api/settings", {"ntfy_url": "ftp://x/y", "saved_interval_hours": 6})[0] == 400
_real_public = m.host_is_public   # answer "ntfy.sh is public" without needing internet DNS here
m.host_is_public = lambda h: h == "ntfy.sh" or _real_public(h)
assert api("/api/settings", {"ntfy_url": "http://ntfy.sh/topic", "saved_interval_hours": 6})[0] == 400  # public host needs https
m.host_is_public = _real_public
assert api("/api/settings", {"ntfy_url": "https://ntfy.sh/a?b=c", "saved_interval_hours": 6})[0] == 400
assert api("/api/settings", {"ntfy_url": f"http://127.0.0.1:{port(ntfy)}/home-alerts", "saved_interval_hours": 7})[0] == 400
st, s = api("/api/settings", {"ntfy_url": f"http://127.0.0.1:{port(ntfy)}/home-alerts", "ntfy_token": "tk_secret123",
                              "notify_failed": False, "saved_interval_hours": 3})
assert st == 200 and s["ntfy_token_set"] and not s["notify_failed"] and s["saved_interval_hours"] == 3, s
stored = json.loads(m.SETTINGS_FILE.read_text())
assert "tk_secret123" not in json.dumps(stored) or os.name != "nt"                             # DPAPI-protected on Windows
assert api("/api/settings/test", {})[0] == 200
path, body, auth = log["ntfy"][-1]
assert path == "/" and body["topic"] == "home-alerts" and "working" in body["message"] and auth == "Bearer tk_secret123", log["ntfy"][-1]

# background worker: first look is silent, then one alert per new finished download; failed alerts are switched off
w = m.BackgroundWorker()
Sab.history = [{"nzo_id": "a", "name": "Old", "status": "Completed", "completed": now}, {"nzo_id": "p", "name": "Busy", "status": "Extracting"}]
log["ntfy"].clear(); w.tick(now=1000)
assert log["ntfy"] == []
Sab.history = [{"nzo_id": "p", "name": "Busy.Show.S01E01", "status": "Completed", "completed": now},
               {"nzo_id": "f", "name": "Broken", "status": "Failed", "fail_message": "CRC"}] + Sab.history
w.tick(now=1010); assert log["ntfy"] == []                                                        # history checked at most every 30 s
w.tick(now=1031)
assert len(log["ntfy"]) == 1 and log["ntfy"][0][1]["title"] == "Download finished" and "Busy.Show.S01E01" in log["ntfy"][0][1]["message"], log["ntfy"]
w.tick(now=1062); assert len(log["ntfy"]) == 1                                                    # not announced twice

# ---- Saved searches ---------------------------------------------------------------------------
assert api("/api/saved", {"action": "add", "query": "x"})[0] == 400
st, sv = api("/api/saved", {"action": "add", "query": "Severance", "category": "5000"}); assert st == 200 and len(sv["searches"]) == 1
assert api("/api/saved", {"action": "add", "query": "severance", "category": "5000"})[0] == 400  # duplicate
sid = sv["searches"][0]["id"]
Indexer.titles = ["Severance.S01E05.1080p.WEB"]; log["ntfy"].clear()
st, sv = api("/api/saved", {"action": "run", "id": sid})
assert st == 200 and sv["searches"][0]["new"] == [] and sv["searches"][0]["last_count"] == 1      # first run = baseline
Indexer.titles = ["Severance.S01E05.1080p.WEB", "Severance.S02E01.2160p.WEB"]
s = m.load_settings()
due = m.due_saved_search(s, time.time() + 4 * 3600)                                                 # interval is 3 h
assert due and due["id"] == sid and m.due_saved_search(s, time.time()) is None
w.tick(now=time.time() + 4 * 3600)
st, sv = api("/api/saved"); entry = sv["searches"][0]
assert [x["title"] for x in entry["new"]] == ["Severance.S02E01.2160p.WEB"], entry
assert any("Severance.S02E01" in n[1]["message"] for n in log["ntfy"]), log["ntfy"]
st, home = api("/api/home"); assert home["saved_new"] == 1
st, sv = api("/api/saved", {"action": "clear", "id": sid}); assert sv["searches"][0]["new"] == []
st, sv = api("/api/saved", {"action": "delete", "id": sid}); assert sv["searches"] == []
assert api("/api/saved", {"action": "delete", "id": sid})[0] == 400

# ---- Autostart (written into a temporary APPDATA, never the real Startup folder) ------------
if os.name == "nt":
    real = os.environ.get("APPDATA")
    os.environ["APPDATA"] = str(tmp / "appdata")
    try:
        assert m.autostart_enabled() is False
        p = m.install_autostart(["--port", "9000"])
        text = p.read_text(encoding="utf-16")
        assert m.autostart_enabled() and "mobile_server.py" in text and '""--port"" ""9000""' in text and ", 0, False" in text, text
        assert m.remove_autostart() and not m.autostart_enabled() and not m.remove_autostart()
    finally:
        os.environ["APPDATA"] = real
args = m.parse_args(["--port", "9001", "--allow-cidr", "10.9.0.0/16"])
assert m._autostart_args(args) == ["--port", "9001", "--allow-cidr", "10.9.0.0/16"]

# ---- Page ------------------------------------------------------------------------------------
with urllib.request.urlopen(base + "/", timeout=5) as r:
    page = r.read().decode()
assert 'data-tab="more"' not in page and 'id="homeusage"' in page and 'id="kodiremote"' in page and "v" + m.VERSION in page and "__VERSION__" not in page
print("FEATURE TESTS PASSED")
