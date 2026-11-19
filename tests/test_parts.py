"""mobile_server.py's page and icons live in mobile_page.py and mobile_icons.py.

Checks they're picked up, and that the server still loads (with a placeholder page and a plain icon, and
says what's missing) when someone updates only mobile_server.py.
"""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))

import subprocess
import sys

import mobile_icons
import mobile_page
import mobile_server as m

assert m.MISSING_PARTS == []
assert m.PAGE is mobile_page.PAGE and len(m.PAGE) > 100_000
assert m.ICON_PNG[:8] == b"\x89PNG\r\n\x1a\n" and len(m.ICON_PNG) > 5000
assert m.KODI_ICON_PNG[:4] == b"\x89PNG" and m.KODI_ICON_MASKABLE_PNG[:4] == b"\x89PNG"
assert "activeTab='kodi'" in m.KODI_PAGE and "<title>Kodi</title>" in m.KODI_PAGE
print("parts found ok")

# Without the parts: the server module still loads and builds both pages.
code = r"""
import sys, importlib.abc
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name in ("mobile_page", "mobile_icons"):
            raise ImportError("blocked for the test: " + name)
sys.meta_path.insert(0, Block())
sys.path.insert(0, sys.argv[1])
import mobile_server as m
assert sorted(m.MISSING_PARTS) == ["mobile_icons.py", "mobile_page.py"], m.MISSING_PARTS
assert "mobile_page.py" in m.PAGE and "update_all.ps1" in m.PAGE
assert m.ICON_PNG[:4] == b"\x89PNG"
assert "<title>Kodi</title>" in m.KODI_PAGE
print("ok")
"""
r = subprocess.run([sys.executable, "-c", code, str(ROOT)], capture_output=True, text=True, timeout=120)
assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stdout + r.stderr
print("missing parts ok")
print("\nALL PARTS TESTS PASSED")
