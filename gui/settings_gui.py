"""
AlienCore - settings_gui.py
Clean rewrite. Loads config fresh from disk on open.
Hardware profile cached once at init. No blocking disk reads per tab.
"""

import json
import os
import copy
import logging
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from core import config_manager as cfg
from core.constants import (
    APP_NAME, VERSION, HARDWARE_CACHE,
    COLOR_COOL, COLOR_WARM, COLOR_HOT,
)

logger = logging.getLogger("aliencore.gui")

# ── Palette ───────────────────────────────────────────────────────────────────
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
    import sys, os, time
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if base not in sys.path:
        sys.path.insert(0, base)

    # Small delay to ensure any in-flight disk writes have completed
    time.sleep(0.2)

    # Always load config fresh from disk
    cfg.load()

    root = tk.Tk()
    SettingsWindow(root, on_save_callback=on_save_callback,
                   is_first_run=is_first_run)
    root.mainloop()


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

        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure("TNotebook",     background=BG,       borderwidth=0)
        s.configure("TNotebook.Tab", background=BG_PANEL, foreground=FG_DIM,
                    padding=[12, 7], font=("Segoe UI", 9))
        s.map("TNotebook.Tab",
              background=[("selected", BG_SECT)],
              foreground=[("selected", FG_HEAD)])
        s.configure("TFrame",    background=BG)
        s.configure("Vertical.TScrollbar", background=BG_PANEL,
                    troughcolor=BG, borderwidth=0, arrowcolor=FG_DIM)

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

        self._tab_display()
        self._tab_cpu()
        self._tab_gpu()
        self._tab_ram()
        self._tab_visual()
        self._tab_network()
        self._tab_storage()
        self._tab_privacy()
        self._tab_profiles()
        self._tab_custom_profiles()
        self._tab_service()
        self._tab_thresholds()
        self._tab_ai()
        self._tab_insights()
        self._tab_drivers()

        # Footer
        tk.Frame(self.root, bg=SEP, height=1).pack(fill="x")
        foot = tk.Frame(self.root, bg=BG, pady=8)
        foot.pack(fill="x", padx=16)
        tk.Label(foot, text="Changes take effect immediately.",
                 font=("Segoe UI", 9), bg=BG, fg=FG_DIM).pack(side="left")
        self._btn(foot, "Cancel",           self._cancel,   FG_DIM).pack(side="right", padx=4)
        self._btn(foot, "Restore Defaults", self._defaults, WARN).pack(side="right", padx=4)
        self._btn(foot, "  Save  ",         self._save,     ACCENT2, bold=True).pack(side="right", padx=4)

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
        self._opt(t, "sensors.nvme_temp",   "NVMe / SSD temperature",       "Via LibreHardwareMonitor")
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
        self._opt(t, "sensors.net_io",      "Network throughput (MB/s)",    "Upload ↑ and download ↓ speeds")
        self._opt(t, "sensors.disk_io",     "Disk throughput (MB/s)",       "Read and write speeds")

        self._row_label(t, "CPU temperature mode")
        mv = self._var("sensors.cpu_temp_mode", str)
        rm = tk.Frame(t, bg=BG_SECT); rm.pack(fill="x", padx=20, pady=(0,8))
        for val, lbl in [("average","Average"),("per_core","Per-core")]:
            tk.Radiobutton(rm, text=lbl, variable=mv, value=val,
                           bg=BG_SECT, fg=FG, selectcolor=BG_SECT,
                           activebackground=BG_SECT, activeforeground=ACCENT,
                           font=("Segoe UI",9)).pack(side="left", padx=10)

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
        self._section(t, "NIC Adapter (applied under Optimal Decisions)")
        self._opt(t, "network.nic_interrupt_moderation", "Disable interrupt moderation",       "Lowest DPC latency — NIC interrupts CPU immediately instead of batching")
        self._opt(t, "network.nic_rss_tuning",           "RSS tuning",                         "Enables Receive Side Scaling and tunes queue count to P-core count")

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
            from gui.ai_chat import open_chat_thread
            open_chat_thread()

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
            capture_output=True, text=True, timeout=60
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

    def _startup_panel(self, parent):
        from core import startup as _startup

        panel = tk.Frame(parent, bg=BG_HW, padx=16, pady=12)
        panel.pack(fill="x", padx=16, pady=(4, 8))

        # Status row
        status_row = tk.Frame(panel, bg=BG_HW)
        status_row.pack(fill="x", pady=(0, 8))

        tk.Label(status_row, text="Registry status:", font=("Segoe UI", 9, "bold"),
                 bg=BG_HW, fg=FG_DIM, width=16, anchor="w").pack(side="left")

        enabled = _startup.is_enabled()
        self._startup_status_lbl = tk.Label(
            status_row,
            text="Enabled — AlienCore will launch at login" if enabled
                 else "Disabled — AlienCore will not auto-start",
            font=("Segoe UI", 9), bg=BG_HW,
            fg=ACCENT2 if enabled else WARN
        )
        self._startup_status_lbl.pack(side="left")

        # Button row
        btn_row = tk.Frame(panel, bg=BG_HW)
        btn_row.pack(fill="x")

        def _enable():
            ok = _startup.enable()
            cfg.set_value("service", "start_with_windows", value=True)
            cfg.save(cfg.get())
            if ok:
                self._startup_status_lbl.config(
                    text="Enabled — AlienCore will launch at login", fg=ACCENT2)
            else:
                self._startup_status_lbl.config(
                    text="Failed to write registry key — check permissions", fg=DANGER)

        def _disable():
            _startup.disable()
            cfg.set_value("service", "start_with_windows", value=False)
            cfg.save(cfg.get())
            self._startup_status_lbl.config(
                text="Disabled — AlienCore will not auto-start", fg=WARN)

        self._btn(btn_row, "Enable",  _enable,  ACCENT2, bold=True).pack(side="left", padx=(0, 8))
        self._btn(btn_row, "Disable", _disable, DANGER).pack(side="left")

        tk.Label(panel,
                 text="Uses HKCU\\...\\Run — no admin rights required. "
                      "Points to launch.vbs (silent, no console window).",
                 font=("Segoe UI", 8), bg=BG_HW, fg=FG_DIM
                 ).pack(anchor="w", pady=(8, 0))

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
        found = bool(lhm_manager._bridge_path())
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
            awcc_status = ("WMI connected" if plat.get("has_awcc_wmi")
                           else "Installed (WMI offline)" if plat.get("has_awcc")
                           else "Not found")
            item("AWCC", awcc_status,
                 color=ACCENT2 if plat.get("has_awcc_wmi") else
                       WARN    if plat.get("has_awcc")     else FG_DIM)
            item("nvidia-smi",  "Available" if plat.get("has_nvidia_smi") else "Not found",
                 color=ACCENT2 if plat.get("has_nvidia_smi") else WARN)
            item("OS",          f"Windows {hw.get('os',{}).get('release','?')}")

    # ── Widget helpers ────────────────────────────────────────────────────────

    def _make_tab(self, label: str) -> tk.Frame:
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

    def _save(self):
        self._collect()
        # Debug — log all sensor values before saving
        logger.info("DEBUG sensors at save: %s", {
            k: v for k, v in self.config.get("sensors", {}).items()
        })
        logger.info("DEBUG vars keys: %s", [k for k in self.vars.keys() if "sensor" in k])
        cfg.save(self.config)
        cfg.set_value("first_run_complete", value=True)
        if self.on_save_callback:
            self.on_save_callback()
        logger.info("Settings saved.")
        if self.is_first_run:
            messagebox.showinfo("AlienCore", "Settings saved. AlienCore is initializing.")
        self.root.destroy()

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
