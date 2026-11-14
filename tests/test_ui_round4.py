"""Tests for the UI round: size verdicts, release kinds, details links, compact dashboard,
window-state memory, SABnzbd placeholders, local inspector buttons, queue moves, result menus."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))

import json
import tempfile
import time
import types
from pathlib import Path

import nzb_command_center as ncc

G = 1024 ** 3

# ---------------- pure helpers ----------------
V = lambda title, gb: ncc.size_verdict({"title": title, "size_bytes": int(gb * G)})
assert V("Movie.2024.1080p.BluRay.x264-GRP", 0.3) == "tiny"
assert V("Movie.2024.1080p.BluRay.x264-GRP", 9) == "ok"
assert V("Movie.2024.2160p.UHD.BluRay.REMUX-GRP", 300) == "large"
assert V("Show.S01.1080p.WEB-DL-GRP", 40) == ""          # season pack: not judged
assert V("Movie.2024.1080p", 0) == ""                     # unknown size

K = lambda **kw: ncc.release_kind(kw)[0]
assert K(title="Show.S01E01.1080p", category="TV > HD") == "tv"
assert K(title="Movie.2020.1080p", category="Movies > HD") == "movie"
assert K(title="Artist - Album (2019) FLAC", category="Audio > Lossless") == "music"
assert K(title="Some.App.v2", category="PC > Software") == "app"
assert K(title="thing", category="Other") == "package"

assert ncc._details_link("https://ix.example/details/abc#comments", "") == "https://ix.example/details/abc"
assert ncc._details_link("", "javascript:alert(1)") == ""
print("helpers ok")

# ---------------- app ----------------
tmp = Path(tempfile.mkdtemp())
ncc.CONFIG_FILE = tmp / "c.json"; ncc.STATS_FILE = tmp / "s.json"; ncc.CAPS_CACHE_FILE = tmp / "caps.json"
ncc.DEFAULT_CONFIG["indexers"] = []; ncc.DEFAULT_CONFIG["nzb_folder"] = str(tmp)


def new_app():
    a = ncc.NzbManagerApp()
    a.after_cancel(a._auto_refresh_after_id)
    return a


def pump(app, secs=0.5):
    end = time.time() + secs
    while time.time() < end:
        app.update(); time.sleep(0.02)


app = new_app()
app.geometry("1200x800+40+30")
pump(app, 1.0)

# SABnzbd not configured -> placeholders instead of zeros/blank boxes
app._refresh_sab_all()
assert app.card_speed_val.cget("text") == "—"
texts = []
stack = list(app.queue_frame.winfo_children())
while stack:
    w = stack.pop(); stack.extend(w.winfo_children())
    try:
        texts.append(str(w.cget("text")))
    except Exception:
        pass
assert any("isn't connected" in t for t in texts), texts

# Local inspector buttons are disabled until a file is selected
assert app.local_send_btn.cget("state") == "disabled" and app.local_delete_btn.cget("state") == "disabled"
nzb = tmp / "x.nzb"
nzb.write_text('<?xml version="1.0"?><nzb xmlns="http://www.newzbin.com/DTD/2003/nzb"><file subject="a">'
               '<groups><group>a.b</group></groups><segments><segment bytes="5">x</segment></segments></file></nzb>')
app._inspect_nzb(nzb)
assert app.local_send_btn.cget("state") == "normal" and app.local_popout_btn.cget("state") == "normal"
app._clear_inspector()
assert app.local_delete_btn.cget("state") == "disabled"
print("placeholders + local buttons ok")

# Compact dashboard
app._toggle_dashboard(); pump(app, 0.3)
assert app._dashboard_compact and not app._telemetry_row.winfo_ismapped() and app._compact_strip.winfo_ismapped()
assert "NZBs today" in app.cs_nzbs.cget("text")
assert json.loads(ncc.CONFIG_FILE.read_text())["compact_dashboard"] is True
app._toggle_dashboard(); pump(app, 0.3)
assert app._telemetry_row.winfo_ismapped() and not app._compact_strip.winfo_ismapped()
app._toggle_dashboard()  # leave compact on: must be restored next launch
print("compact dashboard ok")

# Queue move up/down sends SABnzbd "switch" with the new position
calls = []
app.sab._request = lambda mode, extra=None, timeout=10.0: calls.append((mode, extra)) or {}
app._refresh_sab_all = lambda *a, **k: None
app._move_queue_item("SABnzbd_nzo_1", 3)
pump(app, 0.4)
assert calls == [("switch", {"value": "SABnzbd_nzo_1", "value2": "3"})], calls

# Result cards: right-click bound everywhere, menu actions
item = {
    "index": 0, "uid": "ix|u|Dune.Part.Two.2024.1080p.WEB-DL", "norm_key": "dune part two 2024", "indexer_name": "ix",
    "title": "Dune.Part.Two.2024.1080p.WEB-DL.DDP5.1-FLUX", "category": "Movies > HD", "cat_hint": "Movies > HD",
    "language": "English", "quality": "1080p", "tags": [], "passworded": False, "files_count": "", "size_bytes": 9 * G,
    "size_human": "9.0 GB", "age": "1d", "pub_ts": time.time(), "grabs": "", "grabs_int": 0, "cover_url": "",
    "nzb_url": "http://ix/get?apikey=k", "details_url": "https://ix/details/1", "mirror_count": 0,
}
app.current_search_results = [item]
app._apply_filters_and_render(reset_page=True)
pump(app, 0.3)
card = app.search_results_frame.winfo_children()[0]
bound = []
stack = [card]
while stack:
    w = stack.pop(); stack.extend(w.winfo_children())
    bound.append(bool(ncc.tk.Misc.bind(w, "<Button-3>")))
assert all(bound), f"{bound.count(False)} widgets without right-click"
app._copy_to_clipboard(item["title"], "copied")
assert app.clipboard_get() == item["title"]
app._set_result_selected(item, True)
assert item["uid"] in app.selected_search_uids
searched = []
app._on_search_button = lambda: searched.append(app.search_entry.get())
app._search_similar(item)
assert searched == ["Dune Part Two 2024"], searched
print("queue moves + result menu ok")

# Pill -> Settings opens scrolled to the indexers
app._open_settings("indexers"); pump(app, 0.6)
assert app._settings_dialog is not None and app._settings_dialog.winfo_exists()
app._settings_dialog.destroy()

# Window state + last tab survive a restart
app.tabs.set("Activity Log")
pump(app, 0.2)
app._on_close()
saved = json.loads(ncc.CONFIG_FILE.read_text())
assert saved["last_tab"] == "Activity Log" and saved["window_geometry"].startswith("1200x800"), saved
app2 = new_app(); pump(app2, 0.5)
assert app2.tabs.get() == "Activity Log" and app2._dashboard_compact
assert app2.geometry().startswith("1200x800"), app2.geometry()
app2._on_close()
# an off-screen position (unplugged monitor) is ignored
cfg = json.loads(ncc.CONFIG_FILE.read_text()); cfg["window_geometry"] = "1200x800+99999+99999"
ncc.CONFIG_FILE.write_text(json.dumps(cfg))
app3 = new_app(); pump(app3, 0.3)
assert "+99999" not in app3.geometry(), app3.geometry()
app3._on_close()
print("window state ok")
print("UI ROUND 4 TESTS PASSED")
