"""TV screensaver photos: listed from one folder, served only by id, nothing outside it."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))
import tempfile
from pathlib import Path
import mobile_server as m

tmp = Path(tempfile.mkdtemp())
pics = tmp / "My Pictures"
(pics / "Trip").mkdir(parents=True)
(pics / ".hidden").mkdir()
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100
(pics / "a.jpg").write_bytes(JPEG)
(pics / "Trip" / "b.PNG").write_bytes(PNG)
(pics / "notes.txt").write_bytes(b"x")
(pics / "fake.jpg").write_bytes(b"not an image")
(pics / ".hidden" / "c.jpg").write_bytes(JPEG)
(tmp / "outside.jpg").write_bytes(JPEG)
m.TV_PHOTOS_FILE = tmp / "tv_photos_folder.txt"
m.TV_PHOTOS_FILE.write_text(f'"{pics}"\n', encoding="utf-8")

lst = m.tv_photos_list()
names = sorted(x["name"] for x in lst["items"])
assert lst["folder"] == "My Pictures" and names == ["a", "b", "fake"], lst
ids = {x["name"]: x["id"] for x in lst["items"]}
assert m.tv_photo(ids["a"]) == JPEG and m.tv_photo(ids["b"]) == PNG
assert m.tv_photo(ids["fake"]) is None            # not really an image
assert m.tv_photo("0123456789abcdef") is None     # unknown id
for bad in ("../outside.jpg", "a.jpg", "ABCDEF0123456789", ""):
    try:
        m.tv_photo(bad); raise AssertionError(bad)
    except ValueError:
        pass
print("test_tv_photos OK")
