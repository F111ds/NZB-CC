"""Search-result artwork on the phone: kind/resolution placeholders, the indexer's own cover,
TVmaze / iTunes lookups (stubbed - no internet), caching, and what the server refuses to fetch."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))
import json, tempfile, threading, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import mobile_server as m

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100
JPEG = b"\xff\xd8\xff\xe0" + b"j" * 100

# ---- classification (mirrors the desktop app) --------------------------------------------
assert m.release_kind("Dune.Part.Two.2024.2160p.UHD.BluRay", "Movies > UHD") == "movie"
assert m.release_kind("Severance.S02E04.1080p.WEB", "") == "tv"
assert m.release_kind("Artist-Album-2024-FLAC", "Audio > Lossless") == "music"
assert m.release_kind("Some.Ebook.Collection", "Books > Ebook") == "book"
assert m.release_kind("Game.Title-CODEX", "PC > Games") == "game"
assert m.release_kind("Tool.v2.1", "PC > 0day") == "app"
assert m.release_kind("random_thing", "") == "package"
assert m.release_resolution("Dune.2021.2160p") == "4K" and m.release_resolution("X.1080p") == "1080p" and m.release_resolution("X") == ""
assert m.artwork_query("Dune.Part.Two.2024.2160p", "Movies") == ("Dune Part Two", "2024", "movie")
assert m.artwork_query("Severance.S02E04.1080p", "") == ("Severance", "", "tv")
assert m.artwork_query("The.Bear.2022.S03E01", "") == ("The Bear", "", "tv")
assert m.artwork_query("Artist-Album-FLAC", "Audio > Lossless") == ("", "", "")      # no poster service for music

# ---- fake indexer: covers on its own host, one on another private host, one music release ---
hits = []


class Indexer(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        hits.append(path)
        if path == "/cover.png":
            b, ct = PNG, "image/png"
        elif path == "/notimage":
            b, ct = b"<html>no</html>", "text/html"
        else:
            port = self.server.server_address[1]
            items = [("Dune.Part.Two.2024.2160p.WEB", "Movies > UHD", f"http://127.0.0.1:{port}/cover.png"),
                     ("Fake.Cover.2020.1080p", "Movies > HD", f"http://127.0.0.1:{port}/notimage"),
                     ("Elsewhere.2019.720p", "Movies > HD", "http://10.255.255.1/cover.jpg"),
                     ("Severance.S02E04.1080p.WEB", "TV > HD", ""),
                     ("Artist-Album-FLAC", "Audio > Lossless", "")]
            xml = "".join(f'<item><title>{t}</title><category>{c}</category><link>http://127.0.0.1:{port}/get/{i}</link>'
                          + (f'<newznab:attr name="coverurl" value="{cv}"/>' if cv else "")
                          + '<newznab:attr name="poster" value="someone@example.invalid"/></item>'
                          for i, (t, c, cv) in enumerate(items))
            b, ct = f'<?xml version="1.0"?><rss xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/"><channel>{xml}</channel></rss>'.encode(), "application/xml"
        self.send_response(200); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)


def serve(h):
    s = ThreadingHTTPServer(("127.0.0.1", 0), h); threading.Thread(target=s.serve_forever, daemon=True).start(); return s
idx, app = serve(Indexer), serve(m.MobileHandler)
tmp = Path(tempfile.mkdtemp())
(tmp / "nzb_manager_config.json").write_text(json.dumps({"sab_url": "127.0.0.1:9", "sab_api_key": "plainsabkey123",
    "indexers": [{"name": "Local", "url": f"http://127.0.0.1:{idx.server_address[1]}/api", "api_key": "idxkey12345"}]}))
m.CONFIG_FILE, m.SETTINGS_FILE = tmp / "nzb_manager_config.json", tmp / "mobile_settings.json"
m._cfg_state.update(mtime=None, data=None)
m._token_bytes = b"testtoken"
base = f"http://127.0.0.1:{app.server_address[1]}"

# Lookups go to TVmaze / iTunes in real use; here they're stubbed and their images come from "img.test".
lookups = []
real_fetch, real_public = m.http_fetch, m.host_is_public
def fake_fetch(url, **kw):
    if url.startswith("https://api.tvmaze.com/"):
        lookups.append(url); return json.dumps({"image": {"medium": "https://img.test/severance.jpg"}}).encode()
    if url.startswith("https://itunes.apple.com/"):
        lookups.append(url); return json.dumps({"results": []}).encode()
    if url == "https://img.test/severance.jpg":
        return JPEG
    return real_fetch(url, **kw)
m.http_fetch = fake_fetch
m.host_is_public = lambda h: h == "img.test" or real_public(h)


def get(path, body=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Mobile-Token": "testtoken", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r: return r.status, r.headers.get("Content-Type"), r.read()
    except urllib.error.HTTPError as e: return e.code, None, e.read()


st, _, body = get("/api/search", {"query": "anything"})
assert st == 200, body
res = {x["title"]: x for x in json.loads(body)["results"]}
dune, sev, music = res["Dune.Part.Two.2024.2160p.WEB"], res["Severance.S02E04.1080p.WEB"], res["Artist-Album-FLAC"]
assert (dune["kind"], dune["res"], dune["art"]) == ("movie", "4K", True), dune
assert (sev["kind"], sev["res"], sev["art"]) == ("tv", "1080p", True), sev
assert (music["kind"], music["art"]) == ("music", False), music
assert "cover_url" not in dune and "nzb_url" not in dune                      # the phone never sees URLs

# The indexer's own cover (same host as the indexer) is served, then cached.
st, ct, img = get("/api/search/art?id=" + dune["id"])
assert st == 200 and ct == "image/png" and img == PNG
n = hits.count("/cover.png")
assert get("/api/search/art?id=" + dune["id"])[0] == 200 and hits.count("/cover.png") == n

# No cover from the indexer -> TVmaze lookup for a show.
st, ct, img = get("/api/search/art?id=" + sev["id"])
assert st == 200 and ct == "image/jpeg" and img == JPEG and any("tvmaze" in u and "Severance" in u for u in lookups), lookups

# A "cover" that isn't an image, and one on another private address, are refused (then the
# iTunes lookup finds nothing) -> 404, and the miss is remembered instead of retried.
lookups.clear()
assert get("/api/search/art?id=" + res["Fake.Cover.2020.1080p"]["id"])[0] == 404
assert get("/api/search/art?id=" + res["Elsewhere.2019.720p"]["id"])[0] == 404
assert len([u for u in lookups if "itunes" in u]) == 2
assert get("/api/search/art?id=" + res["Elsewhere.2019.720p"]["id"])[0] == 404 and len(lookups) == 2   # cached miss

# Music: nothing to look up. Unknown / malformed ids: 404 without fetching anything.
assert get("/api/search/art?id=" + music["id"])[0] == 404
assert get("/api/search/art?id=nope-not-a-result")[0] == 404 and get("/api/search/art?id=../../x")[0] == 404
req = urllib.request.Request(base + "/api/search/art?id=" + dune["id"])
try: urllib.request.urlopen(req, timeout=5); raise AssertionError("art without the access code")
except urllib.error.HTTPError as e: assert e.code == 401

# The page draws the placeholder and asks for the poster by result id.
with urllib.request.urlopen(base + "/", timeout=5) as r:
    page = r.read().decode()
assert "const KINDS=" in page and "'/api/search/art?id='" in page
m.http_fetch, m.host_is_public = real_fetch, real_public
print("SEARCH ART TESTS PASSED")
