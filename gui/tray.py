"""
AlienCore - tray.py
One individual system tray icon per enabled sensor, all lined up side by side.
Each icon shows its reading in color (green/amber/red) and updates on a shared timer.
Right-clicking any icon shows the full AlienCore menu.
"""

import logging
import threading
import time
import pystray
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from core import config_manager as cfg, sensors, profiles
from core.constants import (
    APP_NAME, VERSION,
    COLOR_COOL, COLOR_WARM, COLOR_HOT,
)

logger = logging.getLogger("aliencore.tray")

# Active pystray Icon objects, one per sensor
_icons             = []
_running           = False
_thread            = None
_overlay           = None
_last_shown_alert  = [None]   # tracks the alert text we last toasted
_last_icon_color   = [None]   # hex color of last rendered icon (avoid re-renders)
_ICON_CACHE        = {}       # color_hex → PIL Image  (at most 4 entries)

# ─────────────────────────────────────────────────────────────────────────────
# Alien-head icon geometry  (all coordinates for a 256×256 canvas)
# ─────────────────────────────────────────────────────────────────────────────

# Head outline — smooth 20-point ellipse matched to the Alienware logo:
# vertical oval, taller than wide (rx=90, ry=108), centred at (128, 124).
# Rounded at both top and bottom — no chin point.
_HEAD_POLY = [
    (218, 124), (214,  91), (201,  61), (181,  37), (156,  21),
    (128,  16), (100,  21), ( 75,  37), ( 55,  61), ( 42,  91),
    ( 38, 124), ( 42, 157), ( 55, 188), ( 75, 211), (100, 227),
    (128, 232), (156, 227), (181, 211), (201, 188), (214, 157),
]

# Outer poly for glow (scaled 115 % from centroid)
def _scale_poly(poly, cx, cy, s):
    return [(int(cx + (x - cx) * s), int(cy + (y - cy) * s))
            for x, y in poly]

_HEAD_CENTROID   = (128, 124)
_HEAD_POLY_OUTER = _scale_poly(_HEAD_POLY, *_HEAD_CENTROID, 1.15)

# Eyes — angled teardrop/almond shapes, ~20° below horizontal.
# Outer corner sits upper-outer; inner corner lower-center.
# Computed as a rotated ellipse (semi-major a=35, semi-minor b=18)
# centred at (80, 124) for left, (176, 124) for right.
_LEFT_EYE = [
    (113, 136),  # inner tip
    ( 99, 145),  # lower inner
    ( 74, 141),  # lower center
    ( 52, 128),  # lower outer
    ( 44, 112),  # outer tip
    ( 61, 104),  # upper outer
    ( 86, 107),  # upper center
    (108, 121),  # upper inner
]
_RIGHT_EYE = [
    (143, 136),  # inner tip   (mirrored: 256 - left_x)
    (157, 145),  # lower inner
    (182, 141),  # lower center
    (204, 128),  # lower outer
    (212, 112),  # outer tip
    (195, 104),  # upper outer
    (170, 107),  # upper center
    (148, 121),  # upper inner
]

def _eye_inner(eye_poly, shrink=0.42):
    """Return a smaller version of an eye polygon for the highlight."""
    cx = sum(x for x, y in eye_poly) / len(eye_poly)
    cy = sum(y for x, y in eye_poly) / len(eye_poly)
    return [(int(cx + (x - cx) * shrink), int(cy + (y - cy) * shrink))
            for x, y in eye_poly]

# Sensor definitions — (config_key, display_label, reading_key, unit, kind)
# kind: 'temp' | 'pct' | 'list_temp' | 'list_rpm' | 'vram' | 'watts' | 'ram_temp' | 'nvme_primary' | 'nvme_secondary'
SENSOR_DEFS = [
    ("cpu_temp",    "CPU",  "cpu_temp_avg",      "°", "temp"),
    ("gpu_temp",    "GPU",  "gpu_temp",          "°", "temp"),
    ("gpu_hotspot", "GHOT", "gpu_temp_hotspot",  "°", "temp"),
    ("gpu_mem_temp","GMEM", "gpu_temp_memory",   "°", "temp"),
    ("nvme_temp",   "NVM1", "nvme_temps",        "°", "nvme_primary"),
    ("nvme_temp2",  "NVM2", "nvme_temps",        "°", "nvme_secondary"),
    ("fan_rpm",     "DIMM", "ram_temps",         "°", "ram_temp"),
    ("ram_usage",   "RAM",  "ram_usage_pct",     "%", "pct"),
    ("cpu_load",    "CPU%", "cpu_load_pct",      "%", "pct"),
    ("gpu_load",    "GPU%", "gpu_load",          "%", "pct"),
    ("gpu_vram",    "VRAM", "gpu_vram_used_mb",  "M", "vram"),
    ("cpu_watts",   "CPUW", "cpu_watts",         "W", "watts"),
    ("gpu_watts",   "GPUW", "gpu_watts",         "W", "watts"),
    ("gpu_fan",     "GFAN", "gpu_fan_pct",       "%", "pct"),
    ("cpu_freq",    "CFRQ", "cpu_freq_ghz",      "G", "freq"),
    ("gpu_clock",   "GCLK", "gpu_clock_mhz",     "M", "mhz"),
    ("battery",     "BAT",  "battery_pct",       "%", "battery"),
    ("net_io",      "NET",  "net_down_mbps",     "M", "net_io"),
    ("disk_io",     "DISK", "disk_read_mbps",    "M", "disk_io"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_app_icon(color: str = COLOR_COOL):
    """Return the AlienCore alien-head as a PIL Image (for window iconphoto)."""
    return _get_alien_icon(color)


def set_window_icon(root) -> None:
    """Set the AlienCore alien-head as the icon on any Tk window."""
    try:
        from PIL import ImageTk
        _img = ImageTk.PhotoImage(_get_alien_icon(COLOR_COOL))
        root.iconphoto(True, _img)
        # Keep reference on the widget to prevent GC
        root._ac_icon = _img
    except Exception:
        pass


def start(on_settings_open, on_quit):
    """
    Single tray icon showing current profile.
    Bar handles all sensor display — tray just provides the menu.
    """
    global _running, _thread, _icons

    _running = True
    _icons   = []

    menu = _build_menu(on_settings_open, on_quit)

    # Alien-head icon — starts with cool (green); update loop adjusts color
    img  = _get_alien_icon(COLOR_COOL)
    icon = pystray.Icon(
        name  = "aliencore_main",
        icon  = img,
        title = f"AlienCore v{VERSION}",
        menu  = menu,
    )
    _icons.append(icon)

    # Start update thread
    _thread = threading.Thread(target=_update_loop, daemon=True,
                               name="TrayUpdateThread")
    _thread.start()

    logger.info("Tray icon started.")
    icon.run()   # blocks until stop() called


def stop():
    global _running
    _running = False
    for icon in _icons:
        try:
            icon.stop()
        except Exception:
            pass
    if _overlay:
        try:
            _overlay.root.destroy()
        except Exception:
            pass
    logger.info("Tray stopped.")


def send_notification(message: str, title: str = "AlienCore"):
    """
    Show a Windows balloon/toast notification via the pystray tray icon.
    No subprocess spawned — completely silent on screen.
    Safe to call from any thread; silently ignored if tray isn't running yet.
    """
    try:
        if _icons:
            _icons[0].notify(message, title)
    except Exception as e:
        logger.debug("Tray notification failed: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Update loop
# ─────────────────────────────────────────────────────────────────────────────


def _update_loop():
    """
    Updates the alien-head icon color based on CPU Package temperature,
    refreshes the tooltip with live readings, and polls the AI watchdog alert.
    """
    reregister_counter = 0
    while _running:
        # ── Tray icon resilience ──────────────────────────────────────────────
        # pystray handles WM_TASKBARCREATED (Explorer restart), but Windows 11
        # silently drops tray icons on sleep/resume and on some DWM resets
        # without firing that broadcast. Every ~32 s we re-issue NIM_ADD via
        # icon._show(); Windows ignores the call if the icon is still alive,
        # and revives it otherwise.
        reregister_counter += 1
        if reregister_counter >= 8:
            reregister_counter = 0
            try:
                ico = _icons[0] if _icons else None
                if (ico is not None
                        and getattr(ico, "_running", False)
                        and getattr(ico, "visible", False)):
                    ico._show()
            except Exception as e:
                logger.debug("Tray re-register skipped: %s", e)


        # ── CPU Package temp → icon color ─────────────────────────────────────
        try:
            readings = sensors.get_readings()
            thresh   = cfg.get().get("thresholds", {})
            cpu_t    = (readings.get("cpu_temp_package") or
                        readings.get("cpu_temp_avg"))
            warn     = thresh.get("cpu_warn", 80)
            crit     = thresh.get("cpu_crit", 95)

            if cpu_t is None:
                color = "#1a4a2a"          # dim green = waiting for first reading
            elif cpu_t >= crit:
                color = COLOR_HOT
            elif cpu_t >= warn:
                color = COLOR_WARM
            else:
                color = COLOR_COOL

            if _icons and color != _last_icon_color[0]:
                _icons[0].icon = _get_alien_icon(color)
                _last_icon_color[0] = color

            # Tooltip: profile + live CPU temp
            profile  = profiles.get_current()
            temp_str = f"  CPU {int(cpu_t)}°" if cpu_t is not None else ""
            if _icons:
                _icons[0].title = (
                    f"AlienCore  [{profile.upper()}]{temp_str}"
                    f"  —  right-click for menu"
                )
        except Exception as e:
            logger.debug("Tray update error: %s", e)

        # ── AI watchdog alert toast ───────────────────────────────────────────
        try:
            from core import ai_manager
            alert = ai_manager.get_last_alert()
            if alert and alert != _last_shown_alert[0]:
                _last_shown_alert[0] = alert
                if _icons:
                    _icons[0].notify(alert, "AlienCore AI Alert")
                logger.info("Watchdog alert toasted to user.")
        except Exception as e:
            logger.debug("Alert toast error: %s", e)

        # ── Update available — show dialog once when first detected ───────────
        try:
            from core import updater as _upd
            if _upd.is_dialog_pending():
                _upd.clear_dialog_pending()
                info = _upd.get_update_info()
                if info:
                    # Toast notification first so the user knows something happened
                    if _icons:
                        _icons[0].notify(
                            f"v{info['version']} is ready to install. "
                            f"Open Settings or click the tray menu to update.",
                            f"{APP_NAME} Update Available",
                        )
                    # Show the full dialog on a dedicated thread (Tk is fine
                    # from non-main threads on Windows; tray callbacks do this too).
                    threading.Thread(
                        target=_show_update_dialog_standalone,
                        args=(info,),
                        name="UpdateDialog",
                        daemon=True,
                    ).start()
        except Exception as e:
            logger.debug("Update dialog trigger error: %s", e)

        # Tray colour/tooltip only needs to refresh on the same cadence as the
        # sensor thread — sleeping less just burns CPU on cached data.
        time.sleep(4)


def _get_interval_ms() -> float:
    """Return the update interval in milliseconds from config."""
    c    = cfg.get()
    unit = c.get("display", {}).get("update_interval_unit", "seconds")
    val  = c.get("display", {}).get("update_interval_value", 2.0)
    if unit == "milliseconds":
        return max(100.0, float(val))
    else:
        return max(0.5, float(val)) * 1000.0


# ─────────────────────────────────────────────────────────────────────────────
# Reading helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_reading(kind, reading_key, label, unit, readings, thresh):
    """Return (display_text, hex_color) for a given sensor."""

    if kind == "temp":
        val = readings.get(reading_key)
        if val is None:
            return "---", "#888888"
        return sensors.fmt_temp(val), _temp_color(label, val, thresh)

    elif kind == "ram_temp":
        items = readings.get(reading_key, [])
        if not items:
            return "---", "#888888"
        val = max(t["temp_c"] for t in items)
        color = COLOR_HOT if val > 55 else COLOR_WARM if val > 45 else COLOR_COOL
        return sensors.fmt_temp(val), color

    elif kind == "list_temp":
        items = readings.get(reading_key, [])
        if not items:
            return "---", "#888888"
        val = max(t["temp_c"] for t in items)
        return sensors.fmt_temp(val), _temp_color(label, val, thresh)

    elif kind == "nvme_primary":
        items = readings.get(reading_key, [])
        if not items:
            return "---", "#888888"
        val = items[0]["temp_c"]
        return sensors.fmt_temp(val), _temp_color(label, val, thresh)

    elif kind == "nvme_secondary":
        items = readings.get(reading_key, [])
        if len(items) < 2:
            return "---", "#888888"
        val = items[1]["temp_c"]
        return sensors.fmt_temp(val), _temp_color(label, val, thresh)

    elif kind == "list_rpm":
        fans = readings.get(reading_key, [])
        if not fans:
            return "---", "#888888"
        rpm  = max(f["rpm"] for f in fans)
        text = f"{rpm/1000:.1f}k" if rpm >= 1000 else str(rpm)
        return text, COLOR_COOL

    elif kind == "pct":
        val = readings.get(reading_key)
        if val is None:
            return "---", "#888888"
        color = COLOR_HOT if val > 90 else COLOR_WARM if val > 75 else COLOR_COOL
        return f"{int(val)}{unit}", color

    elif kind == "vram":
        used  = readings.get("gpu_vram_used_mb")
        total = readings.get("gpu_vram_total_mb")
        if used is None:
            return "---", "#888888"
        if total and total > 0:
            pct   = (used / total) * 100
            color = COLOR_HOT if pct > 90 else COLOR_WARM if pct > 75 else COLOR_COOL
            return f"{int(pct)}%", color
        return f"{int(used)}M", COLOR_COOL

    elif kind == "watts":
        val = readings.get(reading_key)
        if val is None:
            return "---", "#888888"
        return f"{int(val)}W", COLOR_COOL

    elif kind == "freq":
        val = readings.get(reading_key)
        if val is None:
            return "---", "#888888"
        return f"{val:.1f}G", COLOR_COOL

    elif kind == "mhz":
        val = readings.get(reading_key)
        if val is None:
            return "---", "#888888"
        return f"{int(val)}M", COLOR_COOL

    elif kind == "battery":
        pct      = readings.get("battery_pct")
        charging = readings.get("battery_charging", False)
        if pct is None:
            return "---", "#888888"
        color  = (COLOR_COOL if charging or pct > 40 else
                  COLOR_WARM if pct > 20 else COLOR_HOT)
        suffix = "+" if charging else "%"
        return f"{int(pct)}{suffix}", color

    elif kind == "net_io":
        dn = readings.get("net_down_mbps")
        up = readings.get("net_up_mbps")
        if dn is None:
            return "---", "#888888"
        def _f(v): return f"{v:.0f}" if v >= 10 else f"{v:.1f}"
        return f"↓{_f(dn)} ↑{_f(up)}", COLOR_COOL

    elif kind == "disk_io":
        rd = readings.get("disk_read_mbps")
        wr = readings.get("disk_write_mbps")
        if rd is None:
            return "---", "#888888"
        def _f(v): return f"{v:.0f}" if v >= 10 else f"{v:.1f}"
        color = COLOR_WARM if (rd + wr) > 500 else COLOR_COOL
        return f"R{_f(rd)} W{_f(wr)}", color

    return "---", "#888888"


def _temp_color(label: str, val: float, thresh: dict) -> str:
    ll = label.lower()
    if "nvm" in ll:
        warn, crit = thresh.get("nvme_warn", 60), thresh.get("nvme_crit", 70)
    elif "gpu" in ll:
        warn, crit = thresh.get("gpu_warn", 80), thresh.get("gpu_crit", 90)
    else:
        warn, crit = thresh.get("cpu_warn", 80), thresh.get("cpu_crit", 95)
    return sensors.color_for_temp(val, warn, crit)


# ─────────────────────────────────────────────────────────────────────────────
# Alien-head icon renderer
# ─────────────────────────────────────────────────────────────────────────────

def _render_alien_head(color_hex: str) -> Image.Image:
    """
    Render the Alienware alien-head icon.

    Style mirrors the LED-lit logo on the laptop lid:
      • Near-black head silhouette with a faint accent-coloured tint
      • Glowing rim around the head outline
      • Large, vividly glowing eyes with an inner highlight
      • Diffuse outer glow behind the whole shape

    Rendered at 2× (512×512) then Lanczos-downscaled to 256×256 for
    crisp, anti-aliased edges at any tray display size.
    """
    RENDER = 512   # supersample canvas
    OUTPUT = 256   # final icon size
    S2     = RENDER / 256  # scale factor = 2.0

    try:
        r = int(color_hex[1:3], 16)
        g = int(color_hex[3:5], 16)
        b = int(color_hex[5:7], 16)
    except Exception:
        r, g, b = 0, 204, 102

    def _s(pts):
        """Scale 256-space polygon to RENDER space."""
        return [(int(x * S2), int(y * S2)) for x, y in pts]

    head     = _s(_HEAD_POLY)
    head_out = _s(_HEAD_POLY_OUTER)
    leye     = _s(_LEFT_EYE)
    reye     = _s(_RIGHT_EYE)
    leye_hi  = _s(_eye_inner(_LEFT_EYE))
    reye_hi  = _s(_eye_inner(_RIGHT_EYE))

    img = Image.new("RGBA", (RENDER, RENDER), (0, 0, 0, 0))

    # ── Diffuse outer glow ────────────────────────────────────────────────────
    layer = Image.new("RGBA", (RENDER, RENDER), (0, 0, 0, 0))
    ImageDraw.Draw(layer).polygon(head_out, fill=(r, g, b, 45))
    layer = layer.filter(ImageFilter.GaussianBlur(int(RENDER * 0.072)))
    img   = Image.alpha_composite(img, layer)

    # ── Dark head fill (deeply-tinted black — colour temperature hint) ─────────
    hr = max(8,  min(32, r // 9 + 6))
    hg = max(8,  min(32, g // 9 + 6))
    hb = max(8,  min(32, b // 9 + 6))
    layer = Image.new("RGBA", (RENDER, RENDER), (0, 0, 0, 0))
    ImageDraw.Draw(layer).polygon(head, fill=(hr, hg, hb, 248))
    img   = Image.alpha_composite(img, layer)

    # ── Glowing head-edge rim ─────────────────────────────────────────────────
    layer = Image.new("RGBA", (RENDER, RENDER), (0, 0, 0, 0))
    ImageDraw.Draw(layer).polygon(head, outline=(r, g, b, 210), width=8)
    layer = layer.filter(ImageFilter.GaussianBlur(5))
    img   = Image.alpha_composite(img, layer)

    # ── Eye bloom (soft glow behind the eyes) ─────────────────────────────────
    layer = Image.new("RGBA", (RENDER, RENDER), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.polygon(leye, fill=(r, g, b, 230))
    d.polygon(reye, fill=(r, g, b, 230))
    layer = layer.filter(ImageFilter.GaussianBlur(14))
    img   = Image.alpha_composite(img, layer)

    # ── Solid eye fill ────────────────────────────────────────────────────────
    d = ImageDraw.Draw(img)
    d.polygon(leye, fill=(r, g, b, 255))
    d.polygon(reye, fill=(r, g, b, 255))

    # ── Inner eye highlight (near-white hot-spot) ─────────────────────────────
    hi_r = min(255, r + 110)
    hi_g = min(255, g + 110)
    hi_b = min(255, b + 110)
    d.polygon(leye_hi, fill=(hi_r, hi_g, hi_b, 210))
    d.polygon(reye_hi, fill=(hi_r, hi_g, hi_b, 210))

    # ── Downsample with Lanczos for smooth final result ───────────────────────
    return img.resize((OUTPUT, OUTPUT), Image.LANCZOS)


def _get_alien_icon(color_hex: str) -> Image.Image:
    """Return a cached rendered alien-head icon for the given color."""
    if color_hex not in _ICON_CACHE:
        _ICON_CACHE[color_hex] = _render_alien_head(color_hex)
    return _ICON_CACHE[color_hex].copy()


# ─────────────────────────────────────────────────────────────────────────────
# Tray menu
# ─────────────────────────────────────────────────────────────────────────────

def _build_override_items():
    """Dynamically build Override Profile submenu items at click time."""
    items = [
        pystray.MenuItem("Auto (clear override)",
                         lambda item: profiles.set_manual_override(None)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Idle",
                         lambda item: profiles.set_manual_override("idle")),
        pystray.MenuItem("Gaming",
                         lambda item: profiles.set_manual_override("gaming")),
        pystray.MenuItem("Streaming",
                         lambda item: profiles.set_manual_override("streaming")),
    ]
    user_profiles = cfg.get().get("profiles", {}).get("user_profiles", [])
    if user_profiles:
        items.append(pystray.Menu.SEPARATOR)
        for up in sorted(user_profiles, key=lambda p: p.get("priority", 50)):
            name  = up["name"]
            label = up.get("label", name)
            items.append(pystray.MenuItem(
                label, lambda item, n=name: profiles.set_manual_override(n)
            ))
    return items


def _build_menu(on_settings_open, on_quit):
    return pystray.Menu(
        pystray.MenuItem(f"AlienCore  v{VERSION}", None, enabled=False),
        pystray.MenuItem(
            lambda item: f"Profile: {profiles.get_current().upper()}",
            None, enabled=False
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            lambda item: (
                f"Update Available \u2192 v{_get_update_version()}"
                if _update_available() else "Check for Updates"
            ),
            lambda item: _open_update_dialog(),
            visible=lambda item: _update_available(),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open Settings",
                         lambda item: _safe(on_settings_open)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            lambda item: "Turbo Cool: ON  (click to disable)"
                         if _turbo_active() else
                         "Turbo Cool: OFF  (click to enable)",
            lambda item: _toggle_turbo_cool(),
            visible=lambda item: _awcc_available(),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Override Profile",
                         pystray.Menu(_build_override_items)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            lambda item: (
                f"\u26a0 AI: {(_get_ai_alert() or '')[:50]}"
                if _get_ai_alert() else "No AI alerts"
            ),
            lambda item: _show_ai_alert_dialog(),
            enabled=lambda item: bool(_get_ai_alert()),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("AI Config Advisor",  lambda item: _open_ai_advisor()),
        pystray.MenuItem("Open AI Chat",      lambda item: _open_ai_chat()),
        pystray.MenuItem("Toggle Overlay",   lambda item: _toggle_overlay()),
        pystray.MenuItem("Show/Hide Bar",    lambda item: _toggle_bar()),
        pystray.MenuItem("View Log",         lambda item: _open_log()),
        pystray.MenuItem("Send Feedback",    lambda item: _open_feedback()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            lambda item: "Start with Windows: ON  (click to disable)"
                         if _startup_enabled() else
                         "Start with Windows: OFF  (click to enable)",
            lambda item: _toggle_startup()
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("About AlienCore",  lambda item: _open_about()),
        pystray.MenuItem("Exit AlienCore",   lambda item: _safe(on_quit)),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Overlay
# ─────────────────────────────────────────────────────────────────────────────

def _awcc_available() -> bool:
    # Read from cached sensor readings — never call AWCC WMI from the tray thread.
    # All WMI access must stay on the SensorThread (COM STA apartment model).
    try:
        from core import sensors
        return bool(sensors.get_readings().get("awcc_available", False))
    except Exception:
        return False


def _turbo_active() -> bool:
    try:
        from core import turbo_cool
        return turbo_cool.is_active()
    except Exception:
        return False


def _toggle_turbo_cool():
    try:
        from core import turbo_cool
        turbo_cool.toggle()
        logger.info("Turbo Cool toggled from tray")
    except Exception as e:
        logger.error("Turbo Cool toggle error: %s", e)


def _toggle_bar():
    try:
        from gui import bar
        if bar._instance:
            root = bar._instance.root
            if root.winfo_viewable():
                bar._instance._hide_bar()
            else:
                bar._instance._show_bar()
    except Exception as e:
        logger.debug("Bar toggle error: %s", e)


def _toggle_overlay():
    current = cfg.get_value("display", "overlay_enabled", default=False)
    cfg.set_value("display", "overlay_enabled", value=not current)
    if not current and _overlay is None:
        threading.Thread(target=_start_overlay, daemon=True,
                         name="OverlayThread").start()
    logger.info("Overlay toggled: %s", not current)


def _start_overlay():
    global _overlay
    try:
        import tkinter as tk
        from core.constants import COLOR_COOL, COLOR_WARM, COLOR_HOT

        class OverlayWindow:
            def __init__(self):
                self.root = tk.Tk()
                self.root.overrideredirect(True)
                self.root.attributes("-topmost", True)
                self.root.attributes("-alpha",
                    cfg.get_value("display", "overlay_opacity", default=0.85))
                self.root.configure(bg="#111111")
                self._drag_x = self._drag_y = 0
                self._build()
                self._position()
                self._update()

            def _build(self):
                pad = tk.Frame(self.root, bg="#111111", padx=8, pady=6)
                pad.pack()
                self.labels = {}
                sens_cfg = cfg.get().get("sensors", {})
                for config_key, label, _, _, _ in SENSOR_DEFS:
                    if not sens_cfg.get(config_key, False):
                        continue
                    row = tk.Frame(pad, bg="#111111")
                    row.pack(anchor="w", pady=1)
                    tk.Label(row, text=f"{label:<5}",
                             font=("Consolas", 9), bg="#111111",
                             fg="#555555", width=5).pack(side="left")
                    lbl = tk.Label(row, text="---",
                                   font=("Consolas", 9, "bold"),
                                   bg="#111111", fg=COLOR_COOL,
                                   width=8, anchor="w")
                    lbl.pack(side="left")
                    self.labels[label] = lbl
                pad.bind("<ButtonPress-1>", self._drag_start)
                pad.bind("<B1-Motion>",     self._drag_move)

            def _position(self):
                self.root.update_idletasks()
                w  = self.root.winfo_width()
                h  = self.root.winfo_height()
                sw = self.root.winfo_screenwidth()
                sh = self.root.winfo_screenheight()
                pos = cfg.get_value("display", "overlay_position",
                                    default="bottom_right")
                m = 12
                coords = {
                    "bottom_right": (sw-w-m, sh-h-m-40),
                    "bottom_left":  (m,      sh-h-m-40),
                    "top_right":    (sw-w-m, m),
                    "top_left":     (m,      m),
                }
                x, y = coords.get(pos, coords["bottom_right"])
                self.root.geometry(f"+{x}+{y}")

            def _drag_start(self, e):
                self._drag_x, self._drag_y = e.x, e.y

            def _drag_move(self, e):
                x = self.root.winfo_x() + e.x - self._drag_x
                y = self.root.winfo_y() + e.y - self._drag_y
                self.root.geometry(f"+{x}+{y}")

            def _update(self):
                if not cfg.get_value("display", "overlay_enabled", default=True):
                    self.root.withdraw()
                    self.root.after(2000, self._update)
                    return
                self.root.deiconify()
                readings = sensors.get_readings()
                thresh   = cfg.get().get("thresholds", {})
                for _, label, reading_key, unit, kind in SENSOR_DEFS:
                    lbl = self.labels.get(label)
                    if not lbl:
                        continue
                    try:
                        text, color = _get_reading(
                            kind, reading_key, label, unit, readings, thresh)
                        lbl.config(text=text, fg=color)
                    except Exception:
                        pass
                self.root.after(2000, self._update)

            def run(self):
                self.root.mainloop()

        _overlay = OverlayWindow()
        _overlay.run()
    except Exception as e:
        logger.error("Overlay error: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _startup_enabled() -> bool:
    try:
        from core import startup as _su
        return _su.is_enabled()
    except Exception:
        return False


def _toggle_startup():
    try:
        from core import startup as _su
        if _su.is_enabled():
            _su.disable()
            cfg.set_value("service", "start_with_windows", value=False)
        else:
            _su.enable()
            cfg.set_value("service", "start_with_windows", value=True)
        cfg.save(cfg.get())
        logger.info("Start with Windows toggled from tray: %s", _su.is_enabled())
    except Exception as e:
        logger.error("Startup toggle error: %s", e)


def _get_ai_alert() -> str | None:
    try:
        from core import ai_manager
        return ai_manager.get_last_alert()
    except Exception:
        return None


def _dismiss_alert():
    try:
        from core import ai_manager
        ai_manager.clear_alert()
        _last_shown_alert[0] = None
    except Exception:
        pass


def _show_ai_alert_dialog():
    alert = _get_ai_alert()
    if not alert:
        return
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo("AlienCore AI Alert", alert, parent=root)
    root.destroy()
    _dismiss_alert()


def _open_ai_advisor():
    try:
        from gui.ai_advisor_ui import open_advisor_thread
        open_advisor_thread()
    except Exception as e:
        logger.error("AI advisor open error: %s", e)


_ai_chat_proc = None


def _open_ai_chat():
    """Launch the AI chat as a subprocess so its Tk root can't collide with
    the sensor bar's Tk root (two live tk.Tk() instances in one process make
    the bar shrink / flicker)."""
    global _ai_chat_proc
    try:
        import os, sys, subprocess
        if _ai_chat_proc is not None and _ai_chat_proc.poll() is None:
            return   # already open
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _ai_chat_proc = subprocess.Popen(
            [sys.executable, os.path.join(base, "aliencore.py"), "--ai-chat"],
            cwd=base,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as e:
        logger.error("AI chat open error: %s", e)


def _open_log():
    import os
    from core.constants import LOG_PATH
    try:
        os.startfile(LOG_PATH)
    except Exception:
        import subprocess
        subprocess.Popen(["notepad.exe", LOG_PATH])


def _open_feedback():
    from gui import feedback
    feedback.open_feedback_thread()


def _open_about():
    """Lightweight standalone About dialog (works even when Settings is closed)."""
    import tkinter as tk
    import webbrowser
    from core.constants import SUPPORT_EMAIL, GITHUB_ISSUES_URL

    def _build():
        root = tk.Tk()
        root.title(f"About {APP_NAME}")
        root.configure(bg="#1a1a1a")
        root.resizable(False, False)

        pad = tk.Frame(root, bg="#1a1a1a", padx=32, pady=24)
        pad.pack()

        tk.Label(pad, text=APP_NAME, font=("Segoe UI", 28, "bold"),
                 bg="#1a1a1a", fg="#00aaff").pack(anchor="w")
        tk.Label(pad, text=f"Version {VERSION}", font=("Segoe UI", 10),
                 bg="#1a1a1a", fg="#888888").pack(anchor="w", pady=(0, 12))

        tk.Frame(pad, bg="#333333", height=1).pack(fill="x", pady=(0, 14))

        tk.Label(pad, text="Kyle Yeroshefsky",
                 font=("Segoe UI", 12, "bold"),
                 bg="#1a1a1a", fg="#e8e8e8").pack(anchor="w")

        email_lnk = tk.Label(pad, text=SUPPORT_EMAIL,
                              font=("Segoe UI", 9, "underline"),
                              bg="#1a1a1a", fg="#00aaff", cursor="hand2")
        email_lnk.pack(anchor="w", pady=(4, 0))
        email_lnk.bind("<Button-1>",
                       lambda e: webbrowser.open(f"mailto:{SUPPORT_EMAIL}"))

        gh_url = GITHUB_ISSUES_URL.split("/issues")[0]
        gh_lnk = tk.Label(pad, text=gh_url,
                           font=("Segoe UI", 9, "underline"),
                           bg="#1a1a1a", fg="#00aaff", cursor="hand2")
        gh_lnk.pack(anchor="w", pady=(4, 12))
        gh_lnk.bind("<Button-1>", lambda e: webbrowser.open(gh_url))

        tk.Frame(pad, bg="#333333", height=1).pack(fill="x", pady=(0, 12))

        tk.Label(pad,
                 text=(
                     "A comprehensive Windows system optimizer for Alienware hardware.\n"
                     "CPU · GPU · RAM · Network · Storage · Privacy · AI-assisted tuning."
                 ),
                 font=("Segoe UI", 9), bg="#1a1a1a", fg="#888888",
                 justify="left").pack(anchor="w", pady=(0, 14))

        tk.Button(pad, text="  Close  ", command=root.destroy,
                  font=("Segoe UI", 10), fg="#e8e8e8", bg="#333333",
                  activeforeground="#e8e8e8", activebackground="#444444",
                  relief="flat", padx=12, pady=5, cursor="hand2",
                  bd=0, highlightthickness=0).pack(anchor="e")

        root.update_idletasks()
        w, h = root.winfo_width(), root.winfo_height()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"+{(sw-w)//2}+{(sh-h)//2}")
        root.mainloop()

    threading.Thread(target=_build, daemon=True, name="AboutDialog").start()


def _show_update_dialog_standalone(info: dict):
    """Open the update dialog on a dedicated thread (safe from tray callbacks)."""
    try:
        from gui import update_dialog
        update_dialog.show_standalone(info)
    except Exception as e:
        logger.debug("Update dialog error: %s", e)


def _open_update_dialog():
    """Tray menu action — open update dialog if an update is available."""
    from core import updater as _upd
    info = _upd.get_update_info()
    if info:
        threading.Thread(
            target=_show_update_dialog_standalone,
            args=(info,),
            name="UpdateDialog",
            daemon=True,
        ).start()


def _update_available() -> bool:
    """Returns True when an update is available."""
    try:
        from core import updater as _upd
        return _upd.get_update_info() is not None
    except Exception:
        return False


def _get_update_version() -> str:
    try:
        from core import updater as _upd
        info = _upd.get_update_info()
        return info["version"] if info else ""
    except Exception:
        return ""


def _safe(fn):
    try:
        fn()
    except Exception as e:
        logger.error("Tray callback error: %s", e)
