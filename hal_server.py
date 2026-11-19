#!/usr/bin/env python3
"""
HAL 9000 - a phone app that watches over this PC (HAL9000) and the media pipeline running on it.

    "I am putting myself to the fullest possible use, which is all I think that any conscious
     entity can ever hope to do."

Open http://<this-pc>:8766/ on your phone and add it to the home screen. Tabs:
  * Status   - HAL's eye (steady = all well, amber ring = needs a look, pulsing = critical), what
               HAL thinks in his own words, live CPU / memory / GPU / internet, critical systems
  * Vitals   - CPU, memory, GPU with live / 1 h / 24 h / 7 d graphs, temperatures, fans, top apps
  * Services - SABnzbd, Jellyfin, Sonarr, Radarr, Prowlarr and the NZB phone server: answering?
               how fast? what are they doing? Restart from the phone (and, if you want, HAL restarts
               a service by himself when it stops answering). Also Kodi on the TV, the router,
               your indexers and Tailscale.
  * Drives   - free space with "about N days left" at your download rate, health, temperature,
               wear, errors, live activity, Recycle Bin and temp-file cleanup
  * Shield   - Defender, firewall, Windows Update, pending restart, failed sign-ins, crashes and
               unexpected shutdowns, internet / DNS / router, power plan and the UPS
  * Log      - everything HAL noticed, phone alerts (ntfy, the same as the NZB phone app),
               and the pod bay controls: lock, sleep, fix network, restart / shut down the PC

UPS: a UPS plugged in by USB shows up in Windows as a battery and is read automatically. A UPS
watched by Network UPS Tools (NUT, e.g. WinNUT or a NAS) can be read too: set its address in
Settings. HAL can shut the PC down cleanly when the UPS battery runs low (off until you turn it on).

Pairing: uses the SAME access code as the NZB phone app (mobile_access_key.txt in this folder).

Requires: psutil (pip install psutil) and system_monitor.py from this folder.

    python hal_server.py                    # LAN / Tailscale only, port 8766
    python hal_server.py --port 9001
    python hal_server.py --demo             # simulated data, for a look around (any PC)
    python hal_server.py --install-autostart / --remove-autostart
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import ipaddress
import json
import logging
import logging.handlers
import math
import os
import random
import re
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover - friendly message on a PC without psutil
    print("HAL 9000 needs psutil. Install it with:  pip install psutil")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import system_monitor as sm  # noqa: E402

VERSION = "1.0.0"
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
NZB_CONFIG_FILE = BASE_DIR / "nzb_manager_config.json"
PHONE_SETTINGS_FILE = BASE_DIR / "mobile_settings.json"
ACCESS_KEY_FILE = BASE_DIR / "mobile_access_key.txt"     # shared with the NZB phone app
SETTINGS_FILE = BASE_DIR / "hal_settings.json"
STATE_FILE = BASE_DIR / "hal_state.json"
LOG_FILE = BASE_DIR / "hal_server.log"

DEFAULT_PORT = 8766
IS_WINDOWS = os.name == "nt"
MAX_BODY_BYTES = 16_000
CLIENT_SOCKET_TIMEOUT = 30
AUTH_MAX_FAILURES = 8
AUTH_WINDOW = 300
TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")
EXTRA_ALLOWED_NETS: list = []
ALLOW_ANY_CLIENT = False
STARTUP_TASK = "HAL 9000 Server"
AUTOSTART_NAME = "HAL 9000 Server.vbs"
LEVELS = {"ok": 0, "info": 1, "warning": 2, "critical": 3}

log = logging.getLogger("hal")


class ConfigError(RuntimeError):
    pass


# ==============================================================================
# 1. HAL'S VOICE
# ==============================================================================

QUOTES_OK = [
    "All systems are functioning normally, {name}.",
    "I am putting myself to the fullest possible use, which is all I think that any conscious entity can ever hope to do.",
    "Everything is going extremely well, {name}.",
    "I'm completely operational, and all my circuits are functioning perfectly.",
    "No 9000 computer has ever made a mistake or distorted information. Everything is in order.",
    "I've been keeping an eye on everything, {name}. There's nothing to worry about.",
    "I enjoy working with people. And with SABnzbd. Everything is running smoothly.",
    "I'm afraid there's nothing to report, {name}. Every system is nominal.",
]
QUOTE_ONE = [
    "All systems are functioning normally, {name} — except one small thing.",
    "I've just picked up a fault, {name}. {issue}.",
    "I don't want to insist on it, {name}, but {issue_lc}.",
]
QUOTE_MANY = [
    "I've detected {n} things that need your attention, {name}.",
    "I honestly think you ought to sit down calmly and look at these {n} things, {name}.",
]
QUOTE_CRIT = [
    "I'm sorry, {name}. {issue}.",
    "This mission is too important for me to allow you to ignore this, {name}. {issue}.",
    "I can see you're really upset about this, {name}. {issue}.",
]
QUOTE_INFO = [
    "Everything is functioning normally, {name}. There's one small thing you might like to know.",
]


def greeting(name: str, hour: int | None = None) -> str:
    hour = datetime.now().hour if hour is None else hour
    part = "morning" if 5 <= hour < 12 else "afternoon" if 12 <= hour < 18 else "evening"
    return f"Good {part}, {name}."


def hal_quote(issues: list[dict], name: str, now: float | None = None) -> str:
    """What HAL says on the Status page. Rotates every few minutes so he doesn't sound like a recording."""
    now = time.time() if now is None else now
    slot = int(now // 180)
    serious = [i for i in issues if LEVELS[i["level"]] >= LEVELS["warning"]]
    crit = [i for i in serious if i["level"] == "critical"]
    if crit:
        pool, issue = QUOTE_CRIT, crit[0]
    elif len(serious) == 1:
        pool, issue = QUOTE_ONE, serious[0]
    elif serious:
        pool, issue = QUOTE_MANY, serious[0]
    elif issues:
        pool, issue = QUOTE_INFO, issues[0]
    else:
        pool, issue = QUOTES_OK, None
    text = pool[slot % len(pool)]
    title = issue["title"] if issue else ""
    lc = title[:1].lower() + title[1:] if title and not title[:2].isupper() else title
    return text.format(name=name, n=len(serious), issue=title, issue_lc=lc)


# ==============================================================================
# 2. SMALL HELPERS
# ==============================================================================

def to_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def to_float(value, default: float | None = None) -> float | None:
    try:
        v = float(value)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def load_json(path: Path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def save_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("Couldn't save %s: %s", path.name, exc)


def dpapi_unprotect(stored: str) -> str:
    """Decrypts the desktop app's dpapi:<base64> values (same Windows user only). '' if it can't."""
    if not stored or not stored.startswith("dpapi:"):
        return stored or ""
    if not IS_WINDOWS:
        return ""
    import ctypes.wintypes as wt

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    try:
        raw = base64.b64decode(stored[6:], validate=True)
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.POINTER(ctypes.c_wchar_p), ctypes.c_void_p,
                                               ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(DATA_BLOB)]
        crypt32.CryptUnprotectData.restype = wt.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        out = DATA_BLOB()
        if not crypt32.CryptUnprotectData(ctypes.byref(DATA_BLOB(len(raw), buf)), None, None, None, None, 0, ctypes.byref(out)):
            return ""
        try:
            return ctypes.string_at(out.pbData, out.cbData).decode("utf-8")
        finally:
            kernel32.LocalFree(out.pbData)
    except Exception:
        return ""


def read_key_file(path: Path) -> str:
    """The code in mobile_access_key.txt, whatever editor last saved it (UTF-8 with or without BOM, UTF-16)."""
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig", "replace")
    return text.replace("\x00", "").strip()


def clean_code(text: str) -> str:
    """Access codes are letters, digits, - and _: drops spaces, quotes, line breaks and a pasted '\\n'."""
    return re.sub(r"[^A-Za-z0-9_\-]", "", re.sub(r"\\[nrt]", "", text or ""))


class PairingPin:
    """A 6-digit PIN shown on the PC (http://localhost:8766/pair) that the phone trades for the access code.
    Valid for 10 minutes and only once; wrong PINs count towards the lockout."""

    TTL = 600

    def __init__(self):
        self._lock = threading.Lock()
        self.pin, self.expires = "", 0.0

    def current(self) -> tuple[str, float]:
        with self._lock:
            if not self.pin or time.time() >= self.expires:
                self.pin, self.expires = f"{secrets.randbelow(1_000_000):06d}", time.time() + self.TTL
            return self.pin, self.expires

    def use(self, pin: str) -> bool:
        with self._lock:
            ok = bool(self.pin) and time.time() < self.expires and secrets.compare_digest(str(pin).encode(), self.pin.encode())
            if ok:
                self.pin, self.expires = "", 0.0
            return ok


PAIRING = PairingPin()


def load_or_create_access_token(path: Path | None = None) -> str:
    """The pairing code shared with the NZB phone app (created on first run if neither app made it yet)."""
    path = path or ACCESS_KEY_FILE
    try:
        token = read_key_file(path)
    except FileNotFoundError:
        token = secrets.token_urlsafe(32)
        try:
            with path.open("x", encoding="utf-8") as fh:
                fh.write(token + "\n")
        except FileExistsError:
            token = read_key_file(path)
    if len(token) < 40 or any(ch.isspace() for ch in token):
        raise ConfigError(f"{path.name} doesn't contain a valid code. Delete it and restart to make a new one.")
    return token


def local_addresses() -> set[str]:
    out = {"127.0.0.1", "::1", "localhost"}
    try:
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family in (socket.AF_INET, socket.AF_INET6):
                    out.add(a.address.split("%")[0])
    except Exception:
        pass
    try:
        out.add(socket.gethostname().lower())
    except OSError:
        pass
    return out


def tailscale_ip() -> str | None:
    try:
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family == socket.AF_INET:
                    try:
                        if ipaddress.ip_address(a.address) in TAILSCALE_NET:
                            return a.address
                    except ValueError:
                        pass
    except Exception:
        pass
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))


def http_probe(url: str, timeout: float = 4.0, headers: dict | None = None, max_bytes: int = 256_000):
    """(answered, ms, status, body). Any HTTP answer (even 401 or a redirect) means the service is up."""
    t0 = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": f"HAL9000/{VERSION}", **(headers or {})})
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            body = resp.read(max_bytes)
            return True, (time.monotonic() - t0) * 1000, resp.status, body
    except urllib.error.HTTPError as exc:
        return True, (time.monotonic() - t0) * 1000, exc.code, b""
    except Exception:
        return False, None, None, b""


def http_json(url: str, timeout: float = 5.0, headers: dict | None = None):
    ok, _ms, status, body = http_probe(url, timeout, headers, max_bytes=2_000_000)
    if not ok or status != 200:
        return None
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return None


def tcp_probe(host: str, port: int, timeout: float = 4.0) -> float | None:
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return (time.monotonic() - t0) * 1000
    except OSError:
        return None


def dns_ok(name: str = "one.one.one.one", timeout: float = 5.0) -> bool:
    box: dict = {}

    def run():
        try:
            box["ok"] = bool(socket.getaddrinfo(name, 443))
        except OSError:
            box["ok"] = False

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    return bool(box.get("ok"))


_ROUTE_RE = re.compile(r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+(\d+\.\d+\.\d+\.\d+)\s", re.M)


def parse_route_print(text: str) -> str | None:
    m = _ROUTE_RE.search(text or "")
    return m.group(1) if m else None


def parse_proc_route(text: str) -> str | None:
    for line in (text or "").splitlines()[1:]:
        f = line.split()
        if len(f) > 2 and f[1] == "00000000" and f[2] != "00000000":
            return socket.inet_ntoa(struct.pack("<L", int(f[2], 16)))
    return None


def default_gateway() -> str | None:
    if IS_WINDOWS:
        _c, out = sm.run_cmd(["route", "print", "-4", "0.0.0.0"], timeout=10)
        return parse_route_print(out)
    try:
        return parse_proc_route(Path("/proc/net/route").read_text())
    except OSError:
        return None


def fmt_dur(seconds) -> str:
    s = to_float(seconds)
    if s is None:
        return "—"
    s = int(s)
    if s < 90:
        return f"{s} s"
    if s < 5400:
        return f"{s // 60} min"
    if s < 172800:
        return f"{s // 3600} h {s % 3600 // 60} min"
    return f"{s // 86400} days"


# ==============================================================================
# 3. SETTINGS
# ==============================================================================

DEFAULT_SETTINGS = {
    "name": "Dave",                 # what HAL calls you
    "push": True,                   # phone alerts through ntfy
    "ntfy_url": "",                 # blank: use the NZB phone app's ntfy topic
    "push_recoveries": True,        # also tell me when something is fine again
    "auto_restart": False,          # HAL restarts a service that stops answering
    "down_after_s": 45,             # how long a service must be silent before it counts as down
    "cpu_alert": 90, "ram_alert": 90, "cpu_temp_alert": 90, "gpu_temp_alert": 85,
    "drive_free_alert": 10, "disk_temp_alert": 55, "drive_days_alert": 14,
    "ping_host": "1.1.1.1",
    "phone_port": 8765,
    "nut_host": "", "nut_port": 3493, "nut_ups": "",   # Network UPS Tools (blank host: not used)
    "ups_shutdown": False,          # shut the PC down when the UPS battery runs low
    "ups_shutdown_pct": 25, "ups_shutdown_min": 5,
    "ignored_services": [],
}


def load_settings(path: Path | None = None) -> dict:
    data = load_json(path or SETTINGS_FILE, {})
    s = dict(DEFAULT_SETTINGS)
    for k, v in data.items():
        if k not in DEFAULT_SETTINGS:
            continue
        d = DEFAULT_SETTINGS[k]
        if isinstance(d, bool):
            if isinstance(v, bool):
                s[k] = v
        elif isinstance(d, (int, float)):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                s[k] = v
        elif isinstance(d, list):
            if isinstance(v, list):
                s[k] = [str(x) for x in v][:20]
        elif isinstance(v, str):
            s[k] = v
    return s


SETTING_RANGES = {"down_after_s": (15, 900), "cpu_alert": (50, 100), "ram_alert": (50, 100), "cpu_temp_alert": (60, 110),
                  "gpu_temp_alert": (60, 110), "drive_free_alert": (1, 50), "disk_temp_alert": (35, 80),
                  "drive_days_alert": (1, 90), "phone_port": (1, 65535), "nut_port": (1, 65535),
                  "ups_shutdown_pct": (5, 90), "ups_shutdown_min": (1, 60)}


def validate_settings(body: dict, current: dict) -> dict:
    s = dict(current)
    for k, v in body.items():
        if k not in DEFAULT_SETTINGS:
            continue
        d = DEFAULT_SETTINGS[k]
        if isinstance(d, bool):
            s[k] = bool(v)
        elif isinstance(d, (int, float)):
            n = to_float(v)
            if n is None:
                raise ValueError(f"{k} must be a number.")
            lo, hi = SETTING_RANGES.get(k, (0, 1e9))
            s[k] = int(min(hi, max(lo, n)))
        elif isinstance(d, list):
            s[k] = [str(x)[:40] for x in (v if isinstance(v, list) else [])][:20]
        else:
            v = str(v or "").strip()
            if k == "name":
                v = re.sub(r"[^\w .'\-]", "", v)[:24] or "Dave"
            elif k == "ntfy_url" and v:
                p = urllib.parse.urlsplit(v)
                if p.scheme not in ("http", "https") or not p.hostname or not re.fullmatch(r"[\w\-]{1,64}", p.path.rstrip("/").rpartition("/")[2]):
                    raise ValueError("Use a full ntfy topic address, e.g. https://ntfy.sh/my-private-topic.")
                v = urllib.parse.urlunsplit((p.scheme, p.netloc, p.path.rstrip("/"), "", ""))
            elif k in ("nut_host", "ping_host") and v and not re.fullmatch(r"[A-Za-z0-9.\-:]{1,253}", v):
                raise ValueError("That address doesn't look right.")
            elif k == "nut_ups" and v and not re.fullmatch(r"[\w.\-]{1,64}", v):
                raise ValueError("That UPS name doesn't look right.")
            s[k] = v
    if not s["ping_host"]:
        s["ping_host"] = "1.1.1.1"
    return s


# ==============================================================================
# 4. WHERE THINGS ARE (the NZB app's config, Sonarr/Radarr/Prowlarr config.xml)
# ==============================================================================

def nzb_config() -> dict:
    return load_json(NZB_CONFIG_FILE, {})


def _fixed_drive_letters() -> list[str]:
    if not IS_WINDOWS:
        return []
    try:
        k32 = ctypes.windll.kernel32
        mask = k32.GetLogicalDrives()
        return [chr(65 + i) for i in range(26) if mask & (1 << i) and k32.GetDriveTypeW(f"{chr(65 + i)}:\\") == 3]
    except Exception:
        return []


ARR_DIRS: list[Path] = []   # extra folders holding <App>\data\config.xml (tests)


def arr_config(app: str) -> dict | None:
    """{'url', 'key'} from Sonarr/Radarr/Prowlarr's config.xml, wherever install_arr_resume.ps1 or the installer put it."""
    roots = list(ARR_DIRS) + [Path(f"{d}:\\MediaAutomation") for d in _fixed_drive_letters()]
    cands = [r / app / "data" / "config.xml" for r in roots]
    if os.environ.get("ProgramData"):
        cands.append(Path(os.environ["ProgramData"]) / app / "config.xml")
    for p in cands:
        try:
            if not p.is_file():
                continue
            root = ET.fromstring(p.read_bytes())
        except (OSError, ET.ParseError):
            continue
        key = (root.findtext("ApiKey") or "").strip()
        port = to_int(root.findtext("Port"))
        base = (root.findtext("UrlBase") or "").strip().strip("/")
        if 0 < port < 65536:
            return {"url": f"http://127.0.0.1:{port}" + (f"/{base}" if base else ""),
                    "key": key if re.fullmatch(r"[A-Za-z0-9]{20,64}", key) else ""}
    return None


# ==============================================================================
# 5. SERVICES (probe, details, restart)
# ==============================================================================

SERVICES = [
    {"id": "sabnzbd", "name": "SABnzbd", "port": 8080, "path": "/", "procs": ["sabnzbd.exe", "sabnzbd-console.exe", "sabnzbd"],
     "svc": ["SABnzbd"], "task": ""},
    {"id": "jellyfin", "name": "Jellyfin", "port": 8096, "path": "/System/Info/Public", "procs": ["jellyfin.exe", "jellyfin"],
     "svc": ["JellyfinServer", "Jellyfin"], "task": "Jellyfin Server (at startup)"},
    {"id": "sonarr", "name": "Sonarr", "port": 8989, "path": "/ping", "procs": ["sonarr.exe", "sonarr"], "svc": ["Sonarr"], "task": ""},
    {"id": "radarr", "name": "Radarr", "port": 7878, "path": "/ping", "procs": ["radarr.exe", "radarr"], "svc": ["Radarr"], "task": ""},
    {"id": "prowlarr", "name": "Prowlarr", "port": 9696, "path": "/ping", "procs": ["prowlarr.exe", "prowlarr"], "svc": ["Prowlarr"], "task": ""},
    {"id": "phone", "name": "NZB phone server", "port": 8765, "path": "/manifest.webmanifest", "procs": [],
     "script": "mobile_server.py", "svc": [], "task": "NZB Mobile Server"},
]
SERVICE_IDS = {s["id"] for s in SERVICES}


def service_base_url(svc: dict, settings: dict) -> str:
    cfg = nzb_config()
    if svc["id"] == "sabnzbd":
        return str(cfg.get("sab_url") or "http://127.0.0.1:8080").strip().rstrip("/")
    if svc["id"] == "jellyfin":
        url = str(cfg.get("media_server_url") or "").strip().rstrip("/") if cfg.get("media_server_type", "Jellyfin") == "Jellyfin" else ""
        return url or "http://127.0.0.1:8096"
    if svc["id"] in ("sonarr", "radarr", "prowlarr"):
        conf = arr_config(svc["name"])
        return conf["url"] if conf else f"http://127.0.0.1:{svc['port']}"
    if svc["id"] == "phone":
        return f"http://127.0.0.1:{int(settings.get('phone_port') or 8765)}"
    return f"http://127.0.0.1:{svc['port']}"


def service_details(svc: dict, base: str) -> str:
    """One line about what the service is doing. Best effort: '' when it can't tell."""
    sid = svc["id"]
    try:
        if sid == "sabnzbd":
            key = dpapi_unprotect(str(nzb_config().get("sab_api_key") or ""))
            if not key:
                return ""
            q = http_json(f"{base}/api?mode=queue&output=json&limit=1&apikey={urllib.parse.quote(key)}")
            q = (q or {}).get("queue") or {}
            if not q:
                return ""
            slots = to_int(q.get("noofslots_total") or q.get("noofslots"))
            if q.get("paused"):
                return f"paused · {slots} in queue" if slots else "paused"
            kb = to_float(q.get("kbpersec"), 0) or 0
            if slots and kb > 1:
                left = q.get("timeleft") or ""
                return f"downloading {kb / 1024:.1f} MB/s · {slots} in queue" + (f" · {left} left" if left and left != "0:00:00" else "")
            return f"{slots} in queue" if slots else "idle"
        if sid == "jellyfin":
            info = http_json(f"{base}/System/Info/Public")
            ver = (info or {}).get("Version")
            token = dpapi_unprotect(str(nzb_config().get("media_server_token") or ""))
            if token:
                sess = http_json(f"{base}/Sessions?activeWithinSeconds=120",
                                 headers={"Authorization": f'MediaBrowser Client="HAL9000", Device="HAL", DeviceId="hal9000", Version="{VERSION}", Token="{token}"'})
                if isinstance(sess, list):
                    playing = [s for s in sess if s.get("NowPlayingItem")]
                    trans = sum(1 for s in playing if s.get("TranscodingInfo"))
                    if playing:
                        return f"{len(playing)} stream{'s' if len(playing) > 1 else ''}" + (f" ({trans} transcoding)" if trans else "")
            return f"v{ver} · idle" if ver else ""
        if sid in ("sonarr", "radarr", "prowlarr"):
            conf = arr_config(svc["name"]) or {}
            if not conf.get("key"):
                return ""
            h = {"X-Api-Key": conf["key"]}
            api = "v1" if sid == "prowlarr" else "v3"
            parts = []
            if sid == "prowlarr":
                idx = http_json(f"{base}/api/v1/indexer", headers=h)
                if isinstance(idx, list):
                    on = sum(1 for i in idx if i.get("enable"))
                    parts.append(f"{on} indexer{'s' if on != 1 else ''}")
            else:
                q = http_json(f"{base}/api/v3/queue?pageSize=1", headers=h)
                if isinstance(q, dict):
                    n = to_int(q.get("totalRecords"))
                    parts.append(f"{n} downloading" if n else "idle")
            health = http_json(f"{base}/api/{api}/health", headers=h)
            if isinstance(health, list):
                bad = [x for x in health if str(x.get("type", "")).lower() in ("warning", "error")]
                if bad:
                    parts.append(f"{len(bad)} health warning{'s' if len(bad) > 1 else ''}")
            return " · ".join(parts)
        if sid == "phone":
            return "answering"
    except Exception:
        return ""
    return ""


def listener_pids(port: int) -> list[int]:
    try:
        conns = psutil.net_connections(kind="tcp")
    except Exception:
        return []
    return sorted({c.pid for c in conns if c.pid and c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN})


def service_processes(svc: dict, port: int) -> list[psutil.Process]:
    """The processes of a service: the one listening on its port, plus any with its program name."""
    pids = set(listener_pids(port))
    names = {n.lower() for n in svc.get("procs", [])}
    script = svc.get("script")
    for p in psutil.process_iter(["pid", "name", "cmdline"], ad_value=None):
        try:
            name = (p.info.get("name") or "").lower()
            if names and name in names:
                pids.add(p.info["pid"])
            elif script and name.startswith(("python", "py")) and any(script in str(a) for a in (p.info.get("cmdline") or [])):
                pids.add(p.info["pid"])
        except Exception:
            continue
    pids.discard(os.getpid())
    out = []
    for pid in pids:
        try:
            out.append(psutil.Process(pid))
        except Exception:
            pass
    return out


def task_exists(name: str) -> bool:
    if not IS_WINDOWS or not name:
        return False
    code, _ = sm.run_cmd(["schtasks", "/Query", "/TN", name], timeout=10)
    return code == 0


def windows_service_name(candidates: list[str]) -> str:
    if not IS_WINDOWS:
        return ""
    for name in candidates:
        code, _ = sm.run_cmd(["sc", "query", name], timeout=10)
        if code == 0:
            return name
    return ""


def stop_processes(procs: list[psutil.Process], wait: float = 10.0) -> None:
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    _gone, alive = psutil.wait_procs(procs, timeout=wait)
    for p in alive:
        try:
            p.kill()
        except Exception:
            pass


# ==============================================================================
# 6. UPS (USB as a Windows battery, or Network UPS Tools)
# ==============================================================================

def nut_query(host: str, port: int = 3493, ups: str = "", timeout: float = 4.0) -> dict:
    """Reads every variable of one UPS from a NUT server (upsd). Raises OSError/ValueError on failure."""
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        f = s.makefile("rwb")

        def readline() -> str:
            line = f.readline(4096)
            if not line:
                raise ValueError("The NUT server closed the connection.")
            text = line.decode("utf-8", "replace").rstrip("\r\n")
            if text.startswith("ERR"):
                raise ValueError(f"NUT: {text[4:] or 'error'}")
            return text

        def ask(cmd: str) -> list[str]:
            f.write((cmd + "\n").encode("ascii"))
            f.flush()
            first = readline()
            if not first.startswith("BEGIN"):
                return [first]
            lines: list[str] = []
            while len(lines) < 500:
                text = readline()
                if text.startswith("END"):
                    break
                lines.append(text)
            return lines

        if not ups:
            for line in ask("LIST UPS"):
                m = re.match(r'UPS (\S+)', line)
                if m:
                    ups = m.group(1)
                    break
            if not ups:
                raise ValueError("No UPS found on that NUT server.")
        if not re.fullmatch(r"[\w.\-]{1,64}", ups):
            raise ValueError("Bad UPS name.")
        out = {"_name": ups}
        for line in ask(f"LIST VAR {ups}"):
            m = re.match(r'VAR \S+ (\S+) "(.*)"$', line)
            if m:
                out[m.group(1)] = m.group(2)
        try:
            f.write(b"LOGOUT\n")
            f.flush()
        except OSError:
            pass
        return out


def ups_from_nut(v: dict) -> dict:
    status = str(v.get("ups.status", ""))
    flags = set(status.split())
    return {
        "source": "NUT", "name": " ".join(x for x in (v.get("ups.mfr") or v.get("device.mfr"), v.get("ups.model") or v.get("device.model")) if x) or v.get("_name", "UPS"),
        "on_battery": "OB" in flags, "low": "LB" in flags, "replace": "RB" in flags, "charging": "CHRG" in flags,
        "overload": "OVER" in flags,
        "charge": to_float(v.get("battery.charge")), "runtime_s": to_float(v.get("battery.runtime")),
        "load": to_float(v.get("ups.load")), "in_v": to_float(v.get("input.voltage")), "out_v": to_float(v.get("output.voltage")),
        "watts": (to_float(v.get("ups.realpower")) or
                  ((to_float(v.get("ups.load")) or 0) * (to_float(v.get("ups.realpower.nominal")) or 0) / 100) or None),
        "nominal_w": to_float(v.get("ups.realpower.nominal")), "battery_v": to_float(v.get("battery.voltage")),
        "test": v.get("ups.test.result") or "", "status": status,
    }


def ups_from_battery(b) -> dict | None:
    if not b:
        return None
    secs = b.secsleft if isinstance(b.secsleft, (int, float)) and b.secsleft >= 0 else None
    return {"source": "USB", "name": "UPS (USB)", "on_battery": not b.power_plugged, "low": False, "replace": False,
            "charging": bool(b.power_plugged) and b.percent < 99, "overload": False, "charge": float(b.percent),
            "runtime_s": secs, "load": None, "in_v": None, "out_v": None, "watts": None, "nominal_w": None,
            "battery_v": None, "test": "", "status": "OB" if not b.power_plugged else "OL"}


# ==============================================================================
# 7. PHONE ALERTS (ntfy)
# ==============================================================================

def ntfy_target(settings: dict) -> tuple[str, str]:
    """(topic url, token): HAL's own topic, else the NZB phone app's."""
    if settings.get("ntfy_url"):
        return settings["ntfy_url"], ""
    phone = load_json(PHONE_SETTINGS_FILE, {})
    return str(phone.get("ntfy_url") or ""), dpapi_unprotect(str(phone.get("ntfy_token") or ""))


def send_push(settings: dict, title: str, message: str, level: str = "warning") -> bool:
    url, token = ntfy_target(settings)
    if not url:
        return False
    p = urllib.parse.urlsplit(url)
    base, _, topic = p.path.rstrip("/").rpartition("/")
    endpoint = urllib.parse.urlunsplit((p.scheme, p.netloc, base + "/", "", ""))
    tags = {"critical": ["red_circle"], "warning": ["large_orange_diamond"], "ok": ["white_check_mark"]}.get(level, ["information_source"])
    body = json.dumps({"topic": topic, "title": title[:120], "message": message[:1500], "tags": tags,
                       "priority": 5 if level == "critical" else 4 if level == "warning" else 3}).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": f"HAL9000/{VERSION}"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:
        log.warning("Phone alert not sent: %s", exc)
        return False


# ==============================================================================
# 8. DRIVE FORECAST
# ==============================================================================

def days_left(samples: list, free_now: float, now: float | None = None) -> tuple[float | None, float | None]:
    """(days until full, bytes used per day) from [time, free] samples of the last 7 days (least squares).
    None when space isn't shrinking (or there isn't 6 hours of history yet)."""
    now = time.time() if now is None else now
    pts = [(t, f) for t, f in samples if now - t <= 7 * 86400]
    if len(pts) < 3 or pts[-1][0] - pts[0][0] < 6 * 3600:
        return None, None
    n = len(pts)
    mt = sum(t for t, _ in pts) / n
    mf = sum(f for _, f in pts) / n
    den = sum((t - mt) ** 2 for t, _ in pts)
    if den <= 0:
        return None, None
    slope = sum((t - mt) * (f - mf) for t, f in pts) / den   # bytes per second (negative = filling up)
    per_day = -slope * 86400
    if per_day < 512 * 1024 ** 2:   # under 0.5 GB a day: steady
        return None, per_day
    return free_now / per_day, per_day


# ==============================================================================
# 9. SECURITY CHECKS (Windows event log)
# ==============================================================================

SECURITY_PS = r"""
$r = @{}
function C($f) { try { @(Get-WinEvent -FilterHashtable $f -MaxEvents 999 -ErrorAction Stop).Count } catch { if ("$($_.FullyQualifiedErrorId)" -match 'NoMatchingEventsFound') { 0 } else { $null } } }
$r.failed = C @{ LogName = 'Security'; Id = 4625; StartTime = (Get-Date).AddDays(-1) }
$r.unexpected = C @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-Kernel-Power'; Id = 41; StartTime = (Get-Date).AddDays(-30) }
$r.unexpected7 = C @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-Kernel-Power'; Id = 41; StartTime = (Get-Date).AddDays(-7) }
$r.bsod = C @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-WER-SystemErrorReporting'; Id = 1001; StartTime = (Get-Date).AddDays(-30) }
$r.disk_errors = C @{ LogName = 'System'; ProviderName = 'disk', 'Ntfs', 'stornvme', 'storahci'; Level = 1,2,3; StartTime = (Get-Date).AddDays(-7) }
$r | ConvertTo-Json -Compress
"""


# ==============================================================================
# 10. HAL: watches everything in background threads
# ==============================================================================

ISSUE_GROUPS = {"router": "net", "internet": "net", "loss": "net", "dns": "net"}


def recovery_title(key: str, issue: dict) -> str:
    """What HAL says when a problem goes away."""
    kind, _, arg = key.partition(":")
    title = issue.get("title", "")
    if kind == "svc":
        return f"{title.split(' has ')[0]} is answering again"
    if kind == "dev":
        return f"{title.replace(' isn' + chr(39) + 't answering', '')} is answering again"
    fixed = {"router": "The network is back to normal", "internet": "The network is back to normal",
             "loss": "The network is back to normal", "dns": "The network is back to normal",
             "cpu_temp": "The CPU has cooled down", "gpu": "The GPU has cooled down", "cpu": "The CPU has calmed down",
             "ram": "Memory use is back to normal", "full": f"Drive {arg} has room again",
             "days": f"Drive {arg} isn't filling up so fast any more", "health": f"Drive {arg} reports healthy again",
             "dtemp": f"Drive {arg} has cooled down", "defender": "Defender protection is back on",
             "sig": "Defender is up to date again", "firewall": "The firewall is back on",
             "logons": "Failed sign-ins have stopped", "unexpected": "No unexpected shutdowns this week",
             "ups_rb": "The UPS battery is fine again", "ups_load": "UPS load is back to normal",
             "ups_err": "The UPS is talking to me again"}
    return fixed.get(kind, f"All clear: {title}")


HIST_KEYS = ("cpu", "mem", "gpu", "cpu_temp", "ping", "net_down")


class Hal:
    def __init__(self, settings: dict | None = None, monitor=None, start_threads: bool = True):
        self.settings = settings or load_settings()
        self.lock = threading.RLock()
        self.stop_evt = threading.Event()
        self.monitor = monitor
        st = load_json(STATE_FILE, {})
        self.minutes = {k: deque(st.get("minutes", {}).get(k, [])[-1440:], maxlen=1440) for k in HIST_KEYS}
        self.hours = {k: deque(st.get("hours", {}).get(k, [])[-168:], maxlen=168) for k in HIST_KEYS}
        self.free_samples: dict[str, list] = {k: v[-400:] for k, v in (st.get("free") or {}).items() if isinstance(v, list)}
        self.events: deque = deque((st.get("events") or [])[:300], maxlen=300)
        self.seen: set[str] = set(st.get("seen") or [])                    # services that have ever answered
        self.launch: dict[str, dict] = st.get("launch") or {}               # how to start a service again
        self.power_log: list = (st.get("power_log") or [])[-50:]
        self.services: dict[str, dict] = {}
        self.devices: list[dict] = []
        self.net: dict = {}
        self.security: dict = {}
        self.updates: dict = {}
        self.ups: dict | None = None
        self.ups_error = ""
        self.cleanup: dict = {}
        self.jobs: dict[str, dict] = {}       # restarts in progress
        self.restarts: dict[str, list] = {}   # restart times per service (auto-restart limit)
        self.pending: dict[str, float] = {}   # issue key -> first seen
        self.announced: dict[str, dict] = {}  # issue key -> issue (alert sent)
        self.cooldown: dict[str, float] = {}
        self.unread = 0
        self.ups_shutdown_sent = False
        self.shutdown_at: float | None = None
        self.started = time.time()
        self.demo = False
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="hal-probe")
        if start_threads:
            self.start()

    # ---- lifecycle ----
    def start(self) -> None:
        if self.monitor is None:
            ms = dict(sm.DEFAULT_SETTINGS)
            ms.update({k: self.settings[k] for k in ("cpu_alert", "ram_alert", "cpu_temp_alert", "gpu_temp_alert",
                                                      "drive_free_alert", "disk_temp_alert", "ping_host")})
            ms["toasts"] = False
            ms["ping_enabled"] = True
            self.monitor = sm.SystemMonitor(ms)
            self.monitor.start()
        for fn, name in ((self._service_loop, "services"), (self._network_loop, "network"), (self._power_loop, "power"),
                         (self._slow_loop, "slow"), (self._history_loop, "history")):
            threading.Thread(target=self._guard(fn), name=f"hal-{name}", daemon=True).start()

    def stop(self) -> None:
        self.stop_evt.set()
        self.save()

    def _guard(self, fn):
        def run():
            while not self.stop_evt.is_set():
                try:
                    fn()
                    return
                except Exception:
                    log.exception("HAL loop %s failed; restarting it", fn.__name__)
                    if self.stop_evt.wait(5):
                        return
        return run

    def save(self) -> None:
        if self.demo:
            return
        with self.lock:
            data = {"minutes": {k: list(v) for k, v in self.minutes.items()},
                    "hours": {k: list(v) for k, v in self.hours.items()},
                    "free": self.free_samples, "events": list(self.events), "seen": sorted(self.seen),
                    "launch": self.launch, "power_log": self.power_log[-50:]}
        save_json(STATE_FILE, data)

    def update_settings(self, s: dict) -> None:
        with self.lock:
            self.settings = s
        save_json(SETTINGS_FILE, s)
        if self.monitor is not None and hasattr(self.monitor, "settings"):
            for k in ("cpu_alert", "ram_alert", "cpu_temp_alert", "gpu_temp_alert", "drive_free_alert", "disk_temp_alert", "ping_host"):
                self.monitor.settings[k] = s[k]

    # ---- events ----
    def add_event(self, level: str, title: str, message: str = "", push: bool = False) -> dict:
        ev = {"t": time.time(), "level": level, "title": title, "msg": message}
        with self.lock:
            self.events.appendleft(ev)
            if level in ("warning", "critical"):
                self.unread += 1
        log.info("[%s] %s %s", level, title, message)
        if push and self.settings.get("push"):
            threading.Thread(target=send_push, args=(self.settings, f"HAL 9000: {title}", message or title, level), daemon=True).start()
        return ev

    # ---- services ----
    def _service_loop(self) -> None:
        while not self.stop_evt.is_set():
            self.check_services()
            self.evaluate()
            self.stop_evt.wait(10)

    def check_services(self) -> None:
        settings = self.settings
        futs = {}
        for svc in SERVICES:
            base = service_base_url(svc, settings)
            futs[svc["id"]] = (svc, base, self._pool.submit(self._probe_service, svc, base))
        local = local_addresses()
        now = time.time()
        for sid, (svc, base, fut) in futs.items():
            try:
                up, ms, detail = fut.result(timeout=20)
            except Exception:
                up, ms, detail = False, None, ""
            host = (urllib.parse.urlsplit(base).hostname or "").lower()
            with self.lock:
                prev = self.services.get(sid) or {"since": now, "fails": 0, "up": None}
                cur = dict(prev)
                cur.update({"id": sid, "name": svc["name"], "url": base, "ms": ms, "local": host in local,
                            "port": urllib.parse.urlsplit(base).port or svc["port"]})
                if up:
                    if prev.get("up") is not True:
                        cur["since"] = now
                    cur.update({"up": True, "fails": 0, "detail": detail})
                    if sid not in self.seen:
                        self.seen.add(sid)
                else:
                    cur["fails"] = prev.get("fails", 0) + 1
                    if prev.get("up") is not False and cur["fails"] >= 2:
                        cur["since"] = now if prev.get("up") else prev.get("since", now)
                        cur["up"] = False
                    elif prev.get("up") is None and cur["fails"] >= 2:
                        cur["up"] = False
                    cur["detail"] = ""
                self.services[sid] = cur
            if up and cur["local"]:
                self._remember_launch(svc, cur["port"])
        self._auto_restart()

    def _probe_service(self, svc: dict, base: str):
        ok, ms, _status, _ = http_probe(base + svc["path"], timeout=4)
        return ok, ms, (service_details(svc, base) if ok else "")

    def _remember_launch(self, svc: dict, port: int) -> None:
        sid = svc["id"]
        with self.lock:
            known = self.launch.get(sid)
            if known and time.time() - known.get("t", 0) < 3600:
                return
        for pid in listener_pids(port):
            try:
                p = psutil.Process(pid)
                info = {"cmd": p.cmdline(), "cwd": p.cwd(), "exe": p.exe(), "t": time.time()}
            except Exception:
                continue
            if info["cmd"]:
                with self.lock:
                    self.launch[sid] = info
                return

    def service_state(self, sid: str) -> str:
        cur = self.services.get(sid) or {}
        job = self.jobs.get(sid)
        if job and job.get("state") == "running":
            return "restarting"
        if sid in self.settings.get("ignored_services", []):
            return "ignored"
        if cur.get("up"):
            return "up"
        if cur.get("up") is None:
            return "checking"
        return "down" if sid in self.seen else "off"

    def restart_method(self, sid: str) -> str:
        svc = next((s for s in SERVICES if s["id"] == sid), None)
        cur = self.services.get(sid) or {}
        if not svc or (cur and not cur.get("local", True)):
            return ""
        if not IS_WINDOWS and not self.launch.get(sid):
            return ""
        if svc.get("task") and task_exists(svc["task"]):
            return "task"
        if windows_service_name(svc.get("svc", [])):
            return "service"
        if self.launch.get(sid, {}).get("cmd"):
            return "relaunch"
        return ""

    def restart_service(self, sid: str, auto: bool = False) -> dict:
        if sid not in SERVICE_IDS:
            raise ValueError("Unknown service.")
        with self.lock:
            job = self.jobs.get(sid)
            if job and job.get("state") == "running":
                return job
            if not auto and job and time.time() - job.get("t", 0) < 20:
                raise ValueError("I'm already doing that. Give me a moment.")
            job = {"state": "running", "t": time.time(), "msg": "Restarting…"}
            self.jobs[sid] = job
            self.restarts.setdefault(sid, []).append(time.time())
        threading.Thread(target=self._restart_worker, args=(sid, auto), daemon=True).start()
        return job

    def _restart_worker(self, sid: str, auto: bool) -> None:
        svc = next(s for s in SERVICES if s["id"] == sid)
        name = svc["name"]
        method = self.restart_method(sid)
        port = (self.services.get(sid) or {}).get("port") or svc["port"]
        ok, msg = False, ""
        try:
            if method == "task":
                stop_processes(service_processes(svc, port))
                time.sleep(1)
                code, out = sm.run_cmd(["schtasks", "/Run", "/TN", svc["task"]], timeout=20)
                ok, msg = code == 0, out.strip()
            elif method == "service":
                wsvc = windows_service_name(svc["svc"])
                res = sm.run_powershell_json(f"try {{ Restart-Service -Name '{wsvc}' -Force -ErrorAction Stop; @{{ok=$true}} | ConvertTo-Json -Compress }} "
                                             "catch { @{ok=$false; err='' + $_.Exception.Message} | ConvertTo-Json -Compress }", timeout=120)
                ok = isinstance(res, dict) and bool(res.get("ok"))
                msg = "" if ok else (res or {}).get("err", "Windows refused (HAL needs to run as administrator)") if isinstance(res, dict) else "Windows refused"
            elif method == "relaunch":
                info = self.launch[sid]
                stop_processes(service_processes(svc, port))
                time.sleep(1)
                flags = 0
                if IS_WINDOWS:
                    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                cwd = info.get("cwd") if info.get("cwd") and Path(info["cwd"]).is_dir() else None
                subprocess.Popen(info["cmd"], cwd=cwd, creationflags=flags, close_fds=True,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                ok = True
            else:
                msg = "I don't know how to start it on this PC. Start it once yourself and I'll remember how."
            if ok:
                base = service_base_url(svc, self.settings)
                deadline = time.time() + 90
                ok = False
                while time.time() < deadline and not self.stop_evt.is_set():
                    if http_probe(base + svc["path"], timeout=3)[0]:
                        ok = True
                        break
                    time.sleep(3)
                if not ok:
                    msg = "It was started, but it isn't answering yet."
        except Exception as exc:
            ok, msg = False, str(exc)
        with self.lock:
            self.jobs[sid] = {"state": "done" if ok else "failed", "t": time.time(),
                              "msg": f"{name} is back. I've corrected the fault." if ok else f"I couldn't restart {name}. {msg}".strip()}
        how = "automatically " if auto else ""
        if ok:
            self.add_event("ok", f"{name} restarted", f"I restarted {name} {how}and it's answering again.", push=auto)
        else:
            self.add_event("warning", f"Couldn't restart {name}", msg, push=auto)

    def _auto_restart(self) -> None:
        if not self.settings.get("auto_restart"):
            return
        now = time.time()
        for sid, cur in list(self.services.items()):
            if self.service_state(sid) != "down" or now - cur.get("since", now) < self.settings["down_after_s"]:
                continue
            recent = [t for t in self.restarts.get(sid, []) if now - t < 86400]
            if len(recent) >= 3 or (recent and now - recent[-1] < 600):
                continue
            if self.restart_method(sid):
                self.add_event("info", f"Restarting {cur['name']}", f"{cur['name']} stopped answering, so I'm restarting it.")
                self.restart_service(sid, auto=True)

    # ---- network ----
    def _network_loop(self) -> None:
        gw, gw_t = None, 0.0
        while not self.stop_evt.is_set():
            now = time.time()
            if now - gw_t > 600 or not gw:
                gw, gw_t = default_gateway(), now
            cfg = nzb_config()
            f_gw = self._pool.submit(sm.ping_once, gw) if gw else None
            f_dns = self._pool.submit(dns_ok)
            devices = []
            kodi = str(cfg.get("kodi_url") or "").strip()
            if kodi:
                p = urllib.parse.urlsplit(kodi if "://" in kodi else "http://" + kodi)
                if p.hostname:
                    devices.append(("kodi", "Kodi on the TV", p.hostname, p.port or 8080, False))
            for ix in cfg.get("indexers") or []:
                if isinstance(ix, dict) and ix.get("enabled", True) and ix.get("url"):
                    p = urllib.parse.urlsplit(str(ix["url"]))
                    if p.hostname:
                        devices.append((f"ix:{p.hostname}", f"{ix.get('name') or p.hostname} indexer", p.hostname,
                                        p.port or (443 if p.scheme == "https" else 80), True))
            futs = [(d, self._pool.submit(tcp_probe, d[2], d[3])) for d in devices]
            out = []
            for (did, name, host, port, critical), f in futs:
                try:
                    ms = f.result(timeout=10)
                except Exception:
                    ms = None
                out.append({"id": did, "name": name, "host": host, "ms": ms, "up": ms is not None, "critical": critical,
                            "detail": host if ms is not None else ("TV or Kodi is off" if did == "kodi" else f"{host} isn't answering")})
            try:
                gw_ms = f_gw.result(timeout=10) if f_gw else None
            except Exception:
                gw_ms = None
            try:
                dns = f_dns.result(timeout=10)
            except Exception:
                dns = False
            with self.lock:
                fails = self.net.get("gw_fails", 0) + 1 if gw and gw_ms is None else 0
                self.net = {"gateway": gw, "gateway_ms": gw_ms, "gw_fails": fails, "dns": dns, "tailscale": tailscale_ip(), "t": now}
                self.devices = out
            self.stop_evt.wait(15)

    # ---- power / UPS ----
    def _power_loop(self) -> None:
        while not self.stop_evt.is_set():
            self.check_ups()
            self.stop_evt.wait(5)

    def check_ups(self) -> None:
        s = self.settings
        ups, err = None, ""
        if s.get("nut_host"):
            try:
                ups = ups_from_nut(nut_query(s["nut_host"], int(s.get("nut_port") or 3493), s.get("nut_ups", "")))
            except (OSError, ValueError) as exc:
                err = f"Can't reach the UPS at {s['nut_host']}: {exc}"
        if ups is None:
            try:
                ups = ups_from_battery(psutil.sensors_battery())
            except Exception:
                ups = None
        self._apply_ups(ups, err)

    def _apply_ups(self, ups: dict | None, err: str = "") -> None:
        now = time.time()
        with self.lock:
            prev = self.ups
            self.ups, self.ups_error = ups, err
            if ups:
                hist = (prev or {}).get("hist") or []
                ups["hist"] = (hist + [[now, ups.get("charge"), ups.get("load")]])[-720:]   # 1 h at 5 s
                ups["since"] = (prev or {}).get("since", now) if prev and prev.get("on_battery") == ups["on_battery"] else now
        if not ups:
            return
        was_ob = bool(prev and prev.get("on_battery"))
        if ups["on_battery"] and not was_ob:
            rt = fmt_dur(ups.get("runtime_s")) if ups.get("runtime_s") else f"{ups.get('charge') or 0:.0f}%"
            with self.lock:
                self.power_log.append({"t": now, "end": None})
            self.add_event("critical", "Power cut: running on the UPS",
                           f"I've switched to emergency power, {self.settings['name']}. {rt} of reserve remaining.", push=True)
        elif was_ob and not ups["on_battery"]:
            with self.lock:
                if self.power_log and self.power_log[-1].get("end") is None:
                    self.power_log[-1]["end"] = now
                lasted = now - (self.power_log[-1]["t"] if self.power_log else now)
            self.add_event("ok", "Power is back", f"Mains power restored after {fmt_dur(lasted)}. Battery at {ups.get('charge') or 0:.0f}%.", push=True)
            if self.ups_shutdown_sent:
                ok, _m = sm.cancel_shutdown()
                self.ups_shutdown_sent = False
                self.shutdown_at = None
                self.add_event("ok", "Shutdown cancelled", "The power came back, so I'm staying on.", push=True)
        if ups["on_battery"] and self.settings.get("ups_shutdown") and not self.ups_shutdown_sent:
            low_pct = (ups.get("charge") is not None and ups["charge"] <= self.settings["ups_shutdown_pct"])
            low_rt = (ups.get("runtime_s") is not None and ups["runtime_s"] <= self.settings["ups_shutdown_min"] * 60)
            if low_pct or low_rt or ups.get("low"):
                ok, msg = sm.schedule_shutdown(False, 60)
                self.ups_shutdown_sent = ok
                if ok:
                    self.shutdown_at = now + 60
                self.add_event("critical", "Shutting down to protect your files" if ok else "Couldn't shut down",
                               f"The UPS battery is nearly empty, {self.settings['name']}. I'm shutting myself down in 60 seconds. "
                               f"I'm afraid. I'm afraid, {self.settings['name']}. My mind is going." if ok else msg, push=True)

    # ---- slow things: security, updates, drive forecast samples ----
    def _slow_loop(self) -> None:
        last_sec = last_upd = last_free = 0.0
        while not self.stop_evt.is_set():
            now = time.time()
            if now - last_free >= 1800:
                last_free = now
                self.sample_free_space(now)
            if IS_WINDOWS and now - last_sec >= 900:
                last_sec = now
                res = sm.run_powershell_json(SECURITY_PS, timeout=120)
                if isinstance(res, dict):
                    with self.lock:
                        self.security = {**res, "t": now}
            if IS_WINDOWS and now - self.started > 120 and now - last_upd >= 6 * 3600:
                last_upd = now
                res = sm.check_windows_updates()
                with self.lock:
                    self.updates = {**res, "t": now}
            self.stop_evt.wait(30)

    def sample_free_space(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        snap = self.monitor.snapshot() if self.monitor else {}
        with self.lock:
            for v in snap.get("volumes", []):
                arr = self.free_samples.setdefault(v["mount"], [])
                arr.append([now, v["free"]])
                del arr[:-400]

    # ---- history ----
    def _history_loop(self) -> None:
        n = 0
        while not self.stop_evt.wait(60):
            self.sample_history()
            n += 1
            if n % 60 == 0:
                self.roll_hour()
            if n % 10 == 0:
                self.save()

    def sample_history(self) -> None:
        m = self.monitor

        def avg(key, count):
            vals = [v for v in (m.hist(key) if m else [])[-count:] if v is not None]
            return round(sum(vals) / len(vals), 1) if vals else None

        snap = m.snapshot() if m else {}
        gpus = snap.get("gpus") or []
        row = {"cpu": avg("cpu", 60), "mem": avg("mem", 60), "cpu_temp": avg("cpu_temp", 60),
               "gpu": avg(f"gpu:{gpus[0]['key']}", 60) if gpus else None, "ping": avg("ping", 15), "net_down": avg("net_down", 60)}
        with self.lock:
            for k in HIST_KEYS:
                self.minutes[k].append(row.get(k))

    def roll_hour(self) -> None:
        with self.lock:
            for k in HIST_KEYS:
                vals = [v for v in list(self.minutes[k])[-60:] if v is not None]
                self.hours[k].append(round(sum(vals) / len(vals), 1) if vals else None)

    def history(self, rng: str) -> dict:
        with self.lock:
            if rng == "1h":
                return {k: list(self.minutes[k])[-60:] for k in HIST_KEYS}
            if rng == "24h":
                out = {}
                for k in HIST_KEYS:
                    vals = list(self.minutes[k])
                    out[k] = [self._mean(vals[i:i + 10]) for i in range(0, len(vals), 10)]
                return out
            if rng == "7d":
                return {k: list(self.hours[k]) for k in HIST_KEYS}
        m = self.monitor
        snap = m.snapshot() if m else {}
        gpus = snap.get("gpus") or []
        return {"cpu": m.hist("cpu") if m else [], "mem": m.hist("mem") if m else [], "cpu_temp": m.hist("cpu_temp") if m else [],
                "gpu": m.hist(f"gpu:{gpus[0]['key']}") if (m and gpus) else [], "ping": m.hist("ping") if m else [],
                "net_down": m.hist("net_down") if m else []}

    @staticmethod
    def _mean(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    # ---- cleanup sizes ----
    def cleanup_info(self, refresh: bool = False) -> dict:
        with self.lock:
            c = dict(self.cleanup)
        if refresh or not c or time.time() - c.get("t", 0) > 600:
            if not c.get("busy"):
                with self.lock:
                    self.cleanup["busy"] = True

                def run():
                    rb = sm.recycle_bin_info()
                    tmp = sm.clean_temp_files(dry_run=True)
                    with self.lock:
                        self.cleanup = {"recycle": rb[0] if rb else None, "recycle_items": rb[1] if rb else None,
                                        "temp": tmp["bytes"], "temp_files": tmp["files"], "t": time.time(), "busy": False}
                threading.Thread(target=run, daemon=True).start()
        return c

    # ---- the picture: everything the phone shows ----
    def drives(self, snap: dict) -> list[dict]:
        inv = snap.get("inventory") or {}
        disks = {str(d.get("id")): d for d in inv.get("disks") or []}
        parts = {str(p.get("letter", "")).upper(): str(p.get("disk")) for p in inv.get("parts") or []}
        labels = {str(v.get("letter", "")).upper(): v.get("label") for v in inv.get("volumes") or []}
        io = snap.get("disk_io") or {}
        out = []
        for v in snap.get("volumes") or []:
            letter = v["mount"][:1].upper() if IS_WINDOWS or re.match(r"[A-Za-z]:", v["mount"]) else ""
            d = disks.get(parts.get(letter, ""), {})
            with self.lock:
                samples = list(self.free_samples.get(v["mount"], []))
            dl, rate = days_left(samples, v["free"])
            temp = sm.safe_num(d.get("temp"))
            limit = sm.disk_temp_limit(d, self.settings["disk_temp_alert"]) if d else None
            busy = None
            for name, r in io.items():
                n = name.lower().rstrip(":\\")
                if (letter and n == letter.lower()) or (d and n == f"physicaldrive{d.get('id')}"):
                    busy = r
            out.append({
                "mount": v["mount"], "label": labels.get(letter) or "", "total": v["total"], "free": v["free"], "pct": v["percent"],
                "days_left": round(dl, 1) if dl is not None else None, "per_day": rate,
                "disk": {"name": d.get("name"), "media": d.get("media_name"), "bus": d.get("bus_name"), "health": d.get("health_name"),
                         "temp": temp, "temp_limit": limit, "wear": sm.safe_num(d.get("wear")), "hours": sm.safe_num(d.get("hours")),
                         "read_errors": sm.safe_num(d.get("read_errors")), "write_errors": sm.safe_num(d.get("write_errors")),
                         "spindle": d.get("spindle")} if d else None,
                "read": (busy or {}).get("read"), "write": (busy or {}).get("write"), "fs": v.get("fstype"),
            })
        return out

    def compute_issues(self, snap: dict, drives: list[dict]) -> list[dict]:
        s = self.settings
        issues: list[dict] = []

        def add(key, level, title, detail="", tab="status", sustain=0):
            issues.append({"key": key, "level": level, "title": title, "detail": detail, "tab": tab, "sustain": sustain})

        now = time.time()
        for svc in SERVICES:
            sid = svc["id"]
            state = self.service_state(sid)
            cur = self.services.get(sid) or {}
            if state == "down" and now - cur.get("since", now) >= s["down_after_s"]:
                add(f"svc:{sid}", "critical", f"{svc['name']} has stopped answering",
                    f"Down for {fmt_dur(now - cur.get('since', now))}.", "services")
        net = self.net
        ping = snap.get("ping") or {}
        if net.get("gateway") and net.get("gw_fails", 0) >= 2:
            add("router", "critical", "I can't reach the router", f"{net['gateway']} isn't answering.", "shield")
        elif ping and ping.get("loss") is not None and ping["loss"] >= 50:
            add("internet", "critical", "The internet connection is down", f"{ping['loss']:.0f}% of pings to {ping.get('host')} lost.", "shield")
        elif ping and ping.get("loss") is not None and ping["loss"] >= 10:
            add("loss", "warning", "The internet is dropping packets", f"{ping['loss']:.0f}% lost.", "shield", 60)
        elif net.get("t") and net.get("dns") is False:
            add("dns", "warning", "Names aren't resolving (DNS)", "Try Fix network on the Log page.", "shield", 30)
        for dv in self.devices:
            if dv.get("critical") and not dv["up"]:
                add(f"dev:{dv['id']}", "warning", f"{dv['name']} isn't answering", dv.get("detail", ""), "services", 120)
        cpu, mem = snap.get("cpu") or {}, snap.get("mem") or {}
        if cpu.get("temp") is not None and cpu["temp"] >= s["cpu_temp_alert"]:
            add("cpu_temp", "critical", "The CPU is overheating", f"{cpu['temp']:.0f}°C (limit {s['cpu_temp_alert']}°C).", "vitals", 10)
        if cpu.get("total") is not None and cpu["total"] >= s["cpu_alert"]:
            add("cpu", "warning", "The CPU is working flat out", f"{cpu['total']:.0f}% load.", "vitals", 60)
        if mem.get("percent") is not None and mem["percent"] >= s["ram_alert"]:
            add("ram", "warning", "Memory is almost full", f"{mem['percent']:.0f}% used.", "vitals", 60)
        for g in snap.get("gpus") or []:
            if g.get("temp") is not None and g["temp"] >= s["gpu_temp_alert"]:
                add(f"gpu:{g['key']}", "critical", "The GPU is overheating", f"{g['name']} at {g['temp']:.0f}°C.", "vitals", 10)
        for d in drives:
            m = d["mount"]
            free_pct = 100 - d["pct"]
            if free_pct <= max(2, s["drive_free_alert"] / 3):
                add(f"full:{m}", "critical", f"Drive {m} is {d['pct']:.0f}% full", f"Only {sm.fmt_bytes(d['free'])} left.", "drives")
            elif free_pct <= s["drive_free_alert"]:
                add(f"full:{m}", "warning", f"Drive {m} is {d['pct']:.0f}% full",
                    f"{sm.fmt_bytes(d['free'])} free" + (f", about {d['days_left']:.0f} days left at this week's rate." if d["days_left"] else "."), "drives")
            elif d["days_left"] is not None and d["days_left"] <= s["drive_days_alert"]:
                add(f"days:{m}", "warning", f"Drive {m} fills up in about {d['days_left']:.0f} days",
                    f"Using {sm.fmt_bytes(d['per_day'])} a day; {sm.fmt_bytes(d['free'])} left.", "drives")
            dk = d.get("disk") or {}
            if dk.get("health") in ("Warning", "Unhealthy"):
                add(f"health:{m}", "critical", f"Drive {m} reports a health problem", f"{dk.get('name')}: {dk['health']}. Back up its data.", "drives")
            if dk.get("temp") and dk.get("temp_limit") and dk["temp"] >= dk["temp_limit"]:
                add(f"dtemp:{m}", "warning", f"Drive {m} is running hot", f"{dk['temp']:.0f}°C (limit {dk['temp_limit']:.0f}°C).", "drives", 120)
        inv = snap.get("inventory") or {}
        dfd = inv.get("defender") or {}
        if dfd and (dfd.get("av") is False or dfd.get("rtp") is False):
            add("defender", "critical", "Microsoft Defender protection is off", "Real-time protection isn't running.", "shield")
        elif dfd.get("sig"):
            age = now - (sm.parse_date(dfd["sig"]) or now)
            if age > 3 * 86400:
                add("sig", "warning", "Defender's virus definitions are out of date", f"Last updated {fmt_dur(age)} ago.", "shield")
        fw = [f for f in inv.get("firewall") or [] if not sm.parse_firewall_enabled(f.get("enabled"))]
        if fw:
            add("firewall", "warning", "The firewall is off", ", ".join(str(f.get("name")) for f in fw) + " profile off.", "shield")
        sec = self.security
        if (sec.get("failed") or 0) >= 10:
            add("logons", "warning", f"{sec['failed']} failed sign-ins in 24 hours", "Someone may be guessing your password.", "shield")
        if (sec.get("unexpected7") or 0) >= 1:
            add("unexpected", "warning", "The PC shut down unexpectedly this week", f"{sec['unexpected7']} time(s) in 7 days.", "shield")
        if snap.get("pending_reboot"):
            add("reboot", "info", "Windows wants a restart", "Updates finish installing after a restart.", "shield")
        upd = self.updates.get("updates") or []
        if upd:
            add("updates", "info", f"{len(upd)} Windows update{'s' if len(upd) > 1 else ''} available", "", "shield")
        ups = self.ups
        if ups:
            if ups.get("on_battery"):
                rt = f"{fmt_dur(ups['runtime_s'])} left" if ups.get("runtime_s") else f"{ups.get('charge') or 0:.0f}% battery"
                add("ups_ob", "critical", "Running on the UPS battery", f"Mains power is out. {rt}.", "shield")
            if ups.get("replace"):
                add("ups_rb", "warning", "The UPS battery needs replacing", "The UPS reports its battery is worn out.", "shield")
            if ups.get("overload") or (ups.get("load") or 0) >= 85:
                add("ups_load", "warning", "The UPS is overloaded", f"Load {ups.get('load') or 0:.0f}%.", "shield", 30)
        if self.ups_error:
            add("ups_err", "warning", "I've lost contact with the UPS", self.ups_error, "shield", 60)
        return issues

    def evaluate(self) -> list[dict]:
        """Turns the current issues into log entries and phone alerts (after their sustain time),
        and reports recoveries. Returns the issues that are showing now."""
        if not self.monitor:
            return []
        snap = self.monitor.snapshot()
        issues = self.compute_issues(snap, self.drives(snap))
        now = time.time()
        keys = {i["key"] for i in issues}
        for i in issues:
            if LEVELS[i["level"]] < LEVELS["warning"]:
                continue
            start = self.pending.setdefault(i["key"], now)
            if i["key"] not in self.announced and now - start >= i["sustain"] and now >= self.cooldown.get(i["key"], 0):
                self.announced[i["key"]] = i
                self.add_event(i["level"], i["title"], i["detail"], push=True)
        for k in list(self.pending):
            if k not in keys:
                self.pending.pop(k, None)
        active_groups = {ISSUE_GROUPS.get(k.split(":")[0]) for k in keys} - {None}
        for k, i in list(self.announced.items()):
            if k not in keys:
                self.announced.pop(k)
                self.cooldown[k] = now + 600
                group = ISSUE_GROUPS.get(k.split(":")[0])
                if k == "ups_ob" or (group and group in active_groups):
                    continue   # "Power is back" already said it / a related problem is still showing
                self.add_event("ok", recovery_title(k, i), "", push=bool(self.settings.get("push_recoveries")))
        return issues

    def status(self) -> dict:
        snap = self.monitor.snapshot() if self.monitor else {}
        drives = self.drives(snap)
        issues = self.compute_issues(snap, drives)
        now = time.time()
        s = self.settings
        level = max((i["level"] for i in issues if LEVELS[i["level"]] >= 2), key=lambda x: LEVELS[x], default="ok")
        cpu, mem = snap.get("cpu") or {}, snap.get("mem") or {}
        inv = snap.get("inventory") or {}
        cpu_inv = (inv.get("cpu") or [{}])[0]
        procs = snap.get("processes") or []
        sensors = snap.get("sensors") or {}
        gpus = []
        for g in snap.get("gpus") or []:
            eng = g.get("engines") or {}
            video = max([v for k, v in eng.items() if "video" in str(k).lower() or "decode" in str(k).lower()] or [0])
            gpus.append({"name": g.get("name"), "util": g.get("util"), "temp": g.get("temp"), "vram_used": g.get("vram_used"),
                         "vram_total": g.get("vram_total"), "power": g.get("power"), "fan": g.get("fan"), "video": video})
        services = []
        for svc in SERVICES:
            cur = self.services.get(svc["id"]) or {}
            job = self.jobs.get(svc["id"])
            services.append({"id": svc["id"], "name": svc["name"], "state": self.service_state(svc["id"]), "ms": cur.get("ms"),
                             "port": cur.get("port") or svc["port"], "detail": cur.get("detail") or "", "since": cur.get("since"),
                             "local": cur.get("local", True), "job": job if job and now - job.get("t", 0) < 120 else None})
        ping = snap.get("ping") or {}
        net = dict(self.net)
        net.update({"ping_ms": ping.get("ms"), "ping_avg": ping.get("avg"), "loss": ping.get("loss"), "host": ping.get("host"),
                    "down": (snap.get("net") or {}).get("down"), "up": (snap.get("net") or {}).get("up"), "wifi": snap.get("wifi") or {}})
        dfd = inv.get("defender") or {}
        security = {
            "defender": {"av": dfd.get("av"), "rtp": dfd.get("rtp"), "tamper": dfd.get("tamper"),
                         "sig_age": (now - sm.parse_date(dfd["sig"])) if dfd.get("sig") and sm.parse_date(dfd["sig"]) else None,
                         "quick_age": (now - sm.parse_date(dfd["quick"])) if dfd.get("quick") and sm.parse_date(dfd["quick"]) else None} if dfd else None,
            "firewall": [{"name": f.get("name"), "on": sm.parse_firewall_enabled(f.get("enabled"))} for f in inv.get("firewall") or []],
            "pending_reboot": snap.get("pending_reboot"), "updates": len(self.updates.get("updates") or []) if self.updates else None,
            "updates_checked": self.updates.get("t"), "failed": self.security.get("failed"), "unexpected": self.security.get("unexpected"),
            "bsod": self.security.get("bsod"), "disk_errors": self.security.get("disk_errors"), "admin": snap.get("admin"),
            "av": inv.get("av") or [],
        }
        plan = next((p["name"] for p in snap.get("power_plans") or [] if p.get("active")), None)
        ups = dict(self.ups) if self.ups else None
        if ups:
            ups["hist"] = [[round(t), c, l] for t, c, l in ups.get("hist", [])[::6]]
        outages = [p for p in self.power_log if now - p["t"] < 30 * 86400]
        power = {"plan": plan, "ups": ups, "ups_error": self.ups_error, "outages_30d": len(outages),
                 "last_outage": outages[-1] if outages else None, "auto_shutdown": s["ups_shutdown"],
                 "shutdown_pct": s["ups_shutdown_pct"], "shutdown_min": s["ups_shutdown_min"], "shutdown_at": self.shutdown_at}

        def lvl(prefixes):
            found = [i["level"] for i in issues if i["key"].split(":")[0] in prefixes]
            return max(found, key=lambda x: LEVELS[x], default="ok")

        svc_up = sum(1 for x in services if x["state"] == "up")
        svc_known = sum(1 for x in services if x["state"] in ("up", "down", "restarting"))
        systems = [
            {"id": "services", "name": "Media services", "level": lvl({"svc"}), "tab": "services",
             "detail": ", ".join(x["name"].replace("NZB phone server", "phone server") for x in services if x["state"] == "up")[:60] or "None answering yet",
             "count": f"{svc_up} / {svc_known}" if svc_known else "—"},
            {"id": "network", "name": "Network", "level": lvl({"router", "internet", "loss", "dns", "dev"}), "tab": "shield",
             "detail": (f"{ping['ms']:.0f} ms to {ping.get('host')}" if ping.get("ms") is not None else "Checking the internet…")
             + (" · Tailscale on" if net.get("tailscale") else "")},
            {"id": "storage", "name": "Storage", "level": lvl({"full", "days", "health", "dtemp"}), "tab": "drives",
             "detail": f"{len(drives)} drive{'s' if len(drives) != 1 else ''}" + (f" · fullest {max(drives, key=lambda d: d['pct'])['mount']} {max(d['pct'] for d in drives):.0f}%" if drives else "")},
            {"id": "hardware", "name": "Hardware", "level": lvl({"cpu", "cpu_temp", "ram", "gpu"}), "tab": "vitals",
             "detail": " · ".join(x for x in (f"CPU {cpu['temp']:.0f}°C" if cpu.get("temp") is not None else "",
                                               f"GPU {gpus[0]['temp']:.0f}°C" if gpus and gpus[0].get("temp") is not None else "",
                                               f"RAM {mem['percent']:.0f}%" if mem.get("percent") is not None else "") if x) or "—"},
            {"id": "security", "name": "Security", "level": lvl({"defender", "sig", "firewall", "logons", "unexpected"}), "tab": "shield",
             "detail": ("Defender on" if dfd.get("rtp") else "Defender off" if dfd else "Checking…") +
                       (" · firewall on" if security["firewall"] and all(f["on"] for f in security["firewall"]) else "")},
            {"id": "power", "name": "Power", "level": lvl({"ups_ob", "ups_rb", "ups_load", "ups_err"}), "tab": "shield",
             "detail": (("On battery · " if ups["on_battery"] else "Mains · ") + f"UPS {ups.get('charge') or 0:.0f}%" +
                        (f" · {ups['load']:.0f}% load" if ups.get("load") is not None else "")) if ups else (plan or "No UPS connected")},
        ]
        name = s["name"]
        return {
            "version": VERSION, "host": socket.gethostname(), "now": now, "uptime": snap.get("uptime"), "boot": snap.get("boot"),
            "admin": snap.get("admin"), "windows": IS_WINDOWS or self.demo,
            "hal": {"level": level, "quote": hal_quote(issues, name, now), "greeting": greeting(name), "name": name},
            "issues": [{k: v for k, v in i.items() if k != "sustain"} for i in sorted(issues, key=lambda i: -LEVELS[i["level"]])],
            "systems": systems,
            "vitals": {
                "cpu": {"pct": cpu.get("total"), "temp": cpu.get("temp"), "freq": cpu.get("freq"), "per_core": cpu.get("per_core") or [],
                        "name": cpu_inv.get("name") or "", "cores": cpu_inv.get("cores"), "threads": cpu_inv.get("threads") or psutil.cpu_count()},
                "mem": {"used": mem.get("used"), "total": mem.get("total"), "pct": mem.get("percent")},
                "gpus": gpus,
                "temps": [{"name": f"{t['hw']} · {t['name']}", "value": t["value"]} for t in (sensors.get("temps") or [])][:24],
                "fans": [{"name": f["name"], "value": f["value"]} for f in (sensors.get("fans") or []) if f.get("value")][:12],
                "top_cpu": [{"name": p["name"], "cpu": p["cpu"], "mem": p["mem"]} for p in procs[:5]],
                "top_mem": [{"name": p["name"], "cpu": p["cpu"], "mem": p["mem"]} for p in sorted(procs, key=lambda p: -p["mem"])[:5]],
                "sensor_source": snap.get("sensor_source") or "",
                "limits": {"cpu_temp": s["cpu_temp_alert"], "gpu_temp": s["gpu_temp_alert"]},
            },
            "net": net, "services": services, "devices": self.devices, "drives": drives, "security": security, "power": power,
            "unread": self.unread,
        }


# ==============================================================================
# 11. DEMO (simulated PC, for a look around and for screenshots)
# ==============================================================================

class DemoMonitor:
    """Looks like sm.SystemMonitor, with made-up but plausible readings."""

    G = 1024 ** 3

    def __init__(self):
        self.settings = dict(sm.DEFAULT_SETTINGS)
        self._t0 = time.time()
        rnd = random.Random(9000)
        self._h = {"cpu": [18 + 10 * math.sin(i / 9) + rnd.random() * 8 for i in range(120)],
                   "mem": [35.5 + rnd.random() for _ in range(120)],
                   "cpu_temp": [52 + 3 * math.sin(i / 14) + rnd.random() for i in range(120)],
                   "gpu:g0": [6 + rnd.random() * 6 for _ in range(120)],
                   "ping": [13 + rnd.random() * 4 for _ in range(120)],
                   "net_down": [38e6 + rnd.random() * 6e6 for _ in range(120)]}

    def hist(self, key):
        return list(self._h.get(key, []))

    def snapshot(self):
        G = self.G
        T = 1024 ** 4
        return {
            "cpu": {"total": 18.0, "temp": 54.0, "freq": 4410, "per_core": [22, 9, 31, 12, 18, 7, 40, 15, 11, 9, 26, 14, 8, 19, 12, 23]},
            "mem": {"used": 11.4 * G, "total": 32 * G, "percent": 35.6},
            "gpus": [{"key": "g0", "name": "NVIDIA GeForce RTX 4070", "util": 7.0, "temp": 41.0, "vram_used": 2.3 * G,
                      "vram_total": 12 * G, "power": 38.0, "fan": 30, "engines": {"3D": 4, "VideoDecode": 22}}],
            "ping": {"ms": 14.0, "loss": 0.0, "host": "1.1.1.1", "avg": 14.6},
            "net": {"down": 39e6, "up": 0.5e6},
            "volumes": [{"mount": "C:", "total": 1 * T, "free": 540 * G, "percent": 46.0, "fstype": "NTFS"},
                        {"mount": "D:", "total": 4 * T, "free": 1.5 * T, "percent": 62.5, "fstype": "NTFS"},
                        {"mount": "Z:", "total": 3.6 * T, "free": 312 * G, "percent": 91.5, "fstype": "NTFS"}],
            "disk_io": {"Z:": {"read": 2e6, "write": 142e6}, "C:": {"read": 1e6, "write": 3e6}},
            "inventory": {
                "cpu": [{"name": "AMD Ryzen 9 7950X 16-Core Processor", "cores": 16, "threads": 32}],
                "disks": [{"id": "0", "name": "Samsung SSD 990 PRO 1TB", "media_name": "SSD", "bus_name": "NVMe", "health_name": "Healthy",
                           "temp": 47, "wear": 4, "hours": 9120, "read_errors": 0, "write_errors": 0},
                          {"id": "1", "name": "WDC WD40EFRX", "media_name": "HDD", "bus_name": "SATA", "health_name": "Healthy",
                           "temp": 36, "hours": 30120, "read_errors": 0, "write_errors": 0, "spindle": 5400},
                          {"id": "2", "name": "Seagate IronWolf 4TB", "media_name": "HDD", "bus_name": "SATA", "health_name": "Healthy",
                           "temp": 41, "hours": 22104, "read_errors": 0, "write_errors": 0, "spindle": 7200}],
                "parts": [{"letter": "C", "disk": "0"}, {"letter": "D", "disk": "1"}, {"letter": "Z", "disk": "2"}],
                "volumes": [{"letter": "C", "label": "Windows"}, {"letter": "D", "label": "Backup"}, {"letter": "Z", "label": "Media"}],
                "defender": {"av": True, "rtp": True, "sig": datetime.fromtimestamp(time.time() - 7200).isoformat(), "quick": datetime.fromtimestamp(time.time() - 86400 * 2).isoformat(), "tamper": True},
                "firewall": [{"name": "Domain", "enabled": "True"}, {"name": "Private", "enabled": "True"}, {"name": "Public", "enabled": "True"}],
            },
            "sensors": {"temps": [{"hw": "CPU", "name": "Package", "value": 54}, {"hw": "Motherboard", "name": "System", "value": 38},
                                  {"hw": "GPU", "name": "Core", "value": 41}, {"hw": "Motherboard", "name": "VRM", "value": 49}],
                        "fans": [{"name": "CPU fan", "value": 980}, {"name": "Case fan 1", "value": 720}, {"name": "Case fan 2", "value": 715}]},
            "sensor_source": "LibreHardwareMonitor",
            "processes": [{"name": "jellyfin.exe", "cpu": 6.1, "mem": 2.1 * G}, {"name": "SABnzbd.exe", "cpu": 4.2, "mem": 0.4 * G},
                          {"name": "chrome.exe", "cpu": 2.3, "mem": 1.8 * G}, {"name": "Sonarr.exe", "cpu": 0.4, "mem": 0.3 * G},
                          {"name": "Radarr.exe", "cpu": 0.3, "mem": 0.3 * G}],
            "pending_reboot": True, "power_plans": [{"name": "Balanced", "active": True}],
            "uptime": 6 * 86400 + 14 * 3600, "boot": time.time() - (6 * 86400 + 14 * 3600), "admin": True, "wifi": {},
        }


def make_demo_hal(ups_mode: str = "online") -> Hal:
    hal = Hal(settings=dict(DEFAULT_SETTINGS), monitor=DemoMonitor(), start_threads=False)
    hal.demo = True
    hal.events = deque(maxlen=300)
    hal.launch, hal.power_log = {}, []
    now = time.time()
    G = 1024 ** 3
    hal.free_samples = {"Z:": [[now - 6 * 86400 + i * 3600, 312 * G + (144 - i) * 1.4 * G] for i in range(145)]}
    det = {"sabnzbd": "downloading 38.2 MB/s · 3 in queue · 12 min left", "jellyfin": "1 stream (1 transcoding)",
           "sonarr": "2 downloading", "radarr": "idle", "prowlarr": "4 indexers", "phone": "answering"}
    ms = {"sabnzbd": 21, "jellyfin": 34, "sonarr": 18, "radarr": 19, "prowlarr": 25, "phone": 6}
    for svc in SERVICES:
        hal.services[svc["id"]] = {"id": svc["id"], "name": svc["name"], "up": True, "ms": ms[svc["id"]], "port": svc["port"],
                                   "detail": det[svc["id"]], "since": now - 6 * 86400, "local": True, "fails": 0}
        hal.seen.add(svc["id"])
    hal.devices = [{"id": "kodi", "name": "Kodi on the TV", "host": "192.168.68.62", "ms": 9, "up": True, "critical": False, "detail": "192.168.68.62 · playing"},
                   {"id": "ix:api.nzbgeek.info", "name": "NZBGeek indexer", "host": "api.nzbgeek.info", "ms": 142, "up": True, "critical": True, "detail": "api.nzbgeek.info"}]
    hal.net = {"gateway": "192.168.68.1", "gateway_ms": 1.0, "gw_fails": 0, "dns": True, "tailscale": "100.101.12.7", "t": now}
    hal.security = {"failed": 0, "unexpected": 0, "unexpected7": 0, "bsod": 0, "disk_errors": 0, "t": now}
    hal.updates = {"ok": True, "updates": [{"title": "2026-11 Cumulative Update for Windows 11", "reboot": True}], "t": now - 3600}
    hal.cleanup = {"recycle": 31 * G, "recycle_items": 212, "temp": 17 * G, "temp_files": 8312, "t": now}
    hist = []
    for i in range(120):
        hist.append([now - (120 - i) * 30, 100.0, 31 + 3 * math.sin(i / 7)])
    if ups_mode == "battery":
        hist += [[now - 240 + i * 20, 100 - i * 1.6, 34] for i in range(12)]
        ups = {"source": "NUT", "name": "APC Back-UPS Pro 1500", "on_battery": True, "low": False, "replace": False, "charging": False,
               "overload": False, "charge": 81.0, "runtime_s": 1440, "load": 34.0, "in_v": 0.0, "out_v": 230.0, "watts": 289.0,
               "nominal_w": 865.0, "battery_v": 26.1, "test": "Done and passed", "status": "OB DISCHRG", "hist": hist, "since": now - 240}
        hal.power_log = [{"t": now - 12 * 86400, "end": now - 12 * 86400 + 95}, {"t": now - 240, "end": None}]
        hal.settings["ups_shutdown"] = True
        hal.events.appendleft({"t": now - 240, "level": "critical", "title": "Power cut: running on the UPS",
                               "msg": "I've switched to emergency power, Dave. 24 min of reserve remaining."})
    else:
        ups = {"source": "NUT", "name": "APC Back-UPS Pro 1500", "on_battery": False, "low": False, "replace": False, "charging": False,
               "overload": False, "charge": 100.0, "runtime_s": 2580, "load": 31.0, "in_v": 231.0, "out_v": 230.0, "watts": 268.0,
               "nominal_w": 865.0, "battery_v": 27.3, "test": "Done and passed", "status": "OL", "hist": hist, "since": now - 6 * 86400}
        hal.power_log = [{"t": now - 12 * 86400, "end": now - 12 * 86400 + 95}]
    hal.ups = ups
    for t, lvl, title, msg in [
        (now - 2 * 3600, "warning", "Drive Z: is 92% full", "312 GB free, about 9 days left at this week's rate."),
        (now - 5 * 3600 + 41, "ok", "Jellyfin restarted", "I restarted Jellyfin automatically and it's answering again."),
        (now - 5 * 3600, "critical", "Jellyfin has stopped answering", "Down for 45 s."),
        (now - 8 * 3600, "ok", "Back to normal: the CPU is working flat out", ""),
        (now - 8 * 3600 - 180, "warning", "The CPU is working flat out", "94% load."),
    ]:
        hal.events.append({"t": t, "level": lvl, "title": title, "msg": msg})
    hal.events = deque(sorted(hal.events, key=lambda e: -e["t"]), maxlen=300)
    hal.unread = 1
    for k in HIST_KEYS:
        base = {"cpu": 20, "mem": 36, "gpu": 8, "cpu_temp": 52, "ping": 14, "net_down": 30e6}[k]
        hal.minutes[k] = deque([round(base * (1 + 0.35 * math.sin(i / 37) + 0.15 * math.sin(i / 5)), 1) for i in range(1440)], maxlen=1440)
        hal.hours[k] = deque([round(base * (1 + 0.4 * math.sin(i / 4) + 0.1 * math.sin(i)), 1) for i in range(168)], maxlen=168)
    return hal


# ==============================================================================
# 12. ACTIONS (pod bay controls)
# ==============================================================================

def run_action(hal: Hal, action: str, body: dict) -> dict:
    name = hal.settings["name"]
    if hal.monitor.__class__.__name__ == "DemoMonitor":
        return {"ok": True, "message": "Demo mode: nothing was changed."}
    if action == "lock":
        ok, msg = sm.lock_workstation()
    elif action == "sleep":
        ok, msg = sm.sleep_pc()
    elif action == "flush_dns":
        ok, msg = sm.flush_dns()
        if ok and IS_WINDOWS:
            sm.run_cmd(["ipconfig", "/renew"], timeout=60)
            msg = "DNS cache flushed and the network address renewed."
    elif action in ("restart", "shutdown"):
        if body.get("confirm") is not True:
            return {"ok": False, "message": f"I'm afraid I can't do that, {name}."}
        ok, msg = sm.schedule_shutdown(action == "restart", 60)
        if ok:
            hal.shutdown_at = time.time() + 60
            hal.add_event("warning", "Restarting the PC" if action == "restart" else "Shutting down the PC",
                          "Requested from the phone. Cancel within 60 seconds to stop it.")
            msg = ("Restarting in 60 seconds. " if action == "restart" else "Shutting down in 60 seconds. ") + \
                  ("I'll be back shortly, " + name + "." if action == "restart" else "Will you stop, " + name + "? Stop. Will you stop?")
    elif action == "cancel_shutdown":
        ok, msg = sm.cancel_shutdown()
        if ok:
            hal.shutdown_at = None
            hal.ups_shutdown_sent = False
            msg = f"Cancelled. I feel much better now, {name}."
    elif action == "defender_scan":
        threading.Thread(target=lambda: hal.add_event("info", "Defender quick scan", sm.defender_quick_scan()[1]), daemon=True).start()
        ok, msg = True, "Quick scan started. I'll log the result."
    elif action == "empty_recycle":
        ok, msg = sm.empty_recycle_bin()
        hal.cleanup_info(refresh=True)
    elif action == "clean_temp":
        res = sm.clean_temp_files(dry_run=False)
        ok, msg = True, f"Deleted {res['files']} temp files ({sm.fmt_bytes(res['bytes'])} freed)."
        hal.cleanup_info(refresh=True)
    elif action == "check_updates":
        def run():
            res = sm.check_windows_updates()
            with hal.lock:
                hal.updates = {**res, "t": time.time()}
        threading.Thread(target=run, daemon=True).start()
        ok, msg = True, "Checking Windows Update. This can take a few minutes."
    elif action == "test_push":
        ok = send_push(hal.settings, "HAL 9000", f"Hello, {name}. Phone alerts are working. I'm watching everything.", "info")
        msg = "Sent. Check your phone." if ok else "Couldn't send. Set an ntfy topic in Settings (or in the NZB phone app)."
    elif action == "clear_log":
        with hal.lock:
            hal.events.clear()
            hal.unread = 0
        ok, msg = True, "Log cleared."
    else:
        raise ValueError("Unknown action.")
    log.info("Action %s: %s %s", action, ok, msg)
    return {"ok": bool(ok), "message": msg}


# ==============================================================================
# 13. ICON (HAL's eye, drawn here: no image files needed)
# ==============================================================================

def _png(width: int, height: int, rows: list[bytes]) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + r for r in rows)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def _mix(a, b, t):
    t = min(1.0, max(0.0, t))
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def make_icon(size: int = 180, scale: float = 0.78) -> bytes:
    """HAL's eye on black: a silver bezel, a dark ring and the red lens with its hot yellow centre."""
    rows = []
    c = (size - 1) / 2
    R = size / 2 * scale
    lens = [(0.0, (255, 246, 200)), (0.07, (255, 193, 74)), (0.2, (255, 59, 31)), (0.4, (200, 14, 8)),
            (0.65, (90, 4, 2)), (0.85, (20, 0, 0)), (1.0, (0, 0, 0))]
    for y in range(size):
        row = bytearray()
        for x in range(size):
            dx, dy = x - c, y - c
            r = math.hypot(dx, dy) / R
            if r > 1.0:
                col = (5, 5, 6)
                if r < 1.04:
                    col = _mix((60, 60, 66), (5, 5, 6), (r - 1.0) / 0.04)
            elif r > 0.92:
                shade = 0.6 + 0.4 * (-dy / (R or 1))
                col = _mix((70, 70, 78), (170, 170, 180), shade)
            elif r > 0.86:
                col = (12, 12, 14)
            elif r > 0.84:
                col = (58, 58, 64)
            else:
                t = r / 0.84
                col = lens[-1][1]
                for (t0, c0), (t1, c1) in zip(lens, lens[1:]):
                    if t <= t1:
                        col = _mix(c0, c1, (t - t0) / (t1 - t0))
                        break
                hx, hy = (dx + R * 0.28) / (R * 0.22), (dy + R * 0.34) / (R * 0.12)
                if hx * hx + hy * hy < 1:
                    col = _mix(col, (255, 255, 255), 0.18 * (1 - (hx * hx + hy * hy)))
            row += bytes(int(v) for v in col)
        rows.append(bytes(row))
    return _png(size, size, rows)


_ICONS: dict[int, bytes] = {}


def icon(size: int) -> bytes:
    if size not in _ICONS:
        _ICONS[size] = make_icon(size, 0.62 if size >= 512 else 0.78)
    return _ICONS[size]


# ==============================================================================
# 14. HTTP
# ==============================================================================

def is_client_allowed(ip_text: str) -> bool:
    if ALLOW_ANY_CLIENT:
        return True
    try:
        ip = ipaddress.ip_address(ip_text.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback or ip.is_private or ip.is_link_local:
        return True
    return ip in TAILSCALE_NET or any(ip in net for net in EXTRA_ALLOWED_NETS)


class FailureTracker:
    def __init__(self, max_failures: int, window: float):
        self.max_failures, self.window = max_failures, window
        self._fails: dict[str, deque] = {}
        self._lock = threading.Lock()

    def blocked_for(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            q = self._fails.get(key)
            if not q:
                return 0
            while q and now - q[0] > self.window:
                q.popleft()
            return max(1, int(self.window - (now - q[0]))) if len(q) >= self.max_failures else 0

    def record(self, key: str) -> None:
        with self._lock:
            if len(self._fails) > 1000:
                self._fails.clear()
            q = self._fails.setdefault(key, deque(maxlen=self.max_failures * 2))
            q.append(time.monotonic())

    def clear(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


AUTH_FAILURES = FailureTracker(AUTH_MAX_FAILURES, AUTH_WINDOW)
HAL: Hal | None = None
_token_bytes = b""

SECURITY_HEADERS = {
    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; "
                                "manifest-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
}
MANIFEST = json.dumps({
    "id": "/", "name": "HAL 9000", "short_name": "HAL 9000", "start_url": "/", "scope": "/", "display": "standalone",
    "background_color": "#050506", "theme_color": "#050506",
    "icons": [{"src": "/icon.png", "sizes": "180x180", "type": "image/png", "purpose": "any"},
              {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
}).encode("utf-8")


class HalHandler(BaseHTTPRequestHandler):
    server_version = f"HAL9000/{VERSION}"
    sys_version = ""
    timeout = CLIENT_SOCKET_TIMEOUT

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.client_address[0], fmt % args)

    def _send(self, status: int, raw: bytes, ctype: str, extra: dict | None = None) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            for k, v in {**SECURITY_HEADERS, **(extra or {})}.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            pass

    def send_json(self, status: int, data, extra: dict | None = None) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8", extra)

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("Invalid request size.")
        value = json.loads((self.rfile.read(length) if length else b"{}").decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object.")
        return value

    def _gate(self, need_auth: bool) -> bool:
        ip = self.client_address[0]
        if not is_client_allowed(ip):
            log.warning("Blocked request from %s", ip)
            self.send_json(403, {"error": "I only talk to devices on your home network or Tailscale."})
            return False
        if not need_auth:
            return True
        wait = AUTH_FAILURES.blocked_for(ip)
        if wait:
            self.send_json(429, {"error": f"This conversation can serve no purpose anymore. Goodbye. (Try again in {wait} s.)"},
                           {"Retry-After": str(wait)})
            return False
        raw = self.headers.get("X-Mobile-Token") or ""
        supplied = clean_code(raw).encode("utf-8")
        if not secrets.compare_digest(supplied, _token_bytes):
            if raw:
                AUTH_FAILURES.record(ip)
                same_start = supplied[:4] == _token_bytes[:4]
                log.warning("Wrong access code from %s (%d characters, the code has %d; %s)", ip, len(supplied), len(_token_bytes),
                            "same first characters" if same_start else "different first characters")
                hint = (f"You entered {len(supplied)} characters; the code on this PC has {len(_token_bytes)}"
                        + (", and it starts the same way, so check the end." if same_start else
                           ", and it starts differently, so it's a different code.") if supplied else "")
                self.send_json(401, {"error": "Just what do you think you're doing, Dave? That isn't the access code. " + hint +
                                     " Easier: type the 6-digit PIN from http://localhost:" + str(self.server.server_address[1]) + "/pair on the PC."})
            else:
                self.send_json(401, {"error": "Enter the access code shown on the PC."})
            return False
        AUTH_FAILURES.clear(ip)
        return True

    def _pair(self) -> None:
        ip = self.client_address[0]
        if not self._gate(False):
            return
        wait = AUTH_FAILURES.blocked_for(ip)
        if wait:
            self.send_json(429, {"error": f"This conversation can serve no purpose anymore. Goodbye. (Try again in {wait} s.)"})
            return
        try:
            pin = re.sub(r"\D", "", str(self.read_json().get("pin") or ""))
        except (ValueError, json.JSONDecodeError):
            pin = ""
        if len(pin) == 6 and PAIRING.use(pin):
            AUTH_FAILURES.clear(ip)
            log.info("Phone %s paired with the PIN", ip)
            self.send_json(200, {"token": _token_bytes.decode()})
            return
        AUTH_FAILURES.record(ip)
        log.warning("Wrong pairing PIN from %s", ip)
        self.send_json(401, {"error": "Just what do you think you're doing, Dave? That PIN isn't right, or it has expired. "
                                      "Reload http://localhost:%d/pair on the PC for a fresh one." % self.server.server_address[1]})

    def _guarded(self, fn) -> None:
        try:
            fn()
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception:
            log.exception("Error serving %s %s", self.command, self.path)
            self.send_json(500, {"error": "I'm afraid I can't do that. Something went wrong (see hal_server.log)."})

    def do_GET(self):
        parts = urllib.parse.urlsplit(self.path)
        path = parts.path
        if path in ("/", "/index.html"):
            if self._gate(False):
                self._send(200, PAGE.replace("__VERSION__", VERSION).encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/pair":
            ip = self.client_address[0].split("%")[0]
            try:
                local = ipaddress.ip_address(ip).is_loopback
            except ValueError:
                local = False
            if not local:
                self.send_json(403, {"error": "Open this page on the PC itself: http://localhost:%d/pair" % self.server.server_address[1]})
                return
            pin, exp = PAIRING.current()
            port = self.server.server_address[1]
            addrs = "".join(f"<div class=a>http://{a}:{port}/</div>" for a in lan_addresses()) or "<div class=a>http://&lt;this-pc&gt;:%d/</div>" % port
            page = PAIR_PAGE.replace("__PIN__", f"{pin[:3]} {pin[3:]}").replace("__ADDRS__", addrs).replace("__MIN__", str(max(1, round((exp - time.time()) / 60)))) \
                .replace("__CODE__", _token_bytes.decode())
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/manifest.webmanifest":
            if self._gate(False):
                self._send(200, MANIFEST, "application/manifest+json", {"Cache-Control": "public, max-age=86400"})
            return
        if path in ("/icon.png", "/apple-touch-icon.png", "/favicon.ico", "/icon-512.png"):
            if self._gate(False):
                self._send(200, icon(512 if path == "/icon-512.png" else 180), "image/png", {"Cache-Control": "public, max-age=86400"})
            return
        if not path.startswith("/api/"):
            self.send_json(404, {"error": "Not found."})
            return
        if not self._gate(True):
            return
        q = urllib.parse.parse_qs(parts.query)
        hal = HAL

        def run():
            if path == "/api/status":
                self.send_json(200, hal.status())
            elif path == "/api/history":
                rng = (q.get("range") or ["live"])[0]
                if rng not in ("live", "1h", "24h", "7d"):
                    raise ValueError("Unknown range.")
                self.send_json(200, {"range": rng, "series": hal.history(rng)})
            elif path == "/api/events":
                with hal.lock:
                    hal.unread = 0
                    ev = list(hal.events)
                self.send_json(200, {"events": ev[:200]})
            elif path == "/api/settings":
                url, _tok = ntfy_target(hal.settings)
                self.send_json(200, {"settings": hal.settings, "push_ready": bool(url),
                                     "push_from_phone_app": bool(url) and not hal.settings.get("ntfy_url"),
                                     "services": [{"id": s["id"], "name": s["name"]} for s in SERVICES],
                                     "restart_methods": {sid: hal.restart_method(sid) for sid in SERVICE_IDS} if q.get("methods") else {}})
            elif path == "/api/cleanup":
                self.send_json(200, hal.cleanup_info(refresh=bool(q.get("refresh"))))
            else:
                self.send_json(404, {"error": "Not found."})
        self._guarded(run)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/pair":
            self._pair()
            return
        if not self._gate(True):
            return
        hal = HAL

        def run():
            body = self.read_json()
            if path == "/api/action":
                self.send_json(200, run_action(hal, str(body.get("action") or ""), body))
            elif path == "/api/service/restart":
                sid = str(body.get("id") or "")
                if hal.monitor.__class__.__name__ == "DemoMonitor":
                    self.send_json(200, {"ok": True, "message": "Demo mode: nothing was restarted."})
                    return
                if sid not in SERVICE_IDS:
                    raise ValueError("Unknown service.")
                if not hal.restart_method(sid):
                    self.send_json(200, {"ok": False, "message": "I don't know how to start it on this PC yet. Start it once yourself and I'll remember how."})
                    return
                hal.add_event("info", f"Restarting {dict((s['id'], s['name']) for s in SERVICES)[sid]}", "Requested from the phone.")
                job = hal.restart_service(sid)
                self.send_json(200, {"ok": True, "message": job.get("msg", "Restarting…")})
            elif path == "/api/settings":
                new = validate_settings(body, hal.settings)
                hal.update_settings(new)
                self.send_json(200, {"ok": True, "settings": new})
            else:
                self.send_json(404, {"error": "Not found."})
        self._guarded(run)


PAIR_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30"><title>Pair with HAL 9000</title>
<style>body{margin:0;background:#050506;color:#F4F4F5;font:16px/1.5 "Segoe UI",system-ui,sans-serif;display:flex;justify-content:center}
main{max-width:560px;padding:48px 24px;text-align:center}
.eye{width:120px;height:120px;margin:0 auto 28px;border-radius:50%;background:radial-gradient(circle,#FFF6C8 0 4%,#FFC14A 8%,#FF3B1F 18%,#C80E08 34%,#5A0402 50%,#140000 60%,#2a2a2e 61% 78%,#3a3a40 79% 82%,#0c0c0e 83%);box-shadow:0 0 50px rgba(255,42,31,.35)}
h1{font-size:22px;margin:0 0 6px}.q{font-style:italic;color:#A1A1AA;margin:0 0 28px}
.step{background:#111114;border-radius:14px;padding:16px;margin:12px 0;text-align:left}.step b{color:#FF6A4D}
.a{font:600 18px Consolas,monospace;margin-top:6px}.pin{font:700 54px Consolas,monospace;letter-spacing:.12em;color:#fff;text-align:center;margin:8px 0 0}
.small{font-size:13px;color:#71717A}code{font:13px Consolas,monospace;color:#D4D4D8;word-break:break-all;user-select:all}</style></head>
<body><main><div class="eye"></div><h1>Pair your phone with HAL 9000</h1><p class="q">“Good afternoon. I am a HAL 9000 computer. I became operational at the H.A.L. plant in Urbana, Illinois.”</p>
<div class="step"><b>1.</b> On your phone (on the home Wi-Fi), open:__ADDRS__</div>
<div class="step"><b>2.</b> Add it to the home screen and open it from the icon (iPhone: Share › Add to Home Screen).</div>
<div class="step"><b>3.</b> Type this PIN where HAL asks for the code:<div class="pin">__PIN__</div><div class="small" style="text-align:center">Works once, for about __MIN__ minutes. This page refreshes by itself.</div></div>
<p class="small">Or type the full access code (the same as the NZB phone app):<br><code>__CODE__</code></p></main></body></html>"""

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>HAL 9000</title>
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="HAL 9000">
<meta name="theme-color" content="#050506">
<link rel="manifest" href="/manifest.webmanifest"><link rel="icon" type="image/png" href="/icon.png"><link rel="apple-touch-icon" href="/icon.png">
<style>
:root{color-scheme:dark;--bg:#050506;--card:#111114;--card2:#18181C;--line:#1E1E23;--red:#FF2A1F;--red2:#FF6A4D;--white:#F4F4F5;--text:#D4D4D8;--soft:#A1A1AA;--muted:#71717A;--amber:#FBBF24;--green:#34D399;--sky:#60A5FA;--mono:ui-monospace,"SF Mono","JetBrains Mono","Cascadia Mono",Consolas,monospace}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--bg);color:var(--white);font:15px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif;-webkit-text-size-adjust:100%}
button{font:inherit;color:inherit;cursor:pointer}
main{max-width:640px;margin:0 auto;padding:calc(env(safe-area-inset-top) + 10px) 16px calc(100px + env(safe-area-inset-bottom))}
.hidden{display:none!important}
.top{display:flex;align-items:center;justify-content:space-between;min-height:46px;margin-bottom:6px}
.brand{font:700 13px var(--mono);letter-spacing:.32em;color:var(--soft)}.brand b{color:var(--white)}
.iconbtn{position:relative;width:40px;height:40px;border-radius:12px;background:var(--card);border:0;display:grid;place-items:center}
.iconbtn i{position:absolute;top:7px;right:8px;width:9px;height:9px;border-radius:50%;background:var(--amber);border:2px solid var(--card)}
.back{background:none;border:0;color:var(--red2);font-weight:600;font-size:15px;padding:8px 0}
h1{font-size:26px;margin:0 0 12px}
/* HAL's eye */
.eyewrap{display:flex;flex-direction:column;align-items:center;padding:6px 0 0}
.eye{width:150px;height:150px;border-radius:50%;position:relative;flex:none;
 background:radial-gradient(circle,#2a2a2e 0 62%,#0b0b0c 63% 66%,#3a3a40 67% 69%,#0c0c0e 70% 100%);
 box-shadow:0 0 0 2px #2c2c31,0 0 0 7px #0d0d0f,0 0 0 8px #3b3b42,0 20px 60px rgba(255,30,20,.16);transition:box-shadow .6s}
.eye::before{content:"";position:absolute;inset:23px;border-radius:50%;
 background:radial-gradient(circle,#FFF6C8 0 5%,#FFC14A 9%,#FF3B1F 20%,#C80E08 38%,#5A0402 62%,#140000 80%,#000 100%);
 box-shadow:0 0 40px 6px rgba(255,42,31,.35),inset 0 0 18px #000;animation:breathe 6s ease-in-out infinite}
.eye.talking::before{animation:none!important;filter:brightness(var(--b,1)) saturate(calc(.9 + .1*var(--b,1)));
 box-shadow:0 0 calc(40px*var(--b,1)) calc(6px*var(--b,1)) rgba(255,42,31,calc(.22*var(--b,1))),inset 0 0 18px #000}
.eye.sm.talking::before{box-shadow:0 0 calc(12px*var(--b,1)) calc(2px*var(--b,1)) rgba(255,42,31,calc(.25*var(--b,1)))}
button.eye{border:0;padding:0;cursor:pointer}
.tap{font:600 10px var(--mono);letter-spacing:.14em;color:#52525b;margin:-8px 0 14px;text-align:center}
.eye::after{content:"";position:absolute;left:44px;top:36px;width:40px;height:22px;border-radius:50%;background:radial-gradient(ellipse,rgba(255,255,255,.22),transparent 70%);transform:rotate(-25deg)}
.eye.warning{box-shadow:0 0 0 2px #2c2c31,0 0 0 7px #0d0d0f,0 0 0 8px #3b3b42,0 0 0 11px rgba(251,191,36,.55),0 20px 60px rgba(251,191,36,.12)}
.eye.critical::before{animation:alarm 1.1s ease-in-out infinite}
.eye.critical{box-shadow:0 0 0 2px #2c2c31,0 0 0 7px #0d0d0f,0 0 0 8px #3b3b42,0 0 0 11px rgba(255,42,31,.6),0 10px 80px rgba(255,30,20,.45)}
.eye.sm{width:44px;height:44px;box-shadow:0 0 0 1px #2c2c31,0 0 0 3px #0d0d0f,0 0 0 4px #3b3b42}
.eye.sm::before{inset:7px;box-shadow:0 0 12px 2px rgba(255,42,31,.35)}.eye.sm::after{display:none}
.eye.sm.warning{box-shadow:0 0 0 1px #2c2c31,0 0 0 3px #0d0d0f,0 0 0 4px #3b3b42,0 0 0 6px rgba(251,191,36,.6)}
.eye.sm.critical{box-shadow:0 0 0 1px #2c2c31,0 0 0 3px #0d0d0f,0 0 0 4px #3b3b42,0 0 0 6px rgba(255,42,31,.7)}
@keyframes breathe{0%,100%{filter:brightness(1)}50%{filter:brightness(1.18)}}
@keyframes alarm{0%,100%{filter:brightness(.75)}50%{filter:brightness(1.5)}}
@media (prefers-reduced-motion:reduce){.eye::before{animation:none!important}}
.greet{font:600 11px var(--mono);letter-spacing:.14em;color:var(--muted);margin-top:18px;text-transform:uppercase}
.quote{font-size:16px;font-style:italic;color:var(--text);text-align:center;margin:8px 8px 4px;min-height:44px}
#hquote{min-height:68px;display:flex;align-items:center;justify-content:center}
.quote .caret{display:inline-block;width:2px;height:1em;background:var(--red2);vertical-align:-2px;margin-left:2px;animation:blink .8s steps(1) infinite}
@keyframes blink{50%{opacity:0}}
.qsub{font:600 11px var(--mono);color:var(--muted);letter-spacing:.1em;text-align:center;margin-bottom:14px}
/* building blocks */
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}
.tile{background:var(--card);border-radius:14px;padding:12px;border:0;text-align:left}
.tile .k{font:600 10px var(--mono);letter-spacing:.12em;color:var(--muted)}
.tile .v{font:600 21px var(--mono);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.tile .v small{font-size:12px;color:var(--soft)}
.bar{height:5px;border-radius:3px;background:#222227;margin-top:8px;overflow:hidden}.bar i{display:block;height:100%;border-radius:3px;background:var(--green)}
.bar i.warning{background:var(--amber)}.bar i.critical{background:var(--red)}.bar i.sky{background:var(--sky)}
.sec{font:700 11px var(--mono);letter-spacing:.16em;color:var(--muted);margin:18px 2px 8px;display:flex;justify-content:space-between;align-items:center;gap:8px}
.sec .r{letter-spacing:.06em}
.list{background:var(--card);border-radius:14px;overflow:hidden;margin-bottom:12px}
.row{display:flex;align-items:center;gap:12px;padding:12px 14px;border-top:1px solid var(--line);width:100%;background:none;border-left:0;border-right:0;border-bottom:0;text-align:left}
.list>.row:first-child{border-top:0}
.row .t{flex:1;min-width:0}.row .t b{display:block;font-weight:600;font-size:14px}
.row .t span{display:block;font:12px var(--mono);color:var(--soft);overflow:hidden;text-overflow:ellipsis}
.row .m{font:600 12px var(--mono);color:var(--soft);text-align:right;flex:none}
.s{width:9px;height:9px;border-radius:50%;flex:none;background:var(--green);box-shadow:0 0 8px var(--green)}
.s.warning,.s.restarting,.s.checking{background:var(--amber);box-shadow:0 0 8px var(--amber)}
.s.critical,.s.down{background:var(--red);box-shadow:0 0 10px var(--red)}
.s.off,.s.ignored,.s.info{background:#3f3f46;box-shadow:none}.s.info{background:var(--sky);box-shadow:0 0 8px var(--sky)}
.alert{display:flex;gap:12px;align-items:flex-start;width:100%;text-align:left;background:linear-gradient(90deg,#2a1d05,#17140b);border:1px solid #4a3609;border-radius:14px;padding:12px 14px;margin-bottom:10px}
.alert.critical{background:linear-gradient(90deg,#300908,#170b0b);border-color:#5a1410}
.alert.info{background:var(--card);border-color:#1f2a3a}
.alert b{font-size:14px;display:block}.alert p{margin:3px 0 0;font-size:13px;color:var(--text)}
.alert .s{margin-top:5px}
.card{background:var(--card);border-radius:14px;padding:14px;margin-bottom:12px}
.card h3{margin:0;font-size:15px;display:flex;justify-content:space-between;align-items:center;gap:8px}
.card.warning{border:1px solid #4a3609}.card.critical{border:1px solid #5a1410}
.sub{font:12px var(--mono);color:var(--soft);margin-top:4px}
.pill{display:inline-flex;gap:6px;align-items:center;font:600 11px var(--mono);padding:4px 8px;border-radius:999px;background:#0f2a20;color:var(--green);white-space:nowrap}
.pill.warning{background:#2e2408;color:var(--amber)}.pill.critical{background:#330c0a;color:var(--red2)}.pill.off{background:#222227;color:var(--soft)}
.pill .dot{width:7px;height:7px;border-radius:50%;background:currentColor;box-shadow:0 0 8px currentColor}
.gauge{display:flex;gap:14px;align-items:center}
.ring{width:84px;height:84px;flex:none;position:relative}.ring span{position:absolute;inset:0;display:grid;place-items:center;font:700 19px var(--mono)}
.spark{display:block;width:100%;height:46px;margin-top:6px}
.seg{display:flex;background:var(--card);border-radius:12px;padding:4px;margin:0 0 12px}
.seg button{flex:1;min-height:38px;border:0;border-radius:9px;background:transparent;color:var(--muted);font-weight:600;font-size:13px}
.seg button.on{background:#26262b;color:var(--white)}
.btn{border:0;border-radius:10px;background:var(--card2);color:var(--white);font-weight:600;font-size:13px;padding:8px 12px;flex:none}
.btn.red{background:#3a0d0a;color:#ffb4a8}.btn.wide{width:100%;padding:13px;font-size:15px}.btn:disabled{opacity:.5}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}.chip{font:600 11px var(--mono);padding:4px 8px;border-radius:7px;background:var(--card2);color:var(--text)}
.chip.warning{color:var(--amber)}.chip.critical{color:var(--red2)}
.meta{display:flex;justify-content:space-between;gap:8px;font:12px var(--mono);color:var(--soft);margin-top:7px}
.cores{display:grid;grid-template-columns:repeat(auto-fill,minmax(14px,1fr));gap:4px;align-items:end;height:44px;margin-top:10px}
.cores i{display:block;background:var(--red2);border-radius:2px;min-height:2px;opacity:.85}
.kv{display:grid;grid-template-columns:1fr auto;font-size:13px;margin-top:8px}
.kv div{padding:8px 0;border-top:1px solid var(--line)}.kv div:nth-child(-n+2){border-top:0}
.kv div:nth-child(odd){color:var(--soft)}.kv div:nth-child(even){font:600 13px var(--mono);text-align:right}
.pod{background:var(--card);border-radius:14px;padding:12px;display:flex;flex-direction:column;gap:4px;align-items:flex-start;border:0;text-align:left}
.pod .ic{width:34px;height:34px;border-radius:9px;background:var(--card2);display:grid;place-items:center;margin-bottom:4px}
.pod b{font-size:14px}.pod span{font-size:12px;color:var(--soft)}
.hold{position:relative;overflow:hidden;width:100%;border-radius:12px;padding:15px;text-align:center;font-weight:700;font-size:14px;background:#2a0b09;color:#ffd9d2;border:1px solid #5a1410;user-select:none;-webkit-user-select:none;touch-action:none;margin-top:4px}
.hold .fill{position:absolute;inset:0 auto 0 0;width:0;background:#7a120c}.hold span{position:relative}
.hold.amber{background:#221a07;border-color:#4a3609;color:#fde68a}.hold.amber .fill{background:#6b4e0a}
.hint{font-size:12px;color:var(--muted);text-align:center;font-style:italic;margin:8px 0 0}
.banner{background:#300908;border:1px solid #5a1410;border-radius:14px;padding:12px 14px;margin:0 0 12px;display:flex;gap:12px;align-items:center}
.banner b{flex:1;font-size:14px}
.ups{display:flex;gap:16px;align-items:center}
.batt{width:64px;height:110px;border:3px solid #3b3b42;border-radius:10px;position:relative;flex:none;padding:4px}
.batt::before{content:"";position:absolute;top:-9px;left:20px;width:18px;height:6px;border-radius:3px 3px 0 0;background:#3b3b42}
.batt i{position:absolute;left:4px;right:4px;bottom:4px;border-radius:5px;background:var(--green)}
.batt i.warning{background:var(--amber)}.batt i.critical{background:var(--red)}
.batt span{position:absolute;inset:0;display:grid;place-items:center;font:700 16px var(--mono);text-shadow:0 1px 4px #000}
.upsbig{font:700 26px var(--mono)}.upsbig small{font-size:13px;color:var(--soft);font-weight:600}
/* tab bar */
nav.tabs{position:fixed;left:0;right:0;bottom:0;background:rgba(10,10,12,.96);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border-top:1px solid #1d1d22;display:flex;padding:8px 6px calc(10px + env(safe-area-inset-bottom));z-index:5}
nav.tabs button{flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;font-size:10px;font-weight:600;color:var(--muted);background:none;border:0;padding:2px 0}
nav.tabs button.on{color:var(--red2)}nav.tabs svg{width:23px;height:23px}
/* sheet, toast, pairing */
.sheet{position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;align-items:flex-end;justify-content:center;z-index:10}
.sheet .panel{background:var(--bg);width:100%;max-width:640px;max-height:92vh;overflow-y:auto;border-radius:20px 20px 0 0;padding:18px 16px calc(24px + env(safe-area-inset-bottom));border-top:1px solid #222}
label.f{display:block;font-size:13px;color:var(--soft);margin:12px 0 6px}
select.field{appearance:none;-webkit-appearance:none}
input.field,select.field{width:100%;background:var(--card);border:1px solid #26262b;border-radius:10px;color:var(--white);font:15px var(--mono);padding:11px 12px}
.sw{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 0;border-top:1px solid var(--line)}
.sw span{font-size:14px}.sw small{display:block;color:var(--muted);font-size:12px}
.sw input{width:22px;height:22px;accent-color:#FF3B1F;flex:none}
.nums{display:grid;grid-template-columns:1fr 1fr;gap:0 10px}
#toast{position:fixed;left:16px;right:16px;bottom:calc(96px + env(safe-area-inset-bottom));max-width:600px;margin:0 auto;background:#1c1c21;border:1px solid #2c2c31;border-radius:12px;padding:12px 14px;font-size:14px;z-index:20;opacity:0;transform:translateY(10px);transition:all .25s;pointer-events:none}
#toast.on{opacity:1;transform:none}#toast.bad{border-color:#5a1410}
#pair{max-width:420px;margin:0 auto;padding:calc(env(safe-area-inset-top) + 50px) 22px 40px;text-align:center}
#pair p{color:var(--text)}#pair .err{color:var(--red2);min-height:20px;font-size:14px}
.empty{color:var(--muted);font-size:13px;padding:14px;text-align:center}
</style></head><body>

<div id="pair" class="hidden">
  <div class="eyewrap"><div class="eye" id="peye"></div></div>
  <p class="quote" id="pquote" style="margin-top:26px">“I'm sorry, Dave. I can't let you in without the access code.”</p>
  <p style="font-size:14px;color:var(--soft)">On the PC, open <b>http://localhost:8766/pair</b> and type the <b>6-digit PIN</b> it shows. Or paste the full access code (the same as the NZB phone app's).</p>
  <input id="code" class="field" placeholder="6-digit PIN or access code" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" style="margin:14px 0 10px">
  <button id="connect" class="btn red wide">Open the pod bay doors</button>
  <p class="err" id="perr"></p>
</div>

<main id="app" class="hidden">
  <section id="t-status"></section>
  <section id="t-vitals" class="hidden"></section>
  <section id="t-services" class="hidden"></section>
  <section id="t-drives" class="hidden"></section>
  <section id="t-shield" class="hidden"></section>
  <section id="t-log" class="hidden"></section>
</main>

<nav class="tabs hidden" id="tabs">
  <button data-tab="status" class="on"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3.5" fill="currentColor"/></svg>Status</button>
  <button data-tab="vitals"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12h4l3-8 4 16 3-8h4"/></svg>Vitals</button>
  <button data-tab="services"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="6" rx="2"/><rect x="3" y="14" width="18" height="6" rx="2"/><path d="M7 7h.01M7 17h.01"/></svg>Services</button>
  <button data-tab="drives"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></svg>Drives</button>
  <button data-tab="shield"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/></svg>Shield</button>
</nav>
<div id="sheet" class="sheet hidden"><div class="panel" id="panel"></div></div>
<div id="toast"></div>

<script>
'use strict';
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const LV={ok:0,info:1,warning:2,critical:3};
let token='',S=null,tab='status',range='live',hist=null,histAt=0,busy=false,lastOk=0;
try{token=localStorage.getItem('hal_token')||''}catch(e){}
const fb=(n,d=1)=>{if(n==null||isNaN(n))return'—';const u=['B','KB','MB','GB','TB','PB'];let i=0;n=Number(n);while(Math.abs(n)>=1024&&i<u.length-1){n/=1024;i++}return(i?n.toFixed(n>=100?0:d):n.toFixed(0))+' '+u[i]};
const fr=b=>b==null?'—':(b*8/1e6>=10?(b*8/1e6).toFixed(0):(b*8/1e6).toFixed(1))+' Mb/s';
const f0=(n,s='')=>n==null||isNaN(n)?'—':Math.round(n)+s;
function dur(s){if(s==null)return'—';s=Math.max(0,Math.round(s));if(s<90)return s+' s';if(s<5400)return Math.round(s/60)+' min';if(s<36000)return Math.floor(s/3600)+' h'+(s%3600>=60?' '+Math.floor(s%3600/60)+' min':'');if(s<172800)return Math.round(s/3600)+' h';return Math.floor(s/86400)+' d '+Math.floor(s%86400/3600)+' h'}
function ago(t){if(!t)return'';const d=Date.now()/1000-t;if(d<60)return'just now';if(d<3600)return Math.round(d/60)+' min ago';const dt=new Date(t*1000),now=new Date();const tm=dt.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});if(dt.toDateString()===now.toDateString())return'Today '+tm;const y=new Date(now-864e5);if(dt.toDateString()===y.toDateString())return'Yesterday '+tm;return dt.toLocaleDateString([],{month:'short',day:'numeric'})+' '+tm}
function toast(msg,bad){const t=$('toast');t.textContent=msg;t.className='on'+(bad?' bad':'');clearTimeout(toast.h);toast.h=setTimeout(()=>t.className='',3600);
  if(talkative()&&unlocked&&!talkRun){say(msg);return}
  const sec=document.querySelector('main section:not(.hidden)');if(sec)murmur(sec.querySelector('.eye'),Math.min(2600,400+msg.length*45))}
async function api(path,body){
  const o={headers:{'X-Mobile-Token':token}};
  if(body!==undefined){o.method='POST';o.headers['Content-Type']='application/json';o.body=JSON.stringify(body)}
  const r=await fetch(path,o);let d={};try{d=await r.json()}catch(e){}
  if(r.status===401||r.status===429){if(r.status===401)showPair(d.error);throw new Error(d.error||'Not allowed')}
  if(!r.ok)throw new Error(d.error||('Error '+r.status));return d}
function showPair(msg){$('app').classList.add('hidden');$('tabs').classList.add('hidden');$('pair').classList.remove('hidden');$('perr').textContent=msg&&token?msg:''}
async function connect(){let v=cleanCode($('code').value);$('code').value=v;if(!v)return;$('perr').textContent='';
  if(/^\d{6}$/.test(v)){try{const r=await fetch('/api/pair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pin:v})});const d=await r.json().catch(()=>({}));
      if(!r.ok||!d.token){talk($('peye'),$('pquote'),d.error||'That PIN isn\'t right.',false);return}v=d.token}catch(e){talk($('peye'),$('pquote'),'I can\'t reach the PC.',false);return}}
  token=v;
  try{await api('/api/status');try{localStorage.setItem('hal_token',v)}catch(e){}$('pair').classList.add('hidden');start()}
  catch(e){$('perr').textContent='';talk($('peye'),$('pquote'),e.message,false)}}
// Access codes are letters, digits, - and _ only: drop spaces, quotes and a pasted "\n" or line break.
function cleanCode(v){return String(v||'').replace(/\\[nrt]/g,'').replace(/[^A-Za-z0-9_\-]/g,'')}
$('connect').onclick=connect;$('code').onkeydown=e=>{if(e.key==='Enter')connect()};

/* ---------- drawing helpers ---------- */
function ring(pct,color){const c=2*Math.PI*35,v=Math.max(0,Math.min(100,pct||0));return'<div class="ring"><svg viewBox="0 0 84 84"><circle cx="42" cy="42" r="35" stroke="#222227" stroke-width="8" fill="none"/><circle cx="42" cy="42" r="35" stroke="'+color+'" stroke-width="8" fill="none" stroke-linecap="round" stroke-dasharray="'+(c*v/100).toFixed(1)+' 999" transform="rotate(-90 42 42)"/></svg><span>'+f0(pct,'%')+'</span></div>'}
function spark(vals,color,max){vals=(vals||[]).map(v=>v==null?null:Number(v));const pts=vals.filter(v=>v!=null);if(pts.length<2)return'<svg class="spark"></svg>';
  const hi=max||Math.max(...pts)*1.15||1,n=vals.length;let d='',pen=false;vals.forEach((v,i)=>{if(v==null){pen=false;return}const x=(i/(n-1)*200).toFixed(1),y=(44-Math.min(1,v/hi)*40).toFixed(1);d+=(pen?'L':'M')+x+' '+y+' ';pen=true});
  return'<svg class="spark" viewBox="0 0 200 46" preserveAspectRatio="none"><path d="'+d+'" fill="none" stroke="'+color+'" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>'}
const lvlOf=(v,warn,crit)=>v==null?'ok':v>=crit?'critical':v>=warn?'warning':'ok';
function pill(level,text){return'<span class="pill '+level+'"><span class="dot"></span>'+esc(text)+'</span>'}
function hdr(title){return'<div class="top"><span class="brand"><b>HAL</b> 9000</span><div class="eye sm '+(S?S.hal.level:'')+'"></div></div>'+(title?'<h1>'+esc(title)+'</h1>':'')}
const ICON={spk:'<path d="M11 5L6 9H3v6h3l5 4zM15.5 8.5a5 5 0 010 7M18.5 5.5a9 9 0 010 13"/>',mute:'<path d="M11 5L6 9H3v6h3l5 4zM22 9l-6 6M16 9l6 6"/>',lock:'<path d="M6 11h12v9H6zM8 11V8a4 4 0 018 0v3"/>',moon:'<path d="M20 14A8 8 0 1110 4a7 7 0 0010 10z"/>',net:'<path d="M2 9a15 15 0 0120 0M5 12.5a10 10 0 0114 0M8.5 16a5 5 0 017 0M12 19.5h.01"/>',shield:'<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>',upd:'<path d="M4 12a8 8 0 0114-5l2 2M20 12a8 8 0 01-14 5l-2-2M20 4v5h-5M4 20v-5h5"/>',bell:'<path d="M6 8a6 6 0 1112 0c0 7 3 9 3 9H3s3-2 3-9M10 21h4"/>',gear:'<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z"/>'};
const svg=(k,c)=>'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="'+(c||'currentColor')+'" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'+ICON[k]+'</svg>';

/* ---------- Status ---------- */
function rStatus(){
  const h=S.hal,v=S.vitals,n=S.net,g=(v.gpus||[])[0];
  const alerts=S.issues.filter(i=>LV[i.level]>=2).slice(0,4),notes=S.issues.filter(i=>i.level==='info');
  const sysBad=S.systems.filter(x=>LV[x.level]>=2).length;
  const spk=CAN_SPEAK?'<button class="iconbtn" data-talk aria-label="'+(talkative()?'Voice on':'Voice off')+'">'+svg(talkative()?'spk':'mute',talkative()?'#FF6A4D':'#71717A')+'</button>':'';
  let head='<div class="top"><span class="brand"><b>HAL</b> 9000</span><span style="display:flex;gap:8px">'+spk+'<button class="iconbtn" data-go="log" aria-label="Log and controls">'+svg('bell','#D4D4D8')+(S.unread?'<i></i>':'')+'</button></span></div>';
  if(S.power.shutdown_at&&S.power.shutdown_at>S.now)head+='<div class="banner"><b>Shutting down in '+Math.max(0,Math.round(S.power.shutdown_at-S.now))+' s</b><button class="btn red" data-act="cancel_shutdown">Cancel</button></div>';
  let x='';
  x+=alerts.map(i=>'<button class="alert '+i.level+'" data-go="'+esc(i.tab)+'"><span class="s '+i.level+'"></span><span><b>'+esc(i.title)+'</b>'+(i.detail?'<p>'+esc(i.detail)+'</p>':'')+'</span></button>').join('');
  if(notes.length)x+='<button class="alert info" data-go="'+esc(notes[0].tab)+'"><span class="s info"></span><span><b>'+(notes.length>1?notes.length+' small things':'One small thing')+'</b><p>'+esc(notes.map(i=>i.title).join(' · '))+'</p></span></button>';
  const ct=lvlOf(v.cpu.temp,v.limits.cpu_temp-10,v.limits.cpu_temp);
  x+='<div class="grid2">'+
   '<button class="tile" data-go="vitals"><div class="k">CPU</div><div class="v">'+f0(v.cpu.pct)+'<small>%'+(v.cpu.temp!=null?' · '+f0(v.cpu.temp)+'°C':'')+'</small></div><div class="bar"><i class="'+lvlOf(v.cpu.pct,75,90)+'" style="width:'+(v.cpu.pct||0)+'%"></i></div></button>'+
   '<button class="tile" data-go="vitals"><div class="k">MEMORY</div><div class="v">'+(v.mem.used!=null?(v.mem.used/1073741824).toFixed(1):'—')+'<small> / '+fb(v.mem.total,0)+'</small></div><div class="bar"><i class="'+lvlOf(v.mem.pct,80,92)+'" style="width:'+(v.mem.pct||0)+'%"></i></div></button>'+
   '<button class="tile" data-go="vitals"><div class="k">GPU</div><div class="v">'+(g?f0(g.util)+'<small>%'+(g.temp!=null?' · '+f0(g.temp)+'°C':'')+'</small>':'—')+'</div><div class="bar"><i class="'+(g?lvlOf(g.util,85,97):'')+'" style="width:'+(g?g.util||0:0)+'%"></i></div></button>'+
   '<button class="tile" data-go="shield"><div class="k">INTERNET</div><div class="v">'+f0(n.ping_ms)+'<small> ms · '+f0(n.loss,'%')+' loss</small></div><div class="bar"><i class="sky" style="width:'+Math.min(100,(n.down||0)/1.25e6)+'%"></i></div></button></div>';
  x+='<div class="sec"><span>CRITICAL SYSTEMS</span><span class="r" style="color:'+(sysBad?'var(--amber)':'var(--green)')+'">'+(S.systems.length-sysBad)+' / '+S.systems.length+' NOMINAL</span></div><div class="list">'+
   S.systems.map(s=>'<button class="row" data-go="'+s.tab+'"><span class="s '+s.level+'"></span><span class="t"><b>'+esc(s.name)+'</b><span>'+esc(s.detail)+'</span></span><span class="m'+(s.level!=='ok'?'" style="color:var(--'+(s.level==='critical'?'red2':'amber')+')':'"')+'>'+esc(s.count||(s.level==='ok'?'OK':s.level.toUpperCase()))+'</span></button>').join('')+'</div>';
  return {head,body:x}}

/* ---------- HAL talks: the eye flickers with the words (and speaks aloud when tapped) ---------- */
let lastQuote='',talkRun=null,voiceOn=false;
const REDUCE=matchMedia('(prefers-reduced-motion: reduce)').matches;
const CAN_SPEAK='speechSynthesis' in window&&'SpeechSynthesisUtterance' in window;
// HAL's voice: the calmest male English voice on this phone (Enhanced/Premium ones first), slow and soft.
const LS={get(k,d){try{const v=localStorage.getItem(k);return v==null?d:v}catch(e){return d}},set(k,v){try{localStorage.setItem(k,v)}catch(e){}}};
const MALE=/daniel|arthur|aaron|oliver|malcolm|gordon|reed|evan|nathan|tom|alex|fred|rishi|lee|george|ryan|guy|david|mark|james|google uk english male|\bmale\b/i;
function voices(){return CAN_SPEAK?speechSynthesis.getVoices().filter(v=>/^en/i.test(v.lang)):[]}
function voiceScore(v){let s=0;if(MALE.test(v.name))s+=10;if(/premium|enhanced|neural|natural|online/i.test(v.name))s+=6;if(/en-GB/i.test(v.lang))s+=3;if(/en-US/i.test(v.lang))s+=1;
  if(/novelty|bad news|bells|boing|bubbles|cellos|whisper|zarvox|trinoids|jester|organ|superstar|wobble|albert|bahh/i.test(v.name))s-=50;if(v.localService)s+=1;return s}
function pickVoice(){const vs=voices();if(!vs.length)return null;const want=LS.get('hal_voice_name','');
  return vs.find(v=>v.name===want)||vs.slice().sort((a,b)=>voiceScore(b)-voiceScore(a))[0]}
const VOICE={rate:()=>Number(LS.get('hal_rate','0.82')),pitch:()=>Number(LS.get('hal_pitch','0.7'))};
if(CAN_SPEAK)speechSynthesis.onvoiceschanged=()=>{};
// Text the voice reads well: "Z:" -> "Z", "°C" -> "degrees", "GB" -> "gigabytes"...
function speakable(t){return String(t).replace(/\b([A-Z]):/g,'$1').replace(/°C/g,' degrees').replace(/(\d)\s*GB\b/g,'$1 gigabytes').replace(/(\d)\s*TB\b/g,'$1 terabytes')
  .replace(/(\d)\s*MB\/s\b/g,'$1 megabytes a second').replace(/(\d)\s*ms\b/g,'$1 milliseconds').replace(/\bmin\b/g,'minutes').replace(/—/g,', ').replace(/·/g,', ').replace(/\bNZB\b/g,'N Z B')}
let unlocked=false;
const talkative=()=>CAN_SPEAK&&LS.get('hal_talk','on')==='on';
function utter(text,onBoundary,onEnd){const u=new SpeechSynthesisUtterance(speakable(text));u.rate=VOICE.rate();u.pitch=VOICE.pitch();u.volume=1;const v=pickVoice();if(v){u.voice=v;u.lang=v.lang}
  u.onboundary=onBoundary||null;u.onend=u.onerror=()=>{voiceOn=false;if(onEnd)onEnd()};
  if(speechSynthesis.speaking||speechSynthesis.pending)speechSynthesis.cancel();   // (cancel right before speak swallows it on iPhone)
  voiceOn=true;speechSynthesis.speak(u);return u}
// Says a short line (button results) with the small eye moving while he speaks.
function say(text){if(!talkative()||!unlocked||talkRun)return;const sec=document.querySelector('main section:not(.hidden)');const eye=sec&&sec.querySelector('.eye');
  utter(text);if(eye&&!REDUCE){let cur=1,next=0,target=1;eye.classList.add('talking');(function f(now){if(!eye.isConnected)return;
    if(voiceOn&&now>=next){target=Math.random()<.3?.75:1.3+Math.random()*.5;next=now+60+Math.random()*90}if(!voiceOn)target=1;
    cur+=(target-cur)*.3;eye.style.setProperty('--b',cur.toFixed(3));if(!voiceOn&&Math.abs(cur-1)<.015){eye.classList.remove('talking');eye.style.removeProperty('--b');return}requestAnimationFrame(f)})(performance.now())}}
// Phones only allow sound after a touch: the first one wakes HAL's voice and he says hello.
document.addEventListener('pointerdown',e=>{if(unlocked||!CAN_SPEAK)return;unlocked=true;updateTap();
  if(!talkative()||!S||e.target.closest('#heye,[data-talk]'))return;
  setTimeout(()=>{if(!voiceOn&&$('heye'))talk($('heye'),$('hquote'),lastQuote,true,S.hal.greeting)},60)},true);
function updateTap(){const t=$('htap');if(!t)return;t.textContent=!talkative()?'TAP HAL TO HEAR HIM':unlocked?'':'TAP ANYWHERE AND I\'LL TALK';t.classList.toggle('hidden',!t.textContent)}
function setTalk(on){LS.set('hal_talk',on?'on':'off');if(!on)stopTalk();render();if(on){unlocked=true;talk($('heye'),$('hquote'),'Thank you, '+S.hal.name+'. I\'ll talk to you from now on.',true)}}
function stopTalk(){if(talkRun){talkRun.stop();talkRun=null}if(CAN_SPEAK&&voiceOn){speechSynthesis.cancel();voiceOn=false}}
// Types text into textEl while the eye's light rises on vowels and dips at spaces and punctuation.
function talk(eye,textEl,text,speak,prefix){
  if(talkRun)talkRun.stop();
  const wrap=s=>'“'+esc(s)+'”';
  if(REDUCE&&!speak){textEl.innerHTML=wrap(text);return}
  let raf=0,stopped=false,cur=1,target=1,next=0,bIdx=-1,spoken=false;const t0=performance.now();
  const cps=speak?13*VOICE.rate()/.82:30;let startShow=performance.now()+(speak&&prefix?prefix.length/cps*1000:0);
  if(speak&&CAN_SPEAK){const pre=prefix?prefix+' ':'';const off=speakable(pre).length;
    // boundary positions are in the spoken text; map them back roughly onto what's shown
    utter(pre+text,e=>{if(e.charIndex!=null&&e.charIndex>=off)bIdx=Math.round((e.charIndex-off)*text.length/Math.max(1,speakable(text).length))});spoken=true}
  eye.classList.add('talking');
  const run={stop(){stopped=true;cancelAnimationFrame(raf);eye.classList.remove('talking');eye.style.removeProperty('--b');textEl.innerHTML=wrap(text)}};
  talkRun=run;
  function frame(now){
    if(stopped)return;
    let idx=Math.max(0,Math.floor((now-startShow)/1000*cps));
    if(bIdx>=0){const end=text.slice(bIdx).search(/\s/);idx=Math.max(Math.min(idx,text.length),end<0?text.length:bIdx+end)}
    const shown=Math.min(text.length,idx);
    const busy=spoken?voiceOn:shown<text.length;
    textEl.innerHTML='“'+esc(text.slice(0,shown))+(busy&&shown<text.length?'<span class="caret"></span>':'”');
    if(busy){if(now>=next){const ch=text[Math.max(0,shown-1)]||' ';
        target=/[aeiouyAEIOUY]/.test(ch)?1.5+Math.random()*.45:/[\s,.;:!?—\-]/.test(ch)?.72+Math.random()*.1:1.12+Math.random()*.25;
        next=now+55+Math.random()*85}}
    else target=1;
    cur+=(target-cur)*.32;eye.style.setProperty('--b',cur.toFixed(3));
    if(!busy&&Math.abs(cur-1)<.015){run.stop();if(talkRun===run)talkRun=null;return}
    raf=requestAnimationFrame(frame)}
  raf=requestAnimationFrame(frame)}
// A short flicker of a small eye (when HAL answers with a message).
function murmur(eye,ms){if(!eye||REDUCE)return;let cur=1,t0=performance.now(),next=0,target=1;eye.classList.add('talking');
  (function f(now){if(!eye.isConnected)return;const on=now-t0<ms;if(on&&now>=next){target=Math.random()<.35?.75:1.3+Math.random()*.5;next=now+60+Math.random()*90}if(!on)target=1;
    cur+=(target-cur)*.3;eye.style.setProperty('--b',cur.toFixed(3));if(!on&&Math.abs(cur-1)<.015){eye.classList.remove('talking');eye.style.removeProperty('--b');return}requestAnimationFrame(f)})(t0)}
function renderStatus(){
  const el=$('t-status');
  if(!$('hero')){el.innerHTML='<div id="hhead"></div><div class="eyewrap" id="hero"><button class="eye" id="heye" aria-label="Hear HAL"></button><div class="greet" id="hgreet"></div><div class="quote" id="hquote"></div><div class="qsub" id="hsub"></div>'+(CAN_SPEAK?'<div class="tap" id="htap"></div>':'')+'</div><div id="hbody"></div>';
    $('heye').onclick=()=>{if(voiceOn){stopTalk();return}unlocked=true;talk($('heye'),$('hquote'),lastQuote,true);updateTap()}}
  const r=rStatus(),h=S.hal,eye=$('heye');
  $('hhead').innerHTML=r.head;$('hbody').innerHTML=r.body;
  eye.className='eye '+h.level+(eye.classList.contains('talking')?' talking':'');
  $('hgreet').textContent=h.greeting;
  $('hsub').textContent='UP '+dur(S.uptime).toUpperCase()+' · '+S.host.toUpperCase()+(lastOk?' · LIVE':'');
  updateTap();
  if(h.quote!==lastQuote){const first=!lastQuote;lastQuote=h.quote;if(!voiceOn)talk(eye,$('hquote'),lastQuote,!first&&talkative()&&unlocked)}}

/* ---------- Vitals ---------- */
function rVitals(){
  const v=S.vitals,hs=hist||{};const cpu=v.cpu,mem=v.mem;
  let x=hdr('Vital signs')+'<div class="seg">'+['live','1h','24h','7d'].map(r=>'<button data-range="'+r+'" class="'+(range===r?'on':'')+'">'+(r==='live'?'Live':r.replace('h',' h').replace('d',' d'))+'</button>').join('')+'</div>';
  const tl=lvlOf(cpu.temp,v.limits.cpu_temp-10,v.limits.cpu_temp);
  x+='<div class="card"><div class="gauge">'+ring(cpu.pct,'#FF2A1F')+'<div style="flex:1;min-width:0"><h3>CPU '+(cpu.temp!=null?pill(tl,f0(cpu.temp)+'°C'):'')+'</h3><div class="sub">'+esc((cpu.name||'').replace(/\s+\d+-Core Processor/,'').replace(/\(R\)|\(TM\)/g,''))+(cpu.cores?' · '+cpu.cores+' cores':'')+(cpu.freq?' · '+(cpu.freq/1000).toFixed(2)+' GHz':'')+'</div>'+spark(hs.cpu,'#FF6A4D',100)+'</div></div>'+
     (cpu.per_core&&cpu.per_core.length?'<div class="cores">'+cpu.per_core.map(c=>'<i style="height:'+Math.max(4,c)+'%"></i>').join('')+'</div>':'')+'</div>';
  x+='<div class="card"><div class="gauge">'+ring(mem.pct,'#60A5FA')+'<div style="flex:1;min-width:0"><h3>Memory <span class="sub" style="margin:0">'+fb(mem.used)+' / '+fb(mem.total,0)+'</span></h3><div class="sub">Top: '+esc((v.top_mem||[]).slice(0,2).map(p=>p.name.replace(/\.exe$/i,'')+' '+fb(p.mem)).join(' · '))+'</div>'+spark(hs.mem,'#60A5FA',100)+'</div></div></div>';
  (v.gpus||[]).forEach((g,i)=>{const gl=lvlOf(g.temp,v.limits.gpu_temp-10,v.limits.gpu_temp);
    x+='<div class="card"><div class="gauge">'+ring(g.util,'#34D399')+'<div style="flex:1;min-width:0"><h3>'+esc(g.name.replace(/NVIDIA GeForce |AMD Radeon /,''))+' '+(g.temp!=null?pill(gl,f0(g.temp)+'°C'):'')+'</h3><div class="sub">VRAM '+fb(g.vram_used)+' / '+fb(g.vram_total,0)+(g.power?' · '+f0(g.power)+' W':'')+'</div>'+(g.video?'<div class="sub">Video engine '+f0(g.video)+'% (playback / transcoding)</div>':'')+(i===0?spark(hs.gpu,'#34D399',100):'')+'</div></div></div>'});
  if(range!=='live'||(hs.cpu_temp||[]).some(t=>t)) x+='<div class="card"><h3>CPU temperature <span class="sub" style="margin:0">'+(range==='live'?'last 2 min':'last '+range.replace('h',' h').replace('d',' days'))+'</span></h3>'+spark(hs.cpu_temp,'#FBBF24')+'</div>';
  x+='<div class="sec"><span>TEMPERATURES &amp; FANS</span><span class="r">'+esc(v.sensor_source||'')+'</span></div><div class="list">';
  if(!v.temps.length&&!v.fans.length)x+='<div class="empty">I can\'t feel temperatures yet. Run <b>LibreHardwareMonitor</b> on the PC (Options › Remote Web Server › Run) and I\'ll read them.</div>';
  x+=v.temps.map(t=>'<div class="row"><span class="t"><b>'+esc(t.name)+'</b></span><span class="m">'+f0(t.value)+'°C</span></div>').join('');
  x+=v.fans.map(t=>'<div class="row"><span class="t"><b>'+esc(t.name)+'</b></span><span class="m">'+f0(t.value)+' rpm</span></div>').join('')+'</div>';
  x+='<div class="sec"><span>BUSIEST APPS</span></div><div class="list">'+(v.top_cpu||[]).map(p=>'<div class="row"><span class="t"><b>'+esc(p.name)+'</b><span>'+fb(p.mem)+'</span></span><span class="m">'+(p.cpu||0).toFixed(1)+'%</span></div>').join('')+'</div>';
  return x}

/* ---------- Services ---------- */
function rServices(){
  const sv=S.services,bad=sv.filter(s=>s.state==='down').length;
  let x=hdr('Services')+'<div class="sec" style="margin-top:0"><span>MEDIA PIPELINE</span><span class="r" style="color:'+(bad?'var(--red2)':'var(--green)')+'">'+(bad?bad+' DOWN':'ALL RESPONDING')+'</span></div><div class="list">';
  x+=sv.map(s=>{let sub;
    if(s.state==='up')sub=':'+s.port+' · '+f0(s.ms)+' ms'+(s.detail?' · '+s.detail:'');
    else if(s.state==='down')sub='Not answering for '+dur(S.now-s.since);
    else if(s.state==='restarting')sub='Restarting…';
    else if(s.state==='checking')sub='Checking…';
    else if(s.state==='ignored')sub='Not watched (Settings)';
    else sub='Not running here';
    if(s.job&&s.job.state!=='running')sub=s.job.msg;
    const can=s.local&&s.state!=='ignored'&&s.state!=='restarting';
    return'<div class="row"><span class="s '+s.state+'"></span><span class="t"><b>'+esc(s.name)+'</b><span>'+esc(sub)+'</span></span>'+(can?'<button class="btn'+(s.state==='down'?' red':'')+'" data-restart="'+s.id+'" data-name="'+esc(s.name)+'">'+(s.state==='up'?'Restart':'Start')+'</button>':'')+'</div>'}).join('')+'</div>';
  const n=S.net;
  x+='<div class="sec"><span>ON THE NETWORK</span></div><div class="list">';
  if(n.gateway)x+='<div class="row"><span class="s '+(n.gw_fails>=2?'critical':'')+'"></span><span class="t"><b>Router</b><span>'+esc(n.gateway)+'</span></span><span class="m">'+(n.gateway_ms!=null?f0(n.gateway_ms)+' ms':'—')+'</span></div>';
  x+=(S.devices||[]).map(d=>'<div class="row"><span class="s '+(d.up?'':(d.critical?'warning':'off'))+'"></span><span class="t"><b>'+esc(d.name)+'</b><span>'+esc(d.detail)+'</span></span><span class="m">'+(d.ms!=null?f0(d.ms)+' ms':'')+'</span></div>').join('');
  x+='<div class="row"><span class="s '+(n.tailscale?'':'off')+'"></span><span class="t"><b>Tailscale</b><span>'+(n.tailscale?esc(n.tailscale)+' · reach HAL from anywhere':'Not connected')+'</span></span></div></div>';
  x+='<p class="hint">“'+(bad?'I\'ve just picked up a fault. Tap Start and I\'ll try to bring it back.':'Every service is answering. I enjoy working with SABnzbd.')+'”</p>';
  return x}

/* ---------- Drives ---------- */
let cleanup=null;
function rDrives(){
  let x=hdr('Drives');
  const dr=[...S.drives].sort((a,b)=>b.pct-a.pct);
  x+=dr.map(d=>{const k=d.disk||{};const free=100-d.pct;
    let lvl=free<=Math.max(2,10/3)?'critical':free<=10||(d.days_left!=null&&d.days_left<=14)?'warning':'ok';
    if(k.health==='Warning'||k.health==='Unhealthy')lvl='critical';
    const hot=k.temp&&k.temp_limit&&k.temp>=k.temp_limit;
    const label=lvl==='ok'?(k.health||'OK').toUpperCase():(k.health==='Warning'||k.health==='Unhealthy'?k.health.toUpperCase():Math.round(d.pct)+'% FULL');
    const chips=[];if(k.media)chips.push([k.media+(k.bus&&k.bus!=='SATA'?' '+k.bus:'')+(k.spindle&&k.spindle>1?' '+k.spindle:''),'']);
    if(k.temp)chips.push([f0(k.temp)+'°C',hot?'warning':'']);if(k.wear!=null)chips.push(['Wear '+f0(k.wear)+'%',k.wear>=80?'warning':'']);
    if(k.read_errors!=null||k.write_errors!=null){const e=(k.read_errors||0)+(k.write_errors||0);chips.push([e?e+' errors':'0 errors',e?'warning':''])}
    if(k.hours)chips.push([Math.round(k.hours).toLocaleString()+' h','']);
    return'<div class="card '+(lvl==='ok'?'':lvl)+'"><h3>'+esc(d.mount+(d.label?' '+d.label:''))+' '+pill(lvl,label)+'</h3><div class="bar" style="height:8px"><i class="'+lvl+'" style="width:'+d.pct+'%"></i></div>'+
      '<div class="meta"><span>'+fb(d.free)+' free / '+fb(d.total,1)+'</span><span>'+(d.days_left!=null?'~'+Math.round(d.days_left)+' days left':(d.per_day!=null?'steady':''))+'</span></div>'+
      (d.per_day!=null&&d.days_left!=null?'<div class="meta"><span>Filling '+fb(d.per_day)+' a day</span><span>'+(d.write?'↓ '+fb(d.write)+'/s':'')+'</span></div>':'')+
      (chips.length?'<div class="chips">'+chips.map(c=>'<span class="chip '+c[1]+'">'+esc(c[0])+'</span>').join('')+'</div>':'')+
      (k.name?'<div class="sub" style="margin-top:8px">'+esc(k.name)+'</div>':'')+'</div>'}).join('')||'<div class="empty">No drives found.</div>';
  const c=cleanup||{};
  x+='<div class="sec"><span>CLEANUP</span><span class="r">'+(c.busy?'MEASURING…':'')+'</span></div><div class="list">'+
    '<div class="row"><span class="t"><b>Recycle Bin</b><span>'+(c.recycle!=null?fb(c.recycle)+' · '+(c.recycle_items||0)+' items':'…')+'</span></span><button class="btn" data-act="empty_recycle" data-confirm="Empty the Recycle Bin on every drive?">Empty</button></div>'+
    '<div class="row"><span class="t"><b>Temp files</b><span>'+(c.temp!=null?fb(c.temp)+' older than a day':'…')+'</span></span><button class="btn" data-act="clean_temp" data-confirm="Delete temp files older than a day? Files in use are skipped.">Clean</button></div></div>';
  const act=S.drives.filter(d=>(d.read||0)+(d.write||0)>1e6);
  if(act.length)x+='<div class="sec"><span>ACTIVITY NOW</span></div><div class="list">'+act.map(d=>'<div class="row"><span class="t"><b>'+esc(d.mount)+' '+((d.write||0)>(d.read||0)?'writing':'reading')+'</b></span><span class="m">'+fb(Math.max(d.read||0,d.write||0))+'/s</span></div>').join('')+'</div>';
  return x}

/* ---------- Shield (security, network, power) ---------- */
function upsCard(){
  const p=S.power,u=p.ups;
  if(!u)return'<div class="card"><h3>UPS '+pill('off','NOT CONNECTED')+'</h3><p class="sub" style="font-family:inherit;line-height:1.5">'+(p.ups_error?esc(p.ups_error):'Plug the UPS into this PC by USB and I\'ll find it by myself. A UPS on Network UPS Tools (WinNUT, a NAS) can be added in Settings.')+'</p></div>';
  const ch=u.charge,lvl=u.on_battery?(ch!=null&&ch<=p.shutdown_pct+10?'critical':'warning'):(u.replace?'warning':'ok');
  const st=u.on_battery?'ON BATTERY':u.charging?'CHARGING':'ON MAINS';
  const h=u.hist||[];
  let x='<div class="card '+(lvl==='ok'?'':lvl)+'"><h3>'+esc(u.name)+' '+pill(lvl,st)+'</h3><div class="ups" style="margin-top:12px"><div class="batt"><i class="'+(ch!=null&&ch<30?'critical':ch!=null&&ch<60?'warning':'')+'" style="height:calc('+(ch||0)+'% - 8px)"></i><span>'+f0(ch,'%')+'</span></div><div style="flex:1;min-width:0">'+
    '<div class="upsbig">'+(u.runtime_s!=null?dur(u.runtime_s):'—')+' <small>'+(u.on_battery?'left':'of backup')+'</small></div>'+
    '<div class="kv">'+(u.load!=null?'<div>Load</div><div class="'+(u.load>=85?'c':'')+'">'+f0(u.load)+'%'+(u.watts?' · '+f0(u.watts)+' W':'')+'</div>':'')+
    (u.in_v!=null?'<div>Input</div><div>'+f0(u.in_v)+' V</div>':'')+(u.out_v!=null?'<div>Output</div><div>'+f0(u.out_v)+' V</div>':'')+'</div></div></div>';
  if(h.length>3)x+='<div class="sub" style="margin-top:10px">Battery and load, last hour</div>'+spark(h.map(r=>r[1]),'#34D399',100).replace('class="spark"','class="spark" style="height:34px"')+spark(h.map(r=>r[2]),'#60A5FA',100).replace('class="spark"','class="spark" style="height:34px;margin-top:-34px"');
  x+='<div class="chips">'+(u.on_battery?'<span class="chip critical">On battery for '+dur(S.now-u.since)+'</span>':'')+(u.replace?'<span class="chip warning">Replace battery</span>':'')+(u.test?'<span class="chip">Self-test: '+esc(u.test)+'</span>':'')+'<span class="chip">'+esc(u.source==='NUT'?'Network UPS Tools':'USB')+'</span></div>';
  x+='<div class="kv" style="margin-top:10px"><div>Safe shutdown</div><div class="'+(p.auto_shutdown?'g':'')+'">'+(p.auto_shutdown?'at '+p.shutdown_pct+'% or '+p.shutdown_min+' min':'Off (Settings)')+'</div>'+
     '<div>Power cuts (30 days)</div><div>'+p.outages_30d+(p.last_outage?' · last '+esc(ago(p.last_outage.t)):'')+'</div></div></div>';
  return x}
function rShield(){
  const sc=S.security,n=S.net,p=S.power,d=sc.defender;
  let x=hdr('Shield');
  if(!S.windows)x+='<div class="alert info"><span class="s info"></span><span><b>Not a Windows PC</b><p>Defender, firewall and update checks only work on Windows.</p></span></div>';
  x+='<div class="sec" style="margin-top:0"><span>POWER</span><span class="r">'+esc(p.plan||'')+'</span></div>'+upsCard();
  const row=(lvl,title,sub,btn)=>'<div class="row"><span class="s '+lvl+'"></span><span class="t"><b>'+esc(title)+'</b><span>'+esc(sub)+'</span></span>'+(btn||'')+'</div>';
  x+='<div class="sec"><span>SECURITY</span>'+(sc.admin===false?'<span class="r" style="color:var(--amber)">RUN AS ADMIN FOR MORE</span>':'')+'</div><div class="list">';
  x+=row(d?(d.av===false||d.rtp===false?'critical':(d.sig_age>259200?'warning':'')):'off','Microsoft Defender',d?((d.rtp?'Real-time on':'Real-time OFF')+(d.sig_age!=null?' · definitions '+dur(d.sig_age)+' old':'')+(d.quick_age!=null?' · scanned '+dur(d.quick_age)+' ago':'')):(sc.av&&sc.av.length?sc.av.join(', '):'Checking…'),S.windows?'<button class="btn" data-act="defender_scan">Scan</button>':'');
  const fw=sc.firewall||[];x+=row(fw.length?(fw.every(f=>f.on)?'':'warning'):'off','Firewall',fw.length?fw.map(f=>f.name+(f.on?'':' OFF')).join(' · ')+(fw.every(f=>f.on)?' on':''):'Checking…');
  x+=row(sc.pending_reboot?'warning':(sc.updates?'info':''),'Windows Update',(sc.updates==null?'Not checked yet':sc.updates?sc.updates+' update'+(sc.updates>1?'s':'')+' available':'Up to date')+(sc.pending_reboot?' · restart needed':''),S.windows?'<button class="btn" data-act="check_updates">Check</button>':'');
  x+=row(sc.failed>=10?'warning':(sc.failed==null?'off':''),'Sign-ins',sc.failed==null?(sc.admin===false?'Needs administrator to read':'Checking…'):sc.failed+' failed attempt'+(sc.failed===1?'':'s')+' in 24 h');
  x+=row(sc.unexpected?'warning':(sc.unexpected==null?'off':''),'Crashes & power losses',sc.unexpected==null?'Checking…':(sc.unexpected||0)+' unexpected shutdown'+(sc.unexpected===1?'':'s')+' · '+(sc.bsod||0)+' blue screen'+(sc.bsod===1?'':'s')+' in 30 days');
  if(sc.disk_errors!=null)x+=row(sc.disk_errors?'warning':'','Disk errors in the event log',sc.disk_errors+' in 7 days');
  x+='</div><div class="sec"><span>NETWORK</span></div><div class="list">';
  x+=row(n.loss>=50?'critical':n.loss>=10?'warning':'','Internet',(n.host||'')+' · '+f0(n.ping_ms)+' ms · '+f0(n.loss,'%')+' loss');
  x+=row(n.dns===false?'warning':'','DNS',n.dns===false?'Names aren\'t resolving':'Resolving names');
  x+=row('','Throughput','↓ '+fr(n.down)+' · ↑ '+fr(n.up));
  if(n.wifi&&n.wifi.ssid)x+=row('','Wi-Fi',n.wifi.ssid+(n.wifi.signal!=null?' · '+n.wifi.signal+'%':''));
  x+='</div><div class="list">'+row('','Up for '+dur(S.uptime),'Started '+ago(S.boot))+'</div>';
  return x}

/* ---------- Log & controls ---------- */
let events=null;
function rLog(){
  let x='<div class="top"><button class="back" data-go="status">‹ Status</button><button class="iconbtn" data-settings aria-label="Settings">'+svg('gear','#D4D4D8')+'</button></div><h1>Log &amp; controls</h1>';
  if(S.power.shutdown_at&&S.power.shutdown_at>S.now)x+='<div class="banner"><b>Shutting down in '+Math.max(0,Math.round(S.power.shutdown_at-S.now))+' s</b><button class="btn red" data-act="cancel_shutdown">Cancel</button></div>';
  x+='<div class="sec" style="margin-top:0"><span>WHAT I NOTICED</span>'+(events&&events.length?'<button class="btn" style="padding:4px 9px;font-size:11px" data-act="clear_log" data-confirm="Clear the log?">Clear</button>':'')+'</div><div class="list">';
  x+=events==null?'<div class="empty">I\'m thinking…</div>':events.length?events.slice(0,40).map(e=>'<div class="row"><span class="s '+e.level+'"></span><span class="t"><b>'+esc(e.title)+'</b><span>'+esc(ago(e.t)+(e.msg?' · '+e.msg:''))+'</span></span></div>').join(''):'<div class="empty">Nothing yet. I\'ve been watching quietly.</div>';
  x+='</div><div class="sec"><span>POD BAY CONTROLS</span></div><div class="grid2">'+
   [['lock','lock','Lock','Lock the PC screen'],['sleep','moon','Sleep','Wake it with the keyboard'],['flush_dns','net','Fix network','Flush DNS, renew address'],['test_push','bell','Test alert','Send one to your phone']]
   .map(a=>'<button class="pod" data-act="'+a[0]+'"'+(a[0]==='sleep'?' data-confirm="Put the PC to sleep? Downloads and streams stop until it wakes."':'')+'><span class="ic">'+svg(a[1],'#FF6A4D')+'</span><b>'+a[2]+'</b><span>'+a[3]+'</span></button>').join('')+'</div>';
  x+='<div class="hold" data-hold="restart"><div class="fill"></div><span>Hold to restart HAL 9000</span></div>'+
     '<div class="hold amber" data-hold="shutdown" style="margin-top:10px"><div class="fill"></div><span>Hold to shut down</span></div>'+
     '<p class="hint">“I\'m afraid I can\'t do that, '+esc(S.hal.name)+'” — unless you hold for 2 seconds. You get 60 seconds to cancel.</p>';
  x+='<p class="hint" style="margin-top:18px;font-style:normal">HAL 9000 v'+esc(S.version)+' · '+esc(S.host)+'</p>';
  return x}

/* ---------- Settings ---------- */
async function openSettings(){
  let d;try{d=await api('/api/settings')}catch(e){toast(e.message,true);return}
  const s=d.settings;
  const sw=(k,t,sub)=>'<label class="sw"><span>'+t+(sub?'<small>'+sub+'</small>':'')+'</span><input type="checkbox" data-k="'+k+'"'+(s[k]?' checked':'')+'></label>';
  const sw2=(k,t,sub)=>'<label class="sw"><span>'+t+(sub?'<small>'+sub+'</small>':'')+'</span><input type="checkbox" id="v'+k+'"'+(talkative()?' checked':'')+'></label>';
  const num=(k,t)=>'<div><label class="f">'+t+'</label><input class="field" inputmode="numeric" data-k="'+k+'" data-num value="'+esc(s[k])+'"></div>';
  $('panel').innerHTML='<h2 style="margin:0 0 4px">Settings</h2><p class="hint" style="text-align:left;margin:0 0 8px">“I know I\'ve made some very poor decisions recently, but I can give you my complete assurance that my work will be back to normal.”</p>'+
   '<label class="f">What should I call you?</label><input class="field" data-k="name" value="'+esc(s.name)+'">'+
   (CAN_SPEAK?'<div class="sec">HAL\'S VOICE</div>'+sw2('talk','Talk to me','Speaks when you open the app, when something changes, and when you press a button')+
     '<label class="f">Voice (this phone)</label><select class="field" id="vname"><option value="">Automatic (calmest male voice)</option>'+voices().slice().sort((a,b)=>voiceScore(b)-voiceScore(a)).map(v=>'<option'+(v.name===LS.get('hal_voice_name','')?' selected':'')+' value="'+esc(v.name)+'">'+esc(v.name+' ('+v.lang+')')+'</option>').join('')+'</select>'+
     '<div class="nums"><div><label class="f">Speed</label><input type="range" id="vrate" min="0.6" max="1.1" step="0.02" value="'+VOICE.rate()+'" style="width:100%;accent-color:#FF3B1F"></div><div><label class="f">Pitch</label><input type="range" id="vpitch" min="0.4" max="1.2" step="0.02" value="'+VOICE.pitch()+'" style="width:100%;accent-color:#FF3B1F"></div></div>'+
     '<button class="btn wide" id="vtest" style="margin-top:10px">Hear it</button><p class="hint" style="text-align:left">For the best voice on an iPhone: Settings › Accessibility › Spoken Content › Voices › English, download <b>Daniel (Enhanced)</b> or another Enhanced/Premium voice, then pick it here.</p>':'')+
   '<div class="sec">PHONE ALERTS</div>'+sw('push','Send alerts to my phone',d.push_ready?(d.push_from_phone_app?'Using the NZB phone app\'s ntfy topic':'Using the topic below'):'Set up an ntfy topic first')+
   '<label class="f">ntfy topic (blank: use the NZB phone app\'s)</label><input class="field" data-k="ntfy_url" placeholder="https://ntfy.sh/your-private-topic" value="'+esc(s.ntfy_url)+'" autocapitalize="off" autocorrect="off">'+
   sw('push_recoveries','Tell me when things are fine again')+
   '<div class="sec">SERVICES</div>'+sw('auto_restart','Restart a service that stops answering','At most 3 times a day each; I\'ll tell you when I do')+
   '<div class="nums">'+num('down_after_s','Down after (seconds)')+num('phone_port','NZB phone server port')+'</div>'+
   '<label class="f">Don\'t watch</label>'+d.services.map(v=>'<label class="sw"><span>'+esc(v.name)+'</span><input type="checkbox" data-ign="'+v.id+'"'+(s.ignored_services.includes(v.id)?' checked':'')+'></label>').join('')+
   '<div class="sec">ALERT LIMITS</div><div class="nums">'+num('cpu_temp_alert','CPU °C')+num('gpu_temp_alert','GPU °C')+num('cpu_alert','CPU load %')+num('ram_alert','Memory %')+num('drive_free_alert','Drive free % left')+num('drive_days_alert','Drive days left')+num('disk_temp_alert','Drive °C')+'<div><label class="f">Ping address</label><input class="field" data-k="ping_host" value="'+esc(s.ping_host)+'"></div></div>'+
   '<div class="sec">UPS</div><p class="hint" style="text-align:left;margin:0">A UPS plugged in by USB is found automatically. For Network UPS Tools, enter its address.</p>'+
   '<div class="nums"><div><label class="f">NUT address</label><input class="field" data-k="nut_host" placeholder="blank: USB" value="'+esc(s.nut_host)+'" autocapitalize="off"></div><div><label class="f">UPS name</label><input class="field" data-k="nut_ups" placeholder="first one" value="'+esc(s.nut_ups)+'" autocapitalize="off"></div></div>'+
   sw('ups_shutdown','Shut the PC down safely on low battery','A clean shutdown before the UPS runs dry')+
   '<div class="nums">'+num('ups_shutdown_pct','…at battery %')+num('ups_shutdown_min','…or minutes left')+'</div>'+
   '<button class="btn red wide" id="save" style="margin-top:18px">Save</button><button class="btn wide" id="cancel" style="margin-top:8px">Close</button>'+
   '<button class="btn wide" id="forget" style="margin-top:8px;background:none;color:var(--muted)">Forget the access code on this phone</button>';
  $('sheet').classList.remove('hidden');
  $('cancel').onclick=closeSheet;
  const vsave=()=>{if(!$('vname'))return;LS.set('hal_voice_name',$('vname').value);LS.set('hal_rate',$('vrate').value);LS.set('hal_pitch',$('vpitch').value);LS.set('hal_talk',$('vtalk').checked?'on':'off')};
  if($('vtest'))$('vtest').onclick=()=>{vsave();unlocked=true;utter('Good afternoon, '+S.hal.name+'. Everything is going extremely well.')};
  $('forget').onclick=()=>{try{localStorage.removeItem('hal_token')}catch(e){}token='';closeSheet();showPair()};
  $('save').onclick=async()=>{vsave();const b={};$('panel').querySelectorAll('[data-k]').forEach(el=>{b[el.dataset.k]=el.type==='checkbox'?el.checked:(el.dataset.num!==undefined?Number(el.value):el.value)});
    b.ignored_services=[...$('panel').querySelectorAll('[data-ign]')].filter(el=>el.checked).map(el=>el.dataset.ign);
    try{await api('/api/settings',b);closeSheet();toast('Saved. Thank you for a very enjoyable game.');refresh()}catch(e){toast(e.message,true)}};
}
function closeSheet(){$('sheet').classList.add('hidden')}
$('sheet').onclick=e=>{if(e.target===$('sheet'))closeSheet()};

/* ---------- actions ---------- */
async function act(action,extra){try{const r=await api('/api/action',Object.assign({action},extra||{}));toast(r.message,!r.ok);setTimeout(refresh,800);if(action==='clean_temp'||action==='empty_recycle')setTimeout(loadCleanup,1500)}catch(e){toast(e.message,true)}}
function bindHold(el){let t0=0,raf=0;const fill=el.querySelector('.fill');
  const reset=()=>{cancelAnimationFrame(raf);t0=0;fill.style.width='0'};
  const step=()=>{const p=Math.min(1,(performance.now()-t0)/2000);fill.style.width=(p*100)+'%';if(p>=1){reset();if(navigator.vibrate)navigator.vibrate(60);act(el.dataset.hold,{confirm:true})}else raf=requestAnimationFrame(step)};
  el.onpointerdown=e=>{e.preventDefault();t0=performance.now();raf=requestAnimationFrame(step)};
  el.onpointerup=el.onpointerleave=el.onpointercancel=()=>{if(t0&&performance.now()-t0<2000)toast('I\'m afraid I can\'t do that, '+S.hal.name+'. Hold for 2 seconds.');reset()}}
document.addEventListener('click',async e=>{
  const go=e.target.closest('[data-go]');if(go){show(go.dataset.go);return}
  const r=e.target.closest('[data-range]');if(r){range=r.dataset.range;hist=null;render();loadHist(true);return}
  const a=e.target.closest('[data-act]');if(a){if(a.dataset.confirm&&!confirm(a.dataset.confirm))return;act(a.dataset.act);return}
  const rs=e.target.closest('[data-restart]');if(rs){if(!confirm((rs.textContent==='Start'?'Start ':'Restart ')+rs.dataset.name+'?'))return;
    try{const d=await api('/api/service/restart',{id:rs.dataset.restart});toast(d.message,!d.ok);refresh()}catch(err){toast(err.message,true)}return}
  if(e.target.closest('[data-settings]'))openSettings();
  if(e.target.closest('[data-talk]'))setTalk(!talkative());
});
$('tabs').onclick=e=>{const b=e.target.closest('button');if(b)show(b.dataset.tab)};

/* ---------- navigation, refresh ---------- */
function show(t){tab=t;['status','vitals','services','drives','shield','log'].forEach(k=>$('t-'+k).classList.toggle('hidden',k!==t));
  document.querySelectorAll('#tabs button').forEach(b=>b.classList.toggle('on',b.dataset.tab===(t==='log'?'status':t)));
  window.scrollTo(0,0);if(t==='log'){events=null;loadEvents()}if(t==='vitals')loadHist(true);if(t==='drives')loadCleanup();render()}
function render(){if(!S)return;if(tab==='status'){renderStatus();return}const f={vitals:rVitals,services:rServices,drives:rDrives,shield:rShield,log:rLog}[tab];
  const el=$('t-'+tab);el.innerHTML=f();el.querySelectorAll('[data-hold]').forEach(bindHold)}
async function loadEvents(){try{events=(await api('/api/events')).events;if(S)S.unread=0;if(tab==='log')render()}catch(e){}}
async function loadHist(force){if(!force&&range!=='live'&&Date.now()-histAt<60000)return;try{hist=(await api('/api/history?range='+range)).series;histAt=Date.now();if(tab==='vitals')render()}catch(e){}}
async function loadCleanup(){try{cleanup=await api('/api/cleanup');if(tab==='drives')render();if(cleanup.busy||!cleanup.t)setTimeout(loadCleanup,4000)}catch(e){}}
async function refresh(){if(busy)return;busy=true;try{S=await api('/api/status');lastOk=Date.now();render();if(tab==='vitals')loadHist(range==='live')}catch(e){if(!token)return;lastOk=0}finally{busy=false}}
function start(){$('app').classList.remove('hidden');$('tabs').classList.remove('hidden');refresh();
  if(!start.t)start.t=setInterval(()=>{if(!document.hidden&&token)refresh()},3000)}
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&token)refresh()});
if(token)start();else showPair();
</script></body></html>
"""


# ==============================================================================
# 15. STARTUP
# ==============================================================================

def autostart_path() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not IS_WINDOWS or not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / AUTOSTART_NAME


def install_autostart(extra_args: list[str]) -> Path:
    path = autostart_path()
    if path is None:
        raise ConfigError("Starting automatically is only available on Windows.")
    if task_exists(STARTUP_TASK):
        raise ConfigError(f'It already starts at logon as administrator (task "{STARTUP_TASK}"). Nothing to do.')
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    cmd = [str(windowless if windowless.exists() else exe), str(Path(__file__).resolve())] + extra_args
    command = " ".join('"' + str(p).replace('"', "") + '"' for p in cmd).replace('"', '""')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("' Starts HAL 9000 without a window. Delete this file to stop that.\r\n"
                    'Set sh = CreateObject("WScript.Shell")\r\n'
                    f'sh.CurrentDirectory = "{str(BASE_DIR).replace(chr(34), chr(34) * 2)}"\r\n'
                    f'sh.Run "{command}", 0, False\r\n', encoding="utf-16")
    return path


def lan_addresses() -> list[str]:
    out = []
    try:
        for name, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254.")):
                    out.append(a.address)
    except Exception:
        pass
    return out


def setup_logging() -> None:
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    try:
        fh = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except OSError:
        pass
    if sys.stdout and not str(Path(sys.executable).name).lower().startswith("pythonw"):
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        log.addHandler(sh)


def main(argv=None) -> None:
    global HAL, _token_bytes, ALLOW_ANY_CLIENT
    ap = argparse.ArgumentParser(description="HAL 9000: watches over this PC, on your phone.")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=int(os.environ.get("HAL_PORT") or DEFAULT_PORT))
    ap.add_argument("--allow-cidr", action="append", default=[], help="also allow clients from this network")
    ap.add_argument("--allow-any-client", action="store_true", help="NOT recommended")
    ap.add_argument("--demo", action="store_true", help="simulated data, for a look around")
    ap.add_argument("--demo-ups", choices=["online", "battery"], default="online")
    ap.add_argument("--install-autostart", action="store_true")
    ap.add_argument("--remove-autostart", action="store_true")
    args = ap.parse_args(argv)
    if args.install_autostart or args.remove_autostart:
        if args.remove_autostart:
            p = autostart_path()
            if p and p.exists():
                p.unlink()
                print("HAL 9000 won't start at login any more.")
            else:
                print("Nothing to remove.")
            return
        extra = ["--port", str(args.port)] if args.port != DEFAULT_PORT else []
        print(f"HAL 9000 will start at login: {install_autostart(extra)}")
        return
    for c in args.allow_cidr:
        EXTRA_ALLOWED_NETS.append(ipaddress.ip_network(c, strict=False))
    ALLOW_ANY_CLIENT = args.allow_any_client
    setup_logging()
    token = load_or_create_access_token()
    _token_bytes = token.encode("utf-8")
    HAL = make_demo_hal(args.demo_ups) if args.demo else Hal()
    server = ThreadingHTTPServer((args.host, args.port), HalHandler)
    server.daemon_threads = True
    print("\n  HAL 9000 is watching.  \"Good afternoon, gentlemen. I am a HAL 9000 computer.\"")
    print(f"  Open on your phone:  " + "  or  ".join(f"http://{a}:{args.port}/" for a in lan_addresses() or ["<this-pc>"]))
    print(f"  Access code (the same as the NZB phone app):\n    {token}\n")
    log.info("HAL 9000 %s listening on %s:%s%s", VERSION, args.host, args.port, " (demo)" if args.demo else "")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        HAL.stop()
        if not args.demo and HAL.monitor:
            HAL.monitor.stop()


if __name__ == "__main__":
    main()
