import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]  # the project folder
_sys.path.insert(0, str(ROOT))
import sys, types
import media_server as ms
import nzb_command_center as ncc

# --- path map ---
pm = ms.parse_path_map(r"/data=X:\Other; /data/movies=D:\Movies")
assert pm[0][0] == "/data/movies", pm
assert ms.apply_path_map("/data/movies/Heat (1995)/Heat.mkv", pm) == "D:/Movies/Heat (1995)/Heat.mkv"
assert ms.apply_path_map("/elsewhere/x.mkv", pm) == "/elsewhere/x.mkv"
assert not ms._is_distinctive_name("s01e01.mkv") and ms._is_distinctive_name("heat.1995.1080p.mkv")

# --- audit ---
P = types.SimpleNamespace(path_map_raw=r"/data/tv=D:\TV; /data/movies=D:\Movies")
P._movies = [{"title": "Heat", "year": "1995", "file": "/data/movies/Heat (1995)/Heat.1995.mkv"}]
P._episodes = [  # two different shows with same generic file name
    {"showtitle": "Show A", "season": 1, "episode": 1, "file": "/data/tv/Show A/Season 01/S01E01.mkv"},
    {"showtitle": "Ghost", "season": 1, "episode": 1, "file": "/data/tv/Ghost/Season 01/S01E01.mkv"},
]
disk = [
    {"path": r"D:\Movies\Heat (1995)\Heat.1995.mkv", "title": "Heat"},
    {"path": r"D:\TV\Show A\Season 01\S01E01.mkv", "title": "A1"},
    {"path": r"D:\TV\Show B\Season 01\S01E01.mkv", "title": "B1"},  # not on server
]
ms.MediaServerPanel._compute_audit(P, disk)
assert [m["title"] for m in P._audit_missing] == ["B1"], P._audit_missing
assert [o["title"] for o in P._audit_orphaned] == ["Ghost — S01E01"], P._audit_orphaned
# without a path map, generic names must NOT cross-match (old code would hide B1)
P.path_map_raw = ""
ms.MediaServerPanel._compute_audit(P, disk)
assert "B1" in [m["title"] for m in P._audit_missing]

# --- Jellyfin paging + RecursiveItemCount ---
jc = ms.JellyfinClient("http://x", "k"); jc._user_id_cache = "u"
calls = []
def fake(path, method="GET", params=None, timeout=10):
    calls.append(params)
    s, n = params["StartIndex"], params["Limit"]
    total = 2500
    return {"Items": [{"Name": f"m{i}", "RecursiveItemCount": 10, "UserData": {"UnplayedItemCount": 4}}
                      for i in range(s, min(s + n, total))], "TotalRecordCount": total}
jc._request = fake
assert len(jc.get_movies()) == 2500 and len(calls) == 3
assert "RecursiveItemCount" in calls[0]["Fields"]
shows = jc.get_tv_shows()
assert shows[0]["episodes"] == 10 and shows[0]["watched_episodes"] == 6

# --- secrets kept when undecryptable ---
app = types.SimpleNamespace(_undecryptable={"sab_api_key": "dpapi:BLOB", ncc.NzbManagerApp._indexer_secret_id("https://ix/api"): "dpapi:IX"})
keep = lambda f, v: ncc.NzbManagerApp._protect_or_keep(app, f, v)
assert keep("sab_api_key", "") == "dpapi:BLOB"
assert keep(ncc.NzbManagerApp._indexer_secret_id("https://IX/api/"), "") == "dpapi:IX"
new = keep("sab_api_key", "newkey")
assert new != "dpapi:BLOB" and "sab_api_key" not in app._undecryptable
assert keep("kodi_password", "") == ""

# --- settings merge keeps live keys ---
class V:
    def __init__(s, v): s.v = v
    def get(s): return s.v
live = types.SimpleNamespace(config_data={"media_folders": ["D:/NewFolder"], "kodi_url": "http://kodi", "sab_url": "old"},
                             auto_refresh_var=V(True), auto_archive_var=V(False))
written = {}
def stop(cfg): written.update(cfg); raise StopIteration
live._write_config_file = stop
try: ncc.NzbManagerApp._save_config(live, {"sab_url": "new", "sab_api_key": "k", "indexers": [], "nzb_folder": "f"})
except StopIteration: pass
assert written["media_folders"] == ["D:/NewFolder"] and written["kodi_url"] == "http://kodi" and written["sab_url"] == "new"
print("ALL TESTS PASSED")

# --- different roots, no path map: same-named episodes still resolve by longer ending ---
P.path_map_raw = ""
ms.MediaServerPanel._compute_audit(P, disk)
assert [m["title"] for m in P._audit_missing] == ["B1"], P._audit_missing
assert [o["title"] for o in P._audit_orphaned] == ["Ghost — S01E01"], P._audit_orphaned
print("NO-MAP SUFFIX TEST PASSED")
