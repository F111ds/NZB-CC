"""Phone downloads pick a SABnzbd category per release ("Auto (match)"), like the desktop app,
so they land in the movies/ tv/ ... folders instead of SABnzbd's default one."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))
import json, tempfile, threading, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import mobile_server as m

# ---- matching (same rules as the desktop app) -------------------------------------------
CATS = ["movies", "tv", "audio", "software", "books"]
assert m.match_sab_category(CATS, "Movies > UHD", "Dune.Part.Two.2024.2160p") == "movies"
assert m.match_sab_category(CATS, "TV > HD", "Severance.S02E04.1080p") == "tv"
assert m.match_sab_category(CATS, "", "Severance.S02E04.1080p.WEB") == "tv"            # title only
assert m.match_sab_category(CATS, "", "The.Brutalist.2024.1080p.WEB.H264") == "movies"
assert m.match_sab_category(CATS, "5070", "Some.Anime.01") == "tv"                    # anime falls back to tv
assert m.match_sab_category(CATS, "Audio > Lossless", "Artist - Album") == "audio"
assert m.match_sab_category(CATS, "PC > 0day", "Tool.v5") == "software"
assert m.match_sab_category(CATS, "2040", "x") == "movies"                            # newznab number
assert m.match_sab_category(CATS, "", "misc_upload_0342") == ""                       # no guess -> default
assert m.match_sab_category([], "Movies", "Dune.2021.1080p") == ""
assert m.match_sab_category(["Films HD", "Series"], "Movies > HD", "Dune.2021.1080p") == "Films HD"

# ---- end to end: what the phone's add sends to SABnzbd ----------------------------------
sent = []
NZB = b'<?xml version="1.0"?><nzb xmlns="http://www.newzbin.com/DTD/2003/nzb"><file subject="x"></file></nzb>'


def reply(h, body, ct="application/json"):
    b = body if isinstance(body, bytes) else json.dumps(body).encode()
    h.send_response(200); h.send_header("Content-Type", ct); h.send_header("Content-Length", str(len(b))); h.end_headers(); h.wfile.write(b)


class Sab(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
        reply(self, {"categories": ["*"] + CATS} if q.get("mode") == "get_cats" else {"status": True})
    def do_POST(self):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        sent.append(q.get("cat"))
        reply(self, {"status": True, "nzo_ids": ["SABnzbd_nzo_x"]})


class Indexer(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/get/"):
            return reply(self, NZB, "application/x-nzb")
        port = self.server.server_address[1]
        items = [("Dune.Part.Two.2024.2160p.WEB", "Movies > UHD"), ("Severance.S02E04.1080p.WEB", "TV > HD"),
                 ("misc_upload_0342", "Other")]
        xml = "".join(f'<item><title>{t}</title><category>{c}</category><link>http://127.0.0.1:{port}/get/{i}</link></item>'
                      for i, (t, c) in enumerate(items))
        reply(self, f'<?xml version="1.0"?><rss><channel>{xml}</channel></rss>'.encode(), "application/xml")


def serve(h):
    s = ThreadingHTTPServer(("127.0.0.1", 0), h); threading.Thread(target=s.serve_forever, daemon=True).start(); return s
sab, idx, app = serve(Sab), serve(Indexer), serve(m.MobileHandler)
tmp = Path(tempfile.mkdtemp())
(tmp / "nzb_manager_config.json").write_text(json.dumps({"sab_url": f"127.0.0.1:{sab.server_address[1]}", "sab_api_key": "plainsabkey123",
    "indexers": [{"name": "Local", "url": f"http://127.0.0.1:{idx.server_address[1]}/api", "api_key": "idxkey12345"}]}))
m.CONFIG_FILE, m.SETTINGS_FILE = tmp / "nzb_manager_config.json", tmp / "mobile_settings.json"
m._cfg_state.update(mtime=None, data=None)
m._cat_cache.update(ts=0.0, cats=[])
m._token_bytes = b"testtoken"
base = f"http://127.0.0.1:{app.server_address[1]}"


def post(path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"X-Mobile-Token": "testtoken", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


res = {x["title"]: x["id"] for x in post("/api/search", {"query": "anything"})["results"]}
dune, sev, misc = res["Dune.Part.Two.2024.2160p.WEB"], res["Severance.S02E04.1080p.WEB"], res["misc_upload_0342"]

assert post("/api/add", {"id": dune, "category": ""})["category"] == "movies" and sent[-1] == "movies", sent
assert post("/api/add", {"id": sev})["category"] == "tv" and sent[-1] == "tv", sent                 # no category = Auto
assert post("/api/add", {"id": misc, "category": ""})["category"] == "" and sent[-1] is None, sent  # no match: SAB default
assert post("/api/add", {"id": dune, "category": "*"})["category"] == "" and sent[-1] is None, sent # explicit Default
assert post("/api/add", {"id": sev, "category": "movies"})["category"] == "movies" and sent[-1] == "movies"  # chosen wins

# Batch: each release gets its own category.
sent.clear()
d = post("/api/add-batch", {"ids": [dune, sev], "category": ""})
assert d["ok"] and sorted(x["category"] for x in d["added"]) == ["movies", "tv"] and sorted(sent) == ["movies", "tv"], (d, sent)

with urllib.request.urlopen(base + "/", timeout=5) as r:
    page = r.read().decode()
assert '<option value="">Auto (match)</option><option value="*">Default</option>' in page
print("AUTO CATEGORY TESTS PASSED")
