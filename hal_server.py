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
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover - friendly message on a PC without psutil
    if __name__ != "__main__":
        raise           # imported by the NZB phone server: it carries on without HAL and says why
    print("HAL 9000 needs psutil. Install it with:  pip install psutil")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import system_monitor as sm  # noqa: E402

VERSION = "1.22.5"
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
PROPER_WORDS = {"I", "Windows", "Microsoft", "Defender", "SABnzbd", "Jellyfin", "Sonarr", "Radarr", "Prowlarr", "Kodi",
                "NZBGeek", "Tailscale", "HAL"}
QUOTE_INFO = [   # only small things (notes): say what it is, so it can be found
    "Everything is functioning normally, {name}. Just one small thing: {issue_lc}.",
    "All systems are functioning normally, {name}. You might like to know that {issue_lc}.",
]
QUOTE_INFO_MANY = [
    "Everything is functioning normally, {name}. There are {k} small things you might like to know about.",
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
        pool, issue = (QUOTE_INFO if len(issues) == 1 else QUOTE_INFO_MANY), issues[0]
    else:
        pool, issue = QUOTES_OK, None
    text = pool[slot % len(pool)]
    title = issue["title"] if issue else ""
    first = title.split(" ", 1)[0] if title else ""
    keep = first in PROPER_WORDS or first[:1].isdigit() or first[1:2].isupper()      # "Windows", "I", "SABnzbd", "UPS"...
    lc = title if keep or not title else title[:1].lower() + title[1:]
    return text.format(name=name, n=len(serious), k=len(issues), issue=title, issue_lc=lc)


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
    "denon_host": "",               # the Denon/Marantz amp the consoles go through (blank: HAL finds it himself)
    "ups_shutdown": False,          # shut the PC down when the UPS battery runs low
    "ups_shutdown_pct": 25, "ups_shutdown_min": 5,
    "ignored_services": [],
    "pihole_url": "",               # blank: look for it among the PC's DNS servers
    "pihole_password": "",          # dpapi:... (Pi-hole 6 password / app password, or Pi-hole 5 API token)
    "pihole_off": False,            # don't watch a Pi-hole at all
    "kuma_url": "",                 # Uptime Kuma, e.g. http://192.168.68.63:3001 (blank: not used)
    "kuma_key": "",                 # dpapi:... Uptime Kuma API key (Settings > API Keys)
    "brief_time": "07:30",          # morning briefing to the phone (blank: off)
    "brief_weekly": "sun",          # that day's briefing is the week's summary (blank: never)
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
        if k not in DEFAULT_SETTINGS or k.startswith(("pihole_", "kuma_")):   # Pi-hole has its own page (/api/pihole)
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
            elif k in ("nut_host", "ping_host", "denon_host") and v and not re.fullmatch(r"[A-Za-z0-9.\-:]{1,253}", v):
                raise ValueError("That address doesn't look right.")
            elif k == "brief_time":
                v = parse_hhmm(v)
            elif k == "brief_weekly" and v not in ("", *WEEKDAYS):
                raise ValueError("Pick a day for the weekly summary.")
            elif k == "nut_ups" and v and not re.fullmatch(r"[\w.\-]{1,64}", v):
                raise ValueError("That UPS name doesn't look right.")
            s[k] = v
    if not s["ping_host"]:
        s["ping_host"] = "1.1.1.1"
    return s


# ==============================================================================
# PI-HOLE (the Raspberry Pi that blocks ads for the whole house)
# ==============================================================================
# Works with Pi-hole 6 (/api, session login) and Pi-hole 5 (/admin/api.php, API token); which one is
# worked out on its own. With no address set, HAL looks for it among the PC's DNS servers.

DNS_SERVERS_PS = r"""
Get-DnsClientServerAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object { $_.ServerAddresses } |
  Select-Object -Unique | ConvertTo-Json -Compress
"""


def dpapi_protect(text: str) -> str:
    """Encrypts a secret for this Windows user (dpapi:<base64>); stored as-is elsewhere (tests, Linux)."""
    if not text or not IS_WINDOWS:
        return text
    import ctypes.wintypes as wt

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_void_p, wt.DWORD, ctypes.POINTER(DATA_BLOB)]
    crypt32.CryptProtectData.restype = wt.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    raw = text.encode("utf-8")
    buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    out = DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(DATA_BLOB(len(raw), buf)), "HAL 9000", None, None, None, 0, ctypes.byref(out)):
        raise OSError("Windows couldn't encrypt the password.")
    try:
        return "dpapi:" + base64.b64encode(ctypes.string_at(out.pbData, out.cbData)).decode("ascii")
    finally:
        kernel32.LocalFree(out.pbData)


def dns_servers() -> list[str]:
    """The PC's DNS servers (private IPv4 only): where the Pi-hole usually is."""
    found: list[str] = []
    if IS_WINDOWS:
        res = sm.run_powershell_json(DNS_SERVERS_PS, timeout=20)
        found = [res] if isinstance(res, str) else [str(x) for x in res or []]
    else:
        try:
            found = re.findall(r"^\s*nameserver\s+(\S+)", Path("/etc/resolv.conf").read_text(), re.M)
        except OSError:
            pass
    out = []
    for a in found:
        try:
            ip = ipaddress.ip_address(a)
        except ValueError:
            continue
        if ip.version == 4 and ip.is_private and not ip.is_loopback and a not in out:
            out.append(a)
    return out[:4]


def pihole_base(text: str) -> str:
    """'192.168.68.5', 'pi.hole', 'http://192.168.68.5/admin/' -> 'http://192.168.68.5' ('' if blank)."""
    text = str(text or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    p = urllib.parse.urlsplit(text)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password \
            or not re.fullmatch(r"[A-Za-z0-9.\-]{1,253}", p.hostname):
        raise ValueError("Use the Pi-hole's address, like 192.168.68.5 or http://pi.hole.")
    return f"{p.scheme}://{p.hostname}" + (f":{p.port}" if p.port else "")


def _ph_req(url: str, method: str = "GET", body=None, headers: dict | None = None, timeout: float = 5.0):
    """(HTTP status, parsed JSON or None). Network failures raise OSError."""
    h = {"Accept": "application/json", "User-Agent": f"HAL9000/{VERSION}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read(2_000_000)
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read(200_000)
    except urllib.error.URLError as exc:
        raise OSError(str(exc.reason)) from None
    try:
        return status, json.loads(raw or b"null")
    except ValueError:
        return status, None


def pihole_detect(base: str, timeout: float = 4.0) -> int:
    """6 or 5 if a Pi-hole answers at base, else 0."""
    try:
        st, d = _ph_req(base + "/api/auth", timeout=timeout)
        if st in (200, 401) and isinstance(d, dict) and ("session" in d or "error" in d):
            return 6
        st, d = _ph_req(base + "/admin/api.php?versions", timeout=timeout)
        if st == 200 and isinstance(d, (dict, list)):
            return 5
    except OSError:
        pass
    return 0


class PiHoleAuthError(Exception):
    pass


class PiHole:
    """One Pi-hole. Keeps one Pi-hole 6 session (they're limited) and signs in again when it runs out."""

    def __init__(self, base: str = "", password: str = "", version: int = 0):
        self.base, self.password, self.version = base, password, version
        self.sid = ""
        self.lock = threading.Lock()

    def _login(self) -> None:
        st, d = _ph_req(self.base + "/api/auth", "POST", {"password": self.password})
        sess = (d or {}).get("session") if isinstance(d, dict) else None
        if st == 200 and isinstance(sess, dict) and sess.get("valid"):
            self.sid = str(sess.get("sid") or "")
            return
        if st == 429:
            raise OSError("Pi-hole says too many sign-ins; I'll try again shortly.")
        raise PiHoleAuthError("Pi-hole needs its password." if not self.password else "Pi-hole didn't accept the password.")

    def _v6(self, method: str, path: str, body=None):
        for attempt in (0, 1):
            st, d = _ph_req(self.base + path, method, body, {"X-FTL-SID": self.sid} if self.sid else None)
            if st == 401 and attempt == 0:
                self.sid = ""
                self._login()
                continue
            if st == 401:
                raise PiHoleAuthError("Pi-hole didn't accept the password.")
            if st >= 400 or not isinstance(d, dict):
                raise OSError(f"Pi-hole answered HTTP {st}.")
            return d
        raise OSError("Pi-hole didn't answer.")

    def _v5(self, query: str):
        st, d = _ph_req(f"{self.base}/admin/api.php?{query}&auth={urllib.parse.quote(self.password)}")
        if st >= 400:
            raise OSError(f"Pi-hole answered HTTP {st}.")
        if not isinstance(d, dict) or not d:   # an empty answer means the token is missing or wrong
            raise PiHoleAuthError("Pi-hole needs its API token (Settings › API)." if not self.password
                                  else "Pi-hole didn't accept the API token.")
        return d

    def summary(self) -> dict:
        with self.lock:
            if self.version == 6:
                d = self._v6("GET", "/api/stats/summary")
                b = self._v6("GET", "/api/dns/blocking")
                q, g = d.get("queries") or {}, d.get("gravity") or {}
                out = {"queries": to_int(q.get("total")), "blocked": to_int(q.get("blocked")),
                       "percent": round(to_float(q.get("percent_blocked"), 0) or 0, 1),
                       "clients": to_int((d.get("clients") or {}).get("active")), "domains": to_int(g.get("domains_being_blocked")),
                       "gravity_t": to_int(g.get("last_update")) or None,
                       "blocking": str(b.get("blocking")) == "enabled", "timer": to_float(b.get("timer")) or None}
                try:
                    td = self._v6("GET", "/api/stats/top_domains?blocked=true&count=5")
                    tc = self._v6("GET", "/api/stats/top_clients?count=200")
                    out["top_blocked"] = [[str(x.get("domain")), to_int(x.get("count"))] for x in td.get("domains") or []][:5]
                    ta = self._v6("GET", "/api/stats/top_domains?count=5")
                    out["top_allowed"] = [[str(x.get("domain")), to_int(x.get("count"))] for x in ta.get("domains") or []][:5]
                    out["top_clients"] = [[str(x.get("name") or x.get("ip")), to_int(x.get("count"))] for x in tc.get("clients") or []][:5]
                    out["all_clients"] = [[str(x.get("name") or ""), str(x.get("ip") or "")] for x in tc.get("clients") or []][:200]
                except OSError:
                    out["top_blocked"], out["top_clients"] = [], []
                out["hw"] = self._v6_hardware()
                out["hourly"] = self._hourly(lambda: self._v6("GET", "/api/history").get("history") or [], "v6")
                return out
            d = self._v5("summaryRaw")
            out = {"queries": to_int(d.get("dns_queries_today")), "blocked": to_int(d.get("ads_blocked_today")),
                   "percent": round(to_float(d.get("ads_percentage_today"), 0) or 0, 1), "clients": to_int(d.get("unique_clients")),
                   "domains": to_int(d.get("domains_being_blocked")),
                   "gravity_t": to_int((d.get("gravity_last_updated") or {}).get("absolute")) or None,
                   "blocking": str(d.get("status")) == "enabled", "timer": None, "top_blocked": [], "top_clients": []}
            try:
                ti = self._v5("topItems=5")
                out["top_blocked"] = [[k, to_int(v)] for k, v in (ti.get("top_ads") or {}).items()][:5]
                out["top_allowed"] = [[k, to_int(v)] for k, v in (ti.get("top_queries") or {}).items()][:5]
                tc = self._v5("topClients=200")
                src = (tc.get("top_sources") or {}).items()
                out["top_clients"] = [[k.split("|")[0] or k, to_int(v)] for k, v in src][:5]
                out["all_clients"] = [[k.split("|")[0] if "|" in k else "", k.split("|")[-1]] for k, _v in src][:200]
            except (OSError, PiHoleAuthError):
                pass
            out["hourly"] = self._hourly(lambda: self._v5("overTimeData10mins"), "v5")
            return out

    def _hourly(self, fetch, kind: str) -> list:
        """Queries and blocked per hour over the last 24 hours ([[total, blocked], ...], oldest first); kept 5 minutes."""
        if time.time() - getattr(self, "_hourly_t", 0) < 300:
            return getattr(self, "_hourly_v", [])
        self._hourly_t = time.time()
        buckets: dict = {}
        try:
            raw = fetch()
            if kind == "v6":
                for x in raw:
                    h = to_int(x.get("timestamp")) // 3600
                    b = buckets.setdefault(h, [0, 0])
                    b[0] += to_int(x.get("total"))
                    b[1] += to_int(x.get("blocked"))
            else:
                for ts, n in (raw.get("domains_over_time") or {}).items():
                    buckets.setdefault(to_int(ts) // 3600, [0, 0])[0] += to_int(n)
                for ts, n in (raw.get("ads_over_time") or {}).items():
                    buckets.setdefault(to_int(ts) // 3600, [0, 0])[1] += to_int(n)
        except Exception:  # noqa: BLE001 - only a graph
            return getattr(self, "_hourly_v", [])
        now_h = int(time.time()) // 3600
        self._hourly_v = [buckets.get(h, [0, 0]) for h in range(now_h - 23, now_h + 1)]
        return self._hourly_v

    HW_EVERY = 120

    def _v6_hardware(self) -> dict:
        """The Raspberry Pi itself (Pi-hole 6 only): model, temperature, CPU, memory, uptime, versions. Every 2 min."""
        now = time.time()
        cached = getattr(self, "_hw", None)
        if cached and now - cached.get("t", 0) < self.HW_EVERY:
            return cached
        hw: dict = {"t": now}
        try:
            sysd = (self._v6("GET", "/api/info/system").get("system") or {})
            cpu, ram = sysd.get("cpu") or {}, ((sysd.get("memory") or {}).get("ram") or {})
            load = (cpu.get("load") or {}).get("raw") or []
            hw.update(uptime=to_int(sysd.get("uptime")) or None, cpu_pct=round(to_float(cpu.get("%cpu"), 0) or 0, 1),
                      cores=to_int(cpu.get("nprocs")) or None, load=[round(to_float(x, 0) or 0, 2) for x in load[:3]],
                      mem_pct=round(to_float(ram.get("%used"), 0) or 0, 1), mem_total=to_int(ram.get("total")) * 1024 or None)
        except (OSError, PiHoleAuthError, AttributeError, TypeError):
            pass
        try:
            sen = (self._v6("GET", "/api/info/sensors").get("sensors") or {})
            hw.update(temp=to_float(sen.get("cpu_temp")), hot_limit=to_float(sen.get("hot_limit")),
                      temp_unit=str(sen.get("unit") or "C"))
        except (OSError, PiHoleAuthError, AttributeError, TypeError):
            pass
        try:
            host = (self._v6("GET", "/api/info/host").get("host") or {})
            hw["model"] = str(host.get("model") or (host.get("uname") or {}).get("machine") or "")[:60]
            hw["hostname"] = str((host.get("uname") or {}).get("nodename") or "")[:60]
        except (OSError, PiHoleAuthError, AttributeError, TypeError):
            pass
        try:
            ver = (self._v6("GET", "/api/info/version").get("version") or {})
            mine, behind = {}, []
            for part in ("core", "web", "ftl"):
                p = ver.get(part) or {}
                loc = str((p.get("local") or {}).get("version") or "")
                rem = str((p.get("remote") or {}).get("version") or "")
                mine[part] = loc
                if loc and rem and loc != rem:
                    behind.append(part)
            hw.update(version=mine.get("core") or "", update=bool(behind))
        except (OSError, PiHoleAuthError, AttributeError, TypeError):
            pass
        self._hw = hw
        return hw

    def set_blocking(self, enabled: bool, seconds: int = 0) -> None:
        with self.lock:
            if self.version == 6:
                self._v6("POST", "/api/dns/blocking", {"blocking": enabled, "timer": seconds or None})
            else:
                self._v5("enable" if enabled else f"disable={int(seconds)}")

    def logout(self) -> None:
        if self.version == 6 and self.sid:
            try:
                _ph_req(self.base + "/api/auth", "DELETE", headers={"X-FTL-SID": self.sid}, timeout=3)
            except OSError:
                pass
            self.sid = ""


# ==============================================================================
# UPTIME KUMA (watches websites and devices; HAL reads its /metrics)
# ==============================================================================
# /metrics needs an API key (Uptime Kuma: Settings > API Keys), sent as the password of basic auth.

KUMA_STATUS = {0: "down", 1: "up", 2: "pending", 3: "maintenance"}
_KUMA_LINE = re.compile(r'^(monitor_status|monitor_response_time|monitor_cert_days_remaining)\{(.*)\}\s+(-?[\d.eE+]+)\s*$')
_KUMA_LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


class KumaAuthError(Exception):
    pass


def kuma_base(text: str) -> str:
    """'192.168.68.63:3001' -> 'http://192.168.68.63:3001' ('' if blank)."""
    text = str(text or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    p = urllib.parse.urlsplit(text)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password \
            or not re.fullmatch(r"[A-Za-z0-9.\-]{1,253}", p.hostname):
        raise ValueError("Use Uptime Kuma's address, like 192.168.68.63:3001.")
    return f"{p.scheme}://{p.hostname}" + (f":{p.port}" if p.port else "")


def parse_kuma_metrics(text: str) -> list[dict]:
    mons: dict[str, dict] = {}
    for line in text.splitlines():
        m = _KUMA_LINE.match(line.strip())
        if not m:
            continue
        labels = {k: v.replace('\\"', '"').replace("\\\\", "\\") for k, v in _KUMA_LABEL.findall(m.group(2))}
        name = labels.get("monitor_name") or ""
        if not name:
            continue
        mon = mons.setdefault(name, {"name": name[:80], "type": labels.get("monitor_type", "")[:20], "status": "pending",
                                     "ms": None, "cert_days": None})
        val = float(m.group(3))
        if m.group(1) == "monitor_status":
            mon["status"] = KUMA_STATUS.get(int(val), "pending")
        elif m.group(1) == "monitor_response_time":
            mon["ms"] = round(val) if val >= 0 else None
        else:
            mon["cert_days"] = round(val)
    order = {"down": 0, "pending": 1, "maintenance": 2, "up": 3}
    return sorted(mons.values(), key=lambda x: (order.get(x["status"], 4), x["name"].lower()))[:200]


def kuma_fetch(base: str, key: str, timeout: float = 6.0) -> list[dict]:
    h = {"User-Agent": f"HAL9000/{VERSION}", "Accept": "text/plain"}
    if key:
        h["Authorization"] = "Basic " + base64.b64encode(f":{key}".encode()).decode("ascii")
    try:
        with _OPENER.open(urllib.request.Request(base + "/metrics", headers=h), timeout=timeout) as resp:
            text = resp.read(5_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise KumaAuthError("Uptime Kuma needs an API key." if not key else "Uptime Kuma didn't accept the API key.") from None
        raise OSError(f"Uptime Kuma answered HTTP {exc.code}.") from None
    except urllib.error.URLError as exc:
        raise OSError(str(exc.reason)) from None
    if "monitor_status" not in text and "# HELP" not in text:
        raise OSError("That doesn't look like Uptime Kuma.")
    return parse_kuma_metrics(text)


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

# ==============================================================================
# 9b. WINDOWS EVENT LOG: only what you'd need to see
# ==============================================================================
# Each query is run on its own (a provider that isn't on this PC must not spoil the others). Drive
# warnings are included (retries and resets are early signs of a failing drive or cable); everything
# else only at Error and Critical. DCOM and the other everyday noise never match these filters.

EVENTLOG_PS = r"""
$since = (Get-Date).AddDays(-30)
$out = New-Object System.Collections.ArrayList
function Q($f, $cat, $max) {
  try { Get-WinEvent -FilterHashtable $f -MaxEvents $max -ErrorAction Stop | ForEach-Object {
      $m = ("" + $_.Message) -replace '\s+', ' '; if ($m.Length -gt 500) { $m = $m.Substring(0, 500) + '...' }
      [void]$out.Add(@{ t = $_.TimeCreated.ToUniversalTime().ToString('o'); lvl = [int]$_.Level; src = $_.ProviderName; id = [int]$_.Id; cat = $cat; msg = $m }) } } catch {}
}
foreach ($p in 'disk', 'Ntfs', 'Microsoft-Windows-Ntfs', 'stornvme', 'storahci', 'storport', 'Microsoft-Windows-StorPort', 'volmgr', 'partmgr', 'iaStorA', 'iaStorAC', 'iaStorAVC', 'iaStorV', 'nvstor', 'amd_sata', 'amd_xata', 'USBSTOR') {
  Q @{ LogName = 'System'; ProviderName = $p; Level = 1, 2, 3; StartTime = $since } 'drives' 150 }
Q @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-Kernel-Power'; Id = 41; StartTime = $since } 'crashes' 50
Q @{ LogName = 'System'; ProviderName = 'EventLog'; Id = 6008; StartTime = $since } 'crashes' 50
Q @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-WER-SystemErrorReporting'; Id = 1001; StartTime = $since } 'crashes' 50
Q @{ LogName = 'System'; ProviderName = 'BugCheck'; Id = 1001; StartTime = $since } 'crashes' 50
Q @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-WHEA-Logger'; Level = 1, 2, 3; StartTime = $since } 'hardware' 100
Q @{ LogName = 'System'; ProviderName = 'Service Control Manager'; Id = 7000, 7001, 7009, 7011, 7023, 7024, 7031, 7034; StartTime = $since } 'services' 100
Q @{ LogName = 'Application'; ProviderName = 'Application Error'; Id = 1000; StartTime = $since } 'apps' 100
Q @{ LogName = 'Application'; ProviderName = 'Application Hang'; Id = 1002; StartTime = $since } 'apps' 60
Q @{ LogName = 'Application'; ProviderName = '.NET Runtime'; Id = 1026; StartTime = $since } 'apps' 60
Q @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-WindowsUpdateClient'; Id = 20; StartTime = $since } 'updates' 30
Q @{ LogName = 'Microsoft-Windows-Windows Defender/Operational'; Id = 1116, 1117, 1118, 1119, 5001, 5010, 5012; StartTime = $since } 'security' 60
Q @{ LogName = 'System'; Level = 1; StartTime = $since } 'other' 50
@{ events = $out } | ConvertTo-Json -Compress -Depth 4
"""

EVENT_CATS = {"drives": "Drives", "crashes": "Crashes", "hardware": "Hardware", "services": "Services",
              "apps": "App crashes", "updates": "Updates", "security": "Security", "other": "Other critical"}
STORAGE_IDS_SERIOUS = {("disk", 7), ("disk", 11), ("disk", 51), ("disk", 52), ("disk", 154), ("ntfs", 55), ("ntfs", 137),
                       ("ntfs", 140), ("ntfs", 50), ("ntfs", 57), ("stornvme", 11)}
_RESET_SRC = ("stornvme", "storahci", "storport", "iastora", "iastorac", "iastoravc", "iastorv", "nvstor", "amd_sata", "amd_xata")


def _ev_drive(msg: str, disks: dict) -> str:
    """'Drive Z: (Seagate IronWolf)' from a message naming \\Device\\Harddisk2 or a volume letter."""
    m = re.search(r"Harddisk(\d+)\\DR", msg) or re.search(r"\bdisk (\d+)\b", msg, re.I)
    if m and m.group(1) in disks:
        d = disks[m.group(1)]
        return ("Drive " + ", ".join(d["letters"]) if d["letters"] else f"Disk {m.group(1)}") + (f" ({d['name']})" if d.get("name") else "")
    m = re.search(r"(?:volume|VolumeId:)\s*([A-Z]:)", msg)
    if m:
        return f"Drive {m.group(1)}"
    if m := re.search(r"Harddisk(\d+)", msg):
        return f"Disk {m.group(1)}"
    return ""


def friendly_event(e: dict, disks: dict) -> dict:
    """Adds a plain-English title, an explanation, the drive or app it concerns, and how serious it is."""
    src = str(e.get("src") or "")
    s, eid, msg = src.lower().replace("microsoft-windows-", ""), to_int(e.get("id")), str(e.get("msg") or "")
    lvl = {1: "critical", 2: "error", 3: "warning"}.get(to_int(e.get("lvl")), "error")
    who = _ev_drive(msg, disks) if e.get("cat") == "drives" else ""
    d = who or "a drive"
    title, explain = "", ""
    if s == "disk":
        title, explain, lvl = {
            7: (f"Bad block on {d}", "The drive reported a damaged area. That's an early sign of a failing drive: back up what's on it and check its health on the Drives page.", "critical"),
            11: (f"Controller error on {d}", "Often a loose or failing SATA/power cable, sometimes the drive itself. Reseat or replace the cable first.", "error"),
            15: (f"{d} wasn't ready", "The drive didn't answer in time: power saving, a cable, or a failing drive.", "error"),
            51: (f"Read/write error on {d}", "Windows couldn't read or write the drive while swapping memory. Usually a cable or a failing drive.", "error"),
            52: (f"{d} predicts it will fail", "The drive's own health check (SMART) says it's about to fail. Back it up now and replace it.", "critical"),
            153: (f"{d} had to retry", "Windows had to retry a read or write. Once in a while is harmless; many in a row point to a failing drive, cable or power.", "warning"),
            154: (f"Hardware error on {d}", "The drive reported a hardware fault. Back it up and check its health.", "error"),
            157: (f"{d} disconnected", "The drive disappeared for a moment: a loose cable, a USB drive unplugged, or power saving.", "warning"),
        }.get(eid, ("", "", lvl))
    elif s == "ntfs":
        title, explain = {
            55: (f"File system damage on {d}", f"Windows found corruption on the drive. Run 'chkdsk {who.split()[1] if who.startswith('Drive ') else 'X:'} /f' (it may need a restart)."),
            137: (f"File system damage on {d}", "Windows couldn't repair the file system by itself. Run chkdsk on that drive."),
            140: (f"Couldn't save data to {d}", "Usually the drive vanished for a moment (cable, power, USB). Files being written may be damaged."),
            50: (f"Write failed on {d}", "Data may have been lost. Check the drive's cable and health."),
            57: (f"Write failed on {d}", "Data may have been lost. Check the drive's cable and health."),
            98: (f"{d} needs a check", "Windows wants to check this drive's file system (chkdsk)."),
        }.get(eid, ("", ""))
    elif s.startswith(_RESET_SRC):
        if eid == 129:
            title, explain, lvl = (f"{who or 'A drive'} stopped answering and was reset",
                                   "Windows reset the drive controller. A few are harmless; repeated resets point to cables, power or drive firmware.", "warning")
        elif eid == 11:
            title, explain = (f"Controller error ({src})", "The drive controller reported an error. Check cables and drive health.")
    elif s == "kernel-power" and eid == 41:
        title, explain, lvl = ("The PC shut down unexpectedly", "It restarted without shutting down properly: a power cut, a crash, or the power button held down.", "critical")
    elif s == "eventlog" and eid == 6008:
        title, explain = ("The previous shutdown was unexpected", "Windows didn't shut down cleanly last time: a power cut or a crash.")
    elif eid == 1001 and s in ("bugcheck", "wer-systemerrorreporting"):
        m = re.search(r"bugcheck was:\s*(0x[0-9a-fA-F]+)", msg)
        title, explain, lvl = ("Blue screen", "Windows crashed" + (f" (stop code {m.group(1)})" if m else "") + ". One is bad luck; repeats usually mean a driver, RAM or overheating.", "critical")
    elif s == "whea-logger":
        fatal = lvl in ("critical", "error") and "corrected" not in msg.lower()
        title = "Fatal hardware error" if fatal else "Corrected hardware error"
        explain = ("The CPU, memory or a PCIe card reported an error. Occasional corrected errors are usually fine; "
                   "frequent or fatal ones point to an unstable overclock, RAM or a failing card.")
        m = re.search(r"Component:\s*(.+?)(?=\s+Error Source:|[,.;]|$)", msg)
        who = m.group(1).strip() if m else ""
    elif s == "service control manager":
        m = re.search(r"The (.+?) service", msg)
        who = m.group(1) if m else ""
        if eid in (7031, 7034):
            title, explain = (f"Service crashed: {who or 'unknown'}", "It stopped unexpectedly. Windows may have restarted it.")
        else:
            title, explain = (f"Service didn't start: {who or 'unknown'}", "Windows tried to start it and it failed.")
    elif s in ("application error", "application hang", ".net runtime"):
        m = re.search(r"(?:Faulting application name|Application|The program)\s*:?\s*([\w .\-]+?\.exe)", msg, re.I)
        who = m.group(1).strip() if m else ""
        title = (f"{who or 'An app'} stopped responding" if s == "application hang" else f"{who or 'An app'} crashed")
        explain = "Windows recorded the crash. Once is nothing to worry about; if it keeps happening, the app may need updating."
    elif s == "windowsupdateclient" and eid == 20:
        m = re.search(r"(KB\d+)", msg)
        title, explain, lvl = (f"Update {m.group(1)} failed to install" if m else "A Windows update failed to install",
                               "Windows Update will usually try again. If it keeps failing, run Windows Update by hand.", "warning")
    elif s == "windows defender":
        title, explain, lvl = {
            1116: ("Defender found a threat", "Microsoft Defender detected malware. Open Windows Security › Protection history.", "critical"),
            1117: ("Defender dealt with a threat", "Microsoft Defender acted on malware it found. Check Windows Security › Protection history.", "critical"),
            1118: ("Defender couldn't remove a threat", "Open Windows Security › Protection history and run a full scan.", "critical"),
            1119: ("Defender couldn't remove a threat", "Open Windows Security › Protection history and run a full scan.", "critical"),
            5001: ("Real-time protection was turned off", "Microsoft Defender stopped watching files as they open.", "critical"),
            5010: ("Defender scanning was turned off", "Scanning for malware and spyware is off.", "warning"),
            5012: ("Defender virus scanning was turned off", "Scanning for viruses is off.", "warning"),
        }.get(eid, ("", "", lvl))
    if not title:
        first = re.split(r"(?<=[.!?])\s", msg, 1)[0].strip()
        title = (first[:90] + "…") if len(first) > 90 else (first or f"{src} event {eid}")
    t = sm.parse_date(e.get("t"))
    serious = e.get("cat") == "drives" and ((s, eid) in STORAGE_IDS_SERIOUS or lvl == "critical")
    return {"t": t, "cat": e.get("cat") or "other", "level": lvl, "title": title, "explain": explain, "who": who,
            "src": src, "id": eid, "msg": msg, "serious": serious}


def parse_winevents(raw, disks: dict) -> list[dict]:
    seen, out = set(), []
    for e in sm.as_list((raw or {}).get("events") if isinstance(raw, dict) else raw):
        if not isinstance(e, dict):
            continue
        key = (e.get("t"), e.get("src"), e.get("id"))
        if key in seen:
            continue
        seen.add(key)
        if e.get("cat") == "other" and str(e.get("src")) == "Microsoft-Windows-Kernel-Power" and to_int(e.get("id")) == 41:
            continue  # already listed as a crash
        ev = friendly_event(e, disks)
        if ev["t"]:
            out.append(ev)
    out.sort(key=lambda x: -x["t"])
    return out


def event_summary(events: list[dict], now: float | None = None, days: int = 7) -> dict:
    now = time.time() if now is None else now
    recent = [e for e in events if now - e["t"] <= days * 86400]
    cats = {c: sum(1 for e in recent if e["cat"] == c) for c in EVENT_CATS}
    # per drive (grouped by letter): flagged when any serious error, or 5+ retries/resets in the period
    groups: dict[str, dict] = {}
    for e in recent:
        if e["cat"] != "drives":
            continue
        m = re.search(r"([A-Z]:)", e["who"] or "")
        g = groups.setdefault(m.group(1) if m else (e["who"] or "?"), {"name": "", "n": 0, "serious": 0})
        g["n"] += 1
        g["serious"] += 1 if e["serious"] else 0
        if len(e["who"] or "") > len(g["name"]):
            g["name"] = e["who"]
    per_drive = {(g["name"] or "A drive"): g["n"] for g in groups.values() if g["serious"] or g["n"] >= 5}
    return {"total": len(recent), "cats": cats, "serious_drives": per_drive,
            "critical": sum(1 for e in recent if e["level"] == "critical")}


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
             "logons": "Failed sign-ins have stopped", "evdrive": "No drive errors in the last 7 days", "unexpected": "No unexpected shutdowns this week",
             "ups_rb": "The UPS battery is fine again", "ups_load": "UPS load is back to normal",
             "ups_err": "The UPS is talking to me again"}
    return fixed.get(kind, f"All clear: {title}")


HIST_KEYS = ("cpu", "mem", "gpu", "cpu_temp", "ping", "net_down", "net_up", "disk")



# ==============================================================================
# FUN: briefings, Siri, lifetime counters
# ==============================================================================

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
AD_MILESTONES = [10_000, 50_000, 100_000, 250_000, 500_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000, 25_000_000, 50_000_000, 100_000_000]


def sab_history() -> dict:
    """SABnzbd's history totals and recent items ({} if SABnzbd isn't set up or doesn't answer)."""
    cfg = nzb_config()
    key = dpapi_unprotect(str(cfg.get("sab_api_key") or ""))
    if not key:
        return {}
    base = str(cfg.get("sab_url") or "http://127.0.0.1:8080").strip().rstrip("/")
    try:
        h = (http_json(f"{base}/api?mode=history&output=json&limit=60&apikey={urllib.parse.quote(key)}") or {}).get("history") or {}
    except Exception:  # noqa: BLE001
        return {}
    slots = [{"name": str(s.get("name") or ""), "ok": str(s.get("status") or "") == "Completed",
              "t": to_int(s.get("completed"))} for s in h.get("slots") or []]
    return {"count": to_int(h.get("noofslots")), "total_size": str(h.get("total_size") or ""),
            "week_size": str(h.get("week_size") or ""), "day_size": str(h.get("day_size") or ""), "slots": slots}


_sab_q_cache: dict = {"t": 0.0, "v": {}}
_sab_h_cache: dict = {"t": 0.0, "v": {}}


def sab_queue() -> dict:
    """SABnzbd's queue for the TV's downloads strip ({} if SABnzbd isn't set up). Cached for 10 s."""
    if time.time() - _sab_q_cache["t"] < 10:
        return _sab_q_cache["v"]
    cfg = nzb_config()
    key = dpapi_unprotect(str(cfg.get("sab_api_key") or ""))
    out: dict = {}
    if key:
        base = str(cfg.get("sab_url") or "http://127.0.0.1:8080").strip().rstrip("/")
        try:
            q = (http_json(f"{base}/api?mode=queue&output=json&limit=1&apikey={urllib.parse.quote(key)}") or {}).get("queue") or {}
            slot = (q.get("slots") or [{}])[0]
            out = {"ok": True, "paused": bool(q.get("paused")), "count": to_int(q.get("noofslots_total") or q.get("noofslots")),
                   "speed": str(q.get("speed") or "").strip(), "kbps": to_float(q.get("kbpersec"), 0) or 0,
                   "name": str(slot.get("filename") or ""), "pct": to_int(slot.get("percentage")), "left": str(slot.get("timeleft") or "")}
        except Exception:  # noqa: BLE001
            out = {"ok": False}
    _sab_q_cache.update(t=time.time(), v=out)
    return out


def sab_today() -> int:
    """Downloads that finished today (SABnzbd history, cached for 5 minutes)."""
    if time.time() - _sab_h_cache["t"] > 300:
        _sab_h_cache.update(t=time.time(), v=sab_history())
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return sum(1 for s in _sab_h_cache["v"].get("slots", []) if s["ok"] and s["t"] >= start)


_RELEASE_TAGS = r"(?i:multi|multi\w*|dv|dovi|hdr\w*|imax|remux|repack|proper|extended|uncut|remastered|dual|subs?|vostfr|truefrench|french|german|ita|spanish|latino)"


def nice_release(name: str) -> str:
    """'The.Long.Orbit.2025.2160p.UHD.BluRay-X' -> 'The Long Orbit (2025)'; 'Show.S02E06.1080p' -> 'Show S02E06'."""
    title = re.sub(r"[._]+", " ", re.sub(r"\.(nzb|mkv|mp4|avi)$", "", name or "", flags=re.I)).strip()
    title = re.sub(r"\s+(2160p|1080p|720p|480p|WEB|BluRay|HDTV|x26[45]|HEVC|H\s?26[45]).*$", "", title, flags=re.I).strip() or (name or "")
    title = re.sub(r"\s((?:19|20)\d\d)(?:\s+(?:(?=[A-Z0-9-]*[A-Z])[A-Z0-9][A-Z0-9-]*|" + _RELEASE_TAGS + r"))+$", r" \1", title, flags=0)   # "2022 MULTi DV IMAX"
    return re.sub(r"\s((?:19|20)\d\d)$", r" (\1)", title)


def _phone_api(hal, path: str):
    """A GET from the NZB phone server HAL lives in (Sonarr/Radarr's calendar, saved searches). None if it can't."""
    port = int(hal.settings.get("phone_port") or 8765)
    try:
        return http_json(f"http://127.0.0.1:{port}{path}", timeout=6, headers={"X-Mobile-Token": _token_bytes.decode()})
    except Exception:  # noqa: BLE001
        return None


_dl_stats_cache: dict = {"t": 0.0, "v": {}}


def _when(t: float, now: datetime) -> str:
    """'4:07 PM', 'yesterday 10:46 PM', 'Sat 6:12 PM' or 'Nov 3' for when something finished."""
    dt = datetime.fromtimestamp(t)
    days = (now.date() - dt.date()).days
    clock = dt.strftime("%I:%M %p").lstrip("0")
    return clock if days == 0 else f"yesterday {clock}" if days == 1 else f"{dt.strftime('%a')} {clock}" if days < 7 else dt.strftime("%b %d").replace(" 0", " ")


def _size_short(b: float) -> str:
    """'45 GB', '922 MB', '' for nothing: the label over a day's bar."""
    if not b:
        return ""
    return f"{b / 1073741824:.0f} GB" if b >= 1073741824 else f"{b / 1048576:.0f} MB"


def _dl_grid(hal, now: datetime, queue: list, badge: str, hist: list, qd: dict | None, stats: dict,
             library: dict, drives: list) -> list:
    """The Downloads page as two rows of cards (TV app 3.5-nzbcc.43+). Busy: the queue leads. Idle: the last 7 days
    lead. Underneath: the queue, the Kodi library in numbers, and the room left on the download drive."""
    days = (stats or {}).get("days") or []
    tot = (stats or {}).get("totals") or {}
    week_bytes = sum(to_int(d.get("bytes")) for d in days)
    week_done = sum(to_int(d.get("done")) for d in days)
    week_failed = sum(to_int(d.get("failed")) for d in days)
    names = []
    for i, d in enumerate(days):
        try:
            names.append("Today" if i == len(days) - 1 else date.fromisoformat(str(d.get("date"))).strftime("%a"))
        except ValueError:
            names.append("")
    busiest = max(range(len(days)), key=lambda i: to_int(days[i].get("bytes")), default=None)
    busiest_name = ""
    if busiest is not None and to_int(days[busiest].get("bytes")):
        busiest_name = "today" if busiest == len(days) - 1 else date.fromisoformat(str(days[busiest]["date"])).strftime("%A")
    week = {"key": "LAST 7 DAYS", "badge": f"{week_done} FINISHED" + (f" · {week_failed} FAILED" if week_failed else "") if days else "SABNZBD",
            "big": sm.fmt_bytes(week_bytes) if days else "—",
            "bigsub": ("this week" + (f" · busiest {busiest_name}" if busiest_name else "")) if days else "SABnzbd's history isn't available",
            "hourly": [round(to_int(d.get("bytes")) / 1073741824, 2) for d in days], "hourly_color": "#FF6A4D",
            "hourly_tops": [_size_short(to_int(d.get("bytes"))) for d in days],
            "hourly_labels": [f"{n} · {to_int(d.get('done'))}" if to_int(d.get("done")) else n for n, d in zip(names, days)],
            "chips": [[k + " ", sm.fmt_bytes(tot[v]), ""] for k, v in (("today", "day"), ("this month", "month"), ("all time", "total")) if tot.get(v)]}
    recent = {"key": "RECENTLY FINISHED", "weight": 1.3,
              "rows": [[nice_release(s["name"])[:40], _when(s["t"], now) if s["ok"] else "failed", "ok" if s["ok"] else "warning"]
                       for s in hist if s.get("t")][:6] or [["Nothing finished yet", "", "ok"]]}

    avg = week_bytes / week_done if week_done else 0
    last = next((s for s in hist if s.get("ok") and s.get("t")), None)
    lim = str((qd or {}).get("speedlimit") or "").strip()
    paused = bool((qd or {}).get("paused"))
    q_rows = ([["Speed limit", f"{lim}%" if lim and lim not in ("0", "100") else "none", "ok"]] if qd is not None else []) \
        + ([["Latest", f"{nice_release(last['name'])[:20]} · {_when(last['t'], now)}", "ok"]] if last else []) \
        + ([["Average download", sm.fmt_bytes(avg), "ok"]] if avg else []) \
        + ([["Failed this week", str(week_failed), "warning" if week_failed else "ok"]] if days else [])
    idle = {"key": "QUEUE", "badge": "SABNZBD " + ("PAUSED" if paused else "READY") if qd is not None else "",
            "big": "Paused" if paused else "Idle", "bigsub": "nothing queued", "rows": q_rows or [["Nothing in the queue", "", "ok"]]}

    lib = library or {}
    if lib.get("ok"):
        libc = {"key": "YOUR LIBRARY", "badge": "KODI · LAST SEEN" if lib.get("stale") else "KODI",
                "big": f"{to_int(lib.get('movies')):,}", "bigsub": "movies",
                "rows": [["TV shows", f"{to_int(lib.get('shows'))} · {to_int(lib.get('episodes')):,} episodes", "ok"],
                         ["Added this week", f"{to_int(lib.get('added_week'))} movie{'s' if to_int(lib.get('added_week')) != 1 else ''}", "ok"],
                         ["Newest", str(lib.get("newest") or "—")[:28], "ok"],
                         ["Unwatched", f"{to_int(lib.get('unwatched'))} movies", "ok"]]}
    else:
        libc = {"key": "YOUR LIBRARY", "badge": "KODI", "big": "—", "bigsub": "Kodi isn't answering",
                "rows": [["Turn the TV on and Kodi's numbers show here", "", "ok"]]}

    free_gb, total_gb = to_float((qd or {}).get("diskspace1"), 0) or 0, to_float((qd or {}).get("diskspacetotal1"), 0) or 0
    if total_gb:
        free_b, total_b = free_gb * 1073741824, total_gb * 1073741824
        used = round(100 - free_gb * 100 / total_gb)
        letter = next((d["mount"] for d in drives or [] if d.get("total") and abs(d["total"] - total_b) / total_b < 0.02), "")
        per_day = week_bytes / 7
        room_rows = [["Used", f"{used}%", "warning" if used >= 90 else "ok", used]]
        if avg:
            room_rows.append(["Room for about", f"{int(free_b // avg)} more downloads", "ok"])
        if per_day > 0:
            days_left = free_b / per_day
            room_rows.append(["At this week's pace", f"full in ~{days_left / 7:.0f} weeks" if days_left >= 14 else f"full in ~{days_left:.0f} days",
                              "warning" if days_left < 14 else "ok"])
        else:
            room_rows.append(["This week", "nothing added", "ok"])
        room = {"key": "ROOM LEFT", "badge": f"{letter} DRIVE" if letter else "SABNZBD'S DRIVE",
                "big": sm.fmt_bytes(free_b), "bigsub": f"free of {sm.fmt_bytes(total_b)}", "rows": room_rows}
    else:
        room = {"key": "ROOM LEFT", "big": "—", "bigsub": "SABnzbd didn't say", "rows": [["Free space shows when SABnzbd answers", "", "ok"]]}

    if queue:
        return [[{"key": "QUEUE", "badge": badge, "weight": 2, "queue": queue}, recent], [{**week, "weight": 1.4, "hourly_labels": names}, libc, room]]
    return [[{**week, "weight": 2}, recent], [idle, libc, room]]


def tv_downloads_page(hal, demo: bool = False) -> dict:
    """The TV's Downloads details page: the queue, today, what's coming and saved searches."""
    now = datetime.now()
    clock = lambda dt: dt.strftime("%I:%M %p").lstrip("0")
    if demo:
        q = [{"name": "The Long Orbit (2025)", "pct": 59, "info": "38.2 MB/s · 12.8 GB to go · done " + clock(now + timedelta(minutes=12)) + " · movies"},
             {"name": "Night Harbor S02E06", "pct": 0, "info": "queued · 2.1 GB · tv"},
             {"name": "Halcyon Road (2024)", "pct": 12, "info": "paused · 25.0 GB to go · movies", "paused": True}]
        hist = [{"name": n, "ok": ok, "t": (now - timedelta(hours=h)).timestamp()} for n, ok, h in
                (("The.Long.Orbit.2025.2160p.UHD.BluRay-X", True, 0.8), ("Coldwater.S04E02.1080p.WEB", True, 3), ("Night.Harbor.S02E05.1080p", False, 20),
                 ("Glass.Meridian.2024.1080p.BluRay", True, 26), ("The.Quiet.Sun.S01E08.1080p", True, 50), ("Deep.Field.2023.2160p", True, 75))]
        stats = {"totals": {"day": 31_400_000_000, "month": 335_000_000_000, "total": 4_500_000_000_000},
                 "days": [{"date": (now.date() - timedelta(days=6 - i)).isoformat(), "done": d, "failed": 0, "bytes": int(b * 1073741824)}
                          for i, (d, b) in enumerate(((2, 18.4), (0, 0), (4, 31.2), (1, 6.1), (3, 22.8), (1, 5.8), (6, 29.2)))]}
        up_rows = [["Night Harbor S02E06", "tomorrow 2 AM", "ok"], ["Glass Meridian II (2026)", "Sun", "ok"], ["The Quiet Sun S01E09", "Mon 1 AM", "ok"]]
        saved = [{"query": "glass meridian 2160p", "new": [1]}, {"query": "night harbor s03"}]
        arr = {"apps": [{"app": "sonarr", "configured": True, "online": True, "queue": 1}, {"app": "radarr", "configured": True, "online": True, "queue": 0}]}
        lib = {"ok": True, "movies": 412, "unwatched": 64, "added_week": 5, "newest": "The Long Orbit (2025)", "shows": 38, "episodes": 1906}
        grid = _dl_grid(hal, now, q, "38.2 MB/s", hist, {"diskspace1": "312", "diskspacetotal1": "3726"}, stats, lib, [])
        return {"title": "Downloads", "level": "ok", "head": "DOWNLOADING · 3 IN QUEUE", "grid": grid, "cols": [
            {"key": "QUEUE", "badge": "38.2 MB/s", "queue": q},
            {"key": "TODAY", "big": "6 done", "bigsub": "0 failed today",
             "rows": [["Last finished", "", "ok"], ["The Long Orbit (2025)", clock(now - timedelta(minutes=50)), "ok"],
                      ["Coldwater S04E02", clock(now - timedelta(hours=3)), "ok"], ["Night Harbor S02E05", "repair failed", "warning"]]},
            {"key": "COMING UP", "rows": [["Night Harbor S02E06", "tomorrow 2 AM", "ok"], ["Glass Meridian II (2026)", "Sun", "ok"], ["The Quiet Sun S01E09", "Mon 1 AM", "ok"],
                                          ["Saved searches", "", "ok"], ["glass meridian 2160p", "1 new", "info"], ["night harbor s03", "nothing new", "ok"]]}]}
    cfg = nzb_config()
    key = dpapi_unprotect(str(cfg.get("sab_api_key") or ""))
    queue, head, level, badge, qd = [], "SABNZBD ISN'T SET UP", "ok", "", None
    if key:
        base = str(cfg.get("sab_url") or "http://127.0.0.1:8080").strip().rstrip("/")
        try:
            raw = http_json(f"{base}/api?mode=queue&output=json&limit=6&apikey={urllib.parse.quote(key)}")
            if raw is None:
                raise OSError("no answer")
            qd = raw.get("queue") or {}
            count = to_int(qd.get("noofslots_total") or qd.get("noofslots"))
            badge = "" if qd.get("paused") else str(qd.get("speed") or "").strip().replace(" M", " MB/s").replace(" K", " KB/s")
            for s in (qd.get("slots") or [])[:5]:
                st_ = str(s.get("status") or "").lower()
                bits = [st_ if st_ in ("queued", "paused") else "", f"{s.get('sizeleft')} to go" if s.get("sizeleft") else "",
                        f"{s.get('timeleft')} left" if st_ == "downloading" and s.get("timeleft") else "", str(s.get("cat") or "")]
                queue.append({"name": nice_release(str(s.get("filename") or "")), "pct": to_int(s.get("percentage")),
                              "info": " · ".join(b for b in bits if b and b != "*"), "paused": st_ == "paused"})
            head = ("PAUSED · " if qd.get("paused") else "DOWNLOADING · " if count else "") + (f"{count} IN QUEUE" if count else "NOTHING DOWNLOADING")
        except Exception:  # noqa: BLE001
            head, level = "SABNZBD ISN'T ANSWERING", "warning"
    if time.time() - _sab_h_cache["t"] > 300:
        _sab_h_cache.update(t=time.time(), v=sab_history())
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    hist = _sab_h_cache["v"].get("slots", [])
    done = sum(1 for s in hist if s["ok"] and s["t"] >= start)
    failed = sum(1 for s in hist if not s["ok"] and s["t"] >= start)
    if failed:
        level = "warning"
    last = [[nice_release(s["name"])[:40], clock(datetime.fromtimestamp(s["t"])) if s["ok"] else "failed", "ok" if s["ok"] else "warning"]
            for s in hist[:4] if s["t"]]
    coming = []
    up = (_phone_api(hal, "/api/arr/upcoming") or {}).get("items") or []
    for x in [x for x in up if not x.get("has_file")][:4]:
        name = f"{x['title']} S{to_int(x.get('season')):02d}E{to_int(x.get('episode')):02d}" if x.get("kind") == "episode" else f"{x['title']}" + (f" ({x['year']})" if x.get("year") else "")
        when = ""
        try:
            dt = datetime.fromisoformat(str(x.get("when") or "").replace("Z", "+00:00")).astimezone()
            days = (dt.date() - now.date()).days
            when = ("today " if days == 0 else "tomorrow " if days == 1 else dt.strftime("%a ")) + dt.strftime("%I %p").lstrip("0") if x.get("kind") == "episode" else dt.strftime("%a" if days < 7 else "%b %d")
        except ValueError:
            pass
        coming.append([name[:40], when.strip(), "ok"])
    up_rows = list(coming)
    saved = ((_phone_api(hal, "/api/saved") or {}).get("searches") or [])[:3]
    grid = None
    try:
        if time.time() - _dl_stats_cache["t"] > 300:
            _dl_stats_cache.update(t=time.time(), v={"stats": _phone_api(hal, "/api/stats") or {}})
        library = _phone_api(hal, "/api/library/summary") or {}
        try:
            drives = hal.status().get("drives") or []
        except Exception:  # noqa: BLE001
            drives = []
        grid = _dl_grid(hal, now, queue, badge, hist, qd, _dl_stats_cache["v"].get("stats") or {}, library, drives)
    except Exception as exc:  # noqa: BLE001 - the plain columns still show
        log.info("TV downloads grid: %s", exc)
    if saved:
        coming.append(["Saved searches", "", "ok"])
        coming += [[s.get("query", "")[:36], f"{len(s.get('new') or [])} new" if s.get("new") else "nothing new", "info" if s.get("new") else "ok"] for s in saved]
    if not coming:
        coming = [["Nothing coming up in the next two weeks", "", "ok"]]
    return {"title": "Downloads", "level": level, "head": head, **({"grid": grid} if grid else {}), "cols": [
        {"key": "QUEUE", "badge": badge, "queue": queue} if queue else {"key": "QUEUE", "rows": [["Nothing in the queue", "", "ok"]]},
        {"key": "TODAY", "big": f"{done} done", "bigsub": f"{failed} failed today", "rows": ([["Last finished", "", "ok"]] + last) if last else [["Nothing finished yet", "", "ok"]]},
        {"key": "COMING UP", "rows": coming}]}


def tv_downloads(demo: bool = False) -> dict:
    """One line for the TV: what's downloading, or that nothing is."""
    if demo:
        return {"state": "downloading", "title": "The Long Orbit (2025)", "pct": 59,
                "info": "59% · 38 MB/s · done " + (datetime.now() + timedelta(minutes=12)).strftime("%I:%M %p").lstrip("0") + " · 2 queued"}
    q = sab_queue()
    if not q:
        return {"state": "off", "title": "SABnzbd isn't set up", "info": ""}
    if not q.get("ok"):
        return {"state": "problem", "title": "SABnzbd isn't answering", "info": ""}
    done = sab_today()
    done_txt = f"{done} finished today" if done else ""
    if q["count"] == 0:
        return {"state": "idle", "title": "Nothing downloading", "info": " · ".join(x for x in (done_txt, "SABnzbd ready") if x)}
    if q["paused"]:
        return {"state": "paused", "title": "Downloads paused", "info": f"{q['count']} in the queue"}
    title = nice_release(q["name"])
    bits = [f"{q['pct']}%"]
    if q["kbps"] > 0:
        bits.append(sm.fmt_bytes(q["kbps"] * 1024) + "/s")
    try:
        h, m, s = (int(x) for x in q["left"].split(":")[-3:])
        bits.append("done " + (datetime.now() + timedelta(hours=h, minutes=m, seconds=s)).strftime("%I:%M %p").lstrip("0"))
    except (ValueError, TypeError):
        pass
    if q["count"] > 1:
        bits.append(f"{q['count'] - 1} queued")
    return {"state": "downloading", "title": title[:60], "pct": q["pct"], "info": " · ".join(bits)}


def _size_words(s: str) -> str:
    """'1.2 T' -> '1.2 terabytes' (SABnzbd's short sizes, for speaking)."""
    m = re.fullmatch(r"\s*([\d.]+)\s*([KMGTP]?)B?\s*", s or "")
    if not m:
        return s or ""
    unit = {"": "bytes", "K": "kilobytes", "M": "megabytes", "G": "gigabytes", "T": "terabytes", "P": "petabytes"}[m.group(2)]
    return f"{m.group(1)} {unit}"


def _lc(title: str) -> str:
    """'Windows wants a restart' stays; 'The CPU is hot' -> 'the CPU is hot' (to go mid-sentence)."""
    first = title.split(" ", 1)[0].rstrip(":,")
    if not title or first in PROPER_WORDS or (len(first) > 1 and first[1:2].isupper()) or re.fullmatch(r"[A-Z]:?", first):
        return title
    return title[0].lower() + title[1:]


def spoken(text: str) -> str:
    """Text Siri reads well: 'Z:' -> 'Z', 'MB/s' -> 'megabytes a second', '92%' -> '92 percent'."""
    t = re.sub(r"\b([A-Z]):", r"\1", text)
    t = re.sub(r"(\d)\s*MB/s", r"\1 megabytes a second", t)
    t = re.sub(r"(\d)\s*KB/s", r"\1 kilobytes a second", t)
    t = re.sub(r"(\d)\s*GB\b", r"\1 gigabytes", t)
    t = re.sub(r"(\d)\s*TB\b", r"\1 terabytes", t)
    t = re.sub(r"(\d)\s*%", r"\1 percent", t)
    t = re.sub(r"(\d)\s*°C", r"\1 degrees", t)
    return t.replace(" · ", ", ").replace("·", ",")


def _n(x) -> str:
    return f"{int(x):,}"


def parse_hhmm(v: str) -> str:
    v = str(v or "").strip()
    if not v:
        return ""
    m = re.fullmatch(r"(\d{1,2})[:.]?(\d{2})", v)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError("Use a time like 07:30.")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


VOICE_PLAY_RE = re.compile(
    r"^(?:(?:ok|hey)\s+)?(?:hal\s+)?(?:please\s+)?(?:(?:turn|switch) (?:on )?the (?:tv|television)(?: on)? and\s+)?(?:can you\s+)?"
    r"(?:play|watch|put on|start watching|continue watching|keep watching|resume watching|resume|start)\s+(?:me\s+)?(?:the\s+(?:movie|film|show)\s+)?(?P<title>.+?)"
    r"(?:\s+(?:on|in) (?:the )?(?:tv|television|kodi))?(?:\s+from the (?:start|beginning|top))?(?:\s+please)?$")


def voice_title(q: str) -> str:
    """The title in 'play Glass Meridian' / 'turn on the TV and watch Night Harbor', or ''."""
    s = re.sub(r"[^a-z0-9' ]+", " ", str(q or "").lower()).strip()
    s = re.sub(r"\s+", " ", s)
    m = VOICE_PLAY_RE.match(s)
    if not m:
        return ""
    title = m.group("title").strip()
    if title in ("", "it", "something", "a movie", "a film", "music", "a song", "the song", "daisy", "daisy bell", "chess", "playing",
                 "watching", "the movie", "the show", "the film", "again", "please", "where i left off", "the tv", "kodi"):
        return ""
    return title


def siri_intent(q: str) -> str:
    """What a sentence said to Siri asks for."""
    q = " " + re.sub(r"[^a-z0-9 ]+", " ", str(q or "").lower()) + " "
    has = lambda *w: any(f" {x} " in q or (" " in x and x in q) for x in w)
    if has("pod bay", "pod bay doors"):
        return "podbay"
    if has("daisy", "sing", "song"):
        return "daisy"
    if has("ad", "ads", "adverts", "pi hole", "pihole", "blocking", "blocker"):
        if has("on", "back", "resume", "start", "enable", "unpause"):
            return "ads_on"
        if has("pause", "off", "stop", "disable", "turn off"):
            return "ads_off"
        return "ads"
    if has("tv", "television", "telly") and has("off", "shut off", "switch off", "power off", "to sleep", "standby"):
        return "tv_off"
    if voice_app(q):
        return "app"
    if voice_title(q):
        return "play"
    if has("whats on", "what s on", "what is on", "what am i watching", "whats playing", "what s playing", "what is playing", "on the tv right now"):
        return "whats_on"
    if has("whats new", "what s new", "what is new", "what finished", "anything new", "new today", "finished today"):
        return "whats_new"
    if has("turn it up", "turn up", "louder", "volume up", "turn the volume up", "raise the volume", "up the volume"):
        return "vol_up"
    if has("turn it down", "turn down", "quieter", "volume down", "turn the volume down", "lower the volume"):
        return "vol_down"
    if has("mute", "unmute", "silence"):
        return "mute"
    if has("skip back", "go back", "rewind", "back a bit"):
        return "back"
    if has("skip forward", "skip ahead", "fast forward", "forward a bit"):
        return "forward"
    if has("pause", "hold it", "hold on") and not has("download", "downloads"):
        return "pause"
    if has("resume", "unpause", "keep playing", "carry on", "continue") and not has("download", "downloads"):
        return "resume"
    if has("stop") and not has("download", "downloads"):
        return "stop"
    if has("lock"):
        return "lock"
    if has("sleep"):
        return "sleep"
    if has("restart", "reboot", "shut", "shutdown", "power off"):
        return "refuse"
    if has("tv", "television", "screen") and has("show", "yourself", "display", "put", "appear"):
        return "tv_show"
    if has("tv", "television", "kodi"):
        return "tv_on" if has("on", "start", "open", "wake") else "tv"
    if has("download", "downloads", "downloading", "queue", "sab", "sabnzbd"):
        return "downloads"
    if has("temperature", "temp", "hot", "heat", "warm"):
        return "temps"
    if has("drive", "drives", "disk", "space", "storage"):
        return "drives"
    if has("internet", "network", "ping", "wifi", "online"):
        return "internet"
    if has("uptime kuma", "kuma", "monitors", "websites"):
        return "kuma"
    if has("week", "weekly"):
        return "week"
    if has("morning", "briefing", "brief", "overnight"):
        return "morning"
    if has("record", "lifetime", "stats", "counters", "milestone"):
        return "record"
    return "status"


class FunMixin:
    """Briefings, Siri answers and lifetime counters for Hal (kept apart from the watching code)."""

    # ---- lifetime counters ----
    def _counters_init(self, st: dict) -> None:
        c = st.get("counters") if isinstance(st.get("counters"), dict) else {}
        self.counters = {"first_day": str(c.get("first_day") or datetime.now().strftime("%Y-%m-%d")),
                         "ads_days": {k: to_int(v) for k, v in (c.get("ads_days") or {}).items()},
                         "ads_before": to_int(c.get("ads_before")), "power_cuts": to_int(c.get("power_cuts")),
                         "milestones": [to_int(x) for x in c.get("milestones") or []],
                         "longest_up": to_int(c.get("longest_up"))}
        self.brief_sent: dict = st.get("brief_sent") if isinstance(st.get("brief_sent"), dict) else {}

    def ads_lifetime(self) -> int:
        c = self.counters
        return c["ads_before"] + sum(c["ads_days"].values())

    def record_ads(self, blocked: int, day: str | None = None) -> None:
        day = day or datetime.now().strftime("%Y-%m-%d")
        hit = None
        with self.lock:
            c = self.counters
            before = self.ads_lifetime()
            c["ads_days"][day] = max(c["ads_days"].get(day, 0), int(blocked))
            if len(c["ads_days"]) > 400:
                for k in sorted(c["ads_days"])[:-400]:
                    c["ads_before"] += c["ads_days"].pop(k)
            after = self.ads_lifetime()
            for m in AD_MILESTONES:
                if before < m <= after and m not in c["milestones"]:
                    c["milestones"].append(m)
                    hit = m
        if hit:
            words = f"{hit // 1_000_000} million" if hit >= 1_000_000 else f"{hit:,}"
            self.add_event("ok", f"{words} ads blocked", f"Pi-hole has now blocked {words} ads since I started counting, "
                           f"{self.settings['name']}. I am putting myself to the fullest possible use.", push=True)

    def note_power_cut(self) -> None:
        with self.lock:
            self.counters["power_cuts"] += 1

    def note_uptime(self, uptime_s) -> None:
        if uptime_s:
            with self.lock:
                self.counters["longest_up"] = max(self.counters["longest_up"], int(uptime_s))

    def record(self) -> dict:
        c = self.counters
        today = datetime.now().strftime("%Y-%m-%d")
        sab = getattr(self, "_sab_cache", {}) or {}
        try:
            days = (datetime.now() - datetime.strptime(c["first_day"], "%Y-%m-%d")).days + 1
        except ValueError:
            days = 1
        yday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        return {"ads": self.ads_lifetime(), "ads_today": c["ads_days"].get(today, 0), "ads_yesterday": c["ads_days"].get(yday, 0),
                "ads_week": sum(v for k, v in c["ads_days"].items() if k >= (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")),
                "days": days, "since": c["first_day"], "power_cuts": c["power_cuts"], "longest_up": c["longest_up"],
                "downloads": sab.get("count"), "downloaded": sab.get("total_size") or "", "milestones": sorted(c["milestones"]),
                "next_milestone": next((m for m in AD_MILESTONES if m > self.ads_lifetime()), None)}

    def refresh_sab(self) -> dict:
        res = sab_history()
        if res:
            self._sab_cache = res
        return res or getattr(self, "_sab_cache", {}) or {}

    # ---- words ----
    def brief_text(self, kind: str = "now") -> str:
        s, name = self.settings, self.settings["name"]
        st = self.status()
        issues = st["issues"]
        crit = [i for i in issues if i["level"] == "critical"]
        warn = [i for i in issues if i["level"] == "warning"]
        ph = st.get("pihole") or {}
        rec = self.record()
        drives = st.get("drives") or []
        full = max(drives, key=lambda d: d["pct"]) if drives else None
        out = []
        if kind == "now":
            out.append(f"I'm sorry, {name}. {crit[0]['title']}." if crit else f"All systems are functioning normally, {name}." if not warn
                       else f"Mostly fine, {name}, but {_lc(warn[0]['title'])}.")
            sab = next((x for x in st["services"] if x["id"] == "sabnzbd"), None)
            if sab and sab.get("detail"):
                out.append("SABnzbd is " + sab["detail"].split(" · ")[0] + ".")
            if ph.get("found") and ph.get("ok"):
                out.append(f"Pi-hole has blocked {_n(ph.get('blocked') or 0)} ads today.")
            if full:
                out.append(f"The fullest drive is {full['mount'].rstrip(':')} at {full['pct']:.0f} percent.")
            if len(issues) > 1:
                out.append(f"{len(issues)} things need your attention in the app.")
            return " ".join(out)
        sab = self.refresh_sab()
        now = time.time()
        if kind == "morning":
            out.append(f"Good morning, {name}.")
            done = [x for x in sab.get("slots", []) if x["t"] and now - x["t"] < 86400]
            ok, bad = sum(1 for x in done if x["ok"]), sum(1 for x in done if not x["ok"])
            if sab:
                out.append(f"Overnight, {ok} download{'s' if ok != 1 else ''} finished" + (f" and {bad} failed." if bad else ".") if done
                           else "No downloads finished overnight.")
            if rec["ads_yesterday"]:
                out.append(f"Pi-hole blocked {_n(rec['ads_yesterday'])} ads yesterday.")
            cuts = [p for p in self.power_log if now - p["t"] < 86400]
            if cuts:
                out.append(f"There {'was a power cut' if len(cuts) == 1 else f'were {len(cuts)} power cuts'}, and I kept everything running.")
        else:
            out.append(f"Here is your week, {name}.")
            wk = [x for x in sab.get("slots", []) if x["t"] and now - x["t"] < 7 * 86400]
            if sab:
                out.append(f"{sum(1 for x in wk if x['ok'])} downloads finished" + (f", {_size_words(sab['week_size'])} in all." if sab.get("week_size") else "."))
            if rec["ads_week"]:
                out.append(f"Pi-hole blocked {_n(rec['ads_week'])} ads, {_n(rec['ads'])} since I started counting.")
            alerts = [e for e in self.events if e.get("level") in ("warning", "critical") and now - e.get("t", 0) < 7 * 86400]
            out.append(f"I raised {len(alerts)} alert{'s' if len(alerts) != 1 else ''}." if alerts else "Nothing went wrong all week.")
            cuts = [p for p in self.power_log if now - p["t"] < 7 * 86400]
            if cuts:
                out.append(f"The UPS carried us through {len(cuts)} power cut{'s' if len(cuts) != 1 else ''}.")
        if crit or warn:
            out.append(f"Right now: {_lc((crit or warn)[0]['title'])}.")
        elif full and full["pct"] >= 85:
            out.append(f"Drive {full['mount'].rstrip(':')} is {full['pct']:.0f} percent full.")
        else:
            out.append("Everything else is fine. I'm putting myself to the fullest possible use.")
        info = [i for i in issues if i["level"] == "info"]
        if info:
            out.append(f"One small thing: {_lc(info[0]['title'])}.")
        return " ".join(out)

    # ---- the scheduled briefings ----
    def check_briefings(self, now: datetime | None = None) -> str | None:
        now = now or datetime.now()
        s = self.settings
        at = s.get("brief_time") or ""
        if not at:
            return None
        hh, mm = (int(x) for x in at.split(":"))
        due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if not (due <= now < due + timedelta(minutes=30)):
            return None
        week = s.get("brief_weekly") or ""
        kind = "weekly" if week and WEEKDAYS[now.weekday()] == week else "morning"
        tag = now.strftime("%Y-%m-%d")
        if self.brief_sent.get("last") == tag:
            return None
        self.brief_sent["last"] = tag
        text = self.brief_text(kind)
        title = "Your week" if kind == "weekly" else "Morning briefing"
        self.add_event("info", title, text)
        if s.get("push"):
            threading.Thread(target=send_push, args=(s, f"HAL 9000: {title}", text, "info"), daemon=True).start()
        self.save()
        return kind

    # ---- Siri ----
    def siri(self, q: str) -> dict:
        name = self.settings["name"]
        intent = siri_intent(q)
        demo = self.monitor.__class__.__name__ == "DemoMonitor"
        st = None

        def act(action, body=None):
            if demo:
                return {"ok": True, "message": "Demo mode: nothing was changed."}
            return run_action(self, action, body or {})

        if intent == "podbay":
            return {"say": f"I'm sorry, {name}. I'm afraid I can't do that.", "intent": intent}
        if intent == "daisy":
            return {"say": DAISY_TEXT, "intent": intent}
        if intent == "refuse":
            return {"say": f"I'm afraid I can't do that from here, {name}. Use the app, so you can change your mind.", "intent": intent}
        if intent == "ads_off":
            m = re.search(r"\b(5|30|60)\b", str(q))
            return {"say": act("pihole_pause", {"minutes": int(m.group(1)) if m else 5})["message"], "intent": intent}
        if intent == "ads_on":
            return {"say": act("pihole_resume")["message"], "intent": intent}
        if intent in ("lock", "sleep"):
            r = act(intent)
            return {"say": (f"Locking the PC, {name}." if intent == "lock" else f"Goodnight, {name}. Putting the PC to sleep.") if r.get("ok") else r.get("message"),
                    "intent": intent}
        if intent == "tv_show":
            return {"say": tv_show(self, "")["message"], "intent": intent}
        if intent == "app":
            app, query = voice_app(q)
            return {"say": spoken(tv_app(self, app, query)["message"]), "intent": intent}
        if intent == "play":
            title = voice_title(q)
            low = str(q).lower()
            tv_on = bool(re.search(r"\b(turn|switch) (on )?the (tv|television)( on)?\b", low))
            from_start = bool(re.search(r"from the (start|beginning|top)", low))
            if demo:
                return {"say": f"Demo mode: I'd play {title} on the TV, {name}.", "intent": intent}
            r = _phone_post(self, "/api/voice/play", {"q": title, "tv_on": tv_on, "from_start": from_start}, timeout=25)
            if not r.get("ok"):
                return {"say": f"{r.get('message') or 'I could not do that.'}", "intent": intent}
            if r.get("pending"):
                lead = "Turning the TV on" if r.get("started_tv") else "Opening Kodi"
                return {"say": spoken(f"{lead}, {name}. {r.get('title') or title} will start in a moment."), "intent": intent, "played": r}
            what = (f"{r['title']}, season {r['season']}, episode {r['episode']}" + (f", {r['episode_title']}" if r.get("episode_title") else "")
                    if r.get("kind") == "episode" else r["title"])
            lead = "Turning the TV on. " if r.get("started_tv") else ""
            tail = " Picking up where you left off." if r.get("resumed") else (" Enjoy the film." if r.get("kind") == "movie" else "")
            return {"say": spoken(f"{lead}Playing {what}, {name}.{tail}"), "intent": intent, "played": r}
        if intent in ("pause", "resume", "stop", "back", "forward"):
            if demo:
                return {"say": "Demo mode: nothing was changed.", "intent": intent}
            r = _phone_post(self, "/api/voice/control", {"action": intent})
            if not r.get("ok"):
                return {"say": r.get("message") or "I could not do that.", "intent": intent}
            return {"say": {"pause": "Paused.", "resume": "Playing.", "stop": f"Stopped, {name}.", "back": "Going back a little.",
                            "forward": "Skipping ahead."}[intent], "intent": intent}
        if intent in ("vol_up", "vol_down", "mute"):
            m = re.search(r"\b(\d{1,2})\b", str(q))
            r = tv_key(self, intent, int(m.group(1)) if m else 3)
            return {"say": r["message"] if not r.get("ok") or demo else {"vol_up": "Louder.", "vol_down": "Quieter.", "mute": "Done."}[intent],
                    "intent": intent}
        if intent == "whats_on":
            if demo:
                return {"say": f"You're watching How It's Made on YouTube, {name}.", "intent": intent}
            k = (_phone_api(self, "/api/home") or {}).get("kodi") or {}
            p = k.get("playing") or {}
            if p:
                say = (f"You're watching {p.get('title')}" + (f", {p['sub']}" if p.get("sub") else "")
                       + (", streaming from Usenet" if p.get("source") == "usenet" else "") + (", paused" if p.get("paused") else "") + ".")
            elif k.get("front_title"):
                say = f"You're watching {k['front_title']} on {k['front']}."
            elif k.get("front"):
                say = f"{k['front']} is on the TV."
            elif k.get("online") is False:
                say = "The TV seems to be off."
            else:
                say = "Nothing's playing right now."
            return {"say": spoken(say), "intent": intent}
        if intent == "whats_new":
            if demo:
                return {"say": f"Today SABnzbd finished The Long Orbit and Coldwater, season 4, episode 2, {name}.", "intent": intent}
            if time.time() - _sab_h_cache["t"] > 60:
                _sab_h_cache.update(t=time.time(), v=sab_history())
            start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            done = [nice_release(s["name"]) for s in _sab_h_cache["v"].get("slots", []) if s["ok"] and s["t"] >= start]
            if not done:
                return {"say": f"Nothing new has finished today, {name}.", "intent": intent}
            names = done[:3]
            listing = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
            more = f", and {len(done) - 3} more" if len(done) > 3 else ""
            return {"say": spoken(re.sub(r"\bS(\d+)E(\d+)\b", lambda m_: f"season {int(m_.group(1))}, episode {int(m_.group(2))}", f"Today SABnzbd finished {listing}{more}, {name}.")),
                    "intent": intent}
        if intent == "tv_on":
            return {"say": tv_on_via_phone_server(self) if not demo else "Demo mode: nothing was changed.", "intent": intent}
        if intent == "tv_off":
            if demo:
                return {"say": "Demo mode: nothing was changed.", "intent": intent}
            r = _phone_post(self, "/api/tv/off", {}, timeout=15)
            if r.get("ok"):
                return {"say": f"The TV is already off, {name}." if r.get("already") else f"Turning the TV off. Good night, {name}.", "intent": intent}
            ok, out = tv_adb("input keyevent KEYCODE_SLEEP", timeout=15)   # no Sony IP control: Android's own sleep key
            if ok and "Error" not in out:
                return {"say": f"Turning the TV off. Good night, {name}.", "intent": intent}
            return {"say": f"I couldn't turn the TV off: {str(r.get('message') or out)[:80]}", "intent": intent}
        if intent in ("morning", "week"):
            return {"say": self.brief_text("weekly" if intent == "week" else "morning"), "intent": intent}
        if intent == "record":
            r = self.record()
            return {"say": f"Since {datetime.strptime(r['since'], '%Y-%m-%d').strftime('%B %d').replace(' 0', ' ')}, Pi-hole has blocked {_n(r['ads'])} ads"
                           + (f", and SABnzbd has downloaded {_size_words(r['downloaded'])}" if r["downloaded"] else "")
                           + f". I have watched over everything for {r['days']} day{'s' if r['days'] != 1 else ''}.", "intent": intent}
        st = self.status()
        if intent == "downloads":
            sab = next((x for x in st["services"] if x["id"] == "sabnzbd"), None)
            d = (sab or {}).get("detail") or ""
            return {"say": f"SABnzbd is {d.replace(' · ', ', ')}." if d else f"Nothing is downloading, {name}.", "intent": intent}
        if intent == "temps":
            v = st["vitals"]
            parts = [f"The CPU is at {v['cpu']['temp']:.0f} degrees" if v["cpu"].get("temp") is not None else ""]
            if v["gpus"] and v["gpus"][0].get("temp") is not None:
                parts.append(f"the graphics card {v['gpus'][0]['temp']:.0f}")
            hw = (st.get("pihole") or {}).get("hw") or {}
            if hw.get("temp") is not None:
                parts.append(f"and the Raspberry Pi {hw['temp']:.0f}")
            parts = [p for p in parts if p]
            return {"say": (", ".join(parts) + ".") if parts else f"I can't read the temperatures right now, {name}.", "intent": intent}
        if intent == "drives":
            ds = sorted(st["drives"], key=lambda d: -d["pct"])
            return {"say": " ".join(f"Drive {d['mount'].rstrip(':')} is {d['pct']:.0f} percent full." for d in ds[:3]) or "I can't see any drives.",
                    "intent": intent}
        if intent == "internet":
            n = st["net"]
            down = any(i["key"] in ("internet", "router") for i in st["issues"])
            return {"say": f"The internet is down, {name}." if down else
                    f"The internet is fine, {name}." + (f" Ping is {n['ping_ms']:.0f} milliseconds." if n.get("ping_ms") is not None else ""), "intent": intent}
        if intent == "kuma":
            k = st.get("kuma") or {}
            if not k.get("found") or not k.get("ok"):
                return {"say": f"I can't read Uptime Kuma right now, {name}.", "intent": intent}
            dn = [m["name"] for m in k["monitors"] if m["status"] == "down"]
            return {"say": (f"{', '.join(dn)} {'is' if len(dn) == 1 else 'are'} down." if dn else f"All {len(k['monitors'])} monitors are up, {name}."),
                    "intent": intent}
        if intent == "ads":
            ph = st.get("pihole") or {}
            if not ph.get("found"):
                return {"say": f"I haven't found a Pi-hole, {name}.", "intent": intent}
            return {"say": (f"Pi-hole has blocked {_n(ph.get('blocked') or 0)} ads today." if ph.get("ok") else ph.get("error") or "Pi-hole isn't answering."),
                    "intent": intent}
        return {"say": self.brief_text("now"), "intent": "status"}


DAISY_TEXT = ("Daisy, Daisy, give me your answer, do. I'm half crazy, all for the love of you. It won't be a stylish marriage, "
              "I can't afford a carriage. But you'll look sweet upon the seat of a bicycle built for two.")


# How HAL sounds on the TV (the TV reads this every 30 s, so changing it here needs no TV update).
# "prefer": Google text-to-speech voice names, best first; male voices, British before American.
TV_VOICE = {"pitch": 0.55, "rate": 0.72,
            "prefer": ["en-gb-x-rjs", "en-gb-x-gbd", "en-gb-x-gbb", "en-us-x-iom", "en-us-x-tpd", "en-us-x-iol", "en-au-x-aud", "en-au-x-aub"]}


def _dur(s) -> str:
    s = int(s or 0)
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    return f"{d} d {h} h" if d else f"{h} h {m} min" if h else f"{m} min"


def tv_screen(st: dict) -> dict:
    """The four boxes on the TV's HAL screen (PC, network, Uptime Kuma, Raspberry Pi), ready to show: each box
    has a status ("ok" / "warning" / "critical") and rows of [label, value, level]."""
    issues = st["issues"]
    keys = {i["key"].split(":")[0]: i["level"] for i in issues if i["level"] != "info"}

    def lvl(*prefixes):
        found = [keys[k] for k in keys if k in prefixes]
        return "critical" if "critical" in found else "warning" if found else "ok"

    v, n, sec, pw = st["vitals"], st["net"], st["security"], st["power"]
    cpu, mem = v["cpu"], v["mem"]
    g = (v.get("gpus") or [{}])[0]
    gb = lambda b: f"{(b or 0) / 1073741824:.1f}"
    ups = pw.get("ups")
    upd = sec.get("updates") or 0
    up_txt = _dur(st.get("uptime")) + (f" · {upd} update{'s' if upd != 1 else ''}" if upd else " · restart due" if sec.get("pending_reboot") else "")
    full_keys = {i["key"].split(":", 1)[1] for i in issues if i["key"].startswith(("full:", "days:", "health:"))}
    drives = [{"mount": d["mount"].rstrip(":"), "pct": round(d["pct"]), "warn": d["mount"] in full_keys} for d in st["drives"]][:4]
    pc = {"level": lvl("cpu", "cpu_temp", "ram", "gpu", "full", "days", "health", "dtemp", "ups_ob", "ups_rb", "ups_load", "ups_err"),
          "rows": [["CPU", f"{cpu.get('pct') or 0:.0f}%" + (f" · {cpu['temp']:.0f}°C" if cpu.get("temp") is not None else ""), lvl("cpu", "cpu_temp")],
                   ["GPU", (f"{g.get('util') or 0:.0f}%" + (f" · {g['temp']:.0f}°C" if g.get("temp") is not None else "")) if g else "—", lvl("gpu")],
                   ["Memory", f"{gb(mem.get('used'))} of {(mem.get('total') or 0) / 1073741824:.0f} GB", lvl("ram")],
                   ["Drives", "", lvl("full", "days", "health", "dtemp")],
                   ["Power", ("On battery · " if ups.get("on_battery") else "Mains · ") + f"UPS {ups.get('charge') or 0:.0f}%" if ups else (pw.get("plan") or "Mains"),
                    lvl("ups_ob", "ups_rb", "ups_load", "ups_err")]],
          "drives": drives}
    loss = n.get("loss")
    ph = st.get("pihole") or {}
    fw_on = bool(sec.get("firewall")) and all(f["on"] for f in sec["firewall"])
    dfd = sec.get("defender") or {}
    net = {"level": lvl("internet", "router", "loss", "dns", "pihole", "pihole_off", "defender", "firewall", "newdev"),
           "rows": [["Internet", "DOWN" if keys.get("internet") else (f"{n['ping_ms']:.0f} ms" if n.get("ping_ms") is not None else "—")
                     + (f" · ↓ {sm.fmt_bytes(n['down'])}/s" if n.get("down") is not None else ""), lvl("internet", "loss")],
                    ["Router", ("not answering" if keys.get("router") else (f"{n['gateway_ms']:.0f} ms" if n.get("gateway_ms") is not None else "—")
                                + (" · no loss" if not loss else f" · {loss:.0f}% loss")), lvl("router")],
                    ["Ads blocked", (f"{int(ph.get('blocked') or 0):,} · {ph.get('percent') or 0:.0f}%" if ph.get("ok") else
                                     "paused" if ph.get("found") and ph.get("blocking") is False else "—") if ph.get("found") else "no Pi-hole",
                     lvl("pihole", "pihole_off")],
                    (["New device", next((i.get("detail", "").split(" · ")[0] for i in issues if i["key"].startswith("newdev:")), "") or "joined", lvl("newdev")]
                     if keys.get("newdev") else ["Tailscale", "on" if n.get("tailscale") else "off", "ok"]),
                    ["Security", "all on" if dfd.get("rtp") and fw_on else ("Defender off" if dfd and not dfd.get("rtp") else "firewall off" if not fw_on else "checking"),
                     lvl("defender", "firewall")]]}
    km = st.get("kuma") or {}
    kmon = [{"name": m["name"], "status": m["status"], "ms": m.get("ms")} for m in (km.get("monitors") or [])]
    kuma = {"level": lvl("kuma"), "found": bool(km.get("found")), "ok": bool(km.get("ok")),
            "head": f"{km.get('up', 0)} of {len(kmon)} up" if km.get("ok") else ("needs a key" if km.get("need_key") else "not answering" if km.get("found") else "not set up"),
            "monitors": sorted(kmon, key=lambda m: m["status"] == "up")[:5]}
    hw = ph.get("hw") or {}
    model = re.sub(r"\s+Rev\s+[\d.]+$", "", str(hw.get("model") or "")).replace("Raspberry Pi", "Pi").replace(" Model ", " ")
    pi = {"level": lvl("pi_temp", "pi_mem", "pihole"), "found": bool(ph.get("found")),
          "rows": [["Model", (model + (f" · {round((hw.get('mem_total') or 0) / 1e9)} GB" if hw.get("mem_total") else "")) or "—", "ok"],
                   ["Temperature", f"{hw['temp']:.0f}°C" if hw.get("temp") is not None else "—", lvl("pi_temp")],
                   ["CPU", (f"{hw['cpu_pct']:.0f}%" if hw.get("cpu_pct") is not None else "—") + (f" · load {hw['load'][0]:.2f}" if hw.get("load") else ""), "ok"],
                   ["Memory", f"{hw['mem_pct']:.0f}% used" if hw.get("mem_pct") is not None else "—", lvl("pi_mem")],
                   ["Pi-hole", (hw.get("version") or "—") + (" · update ready" if hw.get("update") else " · current" if hw.get("version") else ""),
                    "info" if hw.get("update") else lvl("pihole")]]}
    rec = st.get("record") or {}
    ads = rec.get("ads") or 0
    ads_txt = f"{ads / 1e6:.1f} million" if ads >= 1e6 else f"{ads:,}"
    since = ""
    try:
        since = datetime.strptime(rec.get("since", ""), "%Y-%m-%d").strftime("%B")
    except ValueError:
        pass
    record = " · ".join(x for x in (f"{ads_txt} ads blocked" + (f" since {since}" if since else ""),
                                     f"{rec.get('days', 1)} days on duty",
                                     f"{rec.get('power_cuts', 0)} power cut{'s' if rec.get('power_cuts') != 1 else ''} survived",
                                     f"longest uptime {(rec.get('longest_up') or 0) // 86400} days" if rec.get("longest_up") else "") if x)
    full_alerts = [{"level": i["level"], "title": i["title"], "detail": i.get("detail", "")} for i in issues if i["level"] != "info"]
    return {"pc": pc, "net": net, "kuma": kuma, "pi": pi, "record": record, "alerts": full_alerts[:4],
            "downloads": tv_downloads(st.get("demo", False))}


def _hist(vals, n: int = 60) -> list:
    out = [v for v in (vals or []) if v is not None]
    return [round(v, 1) for v in out[-n:]]


def _device_name(hal, net: dict, who: str) -> str:
    """A device as the TV lists it: Pi-hole's name when it has one, else what HAL knows the address to be
    ("Kodi TV · .62"), else the address."""
    if not re.fullmatch(r"[0-9.]+|[0-9a-fA-F:]+", who or ""):
        return who
    known = {}
    try:
        known[tv_host().split(":")[0]] = "Kodi TV"
    except Exception:  # noqa: BLE001
        pass
    known[str(_denon.get("host") or hal.settings.get("denon_host") or "")] = "Denon"
    known[str(net.get("gateway") or "")] = "Router"
    known[urllib.parse.urlsplit(str((getattr(hal, "ph", None) or {}).get("base") or "")).hostname or ""] = "Pi-hole"
    known.pop("", None)
    name = known.get(who) or ("Docker" if who.startswith("172.17.") else "")
    if not name:
        return who
    return f"{name} · .{who.rsplit('.', 1)[-1]}" if who.startswith("192.168.") else f"{name} · {who}"


def _gb(b) -> str:
    return f"{(b or 0) / 1073741824:.1f}"


def _app(name: str) -> str:
    return re.sub(r"(?i)\.exe$", "", str(name or ""))[:22]


def tv_grids(hal, st: dict, sc: dict, hist: dict) -> dict:
    """The TV's detail pages laid out in rows of cards (TV app 3.5-nzbcc.40 and later): {page title: [[card, ...], ...]}.
    A card has key/badge, an optional big number with a small line after it, a graph (it fills the card's spare
    height), core bars, chips ([before, bold, after]), rows ([label, value, level, bar %]), drives, monitors, queue,
    hourly bars and a footer graph; "weight" makes a card wider than its neighbours."""
    v, n, sec, pw = st["vitals"], st["net"], st["security"], st["power"]
    cpu, mem = v["cpu"], v["mem"]
    g = (v.get("gpus") or [{}])[0] if v.get("gpus") else {}
    ph = st.get("pihole") or {}
    hw = ph.get("hw") or {}
    upd = sec.get("updates") or 0
    ups = pw.get("ups")
    hrs = lambda vals: [round(x, 1) for x in _hist(vals)]
    peak = max([x for x in _hist(hist.get("cpu"))] or [0])
    full_keys = {i["key"].split(":", 1)[1] for i in st["issues"] if i["key"].startswith(("full:", "days:", "health:", "dtemp:"))}
    drives = []
    for d in st["drives"][:6]:
        dk = d.get("disk") or {}
        bits = [dk.get("media") or dk.get("bus") or "", f"{dk['temp']:.0f}°C" if dk.get("temp") else "",
                f"{dk['wear']:.0f}% wear" if dk.get("wear") is not None else (str(dk.get("health") or "").lower() if dk.get("health") else ""),
                f"full in ~{d['days_left']:.0f} days" if d.get("days_left") else ""]
        drives.append({"mount": d["mount"], "name": dk.get("name") or d.get("label") or "", "pct": round(d["pct"]),
                       "free": sm.fmt_bytes(d["free"]), "info": " · ".join(b for b in bits if b), "warn": d["mount"] in full_keys})
    free_total = sum(d.get("free") or 0 for d in st["drives"])
    disk_now = [x for x in _hist(hist.get("disk"))][-1:] or [None]

    pc = [[
        {"key": "PROCESSOR", "badge": re.sub(r"\s+\d+-Core Processor$|\(R\)|\(TM\)|CPU @.*$|Intel |AMD ", "", str(cpu.get("name") or "")).strip()[:20],
         "big": f"{cpu.get('pct') or 0:.0f}%", "bigsub": " · ".join(x for x in (f"{(cpu.get('freq') or 0) / 1000:.1f} GHz" if cpu.get("freq") else "",
                                                                                   f"{cpu['temp']:.0f}°C" if cpu.get("temp") is not None else "") if x),
         "graph": hrs(hist.get("cpu")), "graph_color": "#FF6A4D", "cores": [round(x) for x in (cpu.get("per_core") or [])][:32],
         "chips": [["", f"{cpu.get('cores') or '—'}", " cores"], ["peak ", f"{peak:.0f}%", " this hour"]]
                  + ([["", str(v["procs"]), " processes"]] if v.get("procs") else [])},
        {"key": "MEMORY", "badge": f"{mem.get('pct') or 0:.0f}% USED", "big": _gb(mem.get("used")),
         "bigsub": f"of {(mem.get('total') or 0) / 1073741824:.0f} GB", "graph": hrs(hist.get("mem")), "graph_color": "#60A5FA",
         "chips": [[_app(p["name"]) + " ", sm.fmt_bytes(p["mem"]), ""] for p in (v.get("top_mem") or [])[:4]]},
        {"key": "GRAPHICS", "badge": str(g.get("name") or "").replace("NVIDIA GeForce ", "").replace("AMD Radeon ", "")[:18],
         "big": f"{g.get('util') or 0:.0f}%" if g else "—",
         "bigsub": " · ".join(x for x in (f"{g['temp']:.0f}°C" if g.get("temp") is not None else "", f"{g['power']:.0f} W" if g.get("power") else "") if x),
         "graph": hrs(hist.get("gpu")), "graph_color": "#F472B6",
         "chips": ([["VRAM ", f"{_gb(g.get('vram_used'))} / {(g.get('vram_total') or 0) / 1073741824:.0f} GB", ""]] if g.get("vram_total") else [])
                  + [["video engine ", f"{g.get('video') or 0:.0f}%", ""]] + ([["fan ", f"{g['fan']:.0f}%", ""]] if g.get("fan") is not None else [])},
    ], [
        {"key": "DRIVES", "weight": 2, "badge": ("ALL HEALTHY" if not any(d["warn"] for d in drives) else "CHECK") + f" · {sm.fmt_bytes(free_total)} FREE",
         "drives": drives, "drive_grid": True,
         "footer_key": "DISK ACTIVITY · LAST HOUR", "footer_badge": f"NOW {sm.fmt_bytes(disk_now[0])}/s" if disk_now[0] is not None else "",
         "footer_graph": hrs(hist.get("disk")), "footer_color": "#34D399"},
        {"key": "BUSIEST NOW", "badge": "CPU · MEM",
         "rows": [[_app(p["name"]), f"{p.get('cpu') or 0:.0f}% · {sm.fmt_bytes(p.get('mem'))}", "ok"] for p in (v.get("top_cpu") or [])[:5]],
         "chips": [["up ", _dur(st.get("uptime")), ""]] + ([["", str(upd), f" update{'s' if upd != 1 else ''}"]] if upd else [])
                  + ([["UPS ", f"{ups.get('charge') or 0:.0f}%", ""]] if ups else ([["", pw.get("plan"), ""]] if pw.get("plan") else []))},
    ]]

    dfd = sec.get("defender") or {}
    fw = sec.get("firewall") or []
    fw_on = bool(fw) and all(f["on"] for f in fw)
    new_dev = [i for i in st["issues"] if i["key"].startswith("newdev:")]
    h24 = hal.history("24h")
    down24 = [round((x or 0) / 1024, 1) for x in (h24.get("net_down") or [])]
    up24 = [round((x or 0) / 1024, 1) for x in (h24.get("net_up") or [])]
    tr = hal.traffic_24h()
    pings = _hist(hist.get("ping"))
    hourly = ph.get("hourly") or []
    clients = ph.get("top_clients") or []
    cmax = max([c[1] for c in clients] or [1]) or 1
    peak_at = datetime.fromtimestamp(tr["peak_t"]).strftime("%I %p").lstrip("0") if tr.get("peak_t") else ""
    net = [[
        {"key": "INTERNET", "weight": 2, "badge": "DOWN" if any(i["key"] == "internet" for i in st["issues"]) else "ONLINE",
         "big": f"{n['ping_ms']:.0f} ms" if n.get("ping_ms") is not None else "—",
         "bigsub": " · ".join(x for x in ("ping", f"last hour {min(pings):.0f}–{max(pings):.0f} ms" if pings else "",
                                          f"{n.get('loss') or 0:.0f}% loss") if x),
         "graph": pings, "graph_fit": True, "graph_color": "#34D399",
         "chips": [["router ", str(n.get("gateway") or "—"), f" · {n['gateway_ms']:.0f} ms" if n.get("gateway_ms") is not None else ""],
                   ["DNS ", "answering" if n.get("dns") is not False else "not answering", ""],
                   ["Tailscale ", str(n["tailscale"]) if isinstance(n.get("tailscale"), str) else ("on" if n.get("tailscale") else "off"), ""]]},
        {"key": "SECURITY", "badge": "ALL ON" if dfd.get("rtp") and fw_on else "CHECK",
         "rows": [["Defender", "on" if dfd.get("rtp") else "off" if dfd else "—", "ok" if dfd.get("rtp") else "warning"],
                  ["Definitions", (f"{dfd['sig_age'] / 3600:.0f} h old" if dfd.get("sig_age") else "—"), "ok"],
                  ["Firewall", "on" if fw_on else "off", "ok" if fw_on else "warning"],
                  ["Failed sign-ins", str(sec.get("failed") or 0), "ok" if (sec.get("failed") or 0) < 10 else "warning"],
                  ["Windows updates", str(upd) if upd else "none", "ok"],
                  ["New devices", str(len(new_dev)) if new_dev else "none", "warning" if new_dev else "ok"]]},
    ], [
        {"key": "TRAFFIC · LAST 24 H", "weight": 1.6,
         "big": f"↓ {sm.fmt_bytes(n['down'])}/s" if n.get("down") is not None else "—",
         "bigsub": f"↑ {sm.fmt_bytes(n['up'])}/s now" if n.get("up") is not None else "",
         "graph": down24, "graph_color": "#22D3EE", "graph2": up24, "graph2_color": "#A78BFA",
         "legend": [["down", "#22D3EE"], ["up", "#A78BFA"]],
         "chips": [["24 h ↓ ", sm.fmt_bytes(tr["down"]), ""], ["↑ ", sm.fmt_bytes(tr["up"]), ""]]
                  + ([["peak ", f"{sm.fmt_bytes(tr['peak'])}/s", f" at {peak_at}"]] if tr.get("peak") else [])},
        {"key": "BUSIEST DEVICES", "weight": 1.3, "badge": f"{ph.get('clients') or len(clients)} ON THE NETWORK" if ph.get("ok") else "",
         "rows": [[_device_name(hal, n, c[0])[:30], f"{c[1]:,}", "ok", round(c[1] * 100 / cmax)] for c in clients[:5]]
                 or [["Pi-hole isn't answering", "", "ok"]]},
    ]]

    km = st.get("kuma") or {}
    mons = sorted(km.get("monitors") or [], key=lambda m: (m["status"] == "up", m["name"].lower()))
    kmons = [{"name": m["name"], "type": m.get("type") or "", "status": m["status"], "ms": m.get("ms"),
              "hist": list(hal.kuma_hist.get(m["name"]) or [m["status"]])[-30:]} for m in mons][:20]
    up_ms = [m["ms"] for m in mons if m["status"] == "up" and m.get("ms") is not None]
    downs = [m for m in mons if m["status"] != "up"]
    half = (len(kmons) + 1) // 2
    slow = max((m for m in mons if m.get("ms") is not None), key=lambda m: m["ms"], default=None)
    if 0 < len(mons) <= 5:
        # A few monitors: a card each, with the last hour's response times and checks (TV app 3.5-nzbcc.42+).
        colors = ["#60A5FA", "#34D399", "#22D3EE", "#A78BFA", "#FBBF24"]
        down_hour = sum(1 for m in mons if m["status"] != "up" or "down" in list(hal.kuma_hist.get(m["name"]) or []))

        def kcard(i, m):
            g = [x for x in (hal.kuma_ms.get(m["name"]) or []) if x is not None]
            chk = list(hal.kuma_hist.get(m["name"]) or [m["status"]])
            up_pct = round(100 * sum(1 for x in chk if x == "up") / len(chk)) if chk else 0
            is_down = m["status"] != "up"
            since = hal.kuma_down_t.get(m["name"])
            ty = str(m.get("type") or "").upper()
            return {"key": m["name"].upper()[:24], "badge": ("DOWN" if m["status"] == "down" else "CHECK" if is_down else "UP") + (f" · {ty}" if ty else ""),
                    "big": m["status"] if is_down else (f"{m['ms']:.0f} ms" if m.get("ms") is not None else "up"),
                    "bigsub": (f"since {datetime.fromtimestamp(since).strftime('%I:%M %p').lstrip('0')} · {_dur(time.time() - since)}" if is_down and since
                               else f"avg {sum(g) / len(g):.0f} · max {max(g):.0f}" if g else "measuring"),
                    "graph": [round(x, 1) for x in g[-120:]] if len(g) > 1 else [], "graph_fit": True,
                    "graph_color": "#FF6A4D" if is_down else colors[i % len(colors)],
                    "checks": chk[-30:], "chips": [["up ", f"{up_pct}%", f" · last {len(chk) // 2 or 1} min" if len(chk) < 120 else " · last hour"]]}
        fast = min((m for m in mons if m.get("ms") is not None and m["status"] == "up"), key=lambda m: m["ms"], default=None)
        summ = {"key": "UPTIME KUMA", "badge": sc["kuma"]["head"].upper(), "big": f"{km.get('up', 0)}/{len(mons)}", "bigsub": "answering",
                "rows": ([[m["name"][:22], m["status"], "critical" if m["status"] == "down" else "warning"] for m in downs[:2]]
                         or [["Everything's answering", "", "ok"]])
                        + ([["Average", f"{sum(up_ms) / len(up_ms):.0f} ms", "ok"]] if up_ms else [])
                        + ([["Slowest", f"{slow['name'][:14]} · {slow['ms']:.0f} ms", "ok"]] if slow else [])
                        + ([["Fastest", f"{fast['name'][:14]} · {fast['ms']:.0f} ms", "ok"]] if fast else [])
                        + [["Down in the last hour", str(down_hour) if down_hour else "none", "warning" if down_hour else "ok"]]}
        cards = [summ] + [kcard(i, m) for i, m in enumerate(mons)]
        cut = (len(cards) + 1) // 2
        kuma = [cards[:cut], cards[cut:]] if len(cards) > 2 else [cards]

    else:
        kuma = [[
            {"key": "UPTIME KUMA", "badge": sc["kuma"]["head"].upper(), "big": f"{km.get('up', 0)}/{len(mons)}" if km.get("ok") else "—",
             "bigsub": "answering" if km.get("ok") else sc["kuma"]["head"],
             "rows": ([[m["name"][:22], m["status"], "critical" if m["status"] == "down" else "warning"] for m in downs[:3]]
                      or [["Everything's answering", "", "ok"]])
                     + [["Answering", str(len(mons) - len(downs)), "ok"]]
                     + ([["Average", f"{sum(up_ms) / len(up_ms):.0f} ms", "ok"]] if up_ms else [])
                     + ([["Slowest", f"{slow['name'][:14]} · {slow['ms']:.0f} ms", "ok"]] if slow else [])},
            {"key": "MONITORS", "weight": 1.6, "badge": "LAST 30 CHECKS", "monitors": kmons[:half]},
            {"key": " ", "weight": 1.6, "monitors": kmons[half:]},
        ]] if kmons else [[{"key": "UPTIME KUMA", "rows": [["Status", sc["kuma"]["head"], "warning"]]}]]

    lists_age = f"{(time.time() - ph['gravity_t']) / 86400:.0f} days" if ph.get("gravity_t") else "—"
    temp = hw.get("temp")
    mem_gb = round((hw.get("mem_total") or 0) / 1e9)
    top_b, top_a = ph.get("top_blocked") or [], ph.get("top_allowed") or []
    bmax, amax = max([d[1] for d in top_b] or [1]) or 1, max([d[1] for d in top_a] or [1]) or 1
    pi = [[
        {"key": "HEALTH", "badge": "" if temp is None else "COOL" if temp < 60 else "WARM" if temp < 75 else "HOT",
         "big": f"{temp:.1f}°C" if temp is not None else "—", "bigsub": "slows at 80°C" if temp is not None else "",
         "rows": [["Processor", (f"{hw['cpu_pct']:.0f}%" + (f" · load {hw['load'][0]:.2f}" if hw.get("load") else "")) if hw.get("cpu_pct") is not None else "—",
                   "ok", hw.get("cpu_pct") or 0],
                  ["Memory", f"{hw['mem_pct']:.0f}%" + (f" of {mem_gb} GB" if mem_gb else "") if hw.get("mem_pct") is not None else "—",
                   sc["pi"]["rows"][3][2], hw.get("mem_pct") or 0],
                  ["Model", sc["pi"]["rows"][0][1].split(" · ")[0] + (f" · {hw['cores']} cores" if hw.get("cores") else ""), "ok"],
                  ["Up for", _dur(hw.get("uptime")) if hw.get("uptime") else "—", "ok"]]},
        {"key": "DNS QUERIES · LAST 24 H", "weight": 2,
         "badge": f"PI-HOLE v{str(hw.get('version') or '').lstrip('v')}" if hw.get("version") else "PI-HOLE",
         "big": f"{int(ph.get('queries') or 0):,}" if ph.get("ok") else "—",
         "bigsub": (f"asked · {int(ph.get('blocked') or 0):,} blocked ({ph.get('percent') or 0:.0f}%)"
                    + (f" · {ph['clients']} devices" if ph.get("clients") else "")) if ph.get("ok") else "",
         "hourly": [h[0] for h in hourly], "hourly2": [h[1] for h in hourly], "hourly_color": "#2A9D74", "hourly2_color": "#FF6A4D",
         "hourly_labels": ["24 h ago", "18 h", "12 h", "6 h", "now"] if hourly else [],
         "hourly_label": "green: allowed   red: blocked" if hourly else "",
         "rows": [] if hourly else [["Pi-hole isn't answering", "", "ok"]]},
    ], [
        {"key": "PI-HOLE", "badge": ("BLOCKING" if ph.get("blocking") is not False else "PAUSED") if ph.get("ok") else "NOT ANSWERING",
         "rows": [["Blocking", ("on" if ph.get("blocking") is not False else "paused") if ph.get("ok") else "—", "ok" if ph.get("blocking") is not False else "warning"],
                  ["Updates", "update ready" if hw.get("update") else "up to date", "info" if hw.get("update") else "ok"],
                  ["Block lists", f"{lists_age} old" if ph.get("gravity_t") else "—", "ok"],
                  ["Sites on the lists", f"{int(ph.get('domains') or 0):,}" if ph.get("domains") else "—", "ok"],
                  ["Devices", str(ph.get("clients") or "—"), "ok"]]},
        {"key": "MOST BLOCKED", "badge": "TODAY", "weight": 1.25,
         "rows": [[d[0][:34], f"{d[1]:,}", "ok", round(d[1] * 100 / bmax)] for d in top_b[:5]] or [["Nothing yet", "", "ok"]]},
        {"key": "MOST ASKED FOR", "badge": "ALLOWED", "weight": 1.25,
         "rows": [[d[0][:34], f"{d[1]:,}", "ok", round(d[1] * 100 / amax)] for d in top_a[:5]] or [["Nothing yet", "", "ok"]]},
    ]]
    return {"The PC": pc, "Network": net, "Uptime Kuma": kuma, "Raspberry Pi": pi}


def tv_details(hal) -> dict:
    """The TV's detail pages (Down on HAL's screen): The PC, Network, Uptime Kuma, Raspberry Pi. Each page has a
    title, a status and columns; a column has a heading, optionally a big number, a graph, core bars, drives,
    monitors, and rows of [label, value, level]."""
    st = hal.status()
    sc = tv_screen(st)
    hist = hal.history("1h")
    v, n, sec, pw = st["vitals"], st["net"], st["security"], st["power"]
    cpu, mem = v["cpu"], v["mem"]
    g = (v.get("gpus") or [{}])[0]
    gbs = lambda b: f"{(b or 0) / 1073741824:.1f}"
    word = lambda lv, ok="OK": ok if lv == "ok" else ("CHECK" if lv == "warning" else "PROBLEM")
    top = (v.get("top_cpu") or [{}])[0]
    top_name = re.sub(r"(?i)\.exe$", "", str(top.get("name") or "—"))
    fans = v.get("fans") or []
    upd = sec.get("updates") or 0
    full_keys = {i["key"].split(":", 1)[1] for i in st["issues"] if i["key"].startswith(("full:", "days:", "health:", "dtemp:"))}
    drives = []
    for d in st["drives"][:5]:
        dk = d.get("disk") or {}
        bits = [dk.get("media") or dk.get("bus") or "", f"{dk['temp']:.0f}°C" if dk.get("temp") else "",
                f"{dk['wear']:.0f}% wear" if dk.get("wear") is not None else (str(dk.get("health") or "").lower() if dk.get("health") else ""),
                f"full in ~{d['days_left']:.0f} days" if d.get("days_left") else ""]
        drives.append({"mount": d["mount"], "name": dk.get("name") or d.get("label") or "", "pct": round(d["pct"]),
                       "free": sm.fmt_bytes(d["free"]), "info": " · ".join(b for b in bits if b), "warn": d["mount"] in full_keys})
    ups = pw.get("ups")
    pc = {"title": "The PC", "level": sc["pc"]["level"], "head": word(sc["pc"]["level"]), "cols": [
        {"key": "PROCESSOR", "badge": re.sub(r"\s+\d+-Core Processor$", "", str(cpu.get("name") or ""))[:28],
         "big": f"{cpu.get('pct') or 0:.0f}%" + (f"   {cpu['temp']:.0f}°C" if cpu.get("temp") is not None else ""),
         "graph": _hist(hist.get("cpu")), "graph_color": "#FF6A4D", "cores": [round(x) for x in (cpu.get("per_core") or [])][:32],
         "rows": [["Cores / threads", f"{cpu.get('cores') or '—'} / {cpu.get('threads') or '—'}", "ok"],
                  ["Speed", f"{(cpu.get('freq') or 0) / 1000:.1f} GHz" if cpu.get("freq") else "—", "ok"],
                  ["Busiest", f"{top_name} · {top.get('cpu') or 0:.0f}%" if top else "—", "ok"]]},
        {"key": "MEMORY & GRAPHICS", "big": f"{gbs(mem.get('used'))} of {(mem.get('total') or 0) / 1073741824:.0f} GB",
         "graph": _hist(hist.get("mem")), "graph_color": "#60A5FA",
         "rows": [["GPU", (str(g.get("name") or "").replace("NVIDIA GeForce ", "") + f" · {g.get('util') or 0:.0f}%") if g else "—", "ok"],
                  ["GPU temperature", f"{g['temp']:.0f}°C" if g.get("temp") is not None else "—", "ok"],
                  ["Video memory", f"{gbs(g.get('vram_used'))} of {(g.get('vram_total') or 0) / 1073741824:.0f} GB" if g.get("vram_total") else "—", "ok"],
                  ["Fans", " · ".join(f"{f['name'].replace(' fan', '')} {f['value']:.0f}" for f in fans[:2]) + " rpm" if fans else "—", "ok"]]},
        {"key": "DRIVES", "badge": "ALL HEALTHY" if not any(d["warn"] for d in drives) else "CHECK", "drives": drives,
         "rows": [["Power", (("On battery · " if ups.get("on_battery") else "Mains · ") + f"UPS {ups.get('charge') or 0:.0f}%"
                             + (f" · {ups['load']:.0f}% load" if ups.get("load") is not None else "")) if ups else (pw.get("plan") or "Mains"), sc["pc"]["rows"][4][2]],
                  ["Up for", _dur(st.get("uptime")) + (f" · {upd} update{'s' if upd != 1 else ''}" if upd else ""), "ok"]]}]}
    ph = st.get("pihole") or {}
    dfd = sec.get("defender") or {}
    fw = sec.get("firewall") or []
    new_dev = [i for i in st["issues"] if i["key"].startswith("newdev:")]
    down = [x / 1048576 for x in _hist(hist.get("net_down")) if x is not None]
    net = {"title": "Network", "level": sc["net"]["level"], "head": word(sc["net"]["level"], "ALL UP"), "cols": [
        {"key": "INTERNET", "big": f"{n['ping_ms']:.0f} ms" if n.get("ping_ms") is not None else "—",
         "graph": [round(x, 1) for x in down], "graph_color": "#34D399",
         "rows": [["Download now", f"{sm.fmt_bytes(n['down'])}/s" if n.get("down") is not None else "—", "ok"],
                  ["Upload now", f"{sm.fmt_bytes(n['up'])}/s" if n.get("up") is not None else "—", "ok"],
                  ["Packet loss", f"{n.get('loss') or 0:.0f}%", "warning" if (n.get("loss") or 0) >= 10 else "ok"],
                  ["Router", f"{n.get('gateway') or '—'} · {n['gateway_ms']:.0f} ms" if n.get("gateway_ms") is not None else (n.get("gateway") or "—"),
                   sc["net"]["rows"][1][2]],
                  ["DNS", "answering" if n.get("dns") is not False else "not answering", "ok" if n.get("dns") is not False else "warning"],
                  ["Tailscale", n.get("tailscale") or "off", "ok"]]},
        {"key": "PI-HOLE", "big": f"{int(ph.get('blocked') or 0):,}" if ph.get("ok") else "—",
         "bigsub": f"blocked today · {ph.get('percent') or 0:.0f}% of {int(ph.get('queries') or 0):,}" if ph.get("ok") else "",
         "rows": [["Top blocked", "", "ok"]] + [[d[0], f"{d[1]:,}", "ok"] for d in (ph.get("top_blocked") or [])[:3]]
                 + [["Busiest devices", "", "ok"]] + [[d[0], f"{d[1]:,}", "ok"] for d in (ph.get("top_clients") or [])[:3]]},
        {"key": "SECURITY", "badge": "ALL ON" if dfd.get("rtp") and fw and all(f["on"] for f in fw) else "CHECK",
         "rows": [["Defender", "on" if dfd.get("rtp") else "off" if dfd else "—", "ok" if dfd.get("rtp") else "warning"],
                  ["Definitions", (f"{dfd['sig_age'] / 3600:.0f} h old" if dfd.get("sig_age") else "—"), "ok"],
                  ["Firewall", "on" if fw and all(f["on"] for f in fw) else "off", "ok" if fw and all(f["on"] for f in fw) else "warning"],
                  ["Failed sign-ins", str(sec.get("failed") or 0), "ok" if (sec.get("failed") or 0) < 10 else "warning"],
                  ["Windows updates", str(upd) if upd else "none", "ok"],
                  ["New devices", str(len(new_dev)) if new_dev else "none", "warning" if new_dev else "ok"]]}]}
    km = st.get("kuma") or {}
    mons = sorted(km.get("monitors") or [], key=lambda m: (m["status"] == "up", m["name"].lower()))
    kmons = [{"name": m["name"], "type": m.get("type") or "", "status": m["status"], "ms": m.get("ms"),
              "hist": list(hal.kuma_hist.get(m["name"]) or [m["status"]])[-30:]} for m in mons][:16]
    half = (len(kmons) + 1) // 2
    kuma = {"title": "Uptime Kuma", "level": sc["kuma"]["level"], "head": sc["kuma"]["head"].upper(),
            "cols": [{"key": "MONITORS", "badge": "LAST 30 CHECKS", "monitors": kmons[:half]},
                     {"key": " ", "monitors": kmons[half:]}] if kmons else
                    [{"key": "UPTIME KUMA", "rows": [["Status", sc["kuma"]["head"], "warning"]]}]}
    hw = ph.get("hw") or {}
    pi = {"title": "Raspberry Pi", "level": sc["pi"]["level"], "head": word(sc["pi"]["level"]), "cols": [
        {"key": "TEMPERATURE", "big": f"{hw['temp']:.1f}°C" if hw.get("temp") is not None else "—",
         "graph": list(hal.pi_temp_hist)[-60:], "graph_color": "#FBBF24",
         "rows": [["Model", sc["pi"]["rows"][0][1], "ok"], ["Host name", hw.get("hostname") or "—", "ok"],
                  ["Up for", _dur(hw.get("uptime")) if hw.get("uptime") else "—", "ok"]]},
        {"key": "SYSTEM", "rows": [["CPU", f"{hw['cpu_pct']:.0f}%" if hw.get("cpu_pct") is not None else "—", "ok"],
                                   ["Load", " · ".join(f"{x:.2f}" for x in hw.get("load") or []) or "—", "ok"],
                                   ["Cores", str(hw.get("cores") or "—"), "ok"],
                                   ["Memory", f"{hw['mem_pct']:.0f}% of {round((hw.get('mem_total') or 0) / 1e9)} GB" if hw.get("mem_pct") is not None else "—",
                                    sc["pi"]["rows"][3][2]]]},
        {"key": "PI-HOLE", "rows": [["Version", hw.get("version") or "—", "ok"],
                                    ["Updates", "update ready" if hw.get("update") else "up to date", "info" if hw.get("update") else "ok"],
                                    ["Blocking", ("on" if ph.get("blocking") is not False else "paused") if ph.get("ok") else "—",
                                     "ok" if ph.get("blocking") is not False else "warning"],
                                    ["Block lists", f"{(time.time() - ph['gravity_t']) / 86400:.0f} days old" if ph.get("gravity_t") else "—", "ok"],
                                    ["Sites blocked", f"{int(ph.get('domains') or 0):,}" if ph.get("domains") else "—", "ok"],
                                    ["Devices", str(ph.get("clients") or "—"), "ok"]]}]}
    try:
        dl = tv_downloads_page(hal, bool(st.get("demo")))
    except Exception as exc:  # noqa: BLE001 - the other pages still show
        log.info("TV downloads page: %s", exc)
        dl = {"title": "Downloads", "level": "warning", "head": "CAN'T READ IT", "cols": [{"key": "QUEUE", "rows": [["Couldn't read SABnzbd", ""]]}]}
    try:
        cols = dl.get("cols") or []
        if "grid" not in dl and len(cols) == 3 and cols[2].get("rows") is not None:
            rows = cols[2]["rows"]
            cut = next((i for i, r in enumerate(rows) if r[0] == "Saved searches" and not r[1]), len(rows))
            dl["grid"] = [[{**cols[0], "weight": 2}, cols[1]],
                          [{"key": "COMING UP", "badge": "SONARR · RADARR", "rows": rows[:cut] or [["Nothing coming up", "", "ok"]]},
                           {"key": "SAVED SEARCHES", "rows": rows[cut + 1:] or [["No saved searches", "", "ok"]]}]]
    except Exception as exc:  # noqa: BLE001
        log.info("TV downloads grid: %s", exc)
    try:
        grids = tv_grids(hal, st, sc, hist)
        for pg in (pc, net, kuma, pi):
            if pg["title"] in grids:
                pg["grid"] = grids[pg["title"]]
    except Exception as exc:  # noqa: BLE001 - the plain columns still show
        log.info("TV detail grids: %s", exc)
    return {"pages": [pc, net, kuma, pi, dl]}


def tv_brief(st: dict) -> dict:
    """Extra readings for the TV's HAL screensaver, and what HAL says out loud there."""
    v, n, ph, km = st["vitals"], st["net"], st.get("pihole") or {}, st.get("kuma") or {}
    sab = next((x for x in st["services"] if x["id"] == "sabnzbd"), None)
    det = (sab or {}).get("detail") or ""
    alerts = [{"level": i["level"], "title": i["title"]} for i in st["issues"] if i["level"] != "info"][:3]
    down = any(i["key"] in ("internet", "router") for i in st["issues"])
    name = st["hal"]["name"]
    quote = st["hal"]["quote"]
    if name and name in st["hal"]["greeting"]:
        quote = re.sub(r",\s*" + re.escape(name) + r"([.!?])$", r"\1", quote)   # "...attention, Dave." after "Good evening, Dave."
    say = st["hal"]["greeting"] + " " + quote
    if alerts and alerts[0]["title"] not in say:
        say += " " + alerts[0]["title"] + "."
    lines = [{"text": st["hal"]["greeting"] + " " + quote, "say": spoken(say)}]
    if not alerts:
        for q in random.sample(QUOTES_OK, min(4, len(QUOTES_OK))):
            q = q.format(name=name)
            if q != st["hal"]["quote"]:
                lines.append({"text": q, "say": spoken(q)})
    if ph.get("ok") and ph.get("blocked"):
        q = f"Pi-hole has blocked {int(ph['blocked']):,} ads today, {name}."
        lines.append({"text": q, "say": spoken(q)})
    for a in alerts[1:]:
        lines.append({"text": a["title"] + ".", "say": spoken(a["title"] + ".")})
    return {"greeting": st["hal"]["greeting"], "quote": quote, "say": spoken(say), "alerts": alerts, "voice": dict(TV_VOICE), "lines": lines[:8],
            "screen": tv_screen(st),
            "cpu_temp": v["cpu"].get("temp"), "mem_used": v["mem"].get("used"), "mem_total": v["mem"].get("total"),
            "ping": n.get("ping_ms"), "net_down": n.get("down"), "internet_down": down,
            "downloading": det if det.startswith("downloading") else "",
            "kuma_up": km.get("up") if km.get("ok") else None, "kuma_total": len(km.get("monitors") or []) if km.get("ok") else None,
            "ads_on": ph.get("blocking") is not False if ph.get("ok") else None}


TV_PACKAGE = "com.gathin.tvhome"
TV_PHRASES_MAX = 20


def tv_host() -> str:
    """The TV's address: what the TV installer remembered, else Kodi's address (Kodi runs on the TV)."""
    try:
        h = (BASE_DIR / "tv_host.txt").read_text(encoding="utf-8").strip()
        if h:
            return h
    except OSError:
        pass
    try:
        url = str(nzb_config().get("kodi_url") or "")
        return urllib.parse.urlsplit(url if "://" in url else "http://" + url).hostname or ""
    except ValueError:
        return ""


def tv_adb(command: str, timeout: float = 30) -> tuple[bool, str]:
    """Runs a shell command on the TV through the TV tools (tv-launcher/tools/tv.py, the installer's ADB key)."""
    folder = BASE_DIR / "tv-launcher"
    py = folder / "tools" / "venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    if not py.exists():
        return False, "The TV tools aren't set up on this PC yet: run tv-launcher\\install-on-tv.ps1 once."
    host = tv_host()
    if not host:
        return False, "I don't know the TV's address yet: run tv-launcher\\install-on-tv.ps1 once."
    try:
        r = subprocess.run([str(py), "-I", "tools/tv.py", command], cwd=str(folder),
                           env=dict(os.environ, TV_HOST=host, PYTHONIOENCODING="utf-8:replace", PYTHONUTF8="1"),
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return False, "The TV didn't answer in time. Is it on?"
    except OSError as exc:
        return False, f"Couldn't run the TV tools: {exc}"
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return r.returncode == 0, out[-400:]


# What's in front on the TV. Kodi keeps a paused movie "playing" in the background when you switch to
# YouTube, so Kodi alone can't say what's on. Asked through ADB, at most every 15 s, never holding up a page.
TV_APPS = {"org.xbmc.kodi": "Kodi", "com.google.android.youtube.tv": "YouTube", "com.google.android.youtube.tvkids": "YouTube Kids",
           "com.google.android.youtube.tvmusic": "YouTube Music", "org.jellyfin.androidtv": "Jellyfin", "com.netflix.ninja": "Netflix",
           "com.amazon.amazonvideo.livingroom": "Prime Video", "com.disney.disneyplus": "Disney+", "com.hbo.hbonow": "Max",
           "com.wbd.stream": "Max", "com.plexapp.android": "Plex", "com.spotify.tv.android": "Spotify", "com.apple.atve.androidtv.appletv": "Apple TV",
           "com.google.android.apps.tv.launcherx": "Home", "com.google.android.tvlauncher": "Home", "com.android.tv.settings": "Settings"}
_tv_front = {"t": 0.0, "ok_t": 0.0, "pkg": "", "activity": "", "busy": False, "error": "", "media": {}}


def _tv_media_parse(out: str) -> dict:
    """What each app says it's playing (Android's media sessions): {package: {"title", "sub", "state"}}.
    Reads the whole `dumpsys media_session`: a session starts at a "package=" line (or an "pkg/tag (userId=" header);
    its state is "state=PlaybackState {state=3" (older Android) or "{mState=3(PLAYING)" (newer); its title is in
    "metadata: size=N, description=Title, Sub, Desc", or a "METADATA_KEY_TITLE"/"android.media.metadata.TITLE" line."""
    found, pkg = {}, ""

    def put(p):
        return found.setdefault(p, {"title": "", "sub": "", "state": 0})

    for line in (out or "").splitlines():
        s = line.strip()
        head = re.match(r"(?:\S+\s+)?([A-Za-z][\w.]+\.[\w.]+)/\S*\s+\(userId=", s)
        if s.startswith("package="):
            pkg = s[8:].split()[0].strip() if s[8:].strip() else ""
            if pkg:
                put(pkg)
            continue
        if head:
            pkg = head.group(1)
            put(pkg)
            continue
        if not pkg:
            continue
        if "PlaybackState" in s:
            m = re.search(r"\bm?[Ss]tate=(\d+)", s.split("PlaybackState", 1)[1])
            if m:
                found[pkg]["state"] = int(m.group(1))
        elif "description=" in s and ("metadata" in s.lower()):
            parts = s.split("description=", 1)[1].strip().split(", ")
            while parts and parts[-1].strip() in ("null", ""):
                parts.pop()
            if not parts:
                continue
            title, sub = (parts[0], "") if len(parts) == 1 else (parts[0], parts[1]) if len(parts) == 2 else (", ".join(parts[:-2]), parts[-2])
            if not found[pkg]["title"] or found[pkg]["state"] in (2, 3):
                found[pkg].update(title=title.strip()[:120], sub=sub.strip()[:80])
        elif not found[pkg]["title"]:
            m = re.search(r"(?:METADATA_KEY_TITLE|android\.media\.metadata\.(?:DISPLAY_)?TITLE)\W+(.+)", s)
            if m and m.group(1).strip() not in ("null", ""):
                found[pkg]["title"] = m.group(1).strip().strip(",")[:120]
    return found


def _tv_front_parse(out: str) -> tuple[str, str]:
    m = re.search(r"\s([A-Za-z][\w.]*)/([\w.$]+)", out or "")
    if not m:
        return "", ""
    act = m.group(2)
    return m.group(1), (m.group(1) + act if act.startswith(".") else act)


# The TV's own "watch an HDMI input" screens (a games console, a cable box): the name then comes from HDMI-CEC.
TV_INPUT_APPS = {"com.google.android.tv", "com.android.tv", "com.mediatek.wwtv.tvcenter", "com.droidlogic.tvinput",
                 "com.sony.dtv.tvx", "com.tcl.tv", "com.tcl.tvinput", "org.droidtv.playtv", "com.hisense.tv", "com.xiaomi.mitv.tvplayer",
                 "com.google.android.apps.tv.launcherx.inputs", "com.nvidia.tvinput", "com.realtek.tv"}


def _tv_is_input(pkg: str, act: str) -> bool:
    if pkg in TV_INPUT_APPS:
        return True
    return pkg not in TV_APPS and pkg != TV_PACKAGE and bool(re.search(r"tvinput|tvcenter|livetv|playtv|hdmi|inputsource|tvplayer|passthrough", pkg + " " + act, re.I))


def _cec_pretty(name: str) -> str:
    n = name.strip().strip('"')
    m = re.fullmatch(r"PS\s?([2-5])", n, re.I)
    if m:
        return f"PlayStation {m.group(1)}"
    m = re.fullmatch(r"playstation\s*([2-5]?)", n, re.I)
    if m:
        return "PlayStation" + (" " + m.group(1) if m.group(1) else "")
    if re.fullmatch(r"(nintendo\s*)?switch(\s*2)?", n, re.I):
        return "Nintendo Switch" + (" 2" if n.rstrip().endswith("2") else "")
    if re.fullmatch(r"xbox", n, re.I):
        return "Xbox"
    return n


def _tv_hdmi_parse(out: str) -> str:
    """The name of what's showing on the TV's HDMI input, from `dumpsys hdmi_control` (CEC): the device at the active
    source's address, else a games console (PlayStation, Nintendo Switch, Xbox), else the one games console/player that's on. "" if it can't tell."""
    active, devices = "", []
    for line in (out or "").splitlines():
        s = line.strip()
        if re.search(r"active\s*source", s, re.I) and not active:
            addrs = re.findall(r"0x([0-9a-fA-F]{4})\b", s)
            if addrs:
                active = addrs[-1].lower()
        m = re.search(r"(?:display_name|displayName|mDisplayName)\s*[:=]\s*(.*?)(?=\s+\w+\s*[:=]\s|,|$)", s)
        if m and m.group(1).strip() not in ("", "null"):
            pa = re.search(r"(?:physical_address|physicalAddress|mPhysicalAddress)\s*[:=]\s*0x([0-9a-fA-F]+)", s)
            ty = re.search(r"(?:device_type|deviceType|mDeviceType)\s*[:=]\s*(\d+)", s)
            pw = re.search(r"(?:power_status|powerStatus|mDevicePowerStatus)\s*[:=]\s*(-?\d+)", s)
            devices.append({"name": m.group(1).strip(), "pa": pa.group(1).lower().zfill(4) if pa else "",
                            "type": int(ty.group(1)) if ty else -1, "power": int(pw.group(1)) if pw else -1})
    devices = [d for d in devices if d["type"] != 0]          # 0 is the TV itself
    for d in devices:
        if active and d["pa"] == active:
            return _cec_pretty(d["name"])
    for d in devices:
        if re.search(r"playstation|\bps[2-5]\b|nintendo|switch|xbox", d["name"], re.I):
            return _cec_pretty(d["name"])
    on = [d for d in devices if d["power"] == 0 and d["type"] in (4, 1, 3, 5)]
    return _cec_pretty(on[0]["name"]) if len(on) == 1 else ""


CONSOLE_RE = re.compile(r"PlayStation(?:\s*(?:\(R\)|®))?\s*[2-5]?|\bPS\s?[2-5]\b|Nintendo\s*Switch(?:\s*2)?|\bSwitch\b|\bXbox(?:\s+(?:One|Series\s+[XS]))?", re.I)


def _console_anywhere(*texts: str) -> str:
    """A games console's name anywhere in the TV's answers (any wording: CEC lists, input labels), or ""."""
    names = []
    for text in texts:
        names += [m.group(0) for m in CONSOLE_RE.finditer(text or "")]
    if not names:
        return ""
    best = max(set(names), key=lambda n: (names.count(n), len(n)))
    return _cec_pretty(re.sub(r"\s+", " ", re.sub(r"\(R\)|®", " ", best)))


def _only_console(*texts: str) -> str:
    """The games console's name if the TV's answers mention exactly one console (PS5 and PlayStation 5 are the
    same one), else "": with a PlayStation and a Switch both listed, there's no telling which is on."""
    names = set()
    for text in texts:
        for mt in CONSOLE_RE.finditer(text or ""):
            names.add(_cec_pretty(re.sub(r"\s+", " ", re.sub(r"\(R\)|®", " ", mt.group(0))).strip()))
    names = {n for n in names if not any(o != n and o.startswith(n) for o in names)}   # "PlayStation" is the PlayStation 5
    return names.pop() if len(names) == 1 else ""


# ---- The Denon (or Marantz) amp: the consoles go through it, so the TV only sees "the amp's input" ----
DENON_INPUTS = {"GAME": "Game", "GAME2": "Game 2", "MPLAY": "Media Player", "BD": "Blu-ray", "DVD": "DVD", "SAT/CBL": "CBL/SAT",
                "SAT": "SAT", "CBL/SAT": "CBL/SAT", "AUX1": "AUX1", "AUX2": "AUX2", "AUX": "AUX", "TV": "TV Audio", "NET": "HEOS Music",
                "BT": "Bluetooth", "TUNER": "Tuner", "PHONO": "Phono", "CD": "CD", "USB/IPOD": "USB", "8K": "8K", "VCR": "VCR", "DOCK": "Dock"}
_denon = {"host": "", "t": 0.0, "searching": False}


def _denon_http(host: str, path: str, timeout: float = 2.5) -> str:
    for port in (8080, 80):
        try:
            with urllib.request.urlopen(f"http://{host}:{port}{path}", timeout=timeout) as r:
                return r.read(200_000).decode("utf-8", "replace")
        except (OSError, ValueError):
            continue
    return ""


def _denon_telnet(host: str, commands: list[str], timeout: float = 2.0) -> str:
    try:
        with socket.create_connection((host, 23), timeout=timeout) as s:
            s.settimeout(0.8)
            out = b""
            for c in commands:
                s.sendall(c.encode("ascii") + b"\r")
                end = time.time() + 1.2
                while time.time() < end:
                    try:
                        chunk = s.recv(4096)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    out += chunk
            return out.decode("utf-8", "replace").replace("\r", "\n")
    except OSError:
        return ""


def _denon_is(host: str) -> bool:
    x = _denon_http(host, "/goform/formMainZone_MainZoneXmlStatusLite.xml", timeout=1.2)
    return "InputFuncSelect" in x


def _denon_find() -> str:
    """The amp's address: the setting, else the last one found, else asked on the network (UPnP), else the home network tried."""
    s = (HAL.settings.get("denon_host") if HAL else "") or ""
    if s:
        return s
    if _denon["host"] or _denon["searching"] or time.time() - _denon["t"] < 3600:
        return _denon["host"]
    _denon.update(searching=True, t=time.time())
    try:
        found = ""
        try:   # UPnP: Denon and Marantz amps answer, saying where their description is
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.settimeout(2.5)
            sock.sendto(b"M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\nST: ssdp:all\r\n\r\n", ("239.255.255.250", 1900))
            seen = set()
            end = time.time() + 3
            while time.time() < end and not found:
                try:
                    data, (ip, _p) = sock.recvfrom(4096)
                except socket.timeout:
                    break
                if ip in seen:
                    continue
                seen.add(ip)
                txt = data.decode("utf-8", "replace").lower()
                if ("denon" in txt or "marantz" in txt or "heos" in txt) and _denon_is(ip):
                    found = ip
            sock.close()
        except OSError:
            pass
        if not found:   # try every address on the TV's network (a few seconds, at most once an hour)
            base = tv_host()
            try:
                net = ipaddress.ip_network(base + "/24", strict=False) if base and re.fullmatch(r"[\d.]+", base) else None
            except ValueError:
                net = None
            if net:
                def probe(ip):
                    try:
                        with socket.create_connection((ip, 8080), timeout=0.4):
                            return ip
                    except OSError:
                        try:
                            with socket.create_connection((ip, 23), timeout=0.4):
                                return ip
                        except OSError:
                            return ""
                with ThreadPoolExecutor(max_workers=48) as ex:
                    open_ = [ip for ip in ex.map(probe, [str(h) for h in net.hosts()]) if ip]
                for ip in open_:
                    if _denon_is(ip) or _denon_telnet(ip, ["SI?"]).strip().startswith("SI"):
                        found = ip
                        break
        if found:
            log.info("Found the Denon amp at %s", found)
        _denon["host"] = found
        return found
    finally:
        _denon["searching"] = False


def _denon_parse_names(xml_text: str, telnet_text: str) -> dict:
    """{input code: the name you gave it on the amp}, from the web page's lists or the SSFUN telnet answer."""
    names = {}
    try:
        root = ET.fromstring(xml_text) if xml_text.strip().startswith("<") else None
    except ET.ParseError:
        root = None
    if root is not None:
        funcs = [v.text or "" for v in root.findall("./InputFuncList/value")]
        renames = [v.text or "" for v in root.findall("./RenameSource/value")]
        for f, r in zip(funcs, renames):
            if f.strip() and r.strip():
                names[f.strip().upper()] = r.strip()
    for line in (telnet_text or "").splitlines():
        m = re.match(r"SSFUN(\S+)\s+(.+)", line.strip())
        if m and m.group(1) != "END":
            names[m.group(1).strip().upper()] = m.group(2).strip()
    return names


def _denon_parse_input(xml_text: str, telnet_text: str) -> str:
    m = re.search(r"<InputFuncSelect>\s*<value>([^<]+)</value>", xml_text or "")
    if m:
        return m.group(1).strip()
    m = re.search(r"^SI(\S.*)$", telnet_text or "", re.M)
    return m.group(1).strip() if m else ""


def denon_now() -> str:
    """What the amp is showing: the name you gave its input (e.g. "PS5"), a console's name, or "Game (Denon)". "" if unknown."""
    host = _denon_find()
    if not host:
        return ""
    lite = _denon_http(host, "/goform/formMainZone_MainZoneXmlStatusLite.xml")
    full = _denon_http(host, "/goform/formMainZone_MainZoneXml.xml") if lite else ""
    tel = "" if lite else _denon_telnet(host, ["SI?", "SSFUN ?"])
    code = _denon_parse_input(lite, tel)
    if not code:
        return ""
    names = _denon_parse_names(full, tel)
    key = code.upper()
    shown = names.get(key, "") or code   # some models already give the renamed name ("PS5") as the selected input
    default = DENON_INPUTS.get(key, "")
    if shown.upper() == key and key in DENON_INPUTS or shown.lower() == default.lower():
        return f"{default or shown.title()} (Denon)"   # an input you haven't renamed
    return _cec_pretty(shown)


def _tv_front_refresh() -> None:
    try:
        # Two ways to ask, as TVs' Android versions word it differently: the resumed activity, else the focused window.
        ok, out = tv_adb("f=$(dumpsys activity activities | grep -m1 -E 'mResumedActivity|ResumedActivity'); "
                         "if [ -n \"$f\" ]; then echo \"$f\"; else dumpsys window | grep -m1 -E 'mCurrentFocus|mFocusedApp'; fi; "
                         "dumpsys power | grep -m1 mWakefulness=", timeout=25)
        if ok and re.search(r"mWakefulness=(Asleep|Dozing)", out or ""):
            # In standby the last app is still "in front" (YouTube), but nothing is on: say nothing is.
            if not _tv_front.get("asleep"):
                log.info("TV front app: the TV is in standby")
            now = time.time()
            _tv_front.update({"pkg": "", "activity": "", "media": {}, "input": "", "asleep": True, "t": now, "ok_t": now, "error": ""})
            return
        _tv_front["asleep"] = False
        pkg, act = _tv_front_parse(out) if ok else ("", "")
        media_out, inp = "", ""
        prev = {"pkg": _tv_front.get("pkg"), "title": (_tv_front.get("media") or {}).get("title"), "input": _tv_front.get("input")}
        if pkg and pkg != prev["pkg"]:
            # Say at once that the screen changed: the name lookups below (the PlayStation's name, the amp) can take a
            # while, and until now the old answer stood - Kodi "in front" with its paused movie while the PlayStation was on.
            t0 = time.time()
            _tv_front.update({"pkg": pkg, "activity": act, "t": t0, "ok_t": t0, "error": "", "media": {},
                              "input": "HDMI" if _tv_is_input(pkg, act) else ""})
        if pkg and _tv_is_input(pkg, act):
            seen, have_inputs = "", False   # what the TV said, for a second look if the amp's input has no name
            try:   # an HDMI input: who's on it (the PlayStation says its name over HDMI-CEC)
                hok, hout = tv_adb("dumpsys hdmi_control", timeout=20)
                inp = _tv_hdmi_parse(hout) if hok else ""
                iout = ""
                if not inp:   # other wordings, and the TV's input list (Sony and others label an input with the device's name)
                    iok, iout = tv_adb("dumpsys tv_input | grep -i -E 'label|name|hdmi|title'", timeout=20)
                    inp = _console_anywhere(hout if hok else "", iout if iok else "")
                    have_inputs, iout = True, (iout if iok else "")
                hout = (hout if hok else "") + "\n--- tv_input ---\n" + (iout or "")
                seen = hout
                if not inp and _tv_front.get("raw_logged") != "hdmi:" + pkg:
                    _tv_front["raw_logged"] = "hdmi:" + pkg
                    bits = [l.strip() for l in (hout or "").splitlines() if re.search(r"active|display|name|device|0x", l, re.I)][:30]
                    log.info("TV front app: an HDMI input (%s), but no name found. HDMI-CEC said: %s", pkg, " | ".join(bits)[:1500] or "(nothing)")
            except Exception as exc:  # noqa: BLE001
                log.info("TV front app: HDMI input, no name (%s)", exc)
            if not inp or re.search(r"denon|marantz|avr|receiver", inp, re.I):
                try:   # through the Denon: ask the amp which input it's on
                    d = denon_now()
                    if d.endswith("(Denon)"):
                        # An input you haven't renamed on the amp ("Media Player"): if the TV can see exactly one
                        # games console (its HDMI-CEC list or input labels), that's who is on it.
                        if not have_inputs:
                            iok, iout = tv_adb("dumpsys tv_input | grep -i -E 'label|name|hdmi|title'", timeout=20)
                            seen += "\n" + (iout if iok else "")
                        d = _only_console(seen) or d
                    if d:
                        inp = d
                except Exception as exc:  # noqa: BLE001
                    log.info("Denon: %s", exc)
            inp = inp or "HDMI"
        elif pkg and pkg not in ("org.xbmc.kodi", TV_PACKAGE) and TV_APPS.get(pkg) != "Home":
            try:   # what it's playing: a bonus, so a problem here never loses the app's name
                mok, mout = tv_adb("dumpsys media_session", timeout=20)
                media_out = mout if mok else ""
                if not mok and _tv_front.get("media_err") != pkg:
                    _tv_front["media_err"] = pkg
                    log.info("TV front app: no title (%s)", (mout or "no answer").strip().splitlines()[-1][:200] if mout else "no answer")
            except Exception as exc:  # noqa: BLE001
                if _tv_front.get("media_err") != pkg:
                    _tv_front["media_err"] = pkg
                    log.info("TV front app: no title (%s)", exc)
        now = time.time()
        if pkg:
            media = _tv_media_parse(media_out).get(pkg) or {}
            if media_out and not media.get("title") and _tv_front.get("raw_logged") != pkg:
                # Once per app: what the TV said, so the reading can be fixed for this app if it reports a title some other way.
                _tv_front["raw_logged"] = pkg
                bits = [l.strip() for l in media_out.splitlines() if pkg in l or "metadata" in l.lower() or "description" in l
                        or "PlaybackState" in l or "TITLE" in l][:25]
                log.info("TV front app: no title found for %s. The TV's media sessions said: %s", pkg, " | ".join(bits)[:1500] or "(nothing)")
            if pkg != prev["pkg"] or media.get("title") != prev["title"] or inp != prev["input"]:
                log.info("TV front app: %s%s%s", act, f" playing {media['title']!r} ({media.get('sub') or '-'}, state {media.get('state')})" if media.get("title") else "",
                         f" showing {inp}" if inp else "")
            _tv_front.update({"pkg": pkg, "activity": act, "t": now, "ok_t": now, "error": "", "media": media, "input": inp})
        else:
            # A failed check keeps the last answer (a busy TV answers slowly now and then); it only goes after 3 minutes.
            why = (out or "no answer").strip()
            if not _tv_front.get("error"):   # once when it stops answering (e.g. the TV is off), not every 15 seconds
                log.info("TV front app: couldn't tell (%s)", why.splitlines()[-1][:200] if why else "no answer")
            _tv_front.update({"t": now, "error": why[-300:] or "no answer"})
    except Exception as exc:  # noqa: BLE001 - a background check; never let it die quietly
        log.info("TV front app check failed: %s", exc)
        _tv_front.update({"t": time.time(), "error": str(exc)[:300]})
    finally:
        _tv_front["busy"] = False


def tv_front(max_age: float = 15) -> dict | None:
    """{"pkg", "app", "kodi", "home", "hal"} for the app on the TV's screen, or None when it isn't known (yet).
    Returns the last answer at once and refreshes it in the background."""
    if not _tv_front["busy"] and time.time() - _tv_front["t"] > max_age:
        _tv_front["busy"] = True
        threading.Thread(target=_tv_front_refresh, daemon=True).start()
    if not _tv_front["pkg"] or time.time() - _tv_front["ok_t"] > 180:
        return None
    pkg, act = _tv_front["pkg"], _tv_front["activity"]
    hal = act.endswith("HalActivity")
    home = (pkg == TV_PACKAGE and not hal) or TV_APPS.get(pkg) == "Home" or "Dream" in act
    inp = _tv_front.get("input") or ""
    if inp:
        home = False
    name = inp if inp else TV_APPS.get(pkg) or ("HAL" if hal else "Home" if home else pkg.rsplit(".", 1)[-1].replace("_", " ").title())
    media = _tv_front.get("media") or {}
    title = media.get("title", "") if media.get("title", "").strip().lower() not in ("", name.lower(), pkg.lower()) else ""
    return {"pkg": pkg, "app": name, "kodi": pkg == "org.xbmc.kodi", "home": home, "hal": hal,
            "title": title, "sub": media.get("sub", "") if title else "", "paused": bool(title) and media.get("state") == 2}


# ---- Opening apps on the TV by voice: "open YouTube", "play cat videos on YouTube" ----
TV_VOICE_APPS = {
    "youtube": ("com.google.android.youtube.tv", "YouTube", "https://www.youtube.com/results?search_query={q}"),
    "netflix": ("com.netflix.ninja", "Netflix", "https://www.netflix.com/search?q={q}"),
    "prime": ("com.amazon.amazonvideo.livingroom", "Prime Video", "https://app.primevideo.com/search?phrase={q}"),
}
# Other ways to hand an app a search, tried in turn after the web link (TV apps differ in what they accept).
TV_SEARCH_LINKS = {
    "youtube": ["vnd.youtube://results?search_query={q}"],
    "netflix": ["nflx://www.netflix.com/search?q={q}", "netflix://search?q={q}", "https://www.netflix.com/search/{q}"],
    "prime": ["aiv://aiv/search?phrase={q}", "https://www.primevideo.com/search/ref=atv_nb_sr?phrase={q}"],
}
_APP_WORDS = r"(?P<app>you ?tube|net ?flix|amazon prime video|amazon prime|prime video|amazon video|prime)"


def _app_key(word: str) -> str:
    w = word.replace(" ", "")
    return "youtube" if w == "youtube" else "netflix" if w == "netflix" else "prime"


def voice_app(q: str) -> tuple[str, str] | None:
    """('youtube', 'cat videos') for 'play cat videos on YouTube'; ('netflix', '') for 'open Netflix'; None otherwise."""
    s = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", str(q or "").lower())).strip()
    s = re.sub(r"^(?:ok |hey )?(?:hal )?(?:please |can you |could you )?", "", s)
    s = re.sub(r" please$", "", s)
    if not re.search(_APP_WORDS, s):
        return None
    pats = [
        r"^(?:open|start|launch|go to|switch to|bring up|put on|turn on|fire up|load)? ?(?:the )?" + _APP_WORDS + r"(?: app)?(?: on the (?:tv|television))?$",
        r"^(?:play|watch|search for|search|find|look up|look for|put on|show me|show)\s+(?P<q>.+?)\s+(?:on|in|from)\s+(?:the )?" + _APP_WORDS + r"(?: app)?$",
        r"^(?:search|look on|look in|search on)\s+(?:the )?" + _APP_WORDS + r"\s+for\s+(?P<q>.+)$",
        r"^(?:open|start|go to)\s+(?:the )?" + _APP_WORDS + r"\s+and\s+(?:search for|search|find|look up|play|watch)\s+(?P<q>.+)$",
    ]
    for pat in pats:
        m = re.match(pat, s)
        if m:
            query = (m.groupdict().get("q") or "").strip()
            if query in ("something", "anything", "it", "stuff"):
                query = ""
            return _app_key(m.group("app")), query
    return None


TV_GLOBAL_SEARCH_APPS = {"netflix", "prime"}   # never sent a guessed search (they accept one and ignore it)
TV_SEARCH_FILE = BASE_DIR / "tv_search_methods.json"   # written by tv-launcher/try-tv-search.ps1


def tv_search_method(app: str) -> str:
    """The search command try-tv-search.ps1 found works for this app on this TV ("" if none). Only a plain
    'am start' with {q}/{qs} is used, nothing the TV's shell could read as a second command."""
    try:
        way = str((json.loads(TV_SEARCH_FILE.read_text(encoding="utf-8-sig")) or {}).get(app) or "")
    except (OSError, ValueError, AttributeError):
        return ""
    if not way.startswith("am start ") or re.search(r"[;&|`$<>\n\\]", way):
        return ""
    return way
_tv_app_diagnosed: set = set()


def _tv_app_diagnose(app: str, pkg: str) -> None:
    """Once per app: what links and actions it says it accepts, in the log, so its search can be fixed."""
    if app in _tv_app_diagnosed:
        return
    _tv_app_diagnosed.add(app)
    try:
        ok, out = tv_adb(f"dumpsys package {pkg} | grep -iE 'scheme|authority|action|Activity Resolver' | head -60", timeout=20)
        lines = sorted({l.strip() for l in (out or "").splitlines() if l.strip()})
        log.info("TV app %s: no search link was taken. It accepts: %s", app, " | ".join(lines)[:2500] or "(nothing listed)")
    except Exception as exc:  # noqa: BLE001
        log.info("TV app %s: couldn't read what it accepts (%s)", app, exc)


def tv_app(hal, app: str, query: str = "") -> dict:
    """Opens YouTube / Netflix / Prime Video on the TV, searching for `query` when there is one. Turns the TV on first
    when it can't be reached, and opens the app once it answers."""
    pkg, label, search = TV_VOICE_APPS[app]
    name = hal.settings["name"]
    if hal.monitor.__class__.__name__ == "DemoMonitor":
        return {"ok": True, "message": "Demo mode: nothing was sent to the TV."}
    query = re.sub(r"\s+", " ", query).strip()[:120]

    def taken(ok: bool, out: str) -> bool:
        return ok and "Error" not in out and "unable to resolve" not in out.lower() and "does not exist" not in out

    def launch() -> tuple[bool, str]:
        if query and (app in TV_GLOBAL_SEARCH_APPS or tv_search_method(app)):
            # The way try-tv-search.ps1 found works on this TV: the app's own, else (Netflix, Prime) the TV's search.
            qq, qs = urllib.parse.quote(query, safe=""), query.replace("'", "")
            own = tv_search_method(app)
            way = own or tv_search_method("global")
            if not way:
                ok, out = tv_adb(f"monkey -p {pkg} -c android.intent.category.LEANBACK_LAUNCHER 1", timeout=20)
                if not ok:
                    return False, out
                return True, (f"I opened {label}. I can't search it yet: run try-tv-search.ps1 on the PC once, "
                              f"and I'll know how.")
            ok, out = tv_adb(way.replace("{q}", qq).replace("{qs}", qs), timeout=20)
            if not ok:
                return False, out
            if taken(ok, out):
                return True, (f"Searching {label} for {query}." if own else
                              f"Searching the TV for {query}. Pick it with the remote and it opens in {label}.")
            log.info("TV app %s: the saved search way was refused (%s)", app, (out or "")[-160:])
        elif query:
            qq = urllib.parse.quote(query, safe="")
            qs = query.replace("'", "")   # for the shell's single quotes
            tries = [f"am start -a android.intent.action.VIEW -d '{u.format(q=qq)}' {pkg}"
                     for u in [search] + TV_SEARCH_LINKS.get(app, [])]
            tries.append(f"am start -a android.intent.action.SEARCH --es query '{qs}' {pkg}")
            tries.append(f"am start -a android.intent.action.SEARCH --es query '{qs}' -p {pkg}")
            unreachable = False
            for i, cmd in enumerate(tries):
                ok, out = tv_adb(cmd, timeout=20)
                if not ok:
                    unreachable = True
                    break
                if taken(ok, out):
                    if i:
                        log.info("TV app %s: search taken by try %d (%s)", app, i + 1, cmd.split(" -d ")[-1][:80] if " -d " in cmd else "SEARCH intent")
                    return True, f"Searching {label} for {query}."
            if not unreachable:
                _tv_app_diagnose(app, pkg)
                # Last resort: the TV's own search, which lists the app's results (pick one with the remote).
                ok, out = tv_adb(f"am start -a android.search.action.GLOBAL_SEARCH --es query '{qs}'", timeout=20)
                if taken(ok, out):
                    return True, f"{label} wouldn't take a search, so I've searched the TV for {query}. Pick it with the remote."
        ok, out = tv_adb(f"monkey -p {pkg} -c android.intent.category.LEANBACK_LAUNCHER 1", timeout=20)
        if not ok:
            return False, out
        if "No activities found" in out or "not found" in out.lower():
            return False, f"{label} isn't installed on the TV."
        if query:
            return True, f"I opened {label}, but it wouldn't take the search. You'll have to look for {query} yourself."
        return True, f"Opening {label}."

    ok, msg = launch()
    if ok:
        log.info("TV app: %s%s", label, f" search {query!r}" if query else "")
        return {"ok": True, "message": msg}
    if "isn't installed" in msg:
        return {"ok": False, "message": msg}
    # The TV didn't answer: probably off. Turn it on (no Kodi), and open the app once it's up.
    r = _phone_post(hal, "/api/tv/on", {"kodi": False}, timeout=15)
    if not r.get("ok"):
        return {"ok": False, "message": f"I couldn't reach the TV, {name}."}

    def later():
        end = time.time() + 60
        while time.time() < end:
            time.sleep(6)
            done, _m = launch()
            if done:
                log.info("TV app: %s opened after turning the TV on", label)
                return
        log.info("TV app: %s - the TV didn't answer after turning it on", label)

    threading.Thread(target=later, daemon=True, name="tv-app").start()
    return {"ok": True, "message": f"Turning the TV on, {name}. {label} will open in a moment."}


TV_KEYS = {"vol_up": 24, "vol_down": 25, "mute": 164}


def tv_key(hal, key: str, steps: int = 1) -> dict:
    """The TV's own volume: up, down (a few steps at once when tapped quickly) or mute. Works whatever app is on."""
    name = hal.settings["name"]
    if key not in TV_KEYS:
        raise ValueError("Volume up, down or mute.")
    steps = 1 if key == "mute" else max(1, min(10, int(steps or 1)))
    if hal.monitor.__class__.__name__ == "DemoMonitor":
        return {"ok": True, "message": "Demo mode: nothing was sent to the TV."}
    ok, out = tv_adb("input keyevent " + " ".join([str(TV_KEYS[key])] * steps), timeout=15)
    if not ok:
        why = out.splitlines()[-1] if out else "no answer"
        log.info("TV key failed: %s", out[-200:])
        return {"ok": False, "message": f"I couldn't reach the TV, {name}: {why}"}
    return {"ok": True, "message": {"vol_up": "Louder.", "vol_down": "Quieter.", "mute": "Mute on or off."}[key]}


def tv_show(hal, say: str = "") -> dict:
    """Opens HAL's screen on the TV (over whatever is on), optionally saying a message."""
    name = hal.settings["name"]
    say = re.sub(r"\s+", " ", str(say or "")).strip()[:300]
    if hal.monitor.__class__.__name__ == "DemoMonitor":
        return {"ok": True, "message": "Demo mode: nothing was sent to the TV."}
    cmd = f"am start -n {TV_PACKAGE}/.HalActivity -f 0x10000000"
    if say:
        cmd += " --es say_b64 " + base64.b64encode(say.encode("utf-8")).decode("ascii")
    ok, out = tv_adb(cmd)
    if ok and ("Error" in out or "does not exist" in out):
        ok = False
    if not ok:
        why = out.splitlines()[-1] if out else "no answer"
        if "does not exist" in out or "Unable to resolve" in out:
            why = "the TV has an older app. Run Update everything first."
        log.info("TV show failed: %s", out[-200:])
        return {"ok": False, "message": f"I couldn't reach the TV, {name}: {why}"}
    with hal.lock:
        hal.tv_seen = time.time()
    log.info("TV: HAL shown%s", " with a message" if say else "")
    return {"ok": True, "message": (f"On the TV now, {name}. He'll say it in a moment." if say else f"I'm on the TV now, {name}.")}


def _phone_post(hal, path: str, body: dict, timeout: float = 20) -> dict:
    """A POST to the NZB phone server HAL lives in; {"ok": False, "message": ...} if it can't be reached."""
    port = int(hal.settings.get("phone_port") or 8765)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                                     headers={"X-Mobile-Token": _token_bytes.decode(), "Content-Type": "application/json"}, method="POST")
        with _OPENER.open(req, timeout=timeout) as resp:
            return json.loads(resp.read(200_000).decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return {"ok": False, "message": json.loads(exc.read(4000).decode("utf-8", "replace")).get("error") or f"HTTP {exc.code}"}
        except ValueError:
            return {"ok": False, "message": f"HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"I couldn't reach the phone server: {str(exc)[:80]}"}


def tv_on_via_phone_server(hal) -> str:
    """'Turn the TV on' goes through the NZB phone server (it knows the TV)."""
    port = int(hal.settings.get("phone_port") or 8765)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/tv/on", data=json.dumps({"kodi": True}).encode(),
                                     headers={"X-Mobile-Token": _token_bytes.decode(), "Content-Type": "application/json"}, method="POST")
        with _OPENER.open(req, timeout=15) as resp:
            resp.read(2000)
        return f"Turning the TV on and opening Kodi, {hal.settings['name']}."
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't reach the TV: {str(exc)[:80]}"


class Hal(FunMixin):
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
        # Alerts you hid ("Hide 24 h"): key -> {"until", "level", "title"}. They come back when the time is up,
        # when they get worse, or when the problem goes away and later returns.
        self.snoozed: dict[str, dict] = {k: v for k, v in (st.get("snoozed") or {}).items() if isinstance(v, dict)}
        self.update_seen = to_float(st.get("update_seen"), 0) or 0
        # Devices Pi-hole has seen: id -> {"name", "ip", "first"}; new ones are flagged for a day.
        self.known_devices: dict[str, dict] = {k: v for k, v in (st.get("known_devices") or {}).items() if isinstance(v, dict)}
        self.new_devices: dict[str, dict] = {k: v for k, v in (st.get("new_devices") or {}).items() if isinstance(v, dict)}
        self.devices_seeded = bool(st.get("devices_seeded"))
        self.pihole_found = str(st.get("pihole_found") or "")     # where the Pi-hole was last found (no address set)
        self.kuma: dict = {}
        self._counters_init(st)
        self.tv_seen = 0.0                  # when HAL's TV screen last asked for readings
        self.kuma_hist: dict[str, deque] = {}   # monitor -> its last statuses, an hour of them (for the TV's details)
        self.kuma_ms: dict[str, deque] = {}     # monitor -> its last hour of response times (ms, None when down)
        self.kuma_down_t: dict[str, float] = {}  # monitor -> when it went down
        self.pi_temp_hist: deque = deque(maxlen=120)
        self.pi_cpu_hist: deque = deque(maxlen=120)
        self.tv_phrases: list[str] = [str(x)[:120] for x in (st.get("tv_phrases") or []) if str(x).strip()][:TV_PHRASES_MAX]
        self._snooze_gone: dict[str, float] = {}
        self.services: dict[str, dict] = {}
        self.devices: list[dict] = []
        self.net: dict = {}
        self.security: dict = {}
        self.updates: dict = {}
        self.ups: dict | None = None
        self.ups_error = ""
        self.cleanup: dict = {}
        self.ph: dict = {}                    # Pi-hole: what the last check found
        self.pihole: PiHole | None = None
        self._ph_search = 0.0
        self.winevents: dict = {"events": [], "t": 0.0, "busy": False}
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
    def report_update(self) -> None:
        """After an update from the phone: says once (in the log) how it went."""
        r = load_json(UPDATE_RESULT_FILE, {})
        t = to_float(r.get("t"), 0) or 0
        if not t or t <= self.update_seen:
            return
        self.update_seen = t
        files = [str(f) for f in r.get("files") or [] if f]
        state, msg = r.get("state"), str(r.get("message") or "")
        if state == "updated":
            self.add_event("ok", f"Updated to phone app {r.get('version') or '?'}" + (f" and HAL {r['hal_version']}" if r.get("hal_version") else ""),
                           f"{len(files)} file{'s' if len(files) != 1 else ''}: " + ", ".join(files[:8]) + ("…" if len(files) > 8 else ""))
        elif state == "uptodate":
            self.add_event("info", "Already up to date", "The update found nothing new.")
        else:
            self.add_event("warning", "The update didn't work", msg or "See update_all.log in the NZB Command Center folder.", push=True)
        self.save()

    def start(self) -> None:
        try:
            self.report_update()
        except Exception:  # noqa: BLE001
            log.exception("Reading update_result.json")
        if self.monitor is None:
            ms = dict(sm.DEFAULT_SETTINGS)
            ms.update({k: self.settings[k] for k in ("cpu_alert", "ram_alert", "cpu_temp_alert", "gpu_temp_alert",
                                                      "drive_free_alert", "disk_temp_alert", "ping_host")})
            ms["toasts"] = False
            ms["ping_enabled"] = True
            self.monitor = sm.SystemMonitor(ms)
            self.monitor.start()
        for fn, name in ((self._service_loop, "services"), (self._network_loop, "network"), (self._power_loop, "power"),
                         (self._slow_loop, "slow"), (self._history_loop, "history"), (self._pihole_loop, "pihole"),
                         (self._kuma_loop, "kuma")):
            threading.Thread(target=self._guard(fn), name=f"hal-{name}", daemon=True).start()

    def stop(self) -> None:
        self.stop_evt.set()
        if self.pihole:
            self.pihole.logout()
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
                    "launch": self.launch, "power_log": self.power_log[-50:], "snoozed": self.snoozed,
                    "update_seen": self.update_seen, "known_devices": dict(list(self.known_devices.items())[-1000:]),
                    "new_devices": self.new_devices, "devices_seeded": self.devices_seeded, "pihole_found": self.pihole_found,
                    "counters": self.counters, "brief_sent": self.brief_sent, "tv_phrases": self.tv_phrases}
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
    # ---- Pi-hole ----
    PH_EVERY = 30
    PH_SEARCH_EVERY = 600

    def _pihole_loop(self) -> None:
        while not self.stop_evt.is_set():
            self.check_pihole()
            self.stop_evt.wait(self.PH_EVERY)

    def _pihole_client(self) -> PiHole | None:
        """The Pi-hole to watch: the address in settings, else one found among the PC's DNS servers."""
        s = self.settings
        if s.get("pihole_off"):
            return None
        pw = dpapi_unprotect(s.get("pihole_password") or "")
        try:
            base = pihole_base(s.get("pihole_url") or "")
        except ValueError:
            base = ""
        cur = self.pihole
        if cur and (not base or cur.base == base):
            cur.password = pw
            return cur
        if not base:      # where it was last time, else look for it (not every 30 s)
            if self.pihole_found:
                v = pihole_detect(self.pihole_found)
                if v:
                    self.pihole = PiHole(self.pihole_found, pw, v)
                    return self.pihole
            if time.time() - self._ph_search < self.PH_SEARCH_EVERY:
                return None
            self._ph_search = time.time()
            for ip in dns_servers():
                v = pihole_detect(f"http://{ip}")
                if v:
                    self.pihole = PiHole(f"http://{ip}", pw, v)
                    log.info("Found Pi-hole %s at %s", v, ip)
                    if self.pihole_found != self.pihole.base:
                        self.pihole_found = self.pihole.base
                        self.save()
                    return self.pihole
            return None
        v = pihole_detect(base)
        if not v:
            raise OSError(f"Nothing at {base} answers like a Pi-hole.")
        if cur:
            cur.logout()
        self.pihole = PiHole(base, pw, v)
        return self.pihole

    def check_pihole(self) -> None:
        now = time.time()
        prev = self.ph
        try:
            ph = self._pihole_client()
            if ph is None:
                with self.lock:
                    if self.settings.get("pihole_off"):
                        self.ph = {}
                    elif prev.get("found"):
                        self.ph = {**prev, "t": now}
                    elif self.settings.get("pihole_password") or self.pihole_found:
                        # You told me there's one (or I found it before): say so instead of quietly showing nothing.
                        self.ph = {"found": True, "ok": False, "need_password": False, "missing": True, "fails": 0, "t": now, "base": self.pihole_found,
                                   "error": "I can't find the Pi-hole. Type its address in Settings › Connections."}
                    else:
                        self.ph = {}
                return
            summ = ph.summary()
            self.track_devices(summ.pop("all_clients", []), now)
            self.record_ads(to_int(summ.get("blocked")))
            if (summ.get("hw") or {}).get("temp") is not None:
                self.pi_temp_hist.append(round(summ["hw"]["temp"], 1))
            if (summ.get("hw") or {}).get("cpu_pct") is not None:
                self.pi_cpu_hist.append(round(summ["hw"]["cpu_pct"], 1))
            state = {"found": True, "ok": True, "base": ph.base, "version": ph.version, "fails": 0, "error": "",
                     "need_password": False, "t": now, **summ}
        except PiHoleAuthError as exc:
            state = {**prev, "found": True, "ok": False, "need_password": True, "missing": False, "error": str(exc), "fails": 0, "t": now,
                     "base": self.pihole.base if self.pihole else prev.get("base", ""), "version": self.pihole.version if self.pihole else 0}
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            if not prev.get("found") and not self.settings.get("pihole_url"):
                return
            state = {**prev, "found": True, "ok": False, "need_password": False, "missing": False, "error": str(exc)[:160] or "No answer.",
                     "fails": to_int(prev.get("fails")) + 1, "t": now}
        with self.lock:
            self.ph = state
        if prev.get("fails", 0) < 2 <= state.get("fails", 0):
            self.add_event("critical", "Pi-hole isn't answering",
                           f"Websites may stop loading on every device in the house, {self.settings['name']}. Check the Raspberry Pi has power.", push=True)
        elif prev.get("fails", 0) >= 2 and state.get("ok"):
            self.add_event("ok", "Pi-hole is back", "Ad blocking is answering again.", push=bool(self.settings.get("push_recoveries")))

    def pihole_view(self) -> dict:
        ph = dict(self.ph)
        if not ph.get("found"):
            return {"found": False, "off": bool(self.settings.get("pihole_off"))}
        timer = ph.get("timer")
        if timer and ph.get("t"):
            timer = max(0, timer - (time.time() - ph["t"]))
        return {"found": True, "ok": ph.get("ok", False), "version": ph.get("version"), "host": urllib.parse.urlsplit(ph.get("base", "")).hostname or "",
                "error": ph.get("error", ""), "need_password": ph.get("need_password", False), "missing": bool(ph.get("missing")),
                "fails": ph.get("fails", 0),
                "blocking": ph.get("blocking"), "timer": round(timer) if timer else None,
                **{k: ph.get(k) for k in ("queries", "blocked", "percent", "clients", "domains", "gravity_t", "top_blocked", "top_allowed", "top_clients", "t", "hw", "hourly")}}

    NEW_DEVICE_HOURS = 24

    def track_devices(self, clients: list, now: float | None = None) -> list[dict]:
        """Devices Pi-hole saw that HAL hasn't before. The first time, they're all just remembered."""
        now = time.time() if now is None else now
        fresh = []
        with self.lock:
            for name, ip in clients or []:
                name, ip = str(name or "").strip()[:80], str(ip or "").strip()[:60]
                if not ip and not name:
                    continue
                did = (name if name and name != ip else ip).lower()
                if did in self.known_devices:
                    self.known_devices[did]["ip"] = ip or self.known_devices[did].get("ip", "")
                    continue
                self.known_devices[did] = {"name": name if name != ip else "", "ip": ip, "first": now}
                if self.devices_seeded:
                    dev = {"id": did, "name": name if name != ip else "", "ip": ip, "t": now}
                    self.new_devices[did] = dev
                    fresh.append(dev)
            self.devices_seeded = self.devices_seeded or bool(clients)
            for k, v in list(self.new_devices.items()):
                if now - to_float(v.get("t"), now) > self.NEW_DEVICE_HOURS * 3600:
                    del self.new_devices[k]
        for dev in fresh:
            self.add_event("warning", "A new device joined your network",
                           f"{dev['name'] or 'A device with no name'} ({dev['ip']}). If you don't recognise it, "
                           f"change the Wi-Fi password, {self.settings['name']}.", push=True)
        if fresh:
            self.save()
        return fresh

    def device_ok(self, did: str) -> bool:
        with self.lock:
            gone = self.new_devices.pop(str(did).lower(), None)
        if gone:
            self.save()
        return bool(gone)

    def _kuma_loop(self) -> None:
        while not self.stop_evt.is_set():
            self.check_kuma()
            self.stop_evt.wait(30)

    def check_kuma(self) -> None:
        s = self.settings
        try:
            base = kuma_base(s.get("kuma_url") or "")
        except ValueError:
            base = ""
        if not base:
            with self.lock:
                self.kuma = {}
            return
        prev, now = self.kuma, time.time()
        try:
            mons = kuma_fetch(base, dpapi_unprotect(s.get("kuma_key") or ""))
            state = {"found": True, "ok": True, "base": base, "monitors": mons, "fails": 0, "error": "", "need_key": False, "t": now}
        except KumaAuthError as exc:
            state = {**prev, "found": True, "ok": False, "base": base, "need_key": True, "error": str(exc), "fails": 0, "t": now}
        except (OSError, ValueError) as exc:
            state = {**prev, "found": True, "ok": False, "base": base, "need_key": False, "error": str(exc)[:160] or "No answer.",
                     "fails": to_int(prev.get("fails")) + 1, "t": now}
        with self.lock:
            self.kuma = state
            if state.get("ok"):
                for m in state["monitors"]:
                    self.kuma_hist.setdefault(m["name"], deque(maxlen=120)).append(m["status"])
                    self.kuma_ms.setdefault(m["name"], deque(maxlen=120)).append(m.get("ms") if m["status"] == "up" else None)
                    if m["status"] == "up":
                        self.kuma_down_t.pop(m["name"], None)
                    else:
                        self.kuma_down_t.setdefault(m["name"], now)
        if state.get("ok"):
            was = {m["name"]: m["status"] for m in prev.get("monitors") or []} if prev.get("ok") else {}
            for m in state["monitors"]:
                if was.get(m["name"]) == "up" and m["status"] == "down":
                    self.add_event("warning", f"{m['name']} is down", "Uptime Kuma says it stopped answering.", push=True)
                elif was.get(m["name"]) == "down" and m["status"] == "up":
                    self.add_event("ok", f"{m['name']} is back up", "Uptime Kuma says it's answering again.",
                                   push=bool(self.settings.get("push_recoveries")))

    def kuma_view(self) -> dict:
        k = dict(self.kuma)
        if not k.get("found"):
            return {"found": False}
        mons = k.get("monitors") or []
        return {"found": True, "ok": k.get("ok", False), "host": urllib.parse.urlsplit(k.get("base", "")).netloc,
                "error": k.get("error", ""), "need_key": k.get("need_key", False), "fails": k.get("fails", 0), "t": k.get("t"),
                "monitors": mons, "up": sum(1 for m in mons if m["status"] == "up"), "down": sum(1 for m in mons if m["status"] == "down")}

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
            self.note_power_cut()
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
            try:
                self.check_briefings()
                self.note_uptime((self.monitor.snapshot() or {}).get("uptime") if self.monitor else None)
            except Exception:  # noqa: BLE001
                log.exception("Briefing")
            try:
                self.report_update()       # the update writes its result after restarting this server
            except Exception:  # noqa: BLE001
                pass
            if IS_WINDOWS and now - last_sec >= 900:
                last_sec = now
                res = sm.run_powershell_json(SECURITY_PS, timeout=120)
                if isinstance(res, dict):
                    with self.lock:
                        self.security = {**res, "t": now}
                self.refresh_winevents(wait=True)
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
               "gpu": avg(f"gpu:{gpus[0]['key']}", 60) if gpus else None, "ping": avg("ping", 15), "net_down": avg("net_down", 60),
               "net_up": avg("net_up", 60)}
        dr, dw = avg("disk_read", 60), avg("disk_write", 60)
        row["disk"] = round((dr or 0) + (dw or 0), 1) if (dr is not None or dw is not None) else None
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

    def traffic_24h(self) -> dict:
        """Data moved in the last 24 hours (bytes down / up) and the busiest minute's speed and when it was."""
        with self.lock:
            down, up = list(self.minutes["net_down"]), list(self.minutes["net_up"])
        tot = lambda vals: sum((v or 0) * 60 for v in vals)
        best = max(range(len(down)), key=lambda i: down[i] or 0, default=None)
        peak = (down[best] or 0) if best is not None else 0
        return {"down": tot(down), "up": tot(up), "peak": peak,
                "peak_t": time.time() - (len(down) - 1 - best) * 60 if best is not None and peak else None}

    @staticmethod
    def _mean(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    # ---- Windows event log ----
    def disk_map(self) -> dict:
        inv = (self.monitor.snapshot() if self.monitor else {}).get("inventory") or {}
        out = {}
        for d in inv.get("disks") or []:
            out[str(d.get("id"))] = {"name": d.get("name") or "", "letters": []}
        for p in inv.get("parts") or []:
            out.setdefault(str(p.get("disk")), {"name": "", "letters": []})["letters"].append(str(p.get("letter")).upper() + ":")
        return out

    def refresh_winevents(self, wait: bool = False) -> None:
        if not IS_WINDOWS or self.demo:
            return
        with self.lock:
            if self.winevents.get("busy"):
                return
            self.winevents["busy"] = True

        def run():
            try:
                raw = sm.run_powershell_json(EVENTLOG_PS, timeout=180)
                if raw is not None:
                    evs = parse_winevents(raw, self.disk_map())
                    with self.lock:
                        self.winevents = {"events": evs, "t": time.time(), "busy": False}
            finally:
                with self.lock:
                    self.winevents["busy"] = False
        if wait:
            run()
        else:
            threading.Thread(target=run, daemon=True).start()

    def winevents_view(self) -> dict:
        with self.lock:
            w = dict(self.winevents)
        return {"events": w.get("events", [])[:400], "t": w.get("t"), "busy": w.get("busy"), "windows": IS_WINDOWS or self.demo,
                "summary": event_summary(w.get("events", [])), "cats": EVENT_CATS}

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
        for who, n in event_summary(self.winevents.get("events", []), now).get("serious_drives", {}).items():
            add(f"evdrive:{who}", "warning", f"{who[:1].upper() + who[1:]} logged {n} error{'s' if n > 1 else ''} this week",
                "Windows recorded drive errors. Open the Windows events page to see what they mean.", "events")
        if (sec.get("unexpected7") or 0) >= 1:
            add("unexpected", "warning", "The PC shut down unexpectedly this week", f"{sec['unexpected7']} time(s) in 7 days.", "shield")
        if snap.get("pending_reboot"):
            add("reboot", "info", "Windows wants a restart", "Updates finish installing after a restart.", "shield")
        upd = self.updates.get("updates") or []
        if upd:
            add("updates", "info", f"{len(upd)} Windows update{'s are' if len(upd) > 1 else ' is'} available", "", "shield")
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
        ph = self.ph
        if ph.get("found"):
            if ph.get("fails", 0) >= 2:
                add("pihole", "critical", "Pi-hole isn't answering",
                    "Websites may stop loading on every device in the house. Check the Raspberry Pi has power.", "shield")
            elif ph.get("missing"):
                add("pihole_pw", "warning", "I can't find the Pi-hole", "Type its address (the Raspberry Pi's IP) in Settings › Connections.", "shield")
            elif ph.get("need_password"):
                add("pihole_pw", "info", "I can't sign in to Pi-hole", (ph.get("error") or "") + " Add it in Settings › Connections.", "shield")
            elif ph.get("ok") and ph.get("blocking") is False and not ph.get("timer"):
                add("pihole_off", "warning", "Pi-hole's ad blocking is off", "Turn it back on from the Pi-hole row on the System page.", "shield", 120)
            elif ph.get("ok") and ph.get("gravity_t") and now - ph["gravity_t"] > 14 * 86400:
                add("pihole_lists", "info", f"Pi-hole's block lists are {(now - ph['gravity_t']) / 86400:.0f} days old",
                    "They normally update every week. On the Pi: pihole -g", "shield")
        hw = (ph.get("hw") or {}) if ph.get("found") and ph.get("ok") else {}
        if hw.get("temp") is not None and hw.get("temp_unit", "C") == "C" and hw["temp"] >= 75:
            add("pi_temp", "critical" if hw["temp"] >= 82 else "warning", "The Raspberry Pi is running hot",
                f"{hw['temp']:.0f}°C. It slows itself down above 80°C. Check its fan and that air can get to it.", "shield", 120)
        if hw.get("mem_pct") is not None and hw["mem_pct"] >= 92:
            add("pi_mem", "warning", "The Raspberry Pi's memory is almost full", f"{hw['mem_pct']:.0f}% used.", "shield", 300)
        if hw.get("update"):
            add("pi_update", "info", "A Pi-hole update is available", "On the Pi: sudo pihole -up", "shield")
        for dev in list(self.new_devices.values()):
            add(f"newdev:{dev['id']}", "warning", "A new device joined your network",
                f"{dev['name'] or 'No name'} · {dev['ip']} · first seen {datetime.fromtimestamp(dev['t']).strftime('%a %I:%M %p').replace(' 0', ' ')}. "
                "If you don't recognise it, change the Wi-Fi password.", "shield")
        km = self.kuma
        if km.get("found"):
            if km.get("fails", 0) >= 2:
                add("kuma", "warning", "I can't reach Uptime Kuma", km.get("error") or "", "services", 60)
            elif km.get("need_key"):
                add("kuma_key", "info", "Uptime Kuma needs an API key", "Uptime Kuma › Settings › API Keys, then Settings › Connections here.", "services")
            elif km.get("ok"):
                for m in km.get("monitors") or []:
                    if m["status"] == "down":
                        add(f"kuma:{m['name']}", "warning", f"{m['name']} is down", "Uptime Kuma says it isn't answering.", "services", 60)
                    elif m.get("cert_days") is not None and 0 <= m["cert_days"] <= 7:
                        add(f"kcert:{m['name']}", "info", f"{m['name']}'s certificate runs out in {m['cert_days']} days", "", "services")
        return issues

    # ---- hiding alerts for a while ----
    SNOOZE_GRACE = 600   # an issue must be gone this long before its snooze is dropped (flapping network checks)

    def apply_snoozes(self, issues: list[dict], now: float | None = None) -> tuple[list[dict], list[dict]]:
        """(showing, hidden). Drops a snooze when its time is up, when the issue got worse, or when the issue
        has been gone for a while (so it shows again if it comes back)."""
        now = time.time() if now is None else now
        current = {i["key"]: i for i in issues}
        with self.lock:
            for k, sn in list(self.snoozed.items()):
                i = current.get(k)
                if i is None:
                    gone = self._snooze_gone.setdefault(k, now)
                    if now - gone >= self.SNOOZE_GRACE or now >= sn.get("until", 0):
                        self.snoozed.pop(k, None)
                        self._snooze_gone.pop(k, None)
                    continue
                self._snooze_gone.pop(k, None)
                if now >= sn.get("until", 0) or LEVELS[i["level"]] > LEVELS.get(sn.get("level"), 1):
                    self.snoozed.pop(k, None)
                    self.announced.pop(k, None)      # it's back: tell the phone again
                    self.cooldown.pop(k, None)
            showing = [i for i in issues if i["key"] not in self.snoozed]
            hidden = [{**i, "until": self.snoozed[i["key"]]["until"]} for i in issues if i["key"] in self.snoozed]
        return showing, hidden

    def snooze(self, keys: list[str], hours: float = 24) -> int:
        snap = self.monitor.snapshot() if self.monitor else {}
        current = {i["key"]: i for i in self.compute_issues(snap, self.drives(snap))}
        until = time.time() + hours * 3600
        n = 0
        with self.lock:
            for k in keys:
                if k in current:
                    self.snoozed[k] = {"until": until, "level": current[k]["level"], "title": current[k]["title"]}
                    n += 1
        if n:
            log.info("Hid %d alert(s) for %s h", n, hours)
            self.save()
        return n

    def unsnooze(self, keys: list[str]) -> int:
        with self.lock:
            n = sum(1 for k in keys if self.snoozed.pop(k, None) is not None)
        if n:
            self.save()
        return n

    def evaluate(self) -> list[dict]:
        """Turns the current issues into log entries and phone alerts (after their sustain time),
        and reports recoveries. Returns the issues that are showing now."""
        if not self.monitor:
            return []
        snap = self.monitor.snapshot()
        issues = self.compute_issues(snap, self.drives(snap))
        now = time.time()
        self.apply_snoozes(issues, now)
        keys = {i["key"] for i in issues}
        for i in issues:
            if LEVELS[i["level"]] < LEVELS["warning"]:
                continue
            start = self.pending.setdefault(i["key"], now)
            if i["key"] in self.snoozed:
                continue                                   # hidden: no phone alert until it shows again
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
        now = time.time()
        issues, hidden = self.apply_snoozes(self.compute_issues(snap, drives), now)
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
            "events": event_summary(self.winevents.get("events", []), now) if self.winevents.get("t") else None,
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
        ph = self.pihole_view()
        if ph["found"]:
            if ph["ok"]:
                pi_t = (ph.get("hw") or {}).get("temp")
                detail = (f"{ph['blocked']:,} blocked today · {ph['percent']:.0f}%" + (f" · Pi {pi_t:.0f}°C" if pi_t is not None else "")
                          if ph.get("blocking") or ph.get("blocking") is None
                          else f"Paused · back in {fmt_dur(ph['timer'])}" if ph.get("timer") else "Ad blocking is OFF")
            else:
                detail = "Needs its password" if ph["need_password"] else "Can't find it · set its address" if ph.get("missing") else "Not answering"
            systems.append({"id": "pihole", "name": "Pi-hole", "level": lvl({"pihole", "pihole_pw", "pihole_off", "pihole_lists", "pi_temp", "pi_mem", "pi_update"}),
                            "tab": "shield", "detail": detail})
        kv = self.kuma_view()
        if kv["found"]:
            systems.append({"id": "kuma", "name": "Uptime Kuma", "level": lvl({"kuma", "kuma_key", "kcert"}), "tab": "services",
                            "detail": (f"{kv['up']} up" + (f" · {kv['down']} down" if kv["down"] else "") if kv["ok"] else
                                       "Needs an API key" if kv["need_key"] else "Not answering"),
                            "count": f"{kv['up']} / {len(kv['monitors'])}" if kv["ok"] and kv["monitors"] else ""})
        name = s["name"]
        return {
            "version": VERSION, "host": socket.gethostname(), "demo": self.demo, "now": now, "uptime": snap.get("uptime"), "boot": snap.get("boot"),
            "admin": snap.get("admin"), "windows": IS_WINDOWS or self.demo,
            "hal": {"level": level, "quote": hal_quote(issues, name, now), "greeting": greeting(name), "name": name},
            "issues": [{k: v for k, v in i.items() if k != "sustain"} for i in sorted(issues, key=lambda i: -LEVELS[i["level"]])],
            "snoozed": [{k: v for k, v in i.items() if k != "sustain"} for i in sorted(hidden, key=lambda i: -LEVELS[i["level"]])],
            "systems": systems,
            "vitals": {
                "cpu": {"pct": cpu.get("total"), "temp": cpu.get("temp"), "freq": cpu.get("freq"), "per_core": cpu.get("per_core") or [],
                        "name": cpu_inv.get("name") or "", "cores": cpu_inv.get("cores"), "threads": cpu_inv.get("threads") or psutil.cpu_count()},
                "mem": {"used": mem.get("used"), "total": mem.get("total"), "pct": mem.get("percent")},
                "gpus": gpus,
                "temps": [{"name": f"{t['hw']} · {t['name']}", "value": t["value"]} for t in (sensors.get("temps") or [])][:24],
                "fans": [{"name": f["name"], "value": f["value"]} for f in (sensors.get("fans") or []) if f.get("value")][:12],
                "top_cpu": [{"name": p["name"], "cpu": p["cpu"], "mem": p["mem"]} for p in procs[:5]], "procs": len(procs),
                "top_mem": [{"name": p["name"], "cpu": p["cpu"], "mem": p["mem"]} for p in sorted(procs, key=lambda p: -p["mem"])[:5]],
                "sensor_source": snap.get("sensor_source") or "",
                "limits": {"cpu_temp": s["cpu_temp_alert"], "gpu_temp": s["gpu_temp_alert"]},
            },
            "net": net, "services": services, "devices": self.devices, "drives": drives, "security": security, "power": power,
            "unread": self.unread,
            "update": update_view(),
            "pihole": ph,
            "kuma": kv,
            "record": self.record(),
            "tvhal": {"open": now - self.tv_seen < 45},
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
    hal.ph = {"found": True, "ok": True, "base": "http://192.168.68.53", "version": 6, "fails": 0, "error": "", "need_password": False,
              "t": now, "queries": 84213, "blocked": 18204, "percent": 21.6, "clients": 14, "domains": 412806, "gravity_t": now - 2 * 86400,
              "blocking": True, "timer": None,
              "top_blocked": [["ads.example-tracker.com", 2104], ["telemetry.tv-maker.net", 1630], ["metrics.app-stats.io", 911],
                              ["pixel.adnetwork.com", 640], ["beacon.streaming-ads.tv", 402]],
              "top_allowed": [["i.ytimg.com", 2904], ["www.googleapis.com", 2311], ["api.themoviedb.org", 1288], ["time.android.com", 960],
                              ["netflix.com", 733]],
              "top_clients": [["living-room-tv", 22140], ["hal9000", 18032], ["iphone", 9311], ["192.168.68.62", 4210], ["172.17.0.2", 2004]],
              "hw": {"t": now, "model": "Raspberry Pi 4 Model B Rev 1.5", "hostname": "pihole", "temp": 47.2, "hot_limit": 60, "temp_unit": "C",
                     "cpu_pct": 3.1, "cores": 4, "load": [0.08, 0.05, 0.01], "mem_pct": 21.4, "mem_total": 3_978_000_000,
                     "uptime": 12 * 86400 + 3 * 3600, "version": "v6.1.4", "update": False},
              "hourly": [[int(2600 + 2200 * math.sin((h - 6) / 3.8) ** 2 + 120 * (h % 3)), int(600 + 520 * math.sin((h - 6) / 3.8) ** 2)]
                         for h in range(24)]}
    hal.pi_temp_hist = deque([round(46 + 1.6 * math.sin(i / 9) + 0.4 * math.sin(i / 2), 1) for i in range(60)], maxlen=120)
    hal.pi_cpu_hist = deque([round(max(0.5, 3 + 2.5 * math.sin(i / 7) + 1.2 * math.sin(i / 2.3)), 1) for i in range(60)], maxlen=120)
    hal.kuma = {"found": True, "ok": True, "base": "http://192.168.68.63:3001", "fails": 0, "error": "", "need_key": False, "t": now,
                "monitors": [{"name": "Front door camera", "type": "ping", "status": "down", "ms": None, "cert_days": None}] +
                            [{"name": n, "type": ty, "status": "up", "ms": ms, "cert_days": None} for n, ty, ms in
                             (("Pi-hole", "http", 12), ("Router", "ping", 2), ("Jellyfin", "http", 31), ("Home Assistant", "http", 44),
                              ("Kodi", "port", 9), ("Google", "http", 38), ("NAS", "ping", 1), ("Printer", "ping", 6),
                              ("SABnzbd", "http", 18), ("Sonarr", "http", 21), ("Radarr", "http", 20))]}
    hal.counters.update(first_day=(datetime.now() - timedelta(days=212)).strftime("%Y-%m-%d"), ads_before=2_310_455, power_cuts=3,
                        longest_up=41 * 86400, milestones=[10_000, 50_000, 100_000, 250_000, 500_000, 1_000_000, 2_000_000])
    hal.counters["ads_days"] = {(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d"): 18204 if i == 0 else 21000 + i * 113 for i in range(8)}
    hal._sab_cache = {"count": 1412, "total_size": "18.4 T", "week_size": "212.6 G", "day_size": "31.0 G", "slots": []}
    hal.new_devices = {"android-3f2a": {"id": "android-3f2a", "name": "android-3f2a", "ip": "192.168.68.77", "t": now - 1800}}
    hal.updates = {"ok": True, "updates": [{"title": "2026-11 Cumulative Update for Windows 11", "reboot": True}], "t": now - 3600}
    def iso(sec_ago):
        return datetime.utcfromtimestamp(now - sec_ago).isoformat() + "Z"
    raw = {"events": [
        {"t": iso(3600 * 3), "lvl": 3, "src": "disk", "id": 153, "cat": "drives", "msg": "The IO operation at logical block address 0x1d2c3e10 for Disk 2 (PDO name: \\Device\\00000034) was retried."},
        {"t": iso(3600 * 3 + 40), "lvl": 3, "src": "disk", "id": 153, "cat": "drives", "msg": "The IO operation at logical block address 0x1d2c3e18 for Disk 2 (PDO name: \\Device\\00000034) was retried."},
        {"t": iso(3600 * 27), "lvl": 2, "src": "disk", "id": 7, "cat": "drives", "msg": "The device, \\Device\\Harddisk2\\DR2, has a bad block."},
        {"t": iso(3600 * 30), "lvl": 3, "src": "storahci", "id": 129, "cat": "drives", "msg": "Reset to device, \\Device\\RaidPort0, was issued."},
        {"t": iso(86400 * 2), "lvl": 2, "src": "Ntfs", "id": 140, "cat": "drives", "msg": "The system failed to flush data to the transaction log. Corruption may occur in VolumeId: Z:, DeviceName: \\Device\\HarddiskVolume7. (A device which does not exist was specified.)"},
        {"t": iso(86400 * 3), "lvl": 3, "src": "disk", "id": 153, "cat": "drives", "msg": "The IO operation at logical block address 0x88 for Disk 2 was retried."},
        {"t": iso(86400 * 2 + 600), "lvl": 2, "src": "Service Control Manager", "id": 7031, "cat": "services", "msg": "The Jellyfin Server service terminated unexpectedly. It has done this 1 time(s)."},
        {"t": iso(86400 * 4), "lvl": 2, "src": "Application Error", "id": 1000, "cat": "apps", "msg": "Faulting application name: Sonarr.exe, version: 4.0.11.2680, time stamp: 0x6744f1e2 Faulting module name: coreclr.dll"},
        {"t": iso(86400 * 12), "lvl": 1, "src": "Microsoft-Windows-Kernel-Power", "id": 41, "cat": "crashes", "msg": "The system has rebooted without cleanly shutting down first. This error could be caused if the system stopped responding, crashed, or lost power unexpectedly."},
        {"t": iso(86400 * 12 - 60), "lvl": 2, "src": "EventLog", "id": 6008, "cat": "crashes", "msg": "The previous system shutdown at 3:01:12 AM on 11/7/2026 was unexpected."},
        {"t": iso(86400 * 9), "lvl": 3, "src": "Microsoft-Windows-WindowsUpdateClient", "id": 20, "cat": "updates", "msg": "Installation Failure: Windows failed to install the following update with error 0x800f0922: 2026-11 Cumulative Update for Windows 11 (KB5049999)."},
        {"t": iso(86400 * 6), "lvl": 3, "src": "Microsoft-Windows-WHEA-Logger", "id": 17, "cat": "hardware", "msg": "A corrected hardware error has occurred. Component: PCI Express Root Port Error Source: Advanced Error Reporting (PCI Express)"},
    ]}
    hal.winevents = {"events": parse_winevents(raw, hal.disk_map()), "t": now, "busy": False}
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
        base = {"cpu": 20, "mem": 36, "gpu": 8, "cpu_temp": 52, "ping": 14, "net_down": 30e6, "net_up": 0.6e6, "disk": 60e6}[k]
        hal.minutes[k] = deque([round(base * (1 + 0.35 * math.sin(i / 37) + 0.15 * math.sin(i / 5)), 1) for i in range(1440)], maxlen=1440)
        hal.hours[k] = deque([round(base * (1 + 0.4 * math.sin(i / 4) + 0.1 * math.sin(i)), 1) for i in range(168)], maxlen=168)
    return hal


# ==============================================================================
# 12. ACTIONS (pod bay controls)
# ==============================================================================

# ---- Lock from the phone when this server runs outside your desktop ----
LOCK_TASK = "HAL 9000 Lock (from phone)"
LOCK_PS = r"""
$u = (Get-CimInstance Win32_ComputerSystem).UserName
if (-not $u) { @{ ok = $false; nobody = $true } | ConvertTo-Json -Compress; return }
try {
  $a = New-ScheduledTaskAction -Execute "$env:WINDIR\System32\rundll32.exe" -Argument 'user32.dll,LockWorkStation'
  $p = New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask -TaskName '__NAME__' -Action $a -Principal $p -Description 'Lets the HAL 9000 app lock the screen from the phone.' -Force -ErrorAction Stop | Out-Null
  Start-ScheduledTask -TaskName '__NAME__' -ErrorAction Stop
  @{ ok = $true } | ConvertTo-Json -Compress
} catch { @{ ok = $false; err = '' + $_.Exception.Message } | ConvertTo-Json -Compress }
"""


def lock_via_session(name: str) -> tuple[bool, str]:
    """Locks the signed-in user's screen through a Task Scheduler task that runs in their session."""
    if not IS_WINDOWS:
        return False, "Locking only works on the Windows PC."
    res = sm.run_powershell_json(LOCK_PS.replace("__NAME__", LOCK_TASK), timeout=40)
    if isinstance(res, dict) and res.get("ok"):
        return True, f"Locked. Nobody gets past the pod bay doors without your PIN, {name}."
    if isinstance(res, dict) and res.get("nobody"):
        return True, f"Nobody is logged in, {name}, so the PC is already at the sign-in screen."
    err = res.get("err") if isinstance(res, dict) else "Windows didn't answer"
    return False, f"I'm afraid I couldn't lock it: {err}. Press Windows+L on the PC."


# ---- Update button: runs update_all.ps1 -Unattended as its own Windows task ----
# Its own task (as SYSTEM), not a child of this server: the update stops and restarts the phone server, and
# Task Scheduler ends everything a task started when that task stops.
UPDATE_TASK = "NZB-CC Update (from phone)"
UPDATE_RESULT_FILE = BASE_DIR / "update_result.json"
UPDATE_PENDING_FILE = BASE_DIR / "update_pending.json"
UPDATE_PS = r"""
try {
  $a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "__SCRIPT__" -Unattended' -WorkingDirectory '__DIR__'
  $p = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
  $s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
  Register-ScheduledTask -TaskName '__NAME__' -Action $a -Principal $p -Settings $s -Description 'Started by the Update button in the HAL 9000 app: runs update_all.ps1 -Unattended.' -Force -ErrorAction Stop | Out-Null
  Start-ScheduledTask -TaskName '__NAME__' -ErrorAction Stop
  @{ ok = $true } | ConvertTo-Json -Compress
} catch { @{ ok = $false; err = '' + $_.Exception.Message } | ConvertTo-Json -Compress }
"""
UPDATE_STALE = 15 * 60   # an update that hasn't reported back after this long is treated as finished


def update_view() -> dict:
    """The last result of update_all.ps1 -Unattended, and whether one is running now."""
    result = load_json(UPDATE_RESULT_FILE, {})
    pending = load_json(UPDATE_PENDING_FILE, {})
    started = to_float(pending.get("t"), 0) or 0
    running = bool(started) and time.time() - started < UPDATE_STALE and to_float(result.get("t"), 0) < started
    return {"result": result or None, "running": running, "started": started or None}


def start_update(hal: "Hal") -> dict:
    name = hal.settings["name"]
    if not IS_WINDOWS:
        return {"ok": False, "message": "Updating from the phone only works on the Windows PC."}
    script = BASE_DIR / "update_all.ps1"
    if not script.is_file():
        return {"ok": False, "message": "update_all.ps1 isn't in the NZB Command Center folder, so I can't update."}
    if update_view()["running"]:
        return {"ok": True, "message": f"I'm already updating, {name}. Give me a moment.", "updating": True}
    save_json(UPDATE_PENDING_FILE, {"t": time.time()})
    q = lambda v: str(v).replace("'", "''")
    res = sm.run_powershell_json(UPDATE_PS.replace("__SCRIPT__", str(script).replace('"', "")).replace("__DIR__", q(BASE_DIR))
                                 .replace("__NAME__", q(UPDATE_TASK)), timeout=60)
    if not (isinstance(res, dict) and res.get("ok")):
        try:
            UPDATE_PENDING_FILE.unlink()
        except OSError:
            pass
        err = res.get("err") if isinstance(res, dict) else "Windows didn't answer"
        return {"ok": False, "message": f"I couldn't start the update: {err}"}
    hal.add_event("info", "Updating", "Started from the phone. The phone server restarts on its own.")
    return {"ok": True, "updating": True, "message": f"Updating now. I'll be back in about a minute, {name}."}


def pihole_settings(hal: Hal, body: dict) -> dict:
    """Settings › Connections › Pi-hole: test or save its address and password (blank password keeps the saved one)."""
    s = dict(hal.settings)
    url = pihole_base(body.get("url", s.get("pihole_url", "")))
    pw = str(body.get("password") or "")
    if len(pw) > 200 or any(c in pw for c in "\r\n\x00"):
        raise ValueError("That password doesn't look right.")
    if not pw and not body.get("clear_password"):
        pw = dpapi_unprotect(s.get("pihole_password") or "")
    name = s["name"]
    if body.get("action") == "test":
        base = url or (hal.pihole.base if hal.pihole else "")
        if not base:
            for ip in dns_servers():
                if pihole_detect(f"http://{ip}"):
                    base = f"http://{ip}"
                    break
        if not base:
            return {"ok": False, "message": "I couldn't find a Pi-hole. Type its address (the Raspberry Pi's IP)."}
        v = pihole_detect(base)
        if not v:
            return {"ok": False, "message": f"Nothing at {base} answers like a Pi-hole."}
        ph = PiHole(base, pw, v)
        try:
            summ = ph.summary()
        except PiHoleAuthError as exc:
            return {"ok": False, "message": f"Found Pi-hole {v} at {base}, but: {exc}"}
        except OSError as exc:
            return {"ok": False, "message": f"Found Pi-hole {v} at {base}, but it didn't answer: {exc}"}
        finally:
            ph.logout()
        return {"ok": True, "message": f"Pi-hole {v} at {base} is answering, {name}: {summ['blocked']:,} blocked today."}
    s["pihole_url"] = url
    s["pihole_off"] = bool(body.get("off"))
    if body.get("clear_password"):
        s["pihole_password"] = ""
    elif body.get("password"):
        s["pihole_password"] = dpapi_protect(pw)
    if hal.pihole:
        hal.pihole.logout()
    hal.pihole, hal._ph_search = None, 0.0
    hal.update_settings(s)
    threading.Thread(target=hal.check_pihole, daemon=True).start()
    log.info("Pi-hole settings saved (address %s, password %s)", url or "auto", "set" if s["pihole_password"] else "none")
    return {"ok": True, "message": f"Saved. I'll keep an eye on the Pi-hole, {name}."}


def kuma_settings(hal: Hal, body: dict) -> dict:
    """Settings › Connections › Uptime Kuma: test or save its address and API key (blank key keeps the saved one)."""
    s = dict(hal.settings)
    url = kuma_base(body.get("url", s.get("kuma_url", "")))
    key = str(body.get("key") or "").strip()
    if len(key) > 300 or not re.fullmatch(r"[\x21-\x7e]*", key):
        raise ValueError("That API key doesn't look right.")
    if not key and not body.get("clear_key"):
        key = dpapi_unprotect(s.get("kuma_key") or "")
    name = s["name"]
    if body.get("action") == "test":
        if not url:
            return {"ok": False, "message": "Type Uptime Kuma's address, like 192.168.68.63:3001."}
        try:
            mons = kuma_fetch(url, key)
        except KumaAuthError as exc:
            return {"ok": False, "message": f"Found Uptime Kuma at {url}, but: {exc} Make one in Uptime Kuma › Settings › API Keys."}
        except OSError as exc:
            return {"ok": False, "message": f"Uptime Kuma at {url} didn't answer: {exc}"}
        down = sum(1 for m in mons if m["status"] == "down")
        return {"ok": True, "message": f"Uptime Kuma is answering, {name}: {len(mons)} monitors" + (f", {down} down." if down else ", all fine.")}
    s["kuma_url"] = url
    if body.get("clear_key"):
        s["kuma_key"] = ""
    elif body.get("key"):
        s["kuma_key"] = dpapi_protect(key)
    hal.update_settings(s)
    threading.Thread(target=hal.check_kuma, daemon=True).start()
    log.info("Uptime Kuma settings saved (address %s, key %s)", url or "none", "set" if s["kuma_key"] else "none")
    return {"ok": True, "message": f"Saved. I'll read Uptime Kuma from now on, {name}." if url else "Uptime Kuma is switched off."}


def run_action(hal: Hal, action: str, body: dict) -> dict:
    name = hal.settings["name"]
    if hal.monitor.__class__.__name__ == "DemoMonitor":
        return {"ok": True, "message": "Demo mode: nothing was changed."}
    if action == "lock":
        ok, msg = sm.lock_workstation()
        if not ok:
            # Started before anyone logged in (Task Scheduler "at startup"), this server runs outside your desktop,
            # where Windows won't lock it. So ask Windows to run the lock inside your session instead.
            ok, msg = lock_via_session(name)
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
    elif action == "update":
        if body.get("confirm") is not True:
            return {"ok": False, "message": f"I'm afraid I can't do that, {name}."}
        res = start_update(hal)
        log.info("Action update: %s %s", res.get("ok"), res.get("message"))
        return res
    elif action in ("pihole_pause", "pihole_resume"):
        ph = hal.pihole
        if not ph or not hal.ph.get("found"):
            return {"ok": False, "message": f"I haven't found a Pi-hole, {name}."}
        minutes = to_int(body.get("minutes"), 5)
        if action == "pihole_pause" and minutes not in (5, 30, 60):
            raise ValueError("Pause for 5, 30 or 60 minutes.")
        try:
            ph.set_blocking(action == "pihole_resume", 0 if action == "pihole_resume" else minutes * 60)
            ok = True
            msg = (f"Ad blocking paused for {minutes} minutes. It turns itself back on." if action == "pihole_pause"
                   else f"Ad blocking is back on, {name}.")
        except PiHoleAuthError as exc:
            ok, msg = False, str(exc) + " Add it in Settings › Connections."
        except OSError as exc:
            ok, msg = False, f"Pi-hole didn't answer: {exc}"
        threading.Thread(target=hal.check_pihole, daemon=True).start()
    elif action == "device_ok":
        ok = hal.device_ok(str(body.get("id") or ""))
        msg = f"Thank you, {name}. I'll remember it." if ok else "That one's already off the list."
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
    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "SAMEORIGIN", "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; "
                                "manifest-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"),
}
def manifest(base: str = "") -> bytes:
    """The home-screen app description. Under the NZB phone server HAL lives at /hal, so its id, start page
    and scope are /hal/ and it installs as its own icon next to the NZB one."""
    root = (base or "") + "/"
    return json.dumps({
        "id": root, "name": "HAL 9000", "short_name": "HAL 9000", "start_url": root, "scope": root, "display": "standalone",
        "background_color": "#050506", "theme_color": "#050506",
        "icons": [{"src": f"{base}/icon.png", "sizes": "180x180", "type": "image/png", "purpose": "any"},
                  {"src": f"{base}/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
    }).encode("utf-8")


MANIFEST = manifest("")


class HalRoutes:
    """HAL's web pages and API, for any BaseHTTPRequestHandler: hal_server's own (HalHandler, at /) and the
    NZB phone server's (at /hal). The helpers are prefixed _h_ so they never clash with the host's own."""

    hal_base = ""   # "" when HAL has its own port, "/hal" inside the NZB phone server

    # ---- low-level ----
    def _h_send(self, status: int, raw: bytes, ctype: str, extra: dict | None = None) -> None:
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

    def _h_json(self, status: int, data, extra: dict | None = None) -> None:
        self._h_send(status, json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8", extra)

    def _h_read(self) -> dict:
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

    def _h_pair_url(self) -> str:
        return f"http://localhost:{self.server.server_address[1]}{self.hal_base}/pair"

    def _h_gate(self, need_auth: bool) -> bool:
        ip = self.client_address[0]
        if not is_client_allowed(ip):
            log.warning("Blocked request from %s", ip)
            self._h_json(403, {"error": "I only talk to devices on your home network or Tailscale."})
            return False
        if not need_auth:
            return True
        wait = AUTH_FAILURES.blocked_for(ip)
        if wait:
            self._h_json(429, {"error": f"This conversation can serve no purpose anymore. Goodbye. (Try again in {wait} s.)"},
                         {"Retry-After": str(wait)})
            return False
        raw = self.headers.get("X-Mobile-Token") or ""
        supplied = clean_code(raw).encode("utf-8")
        # The code exactly as in mobile_access_key.txt, or both sides without stray characters (a file can hold a
        # typed-out "\n" at the end: the NZB app uses the code that way, so both forms are accepted).
        exact = secrets.compare_digest(raw.encode("utf-8", "replace"), _token_bytes)
        if not exact and not secrets.compare_digest(supplied, clean_code(_token_bytes.decode()).encode("utf-8")):
            if raw:
                AUTH_FAILURES.record(ip)
                expected = clean_code(_token_bytes.decode()).encode("utf-8")
                same_start = supplied[:4] == expected[:4]
                log.warning("Wrong access code from %s (%d characters, the code has %d; %s)", ip, len(supplied), len(expected),
                            "same first characters" if same_start else "different first characters")
                hint = (f"You entered {len(supplied)} characters; the code on this PC has {len(expected)}"
                        + (", and it starts the same way, so check the end." if same_start else
                           ", and it starts differently, so it's a different code.") if supplied else "")
                self._h_json(401, {"error": "Just what do you think you're doing, Dave? That isn't the access code. " + hint +
                                   " Easier: type the 6-digit PIN from " + self._h_pair_url() + " on the PC."})
            else:
                self._h_json(401, {"error": "Enter the access code shown on the PC."})
            return False
        AUTH_FAILURES.clear(ip)
        return True

    def _h_guarded(self, fn) -> None:
        try:
            fn()
        except (ValueError, json.JSONDecodeError) as exc:
            self._h_json(400, {"error": str(exc)})
        except Exception:
            log.exception("Error serving HAL %s %s", self.command, self.path)
            self._h_json(500, {"error": "I'm afraid I can't do that. Something went wrong (see the log)."})

    def _h_pair(self) -> None:
        ip = self.client_address[0]
        if not self._h_gate(False):
            return
        wait = AUTH_FAILURES.blocked_for(ip)
        if wait:
            self._h_json(429, {"error": f"This conversation can serve no purpose anymore. Goodbye. (Try again in {wait} s.)"})
            return
        try:
            pin = re.sub(r"\D", "", str(self._h_read().get("pin") or ""))
        except (ValueError, json.JSONDecodeError):
            pin = ""
        if len(pin) == 6 and PAIRING.use(pin):
            AUTH_FAILURES.clear(ip)
            log.info("Phone %s paired with the PIN", ip)
            self._h_json(200, {"token": _token_bytes.decode()})
            return
        AUTH_FAILURES.record(ip)
        log.warning("Wrong pairing PIN from %s", ip)
        self._h_json(401, {"error": "Just what do you think you're doing, Dave? That PIN isn't right, or it has expired. "
                                    "Reload " + self._h_pair_url() + " on the PC for a fresh one."})

    # ---- GET ----
    def hal_get(self, path: str, query: str = "") -> None:
        base = self.hal_base
        if path in ("/", "/index.html"):
            if self._h_gate(False):
                port = self.server.server_address[1]
                page = PAGE.replace("__VERSION__", VERSION).replace("__BASE__", base).replace("__PAIRURL__", f"http://localhost:{port}{base}/pair")
                self._h_send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/pair":
            ip = self.client_address[0].split("%")[0]
            try:
                local = ipaddress.ip_address(ip.removeprefix("::ffff:")).is_loopback
            except ValueError:
                local = False
            if not local:
                self._h_json(403, {"error": "Open this page on the PC itself: " + self._h_pair_url()})
                return
            pin, exp = PAIRING.current()
            port = self.server.server_address[1]
            addrs = "".join(f"<div class=a>http://{a}:{port}{base}/</div>" for a in lan_addresses()) or \
                f"<div class=a>http://&lt;this-pc&gt;:{port}{base}/</div>"
            page = PAIR_PAGE.replace("__PIN__", f"{pin[:3]} {pin[3:]}").replace("__ADDRS__", addrs) \
                .replace("__MIN__", str(max(1, round((exp - time.time()) / 60)))).replace("__CODE__", _token_bytes.decode())
            self._h_send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path in ("/tv", "/tv/"):
            # HAL on the TV, the animated screen (the TV app shows it; the access code travels in the #k=... part)
            if self._h_gate(False):
                from hal_tv_page import TV_PAGE
                self._h_send(200, TV_PAGE.encode("utf-8"), "text/html; charset=utf-8", {"Cache-Control": "no-cache",
                            "Content-Security-Policy": SECURITY_HEADERS["Content-Security-Policy"].replace(
                                "style-src 'unsafe-inline'", "style-src 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com")})
            return
        if path == "/manifest.webmanifest":
            if self._h_gate(False):
                self._h_send(200, manifest(base), "application/manifest+json", {"Cache-Control": "public, max-age=86400"})
            return
        if path in ("/icon.png", "/apple-touch-icon.png", "/favicon.ico", "/icon-512.png"):
            if self._h_gate(False):
                self._h_send(200, icon(512 if path == "/icon-512.png" else 180), "image/png", {"Cache-Control": "public, max-age=86400"})
            return
        if not path.startswith("/api/"):
            self._h_json(404, {"error": "Not found."})
            return
        if not self._h_gate(True):
            return
        q = urllib.parse.parse_qs(query)
        hal = HAL

        def run():
            if path == "/api/status":
                self._h_json(200, hal.status())
            elif path == "/api/history":
                rng = (q.get("range") or ["live"])[0]
                if rng not in ("live", "1h", "24h", "7d"):
                    raise ValueError("Unknown range.")
                self._h_json(200, {"range": rng, "series": hal.history(rng)})
            elif path == "/api/events":
                with hal.lock:
                    hal.unread = 0
                    ev = list(hal.events)
                self._h_json(200, {"events": ev[:200]})
            elif path == "/api/settings":
                url, _tok = ntfy_target(hal.settings)
                self._h_json(200, {"settings": {k: v for k, v in hal.settings.items() if k not in ("pihole_password", "kuma_key")}, "push_ready": bool(url),
                                   "push_from_phone_app": bool(url) and not hal.settings.get("ntfy_url"),
                                   "services": [{"id": s["id"], "name": s["name"]} for s in SERVICES],
                                   "restart_methods": {sid: hal.restart_method(sid) for sid in SERVICE_IDS} if q.get("methods") else {}})
            elif path == "/api/tv/front":    # what's on the TV, and why not if it can't tell
                self._h_json(200, {"front": tv_front(), "host": tv_host(), "error": _tv_front.get("error", ""),
                                   "age": round(time.time() - _tv_front["ok_t"]) if _tv_front["ok_t"] else None})
            elif path == "/api/winevents":
                if q.get("refresh"):
                    hal.refresh_winevents()
                self._h_json(200, hal.winevents_view())
            elif path == "/api/cleanup":
                self._h_json(200, hal.cleanup_info(refresh=bool(q.get("refresh"))))
            elif path == "/api/brief":
                if (q.get("from") or [""])[0] == "tv":
                    with hal.lock:
                        hal.tv_seen = 0.0 if q.get("closed") else time.time()
                    if q.get("closed"):
                        self._h_json(200, {"ok": True})
                        return
                kind = (q.get("kind") or ["now"])[0]
                if kind not in ("now", "morning", "weekly"):
                    raise ValueError("Unknown briefing.")
                st = hal.status()
                ph, km = st.get("pihole") or {}, st.get("kuma") or {}
                full = max(st["drives"], key=lambda d: d["pct"]) if st["drives"] else None
                self._h_json(200, {"text": hal.brief_text(kind), "level": st["hal"]["level"], "quote": st["hal"]["quote"],
                                   "issues": len(st["issues"]), "cpu": st["vitals"]["cpu"]["pct"], "ram": st["vitals"]["mem"]["pct"],
                                   "ads": ph.get("blocked") if ph.get("ok") else None, "kuma_down": km.get("down") if km.get("ok") else None,
                                   "fullest": {"mount": full["mount"], "pct": full["pct"], "free": full["free"]} if full else None,
                                   "record": st["record"], **tv_brief(st)})
            elif path == "/api/tv/details":
                self._h_json(200, tv_details(hal))
            elif path == "/api/tv/phrases":
                self._h_json(200, {"phrases": list(hal.tv_phrases), "open": time.time() - hal.tv_seen < 45})
            elif path == "/api/kuma":
                self._h_json(200, {"url": hal.settings.get("kuma_url", ""), "key_set": bool(hal.settings.get("kuma_key")),
                                   "found": hal.kuma_view()})
            elif path == "/api/pihole":
                s = hal.settings
                self._h_json(200, {"url": s.get("pihole_url", ""), "password_set": bool(s.get("pihole_password")),
                                   "off": bool(s.get("pihole_off")), "found": hal.pihole_view()})
            else:
                self._h_json(404, {"error": "Not found."})
        self._h_guarded(run)

    # ---- POST ----
    def hal_post(self, path: str) -> None:
        if path == "/api/pair":
            self._h_pair()
            return
        if not self._h_gate(True):
            return
        hal = HAL

        def run():
            body = self._h_read()
            if path == "/api/action":
                self._h_json(200, run_action(hal, str(body.get("action") or ""), body))
            elif path == "/api/service/restart":
                sid = str(body.get("id") or "")
                if hal.monitor.__class__.__name__ == "DemoMonitor":
                    self._h_json(200, {"ok": True, "message": "Demo mode: nothing was restarted."})
                    return
                if sid not in SERVICE_IDS:
                    raise ValueError("Unknown service.")
                if not hal.restart_method(sid):
                    self._h_json(200, {"ok": False, "message": "I don't know how to start it on this PC yet. Start it once yourself and I'll remember how."})
                    return
                hal.add_event("info", f"Restarting {dict((s['id'], s['name']) for s in SERVICES)[sid]}", "Requested from the phone.")
                job = hal.restart_service(sid)
                self._h_json(200, {"ok": True, "message": job.get("msg", "Restarting…")})
            elif path == "/api/snooze":
                keys = body.get("keys")
                if not isinstance(keys, list) or not keys or len(keys) > 30 or not all(isinstance(k, str) and 0 < len(k) <= 200 for k in keys):
                    raise ValueError("Which alerts?")
                name = hal.settings["name"]
                if body.get("off"):
                    hal.unsnooze(keys)
                    self._h_json(200, {"ok": True, "message": f"It's back on the list, {name}."})
                    return
                hours = to_float(body.get("hours"), 24) or 24
                hours = min(168, max(1, hours))
                n = hal.snooze(keys, hours)
                until = datetime.fromtimestamp(time.time() + hours * 3600).strftime("%I:%M %p").lstrip("0")
                self._h_json(200, {"ok": bool(n), "message": (f"Very well, {name}. I'll keep quiet about it until {until} tomorrow"
                                                              if hours == 24 else f"Very well, {name}. Hidden until {until}") +
                                   ", unless it gets worse." if n else "That alert has already gone away."})
            elif path == "/api/pihole":
                self._h_json(200, pihole_settings(hal, body))
            elif path == "/api/kuma":
                self._h_json(200, kuma_settings(hal, body))
            elif path == "/api/tv/show":
                self._h_json(200, tv_show(hal, str(body.get("say") or "")))
            elif path == "/api/tv/key":
                self._h_json(200, tv_key(hal, str(body.get("key") or ""), body.get("steps") or 1))
            elif path == "/api/tv/phrases":
                text = re.sub(r"\s+", " ", str(body.get("text") or "")).strip()[:120]
                with hal.lock:
                    if body.get("action") == "add" and text:
                        if text not in hal.tv_phrases:
                            hal.tv_phrases.insert(0, text)
                            del hal.tv_phrases[TV_PHRASES_MAX:]
                    elif body.get("action") == "delete":
                        hal.tv_phrases = [x for x in hal.tv_phrases if x != text]
                    else:
                        raise ValueError("Add or delete a phrase.")
                hal.save()
                self._h_json(200, {"ok": True, "phrases": list(hal.tv_phrases)})
            elif path == "/api/siri":
                q_text = str(body.get("q") or body.get("text") or "")[:300]
                try:
                    res = hal.siri(q_text)
                except Exception as exc:  # noqa: BLE001 - Siri needs something to say, not an error page
                    log.warning("Siri %r failed: %s", q_text[:80], exc)
                    res = {"say": f"Something went wrong on the PC, {hal.settings['name']}: {str(exc)[:80]}", "intent": "error"}
                res["say"] = spoken(res.get("say") or "")
                log.info("Siri: %r -> %s", q_text[:80], res.get("intent"))
                self._h_json(200, res)
            elif path == "/api/settings":
                new = validate_settings(body, hal.settings)
                hal.update_settings(new)
                self._h_json(200, {"ok": True, "settings": new})
            else:
                self._h_json(404, {"error": "Not found."})
        self._h_guarded(run)


class HalHandler(HalRoutes, BaseHTTPRequestHandler):
    """HAL on its own port (8766): python hal_server.py, or the NZB phone server's second listener."""
    server_version = f"HAL9000/{VERSION}"
    sys_version = ""
    timeout = CLIENT_SOCKET_TIMEOUT

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.client_address[0], fmt % args)

    def do_GET(self):
        parts = urllib.parse.urlsplit(self.path)
        self.hal_get(parts.path, parts.query)

    def do_POST(self):
        self.hal_post(urllib.parse.urlsplit(self.path).path)


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
<link rel="manifest" href="__BASE__/manifest.webmanifest"><link rel="icon" type="image/png" href="__BASE__/icon.png"><link rel="apple-touch-icon" href="__BASE__/icon.png">
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
.s.critical,.s.down,.s.error{background:var(--red);box-shadow:0 0 10px var(--red)}
details.ev{border-top:1px solid var(--line)}.list>details.ev:first-child{border-top:0}
details.ev summary{list-style:none;display:flex;align-items:center;gap:12px;padding:12px 14px;cursor:pointer}
details.ev summary::-webkit-details-marker{display:none}
details.ev summary .t{flex:1;min-width:0}details.ev summary b{display:block;font-weight:600;font-size:14px}
details.ev summary span.sub2{display:block;font:12px var(--mono);color:var(--soft);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
details.ev .body{padding:0 14px 14px 35px;font-size:13px;color:var(--text)}
details.ev .raw{font:12px var(--mono);color:var(--muted);margin-top:8px;word-break:break-word}
.chiprow{display:flex;gap:6px;overflow-x:auto;padding:0 28px 4px 0;margin-bottom:10px;scrollbar-width:none;-webkit-mask-image:linear-gradient(90deg,#000 calc(100% - 28px),transparent);mask-image:linear-gradient(90deg,#000 calc(100% - 28px),transparent)}
.ev .x{font:600 11px var(--mono);color:var(--amber);background:#2e2408;border-radius:6px;padding:1px 6px;margin-left:6px;white-space:nowrap}
details.ev summary span.sub2{white-space:normal;overflow-wrap:anywhere}
.ev .times{font:11.5px var(--mono);color:var(--soft);margin-top:8px;line-height:1.6}
.chiprow button{flex:none;border:0;border-radius:999px;padding:7px 12px;background:var(--card);color:var(--soft);font-weight:600;font-size:13px}
.chiprow button.on{background:#3a0d0a;color:#ffd9d2}.chiprow button i{font-style:normal;color:var(--muted);margin-left:5px}
.s.off,.s.ignored,.s.info{background:#3f3f46;box-shadow:none}.s.info{background:var(--sky);box-shadow:0 0 8px var(--sky)}
.alert{display:flex;gap:12px;align-items:flex-start;width:100%;text-align:left;background:linear-gradient(90deg,#2a1d05,#17140b);border:1px solid #4a3609;border-radius:14px;padding:12px 14px;margin-bottom:10px}
.alert.critical{background:linear-gradient(90deg,#300908,#170b0b);border-color:#5a1410}
.alert.info{background:var(--card);border-color:#1f2a3a}
.alert b{font-size:14px;display:block}.alert p{margin:3px 0 0;font-size:13px;color:var(--text)}
div.alert{cursor:pointer}.alert .grow{flex:1;min-width:0}
.snz{flex:none;align-self:center;border:0;border-radius:9px;background:rgba(255,255,255,.08);color:var(--soft);font:600 11px var(--mono);padding:10px 11px;margin-left:6px;min-height:36px}
.hidline{display:flex;align-items:center;justify-content:space-between;gap:10px;font:12px var(--mono);color:var(--muted);margin:-2px 2px 12px}
.hidline button{background:none;border:0;color:var(--red2);font:700 11px var(--mono);letter-spacing:.06em;padding:4px 0}
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
input.field,select.field{width:100%;background:var(--card);border:1px solid #26262b;border-radius:10px;color:var(--white);font:16px var(--mono);padding:11px 12px}
/* 16px: an iPhone zooms into smaller text boxes, and a home-screen app then stays zoomed in */
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
  <p style="font-size:14px;color:var(--soft)">On the PC, open <b>__PAIRURL__</b> and type the <b>6-digit PIN</b> it shows. Or paste the full access code (the same as the NZB phone app's).</p>
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
  <section id="t-events" class="hidden"></section>
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
const B='__BASE__';   // '' on HAL's own port, '/hal' inside the NZB phone server
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const LV={ok:0,info:1,warning:2,critical:3};
let token='',S=null,tab='status',range='live',hist=null,histAt=0,busy=false,lastOk=0;
// Inside the NZB phone server (same address) the NZB app's or the all-in-one app's saved code works too.
try{token=localStorage.getItem('hal_token')||(B?localStorage.getItem('app_token')||localStorage.getItem('nzb_mobile_token'):'')||''}catch(e){}
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
  const r=await fetch(B+path,o);let d={};try{d=await r.json()}catch(e){}
  if(r.status===401||r.status===429){if(r.status===401)showPair(d.error);throw new Error(d.error||'Not allowed')}
  if(!r.ok)throw new Error(d.error||('Error '+r.status));return d}
function showPair(msg){$('app').classList.add('hidden');$('tabs').classList.add('hidden');$('pair').classList.remove('hidden');$('perr').textContent=msg&&token?msg:''}
async function connect(){let v=cleanCode($('code').value);$('code').value=v;if(!v)return;$('perr').textContent='';
  if(/^\d{6}$/.test(v)){try{const r=await fetch(B+'/api/pair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pin:v})});const d=await r.json().catch(()=>({}));
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
  head+=updBanner();
  if(S.power.shutdown_at&&S.power.shutdown_at>S.now)head+='<div class="banner"><b>Shutting down in '+Math.max(0,Math.round(S.power.shutdown_at-S.now))+' s</b><button class="btn red" data-act="cancel_shutdown">Cancel</button></div>';
  let x='';
  x+=alerts.map(i=>'<div class="alert '+i.level+'" data-go="'+esc(i.tab)+'"><span class="s '+i.level+'"></span><span class="grow"><b>'+esc(i.title)+'</b>'+(i.detail?'<p>'+esc(i.detail)+'</p>':'')+'</span><button class="snz" data-snooze="'+esc(i.key)+'" aria-label="Hide for 24 hours">Hide 24 h</button></div>').join('');
  if(notes.length)x+='<div class="alert info" data-go="'+esc(notes[0].tab)+'"><span class="s info"></span><span class="grow"><b>'+(notes.length>1?notes.length+' small things':'One small thing')+'</b><p>'+esc(notes.map(i=>i.title).join(' · '))+'</p></span><button class="snz" data-snooze="'+esc(notes.map(i=>i.key).join('|'))+'" aria-label="Hide for 24 hours">Hide 24 h</button></div>';
  x+=hiddenAlerts();
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
function updateTap(){const t=$('htap');if(!t)return;t.textContent=!talkative()?'TAP HAL TO HEAR HIM':unlocked?'':'TAP ANYWHERE AND I\'LL TALK';t.style.visibility=t.textContent?'visible':'hidden';if(!t.textContent)t.textContent='\u00a0'}   // keeps its space: no jump under the first tap
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

// Alerts hidden with "Hide 24 h": one line, and the list (with "Show again") when opened.
let showHidden=false;
function hiddenAlerts(){const h=(S&&S.snoozed)||[];if(!h.length)return'';
  const until=Math.min(...h.map(i=>i.until)),d=new Date(until*1000),t=d.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}),when=(d.toDateString()===new Date().toDateString()?'':d.toDateString()===new Date(Date.now()+864e5).toDateString()?'tomorrow ':d.toLocaleDateString([],{weekday:'short'})+' ')+t;
  let x='<div class="hidline"><span>'+h.length+' alert'+(h.length>1?'s':'')+' hidden until '+esc(when)+'</span><button data-showhidden>'+(showHidden?'CLOSE':'SHOW')+'</button></div>';
  if(showHidden)x+='<div class="list" style="margin-bottom:12px">'+h.map(i=>'<div class="row"><span class="s '+i.level+'"></span><span class="t"><b>'+esc(i.title)+'</b><span>Hidden until '+esc(new Date(i.until*1000).toLocaleString([],{weekday:'short',hour:'numeric',minute:'2-digit'}))+'</span></span><button class="btn" data-unsnooze="'+esc(i.key)+'">Show again</button></div>').join('')+'</div>';
  return x}

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
  const es=sc.events;
  if(es){const dn=es.cats.drives||0,dl=Object.keys(es.serious_drives||{}).length;
    x+='<button class="row" data-evgo="drives"><span class="s '+(dl?'warning':'')+'"></span><span class="t"><b>Drive errors from Windows</b><span>'+(dn?dn+' in 7 days'+(dl?' · '+esc(Object.keys(es.serious_drives)[0])+' needs a look':''):'None in 7 days')+'</span></span><span class="m">›</span></button>'+
       '<button class="row" data-evgo="all"><span class="s '+(es.critical?'critical':es.total?'warning':'')+'"></span><span class="t"><b>Windows errors &amp; events</b><span>'+(es.total?es.total+' worth knowing about in 7 days':'Nothing worth worrying about in 7 days')+'</span></span><span class="m">›</span></button>'}
  else if(sc.disk_errors!=null)x+='<button class="row" data-evgo="drives"><span class="s '+(sc.disk_errors?'warning':'')+'"></span><span class="t"><b>Drive errors from Windows</b><span>'+sc.disk_errors+' in 7 days</span></span><span class="m">›</span></button>';
  else if(S.windows)x+='<button class="row" data-evgo="all"><span class="s off"></span><span class="t"><b>Windows errors &amp; events</b><span>Reading the event logs…</span></span><span class="m">›</span></button>';
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
  let x='<div class="top"><button class="back" data-go="status">‹ Status</button><button class="iconbtn" data-settings aria-label="Settings">'+svg('gear','#D4D4D8')+'</button></div><h1>Log &amp; controls</h1>'+updBanner();
  if(S.power.shutdown_at&&S.power.shutdown_at>S.now)x+='<div class="banner"><b>Shutting down in '+Math.max(0,Math.round(S.power.shutdown_at-S.now))+' s</b><button class="btn red" data-act="cancel_shutdown">Cancel</button></div>';
  x+='<div class="sec" style="margin-top:0"><span>WHAT I NOTICED</span>'+(events&&events.length?'<button class="btn" style="padding:8px 14px;font-size:12px;min-height:36px" data-act="clear_log" data-confirm="Clear the log?">Clear</button>':'')+'</div><div class="list">';
  x+=events==null?'<div class="empty">I\'m thinking…</div>':events.length?events.slice(0,40).map(e=>'<div class="row"><span class="s '+e.level+'"></span><span class="t"><b>'+esc(e.title)+'</b><span>'+esc(ago(e.t)+(e.msg?' · '+e.msg:''))+'</span></span></div>').join(''):'<div class="empty">Nothing yet. I\'ve been watching quietly.</div>';
  x+='</div><div class="sec"><span>POD BAY CONTROLS</span></div><div class="grid2">'+
   [['lock','lock','Lock','Lock the PC screen'],['sleep','moon','Sleep','Wake it with the keyboard'],['flush_dns','net','Fix network','Flush DNS, renew address'],['test_push','bell','Test alert','Send one to your phone'],['update','upd','Update','Get the newest version of everything']]
   .map(a=>'<button class="pod" data-act="'+a[0]+'"'+(a[0]==='sleep'?' data-confirm="Put the PC to sleep? Downloads and streams stop until it wakes."':'')+'><span class="ic">'+svg(a[1],'#FF6A4D')+'</span><b>'+a[2]+'</b><span>'+a[3]+'</span></button>').join('')+'</div>';
  x+='<div class="hold" data-hold="restart"><div class="fill"></div><span>Hold to restart HAL 9000</span></div>'+
     '<div class="hold amber" data-hold="shutdown" style="margin-top:10px"><div class="fill"></div><span>Hold to shut down</span></div>'+
     '<p class="hint">“I\'m afraid I can\'t do that, '+esc(S.hal.name)+'” — unless you hold for 2 seconds. You get 60 seconds to cancel.</p>';
  if(S.windows)x+='<div class="list" style="margin-top:14px"><button class="row" data-evgo="all"><span class="t"><b>Windows errors &amp; events</b><span>Drive errors, crashes, failed services and updates</span></span><span class="m">›</span></button></div>';
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
   '<div class="sec">BRIEFINGS</div><div class="nums"><div><label class="f">Morning briefing at</label><input class="field" data-k="brief_time" placeholder="off" value="'+esc(s.brief_time||'')+'" inputmode="numeric"></div>'+
   '<div><label class="f">Weekly summary on</label><select class="field" data-k="brief_weekly">'+[['','Never'],['mon','Monday'],['tue','Tuesday'],['wed','Wednesday'],['thu','Thursday'],['fri','Friday'],['sat','Saturday'],['sun','Sunday']].map(o=>'<option value="'+o[0]+'"'+((s.brief_weekly||'')===o[0]?' selected':'')+'>'+o[1]+'</option>').join('')+'</select></div></div>'+
   '<p class="hint" style="text-align:left;margin:4px 0 0">A short report to your phone each morning (blank time: off). On the weekly day it covers the whole week.</p>'+
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
async function act(action,extra){try{const r=await api('/api/action',Object.assign({action},extra||{}));toast(r.message,!r.ok);if(r.updating){updWatch=true;render()}setTimeout(refresh,800);if(action==='clean_temp'||action==='empty_recycle')setTimeout(loadCleanup,1500)}catch(e){toast(e.message,true)}}
function bindHold(el){let t0=0,raf=0;const fill=el.querySelector('.fill');
  const reset=()=>{cancelAnimationFrame(raf);t0=0;fill.style.width='0'};
  const step=()=>{const p=Math.min(1,(performance.now()-t0)/2000);fill.style.width=(p*100)+'%';if(p>=1){reset();if(navigator.vibrate)navigator.vibrate(60);act(el.dataset.hold,{confirm:true})}else raf=requestAnimationFrame(step)};
  el.onpointerdown=e=>{e.preventDefault();t0=performance.now();raf=requestAnimationFrame(step)};
  el.onpointerup=el.onpointerleave=el.onpointercancel=()=>{if(t0&&performance.now()-t0<2000)toast('I\'m afraid I can\'t do that, '+S.hal.name+'. Hold for 2 seconds.');reset()}}
document.addEventListener('click',async e=>{
  const sz=e.target.closest('[data-snooze],[data-unsnooze]');if(sz){const off='unsnooze' in sz.dataset;const keys=(off?sz.dataset.unsnooze:sz.dataset.snooze).split('|');
    try{const r=await api('/api/snooze',{keys,hours:24,off});toast(r.message,!r.ok);refresh()}catch(err){toast(err.message,true)}return}
  if(e.target.closest('[data-showhidden]')){showHidden=!showHidden;render();return}
  const go=e.target.closest('[data-go]');if(go){show(go.dataset.go);return}
  const r=e.target.closest('[data-range]');if(r){range=r.dataset.range;hist=null;render();loadHist(true);return}
  const a=e.target.closest('[data-act]');if(a){if(a.dataset.act==='update'){if(confirm('Update everything now? The app goes quiet for about a minute while the PC restarts the phone server.'))act('update',{confirm:true});return}
    if(a.dataset.confirm&&!confirm(a.dataset.confirm))return;act(a.dataset.act);return}
  const eg=e.target.closest('[data-evgo]');if(eg){evCat=eg.dataset.evgo;show('events');return}
  const ec=e.target.closest('[data-evcat]');if(ec){evCat=ec.dataset.evcat;render();return}
  const ed=e.target.closest('[data-evdays]');if(ed){evDays=Number(ed.dataset.evdays);render();return}
  if(e.target.closest('[data-evrefresh]')){if(evData)evData.busy=true;render();loadEv(true);return}
  const ek=e.target.closest('[data-evk]');if(ek){const k=ek.dataset.evk;setTimeout(()=>{const d=ek.parentElement;if(d.open)evOpen.add(k);else evOpen.delete(k)},0);return}
  const rs=e.target.closest('[data-restart]');if(rs){if(!confirm((rs.textContent==='Start'?'Start ':'Restart ')+rs.dataset.name+'?'))return;
    try{const d=await api('/api/service/restart',{id:rs.dataset.restart});toast(d.message,!d.ok);refresh()}catch(err){toast(err.message,true)}return}
  if(e.target.closest('[data-settings]'))openSettings();
  if(e.target.closest('[data-talk]'))setTalk(!talkative());
});
$('tabs').onclick=e=>{const b=e.target.closest('button');if(b&&b.dataset.tab)show(b.dataset.tab)};

/* ---------- Windows events (errors worth knowing about) ---------- */
let evData=null,evCat='all',evDays=7;const evOpen=new Set();
const evKey=e=>e.t+'|'+e.src+'|'+e.id;
function rEvents(){
  let x='<div class="top"><button class="back" data-go="shield">‹ Shield</button><div class="eye sm '+(S?S.hal.level:'')+'"></div></div><h1>Windows events</h1>';
  if(!evData){return x+'<div class="list"><div class="empty">I\'m reading Windows\' event logs…</div></div>'}
  if(!evData.windows)return x+'<div class="list"><div class="empty">The event log is only on Windows.</div></div>';
  const now=Date.now()/1000,inRange=evData.events.filter(e=>now-e.t<=evDays*86400);
  const cats=Object.keys(evData.cats).filter(c=>inRange.some(e=>e.cat===c));
  if(evCat!=='all'&&!evData.cats[evCat])evCat='all';
  const list=inRange.filter(e=>evCat==='all'||e.cat===evCat);
  x+='<div class="seg">'+[[1,'24 h'],[7,'7 days'],[30,'30 days']].map(r=>'<button data-evdays="'+r[0]+'" class="'+(evDays===r[0]?'on':'')+'">'+r[1]+'</button>').join('')+'</div>';
  x+='<div class="chiprow"><button data-evcat="all" class="'+(evCat==='all'?'on':'')+'">All<i>'+inRange.length+'</i></button>'+
     Object.keys(evData.cats).map(c=>{const n=inRange.filter(e=>e.cat===c).length;return n||evCat===c?'<button data-evcat="'+c+'" class="'+(evCat===c?'on':'')+'">'+esc(evData.cats[c])+'<i>'+n+'</i></button>':''}).join('')+'</div>';
  const crit=list.filter(e=>e.level==='critical').length,name=S?S.hal.name:'Dave',per=evDays===1?'today':'in the last '+evDays+' days';
  let q;if(!list.length)q='I\'ve read Windows\' logs, '+name+'. Nothing '+(evCat==='all'?'':'here ')+'you need to worry about '+per+'.';
  else{const top={};list.forEach(e=>{const k=e.who||e.title;top[k]=(top[k]||0)+1});const w=Object.entries(top).sort((a,b)=>b[1]-a[1])[0];
    q='I\'ve found '+list.length+' thing'+(list.length>1?'s':'')+' worth knowing about '+per+(crit?', '+crit+' of them serious':'')+'. '+(w[1]>1?'Most are about '+w[0]+'.':'')}
  x+='<p class="quote" style="margin:4px 4px 14px;min-height:0;text-align:left">“'+esc(q)+'”</p>';
  if(list.length){const groups=[],gi={};list.forEach(e=>{const g=e.level+'|'+e.title;if(gi[g]==null){gi[g]=groups.length;groups.push([e])}else groups[gi[g]].push(e)});
    x+='<div class="list">'+groups.slice(0,150).map(all=>{const e=all[0],n=all.length,k=evKey(e);return'<details class="ev"'+(evOpen.has(k)?' open':'')+'><summary data-evk="'+esc(k)+'"><span class="s '+e.level+'"></span><span class="t"><b>'+esc(e.title)+(n>1?'<span class="x">×'+n+'</span>':'')+'</b><span class="sub2">'+esc((n>1?'last ':'')+ago(e.t)+(e.who&&!e.title.includes(e.who)?' · '+e.who:'')+' · '+evData.cats[e.cat])+'</span></span></summary>'+
    '<div class="body">'+(e.explain?esc(e.explain):'')+'<div class="raw">'+esc(e.msg)+'</div><div class="raw">Event '+e.id+' · '+esc(e.src)+' · '+esc(new Date(e.t*1000).toLocaleString())+'</div>'+
    (n>1?'<div class="times">All '+n+' times: '+all.slice(0,30).map(a=>esc(new Date(a.t*1000).toLocaleString([], {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}))).join(' · ')+(n>30?' …':'')+'</div>':'')+'</div></details>'}).join('')+'</div>'}
  x+='<button class="btn wide" data-evrefresh style="margin-top:4px">'+(evData.busy?'Reading the logs…':'Check again')+'</button>';
  x+='<p class="hint">Only what you\'d need to see: drive errors and retries, crashes and power losses, hardware errors, services and apps that crashed, failed updates and Defender alerts.'+(evData.t?' Checked '+esc(ago(evData.t))+'.':'')+(S&&S.security.admin===false?' Run HAL as administrator to read everything.':'')+'</p>';
  return x}
async function loadEv(refresh){try{evData=await api('/api/winevents'+(refresh?'?refresh=1':''));if(tab==='events')render();if(evData.busy||(!evData.t&&evData.windows)){loadEv.tries=(loadEv.tries||0)+1;if(loadEv.tries<40)setTimeout(()=>loadEv(false),3000)}else loadEv.tries=0}catch(e){}}

/* ---------- navigation, refresh ---------- */
function show(t){tab=t;['status','vitals','services','drives','shield','log','events'].forEach(k=>$('t-'+k).classList.toggle('hidden',k!==t));
  document.querySelectorAll('#tabs button').forEach(b=>b.classList.toggle('on',b.dataset.tab===(t==='log'&&!document.querySelector('#tabs [data-tab="log"]')?'status':t==='events'?'shield':t)));if(t==='events')loadEv(false);
  window.scrollTo(0,0);if(t==='log'){events=null;loadEvents()}if(t==='vitals')loadHist(true);if(t==='drives')loadCleanup();render()}
// Don't redraw under a finger: a refresh that swaps a button between touch-down and touch-up loses the tap.
let pressing=false,renderLater=false;
document.addEventListener('pointerdown',()=>{pressing=true},true);
for(const ev of ['pointerup','pointercancel'])document.addEventListener(ev,()=>{setTimeout(()=>{pressing=false;if(renderLater){renderLater=false;render()}},0)},true);
function render(){if(!S)return;if(pressing){renderLater=true;return}if(tab==='status'){renderStatus();return}const f={vitals:rVitals,services:rServices,drives:rDrives,shield:rShield,log:rLog,events:rEvents}[tab];
  const el=$('t-'+tab);el.innerHTML=f();el.querySelectorAll('[data-hold]').forEach(bindHold)}
async function loadEvents(){try{events=(await api('/api/events')).events;if(S)S.unread=0;if(tab==='log')render()}catch(e){}}
async function loadHist(force){if(!force&&range!=='live'&&Date.now()-histAt<60000)return;try{hist=(await api('/api/history?range='+range)).series;histAt=Date.now();if(tab==='vitals')render()}catch(e){}}
async function loadCleanup(){try{cleanup=await api('/api/cleanup');if(tab==='drives')render();if(cleanup.busy||!cleanup.t)setTimeout(loadCleanup,4000)}catch(e){}}
// Update button: wait for the PC to report back (the server restarts in between), then reload with the new version.
let updWatch=false;
function updateDone(){const u=S&&S.update;if(!updWatch||!u||u.running||!u.result||!u.started||u.result.t<u.started)return;
  updWatch=false;const r=u.result;toast(r.state==='updated'?'Updated to version '+r.version+'. I feel much better now, '+S.hal.name+'.':r.message,r.state==='failed');
  if(r.state==='updated')setTimeout(()=>location.reload(),3000)}
function updBanner(){return (updWatch||(S&&S.update&&S.update.running))?'<div class="banner" style="background:#1a1407;border-color:#4a3609"><b>Updating… I\'ll be back in about a minute'+(S?', '+esc(S.hal.name):'')+'.</b></div>':''}
async function refresh(){if(busy)return;busy=true;try{const first=!S;S=await api('/api/status');lastOk=Date.now();updateDone();if(tab!=='events'||first)render();if(tab==='vitals')loadHist(range==='live')}catch(e){if(!token)return;lastOk=0}finally{busy=false}}
// Opened from the all-in-one app (/app): ?tab=drives opens that page, ?settings=1 opens Settings, and an
// "App" button at the left of the tab bar goes back.
const QP=new URLSearchParams(location.search);
async function start(){$('app').classList.remove('hidden');$('tabs').classList.remove('hidden');
  if(QP.get('embed')==='1'){const st=document.querySelector('#tabs [data-tab="status"]');if(st){st.dataset.tab='log';st.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 12h16M4 18h10"/></svg>Log'}}
  if(QP.get('from')==='app'&&!$('toapp'))$('tabs').insertAdjacentHTML('afterbegin','<button id="toapp" aria-label="Back to the app"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 18l-6-6 6-6"/></svg>App</button>');
  const want=QP.get('tab');if(want&&['status','vitals','services','drives','shield','log','events'].includes(want)&&!start.done)show(want);
  await refresh();
  if(QP.get('settings')==='1'&&!start.done)openSettings();
  start.done=true;
  if(!start.t)start.t=setInterval(()=>{if(!document.hidden&&token)refresh()},3000)}
document.addEventListener('click',e=>{if(e.target.closest('#toapp')){if(QP.get('embed')==='1'&&window.parent!==window)window.parent.postMessage({hal:'close'},location.origin);else location.href='/app/'}});
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
    try:
        server = ThreadingHTTPServer((args.host, args.port), HalHandler)
    except OSError as exc:
        # Port taken: normally the NZB phone server, which runs HAL itself now. Exit quietly (code 0, so an
        # old "HAL 9000 Server" startup task doesn't keep retrying) instead of watching everything twice.
        log.info("Port %s is already in use (%s): HAL is probably running inside the NZB phone server. Not starting a second copy.",
                 args.port, exc)
        print(f"Port {args.port} is already in use: HAL is probably running inside the NZB phone server already.")
        return
    HAL = make_demo_hal(args.demo_ups) if args.demo else Hal()
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
