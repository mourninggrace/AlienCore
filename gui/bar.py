"""
AlienCore - bar.py
HUD-style always-on-top sensor bar.

Each sensor renders as a chamfered canvas cell containing:
  · colored left accent strip (cool / warn / hot)
  · dim label row
  · bold value row
  · inline micro-chart (last 30 samples, area fill + line)

Preserves: drag, right-click context menu, double-click 90-sample sparkline
popup, hover tooltip, orientation toggle, size presets (S/M/L/XL), fullscreen
auto-hide, position persistence, DWM rounded corners, profile-aware highlight.
"""

import tkinter as tk
import tkinter.font as tkFont
import logging
import collections
import ctypes
import math
import time
from core import config_manager as cfg, sensors, profiles
from core.constants import COLOR_COOL, COLOR_WARM, COLOR_HOT

logger = logging.getLogger("aliencore.bar")

# ── Inline micro-chart animation ──────────────────────────────────────────────
# Sensors are read at the sensor-poll rate (~2–3 s, cheap).  The inline chart
# is redrawn at a much higher rate and plots samples on a time-indexed x-axis,
# so the line flows smoothly leftward between polls.
CHART_WINDOW_SECONDS   = 20    # time span shown inside each cell
CHART_DRAW_INTERVAL_MS = 33    # ~30 fps
# Per-sensor ring buffers.  Defaults are sized for 8 GB hosts; systems with
# more installed RAM keep proportionally more history (see core.mem_tier) so
# pop-out sparklines and the inline chart can show a longer timeline without
# any perceptible memory cost.
from core import mem_tier as _mt
_SAMPLE_LOG_MAXLEN     = _mt.scaled(120, cap=960)   # (t, v) samples per sensor
_SPARK_HISTORY_MAXLEN  = _mt.scaled(90,  cap=720)   # value-only ring for sparkline popup

# Cells whose values come from the lhm_bridge daemon (CPU/GPU/NVMe/DIMM temps
# + CPU package watts).  When sensors.py is serving cached data because the
# bridge missed a poll, _update() dims these cells.  Others (GPU via pynvml,
# CPU load/freq via psutil, AWCC fans, battery, net/disk I/O) are sourced
# independently and stay at full brightness.
_LHM_SENSOR_KEYS = frozenset({
    "cpu_temp", "gpu_temp", "gpu_hotspot", "gpu_mem_temp",
    "nvme_temp", "nvme_temp2", "cpu_watts",
})

# ── Polish animation tunables ────────────────────────────────────────────────
_PULSE_PERIOD_SECONDS  = 1.8   # breathing cycle for accent / current-value dot
_COLOR_LERP_PER_FRAME  = 0.22  # catch-up rate when threshold color changes (~350ms)

# ── Perimeter comet (green glow traveling around the bar edge) ───────────────
# A short, faded trail of line segments walks the 1-px border ring clockwise.
# Its speed ramps with CPU load: idle = slow drift, max load = brisk sweep.
_COMET_HEAD_COLOR   = "#00FF88"   # bright edge of the trail
_COMET_SEGMENT_PX   = 4           # length of each tail segment (pixels)
_PERIMETER_SEGMENTS = 18          # segment count — total trail ≈ 72 px
_COMET_SPEED_MIN    = 45.0        # pixels / second at  0 % CPU load
_COMET_SPEED_MAX    = 240.0       # pixels / second at 100 % CPU load

# ── Sizing ────────────────────────────────────────────────────────────────────
# The bar is resized by dragging an edge — bottom edge for horizontal bars,
# right edge for vertical bars.  A single continuous scale factor drives every
# font size, chart height, and padding in the bar.  A saved scale is persisted
# to config so the bar reopens at the user's chosen dimensions.
_SCALE_MIN     = 0.65
_SCALE_MAX     = 2.20
_SCALE_DEFAULT = 1.00

_RING_PX           = 5     # ring of outer-canvas pixels reserved for the comet
_COMET_INSET       = 2     # comet line inset from the canvas edge (centers it in the ring)
_RESIZE_EDGE_PX    = 7     # distance from edge that qualifies as the resize zone

def _scaled_preset(scale: float) -> dict:
    """Per-size parameters derived from the continuous scale factor."""
    scale = max(_SCALE_MIN, min(_SCALE_MAX, scale))
    return {
        "label":   max(6, int(round(8.0  * scale))),
        "value":   max(8, int(round(13.0 * scale))),
        "chart_h": max(5, int(round(9.0  * scale))),
        "accent":  max(2, int(round(3.0  * scale))),
        "pad_x":   max(4, int(round(6.0  * scale))),
        "pad_y":   max(2, int(round(4.0  * scale))),
        "gap":     max(2, int(round(4.0  * scale))),
    }

# ── Color palette ─────────────────────────────────────────────────────────────
# Overwritten by apply_theme() once the Settings theme is known.  These values
# are the historical "Void" defaults — safe to display if the config read
# happens to fail.
BG           = "#0b0b12"
CELL_BG      = "#13131e"
CELL_OUTLINE = "#232334"
BORDER       = "#1a1f2e"
FG_LABEL     = "#6e6e88"
FG_DIM       = "#44445a"


def _hex_to_rgb(h: str):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    r = max(0, min(255, int(r))); g = max(0, min(255, int(g))); b = max(0, min(255, int(b)))
    return f"#{r:02x}{g:02x}{b:02x}"


def _scale_color(hex_color: str, factor: float) -> str:
    """factor < 1 darkens, > 1 lightens.  Clamped."""
    try:
        r, g, b = _hex_to_rgb(hex_color)
        return _rgb_to_hex(r * factor, g * factor, b * factor)
    except Exception:
        return hex_color


def _derive_bar_palette(theme_name: str) -> dict:
    """Map a Settings theme entry to the bar's 6-slot palette.

    The Settings palette has more slots than the bar needs, but the roles
    line up cleanly:
      bar BG           ← theme BG_HW       (darkest surface, inside ring)
      bar CELL_BG      ← theme BG_PANEL    (one step up — cell faces)
      bar CELL_OUTLINE ← theme BG_SECT     (quiet cell borders)
      bar BORDER       ← theme SEP         (outer ring the comet rides on)
      bar FG_LABEL     ← theme FG_DIM      (cell labels)
      bar FG_DIM       ← darken(FG_DIM)    (tick/guide marks — must be dim)
    """
    try:
        # Lazy import to avoid a circular edge during early startup — the
        # Settings module is heavy, and the bar builds before Settings
        # is ever opened.
        from gui.settings_gui import THEMES
    except Exception:
        THEMES = {}
    t = THEMES.get(theme_name) or THEMES.get("Void") or {}
    return {
        "BG":           t.get("BG_HW",    "#0b0b12"),
        "CELL_BG":      t.get("BG_PANEL", "#13131e"),
        "CELL_OUTLINE": t.get("BG_SECT",  "#232334"),
        "BORDER":       t.get("SEP",      "#1a1f2e"),
        "FG_LABEL":     t.get("FG_DIM",   "#6e6e88"),
        "FG_DIM":       _scale_color(t.get("FG_DIM", "#6e6e88"), 0.55),
    }


def apply_theme(name: str):
    """Rewrite the bar's module-level palette from a Settings theme name.

    Safe to call at any time.  If the sensor bar window is alive, schedules
    a rebuild on the Tk main thread so new colors take effect immediately.
    """
    global BG, CELL_BG, CELL_OUTLINE, BORDER, FG_LABEL, FG_DIM
    p = _derive_bar_palette(name)
    BG           = p["BG"]
    CELL_BG      = p["CELL_BG"]
    CELL_OUTLINE = p["CELL_OUTLINE"]
    BORDER       = p["BORDER"]
    FG_LABEL     = p["FG_LABEL"]
    FG_DIM       = p["FG_DIM"]

    inst = _instance
    if inst is not None:
        try:
            inst.root.after(0, inst._reapply_theme)
        except Exception:
            pass

# Per-sensor maximum sample values (used once at build to size each cell)
_SAMPLE_VALUES = {
    "cpu_temp":     "100°",
    "gpu_temp":     "100°",
    "gpu_hotspot":  "110°",
    "gpu_mem_temp": "100°",
    "nvme_temp":    "100°",
    "nvme_temp2":   "100°",
    "fan_rpm":      "9.9K",
    "ram_usage":    "100%",
    "cpu_load":     "100%",
    "gpu_load":     "100%",
    "gpu_vram":     "100%",
    "cpu_watts":    "250W",
    "gpu_watts":    "250W",
    "gpu_fan":      "100%",
    "cpu_freq":     "5.8G",
    "gpu_clock":    "2850M",
    "battery":      "100+",
    "net_io":       "↓999k ↑999k",
    "disk_io":      "R999 W999",
}

_PROFILE_COLORS = {
    "idle":      "#2a6b98",
    "working":   "#3aa15e",
    "gaming":    "#e63300",
    "streaming": "#0088e0",
    "manual":    "#b88800",
}

_instance = None


# ─────────────────────────────────────────────────────────────────────────────
# Tooltip
# ─────────────────────────────────────────────────────────────────────────────

class _Tooltip:
    """Simple hover tooltip shown below a widget."""
    def __init__(self, widget, text_fn):
        self._widget  = widget
        self._text_fn = text_fn
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
# Sparkline popup (double-click sensor cell to open)
# ─────────────────────────────────────────────────────────────────────────────

class _Sparkline:
    _open = {}

    _W     = 600
    _H     = 200
    _PAD_L = 48
    _PAD_R = 20
    _PAD_T = 18
    _PAD_B = 32

    @classmethod
    def show(cls, config_key: str, label: str,
             history: collections.deque, unit: str,
             position_idx: int = 0, position_count: int = 1):
        if config_key in cls._open:
            try:
                cls._open[config_key]["win"].destroy()
            except Exception:
                pass

        PL, PR     = cls._PAD_L, cls._PAD_R
        PT, PB     = cls._PAD_T, cls._PAD_B
        BG_W       = "#0c0c12"
        BG2        = "#12121c"
        FG         = "#e0e0f0"
        FG_DIM_    = "#44445a"
        FG_MED     = "#666680"

        win = tk.Toplevel()
        win.withdraw()
        win.title(f"{label}  —  90-second history")
        win.attributes("-topmost", True)
        win.configure(bg=BG_W)
        win.resizable(True, True)
        win.minsize(500, 340)
        from gui.tray import set_window_icon
        set_window_icon(win)

        hdr = tk.Frame(win, bg=BG_W)
        hdr.pack(fill="x", padx=18, pady=(14, 0))
        tk.Label(hdr, text=label,
                 font=("Segoe UI", 13, "bold"),
                 bg=BG_W, fg=FG).pack(side="left")
        cur_lbl = tk.Label(hdr, text="—",
                           font=("Consolas", 22, "bold"),
                           bg=BG_W, fg=COLOR_COOL)
        cur_lbl.pack(side="right", padx=(0, 2))

        tk.Frame(win, bg="#1a1a28", height=1).pack(fill="x", padx=18, pady=(8, 0))

        canvas = tk.Canvas(win, bg=BG2, highlightthickness=0, bd=0)
        canvas.pack(padx=18, pady=(10, 0), fill="both", expand=True)

        stats = tk.Frame(win, bg=BG_W)
        stats.pack(fill="x", padx=18, pady=(8, 16))

        min_lbl = tk.Label(stats, text="Min  —",
                           font=("Segoe UI", 8), bg=BG_W, fg=FG_MED)
        min_lbl.pack(side="left")
        avg_lbl = tk.Label(stats, text="Avg  —",
                           font=("Segoe UI", 8), bg=BG_W, fg=FG_MED)
        avg_lbl.pack(side="left", padx=(22, 0))
        max_lbl = tk.Label(stats, text="Max  —",
                           font=("Segoe UI", 8), bg=BG_W, fg=FG_MED)
        max_lbl.pack(side="left", padx=(22, 0))
        cnt_lbl = tk.Label(stats, text="",
                           font=("Segoe UI", 8), bg=BG_W, fg=FG_DIM_)
        cnt_lbl.pack(side="right")

        cls._open[config_key] = {"win": win, "history": history}

        _after_id = [None]

        def _draw():
            if config_key not in cls._open:
                return
            W = canvas.winfo_width()
            H = canvas.winfo_height()
            if W <= 1:
                W = cls._W
                H = cls._H

            hist = cls._open[config_key]["history"]
            vals = [v for v in hist if v is not None]

            canvas.delete("all")
            cw = W - PL - PR
            ch = H - PT - PB

            for frac, alpha_hint in [(0.25, "#141420"), (0.5, "#181826"),
                                     (0.75, "#141420")]:
                gy = PT + int(frac * ch)
                canvas.create_line(PL, gy, W - PR, gy,
                                   fill=alpha_hint, dash=(3, 8), width=1)

            canvas.create_line(PL, PT + ch, W - PR, PT + ch,
                               fill="#1c1c2c", width=1)
            canvas.create_line(PL, PT, PL, PT + ch,
                               fill="#1c1c2c", width=1)

            if len(vals) < 2:
                canvas.create_text(
                    PL + cw // 2, PT + ch // 2,
                    text="Collecting data…",
                    fill=FG_DIM_, font=("Segoe UI", 11))
                if config_key in cls._open:
                    _after_id[0] = win.after(2000, _draw)
                return

            mn       = min(vals)
            mx       = max(vals)
            span     = max(mx - mn, 0.5)
            avg_val  = sum(vals) / len(vals)
            cur      = vals[-1]
            n        = len(vals)

            if unit in ("°", "°C", "°F"):
                color = (COLOR_HOT  if cur >= 90 else
                         COLOR_WARM if cur >= 75 else COLOR_COOL)
            elif unit == "%":
                color = (COLOR_HOT  if cur >= 90 else
                         COLOR_WARM if cur >= 70 else COLOR_COOL)
            else:
                color = COLOR_COOL

            try:
                r = int(color[1:3], 16)
                g = int(color[3:5], 16)
                b = int(color[5:7], 16)
                fill_col = f"#{max(0,r//5):02x}{max(0,g//5):02x}{max(0,b//5):02x}"
                mid_col  = f"#{max(0,r//3):02x}{max(0,g//3):02x}{max(0,b//3):02x}"
            except Exception:
                fill_col = "#051008"
                mid_col  = "#0a2010"

            def _pt(i, v):
                x = PL + int(i * cw / max(n - 1, 1))
                y = PT + ch - int((v - mn) / span * ch)
                return x, max(PT, min(PT + ch, y))

            pts = [_pt(i, v) for i, v in enumerate(vals)]
            poly = pts + [(pts[-1][0], PT + ch), (pts[0][0], PT + ch)]
            canvas.create_polygon(poly, fill=fill_col, outline="")

            mid_h = max(2, int(ch * 0.3))
            mid_pts = []
            for x, y in pts:
                mid_pts.append((x, min(y + mid_h, PT + ch)))
            mid_poly = pts + list(reversed(mid_pts))
            canvas.create_polygon(mid_poly, fill=mid_col, outline="")

            flat = [coord for pt in pts for coord in pt]
            canvas.create_line(flat, fill=color, width=2,
                               smooth=True, splinesteps=24)

            cx, cy = pts[-1]
            canvas.create_oval(cx - 5, cy - 5, cx + 5, cy + 5,
                               fill=color, outline=BG2, width=2)

            for frac, val in [(0.0, mx), (0.5, (mn + mx) / 2), (1.0, mn)]:
                gy = PT + int(frac * ch)
                canvas.create_text(PL - 6, gy,
                                   text=f"{val:.1f}",
                                   anchor="e", fill=FG_MED,
                                   font=("Consolas", 8))

            elapsed = n * 2
            canvas.create_text(PL, PT + ch + 14,
                               text=f"−{elapsed}s",
                               anchor="w", fill=FG_DIM_,
                               font=("Consolas", 8))
            canvas.create_text(W - PR, PT + ch + 14,
                               text="now",
                               anchor="e", fill=FG_DIM_,
                               font=("Consolas", 8))
            canvas.create_text(PL + cw // 2, PT + ch + 14,
                               text=f"−{elapsed // 2}s",
                               anchor="center", fill=FG_DIM_,
                               font=("Consolas", 8))

            cur_lbl.config(text=f"{cur:.1f}{unit}", fg=color)
            min_lbl.config(text=f"Min   {mn:.1f}{unit}",  fg=FG_MED)
            avg_lbl.config(text=f"Avg   {avg_val:.1f}{unit}", fg=color)
            max_lbl.config(text=f"Max   {mx:.1f}{unit}", fg=FG_MED)
            cnt_lbl.config(text=f"{n} / 90 samples")

            if config_key in cls._open:
                _after_id[0] = win.after(2000, _draw)

        def _on_resize(event=None):
            if _after_id[0]:
                try:
                    win.after_cancel(_after_id[0])
                except Exception:
                    pass
            _after_id[0] = win.after(80, _draw)

        canvas.bind("<Configure>", _on_resize)
        _draw()

        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        win_w  = cls._W + 36
        win_h  = cls._H + 140
        # Stagger sibling windows (e.g. NET ↓ + NET ↑) so they don't overlap.
        gap     = 24
        total_w = win_w * position_count + gap * max(0, position_count - 1)
        start_x = max(20, (sw - total_w) // 2)
        x_pos   = start_x + position_idx * (win_w + gap)
        y_pos   = (sh - win_h) // 2
        win.geometry(f"{win_w}x{win_h}+{x_pos}+{y_pos}")
        win.deiconify()

        win.protocol("WM_DELETE_WINDOW", lambda: cls._close(config_key))

    @classmethod
    def _close(cls, key):
        if key in cls._open:
            try:
                cls._open[key]["win"].destroy()
            except Exception:
                pass
            del cls._open[key]


# ─────────────────────────────────────────────────────────────────────────────
# Color helper
# ─────────────────────────────────────────────────────────────────────────────

def _darken(hex_color: str, factor: float) -> str:
    """Return a darker shade of a #rrggbb color (0.0 = black, 1.0 = original)."""
    try:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        f = max(0.0, min(1.0, factor))
        return f"#{int(r*f):02x}{int(g*f):02x}{int(b*f):02x}"
    except Exception:
        return "#000000"


def _lerp_color(c1: str, c2: str, t: float) -> str:
    """Interpolate between two #rrggbb colors by t in [0, 1]."""
    try:
        r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
        r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)
        return f"#{max(0,min(255,r)):02x}{max(0,min(255,g)):02x}{max(0,min(255,b)):02x}"
    except Exception:
        return c2


def _perimeter_point(t: float, W: int, H: int) -> tuple:
    """(x, y) at distance t along the canvas perimeter, clockwise from (0,0)."""
    if W <= 0 or H <= 0:
        return (0.0, 0.0)
    P = 2.0 * (W + H)
    t %= P
    if t < W:
        return (t, 0.0)
    t -= W
    if t < H:
        return (float(W), t)
    t -= H
    if t < W:
        return (float(W) - t, float(H))
    t -= W
    return (0.0, float(H) - t)


def _perimeter_segment_pts(t1: float, t2: float, W: int, H: int) -> list:
    """Three perimeter points from t1 → t2 (clockwise), folding at a corner
    when the segment crosses one.  Always yields 3 points so canvas.coords()
    can stay a fixed length."""
    if W <= 0 or H <= 0:
        return [(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)]
    P = 2.0 * (W + H)
    t1 %= P
    t2 %= P
    if t2 < t1:
        t2 += P
    p1 = _perimeter_point(t1, W, H)
    p2 = _perimeter_point(t2 % P, W, H)
    mid_pt = None
    for c in (W, W + H, 2 * W + H, 2 * (W + H)):
        if t1 < c < t2:
            mid_pt = _perimeter_point(c % P, W, H)
            break
    if mid_pt is None:
        mid_pt = _perimeter_point(((t1 + t2) * 0.5) % P, W, H)
    return [p1, mid_pt, p2]


# ─────────────────────────────────────────────────────────────────────────────
# HUD sensor cell
# ─────────────────────────────────────────────────────────────────────────────

class _HudCell:
    """
    A chamfered sensor cell rendered on a single tk.Canvas.

    Layout (top → bottom):
        label text     (dim)
        value text     (bold, threshold-colored)
        inline chart   (time-indexed samples, flows left at CHART_DRAW_INTERVAL_MS)

    Left edge is a thin colored accent strip that tracks the threshold state.
    Top-right corner is chamfered (5-px diagonal cut) for a HUD-panel feel.

    Chart items (polygon + line + dot) are created once at construction and
    updated via coords()/itemconfigure() instead of delete+create, so 30-fps
    redraws on ~10 cells stay well under 1 % CPU.
    """

    _CHAMFER = 5

    def __init__(self, parent, config_key: str, label: str, unit: str,
                 size_preset: dict, bg: str = BG):
        self.config_key = config_key
        self.label      = label
        self.unit       = unit

        accent_w = size_preset["accent"]
        chart_h  = size_preset["chart_h"]
        pad      = 6
        top_pad  = 3
        mid_gap  = 2
        bot_pad  = 3

        self._label_font = tkFont.Font(
            family="Consolas", size=size_preset["label"], weight="bold")
        self._value_font = tkFont.Font(
            family="Consolas", size=size_preset["value"], weight="bold")

        # Measure text up front so we can size the cell once and keep the
        # layout stable across updates.
        sample = _SAMPLE_VALUES.get(config_key, "100%")
        text_w = max(self._label_font.measure(label),
                     self._value_font.measure(sample))
        text_w = int(text_w * 1.05) + 2   # small margin for unicode width drift

        label_h = self._label_font.metrics("linespace")
        value_h = self._value_font.metrics("linespace")

        cell_w = accent_w + pad + text_w + pad
        cell_h = top_pad + label_h + mid_gap + value_h + mid_gap + chart_h + bot_pad

        self.canvas = tk.Canvas(
            parent, width=cell_w, height=cell_h,
            bg=bg, highlightthickness=0, bd=0, cursor="fleur"
        )
        self._w = cell_w
        self._h = cell_h

        # 1. Chamfered background polygon (top-right corner cut)
        ch = self._CHAMFER
        self._bg_poly = self.canvas.create_polygon(
            [0, 0,
             cell_w - ch, 0,
             cell_w, ch,
             cell_w, cell_h,
             0, cell_h],
            fill=CELL_BG, outline=CELL_OUTLINE, width=1
        )

        # 2. Chart bounds
        self._chart_x0 = accent_w + pad
        self._chart_x1 = cell_w - pad
        self._chart_y0 = cell_h - chart_h - bot_pad
        self._chart_y1 = cell_h - bot_pad

        # 3. Top-edge subtle highlight — implies a slightly raised panel
        self.canvas.create_line(
            accent_w + 1, 1, cell_w - ch - 1, 1,
            fill="#2e2e44", width=1
        )

        # 4. Left accent strip
        self._accent_id = self.canvas.create_rectangle(
            0, 1, accent_w, cell_h - 1,
            fill=COLOR_COOL, outline=""
        )

        # 7. Label text
        self._label_id = self.canvas.create_text(
            accent_w + pad, top_pad,
            text=label, anchor="nw",
            fill=FG_LABEL, font=self._label_font
        )

        # 8. Value text
        val_y = top_pad + label_h + mid_gap
        self._value_id = self.canvas.create_text(
            accent_w + pad, val_y,
            text="---", anchor="nw",
            fill=COLOR_COOL, font=self._value_font
        )

        # 9-13. Persistent chart items — z-ordered bottom→top: polygon, glow
        # line, main line, outer dot, inner dot.  All hidden until ≥2 samples.
        self._chart_poly = self.canvas.create_polygon(
            0, 0, 1, 0, 0, 1,
            fill="", outline="", state="hidden"
        )
        self._chart_glow = self.canvas.create_line(
            0, 0, 1, 0,
            fill="", width=3, state="hidden"
        )
        self._chart_line = self.canvas.create_line(
            0, 0, 1, 0,
            fill="", width=1, state="hidden"
        )
        self._chart_dot_outer = self.canvas.create_oval(
            0, 0, 1, 1,
            fill="", outline="", state="hidden"
        )
        self._chart_dot = self.canvas.create_oval(
            0, 0, 1, 1,
            fill="", outline="", state="hidden"
        )

        # Animation state — target is the threshold color set by update_value;
        # shown lerps toward target each frame.  is_alert drives accent pulsing.
        self._target_color = COLOR_COOL
        self._shown_color  = COLOR_COOL
        self._is_alert     = False

    # Widget proxying — let SensorBar call .pack / .bind directly.
    @property
    def widget(self):
        return self.canvas

    def pack(self, **kw):
        self.canvas.pack(**kw)

    def bind(self, *args, **kw):
        return self.canvas.bind(*args, **kw)

    def destroy(self):
        try:
            self.canvas.destroy()
        except Exception:
            pass

    def update_value(self, text: str, color: str):
        """Update value text + set target color.  Called at sensor-poll rate.

        Only the text is written here; the accent strip and value-text *color*
        are driven by the draw loop so they can interpolate smoothly toward
        the new target over ~350 ms instead of snapping on threshold crossings.
        """
        try:
            self.canvas.itemconfig(self._value_id, text=text)
        except Exception:
            pass
        self._target_color = color
        self._is_alert = color in (COLOR_WARM, COLOR_HOT)

    def update_label(self, new_label: str):
        try:
            self.canvas.itemconfig(self._label_id, text=new_label)
        except Exception:
            pass

    def render_chart(self, sample_log, now: float, color: str,
                     window_seconds: float = CHART_WINDOW_SECONDS):
        """
        Redraw the inline chart from a time-indexed sample log.

        Also drives the per-frame polish animations: color interpolation
        toward the current threshold target, a slow sine pulse on the accent
        strip and value-text glow when the sensor is in warn/hot state, and
        a breathing current-value dot.  All of this is cheap — just
        coords()/itemconfigure() on items that already exist.
        """
        # Smoothly lerp shown color toward the threshold target.  Runs every
        # frame regardless of whether the chart is visible, so the accent
        # strip and value text stay consistent even during the "collecting
        # data" phase before two samples exist.
        self._shown_color = _lerp_color(
            self._shown_color, self._target_color, _COLOR_LERP_PER_FRAME)
        shown = self._shown_color

        pulse = 0.5 + 0.5 * math.sin(now * 2.0 * math.pi / _PULSE_PERIOD_SECONDS)
        if self._is_alert:
            accent_fill = _lerp_color(_darken(shown, 0.78), shown, pulse)
        else:
            accent_fill = shown

        try:
            self.canvas.itemconfigure(self._value_id, fill=shown)
            self.canvas.itemconfigure(self._accent_id, fill=accent_fill)
        except Exception:
            pass

        if not sample_log or len(sample_log) < 2:
            try:
                self.canvas.itemconfigure(self._chart_poly,      state="hidden")
                self.canvas.itemconfigure(self._chart_glow,      state="hidden")
                self.canvas.itemconfigure(self._chart_line,      state="hidden")
                self.canvas.itemconfigure(self._chart_dot_outer, state="hidden")
                self.canvas.itemconfigure(self._chart_dot,       state="hidden")
            except Exception:
                pass
            return

        cutoff = now - window_seconds
        pts_raw = [(t, v) for (t, v) in sample_log
                   if t >= cutoff and v is not None]
        if len(pts_raw) < 2:
            try:
                self.canvas.itemconfigure(self._chart_poly,      state="hidden")
                self.canvas.itemconfigure(self._chart_glow,      state="hidden")
                self.canvas.itemconfigure(self._chart_line,      state="hidden")
                self.canvas.itemconfigure(self._chart_dot_outer, state="hidden")
                self.canvas.itemconfigure(self._chart_dot,       state="hidden")
            except Exception:
                pass
            return

        x0, x1 = self._chart_x0, self._chart_x1
        y0, y1 = self._chart_y0, self._chart_y1
        cw = x1 - x0
        ch = y1 - y0

        vals = [v for _, v in pts_raw]
        mn = min(vals)
        mx = max(vals)
        span = max(mx - mn, 0.5)

        pts = []
        for t, v in pts_raw:
            x_frac = (t - cutoff) / window_seconds
            if x_frac < 0.0:
                x_frac = 0.0
            elif x_frac > 1.0:
                x_frac = 1.0
            x = x0 + x_frac * cw
            y = y1 - ((v - mn) / span) * ch
            if y < y0:
                y = y0
            elif y > y1:
                y = y1
            pts.append((x, y))

        line_coords = [c for pt in pts for c in pt]
        poly_coords = line_coords + [pts[-1][0], y1, pts[0][0], y1]
        cx, cy = pts[-1]

        fill_col = _darken(shown, 0.24)
        glow_col = _darken(shown, 0.45)
        outer_col = _darken(shown, 0.40)

        # Breathing dot — radius oscillates 1.4 → 2.3 over the pulse period.
        dot_r   = 1.4 + 0.9 * pulse
        outer_r = dot_r + 1.5

        try:
            self.canvas.coords(self._chart_poly, *poly_coords)
            self.canvas.itemconfigure(self._chart_poly,
                                      fill=fill_col, state="normal")
            self.canvas.coords(self._chart_glow, *line_coords)
            self.canvas.itemconfigure(self._chart_glow,
                                      fill=glow_col, state="normal")
            self.canvas.coords(self._chart_line, *line_coords)
            self.canvas.itemconfigure(self._chart_line,
                                      fill=shown, state="normal")
            self.canvas.coords(self._chart_dot_outer,
                               cx - outer_r, cy - outer_r,
                               cx + outer_r, cy + outer_r)
            self.canvas.itemconfigure(self._chart_dot_outer,
                                      fill=outer_col, state="normal")
            self.canvas.coords(self._chart_dot,
                               cx - dot_r, cy - dot_r,
                               cx + dot_r, cy + dot_r)
            self.canvas.itemconfigure(self._chart_dot,
                                      fill=shown, state="normal")
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Profile badge — mini chamfered pill matching HUD cell geometry
# ─────────────────────────────────────────────────────────────────────────────

class _ProfileBadge:
    """Profile badge with a live system clock stacked below the profile name.

    Profile label sits in the upper third, clock ("H:MM:SS AM/PM") fills the
    lower portion in the HUD-cell value font so it reads clearly across the
    room.  Cell width is sized to the wider of "STREAMING" or "11:59:59 PM".
    """
    _CHAMFER = 5

    def __init__(self, parent, size_preset: dict, target_h: int, bg: str = BG):
        accent_w = size_preset["accent"]
        pad      = 6

        self._profile_font = tkFont.Font(
            family="Consolas", size=size_preset["label"], weight="bold")
        self._clock_font = tkFont.Font(
            family="Consolas", size=size_preset["value"], weight="bold")

        # Size for whichever string is wider.  Clock width dominates most of
        # the time ("11:59:59 PM" is ~12 chars at the value-font size).
        profile_w = self._profile_font.measure("STREAMING")
        clock_w   = self._clock_font.measure("11:59:59 PM")
        text_w    = int(max(profile_w, clock_w) * 1.05) + 2

        cell_w = accent_w + pad + text_w + pad
        cell_h = target_h

        self.canvas = tk.Canvas(
            parent, width=cell_w, height=cell_h,
            bg=bg, highlightthickness=0, bd=0, cursor="fleur"
        )

        ch = self._CHAMFER
        self.canvas.create_polygon(
            [0, 0,
             cell_w - ch, 0,
             cell_w, ch,
             cell_w, cell_h,
             0, cell_h],
            fill=CELL_BG, outline=CELL_OUTLINE, width=1
        )
        self._accent = self.canvas.create_rectangle(
            0, 1, accent_w, cell_h - 1,
            fill=_PROFILE_COLORS["idle"], outline=""
        )
        # Stack the profile label and the clock, centered as a group within
        # the badge.  This nudges the profile name above center (satisfying
        # "move it up") and gives the clock room below at the value-font size.
        text_cx   = accent_w + pad + text_w / 2
        profile_h = self._profile_font.metrics("linespace")
        clock_h   = self._clock_font.metrics("linespace")
        mid_gap   = 2
        group_h   = profile_h + mid_gap + clock_h
        start_y   = max(2, (cell_h - group_h) // 2)

        self._text = self.canvas.create_text(
            text_cx, start_y + profile_h / 2,
            text="IDLE", anchor="center",
            fill="#aaaabf", font=self._profile_font
        )
        self._clock = self.canvas.create_text(
            text_cx, start_y + profile_h + mid_gap + clock_h / 2,
            text="--:--:-- --", anchor="center",
            fill="#e0e0f0", font=self._clock_font
        )

    @property
    def widget(self):
        return self.canvas

    def pack(self, **kw):
        self.canvas.pack(**kw)

    def bind(self, *args, **kw):
        return self.canvas.bind(*args, **kw)

    def destroy(self):
        try:
            self.canvas.destroy()
        except Exception:
            pass

    def update(self, text: str, accent_color: str, fg: str = "#aaaabf"):
        try:
            self.canvas.itemconfig(self._accent, fill=accent_color)
            self.canvas.itemconfig(self._text, text=text, fill=fg)
        except Exception:
            pass

    def update_clock(self, clock_text: str):
        try:
            self.canvas.itemconfig(self._clock, text=clock_text)
        except Exception:
            pass


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
    """Tear down the sensor bar.  Safe to call from any thread."""
    global _instance
    if not _instance:
        return
    root = _instance.root
    try:
        root.after(0, root.destroy)
    except Exception:
        pass
    try:
        root.quit()
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
        "nvme_temp", "nvme_temp2", "fan_rpm",
        "ram_usage", "cpu_load", "gpu_load", "gpu_vram",
        "cpu_watts", "gpu_watts",
        "gpu_fan", "cpu_freq", "gpu_clock",
        "battery", "net_io", "disk_io",
    ]

    # Shell windows that GetForegroundWindow may briefly return — excluded
    # from the fullscreen-active check so transient system surfaces don't
    # hide the bar.
    _SHELL_CLASSES = {
        "Progman",
        "WorkerW",
        "Shell_TrayWnd",
        "Shell_SecondaryTrayWnd",
        "Windows.UI.Core.CoreWindow",
        "XamlExplorerHostIslandWindow",
        "ApplicationFrameWindow",
        "TaskListThumbnailWnd",
        "MultitaskingViewFrame",
    }

    def __init__(self):
        # Read the currently-selected Settings theme and map it into the
        # bar's palette before any widget is built.
        theme_name = "Void"
        try:
            theme_name = cfg.get_value("display", "settings_theme", default="Void")
            apply_theme(theme_name)
        except Exception:
            pass
        self._last_theme = theme_name

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self._last_alpha = self._read_alpha()
        self.root.attributes("-alpha", self._last_alpha)
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        self._drag_x      = 0
        self._drag_y      = 0
        self._cells       = {}    # config_key → {"hud": _HudCell, "getter": fn, "unit": str, "label": str}
        self._history     = {}    # config_key → deque of value floats (for sparkline)
        self._sample_log  = {}    # config_key → deque of (t_monotonic, value) for inline chart
        self._cell_colors = {}    # config_key → last threshold color (used by draw loop)
        self._fs_hidden   = False
        self._fs_count    = 0

        # Perimeter comet state — driven by _draw_frame
        self._cpu_load_cache  = 0.0
        self._perimeter_phase = 0.0
        self._last_draw_time  = time.monotonic()
        self._last_inner_req  = (0, 0)
        self._last_clock_sec  = -1
        self._gpu_warn_w, self._gpu_hot_w = self._read_gpu_tdp_thresholds()

        saved_scale = cfg.get_value("display", "bar_scale", default=_SCALE_DEFAULT)
        try:
            self._scale = max(_SCALE_MIN, min(_SCALE_MAX, float(saved_scale)))
        except (TypeError, ValueError):
            self._scale = _SCALE_DEFAULT

        # Drag-to-resize state.  `_resize_axis` is "x", "y", or None when the
        # pointer is not hovering a resize edge.  `_resizing` latches at mouse
        # press so the subsequent drag moves resize instead of repositioning.
        self._resize_axis      = None
        self._resizing         = False
        self._resize_start     = None     # (start_root_xy, start_w, start_h, start_scale)
        self._current_cursor   = ""
        self._last_rebuild_scale = self._scale

        self._orient      = cfg.get_value("display", "bar_orientation", default="horizontal")
        self._last_orient = self._orient

        self._build()
        self._restore_position()
        self._apply_rounded_corners()

        if cfg.get_value("display", "bar_hidden", default=False):
            self.root.withdraw()
        self._update()
        self._draw_frame()

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

    # ── Orientation helpers ───────────────────────────────────────────────────

    def _pack_side(self) -> str:
        return "top" if self._orient == "vertical" else "left"

    def _cell_pad_kw(self, gap: int) -> dict:
        """Pack padding kwargs for per-cell spacing based on orientation."""
        half = gap // 2
        if self._orient == "vertical":
            return {"pady": (half, gap - half)}
        return {"padx": (half, gap - half)}

    def _sync_outer_size(self):
        """Size the outer canvas to fit inner's reqsize + the comet ring."""
        try:
            self.root.update_idletasks()
            reqw = self.inner.winfo_reqwidth()  + 2 * _RING_PX
            reqh = self.inner.winfo_reqheight() + 2 * _RING_PX
            if reqw > 2 * _RING_PX and reqh > 2 * _RING_PX:
                self._outer_canvas.config(width=reqw, height=reqh)
        except Exception:
            pass

    def _on_inner_configure(self, event=None):
        """Keep outer canvas sized to inner whenever its reqsize changes."""
        try:
            rw = self.inner.winfo_reqwidth()
            rh = self.inner.winfo_reqheight()
            if (rw, rh) != self._last_inner_req:
                self._last_inner_req = (rw, rh)
                self._sync_outer_size()
        except Exception:
            pass

    def _cell_height(self, size_preset: dict) -> int:
        """Compute the cell height that a _HudCell would produce — used to
        size the profile badge so it aligns with sensor cells."""
        lf = tkFont.Font(family="Consolas", size=size_preset["label"], weight="bold")
        vf = tkFont.Font(family="Consolas", size=size_preset["value"], weight="bold")
        return (3 + lf.metrics("linespace") + 2 +
                vf.metrics("linespace") + 2 + size_preset["chart_h"] + 3)

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self):
        s = _scaled_preset(self._scale)

        # Outer is a Canvas so we can draw the animated perimeter comet on
        # the _RING_PX-wide border ring.  Canvas bg is BORDER; inner (packed
        # with padx=_RING_PX, pady=_RING_PX) covers all but that ring, so
        # comet segments are visible only along the edge.
        outer = tk.Canvas(self.root, bg=BORDER,
                          highlightthickness=0, bd=0,
                          width=400, height=50)
        outer.pack_propagate(False)
        outer.pack(fill="both", expand=True)
        self._outer_canvas = outer

        self.inner = tk.Frame(outer, bg=BG,
                              padx=s["pad_x"], pady=s["pad_y"])
        self.inner.pack(fill="both", expand=True,
                        padx=_RING_PX, pady=_RING_PX)

        # Pre-allocate comet tail segments — each is a 3-point polyline so
        # coords() stays a fixed length when the segment spans a corner.
        # Line width 2 fits neatly inside the 5-px ring with 1 px of margin.
        self._perimeter_segments = []
        for _ in range(_PERIMETER_SEGMENTS):
            seg_id = outer.create_line(
                0, 0, 0, 0, 0, 0,
                fill=BORDER, width=2, state="hidden",
                capstyle="round", joinstyle="round"
            )
            self._perimeter_segments.append(seg_id)

        for w in [self.root, outer, self.inner]:
            w.bind("<ButtonPress-1>",   self._drag_start)
            w.bind("<B1-Motion>",       self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)
            w.bind("<Button-3>",        self._show_context_menu)
            w.bind("<Motion>",          self._on_motion, add="+")

        self.inner.bind("<Configure>", self._on_inner_configure, add="+")

        self._build_header(s)
        self._build_sensor_cells()
        self._sync_outer_size()

    def _build_header(self, s: dict):
        """Grip + profile badge at the leading edge of the bar."""
        grip_font_size = max(s["label"], 9)
        grip = tk.Label(self.inner, text="⫶", font=("Consolas", grip_font_size, "bold"),
                        bg=BG, fg="#3a3a4a", cursor="fleur", padx=4)
        grip.pack(side=self._pack_side())
        for ev, cb in [("<ButtonPress-1>",   self._drag_start),
                       ("<B1-Motion>",       self._drag_move),
                       ("<ButtonRelease-1>", self._drag_end),
                       ("<Button-3>",        self._show_context_menu),
                       ("<Motion>",          self._on_motion)]:
            grip.bind(ev, cb)

        target_h = self._cell_height(s)
        self.profile_badge = _ProfileBadge(self.inner, s, target_h, bg=BG)
        pad_kw = self._cell_pad_kw(s["gap"])
        self.profile_badge.pack(side=self._pack_side(), **pad_kw)
        for ev, cb in [("<ButtonPress-1>",   self._drag_start),
                       ("<B1-Motion>",       self._drag_move),
                       ("<ButtonRelease-1>", self._drag_end),
                       ("<Button-3>",        self._show_context_menu),
                       ("<Motion>",          self._on_motion)]:
            self.profile_badge.bind(ev, cb)

    # ── Sensor cells ──────────────────────────────────────────────────────────

    def _build_sensor_cells(self):
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
        # Destroy any old HudCells too
        for entry in self._cells.values():
            try:
                entry["hud"].destroy()
            except Exception:
                pass
        self._cells = {}

        sensor_map = {
            "cpu_temp":    ("CPU",  self._get_cpu,          "°"),
            "gpu_temp":    ("GPU",  self._get_gpu,          "°"),
            "gpu_hotspot": ("GHOT", self._get_gpu_hotspot,  "°"),
            "gpu_mem_temp":("GMEM", self._get_gpu_mem_temp, "°"),
            "nvme_temp":   ("NVM1", self._get_nvme,         "°"),
            "nvme_temp2":  ("NVM2", self._get_nvme2,        "°"),
            "fan_rpm":     ("FAN",  self._get_fan_or_dimm,  "rpm"),
            "ram_usage":   ("RAM",  self._get_ram,          "%"),
            "cpu_load":    ("CPU%", self._get_cpu_load,     "%"),
            "gpu_load":    ("GPU%", self._get_gpu_load,     "%"),
            "gpu_vram":    ("VRAM", self._get_vram,         "%"),
            "cpu_watts":   ("CPUW", self._get_cpu_watts,    "W"),
            "gpu_watts":   ("GPUW", self._get_gpu_watts,    "W"),
            "gpu_fan":     ("GFAN", self._get_gpu_fan,      "%"),
            "cpu_freq":    ("CFRQ", self._get_cpu_freq,     "GHz"),
            "gpu_clock":   ("GCLK", self._get_gpu_clock,    "MHz"),
            "battery":     ("BAT",  self._get_battery,      "%"),
            "net_io":      ("NET",  self._get_net_io,       "MB/s"),
            "disk_io":     ("DISK", self._get_disk_io,      "MB/s"),
        }

        s        = _scaled_preset(self._scale)
        pad_kw   = self._cell_pad_kw(s["gap"])
        ordered  = self._get_sensor_order(sens)

        for config_key in ordered:
            if config_key not in sensor_map:
                continue
            label, getter, unit = sensor_map[config_key]

            hud = _HudCell(self.inner, config_key, label, unit, s, bg=BG)
            hud.pack(side=self._pack_side(), **pad_kw)
            self._cell_widgets.append(hud.canvas)

            # Drag + right-click menu + double-click sparkline + hover motion
            hud.bind("<ButtonPress-1>",   self._drag_start, add="+")
            hud.bind("<B1-Motion>",       self._drag_move, add="+")
            hud.bind("<ButtonRelease-1>", self._drag_end, add="+")
            hud.bind("<Motion>",          self._on_motion, add="+")
            hud.bind("<Button-3>",
                     lambda e, k=config_key: self._show_sensor_menu(e, k))
            hud.bind("<Double-Button-1>",
                     lambda e, k=config_key, lb=label, u=unit:
                         self._open_sparkline(k, lb, u))

            _Tooltip(hud.canvas,
                     lambda lb=label, k=config_key: self._tooltip_text(lb, k))

            self._cells[config_key] = {
                "hud":    hud,
                "getter": getter,
                "unit":   unit,
                "label":  label,
            }
            if config_key not in self._history:
                self._history[config_key] = collections.deque(maxlen=90)
            if config_key not in self._sample_log:
                self._sample_log[config_key] = collections.deque(maxlen=_SAMPLE_LOG_MAXLEN)
            self._cell_colors.setdefault(config_key, COLOR_COOL)

        # After cells are (re)built, resize the outer canvas so the perimeter
        # comet has the correct track length.
        if hasattr(self, "_outer_canvas"):
            self._sync_outer_size()

    def _tooltip_text(self, label: str, config_key: str) -> str:
        if config_key == "net_io":
            unit    = self._cell_unit(config_key)
            dn_hist = self._history.get("net_io")
            up_hist = self._history.get("net_io_up")
            dn = next((v for v in reversed(dn_hist) if v is not None), None) \
                if dn_hist else None
            up = next((v for v in reversed(up_hist) if v is not None), None) \
                if up_hist else None
            if dn is None and up is None:
                return label
            dn_s = f"{dn:.1f}" if dn is not None else "—"
            up_s = f"{up:.1f}" if up is not None else "—"
            return f"{label}  ↓ {dn_s} {unit}   ↑ {up_s} {unit}"
        hist = self._history.get(config_key)
        if hist:
            vals = [v for v in hist if v is not None]
            if vals:
                unit = self._cell_unit(config_key)
                return f"{label}  {vals[-1]:.1f} {unit}"
        return label

    def _cell_unit(self, config_key: str) -> str:
        """Return the current display unit for a cell.  For net_io this is
        user-configurable (MB/s / Mbps / kbps) and must be looked up live."""
        if config_key == "net_io":
            return cfg.get_value("display", "net_unit", default="MB/s")
        return self._cells.get(config_key, {}).get("unit", "")

    def _open_sparkline(self, config_key: str, label: str, unit: str):
        if config_key == "net_io":
            u       = self._cell_unit(config_key)
            dn_hist = self._history.get("net_io",    collections.deque())
            up_hist = self._history.get("net_io_up", collections.deque())
            _Sparkline.show("net_io",    f"{label} ↓ Download",
                            dn_hist, u, position_idx=0, position_count=2)
            _Sparkline.show("net_io_up", f"{label} ↑ Upload",
                            up_hist, u, position_idx=1, position_count=2)
            return
        hist = self._history.get(config_key, collections.deque())
        _Sparkline.show(config_key, label, hist, self._cell_unit(config_key))

    def _set_orientation(self, orient: str):
        cfg.set_value("display", "bar_orientation", value=orient)
        # _update loop detects and rebuilds

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
        t = nvmes[0]["temp_c"]
        warn = thresh.get("nvme_warn", 60)
        crit = thresh.get("nvme_crit", 70)
        return sensors.fmt_temp(t), self._temp_color(t, warn, crit)

    def _get_nvme2(self, readings, thresh):
        nvmes = readings.get("nvme_temps", [])
        if len(nvmes) < 2:
            return "---", COLOR_COOL
        t = nvmes[1]["temp_c"]
        warn = thresh.get("nvme_warn", 60)
        crit = thresh.get("nvme_crit", 70)
        return sensors.fmt_temp(t), self._temp_color(t, warn, crit)

    def _get_fan_or_dimm(self, readings, thresh):
        """
        Returns (label, text, color) — 3-tuple so the cell can switch its label
        between FAN and DIMM depending on which data source is available.
        """
        if readings.get("awcc_available") and readings.get("awcc_fans"):
            rpms = [f["rpm"] for f in readings["awcc_fans"]
                    if f.get("rpm") is not None]
            if rpms and max(rpms) > 0:
                mx    = max(rpms)
                text  = f"{mx/1000:.1f}K" if mx >= 1000 else str(mx)
                color = (COLOR_HOT  if mx > 4500 else
                         COLOR_WARM if mx > 3000 else COLOR_COOL)
                return "FAN", text, color

        lhm_fans = readings.get("lhm_fan_rpms", [])
        if lhm_fans:
            mx    = max(f["rpm"] for f in lhm_fans)
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
        # Internal reading is MiB/s (bytes ÷ 2^20).  Convert to the unit
        # the user picked; factors are precise so values match ISP reporting.
        unit = cfg.get_value("display", "net_unit", default="MB/s")
        if unit == "Mbps":
            dn *= 8.388608
            up *= 8.388608
        elif unit == "kbps":
            dn *= 8388.608
            up *= 8388.608

        def _f(v):
            if v >= 10000:
                return f"{v/1000:.0f}k"
            if v >= 1000:
                return f"{v/1000:.1f}k"
            if v >= 10:
                return f"{v:.0f}"
            return f"{v:.1f}"

        return f"↓{_f(dn)} ↑{_f(up)}", COLOR_COOL

    def _get_disk_io(self, readings, thresh):
        rd = readings.get("disk_read_mbps")
        wr = readings.get("disk_write_mbps")
        if rd is None:
            return "---", COLOR_COOL
        def _f(v): return f"{v:.0f}" if v >= 10 else f"{v:.1f}"
        color = COLOR_WARM if (rd + wr) > 500 else COLOR_COOL
        return f"R{_f(rd)} W{_f(wr)}", color

    # ── History extraction (for sparkline + inline micro-chart) ───────────────

    def _extract_numeric(self, config_key: str, readings: dict):
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
            "disk_io":     "disk_read_mbps",
        }
        if config_key in simple:
            return readings.get(simple[config_key])
        if config_key in ("net_io", "net_io_up"):
            # Store history in the user-chosen unit so tooltip/sparkline
            # agree with the cell text.  Sensor emits MiB/s internally.
            field = "net_up_mbps" if config_key == "net_io_up" else "net_down_mbps"
            val = readings.get(field)
            if val is None:
                return None
            unit = cfg.get_value("display", "net_unit", default="MB/s")
            if unit == "Mbps":
                return val * 8.388608
            if unit == "kbps":
                return val * 8388.608
            return val
        if config_key == "nvme_temp":
            nvmes = readings.get("nvme_temps", [])
            return nvmes[0]["temp_c"] if nvmes else None
        if config_key == "nvme_temp2":
            nvmes = readings.get("nvme_temps", [])
            return nvmes[1]["temp_c"] if len(nvmes) >= 2 else None
        if config_key == "gpu_vram":
            used  = readings.get("gpu_vram_used_mb")
            total = readings.get("gpu_vram_total_mb")
            if used and total and total > 0:
                return (used / total) * 100
        if config_key == "fan_rpm":
            fans = readings.get("awcc_fans", [])
            if fans:
                rpms = [f["rpm"] for f in fans if f.get("rpm")]
                mx = max(rpms) if rpms else 0
                if mx > 0:
                    return float(mx)
            lhm_fans = readings.get("lhm_fan_rpms", [])
            if lhm_fans:
                return float(max(f["rpm"] for f in lhm_fans))
            dimms = readings.get("ram_temps", [])
            return max(d["temp_c"] for d in dimms) if dimms else None
        return None

    def _feed_history(self, readings: dict):
        now = time.monotonic()
        for key in self._cells:
            if key not in self._history:
                self._history[key] = collections.deque(maxlen=_SPARK_HISTORY_MAXLEN)
            if key not in self._sample_log:
                self._sample_log[key] = collections.deque(maxlen=_SAMPLE_LOG_MAXLEN)
            val = self._extract_numeric(key, readings)
            self._history[key].append(val)
            if val is not None:
                self._sample_log[key].append((now, float(val)))
        # Track upload separately so the tooltip can show both directions
        # and the user can open a standalone upload sparkline window.
        if "net_io" in self._cells:
            if "net_io_up" not in self._history:
                self._history["net_io_up"] = collections.deque(maxlen=_SPARK_HISTORY_MAXLEN)
            up_val = self._extract_numeric("net_io_up", readings)
            self._history["net_io_up"].append(up_val)

    # ── Fullscreen auto-hide ──────────────────────────────────────────────────

    def _is_fullscreen_active(self) -> bool:
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return False

            own = ctypes.windll.user32.GetParent(self.root.winfo_id()) \
                  or int(self.root.winfo_id())
            if hwnd == own:
                return False

            cls_buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetClassNameW(hwnd, cls_buf, 256)
            if cls_buf.value in self._SHELL_CLASSES:
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
                if not cfg.get_value("display", "bar_hidden", default=False):
                    self.root.deiconify()
                self._fs_hidden = False
                self._fs_count = 0
            return

        fs = self._is_fullscreen_active()
        hidden = cfg.get_value("display", "bar_hidden", default=False)

        if fs:
            self._fs_count = min(self._fs_count + 1, 2)
        else:
            self._fs_count = max(self._fs_count - 1, 0)

        if self._fs_count >= 2 and not self._fs_hidden and not hidden:
            self.root.withdraw()
            self._fs_hidden = True
        elif self._fs_count == 0 and self._fs_hidden:
            if not hidden:
                self.root.deiconify()
            self._fs_hidden = False

    # ── Profile badge update ─────────────────────────────────────────────────

    def _profile_color(self, profile: str) -> str:
        color = _PROFILE_COLORS.get(profile, "#444455")
        for up in cfg.get().get("profiles", {}).get("user_profiles", []):
            if up.get("name") == profile:
                color = up.get("color", "#7700cc")
                break
        return color

    # ── Update loop ───────────────────────────────────────────────────────────

    def _update(self):
        try:
            self._check_config_changed()
            self._apply_alpha_if_changed()
            self._apply_theme_if_changed()
            c        = cfg.get()
            readings = sensors.get_readings()
            thresh   = c.get("thresholds", {})
            profile  = profiles.get_current()
            sens     = c.get("sensors", {})

            orient_now = cfg.get_value("display", "bar_orientation", default="horizontal")
            if orient_now != self._last_orient:
                self._orient      = orient_now
                self._last_orient = orient_now
                self._rebuild_bar_widgets()

            # Auto-hide NVM2 when no second NVMe drive is present, even if
            # the user enabled it in config — no point reserving a cell for
            # a drive that doesn't exist.
            effective_sens = dict(sens)
            if len(readings.get("nvme_temps", [])) < 2:
                effective_sens["nvme_temp2"] = False

            enabled_now = tuple(
                k for k in self._SENSOR_DEFS if effective_sens.get(k, False)
            )
            if enabled_now != getattr(self, "_last_enabled", None):
                self._rebuild_sensor_cells(effective_sens)
                self._last_enabled = enabled_now

            self._feed_history(readings)

            cpu_val = readings.get("cpu_load_pct")
            if cpu_val is not None:
                self._cpu_load_cache = float(cpu_val)

            # Profile badge
            try:
                from core import turbo_cool
                if turbo_cool.is_active():
                    tc_text = (turbo_cool.status_text() or "COOL")[:9].upper()
                    self.profile_badge.update(tc_text, "#00ccdd", fg="#00e8ff")
                else:
                    self.profile_badge.update(
                        profile.upper()[:9],
                        self._profile_color(profile),
                        fg="#cccce0"
                    )
            except Exception:
                try:
                    self.profile_badge.update(
                        profile.upper()[:9],
                        self._profile_color(profile),
                        fg="#cccce0"
                    )
                except Exception:
                    pass

            # Sensor cells — value text + accent color update at poll rate.
            # The inline chart is driven by _draw_frame() at ~30 fps.
            # When the lhm_bridge has missed one or more polls (typically
            # during an all-core CPU stress test), sensors.py serves the
            # last good values for up to 30 s with a non-zero
            # readings["lhm_stale_age_s"]; dim the LHM-sourced cells so the
            # user sees "recent but stale" data rather than "---".
            stale_age = readings.get("lhm_stale_age_s") or 0.0
            for key, cell in self._cells.items():
                try:
                    result = cell["getter"](readings, thresh)
                    if len(result) == 3:
                        new_label, text, color = result
                        cell["hud"].update_label(new_label)
                    else:
                        text, color = result
                    if (stale_age > 0
                            and key in _LHM_SENSOR_KEYS
                            and text != "---"):
                        color = _darken(color, 0.55)
                    cell["hud"].update_value(text, color)
                    self._cell_colors[key] = color
                except Exception:
                    pass

            if not _Sparkline._open:
                self._check_fullscreen_hide()

        except Exception as e:
            logger.debug("Bar update error: %s", e)

        if self.root.winfo_exists():
            self.root.after(int(self._get_interval_ms()), self._update)

    def _draw_frame(self):
        """Redraw inline charts + perimeter comet at CHART_DRAW_INTERVAL_MS.

        Sensor reads are NOT performed here — we just reuse the time-indexed
        sample log.  Since x-position is derived from (now - t_sample), every
        frame shifts the line a fraction of a pixel leftward and new samples
        appear to rise into the right edge.  The perimeter comet advances by
        speed * dt pixels each frame, so motion is smooth regardless of the
        poll rate.  Cost is dominated by coords()/itemconfigure() calls on
        pre-allocated canvas items — no create/delete per frame.
        """
        try:
            now = time.monotonic()
            dt  = max(0.0, min(0.1, now - self._last_draw_time))
            self._last_draw_time = now

            if not self._fs_hidden and self.root.state() != "withdrawn":
                for key, cell in self._cells.items():
                    log = self._sample_log.get(key)
                    if not log:
                        continue
                    color = self._cell_colors.get(key, COLOR_COOL)
                    cell["hud"].render_chart(log, now, color)

                self._render_perimeter_comet(dt)
                self._tick_clock()
        except Exception as e:
            logger.debug("Bar draw frame error: %s", e)

        if self.root.winfo_exists():
            self.root.after(CHART_DRAW_INTERVAL_MS, self._draw_frame)

    def _tick_clock(self):
        """Update the profile-badge clock text when the wall-clock second
        rolls over.  Called from _draw_frame (~30 fps) but the itemconfig
        call only fires once per second."""
        try:
            local = time.localtime()
            if local.tm_sec == self._last_clock_sec:
                return
            self._last_clock_sec = local.tm_sec
            hour12 = local.tm_hour % 12 or 12
            suffix = "AM" if local.tm_hour < 12 else "PM"
            text = f"{hour12}:{local.tm_min:02d}:{local.tm_sec:02d} {suffix}"
            self.profile_badge.update_clock(text)
        except Exception:
            pass

    def _render_perimeter_comet(self, dt: float):
        """Advance the phase and repaint the tail segments.

        Speed scales linearly with cached CPU load (0 % → min, 100 % → max),
        so the comet visibly quickens during heavy work and drifts during
        idle — doubling as an ambient load indicator.
        """
        try:
            outer = self._outer_canvas
            inset = _COMET_INSET
            # Track rectangle is centered in the ring (inset pixels from each
            # outer edge).  W/H below are the track's inner width/height.
            W = outer.winfo_width()  - 1 - 2 * inset
            H = outer.winfo_height() - 1 - 2 * inset
            if W <= 4 or H <= 4:
                return

            P = 2.0 * (W + H)
            cpu   = max(0.0, min(100.0, self._cpu_load_cache))
            speed = _COMET_SPEED_MIN + (_COMET_SPEED_MAX - _COMET_SPEED_MIN) * (cpu / 100.0)
            self._perimeter_phase = (self._perimeter_phase + speed * dt) % P

            head = self._perimeter_phase
            step = float(_COMET_SEGMENT_PX)
            N    = len(self._perimeter_segments)
            denom = float(max(N - 1, 1))

            for i, seg_id in enumerate(self._perimeter_segments):
                t_lead  = head - i * step
                t_trail = head - (i + 1) * step
                pts = _perimeter_segment_pts(t_trail, t_lead, W, H)
                flat = []
                for px, py in pts:
                    flat.append(px + inset)
                    flat.append(py + inset)
                # Ease-out fade so the head reads bright and the tail dissolves
                # into the BORDER-colored ring with no visible cut-off.
                frac  = (i / denom) ** 0.6
                color = _lerp_color(_COMET_HEAD_COLOR, BORDER, frac)
                outer.coords(seg_id, *flat)
                outer.itemconfigure(seg_id, fill=color, state="normal")
        except Exception:
            pass

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

    def _read_alpha(self) -> float:
        """Resolve the bar window alpha from the Display → Overlay opacity
        slider (display.overlay_opacity), clamped to the Tk-supported range."""
        try:
            raw = float(cfg.get_value("display", "overlay_opacity", default=0.94))
        except (TypeError, ValueError):
            raw = 0.94
        return max(0.15, min(1.0, raw))

    def _apply_alpha_if_changed(self):
        """Re-apply the bar window alpha when the slider value changed.
        Called once per _update tick after _check_config_changed has reloaded
        the config, so Display → Overlay opacity takes effect live."""
        new_alpha = self._read_alpha()
        if abs(new_alpha - getattr(self, "_last_alpha", -1.0)) < 1e-3:
            return
        try:
            self.root.attributes("-alpha", new_alpha)
            self._last_alpha = new_alpha
        except Exception:
            pass

    def _apply_theme_if_changed(self):
        """Live-retint the bar when Settings (a separate subprocess) writes
        a new display.settings_theme into config.  Called from _update right
        after _check_config_changed reloads the file."""
        try:
            new_theme = cfg.get_value("display", "settings_theme", default="Void")
        except Exception:
            return
        if new_theme == getattr(self, "_last_theme", None):
            return
        self._last_theme = new_theme
        try:
            apply_theme(new_theme)        # rewrites module palette
            self._reapply_theme()         # re-tints live widgets + rebuilds cells
        except Exception as e:
            logger.debug("bar theme apply failed: %s", e)

    def _get_interval_ms(self) -> int:
        c    = cfg.get()
        unit = c.get("display", {}).get("update_interval_unit", "seconds")
        val  = c.get("display", {}).get("update_interval_value", 2.0)
        if unit == "milliseconds":
            return max(100, int(val))
        return max(500, int(float(val) * 1000))

    # ── Drag / drag-to-resize ────────────────────────────────────────────────

    def _resize_axis_for_event(self, event) -> str:
        """Return 'x' or 'y' if event lands on the active resize edge, else ''.

        Horizontal bars resize from the bottom edge (height-axis drag scales
        everything up/down).  Vertical bars resize from the right edge.
        """
        try:
            # Translate to window-local coordinates.
            wx = event.x_root - self.root.winfo_rootx()
            wy = event.y_root - self.root.winfo_rooty()
            w  = self.root.winfo_width()
            h  = self.root.winfo_height()
        except Exception:
            return ""
        # Scale the hotzone with the bar — at _SCALE_MIN a 7-px zone is a
        # large fraction of the bar; at _SCALE_MAX it becomes fiddly.
        edge = max(5, int(round(_RESIZE_EDGE_PX * self._scale)))
        if self._orient == "vertical":
            if 0 <= wy <= h and (w - edge) <= wx <= w:
                return "x"
        else:
            if 0 <= wx <= w and (h - edge) <= wy <= h:
                return "y"
        return ""

    def _set_cursor(self, cur: str):
        if cur == self._current_cursor:
            return
        self._current_cursor = cur
        try:
            self.root.configure(cursor=cur)
        except Exception:
            pass

    def _on_motion(self, event):
        """Hover feedback — switch to a resize cursor near the draggable edge."""
        if self._resizing:
            return
        axis = self._resize_axis_for_event(event)
        if axis == "y":
            self._set_cursor("sb_v_double_arrow")
        elif axis == "x":
            self._set_cursor("sb_h_double_arrow")
        else:
            self._set_cursor("")

    def _drag_start(self, event):
        axis = self._resize_axis_for_event(event)
        if axis:
            self._resizing     = True
            self._resize_axis  = axis
            self._resize_start = (
                event.x_root, event.y_root,
                self.root.winfo_width(), self.root.winfo_height(),
                self._scale,
            )
            return
        self._resizing    = False
        self._resize_axis = None
        self._drag_x = event.x
        self._drag_y = event.y

    def _drag_move(self, event):
        if self._resizing and self._resize_start is not None:
            sx, sy, sw, sh, sscale = self._resize_start
            if self._resize_axis == "y":
                delta    = event.y_root - sy
                ratio    = max(0.4, (sh + delta) / max(sh, 1))
            else:
                delta    = event.x_root - sx
                ratio    = max(0.4, (sw + delta) / max(sw, 1))
            new_scale = max(_SCALE_MIN, min(_SCALE_MAX, sscale * ratio))
            # Throttle: only rebuild when the scale actually moves meaningfully.
            if abs(new_scale - self._last_rebuild_scale) >= 0.03:
                self._apply_scale(new_scale, persist=False)
            return
        x = self.root.winfo_x() + event.x - self._drag_x
        y = self.root.winfo_y() + event.y - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    def _drag_end(self, event):
        if self._resizing:
            self._resizing     = False
            self._resize_axis  = None
            self._resize_start = None
            self._set_cursor("")
            cfg.set_value("display", "bar_scale", value=round(self._scale, 3))
            return
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

    def _apply_scale(self, new_scale: float, *, persist: bool = True):
        """Set a new bar scale and rebuild all cells at the new font sizes."""
        new_scale = max(_SCALE_MIN, min(_SCALE_MAX, float(new_scale)))
        if abs(new_scale - self._scale) < 1e-3:
            return
        self._scale = new_scale
        self._last_rebuild_scale = new_scale
        if persist:
            cfg.set_value("display", "bar_scale", value=round(new_scale, 3))
        s = _scaled_preset(self._scale)
        self.inner.config(padx=s["pad_x"], pady=s["pad_y"])
        self._rebuild_bar_widgets()

    def _rebuild_bar_widgets(self):
        for w in self.inner.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass
        # Drop HUD references — they pointed at now-destroyed canvases.
        self._cells        = {}
        self._cell_widgets = []
        self._last_enabled = None

        s = _scaled_preset(self._scale)
        self._build_header(s)
        # Rebuild sensor cells immediately so drag-to-resize feels live —
        # waiting for the next _update tick (~2 s) would look janky.
        try:
            self._rebuild_sensor_cells(cfg.get().get("sensors", {}))
        except Exception:
            pass
        self._sync_outer_size()

    def _reapply_theme(self):
        """Reskin the live bar after apply_theme() rewrote the module palette.

        Re-tints the root + outer ring + inner frame, then rebuilds the cells
        so every HUD canvas picks up the new CELL_BG / CELL_OUTLINE / FG_*.
        """
        try:
            self.root.configure(bg=BG)
        except Exception:
            pass
        try:
            if getattr(self, "_outer_canvas", None):
                self._outer_canvas.configure(bg=BORDER)
        except Exception:
            pass
        try:
            if getattr(self, "inner", None):
                self.inner.configure(bg=BG)
        except Exception:
            pass
        # Recolor the pre-allocated comet segments — they persist across
        # rebuilds and keep a fill reference from the old palette otherwise.
        for seg_id in getattr(self, "_perimeter_segments", []):
            try:
                self._outer_canvas.itemconfigure(seg_id, fill=BORDER)
            except Exception:
                pass
        self._rebuild_bar_widgets()

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
        menu.add_command(
            label="Reset Bar Size",
            command=lambda: self._apply_scale(_SCALE_DEFAULT),
        )

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
        menu.add_command(label="Profile: Working",
                         command=lambda: profiles.set_manual_override("working"))
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
