"""
NZB Command Center — Operations Center Edition
Local NZB, SABnzbd, Multi-Indexer Newznab, Media Inspector, Kodi Manager
& Jellyfin / Plex Media Server Monitor

Dependencies:
    pip install customtkinter pillow

Sibling modules (keep in the same folder as this script):
    kodi_client.py      Kodi tab
    media_inspector.py  Media Inspector tab
    media_server.py     Media Server tab (Jellyfin / Plex)

Usage (PowerShell):
    python nzb_command_center.py
"""

import base64
import ctypes
import ctypes.wintypes
import gzip
import io
import json
import math
import mimetypes
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import tkinter as tk
import tkinter.font as tkfont
import uuid
import webbrowser
import xml.etree.ElementTree as ET
import zlib
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)


try:
    from kodi_client import KodiPanel
    _KODI_IMPORT_ERROR: Exception | None = None
except ImportError as _kodi_exc:  # app still runs; the tab explains what is missing
    KodiPanel = None
    _KODI_IMPORT_ERROR = _kodi_exc

try:
    from media_server import MediaServerPanel
    _MEDIA_SERVER_IMPORT_ERROR: Exception | None = None
except ImportError as _ms_exc:  # app still runs; the tab explains what is missing
    MediaServerPanel = None
    _MEDIA_SERVER_IMPORT_ERROR = _ms_exc

from media_inspector import (
    MEDIA_CACHE_FILE,
    MediaInspectorPanel,
    build_library_index,
    build_library_index_from_cache,
    match_release_to_library,
)

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# ==============================================================================
# 1. CONFIGURATION, CREDENTIAL PROTECTION & THEME
# ==============================================================================

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "nzb_manager_config.json"
STATS_FILE = BASE_DIR / "nzb_daily_stats.json"
CAPS_CACHE_FILE = BASE_DIR / "nzb_caps_cache.json"

MAX_ARTWORK_BYTES = 2 * 1024 * 1024  # 2 MB cap per poster download
MAX_ARTWORK_CACHE = 200              # LRU poster cache limit
ARTWORK_FAIL_TTL_SEC = 300.0         # Retry failed poster lookups after 5 minutes
MAX_RAW_XML_PREVIEW_CHARS = 250_000  # Cap raw XML viewer text to prevent UI freezes
MAX_API_BYTES = 20 * 1024 * 1024     # Cap for indexer search / caps responses
MAX_NZB_BYTES = 50 * 1024 * 1024     # Cap for a downloaded (or decompressed) NZB
USER_AGENT = "Mozilla/5.0 NZBCommandCenter/1.0"
SEARCH_PAGE_SIZE = 40
AUTO_CAT_LABEL = "Auto (Match)"

DEFAULT_CONFIG = {
    "sab_url": "http://127.0.0.1:8080",
    "sab_api_key": "",
    "indexers": [
        {
            "name": "omgwtfnzbs",
            "url": "https://api.omgwtfnzbs.me",
            "api_key": "",
            "enabled": True,
        }
    ],
    "nzb_folder": str(Path.home() / "Downloads"),
    "auto_refresh": True,
    "auto_archive_sent": False,
    "media_folders": [],
    "ffprobe_path": "",
    "kodi_url": "",
    "kodi_username": "",
    "kodi_password": "",
    "kodi_auto_scan": True,
    "kodi_notify_on_complete": True,
    "media_server_type": "Jellyfin",
    "media_server_url": "",
    "media_server_token": "",
    "media_server_auto_scan": True,
    "media_server_path_map": "",
    "search_history": [],
    "desktop_notifications": True,
    "watch_folder": False,
    "window_geometry": "",
    "window_zoomed": False,
    "last_tab": "",
    "compact_dashboard": False,
}

# Keys edited live by the Media Server tab. Settings saves must never overwrite them
# with the (possibly stale) snapshot the Settings dialog captured when it opened.
MEDIA_SERVER_CONFIG_KEYS = (
    "media_server_type",
    "media_server_url",
    "media_server_token",
    "media_server_auto_scan",
    "media_server_path_map",
)

# "Operations Center" Obsidian-Teal & Aqua Palette
COLORS = {
    "bg_root": "#060D10",
    "bg_panel": "#0A151A",
    "bg_card": "#0F1E25",
    "bg_card_alt": "#152933",
    "border": "#193642",
    "border_accent": "#14B8A6",
    "grid_line": "#112832",
    "text_primary": "#F0FDFA",
    "text_secondary": "#94B2BA",
    "text_muted": "#5E7E87",
    "accent": "#2DD4BF",
    "accent_hover": "#5EEAD4",
    "green": "#10B981",
    "amber": "#F59E0B",
    "red": "#EF4444",
    "sky": "#38BDF8",
    "purple": "#A78BFA",
}

_SYS = platform.system()
UI_FONT = "Segoe UI" if _SYS == "Windows" else ("SF Pro Text" if _SYS == "Darwin" else "DejaVu Sans")
MONO_FONT = "Consolas" if _SYS == "Windows" else ("SF Mono" if _SYS == "Darwin" else "DejaVu Sans Mono")
UI_FONT_SEMIBOLD = "Segoe UI Semibold" if _SYS == "Windows" else UI_FONT


# --- Windows toast notifications (no extra packages) ---------------------------------------
# Uses the WinRT toast API through Windows PowerShell 5.1, which ships with Windows 10/11.
# The title/message travel in environment variables and are XML-escaped inside PowerShell,
# so release names can never break or inject into the script.
_TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$t = [Security.SecurityElement]::Escape($env:NZBCC_TOAST_TITLE)
$m = [Security.SecurityElement]::Escape($env:NZBCC_TOAST_MSG)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$m</text></binding></visual></toast>")
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show($toast)
"""


def show_windows_notification(title: str, message: str) -> bool:
    """Shows a Windows notification in the background. Returns False where unsupported."""
    if _SYS != "Windows":
        return False
    env = dict(os.environ, NZBCC_TOAST_TITLE=title[:120], NZBCC_TOAST_MSG=message[:400])
    encoded = base64.b64encode(_TOAST_PS.encode("utf-16-le")).decode("ascii")

    def run() -> None:
        try:
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-EncodedCommand", encoded],
                env=env,
                capture_output=True,
                timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass  # notifications are best-effort; never disturb the app

    threading.Thread(target=run, daemon=True).start()
    return True


def _tint(hex_color: str, amount: float = 0.16, base: str = "#0F1E25") -> str:
    """Blends a colour into a dark base: subtle tinted backgrounds for pills and badges."""
    def rgb(h: str) -> tuple[int, int, int]:
        h = h.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    try:
        c, b = rgb(hex_color), rgb(base)
    except (ValueError, IndexError):
        return base
    mixed = tuple(round(b[i] + (c[i] - b[i]) * amount) for i in range(3))
    return "#%02X%02X%02X" % mixed


ON_ACCENT = "#041016"  # text/icon colour on accent-filled buttons

# --- Icons: Windows' built-in icon font (Segoe Fluent Icons on 11, Segoe MDL2 Assets on 10) ---
ICON_GLYPHS = {
    "search": "\uE721", "send": "\uE724", "view": "\uE890", "save": "\uE74E", "refresh": "\uE72C",
    "pause": "\uE769", "play": "\uE768", "settings": "\uE713", "folder_open": "\uE838", "folder": "\uE8B7",
    "file": "\uE8E5", "delete": "\uE74D", "star": "\uE735", "cancel": "\uE711", "up": "\uE70E",
    "down": "\uE70D", "top": "\uE74A", "movie": "\uE714", "tv": "\uE7F4", "music": "\uE8D6",
    "book": "\uE8F1", "game": "\uE7FC", "app": "\uE71D", "package": "\uE7B8", "code": "\uE943",
    "collapse": "\uE70E", "expand": "\uE70D",
}
_ICON_FONT_FAMILIES = ("Segoe Fluent Icons", "Segoe MDL2 Assets")
_ICON_FONT_FILES = ("SegoeIcons.ttf", "segmdl2.ttf")
_icon_font_path: str | None = None
_icon_font_family: str | None = None
_ICON_CACHE: dict = {}


def _find_icon_font_file() -> str:
    global _icon_font_path
    if _icon_font_path is None:
        _icon_font_path = ""
        fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for name in _ICON_FONT_FILES:
            if (fonts_dir / name).exists():
                _icon_font_path = str(fonts_dir / name)
                break
    return _icon_font_path


def icon_font_family() -> str:
    """Tk font family for drawing icon glyphs as text ("" when the system has none). Needs a Tk root."""
    global _icon_font_family
    if _icon_font_family is None:
        try:
            available = set(tkfont.families())
        except Exception:
            return ""
        _icon_font_family = next((f for f in _ICON_FONT_FAMILIES if f in available), "")
    return _icon_font_family


def app_icon(name: str, color: str, size: int = 14):
    """A crisp CTkImage of an icon glyph in the given colour, or None (no Pillow / no icon font).

    Buttons pass this as image=..., so without Pillow or the font they simply show text only.
    """
    key = (name, color, size)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    img = None
    glyph = ICON_GLYPHS.get(name)
    font_path = _find_icon_font_file()
    if PIL_AVAILABLE and glyph and font_path:
        try:
            from PIL import ImageDraw, ImageFont
            px = size * 3  # drawn large and scaled down: sharp at 100-200% display scaling
            font = ImageFont.truetype(font_path, px)
            im = Image.new("RGBA", (px, px), (0, 0, 0, 0))
            draw = ImageDraw.Draw(im)
            l, t, r, b = draw.textbbox((0, 0), glyph, font=font)
            draw.text(((px - (r - l)) / 2 - l, (px - (b - t)) / 2 - t), glyph, font=font, fill=color)
            img = ctk.CTkImage(light_image=im, dark_image=im, size=(size, size))
        except Exception:
            img = None
    _ICON_CACHE[key] = img
    return img


APP_ICON_FILE = Path(__file__).resolve().parent / "app_icon.ico"


def ensure_app_icon() -> Path | None:
    """The window/taskbar icon (the teal "N" logo). Generated once with Pillow if missing."""
    if APP_ICON_FILE.exists():
        return APP_ICON_FILE
    if not PIL_AVAILABLE:
        return None
    try:
        from PIL import ImageDraw, ImageFont
        S = 256
        im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        draw = ImageDraw.Draw(im)
        draw.rounded_rectangle((8, 8, S - 8, S - 8), radius=58, fill=COLORS["accent"])
        font = None
        for face in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
            try:
                font = ImageFont.truetype(face, 176)
                break
            except OSError:
                continue
        font = font or ImageFont.load_default()
        l, t, r, b = draw.textbbox((0, 0), "N", font=font)
        draw.text(((S - (r - l)) / 2 - l, (S - (b - t)) / 2 - t), "N", font=font, fill=ON_ACCENT)
        im.save(APP_ICON_FILE, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        return APP_ICON_FILE
    except Exception:
        return None


def style_window_chrome(win, caption: str | None = None) -> None:
    """Windows 11: tints the title bar to match the app header. Harmless no-op elsewhere."""
    if _SYS != "Windows":
        return

    def colorref(hex_color: str) -> int:
        h = hex_color.lstrip("#")
        return int(h[4:6] + h[2:4] + h[0:2], 16)  # 0x00BBGGRR

    try:
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        # 35 = caption colour, 34 = border colour, 36 = caption text colour (Windows 11 build 22000+)
        for attr, color in ((35, caption or COLORS["bg_panel"]), (34, COLORS["border"]), (36, COLORS["text_primary"])):
            val = ctypes.c_int(colorref(color))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass


def apply_window_chrome(win, delay_ms: int = 250) -> None:
    """App icon + tinted title bar for a window. Delayed: CustomTkinter sets its own icon at ~200 ms."""
    def run() -> None:
        try:
            if not win.winfo_exists():
                return
            icon = ensure_app_icon()
            if icon:
                win.iconbitmap(str(icon))
            style_window_chrome(win)
        except Exception:
            pass

    win.after(delay_ms, run)


class Tooltip:
    """Small hover hint for buttons and pills."""

    def __init__(self, widget, text: str, delay_ms: int = 450):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def set_text(self, text: str) -> None:
        self.text = text

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self) -> None:
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self) -> None:
        self._after_id = None
        try:
            if self._tip is not None or not self.widget.winfo_exists() or not self.text:
                return
            x = self.widget.winfo_rootx() + 6
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            tip = tk.Toplevel(self.widget)
            tip.wm_overrideredirect(True)
            tip.attributes("-topmost", True)
            tk.Label(
                tip, text=self.text, justify="left", wraplength=380, padx=9, pady=5,
                bg=COLORS["bg_card_alt"], fg=COLORS["text_primary"], font=(UI_FONT, 9),
                highlightthickness=1, highlightbackground=COLORS["border_accent"], bd=0,
            ).pack()
            tip.wm_geometry(f"+{x}+{y}")
            self._tip = tip
        except Exception:
            self._tip = None

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


def apply_app_theme() -> None:
    """One visual system for every widget that doesn't set its own style.

    CustomTkinter's stock theme uses the "Roboto" font (not installed on Windows, so Tk falls
    back to a different face and size) plus blue/grey widgets. Setting the defaults here makes
    buttons, dropdowns, entries, checkboxes, scrollbars and tabs consistent across the main
    window and the Media Inspector, Kodi and Media Server panels.
    Must run before the first widget is created.
    """
    C = COLORS
    t = ctk.ThemeManager.theme

    def pair(color: str) -> list[str]:
        return [color, color]  # [light, dark]: the app always runs in dark mode

    t["CTkFont"].update(family=UI_FONT, size=12, weight="normal")
    t["CTk"]["fg_color"] = pair(C["bg_root"])
    t["CTkToplevel"]["fg_color"] = pair(C["bg_root"])
    t["CTkFrame"].update(fg_color=pair(C["bg_panel"]), top_fg_color=pair(C["bg_card"]), border_color=pair(C["border"]))
    t["CTkLabel"]["text_color"] = pair(C["text_primary"])
    t["CTkButton"].update(
        corner_radius=8, border_width=0, fg_color=pair(C["bg_card_alt"]), hover_color=pair("#1C3542"),
        border_color=pair(C["border"]), text_color=pair(C["text_primary"]), text_color_disabled=pair(C["text_muted"]),
    )
    t["CTkEntry"].update(
        corner_radius=8, border_width=1, fg_color=pair(C["bg_card"]), border_color=pair(C["border"]),
        text_color=pair(C["text_primary"]), placeholder_text_color=pair(C["text_muted"]),
    )
    t["CTkOptionMenu"].update(
        corner_radius=8, fg_color=pair(C["bg_card"]), button_color=pair(C["bg_card_alt"]),
        button_hover_color=pair(C["border"]), text_color=pair(C["text_primary"]), text_color_disabled=pair(C["text_muted"]),
    )
    t["CTkComboBox"].update(
        corner_radius=8, border_width=1, fg_color=pair(C["bg_card"]), border_color=pair(C["border"]),
        button_color=pair(C["bg_card_alt"]), button_hover_color=pair(C["border"]), text_color=pair(C["text_primary"]),
    )
    t["DropdownMenu"].update(fg_color=pair(C["bg_card_alt"]), hover_color=pair(C["border"]), text_color=pair(C["text_primary"]))
    t["CTkCheckBox"].update(
        corner_radius=5, border_width=2, fg_color=pair(C["accent"]), border_color=pair(C["text_muted"]),
        hover_color=pair(C["accent_hover"]), checkmark_color=pair(C["bg_root"]), text_color=pair(C["text_primary"]),
    )
    t["CTkSwitch"].update(fg_color=pair(C["border"]), progress_color=pair(C["accent"]), button_color=pair(C["text_primary"]),
                          button_hover_color=pair("#FFFFFF"), text_color=pair(C["text_primary"]))
    t["CTkRadioButton"].update(fg_color=pair(C["accent"]), border_color=pair(C["text_muted"]),
                               hover_color=pair(C["accent_hover"]), text_color=pair(C["text_primary"]))
    t["CTkProgressBar"].update(fg_color=pair(C["bg_card"]), progress_color=pair(C["accent"]))
    t["CTkSlider"].update(fg_color=pair(C["border"]), progress_color=pair(C["accent"]),
                          button_color=pair(C["accent"]), button_hover_color=pair(C["accent_hover"]))
    t["CTkScrollbar"].update(button_color=pair(C["border"]), button_hover_color=pair("#24505F"))
    t["CTkSegmentedButton"].update(
        corner_radius=8, border_width=3, fg_color=pair(C["bg_panel"]), selected_color=pair(C["accent"]),
        selected_hover_color=pair(C["accent_hover"]), unselected_color=pair(C["bg_panel"]),
        unselected_hover_color=pair(C["bg_card_alt"]), text_color=pair(C["text_primary"]),
    )
    t["CTkTextbox"].update(
        corner_radius=8, fg_color=pair(C["bg_card"]), border_color=pair(C["border"]), text_color=pair(C["text_primary"]),
        scrollbar_button_color=pair(C["border"]), scrollbar_button_hover_color=pair("#24505F"),
    )
    t["CTkScrollableFrame"]["label_fg_color"] = pair(C["bg_card"])


def get_status_style(status_text: str) -> tuple[str, str]:
    s = (status_text or "").strip().lower()
    if s in ("downloading", "completed", "ok", "ready", "online"):
        return COLORS["green"], s.upper()
    if s in ("paused", "warning"):
        return COLORS["amber"], s.upper()
    if s in ("failed", "error", "offline"):
        return COLORS["red"], s.upper()
    if s in ("queued", "grabbing", "fetching", "verifying", "repairing", "extracting"):
        return COLORS["sky"], s.upper()
    return COLORS["text_muted"], (s.upper() or "IDLE")


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _dpapi_funcs():
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_wchar_p,
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = ctypes.wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = ctypes.wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


_DPAPI_FAILURES = 0  # incremented when a secret had to be saved unencrypted


def _protect_secret(plaintext: str) -> str:
    global _DPAPI_FAILURES
    if not plaintext:
        return ""
    if _SYS != "Windows":
        return plaintext
    try:
        raw = plaintext.encode("utf-8")
        buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        in_blob = _DATA_BLOB(len(raw), buf)
        out_blob = _DATA_BLOB()
        crypt32, kernel32 = _dpapi_funcs()
        if crypt32.CryptProtectData(
            ctypes.byref(in_blob), "NZBCommandCenter", None, None, None, 0, ctypes.byref(out_blob)
        ):
            try:
                encrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
                return "dpapi:" + base64.b64encode(encrypted).decode("ascii")
            finally:
                kernel32.LocalFree(out_blob.pbData)
    except Exception:
        pass
    _DPAPI_FAILURES += 1  # the caller warns: this secret is about to be written in plain text
    return plaintext


def _unprotect_secret_checked(stored: str) -> tuple[str, bool]:
    if not stored:
        return "", False
    if not stored.startswith("dpapi:"):
        return stored, False
    if _SYS != "Windows":
        return "", True
    try:
        raw = base64.b64decode(stored[6:])
        buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        in_blob = _DATA_BLOB(len(raw), buf)
        out_blob = _DATA_BLOB()
        crypt32, kernel32 = _dpapi_funcs()
        if crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
        ):
            try:
                decrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
                return decrypted.decode("utf-8"), False
            finally:
                kernel32.LocalFree(out_blob.pbData)
    except Exception:
        pass
    return "", True


def _is_remote_http(url: str) -> bool:
    parsed = urllib.parse.urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "http" and host not in ("", "localhost", "127.0.0.1", "::1")


def redact_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw or "?" not in raw:
        return raw
    try:
        parts = urllib.parse.urlsplit(raw)
        sensitive = {"apikey", "api_key", "key", "r", "passkey", "token"}
        pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        masked = [(k, "***" if k.lower() in sensitive and v else v) for k, v in pairs]
        new_query = urllib.parse.urlencode(masked)
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))
    except Exception:
        return redact_text(raw)


def redact_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(
        r"([?&](?:apikey|api_key|key|r|passkey|token)=)[^&\s'\"]+",
        r"\1***",
        str(text),
        flags=re.I,
    )


def _format_http_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        detail = ""
        try:
            body = exc.read(512).decode("utf-8", errors="replace").strip()
            if body.startswith("{"):
                j = json.loads(body)
                detail = str(j.get("error") or j.get("message") or "")
            elif "<error" in body.lower():
                m = re.search(r'description=["\']([^"\']+)["\']', body, re.I)
                if m:
                    detail = m.group(1)
        except Exception:
            pass
        reason = detail or getattr(exc, "reason", "HTTP Error")
        return redact_text(f"HTTP {exc.code}: {reason}")
    return redact_text(str(exc))


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


def safe_float(val, default: float = 0.0) -> float:
    try:
        s = str(val or "").replace("%", "").strip()
        return float(s) if s else default
    except (TypeError, ValueError):
        return default


def unique_nzb_path(folder: Path, base_title: str) -> Path:
    safe = "".join(c if c.isalnum() or c in " ._-()" else "_" for c in base_title).strip()
    safe = (safe[:120] or "release").rstrip(" .")
    if safe.lower().endswith(".nzb"):
        safe = safe[:-4]
    candidate = folder / f"{safe}.nzb"
    counter = 2
    while candidate.exists():
        candidate = folder / f"{safe} ({counter}).nzb"
        counter += 1
    return candidate


FALLBACK_INDEXER_CATEGORIES = [
    ("All Categories", ""),
    ("Console (1000)", "1000"),
    ("  ↳ Console > NDS (1010)", "1010"),
    ("  ↳ Console > PSP (1020)", "1020"),
    ("  ↳ Console > Wii (1030)", "1030"),
    ("  ↳ Console > Xbox (1040)", "1040"),
    ("  ↳ Console > Xbox 360 (1050)", "1050"),
    ("  ↳ Console > WiiWare (1060)", "1060"),
    ("  ↳ Console > Xbox 360 DLC (1070)", "1070"),
    ("  ↳ Console > PS3 (1080)", "1080"),
    ("  ↳ Console > Other (1090)", "1090"),
    ("Movies (2000)", "2000"),
    ("  ↳ Movies > Foreign (2010)", "2010"),
    ("  ↳ Movies > Other (2020)", "2020"),
    ("  ↳ Movies > SD (2030)", "2030"),
    ("  ↳ Movies > HD (2040)", "2040"),
    ("  ↳ Movies > UHD (2045)", "2045"),
    ("  ↳ Movies > BluRay (2050)", "2050"),
    ("  ↳ Movies > 3D (2060)", "2060"),
    ("  ↳ Movies > DVD (2070)", "2070"),
    ("Audio (3000)", "3000"),
    ("  ↳ Audio > MP3 (3010)", "3010"),
    ("  ↳ Audio > Video (3020)", "3020"),
    ("  ↳ Audio > Audiobook (3030)", "3030"),
    ("  ↳ Audio > Lossless (3040)", "3040"),
    ("  ↳ Audio > Other (3050)", "3050"),
    ("PC (4000)", "4000"),
    ("  ↳ PC > 0day (4010)", "4010"),
    ("  ↳ PC > ISO (4020)", "4020"),
    ("  ↳ PC > Mac (4030)", "4030"),
    ("  ↳ PC > Mobile-Other (4040)", "4040"),
    ("  ↳ PC > Games (4050)", "4050"),
    ("  ↳ PC > Mobile-iOS (4060)", "4060"),
    ("  ↳ PC > Mobile-Android (4070)", "4070"),
    ("TV (5000)", "5000"),
    ("  ↳ TV > Foreign (5020)", "5020"),
    ("  ↳ TV > SD (5030)", "5030"),
    ("  ↳ TV > HD (5040)", "5040"),
    ("  ↳ TV > UHD (5045)", "5045"),
    ("  ↳ TV > Other (5050)", "5050"),
    ("  ↳ TV > Sport (5060)", "5060"),
    ("  ↳ TV > Anime (5070)", "5070"),
    ("  ↳ TV > Documentary (5080)", "5080"),
    ("XXX (6000)", "6000"),
    ("  ↳ XXX > DVD (6010)", "6010"),
    ("  ↳ XXX > WMV (6020)", "6020"),
    ("  ↳ XXX > XviD (6030)", "6030"),
    ("  ↳ XXX > x264 (6040)", "6040"),
    ("  ↳ XXX > Pack (6050)", "6050"),
    ("  ↳ XXX > ImgSet (6060)", "6060"),
    ("  ↳ XXX > Other (6070)", "6070"),
    ("Books / Other (7000)", "7000"),
    ("  ↳ Books > Magazines (7010)", "7010"),
    ("  ↳ Books > EBook (7020)", "7020"),
    ("  ↳ Books > Comics (7030)", "7030"),
    ("Other (8000)", "8000"),
    ("  ↳ Other > Misc (8010)", "8010"),
]

LANGUAGE_RULES = [
    ("Multi", re.compile(r"(?:^|[.\s_\-\[\(])(multi|dual[.\s_-]?audio|dual)(?:$|[.\s_\-\]\)])", re.I)),
    ("French", re.compile(r"(?:^|[.\s_\-\[\(])(truefrench|french|vff|vfq|vf2)(?:$|[.\s_\-\]\)])", re.I)),
    ("German", re.compile(r"(?:^|[.\s_\-\[\(])(german|deutsch)(?:$|[.\s_\-\]\)])", re.I)),
    ("Spanish", re.compile(r"(?:^|[.\s_\-\[\(])(spanish|espanol|castellano|latino)(?:$|[.\s_\-\]\)])", re.I)),
    ("Italian", re.compile(r"(?:^|[.\s_\-\[\(])(italian|ita)(?:$|[.\s_\-\]\)])", re.I)),
    ("Portuguese", re.compile(r"(?:^|[.\s_\-\[\(])(portuguese|pt-br|dublado)(?:$|[.\s_\-\]\)])", re.I)),
    ("Japanese", re.compile(r"(?:^|[.\s_\-\[\(])(japanese|jpn)(?:$|[.\s_\-\]\)])", re.I)),
    ("Korean", re.compile(r"(?:^|[.\s_\-\[\(])(korean|kor)(?:$|[.\s_\-\]\)])", re.I)),
    ("Hindi / Indian", re.compile(r"(?:^|[.\s_\-\[\(])(hindi|tamil|telugu|malayalam|kannada)(?:$|[.\s_\-\]\)])", re.I)),
    ("Russian", re.compile(r"(?:^|[.\s_\-\[\(])(russian|rus)(?:$|[.\s_\-\]\)])", re.I)),
    ("Dutch", re.compile(r"(?:^|[.\s_\-\[\(])(dutch|flemish|nl)(?:$|[.\s_\-\]\)])", re.I)),
    ("Nordic", re.compile(r"(?:^|[.\s_\-\[\(])(nordic|swedish|danish|norwegian|finnish)(?:$|[.\s_\-\]\)])", re.I)),
    ("Polish", re.compile(r"(?:^|[.\s_\-\[\(])(polish|pl)(?:$|[.\s_\-\]\)])", re.I)),
    ("Chinese", re.compile(r"(?:^|[.\s_\-\[\(])(chinese|mandarin|cantonese)(?:$|[.\s_\-\]\)])", re.I)),
    ("English", re.compile(r"(?:^|[.\s_\-\[\(])(english|eng)(?:$|[.\s_\-\]\)])", re.I)),
]

LANGUAGE_FILTER_OPTIONS = [
    "All Languages",
    "English",
    "Multi",
    "French",
    "German",
    "Spanish",
    "Italian",
    "Japanese",
    "Korean",
    "Hindi / Indian",
    "Other",
]

QUALITY_FILTER_OPTIONS = [
    "All Qualities",
    "2160p / 4K",
    "1080p",
    "720p",
    "SD / Other",
]

SIZE_FILTER_OPTIONS = [
    "All Sizes",
    "> 100 MB",
    "> 1 GB",
    "1 GB – 15 GB",
    "> 15 GB",
    "< 1 GB",
]

SORT_OPTIONS = [
    "Best Match",
    "Default (Indexer)",
    "Size: Largest First",
    "Size: Smallest First",
    "Newest First",
    "Most Grabs",
]

LIBRARY_FILTER_OPTIONS = [
    "Lib: All",
    "Missing Only",
    "Upgrades Only",
]

SAB_PRIORITY_LABELS = ["Force", "High", "Normal", "Low"]
SAB_PRIORITY_TO_VAL = {"Force": "2", "High": "1", "Normal": "0", "Low": "-1"}
SAB_VAL_TO_PRIORITY = {"2": "Force", "Force": "Force", "1": "High", "High": "High", "0": "Normal", "Normal": "Normal", "-1": "Low", "Low": "Low"}

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

NEWZNAB_NUM_DOMAIN = {
    "1": "games",
    "2": "movies",
    "3": "audio",
    "4": "software",
    "5": "tv",
    "6": "xxx",
    "7": "books",
}


def infer_domain_and_tokens(item_category: str, title: str, fallback_cat_label: str = "") -> tuple[str, set[str]]:
    cat_text = " ".join(
        p for p in (item_category, "" if fallback_cat_label == "All Categories" else fallback_cat_label) if p
    ).lower()
    cat_tokens = set(re.findall(r"[a-z0-9]+", cat_text))
    title_tokens = set(re.findall(r"[a-z0-9]+", (title or "").lower()))

    for domain, aliases in CATEGORY_ALIASES.items():
        if cat_tokens & aliases:
            return domain, (cat_tokens | title_tokens)

    for tok in cat_tokens:
        if len(tok) == 4 and tok.isdigit():
            if tok == "5070":
                return "anime", (cat_tokens | title_tokens)
            if tok == "3030":
                return "audiobooks", (cat_tokens | title_tokens)
            if tok == "4050":
                return "games", (cat_tokens | title_tokens)
            if tok[0] in NEWZNAB_NUM_DOMAIN:
                return NEWZNAB_NUM_DOMAIN[tok[0]], (cat_tokens | title_tokens)

    t = title or ""
    if re.search(r"\b(S\d{1,2}E\d{1,2}|S\d{1,2}|Season\s*\d+|\d{4}\.\d{2}\.\d{2})\b", t, re.I):
        return "tv", title_tokens
    if re.search(r"\b(audiobook|mp3|flac|lossless|320kbps|v0|web-flac)\b", t, re.I):
        return "audio", title_tokens
    if re.search(r"\b(epub|mobi|azw3|cbr|cbz)\b", t, re.I):
        return "books", title_tokens
    if re.search(r"\b(19\d\d|20\d\d)\b", t) and re.search(
        r"\b(2160p|1080p|720p|480p|4k|uhd|bluray|bdrip|brrip|web-dl|webdl|webrip|remux|x264|x265|hevc)\b",
        t,
        re.I,
    ):
        return "movies", title_tokens
    return "", (cat_tokens | title_tokens)


def match_sab_category(sab_categories: list[str], item_category: str = "", title: str = "", fallback_cat_label: str = "") -> str:
    candidates = [c for c in sab_categories if c and c not in ("*", AUTO_CAT_LABEL)]
    if not candidates:
        return "*"

    domain, all_tokens = infer_domain_and_tokens(item_category, title, fallback_cat_label)
    domains_to_try = [domain]
    if domain == "anime":
        domains_to_try.append("tv")
    elif domain == "audiobooks":
        domains_to_try.append("audio")

    for try_domain in domains_to_try:
        domain_aliases = CATEGORY_ALIASES.get(try_domain, set())
        best_cat = "*"
        best_score = 0
        for sab_cat in candidates:
            sab_low = sab_cat.strip().lower()
            sab_tokens = set(re.findall(r"[a-z0-9]+", sab_low))
            if not sab_tokens:
                continue

            score = 0
            if domain_aliases and (sab_tokens & domain_aliases or sab_low in domain_aliases):
                score += 20

            overlap = sab_tokens & all_tokens
            score += len(overlap) * 8

            unmatched = len(sab_tokens - (all_tokens | domain_aliases))
            score -= unmatched * 3

            if score > best_score:
                best_score = score
                best_cat = sab_cat

        if best_score >= 15:
            return best_cat
    return "*"


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


def format_sab_speed(raw_speed: str) -> str:
    s = (raw_speed or "").strip()
    if not s or s == "0":
        return "0 B/s"
    if s.lower().endswith("b/s"):
        return s
    if s[-1].upper() in ("K", "M", "G", "T"):
        return f"{s}B/s"
    return f"{s} B/s"


def parse_speed_to_mbps(raw_speed: str) -> float:
    s = (raw_speed or "").strip().upper().replace("B/S", "").strip()
    if not s or s == "0":
        return 0.0
    m = re.match(r"^([\d.]+)\s*([KMGT]?)$", s)
    if not m:
        return 0.0
    val = float(m.group(1))
    unit = m.group(2)
    if unit == "K":
        return val / 1024.0
    if unit == "G":
        return val * 1024.0
    if unit == "M":
        return val
    return val / (1024.0 * 1024.0)


def format_age_and_ts(pub_date: str) -> tuple[str, float]:
    if not pub_date:
        return "—", 0.0
    try:
        dt = parsedate_to_datetime(pub_date)
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        delta = now - dt
        days = delta.days
        ts = dt.timestamp()
        if days < 1:
            hours = max(1, int(delta.total_seconds() // 3600))
            return f"{hours}h ago", ts
        if days < 30:
            return f"{days}d ago", ts
        return dt.strftime("%Y-%m-%d"), ts
    except Exception:
        return pub_date[:16], 0.0


def detect_language(title: str, attr_lang: str = "") -> str:
    clean_attr = (attr_lang or "").strip()
    if clean_attr and clean_attr.lower() not in ("unknown", "none", "n/a"):
        low = clean_attr.lower()
        if low in ("en", "eng", "english"):
            return "English"
        for label, pattern in LANGUAGE_RULES:
            if pattern.search(clean_attr) or low == label.lower():
                return label
        return clean_attr.title()

    for label, pattern in LANGUAGE_RULES:
        if pattern.search(title or ""):
            return label
    return "English / Untagged"


# Plausible size ranges in GB: (min, max) per (is_tv_episode, resolution bucket)
_EXPECTED_GB = {
    (False, "2160p / 4K"): (10, 90), (False, "1080p"): (4, 25), (False, "720p"): (2, 10), (False, "SD / Other"): (0.6, 4),
    (True, "2160p / 4K"): (3, 20), (True, "1080p"): (0.8, 6), (True, "720p"): (0.4, 3), (True, "SD / Other"): (0.1, 1.2),
}


def score_release(item: dict, in_queue: bool = False, downloaded: bool = False) -> tuple[int, list[str]]:
    """Scores a search result 0-100 for "Best Match", with the reasons behind the score.

    Quality (resolution, source, codec, HDR, repack) earns points; a size that's implausibly small
    for its resolution (fakes, broken encodes), passworded archives, and things you already have
    or have queued lose points. Grabs add a little confidence.
    """
    t = item.get("title", "")
    pts = 0
    why: list[str] = []

    quality = item.get("quality") or detect_quality_bucket(t)
    res_pts = {"2160p / 4K": 30, "1080p": 24, "720p": 12}.get(quality, 4)
    pts += res_pts
    why.append(f"{quality} +{res_pts}")

    if re.search(r"\bremux\b", t, re.I):
        src, sp = "REMUX", 20
    elif re.search(r"\b(bluray|blu-ray|bdrip|brrip)\b", t, re.I):
        src, sp = "BluRay", 16
    elif re.search(r"\b(web-dl|webdl)\b", t, re.I):
        src, sp = "WEB-DL", 14
    elif re.search(r"\b(webrip|web)\b", t, re.I):
        src, sp = "WEBRip", 11
    elif re.search(r"\bhdtv\b", t, re.I):
        src, sp = "HDTV", 6
    elif re.search(r"\b(cam|hdcam|ts|telesync|hdts)\b", t, re.I):
        src, sp = "CAM/TS", -25
    else:
        src, sp = "unknown source", 6
    pts += sp
    why.append(f"{src} {sp:+d}")

    if re.search(r"\b(x265|h\.?265|hevc|av1)\b", t, re.I):
        pts += 6
        why.append("HEVC/AV1 +6")
    elif re.search(r"\b(x264|h\.?264|avc)\b", t, re.I):
        pts += 4
        why.append("x264 +4")
    if re.search(r"\b(dv|dovi|dolby[.\s_-]?vision|hdr10\+?|hdr)\b", t, re.I):
        pts += 4
        why.append("HDR +4")
    if re.search(r"\b(proper|repack)\b", t, re.I):
        pts += 2
        why.append("repack +2")

    verdict = size_verdict(item)
    if verdict == "tiny":
        pts -= 25
        why.append("suspiciously small -25")
    elif verdict == "small":
        pts -= 6
        why.append("small for quality -6")
    elif verdict == "large":
        pts -= 4
        why.append("very large -4")

    grabs = int(item.get("grabs_int") or 0)
    if grabs > 0:
        gp = min(10, round(math.log10(grabs + 1) * 4))
        pts += gp
        why.append(f"{grabs} grabs +{gp}")

    if item.get("passworded"):
        pts -= 40
        why.append("passworded -40")

    lib = item.get("_lib_match") or {}
    status = lib.get("status")
    if in_queue:
        pts -= 50
        why.append("already in queue -50")
    elif downloaded:
        pts -= 40
        why.append("already downloaded -40")
    elif status in ("owned", "have_better"):
        pts -= 30
        why.append("already in library -30")
    elif status in ("upgrade", "corrupt"):
        pts += 5
        why.append("upgrade/replacement +5")

    return max(0, min(100, pts)), why


def size_verdict(item: dict) -> str:
    """How a release's size compares with what its resolution normally needs.

    "tiny" (under half the usual minimum: likely fake/broken), "small", "large" (well above the
    usual maximum, e.g. remuxes), "ok", or "" when it can't be judged (season packs, unknown size).
    """
    t = item.get("title", "")
    quality = item.get("quality") or detect_quality_bucket(t)
    size_gb = float(item.get("size_bytes") or 0) / 1024 ** 3
    tv = parse_release_identity_is_tv(t)
    is_episode = tv and bool(re.search(r"\bS\d{1,2}[\s._-]?E\d{1,3}\b|\b\d{1,2}x\d{2}\b", t, re.I))
    if size_gb <= 0 or (tv and not is_episode):
        return ""
    lo, hi = _EXPECTED_GB.get((is_episode, quality), (0, 10 ** 6))
    if size_gb < lo * 0.5:
        return "tiny"
    if size_gb < lo:
        return "small"
    if size_gb > hi * 1.5:
        return "large"
    return "ok"


SIZE_VERDICT_STYLE = {
    "tiny": ("red", "Suspiciously small for its resolution: likely fake or broken."),
    "small": ("amber", "Smaller than usual for its resolution."),
    "large": ("purple", "Much larger than usual (e.g. a remux)."),
    "ok": ("text_secondary", "Typical size for its resolution."),
    "": ("text_secondary", ""),
}


def release_kind(item: dict) -> tuple[str, str, str]:
    """(icon name, colour key, short label) describing what a release is, for artwork placeholders."""
    cat = f"{item.get('category', '')} {item.get('cat_hint', '')}".lower()
    title = item.get("title", "")
    if re.search(r"audio|music|mp3|flac|lossless", cat):
        return "music", "purple", "MUSIC"
    if re.search(r"book|comic|magazine", cat):
        return "book", "amber", "BOOK"
    if re.search(r"console|game", cat):
        return "game", "red", "GAME"
    if re.search(r"\bpc\b|\bapps?\b|software", cat):
        return "app", "sky", "APP"
    if "tv" in cat or parse_release_identity_is_tv(title):
        return "tv", "sky", "TV"
    if "movie" in cat or re.search(r"\b(19|20)\d{2}\b", title):
        return "movie", "accent", "MOVIE"
    return "package", "text_muted", "NZB"


def _details_link(*candidates: str) -> str:
    """First http(s) link among an item's comments/guid fields: the release page on the indexer."""
    for c in candidates:
        c = (c or "").strip()
        if c.startswith(("http://", "https://")):
            return c.split("#", 1)[0]
    return ""


def parse_release_identity_is_tv(title: str) -> bool:
    return bool(re.search(r"\b(S\d{1,2}([\s._-]?E\d{1,3})?|\d{1,2}x\d{2}|Season[\s._-]?\d+)\b", title or "", re.I))


_TV_QUERY_RE = re.compile(
    r"^(?P<show>.+?)[\s._-]+(?:S(?P<s>\d{1,2})(?:[\s._-]?E(?P<e>\d{1,3}))?|(?P<s2>\d{1,2})x(?P<e2>\d{1,3}))\s*$",
    re.I,
)


def parse_tv_query(query: str) -> dict | None:
    """'The Bear S03E01' -> show/season/episode; 'The Bear S03' -> season only; else None."""
    m = _TV_QUERY_RE.match((query or "").strip())
    if not m:
        return None
    show = m.group("show").replace(".", " ").replace("_", " ").strip(" -")
    season = m.group("s") or m.group("s2")
    episode = m.group("e") or m.group("e2")
    if not show or season is None:
        return None
    return {"show": show, "season": int(season), "episode": int(episode) if episode else None}


def normalize_release_key(title: str) -> str:
    clean = re.sub(r"\.nzb$", "", (title or "").strip(), flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", clean.lower())


def detect_quality_bucket(title: str) -> str:
    t = title or ""
    if re.search(r"\b(2160p|4k|uhd)\b", t, re.I):
        return "2160p / 4K"
    if re.search(r"\b1080p\b", t, re.I):
        return "1080p"
    if re.search(r"\b720p\b", t, re.I):
        return "720p"
    return "SD / Other"


def parse_release_tags(title: str) -> list[tuple[str, str]]:
    t = title or ""
    tags: list[tuple[str, str]] = []
    if re.search(r"\b(2160p|4k|uhd)\b", t, re.I):
        tags.append(("2160p", COLORS["purple"]))
    elif re.search(r"\b1080p\b", t, re.I):
        tags.append(("1080p", COLORS["accent"]))
    elif re.search(r"\b720p\b", t, re.I):
        tags.append(("720p", COLORS["sky"]))

    if re.search(r"\b(dv|dovi|dolby[.\s_-]?vision)\b", t, re.I):
        tags.append(("DV", COLORS["amber"]))
    elif re.search(r"\b(hdr10\+|hdr10|hdr)\b", t, re.I):
        tags.append(("HDR", COLORS["amber"]))

    if re.search(r"\b(x265|h\.?265|hevc)\b", t, re.I):
        tags.append(("HEVC", COLORS["text_secondary"]))
    elif re.search(r"\b(x264|h\.?264)\b", t, re.I):
        tags.append(("x264", COLORS["text_secondary"]))
    elif re.search(r"\bav1\b", t, re.I):
        tags.append(("AV1", COLORS["text_secondary"]))

    if re.search(r"\bremux\b", t, re.I):
        tags.append(("REMUX", COLORS["purple"]))
    elif re.search(r"\b(bluray|bdrip|brrip)\b", t, re.I):
        tags.append(("BluRay", COLORS["sky"]))
    elif re.search(r"\b(web-dl|webdl|webrip)\b", t, re.I):
        tags.append(("WEB", COLORS["text_secondary"]))

    if re.search(r"\b(proper|repack)\b", t, re.I):
        tags.append(("REPACK", COLORS["green"]))
    return tags[:4]


def parse_title_for_artwork(title: str, category: str = "") -> tuple[str, str, str]:
    raw = (title or "").replace(".", " ").replace("_", " ").strip()

    tv_match = re.search(r"\b(S\d{1,2}E\d{1,2}|S\d{1,2}|Season\s*\d+)\b", raw, re.I)
    if tv_match or "tv" in (category or "").lower():
        clean = raw[: tv_match.start()].strip(" -") if tv_match else raw
        clean = re.sub(r"\b(19\d\d|20\d\d)\b", "", clean).strip()
        return clean, "", "tv"

    year_match = re.search(r"\b(19\d\d|20\d\d)\b", raw)
    if year_match:
        year = year_match.group(1)
        clean = raw[: year_match.start()].strip(" -([")
        return clean, year, "movie"

    clean = re.split(
        r"\b(2160p|1080p|720p|480p|4k|uhd|bluray|bdrip|web-dl|webdl|webrip|hdtv|hdr|x264|x265|hevc|remux)\b",
        raw,
        maxsplit=1,
        flags=re.I,
    )[0].strip(" -([")
    return clean or raw, "", "movie"


def _sanitize_xml(raw_bytes: bytes) -> str:
    text = raw_bytes.decode("utf-8", errors="replace")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)", "&amp;", text)
    return text


def _http_get_limited(url: str, timeout: float, max_bytes: int) -> bytes:
    """GET with a hard size cap, so a broken or hostile server can't exhaust memory."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise RuntimeError(f"Response exceeds the {max_bytes // (1024 * 1024)} MB safety limit.")
    return raw


def _gunzip_limited(payload: bytes) -> bytes:
    """Some indexers serve NZBs gzip-compressed; decompress with the same size cap."""
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as gz:
            out = gz.read(MAX_NZB_BYTES + 1)
    except (OSError, EOFError, zlib.error):
        raise RuntimeError("Indexer returned a corrupt gzip-compressed NZB.") from None
    if len(out) > MAX_NZB_BYTES:
        raise RuntimeError("Decompressed NZB exceeds the size limit.")
    return out


def _xml_tag(elem) -> str:
    """Element tag without any XML namespace, lower-case."""
    return elem.tag.split("}")[-1].lower()


def _require_complete_nzb(raw_bytes: bytes) -> None:
    """Rejects anything that isn't a whole NZB (JSON errors, empty bodies, truncated downloads)."""
    if not re.search(rb"<nzb[\s>]", raw_bytes[:8192], re.I):
        raise RuntimeError("Indexer returned data that is not an NZB file.")
    if not raw_bytes[-512:].rstrip().lower().endswith(b"</nzb>"):
        raise RuntimeError("Indexer returned a truncated NZB file.")


def _validate_nzb_payload(raw_bytes: bytes) -> None:
    preview = raw_bytes[:512].decode("utf-8", errors="replace").strip()
    low = preview.lower()
    if low.startswith(("<!doctype html", "<html")):
        raise RuntimeError("Server returned HTML instead of an NZB file (check API key or rate limit).")
    if "<error" in low and "<nzb" not in low:
        try:
            root = ET.fromstring(_sanitize_xml(raw_bytes))
            if root.tag.split("}")[-1].lower() == "error":
                desc = root.attrib.get("description", "Unknown indexer error.")
                raise RuntimeError(f"Indexer error {root.attrib.get('code', '')}: {desc}")
        except ET.ParseError:
            pass


def parse_nzb_bytes(raw_bytes: bytes, name: str, source_label: str = "") -> dict:
    _validate_nzb_payload(raw_bytes)
    root = ET.fromstring(_sanitize_xml(raw_bytes))
    if root.tag.split("}")[-1].lower() == "error":
        desc = root.attrib.get("description", "Unknown indexer error.")
        raise RuntimeError(f"Indexer error {root.attrib.get('code', '')}: {desc}")

    files_info: list[dict] = []
    groups: set[str] = set()
    total_payload = 0

    for f_elem in root:
        if not f_elem.tag.endswith("file"):
            continue
        subject = f_elem.attrib.get("subject", "Unknown")
        poster = f_elem.attrib.get("poster", "")
        file_bytes = 0

        for child in f_elem:
            if child.tag.endswith("groups"):
                for g in child:
                    if g.text:
                        groups.add(g.text.strip())
            elif child.tag.endswith("segments"):
                for seg in child:
                    try:
                        file_bytes += int(seg.attrib.get("bytes", "0"))
                    except ValueError:
                        pass

        total_payload += file_bytes
        files_info.append({
            "subject": subject,
            "poster": poster,
            "size_bytes": file_bytes,
            "size_human": format_bytes(file_bytes),
        })

    return {
        "name": name,
        "source": redact_url(source_label) if source_label else name,
        "disk_size": format_bytes(len(raw_bytes)),
        "payload_bytes": total_payload,
        "payload_human": format_bytes(total_payload),
        "file_count": len(files_info),
        "groups": sorted(groups),
        "files": files_info,
        "raw_xml": raw_bytes.decode("utf-8", errors="replace"),
    }


def parse_local_nzb(file_path: Path) -> dict:
    return parse_nzb_bytes(file_path.read_bytes(), file_path.name, str(file_path))


# ==============================================================================
# 2. ARTWORK FETCHER & LRU CACHE (Deduplicated, Size-Capped, Shutdown-Safe)
# ==============================================================================

class ArtworkCache:
    def __init__(self, size: tuple[int, int] = (58, 84), max_entries: int = MAX_ARTWORK_CACHE):
        self.size = size
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._shutdown = False
        self._cache: OrderedDict[str, object] = OrderedDict()
        self._failed_until: dict[str, float] = {}
        self._in_flight: dict[str, list] = {}
        self._executor = ThreadPoolExecutor(max_workers=4)

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
            self._in_flight.clear()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _download_image_bytes(self, url: str) -> bytes | None:
        if not url or not url.startswith(("http://", "https://")) or self._shutdown:
            return None
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 NZBCommandCenter/1.0"})
        with urllib.request.urlopen(req, timeout=6.0) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "image" not in ctype and not any(url.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
                return None
            raw = resp.read(MAX_ARTWORK_BYTES + 1)
            if len(raw) > MAX_ARTWORK_BYTES:
                return None
            return raw

    def _lookup_poster_url(self, clean_name: str, year: str, media_hint: str) -> str:
        if not clean_name or self._shutdown:
            return ""
        try:
            if media_hint == "tv":
                url = f"https://api.tvmaze.com/singlesearch/shows?{urllib.parse.urlencode({'q': clean_name})}"
                req = urllib.request.Request(url, headers={"User-Agent": "NZBCommandCenter/1.0"})
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    data = json.loads(resp.read().decode("utf-8", errors="replace"))
                img = data.get("image") or {}
                return img.get("medium") or img.get("original") or ""
            else:
                term = f"{clean_name} {year}".strip()
                params = urllib.parse.urlencode({"term": term, "media": "movie", "limit": 1})
                url = f"https://itunes.apple.com/search?{params}"
                req = urllib.request.Request(url, headers={"User-Agent": "NZBCommandCenter/1.0"})
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    data = json.loads(resp.read().decode("utf-8", errors="replace"))
                results = data.get("results") or []
                if results:
                    art = results[0].get("artworkUrl100") or ""
                    return art.replace("100x100bb", "200x200bb")
        except Exception:
            pass
        return ""

    def get_or_fetch(self, item: dict, callback) -> None:
        if not PIL_AVAILABLE or self._shutdown:
            return

        cover_url = item.get("cover_url") or ""
        clean_name, year, media_hint = parse_title_for_artwork(item.get("title", ""), item.get("category", ""))
        cache_key = cover_url if cover_url else f"{media_hint}:{clean_name.lower()}:{year}"

        with self._lock:
            if self._shutdown:
                return
            if cache_key in self._cache:
                self._cache.move_to_end(cache_key)
                callback(self._cache[cache_key])
                return
            if self._failed_until.get(cache_key, 0.0) > time.monotonic():
                return
            if cache_key in self._in_flight:
                self._in_flight[cache_key].append(callback)
                return
            self._in_flight[cache_key] = [callback]

        def task():
            ctk_img = None
            try:
                if not self._shutdown:
                    target_url = cover_url or self._lookup_poster_url(clean_name, year, media_hint)
                    raw = self._download_image_bytes(target_url) if target_url else None
                    if raw and not self._shutdown:
                        pil_img = Image.open(io.BytesIO(raw)).convert("RGB")
                        ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=self.size)
            except Exception:
                ctk_img = None

            with self._lock:
                waiters = self._in_flight.pop(cache_key, [])
                if self._shutdown:
                    return
                if ctk_img is not None:
                    self._cache[cache_key] = ctk_img
                    self._cache.move_to_end(cache_key)
                    while len(self._cache) > self.max_entries:
                        self._cache.popitem(last=False)
                    self._failed_until.pop(cache_key, None)
                else:
                    self._failed_until[cache_key] = time.monotonic() + ARTWORK_FAIL_TTL_SEC

            if ctk_img is not None:
                for cb in waiters:
                    cb(ctk_img)

        self._executor.submit(task)


# ==============================================================================
# 3. SABNZBD API CLIENT
# ==============================================================================

class SabnzbdClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = self._normalize_url(base_url)
        self.api_key = api_key.strip()

    @staticmethod
    def _normalize_url(url: str) -> str:
        clean = (url or "").strip().rstrip("/")
        if clean and not clean.startswith(("http://", "https://")):
            clean = f"http://{clean}"
        if clean.endswith("/api"):
            clean = clean[:-4]
        return clean

    def _request(self, mode: str, extra_params: dict | None = None, timeout: float = 10.0) -> dict:
        if not self.base_url or not self.api_key:
            raise ValueError("SABnzbd URL and API key are required in Settings.")

        params = {"mode": mode, "output": "json", "apikey": self.api_key}
        if extra_params:
            params.update({k: v for k, v in extra_params.items() if v is not None})

        query = urllib.parse.urlencode(params)
        url = f"{self.base_url}/api?{query}"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            raise RuntimeError(_format_http_error(exc)) from exc

        if isinstance(data, dict) and data.get("status") is False:
            raise RuntimeError(data.get("error") or "SABnzbd returned an error.")
        return data

    def test_connection(self) -> tuple[str, int]:
        t0 = time.monotonic()
        q = self.get_queue()
        version = q.get("version") or "Connected"
        slots = q.get("slots") or []
        ms = int((time.monotonic() - t0) * 1000)
        return f"SABnzbd {version} ({len(slots)} queued)", ms

    def get_queue(self) -> dict:
        return self._request("queue").get("queue", {})

    def get_history(self, limit: int = 30) -> dict:
        return self._request("history", {"limit": limit}).get("history", {})

    def get_categories(self) -> list[str]:
        data = self._request("get_cats")
        cats = data.get("categories") or ["*"]
        return [str(c) for c in cats]

    def pause_all(self) -> None:
        self._request("pause")

    def resume_all(self) -> None:
        self._request("resume")

    def pause_item(self, nzo_id: str) -> None:
        self._request("queue", {"name": "pause", "value": nzo_id})

    def resume_item(self, nzo_id: str) -> None:
        self._request("queue", {"name": "resume", "value": nzo_id})

    def move_queue_item_top(self, nzo_id: str) -> None:
        self._request("switch", {"value": nzo_id, "value2": "0"})

    def move_queue_item(self, nzo_id: str, position: int) -> None:
        self._request("switch", {"value": nzo_id, "value2": str(max(0, int(position)))})

    def set_priority(self, nzo_id: str, priority_val: str) -> None:
        self._request("queue", {"name": "priority", "value": nzo_id, "value2": priority_val})

    def change_category(self, nzo_id: str, category: str) -> None:
        self._request("change_cat", {"value": nzo_id, "value2": category})

    def delete_queue_item(self, nzo_id: str) -> None:
        self._request("queue", {"name": "delete", "value": nzo_id})

    def retry_history_item(self, nzo_id: str) -> None:
        self._request("retry", {"value": nzo_id})

    def delete_history_item(self, nzo_id: str) -> None:
        self._request("history", {"name": "delete", "value": nzo_id})

    def clear_history(self, failed_only: bool = False) -> None:
        self._request("history", {"name": "delete", "value": "failed" if failed_only else "all"})

    def set_speed_limit(self, value: str) -> None:
        self._request("config", {"name": "speedlimit", "value": value})

    def add_url(self, nzb_url: str, nzb_name: str = "", category: str = "*") -> dict:
        params = {"name": nzb_url}
        if nzb_name:
            params["nzbname"] = nzb_name
        if category and category not in ("*", AUTO_CAT_LABEL):
            params["cat"] = category
        return self._request("addurl", params, timeout=15.0)

    def add_file(self, file_path: Path, category: str = "*") -> dict:
        if not self.base_url or not self.api_key:
            raise ValueError("SABnzbd URL and API key are required in Settings.")

        raw = file_path.read_bytes()
        boundary = f"----NZBManager{uuid.uuid4().hex}"
        safe_name = file_path.name.replace('"', "")
        mime = mimetypes.guess_type(safe_name)[0] or "application/x-nzb"

        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="nzbfile"; filename="{safe_name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8") + raw + f"\r\n--{boundary}--\r\n".encode("utf-8")

        params = {"mode": "addfile", "output": "json", "apikey": self.api_key}
        if category and category not in ("*", AUTO_CAT_LABEL):
            params["cat"] = category

        url = f"{self.base_url}/api?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20.0) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            raise RuntimeError(_format_http_error(exc)) from exc

        if isinstance(data, dict) and data.get("status") is False:
            raise RuntimeError(data.get("error") or "Failed to upload NZB.")
        return data


# ==============================================================================
# 4. NEWZNAB INDEXER API CLIENT
# ==============================================================================

class NewznabClient:
    def __init__(self, base_url: str, api_key: str, name: str = "Indexer"):
        self.name = (name or "Indexer").strip()
        self.base_url = self._normalize_api_url(base_url)
        self.api_key = api_key.strip()

    @staticmethod
    def _normalize_api_url(url: str) -> str:
        clean = (url or "").strip().split("?", 1)[0].rstrip("/")
        if clean and not clean.startswith(("http://", "https://")):
            clean = f"https://{clean}"
        if "omgwtfnzbs.org/index" in clean.lower():
            clean = "https://api.omgwtfnzbs.me/api"
        elif clean and not clean.endswith(("/api", "/api.php", "/rss")):
            clean = f"{clean}/api"
        return clean

    def test_connection(self) -> tuple[int, int]:
        t0 = time.monotonic()
        cats = self.get_categories()
        if self.api_key:
            self.search("", cat_id="2000", limit=1)
        ms = int((time.monotonic() - t0) * 1000)
        return max(0, len(cats) - 1), ms

    def get_categories(self) -> list[tuple[str, str]]:
        if not self.base_url:
            raise ValueError("Indexer URL is required.")

        params = {"t": "caps"}
        if self.api_key:
            params["apikey"] = self.api_key
        url = f"{self.base_url}?{urllib.parse.urlencode(params)}"
        try:
            raw_bytes = _http_get_limited(url, timeout=15.0, max_bytes=MAX_API_BYTES)
        except Exception as exc:
            raise RuntimeError(_format_http_error(exc)) from exc

        preview = raw_bytes[:200].decode("utf-8", errors="replace").strip().lower()
        if preview.startswith(("<!doctype html", "<html")):
            raise RuntimeError(f"Indexer returned HTML from {self.base_url} instead of ?t=caps XML.")

        root = ET.fromstring(_sanitize_xml(raw_bytes))
        if _xml_tag(root) == "error":
            desc = root.attrib.get("description", "Unknown indexer API error.")
            raise RuntimeError(f"Indexer error {root.attrib.get('code', '')}: {desc}")

        cats: list[tuple[str, str]] = [("All Categories", "")]
        seen_ids: set[str] = set()

        for elem in root.iter():
            tag_name = elem.tag.split("}")[-1].lower()
            if tag_name != "category":
                continue
            cid = (elem.attrib.get("id") or "").strip()
            cname = (elem.attrib.get("name") or f"Category {cid}").strip()
            if cid and cid not in seen_ids:
                seen_ids.add(cid)
                cats.append((f"{cname} ({cid})", cid))

            for sub in elem:
                sub_tag = sub.tag.split("}")[-1].lower()
                if sub_tag != "subcat":
                    continue
                sid = (sub.attrib.get("id") or "").strip()
                sname = (sub.attrib.get("name") or f"Subcat {sid}").strip()
                if sid and sid not in seen_ids:
                    seen_ids.add(sid)
                    cats.append((f"  ↳ {cname} > {sname} ({sid})", sid))

        if len(cats) == 1:
            return list(FALLBACK_INDEXER_CATEGORIES)
        return cats

    def search(self, query: str, cat_id: str = "", limit: int = 100, offset: int = 0, mode: str = "auto") -> list[dict]:
        """Searches the indexer. Every result carries "search_mode" ("tv" or "text").

        mode="auto": a query like "The Bear S03E01" / "The Bear S03" uses the Newznab TV search
        (t=tvsearch with season/episode), which also finds "3x01", season packs and differently
        named releases. If the indexer doesn't support it (API error) or returns nothing, it falls
        back to a plain text search. Pass the mode of the first page back in for "Load more", so
        later pages come from the same kind of search. `offset` pages past the first `limit` results.
        """
        if not self.base_url or not self.api_key:
            raise ValueError(f"URL and API key are required for '{self.name}'.")

        tv = parse_tv_query(query) if mode in ("auto", "tv") else None
        if tv:
            params: dict[str, object] = {"t": "tvsearch", "apikey": self.api_key, "limit": limit, "q": tv["show"]}
            if tv["season"] is not None:
                params["season"] = tv["season"]
            if tv["episode"] is not None:
                params["ep"] = tv["episode"]
            if cat_id:
                params["cat"] = cat_id
            if offset:
                params["offset"] = offset
            try:
                results = self._search_request(params, limit)
                if results or mode == "tv":
                    for r in results:
                        r["search_mode"] = "tv"
                    return results
            except RuntimeError:
                if mode == "tv":
                    raise
                # e.g. "Function not available": fall through to the plain text search

        params = {"t": "search", "apikey": self.api_key, "limit": limit}
        if query.strip():
            params["q"] = query.strip()
        if cat_id:
            params["cat"] = cat_id
        if offset:
            params["offset"] = offset
        results = self._search_request(params, limit)
        for r in results:
            r["search_mode"] = "text"
        return results

    def _search_request(self, params: dict, limit: int) -> list[dict]:
        url = f"{self.base_url}?{urllib.parse.urlencode(params)}"
        try:
            raw_bytes = _http_get_limited(url, timeout=20.0, max_bytes=MAX_API_BYTES)
        except Exception as exc:
            raise RuntimeError(f"{self.name}: {_format_http_error(exc)}") from exc

        if raw_bytes.lstrip()[:1] in (b"{", b"["):
            # Some indexers answer in JSON regardless of format; parse it without a second API hit.
            try:
                jdata = json.loads(raw_bytes.decode("utf-8", errors="replace"))
            except ValueError as exc:
                raise RuntimeError(f"{self.name} returned unreadable JSON ({exc}).") from None
            if isinstance(jdata, dict) and jdata.get("error"):
                raise RuntimeError(f"{self.name} error: {redact_text(str(jdata['error']))[:160]}")
            if not isinstance(jdata, dict):
                raise RuntimeError(f"{self.name} returned an unexpected JSON structure.")
            return self._parse_json_results(jdata, limit)

        text_preview = raw_bytes[:300].decode("utf-8", errors="replace").strip()
        if text_preview.lower().startswith(("<!doctype html", "<html")):
            raise RuntimeError(
                f"{self.name} returned HTML instead of XML. Check URL/API key. Preview: {text_preview[:120]}"
            )

        try:
            root = ET.fromstring(_sanitize_xml(raw_bytes))
        except ET.ParseError as xml_err:
            try:
                raw_json = _http_get_limited(f"{url}&o=json", timeout=20.0, max_bytes=MAX_API_BYTES)
                jdata = json.loads(raw_json.decode("utf-8", errors="replace"))
                return self._parse_json_results(jdata, limit)
            except Exception:
                raise RuntimeError(
                    f"{self.name} XML parse failed ({xml_err}). Starts with: {text_preview[:120]}"
                ) from xml_err

        if _xml_tag(root) == "error":
            desc = root.attrib.get("description", "Unknown indexer API error.")
            raise RuntimeError(f"{self.name} error {root.attrib.get('code', '')}: {desc}")

        results: list[dict] = []
        for idx, item in enumerate(root.findall(".//item")[:limit]):
            title = (item.findtext("title") or "Untitled Release").strip()
            link = (item.findtext("link") or "").strip()
            guid_el = item.find("guid")
            details_url = _details_link(
                item.findtext("comments") or "",
                (guid_el.text or "") if guid_el is not None and guid_el.attrib.get("isPermaLink", "").lower() == "true" else "",
            )
            pub_date = (item.findtext("pubDate") or "").strip()
            category = (item.findtext("category") or "").strip()

            enclosure = item.find("enclosure")
            nzb_url = (enclosure.attrib.get("url") if enclosure is not None else "") or link
            size_bytes = int(enclosure.attrib.get("length", "0") or 0) if enclosure is not None else 0

            grabs = ""
            attr_lang = ""
            cover_url = ""
            attr_cats: list[str] = []
            passworded = False
            files_count = ""

            for child in item:
                if child.tag.endswith("attr"):
                    attr_name = child.attrib.get("name", "").lower()
                    attr_val = child.attrib.get("value", "")
                    if attr_name == "size" and attr_val.isdigit() and size_bytes == 0:
                        size_bytes = int(attr_val)
                    elif attr_name == "grabs":
                        grabs = attr_val
                    elif attr_name == "language":
                        attr_lang = attr_val
                    elif attr_name == "category" and attr_val:
                        attr_cats.append(attr_val.strip())
                    elif attr_name == "password" and str(attr_val).strip() in ("1", "2", "yes", "true"):
                        passworded = True
                    elif attr_name == "files" and attr_val.isdigit():
                        files_count = attr_val
                    elif attr_name in ("coverurl", "poster", "thumb", "backdropurl") and not cover_url:
                        cover_url = attr_val.strip()

            if not category:
                category = attr_cats[0] if attr_cats else "General"
            cat_hint = f"{category} {' '.join(attr_cats)}".strip()

            age_str, pub_ts = format_age_and_ts(pub_date)
            grabs_int = int(grabs) if grabs.isdigit() else 0

            results.append({
                "index": idx,
                "uid": f"{self.name}|{nzb_url}|{title}",
                "norm_key": normalize_release_key(title),
                "indexer_name": self.name,
                "title": title,
                "category": category,
                "cat_hint": cat_hint,
                "language": detect_language(title, attr_lang),
                "quality": detect_quality_bucket(title),
                "tags": parse_release_tags(title),
                "passworded": passworded,
                "files_count": files_count,
                "size_bytes": size_bytes,
                "size_human": format_bytes(size_bytes),
                "age": age_str,
                "pub_ts": pub_ts,
                "grabs": grabs,
                "grabs_int": grabs_int,
                "cover_url": cover_url,
                "nzb_url": nzb_url,
                "details_url": details_url,
                "mirror_count": 0,
            })
        return results

    def _parse_json_results(self, data: dict, limit: int) -> list[dict]:
        channel = data.get("channel") or data
        items = channel.get("item") or []
        if isinstance(items, dict):
            items = [items]

        results: list[dict] = []
        for idx, item in enumerate(items[:limit]):
            title = str(item.get("title") or "Untitled Release").strip()
            pub_date = str(item.get("pubDate") or "").strip()
            category = str(item.get("category") or "General").strip()
            enclosure = item.get("enclosure", {})
            enc_attrs = enclosure.get("@attributes", enclosure) if isinstance(enclosure, dict) else {}

            nzb_url = str(enc_attrs.get("url") or item.get("link") or "").strip()
            try:
                size_bytes = int(enc_attrs.get("length") or 0)
            except (TypeError, ValueError):
                size_bytes = 0

            age_str, pub_ts = format_age_and_ts(pub_date)
            results.append({
                "index": idx,
                "uid": f"{self.name}|{nzb_url}|{title}",
                "norm_key": normalize_release_key(title),
                "indexer_name": self.name,
                "title": title,
                "category": category,
                "cat_hint": category,
                "language": detect_language(title),
                "quality": detect_quality_bucket(title),
                "tags": parse_release_tags(title),
                "passworded": False,
                "files_count": "",
                "size_bytes": size_bytes,
                "size_human": format_bytes(size_bytes),
                "age": age_str,
                "pub_ts": pub_ts,
                "grabs": "",
                "grabs_int": 0,
                "cover_url": "",
                "nzb_url": nzb_url,
                "details_url": _details_link(str(item.get("comments") or ""), str(item.get("guid") or "")),
                "mirror_count": 0,
            })
        return results

    @staticmethod
    def fetch_nzb_bytes(nzb_url: str) -> bytes:
        try:
            raw = _http_get_limited(nzb_url, timeout=30.0, max_bytes=MAX_NZB_BYTES)
        except Exception as exc:
            raise RuntimeError(_format_http_error(exc)) from exc
        if not raw:
            raise RuntimeError("Indexer returned an empty NZB file.")
        if raw.startswith(b"\x1f\x8b"):
            raw = _gunzip_limited(raw)
        _validate_nzb_payload(raw)   # turns indexer <error>/HTML pages into a readable message
        _require_complete_nzb(raw)   # never save a JSON error or half a file as ".nzb"
        return raw

    @staticmethod
    def download_nzb(nzb_url: str, dest_path: Path) -> Path:
        content = NewznabClient.fetch_nzb_bytes(nzb_url)
        dest_path.write_bytes(content)
        return dest_path


# ==============================================================================
# 5. UI WIDGETS & DIALOGS (SPEED CHART, NZB VIEWER, MULTI-INDEXER SETTINGS)
# ==============================================================================

class SpeedChartWidget(ctk.CTkFrame):
    def __init__(self, master, height: int = 86):
        super().__init__(
            master,
            fg_color=COLORS["bg_panel"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=10,
        )
        self.height = height
        self._samples: list[float] = [0.0] * 24

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(8, 2))
        ctk.CTkLabel(
            top,
            text="LIVE BANDWIDTH TELEMETRY",
            font=ctk.CTkFont(family=UI_FONT, size=10, weight="bold"),
            text_color=COLORS["text_muted"],
        ).pack(side="left")
        self.peak_lbl = ctk.CTkLabel(
            top,
            text="Peak: 0.0 MB/s",
            font=ctk.CTkFont(family=UI_FONT, size=10),
            text_color=COLORS["accent"],
        )
        self.peak_lbl.pack(side="right")

        self.canvas = ctk.CTkCanvas(
            self,
            height=height,
            bg=COLORS["bg_panel"],
            highlightthickness=0,
        )
        self.canvas.pack(fill="x", padx=10, pady=(0, 8))
        self.canvas.bind("<Configure>", lambda _e: self._redraw())

    def push_sample(self, mbps: float) -> None:
        self._samples.append(max(0.0, mbps))
        if len(self._samples) > 30:
            self._samples = self._samples[-30:]
        self._redraw()

    def _redraw(self) -> None:
        if not self.winfo_exists():
            return
        c = self.canvas
        c.delete("all")
        w = max(c.winfo_width(), 200)
        h = max(c.winfo_height(), self.height)

        for frac in (0.25, 0.5, 0.75):
            y = h * frac
            c.create_line(0, y, w, y, fill=COLORS["grid_line"], width=1)
        cols = 10
        for i in range(1, cols):
            x = (w / cols) * i
            c.create_line(x, 0, x, h, fill=COLORS["grid_line"], width=1)

        peak = max(self._samples) if self._samples else 0.0
        self.peak_lbl.configure(text=f"Peak: {peak:.1f} MB/s")
        scale_max = max(peak * 1.18, 1.0)

        n = len(self._samples)
        if n < 2:
            return

        pad_y = 6
        pts: list[float] = []
        for idx, val in enumerate(self._samples):
            x = (idx / (n - 1)) * (w - 8) + 4
            y = h - pad_y - (val / scale_max) * (h - pad_y * 2)
            pts.extend([x, y])

        poly = [pts[0], h - 2] + pts + [pts[-2], h - 2]
        c.create_polygon(poly, fill="#0D2B33", outline="")
        c.create_line(*pts, fill=COLORS["accent"], width=2.2, smooth=True)
        lx, ly = pts[-2], pts[-1]
        c.create_oval(lx - 3.5, ly - 3.5, lx + 3.5, ly + 3.5, fill=COLORS["accent_hover"], outline="")


class NzbViewerDialog(ctk.CTkToplevel):
    def __init__(self, master, title: str, meta: dict):
        super().__init__(master)
        self.title(f"NZB Viewer — {title}")
        self.geometry("820x580")
        self.minsize(640, 440)
        apply_window_chrome(self)
        self.configure(fg_color=COLORS["bg_root"])
        self.transient(master)
        self.bind("<Escape>", lambda _e: self.destroy())

        self.meta = meta
        self._showing_raw = False

        header = ctk.CTkFrame(self, fg_color=COLORS["bg_panel"], corner_radius=10, border_width=1, border_color=COLORS["border"])
        header.pack(fill="x", padx=16, pady=(14, 8))

        self.toggle_btn = ctk.CTkButton(
            header,
            text="View Raw XML",
            width=112,
            height=28,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_alt"],
            command=self._toggle_view,
        )
        self.toggle_btn.pack(side="right", padx=(6, 12), pady=10)

        ctk.CTkLabel(
            header,
            text=meta["name"],
            anchor="w",
            font=ctk.CTkFont(family=UI_FONT, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side="left", fill="x", expand=True, padx=14, pady=10)

        self.textbox = ctk.CTkTextbox(
            self,
            fg_color=COLORS["bg_panel"],
            border_width=1,
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(family=MONO_FONT, size=12),
        )
        self.textbox.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        self._render_summary()

    def _render_summary(self) -> None:
        m = self.meta
        lines = [
            f"Source:       {m['source']}",
            f"NZB Size:     {m['disk_size']}",
            f"Payload Size: {m['payload_human']} ({m['file_count']} files)",
            f"Groups:       {', '.join(m['groups']) or 'None listed'}",
            "-" * 72,
            "Files inside NZB:",
        ]
        for idx, f in enumerate(m["files"], 1):
            lines.append(f"{idx:>4}. [{f['size_human']:>9}]  {f['subject']}")

        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        self.textbox.insert("1.0", "\n".join(lines))
        self.textbox.configure(state="disabled")

    def _toggle_view(self) -> None:
        self._showing_raw = not self._showing_raw
        if self._showing_raw:
            self.toggle_btn.configure(text="View File List")
            raw = self.meta.get("raw_xml", "")
            if len(raw) > MAX_RAW_XML_PREVIEW_CHARS:
                raw = (
                    raw[:MAX_RAW_XML_PREVIEW_CHARS]
                    + f"\n\n... [Truncated after {MAX_RAW_XML_PREVIEW_CHARS:,} chars; total {len(raw):,} chars] ..."
                )
            self.textbox.configure(state="normal")
            self.textbox.delete("1.0", "end")
            self.textbox.insert("1.0", raw)
            self.textbox.configure(state="disabled")
        else:
            self.toggle_btn.configure(text="View Raw XML")
            self._render_summary()


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, master, config: dict, on_save):
        super().__init__(master)
        self.title("Connection & Indexer Settings")
        apply_window_chrome(self)
        self.geometry("760x710")
        self.minsize(640, 540)
        self.resizable(True, True)
        self.configure(fg_color=COLORS["bg_root"])
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_close_dialog)
        self.bind("<Escape>", lambda _e: self._on_close_dialog())

        self._config = dict(config)
        self._indexers: list[dict] = [dict(ix) for ix in config.get("indexers", [])]
        self._editing_idx: int | None = None
        self._dirty = False
        self._on_save = on_save
        self._show_keys_var = ctk.BooleanVar(value=False)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=16, pady=(0, 14))

        self.test_status_lbl = ctk.CTkLabel(
            footer,
            text="",
            anchor="w",
            font=ctk.CTkFont(family=MONO_FONT, size=11),
            text_color=COLORS["text_secondary"],
        )
        self.test_status_lbl.pack(side="left", fill="x", expand=True, padx=(4, 8))

        ctk.CTkButton(
            footer,
            text="Cancel",
            width=90,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["border"],
            command=self.destroy,
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            footer,
            text="Save & Connect",
            width=142,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color="#041016",
            font=ctk.CTkFont(family=UI_FONT, size=12, weight="bold"),
            command=self._save,
        ).pack(side="right")

        container = ctk.CTkScrollableFrame(
            self,
            fg_color=COLORS["bg_panel"],
            corner_radius=12,
            border_width=1,
            border_color=COLORS["border"],
        )
        container.pack(side="top", fill="both", expand=True, padx=16, pady=(16, 10))
        self._container = container

        sab_hdr = ctk.CTkFrame(container, fg_color="transparent")
        sab_hdr.pack(fill="x", padx=14, pady=(10, 6))
        ctk.CTkLabel(
            sab_hdr,
            text="SABnzbd & Local Storage",
            font=ctk.CTkFont(family=UI_FONT, size=14, weight="bold"),
            text_color=COLORS["accent"],
        ).pack(side="left")

        ctk.CTkButton(
            sab_hdr,
            text="Test SABnzbd",
            width=108,
            height=26,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_alt"],
            border_width=1,
            border_color=COLORS["border"],
            command=self._test_sab_connection,
        ).pack(side="right")

        ctk.CTkCheckBox(
            sab_hdr,
            text="Show API Keys",
            variable=self._show_keys_var,
            checkbox_width=16,
            checkbox_height=16,
            fg_color=COLORS["accent"],
            font=ctk.CTkFont(family=UI_FONT, size=11),
            command=self._toggle_key_mask,
        ).pack(side="right", padx=(0, 12))

        self.sab_url = self._field(container, "SABnzbd URL", config.get("sab_url", ""), "http://127.0.0.1:8080")
        self.sab_key = self._field(container, "SABnzbd API Key", config.get("sab_api_key", ""), "Paste SABnzbd API key", show="*")
        self.folder = self._field(
            container,
            "Local NZB Folder",
            config.get("nzb_folder", ""),
            str(Path.home() / "Downloads"),
            browse_cb=self._browse_folder,
        )
        for entry in (self.sab_url, self.sab_key, self.folder):
            entry.bind("<KeyRelease>", lambda _e: self._mark_dirty())

        self._idx_header = ctk.CTkLabel(
            container,
            text="Newznab Indexers",
            font=ctk.CTkFont(family=UI_FONT, size=14, weight="bold"),
            text_color=COLORS["accent"],
        )
        self._idx_header.pack(anchor="w", padx=14, pady=(14, 6))

        self.idx_list_frame = ctk.CTkFrame(
            container,
            fg_color=COLORS["bg_root"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=8,
        )
        self.idx_list_frame.pack(fill="x", padx=14, pady=(0, 10))
        self._render_indexer_rows()

        form = ctk.CTkFrame(container, fg_color=COLORS["bg_card"], corner_radius=8, border_width=1, border_color=COLORS["border"])
        form.pack(fill="x", padx=14, pady=(0, 8))
        form.grid_columnconfigure(1, weight=1)

        self.ix_name_entry = self._grid_field(form, 0, "Name", "e.g. NZBgeek")
        self.ix_url_entry = self._grid_field(form, 1, "URL", "e.g. https://api.nzbgeek.info")
        self.ix_key_entry = self._grid_field(form, 2, "API Key", "Paste indexer API key", show="*")

        form_btns = ctk.CTkFrame(form, fg_color="transparent")
        form_btns.grid(row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=(4, 10))

        self.add_idx_btn = ctk.CTkButton(
            form_btns,
            text="+ Add Indexer",
            width=126,
            height=28,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color="#041016",
            font=ctk.CTkFont(family=UI_FONT, size=11, weight="bold"),
            command=self._add_or_update_indexer,
        )
        self.add_idx_btn.pack(side="right")

        ctk.CTkButton(
            form_btns,
            text="Test Form",
            width=86,
            height=28,
            fg_color=COLORS["bg_panel"],
            hover_color=COLORS["border"],
            command=self._test_form_indexer,
        ).pack(side="right", padx=(0, 8))

        self.cancel_edit_btn = ctk.CTkButton(
            form_btns,
            text="Clear",
            width=70,
            height=28,
            fg_color=COLORS["bg_panel"],
            hover_color=COLORS["border"],
            command=self._clear_indexer_form,
        )
        self.cancel_edit_btn.pack(side="right", padx=(0, 8))

        sec_note = (
            "All API keys are encrypted with Windows DPAPI. Use HTTPS for remote SABnzbd servers."
            if _SYS == "Windows"
            else "Use HTTPS for remote SABnzbd servers."
        )
        ctk.CTkLabel(
            container,
            text=sec_note,
            anchor="w",
            font=ctk.CTkFont(family=UI_FONT, size=11),
            text_color=COLORS["text_muted"],
        ).pack(fill="x", padx=14, pady=(4, 10))

    def _toggle_key_mask(self) -> None:
        mask = "" if self._show_keys_var.get() else "*"
        self.sab_key.configure(show=mask)
        self.ix_key_entry.configure(show=mask)

    def _mark_dirty(self) -> None:
        self._dirty = True

    def _set_test_status(self, msg: str, color: str) -> None:
        if self.winfo_exists():
            self.test_status_lbl.configure(text=msg, text_color=color)

    def _run_test_bg(self, label: str, fn) -> None:
        self._set_test_status(f"Testing {label}...", COLORS["sky"])

        def worker():
            try:
                ok_msg = fn()
                try:
                    if self.winfo_exists():
                        self.after(0, lambda m=ok_msg: self._set_test_status(m, COLORS["green"]))
                except Exception:
                    pass
            except Exception as exc:
                err_msg = _format_http_error(exc)
                try:
                    if self.winfo_exists():
                        self.after(0, lambda m=err_msg: self._set_test_status(f"{label} failed: {m}", COLORS["red"]))
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def _test_sab_connection(self) -> None:
        url = self.sab_url.get().strip()
        key = self.sab_key.get().strip()

        def task():
            info, ms = SabnzbdClient(url, key).test_connection()
            return f"OK: {info} ({ms} ms)"

        self._run_test_bg("SABnzbd", task)

    def _test_indexer(self, name: str, url: str, key: str) -> None:
        if not url.strip():
            self._set_test_status("Enter an indexer URL first.", COLORS["amber"])
            return

        def task():
            cat_count, ms = NewznabClient(url, key, name=name or "Indexer").test_connection()
            key_note = "" if key.strip() else " (no API key)"
            return f"OK: {name or 'Indexer'} — {cat_count} cats, {ms} ms{key_note}"

        self._run_test_bg(name or "Indexer", task)

    def _test_form_indexer(self) -> None:
        self._test_indexer(
            self.ix_name_entry.get().strip() or "Indexer",
            self.ix_url_entry.get().strip(),
            self.ix_key_entry.get().strip(),
        )

    def _browse_folder(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.folder.get() or str(Path.home()), parent=self)
        if chosen:
            self.folder.delete(0, "end")
            self.folder.insert(0, chosen)
            self._dirty = True

    def _field(self, parent, label: str, initial: str, placeholder: str, show: str = "", browse_cb=None) -> ctk.CTkEntry:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=4)
        ctk.CTkLabel(
            row,
            text=label,
            width=130,
            anchor="w",
            font=ctk.CTkFont(family=UI_FONT, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(side="left")
        entry = ctk.CTkEntry(
            row,
            fg_color=COLORS["bg_card"],
            border_color=COLORS["border"],
            placeholder_text=placeholder,
            show=show,
        )
        entry.insert(0, initial)
        entry.pack(side="left", fill="x", expand=True)
        if browse_cb is not None:
            ctk.CTkButton(
                row,
                text="Browse...",
                width=78,
                height=28,
                fg_color=COLORS["bg_card"],
                hover_color=COLORS["bg_card_alt"],
                command=browse_cb,
            ).pack(side="left", padx=(6, 0))
        return entry

    def _grid_field(self, parent, row_idx: int, label: str, placeholder: str, show: str = "") -> ctk.CTkEntry:
        ctk.CTkLabel(
            parent,
            text=label,
            width=80,
            anchor="w",
            font=ctk.CTkFont(family=UI_FONT, size=11),
            text_color=COLORS["text_secondary"],
        ).grid(row=row_idx, column=0, padx=(12, 6), pady=4, sticky="w")
        entry = ctk.CTkEntry(
            parent,
            height=28,
            fg_color=COLORS["bg_panel"],
            border_color=COLORS["border"],
            placeholder_text=placeholder,
            show=show,
        )
        entry.grid(row=row_idx, column=1, padx=(0, 12), pady=4, sticky="ew")
        return entry

    def focus_section(self, section: str) -> None:
        """Scrolls to a section: "sab" (connection fields) or "indexers"."""
        try:
            self.update_idletasks()
            canvas = self._container._parent_canvas
            if section == "indexers":
                total = max(1, self._container.winfo_height())
                canvas.yview_moveto(max(0.0, (self._idx_header.winfo_y() - 8) / total))
            else:
                canvas.yview_moveto(0.0)
                (self.sab_url if not self.sab_url.get().strip() else self.sab_key).focus_set()
        except Exception:
            pass

    def _render_indexer_rows(self) -> None:
        for w in self.idx_list_frame.winfo_children():
            w.destroy()

        if not self._indexers:
            ctk.CTkLabel(
                self.idx_list_frame,
                text="No indexers configured. Add one below.",
                text_color=COLORS["text_muted"],
            ).pack(pady=18)
            return

        for idx, ix in enumerate(self._indexers):
            row = ctk.CTkFrame(self.idx_list_frame, fg_color=COLORS["bg_card"], corner_radius=6)
            row.pack(fill="x", padx=6, pady=4)

            enabled_var = ctk.BooleanVar(value=bool(ix.get("enabled", True)))
            ctk.CTkCheckBox(
                row,
                text="",
                width=24,
                variable=enabled_var,
                fg_color=COLORS["accent"],
                command=lambda i=idx, v=enabled_var: self._toggle_ix_enabled(i, v.get()),
            ).pack(side="left", padx=(8, 4), pady=6)

            ctk.CTkButton(
                row,
                text="Del",
                width=44,
                height=24,
                fg_color=COLORS["red"],
                hover_color="#DC2626",
                command=lambda i=idx: self._remove_indexer(i),
            ).pack(side="right", padx=(2, 8), pady=4)

            ctk.CTkButton(
                row,
                text="Edit",
                width=48,
                height=24,
                fg_color=COLORS["bg_panel"],
                hover_color=COLORS["border"],
                command=lambda i=idx: self._load_indexer_to_form(i),
            ).pack(side="right", padx=2, pady=4)

            ctk.CTkButton(
                row,
                text="Test",
                width=48,
                height=24,
                fg_color=COLORS["bg_panel"],
                hover_color=COLORS["border"],
                command=lambda item=ix: self._test_indexer(item.get("name", "Indexer"), item.get("url", ""), item.get("api_key", "")),
            ).pack(side="right", padx=2, pady=4)

            has_key = "Key Set" if ix.get("api_key") else "No Key"
            key_color = COLORS["green"] if ix.get("api_key") else COLORS["amber"]
            ctk.CTkLabel(
                row,
                text=has_key,
                font=ctk.CTkFont(family=MONO_FONT, size=10),
                text_color=key_color,
            ).pack(side="right", padx=6)

            ctk.CTkLabel(
                row,
                text=f"{ix.get('name', 'Indexer')}  •  {ix.get('url', '')}",
                anchor="w",
                font=ctk.CTkFont(family=UI_FONT, size=12, weight="bold"),
                text_color=COLORS["text_primary"],
            ).pack(side="left", fill="x", expand=True, padx=4)

    def _toggle_ix_enabled(self, idx: int, enabled: bool) -> None:
        if 0 <= idx < len(self._indexers):
            self._indexers[idx]["enabled"] = enabled
            self._dirty = True

    def _load_indexer_to_form(self, idx: int) -> None:
        ix = self._indexers[idx]
        self._editing_idx = idx
        self.ix_name_entry.delete(0, "end")
        self.ix_name_entry.insert(0, ix.get("name", ""))
        self.ix_url_entry.delete(0, "end")
        self.ix_url_entry.insert(0, ix.get("url", ""))
        self.ix_key_entry.delete(0, "end")
        self.ix_key_entry.insert(0, ix.get("api_key", ""))
        self.add_idx_btn.configure(text="Update Indexer")

    def _clear_indexer_form(self) -> None:
        self._editing_idx = None
        self.ix_name_entry.delete(0, "end")
        self.ix_url_entry.delete(0, "end")
        self.ix_key_entry.delete(0, "end")
        self.add_idx_btn.configure(text="+ Add Indexer")

    def _unique_indexer_name(self, desired: str, skip_idx: int | None = None) -> str:
        existing = {
            ix.get("name", "").strip().lower()
            for i, ix in enumerate(self._indexers)
            if i != skip_idx
        }
        base = desired.strip() or "Indexer"
        if base.lower() not in existing:
            return base
        n = 2
        while f"{base} ({n})".lower() in existing:
            n += 1
        return f"{base} ({n})"

    def _add_or_update_indexer(self) -> bool:
        url = self.ix_url_entry.get().strip()
        key = self.ix_key_entry.get().strip()
        name = self.ix_name_entry.get().strip()
        if not url:
            messagebox.showwarning("Missing URL", "Enter an indexer URL first.", parent=self)
            return False
        if not name:
            parsed = urllib.parse.urlparse(url if "://" in url else f"https://{url}")
            name = parsed.hostname or "Indexer"
        name = self._unique_indexer_name(name, skip_idx=self._editing_idx)

        entry = {"name": name, "url": url, "api_key": key, "enabled": True}
        if self._editing_idx is not None and 0 <= self._editing_idx < len(self._indexers):
            entry["enabled"] = self._indexers[self._editing_idx].get("enabled", True)
            self._indexers[self._editing_idx] = entry
        else:
            self._indexers.append(entry)

        self._dirty = True
        self._clear_indexer_form()
        self._render_indexer_rows()
        return True

    def _remove_indexer(self, idx: int) -> None:
        if 0 <= idx < len(self._indexers):
            self._indexers.pop(idx)
            if self._editing_idx == idx:
                self._clear_indexer_form()
            elif self._editing_idx is not None and idx < self._editing_idx:
                self._editing_idx -= 1
            self._dirty = True
            self._render_indexer_rows()

    def _on_close_dialog(self) -> None:
        has_pending = bool(
            self.ix_name_entry.get().strip()
            or self.ix_url_entry.get().strip()
            or self.ix_key_entry.get().strip()
        )
        if self._dirty or has_pending:
            choice = messagebox.askyesnocancel(
                "Unsaved Settings Changes",
                "Save your connection and indexer changes before closing?",
                parent=self,
            )
            if choice is None:
                return
            if choice:
                self._save()
                return
        self.destroy()

    def _save(self) -> None:
        sab_url = self.sab_url.get().strip()
        if _is_remote_http(sab_url):
            proceed = messagebox.askyesno(
                "Unencrypted Remote Connection",
                f"'{sab_url}' uses HTTP (unencrypted) for a non-local address.\n"
                "Your SABnzbd API key would be sent in plain text.\n\nSave anyway?",
                parent=self,
            )
            if not proceed:
                return

        pending_name = self.ix_name_entry.get().strip()
        pending_url = self.ix_url_entry.get().strip()
        pending_key = self.ix_key_entry.get().strip()
        if pending_url:
            if not self._add_or_update_indexer():
                return
        elif pending_name or pending_key:
            messagebox.showwarning(
                "Missing Indexer URL",
                "You entered an indexer Name or API Key without a URL.\n"
                "Enter the URL or click Clear before saving.",
                parent=self,
            )
            return

        # Only the fields this dialog edits. Everything else (media folders, Kodi, Media Server,
        # toggles) is owned by other tabs and is merged from the live config by the app, so a
        # save here can never revert changes made elsewhere while the dialog was open.
        updated = {
            "sab_url": sab_url,
            "sab_api_key": self.sab_key.get().strip(),
            "indexers": [dict(ix) for ix in self._indexers],
            "nzb_folder": self.folder.get().strip(),
        }
        try:
            self._on_save(updated)
        except Exception as exc:
            messagebox.showerror("Save Failed", f"Could not save settings:\n{exc}", parent=self)
            return
        self._dirty = False
        self.destroy()


# ==============================================================================
# 6. MAIN APPLICATION
# ==============================================================================

class NzbManagerApp(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode("dark")
        apply_app_theme()  # before any widget exists, including this window
        _ICON_CACHE.clear()  # icon images belong to one Tk interpreter; never reuse a destroyed one's
        super().__init__()

        self.title("NZB Command Center — Operations Center")
        self.geometry("1360x900")
        self.minsize(1080, 720)
        self.configure(fg_color=COLORS["bg_root"])
        apply_window_chrome(self, delay_ms=60)
        self._dashboard_compact = False
        self._compact_after_id = None

        self._shutting_down = False
        self._ui_queue: queue.Queue = queue.Queue()
        self._ui_drain_after_id = None
        self._auto_refresh_after_id = None
        self._settings_dialog: SettingsDialog | None = None
        self._config_warnings: list[str] = []
        # Encrypted values that could not be decrypted at load time, keyed by field id.
        # They are written back unchanged so an unrelated save never erases them.
        self._undecryptable: dict[str, str] = {}

        self._sab_refresh_in_flight = False
        self._search_in_flight = False
        self._search_seq = 0
        self._last_queue_sig = None
        self._last_history_sig = None
        self._known_history_states: dict[str, str] | None = None
        self._sab_queue_keys: set[str] = set()
        self._sab_history_keys: set[str] = set()
        self.library_index: dict = build_library_index([])

        self.config_data = self._load_config()
        self.daily_stats = self._load_daily_stats()
        self.sab = SabnzbdClient(self.config_data["sab_url"], self.config_data["sab_api_key"])
        self.indexers: list[NewznabClient] = self._build_indexer_clients(self.config_data)
        self.artwork = ArtworkCache(size=(58, 84))
        self._restore_window_geometry()

        self.sab_categories: list[str] = ["*"]
        self._sab_cats_loaded = False
        self.indexer_cat_map: dict[str, str] = {label: cid for label, cid in FALLBACK_INDEXER_CATEGORIES}
        self.caps_by_url: dict[str, list[tuple[str, str]]] = {}

        self.local_nzbs: list[Path] = []
        self.local_row_vars: dict[Path, ctk.BooleanVar] = {}
        self.local_row_frames: dict[Path, ctk.CTkFrame] = {}
        self.selected_nzb_path: Path | None = None
        self.selected_nzb_meta: dict | None = None

        self.current_search_results: list[dict] = []
        self._watch_after_id = None
        self._watch_dir: Path | None = None
        self._watch_seen: dict[str, float] = {}   # path -> mtime already handled (or present at start)
        self._watch_in_flight: set[str] = set()
        self._search_ctx: dict | None = None  # query/offsets/modes of the last search, for "Load more"
        self._recent_pos = -1
        self.filtered_search_results: list[dict] = []
        self.selected_search_uids: set[str] = set()
        self.search_page: int = 0
        self.sab_paused = False

        self._build_ui()
        self._bind_shortcuts()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        last_tab = self.config_data.get("last_tab", "")
        if last_tab in self._tab_names:
            self.tabs.set(last_tab)
        if self.config_data.get("compact_dashboard"):
            self._set_dashboard_compact(True, save=False)

        self._drain_ui_queue()
        self._load_cached_indexer_categories()
        self._refresh_indexer_selector()
        self._update_local_drive_space()
        self._scan_local_folder()
        self._update_indexer_pill()

        for warn in self._config_warnings:
            self._set_status(warn, log=True, level="WARN")
        if _is_remote_http(self.config_data.get("sab_url", "")):
            self._log_activity("Remote SABnzbd URL uses unencrypted HTTP. Consider switching to HTTPS.", "WARN")
        if not PIL_AVAILABLE:
            self._set_status("Ready. Tip: run 'pip install pillow' to enable poster artwork.")

        self.after(150, self._refresh_library_index_from_cache)
        self.after(200, self._refresh_sab_all)
        self.after(900, self._auto_fetch_missing_caps)
        self._auto_refresh_after_id = self.after(4000, self._auto_refresh_loop)
        if self.config_data.get("watch_folder"):
            self.after(1500, self._start_watch)

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-f>", self._shortcut_focus_search)
        self.bind("<Control-F>", self._shortcut_focus_search)
        self.bind("<Control-comma>", lambda _e: self._open_settings())
        self.bind("<F5>", lambda _e: self._refresh_sab_all())
        self.bind("<F9>", lambda _e: self._toggle_dashboard())

    def _shortcut_focus_search(self, _event=None) -> None:
        self.tabs.set("Indexer Search")
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")

    def _open_settings(self, section: str | None = None) -> None:
        if self._settings_dialog is not None and self._settings_dialog.winfo_exists():
            self._settings_dialog.focus()
        else:
            self._settings_dialog = SettingsDialog(self, self.config_data, self._save_config)
        if section:
            dlg = self._settings_dialog
            self.after(220, lambda: dlg.winfo_exists() and dlg.focus_section(section))

    # ------------------------------------------------------------------ window state
    @staticmethod
    def _virtual_screen(widget) -> tuple[int, int, int, int]:
        """(x, y, width, height) of the whole desktop across all monitors."""
        if _SYS == "Windows":
            try:
                gsm = ctypes.windll.user32.GetSystemMetrics
                w, h = gsm(78), gsm(79)
                if w > 0 and h > 0:
                    return gsm(76), gsm(77), w, h
            except Exception:
                pass
        return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()

    def _restore_window_geometry(self) -> None:
        """Reopens at the last size/position, unless that's off-screen (e.g. a monitor was unplugged)."""
        m = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", str(self.config_data.get("window_geometry") or ""))
        if m:
            w, h, x, y = (int(v) for v in m.groups())
            w, h = max(w, 1080), max(h, 720)
            vx, vy, vw, vh = self._virtual_screen(self)
            if vx - w + 160 <= x <= vx + vw - 160 and vy - 10 <= y <= vy + vh - 120:
                self.geometry(f"{w}x{h}+{x}+{y}")
        if self.config_data.get("window_zoomed"):
            self.after(80, lambda: self.state("zoomed"))

    def _remember_window_state(self) -> None:
        try:
            state = self.state()
            self.config_data["window_zoomed"] = state == "zoomed"
            if state == "normal":
                self.config_data["window_geometry"] = self.geometry()
            self.config_data["last_tab"] = self.tabs.get()
            self.config_data["compact_dashboard"] = self._dashboard_compact
            self._write_config_file(self.config_data)
        except Exception:
            pass

    def _load_config(self) -> dict:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        if CONFIG_FILE.exists():
            try:
                loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                cfg["sab_url"] = loaded.get("sab_url", cfg["sab_url"])
                sab_key, sab_fail = _unprotect_secret_checked(loaded.get("sab_api_key", ""))
                if sab_fail:
                    self._undecryptable["sab_api_key"] = str(loaded.get("sab_api_key", ""))
                    self._config_warnings.append("SABnzbd DPAPI key could not be decrypted; please re-enter it in Settings.")
                cfg["sab_api_key"] = sab_key
                cfg["nzb_folder"] = loaded.get("nzb_folder", cfg["nzb_folder"])
                cfg["auto_refresh"] = loaded.get("auto_refresh", True)
                cfg["auto_archive_sent"] = loaded.get("auto_archive_sent", False)
                if isinstance(loaded.get("media_folders"), list):
                    cfg["media_folders"] = [str(f) for f in loaded["media_folders"] if str(f).strip()]
                cfg["ffprobe_path"] = str(loaded.get("ffprobe_path") or "")
                cfg["kodi_url"] = str(loaded.get("kodi_url") or "")
                cfg["kodi_username"] = str(loaded.get("kodi_username") or "")
                kodi_pw, kodi_fail = _unprotect_secret_checked(str(loaded.get("kodi_password") or ""))
                if kodi_fail:
                    self._undecryptable["kodi_password"] = str(loaded.get("kodi_password") or "")
                    self._config_warnings.append("Kodi DPAPI password could not be decrypted; please re-enter it in the Kodi tab.")
                cfg["kodi_password"] = kodi_pw
                cfg["kodi_auto_scan"] = bool(loaded.get("kodi_auto_scan", True))
                cfg["kodi_notify_on_complete"] = bool(loaded.get("kodi_notify_on_complete", True))
                ms_type = str(loaded.get("media_server_type") or "Jellyfin")
                cfg["media_server_type"] = ms_type if ms_type in ("Jellyfin", "Plex") else "Jellyfin"
                cfg["media_server_url"] = str(loaded.get("media_server_url") or "")
                ms_token, ms_fail = _unprotect_secret_checked(str(loaded.get("media_server_token") or ""))
                if ms_fail:
                    self._undecryptable["media_server_token"] = str(loaded.get("media_server_token") or "")
                    self._config_warnings.append(
                        "Media Server DPAPI token could not be decrypted; please re-enter it in the Media Server tab."
                    )
                cfg["media_server_token"] = ms_token
                cfg["media_server_auto_scan"] = bool(loaded.get("media_server_auto_scan", True))
                cfg["media_server_path_map"] = str(loaded.get("media_server_path_map") or "")
                if isinstance(loaded.get("search_history"), list):
                    cfg["search_history"] = [str(q) for q in loaded["search_history"] if str(q).strip()][:15]
                cfg["desktop_notifications"] = bool(loaded.get("desktop_notifications", True))
                cfg["watch_folder"] = bool(loaded.get("watch_folder", False))
                cfg["window_geometry"] = str(loaded.get("window_geometry") or "")
                cfg["window_zoomed"] = bool(loaded.get("window_zoomed", False))
                cfg["last_tab"] = str(loaded.get("last_tab") or "")
                cfg["compact_dashboard"] = bool(loaded.get("compact_dashboard", False))

                if isinstance(loaded.get("indexers"), list):
                    migrated = []
                    seen_names: set[str] = set()
                    for ix in loaded["indexers"]:
                        if isinstance(ix, dict) and str(ix.get("url", "")).strip():
                            base_name = str(ix.get("name") or "Indexer").strip()
                            uniq_name = base_name
                            n = 2
                            while uniq_name.lower() in seen_names:
                                uniq_name = f"{base_name} ({n})"
                                n += 1
                            seen_names.add(uniq_name.lower())

                            ix_key, ix_fail = _unprotect_secret_checked(str(ix.get("api_key") or ""))
                            if ix_fail:
                                self._undecryptable[self._indexer_secret_id(str(ix.get("url", "")))] = str(ix.get("api_key") or "")
                                self._config_warnings.append(f"DPAPI key for indexer '{uniq_name}' could not be decrypted.")
                            migrated.append({
                                "name": uniq_name,
                                "url": str(ix.get("url", "")).strip(),
                                "api_key": ix_key,
                                "enabled": bool(ix.get("enabled", True)),
                            })
                    cfg["indexers"] = migrated
                elif loaded.get("indexer_url"):
                    url = loaded["indexer_url"]
                    host = urllib.parse.urlparse(url).hostname or "Primary Indexer"
                    ix_key, ix_fail = _unprotect_secret_checked(loaded.get("indexer_api_key", ""))
                    if ix_fail:
                        self._undecryptable[self._indexer_secret_id(str(url))] = str(loaded.get("indexer_api_key", ""))
                        self._config_warnings.append(f"DPAPI key for indexer '{host}' could not be decrypted.")
                    cfg["indexers"] = [{
                        "name": host,
                        "url": url,
                        "api_key": ix_key,
                        "enabled": True,
                    }]
            except Exception as exc:
                self._config_warnings.append(f"Failed to read {CONFIG_FILE.name}: {exc}")
        return cfg

    @staticmethod
    def _build_indexer_clients(cfg: dict) -> list[NewznabClient]:
        clients: list[NewznabClient] = []
        for ix in cfg.get("indexers", []):
            if ix.get("enabled", True) and ix.get("url"):
                clients.append(NewznabClient(ix["url"], ix.get("api_key", ""), name=ix.get("name", "Indexer")))
        return clients

    @staticmethod
    def _indexer_secret_id(url: str) -> str:
        return "indexer:" + (url or "").strip().rstrip("/").lower()

    def _protect_or_keep(self, field_id: str, value: str) -> str:
        """Encrypts a secret, or keeps the original undecryptable blob while the field is blank.

        A key that failed to decrypt (other Windows user / restored profile) shows as blank in
        the UI. Writing "" back would destroy it, so the original blob is preserved until the
        user enters a new value.
        """
        value = value or ""
        if value:
            self._undecryptable.pop(field_id, None)
            return _protect_secret(value)
        return self._undecryptable.get(field_id, "")

    def _write_config_file(self, cfg: dict) -> None:
        to_disk = {
            "sab_url": cfg.get("sab_url", ""),
            "sab_api_key": self._protect_or_keep("sab_api_key", cfg.get("sab_api_key", "")),
            "indexers": [
                {
                    "name": ix.get("name", "Indexer"),
                    "url": ix.get("url", ""),
                    "api_key": self._protect_or_keep(
                        self._indexer_secret_id(ix.get("url", "")), ix.get("api_key", "")
                    ),
                    "enabled": bool(ix.get("enabled", True)),
                }
                for ix in cfg.get("indexers", [])
            ],
            "nzb_folder": cfg.get("nzb_folder", ""),
            "auto_refresh": bool(cfg.get("auto_refresh", True)),
            "auto_archive_sent": bool(cfg.get("auto_archive_sent", False)),
            "media_folders": list(cfg.get("media_folders", [])),
            "ffprobe_path": cfg.get("ffprobe_path", ""),
            "kodi_url": cfg.get("kodi_url", ""),
            "kodi_username": cfg.get("kodi_username", ""),
            "kodi_password": self._protect_or_keep("kodi_password", cfg.get("kodi_password", "")),
            "kodi_auto_scan": bool(cfg.get("kodi_auto_scan", True)),
            "kodi_notify_on_complete": bool(cfg.get("kodi_notify_on_complete", True)),
            "media_server_type": cfg.get("media_server_type", "Jellyfin"),
            "media_server_url": cfg.get("media_server_url", ""),
            "media_server_token": self._protect_or_keep("media_server_token", cfg.get("media_server_token", "")),
            "media_server_auto_scan": bool(cfg.get("media_server_auto_scan", True)),
            "media_server_path_map": str(cfg.get("media_server_path_map", "")),
            "search_history": [str(q) for q in cfg.get("search_history", [])][:15],
            "desktop_notifications": bool(cfg.get("desktop_notifications", True)),
            "watch_folder": bool(cfg.get("watch_folder", False)),
            "window_geometry": str(cfg.get("window_geometry", "")),
            "window_zoomed": bool(cfg.get("window_zoomed", False)),
            "last_tab": str(cfg.get("last_tab", "")),
            "compact_dashboard": bool(cfg.get("compact_dashboard", False)),
        }
        _atomic_write_json(CONFIG_FILE, to_disk)
        # Warn (once per new failure) when Windows couldn't encrypt a secret, instead of
        # silently leaving API keys / passwords in plain text in the config file.
        if _DPAPI_FAILURES > getattr(self, "_dpapi_failures_reported", 0):
            self._dpapi_failures_reported = _DPAPI_FAILURES
            try:
                self._log_activity(
                    "Windows could not encrypt a saved key/password; it was written to "
                    f"{CONFIG_FILE.name} in plain text.",
                    "WARN",
                )
            except Exception:
                pass

    def _on_media_config_changed(self, folders: list[str], ffprobe_path: str) -> None:
        folders_changed = list(folders) != list(self.config_data.get("media_folders", []))
        self.config_data["media_folders"] = list(folders)
        self.config_data["ffprobe_path"] = ffprobe_path
        self._safe_write_config("Media Inspector")
        if folders_changed:
            self._refresh_library_index_from_cache()

    def _on_kodi_config_changed(self, kodi_cfg: dict) -> None:
        self.config_data.update(kodi_cfg)
        self._safe_write_config("Kodi")

    def _safe_write_config(self, source: str) -> None:
        """Writes the config, reporting (instead of raising) a failure such as a locked or read-only file."""
        try:
            self._write_config_file(self.config_data)
        except Exception as exc:
            self._set_status(f"Could not save {source} settings: {exc}", log=True, level="ERROR")

    def _on_kodi_state_changed(self, text: str, color_key: str) -> None:
        if hasattr(self, "kodi_status_pill"):
            self.kodi_status_pill.configure(
                text=f"●  Kodi: {text}",
                text_color=COLORS.get(color_key, COLORS["text_muted"]),
            )

    def _on_media_server_config_changed(self, ms_cfg: dict) -> None:
        self.config_data.update(ms_cfg)
        try:
            self._write_config_file(self.config_data)
        except Exception as exc:
            self._set_status(f"Could not save Media Server settings: {exc}", log=True, level="ERROR")

    def _on_media_server_state_changed(self, text: str, color_key: str) -> None:
        if hasattr(self, "media_server_status_pill"):
            # The panel reports "Jellyfin: Online" style text, or a bare "Not Configured".
            label = text if ":" in text else f"Media Server: {text}"
            self.media_server_status_pill.configure(
                text=f"●  {label}",
                text_color=COLORS.get(color_key, COLORS["text_muted"]),
            )

    def _get_disk_items_for_kodi(self) -> list[dict]:
        if hasattr(self, "media_inspector") and self.media_inspector.all_items:
            return [
                {
                    "path": str(it.path),
                    "title": it.display_title,
                    "media_type": it.media_type,
                    "is_corrupt": it.is_corrupt,
                }
                for it in self.media_inspector.all_items
            ]
        # Fallback to media_inspector_cache.json if user hasn't rescanned yet this session
        folders = list(self.config_data.get("media_folders", []))
        if not folders or not MEDIA_CACHE_FILE.exists():
            return []
        try:
            raw = json.loads(MEDIA_CACHE_FILE.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return []
        except Exception:
            return []

        norm_roots = [str(Path(f)).lower() if _SYS == "Windows" else str(Path(f)) for f in folders]
        items: list[dict] = []
        for path_str, entry in raw.items():
            if not isinstance(entry, dict):
                continue
            cmp_path = path_str.lower() if _SYS == "Windows" else path_str
            if not any(cmp_path.startswith(r) for r in norm_roots):
                continue
            if not Path(path_str).exists():
                continue
            data = entry.get("data") or {}
            items.append({
                "path": path_str,
                "title": Path(path_str).name,
                "media_type": "Media",
                "is_corrupt": bool(data.get("is_corrupt", False)),
            })
        return items

    def _update_library_pill(self) -> None:
        if not hasattr(self, "lib_index_badge"):
            return
        total = self.library_index.get("total_count", 0)
        if total > 0:
            self.lib_index_badge.configure(text=f"Lib: {total:,} indexed", text_color=COLORS["accent"])
        else:
            self.lib_index_badge.configure(text="Lib: Not scanned", text_color=COLORS["text_muted"])

    def _refresh_library_index_from_cache(self) -> None:
        folders = list(self.config_data.get("media_folders", []))
        if not folders:
            self.library_index = build_library_index([])
            self._update_library_pill()
            if self.current_search_results:
                self._apply_filters_and_render(reset_page=False)
            return

        def load_idx():
            return build_library_index_from_cache(allowed_folders=folders)

        def ok(idx: dict):
            self.library_index = idx
            self._update_library_pill()
            if idx.get("total_count", 0) > 0:
                self._log_activity(f"Loaded {idx['total_count']:,} media item(s) into search library index.", "INFO")
            if self.current_search_results:
                self._apply_filters_and_render(reset_page=False)

        self._run_bg(load_idx, on_ok=ok, on_err=lambda _e: None)

    def _on_media_library_updated(self, items: list) -> None:
        self.library_index = build_library_index(items)
        self._update_library_pill()
        if self.current_search_results:
            self._apply_filters_and_render(reset_page=False)

    @staticmethod
    def _load_daily_stats() -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        if STATS_FILE.exists():
            try:
                data = json.loads(STATS_FILE.read_text(encoding="utf-8"))
                if data.get("date") == today:
                    return {"date": today, "count": int(data.get("count", 0))}
            except Exception:
                pass
        return {"date": today, "count": 0}

    def _increment_daily_nzb_count(self, delta: int = 1) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if self.daily_stats.get("date") != today:
            self.daily_stats = {"date": today, "count": 0}
        self.daily_stats["count"] += delta
        try:
            _atomic_write_json(STATS_FILE, self.daily_stats)
        except Exception:
            pass
        self.card_nzbs_val.configure(text=str(self.daily_stats["count"]))

    def _save_config(self, settings_fields: dict) -> None:
        # Merge the Settings dialog's fields into the LIVE config rather than replacing it.
        new_cfg = dict(self.config_data)
        new_cfg.update(settings_fields)
        new_cfg["auto_refresh"] = self.auto_refresh_var.get()
        new_cfg["auto_archive_sent"] = self.auto_archive_var.get()
        self.config_data = new_cfg
        self._write_config_file(new_cfg)
        self.sab = SabnzbdClient(new_cfg["sab_url"], new_cfg["sab_api_key"])
        self.indexers = self._build_indexer_clients(new_cfg)
        self._sab_cats_loaded = False
        self._last_queue_sig = None
        self._last_history_sig = None
        self._known_history_states = None

        self.folder_entry.delete(0, "end")
        self.folder_entry.insert(0, new_cfg["nzb_folder"])
        if self.watch_var.get() and self._watch_dir != self._watch_folder_path():
            self._start_watch()
        self._refresh_indexer_selector()
        self._apply_cached_caps_for_selection()
        self._update_local_drive_space()
        self._scan_local_folder()
        self._update_indexer_pill()
        self._refresh_sab_all()
        self.after(300, self._auto_fetch_missing_caps)
        self._set_status(
            f"Settings saved ({len(new_cfg.get('indexers', []))} indexer(s) configured).",
            log=True,
            level="OK",
        )

    def _sab_cat_menu_values(self) -> list[str]:
        extras = [c for c in self.sab_categories if c not in ("*", AUTO_CAT_LABEL)]
        return [AUTO_CAT_LABEL, "*"] + extras

    def _resolve_sab_category(self, selected_cat: str, item_category: str = "", title: str = "") -> str:
        sel = (selected_cat or AUTO_CAT_LABEL).strip()
        if sel != AUTO_CAT_LABEL:
            return sel
        fallback_label = self.indexer_cat_var.get() if hasattr(self, "indexer_cat_var") else ""
        return match_sab_category(
            self.sab_categories,
            item_category=item_category,
            title=title,
            fallback_cat_label=fallback_label,
        )

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
                    # Previously swallowed silently, which hid real bugs. Surface them in the
                    # Activity log (guarded so a broken logger can't break the drain loop).
                    try:
                        self._log_activity(f"Internal UI error: {exc!r}", "ERROR")
                    except Exception:
                        pass
        except queue.Empty:
            pass
        self._ui_drain_after_id = self.after(40, self._drain_ui_queue)

    def _on_close(self) -> None:
        if self._shutting_down:
            return
        self._remember_window_state()
        self._shutting_down = True
        for job_id in (self._ui_drain_after_id, self._auto_refresh_after_id, self._watch_after_id, self._compact_after_id):
            if job_id:
                try:
                    self.after_cancel(job_id)
                except Exception:
                    pass
        if hasattr(self, "media_inspector"):
            self.media_inspector.shutdown()
        if hasattr(self, "kodi_panel"):
            self.kodi_panel.shutdown()
        if hasattr(self, "media_server_panel"):
            self.media_server_panel.shutdown()
        self.artwork.shutdown()
        self.destroy()

    def _run_bg(self, fn, on_ok=None, on_err=None) -> None:
        def worker():
            try:
                res = fn()
                if on_ok and not self._shutting_down:
                    self._post_ui(lambda r=res: on_ok(r))
            except Exception as exc:
                if self._shutting_down:
                    return
                safe_msg = _format_http_error(exc)
                if on_err:
                    self._post_ui(lambda m=safe_msg: on_err(RuntimeError(m)))
                else:
                    self._post_ui(lambda m=safe_msg: self._set_status(f"Error: {m}", log=True, level="ERROR"))

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _get_scroll_y(scrollable) -> float:
        try:
            canvas = getattr(scrollable, "_parent_canvas", None)
            if canvas is not None:
                return float(canvas.yview()[0])
        except Exception:
            pass
        return 0.0

    @staticmethod
    def _set_scroll_y(scrollable, pos: float) -> None:
        try:
            canvas = getattr(scrollable, "_parent_canvas", None)
            if canvas is not None and pos > 0.0:
                canvas.yview_moveto(pos)
        except Exception:
            pass

    def _build_ui(self) -> None:
        self.grid_rowconfigure(3, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, height=64, fg_color=COLORS["bg_panel"], corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.pack_propagate(False)

        header_border = ctk.CTkFrame(header, height=1, fg_color=COLORS["border"], corner_radius=0)
        header_border.place(relx=0, rely=1.0, relwidth=1.0, anchor="sw")

        left = ctk.CTkFrame(header, fg_color="transparent")
        left.pack(side="left", padx=(18, 0), fill="y")

        # Brand mark + title block
        ctk.CTkLabel(
            left,
            text="N",
            width=34,
            height=34,
            corner_radius=9,
            fg_color=COLORS["accent"],
            text_color="#041016",
            font=ctk.CTkFont(family=UI_FONT, size=18, weight="bold"),
        ).pack(side="left", pady=15)

        title_block = ctk.CTkFrame(left, fg_color="transparent")
        title_block.pack(side="left", padx=(11, 18))
        ctk.CTkLabel(
            title_block,
            text="NZB Command Center",
            height=20,
            anchor="w",
            font=ctk.CTkFont(family=UI_FONT, size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_block,
            text="OPERATIONS CENTER",
            height=14,
            anchor="w",
            font=ctk.CTkFont(family=UI_FONT, size=9, weight="bold"),
            text_color=COLORS["accent"],
        ).pack(anchor="w")

        # Thin divider between the brand and the live status pills
        ctk.CTkFrame(left, width=1, height=30, fg_color=COLORS["border"], corner_radius=0).pack(side="left", padx=(0, 14))

        def status_pill(text: str, color: str, last: bool = False) -> ctk.CTkLabel:
            pill = ctk.CTkLabel(
                left,
                text=text,
                height=26,
                fg_color=COLORS["bg_card"],
                corner_radius=13,
                padx=11,
                font=ctk.CTkFont(family=UI_FONT_SEMIBOLD, size=11),
                text_color=color,
            )
            pill.pack(side="left", padx=(0, 0 if last else 6))
            return pill

        self.sab_status_pill = status_pill("●  SABnzbd: Connecting", COLORS["amber"])
        self.indexer_status_pill = status_pill("●  Indexers: Ready", COLORS["accent"])
        self.kodi_status_pill = status_pill("●  Kodi: Not Configured", COLORS["text_muted"])
        self.media_server_status_pill = status_pill("●  Media Server: Not Configured", COLORS["text_muted"], last=True)

        # Each pill is a shortcut to the place where that connection is set up.
        for pill, action, hint in (
            (self.sab_status_pill, lambda: self._open_settings("sab"), "SABnzbd connection: click to open Settings"),
            (self.indexer_status_pill, lambda: self._open_settings("indexers"), "Indexers: click to manage them in Settings"),
            (self.kodi_status_pill, lambda: self.tabs.set("Kodi"), "Kodi: click to open the Kodi tab"),
            (self.media_server_status_pill, lambda: self.tabs.set("Media Server"), "Jellyfin / Plex: click to open the Media Server tab"),
        ):
            pill.configure(cursor="hand2")
            pill.bind("<Button-1>", lambda _e, a=action: a(), add="+")
            pill.bind("<Enter>", lambda _e, w=pill: w.configure(fg_color=COLORS["bg_card_alt"]), add="+")
            pill.bind("<Leave>", lambda _e, w=pill: w.configure(fg_color=COLORS["bg_card"]), add="+")
            Tooltip(pill, hint)

        right = ctk.CTkFrame(header, fg_color="transparent")
        right.pack(side="right", padx=18, fill="y")

        ctk.CTkButton(
            right,
            text="Settings",
            image=app_icon("settings", ON_ACCENT),
            width=108,
            height=34,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color="#041016",
            font=ctk.CTkFont(family=UI_FONT, size=12, weight="bold"),
            command=self._open_settings,
        ).pack(side="right", pady=15)

        self.pause_all_btn = ctk.CTkButton(
            right,
            text="Pause Queue",
            image=app_icon("pause", COLORS["text_primary"]),
            width=128,
            height=34,
            fg_color="transparent",
            hover_color=COLORS["bg_card_alt"],
            border_width=1,
            border_color=COLORS["border"],
            font=ctk.CTkFont(family=UI_FONT_SEMIBOLD, size=12),
            command=self._toggle_pause_all,
        )
        self.pause_all_btn.pack(side="right", padx=(0, 8), pady=15)

        collapse_icon = app_icon("collapse", COLORS["text_secondary"])
        self.dash_toggle_btn = ctk.CTkButton(
            right,
            text="" if collapse_icon else "▴",
            image=collapse_icon,
            width=34,
            height=34,
            fg_color="transparent",
            hover_color=COLORS["bg_card_alt"],
            border_width=1,
            border_color=COLORS["border"],
            text_color=COLORS["text_secondary"],
            command=self._toggle_dashboard,
        )
        self.dash_toggle_btn.pack(side="right", padx=(0, 8), pady=15)
        self._dash_toggle_tip = Tooltip(self.dash_toggle_btn, "Shrink the dashboard to one line for more room (F9)")

        telemetry = ctk.CTkFrame(self, fg_color="transparent")
        telemetry.grid(row=1, column=0, sticky="ew", padx=16, pady=(14, 8))
        for col in range(4):
            telemetry.grid_columnconfigure(col, weight=1, uniform="metric")

        self._telemetry_row = telemetry
        self.card_speed_val, self.card_speed_sub = self._metric_card(
            telemetry, 0, "DOWNLOAD SPEED & QUEUE", "—", "Connecting to SABnzbd…", COLORS["accent"]
        )
        self.card_dl_val, self.card_dl_sub = self._metric_card(
            telemetry, 1, "TOTAL DOWNLOADED", "0 B", "Today: 0 B  •  Week: 0 B", COLORS["sky"]
        )

        drive_card, self.card_drive_val, self.card_drive_sub = self._metric_card(
            telemetry, 2, "DRIVE SPACE", "Checking...", "Download volume", COLORS["green"], return_card=True
        )
        self.drive_bar = ctk.CTkProgressBar(
            drive_card, height=4, fg_color=COLORS["bg_card"], progress_color=COLORS["green"]
        )
        self.drive_bar.set(0)
        self.drive_bar.pack(fill="x", padx=16, pady=(2, 10))

        self.card_nzbs_val, self.card_nzbs_sub = self._metric_card(
            telemetry, 3, "NZBs TODAY", str(self.daily_stats["count"]), "Sent or saved today", COLORS["amber"]
        )

        ops_row = ctk.CTkFrame(self, fg_color="transparent")
        ops_row.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
        self._ops_row = ops_row
        ops_row.grid_columnconfigure(0, weight=3)
        ops_row.grid_columnconfigure(1, weight=2)

        now_card = ctk.CTkFrame(
            ops_row,
            fg_color=COLORS["bg_panel"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=10,
        )
        now_card.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        now_hdr = ctk.CTkFrame(now_card, fg_color="transparent")
        now_hdr.pack(fill="x", padx=16, pady=(12, 2))

        self.now_dl_badge = ctk.CTkLabel(
            now_hdr,
            text="IDLE",
            height=22,
            fg_color=COLORS["bg_card"],
            corner_radius=11,
            padx=10,
            font=ctk.CTkFont(family=UI_FONT, size=10, weight="bold"),
            text_color=COLORS["text_muted"],
        )
        self.now_dl_badge.pack(side="left", padx=(0, 10))

        ctk.CTkLabel(
            now_hdr,
            text="ACTIVE DOWNLOAD",
            font=ctk.CTkFont(family=UI_FONT, size=10, weight="bold"),
            text_color=COLORS["text_muted"],
        ).pack(side="left")

        self.now_dl_stats = ctk.CTkLabel(
            now_hdr,
            text="—",
            font=ctk.CTkFont(family=UI_FONT_SEMIBOLD, size=11),
            text_color=COLORS["text_secondary"],
        )
        self.now_dl_stats.pack(side="right")

        self.now_dl_name = ctk.CTkLabel(
            now_card,
            text="No active download in SABnzbd",
            anchor="w",
            font=ctk.CTkFont(family=UI_FONT, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        self.now_dl_name.pack(fill="x", padx=16, pady=(6, 8))

        self.now_dl_bar = ctk.CTkProgressBar(
            now_card, height=6, fg_color=COLORS["bg_card"], progress_color=COLORS["accent"]
        )
        self.now_dl_bar.set(0)
        self.now_dl_bar.pack(fill="x", padx=16, pady=(0, 14))

        self.speed_chart = SpeedChartWidget(ops_row, height=74)
        self.speed_chart.grid(row=0, column=1, sticky="nsew")

        self._build_compact_strip()

        self.tabs = ctk.CTkTabview(
            self,
            fg_color=COLORS["bg_root"],
            segmented_button_fg_color=COLORS["bg_panel"],
            segmented_button_selected_color=COLORS["accent"],
            segmented_button_selected_hover_color=COLORS["accent_hover"],
            segmented_button_unselected_color=COLORS["bg_panel"],
            segmented_button_unselected_hover_color=COLORS["bg_card_alt"],
            segmented_button_font=ctk.CTkFont(family=UI_FONT_SEMIBOLD, size=12),
            text_color=COLORS["text_secondary"],
            corner_radius=10,
            anchor="w",
        )
        self.tabs.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 4))
        try:  # taller tab buttons (private attr: harmless to skip on other CustomTkinter versions)
            self.tabs._segmented_button.configure(height=34, text_color=COLORS["text_primary"])
        except Exception:
            pass

        self.tab_search = self.tabs.add("Indexer Search")
        self.tab_local = self.tabs.add("Local NZB Files")
        self.tab_sab = self.tabs.add("SABnzbd Queue & History")
        self.tab_media = self.tabs.add("Media Inspector")
        self.tab_kodi = self.tabs.add("Kodi")
        self.tab_media_server = self.tabs.add("Media Server")
        self.tab_log = self.tabs.add("Activity Log")
        self._tab_names = (
            "Indexer Search", "Local NZB Files", "SABnzbd Queue & History", "Media Inspector",
            "Kodi", "Media Server", "Activity Log",
        )

        self._build_search_tab()
        self._build_local_tab()
        self._build_sab_tab()
        self._build_media_tab()
        self._build_kodi_tab()
        self._build_media_server_tab()
        self._build_log_tab()

        footer = ctk.CTkFrame(self, height=30, fg_color=COLORS["bg_panel"], corner_radius=0)
        footer.grid(row=4, column=0, sticky="ew")
        footer.pack_propagate(False)
        ctk.CTkFrame(footer, height=1, fg_color=COLORS["border"], corner_radius=0).place(relx=0, rely=0, relwidth=1.0)

        ctk.CTkLabel(
            footer,
            text="Ctrl+F  Search     F5  Refresh SAB     F9  Compact view     Ctrl+,  Settings     Right-click a result for more",
            font=ctk.CTkFont(family=UI_FONT, size=10),
            text_color=COLORS["text_muted"],
        ).pack(side="right", padx=18)

        self.status_bar = ctk.CTkLabel(
            footer,
            text="Ready.",
            anchor="w",
            font=ctk.CTkFont(family=UI_FONT, size=11),
            text_color=COLORS["text_secondary"],
        )
        self.status_bar.pack(side="left", fill="x", expand=True, padx=18)

    def _build_compact_strip(self) -> None:
        """One-line version of the dashboard, shown instead of the cards in compact view."""
        strip = ctk.CTkFrame(self, height=46, fg_color=COLORS["bg_panel"], border_width=1, border_color=COLORS["border"], corner_radius=10)
        strip.pack_propagate(False)
        self._compact_strip = strip

        self.cs_badge = ctk.CTkLabel(
            strip, text="IDLE", height=22, corner_radius=11, padx=10, fg_color=COLORS["bg_card"],
            font=ctk.CTkFont(family=UI_FONT, size=10, weight="bold"), text_color=COLORS["text_muted"],
        )
        self.cs_badge.pack(side="left", padx=(14, 10))

        def stat(text: str = "", color: str = COLORS["text_secondary"], bold: bool = False) -> ctk.CTkLabel:
            lbl = ctk.CTkLabel(
                strip, text=text, text_color=color,
                font=ctk.CTkFont(family=UI_FONT_SEMIBOLD if bold else UI_FONT, size=12 if bold else 11),
            )
            lbl.pack(side="right", padx=(0, 16))
            return lbl

        self.cs_nzbs = stat()
        self.cs_drive = stat(bold=True)
        ctk.CTkFrame(strip, width=1, height=22, fg_color=COLORS["border"], corner_radius=0).pack(side="right", padx=(0, 16))
        self.cs_queue = stat()
        self.cs_speed = stat(bold=True)

        self.cs_bar = ctk.CTkProgressBar(strip, width=150, height=6, fg_color=COLORS["bg_card"], progress_color=COLORS["accent"])
        self.cs_bar.set(0)
        self.cs_bar.pack(side="right", padx=(0, 16))

        self.cs_name = ctk.CTkLabel(
            strip, text="", anchor="w", font=ctk.CTkFont(family=UI_FONT, size=12, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        self.cs_name.pack(side="left", fill="x", expand=True, padx=(0, 12))

    def _toggle_dashboard(self) -> None:
        self._set_dashboard_compact(not self._dashboard_compact)

    def _set_dashboard_compact(self, compact: bool, save: bool = True) -> None:
        self._dashboard_compact = compact
        if self._compact_after_id:
            try:
                self.after_cancel(self._compact_after_id)
            except Exception:
                pass
            self._compact_after_id = None
        if compact:
            self._telemetry_row.grid_remove()
            self._ops_row.grid_remove()
            self._compact_strip.grid(row=1, column=0, sticky="ew", padx=16, pady=(12, 8))
            self._sync_compact_strip()
        else:
            self._compact_strip.grid_remove()
            self._telemetry_row.grid()
            self._ops_row.grid()
        icon = app_icon("expand" if compact else "collapse", COLORS["text_secondary"])
        self.dash_toggle_btn.configure(image=icon, text="" if icon else ("▾" if compact else "▴"))
        self._dash_toggle_tip.set_text(
            "Show the full dashboard (F9)" if compact else "Shrink the dashboard to one line for more room (F9)"
        )
        if save:
            self.config_data["compact_dashboard"] = compact
            self._safe_write_config("layout")

    def _sync_compact_strip(self) -> None:
        """Mirrors the (hidden) dashboard cards into the compact strip once a second."""
        self._compact_after_id = None
        if not self._dashboard_compact or self._shutting_down:
            return
        try:
            b = self.now_dl_badge
            self.cs_badge.configure(text=b.cget("text"), text_color=b.cget("text_color"), fg_color=b.cget("fg_color"))
            name = str(self.now_dl_name.cget("text"))
            self.cs_name.configure(text=name if len(name) <= 90 else name[:87] + "…")
            self.cs_bar.set(self.now_dl_bar.get())
            self.cs_bar.configure(progress_color=self.now_dl_bar.cget("progress_color"))
            self.cs_speed.configure(text=self.card_speed_val.cget("text"), text_color=self.card_speed_val.cget("text_color"))
            self.cs_queue.configure(text=self.card_speed_sub.cget("text"))
            self.cs_drive.configure(text=self.card_drive_val.cget("text"), text_color=self.card_drive_val.cget("text_color"))
            self.cs_nzbs.configure(text=f"{self.card_nzbs_val.cget('text')} NZBs today")
        except Exception:
            pass
        self._compact_after_id = self.after(1000, self._sync_compact_strip)

    def _metric_card(self, parent, col: int, title: str, value: str, sub: str, accent: str, return_card: bool = False):
        card = ctk.CTkFrame(
            parent,
            fg_color=COLORS["bg_panel"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=10,
        )
        pad_r = 0 if col == 3 else 12
        card.grid(row=0, column=col, sticky="nsew", padx=(0, pad_r))

        # Slim accent stripe on the left edge ties each card to its colour.
        ctk.CTkFrame(card, width=3, fg_color=accent, corner_radius=2).place(x=0, rely=0.22, relheight=0.56)

        ctk.CTkLabel(
            card,
            text=title,
            anchor="w",
            height=16,
            font=ctk.CTkFont(family=UI_FONT, size=10, weight="bold"),
            text_color=COLORS["text_muted"],
        ).pack(fill="x", padx=16, pady=(11, 0))

        val_lbl = ctk.CTkLabel(
            card,
            text=value,
            anchor="w",
            height=30,
            font=ctk.CTkFont(family=UI_FONT, size=20, weight="bold"),
            text_color=accent,
        )
        val_lbl.pack(fill="x", padx=16)

        sub_lbl = ctk.CTkLabel(
            card,
            text=sub,
            anchor="w",
            height=16,
            font=ctk.CTkFont(family=UI_FONT, size=11),
            text_color=COLORS["text_secondary"],
        )
        sub_lbl.pack(fill="x", padx=16, pady=(0, 6 if return_card else 11))

        if return_card:
            return card, val_lbl, sub_lbl
        return val_lbl, sub_lbl

    @staticmethod
    def _pill(parent, text: str, color: str, strong: bool = False) -> ctk.CTkLabel:
        """Small rounded tag with a tinted background in its own colour."""
        return ctk.CTkLabel(
            parent,
            text=text,
            height=22,
            corner_radius=11,
            padx=9,
            fg_color=_tint(color, 0.22 if strong else 0.14),
            text_color=color,
            font=ctk.CTkFont(family=UI_FONT, size=10, weight="bold"),
        )

    @staticmethod
    def _empty_state(parent, title: str, hint: str, compact: bool = False) -> None:
        """Centered title + hint used wherever a list has nothing to show."""
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.pack(pady=(26, 18) if compact else (56, 40))
        ctk.CTkLabel(
            box, text="◇", font=ctk.CTkFont(family=UI_FONT, size=26), text_color=COLORS["border_accent"]
        ).pack()
        ctk.CTkLabel(
            box, text=title, font=ctk.CTkFont(family=UI_FONT, size=14, weight="bold"), text_color=COLORS["text_primary"]
        ).pack(pady=(4, 2))
        ctk.CTkLabel(
            box, text=hint, font=ctk.CTkFont(family=UI_FONT, size=12), text_color=COLORS["text_muted"]
        ).pack()

    def _build_media_tab(self) -> None:
        self.tab_media.grid_rowconfigure(0, weight=1)
        self.tab_media.grid_columnconfigure(0, weight=1)
        self.media_inspector = MediaInspectorPanel(
            self.tab_media,
            colors=COLORS,
            ui_font=UI_FONT,
            mono_font=MONO_FONT,
            initial_folders=self.config_data.get("media_folders", []),
            ffprobe_path=self.config_data.get("ffprobe_path", ""),
            on_config_change=self._on_media_config_changed,
            on_status=self._set_status,
            on_library_updated=self._on_media_library_updated,
        )
        self.media_inspector.grid(row=0, column=0, sticky="nsew")

    def _build_kodi_tab(self) -> None:
        self.tab_kodi.grid_rowconfigure(0, weight=1)
        self.tab_kodi.grid_columnconfigure(0, weight=1)

        if KodiPanel is None:
            ctk.CTkLabel(
                self.tab_kodi,
                text=(
                    "Kodi support is unavailable.\n\n"
                    "Place kodi_client.py in the same folder as this script and restart.\n"
                    f"({_KODI_IMPORT_ERROR})"
                ),
                justify="center",
                font=ctk.CTkFont(family=UI_FONT, size=13),
                text_color=COLORS["amber"],
            ).grid(row=0, column=0)
            return

        self.kodi_panel = KodiPanel(
            self.tab_kodi,
            colors=COLORS,
            ui_font=UI_FONT,
            mono_font=MONO_FONT,
            kodi_config=self.config_data,
            on_config_change=self._on_kodi_config_changed,
            on_status=self._set_status,
            on_kodi_state=self._on_kodi_state_changed,
            get_disk_items=self._get_disk_items_for_kodi,
        )
        self.kodi_panel.grid(row=0, column=0, sticky="nsew")

    def _build_media_server_tab(self) -> None:
        self.tab_media_server.grid_rowconfigure(0, weight=1)
        self.tab_media_server.grid_columnconfigure(0, weight=1)

        if MediaServerPanel is None:
            ctk.CTkLabel(
                self.tab_media_server,
                text=(
                    "Jellyfin / Plex support is unavailable.\n\n"
                    "Place media_server.py in the same folder as this script and restart.\n"
                    f"({_MEDIA_SERVER_IMPORT_ERROR})"
                ),
                justify="center",
                font=ctk.CTkFont(family=UI_FONT, size=13),
                text_color=COLORS["amber"],
            ).grid(row=0, column=0)
            return

        self.media_server_panel = MediaServerPanel(
            self.tab_media_server,
            colors=COLORS,
            ui_font=UI_FONT,
            mono_font=MONO_FONT,
            server_config=self.config_data,
            on_config_change=self._on_media_server_config_changed,
            on_status=self._set_status,
            on_server_state=self._on_media_server_state_changed,
            get_disk_items=self._get_disk_items_for_kodi,  # same {path,title,media_type,is_corrupt} shape
        )
        self.media_server_panel.grid(row=0, column=0, sticky="nsew")

    def _build_search_tab(self) -> None:
        self.tab_search.grid_rowconfigure(4, weight=1)
        self.tab_search.grid_columnconfigure(0, weight=1)

        # One toolbar card (search / filters / batch actions) instead of three stacked boxes.
        toolbar = ctk.CTkFrame(self.tab_search, fg_color=COLORS["bg_panel"], border_width=1, border_color=COLORS["border"], corner_radius=12)
        toolbar.grid(row=0, column=0, sticky="ew", padx=4, pady=(8, 6))

        def divider() -> None:
            ctk.CTkFrame(toolbar, height=1, fg_color=COLORS["border"], corner_radius=0).pack(fill="x", padx=12)

        bar = ctk.CTkFrame(toolbar, fg_color="transparent")
        bar.pack(fill="x", padx=2, pady=(4, 2))
        bar.grid_columnconfigure(0, weight=1)

        self.search_entry = ctk.CTkEntry(
            bar,
            height=38,
            fg_color=COLORS["bg_card"],
            border_color=COLORS["border"],
            font=ctk.CTkFont(family=UI_FONT, size=13),
            placeholder_text="Search your indexers…  (leave blank to browse the selected category)",
        )
        self.search_entry.grid(row=0, column=0, sticky="ew", padx=(12, 8), pady=8)
        self.search_entry.bind("<Return>", lambda _e: self._on_search_button())
        self.search_entry.bind("<Up>", lambda _e: self._cycle_recent(1))
        self.search_entry.bind("<Down>", lambda _e: self._cycle_recent(-1))

        self.recent_var = ctk.StringVar(value=self.RECENT_SEARCH_LABEL)
        self.recent_menu = ctk.CTkOptionMenu(
            bar,
            variable=self.recent_var,
            values=["No recent searches"],
            width=96,
            height=34,
            dynamic_resizing=False,
            fg_color=COLORS["bg_card"],
            button_color=COLORS["bg_card_alt"],
            command=self._on_recent_chosen,
        )
        self.recent_menu.grid(row=0, column=1, padx=(0, 4), pady=8)

        self.indexer_select_var = ctk.StringVar(value="All Enabled")
        self.indexer_select_menu = ctk.CTkOptionMenu(
            bar,
            variable=self.indexer_select_var,
            values=["All Enabled"],
            width=150,
            height=34,
            fg_color=COLORS["bg_card"],
            button_color=COLORS["border"],
            command=lambda _v: self._apply_cached_caps_for_selection(),
        )
        self.indexer_select_menu.grid(row=0, column=2, padx=4, pady=8)

        self.indexer_cat_var = ctk.StringVar(value="All Categories")
        self.indexer_cat_menu = ctk.CTkOptionMenu(
            bar,
            variable=self.indexer_cat_var,
            values=[label for label, _cid in FALLBACK_INDEXER_CATEGORIES],
            width=230,
            height=34,
            fg_color=COLORS["bg_card"],
            button_color=COLORS["border"],
        )
        self.indexer_cat_menu.grid(row=0, column=3, padx=4, pady=8)

        cats_btn = ctk.CTkButton(
            bar,
            text="Categories",
            image=app_icon("refresh", COLORS["text_secondary"], 13),
            width=112,
            height=34,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_alt"],
            command=lambda: self._refresh_indexer_categories(force=True),
        )
        cats_btn.grid(row=0, column=4, padx=2, pady=8)
        Tooltip(cats_btn, "Reload the category list from the selected indexer(s)")

        self.search_btn = ctk.CTkButton(
            bar,
            text="Search",
            image=app_icon("search", ON_ACCENT),
            width=110,
            height=38,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color="#041016",
            font=ctk.CTkFont(family=UI_FONT, size=12, weight="bold"),
            command=self._on_search_button,
        )
        self.search_btn.grid(row=0, column=5, padx=(6, 12), pady=8)
        self._refresh_recent_menu()

        divider()
        filters = ctk.CTkFrame(toolbar, fg_color="transparent")
        filters.pack(fill="x", padx=2, pady=2)

        self.local_filter_entry = ctk.CTkEntry(
            filters,
            width=165,
            height=30,
            fg_color=COLORS["bg_card"],
            border_color=COLORS["border"],
            placeholder_text="Filter results locally...",
        )
        self.local_filter_entry.pack(side="left", padx=(12, 6), pady=7)
        self.local_filter_entry.bind("<KeyRelease>", lambda _e: self._apply_filters_and_render(reset_page=True))
        self.local_filter_entry.bind("<Escape>", self._clear_local_filter_box)

        self.sort_var = ctk.StringVar(value=SORT_OPTIONS[0])
        ctk.CTkOptionMenu(
            filters,
            variable=self.sort_var,
            values=SORT_OPTIONS,
            width=142,
            height=30,
            fg_color=COLORS["bg_card"],
            button_color=COLORS["border"],
            command=lambda _v: self._apply_filters_and_render(reset_page=True),
        ).pack(side="left", padx=(0, 6), pady=7)

        self.quality_var = ctk.StringVar(value=QUALITY_FILTER_OPTIONS[0])
        ctk.CTkOptionMenu(
            filters,
            variable=self.quality_var,
            values=QUALITY_FILTER_OPTIONS,
            width=110,
            height=30,
            fg_color=COLORS["bg_card"],
            button_color=COLORS["border"],
            command=lambda _v: self._apply_filters_and_render(reset_page=True),
        ).pack(side="left", padx=(0, 6), pady=7)

        self.size_filter_var = ctk.StringVar(value=SIZE_FILTER_OPTIONS[0])
        ctk.CTkOptionMenu(
            filters,
            variable=self.size_filter_var,
            values=SIZE_FILTER_OPTIONS,
            width=108,
            height=30,
            fg_color=COLORS["bg_card"],
            button_color=COLORS["border"],
            command=lambda _v: self._apply_filters_and_render(reset_page=True),
        ).pack(side="left", padx=(0, 6), pady=7)

        self.lang_var = ctk.StringVar(value=LANGUAGE_FILTER_OPTIONS[0])
        ctk.CTkOptionMenu(
            filters,
            variable=self.lang_var,
            values=LANGUAGE_FILTER_OPTIONS,
            width=116,
            height=30,
            fg_color=COLORS["bg_card"],
            button_color=COLORS["border"],
            command=lambda _v: self._apply_filters_and_render(reset_page=True),
        ).pack(side="left", padx=(0, 6), pady=7)

        self.dedupe_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            filters,
            text="Hide Dupes",
            variable=self.dedupe_var,
            checkbox_width=18,
            checkbox_height=18,
            fg_color=COLORS["accent"],
            font=ctk.CTkFont(family=UI_FONT, size=11),
            command=lambda: self._apply_filters_and_render(reset_page=True),
        ).pack(side="left", padx=(4, 6), pady=7)

        ctk.CTkButton(
            filters,
            text="Reset",
            width=54,
            height=30,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_alt"],
            command=self._reset_search_filters,
        ).pack(side="left", padx=(2, 6), pady=7)

        self.search_cat_var = ctk.StringVar(value=AUTO_CAT_LABEL)
        self.search_cat_menu = ctk.CTkOptionMenu(
            filters,
            variable=self.search_cat_var,
            values=self._sab_cat_menu_values(),
            width=126,
            height=30,
            fg_color=COLORS["bg_card"],
            button_color=COLORS["border"],
        )
        self.search_cat_menu.pack(side="right", padx=(0, 10), pady=7)

        ctk.CTkLabel(
            filters,
            text="SAB Cat:",
            font=ctk.CTkFont(family=UI_FONT, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side="right", padx=(6, 4), pady=7)

        divider()
        batch_bar = ctk.CTkFrame(toolbar, fg_color="transparent")
        batch_bar.pack(fill="x", padx=2, pady=(2, 4))

        ctk.CTkButton(
            batch_bar,
            text="Select Page",
            width=92,
            height=30,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_alt"],
            command=self._select_current_search_page,
        ).pack(side="left", padx=(12, 4), pady=6)

        ctk.CTkButton(
            batch_bar,
            text="Clear Sel",
            width=78,
            height=30,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_alt"],
            command=self._clear_search_selection,
        ).pack(side="left", padx=4, pady=6)

        self.batch_send_search_btn = ctk.CTkButton(
            batch_bar,
            text="Send Selected (0)",
            image=app_icon("send", ON_ACCENT, 13),
            width=156,
            height=30,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color="#041016",
            font=ctk.CTkFont(family=UI_FONT, size=11, weight="bold"),
            command=self._send_selected_search_to_sab,
        )
        self.batch_send_search_btn.pack(side="left", padx=(6, 6), pady=6)

        ctk.CTkButton(
            batch_bar,
            text="Grab Best",
            image=app_icon("star", COLORS["accent"], 13),
            width=114,
            height=30,
            fg_color="transparent",
            hover_color=COLORS["bg_card_alt"],
            border_width=1,
            border_color=COLORS["border_accent"],
            text_color=COLORS["accent"],
            font=ctk.CTkFont(family=UI_FONT_SEMIBOLD, size=12),
            command=self._grab_best,
        ).pack(side="left", padx=(0, 10), pady=6)

        self.lib_filter_var = ctk.StringVar(value=LIBRARY_FILTER_OPTIONS[0])
        ctk.CTkOptionMenu(
            batch_bar,
            variable=self.lib_filter_var,
            values=LIBRARY_FILTER_OPTIONS,
            width=126,
            height=30,
            fg_color=COLORS["bg_card"],
            button_color=COLORS["border"],
            command=lambda _v: self._apply_filters_and_render(reset_page=True),
        ).pack(side="left", padx=(0, 8), pady=6)

        self.lib_index_badge = ctk.CTkLabel(
            batch_bar,
            text="Lib: Not scanned",
            height=24,
            corner_radius=12,
            padx=10,
            fg_color=COLORS["bg_card"],
            font=ctk.CTkFont(family=UI_FONT_SEMIBOLD, size=11),
            text_color=COLORS["text_muted"],
        )
        self.lib_index_badge.pack(side="left", padx=4, pady=6)

        self.next_page_btn = ctk.CTkButton(
            batch_bar,
            text="Next ▶",
            width=72,
            height=30,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_alt"],
            command=lambda: self._change_search_page(1),
        )
        self.next_page_btn.pack(side="right", padx=(4, 12), pady=6)

        self.page_label = ctk.CTkLabel(
            batch_bar,
            text="Page 1 / 1",
            font=ctk.CTkFont(family=UI_FONT_SEMIBOLD, size=11),
            text_color=COLORS["text_secondary"],
        )
        self.page_label.pack(side="right", padx=8, pady=6)

        self.prev_page_btn = ctk.CTkButton(
            batch_bar,
            text="◀ Prev",
            width=72,
            height=30,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_alt"],
            command=lambda: self._change_search_page(-1),
        )
        self.prev_page_btn.pack(side="right", padx=4, pady=6)

        self.results_count_label = ctk.CTkLabel(
            batch_bar,
            text="",
            font=ctk.CTkFont(family=UI_FONT, size=11),
            text_color=COLORS["text_muted"],
        )
        self.results_count_label.pack(side="right", padx=14, pady=6)

        self.search_progress = ctk.CTkProgressBar(
            self.tab_search,
            height=4,
            mode="indeterminate",
            fg_color=COLORS["bg_root"],
            progress_color=COLORS["accent"],
        )

        self.search_results_frame = ctk.CTkScrollableFrame(
            self.tab_search, fg_color=COLORS["bg_panel"], border_width=1, border_color=COLORS["border"], corner_radius=12
        )
        self.search_results_frame.grid(row=4, column=0, sticky="nsew", padx=4, pady=(0, 4))

        self._empty_state(
            self.search_results_frame,
            "Search your indexers",
            "Type a title above and press Enter, or pick a category and click Search to browse.",
        )

    def _build_local_tab(self) -> None:
        self.tab_local.grid_rowconfigure(1, weight=1)
        self.tab_local.grid_columnconfigure(0, weight=1)
        self.tab_local.grid_columnconfigure(1, weight=1)

        top = ctk.CTkFrame(self.tab_local, fg_color=COLORS["bg_panel"], border_width=1, border_color=COLORS["border"], corner_radius=10)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=4, pady=(6, 8))
        top.grid_columnconfigure(0, weight=1)

        self.folder_entry = ctk.CTkEntry(top, height=34, fg_color=COLORS["bg_card"], border_color=COLORS["border"])
        self.folder_entry.insert(0, self.config_data["nzb_folder"])
        self.folder_entry.grid(row=0, column=0, sticky="ew", padx=(12, 8), pady=10)

        sec = COLORS["text_secondary"]
        ctk.CTkButton(top, text="Browse...", image=app_icon("folder_open", sec, 13), width=104, fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_alt"], command=self._browse_folder).grid(row=0, column=1, padx=4, pady=10)
        ctk.CTkButton(top, text="Open Folder", image=app_icon("folder", sec, 13), width=112, fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_alt"], command=self._open_local_folder).grid(row=0, column=2, padx=4, pady=10)
        ctk.CTkButton(top, text="Scan", image=app_icon("refresh", sec, 13), width=80, fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_alt"], command=self._scan_local_folder).grid(row=0, column=3, padx=4, pady=10)
        ctk.CTkButton(
            top,
            text="Open .nzb File...",
            image=app_icon("file", ON_ACCENT, 13),
            width=140,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color="#041016",
            font=ctk.CTkFont(family=UI_FONT, size=12, weight="bold"),
            command=self._pick_single_nzb,
        ).grid(row=0, column=4, padx=(4, 12), pady=10)

        local_left = ctk.CTkFrame(self.tab_local, fg_color=COLORS["bg_panel"], border_width=1, border_color=COLORS["border"], corner_radius=10)
        local_left.grid(row=1, column=0, sticky="nsew", padx=(4, 5), pady=(0, 4))
        local_left.grid_rowconfigure(1, weight=1)
        local_left.grid_columnconfigure(0, weight=1)

        local_bar = ctk.CTkFrame(local_left, fg_color="transparent")
        local_bar.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))

        ctk.CTkButton(
            local_bar,
            text="All",
            width=46,
            height=26,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_alt"],
            command=self._select_all_local,
        ).pack(side="left", padx=(0, 4))

        ctk.CTkButton(
            local_bar,
            text="None",
            width=52,
            height=26,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_alt"],
            command=self._clear_local_selection,
        ).pack(side="left", padx=(0, 6))

        self.local_batch_send_btn = ctk.CTkButton(
            local_bar,
            text="Send Selected (0)",
            image=app_icon("send", ON_ACCENT, 12),
            width=144,
            height=26,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color="#041016",
            font=ctk.CTkFont(family=UI_FONT, size=11, weight="bold"),
            command=self._upload_selected_batch_nzbs,
        )
        self.local_batch_send_btn.pack(side="left", padx=(0, 8))

        self.auto_archive_var = ctk.BooleanVar(value=self.config_data.get("auto_archive_sent", False))
        ctk.CTkSwitch(
            local_bar,
            text="Archive Sent",
            variable=self.auto_archive_var,
            progress_color=COLORS["accent"],
            font=ctk.CTkFont(family=UI_FONT, size=11),
            command=self._on_auto_archive_toggled,
        ).pack(side="right")

        self.watch_var = ctk.BooleanVar(value=self.config_data.get("watch_folder", False))
        ctk.CTkSwitch(
            local_bar,
            text="Auto-send new",
            variable=self.watch_var,
            progress_color=COLORS["accent"],
            font=ctk.CTkFont(family=UI_FONT, size=11),
            command=self._on_watch_toggled,
        ).pack(side="right", padx=(0, 12))

        self.local_list_frame = ctk.CTkScrollableFrame(local_left, fg_color="transparent")
        self.local_list_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 6))

        inspector = ctk.CTkFrame(self.tab_local, fg_color=COLORS["bg_panel"], border_width=1, border_color=COLORS["border"], corner_radius=10)
        inspector.grid(row=1, column=1, sticky="nsew", padx=(5, 4), pady=(0, 4))
        inspector.grid_rowconfigure(1, weight=1)
        inspector.grid_columnconfigure(0, weight=1)

        self.inspector_title = ctk.CTkLabel(
            inspector,
            text="Select an .nzb file to inspect",
            anchor="w",
            font=ctk.CTkFont(family=UI_FONT, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        self.inspector_title.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))

        self.inspector_box = ctk.CTkTextbox(
            inspector,
            fg_color=COLORS["bg_card"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(family=MONO_FONT, size=12),
        )
        self.inspector_box.grid(row=1, column=0, sticky="nsew", padx=14, pady=6)
        self.inspector_box.configure(state="disabled")

        actions = ctk.CTkFrame(inspector, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew", padx=14, pady=(6, 12))

        self.local_cat_var = ctk.StringVar(value=AUTO_CAT_LABEL)
        self.local_cat_menu = ctk.CTkOptionMenu(
            actions,
            variable=self.local_cat_var,
            values=self._sab_cat_menu_values(),
            width=130,
            fg_color=COLORS["bg_card"],
            button_color=COLORS["border"],
        )
        self.local_cat_menu.pack(side="left", padx=(0, 8))

        self.local_send_btn = ctk.CTkButton(
            actions,
            text="Send to SABnzbd",
            image=app_icon("send", ON_ACCENT, 13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color="#041016",
            font=ctk.CTkFont(family=UI_FONT, size=12, weight="bold"),
            command=self._upload_selected_nzb,
        )
        self.local_send_btn.pack(side="left", padx=(0, 8))

        self.local_popout_btn = ctk.CTkButton(
            actions,
            text="Pop-out / XML",
            image=app_icon("code", COLORS["text_secondary"], 13),
            width=122,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_alt"],
            command=self._open_selected_nzb_popup,
        )
        self.local_popout_btn.pack(side="left")

        self.local_delete_btn = ctk.CTkButton(
            actions,
            text="Delete File",
            image=app_icon("delete", "#FFFFFF", 13),
            width=108,
            fg_color=COLORS["red"],
            hover_color="#DC2626",
            command=self._delete_selected_nzb,
        )
        self.local_delete_btn.pack(side="right")
        self._set_local_actions(False)

    def _set_local_actions(self, has_file: bool, parsed: bool = True) -> None:
        """The inspector's buttons only light up once a file is selected (Delete stays muted until then)."""
        state = "normal" if has_file else "disabled"
        self.local_send_btn.configure(
            state=state, fg_color=COLORS["accent"] if has_file else COLORS["bg_card"],
            image=app_icon("send", ON_ACCENT if has_file else COLORS["text_muted"], 13),
        )
        self.local_popout_btn.configure(state="normal" if has_file and parsed else "disabled")
        self.local_delete_btn.configure(
            state=state, fg_color=COLORS["red"] if has_file else COLORS["bg_card"],
            image=app_icon("delete", "#FFFFFF" if has_file else COLORS["text_muted"], 13),
        )

    def _build_sab_tab(self) -> None:
        self.tab_sab.grid_rowconfigure(1, weight=1)
        self.tab_sab.grid_rowconfigure(3, weight=1)
        self.tab_sab.grid_columnconfigure(0, weight=1)

        ctrl = ctk.CTkFrame(self.tab_sab, fg_color=COLORS["bg_panel"], border_width=1, border_color=COLORS["border"], corner_radius=10)
        ctrl.grid(row=0, column=0, sticky="ew", padx=4, pady=(6, 8))

        self.sab_summary_label = ctk.CTkLabel(
            ctrl,
            text="ACTIVE QUEUE  •  Connecting…",
            font=ctk.CTkFont(family=UI_FONT, size=12, weight="bold"),
            text_color=COLORS["accent"],
        )
        self.sab_summary_label.pack(side="left", padx=14, pady=10)

        self.auto_refresh_var = ctk.BooleanVar(value=self.config_data.get("auto_refresh", True))
        ctk.CTkSwitch(
            ctrl,
            text="Auto-Refresh",
            variable=self.auto_refresh_var,
            progress_color=COLORS["accent"],
            command=self._on_auto_refresh_toggled,
        ).pack(side="right", padx=14, pady=10)

        self.notify_var = ctk.BooleanVar(value=self.config_data.get("desktop_notifications", True))
        ctk.CTkSwitch(
            ctrl,
            text="Notifications",
            variable=self.notify_var,
            progress_color=COLORS["accent"],
            command=self._on_notifications_toggled,
        ).pack(side="right", padx=(6, 4), pady=10)

        ctk.CTkButton(
            ctrl,
            text="Refresh",
            image=app_icon("refresh", COLORS["text_secondary"], 13),
            width=92,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_alt"],
            command=self._refresh_sab_all,
        ).pack(side="right", padx=6, pady=10)

        ctk.CTkButton(
            ctrl,
            text="Unlim",
            width=58,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_alt"],
            command=lambda: self._apply_speed_limit("0"),
        ).pack(side="right", padx=2, pady=10)

        ctk.CTkButton(
            ctrl,
            text="Set Speed",
            width=78,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_alt"],
            command=self._apply_speed_limit,
        ).pack(side="right", padx=2, pady=10)

        self.speed_entry = ctk.CTkEntry(
            ctrl,
            width=84,
            fg_color=COLORS["bg_card"],
            border_color=COLORS["border"],
            placeholder_text="KB/s or %",
        )
        self.speed_entry.pack(side="right", padx=(6, 4), pady=10)
        self.speed_entry.bind("<Return>", lambda _e: self._apply_speed_limit())

        self.speed_limit_badge = ctk.CTkLabel(
            ctrl,
            text="Limit: Unlimited",
            fg_color=COLORS["bg_card"],
            corner_radius=6,
            padx=8,
            pady=3,
            font=ctk.CTkFont(family=UI_FONT, size=10),
            text_color=COLORS["text_secondary"],
        )
        self.speed_limit_badge.pack(side="right", padx=6, pady=10)

        self.queue_frame = ctk.CTkScrollableFrame(
            self.tab_sab, fg_color=COLORS["bg_panel"], border_width=1, border_color=COLORS["border"], corner_radius=10
        )
        self.queue_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 8))

        hist_hdr = ctk.CTkFrame(self.tab_sab, fg_color="transparent")
        hist_hdr.grid(row=2, column=0, sticky="ew", padx=6, pady=(2, 4))

        self.history_header_label = ctk.CTkLabel(
            hist_hdr,
            text="RECENT HISTORY",
            anchor="w",
            font=ctk.CTkFont(family=UI_FONT, size=11, weight="bold"),
            text_color=COLORS["text_secondary"],
        )
        self.history_header_label.pack(side="left", padx=4)

        ctk.CTkButton(
            hist_hdr,
            text="Clear All",
            width=74,
            height=24,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["red"],
            font=ctk.CTkFont(family=UI_FONT, size=11),
            command=lambda: self._clear_sab_history(failed_only=False),
        ).pack(side="right", padx=(4, 0))

        ctk.CTkButton(
            hist_hdr,
            text="Clear Failed",
            width=86,
            height=24,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["amber"],
            font=ctk.CTkFont(family=UI_FONT, size=11),
            command=lambda: self._clear_sab_history(failed_only=True),
        ).pack(side="right", padx=4)

        self.history_frame = ctk.CTkScrollableFrame(
            self.tab_sab, fg_color=COLORS["bg_panel"], border_width=1, border_color=COLORS["border"], corner_radius=10
        )
        self.history_frame.grid(row=3, column=0, sticky="nsew", padx=4, pady=(0, 4))

    def _build_log_tab(self) -> None:
        self.tab_log.grid_rowconfigure(1, weight=1)
        self.tab_log.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(self.tab_log, fg_color=COLORS["bg_panel"], border_width=1, border_color=COLORS["border"], corner_radius=10)
        top.grid(row=0, column=0, sticky="ew", padx=4, pady=(6, 8))

        ctk.CTkLabel(
            top,
            text="APPLICATION ACTIVITY & DIAGNOSTIC LOG",
            font=ctk.CTkFont(family=UI_FONT, size=12, weight="bold"),
            text_color=COLORS["accent"],
        ).pack(side="left", padx=14, pady=10)

        ctk.CTkButton(
            top,
            text="Clear Log",
            image=app_icon("delete", COLORS["text_secondary"], 12),
            width=98,
            height=28,
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_alt"],
            command=self._clear_log,
        ).pack(side="right", padx=12, pady=8)

        self.log_box = ctk.CTkTextbox(
            self.tab_log,
            fg_color=COLORS["bg_panel"],
            border_width=1,
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(family=MONO_FONT, size=12),
        )
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        self.log_box.tag_config("INFO", foreground=COLORS["text_secondary"])
        self.log_box.tag_config("OK", foreground=COLORS["green"])
        self.log_box.tag_config("WARN", foreground=COLORS["amber"])
        self.log_box.tag_config("ERROR", foreground=COLORS["red"])
        self.log_box.configure(state="disabled")
        self._log_activity("Operations Center initialized.", "OK")

    def _set_status(self, msg: str, log: bool = False, level: str = "INFO") -> None:
        clean = redact_text(msg)
        self.status_bar.configure(text=clean)
        if log:
            self._log_activity(clean, level=level)

    def _log_activity(self, msg: str, level: str = "INFO") -> None:
        if not hasattr(self, "log_box"):
            return
        lvl = level.upper() if level.upper() in ("INFO", "OK", "WARN", "ERROR") else "INFO"
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{ts}] [{lvl:<5}] {redact_text(msg)}\n", lvl)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _refresh_indexer_selector(self) -> None:
        names = [c.name for c in self.indexers]
        values = ["All Enabled"] + names if len(names) > 1 else (names or ["No Indexers"])
        self.indexer_select_menu.configure(values=values)
        if self.indexer_select_var.get() not in values:
            self.indexer_select_var.set(values[0])

    def _get_selected_clients(self) -> list[NewznabClient]:
        choice = self.indexer_select_var.get()
        if choice == "All Enabled":
            return list(self.indexers)
        return [c for c in self.indexers if c.name == choice] or list(self.indexers)

    def _update_indexer_pill(self, state: str = "") -> None:
        active_with_keys = [c for c in self.indexers if c.api_key]
        if not active_with_keys:
            self.indexer_status_pill.configure(text="●  Indexers: Add API Key", text_color=COLORS["amber"])
            return
        count = len(active_with_keys)
        noun = "Indexer" if count == 1 else "Indexers"
        if state == "error":
            self.indexer_status_pill.configure(text=f"●  {count} {noun}: Error", text_color=COLORS["red"])
        elif state == "ok":
            self.indexer_status_pill.configure(text=f"●  {count} {noun}: Connected", text_color=COLORS["green"])
        else:
            label = active_with_keys[0].name if count == 1 else f"{count} Active"
            self.indexer_status_pill.configure(text=f"●  Indexer: {label}", text_color=COLORS["accent"])

    def _on_auto_refresh_toggled(self) -> None:
        enabled = self.auto_refresh_var.get()
        self.config_data["auto_refresh"] = enabled
        self._write_config_file(self.config_data)
        self._set_status(f"Auto-refresh {'enabled' if enabled else 'paused'}.", log=True)

    # ------------------------------------------------------------------ watch folder
    WATCH_INTERVAL_MS = 5000
    WATCH_SETTLE_S = 3.0  # a file must be unchanged this long (fully written) before it's sent

    def _watch_folder_path(self) -> Path:
        return Path(self.folder_entry.get().strip() or self.config_data.get("nzb_folder") or Path.home())

    def _list_watch_nzbs(self) -> dict[str, tuple[float, int]]:
        out: dict[str, tuple[float, int]] = {}
        try:
            with os.scandir(self._watch_dir or self._watch_folder_path()) as it:
                for e in it:
                    if e.is_file() and e.name.lower().endswith(".nzb"):
                        st = e.stat()
                        out[e.path] = (st.st_mtime, st.st_size)
        except OSError:
            pass
        return out

    def _on_watch_toggled(self) -> None:
        on = bool(self.watch_var.get())
        self.config_data["watch_folder"] = on
        self._safe_write_config("watch folder")
        if on:
            self._start_watch()
        else:
            self._stop_watch()
            self._set_status("Auto-send stopped.", log=True)

    def _start_watch(self) -> None:
        """Files already in the folder are left alone; only NZBs that appear from now on are sent."""
        self._stop_watch()
        # Lock onto this folder: typing another path in the folder box must not make the watcher
        # send everything already sitting in that other folder.
        self._watch_dir = self._watch_folder_path()
        self._watch_seen = {p: m for p, (m, _sz) in self._list_watch_nzbs().items()}
        self._set_status(
            f"Auto-send on: new .nzb files in {self._watch_dir} will go to SABnzbd.", log=True, level="OK"
        )
        self._watch_after_id = self.after(self.WATCH_INTERVAL_MS, self._watch_tick)

    def _stop_watch(self) -> None:
        if self._watch_after_id:
            try:
                self.after_cancel(self._watch_after_id)
            except Exception:
                pass
            self._watch_after_id = None

    def _watch_tick(self) -> None:
        self._watch_after_id = None
        if self._shutting_down or not self.watch_var.get():
            return
        if self.config_data.get("sab_api_key"):
            now = time.time()
            for path, (mtime, size) in self._list_watch_nzbs().items():
                if path in self._watch_in_flight or self._watch_seen.get(path) == mtime:
                    continue
                if size <= 0 or now - mtime < self.WATCH_SETTLE_S:
                    continue  # still being written; check again next tick
                self._watch_seen[path] = mtime
                self._auto_send_nzb(Path(path))
        self._watch_after_id = self.after(self.WATCH_INTERVAL_MS, self._watch_tick)

    def _auto_send_nzb(self, path: Path) -> None:
        cat = self._resolve_sab_category(self.local_cat_var.get(), item_category="", title=path.stem)
        archive = self.auto_archive_var.get()
        self._watch_in_flight.add(str(path))

        def task():
            try:
                parse_local_nzb(path)  # reject half-downloaded / non-NZB files before sending
            except Exception as exc:
                raise RuntimeError(f"not a valid NZB ({exc})") from None
            self.sab.add_file(path, category=cat)
            if archive:
                try:
                    self._archive_sent_nzb(path)
                except Exception as exc:
                    return f" (archiving failed: {_format_http_error(exc)})"
                return " and archived to sent/"
            return ""

        def ok(note):
            self._watch_in_flight.discard(str(path))
            self._increment_daily_nzb_count()
            self._set_status(f"Auto-sent {path.name} to SABnzbd [cat={cat}]{note}.", log=True, level="OK")
            self._scan_local_folder()
            self._refresh_sab_all()

        def err(exc):
            self._watch_in_flight.discard(str(path))
            self._set_status(f"Auto-send failed for {path.name}: {exc}", log=True, level="ERROR")

        self._set_status(f"Auto-sending {path.name} to SABnzbd...")
        self._run_bg(task, on_ok=ok, on_err=err)

    def _on_auto_archive_toggled(self) -> None:
        enabled = self.auto_archive_var.get()
        self.config_data["auto_archive_sent"] = enabled
        self._write_config_file(self.config_data)
        self._set_status(
            f"Archive Sent .nzb {'enabled (moves sent files to sent/)' if enabled else 'disabled'}.",
            log=True,
        )

    def _update_local_drive_space(self) -> None:
        target = Path(self.config_data.get("nzb_folder") or Path.home())
        if not target.exists():
            target = Path.home()
        try:
            usage = shutil.disk_usage(target)
            used_pct = usage.used / usage.total if usage.total else 0.0
            color = COLORS["red"] if used_pct > 0.9 else (COLORS["amber"] if used_pct > 0.78 else COLORS["green"])
            self.card_drive_val.configure(text=f"{format_bytes(usage.free)} free", text_color=color)
            self.card_drive_sub.configure(
                text=f"Local: {format_bytes(usage.used)} / {format_bytes(usage.total)} ({used_pct * 100:.0f}% used)"
            )
            self.drive_bar.set(min(max(used_pct, 0.0), 1.0))
            self.drive_bar.configure(progress_color=color)
        except Exception:
            pass

    def _load_cached_indexer_categories(self) -> None:
        if CAPS_CACHE_FILE.exists():
            try:
                raw = json.loads(CAPS_CACHE_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and isinstance(raw.get("by_url"), dict):
                    self.caps_by_url = {k: [tuple(p) for p in v] for k, v in raw["by_url"].items()}
                elif isinstance(raw, list) and raw and self.indexers:
                    self.caps_by_url[self.indexers[0].base_url] = [tuple(p) for p in raw]
            except Exception:
                pass
        self._apply_cached_caps_for_selection()

    def _apply_cached_caps_for_selection(self) -> None:
        clients = self._get_selected_clients()
        cats = None
        for c in clients:
            if c.base_url in self.caps_by_url:
                cats = self.caps_by_url[c.base_url]
                break
        if not cats:
            cats = FALLBACK_INDEXER_CATEGORIES

        self.indexer_cat_map = {label: cid for label, cid in cats}
        labels = [label for label, _cid in cats]
        self.indexer_cat_menu.configure(values=labels)
        if self.indexer_cat_var.get() not in self.indexer_cat_map:
            self.indexer_cat_var.set(labels[0])

    def _auto_fetch_missing_caps(self) -> None:
        missing = [c for c in self.indexers if c.base_url and c.base_url not in self.caps_by_url]
        if not missing:
            return

        def fetch_all():
            updated: dict[str, list[tuple[str, str]]] = {}
            for c in missing:
                try:
                    updated[c.base_url] = c.get_categories()
                except Exception:
                    pass
            return updated

        def ok(updated: dict[str, list[tuple[str, str]]]):
            if not updated:
                return
            self.caps_by_url.update(updated)
            try:
                _atomic_write_json(CAPS_CACHE_FILE, {"by_url": self.caps_by_url})
            except Exception:
                pass
            self._apply_cached_caps_for_selection()
            self._log_activity(f"Auto-cached categories for {len(updated)} indexer(s).", "INFO")

        self._run_bg(fetch_all, on_ok=ok, on_err=lambda _e: None)

    def _refresh_indexer_categories(self, force: bool = False) -> None:
        if not force:
            self._load_cached_indexer_categories()
            return

        clients = self._get_selected_clients()
        if not clients:
            self._set_status("No enabled indexers configured in Settings.", log=True, level="WARN")
            return
        target = clients[0]

        def ok(cats: list[tuple[str, str]]):
            self.caps_by_url[target.base_url] = cats
            self._apply_cached_caps_for_selection()
            try:
                _atomic_write_json(CAPS_CACHE_FILE, {"by_url": self.caps_by_url})
            except Exception:
                pass
            self._update_indexer_pill("ok")
            self._set_status(f"Loaded {len(cats) - 1} categories from {target.name}.", log=True, level="OK")

        def err(exc):
            self._update_indexer_pill("error")
            self._set_status(f"Category refresh failed for {target.name} ({exc})", log=True, level="WARN")

        self._run_bg(target.get_categories, on_ok=ok, on_err=err)

    def _clear_local_filter_box(self, _event=None) -> None:
        self.local_filter_entry.delete(0, "end")
        self._apply_filters_and_render(reset_page=True)

    def _reset_search_filters(self) -> None:
        self.local_filter_entry.delete(0, "end")
        self.sort_var.set(SORT_OPTIONS[0])
        self.quality_var.set(QUALITY_FILTER_OPTIONS[0])
        self.size_filter_var.set(SIZE_FILTER_OPTIONS[0])
        self.lang_var.set(LANGUAGE_FILTER_OPTIONS[0])
        self.lib_filter_var.set(LIBRARY_FILTER_OPTIONS[0])
        self.dedupe_var.set(False)
        self._apply_filters_and_render(reset_page=True)

    def _set_search_busy(self, busy: bool) -> None:
        self._search_in_flight = busy
        if busy:
            self.search_btn.configure(text="Cancel", image=app_icon("cancel", "#FFFFFF"), fg_color=COLORS["red"], hover_color="#DC2626", text_color="#FFFFFF")
            self.search_progress.grid(row=3, column=0, sticky="ew", padx=6, pady=(2, 2))
            self.search_progress.start()
        else:
            self.search_btn.configure(text="Search", image=app_icon("search", ON_ACCENT), fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"], text_color="#041016")
            self.search_progress.stop()
            self.search_progress.grid_forget()

    def _on_search_button(self) -> None:
        if self._search_in_flight:
            self._search_seq += 1
            self._set_search_busy(False)
            self._set_status("Search cancelled.", log=True, level="WARN")
        else:
            self._run_indexer_search()

    SEARCH_PAGE_LIMIT = 100  # results requested per indexer per page

    def _run_indexer_search(self, load_more: bool = False) -> None:
        """Runs a new search, or (load_more=True) fetches the next page from every indexer that
        still has results, continuing the same query, category and search mode."""
        if load_more:
            ctx = self._search_ctx
            if not ctx or self._search_in_flight:
                return
            clients = [c for c in ctx["clients"] if c.name not in ctx["exhausted"]]
            if not clients:
                return
            query, cat_id, cat_label = ctx["query"], ctx["cat_id"], ctx["cat_label"]
        else:
            clients = self._get_selected_clients()
            if not clients:
                self._set_status("No enabled indexers configured. Open Settings to add one.", log=True, level="WARN")
                return
            query = self.search_entry.get().strip()
            cat_label = self.indexer_cat_var.get()
            cat_id = self.indexer_cat_map.get(cat_label, "")
            if not query and not cat_id:
                self._set_status("Enter a search term or pick a category/subcategory first.")
                return
            ctx = {
                "query": query, "cat_id": cat_id, "cat_label": cat_label, "clients": list(clients),
                "offsets": {c.name: 0 for c in clients}, "modes": {c.name: "auto" for c in clients},
                "exhausted": set(),
            }
            self._search_ctx = ctx
            self._recent_pos = -1
            if query:
                self._remember_search(query)

        self._search_seq += 1
        req_id = self._search_seq
        limit = self.SEARCH_PAGE_LIMIT
        desc = f"'{query}'" if query else f"category '{cat_label}'"
        target_names = ", ".join(c.name for c in clients)
        self._set_search_busy(True)
        verb = "Loading more" if load_more else "Searching"
        self._set_status(f"{verb} [{target_names}] for {desc}...", log=True)
        jobs = [(c, ctx["offsets"].get(c.name, 0), ctx["modes"].get(c.name, "auto")) for c in clients]

        def multi_search():
            per_client: dict[int, list[dict]] = {}
            errors: list[str] = []
            with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as pool:
                futs = {
                    i: pool.submit(c.search, query, cat_id, limit, off, mode)
                    for i, (c, off, mode) in enumerate(jobs)
                }
                for i, fut in futs.items():
                    try:
                        per_client[i] = fut.result()
                    except Exception as exc:
                        per_client[i] = []
                        errors.append(f"{jobs[i][0].name}: {_format_http_error(exc)}")
            return per_client, errors

        def ok(res):
            if req_id != self._search_seq:
                return
            per_client, errors = res
            self._set_search_busy(False)
            for err_msg in errors:
                self._log_activity(err_msg, "WARN")

            # Paging bookkeeping per indexer (the search mode is fixed by the first page).
            got_tv = False
            for i, (c, off, _mode) in enumerate(jobs):
                bucket = per_client.get(i, [])
                ctx["offsets"][c.name] = off + len(bucket)
                if bucket and ctx["modes"].get(c.name) == "auto":
                    ctx["modes"][c.name] = bucket[0].get("search_mode", "text")
                if any(r.get("search_mode") == "tv" for r in bucket):
                    got_tv = True
                if len(bucket) < limit:
                    ctx["exhausted"].add(c.name)

            # Interleave indexers so each one's top results stay near the top.
            existing = self.current_search_results if load_more else []
            seen = {it["uid"] for it in existing}
            seq = max((it["index"] for it in existing), default=-1) + 1
            new_items: list[dict] = []
            max_len = max((len(v) for v in per_client.values()), default=0)
            for r in range(max_len):
                for i in range(len(jobs)):
                    bucket = per_client.get(i, [])
                    if r < len(bucket) and bucket[r]["uid"] not in seen:
                        it = bucket[r]
                        it["index"] = seq
                        seq += 1
                        seen.add(it["uid"])
                        new_items.append(it)

            if new_items:
                self._update_indexer_pill("ok")
            elif errors and not load_more:
                self._update_indexer_pill("error")
                self._set_status(f"Search error: {errors[0]}", log=True, level="ERROR")
                return

            if load_more:
                self.current_search_results = existing + new_items
                self._apply_filters_and_render(reset_page=False)
            else:
                self.current_search_results = new_items
                self.selected_search_uids.clear()
                self._update_batch_search_btn()
                self._apply_filters_and_render(reset_page=True)

            more = " More available." if self._search_has_more() else ""
            tv_note = " (TV search)" if got_tv else ""
            if load_more:
                total = len(self.current_search_results)
                msg = f"Loaded {len(new_items)} more release(s){tv_note}, {total} total.{more}"
            else:
                ok_count = len(jobs) - len(errors)
                msg = f"Found {len(new_items)} release(s){tv_note} across {ok_count}/{len(jobs)} indexer(s).{more}"
            self._set_status(msg, log=True, level="OK")

        def err(exc):
            if req_id != self._search_seq:
                return
            self._set_search_busy(False)
            self._update_indexer_pill("error")
            self._set_status(f"Search failed: {exc}", log=True, level="ERROR")

        self._run_bg(multi_search, on_ok=ok, on_err=err)

    def _search_has_more(self) -> bool:
        ctx = self._search_ctx
        return bool(ctx) and any(c.name not in ctx["exhausted"] for c in ctx["clients"])

    # --- recent searches -------------------------------------------------------
    RECENT_SEARCH_LABEL = "Recent"
    MAX_RECENT_SEARCHES = 15

    def _remember_search(self, query: str) -> None:
        hist = [q for q in self.config_data.get("search_history", []) if q.lower() != query.lower()]
        hist.insert(0, query)
        self.config_data["search_history"] = hist[: self.MAX_RECENT_SEARCHES]
        self._refresh_recent_menu()
        self._safe_write_config("search history")

    def _refresh_recent_menu(self) -> None:
        if not hasattr(self, "recent_menu"):
            return
        hist = list(self.config_data.get("search_history", []))
        values = hist + (["Clear recent searches"] if hist else ["No recent searches"])
        self.recent_menu.configure(values=values)
        self.recent_var.set(self.RECENT_SEARCH_LABEL)

    def _on_recent_chosen(self, choice: str) -> None:
        self.recent_var.set(self.RECENT_SEARCH_LABEL)
        if choice == "Clear recent searches":
            self.config_data["search_history"] = []
            self._refresh_recent_menu()
            self._safe_write_config("search history")
            return
        if choice == "No recent searches":
            return
        self.search_entry.delete(0, "end")
        self.search_entry.insert(0, choice)
        self._on_search_button()

    def _cycle_recent(self, step: int) -> str:
        """Up/Down in the search box walks through recent searches."""
        hist = list(self.config_data.get("search_history", []))
        if not hist:
            return "break"
        self._recent_pos = max(-1, min(len(hist) - 1, getattr(self, "_recent_pos", -1) + step))
        self.search_entry.delete(0, "end")
        if self._recent_pos >= 0:
            self.search_entry.insert(0, hist[self._recent_pos])
        return "break"

    def _apply_filters_and_render(self, reset_page: bool = False) -> None:
        items = list(self.current_search_results)

        # Library matching re-parses every title, so only redo it when the library index changed
        # (a new scan / cache load), not on every filter keystroke or page change.
        lib_index = self.library_index
        for it in items:
            if it.get("_lib_index") is not lib_index:
                it["_lib_match"] = match_release_to_library(
                    it["title"],
                    lib_index,
                    category=it.get("cat_hint") or it.get("category", ""),
                )
                it["_lib_index"] = lib_index
            norm_k = it.get("norm_key", "")
            it["_score"], it["_score_why"] = score_release(
                it,
                in_queue=bool(norm_k and norm_k in self._sab_queue_keys),
                downloaded=bool(norm_k and norm_k in self._sab_history_keys),
            )

        text_q = self.local_filter_entry.get().strip().lower()
        if text_q:
            tokens = text_q.split()
            items = [
                it for it in items
                if all(tok in it["title"].lower() or tok in it["indexer_name"].lower() or tok in it["category"].lower() for tok in tokens)
            ]

        qual_filter = self.quality_var.get()
        if qual_filter != "All Qualities":
            items = [it for it in items if it.get("quality") == qual_filter]

        size_filter = self.size_filter_var.get()
        mb = 1024 * 1024
        gb = 1024 * mb
        if size_filter == "> 100 MB":
            items = [it for it in items if it["size_bytes"] >= 100 * mb]
        elif size_filter == "> 1 GB":
            items = [it for it in items if it["size_bytes"] >= gb]
        elif size_filter == "1 GB – 15 GB":
            items = [it for it in items if gb <= it["size_bytes"] <= 15 * gb]
        elif size_filter == "> 15 GB":
            items = [it for it in items if it["size_bytes"] >= 15 * gb]
        elif size_filter == "< 1 GB":
            items = [it for it in items if 0 < it["size_bytes"] < gb]

        lang_filter = self.lang_var.get()
        if lang_filter != "All Languages":
            if lang_filter == "English":
                items = [it for it in items if it["language"] in ("English", "English / Untagged")]
            elif lang_filter == "Other":
                known = set(LANGUAGE_FILTER_OPTIONS) | {"English / Untagged"}
                items = [it for it in items if it["language"] not in known]
            else:
                items = [it for it in items if it["language"] == lang_filter]

        lib_filter = self.lib_filter_var.get()
        if lib_filter == "Missing Only":
            items = [
                it for it in items
                if it.get("_lib_match") is None or it["_lib_match"]["status"] == "corrupt"
            ]
        elif lib_filter == "Upgrades Only":
            items = [
                it for it in items
                if it.get("_lib_match") is not None and it["_lib_match"]["status"] == "upgrade"
            ]

        sort_mode = self.sort_var.get()
        if sort_mode == "Best Match":
            items.sort(key=lambda it: (-it.get("_score", 0), it["index"]))
        elif sort_mode == "Size: Largest First":
            items.sort(key=lambda it: it["size_bytes"], reverse=True)
        elif sort_mode == "Size: Smallest First":
            items.sort(key=lambda it: it["size_bytes"])
        elif sort_mode == "Newest First":
            items.sort(key=lambda it: it["pub_ts"], reverse=True)
        elif sort_mode == "Most Grabs":
            items.sort(key=lambda it: it["grabs_int"], reverse=True)
        else:
            items.sort(key=lambda it: it["index"])

        if self.dedupe_var.get():
            deduped: list[dict] = []
            by_key: dict[str, dict] = {}
            for it in items:
                k = it.get("norm_key") or it["title"].lower()
                if k not in by_key:
                    copy_it = dict(it)
                    copy_it["mirror_count"] = 0
                    by_key[k] = copy_it
                    deduped.append(copy_it)
                else:
                    by_key[k]["mirror_count"] += 1
            items = deduped
        else:
            for it in items:
                it["mirror_count"] = 0

        self.filtered_search_results = items
        self._best_uid = self._pick_best_uid(items)
        if reset_page:
            self.search_page = 0

        total_pages = max(1, (len(items) + SEARCH_PAGE_SIZE - 1) // SEARCH_PAGE_SIZE)
        if self.search_page >= total_pages:
            self.search_page = total_pages - 1

        total_raw = len(self.current_search_results)
        self.results_count_label.configure(
            text=f"Showing {len(items)} of {total_raw} release(s)" if total_raw else ""
        )
        self.page_label.configure(text=f"Page {self.search_page + 1} / {total_pages}")
        self.prev_page_btn.configure(state="normal" if self.search_page > 0 else "disabled")
        self.next_page_btn.configure(state="normal" if (self.search_page + 1) < total_pages else "disabled")

        start = self.search_page * SEARCH_PAGE_SIZE
        page_items = items[start : start + SEARCH_PAGE_SIZE]
        self._render_search_results(page_items)

    @staticmethod
    def _pick_best_uid(items: list[dict]) -> str | None:
        """Highest-scoring release you don't already have, haven't queued and that isn't passworded."""
        def eligible(it: dict) -> bool:
            if it.get("passworded"):
                return False
            why = " ".join(it.get("_score_why", []))
            return not any(k in why for k in ("already in queue", "already downloaded", "already in library"))
        cands = [it for it in items if eligible(it) and it.get("_score", 0) > 0]
        if not cands:
            return None
        return max(cands, key=lambda it: (it.get("_score", 0), -it["index"]))["uid"]

    def _grab_best(self) -> None:
        best = next((it for it in self.filtered_search_results if it["uid"] == getattr(self, "_best_uid", None)), None)
        if best is None:
            self._set_status("No suitable release to grab (everything shown is already queued, owned or passworded).", log=True, level="WARN")
            return
        self._set_status(f"Best match (score {best.get('_score', 0)}): {best['title']}")
        self._send_url_to_sab(best, self.search_cat_var.get())

    def _change_search_page(self, delta: int) -> None:
        total_pages = max(1, (len(self.filtered_search_results) + SEARCH_PAGE_SIZE - 1) // SEARCH_PAGE_SIZE)
        new_page = max(0, min(total_pages - 1, self.search_page + delta))
        if new_page != self.search_page:
            self.search_page = new_page
            self._apply_filters_and_render(reset_page=False)

    def _toggle_search_selection(self, uid: str, selected: bool) -> None:
        if selected:
            self.selected_search_uids.add(uid)
        else:
            self.selected_search_uids.discard(uid)
        self._update_batch_search_btn()

    def _select_current_search_page(self) -> None:
        start = self.search_page * SEARCH_PAGE_SIZE
        for it in self.filtered_search_results[start : start + SEARCH_PAGE_SIZE]:
            self.selected_search_uids.add(it["uid"])
        self._update_batch_search_btn()
        self._apply_filters_and_render(reset_page=False)

    def _clear_search_selection(self) -> None:
        self.selected_search_uids.clear()
        self._update_batch_search_btn()
        self._apply_filters_and_render(reset_page=False)

    def _update_batch_search_btn(self) -> None:
        self.batch_send_search_btn.configure(text=f"Send Selected ({len(self.selected_search_uids)})")

    def _send_selected_search_to_sab(self) -> None:
        if not self.selected_search_uids:
            self._set_status("Check one or more search results first.")
            return
        targets = [it for it in self.current_search_results if it["uid"] in self.selected_search_uids]
        if not targets:
            return
        dupes = [(it, reason) for it in targets if (reason := self._duplicate_reason(it))]
        if dupes:
            listing = "\n".join(f"• {it['title'][:70]}  ({reason})" for it, reason in dupes[:6])
            more = f"\n…and {len(dupes) - 6} more" if len(dupes) > 6 else ""
            choice = messagebox.askyesnocancel(
                "Possible duplicates",
                f"{len(dupes)} of {len(targets)} selected release(s) look like something you already have:\n\n"
                f"{listing}{more}\n\n"
                "Yes = send everything anyway\nNo = skip these and send the rest\nCancel = send nothing",
                parent=self,
            )
            if choice is None:
                return
            if choice is False:
                dupe_uids = {it["uid"] for it, _r in dupes}
                targets = [it for it in targets if it["uid"] not in dupe_uids]
                if not targets:
                    self._set_status("Nothing sent: every selected release was a duplicate.", log=True, level="INFO")
                    return
        selected_cat = self.search_cat_var.get()
        resolved_pairs = [
            (
                it,
                self._resolve_sab_category(
                    selected_cat,
                    item_category=it.get("cat_hint") or it.get("category", ""),
                    title=it.get("title", ""),
                ),
            )
            for it in targets
        ]
        self._set_status(f"Sending {len(targets)} selected release(s) to SABnzbd...", log=True)

        def send_batch():
            ok_count = 0
            errors: list[str] = []
            used_cats: set[str] = set()
            for it, cat in resolved_pairs:
                try:
                    self.sab.add_url(it["nzb_url"], nzb_name=it["title"], category=cat)
                    ok_count += 1
                    used_cats.add(cat)
                except Exception as exc:
                    errors.append(f"{it['title']}: {_format_http_error(exc)}")
            return ok_count, errors, sorted(used_cats)

        def ok(res):
            ok_count, errors, used_cats = res
            if ok_count:
                self._increment_daily_nzb_count(ok_count)
            for err_msg in errors:
                self._log_activity(err_msg, "ERROR")
            self.selected_search_uids.clear()
            self._update_batch_search_btn()
            self._apply_filters_and_render(reset_page=False)
            cat_info = ", ".join(used_cats) or "*"
            self._set_status(
                f"Queued {ok_count}/{len(targets)} release(s) in SABnzbd [cat={cat_info}].",
                log=True,
                level="OK" if ok_count else "ERROR",
            )
            self._refresh_sab_all()

        self._run_bg(send_batch, on_ok=ok)

    def _render_search_results(self, items: list[dict]) -> None:
        for w in self.search_results_frame.winfo_children():
            w.destroy()

        self._set_scroll_y(self.search_results_frame, 0.0)

        if not items:
            self._empty_state(
                self.search_results_frame,
                "No matching releases",
                "Try a different search, or loosen the filters above (Reset clears them).",
            )
            return

        max_bytes = max((int(it.get("size_bytes") or 0) for it in items), default=0)
        glyph_font = icon_font_family()

        for item in items:
            is_selected = item["uid"] in self.selected_search_uids
            card = ctk.CTkFrame(
                self.search_results_frame,
                fg_color=COLORS["bg_card"],
                corner_radius=10,
                border_width=1,
                border_color=COLORS["border_accent"] if is_selected else COLORS["border"],
            )
            card.pack(fill="x", padx=10, pady=(8, 0))

            sel_var = ctk.BooleanVar(value=is_selected)

            def on_toggle(u=item["uid"], v=sel_var, c=card):
                self._toggle_search_selection(u, v.get())
                c.configure(border_color=COLORS["border_accent"] if v.get() else COLORS["border"])

            ctk.CTkCheckBox(
                card,
                text="",
                width=22,
                checkbox_width=18,
                checkbox_height=18,
                variable=sel_var,
                command=on_toggle,
            ).pack(side="left", padx=(14, 2))

            # Placeholder until (or instead of) a poster: what kind of release it is + its resolution.
            kind_icon, kind_color_key, kind_label = release_kind(item)
            kind_color = COLORS[kind_color_key]
            art_frame = ctk.CTkFrame(card, fg_color=_tint(kind_color, 0.12, COLORS["bg_panel"]), corner_radius=8)
            art_frame.pack(side="left", padx=(6, 4), pady=10)

            poster_lbl = ctk.CTkLabel(
                art_frame,
                text=ICON_GLYPHS[kind_icon] if glyph_font else kind_label,
                width=58,
                height=84,
                fg_color="transparent",
                font=ctk.CTkFont(family=glyph_font, size=24) if glyph_font else ctk.CTkFont(family=UI_FONT, size=10, weight="bold"),
                text_color=kind_color,
            )
            poster_lbl.pack(padx=0, pady=0)
            res_txt = {"2160p / 4K": "4K", "1080p": "1080p", "720p": "720p"}.get(item.get("quality", ""), "")
            res_lbl = None
            if res_txt:
                res_lbl = ctk.CTkLabel(
                    art_frame, text=res_txt, height=14, fg_color="transparent", text_color=kind_color,
                    font=ctk.CTkFont(family=UI_FONT, size=9, weight="bold"),
                )
                res_lbl.place(relx=0.5, rely=1.0, y=-5, anchor="s")

            def apply_art(ctk_img, lbl=poster_lbl, extra=res_lbl):
                def show():
                    if lbl.winfo_exists():
                        lbl.configure(image=ctk_img, text="")
                        if extra is not None and extra.winfo_exists():
                            extra.destroy()
                self._post_ui(show)

            self.artwork.get_or_fetch(item, apply_art)

            # Actions: primary "Send to SAB", quieter secondary actions.
            btns = ctk.CTkFrame(card, fg_color="transparent")
            btns.pack(side="right", padx=14)

            ghost = dict(
                height=32,
                fg_color="transparent",
                hover_color=COLORS["bg_card_alt"],
                border_width=1,
                border_color=COLORS["border"],
                text_color=COLORS["text_secondary"],
            )
            ctk.CTkButton(
                btns, text="View", image=app_icon("view", COLORS["text_secondary"], 13), width=76,
                command=lambda it=item: self._view_indexer_nzb(it), **ghost
            ).pack(side="left", padx=(0, 6))
            ctk.CTkButton(
                btns, text="Save", image=app_icon("save", COLORS["text_secondary"], 13), width=76,
                command=lambda it=item: self._save_indexer_nzb(it), **ghost
            ).pack(side="left", padx=(0, 6))
            ctk.CTkButton(
                btns,
                text="Send to SAB",
                image=app_icon("send", ON_ACCENT, 13),
                width=124,
                height=32,
                fg_color=COLORS["accent"],
                hover_color=COLORS["accent_hover"],
                text_color="#041016",
                font=ctk.CTkFont(family=UI_FONT, size=12, weight="bold"),
                command=lambda it=item: self._send_url_to_sab(it, self.search_cat_var.get()),
            ).pack(side="left")

            info = ctk.CTkFrame(card, fg_color="transparent")
            info.pack(side="left", fill="both", expand=True, padx=(10, 6), pady=12)

            ctk.CTkLabel(
                info,
                text=item["title"],
                anchor="w",
                justify="left",
                wraplength=560,
                font=ctk.CTkFont(family=UI_FONT, size=13, weight="bold"),
                text_color=COLORS["text_primary"],
            ).pack(fill="x")

            # Line 2: plain facts (indexer · size · age · category · grabs · files)
            meta = ctk.CTkFrame(info, fg_color="transparent")
            meta.pack(fill="x", pady=(3, 0))
            ctk.CTkLabel(
                meta,
                text=item.get("indexer_name", "Indexer"),
                font=ctk.CTkFont(family=UI_FONT_SEMIBOLD, size=11),
                text_color=COLORS["accent"],
            ).pack(side="left")

            # Size, coloured by how plausible it is for the resolution, with a bar relative to the
            # biggest result on screen.
            verdict = size_verdict(item)
            size_color_key, size_hint = SIZE_VERDICT_STYLE[verdict]
            size_color = COLORS[size_color_key]
            ctk.CTkLabel(meta, text="   ·   ", font=ctk.CTkFont(family=UI_FONT, size=11), text_color=COLORS["text_secondary"]).pack(side="left")
            size_lbl = ctk.CTkLabel(
                meta,
                text=item["size_human"],
                font=ctk.CTkFont(family=UI_FONT_SEMIBOLD if verdict in ("tiny", "small", "large") else UI_FONT, size=11),
                text_color=size_color,
            )
            size_lbl.pack(side="left")
            if size_hint:
                Tooltip(size_lbl, size_hint)
            if max_bytes > 0 and item.get("size_bytes"):
                frac = math.sqrt(int(item["size_bytes"]) / max_bytes)  # sqrt: 68 GB vs 9 GB reads as ~3x, not 8x
                size_bar = ctk.CTkProgressBar(
                    meta, width=46, height=5, fg_color=COLORS["bg_panel"],
                    progress_color=size_color if verdict in ("tiny", "small", "large") else COLORS["accent"],
                )
                size_bar.set(max(0.04, min(1.0, frac)))
                size_bar.pack(side="left", padx=(7, 0))
            facts = [item["age"], item["category"]]
            if item["grabs"]:
                facts.append(f"{item['grabs']} grabs")
            if item.get("files_count"):
                facts.append(f"{item['files_count']} files")
            ctk.CTkLabel(
                meta,
                text="   ·   " + "   ·   ".join(f for f in facts if f),
                font=ctk.CTkFont(family=UI_FONT, size=11),
                text_color=COLORS["text_secondary"],
            ).pack(side="left")

            # Line 3: tags that matter for choosing a release (score first; click it for the why)
            badge_items: list[tuple[str, str, str]] = []
            if item["uid"] == getattr(self, "_best_uid", None):
                badge_items.append(("★ BEST PICK", COLORS["accent"], "Best match: " + ", ".join(item.get("_score_why", []))))
            score = int(item.get("_score", 0))
            score_col = COLORS["green"] if score >= 60 else (COLORS["accent"] if score >= 40 else COLORS["text_muted"])
            badge_items.append((f"Score {score}", score_col, f"Score {score}: " + ", ".join(item.get("_score_why", []))))
            for tag_txt, tag_col in item.get("tags", []):
                badge_items.append((tag_txt, tag_col, ""))
            if item["language"] not in ("English", "English / Untagged"):
                badge_items.append((item["language"], COLORS["sky"], ""))
            if item.get("passworded"):
                badge_items.append(("PASSWORDED", COLORS["red"], ""))

            norm_k = item.get("norm_key", "")
            if norm_k and norm_k in self._sab_queue_keys:
                badge_items.append(("IN QUEUE", COLORS["amber"], ""))
            elif norm_k and norm_k in self._sab_history_keys:
                badge_items.append(("DOWNLOADED", COLORS["green"], ""))

            lib_match = item.get("_lib_match")
            if lib_match:
                badge_items.append((
                    lib_match["badge"],
                    COLORS.get(lib_match["color_key"], COLORS["accent"]),
                    lib_match.get("summary", ""),
                ))

            if item.get("mirror_count", 0) > 0:
                badge_items.append((f"+{item['mirror_count']} mirror", COLORS["purple"], ""))

            if badge_items:
                badges = ctk.CTkFrame(info, fg_color="transparent")
                badges.pack(fill="x", pady=(7, 0))
                for txt, col, tip_summary in badge_items:
                    pill = self._pill(badges, txt, col, strong=bool(tip_summary) or col == COLORS["red"])
                    pill.pack(side="left", padx=(0, 5))
                    if tip_summary:
                        pill.configure(cursor="hand2")
                        pill.bind("<Button-1>", lambda _e, s=tip_summary: self._set_status(s))

            self._bind_context_menu(card, lambda e, it=item: self._show_result_menu(e, it))

        # "Load more" under the last page when an indexer has results past the first page(s).
        total_pages = max(1, (len(self.filtered_search_results) + SEARCH_PAGE_SIZE - 1) // SEARCH_PAGE_SIZE)
        if self.search_page + 1 >= total_pages and self._search_has_more():
            ctk.CTkButton(
                self.search_results_frame,
                text=f"Load more results  (next {self.SEARCH_PAGE_LIMIT} per indexer)",
                height=34,
                fg_color="transparent",
                hover_color=COLORS["bg_card_alt"],
                border_width=1,
                border_color=COLORS["border_accent"],
                text_color=COLORS["accent"],
                font=ctk.CTkFont(family=UI_FONT_SEMIBOLD, size=12),
                command=lambda: self._run_indexer_search(load_more=True),
            ).pack(pady=(12, 4))

        # breathing room under the last card
        ctk.CTkFrame(self.search_results_frame, height=8, fg_color="transparent").pack()

    @staticmethod
    def _bind_context_menu(root_widget, handler) -> None:
        """Right-click anywhere on a card (including its labels and pills) opens its menu."""
        stack = [root_widget]
        while stack:
            w = stack.pop()
            try:
                tk.Misc.bind(w, "<Button-3>", handler, "+")
            except Exception:
                pass
            stack.extend(w.winfo_children())

    def _show_result_menu(self, event, item: dict) -> None:
        menu = tk.Menu(
            self, tearoff=0, bg=COLORS["bg_card_alt"], fg=COLORS["text_primary"],
            activebackground=COLORS["accent"], activeforeground=ON_ACCENT,
            disabledforeground=COLORS["text_muted"], bd=0, relief="flat", font=(UI_FONT, 10),
        )
        menu.add_command(label="Send to SABnzbd", command=lambda: self._send_url_to_sab(item, self.search_cat_var.get()))
        menu.add_command(label="View NZB contents", command=lambda: self._view_indexer_nzb(item))
        menu.add_command(label="Save .nzb file", command=lambda: self._save_indexer_nzb(item))
        menu.add_separator()
        menu.add_command(label="Copy title", command=lambda: self._copy_to_clipboard(item["title"], "Copied release title."))
        menu.add_command(
            label="Copy NZB link",
            command=lambda: self._copy_to_clipboard(
                item.get("nzb_url", ""), "Copied NZB link. It contains your indexer API key, so don't share it."
            ),
        )
        menu.add_command(label="Search for similar releases", command=lambda: self._search_similar(item))
        details = item.get("details_url", "")
        menu.add_command(
            label="Open on indexer website",
            state="normal" if details else "disabled",
            command=lambda: details and webbrowser.open(details),
        )
        menu.add_separator()
        selected = item["uid"] in self.selected_search_uids
        menu.add_command(label="Deselect" if selected else "Select", command=lambda: self._set_result_selected(item, not selected))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_to_clipboard(self, text: str, message: str) -> None:
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status(message)

    def _set_result_selected(self, item: dict, selected: bool) -> None:
        self._toggle_search_selection(item["uid"], selected)
        self._apply_filters_and_render(reset_page=False)

    def _search_similar(self, item: dict) -> None:
        if self._search_in_flight:
            self._set_status("A search is still running. Wait for it (or cancel it) first.")
            return
        name, year, _hint = parse_title_for_artwork(item.get("title", ""), item.get("category", ""))
        query = f"{name} {year}".strip() or item.get("title", "")
        self.search_entry.delete(0, "end")
        self.search_entry.insert(0, query)
        self._on_search_button()

    def _view_indexer_nzb(self, item: dict) -> None:
        self._set_status(f"Fetching NZB preview for '{item['title']}'...")

        def fetch_and_parse():
            raw = NewznabClient.fetch_nzb_bytes(item["nzb_url"])
            return parse_nzb_bytes(raw, f"{item['title']}.nzb", item["nzb_url"])

        def ok(meta: dict):
            self._set_status(f"Viewing NZB: {item['title']}")
            NzbViewerDialog(self, item["title"], meta)

        self._run_bg(fetch_and_parse, on_ok=ok)

    def _duplicate_reason(self, item: dict) -> str | None:
        """Why this release looks like something you already have, or None."""
        norm_k = item.get("norm_key", "")
        if norm_k and norm_k in self._sab_queue_keys:
            return "already in the SABnzbd queue"
        if norm_k and norm_k in self._sab_history_keys:
            return "already downloaded (SABnzbd history)"
        lib = item.get("_lib_match") or {}
        if lib.get("status") in ("owned", "have_better"):
            return lib.get("summary") or "already in your library"
        return None

    def _send_url_to_sab(self, item: dict, selected_cat: str) -> None:
        reason = self._duplicate_reason(item)
        if reason and not messagebox.askyesno(
            "Possible duplicate",
            f"{item['title']}\n\nThis looks like something you already have:\n{reason}\n\nSend it to SABnzbd anyway?",
            parent=self,
        ):
            self._set_status(f"Not sent (duplicate): {item['title']}")
            return
        cat = self._resolve_sab_category(
            selected_cat,
            item_category=item.get("cat_hint") or item.get("category", ""),
            title=item.get("title", ""),
        )
        self._set_status(f"Sending '{item['title']}' to SABnzbd [cat={cat}]...")

        def on_sent(_r):
            self._increment_daily_nzb_count()
            extra = ""
            if selected_cat == AUTO_CAT_LABEL and cat == "*" and len(self.sab_categories) <= 1:
                extra = " (No SABnzbd categories configured — add e.g. 'movies' in SABnzbd Config → Categories)"
            self._set_status(f"Queued in SABnzbd [cat={cat}]: {item['title']}{extra}", log=True, level="OK")
            self._refresh_sab_all()

        self._run_bg(
            lambda: self.sab.add_url(item["nzb_url"], nzb_name=item["title"], category=cat),
            on_ok=on_sent,
        )

    def _save_indexer_nzb(self, item: dict) -> None:
        folder = Path(self.folder_entry.get().strip() or self.config_data["nzb_folder"])
        folder.mkdir(parents=True, exist_ok=True)
        dest = unique_nzb_path(folder, item["title"])
        self._set_status(f"Downloading {dest.name}...")
        self._run_bg(
            lambda: NewznabClient.download_nzb(item["nzb_url"], dest),
            on_ok=lambda p: (
                self._increment_daily_nzb_count(),
                self._set_status(f"Saved {p.name}", log=True, level="OK"),
                self._scan_local_folder(),
            ),
        )

    def _browse_folder(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.folder_entry.get() or str(Path.home()))
        if chosen:
            self.folder_entry.delete(0, "end")
            self.folder_entry.insert(0, chosen)
            self.config_data["nzb_folder"] = chosen
            self._write_config_file(self.config_data)
            self._update_local_drive_space()
            self._scan_local_folder()
            if self.watch_var.get():
                self._start_watch()  # new folder: only files arriving from now on are sent

    def _open_local_folder(self) -> None:
        folder = Path(self.folder_entry.get().strip() or self.config_data["nzb_folder"])
        if not folder.exists():
            self._set_status("Local folder does not exist.", log=True, level="WARN")
            return
        try:
            _open_folder_in_os(folder)
        except Exception as exc:
            self._set_status(f"Could not open folder: {exc}", log=True, level="ERROR")

    def _pick_single_nzb(self) -> None:
        chosen = filedialog.askopenfilename(filetypes=[("NZB Files", "*.nzb"), ("All Files", "*.*")])
        if chosen:
            self._inspect_nzb(Path(chosen))

    def _scan_local_folder(self) -> None:
        folder = Path(self.folder_entry.get().strip() or self.config_data["nzb_folder"])
        for w in self.local_list_frame.winfo_children():
            w.destroy()
        self.local_row_vars.clear()
        self.local_row_frames.clear()

        if not folder.exists():
            ctk.CTkLabel(self.local_list_frame, text="Folder does not exist.", text_color=COLORS["amber"]).pack(pady=20)
            self._update_local_batch_btn()
            return

        # stat() each file once; a file moved/archived between glob() and stat() is skipped
        # instead of crashing the whole folder scan.
        entries: list[tuple[Path, os.stat_result]] = []
        for p in folder.glob("*.nzb"):
            try:
                entries.append((p, p.stat()))
            except OSError:
                continue
        entries.sort(key=lambda e: e[1].st_mtime, reverse=True)
        self.local_nzbs = [p for p, _st in entries]
        if not self.local_nzbs:
            self._empty_state(
                self.local_list_frame,
                "No .nzb files here",
                "Save NZBs from Indexer Search, or turn on Auto-send to send new ones automatically.",
                compact=True,
            )
            self._update_local_batch_btn()
            return

        for p, st in entries:
            size_str = format_bytes(st.st_size)
            is_selected = self.selected_nzb_path == p
            row = ctk.CTkFrame(
                self.local_list_frame,
                fg_color=COLORS["bg_card_alt"] if is_selected else COLORS["bg_card"],
                border_width=1 if is_selected else 0,
                border_color=COLORS["border_accent"],
                corner_radius=6,
            )
            row.pack(fill="x", padx=4, pady=3)
            self.local_row_frames[p] = row

            var = ctk.BooleanVar(value=False)
            self.local_row_vars[p] = var
            ctk.CTkCheckBox(
                row,
                text="",
                width=22,
                checkbox_width=18,
                checkbox_height=18,
                variable=var,
                fg_color=COLORS["accent"],
                command=self._update_local_batch_btn,
            ).pack(side="left", padx=(8, 4), pady=5)

            ctk.CTkButton(
                row,
                text=f"{p.name}   ({size_str})",
                anchor="w",
                height=30,
                fg_color="transparent",
                hover_color=COLORS["bg_card_alt"],
                text_color=COLORS["text_primary"],
                command=lambda path=p: self._inspect_nzb(path),
            ).pack(side="left", fill="x", expand=True, padx=(0, 6), pady=3)

        self._update_local_batch_btn()

    def _highlight_local_row(self, active_path: Path | None) -> None:
        for p, frame in self.local_row_frames.items():
            if not frame.winfo_exists():
                continue
            if p == active_path:
                frame.configure(fg_color=COLORS["bg_card_alt"], border_width=1, border_color=COLORS["border_accent"])
            else:
                frame.configure(fg_color=COLORS["bg_card"], border_width=0)

    def _clear_inspector(self) -> None:
        self.selected_nzb_path = None
        self.selected_nzb_meta = None
        self.inspector_title.configure(text="Select an .nzb file to inspect")
        self.inspector_box.configure(state="normal")
        self.inspector_box.delete("1.0", "end")
        self.inspector_box.configure(state="disabled")
        self._highlight_local_row(None)
        self._set_local_actions(False)

    def _update_local_batch_btn(self) -> None:
        count = sum(1 for v in self.local_row_vars.values() if v.get())
        self.local_batch_send_btn.configure(text=f"Send Selected ({count})")

    def _select_all_local(self) -> None:
        for v in self.local_row_vars.values():
            v.set(True)
        self._update_local_batch_btn()

    def _clear_local_selection(self) -> None:
        for v in self.local_row_vars.values():
            v.set(False)
        self._update_local_batch_btn()

    def _archive_sent_nzb(self, file_path: Path) -> Path:
        sent_dir = file_path.parent / "sent"
        sent_dir.mkdir(parents=True, exist_ok=True)
        dest = unique_nzb_path(sent_dir, file_path.stem)
        shutil.move(str(file_path), str(dest))
        return dest

    def _inspect_nzb(self, file_path: Path) -> None:
        self.selected_nzb_path = file_path
        self._highlight_local_row(file_path)
        self.inspector_title.configure(text=file_path.name)
        try:
            meta = parse_local_nzb(file_path)
            self.selected_nzb_meta = meta
            lines = [
                f"Path:         {meta['source']}",
                f"NZB Size:     {meta['disk_size']}",
                f"Payload Size: {meta['payload_human']} ({meta['file_count']} files)",
                f"Groups:       {', '.join(meta['groups']) or 'None listed'}",
                "-" * 60,
                "Files inside NZB:",
            ]
            for idx, f in enumerate(meta["files"][:100], 1):
                lines.append(f"{idx:>3}. [{f['size_human']}] {f['subject']}")
            if meta["file_count"] > 100:
                lines.append(f"... and {meta['file_count'] - 100} more files.")
            text = "\n".join(lines)
        except Exception as exc:
            self.selected_nzb_meta = None
            text = f"Could not parse NZB XML:\n{exc}"

        self.inspector_box.configure(state="normal")
        self.inspector_box.delete("1.0", "end")
        self.inspector_box.insert("1.0", text)
        self.inspector_box.configure(state="disabled")
        self._set_local_actions(True, parsed=self.selected_nzb_meta is not None)

    def _open_selected_nzb_popup(self) -> None:
        if not self.selected_nzb_meta:
            self._set_status("Select an .nzb file first.")
            return
        NzbViewerDialog(self, self.selected_nzb_meta["name"], self.selected_nzb_meta)

    def _upload_selected_nzb(self) -> None:
        if not self.selected_nzb_path or not self.selected_nzb_path.exists():
            self._set_status("Select an .nzb file first.")
            return
        path = self.selected_nzb_path
        selected_cat = self.local_cat_var.get()
        groups_hint = " ".join(self.selected_nzb_meta.get("groups", [])) if self.selected_nzb_meta else ""
        cat = self._resolve_sab_category(selected_cat, item_category=groups_hint, title=path.stem)
        archive = self.auto_archive_var.get()
        self._set_status(f"Uploading {path.name} to SABnzbd [cat={cat}]...")

        def task():
            self.sab.add_file(path, category=cat)
            # The upload already succeeded here. An archive failure must not make it look like
            # the upload failed, or the user re-sends it and SABnzbd gets a duplicate.
            if archive:
                try:
                    self._archive_sent_nzb(path)
                except Exception as exc:
                    return _format_http_error(exc)
            return None

        def ok(archive_err):
            self._increment_daily_nzb_count()
            if archive and archive_err:
                self._set_status(
                    f"Uploaded {path.name} to SABnzbd [cat={cat}], but archiving to sent/ failed: {archive_err}",
                    log=True,
                    level="WARN",
                )
            else:
                note = " and archived to sent/" if archive else ""
                self._set_status(f"Uploaded {path.name} to SABnzbd [cat={cat}]{note}.", log=True, level="OK")
            if archive and not archive_err:
                self._clear_inspector()
                self._scan_local_folder()
            self._refresh_sab_all()

        self._run_bg(task, on_ok=ok)

    def _upload_selected_batch_nzbs(self) -> None:
        paths = [p for p, v in self.local_row_vars.items() if v.get() and p.exists()]
        if not paths:
            self._set_status("Check one or more local .nzb files first.")
            return
        selected_cat = self.local_cat_var.get()
        resolved_pairs = [
            (p, self._resolve_sab_category(selected_cat, item_category="", title=p.stem))
            for p in paths
        ]
        archive = self.auto_archive_var.get()
        self._set_status(f"Uploading {len(paths)} local .nzb file(s)...", log=True)

        def task():
            ok_count = 0
            errors: list[str] = []
            used_cats: set[str] = set()
            archive_warnings: list[str] = []
            for p, cat in resolved_pairs:
                try:
                    self.sab.add_file(p, category=cat)
                except Exception as exc:
                    errors.append(f"{p.name}: {_format_http_error(exc)}")
                    continue
                ok_count += 1
                used_cats.add(cat)
                if archive:  # uploaded already; an archive problem is only a warning
                    try:
                        self._archive_sent_nzb(p)
                    except Exception as exc:
                        archive_warnings.append(f"{p.name}: queued, but archiving failed: {_format_http_error(exc)}")
            return ok_count, errors, sorted(used_cats), archive_warnings

        def ok(res):
            ok_count, errors, used_cats, archive_warnings = res
            if ok_count:
                self._increment_daily_nzb_count(ok_count)
            for err_msg in errors:
                self._log_activity(err_msg, "ERROR")
            for warn_msg in archive_warnings:
                self._log_activity(warn_msg, "WARN")
            if archive and self.selected_nzb_path and not self.selected_nzb_path.exists():
                self._clear_inspector()
            self._scan_local_folder()
            self._refresh_sab_all()
            cat_info = ", ".join(used_cats) or "*"
            self._set_status(
                f"Uploaded {ok_count}/{len(paths)} local .nzb file(s) to SABnzbd [cat={cat_info}].",
                log=True,
                level="OK" if ok_count else "ERROR",
            )

        self._run_bg(task, on_ok=ok)

    def _delete_selected_nzb(self) -> None:
        if not self.selected_nzb_path or not self.selected_nzb_path.exists():
            return
        if not messagebox.askyesno("Delete NZB", f"Delete '{self.selected_nzb_path.name}' from disk?"):
            return
        try:
            name = self.selected_nzb_path.name
            os.remove(self.selected_nzb_path)
            self._set_status(f"Deleted {name}", log=True, level="WARN")
            self._clear_inspector()
            self._scan_local_folder()
        except OSError as exc:
            self._set_status(f"Delete failed: {exc}", log=True, level="ERROR")

    def _auto_refresh_loop(self) -> None:
        if self._shutting_down:
            return
        if self.auto_refresh_var.get() and self.config_data.get("sab_api_key") and not self._sab_refresh_in_flight:
            self._refresh_sab_all(silent=True)
        self._auto_refresh_after_id = self.after(4000, self._auto_refresh_loop)

    def _refresh_sab_all(self, silent: bool = False) -> None:
        if not self.config_data.get("sab_api_key"):
            self.sab_status_pill.configure(text="●  SABnzbd: Add API Key", text_color=COLORS["amber"])
            self._show_sab_unavailable(
                "SABnzbd isn't connected yet",
                "Add your SABnzbd URL and API key in Settings (or click the SABnzbd pill at the top).",
                badge="SETUP", color=COLORS["amber"],
            )
            return
        if self._sab_refresh_in_flight:
            return

        self._sab_refresh_in_flight = True
        need_cats = (not self._sab_cats_loaded) or (not silent) or (len(self.sab_categories) <= 1)

        def fetch():
            q = self.sab.get_queue()
            h = self.sab.get_history(limit=25)
            cats = self.sab.get_categories() if need_cats else None
            return q, h, cats

        def ok(res):
            self._sab_refresh_in_flight = False
            q, h, cats = res
            if cats is not None:
                self.sab_categories = cats or ["*"]
                self._sab_cats_loaded = True
                menu_vals = self._sab_cat_menu_values()
                self.search_cat_menu.configure(values=menu_vals)
                self.local_cat_menu.configure(values=menu_vals)
                if self.search_cat_var.get() not in menu_vals:
                    self.search_cat_var.set(AUTO_CAT_LABEL)
                if self.local_cat_var.get() not in menu_vals:
                    self.local_cat_var.set(AUTO_CAT_LABEL)
            self._render_sab_queue(q)
            self._render_sab_history(h)

        def err(exc):
            self._sab_refresh_in_flight = False
            self.sab_status_pill.configure(text="●  SABnzbd: Offline", text_color=COLORS["red"])
            self._show_sab_unavailable(
                "SABnzbd is offline",
                "Check that SABnzbd is running and the URL in Settings is right. Retrying automatically.",
                badge="OFFLINE", color=COLORS["red"],
            )
            self._update_local_drive_space()
            if not silent:
                self._set_status(f"SABnzbd error: {exc}", log=True, level="ERROR")

        self._run_bg(fetch, on_ok=ok, on_err=err)

    def _show_sab_unavailable(self, title: str, hint: str, badge: str, color: str) -> None:
        """Dashboard + queue/history placeholders while SABnzbd isn't configured or can't be reached."""
        self.card_speed_val.configure(text="—", text_color=COLORS["text_muted"])
        self.card_speed_sub.configure(text="Offline, retrying…" if badge == "OFFLINE" else "Not connected")
        self.now_dl_badge.configure(text=badge, text_color=color, fg_color=_tint(color))
        self.now_dl_name.configure(text=title)
        self.now_dl_stats.configure(text="—")
        self.now_dl_bar.set(0)
        self.sab_summary_label.configure(text=f"ACTIVE QUEUE  •  {title}", text_color=COLORS["text_muted"])
        key = ("unavailable", title)
        if self._last_queue_sig == key:
            return
        self._last_queue_sig = self._last_history_sig = key
        for frame in (self.queue_frame, self.history_frame):
            for w in frame.winfo_children():
                w.destroy()
        self._empty_state(self.queue_frame, title, hint, compact=True)
        self._empty_state(self.history_frame, "No history to show", "Finished downloads appear here once SABnzbd is connected.", compact=True)

    def _move_queue_item(self, nzo_id: str, position: int) -> None:
        self._run_bg(lambda: self.sab.move_queue_item(nzo_id, position), on_ok=lambda _r: self._refresh_sab_all())

    def _render_sab_queue(self, queue_data: dict) -> None:
        status = queue_data.get("status", "Idle")
        raw_speed = queue_data.get("speed", "0")
        speed = format_sab_speed(raw_speed)
        self.speed_chart.push_sample(parse_speed_to_mbps(raw_speed))

        slimit = str(queue_data.get("speedlimit") or "").strip()
        slimit_abs = str(queue_data.get("speedlimit_abs") or "").strip()
        if slimit and slimit not in ("0", "100"):
            lim_txt = f"Limit: {slimit}% ({slimit_abs} KB/s)" if slimit_abs and slimit_abs != "0" else f"Limit: {slimit}%"
            self.speed_limit_badge.configure(text=lim_txt, text_color=COLORS["amber"])
        else:
            self.speed_limit_badge.configure(text="Limit: Unlimited", text_color=COLORS["text_secondary"])

        size_left = queue_data.get("sizeleft", "0 B")
        time_left = queue_data.get("timeleft", "0:00:00")
        slots = queue_data.get("slots", [])
        self.sab_paused = status.lower() == "paused"
        self._sab_queue_keys = {normalize_release_key(s.get("filename", "")) for s in slots if s.get("filename")}

        self.pause_all_btn.configure(
            text="Resume Queue" if self.sab_paused else "Pause Queue",
            image=app_icon("play" if self.sab_paused else "pause", COLORS["text_primary"]),
        )
        eta = time_left if slots and time_left not in ("", "0:00:00") else "—"
        downloading_slot = next((s for s in slots if s.get("status", "").lower() == "downloading"), None)
        color = (
            COLORS["amber"] if self.sab_paused
            else (COLORS["green"] if downloading_slot else (COLORS["sky"] if slots else COLORS["accent"]))
        )

        self.sab_status_pill.configure(text=f"●  SABnzbd: {status} ({speed})", text_color=color)
        self.card_speed_val.configure(text=speed, text_color=color)
        self.card_speed_sub.configure(
            text=f"{len(slots)} queued  •  {size_left} left  •  ETA {eta}" if slots else "Queue empty"
        )

        if downloading_slot:
            pct = safe_float(downloading_slot.get("percentage"))
            self.now_dl_badge.configure(text="DOWNLOADING", text_color=COLORS["green"], fg_color=_tint(COLORS["green"]))
            self.now_dl_name.configure(text=downloading_slot.get("filename", "Unknown"))
            self.now_dl_stats.configure(
                text=f"{pct:.0f}%  •  {downloading_slot.get('sizeleft', '?')} left  •  ETA {downloading_slot.get('timeleft', '?')}  •  {speed}"
            )
            self.now_dl_bar.set(min(max(pct / 100.0, 0.0), 1.0))
            self.now_dl_bar.configure(progress_color=COLORS["green"])
        elif slots and self.sab_paused:
            first = slots[0]
            pct = safe_float(first.get("percentage"))
            self.now_dl_badge.configure(text="PAUSED", text_color=COLORS["amber"], fg_color=_tint(COLORS["amber"]))
            self.now_dl_name.configure(text=first.get("filename", "Unknown"))
            self.now_dl_stats.configure(text=f"{pct:.0f}%  •  {first.get('sizeleft', '?')} left  •  Paused")
            self.now_dl_bar.set(min(max(pct / 100.0, 0.0), 1.0))
            self.now_dl_bar.configure(progress_color=COLORS["amber"])
        elif slots:
            first = slots[0]
            pct = safe_float(first.get("percentage"))
            self.now_dl_badge.configure(text="QUEUED", text_color=COLORS["sky"], fg_color=_tint(COLORS["sky"]))
            self.now_dl_name.configure(text=f"Next in queue: {first.get('filename', 'Unknown')}")
            self.now_dl_stats.configure(text=f"{len(slots)} queued  •  {size_left} left  •  {speed}")
            self.now_dl_bar.set(min(max(pct / 100.0, 0.0), 1.0))
            self.now_dl_bar.configure(progress_color=COLORS["sky"])
        else:
            self.now_dl_badge.configure(text="IDLE", text_color=COLORS["text_muted"], fg_color=_tint(COLORS["text_muted"]))
            self.now_dl_name.configure(text="Nothing downloading")
            self.now_dl_stats.configure(text="Idle")
            self.now_dl_bar.set(0)

        d1 = queue_data.get("diskspace1")
        t1 = queue_data.get("diskspacetotal1")
        d2 = queue_data.get("diskspace2")
        t2 = queue_data.get("diskspacetotal2")
        free_raw = d2 if d2 is not None else d1
        total_raw = t2 if t2 is not None else t1
        if free_raw is not None:
            try:
                free_gb = float(free_raw)
                total_gb = float(total_raw) if total_raw else 0.0
                used_gb = max(0.0, total_gb - free_gb)
                used_pct = max(0.0, min(1.0, used_gb / total_gb)) if total_gb > 0 else 0.0
                d_color = COLORS["red"] if (free_gb < 20 or used_pct > 0.9) else (COLORS["amber"] if used_pct > 0.78 else COLORS["green"])
                self.card_drive_val.configure(text=f"{free_gb:,.1f} GB free", text_color=d_color)

                if d1 is not None and d2 is not None and abs(float(d1) - float(d2)) > 0.5:
                    sub_txt = f"Complete: {float(d2):,.1f}G • Temp: {float(d1):,.1f}G"
                elif total_gb > 0:
                    sub_txt = f"{used_gb:,.1f} / {total_gb:,.1f} GB used ({used_pct * 100:.0f}%)"
                else:
                    sub_txt = "SABnzbd Download Volume"
                self.card_drive_sub.configure(text=sub_txt)
                self.drive_bar.set(used_pct)
                self.drive_bar.configure(progress_color=d_color)
            except ValueError:
                self._update_local_drive_space()
        else:
            self._update_local_drive_space()

        self.sab_summary_label.configure(
            text=(
                f"ACTIVE QUEUE ({len(slots)})  •  {size_left} remaining  •  ETA {eta}  •  {speed}"
                if slots else "ACTIVE QUEUE  •  Empty"
            ),
            text_color=color,
        )

        sig = (
            tuple(self.sab_categories),
            tuple(
                (
                    s.get("nzo_id", ""),
                    s.get("filename", ""),
                    s.get("percentage", "0"),
                    s.get("sizeleft", ""),
                    s.get("size", ""),
                    s.get("timeleft", ""),
                    s.get("status", ""),
                    s.get("cat", "*"),
                    str(s.get("priority", "Normal")),
                )
                for s in slots
            ),
        )
        if sig == self._last_queue_sig:
            return
        self._last_queue_sig = sig

        scroll_y = self._get_scroll_y(self.queue_frame)

        for w in self.queue_frame.winfo_children():
            w.destroy()

        if not slots:
            self._empty_state(
                self.queue_frame,
                "Queue is empty",
                "Send releases from Indexer Search or Local NZB Files and they'll show up here.",
                compact=True,
            )
            return

        cat_choices = list(self.sab_categories) if self.sab_categories else ["*"]

        for slot_idx, slot in enumerate(slots):
            nzo_id = slot.get("nzo_id", "")
            name = slot.get("filename", "Unknown")
            pct = safe_float(slot.get("percentage"))
            left = slot.get("sizeleft", "?")
            total_sz = slot.get("size", "")
            eta = slot.get("timeleft", "?")
            item_status = slot.get("status", "Queued")
            cat = slot.get("cat", "*") or "*"
            raw_prio = str(slot.get("priority", "Normal"))
            prio_label = SAB_VAL_TO_PRIORITY.get(raw_prio, "Normal")

            status_color, status_label = get_status_style(item_status)
            is_item_paused = status_label == "PAUSED"

            card = ctk.CTkFrame(
                self.queue_frame,
                fg_color=COLORS["bg_card"],
                corner_radius=10,
                border_width=1,
                border_color=COLORS["border"],
            )
            card.pack(fill="x", padx=8, pady=5)

            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=12, pady=(10, 6))

            ctk.CTkLabel(
                top,
                text=status_label,
                fg_color=_tint(status_color),
                corner_radius=11,
                padx=8,
                pady=2,
                font=ctk.CTkFont(family=UI_FONT, size=10, weight="bold"),
                text_color=status_color,
            ).pack(side="left", padx=(0, 8))

            row_cat_choices = cat_choices if cat in cat_choices else [cat] + cat_choices
            cat_var = ctk.StringVar(value=cat)
            ctk.CTkOptionMenu(
                top,
                variable=cat_var,
                values=row_cat_choices,
                width=105,
                height=24,
                fg_color=COLORS["bg_panel"],
                button_color=COLORS["border"],
                text_color=COLORS["accent"],
                font=ctk.CTkFont(family=UI_FONT, size=11),
                command=lambda choice, nid=nzo_id, nm=name: self._run_bg(
                    lambda: self.sab.change_category(nid, choice),
                    on_ok=lambda _r: (
                        self._set_status(f"Changed category to '{choice}': {nm}", log=True, level="OK"),
                        self._refresh_sab_all(),
                    ),
                ),
            ).pack(side="left", padx=(0, 8))

            size_info = f"{left} / {total_sz} left" if total_sz else f"{left} left"
            ctk.CTkLabel(
                top,
                text=f"{size_info}  •  ETA {eta}",
                font=ctk.CTkFont(family=UI_FONT, size=11),
                text_color=COLORS["text_secondary"],
            ).pack(side="right", padx=(8, 0))

            ctk.CTkLabel(
                top,
                text=name,
                anchor="w",
                font=ctk.CTkFont(family=UI_FONT, size=12, weight="bold"),
                text_color=COLORS["text_primary"],
            ).pack(side="left", fill="x", expand=True)

            bottom = ctk.CTkFrame(card, fg_color="transparent")
            bottom.pack(fill="x", padx=12, pady=(0, 10))

            ctk.CTkLabel(
                bottom,
                text=f"{pct:>3.0f}%",
                width=40,
                anchor="w",
                font=ctk.CTkFont(family=UI_FONT, size=11, weight="bold"),
                text_color=status_color,
            ).pack(side="left", padx=(0, 6))

            ctk.CTkButton(
                bottom,
                text="Delete",
                image=app_icon("delete", "#FFFFFF", 12),
                width=78,
                height=26,
                fg_color=COLORS["red"],
                hover_color="#DC2626",
                command=lambda nid=nzo_id: self._run_bg(
                    lambda: self.sab.delete_queue_item(nid),
                    on_ok=lambda _r: self._refresh_sab_all(),
                ),
            ).pack(side="right", padx=(3, 0))

            ctk.CTkButton(
                bottom,
                text="Resume" if is_item_paused else "Pause",
                image=app_icon("play" if is_item_paused else "pause", COLORS["text_primary"], 12),
                width=86,
                height=26,
                fg_color=COLORS["bg_card_alt"],
                hover_color=COLORS["border"],
                command=lambda nid=nzo_id, p=is_item_paused: self._run_bg(
                    lambda: self.sab.resume_item(nid) if p else self.sab.pause_item(nid),
                    on_ok=lambda _r: self._refresh_sab_all(),
                ),
            ).pack(side="right", padx=3)

            try:
                pos = int(slot.get("index", slot_idx))
            except (TypeError, ValueError):
                pos = slot_idx
            move_buttons = []
            if slot_idx < len(slots) - 1:
                move_buttons.append(("down", "▼", "Move down one place", lambda nid=nzo_id, p=pos: self._move_queue_item(nid, p + 1)))
            if slot_idx > 0:
                move_buttons.append(("up", "▲", "Move up one place", lambda nid=nzo_id, p=pos: self._move_queue_item(nid, p - 1)))
                move_buttons.append(("top", "⤒", "Move to the top of the queue", lambda nid=nzo_id: self._run_bg(
                    lambda: self.sab.move_queue_item_top(nid), on_ok=lambda _r: self._refresh_sab_all()
                )))
            for icon_name, fallback, hint, cmd in move_buttons:
                ico = app_icon(icon_name, COLORS["text_secondary"], 12)
                mb = ctk.CTkButton(
                    bottom, text="" if ico else fallback, image=ico, width=30, height=26,
                    fg_color=COLORS["bg_panel"], hover_color=COLORS["border"], command=cmd,
                )
                mb.pack(side="right", padx=2)
                Tooltip(mb, hint)

            prio_var = ctk.StringVar(value=prio_label)
            ctk.CTkOptionMenu(
                bottom,
                variable=prio_var,
                values=SAB_PRIORITY_LABELS,
                width=82,
                height=26,
                fg_color=COLORS["bg_panel"],
                button_color=COLORS["border"],
                command=lambda choice, nid=nzo_id: self._run_bg(
                    lambda: self.sab.set_priority(nid, SAB_PRIORITY_TO_VAL[choice]),
                    on_ok=lambda _r: self._refresh_sab_all(),
                ),
            ).pack(side="right", padx=3)

            bar = ctk.CTkProgressBar(bottom, height=9, fg_color=COLORS["bg_root"], progress_color=status_color)
            bar.set(min(max(pct / 100.0, 0.0), 1.0))
            bar.pack(side="left", fill="x", expand=True, padx=(0, 12))

        self.after(20, lambda: self._set_scroll_y(self.queue_frame, scroll_y))

    def _render_sab_history(self, history_data: dict) -> None:
        day_size = history_data.get("day_size", "0 B")
        week_size = history_data.get("week_size", "0 B")
        total_size = history_data.get("total_size", "0 B")

        self.card_dl_val.configure(text=total_size)
        self.card_dl_sub.configure(text=f"Today: {day_size}  •  Week: {week_size}")
        self.history_header_label.configure(
            text=f"RECENT HISTORY  •  Today: {day_size}  |  Week: {week_size}  |  Total: {total_size}"
        )

        slots = history_data.get("slots", [])
        self._sab_history_keys = {
            normalize_release_key(s.get("name", ""))
            for s in slots
            if s.get("name") and (s.get("status") or "").strip().lower() == "completed"
        }

        current_states = {
            s.get("nzo_id", ""): (s.get("status", ""), s.get("name", "Unknown"), s.get("fail_message", ""))
            for s in slots
            if s.get("nzo_id")
        }
        if self._known_history_states is None:
            self._known_history_states = {nid: st for nid, (st, _nm, _fm) in current_states.items()}
        else:
            completed_names: list[str] = []
            failed_items: list[tuple[str, str]] = []
            for nid, (st, nm, fm) in current_states.items():
                prev = self._known_history_states.get(nid, "")
                st_low = st.strip().lower()
                if st_low != prev.strip().lower():
                    if st_low == "completed":
                        completed_names.append(nm)
                        self._set_status(f"Download completed: {nm}", log=True, level="OK")
                        if hasattr(self, "kodi_panel"):
                            self.kodi_panel.notify_download_completed(nm)
                        if hasattr(self, "media_server_panel"):
                            self.media_server_panel.notify_download_completed(nm)
                    elif st_low == "failed":
                        failed_items.append((nm, fm))
                        reason = f" — {fm}" if fm else ""
                        self._set_status(f"Download FAILED: {nm}{reason}", log=True, level="ERROR")
            self._known_history_states = {nid: st for nid, (st, _nm, _fm) in current_states.items()}
            self._notify_downloads(completed_names, failed_items)

        sig = tuple(
            (
                s.get("nzo_id", ""),
                s.get("name", ""),
                s.get("status", ""),
                s.get("size", ""),
                s.get("category", "*"),
                s.get("fail_message", ""),
            )
            for s in slots
        )
        if sig == self._last_history_sig:
            return
        self._last_history_sig = sig

        scroll_y = self._get_scroll_y(self.history_frame)

        for w in self.history_frame.winfo_children():
            w.destroy()

        if not slots:
            self._empty_state(
                self.history_frame, "No recent history", "Finished and failed downloads will be listed here.", compact=True
            )
            return

        for slot in slots:
            nzo_id = slot.get("nzo_id", "")
            name = slot.get("name", "Unknown")
            status = slot.get("status", "Unknown")
            size = slot.get("size", "")
            cat = slot.get("category", "*")
            fail_msg = (slot.get("fail_message") or "").strip()
            color, label = get_status_style(status)

            row = ctk.CTkFrame(self.history_frame, fg_color=COLORS["bg_card"], corner_radius=8, border_width=1, border_color=COLORS["border"])
            row.pack(fill="x", padx=8, pady=3)

            ctk.CTkLabel(
                row,
                text=label,
                width=92,
                fg_color=_tint(color),
                corner_radius=11,
                pady=2,
                font=ctk.CTkFont(family=UI_FONT, size=10, weight="bold"),
                text_color=color,
            ).pack(side="left", padx=(10, 8), pady=7)

            ctk.CTkButton(
                row,
                text="Remove",
                image=app_icon("delete", COLORS["text_secondary"], 12),
                width=82,
                height=24,
                fg_color=COLORS["bg_card_alt"],
                hover_color=COLORS["border"],
                command=lambda nid=nzo_id: self._run_bg(
                    lambda: self.sab.delete_history_item(nid),
                    on_ok=lambda _r: self._refresh_sab_all(),
                ),
            ).pack(side="right", padx=(4, 10), pady=6)

            if label == "FAILED":
                ctk.CTkButton(
                    row,
                    text="Retry",
                    image=app_icon("refresh", ON_ACCENT, 12),
                    width=72,
                    height=24,
                    fg_color=COLORS["amber"],
                    hover_color="#D97706",
                    text_color="#041016",
                    font=ctk.CTkFont(family=UI_FONT, size=11, weight="bold"),
                    command=lambda nid=nzo_id, nm=name: self._run_bg(
                        lambda: self.sab.retry_history_item(nid),
                        on_ok=lambda _r: (
                            self._set_status(f"Retrying failed job: {nm}", log=True, level="WARN"),
                            self._refresh_sab_all(),
                        ),
                    ),
                ).pack(side="right", padx=4, pady=6)

            info_txt = f"{name}  [{cat} • {size}]"
            if label == "FAILED" and fail_msg:
                info_txt += f"  —  {fail_msg}"

            ctk.CTkLabel(
                row,
                text=info_txt,
                anchor="w",
                text_color=COLORS["red"] if (label == "FAILED" and fail_msg) else COLORS["text_primary"],
            ).pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.after(20, lambda: self._set_scroll_y(self.history_frame, scroll_y))

    def _app_has_focus(self) -> bool:
        try:
            return self.state() != "iconic" and self.focus_displayof() is not None
        except Exception:
            return False

    def _notify_downloads(self, completed: list[str], failed: list[tuple[str, str]], force: bool = False) -> None:
        """One Windows notification per refresh for finished/failed downloads.

        Only shown while the app isn't the active window (when you're looking at it, the status
        bar and Activity log already say it). Several completions are grouped into one notification.
        """
        if not (completed or failed) or not self.config_data.get("desktop_notifications", True):
            return
        if not force and self._app_has_focus():
            return
        if failed:
            name, reason = failed[0]
            title = "Download failed" if len(failed) == 1 else f"{len(failed)} downloads failed"
            msg = f"{name}" + (f"\n{reason}" if reason and len(failed) == 1 else "")
            if completed:
                msg += f"\n{len(completed)} other download(s) completed."
        elif len(completed) == 1:
            title, msg = "Download complete", completed[0]
        else:
            title = f"{len(completed)} downloads complete"
            msg = "\n".join(completed[:3]) + (f"\n…and {len(completed) - 3} more" if len(completed) > 3 else "")
        show_windows_notification(title, msg)

    def _on_notifications_toggled(self) -> None:
        on = bool(self.notify_var.get())
        self.config_data["desktop_notifications"] = on
        self._safe_write_config("notification")
        if on and show_windows_notification("NZB Command Center", "Notifications are on. You'll be told when downloads finish or fail."):
            self._set_status("Desktop notifications on (a test notification was sent).", log=True, level="OK")
        elif not on:
            self._set_status("Desktop notifications off.", log=True, level="INFO")

    def _clear_sab_history(self, failed_only: bool = False) -> None:
        prompt = "Remove all FAILED jobs from SABnzbd history?" if failed_only else "Clear ALL items from SABnzbd history?"
        if not messagebox.askyesno("Clear SABnzbd History", prompt):
            return
        self._run_bg(
            lambda: self.sab.clear_history(failed_only=failed_only),
            on_ok=lambda _r: (
                self._set_status(f"Cleared {'failed ' if failed_only else ''}SABnzbd history.", log=True, level="INFO"),
                self._refresh_sab_all(),
            ),
        )

    def _toggle_pause_all(self) -> None:
        self._run_bg(
            lambda: self.sab.resume_all() if self.sab_paused else self.sab.pause_all(),
            on_ok=lambda _r: self._refresh_sab_all(),
        )

    def _apply_speed_limit(self, explicit_val: str | None = None) -> None:
        val = explicit_val if explicit_val is not None else self.speed_entry.get().strip()
        if not val:
            return
        self._run_bg(
            lambda: self.sab.set_speed_limit(val),
            on_ok=lambda _r: (
                self._set_status(
                    "SABnzbd speed limit removed (Unlimited)" if val in ("0", "100%") else f"SABnzbd speed limit set to {val}",
                    log=True,
                    level="OK",
                ),
                self._refresh_sab_all(),
            ),
        )


def main() -> None:
    if _SYS == "Windows":
        try:  # group under our own icon on the taskbar instead of python.exe's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NZBCommandCenter.App")
        except Exception:
            pass
    app = NzbManagerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
