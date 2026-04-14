"""
AlienCore - constants.py
Central definitions for version, paths, defaults, and thresholds.
"""

import os
import sys

VERSION = "1.0.0"
APP_NAME = "AlienCore"

# ── Support / feedback ────────────────────────────────────────────────────────
SUPPORT_EMAIL     = "mourning.grace.2014@gmail.com"
GITHUB_ISSUES_URL = "https://github.com/mourninggrace/AlienCore/issues/new"

# ── Licensing backend ────────────────────────────────────────────────────────
# Update this to your deployed server URL once you have a domain.
# Example: "https://api.aliencore.app"  or  "https://yourserver.com:8765"
BACKEND_URL = "https://api.aliencore.app"

# PayPal account that receives payments
PAYPAL_BUSINESS_EMAIL = "mourning.grace.2014@gmail.com"

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR              = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH           = os.path.join(BASE_DIR, "config.json")
LOG_PATH              = os.path.join(BASE_DIR, "logs", "aliencore.log")
PROFILE_DIR           = os.path.join(BASE_DIR, "profiles")
HARDWARE_CACHE        = os.path.join(BASE_DIR, "hardware_profile.json")
THROTTLE_LOG_PATH     = os.path.join(BASE_DIR, "logs", "throttle_events.json")
EFFICIENCY_HISTORY_PATH = os.path.join(BASE_DIR, "logs", "efficiency_history.json")
BOOST_HISTORY_PATH    = os.path.join(BASE_DIR, "logs", "boost_history.json")
PAGEFILE_SESSIONS_PATH = os.path.join(BASE_DIR, "logs", "pagefile_sessions.json")

# ── Feature thresholds ────────────────────────────────────────────────────────
TVB_TEMP_THRESHOLD = 70    # Intel TVB activates below this temp (Raptor Lake i9)
DDR5_PEAK_GBPS     = 89.0  # DDR5-5600 dual-channel theoretical peak (GB/s)

# ── Temperature thresholds (°C) ───────────────────────────────────────────────
TEMP_CPU_WARN     = 80
TEMP_CPU_CRIT     = 95
TEMP_GPU_WARN     = 80
TEMP_GPU_CRIT     = 90
TEMP_NVME_WARN    = 60
TEMP_NVME_CRIT    = 70

# Overlay color codes (used in tray/overlay rendering)
COLOR_COOL   = "#00CC66"   # green
COLOR_WARM   = "#FFAA00"   # amber
COLOR_HOT    = "#FF3333"   # red
COLOR_WARN   = "#FFAA00"   # alias for warm

# ── Monitor loop timing ───────────────────────────────────────────────────────
SENSOR_POLL_INTERVAL   = 2    # seconds between sensor reads
PROCESS_POLL_INTERVAL  = 5    # seconds between process list checks
PROFILE_EVAL_INTERVAL  = 10   # seconds between profile decisions
TRAY_CYCLE_INTERVAL    = 3    # seconds between tray icon stat rotations

# ── CPU power plan GUIDs (Windows built-in) ───────────────────────────────────
POWER_PLAN_BALANCED      = "381b4222-f694-41f0-9685-ff5bb260df2e"
POWER_PLAN_HIGH_PERF     = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
POWER_PLAN_POWER_SAVER   = "a1841308-3541-4fab-bc81-f71556f20b4a"
POWER_PLAN_ULTIMATE_PERF = "e9a42b02-d5df-448d-aa00-03f14749eb61"  # best base for gaming
POWER_PLAN_ALIENCORE     = "AlienCore-Adaptive"   # we create this custom plan

# ── CPU heterogeneous scheduling GUIDs (Intel Raptor Lake / 13th+14th Gen) ────
# These tune how Windows 11 routes threads across P-cores and E-cores using
# Intel Thread Director. i9-14900HX = 8P + 16E = 32 logical processors.
CPU_SUBGROUP_GUID             = "54533251-82be-4824-96c1-47b60b740d00"  # SUB_PROCESSOR
CPU_SCHED_POLICY_GUID         = "93b8b6dc-0698-4d1c-9ee4-0644e900c85d"  # SCHEDPOLICY (5=Automatic/ITD-guided)
CPU_SCHED_SHORT_GUID          = "bae08b81-2d5e-4688-ad6a-13243356654b"  # short-thread policy (2=PreferPerformant)
CPU_HETERO_POLICY_GUID        = "60fbe21b-efd9-49f2-b066-8674d8e9f423"  # HETEROPOLICY (3=HeteroParking)
CPU_PERF_AUTONOMOUS_GUID      = "8baa4a8a-14c6-4451-8e8b-14bdbd197537"  # PERFAUTONOMOUS (0=OS, 1=HW-guided)
CPU_PERF_INCREASE_THRESH_GUID = "06cadf0e-64ed-448a-8927-ce7bf90eb35d"  # perf step-up utilization threshold
CPU_CORE_PARKING_MIN_GUID     = "0cc5b647-c1df-4637-891a-dec35c318583"  # min unparked cores %
CPU_CORE_PARKING_MAX_GUID     = "ea062031-0e34-4ff1-9b6d-eb1059334028"  # max unparked cores %

# ── Win32PrioritySeparation (scheduler quanta + foreground boost) ─────────────
# Bits 0-1: FG boost (2=high), Bit 2: quantum type (1=fixed), Bit 3: length (0=short)
WIN32_PRIORITY_SEP_IDLE      = 38   # 0x26: short variable quanta, high FG boost (Windows default)
WIN32_PRIORITY_SEP_GAMING    = 42   # 0x2A: short fixed quanta, high FG boost (pure gaming)
WIN32_PRIORITY_SEP_STREAMING = 41   # 0x29: short fixed quanta, medium FG boost (gaming + stream)

# ── Process signatures for profile detection ─────────────────────────────────
STREAMING_PROCESSES = [
    "obs64.exe", "obs32.exe", "obs.exe",
    "xsplit.core.exe", "xsplitvideomixer.exe",
    "streamlabs obs.exe", "slobs.exe",
]

GAMING_PROCESSES = [
    # Common launchers / engines — fingerprint also adds detected game EXEs
    "steam.exe", "epicgameslauncher.exe", "gog galaxy.exe",
    "gameoverlayui.exe",          # Steam overlay = game is running
    "thedivision2.exe",           # your current game
    "d3d11.dll",                  # not a process but noted for load signals
]

BROWSER_PROCESSES = [
    "chrome.exe", "firefox.exe", "msedge.exe", "brave.exe",
]

# Background processes to route to E-cores via EcoQoS during gaming
BACKGROUND_PROCESSES_ECOQOS = [
    "chrome.exe", "firefox.exe", "msedge.exe", "brave.exe",
    "discord.exe", "slack.exe", "teams.exe",
    "onedrive.exe", "dropbox.exe",
    "searchhost.exe", "searchindexer.exe",
]

# ── Default config (written on first run if config.json missing) ──────────────
DEFAULT_CONFIG = {
    "version": VERSION,
    "first_run_complete": False,

    # ── Turbo Cool ──
    "turbo_cool": {
        "auto_off_minutes":  10,      # auto-disable after this many minutes
        "auto_off_on_cool":  True,    # also auto-disable when temps drop below warn
    },

    # ── LHM bridge (embedded sensor reader) ──
    "lhm": {
        "bridge_exe": "",  # leave empty to use default: tools/lhm_bridge/dist/lhm_bridge.exe
    },

    # ── Display / overlay ──
    "display": {
        "temp_unit": "celsius",               # "celsius" | "fahrenheit"
        "auto_hide_fullscreen": True,         # withdraw bar when a fullscreen app is active
        "overlay_enabled": False,
        "overlay_position": "bottom_right",   # bottom_right | bottom_left | top_right | top_left
        "overlay_opacity": 0.85,
        "update_interval_value": 2.0,         # numeric interval value
        "update_interval_unit": "seconds",    # "seconds" or "milliseconds"
        "bar_orientation": "horizontal",      # "horizontal" | "vertical"
        "settings_theme": "Venom",            # settings window color theme
    },

    # ── Sensors to show ──
    "sensors": {
        "cpu_temp":       True,
        "cpu_temp_mode":  "average",          # average | per_core
        "gpu_temp":       True,
        "gpu_hotspot":    False,              # GPU hot spot junction temp
        "gpu_mem_temp":   False,              # VRAM junction temp
        "nvme_temp":      True,
        "fan_rpm":        True,
        "ram_usage":      True,
        "cpu_load":       False,
        "gpu_load":       False,
        "gpu_vram":       False,
        "gpu_fan":        False,              # GPU fan % (AWCC may report 0)
        "cpu_freq":       False,              # current CPU boost clock (GHz)
        "gpu_clock":      False,              # GPU core clock (GHz)
        "cpu_watts":      False,
        "gpu_watts":      False,
        "battery":        True,               # battery % + charging state
        "net_io":         False,              # network throughput (MB/s up/down)
        "disk_io":        False,              # disk throughput (MB/s read/write)
    },

    # ── Temperature thresholds (user-editable) ──
    "thresholds": {
        "cpu_warn": TEMP_CPU_WARN,
        "cpu_crit": TEMP_CPU_CRIT,
        "gpu_warn": TEMP_GPU_WARN,
        "gpu_crit": TEMP_GPU_CRIT,
        "nvme_warn": TEMP_NVME_WARN,
        "nvme_crit": TEMP_NVME_CRIT,
    },

    # ── CPU management ──
    "cpu": {
        "enabled": True,
        "dynamic_throttle": True,             # lower max processor state at idle dynamically
        "idle_max_state_pct": 40,             # ceiling % when fully idle and cool
        "throttle_temp_trigger": 75,          # start throttling if CPU hits this °C at idle
        "full_power_in_gaming": True,
        "full_power_in_streaming": True,
        "hetero_scheduling": True,            # Intel Thread Director + hetero parking policy
        "core_parking_gaming": True,          # unpark all 32 logical processors in gaming/streaming
        "tvb_optimizer": False,               # nudge idle CPU ceiling to stay under TVB threshold
        "interrupt_steering": False,          # steer critical IRQs toward P-cores via registry
        "process_affinity_tags": {},          # {exe_name: "p" | "e"} manual affinity hints
    },

    # ── GPU management ──
    "gpu": {
        "enabled": True,
        "fan_curve_enabled": True,
        "power_limit_idle_pct": 60,           # informational — AWCC manages laptop GPU power
        "power_limit_gaming_pct": 100,
        "power_limit_streaming_pct": 85,
        "optimal_decision": True,
        "hags_enabled": True,                 # Hardware-Accelerated GPU Scheduling (req'd for DLSS 3)
        "powermizer_max_performance": True,   # NVIDIA max performance P-state on AC power
        "vram_idle_clock_lock": False,        # lock VRAM to idle clock when not gaming
        "vram_idle_clock_mhz": 405,           # VRAM clock to lock to at idle (405 MHz = P8 state)
    },

    # ── RAM ──
    "ram": {
        "enabled": True,
        "disable_superfetch": True,           # SysMain — unnecessary on NVMe + 32GB+
        "pagefile_managed": True,
        "pagefile_custom_mb": 0,
        "clear_standby_cache_on_idle": True,
        "disable_paging_executive": True,     # keep kernel code pinned in RAM (never paged)
        "dimm_throttle_protection": False,    # alert + throttle CPU when DIMM temps exceed threshold
        "dimm_throttle_temp_c": 52,           # DIMM temp (°C) that triggers protection
        "leak_watchdog_enabled": False,       # monitor per-process RSS for memory leaks
        "leak_threshold_mb_per_min": 50.0,    # growth rate that flags a leak
        "leak_window_minutes": 5.0,           # observation window for leak detection
    },

    # ── Scheduler / process priority ──
    "scheduler": {
        "enabled": True,
        "win32_priority_separation": True,    # per-profile quanta tuning (idle=38, gaming=42)
        "ecoqos_background": True,            # push browsers/Discord to E-cores during gaming
    },

    # ── Visual effects / UI snappiness ──
    "visual": {
        "enabled": True,
        "disable_animations": True,
        "disable_transparency": True,
        "best_performance_mode": False,       # nuclear option — disables everything
        "optimal_decision": True,
    },

    # ── Network stack ──
    "network": {
        "enabled": True,
        "disable_nagle": True,                # TcpAckFrequency + TCPNoDelay + TcpDelAckTicks
        "set_dns_cache_size": True,
        "disable_autotuning": False,          # LEAVE FALSE — disabling breaks throughput on fast links
        "disable_rsc": True,                  # Receive Segment Coalescing off (lower DPC latency)
        "disable_ecn": True,                  # ECN off (router compatibility)
        "mmcss_tuning": True,                 # NetworkThrottlingIndex + SystemResponsiveness
        "tcp_socket_tuning": True,            # TcpTimedWaitDelay + MaxUserPort + TcpNumConnections
        "qos_reserve_fix": True,              # Remove 20% QoS bandwidth reservation
        "enforce_pmtud": True,                # Ensure Path MTU Discovery is enabled
        "flush_dns_on_switch": True,          # ipconfig /flushdns on every profile switch
        "nic_interrupt_moderation": True,     # Disable interrupt moderation per NIC (lower DPC latency)
        "nic_rss_tuning": True,               # Enforce RSS on + tune queue count per NIC
        "optimal_decision": True,
    },

    # ── Storage ──
    "storage": {
        "enabled": True,
        "ensure_trim_enabled": True,
        "disable_8dot3_names": True,
        "disable_last_access_update": True,
        "write_cache_enabled": True,
        "indexing_managed": True,             # disable indexing on NVMe, keep on HDD
        "disable_ahci_lpm": True,             # AHCI link power management off for SATA drives
        "optimal_decision": True,
    },

    # ── Privacy / telemetry ──
    "privacy": {
        "enabled": True,
        "disable_telemetry": True,
        "disable_advertising_id": True,
        "disable_activity_history": True,
        "disable_cortana": True,
        "disable_feedback_notifications": True,
        "optimal_decision": True,
    },

    # ── App-based profile switching ──
    "profiles": {
        "enabled": True,
        "detect_by_process": True,
        "detect_by_load": True,
        "gaming_gpu_threshold": 40,           # GPU% above this = likely gaming
        "gaming_cpu_threshold": 30,
        "custom_streaming_processes": [],
        "custom_gaming_processes": [],
        # User-created profiles. Each entry:
        # { "name": "video_editing",  "label": "Video Editing",
        #   "processes": ["premiere.exe", "resolve.exe"],
        #   "behavior": "streaming",   # idle | gaming | streaming
        #   "color": "#aa00ff",
        #   "priority": 50 }          # lower = checked first
        "user_profiles": [],
    },

    # ── AWCC (Alienware Command Center WMI) ──
    "awcc": {
        "enabled":          True,   # use AWCC WMI when available
        "sync_profile":     True,   # sync AWCC thermal profile with AlienCore profile
        "gmode_on_gaming":  False,  # auto-enable G-Mode in gaming (loud — opt-in)
    },

    # ── AI integration ──
    "ai": {
        "provider":              "anthropic",   # "anthropic" | "openai_compat"
        "api_key":               "",            # stored locally in config.json
        "base_url":              "",            # OpenAI-compat only; blank = https://api.openai.com/v1
        "model":                 "",            # blank = sensible default per provider
        "watchdog_model":        "",            # blank = falls back to model, then cheapest default
        "watchdog_enabled":      False,         # periodically analyze system health via AI
        "watchdog_interval_sec": 300,           # seconds between watchdog checks (min 60)
        "chat_history_max":      20,            # messages kept in rolling chat window
    },

    # ── Service behavior ──
    "service": {
        "log_enabled": True,
        "start_with_windows": True,
        "notify_on_profile_switch": False,    # toast notification on switch
        "hardware_refresh_on_startup": True,
    },
}
