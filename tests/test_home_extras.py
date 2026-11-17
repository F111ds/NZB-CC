"""Phone app 3.10: Home's Jellyfin now playing, Just arrived + Play on TV, saved searches only with alerts."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))
import json, tempfile
from pathlib import Path
import mobile_server as m

tmp = Path(tempfile.mkdtemp())
m.SETTINGS_FILE = tmp / "mobile_settings.json"

# ---- Jellyfin on Home: everything except Kodi's own stream (the Kodi card shows that) ----
m.jellyfin_configured = lambda: True
m.jf_sessions = lambda: [
    {"session": "a", "client": "Jellyfin for Kodi", "device": "Living Room", "title": "Back to the Future"},
    {"session": "b", "client": "Jellyfin Mobile (iOS)", "device": "iPad", "title": "The Long Road"},
    {"session": "c", "client": "Jellyfin Web", "device": "Kodi box", "title": "Coco"}]
assert [x["session"] for x in m.home_jellyfin_sessions()] == ["b"]

# Jellyfin failing: [] and not asked again for a while (Home polls every 5 s).
calls = {"n": 0}
def broken():
    calls["n"] += 1
    raise m.UpstreamError("offline")
m.jf_sessions = broken
m._home_jf_down_until = 0.0
assert m.home_jellyfin_sessions() == [] and m.home_jellyfin_sessions() == [] and calls["n"] == 1
m._home_jf_down_until = 0.0

# ---- Just arrived: newest first, at most 10, posters as "t:<id>" ----
m.tv_row = lambda: {"items": [
    {"id": f"m{i:02d}", "title": f"Movie {i}", "year": 2020, "added": f"2026-11-{i:02d} 10:00:00", "poster": "/x" if i % 2 else "",
     "new": i > 10, "watched": False, "quality": "1080p", "in_kodi": True} for i in range(1, 13)]}
arr = m.home_arrived()
assert [x["id"] for x in arr] == [f"m{i:02d}" for i in range(12, 2, -1)], [x["id"] for x in arr]
assert arr[1]["poster"] == "t:m11" and arr[0]["poster"] == "" and arr[0]["new"] is True

# ---- Play on TV: Kodi already on -> just play; Kodi off -> TV on first, then play (waits longer) ----
played, started = [], []
m.tv_play = lambda item_id, wait=45, from_start=False: played.append((item_id, wait)) or {"ok": True, "played": {"movieid": 1}}
m.tv_start = lambda launch_kodi=True: started.append(launch_kodi) or True
m.kodi_call = lambda method, params=None, timeout=6: "pong"
r = m.home_play("m12")
assert r["started_tv"] is False and played[-1] == ("m12", m.TV_PLAY_WAIT_SECS) and not started
def kodi_off(method, params=None, timeout=6):
    raise m.UpstreamError("Can't reach Kodi")
m.kodi_call = kodi_off
m.tv_configured = lambda settings=None: True
r = m.home_play("m11")
assert r["started_tv"] is True and started == [True] and played[-1] == ("m11", 90)
m.tv_configured = lambda settings=None: False
try:
    m.home_play("m10"); raise AssertionError("played without Kodi or TV control")
except m.UpstreamError as exc:
    assert "Turn the TV on" in str(exc)
try:
    m.home_play("../../x"); raise AssertionError("bad id accepted")
except ValueError:
    pass

# ---- Saved searches only run when alerts are set up (their results only go out as alerts) ----
m.SETTINGS_FILE.write_text(json.dumps({"saved_searches": [{"id": "s1", "query": "dune", "last_run": 0}]}))
ran = []
m.run_saved_search = lambda sid, notify=True: ran.append(sid)
m.tv_refresh = m.tv_watched_refresh = lambda: None
w = m.BackgroundWorker(); w.check_drives = lambda *a: None; w._next_drives = 9e18
w.tick(now=10_000_000)
assert ran == []                                            # no alerts: no background indexer searches
m.SETTINGS_FILE.write_text(json.dumps({"ntfy_url": "https://ntfy.sh/x", "saved_searches": [{"id": "s1", "query": "dune", "last_run": 0}]}))
w.check_history = lambda settings: None
w.tick(now=10_000_000)
assert ran == ["s1"]

# ---- Page wiring ----
p = m.PAGE
for needle in ('id="alertsbtn"', 'id="homejf"', 'id="homearrived"', 'id="homever"', "function readyAt", "function openAlerts",
               "function setupPullToRefresh", "/api/home/play", "'/api/tv/poster?id='", 'class="bar big"'):
    assert needle in p, needle
print("home extras OK")
