#!/usr/bin/env python3
"""
NZB Command Center - Phone App (v3)

A phone version of NZB Command Center, served on your home network:
  * Home   - current download, pause/resume, today's done/failed, free space,
             data downloaded today / this week / this month, Kodi now playing
  * Search - your Newznab indexers, with a category drop-down per indexer; send to SABnzbd
  * Queue  - SABnzbd queue and history (pause, delete, retry, speed limit)
  * Kodi   - now playing with a full remote, movies and TV shows (continue watching,
             next episode, missing episodes), "find a better copy" for a movie,
             and "TV on + Kodi" / "Open Kodi" buttons for a Sony TV (IP control)
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

VERSION = "3.5.1"
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

APP_ICON_PNG_B64 = (
    "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"
)
ICON_PNG = base64.b64decode("".join(APP_ICON_PNG_B64))


# ==============================================================================
# 1. ERRORS, REDACTION, SMALL HELPERS
# ==============================================================================

class UpstreamError(RuntimeError):
    """Failure talking to an indexer or SABnzbd. The message is already safe to show to the phone."""


class ConfigError(RuntimeError):
    """Problem with the desktop configuration. The message contains no secrets."""


_KNOWN_SECRETS: frozenset[str] = frozenset()
_EXTRA_SECRETS: frozenset[str] = frozenset()  # phone-only secrets (the ntfy access token)
_SECRET_QS = re.compile(r"(?i)\b(apikey|api_key|passkey|token|r)=([^&\s'\"<>]+)")


def redact(text) -> str:
    """Strips API keys from any text (query-string style, plus every key currently configured)."""
    out = _SECRET_QS.sub(lambda m: f"{m.group(1)}=***", str(text))
    for secret in _KNOWN_SECRETS | _EXTRA_SECRETS:  # frozensets that are only ever swapped whole, never mutated
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
        if new.hostname.lower() != (old.hostname or "").lower() and not host_is_public(new.hostname):
            raise UpstreamError("Refused a redirect to a private/local address.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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
    sab_key = _dpapi_unprotect(str(data.get("sab_api_key") or "")).strip()

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

    if not sab_key:
        raise ConfigError("SABnzbd API key is missing (or could not be decrypted) in nzb_manager_config.json.")

    kodi_pw = ""
    try:
        kodi_pw = _dpapi_unprotect(str(data.get("kodi_password") or ""))
    except ConfigError:
        log.warning("Kodi password could not be decrypted; Kodi requests may be refused.")
    kodi = {"url": normalize_kodi_url(str(data.get("kodi_url") or "")),
            "user": str(data.get("kodi_username") or ""), "password": kodi_pw}

    global _KNOWN_SECRETS
    # Swap in a complete new set at once, so a concurrent redact() never sees an empty/partial set.
    _KNOWN_SECRETS = frozenset([sab_key] + [ix["api_key"] for ix in indexers if ix["api_key"]]
                               + ([kodi_pw] if kodi_pw else []))
    return {"sab_url": sab_url, "sab_api_key": sab_key, "indexers": indexers, "kodi": kodi}


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
    params = {"mode": mode, "output": "json", "apikey": cfg["sab_api_key"]}
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

    params = {"mode": "addfile", "output": "json", "apikey": cfg["sab_api_key"], "nzbname": safe_name[:-4]}
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
    out = []
    for raw in res.get("tvshows") or []:
        show = _kodi_show(_kdict(raw))
        if not show["lastplayed"] or show["watched"] >= show["episodes"]:
            continue
        nxt = next_episode(kodi_show_episodes(show["id"]))
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
    download_url = urllib.parse.urljoin(indexer["url"].rstrip("/") + "/", nzb_url)
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
            # Sony TV power / app launch (TV on + Kodi). Blank tv_host = the host from the Kodi URL.
            "tv_host": "", "tv_psk": "", "tv_mac": "", "tv_mac_manual": False, "tv_kodi_uri": ""}


def load_settings() -> dict:
    """Phone-only settings (never the desktop configuration). Missing/damaged file -> defaults."""
    settings = _default_settings()
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return settings
    except (OSError, ValueError) as exc:
        log.warning("%s could not be read (%s); using defaults.", SETTINGS_FILE.name, exc)
        return settings
    if isinstance(data, dict):
        for key, default in _default_settings().items():
            if isinstance(data.get(key), type(default)):
                settings[key] = data[key]
    settings["saved_searches"] = [x for x in settings["saved_searches"] if isinstance(x, dict) and x.get("id")]
    return settings


def save_settings(settings: dict) -> None:
    tmp = SETTINGS_FILE.with_name(SETTINGS_FILE.name + ".tmp")
    tmp.write_text(json.dumps(settings, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, SETTINGS_FILE)


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
_tv_lock = threading.Lock()
_tv_refreshing = threading.Lock()


def tv_load_state() -> dict:
    try:
        data = json.loads(TV_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    return {"updated": to_float(data.get("updated")), "kodi": [x for x in data.get("kodi") or [] if isinstance(x, dict)],
            "items": [x for x in data.get("items") or [] if isinstance(x, dict)],
            "path_map": [p for p in data.get("path_map") or [] if isinstance(p, list) and len(p) == 2]}


def tv_save_state(state: dict) -> None:
    tmp = TV_STATE_FILE.with_name(TV_STATE_FILE.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, TV_STATE_FILE)


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
    os.replace(tmp, dest)
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
        with _tv_lock:
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
    with _tv_lock:   # show it finished right away in the saved list too
        state = tv_watched_load()
        for it in state["items"]:
            if it.get("id") == item_id:
                it.update(progress=1.0, remaining=0, watched=True)
        tmp = TV_WATCHED_FILE.with_name(TV_WATCHED_FILE.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, TV_WATCHED_FILE)
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
    os.replace(tmp, dest)
    return True


def tv_watched_load() -> dict:
    try:
        data = json.loads(TV_WATCHED_FILE.read_text(encoding="utf-8"))
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
        with _tv_lock:
            tmp = TV_WATCHED_FILE.with_name(TV_WATCHED_FILE.name + ".tmp")
            tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, TV_WATCHED_FILE)
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
            self.check_history(settings)
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
        due = due_saved_search(settings, now)
        if due:  # one per tick spreads indexer API use out
            run_saved_search(due["id"], notify=True)

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


AUTOSTART_NAME = "NZB Mobile Server.vbs"


def autostart_path() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if os.name != "nt" or not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / AUTOSTART_NAME


def autostart_enabled() -> bool | None:
    path = autostart_path()
    return None if path is None else path.exists()


def install_autostart(extra_args: list[str]) -> Path:
    """Adds a hidden launcher to the user's Startup folder (no admin rights or system changes needed)."""
    path = autostart_path()
    if path is None:
        raise ConfigError("Starting automatically is only available on Windows.")
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
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="theme-color" content="#060D10">
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
.tab{flex:1;background:transparent;border:0;color:var(--muted);display:flex;flex-direction:column;align-items:center;gap:3px;padding:6px 0;font-size:11px;font-weight:600;position:relative;min-height:52px}
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
.pills{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 4px}.pills button{border:1px solid var(--line);background:transparent;color:var(--text);border-radius:18px;padding:0 14px;min-height:38px;font-size:13px;font-weight:600}.pills button.on{background:var(--teal);color:var(--ink);border-color:var(--teal)}
</style></head><body>
<header><div class="brand"><img src="/icon.png" alt="">NZB Command Center</div><div id="statusbar" class="statusbar hidden"><span id="sabpill" class="spill"><i></i>SABnzbd</span></div></header>
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
</section>
<section id="search" class="hidden">
<h1>Search</h1><p class="lede">Your indexers, from anywhere at home.</p>
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

const API_TIMEOUTS={'/api/search':90000,'/api/add':120000,'/api/add-batch':300000};
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
function showPair(msg){$('app').classList.add('hidden');$('tabbar').classList.add('hidden');$('batchbar').classList.add('hidden');$('pair').classList.remove('hidden');$('token').value='';if(msg)$('pairmsg').textContent=msg}
function pair(){const v=$('token').value.trim();if(!v)return;token=v;LS.set('nzb_mobile_token',token);connect()}

async function connect(){
  try{
    const d=await api('/api/status');
    $('pair').classList.add('hidden');$('app').classList.remove('hidden');$('tabbar').classList.remove('hidden');
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

function showTab(t){activeTab=t;for(const n of ['home','search','queue','history','kodi'])$(n).classList.toggle('hidden',n!==t);const tab=t==='history'?'queue':t;document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));updateBatchButton();window.scrollTo(0,0);if(t==='home')refreshHome();if(t==='queue')refreshQueue();if(t==='history')refreshHistory();if(t==='kodi')refreshKodi()}

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
  return '<div class="hero queueitem" data-id="'+esc(x.id)+'">'+stateTag(x.status)+'<div class="name">'+esc(x.name)+'</div><div class="sub">'+esc(x.sizeleft)+' of '+esc(x.size)+' left · ETA '+esc(x.eta||'—')+(x.cat&&x.cat!=='*'?' · '+esc(x.cat):'')+'</div><div class="bar"><span style="width:'+p+'%"></span></div><div class="row"><span style="flex:1;font:600 14px var(--mono);color:var(--teal)">'+p.toFixed(0)+'%</span><button class="icon-btn" data-act="more" aria-label="Category and priority">'+ICON.more+'</button><button class="btn ghost sm" style="flex:none" data-act="'+(paused?'resume':'pause')+'">'+(paused?'Resume':'Pause')+'</button><button class="btn danger sm" style="flex:none" data-act="delete">Delete</button></div></div>';
}
function queueRow(x){
  const p=clampPct(x.percentage),paused=String(x.status).toLowerCase()==='paused';
  return '<div class="qrow queueitem" data-id="'+esc(x.id)+'"><div class="info"><div class="t">'+esc(x.name)+'</div><div class="m">'+esc(String(x.status||'').toUpperCase())+' · '+esc(x.size)+(x.eta?' · '+esc(x.eta):'')+(x.cat&&x.cat!=='*'?' · '+esc(x.cat):'')+'</div><div class="bar thin"><span style="width:'+p+'%"></span></div></div><button class="icon-btn" data-act="more" aria-label="More options: move to top, category, priority">'+ICON.more+'</button><button class="icon-btn" data-act="'+(paused?'resume':'pause')+'" aria-label="'+(paused?'Resume':'Pause')+'">'+(paused?ICON.play:ICON.pause)+'</button><button class="icon-btn red" data-act="delete" aria-label="Delete">'+ICON.trash+'</button></div>';
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
function homeDownload(d){
  const c=d.current;
  const btn=d.paused?'<button class="btn sm" data-hact="resume">Resume all</button>':'<button class="btn ghost sm" data-hact="pause">Pause all</button>';
  const qbtn='<button class="btn ghost sm" data-tab="queue">Queue'+(d.total?' ('+esc(d.total)+')':'')+'</button>';
  if(!c)return '<div class="hero"><div class="kode">DOWNLOADS</div><div class="name">Nothing downloading</div><div class="sub">Search for something and send it to SABnzbd.</div><div class="row" style="margin-top:12px">'+(d.paused?btn:'')+qbtn+'</div></div>';
  const low=String(d.state||'').toLowerCase(),tag=low==='paused'?'warn':(low==='downloading'?'ok':'busy');
  return '<div class="hero"><div class="row" style="justify-content:space-between"><span class="tag '+tag+'" style="flex:none">'+esc(String(d.state||'').toUpperCase())+'</span><span style="flex:none;font:700 18px var(--mono);color:var(--teal)">'+esc(d.speed||'')+'</span></div>'+
    '<div class="name">'+esc(c.name)+'</div><div class="bar"><span style="width:'+clampPct(c.pct)+'%"></span></div>'+
    '<div class="sub">'+esc(Math.round(c.pct||0))+'% · '+esc(c.sizeleft||'?')+' left of '+esc(c.size||'?')+(c.eta?' · '+esc(c.eta):'')+'</div><div class="row" style="margin-top:12px">'+btn+qbtn+'</div></div>';
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
    loadHomeUsage();
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
    const src=j.ref.startsWith('r:')?'/api/search/art?id='+encodeURIComponent(j.ref.slice(2)):'/api/kodi/art?u='+encodeURIComponent(j.ref);
    fetch(src,{headers:{'X-Mobile-Token':token}})
      .then(r=>{if(r.status===404)return null;if(!r.ok)throw new Error('HTTP '+r.status);return r.blob()})
      .then(b=>{if(!b){j.res(null);return}const u=URL.createObjectURL(b);artReady.set(j.ref,u);j.res(u)})  // null = no poster: keep the placeholder, don't ask again
      .catch(e=>{artCache.delete(j.ref);j.rej(e)}).finally(()=>{artActive--;pumpArt()});
  }
}
const artReady=new Map();
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
  if(tvInfo.error){tvSay(tvInfo.error,true);return}
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
    if(tvInfo.configured&&!tvInfo.error){toast('TV connected');$('tvsetup').open=false}
    else if(tvInfo.error)toast(tvInfo.error,true);
  }catch(e){toast(e.message,true)}finally{b.disabled=false}
}
async function refreshKodi(){
  const st=$('kodistatus');loadTv();
  try{
    const d=await api('/api/kodi');
    const off=()=>{$('kodinow').innerHTML='';['kodilib','kodiTV','kodiseg','kodiremote'].forEach(id=>$(id).classList.add('hidden'));kodiOnline=false};
    if(!d.configured){st.className='lede';st.textContent='Kodi isn\'t set up yet — add it in the desktop app\'s Kodi tab.';off();$('kodiscan').classList.add('hidden');return}
    $('kodiscan').classList.remove('hidden');
    if(!d.online){st.className='lede status error';st.textContent=d.error||'Kodi is offline.';off();return}
    const c=d.counts||{};st.className='lede';st.textContent=(c.movies||0)+' movies · '+(c.shows||0)+' shows · '+(c.episodes||0)+' episodes';
    kodiPid=d.playing?d.playing.playerid:-1;
    $('kodinow').innerHTML=nowCard(d.playing,false);hydrateArt($('kodinow'));
    kodiOnline=true;$('kodiremote').classList.remove('hidden');$('kodiseg').classList.remove('hidden');
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
async function kodiScan(){
  const b=$('kodiscan');b.disabled=true;
  try{await api('/api/kodi/scan',{});kodiMovies=null;toast('Library scan started on Kodi')}catch(e){toast(e.message,true)}finally{b.disabled=false}
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
  setInterval(()=>{if(document.hidden||$('app').classList.contains('hidden'))return;if(activeTab==='home')refreshHome();else if(activeTab==='kodi')refreshKodiNow()},5000);
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
MANIFEST = json.dumps({
    "name": "NZB Command Center", "short_name": "NZB Center", "start_url": "/", "scope": "/",
    "display": "standalone", "background_color": "#060D10", "theme_color": "#060D10",
    "icons": [{"src": "/icon.png", "sizes": "180x180", "type": "image/png", "purpose": "any"}],
}).encode("utf-8")


class MobileHandler(BaseHTTPRequestHandler):
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
        if path in ("/", "/index.html"):
            self._send(200, PAGE.replace("__VERSION__", VERSION).encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/manifest.webmanifest":
            self._send(200, MANIFEST, "application/manifest+json", {"Cache-Control": "public, max-age=86400"})
            return
        if path in ("/icon.png", "/favicon.ico", "/apple-touch-icon.png"):
            self._send(200, ICON_PNG, "image/png", {"Cache-Control": "public, max-age=86400"})
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
        if not self._network_ok() or not self._auth_ok():
            return
        self._guarded(lambda: self._route_post(path))

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
        elif path == "/api/tv/mark_watched":
            self.send_json(200, tv_mark_watched(str(body.get("id") or "")))
        elif path == "/api/kodi/control":
            kodi_control(str(body.get("action") or ""), to_int(body.get("playerid"), -1), body.get("value"))
            self.send_json(200, {"ok": True})
        elif path == "/api/saved":
            self._post_saved(body)
        elif path == "/api/settings":
            self._post_settings(body)
        elif path == "/api/settings/test":
            if not send_notification("NZB Command Center", "Test alert from your phone app — alerts are working.", tags=("bell",)):
                raise ValueError("Save an ntfy topic URL first.")
            self.send_json(200, {"ok": True})
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
            kodi_call("VideoLibrary.Scan", {"showdialogs": False})
            log.info("kodi scan ip=%s", self.ip)
            self.send_json(200, {"ok": True})
        elif path == "/api/speedlimit":
            mbps = to_float(body.get("mbps"), -1.0)
            if not 0 <= mbps <= 10000:
                raise ValueError("Speed limit must be between 0 and 10000 MB/s.")
            sab_request("config", {"name": "speedlimit", "value": "0" if mbps == 0 else f"{int(round(mbps * 1024))}K"})
            log.info("speed limit %s MB/s ip=%s", mbps, self.ip)
            self.send_json(200, {"ok": True})
        else:
            self.send_json(404, {"error": "Not found."})

    def _home(self) -> dict:
        """One call for the Home tab: SABnzbd summary, today's totals and what Kodi is playing."""
        out: dict = {"sab_ok": True}
        # Kodi is asked in parallel with SABnzbd, so a slow or switched-off Kodi doesn't add
        # its timeout on top of SABnzbd's.
        with ThreadPoolExecutor(max_workers=1) as pool:
            kodi_future = pool.submit(self._kodi_brief)
            self._home_sab(out)
            try:
                out["kodi"] = kodi_future.result()
            except ConfigError:
                out["kodi"] = {"configured": False}
        out["saved_new"] = sum(len(x.get("new") or []) for x in load_settings()["saved_searches"])
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
            return {"configured": True, "online": False, "error": str(exc)}

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
            info.update(online=False, error=str(exc))
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
                view["power"], view["error"] = "unknown", str(exc)
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
    return out


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
        else:
            path = install_autostart(_autostart_args(args))
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
    print(f" This PC         : http://127.0.0.1:{args.port}")
    print(f" Phone (Wi-Fi)   : http://{local_ip}:{args.port}")
    print()
    print(" MOBILE ACCESS CODE (stable across restarts):")
    print(f" {token}")
    print()
    print(" Pairing link - open this on the phone once and it connects itself:")
    print(f" http://{local_ip}:{args.port}/#k={token}")
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
