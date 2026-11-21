#!/usr/bin/env python3
"""
NZB Command Center - Phone App (v3)

A phone version of NZB Command Center, served on your home network:
  * Home   - current download (with "ready ~9:40 PM"), what's playing on Kodi and Jellyfin,
             "Just arrived" movies with Play on TV, phone alerts (bell), pull down to refresh,
             current download, pause/resume, today's done/failed, free space,
             data downloaded today / this week / this month, Kodi now playing
  * Search - your Newznab indexers, with a category drop-down per indexer; send to SABnzbd
  * Queue  - SABnzbd queue and history (pause, delete, retry, speed limit)
  * Kodi   - now playing with a full remote, movies and TV shows (continue watching,
             next episode, missing episodes), "find a better copy" for a movie,
             "TV on + Kodi" / "Open Kodi" buttons for a Sony TV (IP control), and
             "Type on TV": type on the phone into the text box open in Kodi
  * Jellyfin - now playing (pause / stop), library scan, "do I already have it?" search with
             quality, missing episodes, movies below 1080p, show seasons; connect from the phone
  * /app   - the all-in-one HAL 9000 app (Home, Get, Watch, System) built on the same API; it opens
             signed in with the code this phone already saved for the NZB app. Its page is app_page.py.
  * HAL 9000 - runs inside this server (the first step towards one all-in-one app): open /hal on
             the phone, or HAL's own port 8766 as before. Needs psutil and hal_server.py + system_monitor.py
             in this folder; without them everything else works and the banner says why HAL is off.
  * Drives - every drive's health, temperature, free space, live read/write activity,
             health counters (errors, wear, hours, cycles), volumes and identity, a 24 h
             temperature graph, and phone alerts for problems, heat and low space
The page and the icons are in mobile_page.py and mobile_icons.py: keep them next to this file.
It reads the SAME nzb_manager_config.json (and DPAPI-protected keys) as the desktop
app, including its Kodi settings, and never modifies it. Phone-only
settings (alerts, saved searches) live in mobile_settings.json. Add the page to your
phone's home screen to open it like an app.

Place this file in the SAME folder as nzb_manager_config.json.
Standard library only (Python 3.9+).

    python mobile_server.py                 # LAN only (private networks)
    python mobile_server.py --port 9000
    python mobile_server.py --allow-cidr 203.0.113.0/24
    python mobile_server.py --allow-any-client   # NOT recommended
    python mobile_server.py --install-autostart  # start hidden whenever you log in to Windows
    python mobile_server.py --remove-autostart

Security model
  * Every /api/ call needs the access code (X-Mobile-Token header, constant-time compare).
  * Only private / loopback / link-local / Tailscale clients are served by default.
  * Repeated wrong codes lock that client out temporarily.
  * The server downloads NZBs itself; the phone can never make it fetch an arbitrary URL.
  * Indexer API keys are only ever attached to the indexer's own host, and never over a
    scheme downgrade. Secrets are redacted from every error shown or logged.
"""

from __future__ import annotations

import argparse
import base64
import difflib
import ctypes
import ctypes.wintypes
import gzip
import hashlib
import html
import http.client
import io
import ipaddress
import json
import logging
import logging.handlers
import os
import re
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "3.25.2"
# When packaged as an .exe, __file__ points into a temporary folder that is deleted on exit,
# so settings and caches go next to the .exe instead.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "nzb_manager_config.json"
ACCESS_KEY_FILE = BASE_DIR / "mobile_access_key.txt"
LOG_FILE = BASE_DIR / "mobile_server.log"

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765
MAX_RESULTS_PER_INDEXER = 50
MAX_TOTAL_RESULTS = 200
MAX_BATCH_ADD = 10
BATCH_ADD_WORKERS = 3               # NZBs downloaded in parallel for a batch add
REQUEST_TIMEOUT = 20
NZB_DOWNLOAD_TIMEOUT = 45
MAX_NZB_BYTES = 50 * 1024 * 1024
MAX_API_BYTES = 20 * 1024 * 1024
MAX_SAB_BYTES = 8 * 1024 * 1024
MAX_BODY_BYTES = 32_000
RESULT_TTL = 30 * 60
MAX_CACHED_RESULTS = 2000
CLIENT_SOCKET_TIMEOUT = 30          # drops stalled clients (slow-loris) instead of pinning a thread
AUTH_MAX_FAILURES = 8
AUTH_WINDOW = 300                   # seconds
SEARCH_RATE = (20, 60)              # searches per client per minute (each one spends indexer API hits)
ADD_RATE = (40, 60)
USER_AGENT = f"NZBCommandCenter-Mobile/{VERSION}"
SAB_PRIORITIES = {"-100": "Default", "-1": "Low", "0": "Normal", "1": "High", "2": "Force"}

TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")
EXTRA_ALLOWED_NETS: list = []
ALLOW_ANY_CLIENT = False

log = logging.getLogger("mobile")

# HAL 9000 (hal_server.py) runs inside this server: at /hal and on its own port (8766), with this server's
# access code, log and startup task. Optional: without psutil or hal_server.py the phone app works as before.
try:
    import hal_server as _hal_mod
    HAL_IMPORT_ERROR = ""
except Exception as _exc:  # noqa: BLE001 - any import problem just means "no HAL"
    _hal_mod = None
    HAL_IMPORT_ERROR = ("psutil isn't installed (pip install psutil)" if isinstance(_exc, ImportError) and "psutil" in str(_exc)
                        else f"{type(_exc).__name__}: {_exc}")
HAL_PORT = 8766
_hal_state = {"running": False, "note": "not started", "server": None}

# Home-screen icons (mobile_icons.py, next to this file). Without that file the server still runs, with a plain icon.
try:
    from mobile_icons import APP_ICON_PNG_B64, KODI_ICON_PNG_B64, KODI_ICON_MASKABLE_PNG_B64
    MISSING_PARTS: list[str] = []
except ImportError:
    _PLAIN_PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk0Gb4DwABbAEbm5A2ZwAAAABJRU5ErkJggg==",)
    APP_ICON_PNG_B64 = KODI_ICON_PNG_B64 = KODI_ICON_MASKABLE_PNG_B64 = _PLAIN_PNG
    MISSING_PARTS = ["mobile_icons.py"]
ICON_PNG = base64.b64decode("".join(APP_ICON_PNG_B64))
KODI_ICON_PNG = base64.b64decode("".join(KODI_ICON_PNG_B64))
KODI_ICON_MASKABLE_PNG = base64.b64decode("".join(KODI_ICON_MASKABLE_PNG_B64))


# ==============================================================================
# 1. ERRORS, REDACTION, SMALL HELPERS
# ==============================================================================

class UpstreamError(RuntimeError):
    """Failure talking to an indexer or SABnzbd. The message is already safe to show to the phone."""


class ConfigError(RuntimeError):
    """Problem with the desktop configuration. The message contains no secrets."""


_KNOWN_SECRETS: frozenset[str] = frozenset()
_EXTRA_SECRETS: frozenset[str] = frozenset()  # phone-only secrets (the ntfy access token)
_JF_SECRETS: frozenset[str] = frozenset()     # the Jellyfin API key in use
_ARR_SECRETS: frozenset[str] = frozenset()    # the Sonarr / Radarr API keys found on this PC
_SECRET_QS = re.compile(r"(?i)\b(apikey|api_key|passkey|token|r)=([^&\s'\"<>]+)")


def redact(text) -> str:
    """Strips API keys from any text (query-string style, plus every key currently configured)."""
    out = _SECRET_QS.sub(lambda m: f"{m.group(1)}=***", str(text))
    for secret in _KNOWN_SECRETS | _EXTRA_SECRETS | _JF_SECRETS | _ARR_SECRETS:  # frozensets that are only ever swapped whole, never mutated
        if secret and len(secret) >= 6:
            out = out.replace(secret, "***")
    return out


def format_bytes(value) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if number < 1024 or unit == "TB":
            return f"{number:.1f} {unit}" if unit != "B" else f"{int(number)} B"
        number /= 1024
    return "0 B"


def to_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def pub_timestamp(pub_date: str) -> float:
    try:
        return parsedate_to_datetime(pub_date).timestamp()
    except Exception:
        return 0.0


def sanitize_xml(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    # Escape bare ampersands (indexers emit them in titles). This also neutralises custom entity references.
    return re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)", "&amp;", text)


def safe_xml_fromstring(raw: bytes):
    text = sanitize_xml(raw)
    if re.search(r"<!ENTITY", text, re.I):
        raise ValueError("XML containing entity declarations was rejected.")
    return ET.fromstring(text)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-applies the download safety rules to every redirect hop.

    Plain urlopen() follows redirects blindly, so a public CDN link that passed the
    private-address check could bounce this server to 192.168.x.x, or an HTTPS link could
    be downgraded to HTTP. Redirects to the SAME host (e.g. a LAN NZBHydra/Prowlarr talking
    to itself) are allowed; any other host must resolve only to public addresses.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(urllib.parse.urljoin(req.full_url, newurl))
        if new.scheme not in ("http", "https") or not new.hostname:
            raise UpstreamError("Refused a redirect to an unsupported URL.")
        if old.scheme == "https" and new.scheme != "https":
            raise UpstreamError("Refused a redirect from HTTPS to plain HTTP.")
        same_host = new.hostname.lower() == (old.hostname or "").lower()
        if not same_host and not host_is_public(new.hostname):
            raise UpstreamError("Refused a redirect to a private/local address.")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and not same_host:
            # urllib copies every header to the new host. Credentials (Kodi's Basic auth, Jellyfin's
            # key, the TV's PSK, the ntfy token...) must only ever go to the host they were meant for.
            for name in list(redirected.headers) + list(redirected.unredirected_hdrs):
                low = name.lower()
                if low in ("authorization", "cookie", "proxy-authorization") or any(
                        w in low for w in ("token", "key", "auth", "psk", "secret", "password")):
                    redirected.headers.pop(name, None)
                    redirected.unredirected_hdrs.pop(name, None)
        return redirected


_OPENER = urllib.request.build_opener(_SafeRedirectHandler())


def http_fetch(url: str, *, timeout: float, max_bytes: int, data: bytes | None = None,
               headers: dict | None = None) -> bytes:
    """Size-capped http(s) request. Raises UpstreamError with a message that never contains the URL."""
    if not url.lower().startswith(("http://", "https://")):
        raise UpstreamError("Only http:// and https:// URLs are supported.")
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST" if data is not None else "GET")
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            body = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(300).decode("utf-8", errors="replace").strip()
        except Exception:
            detail = ""
        hint = {401: " — check the API key.", 403: " — access denied.", 429: " — rate limited, try again later."}.get(exc.code, "")
        extra = f": {redact(detail[:160])}" if (detail and not detail.startswith("<") and not hint) else ""
        raise UpstreamError(f"HTTP {exc.code}{hint}{extra}") from None
    except urllib.error.URLError as exc:
        raise UpstreamError(f"Connection failed: {redact(exc.reason)}") from None
    except OSError as exc:  # timeouts, resets
        raise UpstreamError(f"Network error: {redact(exc)}") from None
    except (http.client.HTTPException, ValueError) as exc:
        # InvalidURL (its message contains the full URL, API key included), BadStatusLine (not an
        # HTTP server on that port), IncompleteRead... Never let these escape unconverted: callers
        # only expect UpstreamError, and the raw text would put the key in the log.
        raise UpstreamError(f"Bad response or address ({type(exc).__name__}): {redact(exc)[:160]}") from None
    if len(body) > max_bytes:
        raise UpstreamError(f"Response exceeds the {max_bytes // (1024 * 1024)} MB safety limit.")
    return body


# ==============================================================================
# 2. ACCESS TOKEN, DPAPI, CONFIG (hot-reloaded when the desktop app saves)
# ==============================================================================

_token_bytes = b""


def load_or_create_access_token(path: Path | None = None) -> str:
    """Loads the stable pairing code from a companion-only file (created on first run)."""
    path = path or ACCESS_KEY_FILE
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        token = secrets.token_urlsafe(32)
        try:
            with path.open("x", encoding="utf-8") as handle:  # exclusive create: never clobber a key
                handle.write(token + "\n")
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except FileExistsError:
            token = path.read_text(encoding="utf-8").strip()
    if len(token) < 40 or any(ch.isspace() for ch in token):
        raise ConfigError(
            f"{path.name} exists but does not contain a valid key. "
            "Stop the server, delete that file, and restart to generate a new key."
        )
    return token


def _dpapi_unprotect(stored: str) -> str:
    """Decrypts the desktop app's dpapi:<base64> values (same Windows user only)."""
    if not stored:
        return ""
    if not stored.startswith("dpapi:"):
        return stored
    if os.name != "nt":
        raise ConfigError("A DPAPI-protected key can only be read on the Windows PC/user that encrypted it.")

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = ctypes.wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    try:
        raw = base64.b64decode(stored[6:], validate=True)
    except ValueError:  # binascii.Error: a damaged value in the config file
        raise ConfigError("A saved API key in the desktop configuration is damaged.") from None
    in_buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    in_blob = DATA_BLOB(len(raw), in_buf)
    out_blob = DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise ConfigError("Windows could not decrypt a saved API key. Run this server as the same Windows user as the desktop app.")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _indexer_id(name: str, url: str) -> str:
    return hashlib.sha256(f"{name}|{url}".encode("utf-8")).hexdigest()[:10]


def load_config(path: Path | None = None) -> dict:
    path = path or CONFIG_FILE
    if not path.exists():
        raise ConfigError(f"Could not find {path.name}. Put mobile_server.py beside your NZB Command Center configuration.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ConfigError(f"{path.name} is not valid JSON ({exc}).") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} does not contain a JSON object.")

    sab_url = str(data.get("sab_url") or "http://127.0.0.1:8080").strip().rstrip("/")
    if not sab_url.startswith(("http://", "https://")):
        sab_url = "http://" + sab_url
    if sab_url.endswith("/api"):
        sab_url = sab_url[:-4]
    # A SABnzbd key Windows can no longer decrypt (its DPAPI keys were reset, e.g. after a password
    # or sign-in change) must not stop the server: everything else keeps working, and SABnzbd
    # requests say what to fix. Only a key that was never set at all is a startup error.
    sab_key_error = ""
    try:
        sab_key = _dpapi_unprotect(str(data.get("sab_api_key") or "")).strip()
    except ConfigError:
        sab_key = ""
        sab_key_error = ("The saved SABnzbd API key could not be decrypted on this PC — "
                         "re-enter it in the desktop app's Settings.")
        log.warning("SABnzbd API key could not be decrypted; SABnzbd will be unavailable until it is re-entered.")

    indexers: list[dict] = []
    seen: set[str] = set()
    for item in data.get("indexers", []) or []:
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        url = str(item.get("url") or "").strip().split("?", 1)[0].rstrip("/")
        if not url:
            continue
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        if "omgwtfnzbs.org/index" in url.lower():
            url = "https://api.omgwtfnzbs.me/api"
        elif not url.endswith(("/api", "/api.php", "/rss")):
            url += "/api"
        name = str(item.get("name") or "Indexer")
        ix_id = _indexer_id(name, url)
        if ix_id in seen:
            continue
        seen.add(ix_id)
        # One indexer whose key can't be decrypted must not take the whole server down:
        # keep it listed, but searches against it report why it can't be used.
        key_error = ""
        try:
            api_key = _dpapi_unprotect(str(item.get("api_key") or "")).strip()
        except ConfigError:
            api_key = ""
            key_error = "Its saved API key could not be decrypted — re-enter it in the desktop app's Settings."
            log.warning("Indexer '%s': API key could not be decrypted; it will be unavailable.", name)
        indexers.append({
            "id": ix_id,
            "name": name,
            "url": url,
            "api_key": api_key,
            "key_error": key_error,
        })

    if not sab_key and not sab_key_error:
        raise ConfigError("SABnzbd API key is missing in nzb_manager_config.json.")

    kodi_pw = ""
    try:
        kodi_pw = _dpapi_unprotect(str(data.get("kodi_password") or ""))
    except ConfigError:
        log.warning("Kodi password could not be decrypted; Kodi requests may be refused.")
    kodi = {"url": normalize_kodi_url(str(data.get("kodi_url") or "")),
            "user": str(data.get("kodi_username") or ""), "password": kodi_pw}

    global _KNOWN_SECRETS
    # Swap in a complete new set at once, so a concurrent redact() never sees an empty/partial set.
    _KNOWN_SECRETS = frozenset(([sab_key] if sab_key else []) + [ix["api_key"] for ix in indexers if ix["api_key"]]
                               + ([kodi_pw] if kodi_pw else []))
    return {"sab_url": sab_url, "sab_api_key": sab_key, "sab_key_error": sab_key_error,
            "indexers": indexers, "kodi": kodi}


def _sab_key(cfg: dict) -> str:
    """The SABnzbd API key, or a readable error if it couldn't be decrypted."""
    if not cfg.get("sab_api_key"):
        raise UpstreamError(cfg.get("sab_key_error") or "SABnzbd API key is missing — set it in the desktop app.")
    return cfg["sab_api_key"]


_cfg_lock = threading.Lock()
_cfg_state: dict = {"mtime": None, "data": None, "retry_at": 0.0, "failed_mtime": None}
CONFIG_RETRY_SECONDS = 5


def get_config() -> dict:
    """Returns the current desktop configuration, re-reading it whenever the file changes.

    A reload that fails (e.g. the file was caught mid-save) keeps the previous settings and is
    retried every few seconds, so the final save is picked up even if its timestamp doesn't change again.
    """
    try:
        mtime = CONFIG_FILE.stat().st_mtime_ns
    except OSError:
        with _cfg_lock:
            if _cfg_state["data"] is not None:
                return _cfg_state["data"]
        raise ConfigError(f"Could not find {CONFIG_FILE.name}.") from None
    with _cfg_lock:
        if _cfg_state["data"] is not None and _cfg_state["mtime"] == mtime:
            return _cfg_state["data"]
        if _cfg_state["data"] is not None and time.monotonic() < _cfg_state.get("retry_at", 0.0):
            return _cfg_state["data"]  # a recent reload failed; don't re-read the file on every request
        try:
            data = load_config()
        except ConfigError as exc:
            if _cfg_state["data"] is not None:  # e.g. caught the desktop app mid-edit; keep working
                if _cfg_state.get("failed_mtime") != mtime:  # log once per file version, not every retry
                    log.warning("Configuration reload failed (%s); keeping the previous settings and retrying.", redact(exc))
                # mtime is deliberately NOT recorded, so the same file version is read again after the delay.
                _cfg_state.update(retry_at=time.monotonic() + CONFIG_RETRY_SECONDS, failed_mtime=mtime)
                return _cfg_state["data"]
            raise
        first = _cfg_state["data"] is None
        _cfg_state.update(mtime=mtime, data=data, retry_at=0.0, failed_mtime=None)
        if not first:
            log.info("Configuration reloaded: %d enabled indexer(s).", len(data["indexers"]))
        return data


# ==============================================================================
# 3. SABNZBD
# ==============================================================================

def sab_request(mode: str, extra: dict | None = None, timeout: float = 15) -> dict:
    cfg = get_config()
    params = {"mode": mode, "output": "json", "apikey": _sab_key(cfg)}
    if extra:
        params.update(extra)
    body = http_fetch(cfg["sab_url"] + "/api?" + urllib.parse.urlencode(params), timeout=timeout, max_bytes=MAX_SAB_BYTES)
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        raise UpstreamError("SABnzbd returned a non-JSON response — check its URL in the desktop settings.") from None
    if not isinstance(payload, dict):
        raise UpstreamError("SABnzbd returned an unexpected response.")
    if payload.get("status") is False:
        raise UpstreamError(redact(payload.get("error") or "SABnzbd returned an error."))
    return payload


_cat_lock = threading.Lock()
_cat_cache: dict = {"ts": 0.0, "cats": []}


def sab_categories(force: bool = False) -> list[str]:
    now = time.time()
    with _cat_lock:
        if not force and _cat_cache["cats"] and now - _cat_cache["ts"] < 300:
            return list(_cat_cache["cats"])
    cats = [str(c) for c in (sab_request("get_cats").get("categories") or []) if str(c) and str(c) != "*"]
    with _cat_lock:
        _cat_cache.update(ts=now, cats=cats)
    return list(cats)


# Picking a SABnzbd category from the release, the same way the desktop app's "Auto (Match)" does,
# so phone downloads land in the same folders (movies/, tv/, ...) as desktop ones.
CATEGORY_ALIASES = {
    "movies": {"movie", "movies", "film", "films", "cinema"},
    "tv": {"tv", "series", "show", "shows", "television", "episode"},
    "anime": {"anime"},
    "audio": {"audio", "music", "mp3", "flac", "lossless", "soundtrack"},
    "audiobooks": {"audiobook", "audiobooks"},
    "books": {"book", "books", "ebook", "ebooks", "comic", "comics", "magazine", "magazines"},
    "software": {"pc", "software", "app", "apps", "0day", "iso", "mac"},
    "games": {"game", "games", "console", "ps3", "ps4", "ps5", "xbox", "wii", "nds", "psp", "switch"},
    "xxx": {"xxx", "adult"},
}
NEWZNAB_NUM_DOMAIN = {"1": "games", "2": "movies", "3": "audio", "4": "software", "5": "tv", "6": "xxx", "7": "books"}


def infer_domain_and_tokens(item_category: str, title: str) -> tuple[str, set[str]]:
    cat_tokens = set(re.findall(r"[a-z0-9]+", str(item_category or "").lower()))
    title_tokens = set(re.findall(r"[a-z0-9]+", str(title or "").lower()))
    for domain, aliases in CATEGORY_ALIASES.items():
        if cat_tokens & aliases:
            return domain, cat_tokens | title_tokens
    for tok in cat_tokens:
        if len(tok) == 4 and tok.isdigit():
            special = {"5070": "anime", "3030": "audiobooks", "4050": "games"}.get(tok)
            if special or tok[0] in NEWZNAB_NUM_DOMAIN:
                return special or NEWZNAB_NUM_DOMAIN[tok[0]], cat_tokens | title_tokens
    t = str(title or "")
    if re.search(r"\b(S\d{1,2}E\d{1,2}|S\d{1,2}|Season\s*\d+|\d{4}\.\d{2}\.\d{2})\b", t, re.I):
        return "tv", title_tokens
    if re.search(r"\b(audiobook|mp3|flac|lossless|320kbps|v0|web-flac)\b", t, re.I):
        return "audio", title_tokens
    if re.search(r"\b(epub|mobi|azw3|cbr|cbz)\b", t, re.I):
        return "books", title_tokens
    if re.search(r"\b(19\d\d|20\d\d)\b", t) and re.search(
            r"\b(2160p|1080p|720p|480p|4k|uhd|bluray|bdrip|brrip|web-dl|webdl|webrip|remux|x264|x265|hevc)\b", t, re.I):
        return "movies", title_tokens
    return "", cat_tokens | title_tokens


def match_sab_category(sab_cats: list[str], item_category: str = "", title: str = "") -> str:
    """Best SABnzbd category for a release, or "" (SABnzbd's default) when nothing fits well."""
    candidates = [c for c in sab_cats if c and c != "*"]
    if not candidates:
        return ""
    domain, all_tokens = infer_domain_and_tokens(item_category, title)
    domains = [domain] + {"anime": ["tv"], "audiobooks": ["audio"]}.get(domain, [])
    for try_domain in domains:
        aliases = CATEGORY_ALIASES.get(try_domain, set())
        best_cat, best_score = "", 0
        for sab_cat in candidates:
            low = sab_cat.strip().lower()
            tokens = set(re.findall(r"[a-z0-9]+", low))
            if not tokens:
                continue
            score = 20 if aliases and (tokens & aliases or low in aliases) else 0
            score += len(tokens & all_tokens) * 8
            score -= len(tokens - (all_tokens | aliases)) * 3
            if score > best_score:
                best_cat, best_score = sab_cat, score
        if best_score >= 15:
            return best_cat
    return ""


def parse_add_options(body: dict) -> tuple[str, str]:
    """Validates the optional SABnzbd category / priority sent with an add request.

    Category "" (or missing) means Auto: pick one per release, like the desktop app.
    "*" means SABnzbd's own default category."""
    category = str(body.get("category") or "").strip()
    if category and category != "*":
        if not re.fullmatch(r"[\w .\-]{1,60}", category):
            raise ValueError("Invalid SABnzbd category.")
        if category not in sab_categories():
            raise ValueError(f"Unknown SABnzbd category '{category}'.")
    raw_priority = body.get("priority")
    priority = "" if raw_priority is None else str(raw_priority).strip()
    if priority and priority not in SAB_PRIORITIES:
        raise ValueError("Invalid priority.")
    return category, priority


def safe_nzb_filename(title: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(title or "download"))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")[:150] or "download"
    return cleaned if cleaned.lower().endswith(".nzb") else cleaned + ".nzb"


def sab_addfile(nzb_data: bytes, filename: str, category: str = "", priority: str = "") -> dict:
    """Uploads NZB bytes to SABnzbd via its multipart addfile API."""
    cfg = get_config()
    boundary = "----NZBMobile" + secrets.token_hex(16)
    safe_name = safe_nzb_filename(filename)
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="name"; filename="{safe_name}"\r\n'
        "Content-Type: application/x-nzb\r\n\r\n"
    ).encode("utf-8") + nzb_data + f"\r\n--{boundary}--\r\n".encode("ascii")

    params = {"mode": "addfile", "output": "json", "apikey": _sab_key(cfg), "nzbname": safe_name[:-4]}
    if category:
        params["cat"] = category
    if priority != "":
        params["priority"] = priority
    raw = http_fetch(
        cfg["sab_url"] + "/api?" + urllib.parse.urlencode(params),
        timeout=NZB_DOWNLOAD_TIMEOUT, max_bytes=MAX_SAB_BYTES, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        raise UpstreamError("SABnzbd returned an unreadable response to the NZB upload.") from None
    if not isinstance(payload, dict):
        raise UpstreamError("SABnzbd returned an unexpected response to the NZB upload.")
    if payload.get("status") is False or payload.get("error"):
        raise UpstreamError(redact(payload.get("error") or "SABnzbd reported that the NZB upload failed."))
    if payload.get("status") is not True and not payload.get("nzo_ids"):
        raise UpstreamError("SABnzbd did not confirm the NZB upload.")
    return payload


# ==============================================================================
# 3b. KODI (JSON-RPC) — same settings as the desktop app's Kodi tab
# ==============================================================================

MAX_KODI_BYTES = 8 * 1024 * 1024


def normalize_kodi_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    url = url.rstrip("/")
    if url.lower().endswith("/jsonrpc"):
        url = url[: -len("/jsonrpc")]
    parsed = urllib.parse.urlparse(url)
    if not parsed.port and parsed.scheme == "http" and parsed.hostname:
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        url = f"{parsed.scheme}://{host}:8080{parsed.path}"
    return url


BACKEND_RETRY_SECONDS = 20
_down_lock = threading.Lock()
_backend_down: dict[str, tuple[float, str]] = {}


def backend_recently_down(key: str) -> str:
    """The error from a connection failure in the last few seconds, or "" (so polls don't wait on a dead host)."""
    with _down_lock:
        entry = _backend_down.get(key)
        if entry and time.monotonic() < entry[0]:
            return entry[1]
        _backend_down.pop(key, None)
        return ""


def mark_backend(key: str, error: str = "") -> None:
    with _down_lock:
        if error:
            _backend_down[key] = (time.monotonic() + BACKEND_RETRY_SECONDS, error)
        else:
            _backend_down.pop(key, None)


def kodi_configured() -> bool:
    return bool(get_config()["kodi"]["url"])


def kodi_call(method: str, params: dict | None = None, timeout: float = 6):
    k = get_config()["kodi"]
    if not k["url"]:
        raise UpstreamError("Kodi isn't set up — add it in the desktop app's Kodi tab.")
    payload: dict = {"jsonrpc": "2.0", "method": method, "id": 1}
    if params:
        payload["params"] = params
    headers = {"Content-Type": "application/json"}
    if k["user"] or k["password"]:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{k['user']}:{k['password']}".encode()).decode("ascii")
    down_key = "kodi|" + k["url"]
    recent = backend_recently_down(down_key)
    if recent:
        raise UpstreamError(recent)
    try:
        raw = http_fetch(k["url"] + "/jsonrpc", timeout=timeout, max_bytes=MAX_KODI_BYTES,
                         data=json.dumps(payload).encode("utf-8"), headers=headers)
    except UpstreamError as exc:
        msg = str(exc)
        if msg.startswith("HTTP 401"):
            raise UpstreamError("Kodi refused the username / password saved in the desktop app.") from None
        # Only genuine connection failures mean "Kodi is off"; anything else (an HTTP error,
        # a response over the size limit, ...) is reported as-is so it isn't misdiagnosed.
        if msg.startswith(("Connection failed", "Network error")):
            mark_backend(down_key, "Can't reach Kodi — is it on?")
            raise UpstreamError("Can't reach Kodi — is it on?") from None
        raise UpstreamError(f"Kodi: {msg}") from None
    mark_backend(down_key)
    try:
        body = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        raise UpstreamError("Kodi returned an invalid response.") from None
    if isinstance(body, dict) and body.get("error"):
        raise UpstreamError(f"Kodi: {_kdict(body['error']).get('message', 'error')}")
    return body.get("result") if isinstance(body, dict) else None


def _kodi_secs(t) -> int:
    t = t or {}
    return to_int(t.get("hours")) * 3600 + to_int(t.get("minutes")) * 60 + to_int(t.get("seconds"))


def tv_front_app() -> dict | None:
    """The app in front on the TV (through HAL's TV tools), or None if unknown."""
    try:
        return _hal_mod.tv_front() if _hal_mod and _hal_mod.tv_host() else None
    except Exception:  # noqa: BLE001 - never let this break the page
        return None


def kodi_now_playing() -> dict | None:
    """What Kodi is playing, but only if Kodi is on the TV's screen: switch to YouTube and Kodi keeps the movie
    paused in the background, which isn't what's on."""
    playing = _kodi_now_playing()
    if playing:
        front = tv_front_app()
        if front and not front["kodi"] and not front["hal"]:
            return None
    return playing


def _kodi_now_playing() -> dict | None:
    players = kodi_call("Player.GetActivePlayers")
    players = [p for p in players if isinstance(p, dict)] if isinstance(players, list) else []
    video = [p for p in players if p.get("type") == "video"] or players
    if not video:
        return None
    pid = to_int(video[0].get("playerid"))
    item = _kdict(kodi_call("Player.GetItem", {"playerid": pid, "properties": ["title", "showtitle", "season", "episode", "year", "art", "thumbnail", "file"]})).get("item", {})
    props = _kdict(kodi_call("Player.GetProperties", {"playerid": pid, "properties": [
        "percentage", "time", "totaltime", "speed", "currentsubtitle", "subtitleenabled", "currentaudiostream"]}))
    title = str(item.get("title") or item.get("label") or "Playing")
    file = str(item.get("file") or "")
    usenet = is_stream_file(file)
    if usenet:   # Kodi only knows an address: the movie's name is the one kept when it was streamed
        title, yr = stream_title(file, title)
        item["year"] = to_int(yr)
    if item.get("showtitle"):
        sub = f"{item['showtitle']} · S{to_int(item.get('season')):02d}E{to_int(item.get('episode')):02d}"
    else:
        sub = str(item.get("year") or "") if to_int(item.get("year")) else ""
    art = item.get("art") or {}
    poster = _art_ref(art.get("poster") or art.get("tvshow.poster") or art.get("thumb") or item.get("thumbnail"))
    fanart = _art_ref(art.get("fanart") or art.get("tvshow.fanart"))
    return {"playerid": pid, "title": title, "sub": sub, "poster": poster, "fanart": fanart,
            "source": "usenet" if usenet else ("pc" if file else ""),
            "movieid": to_int(item.get("id")) if item.get("type") == "movie" else None,
            "time": _kodi_secs(props.get("time")),
            "total": _kodi_secs(props.get("totaltime")), "pct": to_float(props.get("percentage")),
            "paused": to_int(props.get("speed")) == 0,
            "subtitle": _stream_label(props.get("currentsubtitle")) if props.get("subtitleenabled") else "",
            "audio": _stream_label(props.get("currentaudiostream"))}


def _kdict(value) -> dict:
    """Kodi results are dicts; anything else (e.g. "OK" from an action) is treated as empty."""
    return value if isinstance(value, dict) else {}


def _stream_label(stream) -> str:
    stream = _kdict(stream)
    name, lang = str(stream.get("name") or "").strip(), str(stream.get("language") or "").strip()
    return (name if name.lower() not in ("", lang.lower()) else lang.upper())[:40]


def kodi_volume() -> dict | None:
    try:
        res = _kdict(kodi_call("Application.GetProperties", {"properties": ["volume", "muted"]}))
    except UpstreamError:
        return None
    return {"level": to_int(res.get("volume")), "muted": bool(res.get("muted"))} if res else None


KODI_NAV = {"up": "Input.Up", "down": "Input.Down", "left": "Input.Left", "right": "Input.Right",
            "select": "Input.Select", "back": "Input.Back", "home": "Input.Home", "info": "Input.Info",
            "menu": "Input.ContextMenu", "osd": "Input.ShowOSD"}
KODI_PLAYER_ACTIONS = ("playpause", "stop", "seek_fwd", "seek_back", "seek_pct", "subs_next", "subs_off", "audio_next")


def kodi_control(action: str, pid: int, value=None) -> None:
    """Remote-control actions. Navigation and volume need no player; the rest act on `pid`."""
    if action in KODI_NAV:
        kodi_call(KODI_NAV[action])
    elif action in ("vol_up", "vol_down"):
        kodi_call("Application.SetVolume", {"volume": "increment" if action == "vol_up" else "decrement"})
    elif action == "mute":
        kodi_call("Application.SetMute", {"mute": "toggle"})
    elif action in KODI_PLAYER_ACTIONS:
        if not 0 <= pid <= 10:
            raise ValueError("Nothing is playing on Kodi.")
        if action == "playpause":
            kodi_call("Player.PlayPause", {"playerid": pid})
        elif action == "stop":
            kodi_call("Player.Stop", {"playerid": pid})
        elif action in ("seek_fwd", "seek_back"):
            kodi_call("Player.Seek", {"playerid": pid, "value": {"seconds": 30 if action == "seek_fwd" else -15}})
        elif action == "seek_pct":
            pct = to_float(value, -1.0)
            if not 0 <= pct <= 100:
                raise ValueError("Seek position must be 0-100%.")
            kodi_call("Player.Seek", {"playerid": pid, "value": {"percentage": pct}})
        elif action == "subs_next":
            kodi_call("Player.SetSubtitle", {"playerid": pid, "subtitle": "next", "enable": True})
        elif action == "subs_off":
            kodi_call("Player.SetSubtitle", {"playerid": pid, "subtitle": "off"})
        else:
            kodi_call("Player.SetAudioStream", {"playerid": pid, "stream": "next"})
    else:
        raise ValueError("Invalid player action.")


KODI_TEXT_WINDOWS = {10103: "keyboard", 10109: "numeric"}  # Kodi's on-screen keyboard dialogs
KODI_TEXT_MAX = 500


def kodi_send_text(text: str, done: bool = True) -> dict:
    """Types `text` into Kodi: into the on-screen keyboard when it's open, otherwise into the selected
    text field of whatever dialog is open (e.g. an add-on's "Connect to server" box). It replaces
    what's there. done=True also presses OK on the keyboard."""
    text = str(text or "")
    if len(text) > KODI_TEXT_MAX:
        raise ValueError(f"That's too long (at most {KODI_TEXT_MAX} characters).")
    if any(ord(ch) < 32 for ch in text):
        text = " ".join(text.split())  # pasted line breaks/tabs: Kodi's boxes are one line
    win = kodi_call("GUI.GetProperties", {"properties": ["currentwindow"]}) or {}
    wid = to_int((win.get("currentwindow") or {}).get("id"), 0)
    kodi_call("Input.SendText", {"text": text, "done": bool(done)})
    # Kodi can't tell us whether a text field was selected; outside its keyboard, say what to check.
    return {"ok": True, "window": KODI_TEXT_WINDOWS.get(wid, "field")}


def library_scan() -> dict:
    """The Kodi tab's "Scan library": Jellyfin first, then Kodi.

    With the Jellyfin add-on, new downloads reach Kodi through Jellyfin: Jellyfin has to find them
    first, then the add-on copies them into Kodi. Scanning only Kodi's own folders finds nothing new."""
    out = {"ok": True, "jellyfin": None, "kodi": None}
    if jellyfin_configured():
        try:
            jf_request("/Library/Refresh", post=True, timeout=10)
            jf_clear_cache()
            out["jellyfin"] = True
        except (UpstreamError, ConfigError) as exc:
            out["jellyfin"] = False
            out["jellyfin_problem"] = redact(exc)
    try:
        kodi_call("VideoLibrary.Scan", {"showdialogs": False})
        out["kodi"] = True
    except (UpstreamError, ConfigError) as exc:
        out["kodi"] = False
        out["kodi_problem"] = redact(exc)
    if not out["jellyfin"] and not out["kodi"]:
        raise UpstreamError(out.get("kodi_problem") or out.get("jellyfin_problem") or "Couldn't start a scan.")
    log.info("library scan: jellyfin=%s kodi=%s", out["jellyfin"], out["kodi"])
    return out


def _art_ref(value) -> str:
    """Kodi artwork reference ("image://..."), or "" -- the phone asks /api/kodi/art for the bytes."""
    value = str(value or "")
    return value if value.startswith("image://") and len(value) <= 2000 else ""


IMAGE_MAGIC = ((b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"), (b"GIF8", "image/gif"))


def kodi_art(ref: str) -> tuple[bytes, str]:
    """Fetches artwork through Kodi's own /image/ endpoint (its texture cache). Only Kodi is ever contacted."""
    if not _art_ref(ref):
        raise ValueError("Invalid artwork reference.")
    k = get_config()["kodi"]
    if not k["url"]:
        raise UpstreamError("Kodi isn't set up.")
    headers = {}
    if k["user"] or k["password"]:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{k['user']}:{k['password']}".encode()).decode("ascii")
    raw = http_fetch(k["url"] + "/image/" + urllib.parse.quote(ref, safe=""), timeout=20, max_bytes=12 * 1024 * 1024, headers=headers)
    for magic, ctype in IMAGE_MAGIC:
        if raw.startswith(magic):
            return raw, ctype
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return raw, "image/webp"
    raise UpstreamError("Kodi didn't return an image.")


def kodi_movie_details(movieid: int) -> dict:
    # Kodi rejects a request that names the same property twice ("Invalid params"), so de-duplicate.
    props = list(dict.fromkeys(KODI_MOVIE_PROPS + [
        "plot", "tagline", "genre", "director", "mpaa", "rating", "ratings", "votes", "imdbnumber", "cast", "studio", "premiered",
        "streamdetails"]))
    res = _kdict(kodi_call("VideoLibrary.GetMovieDetails", {"movieid": movieid, "properties": props}))
    mv = res.get("moviedetails") or {}
    if not mv:
        raise ValueError("That movie isn't in Kodi's library any more.")
    out = _kodi_movie(mv)
    art = mv.get("art") or {}
    ratings = mv.get("ratings") or {}
    imdb = ratings.get("imdb") or {}
    other = next((dict(v, source=k) for k, v in ratings.items() if k != "imdb" and isinstance(v, dict)), None)
    imdb_id = str(mv.get("imdbnumber") or "")
    quality, hdr = video_quality(mv.get("streamdetails"))
    out.update({
        "quality": quality, "hdr": hdr,
        "imdb_id": imdb_id if IMDB_RE.fullmatch(imdb_id) else "",
        "fanart": _art_ref(art.get("fanart")),
        "plot": str(mv.get("plot") or ""), "tagline": str(mv.get("tagline") or ""),
        "genres": [str(g) for g in (mv.get("genre") or [])][:5],
        "directors": [str(d) for d in (mv.get("director") or [])][:3],
        "studio": [str(x) for x in (mv.get("studio") or [])][:2],
        "mpaa": re.sub(r"^Rated\s+", "", str(mv.get("mpaa") or "")).strip(),
        "imdb_rating": round(to_float(imdb.get("rating")), 1) if imdb.get("rating") else None,
        "imdb_votes": to_int(imdb.get("votes")) or None,
        "rating": round(to_float((other or {}).get("rating") or mv.get("rating")), 1) or None,
        "rating_source": str((other or {}).get("source") or ""),
        "imdb_url": f"https://www.imdb.com/title/{imdb_id}/" if re.fullmatch(r"tt\d{5,10}", imdb_id) else "",
        "premiered": str(mv.get("premiered") or ""),
        "cast": [{"name": str(c.get("name") or ""), "role": str(c.get("role") or "")} for c in (mv.get("cast") or [])[:8]],
    })
    return out


IMDB_RE = re.compile(r"tt\d{5,10}")


def video_quality(streamdetails) -> tuple[str, str]:
    """("2160p" / "1080p" / "720p" / "SD" / "", HDR type) of the file Kodi has."""
    videos = _kdict(streamdetails).get("video") or [{}]
    v = _kdict(videos[0] if isinstance(videos, list) and videos else {})
    w, h = to_int(v.get("width")), to_int(v.get("height"))
    if w >= 3200 or h >= 2000:
        q = "2160p"
    elif w >= 1800 or h >= 1000:
        q = "1080p"
    elif w >= 1200 or h >= 700:
        q = "720p"
    else:
        q = "SD" if w or h else ""
    return q, str(v.get("hdrtype") or "")


def _norm_title(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower().replace("&", " and ")).strip()
    return re.sub(r"^(the|a|an) ", "", text)


_RELEASE_TV = re.compile(r"^(.*?)[ ._\-]+S(\d{1,2})[ ._\-]?E(\d{1,3})", re.I)
_RELEASE_MOVIE = re.compile(r"^(.*?)[ ._(\[\-]+((?:19|20)\d{2})(?:[ ._)\]\-]|$)")
_lib_lock = threading.Lock()
_lib_state: dict = {"ts": 0.0, "movies": {}, "shows": set(), "episodes": set(), "ok": False}


def kodi_library_index(max_age: float = 600, background: bool = False) -> dict:
    """Normalised titles of what Kodi already has, refreshed at most every 10 minutes (empty if Kodi is unreachable).

    With background=True a stale index is returned immediately and refreshed on a worker thread,
    so a search never waits on a slow or sleeping Kodi.
    """
    with _lib_lock:
        if time.time() - _lib_state["ts"] < max_age:
            return _lib_state
        _lib_state["ts"] = time.time()  # don't hammer an offline Kodi; also stops a second refresh starting
    if background:
        threading.Thread(target=_refresh_library_index, name="kodi-library", daemon=True).start()
        return _lib_state
    return _refresh_library_index()


def _refresh_library_index() -> dict:
    try:
        if not kodi_configured():
            return _lib_state
    except ConfigError:
        return _lib_state
    try:
        movies = _kdict(kodi_call("VideoLibrary.GetMovies", {"properties": ["title", "year"]}, timeout=10)).get("movies", [])
        eps = _kdict(kodi_call("VideoLibrary.GetEpisodes", {"properties": ["showtitle", "season", "episode"]}, timeout=15)).get("episodes", [])
    except (UpstreamError, ConfigError):
        return _lib_state
    except Exception:  # on a worker thread nobody else would see this
        log.exception("Refreshing the Kodi library index failed")
        return _lib_state
    mv_index: dict[str, set[int]] = {}
    for m in movies:
        mv_index.setdefault(_norm_title(m.get("title")), set()).add(to_int(m.get("year")))
    ep_index = {(_norm_title(e.get("showtitle")), to_int(e.get("season")), to_int(e.get("episode"))) for e in eps}
    with _lib_lock:
        _lib_state.update(movies=mv_index, episodes=ep_index, shows={k[0] for k in ep_index}, ok=True)
    return _lib_state


def library_match(release_title: str, lib: dict) -> str:
    """"episode" / "movie" when Kodi already has what this release is, else ""."""
    if not lib.get("ok"):
        return ""
    tv = _RELEASE_TV.match(release_title)
    if tv:
        key = (_norm_title(tv.group(1)), int(tv.group(2)), int(tv.group(3)))
        if key in lib["episodes"]:
            return "episode"
        # "Show.Name.2024.S01E02" style: try without a trailing year
        alt = (re.sub(r" (19|20)\d{2}$", "", key[0]), key[1], key[2])
        return "episode" if alt in lib["episodes"] else ""
    mv = _RELEASE_MOVIE.match(release_title)
    if mv:
        years = lib["movies"].get(_norm_title(mv.group(1)))
        if years and any(abs(y - int(mv.group(2))) <= 1 or not y for y in years):
            return "movie"
    return ""


def _kodi_movie(mv: dict) -> dict:
    resume = mv.get("resume") or {}
    art = mv.get("art") or {}
    return {"id": to_int(mv.get("movieid")), "title": str(mv.get("title") or "Untitled"), "year": to_int(mv.get("year")) or None,
            "runtime": to_int(mv.get("runtime")), "resume": int(to_float(resume.get("position"))),
            "watched": to_int(mv.get("playcount")) > 0, "added": str(mv.get("dateadded") or "")[:19],
            "poster": _art_ref(art.get("poster") or mv.get("thumbnail")),
            "imdb": _rating_of(mv, "imdb"), "tmdb": _rating_of(mv, "themoviedb", "tmdb"),
            "mpaa": re.sub(r"^Rated\s+", "", str(mv.get("mpaa") or "")).strip()[:12],
            "genres": [str(g) for g in (mv.get("genre") or [])][:5]}


def _rating_of(mv: dict, *sources: str):
    ratings = mv.get("ratings") or {}
    for src in sources:
        r = to_float((ratings.get(src) or {}).get("rating"))
        if r > 0:
            return round(r, 1)
    return None


KODI_MOVIE_PROPS = ["title", "year", "runtime", "playcount", "resume", "dateadded", "art", "thumbnail", "ratings", "mpaa", "genre"]
KODI_MOVIE_PAGE = 500               # movies per JSON-RPC call: keeps each response far below MAX_KODI_BYTES
KODI_MAX_MOVIES = 50_000


def kodi_paged(method: str, key: str, params: dict, page: int = KODI_MOVIE_PAGE,
               cap: int = KODI_MAX_MOVIES, timeout: float = 20) -> list[dict]:
    """Fetches a Kodi list in pages so a large library never trips the response-size limit."""
    items: list[dict] = []
    while len(items) < cap:
        start = len(items)
        res = _kdict(kodi_call(method, dict(params, limits={"start": start, "end": start + page}), timeout=timeout))
        chunk = [x for x in (res.get(key) or []) if isinstance(x, dict)]
        items.extend(chunk)
        total = to_int(_kdict(res.get("limits")).get("total"), len(items))
        if len(chunk) < page or len(items) >= total:
            break
    return items


def kodi_all_movies() -> list[dict]:
    """The whole movie library, A-Z."""
    return kodi_paged("VideoLibrary.GetMovies", "movies", {
        "properties": KODI_MOVIE_PROPS, "sort": {"method": "sorttitle", "order": "ascending", "ignorearticle": True}})


_library_cache: dict = {"t": 0.0, "v": {}}


def library_summary() -> dict:
    """Kodi's library in numbers, for the TV's Downloads page: movies, unwatched, added this week, the newest, shows
    and episodes. Kept 10 minutes; while Kodi is off (the TV asleep) the last good answer stays, marked stale."""
    if time.time() - _library_cache["t"] < 600 and _library_cache["v"]:
        return _library_cache["v"]
    try:
        movies = kodi_paged("VideoLibrary.GetMovies", "movies", {"properties": ["title", "year", "playcount", "dateadded"]})
        shows = kodi_paged("VideoLibrary.GetTVShows", "tvshows", {"properties": ["episode"]})
    except (UpstreamError, OSError, ValueError) as exc:
        old = dict(_library_cache["v"])
        if old:
            old["stale"] = True
            return old
        return {"ok": False, "error": str(exc)[:120]}
    week_ago = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 7 * 86400))
    newest = max(movies, key=lambda m: str(m.get("dateadded") or ""), default=None)
    out = {"ok": True, "movies": len(movies), "unwatched": sum(1 for m in movies if not to_int(m.get("playcount"))),
           "added_week": sum(1 for m in movies if str(m.get("dateadded") or "") >= week_ago),
           "newest": (f"{newest.get('title')} ({newest['year']})" if to_int(newest.get("year")) else str(newest.get("title") or "")) if newest else "",
           "shows": len(shows), "episodes": sum(to_int(x.get("episode")) for x in shows), "t": time.time()}
    _library_cache.update(t=time.time(), v=out)
    return out


# ---- TV shows ------------------------------------------------------------------
KODI_SHOW_PROPS = ["title", "year", "art", "thumbnail", "episode", "watchedepisodes", "dateadded", "lastplayed", "genre"]
KODI_EP_PROPS = ["title", "season", "episode", "playcount", "resume", "runtime", "firstaired", "tvshowid", "showtitle"]
EPISODE_SORT = {"method": "episode", "order": "ascending"}


def _kodi_show(s: dict) -> dict:
    art = _kdict(s.get("art"))
    return {"id": to_int(s.get("tvshowid")), "title": str(s.get("title") or s.get("label") or "Untitled"),
            "year": to_int(s.get("year")) or None, "poster": _art_ref(art.get("poster") or s.get("thumbnail")),
            "episodes": to_int(s.get("episode")), "watched": to_int(s.get("watchedepisodes")),
            "added": str(s.get("dateadded") or "")[:19], "lastplayed": str(s.get("lastplayed") or "")[:19],
            "genres": [str(g) for g in (s.get("genre") or [])][:3]}


def _kodi_episode(e: dict) -> dict:
    return {"id": to_int(e.get("episodeid")), "show_id": to_int(e.get("tvshowid")), "show": str(e.get("showtitle") or ""),
            "title": str(e.get("title") or e.get("label") or "Episode"),
            "season": to_int(e.get("season")), "episode": to_int(e.get("episode")),
            "watched": to_int(e.get("playcount")) > 0, "resume": int(to_float(_kdict(e.get("resume")).get("position"))),
            "runtime": to_int(e.get("runtime")), "aired": str(e.get("firstaired") or "")[:10]}


MAX_GAP_EPISODE = 500  # beyond this the numbering is dates/absolute numbers, not real gaps


def episode_gaps(numbers) -> list[int]:
    """Episode numbers missing before the highest one Kodi has (Kodi can't know about later ones).

    Seasons numbered by date (e.g. E20241113) or with stray huge numbers are skipped: listing
    "gaps" there would be meaningless and could mean building a list of millions of numbers.
    """
    have = {n for n in numbers if n > 0}
    if not have:
        return []
    top = max(have)
    if top > MAX_GAP_EPISODE or top > 3 * len(have) + 10:
        return []
    return [n for n in range(1, top) if n not in have]


def next_episode(episodes: list[dict]) -> dict | None:
    """The episode to watch next: one already started, else the first unwatched one after the last watched."""
    ordered = sorted((e for e in episodes if e["season"] > 0), key=lambda e: (e["season"], e["episode"]))
    started = [e for e in ordered if e["resume"] > 0 and not e["watched"]]
    if started:
        return started[0]
    last = max((i for i, e in enumerate(ordered) if e["watched"]), default=-1)
    return next((e for e in ordered[last + 1:] if not e["watched"]), None)


def kodi_all_shows() -> list[dict]:
    return [_kodi_show(s) for s in kodi_paged("VideoLibrary.GetTVShows", "tvshows", {
        "properties": KODI_SHOW_PROPS, "sort": {"method": "sorttitle", "order": "ascending", "ignorearticle": True}})]


def kodi_show_episodes(show_id: int) -> list[dict]:
    return [_kodi_episode(e) for e in kodi_paged("VideoLibrary.GetEpisodes", "episodes", {
        "tvshowid": show_id, "properties": KODI_EP_PROPS, "sort": EPISODE_SORT}, page=1000, cap=10_000)]


def kodi_show_details(show_id: int) -> dict:
    det = _kdict(_kdict(kodi_call("VideoLibrary.GetTVShowDetails", {"tvshowid": show_id, "properties": list(dict.fromkeys(
        KODI_SHOW_PROPS + ["plot", "studio", "premiered", "imdbnumber"]))})).get("tvshowdetails"))
    if not det:
        raise ValueError("That show isn't in Kodi's library any more.")
    det.setdefault("tvshowid", show_id)
    episodes = kodi_show_episodes(show_id)
    by_season: dict[int, list[dict]] = {}
    for ep in episodes:
        by_season.setdefault(ep["season"], []).append(ep)
    out = _kodi_show(det)
    art = _kdict(det.get("art"))
    out.update({
        "fanart": _art_ref(art.get("fanart")), "plot": str(det.get("plot") or ""),
        "studio": [str(x) for x in (det.get("studio") or [])][:2], "premiered": str(det.get("premiered") or ""),
        "seasons": [{"season": s, "episodes": sorted(eps, key=lambda e: e["episode"]),
                     "missing": episode_gaps(e["episode"] for e in eps) if s > 0 else []}
                    for s, eps in sorted(by_season.items(), key=lambda kv: (kv[0] == 0, kv[0]))],  # specials last
        "next": next_episode(episodes),
    })
    return out


# ---- Voice (HAL's Siri commands): play a title by name, and simple playback control -------------------------
VOICE_FILLER = {"the", "a", "an", "movie", "film", "show", "series", "please", "on", "tv", "kodi", "for", "me", "some", "next", "episode", "of"}


def _voice_norm(s: str, drop_filler: bool = True) -> str:
    words = re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower().replace("&", " and ")).split()
    if drop_filler:
        words = [w for w in words if w not in VOICE_FILLER] or words
    return " ".join(words)


def voice_match(query: str, movies: list[dict], shows: list[dict]) -> tuple[str, dict, float] | None:
    """The movie or show a spoken title means: ("movie" | "show", item, score 0-1), or None if nothing is close.
    Spoken titles lose punctuation and articles, so both sides are compared without them."""
    q = _voice_norm(query)
    if not q:
        return None
    best = None
    for kind, items in (("movie", movies), ("show", shows)):
        for it in items:
            title = _voice_norm(it.get("title", ""))
            if not title:
                continue
            score = difflib.SequenceMatcher(None, q, title).ratio()
            if q == title:
                score = 1.0
            elif f" {q} " in f" {title} " or f" {title} " in f" {q} ":
                score = max(score, 0.86)
            year = str(it.get("year") or "")
            if year and year in q.split():
                score += 0.05
            if best is None or score > best[2]:
                best = (kind, it, score)
    return best if best and best[2] >= 0.62 else None


def _kodi_wait(seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        try:
            kodi_call("JSONRPC.Ping", timeout=3)
            return True
        except (UpstreamError, ConfigError):
            time.sleep(2)
    return False


_voice_lib: dict = {"t": 0.0, "movies": [], "shows": []}


def _voice_library(fresh: bool = False) -> tuple[list, list]:
    """Kodi's movies and shows for matching a spoken title, kept for 10 minutes (a big library takes seconds to read)."""
    if fresh or not _voice_lib["t"] or time.time() - _voice_lib["t"] > 600:
        movies = [{"id": to_int(m.get("movieid")), "title": str(m.get("title") or m.get("label") or ""), "year": to_int(m.get("year")),
                   "resume": int(to_float(_kdict(m.get("resume")).get("position")))} for m in kodi_all_movies()]
        _voice_lib.update(t=time.time(), movies=movies, shows=kodi_all_shows())
    return _voice_lib["movies"], _voice_lib["shows"]


def _voice_play_now(query: str, from_start: bool) -> dict:
    """Finds the title (Kodi is answering) and plays it."""
    movies, shows = _voice_library()
    hit = voice_match(query, movies, shows)
    if not hit and _voice_lib["t"] and time.time() - _voice_lib["t"] > 30:
        movies, shows = _voice_library(fresh=True)        # maybe it's new: read the library again once
        hit = voice_match(query, movies, shows)
    if not hit:
        return {"ok": False, "message": f"I couldn't find {query.strip()} in your library."}
    kind, it, score = hit
    if kind == "movie":
        resume = it["resume"] > 0 and not from_start
        kodi_call("Player.Open", {"item": {"movieid": it["id"]}, "options": {"resume": resume}})
        log.info("voice play movie %s (%s, score %.2f, resume=%s)", it["id"], it["title"], score, resume)
        return {"ok": True, "kind": "movie", "title": it["title"], "year": it["year"], "resumed": resume}
    nxt = next_episode(kodi_show_episodes(it["id"]))
    if not nxt:
        return {"ok": False, "message": f"You've watched every episode of {it['title']} in your library."}
    resume = nxt["resume"] > 0 and not from_start
    kodi_call("Player.Open", {"item": {"episodeid": nxt["id"]}, "options": {"resume": resume}})
    log.info("voice play episode %s (%s S%02dE%02d, score %.2f)", nxt["id"], it["title"], nxt["season"], nxt["episode"], score)
    return {"ok": True, "kind": "episode", "title": it["title"], "season": nxt["season"], "episode": nxt["episode"],
            "episode_title": nxt["title"], "resumed": resume}


def voice_play(query: str, from_start: bool = False, tv_on: bool = False, wait: bool = False) -> dict:
    """'Play Glass Meridian': finds it in Kodi's library and plays it on the TV (resuming, unless from_start).
    A show plays its next episode. When Kodi isn't answering (the TV is off, or Kodi isn't open), the TV is turned on
    or Kodi opened, and the title plays as soon as Kodi answers; the answer comes back at once ("pending"), so Siri
    doesn't time out waiting for the TV."""
    if not kodi_configured():
        return {"ok": False, "message": "Kodi isn't set up yet."}
    awake = False
    try:
        kodi_call("JSONRPC.Ping", timeout=3)
        awake = True
    except (UpstreamError, ConfigError):
        awake = False
    if awake:
        return {**_voice_play_now(query, from_start), "started_tv": False}
    if not tv_configured():
        return {"ok": False, "message": "Kodi isn't answering. Is the TV on?"}
    tv_was_on = tv_power_state() == "on"
    try:
        if tv_was_on:
            tv_open_kodi()
        else:
            tv_start(launch_kodi=True)
    except (UpstreamError, ConfigError) as exc:
        return {"ok": False, "message": f"I couldn't reach the TV: {str(exc)[:80]}"}
    guess = voice_match(query, _voice_lib["movies"], _voice_lib["shows"]) if _voice_lib["t"] else None
    title = guess[1]["title"] if guess else query.strip()

    def later():
        if not _kodi_wait(90):
            log.info("voice play %r: Kodi didn't answer in time", query)
            return
        try:
            _voice_play_now(query, from_start)
        except Exception as exc:  # noqa: BLE001 - a background job: log it
            log.info("voice play %r failed: %s", query, exc)

    if wait:
        if not _kodi_wait(90):
            return {"ok": False, "message": "Kodi isn't answering yet. Try again in a moment."}
        return {**_voice_play_now(query, from_start), "started_tv": not tv_was_on, "opened_kodi": tv_was_on}
    threading.Thread(target=later, daemon=True, name="voice-play").start()
    log.info("voice play %r: %s, playing when Kodi answers", query, "opening Kodi" if tv_was_on else "turning the TV on")
    return {"ok": True, "pending": True, "title": title, "started_tv": not tv_was_on, "opened_kodi": tv_was_on}


def voice_control(action: str) -> dict:
    """pause / resume / stop / back (15 s) / forward (30 s), on whatever Kodi is playing."""
    if action not in ("pause", "resume", "stop", "back", "forward"):
        raise ValueError("Pause, resume, stop, back or forward.")
    if not kodi_configured():
        return {"ok": False, "message": "Kodi isn't set up yet."}
    players = kodi_call("Player.GetActivePlayers")
    players = [p for p in players if isinstance(p, dict)] if isinstance(players, list) else []
    if not players:
        return {"ok": False, "message": "Nothing is playing in Kodi."}
    pid = to_int(players[0].get("playerid"))
    if action in ("pause", "resume"):
        kodi_call("Player.PlayPause", {"playerid": pid, "play": action == "resume"})
    elif action == "stop":
        kodi_call("Player.Stop", {"playerid": pid})
    else:
        kodi_control("seek_back" if action == "back" else "seek_fwd", pid)
    log.info("voice %s", action)
    return {"ok": True, "action": action}


def kodi_continue_watching(limit: int = 6) -> list[dict]:
    """Shows you've started and not finished, most recently watched first, each with its next episode."""
    res = _kdict(kodi_call("VideoLibrary.GetTVShows", {"properties": KODI_SHOW_PROPS,
                           "sort": {"method": "lastplayed", "order": "descending"}, "limits": {"start": 0, "end": 25}}))
    shows = [sh for sh in (_kodi_show(_kdict(raw)) for raw in res.get("tvshows") or [])
             if sh["lastplayed"] and sh["watched"] < sh["episodes"]]
    if not shows:
        return []
    # Ask Kodi for each show's episodes in parallel (one after another this took up to 25 round
    # trips before the Kodi tab could open), then keep the most recently watched order.
    with ThreadPoolExecutor(max_workers=min(6, len(shows))) as pool:
        episodes = list(pool.map(lambda sh: kodi_show_episodes(sh["id"]), shows))
    out = []
    for show, eps in zip(shows, episodes):
        nxt = next_episode(eps)
        if nxt:
            out.append({"show": show, "episode": nxt})
        if len(out) >= limit:
            break
    return out


def kodi_missing_episodes(cap: int = 400) -> list[dict]:
    """Gaps inside each season of every show in Kodi's library."""
    episodes = kodi_paged("VideoLibrary.GetEpisodes", "episodes", {"properties": ["tvshowid", "showtitle", "season", "episode"]},
                          page=5000, cap=300_000, timeout=30)
    by_show: dict[tuple[int, str], dict[int, set[int]]] = {}
    for e in episodes:
        season = to_int(e.get("season"))
        if season > 0:
            key = (to_int(e.get("tvshowid")), str(e.get("showtitle") or ""))
            by_show.setdefault(key, {}).setdefault(season, set()).add(to_int(e.get("episode")))
    out: list[dict] = []
    for (show_id, title), seasons in sorted(by_show.items(), key=lambda kv: _norm_title(kv[0][1])):
        for season in sorted(seasons):
            gaps = episode_gaps(seasons[season])
            if gaps:
                out.append({"show_id": show_id, "show": title, "season": season, "missing": gaps[:60]})
                if len(out) >= cap:
                    return out
    return out


# ==============================================================================
# 3c. SONY TV - power on and open Kodi (Sony "IP control" REST API + Wake-on-LAN)
# ==============================================================================
# On the TV: Settings > Network > Remote start: On, and
# Settings > Network > Home network > IP control > Authentication: "Normal and Pre-Shared Key" + a key.

TV_WAKE_SECONDS = 45       # how long to wait for the TV to come out of standby
TV_KODI_SECONDS = 60       # how long to wait for Kodi to answer after it's opened
MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}$")
_tv_lock = threading.Lock()
_tv_job: dict = {"state": "idle", "message": "", "at": 0.0}


def tv_psk(settings: dict) -> str:
    global _EXTRA_SECRETS
    try:
        key = _dpapi_unprotect(str(settings.get("tv_psk") or ""))
    except ConfigError:
        return ""
    if key and len(key) >= 4 and key not in _EXTRA_SECRETS:
        _EXTRA_SECRETS = _EXTRA_SECRETS | {key}
    return key


def tv_host(settings: dict | None = None) -> str:
    """The TV's address: the one saved for the TV, else the host from the Kodi URL (Kodi runs on the TV)."""
    settings = settings if settings is not None else load_settings()
    host = str(settings.get("tv_host") or "").strip()
    if host:
        return host
    try:
        return urllib.parse.urlsplit(get_config()["kodi"]["url"]).hostname or ""
    except ConfigError:
        return ""


def tv_host_is_local(host: str) -> bool:
    """True only if the TV address points at the home network (every address a name resolves to is private)."""
    name = host.rsplit(":", 1)[0] if re.fullmatch(r".+:\d{1,5}", host) else host
    try:
        addrs = {info[4][0].split("%")[0] for info in socket.getaddrinfo(name, None)}
    except (OSError, ValueError, UnicodeError):
        return False
    try:
        return bool(addrs) and not any(ipaddress.ip_address(a).is_global for a in addrs)
    except ValueError:
        return False


def validate_tv_host(host: str) -> str:
    host = str(host or "").strip()
    if not host:
        return ""
    if "://" in host:
        host = urllib.parse.urlsplit(host).netloc
    m_ = re.fullmatch(r"([A-Za-z0-9.\-]{1,253})(?::(\d{1,5}))?", host)
    if not m_ or (m_.group(2) and not 1 <= int(m_.group(2)) <= 65535):
        raise ValueError("Enter the TV's address like 192.168.1.40.")
    if not tv_host_is_local(host):
        raise ValueError("That address isn't on your home network (or can't be found). Use the TV's IP, like 192.168.1.40.")
    return host


def tv_configured(settings: dict | None = None) -> bool:
    settings = settings if settings is not None else load_settings()
    return bool(tv_host(settings) and tv_psk(settings))


def sony_call(service: str, method: str, params: list | None = None, version: str = "1.0",
              timeout: float = 4, settings: dict | None = None):
    settings = settings if settings is not None else load_settings()
    host, key = tv_host(settings), tv_psk(settings)
    if not host or not key:
        raise UpstreamError("Set up TV control first (TV address and Pre-Shared Key).")
    if not tv_host_is_local(host):
        raise UpstreamError("The TV address isn't on your home network, so the key wasn't sent. Fix it in TV setup.")
    payload = {"method": method, "id": 1, "params": params or [], "version": version}
    url = f"http://{host}/sony/{service}"   # host may carry a :port
    try:
        raw = http_fetch(url, timeout=timeout, max_bytes=512 * 1024, data=json.dumps(payload).encode("utf-8"),
                         headers={"Content-Type": "application/json", "X-Auth-PSK": key})
    except UpstreamError as exc:
        msg = str(exc)
        if msg.startswith("HTTP 403") or msg.startswith("HTTP 401"):
            raise UpstreamError("The TV refused the Pre-Shared Key. Check it matches the TV's IP control setting.") from None
        if msg.startswith(("Connection failed", "Network error")):
            raise UpstreamError("Can't reach the TV.") from None
        raise UpstreamError(f"TV: {msg}") from None
    try:
        body = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        raise UpstreamError("The TV sent an invalid response.") from None
    if isinstance(body, dict) and body.get("error"):
        err = body["error"]
        code = err[0] if isinstance(err, list) and err else err
        if code in (40005,):   # "Display is turned off" style errors
            raise UpstreamError("The TV is in standby.")
        raise UpstreamError(f"TV error {err}")
    return (body.get("result") or [None])[0] if isinstance(body, dict) else None


IRCC_WAKEUP = "AAAAAQAAAAEAAAAuAw=="   # the remote's "power on" code (discrete, not a toggle)


def sony_ircc(code: str, settings: dict | None = None, timeout: float = 3) -> None:
    """Sends a remote-control (IR) code over the network. Some Sony TVs only wake from this."""
    settings = settings if settings is not None else load_settings()
    host, key = tv_host(settings), tv_psk(settings)
    if not host or not key:
        raise UpstreamError("Set up TV control first (TV address and Pre-Shared Key).")
    if not tv_host_is_local(host):
        raise UpstreamError("The TV address isn't on your home network, so the key wasn't sent. Fix it in TV setup.")
    body = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            '<u:X_SendIRCC xmlns:u="urn:schemas-sony-com:service:IRCC:1"><IRCCCode>' + code +
            '</IRCCCode></u:X_SendIRCC></s:Body></s:Envelope>')
    try:
        http_fetch(f"http://{host}/sony/IRCC", timeout=timeout, max_bytes=64 * 1024, data=body.encode("utf-8"),
                   headers={"Content-Type": 'text/xml; charset="UTF-8"',
                            "SOAPACTION": '"urn:schemas-sony-com:service:IRCC:1#X_SendIRCC"', "X-Auth-PSK": key})
    except UpstreamError as exc:
        msg = str(exc)
        if msg.startswith(("HTTP 401", "HTTP 403")):
            raise UpstreamError("The TV refused the Pre-Shared Key. Check it matches the TV's IP control setting.") from None
        if msg.startswith(("Connection failed", "Network error")):
            raise UpstreamError("Can't reach the TV.") from None
        raise UpstreamError(f"TV: {msg}") from None


def tv_power_state(settings: dict | None = None) -> str:
    """'on', 'standby' (network alive, screen off) or 'off' (unreachable)."""
    try:
        res = sony_call("system", "getPowerStatus", timeout=3, settings=settings)
    except UpstreamError as exc:
        if "Pre-Shared Key" in str(exc) or "Set up TV control" in str(exc):
            raise
        return "off"
    return "on" if _kdict(res).get("status") == "active" else "standby"


IRCC_POWER_OFF = "AAAAAQAAAAEAAAAvAw=="   # the remote's "power off" code (discrete, not a toggle)


def _tv_awake() -> bool | None:
    """Whether the TV's screen is awake, asked over ADB (HAL's TV tools): True, False, or None if that can't tell.
    Sony's IP control can say the TV is off while YouTube is on screen, so this is the second opinion."""
    try:
        if not (_hal_mod and _hal_mod.tv_host()):
            return None
        ok, out = _hal_mod.tv_adb("dumpsys power | grep -m1 mWakefulness=", timeout=12)
    except Exception:  # noqa: BLE001
        return None
    if not ok:
        return None
    if "Awake" in out:
        return True
    return False if re.search(r"Asleep|Dozing", out) else None


def _tv_sleep_adb() -> bool:
    """Android's own sleep key over ADB (the remote's power button doing standby)."""
    try:
        ok, out = _hal_mod.tv_adb("input keyevent KEYCODE_SLEEP", timeout=12)
        return bool(ok) and "Error" not in (out or "")
    except Exception:  # noqa: BLE001
        return False


def tv_off() -> dict:
    """Puts the TV in standby if it's on. Sony's IP control first (or the remote's power-off code); when that says
    the TV is off, or isn't set up, ADB is asked too, and the TV's own sleep key used if the screen is awake."""
    settings = load_settings()
    rest = tv_configured(settings)
    state = "unknown"
    if rest:
        try:
            state = tv_power_state(settings)
        except UpstreamError:
            state = "unknown"
    if state == "on":
        try:
            sony_call("system", "setPowerStatus", [{"status": False}], settings=settings)
            return {"ok": True, "already": False, "message": "Turning the TV off."}
        except UpstreamError as exc:
            log.info("tv off: setPowerStatus refused (%s), sending the remote's power-off", exc)
            try:
                sony_ircc(IRCC_POWER_OFF, settings=settings)
                return {"ok": True, "already": False, "message": "Turning the TV off."}
            except UpstreamError as exc2:
                log.info("tv off: power-off code refused too (%s), trying ADB", exc2)
    awake = _tv_awake()
    log.info("tv off: IP control says %s, ADB says the screen is %s", state, {True: "awake", False: "asleep", None: "unknown"}[awake])
    if awake or (state == "on" and awake is None):
        if _tv_sleep_adb():
            return {"ok": True, "already": False, "message": "Turning the TV off."}
        raise UpstreamError("The TV didn't take the off command.")
    if awake is False or state in ("off", "standby"):
        return {"ok": True, "already": True, "message": "The TV is already off."}
    if not rest:
        raise ValueError("Set up TV control first.")
    raise UpstreamError("Can't reach the TV.")


def send_wol(mac: str) -> None:
    """Wake-on-LAN magic packet to the whole local network (helps a TV whose network is asleep)."""
    if not MAC_RE.match(mac or ""):
        return
    packet = b"\xff" * 6 + bytes.fromhex(re.sub(r"[:-]", "", mac)) * 16
    targets = {"255.255.255.255"}
    try:
        targets.add(str(ipaddress.ip_network(get_local_ip() + "/24", strict=False).broadcast_address))
    except ValueError:
        pass
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for target in targets:
            for port in (9, 7):
                try:
                    sock.sendto(packet, (target, port))
                except OSError:
                    pass


def tv_learn_mac(settings: dict) -> None:
    """While the TV is on, remember its network card address so Wake-on-LAN can be used later."""
    if settings.get("tv_mac_manual"):
        return
    try:
        nets = sony_call("system", "getNetworkSettings", [{"netif": ""}], timeout=3, settings=settings) or []
    except UpstreamError:
        return
    host = tv_host(settings)
    best = ""
    for n in nets if isinstance(nets, list) else []:
        mac = str(_kdict(n).get("hwAddr") or "")
        if MAC_RE.match(mac) and (not best or _kdict(n).get("ipAddrV4") == host.split(":")[0]):
            best = mac
    if best and best.lower() != str(settings.get("tv_mac") or "").lower():
        def change(st: dict) -> None:
            st["tv_mac"] = best
        update_settings(change)


def tv_kodi_uri(settings: dict, refresh: bool = False) -> str:
    """The TV's launch address for Kodi (looked up once, then remembered)."""
    cached = str(settings.get("tv_kodi_uri") or "")
    if cached and not refresh:
        return cached
    apps = sony_call("appControl", "getApplicationList", settings=settings) or []
    uri = ""
    for app in apps if isinstance(apps, list) else []:
        a = _kdict(app)
        if "kodi" in str(a.get("title") or "").lower() or "xbmc" in str(a.get("uri") or "").lower():
            uri = str(a.get("uri") or "")
            break
    if not uri:
        raise UpstreamError("Kodi isn't installed on the TV (it's not in the TV's app list).")
    if uri != cached:
        def change(st: dict) -> None:
            st["tv_kodi_uri"] = uri
        update_settings(change)
    return uri


def tv_open_kodi(settings: dict | None = None) -> None:
    """Brings Kodi to the front on the TV (starts it if it isn't running)."""
    settings = settings if settings is not None else load_settings()
    try:
        sony_call("appControl", "setActiveApp", [{"uri": tv_kodi_uri(settings), "data": ""}], timeout=6, settings=settings)
    except UpstreamError as exc:
        if "standby" in str(exc) or "Can't reach" in str(exc):
            raise
        # The remembered launch address may be stale (Kodi reinstalled/updated): look it up again once.
        sony_call("appControl", "setActiveApp", [{"uri": tv_kodi_uri(settings, refresh=True), "data": ""}],
                  timeout=6, settings=settings)


def wait_for_kodi(seconds: float) -> bool:
    if not kodi_configured():
        return True
    url = get_config()["kodi"]["url"]
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        mark_backend("kodi|" + url)   # forget an earlier "Kodi is off" so we really ask again
        try:
            kodi_call("JSONRPC.Ping", timeout=3)
            return True
        except UpstreamError:
            time.sleep(2)
    return False


def _set_tv_job(state: str, message: str = "") -> None:
    with _tv_lock:
        _tv_job.update(state=state, message=message, at=time.time())


def tv_job_view() -> dict:
    with _tv_lock:
        return dict(_tv_job)


def tv_start(launch_kodi: bool = True) -> bool:
    """Starts 'TV on (+ open Kodi)' in the background. False if one is already running."""
    with _tv_lock:
        if _tv_job["state"] in ("waking", "launching", "waiting"):
            return False
        _tv_job.update(state="waking", message="Turning the TV on…", at=time.time())
    threading.Thread(target=_tv_worker, args=(launch_kodi,), daemon=True, name="tv-on").start()
    return True


def _tv_worker(launch_kodi: bool) -> None:
    try:
        settings = load_settings()
        state = tv_power_state(settings)
        log.info("tv on: TV is %s (MAC %s)", state, settings.get("tv_mac") or "not known yet")
        if state != "on":
            mac = str(settings.get("tv_mac") or "")
            deadline = time.monotonic() + TV_WAKE_SECONDS
            next_send, reached, sent_ok = 0.0, state == "standby", False
            while time.monotonic() < deadline:
                if time.monotonic() >= next_send:
                    # Every few seconds, try every way of waking it: Wake-on-LAN, the power API, the remote's IR code.
                    send_wol(mac)
                    for label, fn in (("setPowerStatus", lambda: sony_call("system", "setPowerStatus", [{"status": True}],
                                                                           timeout=3, settings=settings)),
                                      ("IRCC WakeUp", lambda: sony_ircc(IRCC_WAKEUP, settings=settings))):
                        try:
                            fn()
                            reached = sent_ok = True
                            log.info("tv on: %s accepted", label)
                        except UpstreamError as exc:
                            if "Pre-Shared Key" in str(exc):
                                raise
                            log.info("tv on: %s failed: %s", label, redact(exc))
                    next_send = time.monotonic() + 8
                now_state = tv_power_state(settings)
                if now_state == "on":
                    break
                if now_state == "standby":
                    reached = True
                time.sleep(2)
            else:
                if not reached:
                    hint = ("The TV can't be reached while it's off. On the TV turn on Settings › Network › Remote start"
                            + ("." if mac else ", then use Open Kodi once while the TV is on so the app can learn its network address."))
                elif sent_ok:
                    hint = "The TV got the wake command but stayed off. Try turning Remote start off and on again on the TV."
                else:
                    hint = "The TV answered but refused to turn on. Check Remote start is on in the TV's Network settings."
                raise UpstreamError(hint)
            log.info("tv on: TV woke up")
            time.sleep(3)  # the TV's home screen takes a moment after waking
        tv_learn_mac(settings)
        if not launch_kodi:
            _set_tv_job("done", "TV is on.")
            return
        _set_tv_job("launching", "Opening Kodi…")
        for attempt in range(3):
            try:
                tv_open_kodi(settings)
                break
            except UpstreamError:
                if attempt == 2:
                    raise
                time.sleep(3)
        _set_tv_job("waiting", "Waiting for Kodi…")
        if wait_for_kodi(TV_KODI_SECONDS):
            _set_tv_job("done", "Kodi is ready.")
        else:
            _set_tv_job("error", "Kodi opened on the TV but isn't answering yet. Check Kodi's remote control setting.")
        log.info("tv on%s finished", " + kodi" if launch_kodi else "")
    except UpstreamError as exc:
        _set_tv_job("error", str(exc))
    except Exception as exc:  # never leave the phone waiting on a crashed worker
        log.warning("TV start failed: %s", redact(exc))
        _set_tv_job("error", "Something went wrong turning the TV on.")


# ==============================================================================
# 4. NEWZNAB SEARCH
# ==============================================================================

COVER_ATTRS = ("coverurl", "poster", "thumb", "backdropurl")  # Newznab attrs some indexers use for artwork


def _item_from_fields(indexer: dict, title, category, pub_date, nzb_url, size, grabs, cover="") -> dict | None:
    nzb_url = str(nzb_url or "").strip()
    if not nzb_url:
        return None
    cover = str(cover or "").strip()
    return {
        "cover_url": cover[:1000] if cover.lower().startswith(("http://", "https://")) else "",
        "title": str(title or "Untitled release").strip(),
        "category": str(category or "General").strip(),
        "size_bytes": to_int(size),
        "date": str(pub_date or "").strip(),
        "pub_ts": pub_timestamp(str(pub_date or "")),
        "grabs": to_int(grabs),
        "nzb_url": nzb_url,
        "indexer_id": indexer["id"],
        "indexer_name": indexer["name"],
        "indexer_url": indexer["url"],
    }


def parse_newznab_xml(raw: bytes, indexer: dict) -> list[dict]:
    head = raw[:300].decode("utf-8", errors="replace").lstrip().lower()
    if head.startswith(("<!doctype html", "<html")):
        raise UpstreamError("Indexer returned HTML instead of Newznab XML — check its URL/API key.")
    root = safe_xml_fromstring(raw)
    if root.tag.split("}")[-1].lower() == "error":
        raise UpstreamError(redact(root.attrib.get("description") or "Indexer returned an API error."))
    results = []
    for item in root.findall(".//item"):
        enclosure = item.find("enclosure")
        link = (item.findtext("link") or "").strip()
        size = to_int(enclosure.attrib.get("length")) if enclosure is not None else 0
        grabs, cover = 0, ""
        for child in item:
            if child.tag.split("}")[-1].lower() == "attr":
                name = child.attrib.get("name", "").lower()
                val = child.attrib.get("value", "")
                if name == "size" and not size:
                    size = to_int(val)
                elif name == "grabs":
                    grabs = to_int(val)
                elif name in COVER_ATTRS and not cover and val.strip().lower().startswith(("http://", "https://")):
                    cover = val.strip()
        parsed = _item_from_fields(
            indexer, item.findtext("title"), item.findtext("category"), item.findtext("pubDate"),
            (enclosure.attrib.get("url") if enclosure is not None else "") or link, size, grabs, cover,
        )
        if parsed:
            results.append(parsed)
    return results


def parse_newznab_json(raw: bytes, indexer: dict) -> list[dict]:
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        raise UpstreamError("Indexer returned invalid JSON.") from None
    if not isinstance(data, dict):
        raise UpstreamError("Indexer returned an unexpected JSON structure.")
    if data.get("error"):
        raise UpstreamError(redact(f"Indexer error: {str(data['error'])[:160]}"))
    channel = data.get("channel") if isinstance(data.get("channel"), dict) else data
    items = channel.get("item") or []
    if isinstance(items, dict):
        items = [items]
    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        enc = item.get("enclosure") if isinstance(item.get("enclosure"), dict) else {}
        enc = enc.get("@attributes", enc)
        attrs: dict[str, str] = {}
        raw_attrs = item.get("attr") or []
        if isinstance(raw_attrs, dict):
            raw_attrs = [raw_attrs]
        for entry in raw_attrs:
            if isinstance(entry, dict):
                entry = entry.get("@attributes", entry)
                name = str(entry.get("name", "")).lower()
                if name:
                    attrs.setdefault(name, str(entry.get("value", "")))
        parsed = _item_from_fields(
            indexer, item.get("title"), item.get("category"), item.get("pubDate"),
            enc.get("url") or item.get("link"), to_int(enc.get("length")) or to_int(attrs.get("size")), attrs.get("grabs"),
            next((attrs[k] for k in COVER_ATTRS if str(attrs.get(k, "")).lower().startswith(("http://", "https://"))), ""),
        )
        if parsed:
            results.append(parsed)
    return results


def search_one(indexer: dict, query: str, category: str, limit: int, plan: dict | None = None) -> list[dict]:
    if not indexer["api_key"]:
        raise UpstreamError(indexer.get("key_error") or "API key is blank in the desktop configuration.")
    params: dict[str, object] = {"t": "search", "apikey": indexer["api_key"], "limit": limit}
    if query:
        params["q"] = query
    if category:
        params["cat"] = category
    if plan:  # a Newznab TV / movie search (t=tvsearch / t=movie and their own parameters)
        params.update(plan)
    url = indexer["url"] + "?" + urllib.parse.urlencode(params)
    raw = http_fetch(url, timeout=REQUEST_TIMEOUT, max_bytes=MAX_API_BYTES)

    if raw.lstrip()[:1] in (b"{", b"["):  # some indexers answer in JSON regardless; no second API hit needed
        return parse_newznab_json(raw, indexer)
    try:
        return parse_newznab_xml(raw, indexer)
    except (ET.ParseError, ValueError) as xml_err:
        try:
            return parse_newznab_json(http_fetch(url + "&o=json", timeout=REQUEST_TIMEOUT, max_bytes=MAX_API_BYTES), indexer)
        except Exception:
            raise UpstreamError(f"Indexer returned malformed XML ({redact(xml_err)}).") from None


_TV_QUERY = re.compile(r"^(.*?)[ ._\-]+S(\d{1,2})(?:[ ._\-]?E(\d{1,3}))?$", re.I)


def search_plan(query: str, imdbid: str = "") -> dict | None:
    """A Newznab TV or movie search when the request allows one ("Show S02E04", or an IMDb id)."""
    if imdbid:
        return {"t": "movie", "imdbid": imdbid[2:]}
    m = _TV_QUERY.match(query.strip())
    if m and m.group(1).strip():
        plan: dict = {"t": "tvsearch", "q": m.group(1).strip(), "season": int(m.group(2))}
        if m.group(3):
            plan["ep"] = int(m.group(3))
        return plan
    return None


def search_indexer(indexer: dict, query: str, category: str, limit: int, plan: dict | None) -> list[dict]:
    """Uses the precise TV/movie search when it applies, falling back to a plain text search
    if the indexer doesn't support it or finds nothing that way."""
    if plan:
        try:
            found = search_one(indexer, query, category, limit, plan)
            if found:
                return found
        except UpstreamError as exc:
            if not query or str(exc).startswith(("Connection failed", "Network error", "HTTP 401", "HTTP 429")):
                raise
        if not query:
            return []
    return search_one(indexer, query, category, limit)


def run_search(query: str, chosen: str, category: str, imdbid: str = "") -> tuple[list[dict], list[str], int]:
    """Searches the chosen indexers in parallel. Returns (merged results, per-indexer errors, indexers searched)."""
    query = str(query or "").strip()[:180]
    category = str(category or "").strip()
    if category and not CATEGORY_RE.match(category):
        raise ValueError("Category must be numeric Newznab IDs, e.g. 2000 or 5030,5040.")
    if imdbid and not IMDB_RE.fullmatch(imdbid):
        raise ValueError("Invalid IMDb id.")
    indexers = get_config()["indexers"]
    if not indexers:
        raise ValueError("No enabled indexers were found in the desktop configuration.")
    chosen = str(chosen or "*")
    selected = indexers if chosen == "*" else [x for x in indexers if x["id"] == chosen]
    if not selected:
        raise ValueError("Selected indexer does not exist.")
    plan = search_plan(query, imdbid)

    combined: list[dict] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(6, len(selected))) as pool:
        futures = {pool.submit(search_indexer, ix, query, category, MAX_RESULTS_PER_INDEXER, plan): ix for ix in selected}
        for future in as_completed(futures):
            ix = futures[future]
            try:
                combined.extend(future.result())
            except UpstreamError as exc:
                errors.append(f"{ix['name']}: {exc}")
            except Exception:
                log.exception("Unexpected error searching %s", ix["name"])
                errors.append(f"{ix['name']}: unexpected error (see mobile_server.log)")
    merged = merge_duplicate_results(combined)
    merged.sort(key=lambda x: (x["pub_ts"], x["size_bytes"]), reverse=True)  # newest survive the cap
    return merged[:MAX_TOTAL_RESULTS], errors, len(selected)


def result_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(title or "").lower())


def merge_duplicate_results(items: list[dict]) -> list[dict]:
    """Groups identical titles across indexers, keeping every source so a failed download can fall back."""
    merged: dict[str, dict] = {}
    for item in items:
        key = result_key(item["title"]) or secrets.token_hex(8)
        source = {
            "indexer_id": item["indexer_id"], "indexer_name": item["indexer_name"],
            "indexer_url": item["indexer_url"], "nzb_url": item["nzb_url"],
        }
        if key not in merged:
            entry = {k: v for k, v in item.items() if k not in ("nzb_url", "indexer_id", "indexer_name", "indexer_url")}
            entry["sources"] = [source]
            entry["also_found"] = []
            merged[key] = entry
            continue
        entry = merged[key]
        if all(s["indexer_id"] != source["indexer_id"] for s in entry["sources"]):
            entry["sources"].append(source)
            entry["also_found"].append(source["indexer_name"])
        if not entry.get("cover_url") and item.get("cover_url"):
            entry["cover_url"] = item["cover_url"]
        entry["size_bytes"] = max(entry["size_bytes"], item["size_bytes"])
        entry["grabs"] = max(entry["grabs"], item["grabs"])
        entry["pub_ts"] = max(entry["pub_ts"], item["pub_ts"])
    return list(merged.values())


_results_lock = threading.Lock()
_results: dict[str, tuple[float, dict]] = {}


def cache_results(items: list[dict]) -> list[dict]:
    """Stores results server-side (so the phone only ever sends back an opaque id) and returns the public view."""
    now = time.time()
    out = []
    with _results_lock:
        for key in [k for k, (ts, _) in _results.items() if now - ts > RESULT_TTL]:
            del _results[key]
        while _results and len(_results) + len(items) > MAX_CACHED_RESULTS:
            del _results[next(iter(_results))]
        for item in items:
            result_id = secrets.token_urlsafe(12)
            _results[result_id] = (now, item)
            out.append({
                "id": result_id, "title": item["title"], "indexer": item["sources"][0]["indexer_name"],
                "category": item["category"], "size": format_bytes(item["size_bytes"]),
                "size_bytes": item["size_bytes"], "date": item["date"], "pub_ts": item["pub_ts"],
                "grabs": item["grabs"], "also_found": item["also_found"],
                "kind": release_kind(item["title"], item["category"]), "res": release_resolution(item["title"]),
                "art": bool(item.get("cover_url") or artwork_query(item["title"], item["category"])[0]),
            })
    return out


# ---- Search-result artwork (same sources as the desktop app) ---------------------
MAX_ART_BYTES = 2 * 1024 * 1024        # per image
ART_CACHE_BYTES = 40 * 1024 * 1024     # all cached images together
ART_FAIL_TTL = 300                     # don't retry a failed lookup for 5 minutes
ART_LOOKUP_WORKERS = 4                 # TVmaze / iTunes rate-limit, so few lookups at once
_art_lock = threading.Lock()
_art_cache: "OrderedDict[str, tuple[bytes, str]]" = OrderedDict()
_art_state = {"bytes": 0}
_art_failed: dict[str, float] = {}
_art_inflight: dict[str, threading.Event] = {}
_art_slots = threading.BoundedSemaphore(ART_LOOKUP_WORKERS)


def release_kind(title: str, category: str = "") -> str:
    """What a release is, for the placeholder icon (mirrors the desktop app's release_kind)."""
    cat, title = str(category or "").lower(), str(title or "")
    if re.search(r"audio|music|mp3|flac|lossless", cat):
        return "music"
    if re.search(r"book|comic|magazine", cat):
        return "book"
    if re.search(r"console|game", cat):
        return "game"
    if re.search(r"\bpc\b|\bapps?\b|software", cat):
        return "app"
    if "tv" in cat or re.search(r"\b(S\d{1,2}([\s._-]?E\d{1,3})?|\d{1,2}x\d{2}|Season[\s._-]?\d+)\b", title, re.I):
        return "tv"
    if "movie" in cat or re.search(r"\b(19|20)\d{2}\b", title):
        return "movie"
    return "package"


def release_resolution(title: str) -> str:
    t = str(title or "")
    if re.search(r"\b(2160p|4k|uhd)\b", t, re.I):
        return "4K"
    m = re.search(r"\b(1080p|720p|480p)\b", t, re.I)
    return m.group(1).lower() if m else ""


def artwork_query(title: str, category: str = "") -> tuple[str, str, str]:
    """(clean name, year, "tv" / "movie") to look a poster up by (mirrors the desktop app)."""
    raw = str(title or "").replace(".", " ").replace("_", " ").strip()
    kind = release_kind(title, category)
    if kind in ("music", "book", "game", "app"):
        return "", "", ""  # the poster services only know films and shows
    tv = re.search(r"\b(S\d{1,2}E\d{1,3}|S\d{1,2}|Season\s*\d+)\b", raw, re.I)
    if tv or "tv" in str(category or "").lower():
        clean = raw[: tv.start()].strip(" -") if tv else raw
        return re.sub(r"\b(19\d\d|20\d\d)\b", "", clean).strip(" -([")[:100], "", "tv"
    year = re.search(r"\b(19\d\d|20\d\d)\b", raw)
    if year:
        return raw[: year.start()].strip(" -([")[:100], year.group(1), "movie"
    clean = re.split(r"\b(2160p|1080p|720p|480p|4k|uhd|bluray|bdrip|web-dl|webdl|webrip|hdtv|hdr|x264|x265|hevc|remux)\b",
                     raw, maxsplit=1, flags=re.I)[0].strip(" -([")
    return clean[:100], "", "movie"


def _image_type(raw: bytes) -> str:
    for magic, ctype in IMAGE_MAGIC:
        if raw.startswith(magic):
            return ctype
    return "image/webp" if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP" else ""


def _fetchable_image_url(url: str, allowed_hosts: set[str]) -> bool:
    """An image URL this server may download: http(s), and either a known indexer host or a public address."""
    p = urllib.parse.urlsplit(str(url or ""))
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    return p.hostname.lower() in allowed_hosts or host_is_public(p.hostname)


def lookup_poster_url(clean: str, year: str, hint: str) -> str:
    """Poster URL from TVmaze (shows) or the iTunes catalogue (films), like the desktop app. "" if none."""
    if not clean:
        return ""
    try:
        if hint == "tv":
            raw = http_fetch("https://api.tvmaze.com/singlesearch/shows?" + urllib.parse.urlencode({"q": clean}),
                             timeout=5, max_bytes=512 * 1024)
            image = _kdict(_kdict(json.loads(raw.decode("utf-8", errors="replace"))).get("image"))
            return str(image.get("medium") or image.get("original") or "")
        raw = http_fetch("https://itunes.apple.com/search?" + urllib.parse.urlencode(
            {"term": f"{clean} {year}".strip(), "media": "movie", "limit": 1}), timeout=5, max_bytes=512 * 1024)
        results = _kdict(json.loads(raw.decode("utf-8", errors="replace"))).get("results") or []
        art = str(_kdict(results[0]).get("artworkUrl100") or "") if results else ""
        return art.replace("100x100bb", "300x300bb")
    except (UpstreamError, ValueError):  # 404 = no such show, bad JSON, offline, rate limited...
        return ""


def release_artwork(item: dict) -> tuple[bytes, str] | None:
    """Poster bytes for a cached search result: the indexer's own cover if it sent one, else a lookup.
    Shared by every result for the same title, cached, and never retried within ART_FAIL_TTL after a miss."""
    cover = str(item.get("cover_url") or "")
    clean, year, hint = artwork_query(item.get("title", ""), item.get("category", ""))
    if not cover and not clean:
        return None
    key = cover or f"{hint}:{clean.lower()}:{year}"
    with _art_lock:
        if key in _art_cache:
            _art_cache.move_to_end(key)
            return _art_cache[key]
        if _art_failed.get(key, 0) > time.monotonic():
            return None
        waiting = _art_inflight.get(key)
        if waiting is None:
            _art_inflight[key] = threading.Event()
    if waiting is not None:  # someone else is already fetching this poster
        waiting.wait(20)
        with _art_lock:
            return _art_cache.get(key)

    def download(url: str) -> tuple[bytes, str] | None:
        try:
            raw = http_fetch(url, timeout=8, max_bytes=MAX_ART_BYTES, headers={"Accept": "image/*"})
        except UpstreamError:
            return None
        ctype = _image_type(raw)
        return (raw, ctype) if ctype else None

    result = None
    try:
        hosts = {(urllib.parse.urlsplit(s.get("indexer_url", "")).hostname or "").lower() for s in item.get("sources", [])}
        with _art_slots:
            if cover and _fetchable_image_url(cover, hosts):
                result = download(cover)
            if result is None and clean:  # no usable cover from the indexer: look the title up
                url = lookup_poster_url(clean, year, hint)
                if url and _fetchable_image_url(url, set()):
                    result = download(url)
    finally:
        with _art_lock:
            if result:
                _art_cache[key] = result
                _art_state["bytes"] += len(result[0])
                while _art_state["bytes"] > ART_CACHE_BYTES and len(_art_cache) > 1:
                    _, (old, _) = _art_cache.popitem(last=False)
                    _art_state["bytes"] -= len(old)
                _art_failed.pop(key, None)
            else:
                _art_failed[key] = time.monotonic() + ART_FAIL_TTL
                if len(_art_failed) > 5000:
                    _art_failed.clear()
            _art_inflight.pop(key).set()
    return result


def get_cached_result(result_id: str) -> dict | None:
    with _results_lock:
        entry = _results.get(result_id)
    if not entry or time.time() - entry[0] > RESULT_TTL:
        return None
    return entry[1]


# ==============================================================================
# 5. NZB DOWNLOAD (server-side, hardened)
# ==============================================================================

def host_is_public(host: str) -> bool:
    try:
        for info in socket.getaddrinfo(host, None):
            if not ipaddress.ip_address(info[4][0].split("%")[0]).is_global:
                return False
        return True
    except (OSError, ValueError):
        return False


def build_download_url(indexer: dict, nzb_url: str) -> str:
    base = urllib.parse.urlsplit(indexer["url"])
    download_url = urllib.parse.urljoin(indexer["url"].rstrip("/") + "/", str(nzb_url or "").strip())
    # Percent-encode spaces and other characters urllib refuses (an indexer link like "/get nzb/x")
    # instead of failing with an error whose text contains the full URL.
    download_url = urllib.parse.quote(download_url, safe=":/?#[]@!$&'()*+,;=%~")
    parsed = urllib.parse.urlsplit(download_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UpstreamError("Indexer returned an invalid NZB download URL.")

    if parsed.hostname.lower() == (base.hostname or "").lower():
        if base.scheme == "https" and parsed.scheme != "https":
            raise UpstreamError("Refusing a plain-HTTP NZB link from an HTTPS indexer (it would expose the API key).")
        query = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) if k.lower() != "apikey"]
        query.append(("apikey", indexer["api_key"]))
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment))

    # A different host (e.g. a signed CDN link): never attach the key, and never let an
    # indexer point this server at something on the home network.
    if not host_is_public(parsed.hostname):
        raise UpstreamError("Refusing to fetch an NZB from a private/local address that is not the indexer.")
    return download_url


def gunzip_limited(payload: bytes) -> bytes:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as gz:
            out = gz.read(MAX_NZB_BYTES + 1)  # bounded: a tiny gzip bomb cannot exhaust memory
    except (OSError, EOFError, zlib.error):
        raise UpstreamError("Indexer returned a corrupt gzip-compressed NZB.") from None
    if len(out) > MAX_NZB_BYTES:
        raise UpstreamError("Decompressed NZB exceeds the size limit.")
    return out


def validate_nzb_payload(payload: bytes, name: str) -> None:
    head = payload[:8192]
    if not re.search(rb"<nzb[\s>]", head, re.I):
        text = head.decode("utf-8", errors="replace")
        match = re.search(r'<error[^>]*description="([^"]*)"', text, re.I)
        if match:
            raise UpstreamError(f"{name}: {redact(match.group(1))}")
        if "<html" in text.lower() or "<!doctype html" in text.lower():
            raise UpstreamError(f"{name} returned a web page instead of an NZB (check the API key or download limits).")
        raise UpstreamError(f"{name} returned data that is not an NZB.")
    if not payload[-512:].rstrip().lower().endswith(b"</nzb>"):
        raise UpstreamError(f"{name} returned a truncated NZB.")


def _download_from(indexer: dict, nzb_url: str) -> bytes:
    if not indexer.get("api_key"):
        raise UpstreamError(indexer.get("key_error") or "API key is blank in the desktop configuration.")
    url = build_download_url(indexer, nzb_url)
    payload = http_fetch(
        url, timeout=NZB_DOWNLOAD_TIMEOUT, max_bytes=MAX_NZB_BYTES,
        headers={"Accept": "application/x-nzb, application/octet-stream, application/gzip, */*"},
    )
    if not payload:
        raise UpstreamError("Returned an empty NZB file.")
    if payload.startswith(b"\x1f\x8b"):
        payload = gunzip_limited(payload)
    validate_nzb_payload(payload, indexer["name"])
    return payload


def download_indexer_nzb(item: dict) -> tuple[bytes, str]:
    """Tries each indexer that carries the release until one delivers a valid NZB."""
    by_id = {ix["id"]: ix for ix in get_config()["indexers"]}
    errors = []
    for src in item.get("sources", []):
        indexer = by_id.get(src["indexer_id"])
        if not indexer:
            errors.append(f"{src['indexer_name']}: no longer enabled in the desktop configuration")
            continue
        try:
            return _download_from(indexer, src["nzb_url"]), indexer["name"]
        except UpstreamError as exc:
            errors.append(f"{indexer['name']}: {exc}")
    raise UpstreamError("; ".join(errors) or "No download source is available for this result.")


# ==============================================================================
# 5b2. JELLYFIN
# ==============================================================================
# Uses the desktop app's Media Server settings (when it is set to Jellyfin), or a server
# connected from the phone (stored in mobile_settings.json, the API key DPAPI-protected).
# A server connected from the phone must be on the home network or Tailscale, so the phone
# can't point the PC at an arbitrary internet address.

JF_CACHE_SECONDS = 600
JF_DISCOVERY_PORT = 7359
_jf_lock = threading.Lock()
_jf_cache: dict = {}


def _desktop_jellyfin() -> tuple[str, str]:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", ""
    if not isinstance(data, dict) or str(data.get("media_server_type") or "Jellyfin") != "Jellyfin":
        return "", ""
    url = str(data.get("media_server_url") or "").strip()
    try:
        key = _dpapi_unprotect(str(data.get("media_server_token") or "")).strip()
    except ConfigError:
        key = ""
    return url, key


def normalize_jellyfin_url(url: str) -> str:
    url = str(url or "").strip().rstrip("/")
    if not url:
        return ""
    if not url.lower().startswith(("http://", "https://")):
        url = "http://" + url
    parsed = urllib.parse.urlsplit(url)
    if not parsed.hostname:
        raise ValueError("That server address doesn't look right.")
    if not parsed.port and parsed.scheme == "http":
        url = url.replace(parsed.netloc, f"{parsed.netloc}:8096", 1)
    return url.split("?", 1)[0].split("#", 1)[0]


_jf_conf_cache: dict = {"stamp": None, "conf": None}
_jf_conf_lock = threading.Lock()


def _file_stamp(path: Path):
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def jellyfin_conf() -> dict:
    """{'url', 'key', 'source': 'phone'|'desktop'|''}: the phone's own connection wins.

    Remembered until either settings file changes: working it out reads two files and decrypts the
    key, and the Jellyfin tab asks for it several times per refresh (every 5 s while it's open)."""
    stamp = (_file_stamp(SETTINGS_FILE), _file_stamp(CONFIG_FILE))
    with _jf_conf_lock:
        if _jf_conf_cache["conf"] is not None and _jf_conf_cache["stamp"] == stamp:
            return dict(_jf_conf_cache["conf"])
    conf = _jellyfin_conf_uncached()
    with _jf_conf_lock:
        _jf_conf_cache.update(stamp=stamp, conf=dict(conf))
    return conf


def _jellyfin_conf_uncached() -> dict:
    global _JF_SECRETS
    s = load_settings()
    url, key, source = s.get("jellyfin_url", ""), "", ""
    if url and s.get("jellyfin_token"):
        try:
            key, source = _dpapi_unprotect(s["jellyfin_token"]).strip(), "phone"
        except ConfigError:
            url = ""
    if not (url and key):
        url, key = _desktop_jellyfin()
        source = "desktop" if url and key else ""
    try:
        url = normalize_jellyfin_url(url)
    except ValueError:
        url, source = "", ""
    if key and key not in _JF_SECRETS:
        _JF_SECRETS = frozenset([key])
    return {"url": url, "key": key if url else "", "source": source}


def jellyfin_configured() -> bool:
    c = jellyfin_conf()
    return bool(c["url"] and c["key"])


def jf_auth_header(key: str) -> str:
    """Jellyfin 10.11+ refuses the legacy X-Emby-Token header by default; this standard
    Authorization header works on every version (both are sent)."""
    return (f'MediaBrowser Client="NZB Command Center", Device="Phone server", DeviceId="nzbcc-mobile", '
            f'Version="{VERSION}", Token="{key}"')


def jf_request(path: str, params: dict | None = None, *, post: bool = False, timeout: float = 10,
               conf: dict | None = None, raw: bool = False, max_bytes: int = 20_000_000):
    c = conf or jellyfin_conf()
    if not (c["url"] and c["key"]):
        raise ConfigError("Jellyfin isn't connected yet.")
    url = c["url"] + path + ("?" + urllib.parse.urlencode(params) if params else "")
    body = http_fetch(url, timeout=timeout, max_bytes=max_bytes, data=b"" if post else None,
                      headers={"Authorization": jf_auth_header(c["key"]), "X-Emby-Token": c["key"],
                               "Accept": "application/json" if not raw else "*/*"})
    if raw:
        return body
    if not body.strip():
        return {}
    try:
        return json.loads(body)
    except ValueError:
        raise UpstreamError("Jellyfin sent an answer that isn't JSON. Is the address right?") from None


def _jf_cached(key: str, fn, ttl: float = JF_CACHE_SECONDS, force: bool = False):
    conf = jellyfin_conf()
    ck = (key, conf["url"], conf["key"][:6])
    with _jf_lock:
        hit = _jf_cache.get(ck)
        if hit and not force and time.time() - hit[0] < ttl:
            return hit[1]
    value = fn()
    with _jf_lock:
        _jf_cache[ck] = (time.time(), value)
    return value


def jf_clear_cache() -> None:
    with _jf_lock:
        _jf_cache.clear()


def jf_user_id() -> str:
    def find():
        users = jf_request("/Users", timeout=6)
        if not isinstance(users, list) or not users:
            raise UpstreamError("Jellyfin has no users.")
        admin = next((u for u in users if (u.get("Policy") or {}).get("IsAdministrator")), users[0])
        return str(admin.get("Id") or "")
    return _jf_cached("uid", find, ttl=3600)


def _ticks_s(ticks) -> int:
    return int(to_float(ticks) / 10_000_000)


def _jf_quality(item: dict) -> tuple[str, int]:
    """('1080p', size in bytes) from an item's media sources, or its Height field."""
    height, size = to_int(item.get("Height")), 0
    for src in item.get("MediaSources") or []:
        size = size or to_int(src.get("Size"))
        for st in src.get("MediaStreams") or []:
            if st.get("Type") == "Video":
                height = height or to_int(st.get("Height"))
    if height >= 1600:
        label = "2160p"
    elif height >= 900:
        label = "1080p"
    elif height >= 650:
        label = "720p"
    elif height > 0:
        label = "SD"
    else:
        label = ""
    return label, size


def _jf_item(x: dict) -> dict:
    ud = x.get("UserData") or {}
    kind = str(x.get("Type") or "")
    quality, size = _jf_quality(x)
    out = {
        "id": str(x.get("Id") or ""), "type": kind, "title": str(x.get("Name") or "Untitled"),
        "year": to_int(x.get("ProductionYear")) or None, "watched": bool(ud.get("Played")),
        "resume": _ticks_s(ud.get("PlaybackPositionTicks")), "runtime": _ticks_s(x.get("RunTimeTicks")),
        "quality": quality, "size": size, "added": str(x.get("DateCreated") or "")[:10],
        "poster": "j:" + str(x.get("Id") or ""),
    }
    if kind == "Episode":
        out.update(show=str(x.get("SeriesName") or ""), show_id=str(x.get("SeriesId") or ""),
                   season=to_int(x.get("ParentIndexNumber")), episode=to_int(x.get("IndexNumber")),
                   poster="j:" + str(x.get("SeriesId") or x.get("Id") or ""))
    return out


def jf_sessions() -> list[dict]:
    out = []
    for s in jf_request("/Sessions", timeout=6) or []:
        item = s.get("NowPlayingItem")
        if not item:
            continue
        ps = s.get("PlayState") or {}
        t = s.get("TranscodingInfo") or {}
        it = _jf_item(item)
        total = it["runtime"]
        pos = _ticks_s(ps.get("PositionTicks"))
        method = str(ps.get("PlayMethod") or ("Transcode" if t else "DirectPlay"))
        sub = []
        if item.get("Type") == "Episode":
            sub.append(f"S{it['season']:02d}E{it['episode']:02d} · {it['title']}")
            title = it["show"] or it["title"]
        else:
            title = it["title"] + (f" ({it['year']})" if it["year"] else "")
        out.append({
            "session": str(s.get("Id") or ""), "user": str(s.get("UserName") or ""),
            "device": str(s.get("DeviceName") or s.get("Client") or "Device"), "client": str(s.get("Client") or ""),
            "title": title, "sub": " · ".join(sub), "poster": it["poster"], "item_id": it["id"],
            "paused": bool(ps.get("IsPaused")), "time": pos, "total": total,
            "pct": round(pos * 100 / total, 1) if total else 0,
            "method": "Transcoding" if method.lower() == "transcode" else ("Direct stream" if method.lower() == "directstream" else "Direct play"),
            "quality": it["quality"],
        })
    return out


def jf_overview() -> dict:
    conf = jellyfin_conf()
    if not (conf["url"] and conf["key"]):
        return {"configured": False}
    out = {"configured": True, "source": conf["source"], "url": conf["url"]}
    t0 = time.monotonic()
    try:
        info = jf_request("/System/Info", timeout=6, conf=conf)
    except (UpstreamError, ConfigError) as exc:
        out.update(online=False, problem=redact(exc))  # not "error": the page treats that as a failed request
        return out
    out.update(online=True, ms=int((time.monotonic() - t0) * 1000),
               server=str(info.get("ServerName") or "Jellyfin"), version=str(info.get("Version") or ""))
    try:
        out["sessions"] = jf_sessions()
    except UpstreamError as exc:
        out["sessions"], out["sessions_error"] = [], redact(exc)
    try:
        counts = _jf_cached("counts", lambda: jf_request("/Items/Counts", timeout=8), ttl=300)
        out["counts"] = {"movies": to_int(counts.get("MovieCount")), "shows": to_int(counts.get("SeriesCount")),
                         "episodes": to_int(counts.get("EpisodeCount"))}
    except UpstreamError:
        out["counts"] = None
    try:
        out["recent"] = _jf_cached("recent", jf_recent, ttl=120)
    except UpstreamError:
        out["recent"] = []
    return out


def jf_recent(limit: int = 16) -> list[dict]:
    uid = jf_user_id()
    res = jf_request(f"/Users/{uid}/Items", {
        "Recursive": "true", "IncludeItemTypes": "Movie,Episode", "SortBy": "DateCreated", "SortOrder": "Descending",
        "Limit": limit, "Fields": "ProductionYear,DateCreated", "EnableTotalRecordCount": "false"}, timeout=10)
    return [_jf_item(x) for x in (res.get("Items") or [])]


def jf_search(query: str) -> list[dict]:
    query = query.strip()[:100]
    if not query:
        return []
    uid = jf_user_id()
    res = jf_request(f"/Users/{uid}/Items", {
        "Recursive": "true", "IncludeItemTypes": "Movie,Series", "SearchTerm": query, "Limit": 30,
        "Fields": "ProductionYear,MediaSources,DateCreated,RecursiveItemCount", "EnableTotalRecordCount": "false"}, timeout=12)
    items = []
    for x in res.get("Items") or []:
        it = _jf_item(x)
        if it["type"] == "Series":
            it["episodes"] = to_int(x.get("RecursiveItemCount"))
        items.append(it)
    return items


def _jf_episodes_all() -> list[dict]:
    uid = jf_user_id()
    out, start = [], 0
    while start < 100_000:
        res = jf_request(f"/Users/{uid}/Items", {
            "Recursive": "true", "IncludeItemTypes": "Episode", "StartIndex": start, "Limit": 5000,
            "Fields": "PremiereDate", "EnableImages": "false", "EnableUserData": "false",
            "EnableTotalRecordCount": "true"}, timeout=30)
        page = res.get("Items") or []
        out.extend(page)
        start += len(page)
        if not page or start >= to_int(res.get("TotalRecordCount"), start):
            break
    return out


def _season_missing(eps: list[dict], today: str) -> list[int]:
    """Gaps before the newest episode you have, plus aired episodes Jellyfin lists as missing."""
    have = [to_int(e.get("IndexNumber")) for e in eps if str(e.get("LocationType")) != "Virtual"]
    virtual = {to_int(e.get("IndexNumber")) for e in eps if str(e.get("LocationType")) == "Virtual"
               and str(e.get("PremiereDate") or "9999")[:10] <= today and to_int(e.get("IndexNumber")) > 0}
    return sorted(set(episode_gaps(have)) | (virtual - set(have)))


def jf_missing(force: bool = False) -> list[dict]:
    def build():
        today = date.today().isoformat()
        groups: dict[tuple, list] = {}
        names: dict[str, str] = {}
        for e in _jf_episodes_all():
            sid, season = str(e.get("SeriesId") or ""), to_int(e.get("ParentIndexNumber"))
            if not sid or season <= 0:
                continue
            names[sid] = str(e.get("SeriesName") or "Show")
            groups.setdefault((sid, season), []).append(e)
        out = []
        for (sid, season), eps in groups.items():
            missing = _season_missing(eps, today)
            if missing:
                out.append({"show": names[sid], "show_id": sid, "season": season, "missing": missing})
        out.sort(key=lambda x: (x["show"].lower(), x["season"]))
        return out
    return _jf_cached("missing", build, force=force)


def jf_upgrades(force: bool = False) -> list[dict]:
    """Movies below 1080p, worst first."""
    def build():
        uid = jf_user_id()
        res = jf_request(f"/Users/{uid}/Items", {
            "Recursive": "true", "IncludeItemTypes": "Movie", "Fields": "Height,Width,ProductionYear,DateCreated",
            "EnableImages": "false", "Limit": 20000, "EnableTotalRecordCount": "false"}, timeout=30)
        items = [_jf_item(x) for x in res.get("Items") or []]
        low = [x for x in items if x["quality"] in ("SD", "720p")]
        low.sort(key=lambda x: (x["quality"] != "SD", x["title"].lower()))
        return low
    return _jf_cached("upgrades", build, force=force)


def jf_show(show_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-fA-F-]{8,40}", show_id or ""):
        raise ValueError("Invalid show.")
    uid = jf_user_id()
    s = jf_request(f"/Users/{uid}/Items/{show_id}", timeout=8)
    res = jf_request(f"/Shows/{show_id}/Episodes", {"UserId": uid, "Fields": "PremiereDate,Overview"}, timeout=15)
    eps = res.get("Items") or []
    today = date.today().isoformat()
    seasons: dict[int, list] = {}
    for e in eps:
        seasons.setdefault(to_int(e.get("ParentIndexNumber")), []).append(e)
    out_seasons = []
    for num in sorted(seasons):
        group = seasons[num]
        owned = [e for e in group if str(e.get("LocationType")) != "Virtual"]
        out_seasons.append({
            "season": num,
            "missing": _season_missing(group, today) if num > 0 else [],
            "episodes": [{
                "episode": to_int(e.get("IndexNumber")), "title": str(e.get("Name") or ""),
                "watched": bool((e.get("UserData") or {}).get("Played")),
                "aired": str(e.get("PremiereDate") or "")[:10], "runtime": _ticks_s(e.get("RunTimeTicks")),
            } for e in sorted(owned, key=lambda e: to_int(e.get("IndexNumber")))],
        })
    nxt = None
    try:
        up = jf_request("/Shows/NextUp", {"UserId": uid, "SeriesId": show_id, "Limit": 1}, timeout=8).get("Items") or []
        if up:
            nxt = {"season": to_int(up[0].get("ParentIndexNumber")), "episode": to_int(up[0].get("IndexNumber")),
                   "title": str(up[0].get("Name") or "")}
    except UpstreamError:
        pass
    owned_all = [e for e in eps if str(e.get("LocationType")) != "Virtual"]
    return {
        "id": show_id, "title": str(s.get("Name") or "Show"), "year": to_int(s.get("ProductionYear")) or None,
        "plot": str(s.get("Overview") or "")[:1500], "genres": list(s.get("Genres") or [])[:4],
        "poster": "j:" + show_id, "episodes": len(owned_all),
        "watched": sum(1 for e in owned_all if (e.get("UserData") or {}).get("Played")),
        "seasons": out_seasons, "next": nxt,
    }


def jf_art(item_id: str) -> tuple[bytes, str]:
    if not re.fullmatch(r"[0-9a-fA-F-]{8,40}", item_id or ""):
        raise ValueError("Invalid picture.")
    raw = jf_request(f"/Items/{item_id}/Images/Primary", {"maxHeight": 450, "quality": 85}, raw=True,
                     timeout=10, max_bytes=8_000_000)
    ctype = _image_type(raw)
    if not ctype:
        raise UpstreamError("No picture.")
    return raw, ctype


def jf_control(session_id: str, action: str) -> None:
    cmd = {"playpause": "PlayPause", "stop": "Stop"}.get(action)
    if not cmd or not re.fullmatch(r"[0-9a-zA-Z-]{4,80}", session_id or ""):
        raise ValueError("Invalid playback command.")
    jf_request(f"/Sessions/{session_id}/Playing/{cmd}", post=True, timeout=6)


def jf_discover(wait: float = 1.5) -> list[dict]:
    """Jellyfin servers answering the standard LAN discovery broadcast."""
    found: dict[str, dict] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.3)
        sock.sendto(b"Who is JellyfinServer?", ("255.255.255.255", JF_DISCOVERY_PORT))
        end = time.monotonic() + wait
        while time.monotonic() < end:
            try:
                data, _addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            try:
                info = json.loads(data.decode("utf-8", errors="replace"))
                url = normalize_jellyfin_url(str(info.get("Address") or ""))
            except (ValueError, AttributeError):
                continue
            if url:
                found[url] = {"url": url, "name": str(info.get("Name") or "Jellyfin")[:60]}
    except OSError:
        pass
    finally:
        sock.close()
    return list(found.values())


def jf_url_allowed(url: str) -> bool:
    """A server connected from the phone must be private (home network, Tailscale, this PC)."""
    host = urllib.parse.urlsplit(url).hostname or ""
    return bool(host) and not host_is_public(host)


# ==============================================================================
# WATCH ON THE PHONE (home Wi-Fi only): Kodi's library, played through Jellyfin
# ==============================================================================
# The phone never talks to Jellyfin and never sees its key. It asks for a stream; the server
# makes an unguessable session and relays Jellyfin's HLS stream under /watch/<session>/...
# (an iPhone plays HLS natively). Where the phone stopped is kept in watch_positions.json and
# handed to Kodi too, so the TV can carry on from the same spot.

WATCH_FILE = BASE_DIR / "watch_positions.json"
WATCH_DEVICE = "nzbcc-phone"
WATCH_IDLE_SECONDS = 4 * 3600          # a session nobody has asked for in this long is dropped
WATCH_MATCH_SECONDS = 1800
WATCH_MAX_PLAYLIST = 5_000_000
WATCH_CHUNK = 256 * 1024
# Quality: Best plays the file as it is (H.264/HEVC copied, only the sound converted if the
# phone can't play it); the others convert the picture to H.264 at that size.
WATCH_QUALITIES = {"best": None, "1080": (1920, 20_000_000), "720": (1280, 6_000_000)}
WATCH_SID_RE = re.compile(r"[A-Za-z0-9_-]{24,48}")
WATCH_PATH_RE = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+){0,6}")
TAILSCALE_NET6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
_watch_lock = threading.Lock()
_watch_sessions: dict[str, dict] = {}
_watch_matches: dict[str, tuple[float, dict]] = {}


def client_at_home(ip_text: str) -> bool:
    """True for an address on the home network (Wi-Fi/LAN or this PC); False for Tailscale or the internet."""
    try:
        ip = ipaddress.ip_address(str(ip_text).split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip in TAILSCALE_NET or ip in TAILSCALE_NET6:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def _watch_key(kind: str, item_id: int) -> str:
    return f"{kind}:{item_id}"


def _watch_parse(body: dict) -> tuple[str, int]:
    kind, item_id = str(body.get("kind") or ""), to_int(body.get("id"))
    if kind not in ("movie", "episode") or item_id <= 0:
        raise ValueError("Invalid movie or episode.")
    return kind, item_id


def _watch_title(text: str) -> str:
    t = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()
    return re.sub(r"^(the|a|an) ", "", t).replace(" ", "")


def _file_name(path: str) -> str:
    return re.split(r"[\\/]", str(path or ""))[-1].lower()


def watch_positions() -> dict:
    try:
        data = json.loads(WATCH_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _watch_write(data: dict) -> None:
    tmp = WATCH_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, WATCH_FILE)


def watch_save_position(key: str, pos: float, dur: float) -> bool:
    """Remembers where the phone stopped. Returns True when the film counts as finished (95 %)."""
    pos, dur = max(0.0, float(pos)), max(0.0, float(dur))
    finished = dur > 0 and pos >= dur * 0.95
    with _watch_lock:
        data = watch_positions()
        if finished or pos < 30:
            data.pop(key, None)
        else:
            data[key] = {"pos": int(pos), "dur": int(dur), "t": int(time.time())}
            if len(data) > 500:   # keep the newest 500
                data = dict(sorted(data.items(), key=lambda kv: -to_int(_kdict(kv[1]).get("t")))[:500])
        _watch_write(data)
    return finished


def _kodi_item(kind: str, item_id: int) -> dict:
    if kind == "movie":
        res = kodi_call("VideoLibrary.GetMovieDetails", {"movieid": item_id, "properties": [
            "title", "year", "file", "runtime", "resume", "plot", "mpaa", "playcount"]}, timeout=8)
        d = _kdict(_kdict(res).get("moviedetails"))
    else:
        res = kodi_call("VideoLibrary.GetEpisodeDetails", {"episodeid": item_id, "properties": [
            "title", "showtitle", "season", "episode", "file", "runtime", "resume", "plot", "playcount"]}, timeout=8)
        d = _kdict(_kdict(res).get("episodedetails"))
    if not d:
        raise UpstreamError("Kodi doesn't know that one any more.")
    return d


JF_WATCH_FIELDS = "ProductionYear,MediaSources,Path,RunTimeTicks,Overview,OfficialRating"


def _jf_match(kind: str, kd: dict) -> dict | None:
    """The Jellyfin item for a Kodi movie/episode: same file name first, else title + year / season + episode."""
    uid = jf_user_id()
    fname = _file_name(kd.get("file"))
    if kind == "movie":
        title = str(kd.get("title") or "")
        items = (jf_request(f"/Users/{uid}/Items", {"searchTerm": title[:80], "IncludeItemTypes": "Movie", "Recursive": "true",
                                                    "Fields": JF_WATCH_FIELDS, "Limit": 30}, timeout=10) or {}).get("Items") or []
    else:
        show = str(kd.get("showtitle") or "")
        series = (jf_request(f"/Users/{uid}/Items", {"searchTerm": show[:80], "IncludeItemTypes": "Series", "Recursive": "true",
                                                     "Limit": 10}, timeout=10) or {}).get("Items") or []
        series.sort(key=lambda s: _watch_title(s.get("Name")) != _watch_title(show))
        items = []
        for s in series[:2]:
            eps = jf_request(f"/Shows/{s.get('Id')}/Episodes", {"UserId": uid, "Season": to_int(kd.get("season")),
                                                                "Fields": JF_WATCH_FIELDS}, timeout=10) or {}
            items += eps.get("Items") or []
    best, best_score = None, 0
    for it in items:
        score = 0
        paths = [it.get("Path")] + [src.get("Path") for src in (it.get("MediaSources") or [])]
        if fname and any(_file_name(p) == fname for p in paths if p):
            score += 100
        if kind == "movie":
            if _watch_title(it.get("Name")) == _watch_title(kd.get("title")):
                score += 50
                y1, y2 = to_int(it.get("ProductionYear")), to_int(kd.get("year"))
                score += 20 if y1 and y1 == y2 else 10 if y1 and y2 and abs(y1 - y2) == 1 else 0
        elif (to_int(it.get("IndexNumber")) == to_int(kd.get("episode"))
              and to_int(it.get("ParentIndexNumber")) == to_int(kd.get("season"))):
            score += 60
        if score > best_score:
            best, best_score = it, score
    return best if best_score >= 50 else None


def _jf_stream_label(st: dict) -> str:
    return str(st.get("DisplayTitle") or st.get("Language") or st.get("Codec") or "Track")[:60]


def watch_item(kind: str, item_id: int, ip: str = "", host: str = "") -> dict:
    """What the phone's movie page needs: Kodi's details, the Jellyfin match, tracks and where you stopped."""
    key = _watch_key(kind, item_id)
    kd = _kodi_item(kind, item_id)
    with _watch_lock:
        hit = _watch_matches.get(key)
    if hit and time.time() - hit[0] < WATCH_MATCH_SECONDS:
        jf = hit[1]
    else:
        jf = _jf_match(kind, kd) if jellyfin_configured() else None
        with _watch_lock:
            if len(_watch_matches) > 300:
                _watch_matches.clear()
            _watch_matches[key] = (time.time(), jf or {})
    runtime = to_int(kd.get("runtime")) or _ticks_s((jf or {}).get("RunTimeTicks"))
    saved = _kdict(watch_positions().get(key))
    kodi_resume = int(to_float(_kdict(kd.get("resume")).get("position")))
    start = to_int(saved.get("pos")) or kodi_resume
    out = {
        "kind": kind, "id": item_id, "title": str(kd.get("title") or "Untitled"), "year": to_int(kd.get("year")) or None,
        "show": str(kd.get("showtitle") or ""), "season": to_int(kd.get("season")), "episode": to_int(kd.get("episode")),
        "runtime": runtime, "plot": str(kd.get("plot") or (jf or {}).get("Overview") or "")[:1200],
        "mpaa": re.sub(r"^Rated\s+", "", str(kd.get("mpaa") or (jf or {}).get("OfficialRating") or "")).strip()[:12],
        "watched": to_int(kd.get("playcount")) > 0, "start": start, "on_phone": bool(saved),
        "home": client_at_home(ip), "jellyfin": jellyfin_configured(), "matched": bool(jf),
        "chips": [], "audio": [], "subs": [], "direct": False, "web": "", "poster": "",
    }
    if not jf:
        return out
    src = (jf.get("MediaSources") or [{}])[0]
    streams = src.get("MediaStreams") or []
    video = next((s for s in streams if s.get("Type") == "Video"), {})
    height, width = to_int(video.get("Height")), to_int(video.get("Width"))
    res = "4K" if height >= 1600 or width >= 3200 else "1080p" if height >= 900 or width >= 1800 else "720p" if height >= 650 else "SD" if height else ""
    hdr = str(video.get("VideoRange") or "").upper() == "HDR"
    audio = [s for s in streams if s.get("Type") == "Audio"]
    subs = [s for s in streams if s.get("Type") == "Subtitle"]
    out["chips"] = [c for c in [(res + " HDR") if hdr and res else res,
                                "Atmos" if any("atmos" in _jf_stream_label(a).lower() for a in audio) else "",
                                "Subs" if subs else ""] if c]
    out["audio"] = [{"index": to_int(a.get("Index")), "label": _jf_stream_label(a), "default": bool(a.get("IsDefault"))} for a in audio][:20]
    out["subs"] = [{"index": to_int(s.get("Index")), "label": _jf_stream_label(s) + ("" if s.get("IsTextSubtitleStream") else " (burned in)"),
                    "default": bool(s.get("IsDefault") or s.get("IsForced"))} for s in subs][:30]
    out["direct"] = str(video.get("Codec") or "").lower() in ("h264", "hevc", "h265")
    out["poster"] = "j:" + str(jf.get("Id") or "")
    c = jellyfin_conf()
    web = c["url"]
    try:   # Jellyfin set up as localhost on the PC: the phone reaches it at the PC's address instead
        u = urllib.parse.urlsplit(web)
        if u.hostname and ipaddress.ip_address(u.hostname).is_loopback and host:
            web = f"{u.scheme}://{host.split(':')[0]}:{u.port or 8096}"
    except ValueError:
        pass
    out["web"] = f"{web}/web/#/details?id={jf.get('Id')}" if web else ""
    return out


def _watch_jf_item(kind: str, item_id: int) -> tuple[dict, dict]:
    """(Jellyfin item, its first media source) for a Kodi id, from the match cache or found again."""
    key = _watch_key(kind, item_id)
    with _watch_lock:
        hit = _watch_matches.get(key)
    jf = hit[1] if hit and time.time() - hit[0] < WATCH_MATCH_SECONDS else None
    if jf is None:
        jf = _jf_match(kind, _kodi_item(kind, item_id)) or {}
        with _watch_lock:
            _watch_matches[key] = (time.time(), jf)
    if not jf:
        raise UpstreamError("I can't find this one in Jellyfin. Is Jellyfin looking at the same folders as Kodi?")
    return jf, (jf.get("MediaSources") or [{}])[0]


def _jf_delete(path: str, params: dict) -> None:
    c = jellyfin_conf()
    if not (c["url"] and c["key"]):
        return
    req = urllib.request.Request(c["url"] + path + "?" + urllib.parse.urlencode(params), method="DELETE",
                                 headers={"Authorization": jf_auth_header(c["key"]), "X-Emby-Token": c["key"], "User-Agent": USER_AGENT})
    try:
        with _OPENER.open(req, timeout=6) as resp:
            resp.read(1000)
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError):
        pass


def _watch_end(sess: dict) -> None:
    """Tells Jellyfin to stop converting for a session (frees the PC)."""
    _jf_delete("/Videos/ActiveEncodings", {"DeviceId": WATCH_DEVICE, "PlaySessionId": sess["psid"]})


def watch_start(body: dict, ip: str) -> dict:
    if not client_at_home(ip):
        raise ValueError("Watching on the phone only works on your home Wi-Fi, so it never uses mobile data.")
    kind, item_id = _watch_parse(body)
    jf, src = _watch_jf_item(kind, item_id)
    streams = src.get("MediaStreams") or []
    video = next((s for s in streams if s.get("Type") == "Video"), {})
    audio = {to_int(s.get("Index")): s for s in streams if s.get("Type") == "Audio"}
    subs = {to_int(s.get("Index")): s for s in streams if s.get("Type") == "Subtitle"}
    quality = str(body.get("quality") or "best")
    if quality not in WATCH_QUALITIES:
        raise ValueError("Unknown quality.")
    a = body.get("audio")
    a = to_int(a, -1) if a is not None and a != "" else next((i for i, s in audio.items() if s.get("IsDefault")), next(iter(audio), -1))
    if a >= 0 and a not in audio:
        raise ValueError("Unknown sound track.")
    sub = to_int(body.get("sub"), -1) if body.get("sub") not in (None, "") else -1
    if sub >= 0 and sub not in subs:
        raise ValueError("Unknown subtitles.")
    psid = secrets.token_hex(16)
    params = {"MediaSourceId": str(src.get("Id") or jf.get("Id")), "DeviceId": WATCH_DEVICE, "PlaySessionId": psid,
              "SegmentContainer": "mp4", "MinSegments": "1", "BreakOnNonKeyFrames": "True",
              "AudioCodec": "aac,ac3,eac3", "TranscodingMaxAudioChannels": "6", "AllowAudioStreamCopy": "true"}
    if a >= 0:
        params["AudioStreamIndex"] = str(a)
    burn = sub >= 0 and not subs[sub].get("IsTextSubtitleStream")
    can_copy = str(video.get("Codec") or "").lower() in ("h264", "hevc", "h265")
    if quality == "best" and can_copy and not burn:
        params.update(VideoCodec="hevc,h264", AllowVideoStreamCopy="true", MaxStreamingBitrate="200000000")
        direct = True
    else:
        width, rate = WATCH_QUALITIES.get(quality) or WATCH_QUALITIES["1080"]
        params.update(VideoCodec="h264", AllowVideoStreamCopy="false", VideoBitrate=str(rate),
                      MaxStreamingBitrate=str(rate + 640_000), MaxWidth=str(width))
        direct = False
    if sub >= 0:
        params.update(SubtitleStreamIndex=str(sub), SubtitleMethod="Encode" if burn else "Hls")
    sid = secrets.token_urlsafe(24)
    with _watch_lock:
        old = list(_watch_sessions.values())
        _watch_sessions.clear()     # one phone stream at a time
        _watch_sessions[sid] = {"jf_id": str(jf.get("Id")), "params": params, "psid": psid, "at": time.time(),
                                "key": _watch_key(kind, item_id)}
    for s in old:
        threading.Thread(target=_watch_end, args=(s,), daemon=True).start()
    log.info("watch on phone %s:%s quality=%s direct=%s ip=%s", kind, item_id, quality, direct, ip)
    return {"url": f"/watch/{sid}/master.m3u8", "sid": sid, "direct": direct, "quality": quality}


def watch_stop(sid: str) -> None:
    with _watch_lock:
        sess = _watch_sessions.pop(sid, None)
    if sess:
        _watch_end(sess)


def _kodi_set_resume(kind: str, item_id: int, pos: float, dur: float, finished: bool) -> None:
    method, idk = ("VideoLibrary.SetMovieDetails", "movieid") if kind == "movie" else ("VideoLibrary.SetEpisodeDetails", "episodeid")
    params: dict = {idk: item_id, "resume": {"position": 0 if finished else int(pos), "total": int(dur)}}
    if finished:
        params["playcount"] = 1
    kodi_call(method, params, timeout=6)


def watch_progress(body: dict) -> dict:
    kind, item_id = _watch_parse(body)
    pos, dur = to_float(body.get("pos")), to_float(body.get("dur"))
    if not (0 <= pos <= 86400 and 0 <= dur <= 86400):
        raise ValueError("Invalid position.")
    finished = watch_save_position(_watch_key(kind, item_id), pos, dur)
    kodi = False
    if body.get("final") and kodi_configured() and (pos >= 30 or finished):
        try:
            _kodi_set_resume(kind, item_id, pos, dur, finished)
            kodi = True
        except UpstreamError:
            pass    # Kodi is off; the phone's own copy is kept
    return {"ok": True, "finished": finished, "kodi": kodi}


def watch_to_tv(body: dict) -> dict:
    """Carries on on the TV: Kodi gets the phone's position, then plays from there."""
    kind, item_id = _watch_parse(body)
    pos, dur = to_float(body.get("pos")), to_float(body.get("dur"))
    if pos >= 30:
        watch_save_position(_watch_key(kind, item_id), pos, dur)
        _kodi_set_resume(kind, item_id, pos, dur, False)
    watch_stop(str(body.get("sid") or ""))
    kodi_call("Player.Open", {"item": {"movieid" if kind == "movie" else "episodeid": item_id}, "options": {"resume": pos >= 30}})
    return {"ok": True}


def _strip_jf_keys(text: str, jf_id: str, sid: str) -> str:
    """A playlist from Jellyfin, made safe for the phone: no API key, and any absolute paths sent through /watch."""
    text = re.sub(r"(?i)([?&])api_?key=[^&\s\"#]*&?", r"\1", text)
    text = re.sub(r"[?&](?=\s|$)", "", text, flags=re.M)
    return text.replace(f"/Videos/{jf_id}/", f"/watch/{sid}/").replace(f"/videos/{jf_id}/", f"/watch/{sid}/")


# ==============================================================================
# 5b3. SONARR & RADARR
# ==============================================================================
# Found automatically: each app keeps its port, URL base and API key in config.xml in its data
# folder, so nothing has to be typed in. Looked for in <drive>:\MediaAutomation\<App>\data (where
# install_arr_resume.ps1 puts them) and in the standard ProgramData\<App>.

ARR_APPS = {"radarr": "Radarr", "sonarr": "Sonarr"}
ARR_EXTRA_DIRS: list[Path] = []          # more install folders to look in (holding <App>\data)
ARR_UPCOMING_DAYS = 14
ARR_CACHE_SECONDS = 600
ARR_CONF_SECONDS = 60
ARR_ART_RE = re.compile(r"/MediaCover/\d{1,9}/(poster|fanart|banner)(-\d{2,4})?\.(jpg|jpeg|png)")
ARR_REMOTE_ART_HOSTS = {"image.tmdb.org", "artworks.thetvdb.com", "thetvdb.com", "www.thetvdb.com",
                        "assets.fanart.tv", "static.tvmaze.com"}
ARR_MONITOR = {"future": "New episodes only", "all": "Every episode", "latestSeason": "Latest season",
               "firstSeason": "First season", "missing": "Missing episodes"}
_arr_lock = threading.Lock()
_arr_conf_cache: dict = {}
_arr_cache: dict = {}
_home_arr_down_until = 0.0


def _fixed_drive_letters() -> list[str]:
    if os.name != "nt":
        return []
    try:
        k32 = ctypes.windll.kernel32
        mask = k32.GetLogicalDrives()
        return [chr(65 + i) for i in range(26) if mask & (1 << i) and k32.GetDriveTypeW(f"{chr(65 + i)}:\\") == 3]
    except Exception:
        return []


def arr_config_candidates(app: str) -> list[Path]:
    name = ARR_APPS[app]
    roots = list(ARR_EXTRA_DIRS) + [Path(f"{letter}:\\MediaAutomation") for letter in _fixed_drive_letters()]
    out = [Path(r) / name / "data" / "config.xml" for r in roots]
    if os.environ.get("ProgramData"):
        out.append(Path(os.environ["ProgramData"]) / name / "config.xml")
    return out


def _read_arr_config(path: Path) -> dict | None:
    try:
        root = safe_xml_fromstring(path.read_bytes())
    except Exception:
        return None
    get = lambda tag: (root.findtext(tag) or "").strip()
    key, port, base = get("ApiKey"), to_int(get("Port")), get("UrlBase").strip("/")
    if not re.fullmatch(r"[A-Za-z0-9]{20,64}", key) or not 0 < port < 65536:
        return None
    return {"url": f"http://127.0.0.1:{port}" + (f"/{base}" if base else ""), "key": key, "file": str(path)}


def arr_conf(app: str) -> dict:
    global _ARR_SECRETS
    if app not in ARR_APPS:
        raise ValueError("Unknown app.")
    now = time.monotonic()
    with _arr_lock:
        hit = _arr_conf_cache.get(app)
        if hit and now - hit[0] < ARR_CONF_SECONDS:
            return hit[1]
    conf = {"url": "", "key": "", "file": ""}
    for path in arr_config_candidates(app):
        try:
            found = _read_arr_config(path) if path.is_file() else None
        except OSError:
            found = None
        if found:
            conf = found
            break
    with _arr_lock:
        _arr_conf_cache[app] = (now, conf)
        _ARR_SECRETS = frozenset(c["key"] for _, c in _arr_conf_cache.values() if c["key"])
    return conf


def arr_clear_cache() -> None:
    with _arr_lock:
        _arr_conf_cache.clear()
        _arr_cache.clear()


def arr_request(app: str, path: str, params: dict | None = None, body=None, timeout: float = 10):
    c = arr_conf(app)
    if not c["key"]:
        raise ConfigError(f"{ARR_APPS[app]} wasn't found on this PC.")
    url = c["url"] + "/api/v3" + path + ("?" + urllib.parse.urlencode(params) if params else "")
    hdrs = {"X-Api-Key": c["key"], "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    try:
        raw = http_fetch(url, timeout=timeout, max_bytes=MAX_API_BYTES, data=data, headers=hdrs)
    except UpstreamError as exc:
        raise UpstreamError(f"{ARR_APPS[app]}: {exc}") from None
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        raise UpstreamError(f"{ARR_APPS[app]} sent an answer that isn't JSON.") from None


def _arr_cached(key, fn, ttl: float = ARR_CACHE_SECONDS, force: bool = False):
    with _arr_lock:
        hit = _arr_cache.get(key)
        if hit and not force and time.monotonic() - hit[0] < ttl:
            return hit[1]
    value = fn()
    with _arr_lock:
        _arr_cache[key] = (time.monotonic(), value)
    return value


def _arr_poster(app: str, images) -> str:
    """An art reference for the page: "a:<app>:<url>". A public TMDB/TVDB picture when there is one
    (smaller and no key needed), otherwise the copy the app keeps in its MediaCover folder."""
    local = ""
    for im in images or []:
        if not isinstance(im, dict) or str(im.get("coverType", "")).lower() != "poster":
            continue
        remote = str(im.get("remoteUrl") or "")
        if not remote and str(im.get("url") or "").startswith("http"):
            remote = str(im.get("url"))
        p = urllib.parse.urlsplit(remote)
        if p.scheme == "https" and (p.hostname or "").lower() in ARR_REMOTE_ART_HOSTS:
            return f"a:{app}:" + remote.replace("/t/p/original/", "/t/p/w342/")
        path = urllib.parse.urlsplit(str(im.get("url") or "")).path
        i = path.find("/MediaCover/")
        if i >= 0 and ARR_ART_RE.fullmatch(path[i:]):
            local = f"a:{app}:" + path[i:]
    return local


def arr_art(ref: str) -> tuple[bytes, str]:
    app, _, target = str(ref or "").partition(":")
    if app not in ARR_APPS:
        raise ValueError("Invalid picture.")
    if target.startswith("/"):
        if not ARR_ART_RE.fullmatch(target):
            raise ValueError("Invalid picture.")
        c = arr_conf(app)
        if not c["key"]:
            raise UpstreamError("Not found.")
        raw = http_fetch(c["url"] + target, timeout=8, max_bytes=MAX_ART_BYTES, headers={"X-Api-Key": c["key"]})
    else:
        p = urllib.parse.urlsplit(target)
        if p.scheme != "https" or (p.hostname or "").lower() not in ARR_REMOTE_ART_HOSTS:
            raise ValueError("Invalid picture.")
        raw = http_fetch(target, timeout=8, max_bytes=MAX_ART_BYTES)
    ctype = _image_type(raw)
    if not ctype:
        raise UpstreamError("Not a picture.")
    return raw, ctype


def arr_app_status(app: str) -> dict:
    c = arr_conf(app)
    out = {"app": app, "name": ARR_APPS[app], "configured": bool(c["key"]), "online": False}
    if not c["key"]:
        return out
    out["url"] = c["url"].replace("127.0.0.1", "this PC")
    try:
        st = arr_request(app, "/system/status", timeout=5)
        out.update(online=True, version=str(st.get("version") or ""))
    except UpstreamError as exc:
        out["problem"] = str(exc)
        return out
    try:
        out["queue"] = to_int(arr_request(app, "/queue/status", timeout=5).get("totalCount"))
    except UpstreamError:
        pass
    return out


_arr_counts_cache: dict = {"t": 0.0, "v": {}}


def arr_counts() -> dict:
    """How many shows Sonarr follows and how many movies Radarr still wants (for the TV's Downloads page).
    Cached for 10 minutes; a missing app is just left out."""
    if time.time() - _arr_counts_cache["t"] < 600:
        return _arr_counts_cache["v"]
    out: dict = {}
    if arr_conf("sonarr")["key"]:
        try:
            series = arr_request("sonarr", "/series", timeout=15) or []
            out["shows"] = sum(1 for x in series if x.get("monitored", True))
        except (UpstreamError, OSError, ValueError):
            pass
    if arr_conf("radarr")["key"]:
        try:
            movies = arr_request("radarr", "/movie", timeout=20) or []
            out["wanted"] = sum(1 for x in movies if x.get("monitored") and not x.get("hasFile") and x.get("isAvailable", True))
        except (UpstreamError, OSError, ValueError):
            pass
    _arr_counts_cache.update(t=time.time(), v=out)
    return out


def arr_overview() -> dict:
    with ThreadPoolExecutor(max_workers=2) as pool:
        apps = list(pool.map(arr_app_status, ARR_APPS))
    return {"apps": apps, "monitor": ARR_MONITOR}


def arr_configured() -> bool:
    return any(arr_conf(a)["key"] for a in ARR_APPS)


_ARR_MOVIE_DATES = (("digitalRelease", "Digital release"), ("physicalRelease", "Blu-ray release"), ("inCinemas", "In cinemas"))


def _arr_upcoming_uncached(days: int) -> list[dict]:
    start, end = date.today(), date.today() + timedelta(days=days)
    s, e = start.isoformat(), end.isoformat()
    items: list[dict] = []
    if arr_conf("sonarr")["key"]:
        try:
            for ep in arr_request("sonarr", "/calendar", {"start": s, "end": e, "includeSeries": "true", "unmonitored": "false"}, timeout=12) or []:
                series = ep.get("series") or {}
                items.append({"app": "sonarr", "kind": "episode", "title": str(series.get("title") or ep.get("seriesTitle") or ""),
                              "episode_title": str(ep.get("title") or ""), "season": to_int(ep.get("seasonNumber")),
                              "episode": to_int(ep.get("episodeNumber")), "when": str(ep.get("airDateUtc") or ep.get("airDate") or ""),
                              "has_file": bool(ep.get("hasFile")), "network": str(series.get("network") or ""),
                              "poster": _arr_poster("sonarr", series.get("images"))})
        except (UpstreamError, ConfigError) as exc:
            log.info("sonarr calendar: %s", exc)
    if arr_conf("radarr")["key"]:
        try:
            for mv in arr_request("radarr", "/calendar", {"start": s, "end": e, "unmonitored": "false"}, timeout=12) or []:
                when, label = "", ""
                for field, text in _ARR_MOVIE_DATES:
                    d = str(mv.get(field) or "")[:10]
                    if s <= d <= e:
                        when, label = d, text
                        break
                if not when:
                    continue
                items.append({"app": "radarr", "kind": "movie", "title": str(mv.get("title") or ""), "year": to_int(mv.get("year")) or "",
                              "when": when, "release": label, "has_file": bool(mv.get("hasFile")),
                              "poster": _arr_poster("radarr", mv.get("images"))})
        except (UpstreamError, ConfigError) as exc:
            log.info("radarr calendar: %s", exc)
    items.sort(key=lambda x: (x["when"], x["title"], x.get("season", 0), x.get("episode", 0)))
    return items[:200]


def arr_upcoming(days: int = ARR_UPCOMING_DAYS, force: bool = False) -> list[dict]:
    return _arr_cached(("upcoming", days), lambda: _arr_upcoming_uncached(days), force=force)


def home_upcoming(limit: int = 5) -> list[dict]:
    """The next few things coming, for Home. [] when Sonarr/Radarr aren't there or answering."""
    global _home_arr_down_until
    if time.monotonic() < _home_arr_down_until:
        return []
    try:
        if not arr_configured():
            return []
        week = (date.today() + timedelta(days=7)).isoformat()
        return [x for x in arr_upcoming() if x["when"][:10] <= week and not x["has_file"]][:limit]
    except Exception:
        _home_arr_down_until = time.monotonic() + 60
        return []


def _arr_ext_id(app: str, item: dict) -> int:
    return to_int(item.get("tmdbId" if app == "radarr" else "tvdbId"))


def arr_lookup(app: str, term: str) -> list[dict]:
    term = " ".join(str(term or "").split())[:120]
    if app not in ARR_APPS:
        raise ValueError("Unknown app.")
    if not term:
        return []
    found = arr_request(app, "/movie/lookup" if app == "radarr" else "/series/lookup", {"term": term}, timeout=25) or []
    out = []
    for x in found[:25]:
        ext = _arr_ext_id(app, x)
        if not ext or not x.get("title"):
            continue
        if app == "radarr":
            extra = [x.get("studio"), _arr_runtime(to_int(x.get("runtime")))]
        else:
            seasons = len([s for s in (x.get("seasons") or []) if to_int(s.get("seasonNumber")) > 0])
            extra = [x.get("network"), f"{seasons} season{'s' if seasons != 1 else ''}" if seasons else "",
                     "ended" if str(x.get("status")) == "ended" else ""]
        out.append({"app": app, "ext_id": ext, "title": str(x["title"]), "year": to_int(x.get("year")) or "",
                    "overview": str(x.get("overview") or "")[:320], "extra": " · ".join(str(v) for v in extra if v),
                    "in_library": to_int(x.get("id")) > 0, "has_file": bool(x.get("hasFile")),
                    "poster": _arr_poster(app, x.get("images"))})
    return out[:20]


def _arr_runtime(minutes: int) -> str:
    if not minutes:
        return ""
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def arr_options(app: str, force: bool = False) -> dict:
    if app not in ARR_APPS:
        raise ValueError("Unknown app.")

    def fetch():
        profiles = arr_request(app, "/qualityprofile", timeout=8) or []
        roots = arr_request(app, "/rootfolder", timeout=8) or []
        return {"profiles": [{"id": to_int(p.get("id")), "name": str(p.get("name") or "")} for p in profiles if p.get("id")],
                "roots": [{"path": str(r.get("path") or ""), "free": to_int(r.get("freeSpace"))} for r in roots if r.get("path")]}
    return _arr_cached(("options", app), fetch, force=force)


def arr_add(app: str, ext_id: int, profile_id: int = 0, root: str = "", search: bool = True, monitor: str = "future") -> dict:
    if app not in ARR_APPS:
        raise ValueError("Unknown app.")
    ext_id = to_int(ext_id)
    if ext_id <= 0:
        raise ValueError("Invalid item.")
    name = ARR_APPS[app]
    lookup = "/movie/lookup" if app == "radarr" else "/series/lookup"
    found = arr_request(app, lookup, {"term": f"{'tmdb' if app == 'radarr' else 'tvdb'}:{ext_id}"}, timeout=25) or []
    item = next((x for x in found if _arr_ext_id(app, x) == ext_id), None)
    if not item:
        raise UpstreamError(f"{name} couldn't find it any more. Search again and retry.")
    title = str(item.get("title") or "")
    if to_int(item.get("id")) > 0:
        return {"ok": True, "already": True, "title": title}
    opts = arr_options(app, force=True)
    if not opts["roots"]:
        raise ConfigError(f"{name} has no media folder yet. In {name} open Settings → Media Management and add a root folder.")
    if not opts["profiles"]:
        raise ConfigError(f"{name} has no quality profiles.")
    pid = profile_id if any(p["id"] == profile_id for p in opts["profiles"]) else opts["profiles"][0]["id"]
    folder = root if any(r["path"] == root for r in opts["roots"]) else opts["roots"][0]["path"]
    body = dict(item)
    body.pop("path", None)
    body.update(qualityProfileId=pid, rootFolderPath=folder, monitored=True)
    if app == "radarr":
        body["minimumAvailability"] = "released"
        body["addOptions"] = {"searchForMovie": bool(search), "monitor": "movieOnly"}
        made = arr_request(app, "/movie", body=body, timeout=30)
    else:
        monitor = monitor if monitor in ARR_MONITOR else "future"
        body["seasonFolder"] = True
        body.setdefault("languageProfileId", 1)          # Sonarr v3 wants one; v4 ignores it
        body["addOptions"] = {"monitor": monitor, "searchForMissingEpisodes": bool(search) and monitor != "future",
                              "searchForCutoffUnmetEpisodes": False}
        made = arr_request(app, "/series", body=body, timeout=30)
    with _arr_lock:
        for k in [k for k in _arr_cache if k[0] == "upcoming"]:
            _arr_cache.pop(k, None)
    log.info("%s: added %s (search=%s)", app, title, bool(search))
    return {"ok": True, "already": False, "title": str((made or {}).get("title") or title),
            "searching": bool(search) and (app == "radarr" or monitor != "future")}


# ==============================================================================
# 5c. PHONE SETTINGS, ALERTS (ntfy), SAVED SEARCHES, STATS, AUTOSTART
# ==============================================================================

SETTINGS_FILE = BASE_DIR / "mobile_settings.json"
MAX_SAVED_SEARCHES = 20
SAVED_SEEN_CAP = 600
SAVED_INTERVALS = (1, 3, 6, 12, 24)
_settings_lock = threading.RLock()


# ---- Connections (SABnzbd, indexers, Kodi): edited from the app, in the desktop app's own file ----
# Written to nzb_manager_config.json in the desktop app's format (keys encrypted for this Windows user as
# "dpapi:..."), so both apps keep working on the same settings. Keys are never sent back to the phone: it
# only learns whether one is saved. Every other field in the file is left exactly as it was.
_conn_lock = threading.Lock()
SAB_KEY_RE = re.compile(r"^[A-Za-z0-9]{8,64}$")
INDEXER_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]{8,128}$")
MAX_INDEXERS = 20


def _plain_url(value, what: str) -> str:
    url = str(value or "").strip().rstrip("/")
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "http://" + url
    p = urllib.parse.urlsplit(url)
    if not p.hostname or p.username or p.password or len(url) > 300 or any(c.isspace() for c in url):
        raise ValueError(f"The {what} address doesn't look right.")
    return url


def desktop_app_running() -> bool:
    """Whether NZB Command Center's desktop window is open (its next save would overwrite changes made here)."""
    try:
        import psutil  # optional; present when HAL runs
    except ImportError:
        return False
    try:
        for proc in psutil.process_iter(["name", "cmdline"]):
            cmd = " ".join(proc.info.get("cmdline") or [])
            if "nzb_command_center" in cmd.lower():
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _read_config_raw() -> dict:
    data = json.loads(read_text_retry(CONFIG_FILE, encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ConfigError(f"{CONFIG_FILE.name} does not contain a JSON object.")
    return data


def connections_view() -> dict:
    data = _read_config_raw()
    return {
        "sab": {"url": str(data.get("sab_url") or "http://127.0.0.1:8080"), "key_set": bool(data.get("sab_api_key"))},
        "indexers": [{"index": i, "name": str(x.get("name") or ""), "url": str(x.get("url") or ""),
                      "enabled": bool(x.get("enabled", True)), "key_set": bool(x.get("api_key"))}
                     for i, x in enumerate(data.get("indexers") or []) if isinstance(x, dict)],
        "kodi": {"url": str(data.get("kodi_url") or ""), "username": str(data.get("kodi_username") or ""),
                 "password_set": bool(data.get("kodi_password"))},
        "desktop_open": desktop_app_running(),
        "altmount": {"url": altmount_config()[0], "key_set": bool(load_settings().get("altmount_key"))},
    }


def save_connections(body: dict) -> dict:
    """Applies the app's Connections page to nzb_manager_config.json (a backup is kept as .bak)."""
    with _conn_lock:
        data = _read_config_raw()
        if "sab_url" in body:
            data["sab_url"] = _plain_url(body.get("sab_url"), "SABnzbd") or "http://127.0.0.1:8080"
        key = str(body.get("sab_api_key") or "").strip()
        if key:
            if not SAB_KEY_RE.match(key):
                raise ValueError("That SABnzbd API key doesn't look right (letters and digits, from SABnzbd's Config > General).")
            data["sab_api_key"] = _dpapi_protect(key)
        if "indexers" in body:
            items = body.get("indexers")
            if not isinstance(items, list) or len(items) > MAX_INDEXERS:
                raise ValueError("Which indexers?")
            old = [x for x in (data.get("indexers") or []) if isinstance(x, dict)]
            new = []
            for it in items:
                if not isinstance(it, dict):
                    raise ValueError("Which indexers?")
                idx = it.get("index")
                base = dict(old[idx]) if isinstance(idx, int) and 0 <= idx < len(old) else {}
                name = str(it.get("name") or "").strip()[:60]
                url = _plain_url(it.get("url"), name or "indexer")
                if not name or not url:
                    raise ValueError("Each indexer needs a name and an address.")
                k = str(it.get("api_key") or "").strip()
                if k and not INDEXER_KEY_RE.match(k):
                    raise ValueError(f"The API key for {name} doesn't look right.")
                base.update({"name": name, "url": url, "enabled": bool(it.get("enabled", True))})
                if k:
                    base["api_key"] = _dpapi_protect(k)
                base.setdefault("api_key", "")
                new.append(base)
            data["indexers"] = new
        if "kodi_url" in body:
            data["kodi_url"] = _plain_url(body.get("kodi_url"), "Kodi")
        if "kodi_username" in body:
            data["kodi_username"] = str(body.get("kodi_username") or "").strip()[:60]
        pw = str(body.get("kodi_password") or "")
        if pw:
            if len(pw) > 128 or any(ord(c) < 32 for c in pw):
                raise ValueError("That Kodi password doesn't look right.")
            data["kodi_password"] = _dpapi_protect(pw)
        elif body.get("clear_kodi_password"):
            data["kodi_password"] = ""
        if "altmount_url" in body or str(body.get("altmount_key") or "").strip():
            am_url = _plain_url(body.get("altmount_url"), "AltMount") if "altmount_url" in body else None
            am_key = str(body.get("altmount_key") or "").strip()
            if am_key and not re.fullmatch(r"[A-Za-z0-9_\-]{8,128}", am_key):
                raise ValueError("That AltMount API key doesn't look right (AltMount › Configuration › System).")

            def _am(st):
                if am_url is not None:
                    st["altmount_url"] = am_url
                if am_key:
                    st["altmount_key"] = _dpapi_protect(am_key)
            update_settings(_am)
        bak = CONFIG_FILE.with_name(CONFIG_FILE.name + ".bak")
        tmp = CONFIG_FILE.with_suffix(".tmp")
        _retry_busy(lambda: bak.write_bytes(CONFIG_FILE.read_bytes()))
        tmp.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
        _retry_busy(lambda: os.replace(tmp, CONFIG_FILE))
    log.info("Connections changed from the app (SABnzbd, %d indexer(s), Kodi).", len(data.get("indexers") or []))
    try:
        get_config()                                   # pick the change up now (and say so if it's broken)
    except ConfigError as exc:
        return {"ok": True, "warning": str(exc)}
    return {"ok": True}


def test_connection(body: dict) -> dict:
    """Tries SABnzbd, an indexer or Kodi with what's typed (or what's saved)."""
    what = str(body.get("what") or "")
    data = _read_config_raw()
    try:
        if what == "sab":
            url = _plain_url(body.get("url") or data.get("sab_url"), "SABnzbd")
            key = str(body.get("key") or "").strip() or _dpapi_unprotect(str(data.get("sab_api_key") or ""))
            raw = http_fetch(f"{url}/api?mode=queue&limit=1&output=json&apikey={urllib.parse.quote(key)}", timeout=8, max_bytes=MAX_SAB_BYTES)
            d = json.loads(raw.decode("utf-8", "replace"))
            if isinstance(d, dict) and d.get("error"):
                return {"ok": False, "message": f"SABnzbd answered, but: {redact(d['error'])}"}
            return {"ok": True, "message": "SABnzbd is answering and accepts the key."}
        if what == "indexer":
            idx = body.get("index")
            old = [x for x in (data.get("indexers") or []) if isinstance(x, dict)]
            saved = old[idx] if isinstance(idx, int) and 0 <= idx < len(old) else {}
            url = _plain_url(body.get("url") or saved.get("url"), "indexer")
            key = str(body.get("key") or "").strip() or _dpapi_unprotect(str(saved.get("api_key") or ""))
            api = url if url.endswith(("/api", "/api.php")) else url + "/api"
            raw = http_fetch(f"{api}?t=caps&apikey={urllib.parse.quote(key)}", timeout=12, max_bytes=MAX_API_BYTES)
            text = raw.decode("utf-8", "replace")
            m = re.search(r'<error[^>]*description="([^"]*)"', text)
            if m:
                return {"ok": False, "message": f"The indexer answered, but: {redact(m.group(1))}"}
            if "<caps" not in text:
                return {"ok": False, "message": "That address answered, but not like a Newznab indexer."}
            return {"ok": True, "message": "The indexer is answering and accepts the key."}
        if what == "kodi":
            url = normalize_kodi_url(str(body.get("url") or data.get("kodi_url") or ""))
            if not url:
                return {"ok": False, "message": "Enter Kodi's address first, e.g. http://192.168.68.62:8080."}
            user = str(body.get("username") if body.get("username") is not None else data.get("kodi_username") or "")
            pw = str(body.get("password") or "") or _dpapi_unprotect(str(data.get("kodi_password") or ""))
            hdrs = {"Content-Type": "application/json"}
            if user or pw:
                hdrs["Authorization"] = "Basic " + base64.b64encode(f"{user}:{pw}".encode("utf-8")).decode("ascii")
            raw = http_fetch(url.rstrip("/") + "/jsonrpc", timeout=6, max_bytes=64_000, headers=hdrs,
                             data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "JSONRPC.Ping"}).encode())
            ok = '"pong"' in raw.decode("utf-8", "replace")
            return {"ok": ok, "message": "Kodi is answering." if ok else "That address answered, but not like Kodi."}
        if what == "altmount":
            saved_url, saved_key = altmount_config()
            url = _plain_url(body.get("url"), "AltMount") if body.get("url") else saved_url
            return altmount_test(url, str(body.get("key") or "").strip() or saved_key)
    except UpstreamError as exc:
        return {"ok": False, "message": str(exc)}
    except ConfigError as exc:
        return {"ok": False, "message": str(exc)}
    raise ValueError("Test what?")


def _default_settings() -> dict:
    return {"ntfy_url": "", "ntfy_token": "", "notify_complete": True, "notify_failed": True, "notify_saved": True,
            "saved_interval_hours": 6, "saved_searches": [],
            "jellyfin_url": "", "jellyfin_token": "",
            "notify_drive_health": True, "notify_drive_hot": True, "notify_drive_low": True, "drive_low_gb": DRIVE_LOW_GB,
            # Sony TV power / app launch (TV on + Kodi). Blank tv_host = the host from the Kodi URL.
            "tv_host": "", "tv_psk": "", "tv_mac": "", "tv_mac_manual": False, "tv_kodi_uri": "",
            # Usenet streaming through AltMount (blank address: no Stream buttons). The key is DPAPI-encrypted.
            "altmount_url": "", "altmount_key": ""}


def _retry_busy(fn, tries: int = 20):
    """Windows refuses to read or replace a file that another program (an antivirus scan, a backup)
    has open at that moment ("sharing violation", PermissionError). That's brief: try again."""
    for attempt in range(tries):
        try:
            return fn()
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(0.05)


def read_text_retry(path: Path, encoding: str = "utf-8") -> str:
    return _retry_busy(lambda: path.read_text(encoding=encoding))


def replace_retry(src: Path, dst: Path) -> None:
    _retry_busy(lambda: os.replace(src, dst))


def load_settings() -> dict:
    """Phone-only settings (never the desktop configuration). Missing/damaged file -> defaults.

    Read under the same lock as saving, so a save can never replace the file mid-read. A file that
    exists but can't be read raises instead of returning defaults: otherwise the next save would
    write defaults over the user's real settings."""
    with _settings_lock:
        return _load_settings_locked()


def _load_settings_locked() -> dict:
    settings = _default_settings()
    try:
        data = json.loads(read_text_retry(SETTINGS_FILE))
    except FileNotFoundError:
        return settings
    except ValueError as exc:  # damaged JSON: keep a copy aside, start from defaults
        try:
            import shutil
            shutil.copy2(SETTINGS_FILE, SETTINGS_FILE.with_name(SETTINGS_FILE.stem + ".damaged.json"))
        except OSError:
            pass
        log.warning("%s could not be read (%s); using defaults (a copy was kept as %s).",
                    SETTINGS_FILE.name, exc, SETTINGS_FILE.stem + ".damaged.json")
        return settings
    if isinstance(data, dict):
        defaults = _default_settings()
        for key, default in defaults.items():
            if isinstance(data.get(key), type(default)):
                settings[key] = data[key]
        # Keep settings this version doesn't know (e.g. written by a newer or another version), so
        # saving here doesn't wipe them - that is how the TV's key was lost once.
        for key, value in data.items():
            if key not in defaults:
                settings[key] = value
    settings["saved_searches"] = [x for x in settings["saved_searches"] if isinstance(x, dict) and x.get("id")]
    if settings["drive_low_gb"] == 100:  # the old default, saved by 3.6-3.8: follow the new one
        settings["drive_low_gb"] = DRIVE_LOW_GB
    return settings


def save_settings(settings: dict) -> None:
    """Writes only what differs from the defaults (plus anything this version doesn't know), so a
    later change to a default reaches everyone who never changed that setting."""
    defaults = _default_settings()
    changed = {k: v for k, v in settings.items() if k not in defaults or v != defaults[k]}
    with _settings_lock:
        tmp = SETTINGS_FILE.with_name(SETTINGS_FILE.name + ".tmp")
        tmp.write_text(json.dumps(changed, indent=1, ensure_ascii=False), encoding="utf-8")
        replace_retry(tmp, SETTINGS_FILE)


def update_settings(fn):
    """Read-modify-write of the settings file under a lock; returns fn's result."""
    with _settings_lock:
        settings = load_settings()
        result = fn(settings)
        save_settings(settings)
        return result


def _dpapi_protect(text: str) -> str:
    """Encrypts a phone-only secret for this Windows user (stored as-is elsewhere)."""
    if not text or os.name != "nt":
        return text

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    crypt32.CryptProtectData.restype = ctypes.wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    raw = text.encode("utf-8")
    in_buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    in_blob, out_blob = DATA_BLOB(len(raw), in_buf), DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        return text
    try:
        return "dpapi:" + base64.b64encode(ctypes.string_at(out_blob.pbData, out_blob.cbData)).decode("ascii")
    finally:
        kernel32.LocalFree(out_blob.pbData)


def ntfy_token(settings: dict) -> str:
    global _EXTRA_SECRETS
    try:
        token = _dpapi_unprotect(str(settings.get("ntfy_token") or ""))
    except ConfigError:
        return ""
    if token and token not in _EXTRA_SECRETS:
        _EXTRA_SECRETS = _EXTRA_SECRETS | {token}
    return token


def validate_ntfy_url(url: str) -> str:
    url = str(url or "").strip()
    if not url:
        return ""
    if len(url) > 300:
        raise ValueError("That ntfy URL is too long.")
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("Use a full ntfy topic URL, e.g. https://ntfy.sh/my-secret-topic.")
    if p.query or p.fragment or p.username or p.password:
        raise ValueError("Leave out ?query, #fragment and user:password — put a token in the token field instead.")
    topic = p.path.rstrip("/").rpartition("/")[2]
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", topic):
        raise ValueError("The URL must end in a topic name (letters, digits, - and _).")
    if p.scheme == "http" and host_is_public(p.hostname):
        raise ValueError("Use https:// for an ntfy server on the internet.")
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path.rstrip("/"), "", ""))


def send_notification(title: str, message: str, tags: tuple = (), priority: int = 3, settings: dict | None = None) -> bool:
    """Publishes to the configured ntfy topic (JSON publishing, so any language works). False if not set up."""
    settings = settings or load_settings()
    url = settings.get("ntfy_url") or ""
    if not url:
        return False
    p = urllib.parse.urlsplit(url)
    base_path, _, topic = p.path.rstrip("/").rpartition("/")
    endpoint = urllib.parse.urlunsplit((p.scheme, p.netloc, base_path + "/", "", ""))
    body = json.dumps({"topic": topic, "title": title[:120], "message": message[:1500],
                       "tags": list(tags), "priority": priority}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token = ntfy_token(settings)
    if token:
        headers["Authorization"] = "Bearer " + token
    http_fetch(endpoint, timeout=10, max_bytes=64_000, data=body, headers=headers)
    return True


def _title_list(names: list[str], limit: int = 5) -> str:
    text = "\n".join(names[:limit])
    return text + (f"\n…and {len(names) - limit} more" if len(names) > limit else "")


def due_saved_search(settings: dict, now: float) -> dict | None:
    hours = to_int(settings.get("saved_interval_hours"), 6)
    interval = (hours if hours in SAVED_INTERVALS else 6) * 3600
    due = [x for x in settings["saved_searches"] if now - to_float(x.get("last_run")) >= interval]
    return min(due, key=lambda x: to_float(x.get("last_run")), default=None)


def run_saved_search(search_id: str, notify: bool = True) -> dict:
    """Runs one saved search. The first successful run only records what exists; later runs report new releases."""
    settings = load_settings()
    entry = next((x for x in settings["saved_searches"] if x.get("id") == search_id), None)
    if not entry:
        raise ValueError("That saved search no longer exists.")
    try:
        merged, errors, _ = run_search(entry.get("query", ""), entry.get("indexer") or "*", entry.get("category") or "")
        error = "; ".join(errors)[:300] if errors and not merged else ""
    except (ValueError, UpstreamError, ConfigError) as exc:
        merged, error = [], redact(exc)[:300]
    now = time.time()

    def apply(st: dict):
        e = next((x for x in st["saved_searches"] if x.get("id") == search_id), None)
        if e is None:
            return None
        seen = [str(k) for k in (e.get("seen") or [])]
        seen_set = set(seen)
        fresh, keys = [], set()
        for item in merged:
            key = result_key(item["title"])
            if key and key not in seen_set and key not in keys:
                keys.add(key)
                fresh.append(item)
        baseline = not e.get("baselined")
        e["seen"] = (seen + [result_key(x["title"]) for x in fresh])[-SAVED_SEEN_CAP:]
        if fresh and not baseline:
            e["new"] = ([{"title": x["title"], "size": format_bytes(x["size_bytes"]),
                          "indexer": x["sources"][0]["indexer_name"], "found": int(now)} for x in fresh]
                        + list(e.get("new") or []))[:25]
        if not error:
            e["baselined"] = True
        e.update(last_run=now, last_error=error, last_count=len(merged))
        return [] if baseline else fresh

    fresh = update_settings(apply)
    if fresh is None:
        raise ValueError("That saved search no longer exists.")
    if fresh and notify and settings.get("notify_saved"):
        try:
            send_notification(f"New for “{entry.get('query')}”", _title_list([x["title"] for x in fresh]), tags=("mag",), settings=settings)
        except UpstreamError as exc:
            log.warning("Saved-search alert failed: %s", redact(exc))
    log.info("saved search %r: %d result(s), %d new%s", entry.get("query"), len(merged), len(fresh), f", error: {error}" if error else "")
    return {"new": len(fresh), "error": error}


# ---- TV home row: the newest movies, for the TV launcher ------------------------------
# The TV's home screen shows the newest movies with their posters and starts one in Kodi in
# one press, even when Kodi was not running. Kodi only answers while it runs, so this server
# keeps the row: Kodi's recently added movies (whenever Kodi is reachable, remembered for when
# it isn't) merged with finished SABnzbd movie downloads Kodi hasn't scanned yet. Posters are
# saved on disk next to this file, so the row survives restarts of this server and of Kodi.
TV_ROW_SIZE = 12
TV_REFRESH_SECS = 300
TV_PLAY_WAIT_SECS = 45              # how long /api/tv/play waits for a Kodi that was just started
TV_STATE_FILE = BASE_DIR / "tv_recent.json"
TV_ART_DIR = BASE_DIR / "tv_art"
TV_ID_RE = re.compile(r"[ksme][A-Za-z0-9_]{1,80}")
# "Recently watched" (GET /api/tv/watched): Kodi's movies and episodes started or finished, the last
# played first, with how far in each is. Kept on disk like the downloads row, so the TV still has it
# while Kodi is closed. Only items with a poster are listed; ids are m<movieid> and e<episodeid>.
TV_WATCHED_SIZE = 10
TV_WATCHED_REFRESH_SECS = 120
TV_WATCHED_FILE = BASE_DIR / "tv_watched.json"
TV_WATCHED_ART_DIR = BASE_DIR / "tv_watched_art"
TV_WATCHED_ID_RE = re.compile(r"[me][0-9]{1,12}")
VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".mpg", ".mpeg", ".webm", ".iso"}
TV_KODI_PROPS = ["title", "year", "runtime", "file", "art", "thumbnail", "dateadded", "playcount", "streamdetails"]
_tv_files_lock = threading.RLock()  # the TV row / watched files, reads and writes (not the TV power-on job: _tv_lock)
_tv_refreshing = threading.Lock()


def tv_load_state() -> dict:
    try:
        with _tv_files_lock:
            data = json.loads(read_text_retry(TV_STATE_FILE))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    return {"updated": to_float(data.get("updated")), "kodi": [x for x in data.get("kodi") or [] if isinstance(x, dict)],
            "items": [x for x in data.get("items") or [] if isinstance(x, dict)],
            "path_map": [p for p in data.get("path_map") or [] if isinstance(p, list) and len(p) == 2]}


def tv_save_state(state: dict) -> None:
    with _tv_files_lock:
        tmp = TV_STATE_FILE.with_name(TV_STATE_FILE.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
        replace_retry(tmp, TV_STATE_FILE)


def _split_path(path: str) -> list[str]:
    return [p for p in re.split(r"[\\/]+", str(path or "")) if p]


def tv_learn_prefix(local_folder: str, kodi_file: str) -> tuple[str, str] | None:
    """('D:\\Movies', 'smb://HAL9000/Movies') from a download folder and the file Kodi plays from it.

    Both paths contain the download's folder ("Movie (2020)"); what comes before it is how Kodi
    reaches this PC's folder. None when Kodi's path doesn't contain it."""
    m = re.match(r"^([a-z][a-z0-9+.-]*://[^/]*)(/.*)?$", kodi_file or "", re.I)
    head, rest = (m.group(1), m.group(2) or "") if m else ("", kodi_file or "")
    k_parts = _split_path(rest)[:-1]          # Kodi's folders, without the file name
    l_parts = _split_path(local_folder)
    if len(l_parts) < 2:
        return None
    # The download's own folder ("Movie (2020)") is the distinctive part; the video can sit in a
    # subfolder of it on Kodi's side. Everything before that folder is how each side reaches it.
    job = l_parts[-1].lower()
    at = max((i for i, part in enumerate(k_parts) if part.lower() == job), default=-1)
    if at < 0:
        return None
    common = 1
    k_parts = k_parts[: at + 1]
    local_prefix = "\\".join(l_parts[: len(l_parts) - common]) if "\\" in local_folder or ":" in l_parts[0] else \
        "/" + "/".join(l_parts[: len(l_parts) - common])
    kodi_prefix = head + "/" + "/".join(k_parts[: len(k_parts) - common]) if head else \
        "/".join(k_parts[: len(k_parts) - common])
    return local_prefix, kodi_prefix.rstrip("/")


def tv_map_path(local_path: str, path_map: list) -> str:
    """The path Kodi plays a local file by, using the learned prefixes ("" when none fits)."""
    norm = str(local_path or "").replace("/", "\\").lower()
    for local_prefix, kodi_prefix in sorted(path_map, key=lambda p: -len(p[0])):
        lp = str(local_prefix).replace("/", "\\").rstrip("\\").lower()
        if norm == lp or norm.startswith(lp + "\\"):
            tail = str(local_path)[len(lp):].replace("\\", "/").lstrip("/")
            sep = "/" if "://" in kodi_prefix or kodi_prefix.startswith("/") else "\\"
            return kodi_prefix.rstrip("/\\") + sep + (tail if sep == "/" else tail.replace("/", "\\"))
    return ""


def tv_video_file(storage: str) -> str:
    """The main video file of a finished download (the largest video in its folder), or ""."""
    p = Path(str(storage or ""))
    try:
        if p.is_file():
            return str(p) if p.suffix.lower() in VIDEO_EXTS else ""
        best, size = "", 0
        for root, dirs, files in os.walk(p):
            if root[len(str(p)):].count(os.sep) >= 2:
                dirs[:] = []
            for f in files:
                if Path(f).suffix.lower() in VIDEO_EXTS and "sample" not in f.lower():
                    s = (Path(root) / f).stat().st_size
                    if s > size:
                        best, size = str(Path(root) / f), s
        return best
    except OSError:
        return ""


def _kodi_time(text: str) -> float:
    try:
        return time.mktime(time.strptime(str(text or "")[:19], "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return 0.0


def tv_kodi_recent(limit: int = TV_ROW_SIZE) -> list[dict]:
    res = _kdict(kodi_call("VideoLibrary.GetRecentlyAddedMovies",
                           {"properties": TV_KODI_PROPS, "limits": {"start": 0, "end": limit}}, timeout=10))
    out = []
    for mv in res.get("movies") or []:
        if not isinstance(mv, dict) or to_int(mv.get("movieid")) <= 0:
            continue
        art = _kdict(mv.get("art"))
        quality, _hdr = video_quality(mv.get("streamdetails"))
        out.append({"id": f"k{to_int(mv.get('movieid'))}", "movieid": to_int(mv.get("movieid")),
                    "title": str(mv.get("title") or "Untitled"), "year": to_int(mv.get("year")) or None,
                    "runtime": to_int(mv.get("runtime")) // 60, "quality": "4K" if quality == "2160p" else quality,
                    "added": _kodi_time(mv.get("dateadded")), "watched": to_int(mv.get("playcount")) > 0,
                    "file": str(mv.get("file") or ""), "art_ref": _art_ref(art.get("poster") or mv.get("thumbnail"))})
    return out


def _same_movie(a: dict, b: dict) -> bool:
    if _norm_title(a.get("title")) != _norm_title(b.get("title")):
        return False
    ya, yb = to_int(a.get("year")), to_int(b.get("year"))
    return not ya or not yb or abs(ya - yb) <= 1


def tv_sab_movies(limit: int = 60) -> list[dict]:
    slots = sab_request("history", {"limit": limit}).get("history", {}).get("slots", [])
    out = []
    for x in slots:
        name, cat = str(x.get("name") or ""), str(x.get("category") or "")
        if str(x.get("status", "")).lower() != "completed" or release_kind(name, cat) != "movie":
            continue
        clean, year, _hint = artwork_query(name, cat)
        nzo = re.sub(r"[^A-Za-z0-9_]", "", str(x.get("nzo_id") or ""))[:80]
        if not clean or not nzo:
            continue
        out.append({"id": f"s{nzo}", "title": clean, "year": to_int(year) or None, "added": float(to_int(x.get("completed"))),
                    "quality": release_resolution(name), "storage": str(x.get("storage") or ""), "watched": False})
    return out


def _tv_save_poster(item: dict) -> bool:
    TV_ART_DIR.mkdir(exist_ok=True)
    dest = TV_ART_DIR / f"{item['id']}.img"
    if dest.exists():
        return True
    raw = None
    try:
        if item.get("art_ref"):
            raw = kodi_art(item["art_ref"])[0]
        else:
            url = lookup_poster_url(item["title"], str(item.get("year") or ""), "movie")
            if url and _fetchable_image_url(url, set()):
                raw = http_fetch(url, timeout=8, max_bytes=MAX_ART_BYTES, headers={"Accept": "image/*"})
    except (UpstreamError, ValueError, ConfigError):
        raw = None
    if not raw or not _image_type(raw):
        return False
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(raw)
    replace_retry(tmp, dest)
    return True


def tv_refresh() -> dict:
    """Rebuilds the row. Never raises for an offline Kodi or SABnzbd: the last good part is kept."""
    with _tv_refreshing:
        state = tv_load_state()
        kodi_items, kodi_ok = state["kodi"], False
        try:
            if kodi_configured():
                kodi_items, kodi_ok = tv_kodi_recent(), True
        except (UpstreamError, ConfigError):
            pass
        try:
            sab_items = tv_sab_movies()
        except (UpstreamError, ConfigError):
            sab_items = []
        path_map = [tuple(p) for p in state["path_map"]]
        for s in sab_items:          # learn how Kodi reaches this PC's folders from downloads it already has
            k = next((k for k in kodi_items if _same_movie(s, k) and k.get("file")), None)
            pair = tv_learn_prefix(s["storage"], k["file"]) if k and s["storage"] else None
            if pair and pair not in path_map:
                path_map.append(pair)
        merged = list(kodi_items)
        for s in sab_items:
            if any(_same_movie(s, k) for k in kodi_items):
                continue
            video = tv_video_file(s["storage"])
            s["file"] = tv_map_path(video, path_map) if video else ""
            merged.append(s)
        merged.sort(key=lambda x: -to_float(x.get("added")))
        items = merged[:TV_ROW_SIZE]
        newest_kodi = max((to_float(k.get("added")) for k in kodi_items), default=0.0)
        for it in items:
            it.pop("storage", None)
            it["poster"] = _tv_save_poster(it)
            it["in_kodi"] = bool(it.get("movieid"))
            it["new"] = not it.get("watched") and to_float(it.get("added")) >= time.time() - 3 * 86400
        if TV_ART_DIR.exists():   # forget posters the row no longer shows
            keep = {f"{it['id']}.img" for it in items} | {f"{k['id']}.img" for k in kodi_items}
            for f in TV_ART_DIR.glob("*.img"):
                if f.name not in keep:
                    f.unlink(missing_ok=True)
        state = {"updated": time.time(), "kodi_online": kodi_ok, "newest_kodi": newest_kodi,
                 "kodi": kodi_items, "items": items, "path_map": [list(p) for p in path_map][-20:]}
        with _tv_files_lock:
            tv_save_state(state)
        return state


def tv_row(background: bool = True) -> dict:
    """The row as the TV reads it; a stale row is returned at once and rebuilt on a worker thread."""
    state = tv_load_state()
    if time.time() - state["updated"] > TV_REFRESH_SECS:
        if background and state["items"]:
            if not _tv_refreshing.locked():
                threading.Thread(target=tv_refresh, name="tv-row", daemon=True).start()
        else:
            state = tv_refresh()
    items = [{k: it.get(k) for k in ("id", "title", "year", "runtime", "quality", "added", "watched", "new",
                                      "in_kodi", "movieid", "file")}
             | {"poster": f"/api/tv/poster?id={it['id']}" if it.get("poster") else ""} for it in state["items"]]
    return {"updated": state["updated"], "items": items}


def tv_poster(item_id: str) -> bytes | None:
    if not TV_ID_RE.fullmatch(item_id or ""):
        raise ValueError("Invalid item.")
    folder = TV_WATCHED_ART_DIR if TV_WATCHED_ID_RE.fullmatch(item_id) else TV_ART_DIR
    try:
        return (folder / f"{item_id}.img").read_bytes()
    except OSError:
        return None


# How the TV copes with the HAL-style home (POST /api/tv/perf, once a minute from the TV app): free memory, the
# app's memory and CPU, and how smoothly Home draws. The last day is kept (tv_perf.json) and summed up by
# GET /api/tv/perf, so the animations can be checked against what the TV can take.
TV_PERF_FILE = BASE_DIR / "tv_perf.json"
TV_PERF_KEEP = 1440
_tv_perf: list[dict] = []
_tv_perf_lock = threading.Lock()
_tv_perf_logged = 0.0


def tv_perf_add(body: dict, now: float | None = None) -> dict:
    """Stores one reading from the TV (numbers only, clamped) and logs a summary every 10 minutes."""
    global _tv_perf_logged
    now = time.time() if now is None else now
    def num(k, hi):
        v = to_float(body.get(k))
        return max(0.0, min(hi, v))
    r = {"t": now, "avail_mb": num("avail_mb", 1e6), "total_mb": num("total_mb", 1e6), "low_memory": bool(body.get("low_memory")),
         "app_mb": num("app_mb", 1e5), "cpu": round(to_float(body.get("cpu")), 1) if to_float(body.get("cpu")) >= 0 else None,
         "cores": int(num("cores", 64)), "frames": int(num("frames", 1e6)), "slow_frames": int(num("slow_frames", 1e6)),
         "freezes": int(num("freezes", 1e6)), "home_s": int(num("home_s", 3600)),
         "screen": "home" if body.get("screen") == "home" else "away", "version": str(body.get("version") or "")[:40]}
    with _tv_perf_lock:
        if not _tv_perf:
            try:
                old = json.loads(read_text_retry(TV_PERF_FILE))
                _tv_perf.extend(x for x in old if isinstance(x, dict) and now - to_float(x.get("t")) < 86400)
            except (OSError, ValueError):
                pass
        _tv_perf.append(r)
        del _tv_perf[:-TV_PERF_KEEP]
        try:
            tmp = TV_PERF_FILE.with_name(TV_PERF_FILE.name + ".tmp")
            tmp.write_text(json.dumps(_tv_perf), encoding="utf-8")
            replace_retry(tmp, TV_PERF_FILE)
        except OSError:
            pass
    if now - _tv_perf_logged >= 600:
        _tv_perf_logged = now
        s = tv_perf_summary(3600, now)
        log.info("TV: %s MB free of %s, app %s MB, CPU %s%% (peak %s%%), smooth %s%% of %s frames, %s freezes (last hour)",
                 s["avail_mb_min"], s["total_mb"], s["app_mb_max"], s["cpu_avg"], s["cpu_max"], s["smooth_pct"], s["frames"], s["freezes"])
    return r


def tv_perf_summary(window: float = 3600, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    with _tv_perf_lock:
        rs = [r for r in _tv_perf if now - r["t"] <= window]
    cpus = [r["cpu"] for r in rs if r.get("cpu") is not None]
    frames = sum(r["frames"] for r in rs)
    slow = sum(r["slow_frames"] for r in rs)
    return {"readings": len(rs), "total_mb": rs[-1]["total_mb"] if rs else None,
            "avail_mb_min": min((r["avail_mb"] for r in rs), default=None),
            "avail_mb_now": rs[-1]["avail_mb"] if rs else None,
            "app_mb_max": max((r["app_mb"] for r in rs), default=None),
            "cpu_avg": round(sum(cpus) / len(cpus), 1) if cpus else None, "cpu_max": max(cpus, default=None),
            "frames": frames, "slow_frames": slow, "freezes": sum(r["freezes"] for r in rs),
            "smooth_pct": round(100.0 * (frames - min(slow, frames)) / frames, 1) if frames else None,
            "low_memory": any(r["low_memory"] for r in rs)}


def tv_perf_report() -> dict:
    with _tv_perf_lock:
        latest = dict(_tv_perf[-1]) if _tv_perf else None
        history = [dict(r) for r in _tv_perf[-120:]]
    return {"latest": latest, "last_hour": tv_perf_summary(3600), "last_day": tv_perf_summary(86400), "history": history}


def tv_backdrop(item_id: str) -> bytes | None:
    """A Recently watched item's background art (saved by _tv_save_watched_backdrop), or None."""
    if not TV_WATCHED_ID_RE.fullmatch(item_id or ""):
        raise ValueError("Invalid item.")
    try:
        return (TV_WATCHED_ART_DIR / f"{item_id}.bg").read_bytes()
    except OSError:
        return None


def _tv_wait_for_kodi(wait: float) -> None:
    deadline = time.monotonic() + wait
    while True:
        try:
            kodi_call("JSONRPC.Ping", timeout=3)
            return
        except UpstreamError:
            if time.monotonic() >= deadline:
                raise UpstreamError("Kodi didn't start in time.")
            time.sleep(1.5)


HOME_ARRIVED = 10
_home_jf_down_until = 0.0   # Home polls every 5 s: after Jellyfin fails, skip it for a while


def home_jellyfin_sessions() -> list[dict]:
    """What's playing on Jellyfin, for Home - without Kodi's own stream (Kodi plays through the
    Jellyfin add-on), which the Kodi card already shows. [] when Jellyfin isn't set up or answering."""
    global _home_jf_down_until
    if time.monotonic() < _home_jf_down_until:
        return []
    try:
        if not jellyfin_configured():
            return []
        return [x for x in jf_sessions() if "kodi" not in (x.get("client", "") + " " + x.get("device", "")).lower()]
    except (UpstreamError, ConfigError):
        _home_jf_down_until = time.monotonic() + 30
        return []


def home_arrived() -> list[dict]:
    """The newest movies from the TV launcher's row (finished downloads and Kodi's recently added)."""
    try:
        items = tv_row()["items"]
    except Exception:
        return []
    items = sorted(items, key=lambda x: str(x.get("added") or ""), reverse=True)[:HOME_ARRIVED]
    return [{k: x.get(k) for k in ("id", "title", "year", "quality", "added", "watched", "new", "in_kodi")}
            | {"poster": "t:" + str(x["id"]) if x.get("poster") else ""} for x in items]


def home_play(item_id: str) -> dict:
    """"Play on TV" from Home: plays it in Kodi, turning the TV on and opening Kodi first if needed."""
    if not TV_ID_RE.fullmatch(item_id or ""):
        raise ValueError("Invalid item.")
    try:
        kodi_call("JSONRPC.Ping", timeout=3)
        started_tv = False
    except UpstreamError:
        if not tv_configured():
            raise UpstreamError("Kodi isn't answering. Turn the TV on and open Kodi, then try again.") from None
        tv_start(launch_kodi=True)       # TV on + Kodi; tv_play then waits for Kodi to answer
        started_tv = True
    result = tv_play(item_id, wait=90 if started_tv else TV_PLAY_WAIT_SECS)   # waking the TV takes a while
    result["started_tv"] = started_tv
    return result


def tv_play(item_id: str, wait: float = TV_PLAY_WAIT_SECS, from_start: bool = False) -> dict:
    """Plays a row item in Kodi. The TV starts Kodi first, so this waits for Kodi to answer."""
    if not TV_ID_RE.fullmatch(item_id or ""):
        raise ValueError("Invalid item.")
    if TV_WATCHED_ID_RE.fullmatch(item_id):   # recently watched: resume where it was left, or from the start
        _tv_wait_for_kodi(wait)
        kind = "episodeid" if item_id[0] == "e" else "movieid"
        target = {kind: int(item_id[1:])}
        kodi_call("Player.Open", {"item": target, "options": {"resume": not from_start}})
        return {"ok": True, "played": target}
    item = next((x for x in tv_load_state()["items"] if x.get("id") == item_id), None)
    if not item:
        raise ValueError("That movie isn't in the row any more.")
    _tv_wait_for_kodi(wait)
    target = None
    if item.get("movieid"):
        target = {"movieid": int(item["movieid"])}
    else:   # a download Kodi hadn't scanned: look for it now, else play the file itself
        res = _kdict(kodi_call("VideoLibrary.GetMovies", {"properties": ["title", "year"], "filter": {
            "field": "title", "operator": "is", "value": item["title"]}}, timeout=10))
        hit = next((m for m in res.get("movies") or [] if _same_movie(item, m)), None)
        if hit:
            target = {"movieid": to_int(hit.get("movieid"))}
        elif item.get("file"):
            target = {"file": item["file"]}
    if not target:
        raise ValueError("Kodi can't find this movie yet. Update Kodi's library and try again.")
    kodi_call("Player.Open", {"item": target})
    return {"ok": True, "played": target}


# ---- Usenet streaming through AltMount -----------------------------------------------------------------
# "Stream" on a search result: the NZB goes to AltMount (POST /api/nzb/streams, its Stremio endpoint, which
# must be switched on in AltMount), AltMount checks the release with the Usenet provider and answers with a
# stream address, and Kodi plays that address. Nothing is downloaded. Streamed movies are remembered in
# streamed.json ("Streamed lately"), and a movie Kodi plays from AltMount counts as source "usenet".
STREAMS_FILE = BASE_DIR / "streamed.json"
STREAM_WAIT_SECS = 240               # how long AltMount may take to get a release ready
STREAMS_KEEP = 30
STREAM_JOBS_KEEP = 20
MAX_ALTMOUNT_BYTES = 2 * 1024 * 1024
STREAM_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{8,40}$")
_stream_jobs: dict[str, dict] = {}
_stream_jobs_lock = threading.Lock()
_streams_lock = threading.Lock()


def altmount_config(settings: dict | None = None) -> tuple[str, str]:
    """(address, API key) of AltMount, or blanks when it isn't set up."""
    s = settings if settings is not None else load_settings()
    url = str(s.get("altmount_url") or "").strip().rstrip("/")
    raw = str(s.get("altmount_key") or "")
    return url, (_dpapi_unprotect(raw).strip() if raw else "")


def altmount_configured(settings: dict | None = None) -> bool:
    url, key = altmount_config(settings)
    return bool(url and key)


def _multipart(fields: dict, file: tuple[str, bytes] | None = None) -> tuple[bytes, str]:
    boundary = "----NZBStream" + secrets.token_hex(16)
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8"))
    if file:
        fname = file[0].replace('"', "_")
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{fname}"\r\n'
                     "Content-Type: application/x-nzb\r\n\r\n".encode("utf-8") + file[1] + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def altmount_post(path: str, *, url: str, key: str, fields: dict | None = None,
                  file: tuple[str, bytes] | None = None, timeout: float = 30) -> tuple[int, object]:
    """POSTs a form to AltMount. Returns (HTTP status, parsed JSON or text); only a connection problem raises."""
    body, ctype = _multipart(fields or {}, file)
    req = urllib.request.Request(url + path, data=body, method="POST",
                                 headers={"User-Agent": USER_AGENT, "Content-Type": ctype, "X-Api-Key": key})
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read(MAX_ALTMOUNT_BYTES)
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read(64_000)
        except Exception:  # noqa: BLE001
            raw = b""
    except urllib.error.URLError as exc:
        raise UpstreamError(f"AltMount isn't answering ({redact(exc.reason)}). Is it running on the PC?") from None
    except OSError as exc:  # timeouts, resets
        raise UpstreamError(f"AltMount didn't answer in time ({redact(exc)}).") from None
    except (http.client.HTTPException, ValueError) as exc:
        raise UpstreamError(f"AltMount gave a bad answer ({type(exc).__name__}).") from None
    try:
        return status, json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return status, raw.decode("utf-8", "replace")[:300]


def altmount_error_text(status: int, payload) -> str:
    """AltMount's error answer ({"error": {"message", "details"}}) as one line."""
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            msg, det = str(err.get("message") or "").strip(), str(err.get("details") or "").strip()
            text = msg + (": " + det if det and det.lower() not in msg.lower() else "")
            if text:
                return redact(text)[:300]
        if isinstance(err, str) and err:
            return redact(err)[:300]
        if payload.get("message"):
            return redact(str(payload["message"]))[:300]
    elif isinstance(payload, str) and payload.strip() and not payload.lstrip().startswith("<"):
        return redact(payload.strip())[:300]
    return f"HTTP {status}"


def stream_problem(status: int, message: str) -> tuple[str, str]:
    """(kind, what to tell the user) for a release AltMount couldn't get ready."""
    m = message.lower()
    if status == 404 and "stremio" in m:
        return "setup", ("AltMount's Stremio Integration is switched off. In AltMount open Configuration › "
                         "Stremio Integration, turn on Enable Integration and Save.")
    if status == 401 or "api key" in m or "download_key" in m:
        return "setup", "AltMount didn't accept the API key. Check it under Settings › Connections."
    if "episode not specified" in m or "multi-episode" in m:
        return "pack", "It's a whole season in one release. Pick a single-episode release instead."
    if "compress" in m:
        return "archive", "It's packed in a compressed archive, so it has to be downloaded before it can play."
    if "encrypt" in m or "password" in m:
        return "archive", "It's password-protected, so it can't be streamed."
    if any(w in m for w in ("missing", "segment", "article", "incomplete", "corrupt", "not available", "unavailable")):
        return "missing", "Pieces of it are missing from your provider, so it wouldn't play to the end."
    if status == 408 or "timed out" in m or "timeout" in m:
        return "slow", "AltMount took too long to get it ready. Try again, or try another release."
    if "no media" in m or "no video" in m or "no file" in m:
        return "nomedia", "There's no video in this release that I can play."
    return "other", f"AltMount couldn't get it ready: {message}"


def release_title_year(release: str) -> tuple[str, str]:
    """('Glass Meridian', '2025') from 'Glass.Meridian.2025.1080p.WEB-DL...'; (cleaned name, '') without a year."""
    base = re.sub(r"\.(mkv|mp4|m4v|avi|ts|m2ts|mov|wmv)$", "", str(release or "").strip(), flags=re.I)
    # The year is the last year-like number before the quality tags ("Blade Runner 2049 (2017) 2160p" is 2017).
    tags = re.search(r"[ ._\-(\[](?:2160p|1080p|720p|576p|480p|4k|uhd|S\d{1,2}E\d{1,3}|web-?dl|webrip|bluray|blu-ray|remux|hdtv|dvdrip)\b", base, re.I)
    cut = tags.start() if tags else len(base)
    years = [y for y in re.finditer(r"[ ._\-(\[]+((?:19|20)\d{2})(?=[ ._\-)\]]|$)", base) if y.start() < cut and y.start() > 0]
    if years:
        y = years[-1]
        return re.sub(r"[._]+", " ", base[:y.start()]).strip(" -([") or base[:y.start()], y.group(1)
    m = re.match(r"^(.+?)[ ._\-]+(?:S\d{1,2}E\d{1,3}|2160p|1080p|720p|480p)", base, re.I)
    return re.sub(r"[._]+", " ", (m.group(1) if m else base)).strip(" -")[:120], ""


def load_streams() -> list[dict]:
    try:
        data = json.loads(read_text_retry(STREAMS_FILE))
    except (FileNotFoundError, ValueError):
        return []
    return [x for x in data if isinstance(x, dict) and x.get("id") and x.get("path")] if isinstance(data, list) else []


def _remember_stream(entry: dict) -> dict:
    with _streams_lock:
        items = [x for x in load_streams() if x.get("path") != entry["path"]]
        items.insert(0, entry)
        tmp = STREAMS_FILE.with_name(STREAMS_FILE.name + ".tmp")
        tmp.write_text(json.dumps(items[:STREAMS_KEEP], indent=1, ensure_ascii=False), encoding="utf-8")
        replace_retry(tmp, STREAMS_FILE)
    return entry


def _stream_mark_watched(stream_id: str) -> None:
    with _streams_lock:
        items = load_streams()
        for x in items:
            if x["id"] == stream_id:
                x["played"] = time.time()
        tmp = STREAMS_FILE.with_name(STREAMS_FILE.name + ".tmp")
        tmp.write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
        replace_retry(tmp, STREAMS_FILE)


def streams_view() -> dict:
    """"Streamed lately" for the Watch tab (no paths or keys)."""
    return {"configured": altmount_configured(),
            "items": [{k: x.get(k) for k in ("id", "title", "year", "res", "size", "t", "played")} for x in load_streams()[:10]]}


def is_stream_file(file: str) -> bool:
    """Whether a file Kodi is playing comes from AltMount (Usenet) rather than the PC's folders."""
    f = str(file or "").split("|", 1)[0]
    low = f.lower()
    return "/api/files/stream" in low or ("/webdav/" in low and low.startswith(("http://", "https://", "dav://", "davs://")))


def stream_path_of(file: str) -> str:
    """The AltMount path inside a stream address, however Kodi reports it: escaped or not, with Kodi's
    "|header=..." options on the end, or a WebDAV address."""
    f = str(file or "").split("|", 1)[0].strip()
    if not is_stream_file(f):
        return ""
    parts = urllib.parse.urlsplit(f)
    if "/api/files/stream" in parts.path.lower():
        path = (urllib.parse.parse_qs(parts.query).get("path") or [""])[0]
        if not path:   # Kodi sometimes reports the address unescaped: take everything after "path="
            m = re.search(r"[?&]path=(.+?)(?:&download_key=|$)", f, re.I)
            path = urllib.parse.unquote(m.group(1)) if m else ""
        return path
    i = parts.path.lower().find("/webdav/")
    return urllib.parse.unquote(parts.path[i + len("/webdav"):]) if i >= 0 else ""


def _norm_name(path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (str(path).replace("\\", "/").rsplit("/", 1)[-1]).lower())


def stream_for_file(file: str) -> dict | None:
    """The "Streamed lately" entry for a file Kodi is playing, if it's one of ours: the same path, else the same
    file name (a WebDAV address, or one Kodi has rewritten)."""
    path = stream_path_of(file)
    if not path:
        return None
    items = load_streams()
    hit = next((x for x in items if x.get("path") == path), None)
    if hit:
        return hit
    name = _norm_name(path)
    return next((x for x in items if name and _norm_name(x.get("path", "")) == name), None)


STREAM_RECENT_SECS = 12 * 3600      # "the one you sent to the TV" counts for this long
_stream_logged: set[str] = set()


def stream_title(file: str, label: str = "") -> tuple[str, str]:
    """(movie name, year) for a stream Kodi is playing: the name kept when it was streamed, else read from the
    file's name in the address, else the stream last sent to the TV with Play on TV. Never the address itself,
    which is what Kodi shows as its label."""
    entry = stream_for_file(file)
    if entry:
        return entry["title"], str(entry.get("year") or "")
    shown = re.sub(r"(download_key=)[^&|]+", r"\1…", str(file))[:300]
    if shown not in _stream_logged:          # once per address: what Kodi reports, to match it next time
        _stream_logged.add(shown)
        log.info("stream title: no match for Kodi's file %r (label %r)", shown, str(label)[:120])
    base = stream_path_of(file).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if base:                                  # a file name in the address: the movie's name is in it
        title, year = release_title_year(base)
        if title:
            return title, year
    recent = [x for x in load_streams() if x.get("played") and time.time() - float(x["played"]) < STREAM_RECENT_SECS]
    if recent:                                # no name in the address: the one last sent to the TV
        last = max(recent, key=lambda x: float(x["played"]))
        return last["title"], str(last.get("year") or "")
    if label and "://" not in label and "download_key" not in label and "stream?" not in label:
        title, year = release_title_year(label)
        if title:
            return title, year
    return "a stream from Usenet", ""


def _local_ipv4s() -> list[str]:
    """Every IPv4 address this PC has (home network, VPNs, virtual adapters)."""
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith(("127.", "169.254.")):
                found.append(ip)
    except OSError:
        pass
    return found


def _lan_ip_towards(host: str, local_ips: list[str] | None = None) -> str:
    """This PC's address on the TV's own network, for addresses the TV can open. An address in the same /24 as
    the TV wins: with a VPN on, Windows' route to the TV can go through the VPN's address, which the TV can't reach."""
    try:
        target = ipaddress.ip_address(host)
    except ValueError:
        try:
            target = ipaddress.ip_address(socket.gethostbyname(host))
        except OSError:
            target = None
    ips = local_ips if local_ips is not None else _local_ipv4s()
    if target is not None and target.version == 4:
        net = ipaddress.ip_network(f"{target}/24", strict=False)
        same = [ip for ip in ips if ipaddress.ip_address(ip) in net]
        if same:
            return same[0]
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((str(target or host), 9))
            return s.getsockname()[0]
    except OSError:
        return ""


def altmount_stream_url(path: str, viewer_host: str = "") -> str:
    """The address a player opens for a streamed file. AltMount on "localhost" is swapped for an address the
    viewer (the TV, or the phone) can reach. The key in it is AltMount's own download key (a hash of its API key)."""
    url, key = altmount_config()
    if not url or not key:
        raise ValueError("Usenet streaming isn't set up. Add AltMount under Settings › Connections.")
    p = urllib.parse.urlsplit(url)
    host = p.hostname or "localhost"
    if host in ("localhost", "127.0.0.1", "::1") and viewer_host:
        host = viewer_host
    port = f":{p.port}" if p.port else ""
    netloc = (f"[{host}]" if ":" in host else host) + port
    download_key = hashlib.sha256(key.encode("utf-8")).hexdigest()
    # The path last, so the address ends in ".mkv" (players guess the format from it).
    return (f"{p.scheme or 'http'}://{netloc}/api/files/stream?download_key={download_key}"
            f"&path={urllib.parse.quote(path, safe='')}")


def _kodi_host() -> str:
    """The TV's (Kodi's) address, from the Kodi connection settings."""
    try:
        k = get_config().get("kodi") or {}
        return urllib.parse.urlsplit(normalize_kodi_url(str(k.get("url") or ""))).hostname or ""
    except Exception:  # noqa: BLE001
        return ""


def _home_lan_ip() -> str:
    """This PC's home-network address when the TV's isn't known: a private 192.168.x.x / 10.x / 172.16-31.x one,
    192.168 first (VPNs and virtual adapters usually sit on 10.x or 172.x)."""
    ips = [ip for ip in _local_ipv4s() if ipaddress.ip_address(ip).is_private]
    ips.sort(key=lambda ip: (not ip.startswith("192.168."), ip.endswith(".1")))
    return ips[0] if ips else ""


def _stream_job_view(job: dict) -> dict:
    out = {k: job.get(k) for k in ("id", "state", "stage", "title", "year", "release", "res", "size", "kind",
                                    "message", "stream_id")}
    out["elapsed"] = int(time.time() - job["started"])
    return out


def _stream_run(job_id: str, item: dict) -> None:
    def update(**kw):
        with _stream_jobs_lock:
            if job_id in _stream_jobs:
                _stream_jobs[job_id].update(kw)

    def cancelled() -> bool:
        with _stream_jobs_lock:
            return _stream_jobs.get(job_id, {}).get("state") == "cancelled"

    try:
        url, key = altmount_config()
        nzb_data, used = download_indexer_nzb(item)
        if cancelled():
            return
        update(stage="check")
        status, payload = altmount_post("/api/nzb/streams", url=url, key=key,
                                        fields={"timeout": str(STREAM_WAIT_SECS)},
                                        file=(safe_nzb_filename(item["title"]), nzb_data), timeout=STREAM_WAIT_SECS + 30)
        if cancelled():
            return
        streams = payload.get("streams") if status == 200 and isinstance(payload, dict) else None
        if not isinstance(streams, list) or not streams:
            text = altmount_error_text(status, payload) if status != 200 else "no video in it"
            kind, message = stream_problem(status, text if status != 200 else "no media")
            log.info("stream failed title=%r status=%s: %s", item["title"], status, text)
            update(state="failed", kind=kind, message=message)
            return
        good = [s for s in streams if isinstance(s, dict) and s.get("url")]
        pick = next((s for s in good if not re.search(r"\bsample\b", str(s.get("name") or ""), re.I)), good[0] if good else None)
        path = (urllib.parse.parse_qs(urllib.parse.urlsplit(str(pick.get("url") if pick else "")).query).get("path") or [""])[0]
        if not path:
            update(state="failed", kind="other", message="AltMount answered without a stream address.")
            return
        title, year = release_title_year(item["title"])
        entry = _remember_stream({"id": secrets.token_urlsafe(9), "title": title, "year": year, "release": item["title"],
                                  "res": release_resolution(item["title"]), "size": format_bytes(item.get("size_bytes") or 0),
                                  "path": path, "t": time.time()})
        log.info("stream ready title=%r indexer=%s", item["title"], used)
        update(state="ready", stage="ready", stream_id=entry["id"])
    except (UpstreamError, ValueError, ConfigError) as exc:
        kind, message = ("offline", str(exc)) if "AltMount" in str(exc) else ("other", str(exc))
        update(state="failed", kind=kind, message=message)
    except Exception:  # noqa: BLE001 - a background job: report, never die quietly
        log.exception("stream job failed")
        update(state="failed", kind="other", message="Something went wrong (see mobile_server.log on the PC).")


def stream_start(result_id: str) -> dict:
    """Starts getting a search result ready to stream; the phone follows it with stream_status()."""
    if not altmount_configured():
        raise ValueError("Usenet streaming isn't set up. Add AltMount under Settings › Connections.")
    item = get_cached_result(result_id)
    if not item:
        raise ValueError("That search result expired. Search again and retry.")
    title, year = release_title_year(item["title"])
    job = {"id": secrets.token_urlsafe(12), "state": "working", "stage": "nzb", "title": title, "year": year,
           "release": item["title"], "res": release_resolution(item["title"]),
           "size": format_bytes(item.get("size_bytes") or 0), "started": time.time()}
    with _stream_jobs_lock:
        while len(_stream_jobs) >= STREAM_JOBS_KEEP:
            del _stream_jobs[next(iter(_stream_jobs))]
        _stream_jobs[job["id"]] = job
    threading.Thread(target=_stream_run, args=(job["id"], item), daemon=True, name="stream-job").start()
    return _stream_job_view(job)


def stream_status(job_id: str) -> dict:
    with _stream_jobs_lock:
        job = _stream_jobs.get(job_id) if STREAM_ID_RE.fullmatch(job_id or "") else None
        if not job:
            raise ValueError("I lost track of that one. Tap Stream again.")
        return _stream_job_view(job)


def stream_cancel(job_id: str) -> dict:
    with _stream_jobs_lock:
        job = _stream_jobs.get(job_id)
        if job and job.get("state") == "working":
            job["state"] = "cancelled"
    return {"ok": True}


def _stream_entry(stream_id: str) -> dict:
    entry = next((x for x in load_streams() if x["id"] == stream_id), None) if STREAM_ID_RE.fullmatch(stream_id or "") else None
    if not entry:
        raise ValueError("That stream isn't in the list any more. Stream it again from Get.")
    return entry


def stream_to_tv(stream_id: str) -> dict:
    """Plays a streamed movie in Kodi, turning the TV on and opening Kodi first if needed."""
    entry = _stream_entry(stream_id)
    kodi_host = _kodi_host()
    url = altmount_stream_url(entry["path"], (_lan_ip_towards(kodi_host) if kodi_host else "") or _home_lan_ip())
    if urllib.parse.urlsplit(url).hostname in ("localhost", "127.0.0.1", "::1"):
        raise UpstreamError("I couldn't work out this PC's address on the home network for the TV. In Settings › "
                            "Connections, set AltMount's address to the PC's address, e.g. http://192.168.68.60:8090.")
    try:
        kodi_call("JSONRPC.Ping", timeout=3)
        started_tv = False
    except UpstreamError:
        if not tv_configured():
            raise UpstreamError("Kodi isn't answering. Turn the TV on and open Kodi, then try again.") from None
        tv_start(launch_kodi=True)
        started_tv = True
    _tv_wait_for_kodi(90 if started_tv else TV_PLAY_WAIT_SECS)
    if not started_tv and tv_configured():
        front = tv_front_app()
        if front and not front.get("kodi"):   # Kodi answers in the background: bring it up, or it plays out of sight
            try:
                tv_open_kodi()
                time.sleep(2)
            except Exception as exc:  # noqa: BLE001 - best effort: play anyway
                log.info("stream to tv: couldn't bring Kodi to the front (%s)", exc)
    where = urllib.parse.urlsplit(url).netloc
    log.info("stream to tv: Kodi at %s opens AltMount at %s", kodi_host or "?", where)
    kodi_call("Player.Open", {"item": {"file": url}}, timeout=20)
    # Kodi says OK at once, then may fail to open the address: check it really started.
    playing = False
    for _ in range(10):
        time.sleep(1)
        try:
            players = kodi_call("Player.GetActivePlayers", timeout=4)
        except UpstreamError:
            continue
        if isinstance(players, list) and players:
            playing = True
            break
    if not playing:
        log.info("stream to tv: Kodi didn't start playing %s", where)
        raise UpstreamError(f"Kodi couldn't open the stream from {where}. Check that the TV can reach the PC: "
                            f"on the phone, http://{where}/live should show OK.")
    _stream_mark_watched(stream_id)
    return {"ok": True, "started_tv": started_tv, "title": entry["title"]}


def stream_phone_url(stream_id: str, phone_host: str) -> dict:
    """The address the phone's video player (VLC) opens: AltMount by the same name the phone reached this PC by."""
    entry = _stream_entry(stream_id)
    host = urllib.parse.urlsplit("http://" + (phone_host or "")).hostname or ""
    _stream_mark_watched(stream_id)
    return {"ok": True, "url": altmount_stream_url(entry["path"], host), "title": entry["title"]}


def altmount_test(url: str, key: str) -> dict:
    """Connections › Test: is AltMount there, is the key right, is its Stremio Integration on?"""
    if not url:
        return {"ok": False, "message": "Enter AltMount's address first, e.g. http://localhost:8090."}
    try:
        http_fetch(url + "/live", timeout=6, max_bytes=64_000)
    except UpstreamError as exc:
        return {"ok": False, "message": f"AltMount isn't answering at that address ({exc}). Is it running?"}
    if not key:
        return {"ok": False, "message": "AltMount is answering. Now add its API key (AltMount › Configuration › System)."}
    status, payload = altmount_post("/api/nzb/streams", url=url, key=key, timeout=10)
    text = altmount_error_text(status, payload)
    if status == 404:
        return {"ok": False, "message": "AltMount is answering, but its Stremio Integration is off. Turn it on: "
                                        "AltMount › Configuration › Stremio Integration › Enable Integration › Save."}
    if status == 401:
        return {"ok": False, "message": "AltMount is answering, but it didn't accept that API key."}
    if status == 400:   # it got past the key check and only wants an NZB: all set
        return {"ok": True, "message": "AltMount is answering, accepts the key and is ready to stream."}
    return {"ok": False, "message": f"AltMount answered: {text}"}


# Screensaver photos (GET /api/tv/photos, /api/tv/photo): pictures from a folder on this PC, which
# the TV's screensaver shows one after another. The folder is the first line of tv_photos_folder.txt
# next to this file, else the user's Pictures folder (or OneDrive's). Only files found by walking that
# folder can be served, by an id derived from their path; nothing else on the PC is reachable.
TV_PHOTOS_FILE = BASE_DIR / "tv_photos_folder.txt"
TV_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
TV_PHOTO_MAX_FILES = 5000
TV_PHOTO_MAX_BYTES = 40 * 1024 * 1024
TV_PHOTO_ID_RE = re.compile(r"[0-9a-f]{16}")
_tv_photo_lock = threading.Lock()
_tv_photo_index: dict = {"at": 0.0, "folder": "", "items": {}}


def tv_photos_folder() -> Path | None:
    try:
        line = TV_PHOTOS_FILE.read_text(encoding="utf-8-sig").strip().splitlines()[0].strip().strip('"')
        if line and Path(line).is_dir():
            return Path(line)
    except (OSError, IndexError):
        pass
    home = Path.home()
    for cand in (home / "Pictures", home / "OneDrive" / "Pictures", home / "OneDrive" / "Imágenes", home / "Imágenes"):
        if cand.is_dir():
            return cand
    return None


def tv_photo_index(max_age: float = 600) -> dict:
    """{id: (path, mtime)} for the pictures in the folder, re-walked at most every max_age seconds."""
    folder = tv_photos_folder()
    key = str(folder or "")
    with _tv_photo_lock:
        if _tv_photo_index["folder"] == key and time.time() - _tv_photo_index["at"] < max_age:
            return _tv_photo_index["items"]
        items: dict = {}
        if folder:
            for root, dirs, files in os.walk(folder):
                dirs[:] = sorted(d for d in dirs if not d.startswith((".", "$")))
                for f in sorted(files):
                    if Path(f).suffix.lower() not in TV_PHOTO_EXTS or f.startswith("."):
                        continue
                    full = Path(root) / f
                    try:
                        st = full.stat()
                    except OSError:
                        continue
                    if not 0 < st.st_size <= TV_PHOTO_MAX_BYTES:
                        continue
                    rel = str(full.relative_to(folder)).replace("\\", "/")
                    items[hashlib.sha1(rel.encode("utf-8")).hexdigest()[:16]] = (str(full), st.st_mtime)
                    if len(items) >= TV_PHOTO_MAX_FILES:
                        break
                if len(items) >= TV_PHOTO_MAX_FILES:
                    break
        _tv_photo_index.update(at=time.time(), folder=key, items=items)
        return items


def tv_photos_list() -> dict:
    items = tv_photo_index()
    folder = tv_photos_folder()
    return {"folder": folder.name if folder else "", "count": len(items),
            "items": [{"id": pid, "name": Path(path).stem[:80], "date": time.strftime("%Y-%m-%d", time.localtime(mtime))}
                      for pid, (path, mtime) in sorted(items.items(), key=lambda kv: -kv[1][1])]}


def tv_photo(photo_id: str) -> bytes | None:
    if not TV_PHOTO_ID_RE.fullmatch(photo_id or ""):
        raise ValueError("Invalid photo.")
    hit = tv_photo_index().get(photo_id)
    if not hit:
        return None
    try:
        raw = Path(hit[0]).read_bytes()
    except OSError:
        return None
    return raw if _image_type(raw) else None


def tv_mark_watched(item_id: str) -> dict:
    """Marks a recently-watched item as watched in Kodi (play count 1, resume point cleared)."""
    if not TV_WATCHED_ID_RE.fullmatch(item_id or ""):
        raise ValueError("Invalid item.")
    num = int(item_id[1:])
    done = {"playcount": 1, "resume": {"position": 0, "total": 0}}
    if item_id[0] == "e":
        kodi_call("VideoLibrary.SetEpisodeDetails", dict(done, episodeid=num))
    else:
        kodi_call("VideoLibrary.SetMovieDetails", dict(done, movieid=num))
    with _tv_files_lock:   # show it finished right away in the saved list too
        state = tv_watched_load()
        for it in state["items"]:
            if it.get("id") == item_id:
                it.update(progress=1.0, remaining=0, watched=True)
        tmp = TV_WATCHED_FILE.with_name(TV_WATCHED_FILE.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
        replace_retry(tmp, TV_WATCHED_FILE)
    return {"ok": True}


def tv_finished(limit: int = 10) -> dict:
    """Downloads SABnzbd finished most recently (movies and episodes), newest first, for the TV's
    "Ready to watch" note. A poster is offered when the TV row already has one for that title."""
    slots = sab_request("history", {"limit": 30}).get("history", {}).get("slots", [])
    row = tv_load_state()["items"]
    out = []
    for x in slots:
        if str(x.get("status", "")).lower() != "completed":
            continue
        name, cat = str(x.get("name") or ""), str(x.get("category") or "")
        clean, year, kind = artwork_query(name, cat)
        nzo = re.sub(r"[^A-Za-z0-9_]", "", str(x.get("nzo_id") or ""))[:80]
        if not clean or not nzo or kind not in ("movie", "tv"):
            continue
        ep = re.search(r"\bS(\d{1,2})E(\d{1,3})\b", name.replace(".", " "), re.I)
        title = f"{clean} S{int(ep.group(1))} E{int(ep.group(2))}" if kind == "tv" and ep else clean
        poster = ""
        if kind == "movie":
            hit = next((it for it in row if it.get("poster") and _same_movie({"title": clean, "year": year}, it)), None)
            if hit:
                poster = f"/api/tv/poster?id={hit['id']}"
        out.append({"id": f"s{nzo}", "title": title, "year": to_int(year) or None, "kind": kind,
                    "completed": to_int(x.get("completed")), "poster": poster})
        if len(out) >= limit:
            break
    out.sort(key=lambda it: -it["completed"])
    return {"items": out}


_tv_watched_refreshing = threading.Lock()
_TV_SEEN_FILTER = {"or": [{"field": "inprogress", "operator": "true", "value": ""},
                          {"field": "playcount", "operator": "greaterthan", "value": "0"}]}


def _tv_progress(raw: dict) -> dict:
    """{"progress": 0..1, "remaining": minutes left, "runtime": minutes} from Kodi's resume point."""
    resume = _kdict(raw.get("resume"))
    pos, total = to_float(resume.get("position")), to_float(resume.get("total"))
    if total <= 0:
        total = to_float(raw.get("runtime"))
    watched = to_int(raw.get("playcount")) > 0
    if pos > 0 and total > 0:
        progress, remaining = min(1.0, pos / total), round((total - pos) / 60)
    else:
        progress, remaining = (1.0 if watched else 0.0), 0
    return {"progress": round(progress, 3), "remaining": remaining, "runtime": round(total / 60), "watched": watched}


def tv_kodi_watched(limit: int = TV_WATCHED_SIZE) -> list[dict]:
    """Movies and episodes started or finished in Kodi, the last played first."""
    common = {"filter": _TV_SEEN_FILTER, "sort": {"method": "lastplayed", "order": "descending"},
              "limits": {"start": 0, "end": limit * 2}}
    movies = _kdict(kodi_call("VideoLibrary.GetMovies", dict(common, properties=[
        "title", "year", "runtime", "art", "thumbnail", "playcount", "resume", "lastplayed", "streamdetails",
        "plot", "genre"]), timeout=10)).get("movies") or []
    episodes = _kdict(kodi_call("VideoLibrary.GetEpisodes", dict(common, properties=[
        "title", "showtitle", "season", "episode", "runtime", "art", "playcount", "resume", "lastplayed", "plot"]),
        timeout=10)).get("episodes") or []
    out = []
    for mv in movies:
        if not isinstance(mv, dict) or to_int(mv.get("movieid")) <= 0:
            continue
        art = _kdict(mv.get("art"))
        quality, _hdr = video_quality(mv.get("streamdetails"))
        out.append({"id": f"m{to_int(mv.get('movieid'))}", "movieid": to_int(mv.get("movieid")),
                    "title": str(mv.get("title") or "Untitled"), "year": to_int(mv.get("year")) or None,
                    "quality": "4K" if quality == "2160p" else quality,
                    "lastplayed": str(mv.get("lastplayed") or ""), "plot": str(mv.get("plot") or "")[:400],
                    "genres": [str(g) for g in (mv.get("genre") or [])][:3], "bg_ref": _art_ref(art.get("fanart")),
                    "art_ref": _art_ref(art.get("poster") or mv.get("thumbnail"))} | _tv_progress(mv))
    for ep in episodes:
        if not isinstance(ep, dict) or to_int(ep.get("episodeid")) <= 0:
            continue
        art = _kdict(ep.get("art"))
        show = str(ep.get("showtitle") or "")
        out.append({"id": f"e{to_int(ep.get('episodeid'))}", "episodeid": to_int(ep.get("episodeid")),
                    "title": show or str(ep.get("title") or "Untitled"), "episode_title": str(ep.get("title") or ""),
                    "season": to_int(ep.get("season")), "episode": to_int(ep.get("episode")),
                    "lastplayed": str(ep.get("lastplayed") or ""), "plot": str(ep.get("plot") or "")[:400],
                    "bg_ref": _art_ref(art.get("tvshow.fanart") or art.get("fanart")),
                    "art_ref": _art_ref(art.get("tvshow.poster") or art.get("season.poster"))} | _tv_progress(ep))
    out.sort(key=lambda x: x["lastplayed"], reverse=True)   # "YYYY-MM-DD HH:MM:SS" sorts as text
    now = _tv_now_watching()
    if now:   # still open in Kodi (playing, or paused behind Home): first, with where it is now
        out = [now] + [x for x in out if x["id"] != now["id"]]
    return out


def _tv_now_watching() -> dict | None:
    """The movie or episode open in Kodi's player, as a Recently watched entry, or None.

    Kodi writes the resume point and "last played" only when playback stops, so a movie left playing or paused
    while the owner pressed Home would otherwise be missing from the row until Kodi was stopped."""
    try:
        players = kodi_call("Player.GetActivePlayers", timeout=4)
        players = [p for p in players if isinstance(p, dict)] if isinstance(players, list) else []
        video = [p for p in players if p.get("type") == "video"]
        if not video:
            return None
        pid = to_int(video[0].get("playerid"))
        item = _kdict(_kdict(kodi_call("Player.GetItem", {"playerid": pid, "properties": [
            "title", "showtitle", "season", "episode", "year", "art", "thumbnail", "streamdetails", "plot", "genre"]},
            timeout=4)).get("item"))
        props = _kdict(kodi_call("Player.GetProperties", {"playerid": pid, "properties": ["time", "totaltime"]}, timeout=4))
    except (UpstreamError, ConfigError):
        return None
    kind, kid = str(item.get("type") or ""), to_int(item.get("id"))
    if kind not in ("movie", "episode") or kid <= 0:
        return None   # a file outside the library: nothing to play it again by
    pos, total = _kodi_secs(props.get("time")), _kodi_secs(props.get("totaltime"))
    progress = round(min(1.0, pos / total), 3) if pos > 0 and total > 0 else 0.0
    base = {"lastplayed": time.strftime("%Y-%m-%d %H:%M:%S"), "progress": progress,
            "remaining": round((total - pos) / 60) if total > pos else 0, "runtime": round(total / 60), "watched": False}
    art = _kdict(item.get("art"))
    base["plot"] = str(item.get("plot") or "")[:400]
    base["bg_ref"] = _art_ref(art.get("fanart") or art.get("tvshow.fanart"))
    if kind == "movie":
        base["genres"] = [str(g) for g in (item.get("genre") or [])][:3]
        quality, _hdr = video_quality(item.get("streamdetails"))
        return base | {"id": f"m{kid}", "movieid": kid, "title": str(item.get("title") or item.get("label") or "Untitled"),
                       "year": to_int(item.get("year")) or None, "quality": "4K" if quality == "2160p" else quality,
                       "art_ref": _art_ref(art.get("poster") or item.get("thumbnail"))}
    show = str(item.get("showtitle") or "")
    return base | {"id": f"e{kid}", "episodeid": kid, "title": show or str(item.get("title") or "Untitled"),
                   "episode_title": str(item.get("title") or ""), "season": to_int(item.get("season")),
                   "episode": to_int(item.get("episode")),
                   "art_ref": _art_ref(art.get("tvshow.poster") or art.get("season.poster"))}


def _tv_save_watched_poster(item: dict) -> bool:
    TV_WATCHED_ART_DIR.mkdir(exist_ok=True)
    dest = TV_WATCHED_ART_DIR / f"{item['id']}.img"
    if dest.exists():
        return True
    if not item.get("art_ref"):
        return False
    try:
        raw = kodi_art(item["art_ref"])[0]
    except (UpstreamError, ValueError, ConfigError):
        return False
    if not raw or not _image_type(raw):
        return False
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(raw)
    replace_retry(tmp, dest)
    return True


def _tv_save_watched_backdrop(item: dict) -> bool:
    """The movie's or show's background art (Kodi's fanart), for the big picture behind the TV's home screen."""
    dest = TV_WATCHED_ART_DIR / f"{item['id']}.bg"
    if dest.exists():
        return True
    if not item.get("bg_ref"):
        return False
    try:
        raw = kodi_art(item["bg_ref"])[0]
    except (UpstreamError, ValueError, ConfigError):
        return False
    if not raw or not _image_type(raw):
        return False
    tmp = dest.with_suffix(".tmpbg")
    tmp.write_bytes(raw)
    replace_retry(tmp, dest)
    return True


def tv_watched_load() -> dict:
    try:
        with _tv_files_lock:
            data = json.loads(read_text_retry(TV_WATCHED_FILE))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    return {"updated": to_float(data.get("updated")), "items": [x for x in data.get("items") or [] if isinstance(x, dict)]}


def tv_watched_refresh() -> dict:
    """Re-reads the list from Kodi; while Kodi is closed the last list is kept."""
    with _tv_watched_refreshing:
        state = tv_watched_load()
        try:
            if not kodi_configured():
                return state
            found = tv_kodi_watched()
        except (UpstreamError, ConfigError):
            return state
        items = []
        for it in found:   # no poster, no tile
            if _tv_save_watched_poster(it):
                it.pop("art_ref", None)
                it["backdrop"] = _tv_save_watched_backdrop(it)
                it.pop("bg_ref", None)
                items.append(it)
            if len(items) >= TV_WATCHED_SIZE:
                break
        if TV_WATCHED_ART_DIR.exists():
            keep = {f"{it['id']}.img" for it in items} | {f"{it['id']}.bg" for it in items}
            for f in list(TV_WATCHED_ART_DIR.glob("*.img")) + list(TV_WATCHED_ART_DIR.glob("*.bg")):
                if f.name not in keep:
                    f.unlink(missing_ok=True)
        state = {"updated": time.time(), "items": items}
        with _tv_files_lock:
            tmp = TV_WATCHED_FILE.with_name(TV_WATCHED_FILE.name + ".tmp")
            tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
            replace_retry(tmp, TV_WATCHED_FILE)
        return state


def tv_watched_row(background: bool = True, fresh: bool = False) -> dict:
    """Recently watched as the TV reads it; a stale list is returned at once and re-read on a worker thread.
    fresh: read Kodi now (the TV asks this when Home comes back, so what was just watched shows at once);
    at most every 10 seconds, and the saved list when Kodi doesn't answer."""
    state = tv_watched_load()
    if fresh and time.time() - state["updated"] > 10:
        try:
            return tv_watched_row_from(tv_watched_refresh())
        except Exception:
            log.exception("Reading Kodi's recently watched list failed")
    if time.time() - state["updated"] > TV_WATCHED_REFRESH_SECS:
        if background and state["items"]:
            if not _tv_watched_refreshing.locked():
                threading.Thread(target=tv_watched_refresh, name="tv-watched", daemon=True).start()
        else:
            state = tv_watched_refresh()
    return tv_watched_row_from(state)


def tv_watched_row_from(state: dict) -> dict:
    keys = ("id", "title", "episode_title", "year", "season", "episode", "runtime", "quality", "watched",
            "progress", "remaining", "movieid", "episodeid", "lastplayed", "plot", "genres")
    items = [{k: it.get(k) for k in keys} | {"poster": f"/api/tv/poster?id={it['id']}",
                                             "backdrop": f"/api/tv/backdrop?id={it['id']}" if it.get("backdrop") else ""}
             for it in state["items"]]
    return {"updated": state["updated"], "items": items}


class BackgroundWorker:
    """Sends download alerts and runs saved searches while the server is up."""

    TICK = 15
    HISTORY_EVERY = 30

    def __init__(self):
        self._seen: set[str] | None = None
        self._next_history = 0.0
        self._next_drives = time.time() + 60  # first drive check a minute after start-up
        self._drive_sent: dict[str, float] = {}
        self._next_tv = 0.0
        self._next_watched = 0.0
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="mobile-worker", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.TICK):
            try:
                self.tick()
            except Exception:
                log.exception("Background task failed")

    def tick(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        settings = load_settings()
        if settings["ntfy_url"] and now >= self._next_history:
            self._next_history = now + self.HISTORY_EVERY
            try:
                self.check_history(settings)
            except Exception:
                log.exception("Download alert check failed")
        if now >= self._next_tv:
            self._next_tv = now + TV_REFRESH_SECS
            try:
                tv_refresh()
            except Exception:
                log.exception("Refreshing the TV row failed")
        if now >= self._next_watched:
            self._next_watched = now + TV_WATCHED_REFRESH_SECS
            try:
                tv_watched_refresh()
            except Exception:
                log.exception("Refreshing the TV's recently watched row failed")
        # Saved searches report only through phone alerts: without alerts set up they'd use indexer
        # API calls for nothing, so they only run when alerts are on.
        due = due_saved_search(settings, now) if settings["ntfy_url"] else None
        if due:  # one per tick spreads indexer API use out
            try:
                run_saved_search(due["id"], notify=True)
            except Exception:
                log.exception("Saved search failed")
        if now >= self._next_drives:
            self._next_drives = now + DRIVE_CHECK_EVERY
            try:
                self.check_drives(settings, now)
            except Exception:
                log.exception("Drive check failed")

    def check_drives(self, settings: dict, now: float) -> None:
        """Refreshes drive health (also logging temperatures for the 24 h graph) and sends drive alerts."""
        report = drive_report(force=True, with_rate=False)
        if not settings["ntfy_url"]:
            return
        alerts = list(drive_alerts(report, settings, self._drive_sent, now))   # (keeps its "already sent" bookkeeping)
        if _hal_state["running"]:
            return   # HAL watches the drives too (and lets you hide an alert for 24 h): one phone alert, not two
        for title, message, prio in alerts:
            try:
                send_notification(title, message, tags=("floppy_disk",), priority=prio, settings=settings)
            except UpstreamError as exc:
                log.warning("Drive alert failed: %s", redact(exc))

    def check_history(self, settings: dict) -> None:
        try:
            slots = sab_request("history", {"limit": 40}).get("history", {}).get("slots", [])
        except (UpstreamError, ConfigError):
            return
        final = {str(x.get("nzo_id")): x for x in slots if str(x.get("status", "")).lower() in ("completed", "failed")}
        if self._seen is None:  # first look: remember what's already there, announce nothing
            self._seen = set(final)
            return
        fresh = [x for nzo, x in final.items() if nzo not in self._seen]
        self._seen |= set(final)
        if len(self._seen) > 5000:
            self._seen = set(final)
        done = [str(x.get("name") or "Download") for x in fresh if str(x.get("status")).lower() == "completed"]
        failed = [f"{x.get('name') or 'Download'} — {str(x.get('fail_message') or 'failed')[:120]}"
                  for x in fresh if str(x.get("status")).lower() == "failed"]
        try:
            if done and settings.get("notify_complete"):
                send_notification("Download finished" if len(done) == 1 else f"{len(done)} downloads finished",
                                  _title_list(done), tags=("white_check_mark",), settings=settings)
            if failed and settings.get("notify_failed"):
                send_notification("Download failed" if len(failed) == 1 else f"{len(failed)} downloads failed",
                                  _title_list(failed), tags=("warning",), priority=4, settings=settings)
        except UpstreamError as exc:
            log.warning("Download alert failed: %s", redact(exc))


def sab_stats(days: int = 7) -> dict:
    """Data downloaded (SABnzbd server_stats) plus finished / failed jobs per day for the last week."""
    st = sab_request("server_stats")
    slots = sab_request("history", {"limit": 500}).get("history", {}).get("slots", [])
    today = date.today()
    buckets = {(today - timedelta(days=d)).isoformat(): {"date": (today - timedelta(days=d)).isoformat(), "done": 0, "failed": 0, "bytes": 0}
               for d in range(days - 1, -1, -1)}
    for x in slots:
        ts = to_int(x.get("completed"))
        bucket = buckets.get(date.fromtimestamp(ts).isoformat()) if ts > 0 else None
        if not bucket:
            continue
        status = str(x.get("status", "")).lower()
        if status == "completed":
            bucket["done"] += 1
            bucket["bytes"] += to_int(x.get("bytes"))
        elif status == "failed":
            bucket["failed"] += 1
    return {"totals": {k: to_int(st.get(k)) for k in ("day", "week", "month", "total")}, "days": list(buckets.values())}


# ==============================================================================
# 5d. DRIVE HEALTH
# ==============================================================================
# Physical disks, health and SMART-style counters come from Windows (Get-PhysicalDisk and
# Get-StorageReliabilityCounter) every 10 minutes; free space is read live. Temperatures need the
# server to run as administrator, or LibreHardwareMonitor's web server (port 8085) to be on.

DRIVE_CHECK_EVERY = 600
DRIVE_HISTORY_FILE = BASE_DIR / "drive_history.json"
DRIVE_HISTORY_HOURS = 24
DRIVE_TEMP_ALERT = 55          # °C for hard drives and SATA SSDs
DRIVE_NVME_TEMP_EXTRA = 15     # NVMe SSDs are rated hotter (~70 °C)
DRIVE_WARM_MARGIN = 4          # "running warm" this close to the limit
DRIVE_LOW_GB = 75
DRIVE_ALERT_REPEAT = 24 * 3600
LHM_PORT = 8085

DRIVES_PS = r"""
function N($v) { if ($v -ne $null) { $v } else { $null } }
$r = @{}
try { $r.disks = @(Get-PhysicalDisk | ForEach-Object {
    $rc = $null; try { $rc = $_ | Get-StorageReliabilityCounter -ErrorAction Stop } catch {}
    @{ id = "$($_.DeviceId)"; uid = "$($_.UniqueId)"; name = ("" + $_.FriendlyName).Trim(); model = ("" + $_.Model).Trim();
       serial = ("" + $_.SerialNumber).Trim(); firmware = ("" + $_.FirmwareVersion).Trim();
       media = "$($_.MediaType)"; bus = "$($_.BusType)"; size = [double]$_.Size; health = "$($_.HealthStatus)";
       status = "$($_.OperationalStatus)"; spindle = $_.SpindleSpeed;
       sector = $_.PhysicalSectorSize; lsector = $_.LogicalSectorSize;
       temp = $(if ($rc) { N $rc.Temperature }); temp_max = $(if ($rc) { N $rc.TemperatureMax });
       wear = $(if ($rc) { N $rc.Wear }); hours = $(if ($rc) { N $rc.PowerOnHours });
       read_errors = $(if ($rc) { N $rc.ReadErrorsUncorrected }); read_fixed = $(if ($rc) { N $rc.ReadErrorsCorrected });
       write_errors = $(if ($rc) { N $rc.WriteErrorsUncorrected }); write_fixed = $(if ($rc) { N $rc.WriteErrorsCorrected });
       starts = $(if ($rc) { N $rc.StartStopCycleCount }); loads = $(if ($rc) { N $rc.LoadUnloadCycleCount });
       read_lat = $(if ($rc) { N $rc.ReadLatencyMax }); write_lat = $(if ($rc) { N $rc.WriteLatencyMax });
       smart = [bool]$rc } }) } catch {}
try { $r.dinfo = @(Get-Disk | ForEach-Object { @{ id = "$($_.Number)"; style = "$($_.PartitionStyle)"; boot = [bool]$_.IsBoot;
       system = [bool]$_.IsSystem; parts = $_.NumberOfPartitions; offline = [bool]$_.IsOffline;
       serial = ("" + $_.SerialNumber).Trim(); uid = "$($_.UniqueId)"; name = ("" + $_.FriendlyName).Trim(); size = [double]$_.Size } }) } catch {}
try { $r.parts = @(Get-Partition | Where-Object { $_.DriveLetter } | ForEach-Object { @{ disk = "$($_.DiskNumber)"; letter = "$($_.DriveLetter)" } }) } catch {}
try { $r.volumes = @(Get-Volume | Where-Object { $_.DriveLetter } | ForEach-Object { @{ letter = "$($_.DriveLetter)"; label = $_.FileSystemLabel;
       fs = "$($_.FileSystem)"; health = "$($_.HealthStatus)" } }) } catch {}
# Every drive letter, including ones not on a physical disk Windows lists (network shares, Storage
# Spaces, dynamic disks, some USB enclosures, VHDs). 2 = removable, 3 = local, 4 = network, 6 = RAM.
try { $r.logical = @(Get-CimInstance Win32_LogicalDisk | Where-Object { $_.DriveType -in 2,3,4,6 } | ForEach-Object {
       @{ letter = ("" + $_.DeviceID).TrimEnd(':'); type = [int]$_.DriveType; label = "$($_.VolumeName)"; fs = "$($_.FileSystem)";
          remote = "$($_.ProviderName)" } }) } catch {}
# Mapped network drives of the logged-in user: hidden from a server running as administrator.
try { $r.mapped = @(Get-ChildItem HKCU:\Network -ErrorAction Stop | ForEach-Object {
       @{ letter = $_.PSChildName.ToUpper(); remote = "" + (Get-ItemProperty $_.PSPath).RemotePath } }) } catch {}
$r | ConvertTo-Json -Compress -Depth 4
"""

DRIVE_IO_PS = r"""
$o = @(Get-CimInstance Win32_PerfRawData_PerfDisk_PhysicalDisk | Where-Object { $_.Name -ne '_Total' } | ForEach-Object {
    @{ name = "$($_.Name)"; read = [double]$_.DiskReadBytesPersec; write = [double]$_.DiskWriteBytesPersec;
       busy = [double]$_.PercentDiskTime; t = [double]$_.Timestamp_Sys100NS; f = [double]$_.Frequency_Sys100NS } })
ConvertTo-Json -Compress -InputObject $o
"""

_DRIVE_MEDIA = {"3": "HDD", "4": "SSD", "5": "SCM", "hdd": "HDD", "ssd": "SSD", "scm": "SCM"}
_DRIVE_BUS = {"7": "USB", "11": "SATA", "17": "NVMe", "10": "SAS", "8": "RAID", "1": "SCSI", "3": "ATA", "12": "SD",
              "usb": "USB", "sata": "SATA", "nvme": "NVMe", "sas": "SAS", "raid": "RAID", "scsi": "SCSI", "ata": "ATA"}
_DRIVE_HEALTH = {"0": "Healthy", "1": "Warning", "2": "Unhealthy", "5": "Unknown",
                 "healthy": "Healthy", "warning": "Warning", "unhealthy": "Unhealthy", "unknown": "Unknown"}

_drive_lock = threading.Lock()
_drive_state: dict = {"at": 0.0, "inv": None, "error": "", "history": None, "sab_dir": ("", 0.0)}


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


# Windows PowerShell writes piped output in the old console code page (cp850/437), so names like
# "Películas" came out garbled, and some characters broke the whole drive check. Ask for UTF-8.
PS_UTF8 = ("$u8 = New-Object System.Text.UTF8Encoding $false; "   # $false: no byte-order mark in front
           "[Console]::OutputEncoding = $u8; $OutputEncoding = $u8; ")


def run_powershell(script: str, timeout: float = 45) -> dict:
    """Runs a PowerShell script that prints one JSON object (Windows only, no window)."""
    import subprocess
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", PS_UTF8 + script],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=flags)
    out = (proc.stdout or "").lstrip("\ufeff").strip()
    if not out:
        raise UpstreamError((proc.stderr or "PowerShell returned nothing").strip()[:200])
    data = json.loads(out)
    return data if isinstance(data, dict) else {}


def lhm_drive_temps(port: int = LHM_PORT) -> dict[str, float]:
    """Drive temperatures from LibreHardwareMonitor's web server, keyed by lower-case drive name."""
    try:
        raw = http_fetch(f"http://127.0.0.1:{port}/data.json", timeout=2, max_bytes=4_000_000)
        tree = json.loads(raw)
    except Exception:
        return {}
    temps: dict[str, float] = {}

    def walk(node, hw_name=""):
        image = str(node.get("ImageURL") or "").lower()
        if any(k in image for k in ("hdd", "nvme", "ssd")) and node.get("Text"):
            hw_name = str(node["Text"]).strip().lower()
        for child in node.get("Children") or []:
            if hw_name and str(node.get("Text", "")).lower() == "temperatures":
                m = re.match(r"\s*([\d.,]+)", str(child.get("Value") or ""))
                if m and hw_name not in temps:
                    temps[hw_name] = to_float(m.group(1).replace(",", "."), 0.0)
            walk(child, hw_name)

    if isinstance(tree, dict):
        walk(tree)
    return {k: v for k, v in temps.items() if v > 0}


def _drive_inventory() -> dict:
    """Disks, partitions and volume labels. Windows: PowerShell; elsewhere: the root file system only."""
    if os.name == "nt":
        data = run_powershell(DRIVES_PS)
        return {"disks": _as_list(data.get("disks")), "parts": _as_list(data.get("parts")),
                "volumes": _as_list(data.get("volumes")), "dinfo": _as_list(data.get("dinfo")),
                "logical": _as_list(data.get("logical")), "mapped": _as_list(data.get("mapped"))}
    return {"disks": [{"id": "0", "name": "System disk", "media": "", "bus": "", "size": 0, "health": "Unknown"}],
            "parts": [{"disk": "0", "letter": "/"}], "volumes": []}


def drive_inventory(force: bool = False) -> dict:
    with _drive_lock:
        fresh = _drive_state["inv"] is not None and time.time() - _drive_state["at"] < DRIVE_CHECK_EVERY
        if fresh and not force:
            return _drive_state["inv"]
    try:
        inv = _drive_inventory()
        err = ""
    except Exception as exc:  # keep the last good answer when one check fails
        log.warning("Drive check failed: %s", exc)
        inv, err = None, f"Could not read drive health ({str(exc)[:120]})."
    with _drive_lock:
        if inv is not None:
            _drive_state.update(inv=inv, at=time.time(), error="")
        else:
            _drive_state["error"] = err
            _drive_state["at"] = time.time()  # don't retry on every request
        return _drive_state["inv"] or {"disks": [], "parts": [], "volumes": []}


def _load_drive_history() -> dict:
    if _drive_state["history"] is None:
        try:
            data = json.loads(DRIVE_HISTORY_FILE.read_text(encoding="utf-8"))
            _drive_state["history"] = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _drive_state["history"] = {}
    return _drive_state["history"]


def record_drive_temps(temps: dict[str, float], now: float | None = None) -> None:
    """Keeps 24 h of temperature samples per disk (at most one per 10 minutes)."""
    now = time.time() if now is None else now
    cutoff = now - DRIVE_HISTORY_HOURS * 3600
    with _drive_lock:
        hist = _load_drive_history()
        changed = False
        for key, temp in temps.items():
            pts = [p for p in hist.get(key, []) if isinstance(p, list) and len(p) == 2 and p[0] >= cutoff]
            if not pts or now - pts[-1][0] >= DRIVE_CHECK_EVERY - 30:
                pts.append([round(now), round(float(temp), 1)])
                changed = True
            hist[key] = pts
        for key in [k for k, v in hist.items() if not v]:
            del hist[key]
        if changed:
            try:
                tmp = DRIVE_HISTORY_FILE.with_name(DRIVE_HISTORY_FILE.name + ".tmp")
                tmp.write_text(json.dumps(hist), encoding="utf-8")
                replace_retry(tmp, DRIVE_HISTORY_FILE)
            except OSError as exc:
                log.warning("Could not save drive history: %s", exc)


def drive_temp_history(key: str) -> list:
    with _drive_lock:
        return list(_load_drive_history().get(key, []))


def _letter_of(path: str) -> str:
    path = str(path or "").strip()
    if os.name == "nt":
        return path[0].upper() if len(path) >= 2 and path[1] == ":" else ""
    return "/" if path.startswith("/") else ""


def sab_complete_dir() -> str:
    """SABnzbd's completed-downloads folder (cached for 10 minutes)."""
    cached, at = _drive_state["sab_dir"]
    if time.time() - at < DRIVE_CHECK_EVERY:
        return cached
    folder = ""
    try:
        misc = sab_request("get_config", {"section": "misc"}, timeout=8).get("config", {}).get("misc", {})
        folder = str(misc.get("complete_dir") or "")
    except (UpstreamError, ConfigError):
        pass
    _drive_state["sab_dir"] = (folder, time.time())
    return folder


def daily_download_bytes() -> float:
    """Average bytes SABnzbd finished per day over the last week (0 if unknown)."""
    try:
        days = sab_stats(7)["days"]
    except (UpstreamError, ConfigError, KeyError):
        return 0.0
    return sum(d["bytes"] for d in days) / 7.0


def drive_temp_limit(bus: str, setting: float = DRIVE_TEMP_ALERT) -> float:
    return setting + (DRIVE_NVME_TEMP_EXTRA if bus == "NVMe" else 0)


def _opt_int(v):
    return None if v in (None, "") else to_int(v)


def _drive_details(disk: dict, info: dict) -> dict:
    """Identity, layout and health counters for the Drives tab's detail view (None = Windows didn't say)."""
    serial = str(disk.get("serial") or "").strip()
    return {
        "id": str(disk.get("id", "")), "model": str(disk.get("model") or ""),
        # Only the end of the serial number: enough to tell drives apart without broadcasting it.
        "serial": ("…" + serial[-6:]) if len(serial) > 6 else serial,
        "firmware": str(disk.get("firmware") or ""), "status": str(disk.get("status") or ""),
        "sector": _opt_int(disk.get("sector")), "lsector": _opt_int(disk.get("lsector")),
        "style": str(info.get("style") or ""), "boot": bool(info.get("boot")), "system": bool(info.get("system")),
        "partitions": _opt_int(info.get("parts")), "offline": bool(info.get("offline")),
        "smart": bool(disk.get("smart")),
        "temp_max": _opt_int(disk.get("temp_max")),
        "read_errors": _opt_int(disk.get("read_errors")), "read_fixed": _opt_int(disk.get("read_fixed")),
        "write_errors": _opt_int(disk.get("write_errors")), "write_fixed": _opt_int(disk.get("write_fixed")),
        "starts": _opt_int(disk.get("starts")), "loads": _opt_int(disk.get("loads")),
        "read_lat": _opt_int(disk.get("read_lat")), "write_lat": _opt_int(disk.get("write_lat")),
    }


_io_state: dict = {"at": 0.0, "data": None, "prev": {}}
_io_lock = threading.Lock()  # one PowerShell sample at a time, however many phones are polling


def _io_rates(prev: dict, cur: dict) -> dict:
    """Speeds from two raw samples of the same disk (Windows' raw counters only count up)."""
    dt = cur["t"] - prev["t"]
    if dt <= 0:
        return {}
    secs = dt / (cur.get("f") or 1e7)
    return {"read": max(0.0, (cur["read"] - prev["read"]) / secs),
            "write": max(0.0, (cur["write"] - prev["write"]) / secs),
            # % Disk Time counts busy 100-ns ticks; can pass 100 with queued I/O, so cap it.
            "busy": max(0.0, min(100.0, (cur["busy"] - prev["busy"]) * 100 / dt))}


def drive_activity() -> dict:
    """Live read/write speed and busy % per physical disk, keyed by disk number (Windows; cached 3 s).

    Uses the raw counters and the difference from the previous sample: a fresh PowerShell asking
    for the "formatted" per-second counters mostly gets 0, because Windows needs two readings."""
    if os.name != "nt":
        return {}
    with _io_lock:
        if _io_state["data"] is not None and time.time() - _io_state["at"] < 3:
            return _io_state["data"]
        import subprocess
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_UTF8 + DRIVE_IO_PS],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15, creationflags=flags)
            rows = _as_list(json.loads((proc.stdout or "").lstrip("\ufeff").strip() or "[]"))
        except Exception as exc:
            log.warning("Drive activity check failed: %s", exc)
            rows = []
        cur, out = {}, {}
        for row in rows:
            num = str(row.get("name") or "").split(" ", 1)[0]  # e.g. "0 C:" -> "0"
            if num.isdigit():
                cur[num] = {k: to_float(row.get(k)) for k in ("read", "write", "busy", "t", "f")}
        prev = _io_state["prev"]
        for num, sample in cur.items():
            if num in prev and time.time() - prev[num]["at"] < 120:  # too old: a misleading average
                out[num] = _io_rates(prev[num], sample)
            sample["at"] = time.time()
        _io_state.update(at=time.time(), data=out, prev=cur)
        return out


def _norm_serial(v) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", str(v or "")).upper()


def _match_disk_numbers(disks: list, dinfo: list) -> dict:
    """Physical disk -> Windows disk number (what partitions and drive letters refer to).

    Get-PhysicalDisk's DeviceId is usually the disk number, but not always (after a drive is added or
    swapped, with some NVMe/RAID drivers), and then that drive's letters went missing. Match by serial
    number first, then by unique ID, then by name and size, and only then by DeviceId."""
    out: dict = {}
    free = list(dinfo)

    def take(disk, pred):
        hits = [x for x in free if pred(x)]
        if len(hits) == 1:
            out[id(disk)] = str(hits[0].get("id", ""))
            free.remove(hits[0])
            return True
        return False

    pending = list(disks)
    for rule in (
        lambda d: (lambda s: (lambda x: bool(s) and _norm_serial(x.get("serial")) == s))(_norm_serial(d.get("serial"))),
        lambda d: (lambda u: (lambda x: bool(u) and str(x.get("uid") or "") == u))(str(d.get("uid") or "")),
        lambda d: (lambda: (lambda x: bool(d.get("name")) and str(x.get("name") or "") == str(d.get("name"))
                            and abs(to_float(x.get("size")) - to_float(d.get("size"))) < 1e9))(),
        lambda d: (lambda i: (lambda x: str(x.get("id", "")) == i))(str(d.get("id", ""))),
    ):
        pending = [d for d in pending if not take(d, rule(d))]
    for d in pending:
        out[id(d)] = str(d.get("id", ""))
    return out


_LOGICAL_KIND = {2: "Removable", 3: "Local", 4: "Network", 6: "RAM disk"}


def _other_drive_letters(inv: dict, covered: set, vols: dict, low_gb: float, system: str,
                         media_letters: set, sab_letter: str) -> list[dict]:
    """Drive letters Windows has that aren't on a physical disk it listed: network shares, Storage
    Spaces / dynamic disks, some USB enclosures, VHDs. Free space only: no health counters for these."""
    import shutil
    found: dict[str, dict] = {}
    for x in inv.get("logical") or []:
        letter = str(x.get("letter") or "").upper()[:1]
        if letter and letter not in covered:
            found[letter] = {"type": to_int(x.get("type")), "label": str(x.get("label") or ""),
                             "fs": str(x.get("fs") or ""), "remote": str(x.get("remote") or "")}
    for x in inv.get("mapped") or []:  # mapped drives a server running as administrator can't see by letter
        letter = str(x.get("letter") or "").upper()[:1]
        if letter and letter not in covered and letter not in found and x.get("remote"):
            found[letter] = {"type": 4, "label": "", "fs": "", "remote": str(x["remote"])}
    out = []
    for letter in sorted(found):
        x = found[letter]
        kind = _LOGICAL_KIND.get(x["type"], "Drive")
        total = free = 0
        reach = ""
        for target in ([f"{letter}:\\"] + ([x["remote"]] if x["remote"] else [])):
            try:
                usage = shutil.disk_usage(target)
                total, free = usage.total, usage.free
                reach = target
                break
            except OSError:
                continue
        v = vols.get(letter, {})
        label = x["label"] or str(v.get("label") or "")
        problems = []
        if not reach:
            problems.append("Can't read this drive right now" + (" (network share offline, or it needs your sign-in)." if kind == "Network" else "."))
        elif total and free < low_gb * 1024 ** 3:
            problems.append(f"Only {format_bytes(free)} free.")
        uses = []
        if letter == system:
            uses.append("Windows and apps")
        if letter in media_letters:
            uses.append("Media library")
        if letter == sab_letter:
            uses.append("Finished downloads")
        name = x["remote"] or label or f"Drive {letter}:"
        out.append({
            "key": f"vol:{letter}", "letters": [letter], "name": name, "label": label or (uses[0] if uses else kind + " drive"),
            "media": kind, "bus": "", "size": total, "total": total, "free": free,
            "used_pct": round((total - free) * 100 / total) if total else None,
            "health": _DRIVE_HEALTH.get(str(v.get("health", "")).strip().lower(), "Unknown"),
            "status": ("warn" if problems else "ok"), "problems": problems,
            "temp": None, "temp_limit": DRIVE_TEMP_ALERT, "hours": None, "wear": None, "rpm": None,
            "uses": uses, "days_left": None, "daily_bytes": None,
            "volumes": [{"letter": letter, "label": label, "fs": x["fs"] or str(v.get("fs") or ""), "health": "",
                         "total": total, "free": free,
                         "used_pct": round((total - free) * 100 / total) if total else None}] if total else [],
            "details": {"id": "", "model": "", "serial": "", "firmware": "", "status": "", "sector": None, "lsector": None,
                        "style": "", "boot": False, "system": False, "partitions": None, "offline": False, "smart": False,
                        "temp_max": None, "read_errors": None, "read_fixed": None, "write_errors": None, "write_fixed": None,
                        "starts": None, "loads": None, "read_lat": None, "write_lat": None,
                        "kind": kind.lower(), "remote": x["remote"]},
        })
    return out


def drive_report(force: bool = False, with_rate: bool = True) -> dict:
    """Everything the Drive Health page shows, newest free space included."""
    import shutil
    inv = drive_inventory(force)
    lhm = lhm_drive_temps() if any(d.get("temp") in (None, "", 0) for d in inv["disks"]) else {}
    labels = {str(v.get("letter", "")).upper(): str(v.get("label") or "") for v in inv["volumes"]}
    vols = {str(v.get("letter", "")).upper(): v for v in inv["volumes"]}
    dinfo = {str(x.get("id", "")): x for x in inv.get("dinfo") or []}
    system = _letter_of(os.environ.get("SystemDrive", "C:") + "\\") if os.name == "nt" else "/"
    try:
        cfg_media = [str(x) for x in json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("media_folders", []) or []]
    except (OSError, ValueError, AttributeError):
        cfg_media = []
    media_letters = {_letter_of(p) for p in cfg_media} - {""}
    sab_letter = _letter_of(sab_complete_dir())
    rate = daily_download_bytes() if with_rate and sab_letter else 0.0
    low_gb = load_settings().get("drive_low_gb", DRIVE_LOW_GB)

    disk_numbers = _match_disk_numbers(inv["disks"], inv.get("dinfo") or [])
    drives, temps = [], {}
    for disk in inv["disks"]:
        disk_id = str(disk.get("id", ""))
        num = disk_numbers.get(id(disk), disk_id)  # its "disk number", which partitions refer to
        letters = sorted({str(p.get("letter", "")).upper() if os.name == "nt" else "/"
                          for p in inv["parts"] if str(p.get("disk")) == num} - {""})
        total = free = 0
        volumes = []
        for letter in letters:
            try:
                usage = shutil.disk_usage(f"{letter}:\\" if os.name == "nt" else letter)
                total += usage.total
                free += usage.free
                v = vols.get(letter, {})
                volumes.append({"letter": letter, "label": str(v.get("label") or ""), "fs": str(v.get("fs") or ""),
                                "health": _DRIVE_HEALTH.get(str(v.get("health", "")).strip().lower(), ""),
                                "total": usage.total, "free": usage.free,
                                "used_pct": round((usage.total - usage.free) * 100 / usage.total) if usage.total else None})
            except OSError:
                pass
        media = _DRIVE_MEDIA.get(str(disk.get("media", "")).strip().lower(), "")
        bus = _DRIVE_BUS.get(str(disk.get("bus", "")).strip().lower(), str(disk.get("bus") or ""))
        if bus == "NVMe":
            media = "SSD"
        name = str(disk.get("name") or "Drive")
        temp = to_float(disk.get("temp"), 0.0) or next(
            (t for k, t in lhm.items() if k and (k in name.lower() or name.lower() in k)), 0.0)
        hist_key = f"{disk_id}:{name}"
        if temp > 0:
            temps[hist_key] = temp
        limit = drive_temp_limit(bus)
        health = _DRIVE_HEALTH.get(str(disk.get("health", "")).strip().lower(), "Unknown")
        uses = []
        if system in letters:
            uses.append("Windows and apps" if os.name == "nt" else "System")
        if letters and media_letters & set(letters):
            uses.append("Media library")
        if sab_letter and sab_letter in letters:
            uses.append("Finished downloads")
        problems = []
        if health in ("Warning", "Unhealthy"):
            problems.append(("bad" if health == "Unhealthy" else "warn",
                             f"Windows reports this drive as {health.lower()}. Back up its data."))
        if temp and temp >= limit:
            problems.append(("bad", f"Too hot ({temp:.0f}°C, limit {limit:.0f}°C). Check airflow around it."))
        elif temp and temp >= limit - DRIVE_WARM_MARGIN:
            problems.append(("warn", f"Running warm ({temp:.0f}°C). Check airflow around it."))
        if total and free < low_gb * 1024 ** 3:
            problems.append(("warn", f"Only {format_bytes(free)} free."))
        status = "bad" if any(p[0] == "bad" for p in problems) else ("warn" if problems else
                                                                      ("ok" if health == "Healthy" else "unknown"))
        days_left = None
        if rate > 0 and sab_letter in letters and free:
            days_left = int(free / rate)
        drives.append({
            "key": hist_key, "letters": letters, "name": name,
            "label": " / ".join(labels.get(x, "") for x in letters if labels.get(x)) or (uses[0] if uses else ""),
            "media": media, "bus": bus, "size": to_float(disk.get("size")) or total,
            "total": total, "free": free, "used_pct": round((total - free) * 100 / total) if total else None,
            "health": health, "status": status, "problems": [p[1] for p in problems],
            "temp": round(temp) if temp else None, "temp_limit": limit,
            "hours": to_int(disk.get("hours")) or None,
            "wear": to_int(disk.get("wear")) if media == "SSD" and disk.get("wear") not in (None, "") else None,
            "rpm": to_int(disk.get("spindle")) if media == "HDD" and 1 < to_int(disk.get("spindle")) < 30000 else None,  # 4294967295 = unknown
            "uses": uses, "days_left": days_left, "daily_bytes": rate if days_left is not None else None,
            "volumes": volumes, "details": _drive_details(disk, dinfo.get(num, {})),
        })
    covered = {x for d in drives for x in d["letters"]}
    for extra in _other_drive_letters(inv, covered, vols, low_gb, system, media_letters, sab_letter):
        drives.append(extra)
    if temps:
        record_drive_temps(temps)
    order = {"bad": 0, "warn": 1, "ok": 2, "unknown": 3}
    drives.sort(key=lambda d: (order[d["status"]], d["letters"][0] if d["letters"] else "~"))
    for d in drives:
        d["history"] = drive_temp_history(d["key"])
    with _drive_lock:
        checked, error = _drive_state["at"], _drive_state["error"]
    return {
        "drives": drives, "checked": checked or None, "problem": error,  # not "error": the page treats that as a failed request
        "temps_available": any(d["temp"] for d in drives),
        "totals": {"free": sum(d["free"] for d in drives), "total": sum(d["total"] for d in drives),
                   "ok": sum(d["status"] == "ok" for d in drives),
                   "warn": sum(d["status"] in ("warn", "bad") for d in drives)},
    }


def drive_alerts(report: dict, settings: dict, sent: dict, now: float) -> list[tuple[str, str, int]]:
    """Alerts due for this report: (title, message, priority). `sent` remembers when each was last sent."""
    out = []
    low = settings.get("drive_low_gb", DRIVE_LOW_GB) * 1024 ** 3
    for d in report["drives"]:
        name = (", ".join(f"{x}:" for x in d["letters"] if x != "/") + " " if d["letters"] else "") + d["name"]
        checks = [
            ("health", settings.get("notify_drive_health"), d["health"] in ("Warning", "Unhealthy"),
             f"Drive problem: {name}", f"Windows reports {name} as {d['health'].lower()}. Back up its data soon.", 5),
            ("hot", settings.get("notify_drive_hot"), bool(d["temp"]) and d["temp"] >= d["temp_limit"],
             f"Drive too hot: {name}", f"{name} is at {d['temp']}°C (limit {d['temp_limit']:.0f}°C).", 4),
            ("low", settings.get("notify_drive_low"), bool(d["total"]) and d["free"] < low,
             f"Drive almost full: {name}", f"{name} has only {format_bytes(d['free'])} free.", 3),
        ]
        for kind, enabled, bad, title, message, prio in checks:
            key = f"{kind}:{d['key']}"
            if not bad:
                sent.pop(key, None)  # fixed: alert again if it comes back
                continue
            if enabled and now - sent.get(key, 0) >= DRIVE_ALERT_REPEAT:
                sent[key] = now
                out.append((title, message, prio))
    return out


AUTOSTART_NAME = "NZB Mobile Server.vbs"


def autostart_path() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if os.name != "nt" or not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / AUTOSTART_NAME


STARTUP_TASK = "NZB Mobile Server"  # created by setup_startup.ps1: starts this server as administrator


def startup_task_exists() -> bool:
    """True if the Task Scheduler task from setup_startup.ps1 is there."""
    if os.name != "nt":
        return False
    import subprocess
    try:
        return subprocess.run(["schtasks", "/Query", "/TN", STARTUP_TASK], capture_output=True, timeout=10,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def autostart_enabled() -> bool | None:
    path = autostart_path()
    if path is None:
        return None
    return path.exists() or startup_task_exists()


def install_autostart(extra_args: list[str]) -> Path:
    """Adds a hidden launcher to the user's Startup folder (no admin rights or system changes needed)."""
    path = autostart_path()
    if path is None:
        raise ConfigError("Starting automatically is only available on Windows.")
    if startup_task_exists():  # a second launcher would start a second server at login
        raise ConfigError(f"It already starts at login as administrator (Task Scheduler task \"{STARTUP_TASK}\"). "
                          "Nothing to do. To change that, run setup_startup.ps1 again or delete the task.")
    if getattr(sys, "frozen", False):
        cmd = [sys.executable]
    else:
        exe = Path(sys.executable)
        windowless = exe.with_name("pythonw.exe")
        cmd = [str(windowless if windowless.exists() else exe), str(Path(__file__).resolve())]
    cmd += extra_args
    command = " ".join('"' + str(part).replace('"', "") + '"' for part in cmd).replace('"', '""')
    folder = str(BASE_DIR).replace('"', '""')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "' Starts NZB Command Center's phone server without a window. Delete this file to stop that.\r\n"
        'Set sh = CreateObject("WScript.Shell")\r\n'
        f'sh.CurrentDirectory = "{folder}"\r\n'
        f'sh.Run "{command}", 0, False\r\n',
        encoding="utf-16")
    return path


def remove_autostart() -> bool:
    path = autostart_path()
    if path and path.exists():
        path.unlink()
        return True
    return False


# ==============================================================================
# 6. NETWORK GATE, RATE LIMITS, AUTH LOCKOUT
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


class SlidingWindowLimiter:
    def __init__(self, max_events: int, window: float):
        self.max_events, self.window = max_events, window
        self._events: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, cost: int = 1) -> bool:
        """Records `cost` events for `key` if they fit in the window (all or nothing)."""
        now = time.monotonic()
        cost = max(1, int(cost))
        with self._lock:
            if len(self._events) > 1000:
                self._events = {k: q for k, q in self._events.items() if q and now - q[-1] <= self.window}
            q = self._events.setdefault(key, deque())
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) + cost > self.max_events:
                return False
            q.extend([now] * cost)
            return True


class FailureTracker:
    """Temporary lockout for clients that keep sending a wrong access code."""

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
            if len(q) >= self.max_failures:
                return max(1, int(self.window - (now - q[0])))
            return 0

    def record(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            # Keep memory bounded: forget clients whose failures have all aged out of the window.
            if len(self._fails) > 1000:
                self._fails = {k: q for k, q in self._fails.items() if q and now - q[-1] <= self.window}
            q = self._fails.setdefault(key, deque())
            q.append(now)
            while len(q) > self.max_failures * 2:  # older entries can't affect the lockout
                q.popleft()

    def clear(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


AUTH_FAILURES = FailureTracker(AUTH_MAX_FAILURES, AUTH_WINDOW)
SEARCH_LIMITER = SlidingWindowLimiter(*SEARCH_RATE)
ADD_LIMITER = SlidingWindowLimiter(*ADD_RATE)

NZB_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{4,64}$")
CATEGORY_RE = re.compile(r"^\d{1,6}(,\d{1,6}){0,9}$")
CAPS_CACHE_FILE = BASE_DIR / "nzb_caps_cache.json"

# Standard Newznab categories: used for "All indexers" (these IDs work everywhere) and for any
# indexer whose capabilities the desktop app hasn't cached yet.
STANDARD_CATEGORIES: list[tuple[str, list[tuple[str, str]]]] = [
    ("1000", [("Console", ""), ("NDS", "1010"), ("PSP", "1020"), ("Wii", "1030"), ("Xbox", "1040"), ("Xbox 360", "1050"), ("PS3", "1080"), ("Other", "1090")]),
    ("2000", [("Movies", ""), ("Foreign", "2010"), ("Other", "2020"), ("SD", "2030"), ("HD", "2040"), ("UHD", "2045"), ("BluRay", "2050"), ("3D", "2060"), ("DVD", "2070")]),
    ("3000", [("Audio", ""), ("MP3", "3010"), ("Video", "3020"), ("Audiobook", "3030"), ("Lossless", "3040"), ("Other", "3050")]),
    ("4000", [("PC", ""), ("0day", "4010"), ("ISO", "4020"), ("Mac", "4030"), ("Mobile", "4040"), ("Games", "4050")]),
    ("5000", [("TV", ""), ("Foreign", "5020"), ("SD", "5030"), ("HD", "5040"), ("UHD", "5045"), ("Other", "5050"), ("Sport", "5060"), ("Anime", "5070"), ("Documentary", "5080")]),
    ("6000", [("XXX", "")]),
    ("7000", [("Books", ""), ("Ebook", "7020"), ("Comics", "7030"), ("Magazines", "7010"), ("Other", "7040")]),
    ("8000", [("Other", "")]),
]


def _standard_category_groups() -> list[dict]:
    groups = []
    for parent_id, entries in STANDARD_CATEGORIES:
        name = entries[0][0]
        groups.append({"id": parent_id, "name": name,
                       "children": [{"id": cid, "name": n} for n, cid in entries[1:]]})
    return groups


def _groups_from_caps(pairs: list) -> list[dict]:
    """Turns the desktop app's cached caps ([label, id] with "  ↳ Parent > Child (id)" children) into groups."""
    groups: list[dict] = []
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        label, cid = str(pair[0]), str(pair[1]).strip()
        if not cid.isdigit():
            continue
        name = re.sub(r"\s*\(\d+\)\s*$", "", label.replace("\u21b3", "").strip())
        is_child = label.startswith((" ", "\u21b3")) or "\u21b3" in label
        if is_child and groups:
            groups[-1]["children"].append({"id": cid, "name": name.split(">", 1)[-1].strip()})
        else:
            groups.append({"id": cid, "name": name, "children": []})
    return groups


def _site_key(url: str) -> str:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    parts = [x for x in host.split(".") if x not in ("www", "api")]
    return parts[-2] if len(parts) >= 2 else host


def indexer_category_groups(indexers: list[dict]) -> dict[str, list[dict]]:
    """Category drop-down contents per indexer id (plus "*" for all indexers)."""
    cached: dict = {}
    try:
        cached = json.loads(CAPS_CACHE_FILE.read_text(encoding="utf-8")).get("by_url") or {}
    except (OSError, ValueError, AttributeError):
        cached = {}
    by_site = {_site_key(u): v for u, v in cached.items() if isinstance(v, list)}
    out: dict[str, list[dict]] = {"*": _standard_category_groups()}
    for ix in indexers:
        pairs = cached.get(ix["url"]) or by_site.get(_site_key(ix["url"]))
        groups = _groups_from_caps(pairs) if isinstance(pairs, list) else []
        out[ix["id"]] = groups or out["*"]
    return out


def get_local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


# ==============================================================================
# 7. WEB PAGE
# ==============================================================================

# The all-in-one HAL 9000 app (Home, Get, Watch, System) at /app, in app_page.py next to this file.
try:
    from app_page import APP_PAGE
except ImportError:
    MISSING_PARTS.append("app_page.py")
    APP_PAGE = ("<!doctype html><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                "<body style=\"background:#050506;color:#F4F4F5;font:16px system-ui;padding:40px 24px;text-align:center\">"
                "<h2>HAL 9000</h2><p>The app's page (app_page.py) is missing from the PC's NZB Command Center folder. "
                "Run update_all.ps1 there to get it back.</p></body>")
APP_MANIFEST = json.dumps({
    "id": "/app/", "name": "HAL 9000", "short_name": "HAL 9000", "start_url": "/app/", "scope": "/app/", "display": "standalone",
    "background_color": "#050506", "theme_color": "#050506",
    "icons": [{"src": "/app/icon.png", "sizes": "180x180", "type": "image/png", "purpose": "any"},
              {"src": "/app/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
}).encode("utf-8")


def app_icon(size: int) -> bytes:
    """HAL's eye (drawn by hal_server) for the all-in-one app; the NZB icon if HAL isn't here."""
    if _hal_mod is not None:
        try:
            return _hal_mod.icon(size)
        except Exception:  # noqa: BLE001
            pass
    return ICON_PNG


# The page itself is in mobile_page.py (next to this file). Without that file the server still runs and
# shows how to get it back, instead of failing to start.
try:
    from mobile_page import PAGE
except ImportError:
    MISSING_PARTS.append("mobile_page.py")
    PAGE = ("<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>NZB Command Center</title><meta name=\"apple-mobile-web-app-title\" content=\"NZB Center\">"
            "<link rel=\"manifest\" href=\"/manifest.webmanifest\"><link rel=\"apple-touch-icon\" href=\"/icon.png\"></head>"
            "<body style=\"background:#060D10;color:#F0FDFA;font:16px system-ui;padding:40px 24px;text-align:center\">"
            "<h2>NZB Command Center v__VERSION__</h2><p>The phone app's page (mobile_page.py) is missing from the PC's NZB Command "
            "Center folder. Run update_all.ps1 there to get it back.</p><script>var activeTab='home';</script></body></html>")


# ==============================================================================
# 8. HTTP HANDLER
# ==============================================================================

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' blob:; media-src 'self' blob:; manifest-src 'self'; "
        "connect-src 'self'; frame-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
}


# Lets Android "Add to Home screen" / "Install app" open the page full-screen like an app.
KODI_ICONS = [{"src": "/kodi-icon.png", "sizes": "180x180", "type": "image/png", "purpose": "any"},
              {"src": "/kodi-icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}]
MANIFEST = json.dumps({
    "id": "/", "name": "NZB Command Center", "short_name": "NZB Center", "start_url": "/", "scope": "/",
    "display": "standalone", "background_color": "#060D10", "theme_color": "#060D10",
    "icons": [{"src": "/icon.png", "sizes": "180x180", "type": "image/png", "purpose": "any"}],
    # Long-press the app icon on Android to jump straight to Kodi.
    "shortcuts": [{"name": "Kodi", "url": "/kodi", "icons": KODI_ICONS}],
}).encode("utf-8")

# The Kodi icon is its own home-screen app: a separate id and start_url, so Android installs it next to
# the main one. It shares the origin, so the pairing code saved by the main app works here too.
KODI_MANIFEST = json.dumps({
    "id": "/kodi", "name": "Kodi Remote", "short_name": "Kodi", "start_url": "/kodi", "scope": "/",
    "display": "standalone", "background_color": "#060D10", "theme_color": "#060D10", "icons": KODI_ICONS,
}).encode("utf-8")


def _kodi_page(page: str) -> str:
    """The same app, labelled and iconed for Kodi and opening on the Kodi tab."""
    swaps = [
        ('<title>NZB Command Center</title>', '<title>Kodi</title>'),
        ('content="NZB Center">', 'content="Kodi">'),
        ('href="/manifest.webmanifest"', 'href="/kodi.webmanifest"'),
        ('<link rel="apple-touch-icon" href="/icon.png">', '<link rel="apple-touch-icon" href="/kodi-icon.png">'),
        ("activeTab='home'", "activeTab='kodi'"),
    ]
    for old, new in swaps:
        if page.count(old) != 1:
            raise RuntimeError(f"Kodi page: expected one {old!r} in PAGE")
        page = page.replace(old, new)
    return page


KODI_PAGE = _kodi_page(PAGE)


class MobileHandler(*((_hal_mod.HalRoutes,) if _hal_mod else ()), BaseHTTPRequestHandler):
    server_version = f"NZBMobile/{VERSION}"
    sys_version = ""
    timeout = CLIENT_SOCKET_TIMEOUT

    def log_message(self, fmt, *args):  # request lines only at DEBUG; actions are logged explicitly
        log.debug("%s %s", self.client_address[0], fmt % args)

    # ---- low-level responses ------------------------------------------------
    def _send(self, status: int, raw: bytes, ctype: str, extra: dict | None = None) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            for key, value in {**SECURITY_HEADERS, **(extra or {})}.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass  # phone went away mid-response

    def send_json(self, status: int, data: dict, extra: dict | None = None) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", extra)

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("Invalid request size.")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object.")
        return value

    # ---- gatekeeping ----------------------------------------------------------
    @property
    def ip(self) -> str:
        return self.client_address[0]

    def authorized(self) -> bool:
        supplied = (self.headers.get("X-Mobile-Token") or "").encode("utf-8", "replace")
        return secrets.compare_digest(supplied, _token_bytes)  # bytes: non-ASCII input can't raise

    def _network_ok(self) -> bool:
        if is_client_allowed(self.ip):
            return True
        log.warning("Blocked request from non-local address %s", self.ip)
        self.send_json(403, {"error": "This server only accepts connections from your local network."})
        return False

    def _auth_ok(self) -> bool:
        wait = AUTH_FAILURES.blocked_for(self.ip)
        if wait:
            self.send_json(429, {"error": f"Too many wrong access codes. Try again in {wait} s."}, {"Retry-After": str(wait)})
            return False
        if not self.authorized():
            if self.headers.get("X-Mobile-Token"):  # a missing code isn't an attack; a wrong one is
                AUTH_FAILURES.record(self.ip)
                log.warning("Wrong access code from %s", self.ip)
            self.send_json(401, {"error": "Enter the mobile access code shown on the PC."})
            return False
        AUTH_FAILURES.clear(self.ip)
        return True

    def _limited(self, limiter: SlidingWindowLimiter, what: str, cost: int = 1) -> bool:
        if limiter.allow(self.ip, cost):
            return False
        self.send_json(429, {"error": f"Too many {what} requests — slow down a little."}, {"Retry-After": "30"})
        return True

    def _guarded(self, fn) -> None:
        try:
            fn()
        except UpstreamError as exc:
            self.send_json(502, {"error": str(exc)})
        except ConfigError as exc:
            self.send_json(503, {"error": str(exc)})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": redact(exc)})
        except Exception:
            log.exception("Unhandled error serving %s %s", self.command, self.path)
            self.send_json(500, {"error": "Internal error — see mobile_server.log on the PC."})

    # ---- GET ------------------------------------------------------------------
    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if not self._network_ok():
            return
        if path == "/hal" or path.startswith("/hal/"):
            self._hal_route("GET", path)
            return
        if path == "/app":
            self._send(302, b"", "text/plain", {"Location": "/app/"})
            return
        if path in ("/app/", "/app/index.html"):
            self._send(200, APP_PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/app/manifest.webmanifest":
            self._send(200, APP_MANIFEST, "application/manifest+json", {"Cache-Control": "public, max-age=86400"})
            return
        if path in ("/app/icon.png", "/app/icon-512.png"):
            self._send(200, app_icon(512 if path.endswith("512.png") else 180), "image/png", {"Cache-Control": "public, max-age=86400"})
            return
        if path in ("/", "/index.html"):
            self._send(200, PAGE.replace("__VERSION__", VERSION).encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/manifest.webmanifest":
            self._send(200, MANIFEST, "application/manifest+json", {"Cache-Control": "public, max-age=86400"})
            return
        if path in ("/icon.png", "/favicon.ico", "/apple-touch-icon.png"):
            self._send(200, ICON_PNG, "image/png", {"Cache-Control": "public, max-age=86400"})
            return
        if path in ("/kodi", "/kodi/"):
            self._send(200, KODI_PAGE.replace("__VERSION__", VERSION).encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/kodi.webmanifest":
            self._send(200, KODI_MANIFEST, "application/manifest+json", {"Cache-Control": "public, max-age=86400"})
            return
        if path in ("/kodi-icon.png", "/kodi-icon-512.png"):
            png = KODI_ICON_PNG if path == "/kodi-icon.png" else KODI_ICON_MASKABLE_PNG
            self._send(200, png, "image/png", {"Cache-Control": "public, max-age=86400"})
            return
        if path.startswith("/watch/"):
            self._watch_relay(path)
            return
        if not path.startswith("/api/"):
            self.send_json(404, {"error": "Not found."})
            return
        if not self._auth_ok():
            return
        self._guarded(lambda: self._route_get(path))

    def _watch_relay(self, path: str) -> None:
        """/watch/<session>/<rest>: Jellyfin's /Videos/<item>/<rest>, with the key added here and taken out of playlists."""
        parts = path.split("/", 3)
        sid, rest = (parts[2], parts[3]) if len(parts) == 4 else ("", "")
        with _watch_lock:
            sess = _watch_sessions.get(sid) if WATCH_SID_RE.fullmatch(sid) else None
            if sess and time.time() - sess["at"] > WATCH_IDLE_SECONDS:
                _watch_sessions.pop(sid, None)
                sess = None
            if sess:
                sess["at"] = time.time()
        if not sess or not WATCH_PATH_RE.fullmatch(rest) or ".." in rest:
            self.send_json(404, {"error": "That stream has ended. Start it again from the movie page."})
            return
        if not client_at_home(self.ip):
            self.send_json(403, {"error": "Watching on the phone only works on your home Wi-Fi."})
            return
        c = jellyfin_conf()
        if not (c["url"] and c["key"]):
            self.send_json(503, {"error": "Jellyfin isn't connected."})
            return
        if rest == "master.m3u8":
            query = urllib.parse.urlencode(sess["params"])
        else:
            query = urllib.parse.urlsplit(self.path).query
            query = "&".join(x for x in query.split("&") if x and not x.lower().startswith(("api_key=", "apikey=")))
        url = f"{c['url']}/Videos/{sess['jf_id']}/{rest}" + (f"?{query}" if query else "")
        headers = {"Authorization": jf_auth_header(c["key"]), "X-Emby-Token": c["key"], "User-Agent": USER_AGENT}
        if self.headers.get("Range"):
            headers["Range"] = self.headers["Range"][:100]
        try:
            resp = _OPENER.open(urllib.request.Request(url, headers=headers), timeout=90)
        except urllib.error.HTTPError as exc:
            self.send_json(exc.code if 400 <= exc.code < 600 else 502, {"error": f"Jellyfin answered HTTP {exc.code}."})
            return
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError):
            self.send_json(502, {"error": "Can't reach Jellyfin."})
            return
        with resp:
            ctype = resp.headers.get("Content-Type") or "application/octet-stream"
            if rest.endswith(".m3u8"):
                text = resp.read(WATCH_MAX_PLAYLIST).decode("utf-8", errors="replace")
                self._send(200, _strip_jf_keys(text, sess["jf_id"], sid).encode("utf-8"), "application/vnd.apple.mpegurl")
                return
            try:
                self.send_response(resp.status)
                self.send_header("Content-Type", ctype)
                for h in ("Content-Length", "Content-Range", "Accept-Ranges"):
                    if resp.headers.get(h):
                        self.send_header(h, resp.headers[h])
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                while True:
                    chunk = resp.read(WATCH_CHUNK)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                pass   # the phone moved on (seeked, paused, closed)

    def _route_get(self, path: str) -> None:
        if path == "/api/status":
            cfg = get_config()
            self.send_json(200, {
                "ok": True, "version": VERSION,
                "indexers": [{"id": x["id"], "name": x["name"]} for x in cfg["indexers"]],
                "categories": indexer_category_groups(cfg["indexers"]),
                "priorities": SAB_PRIORITIES,
            })
        elif path == "/api/sab/info":
            self.send_json(200, {"categories": sab_categories()})
        elif path == "/api/home":
            self.send_json(200, self._home())
        elif path == "/api/kodi":
            self.send_json(200, self._kodi_overview())
        elif path == "/api/tv":
            self.send_json(200, self._tv_view(with_power=True))
        elif path == "/api/tv/job":
            self.send_json(200, {"job": tv_job_view()})
        elif path == "/api/kodi/now":
            playing = kodi_now_playing() if kodi_configured() else None
            self.send_json(200, {"playing": playing, "volume": kodi_volume() if kodi_configured() else None})
        elif path == "/api/kodi/shows":
            self.send_json(200, {"shows": kodi_all_shows()})
        elif path == "/api/kodi/show":
            show_id = to_int(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("id", [""])[0])
            if show_id <= 0:
                raise ValueError("Invalid show.")
            self.send_json(200, kodi_show_details(show_id))
        elif path == "/api/kodi/missing":
            self.send_json(200, {"missing": kodi_missing_episodes()})
        elif path == "/api/stats":
            self.send_json(200, sab_stats())
        elif path == "/api/library/summary":
            self.send_json(200, library_summary())
        elif path == "/api/drives":
            self.send_json(200, self._drives_view())
        elif path == "/api/drives/io":
            self.send_json(200, {"io": drive_activity()})
        elif path.startswith("/api/arr"):
            self._get_arr(path)
        elif path.startswith("/api/jellyfin"):
            self._get_jellyfin(path)
        elif path == "/api/watch/item":
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            kind, item_id = _watch_parse({"kind": qs.get("kind", [""])[0], "id": qs.get("id", [""])[0]})
            self.send_json(200, watch_item(kind, item_id, self.ip, str(self.headers.get("Host") or "")[:200]))
        elif path == "/api/saved":
            self.send_json(200, self._saved_view())
        elif path == "/api/settings":
            self.send_json(200, self._settings_view())
        elif path == "/api/connections":
            self.send_json(200, connections_view())
        elif path == "/api/stream/status":
            job = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("job", [""])[0]
            self.send_json(200, stream_status(job))
        elif path == "/api/streams":
            self.send_json(200, streams_view())
        elif path == "/api/kodi/art":
            ref = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("u", [""])[0]
            raw, ctype = kodi_art(ref)
            self._send(200, raw, ctype, {"Cache-Control": "private, max-age=604800"})
        elif path == "/api/search/art":
            # The phone only ever names a search result; the server decides what (if anything) to download.
            result_id = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("id", [""])[0]
            item = get_cached_result(result_id) if NZB_ID_RE.match(result_id) else None
            art = release_artwork(item) if item else None
            if not art:
                self.send_json(404, {"error": "No artwork for this release."})
                return
            self._send(200, art[0], art[1], {"Cache-Control": "private, max-age=86400"})
        elif path == "/api/kodi/movie":
            movieid = to_int(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("id", [""])[0])
            if movieid <= 0:
                raise ValueError("Invalid movie.")
            self.send_json(200, kodi_movie_details(movieid))
        elif path == "/api/kodi/movies":
            self.send_json(200, {"movies": [_kodi_movie(x) for x in kodi_all_movies()]})
        elif path == "/api/queue":
            q = sab_request("queue", {"limit": 100}).get("queue", {})
            kb = to_float(q.get("kbpersec"), -1.0)
            speed = f"{format_bytes(kb * 1024)}/s" if kb >= 0 else f"{q.get('speed', '0')}B/s"
            free = q.get("diskspace2") or q.get("diskspace1")
            limit_pct, limit_abs = str(q.get("speedlimit") or ""), str(q.get("speedlimit_abs") or "")
            slots = q.get("slots", [])
            self.send_json(200, {
                "state": "Paused" if q.get("paused") else q.get("status", "SABnzbd"),
                "speed": speed,
                "total": to_int(q.get("noofslots_total"), len(slots)),
                "disk_free_gb": to_float(free, -1.0) if free not in (None, "") else None,
                "speed_limit": (f"{limit_pct}% ({limit_abs} KB/s)" if limit_pct not in ("", "0", "100") and limit_abs not in ("", "0")
                                else (f"{limit_pct}%" if limit_pct not in ("", "0", "100") else "")),
                "slots": [{
                    "id": str(x.get("nzo_id", "")), "name": x.get("filename", "Unnamed"), "status": x.get("status", ""),
                    "percentage": to_float(x.get("percentage")), "size": x.get("size", ""), "sizeleft": x.get("sizeleft", ""),
                    "eta": x.get("timeleft", ""), "cat": x.get("cat", ""), "priority": str(x.get("priority") or ""),
                } for x in slots],
            })
        elif path == "/api/tv/recent":
            self.send_json(200, tv_row())
        elif path == "/api/tv/watched":
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            self.send_json(200, tv_watched_row(fresh=q.get("fresh", [""])[0] == "1"))
        elif path == "/api/tv/finished":
            self.send_json(200, tv_finished())
        elif path == "/api/tv/photos":
            self.send_json(200, tv_photos_list())
        elif path == "/api/tv/photo":
            raw = tv_photo(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("id", [""])[0])
            if not raw:
                self.send_json(404, {"error": "No such photo."})
                return
            self._send(200, raw, _image_type(raw) or "image/jpeg", {"Cache-Control": "private, max-age=86400"})
        elif path == "/api/tv/poster":
            raw = tv_poster(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("id", [""])[0])
            if not raw:
                self.send_json(404, {"error": "No poster for this movie."})
                return
            self._send(200, raw, _image_type(raw) or "image/jpeg", {"Cache-Control": "private, max-age=604800"})
        elif path == "/api/tv/perf":
            self.send_json(200, tv_perf_report())
        elif path == "/api/tv/backdrop":
            raw = tv_backdrop(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("id", [""])[0])
            if not raw:
                self.send_json(404, {"error": "No background art for this item."})
                return
            self._send(200, raw, _image_type(raw) or "image/jpeg", {"Cache-Control": "private, max-age=604800"})
        elif path == "/api/history":
            h = sab_request("history", {"limit": 25}).get("history", {})
            self.send_json(200, {"slots": [{
                "id": str(x.get("nzo_id", "")), "name": x.get("name", "Unnamed"), "status": x.get("status", ""),
                "size": x.get("size", ""), "cat": x.get("category", ""),
                "completed": to_int(x.get("completed")) or None, "fail_message": str(x.get("fail_message") or "")[:160],
            } for x in h.get("slots", [])]})
        else:
            self.send_json(404, {"error": "Not found."})

    # ---- POST -----------------------------------------------------------------
    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if not self._network_ok():
            return
        if path.startswith("/hal/"):
            self._hal_route("POST", path)   # HAL checks the access code itself (and lets /hal/api/pair in with a PIN)
            return
        if not self._auth_ok():
            return
        self._guarded(lambda: self._route_post(path))

    def _hal_route(self, method: str, path: str) -> None:
        """HAL 9000's pages and API under /hal (hal_server.HalRoutes)."""
        if path == "/hal":
            self._send(302, b"", "text/plain", {"Location": "/hal/"})
            return
        if not _hal_state["running"]:
            msg = f"HAL 9000 isn't running in the phone server: {_hal_state['note']}."
            if method == "GET" and not path.startswith("/hal/api/"):
                body = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                        '<body style="background:#050506;color:#F4F4F5;font:16px system-ui;padding:40px 24px;text-align:center">'
                        f'<h2>HAL 9000</h2><p style="color:#A1A1AA">{html.escape(msg)}</p></body>')
                self._send(503, body.encode("utf-8"), "text/html; charset=utf-8")
            else:
                self.send_json(503, {"error": msg})
            return
        self.hal_base = "/hal"
        sub = path[len("/hal"):] or "/"
        if method == "GET":
            self.hal_get(sub, urllib.parse.urlsplit(self.path).query)
        else:
            self.hal_post(sub)

    def _route_post(self, path: str) -> None:
        body = self.read_json()
        if path == "/api/search":
            self._post_search(body)
        elif path == "/api/add":
            self._post_add(body)
        elif path == "/api/stream":
            if self._limited(ADD_LIMITER, "add"):
                return
            job = stream_start(str(body.get("id") or ""))
            log.info("stream start %r ip=%s", job.get("release"), self.ip)
            self.send_json(200, job)
        elif path == "/api/stream/cancel":
            self.send_json(200, stream_cancel(str(body.get("job") or "")))
        elif path == "/api/stream/tv":
            result = stream_to_tv(str(body.get("stream") or ""))
            log.info("stream to tv %r (tv started: %s) ip=%s", result.get("title"), result.get("started_tv"), self.ip)
            self.send_json(200, result)
        elif path == "/api/stream/phone":
            self.send_json(200, stream_phone_url(str(body.get("stream") or ""), str(self.headers.get("Host") or "")[:200]))
        elif path == "/api/add-batch":
            self._post_add_batch(body)
        elif path in ("/api/queue/pause", "/api/queue/resume"):
            action = path.rsplit("/", 1)[1]
            sab_request(action)
            log.info("queue %s ip=%s", action, self.ip)
            self.send_json(200, {"ok": True})
        elif path == "/api/queue/item":
            self._post_queue_item(body)
        elif path == "/api/history/retry":
            nzo_id = str(body.get("id") or "")
            if not NZB_ID_RE.match(nzo_id):
                raise ValueError("Invalid item id.")
            sab_request("retry", {"value": nzo_id})
            log.info("retry %s ip=%s", nzo_id, self.ip)
            self.send_json(200, {"ok": True})
        elif path == "/api/history/delete":
            nzo_id = str(body.get("id") or "")
            if not NZB_ID_RE.match(nzo_id):
                raise ValueError("Invalid item id.")
            sab_request("history", {"name": "delete", "value": nzo_id})  # removes the entry; downloaded files stay
            log.info("history delete %s ip=%s", nzo_id, self.ip)
            self.send_json(200, {"ok": True})
        elif path == "/api/watch/start":
            self.send_json(200, watch_start(body, self.ip))
        elif path == "/api/watch/stop":
            watch_stop(str(body.get("sid") or ""))
            self.send_json(200, {"ok": True})
        elif path == "/api/watch/progress":
            self.send_json(200, watch_progress(body))
        elif path == "/api/watch/tv":
            self.send_json(200, watch_to_tv(body))
            log.info("watch moved to TV ip=%s", self.ip)
        elif path == "/api/kodi/play":
            movieid, episodeid = to_int(body.get("movieid")), to_int(body.get("episodeid"))
            if (movieid > 0) == (episodeid > 0):
                raise ValueError("Invalid movie or episode.")
            item = {"movieid": movieid} if movieid > 0 else {"episodeid": episodeid}
            kodi_call("Player.Open", {"item": item, "options": {"resume": bool(body.get("resume"))}})
            log.info("kodi play %s resume=%s ip=%s", item, bool(body.get("resume")), self.ip)
            self.send_json(200, {"ok": True})
        elif path == "/api/tv/perf":
            tv_perf_add(body)
            self.send_json(200, {"ok": True})
        elif path == "/api/tv/play":
            result = tv_play(str(body.get("id") or ""), from_start=bool(body.get("from_start")))
            log.info("tv play %s ip=%s", result.get("played"), self.ip)
            self.send_json(200, result)
        elif path == "/api/home/play":
            result = home_play(str(body.get("id") or ""))
            log.info("home play %s (tv started: %s) ip=%s", result.get("played"), result.get("started_tv"), self.ip)
            self.send_json(200, result)
        elif path == "/api/tv/mark_watched":
            self.send_json(200, tv_mark_watched(str(body.get("id") or "")))
        elif path == "/api/voice/play":
            self.send_json(200, voice_play(str(body.get("q") or "")[:200], from_start=bool(body.get("from_start")), tv_on=bool(body.get("tv_on"))))
        elif path == "/api/voice/control":
            self.send_json(200, voice_control(str(body.get("action") or "")))
        elif path == "/api/kodi/control":
            kodi_control(str(body.get("action") or ""), to_int(body.get("playerid"), -1), body.get("value"))
            self.send_json(200, {"ok": True})
        elif path == "/api/kodi/text":
            res = kodi_send_text(body.get("text", ""), done=body.get("done", True) is not False)
            log.info("kodi text (%d chars, done=%s) ip=%s", len(str(body.get("text") or "")), body.get("done", True) is not False, self.ip)
            self.send_json(200, res)
        elif path == "/api/saved":
            self._post_saved(body)
        elif path == "/api/settings":
            self._post_settings(body)
        elif path == "/api/connections":
            self.send_json(200, save_connections(body))
        elif path == "/api/connections/test":
            self.send_json(200, test_connection(body))
        elif path == "/api/settings/test":
            if not send_notification("NZB Command Center", "Test alert from your phone app — alerts are working.", tags=("bell",)):
                raise ValueError("Save an ntfy topic URL first.")
            self.send_json(200, {"ok": True})
        elif path == "/api/arr/add":
            if self._limited(ADD_LIMITER, "add"):
                return
            self.send_json(200, arr_add(str(body.get("app") or ""), to_int(body.get("id")), to_int(body.get("profile")),
                                        str(body.get("root") or "")[:500], bool(body.get("search", True)),
                                        str(body.get("monitor") or "future")))
        elif path.startswith("/api/jellyfin/"):
            self._post_jellyfin(path, body)
        elif path == "/api/drives/check":
            self.send_json(200, self._drives_view(force=True))
        elif path == "/api/drives/alerts":
            def apply(st: dict):
                for key in ("notify_drive_health", "notify_drive_hot", "notify_drive_low"):
                    if key in body:
                        st[key] = bool(body[key])
            update_settings(apply)
            log.info("drive alert settings saved ip=%s", self.ip)
            self.send_json(200, self._drives_view())
        elif path == "/api/tv/on":
            if not tv_configured():
                raise ValueError("Set up TV control first.")
            started = tv_start(launch_kodi=body.get("kodi", True) is not False)
            log.info("tv on (kodi=%s) ip=%s", body.get("kodi", True) is not False, self.ip)
            self.send_json(200, {"ok": True, "started": started, "job": tv_job_view()})
        elif path == "/api/tv/off":
            res = tv_off()
            log.info("tv off (%s) ip=%s", "already off" if res.get("already") else "sent", self.ip)
            if _hal_mod and not res.get("already"):   # look at the TV again once it has gone dark, so Home stops saying YouTube
                threading.Timer(5, lambda: _hal_mod._tv_front.update(t=0)).start()
            self.send_json(200, res)
        elif path == "/api/tv/kodi":
            if not tv_configured():
                raise ValueError("Set up TV control first.")
            if tv_power_state() != "on":
                raise ValueError("The TV is off. Use “TV on + Kodi” instead.")
            tv_open_kodi()
            threading.Thread(target=tv_learn_mac, args=(load_settings(),), daemon=True).start()
            log.info("tv open kodi ip=%s", self.ip)
            self.send_json(200, {"ok": True})
        elif path == "/api/tv/settings":
            self._post_tv_settings(body)
        elif path == "/api/kodi/scan":
            self.send_json(200, library_scan())
        elif path == "/api/speedlimit":
            mbps = to_float(body.get("mbps"), -1.0)
            if not 0 <= mbps <= 10000:
                raise ValueError("Speed limit must be between 0 and 10000 MB/s.")
            sab_request("config", {"name": "speedlimit", "value": "0" if mbps == 0 else f"{int(round(mbps * 1024))}K"})
            log.info("speed limit %s MB/s ip=%s", mbps, self.ip)
            self.send_json(200, {"ok": True})
        else:
            self.send_json(404, {"error": "Not found."})

    def _get_arr(self, path: str) -> None:
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        arg = lambda k: qs.get(k, [""])[0]
        if path == "/api/arr":
            if arg("fresh") == "1":
                arr_clear_cache()
            self.send_json(200, arr_overview())
        elif path == "/api/arr/counts":
            self.send_json(200, arr_counts())
        elif path == "/api/arr/upcoming":
            self.send_json(200, {"items": arr_upcoming(force=arg("fresh") == "1")})
        elif path == "/api/arr/lookup":
            if self._limited(SEARCH_LIMITER, "search"):
                return
            self.send_json(200, {"items": arr_lookup(arg("app"), arg("q"))})
        elif path == "/api/arr/options":
            self.send_json(200, arr_options(arg("app")))
        elif path == "/api/arr/art":
            try:
                raw, ctype = arr_art(arg("ref"))
            except (UpstreamError, ConfigError, ValueError):
                self.send_json(404, {"error": "No picture."})
                return
            self._send(200, raw, ctype, {"Cache-Control": "private, max-age=86400"})
        else:
            self.send_json(404, {"error": "Not found."})

    def _get_jellyfin(self, path: str) -> None:
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        arg = lambda k: qs.get(k, [""])[0]
        if path == "/api/jellyfin":
            self.send_json(200, jf_overview())
        elif path == "/api/jellyfin/now":
            self.send_json(200, {"sessions": jf_sessions()})
        elif path == "/api/jellyfin/search":
            self.send_json(200, {"items": jf_search(arg("q"))})
        elif path == "/api/jellyfin/show":
            self.send_json(200, jf_show(arg("id")))
        elif path == "/api/jellyfin/missing":
            self.send_json(200, {"missing": jf_missing(force=arg("fresh") == "1")})
        elif path == "/api/jellyfin/upgrades":
            self.send_json(200, {"movies": jf_upgrades(force=arg("fresh") == "1")})
        elif path == "/api/jellyfin/discover":
            self.send_json(200, {"servers": jf_discover()})
        elif path == "/api/jellyfin/art":
            try:
                raw, ctype = jf_art(arg("id"))
            except UpstreamError:
                self.send_json(404, {"error": "No picture."})
                return
            self._send(200, raw, ctype, {"Cache-Control": "private, max-age=86400"})
        else:
            self.send_json(404, {"error": "Not found."})

    def _post_jellyfin(self, path: str, body: dict) -> None:
        if path == "/api/jellyfin/control":
            jf_control(str(body.get("session") or ""), str(body.get("action") or ""))
            self.send_json(200, {"ok": True})
        elif path == "/api/jellyfin/scan":
            jf_request("/Library/Refresh", post=True, timeout=10)
            jf_clear_cache()
            log.info("jellyfin scan ip=%s", self.ip)
            self.send_json(200, {"ok": True})
        elif path == "/api/jellyfin/connect":
            url = normalize_jellyfin_url(str(body.get("url") or "")[:300])
            key = str(body.get("key") or "").strip()
            if not url:
                raise ValueError("Enter your Jellyfin server's address.")
            if not jf_url_allowed(url):
                raise ValueError("From the phone you can only connect a Jellyfin server on your home network or Tailscale. "
                                 "Set an internet address in the desktop app instead.")
            if not re.fullmatch(r"[A-Za-z0-9]{16,64}", key):
                raise ValueError("That API key doesn't look right. Copy it from Jellyfin: Dashboard → API Keys.")
            global _JF_SECRETS
            _JF_SECRETS = frozenset([key])
            info = jf_request("/System/Info", timeout=8, conf={"url": url, "key": key, "source": "phone"})

            def change(st: dict) -> None:
                st["jellyfin_url"] = url
                st["jellyfin_token"] = _dpapi_protect(key)
            update_settings(change)
            jf_clear_cache()
            log.info("jellyfin connected from phone (%s) ip=%s", info.get("ServerName"), self.ip)
            self.send_json(200, jf_overview())
        elif path == "/api/jellyfin/forget":
            def clear(st: dict) -> None:
                st["jellyfin_url"] = st["jellyfin_token"] = ""
            update_settings(clear)
            jf_clear_cache()
            log.info("jellyfin phone connection removed ip=%s", self.ip)
            self.send_json(200, jf_overview())
        else:
            self.send_json(404, {"error": "Not found."})

    def _drives_view(self, force: bool = False) -> dict:
        report = drive_report(force=force)
        s = load_settings()
        report["alerts"] = {"ready": bool(s["ntfy_url"]), "health": s["notify_drive_health"], "hot": s["notify_drive_hot"],
                            "low": s["notify_drive_low"], "low_gb": s["drive_low_gb"]}
        report["temp_alert"] = DRIVE_TEMP_ALERT
        report["nvme_temp_alert"] = DRIVE_TEMP_ALERT + DRIVE_NVME_TEMP_EXTRA
        return report

    def _home(self) -> dict:
        """One call for the Home tab: SABnzbd summary, today's totals and what Kodi is playing."""
        out: dict = {"sab_ok": True}
        # Kodi is asked in parallel with SABnzbd, so a slow or switched-off Kodi doesn't add
        # its timeout on top of SABnzbd's.
        with ThreadPoolExecutor(max_workers=4) as pool:
            kodi_future = pool.submit(self._kodi_brief)
            up_future = pool.submit(home_upcoming)
            jf_future = pool.submit(home_jellyfin_sessions)
            arrived_future = pool.submit(home_arrived)
            self._home_sab(out)
            try:
                out["kodi"] = kodi_future.result()
            except ConfigError:
                out["kodi"] = {"configured": False}
            out["jellyfin"] = jf_future.result()
            out["arrived"] = arrived_future.result()
            out["upcoming"] = up_future.result()
        out["saved_new"] = sum(len(x.get("new") or []) for x in load_settings()["saved_searches"])
        out["tv_ready"] = tv_configured()
        out["stream_ready"] = altmount_configured()
        out["version"] = VERSION
        return out

    def _home_sab(self, out: dict) -> None:
        try:
            q = sab_request("queue", {"limit": 5}).get("queue", {})
            kb = to_float(q.get("kbpersec"), -1.0)
            slots = q.get("slots", [])
            cur = next((x for x in slots if str(x.get("status", "")).lower() == "downloading"), slots[0] if slots else None)
            free = q.get("diskspace2") or q.get("diskspace1")
            out.update({
                "state": "Paused" if q.get("paused") else q.get("status", "SABnzbd"),
                "paused": bool(q.get("paused")),
                "speed": f"{format_bytes(kb * 1024)}/s" if kb >= 0 else f"{q.get('speed', '0')}B/s",
                "total": to_int(q.get("noofslots_total"), len(slots)),
                "disk_free_gb": to_float(free, -1.0) if free not in (None, "") else None,
                "current": None if not cur else {
                    "name": cur.get("filename", "Unnamed"), "status": cur.get("status", ""), "pct": to_float(cur.get("percentage")),
                    "size": cur.get("size", ""), "sizeleft": cur.get("sizeleft", ""), "eta": cur.get("timeleft", "")},
            })
            midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
            hist = sab_request("history", {"limit": 60}).get("history", {}).get("slots", [])
            today = [x for x in hist if to_int(x.get("completed")) >= midnight]
            out["done_today"] = sum(1 for x in today if str(x.get("status")).lower() == "completed")
            out["failed_today"] = sum(1 for x in today if str(x.get("status")).lower() == "failed")
        except (UpstreamError, ConfigError) as exc:
            out.update({"sab_ok": False, "sab_error": redact(exc), "state": "Offline", "speed": "", "total": 0})

    def _kodi_brief(self) -> dict:
        if not kodi_configured():
            return {"configured": False}
        try:
            playing, front = kodi_now_playing(), tv_front_app()
            other = bool(front) and not (front["kodi"] or front["home"] or front["hal"])
            return {"configured": True, "online": True, "playing": playing, "front": front["app"] if other else "",
                    "front_title": front.get("title", "") if other else "", "front_sub": front.get("sub", "") if other else "",
                    "front_paused": bool(front.get("paused")) if other else False}
        except UpstreamError as exc:
            return {"configured": True, "online": False, "problem": str(exc)}  # not "error": see api() in the page

    def _kodi_overview(self) -> dict:
        info = self._kodi_brief()
        if info.get("configured") is False:   # no Kodi: nothing could play a stream either
            return info
        try:
            info["streams"] = streams_view()
        except Exception:  # noqa: BLE001 - a bonus: never lose the Kodi overview over it
            info["streams"] = {"configured": False, "items": []}
        if not info.get("online"):
            return info
        lim = {"limits": {"start": 0, "end": 1}}
        try:
            info["counts"] = {
                "movies": to_int((_kdict(kodi_call("VideoLibrary.GetMovies", lim)).get("limits") or {}).get("total")),
                "episodes": to_int((_kdict(kodi_call("VideoLibrary.GetEpisodes", lim)).get("limits") or {}).get("total")),
            }
            recent = _kdict(kodi_call("VideoLibrary.GetRecentlyAddedMovies", {"properties": KODI_MOVIE_PROPS,
                      "limits": {"start": 0, "end": 12}})).get("movies", [])
            info["recent"] = [_kodi_movie(x) for x in recent]
            info["counts"]["shows"] = to_int(_kdict(_kdict(kodi_call("VideoLibrary.GetTVShows", lim)).get("limits")).get("total"))
        except UpstreamError as exc:
            info.update(online=False, problem=str(exc))
            return info
        try:
            info["continue"] = kodi_continue_watching()
        except UpstreamError:
            info["continue"] = []
        return info

    def _post_search(self, body: dict) -> None:
        if self._limited(SEARCH_LIMITER, "search"):
            return
        query = str(body.get("query") or "").strip()[:180]
        category = str(body.get("category") or "").strip()
        imdbid = str(body.get("imdbid") or "").strip()
        merged, errors, searched = run_search(query, str(body.get("indexer") or "*"), category, imdbid)
        output = cache_results(merged)
        lib = kodi_library_index(background=True)
        for item in output:
            item["in_library"] = library_match(item.get("title", ""), lib)
        plan = search_plan(query, imdbid)
        log.info("search ip=%s indexers=%d q=%r cat=%s mode=%s results=%d errors=%d", self.ip, searched, query,
                 category or "-", (plan or {}).get("t", "search"), len(output), len(errors))
        self.send_json(200, {"results": output, "errors": errors, "mode": (plan or {}).get("t", "search")})

    # ---- saved searches & phone settings ---------------------------------------
    def _saved_view(self) -> dict:
        settings = load_settings()
        names = {ix["id"]: ix["name"] for ix in get_config()["indexers"]}
        return {"interval_hours": settings["saved_interval_hours"], "alerts": bool(settings["ntfy_url"]),
                "searches": [{
                    "id": x["id"], "query": x.get("query", ""), "indexer": x.get("indexer", "*"),
                    "indexer_name": "All indexers" if x.get("indexer", "*") == "*" else names.get(x.get("indexer"), "Removed indexer"),
                    "category": x.get("category", ""), "created": x.get("created", 0), "last_run": x.get("last_run", 0),
                    "last_error": x.get("last_error", ""), "last_count": x.get("last_count"), "new": x.get("new") or [],
                } for x in settings["saved_searches"]]}

    def _post_saved(self, body: dict) -> None:
        action, sid = str(body.get("action") or ""), str(body.get("id") or "")
        if action == "add":
            query = str(body.get("query") or "").strip()[:180]
            indexer, category = str(body.get("indexer") or "*"), str(body.get("category") or "").strip()
            if len(query) < 2:
                raise ValueError("Type what to look for before saving the search.")
            if category and not CATEGORY_RE.match(category):
                raise ValueError("Invalid category.")
            if indexer != "*" and indexer not in {ix["id"] for ix in get_config()["indexers"]}:
                raise ValueError("Selected indexer does not exist.")

            def add(st: dict) -> None:
                if any(x.get("query", "").lower() == query.lower() and x.get("indexer") == indexer and x.get("category") == category
                       for x in st["saved_searches"]):
                    raise ValueError("That search is already saved.")
                if len(st["saved_searches"]) >= MAX_SAVED_SEARCHES:
                    raise ValueError(f"You can save up to {MAX_SAVED_SEARCHES} searches.")
                st["saved_searches"].append({"id": secrets.token_hex(6), "query": query, "indexer": indexer, "category": category,
                                             "created": int(time.time()), "last_run": 0, "baselined": False, "seen": [], "new": []})
            update_settings(add)
            log.info("saved search added q=%r ip=%s", query, self.ip)
        elif action in ("delete", "clear"):
            def change(st: dict) -> None:
                entry = next((x for x in st["saved_searches"] if x.get("id") == sid), None)
                if entry is None:
                    raise ValueError("That saved search no longer exists.")
                if action == "delete":
                    st["saved_searches"].remove(entry)
                else:
                    entry["new"] = []
            update_settings(change)
        elif action == "run":
            if self._limited(SEARCH_LIMITER, "search"):
                return
            run_saved_search(sid, notify=False)
        else:
            raise ValueError("Invalid action.")
        self.send_json(200, self._saved_view())

    def _settings_view(self) -> dict:
        s = load_settings()
        return {"ntfy_url": s["ntfy_url"], "ntfy_token_set": bool(s["ntfy_token"]),
                "notify_complete": s["notify_complete"], "notify_failed": s["notify_failed"], "notify_saved": s["notify_saved"],
                "saved_interval_hours": s["saved_interval_hours"], "intervals": list(SAVED_INTERVALS),
                "autostart": autostart_enabled(), "version": VERSION}

    def _tv_view(self, with_power: bool = False) -> dict:
        s = load_settings()
        auto = ""
        try:
            auto = urllib.parse.urlsplit(get_config()["kodi"]["url"]).hostname or ""
        except ConfigError:
            pass
        view = {"configured": tv_configured(s), "host": s.get("tv_host") or "", "auto_host": auto,
                "psk_set": bool(s.get("tv_psk")), "job": tv_job_view()}
        if with_power and view["configured"]:
            try:
                view["power"] = tv_power_state(s)
            except UpstreamError as exc:
                view["power"], view["problem"] = "unknown", str(exc)  # not "error": see api() in the page
            if view.get("power") == "on" and not s.get("tv_mac"):
                threading.Thread(target=tv_learn_mac, args=(s,), daemon=True).start()
        view["mac"] = s.get("tv_mac") or ""
        return view

    def _post_tv_settings(self, body: dict) -> None:
        host = validate_tv_host(body.get("host", ""))
        key = str(body.get("psk") or "").strip()
        if key and (len(key) > 64 or any(ch.isspace() for ch in key)):
            raise ValueError("That Pre-Shared Key doesn't look right.")
        mac = str(body.get("mac") or "").strip().upper().replace("-", ":")
        if mac and not MAC_RE.match(mac):
            raise ValueError("The MAC address should look like AA:BB:CC:DD:EE:FF.")

        def change(st: dict) -> None:
            old_mac = str(st.get("tv_mac") or "").upper()
            if host != st.get("tv_host"):
                st["tv_mac"], st["tv_mac_manual"], st["tv_kodi_uri"] = "", False, ""   # a different TV: forget what was learned
            st["tv_host"] = host
            if "mac" in body and mac != old_mac:      # the field comes back pre-filled; only act on a real change
                st["tv_mac"], st["tv_mac_manual"] = mac, bool(mac)
            if key:
                st["tv_psk"] = _dpapi_protect(key)
            elif body.get("clear"):
                st["tv_psk"] = ""
        update_settings(change)
        view = self._tv_view(with_power=True)
        log.info("tv settings saved ip=%s", self.ip)
        self.send_json(200, view)

    def _post_settings(self, body: dict) -> None:
        url = validate_ntfy_url(body.get("ntfy_url", ""))
        token = str(body.get("ntfy_token") or "").strip()
        if len(token) > 200 or any(ch.isspace() for ch in token):
            raise ValueError("That access token doesn't look right.")
        hours = to_int(body.get("saved_interval_hours"), 6)
        if hours not in SAVED_INTERVALS:
            raise ValueError("Invalid check interval.")

        def change(st: dict) -> None:
            st["ntfy_url"] = url
            if token:
                st["ntfy_token"] = _dpapi_protect(token)
            elif body.get("clear_token") or not url:
                st["ntfy_token"] = ""
            for key in ("notify_complete", "notify_failed", "notify_saved"):
                if key in body:
                    st[key] = bool(body[key])
            st["saved_interval_hours"] = hours
        update_settings(change)
        log.info("phone settings saved (alerts %s) ip=%s", "on" if url else "off", self.ip)
        self.send_json(200, self._settings_view())

    def _add_one(self, result_id: str, category: str, priority: str) -> dict:
        item = get_cached_result(result_id)
        if not item:
            raise ValueError("That search result expired. Search again and retry.")
        if not category:  # Auto
            try:
                category = match_sab_category(sab_categories(), item.get("category", ""), item["title"])
            except UpstreamError:
                category = ""  # couldn't list categories: let SABnzbd use its default
        elif category == "*":
            category = ""
        nzb_data, used = download_indexer_nzb(item)
        sab_addfile(nzb_data, item["title"], category, priority)
        log.info("add ip=%s title=%r indexer=%s cat=%s prio=%s bytes=%d", self.ip, item["title"], used, category or "-", priority or "-", len(nzb_data))
        return {"id": result_id, "title": item["title"], "bytes": len(nzb_data), "indexer": used, "category": category}

    def _post_add(self, body: dict) -> None:
        if self._limited(ADD_LIMITER, "add"):
            return
        category, priority = parse_add_options(body)
        done = self._add_one(str(body.get("id") or ""), category, priority)
        self.send_json(200, {"ok": True, "message": "NZB uploaded to SABnzbd.", **done})

    def _post_add_batch(self, body: dict) -> None:
        ids = body.get("ids")
        if not isinstance(ids, list) or not ids:
            raise ValueError("Select at least one result.")
        if len(ids) > MAX_BATCH_ADD:
            raise ValueError(f"You can add at most {MAX_BATCH_ADD} NZBs at once.")
        unique_ids = list(dict.fromkeys(str(x) for x in ids))
        if self._limited(ADD_LIMITER, "add", cost=len(unique_ids)):  # each NZB counts, not each batch
            return
        category, priority = parse_add_options(body)

        def add(result_id: str):
            try:
                return self._add_one(result_id, category, priority), None
            except (UpstreamError, ValueError) as exc:
                return None, str(exc)
            except Exception:  # one bad item must not lose the report for the ones already added
                log.exception("Unexpected error adding result %s", result_id)
                return None, "Unexpected error (see mobile_server.log on the PC)."

        # Downloaded a few at a time instead of one by one: a 10-item batch used to take
        # minutes (each NZB can need up to 45 s) while the phone showed "Adding…".
        with ThreadPoolExecutor(max_workers=min(BATCH_ADD_WORKERS, len(unique_ids))) as pool:
            outcomes = list(pool.map(add, unique_ids))  # map keeps the original order

        added, errors = [], []
        for result_id, (done, err) in zip(unique_ids, outcomes):
            if done is not None:
                added.append(done)
            else:
                item = get_cached_result(result_id) or {}
                errors.append({"id": result_id, "title": item.get("title", ""), "error": err})
        self.send_json(200, {"ok": not errors, "added": added, "errors": errors})

    def _post_queue_item(self, body: dict) -> None:
        nzo_id, action = str(body.get("id") or ""), str(body.get("action") or "")
        if not NZB_ID_RE.match(nzo_id):
            raise ValueError("Invalid item id.")
        if action not in ("pause", "resume", "delete", "top", "category", "priority"):
            raise ValueError("Invalid action.")
        if action == "category":
            category, _ = parse_add_options({"category": body.get("value")})
            sab_request("change_cat", {"value": nzo_id, "value2": category or "*"})
            log.info("queue item %s category=%s ip=%s", nzo_id, category or "*", self.ip)
            self.send_json(200, {"ok": True})
            return
        if action == "priority":
            priority = str(body.get("value") or "").strip()
            if priority not in ("-1", "0", "1", "2"):
                raise ValueError("Invalid priority.")
            sab_request("queue", {"name": "priority", "value": nzo_id, "value2": priority})
            log.info("queue item %s priority=%s ip=%s", nzo_id, priority, self.ip)
            self.send_json(200, {"ok": True})
            return
        if action == "top":
            sab_request("switch", {"value": nzo_id, "value2": 0})
            log.info("queue item top %s ip=%s", nzo_id, self.ip)
            self.send_json(200, {"ok": True})
            return
        extra = {"name": action, "value": nzo_id}
        if action == "delete":
            extra["del_files"] = 1
        sab_request("queue", extra)
        log.info("queue item %s %s ip=%s", action, nzo_id, self.ip)
        self.send_json(200, {"ok": True})


# ==============================================================================
# 9. STARTUP
# ==============================================================================

def setup_logging(verbose: bool = False) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.append(logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=500_000, backupCount=2, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s", handlers=handlers, force=True,
    )
    for h in handlers:  # keys never reach the console or mobile_server.log, not even in a traceback
        h.setFormatter(_RedactingFormatter("%(asctime)s %(levelname)s %(message)s"))


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def init_state() -> dict:
    """Loads the access code and configuration (raises ConfigError with a clear message)."""
    global _token_bytes
    _token_bytes = load_or_create_access_token().encode("utf-8")
    return get_config()


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="NZB Command Center - Phone App")
    p.add_argument("--host", default=os.environ.get("NZB_MOBILE_HOST", DEFAULT_HOST), help="address to bind (default: all interfaces)")
    p.add_argument("--port", type=int, default=int(os.environ.get("NZB_MOBILE_PORT", DEFAULT_PORT)))
    p.add_argument("--config", type=Path, default=None, help="path to nzb_manager_config.json")
    p.add_argument("--allow-cidr", action="append", default=[], metavar="CIDR", help="also accept this network (repeatable)")
    p.add_argument("--allow-any-client", action="store_true", help="accept connections from ANY address (not recommended)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--install-autostart", action="store_true", help="start this server hidden whenever you log in to Windows")
    p.add_argument("--remove-autostart", action="store_true", help="stop starting it at login")
    p.add_argument("--no-hal", action="store_true", help="don't run HAL 9000 inside this server")
    p.add_argument("--hal-port", type=int, default=int(os.environ.get("HAL_PORT", HAL_PORT)), help="HAL 9000's own port (default 8766)")
    return p.parse_args(argv)


def _autostart_args(args: argparse.Namespace) -> list[str]:
    """The options to repeat when Windows starts the server at login."""
    out = ["--port", str(args.port)]
    if args.host != DEFAULT_HOST:
        out += ["--host", args.host]
    if args.config:
        out += ["--config", str(args.config.resolve())]
    for cidr in args.allow_cidr:
        out += ["--allow-cidr", cidr]
    if args.allow_any_client:
        out.append("--allow-any-client")
    if getattr(args, "no_hal", False):
        out.append("--no-hal")
    if getattr(args, "hal_port", HAL_PORT) != HAL_PORT:
        out += ["--hal-port", str(args.hal_port)]
    return out


def start_embedded_hal(args: argparse.Namespace) -> str:
    """Starts HAL 9000 in this process: its watchers, and its own port (so the HAL home-screen icon keeps
    working). Returns a line for the banner. Never raises: HAL failing must not stop the phone app."""
    if getattr(args, "no_hal", False):
        _hal_state["note"] = "turned off (--no-hal)"
        return "off (--no-hal)"
    if _hal_mod is None:
        _hal_state["note"] = HAL_IMPORT_ERROR
        log.warning("HAL 9000 not started: %s", HAL_IMPORT_ERROR)
        return f"not available: {HAL_IMPORT_ERROR}"
    try:
        srv = ThreadingHTTPServer((args.host, args.hal_port), _hal_mod.HalHandler)
    except OSError as exc:
        _hal_state["note"] = (f"port {args.hal_port} is in use, probably by the separate HAL 9000 Server from before. "
                              "Run update_all.ps1 (or setup_hal_startup.ps1) to move HAL into the phone server")
        log.warning("HAL 9000 not started inside the phone server: port %s is in use (%s).", args.hal_port, exc)
        return f"not started: port {args.hal_port} is in use (the separate HAL 9000 Server is probably still running)"
    try:
        _hal_mod._token_bytes = _token_bytes
        _hal_mod.ALLOW_ANY_CLIENT = ALLOW_ANY_CLIENT
        _hal_mod.EXTRA_ALLOWED_NETS[:] = EXTRA_ALLOWED_NETS
        _hal_mod.HAL = _hal_mod.Hal()
    except Exception as exc:  # noqa: BLE001
        srv.server_close()
        _hal_state["note"] = f"it failed to start ({exc})"
        log.exception("HAL 9000 failed to start")
        return f"failed to start: {exc}"
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.5}, name="hal-http", daemon=True).start()
    _hal_state.update(running=True, note="running", server=srv)
    log.info("HAL 9000 %s running inside the phone server: /hal and port %s", _hal_mod.VERSION, args.hal_port)
    return f"running - /hal on this port, and port {args.hal_port}"


def stop_embedded_hal() -> None:
    if not _hal_state["running"]:
        return
    try:
        _hal_mod.HAL.stop()
        if _hal_mod.HAL.monitor is not None and hasattr(_hal_mod.HAL.monitor, "stop"):
            _hal_mod.HAL.monitor.stop()
        _hal_state["server"].shutdown()
        _hal_state["server"].server_close()
    except Exception:  # noqa: BLE001
        log.exception("Stopping HAL 9000")
    _hal_state["running"] = False


def main(argv=None) -> None:
    global CONFIG_FILE, ALLOW_ANY_CLIENT
    # Started windowless (pythonw / the login launcher): there is no console to print to.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = sys.stdout
    args = parse_args(argv)
    if args.install_autostart or args.remove_autostart:
        if args.remove_autostart:
            print("Removed: the server will no longer start at login." if remove_autostart() else "It wasn't set to start at login.")
            if startup_task_exists():
                print(f"Note: the Task Scheduler task \"{STARTUP_TASK}\" still starts it as administrator. "
                      "Delete it in Task Scheduler to stop that too.")
        else:
            try:
                path = install_autostart(_autostart_args(args))
            except ConfigError as exc:
                print(exc)
                return
            print(f"Done: the server will start hidden when you log in.\nLauncher: {path}\n"
                  f"Your access code stays in {ACCESS_KEY_FILE.name}; the log is {LOG_FILE.name}.")
        return
    if args.config:
        CONFIG_FILE = args.config.resolve()
    ALLOW_ANY_CLIENT = args.allow_any_client
    for cidr in args.allow_cidr:
        EXTRA_ALLOWED_NETS.append(ipaddress.ip_network(cidr, strict=False))
    setup_logging(args.verbose)

    cfg = init_state()
    if MISSING_PARTS:
        log.warning("Missing from %s: %s. The phone app shows a placeholder page until you run update_all.ps1.",
                    BASE_DIR, ", ".join(MISSING_PARTS))
    if not cfg["indexers"]:
        log.warning("No enabled indexers were found in %s.", CONFIG_FILE.name)
    kodi_library_index(background=True)  # warm up so the first search can already show "Already in Kodi"

    server = ThreadingHTTPServer((args.host, args.port), MobileHandler)
    hal_note = start_embedded_hal(args)
    worker = BackgroundWorker()
    worker.start()
    server.daemon_threads = True
    token = _token_bytes.decode("utf-8")
    local_ip = get_local_ip()
    print()
    print("=" * 66)
    print(f" NZB COMMAND CENTER - MOBILE SERVER v{VERSION}")
    print("=" * 66)
    print(f" Desktop config  : {CONFIG_FILE}")
    print(f" Enabled indexers: {len(cfg['indexers'])}   (config changes are picked up automatically)")
    print(f" SABnzbd         : {cfg['sab_url']}")
    print(f" Kodi            : {cfg['kodi']['url'] or 'not set up (add it in the desktop app)'}")
    print(f" HAL 9000        : {hal_note}")
    if MISSING_PARTS:
        print(f" MISSING FILES   : {', '.join(MISSING_PARTS)} - run update_all.ps1 to get them back")
    print(f" This PC         : http://127.0.0.1:{args.port}")
    print(f" Phone (Wi-Fi)   : http://{local_ip}:{args.port}")
    print()
    print(" MOBILE ACCESS CODE (stable across restarts):")
    print(f" {token}")
    print()
    print(" Pairing link - open this on the phone once and it connects itself:")
    print(f" http://{local_ip}:{args.port}/#k={token}")
    print(" Kodi home-screen icon - open this on the phone, then Add to Home Screen:")
    print(f" http://{local_ip}:{args.port}/kodi#k={token}")
    if _hal_state["running"]:
        print(" HAL 9000 - open on the phone (pair with the PIN from http://localhost:%d/hal/pair):" % args.port)
        print(f" http://{local_ip}:{args.port}/hal/   (or http://{local_ip}:{args.hal_port}/ as before)")
    print()
    if ALLOW_ANY_CLIENT:
        print(" WARNING: --allow-any-client is on; anyone who can reach this port can try the code.")
    else:
        print(" Only devices on your local network (and Tailscale) are accepted.")
    print(f" Key file: {ACCESS_KEY_FILE}   Log: {LOG_FILE}")
    print(" To rotate the code: stop the server, delete mobile_access_key.txt, restart.")
    if autostart_enabled() is False:
        print(" To start it automatically at login: python mobile_server.py --install-autostart")
    print()
    print(" Keep this window open while using the phone app. Ctrl+C stops the server.")
    print("=" * 66)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nStopping mobile server...")
    finally:
        worker.stop()
        stop_embedded_hal()
        server.server_close()


if __name__ == "__main__":
    try:
        main()
    except (ConfigError, OSError, ValueError) as exc:
        log.error("Startup error: %s", redact(exc))  # reaches the log file even when started windowless
        print(f"\nSTARTUP ERROR: {redact(exc)}")
        print("Check that mobile_server.py is beside nzb_manager_config.json and that the desktop app's config is valid.")
        if sys.stdin and sys.stdin.isatty():
            input("\nPress Enter to close...")
        sys.exit(1)
