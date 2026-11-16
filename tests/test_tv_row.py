"""TV home row: newest movies from Kodi and SABnzbd, kept for when Kodi is off, playable in one press."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))
import os, tempfile, time
from pathlib import Path
import mobile_server as m

tmp = Path(tempfile.mkdtemp())
m.TV_STATE_FILE = tmp / "tv_recent.json"
m.TV_ART_DIR = tmp / "tv_art"
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100

# ---------- learning how Kodi reaches this PC's folders ----------
assert m.tv_learn_prefix(r"D:\Movies\Old Film (2020)", "smb://HAL9000/Movies/Old Film (2020)/old.film.mkv") == \
    (r"D:\Movies", "smb://HAL9000/Movies")
assert m.tv_learn_prefix(r"D:\Downloads\complete\movies\X (2021)", "nfs://nas/share/X (2021)/x.mkv") == \
    (r"D:\Downloads\complete\movies", "nfs://nas/share")
assert m.tv_learn_prefix(r"D:\A\B", "smb://h/C/D/f.mkv") is None
pm = [(r"D:\Movies", "smb://HAL9000/Movies")]
assert m.tv_map_path(r"D:\Movies\New One (2025)\new.one.mkv", pm) == "smb://HAL9000/Movies/New One (2025)/new.one.mkv"
assert m.tv_map_path(r"E:\Other\x.mkv", pm) == ""

# ---------- the main video of a download folder ----------
dl = tmp / "Movies" / "New One (2025)"
dl.mkdir(parents=True)
(dl / "new.one.sample.mkv").write_bytes(b"0" * 5000)
(dl / "new.one.mkv").write_bytes(b"0" * 3000)
(dl / "new.one.nfo").write_bytes(b"0" * 9000)
assert m.tv_video_file(str(dl)) == str(dl / "new.one.mkv")

# ---------- building the row ----------
now = time.time()
kodi_on = {"v": True}
played = []
def fake_kodi(method, params=None, timeout=6):
    if not kodi_on["v"]:
        raise m.UpstreamError("Kodi is off")
    if method == "VideoLibrary.GetRecentlyAddedMovies":
        return {"movies": [
            {"movieid": 7, "title": "Old Film", "year": 2020, "runtime": 6000, "file": "smb://HAL9000/Movies/Old Film (2020)/old.film.mkv",
             "art": {"poster": "image://poster7/"}, "dateadded": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 86400 * 10)),
             "playcount": 1, "streamdetails": {"video": [{"width": 3840, "height": 2160}]}}]}
    if method == "JSONRPC.Ping":
        return "pong"
    if method == "VideoLibrary.GetMovies":
        if params.get("filter", {}).get("value") == "Library Film":
            return {"movies": [{"movieid": 3, "title": "Library Film", "year": 2019, "file": "smb://HAL9000/Movies/Library Film (2019)/lf.mkv",
                                "art": {"poster": "image://poster3/"}, "playcount": 0, "runtime": 5400}]}
        return {"movies": []}
    if method == "Player.Open":
        played.append(params["item"]); return "OK"
    raise AssertionError(method)
m.kodi_call = fake_kodi
m.kodi_configured = lambda: True
m.kodi_art = lambda ref: (JPEG, "image/jpeg")
m.lookup_poster_url = lambda clean, year, hint: "https://is1-ssl.mzstatic.com/x.jpg"
m.host_is_public = lambda h: True
m.http_fetch = lambda url, **kw: JPEG
m.sab_request = lambda mode, extra=None, timeout=15: {"history": {"slots": [
    {"nzo_id": "SABnzbd_nzo_new", "name": "New.One.2025.2160p.WEB-DL.x265", "category": "movies", "status": "Completed",
     "completed": int(now - 3600), "storage": str(dl)},
    {"nzo_id": "SABnzbd_nzo_again", "name": "New.One.2025.1080p.WEB-DL.x264", "category": "movies", "status": "Completed",
     "completed": int(now - 7200), "storage": str(dl)},
    {"nzo_id": "SABnzbd_nzo_lib", "name": "Library.Film.2019.1080p.BluRay", "category": "movies", "status": "Completed",
     "completed": int(now - 86400 * 20), "storage": r"D:\Movies\Library Film (2019)"},
    {"nzo_id": "SABnzbd_nzo_old", "name": "Old.Film.2020.1080p.BluRay", "category": "movies", "status": "Completed",
     "completed": int(now - 86400 * 10), "storage": r"D:\Movies\Old Film (2020)"},
    {"nzo_id": "SABnzbd_nzo_tv", "name": "Some.Show.S01E02.1080p", "category": "tv", "status": "Completed",
     "completed": int(now - 60), "storage": "x"},
    {"nzo_id": "SABnzbd_nzo_bad", "name": "Broken.Movie.2024", "category": "movies", "status": "Failed", "completed": int(now)}]}}

state = m.tv_refresh()
ids = [x["id"] for x in state["items"]]
# newest first; no TV episode, no failed job, one card for a movie downloaded twice; a download Kodi has
# (just not among its newest) comes with Kodi's id and poster
assert ids == ["sSABnzbd_nzo_new", "k7", "k3"], ids
assert state["items"][2]["movieid"] == 3 and (m.TV_ART_DIR / "k3.img").exists()
new, old, _lib = state["items"]
assert new["title"] == "New One" and new["year"] == 2025 and new["quality"] == "4K" and new["new"] and not new["in_kodi"]
assert old["movieid"] == 7 and old["quality"] == "4K" and old["watched"] and not old["new"]
assert [r"D:\Movies", "smb://HAL9000/Movies"] in state["path_map"]
# the local test folder isn't under D:\Movies, so its file can't be mapped; with a matching prefix it is
assert new["file"] == ""
assert (m.TV_ART_DIR / "k7.img").read_bytes() == JPEG and (m.TV_ART_DIR / "sSABnzbd_nzo_new.img").exists()

row = m.tv_row()
assert row["items"][0]["poster"] == "/api/tv/poster?id=sSABnzbd_nzo_new"
assert m.tv_poster("k7") == JPEG
for bad in ("../x", "k7/../../a", ""):
    try:
        m.tv_poster(bad); raise AssertionError("bad id accepted")
    except ValueError:
        pass

# ---------- Kodi switched off: the row stays, with Kodi's part remembered ----------
kodi_on["v"] = False
state = m.tv_refresh()
assert [x["id"] for x in state["items"]][:2] == ["sSABnzbd_nzo_new", "k7"] and state["kodi_online"] is False

# ---------- playing waits for Kodi to start ----------
def later_on():
    time.sleep(1); kodi_on["v"] = True
import threading
threading.Thread(target=later_on, daemon=True).start()
assert m.tv_play("k7", wait=10)["played"] == {"movieid": 7}
try:
    m.tv_play("sSABnzbd_nzo_new", wait=1); raise AssertionError("unplayable item played")
except ValueError as e:
    assert "Kodi can't find" in str(e)
kodi_on["v"] = False
try:
    m.tv_play("k7", wait=0.5); raise AssertionError("no Kodi, still played")
except m.UpstreamError:
    pass
assert played == [{"movieid": 7}]
print("tv row ok")
