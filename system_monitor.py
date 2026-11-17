"""
System Command Center: data layer (no GUI imports).

Collects live CPU, memory, GPU, drive, network, process and Windows stats in background
threads and exposes them as plain dictionaries for the interface in system_command_center.py.

Sources:
    psutil                      CPU, memory, drives, disk/network I/O, processes, battery
    PowerShell (one process     GPU load and VRAM for every vendor (Windows GPU performance
    that keeps running)         counters, the same ones Task Manager reads), real CPU clock,
                                and temperatures/fans when LibreHardwareMonitor is running
    nvidia-smi (streaming)      NVIDIA temperature, power, fan and clocks
    PowerShell (one-shot)       Windows, hardware, physical disk health, Defender, firewall
    Registry / Win32            DirectX adapter names, pending reboot, startup apps, actions

Every source is optional: if one is missing (not Windows, no NVIDIA card, no admin rights)
its values are None and the interface shows a dash instead of failing.
"""

from __future__ import annotations

import atexit
import base64
import ctypes
import heapq
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import psutil

IS_WINDOWS = platform.system() == "Windows"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HISTORY_LEN = 120          # samples kept for graphs (1 per second = 2 minutes)
CONFIG_FILE = Path(__file__).resolve().parent / "system_center_config.json"

DEFAULT_SETTINGS = {
    "cpu_alert": 90,          # % sustained
    "ram_alert": 90,          # % sustained
    "cpu_temp_alert": 90,     # °C
    "gpu_temp_alert": 85,     # °C
    "vram_alert": 95,         # %
    "drive_free_alert": 10,   # % free left on a volume
    "disk_temp_alert": 55,    # °C for hard drives and SATA SSDs; NVMe drives get +15 (see disk_temp_limit)
    "battery_alert": 15,      # % while discharging
    "alert_sustain_s": 30,    # how long CPU/RAM/temperature must stay high before alerting
    "toasts": True,
    "ping_enabled": True,
    "ping_host": "1.1.1.1",
    "lhm_port": 8085,         # LibreHardwareMonitor "Remote Web Server" port
    "always_on_top": False,
    "window": "",
    "last_page": "dashboard",
}


# ---------------------------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------------------------

def fmt_bytes(num: float | int | None, digits: int = 1) -> str:
    if num is None:
        return "—"
    num = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(num)} B"
            return f"{num:.{digits}f} {unit}"
        num /= 1024
    return f"{num:.{digits}f} PB"


def fmt_rate(bps: float | None) -> str:
    return "—" if bps is None else fmt_bytes(bps) + "/s"


def fmt_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def fmt_pct(value: float | None, digits: int = 0) -> str:
    return "—" if value is None else f"{value:.{digits}f}%"


def fmt_temp(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f}°C"


def fmt_ago(iso_or_ts) -> str:
    """'3 days ago' style text for an ISO date string or epoch seconds."""
    ts = parse_date(iso_or_ts)
    if ts is None:
        return "—"
    delta = time.time() - ts
    if delta < 0:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)} min ago"
    if delta < 86400:
        return f"{int(delta // 3600)} h ago"
    days = int(delta // 86400)
    return f"{days} day{'s' if days != 1 else ''} ago"


def fmt_date(iso_or_ts, with_time: bool = False) -> str:
    ts = parse_date(iso_or_ts)
    if ts is None:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d")


def parse_date(value) -> float | None:
    """Epoch seconds from an ISO string, a PowerShell '/Date(ms)/' string, epoch number or dict."""
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        value = value.get("value") or value.get("DateTime")
        if value is None:
            return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    m = re.search(r"/Date\((-?\d+)", s)
    if m:
        return int(m.group(1)) / 1000.0
    try:
        # Python < 3.11 can't parse 7-digit fractions that .NET 'o' format emits: trim them
        s2 = re.sub(r"(\.\d{6})\d+", r"\1", s).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            return dt.timestamp()
        return dt.astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def safe_num(value) -> float | None:
    """Number from a tool's text field; None for 'N/A', '[Not Supported]', '' and similar."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().strip("[]").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        m = re.match(r"^\s*(-?\d+(?:\.\d+)?)", s)
        return float(m.group(1)) if m else None


def as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


# ---------------------------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------------------------

def _ps_args(script: str) -> list[str]:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-EncodedCommand", encoded]


_PS_PRELUDE = ("$ErrorActionPreference = 'SilentlyContinue'\n"
               "$ProgressPreference = 'SilentlyContinue'\n"
               "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n")


def parse_json_output(text: str):
    """The last JSON value printed by a script (earlier lines may be warnings)."""
    for line in reversed((text or "").strip().splitlines()):
        line = line.strip()
        if line.startswith(("{", "[")):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def run_powershell_json(script: str, timeout: float = 90):
    if not IS_WINDOWS:
        return None
    try:
        proc = subprocess.run(_ps_args(_PS_PRELUDE + script), capture_output=True, timeout=timeout,
                              creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
    except Exception:
        return None
    return parse_json_output(proc.stdout.decode("utf-8", errors="replace"))


def run_cmd(args: list[str], timeout: float = 30) -> tuple[int, str]:
    """Runs a console command; returns (exit code, combined output). Never raises."""
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=NO_WINDOW,
                              stdin=subprocess.DEVNULL)
        out = proc.stdout + proc.stderr
        enc = "oem" if IS_WINDOWS else "utf-8"
        try:
            text = out.decode(enc, errors="replace")
        except LookupError:
            text = out.decode("utf-8", errors="replace")
        return proc.returncode, text
    except FileNotFoundError:
        return 127, f"{args[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


class _KillOnCloseJob:
    """Windows job object: helper processes we put in it die when this app exits, even on a crash."""

    def __init__(self):
        self.handle = None
        if not IS_WINDOWS:
            return
        try:
            k32 = ctypes.windll.kernel32
            k32.CreateJobObjectW.restype = ctypes.c_void_p
            k32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]

            class BASIC(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                            ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                            ("SchedulingClass", ctypes.c_uint32)]

            class EXTENDED(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

            handle = k32.CreateJobObjectW(None, None)
            info = EXTENDED()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if handle and k32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
                self.handle = handle
        except Exception:
            self.handle = None

    def add(self, popen: subprocess.Popen) -> None:
        if not self.handle:
            return
        try:
            ctypes.windll.kernel32.AssignProcessToJobObject(self.handle, int(popen._handle))  # type: ignore[attr-defined]
        except Exception:
            pass


_JOB = _KillOnCloseJob()


class StreamProcess:
    """Keeps a line-printing helper process running and hands each line to a callback."""

    def __init__(self, name: str, args: list[str], on_line, restart_delay: float = 5.0):
        self.name = name
        self.args = args
        self.on_line = on_line
        self.restart_delay = restart_delay
        self.running = False
        self.lines_seen = 0
        self.last_error = ""
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"stream-{self.name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._kill()

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass

    def _run(self) -> None:
        delay = self.restart_delay
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self._proc = subprocess.Popen(self.args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                              stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
                _JOB.add(self._proc)
                self.running = True
                for raw in self._proc.stdout:  # type: ignore[union-attr]
                    if self._stop.is_set():
                        break
                    line = raw.decode("utf-8", errors="replace").strip()
                    if line:
                        self.lines_seen += 1
                        try:
                            self.on_line(line)
                        except Exception as exc:  # noqa: BLE001
                            self.last_error = f"parse: {exc}"
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
            finally:
                self.running = False
                self._kill()
            if self._stop.wait(delay):
                break
            delay = self.restart_delay if time.monotonic() - started > 60 else min(delay * 2, 120)


# ---------------------------------------------------------------------------------------------
# GPU parsing
# ---------------------------------------------------------------------------------------------

NVSMI_FIELDS = ["index", "name", "driver_version", "utilization.gpu", "utilization.memory", "memory.used",
                "memory.total", "temperature.gpu", "power.draw", "power.limit", "fan.speed", "clocks.gr",
                "clocks.mem", "clocks.max.gr", "clocks.max.mem", "pstate", "pcie.link.gen.current",
                "pcie.link.width.current"]
NVSMI_FIELDS_MIN = ["index", "name", "driver_version", "utilization.gpu", "memory.used", "memory.total",
                    "temperature.gpu", "power.draw", "fan.speed"]
_NVSMI_TEXT = {"name", "driver_version", "pstate"}

VENDORS = {0x10DE: "NVIDIA", 0x1002: "AMD", 0x1022: "AMD", 0x8086: "Intel", 0x1414: "Microsoft",
           0x5143: "Qualcomm"}


def parse_nvidia_smi_line(line: str, fields: list[str]) -> dict | None:
    parts = [p.strip() for p in line.split(",")]
    if len(parts) != len(fields):
        return None
    row = {}
    for key, raw in zip(fields, parts):
        row[key] = raw if key in _NVSMI_TEXT else safe_num(raw)
    if row.get("index") is None:
        return None
    row["index"] = int(row["index"])
    return row


def find_nvidia_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    if IS_WINDOWS:
        for cand in (Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "nvidia-smi.exe",
                     Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"):
            if cand.exists():
                return str(cand)
    return None


_ENGINE_RE = re.compile(r"pid_(\d+)_luid_(0x[0-9a-f]+_0x[0-9a-f]+)_phys_(\d+)_eng_(\d+)_engtype_(.*)$", re.I)
_LUID_RE = re.compile(r"luid_(0x[0-9a-f]+_0x[0-9a-f]+)", re.I)


def parse_gpu_engines(lines: list[str]) -> tuple[dict, dict]:
    """GPU load from 'GPU Engine' counter lines ("<instance>=<percent>").

    Returns (adapters, per_process):
        adapters    {luid: {"util": %, "engines": {engine type: %}}}
        per_process {pid: %}
    Like Task Manager: an engine's load is the sum over processes, an adapter's load is its
    busiest engine, and a process's load is its busiest engine.
    """
    engine_load: dict[tuple, float] = {}
    engine_type: dict[tuple, str] = {}
    pid_engine: dict[tuple, float] = {}
    for line in as_list(lines):
        name, _, val = str(line).rpartition("=")
        m = _ENGINE_RE.search(name)
        v = safe_num(val)
        if not m or v is None:
            continue
        pid, luid, phys, eng, etype = int(m.group(1)), m.group(2).lower(), m.group(3), m.group(4), m.group(5)
        key = (luid, phys, eng)
        engine_load[key] = engine_load.get(key, 0.0) + v
        engine_type[key] = etype.strip() or "Other"
        pid_engine[(pid, key)] = pid_engine.get((pid, key), 0.0) + v
    adapters: dict[str, dict] = {}
    for key, load in engine_load.items():
        load = min(load, 100.0)
        a = adapters.setdefault(key[0], {"util": 0.0, "engines": {}})
        a["util"] = max(a["util"], load)
        et = engine_type[key]
        a["engines"][et] = max(a["engines"].get(et, 0.0), load)
    per_process: dict[int, float] = {}
    for (pid, _key), load in pid_engine.items():
        if pid:
            per_process[pid] = min(100.0, max(per_process.get(pid, 0.0), load))
    return adapters, per_process


def parse_gpu_memory(lines: list[str]) -> dict:
    """{luid: {"dedicated": bytes, "shared": bytes}} from "<instance>|<dedicated>|<shared>" lines."""
    out: dict[str, dict] = {}
    for line in as_list(lines):
        parts = str(line).split("|")
        if len(parts) < 3:
            continue
        m = _LUID_RE.search(parts[0])
        if not m:
            continue
        luid = m.group(1).lower()
        cur = out.setdefault(luid, {"dedicated": 0.0, "shared": 0.0})
        cur["dedicated"] += safe_num(parts[1]) or 0.0
        cur["shared"] += safe_num(parts[2]) or 0.0
    return out


def luid_from_qword(q: int) -> str:
    q = int(q) & 0xFFFFFFFFFFFFFFFF
    return f"0x{(q >> 32) & 0xFFFFFFFF:08x}_0x{q & 0xFFFFFFFF:08x}"


def read_dxgi_adapters() -> dict:
    """{luid: {"name", "vendor", "vram"}} from the DirectX adapter list in the registry."""
    out: dict[str, dict] = {}
    if not IS_WINDOWS:
        return out
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\DirectX") as root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, sub) as k:
                        luid = winreg.QueryValueEx(k, "AdapterLuid")[0]
                        desc = str(winreg.QueryValueEx(k, "Description")[0])
                        try:
                            vendor_id = int(winreg.QueryValueEx(k, "VendorId")[0])
                        except OSError:
                            vendor_id = 0
                        try:
                            vram = int(winreg.QueryValueEx(k, "DedicatedVideoMemory")[0])
                        except OSError:
                            vram = 0
                except (OSError, TypeError, ValueError):
                    continue
                out[luid_from_qword(luid)] = {"name": desc, "vendor": VENDORS.get(vendor_id, guess_vendor(desc)),
                                              "vram": vram, "vendor_id": vendor_id}
    except Exception:
        pass
    return out


def guess_vendor(name: str) -> str:
    n = (name or "").lower()
    if "nvidia" in n or "geforce" in n or "quadro" in n or "rtx" in n:
        return "NVIDIA"
    if "amd" in n or "radeon" in n:
        return "AMD"
    if "intel" in n or "arc" in n.split():
        return "Intel"
    if "microsoft" in n or "basic render" in n:
        return "Microsoft"
    return ""


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower().replace("nvidia", ""))


# ---------------------------------------------------------------------------------------------
# Sensors (LibreHardwareMonitor / OpenHardwareMonitor / ACPI) and CPU clock
# ---------------------------------------------------------------------------------------------

def parse_sensor_lines(lines: list[str]) -> dict:
    """Lines "<type>|<hardware>|<name>|<value>" -> {"temps": [...], "fans": [...], "power": [...]}."""
    out = {"temps": [], "fans": [], "power": []}
    bucket = {"temperature": "temps", "fan": "fans", "power": "power"}
    for line in as_list(lines):
        parts = str(line).split("|")
        if len(parts) < 4:
            continue
        kind = bucket.get(parts[0].strip().lower())
        value = safe_num(parts[3])
        if not kind or value is None:
            continue
        if kind == "temps" and not (-20 < value < 150):
            continue
        out[kind].append({"hw": parts[1].strip(), "name": parts[2].strip(), "value": value})
    return out


_CPU_TEMP_NAMES = ("cpu package", "core (tctl/tdie)", "cpu (tctl/tdie)", "tctl/tdie", "core max", "core average", "package")
_BOARD_CPU_NAMES = ("cpu package", "cpu core", "cpu", "cpu socket", "cputin")


def pick_cpu_temp(temps: list[dict]) -> float | None:
    """CPU temperature: the CPU's own sensors first, else the motherboard's "CPU" reading
    (on many boards, e.g. Nuvoton chips, LibreHardwareMonitor lists "CPU Core" there)."""
    cpu = [t for t in temps if "cpu" in t["hw"].lower()]
    for wanted in _CPU_TEMP_NAMES:
        for t in cpu:
            if t["name"].lower() == wanted:
                return t["value"]
    if cpu:
        return max(t["value"] for t in cpu)
    board = [t for t in temps if "cpu" in t["name"].lower() and "gpu" not in t["name"].lower()]
    for wanted in _BOARD_CPU_NAMES:
        for t in board:
            if t["name"].lower() == wanted:
                return t["value"]
    return board[0]["value"] if board else None


_LHM_KIND = {"temperature": "temps", "fan": "fans", "power": "power"}
_LHM_ICON_HW = (("cpu", "/cpu"), ("nvidia", "/gpu-nvidia"), ("ati", "/gpu-amd"), ("amd", "/gpu-amd"),
                ("intel", "/gpu-intel"), ("hdd", "/hdd"), ("nvme", "/nvme"), ("ram", "/ram"),
                ("chip", "/lpc"), ("mainboard", "/mainboard"))


def parse_lhm_json(data) -> dict:
    """Sensors from LibreHardwareMonitor's web server (http://localhost:8085/data.json).

    The tree is computer > hardware (> sub-hardware) > group ("Temperatures") > sensor. Each
    sensor dict gets "hw" (an id like "/lpc/nct6798d/0" used to recognise CPU/GPU sensors),
    "label" (the hardware's display name), "name" and "value".
    """
    out = {"temps": [], "fans": [], "power": []}

    def kind_of(node: dict) -> str | None:
        t = str(node.get("Type") or "").lower()
        if t:
            return _LHM_KIND.get(t)
        v = str(node.get("Value") or "")
        if "°C" in v:
            return "temps"
        if v.endswith("RPM"):
            return "fans"
        if v.endswith(" W"):
            return "power"
        return None

    def walk(node: dict, parents: list[dict]) -> None:
        kids = node.get("Children") or []
        if kids:
            for k in kids:
                if isinstance(k, dict):
                    walk(k, parents + [node])
            return
        kind = kind_of(node)
        value = safe_num(node.get("Value"))
        if not kind or value is None or len(parents) < 2:
            return
        if kind == "temps" and not (-20 < value < 150):
            return
        hw_node = parents[-2]  # parents[-1] is the "Temperatures"/"Fans" group
        sid = str(node.get("SensorId") or "")
        if sid.count("/") >= 3:
            hw = "/".join(sid.rstrip("/").split("/")[:-2])
        else:
            icon = str(hw_node.get("ImageURL") or "").lower()
            hw = next((h for key, h in _LHM_ICON_HW if key in icon), "/hw") + "/" + str(hw_node.get("Text") or "")
        out[kind].append({"hw": hw, "label": str(hw_node.get("Text") or ""), "name": str(node.get("Text") or ""),
                          "value": value})

    if isinstance(data, dict):
        walk(data, [])
    return out


def fetch_lhm_http(port: int, timeout: float = 2.0) -> dict | None:
    """Reads LibreHardwareMonitor's web server on this PC; None if it isn't running."""
    import urllib.request
    try:
        port = int(port)
        if not 0 < port < 65536:
            return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # localhost: never via a proxy
        with opener.open(f"http://127.0.0.1:{port}/data.json", timeout=timeout) as resp:
            data = json.loads(resp.read(8_000_000).decode("utf-8", errors="replace"))
    except Exception:
        return None
    return parse_lhm_json(data)


def pick_gpu_sensor(temps: list[dict], vendor: str) -> float | None:
    tag = {"AMD": "gpu-amd", "Intel": "gpu-intel", "NVIDIA": "gpu-nvidia"}.get(vendor)
    if not tag:
        return None
    for t in temps:
        if tag in t["hw"].lower() and t["name"].lower() in ("gpu core", "gpu temperature", "gpu"):
            return t["value"]
    vals = [t["value"] for t in temps if tag in t["hw"].lower()]
    return max(vals) if vals else None


# ---------------------------------------------------------------------------------------------
# Drives
# ---------------------------------------------------------------------------------------------

_MEDIA = {"0": "Unspecified", "3": "HDD", "4": "SSD", "5": "SCM", "hdd": "HDD", "ssd": "SSD", "scm": "SCM",
          "unspecified": "Unspecified"}
_BUS = {"1": "SCSI", "2": "ATAPI", "3": "ATA", "4": "1394", "5": "SSA", "6": "Fibre", "7": "USB", "8": "RAID",
        "9": "iSCSI", "10": "SAS", "11": "SATA", "12": "SD", "13": "MMC", "15": "File-backed", "16": "Spaces",
        "17": "NVMe", "18": "SCM", "19": "UFS"}
_HEALTH = {"0": "Healthy", "1": "Warning", "2": "Unhealthy", "5": "Unknown"}


def media_type_name(v) -> str:
    return _MEDIA.get(str(v).strip().lower(), str(v) if v not in (None, "") else "Unknown")


def bus_type_name(v) -> str:
    s = str(v).strip()
    return _BUS.get(s, s or "Unknown")


def health_name(v) -> str:
    s = str(v).strip()
    return _HEALTH.get(s, s or "Unknown")


NVME_TEMP_EXTRA = 15  # NVMe SSDs are rated to ~70 °C and run 40-55 °C idle: their limit is higher


def disk_temp_limit(disk: dict, setting: float) -> float:
    """Alert temperature for a physical disk. Hard drives and SATA SSDs like to stay cooler
    than NVMe drives, which routinely run at 50-65 °C under load."""
    if disk.get("bus_name") == "NVMe" or "nvme" in str(disk.get("name", "")).lower():
        return setting + NVME_TEMP_EXTRA
    return setting


def parse_firewall_enabled(v) -> bool:
    return str(v).strip().lower() in ("true", "1")


# ---------------------------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------------------------

_PING_RE = re.compile(r"[=<]\s*(\d+(?:[.,]\d+)?)\s*ms", re.I)


def parse_ping_ms(text: str) -> float | None:
    """Round-trip time from one ping reply (works with localized Windows and Linux output)."""
    for line in (text or "").splitlines():
        if "ttl" not in line.lower():
            continue
        m = _PING_RE.search(line)
        if m:
            return float(m.group(1).replace(",", "."))
    return None


def ping_once(host: str, timeout_ms: int = 1500) -> float | None:
    if not re.fullmatch(r"[A-Za-z0-9.\-:]{1,253}", host or ""):
        return None
    if IS_WINDOWS:
        args = ["ping", "-n", "1", "-w", str(timeout_ms), host]
    else:
        args = ["ping", "-c", "1", "-W", str(max(1, timeout_ms // 1000)), host]
    _code, out = run_cmd(args, timeout=timeout_ms / 1000 + 3)
    return parse_ping_ms(out)


def parse_netsh_wlan(text: str) -> dict:
    """SSID and signal from 'netsh wlan show interfaces' (English labels; others are skipped)."""
    out: dict = {}
    for line in (text or "").splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip().lower(), value.strip()
        if key == "ssid":
            out["ssid"] = value
        elif key == "signal":
            out["signal"] = safe_num(value.rstrip("%"))
        elif key in ("receive rate (mbps)", "transmit rate (mbps)"):
            out["rx_rate" if key.startswith("receive") else "tx_rate"] = safe_num(value)
        elif key == "radio type":
            out["radio"] = value
    return out


# ---------------------------------------------------------------------------------------------
# Power plans, startup apps, pending reboot
# ---------------------------------------------------------------------------------------------

_POWERCFG_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s*\(([^)]*)\)\s*(\*)?", re.I)


def parse_powercfg_list(text: str) -> list[dict]:
    return [{"guid": m.group(1).lower(), "name": m.group(2).strip(), "active": bool(m.group(3))}
            for m in _POWERCFG_RE.finditer(text or "")]


def power_plans() -> list[dict]:
    if not IS_WINDOWS:
        return []
    _code, out = run_cmd(["powercfg", "/list"], timeout=10)
    return parse_powercfg_list(out)


def set_power_plan(guid: str) -> tuple[bool, str]:
    if not re.fullmatch(r"[0-9a-f-]{36}", guid or ""):
        return False, "invalid plan"
    code, out = run_cmd(["powercfg", "/setactive", guid], timeout=10)
    return code == 0, out.strip() or ("Power plan changed" if code == 0 else "powercfg failed")


def startup_entry_enabled(data: bytes | None) -> bool:
    """StartupApproved value: first byte even (02/06) = enabled, odd (03/07) = disabled."""
    if not data:
        return True
    return data[0] % 2 == 0


_RUN_KEYS = [
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run",
     r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"),
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
     r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run",
     r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32"),
]


def list_startup_apps() -> list[dict]:
    """Startup programs from the Run keys and Startup folders, with their enabled state."""
    items: list[dict] = []
    if not IS_WINDOWS:
        return items
    import winreg
    hives = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}

    def approved(hive, key_path: str, name: str) -> bool:
        try:
            with winreg.OpenKey(hives[hive], key_path) as k:
                return startup_entry_enabled(winreg.QueryValueEx(k, name)[0])
        except OSError:
            return True

    for hive, run_key, approved_key in _RUN_KEYS:
        try:
            with winreg.OpenKey(hives[hive], run_key) as k:
                i = 0
                while True:
                    try:
                        name, value, _t = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    i += 1
                    items.append({"name": name, "command": str(value), "scope": "This user" if hive == "HKCU" else "All users",
                                  "hive": hive, "approved_key": approved_key,
                                  "enabled": approved(hive, approved_key, name)})
        except OSError:
            continue
    folders = [("This user", "HKCU", Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup"),
               ("All users", "HKLM", Path(os.environ.get("PROGRAMDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\StartUp")]
    for scope, hive, folder in folders:
        try:
            for f in folder.iterdir():
                if f.name.lower() == "desktop.ini":
                    continue
                key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"
                if hive == "HKLM":
                    key = key.replace("Software", "SOFTWARE")
                items.append({"name": f.stem, "command": str(f), "scope": scope, "hive": hive,
                              "approved_key": key, "value_name": f.name, "enabled": approved(hive, key, f.name)})
        except OSError:
            continue
    items.sort(key=lambda x: x["name"].lower())
    return items


def set_startup_enabled(item: dict, enabled: bool) -> tuple[bool, str]:
    """Enables/disables a startup entry the same way Task Manager does (StartupApproved key)."""
    if not IS_WINDOWS:
        return False, "Windows only"
    import winreg
    hive = winreg.HKEY_CURRENT_USER if item.get("hive") == "HKCU" else winreg.HKEY_LOCAL_MACHINE
    name = item.get("value_name") or item["name"]
    data = bytes([0x02 if enabled else 0x03]) + bytes(11)
    if not enabled:
        ft = int((time.time() + 11644473600) * 10_000_000)  # FILETIME of when it was disabled
        data = bytes([0x03, 0, 0, 0]) + ft.to_bytes(8, "little")
    try:
        with winreg.CreateKeyEx(hive, item["approved_key"], 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_BINARY, data)
        return True, f"{item['name']} {'enabled' if enabled else 'disabled'} at startup"
    except PermissionError:
        return False, "Needs administrator rights (entry is for all users)"
    except OSError as exc:
        return False, str(exc)


def pending_reboot() -> bool | None:
    if not IS_WINDOWS:
        return None
    try:
        import winreg
        for path in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
                     r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending"):
            try:
                winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path))
                return True
            except OSError:
                pass
        return False
    except Exception:
        return None


def windows_version_info() -> dict:
    """DisplayVersion (e.g. 24H2) and full build number from the registry."""
    out: dict = {}
    if not IS_WINDOWS:
        out["caption"] = f"{platform.system()} {platform.release()}"
        return out
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            def q(name):
                try:
                    return winreg.QueryValueEx(k, name)[0]
                except OSError:
                    return None
            out["display_version"] = q("DisplayVersion") or q("ReleaseId")
            build, ubr = q("CurrentBuild"), q("UBR")
            out["build"] = f"{build}.{ubr}" if build and ubr is not None else build
            out["edition"] = q("EditionID")
    except Exception:
        pass
    return out


def is_admin() -> bool:
    if IS_WINDOWS:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return hasattr(os, "geteuid") and os.geteuid() == 0


# ---------------------------------------------------------------------------------------------
# Windows inventory (one-shot PowerShell, a few seconds)
# ---------------------------------------------------------------------------------------------

INVENTORY_PS = r"""
function D($d) { if ($d) { try { ([datetime]$d).ToString('o') } catch { $null } } else { $null } }
$r = @{}
try { $o = Get-CimInstance Win32_OperatingSystem; $r.os = @{ caption = $o.Caption; version = $o.Version; build = $o.BuildNumber; arch = $o.OSArchitecture; installed = (D $o.InstallDate); boot = (D $o.LastBootUpTime) } } catch {}
try { $c = Get-CimInstance Win32_ComputerSystem; $r.computer = @{ name = $c.Name; maker = $c.Manufacturer; model = $c.Model; ram = [double]$c.TotalPhysicalMemory } } catch {}
try { $b = Get-CimInstance Win32_BIOS; $r.bios = @{ version = $b.SMBIOSBIOSVersion; date = (D $b.ReleaseDate); maker = $b.Manufacturer } } catch {}
try { $m = Get-CimInstance Win32_BaseBoard; $r.board = @{ maker = $m.Manufacturer; product = $m.Product } } catch {}
try { $r.cpu = @(Get-CimInstance Win32_Processor | ForEach-Object { @{ name = ("" + $_.Name).Trim(); cores = $_.NumberOfCores; threads = $_.NumberOfLogicalProcessors; maxmhz = $_.MaxClockSpeed; socket = $_.SocketDesignation; l2 = $_.L2CacheSize; l3 = $_.L3CacheSize; virt = $_.VirtualizationFirmwareEnabled } }) } catch {}
try { $r.ram = @(Get-CimInstance Win32_PhysicalMemory | ForEach-Object { @{ slot = $_.DeviceLocator; size = [double]$_.Capacity; speed = $_.Speed; configured = $_.ConfiguredClockSpeed; maker = $_.Manufacturer; part = ("" + $_.PartNumber).Trim() } }) } catch {}
try { $r.video = @(Get-CimInstance Win32_VideoController | ForEach-Object { @{ name = $_.Name; driver = $_.DriverVersion; driver_date = (D $_.DriverDate); w = $_.CurrentHorizontalResolution; h = $_.CurrentVerticalResolution; hz = $_.CurrentRefreshRate; status = $_.Status } }) } catch {}
try { $r.disks = @(Get-PhysicalDisk | ForEach-Object {
    $rc = $null; try { $rc = $_ | Get-StorageReliabilityCounter } catch {}
    @{ id = "$($_.DeviceId)"; name = $_.FriendlyName; media = "$($_.MediaType)"; bus = "$($_.BusType)"; size = [double]$_.Size;
       health = "$($_.HealthStatus)"; status = "$($_.OperationalStatus)"; spindle = $_.SpindleSpeed;
       temp = $(if ($rc) { $rc.Temperature } else { $null }); temp_max = $(if ($rc) { $rc.TemperatureMax } else { $null });
       wear = $(if ($rc) { $rc.Wear } else { $null }); hours = $(if ($rc) { $rc.PowerOnHours } else { $null });
       read_errors = $(if ($rc) { $rc.ReadErrorsTotal } else { $null }); write_errors = $(if ($rc) { $rc.WriteErrorsTotal } else { $null }) } }) } catch {}
try { $r.parts = @(Get-Partition | Where-Object { $_.DriveLetter } | ForEach-Object { @{ disk = "$($_.DiskNumber)"; letter = "$($_.DriveLetter)" } }) } catch {}
try { $r.volumes = @(Get-Volume | Where-Object { $_.DriveLetter } | ForEach-Object { @{ letter = "$($_.DriveLetter)"; label = $_.FileSystemLabel; fs = $_.FileSystem; health = "$($_.HealthStatus)" } }) } catch {}
try { $d = Get-MpComputerStatus; $r.defender = @{ av = $d.AntivirusEnabled; rtp = $d.RealTimeProtectionEnabled; sig = (D $d.AntivirusSignatureLastUpdated); quick = (D $d.QuickScanEndTime); full = (D $d.FullScanEndTime); tamper = $d.IsTamperProtected } } catch {}
try { $r.av = @(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | ForEach-Object { $_.displayName }) } catch {}
try { $r.firewall = @(Get-NetFirewallProfile | ForEach-Object { @{ name = "$($_.Name)"; enabled = "$($_.Enabled)" } }) } catch {}
try { $r.hotfix = @(Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 6 | ForEach-Object { @{ id = $_.HotFixID; desc = $_.Description; date = (D $_.InstalledOn) } }) } catch {}
$r | ConvertTo-Json -Compress -Depth 5
"""

EVENTS_PS = r"""
function D($d) { if ($d) { try { ([datetime]$d).ToString('o') } catch { $null } } else { $null } }
$r = @{}
try { $r.events = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; Level = 1,2; StartTime = (Get-Date).AddDays(-2) } -MaxEvents 40 | ForEach-Object {
    $msg = ("" + $_.Message) -replace '\s+', ' '; if ($msg.Length -gt 260) { $msg = $msg.Substring(0, 260) + '...' }
    @{ time = (D $_.TimeCreated); level = $_.LevelDisplayName; source = $_.ProviderName; id = $_.Id; msg = $msg } }) } catch { $r.events = @() }
try { $r.unexpected = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-Kernel-Power'; Id = 41; StartTime = (Get-Date).AddDays(-30) }).Count } catch { $r.unexpected = 0 }
try { $r.bsod = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-WER-SystemErrorReporting'; Id = 1001; StartTime = (Get-Date).AddDays(-30) }).Count } catch { $r.bsod = 0 }
$r | ConvertTo-Json -Compress -Depth 4
"""

WINDOWS_UPDATE_PS = r"""
$r = @{ ok = $false; updates = @() }
try {
  $s = New-Object -ComObject Microsoft.Update.Session
  $res = $s.CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0 and Type='Software'")
  $r.updates = @($res.Updates | ForEach-Object { @{ title = $_.Title; size = [double]$_.MaxDownloadSize; reboot = $_.RebootRequired } })
  $r.ok = $true
} catch { $r.error = "" + $_.Exception.Message }
$r | ConvertTo-Json -Compress -Depth 4
"""

# Sensor loop: one PowerShell process that keeps printing a JSON line every couple of seconds.
# Exits by itself if this app's process disappears. Temperatures come only from Libre/Open
# HardwareMonitor: the ACPI thermal zone Windows offers is a fixed fake value on most desktops.
SENSOR_PS = r"""
$parentId = __PARENT__
try { $parent = [System.Diagnostics.Process]::GetProcessById($parentId) } catch { exit }
$i = 0; $lhm = $true
while ($true) {
  if ($parent.HasExited) { exit }
  $o = @{ t = $i }
  try { $o.eng = @(Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine | Where-Object { $_.UtilizationPercentage -gt 0 } | ForEach-Object { "$($_.Name)=$($_.UtilizationPercentage)" }); $o.gpuok = $true } catch { $o.gpuok = $false }
  try { $o.mem = @(Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory | ForEach-Object { "$($_.Name)|$($_.DedicatedUsage)|$($_.SharedUsage)" }) } catch {}
  try { $p = Get-CimInstance Win32_PerfFormattedData_Counters_ProcessorInformation -Filter "Name='_Total'"; $o.cpuperf = "$($p.ProcessorFrequency)|$($p.PercentProcessorPerformance)" } catch {}
  if ($i % 3 -eq 0) {
    $s = $null
    if ($lhm -or ($i % 30 -eq 0)) {
      foreach ($ns in 'root/LibreHardwareMonitor', 'root/OpenHardwareMonitor') {
        try { $s = @(Get-CimInstance -Namespace $ns -ClassName Sensor -ErrorAction Stop | Where-Object { $_.SensorType -in 'Temperature', 'Fan', 'Power' } | ForEach-Object { "$($_.SensorType)|$($_.Parent)|$($_.Name)|$($_.Value)" }); $o.sensrc = $ns.Split('/')[1]; break } catch {}
      }
      $lhm = [bool]$s
    }
    if ($s) { $o.sens = $s }
  }
  $o | ConvertTo-Json -Compress -Depth 3
  [Console]::Out.Flush()
  $i++
  Start-Sleep -Milliseconds __INTERVAL__
}
"""


# ---------------------------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------------------------

class AlertEngine:
    """Turns readings into alerts, with a 'must stay high for N seconds' rule and a cooldown."""

    def __init__(self, on_alert=None):
        self.on_alert = on_alert
        self.items: deque = deque(maxlen=200)
        self.unread = 0
        self._since: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def check(self, key: str, active: bool, level: str, title: str, message: str,
              sustain: float = 0, cooldown: float = 900, now: float | None = None) -> dict | None:
        now = time.time() if now is None else now
        with self._lock:
            if not active:
                self._since.pop(key, None)
                return None
            start = self._since.setdefault(key, now)
            if now - start < sustain or now - self._last.get(key, -1e18) < cooldown:
                return None
            self._last[key] = now
            item = {"time": now, "level": level, "title": title, "message": message, "key": key}
            self.items.appendleft(item)
            self.unread += 1
        if self.on_alert:
            try:
                self.on_alert(item)
            except Exception:
                pass
        return item

    def clear(self) -> None:
        with self._lock:
            self.items.clear()
            self.unread = 0

    def evaluate(self, snap: dict, s: dict, now: float | None = None) -> None:
        sustain = float(s.get("alert_sustain_s", 30))
        cpu = snap.get("cpu", {})
        mem = snap.get("mem", {})
        if cpu.get("total") is not None:
            self.check("cpu", cpu["total"] >= s["cpu_alert"], "warning", "CPU running hot",
                       f"CPU load has stayed above {s['cpu_alert']}% for {int(sustain)} s ({cpu['total']:.0f}% now).",
                       sustain, now=now)
        if cpu.get("temp") is not None:
            self.check("cpu_temp", cpu["temp"] >= s["cpu_temp_alert"], "critical", "CPU temperature high",
                       f"CPU is at {cpu['temp']:.0f}°C (limit {s['cpu_temp_alert']}°C).", sustain / 3, now=now)
        if mem.get("percent") is not None:
            self.check("ram", mem["percent"] >= s["ram_alert"], "warning", "Memory almost full",
                       f"RAM use is {mem['percent']:.0f}% ({fmt_bytes(mem.get('available'))} free).", sustain, now=now)
        for g in snap.get("gpus", []):
            name = g.get("name") or "GPU"
            if g.get("temp") is not None:
                self.check(f"gpu_temp:{g['key']}", g["temp"] >= s["gpu_temp_alert"], "critical", "GPU temperature high",
                           f"{name} is at {g['temp']:.0f}°C (limit {s['gpu_temp_alert']}°C).", sustain / 3, now=now)
            if g.get("vram_used") and g.get("vram_total"):
                pct = 100 * g["vram_used"] / g["vram_total"]
                self.check(f"vram:{g['key']}", pct >= s["vram_alert"], "warning", "GPU memory almost full",
                           f"{name} VRAM is {pct:.0f}% used ({fmt_bytes(g['vram_used'])} of {fmt_bytes(g['vram_total'])}).",
                           sustain, now=now)
        for v in snap.get("volumes", []):
            free_pct = 100 - v["percent"]
            self.check(f"vol:{v['mount']}", free_pct <= s["drive_free_alert"], "warning", f"Drive {v['mount']} almost full",
                       f"Only {fmt_bytes(v['free'])} ({free_pct:.0f}%) left on {v['mount']}.", 0, 6 * 3600, now=now)
        for d in snap.get("inventory", {}).get("disks", []):
            health = d.get("health_name")
            self.check(f"health:{d.get('id')}", health in ("Warning", "Unhealthy"), "critical", "Drive health problem",
                       f"{d.get('name')} reports health: {health}. Back up its data.", 0, 24 * 3600, now=now)
            t = safe_num(d.get("temp"))
            if t:
                limit = disk_temp_limit(d, s["disk_temp_alert"])
                self.check(f"disktemp:{d.get('id')}", t >= limit, "warning", "Drive running hot",
                           f"{d.get('name')} is at {t:.0f}°C (limit {limit:.0f}°C).", 120, 3600, now=now)
        bat = snap.get("battery")
        if bat and bat.get("percent") is not None:
            self.check("battery", (not bat.get("plugged")) and bat["percent"] <= s["battery_alert"], "warning",
                       "Battery low", f"Battery at {bat['percent']:.0f}% and discharging.", 0, 1800, now=now)


# ---------------------------------------------------------------------------------------------
# Windows toast notifications (no extra packages; same approach as the NZB app)
# ---------------------------------------------------------------------------------------------

_TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$t = [Security.SecurityElement]::Escape($env:SCC_TOAST_TITLE)
$m = [Security.SecurityElement]::Escape($env:SCC_TOAST_MSG)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$m</text></binding></visual></toast>")
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show($toast)
"""


def show_toast(title: str, message: str) -> bool:
    if not IS_WINDOWS:
        return False
    env = dict(os.environ, SCC_TOAST_TITLE=title[:120], SCC_TOAST_MSG=message[:400])

    def run() -> None:
        try:
            subprocess.run(_ps_args(_TOAST_PS), env=env, capture_output=True, timeout=20, creationflags=NO_WINDOW)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()
    return True


# ---------------------------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------------------------

def load_settings(path: Path | None = None) -> dict:
    path = path or CONFIG_FILE
    s = dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for k, v in data.items():
                if k in DEFAULT_SETTINGS and type(v) is type(DEFAULT_SETTINGS[k]):
                    s[k] = v
                elif k in DEFAULT_SETTINGS and isinstance(DEFAULT_SETTINGS[k], (int, float)) and isinstance(v, (int, float)) \
                        and not isinstance(v, bool):
                    s[k] = v
    except (OSError, ValueError):
        pass
    return s


def save_settings(s: dict, path: Path | None = None) -> None:
    path = path or CONFIG_FILE
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(s, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


# ---------------------------------------------------------------------------------------------
# The monitor
# ---------------------------------------------------------------------------------------------

PROTECTED_PROCESSES = {"system", "system idle process", "registry", "smss.exe", "csrss.exe", "wininit.exe",
                       "winlogon.exe", "services.exe", "lsass.exe", "lsaiso.exe", "memory compression",
                       "secure system", "fontdrvhost.exe", "dwm.exe", "svchost.exe", "msmpeng.exe"}


class SystemMonitor:
    """Samples the system in background threads. The UI reads snapshot() and hist()."""

    def __init__(self, settings: dict | None = None, enable_external: bool = True):
        self.settings = settings if settings is not None else load_settings()
        self.enable_external = enable_external and IS_WINDOWS
        self.alerts = AlertEngine(on_alert=self._toast)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._hist: dict[str, deque] = {}
        self.cpu_count = psutil.cpu_count() or 1
        self.started = time.time()

        self._state: dict = {
            "cpu": {}, "mem": {}, "disk_io": {}, "disk_total": {}, "net": {}, "volumes": [], "battery": None,
            "gpus": [], "processes": [], "ping": {}, "inventory": {}, "sensors": {"temps": [], "fans": [], "power": []},
            "sensor_source": "", "uptime": None, "boot": psutil.boot_time(), "admin": is_admin(),
            "winver": windows_version_info(), "pending_reboot": None, "power_plans": [], "wifi": {},
            "connections": None, "inventory_loading": False,
        }
        self._gpu_counters: dict = {"adapters": {}, "per_process": {}, "mem": {}, "ok": None}
        self._nvsmi: dict[int, dict] = {}
        self._nvsmi_fields = NVSMI_FIELDS
        self._dxgi: dict = {}
        self._cpuperf: tuple[float, float] | None = None
        self._wmi_sensors: dict | None = None
        self._wmi_src = ""
        self._http_sensors: dict | None = None
        self._http_time = 0.0
        self._prev_disk = None
        self._prev_net = None
        self._prev_ctx = None
        self._prev_proc_io: dict[int, tuple[float, int]] = {}
        self._session_net = [0, 0]
        self._streams: list[StreamProcess] = []
        self.sources: dict[str, str] = {}

    # ---- lifecycle ----
    def start(self) -> None:
        atexit.register(self.stop)
        psutil.cpu_percent(percpu=True)  # prime the counters so the first real sample is meaningful
        self._spawn(self._fast_loop, "fast")
        self._spawn(self._proc_loop, "procs")
        self._spawn(self._volume_loop, "volumes")
        self._spawn(self._ping_loop, "ping")
        self._spawn(self._slow_loop, "slow")
        self.refresh_inventory()
        if self.enable_external:
            self._dxgi = read_dxgi_adapters()
            self._start_sensor_stream()
            self._spawn(self._lhm_http_loop, "lhm-http")
            self._start_nvidia_stream()
        else:
            self.sources["GPU counters"] = "Windows only"
            self.sources["nvidia-smi"] = "not used" if not IS_WINDOWS else "off"

    def stop(self) -> None:
        self._stop.set()
        for s in self._streams:
            s.stop()

    def _spawn(self, fn, name: str) -> None:
        t = threading.Thread(target=self._guard(fn), name=f"mon-{name}", daemon=True)
        self._threads.append(t)
        t.start()

    def _guard(self, fn):
        def run():
            while not self._stop.is_set():
                try:
                    fn()
                    return
                except Exception:  # keep the monitor alive whatever a source does
                    if self._stop.wait(3):
                        return
        return run

    # ---- public read API ----
    def snapshot(self) -> dict:
        with self._lock:
            snap = dict(self._state)
            snap["alerts_unread"] = self.alerts.unread
            return snap

    def hist(self, key: str) -> list[float]:
        with self._lock:
            d = self._hist.get(key)
            return list(d) if d else []

    def _push(self, key: str, value: float | None) -> None:
        d = self._hist.get(key)
        if d is None:
            d = self._hist[key] = deque(maxlen=HISTORY_LEN)
        d.append(0.0 if value is None else float(value))

    def _toast(self, item: dict) -> None:
        if self.settings.get("toasts"):
            show_toast(item["title"], item["message"])

    # ---- fast loop: CPU, memory, disk & network I/O, GPU merge, alerts (1 s) ----
    def _fast_loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.monotonic()
            self.sample_fast()
            self._stop.wait(max(0.2, 1.0 - (time.monotonic() - t0)))

    def sample_fast(self) -> None:
        now = time.monotonic()
        per = psutil.cpu_percent(percpu=True)
        total = sum(per) / len(per) if per else 0.0
        freq = None
        freq_max = None
        try:
            f = psutil.cpu_freq()
            if f:
                freq, freq_max = f.current or None, f.max or None
        except Exception:
            pass
        if self._cpuperf:
            base, pct = self._cpuperf
            if base and pct:
                freq_max = freq_max or base
                freq = base * pct / 100.0
        ctx_rate = None
        try:
            st = psutil.cpu_stats()
            if self._prev_ctx:
                dt = now - self._prev_ctx[0]
                ctx_rate = max(0.0, (st.ctx_switches - self._prev_ctx[1]) / dt) if dt > 0 else None
            self._prev_ctx = (now, st.ctx_switches)
        except Exception:
            pass

        vm = psutil.virtual_memory()
        try:
            sw = psutil.swap_memory()
            swap = {"swap_total": sw.total, "swap_used": sw.used, "swap_percent": sw.percent}
        except Exception:
            swap = {"swap_total": None, "swap_used": None, "swap_percent": None}
        mem = {"total": vm.total, "used": vm.total - vm.available, "available": vm.available, "percent": vm.percent, **swap}

        disk_io, disk_total = self._disk_rates(now)
        net = self._net_rates(now)
        with self._lock:
            sensors = self._state["sensors"]
        cpu_temp = pick_cpu_temp(sensors.get("temps", []))
        gpus = self._merge_gpus(sensors.get("temps", []))

        with self._lock:
            self._state["cpu"] = {"total": total, "per_core": per, "freq": freq, "freq_max": freq_max, "temp": cpu_temp,
                                  "ctx_rate": ctx_rate, "procs": len(self._state.get("processes") or []) or None}
            self._state["mem"] = mem
            self._state["disk_io"] = disk_io
            self._state["disk_total"] = disk_total
            self._state["net"] = net
            self._state["gpus"] = gpus
            self._state["uptime"] = time.time() - self._state["boot"]
            self._push("cpu", total)
            self._push("mem", vm.percent)
            self._push("cpu_temp", cpu_temp)
            self._push("disk_read", disk_total.get("read"))
            self._push("disk_write", disk_total.get("write"))
            self._push("net_down", net.get("down"))
            self._push("net_up", net.get("up"))
            for name, d in disk_io.items():
                self._push(f"disk:{name}:busy", d.get("busy"))
            for g in gpus:
                self._push(f"gpu:{g['key']}", g.get("util"))
                vram_pct = 100 * g["vram_used"] / g["vram_total"] if g.get("vram_used") is not None and g.get("vram_total") else None
                self._push(f"vram:{g['key']}", vram_pct)
                self._push(f"gputemp:{g['key']}", g.get("temp"))
            snap = dict(self._state)
        self.alerts.evaluate(snap, self.settings)

    def _disk_rates(self, now: float) -> tuple[dict, dict]:
        try:
            cur = psutil.disk_io_counters(perdisk=True) or {}
        except Exception:
            return {}, {}
        out: dict = {}
        tot_r = tot_w = 0.0
        if self._prev_disk:
            t_prev, prev = self._prev_disk
            dt = now - t_prev
            for name, c in cur.items():
                p = prev.get(name)
                if not p or dt <= 0:
                    continue
                r = max(0, c.read_bytes - p.read_bytes) / dt
                w = max(0, c.write_bytes - p.write_bytes) / dt
                busy = None
                if hasattr(c, "busy_time"):
                    busy = max(0, c.busy_time - p.busy_time) / (dt * 10)
                elif hasattr(c, "read_time"):
                    busy = max(0, (c.read_time + c.write_time) - (p.read_time + p.write_time)) / (dt * 10)
                out[name] = {"read": r, "write": w, "busy": min(100.0, busy) if busy is not None else None}
                tot_r += r
                tot_w += w
        self._prev_disk = (now, cur)
        return out, {"read": tot_r, "write": tot_w}

    def _net_rates(self, now: float) -> dict:
        try:
            cur = psutil.net_io_counters(pernic=True) or {}
        except Exception:
            return {}
        nics: dict = {}
        down = up = 0.0
        if self._prev_net:
            t_prev, prev = self._prev_net
            dt = now - t_prev
            for name, c in cur.items():
                p = prev.get(name)
                if not p or dt <= 0 or "loopback" in name.lower() or name == "lo":
                    continue
                rd = max(0, c.bytes_recv - p.bytes_recv)
                ru = max(0, c.bytes_sent - p.bytes_sent)
                nics[name] = {"down": rd / dt, "up": ru / dt}
                down += rd
                up += ru
            self._session_net[0] += down
            self._session_net[1] += up
            down, up = (down / dt, up / dt) if dt > 0 else (0.0, 0.0)
        self._prev_net = (now, cur)
        return {"down": down, "up": up, "nics": nics, "session_down": self._session_net[0], "session_up": self._session_net[1]}

    # ---- GPU ----
    def _merge_gpus(self, temps: list[dict]) -> list[dict]:
        with self._lock:
            counters = dict(self._gpu_counters)
            nv = dict(self._nvsmi)
        adapters, mem = counters.get("adapters", {}), counters.get("mem", {})
        gpus: list[dict] = []
        luids = set(mem) | set(adapters)
        for luid in luids:
            info = self._dxgi.get(luid, {})
            if info.get("vendor") == "Microsoft":
                continue
            if not info and not mem.get(luid, {}).get("dedicated") and not adapters.get(luid, {}).get("util"):
                continue  # unknown, idle, no VRAM: a virtual/stale adapter
            a = adapters.get(luid, {"util": 0.0, "engines": {}})
            m = mem.get(luid, {})
            gpus.append({
                "key": luid, "name": info.get("name") or f"GPU {luid[-4:]}", "vendor": info.get("vendor") or "",
                "util": a.get("util", 0.0), "engines": a.get("engines", {}),
                "vram_used": m.get("dedicated"), "vram_total": info.get("vram") or None, "shared_used": m.get("shared"),
                "temp": None, "power": None, "power_limit": None, "fan": None, "clock_core": None, "clock_mem": None,
                "clock_core_max": None, "clock_mem_max": None, "pstate": None, "pcie": None, "driver": None,
                "mem_util": None, "source": "Windows counters",
            })
        # NVIDIA details from nvidia-smi (matched by name; falls back to adding the card on its own)
        for idx, row in sorted(nv.items()):
            target = None
            nvn = _norm_name(row.get("name", ""))
            for g in gpus:
                if g["vendor"] == "NVIDIA" and (_norm_name(g["name"]) == nvn or nvn in _norm_name(g["name"]) or _norm_name(g["name"]) in nvn) \
                        and not g.get("_nv"):
                    target = g
                    break
            if target is None:
                nv_cards = [g for g in gpus if g["vendor"] == "NVIDIA" and not g.get("_nv")]
                target = nv_cards[0] if len(nv_cards) == 1 and len(nv) == 1 else None
            if target is None:
                target = {"key": f"nv{idx}", "name": row.get("name") or f"NVIDIA GPU {idx}", "vendor": "NVIDIA",
                          "engines": {}, "shared_used": None, "vram_used": None, "vram_total": None, "util": None}
                gpus.append(target)
            target["_nv"] = True
            mib = 1024 * 1024
            if row.get("utilization.gpu") is not None:
                target["util"] = row["utilization.gpu"]
            if row.get("memory.used") is not None:
                target["vram_used"] = row["memory.used"] * mib
            if row.get("memory.total"):
                target["vram_total"] = row["memory.total"] * mib
            pcie = None
            if row.get("pcie.link.gen.current") is not None and row.get("pcie.link.width.current") is not None:
                pcie = f"Gen {row['pcie.link.gen.current']:.0f} x{row['pcie.link.width.current']:.0f}"
            target.update({
                "temp": row.get("temperature.gpu"), "power": row.get("power.draw"), "power_limit": row.get("power.limit"),
                "fan": row.get("fan.speed"), "clock_core": row.get("clocks.gr"), "clock_mem": row.get("clocks.mem"),
                "clock_core_max": row.get("clocks.max.gr"), "clock_mem_max": row.get("clocks.max.mem"),
                "pstate": row.get("pstate"), "pcie": pcie, "driver": row.get("driver_version"),
                "mem_util": row.get("utilization.memory"), "source": "nvidia-smi + Windows counters",
            })
        for g in gpus:
            g.pop("_nv", None)
            if g.get("temp") is None and temps:
                g["temp"] = pick_gpu_sensor(temps, g.get("vendor", ""))
        # discrete cards first: most VRAM, then busiest
        gpus.sort(key=lambda g: (-(g.get("vram_total") or 0), -(g.get("util") or 0)))
        return gpus

    def _start_sensor_stream(self) -> None:
        script = _PS_PRELUDE + SENSOR_PS.replace("__PARENT__", str(os.getpid())).replace("__INTERVAL__", "1500")
        stream = StreamProcess("sensors", _ps_args(script), self._on_sensor_line, restart_delay=5)
        self._streams.append(stream)
        self.sources["GPU counters"] = "starting"
        stream.start()

    def _on_sensor_line(self, line: str) -> None:
        data = parse_json_output(line)
        if not isinstance(data, dict):
            return
        adapters, per_process = parse_gpu_engines(as_list(data.get("eng")))
        mem = parse_gpu_memory(as_list(data.get("mem")))
        with self._lock:
            ok = bool(data.get("gpuok"))
            self._gpu_counters = {"adapters": adapters, "per_process": per_process, "mem": mem, "ok": ok}
            self.sources["GPU counters"] = "active" if ok else "unavailable"
            perf = data.get("cpuperf")
            if perf:
                base, _, pct = str(perf).partition("|")
                self._cpuperf = (safe_num(base) or 0.0, safe_num(pct) or 0.0)
            if "sens" in data:
                self._wmi_sensors = parse_sensor_lines(as_list(data.get("sens")))
                self._wmi_src = data.get("sensrc") or ""
            elif data.get("t", 0) % 3 == 0:
                self._wmi_sensors = None
                self._wmi_src = ""
            self._apply_sensors()

    def _apply_sensors(self) -> None:
        """Publishes whichever LibreHardwareMonitor feed has data: WMI first, then the web server."""
        with self._lock:
            wmi = self._wmi_sensors
            http_fresh = self._http_sensors is not None and time.monotonic() - self._http_time < 15
            if wmi and (wmi["temps"] or wmi["fans"]):
                self._state["sensors"], self._state["sensor_source"] = wmi, self._wmi_src
                self.sources["Temperatures"] = f"active ({self._wmi_src}, WMI)"
            elif http_fresh and (self._http_sensors["temps"] or self._http_sensors["fans"]):
                self._state["sensors"] = self._http_sensors
                self._state["sensor_source"] = "LibreHardwareMonitor"
                self.sources["Temperatures"] = f"active (LibreHardwareMonitor web server, port {self.settings.get('lhm_port', 8085)})"
            else:
                self._state["sensors"] = {"temps": [], "fans": [], "power": []}
                self._state["sensor_source"] = ""
                self.sources["Temperatures"] = (
                    "not found. In LibreHardwareMonitor turn on Options > Remote Web Server > Run "
                    f"(port {self.settings.get('lhm_port', 8085)}), or run this app as administrator")

    def _lhm_http_loop(self) -> None:
        """Polls LibreHardwareMonitor's web server: works when WMI is blocked or switched off."""
        while not self._stop.is_set():
            res = fetch_lhm_http(self.settings.get("lhm_port", 8085))
            with self._lock:
                if res is not None:
                    self._http_sensors, self._http_time = res, time.monotonic()
                else:
                    self._http_sensors = None
            self._apply_sensors()
            self._stop.wait(2.0 if res is not None else 8.0)

    def _start_nvidia_stream(self) -> None:
        exe = find_nvidia_smi()
        if not exe:
            self.sources["nvidia-smi"] = "not found (no NVIDIA card or driver)"
            return
        code, _out = run_cmd([exe, "--query-gpu=" + ",".join(NVSMI_FIELDS), "--format=csv,noheader,nounits"], timeout=15)
        self._nvsmi_fields = NVSMI_FIELDS if code == 0 else NVSMI_FIELDS_MIN
        args = [exe, "--query-gpu=" + ",".join(self._nvsmi_fields), "--format=csv,noheader,nounits", "-lms", "1000"]
        stream = StreamProcess("nvidia-smi", args, self._on_nvsmi_line, restart_delay=10)
        self._streams.append(stream)
        self.sources["nvidia-smi"] = "active"
        stream.start()

    def _on_nvsmi_line(self, line: str) -> None:
        row = parse_nvidia_smi_line(line, self._nvsmi_fields)
        if row:
            with self._lock:
                self._nvsmi[row["index"]] = row

    # ---- processes (2 s) ----
    def _proc_loop(self) -> None:
        while not self._stop.is_set():
            self.sample_processes()
            self._stop.wait(2.0)

    def sample_processes(self) -> None:
        now = time.monotonic()
        with self._lock:
            gpu_pp = dict(self._gpu_counters.get("per_process", {}))
        procs = []
        new_io: dict[int, tuple[float, int]] = {}
        attrs = ["pid", "name", "username", "memory_info", "cpu_percent", "num_threads", "status", "io_counters"]
        for p in psutil.process_iter(attrs, ad_value=None):
            info = p.info
            pid = info["pid"]
            if pid == 0:  # "System Idle Process": its CPU % is idle time, not load
                continue
            mi = info.get("memory_info")
            io_rate = None
            io = info.get("io_counters")
            if io is not None:
                total_io = io.read_bytes + io.write_bytes
                new_io[pid] = (now, total_io)
                prev = self._prev_proc_io.get(pid)
                if prev and now > prev[0]:
                    io_rate = max(0, total_io - prev[1]) / (now - prev[0])
            user = info.get("username") or ""
            procs.append({
                "pid": pid, "name": info.get("name") or f"pid {pid}",
                "cpu": (info.get("cpu_percent") or 0.0) / self.cpu_count,
                "mem": mi.rss if mi else 0, "threads": info.get("num_threads") or 0,
                "user": user.split("\\")[-1], "status": info.get("status") or "",
                "io": io_rate, "gpu": gpu_pp.get(pid),
            })
        self._prev_proc_io = new_io
        procs.sort(key=lambda x: x["cpu"], reverse=True)
        conns = None
        try:
            conns = sum(1 for c in psutil.net_connections(kind="inet") if c.status == psutil.CONN_ESTABLISHED)
        except Exception:
            pass
        with self._lock:
            self._state["processes"] = procs
            self._state["connections"] = conns

    # ---- volumes & battery (5 s, separate thread: a dead network drive can block) ----
    def _volume_loop(self) -> None:
        while not self._stop.is_set():
            self.sample_volumes()
            self._stop.wait(5.0)

    def sample_volumes(self) -> None:
        vols = []
        try:
            parts = psutil.disk_partitions(all=False)
        except Exception:
            parts = []
        seen = set()
        for p in parts:
            if p.mountpoint in seen or "cdrom" in (p.opts or "") or not p.fstype:
                continue
            if not IS_WINDOWS and (p.mountpoint.startswith(("/snap", "/boot/efi", "/proc", "/sys")) or p.fstype in ("squashfs", "overlay")):
                continue
            seen.add(p.mountpoint)
            try:
                u = psutil.disk_usage(p.mountpoint)
            except (OSError, PermissionError):
                continue
            if u.total <= 0:
                continue
            vols.append({"mount": p.mountpoint.rstrip("\\") if IS_WINDOWS else p.mountpoint, "path": p.mountpoint,
                         "device": p.device, "fstype": p.fstype, "opts": p.opts, "total": u.total, "used": u.used,
                         "free": u.free, "percent": u.percent, "removable": "removable" in (p.opts or "")})
        bat = None
        try:
            b = psutil.sensors_battery()
            if b:
                secs = b.secsleft if isinstance(b.secsleft, (int, float)) and b.secsleft >= 0 else None
                bat = {"percent": b.percent, "plugged": b.power_plugged, "secsleft": secs}
        except Exception:
            pass
        with self._lock:
            self._state["volumes"] = vols
            self._state["battery"] = bat

    # ---- ping ----
    def _ping_loop(self) -> None:
        results: deque = deque(maxlen=20)
        while not self._stop.is_set():
            if self.settings.get("ping_enabled"):
                host = self.settings.get("ping_host") or "1.1.1.1"
                ms = ping_once(host)
                results.append(ms)
                loss = 100.0 * sum(1 for r in results if r is None) / len(results)
                good = [r for r in results if r is not None]
                with self._lock:
                    self._state["ping"] = {"ms": ms, "loss": loss, "host": host, "avg": sum(good) / len(good) if good else None}
                    self._push("ping", ms)
            else:
                results.clear()
                with self._lock:
                    self._state["ping"] = {}
            self._stop.wait(4.0)

    # ---- slow loop: pending reboot, power plan, Wi-Fi (30 s) and drive health (10 min) ----
    def _slow_loop(self) -> None:
        n = 0
        while not self._stop.is_set():
            pr = pending_reboot()
            plans = power_plans()
            wifi = {}
            if IS_WINDOWS:
                code, out = run_cmd(["netsh", "wlan", "show", "interfaces"], timeout=10)
                if code == 0:
                    wifi = parse_netsh_wlan(out)
            with self._lock:
                self._state["pending_reboot"] = pr
                self._state["power_plans"] = plans
                self._state["wifi"] = wifi
            n += 1
            if n % 20 == 0:
                self.refresh_inventory()
            self._stop.wait(30.0)

    def refresh_inventory(self) -> None:
        with self._lock:
            if self._state.get("inventory_loading"):
                return
            self._state["inventory_loading"] = True

        def run():
            try:
                inv = run_powershell_json(INVENTORY_PS, timeout=120) if self.enable_external else None
                inv = normalize_inventory(inv if isinstance(inv, dict) else {})
                with self._lock:
                    if inv or not self._state["inventory"]:
                        self._state["inventory"] = inv
                    self._state["winver"] = windows_version_info()
                    self.sources["Drive health"] = ("active" + ("" if self._state["admin"] else " (temperatures need admin)")) \
                        if inv.get("disks") else ("Windows only" if not IS_WINDOWS else "unavailable")
            finally:
                with self._lock:
                    self._state["inventory_loading"] = False

        threading.Thread(target=run, name="inventory", daemon=True).start()

    # ---- helpers for the UI ----
    def disk_number_for_volume(self, mount: str) -> str | None:
        letter = (mount or "")[:1].upper()
        with self._lock:
            for p in self._state["inventory"].get("parts", []):
                if p.get("letter", "").upper() == letter:
                    return p.get("disk")
        return None


def normalize_inventory(inv: dict) -> dict:
    """Cleans the raw PowerShell inventory: lists everywhere, names for enum codes."""
    out = dict(inv)
    for key in ("cpu", "ram", "video", "disks", "parts", "volumes", "av", "firewall", "hotfix"):
        out[key] = as_list(inv.get(key))
    for d in out["disks"]:
        d["media_name"] = media_type_name(d.get("media"))
        d["bus_name"] = bus_type_name(d.get("bus"))
        d["health_name"] = health_name(d.get("health"))
        if d["bus_name"] == "NVMe" and d["media_name"] in ("Unspecified", "Unknown"):
            d["media_name"] = "SSD"
    out["parts"] = [p for p in out["parts"] if re.fullmatch(r"[A-Za-z]", str(p.get("letter", "")))]
    out["volumes"] = [v for v in out["volumes"] if re.fullmatch(r"[A-Za-z]", str(v.get("letter", "")))]
    for v in out["volumes"]:
        v["health_name"] = health_name(v.get("health"))
    for f in out["firewall"]:
        f["on"] = parse_firewall_enabled(f.get("enabled"))
    return out


# ---------------------------------------------------------------------------------------------
# Actions (called from the UI, usually in a worker thread)
# ---------------------------------------------------------------------------------------------

def kill_process(pid: int) -> tuple[bool, str]:
    if pid in (0, 4) or pid == os.getpid():
        return False, "That process can't be ended from here."
    try:
        p = psutil.Process(pid)
        name = p.name()
        if name.lower() in PROTECTED_PROCESSES:
            return False, f"{name} is a core Windows process; ending it would crash or log off Windows."
        p.terminate()
        try:
            p.wait(timeout=3)
        except psutil.TimeoutExpired:
            p.kill()
        return True, f"Ended {name} (PID {pid})"
    except psutil.NoSuchProcess:
        return True, "Process already exited"
    except psutil.AccessDenied:
        return False, "Access denied: run the Command Center as administrator to end this process."
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def process_exe(pid: int) -> str | None:
    try:
        return psutil.Process(pid).exe() or None
    except Exception:
        return None


def open_path(target: str) -> tuple[bool, str]:
    """Opens a file, folder, URL, ms-settings: page or .msc snap-in with the shell."""
    try:
        if IS_WINDOWS:
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, f"Opened {target}"
    except Exception as exc:  # noqa: BLE001
        return False, f"Couldn't open {target}: {exc}"


def reveal_in_explorer(path: str) -> tuple[bool, str]:
    if IS_WINDOWS:
        try:
            subprocess.Popen(["explorer", "/select,", path])
            return True, f"Showing {path}"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
    return open_path(str(Path(path).parent))


def recycle_bin_info() -> tuple[int, int] | None:
    """(bytes, items) in the Recycle Bin across all drives."""
    if not IS_WINDOWS:
        return None
    try:
        fields = [("cbSize", ctypes.c_uint32), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]
        attrs = {"_fields_": fields}
        if ctypes.sizeof(ctypes.c_void_p) == 4:
            attrs["_pack_"] = 1
        Info = type("SHQUERYRBINFO", (ctypes.Structure,), attrs)
        info = Info()
        info.cbSize = ctypes.sizeof(Info)
        if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) != 0:
            return None
        return int(info.i64Size), int(info.i64NumItems)
    except Exception:
        return None


def empty_recycle_bin() -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Windows only"
    before = recycle_bin_info()
    try:
        res = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    if res in (0, -2147418113):  # S_OK, or E_UNEXPECTED when it was already empty
        freed = fmt_bytes(before[0]) if before else "space"
        return True, f"Recycle Bin emptied ({freed} freed)"
    return False, f"Couldn't empty the Recycle Bin (error {res & 0xFFFFFFFF:#x})"


def temp_folders() -> list[Path]:
    out = []
    for v in ("TEMP", "TMP"):
        p = os.environ.get(v)
        if p and Path(p).is_dir() and Path(p) not in out:
            out.append(Path(p))
    if IS_WINDOWS and is_admin():
        w = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Temp"
        if w.is_dir():
            out.append(w)
    return out


def _is_link_or_junction(path: str) -> bool:
    """Symlinks, NTFS junctions and other reparse points: never walked into by the temp cleanup."""
    try:
        if os.path.islink(path) or (hasattr(os.path, "isjunction") and os.path.isjunction(path)):
            return True
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except OSError:
        return True  # can't tell: leave it alone


def clean_temp_files(dry_run: bool = True, min_age_hours: float = 24, folders: list[Path] | None = None) -> dict:
    """Deletes (or, with dry_run, measures) temp files older than min_age_hours. Skips files in use.

    Never follows symlinks or junctions: one inside Temp can point at real data elsewhere."""
    cutoff = time.time() - min_age_hours * 3600
    found = freed = count = failed = 0
    for root in folders if folders is not None else temp_folders():
        if _is_link_or_junction(str(root)):
            continue
        visited: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            dirnames[:] = [d for d in dirnames if not _is_link_or_junction(os.path.join(dirpath, d))]
            visited.append(dirpath)
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                try:
                    st = os.lstat(fp)
                except OSError:
                    continue
                if st.st_mtime > cutoff:
                    continue
                found += st.st_size
                if dry_run:
                    count += 1
                    continue
                try:
                    os.remove(fp)  # a file symlink: removes the link, not its target
                    freed += st.st_size
                    count += 1
                except OSError:
                    failed += 1
        if not dry_run:
            for dirpath in reversed(visited):  # deepest first
                if dirpath != str(root):
                    try:
                        os.rmdir(dirpath)  # only succeeds when empty
                    except OSError:
                        pass
    return {"bytes": found if dry_run else freed, "files": count, "failed": failed}


def flush_dns() -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Windows only"
    code, out = run_cmd(["ipconfig", "/flushdns"], timeout=20)
    return code == 0, "DNS cache flushed" if code == 0 else out.strip()


def restart_explorer() -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Windows only"
    run_cmd(["taskkill", "/f", "/im", "explorer.exe"], timeout=15)
    time.sleep(1.0)
    try:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "explorer.exe")], creationflags=flags,
                         close_fds=True)
        return True, "Windows Explorer restarted"
    except Exception as exc:  # noqa: BLE001
        return False, f"Explorer was stopped but didn't restart: {exc}. Press Ctrl+Shift+Esc, File > Run new task > explorer"


def lock_workstation() -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Windows only"
    return bool(ctypes.windll.user32.LockWorkStation()), "Locked"


def sleep_pc() -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Windows only"
    ok = bool(ctypes.windll.powrprof.SetSuspendState(0, 0, 0))
    return ok, "Going to sleep" if ok else "Sleep failed"


def schedule_shutdown(restart: bool, delay_s: int = 60) -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Windows only"
    code, out = run_cmd(["shutdown", "/r" if restart else "/s", "/t", str(int(delay_s))], timeout=15)
    verb = "Restart" if restart else "Shut down"
    return code == 0, f"{verb} in {delay_s} seconds. Use 'Cancel shutdown' to stop it." if code == 0 else out.strip()


def cancel_shutdown() -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Windows only"
    code, out = run_cmd(["shutdown", "/a"], timeout=15)
    return code == 0, "Shutdown cancelled" if code == 0 else (out.strip() or "Nothing to cancel")


def defender_quick_scan() -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Windows only"
    res = run_powershell_json("try { Start-MpScan -ScanType QuickScan -ErrorAction Stop; @{ ok = $true } | ConvertTo-Json -Compress } "
                              "catch { @{ ok = $false; err = '' + $_.Exception.Message } | ConvertTo-Json -Compress }", timeout=3600)
    if isinstance(res, dict) and res.get("ok"):
        return True, "Microsoft Defender quick scan finished"
    return False, f"Quick scan failed: {(res or {}).get('err', 'Defender not available') if isinstance(res, dict) else 'Defender not available'}"


def load_events() -> dict:
    res = run_powershell_json(EVENTS_PS, timeout=90)
    if not isinstance(res, dict):
        return {"events": [], "unexpected": None, "bsod": None, "error": "Event log not available"}
    res["events"] = as_list(res.get("events"))
    return res


def check_windows_updates() -> dict:
    res = run_powershell_json(WINDOWS_UPDATE_PS, timeout=300)
    if not isinstance(res, dict):
        return {"ok": False, "updates": [], "error": "Windows Update not reachable"}
    res["updates"] = as_list(res.get("updates"))
    return res


def relaunch_as_admin() -> bool:
    if not IS_WINDOWS:
        return False
    script = os.path.abspath(sys.argv[0])
    import subprocess
    params = subprocess.list2cmdline([script] + sys.argv[1:])  # proper Windows quoting (trailing \\, quotes)
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        pyw = exe[:-10] + "pythonw.exe"
        if os.path.exists(pyw):
            exe = pyw
    try:
        return int(ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)) > 32
    except Exception:
        return False


def scan_folder(root: str, progress=None, cancel: threading.Event | None = None, top_files: int = 25) -> dict:
    """Sizes of a folder's direct children plus its largest files. Doesn't follow links/junctions."""
    root_p = Path(root)
    children: dict[str, list] = {}
    largest: list[tuple[int, str]] = []
    total = files = 0
    last_report = time.monotonic()

    def walk(start: str, bucket: str) -> None:
        nonlocal total, files, last_report
        stack = [start]
        while stack:
            if cancel and cancel.is_set():
                return
            d = stack.pop()
            try:
                it = os.scandir(d)
            except OSError:
                continue
            with it:
                for e in it:
                    try:
                        if e.is_symlink() or (getattr(e, "is_junction", None) and e.is_junction()):
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            sz = e.stat(follow_symlinks=False).st_size
                            total += sz
                            files += 1
                            children[bucket][0] += sz
                            children[bucket][1] += 1
                            if len(largest) < top_files:
                                heapq.heappush(largest, (sz, e.path))
                            elif sz > largest[0][0]:
                                heapq.heapreplace(largest, (sz, e.path))
                    except OSError:
                        continue
            if progress and time.monotonic() - last_report > 0.3:
                last_report = time.monotonic()
                progress(files, total, d)

    try:
        entries = list(os.scandir(root_p))
    except OSError as exc:
        return {"error": str(exc), "children": [], "files": [], "total": 0, "count": 0}
    loose = "(files in this folder)"
    for e in entries:
        if cancel and cancel.is_set():
            break
        try:
            if e.is_symlink() or (getattr(e, "is_junction", None) and e.is_junction()):
                continue
            if e.is_dir(follow_symlinks=False):
                children[e.path] = [0, 0]
                walk(e.path, e.path)
            elif e.is_file(follow_symlinks=False):
                children.setdefault(loose, [0, 0])
                sz = e.stat(follow_symlinks=False).st_size
                children[loose][0] += sz
                children[loose][1] += 1
                total += sz
                files += 1
                if len(largest) < top_files:
                    heapq.heappush(largest, (sz, e.path))
                elif sz > largest[0][0]:
                    heapq.heapreplace(largest, (sz, e.path))
        except OSError:
            continue
    kids = sorted(([p, v[0], v[1]] for p, v in children.items()), key=lambda x: x[1], reverse=True)
    return {"children": kids, "files": sorted(largest, reverse=True), "total": total, "count": files,
            "cancelled": bool(cancel and cancel.is_set())}

