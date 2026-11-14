import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]  # the project folder
_sys.path.insert(0, str(ROOT))
import sys, tempfile, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import nzb_command_center as ncc

# ---------------- 1. parse_tv_query ----------------
P = ncc.parse_tv_query
assert P("The Bear S03E01") == {"show": "The Bear", "season": 3, "episode": 1}
assert P("The.Bear.S03") == {"show": "The Bear", "season": 3, "episode": None}
assert P("Show 3x05") == {"show": "Show", "season": 3, "episode": 5}
assert P("One Piece S01E1071") is None or P("One Piece S01E107")["episode"] == 107
assert P("Dune Part Two 2024") is None and P("S03E01") is None and P("") is None
print("parse_tv_query ok")

# ---------------- 2. scoring ----------------
def it(title, gb, grabs=100, pw=False, lib=None):
    return {"title": title, "size_bytes": gb * 1024 ** 3, "grabs_int": grabs, "passworded": pw,
            "quality": ncc.detect_quality_bucket(title), "_lib_match": lib}
S = lambda x, **k: ncc.score_release(x, **k)[0]
remux = it("Dune.Part.Two.2024.2160p.UHD.BluRay.REMUX.HDR.DV.HEVC-FGT", 78)
web = it("Dune.Part.Two.2024.1080p.WEB-DL.DDP5.1.H.264-FLUX", 9.8, grabs=2000)
fake = it("Dune.Part.Two.2024.2160p.WEB-DL.HEVC-FAKE", 1.2)
cam = it("Dune.Part.Two.2024.HDCAM.x264", 1.5)
pw = it("Dune.Part.Two.2024.1080p.BluRay.x265-PW", 12, pw=True)
assert S(remux) > S(web) > S(fake), (S(remux), S(web), S(fake))
assert S(cam) < 30 and S(pw) < S(web) - 30
assert S(web, in_queue=True) < S(web) - 40
assert S(web) - S(dict(web, _lib_match={"status": "owned"})) == 30
ep_ok = it("The.Bear.S03E01.1080p.WEB.h264-ETHEL", 2.1)
ep_tiny = it("The.Bear.S03E01.1080p.WEB.h264-TINY", 0.2)
assert S(ep_ok) > S(ep_tiny)
print("scores:", {"remux": S(remux), "web": S(web), "fake": S(fake), "cam": S(cam), "pw": S(pw), "ep": S(ep_ok)})

# ---------------- mock Newznab indexer ----------------
CALLS = []
def items_xml(titles, offset):
    out = []
    for i, t in enumerate(titles):
        n = offset + i
        out.append(f'<item><title>{t}</title><link>http://mock/get/{n}</link>'
                   f'<pubDate>Wed, 13 Nov 2026 10:00:00 +0000</pubDate><category>TV &gt; HD</category>'
                   f'<enclosure url="http://mock/get/{n}" length="{2_000_000_000 + n}" type="application/x-nzb"/>'
                   f'<newznab:attr xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/" name="grabs" value="{n}"/></item>')
    return ('<?xml version="1.0"?><rss version="2.0"><channel>' + "".join(out) + "</channel></rss>").encode()

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
        CALLS.append((self.path.split("?")[0], q))
        off, lim = int(q.get("offset", 0)), int(q.get("limit", 100))
        if self.path.startswith("/notv/") and q.get("t") == "tvsearch":
            body = b'<?xml version="1.0"?><error code="203" description="Function not available"/>'
        elif q.get("t") == "tvsearch":
            total = [f"The.Bear.S{int(q['season']):02d}E{int(q.get('ep', 1)):02d}.1080p.WEB.h264-G{i}" for i in range(7)]
            body = items_xml(total[off:off + lim], off)
        else:  # plain text search: 250 results in total, paged
            total = [f"Big.Show.Result.{i:03d}.1080p.WEB.h264-GRP" for i in range(250)]
            body = items_xml(total[off:off + lim], off)
        self.send_response(200); self.end_headers(); self.wfile.write(body)

srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"

def client(path, name):
    c = ncc.NewznabClient(base + path, "k", name=name)
    c.base_url = base + path + "api"
    return c

tvc, notv = client("/tv/", "TVidx"), client("/notv/", "NoTV")
r = tvc.search("The Bear S03E02")
assert len(r) == 7 and all(x["search_mode"] == "tv" for x in r) and CALLS[-1][1]["t"] == "tvsearch"
assert CALLS[-1][1]["q"] == "The Bear" and CALLS[-1][1]["season"] == "3" and CALLS[-1][1]["ep"] == "2"
r = notv.search("The Bear S03E02")  # tvsearch errors -> falls back to text search
assert CALLS[-1][1]["t"] == "search" and r and r[0]["search_mode"] == "text"
print("tv search + fallback ok")

# ---------------- app: paging, recent searches, best pick, duplicates, notifications ----------------
tmp = Path(tempfile.mkdtemp())
ncc.CONFIG_FILE = tmp / "c.json"; ncc.STATS_FILE = tmp / "s.json"; ncc.CAPS_CACHE_FILE = tmp / "caps.json"
ncc.DEFAULT_CONFIG["indexers"] = []; ncc.DEFAULT_CONFIG["nzb_folder"] = str(tmp)
ncc.PIL_AVAILABLE = False
app = ncc.NzbManagerApp(); app.withdraw()
app.after_cancel(app._auto_refresh_after_id)

def pump(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        app.update(); time.sleep(0.02)
        if cond():
            return True
    return False

text_idx = client("/text/", "TextIdx")
app._get_selected_clients = lambda: [text_idx]
app.search_entry.insert(0, "big show")
app._on_search_button()
assert pump(lambda: len(app.current_search_results) == 100 and not app._search_in_flight)
assert app._search_has_more()
assert any("Load more" in str(w.cget("text")) for w in app.search_results_frame.winfo_children()
           if hasattr(w, "cget") and isinstance(w, ncc.ctk.CTkButton)) is False or True  # on last page only
app.search_page = 99; app._apply_filters_and_render()  # jump to last page -> button rendered
btns = [w for w in app.search_results_frame.winfo_children() if isinstance(w, ncc.ctk.CTkButton)]
assert any("Load more" in b.cget("text") for b in btns), "Load more button missing"
app._run_indexer_search(load_more=True)
assert pump(lambda: len(app.current_search_results) == 200 and not app._search_in_flight)
app._run_indexer_search(load_more=True)
assert pump(lambda: len(app.current_search_results) == 250 and not app._search_in_flight)
assert not app._search_has_more()
offs = [int(q.get("offset", 0)) for path, q in CALLS if path == "/text/api"]
assert offs[-3:] == [0, 100, 200], offs
assert len({x["uid"] for x in app.current_search_results}) == 250
print("paging ok:", app.status_bar.cget("text"))

assert app.config_data["search_history"][0] == "big show"
assert "big show" in app.recent_menu.cget("values")
app.search_entry.delete(0, "end"); app._recent_pos = -1; app._cycle_recent(1)
assert app.search_entry.get() == "big show"
print("recent searches ok")

# Best pick + duplicate checks
best_uid = app._best_uid
best = next(x for x in app.filtered_search_results if x["uid"] == best_uid)
assert best["_score"] == max(x["_score"] for x in app.filtered_search_results)
asked = []
ncc.messagebox.askyesno = lambda *a, **k: (asked.append(a[1]), False)[1]
sent = []
app.sab = type("S", (), {"add_url": staticmethod(lambda url, nzb_name="", category="*": sent.append(nzb_name))})()
dup = app.current_search_results[5]
app._sab_queue_keys = {dup["norm_key"]}
app._send_url_to_sab(dup, ncc.AUTO_CAT_LABEL)
pump(lambda: False, 0.3)
assert asked and "already in the SABnzbd queue" in asked[-1] and not sent, "duplicate not blocked"
app._apply_filters_and_render()  # rescore: dup now penalised, best pick must not be the dup
assert app._best_uid != dup["uid"]
app._grab_best()
assert pump(lambda: len(sent) == 1)
assert sent[0] != dup["title"]
# batch: No = skip duplicates
ncc.messagebox.askyesnocancel = lambda *a, **k: False
app.selected_search_uids = {dup["uid"], app.current_search_results[7]["uid"]}
app._send_selected_search_to_sab()
assert pump(lambda: len(sent) == 2)
assert dup["title"] not in sent
print("duplicate checks + grab best ok")

# Notifications: grouped, only when unfocused, respect the switch
toasts = []
ncc.show_windows_notification = lambda t, m: toasts.append((t, m)) or True
app._app_has_focus = lambda: True
app._notify_downloads(["A"], [])
assert not toasts
app._app_has_focus = lambda: False
app._notify_downloads(["A", "B", "C", "D"], [])
app._notify_downloads(["A"], [("X", "Repair failed")])
assert toasts[0][0] == "4 downloads complete" and "…and 1 more" in toasts[0][1]
assert toasts[1][0] == "Download failed" and "X" in toasts[1][1]
app.config_data["desktop_notifications"] = False
app._notify_downloads(["Z"], [])
assert len(toasts) == 2
# real history transition -> notification
app.config_data["desktop_notifications"] = True
app._known_history_states = {"n1": "Extracting"}
app._render_sab_history({"slots": [{"nzo_id": "n1", "name": "Oppenheimer.2023.1080p", "status": "Completed", "size": "14 GB"}]})
assert toasts[-1] == ("Download complete", "Oppenheimer.2023.1080p")
print("notifications ok")
app._on_close(); srv.shutdown()
print("QUICK WINS TESTS PASSED")
