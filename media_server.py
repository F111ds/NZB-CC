# media_server.py
# Standalone Jellyfin & Plex Media Server Client, Stream Monitor & Disk Audit Module
# Designed for NZB Command Center — Operations Center Edition
#
# Zero external dependencies beyond Python stdlib + customtkinter.

import json
import os
import platform
import queue
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk


SERVER_TYPES = ["Jellyfin", "Plex"]
SERVER_VIEWS = [
    "Movies",
    "TV Shows",
    "Audit: Missing from Server",
    "Audit: Orphaned on Server",
]
SERVER_PAGE_SIZE = 50
_SYS = platform.system()


_MAX_SUFFIX = 6  # deepest "same ending" compared when server and disk use different roots


def _norm_media_path(raw_path: str) -> tuple[str, list[str], str]:
    """(full path, path endings of 2.._MAX_SUFFIX segments, file name), all lower-case."""
    clean = urllib.parse.unquote((raw_path or "").strip())
    clean_slash = clean.replace("\\", "/").rstrip("/")
    parts = [p for p in clean_slash.lower().split("/") if p]
    fname = parts[-1] if parts else ""
    suffixes = ["/".join(parts[-k:]) for k in range(2, min(len(parts), _MAX_SUFFIX) + 1)]
    return clean_slash.lower(), suffixes, fname


def parse_path_map(raw: str) -> list[tuple[str, str]]:
    """Parses 'server_prefix=local_prefix' pairs separated by ';' or newlines.

    Example: "/data/movies=D:\\Movies; /data/tv=\\\\nas\\tv"
    Returns (server_prefix_lower, local_prefix) with forward slashes and no trailing slash,
    longest server prefix first so the most specific mapping wins.
    """
    pairs: list[tuple[str, str]] = []
    for chunk in re.split(r"[;\n]+", raw or ""):
        if "=" not in chunk:
            continue
        srv, local = chunk.split("=", 1)
        srv = srv.strip().replace("\\", "/").rstrip("/").lower()
        local = local.strip().replace("\\", "/").rstrip("/")
        if srv and local:
            pairs.append((srv, local))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return pairs


def apply_path_map(raw_path: str, path_map: list[tuple[str, str]]) -> str:
    """Rewrites a path as the media server reports it into the equivalent local path."""
    norm = urllib.parse.unquote((raw_path or "").strip()).replace("\\", "/")
    low = norm.lower()
    for srv_prefix, local_prefix in path_map:
        if low == srv_prefix or low.startswith(srv_prefix + "/"):
            return local_prefix + norm[len(srv_prefix):]
    return raw_path or ""


# File names too generic to identify a file on their own (e.g. "S01E01.mkv", "movie.mkv").
_GENERIC_STEM_RE = re.compile(r"(s\d{1,2}e\d{1,3}|e?\d{1,3}|episode[\s._-]?\d+|movie|video|film|index)", re.I)


def _is_distinctive_name(fname: str) -> bool:
    stem = fname.rsplit(".", 1)[0] if "." in fname else fname
    return len(stem) >= 8 and not _GENERIC_STEM_RE.fullmatch(stem)


def _tint(hex_color: str, amount: float = 0.16, base: str = "#0F1E25") -> str:
    """Blends a colour into the dark card background (tinted badge backgrounds)."""
    try:
        c = [int(hex_color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
        b = [int(base.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    except (ValueError, IndexError):
        return base
    return "#%02X%02X%02X" % tuple(round(b[i] + (c[i] - b[i]) * amount) for i in range(3))


def compute_audit(
    movies: list[dict], episodes: list[dict], disk_items: list[dict], path_map_raw: str
) -> tuple[list[dict], list[dict]]:
    """Compares server items with files on disk -> (missing_from_server, orphaned_on_server).

    Pure function, so it can run on a worker thread (it may touch the network share via
    Path.exists() for orphan checks). Matching, strongest first:
      1. full path, after rewriting server paths through the user's path map;
      2. path ending (last 2..6 segments, e.g. "show a/season 01/s01e01.mkv"), only if that
         ending is unique on BOTH sides (handles different roots without a path map);
      3. file name alone, only if it is unique on both sides and not generic
         (so "S01E01.mkv" from two different shows can never match each other).
    """
    path_map = parse_path_map(path_map_raw)

    srv_entries: list[dict] = []
    for m in movies:
        f = m.get("file") or ""
        if f:
            srv_entries.append({
                "title": f"{m.get('title', 'Movie')} ({m.get('year', '')})".replace(" ()", ""),
                "file": f,
                "kind": "Movie",
            })
    for ep in episodes:
        f = ep.get("file") or ""
        if f:
            srv_entries.append({
                "title": f"{ep.get('showtitle', 'TV')} — S{int(ep.get('season', 0)):02d}E{int(ep.get('episode', 0)):02d}",
                "file": f,
                "kind": "Episode",
            })

    srv_keys = [_norm_media_path(apply_path_map(se["file"], path_map)) for se in srv_entries]
    disk_valid = [d for d in disk_items if d.get("path")]
    disk_keys = [_norm_media_path(d["path"]) for d in disk_valid]

    srv_full = {k[0] for k in srv_keys}
    disk_full = {k[0] for k in disk_keys}
    srv_suffix = Counter(s for k in srv_keys for s in k[1])
    disk_suffix = Counter(s for k in disk_keys for s in k[1])
    srv_names = Counter(k[2] for k in srv_keys if k[2])
    disk_names = Counter(k[2] for k in disk_keys if k[2])

    def is_match(key, other_full, own_suffix, other_suffix, own_names, other_names) -> bool:
        full, suffixes, fname = key
        if full and full in other_full:
            return True
        if any(own_suffix[s] == 1 and other_suffix[s] == 1 for s in suffixes):
            return True
        if fname and _is_distinctive_name(fname) and own_names[fname] == 1 and other_names[fname] == 1:
            return True
        return False

    missing: list[dict] = []
    for d, key in zip(disk_valid, disk_keys):
        if not is_match(key, srv_full, disk_suffix, srv_suffix, disk_names, srv_names):
            fpath = d["path"]
            missing.append({
                "title": d.get("title") or Path(fpath).name,
                "file": fpath,
                "kind": d.get("media_type") or "Media",
                "corrupt": bool(d.get("is_corrupt", False)),
                "folder_exists": Path(fpath).parent.exists(),
            })

    orphaned: list[dict] = []
    if disk_valid:
        for se, key in zip(srv_entries, srv_keys):
            if is_match(key, disk_full, srv_suffix, disk_suffix, srv_names, disk_names):
                continue
            local = apply_path_map(se["file"], path_map)
            is_remote = local.startswith(("smb://", "nfs://", "http://", "https://"))
            if local and not is_remote and Path(local).exists():
                continue  # exists locally, just outside the scanned Media Inspector folders
            orphaned.append(se)
    return missing, orphaned


def _fmt_ms(ms: float | int) -> str:
    total = max(0, int(round(float(ms) / 1000.0)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"


def _format_http_err(exc: Exception, server_name: str = "Server") -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return f"HTTP {exc.code}: {server_name} rejected API key / token."
        return f"HTTP {exc.code}: {getattr(exc, 'reason', 'HTTP Error')}"
    return str(exc)


# ==============================================================================
# 1. JELLYFIN & PLEX API CLIENTS
# ==============================================================================

def jellyfin_auth_header(api_key: str, client: str = "NZB Command Center", device: str = "Desktop") -> str:
    """The standard Jellyfin/Emby Authorization header carrying an API key."""
    return (f'MediaBrowser Client="{client}", Device="{device}", DeviceId="nzbcc-{device.lower()}", '
            f'Version="1.0", Token="{api_key}"')


class JellyfinClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = (base_url or "").strip().rstrip("/")
        if self.base_url and not self.base_url.startswith(("http://", "https://")):
            self.base_url = f"http://{self.base_url}"
        self.api_key = (api_key or "").strip()
        self._user_id_cache: str | None = None

    def _request(self, path: str, method: str = "GET", params: dict | None = None, timeout: float = 10.0):
        if not self.base_url or not self.api_key:
            raise ValueError("Jellyfin URL and API key are required.")

        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        headers = {
            # Jellyfin 10.11+ refuses the legacy X-Emby-Token header by default; the
            # MediaBrowser Authorization header works on every version, so send both.
            "Authorization": jellyfin_auth_header(self.api_key),
            "X-Emby-Token": self.api_key,
            "Accept": "application/json",
        }
        req = urllib.request.Request(url, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace").strip()
        except Exception as exc:
            raise RuntimeError(_format_http_err(exc, "Jellyfin")) from exc

        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Jellyfin returned non-JSON from {path}: {raw[:120]}") from exc

    def _get_user_id(self) -> str:
        if self._user_id_cache:
            return self._user_id_cache
        try:
            users = self._request("/Users", timeout=6.0)
            if isinstance(users, list) and users:
                admin = next(
                    (u for u in users if (u.get("Policy") or {}).get("IsAdministrator")),
                    users[0],
                )
                uid = str(admin.get("Id") or "")
                if uid:
                    self._user_id_cache = uid
                    return uid
        except Exception:
            pass
        return ""

    def test_connection(self) -> tuple[str, int]:
        t0 = time.monotonic()
        info = self._request("/System/Info", timeout=6.0)
        name = info.get("ServerName") or "Jellyfin"
        ver = info.get("Version") or "?"
        ms = int((time.monotonic() - t0) * 1000)
        return f"{name} v{ver}", ms

    def get_sessions(self) -> list[dict]:
        raw_sessions = self._request("/Sessions", timeout=6.0)
        if not isinstance(raw_sessions, list):
            return []

        active: list[dict] = []
        for s in raw_sessions:
            now_item = s.get("NowPlayingItem")
            if not now_item:
                continue
            ps = s.get("PlayState") or {}
            t_info = s.get("TranscodingInfo") or {}

            itype = now_item.get("Type", "")
            name = now_item.get("Name") or "Unknown"
            if itype == "Episode":
                series = now_item.get("SeriesName") or "Series"
                sn = int(now_item.get("ParentIndexNumber") or 0)
                en = int(now_item.get("IndexNumber") or 0)
                title = f"{series} — S{sn:02d}E{en:02d} — {name}"
            elif now_item.get("ProductionYear"):
                title = f"{name} ({now_item['ProductionYear']})"
            else:
                title = name

            dur_ticks = float(now_item.get("RunTimeTicks") or 0)
            pos_ticks = float(ps.get("PositionTicks") or 0)
            pct = (pos_ticks / dur_ticks * 100.0) if dur_ticks > 0 else 0.0
            play_method = ps.get("PlayMethod") or ("Transcode" if t_info else "DirectPlay")

            active.append({
                "session_id": str(s.get("Id") or ""),
                "user": s.get("UserName") or "User",
                "device": f"{s.get('Client', 'Client')} ({s.get('DeviceName', 'Device')})",
                "title": title,
                "paused": bool(ps.get("IsPaused", False)),
                "percentage": min(100.0, max(0.0, pct)),
                "elapsed": _fmt_ms(pos_ticks / 10_000.0),
                "total": _fmt_ms(dur_ticks / 10_000.0),
                "play_method": play_method,
                "can_control": True,
            })
        return active

    def control_session(self, session_id: str, command: str) -> None:
        # command in ('PlayPause', 'Stop')
        if not session_id:
            return
        self._request(f"/Sessions/{urllib.parse.quote(session_id)}/Playing/{command}", method="POST", timeout=6.0)

    def refresh_libraries(self) -> None:
        self._request("/Library/Refresh", method="POST", timeout=10.0)

    def _fetch_items(self, item_type: str, page_size: int = 1000, max_items: int = 250_000) -> list[dict]:
        # Pages through the whole library; a single capped request silently dropped items
        # past the limit, which then showed up as false "missing from server" audit entries.
        uid = self._get_user_id()
        endpoint = f"/Users/{uid}/Items" if uid else "/Items"
        items: list[dict] = []
        start = 0
        while start < max_items:
            res = self._request(
                endpoint,
                params={
                    "Recursive": "true",
                    "IncludeItemTypes": item_type,
                    # RecursiveItemCount is only returned when requested explicitly.
                    "Fields": "Path,ProductionYear,RecursiveItemCount",
                    "SortBy": "SortName",
                    "SortOrder": "Ascending",
                    "StartIndex": start,
                    "Limit": page_size,
                    "EnableTotalRecordCount": "true",
                },
                timeout=30.0,
            )
            page = res.get("Items") or []
            items.extend(page)
            start += len(page)
            total = res.get("TotalRecordCount")
            if len(page) < page_size or (isinstance(total, int) and start >= total):
                break
        return items

    def get_movies(self) -> list[dict]:
        out: list[dict] = []
        for it in self._fetch_items("Movie"):
            ud = it.get("UserData") or {}
            out.append({
                "title": it.get("Name") or "Unknown",
                "year": str(it.get("ProductionYear") or ""),
                "file": it.get("Path") or "",
                "watched": bool(ud.get("Played", False)),
            })
        return out

    def get_tv_shows(self) -> list[dict]:
        out: list[dict] = []
        for it in self._fetch_items("Series"):
            ud = it.get("UserData") or {}
            # RecursiveItemCount = episodes. (ChildCount is the number of seasons, so it is not a
            # valid fallback for an episode total.)
            total_eps = int(it.get("RecursiveItemCount") or 0)
            unplayed = int(ud.get("UnplayedItemCount") or 0)
            watched_eps = max(0, total_eps - unplayed) if total_eps > 0 else 0
            out.append({
                "title": it.get("Name") or "Unknown",
                "year": str(it.get("ProductionYear") or ""),
                "file": it.get("Path") or "",
                "episodes": total_eps,
                "watched_episodes": watched_eps,
            })
        return out

    def get_episodes(self) -> list[dict]:
        out: list[dict] = []
        for it in self._fetch_items("Episode", page_size=2000):
            out.append({
                "title": it.get("Name") or "Episode",
                "showtitle": it.get("SeriesName") or "Series",
                "season": int(it.get("ParentIndexNumber") or 0),
                "episode": int(it.get("IndexNumber") or 0),
                "file": it.get("Path") or "",
            })
        return out


class PlexClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = (base_url or "").strip().rstrip("/")
        if self.base_url and not self.base_url.startswith(("http://", "https://")):
            self.base_url = f"http://{self.base_url}"
        self.token = (token or "").strip()

    def _request(self, path: str, params: dict | None = None, timeout: float = 12.0) -> dict:
        if not self.base_url or not self.token:
            raise ValueError("Plex URL and X-Plex-Token are required.")

        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        headers = {
            "X-Plex-Token": self.token,
            "Accept": "application/json",
        }
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace").strip()
        except Exception as exc:
            raise RuntimeError(_format_http_err(exc, "Plex")) from exc

        if not raw:
            return {}
        if raw.startswith("<"):
            # Rare fallback if Plex responds with XML
            root = ET.fromstring(raw)
            return {"_xml_root": root}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Plex returned non-JSON from {path}: {raw[:120]}") from exc

    def test_connection(self) -> tuple[str, int]:
        t0 = time.monotonic()
        data = self._request("/", timeout=6.0)
        mc = data.get("MediaContainer") or {}
        name = mc.get("friendlyName") or "Plex Media Server"
        ver = mc.get("version") or "?"
        ms = int((time.monotonic() - t0) * 1000)
        return f"{name} v{ver}", ms

    def get_sessions(self) -> list[dict]:
        data = self._request("/status/sessions", timeout=6.0)
        mc = data.get("MediaContainer") or {}
        metas = mc.get("Metadata") or []

        active: list[dict] = []
        for m in metas:
            mtype = m.get("type", "")
            raw_title = m.get("title") or "Unknown"
            if mtype == "episode":
                show = m.get("grandparentTitle") or "Series"
                sn = int(m.get("parentIndex") or 0)
                en = int(m.get("index") or 0)
                title = f"{show} — S{sn:02d}E{en:02d} — {raw_title}"
            elif m.get("year"):
                title = f"{raw_title} ({m['year']})"
            else:
                title = raw_title

            user_obj = m.get("User") or {}
            player_obj = m.get("Player") or {}
            transcode_obj = m.get("TranscodeSession") or {}

            dur_ms = float(m.get("duration") or 0)
            off_ms = float(m.get("viewOffset") or 0)
            pct = (off_ms / dur_ms * 100.0) if dur_ms > 0 else 0.0
            state = str(player_obj.get("state") or "playing").lower()

            if transcode_obj:
                vdec = transcode_obj.get("videoDecision") or "transcode"
                play_method = f"Transcode ({vdec})"
            else:
                play_method = "Direct Play"

            active.append({
                "session_id": str(m.get("sessionKey") or ""),
                "user": user_obj.get("title") or "User",
                "device": f"{player_obj.get('product', 'Plex')} ({player_obj.get('title', 'Device')})",
                "title": title,
                "paused": state == "paused",
                "percentage": min(100.0, max(0.0, pct)),
                "elapsed": _fmt_ms(off_ms),
                "total": _fmt_ms(dur_ms),
                "play_method": play_method,
                "can_control": False,
            })
        return active

    def control_session(self, session_id: str, command: str) -> None:
        raise RuntimeError("Remote pause/stop is only available for Jellyfin sessions here.")

    def _get_sections(self) -> list[dict]:
        data = self._request("/library/sections", timeout=10.0)
        mc = data.get("MediaContainer") or {}
        return mc.get("Directory") or []

    def refresh_libraries(self) -> None:
        self._request("/library/sections/all/refresh", timeout=10.0)

    def get_movies(self) -> list[dict]:
        out: list[dict] = []
        for sec in self._get_sections():
            if sec.get("type") != "movie":
                continue
            key = sec.get("key")
            data = self._request(f"/library/sections/{key}/all", timeout=20.0)
            for m in (data.get("MediaContainer") or {}).get("Metadata") or []:
                media_list = m.get("Media") or []
                parts = (media_list[0].get("Part") or []) if media_list else []
                fpath = parts[0].get("file", "") if parts else ""
                out.append({
                    "title": m.get("title") or "Unknown",
                    "year": str(m.get("year") or ""),
                    "file": fpath,
                    "watched": int(m.get("viewCount") or 0) > 0,
                })
        out.sort(key=lambda x: x["title"].lower())
        return out

    def get_tv_shows(self) -> list[dict]:
        out: list[dict] = []
        for sec in self._get_sections():
            if sec.get("type") != "show":
                continue
            key = sec.get("key")
            data = self._request(f"/library/sections/{key}/all", timeout=20.0)
            for s in (data.get("MediaContainer") or {}).get("Metadata") or []:
                locs = s.get("Location") or []
                fpath = locs[0].get("path", "") if locs else ""
                out.append({
                    "title": s.get("title") or "Unknown",
                    "year": str(s.get("year") or ""),
                    "file": fpath,
                    "episodes": int(s.get("leafCount") or 0),
                    "watched_episodes": int(s.get("viewedLeafCount") or 0),
                })
        out.sort(key=lambda x: x["title"].lower())
        return out

    def get_episodes(self) -> list[dict]:
        out: list[dict] = []
        for sec in self._get_sections():
            if sec.get("type") != "show":
                continue
            key = sec.get("key")
            data = self._request(f"/library/sections/{key}/all", params={"type": 4}, timeout=25.0)
            for ep in (data.get("MediaContainer") or {}).get("Metadata") or []:
                media_list = ep.get("Media") or []
                parts = (media_list[0].get("Part") or []) if media_list else []
                fpath = parts[0].get("file", "") if parts else ""
                out.append({
                    "title": ep.get("title") or "Episode",
                    "showtitle": ep.get("grandparentTitle") or "Series",
                    "season": int(ep.get("parentIndex") or 0),
                    "episode": int(ep.get("index") or 0),
                    "file": fpath,
                })
        return out


# ==============================================================================
# 2. MEDIA SERVER UI PANEL (Jellyfin & Plex Operations Tab)
# ==============================================================================

class MediaServerPanel(ctk.CTkFrame):
    def __init__(
        self,
        master,
        colors: dict,
        ui_font: str,
        mono_font: str,
        server_config: dict | None = None,
        on_config_change=None,
        on_status=None,
        on_server_state=None,
        get_disk_items=None,
    ):
        super().__init__(master, fg_color="transparent")
        self.colors = colors
        self.ui_font = ui_font
        self.mono_font = mono_font
        self.on_config_change = on_config_change
        self.on_status = on_status
        self.on_server_state = on_server_state
        self.get_disk_items = get_disk_items

        cfg = server_config or {}
        self.server_type = str(cfg.get("media_server_type") or "Jellyfin")
        if self.server_type not in SERVER_TYPES:
            self.server_type = "Jellyfin"
        self.server_url = str(cfg.get("media_server_url") or "").strip()
        self.server_token = str(cfg.get("media_server_token") or "").strip()
        self.auto_scan = bool(cfg.get("media_server_auto_scan", True))
        self.path_map_raw = str(cfg.get("media_server_path_map") or "").strip()
        self.client = self._build_client()

        self._shutting_down = False
        self._ui_queue: queue.Queue = queue.Queue()
        self._drain_after_id = None
        self._poll_after_id = None
        self._poll_in_flight = False
        self._last_auto_scan_ts = 0.0

        self._movies: list[dict] = []
        self._shows: list[dict] = []
        self._episodes: list[dict] = []
        self._audit_missing: list[dict] = []
        self._audit_orphaned: list[dict] = []
        self._disk_items: list[dict] = []
        self._has_disk_items = False
        self._pending_refresh_names: list[str] = []
        self._refresh_job = None
        self._filtered_rows: list[dict] = []
        self._current_page: int = 0

        self._build_ui()
        self._drain_ui_queue()

        if self.server_url and self.server_token:
            self.after(500, lambda: self._poll_sessions(silent=True))
        else:
            self._emit_server_state("Not Configured", "text_muted")
        self._poll_after_id = self.after(6000, self._poll_loop)

    def _build_client(self):
        if self.server_type == "Plex":
            return PlexClient(self.server_url, self.server_token)
        return JellyfinClient(self.server_url, self.server_token)

    def shutdown(self) -> None:
        self._shutting_down = True
        for jid in (self._drain_after_id, self._poll_after_id, self._refresh_job):
            if jid:
                try:
                    self.after_cancel(jid)
                except Exception:
                    pass

    def _post_ui(self, fn) -> None:
        if not self._shutting_down:
            self._ui_queue.put(fn)

    def _drain_ui_queue(self) -> None:
        if self._shutting_down:
            return
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception as exc:  # surface instead of silently swallowing
                    try:
                        self._emit_status(f"Media Server tab internal error: {exc!r}", log=True, level="ERROR")
                    except Exception:
                        pass
        except queue.Empty:
            pass
        self._drain_after_id = self.after(50, self._drain_ui_queue)

    def _run_bg(self, fn, on_ok=None, on_err=None) -> None:
        def worker():
            try:
                res = fn()
                if on_ok and not self._shutting_down:
                    self._post_ui(lambda r=res: on_ok(r))
            except Exception as exc:
                if self._shutting_down:
                    return
                if on_err:
                    self._post_ui(lambda e=exc: on_err(e))
                else:
                    self._post_ui(lambda e=exc: self._emit_status(f"{self.server_type} error: {e}", log=True, level="ERROR"))

        threading.Thread(target=worker, daemon=True).start()

    def _emit_status(self, msg: str, log: bool = False, level: str = "INFO") -> None:
        if self.on_status:
            self.on_status(msg, log=log, level=level)

    def _emit_server_state(self, text: str, color_key: str) -> None:
        if self.on_server_state:
            self.on_server_state(text, color_key)

    # ------------------------------------------------------------------
    # SABnzbd Completion Hook (Auto-Refresh Library)
    # ------------------------------------------------------------------
    def notify_download_completed(self, job_name: str) -> None:
        if not (self.server_url and self.server_token) or self._shutting_down:
            return
        if not self.auto_scan_var.get():
            return
        # Batch completions: the first one schedules a refresh REFRESH_DELAY_MS later (also giving
        # SABnzbd time to finish moving files); any that finish meanwhile join that same refresh.
        # Previously completions within 15 s of a refresh were silently dropped.
        self._pending_refresh_names.append(job_name)
        if self._refresh_job is None:
            self._refresh_job = self.after(self.REFRESH_DELAY_MS, self._flush_refresh)

    REFRESH_DELAY_MS = 15_000

    def _flush_refresh(self) -> None:
        self._refresh_job = None
        names, self._pending_refresh_names = self._pending_refresh_names, []
        if not names or self._shutting_down:
            return
        self._last_auto_scan_ts = time.monotonic()
        srv_name = self.server_type
        what = f"'{names[0]}'" if len(names) == 1 else f"{len(names)} completed downloads"
        self._run_bg(
            lambda: self.client.refresh_libraries(),
            on_ok=lambda _r: self._emit_status(f"{srv_name} library refresh triggered for {what}.", log=True, level="OK"),
            on_err=lambda exc: self._emit_status(f"{srv_name} auto-refresh failed: {exc}", log=True, level="WARN"),
        )

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        C = self.colors
        self.grid_rowconfigure(3, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Row 0: Connection & Automation Bar
        conn = ctk.CTkFrame(self, fg_color=C["bg_panel"], border_width=1, border_color=C["border"], corner_radius=10)
        conn.grid(row=0, column=0, sticky="ew", padx=4, pady=(6, 4))

        self.type_var = ctk.StringVar(value=self.server_type)
        ctk.CTkOptionMenu(
            conn,
            variable=self.type_var,
            values=SERVER_TYPES,
            width=105,
            height=28,
            fg_color=C["bg_card"],
            button_color=C["border"],
            command=self._on_type_changed,
        ).pack(side="left", padx=(10, 8), pady=8)

        self.url_entry = ctk.CTkEntry(
            conn,
            width=230,
            height=28,
            fg_color=C["bg_card"],
            border_color=C["border"],
            placeholder_text="http://127.0.0.1:8096 (Jellyfin) or :32400 (Plex)",
        )
        self.url_entry.insert(0, self.server_url)
        self.url_entry.pack(side="left", padx=(0, 8), pady=8)

        self.token_entry = ctk.CTkEntry(
            conn,
            width=210,
            height=28,
            fg_color=C["bg_card"],
            border_color=C["border"],
            placeholder_text="Jellyfin API Key or X-Plex-Token",
            show="*",
        )
        self.token_entry.insert(0, self.server_token)
        self.token_entry.pack(side="left", padx=(0, 8), pady=8)

        ctk.CTkButton(
            conn,
            text="Connect & Save",
            width=116,
            height=28,
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            text_color="#041016",
            font=ctk.CTkFont(family=self.ui_font, size=11, weight="bold"),
            command=self._save_and_connect,
        ).pack(side="left", padx=(0, 8), pady=8)

        self.conn_badge = ctk.CTkLabel(
            conn,
            text="Not Configured" if not (self.server_url and self.server_token) else "Connecting...",
            fg_color=C["bg_card"],
            corner_radius=11,
            padx=10,
            pady=3,
            font=ctk.CTkFont(family=self.ui_font, size=10),
            text_color=C["text_muted"] if not (self.server_url and self.server_token) else C["amber"],
        )
        self.conn_badge.pack(side="left", padx=4, pady=8)

        self.auto_scan_var = ctk.BooleanVar(value=self.auto_scan)
        ctk.CTkSwitch(
            conn,
            text="Auto-Scan on DL",
            variable=self.auto_scan_var,
            progress_color=C["accent"],
            font=ctk.CTkFont(family=self.ui_font, size=11),
            command=self._persist_config,
        ).pack(side="right", padx=(6, 12), pady=8)

        # Row 1: Active Streams (left) + Library Actions (right)
        top_split = ctk.CTkFrame(self, fg_color="transparent")
        top_split.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 4))
        top_split.grid_columnconfigure(0, weight=3)
        top_split.grid_columnconfigure(1, weight=1)

        streams_card = ctk.CTkFrame(top_split, fg_color=C["bg_panel"], border_width=1, border_color=C["border"], corner_radius=10)
        streams_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        st_hdr = ctk.CTkFrame(streams_card, fg_color="transparent")
        st_hdr.pack(fill="x", padx=12, pady=(8, 4))

        self.streams_title_lbl = ctk.CTkLabel(
            st_hdr,
            text="ACTIVE STREAMS (0)",
            font=ctk.CTkFont(family=self.ui_font, size=10, weight="bold"),
            text_color=C["text_muted"],
        )
        self.streams_title_lbl.pack(side="left")

        ctk.CTkButton(
            st_hdr,
            text="Refresh Streams",
            width=104,
            height=22,
            fg_color=C["bg_card"],
            hover_color=C["bg_card_alt"],
            font=ctk.CTkFont(family=self.ui_font, size=10),
            command=lambda: self._poll_sessions(silent=False),
        ).pack(side="right")

        self.streams_frame = ctk.CTkScrollableFrame(streams_card, height=96, fg_color="transparent")
        self.streams_frame.pack(fill="x", padx=6, pady=(0, 6))
        self._render_sessions([])

        actions_card = ctk.CTkFrame(top_split, fg_color=C["bg_panel"], border_width=1, border_color=C["border"], corner_radius=10)
        actions_card.grid(row=0, column=1, sticky="nsew", padx=(4, 0))

        ctk.CTkLabel(
            actions_card,
            text="SERVER ACTIONS",
            anchor="w",
            font=ctk.CTkFont(family=self.ui_font, size=10, weight="bold"),
            text_color=C["text_muted"],
        ).pack(fill="x", padx=12, pady=(10, 6))

        ctk.CTkButton(
            actions_card,
            text="Sync Library & Audit",
            height=30,
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            text_color="#041016",
            font=ctk.CTkFont(family=self.ui_font, size=11, weight="bold"),
            command=self._sync_library,
        ).pack(fill="x", padx=12, pady=(0, 6))

        ctk.CTkButton(
            actions_card,
            text="Scan All Libraries Now",
            height=28,
            fg_color=C["bg_card"],
            hover_color=C["bg_card_alt"],
            command=self._trigger_server_scan,
        ).pack(fill="x", padx=12, pady=(0, 6))

        self.path_map_entry = ctk.CTkEntry(
            actions_card,
            height=26,
            fg_color=C["bg_card"],
            border_color=C["border"],
            placeholder_text="Path map: /data/media=D:\\Media",
            font=ctk.CTkFont(family=self.mono_font, size=10),
        )
        if self.path_map_raw:
            self.path_map_entry.insert(0, self.path_map_raw)
        self.path_map_entry.pack(fill="x", padx=12, pady=(0, 10))
        self.path_map_entry.bind("<Return>", self._on_path_map_changed)
        self.path_map_entry.bind("<FocusOut>", self._on_path_map_changed)

        # Row 2: View Switcher, Search & Pagination
        vbar = ctk.CTkFrame(self, fg_color=C["bg_panel"], border_width=1, border_color=C["border"], corner_radius=10)
        vbar.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 4))

        self.view_var = ctk.StringVar(value=SERVER_VIEWS[0])
        ctk.CTkSegmentedButton(
            vbar,
            variable=self.view_var,
            values=SERVER_VIEWS,
            fg_color=C["bg_card"],
            selected_color=C["accent"],
            selected_hover_color=C["accent_hover"],
            unselected_color=C["bg_card"],
            text_color=C["text_primary"],
            command=lambda _v: self._apply_view_filter(reset_page=True),
        ).pack(side="left", padx=(10, 8), pady=7)

        self.filter_entry = ctk.CTkEntry(
            vbar,
            width=190,
            height=28,
            fg_color=C["bg_card"],
            border_color=C["border"],
            placeholder_text="Filter title or path...",
        )
        self.filter_entry.pack(side="left", padx=(0, 8), pady=7)
        self.filter_entry.bind("<KeyRelease>", lambda _e: self._apply_view_filter(reset_page=True))
        self.filter_entry.bind("<Escape>", self._clear_filter)

        self.next_btn = ctk.CTkButton(
            vbar,
            text="Next ▶",
            width=66,
            height=26,
            fg_color=C["bg_card"],
            hover_color=C["bg_card_alt"],
            command=lambda: self._change_page(1),
        )
        self.next_btn.pack(side="right", padx=(4, 10), pady=7)

        self.page_lbl = ctk.CTkLabel(
            vbar,
            text="Page 1 / 1",
            font=ctk.CTkFont(family=self.ui_font, size=11),
            text_color=C["text_secondary"],
        )
        self.page_lbl.pack(side="right", padx=6, pady=7)

        self.prev_btn = ctk.CTkButton(
            vbar,
            text="◀ Prev",
            width=66,
            height=26,
            fg_color=C["bg_card"],
            hover_color=C["bg_card_alt"],
            command=lambda: self._change_page(-1),
        )
        self.prev_btn.pack(side="right", padx=4, pady=7)

        self.summary_lbl = ctk.CTkLabel(
            vbar,
            text="",
            font=ctk.CTkFont(family=self.ui_font, size=10),
            text_color=C["text_muted"],
        )
        self.summary_lbl.pack(side="right", padx=8, pady=7)

        # Row 3: Scrollable List
        self.list_frame = ctk.CTkScrollableFrame(
            self, fg_color=C["bg_panel"], border_width=1, border_color=C["border"], corner_radius=10
        )
        self.list_frame.grid(row=3, column=0, sticky="nsew", padx=4, pady=(0, 4))

        ctk.CTkLabel(
            self.list_frame,
            text="Select Jellyfin or Plex above, enter your URL & API Key / Token, and click 'Connect & Save'.",
            text_color=C["text_muted"],
        ).pack(pady=40)

    # ------------------------------------------------------------------
    # Connection, Polling & Stream Controls
    # ------------------------------------------------------------------
    def _on_type_changed(self, chosen: str) -> None:
        if not self.url_entry.get().strip():
            self.url_entry.configure(
                placeholder_text="http://127.0.0.1:8096" if chosen == "Jellyfin" else "http://127.0.0.1:32400"
            )
        self._persist_config()

    def _persist_config(self) -> None:
        self.server_type = self.type_var.get()
        self.server_url = self.url_entry.get().strip()
        self.server_token = self.token_entry.get().strip()
        self.auto_scan = self.auto_scan_var.get()
        self.path_map_raw = self.path_map_entry.get().strip()
        self.client = self._build_client()
        if self.on_config_change:
            self.on_config_change({
                "media_server_type": self.server_type,
                "media_server_url": self.server_url,
                "media_server_token": self.server_token,
                "media_server_auto_scan": self.auto_scan,
                "media_server_path_map": self.path_map_raw,
            })

    def _on_path_map_changed(self, _event=None) -> None:
        new_raw = self.path_map_entry.get().strip()
        if new_raw == self.path_map_raw:
            return
        self._persist_config()
        self._emit_status(f"Media server path map saved ({len(parse_path_map(new_raw))} mapping(s)).", log=True)
        if self._movies or self._episodes:
            # Re-run the audit against the new mapping (on a worker) without re-fetching the library.
            movies, episodes, disk = list(self._movies), list(self._episodes), list(self._disk_items)

            def done(res):
                self._audit_missing, self._audit_orphaned = res
                self._apply_view_filter(reset_page=True)

            self._run_bg(lambda: compute_audit(movies, episodes, disk, new_raw), on_ok=done)

    def _save_and_connect(self) -> None:
        C = self.colors
        self._persist_config()
        if not (self.server_url and self.server_token):
            self.conn_badge.configure(text="Missing URL / Key", text_color=C["amber"])
            self._emit_server_state("Not Configured", "text_muted")
            return

        self.conn_badge.configure(text="Testing...", text_color=C["sky"])
        srv_label = self.server_type

        def ok(res):
            info, ms = res
            self.conn_badge.configure(text=f"{info} ({ms} ms)", text_color=C["green"])
            self._emit_server_state(f"{srv_label}: Online", "green")
            self._emit_status(f"Connected to {srv_label}: {info} ({ms} ms)", log=True, level="OK")
            self._poll_sessions(silent=True)
            self._sync_library()

        def err(exc):
            self.conn_badge.configure(text="Offline / Error", text_color=C["red"])
            self._emit_server_state(f"{srv_label}: Offline", "red")
            self._emit_status(f"{srv_label} connection failed: {exc}", log=True, level="ERROR")

        self._run_bg(self.client.test_connection, on_ok=ok, on_err=err)

    def _poll_loop(self) -> None:
        if self._shutting_down:
            return
        if self.server_url and self.server_token and not self._poll_in_flight:
            self._poll_sessions(silent=True)
        # Every 6 s while the tab is on screen; every 30 s in the background (the header pill
        # still stays reasonably current without hammering the server).
        try:
            visible = bool(self.winfo_viewable())
        except Exception:
            visible = False
        self._poll_after_id = self.after(6000 if visible else 30000, self._poll_loop)

    def _poll_sessions(self, silent: bool = False) -> None:
        if not (self.server_url and self.server_token) or self._poll_in_flight:
            return
        self._poll_in_flight = True
        srv_label = self.server_type

        def ok(sessions: list[dict]):
            C = self.colors
            self._poll_in_flight = False
            self._render_sessions(sessions)
            count = len(sessions)
            if count > 0:
                self.conn_badge.configure(text=f"{srv_label}: {count} streaming", text_color=C["green"])
                self._emit_server_state(f"{srv_label}: {count} Stream{'s' if count != 1 else ''}", "green")
            else:
                self.conn_badge.configure(text=f"{srv_label}: Online", text_color=C["green"])
                self._emit_server_state(f"{srv_label}: Online", "green")

        def err(exc):
            C = self.colors
            self._poll_in_flight = False
            self.conn_badge.configure(text="Offline", text_color=C["red"])
            self._emit_server_state(f"{srv_label}: Offline", "red")
            if not silent:
                self._emit_status(f"{srv_label} error: {exc}", log=True, level="ERROR")

        self._run_bg(self.client.get_sessions, on_ok=ok, on_err=err)

    def _render_sessions(self, sessions: list[dict]) -> None:
        C = self.colors
        for w in self.streams_frame.winfo_children():
            w.destroy()

        self.streams_title_lbl.configure(text=f"ACTIVE STREAMS ({len(sessions)})")
        if not sessions:
            ctk.CTkLabel(
                self.streams_frame,
                text="No active playback streams.",
                text_color=C["text_muted"],
            ).pack(pady=24)
            return

        for s in sessions:
            row = ctk.CTkFrame(self.streams_frame, fg_color=C["bg_card"], corner_radius=8, border_width=1, border_color=C["border"])
            row.pack(fill="x", padx=4, pady=3)

            if s.get("can_control"):
                sid = s["session_id"]
                ctk.CTkButton(
                    row,
                    text="Stop",
                    width=52,
                    height=24,
                    fg_color=C["red"],
                    hover_color="#DC2626",
                    command=lambda i=sid, who=s["user"], what=s["title"]: self._confirm_stop(i, who, what),
                ).pack(side="right", padx=(3, 8), pady=6)

                ctk.CTkButton(
                    row,
                    text="Play" if s["paused"] else "Pause",
                    width=56,
                    height=24,
                    fg_color=C["bg_panel"],
                    hover_color=C["border"],
                    command=lambda i=sid: self._run_bg(
                        lambda: self.client.control_session(i, "PlayPause"),
                        on_ok=lambda _r: self._poll_sessions(silent=True),
                    ),
                ).pack(side="right", padx=3, pady=6)

            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="both", expand=True, padx=8, pady=5)

            top = ctk.CTkFrame(info, fg_color="transparent")
            top.pack(fill="x")

            state_col = C["amber"] if s["paused"] else C["green"]
            ctk.CTkLabel(
                top,
                text="PAUSED" if s["paused"] else "PLAYING",
                fg_color=_tint(state_col),
                corner_radius=9,
                padx=6,
                pady=1,
                font=ctk.CTkFont(family=self.ui_font, size=9, weight="bold"),
                text_color=state_col,
            ).pack(side="left", padx=(0, 6))

            is_tc = "transcode" in s["play_method"].lower()
            ctk.CTkLabel(
                top,
                text=s["play_method"],
                fg_color=_tint(C["amber"] if is_tc else C["sky"]),
                corner_radius=9,
                padx=6,
                pady=1,
                font=ctk.CTkFont(family=self.ui_font, size=9),
                text_color=C["amber"] if is_tc else C["sky"],
            ).pack(side="left", padx=(0, 8))

            ctk.CTkLabel(
                top,
                text=f"{s['title']}   •   {s['user']} on {s['device']}",
                anchor="w",
                font=ctk.CTkFont(family=self.ui_font, size=11, weight="bold"),
                text_color=C["text_primary"],
            ).pack(side="left", fill="x", expand=True)

            ctk.CTkLabel(
                top,
                text=f"{s['elapsed']} / {s['total']} ({s['percentage']:.0f}%)",
                font=ctk.CTkFont(family=self.ui_font, size=10),
                text_color=C["text_secondary"],
            ).pack(side="right", padx=4)

            bar = ctk.CTkProgressBar(info, height=5, fg_color=C["bg_root"], progress_color=state_col)
            bar.set(min(max(s["percentage"] / 100.0, 0.0), 1.0))
            bar.pack(fill="x", pady=(4, 0))

    def _confirm_stop(self, session_id: str, user: str, title: str) -> None:
        """Stopping someone else's stream is disruptive, so ask first."""
        if not messagebox.askyesno("Stop stream", f"Stop {user}'s playback of\n\n{title}?", parent=self):
            return
        self._run_bg(
            lambda: self.client.control_session(session_id, "Stop"),
            on_ok=lambda _r: (
                self._emit_status(f"Stopped {user}'s stream: {title}", log=True, level="WARN"),
                self._poll_sessions(silent=True),
            ),
        )

    def _trigger_server_scan(self) -> None:
        if not (self.server_url and self.server_token):
            self._emit_status("Configure Media Server URL and API Key / Token first.")
            return
        srv = self.server_type
        self._run_bg(
            lambda: self.client.refresh_libraries(),
            on_ok=lambda _r: self._emit_status(f"Triggered {srv} library refresh.", log=True, level="OK"),
        )

    # ------------------------------------------------------------------
    # Library Sync & Disk Audit
    # ------------------------------------------------------------------
    def _sync_library(self) -> None:
        if not (self.server_url and self.server_token):
            self._emit_status("Enter URL and API Key / Token first.")
            return
        srv = self.server_type
        self._emit_status(f"Syncing {srv} Movies, TV Shows & running Disk Audit...", log=True)
        get_disk = self.get_disk_items
        client = self.client
        path_map_raw = self.path_map_raw

        def fetch():
            # Everything slow runs here, off the UI thread: the server API calls, reading the
            # disk file list (probe cache / exists() checks) and the audit itself.
            movies = client.get_movies()
            shows = client.get_tv_shows()
            episodes = client.get_episodes()
            disk_items = get_disk() if get_disk else []
            missing, orphaned = compute_audit(movies, episodes, disk_items, path_map_raw)
            return movies, shows, episodes, disk_items, missing, orphaned

        def ok(res):
            movies, shows, episodes, d_items, missing, orphaned = res
            self._movies = movies
            self._shows = shows
            self._episodes = episodes
            self._disk_items = d_items
            self._has_disk_items = bool(d_items)
            self._audit_missing, self._audit_orphaned = missing, orphaned
            self._apply_view_filter(reset_page=True)
            self._emit_status(
                f"{srv} synced: {len(movies)} movies, {len(shows)} TV shows ({len(episodes)} eps). "
                f"Audit: {len(self._audit_missing)} missing from server, {len(self._audit_orphaned)} orphaned.",
                log=True,
                level="OK",
            )

        self._run_bg(fetch, on_ok=ok)

    def _compute_audit(self, disk_items: list[dict]) -> None:
        """Synchronous audit (kept for callers/tests); the UI uses compute_audit() on a worker."""
        self._audit_missing, self._audit_orphaned = compute_audit(
            self._movies, self._episodes, disk_items, self.path_map_raw
        )
        self._has_disk_items = bool(disk_items)

    def _clear_filter(self, _event=None) -> None:
        self.filter_entry.delete(0, "end")
        self._apply_view_filter(reset_page=True)

    def _apply_view_filter(self, reset_page: bool = False) -> None:
        view = self.view_var.get()
        q = self.filter_entry.get().strip().lower()
        tokens = q.split() if q else []

        if view == "Movies":
            source = self._movies
            self.summary_lbl.configure(text=f"{len(source)} movie(s)")
        elif view == "TV Shows":
            source = self._shows
            self.summary_lbl.configure(text=f"{len(source)} show(s)")
        elif view == "Audit: Missing from Server":
            source = self._audit_missing
            self.summary_lbl.configure(text=f"{len(source)} file(s) not on server")
        else:
            source = self._audit_orphaned
            self.summary_lbl.configure(text=f"{len(source)} orphaned server item(s)")

        if tokens:
            filtered = [
                it for it in source
                if all(tok in f"{it.get('title', '')} {it.get('file', '')}".lower() for tok in tokens)
            ]
        else:
            filtered = list(source)

        self._filtered_rows = filtered
        if reset_page:
            self._current_page = 0

        total_pages = max(1, (len(filtered) + SERVER_PAGE_SIZE - 1) // SERVER_PAGE_SIZE)
        if self._current_page >= total_pages:
            self._current_page = total_pages - 1

        self.page_lbl.configure(text=f"Page {self._current_page + 1} / {total_pages}")
        self.prev_btn.configure(state="normal" if self._current_page > 0 else "disabled")
        self.next_btn.configure(state="normal" if (self._current_page + 1) < total_pages else "disabled")

        start = self._current_page * SERVER_PAGE_SIZE
        self._render_rows(view, filtered[start : start + SERVER_PAGE_SIZE])

    def _change_page(self, delta: int) -> None:
        total_pages = max(1, (len(self._filtered_rows) + SERVER_PAGE_SIZE - 1) // SERVER_PAGE_SIZE)
        new_page = max(0, min(total_pages - 1, self._current_page + delta))
        if new_page != self._current_page:
            self._current_page = new_page
            self._apply_view_filter(reset_page=False)

    def _render_rows(self, view: str, rows: list[dict]) -> None:
        C = self.colors
        for w in self.list_frame.winfo_children():
            w.destroy()

        try:
            canvas = getattr(self.list_frame, "_parent_canvas", None)
            if canvas is not None:
                canvas.yview_moveto(0.0)
        except Exception:
            pass

        if not rows:
            # Uses the flag from the last sync: calling get_disk_items() here re-read the probe
            # cache (and stat()ed every file) on the UI thread just to choose a message.
            if view.startswith("Audit:") and not self._has_disk_items:
                msg = "Run a scan in 'Media Inspector' first so the server can be compared against your disk files."
            elif not self._movies and not self._shows:
                msg = "Click 'Sync Library & Audit' above after connecting."
            else:
                msg = "No items in this view."
            ctk.CTkLabel(self.list_frame, text=msg, text_color=C["text_muted"]).pack(pady=36)
            return

        for it in rows:
            card = ctk.CTkFrame(
                self.list_frame,
                fg_color=C["bg_card"],
                corner_radius=8,
                border_width=1,
                border_color=C["border"],
            )
            card.pack(fill="x", padx=6, pady=4)

            if view == "Movies":
                title = f"{it.get('title', 'Unknown')} ({it.get('year', '')})".replace(" ()", "")
                watched = bool(it.get("watched"))
                self._render_card_left(
                    card,
                    title,
                    it.get("file") or "",
                    [("WATCHED" if watched else "UNWATCHED", C["green"] if watched else C["sky"])],
                )
            elif view == "TV Shows":
                title = f"{it.get('title', 'Unknown')} ({it.get('year', '')})".replace(" ()", "")
                eps = int(it.get("episodes") or 0)
                watched = int(it.get("watched_episodes") or 0)
                col = C["green"] if (eps > 0 and watched >= eps) else C["accent"]
                self._render_card_left(card, title, it.get("file") or "", [(f"{watched}/{eps} watched", col)])
            elif view == "Audit: Missing from Server":
                fpath = it.get("file") or ""
                if fpath and it.get("folder_exists", False):  # checked during the background audit
                    ctk.CTkButton(
                        card,
                        text="Open Folder",
                        width=96,
                        height=28,
                        fg_color=C["bg_panel"],
                        hover_color=C["border"],
                        command=lambda p=Path(fpath).parent: self._safe_open_dir(p),
                    ).pack(side="right", padx=(6, 10), pady=8)

                badges = [(it.get("kind", "Media").upper(), C["amber"])]
                badges.append(("CORRUPT", C["red"]) if it.get("corrupt") else ("NOT ON SERVER", C["amber"]))
                self._render_card_left(card, it.get("title", "Unknown"), fpath, badges)
            else:
                self._render_card_left(
                    card,
                    it.get("title", "Unknown"),
                    it.get("file") or "",
                    [(it.get("kind", "Media").upper(), C["sky"]), ("MISSING ON DISK", C["red"])],
                )

    def _render_card_left(self, card, title: str, subtitle: str, badges: list[tuple[str, str]]) -> None:
        C = self.colors
        info = ctk.CTkFrame(card, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, padx=(10, 6), pady=7)

        top = ctk.CTkFrame(info, fg_color="transparent")
        top.pack(fill="x")

        for b_txt, b_col in badges:
            ctk.CTkLabel(
                top,
                text=b_txt,
                fg_color=_tint(b_col),
                corner_radius=10,
                padx=7,
                pady=1,
                font=ctk.CTkFont(family=self.ui_font, size=10, weight="bold"),
                text_color=b_col,
            ).pack(side="left", padx=(0, 6))

        ctk.CTkLabel(
            top,
            text=title,
            anchor="w",
            font=ctk.CTkFont(family=self.ui_font, size=12, weight="bold"),
            text_color=C["text_primary"],
        ).pack(side="left", fill="x", expand=True)

        if subtitle:
            ctk.CTkLabel(
                info,
                text=subtitle,
                anchor="w",
                font=ctk.CTkFont(family=self.ui_font, size=10),
                text_color=C["text_muted"],
            ).pack(fill="x", pady=(3, 0))

    def _safe_open_dir(self, folder: Path) -> None:
        try:
            target = str(folder)
            if _SYS == "Windows":
                os.startfile(target)  # type: ignore[attr-defined]
            elif _SYS == "Darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
        except Exception as exc:
            self._emit_status(f"Could not open folder: {exc}", log=True, level="ERROR")