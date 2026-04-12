"""
AlienCore - bar.py
Slim always-on-top sensor bar — floats anywhere on screen, draggable.
Shows all enabled sensors in a horizontal row with colored values.

Polish:
  - DWM rounded corners (Windows 11)
  - Colored profile dot (Canvas) + dim text label
  - Hover tooltip per sensor
  - Double-click sparkline popup (60-sample history)
  - Threshold flash on warn/crit transition
  - Fullscreen auto-hide
"""

import tkinter as tk
import threading
import logging
import collections
import ctypes
from core import config_manager as cfg, sensors, profiles
from core.constants import COLOR_COOL, COLOR_WARM, COLOR_HOT

logger = logging.getLogger("aliencore.bar")

# ── Size presets ──────────────────────────────────────────────────────────────
BAR_SIZES = {
    "Small":  {"label": 8,  "value": 11, "sep": 9,  "pad_x": 4,  "pad_y": 2},
    "Medium": {"label": 9,  "value": 13, "sep": 11, "pad_x": 6,  "pad_y": 3},
    "Large":  {"label": 10, "value": 16, "sep": 13, "pad_x": 8,  "pad_y": 4},
    "XL":     {"label": 12, "value": 20, "sep": 15, "pad_x": 10, "pad_y": 6},
}
DEFAULT_SIZE = "Medium"
BG         = "#0d0d0d"
FG_LABEL   = "#ffffff"
FG_DIM     = "#666666"
BORDER     = "#1a1f2e"
FONT_LABEL = ("Consolas", 9)
FONT_VALUE = ("Consolas", 13, "bold")
FONT_SEP   = ("Consolas", 11)

_PROFILE_COLORS = {
    "idle":      "#1a4a6e",
    "gaming":    "#cc2200",
    "streaming": "#0066cc",
    "manual":    "#886600",
}

_instance = None


# ─────────────────────────────────────────────────────────────────────────────
# Tooltip
# ─────────────────────────────────────────────────────────────────────────────

class _Tooltip:
    """Simple hover tooltip shown below a widget."""
    def __init__(self, widget, text_fn):
        self._widget  = widget
        self._text_fn = text_fn   # callable returning tooltip string
        self._tip     = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, event=None):
        if self._tip:
            return
        x = self._widget.winfo_rootx() + 10
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._tip = tw = tk.Toplevel(self._widget)
        tw.overrideredirect(True)
        tw.attributes("-topmost", True)
        tw.geometry(f"+{x}+{y}")
        tk.Label(tw, text=self._text_fn(),
                 font=("Segoe UI", 8),
                 bg="#1e2230", fg="#cccccc",
                 relief="flat", padx=6, pady=3).pack()

    def _hide(self, event=None):
        if self._tip:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


# ─────────────────────────────────────────────────────────────────────────────
# Sparkline popup
# ─────────────────────────────────────────────────────────────────────────────

class _Sparkline:
    _open = {}   # config_key → Toplevel

    @classmethod
    def show(cls, config_key: str, label: str,
             history: collections.deque, unit: str):
        if config_key in cls._open:
            try:
                cls._open[config_key].destroy()
            except Exception:
                pass
        win = tk.Toplevel()
        win.title(f"{label} — last {len(history)} samples")
        win.attributes("-topmost", True)
        win.configure(bg="#0d0d0d")
        win.resizable(False, False)
        cls._open[config_key] = win

        W, H = 320, 110
        canvas = tk.Canvas(win, width=W, height=H, bg="#0d0d0d",
                           highlightthickness=0)
        canvas.pack(padx=8, pady=8)

        vals = [v for v in history if v is not None]
        if len(vals) < 2:
            canvas.create_text(W // 2, H // 2, text="Not enough data yet",
                               fill="#555555", font=("Consolas", 9))
        else:
            mn, mx = min(vals), max(vals)
            span   = max(mx - mn, 1)
            pts    = []
            n      = len(vals)
            for i, v in enumerate(vals):
                x = int(i * (W - 24) / max(n - 1, 1)) + 12
                y = H - 12 - int((v - mn) / span * (H - 24))
                pts.append((x, y))
            # Draw lines
            for i in range(len(pts) - 1):
                canvas.create_line(pts[i][0], pts[i][1],
                                   pts[i+1][0], pts[i+1][1],
                                   fill=COLOR_COOL, width=1)
            # Min / max / current labels
            canvas.create_text(12, H - 4, text=f"{mn:.1f}{unit}",
                               anchor="sw", fill="#555555", font=("Consolas", 7))
            canvas.create_text(12, 4, text=f"{mx:.1f}{unit}",
                               anchor="nw", fill="#555555", font=("Consolas", 7))
            canvas.create_text(W - 4, 6, text=f"{vals[-1]:.1f}{unit}",
                               anchor="ne", fill="#ffffff", font=("Consolas", 8))

        win.protocol("WM_DELETE_WINDOW", lambda: cls._close(config_key))

    @classmethod
    def _close(cls, key):
        if key in cls._open:
            try:
                cls._open[key].destroy()
            except Exception:
                pass
            del cls._open[key]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def start():
    global _instance
    try:
        _instance = SensorBar()
        _instance.run()
    except Exception as e:
        logger.error("Sensor bar error: %s", e)


def stop():
    global _instance
    if _instance:
        try:
            _instance.root.destroy()
        except Exception:
            pass
        _instance = None


def is_visible() -> bool:
    return _instance is not None


# ─────────────────────────────────────────────────────────────────────────────
# Sensor bar window
# ─────────────────────────────────────────────────────────────────────────────

class SensorBar:

    # All sensor config keys in default display order
    _SENSOR_DEFS = [
        "cpu_temp", "gpu_temp", "gpu_hotspot", "gpu_mem_temp",
        "nvme_temp", "fan_rpm",
        "ram_usage", "cpu_load", "gpu_load", "gpu_vram",
        "cpu_watts", "gpu_watts",
        "gpu_fan", "cpu_freq", "gpu_clock",
        "battery", "net_io", "disk_io",
    ]

    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.92)
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        self._drag_x    = 0
        self._drag_y    = 0
        self._cells     = {}
        self._history   = {}    # config_key → deque(maxlen=60)
        self._flash     = {}    # config_key → last color str
        self._fs_hidden = False
        self._gpu_warn_w, self._gpu_hot_w = self._read_gpu_tdp_thresholds()

        saved_size = cfg.get_value("display", "bar_size", default=DEFAULT_SIZE)
        self._size  = saved_size if saved_size in BAR_SIZES else DEFAULT_SIZE
        self._apply_size_fonts()

        self._orient      = cfg.get_value("display", "bar_orientation", default="horizontal")
        self._last_orient = self._orient

        self._build()
        self._restore_position()
        self._apply_rounded_corners()

        if cfg.get_value("display", "bar_hidden", default=False):
            self.root.withdraw()
        self._update()

    # ── DWM rounded corners ───────────────────────────────────────────────────

    def _apply_rounded_corners(self):
        try:
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWCP_ROUND = 2
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if not hwnd:
                hwnd = int(self.root.winfo_id())
            val = ctypes.c_int(DWMWCP_ROUND)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(val), ctypes.sizeof(val)
            )
        except Exception:
            pass

    # ── Fonts ─────────────────────────────────────────────────────────────────

    def _apply_size_fonts(self):
        global FONT_LABEL, FONT_VALUE, FONT_SEP
        s = BAR_SIZES[self._size]
        FONT_LABEL = ("Consolas", s["label"])
        FONT_VALUE = ("Consolas", s["value"], "bold")
        FONT_SEP   = ("Consolas", s["sep"])

    # ── Orientation helpers ───────────────────────────────────────────────────

    def _pack_side(self) -> str:
        return "top" if self._orient == "vertical" else "left"

    def _make_separator(self, parent) -> tk.Widget:
        """Return a separator widget appropriate for the current orientation."""
        if self._orient == "vertical":
            w = tk.Frame(parent, height=1, bg="#222222")
            w.pack(side="top", fill="x", pady=2)
        else:
            w = tk.Label(parent, text="|", font=FONT_SEP, bg=BG, fg="#222222")
            w.pack(side="left", padx=3)
        return w

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self):
        s = BAR_SIZES[self._size]
        outer = tk.Frame(self.root, bg=BORDER, padx=1, pady=1)
        outer.pack(fill="both", expand=True)

        self.inner = tk.Frame(outer, bg=BG,
                              padx=s["pad_x"], pady=s["pad_y"])
        self.inner.pack(fill="both", expand=True)

        for w in [self.root, outer, self.inner]:
            w.bind("<ButtonPress-1>",   self._drag_start)
            w.bind("<B1-Motion>",       self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)
            w.bind("<Button-3>",        self._show_context_menu)

        # Drag handle
        grip = tk.Label(self.inner, text="⠿", font=("Consolas", 10),
                        bg=BG, fg="#333333", cursor="fleur", padx=4)
        grip.pack(side=self._pack_side())
        grip.bind("<ButtonPress-1>",   self._drag_start)
        grip.bind("<B1-Motion>",       self._drag_move)
        grip.bind("<ButtonRelease-1>", self._drag_end)
        grip.bind("<Button-3>",        self._show_context_menu)

        # Profile dot (colored canvas circle)
        dot_sz = BAR_SIZES[self._size]["label"] + 4
        self.profile_dot = tk.Canvas(
            self.inner, width=dot_sz, height=dot_sz,
            bg=BG, highlightthickness=0, cursor="fleur"
        )
        pad_kw = {"pady": (2, 0)} if self._orient == "vertical" else {"padx": (2, 0)}
        self.profile_dot.pack(side=self._pack_side(), **pad_kw)
        self._dot_oval = self.profile_dot.create_oval(
            2, 2, dot_sz - 2, dot_sz - 2, fill="#1a4a6e", outline=""
        )

        # Profile text (dim, next to dot)
        self.profile_label = tk.Label(
            self.inner, text="IDLE", font=("Consolas", 7, "bold"),
            bg=BG, fg="#444444", padx=2, cursor="fleur"
        )
        self.profile_label.pack(side=self._pack_side())

        for w in [self.profile_dot, self.profile_label]:
            w.bind("<ButtonPress-1>",   self._drag_start)
            w.bind("<B1-Motion>",       self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)
            w.bind("<Button-3>",        self._show_context_menu)

        sep = self._make_separator(self.inner)
        sep.bind("<ButtonPress-1>",   self._drag_start)
        sep.bind("<B1-Motion>",       self._drag_move)
        sep.bind("<ButtonRelease-1>", self._drag_end)

        self._build_sensor_cells()

    # ── Sensor cells ──────────────────────────────────────────────────────────

    def _build_sensor_cells(self):
        self._cells        = {}
        self._cell_widgets = []
        self._last_enabled = None
        sens = cfg.get().get("sensors", {})
        self._rebuild_sensor_cells(sens)

    def _get_sensor_order(self, sens: dict) -> list:
        default_order = list(self._SENSOR_DEFS)
        saved_order = cfg.get_value("display", "sensor_order", default=None)
        if saved_order:
            ordered = [k for k in saved_order if k in default_order]
            for k in default_order:
                if k not in ordered:
                    ordered.append(k)
        else:
            ordered = default_order
        return [k for k in ordered if sens.get(k, False)]

    def _move_sensor(self, key: str, direction: int):
        sens  = cfg.get().get("sensors", {})
        order = self._get_sensor_order(sens)
        if key not in order:
            return
        idx     = order.index(key)
        new_idx = max(0, min(len(order) - 1, idx + direction))
        if new_idx == idx:
            return
        order.pop(idx)
        order.insert(new_idx, key)
        cfg.set_value("display", "sensor_order", value=order)
        self._last_enabled = None   # force rebuild

    def _rebuild_sensor_cells(self, sens: dict):
        for w in getattr(self, "_cell_widgets", []):
            try:
                w.destroy()
            except Exception:
                pass
        self._cell_widgets = []
        self._cells        = {}

        sensor_map = {
            "cpu_temp":    ("CPU",  self._get_cpu,          "°"),
            "gpu_temp":    ("GPU",  self._get_gpu,          "°"),
            "gpu_hotspot": ("GHOT", self._get_gpu_hotspot,  "°"),
            "gpu_mem_temp":("GMEM", self._get_gpu_mem_temp, "°"),
            "nvme_temp":   ("NVM",  self._get_nvme,         "°"),
            "fan_rpm":     ("FAN",  self._get_fan_or_dimm,  "rpm"),
            "ram_usage":   ("RAM",  self._get_ram,          "%"),
            "cpu_load":    ("CPU%", self._get_cpu_load,     "%"),
            "gpu_load":    ("GPU%", self._get_gpu_load,     "%"),
            "gpu_vram":    ("VRAM", self._get_vram,         "%"),
            "cpu_watts":   ("CPUW", self._get_cpu_watts,    "W"),
            "gpu_watts":   ("GPUW", self._get_gpu_watts,    "W"),
            "gpu_fan":     ("GFAN", self._get_gpu_fan,      "%"),
            "cpu_freq":    ("CFRQ", self._get_cpu_freq,     "GHz"),
            "gpu_clock":   ("GCLK", self._get_gpu_clock,   "MHz"),
            "battery":     ("BAT",  self._get_battery,      "%"),
            "net_io":      ("NET",  self._get_net_io,       "MB/s"),
            "disk_io":     ("DISK", self._get_disk_io,      "MB/s"),
        }

        ordered = self._get_sensor_order(sens)
        first   = True
        for config_key in ordered:
            if config_key not in sensor_map:
                continue
            label, getter, unit = sensor_map[config_key]

            if not first:
                sep = self._make_separator(self.inner)
                self._cell_widgets.append(sep)
            first = False

            cell = tk.Frame(self.inner, bg=BG, padx=2)
            cell.pack(side=self._pack_side())
            self._cell_widgets.append(cell)

            lbl = tk.Label(cell, text=label, font=FONT_LABEL,
                           bg=BG, fg=FG_LABEL, cursor="fleur")
            lbl.pack(anchor="w")
            lbl.bind("<ButtonPress-1>",   self._drag_start)
            lbl.bind("<B1-Motion>",       self._drag_move)
            lbl.bind("<ButtonRelease-1>", self._drag_end)
            lbl.bind("<Button-3>",
                     lambda e, k=config_key: self._show_sensor_menu(e, k))

            val = tk.Label(cell, text="---", font=FONT_VALUE,
                           bg=BG, fg=COLOR_COOL, cursor="fleur")
            val.pack(anchor="w")
            val.bind("<ButtonPress-1>",   self._drag_start)
            val.bind("<B1-Motion>",       self._drag_move)
            val.bind("<ButtonRelease-1>", self._drag_end)
            val.bind("<Button-3>",
                     lambda e, k=config_key: self._show_sensor_menu(e, k))
            val.bind("<Double-Button-1>",
                     lambda e, k=config_key, lb=label, u=unit:
                         self._open_sparkline(k, lb, u))

            cell.bind("<ButtonPress-1>",   self._drag_start)
            cell.bind("<B1-Motion>",       self._drag_move)
            cell.bind("<ButtonRelease-1>", self._drag_end)
            cell.bind("<Button-3>",
                      lambda e, k=config_key: self._show_sensor_menu(e, k))

            # Tooltip on both label and value
            _Tooltip(lbl, lambda lb=label, k=config_key: self._tooltip_text(lb, k))
            _Tooltip(val, lambda lb=label, k=config_key: self._tooltip_text(lb, k))

            self._cells[config_key] = {
                "value":        val,
                "label_widget": lbl,
                "getter":       getter,
                "unit":         unit,
            }
            if config_key not in self._history:
                self._history[config_key] = collections.deque(maxlen=60)

    def _tooltip_text(self, label: str, config_key: str) -> str:
        hist = self._history.get(config_key)
        if hist:
            vals = [v for v in hist if v is not None]
            if vals:
                unit = self._cells.get(config_key, {}).get("unit", "")
                return f"{label}  {vals[-1]:.1f} {unit}"
        return label

    def _open_sparkline(self, config_key: str, label: str, unit: str):
        hist = self._history.get(config_key, collections.deque())
        _Sparkline.show(config_key, label, hist, unit)

    def _set_orientation(self, orient: str):
        cfg.set_value("display", "bar_orientation", value=orient)
        # _update loop will detect the change and rebuild

    def _show_sensor_menu(self, event, key: str):
        menu = tk.Menu(self.root, tearoff=0,
                       bg="#1a1a1a", fg="#cccccc",
                       activebackground="#333333",
                       activeforeground="#ffffff",
                       font=("Segoe UI", 9))
        if self._orient == "vertical":
            menu.add_command(label="▲ Move Up",
                             command=lambda: self._move_sensor(key, -1))
            menu.add_command(label="▼ Move Down",
                             command=lambda: self._move_sensor(key, 1))
        else:
            menu.add_command(label="◀ Move Left",
                             command=lambda: self._move_sensor(key, -1))
            menu.add_command(label="▶ Move Right",
                             command=lambda: self._move_sensor(key, 1))
        menu.add_separator()
        try:
            from core import turbo_cool
            if turbo_cool.is_active():
                menu.add_command(label="⚡ Turbo Cool: ON — disable",
                                 foreground="#00ffff",
                                 command=turbo_cool.deactivate)
            else:
                menu.add_command(label="⚡ Turbo Cool: OFF — enable",
                                 command=turbo_cool.activate)
        except Exception:
            pass
        menu.add_separator()
        menu.add_command(label="Open Settings", command=self._open_settings)
        menu.add_separator()
        menu.add_command(label="Hide Bar", command=self._hide_bar)
        menu.tk_popup(event.x_root, event.y_root)

    def _hide_bar(self):
        cfg.set_value("display", "bar_hidden", value=True)
        self.root.withdraw()

    def _show_bar(self):
        cfg.set_value("display", "bar_hidden", value=False)
        self.root.deiconify()

    # ── Sensor getters ────────────────────────────────────────────────────────

    def _get_cpu(self, readings, thresh):
        val = readings.get("cpu_temp_avg")
        if val is None:
            return "---", COLOR_COOL
        return sensors.fmt_temp(val), self._temp_color(
            val, thresh.get("cpu_warn", 80), thresh.get("cpu_crit", 95))

    def _get_gpu(self, readings, thresh):
        val = readings.get("gpu_temp")
        if val is None:
            return "---", COLOR_COOL
        return sensors.fmt_temp(val), self._temp_color(
            val, thresh.get("gpu_warn", 80), thresh.get("gpu_crit", 90))

    def _get_gpu_hotspot(self, readings, thresh):
        val = readings.get("gpu_temp_hotspot")
        if val is None:
            return "---", COLOR_COOL
        # Hotspot runs ~15 °C hotter; thresholds adjusted accordingly
        return sensors.fmt_temp(val), self._temp_color(val, 95, 105)

    def _get_gpu_mem_temp(self, readings, thresh):
        val = readings.get("gpu_temp_memory")
        if val is None:
            return "---", COLOR_COOL
        return sensors.fmt_temp(val), self._temp_color(val, 90, 105)

    def _get_nvme(self, readings, thresh):
        nvmes = readings.get("nvme_temps", [])
        if not nvmes:
            return "---", COLOR_COOL
        warn = thresh.get("nvme_warn", 60)
        crit = thresh.get("nvme_crit", 70)
        if len(nvmes) >= 2:
            t1, t2 = nvmes[0]["temp_c"], nvmes[1]["temp_c"]
            c1     = self._temp_color(t1, warn, crit)
            c2     = self._temp_color(t2, warn, crit)
            hottest = c1 if t1 >= t2 else c2
            v1 = sensors.fmt_temp(t1).rstrip("°")
            v2 = sensors.fmt_temp(t2)
            return f"{v1}/{v2}", hottest
        t1 = nvmes[0]["temp_c"]
        return sensors.fmt_temp(t1), self._temp_color(t1, warn, crit)

    def _get_fan_or_dimm(self, readings, thresh):
        """Returns (label, text, color) — 3-tuple for dynamic label update."""
        if readings.get("awcc_available") and readings.get("awcc_fans"):
            rpms = [f["rpm"] for f in readings["awcc_fans"]
                    if f.get("rpm") is not None]
            if rpms:
                mx    = max(rpms)
                text  = f"{mx/1000:.1f}K" if mx >= 1000 else str(mx)
                color = (COLOR_HOT  if mx > 4500 else
                         COLOR_WARM if mx > 3000 else COLOR_COOL)
                return "FAN", text, color
        dimms = readings.get("ram_temps", [])
        if not dimms:
            return "DIMM", "---", COLOR_COOL
        val   = max(d["temp_c"] for d in dimms)
        color = COLOR_HOT if val > 55 else COLOR_WARM if val > 45 else COLOR_COOL
        return "DIMM", sensors.fmt_temp(val), color

    def _get_ram(self, readings, thresh):
        val = readings.get("ram_usage_pct")
        if val is None:
            return "---", COLOR_COOL
        color = COLOR_HOT if val > 90 else COLOR_WARM if val > 75 else COLOR_COOL
        return f"{int(val)}%", color

    def _get_cpu_load(self, readings, thresh):
        val = readings.get("cpu_load_pct")
        if val is None:
            return "---", COLOR_COOL
        color = COLOR_HOT if val > 90 else COLOR_WARM if val > 70 else COLOR_COOL
        return f"{int(val)}%", color

    def _get_gpu_load(self, readings, thresh):
        val = readings.get("gpu_load")
        if val is None:
            return "---", COLOR_COOL
        color = COLOR_HOT if val > 90 else COLOR_WARM if val > 70 else COLOR_COOL
        return f"{int(val)}%", color

    def _get_vram(self, readings, thresh):
        used  = readings.get("gpu_vram_used_mb")
        total = readings.get("gpu_vram_total_mb")
        if used is None:
            return "---", COLOR_COOL
        if total and total > 0:
            pct   = (used / total) * 100
            color = COLOR_HOT if pct > 90 else COLOR_WARM if pct > 75 else COLOR_COOL
            return f"{int(pct)}%", color
        return f"{int(used)}M", COLOR_COOL

    def _get_cpu_watts(self, readings, thresh):
        val = readings.get("cpu_watts")
        if val is None:
            return "---", COLOR_COOL
        color = COLOR_HOT if val > 80 else COLOR_WARM if val > 50 else COLOR_COOL
        return f"{int(val)}W", color

    def _get_gpu_watts(self, readings, thresh):
        val = readings.get("gpu_watts")
        if val is None:
            return "---", COLOR_COOL
        color = (COLOR_HOT  if val > self._gpu_hot_w  else
                 COLOR_WARM if val > self._gpu_warn_w else COLOR_COOL)
        return f"{int(val)}W", color

    def _get_gpu_fan(self, readings, thresh):
        val = readings.get("gpu_fan_pct")
        if val is None:
            return "---", COLOR_COOL
        color = COLOR_HOT if val > 85 else COLOR_WARM if val > 60 else COLOR_COOL
        return f"{int(val)}%", color

    def _get_cpu_freq(self, readings, thresh):
        val = readings.get("cpu_freq_ghz")
        if val is None:
            return "---", COLOR_COOL
        return f"{val:.1f}G", COLOR_COOL

    def _get_gpu_clock(self, readings, thresh):
        val = readings.get("gpu_clock_mhz")
        if val is None:
            return "---", COLOR_COOL
        return f"{int(val)}M", COLOR_COOL

    def _get_battery(self, readings, thresh):
        pct      = readings.get("battery_pct")
        charging = readings.get("battery_charging", False)
        if pct is None:
            return "---", COLOR_COOL
        color  = (COLOR_COOL if charging or pct > 40 else
                  COLOR_WARM if pct > 20 else COLOR_HOT)
        suffix = "+" if charging else "%"
        return f"{int(pct)}{suffix}", color

    def _get_net_io(self, readings, thresh):
        dn = readings.get("net_down_mbps")
        up = readings.get("net_up_mbps")
        if dn is None:
            return "---", COLOR_COOL
        def _f(v): return f"{v:.0f}" if v >= 10 else f"{v:.1f}"
        return f"↓{_f(dn)} ↑{_f(up)}", COLOR_COOL

    def _get_disk_io(self, readings, thresh):
        rd = readings.get("disk_read_mbps")
        wr = readings.get("disk_write_mbps")
        if rd is None:
            return "---", COLOR_COOL
        def _f(v): return f"{v:.0f}" if v >= 10 else f"{v:.1f}"
        color = COLOR_WARM if (rd + wr) > 500 else COLOR_COOL
        return f"R{_f(rd)} W{_f(wr)}", color

    # ── History ───────────────────────────────────────────────────────────────

    def _extract_numeric(self, config_key: str, readings: dict):
        """Pull a single float from readings for sparkline history."""
        simple = {
            "cpu_temp":    "cpu_temp_avg",
            "gpu_temp":    "gpu_temp",
            "gpu_hotspot": "gpu_temp_hotspot",
            "gpu_mem_temp":"gpu_temp_memory",
            "ram_usage":   "ram_usage_pct",
            "cpu_load":    "cpu_load_pct",
            "gpu_load":    "gpu_load",
            "cpu_watts":   "cpu_watts",
            "gpu_watts":   "gpu_watts",
            "gpu_fan":     "gpu_fan_pct",
            "cpu_freq":    "cpu_freq_ghz",
            "gpu_clock":   "gpu_clock_mhz",
            "battery":     "battery_pct",
            "net_io":      "net_down_mbps",
            "disk_io":     "disk_read_mbps",
        }
        if config_key in simple:
            return readings.get(simple[config_key])
        if config_key == "nvme_temp":
            nvmes = readings.get("nvme_temps", [])
            return max(n["temp_c"] for n in nvmes) if nvmes else None
        if config_key == "gpu_vram":
            used  = readings.get("gpu_vram_used_mb")
            total = readings.get("gpu_vram_total_mb")
            if used and total and total > 0:
                return (used / total) * 100
        if config_key == "fan_rpm":
            fans = readings.get("awcc_fans", [])
            if fans:
                rpms = [f["rpm"] for f in fans if f.get("rpm")]
                return max(rpms) if rpms else None
            dimms = readings.get("ram_temps", [])
            return max(d["temp_c"] for d in dimms) if dimms else None
        return None

    def _feed_history(self, readings: dict):
        for key in self._cells:
            if key not in self._history:
                self._history[key] = collections.deque(maxlen=60)
            self._history[key].append(self._extract_numeric(key, readings))

    # ── Threshold flash ───────────────────────────────────────────────────────

    def _check_flash(self, config_key: str, color: str):
        """Flash the value widget background on transition into warn/crit."""
        prev = self._flash.get(config_key)
        if prev in (None, COLOR_COOL) and color in (COLOR_WARM, COLOR_HOT):
            cell = self._cells.get(config_key)
            if cell:
                flash_bg = "#332200" if color == COLOR_WARM else "#330000"
                try:
                    lbl = cell["value"]
                    lbl.config(bg=flash_bg)
                    self.root.after(600, lambda w=lbl: w.config(bg=BG))
                except Exception:
                    pass
        self._flash[config_key] = color

    # ── Fullscreen auto-hide ──────────────────────────────────────────────────

    def _is_fullscreen_active(self) -> bool:
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return False
            sw = ctypes.windll.user32.GetSystemMetrics(0)
            sh = ctypes.windll.user32.GetSystemMetrics(1)

            class RECT(ctypes.Structure):
                _fields_ = [("left",   ctypes.c_long),
                             ("top",    ctypes.c_long),
                             ("right",  ctypes.c_long),
                             ("bottom", ctypes.c_long)]
            rect = RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w = rect.right  - rect.left
            h = rect.bottom - rect.top
            return w >= sw and h >= sh and rect.left <= 0 and rect.top <= 0
        except Exception:
            return False

    def _check_fullscreen_hide(self):
        if not cfg.get().get("display", {}).get("auto_hide_fullscreen", True):
            if self._fs_hidden:
                # Feature disabled while hidden — restore
                if not cfg.get_value("display", "bar_hidden", default=False):
                    self.root.deiconify()
                self._fs_hidden = False
            return
        fs = self._is_fullscreen_active()
        hidden = cfg.get_value("display", "bar_hidden", default=False)
        if fs and not self._fs_hidden and not hidden:
            self.root.withdraw()
            self._fs_hidden = True
        elif not fs and self._fs_hidden:
            if not hidden:
                self.root.deiconify()
            self._fs_hidden = False

    # ── Profile dot ───────────────────────────────────────────────────────────

    def _update_profile_dot(self, profile: str):
        color = _PROFILE_COLORS.get(profile, "#444444")
        for up in cfg.get().get("profiles", {}).get("user_profiles", []):
            if up.get("name") == profile:
                color = up.get("color", "#7700cc")
                break
        try:
            self.profile_dot.itemconfig(self._dot_oval, fill=color)
        except Exception:
            pass

    # ── Update loop ───────────────────────────────────────────────────────────

    def _update(self):
        try:
            self._check_config_changed()
            c        = cfg.get()
            readings = sensors.get_readings()
            thresh   = c.get("thresholds", {})
            profile  = profiles.get_current()
            sens     = c.get("sensors", {})

            # Rebuild everything if orientation changed
            orient_now = cfg.get_value("display", "bar_orientation", default="horizontal")
            if orient_now != self._last_orient:
                self._orient      = orient_now
                self._last_orient = orient_now
                self._rebuild_bar_widgets()

            # Rebuild cells if enabled set changed
            enabled_now = tuple(
                k for k in self._SENSOR_DEFS if sens.get(k, False)
            )
            if enabled_now != getattr(self, "_last_enabled", None):
                self._rebuild_sensor_cells(sens)
                self._last_enabled = enabled_now

            # Feed sparkline history
            self._feed_history(readings)

            # Profile dot + label
            try:
                from core import turbo_cool
                if turbo_cool.is_active():
                    tc_text = turbo_cool.status_text() or "COOL"
                    self.profile_label.config(text=tc_text, fg="#00ffff")
                    self._update_profile_dot("manual")
                else:
                    self._update_profile_dot(profile)
                    self.profile_label.config(
                        text=profile.upper()[:4], fg="#444444")
            except Exception:
                self._update_profile_dot(profile)
                self.profile_label.config(text=profile.upper()[:4], fg="#444444")

            # Update sensor values + flash
            for key, cell in self._cells.items():
                try:
                    result = cell["getter"](readings, thresh)
                    if len(result) == 3:
                        new_label, text, color = result
                        lw = cell.get("label_widget")
                        if lw and lw.winfo_exists():
                            lw.config(text=new_label)
                    else:
                        text, color = result
                    cell["value"].config(text=text, fg=color)
                    self._check_flash(key, color)
                except Exception:
                    pass

            # Fullscreen auto-hide
            self._check_fullscreen_hide()

        except Exception as e:
            logger.debug("Bar update error: %s", e)

        self.root.after(int(self._get_interval_ms()), self._update)

    def _check_config_changed(self):
        import os
        from core.constants import CONFIG_PATH
        try:
            mtime = os.path.getmtime(CONFIG_PATH)
            if mtime != getattr(self, "_config_mtime", None):
                cfg.reload()
                self._config_mtime = mtime
        except Exception:
            pass

    def _get_interval_ms(self) -> int:
        c    = cfg.get()
        unit = c.get("display", {}).get("update_interval_unit", "seconds")
        val  = c.get("display", {}).get("update_interval_value", 2.0)
        if unit == "milliseconds":
            return max(100, int(val))
        return max(500, int(float(val) * 1000))

    # ── Drag ─────────────────────────────────────────────────────────────────

    def _drag_start(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _drag_move(self, event):
        x = self.root.winfo_x() + event.x - self._drag_x
        y = self.root.winfo_y() + event.y - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    def _drag_end(self, event):
        cfg.set_value("display", "bar_x", value=self.root.winfo_x())
        cfg.set_value("display", "bar_y", value=self.root.winfo_y())

    def _restore_position(self):
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w  = self.root.winfo_width()
        h  = self.root.winfo_height()

        x = cfg.get_value("display", "bar_x", default=None)
        y = cfg.get_value("display", "bar_y", default=None)
        if x is None or y is None:
            x = sw - w - 10
            y = sh - h - 48
        self.root.geometry(f"+{int(x)}+{int(y)}")

    # ── Size / rebuild ────────────────────────────────────────────────────────

    def _set_size(self, size_name: str):
        self._size = size_name
        self._apply_size_fonts()
        cfg.set_value("display", "bar_size", value=size_name)
        s = BAR_SIZES[self._size]
        self.inner.config(padx=s["pad_x"], pady=s["pad_y"])
        self._rebuild_bar_widgets()

    def _rebuild_bar_widgets(self):
        for w in self.inner.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass

        # Grip
        grip = tk.Label(self.inner, text="⠿",
                        font=("Consolas", BAR_SIZES[self._size]["sep"]),
                        bg=BG, fg="#333333", cursor="fleur", padx=4)
        grip.pack(side=self._pack_side())
        for ev, cb in [("<ButtonPress-1>",   self._drag_start),
                       ("<B1-Motion>",       self._drag_move),
                       ("<ButtonRelease-1>", self._drag_end),
                       ("<Button-3>",        self._show_context_menu)]:
            grip.bind(ev, cb)

        # Profile dot
        dot_sz = BAR_SIZES[self._size]["label"] + 4
        self.profile_dot = tk.Canvas(
            self.inner, width=dot_sz, height=dot_sz,
            bg=BG, highlightthickness=0, cursor="fleur"
        )
        pad_kw = {"pady": (2, 0)} if self._orient == "vertical" else {"padx": (2, 0)}
        self.profile_dot.pack(side=self._pack_side(), **pad_kw)
        self._dot_oval = self.profile_dot.create_oval(
            2, 2, dot_sz - 2, dot_sz - 2, fill="#1a4a6e", outline=""
        )

        # Profile label
        self.profile_label = tk.Label(
            self.inner, text="IDLE",
            font=("Consolas", BAR_SIZES[self._size]["label"], "bold"),
            bg=BG, fg="#444444", padx=2, cursor="fleur"
        )
        self.profile_label.pack(side=self._pack_side())

        for w in [self.profile_dot, self.profile_label]:
            w.bind("<ButtonPress-1>",   self._drag_start)
            w.bind("<B1-Motion>",       self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)
            w.bind("<Button-3>",        self._show_context_menu)

        # Separator
        sep = self._make_separator(self.inner)
        for ev, cb in [("<ButtonPress-1>",   self._drag_start),
                       ("<B1-Motion>",       self._drag_move),
                       ("<ButtonRelease-1>", self._drag_end)]:
            sep.bind(ev, cb)

        self._cell_widgets = []
        self._cells        = {}
        self._last_enabled = None

    # ── Context menu ──────────────────────────────────────────────────────────

    def _show_context_menu(self, event):
        menu = tk.Menu(self.root, tearoff=0,
                       bg="#1a1a1a", fg="#cccccc",
                       activebackground="#333333",
                       activeforeground="#ffffff",
                       font=("Segoe UI", 9))

        try:
            from core import turbo_cool
            if turbo_cool.is_active():
                menu.add_command(label="⚡ Turbo Cool: ON — click to disable",
                                 foreground="#00ffff",
                                 command=turbo_cool.deactivate)
            else:
                menu.add_command(label="⚡ Turbo Cool: OFF — click to enable",
                                 command=turbo_cool.activate)
        except Exception:
            pass

        menu.add_separator()
        menu.add_command(label="Open Settings", command=self._open_settings)

        menu.add_separator()
        size_menu = tk.Menu(menu, tearoff=0,
                            bg="#1a1a1a", fg="#cccccc",
                            activebackground="#333333",
                            activeforeground="#ffffff",
                            font=("Segoe UI", 9))
        for size_name in BAR_SIZES:
            check = "✓ " if size_name == self._size else "   "
            size_menu.add_command(
                label=f"{check}{size_name}",
                command=lambda s=size_name: self._set_size(s)
            )
        menu.add_cascade(label="Bar Size", menu=size_menu)

        orient_menu = tk.Menu(menu, tearoff=0,
                              bg="#1a1a1a", fg="#cccccc",
                              activebackground="#333333",
                              activeforeground="#ffffff",
                              font=("Segoe UI", 9))
        for orient_name, orient_val in [("Horizontal", "horizontal"), ("Vertical", "vertical")]:
            check = "✓ " if self._orient == orient_val else "   "
            orient_menu.add_command(
                label=f"{check}{orient_name}",
                command=lambda v=orient_val: self._set_orientation(v)
            )
        menu.add_cascade(label="Orientation", menu=orient_menu)

        menu.add_separator()
        menu.add_command(label="Profile: Auto",
                         command=lambda: profiles.set_manual_override(None))
        menu.add_command(label="Profile: Idle",
                         command=lambda: profiles.set_manual_override("idle"))
        menu.add_command(label="Profile: Gaming",
                         command=lambda: profiles.set_manual_override("gaming"))
        menu.add_command(label="Profile: Streaming",
                         command=lambda: profiles.set_manual_override("streaming"))
        menu.add_separator()
        menu.add_command(label="Hide Bar", command=self._hide_bar)
        menu.tk_popup(event.x_root, event.y_root)

    def _open_settings(self):
        import subprocess, os, sys
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        subprocess.Popen(
            [sys.executable, os.path.join(base, "aliencore.py"), "--settings"],
            cwd=base,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _temp_color(val, warn, crit):
        if val >= crit:  return COLOR_HOT
        if val >= warn:  return COLOR_WARM
        return COLOR_COOL

    @staticmethod
    def _read_gpu_tdp_thresholds() -> tuple:
        """
        Return (warn_watts, hot_watts) from the hardware-cache GPU TDP.
        Thresholds: warn = 65% of TDP, hot = 88% of TDP.
        Falls back to conservative defaults if cache is unavailable.
        """
        try:
            import json, os
            from core.constants import HARDWARE_CACHE
            if os.path.exists(HARDWARE_CACHE):
                with open(HARDWARE_CACHE, "r", encoding="utf-8") as f:
                    hw = json.load(f)
                gpus = hw.get("gpu", [])
                for g in gpus:
                    tdp = g.get("tdp_watts") or g.get("power_limit_w")
                    if tdp and float(tdp) > 20:
                        tdp = float(tdp)
                        return int(tdp * 0.65), int(tdp * 0.88)
        except Exception:
            pass
        return 100, 140   # safe generic fallback

    def run(self):
        self.root.mainloop()
