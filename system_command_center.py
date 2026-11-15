"""
System Command Center
Windows, GPU, drives, network and processes in one live dashboard.

Dependencies:
    pip install customtkinter psutil pillow        (pillow is optional: window icon)

Sibling module (keep in the same folder as this script):
    system_monitor.py   data collection, alerts and Windows actions

Optional:
    LibreHardwareMonitor running in the background adds CPU / AMD / Intel GPU temperatures and
    fan speeds (Windows itself doesn't expose them). NVIDIA cards report temperature, power,
    fan and clocks through the driver's nvidia-smi, no extra install needed.

Usage (PowerShell):
    python system_command_center.py
"""

from __future__ import annotations

import ctypes
import os
import platform
import queue
import socket
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
import psutil

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import system_monitor as sm  # noqa: E402
from system_monitor import fmt_bytes, fmt_duration, fmt_pct, fmt_rate, fmt_temp  # noqa: E402

APP_NAME = "System Command Center"
APP_VERSION = "1.0"

# Same palette as NZB Command Center so the two apps feel like one family.
C = {
    "bg_root": "#060D10",
    "bg_panel": "#0A151A",
    "bg_card": "#0F1E25",
    "bg_card_alt": "#152933",
    "border": "#193642",
    "border_accent": "#14B8A6",
    "grid_line": "#122A34",
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
    "pink": "#F472B6",
}
ON_ACCENT = "#041016"

_SYS = platform.system()
UI_FONT = "Segoe UI" if _SYS == "Windows" else ("SF Pro Text" if _SYS == "Darwin" else "DejaVu Sans")
UI_FONT_SEMIBOLD = "Segoe UI Semibold" if _SYS == "Windows" else UI_FONT
MONO_FONT = "Consolas" if _SYS == "Windows" else "DejaVu Sans Mono"

# (Segoe Fluent / MDL2 glyph, fallback character for systems without the Windows icon font)
ICONS = {
    "home": ("", "⌂"), "cpu": ("", "▦"), "gpu": ("", "▣"), "drive": ("", "◫"),
    "network": ("", "⇅"), "apps": ("", "≡"), "windows": ("", "⊞"), "tools": ("", "⚒"),
    "bell": ("", "⚠"), "settings": ("", "⚙"), "refresh": ("", "↻"), "shield": ("", "⛨"),
    "folder": ("", "▤"), "search": ("", "⌕"), "delete": ("", "✕"), "power": ("", "⏻"),
    "lock": ("", "◉"), "sleep": ("", "☾"), "restart": ("", "↻"), "cancel": ("", "✕"),
    "pulse": ("", "∿"), "devices": ("", "◧"), "tower": ("", "⇅"), "warning": ("", "⚠"),
    "info": ("", "ⓘ"), "bolt": ("", "ϟ"), "open": ("", "↗"), "down": ("", "↓"),
    "up": ("", "↑"), "list": ("", "≡"), "display": ("", "▭"), "sound": ("", "♪"),
    "clean": ("", "✧"), "terminal": ("", "›"), "update": ("", "⟳"), "apps2": ("", "▦"),
    "chart": ("", "▥"), "services": ("", "⚙"), "event": ("", "▤"), "explorer": ("", "▤"),
}
_ICON_FAMILIES = ("Segoe Fluent Icons", "Segoe MDL2 Assets")
_icon_family: str | None = None


def icon_family() -> str:
    """Windows icon font family, or '' (fallback characters are used then). Needs a Tk root."""
    global _icon_family
    if _icon_family is None:
        try:
            fams = set(tkfont.families())
        except Exception:
            return ""
        _icon_family = next((f for f in _ICON_FAMILIES if f in fams), "")
    return _icon_family


def icon_text(name: str) -> str:
    glyph, fallback = ICONS.get(name, ("", "•"))
    return glyph if icon_family() else fallback


def icon_font(size: int = 14):
    fam = icon_family()
    return (fam, size) if fam else (UI_FONT, size)


def tint(hex_color: str, amount: float = 0.16, base: str = C["bg_card"]) -> str:
    """Blends a colour into a dark base (for subtle pill backgrounds and graph fills)."""
    def rgb(h: str) -> tuple[int, int, int]:
        h = h.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    try:
        c, b = rgb(hex_color), rgb(base)
    except (ValueError, IndexError):
        return base
    return "#%02X%02X%02X" % tuple(round(b[i] + (c[i] - b[i]) * amount) for i in range(3))


def level_color(pct: float | None, warn: float = 75, crit: float = 90) -> str:
    if pct is None:
        return C["text_muted"]
    return C["red"] if pct >= crit else C["amber"] if pct >= warn else C["accent"]


def temp_color(t: float | None, warn: float = 75, crit: float = 88) -> str:
    if t is None:
        return C["text_muted"]
    return C["red"] if t >= crit else C["amber"] if t >= warn else C["green"]


def fmt_mhz(mhz: float | None) -> str:
    if not mhz:
        return "—"
    return f"{mhz / 1000:.2f} GHz" if mhz >= 1000 else f"{mhz:.0f} MHz"


def short_cpu_name(name: str) -> str:
    n = (name or "").replace("(R)", "").replace("(TM)", "").replace("(tm)", "")
    n = n.split(" @ ")[0]
    for junk in (" CPU", " Processor", " with Radeon Graphics", "-Core"):
        n = n.replace(junk, "")
    return " ".join(n.split())


def scaled(widget, px: float) -> int:
    try:
        return int(round(px * ctk.ScalingTracker.get_widget_scaling(widget)))
    except Exception:
        return int(px)


def apply_theme() -> None:
    """Consistent defaults for every CustomTkinter widget (must run before widgets exist)."""
    t = ctk.ThemeManager.theme

    def pair(color: str) -> list[str]:
        return [color, color]

    t["CTkFont"].update(family=UI_FONT, size=12, weight="normal")
    t["CTk"]["fg_color"] = pair(C["bg_root"])
    t["CTkToplevel"]["fg_color"] = pair(C["bg_root"])
    t["CTkFrame"].update(fg_color=pair(C["bg_panel"]), top_fg_color=pair(C["bg_card"]), border_color=pair(C["border"]))
    t["CTkLabel"]["text_color"] = pair(C["text_primary"])
    t["CTkButton"].update(corner_radius=8, border_width=0, fg_color=pair(C["bg_card_alt"]), hover_color=pair("#1C3542"),
                          border_color=pair(C["border"]), text_color=pair(C["text_primary"]),
                          text_color_disabled=pair(C["text_muted"]))
    t["CTkEntry"].update(corner_radius=8, border_width=1, fg_color=pair(C["bg_card"]), border_color=pair(C["border"]),
                         text_color=pair(C["text_primary"]), placeholder_text_color=pair(C["text_muted"]))
    t["CTkOptionMenu"].update(corner_radius=8, fg_color=pair(C["bg_card_alt"]), button_color=pair(C["border"]),
                              button_hover_color=pair("#24505F"), text_color=pair(C["text_primary"]),
                              text_color_disabled=pair(C["text_muted"]))
    t["DropdownMenu"].update(fg_color=pair(C["bg_card_alt"]), hover_color=pair(C["border"]), text_color=pair(C["text_primary"]))
    t["CTkCheckBox"].update(corner_radius=5, border_width=2, fg_color=pair(C["accent"]), border_color=pair(C["text_muted"]),
                            hover_color=pair(C["accent_hover"]), checkmark_color=pair(C["bg_root"]),
                            text_color=pair(C["text_primary"]))
    t["CTkSwitch"].update(fg_color=pair(C["border"]), progress_color=pair(C["accent"]), button_color=pair(C["text_primary"]),
                          button_hover_color=pair("#FFFFFF"), text_color=pair(C["text_primary"]))
    t["CTkProgressBar"].update(fg_color=pair(C["bg_card_alt"]), progress_color=pair(C["accent"]))
    t["CTkSlider"].update(fg_color=pair(C["border"]), progress_color=pair(C["accent"]), button_color=pair(C["accent"]),
                          button_hover_color=pair(C["accent_hover"]))
    t["CTkScrollbar"].update(button_color=pair(C["border"]), button_hover_color=pair("#24505F"))
    t["CTkSegmentedButton"].update(corner_radius=8, border_width=3, fg_color=pair(C["bg_panel"]),
                                   selected_color=pair(C["accent"]), selected_hover_color=pair(C["accent_hover"]),
                                   unselected_color=pair(C["bg_panel"]), unselected_hover_color=pair(C["bg_card_alt"]),
                                   text_color=pair(C["text_primary"]))
    t["CTkTextbox"].update(corner_radius=8, fg_color=pair(C["bg_panel"]), border_color=pair(C["border"]),
                           text_color=pair(C["text_primary"]), scrollbar_button_color=pair(C["border"]),
                           scrollbar_button_hover_color=pair("#24505F"))
    t["CTkScrollableFrame"]["label_fg_color"] = pair(C["bg_card"])


def style_treeview(root) -> str:
    """Dark ttk.Treeview style for the process list and dialogs; returns the style name."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    row_h = scaled(root, 26)
    style.configure("SCC.Treeview", background=C["bg_card"], fieldbackground=C["bg_card"], foreground=C["text_primary"],
                    bordercolor=C["border"], borderwidth=0, rowheight=row_h, font=(UI_FONT, 10))
    style.map("SCC.Treeview", background=[("selected", tint(C["accent"], 0.28))], foreground=[("selected", C["text_primary"])])
    style.configure("SCC.Treeview.Heading", background=C["bg_panel"], foreground=C["text_secondary"], relief="flat",
                    bordercolor=C["border"], font=(UI_FONT_SEMIBOLD, 10), padding=(6, 5))
    style.map("SCC.Treeview.Heading", background=[("active", C["bg_card_alt"])])
    style.layout("SCC.Treeview", [("SCC.Treeview.treearea", {"sticky": "nswe"})])
    return "SCC.Treeview"


APP_ICON_FILE = Path(__file__).resolve().parent / "system_center_icon.ico"


def ensure_app_icon() -> Path | None:
    """Window/taskbar icon: a teal tile with a pulse line. Drawn once with Pillow if missing."""
    if APP_ICON_FILE.exists():
        return APP_ICON_FILE
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    try:
        size = 256
        im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((8, 8, size - 8, size - 8), radius=58, fill=C["accent"])
        pts = [(40, 140), (88, 140), (108, 86), (136, 186), (160, 112), (176, 140), (216, 140)]
        d.line(pts, fill=ON_ACCENT, width=22, joint="curve")
        im.save(APP_ICON_FILE, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        return APP_ICON_FILE
    except Exception:
        return None


def apply_window_chrome(win, delay_ms: int = 250) -> None:
    """App icon plus (Windows 11) a title bar tinted to match the app."""
    def run() -> None:
        try:
            if not win.winfo_exists():
                return
            icon = ensure_app_icon()
            if icon and _SYS == "Windows":
                win.iconbitmap(str(icon))
            if _SYS != "Windows":
                return

            def colorref(hex_color: str) -> int:
                h = hex_color.lstrip("#")
                return int(h[4:6] + h[2:4] + h[0:2], 16)

            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            for attr, color in ((35, C["bg_panel"]), (34, C["border"]), (36, C["text_primary"])):
                val = ctypes.c_int(colorref(color))
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
        except Exception:
            pass

    win.after(delay_ms, run)


# =============================================================================================
# Reusable widgets
# =============================================================================================

class Tooltip:
    def __init__(self, widget, text: str, delay_ms: int = 450):
        self.widget, self.text, self.delay_ms = widget, text, delay_ms
        self._after = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._hide()
        self._after = self.widget.after(self.delay_ms, self._show)

    def _show(self):
        self._after = None
        try:
            if not self.text or not self.widget.winfo_exists():
                return
            tip = tk.Toplevel(self.widget)
            tip.wm_overrideredirect(True)
            tip.attributes("-topmost", True)
            tk.Label(tip, text=self.text, justify="left", wraplength=360, padx=9, pady=5, bg=C["bg_card_alt"],
                     fg=C["text_primary"], font=(UI_FONT, 9), highlightthickness=1,
                     highlightbackground=C["border_accent"], bd=0).pack()
            tip.wm_geometry(f"+{self.widget.winfo_rootx() + 6}+{self.widget.winfo_rooty() + self.widget.winfo_height() + 6}")
            self._tip = tip
        except Exception:
            self._tip = None

    def _hide(self, _e=None):
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except Exception:
                pass
            self._after = None
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


class Card(ctk.CTkFrame):
    """Rounded panel with an optional icon + title row. Put content in .body; actions in .actions."""

    def __init__(self, master, title: str = "", icon: str | None = None, **kw):
        super().__init__(master, fg_color=C["bg_card"], corner_radius=14, border_width=1, border_color=C["border"], **kw)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.actions = None
        if title:
            head = ctk.CTkFrame(self, fg_color="transparent")
            head.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 2))
            if icon:
                ctk.CTkLabel(head, text=icon_text(icon), font=icon_font(14), text_color=C["accent"]).pack(side="left", padx=(0, 8))
            self.title_label = ctk.CTkLabel(head, text=title.upper(), font=(UI_FONT_SEMIBOLD, 11), text_color=C["text_secondary"])
            self.title_label.pack(side="left")
            self.actions = ctk.CTkFrame(head, fg_color="transparent", width=1, height=1)
            self.actions.pack(side="right")
        # width/height=1: an empty CTkFrame otherwise asks for 200x200 and leaves big gaps
        self.body = ctk.CTkFrame(self, fg_color="transparent", width=1, height=1)
        self.body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(6, 14))

    def set_title(self, title: str) -> None:
        if hasattr(self, "title_label"):
            self.title_label.configure(text=title.upper())


def small_button(master, text: str, command, icon: str | None = None, accent: bool = False, width: int = 0, **kw):
    """Compact button. (icon is accepted for readability at call sites; CTkButton can't mix icon fonts.)"""
    btn = ctk.CTkButton(master, text=text, command=command, height=30, width=width or 0,
                        font=(UI_FONT_SEMIBOLD if accent else UI_FONT, 12),
                        fg_color=C["accent"] if accent else C["bg_card_alt"],
                        hover_color=C["accent_hover"] if accent else "#1C3542",
                        text_color=ON_ACCENT if accent else C["text_primary"], **kw)
    return btn


class Pill(ctk.CTkLabel):
    def __init__(self, master, text: str = "", color: str = C["accent"], **kw):
        super().__init__(master, text=text, font=(UI_FONT_SEMIBOLD, 10), corner_radius=8, height=20,
                         fg_color=tint(color, 0.22), text_color=color, **kw)
        self._state = None

    def set(self, text: str, color: str) -> None:
        if self._state != (text, color):
            self._state = (text, color)
            self.configure(text=f"  {text}  ", fg_color=tint(color, 0.22), text_color=color)


class KeyValues(ctk.CTkFrame):
    """Two-column label/value list. set(key, value, color) updates only what changed."""

    def __init__(self, master, keys: list[str], columns: int = 1, key_width: int = 120, wrap: int = 0):
        super().__init__(master, fg_color="transparent")
        self._vals: dict[str, ctk.CTkLabel] = {}
        self._cache: dict[str, tuple] = {}
        rows = (len(keys) + columns - 1) // columns
        for c in range(columns):
            self.grid_columnconfigure(c * 2 + 1, weight=1, uniform="kv")
        for i, key in enumerate(keys):
            r, c = i % rows, i // rows
            ctk.CTkLabel(self, text=key, text_color=C["text_muted"], font=(UI_FONT, 12), anchor="w",
                         width=key_width).grid(row=r, column=c * 2, sticky="nw", pady=2, padx=(0 if c == 0 else 18, 8))
            v = ctk.CTkLabel(self, text="—", font=(UI_FONT, 12), anchor="w", justify="left",
                             wraplength=wrap or 0)
            v.grid(row=r, column=c * 2 + 1, sticky="nw", pady=2)
            self._vals[key] = v

    def set(self, key: str, value, color: str | None = None) -> None:
        text = "—" if value in (None, "") else str(value)
        state = (text, color)
        if self._cache.get(key) == state:
            return
        self._cache[key] = state
        self._vals[key].configure(text=text, text_color=color or C["text_primary"])


class UsageRow(ctk.CTkFrame):
    """Title + value on one line, a bar beneath, optional small caption."""

    def __init__(self, master, title: str, caption: bool = True, bar_height: int = 8, title_font=None):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self.title = ctk.CTkLabel(self, text=title, anchor="w", font=title_font or (UI_FONT_SEMIBOLD, 12))
        self.title.grid(row=0, column=0, sticky="w")
        self.value = ctk.CTkLabel(self, text="—", anchor="e", font=(UI_FONT, 12), text_color=C["text_secondary"])
        self.value.grid(row=0, column=1, sticky="e")
        self.bar = ctk.CTkProgressBar(self, height=bar_height, corner_radius=bar_height // 2)
        self.bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(3, 0))
        self.bar.set(0)
        self.caption = None
        if caption:
            self.caption = ctk.CTkLabel(self, text="", anchor="w", font=(UI_FONT, 11), text_color=C["text_muted"])
            self.caption.grid(row=2, column=0, columnspan=2, sticky="w")
        self._state = None

    def set(self, frac: float | None, value: str, caption: str | None = None, color: str | None = None,
            title: str | None = None) -> None:
        state = (round(frac or 0, 3), value, caption, color, title)
        if state == self._state:
            return
        self._state = state
        self.bar.set(max(0.0, min(1.0, frac or 0.0)))
        self.bar.configure(progress_color=color or C["accent"])
        self.value.configure(text=value)
        if title is not None:
            self.title.configure(text=title)
        if self.caption is not None and caption is not None:
            self.caption.configure(text=caption)


class Sparkline(tk.Canvas):
    """History graph: one or more series with a soft filled area. max_value=None auto-scales."""

    def __init__(self, master, colors=(C["accent"],), height: int = 60, max_value: float | None = 100.0,
                 fmt=None, bg: str = C["bg_card"], fill: bool = True, legend: list[str] | None = None):
        super().__init__(master, height=scaled(master, height), bg=bg, highlightthickness=0, bd=0)
        self.colors = list(colors)
        self.max_value = max_value
        self.fmt = fmt
        self.bgc = bg
        self.fill = fill
        self.legend = legend
        self._data: list[list[float]] = [[] for _ in self.colors]
        self.bind("<Configure>", lambda _e: self._redraw())

    def set(self, *series) -> None:
        self._data = [list(s) for s in series]
        self._redraw()

    @staticmethod
    def _nice(v: float) -> float:
        if v <= 0:
            return 1.0
        mag = 10 ** int(f"{v:e}".split("e")[1])
        for m in (1, 2, 2.5, 5, 10):
            if v <= m * mag:
                return m * mag
        return 10 * mag

    def _redraw(self) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10 or h < 10:
            return
        self.delete("all")
        for i in (1, 2, 3):
            y = h * i / 4
            self.create_line(0, y, w, y, fill=C["grid_line"])
        top = self.max_value
        if top is None:
            peak = max((max(s) for s in self._data if s), default=0.0)
            top = self._nice(peak * 1.1) if peak > 0 else 1.0
        step = w / max(1, sm.HISTORY_LEN - 1)
        lw = max(1, scaled(self, 2))
        lines = []
        for idx in range(len(self._data) - 1, -1, -1):
            series = self._data[idx]
            if len(series) < 2:
                continue
            color = self.colors[idx % len(self.colors)]
            x0 = w - (len(series) - 1) * step
            pts = []
            for i, v in enumerate(series):
                pts.extend((x0 + i * step, h - 2 - (h - 5) * max(0.0, min(v, top)) / top))
            if self.fill:
                self.create_polygon(x0, h, *pts, pts[-2], h, fill=tint(color, 0.20, self.bgc), outline="")
            lines.append((pts, color))
        for pts, color in lines:  # lines above every fill, so no series hides another
            self.create_line(*pts, fill=color, width=lw)
        if self.max_value is None and self.fmt:
            self.create_text(6, 4, anchor="nw", text=self.fmt(top), fill=C["text_muted"], font=(UI_FONT, 8))
        if self.legend:
            x = w - 6
            for idx in range(len(self.legend) - 1, -1, -1):
                t = self.create_text(x, 4, anchor="ne", text=self.legend[idx], fill=self.colors[idx], font=(UI_FONT_SEMIBOLD, 8))
                bbox = self.bbox(t)
                x = (bbox[0] if bbox else x) - 10


class RingGauge(tk.Canvas):
    """270° ring with a big value in the middle and a caption underneath."""

    def __init__(self, master, size: int = 116, thickness: int = 11, bg: str = C["bg_card"], caption: str = ""):
        s = scaled(master, size)
        super().__init__(master, width=s, height=s, bg=bg, highlightthickness=0, bd=0)
        t = scaled(master, thickness)
        pad = t / 2 + 3
        self.create_arc(pad, pad, s - pad, s - pad, start=225, extent=-270, style="arc", width=t, outline=C["bg_card_alt"])
        self._arc = self.create_arc(pad, pad, s - pad, s - pad, start=225, extent=-1, style="arc", width=t,
                                    outline=C["accent"], state="hidden")
        self._big = max(11, int(size / 6.4))
        self._val = self.create_text(s / 2, s / 2 - s * 0.03, text="—", fill=C["text_primary"], font=(UI_FONT_SEMIBOLD, self._big))
        self._cap = self.create_text(s / 2, s / 2 + s * 0.22, text=caption, fill=C["text_muted"], font=(UI_FONT, 9))
        self._state = None

    def set(self, frac: float | None, text: str, caption: str | None = None, color: str | None = None) -> None:
        state = (None if frac is None else round(frac, 3), text, caption, color)
        if state == self._state:
            return
        self._state = state
        if frac is None or frac <= 0.002:
            self.itemconfigure(self._arc, state="hidden")
        else:
            self.itemconfigure(self._arc, state="normal", extent=-270 * min(1.0, frac), outline=color or C["accent"])
        shrink = {5: 0.86, 6: 0.74, 7: 0.66}.get(len(text), 1.0 if len(text) < 5 else 0.58)
        self.itemconfigure(self._val, text=text, font=(UI_FONT_SEMIBOLD, max(9, int(self._big * shrink))))
        if caption is not None:
            self.itemconfigure(self._cap, text=caption)


class CoreGrid(tk.Canvas):
    """One small tile per logical processor, filled from the bottom by its load."""

    def __init__(self, master, bg: str = C["bg_card"]):
        super().__init__(master, height=scaled(master, 120), bg=bg, highlightthickness=0, bd=0)
        self.values: list[float] = []
        self.bind("<Configure>", lambda _e: self._redraw())

    def set(self, values: list[float]) -> None:
        self.values = list(values)
        self._redraw()

    def _redraw(self) -> None:
        n = len(self.values)
        w = self.winfo_width()
        if not n or w < 20:
            return
        gap = scaled(self, 6)
        min_cell = scaled(self, 64)
        cols = max(1, min(n, (w + gap) // (min_cell + gap)))
        rows = (n + cols - 1) // cols
        cw = (w - gap * (cols - 1)) / cols
        ch = scaled(self, 52)
        need = int(rows * ch + (rows - 1) * gap)
        if abs(int(self.cget("height")) - need) > 1:
            self.configure(height=need)
        self.delete("all")
        for i, v in enumerate(self.values):
            r, c = divmod(i, cols)
            x0, y0 = c * (cw + gap), r * (ch + gap)
            x1, y1 = x0 + cw, y0 + ch
            color = level_color(v, 70, 90)
            self.create_rectangle(x0, y0, x1, y1, fill=C["bg_card_alt"], outline="")
            fh = (ch - 2) * max(0.0, min(v, 100.0)) / 100
            if fh > 0:
                self.create_rectangle(x0, y1 - fh, x1, y1, fill=tint(color, 0.55, C["bg_card_alt"]), outline="")
            self.create_text(x0 + 6, y0 + 4, anchor="nw", text=f"CPU {i}", fill=C["text_secondary"], font=(UI_FONT, 8))
            self.create_text(x1 - 6, y1 - 4, anchor="se", text=f"{v:.0f}%", fill=C["text_primary"], font=(UI_FONT_SEMIBOLD, 11))


class NavItem(ctk.CTkFrame):
    def __init__(self, master, key: str, label: str, icon: str, command):
        super().__init__(master, fg_color="transparent", corner_radius=9, height=40, cursor="hand2")
        self.key = key
        self.command = command
        self.selected = False
        self.pack_propagate(False)
        self.bar = ctk.CTkFrame(self, width=3, height=18, corner_radius=2, fg_color="transparent")
        self.bar.pack(side="left", padx=(5, 0))
        self.icon = ctk.CTkLabel(self, text=icon_text(icon), font=icon_font(15), width=30, text_color=C["text_secondary"])
        self.icon.pack(side="left", padx=(8, 4))
        self.text = ctk.CTkLabel(self, text=label, font=(UI_FONT, 13), anchor="w", text_color=C["text_secondary"])
        self.text.pack(side="left", fill="x", expand=True)
        self.badge = ctk.CTkLabel(self, text="", font=(UI_FONT_SEMIBOLD, 10), fg_color=C["red"], corner_radius=9,
                                  width=22, height=18, text_color="#FFFFFF")
        for w in (self, self.icon, self.text, self.bar):
            w.bind("<Button-1>", lambda _e: self.command(self.key))
            w.bind("<Enter>", lambda _e: self._hover(True))
            w.bind("<Leave>", lambda _e: self._hover(False))

    def _hover(self, on: bool) -> None:
        if not self.selected:
            self.configure(fg_color=C["bg_card"] if on else "transparent")

    def set_selected(self, sel: bool) -> None:
        self.selected = sel
        self.configure(fg_color=C["bg_card_alt"] if sel else "transparent")
        self.bar.configure(fg_color=C["accent"] if sel else "transparent")
        color = C["accent"] if sel else C["text_secondary"]
        self.icon.configure(text_color=color)
        self.text.configure(text_color=C["text_primary"] if sel else C["text_secondary"],
                            font=(UI_FONT_SEMIBOLD if sel else UI_FONT, 13))

    def set_badge(self, n: int) -> None:
        if n > 0:
            self.badge.configure(text=str(n) if n < 100 else "99+")
            if not self.badge.winfo_ismapped():
                self.badge.pack(side="right", padx=8)
        elif self.badge.winfo_ismapped():
            self.badge.pack_forget()


class Tile(ctk.CTkFrame):
    """Clickable launcher tile: icon, name and a one-line description."""

    def __init__(self, master, name: str, desc: str, icon: str, command, color: str = C["accent"]):
        super().__init__(master, fg_color=C["bg_card_alt"], corner_radius=10, cursor="hand2")
        self.command = command
        self.grid_columnconfigure(1, weight=1)
        ic = ctk.CTkLabel(self, text=icon_text(icon), font=icon_font(18), width=36, text_color=color)
        ic.grid(row=0, column=0, rowspan=2, padx=(12, 6), pady=10)
        nm = ctk.CTkLabel(self, text=name, font=(UI_FONT_SEMIBOLD, 12), anchor="w")
        nm.grid(row=0, column=1, sticky="sw", pady=(9, 0), padx=(0, 10))
        ds = ctk.CTkLabel(self, text=desc, font=(UI_FONT, 11), anchor="w", text_color=C["text_muted"])
        ds.grid(row=1, column=1, sticky="nw", pady=(0, 9), padx=(0, 10))
        for w in (self, ic, nm, ds):
            w.bind("<Button-1>", lambda _e: self.command())
            w.bind("<Enter>", lambda _e: self.configure(fg_color="#1C3542"))
            w.bind("<Leave>", lambda _e: self.configure(fg_color=C["bg_card_alt"]))


# =============================================================================================
# Pages
# =============================================================================================

class Page(ctk.CTkFrame):
    title = ""
    subtitle = ""
    scroll = True

    def __init__(self, master, app: "CommandCenterApp"):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.monitor: sm.SystemMonitor = app.monitor
        if self.scroll:
            self.body = ctk.CTkScrollableFrame(self, fg_color="transparent", scrollbar_button_color=C["border"])
        else:
            self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True)
        self.build()

    def grid4(self, frame=None) -> None:
        frame = frame or self.body
        for c in range(4):
            frame.grid_columnconfigure(c, weight=1, uniform="col")

    def build(self) -> None:  # pragma: no cover - overridden
        pass

    def on_show(self) -> None:
        pass

    def refresh(self, snap: dict) -> None:
        pass


# ---------------------------------------------------------------------------------------------
class DashboardPage(Page):
    title = "Dashboard"
    subtitle = "Everything at a glance"

    def build(self) -> None:
        b = self.body
        self.grid4()
        pad = dict(padx=7, pady=7, sticky="nsew")

        # --- row 0: four gauge cards ---
        self.cpu_card = Card(b, "Processor", "cpu")
        self.cpu_card.grid(row=0, column=0, **pad)
        self.cpu_ring, self.cpu_kv, self.cpu_spark = self._gauge_card(self.cpu_card, ["Clock", "Temp", "Processes"], C["accent"])

        self.mem_card = Card(b, "Memory", "chart")
        self.mem_card.grid(row=0, column=1, **pad)
        self.mem_ring, self.mem_kv, self.mem_spark = self._gauge_card(self.mem_card, ["In use", "Free", "Total"], C["purple"])

        self.gpu_card = Card(b, "Graphics", "gpu")
        self.gpu_card.grid(row=0, column=2, **pad)
        self.gpu_ring, self.gpu_kv, self.gpu_spark = self._gauge_card(self.gpu_card, ["VRAM", "Temp", "Power"], C["pink"])

        self.net_card = Card(b, "Network", "network")
        self.net_card.grid(row=0, column=3, **pad)
        nb = self.net_card.body
        nb.grid_columnconfigure(0, weight=1)
        self.net_down = ctk.CTkLabel(nb, text="↓ —", font=(UI_FONT_SEMIBOLD, 22), text_color=C["sky"], anchor="w")
        self.net_down.grid(row=0, column=0, sticky="w")
        self.net_up = ctk.CTkLabel(nb, text="↑ —", font=(UI_FONT_SEMIBOLD, 16), text_color=C["purple"], anchor="w")
        self.net_up.grid(row=1, column=0, sticky="w")
        self.net_ping = ctk.CTkLabel(nb, text="Ping —", font=(UI_FONT, 12), text_color=C["text_secondary"], anchor="w")
        self.net_ping.grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.net_spark = Sparkline(nb, colors=(C["sky"], C["purple"]), height=58, max_value=None, fmt=fmt_rate)
        self.net_spark.grid(row=3, column=0, sticky="ew", pady=(10, 0))

        # --- row 1: drives + system ---
        self.drives_card = Card(b, "Drives", "drive")
        self.drives_card.grid(row=1, column=0, columnspan=2, **pad)
        small_button(self.drives_card.actions, "Details", lambda: self.app.show_page("drives"), width=70).pack(side="right")
        self.drives_card.body.grid_columnconfigure((0, 1), weight=1, uniform="d")
        self._drive_rows: dict[str, UsageRow] = {}
        self._drive_order: list[str] = []

        self.sys_card = Card(b, "System", "windows")
        self.sys_card.grid(row=1, column=2, columnspan=2, **pad)
        self.sys_kv = KeyValues(self.sys_card.body, ["Windows", "Version", "Uptime", "Power plan", "Battery",
                                                     "Security", "Restart needed", "Computer"], columns=2, key_width=100)
        self.sys_kv.pack(fill="x")

        # --- row 2: top processes + activity/alerts ---
        self.proc_card = Card(b, "Top processes", "apps")
        self.proc_card.grid(row=2, column=0, columnspan=2, **pad)
        small_button(self.proc_card.actions, "All processes", lambda: self.app.show_page("processes"), width=100).pack(side="right")
        pb = self.proc_card.body
        pb.grid_columnconfigure(0, weight=1)
        for col, (txt, anchor) in enumerate((("Name", "w"), ("CPU", "e"), ("Memory", "e"), ("GPU", "e"))):
            ctk.CTkLabel(pb, text=txt, text_color=C["text_muted"], font=(UI_FONT, 11), anchor=anchor,
                         width=10 if col == 0 else 70).grid(row=0, column=col, sticky="ew", padx=(0, 6))
        self._proc_rows = []
        for r in range(7):
            labels = []
            for col in range(4):
                lbl = ctk.CTkLabel(pb, text="", font=(UI_FONT, 12), anchor="w" if col == 0 else "e",
                                   width=10 if col == 0 else 70)
                lbl.grid(row=r + 1, column=col, sticky="ew", padx=(0, 6), pady=1)
                labels.append(lbl)
            self._proc_rows.append(labels)

        self.act_card = Card(b, "Disk activity & alerts", "pulse")
        self.act_card.grid(row=2, column=2, columnspan=2, **pad)
        ab = self.act_card.body
        ab.grid_columnconfigure(0, weight=1)
        self.disk_rw = ctk.CTkLabel(ab, text="Read —   Write —", font=(UI_FONT, 12), text_color=C["text_secondary"], anchor="w")
        self.disk_rw.grid(row=0, column=0, sticky="w")
        self.disk_spark = Sparkline(ab, colors=(C["green"], C["amber"]), height=64, max_value=None, fmt=fmt_rate,
                                    legend=["read", "write"])
        self.disk_spark.grid(row=1, column=0, sticky="ew", pady=(4, 10))
        self.alert_lines = []
        for r in range(3):
            lbl = ctk.CTkLabel(ab, text="", font=(UI_FONT, 12), anchor="w", justify="left")
            lbl.grid(row=2 + r, column=0, sticky="w")
            self.alert_lines.append(lbl)

    def _gauge_card(self, card: Card, keys: list[str], color: str):
        body = card.body
        body.grid_columnconfigure(1, weight=1)
        ring = RingGauge(body, size=112)
        ring.grid(row=0, column=0, sticky="w", padx=(0, 10))
        kv = KeyValues(body, keys, key_width=54)
        kv.grid(row=0, column=1, sticky="w")
        spark = Sparkline(body, colors=(color,), height=58)
        spark.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        return ring, kv, spark

    def refresh(self, snap: dict) -> None:
        m = self.monitor
        cpu, mem = snap.get("cpu", {}), snap.get("mem", {})
        inv = snap.get("inventory", {})
        cpu_name = short_cpu_name((inv.get("cpu") or [{}])[0].get("name", "")) if inv.get("cpu") else ""
        self.cpu_card.set_title(cpu_name or "Processor")
        total = cpu.get("total")
        self.cpu_ring.set((total or 0) / 100, fmt_pct(total), "load", level_color(total))
        self.cpu_kv.set("Clock", fmt_mhz(cpu.get("freq")))
        self.cpu_kv.set("Temp", fmt_temp(cpu.get("temp")), temp_color(cpu.get("temp")) if cpu.get("temp") else C["text_muted"])
        self.cpu_kv.set("Processes", len(snap.get("processes") or []) or None)
        self.cpu_spark.set(m.hist("cpu"))

        pct = mem.get("percent")
        self.mem_ring.set((pct or 0) / 100, fmt_pct(pct), "used", level_color(pct, 80, 92) if pct else None)
        self.mem_kv.set("In use", fmt_bytes(mem.get("used")))
        self.mem_kv.set("Free", fmt_bytes(mem.get("available")))
        self.mem_kv.set("Total", fmt_bytes(mem.get("total")))
        self.mem_spark.set(m.hist("mem"))

        gpus = snap.get("gpus") or []
        if gpus:
            g = gpus[0]
            self.gpu_card.set_title(g["name"].replace("NVIDIA ", "").replace("AMD ", "")[:28])
            u = g.get("util")
            self.gpu_ring.set((u or 0) / 100, fmt_pct(u), "load", level_color(u, 85, 97))
            gb = 1024 ** 3
            vram = (f"{(g.get('vram_used') or 0) / gb:.1f}/{g['vram_total'] / gb:.0f} GB" if g.get("vram_total")
                    else fmt_bytes(g.get("vram_used")))
            self.gpu_kv.set("VRAM", vram)
            self.gpu_kv.set("Temp", fmt_temp(g.get("temp")), temp_color(g.get("temp"), 78, 86) if g.get("temp") else C["text_muted"])
            self.gpu_kv.set("Power", f"{g['power']:.0f} W" if g.get("power") is not None else "—")
            self.gpu_spark.set(m.hist(f"gpu:{g['key']}"))
        else:
            self.gpu_ring.set(None, "—", "no data")
            self.gpu_kv.set("VRAM", "—")
            self.gpu_kv.set("Temp", "—")
            self.gpu_kv.set("Power", "—")

        net = snap.get("net", {})
        self.net_down.configure(text=f"↓ {fmt_rate(net.get('down'))}")
        self.net_up.configure(text=f"↑ {fmt_rate(net.get('up'))}")
        ping = snap.get("ping") or {}
        if ping:
            ms = ping.get("ms")
            txt = f"Ping {ms:.0f} ms" if ms is not None else "Ping timed out"
            txt += f"  ·  loss {ping.get('loss', 0):.0f}%"
            self.net_ping.configure(text=txt, text_color=C["text_secondary"] if ms is not None else C["amber"])
        else:
            self.net_ping.configure(text="Ping off", text_color=C["text_muted"])
        self.net_spark.set(m.hist("net_down"), m.hist("net_up"))

        self._refresh_drives(snap.get("volumes") or [])
        self._refresh_system(snap)
        self._refresh_procs(snap.get("processes") or [])

        dt = snap.get("disk_total") or {}
        self.disk_rw.configure(text=f"Read {fmt_rate(dt.get('read'))}     Write {fmt_rate(dt.get('write'))}")
        self.disk_spark.set(m.hist("disk_read"), m.hist("disk_write"))
        items = list(m.alerts.items)[:3]
        for i, lbl in enumerate(self.alert_lines):
            if i < len(items):
                a = items[i]
                color = C["red"] if a["level"] == "critical" else C["amber"]
                when = datetime.fromtimestamp(a["time"]).strftime("%H:%M")
                lbl.configure(text=f"●  {when}  {a['title']}", text_color=color)
            elif i == 0:
                lbl.configure(text="✓  No alerts. Everything looks healthy.", text_color=C["green"])
            else:
                lbl.configure(text="")

    def _refresh_drives(self, vols: list[dict]) -> None:
        keys = [v["mount"] for v in vols]
        if keys != self._drive_order:
            for w in self.drives_card.body.winfo_children():
                w.destroy()
            self._drive_rows = {}
            for i, k in enumerate(keys):
                row = UsageRow(self.drives_card.body, k)
                row.grid(row=i // 2, column=i % 2, sticky="ew", padx=(0, 14) if i % 2 == 0 else (0, 0), pady=4)
                self._drive_rows[k] = row
            self._drive_order = keys
        thr = 100 - self.app.settings["drive_free_alert"]
        inv = self.monitor.snapshot().get("inventory", {})
        for v in vols:
            label = self._volume_label(v, inv)
            self._drive_rows[v["mount"]].set(v["percent"] / 100, f"{fmt_bytes(v['free'])} free",
                                             f"{fmt_bytes(v['used'])} of {fmt_bytes(v['total'])} used",
                                             level_color(v["percent"], thr - 10, thr), title=label)

    @staticmethod
    def _volume_label(v: dict, inv: dict) -> str:
        letter = v["mount"][:1].upper()
        for iv in inv.get("volumes", []):
            if iv.get("letter", "").upper() == letter and iv.get("label"):
                return f"{v['mount']}  {iv['label']}"
        return v["mount"]

    def _refresh_system(self, snap: dict) -> None:
        inv = snap.get("inventory", {})
        wv = snap.get("winver", {})
        osd = inv.get("os", {})
        caption = (osd.get("caption") or wv.get("caption") or ("Windows" if sm.IS_WINDOWS else platform.system()))
        self.sys_kv.set("Windows", caption.replace("Microsoft ", ""))
        ver = " ".join(x for x in (wv.get("display_version") or "", f"build {wv['build']}" if wv.get("build") else "") if x)
        self.sys_kv.set("Version", ver or platform.release())
        self.sys_kv.set("Uptime", fmt_duration(snap.get("uptime")))
        plan = next((p["name"] for p in snap.get("power_plans") or [] if p["active"]), None)
        self.sys_kv.set("Power plan", plan)
        bat = snap.get("battery")
        if bat:
            state = "charging" if bat.get("plugged") else (f"{fmt_duration(bat['secsleft'])} left" if bat.get("secsleft") else "on battery")
            self.sys_kv.set("Battery", f"{bat['percent']:.0f}% · {state}", level_color(100 - bat["percent"], 75, 85))
        else:
            self.sys_kv.set("Battery", "Plugged in (no battery)")
        d = inv.get("defender") or {}
        if d:
            ok = d.get("rtp") and d.get("av")
            self.sys_kv.set("Security", "Protected" if ok else "Check Defender", C["green"] if ok else C["amber"])
        elif inv.get("av"):
            self.sys_kv.set("Security", ", ".join(inv["av"])[:30], C["green"])
        else:
            self.sys_kv.set("Security", "—")
        pr = snap.get("pending_reboot")
        self.sys_kv.set("Restart needed", None if pr is None else ("Yes, to finish updates" if pr else "No"),
                        C["amber"] if pr else C["green"] if pr is False else None)
        self.sys_kv.set("Computer", (inv.get("computer") or {}).get("name") or platform.node())

    def _refresh_procs(self, procs: list[dict]) -> None:
        for i, labels in enumerate(self._proc_rows):
            if i < len(procs):
                p = procs[i]
                vals = (p["name"][:34], f"{p['cpu']:.1f}%", fmt_bytes(p["mem"]), fmt_pct(p.get("gpu")) if p.get("gpu") else "")
                for lbl, v in zip(labels, vals):
                    if lbl.cget("text") != v:
                        lbl.configure(text=v)
                labels[1].configure(text_color=level_color(p["cpu"], 25, 60) if p["cpu"] >= 1 else C["text_secondary"])
            else:
                for lbl in labels:
                    lbl.configure(text="")


# ---------------------------------------------------------------------------------------------
class CpuPage(Page):
    title = "CPU & Memory"
    subtitle = "Load per core, clocks, temperatures and memory"

    def build(self) -> None:
        b = self.body
        self.grid4()
        pad = dict(padx=7, pady=7, sticky="nsew")

        self.sum_card = Card(b, "Processor", "cpu")
        self.sum_card.grid(row=0, column=0, columnspan=2, **pad)
        sb = self.sum_card.body
        sb.grid_columnconfigure(1, weight=1)
        self.ring = RingGauge(sb, size=128)
        self.ring.grid(row=0, column=0, padx=(0, 14), sticky="n")
        self.kv = KeyValues(sb, ["Model", "Cores", "Clock now", "Base clock", "Temperature", "Processes",
                                 "Context switches", "Uptime"], key_width=120, wrap=280)
        self.kv.grid(row=0, column=1, sticky="nw")

        self.hist_card = Card(b, "CPU load · last 2 minutes", "chart")
        self.hist_card.grid(row=0, column=2, columnspan=2, **pad)
        self.hist = Sparkline(self.hist_card.body, colors=(C["accent"],), height=170)
        self.hist.pack(fill="both", expand=True)

        self.core_card = Card(b, "Logical processors", "apps2")
        self.core_card.grid(row=1, column=0, columnspan=4, **pad)
        self.cores = CoreGrid(self.core_card.body)
        self.cores.pack(fill="x")

        self.mem_card = Card(b, "Memory", "chart")
        self.mem_card.grid(row=2, column=0, columnspan=2, **pad)
        mb = self.mem_card.body
        mb.grid_columnconfigure(1, weight=1)
        self.mem_ring = RingGauge(mb, size=128)
        self.mem_ring.grid(row=0, column=0, padx=(0, 14), sticky="n")
        self.mem_kv = KeyValues(mb, ["In use", "Available", "Installed", "Page file", "Speed", "Slots used"], key_width=110)
        self.mem_kv.grid(row=0, column=1, sticky="nw")
        self.mem_hist = Sparkline(mb, colors=(C["purple"],), height=70)
        self.mem_hist.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))

        self.mod_card = Card(b, "Memory modules", "list")
        self.mod_card.grid(row=2, column=2, columnspan=2, **pad)
        self._mod_sig = None

        self.sens_card = Card(b, "Sensors", "pulse")
        self.sens_card.grid(row=3, column=0, columnspan=4, **pad)
        self._sens_sig = None
        self._sens_labels: dict[tuple, ctk.CTkLabel] = {}

    def refresh(self, snap: dict) -> None:
        m = self.monitor
        cpu, mem, inv = snap.get("cpu", {}), snap.get("mem", {}), snap.get("inventory", {})
        info = (inv.get("cpu") or [{}])[0] if inv.get("cpu") else {}
        total = cpu.get("total")
        self.ring.set((total or 0) / 100, fmt_pct(total), "load", level_color(total))
        self.kv.set("Model", short_cpu_name(info.get("name", "")) or platform.processor() or "—")
        cores = info.get("cores") or psutil.cpu_count(logical=False)
        threads = info.get("threads") or psutil.cpu_count()
        self.kv.set("Cores", f"{cores} cores · {threads} threads" if cores else f"{threads} threads")
        self.kv.set("Clock now", fmt_mhz(cpu.get("freq")))
        self.kv.set("Base clock", fmt_mhz(info.get("maxmhz") or cpu.get("freq_max")))
        t = cpu.get("temp")
        self.kv.set("Temperature", fmt_temp(t) if t is not None else "needs LibreHardwareMonitor (see Sensors)",
                    temp_color(t) if t is not None else C["text_muted"])
        self.kv.set("Processes", len(snap.get("processes") or []) or None)
        ctx = cpu.get("ctx_rate")
        self.kv.set("Context switches", f"{ctx:,.0f} /s" if ctx is not None else None)
        self.kv.set("Uptime", fmt_duration(snap.get("uptime")))
        self.hist.set(m.hist("cpu"))
        self.cores.set(cpu.get("per_core") or [])

        pct = mem.get("percent")
        self.mem_ring.set((pct or 0) / 100, fmt_pct(pct), "used", level_color(pct, 80, 92) if pct else None)
        self.mem_kv.set("In use", fmt_bytes(mem.get("used")))
        self.mem_kv.set("Available", fmt_bytes(mem.get("available")))
        self.mem_kv.set("Installed", fmt_bytes(mem.get("total")))
        if mem.get("swap_total"):
            self.mem_kv.set("Page file", f"{fmt_bytes(mem.get('swap_used'))} of {fmt_bytes(mem.get('swap_total'))}")
        else:
            self.mem_kv.set("Page file", "—")
        mods = inv.get("ram") or []
        speeds = [x.get("configured") or x.get("speed") for x in mods if (x.get("configured") or x.get("speed"))]
        self.mem_kv.set("Speed", f"{max(speeds)} MT/s" if speeds else None)
        self.mem_kv.set("Slots used", f"{len(mods)}" if mods else None)
        self.mem_hist.set(m.hist("mem"))
        self._refresh_modules(mods)
        self._refresh_sensors(snap)

    def _refresh_modules(self, mods: list[dict]) -> None:
        sig = tuple((x.get("slot"), x.get("size")) for x in mods)
        if sig == self._mod_sig:
            return
        self._mod_sig = sig
        body = self.mod_card.body
        for w in body.winfo_children():
            w.destroy()
        if not mods:
            msg = "Loading hardware details…" if sm.IS_WINDOWS else "Module details are read from Windows."
            ctk.CTkLabel(body, text=msg, text_color=C["text_muted"]).pack(anchor="w")
            return
        body.grid_columnconfigure(0, weight=1)
        for col, h in enumerate(("Slot", "Size", "Speed", "Maker / part")):
            ctk.CTkLabel(body, text=h, text_color=C["text_muted"], font=(UI_FONT, 11), anchor="w").grid(
                row=0, column=col, sticky="w", padx=(0, 14))
        for r, x in enumerate(mods, start=1):
            spd = x.get("configured") or x.get("speed")
            vals = (x.get("slot") or "—", fmt_bytes(x.get("size"), 0), f"{spd} MT/s" if spd else "—",
                    " ".join(v for v in (str(x.get("maker") or "").strip(), x.get("part") or "") if v) or "—")
            for col, v in enumerate(vals):
                ctk.CTkLabel(body, text=v, anchor="w", font=(UI_FONT, 12)).grid(row=r, column=col, sticky="w", padx=(0, 14), pady=1)

    def _refresh_sensors(self, snap: dict) -> None:
        sens = snap.get("sensors") or {}
        temps, fans = sens.get("temps", []), sens.get("fans", [])
        sig = (snap.get("sensor_source"), tuple((t["hw"], t["name"]) for t in temps), tuple((f["hw"], f["name"]) for f in fans))
        body = self.sens_card.body
        if sig != self._sens_sig:
            self._sens_sig = sig
            for w in body.winfo_children():
                w.destroy()
            self._sens_labels = {}
            if not temps and not fans:
                msg = ("Windows doesn't report CPU temperature or fan speeds by itself. Run the free, open-source "
                       "LibreHardwareMonitor in the background and they appear here automatically "
                       "(NVIDIA GPU temperatures already show on the GPU page).")
                ctk.CTkLabel(body, text=msg, wraplength=900, justify="left", text_color=C["text_secondary"]).pack(anchor="w")
                small_button(body, "Get LibreHardwareMonitor", lambda: sm.open_path(
                    "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases"), icon="open").pack(anchor="w", pady=(8, 0))
                return
            self.sens_card.set_title(f"Sensors · {snap.get('sensor_source') or ''}")
            for c in range(4):
                body.grid_columnconfigure(c, weight=1, uniform="s")
            items = [("t", t) for t in temps] + [("f", f) for f in fans]
            for i, (kind, s) in enumerate(items[:48]):
                cell = ctk.CTkFrame(body, fg_color=C["bg_card_alt"], corner_radius=8)
                cell.grid(row=i // 4, column=i % 4, sticky="ew", padx=4, pady=4)
                hw = s["hw"].strip("/").split("/")[0].replace("-", " ").upper()
                ctk.CTkLabel(cell, text=f"{hw} · {s['name']}", text_color=C["text_muted"], font=(UI_FONT, 10),
                             anchor="w").pack(anchor="w", padx=10, pady=(6, 0))
                val = ctk.CTkLabel(cell, text="", font=(UI_FONT_SEMIBOLD, 15), anchor="w")
                val.pack(anchor="w", padx=10, pady=(0, 6))
                self._sens_labels[(kind, s["hw"], s["name"])] = val
        for t in temps:
            lbl = self._sens_labels.get(("t", t["hw"], t["name"]))
            if lbl:
                lbl.configure(text=fmt_temp(t["value"]), text_color=temp_color(t["value"], 70, 85))
        for f in fans:
            lbl = self._sens_labels.get(("f", f["hw"], f["name"]))
            if lbl:
                lbl.configure(text=f"{f['value']:.0f} RPM", text_color=C["sky"])


# ---------------------------------------------------------------------------------------------
class GpuCardView(Card):
    def __init__(self, master, page: "GpuPage", key: str):
        super().__init__(master, "GPU", "gpu")
        self.key = key
        self.page = page
        b = self.body
        b.grid_columnconfigure(0, weight=1)
        top = ctk.CTkFrame(b, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew")
        self.vendor = Pill(top, "", C["accent"])
        self.vendor.pack(side="left")
        self.driver = ctk.CTkLabel(top, text="", text_color=C["text_muted"], font=(UI_FONT, 11))
        self.driver.pack(side="left", padx=10)

        rings = ctk.CTkFrame(b, fg_color="transparent")
        rings.grid(row=1, column=0, sticky="ew", pady=(10, 4))
        for c in range(4):
            rings.grid_columnconfigure(c, weight=1)
        self.r_load = RingGauge(rings, size=118, caption="load")
        self.r_vram = RingGauge(rings, size=118, caption="VRAM")
        self.r_temp = RingGauge(rings, size=118, caption="temp")
        self.r_power = RingGauge(rings, size=118, caption="power")
        for c, r in enumerate((self.r_load, self.r_vram, self.r_temp, self.r_power)):
            r.grid(row=0, column=c, pady=2)

        charts = ctk.CTkFrame(b, fg_color="transparent")
        charts.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        charts.grid_columnconfigure((0, 1), weight=1, uniform="g")
        ctk.CTkLabel(charts, text="Load & VRAM %", text_color=C["text_muted"], font=(UI_FONT, 11)).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(charts, text="Temperature", text_color=C["text_muted"], font=(UI_FONT, 11)).grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.s_load = Sparkline(charts, colors=(C["pink"], C["purple"]), height=90, legend=["load", "vram"])
        self.s_load.grid(row=1, column=0, sticky="ew")
        self.s_temp = Sparkline(charts, colors=(C["amber"],), height=90, max_value=None, fmt=lambda v: f"{v:.0f}°C")
        self.s_temp.grid(row=1, column=1, sticky="ew", padx=(12, 0))

        lower = ctk.CTkFrame(b, fg_color="transparent")
        lower.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        lower.grid_columnconfigure((0, 1), weight=1, uniform="l")
        self.kv = KeyValues(lower, ["Core clock", "Memory clock", "Fan", "Power draw", "Performance state", "PCIe link",
                                    "Shared memory", "Memory bus load", "Data source"], key_width=130)
        self.kv.grid(row=0, column=0, sticky="nw")
        self.eng_frame = ctk.CTkFrame(lower, fg_color="transparent")
        self.eng_frame.grid(row=0, column=1, sticky="new", padx=(14, 0))
        self.eng_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self.eng_frame, text="Engines", text_color=C["text_muted"], font=(UI_FONT, 11)).grid(row=0, column=0, sticky="w")
        self._eng_rows: dict[str, UsageRow] = {}

    def update_from(self, g: dict) -> None:
        m = self.page.monitor
        self.set_title(g["name"])
        vcol = {"NVIDIA": C["green"], "AMD": C["red"], "Intel": C["sky"]}.get(g.get("vendor"), C["accent"])
        self.vendor.set(g.get("vendor") or "GPU", vcol)
        self.driver.configure(text=f"Driver {g['driver']}" if g.get("driver") else "")
        u = g.get("util")
        self.r_load.set((u or 0) / 100, fmt_pct(u), color=level_color(u, 85, 97))
        if g.get("vram_used") is not None and g.get("vram_total"):
            vp = 100 * g["vram_used"] / g["vram_total"]
            self.r_vram.set(vp / 100, fmt_bytes(g["vram_used"]), f"of {fmt_bytes(g['vram_total'], 0)}", level_color(vp, 85, 95))
        else:
            self.r_vram.set(None, fmt_bytes(g.get("vram_used")), "VRAM")
        t = g.get("temp")
        self.r_temp.set((t or 0) / 100, fmt_temp(t), color=temp_color(t, 78, 86))
        p, pl = g.get("power"), g.get("power_limit")
        self.r_power.set((p / pl) if p and pl else None, f"{p:.0f} W" if p is not None else "—",
                         f"of {pl:.0f} W" if pl else "power", level_color(100 * p / pl if p and pl else None, 90, 99))
        vram_hist = m.hist(f"vram:{g['key']}")
        self.s_load.set(m.hist(f"gpu:{g['key']}"), vram_hist)
        self.s_temp.set(m.hist(f"gputemp:{g['key']}"))

        def clock(cur, mx):
            if cur is None:
                return None
            return f"{cur:.0f} MHz" + (f"  (max {mx:.0f})" if mx else "")
        self.kv.set("Core clock", clock(g.get("clock_core"), g.get("clock_core_max")))
        self.kv.set("Memory clock", clock(g.get("clock_mem"), g.get("clock_mem_max")))
        self.kv.set("Fan", fmt_pct(g.get("fan")) if g.get("fan") is not None else None)
        self.kv.set("Power draw", f"{p:.1f} W" + (f" of {pl:.0f} W" if pl else "") if p is not None else None)
        self.kv.set("Performance state", g.get("pstate"))
        self.kv.set("PCIe link", g.get("pcie"))
        self.kv.set("Shared memory", fmt_bytes(g.get("shared_used")) if g.get("shared_used") is not None else None)
        self.kv.set("Memory bus load", fmt_pct(g.get("mem_util")) if g.get("mem_util") is not None else None)
        self.kv.set("Data source", g.get("source"))
        engines = dict(g.get("engines") or {})
        if "counters" in (g.get("source") or ""):
            for name in ("3D", "Copy", "VideoDecode", "VideoEncode"):  # idle engines aren't reported: show them at 0
                engines.setdefault(name, 0.0)
        shown = sorted(engines.items(), key=lambda kv: (-kv[1], kv[0]))[:6]
        for name, row in self._eng_rows.items():
            if name not in dict(shown):
                row.grid_forget()
        for i, (name, val) in enumerate(shown):
            row = self._eng_rows.get(name)
            if row is None:
                row = UsageRow(self.eng_frame, name.replace("_", " "), caption=False, bar_height=6, title_font=(UI_FONT, 11))
                self._eng_rows[name] = row
            row.grid(row=i + 1, column=0, sticky="ew", pady=2)
            row.set(val / 100, fmt_pct(val), color=C["pink"])


class GpuPage(Page):
    title = "GPU"
    subtitle = "Load, VRAM, temperature, power and clocks for every graphics card"

    def build(self) -> None:
        b = self.body
        self.grid4()
        self.cards: dict[str, GpuCardView] = {}
        self.empty = Card(b, "No GPU data yet", "info")
        msg = ("GPU load and VRAM come from Windows' GPU performance counters (Windows 10 1709 or newer). "
               "NVIDIA cards also report temperature, power, fan and clocks via nvidia-smi, installed with the driver. "
               "For AMD/Intel temperatures, run LibreHardwareMonitor.")
        if not sm.IS_WINDOWS:
            msg = "GPU monitoring uses Windows performance counters and nvidia-smi, so it works when this app runs on Windows."
        ctk.CTkLabel(self.empty.body, text=msg, wraplength=900, justify="left", text_color=C["text_secondary"]).pack(anchor="w")
        self.proc_card = Card(b, "Apps using the GPU", "apps")
        self.disp_card = Card(b, "Displays & adapters", "display")
        self._proc_labels: list[list[ctk.CTkLabel]] = []
        pb = self.proc_card.body
        pb.grid_columnconfigure(0, weight=1)
        for r in range(8):
            row = []
            for c in range(3):
                lbl = ctk.CTkLabel(pb, text="", anchor="w" if c == 0 else "e", font=(UI_FONT, 12), width=10 if c == 0 else 80)
                lbl.grid(row=r, column=c, sticky="ew", pady=1)
                row.append(lbl)
            self._proc_labels.append(row)
        self._disp_sig = None
        self._layout_sig = None

    def refresh(self, snap: dict) -> None:
        gpus = snap.get("gpus") or []
        keys = tuple(g["key"] for g in gpus)
        if keys != self._layout_sig:
            self._layout_sig = keys
            for k in list(self.cards):
                if k not in keys:
                    self.cards.pop(k).destroy()
            for w in (self.empty, self.proc_card, self.disp_card):
                w.grid_forget()
            row = 0
            if not gpus:
                self.empty.grid(row=0, column=0, columnspan=4, padx=7, pady=7, sticky="nsew")
                row = 1
            for k in keys:
                if k not in self.cards:
                    self.cards[k] = GpuCardView(self.body, self, k)
                self.cards[k].grid(row=row, column=0, columnspan=4, padx=7, pady=7, sticky="nsew")
                row += 1
            self.proc_card.grid(row=row, column=0, columnspan=2, padx=7, pady=7, sticky="nsew")
            self.disp_card.grid(row=row, column=2, columnspan=2, padx=7, pady=7, sticky="nsew")
        for g in gpus:
            self.cards[g["key"]].update_from(g)

        users = sorted((p for p in snap.get("processes") or [] if p.get("gpu")), key=lambda p: p["gpu"], reverse=True)
        for i, row in enumerate(self._proc_labels):
            if i < len(users):
                p = users[i]
                vals = (p["name"], fmt_pct(p["gpu"], 1), fmt_bytes(p["mem"]))
            elif i == 0:
                vals = ("No app is using the GPU right now" if gpus else "—", "", "")
            else:
                vals = ("", "", "")
            for lbl, v in zip(row, vals):
                if lbl.cget("text") != v:
                    lbl.configure(text=v, text_color=C["text_muted"] if (i == 0 and not users) else C["text_primary"])
        video = (snap.get("inventory") or {}).get("video") or []
        sig = tuple((v.get("name"), v.get("w"), v.get("h"), v.get("hz")) for v in video)
        if sig != self._disp_sig:
            self._disp_sig = sig
            for w in self.disp_card.body.winfo_children():
                w.destroy()
            if not video:
                ctk.CTkLabel(self.disp_card.body, text="Loading…" if sm.IS_WINDOWS else "Windows only",
                             text_color=C["text_muted"]).pack(anchor="w")
            for v in video:
                f = ctk.CTkFrame(self.disp_card.body, fg_color="transparent")
                f.pack(fill="x", pady=4)
                ctk.CTkLabel(f, text=v.get("name") or "Display adapter", font=(UI_FONT_SEMIBOLD, 12), anchor="w").pack(anchor="w")
                parts = []
                if v.get("w") and v.get("h"):
                    parts.append(f"{v['w']}×{v['h']}" + (f" @ {v['hz']} Hz" if v.get("hz") else ""))
                if v.get("driver"):
                    parts.append(f"driver {v['driver']}")
                if v.get("driver_date"):
                    parts.append(f"from {sm.fmt_date(v['driver_date'])}")
                ctk.CTkLabel(f, text="  ·  ".join(parts) or "—", text_color=C["text_muted"], anchor="w",
                             font=(UI_FONT, 11)).pack(anchor="w")


# ---------------------------------------------------------------------------------------------
class DrivesPage(Page):
    title = "Drives"
    subtitle = "Space, health, temperatures and live activity"

    def build(self) -> None:
        b = self.body
        self.grid4()
        self.vol_header = ctk.CTkLabel(b, text="VOLUMES", font=(UI_FONT_SEMIBOLD, 11), text_color=C["text_muted"])
        self.vol_frame = ctk.CTkFrame(b, fg_color="transparent", width=1, height=1)
        self.vol_frame.grid_columnconfigure((0, 1), weight=1, uniform="v")
        self.disk_header = ctk.CTkLabel(b, text="PHYSICAL DISKS", font=(UI_FONT_SEMIBOLD, 11), text_color=C["text_muted"])
        self.disk_frame = ctk.CTkFrame(b, fg_color="transparent", width=1, height=1)
        self.disk_frame.grid_columnconfigure((0, 1), weight=1, uniform="p")
        self.vol_header.grid(row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(4, 0))
        self.vol_frame.grid(row=1, column=0, columnspan=4, sticky="ew")
        self.disk_header.grid(row=2, column=0, columnspan=4, sticky="w", padx=10, pady=(10, 0))
        self.disk_frame.grid(row=3, column=0, columnspan=4, sticky="ew")

        self.clean_card = Card(b, "Free up space", "clean")
        self.clean_card.grid(row=4, column=0, columnspan=4, padx=7, pady=7, sticky="nsew")
        cb = self.clean_card.body
        cb.grid_columnconfigure((0, 1, 2), weight=1, uniform="c")
        self.rb_label = self._clean_block(cb, 0, "Recycle Bin", "Empty", self._empty_bin)
        self.tmp_label = self._clean_block(cb, 1, "Temporary files (older than 1 day)", "Scan", self._scan_temp)
        self.tmp_btn = self._last_btn
        other = ctk.CTkFrame(cb, fg_color="transparent")
        other.grid(row=0, column=2, sticky="nw")
        ctk.CTkLabel(other, text="Windows tools", font=(UI_FONT_SEMIBOLD, 12)).pack(anchor="w")
        btns = ctk.CTkFrame(other, fg_color="transparent")
        btns.pack(anchor="w", pady=(6, 0))
        small_button(btns, "Storage settings", lambda: self.app.launch("ms-settings:storagesense")).pack(side="left", padx=(0, 6))
        small_button(btns, "Disk Cleanup", lambda: self.app.launch("cleanmgr")).pack(side="left")
        self._vol_cards: dict[str, dict] = {}
        self._vol_sig = None
        self._disk_cards: dict[str, dict] = {}
        self._disk_sig = None
        self._temp_scan: dict | None = None

    def _clean_block(self, parent, col: int, title: str, btn: str, cmd):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.grid(row=0, column=col, sticky="nw")
        ctk.CTkLabel(f, text=title, font=(UI_FONT_SEMIBOLD, 12)).pack(anchor="w")
        lbl = ctk.CTkLabel(f, text="—", text_color=C["text_secondary"])
        lbl.pack(anchor="w")
        self._last_btn = small_button(f, btn, cmd, width=90)
        self._last_btn.pack(anchor="w", pady=(6, 0))
        return lbl

    def on_show(self) -> None:
        self._update_bin()

    def _update_bin(self) -> None:
        def done(info):
            if info is None:
                self.rb_label.configure(text="Windows only" if not sm.IS_WINDOWS else "—")
            else:
                self.rb_label.configure(text=f"{fmt_bytes(info[0])} in {info[1]:,} items")
        self.app.run_bg(sm.recycle_bin_info, done)

    def _empty_bin(self) -> None:
        if not messagebox.askyesno(APP_NAME, "Permanently delete everything in the Recycle Bin?", parent=self.app):
            return
        self.app.run_action("Empty Recycle Bin", sm.empty_recycle_bin, after=self._update_bin)

    def _scan_temp(self) -> None:
        if self._temp_scan is not None:
            res = self._temp_scan
            if not messagebox.askyesno(APP_NAME, f"Delete {res['files']:,} temporary files ({fmt_bytes(res['bytes'])})?\n\n"
                                       "Only files older than a day are removed; files in use are skipped.", parent=self.app):
                return
            self._temp_scan = None
            self.tmp_btn.configure(text="Scan")
            self.tmp_label.configure(text="Cleaning…")

            def clean():
                r = sm.clean_temp_files(dry_run=False)
                msg = f"Freed {fmt_bytes(r['bytes'])} ({r['files']:,} files)" + (f", {r['failed']} in use and skipped" if r["failed"] else "")
                return True, msg
            self.app.run_action("Clean temp files", clean, after=lambda: self.tmp_label.configure(text="Cleaned. Scan again any time."))
            return
        self.tmp_label.configure(text="Scanning…")

        def done(res):
            self._temp_scan = res
            self.tmp_label.configure(text=f"{fmt_bytes(res['bytes'])} in {res['files']:,} files can be removed")
            self.tmp_btn.configure(text="Clean" if res["files"] else "Scan")
            if not res["files"]:
                self._temp_scan = None
        self.app.run_bg(lambda: sm.clean_temp_files(dry_run=True), done)

    def refresh(self, snap: dict) -> None:
        self._refresh_volumes(snap)
        self._refresh_disks(snap)

    def _refresh_volumes(self, snap: dict) -> None:
        vols = snap.get("volumes") or []
        inv = snap.get("inventory") or {}
        sig = tuple(v["mount"] for v in vols)
        if sig != self._vol_sig:
            self._vol_sig = sig
            for w in self.vol_frame.winfo_children():
                w.destroy()
            self._vol_cards = {}
            for i, v in enumerate(vols):
                card = Card(self.vol_frame, v["mount"], "drive")
                card.grid(row=i // 2, column=i % 2, padx=7, pady=7, sticky="nsew")
                small_button(card.actions, "Analyze", lambda p=v["path"]: SpaceAnalyzer(self.app, p), icon="search",
                             width=80).pack(side="right", padx=(6, 0))
                small_button(card.actions, "Open", lambda p=v["path"]: self.app.launch(p), icon="folder",
                             width=64).pack(side="right")
                row = UsageRow(card.body, "", bar_height=10)
                row.pack(fill="x")
                kv = KeyValues(card.body, ["File system", "Physical disk", "Health"], key_width=110)
                kv.pack(fill="x", pady=(6, 0))
                self._vol_cards[v["mount"]] = {"card": card, "row": row, "kv": kv}
        thr = 100 - self.app.settings["drive_free_alert"]
        disks = {d.get("id"): d for d in inv.get("disks", [])}
        for v in vols:
            w = self._vol_cards.get(v["mount"])
            if not w:
                continue
            letter = v["mount"][:1].upper()
            iv = next((x for x in inv.get("volumes", []) if x.get("letter", "").upper() == letter), {})
            title = f"{v['mount']}  {iv['label']}" if iv.get("label") else v["mount"]
            if v.get("removable"):
                title += "  (removable)"
            w["card"].set_title(title)
            w["row"].set(v["percent"] / 100, f"{fmt_bytes(v['free'])} free of {fmt_bytes(v['total'])}",
                         f"{v['percent']:.0f}% used", level_color(v["percent"], thr - 10, thr), title=f"{fmt_bytes(v['used'])} used")
            w["kv"].set("File system", iv.get("fs") or v.get("fstype"))
            dn = self.monitor.disk_number_for_volume(v["mount"])
            d = disks.get(dn)
            w["kv"].set("Physical disk", f"{d.get('name')} (disk {dn})" if d else None)
            h = iv.get("health_name")
            w["kv"].set("Health", h, C["green"] if h == "Healthy" else C["amber"] if h else None)

    def _refresh_disks(self, snap: dict) -> None:
        inv = snap.get("inventory") or {}
        io = snap.get("disk_io") or {}
        disks = list(inv.get("disks") or [])
        if not disks:  # no Windows inventory: one card per disk psutil reports activity for
            disks = [{"id": k, "name": k, "_io": k} for k in sorted(io)]
        sig = tuple(d.get("id") for d in disks)
        if sig != self._disk_sig:
            self._disk_sig = sig
            for w in self.disk_frame.winfo_children():
                w.destroy()
            self._disk_cards = {}
            for i, d in enumerate(disks):
                card = Card(self.disk_frame, d.get("name") or f"Disk {d.get('id')}", "drive")
                card.grid(row=i // 2, column=i % 2, padx=7, pady=7, sticky="nsew")
                pills = ctk.CTkFrame(card.body, fg_color="transparent")
                pills.pack(fill="x")
                p_media, p_bus, p_health = Pill(pills), Pill(pills), Pill(pills)
                for p in (p_media, p_bus, p_health):
                    p.pack(side="left", padx=(0, 6))
                rates = ctk.CTkLabel(card.body, text="", font=(UI_FONT, 12), text_color=C["text_secondary"], anchor="w")
                rates.pack(fill="x", pady=(8, 0))
                spark = Sparkline(card.body, colors=(C["green"],), height=46)
                spark.pack(fill="x", pady=(4, 6))
                kv = KeyValues(card.body, ["Size", "Temperature", "Wear", "Power-on time", "Errors"], columns=2, key_width=96)
                kv.pack(fill="x")
                self._disk_cards[str(d.get("id"))] = {"media": p_media, "bus": p_bus, "health": p_health, "rates": rates,
                                                      "spark": spark, "kv": kv}
        for d in disks:
            w = self._disk_cards.get(str(d.get("id")))
            if not w:
                continue
            io_key = d.get("_io") or f"PhysicalDrive{d.get('id')}"
            r = io.get(io_key) or {}
            busy = r.get("busy")
            w["rates"].configure(text=f"Read {fmt_rate(r.get('read'))}   ·   Write {fmt_rate(r.get('write'))}   ·   "
                                      f"Active {fmt_pct(busy)}")
            w["spark"].set(self.monitor.hist(f"disk:{io_key}:busy"))
            if d.get("media_name"):
                w["media"].set(d["media_name"], C["sky"])
                w["bus"].set(d.get("bus_name") or "—", C["purple"])
                h = d.get("health_name") or "Unknown"
                w["health"].set(h, C["green"] if h == "Healthy" else C["red"] if h == "Unhealthy" else C["amber"])
            temp = sm.safe_num(d.get("temp"))
            w["kv"].set("Size", fmt_bytes(d.get("size"), 0) if d.get("size") else None)
            admin_hint = "run as admin" if sm.IS_WINDOWS and not snap.get("admin") and d.get("media_name") else None
            w["kv"].set("Temperature", fmt_temp(temp) if temp else admin_hint,
                        temp_color(temp, 50, self.app.settings["disk_temp_alert"]) if temp else C["text_muted"])
            wear = sm.safe_num(d.get("wear"))
            w["kv"].set("Wear", f"{wear:.0f}% used" if wear is not None else (admin_hint if d.get("media_name") == "SSD" else None),
                        level_color(wear, 70, 90) if wear is not None else C["text_muted"])
            hours = sm.safe_num(d.get("hours"))
            w["kv"].set("Power-on time", f"{hours:,.0f} h ({hours / 24 / 365:.1f} y)" if hours else None)
            errs = [sm.safe_num(d.get("read_errors")), sm.safe_num(d.get("write_errors"))]
            if any(e is not None for e in errs):
                n = int(sum(e or 0 for e in errs))
                w["kv"].set("Errors", f"{n:,}", C["red"] if n else C["green"])


class SpaceAnalyzer(ctk.CTkToplevel):
    """Finds what's using space in a folder: sizes of its subfolders plus the largest files."""

    def __init__(self, app: "CommandCenterApp", path: str):
        super().__init__(app)
        self.app = app
        self.title("Space analyzer")
        self.geometry("980x640")
        self.minsize(720, 480)
        apply_window_chrome(self)
        self.transient(app)
        self.cancel = threading.Event()
        self.history: list[str] = []
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        bar = ctk.CTkFrame(self, fg_color=C["bg_panel"], corner_radius=0)
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_columnconfigure(2, weight=1)
        self.back_btn = small_button(bar, "Up", self._up, width=50)
        self.back_btn.grid(row=0, column=0, padx=(12, 6), pady=10)
        small_button(bar, "Browse…", self._browse, width=80).grid(row=0, column=1, padx=(0, 6))
        self.path_var = tk.StringVar(value=path)
        ctk.CTkEntry(bar, textvariable=self.path_var).grid(row=0, column=2, sticky="ew", padx=(0, 6))
        self.scan_btn = small_button(bar, "Scan", self._scan, accent=True, width=80)
        self.scan_btn.grid(row=0, column=3, padx=(0, 12))
        self.status = ctk.CTkLabel(self, text="", text_color=C["text_secondary"], anchor="w")
        self.status.grid(row=1, column=0, sticky="ew", padx=14, pady=(8, 2))

        panes = ctk.CTkFrame(self, fg_color="transparent")
        panes.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        panes.grid_columnconfigure((0, 1), weight=1, uniform="p")
        panes.grid_rowconfigure(1, weight=1)
        style = style_treeview(self)
        ctk.CTkLabel(panes, text="FOLDERS  (double-click to open)", font=(UI_FONT_SEMIBOLD, 11), text_color=C["text_muted"]).grid(
            row=0, column=0, sticky="w", padx=4)
        ctk.CTkLabel(panes, text="LARGEST FILES  (double-click to show in Explorer)", font=(UI_FONT_SEMIBOLD, 11),
                     text_color=C["text_muted"]).grid(row=0, column=1, sticky="w", padx=4)
        self.folders = ttk.Treeview(panes, columns=("size", "share", "files"), style=style, selectmode="browse")
        self.folders.heading("#0", text="Name", anchor="w")
        self.folders.heading("size", text="Size", anchor="e")
        self.folders.heading("share", text="Share", anchor="w")
        self.folders.heading("files", text="Files", anchor="e")
        self.folders.column("#0", width=220, anchor="w")
        self.folders.column("size", width=80, anchor="e", stretch=False)
        self.folders.column("share", width=110, anchor="w", stretch=False)
        self.folders.column("files", width=70, anchor="e", stretch=False)
        self.folders.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        self.folders.bind("<Double-1>", self._open_folder)
        self.files = ttk.Treeview(panes, columns=("size",), style=style, selectmode="browse")
        self.files.heading("#0", text="File", anchor="w")
        self.files.heading("size", text="Size", anchor="e")
        self.files.column("#0", width=320, anchor="w")
        self.files.column("size", width=90, anchor="e", stretch=False)
        self.files.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        self.files.bind("<Double-1>", self._reveal_file)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(150, self._scan)

    def _close(self) -> None:
        self.cancel.set()
        self.destroy()

    def _browse(self) -> None:
        d = filedialog.askdirectory(parent=self, initialdir=self.path_var.get())
        if d:
            self.path_var.set(d)
            self._scan()

    def _up(self) -> None:
        cur = Path(self.path_var.get())
        if cur.parent != cur:
            self.path_var.set(str(cur.parent))
            self._scan()

    def _open_folder(self, _e=None) -> None:
        sel = self.folders.selection()
        if sel and os.path.isdir(sel[0]):
            self.path_var.set(sel[0])
            self._scan()

    def _reveal_file(self, _e=None) -> None:
        sel = self.files.selection()
        if sel:
            sm.reveal_in_explorer(sel[0])

    def _scan(self) -> None:
        self.cancel.set()
        self.cancel = threading.Event()
        cancel = self.cancel
        root = self.path_var.get()
        self.status.configure(text=f"Scanning {root} …")
        self.scan_btn.configure(state="disabled")
        q: queue.Queue = queue.Queue()

        def progress(files, total, current):
            q.put(("p", files, total, current))

        def work():
            q.put(("done", sm.scan_folder(root, progress, cancel)))

        threading.Thread(target=work, daemon=True).start()

        def poll():
            if cancel.is_set() and cancel is not self.cancel:
                return
            try:
                while True:
                    item = q.get_nowait()
                    if item[0] == "p":
                        self.status.configure(text=f"Scanning… {item[1]:,} files, {fmt_bytes(item[2])}   {str(item[3])[-70:]}")
                    else:
                        self._show(item[1])
                        return
            except queue.Empty:
                pass
            if self.winfo_exists():
                self.after(150, poll)
        poll()

    def _show(self, res: dict) -> None:
        self.scan_btn.configure(state="normal")
        if res.get("error"):
            self.status.configure(text=f"Can't read this folder: {res['error']}", text_color=C["red"])
            return
        self.status.configure(text=f"{fmt_bytes(res['total'])} in {res['count']:,} files", text_color=C["text_secondary"])
        self.folders.delete(*self.folders.get_children())
        self.files.delete(*self.files.get_children())
        total = res["total"] or 1
        for path, size, count in res["children"][:300]:
            share = size / total
            bar = "█" * int(round(share * 12)) + "░" * (12 - int(round(share * 12)))
            name = os.path.basename(path) or path
            try:
                self.folders.insert("", "end", iid=path, text=name, values=(fmt_bytes(size), f"{bar} {share * 100:.0f}%", f"{count:,}"))
            except tk.TclError:
                pass
        for size, path in res["files"]:
            try:
                self.files.insert("", "end", iid=path, text=path, values=(fmt_bytes(size),))
            except tk.TclError:
                pass


# ---------------------------------------------------------------------------------------------
class NetworkPage(Page):
    title = "Network"
    subtitle = "Throughput, latency and adapters"

    def build(self) -> None:
        b = self.body
        self.grid4()
        pad = dict(padx=7, pady=7, sticky="nsew")
        self.tp_card = Card(b, "Throughput", "network")
        self.tp_card.grid(row=0, column=0, columnspan=4, **pad)
        tb = self.tp_card.body
        tb.grid_columnconfigure(3, weight=1)
        self.down = ctk.CTkLabel(tb, text="↓ —", font=(UI_FONT_SEMIBOLD, 26), text_color=C["sky"])
        self.down.grid(row=0, column=0, sticky="w", padx=(0, 30))
        self.up = ctk.CTkLabel(tb, text="↑ —", font=(UI_FONT_SEMIBOLD, 26), text_color=C["purple"])
        self.up.grid(row=0, column=1, sticky="w", padx=(0, 30))
        self.session = ctk.CTkLabel(tb, text="", text_color=C["text_secondary"], justify="left")
        self.session.grid(row=0, column=2, sticky="w")
        self.tp_spark = Sparkline(tb, colors=(C["sky"], C["purple"]), height=150, max_value=None, fmt=fmt_rate,
                                  legend=["download", "upload"])
        self.tp_spark.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(10, 0))

        self.ping_card = Card(b, "Latency", "pulse")
        self.ping_card.grid(row=1, column=0, columnspan=2, **pad)
        self.ping_kv = KeyValues(self.ping_card.body, ["Now", "Average", "Packet loss", "Target"], columns=2, key_width=90)
        self.ping_kv.pack(fill="x")
        self.ping_spark = Sparkline(self.ping_card.body, colors=(C["amber"],), height=80, max_value=None, fmt=lambda v: f"{v:.0f} ms")
        self.ping_spark.pack(fill="x", pady=(8, 0))

        self.conn_card = Card(b, "Connection", "tower")
        self.conn_card.grid(row=1, column=2, columnspan=2, **pad)
        self.conn_kv = KeyValues(self.conn_card.body, ["Computer name", "Wi-Fi network", "Signal", "Wi-Fi link",
                                                       "Open connections"], key_width=130)
        self.conn_kv.pack(fill="x")
        btns = ctk.CTkFrame(self.conn_card.body, fg_color="transparent")
        btns.pack(anchor="w", pady=(10, 0))
        small_button(btns, "Flush DNS", lambda: self.app.run_action("Flush DNS", sm.flush_dns)).pack(side="left", padx=(0, 6))
        small_button(btns, "Network settings", lambda: self.app.launch("ms-settings:network-status")).pack(side="left", padx=(0, 6))
        small_button(btns, "Adapters", lambda: self.app.launch("ncpa.cpl")).pack(side="left")

        self.ad_card = Card(b, "Adapters", "devices")
        self.ad_card.grid(row=2, column=0, columnspan=4, **pad)
        self.show_all = tk.BooleanVar(value=False)
        ctk.CTkSwitch(self.ad_card.actions, text="Show disconnected", variable=self.show_all,
                      command=lambda: setattr(self, "_ad_sig", None), font=(UI_FONT, 11)).pack(side="right")
        self._ad_sig = None
        self._ad_rates: dict[str, ctk.CTkLabel] = {}
        self._ad_last = 0.0

    def refresh(self, snap: dict) -> None:
        m = self.monitor
        net = snap.get("net") or {}
        self.down.configure(text=f"↓ {fmt_rate(net.get('down'))}")
        self.up.configure(text=f"↑ {fmt_rate(net.get('up'))}")
        try:
            io = psutil.net_io_counters()
            boot = f"Since Windows started:  ↓ {fmt_bytes(io.bytes_recv)}   ↑ {fmt_bytes(io.bytes_sent)}"
        except Exception:
            boot = ""
        self.session.configure(text=f"Since this app started:  ↓ {fmt_bytes(net.get('session_down'))}   "
                                    f"↑ {fmt_bytes(net.get('session_up'))}\n{boot}")
        self.tp_spark.set(m.hist("net_down"), m.hist("net_up"))

        ping = snap.get("ping") or {}
        if ping:
            ms = ping.get("ms")
            self.ping_kv.set("Now", f"{ms:.0f} ms" if ms is not None else "timed out",
                             C["green"] if ms is not None and ms < 60 else C["amber"] if ms is not None else C["red"])
            self.ping_kv.set("Average", f"{ping['avg']:.0f} ms" if ping.get("avg") is not None else None)
            loss = ping.get("loss", 0)
            self.ping_kv.set("Packet loss", f"{loss:.0f}%", C["green"] if loss == 0 else C["amber"] if loss < 20 else C["red"])
            self.ping_kv.set("Target", ping.get("host"))
        else:
            for k in ("Now", "Average", "Packet loss"):
                self.ping_kv.set(k, None)
            self.ping_kv.set("Target", "off (Settings)")
        self.ping_spark.set(m.hist("ping"))

        wifi = snap.get("wifi") or {}
        self.conn_kv.set("Computer name", socket.gethostname())
        self.conn_kv.set("Wi-Fi network", wifi.get("ssid") or ("Not on Wi-Fi" if sm.IS_WINDOWS else None))
        sig = wifi.get("signal")
        self.conn_kv.set("Signal", f"{sig:.0f}%" if sig is not None else None, level_color(100 - sig, 50, 70) if sig is not None else None)
        link = " · ".join(x for x in (wifi.get("radio"), f"{wifi['rx_rate']:.0f} Mbps" if wifi.get("rx_rate") else "") if x)
        self.conn_kv.set("Wi-Fi link", link or None)
        self.conn_kv.set("Open connections", snap.get("connections"))
        self._refresh_adapters(net.get("nics") or {})

    def _refresh_adapters(self, rates: dict) -> None:
        now = time.monotonic()
        if now - self._ad_last > 5 or self._ad_sig is None:
            self._ad_last = now
            try:
                stats = psutil.net_if_stats()
                addrs = psutil.net_if_addrs()
            except Exception:
                stats, addrs = {}, {}
            names = sorted(n for n in stats if (self.show_all.get() or stats[n].isup) and "loopback" not in n.lower() and n != "lo")
            sig = tuple((n, stats[n].isup, stats[n].speed, tuple(a.address for a in addrs.get(n, []))) for n in names)
            if sig != self._ad_sig:
                self._ad_sig = sig
                body = self.ad_card.body
                for w in body.winfo_children():
                    w.destroy()
                self._ad_rates = {}
                body.grid_columnconfigure(0, weight=1)
                for col, h in enumerate(("Adapter", "Status", "Link speed", "IPv4", "MAC", "Now")):
                    ctk.CTkLabel(body, text=h, text_color=C["text_muted"], font=(UI_FONT, 11), anchor="w").grid(
                        row=0, column=col, sticky="w", padx=(0, 16))
                for r, n in enumerate(names, start=1):
                    st = stats[n]
                    ipv4 = ", ".join(a.address for a in addrs.get(n, []) if a.family == socket.AF_INET) or "—"
                    mac = next((a.address for a in addrs.get(n, []) if a.family == psutil.AF_LINK), "—")
                    ctk.CTkLabel(body, text=n, anchor="w", font=(UI_FONT_SEMIBOLD, 12)).grid(row=r, column=0, sticky="w", pady=3)
                    p = Pill(body)
                    p.set("Connected" if st.isup else "Down", C["green"] if st.isup else C["text_muted"])
                    p.grid(row=r, column=1, sticky="w", padx=(0, 16))
                    speed = f"{st.speed / 1000:g} Gbps" if st.speed >= 1000 else (f"{st.speed} Mbps" if st.speed else "—")
                    for col, v in ((2, speed), (3, ipv4), (4, mac)):
                        ctk.CTkLabel(body, text=v, anchor="w", font=(UI_FONT, 12)).grid(row=r, column=col, sticky="w", padx=(0, 16))
                    lbl = ctk.CTkLabel(body, text="", anchor="w", font=(UI_FONT, 12), text_color=C["text_secondary"])
                    lbl.grid(row=r, column=5, sticky="w")
                    self._ad_rates[n] = lbl
                if not names:
                    ctk.CTkLabel(body, text="No connected adapters", text_color=C["text_muted"]).grid(row=1, column=0, sticky="w")
        for n, lbl in self._ad_rates.items():
            r = rates.get(n)
            lbl.configure(text=f"↓ {fmt_rate(r['down'])}   ↑ {fmt_rate(r['up'])}" if r else "")


# ---------------------------------------------------------------------------------------------
class ProcessesPage(Page):
    title = "Processes"
    subtitle = "What's running, and what it's using"
    scroll = False
    COLS = [("name", "Name", 240, "w"), ("pid", "PID", 70, "e"), ("cpu", "CPU", 70, "e"), ("mem", "Memory", 90, "e"),
            ("gpu", "GPU", 64, "e"), ("io", "Disk", 90, "e"), ("threads", "Threads", 70, "e"), ("user", "User", 120, "w")]

    def build(self) -> None:
        b = self.body
        b.grid_columnconfigure(0, weight=1)
        b.grid_rowconfigure(1, weight=1)
        bar = ctk.CTkFrame(b, fg_color=C["bg_card"], corner_radius=12, border_width=1, border_color=C["border"])
        bar.grid(row=0, column=0, sticky="ew", padx=7, pady=(4, 8))
        bar.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(bar, text=icon_text("search"), font=icon_font(14), text_color=C["text_muted"]).grid(row=0, column=0, padx=(14, 6), pady=10)
        self.search_entry = ctk.CTkEntry(bar, placeholder_text="Filter by name, PID or user")
        self.search_entry.grid(row=0, column=1, sticky="ew")
        self.search_entry.bind("<KeyRelease>", lambda _e: self._force())
        self.group = tk.BooleanVar(value=False)
        ctk.CTkSwitch(bar, text="Group by app", variable=self.group, command=self._force).grid(row=0, column=2, padx=14)
        self.count = ctk.CTkLabel(bar, text="", text_color=C["text_muted"])
        self.count.grid(row=0, column=3, padx=(0, 10))
        small_button(bar, "Open location", self._open_location, icon="folder").grid(row=0, column=4, padx=(0, 6))
        self.end_btn = ctk.CTkButton(bar, text="End task", command=self._end_task, height=30, width=90,
                                     fg_color=tint(C["red"], 0.35), hover_color=tint(C["red"], 0.55))
        self.end_btn.grid(row=0, column=5, padx=(0, 12))

        wrap = ctk.CTkFrame(b, fg_color=C["bg_card"], corner_radius=12, border_width=1, border_color=C["border"])
        wrap.grid(row=1, column=0, sticky="nsew", padx=7, pady=(0, 6))
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_rowconfigure(0, weight=1)
        style = style_treeview(self.app)
        self.tree = ttk.Treeview(wrap, columns=[c[0] for c in self.COLS[1:]], style=style, selectmode="browse")
        self.tree.heading("#0", text="Name", anchor="w", command=lambda: self._sort_by("name"))
        self.tree.column("#0", width=scaled(self, 240), anchor="w")
        for key, label, width, anchor in self.COLS[1:]:
            self.tree.heading(key, text=label, anchor=anchor, command=lambda k=key: self._sort_by(k))
            self.tree.column(key, width=scaled(self, width), anchor=anchor, stretch=key == "user")
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        sb = ctk.CTkScrollbar(wrap, command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns", pady=8, padx=(0, 4))
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.tag_configure("hot", foreground=C["amber"])
        self.tree.tag_configure("veryhot", foreground=C["red"])
        self.tree.bind("<Button-3>", self._context_menu)
        self.tree.bind("<Delete>", lambda _e: self._end_task())
        self.menu = tk.Menu(self, tearoff=0, bg=C["bg_card_alt"], fg=C["text_primary"], activebackground=C["border"],
                            activeforeground=C["text_primary"], bd=0)
        self.menu.add_command(label="End task", command=self._end_task)
        self.menu.add_command(label="Open file location", command=self._open_location)
        self.menu.add_command(label="Search the web for this name", command=self._search_web)
        self.menu.add_separator()
        self.menu.add_command(label="Copy name", command=self._copy_name)
        self.sort_key, self.sort_desc = "cpu", True
        self._last_procs: list[dict] = []
        self._last_render = 0.0
        self._update_headings()

    def _force(self) -> None:
        self._last_render = 0.0
        self._render(self._last_procs)

    def _sort_by(self, key: str) -> None:
        if self.sort_key == key:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_key, self.sort_desc = key, key not in ("name", "user", "pid")
        self._update_headings()
        self._force()

    def _update_headings(self) -> None:
        arrow = " ▼" if self.sort_desc else " ▲"
        self.tree.heading("#0", text="Name" + (arrow if self.sort_key == "name" else ""))
        for key, label, _w, _a in self.COLS[1:]:
            self.tree.heading(key, text=label + (arrow if self.sort_key == key else ""))

    def refresh(self, snap: dict) -> None:
        procs = snap.get("processes") or []
        if procs is self._last_procs:
            return
        self._last_procs = procs
        self._render(procs)

    def _rows(self, procs: list[dict]) -> list[dict]:
        q = self.search_entry.get().strip().lower()
        if q:
            procs = [p for p in procs if q in p["name"].lower() or q == str(p["pid"]) or q in (p.get("user") or "").lower()]
        if self.group.get():
            groups: dict[str, dict] = {}
            for p in procs:
                g = groups.get(p["name"])
                if g is None:
                    g = groups[p["name"]] = {"iid": f"g:{p['name']}", "name": p["name"], "pid": 0, "cpu": 0.0, "mem": 0,
                                             "gpu": None, "io": None, "threads": 0, "user": p.get("user") or "", "pids": []}
                g["pid"] += 1
                g["pids"].append(p["pid"])
                g["cpu"] += p["cpu"]
                g["mem"] += p["mem"]
                g["threads"] += p["threads"]
                if p.get("gpu") is not None:
                    g["gpu"] = max(g["gpu"] or 0, p["gpu"])
                if p.get("io") is not None:
                    g["io"] = (g["io"] or 0) + p["io"]
            rows = list(groups.values())
        else:
            rows = [dict(p, iid=str(p["pid"])) for p in procs]
        key = self.sort_key
        if key in ("name", "user"):
            rows.sort(key=lambda r: (r.get(key) or "").lower(), reverse=self.sort_desc)
        else:
            rows.sort(key=lambda r: r.get(key) or 0, reverse=self.sort_desc)
        return rows

    def _render(self, procs: list[dict]) -> None:
        self._last_render = time.monotonic()
        rows = self._rows(procs)
        grouped = self.group.get()
        existing = set(self.tree.get_children())
        wanted = []
        for r in rows:
            iid = r["iid"]
            wanted.append(iid)
            vals = (f"{r['pid']} procs" if grouped and r["pid"] > 1 else (str(r["pids"][0]) if grouped else str(r["pid"])),
                    f"{r['cpu']:.1f}%", fmt_bytes(r["mem"]), fmt_pct(r["gpu"]) if r.get("gpu") else "",
                    fmt_rate(r["io"]) if r.get("io") else "", str(r["threads"]), r.get("user") or "")
            tag = ("veryhot",) if r["cpu"] >= 50 else ("hot",) if r["cpu"] >= 15 else ()
            if iid in existing:
                self.tree.item(iid, text=r["name"], values=vals, tags=tag)
            else:
                self.tree.insert("", "end", iid=iid, text=r["name"], values=vals, tags=tag)
        stale = existing - set(wanted)
        if stale:
            self.tree.delete(*stale)
        if list(self.tree.get_children()) != wanted:
            for i, iid in enumerate(wanted):
                self.tree.move(iid, "", i)
        total = len(procs)
        self.count.configure(text=f"{len(rows)} {'apps' if grouped else 'processes'}" + (f" of {total}" if len(rows) != total and not grouped else ""))

    def _selected(self) -> tuple[str, list[int]] | None:
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        name = self.tree.item(iid, "text")
        if iid.startswith("g:"):
            pids = [p["pid"] for p in self._last_procs if p["name"] == iid[2:]]
        else:
            pids = [int(iid)]
        return name, pids

    def _context_menu(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
            self.menu.tk_popup(event.x_root, event.y_root)

    def _end_task(self) -> None:
        s = self._selected()
        if not s:
            self.app.notify("Select a process first.", "warning")
            return
        name, pids = s
        what = f"{name} ({len(pids)} processes)" if len(pids) > 1 else f"{name} (PID {pids[0]})"
        if not messagebox.askyesno(APP_NAME, f"End {what}?\n\nUnsaved work in it will be lost.", parent=self.app):
            return

        def work():
            results = [sm.kill_process(pid) for pid in pids]
            ok = all(r[0] for r in results)
            return ok, results[0][1] if len(results) == 1 else (f"Ended {name}" if ok else next(r[1] for r in results if not r[0]))
        self.app.run_action("End task", work)

    def _open_location(self) -> None:
        s = self._selected()
        if not s:
            return

        def done(exe):
            if exe:
                sm.reveal_in_explorer(exe)
            else:
                self.app.notify("Windows doesn't allow reading that process's location (try running as admin).", "warning")
        self.app.run_bg(lambda: sm.process_exe(s[1][0]), done)

    def _search_web(self) -> None:
        s = self._selected()
        if s:
            import urllib.parse
            sm.open_path("https://www.bing.com/search?q=" + urllib.parse.quote(f'"{s[0]}" process'))

    def _copy_name(self) -> None:
        s = self._selected()
        if s:
            self.clipboard_clear()
            self.clipboard_append(s[0])


# ---------------------------------------------------------------------------------------------
class WindowsPage(Page):
    title = "Windows"
    subtitle = "Version, security, updates, power and startup apps"

    def build(self) -> None:
        b = self.body
        self.grid4()
        pad = dict(padx=7, pady=7, sticky="nsew")
        self.pc_card = Card(b, "This PC", "windows")
        self.pc_card.grid(row=0, column=0, columnspan=2, **pad)
        small_button(self.pc_card.actions, "Refresh", self.monitor.refresh_inventory, icon="refresh", width=70).pack(side="right")
        self.pc_kv = KeyValues(self.pc_card.body, ["Edition", "Version", "Build", "Installed", "Last start", "Uptime",
                                                   "Computer", "Make / model", "Motherboard", "BIOS", "Processor", "Memory"],
                               key_width=110, wrap=330)
        self.pc_kv.pack(fill="x")

        self.sec_card = Card(b, "Health & security", "shield")
        self.sec_card.grid(row=0, column=2, columnspan=2, **pad)
        small_button(self.sec_card.actions, "Windows Security", lambda: self.app.launch("windowsdefender:"), width=120).pack(side="right")
        self.sec_kv = KeyValues(self.sec_card.body, ["Restart pending", "Antivirus", "Real-time protection", "Virus definitions",
                                                     "Last quick scan", "Tamper protection", "Firewall", "Unexpected shutdowns",
                                                     "Blue screens"], key_width=150, wrap=280)
        self.sec_kv.pack(fill="x")

        self.upd_card = Card(b, "Windows Update", "update")
        self.upd_card.grid(row=1, column=0, columnspan=2, **pad)
        small_button(self.upd_card.actions, "Open", lambda: self.app.launch("ms-settings:windowsupdate"), width=60).pack(side="right", padx=(6, 0))
        self.upd_btn = small_button(self.upd_card.actions, "Check now", self._check_updates, accent=True, width=90)
        self.upd_btn.pack(side="right")
        self.upd_status = ctk.CTkLabel(self.upd_card.body, text="Press “Check now” to look for pending updates (takes up to a minute).",
                                       text_color=C["text_secondary"], wraplength=520, justify="left", anchor="w")
        self.upd_status.pack(fill="x")
        self.upd_list = ctk.CTkFrame(self.upd_card.body, fg_color="transparent", width=1, height=1)
        self.upd_list.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(self.upd_card.body, text="RECENTLY INSTALLED", font=(UI_FONT_SEMIBOLD, 10), text_color=C["text_muted"]).pack(anchor="w", pady=(10, 2))
        self.hotfix = ctk.CTkLabel(self.upd_card.body, text="—", justify="left", anchor="w", font=(UI_FONT, 12))
        self.hotfix.pack(fill="x")

        self.pow_card = Card(b, "Power", "bolt")
        self.pow_card.grid(row=1, column=2, columnspan=2, **pad)
        pb = self.pow_card.body
        ctk.CTkLabel(pb, text="Power plan", text_color=C["text_muted"]).pack(anchor="w")
        self.plan_var = tk.StringVar(value="")
        self.plan_menu = ctk.CTkOptionMenu(pb, variable=self.plan_var, values=["—"], command=self._set_plan, width=260)
        self.plan_menu.pack(anchor="w", pady=(2, 10))
        self.pow_kv = KeyValues(pb, ["Battery", "Power source"], key_width=110)
        self.pow_kv.pack(fill="x")
        pbtn = ctk.CTkFrame(pb, fg_color="transparent")
        pbtn.pack(anchor="w", pady=(10, 0))
        small_button(pbtn, "Power options", lambda: self.app.launch("powercfg.cpl")).pack(side="left", padx=(0, 6))
        small_button(pbtn, "Battery report", self._battery_report).pack(side="left")
        self._plans_sig = None

        self.start_card = Card(b, "Startup apps", "apps")
        self.start_card.grid(row=2, column=0, columnspan=4, **pad)
        small_button(self.start_card.actions, "Refresh", self._load_startup, icon="refresh", width=70).pack(side="right")
        self._startup_loaded = False

        self.ev_card = Card(b, "Recent problems (system log, last 2 days)", "event")
        self.ev_card.grid(row=3, column=0, columnspan=4, **pad)
        small_button(self.ev_card.actions, "Event Viewer", lambda: self.app.launch("eventvwr.msc"), width=100).pack(side="right", padx=(6, 0))
        small_button(self.ev_card.actions, "Reload", self._load_events, icon="refresh", width=70).pack(side="right")
        self._events_loaded = False
        self._events: dict = {}

    def on_show(self) -> None:
        if not self._startup_loaded:
            self._load_startup()
        if not self._events_loaded:
            self._load_events()

    # --- data loads ---
    def _load_startup(self) -> None:
        self._startup_loaded = True
        body = self.start_card.body
        for w in body.winfo_children():
            w.destroy()
        ctk.CTkLabel(body, text="Loading…", text_color=C["text_muted"]).pack(anchor="w")
        self.app.run_bg(sm.list_startup_apps, self._show_startup)

    def _show_startup(self, items: list[dict]) -> None:
        body = self.start_card.body
        for w in body.winfo_children():
            w.destroy()
        if not items:
            ctk.CTkLabel(body, text="No startup apps found." if sm.IS_WINDOWS else "Windows only",
                         text_color=C["text_muted"]).pack(anchor="w")
            return
        body.grid_columnconfigure(1, weight=1)
        on = sum(1 for i in items if i["enabled"])
        ctk.CTkLabel(body, text=f"{on} of {len(items)} start with Windows. Fewer means faster sign-in.",
                     text_color=C["text_secondary"]).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
        for r, it in enumerate(items, start=1):
            ctk.CTkLabel(body, text=it["name"], font=(UI_FONT_SEMIBOLD, 12), anchor="w").grid(row=r, column=0, sticky="w", padx=(0, 14), pady=2)
            cmd = it["command"]
            ctk.CTkLabel(body, text=cmd if len(cmd) < 90 else cmd[:87] + "…", text_color=C["text_muted"], font=(UI_FONT, 11),
                         anchor="w").grid(row=r, column=1, sticky="w")
            ctk.CTkLabel(body, text=it["scope"], text_color=C["text_secondary"], font=(UI_FONT, 11)).grid(row=r, column=2, padx=14)
            var = tk.BooleanVar(value=it["enabled"])
            sw = ctk.CTkSwitch(body, text="", variable=var, width=46)
            sw.configure(command=lambda i=it, v=var: self._toggle_startup(i, v))
            sw.grid(row=r, column=3, sticky="e")

    def _toggle_startup(self, item: dict, var: tk.BooleanVar) -> None:
        want = bool(var.get())
        self.app.run_action("Startup app", lambda: sm.set_startup_enabled(item, want),
                            after=lambda: self._sync_startup_switch(item, var, want))

    def _sync_startup_switch(self, item, var, want) -> None:
        """Puts the switch back if Windows refused the change (e.g. an all-users entry without admin)."""
        current = {(i["name"], i["scope"]): i["enabled"] for i in sm.list_startup_apps()}
        actual = current.get((item["name"], item["scope"]), want)
        if actual != want:
            var.set(actual)

    def _load_events(self) -> None:
        self._events_loaded = True
        body = self.ev_card.body
        for w in body.winfo_children():
            w.destroy()
        ctk.CTkLabel(body, text="Reading the event log…" if sm.IS_WINDOWS else "Windows only", text_color=C["text_muted"]).pack(anchor="w")
        if sm.IS_WINDOWS:
            self.app.run_bg(sm.load_events, self._show_events)

    def _show_events(self, res: dict) -> None:
        self._events = res
        body = self.ev_card.body
        for w in body.winfo_children():
            w.destroy()
        events = res.get("events") or []
        if res.get("error"):
            ctk.CTkLabel(body, text=res["error"], text_color=C["text_muted"]).pack(anchor="w")
            return
        if not events:
            ctk.CTkLabel(body, text="✓  No errors in the system log in the last 2 days.", text_color=C["green"]).pack(anchor="w")
            return
        body.grid_columnconfigure(2, weight=1)
        for r, e in enumerate(events[:25]):
            color = C["red"] if str(e.get("level", "")).lower().startswith(("crit", "kri")) else C["amber"]
            ctk.CTkLabel(body, text=sm.fmt_date(e.get("time"), True), text_color=C["text_secondary"], font=(MONO_FONT, 11)).grid(
                row=r, column=0, sticky="nw", padx=(0, 12), pady=2)
            ctk.CTkLabel(body, text=f"{e.get('source', '')} ({e.get('id')})", text_color=color, font=(UI_FONT_SEMIBOLD, 11),
                         anchor="w").grid(row=r, column=1, sticky="nw", padx=(0, 12), pady=2)
            ctk.CTkLabel(body, text=e.get("msg") or "", font=(UI_FONT, 11), anchor="w", justify="left", wraplength=640).grid(
                row=r, column=2, sticky="nw", pady=2)

    def _check_updates(self) -> None:
        if not sm.IS_WINDOWS:
            self.upd_status.configure(text="Windows only")
            return
        self.upd_btn.configure(state="disabled", text="Checking…")
        self.upd_status.configure(text="Asking Windows Update…", text_color=C["text_secondary"])

        def done(res: dict):
            self.upd_btn.configure(state="normal", text="Check now")
            for w in self.upd_list.winfo_children():
                w.destroy()
            if not res.get("ok"):
                self.upd_status.configure(text=f"Couldn't check: {res.get('error') or 'unknown error'}", text_color=C["amber"])
                return
            ups = res.get("updates") or []
            if not ups:
                self.upd_status.configure(text="✓  You're up to date.", text_color=C["green"])
                return
            self.upd_status.configure(text=f"{len(ups)} update{'s' if len(ups) != 1 else ''} available", text_color=C["amber"])
            for u in ups[:12]:
                ctk.CTkLabel(self.upd_list, text=f"•  {u.get('title')}", anchor="w", justify="left", wraplength=520,
                             font=(UI_FONT, 11)).pack(fill="x")
        self.app.run_bg(sm.check_windows_updates, done)

    def _set_plan(self, name: str) -> None:
        plans = self.monitor.snapshot().get("power_plans") or []
        plan = next((p for p in plans if p["name"] == name), None)
        if plan:
            self.app.run_action("Power plan", lambda: sm.set_power_plan(plan["guid"]))

    def _battery_report(self) -> None:
        if not sm.IS_WINDOWS:
            return
        out = Path(os.environ.get("TEMP", ".")) / "battery-report.html"

        def work():
            code, txt = sm.run_cmd(["powercfg", "/batteryreport", "/output", str(out)], timeout=60)
            if code == 0 and out.exists():
                sm.open_path(str(out))
                return True, "Battery report opened in your browser"
            return False, txt.strip() or "This PC has no battery"
        self.app.run_action("Battery report", work)

    # --- live refresh ---
    def refresh(self, snap: dict) -> None:
        inv = snap.get("inventory") or {}
        wv = snap.get("winver") or {}
        osd, comp, bios, board = inv.get("os") or {}, inv.get("computer") or {}, inv.get("bios") or {}, inv.get("board") or {}
        loading = snap.get("inventory_loading") and not inv.get("os")
        self.pc_kv.set("Edition", (osd.get("caption") or wv.get("caption") or ("Loading…" if loading else None) or "").replace("Microsoft ", ""))
        self.pc_kv.set("Version", wv.get("display_version"))
        self.pc_kv.set("Build", " · ".join(x for x in (wv.get("build"), osd.get("arch")) if x))
        self.pc_kv.set("Installed", sm.fmt_date(osd.get("installed")) if osd.get("installed") else None)
        self.pc_kv.set("Last start", sm.fmt_date(snap.get("boot"), True))
        self.pc_kv.set("Uptime", fmt_duration(snap.get("uptime")))
        self.pc_kv.set("Computer", comp.get("name") or platform.node())
        self.pc_kv.set("Make / model", " ".join(x for x in (comp.get("maker"), comp.get("model")) if x))
        self.pc_kv.set("Motherboard", " ".join(x for x in (board.get("maker"), board.get("product")) if x))
        self.pc_kv.set("BIOS", " · ".join(x for x in (bios.get("version"), sm.fmt_date(bios.get("date")) if bios.get("date") else "") if x))
        cpu = (inv.get("cpu") or [{}])[0] if inv.get("cpu") else {}
        self.pc_kv.set("Processor", short_cpu_name(cpu.get("name", "")) or None)
        self.pc_kv.set("Memory", fmt_bytes((snap.get("mem") or {}).get("total"), 0))

        pr = snap.get("pending_reboot")
        self.sec_kv.set("Restart pending", None if pr is None else ("Yes, restart to finish updates" if pr else "No"),
                        C["amber"] if pr else C["green"] if pr is False else None)
        d = inv.get("defender") or {}
        avs = inv.get("av") or []
        self.sec_kv.set("Antivirus", ", ".join(avs) if avs else ("Microsoft Defender" if d else None),
                        C["green"] if (avs or d.get("av")) else None)

        def onoff(v):
            return (None, None) if v is None else (("On", C["green"]) if v else ("Off", C["red"]))
        self.sec_kv.set("Real-time protection", *onoff(d.get("rtp") if d else None))
        sig_ts = sm.parse_date(d.get("sig"))
        if sig_ts:
            age_days = (time.time() - sig_ts) / 86400
            self.sec_kv.set("Virus definitions", f"updated {sm.fmt_ago(d['sig'])}", C["green"] if age_days < 3 else C["amber"])
        else:
            self.sec_kv.set("Virus definitions", None)
        self.sec_kv.set("Last quick scan", sm.fmt_ago(d.get("quick")) if d.get("quick") else None)
        self.sec_kv.set("Tamper protection", *onoff(d.get("tamper") if d else None))
        fw = inv.get("firewall") or []
        if fw:
            off = [f["name"] for f in fw if not f.get("on")]
            self.sec_kv.set("Firewall", "On for all networks" if not off else f"Off: {', '.join(off)}", C["green"] if not off else C["red"])
        else:
            self.sec_kv.set("Firewall", None)
        ev = self._events
        if ev and ev.get("unexpected") is not None:
            n = int(ev.get("unexpected") or 0)
            self.sec_kv.set("Unexpected shutdowns", f"{n} in 30 days", C["green"] if n == 0 else C["amber"])
            n2 = int(ev.get("bsod") or 0)
            self.sec_kv.set("Blue screens", f"{n2} in 30 days", C["green"] if n2 == 0 else C["red"])

        hf = inv.get("hotfix") or []
        text = "\n".join(f"{sm.fmt_date(h.get('date'))}   {h.get('id')}   {h.get('desc') or ''}" for h in hf) if hf else "—"
        if self.hotfix.cget("text") != text:
            self.hotfix.configure(text=text)

        plans = snap.get("power_plans") or []
        sig = tuple((p["guid"], p["active"]) for p in plans)
        if sig != self._plans_sig:
            self._plans_sig = sig
            names = [p["name"] for p in plans] or ["—"]
            self.plan_menu.configure(values=names)
            active = next((p["name"] for p in plans if p["active"]), names[0])
            self.plan_var.set(active)
        bat = snap.get("battery")
        if bat:
            left = f" · {fmt_duration(bat['secsleft'])} left" if bat.get("secsleft") and not bat.get("plugged") else ""
            self.pow_kv.set("Battery", f"{bat['percent']:.0f}%{left}", level_color(100 - bat["percent"], 75, 85))
            self.pow_kv.set("Power source", "Plugged in" if bat.get("plugged") else "Battery")
        else:
            self.pow_kv.set("Battery", "No battery")
            self.pow_kv.set("Power source", "Mains power")


# ---------------------------------------------------------------------------------------------
TOOLS = [
    ("Task Manager", "Apps, performance, startup", "pulse", "taskmgr"),
    ("Device Manager", "Drivers and hardware", "devices", "devmgmt.msc"),
    ("Disk Management", "Partitions and drive letters", "drive", "diskmgmt.msc"),
    ("Services", "Background services", "services", "services.msc"),
    ("Event Viewer", "System and app logs", "event", "eventvwr.msc"),
    ("Resource Monitor", "Live CPU, disk, network detail", "chart", "resmon"),
    ("Reliability Monitor", "Crash and problem history", "warning", ["perfmon", "/rel"]),
    ("System Information", "Full hardware report", "info", "msinfo32"),
    ("DirectX Diagnostics", "Graphics and sound details", "gpu", "dxdiag"),
    ("Windows Update", "Install updates", "update", "ms-settings:windowsupdate"),
    ("Installed apps", "Uninstall programs", "apps2", "ms-settings:appsfeatures"),
    ("Startup apps", "Choose what starts with Windows", "apps", "ms-settings:startupapps"),
    ("Storage", "Storage Sense and cleanup", "drive", "ms-settings:storagesense"),
    ("Display", "Resolution, scaling, HDR", "display", "ms-settings:display"),
    ("Graphics", "Per-app GPU preference", "gpu", "ms-settings:display-advancedgraphics"),
    ("Sound", "Output and input devices", "sound", "ms-settings:sound"),
    ("Network status", "Connection and reset", "tower", "ms-settings:network-status"),
    ("Windows Security", "Virus and threat protection", "shield", "windowsdefender:"),
    ("Power options", "Power plans", "bolt", "powercfg.cpl"),
    ("Optimize drives", "Defrag / TRIM", "drive", "dfrgui"),
    ("Disk Cleanup", "Remove system junk", "clean", "cleanmgr"),
    ("Control Panel", "Classic settings", "settings", "control"),
    ("Environment variables", "PATH and friends", "list", ["rundll32", "sysdm.cpl,EditEnvironmentVariables"]),
    ("Command Prompt", "Console", "terminal", "cmd"),
]


class ToolsPage(Page):
    title = "Tools"
    subtitle = "Windows tools, maintenance and power"

    def build(self) -> None:
        b = self.body
        self.grid4()
        pad = dict(padx=7, pady=7, sticky="nsew")
        launch = Card(b, "Windows tools", "tools")
        launch.grid(row=0, column=0, columnspan=4, **pad)
        for c in range(4):
            launch.body.grid_columnconfigure(c, weight=1, uniform="t")
        for i, (name, desc, icon, target) in enumerate(TOOLS):
            Tile(launch.body, name, desc, icon, lambda t=target: self.app.launch(t)).grid(
                row=i // 4, column=i % 4, sticky="ew", padx=4, pady=4)

        maint = Card(b, "Maintenance", "clean")
        maint.grid(row=1, column=0, columnspan=2, **pad)
        maint.body.grid_columnconfigure((0, 1), weight=1, uniform="m")
        actions = [
            ("Empty Recycle Bin", "Permanently delete binned files", "delete", self._empty_bin),
            ("Clean temp files", "Old files in your Temp folder", "clean", lambda: self._goto_drives()),
            ("Flush DNS", "Fixes some 'site not found' errors", "tower", lambda: self.app.run_action("Flush DNS", sm.flush_dns)),
            ("Restart Explorer", "Fixes a frozen taskbar", "explorer", self._restart_explorer),
            ("Defender quick scan", "Scan for malware now", "shield", self._quick_scan),
            ("Run as administrator", "Unlock drive temps and more", "shield", self.app.relaunch_admin),
        ]
        for i, (name, desc, icon, cmd) in enumerate(actions):
            Tile(maint.body, name, desc, icon, cmd).grid(row=i // 2, column=i % 2, sticky="ew", padx=4, pady=4)

        power = Card(b, "Power", "power")
        power.grid(row=1, column=2, columnspan=2, **pad)
        power.body.grid_columnconfigure((0, 1), weight=1, uniform="p")
        pacts = [
            ("Lock", "Lock the screen", "lock", C["accent"], lambda: self.app.run_action("Lock", sm.lock_workstation)),
            ("Sleep", "Low-power sleep", "sleep", C["sky"], self._sleep),
            ("Restart", "In 60 seconds", "restart", C["amber"], lambda: self._shutdown(True)),
            ("Shut down", "In 60 seconds", "power", C["red"], lambda: self._shutdown(False)),
            ("Cancel shutdown", "Stop a pending restart/shutdown", "cancel", C["text_secondary"],
             lambda: self.app.run_action("Cancel shutdown", sm.cancel_shutdown)),
        ]
        for i, (name, desc, icon, color, cmd) in enumerate(pacts):
            Tile(power.body, name, desc, icon, cmd, color=color).grid(row=i // 2, column=i % 2, sticky="ew", padx=4, pady=4)

        logc = Card(b, "Activity log", "list")
        logc.grid(row=2, column=0, columnspan=4, **pad)
        self.log = ctk.CTkTextbox(logc.body, height=150, font=(MONO_FONT, 11), wrap="word")
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")
        self.app.log_listeners.append(self._append_log)
        for line in self.app.log_lines:
            self._append_log(line)

    def _append_log(self, line: str) -> None:
        try:
            self.log.configure(state="normal")
            self.log.insert("end", line + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        except tk.TclError:
            pass

    def _goto_drives(self) -> None:
        self.app.show_page("drives")
        self.app.notify("Use “Scan” under Free up space to see how much the temp files take.", "info")

    def _empty_bin(self) -> None:
        if messagebox.askyesno(APP_NAME, "Permanently delete everything in the Recycle Bin?", parent=self.app):
            self.app.run_action("Empty Recycle Bin", sm.empty_recycle_bin)

    def _restart_explorer(self) -> None:
        if messagebox.askyesno(APP_NAME, "Restart Windows Explorer? The taskbar and open File Explorer windows "
                               "will disappear for a few seconds.", parent=self.app):
            self.app.run_action("Restart Explorer", sm.restart_explorer)

    def _quick_scan(self) -> None:
        self.app.notify("Defender quick scan started. This takes a few minutes; the result appears in the log.", "info")
        self.app.run_action("Defender quick scan", sm.defender_quick_scan)

    def _sleep(self) -> None:
        if messagebox.askyesno(APP_NAME, "Put the PC to sleep now?", parent=self.app):
            self.app.run_action("Sleep", sm.sleep_pc)

    def _shutdown(self, restart: bool) -> None:
        verb = "Restart" if restart else "Shut down"
        if messagebox.askyesno(APP_NAME, f"{verb} the PC in 60 seconds?\n\nSave your work. You can cancel it with "
                               "“Cancel shutdown”.", parent=self.app):
            self.app.run_action(verb, lambda: sm.schedule_shutdown(restart, 60))


# ---------------------------------------------------------------------------------------------
class AlertsPage(Page):
    title = "Alerts"
    subtitle = "Warnings raised by the thresholds in Settings"

    def build(self) -> None:
        b = self.body
        self.grid4()
        bar = ctk.CTkFrame(b, fg_color="transparent")
        bar.grid(row=0, column=0, columnspan=4, sticky="ew", padx=7, pady=(4, 6))
        self.summary = ctk.CTkLabel(bar, text="", text_color=C["text_secondary"])
        self.summary.pack(side="left")
        small_button(bar, "Clear all", self._clear, icon="delete", width=80).pack(side="right")
        small_button(bar, "Test notification", self._test, width=120).pack(side="right", padx=6)
        small_button(bar, "Thresholds", lambda: self.app.show_page("settings"), icon="settings", width=90).pack(side="right")
        self.list_card = Card(b)
        self.list_card.grid(row=1, column=0, columnspan=4, padx=7, pady=7, sticky="nsew")
        self._sig = None

    def on_show(self) -> None:
        self.monitor.alerts.unread = 0
        self._sig = None

    def _clear(self) -> None:
        self.monitor.alerts.clear()
        self._sig = None

    def _test(self) -> None:
        self.monitor.alerts.check(f"test:{time.time()}", True, "warning", "Test alert",
                                  "This is what an alert looks like. Windows notifications are "
                                  + ("on." if self.app.settings.get("toasts") else "off (Settings)."))

    def refresh(self, snap: dict) -> None:
        items = list(self.monitor.alerts.items)
        self.monitor.alerts.unread = 0
        sig = tuple(a["time"] for a in items[:100])
        if sig == self._sig:
            return
        self._sig = sig
        body = self.list_card.body
        for w in body.winfo_children():
            w.destroy()
        crit = sum(1 for a in items if a["level"] == "critical")
        self.summary.configure(text=f"{len(items)} alert{'s' if len(items) != 1 else ''}" + (f" · {crit} critical" if crit else ""))
        if not items:
            ctk.CTkLabel(body, text="✓  No alerts. Everything's within your thresholds.", text_color=C["green"],
                         font=(UI_FONT, 13)).pack(anchor="w", pady=10)
            return
        body.grid_columnconfigure(2, weight=1)
        for r, a in enumerate(items[:100]):
            color = C["red"] if a["level"] == "critical" else C["amber"]
            ctk.CTkLabel(body, text="●", text_color=color, font=(UI_FONT, 14)).grid(row=r, column=0, sticky="n", padx=(0, 10), pady=4)
            ctk.CTkLabel(body, text=datetime.fromtimestamp(a["time"]).strftime("%a %H:%M:%S"), text_color=C["text_muted"],
                         font=(MONO_FONT, 11)).grid(row=r, column=1, sticky="nw", padx=(0, 14), pady=5)
            cell = ctk.CTkFrame(body, fg_color="transparent")
            cell.grid(row=r, column=2, sticky="ew", pady=3)
            ctk.CTkLabel(cell, text=a["title"], font=(UI_FONT_SEMIBOLD, 12), anchor="w").pack(anchor="w")
            ctk.CTkLabel(cell, text=a["message"], text_color=C["text_secondary"], anchor="w", justify="left",
                         wraplength=820).pack(anchor="w")


# ---------------------------------------------------------------------------------------------
class SettingsPage(Page):
    title = "Settings"
    subtitle = "Alert thresholds, notifications and data sources"

    SLIDERS = [
        ("cpu_alert", "CPU load", 50, 100, "%"),
        ("ram_alert", "Memory use", 50, 100, "%"),
        ("cpu_temp_alert", "CPU temperature", 60, 105, "°C"),
        ("gpu_temp_alert", "GPU temperature", 60, 100, "°C"),
        ("vram_alert", "GPU memory", 50, 100, "%"),
        ("drive_free_alert", "Drive free space below", 1, 40, "%"),
        ("disk_temp_alert", "Drive temperature", 40, 80, "°C"),
        ("battery_alert", "Battery below", 5, 50, "%"),
        ("alert_sustain_s", "Must stay high for", 5, 300, " s"),
    ]

    def build(self) -> None:
        b = self.body
        self.grid4()
        pad = dict(padx=7, pady=7, sticky="nsew")
        s = self.app.settings
        th = Card(b, "Alert thresholds", "bell")
        th.grid(row=0, column=0, columnspan=2, rowspan=2, **pad)
        th.body.grid_columnconfigure(1, weight=1)
        self._vals: dict[str, ctk.CTkLabel] = {}
        for r, (key, label, lo, hi, unit) in enumerate(self.SLIDERS):
            ctk.CTkLabel(th.body, text=label, anchor="w").grid(row=r, column=0, sticky="w", pady=7, padx=(0, 12))
            val = ctk.CTkLabel(th.body, text=f"{s[key]}{unit}", width=60, anchor="e", font=(UI_FONT_SEMIBOLD, 12))
            val.grid(row=r, column=2, sticky="e")
            self._vals[key] = val
            sl = ctk.CTkSlider(th.body, from_=lo, to=hi, number_of_steps=hi - lo,
                               command=lambda v, k=key, u=unit: self._slide(k, v, u))
            sl.set(s[key])
            sl.grid(row=r, column=1, sticky="ew", padx=(0, 10))

        gen = Card(b, "General", "settings")
        gen.grid(row=0, column=2, columnspan=2, **pad)
        self.toasts = tk.BooleanVar(value=s["toasts"])
        ctk.CTkSwitch(gen.body, text="Windows notifications for alerts", variable=self.toasts,
                      command=lambda: self._set("toasts", bool(self.toasts.get()))).pack(anchor="w", pady=4)
        self.ontop = tk.BooleanVar(value=s["always_on_top"])
        ctk.CTkSwitch(gen.body, text="Keep window on top", variable=self.ontop, command=self._ontop).pack(anchor="w", pady=4)
        self.ping = tk.BooleanVar(value=s["ping_enabled"])
        ctk.CTkSwitch(gen.body, text="Measure latency (ping)", variable=self.ping,
                      command=lambda: self._set("ping_enabled", bool(self.ping.get()))).pack(anchor="w", pady=4)
        row = ctk.CTkFrame(gen.body, fg_color="transparent")
        row.pack(anchor="w", pady=(4, 0), fill="x")
        ctk.CTkLabel(row, text="Ping target", text_color=C["text_secondary"]).pack(side="left", padx=(0, 10))
        self.host = tk.StringVar(value=s["ping_host"])
        e = ctk.CTkEntry(row, textvariable=self.host, width=200)
        e.pack(side="left")
        e.bind("<FocusOut>", lambda _e: self._save_host())
        e.bind("<Return>", lambda _e: self._save_host())

        src = Card(b, "Data sources", "info")
        src.grid(row=1, column=2, columnspan=2, **pad)
        self.src_frame = src.body
        self._src_sig = None

        about = Card(b, "About", "info")
        about.grid(row=2, column=0, columnspan=4, **pad)
        ctk.CTkLabel(about.body, text=f"{APP_NAME} {APP_VERSION}  ·  Python {platform.python_version()}  ·  "
                                      f"psutil {psutil.__version__}  ·  settings: {sm.CONFIG_FILE}",
                     text_color=C["text_muted"], anchor="w", wraplength=1000, justify="left").pack(anchor="w")

    def _slide(self, key: str, v: float, unit: str) -> None:
        v = int(round(v))
        self._vals[key].configure(text=f"{v}{unit}")
        self._set(key, v)

    def _set(self, key: str, value) -> None:
        self.app.settings[key] = value
        self.app.save_settings_soon()

    def _save_host(self) -> None:
        h = self.host.get().strip()
        if h and all(ch.isalnum() or ch in ".-:" for ch in h):
            self._set("ping_host", h)
        else:
            self.host.set(self.app.settings["ping_host"])
            self.app.notify("Ping target must be a host name or IP address.", "warning")

    def _ontop(self) -> None:
        self._set("always_on_top", bool(self.ontop.get()))
        self.app.attributes("-topmost", bool(self.ontop.get()))

    def refresh(self, snap: dict) -> None:
        srcs = dict(self.monitor.sources)
        srcs["Administrator"] = "yes" if snap.get("admin") else "no (drive temperatures and some processes hidden)"
        sig = tuple(sorted(srcs.items()))
        if sig == self._src_sig:
            return
        self._src_sig = sig
        for w in self.src_frame.winfo_children():
            w.destroy()
        self.src_frame.grid_columnconfigure(1, weight=1)
        for r, (k, v) in enumerate(sorted(srcs.items())):
            good = v.startswith(("active", "yes", "Libre", "Open"))
            ctk.CTkLabel(self.src_frame, text="●", text_color=C["green"] if good else C["text_muted"]).grid(row=r, column=0, padx=(0, 8))
            ctk.CTkLabel(self.src_frame, text=k, anchor="w", font=(UI_FONT_SEMIBOLD, 12)).grid(row=r, column=1, sticky="w")
            ctk.CTkLabel(self.src_frame, text=v, anchor="w", text_color=C["text_secondary"], wraplength=360,
                         justify="left").grid(row=r, column=2, sticky="w", padx=(10, 0), pady=2)


# =============================================================================================
# Main window
# =============================================================================================

class CommandCenterApp(ctk.CTk):
    PAGES = [
        ("dashboard", "Dashboard", "home", DashboardPage),
        ("cpu", "CPU & Memory", "cpu", CpuPage),
        ("gpu", "GPU", "gpu", GpuPage),
        ("drives", "Drives", "drive", DrivesPage),
        ("network", "Network", "network", NetworkPage),
        ("processes", "Processes", "apps", ProcessesPage),
        ("windows", "Windows", "windows", WindowsPage),
        ("tools", "Tools", "tools", ToolsPage),
        ("alerts", "Alerts", "bell", AlertsPage),
        ("settings", "Settings", "settings", SettingsPage),
    ]

    def __init__(self, monitor: sm.SystemMonitor | None = None, start_monitor: bool = True):
        apply_theme()
        ctk.set_appearance_mode("dark")
        super().__init__()
        self.monitor = monitor or sm.SystemMonitor()
        self.settings = self.monitor.settings
        self.title(APP_NAME)
        self.minsize(1080, 680)
        self._restore_geometry()
        apply_window_chrome(self)
        if self.settings.get("always_on_top"):
            self.attributes("-topmost", True)
        self.ui_queue: queue.Queue = queue.Queue()
        self.log_lines: list[str] = []
        self.log_listeners: list = []
        self._save_after = None
        self._notify_after = None
        self.pages: dict[str, Page] = {}
        self.current: str | None = None

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_content()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        if start_monitor:
            self.monitor.start()
        start = self.settings.get("last_page") or "dashboard"
        self.show_page(start if start in {p[0] for p in self.PAGES} else "dashboard")
        self.after(100, self._poll_queue)
        self.after(600, self._tick)

    # ---- layout ----
    def _build_sidebar(self) -> None:
        side = ctk.CTkFrame(self, fg_color=C["bg_panel"], corner_radius=0, width=228)
        side.grid(row=0, column=0, sticky="nsw")
        side.grid_propagate(False)
        side.pack_propagate(False)
        logo = ctk.CTkFrame(side, fg_color="transparent")
        logo.pack(fill="x", padx=16, pady=(18, 18))
        tile = ctk.CTkLabel(logo, text=icon_text("pulse"), font=icon_font(18), width=38, height=38, corner_radius=10,
                            fg_color=C["accent"], text_color=ON_ACCENT)
        tile.pack(side="left")
        names = ctk.CTkFrame(logo, fg_color="transparent")
        names.pack(side="left", padx=10)
        ctk.CTkLabel(names, text="System", font=(UI_FONT_SEMIBOLD, 16), anchor="w").pack(anchor="w")
        ctk.CTkLabel(names, text="Command Center", font=(UI_FONT, 11), text_color=C["text_muted"], anchor="w").pack(anchor="w")

        self.nav: dict[str, NavItem] = {}
        for key, label, icon, _cls in self.PAGES:
            item = NavItem(side, key, label, icon, self.show_page)
            item.pack(fill="x", padx=10, pady=1)
            self.nav[key] = item

        mini = ctk.CTkFrame(side, fg_color=C["bg_card"], corner_radius=12, border_width=1, border_color=C["border"])
        mini.pack(side="bottom", fill="x", padx=12, pady=14)
        self.mini_rows = {}
        for k, label in (("cpu", "CPU"), ("ram", "RAM"), ("gpu", "GPU")):
            row = UsageRow(mini, label, caption=False, bar_height=5, title_font=(UI_FONT, 11))
            row.pack(fill="x", padx=12, pady=(10 if k == "cpu" else 4, 0))
            self.mini_rows[k] = row
        self.mini_info = ctk.CTkLabel(mini, text="", font=(UI_FONT, 11), text_color=C["text_muted"], justify="left", anchor="w")
        self.mini_info.pack(fill="x", padx=12, pady=(8, 10))

    def _build_content(self) -> None:
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=0, column=1, sticky="nsew")
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(content, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=24, pady=(16, 6))
        head.grid_columnconfigure(0, weight=1)
        self.page_title = ctk.CTkLabel(head, text="", font=(UI_FONT_SEMIBOLD, 24), anchor="w")
        self.page_title.grid(row=0, column=0, sticky="w")
        self.page_sub = ctk.CTkLabel(head, text="", font=(UI_FONT, 12), text_color=C["text_muted"], anchor="w")
        self.page_sub.grid(row=1, column=0, sticky="w")
        right = ctk.CTkFrame(head, fg_color="transparent")
        right.grid(row=0, column=1, rowspan=2, sticky="e")
        if sm.IS_WINDOWS and not self.monitor.snapshot().get("admin"):
            b = small_button(right, "Run as admin", self.relaunch_admin, icon="shield", width=110)
            b.pack(side="left", padx=(0, 12))
            Tooltip(b, "Restart the Command Center with administrator rights to see drive temperatures/wear, "
                       "every process's location, and to change all-users startup apps.")
        self.clock = ctk.CTkLabel(right, text="", font=(UI_FONT_SEMIBOLD, 15), text_color=C["text_secondary"])
        self.clock.pack(side="left")
        self.page_host = ctk.CTkFrame(content, fg_color="transparent")
        self.page_host.grid(row=1, column=0, sticky="nsew", padx=(16, 10), pady=(0, 4))
        self.page_host.grid_columnconfigure(0, weight=1)
        self.page_host.grid_rowconfigure(0, weight=1)
        self.status = ctk.CTkLabel(content, text="", anchor="w", font=(UI_FONT, 12), height=28, corner_radius=8, fg_color="transparent")
        self.status.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 10))

    # ---- navigation ----
    def show_page(self, key: str) -> None:
        cls = next((p[3] for p in self.PAGES if p[0] == key), None)
        if cls is None:
            return
        if key not in self.pages:
            page = cls(self.page_host, self)
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[key] = page
        page = self.pages[key]
        page.tkraise()
        self.current = key
        for k, item in self.nav.items():
            item.set_selected(k == key)
        self.page_title.configure(text=page.title)
        self.page_sub.configure(text=page.subtitle)
        self.settings["last_page"] = key
        page.on_show()
        try:
            page.refresh(self.monitor.snapshot())
        except Exception as exc:  # noqa: BLE001
            self.notify(f"Couldn't draw this page: {exc}", "error")

    # ---- live updates ----
    def _tick(self) -> None:
        try:
            snap = self.monitor.snapshot()
            self._refresh_chrome(snap)
            page = self.pages.get(self.current or "")
            if page is not None:
                page.refresh(snap)
        except Exception as exc:  # noqa: BLE001 - one bad reading must never stop the dashboard
            self.log(f"refresh error: {exc!r}")
        self.after(1000, self._tick)

    def _refresh_chrome(self, snap: dict) -> None:
        self.clock.configure(text=datetime.now().strftime("%a %d %b  %H:%M:%S"))
        cpu, mem = snap.get("cpu", {}), snap.get("mem", {})
        t = cpu.get("total")
        self.mini_rows["cpu"].set((t or 0) / 100, fmt_pct(t), color=level_color(t))
        p = mem.get("percent")
        self.mini_rows["ram"].set((p or 0) / 100, fmt_pct(p), color=level_color(p, 80, 92))
        gpus = snap.get("gpus") or []
        if gpus:
            u = gpus[0].get("util")
            temp = gpus[0].get("temp")
            self.mini_rows["gpu"].set((u or 0) / 100, fmt_pct(u) + (f" · {temp:.0f}°" if temp is not None else ""),
                                      color=level_color(u, 85, 97))
        else:
            self.mini_rows["gpu"].set(0, "—")
        self.mini_info.configure(text=f"Up {fmt_duration(snap.get('uptime'))}\n"
                                      f"{'Administrator' if snap.get('admin') else 'Standard user'}")
        self.nav["alerts"].set_badge(0 if self.current == "alerts" else snap.get("alerts_unread", 0))

    # ---- background work ----
    def run_bg(self, fn, done=None) -> None:
        """Runs fn in a worker thread; done(result) is called back on the UI thread."""
        def work():
            try:
                res = fn()
            except Exception as exc:  # noqa: BLE001
                res = exc
            self.ui_queue.put((done, res))
        threading.Thread(target=work, daemon=True).start()

    def run_action(self, label: str, fn, after=None) -> None:
        """Runs an action that returns (ok, message) and reports it in the status bar and log."""
        self.notify(f"{label}…", "info", sticky=True)

        def done(res):
            if isinstance(res, Exception):
                ok, msg = False, str(res)
            else:
                ok, msg = res
            self.notify(msg, "ok" if ok else "error")
            self.log(f"{label}: {msg}")
            if after:
                try:
                    after()
                except Exception:
                    pass
        done.accepts_errors = True  # type: ignore[attr-defined]
        self.run_bg(fn, done)

    def _poll_queue(self) -> None:
        try:
            while True:
                done, res = self.ui_queue.get_nowait()
                if isinstance(res, Exception):
                    self.notify(f"Something went wrong: {res}", "error")
                    self.log(f"error: {res!r}")
                    if getattr(done, "accepts_errors", False):
                        done(res)
                elif done:
                    try:
                        done(res)
                    except Exception as exc:  # noqa: BLE001
                        self.log(f"callback error: {exc!r}")
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def launch(self, target) -> None:
        if isinstance(target, list):
            def work():
                try:
                    subprocess_popen(target)
                    return True, f"Opened {target[0]}"
                except Exception as exc:  # noqa: BLE001
                    return False, f"Couldn't open {target[0]}: {exc}"
            self.run_action("Open", work)
        else:
            ok, msg = sm.open_path(target)
            self.notify(msg, "ok" if ok else "error")
            if not ok:
                self.log(msg)

    def relaunch_admin(self) -> None:
        if not sm.IS_WINDOWS:
            self.notify("Windows only", "warning")
            return
        if self.monitor.snapshot().get("admin"):
            self.notify("Already running as administrator.", "info")
            return
        if sm.relaunch_as_admin():
            self.on_close()
        else:
            self.notify("Administrator restart was cancelled.", "warning")

    def notify(self, msg: str, level: str = "info", sticky: bool = False) -> None:
        """One-line message in the status strip under the page."""
        color = {"ok": C["green"], "error": C["red"], "warning": C["amber"]}.get(level, C["text_secondary"])
        self.status.configure(text=f"  {msg}", text_color=color, fg_color=tint(color, 0.12, C["bg_root"]))
        if self._notify_after:
            self.after_cancel(self._notify_after)
            self._notify_after = None
        if not sticky:
            self._notify_after = self.after(7000, lambda: self.status.configure(text="", fg_color="transparent"))

    def log(self, line: str) -> None:
        stamped = f"{datetime.now().strftime('%H:%M:%S')}  {line}"
        self.log_lines.append(stamped)
        del self.log_lines[:-300]
        for fn in list(self.log_listeners):
            try:
                fn(stamped)
            except Exception:
                pass

    # ---- settings & window state ----
    def save_settings_soon(self) -> None:
        if self._save_after:
            self.after_cancel(self._save_after)
        self._save_after = self.after(600, lambda: sm.save_settings(self.settings))

    def _restore_geometry(self) -> None:
        geo = self.settings.get("window") or ""
        try:
            if geo:
                size, _, pos = geo.partition("+")
                w, h = (int(x) for x in size.split("x"))
                x, y = (int(v) for v in pos.split("+")) if pos else (60, 40)
                if 0 <= x < self.winfo_screenwidth() - 100 and 0 <= y < self.winfo_screenheight() - 100:
                    self.geometry(f"{w}x{h}+{x}+{y}")
                    return
        except (ValueError, tk.TclError):
            pass
        w = min(1460, self.winfo_screenwidth() - 80)
        h = min(920, self.winfo_screenheight() - 100)
        self.geometry(f"{w}x{h}+40+30")

    def on_close(self) -> None:
        try:
            if self.state() == "normal":
                self.settings["window"] = self.geometry()
        except tk.TclError:
            pass
        sm.save_settings(self.settings)
        self.monitor.stop()
        self.destroy()


def subprocess_popen(args: list[str]) -> None:
    import subprocess
    subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def main() -> None:
    if sm.IS_WINDOWS:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SystemCommandCenter.App")
        except Exception:
            pass
    app = CommandCenterApp()
    app.mainloop()


if __name__ == "__main__":
    main()
