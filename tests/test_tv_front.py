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
SW = "      HdmiDeviceInfo: CEC: logical_address: 0x04 device_type: 4 display_name: NintendoSwitch power_status: 0 physical_address: 0x3000"
assert hs._tv_hdmi_parse("  mActiveSource: ActiveSource(logicalAddress: 0x04, physicalAddress: 0x3000)\n" + SW) == "Nintendo Switch"
assert hs._tv_hdmi_parse(SW.replace("NintendoSwitch", "Switch") + "\n" + SW.replace("NintendoSwitch", "Sonos Arc").replace("device_type: 4", "device_type: 5")) == "Nintendo Switch"
assert hs._console_anywhere("mDeviceInfos=[{id: 0x2004, name: \"PS5\", vendor 0x104}]") == "PlayStation 5"
assert hs._console_anywhere("", "  TvInputInfo{id=com.sony.dtv.tvinput.external/.ExternalTvInputService/HDMI200004, label=PlayStation(R)5}") == "PlayStation 5"
assert hs._console_anywhere("nothing here", "HDMI 2") == ""
assert hs._cec_pretty("Xbox") == "Xbox" and hs._cec_pretty("PS4") == "PlayStation 4" and hs._cec_pretty("Apple TV") == "Apple TV"
assert hs._tv_is_input("com.mediatek.wwtv.tvcenter", "com.mediatek.wwtv.tvcenter.nav.TurnkeyUiMainActivity")
assert not hs._tv_is_input("com.google.android.youtube.tv", "com.google.android.apps.youtube.tv.activity.ShellActivity")
INPUT_FRONT = "  ResumedActivity: ActivityRecord{1 u0 com.mediatek.wwtv.tvcenter/.nav.TurnkeyUiMainActivity t3}"
hs.tv_adb = lambda cmd, timeout=30: (True, INPUT_FRONT if "activities" in cmd else HDMI if "hdmi_control" in cmd else "")
hs._tv_front_refresh()
f = hs.tv_front()
assert f["app"] == "PlayStation 5" and not f["home"] and not f["kodi"]
hs.tv_adb = lambda cmd, timeout=30: (True, INPUT_FRONT if "activities" in cmd else "label=HDMI 3 (PS4)" if "tv_input" in cmd else "")
hs._tv_front_refresh()
assert hs.tv_front()["app"] == "PlayStation 4"                       # from the TV's input list
hs.tv_adb = lambda cmd, timeout=30: (True, INPUT_FRONT if "activities" in cmd else "")
hs._tv_front_refresh()
assert hs.tv_front()["app"] == "HDMI"                                 # an input, nobody named on it
hs.tv_adb = lambda cmd, timeout=30: (True, "  ResumedActivity: ActivityRecord{1 u0 com.google.android.youtube.tv/.Shell t3}" if "activities" in cmd else "")
hs._tv_front_refresh()
assert hs.tv_front()["app"] == "YouTube"                              # back to an app: the input's name goes

# the consoles go through a Denon amp: the amp says which input it's on, and what you named it
LITE = '<item><Power><value>ON</value></Power><InputFuncSelect><value>{}</value></InputFuncSelect></item>'
FULL = ('<item><InputFuncList><value>SAT/CBL</value><value>GAME</value><value>MPLAY</value></InputFuncList>'
        '<RenameSource><value>CBL/SAT</value><value>PS5</value><value>{}</value></RenameSource></item>')
hs._denon_find = lambda: "192.0.2.61"
def fake_http(sel, rename="Switch"):
    return lambda host, path, timeout=2.5: LITE.format(sel) if "StatusLite" in path else FULL.format(rename)
hs._denon_http = fake_http("GAME")
assert hs.denon_now() == "PlayStation 5"
hs._denon_http = fake_http("MPLAY")
assert hs.denon_now() == "Nintendo Switch"
hs._denon_http = fake_http("MPLAY", "Media Player")                  # not renamed
assert hs.denon_now() == "Media Player (Denon)"
hs._denon_http = lambda host, path, timeout=2.5: ""                  # no web page: telnet
hs._denon_telnet = lambda host, cmds, timeout=2.0: "SIGAME\nSSFUNGAME PS5\nSSFUNMPLAY Switch\nSSFUNEND\n"
assert hs.denon_now() == "PlayStation 5"
hs._denon_find = lambda: ""
assert hs.denon_now() == ""
# the TV shows the amp's input and CEC names nobody (or just the amp): the amp is asked
hs._denon_find = lambda: "192.0.2.61"
hs.tv_adb = lambda cmd, timeout=30: (True, INPUT_FRONT if "activities" in cmd else "display_name: Denon AVR-X2700H" if "hdmi" in cmd else "")
hs._tv_front_refresh()
assert hs.tv_front()["app"] == "PlayStation 5"

# the amp is on an input you haven't renamed ("Media Player"): the one console the TV can see is who's on it
hs._denon_http = fake_http("MPLAY", "Media Player")
CEC_AMP_PS = ("  active source: (0x1, 0x1000)\n"
              "  HdmiDeviceInfo: CEC: device_type: 5 display_name: Denon AVR-X2700H power_status: 0 physical_address: 0x1000\n"
              "  HdmiDeviceInfo: CEC: device_type: 4 display_name: PS5 power_status: 0 physical_address: 0x1100")
hs.tv_adb = lambda cmd, timeout=30: (True, INPUT_FRONT if "activities" in cmd else CEC_AMP_PS if "hdmi_control" in cmd else "")
hs._tv_front_refresh()
assert hs.tv_front()["app"] == "PlayStation 5", hs.tv_front()
hs.tv_adb = lambda cmd, timeout=30: (True, INPUT_FRONT if "activities" in cmd else
                                     CEC_AMP_PS + "\n  HdmiDeviceInfo: CEC: device_type: 4 display_name: Switch physical_address: 0x1200" if "hdmi_control" in cmd else "")
hs._tv_front_refresh()
assert hs.tv_front()["app"] == "Media Player (Denon)"                 # a PlayStation and a Switch: no guessing
hs.tv_adb = lambda cmd, timeout=30: (True, INPUT_FRONT if "activities" in cmd else "display_name: Denon AVR-X2700H" if "hdmi_control" in cmd
                                     else "label=PlayStation(R)5" if "tv_input" in cmd else "")
hs._tv_front_refresh()
assert hs.tv_front()["app"] == "PlayStation 5"                         # from the TV's input labels
assert hs._only_console("PS5", "PlayStation(R)5 label=PlayStation") == "PlayStation 5"
assert hs._only_console("PS5 and Nintendo Switch") == "" and hs._only_console("") == ""

# switching from Kodi to the PlayStation: the screen counts as changed at once, not after the slow name lookups
front("org.xbmc.kodi")
mid = {}
def slow_adb(cmd, timeout=30):
    if "activities" in cmd:
        return True, INPUT_FRONT
    if "hdmi_control" in cmd:
        mid["front"] = hs.tv_front()          # what the phone and HAL would see while the TV is still being asked
    return True, CEC_AMP_PS if "hdmi_control" in cmd else ""
hs.tv_adb = slow_adb
hs._tv_front_refresh()
assert mid["front"] and not mid["front"]["kodi"] and mid["front"]["app"] == "HDMI", mid
assert hs.tv_front()["app"] == "PlayStation 5"
m._kodi_now_playing = lambda: {"playerid": 1, "title": "Short Circuit", "paused": True}
m.tv_front_app = lambda: mid["front"]
assert m.kodi_now_playing() is None                                    # no "watching Short Circuit" while the PlayStation is on

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
