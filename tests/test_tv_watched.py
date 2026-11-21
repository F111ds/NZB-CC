"""TV home row, recently watched: Kodi's movies and episodes, last played first, with progress; poster-less items left out."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))
import tempfile, time
from pathlib import Path
import mobile_server as m

tmp = Path(tempfile.mkdtemp())
m.TV_STATE_FILE = tmp / "tv_recent.json"
m.TV_ART_DIR = tmp / "tv_art"
m.TV_WATCHED_FILE = tmp / "tv_watched.json"
m.TV_WATCHED_ART_DIR = tmp / "tv_watched_art"
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100

kodi_on = {"v": True}
open_item = {"v": None}   # what Kodi's player has open (playing or paused), or None
calls, played = [], []
def fake_kodi(method, params=None, timeout=6):
    calls.append((method, params))
    if not kodi_on["v"]:
        raise m.UpstreamError("Kodi is off")
    if method == "VideoLibrary.GetMovies":
        assert params["sort"] == {"method": "lastplayed", "order": "descending"}
        return {"movies": [
            {"movieid": 3, "title": "Half Way", "year": 2024, "runtime": 6000, "art": {"poster": "image://p3/", "fanart": "image://f3/"},
             "plot": "A long trip.", "genre": ["Drama", "Sci-Fi"],
             "playcount": 0, "resume": {"position": 1500, "total": 6000}, "lastplayed": "2026-11-15 21:00:00",
             "streamdetails": {"video": [{"width": 3840, "height": 2160}]}},
            {"movieid": 4, "title": "No Art", "runtime": 5400, "art": {}, "playcount": 1,
             "resume": {"position": 0, "total": 0}, "lastplayed": "2026-11-16 09:00:00"},
            {"movieid": 5, "title": "Done", "runtime": 5400, "art": {"poster": "image://p5/"}, "playcount": 2,
             "resume": {"position": 0, "total": 0}, "lastplayed": "2026-11-10 20:00:00"}]}
    if method == "VideoLibrary.GetEpisodes":
        return {"episodes": [
            {"episodeid": 42, "title": "Pilot", "showtitle": "Some Show", "season": 1, "episode": 2, "runtime": 2700,
             "art": {"tvshow.poster": "image://s/"}, "playcount": 0, "resume": {"position": 900, "total": 2700},
             "lastplayed": "2026-11-16 08:00:00"}]}
    if method == "Player.GetActivePlayers":
        return [{"playerid": 1, "type": "video"}] if open_item["v"] else []
    if method == "Player.GetItem":
        return {"item": open_item["v"]}
    if method == "Player.GetProperties":
        return {"time": {"hours": 0, "minutes": 30, "seconds": 0}, "totaltime": {"hours": 1, "minutes": 38, "seconds": 0}}
    if method == "JSONRPC.Ping":
        return "pong"
    if method == "Player.Open":
        played.append(params); return "OK"
    raise AssertionError(method)
m.kodi_call = fake_kodi
m.kodi_configured = lambda: True
m.kodi_art = lambda ref: (JPEG, "image/jpeg")

row = m.tv_watched_row(background=False)
ids = [it["id"] for it in row["items"]]
assert ids == ["e42", "m3", "m5"], ids                       # last played first; "No Art" has no poster: left out
ep, half, done = row["items"]
assert ep["title"] == "Some Show" and ep["episode_title"] == "Pilot" and (ep["season"], ep["episode"]) == (1, 2)
assert abs(ep["progress"] - 1 / 3) < 0.01 and ep["remaining"] == 30
assert half["progress"] == 0.25 and half["remaining"] == 75 and half["quality"] == "4K" and half["runtime"] == 100
assert done["progress"] == 1.0 and done["watched"] is True
assert all(it["poster"] == f"/api/tv/poster?id={it['id']}" for it in row["items"])
assert m.tv_poster("e42") == JPEG and m.tv_poster("m3") == JPEG
assert m.tv_poster("m4") is None
# The home screen's big picture: the story, genres and background art (only when Kodi has fanart)
assert half["plot"] == "A long trip." and half["genres"] == ["Drama", "Sci-Fi"]
assert half["backdrop"] == "/api/tv/backdrop?id=m3" and ep["backdrop"] == "" and m.tv_backdrop("m3") == JPEG
assert m.tv_backdrop("e42") is None
try:
    m.tv_backdrop("../x"); raise AssertionError("bad id")
except ValueError:
    pass

# A movie left open in Kodi (paused behind Home) has no resume point or "last played" yet: it still comes first,
# with where the player is now; the same movie further down the list isn't shown twice.
open_item["v"] = {"type": "movie", "id": 3, "title": "Half Way", "year": 2024, "art": {"poster": "image://p3/"},
                  "streamdetails": {"video": [{"width": 1920, "height": 1080}]}}
m.TV_WATCHED_FILE.unlink()
row = m.tv_watched_row(background=False)
assert [it["id"] for it in row["items"]] == ["m3", "e42", "m5"], [it["id"] for it in row["items"]]
now = row["items"][0]
assert abs(now["progress"] - 30 / 98) < 0.01 and now["remaining"] == 68 and now["quality"] == "1080p"
open_item["v"] = {"type": "unknown", "id": 0, "label": "some.file.mkv"}   # not in the library: no entry for it
m.TV_WATCHED_FILE.unlink()
assert [it["id"] for it in m.tv_watched_row(background=False)["items"]] == ["e42", "m3", "m5"]
open_item["v"] = None

# Kodi off: the last list stays
kodi_on["v"] = False
m.TV_WATCHED_FILE.write_text(m.TV_WATCHED_FILE.read_text().replace('"updated": ', '"updated": 1, "x": '))
assert [it["id"] for it in m.tv_watched_row(background=False)["items"]] == ["e42", "m3", "m5"]

# Playing resumes the item where it was left
kodi_on["v"] = True
assert m.tv_play("e42", wait=1)["played"] == {"episodeid": 42}
assert m.tv_play("m3", wait=1)["played"] == {"movieid": 3}
assert played == [{"item": {"episodeid": 42}, "options": {"resume": True}},
                  {"item": {"movieid": 3}, "options": {"resume": True}}]
for bad in ("m", "mx1", "../m1", "e1/2"):
    try:
        m.tv_play(bad, wait=1)
        raise AssertionError(bad)
    except ValueError:
        pass
print("test_tv_watched OK")

# Play from the start, and mark as watched
played.clear()
assert m.tv_play("m3", wait=1, from_start=True)["played"] == {"movieid": 3}
assert played == [{"item": {"movieid": 3}, "options": {"resume": False}}]
sets = []
_old = m.kodi_call
def fake2(method, params=None, timeout=6):
    if method.startswith("VideoLibrary.Set"):
        sets.append((method, params)); return "OK"
    return _old(method, params, timeout)
m.kodi_call = fake2
assert m.tv_mark_watched("e42") == {"ok": True}
assert sets == [("VideoLibrary.SetEpisodeDetails", {"playcount": 1, "resume": {"position": 0, "total": 0}, "episodeid": 42})]
assert next(it for it in m.tv_watched_load()["items"] if it["id"] == "e42")["progress"] == 1.0
for bad in ("k1", "m", "../e1"):
    try:
        m.tv_mark_watched(bad); raise AssertionError(bad)
    except ValueError:
        pass
print("test_tv_watched part 2 OK")

# fresh=1 reads Kodi right away even when the saved list is recent
m.kodi_call = _old
before = m.tv_watched_load()["updated"]
import time as _t
st = m.tv_watched_load(); st["updated"] = _t.time() - 30
m.TV_WATCHED_FILE.write_text(__import__("json").dumps(st))
row = m.tv_watched_row(fresh=True)
assert row["updated"] > _t.time() - 5, row["updated"]
assert [it["id"] for it in row["items"]][:1] == ["e42"]
print("test_tv_watched part 3 OK")
