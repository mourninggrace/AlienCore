"""
AlienCore - settings_gui.py
Clean rewrite. Loads config fresh from disk on open.
Hardware profile cached once at init. No blocking disk reads per tab.
"""

import json
import os
import copy
import math
import logging
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from core import config_manager as cfg
from core.constants import (
    APP_NAME, VERSION, HARDWARE_CACHE,
    COLOR_COOL, COLOR_WARM, COLOR_HOT,
    SUPPORT_EMAIL, GITHUB_ISSUES_URL,
)

logger = logging.getLogger("aliencore.gui")

# ── Themes ───────────────────────────────────────────────────────────────────
THEMES = {
    "Void": {
        "BG": "#1a1a1a", "BG_PANEL": "#222222", "BG_SECT": "#2a2a2a",
        "BG_HW": "#1e1e2e", "FG": "#e8e8e8", "FG_DIM": "#888888",
        "FG_HEAD": "#ffffff", "ACCENT": "#00aaff", "ACCENT2": "#00cc66",
        "WARN": "#ffaa00", "DANGER": "#ff4444",
        "BTN_BG": "#333333", "BTN_HOV": "#444444", "SEP": "#333333",
    },
    "Nebula": {
        "BG": "#130d1f", "BG_PANEL": "#1a1228", "BG_SECT": "#221830",
        "BG_HW": "#0f0a1a", "FG": "#e0d8f0", "FG_DIM": "#7a6a9a",
        "FG_HEAD": "#f0e8ff", "ACCENT": "#bb66ff", "ACCENT2": "#8844dd",
        "WARN": "#ff9933", "DANGER": "#ff3355",
        "BTN_BG": "#2a1f40", "BTN_HOV": "#3a2f55", "SEP": "#2a1f40",
    },
    "Ember": {
        "BG": "#1a0f0a", "BG_PANEL": "#221510", "BG_SECT": "#2a1c14",
        "BG_HW": "#160b06", "FG": "#f0e0d0", "FG_DIM": "#997766",
        "FG_HEAD": "#ffe8d0", "ACCENT": "#ff6600", "ACCENT2": "#cc4400",
        "WARN": "#ffcc00", "DANGER": "#ff2200",
        "BTN_BG": "#3a2010", "BTN_HOV": "#4a2e18", "SEP": "#3a2010",
    },
    "Aurora": {
        "BG": "#0a1a14", "BG_PANEL": "#101f18", "BG_SECT": "#162820",
        "BG_HW": "#081510", "FG": "#d0f0e0", "FG_DIM": "#5a9070",
        "FG_HEAD": "#e8fff4", "ACCENT": "#00ffaa", "ACCENT2": "#00cc88",
        "WARN": "#aaff00", "DANGER": "#ff4466",
        "BTN_BG": "#1a3020", "BTN_HOV": "#243a28", "SEP": "#1a3020",
    },
    "Spectre": {
        "BG": "#141414", "BG_PANEL": "#1c1c1c", "BG_SECT": "#242424",
        "BG_HW": "#111111", "FG": "#d0d0d0", "FG_DIM": "#707070",
        "FG_HEAD": "#f8f8f8", "ACCENT": "#c8c8c8", "ACCENT2": "#a0a0a0",
        "WARN": "#ffcc44", "DANGER": "#ff4444",
        "BTN_BG": "#2c2c2c", "BTN_HOV": "#3c3c3c", "SEP": "#2c2c2c",
    },
    "Crimson": {
        "BG": "#1a0a0a", "BG_PANEL": "#221010", "BG_SECT": "#2a1414",
        "BG_HW": "#150808", "FG": "#f0d8d8", "FG_DIM": "#997070",
        "FG_HEAD": "#ffe8e8", "ACCENT": "#ff3355", "ACCENT2": "#cc1133",
        "WARN": "#ff8800", "DANGER": "#ff0022",
        "BTN_BG": "#3a1515", "BTN_HOV": "#4a2020", "SEP": "#3a1515",
    },
    "Phantom": {
        "BG": "#0d0d14", "BG_PANEL": "#13131c", "BG_SECT": "#1a1a26",
        "BG_HW": "#0a0a10", "FG": "#ddd8f0", "FG_DIM": "#7070aa",
        "FG_HEAD": "#f0eeff", "ACCENT": "#ff44cc", "ACCENT2": "#cc22aa",
        "WARN": "#ffaa22", "DANGER": "#ff2244",
        "BTN_BG": "#1e1e30", "BTN_HOV": "#28283e", "SEP": "#1e1e30",
    },
    "Solaris": {
        "BG": "#0f0e14", "BG_PANEL": "#16151e", "BG_SECT": "#1e1c28",
        "BG_HW": "#0c0b12", "FG": "#f0e8c8", "FG_DIM": "#88806a",
        "FG_HEAD": "#fff8e0", "ACCENT": "#ffcc00", "ACCENT2": "#ddaa00",
        "WARN": "#ff8800", "DANGER": "#ff4422",
        "BTN_BG": "#2a2820", "BTN_HOV": "#38362c", "SEP": "#2a2820",
    },
    "Hex": {
        "BG": "#080e08", "BG_PANEL": "#0d140d", "BG_SECT": "#121c12",
        "BG_HW": "#060c06", "FG": "#80ff80", "FG_DIM": "#408040",
        "FG_HEAD": "#aaffaa", "ACCENT": "#00ff41", "ACCENT2": "#00cc33",
        "WARN": "#aaff00", "DANGER": "#ff2200",
        "BTN_BG": "#0f1f0f", "BTN_HOV": "#162816", "SEP": "#0f1f0f",
    },
    "Glacier": {
        "BG": "#0f1820", "BG_PANEL": "#162028", "BG_SECT": "#1e2c38",
        "BG_HW": "#0b1218", "FG": "#e8f4ff", "FG_DIM": "#88aadd",
        "FG_HEAD": "#f8fcff", "ACCENT": "#88ccff", "ACCENT2": "#aaddff",
        "WARN": "#ffd88c", "DANGER": "#ff6688",
        "BTN_BG": "#1a2838", "BTN_HOV": "#223445", "SEP": "#1a2838",
    },
    "Venom": {
        "BG": "#12110a", "BG_PANEL": "#1a180c", "BG_SECT": "#232114",
        "BG_HW": "#0e0d06", "FG": "#eaffaa", "FG_DIM": "#889955",
        "FG_HEAD": "#f8ffcc", "ACCENT": "#ccff00", "ACCENT2": "#99dd22",
        "WARN": "#ffaa00", "DANGER": "#ff4422",
        "BTN_BG": "#1f2010", "BTN_HOV": "#2c2d18", "SEP": "#1f2010",
    },
    "Abyss": {
        "BG": "#0a0820", "BG_PANEL": "#100e2a", "BG_SECT": "#181438",
        "BG_HW": "#07051a", "FG": "#c8c0f0", "FG_DIM": "#6658aa",
        "FG_HEAD": "#e8e0ff", "ACCENT": "#6644ff", "ACCENT2": "#4422cc",
        "WARN": "#ffaa44", "DANGER": "#ff3366",
        "BTN_BG": "#1a1438", "BTN_HOV": "#241a48", "SEP": "#1a1438",
    },
}


def _apply_theme(name: str):
    """Write theme colors into module globals before any widget is built."""
    global BG, BG_PANEL, BG_SECT, BG_HW, FG, FG_DIM, FG_HEAD
    global ACCENT, ACCENT2, WARN, DANGER, BTN_BG, BTN_HOV, SEP
    t = THEMES.get(name, THEMES["Void"])
    BG       = t["BG"]
    BG_PANEL = t["BG_PANEL"]
    BG_SECT  = t["BG_SECT"]
    BG_HW    = t["BG_HW"]
    FG       = t["FG"]
    FG_DIM   = t["FG_DIM"]
    FG_HEAD  = t["FG_HEAD"]
    ACCENT   = t["ACCENT"]
    ACCENT2  = t["ACCENT2"]
    WARN     = t["WARN"]
    DANGER   = t["DANGER"]
    BTN_BG   = t["BTN_BG"]
    BTN_HOV  = t["BTN_HOV"]
    SEP      = t["SEP"]


# ── Palette (default — overwritten by _apply_theme before window opens) ───────
BG       = "#1a1a1a"
BG_PANEL = "#222222"
BG_SECT  = "#2a2a2a"
BG_HW    = "#1e1e2e"
FG       = "#e8e8e8"
FG_DIM   = "#888888"
FG_HEAD  = "#ffffff"
ACCENT   = "#00aaff"
ACCENT2  = "#00cc66"
WARN     = "#ffaa00"
DANGER   = "#ff4444"
BTN_BG   = "#333333"
BTN_HOV  = "#444444"
SEP      = "#333333"


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def open_settings(on_save_callback=None, is_first_run=False):
    # Bootstrap — ensure sys.path includes aliencore root
    import sys, os, time, ctypes
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if base not in sys.path:
        sys.path.insert(0, base)

    # Single-instance mutex — exit silently if settings is already open
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, True, "AlienCore_Settings_v1")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.kernel32.CloseHandle(_mutex)
        return

    # Small delay to ensure any in-flight disk writes have completed
    time.sleep(0.2)

    # Always load config fresh from disk
    cfg.load()
    _apply_theme(cfg.get_value("display", "settings_theme", default="Venom"))

    root = tk.Tk()
    SettingsWindow(root, on_save_callback=on_save_callback,
                   is_first_run=is_first_run)
    root.mainloop()

    ctypes.windll.kernel32.CloseHandle(_mutex)


# Also handle being run directly as a subprocess
if __name__ == "__main__":
    open_settings()


# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────

class SettingsWindow:
    def __init__(self, root, on_save_callback=None, is_first_run=False):
        self.root             = root
        self.on_save_callback = on_save_callback
        self.is_first_run     = is_first_run
        cfg.load()                    # ensure fresh from disk
        self.config           = cfg.get()
        self.vars             = {}
        self._text_widgets    = {}
        self._hw              = self._load_hw()

        self._setup_window()
        self._build_ui()
        # Snapshot clean state for dirty detection
        self._saved_state = {k: v.get() for k, v in self.vars.items()}
        self._saved_theme = self._cfg_get("display.settings_theme") or "Void"

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _load_hw(self) -> dict:
        try:
            if os.path.exists(HARDWARE_CACHE):
                with open(HARDWARE_CACHE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _setup_window(self):
        self.root.title(f"{APP_NAME} Settings  v{VERSION}")
        self.root.configure(bg=BG)
        from gui.tray import set_window_icon
        set_window_icon(self.root)
        self.root.resizable(True, True)
        self.root.minsize(1280, 600)
        self.root.geometry("1360x840")
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x  = max(0, (sw - 1360) // 2)
        y  = max(0, (sh - 840) // 2)
        self.root.geometry(f"1360x840+{x}+{y}")
        self.root.lift()
        self.root.focus_force()
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))
        # Force the window to the foreground via Win32 — needed when launched
        # from a subprocess because Windows focus-stealing prevention blocks
        # the normal lift()/focus_force() path.
        def _force_foreground():
            try:
                import ctypes as _ct
                hwnd = _ct.windll.user32.GetParent(self.root.winfo_id())
                if not hwnd:
                    hwnd = int(self.root.winfo_id())
                _ct.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
        self.root.after(50, _force_foreground)
        self._configure_styles()

    def _configure_styles(self):
        """Apply TTK styles using current theme globals. Safe to call on rebuild."""
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure("TNotebook",     background=BG,       borderwidth=0)
        s.configure("TNotebook.Tab", background=BG_PANEL, foreground=FG_DIM,
                    padding=[12, 7], font=("Segoe UI", 9))
        s.map("TNotebook.Tab",
              background=[("selected", BG_SECT)],
              foreground=[("selected", FG_HEAD)])
        s.configure("TFrame",    background=BG)
        s.configure("TCombobox", fieldbackground=BG_PANEL, background=BTN_BG,
                    foreground=FG, selectbackground=ACCENT, selectforeground=BG,
                    arrowcolor=FG_DIM, insertcolor=FG)
        s.map("TCombobox",
              fieldbackground=[("readonly", BG_PANEL)],
              foreground=[("readonly", FG)],
              selectbackground=[("readonly", ACCENT)],
              selectforeground=[("readonly", BG)])
        s.configure("Vertical.TScrollbar", background=BG_PANEL,
                    troughcolor=BG, borderwidth=0, arrowcolor=FG_DIM)

    # ── Theme rebuild ─────────────────────────────────────────────────────────

    def _rebuild_for_theme(self):
        """Destroy all widgets and rebuild with new theme globals in-place."""
        # Save in-flight var values and tab position
        self._collect()
        try:
            active_tab = self.nb.index(self.nb.select())
        except Exception:
            active_tab = 0
        saved_state = dict(self._saved_state)
        saved_theme = self._saved_theme

        # Tear down
        for w in self.root.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass
        self.vars          = {}
        self._text_widgets = {}

        # Rebuild
        self.root.configure(bg=BG)
        self._configure_styles()
        self._build_ui()

        # Restore tab and clean-state snapshot
        try:
            self.nb.select(active_tab)
        except Exception:
            pass
        self._saved_state = saved_state
        self._saved_theme = saved_theme
        self._mark_dirty()

    # ── UI builder ────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self.root, bg=BG)
        hdr.pack(fill="x")
        tk.Label(hdr, text=f"  {APP_NAME}", font=("Segoe UI", 18, "bold"),
                 bg=BG, fg=ACCENT).pack(side="left", pady=12, padx=8)
        tk.Label(hdr, text=f"v{VERSION}", font=("Segoe UI", 10),
                 bg=BG, fg=FG_DIM).pack(side="left", pady=12)
        if self.is_first_run:
            tk.Label(hdr, text="  First-run setup",
                     font=("Segoe UI", 10, "italic"),
                     bg=BG, fg=WARN).pack(side="left", padx=16)
        tk.Frame(self.root, bg=SEP, height=1).pack(fill="x")

        # Notebook
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True)

        # Tabs are built lazily — the window opens fast, then each tab is
        # built on first selection.  Remaining tabs prewarm in idle time
        # so switching feels instant.
        self._tab_defs = [
            ("Display",         self._tab_display),
            ("CPU",             self._tab_cpu),
            ("GPU",             self._tab_gpu),
            ("RAM",             self._tab_ram),
            ("Visual",          self._tab_visual),
            ("Network",         self._tab_network),
            ("Storage",         self._tab_storage),
            ("Privacy",         self._tab_privacy),
            ("Profiles",        self._tab_profiles),
            ("Custom Profiles", self._tab_custom_profiles),
            ("Service",         self._tab_service),
            ("Thresholds",      self._tab_thresholds),
            ("AI",              self._tab_ai),
            ("Insights",        self._tab_insights),
            ("Drivers",         self._tab_drivers),
            ("About",           self._tab_about),
            ("Account",         self._tab_account),
        ]
        self._tab_built  = [False] * len(self._tab_defs)
        self._tab_frames = []
        for label, _ in self._tab_defs:
            ph = ttk.Frame(self.nb)
            self.nb.add(ph, text=f"  {label}  ")
            self._tab_frames.append(ph)

        # Build the first tab eagerly so the user has content immediately
        self._build_tab(0)

        # Build any other tab on first selection (idempotent)
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # Prewarm the rest in the background so switches feel instant
        self.root.after(80, lambda: self._prewarm_tabs(1))

        # Footer
        tk.Frame(self.root, bg=SEP, height=1).pack(fill="x")
        foot = tk.Frame(self.root, bg=BG, pady=8)
        foot.pack(fill="x", padx=16)
        tk.Label(foot, text="Changes take effect immediately.",
                 font=("Segoe UI", 9), bg=BG, fg=FG_DIM).pack(side="left")
        self._btn(foot, "User Manual", self._open_manual, ACCENT2).pack(side="left", padx=(12, 0))
        self._cancel_btn  = self._btn(foot, "Cancel",           self._cancel,   FG_DIM)
        self._cancel_btn.pack(side="right", padx=4)
        self._btn(foot, "Restore Defaults", self._defaults, WARN).pack(side="right", padx=4)
        self._save_btn = self._btn(foot, "  Close  ", self._close, FG_DIM, bold=True)
        self._save_btn.pack(side="right", padx=4)

        # Update-available button — shown on the left when an update is ready.
        # Checked immediately (in case the check ran before settings opened) and
        # again after 35 s (covers the 30 s startup delay before first check).
        self._update_foot_btn = None
        self._update_foot_frame = tk.Frame(foot, bg=BG)
        self._update_foot_frame.pack(side="left", padx=(14, 0))
        self.root.after(500,   self._refresh_update_button)
        self.root.after(35000, self._refresh_update_button)

    # ── Tabs ──────────────────────────────────────────────────────────────────

    def _tab_display(self):
        t = self._make_tab("Display")
        self._section(t, "Temperature unit")
        tu = self._var("display.temp_unit", str)
        tuf = tk.Frame(t, bg=BG_SECT); tuf.pack(fill="x", padx=20, pady=(0, 6))
        for val, lbl in [("celsius", "Celsius  (°C)"), ("fahrenheit", "Fahrenheit  (°F)")]:
            tk.Radiobutton(tuf, text=lbl, variable=tu, value=val,
                           bg=BG_SECT, fg=FG, selectcolor=BG_SECT,
                           activebackground=BG_SECT, activeforeground=ACCENT,
                           font=("Segoe UI", 9)).pack(side="left", padx=10)

        self._section(t, "Sensor update interval")
        self._row_label(t, "Update unit")
        uv = self._var("display.update_interval_unit", str)
        rf = tk.Frame(t, bg=BG_SECT); rf.pack(fill="x", padx=20, pady=(0,6))
        for val, lbl in [("seconds","Seconds"),("milliseconds","Milliseconds")]:
            tk.Radiobutton(rf, text=lbl, variable=uv, value=val,
                           bg=BG_SECT, fg=FG, selectcolor=BG_SECT,
                           activebackground=BG_SECT, activeforeground=ACCENT,
                           font=("Segoe UI",9)).pack(side="left", padx=10)
        self._slider(t, "display.update_interval_value",
                     "Interval value", 0.1, 60.0, 0.1,
                     fmt=lambda v: f"{v:.1f}",
                     note="Seconds: 0.5–60  |  Milliseconds: 100–60000")

        self._section(t, "Sensors to display")
        self._note(t, "Double-click any value in the bar to see a 60-sample history sparkline.")
        self._opt(t, "sensors.cpu_temp",    "CPU temperature",              "Average or per-core")
        self._opt(t, "sensors.gpu_temp",    "GPU core temperature",         "Via LHM")
        self._opt(t, "sensors.gpu_hotspot", "GPU hot spot temperature",     "Junction temp — runs ~15–20° hotter than core")
        self._opt(t, "sensors.gpu_mem_temp","GPU VRAM junction temperature","Via LHM")
        self._opt(t, "sensors.nvme_temp",   "Primary NVMe / SSD temp",      "Via LibreHardwareMonitor")
        self._opt(t, "sensors.nvme_temp2",  "Second NVMe / SSD temp",       "Auto-hidden if no second drive is installed")
        self._opt(t, "sensors.fan_rpm",     "Fan RPM / DIMM temperature",   "Chassis fan RPMs via AWCC, or RAM stick temps as fallback")
        self._opt(t, "sensors.ram_usage",   "RAM usage %",                  "Live memory pressure")
        self._opt(t, "sensors.cpu_load",    "CPU load %",                   "Overall utilization")
        self._opt(t, "sensors.gpu_load",    "GPU load %",                   "Via nvidia-smi")
        self._opt(t, "sensors.gpu_vram",    "GPU VRAM usage",               "Via nvidia-smi")
        self._opt(t, "sensors.gpu_fan",     "GPU fan %",                    "Via nvidia-smi — may read 0 on AWCC-controlled laptops")
        self._opt(t, "sensors.cpu_freq",    "CPU clock (GHz)",              "Current boost frequency via psutil")
        self._opt(t, "sensors.gpu_clock",   "GPU core clock (GHz)",         "Via nvidia-smi")
        self._opt(t, "sensors.cpu_watts",   "CPU power draw (W)",           "Via LibreHardwareMonitor")
        self._opt(t, "sensors.gpu_watts",   "GPU power draw (W)",           "Via nvidia-smi")
        self._opt(t, "sensors.battery",     "Battery %",                    "Charge level and charging state")
        self._opt(t, "sensors.net_io",      "Network throughput",           "Upload ↑ and download ↓ speeds")
        self._opt(t, "sensors.disk_io",     "Disk throughput (MB/s)",       "Read and write speeds")

        self._row_label(t, "Network units")
        nu = self._var("display.net_unit", str)
        nrow = tk.Frame(t, bg=BG_SECT); nrow.pack(fill="x", padx=20, pady=(0,8))
        for val, lbl in [("MB/s", "MB/s"), ("Mbps", "Mbps"), ("kbps", "kbps")]:
            tk.Radiobutton(nrow, text=lbl, variable=nu, value=val,
                           bg=BG_SECT, fg=FG, selectcolor=BG_SECT,
                           activebackground=BG_SECT, activeforeground=ACCENT,
                           font=("Segoe UI",9)).pack(side="left", padx=10)

        self._row_label(t, "CPU temperature mode")
        mv = self._var("sensors.cpu_temp_mode", str)
        rm = tk.Frame(t, bg=BG_SECT); rm.pack(fill="x", padx=20, pady=(0,8))
        for val, lbl in [("average","Average"),("per_core","Per-core")]:
            tk.Radiobutton(rm, text=lbl, variable=mv, value=val,
                           bg=BG_SECT, fg=FG, selectcolor=BG_SECT,
                           activebackground=BG_SECT, activeforeground=ACCENT,
                           font=("Segoe UI",9)).pack(side="left", padx=10)

        self._section(t, "Settings theme")
        self._note(t, "Color palette for this settings window. Takes effect immediately.")
        theme_row = tk.Frame(t, bg=BG_SECT, pady=4)
        theme_row.pack(fill="x", padx=20, pady=(0, 8))

        current_theme = self._cfg_get("display.settings_theme") or "Void"
        theme_var = tk.StringVar(value=current_theme)

        swatch = tk.Canvas(theme_row, width=40, height=20, highlightthickness=1,
                           highlightbackground="#444444", cursor="arrow")

        def _update_swatch(name):
            td = THEMES.get(name, THEMES["Void"])
            swatch.configure(bg=td["BG"])
            swatch.delete("all")
            swatch.create_rectangle(0,  0, 20, 20, fill=td["ACCENT"],  outline="")
            swatch.create_rectangle(20, 0, 40, 20, fill=td["ACCENT2"], outline="")

        def _on_theme_change(event=None):
            name = theme_var.get()
            self._cfg_set("display.settings_theme", name)
            _apply_theme(name)
            self._rebuild_for_theme()

        combo = ttk.Combobox(theme_row, textvariable=theme_var,
                             values=list(THEMES.keys()),
                             state="readonly", width=14,
                             font=("Segoe UI", 10))
        combo.pack(side="left", padx=(0, 8))
        combo.bind("<<ComboboxSelected>>", _on_theme_change)
        swatch.pack(side="left", padx=(0, 10))
        _update_swatch(current_theme)
        tk.Label(theme_row, text="Takes effect immediately",
                 font=("Segoe UI", 8, "italic"),
                 bg=BG_SECT, fg=FG_DIM).pack(side="left")

        self._section(t, "Overlay")
        self._opt(t, "display.auto_hide_fullscreen", "Auto-hide bar in fullscreen", "Withdraws sensor bar when a fullscreen app is active")
        self._opt(t, "display.overlay_enabled", "Show floating overlay", "Always-on-top overlay window")
        self._row_label(t, "Overlay position")
        pv = self._var("display.overlay_position", str)
        rp = tk.Frame(t, bg=BG_SECT); rp.pack(fill="x", padx=20, pady=(0,8))
        for val in ["bottom_right","bottom_left","top_right","top_left"]:
            tk.Radiobutton(rp, text=val.replace("_"," "), variable=pv, value=val,
                           bg=BG_SECT, fg=FG, selectcolor=BG_SECT,
                           activebackground=BG_SECT, activeforeground=ACCENT,
                           font=("Segoe UI",9)).pack(side="left", padx=10)
        self._slider(t, "display.overlay_opacity", "Overlay opacity", 0.3, 1.0, 0.05,
                     fmt=lambda v: f"{int(v*100)}%")

    def _tab_cpu(self):
        t = self._make_tab("CPU")
        self._hw_panel(t, "cpu")
        self._section(t, "CPU Clock & Power Management")
        self._opt(t, "cpu.enabled",           "Enable CPU management",            "Master toggle")
        self._opt(t, "cpu.dynamic_throttle",  "Dynamic throttle at idle",         "Adjusts ceiling based on temp + load")
        self._slider(t, "cpu.idle_max_state_pct", "Idle CPU ceiling", 10, 100, 5,
                     fmt=lambda v: f"{int(v)}%")
        self._slider(t, "cpu.throttle_temp_trigger", "Throttle temp trigger (°C)", 50, 95, 1,
                     fmt=lambda v: f"{int(v)}°C")
        self._opt(t, "cpu.full_power_in_gaming",    "Full power during gaming",    "Removes ceiling when game detected")
        self._opt(t, "cpu.full_power_in_streaming", "Full power during streaming", "Removes ceiling when OBS/XSplit detected")
        self._gate(t, "cpu_tvb_optimizer",    self._cpu_tvb_panel)
        self._gate(t, "cpu_boost_score",      self._cpu_boost_score_panel)
        self._gate(t, "cpu_topology",         self._cpu_core_role_panel)
        self._gate(t, "cpu_interrupt_steering", self._cpu_interrupt_steering_panel)

    def _tab_gpu(self):
        t = self._make_tab("GPU")
        self._hw_panel(t, "gpu")
        self._section(t, "GPU Power & Fan Management")
        self._opt(t, "gpu.enabled",          "Enable GPU management",           "Master toggle")
        self._opt(t, "gpu.optimal_decision", "Optimal decisions (recommended)", "AlienCore calculates limits from TDP")
        self._opt(t, "gpu.fan_curve_enabled","Apply custom fan curve",          "Via nvidia-smi")
        self._slider(t, "gpu.power_limit_idle_pct",      "Power limit at idle (%)",      30, 100, 5, fmt=lambda v: f"{int(v)}%")
        self._slider(t, "gpu.power_limit_gaming_pct",    "Power limit gaming (%)",       50, 100, 5, fmt=lambda v: f"{int(v)}%")
        self._slider(t, "gpu.power_limit_streaming_pct", "Power limit streaming (%)",    50, 100, 5, fmt=lambda v: f"{int(v)}%")
        if self._hw.get("platform", {}).get("has_awcc", True):
            self._section(t, "Turbo Cool")
            self._note(t, "Spins fans to 100% for rapid cooling via AWCC. Auto-disables when timer OR temps cool — whichever first.")
            self._slider(t, "turbo_cool.auto_off_minutes", "Auto-disable after (minutes)", 1, 60, 1, fmt=lambda v: f"{int(v)} min")
            self._opt(t, "turbo_cool.auto_off_on_cool", "Also disable when temps drop below warning thresholds", "")
        self._gate(t, "gpu_dynamic_boost",    self._gpu_dynamic_boost_panel)
        self._gate(t, "gpu_vram_clock_lock",  self._gpu_vram_clock_panel)
        self._gate(t, "gpu_driver_features",  self._gpu_driver_features_panel)
        self._gate(t, "gpu_throttle_log",     self._gpu_throttle_log_panel)
        self._gate(t, "gpu_efficiency_curve", self._gpu_efficiency_panel)

    def _tab_ram(self):
        t = self._make_tab("RAM")
        self._hw_panel(t, "ram")
        self._section(t, "Memory Management")
        self._opt(t, "ram.enabled",                   "Enable RAM management",         "Master toggle")
        self._opt(t, "ram.disable_superfetch",         "Disable SysMain (Superfetch)",  "Recommended on NVMe systems")
        self._opt(t, "ram.pagefile_managed",           "Let Windows manage pagefile",   "Recommended")
        self._slider(t, "ram.pagefile_custom_mb", "Custom pagefile (MB)", 0, 32768, 512,
                     fmt=lambda v: "Windows managed" if int(v)==0 else f"{int(v)} MB")
        self._opt(t, "ram.clear_standby_cache_on_idle","Clear standby cache at idle",  "Frees unused RAM")
        self._gate(t, "ram_composition",       self._ram_composition_panel)
        self._gate(t, "ram_unified_pressure",  self._ram_unified_pressure_panel)
        self._gate(t, "ram_working_set_trimmer", self._ram_working_set_panel)
        self._gate(t, "ram_leak_watchdog",     self._ram_leak_watchdog_panel)
        self._gate(t, "ram_dimm_protection",   self._ram_dimm_protection_panel)
        self._gate(t, "ram_pagefile_advisor",  self._ram_pagefile_advisor_panel)

    # ─────────────────────────────────────────────────────────────────────────
    # CPU advanced panels
    # ─────────────────────────────────────────────────────────────────────────

    def _cpu_tvb_panel(self, parent):
        from core.constants import TVB_TEMP_THRESHOLD
        self._section(parent, "TVB Headroom & Thermal Velocity Boost Optimizer")
        self._note(parent,
            f"Intel Thermal Velocity Boost (TVB) activates when CPU temp is below "
            f"{TVB_TEMP_THRESHOLD}°C, adding +200 MHz on top of normal boost. "
            f"The optimizer gently lowers the idle ceiling when temps creep toward the threshold, "
            f"keeping you in TVB territory for higher sustained single-core speeds.")
        self._opt(parent, "cpu.tvb_optimizer",
                  "Enable TVB optimizer",
                  f"Automatically adjusts idle ceiling to maintain temp < {TVB_TEMP_THRESHOLD}°C")

        panel = tk.Frame(parent, bg=BG_HW, padx=16, pady=10)
        panel.pack(fill="x", padx=16, pady=(4, 8))
        tk.Label(panel, text="Live TVB Status", font=("Segoe UI", 9, "bold"),
                 bg=BG_HW, fg=FG_HEAD).pack(anchor="w", pady=(0, 4))

        tvb_lbl  = tk.Label(panel, text="—", font=("Segoe UI", 9), bg=BG_HW, fg=FG_DIM)
        tvb_lbl.pack(anchor="w")
        head_lbl = tk.Label(panel, text="—", font=("Segoe UI", 9), bg=BG_HW, fg=FG_DIM)
        head_lbl.pack(anchor="w")

        def _refresh():
            try:
                from core import sensors, boost_tracker
                r     = sensors.get_readings()
                temp  = r.get("cpu_temp_avg")
                score = boost_tracker.get_score()
                if temp is not None:
                    headroom = TVB_TEMP_THRESHOLD - temp
                    if headroom > 0:
                        tvb_lbl.config(
                            text=f"TVB ACTIVE  —  CPU temp {temp:.0f}°C  ({headroom:.1f}°C below threshold)",
                            fg=ACCENT2)
                    else:
                        tvb_lbl.config(
                            text=f"TVB INACTIVE  —  CPU temp {temp:.0f}°C  ({-headroom:.1f}°C above threshold)",
                            fg=WARN)
                    head_lbl.config(
                        text=f"Boost sustainability: {score['score_pct']:.0f}%  |  "
                             f"Avg clock: {score['avg_freq_ghz']:.2f} GHz  |  "
                             f"Max: {score['max_freq_mhz']:,} MHz",
                        fg=FG_DIM)
            except Exception:
                pass
            if panel.winfo_exists():
                panel.after(2500, _refresh)

        panel.after(500, _refresh)

    def _cpu_boost_score_panel(self, parent):
        self._section(parent, "Boost Clock Sustainability Score")

        _MODE_LABELS = {
            "frequency":    "Frequency (power-driven — % of time at ≥90% max clock)",
            "thermal":      "Thermal (% of time below TVB threshold — cooler = more boost)",
            "core_parking": "Core parking (avg share of cores actively working — scheduler)",
        }
        _MODE_NOTES = {
            "frequency":
                "Measures how often the CPU actually reaches ≥90% of its max boost clock "
                "over the last 60 s. High = sustaining boost. Low = thermal or power "
                "throttling is cutting performance short.",
            "thermal":
                "Measures how often the CPU stays below the TVB temperature threshold "
                "over the last 60 s. High = plenty of thermal headroom. Low = thermals "
                "are the bottleneck limiting sustained boost.",
            "core_parking":
                "Measures the average share of cores doing real work (load > 2 %) over "
                "the last 60 s. High = Windows is using the full CPU. Low = aggressive "
                "parking is holding cores back from boost.",
        }

        # ── Mode selector ─────────────────────────────────────────────────────
        current = cfg.get_value("cpu", "boost_score_mode", default="frequency")
        if current not in _MODE_LABELS:
            current = "frequency"

        selector = tk.Frame(parent, bg=BG, padx=16)
        selector.pack(fill="x", padx=16, pady=(0, 4))
        tk.Label(selector, text="Score formula:", font=("Segoe UI", 9, "bold"),
                 bg=BG, fg=FG_DIM).pack(side="left")

        mode_var = tk.StringVar(value=_MODE_LABELS[current])
        note_lbl = tk.Label(parent, text=_MODE_NOTES[current],
                            font=("Segoe UI", 8), bg=BG, fg=FG_DIM,
                            justify="left", wraplength=760, anchor="w")

        def _on_mode_change(val):
            slug = next((k for k, v in _MODE_LABELS.items() if v == val), "frequency")
            cfg.set_value("cpu", "boost_score_mode", value=slug)
            note_lbl.config(text=_MODE_NOTES[slug])
            # Trigger immediate refresh so the user sees the new formula's value
            try:
                _refresh()
            except Exception:
                pass

        dd = tk.OptionMenu(selector, mode_var, *_MODE_LABELS.values(),
                           command=_on_mode_change)
        dd.config(bg=BG_PANEL, fg=FG, activebackground=BTN_HOV, activeforeground=FG,
                  font=("Segoe UI", 8), relief="flat", width=62, anchor="w")
        dd["menu"].config(bg=BG_PANEL, fg=FG, font=("Segoe UI", 8))
        dd.pack(side="left", padx=(8, 0))

        note_lbl.pack(anchor="w", padx=16, pady=(0, 6), fill="x")

        # ── Live score display ────────────────────────────────────────────────
        panel = tk.Frame(parent, bg=BG_HW, padx=16, pady=10)
        panel.pack(fill="x", padx=16, pady=(4, 8))

        score_lbl = tk.Label(panel, text="—", font=("Segoe UI Semibold", 18),
                             bg=BG_HW, fg=ACCENT)
        score_lbl.pack(anchor="w")
        detail_lbl = tk.Label(panel, text="", font=("Segoe UI", 8),
                              bg=BG_HW, fg=FG_DIM)
        detail_lbl.pack(anchor="w")

        bar_canvas = tk.Canvas(panel, width=400, height=14, bg=BG_PANEL,
                               highlightthickness=0, bd=0)
        bar_canvas.pack(anchor="w", pady=(4, 0))

        def _refresh():
            try:
                from core import boost_tracker
                s = boost_tracker.get_score()
                pct   = s["score_pct"]
                mode  = s.get("score_mode", "frequency")
                color = (ACCENT2 if pct >= 70 else WARN if pct >= 40 else DANGER)
                # Score label reflects the mode so the user can tell at a glance
                # which formula produced the number.
                label_map = {
                    "frequency":    "Boost Score",
                    "thermal":      "Thermal Score",
                    "core_parking": "Core Usage Score",
                }
                score_lbl.config(
                    text=f"{pct:.0f}%  {label_map.get(mode, 'Boost Score')}",
                    fg=color)
                detail_lbl.config(
                    text=f"Avg {s['avg_freq_ghz']:.2f} GHz over last {s['window_seconds']}s  "
                         f"({s['sample_count']} samples)")
                bar_canvas.delete("all")
                fill_w = int(400 * pct / 100)
                bar_canvas.create_rectangle(0, 0, 400, 14, fill=BG_PANEL, outline="")
                if fill_w > 0:
                    bar_canvas.create_rectangle(0, 0, fill_w, 14, fill=color, outline="")
            except Exception:
                pass
            if panel.winfo_exists():
                panel.after(2500, _refresh)

        panel.after(800, _refresh)

    def _cpu_core_role_panel(self, parent):
        _is_intel = self._hw.get("cpu", {}).get("is_intel", True)
        if _is_intel:
            self._section(parent, "P-core / E-core Topology")
            self._note(parent,
                "Detected P-cores handle high-priority foreground threads. E-cores handle "
                "background work via EcoQoS. Intel Thread Director automatically routes threads — "
                "the settings below tune how aggressively Windows defers to the hardware scheduler.")
        else:
            self._section(parent, "CPU Topology & Core Management")
            self._note(parent,
                "AMD Ryzen has no E-core concept — all cores are full-performance. "
                "Precision Boost handles frequency scaling automatically. "
                "Core parking and processor state controls below still apply.")

        panel = tk.Frame(parent, bg=BG_HW, padx=16, pady=10)
        panel.pack(fill="x", padx=16, pady=(4, 8))
        tk.Label(panel, text="Detected Topology", font=("Segoe UI", 9, "bold"),
                 bg=BG_HW, fg=FG_HEAD).pack(anchor="w", pady=(0, 4))

        topo_lbl = tk.Label(panel, text="Detecting...", font=("Segoe UI", 9),
                            bg=BG_HW, fg=FG_DIM)
        topo_lbl.pack(anchor="w")

        def _load_topo():
            try:
                from core import cpu_topology, hardware as hw_mod
                t   = cpu_topology.get_topology()
                cpu = hw_mod.get_cached().get("cpu", {})
                confirmed = "confirmed" if t["detected"] else "estimated"
                if cpu.get("is_amd"):
                    ccds = t.get("ccds", [])
                    if ccds:
                        ccd_parts = "  |  ".join(
                            f"CCD{c['ccd_id']}: {c['core_count']} cores" for c in ccds
                        )
                        txt = (f"AMD Ryzen — {t['p_count']} logical processors  "
                               f"|  {len(ccds)} CCD(s): {ccd_parts}  ({confirmed})")
                    else:
                        txt = (f"AMD Ryzen — {t['p_count']} logical processors  ({confirmed})")
                else:
                    if t["p_cores"]:
                        p_range = f"LPs {t['p_cores'][0]}–{t['p_cores'][-1]}"
                    else:
                        p_range = "none detected"
                    txt = (f"P-cores: {t['p_count']} logical processors  "
                           f"({p_range})  "
                           f"|  E-cores: {t['e_count']} logical processors  "
                           f"({confirmed})")
                if panel.winfo_exists():
                    panel.after(0, lambda: topo_lbl.config(text=txt, fg=ACCENT))
            except Exception as e:
                if panel.winfo_exists():
                    panel.after(0, lambda: topo_lbl.config(text=f"Detection failed: {e}", fg=WARN))

        import threading as _t
        _t.Thread(target=_load_topo, daemon=True).start()

        _cpu_is_intel = self._hw.get("cpu", {}).get("is_intel", True)
        self._opt(parent, "cpu.hetero_scheduling",
                  "Intel Thread Director (heterogeneous scheduling)" if _cpu_is_intel
                  else "Heterogeneous scheduling (Intel-only, no effect on AMD)",
                  "Lets Intel's microcontroller guide thread placement across P/E-cores"
                  if _cpu_is_intel else
                  "No-op on AMD Ryzen — AMD's boost algorithm handles thread placement automatically")
        self._opt(parent, "cpu.core_parking_gaming",
                  "Unpark all cores during gaming/streaming",
                  "Eliminates wakeup latency — all 32 logical processors always ready")

    def _cpu_interrupt_steering_panel(self, parent):
        self._section(parent, "Interrupt Affinity Steering")
        _is_intel = self._hw.get("cpu", {}).get("is_intel", True)
        if _is_intel:
            self._note(parent,
                "Routes hardware interrupt requests (IRQs) from your NIC, NVMe, and GPU "
                "toward P-cores instead of E-cores. This reduces DPC interrupt latency by "
                "ensuring your fastest cores service device events. Requires admin. "
                "A reboot is recommended after applying.")
        else:
            self._note(parent,
                "Routes hardware interrupt requests (IRQs) from your NIC, NVMe, and GPU "
                "toward performance cores. On AMD Ryzen, all cores are performance cores — "
                "the affinity mask covers all logical processors equally. Requires admin. "
                "A reboot is recommended after applying.")
        self._opt(parent, "cpu.interrupt_steering",
                  "Enable interrupt steering (stored in config)",
                  "Apply/revert buttons below take effect immediately")

        ctrl = tk.Frame(parent, bg=BG_SECT, pady=4)
        ctrl.pack(fill="x", padx=20, pady=(0, 4))
        status_var = tk.StringVar(value="")
        status_lbl = tk.Label(parent, textvariable=status_var,
                              font=("Segoe UI", 8, "italic"),
                              bg=BG_SECT, fg=FG_DIM, anchor="w")
        status_lbl.pack(fill="x", padx=20, pady=(0, 8))

        def _apply():
            status_var.set("Applying interrupt steering...")
            def _work():
                from core import tweaks
                ok, msg = tweaks.apply_interrupt_steering(dry_run=False)
                parent.after(0, lambda: status_var.set(
                    msg if ok else f"Failed: {msg}"))
            threading.Thread(target=_work, daemon=True).start()

        def _revert():
            status_var.set("Reverting interrupt steering...")
            def _work():
                from core import tweaks
                ok, msg = tweaks.reset_interrupt_steering(dry_run=False)
                parent.after(0, lambda: status_var.set(
                    msg if ok else f"Failed: {msg}"))
            threading.Thread(target=_work, daemon=True).start()

        _is_intel2 = self._hw.get("cpu", {}).get("is_intel", True)
        _apply_lbl = "Apply to P-cores" if _is_intel2 else "Apply to all cores"
        self._btn(ctrl, _apply_lbl, _apply, ACCENT2, bold=True).pack(side="left", padx=(0, 8))
        self._btn(ctrl, "Revert to Windows Auto", _revert, FG_DIM).pack(side="left")

    # ─────────────────────────────────────────────────────────────────────────
    # GPU advanced panels
    # ─────────────────────────────────────────────────────────────────────────

    def _gpu_dynamic_boost_panel(self, parent):
        self._section(parent, "Dynamic Boost Transparency")
        self._note(parent,
            "Live view of GPU boost state — current power draw vs. limit, "
            "clock vs. max, and whether any throttle reasons are active.")

        panel = tk.Frame(parent, bg=BG_HW, padx=16, pady=10)
        panel.pack(fill="x", padx=16, pady=(4, 8))

        boost_lbl  = tk.Label(panel, text="—", font=("Segoe UI Semibold", 11),
                              bg=BG_HW, fg=ACCENT)
        boost_lbl.pack(anchor="w")
        detail_lbl = tk.Label(panel, text="", font=("Segoe UI", 8),
                              bg=BG_HW, fg=FG_DIM, wraplength=700, justify="left")
        detail_lbl.pack(anchor="w", pady=(2, 0))

        def _refresh():
            try:
                from core import sensors, throttle_log
                r = sensors.get_readings()
                watts      = r.get("gpu_watts")
                limit      = r.get("gpu_power_limit_w")
                clock      = r.get("gpu_clock_mhz")
                mem_clock  = r.get("gpu_mem_clock_mhz")
                throttle   = r.get("gpu_throttle_reasons", "0x0000000000000000")
                reasons    = throttle_log.decode_reasons(throttle)
                perf_lost  = throttle_log.is_perf_limited(throttle)

                if clock is not None or watts is not None:
                    watts_str = ""
                    if watts is not None:
                        pct_str   = f" ({watts/limit*100:.0f}% of limit)" if limit else ""
                        watts_str = f"Power: {watts:.0f}W{pct_str}  |  "
                        over90    = watts and limit and watts / limit > 0.9
                    else:
                        over90 = False
                    boost_lbl.config(
                        text=f"{watts_str}"
                             f"Core: {int(clock or 0):,} MHz  |  "
                             f"VRAM: {int(mem_clock or 0):,} MHz",
                        fg=DANGER if perf_lost else ACCENT2 if over90 else ACCENT)
                    detail_lbl.config(
                        text=("Throttle: " + ", ".join(reasons)) if reasons else "Throttle: none",
                        fg=DANGER if perf_lost else FG_DIM)
                else:
                    boost_lbl.config(text="GPU data not available — check sensor service and GPU drivers", fg=FG_DIM)
            except Exception:
                pass
            if panel.winfo_exists():
                panel.after(2000, _refresh)

        panel.after(300, _refresh)

    def _gpu_vram_clock_panel(self, parent):
        self._section(parent, "VRAM Idle Clock Reduction")
        _has_nvidia = any(g.get("is_nvidia") for g in self._hw.get("gpu", []))
        if not _has_nvidia:
            self._note(parent,
                "Not available — VRAM idle clock lock requires an NVIDIA GPU. "
                "No NVIDIA GPU was detected on this system.")
            return
        self._note(parent,
            "Locks VRAM to a low idle clock state when not gaming. "
            "Reduces memory controller heat and saves 10-15W at idle. "
            "AlienCore automatically releases the lock when switching to gaming profile. "
            "Requires nvidia-smi and admin rights.")
        self._opt(parent, "gpu.vram_idle_clock_lock",
                  "Enable VRAM idle clock lock",
                  "Locks memory clock at idle — auto-released during gaming")
        self._slider(parent, "gpu.vram_idle_clock_mhz", "Idle VRAM clock (MHz)",
                     200, 1000, 5, fmt=lambda v: f"{int(v)} MHz",
                     note="405 MHz = NVIDIA P8 idle state (recommended for most GPUs)")

        ctrl = tk.Frame(parent, bg=BG_SECT, pady=4)
        ctrl.pack(fill="x", padx=20, pady=(0, 4))
        vram_status = tk.StringVar(value="")
        tk.Label(parent, textvariable=vram_status,
                 font=("Segoe UI", 8, "italic"), bg=BG_SECT, fg=FG_DIM,
                 anchor="w").pack(fill="x", padx=20, pady=(0, 8))

        def _lock_now():
            mhz = int(self._cfg_get("gpu.vram_idle_clock_mhz") or 405)
            vram_status.set(f"Locking VRAM to {mhz} MHz...")
            def _work():
                from core import tweaks
                ok, msg = tweaks.set_vram_clock_lock(True, mhz)
                parent.after(0, lambda: vram_status.set(msg))
            threading.Thread(target=_work, daemon=True).start()

        def _release_now():
            vram_status.set("Releasing VRAM clock lock...")
            def _work():
                from core import tweaks
                ok, msg = tweaks.set_vram_clock_lock(False)
                parent.after(0, lambda: vram_status.set(msg))
            threading.Thread(target=_work, daemon=True).start()

        self._btn(ctrl, "Lock Now", _lock_now, ACCENT2, bold=True).pack(side="left", padx=(0, 8))
        self._btn(ctrl, "Release Lock", _release_now, FG_DIM).pack(side="left")

    def _gpu_driver_features_panel(self, parent):
        self._section(parent, "NVIDIA Driver Feature Panel")
        self._note(parent,
            "Key driver features managed by AlienCore. Changes apply on next "
            "AlienCore startup or when you click Apply below.")
        self._opt(parent, "gpu.hags_enabled",
                  "Hardware-Accelerated GPU Scheduling (HAGS)",
                  "Required for DLSS 3 Frame Generation. Reduces input latency. Stored in registry.")
        self._opt(parent, "gpu.powermizer_max_performance",
                  "PowerMizer: Prefer Maximum Performance",
                  "Forces GPU to max P-state on AC power — eliminates stutters from clock ramp-up.")

        panel = tk.Frame(parent, bg=BG_HW, padx=16, pady=10)
        panel.pack(fill="x", padx=16, pady=(4, 8))
        feat_status = tk.StringVar(value="")
        feat_lbl    = tk.Label(panel, textvariable=feat_status,
                               font=("Segoe UI", 8, "italic"),
                               bg=BG_HW, fg=FG_DIM, anchor="w", wraplength=720)
        feat_lbl.pack(anchor="w")

        def _read_status():
            try:
                import winreg
                hags = None
                try:
                    k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                       r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers")
                    hags, _ = winreg.QueryValueEx(k, "HwSchMode")
                    winreg.CloseKey(k)
                except Exception:
                    pass
                hags_str = ("Enabled" if hags == 2 else "Disabled" if hags == 1 else "Unknown")
                parent.after(0, lambda: (
                    feat_status.set(f"HAGS: {hags_str}"),
                    feat_lbl.config(fg=ACCENT2 if hags == 2 else WARN)))
            except Exception:
                pass

        threading.Thread(target=_read_status, daemon=True).start()

        ctrl = tk.Frame(parent, bg=BG_HW)
        ctrl.pack(anchor="w", pady=(4, 0))
        apply_status = tk.StringVar(value="")
        tk.Label(parent, textvariable=apply_status,
                 font=("Segoe UI", 8, "italic"), bg=BG_HW, fg=FG_DIM,
                 anchor="w").pack(fill="x", padx=16, pady=(0, 8))

        def _apply_feat():
            apply_status.set("Applying NVIDIA driver features...")
            def _work():
                from core import tweaks
                tweaks._tweak_gpu_baseline(self.config, self._hw, dry_run=False)
                parent.after(0, lambda: (apply_status.set("Applied. A reboot may be needed for HAGS."),
                                         _read_status()))
            threading.Thread(target=_work, daemon=True).start()

        self._btn(ctrl, "Apply Now", _apply_feat, ACCENT, bold=True).pack(side="left")

    def _gpu_throttle_log_panel(self, parent):
        self._section(parent, "Thermal Throttle Event Log")
        self._note(parent,
            "Records every time the GPU was performance-limited by thermals, power cap, "
            "or HW slowdown. Logged continuously while AlienCore is running.")

        outer = tk.Frame(parent, bg=BG_PANEL)
        outer.pack(fill="x", padx=16, pady=(4, 4))

        hdr = tk.Frame(outer, bg=BG_PANEL, pady=3, padx=10)
        hdr.pack(fill="x")
        for col, w in [("Time", 14), ("Reason(s)", 52), ("Temp", 8), ("Watts", 8)]:
            tk.Label(hdr, text=col, font=("Segoe UI", 8, "bold"),
                     bg=BG_PANEL, fg=FG_DIM, width=w, anchor="w").pack(side="left")

        rows_frame = tk.Frame(outer, bg=BG_SECT)
        rows_frame.pack(fill="x")

        ctrl = tk.Frame(parent, bg=BG_SECT, pady=4)
        ctrl.pack(fill="x", padx=16, pady=(0, 8))
        log_status = tk.StringVar(value="")
        tk.Label(parent, textvariable=log_status,
                 font=("Segoe UI", 8, "italic"), bg=BG_SECT, fg=FG_DIM,
                 anchor="w").pack(fill="x", padx=16, pady=(0, 4))

        def _load_log():
            for w in rows_frame.winfo_children():
                w.destroy()
            try:
                from core import throttle_log
                from datetime import datetime
                events = throttle_log.get_recent(20)
                if not events:
                    tk.Label(rows_frame, text="No throttle events recorded this session.",
                             font=("Segoe UI", 8, "italic"), bg=BG_SECT,
                             fg=FG_DIM, padx=10, pady=6).pack(anchor="w")
                    log_status.set("")
                    return
                for ev in reversed(events):
                    row = tk.Frame(rows_frame, bg=BG_SECT, padx=10, pady=2)
                    row.pack(fill="x")
                    ts  = datetime.fromtimestamp(ev["timestamp"]).strftime("%H:%M:%S")
                    reasons_str = "; ".join(ev.get("reasons", []))[:60]
                    temp_str    = f"{ev['gpu_temp']:.0f}°C" if ev.get("gpu_temp") else "—"
                    watts_str   = f"{ev['gpu_watts']:.0f}W" if ev.get("gpu_watts") else "—"
                    col_color   = DANGER if ev.get("perf_limited") else WARN
                    for val, w in [(ts, 14), (reasons_str, 52), (temp_str, 8), (watts_str, 8)]:
                        tk.Label(row, text=val, font=("Consolas", 8),
                                 bg=BG_SECT, fg=col_color, width=w, anchor="w"
                                 ).pack(side="left")
                log_status.set(f"{len(events)} event(s) this session.")
            except Exception as e:
                log_status.set(f"Error: {e}")

        def _clear_log():
            from core import throttle_log
            throttle_log.clear()
            _load_log()

        self._btn(ctrl, "Refresh", _load_log, ACCENT).pack(side="left", padx=(0, 8))
        self._btn(ctrl, "Clear Log", _clear_log, FG_DIM).pack(side="left")
        _load_log()

    def _gpu_efficiency_panel(self, parent):
        self._section(parent, "Power vs. Performance Efficiency")
        self._note(parent,
            "Efficiency score = GPU load% ÷ power draw (W). Higher is better. "
            "A score that drops while load stays the same means you're spending more "
            "watts for the same work — often caused by a climbing thermal limit.")

        panel = tk.Frame(parent, bg=BG_HW, padx=16, pady=10)
        panel.pack(fill="x", padx=16, pady=(4, 8))

        eff_lbl    = tk.Label(panel, text="—", font=("Segoe UI Semibold", 14),
                              bg=BG_HW, fg=ACCENT)
        eff_lbl.pack(anchor="w")
        eff_detail = tk.Label(panel, text="", font=("Segoe UI", 8),
                              bg=BG_HW, fg=FG_DIM)
        eff_detail.pack(anchor="w")

        bar_c = tk.Canvas(panel, width=400, height=12, bg=BG_PANEL,
                          highlightthickness=0, bd=0)
        bar_c.pack(anchor="w", pady=(4, 0))

        # Rolling efficiency average
        _eff_samples = []

        def _refresh():
            try:
                from core import sensors
                r     = sensors.get_readings()
                load  = r.get("gpu_load")
                watts = r.get("gpu_watts")
                if load is not None and watts and watts > 0:
                    score = round(load / watts, 2)
                    _eff_samples.append(score)
                    if len(_eff_samples) > 30:
                        _eff_samples.pop(0)
                    avg_score = sum(_eff_samples) / len(_eff_samples)
                    color = (ACCENT2 if avg_score >= 1.5 else
                             ACCENT  if avg_score >= 0.8 else
                             WARN    if avg_score >= 0.4 else DANGER)
                    eff_lbl.config(
                        text=f"{avg_score:.2f}  %load/W  (30-sample avg)",
                        fg=color)
                    eff_detail.config(
                        text=f"Current: {load:.0f}% @ {watts:.0f}W  →  {score:.2f} %/W  "
                             f"|  Peak: {max(_eff_samples):.2f}")
                    norm = min(1.0, avg_score / 2.0)
                    bar_c.delete("all")
                    bar_c.create_rectangle(0, 0, 400, 12, fill=BG_PANEL, outline="")
                    fill_w = int(400 * norm)
                    if fill_w:
                        bar_c.create_rectangle(0, 0, fill_w, 12, fill=color, outline="")
                else:
                    eff_lbl.config(text="Waiting for GPU data...", fg=FG_DIM)
            except Exception:
                pass
            if panel.winfo_exists():
                panel.after(2000, _refresh)

        panel.after(500, _refresh)

    # ─────────────────────────────────────────────────────────────────────────
    # RAM advanced panels
    # ─────────────────────────────────────────────────────────────────────────

    def _ram_composition_panel(self, parent):
        self._section(parent, "Memory Composition Breakdown")
        self._note(parent,
            "In Use = committed to processes/kernel.  "
            "Modified = dirty pages queued to disk.  "
            "Standby = cached pages (reclaimable instantly).  "
            "Free = zeroed, immediately available.")

        panel = tk.Frame(parent, bg=BG_HW, padx=16, pady=10)
        panel.pack(fill="x", padx=16, pady=(4, 8))

        bar_c  = tk.Canvas(panel, width=500, height=18, bg=BG_PANEL,
                           highlightthickness=0, bd=0)
        bar_c.pack(anchor="w", pady=(0, 4))
        detail_lbl = tk.Label(panel, text="—", font=("Segoe UI", 8),
                              bg=BG_HW, fg=FG_DIM)
        detail_lbl.pack(anchor="w")

        _COLORS = {"in_use": "#e05555", "modified": "#e09020",
                   "standby": "#4488cc", "free": "#33aa66"}

        def _refresh():
            import threading as _t
            def _worker():
                comp = None
                try:
                    from core import wmi_memory
                    comp = wmi_memory.get_composition()
                except Exception:
                    pass
                def _apply():
                    if not panel.winfo_exists():
                        return
                    if comp:
                        total = comp["total_gb"] or 1
                        segments = [
                            ("in_use",   comp["in_use_gb"],  comp["in_use_pct"]),
                            ("modified", comp["modified_gb"], 0),
                            ("standby",  comp["standby_gb"],  comp["standby_pct"]),
                            ("free",     comp["free_gb"],      comp["free_pct"]),
                        ]
                        bar_c.delete("all")
                        x = 0
                        for key, gb, pct in segments:
                            w = int(500 * gb / total)
                            if w > 0:
                                bar_c.create_rectangle(x, 0, x + w, 18,
                                                       fill=_COLORS[key], outline="")
                            x += w
                        mod_str = (f"{comp['modified_gb']:.1f} GB modified"
                                   if comp["modified_gb"] > 0.1 else "")
                        detail_lbl.config(
                            text=f"In Use: {comp['in_use_gb']:.1f} GB ({comp['in_use_pct']:.0f}%)  "
                                 f"Standby: {comp['standby_gb']:.1f} GB  "
                                 f"Free: {comp['free_gb']:.1f} GB  "
                                 + (f"  {mod_str}" if mod_str else ""),
                            fg=FG_DIM)
                    panel.after(3000, _refresh)
                panel.after(0, _apply)
            _t.Thread(target=_worker, daemon=True).start()

        panel.after(400, _refresh)

    def _ram_unified_pressure_panel(self, parent):
        self._section(parent, "Unified Memory Pressure  (RAM + VRAM)")
        self._note(parent,
            "Combined view of system RAM and GPU VRAM pressure. "
            "When both are high simultaneously, expect stutters and asset streaming delays.")

        panel = tk.Frame(parent, bg=BG_HW, padx=16, pady=10)
        panel.pack(fill="x", padx=16, pady=(4, 8))

        ram_row  = tk.Frame(panel, bg=BG_HW); ram_row.pack(fill="x", pady=2)
        vram_row = tk.Frame(panel, bg=BG_HW); vram_row.pack(fill="x", pady=2)

        tk.Label(ram_row,  text="System RAM :", font=("Segoe UI", 8), bg=BG_HW,
                 fg=FG_DIM, width=12, anchor="w").pack(side="left")
        ram_bar  = tk.Canvas(ram_row,  width=300, height=12, bg=BG_PANEL,
                             highlightthickness=0, bd=0)
        ram_bar.pack(side="left", padx=(0, 8))
        ram_lbl  = tk.Label(ram_row,  text="—", font=("Segoe UI", 8),
                            bg=BG_HW, fg=FG_DIM)
        ram_lbl.pack(side="left")

        tk.Label(vram_row, text="GPU VRAM    :", font=("Segoe UI", 8), bg=BG_HW,
                 fg=FG_DIM, width=12, anchor="w").pack(side="left")
        vram_bar = tk.Canvas(vram_row, width=300, height=12, bg=BG_PANEL,
                             highlightthickness=0, bd=0)
        vram_bar.pack(side="left", padx=(0, 8))
        vram_lbl = tk.Label(vram_row, text="—", font=("Segoe UI", 8),
                            bg=BG_HW, fg=FG_DIM)
        vram_lbl.pack(side="left")

        def _bar(canvas, pct):
            color = (DANGER if pct >= 90 else WARN if pct >= 70 else ACCENT2)
            canvas.delete("all")
            w = int(300 * pct / 100)
            if w:
                canvas.create_rectangle(0, 0, w, 12, fill=color, outline="")

        def _refresh():
            import threading as _t, subprocess as _sp, psutil as _ps
            def _worker():
                ram_pct = ram_used = ram_total = None
                vram_used_mb = vram_total_mb = None
                try:
                    vm        = _ps.virtual_memory()
                    ram_pct   = vm.percent
                    ram_used  = round(vm.used  / 1024**3, 1)
                    ram_total = round(vm.total / 1024**3, 1)
                except Exception:
                    pass
                try:
                    p = _sp.run(
                        ["nvidia-smi",
                         "--query-gpu=memory.used,memory.total",
                         "--format=csv,noheader,nounits"],
                        capture_output=True, text=True, timeout=5,
                        creationflags=_sp.CREATE_NO_WINDOW,
                    )
                    if p.returncode == 0:
                        parts = [x.strip() for x in p.stdout.strip().split(",")]
                        if len(parts) >= 2:
                            vram_used_mb  = float(parts[0])
                            vram_total_mb = float(parts[1]) or 1.0
                except Exception:
                    pass
                def _apply():
                    if not panel.winfo_exists():
                        return
                    if ram_pct is not None:
                        _bar(ram_bar, ram_pct)
                        ram_lbl.config(
                            text=f"{ram_pct:.0f}%  ({ram_used:.1f} / {ram_total:.0f} GB)")
                    if vram_used_mb is not None and vram_total_mb:
                        vram_pct = round(vram_used_mb / vram_total_mb * 100, 1)
                        _bar(vram_bar, vram_pct)
                        vram_lbl.config(
                            text=f"{vram_pct:.0f}%  "
                                 f"({vram_used_mb/1024:.1f} / {vram_total_mb/1024:.1f} GB)")
                    panel.after(2000, _refresh)
                if panel.winfo_exists():
                    panel.after(0, _apply)
            _t.Thread(target=_worker, daemon=True).start()

        panel.after(300, _refresh)

    def _ram_working_set_panel(self, parent):
        self._section(parent, "Process Working Set Trimmer")
        self._note(parent,
            "Forces Windows to release idle process working sets back to standby, "
            "making them immediately reclaimable. Useful before starting a game to "
            "reclaim RAM from background apps without killing them.")

        outer = tk.Frame(parent, bg=BG_PANEL)
        outer.pack(fill="x", padx=16, pady=(4, 4))

        hdr = tk.Frame(outer, bg=BG_PANEL, pady=3, padx=10)
        hdr.pack(fill="x")
        for col, w in [("Process", 28), ("RSS (MB)", 12), ("% of RAM", 10)]:
            tk.Label(hdr, text=col, font=("Segoe UI", 8, "bold"),
                     bg=BG_PANEL, fg=FG_DIM, width=w, anchor="w").pack(side="left")

        rows_f = tk.Frame(outer, bg=BG_SECT)
        rows_f.pack(fill="x")

        trim_status = tk.StringVar(value="")
        ctrl = tk.Frame(parent, bg=BG_SECT, pady=4)
        ctrl.pack(fill="x", padx=16, pady=(0, 4))
        tk.Label(parent, textvariable=trim_status,
                 font=("Segoe UI", 8, "italic"), bg=BG_SECT, fg=FG_DIM,
                 anchor="w").pack(fill="x", padx=16, pady=(0, 8))

        def _load_procs():
            for w in rows_f.winfo_children():
                w.destroy()
            try:
                from core import wmi_memory
                procs = wmi_memory.get_top_processes(15)
                for proc in procs:
                    row = tk.Frame(rows_f, bg=BG_SECT, padx=10, pady=1)
                    row.pack(fill="x")
                    for val, w in [(proc["name"][:30], 28),
                                   (f"{proc['rss_mb']:.0f}", 12),
                                   (f"{proc['rss_pct']:.1f}%", 10)]:
                        tk.Label(row, text=val, font=("Segoe UI", 8),
                                 bg=BG_SECT, fg=FG, width=w, anchor="w"
                                 ).pack(side="left")
                trim_status.set(f"Top {len(procs)} processes by RAM usage.")
            except Exception as e:
                trim_status.set(f"Error: {e}")

        def _trim():
            trim_status.set("Trimming working sets...")
            def _work():
                from core import wmi_memory
                result = wmi_memory.trim_working_sets()
                msg = (f"Trimmed {result['trimmed']} process(es). "
                       f"Est. {result['freed_mb_estimate']:.0f} MB freed to standby.")
                parent.after(0, lambda: (trim_status.set(msg), _load_procs()))
            threading.Thread(target=_work, daemon=True).start()

        self._btn(ctrl, "Refresh List", _load_procs, ACCENT).pack(side="left", padx=(0, 8))
        self._btn(ctrl, "Trim All Working Sets", _trim, ACCENT2, bold=True).pack(side="left")
        _load_procs()

    def _ram_leak_watchdog_panel(self, parent):
        self._section(parent, "Memory Leak Watchdog")
        self._note(parent,
            "Monitors per-process RSS growth over time. If any process grows faster "
            "than the threshold rate sustained over the observation window, it appears "
            "in the suspects list below. Useful for catching runaway browser tabs, "
            "game memory leaks, and driver-level leaks.")
        self._opt(parent, "ram.leak_watchdog_enabled",
                  "Enable memory leak watchdog",
                  "Runs in the monitor loop — negligible overhead")
        self._slider(parent, "ram.leak_threshold_mb_per_min",
                     "Flag if growing faster than (MB/min)", 5, 500, 5,
                     fmt=lambda v: f"{int(v)} MB/min")
        self._slider(parent, "ram.leak_window_minutes",
                     "Observation window (minutes)", 1, 30, 1,
                     fmt=lambda v: f"{int(v)} min")

        panel = tk.Frame(parent, bg=BG_HW, padx=16, pady=8)
        panel.pack(fill="x", padx=16, pady=(4, 8))
        tk.Label(panel, text="Suspects", font=("Segoe UI", 9, "bold"),
                 bg=BG_HW, fg=FG_HEAD).pack(anchor="w", pady=(0, 4))

        suspects_lbl = tk.Label(panel, text="No suspects detected.",
                                font=("Segoe UI", 8, "italic"),
                                bg=BG_HW, fg=FG_DIM, anchor="w", wraplength=700)
        suspects_lbl.pack(anchor="w")

        def _refresh():
            try:
                from core import ram_watchdog
                suspects = ram_watchdog.get_suspects()
                if not suspects:
                    suspects_lbl.config(text="No suspects detected.", fg=FG_DIM)
                else:
                    lines = [f"  {s['name']} (PID {s['pid']})  —  "
                             f"+{s['growth_mb_per_min']:.0f} MB/min  |  "
                             f"current: {s['current_mb']:.0f} MB"
                             for s in suspects]
                    suspects_lbl.config(text="\n".join(lines), fg=DANGER)
            except Exception:
                pass
            if panel.winfo_exists():
                panel.after(5000, _refresh)

        panel.after(1000, _refresh)

    def _ram_dimm_protection_panel(self, parent):
        self._section(parent, "DIMM Thermal Throttle Protection")
        self._note(parent,
            "Reads DIMM temperatures via LibreHardwareMonitor. If any DIMM exceeds "
            "the threshold, AlienCore reduces the CPU ceiling to relieve memory controller "
            "heat. Requires AlienCore to run as Administrator and Memory Integrity disabled.")
        self._opt(parent, "ram.dimm_throttle_protection",
                  "Enable DIMM thermal protection",
                  "Reduces CPU ceiling when DIMM temps exceed threshold")
        self._slider(parent, "ram.dimm_throttle_temp_c",
                     "DIMM temp alert threshold (°C)", 40, 75, 1,
                     fmt=lambda v: f"{int(v)}°C")

        panel = tk.Frame(parent, bg=BG_HW, padx=16, pady=10)
        panel.pack(fill="x", padx=16, pady=(4, 8))

        hdr = tk.Frame(panel, bg=BG_HW)
        hdr.pack(fill="x", pady=(0, 6))
        tk.Label(hdr, text="Live DIMM Temperatures",
                 font=("Segoe UI", 9, "bold"),
                 bg=BG_HW, fg=FG_HEAD).pack(side="left")
        legend = tk.Label(hdr, text="cool · warn · alert",
                          font=("Segoe UI", 7), bg=BG_HW, fg=FG_DIM)
        legend.pack(side="right")

        rows_frame = tk.Frame(panel, bg=BG_HW)
        rows_frame.pack(fill="x", anchor="w")

        status_lbl = tk.Label(panel, text="Reading sensors…",
                              font=("Segoe UI", 8),
                              bg=BG_HW, fg=FG_DIM, anchor="w")
        status_lbl.pack(anchor="w", pady=(4, 0))

        BAR_W = 360
        BAR_H = 22

        state = {
            "rows":       {},     # name -> dict(canvas, val_lbl, target, current, phase, history)
            "scale_min":  30,
            "scale_max":  80,
            "anim_after": None,
        }

        def _hex_to_rgb(c):
            return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)

        def _shade(c, factor):
            try:
                r, g, b = _hex_to_rgb(c)
                f = max(0.0, min(1.0, factor))
                return f"#{int(r*f):02x}{int(g*f):02x}{int(b*f):02x}"
            except Exception:
                return c

        def _color_for_temp(t, threshold):
            if t >= threshold:        return DANGER
            if t >= threshold - 4:    return WARN
            if t >= threshold - 12:   return ACCENT2
            return ACCENT

        def _ensure_row(name):
            if name in state["rows"]:
                return state["rows"][name]
            row = tk.Frame(rows_frame, bg=BG_HW)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=name, font=("Consolas", 9, "bold"),
                     bg=BG_HW, fg=FG, width=10, anchor="w").pack(side="left")
            canvas = tk.Canvas(row, width=BAR_W, height=BAR_H,
                               bg=BG_HW, highlightthickness=0, bd=0)
            canvas.pack(side="left", padx=(8, 10))
            val_lbl = tk.Label(row, text="—",
                               font=("Consolas", 11, "bold"),
                               bg=BG_HW, fg=FG_DIM, width=6, anchor="w")
            val_lbl.pack(side="left")
            rec = {
                "canvas":  canvas, "val_lbl": val_lbl,
                "target":  None,   "current": None,
                "phase":   0.0,    "history": [],
            }
            state["rows"][name] = rec
            return rec

        def _draw_row(rec, threshold):
            canvas = rec["canvas"]
            canvas.delete("all")
            smin = state["scale_min"]
            smax = state["scale_max"]
            span = max(smax - smin, 1)

            # Track background
            canvas.create_rectangle(0, 6, BAR_W, BAR_H - 4,
                                    fill=_shade(BG_PANEL, 0.85), outline="")

            # Subtle gridline ticks every ~10°C
            tick_temp = int(smin + 10 - (smin % 10)) if smin % 10 else smin + 10
            while tick_temp < smax:
                gx = int((tick_temp - smin) / span * BAR_W)
                canvas.create_line(gx, 7, gx, BAR_H - 5,
                                   fill=_shade(BG_PANEL, 0.55))
                tick_temp += 10

            # Threshold line
            tx = int((threshold - smin) / span * BAR_W)
            canvas.create_line(tx, 2, tx, BAR_H - 2,
                               fill=WARN, width=1, dash=(2, 3))

            # Faint history trail under the bar
            hist = rec["history"]
            if len(hist) >= 2:
                pts = []
                for i, v in enumerate(hist):
                    px = int(i * BAR_W / max(len(hist) - 1, 1))
                    py = BAR_H - 4 - int((v - smin) / span * (BAR_H - 10))
                    py = max(6, min(BAR_H - 4, py))
                    pts.extend((px, py))
                canvas.create_line(*pts, fill=_shade(FG_DIM, 0.6),
                                   width=1, smooth=True)

            cur = rec["current"]
            if cur is None:
                return

            # Filled bar with two-tone gradient feel
            fx = max(2, int((cur - smin) / span * BAR_W))
            fx = min(BAR_W, fx)
            col = _color_for_temp(cur, threshold)
            dark = _shade(col, 0.35)
            canvas.create_rectangle(0, 6, fx, BAR_H - 4,
                                    fill=dark, outline="")
            canvas.create_rectangle(0, 6, fx, (BAR_H + 2) // 2,
                                    fill=col, outline="", stipple="gray50")
            # Leading edge accent line
            canvas.create_line(fx, 5, fx, BAR_H - 3, fill=col, width=2)

            # Pulsing dot at the current value
            ph = rec["phase"]
            radius = 3.2 + 1.6 * (0.5 + 0.5 * math.sin(ph))
            cy = (BAR_H + 2) / 2
            canvas.create_oval(fx - radius, cy - radius,
                               fx + radius, cy + radius,
                               fill=col, outline="")
            # Soft halo
            halo = radius + 2
            canvas.create_oval(fx - halo, cy - halo,
                               fx + halo, cy + halo,
                               outline=_shade(col, 0.5))

            rec["val_lbl"].config(text=f"{cur:.0f}°C", fg=col)

        def _animate():
            if not panel.winfo_exists():
                state["anim_after"] = None
                return
            try:
                threshold = self._cfg_get("ram.dimm_throttle_temp_c") or 52
                for rec in state["rows"].values():
                    target = rec["target"]
                    if target is None:
                        continue
                    cur = rec["current"]
                    if cur is None:
                        rec["current"] = float(target)
                    else:
                        # Ease toward target so changes glide rather than snap
                        rec["current"] = cur + (target - cur) * 0.18
                    rec["phase"] = (rec["phase"] + 0.16) % (2 * math.pi)
                    _draw_row(rec, threshold)
            except Exception:
                pass
            state["anim_after"] = panel.after(60, _animate)

        def _refresh():
            if not panel.winfo_exists():
                return
            try:
                from core import sensors
                r = sensors.get_readings()
                ram_temps = r.get("ram_temps", [])
                if not ram_temps:
                    status_lbl.config(
                        text="No DIMM temperature data (LHM bridge required).",
                        fg=FG_DIM)
                    if not status_lbl.winfo_ismapped():
                        status_lbl.pack(anchor="w", pady=(4, 0))
                    for rec in state["rows"].values():
                        rec["target"] = None
                else:
                    if status_lbl.winfo_ismapped():
                        status_lbl.pack_forget()
                    threshold = self._cfg_get("ram.dimm_throttle_temp_c") or 52
                    hot = max(d["temp_c"] for d in ram_temps)
                    state["scale_min"] = 30
                    state["scale_max"] = max(80, int(hot) + 10,
                                             int(threshold) + 10)
                    for d in ram_temps:
                        rec = _ensure_row(d["name"])
                        rec["target"] = float(d["temp_c"])
                        rec["history"].append(float(d["temp_c"]))
                        if len(rec["history"]) > 60:
                            rec["history"].pop(0)
            except Exception:
                pass
            if panel.winfo_exists():
                panel.after(2000, _refresh)

        panel.after(300, _refresh)
        state["anim_after"] = panel.after(80, _animate)

    def _ram_pagefile_advisor_panel(self, parent):
        self._section(parent, "Smart Pagefile Advisor")
        self._note(parent,
            "Analyzes your RAM, usage patterns, and drive type to recommend "
            "an optimal pagefile configuration.")

        panel = tk.Frame(parent, bg=BG_HW, padx=16, pady=10)
        panel.pack(fill="x", padx=16, pady=(4, 8))

        advice_lbl = tk.Label(panel, text="Click Analyze to get a recommendation.",
                              font=("Segoe UI", 9), bg=BG_HW, fg=FG_DIM,
                              wraplength=720, justify="left")
        advice_lbl.pack(anchor="w", pady=(0, 6))

        rec_lbl = tk.Label(panel, text="", font=("Segoe UI Semibold", 10),
                           bg=BG_HW, fg=ACCENT)
        rec_lbl.pack(anchor="w")

        apply_status = tk.StringVar(value="")
        tk.Label(parent, textvariable=apply_status,
                 font=("Segoe UI", 8, "italic"), bg=BG_HW, fg=FG_DIM,
                 anchor="w").pack(fill="x", padx=16, pady=(0, 4))

        _last_advice = [None]

        ctrl = tk.Frame(parent, bg=BG_HW)
        ctrl.pack(anchor="w", padx=16, pady=(0, 8))

        def _analyze():
            advice_lbl.config(text="Analyzing...", fg=FG_DIM)
            rec_lbl.config(text="")
            def _work():
                from core import pagefile_advisor
                adv = pagefile_advisor.get_advice(self._hw)
                _last_advice[0] = adv
                size_str = (f"Custom: {adv['size_mb'] // 1024:.0f} GB ({adv['size_mb']:,} MB)"
                            if adv["recommendation"] == "custom" and adv["size_mb"]
                            else "Windows-managed")
                panel.after(0, lambda: (
                    advice_lbl.config(text=adv["reason"], fg=FG),
                    rec_lbl.config(
                        text=f"Recommendation: {size_str}  |  "
                             f"Current: {adv['current_mb'] or 'managed'}  |  "
                             f"RAM: {adv['total_ram_gb']:.0f} GB  NVMe: {'Yes' if adv['has_nvme'] else 'No'}",
                        fg=ACCENT)))
            threading.Thread(target=_work, daemon=True).start()

        def _apply_advice():
            if not _last_advice[0]:
                return
            apply_status.set("Applying pagefile recommendation...")
            def _work():
                from core import pagefile_advisor
                ok, msg = pagefile_advisor.apply_recommendation(_last_advice[0])
                parent.after(0, lambda: apply_status.set(
                    msg if ok else f"Failed: {msg}"))
            threading.Thread(target=_work, daemon=True).start()

        self._btn(ctrl, "Analyze", _analyze, ACCENT, bold=True).pack(side="left", padx=(0, 8))
        self._btn(ctrl, "Apply Recommendation", _apply_advice, ACCENT2).pack(side="left")

    def _tab_visual(self):
        t = self._make_tab("Visual")
        self._section(t, "UI Snappiness & Visual Effects")
        self._opt(t, "visual.enabled",              "Enable visual effects management", "Master toggle")
        self._opt(t, "visual.optimal_decision",     "Optimal decisions",                "Recommended balanced set")
        self._opt(t, "visual.disable_animations",   "Disable window animations",        "UI feels instant")
        self._opt(t, "visual.disable_transparency", "Disable transparency effects",     "Frees GPU overhead")
        self._opt(t, "visual.best_performance_mode","Best performance mode",            "Disables everything — nuclear option")

    def _tab_network(self):
        t = self._make_tab("Network")
        self._section(t, "Network Stack Tuning")
        self._opt(t, "network.enabled",                 "Enable network tuning",              "Master toggle")
        self._opt(t, "network.optimal_decision",         "Optimal decisions",                  "Recommended — enables all per-NIC tuning")
        self._section(t, "TCP/IP")
        self._opt(t, "network.disable_nagle",            "Disable Nagle's algorithm",          "Lower latency: sends packets immediately instead of batching")
        self._opt(t, "network.disable_rsc",              "Disable Receive Segment Coalescing", "Lower DPC interrupt latency (slight CPU overhead increase)")
        self._opt(t, "network.disable_ecn",              "Disable ECN",                        "Prevents stalls on routers that mishandle Explicit Congestion Notification")
        self._opt(t, "network.tcp_socket_tuning",        "TCP socket pool tuning",             "Shrinks TIME_WAIT, raises port ceiling to 65534, raises max connections")
        self._opt(t, "network.enforce_pmtud",            "Enforce Path MTU Discovery",         "Ensures optimal packet size negotiation end-to-end")
        self._opt(t, "network.qos_reserve_fix",          "Remove QoS bandwidth reservation",   "Reclaims the 20% Windows reserves for QoS by default")
        self._opt(t, "network.disable_autotuning",       "Disable TCP autotuning",             "Off by default — risky on some networks, can hurt throughput")
        self._section(t, "DNS")
        self._opt(t, "network.set_dns_cache_size",       "Increase DNS cache TTL",             "Caches DNS results longer — faster repeated connections")
        self._opt(t, "network.flush_dns_on_switch",      "Flush DNS on profile switch",        "Clears stale DNS entries each time a profile changes")
        self._dns_speed_panel(t)
        self._section(t, "NIC Adapter (applied under Optimal Decisions)")
        self._opt(t, "network.nic_interrupt_moderation", "Disable interrupt moderation",       "Lowest DPC latency — NIC interrupts CPU immediately instead of batching")
        self._opt(t, "network.nic_rss_tuning",           "RSS tuning",                         "Enables Receive Side Scaling and tunes queue count to P-core count")

    def _dns_speed_panel(self, parent):
        from core import dns_bench
        from core.constants import COLOR_COOL, COLOR_WARM, COLOR_HOT

        self._section(parent, "DNS Speed Test")
        self._note(parent,
            "Benchmarks 9 public DNS providers from your location and applies the fastest. "
            "Requires administrator privileges to change DNS settings.")

        # ── Controls row ──────────────────────────────────────────────────────
        ctrl = tk.Frame(parent, bg=BG_SECT, pady=4)
        ctrl.pack(fill="x", padx=20, pady=(0, 4))

        tk.Label(ctrl, text="Interface:", font=("Segoe UI", 9),
                 bg=BG_SECT, fg=FG).pack(side="left", padx=(0, 6))

        ifaces     = dns_bench.get_active_interfaces()
        iface_var  = tk.StringVar(value=ifaces[0] if ifaces else "")
        status_var = tk.StringVar(value="" if ifaces else "No connected interfaces found.")

        if not ifaces:
            tk.Label(ctrl, text="No connected interfaces found",
                     font=("Segoe UI", 9), bg=BG_SECT, fg=WARN).pack(side="left")
        elif len(ifaces) == 1:
            tk.Label(ctrl, text=ifaces[0], font=("Segoe UI", 9),
                     bg=BG_SECT, fg=ACCENT).pack(side="left", padx=(0, 12))
        else:
            combo = ttk.Combobox(ctrl, textvariable=iface_var, values=ifaces,
                                 state="readonly", width=20, font=("Segoe UI", 9))
            combo.pack(side="left", padx=(0, 12))

        # ── Results frame (populated after benchmark) ──────────────────────
        results_outer = tk.Frame(parent, bg=BG_SECT)
        results_outer.pack(fill="x", padx=20, pady=(4, 0))

        # ── Status label ──────────────────────────────────────────────────
        status_lbl = tk.Label(parent, textvariable=status_var,
                              font=("Segoe UI", 8, "italic"),
                              bg=BG_SECT, fg=FG_DIM, anchor="w")
        status_lbl.pack(fill="x", padx=20, pady=(2, 8))

        def _apply_dns(primary, secondary, primary_v6, secondary_v6):
            iface = iface_var.get()
            if not iface:
                status_var.set("No interface selected.")
                return
            status_var.set(f"Applying {primary}...")
            def _worker():
                ok, msg = dns_bench.set_dns(iface, primary, secondary, primary_v6, secondary_v6)
                if ok:
                    txt = (f"Applied — IPv4: {primary} / {secondary}"
                           f"   IPv6: {primary_v6} / {secondary_v6}")
                else:
                    if "access" in msg.lower() or "denied" in msg.lower():
                        txt = "Access denied — run AlienCore as administrator."
                    else:
                        txt = f"Failed: {msg}"
                parent.after(0, lambda: status_var.set(txt))
            threading.Thread(target=_worker, daemon=True).start()

        def _reset_dhcp():
            iface = iface_var.get()
            if not iface:
                return
            status_var.set("Resetting to DHCP...")
            def _worker():
                ok, msg = dns_bench.reset_to_dhcp(iface)
                txt = "DNS reset to automatic (DHCP)." if ok else f"Failed: {msg}"
                parent.after(0, lambda: status_var.set(txt))
            threading.Thread(target=_worker, daemon=True).start()

        def _show_results(results):
            for w in results_outer.winfo_children():
                w.destroy()

            # Header row
            hdr = tk.Frame(results_outer, bg=BG_PANEL)
            hdr.pack(fill="x", pady=(0, 2))
            for text, width in [("Provider", 16), ("Primary DNS", 18), ("Latency", 12)]:
                tk.Label(hdr, text=text, font=("Segoe UI", 8, "bold"),
                         bg=BG_PANEL, fg=FG_DIM, width=width, anchor="w"
                         ).pack(side="left", padx=6, pady=3)

            fastest_marked = False
            for r in results:
                row = tk.Frame(results_outer, bg=BG_SECT)
                row.pack(fill="x", pady=1)

                is_fastest = not fastest_marked and not r["failed"]
                if is_fastest:
                    fastest_marked = True
                    prefix     = "★ "
                    name_color = ACCENT
                else:
                    prefix     = "    "
                    name_color = FG

                tk.Label(row, text=prefix + r["name"],
                         font=("Segoe UI", 9), bg=BG_SECT, fg=name_color,
                         width=16, anchor="w").pack(side="left", padx=6, pady=2)
                tk.Label(row, text=r["primary"],
                         font=("Consolas", 9), bg=BG_SECT, fg=FG_DIM,
                         width=18, anchor="w").pack(side="left")

                if r["failed"]:
                    lat_text  = "timeout"
                    lat_color = DANGER
                else:
                    ms        = r["latency_ms"]
                    lat_text  = f"{ms:.1f} ms"
                    lat_color = (COLOR_COOL if ms < 30 else
                                 COLOR_WARM if ms < 80 else COLOR_HOT)

                tk.Label(row, text=lat_text, font=("Consolas", 9, "bold"),
                         bg=BG_SECT, fg=lat_color,
                         width=12, anchor="w").pack(side="left")

                if not r["failed"]:
                    pr, sc = r["primary"], r["secondary"]
                    pv, sv = r["primary_v6"], r["secondary_v6"]
                    tk.Button(row, text="Apply",
                              font=("Segoe UI", 8), fg=ACCENT2, bg=BTN_BG,
                              activeforeground=ACCENT2, activebackground=BTN_HOV,
                              relief="flat", padx=8, pady=1, cursor="hand2", bd=0,
                              highlightthickness=0,
                              command=lambda p=pr, s=sc, p6=pv, s6=sv: _apply_dns(p, s, p6, s6)
                              ).pack(side="right", padx=6)

            run_btn.config(state="normal", text="Run Speed Test")
            status_var.set("Done.  Click Apply on any row to use that DNS.")

        def _run_bench():
            if not iface_var.get():
                return
            run_btn.config(state="disabled", text="Testing...")
            for w in results_outer.winfo_children():
                w.destroy()
            status_var.set("Benchmarking all providers — takes ~5 seconds...")

            def _worker():
                results = dns_bench.benchmark(count=4, timeout=2.0)
                parent.after(0, lambda: _show_results(results))

            threading.Thread(target=_worker, daemon=True).start()

        run_btn = self._btn(ctrl, "Run Speed Test", _run_bench, ACCENT)
        run_btn.pack(side="left", padx=(0, 8))
        if ifaces:
            self._btn(ctrl, "Reset to DHCP", _reset_dhcp, FG_DIM).pack(side="left")

    def _tab_storage(self):
        t = self._make_tab("Storage")
        self._hw_panel(t, "drives")
        self._section(t, "Drive Optimization")
        self._opt(t, "storage.enabled",                    "Enable storage tuning",              "Master toggle")
        self._opt(t, "storage.optimal_decision",           "Optimal decisions",                  "Per drive type")
        self._opt(t, "storage.ensure_trim_enabled",        "Ensure TRIM enabled",                "Keeps NVMe at peak speed")
        self._opt(t, "storage.disable_8dot3_names",        "Disable 8.3 filename generation",    "Small I/O improvement")
        self._opt(t, "storage.disable_last_access_update", "Disable last access timestamps",     "Reduces NVMe write cycles")
        self._opt(t, "storage.write_cache_enabled",        "Enable write caching",               "Improves write speed")
        self._opt(t, "storage.indexing_managed",           "Smart indexing management",          "Disables on NVMe, keeps on HDD")

    def _tab_privacy(self):
        t = self._make_tab("Privacy")
        self._section(t, "Telemetry & Privacy Tweaks")
        self._opt(t, "privacy.enabled",                       "Enable privacy tweaks",          "Master toggle")
        self._opt(t, "privacy.optimal_decision",              "Optimal decisions",              "Full safe set")
        self._opt(t, "privacy.disable_telemetry",             "Disable telemetry",              "Stops DiagTrack")
        self._opt(t, "privacy.disable_advertising_id",        "Disable advertising ID",         "")
        self._opt(t, "privacy.disable_activity_history",      "Disable activity history",       "")
        self._opt(t, "privacy.disable_cortana",               "Disable Cortana",                "")
        self._opt(t, "privacy.disable_feedback_notifications","Disable feedback notifications", "")

    def _tab_profiles(self):
        t = self._make_tab("Profiles")
        self._section(t, "App-Based Profile Switching")
        self._opt(t, "profiles.enabled",          "Enable profile switching",          "Auto idle/gaming/streaming")
        self._opt(t, "profiles.detect_by_process","Detect by process names",           "OBS, game EXEs, etc.")
        self._opt(t, "profiles.detect_by_load",   "Detect by load signals",            "GPU% and CPU% thresholds")
        self._slider(t, "profiles.gaming_gpu_threshold", "Gaming GPU% threshold", 10, 90, 5, fmt=lambda v: f"{int(v)}%")
        self._slider(t, "profiles.gaming_cpu_threshold", "Gaming CPU% threshold", 10, 90, 5, fmt=lambda v: f"{int(v)}%")
        self._section(t, "Custom process lists")
        self._note(t, "Add your own EXEs below — one per line (e.g. mygame.exe)")
        self._text_list(t, "profiles.custom_streaming_processes", "Additional streaming processes")
        self._text_list(t, "profiles.custom_gaming_processes",    "Additional gaming processes")

    def _tab_custom_profiles(self):
        t = self._make_tab("Custom Profiles")
        self._section(t, "User-Defined Profiles")
        self._note(t, (
            "Create named profiles that activate when specific apps are running. "
            "Each custom profile inherits the tweaks of a base behavior "
            "(Idle, Gaming, or Streaming) and appears in the tray Override menu."
        ))

        # ── Profile list ──────────────────────────────────────────────────────
        list_frame = tk.Frame(t, bg=BG_PANEL, padx=12, pady=10)
        list_frame.pack(fill="x", padx=16, pady=(8, 0))

        # Header row
        hdr = tk.Frame(list_frame, bg=BG_PANEL)
        hdr.pack(fill="x", pady=(0, 4))
        for col, w in [("Name", 20), ("Label", 18), ("Behavior", 12),
                       ("Trigger processes", 30), ("Color", 10)]:
            tk.Label(hdr, text=col, font=("Segoe UI", 8, "bold"),
                     bg=BG_PANEL, fg=FG_DIM, width=w, anchor="w").pack(side="left")

        # Scrollable listbox area
        lb_frame = tk.Frame(list_frame, bg=BG_PANEL)
        lb_frame.pack(fill="x")
        self._cp_listbox_frame = lb_frame

        # Button bar
        btn_bar = tk.Frame(t, bg=BG_SECT, pady=6)
        btn_bar.pack(fill="x", padx=16, pady=(4, 0))
        self._btn(btn_bar, "+ New Profile",   self._cp_new,    ACCENT2, bold=True).pack(side="left", padx=(0, 8))
        self._btn(btn_bar, "Edit Selected",   self._cp_edit,   ACCENT).pack(side="left", padx=(0, 8))
        self._btn(btn_bar, "Delete Selected", self._cp_delete, DANGER).pack(side="left")

        self._cp_selected = None   # currently selected profile name
        self._cp_refresh()

    def _cp_refresh(self):
        """Rebuild the custom profiles list display."""
        for w in self._cp_listbox_frame.winfo_children():
            w.destroy()
        self._cp_selected = None

        user_profiles = self.config.get("profiles", {}).get("user_profiles", [])
        if not user_profiles:
            tk.Label(self._cp_listbox_frame, text="No custom profiles yet.",
                     font=("Segoe UI", 9, "italic"), bg=BG_PANEL,
                     fg=FG_DIM).pack(anchor="w", pady=8)
            return

        self._cp_row_vars = {}
        for up in sorted(user_profiles, key=lambda p: p.get("priority", 50)):
            self._cp_make_row(up)

    def _cp_make_row(self, up: dict):
        name      = up.get("name", "")
        label     = up.get("label", name)
        behavior  = up.get("behavior", "idle")
        procs     = ", ".join(up.get("processes", []))
        color_hex = up.get("color", "#7700cc")

        row = tk.Frame(self._cp_listbox_frame, bg=BG_PANEL, pady=4, padx=4,
                       cursor="hand2")
        row.pack(fill="x", pady=1)

        # Color swatch
        swatch = tk.Frame(row, bg=color_hex, width=14, height=14)
        swatch.pack(side="left", padx=(0, 6))
        swatch.pack_propagate(False)

        def select(e, n=name, r=row):
            for w in self._cp_listbox_frame.winfo_children():
                if isinstance(w, tk.Frame):
                    w.configure(bg=BG_PANEL)
                    for child in w.winfo_children():
                        if not isinstance(child, tk.Frame) or child.cget("bg") in (
                                "#7700cc", "#cc2200", "#0066cc", "#1a4a6e", "#886600"):
                            pass  # keep swatches
                        else:
                            try: child.configure(bg=BG_PANEL)
                            except Exception: pass
            r.configure(bg=BG_SECT)
            for child in r.winfo_children():
                try:
                    if child.cget("bg") != color_hex:
                        child.configure(bg=BG_SECT)
                except Exception:
                    pass
            self._cp_selected = n

        row.bind("<Button-1>", select)
        for col, val, w in [
            (name,     name,     20),
            (label,    label,    18),
            (behavior, behavior, 12),
            (procs,    procs[:40] + ("…" if len(procs) > 40 else ""), 30),
            (color_hex, color_hex, 10),
        ]:
            lbl = tk.Label(row, text=val, font=("Segoe UI", 9),
                           bg=BG_PANEL, fg=FG, width=w, anchor="w")
            lbl.pack(side="left")
            lbl.bind("<Button-1>", select)

    def _cp_new(self):
        self._cp_dialog(None)

    def _cp_edit(self):
        if not self._cp_selected:
            return
        profiles_list = self.config.get("profiles", {}).get("user_profiles", [])
        for up in profiles_list:
            if up.get("name") == self._cp_selected:
                self._cp_dialog(up)
                return

    def _cp_delete(self):
        if not self._cp_selected:
            return
        profiles_list = self.config.get("profiles", {}).get("user_profiles", [])
        self.config["profiles"]["user_profiles"] = [
            p for p in profiles_list if p.get("name") != self._cp_selected
        ]
        self._cp_refresh()

    def _cp_dialog(self, existing: dict | None):
        """Open a Toplevel dialog to create or edit a custom profile."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Custom Profile" if existing else "New Custom Profile")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.geometry("520x480")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.lift()
        dlg.focus_force()

        # Center over parent
        self.root.update_idletasks()
        px = self.root.winfo_x() + (self.root.winfo_width()  - 520) // 2
        py = self.root.winfo_y() + (self.root.winfo_height() - 480) // 2
        dlg.geometry(f"520x480+{px}+{py}")

        def field(lbl):
            tk.Label(dlg, text=lbl, font=("Segoe UI", 9),
                     bg=BG, fg=FG).pack(anchor="w", padx=24, pady=(12, 2))

        def entry(initial=""):
            e = tk.Entry(dlg, font=("Consolas", 10), bg=BG_PANEL,
                         fg=FG, insertbackground=FG, relief="flat",
                         width=44)
            e.insert(0, initial)
            e.pack(padx=24, fill="x")
            return e

        tk.Label(dlg, text="Custom Profile",
                 font=("Segoe UI", 14, "bold"),
                 bg=BG, fg=ACCENT).pack(anchor="w", padx=24, pady=(20, 0))
        tk.Frame(dlg, bg=SEP, height=1).pack(fill="x", padx=24, pady=(8, 0))

        field("Name  (slug — lowercase, no spaces, e.g.  video_editing)")
        e_name = entry(existing.get("name", "") if existing else "")

        field("Display label  (shown in tray menu)")
        e_label = entry(existing.get("label", "") if existing else "")

        field("Trigger processes  (comma-separated EXE names)")
        e_procs = entry(", ".join(existing.get("processes", [])) if existing else "")

        field("Base behavior")
        beh_var = tk.StringVar(value=existing.get("behavior", "idle") if existing else "idle")
        beh_row = tk.Frame(dlg, bg=BG); beh_row.pack(anchor="w", padx=24)
        for val, lbl in [("idle", "Idle"), ("gaming", "Gaming"), ("streaming", "Streaming")]:
            tk.Radiobutton(beh_row, text=lbl, variable=beh_var, value=val,
                           bg=BG, fg=FG, selectcolor=BG,
                           activebackground=BG, activeforeground=ACCENT,
                           font=("Segoe UI", 10)).pack(side="left", padx=10)

        field("Tray color  (hex, e.g.  #aa00ff)")
        e_color = entry(existing.get("color", "#7700cc") if existing else "#7700cc")

        field("Priority  (lower = checked first among custom profiles)")
        e_prio = entry(str(existing.get("priority", 50)) if existing else "50")

        err_lbl = tk.Label(dlg, text="", font=("Segoe UI", 8),
                           bg=BG, fg=DANGER)
        err_lbl.pack(anchor="w", padx=24, pady=(6, 0))

        def on_save():
            name  = e_name.get().strip().lower().replace(" ", "_")
            label = e_label.get().strip() or name
            procs = [p.strip() for p in e_procs.get().split(",") if p.strip()]
            color = e_color.get().strip() or "#7700cc"
            try:
                prio = int(e_prio.get().strip())
            except ValueError:
                prio = 50

            if not name:
                err_lbl.config(text="Name is required.")
                return
            if not procs:
                err_lbl.config(text="At least one trigger process is required.")
                return

            profiles_list = self.config.setdefault("profiles", {}).setdefault("user_profiles", [])

            if existing:
                # Update in-place
                for i, p in enumerate(profiles_list):
                    if p.get("name") == existing["name"]:
                        profiles_list[i] = {
                            "name": name, "label": label, "processes": procs,
                            "behavior": beh_var.get(), "color": color, "priority": prio,
                        }
                        break
            else:
                # Check for duplicate name
                if any(p.get("name") == name for p in profiles_list):
                    err_lbl.config(text=f"A profile named '{name}' already exists.")
                    return
                profiles_list.append({
                    "name": name, "label": label, "processes": procs,
                    "behavior": beh_var.get(), "color": color, "priority": prio,
                })

            dlg.destroy()
            self._cp_refresh()

        foot = tk.Frame(dlg, bg=BG); foot.pack(fill="x", padx=24, pady=16, side="bottom")
        self._btn(foot, "Cancel", dlg.destroy, FG_DIM).pack(side="right", padx=(8, 0))
        self._btn(foot, "  Save  ", on_save, ACCENT2, bold=True).pack(side="right")

    def _tab_service(self):
        t = self._make_tab("Service")
        self._hw_panel(t, "platform")
        self._section(t, "Administrator Rights")
        self._admin_panel(t)
        self._section(t, "Start with Windows")
        self._startup_panel(t)

        self._section(t, "AlienCore Behavior")
        self._opt(t, "service.log_enabled",              "Enable logging",                   "Writes to aliencore.log — grows unbounded over time")
        self._opt(t, "service.notify_on_profile_switch", "Toast on profile switch",          "Windows notification")
        self._opt(t, "service.hardware_refresh_on_startup","Re-scan hardware on startup",    "Adds ~3s to boot")

        self._section(t, "LibreHardwareMonitor")
        self._lhm_panel(t)

        self._section(t, "Windows Services Manager")
        self._note(t, "Green = already optimal. Hover a service name for details. "
                      "Change startup type via dropdown. Locked rows are system-critical.")

        # Apply all button
        br = tk.Frame(t, bg=BG_SECT); br.pack(fill="x", padx=16, pady=(4,8))
        self._btn(br, "Apply All Safe Recommendations", self._apply_services, ACCENT2, bold=True).pack(side="left")
        tk.Label(br, text="  Safe services only — skips Caution and Leave Alone",
                 font=("Segoe UI",8), bg=BG_SECT, fg=FG_DIM).pack(side="left")

        # Headers
        hdr = tk.Frame(t, bg=BG_PANEL, padx=16, pady=4)
        hdr.pack(fill="x", padx=16, pady=(0,2))
        for col, w in [("Service",26),("State",10),("Current",16),("Recommended",16),("Safety",10)]:
            tk.Label(hdr, text=col, font=("Segoe UI",8,"bold"),
                     bg=BG_PANEL, fg=FG_DIM, width=w, anchor="w").pack(side="left")

        self._svc_tab    = t
        self._svc_loader = tk.Label(t, text="Loading...",
                                    font=("Segoe UI",9,"italic"),
                                    bg=BG_SECT, fg=FG_DIM)
        self._svc_loader.pack(anchor="w", padx=20, pady=8)
        threading.Thread(target=self._load_services, daemon=True).start()

    def _tab_ai(self):
        t = self._make_tab("AI")
        from core import license as lic
        if not lic.is_allowed("ai_chat"):
            _, reason = lic.check("ai_chat")
            self._gate(t, "ai_chat", lambda p: None)
            return

        # ── Provider ──────────────────────────────────────────────────────────
        self._section(t, "AI Provider")
        self._note(t, "Connect AlienCore to any AI model for intelligent watchdog "
                      "analysis and live chat.")

        pv = self._var("ai.provider", str)
        pf = tk.Frame(t, bg=BG_SECT); pf.pack(fill="x", padx=20, pady=(0, 8))
        for val, lbl in [("anthropic",    "Anthropic  (Claude)"),
                         ("openai_compat","OpenAI-compatible endpoint")]:
            tk.Radiobutton(pf, text=lbl, variable=pv, value=val,
                           bg=BG_SECT, fg=FG, selectcolor=BG_SECT,
                           activebackground=BG_SECT, activeforeground=ACCENT,
                           font=("Segoe UI", 9)).pack(side="left", padx=10)

        # ── API key ───────────────────────────────────────────────────────────
        self._section(t, "API Key")
        self._note(t, "Stored locally in config.json. Sent only to your configured endpoint.")

        key_frame = tk.Frame(t, bg=BG_SECT); key_frame.pack(fill="x", padx=20, pady=(0, 8))

        key_var = self._var("ai.api_key", str)
        key_entry = tk.Entry(key_frame, textvariable=key_var, show="•",
                             bg="#1a1a1a", fg=FG, insertbackground=FG,
                             relief="flat", font=("Consolas", 10), width=52)
        key_entry.pack(side="left", padx=(0, 6), ipady=4)

        def _toggle_show():
            key_entry.config(show="" if key_entry.cget("show") else "•")

        tk.Button(key_frame, text="Show / Hide",
                  bg=BTN_BG, fg=FG, relief="flat",
                  activebackground=BTN_HOV, font=("Segoe UI", 8),
                  command=_toggle_show, padx=8).pack(side="left", padx=4)

        # ── Base URL (OpenAI-compatible only) ─────────────────────────────────
        self._section(t, "Endpoint  (OpenAI-compatible only)")
        self._note(t,
            "Leave blank for OpenAI (https://api.openai.com/v1).\n"
            "Examples:\n"
            "  Groq        https://api.groq.com/openai/v1\n"
            "  Mistral     https://api.mistral.ai/v1\n"
            "  Together    https://api.together.xyz/v1\n"
            "  Perplexity  https://api.perplexity.ai\n"
            "  Ollama      http://localhost:11434/v1   (key = 'ollama')\n"
            "  LM Studio   http://localhost:1234/v1   (key = 'lm-studio')\n"
            "  xAI (Grok)  https://api.x.ai/v1\n"
            "Ignored when provider is set to Anthropic."
        )
        self._entry(t, "ai.base_url", "Base URL", width=50)

        # ── Model ─────────────────────────────────────────────────────────────
        self._section(t, "Model")
        self._note(t,
            "Required for OpenAI-compatible providers. Optional for Anthropic.\n"
            "Anthropic defaults → chat: claude-sonnet-4-6 · watchdog: claude-haiku-4-5-20251001\n"
            "OpenAI defaults    → chat: gpt-4o · watchdog: gpt-4o-mini"
        )
        self._entry(t, "ai.model",         "Chat model")
        self._entry(t, "ai.watchdog_model","Watchdog model  (blank = same as chat)")

        # ── Test ──────────────────────────────────────────────────────────────
        self._test_status = tk.StringVar(value="")
        test_lbl = tk.Label(t, textvariable=self._test_status,
                            bg=BG_SECT, fg=FG_DIM, font=("Segoe UI", 9))
        test_lbl.pack(anchor="w", padx=20, pady=(6, 2))

        def _test():
            # Write current field values to config before testing
            ai_cfg = self.config.setdefault("ai", {})
            ai_cfg["api_key"]  = key_var.get().strip()
            ai_cfg["provider"] = pv.get()
            ai_cfg["base_url"] = self.vars.get("ai.base_url", tk.StringVar()).get().strip()
            ai_cfg["model"]    = self.vars.get("ai.model",    tk.StringVar()).get().strip()
            cfg.save(self.config)
            self._test_status.set("Testing…")
            test_lbl.config(fg=ACCENT)

            def _do():
                from core import ai_manager
                result = ai_manager.test_connection()
                color  = ACCENT2 if "Error" not in result else DANGER
                t.after(0, lambda: (self._test_status.set(result),
                                    test_lbl.config(fg=color)))
            threading.Thread(target=_do, daemon=True).start()

        self._btn(t, "Test Connection", _test, ACCENT).pack(anchor="w",
                  padx=20, pady=(4, 12))

        # ── Watchdog ──────────────────────────────────────────────────────────
        self._section(t, "AI Watchdog")
        self._note(t, "Periodically analyzes sensor data and logs a recommendation "
                      "when temps or load are elevated. Only calls the API when "
                      "something is notable — saves cost on pay-per-token plans.")
        self._opt(t, "ai.watchdog_enabled", "Enable AI watchdog", "")
        self._slider(t, "ai.watchdog_interval_sec",
                     "Check interval", 60, 1800, 30,
                     fmt=lambda v: f"{int(v)//60} min {int(v)%60:02d} s")

        # ── Config Advisor ────────────────────────────────────────────────────
        self._section(t, "AI Config Advisor")
        self._note(t, "Analyzes your current config + live sensor data and proposes specific "
                      "targeted changes. You review and approve each change individually "
                      "before anything is saved. A backup is always made first.")

        def _open_advisor():
            from gui.ai_advisor_ui import open_advisor_thread
            open_advisor_thread()

        self._btn(t, "Open Config Advisor", _open_advisor, ACCENT).pack(anchor="w",
                  padx=20, pady=(0, 12))

        # ── Chat ──────────────────────────────────────────────────────────────
        self._section(t, "AI Chat")
        self._note(t, "Live chat — sensor context is injected automatically with "
                      "every message so the AI knows your current system state.")
        self._slider(t, "ai.chat_history_max",
                     "Rolling history", 4, 60, 2,
                     fmt=lambda v: f"{int(v)} messages")

        def _open_chat():
            # Subprocess so the chat gets its own Tk interpreter — avoids
            # sensor-bar geometry glitches caused by two live tk.Tk() roots.
            import os, sys, subprocess
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            subprocess.Popen(
                [sys.executable, os.path.join(base, "aliencore.py"), "--ai-chat"],
                cwd=base,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

        self._btn(t, "Open AI Chat", _open_chat, ACCENT2).pack(anchor="w",
                  padx=20, pady=(0, 16))

    def _tab_insights(self):
        t = self._make_tab("Insights")
        self._section(t, "What AlienCore Has Learned")

        # Load summary
        try:
            from core import learning
            summary = learning.get_insights_summary()
            suggestions = [s for s in learning.get_suggestions()
                          if not s.get("dismissed") and not s.get("accepted")]
        except Exception as e:
            summary = {"status": "error", "message": str(e)}
            suggestions = []

        if summary.get("status") == "collecting":
            self._note(t, summary.get("message", "Collecting data..."))
        elif summary.get("status") == "active":
            # Stats panel
            panel = tk.Frame(t, bg=BG_HW, padx=16, pady=10)
            panel.pack(fill="x", padx=16, pady=(8, 12))
            tk.Label(panel, text="System Knowledge Summary",
                     font=("Segoe UI", 9, "bold"),
                     bg=BG_HW, fg=FG_HEAD).pack(anchor="w", pady=(0, 6))

            def stat(label, value, color=ACCENT):
                r = tk.Frame(panel, bg=BG_HW); r.pack(anchor="w", pady=1)
                tk.Label(r, text=f"{label}:", font=("Segoe UI", 8),
                         bg=BG_HW, fg=FG_DIM, width=22, anchor="w").pack(side="left")
                tk.Label(r, text=str(value), font=("Segoe UI", 8, "bold"),
                         bg=BG_HW, fg=color, anchor="w").pack(side="left")

            stat("Days of data",        summary.get("days_of_data", 0))
            stat("Total events logged", summary.get("total_events", 0))
            stat("Gaming sessions",     summary.get("gaming_sessions", 0))
            stat("Streaming sessions",  summary.get("streaming_sessions", 0))
            stat("Thermal warnings",    summary.get("thermal_warnings", 0),
                 color=WARN if summary.get("thermal_warnings", 0) > 0 else ACCENT2)
            stat("Critical temp events",summary.get("thermal_criticals", 0),
                 color=DANGER if summary.get("thermal_criticals", 0) > 0 else ACCENT2)
            stat("Pending suggestions", summary.get("pending_suggestions", 0),
                 color=ACCENT if summary.get("pending_suggestions", 0) > 0 else FG_DIM)

            if summary.get("peak_gaming_hours"):
                h = summary["peak_gaming_hours"][0]
                stat("Peak gaming hour", f"{h}:00 - {h+1}:00")
            if summary.get("peak_streaming_hours"):
                h = summary["peak_streaming_hours"][0]
                stat("Peak streaming hour", f"{h}:00 - {h+1}:00")

        # Suggestions inbox
        self._section(t, "Suggestions & Insights")
        if not suggestions:
            self._note(t, "No new suggestions yet. AlienCore will notify you when it has "
                          "something useful to share — usually after a week of normal use.")
        else:
            for sug in suggestions:
                self._suggestion_card(t, sug)

    def _suggestion_card(self, parent, sug: dict):
        """Render a single suggestion card."""
        priority = sug.get("priority", "normal")
        border   = DANGER if priority == "high" else ACCENT
        card = tk.Frame(parent, bg=BG_PANEL, padx=12, pady=10,
                        highlightbackground=border, highlightthickness=1)
        card.pack(fill="x", padx=16, pady=4)

        # Category badge
        cat_colors = {
            "thermal":     (DANGER, "🌡 Thermal"),
            "performance": (ACCENT, "⚡ Performance"),
            "pattern":     (ACCENT2, "📊 Pattern"),
        }
        cat_color, cat_label = cat_colors.get(
            sug.get("category", ""), (FG_DIM, sug.get("category", ""))
        )
        tk.Label(card, text=cat_label, font=("Segoe UI", 7, "bold"),
                 bg=BG_PANEL, fg=cat_color).pack(anchor="w")

        # Title
        tk.Label(card, text=sug["title"], font=("Segoe UI", 10, "bold"),
                 bg=BG_PANEL, fg=FG_HEAD, wraplength=820,
                 justify="left").pack(anchor="w", pady=(2, 0))

        # Message
        tk.Label(card, text=sug["message"], font=("Segoe UI", 8),
                 bg=BG_PANEL, fg=FG, wraplength=820,
                 justify="left").pack(anchor="w", pady=(4, 8))

        # Buttons
        btn_row = tk.Frame(card, bg=BG_PANEL)
        btn_row.pack(anchor="w")

        if sug.get("action") and sug.get("action_label"):
            self._btn(btn_row, f"✓ {sug['action_label']}",
                      lambda s=sug: self._accept_suggestion(s),
                      color=ACCENT2, bold=True).pack(side="left", padx=(0, 8))

        self._btn(btn_row, "Dismiss",
                  lambda s=sug: self._dismiss_suggestion(s, card),
                  color=FG_DIM).pack(side="left")

        # Date
        try:
            from datetime import datetime
            dt = datetime.fromtimestamp(sug["created_at"])
            tk.Label(card, text=dt.strftime("Observed %b %d, %Y"),
                     font=("Segoe UI", 7), bg=BG_PANEL,
                     fg=FG_DIM).pack(anchor="e")
        except Exception:
            pass

    def _accept_suggestion(self, sug: dict):
        from core import learning
        action = learning.accept_suggestion(sug["id"])
        if action and action.get("type") == "set_config":
            key   = action["key"]
            value = action["value"]
            self._cfg_set(key, value)
            cfg.save(self.config)
            logger.info("Suggestion accepted — applied %s = %s", key, value)
        import tkinter.messagebox as mb
        mb.showinfo("AlienCore", "Suggestion applied! Restart AlienCore for full effect.")

    def _dismiss_suggestion(self, sug: dict, card: tk.Frame):
        from core import learning
        learning.dismiss_suggestion(sug["id"])
        card.destroy()

    def _tab_thresholds(self):
        t = self._make_tab("Thresholds")
        self._section(t, "Temperature Alert Thresholds")
        self._note(t, "Controls color coding of sensor readings. Does not trigger throttling.")
        self._slider(t, "thresholds.cpu_warn",  "CPU warning (°C)",   40, 95,  1, fmt=lambda v: f"{int(v)}°C")
        self._slider(t, "thresholds.cpu_crit",  "CPU critical (°C)",  50, 105, 1, fmt=lambda v: f"{int(v)}°C")
        self._slider(t, "thresholds.gpu_warn",  "GPU warning (°C)",   40, 95,  1, fmt=lambda v: f"{int(v)}°C")
        self._slider(t, "thresholds.gpu_crit",  "GPU critical (°C)",  50, 100, 1, fmt=lambda v: f"{int(v)}°C")
        self._slider(t, "thresholds.nvme_warn", "NVMe warning (°C)",  30, 80,  1, fmt=lambda v: f"{int(v)}°C")
        self._slider(t, "thresholds.nvme_crit", "NVMe critical (°C)", 40, 90,  1, fmt=lambda v: f"{int(v)}°C")

    # ── Drivers tab ───────────────────────────────────────────────────────────

    _VENDOR_LINKS = {
        "nvidia":   ("NVIDIA",    "https://www.nvidia.com/drivers"),
        "intel":    ("Intel",     "https://www.intel.com/content/www/us/en/download-center/home.html"),
        "amd":      ("AMD",       "https://www.amd.com/support"),
        "advanced micro devices": ("AMD", "https://www.amd.com/support"),
        "realtek":  ("Realtek",   "https://www.realtek.com/en/downloads"),
        "killer":   ("Killer",    "https://www.killernetworking.com/driver-downloads/"),
        "rivet":    ("Killer",    "https://www.killernetworking.com/driver-downloads/"),
        "qualcomm": ("Qualcomm",  "https://www.qualcomm.com/support"),
        "broadcom": ("Broadcom",  "https://www.broadcom.com/support/download-search"),
        "mediatek": ("MediaTek",  "https://www.mediatek.com/products"),
    }

    def _tab_drivers(self):
        t = self._make_tab("Drivers")
        self._section(t, "Installed Drivers")
        self._note(t, "Third-party drivers shown by default. "
                      "NVIDIA status is checked live against the latest Game Ready Driver. "
                      "Click a vendor link to open their download page.")

        # Controls row
        ctrl = tk.Frame(t, bg=BG_SECT)
        ctrl.pack(fill="x", padx=16, pady=(4, 8))
        self._drv_show_ms = tk.BooleanVar(value=False)
        tk.Checkbutton(ctrl, text="Show Microsoft / Windows drivers",
                       variable=self._drv_show_ms,
                       bg=BG_SECT, fg=FG, selectcolor=BG_SECT,
                       activebackground=BG_SECT, activeforeground=ACCENT,
                       font=("Segoe UI", 9),
                       command=self._drivers_render).pack(side="left")
        self._btn(ctrl, "Refresh", self._drivers_refresh, ACCENT).pack(side="right")

        # Column headers
        hdr = tk.Frame(t, bg=BG_PANEL, padx=16, pady=4)
        hdr.pack(fill="x", padx=16, pady=(0, 2))
        for col, w in [("Device", 28), ("Provider", 18), ("Version", 16), ("Date", 12), ("Status", 20)]:
            tk.Label(hdr, text=col, font=("Segoe UI", 8, "bold"),
                     bg=BG_PANEL, fg=FG_DIM, width=w, anchor="w").pack(side="left")
        tk.Label(hdr, text="Download", font=("Segoe UI", 8, "bold"),
                 bg=BG_PANEL, fg=FG_DIM, anchor="w").pack(side="left")

        self._drv_tab          = t
        self._drv_all          = []
        self._drv_rows_frame   = None
        self._drv_nvidia_latest = None   # cached latest version string per refresh
        self._drv_status_labels = {}     # installed_ver → tk.Label
        self._drv_loader       = tk.Label(t, text="Loading drivers...",
                                          font=("Segoe UI", 9, "italic"),
                                          bg=BG_SECT, fg=FG_DIM)
        self._drv_loader.pack(anchor="w", padx=20, pady=8)
        threading.Thread(target=self._load_drivers, daemon=True).start()

    def _load_drivers(self):
        try:
            drivers = self._query_drivers()
            self._drv_tab.after(0, lambda: self._populate_drivers(drivers))
        except Exception as e:
            self._drv_tab.after(0, lambda: self._drv_loader.config(
                text=f"Failed to query drivers: {e}", fg=DANGER))

    def _query_drivers(self):
        import subprocess, json as _json
        ps = (
            "Get-WmiObject Win32_PnPSignedDriver | "
            "Where-Object { $_.DeviceName -and $_.DriverVersion } | "
            "Select-Object DeviceName, DriverProviderName, DriverVersion, DriverDate, DeviceClass | "
            "Sort-Object DeviceClass, DeviceName | "
            "ConvertTo-Json -Compress"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "PowerShell query failed")
        raw = result.stdout.strip()
        if not raw:
            return []
        data = _json.loads(raw)
        return [data] if isinstance(data, dict) else data

    def _populate_drivers(self, drivers):
        if hasattr(self, "_drv_loader") and self._drv_loader.winfo_exists():
            self._drv_loader.destroy()
        self._drv_all = drivers
        self._drivers_render()

    def _drivers_render(self):
        # Destroy previous rows frame if it exists
        if self._drv_rows_frame and self._drv_rows_frame.winfo_exists():
            self._drv_rows_frame.destroy()
        self._drv_status_labels = {}

        show_ms = self._drv_show_ms.get()
        drivers = [
            d for d in self._drv_all
            if show_ms or (d.get("DriverProviderName") or "").lower() not in
               {"microsoft", "microsoft corporation", "windows", "(standard display types)"}
        ]

        # Deduplicate by (name, version, date) — same package installed as multiple components
        seen = set()
        unique = []
        for d in drivers:
            key = (d.get("DeviceName",""), d.get("DriverVersion",""), d.get("DriverDate",""))
            if key not in seen:
                seen.add(key)
                unique.append(d)
        drivers = unique

        if not drivers:
            self._drv_rows_frame = tk.Label(
                self._drv_tab,
                text="No third-party drivers found. Enable 'Show Microsoft drivers' to see all.",
                font=("Segoe UI", 9, "italic"), bg=BG_SECT, fg=FG_DIM)
            self._drv_rows_frame.pack(anchor="w", padx=20, pady=8)
            return

        self._drv_rows_frame = tk.Frame(self._drv_tab, bg=BG_SECT)
        self._drv_rows_frame.pack(fill="x", padx=16, pady=(0, 16))

        nvidia_versions = []  # list of (version_string, status_label) to check

        for i, d in enumerate(drivers):
            row_bg = BG_PANEL if i % 2 == 0 else BG_SECT
            row = tk.Frame(self._drv_rows_frame, bg=row_bg, padx=16, pady=3)
            row.pack(fill="x", pady=1)

            name     = (d.get("DeviceName")         or "Unknown")[:48]
            provider = (d.get("DriverProviderName") or "")[:22]
            version  = (d.get("DriverVersion")      or "")[:20]
            date_raw = d.get("DriverDate") or ""
            date_str = (f"{date_raw[0:4]}-{date_raw[4:6]}-{date_raw[6:8]}"
                        if len(date_raw) >= 8 else "")

            tk.Label(row, text=name, font=("Segoe UI", 9),
                     bg=row_bg, fg=FG, width=32, anchor="w").pack(side="left")
            tk.Label(row, text=provider, font=("Segoe UI", 8),
                     bg=row_bg, fg=FG_DIM, width=20, anchor="w").pack(side="left")
            tk.Label(row, text=version, font=("Consolas", 8),
                     bg=row_bg, fg=FG_DIM, width=18, anchor="w").pack(side="left")
            tk.Label(row, text=date_str, font=("Segoe UI", 8),
                     bg=row_bg, fg=FG_DIM, width=12, anchor="w").pack(side="left")

            # Status column
            is_nvidia = "nvidia" in (d.get("DriverProviderName") or "").lower()
            if is_nvidia:
                status_lbl = tk.Label(row, text="Checking...", font=("Segoe UI", 8),
                                      bg=row_bg, fg=FG_DIM, width=20, anchor="w")
                nvidia_versions.append((version, status_lbl))
            else:
                status_lbl = tk.Label(row, text="\u2014", font=("Segoe UI", 8),
                                      bg=row_bg, fg=FG_DIM, width=20, anchor="w")
            status_lbl.pack(side="left")

            link_label, link_url = self._driver_vendor_link(provider)
            if link_label and link_url:
                import webbrowser
                lnk = tk.Label(row, text=link_label, font=("Segoe UI", 8, "underline"),
                                bg=row_bg, fg=ACCENT, cursor="hand2")
                lnk.pack(side="left")
                lnk.bind("<Button-1>", lambda e, u=link_url: webbrowser.open(u))

        if nvidia_versions:
            self._schedule_version_checks(nvidia_versions)

    def _schedule_version_checks(self, nvidia_entries):
        """Fetch the latest NVIDIA version once (cached per refresh) and update all status labels."""
        def worker():
            if self._drv_nvidia_latest is None:
                self._drv_nvidia_latest = self._fetch_nvidia_latest()
            latest = self._drv_nvidia_latest
            for installed_ver, lbl in nvidia_entries:
                if not lbl.winfo_exists():
                    continue
                converted = self._parse_nvidia_version(installed_ver)
                if latest is None:
                    text, color = "Check failed", FG_DIM
                elif converted is None:
                    text, color = "Unknown", FG_DIM
                elif converted == latest:
                    text, color = "Up to date", "#00cc66"
                else:
                    text, color = f"Update: v{latest}", "#ffaa00"
                lbl.after(0, lambda l=lbl, t=text, c=color: l.config(text=t, fg=c))

        threading.Thread(target=worker, daemon=True, name="NvidiaVerCheck").start()

    @staticmethod
    def _parse_nvidia_version(windows_ver: str):
        """Convert Windows driver version (e.g. 31.0.15.5585) to NVIDIA display version (555.85)."""
        try:
            parts = windows_ver.strip().split(".")
            if len(parts) != 4:
                return None
            p2, p3 = parts[2], parts[3]
            if len(p3) < 2:
                return None
            return f"{p2[-1]}{p3[:2]}.{p3[2:]}"
        except Exception:
            return None

    @staticmethod
    def _fetch_nvidia_latest():
        """Fetch the latest NVIDIA Game Ready Driver version string, or None on failure."""
        try:
            import urllib.request, json as _json
            url = (
                "https://gfwsl.geforce.com/services_toolkit/services/com/nvidia/services/"
                "AjaxDriverService.php?func=DriverManualLookup&pfid=929&osID=57"
                "&languageCode=1033&isWHQL=1&isMobile=1&isION=0&isQuadro=0&dch=1"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "AlienCore/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read().decode())
            ids = data.get("IDS") or []
            if ids:
                return ids[0].get("downloadInfo", {}).get("Version")
        except Exception:
            pass
        return None

    def _drivers_refresh(self):
        if self._drv_rows_frame and self._drv_rows_frame.winfo_exists():
            self._drv_rows_frame.destroy()
            self._drv_rows_frame = None
        self._drv_nvidia_latest = None   # force re-fetch on next render
        self._drv_status_labels = {}
        ldr = tk.Label(self._drv_tab, text="Loading drivers...",
                       font=("Segoe UI", 9, "italic"), bg=BG_SECT, fg=FG_DIM)
        ldr.pack(anchor="w", padx=20, pady=8)
        self._drv_loader = ldr
        self._drv_all = []
        threading.Thread(target=self._load_drivers, daemon=True).start()

    def _driver_vendor_link(self, provider: str):
        p = (provider or "").lower()
        for key, (label, url) in self._VENDOR_LINKS.items():
            if key in p:
                return label, url
        return None, None

    # ─────────────────────────────────────────────────────────────────────────
    # Feature gate helper
    # ─────────────────────────────────────────────────────────────────────────

    def _gate(self, parent, feature: str, builder_fn):
        """
        Wrap a panel builder with a license check.
        If the feature is allowed, call builder_fn(parent).
        Otherwise show a compact 'locked' notice with a purchase shortcut.
        """
        from core import license as lic
        ok, reason = lic.check(feature)
        if ok:
            builder_fn(parent)
            return

        import webbrowser, urllib.parse
        from core.constants import PAYPAL_BUSINESS_EMAIL, BACKEND_URL
        from core import auth

        is_pro_feature = feature in lic.PRO_FEATURES

        lock = tk.Frame(parent, bg=BG_HW, padx=20, pady=14)
        lock.pack(fill="x", padx=16, pady=(4, 8))

        header_row = tk.Frame(lock, bg=BG_HW)
        header_row.pack(fill="x")
        badge_text  = " PRO " if is_pro_feature else " BASE "
        badge_color = "#cc44ff" if is_pro_feature else ACCENT2
        badge = tk.Label(header_row, text=badge_text,
                         font=("Segoe UI", 7, "bold"),
                         bg=badge_color, fg="#000000", padx=3, pady=1)
        badge.pack(side="left")
        tk.Label(header_row, text=f"  {reason}",
                 font=("Segoe UI", 9, "italic"), bg=BG_HW, fg=FG_DIM,
                 wraplength=700, justify="left").pack(side="left")

        btn_row = tk.Frame(lock, bg=BG_HW)
        btn_row.pack(anchor="w", pady=(8, 0))

        def _open_purchase(item_number, item_name, amount):
            email = auth.get_email()
            params = urllib.parse.urlencode({
                "cmd":           "_xclick",
                "business":      PAYPAL_BUSINESS_EMAIL,
                "item_name":     item_name,
                "item_number":   item_number,
                "amount":        amount,
                "currency_code": "USD",
                "custom":        email,
                "notify_url":    BACKEND_URL.rstrip("/") + "/paypal/ipn",
            })
            webbrowser.open(f"https://www.paypal.com/cgi-bin/webscr?{params}")

        if not auth.is_logged_in():
            self._btn(btn_row, "Sign In", self._open_login, ACCENT,
                      bold=True).pack(side="left", padx=(0, 8))
        elif not auth.is_licensed():
            self._btn(btn_row, "Buy AlienCore  $19.99",
                      lambda: _open_purchase("AC_BASE",
                                             "AlienCore — Lifetime License", "19.99"),
                      ACCENT2, bold=True).pack(side="left", padx=(0, 8))
        elif is_pro_feature and not auth.is_pro():
            self._btn(btn_row, "Upgrade to Pro  +$4.99",
                      lambda: _open_purchase("AC_PRO",
                                             "AlienCore Pro Add-on", "4.99"),
                      "#cc44ff", bold=True).pack(side="left", padx=(0, 8))

        refresh_lbl = tk.Label(btn_row, text="", font=("Segoe UI", 8, "italic"),
                               bg=BG_HW, fg=FG_DIM)
        refresh_lbl.pack(side="left", padx=8)

        def _refresh_lic():
            refresh_lbl.config(text="Checking...", fg=FG_DIM)
            def _work():
                ok_r, msg = auth.refresh_license()
                lock.after(0, lambda: refresh_lbl.config(
                    text=msg, fg=ACCENT2 if ok_r else WARN))
            threading.Thread(target=_work, daemon=True).start()

        self._btn(btn_row, "Refresh License", _refresh_lic,
                  FG_DIM).pack(side="left")

    def _open_login(self):
        """Open the login dialog from within settings."""
        from gui.login_dialog import show as show_login
        threading.Thread(target=show_login, daemon=True,
                         name="LoginFromSettings").start()

    # ─────────────────────────────────────────────────────────────────────────
    # Account tab
    # ─────────────────────────────────────────────────────────────────────────

    def _tab_account(self):
        import webbrowser, urllib.parse
        from core import auth
        from core.constants import PAYPAL_BUSINESS_EMAIL, BACKEND_URL

        t = self._make_tab("Account")

        # ── Login status ──────────────────────────────────────────────────────
        self._section(t, "Account")

        status_panel = tk.Frame(t, bg=BG_HW, padx=24, pady=18)
        status_panel.pack(fill="x", padx=16, pady=(4, 8))

        email_lbl   = tk.Label(status_panel, text="—",
                                font=("Segoe UI", 13, "bold"),
                                bg=BG_HW, fg=FG_HEAD)
        email_lbl.pack(anchor="w")
        tier_lbl    = tk.Label(status_panel, text="",
                                font=("Segoe UI", 9),
                                bg=BG_HW, fg=FG_DIM)
        tier_lbl.pack(anchor="w", pady=(2, 10))
        status_msg  = tk.StringVar(value="")
        status_feed = tk.Label(status_panel, textvariable=status_msg,
                                font=("Segoe UI", 8, "italic"),
                                bg=BG_HW, fg=FG_DIM, anchor="w", wraplength=800)
        status_feed.pack(anchor="w", pady=(0, 8))

        def _refresh_display():
            if auth.is_logged_in():
                email_lbl.config(text=auth.get_email(), fg=FG_HEAD)
                s = auth.get_session()
                parts = []
                if s.get("has_base"): parts.append("Base License")
                if s.get("has_pro"):  parts.append("Pro Add-on")
                creds = s.get("support_credits", 0)
                if creds:             parts.append(f"{creds}x Priority Support")
                if parts:
                    tier_lbl.config(text="Licensed: " + " · ".join(parts),
                                    fg=ACCENT2)
                elif auth.is_on_trial():
                    days = auth.trial_days_left()
                    tier_lbl.config(
                        text=f"Free trial active — {days} day(s) remaining  "
                             f"(base features only, Pro features grayed out)",
                        fg=WARN)
                else:
                    tier_lbl.config(
                        text="No active license  —  trial expired or purchase below.",
                        fg=WARN)
            else:
                email_lbl.config(text="Not signed in", fg=FG_DIM)
                tier_lbl.config(text="", fg=FG_DIM)

        _refresh_display()

        # Action buttons row
        act_row = tk.Frame(status_panel, bg=BG_HW)
        act_row.pack(anchor="w")

        def _do_refresh():
            status_msg.set("Refreshing license...")
            def _work():
                ok, msg = auth.refresh_license()
                status_panel.after(0, lambda: (
                    status_msg.set(msg),
                    status_feed.config(fg=ACCENT2 if ok else WARN),
                    _refresh_display(),
                ))
            threading.Thread(target=_work, daemon=True).start()

        def _do_logout():
            auth.logout()
            _refresh_display()
            status_msg.set("Signed out.")

        if auth.is_logged_in():
            self._btn(act_row, "Refresh License", _do_refresh,
                      ACCENT, bold=True).pack(side="left", padx=(0, 8))
            self._btn(act_row, "Sign Out", _do_logout,
                      FG_DIM).pack(side="left")
        else:
            self._btn(act_row, "Sign In",
                      self._open_login, ACCENT, bold=True).pack(side="left")

        # ── Purchase / upgrade ────────────────────────────────────────────────
        self._section(t, "Licenses & Add-ons")
        self._note(t,
            "One-time payments. No subscriptions. Lifetime license includes "
            "all future updates. Enter your email in the sign-in field first — "
            "your email is attached to the payment so your license activates automatically.")

        def _paypal(item_number, item_name, amount):
            email = auth.get_email()
            params = urllib.parse.urlencode({
                "cmd":           "_xclick",
                "business":      PAYPAL_BUSINESS_EMAIL,
                "item_name":     item_name,
                "item_number":   item_number,
                "amount":        amount,
                "currency_code": "USD",
                "custom":        email,
                "notify_url":    BACKEND_URL.rstrip("/") + "/paypal/ipn",
            })
            webbrowser.open(f"https://www.paypal.com/cgi-bin/webscr?{params}")

        products = tk.Frame(t, bg=BG_HW, padx=24, pady=18)
        products.pack(fill="x", padx=16, pady=(4, 8))

        for item_number, label, price, desc, color in [
            ("AC_BASE",
             "AlienCore  —  Lifetime License",
             "$19.99",
             "Full access to all core features. One payment, yours forever.",
             ACCENT2),
            ("AC_PRO",
             "Pro Add-on  —  AI Integration",
             "+$4.99",
             "Unlocks AI Chat, AI Watchdog, and AI Config Advisor.",
             "#cc44ff"),
            ("AC_SUPPORT",
             "Priority Support  —  Single Incident",
             "$4.99",
             "24-hour response to one bug report or technical issue. "
             "Full refund if I can't resolve it.",
             WARN),
        ]:
            row = tk.Frame(products, bg=BG_SECT, padx=16, pady=10)
            row.pack(fill="x", pady=(0, 6))
            info = tk.Frame(row, bg=BG_SECT)
            info.pack(side="left", fill="x", expand=True)
            hdr = tk.Frame(info, bg=BG_SECT)
            hdr.pack(anchor="w")
            tk.Label(hdr, text=label, font=("Segoe UI", 10, "bold"),
                     bg=BG_SECT, fg=FG_HEAD).pack(side="left")
            tk.Label(hdr, text=f"  {price}", font=("Segoe UI", 10, "bold"),
                     bg=BG_SECT, fg=color).pack(side="left")
            tk.Label(info, text=desc, font=("Segoe UI", 8),
                     bg=BG_SECT, fg=FG_DIM, wraplength=600,
                     justify="left").pack(anchor="w", pady=(2, 0))
            _n, _p = item_number, price
            self._btn(row, "Purchase  \u2192",
                      lambda n=_n, l=label, p=_p: _paypal(
                          n, l, p.replace("+", "").replace("$", "").strip()),
                      color, bold=True).pack(side="right", padx=(12, 0))

        # ── Priority Support ticket form ───────────────────────────────────────
        self._section(t, "Submit a Priority Support Ticket")
        creds = auth.support_credits()
        if creds > 0:
            self._note(t,
                f"You have {creds} support credit(s). Describe your issue below. "
                f"Kyle will respond within 24 hours. If it can't be fixed, you'll receive a full refund.")

            ticket_panel = tk.Frame(t, bg=BG_HW, padx=16, pady=12)
            ticket_panel.pack(fill="x", padx=16, pady=(4, 8))

            msg_box = tk.Text(ticket_panel, height=6, width=80,
                              bg=BG_PANEL, fg=FG, insertbackground=FG,
                              relief="flat", font=("Segoe UI", 9),
                              padx=8, pady=6, wrap="word")
            msg_box.pack(fill="x")

            ticket_status = tk.StringVar(value="")
            tk.Label(ticket_panel, textvariable=ticket_status,
                     font=("Segoe UI", 8, "italic"),
                     bg=BG_HW, fg=FG_DIM, anchor="w").pack(fill="x", pady=(6, 0))

            ctrl = tk.Frame(ticket_panel, bg=BG_HW)
            ctrl.pack(anchor="w", pady=(8, 0))

            def _submit_ticket():
                message = msg_box.get("1.0", "end").strip()
                if not message:
                    ticket_status.set("Please describe your issue first.")
                    return
                ticket_status.set("Submitting...")
                def _work():
                    ok, resp = auth.submit_support_ticket(message)
                    ticket_panel.after(0, lambda: (
                        ticket_status.set(resp),
                        _refresh_display(),
                    ))
                threading.Thread(target=_work, daemon=True).start()

            self._btn(ctrl, "Submit Ticket", _submit_ticket,
                      ACCENT2, bold=True).pack(side="left")
        else:
            self._note(t,
                "No support credits. Purchase Priority Support above to submit a ticket.")

    # ─────────────────────────────────────────────────────────────────────────
    # About tab
    # ─────────────────────────────────────────────────────────────────────────

    def _tab_about(self):
        import webbrowser
        t = self._make_tab("About")

        # ── Logo / title block ────────────────────────────────────────────────
        hero = tk.Frame(t, bg=BG_HW, padx=30, pady=24)
        hero.pack(fill="x", padx=16, pady=(16, 8))

        tk.Label(hero, text=APP_NAME,
                 font=("Segoe UI", 32, "bold"),
                 bg=BG_HW, fg=ACCENT).pack(anchor="w")
        tk.Label(hero, text=f"Version {VERSION}",
                 font=("Segoe UI", 11),
                 bg=BG_HW, fg=FG_DIM).pack(anchor="w", pady=(0, 10))
        tk.Label(hero,
                 text=(
                     "A comprehensive adaptive system optimizer built for Windows. "
                     "Capabilities include, but are not limited to: real-time sensor "
                     "monitoring, GPU transparency tools, memory management, AI-assisted "
                     "tuning with multiple-choice suggestions, change PC settings using "
                     "natural language, reduce heat output on powerful high-end processors "
                     "without pinching performance, and much more.\n\n"
                     "This program was built on the Alienware M18 R2 laptop (2024) "
                     "(i9-14900HX; RTX 4090) and was designed to work with any Windows PC."
                 ),
                 font=("Segoe UI", 9),
                 bg=BG_HW, fg=FG, justify="left", wraplength=860).pack(anchor="w")

        # ── Author block ──────────────────────────────────────────────────────
        self._section(t, "Author")
        info = tk.Frame(t, bg=BG_HW, padx=30, pady=16)
        info.pack(fill="x", padx=16, pady=(4, 8))

        tk.Label(info, text="Kyle Yeroshefsky",
                 font=("Segoe UI", 13, "bold"),
                 bg=BG_HW, fg=FG_HEAD).pack(anchor="w")

        email_row = tk.Frame(info, bg=BG_HW)
        email_row.pack(anchor="w", pady=(4, 0))
        tk.Label(email_row, text="Email:",
                 font=("Segoe UI", 9), bg=BG_HW, fg=FG_DIM).pack(side="left")
        email_lnk = tk.Label(email_row, text=f"  {SUPPORT_EMAIL}",
                              font=("Segoe UI", 9, "underline"),
                              bg=BG_HW, fg=ACCENT, cursor="hand2")
        email_lnk.pack(side="left")
        email_lnk.bind("<Button-1>",
                       lambda e: webbrowser.open(f"mailto:{SUPPORT_EMAIL}"))

        gh_row = tk.Frame(info, bg=BG_HW)
        gh_row.pack(anchor="w", pady=(6, 0))
        tk.Label(gh_row, text="GitHub Issues / Bug Reports:",
                 font=("Segoe UI", 9), bg=BG_HW, fg=FG_DIM).pack(side="left")
        gh_url = GITHUB_ISSUES_URL.split("/issues")[0]   # repo root
        gh_lnk = tk.Label(gh_row, text=f"  {gh_url}",
                           font=("Segoe UI", 9, "underline"),
                           bg=BG_HW, fg=ACCENT, cursor="hand2")
        gh_lnk.pack(side="left")
        gh_lnk.bind("<Button-1>", lambda e: webbrowser.open(gh_url))

        btn_row = tk.Frame(info, bg=BG_HW)
        btn_row.pack(anchor="w", pady=(12, 0))
        self._btn(btn_row, "Send Feedback",
                  lambda: __import__("gui.feedback", fromlist=["feedback"]).open_feedback_thread(),
                  ACCENT2).pack(side="left", padx=(0, 8))
        self._btn(btn_row, "Open GitHub",
                  lambda: webbrowser.open(gh_url),
                  FG_DIM).pack(side="left")

        # ── Build info ────────────────────────────────────────────────────────
        self._section(t, "Build")
        build = tk.Frame(t, bg=BG_HW, padx=30, pady=14)
        build.pack(fill="x", padx=16, pady=(4, 8))

        import sys, platform
        hw = self._hw
        cpu_name  = hw.get("cpu", {}).get("name",  "Unknown CPU")
        gpu_list  = hw.get("gpu", [])
        gpu_name  = gpu_list[0].get("name", "Unknown GPU") if gpu_list else "Unknown GPU"
        ram_gb    = hw.get("ram", {}).get("total_gb", "?")
        os_name   = hw.get("platform", {}).get("os_edition", platform.version())

        for label, val in [
            ("Python",   sys.version.split()[0]),
            ("Platform", f"Windows  {os_name}"),
            ("CPU",      cpu_name),
            ("GPU",      gpu_name),
            ("RAM",      f"{ram_gb} GB"),
        ]:
            row = tk.Frame(build, bg=BG_HW)
            row.pack(anchor="w", pady=1)
            tk.Label(row, text=f"{label}:", font=("Segoe UI", 9),
                     bg=BG_HW, fg=FG_DIM, width=12, anchor="w").pack(side="left")
            tk.Label(row, text=val, font=("Segoe UI", 9),
                     bg=BG_HW, fg=FG).pack(side="left")

    # ── Services list ─────────────────────────────────────────────────────────

    def _load_services(self):
        try:
            from core import services_manager as sm
            svcs = sm.get_all_curated_states()
            self._svc_tab.after(0, lambda: self._populate_services(svcs))
        except Exception as e:
            self._svc_tab.after(0, lambda: self._svc_loader.config(text=f"Error: {e}"))

    def _populate_services(self, services):
        from core import services_manager as sm
        self._svc_loader.destroy()
        t = self._svc_tab

        managed  = [s for s in services if s["managed_by_aliencore"]]
        rest     = [s for s in services if not s["managed_by_aliencore"]]
        safe_s   = [s for s in rest if s["safety"] == sm.SAFE]
        caution_s= [s for s in rest if s["safety"] == sm.CAUTION]
        leave_s  = [s for s in rest if s["safety"] == sm.LEAVE]

        if managed:
            tk.Label(t, text="  Managed by AlienCore", font=("Segoe UI",8,"bold"),
                     bg=BG_SECT, fg=ACCENT).pack(anchor="w", padx=16, pady=(4,0))
            for s in managed: self._svc_row(t, s)

        tk.Frame(t, bg=SEP, height=1).pack(fill="x", padx=16, pady=6)
        if safe_s:
            tk.Label(t, text="  Safe to adjust", font=("Segoe UI",8,"bold"),
                     bg=BG_SECT, fg=ACCENT2).pack(anchor="w", padx=16, pady=(0,2))
            for s in safe_s: self._svc_row(t, s)
        if caution_s:
            tk.Label(t, text="  Adjust with caution", font=("Segoe UI",8,"bold"),
                     bg=BG_SECT, fg=WARN).pack(anchor="w", padx=16, pady=(8,2))
            for s in caution_s: self._svc_row(t, s)
        if leave_s:
            tk.Label(t, text="  Leave alone — system critical", font=("Segoe UI",8,"bold"),
                     bg=BG_SECT, fg=DANGER).pack(anchor="w", padx=16, pady=(8,2))
            for s in leave_s: self._svc_row(t, s)

    def _svc_row(self, parent, svc):
        from core import services_manager as sm
        if not svc["exists"]: return
        cur    = svc["startup_type"]
        rec    = svc["recommended"]
        safety = svc["safety"]
        if safety == sm.LEAVE:    row_bg, sfg = BG_PANEL, FG_DIM
        elif cur == rec:          row_bg, sfg = "#1a2a1a", ACCENT2
        elif safety == sm.CAUTION:row_bg, sfg = "#2a2510", WARN
        else:                     row_bg, sfg = "#2a1a1a", DANGER

        row = tk.Frame(parent, bg=row_bg, padx=16, pady=3)
        row.pack(fill="x", padx=16, pady=1)

        nl = tk.Label(row, text=svc["friendly"], font=("Segoe UI",9),
                      bg=row_bg, fg=FG, width=26, anchor="w")
        nl.pack(side="left")
        self._tooltip(nl, svc["description"])

        sc = ACCENT2 if svc["state"]=="Running" else FG_DIM
        tk.Label(row, text=svc["state"], font=("Segoe UI",8),
                 bg=row_bg, fg=sc, width=10, anchor="w").pack(side="left")

        sv = tk.StringVar(value=cur)
        opts = [sm.AUTO, sm.AUTO_DEL, sm.MANUAL, sm.DISABLED]
        dd = tk.OptionMenu(row, sv, *opts,
                           command=lambda v, n=svc["name"]:
                           threading.Thread(target=lambda: sm.set_startup_type(n,v), daemon=True).start())
        dd.config(bg=BG_PANEL, fg=FG, activebackground=BTN_HOV, activeforeground=FG,
                  font=("Segoe UI",8), relief="flat", width=14,
                  state="disabled" if safety==sm.LEAVE else "normal")
        dd["menu"].config(bg=BG_PANEL, fg=FG, font=("Segoe UI",8))
        dd.pack(side="left")

        tk.Label(row, text=rec, font=("Segoe UI",8),
                 bg=row_bg, fg=sfg, width=16, anchor="w").pack(side="left")

        bdefs = {sm.SAFE:("#003300",ACCENT2,"Safe"),
                 sm.CAUTION:("#332800",WARN,"Caution"),
                 sm.LEAVE:("#220000",DANGER,"Leave alone")}
        bbg,bfg,btxt = bdefs.get(safety,(BG_PANEL,FG_DIM,safety))
        tk.Label(row, text=btxt, font=("Segoe UI",7,"bold"),
                 bg=bbg, fg=bfg, padx=4, pady=1).pack(side="left", padx=4)

    def _apply_services(self):
        from core import services_manager as sm
        threading.Thread(target=lambda: sm.apply_all_recommended(), daemon=True).start()

    # ── Start with Windows panel ──────────────────────────────────────────────

    def _admin_panel(self, parent):
        """Admin-rights status + Restart as Admin button."""
        from core import elevation as _elev

        panel = tk.Frame(parent, bg=BG_HW, padx=16, pady=12)
        panel.pack(fill="x", padx=16, pady=(4, 8))

        is_admin = _elev.is_admin()

        status_row = tk.Frame(panel, bg=BG_HW)
        status_row.pack(fill="x", pady=(0, 8))
        tk.Label(status_row, text="Status:", font=("Segoe UI", 9, "bold"),
                 bg=BG_HW, fg=FG_DIM, width=16, anchor="w").pack(side="left")
        tk.Label(
            status_row,
            text=("Running as Administrator — all sensors available"
                  if is_admin else
                  "Not running as Administrator — CPU temp, DIMM, NVMe may show '---'"),
            font=("Segoe UI", 9), bg=BG_HW,
            fg=ACCENT2 if is_admin else WARN,
        ).pack(side="left")

        if not is_admin:
            btn_row = tk.Frame(panel, bg=BG_HW)
            btn_row.pack(fill="x")

            def _restart_as_admin():
                if _elev.relaunch_as_admin(extra_args=["--settings"]):
                    # New elevated settings window will open; close this one.
                    try:
                        self.root.destroy()
                    except Exception:
                        pass
                else:
                    messagebox.showwarning(
                        "Elevation declined",
                        "Windows did not grant admin rights.\n"
                        "Right-click AlienCore and choose 'Run as administrator' "
                        "to access CPU temp, DIMM, and NVMe sensors.",
                    )

            self._btn(btn_row, "Restart as Admin", _restart_as_admin,
                      ACCENT, bold=True).pack(side="left", padx=(0, 8))

        tk.Label(
            panel,
            text=("Admin rights are required for LibreHardwareMonitor to read "
                  "CPU Package temperature (MSR via WinRing0), SMBus DIMM "
                  "temperatures, and Alienware AWCC WMI controls."),
            font=("Segoe UI", 8), bg=BG_HW, fg=FG_DIM, justify="left",
            wraplength=760, anchor="w",
        ).pack(anchor="w", pady=(8, 0), fill="x")

    def _startup_panel(self, parent):
        from core import startup as _startup
        from core import elevation as _elev

        panel = tk.Frame(parent, bg=BG_HW, padx=16, pady=12)
        panel.pack(fill="x", padx=16, pady=(4, 8))

        # Status row
        status_row = tk.Frame(panel, bg=BG_HW)
        status_row.pack(fill="x", pady=(0, 8))

        tk.Label(status_row, text="Startup status:", font=("Segoe UI", 9, "bold"),
                 bg=BG_HW, fg=FG_DIM, width=16, anchor="w").pack(side="left")

        def _status_text():
            mode = _startup.startup_mode()
            if mode == "task":
                return ("Enabled — Task Scheduler (elevated, silent)", ACCENT2)
            if mode == "registry":
                return ("Enabled — HKCU Run (non-admin, limited sensors)", WARN)
            return ("Disabled — AlienCore will not auto-start", WARN)

        text, color = _status_text()
        self._startup_status_lbl = tk.Label(
            status_row, text=text, font=("Segoe UI", 9), bg=BG_HW, fg=color,
        )
        self._startup_status_lbl.pack(side="left")

        # Button row
        btn_row = tk.Frame(panel, bg=BG_HW)
        btn_row.pack(fill="x")

        def _refresh_status():
            t, c = _status_text()
            self._startup_status_lbl.config(text=t, fg=c)

        def _enable():
            ok = _startup.enable()
            cfg.set_value("service", "start_with_windows", value=True)
            cfg.save(cfg.get())
            if ok:
                _refresh_status()
            else:
                self._startup_status_lbl.config(
                    text="Failed to enable — check logs", fg=DANGER)

        def _disable():
            _startup.disable()
            cfg.set_value("service", "start_with_windows", value=False)
            cfg.save(cfg.get())
            _refresh_status()

        self._btn(btn_row, "Enable",  _enable,  ACCENT2, bold=True).pack(side="left", padx=(0, 8))
        self._btn(btn_row, "Disable", _disable, DANGER).pack(side="left")

        note = ("Running as admin: installs a Task Scheduler entry that launches "
                "silently with full permissions at logon — no UAC prompt, no '---' sensors.\n"
                "Not running as admin: falls back to HKCU\\...\\Run, which starts "
                "without admin rights. Sensors that need kernel access will show '---'.")
        tk.Label(panel, text=note, font=("Segoe UI", 8), bg=BG_HW, fg=FG_DIM,
                 justify="left", wraplength=760, anchor="w"
                 ).pack(anchor="w", pady=(8, 0), fill="x")

    # ── LHM bridge panel ──────────────────────────────────────────────────────

    def _lhm_panel(self, parent):
        """LHM bridge status — embedded sensor reader (no separate LHM app needed)."""
        from core import lhm_manager
        import threading as _threading

        panel = tk.Frame(parent, bg=BG_HW, padx=16, pady=10)
        panel.pack(fill="x", padx=16, pady=(0, 8))

        # Status row
        status_row = tk.Frame(panel, bg=BG_HW)
        status_row.pack(fill="x", pady=(0, 6))

        exe = lhm_manager.bridge_exe_path()
        found = bool(exe)
        status_text  = "Bridge ready" if found else "Bridge exe not found"
        status_color = ACCENT2 if found else WARN

        tk.Label(status_row, text="Status:", font=("Segoe UI", 9, "bold"),
                 bg=BG_HW, fg=FG_DIM, width=14, anchor="w").pack(side="left")
        self._lhm_status_lbl = tk.Label(status_row, text=status_text,
                                        font=("Segoe UI", 9), bg=BG_HW,
                                        fg=status_color)
        self._lhm_status_lbl.pack(side="left")

        # Test button — runs bridge and counts sensors returned
        def _test():
            self._lhm_status_lbl.config(text="Testing...", fg=FG_DIM)
            def _run():
                sensors = lhm_manager.get_sensors()
                if sensors:
                    msg   = f"OK — {len(sensors)} sensors read"
                    color = ACCENT2
                else:
                    msg   = "Failed — no sensors returned"
                    color = WARN
                self._lhm_status_lbl.config(text=msg, fg=color)
            _threading.Thread(target=_run, daemon=True).start()

        self._btn(status_row, "Test", _test, ACCENT).pack(side="left", padx=(12, 0))

        # Bridge exe path (informational)
        exe_row = tk.Frame(panel, bg=BG_HW)
        exe_row.pack(fill="x", pady=(4, 0))
        tk.Label(exe_row, text="Bridge exe:", font=("Segoe UI", 9),
                 bg=BG_HW, fg=FG_DIM, width=14, anchor="w").pack(side="left")
        tk.Label(exe_row, text=exe, font=("Consolas", 8),
                 bg=BG_HW, fg=FG_DIM if found else WARN, anchor="w",
                 wraplength=500, justify="left").pack(side="left")

        tk.Label(panel,
                 text="LibreHardwareMonitor is fully embedded — no separate install needed.",
                 font=("Segoe UI", 8), bg=BG_HW, fg=FG_DIM).pack(anchor="w", pady=(6, 0))

    # ── Hardware info panel ───────────────────────────────────────────────────

    def _hw_panel(self, parent, hw_type: str):
        hw = self._hw
        if not hw: return
        panel = tk.Frame(parent, bg=BG_HW, padx=16, pady=10)
        panel.pack(fill="x", padx=16, pady=(12,0))

        def item(lbl, val, color=ACCENT):
            r = tk.Frame(panel, bg=BG_HW); r.pack(anchor="w", pady=1)
            tk.Label(r, text=f"{lbl}:", font=("Segoe UI",8),
                     bg=BG_HW, fg=FG_DIM, width=20, anchor="w").pack(side="left")
            tk.Label(r, text=str(val), font=("Segoe UI",8,"bold"),
                     bg=BG_HW, fg=color, anchor="w").pack(side="left")

        if hw_type == "cpu":
            cpu = hw.get("cpu", {})
            tk.Label(panel, text="Detected CPU", font=("Segoe UI",9,"bold"),
                     bg=BG_HW, fg=FG_HEAD).pack(anchor="w", pady=(0,6))
            item("Model",          cpu.get("name","Unknown"))
            item("Physical cores", cpu.get("physical_cores","?"))
            item("Logical cores",  cpu.get("logical_cores","?"))
            item("Max frequency",  f"{cpu.get('max_freq_mhz',0):,} MHz")
            item("Family",         cpu.get("family","Unknown").upper())

        elif hw_type == "gpu":
            tk.Label(panel, text="Detected GPU(s)", font=("Segoe UI",9,"bold"),
                     bg=BG_HW, fg=FG_HEAD).pack(anchor="w", pady=(0,6))
            for gpu in hw.get("gpu", []):
                if gpu.get("is_integrated"): continue
                item("Model",      gpu.get("name","Unknown"))
                item("VRAM",       f"{gpu.get('vram_mb',0):,} MB")
                item("TDP",        f"{gpu.get('tdp_watts','?')} W" if gpu.get("tdp_watts") else "Unknown")
                item("Driver",     gpu.get("driver","Unknown"))
                item("nvidia-smi", "Yes" if gpu.get("nvidia_smi") else "No",
                     color=ACCENT2 if gpu.get("nvidia_smi") else WARN)

        elif hw_type == "ram":
            ram = hw.get("ram", {})
            tk.Label(panel, text="Detected RAM", font=("Segoe UI",9,"bold"),
                     bg=BG_HW, fg=FG_HEAD).pack(anchor="w", pady=(0,6))
            item("Total", f"{ram.get('total_gb','?')} GB")
            item("Sticks", len(ram.get("slots",[])))
            for i, slot in enumerate(ram.get("slots",[])):
                item(f"Slot {i+1}",
                     f"{slot.get('capacity_gb','?')} GB @ "
                     f"{slot.get('speed_mhz','?')} MHz "
                     f"({slot.get('manufacturer','?')})")

        elif hw_type == "drives":
            tk.Label(panel, text="Detected Drives", font=("Segoe UI",9,"bold"),
                     bg=BG_HW, fg=FG_HEAD).pack(anchor="w", pady=(0,6))
            _RAMDISK_NAMES = [
                "ramdisk", "ram disk", "starwind", "imdisk",
                "osfmount", "softperfect", "primo", "dataram", "filedisk",
            ]
            for d in hw.get("drives", []):
                name_lower = d.get("name", "").lower()
                if d.get("is_ramdisk") or any(kw in name_lower for kw in _RAMDISK_NAMES):
                    dtype = "RAM Disk"
                elif d.get("is_nvme"):
                    dtype = "NVMe"
                elif d.get("is_ssd"):
                    dtype = "SSD"
                else:
                    dtype = "HDD"
                item(dtype, f"{d.get('name','Unknown')}  —  {d.get('size_gb',0):.0f} GB")

        elif hw_type == "platform":
            plat = hw.get("platform", {})
            tk.Label(panel, text="System", font=("Segoe UI",9,"bold"),
                     bg=BG_HW, fg=FG_HEAD).pack(anchor="w", pady=(0,6))
            item("Form factor", "Laptop" if plat.get("is_laptop") else "Desktop")
            item("Alienware",   "Yes" if plat.get("is_alienware") else "No",
                 color=ACCENT2 if plat.get("is_alienware") else FG_DIM)
            # has_awcc_wmi is always False in the hardware profile (intentionally —
            # we don't call awcc.is_available() at scan time to avoid claiming the
            # COM STA connection on the wrong thread).  Read live status from sensors.
            try:
                from core import sensors as _sens
                _awcc_live = bool(_sens.get_readings().get("awcc_available", False))
            except Exception:
                _awcc_live = False
            awcc_status = ("WMI connected"        if _awcc_live
                           else "Installed (WMI offline)" if plat.get("has_awcc")
                           else "Not found")
            item("AWCC", awcc_status,
                 color=ACCENT2 if _awcc_live else
                       WARN    if plat.get("has_awcc") else FG_DIM)
            item("nvidia-smi",  "Available" if plat.get("has_nvidia_smi") else "Not found",
                 color=ACCENT2 if plat.get("has_nvidia_smi") else WARN)
            os_info = hw.get("os", {})
            os_release = os_info.get("release", "?")
            os_edition = os_info.get("edition", "")
            os_label = f"Windows {os_release} {os_edition}".strip()
            item("OS", os_label)

    # ── Widget helpers ────────────────────────────────────────────────────────

    def _build_tab(self, idx: int):
        """Construct the widgets for tab `idx` if not already built."""
        if not (0 <= idx < len(self._tab_defs)):
            return
        if self._tab_built[idx]:
            return
        self._tab_built[idx] = True
        self._lazy_idx = idx
        try:
            _, builder = self._tab_defs[idx]
            builder()
        finally:
            self._lazy_idx = None

    def _on_tab_changed(self, event=None):
        try:
            idx = self.nb.index(self.nb.select())
        except Exception:
            return
        self._build_tab(idx)

    def _prewarm_tabs(self, idx: int):
        """Build remaining tabs one per idle slice so the UI stays responsive."""
        if not getattr(self, "_tab_built", None):
            return
        if idx >= len(self._tab_defs):
            return
        self._build_tab(idx)
        self.root.after(40, lambda: self._prewarm_tabs(idx + 1))

    def _make_tab(self, label: str) -> tk.Frame:
        idx = getattr(self, "_lazy_idx", None)
        if idx is not None and idx < len(getattr(self, "_tab_frames", [])):
            outer = self._tab_frames[idx]
        else:
            outer = ttk.Frame(self.nb)
            self.nb.add(outer, text=f"  {label}  ")
        canvas = tk.Canvas(outer, bg=BG_SECT, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview,
                           style="Vertical.TScrollbar")
        inner = tk.Frame(canvas, bg=BG_SECT)
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0,0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        def _scroll(e):
            canvas.yview_scroll(int(-1*(e.delta/120)), "units")

        # Bind mousewheel when mouse enters canvas or inner frame
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _scroll))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        inner.bind("<Enter>",  lambda e: canvas.bind_all("<MouseWheel>", _scroll))
        inner.bind("<Leave>",  lambda e: canvas.unbind_all("<MouseWheel>"))

        return inner

    def _section(self, parent, title: str):
        tk.Frame(parent, bg=SEP, height=1).pack(fill="x", padx=16, pady=(14,0))
        tk.Label(parent, text=title, font=("Segoe UI",11,"bold"),
                 bg=BG_SECT, fg=ACCENT).pack(anchor="w", padx=20, pady=(6,3))

    def _note(self, parent, text: str):
        tk.Label(parent, text=text, font=("Segoe UI",8,"italic"),
                 bg=BG_SECT, fg=FG_DIM, wraplength=820, justify="left"
                 ).pack(anchor="w", padx=24, pady=(0,6))

    def _row_label(self, parent, text: str):
        tk.Label(parent, text=text, font=("Segoe UI",9),
                 bg=BG_SECT, fg=FG).pack(anchor="w", padx=20, pady=(8,2))

    def _var(self, key: str, var_type=bool):
        if key in self.vars:
            return self.vars[key]
        val = self._cfg_get(key)
        if var_type == bool:
            v = tk.BooleanVar(value=bool(val))
        elif var_type == str:
            v = tk.StringVar(value=str(val) if val is not None else "")
        elif var_type == float:
            v = tk.DoubleVar(value=float(val) if val is not None else 0.0)
        else:
            v = tk.IntVar(value=int(val) if val is not None else 0)
        self.vars[key] = v
        v.trace_add("write", self._mark_dirty)
        # Seed the dirty baseline for lazily-built tabs so newly created
        # vars (matching current config) aren't flagged as dirty.
        saved = getattr(self, "_saved_state", None)
        if saved is not None and key not in saved:
            saved[key] = v.get()
        return v

    def _cfg_get(self, dot_key: str):
        keys = dot_key.split(".")
        node = self.config
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return None
            node = node[k]
        return node

    def _cfg_set(self, dot_key: str, value):
        keys = dot_key.split(".")
        node = self.config
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value

    def _opt(self, parent, key: str, label: str, description: str = ""):
        var = self._var(key, bool)
        row = tk.Frame(parent, bg=BG_SECT, pady=2)
        row.pack(fill="x", padx=16, pady=1)
        tk.Checkbutton(row, variable=var, bg=BG_SECT, activebackground=BG_SECT,
                       selectcolor=BG_SECT, fg=ACCENT2, activeforeground=ACCENT2,
                       width=2).pack(side="left")
        tf = tk.Frame(row, bg=BG_SECT); tf.pack(side="left", fill="x", expand=True)
        tk.Label(tf, text=label, font=("Segoe UI",10),
                 bg=BG_SECT, fg=FG, anchor="w").pack(anchor="w")
        if description:
            tk.Label(tf, text=description, font=("Segoe UI",8),
                     bg=BG_SECT, fg=FG_DIM, anchor="w", wraplength=750,
                     justify="left").pack(anchor="w")

    def _slider(self, parent, key: str, label: str,
                mn, mx, step, fmt=None, note: str = ""):
        var = self._var(key, float)
        vl  = tk.StringVar(value=(fmt(var.get()) if fmt else str(var.get())))
        def _upd(v):
            snapped = round(float(v)/step)*step
            var.set(snapped)
            vl.set(fmt(snapped) if fmt else str(round(snapped,2)))
        row = tk.Frame(parent, bg=BG_SECT, pady=3)
        row.pack(fill="x", padx=20, pady=2)
        tk.Label(row, text=label, font=("Segoe UI",9),
                 bg=BG_SECT, fg=FG, width=38, anchor="w").pack(side="left")
        tk.Scale(row, from_=mn, to=mx, resolution=step, orient="horizontal",
                 variable=var, command=_upd, bg=BG_SECT, fg=FG,
                 troughcolor=BG_PANEL, highlightthickness=0, bd=0,
                 showvalue=False, length=240, sliderlength=16,
                 activebackground=ACCENT).pack(side="left", padx=6)
        tk.Label(row, textvariable=vl, font=("Segoe UI Semibold",9),
                 bg=BG_SECT, fg=ACCENT, width=14, anchor="w").pack(side="left")
        if note:
            self._note(parent, note)

    def _text_list(self, parent, key: str, label: str):
        val = self._cfg_get(key) or []
        tk.Label(parent, text=label, font=("Segoe UI",9),
                 bg=BG_SECT, fg=FG).pack(anchor="w", padx=20, pady=(8,2))
        txt = tk.Text(parent, height=4, font=("Consolas",9),
                      bg=BG_PANEL, fg=FG, insertbackground=FG,
                      relief="flat", padx=6, pady=4)
        txt.insert("1.0", "\n".join(val))
        txt.pack(fill="x", padx=20, pady=(0,4))
        if not hasattr(self, "_text_widgets"):
            self._text_widgets = {}
        self._text_widgets[key] = txt

    def _btn(self, parent, text, command, color=FG, bold=False):
        font = ("Segoe UI",10,"bold") if bold else ("Segoe UI",10)
        return tk.Button(parent, text=text, command=command,
                         font=font, fg=color, bg=BTN_BG,
                         activeforeground=color, activebackground=BTN_HOV,
                         relief="flat", padx=12, pady=5, cursor="hand2",
                         bd=0, highlightthickness=0)

    def _entry(self, parent, key: str, label: str, width: int = 42):
        """Single-line text entry bound to a dot-key config value."""
        var = self._var(key, str)
        row = tk.Frame(parent, bg=BG_SECT); row.pack(fill="x", padx=16, pady=(2, 4))
        tk.Label(row, text=label, font=("Segoe UI", 9), bg=BG_SECT,
                 fg=FG_DIM, width=24, anchor="w").pack(side="left")
        tk.Entry(row, textvariable=var, width=width,
                 bg="#1a1a1a", fg=FG, insertbackground=FG,
                 relief="flat", font=("Consolas", 9)).pack(side="left", ipady=3)

    def _tooltip(self, widget, text: str):
        tip = None
        def show(e):
            nonlocal tip
            tip = tk.Toplevel(widget)
            tip.overrideredirect(True)
            tip.attributes("-topmost", True)
            tk.Label(tip, text=text, font=("Segoe UI",8),
                     bg="#333333", fg=FG, padx=8, pady=4,
                     wraplength=400, justify="left").pack()
            tip.geometry(f"+{widget.winfo_rootx()+20}+{widget.winfo_rooty()+20}")
        def hide(e):
            nonlocal tip
            if tip: tip.destroy(); tip = None
        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    # ── Save / Cancel / Defaults ──────────────────────────────────────────────

    def _collect(self):
        for key, var in self.vars.items():
            raw = var.get()
            default = self._cfg_get(key)
            if isinstance(default, int) and isinstance(raw, float):
                raw = int(raw)
            elif isinstance(default, int) and isinstance(raw, str):
                try:
                    raw = int(raw)
                except ValueError:
                    pass
            self._cfg_set(key, raw)
        if hasattr(self, "_text_widgets"):
            for key, txt in self._text_widgets.items():
                lines = [l.strip() for l in txt.get("1.0","end").strip().splitlines() if l.strip()]
                self._cfg_set(key, lines)

    def _mark_dirty(self, *_):
        """Recompute dirty state and update the Save/Close button accordingly."""
        btn = getattr(self, "_save_btn", None)
        if not btn or not btn.winfo_exists():
            return
        saved = getattr(self, "_saved_state", {})
        vars_dirty = any(
            self.vars[k].get() != saved.get(k)
            for k in self.vars
        )
        theme_dirty = (
            (self._cfg_get("display.settings_theme") or "Void")
            != getattr(self, "_saved_theme", "Void")
        )
        if vars_dirty or theme_dirty:
            btn.config(text="  Save  ", command=self._save, fg=ACCENT2)
        else:
            btn.config(text="  Close  ", command=self._close, fg=FG_DIM)

    def _save(self):
        self._collect()
        cfg.save(self.config)
        cfg.set_value("first_run_complete", value=True)
        if self.on_save_callback:
            self.on_save_callback()
        logger.info("Settings saved.")
        if self.is_first_run:
            messagebox.showinfo("AlienCore", "Settings saved. AlienCore is initializing.")
            self.root.destroy()
            return
        # Refresh snapshot so dirty check compares against what was just saved
        self._saved_state = {k: v.get() for k, v in self.vars.items()}
        self._saved_theme = self._cfg_get("display.settings_theme") or "Void"
        self._save_btn.config(text="  Close  ", command=self._close, fg=FG_DIM)

    def _refresh_update_button(self):
        """Show or hide the footer 'Update Available' button based on updater state."""
        try:
            if not self.root.winfo_exists():
                return
            from core import updater as _upd
            info = _upd.get_update_info()
            should_show = info is not None and _upd.should_show_button()
            if should_show and self._update_foot_btn is None:
                def _open_update():
                    from gui import update_dialog
                    import threading as _t
                    _t.Thread(
                        target=update_dialog.show_standalone,
                        args=(info,),
                        name="UpdateDialog",
                        daemon=True,
                    ).start()
                self._update_foot_btn = self._btn(
                    self._update_foot_frame,
                    f"  Update Available  v{info['version']}  ",
                    _open_update,
                    "#FFAA00",
                    bold=True,
                )
                self._update_foot_btn.pack(side="left")
            elif not should_show and self._update_foot_btn is not None:
                self._update_foot_btn.destroy()
                self._update_foot_btn = None
        except Exception:
            pass

    def _close(self):
        self.root.destroy()

    def _open_manual(self):
        import webbrowser
        manual = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "docs", "manual.html",
        )
        webbrowser.open(f"file:///{manual.replace(os.sep, '/')}")

    def _cancel(self):
        if self.is_first_run:
            if not messagebox.askyesno("AlienCore", "Exit without saving? Default settings will be used."):
                return
            cfg.set_value("first_run_complete", value=True)
        self.root.destroy()

    def _defaults(self):
        if messagebox.askyesno("Restore Defaults", "Reset all settings to defaults?"):
            from core.constants import DEFAULT_CONFIG
            self.config = copy.deepcopy(DEFAULT_CONFIG)
            self.root.destroy()
            open_settings(on_save_callback=self.on_save_callback,
                          is_first_run=self.is_first_run)
