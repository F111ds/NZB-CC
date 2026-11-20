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
    hs._tv_front.update(pkg=pkg, activity=act or pkg + ".Main", t=time.time(), ok_t=time.time(), busy=False)
    return hs.tv_front()


assert front("com.google.android.youtube.tv")["app"] == "YouTube"
assert front("org.xbmc.kodi")["kodi"] is True
assert front("com.gathin.tvhome", "com.gathin.tvhome.HalActivity")["hal"] is True
assert front("com.gathin.tvhome", "com.gathin.tvhome.MainActivity")["home"] is True
assert front("com.example.someapp")["app"] == "Someapp"
hs._tv_front.update(t=time.time() - 300, ok_t=time.time() - 300, busy=True)
assert hs.tv_front() is None                                    # too old: unknown, not a guess

# a failed check keeps the last answer
front("com.google.android.youtube.tv")
hs.tv_adb = lambda cmd, timeout=30: (False, "The TV didn't answer in time. Is it on?")
hs._tv_front_refresh()
assert hs.tv_front()["app"] == "YouTube" and "Is it on" in hs._tv_front["error"]
hs.tv_adb = lambda cmd, timeout=30: (True, "  topResumedActivity=ActivityRecord{9 u0 com.netflix.ninja/.MainActivity t3}")
hs._tv_front_refresh()
assert hs.tv_front()["app"] == "Netflix"

# what the app says it's playing (media sessions)
ms = hs._tv_media_parse("""    package=com.google.android.youtube.tv
      state=PlaybackState {state=3, position=12000, speed=1.0}
      metadata: size=7, description=How It's Made: Pencils, Science Channel, null
    package=com.netflix.ninja
      state=PlaybackState {state=2, position=5}
      metadata: size=4, description=Stranger Things, Chapter One, null
    package=com.amazon.amazonvideo.livingroom
      metadata: size=0, description=null""")
assert ms["com.google.android.youtube.tv"] == {"title": "How It's Made: Pencils", "sub": "Science Channel", "state": 3}
assert ms["com.netflix.ninja"]["title"] == "Stranger Things" and ms["com.netflix.ninja"]["state"] == 2
assert ms["com.amazon.amazonvideo.livingroom"]["title"] == ""
FRONT = "  ResumedActivity: ActivityRecord{1 u0 com.netflix.ninja/.MainActivity t3}"
hs.tv_adb = lambda cmd, timeout=30: (True, FRONT if "activities" in cmd else
                                     "    package=com.netflix.ninja\n      state=PlaybackState {state=2, position=5}\n"
                                     "      metadata: size=4, description=Stranger Things, Chapter One, null")
hs._tv_front_refresh()
f = hs.tv_front()
assert f["app"] == "Netflix" and f["title"] == "Stranger Things" and f["sub"] == "Chapter One" and f["paused"] is True
hs.tv_adb = lambda cmd, timeout=30: (True, FRONT if "activities" in cmd else
                                     "    package=com.netflix.ninja\n      metadata: size=1, description=Netflix, null, null")
hs._tv_front_refresh()
assert hs.tv_front()["title"] == ""                              # just the app's name: no title
def boom(cmd, timeout=30):
    if "activities" in cmd:
        return True, FRONT
    raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")
hs.tv_adb = boom
hs._tv_front_refresh()
assert hs.tv_front()["app"] == "Netflix" and hs.tv_front()["title"] == ""   # a broken title read keeps the app's name

# newer Android's wording (mState=3(PLAYING), session headers) and a TITLE line
ms = hs._tv_media_parse("""    YouTube com.google.android.youtube.tv/YouTube (userId=0)
      package=com.google.android.youtube.tv
      state=PlaybackState {mState=3(PLAYING), mPosition=12000}
      metadata: size=9, description=Top 10 Robots in Movies, WatchMojo, null
    Prime com.amazon.amazonvideo.livingroom/av (userId=0)
      state=PlaybackState {mState=2(PAUSED)}
      METADATA_KEY_TITLE: The Boys""")
assert ms["com.google.android.youtube.tv"] == {"title": "Top 10 Robots in Movies", "sub": "WatchMojo", "state": 3}
assert ms["com.amazon.amazonvideo.livingroom"]["title"] == "The Boys" and ms["com.amazon.amazonvideo.livingroom"]["state"] == 2

# a games console on an HDMI input: its name comes from HDMI-CEC
HDMI = """  mActiveSource: ActiveSource(logicalAddress: 0x04, physicalAddress: 0x2000)
      HdmiDeviceInfo: CEC: logical_address: 0x04 device_type: 4 vendor_id: 0x104 display_name: PS5 power_status: 0 physical_address: 0x2000 port_id: 2
      HdmiDeviceInfo: CEC: logical_address: 0x05 device_type: 5 display_name: Sonos Arc power_status: 0 physical_address: 0x1000 port_id: 1"""
assert hs._tv_hdmi_parse(HDMI) == "PlayStation 5"
assert hs._tv_hdmi_parse("  active source: (0x1, 0x3000)\n  HdmiDeviceInfo: CEC: device_type: 4 display_name: PlayStation 4 power_status: 1 physical_address: 0x1000") == "PlayStation 4"
assert hs._tv_hdmi_parse("") == ""
assert hs._tv_is_input("com.mediatek.wwtv.tvcenter", "com.mediatek.wwtv.tvcenter.nav.TurnkeyUiMainActivity")
assert not hs._tv_is_input("com.google.android.youtube.tv", "com.google.android.apps.youtube.tv.activity.ShellActivity")
INPUT_FRONT = "  ResumedActivity: ActivityRecord{1 u0 com.mediatek.wwtv.tvcenter/.nav.TurnkeyUiMainActivity t3}"
hs.tv_adb = lambda cmd, timeout=30: (True, INPUT_FRONT if "activities" in cmd else HDMI if "hdmi_control" in cmd else "")
hs._tv_front_refresh()
f = hs.tv_front()
assert f["app"] == "PlayStation 5" and not f["home"] and not f["kodi"]
hs.tv_adb = lambda cmd, timeout=30: (True, INPUT_FRONT if "activities" in cmd else "")
hs._tv_front_refresh()
assert hs.tv_front()["app"] == "HDMI"                                 # an input, nobody named on it
hs.tv_adb = lambda cmd, timeout=30: (True, "  ResumedActivity: ActivityRecord{1 u0 com.google.android.youtube.tv/.Shell t3}" if "activities" in cmd else "")
hs._tv_front_refresh()
assert hs.tv_front()["app"] == "YouTube"                              # back to an app: the input's name goes

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
