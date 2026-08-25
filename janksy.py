"""
Janksy Launcher
Single-file Windows desktop Minecraft launcher.

Development:
    Python 3.12 x64 recommended.

Install:
    pip install customtkinter tkinterdnd2 Pillow pypresence minecraft-launcher-lib pyinstaller

Build:
    pyinstaller --noconfirm --clean --windowed --onefile ^
        --name "Janksy Launcher" ^
        --collect-all tkinterdnd2 ^
        launcher.py
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

import customtkinter as ctk
from tkinter import filedialog, messagebox

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES = None
    TkinterDnD = None

try:
    from PIL import Image, ImageTk, ImageEnhance
except ImportError:
    Image = ImageTk = ImageEnhance = None

try:
    from pypresence import Presence
except ImportError:
    Presence = None

try:
    import minecraft_launcher_lib
    from minecraft_launcher_lib import microsoft_account
except ImportError:
    minecraft_launcher_lib = None
    microsoft_account = None


# ============================================================================
# RELEASE CONFIGURATION
# ============================================================================
MICROSOFT_CLIENT_ID = "86026143-4b3e-40b6-a393-cc22b8332e67"
MICROSOFT_REDIRECT_URI = "http://127.0.0.1:8765"

DISCORD_CLIENT_ID = "1100000000000000000"

APP_NAME = "Janksy Launcher"
APP_VERSION = "1.0.0 "

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("APPDATA", APP_DIR)) / "JanksyLauncher"
ACCOUNTS_FILE = DATA_DIR / "accounts.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

# Apply launcher library argument patch once to avoid recursive monkey-patching
if minecraft_launcher_lib and hasattr(minecraft_launcher_lib.command, "get_arguments"):
    _orig_get_args = minecraft_launcher_lib.command.get_arguments
    if not getattr(_orig_get_args, "_patched", False):
        def _safe_get_arguments(arg_list, data, path, options, classpath):
            safe_list = []
            for item in arg_list:
                if isinstance(item, dict):
                    item_copy = dict(item)
                    if "value" not in item_copy:
                        item_copy["value"] = item_copy.get("values", "")
                    safe_list.append(item_copy)
                else:
                    safe_list.append(item)
            return _orig_get_args(safe_list, data, path, options, classpath)
        _safe_get_arguments._patched = True
        minecraft_launcher_lib.command.get_arguments = _safe_get_arguments

# ============================================================================
# ENHANCED GLASS MORPHISM COLOR PALETTE
# ============================================================================
BG_DARK = "#0F1117"
GLASS_PANEL = "transparent"
GLASS_CARD = "#1C2128"
GLASS_CARD_HOVER = "#262C33"
GLASS_BORDER = "#30363D"

BG = BG_DARK
PANEL = GLASS_PANEL
CARD = GLASS_CARD
CARD_2 = GLASS_CARD_HOVER
OUTLINE = GLASS_BORDER
TEXT = "#FFFFFF"
MUTED = "#8B949E"
SUCCESS = "#3FB950"  # Emerald
ERROR = "#FF7B72"    # Cyber Crimson

ACCENTS = {
    "Electric Blue": "#58A6FF",
    "Neon Purple": "#D29DFF",
    "Emerald Green": "#3FB950",
    "Cyber Crimson": "#FF7B72",
    "Solar Gold": "#D29922",
}

# Window "themes" — the dark-glass palette (default) and a pure-black theme.
# Every key is a module-level color constant, so switching a theme just pushes
# a new palette into the module globals and the UI rebuilds with it.
THEMES = {
    "Midnight Glass": {
        "BG_DARK": "#0F1117",
        "GLASS_CARD": "#1C2128",
        "GLASS_CARD_HOVER": "#2rvers62C33",
        "GLASS_BORDER": "#30363D",
        "CARD": "#1C2128",
        "CARD_2": "#262C33",
        "OUTLINE": "#30363D",
        "TEXT": "#FFFFFF",
        "MUTED": "#8B949E",
    },
    "Pure Black": {
        "BG_DARK": "#050507",
        "GLASS_CARD": "#0D0D11",
        "GLASS_CARD_HOVER": "#17171C",
        "GLASS_BORDER": "#232329",
        "CARD": "#0D0D11",
        "CARD_2": "#17171C",
        "OUTLINE": "#232329",
        "TEXT": "#FFFFFF",
        "MUTED": "#7E838C",
    },
}

# Badge colors for mod-loader tags shown next to version names.
LOADER_BADGE_COLORS = {
    "fabric": "#D29DFF",
    "forge": "#D29922",
    "quilt": "#58A6FF",
}


def _hex_rgb(hex_color: str) -> tuple:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def _mix_colors(base_hex: str, rgb_target, t: float) -> str:
    """Linearly blends a hex color toward an (r,g,b) tuple (t in [0,1])."""
    t = max(0.0, min(1.0, t))
    a = _hex_rgb(base_hex)
    out = tuple(int(a[i] + (rgb_target[i] - a[i]) * t) for i in range(3))
    return "#%02x%02x%02x" % out

# Servers shown in the "Servers" tab.
#   - name   : display name shown on the card
#   - host   : the MAIN / connect domain used to query the server (HIDDEN from
#              the user, edit this in the code)
#   - port   : port used when querying the host
#   - display: the subdomain + port the user sees and can copy
SERVERS = [
    {
        "name": "NetherVileSMP",
        "host": "node-fi-01.tickhosting.com",
        "port": 50009,
        "display": "nethervilesmp.tkmc.me:50009",
    },
]


def _encode_varint(value: int) -> bytes:
    value &= 0xFFFFFFFF
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _mc_text(comp) -> str:
    """Converts a Minecraft chat component (string or json) to plain text."""
    if isinstance(comp, str):
        return comp
    if isinstance(comp, dict):
        parts = []
        if comp.get("text"):
            parts.append(comp["text"])
        for extra in comp.get("extra", []) or []:
            t = _mc_text(extra)
            if t:
                parts.append(t)
        return "".join(parts)
    return str(comp)


def get_minecraft_server_status(address: str, port, timeout: float = 4.0) -> dict:
    """Queries a Minecraft server with the modern Server List Ping (status)
    protocol. Returns a dict with online/latency/version/description/players
    and an optional base64 favicon (server logo). Never raises."""
    import socket
    from time import time as _now

    result = {
        "online": False, "latency": None, "version": None,
        "description": "", "players_online": 0, "players_max": 0,
        "favicon_b64": None,
    }
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    start = _now()
    try:
        host = str(address)
        port = int(port)
        s.connect((host, port))
        host_bytes = host.encode("utf-8")
        handshake = (
            _encode_varint(-1)                    # protocol (status accepts any)
            + _encode_varint(len(host_bytes)) + host_bytes
            + port.to_bytes(2, "big")
            + b"\x01"                              # next state: status
        )
        packet = b"\x00" + handshake
        s.sendall(_encode_varint(len(packet)) + packet)
        s.sendall(b"\x01\x00")                     # status request

        # read packet length (discard) + packet id
        _read_varint(s)
        pid = _read_varint(s)
        if pid != 0:
            return result
        body_len = _read_varint(s)
        body = _read_exact(s, body_len)
        data = json.loads(body.decode("utf-8", errors="replace"))

        result["online"] = True
        result["latency"] = int((_now() - start) * 1000)
        result["version"] = (data.get("version") or {}).get("name")
        players = data.get("players") or {}
        result["players_online"] = players.get("online", 0)
        result["players_max"] = players.get("max", 0)
        result["description"] = _mc_text(data.get("description"))
        fav = data.get("favicon")
        if isinstance(fav, str) and "base64," in fav:
            result["favicon_b64"] = fav.split("base64,", 1)[1]
    except Exception:
        result["online"] = False
    finally:
        try:
            s.close()
        except Exception:
            pass
    return result


def _read_exact(sock, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            break
        data += chunk
    return data


def _read_varint(sock) -> int:
    num = 0
    for i in range(5):
        b = _read_exact(sock, 1)
        if not b:
            raise ConnectionError("connection closed while reading varint")
        val = b[0]
        num |= (val & 0x7F) << (7 * i)
        if not (val & 0x80):
            break
    return num

def log(*args):
    try:
        print("[Janksy]", *args, flush=True)
    except Exception:
        pass


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_asset_path(relative_path: str) -> Path:
    """Resolves asset paths for standard Python and PyInstaller packages."""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / relative_path
    return APP_DIR / relative_path


def load_json(path: Path, default):
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        log("JSON read failed:", path, exc)
    return default


def save_json(path: Path, value):
    try:
        ensure_data_dir()
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(value, f, indent=2)
        tmp.replace(path)
    except Exception as exc:
        log("JSON save failed:", path, exc)


def find_java() -> str:
    """Fast Java detection. Never recursively scans entire drives."""
    candidates = []

    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        root = Path(java_home)
        candidates += [
            root / "bin" / "java.exe",
            root / "bin" / "java",
        ]

    java_path = shutil.which("java")
    if java_path:
        candidates.append(Path(java_path))

    if sys.platform == "win32":
        roots = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Java",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Eclipse Adoptium",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Eclipse Adoptium",
        ]

        for root in roots:
            if root.exists():
                try:
                    for child in root.iterdir():
                        candidates.append(child / "bin" / "java.exe")
                except OSError:
                    pass

    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            pass

    return ""


# ============================================================================
# DISCORD RPC CONTROLLER
# ============================================================================
class DiscordRPCController:
    """Manages connection to Discord RPC client for status updates."""
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.rpc = None
        self.active = False

    def initialize(self):
        if not Presence or not self.client_id:
            return
        try:
            self.rpc = Presence(self.client_id)
            self.rpc.connect()
            self.active = True
            log("Connected to Discord Client")
        except Exception as exc:
            log(f"Discord connection failed: {exc}")

    def update(self, state: str, details: str):
        if not self.active or not self.rpc:
            return
        try:
            self.rpc.update(
                state=state,
                details=details,
                start=int(time.time()),
            )
        except Exception as exc:
            log(f"Discord update failed: {exc}")

    def shutdown(self):
        if self.active and self.rpc:
            try:
                self.rpc.close()
                self.active = False
            except Exception:
                pass

# ============================================================================
# MOD LOADER INSTALLER DIALOG (SEARCHABLE LIST UI)
# ============================================================================
class InstallModDialog(ctk.CTkToplevel):
    def __init__(self, parent, mc_dir, accent_color, on_complete_callback=None):
        super().__init__(parent)
        self.title("Install Mod Loader")
        self.geometry("420x540")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.mc_dir = mc_dir
        self.accent = accent_color
        self.on_complete = on_complete_callback
        
        self.selected_version = None
        self.version_buttons = {}
        self.all_versions = []

        self.configure(fg_color=BG_DARK)

        self.update_idletasks()
        parent_x = parent.winfo_x()
        parent_y = parent.winfo_y()
        parent_w = parent.winfo_width()
        parent_h = parent.winfo_height()
        x = parent_x + (parent_w - 420) // 2
        y = parent_y + (parent_h - 540) // 2
        self.geometry(f"420x540+{max(0, x)}+{max(0, y)}")

        frame = ctk.CTkFrame(self, fg_color=CARD, corner_radius=16, border_width=1, border_color=OUTLINE)
        frame.pack(fill="both", expand=True, padx=15, pady=15)

        ctk.CTkLabel(frame, text="Select Mod Loader", text_color=TEXT, font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=15, pady=(15, 6))
        self.loader_var = ctk.StringVar(value="Fabric")
        self.loader_seg = ctk.CTkSegmentedButton(
            frame,
            values=["Fabric", "Forge", "Quilt"],
            variable=self.loader_var,
            selected_color=self.accent,
            selected_hover_color=self.accent,
            font=ctk.CTkFont(size=12, weight="bold"),
            height=32
        )
        self.loader_seg.pack(fill="x", padx=15, pady=(0, 12))

        ctk.CTkLabel(frame, text="Select Minecraft Version", text_color=TEXT, font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=15, pady=(4, 6))
        
        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", self._filter_versions)
        self.search_entry = ctk.CTkEntry(
            frame,
            placeholder_text="🔍  Search version (e.g. 1.21)...",
            textvariable=self.search_var,
            height=36,
            fg_color=CARD_2,
            border_color=OUTLINE,
            corner_radius=10
        )
        self.search_entry.pack(fill="x", padx=15, pady=(0, 8))

        self.scroll_frame = ctk.CTkScrollableFrame(
            frame,
            height=210,
            fg_color=BG_DARK,
            border_width=1,
            border_color=OUTLINE,
            corner_radius=10
        )
        self.scroll_frame.pack(fill="both", expand=True, padx=15, pady=(0, 10))

        self.status_lbl = ctk.CTkLabel(frame, text="Fetching version list from Mojang...", text_color=MUTED, font=ctk.CTkFont(size=11))
        self.status_lbl.pack(pady=(0, 4))

        self.install_btn = ctk.CTkButton(
            frame,
            text="Install Loader",
            height=40,
            corner_radius=10,
            fg_color=self.accent,
            hover_color=self.accent,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self.start_install,
            state="disabled"
        )
        self.install_btn.pack(fill="x", padx=15, pady=(0, 15))

        threading.Thread(target=self._fetch_versions, daemon=True).start()

    def _fetch_versions(self):
        try:
            import minecraft_launcher_lib as mll
            version_data = mll.utils.get_version_list()
            releases = [v["id"] for v in version_data if v.get("type") == "release"]
            if releases:
                self.after(0, lambda: self._build_version_list(releases))
                return
        except Exception:
            pass

        fallback = ["1.21.4", "1.21.3", "1.21.1", "1.20.6", "1.20.4", "1.20.1", "1.19.4", "1.18.2", "1.16.5"]
        self.after(0, lambda: self._build_version_list(fallback))

    def _build_version_list(self, versions):
        self.all_versions = versions
        self.version_buttons.clear()

        for widget in self.scroll_frame.winfo_children():
            widget.destroy()

        for ver in versions:
            btn = ctk.CTkButton(
                self.scroll_frame,
                text=f"Minecraft  {ver}",
                anchor="w",
                height=34,
                corner_radius=8,
                fg_color=CARD_2,
                hover_color=OUTLINE,
                text_color=TEXT,
                font=ctk.CTkFont(size=12, weight="bold"),
                command=lambda v=ver: self._select_version(v)
            )
            btn.pack(fill="x", pady=2, padx=2)
            self.version_buttons[ver] = btn

        self.status_lbl.configure(text="Select a version to install.")
        if versions:
            self._select_version(versions[0])

    def _filter_versions(self, *args):
        query = self.search_var.get().lower().strip()
        visible_count = 0

        for ver, btn in self.version_buttons.items():
            if not query or query in ver.lower():
                btn.pack(fill="x", pady=2, padx=2)
                visible_count += 1
            else:
                btn.pack_forget()

        if visible_count == 0:
            self.status_lbl.configure(text="No matching versions found.", text_color=ERROR)
        elif self.selected_version:
            self.status_lbl.configure(text=f"Selected: {self.loader_var.get()} for {self.selected_version}", text_color=MUTED)

    def _select_version(self, version):
        self.selected_version = version

        for ver, btn in self.version_buttons.items():
            if ver == version:
                btn.configure(fg_color=self.accent, hover_color=self.accent, text_color="white")
            else:
                btn.configure(fg_color=CARD_2, hover_color=OUTLINE, text_color=TEXT)

        self.install_btn.configure(state="normal", text=f"Install {self.loader_var.get()} {version}")
        self.status_lbl.configure(text=f"Ready to install {self.loader_var.get()} {version}", text_color=MUTED)

    def start_install(self):
        loader = self.loader_var.get().lower()
        version = self.selected_version

        if not version:
            return

        self.install_btn.configure(state="disabled")
        self.loader_seg.configure(state="disabled")
        self.search_entry.configure(state="disabled")
        self.status_lbl.configure(text=f"Downloading & installing {loader.capitalize()} {version}...", text_color=self.accent)

        threading.Thread(target=self._worker, args=(loader, version), daemon=True).start()

    def _worker(self, loader, version):
        try:
            import minecraft_launcher_lib as mll

            if loader == "fabric":
                mll.fabric.install_fabric(version, self.mc_dir)
            elif loader == "quilt":
                mll.quilt.install_quilt(version, self.mc_dir)
            elif loader == "forge":
                forge_ver = mll.forge.find_forge_version(version)
                if forge_ver:
                    mll.forge.install_forge_version(forge_ver, self.mc_dir)
                else:
                    raise Exception(f"No Forge release found for {version}")

            self.after(0, lambda: self._on_success(f"{loader.capitalize()} {version} Installed!"))
        except Exception as exc:
            self.after(0, lambda: self._on_failure(str(exc)))

    def _on_success(self, msg):
        self.status_lbl.configure(text=msg, text_color=SUCCESS)
        if self.on_complete:
            self.on_complete()
        self.after(1200, self.destroy)

    def _on_failure(self, err_text):
        self.status_lbl.configure(text=f"Error: {err_text}", text_color=ERROR)
        self.install_btn.configure(state="normal")
        self.loader_seg.configure(state="normal")
        self.search_entry.configure(state="normal")


# ============================================================================
# GAME LOG / TERMINAL WINDOW
# ============================================================================
class GameLogWindow(ctk.CTkToplevel):
    def __init__(self, parent, accent_color, process=None):
        super().__init__(parent)
        self.title("Janksy Terminal — Game Logs")
        self.geometry("720x460")
        self.resizable(True, True)
        self.transient(parent)

        self.accent = accent_color
        self.process = process

        self.configure(fg_color=BG_DARK)

        self.update_idletasks()
        parent_x = parent.winfo_x()
        parent_y = parent.winfo_y()
        parent_w = parent.winfo_width()
        parent_h = parent.winfo_height()
        x = parent_x + (parent_w - 720) // 2
        y = parent_y + (parent_h - 460) // 2
        self.geometry(f"720x460+{max(0, x)}+{max(0, y)}")

        top_bar = ctk.CTkFrame(self, fg_color="transparent")
        top_bar.pack(fill="x", padx=15, pady=(15, 5))

        ctk.CTkLabel(
            top_bar,
            text="> Console Output",
            font=ctk.CTkFont(family="Consolas", size=14, weight="bold"),
            text_color=TEXT
        ).pack(side="left")

        self.copy_btn = ctk.CTkButton(
            top_bar,
            text="📋 Copy Logs",
            width=100,
            height=32,
            fg_color=CARD_2,
            hover_color=OUTLINE,
            text_color=TEXT,
            font=ctk.CTkFont(size=12, weight="bold"),
            corner_radius=8,
            command=self.copy_logs
        )
        self.copy_btn.pack(side="right", padx=(6, 0))

        self.save_btn = ctk.CTkButton(
            top_bar,
            text="💾 Save Logs",
            width=100,
            height=32,
            fg_color=self.accent,
            hover_color=self.accent,
            font=ctk.CTkFont(size=12, weight="bold"),
            corner_radius=8,
            command=self.save_logs
        )
        self.save_btn.pack(side="right")

        self.log_box = ctk.CTkTextbox(
            self,
            fg_color=CARD,
            text_color="#A9B7C6",
            font=ctk.CTkFont(family="Consolas", size=11),
            border_width=1,
            border_color=OUTLINE,
            corner_radius=12
        )
        self.log_box.pack(fill="both", expand=True, padx=15, pady=(5, 15))

        if self.process and self.process.stdout:
            threading.Thread(target=self._stream_logs, daemon=True).start()

    def append_log(self, line):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _stream_logs(self):
        self.after(0, lambda: self.append_log("[Janksy] Launching game process...\n\n"))
        
        try:
            for line in iter(self.process.stdout.readline, ''):
                if line:
                    self.after(0, lambda l=line: self.append_log(l))
                elif self.process.poll() is not None:
                    break
        except Exception as exc:
            self.after(0, lambda: self.append_log(f"\n[Janksy Log Stream Error] {exc}\n"))

        if self.process.stdout:
            try: self.process.stdout.close()
            except Exception: pass
        self.after(0, lambda: self.append_log("\n[Janksy] Game process terminated.\n"))

    def copy_logs(self):
        logs = self.log_box.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(logs)
        self.copy_btn.configure(text="✓ Copied!")
        self.after(1500, lambda: self.copy_btn.configure(text="📋 Copy Logs"))

    def save_logs(self):
        logs = self.log_box.get("1.0", "end-1c")
        file_path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text Files", "*.txt"), ("Log Files", "*.log"), ("All Files", "*.*")],
            title="Save Game Output Logs",
            initialfile="latest_game_log.txt"
        )
        if file_path:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(logs)
            except Exception as exc:
                log(f"Failed to save log file: {exc}")


class JanksyLauncher(ctk.CTk, TkinterDnD.DnDWrapper if TkinterDnD else object):
    def __init__(self):
        super().__init__()

        if TkinterDnD is not None:
            try:
                self.TkdndVersion = TkinterDnD._require(self)
                log("TkDND loaded:", self.TkdndVersion)
            except Exception as exc:
                log("TkDND unavailable:", exc)
                self.TkdndVersion = None

        self.title(APP_NAME)
        self.geometry("1250x800")
        self.minsize(1050, 700)
        self.configure(fg_color=BG_DARK)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        ensure_data_dir()

        self.settings = load_json(
            SETTINGS_FILE,
            {
                "accent": "Electric Blue",
                "theme": "Midnight Glass",
                "minecraft_directory": "",
                "java_path": "",
                "ram": 6,
                "jvm_args": "",
                "width": 1920,
                "height": 1080,
                "behavior": "Keep Open",
                "discord_rpc": False,
                "background": "",
                "background_alpha": 1.0,
            },
        )

        self.accounts = load_json(ACCOUNTS_FILE, [])
        if not isinstance(self.accounts, list):
            self.accounts = []

        self.accent_name = self.settings.get("accent", "Electric Blue")
        if self.accent_name not in ACCENTS:
            self.accent_name = "Electric Blue"
        self.accent = ACCENTS[self.accent_name]

        self._load_theme()

        self.minecraft_directory = self.settings.get("minecraft_directory", "")
        self.java_path = self.settings.get("java_path", "")

        self.versions: list[dict[str, Any]] = []
        self.installed_versions: set[str] = set()
        self._filtered_versions: list[dict[str, Any]] = []
        self._version_meta: dict[str, dict[str, Any]] = {}
        self._loader_catalog_loaded = False

        self.selected_version = None
        self.selected_account_index = 0

        self.current_tab = "Play"
        self.closing = False
        self.busy = False
        self._last_win_size = (0, 0)
        self.bg_photo = None
        self._glass_mode = False
        self.glass_color = BG_DARK
        self._glass_applied = False
        self._shell_corners = {}
        self.background_alpha = float(self.settings.get("background_alpha", 1.0))
        self._img_rgb = _hex_rgb(CARD)

        self.discord = DiscordRPCController(DISCORD_CLIENT_ID)

        self._build_base()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<Configure>", self._on_window_resize)

        if self.settings.get("background"):
            self.after(120, self._load_background_image)
            self.after(380, self._refresh_appearance)

        self.after(50, self.start_background_initialization)

    def ui(self, callback, *args, **kwargs):
        if self.closing:
            return
        try:
            self.after(0, callback, *args, **kwargs)
        except Exception:
            pass

    def set_status(self, text):
        def update():
            if hasattr(self, "status_label"):
                self.status_label.configure(text=text)
        self.ui(update)

    def set_progress(self, value):
        def update():
            if hasattr(self, "progress"):
                self.progress.set(max(0.0, min(1.0, value)))
        self.ui(update)

    def start_background_initialization(self):
        threading.Thread(
            target=self._initialize_background,
            name="JanksyStartup",
            daemon=True,
        ).start()

    def _initialize_background(self):
        try:
            self.set_status("Detecting Minecraft...")
            if not self.minecraft_directory:
                self.minecraft_directory = self.detect_minecraft_directory()
                self.settings["minecraft_directory"] = self.minecraft_directory
                save_json(SETTINGS_FILE, self.settings)

            Path(self.minecraft_directory).mkdir(parents=True, exist_ok=True)

            self.set_status("Detecting Java...")
            if not self.java_path:
                self.java_path = find_java()
                self.settings["java_path"] = self.java_path
                save_json(SETTINGS_FILE, self.settings)

            self.set_status("Loading Minecraft versions...")
            self.load_version_data()

            self.set_status("Ready")
            self.set_progress(0)

            if self.settings.get("discord_rpc", False):
                self.start_rpc_background()

        except Exception as exc:
            log("Startup worker failed:", repr(exc))
            self.set_status(f"Startup warning: {exc}")

        self.ui(self._post_startup_refresh)

    def _post_startup_refresh(self):
        if hasattr(self, "java_entry"):
            self.java_entry.delete(0, "end")
            self.java_entry.insert(0, self.java_path)

        if hasattr(self, "directory_entry"):
            self.directory_entry.delete(0, "end")
            self.directory_entry.insert(0, self.minecraft_directory)

        self.refresh_accounts()

    def detect_minecraft_directory(self):
        if minecraft_launcher_lib:
            try:
                from minecraft_launcher_lib import utils
                return str(utils.get_minecraft_directory())
            except Exception as exc:
                log("minecraft-launcher-lib directory detection failed:", exc)

        if sys.platform == "win32":
            return str(Path(os.getenv("APPDATA", Path.home())) / ".minecraft")
        if sys.platform == "darwin":
            return str(Path.home() / "Library/Application Support/minecraft")
        return str(Path.home() / ".minecraft")

    def _build_base(self):
        # Full-window backdrop canvas. The custom background image (when set)
        # is drawn here and stays visible through the pack padding around the
        # floating glass cards below — this is what makes the background work.
        self.bg_canvas = ctk.CTkCanvas(self, bg=BG_DARK, highlightthickness=0)
        self.bg_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)

        shell_bg = self._shell_bg()

        self._create_sidebar(shell_bg)

        # The main content card floats over the backdrop. The pack padding
        # (22px ring + 20px gutter) is left empty, so the canvas artwork shows
        # through around the two glass cards.
        content_cc = self._shell_corners.get("content") if self._glass_mode else None
        self.content = ctk.CTkFrame(
            self,
            fg_color=shell_bg,
            bg_color=shell_bg,
            background_corner_colors=content_cc,
            corner_radius=24,
            border_width=1,
            border_color=OUTLINE,
        )
        self.content.pack(side="left", fill="both", expand=True, padx=(0, 22), pady=22)

        self.show_play()
        self._apply_shell_corners()

    def _shell_bg(self):
        """Background color used by the two big shell cards. In glass mode it is
        derived from the custom background image so the card corners blend into
        the artwork instead of showing hard dark notches."""
        return self.glass_color if self._glass_mode else BG_DARK

    def _sync_glass_mode(self):
        """Applies (or removes) the glass-mode window state. We keep the window
        fully opaque — transparency is simulated by tinting the glass panels
        toward the background image, so the UI buttons always stay crisp."""
        if self._glass_mode:
            self.configure(fg_color=self.glass_color)
        else:
            self.configure(fg_color=BG_DARK)

    def _compute_glass_color(self):
        """Glass-panel tint based on the desired opacity. High opacity keeps the
        solid theme card color; lower opacity blends toward the background image
        so the panels look more see-through while controls stay readable."""
        t = max(0.0, min(1.0, 1.0 - self.background_alpha))
        self.glass_color = _mix_colors(CARD, self._img_rgb, t)

    def _apply_shell_colors(self, include_corners=True):
        """Recolors the two shell cards' fill and (optionally) their corner
        squares to the current glass tint, without rebuilding anything."""
        if not hasattr(self, "sidebar") or not hasattr(self, "content"):
            return
        cc = self._shell_corners if self._glass_mode else {}
        for w in (self.sidebar, self.content):
            try:
                w.configure(
                    fg_color=self.glass_color,
                    bg_color=self.glass_color,
                    background_corner_colors=cc.get("sidebar" if w is self.sidebar else "content")
                    if include_corners else None,
                )
            except Exception:
                pass

    def _apply_shell_corners(self):
        """Blends the four corner squares of the shell cards into the backdrop
        image so there is no hard edge behind the rounded corners."""
        if not self._glass_mode:
            self._shell_corners = {}
        cc = self._shell_corners
        sidebar_cc = cc.get("sidebar")
        content_cc = cc.get("content")
        try:
            if sidebar_cc and hasattr(self, "sidebar"):
                self.sidebar.configure(background_corner_colors=sidebar_cc)
            if content_cc and hasattr(self, "content"):
                self.content.configure(background_corner_colors=content_cc)
        except Exception as exc:
            log("Shell corner configure failed:", exc)

    def _refresh_appearance(self):
        """Applies glass-mode colors everywhere, rebuilding once if needed.
        Safe to call multiple times."""
        if not self._glass_mode:
            return
        self._sync_glass_mode()
        if not self._glass_applied:
            self._glass_applied = True
            self._rebuild_ui()
        self._apply_shell_colors()

    def _create_sidebar(self, shell_bg):
        # Floating glass sidebar card. Packs with a 22px outer ring and a
        # 20px gutter before the content card so the backdrop peeks through.
        sidebar_cc = self._shell_corners.get("sidebar") if self._glass_mode else None
        sidebar = ctk.CTkFrame(
            self,
            fg_color=shell_bg,
            bg_color=shell_bg,
            background_corner_colors=sidebar_cc,
            corner_radius=24,
            border_width=1,
            border_color=OUTLINE,
            width=232,
        )
        sidebar.pack(side="left", fill="y", padx=(22, 20), pady=22)
        sidebar.pack_propagate(False)
        self.sidebar = sidebar

        logo_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        logo_frame.pack(pady=(30, 20))

        logo_badge = ctk.CTkFrame(
            logo_frame,
            fg_color=self.accent,
            corner_radius=18,
            width=58,
            height=58,
        )
        logo_badge.pack(pady=(0, 12))
        logo_badge.pack_propagate(False)
        ctk.CTkLabel(
            logo_badge,
            text="J",
            text_color="#0B0E14",
            font=ctk.CTkFont(size=30, weight="bold"),
        ).pack(expand=True)

        ctk.CTkLabel(
            logo_frame,
            text=APP_NAME,
            text_color=TEXT,
            font=ctk.CTkFont(size=19, weight="bold"),
        ).pack()
        ctk.CTkLabel(
            logo_frame,
            text="GLASS  EDITION",
            text_color=MUTED,
            font=ctk.CTkFont(size=9, weight="bold"),
        ).pack(pady=(3, 0))

        nav_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        nav_frame.pack(fill="both", expand=True, padx=14, pady=12)

        self.nav_buttons = {}
        nav_items = [
            ("Play", "▶"),
            ("Mod Manager", "🧩"),
            ("Servers", "🌍"),
            ("Modpacks", "🎁"),
            ("Settings", "⚙"),
            ("About", "ℹ"),
        ]
        for name, icon in nav_items:
            btn = ctk.CTkButton(
                nav_frame,
                text=f"{icon}   {name}",
                height=44,
                corner_radius=12,
                fg_color=self.accent if name == self.current_tab else "transparent",
                hover_color=CARD_2,
                text_color=TEXT,
                anchor="w",
                font=ctk.CTkFont(size=13, weight="bold"),
                command=lambda n=name: self.switch_tab(n),
            )
            btn.pack(fill="x", pady=4)
            self.nav_buttons[name] = btn

        footer = ctk.CTkFrame(sidebar, fg_color="transparent")
        footer.pack(fill="x", padx=14, pady=(0, 16))
        ctk.CTkLabel(
            footer,
            text=f"v{APP_VERSION}",
            text_color=MUTED,
            font=ctk.CTkFont(size=10),
        ).pack(anchor="w")
        ctk.CTkLabel(
            footer,
            text="Ready to play",
            text_color=self.accent,
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(anchor="w", pady=(4, 0))

    def switch_tab(self, tab):
        self.current_tab = tab

        for name, btn in self.nav_buttons.items():
            btn.configure(
                fg_color=self.accent if name == tab else "transparent"
            )

        for child in self.content.winfo_children():
            child.destroy()

        if tab == "Play":
            self.show_play()
        elif tab == "Mod Manager":
            self.show_mod_manager()
        elif tab == "Servers":
            self.show_servers()
        elif tab == "Modpacks":
            self.show_modpacks()
        elif tab == "Settings":
            self.show_settings()
        else:
            self.show_about()

    def _load_theme(self):
        """Applies the saved theme palette to the module-level color globals."""
        name = self.settings.get("theme", "Midnight Glass")
        if name not in THEMES:
            name = "Midnight Glass"
        globals().update(THEMES[name])
        self.theme_name = name

    def _rebuild_ui(self):
        """Destroys and rebuilds the whole window shell (used on theme/accent
        changes) so every widget picks up the new palette."""
        self._sync_glass_mode()
        tab = self.current_tab
        for child in self.winfo_children():
            child.destroy()
        self.bg_photo = None
        self._last_win_size = (0, 0)
        self._build_base()
        self.switch_tab(tab)
        if self.settings.get("background"):
            self.after(50, self._load_background_image)

    def _apply_theme(self, name):
        if name not in THEMES:
            return
        self.settings["theme"] = name
        save_json(SETTINGS_FILE, self.settings)
        self._load_theme()
        self._compute_glass_color()
        self.configure(fg_color=BG_DARK)
        self._rebuild_ui()
        log("Theme changed:", name)

    def _apply_accent(self, name):
        if name not in ACCENTS:
            return
        self.accent_name = name
        self.accent = ACCENTS[name]
        self.settings["accent"] = name
        save_json(SETTINGS_FILE, self.settings)
        self._rebuild_ui()
        log("Accent changed:", name)

    def show_play(self):
        root = ctk.CTkFrame(self.content, fg_color="transparent")
        root.pack(fill="both", expand=True, padx=10, pady=10)
        root.grid_columnconfigure(0, weight=0)
        root.grid_columnconfigure(1, weight=1)
        root.grid_rowconfigure(0, weight=1)

        self._build_account_panel(root)
        self._build_play_panel(root)
        self.refresh_accounts()

    def _build_account_panel(self, parent):
        panel = ctk.CTkFrame(
            parent,
            width=315,
            fg_color="transparent",
            corner_radius=22,
            border_width=1,
            border_color=OUTLINE,
        )
        panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        panel.grid_propagate(False)

        ctk.CTkLabel(
            panel,
            text="ACCOUNT",
            text_color=self.accent,
            font=ctk.CTkFont(size=11, weight="bold"),
        ).pack(anchor="w", padx=24, pady=(24, 4))

        ctk.CTkLabel(
            panel,
            text="Who's playing?",
            text_color=TEXT,
            font=ctk.CTkFont(size=23, weight="bold"),
        ).pack(anchor="w", padx=24)

        self.account_combo = ctk.CTkComboBox(
            panel,
            values=["No accounts"],
            height=44,
            corner_radius=12,
            fg_color=CARD_2,
            border_color=OUTLINE,
            button_color=self.accent,
            command=self.account_changed,
        )
        self.account_combo.pack(fill="x", padx=24, pady=(20, 10))

        actions = ctk.CTkFrame(panel, fg_color="transparent")
        actions.pack(fill="x", padx=20)

        ctk.CTkButton(
            actions,
            text="+ Add",
            height=37,
            corner_radius=10,
            fg_color=self.accent,
            hover_color=self.accent,
            command=self.add_account_dialog,
        ).pack(side="left", fill="x", expand=True, padx=3)

        ctk.CTkButton(
            actions,
            text="Edit",
            width=55,
            height=37,
            corner_radius=10,
            fg_color=CARD_2,
            hover_color=OUTLINE,
            command=self.edit_account,
        ).pack(side="left", padx=3)

        ctk.CTkButton(
            actions,
            text="Delete",
            width=65,
            height=37,
            corner_radius=10,
            fg_color=CARD_2,
            hover_color=ERROR,
            command=self.delete_account,
        ).pack(side="left", padx=3)

        self.account_card = ctk.CTkFrame(
            panel,
            fg_color=CARD_2,
            corner_radius=18,
            border_width=2,
            border_color=self.accent,
        )
        self.account_card.pack(fill="x", padx=24, pady=24)

        self.avatar = ctk.CTkLabel(
            self.account_card,
            text="?",
            width=76,
            height=76,
            corner_radius=18,
            fg_color=CARD,
            font=ctk.CTkFont(size=30, weight="bold"),
        )
        self.avatar.pack(pady=(18, 8))

        self.account_name = ctk.CTkLabel(
            self.account_card,
            text="No Account",
            text_color=TEXT,
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        self.account_name.pack()

        self.account_type = ctk.CTkLabel(
            self.account_card,
            text="ADD AN ACCOUNT",
            text_color=self.accent,
            font=ctk.CTkFont(size=10, weight="bold"),
        )
        self.account_type.pack(pady=(3, 18))

        ctk.CTkLabel(
            panel,
            text="JAVA PATH",
            text_color=MUTED,
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(anchor="w", padx=24)

        self.java_entry = ctk.CTkEntry(
            panel,
            height=38,
            corner_radius=10,
            fg_color=CARD_2,
            border_color=OUTLINE,
            placeholder_text="Auto-detected",
        )
        self.java_entry.pack(fill="x", padx=24, pady=(5, 7))

        if self.java_path:
            self.java_entry.insert(0, self.java_path)

        ctk.CTkButton(
            panel,
            text="Browse Java",
            height=33,
            corner_radius=9,
            fg_color="transparent",
            border_width=1,
            border_color=OUTLINE,
            hover_color=CARD_2,
            command=self.browse_java,
        ).pack(fill="x", padx=24)

    def _build_play_panel(self, parent):
        panel = ctk.CTkFrame(
            parent,
            fg_color="transparent",
            corner_radius=22,
            border_width=1,
            border_color=OUTLINE,
        )
        panel.grid(row=0, column=1, sticky="nsew")

        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(
            panel,
            text="PLAY",
            text_color=self.accent,
            font=ctk.CTkFont(size=11, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=28, pady=(24, 2))

        ctk.CTkLabel(
            panel,
            text="Choose your Minecraft",
            text_color=TEXT,
            font=ctk.CTkFont(size=27, weight="bold"),
        ).grid(row=1, column=0, sticky="w", padx=28)

        controls = ctk.CTkFrame(panel, fg_color="transparent")
        controls.grid(row=2, column=0, sticky="ew", padx=28, pady=18)
        
        self.search = ctk.CTkEntry(
            controls,
            height=40,
            corner_radius=11,
            fg_color=CARD_2,
            border_color=OUTLINE,
            placeholder_text="Search versions...",
        )
        self.search.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        controls.grid_columnconfigure(0, weight=1)
        
        self._search_job = None
        def schedule_version_filter(_event=None):
            if self._search_job is not None:
                try: self.after_cancel(self._search_job)
                except Exception: pass
            self._search_job = self.after(80, self.filter_versions)
        self.search.bind("<KeyRelease>", schedule_version_filter)

        self.installed_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            controls,
            text="Installed",
            variable=self.installed_var,
            command=self.filter_versions,
            fg_color=self.accent,
            hover_color=self.accent,
            border_width=2,
            corner_radius=6,
        ).grid(row=0, column=1, padx=(0, 10))

        self.snapshot_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            controls,
            text="Snapshots Only",
            variable=self.snapshot_var,
            command=self.filter_versions,
            fg_color=self.accent,
            hover_color=self.accent,
            border_width=2,
            corner_radius=6,
        ).grid(row=0, column=2, padx=(0, 10))

        self.loader_var = ctk.StringVar(value="All Loaders")
        ctk.CTkComboBox(
            controls,
            values=["All Loaders", "Vanilla", "Fabric", "Forge", "Quilt"],
            variable=self.loader_var,
            command=self.filter_versions,
            width=130,
            height=36,
            corner_radius=10,
            fg_color=CARD_2,
            border_color=OUTLINE,
        ).grid(row=0, column=3)
        # Fallback only: hidden automatically once the online loader catalog
        # loads and uninstalled loader versions appear in the list below.
        self.install_loader_btn = ctk.CTkButton(
            controls,
            text="+ Install Loader",
            height=36,
            corner_radius=10,
            fg_color=self.accent,
            hover_color=self.accent,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self.open_mod_installer,
        )
        self.install_loader_btn.grid(row=0, column=4, padx=(10, 0))

        self.version_frame = ctk.CTkScrollableFrame(
            panel,
            fg_color=CARD_2,
            corner_radius=15,
            border_width=1,
            border_color=OUTLINE,
        )
        self.version_frame.grid(row=3, column=0, sticky="nsew", padx=28, pady=(0, 15))

        bottom = ctk.CTkFrame(panel, fg_color="transparent")
        bottom.grid(row=4, column=0, sticky="ew", padx=28, pady=(0, 24))
        bottom.grid_columnconfigure(1, weight=1)

        self.status_label = ctk.CTkLabel(
            bottom,
            text="Initializing launcher...",
            text_color=MUTED,
            font=ctk.CTkFont(size=11),
        )
        self.status_label.grid(row=0, column=0, sticky="w")

        self.progress = ctk.CTkProgressBar(bottom, width=120, height=5)
        self.progress.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.progress.set(0)

        self.launch_btn = ctk.CTkButton(
            bottom,
            text="▶  L A U N C H",
            height=52,
            width=224,
            corner_radius=16,
            fg_color=self.accent,
            hover_color=self.accent,
            font=ctk.CTkFont(size=16, weight="bold"),
            command=self.launch_selected,
        )
        self.launch_btn.grid(row=0, column=2, rowspan=2, sticky="e")

        self.filter_versions()

    def load_version_data(self):
        try:
            from minecraft_launcher_lib import utils

            mc_dir = self.minecraft_directory
            Path(mc_dir).mkdir(parents=True, exist_ok=True)

            self.installed_versions = self._scan_installed_versions()

            raw = utils.get_version_list()
            vanilla: list[dict[str, Any]] = []
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, dict):
                        vid = str(item.get("id", ""))
                        vanilla.append({
                            "id": vid,
                            "type": str(item.get("type", "release")),
                            "installed": vid in self.installed_versions,
                        })

            # Fetch the online mod-loader catalogs (Fabric / Forge / Quilt) so
            # uninstalled loader versions show up inside the main version list.
            loader_entries = self._fetch_loader_catalog()

            self._version_meta = {}
            for entry in loader_entries:
                self._version_meta[entry["id"]] = entry

            by_mc: dict[str, list[dict[str, Any]]] = {}
            for entry in loader_entries:
                by_mc.setdefault(entry["mc_version"], []).append(entry)
            for group in by_mc.values():
                group.sort(key=lambda e: e["loader"])

            # Interleave loader entries directly beneath their Minecraft
            # version so they appear in the "main version place".
            merged: list[dict[str, Any]] = []
            covered_mc = set()
            for item in vanilla:
                merged.append(item)
                group = by_mc.get(item["id"])
                if group:
                    covered_mc.add(item["id"])
                    merged.extend(group)
            for mc, group in by_mc.items():
                if mc not in covered_mc:
                    merged.extend(group)

            # Installed loader folders are already represented above; only
            # surface genuinely custom/unknown version directories here.
            def _represented_by_catalog(name: str) -> bool:
                return self._loader_catalog_loaded and (
                    name.startswith("fabric-loader-")
                    or name.startswith("quilt-loader-")
                    or name.startswith("forge-")
                    or "-forge-" in name
                )

            for installed in self.installed_versions:
                if _represented_by_catalog(installed):
                    continue
                if not any(v["id"] == installed for v in merged):
                    merged.append({
                        "id": installed,
                        "type": "custom",
                        "installed": True,
                    })

            self.versions = merged
            self.ui(self.filter_versions)
            self.ui(self._update_install_loader_btn_visibility)
            log(f"Loaded {len(self.versions)} versions "
                f"({len(loader_entries)} loader entries).")
        except Exception as exc:
            log("Version fetch error:", exc)
            self.ui(lambda: self.set_status(f"Version error: {exc}"))

    @staticmethod
    def _stable_loader_versions(api) -> list[str]:
        """Stable Minecraft versions supported by a loader module.
        Compatible across different minecraft-launcher-lib releases."""
        try:
            if hasattr(api, "get_stable_minecraft_versions"):
                versions = [str(v) for v in api.get_stable_minecraft_versions() if v]
                if versions:
                    return versions
        except Exception:
            pass
        try:
            if hasattr(api, "get_all_minecraft_versions"):
                out = []
                for item in api.get_all_minecraft_versions():
                    if isinstance(item, dict):
                        if item.get("stable"):
                            out.append(str(item.get("version", "")))
                    else:
                        out.append(str(item))
                return [v for v in out if v]
        except Exception:
            pass
        try:
            if hasattr(api, "get_minecraft_version_list"):
                return [str(v) for v in api.get_minecraft_version_list() if v]
        except Exception:
            pass
        return []

    def _fetch_loader_catalog(self) -> list[dict[str, Any]]:
        """Builds selectable entries for every loader/MC combo offered online."""
        entries: list[dict[str, Any]] = []
        self._loader_catalog_loaded = False
        if not minecraft_launcher_lib:
            return entries

        try:
            from minecraft_launcher_lib import fabric as fabric_api
            from minecraft_launcher_lib import forge as forge_api
            from minecraft_launcher_lib import quilt as quilt_api
        except Exception as exc:
            log("Loader modules unavailable:", exc)
            return entries

        # --- Fabric ---
        try:
            for mc in self._stable_loader_versions(fabric_api):
                installed = any(
                    iv.startswith("fabric-loader-") and iv.endswith(f"-{mc}")
                    for iv in self.installed_versions
                )
                entries.append({
                    "id": f"fabric-{mc}",
                    "type": "fabric",
                    "loader": "fabric",
                    "mc_version": mc,
                    "installed": installed,
                })
        except Exception as exc:
            log("Fabric catalog fetch failed:", exc)

        # --- Quilt ---
        try:
            for mc in self._stable_loader_versions(quilt_api):
                installed = any(
                    iv.startswith("quilt-loader-") and iv.endswith(f"-{mc}")
                    for iv in self.installed_versions
                )
                entries.append({
                    "id": f"quilt-{mc}",
                    "type": "quilt",
                    "loader": "quilt",
                    "mc_version": mc,
                    "installed": installed,
                })
        except Exception as exc:
            log("Quilt catalog fetch failed:", exc)

        # --- Forge ---
        # The catalog lists every build (e.g. "1.21-51.0.33"); only the
        # latest build per Minecraft version is shown inline so the main
        # list stays clean.
        try:
            latest_by_mc: dict[str, tuple[tuple[int, ...], str, str]] = {}
            for build in forge_api.list_forge_versions():
                build = str(build)
                if "-" not in build:
                    continue
                mc = build.split("-", 1)[0]
                if not mc:
                    continue
                build_num = build.rsplit("-", 1)[-1]
                try:
                    key = tuple(int(p) for p in build_num.split("."))
                except ValueError:
                    key = (0,)
                cur = latest_by_mc.get(mc)
                if cur is not None and key <= cur[0]:
                    continue
                try:
                    installed_id = str(forge_api.forge_to_installed_version(build))
                except Exception:
                    installed_id = build
                latest_by_mc[mc] = (key, build, installed_id)

            for mc, (_key, build, installed_id) in latest_by_mc.items():
                entries.append({
                    "id": installed_id,
                    "type": "forge",
                    "loader": "forge",
                    "mc_version": mc,
                    "forge_build": build,
                    "installed": installed_id in self.installed_versions,
                })
        except Exception as exc:
            log("Forge catalog fetch failed:", exc)

        self._loader_catalog_loaded = bool(entries)
        return entries

    def _update_install_loader_btn_visibility(self):
        """Hides the fallback '+ Install Loader' button once the online
        loader catalog is available in the main version list."""
        btn = getattr(self, "install_loader_btn", None)
        if btn is None:
            return
        if self._loader_catalog_loaded:
            btn.grid_remove()
        else:
            btn.grid()

    def _scan_installed_versions(self) -> set[str]:
        """Re-scans the <minecraft>/versions directory and refreshes the
        installed-version cache straight from disk."""
        found: set[str] = set()
        versions_dir = Path(self.minecraft_directory) / "versions"
        if versions_dir.is_dir():
            try:
                for entry in versions_dir.iterdir():
                    if entry.is_dir() and (entry / f"{entry.name}.json").is_file():
                        found.add(entry.name)
            except OSError as exc:
                log("Versions dir scan failed:", exc)
        self.installed_versions = found
        return found

    def _find_installed_loader_dir(self, prefix: str, mc_version: str) -> str | None:
        # Re-scan the disk on every call so a loader version that was just
        # installed shows up immediately instead of after a launcher restart.
        candidates = [
            name for name in self._scan_installed_versions()
            if name.startswith(prefix) and name.endswith(f"-{mc_version}")
        ]
        if not candidates:
            return None
        versions_root = Path(self.minecraft_directory) / "versions"
        candidates.sort(
            key=lambda n: (versions_root / n).stat().st_mtime
            if (versions_root / n).exists() else 0,
            reverse=True,
        )
        return candidates[0]

    def _ensure_loader_installed(self, meta: dict[str, Any], set_max, set_progress, set_status) -> str:
        """Installs the selected mod loader on demand and returns the real
        installed version id that should be launched."""
        import minecraft_launcher_lib as mll

        loader = meta["loader"]
        mc = meta["mc_version"]
        cb = {"setMax": set_max, "setProgress": set_progress, "setStatus": set_status}

        if loader in ("fabric", "quilt"):
            existing = self._find_installed_loader_dir(f"{loader}-loader-", mc)
            if existing:
                set_status(f"{loader.capitalize()} {mc} already installed.")
                return existing

        if loader == "fabric":
            set_status(f"Installing Fabric for {mc}...")
            mll.fabric.install_fabric(
                mc, self.minecraft_directory, callback=cb,
                java=self.java_path or None,
            )
            actual = self._find_installed_loader_dir("fabric-loader-", mc)
        elif loader == "quilt":
            set_status(f"Installing Quilt for {mc}...")
            mll.quilt.install_quilt(
                mc, self.minecraft_directory, callback=cb,
                java=self.java_path or None,
            )
            actual = self._find_installed_loader_dir("quilt-loader-", mc)
        elif loader == "forge":
            actual = meta["id"]
            set_status(f"Installing Forge {actual}...")
            mll.forge.install_forge_version(
                meta.get("forge_build", actual), self.minecraft_directory, callback=cb
            )
        else:
            raise Exception(f"Unknown mod loader: {loader}")

        if not actual:
            raise Exception(f"Could not locate installed {loader} version for {mc}.")
        self.installed_versions.add(actual)
        # Refresh the on-screen version list right away so the freshly
        # installed version is flagged installed without relaunching.
        threading.Thread(target=self.load_version_data, daemon=True).start()
        return actual

    def open_mod_installer(self):
        InstallModDialog(
            parent=self,
            mc_dir=self.minecraft_directory,
            accent_color=self.accent,
            on_complete_callback=lambda: threading.Thread(target=self.load_version_data, daemon=True).start()
        )

    def filter_versions(self, *_args):
        query = self.search.get().lower().strip() if hasattr(self, "search") else ""
        installed_only = self.installed_var.get() if hasattr(self, "installed_var") else False
        snapshots_only = self.snapshot_var.get() if hasattr(self, "snapshot_var") else False
        loader = self.loader_var.get() if hasattr(self, "loader_var") else "All Loaders"

        self._filtered_versions = []
        for item in self.versions:
            vid = item["id"].lower()
            if query and query not in vid:
                continue
            if installed_only and not item.get("installed", False):
                continue
            
            is_snapshot = item.get("type") == "snapshot"
            if snapshots_only and not is_snapshot:
                continue
            if not snapshots_only and is_snapshot:
                continue
                
            item_loader = item.get("loader")
            if loader == "Fabric" and item_loader != "fabric" and "fabric" not in vid:
                continue
            if loader == "Forge" and item_loader != "forge" and "forge" not in vid:
                continue
            if loader == "Quilt" and item_loader != "quilt" and "quilt" not in vid:
                continue
            if loader == "Vanilla" and (
                item_loader or "fabric" in vid or "forge" in vid or "quilt" in vid
            ):
                continue

            self._filtered_versions.append(item)

        if hasattr(self, "version_frame"):
            self._draw_visible_versions()

    def _draw_visible_versions(self):
        for widget in self.version_frame.winfo_children():
            widget.destroy()

        if not self._filtered_versions:
            lbl = ctk.CTkLabel(
                self.version_frame,
                text="No matching versions found.",
                text_color=MUTED,
                font=ctk.CTkFont(size=14),
            )
            lbl.pack(pady=40)
            return

        for idx, item in enumerate(self._filtered_versions[:150]):
            self._create_version_item(item, idx)
            
        if len(self._filtered_versions) > 150:
            ctk.CTkLabel(
                self.version_frame, 
                text=f"...and {len(self._filtered_versions)-150} more. Refine your search.",
                text_color=MUTED
            ).pack(pady=10)

    def _create_version_item(self, item, index):
        vid = item["id"]
        is_installed = item.get("installed", False)
        vtype = item.get("type", "unknown")

        bg = CARD if index % 2 == 0 else "transparent"
        row = ctk.CTkFrame(self.version_frame, fg_color=bg, height=45, corner_radius=10)
        row.pack(fill="x", pady=2, padx=4)
        row.pack_propagate(False)

        indicator_color = SUCCESS if is_installed else MUTED
        ctk.CTkFrame(row, width=4, corner_radius=2, fg_color=indicator_color).pack(
            side="left", fill="y", pady=6, padx=(8, 12)
        )

        font = ctk.CTkFont(size=13, weight="bold")
        lbl = ctk.CTkLabel(row, text=vid, font=font, text_color=TEXT, anchor="w")
        lbl.pack(side="left")

        type_lbl = ctk.CTkLabel(
            row,
            text=vtype.upper(),
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color=LOADER_BADGE_COLORS.get(vtype, MUTED),
        )
        type_lbl.pack(side="left", padx=10)

        is_selected = self.selected_version == vid

        if is_installed:
            ctk.CTkButton(
                row,
                text="🗑",
                width=30,
                height=28,
                corner_radius=8,
                fg_color=CARD_2,
                hover_color=ERROR,
                font=ctk.CTkFont(size=11),
                command=lambda v=vid: self.delete_version(v),
            ).pack(side="right", padx=(0, 6))

        btn = ctk.CTkButton(
            row,
            text="Selected" if is_selected else ("Play" if is_installed else "Install"),
            width=70,
            height=28,
            corner_radius=8,
            fg_color=self.accent if is_selected else CARD_2,
            hover_color=self.accent,
            font=ctk.CTkFont(size=11, weight="bold"),
            command=lambda v=vid: self.select_version(v),
        )
        btn.pack(side="right", padx=12)

        if is_selected:
            row.configure(border_width=1, border_color=self.accent)
            lbl.configure(text_color=self.accent)

    def select_version(self, vid):
        if self.busy:
            return
        self.selected_version = vid
        self._draw_visible_versions()
        self.set_status(f"Selected: {vid}")
        log("Selected version:", vid)

    def delete_version(self, vid):
        """Permanently deletes the installed version folder(s) behind a
        selected version entry (loader catalog entries resolve to the real
        on-disk folder such as 'fabric-loader-...-1.20.1')."""
        if self.busy:
            return
        if not self.minecraft_directory:
            return

        meta = self._version_meta.get(vid)
        targets: list[str] = []
        if meta and meta.get("loader"):
            loader = str(meta.get("loader", ""))
            mc = str(meta.get("mc_version", ""))
            if loader == "forge":
                # Forge version ids are already the installed folder names.
                targets.append(vid)
            elif loader in ("fabric", "quilt") and mc:
                prefix = f"{loader}-loader-"
                targets = sorted(
                    name for name in self.installed_versions
                    if name.startswith(prefix) and name.endswith(f"-{mc}")
                )
        else:
            targets.append(vid)

        targets = [t for t in targets if t in self.installed_versions]
        if not targets:
            messagebox.showinfo(
                "Nothing to Delete",
                f"\"{vid}\" does not have any installed folders to remove.",
            )
            return

        details = "\n".join(f"•  {t}" for t in targets)
        if not messagebox.askyesno(
            "Delete Version",
            f"Delete the following installed version(s)?\n\n{details}\n\n"
            "The folder(s) will be removed permanently from your Minecraft directory.",
            default="no",
        ):
            return

        versions_root = Path(self.minecraft_directory) / "versions"
        deleted: list[str] = []
        failed: list[str] = []
        for name in targets:
            folder = versions_root / name
            try:
                if folder.is_dir():
                    shutil.rmtree(folder)
                elif folder.exists():
                    folder.unlink()
                else:
                    failed.append(f"{name} (folder already missing)")
                    continue
                self.installed_versions.discard(name)
                deleted.append(name)
            except Exception as exc:
                log(f"Failed to delete version {name}: {exc}")
                failed.append(f"{name} ({exc})")

        if deleted:
            if self.selected_version == vid:
                self.selected_version = None
            threading.Thread(target=self.load_version_data, daemon=True).start()
            self.set_status(f"Deleted {len(deleted)} version(s).")

        if failed:
            messagebox.showerror(
                "Delete Incomplete",
                "Removed:\n" + "\n".join(f"•  {d}" for d in deleted)
                + "\n\nCould not remove:\n" + "\n".join(f"•  {f}" for f in failed),
            )

    def start_rpc_background(self):
        if self.settings.get("discord_rpc", False):
            self.discord.initialize()

    def launch_selected(self):
        if self.busy:
            return
        if not self.selected_version:
            messagebox.showwarning("Warning", "No version selected!")
            return

        if not self.accounts:
            messagebox.showerror("Error", "Please add an account first!")
            return
        try:
            account = self.accounts[self.selected_account_index]
        except IndexError:
            messagebox.showerror("Error", "Invalid account selected!")
            return

        java_path = self.java_entry.get().strip() if hasattr(self, "java_entry") else self.java_path
        if not java_path or not Path(java_path).is_file():
            messagebox.showerror("Error", "Valid Java executable not found!")
            return

        self.java_path = java_path
        self.settings["java_path"] = java_path
        save_json(SETTINGS_FILE, self.settings)

        self.busy = True
        self.set_status(f"Preparing to launch {self.selected_version}...")
        self.progress.set(0)
        self.launch_btn.configure(state="disabled", text="Working...")

        threading.Thread(
            target=self._launch_thread,
            args=(account, self.selected_version, java_path),
            daemon=True,
        ).start()

    def _launch_thread(self, account, version, java_path):
        try:
            import json
            import minecraft_launcher_lib
            from minecraft_launcher_lib import command, install, runtime

            mc_dir = self.minecraft_directory
            max_progress = 100

            def set_max(val):
                nonlocal max_progress
                max_progress = val if val > 0 else 1

            def set_progress(val):
                self.ui(self.set_progress, val / max_progress)

            def set_status(text):
                self.ui(self.set_status, text)

            # Mod-loader selections (e.g. "fabric-1.21.11") are resolved into
            # their real installed version id here, installing on demand.
            meta = self._version_meta.get(version)
            if meta and meta.get("loader"):
                version = self._ensure_loader_installed(
                    meta, set_max, set_progress, set_status
                )

            base_version = version
            v_json_path = Path(mc_dir) / "versions" / version / f"{version}.json"
            
            if v_json_path.is_file():
                try:
                    with open(v_json_path, "r", encoding="utf-8") as f:
                        v_data = json.load(f)
                        base_version = v_data.get("inheritsFrom", version)
                except Exception as err:
                    log(f"Failed to read version JSON: {err}")
            elif "-" in version:
                parts = version.split("-")
                if len(parts) > 1 and parts[-1].replace(".", "").isdigit():
                    base_version = parts[-1]

            self.ui(self.set_status, f"Ensuring base Minecraft {base_version}...")
            try:
                install.install_minecraft_version(
                    version=base_version,
                    minecraft_directory=mc_dir,
                    callback={
                        "setMax": set_max,
                        "setProgress": set_progress,
                        "setStatus": set_status,
                    },
                )
            except Exception as exc:
                log(f"Base installer notice: {exc}")

            self.ui(self.set_status, f"Resolving Java runtime for {base_version}...")
            try:
                jvm_name = runtime.get_jvm_version_for_version(base_version, mc_dir)
            except Exception:
                jvm_name = None

            if not jvm_name:
                if any(v in base_version for v in ["1.20.5", "1.20.6", "1.21", "1.22"]):
                    jvm_name = "java-runtime-delta"
                elif any(v in base_version for v in ["1.17", "1.18", "1.19", "1.20"]):
                    jvm_name = "java-runtime-gamma"
                else:
                    jvm_name = "jre-legacy"

            self.ui(self.set_status, f"Ensuring Java runtime ({jvm_name})...")
            try:
                runtime.install_jvm_runtime(
                    jvm_name,
                    mc_dir,
                    callback={
                        "setMax": set_max,
                        "setProgress": set_progress,
                        "setStatus": set_status,
                    },
                )
            except Exception as err:
                log(f"JVM download warning: {err}")

            executable_path = runtime.get_executable_path(jvm_name, mc_dir)

            if not executable_path or not Path(executable_path).exists():
                if java_path and Path(java_path).exists():
                    executable_path = java_path
                else:
                    raise Exception(f"Could not locate Java executable for {jvm_name}.")

            self.ui(self.set_status, "Building launch command...")
            ram_mb = int(self.settings.get("ram", 6)) * 1024
            jvm_extra = shlex.split(self.settings.get("jvm_args", ""))
            
            res_w = int(self.settings.get("width", 1920))
            res_h = int(self.settings.get("height", 1080))

            opts = {
                "username": account.get("name", "Player"),
                "uuid": account.get("id", "00000000-0000-0000-0000-000000000000"),
                "token": account.get("token", "0"),
                "executablePath": executable_path,
                "gameDirectory": mc_dir,
                "jvmArguments": [
                    f"-Xmx{ram_mb}M",
                    f"-Xms{ram_mb}M",
                ] + jvm_extra,
                "customResolution": True,
                "resolutionWidth": str(res_w),
                "resolutionHeight": str(res_h),
            }

            cmd = command.get_minecraft_command(version, mc_dir, opts)

            self.ui(self.set_status, "Launching JVM...")
            
            process = subprocess.Popen(
                cmd,
                cwd=mc_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            
            self.ui(lambda: GameLogWindow(self, self.accent, process))
            self.ui(self._on_launch_success)

        except Exception as exc:
            import traceback
            full_traceback = traceback.format_exc()
            log("Launch failed with traceback:\n" + full_traceback)
            self.ui(self._on_launch_fail, exc)

    def _on_launch_success(self):
        behavior = self.settings.get("behavior", "Keep Open")
        if behavior == "Close":
            self.on_close()
        elif behavior == "Minimize":
            self.iconify()
        
        self.set_status("Game launched successfully.")
        self.progress.set(1.0)
        self.launch_btn.configure(state="normal", text="▶  L A U N C H")
        self.busy = False
        self.load_version_data()

    def _on_launch_fail(self, exc):
        self.set_status(f"Launch Error: {exc}")
        self.progress.set(0)
        self.launch_btn.configure(state="normal", text="▶  L A U N C H")
        self.busy = False
        messagebox.showerror("Launch Error", f"Failed to launch:\n{exc}")

    def show_mod_manager(self):
        panel = ctk.CTkFrame(
            self.content,
            fg_color="transparent",
            corner_radius=22,
            border_width=1,
            border_color=OUTLINE,
        )
        panel.pack(fill="both", expand=True, padx=10, pady=10)

        header = ctk.CTkFrame(panel, fg_color="transparent")
        header.pack(fill="x", padx=28, pady=(24, 10))

        ctk.CTkLabel(
            header, text="MOD MANAGER", text_color=self.accent, font=ctk.CTkFont(size=11, weight="bold")
        ).pack(anchor="w")

        ctk.CTkLabel(
            header, text="Manage Modifications", text_color=TEXT, font=ctk.CTkFont(size=27, weight="bold")
        ).pack(anchor="w")

        controls_frame = ctk.CTkFrame(panel, fg_color="transparent")
        controls_frame.pack(fill="x", padx=28, pady=(0, 10))

        self.mod_search_entry = ctk.CTkEntry(
            controls_frame,
            height=38,
            corner_radius=10,
            fg_color=CARD_2,
            border_color=OUTLINE,
            placeholder_text="🔍 Search installed mods...",
        )
        self.mod_search_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.mod_search_entry.bind("<KeyRelease>", lambda e: self.refresh_mods())

        ctk.CTkButton(
            controls_frame,
            text="📁 Open Folder",
            height=38,
            corner_radius=10,
            fg_color=CARD_2,
            hover_color=OUTLINE,
            border_width=1,
            border_color=OUTLINE,
            command=self._open_mods_folder,
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            controls_frame,
            text="+ Add Mod",
            height=38,
            corner_radius=10,
            fg_color=self.accent,
            hover_color=self.accent,
            command=self._browse_mod,
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            controls_frame,
            text="🌙 Modrinth",
            height=38,
            corner_radius=10,
            fg_color="#1BD96A",
            hover_color="#2FE07F",
            command=lambda: webbrowser.open("https://modrinth.com/mods"),
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            controls_frame,
            text="🔥 CurseForge",
            height=38,
            corner_radius=10,
            fg_color="#FF8800",
            hover_color="#FFA500",
            command=lambda: webbrowser.open("https://www.curseforge.com/minecraft/mods"),
        ).pack(side="left", padx=2)

        self.drop_box = ctk.CTkFrame(
            panel,
            fg_color=CARD_2,
            corner_radius=12,
            border_width=2,
            border_color=OUTLINE,
            height=65,
        )
        self.drop_box.pack(fill="x", padx=28, pady=(0, 12))
        self.drop_box.pack_propagate(False)

        self.drop_label = ctk.CTkLabel(
            self.drop_box,
            text="📥 Drag & Drop .jar files here to install",
            text_color=MUTED,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.drop_label.pack(expand=True)

        if DND_FILES is not None:
            try:
                self.drop_box.drop_target_register(DND_FILES)
                self.drop_box.dnd_bind("<<DropEnter>>", self._on_drag_enter)
                self.drop_box.dnd_bind("<<DropLeave>>", self._on_drag_leave)
                self.drop_box.dnd_bind("<<Drop>>", self._on_mod_drop_event)
            except Exception as exc:
                log(f"Failed to register Drop Target: {exc}")

        self.mods_scroll = ctk.CTkScrollableFrame(
            panel, fg_color=CARD_2, corner_radius=15, border_width=1, border_color=OUTLINE
        )
        self.mods_scroll.pack(fill="both", expand=True, padx=28, pady=(0, 24))

        self.refresh_mods()

    def refresh_mods(self):
        if self.current_tab != "Mod Manager" or not hasattr(self, "mods_scroll"):
            return

        for widget in self.mods_scroll.winfo_children():
            widget.destroy()

        md = self._get_mods_dir()

        active_mods = list(md.glob("*.jar"))
        disabled_mods = list(md.glob("*.jar.disabled"))
        all_mods = active_mods + disabled_mods

        query = (
            self.mod_search_entry.get().lower().strip()
            if hasattr(self, "mod_search_entry")
            else ""
        )
        if query:
            all_mods = [m for m in all_mods if query in m.name.lower()]

        if not all_mods:
            msg = (
                "No matching mods found."
                if query
                else "No mods installed.\nAdd some .jar files to get started."
            )
            ctk.CTkLabel(
                self.mods_scroll,
                text=msg,
                text_color=MUTED,
                font=ctk.CTkFont(size=14),
            ).pack(pady=40)
            return

        all_mods.sort(key=lambda x: x.name.lower())
        for mod_path in all_mods:
            self._create_mod_row(mod_path)

    def _create_mod_row(self, mod_path: Path):
        is_enabled = not mod_path.name.endswith(".disabled")
        display_name = mod_path.name.removesuffix(".disabled")

        row = ctk.CTkFrame(self.mods_scroll, fg_color=CARD, height=55, corner_radius=10)
        row.pack(fill="x", pady=4, padx=8)
        row.pack_propagate(False)

        switch_var = ctk.BooleanVar(value=is_enabled)
        switch = ctk.CTkSwitch(
            row,
            text="",
            width=45,
            variable=switch_var,
            progress_color=self.accent,
            command=lambda: self._toggle_mod(mod_path, switch_var.get()),
        )
        switch.pack(side="left", padx=(16, 5))

        info = ctk.CTkFrame(row, fg_color="transparent")
        info.pack(side="left", padx=10, fill="y", pady=8)

        title_color = TEXT if is_enabled else MUTED
        status_suffix = "" if is_enabled else " (Disabled)"

        ctk.CTkLabel(
            info,
            text=f"{display_name}{status_suffix}",
            text_color=title_color,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w")

        size_mb = mod_path.stat().st_size / (1024 * 1024)
        ctk.CTkLabel(
            info, text=f"{size_mb:.2f} MB", text_color=MUTED, font=ctk.CTkFont(size=10)
        ).pack(anchor="w")

        ctk.CTkButton(
            row,
            text="Delete",
            width=65,
            height=30,
            corner_radius=8,
            fg_color=CARD_2,
            hover_color=ERROR,
            command=lambda: self._delete_mod(mod_path),
        ).pack(side="right", padx=16)

    def _toggle_mod(self, mod_path: Path, enable: bool):
        try:
            if enable and mod_path.name.endswith(".disabled"):
                new_path = mod_path.with_name(mod_path.name.removesuffix(".disabled"))
                mod_path.rename(new_path)
            elif not enable and not mod_path.name.endswith(".disabled"):
                new_path = mod_path.with_name(mod_path.name + ".disabled")
                mod_path.rename(new_path)
            self.refresh_mods()
        except Exception as exc:
            messagebox.showerror("Error", f"Failed to toggle mod state:\n{exc}")
            self.refresh_mods()

    def _on_drag_enter(self, event):
        self.drop_box.configure(border_color=self.accent, fg_color=CARD_2)
        self.drop_label.configure(
            text="✨ Release to install mod!", text_color=self.accent
        )

    def _on_drag_leave(self, event):
        self.drop_box.configure(border_color=OUTLINE, fg_color=CARD)
        self.drop_label.configure(
            text="📥 Drag & Drop .jar files here to install", text_color=MUTED
        )

    def _on_mod_drop_event(self, event):
        self._on_drag_leave(event)
        self._on_mod_drop(event)

    def _get_mods_dir(self) -> Path:
        base_dir = Path(self.minecraft_directory) if self.minecraft_directory else (DATA_DIR / ".minecraft")
        mods_dir = base_dir / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)
        return mods_dir

    def _open_mods_folder(self):
        try:
            os.startfile(self._get_mods_dir())
        except Exception as exc:
            messagebox.showerror("Error", f"Could not open mods folder:\n{exc}")

    def _browse_mod(self):
        file_paths = filedialog.askopenfilenames(
            title="Select Minecraft Mods",
            filetypes=[("Minecraft Mods (*.jar)", "*.jar"), ("All Files", "*.*")],
        )
        if file_paths:
            mods_dir = self._get_mods_dir()
            for fp in file_paths:
                src = Path(fp)
                shutil.copy(src, mods_dir / src.name)
            self.refresh_mods()

    def _delete_mod(self, mod_path: Path):
        confirm = messagebox.askyesno(
            "Delete Mod", f"Are you sure you want to delete '{mod_path.name}'?"
        )
        if confirm:
            try:
                mod_path.unlink()
                self.refresh_mods()
            except Exception as exc:
                messagebox.showerror("Error", f"Could not delete mod:\n{exc}")

    def _on_mod_drop(self, event):
        try:
            raw_data = event.data
            if not raw_data:
                return

            file_paths = self.tk.splitlist(raw_data)
            mods_dir = self._get_mods_dir()
            installed_any = False

            for fp in file_paths:
                clean_path = fp.strip("{}").strip()
                p = Path(clean_path)
                if p.exists() and (
                    p.suffix.lower() == ".jar"
                    or p.name.lower().endswith(".jar.disabled")
                ):
                    shutil.copy(p, mods_dir / p.name)
                    installed_any = True

            if installed_any:
                self.refresh_mods()
                messagebox.showinfo("Success", "Mod(s) added successfully!")
            else:
                messagebox.showwarning(
                    "Invalid File", "No valid .jar mod files were detected."
                )

        except Exception as exc:
            messagebox.showerror("Drop Error", f"Failed to install dropped files:\n{exc}")

    def show_servers(self):
        panel = ctk.CTkFrame(
            self.content,
            fg_color="transparent",
            corner_radius=22,
            border_width=1,
            border_color=OUTLINE,
        )
        panel.pack(fill="both", expand=True, padx=10, pady=10)

        header = ctk.CTkFrame(panel, fg_color="transparent")
        header.pack(fill="x", padx=28, pady=(24, 10))
        ctk.CTkLabel(
            header, text="SERVERS", text_color=self.accent,
            font=ctk.CTkFont(size=11, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            header, text="Minecraft Servers", text_color=TEXT,
            font=ctk.CTkFont(size=27, weight="bold"),
        ).pack(anchor="w")

        controls = ctk.CTkFrame(panel, fg_color="transparent")
        controls.pack(fill="x", padx=28, pady=(0, 10))
        ctk.CTkLabel(
            controls,
            text=f"{len(SERVERS)} server(s) tracked",
            text_color=MUTED,
            font=ctk.CTkFont(size=11),
        ).pack(side="left")
        ctk.CTkButton(
            controls,
            text="🔄 Refresh All",
            height=36,
            corner_radius=10,
            fg_color=self.accent,
            hover_color=self.accent,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self.refresh_all_servers,
        ).pack(side="right")

        self.servers_scroll = ctk.CTkScrollableFrame(
            panel, fg_color=CARD_2, corner_radius=15, border_width=1, border_color=OUTLINE
        )
        self.servers_scroll.pack(fill="both", expand=True, padx=28, pady=(0, 24))

        self._build_server_cards()
        self.refresh_all_servers()

    def _build_server_cards(self):
        for w in self.servers_scroll.winfo_children():
            w.destroy()
        self._server_widgets = []
        self._server_imgs = {}
        for idx, srv in enumerate(SERVERS):
            self._server_widgets.append(self._create_server_card(srv, idx))

    def _create_server_card(self, srv, idx):
        card = ctk.CTkFrame(
            self.servers_scroll, fg_color=CARD, height=92, corner_radius=12,
            border_width=1, border_color=OUTLINE,
        )
        card.pack(fill="x", pady=6, padx=8)
        card.pack_propagate(False)

        logo = ctk.CTkLabel(
            card, text=srv["name"][0].upper(), width=54, height=54,
            corner_radius=14, fg_color=self.accent, text_color="#0B0E14",
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        logo.pack(side="left", padx=(16, 12), pady=20)

        info = ctk.CTkFrame(card, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, pady=16)
        ctk.CTkLabel(
            info, text=srv["name"], text_color=TEXT,
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w")
        motd_lbl = ctk.CTkLabel(
            info, text="Checking status...", text_color=MUTED,
            font=ctk.CTkFont(size=11),
        )
        motd_lbl.pack(anchor="w")
        # Show the user-facing address (subdomain + port). The main connect
        # host is hidden — it's edited in the SERVERS config in the code.
        ctk.CTkLabel(
            info, text=srv.get("display", ""),
            text_color=self.accent, font=ctk.CTkFont(size=11, weight="bold"),
        ).pack(anchor="w")

        ctk.CTkButton(
            card,
            text="📋 Copy IP", width=92, height=32, corner_radius=9,
            fg_color=CARD_2, hover_color=OUTLINE,
            font=ctk.CTkFont(size=11, weight="bold"),
            command=lambda d=srv.get("display", ""): self._copy_server_ip(d),
        ).pack(side="right", padx=(0, 12), pady=30)

        right = ctk.CTkFrame(card, fg_color="transparent")
        right.pack(side="right", padx=(0, 10), pady=16)
        players_lbl = ctk.CTkLabel(
            right, text="—", text_color=TEXT,
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        players_lbl.pack(anchor="e")
        state_lbl = ctk.CTkLabel(
            right, text="Checking...", text_color=MUTED,
            font=ctk.CTkFont(size=10),
        )
        state_lbl.pack(anchor="e")
        ctk.CTkButton(
            right, text="Refresh", width=66, height=24, corner_radius=8,
            fg_color=CARD_2, hover_color=OUTLINE,
            font=ctk.CTkFont(size=10, weight="bold"),
            command=lambda i=idx: self.refresh_one_server(i),
        ).pack(anchor="e", pady=(6, 0))

        return {
            "logo": logo, "motd": motd_lbl, "players": players_lbl,
            "state": state_lbl, "card": card,
        }

    def refresh_all_servers(self):
        if not hasattr(self, "_server_widgets"):
            return
        for idx in range(len(SERVERS)):
            self.refresh_one_server(idx)

    def refresh_one_server(self, idx):
        if not hasattr(self, "_server_widgets"):
            return
        try:
            widgets = self._server_widgets[idx]
            widgets["state"].configure(text="Checking...", text_color=MUTED)
            widgets["players"].configure(text="…", text_color=MUTED)
            widgets["motd"].configure(text="Contacting server...", text_color=MUTED)
        except Exception:
            return

        def worker(i=idx):
            srv = SERVERS[i]
            status = get_minecraft_server_status(srv.get("host"), srv.get("port"))
            self.ui(self._update_server_card, i, status)

        threading.Thread(target=worker, daemon=True).start()

    def _update_server_card(self, idx, status):
        try:
            widgets = self._server_widgets[idx]
        except Exception:
            return
        if status["online"]:
            version = status["version"] or "Minecraft"
            widgets["state"].configure(text=version, text_color=SUCCESS)
            widgets["players"].configure(
                text=f"{status['players_online']} / {status['players_max']}",
                text_color=TEXT,
            )
            motd = (status["description"] or "Server is online").strip()
            widgets["motd"].configure(text=motd[:90], text_color=MUTED)
            widgets["card"].configure(border_color=SUCCESS)
            if status.get("favicon_b64"):
                try:
                    import base64
                    from io import BytesIO
                    raw = base64.b64decode(status["favicon_b64"])
                    img = Image.open(BytesIO(raw)).convert("RGBA")
                    img = img.resize((54, 54), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(img)
                    self._server_imgs[idx] = photo
                    widgets["logo"].configure(image=photo, text="")
                except Exception:
                    pass
        else:
            widgets["state"].configure(text="Offline", text_color=ERROR)
            widgets["players"].configure(text="—", text_color=MUTED)
            widgets["motd"].configure(
                text="Server did not respond to a status ping.", text_color=MUTED
            )
            widgets["card"].configure(border_color=OUTLINE)

    def _copy_server_ip(self, display):
        try:
            self.clipboard_clear()
            self.clipboard_append(display)
        except Exception:
            pass
        self.set_status(f"Copied {display} to clipboard.")

    def show_modpacks(self):
        panel = ctk.CTkFrame(
            self.content,
            fg_color="transparent",
            corner_radius=22,
            border_width=1,
            border_color=OUTLINE,
        )
        panel.pack(fill="both", expand=True, padx=10, pady=10)

        header = ctk.CTkFrame(panel, fg_color="transparent")
        header.pack(fill="x", padx=28, pady=(24, 10))

        ctk.CTkLabel(header, text="EXPLORE MODPACKS", text_color=self.accent, font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(header, text="Curated Content", text_color=TEXT, font=ctk.CTkFont(size=27, weight="bold")).pack(anchor="w")

        search_box = ctk.CTkFrame(panel, fg_color="transparent")
        search_box.pack(fill="x", padx=28, pady=8)

        search_entry = ctk.CTkEntry(
            search_box, height=40, corner_radius=11, fg_color=CARD_2,
            border_color=OUTLINE, placeholder_text="🔍 Search online modpacks..."
        )
        search_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))

        ctk.CTkButton(search_box, text="Search", width=100, height=40, corner_radius=11, fg_color=self.accent).pack(side="right")

        cards_scroll = ctk.CTkScrollableFrame(panel, fg_color=CARD_2, corner_radius=15, border_width=1, border_color=OUTLINE)
        cards_scroll.pack(fill="both", expand=True, padx=28, pady=(10, 24))

        mock_packs = [
            {"title": "Janksy Performance Pack", "desc": "Sodium, Lithium, and FerriteCore ultra optimization.", "ver": "1.20.4", "author": "Janksy Team"},
            {"title": "Cybercraft Horizons", "desc": "Futuristic tech mods with Industrial Foregoing.", "ver": "1.20.1", "author": "TechForge"},
            {"title": "RPG Quest Master", "desc": "Dungeon crawling, custom bosses, and magic spells.", "ver": "1.19.2", "author": "MythicGuild"},
            {"title": "Skyblock Reborn", "desc": "Start on a single dirt block and automate everything.", "ver": "1.20.1", "author": "SkyTeam"},
            {"title": "Vanilla+ Expeditions", "desc": "Enhanced vanilla feeling with new biomes and structures.", "ver": "1.20.4", "author": "Explorer"},
            {"title": "Pixelmon Adventure", "desc": "Catch 'em all in this expansive Pokémon world.", "ver": "1.16.5", "author": "PokeMasters"}
        ]

        for pack in mock_packs:
            card = ctk.CTkFrame(cards_scroll, fg_color=CARD, corner_radius=12, border_width=1, border_color=OUTLINE)
            card.pack(fill="x", padx=10, pady=8)

            info = ctk.CTkFrame(card, fg_color="transparent")
            info.pack(side="left", padx=16, pady=16)

            ctk.CTkLabel(info, text=pack["title"], text_color=TEXT, font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w")
            ctk.CTkLabel(info, text=pack["desc"], text_color=MUTED, font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(2, 0))
            ctk.CTkLabel(info, text=f"Version: {pack['ver']} • By {pack['author']}", text_color=self.accent, font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", pady=(4, 0))

            ctk.CTkButton(card, text="Install", width=100, height=36, corner_radius=10, fg_color=self.accent, hover_color=self.accent).pack(side="right", padx=20)

    def show_settings(self):
        panel = ctk.CTkFrame(
            self.content,
            fg_color="transparent",
            corner_radius=22,
            border_width=1,
            border_color=OUTLINE,
        )
        panel.pack(fill="both", expand=True, padx=10, pady=10)

        header = ctk.CTkFrame(panel, fg_color="transparent")
        header.pack(fill="x", padx=28, pady=(24, 10))
        ctk.CTkLabel(header, text="SETTINGS", text_color=self.accent, font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(header, text="Launcher Config", text_color=TEXT, font=ctk.CTkFont(size=27, weight="bold")).pack(anchor="w")

        scroll = ctk.CTkScrollableFrame(panel, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=28, pady=(0, 24))

        # --- Appearance: theme + accent color ---
        self._build_setting_section(scroll, "Appearance")

        theme_row = ctk.CTkFrame(scroll, fg_color=CARD_2, corner_radius=12, border_width=1, border_color=OUTLINE)
        theme_row.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(
            theme_row, text="Theme", text_color=TEXT,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left", padx=20, pady=15)
        self.theme_combo = ctk.CTkComboBox(
            theme_row,
            values=[t for t in THEMES],
            width=180,
            height=36,
            corner_radius=10,
            fg_color=CARD,
            border_color=OUTLINE,
            button_color=self.accent,
            command=self._apply_theme,
        )
        self.theme_combo.pack(side="right", padx=20)
        self.theme_combo.set(self.theme_name)

        accent_row = ctk.CTkFrame(scroll, fg_color=CARD_2, corner_radius=12, border_width=1, border_color=OUTLINE)
        accent_row.pack(fill="x", pady=(0, 15))
        ctk.CTkLabel(
            accent_row, text="Accent Color", text_color=TEXT,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left", padx=20, pady=15)
        self.accent_combo = ctk.CTkComboBox(
            accent_row,
            values=[a for a in ACCENTS],
            width=180,
            height=36,
            corner_radius=10,
            fg_color=CARD,
            border_color=OUTLINE,
            button_color=self.accent,
            command=self._apply_accent,
        )
        self.accent_combo.pack(side="right", padx=20)
        self.accent_combo.set(self.accent_name)

        self._build_setting_section(scroll, "Minecraft Directory")
        dir_frame = ctk.CTkFrame(scroll, fg_color=CARD_2, corner_radius=12, border_width=1, border_color=OUTLINE)
        dir_frame.pack(fill="x", pady=(0, 15))
        
        dir_inner = ctk.CTkFrame(dir_frame, fg_color="transparent")
        dir_inner.pack(fill="x", padx=20, pady=15)
        
        self.directory_entry = ctk.CTkEntry(dir_inner, placeholder_text="Minecraft Install Path...", fg_color=CARD, border_color=OUTLINE)
        self.directory_entry.pack(side="left", fill="x", expand=True)
        if self.minecraft_directory:
            self.directory_entry.insert(0, self.minecraft_directory)
            
        ctk.CTkButton(dir_inner, text="Browse", width=80, fg_color=CARD, hover_color=OUTLINE, command=self._browse_mc_directory).pack(side="left", padx=(10, 0))

        self._build_setting_section(scroll, "Memory Allocation")
        self.ram_var = ctk.IntVar(value=int(self.settings.get("ram", 6)))
        
        mem_frame = ctk.CTkFrame(scroll, fg_color=CARD_2, corner_radius=12, border_width=1, border_color=OUTLINE)
        mem_frame.pack(fill="x", pady=(0, 15))
        
        self.ram_lbl = ctk.CTkLabel(mem_frame, text=f"{self.ram_var.get()} GB Allocated", font=ctk.CTkFont(weight="bold"))
        self.ram_lbl.pack(padx=20, pady=(15, 0), anchor="w")
        
        slider = ctk.CTkSlider(
            mem_frame,
            from_=2, to=32, number_of_steps=30,
            variable=self.ram_var,
            button_color=self.accent,
            progress_color=self.accent,
            command=self._on_ram_slide
        )
        slider.pack(fill="x", padx=20, pady=(10, 20))
        slider.bind("<ButtonRelease-1>", lambda e: self._save_setting("ram", self.ram_var.get()))

        self._build_setting_section(scroll, "Launcher Behavior")
        behav_frame = ctk.CTkFrame(scroll, fg_color=CARD_2, corner_radius=12, border_width=1, border_color=OUTLINE)
        behav_frame.pack(fill="x", pady=(0, 15))
        
        self.behavior_var = ctk.StringVar(value=self.settings.get("behavior", "Keep Open"))
        ctk.CTkSegmentedButton(
            behav_frame,
            values=["Keep Open", "Minimize", "Close"],
            variable=self.behavior_var,
            selected_color=self.accent,
            command=lambda v: self._save_setting("behavior", v)
        ).pack(fill="x", padx=20, pady=15)

        self._build_setting_section(scroll, "Discord RPC")
        rpc_frame = ctk.CTkFrame(scroll, fg_color=CARD_2, corner_radius=12, border_width=1, border_color=OUTLINE)
        rpc_frame.pack(fill="x", pady=(0, 15))
        
        self.rpc_var = ctk.BooleanVar(value=self.settings.get("discord_rpc", False))
        ctk.CTkSwitch(
            rpc_frame,
            text="Enable Discord Rich Presence",
            variable=self.rpc_var,
            progress_color=self.accent,
            command=lambda: self._toggle_discord(self.rpc_var.get())
        ).pack(padx=20, pady=15, anchor="w")

        self._build_setting_section(scroll, "App Background Image")
        bg_frame = ctk.CTkFrame(scroll, fg_color=CARD_2, corner_radius=12, border_width=1, border_color=OUTLINE)
        bg_frame.pack(fill="x", pady=(0, 15))
        
        bg_inner = ctk.CTkFrame(bg_frame, fg_color="transparent")
        bg_inner.pack(fill="x", padx=20, pady=15)
        
        self.bg_entry = ctk.CTkEntry(bg_inner, placeholder_text="Background Image Path...", fg_color=CARD, border_color=OUTLINE)
        self.bg_entry.pack(side="left", fill="x", expand=True)
        if self.settings.get("background"):
            self.bg_entry.insert(0, self.settings.get("background"))
            
        ctk.CTkButton(bg_inner, text="Browse", width=80, fg_color=CARD, hover_color=OUTLINE, command=self._browse_bg_image).pack(side="left", padx=(10, 0))
        ctk.CTkButton(bg_inner, text="Clear", width=80, fg_color=CARD, hover_color=ERROR, command=self._clear_bg_image).pack(side="left", padx=(10, 0))

        self._build_setting_section(scroll, "Background Transparency")
        trans_frame = ctk.CTkFrame(scroll, fg_color=CARD_2, corner_radius=12, border_width=1, border_color=OUTLINE)
        trans_frame.pack(fill="x", pady=(0, 15))

        self.bg_alpha_var = ctk.DoubleVar(value=self.background_alpha)
        self.bg_alpha_lbl = ctk.CTkLabel(
            trans_frame,
            text=f"Glass Panel Opacity  {int(round(self.bg_alpha_var.get() * 100))}%",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.bg_alpha_lbl.pack(padx=20, pady=(15, 0), anchor="w")
        ctk.CTkLabel(
            trans_frame,
            text="Lower = more see-through glass panels. Buttons and text always stay clear.",
            text_color=MUTED,
            font=ctk.CTkFont(size=10),
        ).pack(padx=20, anchor="w")
        alpha_slider = ctk.CTkSlider(
            trans_frame,
            from_=0.5, to=1.0, number_of_steps=50,
            variable=self.bg_alpha_var,
            button_color=self.accent,
            progress_color=self.accent,
            command=self._on_bg_alpha_slide,
        )
        alpha_slider.pack(fill="x", padx=20, pady=(10, 20))
        alpha_slider.bind("<ButtonRelease-1>", self._save_bg_alpha)

    def _on_bg_alpha_slide(self, val):
        try:
            self.bg_alpha_lbl.configure(
                text=f"Glass Panel Opacity  {int(round(float(val) * 100))}%"
            )
        except Exception:
            pass

    def _save_bg_alpha(self, _event=None):
        val = round(float(self.bg_alpha_var.get()), 2)
        self.background_alpha = val
        self.settings["background_alpha"] = val
        save_json(SETTINGS_FILE, self.settings)
        if self._glass_mode:
            self._compute_glass_color()
            self._apply_shell_colors(include_corners=True)

    def _build_setting_section(self, parent, title):
        ctk.CTkLabel(parent, text=title, text_color=MUTED, font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w", pady=(15, 5))

    def _browse_mc_directory(self):
        folder = filedialog.askdirectory(title="Select Minecraft Directory")
        if folder:
            self.minecraft_directory = folder
            self.settings["minecraft_directory"] = folder
            save_json(SETTINGS_FILE, self.settings)
            if hasattr(self, "directory_entry"):
                self.directory_entry.delete(0, "end")
                self.directory_entry.insert(0, folder)
            threading.Thread(target=self.load_version_data, daemon=True).start()

    def _on_ram_slide(self, val):
        self.ram_lbl.configure(text=f"{int(val)} GB Allocated")

    def _save_setting(self, key, value):
        self.settings[key] = value
        save_json(SETTINGS_FILE, self.settings)

    def _toggle_discord(self, enabled):
        self._save_setting("discord_rpc", enabled)
        if enabled:
            self.discord.initialize()
            if self.selected_version:
                self.discord.update("In Launcher", f"Ready to play {self.selected_version}")
        else:
            self.discord.shutdown()

    def _load_background_image(self):
        if not hasattr(self, "bg_canvas"):
            return

        self.update_idletasks()
        width = max(self.winfo_width(), 1050)
        height = max(self.winfo_height(), 700)

        raw_path = str(self.settings.get("background", "")).strip().strip('"\'')
        if not raw_path:
            self._glass_mode = False
            self.bg_canvas.delete("bg_img")
            self.bg_canvas.configure(bg=BG_DARK)
            return

        bg_path = Path(raw_path)
        if not bg_path.exists() or not bg_path.is_file():
            self._glass_mode = False
            self.bg_canvas.delete("bg_img")
            self.bg_canvas.configure(bg=BG_DARK)
            return

        if not Image or not ImageTk:
            return

        try:
            from PIL import ImageDraw, ImageFilter

            img = Image.open(bg_path).convert("RGBA")

            img_w, img_h = img.size
            scale = max(width / img_w, height / img_h)
            new_w, new_h = int(img_w * scale), int(img_h * scale)

            resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

            left = (new_w - width) // 2
            top = (new_h - height) // 2
            cropped = resized.crop((left, top, left + width, top + height))

            # Light veil so the artwork stays bright and clearly visible.
            dark_tint = Image.new("RGBA", (width, height), (15, 17, 23, 70))
            glass_final = Image.alpha_composite(cropped, dark_tint)

            # Derive the tint for the shell cards from the artwork, so
            # the cards feel like part of the image (and their corner squares
            # stop clashing as hard dark notches). Store the average colour and
            # compute the glass fill from the current opacity slider.
            avg = cropped.resize((1, 1), Image.Resampling.BILINEAR).getpixel((0, 0))
            avg = avg[:3]
            self._img_rgb = avg
            self._compute_glass_color()

            # Soft blurred shadows behind the two glass cards smooth the harsh
            # cardboard-color transition between the artwork and the curved
            # shell corners (hiding the frame corner squares).
            try:
                from PIL import ImageDraw, ImageFilter
                e = 8  # how far the shadow extends past each card
                shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                sd = ImageDraw.Draw(shadow)
                # sidebar card footprint: x=22, y=22, w=232, h=height-44
                sd.rounded_rectangle(
                    [22 - e, 22 - e, 22 + 232 + e, height - 22 + e],
                    radius=30,
                    fill=(4, 6, 10, 52),
                )
                # content card footprint: x=274, y=22, w=width-296
                sd.rounded_rectangle(
                    [274 - e, 22 - e, width - 22 + e, height - 22 + e],
                    radius=30,
                    fill=(4, 6, 10, 52),
                )
                shadow = shadow.filter(ImageFilter.GaussianBlur(8))
                glass_final = Image.alpha_composite(glass_final, shadow)
            except Exception as exc:
                log("Background shadow render failed:", exc)

            self._glass_mode = True
            self.bg_photo = ImageTk.PhotoImage(glass_final)
            self.bg_canvas.delete("bg_img")
            self.bg_canvas.create_image(0, 0, image=self.bg_photo, anchor="nw", tags="bg_img")
            self.bg_canvas.tag_lower("bg_img")

            def hex_at(im, x, y):
                x = max(0, min(im.width - 1, x))
                y = max(0, min(im.height - 1, y))
                r, g, b = im.getpixel((x, y))[:3]
                return "#%02x%02x%02x" % (r, g, b)

            # Sample the backdrop image at each shell card's corner so the
            # corner squares blend into the artwork (no hard edge).
            hh = height - 44
            self._shell_corners = {
                "sidebar": (
                    hex_at(glass_final, 22, 22),
                    hex_at(glass_final, 254, 22),
                    hex_at(glass_final, 254, hh + 22),
                    hex_at(glass_final, 22, hh + 22),
                ),
                "content": (
                    hex_at(glass_final, 274, 22),
                    hex_at(glass_final, width - 22, 22),
                    hex_at(glass_final, width - 22, hh + 22),
                    hex_at(glass_final, 274, hh + 22),
                ),
            }
            self._apply_shell_corners()
        except Exception as exc:
            log(f"Background render failed: {exc}")

    def _on_window_resize(self, event):
        if event.widget == self:
            new_size = (event.width, event.height)
            if self._last_win_size != new_size:
                self._last_win_size = new_size
                if hasattr(self, "_bg_resize_job"):
                    try: self.after_cancel(self._bg_resize_job)
                    except Exception: pass
                self._bg_resize_job = self.after(150, self._load_background_image)

    def _browse_bg_image(self):
        path = filedialog.askopenfilename(
            title="Select Custom Background Image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp"), ("All Files", "*.*")]
        )
        if path:
            self.bg_entry.delete(0, "end")
            self.bg_entry.insert(0, path)
            self.settings["background"] = path
            save_json(SETTINGS_FILE, self.settings)
            self._glass_applied = False
            self._load_background_image()
            self._refresh_appearance()

    def _clear_bg_image(self):
        if hasattr(self, "bg_entry"):
            self.bg_entry.delete(0, "end")
        self.settings["background"] = ""
        save_json(SETTINGS_FILE, self.settings)
        self._glass_mode = False
        self._glass_applied = False
        self._shell_corners = {}
        self._sync_glass_mode()
        self._rebuild_ui()

    def show_about(self):
        panel = ctk.CTkFrame(
            self.content,
            fg_color="transparent",
            corner_radius=22,
            border_width=1,
            border_color=OUTLINE,
        )
        panel.pack(fill="both", expand=True, padx=10, pady=10)

        center = ctk.CTkFrame(panel, fg_color="transparent")
        center.pack(expand=True)

        ctk.CTkLabel(
            center, text="J", width=90, height=90, corner_radius=25,
            fg_color=self.accent, font=ctk.CTkFont(size=50, weight="bold")
        ).pack(pady=(0, 20))

        ctk.CTkLabel(center, text=APP_NAME, text_color=TEXT, font=ctk.CTkFont(size=28, weight="bold")).pack()
        ctk.CTkLabel(center, text=f"Version {APP_VERSION}", text_color=MUTED, font=ctk.CTkFont(size=14)).pack(pady=(5, 20))

        legal_frame = ctk.CTkFrame(center, fg_color=CARD_2, corner_radius=15, border_width=1, border_color=OUTLINE)
        legal_frame.pack(pady=20, fill="x")
        
        legal_text = (
            "LEGAL DISCLAIMER\n\n"
            "This is an unofficial application for Minecraft. This application is not affiliated\n"
            "in any way with Mojang AB or Microsoft Corporation.\n\n"
            "The Minecraft Name, the Minecraft Brand and the Minecraft Assets are all property\n"
            "of Mojang AB or their respectful owner. All rights reserved.\n"
            "In accordance with http://account.mojang.com/documents/brand_guidelines"
        )
        ctk.CTkLabel(legal_frame, text=legal_text, text_color=MUTED, font=ctk.CTkFont(size=12), justify="center").pack(padx=25, pady=25)

    def refresh_accounts(self):
        if not hasattr(self, "account_combo"):
            return

        if not self.accounts:
            self.account_combo.configure(values=["No accounts"])
            self.account_combo.set("No accounts")
            self._update_account_card(None)
            return

        names = [f"{acc.get('name', 'Unknown')} ({acc.get('type', 'offline').upper()})" for acc in self.accounts]
        self.account_combo.configure(values=names)

        if self.selected_account_index >= len(self.accounts):
            self.selected_account_index = 0

        self.account_combo.set(names[self.selected_account_index])
        self._update_account_card(self.accounts[self.selected_account_index])

    def _update_account_card(self, account: dict | None):
        if not account:
            self.avatar.configure(text="?", fg_color=CARD_2)
            self.account_name.configure(text="No Account", text_color=TEXT)
            self.account_type.configure(text="ADD AN ACCOUNT", text_color=MUTED)
            return

        name = account.get("name", "Unknown")
        atype = account.get("type", "offline").upper()

        self.account_name.configure(text=name, text_color=TEXT)
        self.account_type.configure(text=f"{atype} ACCOUNT", text_color=self.accent)
        self.avatar.configure(text=name[0].upper() if name else "?", fg_color=self.accent)

    def account_changed(self, choice: str):
        for i, acc in enumerate(self.accounts):
            formatted = f"{acc.get('name', 'Unknown')} ({acc.get('type', 'offline').upper()})"
            if formatted == choice:
                self.selected_account_index = i
                self._update_account_card(acc)
                break

    def add_account_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Add Account")
        dialog.geometry("350x250")
        dialog.transient(self)
        dialog.grab_set()

        frame = ctk.CTkFrame(dialog, fg_color=CARD)
        frame.pack(fill="both", expand=True)

        ctk.CTkLabel(frame, text="CHOOSE ACCOUNT TYPE", font=ctk.CTkFont(weight="bold")).pack(pady=(20, 15))

        ctk.CTkButton(
            frame,
            text="Microsoft Account",
            height=45,
            corner_radius=10,
            fg_color="#3FB950",
            hover_color="#2EA043",
            command=lambda: [dialog.destroy(), self._add_microsoft_account()],
        ).pack(fill="x", padx=40, pady=10)

        ctk.CTkButton(
            frame,
            text="Offline Account",
            height=45,
            corner_radius=10,
            fg_color=CARD_2,
            hover_color=OUTLINE,
            command=lambda: [dialog.destroy(), self._add_offline_account()],
        ).pack(fill="x", padx=40, pady=10)

    def _add_offline_account(self):
        dialog = ctk.CTkInputDialog(text="Enter Username:", title="Offline Account")
        name = dialog.get_input()
        if name:
            name = name.strip()
            if not name:
                return
            import uuid
            new_acc = {
                "name": name,
                "id": str(uuid.uuid4()).replace("-", ""),
                "token": "0",
                "type": "offline",
            }
            self.accounts.append(new_acc)
            self.selected_account_index = len(self.accounts) - 1
            save_json(ACCOUNTS_FILE, self.accounts)
            self.refresh_accounts()

    def _add_microsoft_account(self):
        if not microsoft_account:
            messagebox.showerror("Error", "minecraft-launcher-lib not fully loaded!")
            return

        if MICROSOFT_CLIENT_ID == "YOUR_MICROSOFT_CLIENT_ID":
            messagebox.showwarning("Config Warning", "Please configure MICROSOFT_CLIENT_ID in the script header.")
            return

        def login_worker():
            try:
                self.ui(self.set_status, "Awaiting Microsoft Login...")
                
                # Get the login data
                login_data = microsoft_account.get_login_url(
                    client_id=MICROSOFT_CLIENT_ID,
                    redirect_uri=MICROSOFT_REDIRECT_URI,
                )
                
                # Check if the library returned just a string (older versions) or multiple items (newer versions)
                if isinstance(login_data, str):
                    login_url = login_data
                    challenge = None
                else:
                    login_url = login_data[0]
                    challenge = login_data[2] if len(login_data) >= 3 else None
                
                webbrowser.open(login_url)

                import socket
                server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind(("127.0.0.1", 8765))
                server.listen(1)
                server.settimeout(60) 

                conn, _ = server.accept()
                request = conn.recv(1024).decode(errors="ignore")
                
                auth_code = None
                for line in request.split("\r\n"):
                    if line.startswith("GET"):
                        parts = line.split(" ")
                        if len(parts) > 1 and "?" in parts[1]:
                            query = parts[1].split("?")[1]
                            for param in query.split("&"):
                                if param.startswith("code="):
                                    auth_code = param.split("=")[1]
                                    break
                
                response = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: text/html; charset=utf-8\r\n"
                    "Connection: close\r\n\r\n"
                    "<h2>Login Successful!</h2><p>You can close this browser window and return to Janksy Launcher.</p>"
                )
                conn.sendall(response.encode("utf-8"))
                conn.close()
                server.close()

                if not auth_code:
                    raise Exception("No authorization code received.")

                self.ui(self.set_status, "Authenticating with Xbox Live...")
                
                if challenge:
                    account_info = microsoft_account.complete_login(
                        client_id=MICROSOFT_CLIENT_ID,
                        client_secret=None,  
                        redirect_uri=MICROSOFT_REDIRECT_URI,
                        auth_code=auth_code,
                        code_verifier=challenge,
                    )
                else:
                    account_info = microsoft_account.complete_login(
                        client_id=MICROSOFT_CLIENT_ID,
                        client_secret=None,  
                        redirect_uri=MICROSOFT_REDIRECT_URI,
                        auth_code=auth_code,
                    )

                new_acc = {
                    "name": account_info.get("name", "Player"),
                    "id": account_info.get("id", ""),
                    "token": account_info.get("access_token", ""),
                    "type": "msa",
                    "refresh_token": account_info.get("refresh_token", ""),
                    "expires_at": time.time() + 86000, 
                }

                self.accounts.append(new_acc)
                self.selected_account_index = len(self.accounts) - 1
                save_json(ACCOUNTS_FILE, self.accounts)
                self.ui(self.refresh_accounts)
                self.ui(self.set_status, f"Welcome, {new_acc['name']}!")

            except Exception as exc:
                log("Microsoft login error:", exc)
                self.ui(lambda: messagebox.showerror("Login Failed", str(exc)))
                self.ui(self.set_status, "Login cancelled or failed.")

        threading.Thread(target=login_worker, daemon=True).start()

    def edit_account(self):
        if not self.accounts:
            return
        
        acc = self.accounts[self.selected_account_index]
        if acc.get("type") != "offline":
            messagebox.showinfo("Info", "Only offline accounts can be renamed.")
            return

        dialog = ctk.CTkInputDialog(text="New Username:", title="Edit Account")
        name = dialog.get_input()
        if name and name.strip():
            acc["name"] = name.strip()
            save_json(ACCOUNTS_FILE, self.accounts)
            self.refresh_accounts()

    def delete_account(self):
        if not self.accounts:
            return
        
        if messagebox.askyesno("Confirm", "Delete selected account?"):
            del self.accounts[self.selected_account_index]
            if self.selected_account_index >= len(self.accounts):
                self.selected_account_index = max(0, len(self.accounts) - 1)
            save_json(ACCOUNTS_FILE, self.accounts)
            self.refresh_accounts()

    def browse_java(self):
        path = filedialog.askopenfilename(
            title="Select Java Executable",
            filetypes=[("Java", "java.exe" if sys.platform == "win32" else "java"), ("All", "*.*")]
        )
        if path:
            self.java_path = path
            self.settings["java_path"] = path
            save_json(SETTINGS_FILE, self.settings)
            if hasattr(self, "java_entry"):
                self.java_entry.delete(0, "end")
                self.java_entry.insert(0, path)

    def on_close(self):
        self.closing = True
        self.discord.shutdown()
        self.destroy()


if __name__ == "__main__":
    app = JanksyLauncher()
    app.mainloop()