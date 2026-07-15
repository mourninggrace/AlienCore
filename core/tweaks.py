"""
AlienCore - tweaks.py
Applies all system tweaks based on current config and active profile.
Every public function supports dry_run=True — logs what it WOULD do without touching anything.
Call apply_baseline() once at startup, then apply_profile() on profile switches.
"""

import ctypes
import ctypes.wintypes as wt
import logging
import os
import subprocess
import winreg
from core import config_manager as cfg
from core.constants import (
    POWER_PLAN_BALANCED, POWER_PLAN_HIGH_PERF, POWER_PLAN_POWER_SAVER,
    POWER_PLAN_ULTIMATE_PERF,
    CPU_SUBGROUP_GUID,
    CPU_SCHED_POLICY_GUID, CPU_SCHED_SHORT_GUID, CPU_HETERO_POLICY_GUID,
    CPU_PERF_AUTONOMOUS_GUID, CPU_PERF_INCREASE_THRESH_GUID,
    CPU_CORE_PARKING_MIN_GUID, CPU_CORE_PARKING_MAX_GUID,
    WIN32_PRIORITY_SEP_IDLE, WIN32_PRIORITY_SEP_WORKING,
    WIN32_PRIORITY_SEP_GAMING, WIN32_PRIORITY_SEP_STREAMING,
    BACKGROUND_PROCESSES_ECOQOS,
)

logger = logging.getLogger("aliencore.tweaks")


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def apply_baseline(hardware: dict, dry_run: bool = False):
    """
    Apply all one-time baseline tweaks at startup.
    These are settings that don't change per profile.
    """
    mode = "[DRY RUN] " if dry_run else ""
    logger.info("%sApplying baseline tweaks...", mode)
    c = cfg.get()

    if c["gpu"]["enabled"]:
        _tweak_gpu_baseline(c, hardware, dry_run)
    if c["ram"]["enabled"]:
        _tweak_ram(c, dry_run)
    if c["scheduler"]["enabled"]:
        _tweak_scheduler_baseline(c, dry_run)
    if c["visual"]["enabled"]:
        _tweak_visual(c, dry_run)
    if c["network"]["enabled"]:
        _tweak_network(c, dry_run)
    if c["storage"]["enabled"]:
        _tweak_storage(c, hardware, dry_run)
    if c["privacy"]["enabled"]:
        _tweak_privacy(c, dry_run)

    logger.info("%sBaseline tweaks complete.", mode)


def apply_profile(profile_name: str, hardware: dict, dry_run: bool = False):
    """
    Apply tweaks for the given profile:
        idle | working | gaming | streaming | manual | <user-defined>
    User-defined profiles resolve to a base behavior (idle/working/gaming/streaming)
    for tweaks. Called every time the profile switches.
    """
    mode = "[DRY RUN] " if dry_run else ""
    logger.info("%sSwitching to profile: %s", mode, profile_name)
    c = cfg.get()

    # Resolve user-defined profiles to their base behavior for tweak routing
    behavior = profile_name
    for up in c.get("profiles", {}).get("user_profiles", []):
        if up.get("name") == profile_name:
            behavior = up.get("behavior", "idle")
            logger.info("Custom profile '%s' → behavior: %s", profile_name, behavior)
            break

    if c["cpu"]["enabled"]:
        _apply_cpu_profile(behavior, c, hardware, dry_run)
    if c["gpu"]["enabled"]:
        _apply_gpu_profile(behavior, c, hardware, dry_run)
    if c["scheduler"]["enabled"]:
        _apply_scheduler_profile(behavior, c, dry_run)
    if c["network"]["enabled"] and c["network"].get("flush_dns_on_switch", True):
        _run(["ipconfig", "/flushdns"], dry_run, "Flush DNS cache")


def restore_defaults(dry_run: bool = False):
    """
    Undo AlienCore's tweaks and return system to Windows defaults.
    Called on --restore or uninstall.
    """
    mode = "[DRY RUN] " if dry_run else ""
    logger.info("%sRestoring system defaults...", mode)

    # Power plan
    _run(["powercfg", "/setactive", POWER_PLAN_BALANCED], dry_run,
         "Restore balanced power plan")
    _set_max_processor_state(100, "AC", dry_run)
    _set_max_processor_state(100, "DC", dry_run)
    _set_min_processor_state(5,   "AC", dry_run)
    _set_min_processor_state(5,   "DC", dry_run)

    # Re-enable SysMain
    _run(["sc", "config", "SysMain", "start=", "auto"], dry_run, "Re-enable SysMain")
    _run(["sc", "start",  "SysMain"],                   dry_run, "Start SysMain")

    # Re-enable telemetry
    _reg_set(r"HKLM\SOFTWARE\Policies\Microsoft\Windows\DataCollection",
             "AllowTelemetry", 1, winreg.REG_DWORD, dry_run)

    # Restore Win32PrioritySeparation to Windows default
    _reg_set(r"HKLM\SYSTEM\CurrentControlSet\Control\PriorityControl",
             "Win32PrioritySeparation", WIN32_PRIORITY_SEP_IDLE,
             winreg.REG_DWORD, dry_run)

    # Restore MMCSS defaults
    _reg_set(
        r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile",
        "NetworkThrottlingIndex", 10, winreg.REG_DWORD, dry_run
    )
    _reg_set(
        r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile",
        "SystemResponsiveness", 20, winreg.REG_DWORD, dry_run
    )

    # Restore DisablePagingExecutive
    _reg_set(
        r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management",
        "DisablePagingExecutive", 0, winreg.REG_DWORD, dry_run
    )

    # Restore TCP settings
    _run(["netsh", "interface", "tcp", "set", "global", "rsc=enabled"],
         dry_run, "Re-enable RSC")
    _run(["netsh", "interface", "tcp", "set", "global", "ecncapability=default"],
         dry_run, "Restore ECN default")
    _run(["netsh", "interface", "tcp", "set", "global", "initialRto=3000"],
         dry_run, "Restore initial RTO")

    logger.info("%sDefaults restored.", mode)


# ─────────────────────────────────────────────────────────────────────────────
# CPU
# ─────────────────────────────────────────────────────────────────────────────

def _apply_cpu_profile(profile: str, c: dict, hardware: dict, dry_run: bool):
    cpu      = c["cpu"]
    is_intel = hardware.get("cpu", {}).get("is_intel", False)

    if profile == "idle":
        _ensure_aliencore_power_plan(dry_run)
        if cpu["dynamic_throttle"]:
            ceiling = cpu["idle_max_state_pct"]
            logger.info("CPU idle ceiling: %d%%", ceiling)
            _set_max_processor_state(ceiling, "AC", dry_run)
            _set_max_processor_state(ceiling, "DC", dry_run)
        else:
            _set_max_processor_state(100, "AC", dry_run)
            _set_max_processor_state(100, "DC", dry_run)
        # Allow the CPU to drop as low as possible at idle.
        # Without this, Ultimate Performance-based plans keep min=100%, which
        # pins the CPU at the ceiling even when fully idle — it can never sleep lower.
        _set_min_processor_state(5, "AC", dry_run)
        _set_min_processor_state(5, "DC", dry_run)

    elif profile == "working":
        # Productivity tier: full ceiling for fast ramp, low floor so the CPU
        # can still drop between bursts. Responsiveness is tuned via perf
        # thresholds + half-unparked cores below — not by capping the ceiling.
        _ensure_aliencore_power_plan(dry_run)
        logger.info("CPU working: full ceiling (100%%) with low floor — "
                    "responsiveness tuned via perf thresholds")
        _set_max_processor_state(100, "AC", dry_run)
        _set_max_processor_state(100, "DC", dry_run)
        _set_min_processor_state(5, "AC", dry_run)
        _set_min_processor_state(5, "DC", dry_run)

    elif profile in ("gaming", "streaming"):
        key = "full_power_in_gaming" if profile == "gaming" else "full_power_in_streaming"
        if cpu[key]:
            logger.info("CPU %s: full power (100%%)", profile)
            _set_max_processor_state(100, "AC", dry_run)
            _set_max_processor_state(100, "DC", dry_run)

    elif profile == "manual":
        pass   # manual = tray override, don't touch CPU settings

    # Intel Thread Director + heterogeneous scheduling — Intel hybrid CPUs only.
    # These GUIDs and policies are meaningless (and harmless but noisy) on AMD Ryzen.
    if cpu.get("hetero_scheduling", True):
        if is_intel:
            _set_hetero_scheduling(profile, dry_run)
            _set_perf_autonomous(profile, dry_run)
            _set_perf_thresholds(profile, dry_run)
        else:
            logger.debug("Intel Thread Director tweaks skipped — non-Intel CPU detected")

    # Core parking is a generic Windows powercfg feature — works on Intel and AMD
    if cpu.get("core_parking_gaming", True):
        _set_core_parking(profile, dry_run)


_CNW = subprocess.CREATE_NO_WINDOW  # shorthand used throughout this module


def _ensure_aliencore_power_plan(dry_run: bool):
    """
    Create and activate AlienCore's custom power plan.
    Based on Ultimate Performance (preferred) or Balanced (fallback) so we never
    corrupt the user's built-in plans.
    """
    try:
        result = subprocess.run(
            ["powercfg", "/list"], capture_output=True, text=True,
            creationflags=_CNW, timeout=10
        )
        if "AlienCore" not in result.stdout:
            logger.info("Creating AlienCore power plan...")

            # Try Ultimate Performance as base first — it disables core parking,
            # sets aggressive P-state thresholds, and is the best gaming foundation.
            dup = subprocess.run(
                ["powercfg", "/duplicatescheme", POWER_PLAN_ULTIMATE_PERF],
                capture_output=True, text=True, creationflags=_CNW, timeout=10
            )
            if dup.returncode != 0:
                # Fallback to Balanced on systems where Ultimate Perf unavailable
                logger.info("Ultimate Performance unavailable — falling back to Balanced")
                dup = subprocess.run(
                    ["powercfg", "/duplicatescheme", POWER_PLAN_BALANCED],
                    capture_output=True, text=True, creationflags=_CNW, timeout=10
                )

            for token in dup.stdout.split():
                if len(token) == 36 and token.count("-") == 4:
                    new_guid = token
                    if not dry_run:
                        subprocess.run(["powercfg", "/changename", new_guid,
                                        "AlienCore Adaptive",
                                        "Managed by AlienCore optimizer"],
                                       capture_output=True, creationflags=_CNW,
                                       timeout=10)
                        subprocess.run(["powercfg", "/setactive", new_guid],
                                       capture_output=True, creationflags=_CNW,
                                       timeout=10)
                    logger.info("AlienCore power plan created: %s", new_guid)
                    return new_guid
        else:
            for line in result.stdout.splitlines():
                if "AlienCore" in line:
                    for token in line.split():
                        if len(token) == 36 and token.count("-") == 4:
                            if not dry_run:
                                subprocess.run(["powercfg", "/setactive", token],
                                               capture_output=True,
                                               creationflags=_CNW, timeout=10)
                            return token
    except Exception as e:
        logger.warning("Power plan setup failed: %s", e)
    return None


# Processor-state percentage is a hardware-applied value: it caps how high
# (max) or how low (min) Windows lets every core's P-state run.  A Windows
# "Processor performance state" is defined only over 0..100 %; powercfg will
# reject or silently misbehave on out-of-range input, and a corrupt config,
# a drifting learning suggestion, or an arithmetic overflow upstream could in
# principle produce a value outside that window.  This is the single choke
# point through which EVERY processor-state write in the app flows
# (apply_profile, the dynamic idle ceiling in monitor.py, the DIMM-thermal
# and TVB optimizers), so clamping here makes every applied value provably
# in-range regardless of what the caller computed.
PROC_STATE_MIN_PCT = 0
PROC_STATE_MAX_PCT = 100


def _clamp_proc_state(pct) -> int:
    """Coerce a processor-state percentage to a safe integer in 0..100.

    Non-numeric / NaN input falls back to 100 % (full performance), which is
    the safe-for-hardware default: it never *restricts* cooling headroom and
    matches Windows' own out-of-box ceiling.  Restricting (a low ceiling) is
    the only way this lever can harm responsiveness, so when the requested
    value is garbage we err toward not restricting.
    """
    try:
        f = float(pct)
    except (TypeError, ValueError):
        logger.warning("Processor-state pct %r invalid — defaulting to 100%%", pct)
        return PROC_STATE_MAX_PCT
    if f != f:  # NaN guard (NaN != NaN)
        logger.warning("Processor-state pct was NaN — defaulting to 100%%")
        return PROC_STATE_MAX_PCT
    if f == float("inf"):
        logger.warning("Processor-state pct was +inf — clamped to 100%%")
        return PROC_STATE_MAX_PCT
    if f == float("-inf"):
        logger.warning("Processor-state pct was -inf — clamped to 0%%")
        return PROC_STATE_MIN_PCT
    v = int(round(f))
    clamped = max(PROC_STATE_MIN_PCT, min(PROC_STATE_MAX_PCT, v))
    if clamped != v:
        logger.warning("Processor-state pct %s out of range — clamped to %d%%",
                       v, clamped)
    return clamped


def _set_max_processor_state(pct: int, ac_dc: str, dry_run: bool):
    """Set max processor state % on the active power plan."""
    pct  = _clamp_proc_state(pct)
    sub  = "54533251-82be-4824-96c1-47b60b740d00"
    guid = "bc5038f7-23e0-4960-96da-33abaf5935ec"
    scheme = _get_active_scheme()
    if not scheme:
        return
    args = ["powercfg", "/setacvalueindex" if ac_dc == "AC" else "/setdcvalueindex",
            scheme, sub, guid, str(pct)]
    _run(args, dry_run, f"Set max processor state {pct}% ({ac_dc})")
    _run(["powercfg", "/setactive", scheme], dry_run, "Apply power plan")


def _set_min_processor_state(pct: int, ac_dc: str, dry_run: bool):
    pct  = _clamp_proc_state(pct)
    sub  = "54533251-82be-4824-96c1-47b60b740d00"
    guid = "893dee8e-2bef-41e0-89c6-b55d0929964c"
    scheme = _get_active_scheme()
    if not scheme:
        return
    args = ["powercfg", "/setacvalueindex" if ac_dc == "AC" else "/setdcvalueindex",
            scheme, sub, guid, str(pct)]
    _run(args, dry_run, f"Set min processor state {pct}% ({ac_dc})")
    _run(["powercfg", "/setactive", scheme], dry_run, "Apply power plan")


_scheme_cache: "str | None" = None


def _invalidate_scheme_cache():
    global _scheme_cache
    _scheme_cache = None


def _get_active_scheme() -> "str | None":
    global _scheme_cache
    if _scheme_cache:
        return _scheme_cache
    try:
        result = subprocess.run(["powercfg", "/getactivescheme"],
                                capture_output=True, text=True,
                                creationflags=_CNW, timeout=5)
        for token in result.stdout.split():
            if len(token) == 36 and token.count("-") == 4:
                _scheme_cache = token
                return token
    except Exception as e:
        logger.warning("Could not get active power scheme: %s", e)
    return None


def _set_hetero_scheduling(profile: str, dry_run: bool):
    """
    Configure Intel Thread Director + heterogeneous scheduling policy.
    i9-14900HX: 8 P-cores (HT = LP 0-15) + 16 E-cores (LP 16-31).
    - SCHEDPOLICY 5 = Automatic: ITD microcontroller guides thread placement.
    - HETERO_POLICY 3 = HeteroParking: fill P-cores first, overflow to E-cores.
    - SHORT_THREAD 2 = PreferPerformant: snap short bursts (<1ms) to P-cores.
    """
    scheme = _get_active_scheme()
    if not scheme:
        return

    def _pow(guid, val, label):
        _run(["powercfg", "/setacvalueindex", scheme, CPU_SUBGROUP_GUID, guid, str(val)],
             dry_run, label)

    # ITD-guided scheduling for all profiles — let the hardware direct threads
    _pow(CPU_SCHED_POLICY_GUID, 5, "Sched policy: Automatic (ITD)")
    # Snap short-lived threads to P-cores for fast response
    _pow(CPU_SCHED_SHORT_GUID,  2, "Short-thread policy: PreferPerformant")
    # Fill P-cores first, overflow to E-cores (best for gaming and streaming)
    _pow(CPU_HETERO_POLICY_GUID, 3, "Hetero policy: HeteroParking")

    _run(["powercfg", "/setactive", scheme], dry_run, "Apply hetero scheduling")
    logger.debug("Hetero scheduling applied (profile=%s)", profile)


def _set_perf_autonomous(profile: str, dry_run: bool):
    """
    PERFAUTONOMOUS (CPPC2): controls whether CPU hardware or OS drives P-state.
    Always OS-controlled (0) for all profiles.

    HW-guided (1) sounds better for idle efficiency, but Intel Raptor Lake
    ignores the OS max-processor-state ceiling in autonomous mode and boosts
    to near-max on any workload signal.  OS-controlled strictly enforces the
    idle_max_state_pct ceiling, which is the main lever for keeping idle temps
    in check.  Gaming/streaming also benefits from predictable P-state control.
    """
    scheme = _get_active_scheme()
    if not scheme:
        return

    _run(
        ["powercfg", "/setacvalueindex", scheme, CPU_SUBGROUP_GUID,
         CPU_PERF_AUTONOMOUS_GUID, "0"],
        dry_run, "PERFAUTONOMOUS=OS-controlled (enforces ceiling cap)"
    )
    _run(["powercfg", "/setactive", scheme], dry_run, "Apply autonomous mode")


def _set_core_parking(profile: str, dry_run: bool):
    """
    Core parking: how many logical processors Windows keeps unparked.
    - Gaming/streaming: 100% min/max = all 32 LPs always active (eliminates wake-up stutter).
    - Working: 50% min = half the cores guaranteed online so multi-app burst
      load doesn't pay the wake penalty on every context switch.
    - Idle: 5% min = allow aggressive parking for power savings.
    """
    scheme = _get_active_scheme()
    if not scheme:
        return

    if profile in ("gaming", "streaming"):
        min_pct, label = 100, "all cores unparked"
    elif profile == "working":
        min_pct, label = 50, "half cores guaranteed online"
    else:
        min_pct, label = 5, "parking enabled"

    _run(["powercfg", "/setacvalueindex", scheme, CPU_SUBGROUP_GUID,
          CPU_CORE_PARKING_MIN_GUID, str(min_pct)],
         dry_run, f"Core parking min: {min_pct}% ({label})")
    _run(["powercfg", "/setacvalueindex", scheme, CPU_SUBGROUP_GUID,
          CPU_CORE_PARKING_MAX_GUID, "100"],
         dry_run, "Core parking max: 100%")
    _run(["powercfg", "/setactive", scheme], dry_run, "Apply core parking")


def _set_perf_thresholds(profile: str, dry_run: bool):
    """
    Performance increase threshold: CPU utilization % that triggers a P-state step-up.
    - Gaming/streaming: 15% — respond instantly to any load spike.
    - Working: 30% — fast ramp on burst load (multi-app productivity) but not
      so eager that single-tab background work pegs the CPU.
    - Idle: 50% — require sustained load before stepping up (saves power).
    """
    scheme = _get_active_scheme()
    if not scheme:
        return

    if profile in ("gaming", "streaming"):
        thresh = 15
    elif profile == "working":
        thresh = 30
    else:
        thresh = 50
    _run(
        ["powercfg", "/setacvalueindex", scheme, CPU_SUBGROUP_GUID,
         CPU_PERF_INCREASE_THRESH_GUID, str(thresh)],
        dry_run, f"Perf increase threshold: {thresh}% (profile={profile})"
    )
    _run(["powercfg", "/setactive", scheme], dry_run, "Apply perf threshold")


# ─────────────────────────────────────────────────────────────────────────────
# GPU
# ─────────────────────────────────────────────────────────────────────────────

def _tweak_gpu_baseline(c: dict, hardware: dict, dry_run: bool):
    """GPU baseline tweaks applied once at startup."""
    gpu = c["gpu"]

    # Hardware-Accelerated GPU Scheduling — REQUIRED for DLSS 3 Frame Generation.
    # Without this, Frame Generation on RTX 40-series is silently disabled by the driver.
    if gpu.get("hags_enabled", True):
        logger.info("GPU: enabling Hardware-Accelerated GPU Scheduling (HAGS)")
        _reg_set(
            r"HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers",
            "HwSchMode", 2, winreg.REG_DWORD, dry_run
        )

    has_nvidia = any(g.get("is_nvidia") for g in hardware.get("gpu", []))
    plat = hardware.get("platform", {})

    # nvidia-smi persistent mode — keeps the driver loaded, reduces first-frame latency
    if has_nvidia and plat.get("has_nvidia_smi"):
        _run(["nvidia-smi", "-pm", "1"], dry_run, "Enable nvidia-smi persistent mode")

    # PowerMizer max performance — ensure GPU always targets highest P-state on AC
    if has_nvidia and gpu.get("powermizer_max_performance", True):
        _set_powermizer_max_performance(hardware, dry_run)


def _set_powermizer_max_performance(hardware: dict, dry_run: bool):
    """
    Set NVIDIA PowerMizer to max performance mode via registry.
    This mirrors the 'Prefer Maximum Performance' setting in NVIDIA Control Panel.
    PowerMizerLevelAC=1 forces max clocks on AC power.
    """
    try:
        video_key = r"SYSTEM\CurrentControlSet\Control\Video"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, video_key) as vk:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(vk, i)
                    adapter_path = f"{video_key}\\{subkey_name}\\0000"
                    try:
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, adapter_path) as ak:
                            try:
                                desc, _ = winreg.QueryValueEx(ak, "Device Description")
                                if "nvidia" in desc.lower():
                                    reg_path = f"HKLM\\{adapter_path}"
                                    _reg_set(reg_path, "PowerMizerEnable",    1,      winreg.REG_DWORD, dry_run)
                                    _reg_set(reg_path, "PowerMizerLevel",     1,      winreg.REG_DWORD, dry_run)
                                    _reg_set(reg_path, "PowerMizerLevelAC",   1,      winreg.REG_DWORD, dry_run)
                                    _reg_set(reg_path, "PerfLevelSrc",        0x2222, winreg.REG_DWORD, dry_run)
                                    logger.info("GPU: PowerMizer max performance set (%s)", desc.strip())
                            except FileNotFoundError:
                                pass
                    except (FileNotFoundError, OSError):
                        pass
                    i += 1
                except OSError:
                    break
    except Exception as e:
        logger.debug("PowerMizer registry error: %s", e)


def _apply_gpu_profile(profile: str, c: dict, hardware: dict, dry_run: bool):
    """
    Sync AWCC thermal profile with the current AlienCore profile.
    On laptop GPUs, AWCC is the only lever for GPU power budget (nvidia-smi
    power limit is blocked by WDDM on mobile RTX). Profile mapping:
      idle      → Balanced (0xA0)
      gaming    → Performance (0xA4)
      streaming → Balanced Performance (0xA1)
      manual    → no change
    """
    awcc_cfg = c.get("awcc", {})
    if not awcc_cfg.get("enabled", True) or not awcc_cfg.get("sync_profile", True):
        logger.debug("AWCC profile sync disabled in config")
        return
    if dry_run:
        logger.info("[DRY RUN] Would sync AWCC profile: %s", profile)
        return
    try:
        from core import awcc, sensors
        # Check availability from the cached sensor readings — never call
        # awcc.is_available() from this thread (COM STA: WMI belongs to SensorThread).
        if not sensors.get_readings().get("awcc_available", False):
            logger.debug("AWCC WMI unavailable — profile not synced")
            return

        # Enqueue write operations to run on the SensorThread next poll cycle.
        _profile = profile
        _gmode_cfg = awcc_cfg.get("gmode_on_gaming", False)
        awcc.enqueue(lambda: awcc.apply_aliencore_profile(_profile))
        if _gmode_cfg:
            if _profile == "gaming":
                awcc.enqueue(lambda: awcc.set_gmode(True))
            elif _profile in ("idle", "streaming"):
                awcc.enqueue(lambda: awcc.set_gmode(False))
        logger.info("AWCC thermal profile queued: %s", profile)
    except Exception as e:
        logger.debug("AWCC profile sync error: %s", e)


def _get_gpu_tdp(hardware: dict) -> float:
    for gpu in hardware.get("gpu", []):
        if gpu.get("is_nvidia") and gpu.get("tdp_watts"):
            return float(gpu["tdp_watts"])
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Scheduler / process priority
# ─────────────────────────────────────────────────────────────────────────────

def _tweak_scheduler_baseline(c: dict, dry_run: bool):
    """
    Apply baseline scheduler and MMCSS settings that don't change per profile.
    """
    sched = c.get("scheduler", {})
    if not sched.get("mmcss_tuning", True) and not sched.get("win32_priority_separation", True):
        return

    mmcss_path = r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"

    if sched.get("mmcss_tuning", True):
        logger.info("Scheduler: applying MMCSS baseline tuning")

        # NetworkThrottlingIndex: 0xFFFFFFFF removes the default ~10k pps cap.
        # Beneficial for streaming + gaming workloads simultaneously.
        _reg_set(mmcss_path, "NetworkThrottlingIndex", 0xFFFFFFFF,
                 winreg.REG_DWORD, dry_run)

        # SystemResponsiveness: 10 is the minimum effective value (MMCSS clamps <10 to 20).
        # Allocates 10% CPU to background tasks, 90% to multimedia/game threads.
        _reg_set(mmcss_path, "SystemResponsiveness", 10,
                 winreg.REG_DWORD, dry_run)

        # Keep MMCSS always active, not lazy-sleeping
        _reg_set(mmcss_path, "NoLazyMode", 1, winreg.REG_DWORD, dry_run)

        # MMCSS Games task — ensure it uses High scheduling category.
        # Games that call AvSetMmThreadCharacteristics("Games") get priority 23-26.
        games_task = (r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion"
                      r"\Multimedia\SystemProfile\Tasks\Games")
        _reg_set(games_task, "Scheduling Category", "High", winreg.REG_SZ, dry_run)
        _reg_set(games_task, "Latency Sensitive",   "True", winreg.REG_SZ, dry_run)
        _reg_set(games_task, "Priority",            6,      winreg.REG_DWORD, dry_run)
        _reg_set(games_task, "Clock Rate",          10000,  winreg.REG_DWORD, dry_run)


def _apply_scheduler_profile(profile_name: str, c: dict, dry_run: bool):
    """
    Per-profile scheduler settings: Win32PrioritySeparation quanta tuning
    and EcoQoS routing of background processes.
    """
    sched = c.get("scheduler", {})

    if sched.get("win32_priority_separation", True):
        # Win32PrioritySeparation controls scheduler quantum length and type,
        # plus foreground thread boost relative to background threads.
        # Fixed-length quanta (gaming/streaming) eliminate micro-stutters caused
        # by the scheduler stretching time slices under load.  Working uses
        # variable quanta — productivity is bursty, fast FG response without
        # starving background tasks.
        sep_map = {
            "gaming":    WIN32_PRIORITY_SEP_GAMING,    # 42: fixed quanta, high FG boost
            "streaming": WIN32_PRIORITY_SEP_STREAMING, # 41: fixed quanta, medium FG boost
            "working":   WIN32_PRIORITY_SEP_WORKING,   # 38: variable quanta, high FG boost
            "idle":      WIN32_PRIORITY_SEP_IDLE,      # 38: variable quanta (Windows default)
            "manual":    WIN32_PRIORITY_SEP_IDLE,
        }
        val = sep_map.get(profile_name, WIN32_PRIORITY_SEP_IDLE)
        _reg_set(
            r"HKLM\SYSTEM\CurrentControlSet\Control\PriorityControl",
            "Win32PrioritySeparation", val, winreg.REG_DWORD, dry_run
        )
        logger.debug("Win32PrioritySeparation: %d (profile=%s)", val, profile_name)

    if sched.get("ecoqos_background", True):
        _apply_ecoqos_to_background(profile_name, dry_run)


def _apply_ecoqos_to_background(profile: str, dry_run: bool):
    """
    EcoQoS: route background processes to E-cores during gaming via
    SetProcessInformation(ProcessPowerThrottling). Only meaningful on hybrid CPUs
    (i9-14900HX has 16 E-cores that can absorb Discord, browsers, etc. without
    stealing P-cores from the game).
    Reverted to normal scheduling when leaving gaming profile.
    """
    if dry_run:
        logger.info("[DRY RUN] EcoQoS: would route background procs to E-cores (profile=%s)",
                    profile)
        return

    try:
        import psutil

        # ProcessPowerThrottling constants
        PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
        PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
        ProcessPowerThrottling = 4
        PROCESS_SET_INFORMATION = 0x0200

        class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
            _fields_ = [
                ("Version",     wt.DWORD),
                ("ControlMask", wt.DWORD),
                ("StateMask",   wt.DWORD),
            ]

        kernel32 = ctypes.windll.kernel32
        enable   = (profile == "gaming")
        bg_lower = {p.lower() for p in BACKGROUND_PROCESSES_ECOQOS}
        changed  = 0

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = (proc.info["name"] or "").lower()
                if name not in bg_lower:
                    continue
                pid    = proc.info["pid"]
                handle = kernel32.OpenProcess(PROCESS_SET_INFORMATION, False, pid)
                if not handle:
                    continue
                pts             = PROCESS_POWER_THROTTLING_STATE()
                pts.Version     = PROCESS_POWER_THROTTLING_CURRENT_VERSION
                pts.ControlMask = PROCESS_POWER_THROTTLING_EXECUTION_SPEED
                pts.StateMask   = PROCESS_POWER_THROTTLING_EXECUTION_SPEED if enable else 0
                kernel32.SetProcessInformation(
                    handle,
                    ProcessPowerThrottling,
                    ctypes.byref(pts),
                    ctypes.sizeof(pts),
                )
                kernel32.CloseHandle(handle)
                changed += 1
            except Exception:
                pass

        if changed:
            action = "E-core routed" if enable else "restored"
            logger.info("EcoQoS: %d background process(es) %s (profile=%s)",
                        changed, action, profile)

    except Exception as e:
        logger.debug("EcoQoS apply error: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# RAM
# ─────────────────────────────────────────────────────────────────────────────

def _tweak_ram(c: dict, dry_run: bool):
    ram = c["ram"]
    mem_mgmt = r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"

    if ram["disable_superfetch"]:
        logger.info("RAM: disabling SysMain (Superfetch)")
        _run(["sc", "stop",   "SysMain"], dry_run, "Stop SysMain")
        _run(["sc", "config", "SysMain", "start=", "disabled"],
             dry_run, "Disable SysMain autostart")

    # Keep kernel-mode code and drivers pinned in RAM — never paged to disk.
    # On 32GB+ DDR5 the cost is negligible; prevents rare latency spikes from
    # kernel page faults during gaming.
    if ram.get("disable_paging_executive", True):
        logger.info("RAM: disabling paging executive (kernel pinned in RAM)")
        _reg_set(mem_mgmt, "DisablePagingExecutive", 1, winreg.REG_DWORD, dry_run)

    if ram["clear_standby_cache_on_idle"]:
        logger.info("RAM: standby cache clearing enabled (runs at idle when RAM >70%%)")

    if not ram["pagefile_managed"]:
        try:
            mb = int(ram["pagefile_custom_mb"])
        except (TypeError, ValueError):
            logger.warning("RAM: pagefile_custom_mb is not an integer — skipping")
            mb = 0
        if not (0 < mb <= 65536):
            mb = 0
        if mb > 0:
            logger.info("RAM: setting custom pagefile: %d MB", mb)
            script = (
                f"$pf = Get-WmiObject Win32_PageFileSetting; "
                f"if ($pf) {{ $pf.InitialSize = {mb}; $pf.MaximumSize = {mb}; $pf.Put() }}"
            )
            _run(["powershell", "-Command", script], dry_run, f"Set pagefile {mb}MB")


# ─────────────────────────────────────────────────────────────────────────────
# Visual effects
# ─────────────────────────────────────────────────────────────────────────────

def _tweak_visual(c: dict, dry_run: bool):
    vis = c["visual"]

    if vis["best_performance_mode"]:
        logger.info("Visual effects: best performance (all off)")
        _reg_set(
            r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects",
            "VisualFXSetting", 2, winreg.REG_DWORD, dry_run
        )
        return

    if vis["optimal_decision"] or vis["disable_animations"]:
        logger.info("Visual effects: disabling animations")
        _reg_set(
            r"HKCU\Control Panel\Desktop",
            "UserPreferencesMask",
            b'\x90\x12\x03\x80\x10\x00\x00\x00',
            winreg.REG_BINARY, dry_run
        )
        _reg_set(
            r"HKCU\Control Panel\Desktop\WindowMetrics",
            "MinAnimate", "0", winreg.REG_SZ, dry_run
        )

    if vis["optimal_decision"] or vis["disable_transparency"]:
        logger.info("Visual effects: disabling transparency")
        _reg_set(
            r"HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            "EnableTransparency", 0, winreg.REG_DWORD, dry_run
        )


# ─────────────────────────────────────────────────────────────────────────────
# Network
# ─────────────────────────────────────────────────────────────────────────────

def _tweak_network(c: dict, dry_run: bool):
    net = c["network"]

    # Disable Nagle's algorithm and delayed ACKs per network interface
    if net["optimal_decision"] or net["disable_nagle"]:
        logger.info("Network: disabling Nagle + delayed ACK on all interfaces")
        base = r"HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
                                ) as interfaces:
                i = 0
                while True:
                    try:
                        subkey = winreg.EnumKey(interfaces, i)
                        _reg_set(f"{base}\\{subkey}", "TcpAckFrequency",
                                 1, winreg.REG_DWORD, dry_run)
                        _reg_set(f"{base}\\{subkey}", "TCPNoDelay",
                                 1, winreg.REG_DWORD, dry_run)
                        # TcpDelAckTicks=0 ensures ACKs are sent immediately
                        # (supplements TcpAckFrequency for sub-tick responsiveness)
                        _reg_set(f"{base}\\{subkey}", "TcpDelAckTicks",
                                 0, winreg.REG_DWORD, dry_run)
                        i += 1
                    except OSError:
                        break
        except Exception as e:
            logger.warning("Nagle disable failed: %s", e)

    if net["optimal_decision"] or net["set_dns_cache_size"]:
        logger.info("Network: increasing DNS cache TTL")
        _reg_set(
            r"HKLM\SYSTEM\CurrentControlSet\Services\Dnscache\Parameters",
            "MaxCacheEntryTtlLimit", 86400, winreg.REG_DWORD, dry_run
        )
        _reg_set(
            r"HKLM\SYSTEM\CurrentControlSet\Services\Dnscache\Parameters",
            "MaxSOACacheEntryTtlLimit", 300, winreg.REG_DWORD, dry_run
        )

    # RSC (Receive Segment Coalescing) — coalesces packets in the NIC driver, adding
    # latency. Disable for lowest DPC interrupt latency during gaming/streaming.
    if net.get("disable_rsc", True):
        logger.info("Network: disabling Receive Segment Coalescing (RSC)")
        _run(["netsh", "interface", "tcp", "set", "global", "rsc=disabled"],
             dry_run, "Disable RSC")

    # ECN (Explicit Congestion Notification) — can cause packet loss and stalls
    # with consumer routers that mishandle ECN. Safe to disable for gaming.
    if net.get("disable_ecn", True):
        logger.info("Network: disabling ECN")
        _run(["netsh", "interface", "tcp", "set", "global", "ecncapability=disabled"],
             dry_run, "Disable ECN")

    # Faster initial retransmit timeout (default 3000ms → 2000ms)
    _run(["netsh", "interface", "tcp", "set", "global", "initialRto=2000"],
         dry_run, "Lower initial RTO to 2000ms")

    # NOTE: autotuning is intentionally left at 'normal' — disabling it to a fixed
    # 64KB window causes severe throughput degradation on gigabit connections.
    # The disable_autotuning config option exists but defaults to False for this reason.
    if net["disable_autotuning"]:
        logger.info("Network: disabling TCP autotuning (user override — may hurt throughput)")
        _run(["netsh", "int", "tcp", "set", "global", "autotuninglevel=disabled"],
             dry_run, "Disable TCP autotuning")

    # TCP socket pool tuning
    if net.get("tcp_socket_tuning", True):
        logger.info("Network: tuning TCP socket limits")
        tcpip = r"HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters"
        # Shrink TIME_WAIT hold from 240 s → 30 s; frees ports faster under churn
        _reg_set(tcpip, "TcpTimedWaitDelay",   30,       winreg.REG_DWORD, dry_run)
        # Raise ephemeral port ceiling from ~16 k → 65534
        _reg_set(tcpip, "MaxUserPort",         65534,    winreg.REG_DWORD, dry_run)
        # Raise max simultaneous TCP connections
        _reg_set(tcpip, "TcpNumConnections",   16777214, winreg.REG_DWORD, dry_run)

    # QoS bandwidth reservation — Windows reserves 20 % by default; reclaim it
    if net.get("qos_reserve_fix", True):
        logger.info("Network: removing QoS 20%% bandwidth reservation")
        _reg_set(
            r"HKLM\SOFTWARE\Policies\Microsoft\Windows\Psched",
            "NonBestEffortLimit", 0, winreg.REG_DWORD, dry_run
        )

    # Path MTU Discovery — ensures optimal packet sizing end-to-end
    if net.get("enforce_pmtud", True):
        logger.info("Network: enforcing Path MTU Discovery")
        _reg_set(
            r"HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters",
            "EnablePMTUDiscovery", 1, winreg.REG_DWORD, dry_run
        )

    # Disable Energy-Efficient Ethernet per adapter — EEE introduces latency jitter
    # by letting the PHY enter a low-power state between bursts.
    if net["optimal_decision"]:
        _disable_nic_power_features(dry_run)


def _disable_nic_power_features(dry_run: bool):
    """
    Per-NIC adapter tuning via device class registry.
    Skips virtual/VPN/loopback adapters.
    Applied under optimal_decision for: EEE, flow control, interrupt moderation, RSS.
    """
    import multiprocessing
    # Use P-core count as RSS queue target (half of logical processors on HT/hybrid)
    rss_queues = str(max(4, multiprocessing.cpu_count() // 2))

    nic_class = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}"
    net = cfg.get().get("network", {})
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, nic_class) as ck:
            i = 0
            while True:
                try:
                    inst = winreg.EnumKey(ck, i)
                    inst_path = f"{nic_class}\\{inst}"
                    try:
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, inst_path) as ik:
                            try:
                                desc, _ = winreg.QueryValueEx(ik, "DriverDesc")
                                skip_words = ["virtual", "vpn", "tunnel", "loopback",
                                              "miniport", "tap", "wan"]
                                if any(w in desc.lower() for w in skip_words):
                                    i += 1
                                    continue
                                reg_path = f"HKLM\\{inst_path}"
                                # Energy-Efficient Ethernet — off eliminates PHY sleep jitter
                                _reg_set(reg_path, "*EEE",         "0", winreg.REG_SZ, dry_run)
                                # Flow control off — prevents spurious PAUSE frames
                                _reg_set(reg_path, "*FlowControl", "0", winreg.REG_SZ, dry_run)
                                # Interrupt moderation — batches NIC interrupts to reduce CPU load
                                # but adds latency. Off = lowest DPC latency on a gaming rig.
                                if net.get("nic_interrupt_moderation", True):
                                    _reg_set(reg_path, "*InterruptModeration",
                                             "0", winreg.REG_SZ, dry_run)
                                # RSS — spread receive load across cores; enforce on and
                                # tune queue count to match P-core count.
                                if net.get("nic_rss_tuning", True):
                                    _reg_set(reg_path, "*RSS",
                                             "1", winreg.REG_SZ, dry_run)
                                    _reg_set(reg_path, "*NumRssQueues",
                                             rss_queues, winreg.REG_SZ, dry_run)
                                logger.debug("NIC tuned: %s", desc)
                            except FileNotFoundError:
                                pass
                    except (FileNotFoundError, OSError):
                        pass
                    i += 1
                except OSError:
                    break
    except Exception as e:
        logger.debug("NIC tuning error: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────────────────────────────────────

def _tweak_storage(c: dict, hardware: dict, dry_run: bool):
    stor = c["storage"]

    if stor["optimal_decision"] or stor["ensure_trim_enabled"]:
        logger.info("Storage: ensuring TRIM is enabled")
        _run(["fsutil", "behavior", "set", "disabledeletenotify", "0"],
             dry_run, "Enable TRIM")

    if stor["optimal_decision"] or stor["disable_8dot3_names"]:
        logger.info("Storage: disabling 8.3 filename generation")
        _run(["fsutil", "behavior", "set", "disable8dot3", "1"],
             dry_run, "Disable 8.3 names")

    if stor["optimal_decision"] or stor["disable_last_access_update"]:
        logger.info("Storage: disabling last access timestamp")
        _run(["fsutil", "behavior", "set", "disablelastaccess", "1"],
             dry_run, "Disable last access update")

    if stor["write_cache_enabled"]:
        # Note: IoPageLockLimit is ignored in Windows 10/11 — not set here.
        logger.info("Storage: write caching enabled (managed by driver)")

    if stor["optimal_decision"] or stor["indexing_managed"]:
        _manage_indexing(hardware, dry_run)

    # Disable AHCI Link Power Management for SATA drives.
    # HIPM/DIPM let the host and device negotiate power states, adding access latency.
    # NVMe drives use APST (Autonomous Power State Transitions) — not affected here.
    if stor.get("disable_ahci_lpm", True):
        _set_ahci_lpm_disabled(hardware, dry_run)


def _manage_indexing(hardware: dict, dry_run: bool):
    """Disable Windows Search indexing on NVMe drives, leave on HDD."""
    drives   = hardware.get("drives", [])
    has_nvme = any(d.get("is_nvme") for d in drives)
    has_hdd  = any(d.get("is_hdd")  for d in drives)

    if has_nvme and not has_hdd:
        logger.info("Storage: disabling indexing (NVMe-only system)")
        _run(["sc", "stop",   "WSearch"], dry_run, "Stop Windows Search")
        _run(["sc", "config", "WSearch", "start=", "disabled"],
             dry_run, "Disable Windows Search")
    elif has_nvme and has_hdd:
        logger.info("Storage: mixed NVMe+HDD — leaving indexing enabled (needed for HDD)")
    else:
        logger.info("Storage: no NVMe detected — leaving indexing as-is")


def _set_ahci_lpm_disabled(hardware: dict, dry_run: bool):
    """
    Disable AHCI Link Power Management (HIPM + DIPM) for SATA drives.
    Only applied if SATA drives are present — NVMe uses APST, not AHCI LPM.
    """
    drives   = hardware.get("drives", [])
    has_sata = any(
        d.get("interface", "").lower() in ("sata", "ide") for d in drives
    )
    if not has_sata:
        logger.debug("Storage: no SATA drives detected — AHCI LPM skip")
        return

    scheme = _get_active_scheme()
    if not scheme:
        return

    DISK_SUBGROUP = "0012ee47-9041-4b5d-9b77-535fba8b1442"
    AHCI_LPM_GUID = "0b2d69d7-a2a1-449c-9680-f91c70521c60"

    logger.info("Storage: disabling AHCI link power management (SATA)")
    # Expose hidden setting first
    _run(["powercfg", "-attributes", DISK_SUBGROUP, AHCI_LPM_GUID, "-ATTRIB_HIDE"],
         dry_run, "Expose AHCI LPM setting")
    # Value 0 = Active (LPM fully disabled — lowest latency)
    _run(["powercfg", "/setacvalueindex", scheme, DISK_SUBGROUP, AHCI_LPM_GUID, "0"],
         dry_run, "Set AHCI LPM to Active (disabled)")
    _run(["powercfg", "/setactive", scheme], dry_run, "Apply AHCI LPM setting")


# ─────────────────────────────────────────────────────────────────────────────
# Privacy
# ─────────────────────────────────────────────────────────────────────────────

def _tweak_privacy(c: dict, dry_run: bool):
    priv = c["privacy"]

    if priv["optimal_decision"] or priv["disable_telemetry"]:
        logger.info("Privacy: disabling telemetry")
        _run(["sc", "stop",   "DiagTrack"], dry_run, "Stop DiagTrack")
        _run(["sc", "config", "DiagTrack", "start=", "disabled"],
             dry_run, "Disable DiagTrack")
        _reg_set(
            r"HKLM\SOFTWARE\Policies\Microsoft\Windows\DataCollection",
            "AllowTelemetry", 0, winreg.REG_DWORD, dry_run
        )

    if priv["optimal_decision"] or priv["disable_advertising_id"]:
        logger.info("Privacy: disabling advertising ID")
        _reg_set(
            r"HKCU\Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo",
            "Enabled", 0, winreg.REG_DWORD, dry_run
        )

    if priv["optimal_decision"] or priv["disable_activity_history"]:
        logger.info("Privacy: disabling activity history")
        _reg_set(
            r"HKLM\SOFTWARE\Policies\Microsoft\Windows\System",
            "PublishUserActivities", 0, winreg.REG_DWORD, dry_run
        )
        _reg_set(
            r"HKLM\SOFTWARE\Policies\Microsoft\Windows\System",
            "UploadUserActivities", 0, winreg.REG_DWORD, dry_run
        )

    if priv["optimal_decision"] or priv["disable_cortana"]:
        logger.info("Privacy: disabling Cortana")
        _reg_set(
            r"HKLM\SOFTWARE\Policies\Microsoft\Windows\Windows Search",
            "AllowCortana", 0, winreg.REG_DWORD, dry_run
        )

    if priv["optimal_decision"] or priv["disable_feedback_notifications"]:
        logger.info("Privacy: disabling feedback notifications")
        _reg_set(r"HKCU\Software\Microsoft\Siuf\Rules",
                 "NumberOfSIUFInPeriod", 0, winreg.REG_DWORD, dry_run)
        _reg_set(r"HKCU\Software\Microsoft\Siuf\Rules",
                 "PeriodInNanoSeconds", 0, winreg.REG_DWORD, dry_run)


# ─────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run(args: list, dry_run: bool, description: str = ""):
    """Run a shell command, or just log it in dry-run mode."""
    cmd = " ".join(str(a) for a in args)
    if dry_run:
        logger.info("[DRY RUN] Would run: %s  (%s)", cmd, description)
        return
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=15,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode != 0 and result.stderr:
            logger.warning("Command warning [%s]: %s", description, result.stderr.strip())
        else:
            logger.debug("OK [%s]: %s", description, cmd)
    except FileNotFoundError:
        logger.warning("Command not found [%s]: %s", description, args[0])
    except Exception as e:
        logger.error("Command failed [%s]: %s", description, e)


def _reg_set(path: str, name: str, value, reg_type, dry_run: bool):
    """Write a registry value, creating keys as needed."""
    if dry_run:
        logger.info("[DRY RUN] Registry: %s\\%s = %r", path, name, value)
        return
    try:
        hive_map = {
            "HKLM": winreg.HKEY_LOCAL_MACHINE,
            "HKCU": winreg.HKEY_CURRENT_USER,
        }
        parts    = path.split("\\")
        hive_str = parts[0]
        subpath  = "\\".join(parts[1:])
        if hive_str not in hive_map:
            logger.error("Registry write rejected — unknown hive prefix %r in %s",
                         hive_str, path)
            return
        hive = hive_map[hive_str]

        with winreg.CreateKeyEx(hive, subpath, 0,
                                winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
            winreg.SetValueEx(key, name, 0, reg_type, value)
        logger.debug("Registry set: %s\\%s", path, name)
    except PermissionError:
        logger.warning("Registry permission denied: %s\\%s (need admin?)", path, name)
    except Exception as e:
        logger.error("Registry error [%s\\%s]: %s", path, name, e)


# ─────────────────────────────────────────────────────────────────────────────
# VRAM idle clock lock
# ─────────────────────────────────────────────────────────────────────────────

def set_vram_clock_lock(enabled: bool, mem_mhz: int = 405, dry_run: bool = False) -> tuple:
    """
    Lock or release VRAM memory clock via nvidia-smi.
    When enabled: locks memory clock to mem_mhz (low idle state, saves ~10-15W).
    When disabled: resets application clocks so the driver can boost freely.
    Returns (success: bool, message: str).
    """
    from core import hardware as hw_mod
    hw = hw_mod.get_cached()
    if not any(g.get("is_nvidia") for g in hw.get("gpu", [])):
        names = ", ".join(g.get("name", "Unknown") for g in hw.get("gpu", [])) or "none"
        return False, f"VRAM clock control requires NVIDIA GPU (detected: {names})."

    try:
        if dry_run:
            action = f"lock mem={mem_mhz}MHz" if enabled else "reset clocks"
            logger.info("[DRY RUN] VRAM clock: %s", action)
            return True, f"[DRY RUN] {action}"

        if enabled:
            # Lock memory clock (GPU clock follows VRAM in P-state management)
            r = subprocess.run(
                ["nvidia-smi", f"--lock-memory-clocks={mem_mhz},{mem_mhz}"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if r.returncode == 0:
                logger.info("VRAM clock locked to %d MHz", mem_mhz)
                return True, f"VRAM clock locked to {mem_mhz} MHz."
            return False, (r.stderr or r.stdout).strip() or "nvidia-smi returned non-zero"
        else:
            r = subprocess.run(
                ["nvidia-smi", "--reset-memory-clocks"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if r.returncode == 0:
                logger.info("VRAM clock lock released")
                return True, "VRAM clock lock released — driver manages freely."
            return False, (r.stderr or r.stdout).strip() or "nvidia-smi returned non-zero"
    except FileNotFoundError:
        return False, "nvidia-smi not found."
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────────────────────
# Interrupt affinity steering
# ─────────────────────────────────────────────────────────────────────────────

def apply_interrupt_steering(dry_run: bool = False) -> tuple:
    """
    Steer performance-critical interrupt sources toward performance cores.
    Uses device class registry keys to set InterruptPolicyMask for NIC, GPU,
    and storage controllers.

    The affinity mask is computed from the actual CPU topology so this works
    correctly on both Intel hybrid CPUs (P-cores only) and AMD Ryzen (all cores
    are performance cores — mask covers all logical processors).

    Returns (success: bool, message: str).
    """
    from core import cpu_topology
    topo   = cpu_topology.get_topology()
    p_list = topo.get("p_cores", [])
    if not p_list:
        return False, "No performance cores detected — cannot compute interrupt affinity mask."
    P_MASK = sum(1 << lp for lp in p_list)

    # Interrupt affinity policy registry path
    BASE    = r"HKLM\SYSTEM\CurrentControlSet\Control\PnP\Pci"

    # Devices to steer to P-cores: NIC, NVME, GPU (class GUIDs)
    # These are device class GUIDs that get interrupt policy overrides
    TARGET_CLASSES = {
        "{4d36e972-e325-11ce-bfc1-08002be10318}": "Network Adapter",
        "{4d36e968-e325-11ce-bfc1-08002be10318}": "Display Adapter",
        "{4d36e97b-e325-11ce-bfc1-08002be10318}": "Storage Controller",
    }

    if dry_run:
        logger.info("[DRY RUN] Would steer device interrupts to performance cores (mask=0x%X)", P_MASK)
        return True, f"[DRY RUN] Would steer interrupts to performance cores (mask=0x{P_MASK:X}, {len(p_list)} LPs)."

    applied = 0
    errors  = 0
    try:
        for class_guid, class_name in TARGET_CLASSES.items():
            try:
                key_path = f"SYSTEM\\CurrentControlSet\\Control\\Class\\{class_guid}"
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path,
                                    0, winreg.KEY_READ) as ck:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(ck, i)
                            if not sub.isdigit():
                                i += 1
                                continue
                            sub_path = f"HKLM\\SYSTEM\\CurrentControlSet\\Control\\Class\\{class_guid}\\{sub}"
                            _reg_set(sub_path, "InterruptPolicyMask",
                                     P_MASK, winreg.REG_QWORD if hasattr(winreg, "REG_QWORD")
                                     else winreg.REG_DWORD, False)
                            applied += 1
                            i += 1
                        except OSError:
                            break
            except (FileNotFoundError, OSError):
                pass
        msg = f"Interrupt steering applied to {applied} device(s). Reboot recommended for full effect."
        logger.info("Interrupt steering: %d device(s) updated", applied)
        return True, msg
    except Exception as e:
        logger.warning("Interrupt steering error: %s", e)
        return False, str(e)


def reset_interrupt_steering(dry_run: bool = False) -> tuple:
    """Remove interrupt affinity policy overrides (restore Windows auto-routing)."""
    TARGET_CLASSES = {
        "{4d36e972-e325-11ce-bfc1-08002be10318}": "Network Adapter",
        "{4d36e968-e325-11ce-bfc1-08002be10318}": "Display Adapter",
        "{4d36e97b-e325-11ce-bfc1-08002be10318}": "Storage Controller",
    }
    if dry_run:
        return True, "[DRY RUN] Would reset interrupt steering."
    removed = 0
    for class_guid in TARGET_CLASSES:
        try:
            key_path = f"SYSTEM\\CurrentControlSet\\Control\\Class\\{class_guid}"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path,
                                0, winreg.KEY_READ) as ck:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(ck, i)
                        if sub.isdigit():
                            sub_path = (f"SYSTEM\\CurrentControlSet\\Control\\"
                                        f"Class\\{class_guid}\\{sub}")
                            try:
                                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub_path,
                                                    0, winreg.KEY_SET_VALUE) as sk:
                                    try:
                                        winreg.DeleteValue(sk, "InterruptPolicyMask")
                                        removed += 1
                                    except FileNotFoundError:
                                        pass
                            except OSError:
                                pass
                        i += 1
                    except OSError:
                        break
        except (FileNotFoundError, OSError):
            pass
    return True, f"Interrupt steering cleared from {removed} device(s)."
