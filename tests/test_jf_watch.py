"""Jellyfin rescans when files are deleted or added in its folders (mobile_server.JellyfinWatch)."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))

import os
import tempfile
import time

import mobile_server as ms

lib = tempfile.mkdtemp()
show = os.path.join(lib, "Widow's Bay", "Season 01")
os.makedirs(show)
for n in (1, 2, 3):
    open(os.path.join(show, f"Widow's Bay - S01E0{n}.mkv"), "w").close()
open(os.path.join(show, "poster.jpg"), "w").close()                 # not a video: ignored
os.makedirs(os.path.join(lib, "Other", "trickplay"))
open(os.path.join(lib, "Other", "trickplay", "x.mp4"), "w").close()  # Jellyfin's own folders: ignored

assert ms.video_files([lib]) == {os.path.join(show, f"Widow's Bay - S01E0{n}.mkv") for n in (1, 2, 3)}

calls = []
ms.jellyfin_configured = lambda: True
ms.jf_library_folders = lambda conf=None: [lib]
ms.jf_request = lambda path, *a, **k: calls.append((path, k.get("post"))) or {}

w = ms.JellyfinWatch()
assert w.check() is None and calls == []                 # the first look only remembers
assert w.check() is None and calls == []                 # nothing changed: no rescan
os.remove(os.path.join(show, "Widow's Bay - S01E01.mkv"))
r = w.check()
assert r and r["gone"] == 1 and r["new"] == 0 and calls == [("/Library/Refresh", True)], (r, calls)
open(os.path.join(show, "Widow's Bay - S01E04.mkv"), "w").close()
assert w.check() is None and len(calls) == 1             # within 10 minutes of the last rescan: waits
w.last_scan -= ms.JF_WATCH_MIN_GAP_S + 1
os.remove(os.path.join(show, "Widow's Bay - S01E02.mkv"))
r = w.check()
assert r and r["gone"] == 1 and len(calls) == 2, (r, calls)
ms.jellyfin_configured = lambda: False
assert w.check() is None and w.last is None              # Jellyfin not connected: nothing to do
print("jellyfin watch ok")
print("\nALL JELLYFIN WATCH TESTS PASSED")
