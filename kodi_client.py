"""
kodi_client.py — Kodi tab for John's NZB Command Center.

Talks to Kodi over its JSON-RPC web interface (Kodi: Settings > Services >
Control > "Allow remote control via HTTP"). Keep this file next to
nzb_command_center.py.

Features
    * Connection settings (URL, username, password) saved through the host app
    * Live status polling (online / offline, Kodi version)
    * Library actions: scan / clean video library, scan music library
    * Now Playing with play-pause / stop
    * Library counts and recently added movies & episodes
    * "On disk, not in Kodi" check against the Media Inspector's file list
    * Auto library scan + on-screen notification when SABnzbd finishes a download

No dependencies beyond customtkinter and the standard library.
"""

from __future__ import annotations

import base64
import json
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import PurePath
from typing import Any, Callable

import tkinter as tk

import customtkinter as ctk

POLL_INTERVAL_MS = 15_000      # status / now-playing refresh
SCAN_DEBOUNCE_S = 45           # wait after a completed download before scanning
REQUEST_TIMEOUT_S = 8


# ==============================================================================
# JSON-RPC client
# ==============================================================================

class KodiError(Exception):
    pass


def normalize_kodi_url(raw: str) -> str:
    """Accept '192.168.1.50:8080', 'http://host:8080/', 'http://host:8080/jsonrpc'."""
    url = (raw or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    url = url.rstrip("/")
    if url.lower().endswith("/jsonrpc"):
        url = url[: -len("/jsonrpc")]
    parsed = urllib.parse.urlparse(url)
    if not parsed.port and parsed.scheme == "http":
        url = f"{parsed.scheme}://{parsed.hostname}:8080{parsed.path}"
    return url


class KodiClient:
    def __init__(self, url: str, username: str = "", password: str = "") -> None:
        self.base_url = normalize_kodi_url(url)
        self.username = username or ""
        self.password = password or ""
        self._id = 0
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def call(self, method: str, params: dict | None = None) -> Any:
        if not self.base_url:
            raise KodiError("Kodi URL is not set")
        with self._lock:
            self._id += 1
            req_id = self._id
        payload = {"jsonrpc": "2.0", "method": method, "id": req_id}
        if params:
            payload["params"] = params
        req = urllib.request.Request(
            self.base_url + "/jsonrpc",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if self.username or self.password:
            token = base64.b64encode(f"{self.username}:{self.password}".encode("utf-8")).decode("ascii")
            req.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                body = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise KodiError("Authentication failed (check username / password)") from None
            raise KodiError(f"HTTP {exc.code}: {exc.reason}") from None
        except urllib.error.URLError as exc:
            raise KodiError(f"Cannot reach Kodi: {exc.reason}") from None
        except (TimeoutError, OSError) as exc:
            raise KodiError(f"Cannot reach Kodi: {exc}") from None
        except json.JSONDecodeError:
            raise KodiError("Kodi returned an invalid response") from None
        if "error" in body:
            err = body["error"] or {}
            raise KodiError(f"{method}: {err.get('message', 'error')} ({err.get('code', '?')})")
        return body.get("result")

    # --- convenience wrappers -------------------------------------------------
    def ping(self) -> bool:
        return self.call("JSONRPC.Ping") == "pong"

    def version(self) -> str:
        res = self.call("Application.GetProperties", {"properties": ["version", "name"]}) or {}
        v = res.get("version") or {}
        tag = v.get("tag", "")
        ver = f"{v.get('major', '?')}.{v.get('minor', '?')}"
        if tag and tag != "stable":
            ver += f" {tag}"
        return f"{res.get('name', 'Kodi')} {ver}"

    def notify(self, title: str, message: str, display_ms: int = 6000) -> None:
        self.call("GUI.ShowNotification", {"title": title, "message": message[:200], "displaytime": display_ms})

    def scan_video(self) -> None:
        self.call("VideoLibrary.Scan", {"showdialogs": False})

    def clean_video(self) -> None:
        self.call("VideoLibrary.Clean", {"showdialogs": False})

    def scan_audio(self) -> None:
        self.call("AudioLibrary.Scan", {"showdialogs": False})

    def now_playing(self) -> dict | None:
        players = self.call("Player.GetActivePlayers") or []
        if not players:
            return None
        pid = players[0]["playerid"]
        item = (self.call("Player.GetItem", {
            "playerid": pid,
            "properties": ["title", "showtitle", "season", "episode", "year", "file"],
        }) or {}).get("item", {})
        props = self.call("Player.GetProperties", {
            "playerid": pid,
            "properties": ["percentage", "time", "totaltime", "speed"],
        }) or {}
        return {"playerid": pid, "type": players[0].get("type", ""), "item": item, "props": props}

    def play_pause(self, playerid: int) -> None:
        self.call("Player.PlayPause", {"playerid": playerid})

    def stop(self, playerid: int) -> None:
        self.call("Player.Stop", {"playerid": playerid})

    def library_counts(self) -> dict:
        lim = {"limits": {"start": 0, "end": 1}}
        movies = (self.call("VideoLibrary.GetMovies", lim) or {}).get("limits", {}).get("total", 0)
        shows = (self.call("VideoLibrary.GetTVShows", lim) or {}).get("limits", {}).get("total", 0)
        eps = (self.call("VideoLibrary.GetEpisodes", lim) or {}).get("limits", {}).get("total", 0)
        return {"movies": movies, "shows": shows, "episodes": eps}

    def recently_added(self, n: int = 12) -> list[str]:
        out: list[str] = []
        movies = (self.call("VideoLibrary.GetRecentlyAddedMovies", {
            "properties": ["title", "year", "dateadded"], "limits": {"start": 0, "end": n},
        }) or {}).get("movies", [])
        eps = (self.call("VideoLibrary.GetRecentlyAddedEpisodes", {
            "properties": ["showtitle", "season", "episode", "title", "dateadded"], "limits": {"start": 0, "end": n},
        }) or {}).get("episodes", [])
        rows: list[tuple[str, str]] = []
        for m in movies:
            yr = f" ({m['year']})" if m.get("year") else ""
            rows.append((m.get("dateadded", ""), f"🎬 {m.get('title', '?')}{yr}"))
        for e in eps:
            rows.append((e.get("dateadded", ""),
                         f"📺 {e.get('showtitle', '?')} S{int(e.get('season', 0)):02d}E{int(e.get('episode', 0)):02d} — {e.get('title', '')}"))
        rows.sort(key=lambda r: r[0], reverse=True)
        for added, label in rows[:n]:
            out.append(f"{added[:10]}  {label}" if added else label)
        return out

    def movies(self) -> list[dict]:
        res = self.call("VideoLibrary.GetMovies", {
            "properties": ["title", "year", "runtime", "playcount", "resume"],
            "sort": {"method": "sorttitle", "order": "ascending", "ignorearticle": True},
        }) or {}
        return res.get("movies", [])

    def play_movie(self, movieid: int, resume: bool = False) -> None:
        self.call("Player.Open", {"item": {"movieid": int(movieid)}, "options": {"resume": bool(resume)}})

    def library_filenames(self) -> set[str]:
        """Lower-cased file names (no folder) of every movie and episode Kodi knows about."""
        names: set[str] = set()
        movies = (self.call("VideoLibrary.GetMovies", {"properties": ["file"]}) or {}).get("movies", [])
        eps = (self.call("VideoLibrary.GetEpisodes", {"properties": ["file"]}) or {}).get("episodes", [])
        for it in list(movies) + list(eps):
            f = it.get("file") or ""
            if f.startswith("stack://"):
                for part in f[len("stack://"):].split(" , "):
                    names.add(_basename(part))
            elif f:
                names.add(_basename(f))
        return names


def _basename(path: str) -> str:
    # Kodi paths can be smb://, nfs://, C:\..., /mnt/... — take the last segment either way.
    p = path.replace("\\", "/").rstrip("/")
    return urllib.parse.unquote(p.rsplit("/", 1)[-1]).lower()


# ==============================================================================
# Panel
# ==============================================================================

class KodiPanel(ctk.CTkFrame):
    def __init__(
        self,
        master,
        *,
        colors: dict,
        ui_font: str,
        mono_font: str,
        kodi_config: dict,
        on_config_change: Callable[[dict], None] | None = None,
        on_status: Callable[..., None] | None = None,
        on_kodi_state: Callable[[str, str], None] | None = None,
        get_disk_items: Callable[[], list[dict]] | None = None,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self.c = colors
        self.ui_font = ui_font
        self.mono_font = mono_font
        self._cfg = kodi_config
        self._on_config_change = on_config_change
        self._on_status = on_status
        self._on_kodi_state = on_kodi_state
        self._get_disk_items = get_disk_items

        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="kodi")
        self._results: queue.Queue = queue.Queue()
        self._closing = False
        self._poll_job: str | None = None
        self._pump_job: str | None = None
        self._scan_timer: threading.Timer | None = None
        self._pending_names: list[str] = []
        self._pending_lock = threading.Lock()
        self._online: bool | None = None
        self._playerid: int | None = None
        self._client = self._make_client()

        self._build_ui()
        self._pump_job = self.after(150, self._pump)
        if self._client.configured:
            self._report_state("Connecting…", "sky")
            self.after(500, self._poll)
        else:
            self._report_state("Not Configured", "text_muted")

    # ------------------------------------------------------------------ helpers
    def _font(self, size: int = 13, weight: str = "normal", mono: bool = False) -> ctk.CTkFont:
        return ctk.CTkFont(family=self.mono_font if mono else self.ui_font, size=size, weight=weight)

    def _make_client(self) -> KodiClient:
        return KodiClient(
            self._cfg.get("kodi_url", ""),
            self._cfg.get("kodi_username", ""),
            self._cfg.get("kodi_password", ""),
        )

    def _status(self, msg: str, level: str = "INFO") -> None:
        self._log(msg, level)
        if self._on_status:
            try:
                self._on_status(f"Kodi: {msg}", log=True, level=level)
            except TypeError:
                self._on_status(f"Kodi: {msg}")

    def _report_state(self, text: str, color_key: str) -> None:
        if hasattr(self, "state_lbl"):
            self.state_lbl.configure(text=f"●  {text}", text_color=self.c.get(color_key, self.c["text_muted"]))
        if self._on_kodi_state:
            self._on_kodi_state(text, color_key)

    def _run(self, fn: Callable[[], Any], on_ok: Callable[[Any], None] | None = None,
             on_err: Callable[[Exception], None] | None = None) -> None:
        """Run fn on a worker thread; deliver the result back on the Tk thread."""
        if self._closing:
            return

        def work():
            try:
                res = fn()
                self._results.put((on_ok, res, None))
            except Exception as exc:  # noqa: BLE001 — surfaced in the UI
                self._results.put((on_err, None, exc))

        self._pool.submit(work)

    def _pump(self) -> None:
        if self._closing:
            return
        try:
            while True:
                cb, res, exc = self._results.get_nowait()
                if exc is not None:
                    if cb:
                        cb(exc)
                    else:
                        self._status(str(exc), "ERROR")
                elif cb:
                    cb(res)
        except queue.Empty:
            pass
        self._pump_job = self.after(150, self._pump)

    # ------------------------------------------------------------------ UI
    def _card(self, parent, title: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=self.c["bg_panel"], border_width=1,
                            border_color=self.c["border"], corner_radius=10)
        ctk.CTkLabel(card, text=title, font=self._font(14, "bold"),
                     text_color=self.c["text_primary"]).pack(anchor="w", padx=14, pady=(12, 6))
        return card

    def _button(self, parent, text: str, cmd, accent: bool = False, width: int = 140) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent, text=text, command=cmd, width=width, height=32, font=self._font(12, "bold"),
            fg_color=self.c["accent"] if accent else self.c["bg_card_alt"],
            hover_color=self.c["accent_hover"] if accent else self.c["border"],
            text_color=self.c["bg_root"] if accent else self.c["text_primary"],
            corner_radius=8,
        )

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=0, minsize=360)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        left = ctk.CTkScrollableFrame(self, fg_color="transparent", width=360)
        left.grid(row=0, column=0, sticky="nsew", padx=(10, 6), pady=10)
        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 10), pady=10)
        right.grid_columnconfigure(0, weight=1)
        right.grid_columnconfigure(1, weight=1)
        right.grid_rowconfigure(1, weight=3)
        right.grid_rowconfigure(2, weight=2)

        # --- Connection ----------------------------------------------------
        conn = self._card(left, "Connection")
        conn.pack(fill="x", pady=(0, 10))
        self.state_lbl = ctk.CTkLabel(conn, text="●  Not Configured", font=self._font(12, "bold"),
                                      text_color=self.c["text_muted"])
        self.state_lbl.pack(anchor="w", padx=14)
        self.version_lbl = ctk.CTkLabel(conn, text="", font=self._font(11), text_color=self.c["text_secondary"])
        self.version_lbl.pack(anchor="w", padx=14, pady=(0, 6))

        def field(label: str, value: str, show: str = "") -> ctk.CTkEntry:
            ctk.CTkLabel(conn, text=label, font=self._font(11), text_color=self.c["text_secondary"]).pack(anchor="w", padx=14)
            e = ctk.CTkEntry(conn, font=self._font(12), show=show, height=32,
                             fg_color=self.c["bg_card"], border_color=self.c["border"], text_color=self.c["text_primary"])
            e.pack(fill="x", padx=14, pady=(2, 8))
            if value:
                e.insert(0, value)
            return e

        self.url_e = field("Kodi address (e.g. 192.168.1.50:8080)", self._cfg.get("kodi_url", ""))
        self.user_e = field("Username", self._cfg.get("kodi_username", ""))
        self.pw_e = field("Password", self._cfg.get("kodi_password", ""), show="•")

        self.auto_scan_v = ctk.BooleanVar(value=bool(self._cfg.get("kodi_auto_scan", True)))
        self.notify_v = ctk.BooleanVar(value=bool(self._cfg.get("kodi_notify_on_complete", True)))
        for text, var in (("Scan library when a download completes", self.auto_scan_v),
                          ("Show a notification on Kodi when a download completes", self.notify_v)):
            ctk.CTkCheckBox(conn, text=text, variable=var, font=self._font(12), command=self._save_settings,
                            text_color=self.c["text_primary"], fg_color=self.c["accent"],
                            hover_color=self.c["accent_hover"], border_color=self.c["border"]).pack(anchor="w", padx=14, pady=3)

        row = ctk.CTkFrame(conn, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(8, 14))
        self._button(row, "Save & Connect", self._save_and_connect, accent=True, width=150).pack(side="left")
        self._button(row, "Test", self._test, width=80).pack(side="left", padx=8)

        ctk.CTkLabel(left, text="Enable in Kodi: Settings › Services › Control ›\n"
                                "“Allow remote control via HTTP”.",
                     font=self._font(11), justify="left", text_color=self.c["text_muted"]).pack(anchor="w", padx=6, pady=(0, 10))

        # --- Library actions --------------------------------------------------
        act = self._card(left, "Library Actions")
        act.pack(fill="x", pady=(0, 10))
        grid = ctk.CTkFrame(act, fg_color="transparent")
        grid.pack(fill="x", padx=14, pady=(0, 14))
        actions = (
            ("Scan Video Library", lambda: self._action("Video library scan started", lambda: self._client.scan_video())),
            ("Clean Video Library", lambda: self._action("Video library clean started", lambda: self._client.clean_video())),
            ("Scan Music Library", lambda: self._action("Music library scan started", lambda: self._client.scan_audio())),
            ("Test Notification", lambda: self._action("Notification sent",
                                                       lambda: self._client.notify("NZB Command Center", "Hello from your PC 👋"))),
        )
        for i, (text, cmd) in enumerate(actions):
            self._button(grid, text, cmd, width=150).grid(row=i // 2, column=i % 2, padx=(0, 8), pady=4, sticky="w")

        # --- Now playing (right, top-left) ------------------------------------
        np_card = self._card(right, "Now Playing")
        np_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 10))
        self.np_title = ctk.CTkLabel(np_card, text="Nothing playing", font=self._font(13, "bold"),
                                     text_color=self.c["text_primary"], wraplength=360, justify="left")
        self.np_title.pack(anchor="w", padx=14)
        self.np_time = ctk.CTkLabel(np_card, text="", font=self._font(11, mono=True), text_color=self.c["text_secondary"])
        self.np_time.pack(anchor="w", padx=14, pady=(2, 4))
        self.np_bar = ctk.CTkProgressBar(np_card, height=8, progress_color=self.c["accent"], fg_color=self.c["bg_card_alt"])
        self.np_bar.set(0)
        self.np_bar.pack(fill="x", padx=14, pady=(0, 8))
        ctl = ctk.CTkFrame(np_card, fg_color="transparent")
        ctl.pack(anchor="w", padx=14, pady=(0, 14))
        self.pp_btn = self._button(ctl, "⏯  Play / Pause", self._play_pause, width=130)
        self.pp_btn.pack(side="left")
        self.stop_btn = self._button(ctl, "⏹  Stop", self._stop, width=90)
        self.stop_btn.pack(side="left", padx=8)
        self._set_player_controls(False)

        # --- Library stats (right, top-right) ---------------------------------
        st_card = self._card(right, "Library")
        st_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 10))
        self.stats_lbl = ctk.CTkLabel(st_card, text="—", font=self._font(13), justify="left",
                                      text_color=self.c["text_primary"])
        self.stats_lbl.pack(anchor="w", padx=14)
        self._button(st_card, "Refresh", self._refresh_library, width=90).pack(anchor="w", padx=14, pady=(8, 14))

        # --- Movies (right, middle-left) --------------------------------------
        mv_card = self._card(right, "Movies")
        mv_card.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(0, 10))
        top = ctk.CTkFrame(mv_card, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(0, 6))
        self.search_v = ctk.StringVar()
        self.search_v.trace_add("write", lambda *_: self._filter_movies())
        ctk.CTkEntry(top, textvariable=self.search_v, placeholder_text="Search movies…", height=32,
                     font=self._font(12), fg_color=self.c["bg_card"], border_color=self.c["border"],
                     text_color=self.c["text_primary"]).pack(side="left", fill="x", expand=True)
        self._button(top, "↻", self._load_movies, width=36).pack(side="left", padx=(6, 0))
        self._button(top, "Scan Library", self._scan_and_reload, width=110).pack(side="left", padx=(6, 0))

        lb_wrap = ctk.CTkFrame(mv_card, fg_color=self.c["bg_card"], border_width=1,
                               border_color=self.c["border"], corner_radius=8)
        lb_wrap.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        self.movie_lb = tk.Listbox(
            lb_wrap, activestyle="none", exportselection=False, borderwidth=0, highlightthickness=0,
            bg=self.c["bg_card"], fg=self.c["text_primary"], selectbackground=self.c["accent"],
            selectforeground=self.c["bg_root"], font=(self.ui_font, 11),
        )
        sb = ctk.CTkScrollbar(lb_wrap, command=self.movie_lb.yview)
        self.movie_lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(0, 2), pady=4)
        self.movie_lb.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        self.movie_lb.bind("<Double-Button-1>", lambda _e: self._play_selected())
        self.movie_lb.bind("<Return>", lambda _e: self._play_selected())

        bot = ctk.CTkFrame(mv_card, fg_color="transparent")
        bot.pack(fill="x", padx=14, pady=(0, 14))
        self._button(bot, "▶  Play", self._play_selected, accent=True, width=100).pack(side="left")
        self.resume_v = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(bot, text="Resume where I left off", variable=self.resume_v, font=self._font(12),
                        text_color=self.c["text_primary"], fg_color=self.c["accent"],
                        hover_color=self.c["accent_hover"], border_color=self.c["border"]).pack(side="left", padx=10)
        self.movie_count_lbl = ctk.CTkLabel(bot, text="", font=self._font(11), text_color=self.c["text_muted"])
        self.movie_count_lbl.pack(side="right")
        self._all_movies: list[dict] = []
        self._shown_movies: list[dict] = []

        # --- Recently added (right, middle-right) -----------------------------
        ra_card = self._card(right, "Recently Added")
        ra_card.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=(0, 10))
        self.recent_box = self._textbox(ra_card)

        # --- On disk, not in Kodi (right, middle-right) -----------------------
        miss_card = self._card(right, "On Disk, Not in Kodi")
        miss_card.grid(row=2, column=0, sticky="nsew", padx=(0, 6))
        bar = ctk.CTkFrame(miss_card, fg_color="transparent")
        bar.pack(fill="x", padx=14, pady=(0, 6))
        self._button(bar, "Compare", self._compare, width=90).pack(side="left")
        self.miss_lbl = ctk.CTkLabel(bar, text="Uses the Media Inspector's file list", font=self._font(11),
                                     text_color=self.c["text_muted"])
        self.miss_lbl.pack(side="left", padx=10)
        self.miss_box = self._textbox(miss_card)

        # --- Log (right, bottom) ----------------------------------------------
        log_card = self._card(right, "Kodi Log")
        log_card.grid(row=2, column=1, sticky="nsew", padx=(6, 0))
        self.log_box = self._textbox(log_card, mono=True)
        for tag, key in (("OK", "green"), ("WARN", "amber"), ("ERROR", "red"), ("INFO", "text_secondary")):
            self.log_box.tag_config(tag, foreground=self.c[key])

    def _textbox(self, parent, mono: bool = False) -> ctk.CTkTextbox:
        tb = ctk.CTkTextbox(parent, font=self._font(11 if mono else 12, mono=mono), wrap="none",
                            fg_color=self.c["bg_card"], text_color=self.c["text_primary"],
                            border_width=1, border_color=self.c["border"], corner_radius=8)
        tb.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        tb.configure(state="disabled")
        return tb

    def _set_text(self, box: ctk.CTkTextbox, lines: list[str], empty: str) -> None:
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("end", "\n".join(lines) if lines else empty)
        box.configure(state="disabled")

    def _log(self, msg: str, level: str = "INFO") -> None:
        if not hasattr(self, "log_box"):
            return
        lvl = level.upper() if level.upper() in ("INFO", "OK", "WARN", "ERROR") else "INFO"
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{datetime.now():%H:%M:%S}] [{lvl:<5}] {msg}\n", lvl)
        # keep the log bounded
        if int(self.log_box.index("end-1c").split(".")[0]) > 500:
            self.log_box.delete("1.0", "100.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _set_player_controls(self, enabled: bool) -> None:
        st = "normal" if enabled else "disabled"
        self.pp_btn.configure(state=st)
        self.stop_btn.configure(state=st)

    # ------------------------------------------------------------------ settings
    def _current_settings(self) -> dict:
        return {
            "kodi_url": self.url_e.get().strip(),
            "kodi_username": self.user_e.get().strip(),
            "kodi_password": self.pw_e.get(),
            "kodi_auto_scan": bool(self.auto_scan_v.get()),
            "kodi_notify_on_complete": bool(self.notify_v.get()),
        }

    def _save_settings(self) -> None:
        new = self._current_settings()
        self._cfg.update(new)
        if self._on_config_change:
            try:
                self._on_config_change(new)
            except Exception as exc:  # noqa: BLE001
                self._status(f"Could not save settings: {exc}", "ERROR")

    def _save_and_connect(self) -> None:
        self._save_settings()
        self._client = self._make_client()
        self._online = None
        if not self._client.configured:
            self._report_state("Not Configured", "text_muted")
            self.version_lbl.configure(text="")
            self._status("Settings saved (no address set)", "WARN")
            return
        self._status(f"Settings saved — connecting to {self._client.base_url}")
        self._report_state("Connecting…", "sky")
        self._restart_polling()

    def _test(self) -> None:
        s = self._current_settings()
        client = KodiClient(s["kodi_url"], s["kodi_username"], s["kodi_password"])
        if not client.configured:
            self._status("Enter Kodi's address first", "WARN")
            return
        self._log(f"Testing {client.base_url} …")
        self._run(lambda: (client.ping(), client.version()),
                  lambda r: self._status(f"Connection OK — {r[1]}", "OK"),
                  lambda e: self._status(f"Test failed: {e}", "ERROR"))

    # ------------------------------------------------------------------ polling
    def _restart_polling(self) -> None:
        if self._poll_job:
            try:
                self.after_cancel(self._poll_job)
            except Exception:  # noqa: BLE001
                pass
            self._poll_job = None
        self._poll()

    def _poll(self) -> None:
        self._poll_job = None
        if self._closing:
            return
        client = self._client
        if client.configured:
            first = self._online is not True

            def work():
                client.ping()
                ver = client.version() if first else None
                np = client.now_playing()
                return ver, np

            self._run(work, lambda r: self._on_poll_ok(client, *r), lambda e: self._on_poll_err(client, e))
        self._poll_job = self.after(POLL_INTERVAL_MS, self._poll)

    def _on_poll_ok(self, client: KodiClient, ver: str | None, np: dict | None) -> None:
        if client is not self._client:
            return
        if self._online is not True:
            self._online = True
            self._report_state("Online", "green")
            self._status(f"Connected to {client.base_url}", "OK")
            self._refresh_library()
            self._load_movies()
        if ver:
            self.version_lbl.configure(text=ver)
        self._show_now_playing(np)

    def _on_poll_err(self, client: KodiClient, exc: Exception) -> None:
        if client is not self._client:
            return
        if self._online is not False:
            self._online = False
            self._report_state("Offline", "red")
            self._status(str(exc), "WARN")
        self._show_now_playing(None)

    def _show_now_playing(self, np: dict | None) -> None:
        if not np:
            self._playerid = None
            self.np_title.configure(text="Nothing playing")
            self.np_time.configure(text="")
            self.np_bar.set(0)
            self._set_player_controls(False)
            return
        self._playerid = np["playerid"]
        it, pr = np["item"], np["props"]
        if it.get("showtitle"):
            title = f"{it['showtitle']} — S{int(it.get('season', 0)):02d}E{int(it.get('episode', 0)):02d} {it.get('title', '')}"
        else:
            title = it.get("title") or it.get("label") or (PurePath(it.get("file", "")).name or "Unknown")
            if it.get("year"):
                title += f" ({it['year']})"
        paused = pr.get("speed", 1) == 0
        self.np_title.configure(text=("⏸  " if paused else "▶  ") + title)
        self.np_time.configure(text=f"{_fmt_time(pr.get('time'))} / {_fmt_time(pr.get('totaltime'))}")
        self.np_bar.set(max(0.0, min(1.0, float(pr.get("percentage", 0)) / 100.0)))
        self._set_player_controls(True)

    # ------------------------------------------------------------------ actions
    def _action(self, ok_msg: str, fn: Callable[[], Any]) -> None:
        if not self._client.configured:
            self._status("Set Kodi's address first", "WARN")
            return
        self._run(fn, lambda _r: self._status(ok_msg, "OK"), lambda e: self._status(str(e), "ERROR"))

    def _play_pause(self) -> None:
        if self._playerid is not None:
            pid = self._playerid
            self._run(lambda: self._client.play_pause(pid), lambda _r: self.after(400, self._restart_polling))

    def _stop(self) -> None:
        if self._playerid is not None:
            pid = self._playerid
            self._run(lambda: self._client.stop(pid), lambda _r: self.after(400, self._restart_polling))

    # --- movies -------------------------------------------------------------
    def _load_movies(self) -> None:
        if not self._client.configured:
            self._status("Set Kodi's address first", "WARN")
            return
        self.movie_count_lbl.configure(text="Loading…")

        def done(movies: list[dict]):
            self._all_movies = movies
            self._filter_movies()

        def fail(e: Exception):
            self.movie_count_lbl.configure(text="Could not load")
            self._status(f"Could not load movies: {e}", "ERROR")

        self._run(self._client.movies, done, fail)

    def _filter_movies(self) -> None:
        q = self.search_v.get().strip().lower()
        self._shown_movies = [m for m in self._all_movies
                              if not q or q in f"{m.get('title', '')} {m.get('year', '')}".lower()]
        self.movie_lb.delete(0, "end")
        for m in self._shown_movies:
            yr = f"  ({m['year']})" if m.get("year") else ""
            res = m.get("resume") or {}
            mark = "✔ " if m.get("playcount") else ("◐ " if res.get("position") else "   ")
            self.movie_lb.insert("end", f"{mark}{m.get('title', '?')}{yr}")
        total = len(self._all_movies)
        self.movie_count_lbl.configure(
            text=f"{len(self._shown_movies):,} of {total:,}" if q else f"{total:,} movies")

    def _play_selected(self) -> None:
        sel = self.movie_lb.curselection()
        if not sel:
            self._status("Pick a movie first", "WARN")
            return
        m = self._shown_movies[sel[0]]
        title = m.get("title", "?")
        resume = bool(self.resume_v.get())
        mid = m["movieid"]
        self._run(lambda: self._client.play_movie(mid, resume),
                  lambda _r: (self._status(f"Playing {title}", "OK"), self.after(1500, self._restart_polling)),
                  lambda e: self._status(f"Could not play {title}: {e}", "ERROR"))

    def _scan_and_reload(self) -> None:
        if not self._client.configured:
            self._status("Set Kodi's address first", "WARN")
            return

        def ok(_r):
            self._status("Video library scan started — the movie list refreshes in 60 s", "OK")
            self.after(60_000, self._load_movies)
            self.after(60_000, self._refresh_library)

        self._run(self._client.scan_video, ok, lambda e: self._status(str(e), "ERROR"))

    def _refresh_library(self) -> None:
        if not self._client.configured:
            return
        client = self._client

        def work():
            return client.library_counts(), client.recently_added(15)

        def done(r):
            counts, recent = r
            self.stats_lbl.configure(
                text=f"🎬  {counts['movies']:,} movies\n📺  {counts['shows']:,} TV shows\n"
                     f"     {counts['episodes']:,} episodes")
            self._set_text(self.recent_box, recent, "Nothing recently added")

        self._run(work, done, lambda e: self._log(f"Library refresh failed: {e}", "WARN"))

    def _compare(self) -> None:
        if not self._client.configured:
            self._status("Set Kodi's address first", "WARN")
            return
        disk = []
        if self._get_disk_items:
            try:
                disk = self._get_disk_items() or []
            except Exception as exc:  # noqa: BLE001
                self._status(f"Could not read the media file list: {exc}", "ERROR")
                return
        if not disk:
            self.miss_lbl.configure(text="No files yet — run a Media Inspector scan first")
            self._set_text(self.miss_box, [], "")
            return
        self.miss_lbl.configure(text="Comparing…")
        client = self._client

        def done(kodi_names: set[str]):
            missing = sorted(
                (d for d in disk if _basename(str(d.get("path", ""))) not in kodi_names),
                key=lambda d: str(d.get("title", "")).lower(),
            )
            lines = [
                f"{'⚠ ' if d.get('is_corrupt') else ''}{d.get('title') or PurePath(str(d.get('path', ''))).name}"
                f"   —   {d.get('path', '')}"
                for d in missing
            ]
            self.miss_lbl.configure(text=f"{len(missing):,} of {len(disk):,} files not in Kodi")
            self._set_text(self.miss_box, lines, "Everything on disk is in Kodi ✔")

        def fail(e: Exception):
            self.miss_lbl.configure(text="Compare failed")
            self._status(f"Compare failed: {e}", "ERROR")

        self._run(client.library_filenames, done, fail)

    # ------------------------------------------------------------------ host API
    def notify_download_completed(self, name: str) -> None:
        """Called by the host app when SABnzbd reports a completed download (any thread)."""
        if self._closing or not self._client.configured:
            return
        want_scan = bool(self._cfg.get("kodi_auto_scan", True))
        want_notify = bool(self._cfg.get("kodi_notify_on_complete", True))
        if not (want_scan or want_notify):
            return
        with self._pending_lock:
            self._pending_names.append(name)
            if self._scan_timer is not None:
                self._scan_timer.cancel()
            # Debounce: wait for SABnzbd post-processing and batch several completions into one scan.
            self._scan_timer = threading.Timer(SCAN_DEBOUNCE_S, self._flush_completed)
            self._scan_timer.daemon = True
            self._scan_timer.start()

    def _flush_completed(self) -> None:
        with self._pending_lock:
            names, self._pending_names = self._pending_names, []
            self._scan_timer = None
        if self._closing or not names:
            return
        client = self._client
        want_scan = bool(self._cfg.get("kodi_auto_scan", True))
        want_notify = bool(self._cfg.get("kodi_notify_on_complete", True))
        msgs: list[tuple[str, str]] = []
        try:
            if want_notify:
                text = names[0] if len(names) == 1 else f"{len(names)} downloads: " + ", ".join(names[:3])
                client.notify("Download complete", text)
                msgs.append(("Notification sent to Kodi", "OK"))
            if want_scan:
                client.scan_video()
                msgs.append((f"Library scan started after {len(names)} completed download(s)", "OK"))
        except Exception as exc:  # noqa: BLE001
            msgs.append((f"Post-download action failed: {exc}", "ERROR"))
        for m, lvl in msgs:
            self._results.put((lambda _r, m=m, lvl=lvl: self._status(m, lvl), None, None))
        if want_scan:
            # Refresh "Recently added" once Kodi has had a moment to scan.
            self._results.put((lambda _r: self.after(60_000, self._refresh_library), None, None))

    def shutdown(self) -> None:
        self._closing = True
        with self._pending_lock:
            if self._scan_timer is not None:
                self._scan_timer.cancel()
                self._scan_timer = None
        for job in (self._poll_job, self._pump_job):
            if job:
                try:
                    self.after_cancel(job)
                except Exception:  # noqa: BLE001
                    pass
        self._pool.shutdown(wait=False, cancel_futures=True)


def _fmt_time(t: dict | None) -> str:
    if not t:
        return "0:00"
    h, m, s = int(t.get("hours", 0)), int(t.get("minutes", 0)), int(t.get("seconds", 0))
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
