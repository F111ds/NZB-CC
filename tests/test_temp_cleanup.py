"""System Command Center temp cleanup: never follows links out of the temp folder."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))
import os, tempfile, time
from pathlib import Path
import system_monitor as sm

base = Path(tempfile.mkdtemp())
temp, data = base / "temp", base / "data"
(temp / "sub").mkdir(parents=True)
data.mkdir()
old = time.time() - 3 * 86400
for f in (temp / "old.txt", temp / "sub" / "old2.txt", data / "precious.txt"):
    f.write_text("x")
    os.utime(f, (old, old))
(temp / "new.txt").write_text("x")
os.symlink(data, temp / "link", target_is_directory=True)   # stands in for an NTFS junction

dry = sm.clean_temp_files(dry_run=True, folders=[temp])
assert dry["files"] == 2, dry                               # the file behind the link isn't counted
res = sm.clean_temp_files(dry_run=False, folders=[temp])
assert res["files"] == 2 and res["failed"] == 0, res
assert (data / "precious.txt").exists()                     # not followed through the link
assert (temp / "new.txt").exists()                          # too new
assert not (temp / "old.txt").exists() and not (temp / "sub").exists()  # old file gone, empty folder removed
assert sm.clean_temp_files(dry_run=False, folders=[temp / "link"])["files"] == 0   # a link as the root: skipped
assert (data / "precious.txt").exists()
print("temp cleanup OK")
