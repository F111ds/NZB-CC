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
  * HAL 9000 - runs inside this server (the first step towards one all-in-one app): open /hal on
             the phone, or HAL's own port 8766 as before. Needs psutil and hal_server.py + system_monitor.py
             in this folder; without them everything else works and the banner says why HAL is off.
  * Drives - every drive's health, temperature, free space, live read/write activity,
             health counters (errors, wear, hours, cycles), volumes and identity, a 24 h
             temperature graph, and phone alerts for problems, heat and low space
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

VERSION = "3.12.0"
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

APP_ICON_PNG_B64 = (
    "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"
)
ICON_PNG = base64.b64decode("".join(APP_ICON_PNG_B64))

# Second home-screen icon that opens the app straight on the Kodi tab (/kodi): a TV with a play button,
# drawn in the same style as the main icon. The 512px one fills the square for Android's round/squircle masks.
KODI_ICON_PNG_B64 = (
    "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"
)
KODI_ICON_MASKABLE_PNG_B64 = (
    "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"
    "AAAASUVORK5CYII="
)
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


def kodi_now_playing() -> dict | None:
    players = kodi_call("Player.GetActivePlayers")
    players = [p for p in players if isinstance(p, dict)] if isinstance(players, list) else []
    video = [p for p in players if p.get("type") == "video"] or players
    if not video:
        return None
    pid = to_int(video[0].get("playerid"))
    item = _kdict(kodi_call("Player.GetItem", {"playerid": pid, "properties": ["title", "showtitle", "season", "episode", "year", "art", "thumbnail"]})).get("item", {})
    props = _kdict(kodi_call("Player.GetProperties", {"playerid": pid, "properties": [
        "percentage", "time", "totaltime", "speed", "currentsubtitle", "subtitleenabled", "currentaudiostream"]}))
    title = str(item.get("title") or item.get("label") or "Playing")
    if item.get("showtitle"):
        sub = f"{item['showtitle']} · S{to_int(item.get('season')):02d}E{to_int(item.get('episode')):02d}"
    else:
        sub = str(item.get("year") or "") if to_int(item.get("year")) else ""
    art = item.get("art") or {}
    poster = _art_ref(art.get("poster") or art.get("tvshow.poster") or art.get("thumb") or item.get("thumbnail"))
    fanart = _art_ref(art.get("fanart") or art.get("tvshow.fanart"))
    return {"playerid": pid, "title": title, "sub": sub, "poster": poster, "fanart": fanart,
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


def _default_settings() -> dict:
    return {"ntfy_url": "", "ntfy_token": "", "notify_complete": True, "notify_failed": True, "notify_saved": True,
            "saved_interval_hours": 6, "saved_searches": [],
            "jellyfin_url": "", "jellyfin_token": "",
            "notify_drive_health": True, "notify_drive_hot": True, "notify_drive_low": True, "drive_low_gb": DRIVE_LOW_GB,
            # Sony TV power / app launch (TV on + Kodi). Blank tv_host = the host from the Kodi URL.
            "tv_host": "", "tv_psk": "", "tv_mac": "", "tv_mac_manual": False, "tv_kodi_uri": ""}


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
        "title", "year", "runtime", "art", "thumbnail", "playcount", "resume", "lastplayed", "streamdetails"]),
        timeout=10)).get("movies") or []
    episodes = _kdict(kodi_call("VideoLibrary.GetEpisodes", dict(common, properties=[
        "title", "showtitle", "season", "episode", "runtime", "art", "playcount", "resume", "lastplayed"]),
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
                    "lastplayed": str(mv.get("lastplayed") or ""),
                    "art_ref": _art_ref(art.get("poster") or mv.get("thumbnail"))} | _tv_progress(mv))
    for ep in episodes:
        if not isinstance(ep, dict) or to_int(ep.get("episodeid")) <= 0:
            continue
        art = _kdict(ep.get("art"))
        show = str(ep.get("showtitle") or "")
        out.append({"id": f"e{to_int(ep.get('episodeid'))}", "episodeid": to_int(ep.get("episodeid")),
                    "title": show or str(ep.get("title") or "Untitled"), "episode_title": str(ep.get("title") or ""),
                    "season": to_int(ep.get("season")), "episode": to_int(ep.get("episode")),
                    "lastplayed": str(ep.get("lastplayed") or ""),
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
            "title", "showtitle", "season", "episode", "year", "art", "thumbnail", "streamdetails"]}, timeout=4)).get("item"))
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
    if kind == "movie":
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
                items.append(it)
            if len(items) >= TV_WATCHED_SIZE:
                break
        if TV_WATCHED_ART_DIR.exists():
            keep = {f"{it['id']}.img" for it in items}
            for f in TV_WATCHED_ART_DIR.glob("*.img"):
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
            "progress", "remaining", "movieid", "episodeid", "lastplayed")
    items = [{k: it.get(k) for k in keys} | {"poster": f"/api/tv/poster?id={it['id']}"} for it in state["items"]]
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
        for title, message, prio in drive_alerts(report, settings, self._drive_sent, now):
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

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="theme-color" content="#060D10"><meta name="apple-mobile-web-app-title" content="NZB Center">
<title>NZB Command Center</title>
<link rel="manifest" href="/manifest.webmanifest"><link rel="icon" type="image/png" href="/icon.png"><link rel="apple-touch-icon" href="/icon.png">
<style>
:root{color-scheme:dark;--bg:#060D10;--side:#08141A;--card:#0E1C23;--hover:#13262F;--deep:#08141A;--line:#1A3440;--teal:#2DD4BF;--teal2:#5EEAD4;--ink:#041016;--white:#F0FDFA;--text:#C9DDE2;--soft:#A7C1C8;--muted:#7F9DA6;--red:#F87171;--amber:#FBBF24;--green:#34D399;--sky:#38BDF8;--purple:#C4B5FD;--mono:ui-monospace,"SF Mono","Cascadia Mono",Consolas,monospace}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--white);font:15px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI Variable","Segoe UI",Roboto,Inter,sans-serif;-webkit-text-size-adjust:100%}
button,input,select{font:inherit;color:inherit}
header{position:sticky;top:0;z-index:5;background:rgba(6,13,16,.92);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);padding:calc(14px + env(safe-area-inset-top)) 18px 12px}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:17px;letter-spacing:-.01em}.brand img{width:30px;height:30px;border-radius:8px}
.statusbar{display:flex;gap:8px;margin-top:10px}.spill{display:inline-flex;align-items:center;gap:7px;background:var(--card);border-radius:20px;padding:6px 12px;font:600 12px var(--mono);color:var(--muted)}
.spill i{width:8px;height:8px;border-radius:4px;background:currentColor;display:inline-block}.spill.ok{color:var(--green)}.spill.warn{color:var(--amber)}.spill.bad{color:var(--red)}.spill.busy{color:var(--teal)}
main{max-width:720px;margin:auto;padding:4px 14px calc(150px + env(safe-area-inset-bottom))}
h1{font-size:26px;letter-spacing:-.02em;margin:10px 4px 2px}.lede{color:var(--soft);font-size:13px;margin:0 4px 14px}
.card{background:var(--card);border-radius:14px;padding:14px;margin-bottom:12px}
.label{display:block;font-size:12px;color:var(--soft);margin:0 0 6px}
.section{font-size:11px;font-weight:600;letter-spacing:.08em;color:var(--muted);margin:18px 4px 8px}
.field{width:100%;background:var(--deep);border:1px solid transparent;border-radius:10px;padding:12px 13px;font-size:16px;margin-bottom:10px;outline:none}.field:focus{border-color:var(--teal)}
.search{display:flex;align-items:center;gap:10px;background:var(--deep);border-radius:12px;padding:0 14px;min-height:50px;margin-bottom:10px;border:1px solid transparent}.search:focus-within{border-color:var(--teal)}
.search input{flex:1;background:transparent;border:0;outline:none;font-size:16px;min-width:0}
.row{display:flex;gap:10px;align-items:center}.row>*{flex:1;min-width:0}
.btn{border:0;border-radius:12px;background:var(--teal);color:var(--ink);font-weight:700;padding:13px 16px;font-size:15px;min-height:48px;cursor:pointer}
.btn.ghost{background:transparent;color:var(--white);border:1px solid var(--line);font-weight:600}
.btn.danger{background:transparent;color:var(--red);border:1px solid #5B2328;font-weight:600}
.btn.sm{min-height:40px;padding:8px 13px;font-size:13px;border-radius:10px}
.btn:disabled{opacity:.45}.btn:active:not(:disabled){transform:scale(.98)}.full{width:100%}
.icon-btn{width:44px;height:44px;border-radius:12px;border:0;background:var(--hover);color:var(--text);display:inline-flex;align-items:center;justify-content:center;flex:none}.icon-btn.red{color:var(--red)}
details.opts{background:var(--card);border-radius:14px;margin-bottom:12px}details.opts summary{list-style:none;cursor:pointer;padding:14px;display:flex;align-items:center;gap:10px;font-weight:600;font-size:14px}
details.opts summary::-webkit-details-marker{display:none}details.opts summary .chev{margin-left:auto;transition:transform .2s;color:var(--muted)}details.opts[open] summary .chev{transform:rotate(180deg)}
details.opts .body{padding:0 14px 14px}
.status{font-size:13px;color:var(--soft);margin:2px 4px 10px;white-space:pre-wrap}.status.error{color:var(--red)}
.result{background:var(--card);border-radius:14px;padding:14px;margin-bottom:10px;position:relative}
.result h3{font-size:15px;line-height:1.35;margin:0 40px 6px 0;overflow-wrap:anywhere;font-weight:600}
.details{color:var(--soft);font-size:13px;margin-bottom:10px;overflow-wrap:anywhere}
.badges{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}.badge{font:600 11px var(--mono);padding:4px 8px;border-radius:6px;background:var(--deep);color:var(--text)}.badge.purple{background:#22183A;color:var(--purple)}
.check{position:absolute;top:10px;right:10px;width:40px;height:40px;display:flex;align-items:center;justify-content:center}.check input{width:22px;height:22px;accent-color:var(--teal)}
.result .btn.added{background:#0B2A22;color:var(--green)}
.tiles{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-bottom:12px}
.tile{background:var(--card);border-radius:14px;padding:12px}.tile .k{font-size:11px;color:var(--soft)}.tile .v{font:600 19px var(--mono);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.tile .v small{font-size:12px;color:var(--soft)}
.hero{background:var(--card);border-radius:16px;padding:16px;margin-bottom:12px}
.tag{font:600 11px var(--mono);padding:4px 8px;border-radius:6px;letter-spacing:.03em}.tag.ok{background:#0B2A22;color:var(--green)}.tag.warn{background:#2E2410;color:var(--amber)}.tag.busy{background:#0B2230;color:var(--sky)}
.hero .name{font-weight:600;font-size:16px;margin:10px 0 4px;overflow-wrap:anywhere}.hero .sub{font:500 12px var(--mono);color:var(--soft)}
.bar{height:10px;background:var(--deep);border-radius:5px;overflow:hidden;margin:12px 0 10px}.bar span{display:block;height:100%;background:var(--teal);border-radius:5px;transition:width .6s ease}
.bar.thin{height:4px;margin:8px 0 0}.bar.thin span{background:var(--sky)}
.qrow{display:flex;align-items:center;gap:10px;background:var(--card);border-radius:12px;padding:12px;margin-bottom:8px}
.qrow .info{flex:1;min-width:0}.qrow .t{font-size:14px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.qrow .m{font:500 11px var(--mono);color:var(--soft);margin-top:3px}
.hrow{display:flex;align-items:flex-start;gap:12px;background:#0A171D;border-radius:12px;padding:12px;margin-bottom:8px}
.dot{width:9px;height:9px;border-radius:5px;margin-top:6px;flex:none}.hrow .info{flex:1;min-width:0}.hrow .t{font-size:14px;overflow-wrap:anywhere}.hrow .m{font-size:12px;color:var(--soft);margin-top:3px}.hrow .m.bad{color:var(--red)}
.empty{text-align:center;color:var(--soft);padding:34px 14px;font-size:13px}.empty svg{color:var(--teal);opacity:.7;margin-bottom:8px}.empty b{display:block;color:var(--white);font-size:16px;margin-bottom:4px}
.hidden{display:none!important}.hint{font-size:12px;color:var(--muted);line-height:1.5}
nav.tabs{position:fixed;left:0;right:0;bottom:0;z-index:6;display:flex;background:rgba(8,20,26,.96);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-top:1px solid #10242C;padding:6px 8px calc(6px + env(safe-area-inset-bottom))}
.tab{flex:1;min-width:0;background:transparent;border:0;color:var(--muted);display:flex;flex-direction:column;align-items:center;gap:3px;padding:6px 0;font-size:10.5px;font-weight:600;position:relative;min-height:52px}
.tab.active{color:var(--teal)}.tab .count{position:absolute;top:2px;left:calc(50% + 6px);background:var(--teal);color:var(--ink);font:700 10px var(--mono);padding:1px 6px;border-radius:9px}
.batch{position:fixed;left:12px;right:12px;bottom:calc(74px + env(safe-area-inset-bottom));z-index:7;max-width:696px;margin:auto;display:flex;align-items:center;gap:10px;background:var(--hover);border:1px solid var(--line);border-radius:16px;padding:10px 10px 10px 16px;box-shadow:0 10px 30px rgba(0,0,0,.45)}
.batch span{flex:1;font-weight:600;font-size:14px}
.toast{position:fixed;left:12px;right:12px;bottom:calc(82px + env(safe-area-inset-bottom));max-width:696px;margin:auto;background:var(--hover);border:1px solid var(--teal);border-radius:14px;padding:13px 15px;z-index:9;white-space:pre-wrap;font-size:14px;box-shadow:0 10px 30px rgba(0,0,0,.45)}.toast.err{border-color:var(--red)}
.pair{max-width:440px;margin:40px auto 0}.pair h1{margin-top:0}
.seg{display:flex;background:var(--card);border-radius:12px;padding:4px;margin:4px 0 14px}.seg button{flex:1;min-height:40px;border:0;border-radius:9px;background:transparent;color:var(--muted);font-weight:600;font-size:14px}.seg button.on{background:var(--hover);color:var(--white)}
.shortcut{width:100%;display:flex;align-items:center;gap:10px;background:var(--deep);border:1px solid var(--line);border-radius:14px;padding:0 14px;min-height:52px;color:var(--muted);font-size:16px;margin-bottom:12px;text-align:left}
.kode{font-size:11px;font-weight:700;letter-spacing:.08em;color:var(--muted)}
.ctrls{display:flex;align-items:center;gap:12px}.ctrls .tm{flex:1;font:500 12px var(--mono);color:var(--soft)}
.round{width:48px;height:48px;border-radius:24px;border:0;background:var(--hover);color:var(--white);display:inline-flex;align-items:center;justify-content:center;flex:none}.round.main{background:var(--teal);color:var(--ink)}
.kidle{display:flex;align-items:center;gap:12px;width:100%;border:0;text-align:left;color:var(--soft);font-size:14px}.kidle svg{color:var(--teal);flex:none}
.icon-btn.play{color:var(--teal2)}
.badge.lib{background:#0B2A22;color:var(--green)}
.recent{display:flex;flex-wrap:wrap;gap:8px;margin:-2px 0 12px}.recent button{border:1px solid var(--line);background:transparent;color:var(--text);border-radius:18px;padding:0 13px;min-height:36px;font-size:13px;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.recent button.clear{color:var(--muted);border-color:transparent}
.np{position:relative;overflow:hidden}.np>*{position:relative;z-index:1}.np>.npbg{position:absolute;inset:0;z-index:0}.npbg img.art.loaded{opacity:.32}.npbg::after{content:"";position:absolute;inset:0;background:linear-gradient(90deg,var(--card) 25%,rgba(14,28,35,.55))}
.skel{background:var(--card);border-radius:14px;padding:14px;margin-bottom:10px}.skel i{display:block;height:12px;border-radius:6px;margin:8px 0;background:linear-gradient(90deg,#13262F 0%,#1B3540 50%,#13262F 100%);background-size:200% 100%;animation:sk 1.2s linear infinite}
@keyframes sk{from{background-position:200% 0}to{background-position:-200% 0}}
@media (prefers-reduced-motion:reduce){.skel i{animation:none}}
.rl{display:flex;flex-wrap:wrap;align-items:center;gap:5px;margin-top:5px;font-size:12px;color:var(--soft)}
.rc{font:700 11px var(--mono);padding:2px 6px;border-radius:5px;background:var(--hover);color:var(--text)}.rc.imdb{background:#F5C518;color:#000}.rc.tmdb{background:#0B2A3A;color:#7DD3FC}
.pthumb{width:46px;height:69px;border-radius:6px;border:0;padding:0;background:var(--hover);overflow:hidden;flex:none;cursor:pointer}
.qrow .info.tap,.nowrow .info.tap{cursor:pointer}
img.art{width:100%;height:100%;object-fit:cover;display:block;opacity:0;transition:opacity .25s}img.art.loaded{opacity:1}
.nowrow{display:flex;gap:12px;align-items:center}.nowrow .pthumb{width:52px;height:78px}.nowrow .info{flex:1;min-width:0}.nowrow .name{margin-top:4px}
.sheet{position:fixed;inset:0;z-index:20;background:rgba(0,0,0,.65);display:flex;align-items:flex-end;justify-content:center}
.sheet .panel{background:var(--bg);width:100%;max-width:720px;max-height:92vh;overflow-y:auto;border-radius:20px 20px 0 0;position:relative;padding-bottom:calc(22px + env(safe-area-inset-bottom))}
.fan{position:relative;aspect-ratio:16/9;background:var(--card);overflow:hidden;border:0;padding:0;width:100%;display:block}.fan::after{content:"";position:absolute;inset:0;background:linear-gradient(to bottom,rgba(6,13,16,0) 45%,var(--bg))}
.dhead{display:flex;gap:14px;padding:0 16px;margin-top:-64px;position:relative;z-index:1;align-items:flex-end}
.dposter{width:104px;height:156px;border-radius:10px;overflow:hidden;flex:none;background:var(--hover);border:0;padding:0;box-shadow:0 8px 24px rgba(0,0,0,.55);cursor:zoom-in}
.dtitle{min-width:0;padding-bottom:4px}.dtitle h2{margin:0;font-size:21px;line-height:1.2;letter-spacing:-.01em}.dtitle .m{font:500 12px var(--mono);color:var(--soft);margin-top:6px}
.chips{display:flex;flex-wrap:wrap;gap:6px;padding:14px 16px 0}.chip{font:600 12px var(--mono);padding:5px 9px;border-radius:7px;background:var(--card);color:var(--text);text-decoration:none}.chip.imdb{background:#F5C518;color:#000}
.dbody{padding:12px 16px 0}.tagline{font-style:italic;color:var(--soft);margin:0 0 8px}.plot{line-height:1.55;color:var(--text);margin:0 0 14px}
.facts{font-size:13px;color:var(--soft);line-height:1.6;margin-bottom:14px}.facts b{color:var(--white);font-weight:600}
.dclose{position:absolute;top:12px;right:12px;z-index:3;width:40px;height:40px;border-radius:20px;border:0;background:rgba(0,0,0,.6);color:#fff;display:flex;align-items:center;justify-content:center}
.viewer{position:fixed;inset:0;z-index:30;background:#000;display:flex;align-items:center;justify-content:center;border:0;padding:0;width:100%}.viewer img{max-width:100%;max-height:100%;object-fit:contain}
.rrow{display:flex;gap:8px;margin-top:8px}.rrow button,.remote button{flex:1;min-height:46px;border:0;border-radius:12px;background:var(--hover);color:var(--text);font-weight:600;font-size:13px;display:flex;align-items:center;justify-content:center}
.rrow button:active,.remote button:active{background:var(--line)}
.remote{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;max-width:270px;margin:12px auto 2px}.remote button{min-height:54px;font-size:18px}.remote .ok{background:var(--teal);color:var(--ink);font-size:14px}.remote .sm{font-size:12px;color:var(--soft)}
.rinfo{font:500 12px var(--mono);color:var(--soft);text-align:center;margin-top:10px;min-height:16px}
details.remotebox{background:var(--card);border-radius:14px;margin-bottom:12px}details.remotebox summary{list-style:none;cursor:pointer;padding:14px;display:flex;align-items:center;gap:10px;font-weight:600;font-size:14px}details.remotebox summary::-webkit-details-marker{display:none}details.remotebox .body{padding:0 14px 14px}details.remotebox summary .chev{margin-left:auto;color:var(--muted);transition:transform .2s}details.remotebox[open] summary .chev{transform:rotate(180deg)}
.cw{display:flex;gap:10px;overflow-x:auto;padding-bottom:6px;margin:0 -2px 8px;scroll-snap-type:x mandatory}
.citem{flex:none;width:136px;scroll-snap-align:start;background:var(--card);border-radius:12px;padding:8px;border:0;text-align:left;color:inherit;cursor:pointer}
.citem .ph{width:100%;aspect-ratio:2/3;border-radius:8px;overflow:hidden;background:var(--hover);margin-bottom:8px}
.citem .t{font-weight:600;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.citem .m{font:500 11px var(--mono);color:var(--soft);margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.citem .go{font:700 11px var(--mono);color:var(--teal);margin-top:6px}
details.season{border-top:1px solid #10242C}details.season summary{list-style:none;cursor:pointer;padding:12px 0;display:flex;align-items:center;gap:8px;font-weight:600;font-size:14px}details.season summary::-webkit-details-marker{display:none}details.season summary .m{margin-left:auto;font:500 11px var(--mono);color:var(--soft)}
.eprow{display:flex;align-items:center;gap:10px;padding:9px 0;border-top:1px solid #0E1F26}.eprow .n{font:600 12px var(--mono);color:var(--muted);width:30px;flex:none}.eprow .info{flex:1;min-width:0}.eprow .t{font-size:14px;overflow-wrap:anywhere}.eprow .m{font-size:12px;color:var(--soft);margin-top:2px}.eprow.w .t{color:var(--soft)}
.miss{background:#2E2410;color:var(--amber);border-radius:10px;padding:8px 10px 8px 12px;font-size:13px;margin:8px 0;display:flex;align-items:center;gap:10px}.miss span{flex:1;min-width:0;overflow-wrap:anywhere}.miss .btn{background:transparent;color:var(--amber);border:1px solid #5A4512;flex:none}
.badge.up{background:#0B2230;color:var(--sky)}
.rtop{display:flex;gap:12px;align-items:flex-start;margin-bottom:10px}.rtop .rbody{flex:1;min-width:0}.rtop .details{margin-bottom:0}
.rart{width:52px;height:78px;flex:none;border-radius:8px;position:relative;overflow:hidden;background:var(--deep);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:5px;color:var(--teal)}
.rart small{font:700 9px var(--mono);letter-spacing:.04em;color:var(--soft)}.rart img.art{position:absolute;inset:0}
.rart.k-tv,.rart.k-app{color:var(--sky)}.rart.k-music{color:var(--purple)}.rart.k-book{color:var(--amber)}.rart.k-game{color:var(--red)}.rart.k-package{color:var(--muted)}
.tiles.two{grid-template-columns:repeat(2,minmax(0,1fr))}
.drow{display:block;width:100%;border:0;background:transparent;color:inherit;text-align:left;padding:12px 0;border-top:1px solid #10242C;cursor:pointer}.drow:first-of-type{border-top:0;padding-top:2px}
.dl{display:flex;align-items:center;gap:10px}.dl .lt{font:700 15px var(--mono);width:30px;flex:none}.dl .nm{flex:1;min-width:0;font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.dl .nm small{font-weight:400;color:var(--soft)}
.dsub{display:flex;justify-content:space-between;gap:8px;font-size:12px;color:var(--soft);padding-left:40px}.dsub b{color:var(--white);font-weight:600}
.dbar{height:6px;background:var(--deep);border-radius:3px;overflow:hidden;margin:8px 0 6px 40px}.dbar span{display:block;height:100%;border-radius:3px;background:var(--teal)}
.tag.bad{background:#2E1215;color:var(--red)}.tag.unk{background:var(--deep);color:var(--muted)}
.dcard{background:var(--card);border-radius:14px;padding:14px;margin-bottom:12px}
.dcard .top{display:flex;align-items:center;gap:12px}.dcard .lt{font:800 20px var(--mono);min-width:34px}.dcard .nm{flex:1;min-width:0}.dcard .nm b{display:block;font-size:16px}.dcard .nm span{font-size:12px;color:var(--soft)}
.dstats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px;margin-top:10px}.dstats div{background:var(--deep);border-radius:9px;padding:8px 4px;text-align:center}.dstats b{display:block;font:700 14px var(--mono)}.dstats span{font-size:10px;color:var(--soft)}
.dnote{font-size:12px;color:var(--soft);margin-top:10px}.dnote.warn{color:#E9D9A8;background:#211B0B;border-radius:8px;padding:8px 10px}.dnote.bad{color:#FBC7C7;background:#2A1013;border-radius:8px;padding:8px 10px}
.banner{display:flex;gap:12px;align-items:flex-start;border-radius:14px;padding:14px;margin-bottom:12px}.banner.warn{background:#211B0B;border:1px solid #4A3B10}.banner.bad{background:#2A1013;border:1px solid #5B2328}.banner.ok{background:#0B2420;border:1px solid #14532D}
.banner b{display:block;font-size:15px}.banner.warn b{color:var(--amber)}.banner.bad b{color:var(--red)}.banner.ok b{color:var(--green)}.banner span{font-size:12px;color:var(--soft)}
.tgl{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 0;border-top:1px solid #10242C;min-height:48px}.tgl:first-child{border-top:0}.tgl input{width:22px;height:22px;accent-color:var(--teal);flex:none}.tgl small{display:block;color:var(--soft);font-size:12px}
.legend{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--soft);margin-top:8px}.legend i{display:inline-block;width:9px;height:9px;border-radius:5px;margin-right:5px}
.dio{display:flex;gap:12px;font:500 12px var(--mono);color:var(--soft);margin-top:10px;flex-wrap:wrap}.dio b{color:var(--white);font-weight:600}.dio .busy{margin-left:auto}
.dcard.tap{cursor:pointer;border:0;width:100%;text-align:left;color:inherit;display:block}.dcard .more{font-size:12px;color:var(--teal);margin-top:10px;font-weight:600}
.kv{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:0;font-size:13px}.kv div{padding:9px 0;border-top:1px solid #10242C}.kv div:nth-child(-n+2){border-top:0}.kv div:nth-child(odd){color:var(--soft)}.kv div:nth-child(even){text-align:right;font-family:var(--mono);font-weight:600;color:var(--white)}.kv .bad{color:var(--red)!important}.kv .warn{color:var(--amber)!important}.kv .good{color:var(--green)!important}
.vrow{padding:10px 0;border-top:1px solid #10242C}.vrow:first-child{border-top:0}.vrow .h{display:flex;justify-content:space-between;gap:8px;font-size:13px}.vrow .h span{color:var(--soft);font-family:var(--mono);font-size:12px}
.bar.big{height:14px;border-radius:7px}.bar.big span{border-radius:7px}
.ready{display:inline-block;font:700 13px var(--mono);color:var(--teal);margin-top:6px}
.hdrbtn{position:absolute;right:14px;top:calc(12px + env(safe-area-inset-top));width:44px;height:44px;border-radius:12px;border:0;background:transparent;color:var(--soft);display:flex;align-items:center;justify-content:center}
.hdrbtn.on{color:var(--teal)}
.arr{flex:none;width:136px;scroll-snap-align:start;background:var(--card);border-radius:12px;padding:8px}
.arr .ph{width:100%;aspect-ratio:2/3;border-radius:8px;overflow:hidden;background:var(--hover);margin-bottom:8px;position:relative}
.arr .ph .newtag{position:absolute;top:6px;left:6px;font:800 10px var(--mono);background:var(--teal);color:var(--ink);padding:2px 6px;border-radius:5px}
.arr .t{font-weight:600;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.arr .m{font:500 11px var(--mono);color:var(--soft);margin:3px 0 8px}
.arr .btn{width:100%;min-height:38px;padding:6px;font-size:12px}
.ptr{position:fixed;left:50%;top:calc(6px + env(safe-area-inset-top));z-index:8;transform:translate(-50%,-60px);background:var(--hover);border:1px solid var(--line);color:var(--text);border-radius:18px;padding:7px 14px;font-size:13px;font-weight:600;transition:transform .15s;pointer-events:none}
.ptr.show{transform:translate(-50%,var(--ptr,0px))}
.emptydl{display:flex;align-items:center;gap:14px}.emptydl svg{color:var(--teal);flex:none;opacity:.8}
.alfield{margin-bottom:12px}.altop{display:flex;gap:8px}.altop .field{margin:0}
.jgrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-bottom:12px}
.jq{background:var(--card);border:0;border-radius:14px;padding:12px;text-align:left;color:var(--white);display:flex;flex-direction:column;gap:5px;min-height:78px;cursor:pointer}
.jq b{font-size:14px}.jq span{font-size:12px;color:var(--soft)}.jq.main{background:var(--teal);color:var(--ink)}.jq.main span{color:#0B3B36}.jq.on{outline:2px solid var(--teal)}
.jq .ct{align-self:flex-start;font:700 11px var(--mono);background:var(--teal);color:var(--ink);padding:1px 7px;border-radius:9px}.jq .ct.warn{background:var(--amber)}
.qual{font:600 11px var(--mono);padding:3px 7px;border-radius:6px;background:var(--deep);color:var(--text)}.qual.lo{background:#2E2410;color:var(--amber)}
.jnow .meta{font:500 12px var(--mono);color:var(--soft);margin-top:3px}.tag.tx{background:#2E2410;color:var(--amber)}
.pills{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 4px}.pills button{border:1px solid var(--line);background:transparent;color:var(--text);border-radius:18px;padding:0 14px;min-height:38px;font-size:13px;font-weight:600}.pills button.on{background:var(--teal);color:var(--ink);border-color:var(--teal)}
.pov{font-size:12px;color:var(--muted);margin-top:5px;line-height:1.35;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.upw{font-size:12px;color:var(--teal);font-weight:600;margin-top:4px}
.uptag{flex:none;font:800 10px var(--mono);padding:3px 7px;border-radius:6px;background:var(--hover);color:var(--soft)}.uptag.mv{color:#C4B5FD}.uptag.tv{color:var(--teal)}
.chk{display:flex;gap:10px;align-items:flex-start;margin:4px 2px 14px;font-size:14px;cursor:pointer}.chk input{width:20px;height:20px;margin:1px 0 0;accent-color:var(--teal);flex:none}.chk small{display:block;color:var(--soft);font-size:12px;margin-top:2px}
.pvrhead .name{font-weight:600;font-size:17px;margin-top:4px;overflow-wrap:anywhere}.pvrhead .sub{font:500 12px var(--mono);color:var(--soft);margin-top:3px}
</style></head><body>
<header><div class="brand"><img src="/icon.png" alt="">NZB Command Center</div><button id="alertsbtn" class="hdrbtn hidden" aria-label="Phone alerts"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg></button><div id="statusbar" class="statusbar hidden"><span id="sabpill" class="spill"><i></i>SABnzbd</span></div></header>
<main>
<div id="pair" class="pair card"><h1>Connect</h1><p class="lede" style="margin-left:0">Mobile control for your PC · v__VERSION__</p><label class="label" for="token">Access code</label><input id="token" class="field" type="password" placeholder="Code shown on your PC" autocomplete="off" autocapitalize="off" autocorrect="off"><button class="btn full" id="pairbtn">Connect</button><div id="pairmsg" class="hint" style="margin-top:10px">The code is in the window running NZB Mobile Server. Opening its pairing link on this phone connects automatically.</div></div>
<div id="app" class="hidden">
<section id="home" class="hidden">
<h1 id="greet">Hello</h1><p class="lede" id="homestatus">Loading…</p>
<div id="homedl"></div>
<div class="tiles"><div class="tile"><div class="k">Done today</div><div class="v" id="hdone">—</div></div><div class="tile"><div class="k">Free space</div><div class="v" id="hfree">—</div></div><div class="tile"><div class="k">Failed today</div><div class="v" id="hfail">—</div></div></div>
<div class="tiles" id="homeusage"><div class="tile"><div class="k">Today</div><div class="v" id="husday">—</div></div><div class="tile"><div class="k">This week</div><div class="v" id="husweek">—</div></div><div class="tile"><div class="k">This month</div><div class="v" id="husmonth">—</div></div></div>
<button class="shortcut" data-tab="search"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>Search your indexers…</button>
<div id="homekodi"></div>
<div id="homejf"></div>
<div id="homearrived"></div>
<div id="homeup"></div>
<div id="homedrives"></div>
<p class="hint" id="homever" style="text-align:center;margin:22px 0 4px"></p>
</section>
<section id="jellyfin" class="hidden">
<div class="row" style="align-items:flex-end"><div><h1>Jellyfin</h1><p class="lede" id="jfstatus">Loading…</p></div><button class="btn ghost sm hidden" id="jfscan" style="flex:none;margin-bottom:14px">Scan libraries</button></div>
<div id="jfnow"></div>
<div id="jfbody"></div>
</section>
<section id="drives" class="hidden">
<div class="row" style="align-items:flex-end"><div><h1>Drives</h1><p class="lede" id="drivestatus">Loading…</p></div><button class="btn ghost sm" id="drivecheck" style="flex:none;margin-bottom:14px">Check now</button></div>
<div id="drivebody"></div>
</section>
<section id="search" class="hidden">
<h1>Search</h1><p class="lede">Your indexers, from anywhere at home.</p>
<div class="seg"><button class="on" data-tab="search">Indexers</button><button data-tab="pvr">Movies &amp; shows</button></div>
<div class="card">
<label class="search"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#7F9DA6" stroke-width="2.2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg><input id="query" placeholder="Movie, show or release" enterkeyhint="search" autocomplete="off" aria-label="Search"></label>
<div class="row"><select id="indexer" class="field" aria-label="Indexer"><option value="*">All indexers</option></select><select id="category" class="field" aria-label="Category"><option value="">All categories</option></select></div>
<div class="row"><button class="btn" id="searchbtn">Search</button></div>
<p class="hint" style="margin:10px 2px 0">Tip: “Show S02E04” or “Show S02” searches by episode or season.</p>
</div>
<div id="recent" class="recent"></div>
<div id="searchstatus" class="status"></div>
<details class="opts" id="resulttools" style="display:none"><summary><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 6h16M7 12h10M10 18h4"/></svg>Sort, filter &amp; send options<svg class="chev" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></summary><div class="body">
<label class="label" for="sort">Sort</label><select id="sort" class="field"><option value="date_desc">Newest first</option><option value="size_desc">Largest first</option><option value="size_asc">Smallest first</option><option value="date_asc">Oldest first</option><option value="grabs_desc">Most grabs</option><option value="title_asc">Title A–Z</option><option value="title_desc">Title Z–A</option></select>
<div class="row"><div><label class="label" for="minsize">Min size (MB)</label><input id="minsize" class="field" type="number" min="0" placeholder="Any"></div><div><label class="label" for="maxsize">Max size (MB)</label><input id="maxsize" class="field" type="number" min="0" placeholder="Any"></div></div>
<label class="label" for="mingrabs">Min grabs</label><input id="mingrabs" class="field" type="number" min="0" placeholder="Any">
<div class="row"><div><label class="label" for="sabcat">SABnzbd category</label><select id="sabcat" class="field"><option value="">Auto (match)</option></select></div><div><label class="label" for="prio">Priority</label><select id="prio" class="field"><option value="">Default</option><option value="-1">Low</option><option value="0">Normal</option><option value="1">High</option><option value="2">Force</option></select></div></div>
<div class="row"><button class="btn ghost sm" id="selvisible">Select visible</button><button class="btn ghost sm" id="selnone">Clear selection</button></div>
<p class="hint" style="margin:10px 0 0">Up to 10 at a time. Releases found on several indexers are grouped, and a failed download falls back to the other indexer automatically.</p>
</div></details>
<div id="results"></div>
</section>
<section id="pvr" class="hidden">
<h1>Movies &amp; shows</h1><p class="lede" id="pvrstatus">Loading…</p>
<div class="seg"><button data-tab="search">Indexers</button><button class="on" data-tab="pvr">Movies &amp; shows</button></div>
<div id="pvrbody"></div>
</section>
<section id="queue" class="hidden">
<h1>Queue</h1><p class="lede" id="queuestatus">Loading…</p>
<div class="seg"><button class="on" data-tab="queue">Active</button><button data-tab="history">History</button></div>
<div id="queueitems"></div>
<div class="section">CONTROLS</div>
<div class="card"><div class="row"><button id="qresume" class="btn sm">Resume all</button><button id="qpause" class="btn ghost sm">Pause all</button></div>
<div class="row" style="margin-top:10px"><input id="speedmb" class="field" style="margin:0" type="number" min="0" step="0.5" placeholder="Limit in MB/s (0 = none)" aria-label="Speed limit in MB/s"><button id="speedset" class="btn ghost sm" style="flex:none">Set limit</button></div></div>
</section>
<section id="history" class="hidden">
<div class="row" style="align-items:flex-end"><div><h1>History</h1><p class="lede" id="histstatus">Recent downloads</p></div><button class="icon-btn" id="histrefresh" aria-label="Refresh history" style="flex:none;margin-bottom:14px"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/></svg></button></div>
<div class="seg" style="margin-top:0"><button data-tab="queue">Active</button><button class="on" data-tab="history">History</button></div>
<div id="historyitems"></div>
</section>
<section id="kodi" class="hidden">
<div class="row" style="align-items:flex-end"><div><h1>Kodi</h1><p class="lede" id="kodistatus">Loading…</p></div><button class="btn ghost sm" id="kodiscan" style="flex:none;margin-bottom:14px">Scan library</button></div>
<div id="tvbox" class="hidden">
<div class="row" id="tvbtns"><button class="btn" id="tvon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true" style="vertical-align:-3px;margin-right:6px"><path d="M12 3v9M6.3 6.3a8 8 0 1 0 11.4 0"/></svg>TV on + Kodi</button><button class="btn ghost" id="tvkodi">Open Kodi</button></div>
<p class="hint" id="tvmsg" style="margin:6px 4px 10px"></p>
<details class="opts" id="tvsetup"><summary><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="2" y="5" width="20" height="13" rx="2"/><path d="M8 21h8"/></svg>TV setup<svg class="chev" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></summary><div class="body">
<p class="hint" style="margin:0 0 10px">On the Sony TV: <b>Settings › Network › Remote start › On</b>, then <b>Settings › Network › Home network › IP control › Authentication › “Normal and Pre-Shared Key”</b> and set a <b>Pre-Shared Key</b>.</p>
<label class="label" for="tvhost">TV address</label><input id="tvhost" class="field" placeholder="192.168.1.40" autocomplete="off" autocapitalize="off" inputmode="url">
<label class="label" for="tvpsk">Pre-Shared Key</label><input id="tvpsk" class="field" type="password" placeholder="The key you set on the TV" autocomplete="off" autocapitalize="off">
<label class="label" for="tvmac">TV network (MAC) address <span style="color:var(--muted)">· optional, learned automatically</span></label><input id="tvmac" class="field" placeholder="AA:BB:CC:DD:EE:FF" autocomplete="off" autocapitalize="characters">
<button class="btn full" id="tvsave">Save &amp; test</button>
</div></details>
</div>
<div id="kodinow"></div>
<details class="remotebox hidden" id="kodiremote"><summary><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="7" y="2" width="10" height="20" rx="3"/><circle cx="12" cy="8" r="2"/><path d="M10 14h4M10 17h4"/></svg>Remote<svg class="chev" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></summary><div class="body">
<div class="rrow"><button data-kact="seek_back" aria-label="Back 15 seconds">−15s</button><button data-kact="playpause" aria-label="Play or pause">Play / Pause</button><button data-kact="seek_fwd" aria-label="Forward 30 seconds">+30s</button></div>
<div class="rrow"><button data-kact="subs_next">Subtitles</button><button data-kact="subs_off">Subs off</button><button data-kact="audio_next">Audio track</button></div>
<div class="rrow"><button data-kact="vol_down" aria-label="Volume down">Vol −</button><button data-kact="mute">Mute</button><button data-kact="vol_up" aria-label="Volume up">Vol +</button></div>
<div class="remote"><button class="sm" data-kact="home">Home</button><button data-kact="up" aria-label="Up">▲</button><button class="sm" data-kact="info">Info</button><button data-kact="left" aria-label="Left">◀</button><button class="ok" data-kact="select">OK</button><button data-kact="right" aria-label="Right">▶</button><button class="sm" data-kact="back">Back</button><button data-kact="down" aria-label="Down">▼</button><button class="sm" data-kact="menu">Menu</button></div>
<div class="rinfo" id="kodirinfo"></div>
</div></details>
<div class="card hidden" id="kodytype"><label class="kode" for="kodytext" style="display:block;margin-bottom:8px">TYPE ON TV</label>
<input id="kodytext" class="field" style="margin-bottom:8px" placeholder="Text for the box open on the TV" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" enterkeyhint="send" maxlength="500">
<div class="row"><button class="btn sm" id="kodysend">Send &amp; OK</button><button class="btn ghost sm" id="kodysendonly">Send only</button></div>
<p class="hint" style="margin:8px 2px 0">Select a text box on the TV first (a search, an address, a password). What you send replaces what's in it. “Send &amp; OK” also presses OK on Kodi's keyboard.</p></div>
<div class="seg hidden" id="kodiseg"><button class="on" data-kmode="movies">Movies</button><button data-kmode="tv">TV shows</button></div>
<div id="kodiTV" class="hidden">
<div class="section" id="cwtitle">CONTINUE WATCHING</div>
<div class="cw" id="kodicw"></div>
<label class="search"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#7F9DA6" stroke-width="2.2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg><input id="tvfilter" placeholder="Find a show" autocomplete="off" enterkeyhint="search" aria-label="Find a show"></label>
<div class="row" style="margin:4px 0 8px"><select id="tvview" class="field" style="margin:0" aria-label="Show"><option value="all">All shows (A–Z)</option><option value="recent">Recently watched</option><option value="unwatched">Unwatched episodes</option><option value="added">Newest added</option><option value="missing">Missing episodes</option></select></div>
<div class="section" id="tvlisttitle">SHOWS</div>
<div id="tvlist"></div>
<button class="btn ghost full hidden" id="tvmore" style="margin-top:4px">Show more</button>
</div>
<div id="kodilib" class="hidden">
<label class="search"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#7F9DA6" stroke-width="2.2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg><input id="kodifilter" placeholder="Find a movie or genre" autocomplete="off" enterkeyhint="search" aria-label="Find a movie"></label>
<div class="row" style="margin:4px 0 8px"><select id="kodiview" class="field" style="margin:0" aria-label="Show"><option value="recent">Recently added</option><option value="all">All movies (A–Z)</option><option value="newest">All, newest added first</option><option value="year">All, by release year</option><option value="rated">Highest rated</option><option value="unwatched">Unwatched</option><option value="progress">In progress</option></select></div>
<div class="section" id="kodilisttitle">RECENTLY ADDED</div>
<div id="kodilist"></div>
<button class="btn ghost full hidden" id="kodimore" style="margin-top:4px">Show more</button>
</div>
</section>
</div></main>
<div id="batchbar" class="batch hidden"><span id="batchcount">0 selected</span><button id="batchbtn" class="btn sm">Add to SABnzbd</button></div>
<nav class="tabs hidden" id="tabbar" aria-label="Sections">
<button id="tabhome" class="tab active" data-tab="home"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/></svg>Home</button>
<button id="tabsearch" class="tab" data-tab="search"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>Search</button>
<button id="tabqueue" class="tab" data-tab="queue"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 3v12M7 10l5 5 5-5M5 20h14"/></svg>Queue<span id="queuecount" class="count hidden"></span></button>
<button id="tabkodi" class="tab" data-tab="kodi"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M10 9l5 3-5 3z"/></svg>Kodi</button>
<button id="tabjellyfin" class="tab" data-tab="jellyfin"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l9.5 17h-19z"/><path d="M12 10l4 7H8z"/></svg>Jellyfin</button>
<button id="tabdrives" class="tab" data-tab="drives"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 14h18"/><circle cx="17" cy="17" r=".8" fill="currentColor"/><path d="M7 17h4"/></svg>Drives</button>
</nav>
<div id="toast" class="toast hidden" role="status"></div>
<div id="sheet" class="sheet hidden" role="dialog" aria-modal="true" aria-label="Movie details"><div class="panel" id="sheetpanel"></div></div>
<button id="viewer" class="viewer hidden" aria-label="Close picture"><img id="viewerimg" alt=""></button>
<script>
'use strict';
const LS={get:(k,d='')=>{try{const v=localStorage.getItem(k);return v===null?d:v}catch{return d}},set:(k,v)=>{try{localStorage.setItem(k,v)}catch{}}};
const $=id=>document.getElementById(id);
let token=LS.get('nzb_mobile_token');
let results=[],selected=new Set(),added=new Set(),activeTab='home',queueBusy=false,toastTimer=null,searchNote='',sabCats=[],lastQueue=[],kodiPid=-1;
const ICON={pause:'<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true"><path d="M8 5v14M16 5v14"/></svg>',play:'<svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 5l12 7-12 7z"/></svg>',trash:'<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg>',retry:'<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/></svg>'};
ICON.top='<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M5 4h14M12 20V9M7 13l5-5 5 5"/></svg>';
ICON.more='<svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>';
ICON.x='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
const EMPTY_ICON='<svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M12 3v12M7 10l5 5 5-5M5 20h14"/></svg>';

(function pairFromLink(){const m=(location.hash||'').match(/^#k=([A-Za-z0-9_-]+)$/);if(m){token=m[1];LS.set('nzb_mobile_token',token);try{history.replaceState(null,'',location.pathname)}catch{}}})();

function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function clampPct(v){return Math.max(0,Math.min(100,Number(v)||0))}
function toast(msg,isErr){const t=$('toast');t.textContent=msg;t.className='toast'+(isErr?' err':'');clearTimeout(toastTimer);toastTimer=setTimeout(()=>{t.className='toast hidden'},isErr?6000:3500)}
function skeleton(n,lines){let h='';for(let i=0;i<n;i++)h+='<div class="skel">'+'<i style="width:'+(85-i*7)+'%"></i><i style="width:55%"></i>'+(lines>2?'<i style="width:35%"></i>':'')+'</div>';return h}
function empty(title,hint){return '<div class="empty">'+EMPTY_ICON+'<b>'+esc(title)+'</b>'+esc(hint)+'</div>'}

const API_TIMEOUTS={'/api/home/play':150000,'/api/arr/add':90000,'/api/jellyfin/missing':90000,'/api/jellyfin/upgrades':60000,'/api/jellyfin/connect':30000,'/api/drives':70000,'/api/drives/check':70000,'/api/search':90000,'/api/add':120000,'/api/add-batch':300000};
async function api(path,body){
  const opt={method:body===undefined?'GET':'POST',headers:{'X-Mobile-Token':token}};
  if(body!==undefined){opt.headers['Content-Type']='application/json';opt.body=JSON.stringify(body)}
  // Without a timeout a dropped Wi-Fi connection left buttons stuck on "Searching…"/"Adding…" forever.
  const ctl=new AbortController(),timer=setTimeout(()=>ctl.abort(),API_TIMEOUTS[path]||30000);opt.signal=ctl.signal;
  let r;try{r=await fetch(path,opt)}catch(e){clearTimeout(timer);throw new Error(e&&e.name==='AbortError'?'The server took too long to answer. Check the queue before retrying an add.':'Cannot reach the server. Is the PC on and NZB Mobile Server running?')}
  clearTimeout(timer);
  let d={};try{d=await r.json()}catch{}
  if(r.status===401){showPair(d.error||'The access code was not accepted.');throw new Error(d.error||'Unauthorized')}
  if(!r.ok||d.error)throw new Error(d.error||('HTTP '+r.status));
  return d;
}
function showPair(msg){$('alertsbtn').classList.add('hidden');$('app').classList.add('hidden');$('tabbar').classList.add('hidden');$('batchbar').classList.add('hidden');$('pair').classList.remove('hidden');$('token').value='';if(msg)$('pairmsg').textContent=msg}
function pair(){const v=$('token').value.trim();if(!v)return;token=v;LS.set('nzb_mobile_token',token);connect()}

async function connect(){
  try{
    const d=await api('/api/status');
    $('pair').classList.add('hidden');$('app').classList.remove('hidden');$('tabbar').classList.remove('hidden');$('alertsbtn').classList.remove('hidden');
    const sel=$('indexer');sel.innerHTML='<option value="*">All indexers</option>'+d.indexers.map(x=>'<option value="'+esc(x.id)+'">'+esc(x.name)+'</option>').join('');
    catGroups=d.categories||{};
    loadPrefs();fillSearchCategories(JSON.parse(LS.get('nzb_mobile_prefs','{}')||'{}').category);
    api('/api/sab/info').then(i=>fillCategories(i.categories||[])).catch(()=>{});
    showTab(activeTab);pollStatus();
  }catch(e){if($('app').classList.contains('hidden'))$('pairmsg').textContent=e.message}
}
let catGroups={};
function fillSearchCategories(keep){
  const sel=$('category'),cur=keep!==undefined?keep:sel.value,groups=catGroups[$('indexer').value]||catGroups['*']||[];
  let h='<option value="">All categories</option>';
  for(const g of groups){
    if(!g.children||!g.children.length){h+='<option value="'+esc(g.id)+'">'+esc(g.name)+'</option>';continue}
    h+='<optgroup label="'+esc(g.name)+'"><option value="'+esc(g.id)+'">All '+esc(g.name)+'</option>'+g.children.map(c=>'<option value="'+esc(c.id)+'">'+esc(c.name)+'</option>').join('')+'</optgroup>';
  }
  sel.innerHTML=h;sel.value=[...sel.options].some(o=>o.value===cur)?cur:'';
}
function fillCategories(cats){sabCats=cats;const sel=$('sabcat'),keep=LS.get('nzb_mobile_cat');sel.innerHTML='<option value="">Auto (match)</option><option value="*">Default</option>'+cats.map(c=>'<option value="'+esc(c)+'">'+esc(c)+'</option>').join('');if(keep==='*'||cats.includes(keep))sel.value=keep}

function showTab(t){activeTab=t;for(const n of ['home','search','pvr','queue','history','kodi','drives','jellyfin'])$(n).classList.toggle('hidden',n!==t);const tab=t==='history'?'queue':(t==='pvr'?'search':t);document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));updateBatchButton();window.scrollTo(0,0);if(t==='home')refreshHome();if(t==='queue')refreshQueue();if(t==='history')refreshHistory();if(t==='kodi')refreshKodi();if(t==='drives'){refreshDrives();refreshDriveIO();setTimeout(()=>{if(activeTab==='drives')refreshDriveIO()},3500)}if(t==='jellyfin')refreshJellyfin();if(t==='pvr')refreshPvr()}

function savePrefs(){LS.set('nzb_mobile_prefs',JSON.stringify({sort:$('sort').value,minsize:$('minsize').value,maxsize:$('maxsize').value,mingrabs:$('mingrabs').value,indexer:$('indexer').value,prio:$('prio').value,category:$('category').value}));LS.set('nzb_mobile_cat',$('sabcat').value)}
function loadPrefs(){try{const p=JSON.parse(LS.get('nzb_mobile_prefs','{}'));if(p.sort)$('sort').value=p.sort;if(p.prio!==undefined)$('prio').value=p.prio;if(p.indexer&&[...$('indexer').options].some(o=>o.value===p.indexer))$('indexer').value=p.indexer;['minsize','maxsize','mingrabs'].forEach(k=>{if(p[k]!==undefined)$(k).value=p[k]})}catch{}}

function filterSort(list,f){
  const min=(Number(f.min)||0)*1048576,max=(Number(f.max)||0)*1048576,grabs=Number(f.grabs)||0;
  const a=list.filter(x=>(!min||x.size_bytes>=min)&&(!max||x.size_bytes<=max)&&(!grabs||(Number(x.grabs)||0)>=grabs));
  const by={size_asc:(x,y)=>x.size_bytes-y.size_bytes,size_desc:(x,y)=>y.size_bytes-x.size_bytes,date_asc:(x,y)=>(x.pub_ts||0)-(y.pub_ts||0),date_desc:(x,y)=>(y.pub_ts||0)-(x.pub_ts||0),grabs_desc:(x,y)=>(Number(y.grabs)||0)-(Number(x.grabs)||0),title_asc:(x,y)=>x.title.localeCompare(y.title),title_desc:(x,y)=>y.title.localeCompare(x.title)};
  return a.sort(by[f.mode]||by.date_desc);
}
function currentFilter(){return {min:$('minsize').value,max:$('maxsize').value,grabs:$('mingrabs').value,mode:$('sort').value}}
function addOptions(){return {category:$('sabcat').value,priority:$('prio').value}}
function updateBatchButton(){const n=selected.size;$('batchcount').textContent=n+' selected'+(n>10?' (max 10)':'');$('batchbtn').disabled=n===0||n>10;$('batchbar').classList.toggle('hidden',n===0||activeTab!=='search')}

function relAge(ts){if(!ts)return '';const s=Date.now()/1000-ts;if(s<3600)return Math.max(1,Math.round(s/60))+'m ago';if(s<86400)return Math.round(s/3600)+'h ago';if(s<86400*30)return Math.round(s/86400)+'d ago';return new Date(ts*1000).toLocaleDateString()}
function qualityTags(title){const t=String(title),out=[];const q=t.match(/\b(2160p|1080p|720p)\b/i);if(q)out.push(q[1].toLowerCase());if(/\b(dv|dovi|dolby.?vision)\b/i.test(t))out.push('DV');else if(/\bhdr/i.test(t))out.push('HDR');if(/\b(x265|h\.?265|hevc)\b/i.test(t))out.push('HEVC');else if(/\b(x264|h\.?264)\b/i.test(t))out.push('x264');if(/\bremux\b/i.test(t))out.push('REMUX');else if(/\b(bluray|bdrip)\b/i.test(t))out.push('BluRay');else if(/\bweb/i.test(t))out.push('WEB');return out.slice(0,4)}
// Placeholder "clipart" per kind of release (shown until, or instead of, a poster) - same kinds as the desktop app.
const KI=p=>'<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+p+'</svg>';
const KINDS={
  movie:[KI('<rect x="3" y="6" width="18" height="14" rx="2"/><path d="M3 10h18M7 6l2 4M12 6l2 4M17 6l2 4"/>'),'MOVIE'],
  tv:[KI('<rect x="3" y="7" width="18" height="12" rx="2"/><path d="M8 3l4 4 4-4"/>'),'TV'],
  music:[KI('<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>'),'MUSIC'],
  book:[KI('<path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z"/><path d="M5 17a3 3 0 0 1 3-3h11"/>'),'BOOK'],
  game:[KI('<rect x="2" y="7" width="20" height="11" rx="5"/><path d="M7 11v3M5.5 12.5h3"/><circle cx="16" cy="11.5" r=".8"/><circle cx="18" cy="13.5" r=".8"/>'),'GAME'],
  app:[KI('<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4M9 9l-2 2 2 2M15 9l2 2-2 2"/>'),'APP'],
  package:[KI('<path d="M3 7l9-4 9 4v10l-9 4-9-4z"/><path d="M3 7l9 4 9-4M12 11v10"/>'),'NZB']
};
function resultCard(x){
  const done=added.has(x.id);
  const details=[x.indexer,x.size,(x.grabs||0)+' grabs',relAge(x.pub_ts)||x.date].filter(Boolean).map(esc).join(' · ');
  let badges=qualityTags(x.title).map(t=>'<span class="badge">'+esc(t)+'</span>').join('');
  if(x.in_library)badges='<span class="badge lib">'+(x.in_library==='episode'?'Episode already in Kodi':'Already in Kodi')+'</span>'+badges;
  if(x.also_found&&x.also_found.length)badges+='<span class="badge purple">Also on '+esc(x.also_found.join(', '))+'</span>';
  if(upgradeFrom&&isUpgrade(x.title))badges='<span class="badge up">Better than your '+esc(upgradeFrom.q||'copy')+'</span>'+badges;
  const kind=KINDS[x.kind]?x.kind:'package';
  const art='<div class="rart k-'+kind+'" role="img" aria-label="'+esc(KINDS[kind][1]+(x.res?' '+x.res:''))+'">'+KINDS[kind][0]+'<small>'+esc(x.res||KINDS[kind][1])+'</small>'+(x.art?'<img class="art" data-art="r:'+esc(x.id)+'" alt="">':'')+'</div>';
  return '<article class="result" data-id="'+esc(x.id)+'"><label class="check" aria-label="Select"><input type="checkbox" data-act="sel"'+(selected.has(x.id)?' checked':'')+(done?' disabled':'')+'></label><div class="rtop">'+art+'<div class="rbody"><h3>'+esc(x.title)+'</h3><div class="details">'+details+'</div></div></div>'+(badges?'<div class="badges">'+badges+'</div>':'')+'<button class="btn full'+(done?' added':'')+'" data-act="add"'+(done?' disabled':'')+'>'+(done?'Added ✓':'Add to SABnzbd')+'</button></article>';
}
function renderResults(){
  $('resulttools').style.display=results.length?'block':'none';
  const a=filterSort(results,currentFilter());
  if(results.length)$('searchstatus').textContent='Showing '+a.length+' of '+results.length+' result(s)'+searchNote;
  $('results').innerHTML=a.map(resultCard).join('')||(results.length?empty('Nothing matches these filters','Clear the size or grabs limits to see all '+results.length+' result(s).'):'');
  hydrateArt($('results'));updateBatchButton();
}
function recentList(){try{const a=JSON.parse(LS.get('nzb_mobile_recent','[]'));return Array.isArray(a)?a:[]}catch{return []}}
function rememberSearch(){
  const q=$('query').value.trim();if(!q)return;
  const e={q,ix:$('indexer').value,cat:$('category').value};
  const a=[e].concat(recentList().filter(x=>x.q.toLowerCase()!==q.toLowerCase())).slice(0,8);
  LS.set('nzb_mobile_recent',JSON.stringify(a));renderRecent();
}
function renderRecent(){
  const a=recentList();
  $('recent').innerHTML=a.length?a.map((x,i)=>'<button data-recent="'+i+'" aria-label="Search again for '+esc(x.q)+'">'+esc(x.q)+'</button>').join('')+'<button class="clear" data-recent="clear">Clear</button>':'';
}
function useRecent(i){
  if(i==='clear'){LS.set('nzb_mobile_recent','[]');renderRecent();return}
  const x=recentList()[Number(i)];if(!x)return;
  $('query').value=x.q;
  if([...$('indexer').options].some(o=>o.value===x.ix)){$('indexer').value=x.ix;fillSearchCategories(x.cat)}
  search();
}
// ---------- "Find a better copy" ----------
let upgradeFrom=null;
const RES_RANK={'2160p':4,'1080p':3,'720p':2,'SD':1};
function resOf(title){const m=String(title).match(/\b(2160p|1080p|720p)\b/i);return m?m[1].toLowerCase():(/\b(4k|uhd)\b/i.test(title)?'2160p':'')}
function isUpgrade(title){
  if(!upgradeFrom||!RES_RANK[upgradeFrom.q])return false;  // unknown quality of your copy: don't guess
  const have=RES_RANK[upgradeFrom.q],res=RES_RANK[resOf(title)]||0;
  if(res>have)return true;
  return res===have&&res>0&&!upgradeFrom.hdr&&/\b(hdr|dv|dovi|dolby.?vision)\b/i.test(title);
}
function goSearch(q,opts){
  opts=opts||{};showTab('search');$('query').value=q;
  // Use the requested category; if this indexer doesn't offer it, search all categories rather
  // than silently keeping an unrelated one (e.g. a movie search inside "TV > HD").
  if(opts.category!==undefined)$('category').value=[...$('category').options].some(o=>o.value===opts.category)?opts.category:'';
  search(opts);
}
async function search(extra){
  extra=(extra&&!(extra instanceof Event))?extra:{};
  if(!extra.keepUpgrade)upgradeFrom=null;
  searchNote='';
  const st=$('searchstatus');st.className='status';st.textContent='Searching…';$('results').innerHTML=skeleton(3,3);rememberSearch();results=[];selected.clear();added.clear();updateBatchButton();
  $('searchbtn').disabled=true;$('query').blur();
  try{
    const d=await api('/api/search',{query:$('query').value.trim(),indexer:$('indexer').value,category:$('category').value,imdbid:extra.imdbid||''});
    results=d.results||[];
    searchNote=(d.mode==='tvsearch'?' · episode search':(d.mode==='movie'?' · matched by IMDb id':''));
    if(upgradeFrom)searchNote+=' · '+results.filter(x=>isUpgrade(x.title)).length+' better than your '+(upgradeFrom.q||'copy');
    if(d.errors&&d.errors.length)searchNote+=' · '+d.errors.length+' indexer error(s)\n'+d.errors.join('\n');
    let msg=results.length+' unique result(s)'+searchNote;
    st.textContent=msg;if(d.errors&&d.errors.length&&!results.length)st.className='status error';
    renderResults();
    if(!results.length&&!(d.errors&&d.errors.length))$('results').innerHTML=empty('No releases found','Try a shorter title or a different category.');
  }catch(e){st.textContent=e.message;st.className='status error'}
  finally{$('searchbtn').disabled=false}
}
async function addResult(id,btn){
  btn.disabled=true;btn.textContent='Adding…';
  try{const d=await api('/api/add',Object.assign({id},addOptions()));added.add(id);selected.delete(id);toast('Added to SABnzbd'+(d.category?' → '+d.category:'')+(d.indexer?' (from '+d.indexer+')':''));renderResults();refreshQueue(true)}
  catch(e){btn.disabled=false;btn.textContent='Add to SABnzbd';toast(e.message,true)}
}
async function addSelected(){
  const ids=[...selected];if(!ids.length||ids.length>10)return;
  const b=$('batchbtn');b.disabled=true;b.textContent='Adding…';
  try{
    const d=await api('/api/add-batch',Object.assign({ids},addOptions()));
    (d.added||[]).forEach(x=>{added.add(x.id);selected.delete(x.id)});
    let msg='Added '+(d.added||[]).length+' of '+ids.length+' NZB(s).';
    if(d.errors&&d.errors.length)msg+='\n'+d.errors.map(x=>(x.title||x.id)+': '+x.error).join('\n');
    toast(msg,!!(d.errors&&d.errors.length));renderResults();refreshQueue(true);
  }catch(e){toast(e.message,true)}
  finally{b.textContent='Add to SABnzbd';updateBatchButton()}
}

function stateTag(status){const s=String(status||'').toLowerCase();const cls=s==='downloading'?'ok':(s==='paused'?'warn':'busy');return '<span class="tag '+cls+'">'+esc(String(status||'Queued').toUpperCase())+'</span>'}
function heroCard(x){
  const p=clampPct(x.percentage),paused=String(x.status).toLowerCase()==='paused';
  return '<div class="hero queueitem" data-id="'+esc(x.id)+'">'+stateTag(x.status)+'<div class="name">'+esc(x.name)+'</div><div class="sub">'+esc(x.sizeleft)+' of '+esc(x.size)+' left · ETA '+esc(x.eta||'—')+(x.cat&&x.cat!=='*'?' · '+esc(x.cat):'')+'</div><div class="bar big"><span style="width:'+p+'%"></span></div>'+(!paused&&readyAt(x.eta)?'<div class="ready" style="margin:0 0 8px">'+esc(readyAt(x.eta))+'</div>':'')+'<div class="row"><span style="flex:1;font:600 14px var(--mono);color:var(--teal)">'+p.toFixed(0)+'%</span><button class="icon-btn" data-act="more" aria-label="Category and priority">'+ICON.more+'</button><button class="btn ghost sm" style="flex:none" data-act="'+(paused?'resume':'pause')+'">'+(paused?'Resume':'Pause')+'</button><button class="btn danger sm" style="flex:none" data-act="delete">Delete</button></div></div>';
}
function queueRow(x){
  const p=clampPct(x.percentage),paused=String(x.status).toLowerCase()==='paused';
  return '<div class="qrow queueitem" data-id="'+esc(x.id)+'"><div class="info"><div class="t">'+esc(x.name)+'</div><div class="m">'+esc(String(x.status||'').toUpperCase())+' · '+esc(x.size)+(x.eta?' · '+esc(x.eta):'')+(!paused&&readyAt(x.eta)?' · '+esc(readyAt(x.eta)):'')+(x.cat&&x.cat!=='*'?' · '+esc(x.cat):'')+'</div><div class="bar thin"><span style="width:'+p+'%"></span></div></div><button class="icon-btn" data-act="more" aria-label="More options: move to top, category, priority">'+ICON.more+'</button><button class="icon-btn" data-act="'+(paused?'resume':'pause')+'" aria-label="'+(paused?'Resume':'Pause')+'">'+(paused?ICON.play:ICON.pause)+'</button><button class="icon-btn red" data-act="delete" aria-label="Delete">'+ICON.trash+'</button></div>';
}
async function refreshQueue(silent){
  if(queueBusy)return;queueBusy=true;const st=$('queuestatus');
  try{
    const d=await api('/api/queue');
    st.className='lede';
    st.textContent=(d.total?d.total+' in queue':'Queue is empty')+(d.speed_limit?' · limit '+d.speed_limit:'');
    updateStatus(d);
    const slots=d.slots||[];
    const free=d.disk_free_gb!=null&&d.disk_free_gb>=0?Number(d.disk_free_gb).toFixed(0):'—';
    const sp=String(d.speed||'0 B/s').split(' ');
    let html='<div class="tiles"><div class="tile"><div class="k">Speed</div><div class="v" style="color:var(--teal)">'+esc(sp[0])+' <small>'+esc(sp.slice(1).join(' '))+'</small></div></div><div class="tile"><div class="k">Queued</div><div class="v">'+esc(d.total||0)+'</div></div><div class="tile"><div class="k">Free space</div><div class="v">'+esc(free)+' <small>GB</small></div></div></div>';
    if(!slots.length){html+=empty('Nothing downloading','Search for something and add it to SABnzbd.')}
    else{
      const hi=Math.max(0,slots.findIndex(s=>String(s.status).toLowerCase()==='downloading'));
      html+=heroCard(slots[hi]);
      const rest=slots.filter((_,i)=>i!==hi);
      if(rest.length)html+='<div class="section">UP NEXT</div>'+rest.map(queueRow).join('');
    }
    lastQueue=slots;$('queueitems').innerHTML=html;
  }catch(e){if(!silent||activeTab==='queue'){st.textContent=e.message;st.className='lede status error'}}
  finally{queueBusy=false}
}
function updateStatus(d){
  const pill=$('sabpill'),state=String(d.state||'Idle'),low=state.toLowerCase();
  pill.className='spill '+(low==='paused'?'warn':(low==='downloading'?'ok':(d.total?'busy':'')));
  pill.innerHTML='<i></i>'+esc(state)+' · '+esc(d.speed||'0 B/s')+(d.total?' · '+esc(d.total)+' queued':'');
  $('statusbar').classList.remove('hidden');
  const c=$('queuecount');c.textContent=d.total||'';c.classList.toggle('hidden',!d.total);
}
async function pollStatus(){if(document.hidden||$('app').classList.contains('hidden')||activeTab==='queue'||activeTab==='home')return;try{updateStatus(await api('/api/queue'))}catch{const p=$('sabpill');p.className='spill bad';p.innerHTML='<i></i>SABnzbd offline'}}
async function itemAction(id,action){
  if(action==='more')return openQueueItem(id);
  if(action==='top'){try{await api('/api/queue/item',{id,action});toast('Moved to the top of the queue');await refreshQueue(true)}catch(e){toast(e.message,true)}return}
  if(action==='delete'&&!confirm('Remove this item from the queue and delete its partial files?'))return;
  try{await api('/api/queue/item',{id,action});await refreshQueue(true)}catch(e){toast(e.message,true)}
}
async function queueAll(action){try{await api('/api/queue/'+action,{});await refreshQueue(true)}catch(e){toast(e.message,true)}}
async function setSpeed(){
  const v=$('speedmb').value.trim();if(v==='')return;
  try{await api('/api/speedlimit',{mbps:Number(v)});toast(Number(v)>0?'Speed limit set to '+v+' MB/s':'Speed limit removed');await refreshQueue(true)}catch(e){toast(e.message,true)}
}

function historyRow(x){
  const ok=x.status==='Completed',bad=x.status==='Failed';
  const color=ok?'var(--green)':(bad?'var(--red)':'var(--amber)');
  const when=x.completed?relAge(x.completed):'';
  const note=bad&&x.fail_message?x.fail_message:[x.status,x.size,x.cat&&x.cat!=='*'?x.cat:''].filter(Boolean).join(' · ');
  return '<div class="hrow queueitem" data-id="'+esc(x.id)+'"><span class="dot" style="background:'+color+'"></span><div class="info"><div class="t">'+esc(x.name)+'</div><div class="m'+(bad?' bad':'')+'">'+esc(note)+(when?' · '+esc(when):'')+'</div></div>'+(bad?'<button class="icon-btn" data-act="retry" aria-label="Retry">'+ICON.retry+'</button>':'')+'<button class="icon-btn red" data-act="hdel" aria-label="Remove from history">'+ICON.trash+'</button></div>';
}
async function refreshHistory(){
  const st=$('histstatus');
  try{const d=await api('/api/history');st.className='lede';st.textContent=d.slots.length+' recent download(s)';$('historyitems').innerHTML=d.slots.map(historyRow).join('')||empty('No history yet','Finished and failed downloads show up here.')}
  catch(e){st.textContent=e.message;st.className='lede status error'}
}
// ---------- Home ----------
let homeBusy=false;
function fmtTime(s){s=Math.max(0,Math.floor(Number(s)||0));const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;return (h?h+':'+String(m).padStart(2,'0'):m)+':'+String(x).padStart(2,'0')}
function nowCard(p,compact){
  if(!p)return compact?'<button class="card kidle" data-tab="kodi">'+ICON.play+'<span>Nothing playing on Kodi · open your library</span></button>':'<div class="card kidle">'+ICON.play+'<span>Nothing playing right now.</span></div>';
  const pp=p.paused?ICON.play:ICON.pause;
  const det=p.movieid?' data-detail="'+esc(p.movieid)+'"':'';
  return '<div class="hero np">'+(p.fanart?'<div class="npbg">'+artTag(p.fanart)+'</div>':'')+'<div class="nowrow">'+(p.poster?'<button class="pthumb"'+det+' aria-label="Details for '+esc(p.title)+'">'+artTag(p.poster)+'</button>':'')+'<div class="info'+(det?' tap':'')+'"'+det+'><div class="kode">'+(compact?'NOW ON KODI':(p.paused?'PAUSED':'NOW PLAYING'))+'</div><div class="name">'+esc(p.title)+'</div>'+(p.sub?'<div class="sub">'+esc(p.sub)+'</div>':'')+'</div></div>'+
    '<div class="bar"><span style="width:'+clampPct(p.pct)+'%"></span></div><div class="ctrls"><span class="tm">'+fmtTime(p.time)+' / '+fmtTime(p.total)+'</span>'+
    (compact?'':'<button class="round" data-kact="stop" data-pid="'+esc(p.playerid)+'" aria-label="Stop"><svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/></svg></button>')+
    '<button class="round main" data-kact="playpause" data-pid="'+esc(p.playerid)+'" aria-label="'+(p.paused?'Play':'Pause')+'">'+pp+'</button></div></div>';
}
// "0:02:44" (SABnzbd's time left) -> "ready ~9:41 PM" (tomorrow/day added when it's not today)
function etaSecs(eta){const p=String(eta||'').trim().split(':').map(Number);if(!p.length||p.some(isNaN))return 0;return p.reduce((a,b)=>a*60+b,0)}
function readyAt(eta){
  const s=etaSecs(eta);if(!s)return '';
  const t=new Date(Date.now()+s*1000),now=new Date(),hm=t.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});
  const day=t.toDateString()===now.toDateString()?'':(new Date(now.getTime()+86400000).toDateString()===t.toDateString()?' tomorrow':' '+t.toLocaleDateString([],{weekday:'short'}));
  return 'ready ~'+hm+day;
}
function homeDownload(d){
  const c=d.current;
  const btn=d.paused?'<button class="btn sm" data-hact="resume">Resume all</button>':'<button class="btn ghost sm" data-hact="pause">Pause all</button>';
  const qbtn='<button class="btn ghost sm" data-tab="queue">Queue'+(d.total?' ('+esc(d.total)+')':'')+'</button>';
  if(!c)return '<div class="hero"><div class="emptydl"><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M12 3v12M7 10l5 5 5-5M5 20h14"/></svg><div><div class="kode">DOWNLOADS</div><div class="name" style="margin-top:4px">All caught up</div><div class="sub">Nothing downloading right now.</div></div></div><div class="row" style="margin-top:14px"><button class="btn sm" data-tab="search">Find something</button>'+(d.paused?btn:'')+qbtn+'</div></div>';
  const low=String(d.state||'').toLowerCase(),tag=low==='paused'?'warn':(low==='downloading'?'ok':'busy');
  return '<div class="hero"><div class="row" style="justify-content:space-between"><span class="tag '+tag+'" style="flex:none">'+esc(String(d.state||'').toUpperCase())+'</span><span style="flex:none;font:700 18px var(--mono);color:var(--teal)">'+esc(d.speed||'')+'</span></div>'+
    '<div class="name">'+esc(c.name)+'</div><div class="bar big"><span style="width:'+clampPct(c.pct)+'%"></span></div>'+
    '<div class="sub">'+esc(Math.round(c.pct||0))+'% · '+esc(c.sizeleft||'?')+' left of '+esc(c.size||'?')+(c.eta?' · '+esc(c.eta):'')+'</div>'+
    (low!=='paused'&&readyAt(c.eta)?'<div class="ready">'+esc(readyAt(c.eta))+'</div>':'')+'<div class="row" style="margin-top:12px">'+btn+qbtn+'</div></div>';
}
let arrivedKey='';
function renderArrived(items,tvReady){
  const box=$('homearrived'),key=JSON.stringify(items.map(x=>[x.id,x.watched]));
  if(key===arrivedKey)return;arrivedKey=key;   // Home refreshes every 5 s: don't redraw (and refetch posters) for nothing
  if(!items.length){box.innerHTML='';return}
  box.innerHTML='<div class="section">JUST ARRIVED</div><div class="cw">'+items.map(x=>'<div class="arr"><div class="ph">'+artTag(x.poster)+(x.new&&!x.watched?'<span class="newtag">NEW</span>':'')+'</div><div class="t">'+esc(x.title)+'</div><div class="m">'+esc([x.year,x.quality].filter(Boolean).join(' · ')||'\u00a0')+'</div><button class="btn sm" data-hplay="'+esc(x.id)+'">'+ICON.play+' Play on TV</button></div>').join('')+'</div>'+
    (tvReady?'':'<p class="hint" style="margin:2px 4px 0">Kodi has to be open on the TV. Set up “TV on” on the Kodi tab and Play on TV turns the TV on for you.</p>');
  hydrateArt(box);
}
async function homePlay(b){
  b.disabled=true;const old=b.innerHTML;b.textContent='Starting…';
  toast('Starting it on the TV… (turning the TV on first if needed, this can take a minute)');
  try{const r=await api('/api/home/play',{id:b.dataset.hplay});toast(r.started_tv?'The TV is on and the movie is starting.':'Playing on the TV.');setTimeout(refreshHome,2500)}
  catch(e){toast(e.message,true)}
  finally{b.disabled=false;b.innerHTML=old}
}
async function refreshHome(){
  if(homeBusy)return;homeBusy=true;
  const hr=new Date().getHours();$('greet').textContent=hr<12?'Good morning':(hr<18?'Good afternoon':'Good evening');
  try{
    const d=await api('/api/home');
    if(d.sab_ok){updateStatus(d);$('homestatus').className='lede';$('homestatus').textContent=d.total?d.total+' in queue':'Queue is empty';$('homedl').innerHTML=homeDownload(d)}
    else{const p=$('sabpill');p.className='spill bad';p.innerHTML='<i></i>SABnzbd offline';$('statusbar').classList.remove('hidden');$('homestatus').className='lede status error';$('homestatus').textContent=d.sab_error||'SABnzbd is offline';$('homedl').innerHTML=''}
    $('hdone').textContent=d.done_today!=null?d.done_today:'—';
    $('hfail').textContent=d.failed_today!=null?d.failed_today:'—';$('hfail').style.color=d.failed_today?'var(--red)':'';
    $('hfree').innerHTML=d.disk_free_gb!=null&&d.disk_free_gb>=0?esc(Number(d.disk_free_gb).toFixed(0))+' <small>GB</small>':'—';
    const k=d.kodi||{};
    $('homekodi').innerHTML=!k.configured?'':(k.online?nowCard(k.playing,true):'<button class="card kidle" data-tab="kodi">'+ICON.play+'<span>Kodi is offline</span></button>');hydrateArt($('homekodi'));
    const js=d.jellyfin||[];
    $('homejf').innerHTML=js.length?'<div class="section">JELLYFIN · '+js.length+' WATCHING</div>'+js.map(jfNowCard).join(''):'';hydrateArt($('homejf'));
    renderArrived(d.arrived||[],d.tv_ready);renderHomeUp(d.upcoming||[]);
    if(d.version)$('homever').textContent='NZB Command Center phone app · v'+d.version;
    loadHomeUsage();loadHomeDrives();
  }catch(e){$('homestatus').className='lede status error';$('homestatus').textContent=e.message}
  finally{homeBusy=false}
}

// ---------- Kodi artwork (fetched with the access code, shown as blob: URLs) ----------
const artCache=new Map(),artQueue=[];let artActive=0;
function artURL(ref){
  if(!artCache.has(ref))artCache.set(ref,new Promise((res,rej)=>{artQueue.push({ref,res,rej});pumpArt()}));
  return artCache.get(ref);
}
function pumpArt(){
  while(artActive<4&&artQueue.length){
    const j=artQueue.shift();artActive++;
    // "r:<id>" = a search result's poster (the server picks the source); anything else = Kodi artwork.
    const src=j.ref.startsWith('t:')?'/api/tv/poster?id='+encodeURIComponent(j.ref.slice(2)):j.ref.startsWith('r:')?'/api/search/art?id='+encodeURIComponent(j.ref.slice(2)):(j.ref.startsWith('j:')?'/api/jellyfin/art?id='+encodeURIComponent(j.ref.slice(2)):j.ref.startsWith('a:')?'/api/arr/art?ref='+encodeURIComponent(j.ref.slice(2)):'/api/kodi/art?u='+encodeURIComponent(j.ref));
    fetch(src,{headers:{'X-Mobile-Token':token}})
      .then(r=>{if(r.status===404)return null;if(!r.ok)throw new Error('HTTP '+r.status);return r.blob()})
      .then(b=>{if(!b){j.res(null);return}const u=URL.createObjectURL(b);artReady.set(j.ref,u);trimArt();j.res(u)})  // null = no poster: keep the placeholder, don't ask again
      .catch(e=>{artCache.delete(j.ref);j.rej(e)}).finally(()=>{artActive--;pumpArt()});
  }
}
const artReady=new Map(),ART_KEEP=150;
// Every poster is kept in memory as a blob: URL. Scrolling a big library would otherwise keep
// adding hundreds of MB until iOS closes the home-screen app. Keep the newest ART_KEEP and let go
// of older ones that aren't on screen (they're fetched again if they come back into view).
function trimArt(){
  if(artReady.size<=ART_KEEP)return;
  const shown=new Set([...document.querySelectorAll('img[data-art]')].map(i=>i.dataset.art));
  for(const [ref,u] of artReady){
    if(artReady.size<=ART_KEEP)break;
    if(shown.has(ref))continue;
    artReady.delete(ref);artCache.delete(ref);
    try{URL.revokeObjectURL(u)}catch{}
  }
}
function artTag(ref){if(!ref)return '';const u=artReady.get(ref);return u?'<img class="art loaded" data-art="'+esc(ref)+'" src="'+u+'" alt="">':'<img class="art" data-art="'+esc(ref)+'" alt="">'}
const artObserver='IntersectionObserver' in window?new IntersectionObserver(es=>{for(const e of es)if(e.isIntersecting){artObserver.unobserve(e.target);showArt(e.target)}},{rootMargin:'300px'}):null;
function showArt(img){artURL(img.dataset.art).then(u=>{if(!u)return;img.onload=()=>img.classList.add('loaded');img.src=u}).catch(()=>{})}
function hydrateArt(root){root.querySelectorAll('img[data-art]:not([src])').forEach(img=>artObserver?artObserver.observe(img):showArt(img))}
function openViewer(ref){if(!ref)return;artURL(ref).then(u=>{if(!u)throw new Error('none');$('viewerimg').src=u;$('viewer').classList.remove('hidden')}).catch(()=>toast('Couldn\'t load the picture',true))}

// ---------- Movie details sheet ----------
let sheetOpen=false,detailMovie=null;
function closeSheet(fromPop){if(!sheetOpen)return;sheetOpen=false;$('sheet').classList.add('hidden');document.body.style.overflow='';if(!fromPop&&history.state&&history.state.sheet)history.back()}
async function openDetail(id){
  const panel=$('sheetpanel');
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button><div class="empty"><b>Loading…</b></div>';
  $('sheet').classList.remove('hidden');document.body.style.overflow='hidden';
  if(!sheetOpen){sheetOpen=true;try{history.pushState({sheet:1},'')}catch{}}
  let d;try{d=await api('/api/kodi/movie?id='+encodeURIComponent(id))}catch(e){panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button>'+empty('Couldn\'t load this movie',e.message);return}
  const meta=[d.year,runtimeText(d.runtime),d.watched?'watched':''].filter(Boolean).join(' · ');
  const chips=[];
  if(d.quality)chips.push('<span class="chip">Your copy: '+esc(d.quality)+(d.hdr?' '+esc(d.hdr.toUpperCase()):'')+'</span>');
  if(d.imdb_rating)chips.push(d.imdb_url?'<a class="chip imdb" href="'+esc(d.imdb_url)+'" target="_blank" rel="noopener">IMDb '+esc(Number(d.imdb_rating).toFixed(1))+'</a>':'<span class="chip imdb">IMDb '+esc(Number(d.imdb_rating).toFixed(1))+'</span>');
  if(d.tmdb)chips.push('<span class="chip" style="background:#0B2A3A;color:#7DD3FC">TMDb '+esc(Number(d.tmdb).toFixed(1))+'</span>');
  else if(!d.imdb_rating&&d.rating)chips.push('<span class="chip">★ '+esc(d.rating)+(d.rating_source?' '+esc(d.rating_source.replace('themoviedb','TMDb')):'')+'</span>');
  if(!d.imdb_rating&&d.imdb_url)chips.push('<a class="chip imdb" href="'+esc(d.imdb_url)+'" target="_blank" rel="noopener">IMDb</a>');
  if(d.mpaa)chips.push('<span class="chip">'+esc(d.mpaa)+'</span>');
  d.genres.forEach(g=>chips.push('<span class="chip">'+esc(g)+'</span>'));
  const facts=[];
  if(d.directors.length)facts.push('<b>Director</b> '+esc(d.directors.join(', ')));
  if(d.cast.length)facts.push('<b>Cast</b> '+esc(d.cast.map(c=>c.name).join(', ')));
  if(d.studio.length)facts.push('<b>Studio</b> '+esc(d.studio.join(', ')));
  if(d.imdb_votes)facts.push('<b>IMDb votes</b> '+esc(Number(d.imdb_votes).toLocaleString()));
  const play=(d.resume?'<button class="btn" data-dplay="'+esc(d.id)+'" data-resume="1">Resume at '+fmtTime(d.resume)+'</button><button class="btn ghost" data-dplay="'+esc(d.id)+'">Start over</button>':'<button class="btn" data-dplay="'+esc(d.id)+'">Play on Kodi</button>');
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button>'+
    (d.fanart?'<button class="fan" data-view="'+esc(d.fanart)+'" aria-label="View backdrop full size">'+artTag(d.fanart)+'</button>':'<div class="fan" style="aspect-ratio:3/1"></div>')+
    '<div class="dhead"><button class="dposter" data-view="'+esc(d.poster)+'" aria-label="View poster full size">'+artTag(d.poster)+'</button><div class="dtitle"><h2>'+esc(d.title)+'</h2><div class="m">'+esc(meta)+'</div></div></div>'+
    (chips.length?'<div class="chips">'+chips.join('')+'</div>':'')+
    '<div class="dbody">'+(d.tagline?'<p class="tagline">'+esc(d.tagline)+'</p>':'')+'<p class="plot">'+(esc(d.plot)||'<span style="color:var(--muted)">No synopsis in Kodi for this one.</span>')+'</p>'+
    (facts.length?'<div class="facts">'+facts.join('<br>')+'</div>':'')+'<div class="row">'+play+'</div>'+
    '<button class="btn ghost full" style="margin-top:10px" data-better="1">Find a better copy</button></div>';
  panel.scrollTop=0;hydrateArt(panel);
  detailMovie=d;
}

// ---------- Kodi ----------
let kodiRecent=[],kodiMovies=null,kodiLoading=false;
function runtimeText(sec){if(!sec)return '';const m=Math.round(sec/60);return m>=60?Math.floor(m/60)+'h '+(m%60)+'m':m+'m'}
function movieRow(mv){
  const meta=[mv.year,runtimeText(mv.runtime),mv.mpaa,mv.resume?'resume at '+fmtTime(mv.resume):(mv.watched?'watched':'')].filter(Boolean).join(' · ');
  const rl=(mv.imdb?'<span class="rc imdb">IMDb '+Number(mv.imdb).toFixed(1)+'</span>':'')+(mv.tmdb?'<span class="rc tmdb">TMDb '+Number(mv.tmdb).toFixed(1)+'</span>':'')+
    ((mv.genres||[]).length?'<span>'+esc(mv.genres.slice(0,2).join(', '))+'</span>':'');
  return '<div class="qrow"><button class="pthumb" data-detail="'+esc(mv.id)+'" aria-label="Details for '+esc(mv.title)+'">'+artTag(mv.poster)+'</button><div class="info tap" data-detail="'+esc(mv.id)+'"><div class="t">'+esc(mv.title)+'</div><div class="m">'+esc(meta)+'</div>'+(rl?'<div class="rl">'+rl+'</div>':'')+'</div>'+
    (mv.resume?'<button class="btn ghost sm" style="flex:none" data-play="'+esc(mv.id)+'" data-resume="1">Resume</button>':'')+
    '<button class="icon-btn play" data-play="'+esc(mv.id)+'" aria-label="Play '+esc(mv.title)+(mv.resume?' from the start':'')+'">'+ICON.play+'</button></div>';
}
const KODI_PAGE=100;let kodiShown=KODI_PAGE;
const KODI_VIEWS={recent:'RECENTLY ADDED',all:'ALL MOVIES',newest:'NEWEST ADDED',year:'BY RELEASE YEAR',rated:'HIGHEST RATED',unwatched:'UNWATCHED',progress:'IN PROGRESS'};
async function renderKodiList(resetPage){
  if(resetPage)kodiShown=KODI_PAGE;
  const q=$('kodifilter').value.trim().toLowerCase(),view=$('kodiview').value,title=$('kodilisttitle'),list=$('kodilist'),more=$('kodimore');
  more.classList.add('hidden');
  if(!q&&view==='recent'){title.textContent='RECENTLY ADDED';list.innerHTML=kodiRecent.map(movieRow).join('')||empty('No movies yet','Run a library scan once something finishes downloading.');hydrateArt(list);return}
  if(kodiMovies===null){
    if(kodiLoading)return;kodiLoading=true;title.textContent='LOADING LIBRARY…';list.innerHTML=skeleton(5,2);
    try{kodiMovies=(await api('/api/kodi/movies')).movies||[]}catch(e){title.textContent='';list.innerHTML=empty('Couldn\'t load the library',e.message);return}finally{kodiLoading=false}
    return renderKodiList();
  }
  let a=kodiMovies.slice();
  if(view==='unwatched')a=a.filter(m=>!m.watched);
  if(view==='progress')a=a.filter(m=>m.resume>0);
  if(q)a=a.filter(m=>m.title.toLowerCase().includes(q)||(m.genres||[]).some(g=>g.toLowerCase().includes(q)));
  if(view==='newest'||view==='recent')a.sort((x,y)=>(y.added||'').localeCompare(x.added||''));
  if(view==='rated')a.sort((x,y)=>((y.imdb||y.tmdb||0)-(x.imdb||x.tmdb||0))||x.title.localeCompare(y.title));
  if(view==='year')a.sort((x,y)=>(y.year||0)-(x.year||0)||x.title.localeCompare(y.title));
  const label=q?'MATCH'+(a.length===1?'':'ES'):(view==='all'?'MOVIES':KODI_VIEWS[view]);
  title.textContent=a.length+' '+label+(q&&view!=='all'&&view!=='recent'?' · '+KODI_VIEWS[view]:'');
  list.innerHTML=a.slice(0,kodiShown).map(movieRow).join('')||empty(q?'No matches':'Nothing here',q?'Try part of the title.':'No movies fit this view.');hydrateArt(list);
  if(a.length>kodiShown){more.textContent='Show more ('+(a.length-kodiShown)+' left)';more.classList.remove('hidden')}
}

// ---------- Sony TV: power on + open Kodi ----------
let tvInfo=null,tvPolling=false;
async function loadTv(){try{tvInfo=await api('/api/tv')}catch{tvInfo=null}renderTv()}
function tvSay(t,bad){const m=$('tvmsg');m.textContent=t||'';m.style.color=bad?'var(--red, #ff6b6b)':''}
function renderTv(){
  const box=$('tvbox');if(!tvInfo){box.classList.add('hidden');return}
  box.classList.remove('hidden');
  $('tvbtns').classList.toggle('hidden',!tvInfo.configured);
  const ts=$('tvsetup');if(!tvInfo.configured&&!ts.dataset.seen){ts.open=true;ts.dataset.seen='1';ts.dataset.auto='1'}else if(tvInfo.configured&&ts.dataset.auto){ts.open=false;delete ts.dataset.auto}
  const h=$('tvhost');if(document.activeElement!==h)h.value=tvInfo.host||'';
  const mc=$('tvmac');if(document.activeElement!==mc)mc.value=tvInfo.mac||'';
  h.placeholder=tvInfo.auto_host?'Same as Kodi ('+tvInfo.auto_host+')':'192.168.1.40';
  $('tvpsk').placeholder=tvInfo.psk_set?'•••••• saved (leave blank to keep)':'The key you set on the TV';
  if(tvPolling)return;
  if(!tvInfo.configured){tvSay('Set up TV control below to turn the TV on from here.');return}
  if(tvInfo.problem){tvSay(tvInfo.problem,true);return}
  tvSay({on:'TV is on.',standby:'TV is in standby.',off:'TV is off or unreachable.'}[tvInfo.power]||'')
}
function tvBusy(b){$('tvon').disabled=b;$('tvkodi').disabled=b}
async function tvOn(){
  tvBusy(true);tvPolling=true;tvSay('Turning the TV on…');
  try{await api('/api/tv/on',{kodi:true})}catch(e){tvPolling=false;tvBusy(false);tvSay(e.message,true);return}
  const t0=Date.now();
  while(Date.now()-t0<150000){
    await new Promise(r=>setTimeout(r,2000));
    let j;try{j=(await api('/api/tv/job')).job}catch{continue}
    tvSay(j.message,j.state==='error');
    if(j.state==='done'){toast(j.message);break}
    if(j.state==='error'){toast(j.message,true);break}
  }
  tvPolling=false;tvBusy(false);
  if(activeTab==='kodi')refreshKodi()
}
async function tvKodi(){
  tvBusy(true);tvSay('Opening Kodi…');
  try{await api('/api/tv/kodi',{});toast('Opening Kodi on the TV…');setTimeout(()=>{if(activeTab==='kodi')refreshKodi()},4000)}
  catch(e){tvSay(e.message,true);toast(e.message,true)}
  finally{tvBusy(false)}
}
async function tvSave(){
  const b=$('tvsave');b.disabled=true;
  try{
    tvInfo=await api('/api/tv/settings',{host:$('tvhost').value.trim(),psk:$('tvpsk').value.trim(),mac:$('tvmac').value.trim()});
    $('tvpsk').value='';renderTv();
    if(tvInfo.configured&&!tvInfo.problem){toast('TV connected');$('tvsetup').open=false}
    else if(tvInfo.problem)toast(tvInfo.problem,true);
  }catch(e){toast(e.message,true)}finally{b.disabled=false}
}
async function refreshKodi(){
  const st=$('kodistatus');loadTv();
  try{
    const d=await api('/api/kodi');
    const off=()=>{$('kodinow').innerHTML='';['kodilib','kodiTV','kodiseg','kodiremote','kodytype'].forEach(id=>$(id).classList.add('hidden'));kodiOnline=false};
    if(!d.configured){st.className='lede';st.textContent='Kodi isn\'t set up yet — add it in the desktop app\'s Kodi tab.';off();$('kodiscan').classList.add('hidden');return}
    $('kodiscan').classList.remove('hidden');
    if(!d.online){st.className='lede status error';st.textContent=d.problem||'Kodi is offline.';off();return}
    const c=d.counts||{};st.className='lede';st.textContent=(c.movies||0)+' movies · '+(c.shows||0)+' shows · '+(c.episodes||0)+' episodes';
    kodiPid=d.playing?d.playing.playerid:-1;
    $('kodinow').innerHTML=nowCard(d.playing,false);hydrateArt($('kodinow'));
    kodiOnline=true;$('kodiremote').classList.remove('hidden');$('kodytype').classList.remove('hidden');$('kodiseg').classList.remove('hidden');
    kodiRecent=d.recent||[];kodiMovies=null;kodiShows=null;kodiMissing=null;kodiContinue=d.continue||[];
    setKodiMode(kodiMode);
  }catch(e){st.className='lede status error';st.textContent=e.message}
}
function remoteInfo(d){
  const p=d.playing,v=d.volume,bits=[];
  if(p&&p.subtitle)bits.push('Subtitles: '+p.subtitle);else if(p)bits.push('Subtitles off');
  if(p&&p.audio)bits.push('Audio: '+p.audio);
  if(v)bits.push(v.muted?'Muted':'Volume '+v.level+'%');
  $('kodirinfo').textContent=bits.join(' · ');
}
async function refreshKodiNow(){try{const d=await api('/api/kodi/now');kodiPid=d.playing?d.playing.playerid:-1;$('kodinow').innerHTML=nowCard(d.playing,false);hydrateArt($('kodinow'));remoteInfo(d)}catch{}}
async function kodiControl(action,pid,value){
  if(pid===undefined||pid==='')pid=kodiPid;
  try{await api('/api/kodi/control',{action,playerid:Number(pid),value});setTimeout(()=>{if(activeTab==='kodi')refreshKodiNow();else refreshHome()},400)}catch(e){toast(e.message,true)}
}
async function playMovie(id,resume){
  try{await api('/api/kodi/play',{movieid:Number(id),resume:!!resume});toast(resume?'Resuming on Kodi…':'Starting on Kodi…');setTimeout(refreshKodiNow,2000)}catch(e){toast(e.message,true)}
}
async function playEpisode(id,resume){
  try{await api('/api/kodi/play',{episodeid:Number(id),resume:!!resume});toast(resume?'Resuming on Kodi…':'Starting on Kodi…');setTimeout(()=>{if(activeTab==='kodi')refreshKodi()},2500)}catch(e){toast(e.message,true)}
}

// ---------- Kodi TV shows ----------
const TV_PAGE=100;
let kodiMode=LS.get('nzb_mobile_kodimode','movies')==='tv'?'tv':'movies',kodiOnline=false,kodiShows=null,kodiContinue=[],showsLoading=false,tvShown=TV_PAGE,kodiMissing=null;
function setKodiMode(m){
  kodiMode=m;LS.set('nzb_mobile_kodimode',m);
  document.querySelectorAll('#kodiseg button').forEach(b=>b.classList.toggle('on',b.dataset.kmode===m));
  $('kodilib').classList.toggle('hidden',!kodiOnline||m!=='movies');$('kodiTV').classList.toggle('hidden',!kodiOnline||m!=='tv');
  if(!kodiOnline)return;
  if(m==='movies')renderKodiList(true);else{renderContinue();renderShows(true)}
}
function epCode(e){return 'S'+String(e.season).padStart(2,'0')+'E'+String(e.episode).padStart(2,'0')}
function renderContinue(){
  const box=$('kodicw');$('cwtitle').classList.toggle('hidden',!kodiContinue.length);box.classList.toggle('hidden',!kodiContinue.length);
  box.innerHTML=kodiContinue.map(c=>'<button class="citem" data-ep="'+esc(c.episode.id)+'"'+(c.episode.resume?' data-resume="1"':'')+' aria-label="Play '+esc(c.show.title+' '+epCode(c.episode))+'">'+(c.show.poster?'<div class="ph">'+artTag(c.show.poster)+'</div>':'')+'<div class="t">'+esc(c.show.title)+'</div><div class="m">'+esc(epCode(c.episode)+' · '+c.episode.title)+'</div><div class="go">'+(c.episode.resume?'▶ RESUME '+fmtTime(c.episode.resume):'▶ PLAY NEXT')+'</div></button>').join('');
  hydrateArt(box);
}
function showRow(s){
  const pct=s.episodes?Math.round(s.watched/s.episodes*100):0,left=s.episodes-s.watched;
  const meta=[s.year,s.episodes+' episodes',left>0&&s.watched?left+' unwatched':(left<=0&&s.episodes?'all watched':'')].filter(Boolean).join(' · ');
  return '<div class="qrow"><button class="pthumb" data-show="'+esc(s.id)+'" aria-label="Details for '+esc(s.title)+'">'+artTag(s.poster)+'</button><div class="info tap" data-show="'+esc(s.id)+'"><div class="t">'+esc(s.title)+'</div><div class="m">'+esc(meta)+'</div>'+((s.genres||[]).length?'<div class="rl"><span>'+esc(s.genres.slice(0,2).join(', '))+'</span></div>':'')+'<div class="bar thin"><span style="width:'+pct+'%"></span></div></div></div>';
}
function missingRow(x){
  const eps=x.missing.map(n=>'E'+String(n).padStart(2,'0')),q=x.show+' S'+String(x.season).padStart(2,'0')+(x.missing.length===1?'E'+String(x.missing[0]).padStart(2,'0'):'');
  return '<div class="miss"><span><b>'+esc(x.show)+'</b> · Season '+esc(x.season)+'<br>Missing '+esc(eps.slice(0,12).join(', ')+(eps.length>12?' …':''))+'</span><button class="btn sm" data-tvsearch="'+esc(q)+'">Search</button></div>';
}
async function renderShows(reset){
  if(reset)tvShown=TV_PAGE;
  const view=$('tvview').value,q=$('tvfilter').value.trim().toLowerCase(),list=$('tvlist'),title=$('tvlisttitle'),more=$('tvmore');more.classList.add('hidden');
  if(view==='missing'){
    if(kodiMissing===null){title.textContent='CHECKING FOR GAPS…';list.innerHTML=skeleton(4,2);try{kodiMissing=(await api('/api/kodi/missing')).missing||[]}catch(e){title.textContent='';list.innerHTML=empty('Couldn\'t check',e.message);return}}
    const a=kodiMissing.filter(x=>!q||x.show.toLowerCase().includes(q));
    title.textContent=a.length+' SEASON'+(a.length===1?'':'S')+' WITH GAPS';
    list.innerHTML=a.slice(0,tvShown).map(missingRow).join('')||empty('No gaps found','Every season has all episodes up to its latest one. (Kodi can\'t know about episodes after the newest one you have.)');
    if(a.length>tvShown){more.textContent='Show more ('+(a.length-tvShown)+' left)';more.classList.remove('hidden')}
    return;
  }
  if(kodiShows===null){
    if(showsLoading)return;showsLoading=true;title.textContent='LOADING SHOWS…';list.innerHTML=skeleton(5,2);
    try{kodiShows=(await api('/api/kodi/shows')).shows||[]}catch(e){title.textContent='';list.innerHTML=empty('Couldn\'t load your shows',e.message);return}finally{showsLoading=false}
  }
  let a=kodiShows.filter(s=>!q||s.title.toLowerCase().includes(q));
  if(view==='unwatched')a=a.filter(s=>s.episodes>s.watched);
  if(view==='recent')a=a.filter(s=>s.lastplayed).sort((x,y)=>y.lastplayed.localeCompare(x.lastplayed));
  if(view==='added')a.sort((x,y)=>(y.added||'').localeCompare(x.added||''));
  title.textContent=a.length+' SHOW'+(a.length===1?'':'S');
  list.innerHTML=a.slice(0,tvShown).map(showRow).join('')||empty(q?'No matches':'No shows here',q?'Try part of the title.':'Nothing fits this view.');hydrateArt(list);
  if(a.length>tvShown){more.textContent='Show more ('+(a.length-tvShown)+' left)';more.classList.remove('hidden')}
}
function episodeRow(e){
  const meta=[e.aired,runtimeText(e.runtime),e.resume?'resume at '+fmtTime(e.resume):(e.watched?'watched':'')].filter(Boolean).join(' · ');
  return '<div class="eprow'+(e.watched?' w':'')+'"><span class="n">E'+String(e.episode).padStart(2,'0')+'</span><div class="info"><div class="t">'+esc(e.title)+'</div><div class="m">'+esc(meta)+'</div></div><button class="icon-btn play" data-ep="'+esc(e.id)+'"'+(e.resume?' data-resume="1"':'')+' aria-label="Play '+esc(epCode(e)+' '+e.title)+'">'+ICON.play+'</button></div>';
}
async function openShow(id){
  const panel=$('sheetpanel');
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button><div class="empty"><b>Loading…</b></div>';
  $('sheet').classList.remove('hidden');document.body.style.overflow='hidden';
  if(!sheetOpen){sheetOpen=true;try{history.pushState({sheet:1},'')}catch{}}
  let d;try{d=await api('/api/kodi/show?id='+encodeURIComponent(id))}catch(e){panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button>'+empty('Couldn\'t load this show',e.message);return}
  const n=d.next,openSeason=n?n.season:(d.seasons[0]||{}).season;
  const nextBtn=n?'<button class="btn full" data-ep="'+esc(n.id)+'"'+(n.resume?' data-resume="1"':'')+'>'+(n.resume?'Resume ':'Play next: ')+esc(epCode(n))+' · '+esc(n.title)+'</button>':'<p class="hint">You\'ve watched everything Kodi has for this show.</p>';
  const seasons=d.seasons.map(s=>{
    const w=s.episodes.filter(e=>e.watched).length,name=s.season===0?'Specials':'Season '+s.season;
    const miss=s.missing.length?'<div class="miss"><span>Missing '+esc(s.missing.map(x=>'E'+String(x).padStart(2,'0')).join(', '))+'</span><button class="btn sm" data-tvsearch="'+esc(d.title+' S'+String(s.season).padStart(2,'0')+(s.missing.length===1?'E'+String(s.missing[0]).padStart(2,'0'):''))+'">Search</button></div>':'';
    return '<details class="season"'+(s.season===openSeason?' open':'')+'><summary>'+esc(name)+'<span class="m">'+w+'/'+s.episodes.length+' watched'+(s.missing.length?' · '+s.missing.length+' missing':'')+'</span></summary>'+miss+s.episodes.map(episodeRow).join('')+'</details>';
  }).join('');
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button>'+
    (d.fanart?'<button class="fan" data-view="'+esc(d.fanart)+'" aria-label="View backdrop full size">'+artTag(d.fanart)+'</button>':'<div class="fan" style="aspect-ratio:3/1"></div>')+
    '<div class="dhead"><button class="dposter" data-view="'+esc(d.poster)+'" aria-label="View poster full size">'+artTag(d.poster)+'</button><div class="dtitle"><h2>'+esc(d.title)+'</h2><div class="m">'+esc([d.year,d.episodes+' episodes',d.watched+' watched'].filter(Boolean).join(' · '))+'</div></div></div>'+
    ((d.genres||[]).length?'<div class="chips">'+d.genres.map(g=>'<span class="chip">'+esc(g)+'</span>').join('')+'</div>':'')+
    '<div class="dbody">'+(d.plot?'<p class="plot">'+esc(d.plot)+'</p>':'')+nextBtn+'<div style="margin-top:12px">'+seasons+'</div></div>';
  panel.scrollTop=0;hydrateArt(panel);
}

// ---------- Queue item options ----------
const PRIOS=[['-1','Low'],['0','Normal'],['1','High'],['2','Force']];
function openQueueItem(id){
  const x=lastQueue.find(s=>s.id===id);if(!x)return;
  const panel=$('sheetpanel');panel.dataset.nzo=id;
  const cats=['*'].concat(sabCats.filter(c=>c!=='*'));
  const cur=(x.cat&&x.cat!=='*')?x.cat:'*';
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button><div class="dbody" style="padding-top:20px"><h2 style="margin:0 44px 6px 0;font-size:18px;overflow-wrap:anywhere">'+esc(x.name)+'</h2><div class="hint">'+esc([x.status,x.size,x.priority?'priority '+x.priority:''].filter(Boolean).join(' · '))+'</div>'+
    '<div class="section">PRIORITY</div><div class="pills">'+PRIOS.map(([v,n])=>'<button data-qprio="'+v+'"'+(String(x.priority).toLowerCase()===n.toLowerCase()?' class="on"':'')+'>'+n+'</button>').join('')+'</div>'+
    '<div class="section">CATEGORY</div><div class="row"><select id="qcat" class="field" style="margin:0">'+cats.map(c=>'<option value="'+esc(c)+'"'+(c===cur?' selected':'')+'>'+esc(c==='*'?'Default':c)+'</option>').join('')+'</select><button class="btn ghost sm" style="flex:none" data-qcat="1">Change</button></div>'+
    '<div class="section">ORDER</div><button class="btn ghost full" data-qtop="1">Download this next</button></div>';
  $('sheet').classList.remove('hidden');document.body.style.overflow='hidden';
  if(!sheetOpen){sheetOpen=true;try{history.pushState({sheet:1},'')}catch{}}
}
async function queueItemChange(action,value){
  const id=$('sheetpanel').dataset.nzo;
  try{await api('/api/queue/item',{id,action,value});toast(action==='top'?'Moved to the top of the queue':(action==='priority'?'Priority changed':'Category changed'));closeSheet();await refreshQueue(true)}catch(e){toast(e.message,true)}
}

// ---------- Home: data downloaded ----------
function fmtBytes(n){n=Number(n)||0;const u=['B','KB','MB','GB','TB'];let i=0;while(n>=1024&&i<u.length-1){n/=1024;i++}return (i?n.toFixed(n<10?1:0):n)+' '+u[i]}
let usageAt=0;
async function loadHomeUsage(){
  if(Date.now()-usageAt<60000)return;usageAt=Date.now();
  try{const t=(await api('/api/stats')).totals||{};$('husday').textContent=fmtBytes(t.day);$('husweek').textContent=fmtBytes(t.week);$('husmonth').textContent=fmtBytes(t.month)}catch{}
}
// ---------- Drive Health ----------
let drivesAt=0,drivesData=null,drivesBusy=false;
const DCOL=['#A78BFA','#38BDF8','#F59E0B','#F472B6','#34D399','#FB7185'];
function dLetters(d){return d.letters.filter(x=>x!=='/').map(x=>x+':').join(' ')}
function dTag(d){if(d.key&&d.key.startsWith('vol:')&&d.status==='ok')return '<span class="tag ok" style="flex:none">'+(d.media==='Network'?'CONNECTED':'OK')+'</span>';const t={ok:['ok','HEALTHY'],warn:['warn',d.temp&&d.temp>=d.temp_limit-4?'RUNNING WARM':'NEEDS A LOOK'],bad:['bad',d.health==='Unhealthy'||d.health==='Warning'?'PROBLEM':'TOO HOT'],unknown:['unk','UNKNOWN']}[d.status]||['unk','UNKNOWN'];return '<span class="tag '+t[0]+'" style="flex:none">'+t[1]+'</span>'}
function dKind(d){return [d.media,d.bus].filter(Boolean).join(' · ')+(d.size?(d.media||d.bus?' · ':'')+fmtBytes(d.size):'')}
function dBarColor(d){return d.used_pct>=90?'var(--red)':(d.used_pct>=75?'#F59E0B':'var(--teal)')}
function dYears(h){if(!h)return '—';const y=h/8766;return y>=1?y.toFixed(1)+' yr':(h>=48?Math.round(h/24)+' d':h+' h')}
function dTitle(d){return esc(d.label||d.name)}
async function loadHomeDrives(force){
  if(!force&&Date.now()-drivesAt<120000){if(drivesData)renderHomeDrives(drivesData);return}
  drivesAt=Date.now();
  try{drivesData=await api('/api/drives');renderHomeDrives(drivesData)}
  catch(e){$('homedrives').innerHTML='<div class="section" style="margin:18px 4px 8px">DRIVES</div><button class="card kidle" data-tab="drives"><span>Couldn\'t check your drives: '+esc(e.message)+' · tap to try again</span></button>'}
}
function renderHomeDrives(r){
  const box=$('homedrives');
  if(!r||!r.drives||!r.drives.length){box.innerHTML='<div class="section" style="margin:18px 4px 8px">DRIVES</div><button class="card kidle" data-tab="drives"><span>'+esc((r&&r.problem)||'No drives reported yet')+' · tap for Drive Health</span></button>';return}
  const n=r.totals.warn;
  box.innerHTML='<div class="row" style="justify-content:space-between;margin:18px 4px 8px"><div class="section" style="margin:0;flex:none">DRIVES</div>'+(n?'<span class="tag warn" style="flex:none">'+n+' NEED'+(n===1?'S':'')+' A LOOK</span>':'<span class="tag ok" style="flex:none">ALL HEALTHY</span>')+'</div><div class="card">'+
    r.drives.map(d=>'<button class="drow" data-tab="drives"><div class="dl"><span class="lt">'+esc(dLetters(d)||'—')+'</span><span class="nm">'+dTitle(d)+' <small>· '+esc(dKind(d))+'</small></span>'+dTag(d)+'</div>'+
      (d.total?'<div class="dbar"><span style="width:'+clampPct(d.used_pct)+'%;background:'+dBarColor(d)+'"></span></div>':'')+
      '<div class="dsub"><span><b>'+esc(fmtBytes(d.free))+' free</b>'+(d.days_left!=null?' · ~'+esc(d.days_left)+' days left':(d.wear!=null?' · SSD wear '+esc(d.wear)+'%':''))+'</span><span style="font-family:var(--mono)'+(d.temp&&d.temp>=d.temp_limit-4?';color:var(--amber)':'')+'">'+(d.temp?esc(d.temp)+'°C':'')+'</span></div></button>').join('')+'</div>';
}
function tempChart(drives){
  const series=drives.map((d,i)=>({d,c:DCOL[i%DCOL.length],pts:(d.history||[])})).filter(s=>s.pts.length>1);
  if(!series.length)return '';
  const now=Date.now()/1000,start=now-86400;let lo=Infinity,hi=-Infinity;
  series.forEach(s=>s.pts.forEach(p=>{lo=Math.min(lo,p[1]);hi=Math.max(hi,p[1])}));
  const limit=Math.min(...series.map(s=>s.d.temp_limit));hi=Math.max(hi,limit)+2;lo=Math.max(0,lo-5);
  const W=330,H=110,x=t=>((t-start)/86400*W).toFixed(1),y=v=>(H-(v-lo)/(hi-lo)*H).toFixed(1);
  const lines=series.map(s=>'<polyline fill="none" stroke="'+s.c+'" stroke-width="2" stroke-linejoin="round" points="'+s.pts.map(p=>x(p[0])+','+y(p[1])).join(' ')+'"/>').join('');
  return '<div class="section">TEMPERATURES · 24 H</div><div class="card"><svg width="100%" height="110" viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none" role="img" aria-label="Drive temperatures over the last 24 hours"><line x1="0" x2="'+W+'" y1="'+y(limit)+'" y2="'+y(limit)+'" stroke="#6B5414" stroke-dasharray="4 4"/>'+lines+'</svg>'+
    '<div class="legend">'+series.map(s=>'<span><i style="background:'+s.c+'"></i>'+esc((dLetters(s.d)?dLetters(s.d)+' ':'')+(s.d.label||s.d.name))+'</span>').join('')+'<span style="margin-left:auto;color:#D6C7A0">- - '+esc(Math.round(limit))+'°C alert</span></div></div>';
}
function driveCard(d){
  const other=d.key&&d.key.startsWith('vol:');  // not a physical disk Windows lists: no temperature/health
  const stats=other?[[fmtBytes(d.free),'Free'],[fmtBytes(d.total),'Capacity'],[d.used_pct!=null?d.used_pct+'%':'—','Used'],[d.media||'—','Type']]:[[d.temp?d.temp+'°C':'—','Temp'],[dYears(d.hours),'Powered on']];
  if(!other){
    if(d.wear!=null)stats.push([d.wear+'%','SSD wear']);else if(d.rpm)stats.push([d.rpm,'RPM']);else stats.push([d.used_pct!=null?d.used_pct+'%':'—','Used']);
    stats.push(d.days_left!=null?['~'+d.days_left,'Days left']:[fmtBytes(d.total),'Capacity']);
  }
  const notes=d.problems.map(p=>'<div class="dnote '+(d.status==='bad'?'bad':'warn')+'">'+esc(p)+'</div>').join('');
  const info=d.days_left!=null?'<div class="dnote warn">About <b>'+esc(d.days_left)+' days</b> of space left at your pace ('+esc(fmtBytes(d.daily_bytes))+'/day).</div>':'';
  return '<button class="dcard tap" data-ddrive="'+esc(d.key)+'"><div class="top"><span class="lt">'+esc(dLetters(d)||'—')+'</span><div class="nm"><b>'+dTitle(d)+'</b><span>'+esc(d.name)+(dKind(d)?' · '+esc(dKind(d)):'')+'</span></div>'+dTag(d)+'</div>'+
    (d.total?'<div class="row" style="justify-content:space-between;margin-top:12px;font-size:13px"><b style="flex:none">'+esc(fmtBytes(d.free))+' free of '+esc(fmtBytes(d.total))+'</b><span style="flex:none;font:500 12px var(--mono);color:var(--soft)">'+esc(d.used_pct)+'% used</span></div><div class="bar" style="height:8px;margin:6px 0 0"><span style="width:'+clampPct(d.used_pct)+'%;background:'+dBarColor(d)+'"></span></div>':'')+
    '<div class="dstats">'+stats.map(s=>'<div><b>'+esc(s[0])+'</b><span>'+esc(s[1])+'</span></div>').join('')+'</div>'+
    notes+(notes?'':info)+(d.uses.length?'<div class="dnote">'+esc(d.uses.join(' · '))+(d.temp?' · alert at '+esc(Math.round(d.temp_limit))+'°C':'')+'</div>':'')+
    '<div class="dio" data-io="'+esc(d.details&&d.details.id)+'">'+ioText(driveIO[d.details&&d.details.id])+'</div><div class="more">Details ›</div></button>';
}
let driveIO={},driveOpen=null;
function ioText(x){if(!x)return '<span>Activity: —</span>';return '<span>Read <b>'+esc(fmtBytes(x.read))+'/s</b></span><span>Write <b>'+esc(fmtBytes(x.write))+'/s</b></span><span class="busy">Busy <b>'+esc(Math.round(x.busy))+'%</b></span>'}
async function refreshDriveIO(){
  try{driveIO=(await api('/api/drives/io')).io||{}}catch{return}
  document.querySelectorAll('[data-io]').forEach(el=>{el.innerHTML=ioText(driveIO[el.dataset.io])});
}
function dNum(v,unit){return v==null?'—':Number(v).toLocaleString()+(unit||'')}
function oneTempChart(d){
  const pts=d.history||[];if(pts.length<2)return '<p class="hint" style="margin:0">The graph fills in over the first day (one reading every 10 minutes).</p>';
  const vals=pts.map(p=>p[1]),lo0=Math.min(...vals),hi0=Math.max(...vals),avg=vals.reduce((a,b)=>a+b,0)/vals.length;
  const now=Date.now()/1000,start=now-86400,hi=Math.max(hi0,d.temp_limit)+2,lo=Math.max(0,lo0-5),W=330,H=110;
  const x=t=>((t-start)/86400*W).toFixed(1),y=v=>(H-(v-lo)/(hi-lo)*H).toFixed(1);
  return '<svg width="100%" height="110" viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none" role="img" aria-label="Temperature over the last 24 hours"><line x1="0" x2="'+W+'" y1="'+y(d.temp_limit)+'" y2="'+y(d.temp_limit)+'" stroke="#6B5414" stroke-dasharray="4 4"/><polyline fill="none" stroke="var(--teal)" stroke-width="2" stroke-linejoin="round" points="'+pts.map(p=>x(p[0])+','+y(p[1])).join(' ')+'"/></svg>'+
    '<div class="legend"><span>Low <b style="color:var(--white)">'+Math.round(lo0)+'°C</b></span><span>Average <b style="color:var(--white)">'+Math.round(avg)+'°C</b></span><span>High <b style="color:var(--white)">'+Math.round(hi0)+'°C</b></span><span style="margin-left:auto;color:#D6C7A0">- - '+Math.round(d.temp_limit)+'°C alert</span></div>';
}
function kv(rows){return '<div class="kv">'+rows.filter(r=>r).map(r=>'<div>'+esc(r[0])+'</div><div'+(r[2]?' class="'+r[2]+'"':'')+'>'+esc(r[1])+'</div>').join('')+'</div>'}
function openDrive(key){
  const d=(drivesData&&drivesData.drives||[]).find(x=>x.key===key);if(!d)return;driveOpen=key;
  const x=d.details||{},io=driveIO[x.id];
  const other=d.key&&d.key.startsWith('vol:');
  const health=other?kv([['Type',d.media||'—'],['Health (Windows)',d.health]]):kv([
    ['Health (Windows)',d.health,d.health==='Healthy'?'good':(d.health==='Unknown'?'':'bad')],
    x.status?['Status',x.status]:null,
    ['Temperature now',d.temp?d.temp+'°C':'—',d.temp&&d.temp>=d.temp_limit?'bad':(d.temp&&d.temp>=d.temp_limit-4?'warn':'')],
    ['Highest recorded',x.temp_max?x.temp_max+'°C':'—'],
    ['Alert at',Math.round(d.temp_limit)+'°C'],
    ['Powered on',d.hours?dNum(d.hours,' h')+' ('+dYears(d.hours)+')':'—'],
    d.media==='SSD'?['SSD wear',d.wear!=null?d.wear+'% used':'—',d.wear>=80?'bad':(d.wear>=50?'warn':'')]:null,
    ['Uncorrected read errors',dNum(x.read_errors),x.read_errors?'bad':''],
    ['Uncorrected write errors',dNum(x.write_errors),x.write_errors?'bad':''],
    ['Corrected read / write',dNum(x.read_fixed)+' / '+dNum(x.write_fixed)],
    ['Start / stop cycles',dNum(x.starts)],
    d.media==='HDD'?['Head park cycles',dNum(x.loads)]:null,
    ['Slowest read / write',(x.read_lat!=null?dNum(x.read_lat,' ms'):'—')+' / '+(x.write_lat!=null?dNum(x.write_lat,' ms'):'—')]]);
  const ident=kv([['Model',x.model||d.name],x.serial?['Serial (end)',x.serial]:null,x.firmware?['Firmware',x.firmware]:null,['Type',[d.media,d.rpm?d.rpm+' rpm':''].filter(Boolean).join(' · ')||'—'],['Connection',d.bus||'—'],['Capacity',fmtBytes(d.size||d.total)],
    x.remote?['Shared from',x.remote]:null,x.style?['Partition style',x.style]:null,x.partitions!=null?['Partitions',String(x.partitions)]:null,(x.sector||x.lsector)?['Sector size',(x.lsector||'?')+' / '+(x.sector||'?')+' bytes']:null,
    ['Boot / system disk',(x.boot?'boot':'')+(x.boot&&x.system?' + ':'')+(x.system?'system':'')||'no'],['Disk number',x.id||'—']]);
  const vols=(d.volumes||[]).map(v=>'<div class="vrow"><div class="h"><b>'+esc((v.letter!=='/'?v.letter+': ':'')+(v.label||'Volume'))+'</b><span>'+esc([v.fs,v.health&&v.health!=='Healthy'?v.health:''].filter(Boolean).join(' · '))+'</span></div><div class="bar" style="height:6px;margin:7px 0 5px"><span style="width:'+clampPct(v.used_pct)+'%;background:'+dBarColor(v)+'"></span></div><div class="h"><span>'+esc(fmtBytes(v.free))+' free of '+esc(fmtBytes(v.total))+'</span><span>'+esc(v.used_pct)+'% used</span></div></div>').join('')||'<p class="hint" style="margin:0">No drive letters on this disk.</p>';
  const panel=$('sheetpanel');
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button><div class="dbody" style="padding-top:20px">'+
    '<div class="row" style="gap:12px;margin-right:48px"><span style="flex:none;font:800 22px var(--mono)">'+esc(dLetters(d)||'—')+'</span><div style="min-width:0"><h2 style="margin:0;font-size:19px">'+dTitle(d)+'</h2><div class="hint">'+esc(d.name)+'</div></div></div>'+
    '<div style="margin-top:10px">'+dTag(d)+'</div>'+d.problems.map(p=>'<div class="dnote '+(d.status==='bad'?'bad':'warn')+'">'+esc(p)+'</div>').join('')+
    (d.days_left!=null?'<div class="dnote warn">About <b>'+esc(d.days_left)+' days</b> of space left at your pace ('+esc(fmtBytes(d.daily_bytes))+'/day).</div>':'')+
    '<div class="section">SPACE</div><div class="card">'+vols+'</div>'+
    '<div class="section">ACTIVITY NOW</div><div class="card"><div class="dio" style="margin:0" data-io="'+esc(x.id)+'">'+ioText(io)+'</div></div>'+
    '<div class="section">HEALTH</div><div class="card">'+health+(x.smart?'':'<p class="hint" style="margin:8px 0 0">'+(x.kind==='network'?'A network drive: its health and temperature are on the computer or NAS that shares it.':(x.kind?'Windows doesn\'t report health counters for this kind of drive (for example Storage Spaces, a USB enclosure or a virtual disk).':'Windows didn\'t share this drive\'s health counters. Running NZB Mobile Server as administrator usually fixes that.'))+'</p>')+'</div>'+
    '<div class="section">TEMPERATURE · 24 H</div><div class="card">'+oneTempChart(d)+'</div>'+
    '<div class="section">THIS DRIVE</div><div class="card">'+ident+(d.uses.length?'<p class="hint" style="margin:8px 0 0">Used for: '+esc(d.uses.join(', '))+'</p>':'')+'</div></div>';
  $('sheet').classList.remove('hidden');document.body.style.overflow='hidden';
  if(!sheetOpen){sheetOpen=true;try{history.pushState({sheet:1},'')}catch{}}
  panel.scrollTop=0;
}
function renderDrives(r){
  drivesData=r;const t=r.totals,ds=r.drives;
  const ago=r.checked?Math.max(0,Math.round((Date.now()/1000-r.checked)/60)):null;
  $('drivestatus').textContent=ds.length+' drive'+(ds.length===1?'':'s')+(ago!=null?' · checked '+(ago<1?'just now':ago+' min ago'):'');
  if(!ds.length){$('drivebody').innerHTML=empty('No drives found',r.problem||'Windows did not report any drives.');return}
  const worst=ds.find(d=>d.status==='bad')||ds.find(d=>d.status==='warn');
  const banner=worst?'<div class="banner '+worst.status+'"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="color:'+(worst.status==='bad'?'var(--red)':'var(--amber)')+';flex:none" aria-hidden="true"><path d="M12 3l9 16H3z"/><path d="M12 10v4M12 17h.01"/></svg><div><b>'+t.warn+' drive'+(t.warn===1?' needs':'s need')+' a look</b><span>'+esc((dLetters(worst)?dLetters(worst)+' ':'')+(worst.label||worst.name)+': '+(worst.problems[0]||''))+'</span></div></div>'
    :'<div class="banner ok"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="var(--green)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="flex:none" aria-hidden="true"><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/></svg><div><b>All drives healthy</b><span>Windows reports no problems.</span></div></div>';
  const tiles='<div class="tiles"><div class="tile"><div class="k">Healthy</div><div class="v" style="color:var(--green)">'+t.ok+'</div></div><div class="tile"><div class="k">Need a look</div><div class="v" style="color:'+(t.warn?'var(--amber)':'var(--soft)')+'">'+t.warn+'</div></div><div class="tile"><div class="k">Free of '+esc(fmtBytes(t.total))+'</div><div class="v">'+esc(fmtBytes(t.free))+'</div></div></div>';
  const tempHint=r.temps_available?'':'<p class="hint" style="margin:0 4px 12px">Temperatures and powered-on hours need NZB Mobile Server to run as administrator, or LibreHardwareMonitor running with its web server on (port 8085).</p>';
  const a=r.alerts||{};
  const alerts='<div class="section">ALERTS TO MY PHONE</div><div class="card" id="drivealerts">'+
    '<label class="tgl"><span>Health changes<small>Windows reports a drive problem</small></span><input type="checkbox" data-dalert="notify_drive_health"'+(a.health?' checked':'')+'></label>'+
    '<label class="tgl"><span>Too hot<small>Hard drives '+esc(r.temp_alert)+'°C · NVMe '+esc(r.nvme_temp_alert)+'°C</small></span><input type="checkbox" data-dalert="notify_drive_hot"'+(a.hot?' checked':'')+'></label>'+
    '<label class="tgl"><span>Less than '+esc(a.low_gb||75)+' GB free</span><input type="checkbox" data-dalert="notify_drive_low"'+(a.low?' checked':'')+'></label>'+
    (a.ready?'':'<p class="hint" style="margin:8px 0 0">Phone alerts aren\'t set up yet: they use ntfy, the same as download alerts.</p>')+'</div>';
  $('drivebody').innerHTML=banner+tiles+tempHint+'<div class="section">ALL DRIVES</div>'+ds.map(driveCard).join('')+tempChart(ds)+alerts+(r.problem?'<p class="hint">'+esc(r.problem)+'</p>':'');
  renderHomeDrives(r);
}
async function refreshDrives(force){
  if(drivesBusy)return;drivesBusy=true;
  if(!drivesData){$('drivebody').innerHTML=skeleton(3,3)}else renderDrives(drivesData);
  const b=$('drivecheck');if(force){b.disabled=true;b.textContent='Checking…'}
  try{renderDrives(await api(force?'/api/drives/check':'/api/drives',force?{}:undefined));drivesAt=Date.now();if(force)toast('Drives checked')}
  catch(e){if(!drivesData)$('drivebody').innerHTML=empty('Couldn\'t read your drives',e.message);else toast(e.message,true)}
  finally{drivesBusy=false;b.disabled=false;b.textContent='Check now'}
}
async function setDriveAlert(key,on){try{const body={};body[key]=on;renderDrives(await api('/api/drives/alerts',body));toast(on?'Alert turned on':'Alert turned off')}catch(e){toast(e.message,true)}}

// ---------- Jellyfin ----------
let jf=null,jfMode='',jfBusy=false,jfMissing=null,jfUp=null,jfResults=null,jfQuery='',jfConnectOpen=false;
const JF_ICON='<svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l9.5 17h-19z"/><path d="M12 10l4 7H8z"/></svg>';
function jfNowCard(x){
  const pp=x.paused?ICON.play:ICON.pause;
  return '<div class="hero jnow"><div class="nowrow"><div class="pthumb">'+artTag(x.poster)+'</div><div class="info"><div class="row" style="justify-content:space-between"><div class="kode" style="flex:1">'+(x.paused?'PAUSED':'NOW PLAYING')+'</div>'+(x.method==='Transcoding'?'<span class="tag tx" style="flex:none">TRANSCODING</span>':'')+'</div><div class="name">'+esc(x.title)+'</div>'+(x.sub?'<div class="sub">'+esc(x.sub)+'</div>':'')+
    '<div class="meta">'+esc([x.user,x.device,x.method==='Transcoding'?'':x.method,x.quality].filter(Boolean).join(' · '))+'</div></div></div>'+
    '<div class="bar"><span style="width:'+clampPct(x.pct)+'%"></span></div><div class="ctrls"><span class="tm">'+fmtTime(x.time)+' / '+fmtTime(x.total)+'</span>'+
    '<button class="round" data-jact="stop" data-sid="'+esc(x.session)+'" aria-label="Stop"><svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/></svg></button>'+
    '<button class="round main" data-jact="playpause" data-sid="'+esc(x.session)+'" aria-label="'+(x.paused?'Play':'Pause')+'">'+pp+'</button></div></div>';
}
function renderJfNow(list){const box=$('jfnow');box.innerHTML=(list||[]).map(jfNowCard).join('');hydrateArt(box)}
async function refreshJfNow(){if(!jf||!jf.online)return;try{renderJfNow((await api('/api/jellyfin/now')).sessions)}catch{}}
function jfConnectForm(prefill){
  return '<div class="card"><div class="kode" style="margin-bottom:10px">CONNECT JELLYFIN</div>'+
    '<div id="jffound"><button class="btn ghost sm full" id="jffind">Find Jellyfin on my network</button></div>'+
    '<label class="label" for="jfurl" style="margin-top:12px">Server address</label><input id="jfurl" class="field" placeholder="http://192.168.68.60:8096" autocomplete="off" autocapitalize="off" autocorrect="off" inputmode="url" value="'+esc(prefill||'')+'">'+
    '<label class="label" for="jfkey">API key</label><input id="jfkey" class="field" type="password" placeholder="From Jellyfin: Dashboard → API Keys → +" autocomplete="off" autocapitalize="off" autocorrect="off">'+
    '<button class="btn full" id="jfsave">Test &amp; save</button><p class="hint" style="margin:10px 2px 0">In Jellyfin open Dashboard → API Keys, tap +, name it “NZB Command Center” and copy the key. You can also set it up in the desktop app (Media Server tab).</p></div>';
}
function jfQuick(){
  const c=jf.counts||{};
  const q=(mode,title,sub,ct,cls)=>'<button class="jq'+(jfMode===mode?' on':'')+'" data-jmode="'+mode+'">'+(ct!=null?'<span class="ct'+(cls?' '+cls:'')+'">'+esc(ct)+'</span>':'')+'<b>'+title+'</b><span>'+sub+'</span></button>';
  return '<div class="jgrid">'+q('missing','Missing episodes','Gaps in your shows',jfMissing?jfMissing.reduce((n,x)=>n+x.missing.length,0):null,'')+q('upgrades','Low quality','Movies below 1080p',jfUp?jfUp.length:null,'warn')+'</div>';
}
function jfItemRow(x){
  const tap=x.type==='Series'?' data-jshow="'+esc(x.id)+'"':' data-jmovie="'+esc(x.id)+'"';
  const meta=x.type==='Series'?[x.year,x.episodes?x.episodes+' episodes':'','TV show'].filter(Boolean).join(' · '):[x.year,x.size?fmtBytes(x.size):'',x.watched?'watched':(x.resume?'resume at '+fmtTime(x.resume):'')].filter(Boolean).join(' · ');
  return '<div class="qrow"><button class="pthumb"'+tap+' aria-label="Details for '+esc(x.title)+'">'+artTag(x.poster)+'</button><div class="info tap"'+tap+'><div class="t">'+esc(x.title)+'</div><div class="m">'+esc(meta)+'</div>'+(x.type==='Movie'?'<div class="rl"><span class="badge lib">✓ In Jellyfin</span>'+(x.quality?'<span class="qual'+(x.quality==='SD'||x.quality==='720p'?' lo':'')+'">'+esc(x.quality)+'</span>':'')+'</div>':'')+'</div>'+
    (x.type==='Movie'?'<button class="btn ghost sm" style="flex:none" data-jbetter="'+esc(x.title+(x.year?' '+x.year:''))+'">'+(x.quality==='2160p'?'Search':'Better copy')+'</button>':'')+'</div>';
}
function jfMissingRow(x){
  const eps=x.missing.map(n=>'E'+String(n).padStart(2,'0')),q=x.show+' S'+String(x.season).padStart(2,'0')+(x.missing.length===1?'E'+String(x.missing[0]).padStart(2,'0'):'');
  return '<div class="miss"><span data-jshow="'+esc(x.show_id)+'" style="cursor:pointer"><b>'+esc(x.show)+'</b> · Season '+esc(x.season)+'<br>Missing '+esc(eps.slice(0,12).join(', ')+(eps.length>12?' …':''))+'</span><button class="btn sm" data-tvsearch="'+esc(q)+'">Search</button></div>';
}
async function jfList(){
  const box=$('jflist');if(!box)return;
  if(jfMode==='search'){
    if(jfResults===null){box.innerHTML=skeleton(3,2);try{jfResults=(await api('/api/jellyfin/search?q='+encodeURIComponent(jfQuery))).items||[]}catch(e){box.innerHTML=empty('Search failed',e.message);return}}
    box.innerHTML='<div class="section">'+jfResults.length+' IN YOUR LIBRARY</div>'+(jfResults.map(jfItemRow).join('')||empty('Not in Jellyfin','You don\'t have “'+jfQuery+'” yet.'))+
      '<button class="btn ghost full" data-jbetter="'+esc(jfQuery)+'" data-jcat="" style="margin-top:4px">Search your indexers for “'+esc(jfQuery)+'”</button>';
    hydrateArt(box);return;
  }
  if(jfMode==='missing'){
    if(jfMissing===null){box.innerHTML='<div class="section">CHECKING YOUR SHOWS…</div>'+skeleton(4,2);try{jfMissing=(await api('/api/jellyfin/missing')).missing||[]}catch(e){box.innerHTML=empty('Couldn\'t check',e.message);return}renderJellyfin();return}
    box.innerHTML='<div class="section">'+jfMissing.length+' SEASON'+(jfMissing.length===1?'':'S')+' WITH GAPS</div>'+(jfMissing.slice(0,80).map(jfMissingRow).join('')||empty('No gaps found','Every season has all its episodes up to the newest one you have.'));return;
  }
  if(jfMode==='upgrades'){
    if(jfUp===null){box.innerHTML='<div class="section">CHECKING QUALITY…</div>'+skeleton(4,2);try{jfUp=(await api('/api/jellyfin/upgrades')).movies||[]}catch(e){box.innerHTML=empty('Couldn\'t check',e.message);return}renderJellyfin();return}
    box.innerHTML='<div class="section">'+jfUp.length+' MOVIE'+(jfUp.length===1?'':'S')+' BELOW 1080P</div>'+(jfUp.slice(0,100).map(jfItemRow).join('')||empty('All good','Every movie is 1080p or better.'));hydrateArt(box);return;
  }
  const r=jf.recent||[];
  box.innerHTML=r.length?'<div class="section">RECENTLY ADDED</div><div class="cw">'+r.map(x=>{const tap=x.type==='Episode'?' data-jshow="'+esc(x.show_id)+'"':' data-jmovie="'+esc(x.id)+'"';return '<button class="citem"'+tap+'><div class="ph">'+artTag(x.poster)+'</div><div class="t">'+esc(x.type==='Episode'?x.show:x.title)+'</div><div class="m">'+esc(x.type==='Episode'?'S'+String(x.season).padStart(2,'0')+'E'+String(x.episode).padStart(2,'0'):(x.year||''))+'</div></button>'}).join('')+'</div>':'';
  hydrateArt(box);
}
function renderJellyfin(){
  const st=$('jfstatus'),body=$('jfbody');$('jfscan').classList.toggle('hidden',!(jf&&jf.online));
  if(!jf||!jf.configured){st.className='lede';st.textContent='Not connected yet.';$('jfnow').innerHTML='';body.innerHTML=jfConnectForm('');return}
  if(!jf.online){st.className='lede status error';st.textContent=jf.problem||'Jellyfin is offline';$('jfnow').innerHTML='';
    const badKey=/HTTP 40[13]/.test(jf.problem||'');
    body.innerHTML='<div class="card kidle">'+JF_ICON+'<span>'+(badKey?'Jellyfin at '+esc(jf.url)+' refused the API key'+(jf.source==='desktop'?' saved in the desktop app':'')+'. Enter a current key below.':'Can\'t reach Jellyfin at '+esc(jf.url)+'. Is the server on?')+'</span></div>'+((jfConnectOpen||badKey)?jfConnectForm(jf.url):'<button class="btn ghost full" id="jfchange">Change connection</button>');return}
  st.className='lede';const c=jf.counts;
  st.textContent=[jf.server+(jf.version?' · v'+jf.version:''),jf.ms+' ms',c?c.movies+' movie'+(c.movies===1?'':'s')+' · '+c.shows+' show'+(c.shows===1?'':'s'):''].filter(Boolean).join(' · ');
  renderJfNow(jf.sessions);
  const nothing=(jf.sessions||[]).length?'':'<div class="card kidle">'+ICON.play+'<span>Nothing playing on Jellyfin right now.</span></div>';
  body.innerHTML=nothing+'<label class="search"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#7F9DA6" stroke-width="2.2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg><input id="jfq" placeholder="Do I already have it?" enterkeyhint="search" autocomplete="off" aria-label="Search your Jellyfin library" value="'+esc(jfQuery)+'"></label>'+
    jfQuick()+'<div id="jflist"></div>'+
    '<details class="opts" style="margin-top:14px"><summary>Connection<svg class="chev" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></summary><div class="body"><p class="hint" style="margin:0 0 10px">'+esc(jf.url)+' · set up '+(jf.source==='phone'?'from this phone':'in the desktop app')+'</p>'+
    (jf.source==='phone'?'<button class="btn danger sm full" id="jfforget">Disconnect</button>':'')+(jfConnectOpen?jfConnectForm(''):'<button class="btn ghost sm full" id="jfchange" style="margin-top:8px">Connect a different server</button>')+'</div></details>';
  jfList();
}
async function refreshJellyfin(){
  if(jfBusy)return;jfBusy=true;
  if(!jf){$('jfbody').innerHTML=skeleton(3,2)}
  try{jf=await api('/api/jellyfin');renderJellyfin()}
  catch(e){$('jfstatus').className='lede status error';$('jfstatus').textContent=e.message;if(!jf)$('jfbody').innerHTML=''}
  finally{jfBusy=false}
}
async function jfAction(sid,action){try{await api('/api/jellyfin/control',{session:sid,action});setTimeout(refreshJfNow,500)}catch(e){toast(e.message,true)}}
async function jfScan(){const b=$('jfscan');b.disabled=true;try{await api('/api/jellyfin/scan',{});jfMissing=null;jfUp=null;toast('Library scan started on Jellyfin')}catch(e){toast(e.message,true)}finally{b.disabled=false}}
async function jfFind(){
  const box=$('jffound');box.innerHTML='<p class="hint" style="margin:0">Looking on your network…</p>';
  try{const d=await api('/api/jellyfin/discover');const sv=d.servers||[];
    box.innerHTML=sv.length?'<div class="kode" style="margin-bottom:6px">FOUND ON YOUR NETWORK</div>'+sv.map(x=>'<button class="shortcut" data-jpick="'+esc(x.url)+'" style="color:var(--white)"><span style="flex:1"><b>'+esc(x.name)+'</b><br><small style="font-family:var(--mono);color:var(--soft)">'+esc(x.url)+'</small></span><span style="color:var(--teal);font-weight:700;font-size:13px">Use this</span></button>').join(''):'<p class="hint" style="margin:0">None found. Type the address below (the PC running Jellyfin, port 8096).</p>'}
  catch(e){box.innerHTML='<p class="hint" style="margin:0">'+esc(e.message)+'</p>'}
}
async function jfSave(){
  const b=$('jfsave');b.disabled=true;b.textContent='Testing…';
  try{jf=await api('/api/jellyfin/connect',{url:$('jfurl').value.trim(),key:$('jfkey').value.trim()});jfConnectOpen=false;jfMissing=jfUp=jfResults=null;renderJellyfin();toast('Connected to Jellyfin')}
  catch(e){toast(e.message,true);b.disabled=false;b.textContent='Test & save'}
}
async function jfForget(){if(!confirm('Disconnect the Jellyfin server set up on this phone?'))return;try{jf=await api('/api/jellyfin/forget',{});jfMissing=jfUp=jfResults=null;renderJellyfin()}catch(e){toast(e.message,true)}}
async function openJfShow(id){
  const panel=$('sheetpanel');
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button><div class="empty"><b>Loading…</b></div>';
  $('sheet').classList.remove('hidden');document.body.style.overflow='hidden';
  if(!sheetOpen){sheetOpen=true;try{history.pushState({sheet:1},'')}catch{}}
  let d;try{d=await api('/api/jellyfin/show?id='+encodeURIComponent(id))}catch(e){panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button>'+empty('Couldn\'t load this show',e.message);return}
  const n=d.next,openSeason=n?n.season:((d.seasons.find(s=>s.missing.length)||d.seasons[d.seasons.length-1]||{}).season);
  const totalMiss=d.seasons.reduce((a,s)=>a+s.missing.length,0);
  const seasons=d.seasons.map(s=>{
    const w=s.episodes.filter(e=>e.watched).length,name=s.season===0?'Specials':'Season '+s.season;
    const miss=s.missing.length?'<div class="miss"><span>Missing '+esc(s.missing.map(x=>'E'+String(x).padStart(2,'0')).join(', '))+'</span><button class="btn sm" data-tvsearch="'+esc(d.title+' S'+String(s.season).padStart(2,'0')+(s.missing.length===1?'E'+String(s.missing[0]).padStart(2,'0'):''))+'">Search</button></div>':'';
    const rows=s.episodes.map(e=>'<div class="eprow'+(e.watched?' w':'')+'"><span class="n">E'+String(e.episode).padStart(2,'0')+'</span><div class="info"><div class="t">'+esc(e.title)+'</div><div class="m">'+esc([e.aired,runtimeText(e.runtime),e.watched?'watched':''].filter(Boolean).join(' · '))+'</div></div></div>').join('');
    return '<details class="season"'+(s.season===openSeason?' open':'')+'><summary>'+esc(name)+'<span class="m">'+w+'/'+s.episodes.length+' watched'+(s.missing.length?' · '+s.missing.length+' missing':'')+'</span></summary>'+miss+rows+'</details>';
  }).join('');
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button><div class="fan" style="aspect-ratio:3/1"></div>'+
    '<div class="dhead"><button class="dposter" data-view="'+esc(d.poster)+'" aria-label="View poster full size">'+artTag(d.poster)+'</button><div class="dtitle"><h2>'+esc(d.title)+'</h2><div class="m">'+esc([d.year,d.episodes+' episodes',d.watched+' watched',totalMiss?totalMiss+' missing':''].filter(Boolean).join(' · '))+'</div></div></div>'+
    ((d.genres||[]).length?'<div class="chips">'+d.genres.map(g=>'<span class="chip">'+esc(g)+'</span>').join('')+'</div>':'')+
    '<div class="dbody">'+(d.plot?'<p class="plot">'+esc(d.plot)+'</p>':'')+(n?'<p class="hint">Next up: <b style="color:var(--white)">'+esc(epCode(n))+' · '+esc(n.title)+'</b></p>':'')+
    (totalMiss>1?'<button class="btn full" data-tvsearch="'+esc(d.title)+'" style="margin-bottom:6px">Search for this show</button>':'')+'<div style="margin-top:8px">'+seasons+'</div></div>';
  panel.scrollTop=0;hydrateArt(panel);
}
function openJfMovie(id){
  const pool=[].concat(jf&&jf.recent||[],jfResults||[],jfUp||[]),x=pool.find(m=>m.id===id);if(!x)return;
  const panel=$('sheetpanel');
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button><div class="fan" style="aspect-ratio:3/1"></div><div class="dhead"><button class="dposter" data-view="'+esc(x.poster)+'" aria-label="View poster full size">'+artTag(x.poster)+'</button><div class="dtitle"><h2>'+esc(x.title)+'</h2><div class="m">'+esc([x.year,runtimeText(x.runtime)].filter(Boolean).join(' · '))+'</div></div></div>'+
    '<div class="dbody"><div class="rl" style="margin:14px 0"><span class="badge lib">✓ In Jellyfin</span>'+(x.quality?'<span class="qual'+(x.quality==='SD'||x.quality==='720p'?' lo':'')+'">'+esc(x.quality)+'</span>':'')+(x.size?'<span class="rc">'+esc(fmtBytes(x.size))+'</span>':'')+(x.watched?'<span class="rc">watched</span>':'')+'</div>'+
    '<button class="btn full" data-jbetter="'+esc(x.title+(x.year?' '+x.year:''))+'">Find a better copy</button></div>';
  $('sheet').classList.remove('hidden');document.body.style.overflow='hidden';
  if(!sheetOpen){sheetOpen=true;try{history.pushState({sheet:1},'')}catch{}}
  hydrateArt(panel);
}

async function kodiType(done){
  const box=$('kodytext'),text=box.value,b1=$('kodysend'),b2=$('kodysendonly');
  if(!text){box.focus();return}
  b1.disabled=b2.disabled=true;
  try{const r=await api('/api/kodi/text',{text,done});box.value='';
    toast(r.window==='field'?'Sent. If it didn\'t appear, select the text field on the TV and send again.':(done?'Typed on the TV and confirmed':'Typed on the TV'))}
  catch(e){toast(e.message,true)}
  finally{b1.disabled=b2.disabled=false}
}

// ---------- Phone alerts (ntfy) ----------
function ntfyTopic(){const a='abcdefghjkmnpqrstuvwxyz23456789';let t='nzbcc-';const r=new Uint8Array(12);(window.crypto||{}).getRandomValues?crypto.getRandomValues(r):r.forEach((_,i)=>r[i]=Math.random()*256);r.forEach(x=>t+=a[x%a.length]);return 'https://ntfy.sh/'+t}
async function openAlerts(){
  const panel=$('sheetpanel');
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button><div class="empty"><b>Loading…</b></div>';
  $('sheet').classList.remove('hidden');document.body.style.overflow='hidden';
  if(!sheetOpen){sheetOpen=true;try{history.pushState({sheet:1},'')}catch{}}
  let st,dr;try{[st,dr]=await Promise.all([api('/api/settings'),api('/api/drives').catch(()=>null)])}catch(e){panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button>'+empty('Couldn\'t load the alert settings',e.message);return}
  const da=(dr&&dr.alerts)||{},on=!!st.ntfy_url;
  const tg=(id,label,sub,checked)=>'<label class="tgl"><span>'+label+(sub?'<small>'+sub+'</small>':'')+'</span><input type="checkbox" id="'+id+'"'+(checked?' checked':'')+'></label>';
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button><div class="dbody" style="padding-top:20px">'+
    '<h2 style="margin:0 44px 6px 0;font-size:20px">Phone alerts</h2><p class="hint" style="margin:0 0 14px">'+(on?'Alerts are <b style="color:var(--green)">on</b>.':'Get a notification on this iPhone when a download finishes or a drive needs a look. Free, using the <b>ntfy</b> app.')+'</p>'+
    (on?'':'<div class="card"><div class="kode" style="margin-bottom:8px">SET UP (ONE TIME)</div><ol class="hint" style="margin:0;padding-left:18px;line-height:1.7"><li>Install <b>ntfy</b> from the App Store.</li><li>Tap <b>Use a new private topic</b> below, then <b>Copy</b>.</li><li>In ntfy tap <b>+</b>, paste the topic name, <b>Subscribe</b>, and allow notifications.</li><li>Tap <b>Save</b>, then <b>Send a test</b>.</li></ol></div>')+
    '<div class="alfield"><label class="label" for="alurl">ntfy topic address</label><div class="altop"><input id="alurl" class="field" placeholder="https://ntfy.sh/your-private-topic" autocomplete="off" autocapitalize="off" autocorrect="off" inputmode="url" value="'+esc(st.ntfy_url||'')+'"><button class="btn ghost sm" id="alcopy" style="flex:none">Copy</button></div>'+
    '<button class="btn ghost sm full" id="alnew" style="margin-top:8px">Use a new private topic</button><p class="hint" style="margin:6px 2px 0">Anyone who knows the topic name can read your alerts, so keep it long and random.</p></div>'+
    '<details class="opts"><summary>Access token (only for a private ntfy server)<svg class="chev" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></summary><div class="body"><input id="altoken" class="field" type="password" placeholder="'+(st.ntfy_token_set?'Saved (leave empty to keep it)':'Not needed for ntfy.sh')+'" autocomplete="off"></div></details>'+
    '<div class="section">TELL ME WHEN</div><div class="card">'+
    tg('alcomplete','A download finishes','',st.notify_complete)+tg('alfailed','A download fails','',st.notify_failed)+
    tg('aldhealth','A drive has a problem','Windows reports it unhealthy',da.health!==false)+tg('aldhot','A drive is too hot','',da.hot!==false)+tg('aldlow','A drive is almost full','Less than '+esc(da.low_gb||75)+' GB free',da.low!==false)+'</div>'+
    '<div class="row" style="margin-top:14px"><button class="btn" id="alsave">Save</button><button class="btn ghost" id="altest">Send a test</button></div>'+
    (on?'<button class="btn danger sm full" id="aloff" style="margin-top:12px">Turn alerts off</button>':'')+'</div>';
  panel.scrollTop=0;
  const urlEl=$('alurl');
  $('alnew').onclick=()=>{urlEl.value=ntfyTopic();toast('New private topic ready. Tap Copy, then subscribe to it in ntfy.')};
  $('alcopy').onclick=async()=>{const v=urlEl.value.trim();if(!v){toast('Make a topic first',true);return}const name=v.split('/').pop();try{await navigator.clipboard.writeText(name);toast('Copied “'+name+'”. In ntfy tap +, paste, Subscribe.')}catch{urlEl.select();toast('Select the text and copy it: '+name)}};
  const save=async off=>{
    const body={ntfy_url:off?'':urlEl.value.trim(),saved_interval_hours:st.saved_interval_hours||6,notify_complete:$('alcomplete').checked,notify_failed:$('alfailed').checked};
    const t=$('altoken').value.trim();if(t)body.ntfy_token=t;if(off)body.clear_token=true;
    await api('/api/settings',body);
    await api('/api/drives/alerts',{notify_drive_health:$('aldhealth').checked,notify_drive_hot:$('aldhot').checked,notify_drive_low:$('aldlow').checked}).catch(()=>{});
  };
  $('alsave').onclick=async()=>{try{await save(false);toast(urlEl.value.trim()?'Alerts saved. Tap “Send a test” to check.':'Saved (alerts are off without a topic).');openAlerts()}catch(e){toast(e.message,true)}};
  $('altest').onclick=async()=>{try{await save(false);await api('/api/settings/test',{});toast('Test sent. It should pop up on this iPhone in a few seconds.')}catch(e){toast(e.message,true)}};
  if($('aloff'))$('aloff').onclick=async()=>{if(!confirm('Turn phone alerts off?'))return;try{await save(true);toast('Alerts turned off');openAlerts()}catch(e){toast(e.message,true)}};
}

// ---------- Sonarr & Radarr ("Movies & shows") ----------
let pvr=null,pvrKind='radarr',pvrQuery='',pvrResults=null,pvrUp=null,pvrUpErr='',pvrBusy=false,pvrOpts={},pvrAdding=null,homeUpKey='';
const SEARCH_SVG='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#7F9DA6" stroke-width="2.2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>';
function pvrPrefs(){try{return JSON.parse(LS.get('nzb_mobile_pvr','{}'))||{}}catch{return {}}}
function pvrApp(a){return ((pvr&&pvr.apps)||[]).find(x=>x.app===a)||{name:a==='radarr'?'Radarr':'Sonarr'}}
const pad2=n=>String(n).padStart(2,'0');
function dayLabel(d){const t=new Date();t.setHours(0,0,0,0);const x=new Date(d);x.setHours(0,0,0,0);const diff=Math.round((x-t)/864e5);
  if(diff===0)return 'Today';if(diff===1)return 'Tomorrow';if(diff===-1)return 'Yesterday';return x.toLocaleDateString([],{weekday:'short',day:'numeric',month:'short'})}
function upWhen(x){
  if(!x.when)return '';
  if(x.kind==='movie')return dayLabel(new Date(x.when.slice(0,10)+'T12:00:00'));
  const d=new Date(x.when);if(isNaN(d))return x.when.slice(0,10);
  const day=dayLabel(d),tm=d.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});
  return (day==='Today'&&d.getHours()>=17?'Tonight':day)+' · '+tm;
}
function upSub(x){return x.kind==='episode'?'S'+pad2(x.season)+'E'+pad2(x.episode)+(x.episode_title&&!/^TBA$/i.test(x.episode_title)?' · '+x.episode_title:''):(x.release||'Release')+(x.year?' · '+x.year:'')}
function upRow(x){
  return '<div class="qrow"><div class="pthumb" style="cursor:default">'+artTag(x.poster)+'</div><div class="info"><div class="t">'+esc(x.title)+'</div><div class="m">'+esc(upSub(x))+'</div><div class="upw">'+esc(upWhen(x))+(x.kind==='movie'?'':(x.network?' · '+esc(x.network):''))+'</div></div>'+
    (x.has_file?'<span class="badge lib" style="flex:none">✓ Got it</span>':'<span class="uptag '+(x.kind==='movie'?'mv':'tv')+'">'+(x.kind==='movie'?'MOVIE':'TV')+'</span>')+'</div>';
}
function renderHomeUp(items){
  const box=$('homeup'),key=JSON.stringify(items.map(x=>[x.title,x.when,x.season,x.episode]));
  if(key===homeUpKey)return;homeUpKey=key;
  box.innerHTML=items.length?'<div class="section">COMING UP</div>'+items.map(upRow).join('')+'<button class="btn ghost full sm" data-tab="pvr" style="margin-top:2px">Movies &amp; shows</button>':'';
  hydrateArt(box);
}
function pvrRow(x){
  const right=x.in_library?'<span class="badge lib" style="flex:none">✓ '+(x.has_file?'Have it':'Added')+'</span>':'<button class="btn sm" style="flex:none" data-pvradd="'+esc(x.ext_id)+'">Add</button>';
  return '<div class="qrow" style="align-items:flex-start"><div class="pthumb" style="cursor:default">'+artTag(x.poster)+'</div><div class="info"><div class="t">'+esc(x.title)+'</div><div class="m">'+esc([x.year,x.extra].filter(Boolean).join(' · '))+'</div>'+(x.overview?'<div class="pov">'+esc(x.overview)+'</div>':'')+'</div>'+right+'</div>';
}
async function pvrList(){
  const box=$('pvrlist');if(!box)return;
  if(!pvrQuery){box.innerHTML='';return}
  const a=pvrApp(pvrKind);
  if(!a.online){box.innerHTML=empty(a.name+(a.configured?' isn\'t answering':' wasn\'t found'),a.problem||('Start '+a.name+' on the PC, then pull down to refresh.'));return}
  if(pvrResults===null){box.innerHTML=skeleton(3,2);
    try{pvrResults=(await api('/api/arr/lookup?app='+pvrKind+'&q='+encodeURIComponent(pvrQuery))).items||[]}
    catch(e){box.innerHTML=empty('Couldn\'t look it up',e.message);return}}
  box.innerHTML='<div class="section">'+pvrResults.length+' FOUND</div>'+(pvrResults.map(pvrRow).join('')||empty('Nothing found','Try another spelling, or add the year.'));
  hydrateArt(box);
}
function renderPvrUp(){
  const box=$('pvrup');if(!box)return;
  if(pvrUp===null){box.innerHTML='<div class="section">COMING UP · NEXT 14 DAYS</div>'+skeleton(3,2);return}
  box.innerHTML='<div class="section">COMING UP · NEXT 14 DAYS</div>'+(pvrUp.length?pvrUp.map(upRow).join(''):(pvrUpErr?empty('Couldn\'t load the calendar',pvrUpErr):empty('Nothing coming up','New episodes of your shows in Sonarr and movies you add to Radarr show up here.')));
  hydrateArt(box);
}
function renderPvr(){
  const st=$('pvrstatus'),body=$('pvrbody'),apps=(pvr&&pvr.apps)||[];
  st.className='lede';
  st.textContent=apps.map(a=>a.name+' '+(a.online?'✓'+(a.queue?' · '+a.queue+' downloading':''):(a.configured?'offline':'not found'))).join(' · ');
  if(!apps.some(a=>a.configured)){body.innerHTML='<div class="card kidle"><span>Radarr and Sonarr weren\'t found on this PC. The phone server looks in MediaAutomation on each drive (for example Z:\\MediaAutomation) and in C:\\ProgramData. Start them on the PC, then pull down to refresh.</span></div>';return}
  const kinds=['radarr','sonarr'].map(a=>'<button data-pvrkind="'+a+'"'+(pvrKind===a?' class="on"':'')+'>'+(a==='radarr'?'Movie':'TV show')+'</button>').join('');
  body.innerHTML='<div class="card"><div class="seg" style="margin:0 0 10px">'+kinds+'</div><label class="search" style="margin:0">'+SEARCH_SVG+'<input id="pvrq" placeholder="'+(pvrKind==='radarr'?'Find a movie to add':'Find a TV show to add')+'" enterkeyhint="search" autocomplete="off" aria-label="Find something to add" value="'+esc(pvrQuery)+'"></label>'+
    '<p class="hint" style="margin:8px 2px 0">'+(pvrKind==='radarr'?'Radarr':'Sonarr')+' grabs it from your indexers and files it in your library. Looking up costs no indexer hits.</p></div><div id="pvrlist"></div><div id="pvrup"></div>';
  pvrList();renderPvrUp();
}
async function loadPvrUp(force){try{pvrUp=(await api('/api/arr/upcoming'+(force?'?fresh=1':''))).items||[];pvrUpErr=''}catch(e){pvrUp=[];pvrUpErr=e.message}renderPvrUp()}
async function refreshPvr(force){
  if(pvrBusy)return;pvrBusy=true;
  if(!pvr)$('pvrbody').innerHTML=skeleton(3,2);
  try{pvr=await api('/api/arr'+(force?'?fresh=1':''));if(force){pvrUp=null;pvrOpts={}}renderPvr();if(pvr.apps.some(a=>a.online)&&(pvrUp===null||force))loadPvrUp(force)}
  catch(e){$('pvrstatus').className='lede status error';$('pvrstatus').textContent=e.message;if(!pvr)$('pvrbody').innerHTML=''}
  finally{pvrBusy=false}
}
async function openPvrAdd(id){
  const x=(pvrResults||[]).find(r=>String(r.ext_id)===String(id));if(!x)return;
  const app=x.app,name=app==='radarr'?'Radarr':'Sonarr',panel=$('sheetpanel');
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button><div class="empty"><b>Loading…</b></div>';
  $('sheet').classList.remove('hidden');document.body.style.overflow='hidden';
  if(!sheetOpen){sheetOpen=true;try{history.pushState({sheet:1},'')}catch{}}
  let o;try{o=pvrOpts[app]||(pvrOpts[app]=await api('/api/arr/options?app='+app))}catch(e){panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button>'+empty('Couldn\'t reach '+name,e.message);return}
  if(!o.roots.length){panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button>'+empty(name+' needs a media folder','In '+name+' on the PC open Settings → Media Management → Add Root Folder, then try again.');delete pvrOpts[app];return}
  const p=pvrPrefs()[app]||{},sel=(v,cur)=>String(v)===String(cur)?' selected':'';
  const mons=(pvr&&pvr.monitor)||{future:'New episodes only',all:'Every episode'};
  pvrAdding=x;
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button>'+
    '<div class="dbody" style="padding-bottom:18px"><div class="nowrow pvrhead" style="margin:6px 48px 12px 0"><div class="pthumb" style="cursor:default">'+artTag(x.poster)+'</div><div class="info"><div class="kode">ADD TO '+name.toUpperCase()+'</div><div class="name">'+esc(x.title)+'</div><div class="sub">'+esc([x.year,x.extra].filter(Boolean).join(' · '))+'</div></div></div>'+
    (x.overview?'<p class="hint" style="margin:0 2px 12px">'+esc(x.overview)+'</p>':'')+
    '<label class="label" for="pvrprof">Quality</label><select id="pvrprof" class="field">'+o.profiles.map(q=>'<option value="'+esc(q.id)+'"'+sel(q.id,p.profile)+'>'+esc(q.name)+'</option>').join('')+'</select>'+
    (o.roots.length>1?'<label class="label" for="pvrroot">Save to</label><select id="pvrroot" class="field">'+o.roots.map(r=>'<option value="'+esc(r.path)+'"'+sel(r.path,p.root)+'>'+esc(r.path)+(r.free?' ('+fmtBytes(r.free)+' free)':'')+'</option>').join('')+'</select>':'<p class="hint" style="margin:0 2px 10px">Saves to '+esc(o.roots[0].path)+'</p>')+
    (app==='sonarr'?'<label class="label" for="pvrmon">Get</label><select id="pvrmon" class="field">'+Object.entries(mons).map(([k,v])=>'<option value="'+esc(k)+'"'+sel(k,p.monitor||'future')+'>'+esc(v)+'</option>').join('')+'</select>':'')+
    '<label class="chk"><input type="checkbox" id="pvrsearch"'+(p.search===false?'':' checked')+'> <span>'+(app==='radarr'?'Look for it now':'Look for the episodes now')+'<small>Checks your indexers once. Otherwise '+name+' waits for new releases.</small></span></label>'+
    '<button class="btn full" id="pvrgo">Add to '+name+'</button></div>';
  hydrateArt(panel);
}
async function pvrAdd(){
  const x=pvrAdding;if(!x)return;const b=$('pvrgo');b.disabled=true;b.textContent='Adding…';
  const body={app:x.app,id:x.ext_id,profile:+$('pvrprof').value,root:$('pvrroot')?$('pvrroot').value:'',monitor:$('pvrmon')?$('pvrmon').value:'future',search:$('pvrsearch').checked};
  const all=pvrPrefs();all[x.app]={profile:body.profile,root:body.root,monitor:body.monitor,search:body.search};LS.set('nzb_mobile_pvr',JSON.stringify(all));
  try{const r=await api('/api/arr/add',body);r.title=r.title||x.title;closeSheet();x.in_library=true;
    toast(r.already?r.title+' is already in '+(x.app==='radarr'?'Radarr':'Sonarr')+'.':'Added '+r.title+(r.searching?'. Looking for it on your indexers now.':'.'));
    pvrUp=null;homeUpKey='';renderPvr();loadPvrUp(true)}
  catch(e){toast(e.message,true);b.disabled=false;b.textContent='Try again'}
}

// ---------- Pull down to refresh ----------
function refreshActive(){
  const f={home:()=>{drivesAt=0;usageAt=0;arrivedKey='';homeUpKey='';refreshHome()},queue:()=>refreshQueue(),history:()=>refreshHistory(),kodi:()=>refreshKodi(),
    jellyfin:()=>{jfMissing=jfUp=jfResults=null;refreshJellyfin()},drives:()=>refreshDrives(true),pvr:()=>{pvrResults=null;refreshPvr(true)}}[activeTab];
  if(f){f();return true}return false;
}
function setupPullToRefresh(){
  const ind=document.createElement('div');ind.className='ptr';ind.setAttribute('aria-hidden','true');ind.textContent='Pull to refresh';document.body.appendChild(ind);
  let y0=null,ready=false;
  window.addEventListener('touchstart',e=>{y0=(window.scrollY<=0&&!sheetOpen&&!$('app').classList.contains('hidden')&&e.touches.length===1)?e.touches[0].clientY:null;ready=false},{passive:true});
  window.addEventListener('touchmove',e=>{if(y0==null)return;const dy=e.touches[0].clientY-y0;if(dy<=12){ind.classList.remove('show');return}
    ready=dy>90;ind.textContent=ready?'Release to refresh':'Pull to refresh';ind.style.setProperty('--ptr',Math.min(dy/2,56)+'px');ind.classList.add('show')},{passive:true});
  window.addEventListener('touchend',()=>{if(y0==null)return;y0=null;ind.classList.remove('show');if(ready){ready=false;if(refreshActive())toast('Refreshing…')}},{passive:true});
}

async function kodiScan(){
  const b=$('kodiscan');b.disabled=true;
  try{const r=await api('/api/kodi/scan',{});kodiMovies=null;
    if(r.jellyfin)toast('Scanning Jellyfin and Kodi. New movies show up in Kodi in a few minutes, once Jellyfin has found them and the Jellyfin add-on has synced.'+(r.kodi===false?' (Kodi itself didn\'t answer.)':''));
    else if(r.jellyfin===false)toast('Kodi is scanning, but Jellyfin didn\'t answer ('+(r.jellyfin_problem||'offline')+'), so new movies may not appear yet.',true);
    else toast('Library scan started on Kodi')}
  catch(e){toast(e.message,true)}finally{b.disabled=false}
}

async function retryItem(id){try{await api('/api/history/retry',{id});toast('Retry requested');await refreshHistory()}catch(e){toast(e.message,true)}}
async function deleteHistory(id){if(!confirm('Remove this entry from the history? Downloaded files are kept.'))return;try{await api('/api/history/delete',{id});await refreshHistory()}catch(e){toast(e.message,true)}}

let seenHistory=null;
async function watchFinished(){
  if(document.hidden||$('app').classList.contains('hidden'))return;
  let d;try{d=await api('/api/history')}catch{return}
  const slots=d.slots||[];
  if(seenHistory===null){seenHistory=new Set(slots.map(x=>x.id));return}
  const fresh=slots.filter(x=>!seenHistory.has(x.id)&&/^(completed|failed)$/i.test(x.status||''));
  slots.forEach(x=>seenHistory.add(x.id));
  if(!fresh.length)return;
  const bad=fresh.filter(x=>/failed/i.test(x.status));
  const names=fresh.slice(0,3).map(x=>(/failed/i.test(x.status)?'Failed: ':'Finished: ')+x.name).join('\n')+(fresh.length>3?'\n…and '+(fresh.length-3)+' more':'');
  toast(names,bad.length>0);
  if(fresh.some(x=>/completed/i.test(x.status)))kodiMovies=null;
  if(activeTab==='history')refreshHistory();
}
function bind(){
  $('pairbtn').addEventListener('click',pair);
  $('drivecheck').addEventListener('click',()=>refreshDrives(true));
  $('homejf').addEventListener('click',e=>{const a=e.target.closest('[data-jact]');if(a)jfAction(a.dataset.sid,a.dataset.jact).then(()=>setTimeout(refreshHome,600))});
  $('homearrived').addEventListener('click',e=>{const b=e.target.closest('[data-hplay]');if(b)homePlay(b)});
  $('alertsbtn').addEventListener('click',openAlerts);
  setupPullToRefresh();
  $('kodysend').addEventListener('click',()=>kodiType(true));
  $('kodysendonly').addEventListener('click',()=>kodiType(false));
  $('kodytext').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();kodiType(true)}});
  $('jfscan').addEventListener('click',jfScan);
  $('pvr').addEventListener('click',e=>{
    const k=e.target.closest('[data-pvrkind]');if(k&&k.dataset.pvrkind!==pvrKind){pvrKind=k.dataset.pvrkind;pvrResults=null;return renderPvr()}
    const a=e.target.closest('[data-pvradd]');if(a)return openPvrAdd(a.dataset.pvradd);
  });
  $('pvr').addEventListener('keydown',e=>{if(e.target.id==='pvrq'&&e.key==='Enter'){e.preventDefault();pvrQuery=e.target.value.trim();pvrResults=null;e.target.blur();pvrList()}});
  $('jellyfin').addEventListener('click',e=>{
    const a=e.target.closest('[data-jact]');if(a)return jfAction(a.dataset.sid,a.dataset.jact);
    const m=e.target.closest('[data-jmode]');if(m){jfMode=jfMode===m.dataset.jmode?'':m.dataset.jmode;return renderJellyfin()}
    const pk=e.target.closest('[data-jpick]');if(pk){$('jfurl').value=pk.dataset.jpick;$('jfkey').focus();return}
    if(e.target.closest('#jffind'))return jfFind();
    if(e.target.closest('#jfsave'))return jfSave();
    if(e.target.closest('#jfforget'))return jfForget();
    if(e.target.closest('#jfchange')){jfConnectOpen=true;return renderJellyfin()}
  });
  $('jellyfin').addEventListener('keydown',e=>{if(e.target.id==='jfq'&&e.key==='Enter'){e.preventDefault();jfQuery=e.target.value.trim();jfMode=jfQuery?'search':'';jfResults=null;e.target.blur();renderJellyfin()}});
  document.addEventListener('click',e=>{
    const sh=e.target.closest('[data-jshow]');if(sh){if(e.target.closest('#sheet'))closeSheet();return openJfShow(sh.dataset.jshow)}
    const mv=e.target.closest('[data-jmovie]');if(mv&&!e.target.closest('#sheet'))return openJfMovie(mv.dataset.jmovie);
    const bt=e.target.closest('[data-jbetter]');if(bt){if(e.target.closest('#sheet'))closeSheet();goSearch(bt.dataset.jbetter,{category:bt.dataset.jcat!==undefined?bt.dataset.jcat:'2000'})}
  });
  $('drivebody').addEventListener('click',e=>{const c=e.target.closest('[data-ddrive]');if(c)openDrive(c.dataset.ddrive)});
  $('drivebody').addEventListener('change',e=>{const c=e.target.closest('[data-dalert]');if(c)setDriveAlert(c.dataset.dalert,c.checked)});
  $('token').addEventListener('keydown',e=>{if(e.key==='Enter')pair()});
  document.addEventListener('click',e=>{const b=e.target.closest('[data-tab]');if(b)showTab(b.dataset.tab)});
  document.addEventListener('click',e=>{const b=e.target.closest('[data-kact]');if(b)kodiControl(b.dataset.kact,b.dataset.pid)});
  $('homedl').addEventListener('click',async e=>{const b=e.target.closest('[data-hact]');if(!b)return;b.disabled=true;await queueAll(b.dataset.hact);refreshHome()});
  $('kodilist').addEventListener('click',e=>{const b=e.target.closest('[data-play]');if(b)playMovie(b.dataset.play,b.dataset.resume==='1')});
  document.addEventListener('click',e=>{const b=e.target.closest('[data-detail]');if(b&&!e.target.closest('#sheet'))openDetail(b.dataset.detail)});
  document.addEventListener('click',e=>{const b=e.target.closest('[data-show]');if(b&&!e.target.closest('#sheet'))openShow(b.dataset.show)});
  document.addEventListener('click',e=>{const b=e.target.closest('[data-ep]');if(!b)return;playEpisode(b.dataset.ep,b.dataset.resume==='1');if(e.target.closest('#sheet'))closeSheet()});
  document.addEventListener('click',e=>{const b=e.target.closest('[data-tvsearch]');if(!b)return;if(e.target.closest('#sheet'))closeSheet();goSearch(b.dataset.tvsearch,{category:'5000'})});
  $('sheet').addEventListener('click',e=>{
    if(e.target===$('sheet')||e.target.closest('[data-close]'))return closeSheet();
    if(e.target.closest('#pvrgo'))return pvrAdd();
    const v=e.target.closest('[data-view]');if(v&&v.dataset.view)return openViewer(v.dataset.view);
    const p=e.target.closest('[data-dplay]');if(p){playMovie(p.dataset.dplay,p.dataset.resume==='1');closeSheet();return}
    if(e.target.closest('[data-better]')&&detailMovie){
      const d=detailMovie;upgradeFrom=RES_RANK[d.quality]?{q:d.quality,hdr:d.hdr}:null;closeSheet();
      goSearch(d.title+(d.year?' '+d.year:''),{imdbid:d.imdb_id||'',category:'2000',keepUpgrade:true});return;
    }
    const qp=e.target.closest('[data-qprio]');if(qp)return queueItemChange('priority',qp.dataset.qprio);
    if(e.target.closest('[data-qcat]'))return queueItemChange('category',$('qcat').value);
    if(e.target.closest('[data-qtop]'))return queueItemChange('top');
  });
  $('kodiseg').addEventListener('click',e=>{const b=e.target.closest('[data-kmode]');if(b)setKodiMode(b.dataset.kmode)});
  let tvTimer=null;$('tvfilter').addEventListener('input',()=>{clearTimeout(tvTimer);tvTimer=setTimeout(()=>renderShows(true),200)});
  $('tvview').value=LS.get('nzb_mobile_tvview','all');if(!$('tvview').value)$('tvview').value='all';
  $('tvview').addEventListener('change',()=>{LS.set('nzb_mobile_tvview',$('tvview').value);window.scrollTo(0,0);renderShows(true)});
  $('tvmore').addEventListener('click',()=>{tvShown+=TV_PAGE;renderShows()});
  $('viewer').addEventListener('click',()=>$('viewer').classList.add('hidden'));
  window.addEventListener('popstate',()=>{if(!$('viewer').classList.contains('hidden'))$('viewer').classList.add('hidden');closeSheet(true)});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){if(!$('viewer').classList.contains('hidden'))$('viewer').classList.add('hidden');else closeSheet()}});
  let kodiTimer=null;$('kodifilter').addEventListener('input',()=>{clearTimeout(kodiTimer);kodiTimer=setTimeout(()=>renderKodiList(true),200)});
  $('kodiview').value=LS.get('nzb_mobile_kodiview','recent');if(!$('kodiview').value)$('kodiview').value='recent';
  $('kodiview').addEventListener('change',()=>{LS.set('nzb_mobile_kodiview',$('kodiview').value);window.scrollTo(0,0);renderKodiList(true)});
  $('kodimore').addEventListener('click',()=>{kodiShown+=KODI_PAGE;renderKodiList()});
  $('kodiscan').addEventListener('click',kodiScan);
  $('tvon').addEventListener('click',tvOn);$('tvkodi').addEventListener('click',tvKodi);$('tvsave').addEventListener('click',tvSave);
  $('searchbtn').addEventListener('click',search);
  $('query').addEventListener('keydown',e=>{if(e.key==='Enter')search()});
  ['sort','minsize','maxsize','mingrabs'].forEach(id=>$(id).addEventListener('input',()=>{savePrefs();renderResults()}));
  $('indexer').addEventListener('change',()=>fillSearchCategories());
  ['sabcat','prio','indexer','category'].forEach(id=>$(id).addEventListener('change',savePrefs));
  $('selvisible').addEventListener('click',()=>{filterSort(results,currentFilter()).forEach(x=>{if(!added.has(x.id)&&selected.size<10)selected.add(x.id)});renderResults()});
  $('selnone').addEventListener('click',()=>{selected.clear();renderResults()});
  $('batchbtn').addEventListener('click',addSelected);
  $('results').addEventListener('click',e=>{const card=e.target.closest('.result'),b=e.target.closest('[data-act="add"]');if(card&&b)addResult(card.dataset.id,b)});
  $('results').addEventListener('change',e=>{const card=e.target.closest('.result');if(!card||e.target.dataset.act!=='sel')return;const id=card.dataset.id;if(e.target.checked){if(selected.size>=10){e.target.checked=false;toast('Select no more than 10 NZBs at a time.',true);return}selected.add(id)}else selected.delete(id);updateBatchButton()});
  $('queueitems').addEventListener('click',e=>{const row=e.target.closest('.queueitem'),b=e.target.closest('[data-act]');if(row&&b)itemAction(row.dataset.id,b.dataset.act)});
  $('historyitems').addEventListener('click',e=>{const row=e.target.closest('.queueitem'),b=e.target.closest('[data-act]');if(!row||!b)return;if(b.dataset.act==='retry')retryItem(row.dataset.id);else if(b.dataset.act==='hdel')deleteHistory(row.dataset.id)});
  $('qresume').addEventListener('click',()=>queueAll('resume'));
  $('qpause').addEventListener('click',()=>queueAll('pause'));
  $('speedset').addEventListener('click',setSpeed);
  $('histrefresh').addEventListener('click',refreshHistory);
  setInterval(()=>{if(!document.hidden&&activeTab==='queue'&&!$('app').classList.contains('hidden'))refreshQueue(true)},4000);
  setInterval(pollStatus,10000);
  setInterval(watchFinished,15000);
  $('recent').addEventListener('click',e=>{const b=e.target.closest('[data-recent]');if(b)useRecent(b.dataset.recent)});
  renderRecent();
  setInterval(()=>{if(document.hidden||$('app').classList.contains('hidden'))return;if(activeTab==='home')refreshHome();else if(activeTab==='kodi')refreshKodiNow();else if(activeTab==='jellyfin')refreshJfNow();else if(activeTab==='drives')refreshDriveIO()},5000);
}
bind();
if(token)connect();
</script></body></html>
"""


# ==============================================================================
# 8. HTTP HANDLER
# ==============================================================================

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' blob:; manifest-src 'self'; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
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
        if not path.startswith("/api/"):
            self.send_json(404, {"error": "Not found."})
            return
        if not self._auth_ok():
            return
        self._guarded(lambda: self._route_get(path))

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
        elif path == "/api/drives":
            self.send_json(200, self._drives_view())
        elif path == "/api/drives/io":
            self.send_json(200, {"io": drive_activity()})
        elif path.startswith("/api/arr"):
            self._get_arr(path)
        elif path.startswith("/api/jellyfin"):
            self._get_jellyfin(path)
        elif path == "/api/saved":
            self.send_json(200, self._saved_view())
        elif path == "/api/settings":
            self.send_json(200, self._settings_view())
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
        elif path == "/api/kodi/play":
            movieid, episodeid = to_int(body.get("movieid")), to_int(body.get("episodeid"))
            if (movieid > 0) == (episodeid > 0):
                raise ValueError("Invalid movie or episode.")
            item = {"movieid": movieid} if movieid > 0 else {"episodeid": episodeid}
            kodi_call("Player.Open", {"item": item, "options": {"resume": bool(body.get("resume"))}})
            log.info("kodi play %s resume=%s ip=%s", item, bool(body.get("resume")), self.ip)
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
            return {"configured": True, "online": True, "playing": kodi_now_playing()}
        except UpstreamError as exc:
            return {"configured": True, "online": False, "problem": str(exc)}  # not "error": see api() in the page

    def _kodi_overview(self) -> dict:
        info = self._kodi_brief()
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
