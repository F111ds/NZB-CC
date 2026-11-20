"""What's on the TV: Kodi's paused player doesn't count when another app (YouTube) is in front."""
import sys as _sys
import time
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))

import hal_server as hs
import mobile_server as m

assert hs._tv_front_parse("  mResumedActivity: ActivityRecord{1a2b u0 com.google.android.youtube.tv/com.google.android.apps.youtube.tv.activity.ShellActivity t12}")[0] == "com.google.android.youtube.tv"
assert hs._tv_front_parse("  topResumedActivity=ActivityRecord{9 u0 com.gathin.tvhome/.HalActivity t3}") == ("com.gathin.tvhome", "com.gathin.tvhome.HalActivity")
assert hs._tv_front_parse("") == ("", "") and hs._tv_front_parse("error: device offline") == ("", "")


def front(pkg, act=""):
    hs._tv_front.update(pkg=pkg, activity=act or pkg + ".Main", t=time.time(), busy=False)
    return hs.tv_front()


assert front("com.google.android.youtube.tv")["app"] == "YouTube"
assert front("org.xbmc.kodi")["kodi"] is True
assert front("com.gathin.tvhome", "com.gathin.tvhome.HalActivity")["hal"] is True
assert front("com.gathin.tvhome", "com.gathin.tvhome.MainActivity")["home"] is True
assert front("com.example.someapp")["app"] == "Someapp"
hs._tv_front.update(t=time.time() - 300, busy=True)
assert hs.tv_front() is None                                    # too old: unknown, not a guess

# Kodi has Short Circuit paused in the background
movie = {"playerid": 1, "title": "Short Circuit", "paused": True}
m._kodi_now_playing = lambda: dict(movie)
for pkg, act, shown in [("com.google.android.youtube.tv", "", False), ("org.xbmc.kodi", "", True),
                        ("com.gathin.tvhome", "com.gathin.tvhome.HalActivity", True), ("com.gathin.tvhome", "com.gathin.tvhome.MainActivity", False)]:
    m.tv_front_app = lambda pkg=pkg, act=act: front(pkg, act)
    assert (m.kodi_now_playing() is not None) is shown, pkg
m.tv_front_app = lambda: None                                   # the TV tools aren't set up: trust Kodi, as before
assert m.kodi_now_playing()["title"] == "Short Circuit"
print("ALL TV FRONT TESTS PASSED")
