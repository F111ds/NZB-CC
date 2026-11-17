"""Desktop app: saving indexer NZBs never overwrites another save or exposes a half-written file."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))
import tempfile, threading
from pathlib import Path
import nzb_command_center as ncc

folder = Path(tempfile.mkdtemp())

# Two saves of the same title started together get different names (the bug: both got "Movie.nzb").
a = ncc.unique_nzb_path(folder, "Some.Movie.2024", reserve=True)
b = ncc.unique_nzb_path(folder, "Some.Movie.2024", reserve=True)
assert a.name == "Some.Movie.2024.nzb" and b.name == "Some.Movie.2024 (2).nzb", (a, b)
ncc.release_nzb_path(a)
assert ncc.unique_nzb_path(folder, "Some.Movie.2024").name == "Some.Movie.2024.nzb"   # free again once released
ncc.release_nzb_path(b)

# The download is written as ".nzb.part" and only then renamed to ".nzb".
seen_during_write = []
real_fetch = ncc.NewznabClient.fetch_nzb_bytes
real_write = Path.write_bytes


def spy_write(self, data):
    seen_during_write.append(sorted(p.name for p in folder.iterdir()))
    return real_write(self, data)


ncc.NewznabClient.fetch_nzb_bytes = staticmethod(lambda url: b"<nzb>ok</nzb>")
Path.write_bytes = spy_write
try:
    dest = ncc.unique_nzb_path(folder, "Show S01E01", reserve=True)
    out = ncc.NewznabClient.download_nzb("https://indexer.example/x", dest)
finally:
    Path.write_bytes = real_write
assert out == dest and dest.read_bytes() == b"<nzb>ok</nzb>"
assert "Show S01E01.nzb" not in seen_during_write[0]                       # not visible while being written
assert not any(p.name.endswith(".part") for p in folder.iterdir())        # temp file gone
ncc.release_nzb_path(dest)

# A failed write leaves no .part file and no .nzb behind.
def boom(self, data):
    raise OSError("disk full")


Path.write_bytes = boom
try:
    d2 = ncc.unique_nzb_path(folder, "Broken", reserve=True)
    try:
        ncc.NewznabClient.download_nzb("https://indexer.example/y", d2)
        raise AssertionError("no error")
    except OSError:
        pass
finally:
    Path.write_bytes = real_write
    ncc.NewznabClient.fetch_nzb_bytes = real_fetch
    ncc.release_nzb_path(d2)
assert not (folder / "Broken.nzb").exists() and not (folder / "Broken.nzb.part").exists()

# Many threads at once: every name unique.
names = []
lock = threading.Lock()


def grab():
    p = ncc.unique_nzb_path(folder, "Race", reserve=True)
    with lock:
        names.append(p.name)


ts = [threading.Thread(target=grab) for _ in range(20)]
[t.start() for t in ts]
[t.join() for t in ts]
assert len(set(names)) == 20, names
print("nzb save OK")
