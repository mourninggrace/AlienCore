"""
AlienCore - ai_tools.py
Tool registry that lets the in-app AI chat act on the system — switch
profiles, toggle Turbo Cool, change power plan, open settings, etc.

Risk tiers decide the UX:
  0 (read)  — executed automatically, no UI prompt
  1 (soft)  — needs a user confirm click; easily reversible state change
  2 (risky) — would pop a per-action dialog showing exactly what changes;
              reserved for voltage / clock / registry writes (not used yet)

Adding a new tool:
  1. Write a handler returning (ok: bool, message: str, data: dict | None)
  2. Register it in TOOLS with description + JSON schema + risk tier
"""
# copykitten

import json
import logging
import os
import subprocess
import sys
from typing import Callable

logger = logging.getLogger("aliencore.ai_tools")

RISK_READ = 0
RISK_SOFT = 1
RISK_HIGH = 2


# ─────────────────────────────────────────────────────────────────────────────
# Handlers
# ─────────────────────────────────────────────────────────────────────────────

def _h_get_system_snapshot(args: dict):
    # Lazy import avoids circular dependency with ai_manager at module load.
    from core import ai_manager
    snap = ai_manager.get_system_context()
    return True, "System snapshot captured.", {"snapshot": snap}


def _h_get_profile_status(args: dict):
    from core import profiles, turbo_cool, awcc
    data = {
        "current_profile":   profiles.get_current(),
        "manual_override":   profiles.is_manual_override(),
        "turbo_cool_active": turbo_cool.is_active(),
    }
    try:
        data["gmode_active"] = bool(awcc.get_gmode())
    except Exception:
        data["gmode_active"] = None
    return True, "Profile status retrieved.", data


def _h_switch_profile(args: dict):
    from core import profiles
    profile = args.get("profile")
    valid   = {"auto", "idle", "gaming", "streaming"}
    if profile not in valid:
        return (False,
                f"Invalid profile '{profile}'. "
                f"Must be one of: {', '.join(sorted(valid))}.", None)
    if profile == "auto":
        profiles.set_manual_override(None)
        return True, "Profile switched to automatic detection.", {"profile": "auto"}
    profiles.set_manual_override(profile)
    return True, f"Profile locked to '{profile}'.", {"profile": profile}


def _h_set_turbo_cool(args: dict):
    from core import turbo_cool
    enabled = bool(args.get("enabled"))
    if enabled:
        turbo_cool.activate()
    else:
        turbo_cool.deactivate("ai_chat")
    return True, f"Turbo Cool {'enabled' if enabled else 'disabled'}.", {"enabled": enabled}


def _h_set_gmode(args: dict):
    from core import awcc
    enabled = bool(args.get("enabled"))
    try:
        ok = awcc.set_gmode(enabled)
    except Exception as e:
        return False, f"AWCC error: {e}", None
    if not ok:
        return False, "AWCC unavailable — cannot set G-Mode.", None
    return True, f"G-Mode {'enabled' if enabled else 'disabled'}.", {"enabled": enabled}


# Standard Windows power plan GUIDs (present by default on all Win10/Win11 SKUs
# except Ultimate, which is only on Pro/Workstation — if missing, switching to
# it will fail with a clear message rather than silently fall through).
_POWER_PLANS = {
    "balanced":    "381b4222-f694-41f0-9685-ff5bb260df2e",
    "high_perf":   "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
    "power_saver": "a1841308-3541-4fab-bc81-f71556f20b4a",
    "ultimate":    "e9a42b02-d5df-448d-aa00-03f14749eb61",
}


def _h_set_power_plan(args: dict):
    plan = args.get("plan")
    if plan not in _POWER_PLANS:
        return (False,
                f"Invalid plan '{plan}'. Must be one of: "
                f"{', '.join(_POWER_PLANS)}.", None)
    guid = _POWER_PLANS[plan]
    try:
        r = subprocess.run(
            ["powercfg", "/setactive", guid],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=10,
        )
    except FileNotFoundError:
        return False, "powercfg.exe not found on PATH.", None
    except Exception as e:
        return False, f"powercfg error: {e}", None
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip() or f"exit {r.returncode}"
        return False, f"powercfg failed: {err}", None
    return True, f"Power plan set to '{plan}'.", {"plan": plan, "guid": guid}


def _h_cpu_voltage_status(args: dict):
    """Report Intel MSR 0x150 voltage-control capability.

    On systems where it doesn't apply (Dell/Alienware Plundervolt lock,
    non-Intel CPU), supported=False with a reason string.
    """
    from core import cpu_voltage
    return True, cpu_voltage.status_text(), cpu_voltage.get_status()


def _h_set_cpu_voltage_offset(args: dict):
    from core import cpu_voltage
    plane  = args.get("plane")
    offset = args.get("offset_mv")
    ok, msg = cpu_voltage.set_offset(plane, offset)
    return ok, msg, {"plane": plane, "offset_mv": offset}


# ── GPU tuning (NVIDIA only in v1) ──────────────────────────────────────────

def _h_gpu_tuning_status(args: dict):
    from core import gpu_tuning
    status = gpu_tuning.get_status()
    return True, gpu_tuning.status_text(), status


def _h_set_gpu_core_offset(args: dict):
    from core import gpu_tuning
    vendor = gpu_tuning._primary_gpu_info()["vendor"]
    offset = args.get("offset_mhz")
    if vendor == "nvidia":
        ok, msg = gpu_tuning.set_core_offset(offset)
    else:
        ok, msg = False, ("Runtime GPU tuning is NVIDIA-only in this version. "
                          f"Primary GPU vendor: '{vendor}'.")
    return ok, msg, {"vendor": vendor, "offset_mhz": offset}


def _h_set_gpu_mem_offset(args: dict):
    from core import gpu_tuning
    vendor = gpu_tuning._primary_gpu_info()["vendor"]
    offset = args.get("offset_mhz")
    if vendor == "nvidia":
        ok, msg = gpu_tuning.set_mem_offset(offset)
    else:
        ok, msg = False, ("Runtime GPU tuning is NVIDIA-only in this version. "
                          f"Primary GPU vendor: '{vendor}'.")
    return ok, msg, {"vendor": vendor, "offset_mhz": offset}


def _h_set_gpu_power_limit(args: dict):
    from core import gpu_tuning
    vendor = gpu_tuning._primary_gpu_info()["vendor"]
    if vendor == "nvidia":
        watts = args.get("watts")
        ok, msg = gpu_tuning.set_power_limit(watts)
        data = {"vendor": vendor, "watts": watts}
    else:
        ok, msg = False, ("Runtime GPU tuning is NVIDIA-only in this version. "
                          f"Primary GPU vendor: '{vendor}'.")
        data = {"vendor": vendor}
    return ok, msg, data


def _h_set_gpu_fan_duty(args: dict):
    from core import gpu_tuning
    vendor  = gpu_tuning._primary_gpu_info()["vendor"]
    percent = args.get("percent")
    if vendor == "nvidia":
        ok, msg = gpu_tuning.set_fan_duty(percent, args.get("fan_index", 0))
    else:
        ok, msg = False, ("Runtime GPU tuning is NVIDIA-only in this version. "
                          f"Primary GPU vendor: '{vendor}'.")
    return ok, msg, {"vendor": vendor, "percent": percent}


# ── Memory tuning ───────────────────────────────────────────────────────────

def _h_memory_status(args: dict):
    from core import memory_tuning
    return True, memory_tuning.status_text(), memory_tuning.get_status()


def _h_open_settings(args: dict):
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        subprocess.Popen(
            [sys.executable, os.path.join(base, "aliencore.py"), "--settings"],
            cwd=base, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as e:
        return False, f"Failed to open settings: {e}", None
    return True, "Settings window opened.", None


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

TOOLS = {
    "get_system_snapshot": {
        "description": "Return a JSON snapshot of live sensor readings — CPU "
                       "and GPU temps and loads, fan RPMs, NVMe and DIMM "
                       "temps, power draw, active profile, thresholds, and "
                       "machine identity. Call this before diagnosing issues.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "risk":    RISK_READ,
        "handler": _h_get_system_snapshot,
    },
    "get_profile_status": {
        "description": "Return the currently active profile, whether it's a "
                       "manual override, whether Turbo Cool is active, and "
                       "whether AWCC G-Mode is active.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "risk":    RISK_READ,
        "handler": _h_get_profile_status,
    },
    "open_settings": {
        "description": "Open the AlienCore settings window so the user can "
                       "inspect or edit configuration directly. Use when the "
                       "user asks to change something that isn't covered by "
                       "another tool.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "risk":    RISK_READ,
        "handler": _h_open_settings,
    },
    "switch_profile": {
        "description": "Change the active AlienCore profile. 'auto' returns "
                       "to automatic detection; 'idle', 'gaming', or "
                       "'streaming' lock to that profile as a manual override.",
        "input_schema": {
            "type": "object",
            "properties": {
                "profile": {
                    "type":        "string",
                    "enum":        ["auto", "idle", "gaming", "streaming"],
                    "description": "Profile to activate.",
                },
            },
            "required": ["profile"],
        },
        "risk":    RISK_SOFT,
        "handler": _h_switch_profile,
    },
    "set_turbo_cool": {
        "description": "Enable or disable Turbo Cool, which ramps fans to "
                       "maximum to drop component temperatures quickly.",
        "input_schema": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean",
                            "description": "true to enable, false to disable."},
            },
            "required": ["enabled"],
        },
        "risk":    RISK_SOFT,
        "handler": _h_set_turbo_cool,
    },
    "set_gmode": {
        "description": "Enable or disable AWCC G-Mode, Alienware's built-in "
                       "high-performance fan / power mode. Only works on "
                       "Alienware hardware with AWCC installed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
            },
            "required": ["enabled"],
        },
        "risk":    RISK_SOFT,
        "handler": _h_set_gmode,
    },
    "set_power_plan": {
        "description": "Switch the active Windows power plan. "
                       "'ultimate' is only available on Pro / Workstation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "plan": {
                    "type": "string",
                    "enum": ["balanced", "high_perf", "power_saver", "ultimate"],
                    "description": "Which Windows power plan to activate.",
                },
            },
            "required": ["plan"],
        },
        "risk":    RISK_SOFT,
        "handler": _h_set_power_plan,
    },
    "cpu_voltage_status": {
        "description": "Report whether dynamic CPU voltage offsets are "
                       "available on this machine. Returns support flag, "
                       "reason string (if unsupported — e.g. Dell/Alienware "
                       "Plundervolt lock, non-Intel CPU), list of voltage "
                       "planes, and the allowed offset range in mV. Intel-"
                       "only in this version. Call this before attempting "
                       "to set any voltage offset.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "risk":    RISK_READ,
        "handler": _h_cpu_voltage_status,
    },
    "set_cpu_voltage_offset": {
        "description": "Apply a signed voltage offset (in millivolts) to a "
                       "CPU voltage plane via Intel MSR 0x150 (OC Mailbox). "
                       "Planes: core, igpu, cache, system_agent, analog_io. "
                       "Range: -150 to +100 mV. Undervolting reduces heat "
                       "and power but too aggressive an offset will crash "
                       "the machine. Unavailable on Dell / Alienware "
                       "(Plundervolt mitigation) and non-Intel CPUs — "
                       "always call cpu_voltage_status first.",
        "warning":     ("Writing a CPU voltage offset directly modifies "
                        "hardware operating voltage. Too aggressive an "
                        "undervolt can cause instant hard-freeze, blue "
                        "screen, corrupted in-flight disk writes, file "
                        "system damage, or failure to boot. Too aggressive "
                        "an overvolt can overheat the CPU and, in rare "
                        "cases, permanently damage the silicon. Offsets "
                        "revert on reboot, but damage caused while the "
                        "offset is active does not."),
        "input_schema": {
            "type": "object",
            "properties": {
                "plane": {
                    "type": "string",
                    "enum": ["core", "igpu", "cache",
                            "system_agent", "analog_io"],
                    "description": "Voltage plane to adjust.",
                },
                "offset_mv": {
                    "type":        "integer",
                    "minimum":     -150,
                    "maximum":     100,
                    "description": "Signed offset in millivolts "
                                   "(negative = undervolt, positive = "
                                   "overvolt).",
                },
            },
            "required": ["plane", "offset_mv"],
        },
        "risk":    RISK_HIGH,
        "handler": _h_set_cpu_voltage_offset,
    },

    # ── GPU tuning ──────────────────────────────────────────────────────
    "gpu_tuning_status": {
        "description": "Report whether NVIDIA GPU tuning is available via "
                       "NVML (core/mem clock offsets, power limit, fan). "
                       "Returns firmware-reported power-limit range. "
                       "NVIDIA-only in this version. Call before any "
                       "set_gpu_* tool.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "risk":    RISK_READ,
        "handler": _h_gpu_tuning_status,
    },
    "set_gpu_core_offset": {
        "description": "Apply a signed core-clock offset to the primary "
                       "NVIDIA GPU via NVML (same knob MSI Afterburner "
                       "uses). Range: -200 to +300 MHz. NVIDIA-only; "
                       "always call gpu_tuning_status first.",
        "warning":     ("Raising core-clock offset increases GPU power "
                        "and heat. Too aggressive an offset causes "
                        "driver TDR crashes, flickering artifacts, or "
                        "application hangs. On cards with weak VRM "
                        "cooling, sustained operation at elevated "
                        "clocks can overheat and damage voltage "
                        "regulators. Offsets reset on driver restart "
                        "or reboot, but damage caused while active "
                        "does not."),
        "input_schema": {
            "type": "object",
            "properties": {
                "offset_mhz": {
                    "type": "integer",
                    "minimum": -200,
                    "maximum":  300,
                    "description": "Signed core-clock offset in MHz.",
                },
            },
            "required": ["offset_mhz"],
        },
        "risk":    RISK_HIGH,
        "handler": _h_set_gpu_core_offset,
    },
    "set_gpu_mem_offset": {
        "description": "Apply a signed memory-clock offset to the primary "
                       "NVIDIA GPU via NVML. GDDR6/6X tolerates larger "
                       "offsets than core clock — range: -1000 to +2000 "
                       "MHz. NVIDIA-only; always call gpu_tuning_status "
                       "first.",
        "warning":     ("Memory overclocking past the silicon's stable "
                        "point causes image corruption (sparkles, "
                        "texture flickering), full application crashes, "
                        "or driver TDR. GDDR6X in particular runs hot — "
                        "a too-aggressive offset can push junction "
                        "temperatures above 105 C and force thermal "
                        "throttling or long-term degradation."),
        "input_schema": {
            "type": "object",
            "properties": {
                "offset_mhz": {
                    "type": "integer",
                    "minimum": -1000,
                    "maximum":  2000,
                    "description": "Signed memory-clock offset in MHz.",
                },
            },
            "required": ["offset_mhz"],
        },
        "risk":    RISK_HIGH,
        "handler": _h_set_gpu_mem_offset,
    },
    "set_gpu_power_limit": {
        "description": "Set the NVIDIA GPU power limit. Absolute wattage "
                       "clamped to the firmware min/max (call "
                       "gpu_tuning_status to get the valid range). "
                       "NVIDIA-only in this version.",
        "warning":     ("Raising the GPU power limit allows the card to "
                        "draw more sustained wattage, which increases "
                        "VRM and board temperatures. Budget and "
                        "reference cards often have marginal VRM "
                        "cooling — sustained operation above rated TGP "
                        "can permanently damage MOSFETs over weeks or "
                        "months. Lowering the power limit is safe but "
                        "caps performance."),
        "input_schema": {
            "type": "object",
            "properties": {
                "watts": {"type": "number",
                          "description": "Absolute power limit in watts."},
            },
            "required": ["watts"],
        },
        "risk":    RISK_HIGH,
        "handler": _h_set_gpu_power_limit,
    },
    "set_gpu_fan_duty": {
        "description": "Set a fixed NVIDIA GPU fan duty cycle (0-100 %). "
                       "Pass percent = -1 to hand control back to the "
                       "driver's automatic fan curve. Useful for reducing "
                       "noise at idle (lower percent) or improving "
                       "thermals under load (higher percent). "
                       "fan_index defaults to 0 (primary fan).",
        "warning":     ("Locking the fan below the card's automatic "
                        "curve can allow the GPU to overheat under "
                        "load. Always verify temperatures remain below "
                        "85 C (core) and 95 C (memory junction) under "
                        "your typical workload before leaving a manual "
                        "duty in place."),
        "input_schema": {
            "type": "object",
            "properties": {
                "percent":   {"type": "integer",
                              "minimum": -1, "maximum": 100,
                              "description": "Fan duty % (0-100), or -1 for auto."},
                "fan_index": {"type": "integer", "minimum": 0, "default": 0,
                              "description": "Which fan to control."},
            },
            "required": ["percent"],
        },
        "risk":    RISK_HIGH,
        "handler": _h_set_gpu_fan_duty,
    },

    # ── Memory tuning ───────────────────────────────────────────────────
    "memory_status": {
        "description": "Report RAM configuration — total capacity, slot "
                       "layout, DIMM frequency, inferred XMP state. "
                       "Runtime DDR frequency or primary-timing changes "
                       "are NOT supported on any platform — those must "
                       "be set in BIOS via XMP.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "risk":    RISK_READ,
        "handler": _h_memory_status,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def tool_schemas() -> list[dict]:
    """Return tool definitions in Anthropic's tool_use format."""
    return [
        {
            "name":         name,
            "description":  spec["description"],
            "input_schema": spec["input_schema"],
        }
        for name, spec in TOOLS.items()
    ]


def get_risk(name: str) -> int:
    spec = TOOLS.get(name)
    return spec["risk"] if spec else RISK_HIGH


def describe(name: str) -> dict:
    """Return human-facing metadata the UI needs to build a confirm dialog.

    Keys:
      description  — what the tool does (shown at the top of the dialog)
      warning      — extra copy describing specific damage risks (RISK_HIGH
                     tools only; empty string for lower tiers)
      risk         — RISK_READ / RISK_SOFT / RISK_HIGH
    """
    spec = TOOLS.get(name) or {}
    return {
        "description": spec.get("description", "Unknown tool."),
        "warning":     spec.get("warning", ""),
        "risk":        spec.get("risk", RISK_HIGH),
    }


def execute(name: str, args: dict,
            confirm_fn: Callable | None = None) -> dict:
    """Execute a tool by name.

    confirm_fn(tool_name, args, risk) -> bool is called for tools at
    RISK_SOFT or above. If it returns False, the tool is declined and the
    returned dict has declined=True so the caller can tell the model.

    Returns: {ok, message, data, declined}.
    """
    spec = TOOLS.get(name)
    if spec is None:
        return {"ok": False, "declined": False,
                "message": f"Unknown tool '{name}'.", "data": None}

    if spec["risk"] >= RISK_SOFT and confirm_fn is not None:
        try:
            approved = bool(confirm_fn(name, args, spec["risk"]))
        except Exception as e:
            logger.error("confirm_fn raised for %s: %s", name, e)
            return {"ok": False, "declined": True,
                    "message": "Confirmation failed.", "data": None}
        if not approved:
            logger.info("Tool %s declined by user.", name)
            return {"ok": False, "declined": True,
                    "message": "Declined by user.", "data": None}

    try:
        ok, msg, data = spec["handler"](args or {})
    except Exception as e:
        logger.error("Tool %s raised: %s", name, e)
        return {"ok": False, "declined": False,
                "message": f"Tool error: {e}", "data": None}

    logger.info("Tool %s(%s): ok=%s msg=%s", name, args, ok, msg)
    return {"ok": ok, "declined": False, "message": msg, "data": data}


def result_payload(result: dict) -> str:
    """Serialize an execute() result as JSON for the tool_result content block."""
    payload = {
        "status":  "ok" if result["ok"] else "error",
        "message": result["message"],
    }
    if result.get("data") is not None:
        payload["data"] = result["data"]
    if result.get("declined"):
        payload["declined"] = True
    return json.dumps(payload, default=str)
