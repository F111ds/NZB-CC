# media_inspector.py
# Standalone Media Library Scanner & FFprobe Inspector Module
# Designed for NZB Command Center — Operations Center Edition
#
# Strictly read-only: never deletes, moves, or renames media files.

import json
import os
import platform
import queue
import functools
import re
import shutil
import subprocess
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk


VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv",
    ".ts", ".m2ts", ".webm", ".flv", ".mpg", ".mpeg", ".vob",
}

SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup"}

SKIP_DIR_NAMES = {
    "@eadir", ".trash", ".trashes", "$recycle.bin",
    "system volume information", ".git", "__pycache__",
}

MEDIA_PAGE_SIZE = 50

TYPE_FILTERS = ["All Media", "Movies", "TV Shows"]
QUALITY_FILTERS = ["All Qualities", "4K / 2160p", "1080p", "720p", "SD", "HDR / DV"]
HEALTH_FILTERS = ["All Statuses", "Healthy Only", "Duplicates Only", "Corrupt / Unreadable"]
SORT_MODES = [
    "Title (A–Z)",
    "Size: Largest First",
    "Size: Smallest First",
    "Resolution: Highest First",
    "Bitrate: Highest First",
    "Newest Modified",
]

SHORT_RES_LABELS = {
    0: "?",
    1: "SD",
    2: "720p",
    3: "1080p",
    4: "4K",
}

_SYS = platform.system()
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if _SYS == "Windows" else 0

BASE_DIR = Path(__file__).resolve().parent
MEDIA_CACHE_FILE = BASE_DIR / "media_inspector_cache.json"


# ==============================================================================
# 1. FORMATTING, CLASSIFICATION & LIBRARY MATCHING HELPERS
# ==============================================================================

def format_bytes(num_bytes: int | float) -> str:
    try:
        val = float(num_bytes)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024.0:
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024.0
    return f"{val:.1f} PB"


def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_bitrate(bps: int) -> str:
    if bps <= 0:
        return "—"
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.1f} Mbps"
    return f"{bps / 1_000:.0f} kbps"


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _open_folder_in_os(folder: Path) -> None:
    target = str(folder)
    if _SYS == "Windows":
        os.startfile(target)  # type: ignore[attr-defined]
    elif _SYS == "Darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


def find_ffprobe(explicit_path: str = "") -> str:
    if explicit_path:
        p = Path(explicit_path.strip().strip('"'))
        if p.is_file():
            return str(p)

    found = shutil.which("ffprobe")
    if found:
        return found

    if _SYS == "Windows":
        candidates = [
            Path("C:/ffmpeg/bin/ffprobe.exe"),
            Path("C:/Program Files/ffmpeg/bin/ffprobe.exe"),
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Links/ffprobe.exe",
        ]
        for cand in candidates:
            if cand.is_file():
                return str(cand)
    elif _SYS == "Darwin":
        for cand in (Path("/opt/homebrew/bin/ffprobe"), Path("/usr/local/bin/ffprobe")):
            if cand.is_file():
                return str(cand)
    else:
        for cand in (Path("/usr/bin/ffprobe"), Path("/usr/local/bin/ffprobe")):
            if cand.is_file():
                return str(cand)

    return ""


def detect_resolution_from_name(name: str) -> tuple[str, int]:
    t = name or ""
    if re.search(r"\b(2160p|4k|uhd)\b", t, re.I):
        return "4K / 2160p", 4
    if re.search(r"\b1080p\b", t, re.I):
        return "1080p", 3
    if re.search(r"\b720p\b", t, re.I):
        return "720p", 2
    if re.search(r"\b(480p|576p|dvdrip|xvid|divx|sdtv)\b", t, re.I):
        return "SD", 1
    return "Unknown", 0


def detect_hdr_from_name(name: str) -> bool:
    return bool(re.search(r"\b(dv|dovi|dolby[.\s_-]?vision|hdr10\+|hdr10|hdr|hlg)\b", name or "", re.I))


def parse_release_identity(
    raw_name: str,
    folder_parts: tuple[str, ...] = (),
    category_hint: str = "",
    fallback_parent_name: str = "",
) -> dict:
    # Shared parser used for both local media files and indexer search results.
    stem = (raw_name or "").strip()
    parent_low = [p.lower() for p in folder_parts]

    se_match = re.search(r"\bS(\d{1,2})[\s._-]?E(\d{1,3})(?:[\s._-]?E\d{1,3})*\b", stem, re.I)
    alt_ep_match = re.search(r"\b(\d{1,2})x(\d{2,3})\b", stem, re.I) if not se_match else None
    season_only_match = (
        re.search(r"\b(?:S(\d{1,2})|Season[\s._-]?(\d{1,2}))\b", stem, re.I)
        if not (se_match or alt_ep_match)
        else None
    )
    tv_folder_hint = any(
        re.search(r"\b(tv|tv[\s._-]?shows?|series|episodes?|season[\s._-]?\d+)\b", part, re.I)
        for part in parent_low[-3:]
    )
    tv_cat_hint = bool(re.search(r"\b(tv|series|anime)\b", category_hint or "", re.I))

    is_tv = bool(se_match or alt_ep_match or season_only_match or tv_folder_hint or tv_cat_hint)

    raw_source = stem
    if not is_tv and fallback_parent_name and re.fullmatch(r"(movie|film|video|bdmv|index|\d{4,5})", stem, re.I):
        raw_source = fallback_parent_name

    cleaned = raw_source.replace(".", " ").replace("_", " ").strip()

    if is_tv:
        season_num: int | None = None
        ep_num: int | None = None
        cut_pos = len(cleaned)
        ep_label = ""

        if se_match:
            season_num = int(se_match.group(1))
            ep_num = int(se_match.group(2))
            ep_label = f"S{season_num:02d}E{ep_num:02d}"
            m_clean = re.search(r"\bS\d{1,2}[\s._-]?E\d{1,3}(?:[\s._-]?E\d{1,3})*\b", cleaned, re.I)
            if m_clean:
                cut_pos = m_clean.start()
        elif alt_ep_match:
            season_num = int(alt_ep_match.group(1))
            ep_num = int(alt_ep_match.group(2))
            ep_label = f"S{season_num:02d}E{ep_num:02d}"
            m_clean = re.search(r"\b\d{1,2}x\d{2,3}\b", cleaned, re.I)
            if m_clean:
                cut_pos = m_clean.start()
        elif season_only_match:
            s_val = season_only_match.group(1) or season_only_match.group(2)
            season_num = int(s_val) if s_val else None
            if season_num is not None:
                ep_label = f"S{season_num:02d}"
            m_clean = re.search(r"\b(?:S\d{1,2}|Season[\s._-]?\d{1,2})\b", cleaned, re.I)
            if m_clean:
                cut_pos = m_clean.start()

        show_prefix = cleaned[:cut_pos].strip(" -([")
        if not show_prefix and fallback_parent_name:
            # e.g. file inside ".../Show Name/Season 01/..."
            show_prefix = fallback_parent_name.replace(".", " ").replace("_", " ").strip()
            if re.fullmatch(r"season[\s._-]?\d+", show_prefix, re.I) and len(folder_parts) >= 2:
                show_prefix = folder_parts[-2].replace(".", " ").replace("_", " ").strip()

        show_noyear = re.sub(r"\b(19\d\d|20\d\d)\b", "", show_prefix).strip(" -([")
        norm_show = re.sub(r"[^a-z0-9]+", "", show_prefix.lower())
        norm_show_noyear = re.sub(r"[^a-z0-9]+", "", show_noyear.lower()) or norm_show

        display_title = f"{show_prefix} — {ep_label}".strip(" —") if (show_prefix and ep_label) else (cleaned or stem)
        tv_ep_key = f"tv::{norm_show}::s{season_num:02d}e{ep_num:02d}" if (norm_show and season_num is not None and ep_num is not None) else ""
        tv_ep_noyear_key = (
            f"tv::{norm_show_noyear}::s{season_num:02d}e{ep_num:02d}"
            if (norm_show_noyear and season_num is not None and ep_num is not None)
            else ""
        )

        return {
            "media_type": "TV",
            "display_title": display_title,
            "year": "",
            "movie_key": "",
            "movie_noyear_key": "",
            "tv_show_key": norm_show,
            "tv_show_noyear_key": norm_show_noyear,
            "season": season_num,
            "episode": ep_num,
            "tv_ep_key": tv_ep_key,
            "tv_ep_noyear_key": tv_ep_noyear_key,
        }

    # Movie title & year extraction
    part_match = re.search(r"\b(part[\s._-]?\d+|cd[\s._-]?\d+|disc[\s._-]?\d+)\b", cleaned, re.I)
    part_token = re.sub(r"[\s._-]+", "", part_match.group(1).lower()) if part_match else ""

    year = ""
    year_matches = list(re.finditer(r"\b(19\d\d|20\d\d)\b", cleaned))
    cutoff_idx = len(cleaned)
    if year_matches:
        ym = year_matches[-1]
        year = ym.group(1)
        cutoff_idx = ym.start()

    tag_cut = re.search(
        r"\b(2160p|1080p|720p|480p|576p|4k|uhd|bluray|bdrip|brrip|web-dl|webdl|webrip|hdtv|hdr|hdr10|dv|dovi|x264|x265|h264|h265|hevc|av1|remux|proper|repack)\b",
        cleaned,
        re.I,
    )
    if tag_cut and tag_cut.start() < cutoff_idx:
        cutoff_idx = tag_cut.start()

    base_title = cleaned[:cutoff_idx].strip(" -([.")
    if not base_title:
        base_title = cleaned
        year = ""

    norm_title = re.sub(r"[^a-z0-9]+", "", base_title.lower())
    display_title = f"{base_title} ({year})" if year else base_title
    if part_token:
        display_title = f"{display_title} [{part_token.upper()}]"

    movie_key = f"{norm_title}::{year}::{part_token}" if norm_title else ""
    movie_noyear_key = f"{norm_title}::::{part_token}" if norm_title else ""

    return {
        "media_type": "Movie",
        "display_title": display_title or stem,
        "year": year,
        "movie_key": movie_key,
        "movie_noyear_key": movie_noyear_key,
        "tv_show_key": "",
        "tv_show_noyear_key": "",
        "season": None,
        "episode": None,
        "tv_ep_key": "",
        "tv_ep_noyear_key": "",
    }


def _classify_and_parse_name(file_path: Path) -> dict:
    return parse_release_identity(
        file_path.stem,
        folder_parts=file_path.parts[:-1],
        fallback_parent_name=file_path.parent.name,
    )


@functools.lru_cache(maxsize=4096)
def _subtitle_files_in(folder: str) -> tuple[str, ...]:
    """Subtitle file names in a folder. Cached per scan (cleared when a scan starts): a folder
    with 2,000 videos used to be listed 2,000 times, once per video."""
    try:
        return tuple(
            e.name for e in os.scandir(folder)
            if e.is_file() and os.path.splitext(e.name)[1].lower() in SUBTITLE_EXTENSIONS
        )
    except OSError:
        return ()


def _find_external_subtitles(video_path: Path) -> list[dict]:
    subs: list[dict] = []
    stem_low = video_path.stem.lower()
    try:
        for name in _subtitle_files_in(str(video_path.parent)):
            entry = video_path.parent / name
            ext = entry.suffix.lower()
            if ext not in SUBTITLE_EXTENSIONS:
                continue
            name_low = entry.stem.lower()
            if name_low == stem_low or name_low.startswith(stem_low + "."):
                suffix_lang = entry.stem[len(video_path.stem) :].lstrip("._- ")
                subs.append({
                    "codec": ext.lstrip(".").upper(),
                    "language": suffix_lang or "ext",
                    "title": entry.name,
                    "external": True,
                    "default": False,
                    "forced": "forced" in name_low,
                })
    except OSError:
        pass
    return subs


def _resolution_bucket(width: int, height: int) -> tuple[str, int]:
    if width <= 0 or height <= 0:
        return "Unknown", 0
    if width >= 3800 or height >= 2000:
        return "4K / 2160p", 4
    if width >= 1800 or height >= 1000:
        return "1080p", 3
    if width >= 1200 or height >= 680:
        return "720p", 2
    return "SD", 1


def _normalize_codec(codec_name: str) -> str:
    c = (codec_name or "").strip().lower()
    mapping = {
        "hevc": "HEVC (H.265)",
        "h265": "HEVC (H.265)",
        "h264": "H.264 (AVC)",
        "avc": "H.264 (AVC)",
        "av1": "AV1",
        "vp9": "VP9",
        "mpeg2video": "MPEG-2",
        "mpeg4": "MPEG-4",
        "vc1": "VC-1",
    }
    return mapping.get(c, c.upper() if c else "Unknown")


def _detect_bit_depth(stream: dict) -> str:
    raw_bits = str(stream.get("bits_per_raw_sample") or "").strip()
    if raw_bits.isdigit() and int(raw_bits) > 0:
        return f"{raw_bits}-bit"
    pix = str(stream.get("pix_fmt") or "").lower()
    if "12le" in pix or "12be" in pix:
        return "12-bit"
    if "10le" in pix or "10be" in pix or "p010" in pix:
        return "10-bit"
    return "8-bit" if pix else ""


def _detect_hdr(stream: dict) -> str:
    transfer = str(stream.get("color_transfer") or "").lower()
    primaries = str(stream.get("color_primaries") or "").lower()
    codec_tag = str(stream.get("codec_tag_string") or "").lower()
    side_data = stream.get("side_data_list") or []

    has_dovi = codec_tag in ("dvhe", "dvh1", "dav1")
    has_hdr10_plus = False
    has_master_display = False

    for sd in side_data:
        if not isinstance(sd, dict):
            continue
        stype = str(sd.get("side_data_type") or "").lower()
        if "dovi" in stype or "dolby vision" in stype:
            has_dovi = True
        elif "hdr10+" in stype or "dynamic hdr10+" in stype:
            has_hdr10_plus = True
        elif "mastering display" in stype or "content light level" in stype:
            has_master_display = True

    is_pq = transfer == "smpte2084"
    is_hlg = transfer == "arib-std-b67"
    is_bt2020 = "bt2020" in primaries

    labels: list[str] = []
    if has_dovi:
        labels.append("Dolby Vision")
    if has_hdr10_plus:
        labels.append("HDR10+")
    elif is_pq and (is_bt2020 or has_master_display):
        labels.append("HDR10")
    elif is_hlg:
        labels.append("HLG")

    if not labels:
        return "SDR"
    return " + ".join(labels)


def _format_channels(stream: dict) -> str:
    layout = str(stream.get("channel_layout") or "").strip()
    ch = stream.get("channels")
    if layout:
        return layout.replace("(side)", "").strip()
    if ch == 8:
        return "7.1"
    if ch == 6:
        return "5.1"
    if ch == 2:
        return "2.0"
    if ch == 1:
        return "1.0"
    return f"{ch}ch" if ch else ""


# ==============================================================================
# 2. DATA MODEL, PROBE ENGINE & CROSS-TAB LIBRARY INDEX
# ==============================================================================

@dataclass
class MediaItem:
    path: Path
    filename: str
    folder: str
    media_type: str
    display_title: str
    year: str
    movie_key: str
    size_bytes: int
    mtime_ns: int
    container: str = ""
    duration_sec: float = 0.0
    total_bitrate: int = 0
    video_bitrate: int = 0
    width: int = 0
    height: int = 0
    resolution_label: str = "Unknown"
    resolution_rank: int = 0
    video_codec: str = "Unknown"
    video_profile: str = ""
    bit_depth: str = ""
    fps: str = ""
    hdr_format: str = "Unknown"
    audio_tracks: list[dict] = field(default_factory=list)
    subtitle_tracks: list[dict] = field(default_factory=list)
    external_subtitles: list[dict] = field(default_factory=list)
    probed: bool = False
    is_corrupt: bool = False
    error_reason: str = ""
    is_duplicate: bool = False
    duplicate_count: int = 0
    movie_noyear_key: str = ""
    tv_show_key: str = ""
    tv_show_noyear_key: str = ""
    season: int | None = None
    episode: int | None = None
    tv_ep_key: str = ""
    tv_ep_noyear_key: str = ""

    @property
    def is_hdr(self) -> bool:
        return self.hdr_format not in ("", "SDR", "Unknown")

    def effective_rank(self) -> int:
        if self.resolution_rank > 0:
            return self.resolution_rank
        return detect_resolution_from_name(self.filename)[1]

    def matches_text(self, query_tokens: list[str]) -> bool:
        if not query_tokens:
            return True
        audio_txt = " ".join(
            f"{a.get('codec', '')} {a.get('language', '')} {a.get('title', '')}"
            for a in self.audio_tracks
        )
        sub_txt = " ".join(
            f"{s.get('language', '')} {s.get('codec', '')}"
            for s in (self.subtitle_tracks + self.external_subtitles)
        )
        haystack = " ".join([
            self.display_title,
            self.filename,
            self.folder,
            self.video_codec,
            self.resolution_label,
            self.hdr_format,
            audio_txt,
            sub_txt,
        ]).lower()
        return all(tok in haystack for tok in query_tokens)


def _apply_parsed_identity(item: MediaItem, parsed: dict) -> MediaItem:
    item.movie_noyear_key = parsed.get("movie_noyear_key", "")
    item.tv_show_key = parsed.get("tv_show_key", "")
    item.tv_show_noyear_key = parsed.get("tv_show_noyear_key", "")
    item.season = parsed.get("season")
    item.episode = parsed.get("episode")
    item.tv_ep_key = parsed.get("tv_ep_key", "")
    item.tv_ep_noyear_key = parsed.get("tv_ep_noyear_key", "")
    return item


def probe_media_file(file_path: Path, ffprobe_exe: str, cache_entry: dict | None = None) -> tuple[MediaItem, dict | None]:
    parsed = _classify_and_parse_name(file_path)
    try:
        st = file_path.stat()
        size_bytes = st.st_size
        mtime_ns = st.st_mtime_ns
    except OSError as exc:
        item = MediaItem(
            path=file_path,
            filename=file_path.name,
            folder=str(file_path.parent),
            media_type=parsed["media_type"],
            display_title=parsed["display_title"],
            year=parsed["year"],
            movie_key=parsed["movie_key"],
            size_bytes=0,
            mtime_ns=0,
            is_corrupt=True,
            error_reason=f"Unreadable file: {exc}",
        )
        return _apply_parsed_identity(item, parsed), None

    ext_subs = _find_external_subtitles(file_path)

    if size_bytes == 0:
        item = MediaItem(
            path=file_path,
            filename=file_path.name,
            folder=str(file_path.parent),
            media_type=parsed["media_type"],
            display_title=parsed["display_title"],
            year=parsed["year"],
            movie_key=parsed["movie_key"],
            size_bytes=0,
            mtime_ns=mtime_ns,
            external_subtitles=ext_subs,
            is_corrupt=True,
            error_reason="0-byte empty file.",
        )
        return _apply_parsed_identity(item, parsed), None

    try:
        with file_path.open("rb") as fh:
            fh.read(64)
    except OSError as exc:
        item = MediaItem(
            path=file_path,
            filename=file_path.name,
            folder=str(file_path.parent),
            media_type=parsed["media_type"],
            display_title=parsed["display_title"],
            year=parsed["year"],
            movie_key=parsed["movie_key"],
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            external_subtitles=ext_subs,
            is_corrupt=True,
            error_reason=f"Read error: {exc}",
        )
        return _apply_parsed_identity(item, parsed), None

    if not ffprobe_exe:
        res_lbl, res_rank = detect_resolution_from_name(file_path.name)
        item = MediaItem(
            path=file_path,
            filename=file_path.name,
            folder=str(file_path.parent),
            media_type=parsed["media_type"],
            display_title=parsed["display_title"],
            year=parsed["year"],
            movie_key=parsed["movie_key"],
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            container=file_path.suffix.lstrip(".").upper(),
            resolution_label=res_lbl,
            resolution_rank=res_rank,
            external_subtitles=ext_subs,
            probed=False,
        )
        return _apply_parsed_identity(item, parsed), None

    if (
        cache_entry
        and cache_entry.get("size_bytes") == size_bytes
        and cache_entry.get("mtime_ns") == mtime_ns
        and isinstance(cache_entry.get("data"), dict)
    ):
        d = cache_entry["data"]
        item = MediaItem(
            path=file_path,
            filename=file_path.name,
            folder=str(file_path.parent),
            media_type=parsed["media_type"],
            display_title=parsed["display_title"],
            year=parsed["year"],
            movie_key=parsed["movie_key"],
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            container=d.get("container", ""),
            duration_sec=float(d.get("duration_sec", 0.0)),
            total_bitrate=int(d.get("total_bitrate", 0)),
            video_bitrate=int(d.get("video_bitrate", 0)),
            width=int(d.get("width", 0)),
            height=int(d.get("height", 0)),
            resolution_label=d.get("resolution_label", "Unknown"),
            resolution_rank=int(d.get("resolution_rank", 0)),
            video_codec=d.get("video_codec", "Unknown"),
            video_profile=d.get("video_profile", ""),
            bit_depth=d.get("bit_depth", ""),
            fps=d.get("fps", ""),
            hdr_format=d.get("hdr_format", "Unknown"),
            audio_tracks=list(d.get("audio_tracks", [])),
            subtitle_tracks=list(d.get("subtitle_tracks", [])),
            external_subtitles=ext_subs,
            probed=True,
            is_corrupt=bool(d.get("is_corrupt", False)),
            error_reason=d.get("error_reason", ""),
        )
        return _apply_parsed_identity(item, parsed), cache_entry

    cmd = [
        ffprobe_exe,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(file_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,  # big files on a NAS or a disk that has to spin up can take a while
            creationflags=_CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        # Slow is not broken: report it, but don't call the file corrupt (it isn't cached, so the
        # next scan tries again).
        item = MediaItem(
            path=file_path,
            filename=file_path.name,
            folder=str(file_path.parent),
            media_type=parsed["media_type"],
            display_title=parsed["display_title"],
            year=parsed["year"],
            movie_key=parsed["movie_key"],
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            external_subtitles=ext_subs,
            probed=True,
            is_corrupt=False,
            error_reason="Not checked: ffprobe took longer than 60 s (slow disk or network share).",
        )
        return _apply_parsed_identity(item, parsed), None
    except Exception as exc:
        item = MediaItem(
            path=file_path,
            filename=file_path.name,
            folder=str(file_path.parent),
            media_type=parsed["media_type"],
            display_title=parsed["display_title"],
            year=parsed["year"],
            movie_key=parsed["movie_key"],
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            external_subtitles=ext_subs,
            probed=True,
            is_corrupt=False,  # ffprobe couldn't run: says nothing about the file
            error_reason=f"Not checked: ffprobe couldn't run ({exc}).",
        )
        return _apply_parsed_identity(item, parsed), None

    if proc.returncode != 0 or not proc.stdout.strip():
        err_detail = (proc.stderr or "").strip().splitlines()
        msg = err_detail[-1] if err_detail else f"ffprobe exited with code {proc.returncode}"
        item = MediaItem(
            path=file_path,
            filename=file_path.name,
            folder=str(file_path.parent),
            media_type=parsed["media_type"],
            display_title=parsed["display_title"],
            year=parsed["year"],
            movie_key=parsed["movie_key"],
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            container=file_path.suffix.lstrip(".").upper(),
            external_subtitles=ext_subs,
            probed=True,
            is_corrupt=True,
            error_reason=msg[:280],
        )
        return _apply_parsed_identity(item, parsed), None

    try:
        probe_data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        item = MediaItem(
            path=file_path,
            filename=file_path.name,
            folder=str(file_path.parent),
            media_type=parsed["media_type"],
            display_title=parsed["display_title"],
            year=parsed["year"],
            movie_key=parsed["movie_key"],
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            external_subtitles=ext_subs,
            probed=True,
            is_corrupt=True,
            error_reason=f"Corrupt ffprobe JSON: {exc}",
        )
        return _apply_parsed_identity(item, parsed), None

    fmt = probe_data.get("format") or {}
    streams = probe_data.get("streams") or []

    container = str(fmt.get("format_name") or file_path.suffix.lstrip(".")).split(",")[0].upper()
    try:
        duration_sec = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration_sec = 0.0
    try:
        total_bitrate = int(float(fmt.get("bit_rate") or 0))
    except (TypeError, ValueError):
        total_bitrate = 0

    video_streams = [s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    sub_streams = [s for s in streams if s.get("codec_type") == "subtitle"]

    is_corrupt = False
    error_reason = ""
    width = 0
    height = 0
    res_label = "Unknown"
    res_rank = 0
    v_codec = "None"
    v_profile = ""
    bit_depth = ""
    fps_str = ""
    hdr_fmt = "Unknown"
    v_bitrate = 0

    if not video_streams:
        is_corrupt = True
        error_reason = "No valid video stream found in file."
    else:
        vs = video_streams[0]
        width = int(vs.get("width") or 0)
        height = int(vs.get("height") or 0)
        res_label, res_rank = _resolution_bucket(width, height)
        v_codec = _normalize_codec(vs.get("codec_name", ""))
        v_profile = str(vs.get("profile") or "")
        bit_depth = _detect_bit_depth(vs)
        hdr_fmt = _detect_hdr(vs)

        try:
            v_bitrate = int(float(vs.get("bit_rate") or 0))
        except (TypeError, ValueError):
            v_bitrate = 0

        if total_bitrate == 0 and duration_sec > 0:
            total_bitrate = int((size_bytes * 8) / duration_sec)

        r_fps = str(vs.get("avg_frame_rate") or vs.get("r_frame_rate") or "")
        if "/" in r_fps:
            num_s, den_s = r_fps.split("/", 1)
            try:
                num_v, den_v = float(num_s), float(den_s)
                if den_v > 0 and num_v > 0:
                    fps_str = f"{num_v / den_v:.3f}".rstrip("0").rstrip(".")
            except ValueError:
                pass

        if width <= 0 or height <= 0:
            is_corrupt = True
            error_reason = "Video stream has invalid 0x0 frame dimensions."
        elif duration_sec <= 0 and file_path.suffix.lower() not in (".ts", ".m2ts", ".vob"):
            is_corrupt = True
            error_reason = "Container reports 0s / unreadable duration."

    if not is_corrupt and proc.stderr and "moov atom not found" in proc.stderr.lower():
        is_corrupt = True
        error_reason = "Missing moov atom (truncated or corrupt MP4/MOV)."

    audio_list: list[dict] = []
    for idx, a_s in enumerate(audio_streams, 1):
        tags = a_s.get("tags") or {}
        disp = a_s.get("disposition") or {}
        audio_list.append({
            "index": idx,
            "codec": str(a_s.get("codec_name") or "?").upper(),
            "profile": str(a_s.get("profile") or ""),
            "channels": _format_channels(a_s),
            "language": str(tags.get("language") or "und"),
            "title": str(tags.get("title") or ""),
            "default": bool(disp.get("default")),
            "forced": bool(disp.get("forced")),
        })

    sub_list: list[dict] = []
    for idx, s_s in enumerate(sub_streams, 1):
        tags = s_s.get("tags") or {}
        disp = s_s.get("disposition") or {}
        cname = str(s_s.get("codec_name") or "?").lower()
        cname = {"subrip": "SRT", "hdmv_pgs_subtitle": "PGS", "dvd_subtitle": "VOBSUB"}.get(cname, cname.upper())
        sub_list.append({
            "index": idx,
            "codec": cname,
            "language": str(tags.get("language") or "und"),
            "title": str(tags.get("title") or ""),
            "external": False,
            "default": bool(disp.get("default")),
            "forced": bool(disp.get("forced")),
        })

    cache_payload = {
        "size_bytes": size_bytes,
        "mtime_ns": mtime_ns,
        "data": {
            "container": container,
            "duration_sec": duration_sec,
            "total_bitrate": total_bitrate,
            "video_bitrate": v_bitrate,
            "width": width,
            "height": height,
            "resolution_label": res_label,
            "resolution_rank": res_rank,
            "video_codec": v_codec,
            "video_profile": v_profile,
            "bit_depth": bit_depth,
            "fps": fps_str,
            "hdr_format": hdr_fmt,
            "audio_tracks": audio_list,
            "subtitle_tracks": sub_list,
            "is_corrupt": is_corrupt,
            "error_reason": error_reason,
        },
    }

    item = MediaItem(
        path=file_path,
        filename=file_path.name,
        folder=str(file_path.parent),
        media_type=parsed["media_type"],
        display_title=parsed["display_title"],
        year=parsed["year"],
        movie_key=parsed["movie_key"],
        size_bytes=size_bytes,
        mtime_ns=mtime_ns,
        container=container,
        duration_sec=duration_sec,
        total_bitrate=total_bitrate,
        video_bitrate=v_bitrate,
        width=width,
        height=height,
        resolution_label=res_label,
        resolution_rank=res_rank,
        video_codec=v_codec,
        video_profile=v_profile,
        bit_depth=bit_depth,
        fps=fps_str,
        hdr_format=hdr_fmt,
        audio_tracks=audio_list,
        subtitle_tracks=sub_list,
        external_subtitles=ext_subs,
        probed=True,
        is_corrupt=is_corrupt,
        error_reason=error_reason,
    )
    return _apply_parsed_identity(item, parsed), cache_payload


def duplicate_key(it: "MediaItem") -> str:
    """Identity used for duplicate detection: movie title+year(+part), or show+SxxExx."""
    if it.media_type == "Movie":
        return it.movie_key
    if it.media_type == "TV":
        return it.tv_ep_key
    return ""


def annotate_duplicate_movies(items: list[MediaItem]) -> dict[str, list[MediaItem]]:
    """Marks duplicate movies AND duplicate TV episodes (name kept for compatibility)."""
    groups: dict[str, list[MediaItem]] = defaultdict(list)
    for it in items:
        it.is_duplicate = False
        it.duplicate_count = 0
        key = duplicate_key(it)
        if key:
            groups[key].append(it)

    dup_groups: dict[str, list[MediaItem]] = {}
    for key, bucket in groups.items():
        if len(bucket) > 1:
            dup_groups[key] = bucket
            for it in bucket:
                it.is_duplicate = True
                it.duplicate_count = len(bucket)
    return dup_groups


def build_library_index(items: list[MediaItem]) -> dict:
    # Indexes scanned MediaItems for fast cross-tab lookups from Indexer Search.
    movies_by_key: dict[str, list[MediaItem]] = defaultdict(list)
    movies_by_noyear: dict[str, list[MediaItem]] = defaultdict(list)
    tv_eps: dict[str, list[MediaItem]] = defaultdict(list)
    tv_seasons: dict[tuple[str, int], list[MediaItem]] = defaultdict(list)

    for it in items:
        if it.media_type == "Movie":
            if it.movie_key:
                movies_by_key[it.movie_key].append(it)
            if it.movie_noyear_key:
                movies_by_noyear[it.movie_noyear_key].append(it)
        elif it.media_type == "TV":
            if it.tv_ep_key:
                tv_eps[it.tv_ep_key].append(it)
            if it.tv_ep_noyear_key and it.tv_ep_noyear_key != it.tv_ep_key:
                tv_eps[it.tv_ep_noyear_key].append(it)
            if it.tv_show_key and it.season is not None:
                tv_seasons[(it.tv_show_key, it.season)].append(it)
            if it.tv_show_noyear_key and it.tv_show_noyear_key != it.tv_show_key and it.season is not None:
                tv_seasons[(it.tv_show_noyear_key, it.season)].append(it)

    return {
        "total_count": len(items),
        "movies_by_key": dict(movies_by_key),
        "movies_by_noyear": dict(movies_by_noyear),
        "tv_eps": dict(tv_eps),
        "tv_seasons": dict(tv_seasons),
    }


def build_library_index_from_cache(
    cache_path: Path = MEDIA_CACHE_FILE,
    allowed_folders: list[str] | None = None,
) -> dict:
    # Rebuilds a LibraryIndex immediately from cached probe metadata for configured folders.
    if not cache_path.exists():
        return build_library_index([])
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return build_library_index([])
    except Exception:
        return build_library_index([])

    norm_roots: list[str] = []
    for folder in (allowed_folders or []):
        f_str = str(Path(folder))
        norm_roots.append(f_str.lower() if _SYS == "Windows" else f_str)

    items: list[MediaItem] = []
    for path_str, entry in raw.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("data"), dict):
            continue
        if norm_roots:
            cmp_path = path_str.lower() if _SYS == "Windows" else path_str
            if not any(cmp_path.startswith(root) for root in norm_roots):
                continue
        p = Path(path_str)
        if not p.exists():
            continue

        d = entry["data"]
        parsed = _classify_and_parse_name(p)
        item = MediaItem(
            path=p,
            filename=p.name,
            folder=str(p.parent),
            media_type=parsed["media_type"],
            display_title=parsed["display_title"],
            year=parsed["year"],
            movie_key=parsed["movie_key"],
            size_bytes=int(entry.get("size_bytes") or 0),
            mtime_ns=int(entry.get("mtime_ns") or 0),
            container=str(d.get("container") or ""),
            duration_sec=float(d.get("duration_sec") or 0.0),
            total_bitrate=int(d.get("total_bitrate") or 0),
            video_bitrate=int(d.get("video_bitrate") or 0),
            width=int(d.get("width") or 0),
            height=int(d.get("height") or 0),
            resolution_label=str(d.get("resolution_label") or "Unknown"),
            resolution_rank=int(d.get("resolution_rank") or 0),
            video_codec=str(d.get("video_codec") or "Unknown"),
            video_profile=str(d.get("video_profile") or ""),
            bit_depth=str(d.get("bit_depth") or ""),
            fps=str(d.get("fps") or ""),
            hdr_format=str(d.get("hdr_format") or "Unknown"),
            audio_tracks=list(d.get("audio_tracks") or []),
            subtitle_tracks=list(d.get("subtitle_tracks") or []),
            probed=True,
            is_corrupt=bool(d.get("is_corrupt", False)),
            error_reason=str(d.get("error_reason") or ""),
        )
        items.append(_apply_parsed_identity(item, parsed))

    return build_library_index(items)


def match_release_to_library(title: str, library_index: dict | None, category: str = "") -> dict | None:
    # Compares an indexer release title against the local library index.
    if not library_index or library_index.get("total_count", 0) == 0:
        return None

    ident = parse_release_identity(title, category_hint=category)
    matched: list[MediaItem] = []
    season_pack_mode = False

    if ident["media_type"] == "TV":
        tv_eps = library_index.get("tv_eps", {})
        if ident["tv_ep_key"] and ident["tv_ep_key"] in tv_eps:
            matched = tv_eps[ident["tv_ep_key"]]
        elif ident["tv_ep_noyear_key"] and ident["tv_ep_noyear_key"] in tv_eps:
            matched = tv_eps[ident["tv_ep_noyear_key"]]
        elif ident["episode"] is None and ident["season"] is not None:
            tv_seasons = library_index.get("tv_seasons", {})
            if (ident["tv_show_key"], ident["season"]) in tv_seasons:
                matched = tv_seasons[(ident["tv_show_key"], ident["season"])]
                season_pack_mode = True
            elif (ident["tv_show_noyear_key"], ident["season"]) in tv_seasons:
                matched = tv_seasons[(ident["tv_show_noyear_key"], ident["season"])]
                season_pack_mode = True
    else:
        m_by_key = library_index.get("movies_by_key", {})
        m_noyear = library_index.get("movies_by_noyear", {})
        if ident["movie_key"] and ident["movie_key"] in m_by_key:
            matched = m_by_key[ident["movie_key"]]
        elif not ident["year"] and ident["movie_noyear_key"] in m_noyear:
            cands = m_noyear[ident["movie_noyear_key"]]
            distinct_years = {c.year for c in cands if c.year}
            if len(distinct_years) <= 1:
                matched = cands

    if not matched:
        return None

    healthy = [m for m in matched if not m.is_corrupt]
    if not healthy:
        return {
            "status": "corrupt",
            "badge": "CORRUPT ON DISK",
            "color_key": "red",
            "summary": f"Corrupt file on disk: {matched[0].filename} ({matched[0].error_reason or 'unreadable'})",
        }

    best = max(healthy, key=lambda m: (m.effective_rank(), int(m.is_hdr), m.size_bytes))
    lib_rank = best.effective_rank()
    lib_short = SHORT_RES_LABELS.get(lib_rank, "?")
    lib_hdr = best.is_hdr

    _s_lbl, search_rank = detect_resolution_from_name(title)
    search_short = SHORT_RES_LABELS.get(search_rank, "?")
    search_hdr = detect_hdr_from_name(title)

    best_desc = f"{best.filename} ({lib_short} {best.video_codec}, {format_bytes(best.size_bytes)})"

    if season_pack_mode:
        ep_count = len({m.episode for m in healthy if m.episode is not None}) or len(healthy)
        s_num = ident["season"]
        if search_rank > lib_rank and lib_rank > 0:
            return {
                "status": "upgrade",
                "badge": f"UPGRADE S{s_num:02d} ({lib_short} → {search_short})",
                "color_key": "purple",
                "summary": f"Have {ep_count} ep(s) in S{s_num:02d} up to {lib_short} — e.g. {best_desc}",
            }
        return {
            "status": "owned",
            "badge": f"IN LIBRARY (S{s_num:02d}: {ep_count} eps)",
            "color_key": "green",
            "summary": f"Have {ep_count} ep(s) in S{s_num:02d} ({lib_short}) — e.g. {best_desc}",
        }

    if search_rank == 0 or lib_rank == 0:
        return {
            "status": "owned",
            "badge": f"IN LIBRARY ({lib_short})" if lib_rank > 0 else "IN LIBRARY",
            "color_key": "green",
            "summary": f"On disk: {best_desc}",
        }

    if search_rank > lib_rank:
        return {
            "status": "upgrade",
            "badge": f"UPGRADE ({lib_short} → {search_short})",
            "color_key": "purple",
            "summary": f"On disk: {best_desc}",
        }

    if search_rank == lib_rank and search_hdr and best.probed and not lib_hdr:
        return {
            "status": "upgrade",
            "badge": f"UPGRADE ({lib_short} → HDR)",
            "color_key": "purple",
            "summary": f"On disk (SDR): {best_desc}",
        }

    if search_rank < lib_rank:
        return {
            "status": "have_better",
            "badge": f"HAVE BETTER ({lib_short})",
            "color_key": "green",
            "summary": f"Better copy on disk: {best_desc}",
        }

    hdr_tag = " HDR" if lib_hdr else ""
    return {
        "status": "owned",
        "badge": f"IN LIBRARY ({lib_short}{hdr_tag})",
        "color_key": "green",
        "summary": f"On disk: {best_desc}",
    }


# ==============================================================================
# 3. MEDIA INSPECTOR UI PANEL (Embedded Tab for Operations Center)
# ==============================================================================

class MediaInspectorPanel(ctk.CTkFrame):
    def __init__(
        self,
        master,
        colors: dict,
        ui_font: str,
        mono_font: str,
        initial_folders: list[str] | None = None,
        ffprobe_path: str = "",
        on_config_change=None,
        on_status=None,
        on_library_updated=None,
    ):
        super().__init__(master, fg_color="transparent")
        self.colors = colors
        self.ui_font = ui_font
        self.mono_font = mono_font
        self.folders: list[str] = [f for f in (initial_folders or []) if str(f).strip()]
        self.ffprobe_path: str = (ffprobe_path or "").strip()
        self.on_config_change = on_config_change
        self.on_status = on_status
        self.on_library_updated = on_library_updated

        self._shutting_down = False
        self._ui_queue: queue.Queue = queue.Queue()
        self._drain_after_id = None
        self._scan_in_flight = False
        self._scan_seq = 0
        self._cancel_event = threading.Event()
        self._probe_cache: dict[str, dict] = self._load_cache()

        self.all_items: list[MediaItem] = []
        self.filtered_items: list[MediaItem] = []
        self.duplicate_groups: dict[str, list[MediaItem]] = {}
        self.selected_item: MediaItem | None = None
        self.row_frames: dict[Path, ctk.CTkFrame] = {}
        self.item_by_path: dict[Path, MediaItem] = {}
        self.current_page: int = 0

        self._build_ui()
        self._update_ffprobe_badge()
        self._render_folder_chips()
        self._drain_ui_queue()

    def shutdown(self) -> None:
        self._shutting_down = True
        self._cancel_event.set()
        if self._drain_after_id:
            try:
                self.after_cancel(self._drain_after_id)
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
                except Exception as exc:
                    try:
                        self._emit_status(f"Media Inspector internal error: {exc!r}", log=True, level="ERROR")
                    except Exception:
                        pass
        except queue.Empty:
            pass
        self._drain_after_id = self.after(50, self._drain_ui_queue)

    @staticmethod
    def _load_cache() -> dict[str, dict]:
        if MEDIA_CACHE_FILE.exists():
            try:
                raw = json.loads(MEDIA_CACHE_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return raw
            except Exception:
                pass
        return {}

    def _prune_cache(self, roots: list[Path], discovered: list[Path]) -> int:
        """Drops cached probe results for files under the scanned folders that no longer exist,
        so the cache (and the startup library index built from it) doesn't grow forever."""
        def norm(p: str) -> str:
            p = p.replace("\\", "/").rstrip("/")
            return p.lower() if _SYS == "Windows" else p

        root_keys = [norm(str(r)) + "/" for r in roots]
        found = {norm(str(p)) for p in discovered}
        stale = [
            k for k in self._probe_cache
            if any(norm(k).startswith(rk) for rk in root_keys) and norm(k) not in found
        ]
        for k in stale:
            del self._probe_cache[k]
        return len(stale)

    def _save_cache(self) -> None:
        try:
            _atomic_write_json(MEDIA_CACHE_FILE, self._probe_cache)
        except Exception:
            pass

    def _emit_status(self, msg: str, log: bool = False, level: str = "INFO") -> None:
        if self.on_status:
            self.on_status(msg, log=log, level=level)

    def _persist_config(self) -> None:
        if self.on_config_change:
            self.on_config_change(list(self.folders), self.ffprobe_path)

    def _build_ui(self) -> None:
        C = self.colors
        self.grid_rowconfigure(4, weight=1)
        self.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(self, fg_color=C["bg_panel"], border_width=1, border_color=C["border"], corner_radius=10)
        top.grid(row=0, column=0, sticky="ew", padx=4, pady=(6, 4))

        ctk.CTkLabel(
            top,
            text="MEDIA LIBRARIES",
            font=ctk.CTkFont(family=self.ui_font, size=11, weight="bold"),
            text_color=C["accent"],
        ).pack(side="left", padx=(12, 8), pady=8)

        ctk.CTkButton(
            top,
            text="+ Add Folder",
            width=102,
            height=28,
            fg_color=C["bg_card"],
            hover_color=C["bg_card_alt"],
            border_width=1,
            border_color=C["border"],
            command=self._add_library_folder,
        ).pack(side="left", padx=4, pady=8)

        ctk.CTkButton(
            top,
            text="Set ffprobe...",
            width=96,
            height=28,
            fg_color=C["bg_card"],
            hover_color=C["bg_card_alt"],
            command=self._pick_ffprobe,
        ).pack(side="left", padx=4, pady=8)

        self.ffprobe_badge = ctk.CTkLabel(
            top,
            text="ffprobe: Checking",
            fg_color=C["bg_card"],
            corner_radius=6,
            padx=8,
            pady=3,
            font=ctk.CTkFont(family=self.ui_font, size=10),
            text_color=C["text_secondary"],
        )
        self.ffprobe_badge.pack(side="left", padx=8, pady=8)

        self.scan_btn = ctk.CTkButton(
            top,
            text="Scan Library",
            width=112,
            height=30,
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            text_color="#041016",
            font=ctk.CTkFont(family=self.ui_font, size=12, weight="bold"),
            command=self._on_scan_button,
        )
        self.scan_btn.pack(side="right", padx=(6, 12), pady=8)

        self.force_reprobe_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            top,
            text="Force Re-Probe",
            variable=self.force_reprobe_var,
            checkbox_width=16,
            checkbox_height=16,
            fg_color=C["accent"],
            font=ctk.CTkFont(family=self.ui_font, size=11),
        ).pack(side="right", padx=8, pady=8)

        self.folders_strip = ctk.CTkFrame(self, fg_color=C["bg_panel"], border_width=1, border_color=C["border"], corner_radius=8)
        self.folders_strip.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 4))

        dash = ctk.CTkFrame(self, fg_color="transparent")
        dash.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 4))
        for col in range(4):
            dash.grid_columnconfigure(col, weight=1, uniform="med_metric")

        self.stat_files_val, self.stat_files_sub = self._dash_card(
            dash, 0, "SCANNED LIBRARY", "0 files", "0 Movies  •  0 TV", C["accent"]
        )
        storage_card, self.stat_storage_val, self.stat_storage_sub = self._dash_card(
            dash, 1, "STORAGE FOOTPRINT", "0 B", "No folders scanned", C["sky"], return_card=True
        )
        self.storage_bar = ctk.CTkProgressBar(
            storage_card, height=4, fg_color=C["bg_root"], progress_color=C["sky"]
        )
        self.storage_bar.set(0)
        self.storage_bar.pack(fill="x", padx=12, pady=(0, 7))

        self.stat_quality_val, self.stat_quality_sub = self._dash_card(
            dash, 2, "QUALITY & HDR", "0 4K • 0 HDR", "1080p: 0  •  HEVC: 0", C["purple"]
        )
        self.stat_health_val, self.stat_health_sub = self._dash_card(
            dash, 3, "DUPLICATES & HEALTH", "0 dupes • 0 corrupt", "Read-only inspection", C["green"]
        )

        fbar = ctk.CTkFrame(self, fg_color=C["bg_panel"], border_width=1, border_color=C["border"], corner_radius=10)
        fbar.grid(row=3, column=0, sticky="ew", padx=4, pady=(0, 4))

        self.filter_entry = ctk.CTkEntry(
            fbar,
            width=180,
            height=28,
            fg_color=C["bg_card"],
            border_color=C["border"],
            placeholder_text="Filter title, codec, audio, path...",
        )
        self.filter_entry.pack(side="left", padx=(10, 6), pady=7)
        self.filter_entry.bind("<KeyRelease>", lambda _e: self._apply_filters(reset_page=True))
        self.filter_entry.bind("<Escape>", self._clear_filter_box)

        self.type_var = ctk.StringVar(value=TYPE_FILTERS[0])
        ctk.CTkOptionMenu(
            fbar,
            variable=self.type_var,
            values=TYPE_FILTERS,
            width=106,
            height=28,
            fg_color=C["bg_card"],
            button_color=C["border"],
            command=lambda _v: self._apply_filters(reset_page=True),
        ).pack(side="left", padx=(0, 6), pady=7)

        self.quality_var = ctk.StringVar(value=QUALITY_FILTERS[0])
        ctk.CTkOptionMenu(
            fbar,
            variable=self.quality_var,
            values=QUALITY_FILTERS,
            width=118,
            height=28,
            fg_color=C["bg_card"],
            button_color=C["border"],
            command=lambda _v: self._apply_filters(reset_page=True),
        ).pack(side="left", padx=(0, 6), pady=7)

        self.health_var = ctk.StringVar(value=HEALTH_FILTERS[0])
        ctk.CTkOptionMenu(
            fbar,
            variable=self.health_var,
            values=HEALTH_FILTERS,
            width=142,
            height=28,
            fg_color=C["bg_card"],
            button_color=C["border"],
            command=lambda _v: self._apply_filters(reset_page=True),
        ).pack(side="left", padx=(0, 6), pady=7)

        self.sort_var = ctk.StringVar(value=SORT_MODES[0])
        ctk.CTkOptionMenu(
            fbar,
            variable=self.sort_var,
            values=SORT_MODES,
            width=146,
            height=28,
            fg_color=C["bg_card"],
            button_color=C["border"],
            command=lambda _v: self._apply_filters(reset_page=True),
        ).pack(side="left", padx=(0, 6), pady=7)

        ctk.CTkButton(
            fbar,
            text="Reset",
            width=52,
            height=26,
            fg_color=C["bg_card"],
            hover_color=C["bg_card_alt"],
            command=self._reset_filters,
        ).pack(side="left", padx=2, pady=7)

        self.next_btn = ctk.CTkButton(
            fbar,
            text="Next ▶",
            width=66,
            height=26,
            fg_color=C["bg_card"],
            hover_color=C["bg_card_alt"],
            command=lambda: self._change_page(1),
        )
        self.next_btn.pack(side="right", padx=(4, 10), pady=7)

        self.page_lbl = ctk.CTkLabel(
            fbar,
            text="Page 1 / 1",
            font=ctk.CTkFont(family=self.ui_font, size=11),
            text_color=C["text_secondary"],
        )
        self.page_lbl.pack(side="right", padx=6, pady=7)

        self.prev_btn = ctk.CTkButton(
            fbar,
            text="◀ Prev",
            width=66,
            height=26,
            fg_color=C["bg_card"],
            hover_color=C["bg_card_alt"],
            command=lambda: self._change_page(-1),
        )
        self.prev_btn.pack(side="right", padx=4, pady=7)

        self.scan_progress_lbl = ctk.CTkLabel(
            fbar,
            text="",
            font=ctk.CTkFont(family=self.ui_font, size=10),
            text_color=C["accent"],
        )
        self.scan_progress_lbl.pack(side="right", padx=8, pady=7)

        split = ctk.CTkFrame(self, fg_color="transparent")
        split.grid(row=4, column=0, sticky="nsew", padx=4, pady=(0, 4))
        split.grid_rowconfigure(0, weight=1)
        split.grid_columnconfigure(0, weight=3)
        split.grid_columnconfigure(1, weight=2)

        self.list_frame = ctk.CTkScrollableFrame(
            split,
            fg_color=C["bg_panel"],
            border_width=1,
            border_color=C["border"],
            corner_radius=10,
        )
        self.list_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        ctk.CTkLabel(
            self.list_frame,
            text="Add one or more Movie / TV folders above, then click Scan Library.",
            text_color=C["text_muted"],
        ).pack(pady=40)

        inspector = ctk.CTkFrame(
            split,
            fg_color=C["bg_panel"],
            border_width=1,
            border_color=C["border"],
            corner_radius=10,
        )
        inspector.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        inspector.grid_rowconfigure(1, weight=1)
        inspector.grid_columnconfigure(0, weight=1)

        self.insp_title = ctk.CTkLabel(
            inspector,
            text="Select a media file to inspect metadata",
            anchor="w",
            font=ctk.CTkFont(family=self.ui_font, size=13, weight="bold"),
            text_color=C["text_primary"],
        )
        self.insp_title.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))

        self.insp_box = ctk.CTkTextbox(
            inspector,
            fg_color=C["bg_card"],
            text_color=C["text_primary"],
            font=ctk.CTkFont(family=self.mono_font, size=11),
        )
        self.insp_box.grid(row=1, column=0, sticky="nsew", padx=14, pady=6)
        self.insp_box.configure(state="disabled")

        insp_btns = ctk.CTkFrame(inspector, fg_color="transparent")
        insp_btns.grid(row=2, column=0, sticky="ew", padx=14, pady=(4, 12))

        ctk.CTkButton(
            insp_btns,
            text="Open Folder",
            width=110,
            height=28,
            fg_color=C["bg_card"],
            hover_color=C["bg_card_alt"],
            command=self._open_selected_folder,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            insp_btns,
            text="Copy Path",
            width=96,
            height=28,
            fg_color=C["bg_card"],
            hover_color=C["bg_card_alt"],
            command=self._copy_selected_path,
        ).pack(side="left")

        ctk.CTkLabel(
            insp_btns,
            text="Read-Only Mode",
            font=ctk.CTkFont(family=self.ui_font, size=10),
            text_color=C["text_muted"],
        ).pack(side="right")

    def _dash_card(self, parent, col: int, title: str, value: str, sub: str, accent: str, return_card: bool = False):
        C = self.colors
        card = ctk.CTkFrame(
            parent,
            fg_color=C["bg_panel"],
            border_width=1,
            border_color=C["border"],
            corner_radius=10,
        )
        pad_r = 0 if col == 3 else 8
        card.grid(row=0, column=col, sticky="nsew", padx=(0, pad_r))

        ctk.CTkLabel(
            card,
            text=title,
            anchor="w",
            font=ctk.CTkFont(family=self.ui_font, size=10, weight="bold"),
            text_color=C["text_muted"],
        ).pack(fill="x", padx=12, pady=(8, 2))

        val_lbl = ctk.CTkLabel(
            card,
            text=value,
            anchor="w",
            font=ctk.CTkFont(family=self.ui_font, size=14, weight="bold"),
            text_color=accent,
        )
        val_lbl.pack(fill="x", padx=12)

        sub_lbl = ctk.CTkLabel(
            card,
            text=sub,
            anchor="w",
            font=ctk.CTkFont(family=self.ui_font, size=11),
            text_color=C["text_secondary"],
        )
        sub_lbl.pack(fill="x", padx=12, pady=(2, 5 if return_card else 8))

        if return_card:
            return card, val_lbl, sub_lbl
        return val_lbl, sub_lbl

    def _update_ffprobe_badge(self) -> str:
        C = self.colors
        exe = find_ffprobe(self.ffprobe_path)
        if exe:
            self.ffprobe_badge.configure(text="ffprobe: Ready", text_color=C["green"])
        else:
            self.ffprobe_badge.configure(text="ffprobe: Missing (basic scan)", text_color=C["amber"])
        return exe

    def _pick_ffprobe(self) -> None:
        ftypes = [("ffprobe executable", "ffprobe.exe"), ("All files", "*.*")] if _SYS == "Windows" else [("All files", "*")]
        chosen = filedialog.askopenfilename(title="Locate ffprobe executable", filetypes=ftypes)
        if chosen:
            self.ffprobe_path = chosen
            self._update_ffprobe_badge()
            self._persist_config()
            self._emit_status(f"Set ffprobe path: {chosen}", log=True, level="OK")

    def _add_library_folder(self) -> None:
        chosen = filedialog.askdirectory(title="Select Movie or TV Library Folder")
        if not chosen:
            return
        norm = str(Path(chosen))
        if norm not in self.folders:
            self.folders.append(norm)
            self._render_folder_chips()
            self._persist_config()
            self._emit_status(f"Added media library folder: {norm}", log=True, level="OK")

    def _remove_library_folder(self, folder: str) -> None:
        if folder in self.folders:
            self.folders.remove(folder)
            self._render_folder_chips()
            self._persist_config()

    def _render_folder_chips(self) -> None:
        C = self.colors
        for w in self.folders_strip.winfo_children():
            w.destroy()

        if not self.folders:
            ctk.CTkLabel(
                self.folders_strip,
                text="No library folders added yet. Click '+ Add Folder' to add Movies or TV directories.",
                font=ctk.CTkFont(family=self.ui_font, size=11),
                text_color=C["text_muted"],
            ).pack(side="left", padx=12, pady=6)
            return

        for f in self.folders:
            chip = ctk.CTkFrame(self.folders_strip, fg_color=C["bg_card"], corner_radius=6)
            chip.pack(side="left", padx=(8, 0), pady=5)
            ctk.CTkLabel(
                chip,
                text=f,
                font=ctk.CTkFont(family=self.ui_font, size=10),
                text_color=C["text_primary"],
            ).pack(side="left", padx=(8, 4), pady=3)
            ctk.CTkButton(
                chip,
                text="×",
                width=20,
                height=20,
                fg_color="transparent",
                hover_color=C["red"],
                text_color=C["text_secondary"],
                command=lambda p=f: self._remove_library_folder(p),
            ).pack(side="left", padx=(0, 4), pady=2)

    def _on_scan_button(self) -> None:
        if self._scan_in_flight:
            self._scan_seq += 1
            self._cancel_event.set()
            self._set_scan_busy(False)
            self._emit_status("Media library scan cancelled.", log=True, level="WARN")
        else:
            self._start_scan()

    def _set_scan_busy(self, busy: bool) -> None:
        C = self.colors
        self._scan_in_flight = busy
        if busy:
            self.scan_btn.configure(text="Stop Scan", fg_color=C["red"], hover_color="#DC2626", text_color="#FFFFFF")
        else:
            self.scan_btn.configure(text="Scan Library", fg_color=C["accent"], hover_color=C["accent_hover"], text_color="#041016")
            self.scan_progress_lbl.configure(text="")

    def _start_scan(self) -> None:
        if not self.folders:
            messagebox.showinfo("No Library Folders", "Add at least one Movie or TV folder first.")
            return

        valid_roots = [Path(f) for f in self.folders if Path(f).exists()]
        if not valid_roots:
            self._emit_status("None of the configured media folders exist on disk.", log=True, level="WARN")
            return

        ffprobe_exe = self._update_ffprobe_badge()
        force_reprobe = self.force_reprobe_var.get()

        self._scan_seq += 1
        req_id = self._scan_seq
        # A NEW event per scan. Clearing a shared event would revive a cancelled scan whose
        # worker is still draining its thread pool, so two scans would run at once.
        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        self._set_scan_busy(True)
        self.scan_progress_lbl.configure(text="Discovering files...")
        self._emit_status(f"Scanning {len(valid_roots)} media folder(s) in background...", log=True)

        def worker():
            # Any unexpected error must still end the scan, or the UI stays on "Scanning" for good.
            try:
                scan()
            except Exception as exc:
                def failed(e=exc):
                    if req_id == self._scan_seq and not self._shutting_down:
                        self._set_scan_busy(False)
                        self.scan_progress_lbl.configure(text="Scan failed")
                        self._emit_status(f"Media scan failed: {e!r}", log=True, level="ERROR")
                self._post_ui(failed)

        def scan():
            _subtitle_files_in.cache_clear()  # pick up subtitle files added since the last scan
            discovered: list[Path] = []
            seen_paths: set[str] = set()

            for root_dir in valid_roots:
                if cancel_event.is_set() or self._shutting_down:
                    return
                for dirpath, dirnames, filenames in os.walk(root_dir):
                    if cancel_event.is_set() or self._shutting_down:
                        return
                    dirnames[:] = [
                        d for d in dirnames
                        if not d.startswith(".") and d.lower() not in SKIP_DIR_NAMES
                    ]
                    for fname in filenames:
                        fpath = Path(dirpath) / fname
                        if fpath.suffix.lower() not in VIDEO_EXTENSIONS:
                            continue
                        key = str(fpath.resolve()) if _SYS != "Windows" else str(fpath).lower()
                        if key not in seen_paths:
                            seen_paths.add(key)
                            discovered.append(fpath)

            total = len(discovered)
            self._post_ui(
                lambda: req_id == self._scan_seq and self.scan_progress_lbl.configure(text=f"Probing 0 / {total}")
            )

            results: list[MediaItem] = []
            cache_snapshot = {} if force_reprobe else dict(self._probe_cache)
            new_cache_entries: dict[str, dict] = {}
            completed = 0

            def probe_one(p: Path):
                if cancel_event.is_set() or self._shutting_down:
                    return None
                c_entry = cache_snapshot.get(str(p))
                return probe_media_file(p, ffprobe_exe, cache_entry=c_entry)

            with ThreadPoolExecutor(max_workers=4) as pool:
                for res in pool.map(probe_one, discovered):
                    if cancel_event.is_set() or self._shutting_down:
                        return
                    completed += 1
                    if res is not None:
                        item, cache_rec = res
                        results.append(item)
                        if cache_rec is not None:
                            new_cache_entries[str(item.path)] = cache_rec
                    if completed % 5 == 0 or completed == total:
                        self._post_ui(
                            lambda c=completed, t=total: req_id == self._scan_seq
                            and self.scan_progress_lbl.configure(text=f"Probing {c} / {t}")
                        )

            if cancel_event.is_set() or self._shutting_down:
                return

            dup_groups = annotate_duplicate_movies(results)

            def finish():
                if req_id != self._scan_seq or self._shutting_down:
                    return
                pruned = self._prune_cache(valid_roots, discovered)
                if new_cache_entries:
                    self._probe_cache.update(new_cache_entries)
                if new_cache_entries or pruned:
                    self._save_cache()
                self.all_items = results
                self.duplicate_groups = dup_groups
                self.selected_item = None
                self._clear_inspector()
                self._set_scan_busy(False)
                self._update_dashboard()
                self._apply_filters(reset_page=True)

                if self.on_library_updated:
                    self.on_library_updated(list(results))

                corrupt_cnt = sum(1 for it in results if it.is_corrupt)
                dup_cnt = len(dup_groups)
                note = " (no ffprobe — basic scan)" if not ffprobe_exe else ""
                self._emit_status(
                    f"Media scan complete{note}: {len(results)} file(s), {dup_cnt} duplicate group(s), {corrupt_cnt} corrupt.",
                    log=True,
                    level="WARN" if corrupt_cnt else "OK",
                )

            self._post_ui(finish)

        threading.Thread(target=worker, daemon=True).start()

    def _update_dashboard(self) -> None:
        C = self.colors
        items = self.all_items
        total_files = len(items)
        movies = [it for it in items if it.media_type == "Movie"]
        tvs = [it for it in items if it.media_type == "TV"]

        movie_bytes = sum(it.size_bytes for it in movies)
        tv_bytes = sum(it.size_bytes for it in tvs)
        total_bytes = movie_bytes + tv_bytes

        self.stat_files_val.configure(text=f"{total_files:,} files")
        self.stat_files_sub.configure(text=f"{len(movies):,} Movies  •  {len(tvs):,} TV")

        vol_totals = 0
        vol_free = 0
        seen_devs: set[object] = set()
        for f in self.folders:
            p = Path(f)
            if not p.exists():
                continue
            try:
                dev = p.drive.upper() if _SYS == "Windows" and p.drive else os.stat(p).st_dev
                if dev in seen_devs:
                    continue
                seen_devs.add(dev)
                du = shutil.disk_usage(p)
                vol_totals += du.total
                vol_free += du.free
            except OSError:
                pass

        self.stat_storage_val.configure(text=format_bytes(total_bytes))
        if vol_totals > 0:
            used_pct = max(0.0, min(1.0, (vol_totals - vol_free) / vol_totals))
            self.stat_storage_sub.configure(
                text=f"Movies: {format_bytes(movie_bytes)} • TV: {format_bytes(tv_bytes)} ({format_bytes(vol_free)} free)"
            )
            bar_col = C["red"] if used_pct > 0.9 else (C["amber"] if used_pct > 0.78 else C["sky"])
            self.storage_bar.set(used_pct)
            self.storage_bar.configure(progress_color=bar_col)
        else:
            self.stat_storage_sub.configure(text=f"Movies: {format_bytes(movie_bytes)} • TV: {format_bytes(tv_bytes)}")
            self.storage_bar.set(0)

        uhd_cnt = sum(1 for it in items if it.resolution_label == "4K / 2160p")
        hd_cnt = sum(1 for it in items if it.resolution_label == "1080p")
        hdr_cnt = sum(1 for it in items if it.is_hdr)
        hevc_cnt = sum(1 for it in items if "HEVC" in it.video_codec)

        self.stat_quality_val.configure(text=f"{uhd_cnt} 4K • {hdr_cnt} HDR")
        self.stat_quality_sub.configure(text=f"1080p: {hd_cnt}  •  HEVC: {hevc_cnt}")

        dup_groups_cnt = len(self.duplicate_groups)
        corrupt_cnt = sum(1 for it in items if it.is_corrupt)
        health_col = C["red"] if corrupt_cnt > 0 else (C["amber"] if dup_groups_cnt > 0 else C["green"])
        self.stat_health_val.configure(text=f"{dup_groups_cnt} dupes • {corrupt_cnt} corrupt", text_color=health_col)

        extra_dup_bytes = sum(
            sum(it.size_bytes for it in grp) - max(it.size_bytes for it in grp)
            for grp in self.duplicate_groups.values()
        )
        self.stat_health_sub.configure(
            text=f"Duplicate extra footprint: {format_bytes(extra_dup_bytes)}" if dup_groups_cnt else "No duplicates or corrupt files"
        )

    def _clear_filter_box(self, _event=None) -> None:
        self.filter_entry.delete(0, "end")
        self._apply_filters(reset_page=True)

    def _reset_filters(self) -> None:
        self.filter_entry.delete(0, "end")
        self.type_var.set(TYPE_FILTERS[0])
        self.quality_var.set(QUALITY_FILTERS[0])
        self.health_var.set(HEALTH_FILTERS[0])
        self.sort_var.set(SORT_MODES[0])
        self._apply_filters(reset_page=True)

    def _apply_filters(self, reset_page: bool = False) -> None:
        items = list(self.all_items)

        q = self.filter_entry.get().strip().lower()
        if q:
            tokens = q.split()
            items = [it for it in items if it.matches_text(tokens)]

        t_filter = self.type_var.get()
        if t_filter == "Movies":
            items = [it for it in items if it.media_type == "Movie"]
        elif t_filter == "TV Shows":
            items = [it for it in items if it.media_type == "TV"]

        q_filter = self.quality_var.get()
        if q_filter == "HDR / DV":
            items = [it for it in items if it.is_hdr]
        elif q_filter != "All Qualities":
            items = [it for it in items if it.resolution_label == q_filter]

        h_filter = self.health_var.get()
        if h_filter == "Healthy Only":
            items = [it for it in items if not it.is_corrupt]
        elif h_filter == "Duplicates Only":
            items = [it for it in items if it.is_duplicate]
        elif h_filter == "Corrupt / Unreadable":
            items = [it for it in items if it.is_corrupt]

        s_mode = self.sort_var.get()
        if s_mode == "Title (A–Z)":
            items.sort(key=lambda it: (it.display_title.lower(), -it.size_bytes))
        elif s_mode == "Size: Largest First":
            items.sort(key=lambda it: it.size_bytes, reverse=True)
        elif s_mode == "Size: Smallest First":
            items.sort(key=lambda it: it.size_bytes)
        elif s_mode == "Resolution: Highest First":
            items.sort(key=lambda it: (it.resolution_rank, it.total_bitrate, it.size_bytes), reverse=True)
        elif s_mode == "Bitrate: Highest First":
            items.sort(key=lambda it: it.total_bitrate, reverse=True)
        elif s_mode == "Newest Modified":
            items.sort(key=lambda it: it.mtime_ns, reverse=True)

        self.filtered_items = items
        if reset_page:
            self.current_page = 0

        total_pages = max(1, (len(items) + MEDIA_PAGE_SIZE - 1) // MEDIA_PAGE_SIZE)
        if self.current_page >= total_pages:
            self.current_page = total_pages - 1

        self.page_lbl.configure(text=f"Page {self.current_page + 1} / {total_pages} ({len(items)})")
        self.prev_btn.configure(state="normal" if self.current_page > 0 else "disabled")
        self.next_btn.configure(state="normal" if (self.current_page + 1) < total_pages else "disabled")

        start = self.current_page * MEDIA_PAGE_SIZE
        self._render_list(items[start : start + MEDIA_PAGE_SIZE])

    def _change_page(self, delta: int) -> None:
        total_pages = max(1, (len(self.filtered_items) + MEDIA_PAGE_SIZE - 1) // MEDIA_PAGE_SIZE)
        new_page = max(0, min(total_pages - 1, self.current_page + delta))
        if new_page != self.current_page:
            self.current_page = new_page
            self._apply_filters(reset_page=False)

    def _render_list(self, page_items: list[MediaItem]) -> None:
        C = self.colors
        for w in self.list_frame.winfo_children():
            w.destroy()
        self.row_frames.clear()
        self.item_by_path.clear()

        try:
            canvas = getattr(self.list_frame, "_parent_canvas", None)
            if canvas is not None:
                canvas.yview_moveto(0.0)
        except Exception:
            pass

        if not page_items:
            msg = "No media files found matching filters." if self.all_items else "Add folders above and click Scan Library."
            ctk.CTkLabel(self.list_frame, text=msg, text_color=C["text_muted"]).pack(pady=36)
            return

        for item in page_items:
            is_sel = self.selected_item is not None and self.selected_item.path == item.path
            border_col = C["red"] if item.is_corrupt else (C["border_accent"] if is_sel else C["border"])

            card = ctk.CTkFrame(
                self.list_frame,
                fg_color=C["bg_card_alt"] if is_sel else C["bg_card"],
                corner_radius=8,
                border_width=1,
                border_color=border_col,
            )
            card.pack(fill="x", padx=6, pady=4)
            self.row_frames[item.path] = card
            self.item_by_path[item.path] = item

            ctk.CTkButton(
                card,
                text="Inspect",
                width=74,
                height=28,
                fg_color=C["bg_panel"],
                hover_color=C["border"],
                command=lambda it=item: self._inspect_item(it),
            ).pack(side="right", padx=(6, 10), pady=10)

            info = ctk.CTkFrame(card, fg_color="transparent")
            info.pack(side="left", fill="both", expand=True, padx=(10, 4), pady=8)

            title_lbl = ctk.CTkLabel(
                info,
                text=item.display_title,
                anchor="w",
                font=ctk.CTkFont(family=self.ui_font, size=12, weight="bold"),
                text_color=C["red"] if item.is_corrupt else C["text_primary"],
            )
            title_lbl.pack(fill="x")

            badges = ctk.CTkFrame(info, fg_color="transparent")
            badges.pack(fill="x", pady=(4, 0))

            badge_list: list[tuple[str, str]] = [
                (item.media_type.upper(), C["sky"] if item.media_type == "TV" else C["accent"]),
                (format_bytes(item.size_bytes), C["text_secondary"]),
            ]

            if item.is_corrupt:
                badge_list.append(("CORRUPT", C["red"]))
            elif item.probed:
                res_col = C["purple"] if item.resolution_label == "4K / 2160p" else (C["accent"] if item.resolution_label == "1080p" else C["text_secondary"])
                badge_list.append((item.resolution_label, res_col))
                badge_list.append((item.video_codec, C["text_secondary"]))
                if item.is_hdr:
                    badge_list.append((item.hdr_format, C["amber"]))
                if item.total_bitrate > 0:
                    badge_list.append((format_bitrate(item.total_bitrate), C["text_muted"]))
                if item.audio_tracks:
                    a0 = item.audio_tracks[0]
                    badge_list.append((f"{a0['codec']} {a0['channels']}".strip(), C["green"]))
                sub_cnt = len(item.subtitle_tracks) + len(item.external_subtitles)
                if sub_cnt > 0:
                    badge_list.append((f"{sub_cnt} sub{'s' if sub_cnt != 1 else ''}", C["text_muted"]))

            if item.is_duplicate:
                badge_list.append((f"DUPLICATE (×{item.duplicate_count})", C["amber"]))

            for txt, col in badge_list:
                ctk.CTkLabel(
                    badges,
                    text=txt,
                    fg_color=C["bg_panel"],
                    corner_radius=5,
                    padx=6,
                    pady=1,
                    font=ctk.CTkFont(family=self.ui_font, size=10),
                    text_color=col,
                ).pack(side="left", padx=(0, 4))

            for widget in (card, info, title_lbl, badges):
                widget.bind("<Button-1>", lambda _e, it=item: self._inspect_item(it))

    def _clear_inspector(self) -> None:
        self.insp_title.configure(text="Select a media file to inspect metadata")
        self.insp_box.configure(state="normal")
        self.insp_box.delete("1.0", "end")
        self.insp_box.configure(state="disabled")

    def _inspect_item(self, item: MediaItem) -> None:
        C = self.colors
        self.selected_item = item
        for path_k, frame in self.row_frames.items():
            if not frame.winfo_exists():
                continue
            if path_k == item.path:
                frame.configure(fg_color=C["bg_card_alt"], border_color=C["border_accent"])
            else:
                other = self.item_by_path.get(path_k)
                is_corr = bool(other and other.is_corrupt)
                frame.configure(fg_color=C["bg_card"], border_color=C["red"] if is_corr else C["border"])

        self.insp_title.configure(text=item.display_title)

        mod_str = datetime.fromtimestamp(item.mtime_ns / 1e9).strftime("%Y-%m-%d %H:%M") if item.mtime_ns else "—"
        lines = [
            f"FILE:       {item.filename}",
            f"FOLDER:     {item.folder}",
            f"TYPE:       {item.media_type}",
            f"SIZE:       {format_bytes(item.size_bytes)} ({item.size_bytes:,} bytes)",
            f"MODIFIED:   {mod_str}",
            f"HEALTH:     {'CORRUPT / UNREADABLE' if item.is_corrupt else 'OK'}",
        ]
        if item.is_corrupt and item.error_reason:
            lines.append(f"ERROR:      {item.error_reason}")

        dkey = duplicate_key(item)
        if item.is_duplicate and dkey in self.duplicate_groups:
            kind = "EPISODE" if item.media_type == "TV" else "MOVIE"
            lines.extend(["", f"--- DUPLICATE {kind} GROUP ({item.duplicate_count} files) ---"])
            for dup in self.duplicate_groups[dkey]:
                marker = ">> " if dup.path == item.path else "   "
                lines.append(
                    f"{marker}[{format_bytes(dup.size_bytes):>9} | {dup.resolution_label:<10} | {dup.video_codec}] {dup.path}"
                )

        if item.probed and not item.is_corrupt:
            lines.extend([
                "",
                "--- VIDEO STREAM ---",
                f"Container:  {item.container}   •   Duration: {format_duration(item.duration_sec)}",
                f"Resolution: {item.width}x{item.height} ({item.resolution_label})" + (f" @ {item.fps} fps" if item.fps else ""),
                f"Codec:      {item.video_codec}" + (f" ({item.video_profile})" if item.video_profile else "") + (f" • {item.bit_depth}" if item.bit_depth else ""),
                f"HDR Format: {item.hdr_format}",
                f"Bitrate:    Total {format_bitrate(item.total_bitrate)}   •   Video {format_bitrate(item.video_bitrate)}",
            ])

            lines.extend(["", f"--- AUDIO TRACKS ({len(item.audio_tracks)}) ---"])
            if not item.audio_tracks:
                lines.append("  None detected")
            for a in item.audio_tracks:
                flags = []
                if a.get("default"):
                    flags.append("Default")
                if a.get("forced"):
                    flags.append("Forced")
                flag_txt = f" [{', '.join(flags)}]" if flags else ""
                title_txt = f" — {a['title']}" if a.get("title") else ""
                lines.append(
                    f"  #{a['index']}: {a['codec']} {a['channels']}  ({a['language']}){title_txt}{flag_txt}"
                )

            all_subs = item.subtitle_tracks + item.external_subtitles
            lines.extend(["", f"--- SUBTITLES ({len(all_subs)}) ---"])
            if not all_subs:
                lines.append("  None detected")
            for idx, s in enumerate(all_subs, 1):
                src = "External" if s.get("external") else "Embedded"
                flags = []
                if s.get("default"):
                    flags.append("Default")
                if s.get("forced"):
                    flags.append("Forced")
                flag_txt = f" [{', '.join(flags)}]" if flags else ""
                title_txt = f" — {s['title']}" if s.get("title") else ""
                lines.append(
                    f"  #{idx} ({src}): {s['codec']} ({s['language']}){title_txt}{flag_txt}"
                )
        elif not item.probed:
            lines.extend(["", "Install FFmpeg / set ffprobe path to view codec, HDR, audio, and subtitle tracks."])

        self.insp_box.configure(state="normal")
        self.insp_box.delete("1.0", "end")
        self.insp_box.insert("1.0", "\n".join(lines))
        self.insp_box.configure(state="disabled")

    def _open_selected_folder(self) -> None:
        if not self.selected_item:
            self._emit_status("Select a media item first.")
            return
        folder = self.selected_item.path.parent
        if folder.exists():
            try:
                _open_folder_in_os(folder)
            except Exception as exc:
                self._emit_status(f"Could not open folder: {exc}", log=True, level="ERROR")

    def _copy_selected_path(self) -> None:
        if not self.selected_item:
            self._emit_status("Select a media item first.")
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(str(self.selected_item.path))
            self._emit_status(f"Copied path: {self.selected_item.path}")
        except Exception:
            pass