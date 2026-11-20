"""Voice commands: 'Hey Siri, ask HAL, play Glass Meridian', playback, volume, what's on / new."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))

import hal_server as hs
import mobile_server as m

# ---- which title a spoken name means ----
movies = [{"id": 7, "title": "Glass Meridian", "year": 2024, "resume": 4360}, {"id": 8, "title": "The Quiet Sun", "year": 2025, "resume": 0},
          {"id": 9, "title": "Night Harbor", "year": 2020, "resume": 0}]
shows = [{"id": 1, "title": "Night Harbor", "year": 2024}, {"id": 2, "title": "Coldwater", "year": 2019}]
mt = lambda q: (lambda r: (r[0], r[1]["id"]) if r else None)(m.voice_match(q, movies, shows))
assert mt("glass meridian") == ("movie", 7)
assert mt("the glass meridian movie") == ("movie", 7)
assert mt("quiet sun") == ("movie", 8)
assert mt("cold water") == ("show", 2)
assert mt("night harbor 2024") == ("show", 1)       # the year picks the show over the movie of the same name
assert mt("completely different thing") is None

# ---- playing it ----
calls = []
m.kodi_configured = lambda: True
m.kodi_call = lambda method, params=None, timeout=6: calls.append((method, params)) or {}
m.kodi_all_movies = lambda: [{"movieid": x["id"], "title": x["title"], "year": x["year"], "resume": {"position": x["resume"]}} for x in movies]
m.kodi_all_shows = lambda: shows
eps = [{"id": 100 + n, "show_id": 1, "show": "Night Harbor", "title": f"Ep {n}", "season": 2, "episode": n, "watched": n < 5,
        "resume": 0, "runtime": 0, "aired": ""} for n in range(1, 8)]
m.kodi_show_episodes = lambda sid: eps
r = m.voice_play("glass meridian")
assert r["ok"] and r["kind"] == "movie" and r["resumed"] and calls[-1] == ("Player.Open", {"item": {"movieid": 7}, "options": {"resume": True}})
r = m.voice_play("glass meridian", from_start=True)
assert not r["resumed"] and calls[-1][1]["options"]["resume"] is False
r = m.voice_play("coldwater")
assert not r["ok"] or r["kind"] == "episode"      # Coldwater has no episodes here: every one "watched" or none
r = m.voice_play("night harbor 2024")
assert r["ok"] and r["kind"] == "episode" and r["season"] == 2 and r["episode"] == 5 and calls[-1][1]["item"] == {"episodeid": 105}
r = m.voice_play("nothing like this")
assert not r["ok"] and "couldn't find" in r["message"]

# Kodi not answering: without TV control it says so; with it, the TV is turned on first
def down(method, params=None, timeout=6):
    raise m.UpstreamError("no answer")
m.kodi_call = down
m.tv_configured = lambda settings=None: False
assert "Is the TV on" in m.voice_play("glass meridian")["message"]
started = []
up = {"n": 0}
def wakes(method, params=None, timeout=6):
    up["n"] += 1
    if up["n"] < 3:
        raise m.UpstreamError("starting")
    calls.append((method, params))
    return {}
m.kodi_call = wakes
m.tv_configured = lambda settings=None: True
m.tv_power_state = lambda settings=None: "off"
m.tv_start = lambda launch_kodi=True: started.append(launch_kodi) or True
m.time.sleep = lambda s: None
r = m.voice_play("glass meridian", wait=True)
assert started == [True] and r["ok"] and r["started_tv"]
# the TV is on but Kodi isn't open (the home screen): Kodi is opened, and Siri gets an answer at once
opened = []
up["n"] = 0
m.tv_power_state = lambda settings=None: "on"
m.tv_open_kodi = lambda settings=None: opened.append(1)
_Thread = m.threading.Thread
m.threading.Thread = lambda target, daemon=True, name="": type("T", (), {"start": lambda self: None})()
r = m.voice_play("glass meridian")
m.threading.Thread = _Thread
assert opened == [1] and r["ok"] and r["pending"] and r["opened_kodi"] and r["title"] == "Glass Meridian"

# ---- playback control ----
m.kodi_call = lambda method, params=None, timeout=6: ([{"playerid": 1, "type": "video"}] if method == "Player.GetActivePlayers" else calls.append((method, params)) or {})
assert m.voice_control("pause")["ok"] and calls[-1] == ("Player.PlayPause", {"playerid": 1, "play": False})
m.voice_control("resume"); assert calls[-1] == ("Player.PlayPause", {"playerid": 1, "play": True})
m.voice_control("stop"); assert calls[-1] == ("Player.Stop", {"playerid": 1})
m.voice_control("back"); assert calls[-1][0] == "Player.Seek" and calls[-1][1]["value"]["seconds"] < 0
m.kodi_call = lambda method, params=None, timeout=6: []
assert m.voice_control("pause")["message"] == "Nothing is playing in Kodi."
try:
    m.voice_control("explode")
    raise AssertionError("any action")
except ValueError:
    pass
print("phone server voice ok")

# ---- what HAL hears ----
for q, intent, title in [("play glass meridian", "play", "glass meridian"), ("Turn on the TV and play Night Harbor.", "play", "night harbor"),
                         ("watch the movie Deep Field on the TV", "play", "deep field"), ("continue watching Night Harbor", "play", "night harbor"),
                         ("play glass meridian from the start", "play", "glass meridian"), ("pause", "pause", ""), ("resume", "resume", ""),
                         ("stop", "stop", ""), ("skip back", "back", ""), ("skip ahead", "forward", ""), ("turn it up", "vol_up", ""),
                         ("turn it down by 5", "vol_down", ""), ("mute", "mute", ""), ("what's on", "whats_on", ""), ("what's new", "whats_new", ""),
                         ("pause the ads", "ads_off", ""), ("sing", "daisy", ""), ("play daisy", "daisy", ""), ("turn on the tv", "tv_on", ""),
                         ("what's downloading", "downloads", ""), ("play it", "status", "")]:
    assert hs.siri_intent(q) == intent, (q, hs.siri_intent(q))
    assert hs.voice_title(q) == title, (q, hs.voice_title(q))

# ---- what HAL says (demo, then through the phone server) ----
demo = hs.make_demo_hal()
assert "Glass Meridian".lower() in demo.siri("play Glass Meridian")["say"].lower()
assert "YouTube" in demo.siri("what's on")["say"]
real = hs.Hal(settings=dict(hs.DEFAULT_SETTINGS), monitor=type("RealishMonitor", (hs.DemoMonitor,), {})(), start_threads=False)
sent = []
hs._phone_post = lambda hal, path, body, timeout=20: (sent.append((path, body)),
    {"ok": True, "kind": "episode", "title": "Night Harbor", "season": 2, "episode": 5, "episode_title": "The Lighthouse Keeper", "resumed": True,
     "started_tv": True} if path == "/api/voice/play" else {"ok": True})[1]
r = real.siri("Turn on the TV and play Night Harbor")
assert sent[-1] == ("/api/voice/play", {"q": "night harbor", "tv_on": True, "from_start": False})
assert r["say"].startswith("Turning the TV on. Playing Night Harbor, season 2, episode 5") and "where you left off" in r["say"]
hs._phone_post = lambda hal, path, body, timeout=20: {"ok": True, "pending": True, "title": "Glass Meridian", "opened_kodi": True}
assert real.siri("play glass meridian")["say"].startswith("Opening Kodi") and "Glass Meridian will start in a moment" in real.siri("play glass meridian")["say"]
hs._phone_post = lambda hal, path, body, timeout=20: (sent.append((path, body)), {"ok": True})[1]
assert real.siri("pause")["say"] == "Paused." and sent[-1] == ("/api/voice/control", {"action": "pause"})
hs._phone_post = lambda hal, path, body, timeout=20: {"ok": False, "message": "I couldn't find Zzz in your library."}
assert "couldn't find" in real.siri("play zzz")["say"]
keys = []
hs.tv_key = lambda hal, key, steps=1: keys.append((key, steps)) or {"ok": True, "message": ""}
assert real.siri("turn it up")["say"] == "Louder." and keys[-1] == ("vol_up", 3)
real.siri("turn it down by 5"); assert keys[-1] == ("vol_down", 5)
hs._phone_api = lambda hal, path: {"kodi": {"playing": None, "front": "YouTube", "front_title": "How It's Made: Pencils"}}
assert real.siri("what's on")["say"].startswith("You're watching How It's Made")
hs._sab_h_cache.update(t=1e12, v={"slots": [{"name": "Coldwater.S04E02.1080p.WEB-GRP", "ok": True, "t": 4e9}]})
assert "Coldwater season 4, episode 2" in real.siri("what's new")["say"]
# ---- opening apps on the TV ----
for q, want in [("open YouTube", ("youtube", "")), ("Open Netflix.", ("netflix", "")), ("put on Amazon Prime", ("prime", "")),
                ("play cat videos on YouTube", ("youtube", "cat videos")), ("watch Stranger Things on Netflix", ("netflix", "stranger things")),
                ("search YouTube for how to fix a faucet", ("youtube", "how to fix a faucet")), ("open YouTube and search for lofi music", ("youtube", "lofi music")),
                ("play the office on prime", ("prime", "the office")), ("play glass meridian", None), ("pause the ads", None)]:
    assert hs.voice_app(q) == want, (q, hs.voice_app(q))
assert hs.siri_intent("open netflix") == "app" and hs.siri_intent("play glass meridian") == "play"
cmds = []
hs.tv_adb = lambda cmd, timeout=30: (cmds.append(cmd), (True, "Starting: Intent { act=android.intent.action.VIEW }"))[1]
r = hs.tv_app(real, "youtube", "cat videos & dogs")
assert r["ok"] and r["message"] == "Searching YouTube for cat videos & dogs."
assert "search_query=cat%20videos%20%26%20dogs'" in cmds[-1] and cmds[-1].endswith(" com.google.android.youtube.tv")   # safe for the TV's shell
r = hs.tv_app(real, "netflix")
assert r["message"] == "Opening Netflix." and cmds[-1].startswith("monkey -p com.netflix.ninja")
# Netflix and Prime: the way try-tv-search.ps1 saved (nothing saved yet: open the app and say how to set it up)
import tempfile as _tf, pathlib as _pl, json as _json
hs.TV_SEARCH_FILE = _pl.Path(_tf.mkdtemp()) / "tv_search_methods.json"
hs.tv_adb = lambda cmd, timeout=30: (cmds.append(cmd), (True, "Starting: Intent"))[1]
r = hs.tv_app(real, "netflix", "stranger things")
assert cmds[-1].startswith("monkey -p com.netflix.ninja") and "try-tv-search.ps1" in r["message"]
hs.TV_SEARCH_FILE.write_text("\ufeff" + _json.dumps({"netflix": "am start -a android.intent.action.VIEW -d 'nflx://www.netflix.com/search?q={q}' com.netflix.ninja",
                                                      "global": "am start -a android.intent.action.WEB_SEARCH --es query '{qs}'"}), encoding="utf-8")
r = hs.tv_app(real, "netflix", "stranger things")
assert "nflx://www.netflix.com/search?q=stranger%20things" in cmds[-1] and r["message"] == "Searching Netflix for stranger things."
r = hs.tv_app(real, "prime", "the office")
assert "WEB_SEARCH --es query 'the office'" in cmds[-1] and "opens in Prime Video" in r["message"]
hs.TV_SEARCH_FILE.write_text(_json.dumps({"netflix": "am start -d x; reboot"}), encoding="utf-8")
assert hs.tv_search_method("netflix") == ""                                   # nothing that could run a second command
hs.TV_SEARCH_FILE.write_text(_json.dumps({"global": "am start -a android.search.action.GLOBAL_SEARCH --es query '{qs}'"}), encoding="utf-8")
# YouTube: its own search link
r = hs.tv_app(real, "youtube", "lofi")
assert "youtube.com/results?search_query=lofi" in cmds[-1]
# not even that: it opens the app and says so
hs.tv_adb = lambda cmd, timeout=30: (cmds.append(cmd), (True, "Error: Activity not started, unable to resolve Intent") if cmd.startswith("am start") else (True, "Events injected: 1"))[1]
r = hs.tv_app(real, "prime", "the office")
assert r["ok"] and "wouldn't take the search" in r["message"]
# the TV is off: turned on through the phone server, and the app opens later
hs.tv_adb = lambda cmd, timeout=30: (False, "The TV didn't answer in time. Is it on?")
posted = []
hs._phone_post = lambda hal, path, body, timeout=20: (posted.append((path, body)), {"ok": True})[1]
_T = hs.threading.Thread
hs.threading.Thread = lambda target, daemon=True, name="": type("T", (), {"start": lambda self: None})()
r = hs.tv_app(real, "youtube")
hs.threading.Thread = _T
assert posted[-1] == ("/api/tv/on", {"kodi": False}) and r["message"].startswith("Turning the TV on") and "YouTube will open" in r["message"]
assert "YouTube" in real.siri("open youtube")["say"]
print("\nALL VOICE TESTS PASSED")
