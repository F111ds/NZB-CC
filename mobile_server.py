#!/usr/bin/env python3
"""
NZB Command Center - Mobile Companion (v2)

A small LAN web page for searching your Newznab indexers and controlling SABnzbd
from a phone. It reads the SAME nzb_manager_config.json (and DPAPI-protected keys)
as the desktop app and never modifies it.

Place this file in the SAME folder as nzb_manager_config.json.
Standard library only (Python 3.9+).

    python mobile_server.py                 # LAN only (private networks)
    python mobile_server.py --port 9000
    python mobile_server.py --allow-cidr 203.0.113.0/24
    python mobile_server.py --allow-any-client   # NOT recommended

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
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "2.0"
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


# ==============================================================================
# 1. ERRORS, REDACTION, SMALL HELPERS
# ==============================================================================

class UpstreamError(RuntimeError):
    """Failure talking to an indexer or SABnzbd. The message is already safe to show to the phone."""


class ConfigError(RuntimeError):
    """Problem with the desktop configuration. The message contains no secrets."""


_KNOWN_SECRETS: frozenset[str] = frozenset()
_SECRET_QS = re.compile(r"(?i)\b(apikey|api_key|passkey|token|r)=([^&\s'\"<>]+)")


def redact(text) -> str:
    """Strips API keys from any text (query-string style, plus every key currently configured)."""
    out = _SECRET_QS.sub(lambda m: f"{m.group(1)}=***", str(text))
    for secret in _KNOWN_SECRETS:  # a frozenset that is only ever swapped whole, never mutated
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

    global _KNOWN_SECRETS
    # Swap in a complete new set at once, so a concurrent redact() never sees an empty/partial set.
    _KNOWN_SECRETS = frozenset([sab_key] + [ix["api_key"] for ix in indexers if ix["api_key"]])
    return {"sab_url": sab_url, "sab_api_key": sab_key, "indexers": indexers}


_cfg_lock = threading.Lock()
_cfg_state: dict = {"mtime": None, "data": None}


def get_config() -> dict:
    """Returns the current desktop configuration, re-reading it whenever the file changes."""
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
        try:
            data = load_config()
        except ConfigError as exc:
            if _cfg_state["data"] is not None:  # e.g. caught the desktop app mid-edit; keep working
                log.warning("Configuration reload failed (%s); keeping the previous settings.", redact(exc))
                _cfg_state["mtime"] = mtime
                return _cfg_state["data"]
            raise
        first = _cfg_state["data"] is None
        _cfg_state.update(mtime=mtime, data=data)
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


def parse_add_options(body: dict) -> tuple[str, str]:
    """Validates the optional SABnzbd category / priority sent with an add request."""
    category = str(body.get("category") or "").strip()
    if category and category != "*":
        if not re.fullmatch(r"[\w .\-]{1,60}", category):
            raise ValueError("Invalid SABnzbd category.")
        if category not in sab_categories():
            raise ValueError(f"Unknown SABnzbd category '{category}'.")
    else:
        category = ""
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
# 4. NEWZNAB SEARCH
# ==============================================================================

def _item_from_fields(indexer: dict, title, category, pub_date, nzb_url, size, grabs) -> dict | None:
    nzb_url = str(nzb_url or "").strip()
    if not nzb_url:
        return None
    return {
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
        grabs = 0
        for child in item:
            if child.tag.split("}")[-1].lower() == "attr":
                name = child.attrib.get("name", "").lower()
                val = child.attrib.get("value", "")
                if name == "size" and not size:
                    size = to_int(val)
                elif name == "grabs":
                    grabs = to_int(val)
        parsed = _item_from_fields(
            indexer, item.findtext("title"), item.findtext("category"), item.findtext("pubDate"),
            (enclosure.attrib.get("url") if enclosure is not None else "") or link, size, grabs,
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
        )
        if parsed:
            results.append(parsed)
    return results


def search_one(indexer: dict, query: str, category: str, limit: int) -> list[dict]:
    if not indexer["api_key"]:
        raise UpstreamError(indexer.get("key_error") or "API key is blank in the desktop configuration.")
    params: dict[str, object] = {"t": "search", "apikey": indexer["api_key"], "limit": limit}
    if query:
        params["q"] = query
    if category:
        params["cat"] = category
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


def merge_duplicate_results(items: list[dict]) -> list[dict]:
    """Groups identical titles across indexers, keeping every source so a failed download can fall back."""
    merged: dict[str, dict] = {}
    for item in items:
        key = re.sub(r"[^a-z0-9]+", "", item["title"].lower()) or secrets.token_hex(8)
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
            })
    return out


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

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if len(self._events) > 1000:
                self._events = {k: q for k, q in self._events.items() if q and now - q[-1] <= self.window}
            q = self._events.setdefault(key, deque())
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.max_events:
                return False
            q.append(now)
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
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="theme-color" content="#061014">
<title>NZB Command Center</title>
<style>
:root{color-scheme:dark;--bg:#061014;--panel:#0d1d24;--card:#132831;--line:#21404b;--teal:#2dd4bf;--muted:#91aeb7;--white:#effdfa;--red:#f87171;--amber:#fbbf24;--green:#4ade80}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(160deg,#07151a,#050b0e 65%);color:var(--white);font:15px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;-webkit-text-size-adjust:100%}
header{padding:calc(18px + env(safe-area-inset-top)) 18px 13px;background:#09171d;border-bottom:1px solid var(--line);position:sticky;top:0;z-index:3}
h1{font-size:21px;margin:0 0 5px;letter-spacing:.2px}.sub{font-size:12px;color:var(--muted)}
main{max-width:760px;margin:auto;padding:15px 13px calc(42px + env(safe-area-inset-bottom))}.tabs{display:flex;gap:8px;margin-bottom:14px}.tab{flex:1;background:var(--panel);border:1px solid var(--line);color:var(--muted);padding:12px 6px;border-radius:11px;font-weight:700}.tab.active{color:#06201d;background:var(--teal);border-color:var(--teal)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:15px;padding:14px;margin-bottom:13px}
label{display:block;color:var(--muted);font-size:12px;margin:0 0 6px}input,select{width:100%;background:#071318;border:1px solid var(--line);border-radius:10px;padding:13px;color:var(--white);font-size:16px;margin-bottom:11px}
button{border:0;border-radius:10px;background:var(--teal);color:#04221e;font-weight:800;padding:12px 15px;font-size:14px}button.secondary{background:#1b3540;color:var(--white);border:1px solid var(--line)}button.danger{background:#4b2026;color:#fecaca}
button:disabled{opacity:.45}.full{width:100%}.row{display:flex;gap:8px;align-items:center}.row>*{flex:1}.muted{color:var(--muted);font-size:12px}.status{font-size:13px;color:var(--teal);margin:8px 0;white-space:pre-wrap}.error{color:var(--red)}
.result{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:13px;margin:10px 0}.result h3{font-size:15px;line-height:1.35;margin:0 0 9px;overflow-wrap:anywhere}.meta{display:flex;flex-wrap:wrap;gap:7px;color:var(--muted);font-size:12px;margin-bottom:11px}.pill{background:#203a43;border-radius:20px;padding:4px 8px}.result>button{width:100%}.sel{display:flex;align-items:center;margin-bottom:8px}.sel input{width:auto;margin:0 7px 0 0}
.queueitem{padding:12px 0;border-bottom:1px solid var(--line)}.queueitem:last-child{border:0}.qtitle{font-weight:700;overflow-wrap:anywhere;margin-bottom:5px}.bar{height:6px;background:#233b43;border-radius:6px;margin:8px 0}.bar span{display:block;height:6px;background:var(--teal);border-radius:6px}
.hidden{display:none}.hint{font-size:12px;color:var(--muted);line-height:1.5}.smallbtn{padding:8px 10px;font-size:12px}.ok{color:var(--green)}.bad{color:var(--red)}.warn{color:var(--amber)}
.toast{position:fixed;left:12px;right:12px;bottom:calc(14px + env(safe-area-inset-bottom));background:#10303a;border:1px solid var(--teal);border-radius:12px;padding:13px 15px;z-index:9;white-space:pre-wrap;font-size:14px;max-width:736px;margin:auto}.toast.err{border-color:var(--red);background:#2b1519}
</style></head><body>
<header><h1>NZB Command Center</h1><div class="sub">Mobile control • Home network • v__VERSION__</div></header>
<main>
<div id="pair" class="panel"><label>Mobile access code</label><input id="token" type="password" placeholder="Enter code shown on your PC" autocomplete="off" autocapitalize="off" autocorrect="off"><button class="full" id="pairbtn">Connect</button><div id="pairmsg" class="hint" style="margin-top:9px">The access code is shown in the PowerShell window running the server. Tip: open the “Pairing link” it prints and this step is automatic.</div></div>
<div id="app" class="hidden">
<div class="tabs"><button id="tabsearch" class="tab active" data-tab="search">Search</button><button id="tabqueue" class="tab" data-tab="queue">Queue</button><button id="tabhistory" class="tab" data-tab="history">History</button></div>
<section id="search">
<div class="panel">
<label>Search indexer</label><select id="indexer"><option value="*">All enabled indexers</option></select>
<label>Search title / keyword (blank = latest releases)</label><input id="query" placeholder="Movie, series, release..." enterkeyhint="search" autocomplete="off">
<label>Newznab category ID (optional)</label><input id="category" placeholder="e.g. 2000 for Movies, 5000 for TV" inputmode="numeric">
<button class="full" id="searchbtn">Search indexers</button><div id="searchstatus" class="status"></div>
</div>
<div class="panel" id="resulttools" style="display:none">
<label>Sort results</label><select id="sort"><option value="date_desc">Newest first</option><option value="size_desc">Largest first</option><option value="size_asc">Smallest first</option><option value="date_asc">Oldest first</option><option value="grabs_desc">Most grabs</option><option value="title_asc">Title A–Z</option><option value="title_desc">Title Z–A</option></select>
<div class="row"><div><label>Minimum size (MB)</label><input id="minsize" type="number" min="0" placeholder="No minimum"></div><div><label>Maximum size (MB)</label><input id="maxsize" type="number" min="0" placeholder="No maximum"></div></div>
<label>Minimum grabs</label><input id="mingrabs" type="number" min="0" placeholder="No minimum">
<div class="row"><div><label>SABnzbd category</label><select id="sabcat"><option value="">Default</option></select></div><div><label>Priority</label><select id="prio"><option value="">Default</option><option value="-1">Low</option><option value="0">Normal</option><option value="1">High</option><option value="2">Force</option></select></div></div>
<div class="row"><button class="secondary" id="selvisible">Select visible</button><button class="secondary" id="selnone">Clear selection</button></div>
<button id="batchbtn" class="full" style="margin-top:9px">Add selected to SABnzbd (0)</button>
<div class="hint" style="margin-top:7px">Batch limit: 10 NZBs per request. Duplicate titles found on several indexers are grouped, and a failed download falls back to the other indexer automatically.</div>
</div><div id="results"></div>
</section>
<section id="queue" class="hidden">
<div class="panel"><div class="row"><button id="qresume" class="full">Resume all</button><button id="qpause" class="secondary full">Pause all</button></div>
<div class="row" style="margin-top:9px"><input id="speedmb" type="number" min="0" step="0.5" placeholder="Limit MB/s (0 = none)" style="margin:0"><button id="speedset" class="secondary">Set limit</button></div>
<div id="queuestatus" class="status"></div></div><div id="queueitems" class="panel"></div>
</section>
<section id="history" class="hidden">
<div class="panel"><button class="secondary full" id="histrefresh">Refresh history</button><div id="histstatus" class="status"></div></div><div id="historyitems" class="panel"></div>
</section>
</div></main>
<div id="toast" class="toast hidden" role="status"></div>
<script>
'use strict';
const LS={get:(k,d='')=>{try{const v=localStorage.getItem(k);return v===null?d:v}catch{return d}},set:(k,v)=>{try{localStorage.setItem(k,v)}catch{}}};
const $=id=>document.getElementById(id);
let token=LS.get('nzb_mobile_token');
let results=[],selected=new Set(),added=new Set(),activeTab='search',queueBusy=false,toastTimer=null;

(function pairFromLink(){const m=(location.hash||'').match(/^#k=([A-Za-z0-9_-]+)$/);if(m){token=m[1];LS.set('nzb_mobile_token',token);try{history.replaceState(null,'',location.pathname)}catch{}}})();

function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function fmtBytes(n){n=Number(n)||0;const u=['B','KB','MB','GB','TB'];let i=0;while(n>=1024&&i<u.length-1){n/=1024;i++}return (i?n.toFixed(1):String(Math.round(n)))+' '+u[i]}
function clampPct(v){return Math.max(0,Math.min(100,Number(v)||0))}
function toast(msg,isErr){const t=$('toast');t.textContent=msg;t.className='toast'+(isErr?' err':'');clearTimeout(toastTimer);toastTimer=setTimeout(()=>{t.className='toast hidden'},isErr?6000:3500)}

const API_TIMEOUTS={'/api/search':90000,'/api/add':120000,'/api/add-batch':300000};
async function api(path,body){
  const opt={method:body===undefined?'GET':'POST',headers:{'X-Mobile-Token':token}};
  if(body!==undefined){opt.headers['Content-Type']='application/json';opt.body=JSON.stringify(body)}
  // Without a timeout a dropped Wi-Fi connection left buttons stuck on "Searching…"/"Adding…" forever.
  const ctl=new AbortController(),timer=setTimeout(()=>ctl.abort(),API_TIMEOUTS[path]||30000);opt.signal=ctl.signal;
  let r;try{r=await fetch(path,opt)}catch(e){clearTimeout(timer);throw new Error(e&&e.name==='AbortError'?'The server took too long to answer. Check the queue before retrying an add.':'Cannot reach the server. Is the PC on and the mobile server running?')}
  clearTimeout(timer);
  let d={};try{d=await r.json()}catch{}
  if(r.status===401){showPair(d.error||'The access code was not accepted.');throw new Error(d.error||'Unauthorized')}
  if(!r.ok||d.error)throw new Error(d.error||('HTTP '+r.status));
  return d;
}
function showPair(msg){$('app').classList.add('hidden');$('pair').classList.remove('hidden');$('token').value='';if(msg)$('pairmsg').textContent=msg}
function pair(){const v=$('token').value.trim();if(!v)return;token=v;LS.set('nzb_mobile_token',token);connect()}

async function connect(){
  try{
    const d=await api('/api/status');
    $('pair').classList.add('hidden');$('app').classList.remove('hidden');
    const sel=$('indexer');sel.innerHTML='<option value="*">All enabled indexers</option>'+d.indexers.map(x=>'<option value="'+esc(x.id)+'">'+esc(x.name)+'</option>').join('');
    loadPrefs();
    api('/api/sab/info').then(i=>fillCategories(i.categories||[])).catch(()=>{});
    showTab(activeTab);
  }catch(e){if($('app').classList.contains('hidden'))$('pairmsg').textContent=e.message}
}
function fillCategories(cats){const sel=$('sabcat'),keep=LS.get('nzb_mobile_cat');sel.innerHTML='<option value="">Default</option>'+cats.map(c=>'<option value="'+esc(c)+'">'+esc(c)+'</option>').join('');if(cats.includes(keep))sel.value=keep}

function showTab(t){activeTab=t;for(const n of ['search','queue','history']){$(n).classList.toggle('hidden',n!==t);$('tab'+n).classList.toggle('active',n===t)}if(t==='queue')refreshQueue();if(t==='history')refreshHistory()}

function savePrefs(){LS.set('nzb_mobile_prefs',JSON.stringify({sort:$('sort').value,minsize:$('minsize').value,maxsize:$('maxsize').value,mingrabs:$('mingrabs').value,indexer:$('indexer').value,prio:$('prio').value}));LS.set('nzb_mobile_cat',$('sabcat').value)}
function loadPrefs(){try{const p=JSON.parse(LS.get('nzb_mobile_prefs','{}'));if(p.sort)$('sort').value=p.sort;if(p.prio!==undefined)$('prio').value=p.prio;if(p.indexer&&[...$('indexer').options].some(o=>o.value===p.indexer))$('indexer').value=p.indexer;['minsize','maxsize','mingrabs'].forEach(k=>{if(p[k]!==undefined)$(k).value=p[k]})}catch{}}

function filterSort(list,f){
  const min=(Number(f.min)||0)*1048576,max=(Number(f.max)||0)*1048576,grabs=Number(f.grabs)||0;
  const a=list.filter(x=>(!min||x.size_bytes>=min)&&(!max||x.size_bytes<=max)&&(!grabs||(Number(x.grabs)||0)>=grabs));
  const by={size_asc:(x,y)=>x.size_bytes-y.size_bytes,size_desc:(x,y)=>y.size_bytes-x.size_bytes,date_asc:(x,y)=>(x.pub_ts||0)-(y.pub_ts||0),date_desc:(x,y)=>(y.pub_ts||0)-(x.pub_ts||0),grabs_desc:(x,y)=>(Number(y.grabs)||0)-(Number(x.grabs)||0),title_asc:(x,y)=>x.title.localeCompare(y.title),title_desc:(x,y)=>y.title.localeCompare(x.title)};
  return a.sort(by[f.mode]||by.date_desc);
}
function currentFilter(){return {min:$('minsize').value,max:$('maxsize').value,grabs:$('mingrabs').value,mode:$('sort').value}}
function addOptions(){return {category:$('sabcat').value,priority:$('prio').value}}
function updateBatchButton(){const n=selected.size,b=$('batchbtn');b.textContent='Add selected to SABnzbd ('+n+')';b.disabled=n===0||n>10}

function resultCard(x){
  const done=added.has(x.id);
  const other=(x.also_found&&x.also_found.length)?'<span class="pill">Also: '+esc(x.also_found.join(', '))+'</span>':'';
  return '<article class="result" data-id="'+esc(x.id)+'"><label class="sel"><input type="checkbox" data-act="sel"'+(selected.has(x.id)?' checked':'')+(done?' disabled':'')+'>Select</label><h3>'+esc(x.title)+'</h3><div class="meta"><span class="pill">'+esc(x.indexer)+'</span>'+other+'<span>'+esc(x.size)+'</span><span>'+esc(x.grabs||0)+' grabs</span><span>'+esc(x.category)+'</span><span>'+esc(x.date||'Date unavailable')+'</span></div><button data-act="add"'+(done?' disabled':'')+'>'+(done?'Added ✓':'Add to SABnzbd')+'</button></article>';
}
function renderResults(){
  $('resulttools').style.display=results.length?'block':'none';
  const a=filterSort(results,currentFilter());
  if(results.length)$('searchstatus').textContent='Showing '+a.length+' of '+results.length+' results';
  $('results').innerHTML=a.map(resultCard).join('')||'<div class="panel muted">No results match these filters.</div>';
  updateBatchButton();
}
async function search(){
  const st=$('searchstatus');st.className='status';st.textContent='Searching...';$('results').innerHTML='';results=[];selected.clear();added.clear();updateBatchButton();
  $('searchbtn').disabled=true;
  try{
    const d=await api('/api/search',{query:$('query').value.trim(),indexer:$('indexer').value,category:$('category').value.trim()});
    results=d.results||[];
    let msg=results.length+' unique result(s)';if(d.errors&&d.errors.length)msg+=' • '+d.errors.length+' indexer error(s)\n'+d.errors.join('\n');
    st.textContent=msg;if(d.errors&&d.errors.length&&!results.length)st.className='status error';
    renderResults();
  }catch(e){st.textContent=e.message;st.className='status error'}
  finally{$('searchbtn').disabled=false}
}
async function addResult(id,btn){
  btn.disabled=true;btn.textContent='Adding...';
  try{const d=await api('/api/add',Object.assign({id},addOptions()));added.add(id);selected.delete(id);toast('Added to SABnzbd'+(d.indexer?' (from '+d.indexer+')':''));renderResults();refreshQueue(true)}
  catch(e){btn.disabled=false;btn.textContent='Add to SABnzbd';toast(e.message,true)}
}
async function addSelected(){
  const ids=[...selected];if(!ids.length||ids.length>10)return;
  const b=$('batchbtn');b.disabled=true;b.textContent='Adding NZBs...';
  try{
    const d=await api('/api/add-batch',Object.assign({ids},addOptions()));
    (d.added||[]).forEach(x=>{added.add(x.id);selected.delete(x.id)});
    let msg='Added '+(d.added||[]).length+' of '+ids.length+' NZB(s).';
    if(d.errors&&d.errors.length)msg+='\n'+d.errors.map(x=>(x.title||x.id)+': '+x.error).join('\n');
    toast(msg,!!(d.errors&&d.errors.length));renderResults();refreshQueue(true);
  }catch(e){toast(e.message,true)}
  finally{updateBatchButton()}
}

function queueRow(x){
  const p=clampPct(x.percentage),paused=String(x.status).toLowerCase()==='paused';
  return '<div class="queueitem" data-id="'+esc(x.id)+'"><div class="qtitle">'+esc(x.name)+'</div><div class="meta"><span>'+esc(x.status)+'</span><span>'+esc(x.sizeleft)+' left of '+esc(x.size)+'</span><span>'+p.toFixed(1)+'%</span><span>ETA '+esc(x.eta||'—')+'</span>'+(x.cat&&x.cat!=='*'?'<span class="pill">'+esc(x.cat)+'</span>':'')+'</div><div class="bar"><span style="width:'+p+'%"></span></div><div class="row"><button class="secondary smallbtn" data-act="'+(paused?'resume':'pause')+'">'+(paused?'Resume':'Pause')+'</button><button class="danger smallbtn" data-act="delete">Delete</button></div></div>';
}
async function refreshQueue(silent){
  if(queueBusy)return;queueBusy=true;const st=$('queuestatus');
  try{
    const d=await api('/api/queue');
    st.className='status';
    let line=(d.state||'SABnzbd')+' • '+(d.speed||'0 B/s')+' • '+d.total+' queued';if(d.disk_free_gb!=null)line+=' • '+Number(d.disk_free_gb).toFixed(1)+' GB free';if(d.speed_limit)line+='\nSpeed limit: '+d.speed_limit;
    st.textContent=line;
    $('queueitems').innerHTML=d.slots.map(queueRow).join('')||'<div class="muted">Queue is empty.</div>';
  }catch(e){if(!silent||activeTab==='queue'){st.textContent=e.message;st.className='status error'}}
  finally{queueBusy=false}
}
async function itemAction(id,action){
  if(action==='delete'&&!confirm('Remove this item from the queue and delete its partial files?'))return;
  try{await api('/api/queue/item',{id,action});await refreshQueue(true)}catch(e){toast(e.message,true)}
}
async function queueAll(action){try{await api('/api/queue/'+action,{});await refreshQueue(true)}catch(e){toast(e.message,true)}}
async function setSpeed(){
  const v=$('speedmb').value.trim();if(v==='')return;
  try{await api('/api/speedlimit',{mbps:Number(v)});toast(Number(v)>0?'Speed limit set to '+v+' MB/s':'Speed limit removed');await refreshQueue(true)}catch(e){toast(e.message,true)}
}

function historyRow(x){
  const cls=x.status==='Completed'?'ok':(x.status==='Failed'?'bad':'warn');
  return '<div class="queueitem" data-id="'+esc(x.id)+'"><div class="qtitle">'+esc(x.name)+'</div><div class="meta"><span class="'+cls+'">'+esc(x.status)+'</span><span>'+esc(x.size)+'</span>'+(x.cat&&x.cat!=='*'?'<span class="pill">'+esc(x.cat)+'</span>':'')+(x.completed?'<span>'+esc(new Date(x.completed*1000).toLocaleString())+'</span>':'')+'</div>'+(x.fail_message?'<div class="hint bad">'+esc(x.fail_message)+'</div>':'')+(x.status==='Failed'?'<div class="row" style="margin-top:8px"><button class="secondary smallbtn" data-act="retry">Retry</button></div>':'')+'</div>';
}
async function refreshHistory(){
  const st=$('histstatus');
  try{const d=await api('/api/history');st.className='status';st.textContent=d.slots.length+' recent item(s)';$('historyitems').innerHTML=d.slots.map(historyRow).join('')||'<div class="muted">No history yet.</div>'}
  catch(e){st.textContent=e.message;st.className='status error'}
}
async function retryItem(id){try{await api('/api/history/retry',{id});toast('Retry requested');await refreshHistory()}catch(e){toast(e.message,true)}}

function bind(){
  $('pairbtn').addEventListener('click',pair);
  $('token').addEventListener('keydown',e=>{if(e.key==='Enter')pair()});
  document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>showTab(b.dataset.tab)));
  $('searchbtn').addEventListener('click',search);
  $('query').addEventListener('keydown',e=>{if(e.key==='Enter')search()});
  ['sort','minsize','maxsize','mingrabs'].forEach(id=>$(id).addEventListener('input',()=>{savePrefs();renderResults()}));
  ['sabcat','prio','indexer'].forEach(id=>$(id).addEventListener('change',savePrefs));
  $('selvisible').addEventListener('click',()=>{filterSort(results,currentFilter()).forEach(x=>{if(!added.has(x.id)&&selected.size<10)selected.add(x.id)});renderResults()});
  $('selnone').addEventListener('click',()=>{selected.clear();renderResults()});
  $('batchbtn').addEventListener('click',addSelected);
  $('results').addEventListener('click',e=>{const card=e.target.closest('.result');if(card&&e.target.dataset.act==='add')addResult(card.dataset.id,e.target)});
  $('results').addEventListener('change',e=>{const card=e.target.closest('.result');if(!card||e.target.dataset.act!=='sel')return;const id=card.dataset.id;if(e.target.checked){if(selected.size>=10){e.target.checked=false;toast('Select no more than 10 NZBs at a time.',true);return}selected.add(id)}else selected.delete(id);updateBatchButton()});
  $('queueitems').addEventListener('click',e=>{const row=e.target.closest('.queueitem'),act=e.target.dataset.act;if(row&&act)itemAction(row.dataset.id,act)});
  $('historyitems').addEventListener('click',e=>{const row=e.target.closest('.queueitem');if(row&&e.target.dataset.act==='retry')retryItem(row.dataset.id)});
  $('qresume').addEventListener('click',()=>queueAll('resume'));
  $('qpause').addEventListener('click',()=>queueAll('pause'));
  $('speedset').addEventListener('click',setSpeed);
  $('histrefresh').addEventListener('click',refreshHistory);
  setInterval(()=>{if(!document.hidden&&activeTab==='queue'&&!$('app').classList.contains('hidden'))refreshQueue(true)},4000);
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
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
}


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

    def _limited(self, limiter: SlidingWindowLimiter, what: str) -> bool:
        if limiter.allow(self.ip):
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
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
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
                "priorities": SAB_PRIORITIES,
            })
        elif path == "/api/sab/info":
            self.send_json(200, {"categories": sab_categories()})
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
                    "eta": x.get("timeleft", ""), "cat": x.get("cat", ""),
                } for x in slots],
            })
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
        elif path == "/api/speedlimit":
            mbps = to_float(body.get("mbps"), -1.0)
            if not 0 <= mbps <= 10000:
                raise ValueError("Speed limit must be between 0 and 10000 MB/s.")
            sab_request("config", {"name": "speedlimit", "value": "0" if mbps == 0 else f"{int(round(mbps * 1024))}K"})
            log.info("speed limit %s MB/s ip=%s", mbps, self.ip)
            self.send_json(200, {"ok": True})
        else:
            self.send_json(404, {"error": "Not found."})

    def _post_search(self, body: dict) -> None:
        if self._limited(SEARCH_LIMITER, "search"):
            return
        query = str(body.get("query") or "").strip()[:180]
        category = str(body.get("category") or "").strip()
        if category and not CATEGORY_RE.match(category):
            raise ValueError("Category must be numeric Newznab IDs, e.g. 2000 or 5030,5040.")
        chosen = str(body.get("indexer") or "*")
        indexers = get_config()["indexers"]
        if not indexers:
            raise ValueError("No enabled indexers were found in the desktop configuration.")
        selected = indexers if chosen == "*" else [x for x in indexers if x["id"] == chosen]
        if not selected:
            raise ValueError("Selected indexer does not exist.")

        combined: list[dict] = []
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=min(6, len(selected))) as pool:
            futures = {pool.submit(search_one, ix, query, category, MAX_RESULTS_PER_INDEXER): ix for ix in selected}
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
        output = cache_results(merged[:MAX_TOTAL_RESULTS])
        log.info("search ip=%s indexers=%d q=%r cat=%s results=%d errors=%d", self.ip, len(selected), query, category or "-", len(output), len(errors))
        self.send_json(200, {"results": output, "errors": errors})

    def _add_one(self, result_id: str, category: str, priority: str) -> dict:
        item = get_cached_result(result_id)
        if not item:
            raise ValueError("That search result expired. Search again and retry.")
        nzb_data, used = download_indexer_nzb(item)
        sab_addfile(nzb_data, item["title"], category, priority)
        log.info("add ip=%s title=%r indexer=%s cat=%s prio=%s bytes=%d", self.ip, item["title"], used, category or "-", priority or "-", len(nzb_data))
        return {"id": result_id, "title": item["title"], "bytes": len(nzb_data), "indexer": used}

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
        if self._limited(ADD_LIMITER, "add"):
            return
        category, priority = parse_add_options(body)
        unique_ids = list(dict.fromkeys(str(x) for x in ids))

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
        if action not in ("pause", "resume", "delete"):
            raise ValueError("Invalid action.")
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
    p = argparse.ArgumentParser(description="NZB Command Center - Mobile Companion")
    p.add_argument("--host", default=os.environ.get("NZB_MOBILE_HOST", DEFAULT_HOST), help="address to bind (default: all interfaces)")
    p.add_argument("--port", type=int, default=int(os.environ.get("NZB_MOBILE_PORT", DEFAULT_PORT)))
    p.add_argument("--config", type=Path, default=None, help="path to nzb_manager_config.json")
    p.add_argument("--allow-cidr", action="append", default=[], metavar="CIDR", help="also accept this network (repeatable)")
    p.add_argument("--allow-any-client", action="store_true", help="accept connections from ANY address (not recommended)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> None:
    global CONFIG_FILE, ALLOW_ANY_CLIENT
    args = parse_args(argv)
    if args.config:
        CONFIG_FILE = args.config.resolve()
    ALLOW_ANY_CLIENT = args.allow_any_client
    for cidr in args.allow_cidr:
        EXTRA_ALLOWED_NETS.append(ipaddress.ip_network(cidr, strict=False))
    setup_logging(args.verbose)

    cfg = init_state()
    if not cfg["indexers"]:
        log.warning("No enabled indexers were found in %s.", CONFIG_FILE.name)

    server = ThreadingHTTPServer((args.host, args.port), MobileHandler)
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
    print()
    print(" Keep this window open while using the mobile page. Ctrl+C stops the server.")
    print("=" * 66)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nStopping mobile server...")
    finally:
        server.server_close()


if __name__ == "__main__":
    try:
        main()
    except (ConfigError, OSError, ValueError) as exc:
        print(f"\nSTARTUP ERROR: {redact(exc)}")
        print("Check that mobile_server.py is beside nzb_manager_config.json and that the desktop app's config is valid.")
        if sys.stdin and sys.stdin.isatty():
            input("\nPress Enter to close...")
        sys.exit(1)
