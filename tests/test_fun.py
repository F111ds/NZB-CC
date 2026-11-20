"""HAL's fun bits: Siri answers, briefings, lifetime counters and milestones."""
import sys as _sys
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(ROOT))

import json
import tempfile
from datetime import datetime
from pathlib import Path

import hal_server as hs

tmp = Path(tempfile.mkdtemp())
hs.SETTINGS_FILE = tmp / "hal_settings.json"
hs.STATE_FILE = tmp / "hal_state.json"

# ---- what was said to Siri ----
for q, want in [("How's everything?", "status"), ("open the pod bay doors please", "podbay"), ("sing me a song", "daisy"),
                ("pause the ads for 30 minutes", "ads_off"), ("turn the ads back on", "ads_on"), ("how many ads today", "ads"),
                ("lock the PC", "lock"), ("put the PC to sleep", "sleep"), ("restart the computer", "refuse"), ("shut down", "refuse"),
                ("turn on the TV", "tv_on"), ("what's downloading", "downloads"), ("how hot is it", "temps"),
                ("how are the drives", "drives"), ("is the internet ok", "internet"), ("is anything down on uptime kuma", "kuma"),
                ("how was my week", "week"), ("give me my briefing", "morning"), ("what's my record", "record"), ("", "status")]:
    assert hs.siri_intent(q) == want, (q, hs.siri_intent(q), want)
print("intents ok")

hal = hs.make_demo_hal()
for q in ("how's everything", "how hot is it", "how are the drives", "what's my record", "is anything down", "pause the ads"):
    r = hal.siri(q)
    assert r["say"] and "None" not in r["say"], (q, r)
assert hal.siri("open the pod bay doors")["say"] == "I'm sorry, Dave. I'm afraid I can't do that."
assert "can't do that from here" in hal.siri("restart the pc")["say"]
assert hal.siri("pause the ads")["say"].startswith("Demo mode")                         # demo changes nothing
assert hs.spoken("Drive Z: is 92% full, 38.2 MB/s, 47°C · ok") == "Drive Z is 92 percent full, 38.2 megabytes a second, 47 degrees, ok"
assert hs._lc("Windows wants a restart") == "Windows wants a restart" and hs._lc("The CPU is hot") == "the CPU is hot"
assert hs._lc("CPU load") == "CPU load" and hs._lc("Drive Z: is full") == "drive Z: is full"
print("siri ok")

# ---- briefings ----
for kind in ("now", "morning", "weekly"):
    txt = hal.brief_text(kind)
    assert txt and "None" not in txt and "{" not in txt, (kind, txt)
assert hal.brief_text("morning").startswith("Good morning, Dave.") and hal.brief_text("weekly").startswith("Here is your week, Dave.")
h2 = hs.Hal(settings=dict(hs.DEFAULT_SETTINGS), monitor=hs.DemoMonitor(), start_threads=False)
h2.save = lambda: None
sent = []
h2.add_event = lambda lvl, title, msg="", push=False: sent.append((title, msg))
h2.settings.update(brief_time="07:30", brief_weekly="sun", push=False)
assert h2.check_briefings(datetime(2026, 11, 19, 7, 29)) is None                         # not yet
assert h2.check_briefings(datetime(2026, 11, 19, 7, 31)) == "morning" and sent[-1][0] == "Morning briefing"
assert h2.check_briefings(datetime(2026, 11, 19, 7, 45)) is None                          # once a day
assert h2.check_briefings(datetime(2026, 11, 20, 9, 0)) is None                           # missed the window: skip, don't nag late
assert h2.check_briefings(datetime(2026, 11, 22, 7, 30)) == "weekly" and sent[-1][0] == "Your week"   # a Sunday
h2.settings["brief_time"] = ""
assert h2.check_briefings(datetime(2026, 11, 23, 7, 30)) is None                          # off
s = hs.validate_settings({"brief_time": "6:05", "brief_weekly": "mon"}, h2.settings)
assert s["brief_time"] == "06:05" and s["brief_weekly"] == "mon"
assert hs.validate_settings({"brief_time": ""}, h2.settings)["brief_time"] == ""
for bad in ({"brief_time": "25:00"}, {"brief_time": "seven"}, {"brief_weekly": "someday"}):
    try:
        hs.validate_settings(bad, h2.settings)
        raise AssertionError(bad)
    except ValueError:
        pass
print("briefings ok")

# ---- lifetime counters and milestones ----
h3 = hs.Hal(settings=dict(hs.DEFAULT_SETTINGS), monitor=hs.DemoMonitor(), start_threads=False)
ev = []
h3.add_event = lambda lvl, title, msg="", push=False: ev.append(title)
h3.record_ads(4000, "2026-11-18")
h3.record_ads(3000, "2026-11-18")                                                        # a day's number only goes up
h3.record_ads(5000, "2026-11-19")
assert h3.ads_lifetime() == 9000 and not ev
h3.record_ads(7000, "2026-11-19")
assert h3.ads_lifetime() == 11000 and ev == ["10,000 ads blocked"]
h3.record_ads(8000, "2026-11-19")
assert ev == ["10,000 ads blocked"]                                                       # once
for i in range(420):                                                                      # old days fold into a running total
    h3.record_ads(10, f"2025-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}" if i < 300 else f"2024-01-{i % 28 + 1:02d}")
assert len(h3.counters["ads_days"]) <= 400 and h3.ads_lifetime() >= 12000
h3.note_power_cut()
h3.note_uptime(86400 * 3)
h3.note_uptime(3600)
r = h3.record()
assert r["power_cuts"] == 1 and r["longest_up"] == 86400 * 3 and r["next_milestone"] == 50_000
print("counters ok")
# ---- what the TV's HAL screensaver reads ----
tb = hs.tv_brief(hal.status())
assert tb["say"].startswith("Good") and "Z:" not in tb["say"] and "%" not in tb["say"]
assert tb["alerts"] and all(a["level"] in ("warning", "critical") for a in tb["alerts"]) and len(tb["alerts"]) <= 3
assert tb["kuma_total"] == 12 and tb["kuma_up"] == 11 and tb["internet_down"] is False and tb["downloading"].startswith("downloading")
print("tv ok")
# ---- HAL on the TV, on demand ----
assert len(tb["lines"]) >= 2 and all(l["text"] and l["say"] for l in tb["lines"])
h5 = hs.Hal(settings=dict(hs.DEFAULT_SETTINGS), monitor=type("RealishMonitor", (hs.DemoMonitor,), {})(), start_threads=False)
sent = []
hs.tv_adb = lambda cmd, timeout=30: (sent.append(cmd), (True, "Starting: Intent { cmp=com.gathin.tvhome/.HalActivity }"))[1]
r = hs.tv_show(h5, "Dinner's ready,   Dave.")
assert r["ok"] and h5.status()["tvhal"]["open"] is True
import base64 as _b64
assert sent[-1].startswith("am start -n com.gathin.tvhome/.HalActivity") and "--es say_b64 " in sent[-1]
assert _b64.b64decode(sent[-1].split("say_b64 ")[1]).decode() == "Dinner's ready, Dave."
assert "'" not in sent[-1].split("say_b64 ")[1]                                    # nothing for the TV's shell to trip on
hs.tv_show(h5, "")
assert "say_b64" not in sent[-1]
hs.tv_adb = lambda cmd, timeout=30: (True, "Error type 3\nError: Activity class {com.gathin.tvhome/com.gathin.tvhome.HalActivity} does not exist.")
r = hs.tv_show(h5, "hi")
assert not r["ok"] and "older app" in r["message"]
hs.tv_adb = lambda cmd, timeout=30: (False, "The TV didn't answer in time. Is it on?")
assert "Is it on?" in hs.tv_show(h5, "")["message"]
# TV volume and mute: the TV's own keys through ADB, several steps in one command
hs.tv_adb = lambda cmd, timeout=30: (sent.append(cmd), (True, ""))[1]
assert hs.tv_key(h5, "vol_up", 3)["ok"] and sent[-1] == "input keyevent 24 24 24"
assert hs.tv_key(h5, "vol_down", 99)["ok"] and sent[-1] == "input keyevent " + " ".join(["25"] * 10)
assert hs.tv_key(h5, "mute", 5)["ok"] and sent[-1] == "input keyevent 164"
try:
    hs.tv_key(h5, "power")
    raise AssertionError("any key")
except ValueError:
    pass
hs.tv_adb = lambda cmd, timeout=30: (False, "The TV didn't answer in time. Is it on?")
r = hs.tv_key(h5, "vol_up")
assert not r["ok"] and "Is it on?" in r["message"]
assert hs.tv_key(hal, "mute")["ok"]   # demo: nothing sent
assert hs.siri_intent("show yourself on the TV") == "tv_show" and hs.siri_intent("turn on the tv") == "tv_on"
sc = hs.tv_screen(hal.status())
for box in ("pc", "net", "pi"):
    assert len(sc[box]["rows"]) == 5 and all(len(r) == 3 and r[0] for r in sc[box]["rows"]), box
assert [d["mount"] for d in sc["pc"]["drives"]] == ["C", "D", "Z"] and [d for d in sc["pc"]["drives"] if d["warn"]][0]["mount"] == "Z"
assert sc["pc"]["level"] == "warning" and sc["pc"]["rows"][3][2] == "warning"          # drive Z is filling up
assert sc["kuma"]["monitors"][0]["status"] == "down"                                  # problems first
assert sc["downloads"]["state"] == "downloading" and sc["downloads"]["title"] == "The Long Orbit (2025)"
_q, _t = hs.sab_queue, hs.sab_today
hs.sab_today = lambda: 3
hs.sab_queue = lambda: {"ok": True, "paused": False, "count": 3, "speed": "", "kbps": 38000, "name": "The.Long.Orbit.2025.2160p.UHD.BluRay.nzb", "pct": 59, "left": "0:12:05"}
d = hs.tv_downloads()
assert d["title"] == "The Long Orbit (2025)" and d["info"].startswith("59% · ") and "2 queued" in d["info"] and "done " in d["info"]
hs.sab_queue = lambda: {"ok": True, "paused": False, "count": 0, "speed": "", "kbps": 0, "name": "", "pct": 0, "left": ""}
assert hs.tv_downloads() == {"state": "idle", "title": "Nothing downloading", "info": "3 finished today · SABnzbd ready"}
hs.sab_queue = lambda: {"ok": True, "paused": True, "count": 2, "speed": "", "kbps": 0, "name": "x", "pct": 0, "left": ""}
assert hs.tv_downloads()["state"] == "paused"
hs.sab_queue = lambda: {"ok": False}
assert hs.tv_downloads()["state"] == "problem"
hs.sab_queue = lambda: {}
assert hs.tv_downloads()["state"] == "off"
hs.sab_queue, hs.sab_today = _q, _t
assert sc["kuma"]["ok"] and len(sc["kuma"]["monitors"]) <= 5 and sc["kuma"]["head"] == "11 of 12 up"
assert sc["pi"]["rows"][0][1] == "Pi 4 B · 4 GB" and sc["pi"]["rows"][1][1] == "47°C"
assert sc["alerts"][0]["title"] and "ads blocked" in sc["record"]
assert "Z: (Seagate" not in json.dumps(sc["pc"]) and "screen" in hs.tv_brief(hal.status())
det = hs.tv_details(hal)
assert [pg["title"] for pg in det["pages"]] == ["The PC", "Network", "Uptime Kuma", "Raspberry Pi", "Downloads"]
dlp = det["pages"][4]
assert dlp["cols"][0]["queue"][0]["pct"] == 59 and dlp["cols"][1]["big"] == "6 done" and dlp["cols"][2]["key"] == "COMING UP"
assert hs.nice_release("The.Long.Orbit.2025.2160p.UHD.BluRay-X") == "The Long Orbit (2025)"
# one "Dave" on the TV: the greeting has the name, so HAL's line after it doesn't repeat it
tb2 = hs.tv_brief(hal.status())
assert tb2["lines"][0]["text"].count("Dave") <= 1 and tb2["quote"] and not tb2["quote"].endswith(", Dave.")
# the network box names the new device
netrows = hs.tv_screen(hal.status())["net"]["rows"]
assert any(r[0] == "New device" and r[1] == "android-3f2a" and r[2] == "warning" for r in netrows), netrows
assert "days on duty" in hs.tv_screen(hal.status())["record"]
pc = det["pages"][0]
assert len(pc["cols"][0]["graph"]) > 10 and len(pc["cols"][0]["cores"]) == 16 and len(pc["cols"][2]["drives"]) == 3
assert pc["cols"][2]["badge"] == "CHECK" and [d for d in pc["cols"][2]["drives"] if d["warn"]][0]["mount"] == "Z:"
assert det["pages"][2]["cols"][0]["monitors"][0]["status"] == "down"                    # problems first
assert sum(len(c.get("monitors", [])) for c in det["pages"][2]["cols"]) == 12
assert all(len(r) == 3 for pg in det["pages"] for c in pg["cols"] for r in c.get("rows", []))
# the 2-row card grid (TV app 3.5-nzbcc.40+) on every page, old cols kept for older TV apps
assert all(len(pg["grid"]) in (1, 2) and all(row for row in pg["grid"]) for pg in det["pages"]), [pg["title"] for pg in det["pages"] if "grid" not in pg]
gpc = det["pages"][0]["grid"]
assert [c["key"] for c in gpc[0]] == ["PROCESSOR", "MEMORY", "GRAPHICS"] and len(gpc[1][0]["drives"]) == 3
assert sum(len(c.get("monitors", [])) for c in det["pages"][2]["grid"][0]) == 12
netg, pig, dlg = det["pages"][1]["grid"], det["pages"][3]["grid"], det["pages"][4]["grid"]
assert netg[0][0]["graph_fit"] and [c["key"] for c in netg[1]] == ["TRAFFIC · LAST 24 H", "BUSIEST DEVICES"] and netg[1][0]["graph2"]
assert any(r[0].startswith("Docker") for r in netg[1][1]["rows"])
assert len(pig[0][1]["hourly"]) == 24 and len(pig[0][1]["hourly2"]) == 24 and pig[1][2]["key"] == "MOST ASKED FOR" and pig[1][2]["rows"][0][0] == "i.ytimg.com"
assert [c["key"] for r in dlg for c in r] == ["QUEUE", "RECENTLY FINISHED", "LAST 7 DAYS", "YOUR LIBRARY", "ROOM LEFT"] and len(dlg[1][0]["hourly"]) == 7
assert len(dlg[1][0]["hourly_tops"]) == 7 and dlg[1][0]["hourly_tops"][0].endswith("GB")
from datetime import datetime as _dt
_days = [{"date": f"2026-11-{14 + i:02d}", "done": 1, "failed": 0, "bytes": 2 * 1073741824} for i in range(7)]
idle = hs._dl_grid(hal, _dt.now(), [], "", [], {"diskspace1": "100", "diskspacetotal1": "200"}, {"days": _days}, {"ok": False},
                   [{"mount": "Z:", "total": 200 * 1073741824}])
assert [c["key"] for r in idle for c in r] == ["LAST 7 DAYS", "RECENTLY FINISHED", "QUEUE", "YOUR LIBRARY", "ROOM LEFT"]
assert idle[1][0]["big"] == "Idle" and idle[1][2]["badge"] == "Z: DRIVE" and idle[1][2]["rows"][1] == ["Room for about", "50 more downloads", "ok"]
assert "Kodi isn't answering" in idle[1][1]["bigsub"] and not idle[1][2]["badge"].startswith("DOWN")
assert hs.nice_release("Top.Gun.Maverick.2022.MULTI.DV.IMAX") == "Top Gun Maverick (2022)" and hs.nice_release("Top.Gun.Maverick.2022.MULTi.DV.IMAX") == "Top Gun Maverick (2022)" and hs.nice_release("Blade.Runner.2049.2017.1080p") == "Blade Runner 2049 (2017)"
assert hs._when(_dt.now().timestamp(), _dt.now()).endswith(("AM", "PM"))
# a few Uptime Kuma monitors: a card each (response-time graph, checks), summary first
_mons = hal.kuma["monitors"]
hal.kuma = {**hal.kuma, "monitors": _mons[:5], "up": 4}
for m_ in _mons[:5]:
    for i_ in range(10):
        hal.kuma_ms.setdefault(m_["name"], hs.deque(maxlen=120)).append(m_.get("ms"))
        hal.kuma_hist.setdefault(m_["name"], hs.deque(maxlen=120)).append(m_["status"])
hal.kuma_down_t["Front door camera"] = hs.time.time() - 300
kg = hs.tv_details(hal)["pages"][2]["grid"]
assert [len(r) for r in kg] == [3, 3] and kg[0][0]["key"] == "UPTIME KUMA" and kg[0][1]["badge"].startswith("DOWN") and "since" in kg[0][1]["bigsub"]
assert kg[1][0]["graph_fit"] and len(kg[1][0]["checks"]) == 10 and kg[1][0]["big"].endswith("ms")
hal.kuma = {**hal.kuma, "monitors": _mons}
assert "monitors" in hs.tv_details(hal)["pages"][2]["grid"][0][1]          # many monitors: the list, as before
print("tv on demand ok")
print("\nALL FUN TESTS PASSED")
