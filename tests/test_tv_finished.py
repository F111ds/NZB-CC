"""TV "Ready to watch" note: SABnzbd's finished downloads, newest first, with a poster when the row has one."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))
import json, tempfile
from pathlib import Path
import mobile_server as m

tmp = Path(tempfile.mkdtemp())
m.TV_STATE_FILE = tmp / "tv_recent.json"
m.TV_STATE_FILE.write_text(json.dumps({"updated": 1, "items": [
    {"id": "k7", "title": "The Green Valley", "year": 2024, "poster": True}]}), encoding="utf-8")
m.sab_request = lambda mode, extra=None, timeout=15: {"history": {"slots": [
    {"nzo_id": "SABnzbd_nzo_a", "name": "The.Green.Valley.2024.1080p.WEB-DL.x264", "category": "movies",
     "status": "Completed", "completed": 1700000100},
    {"nzo_id": "SABnzbd_nzo_b", "name": "Northern.Skies.S02E05.1080p.WEB.h264", "category": "tv",
     "status": "Completed", "completed": 1700000200},
    {"nzo_id": "SABnzbd_nzo_c", "name": "Broken.Movie.2023.1080p", "category": "movies",
     "status": "Failed", "completed": 1700000300},
    {"nzo_id": "SABnzbd_nzo_d", "name": "Some.Album.2020.FLAC", "category": "music",
     "status": "Completed", "completed": 1700000400}]}}
items = m.tv_finished()["items"]
assert [i["id"] for i in items] == ["sSABnzbd_nzo_b", "sSABnzbd_nzo_a"], items
ep, mv = items
assert ep["kind"] == "tv" and ep["title"].endswith("S2 E5") and ep["poster"] == "", ep
assert mv["kind"] == "movie" and mv["title"] == "The Green Valley" and mv["year"] == 2024, mv
assert mv["poster"] == "/api/tv/poster?id=k7", mv
print("test_tv_finished OK")
