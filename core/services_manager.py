"""
AlienCore - services_manager.py
Reads, categorizes, and controls Windows services.
Provides curated recommendations for gaming/streaming rigs.
"""

import logging
import subprocess
import winreg
from typing import Optional

logger = logging.getLogger("aliencore.services")

# ── Safety ratings ────────────────────────────────────────────────────────────
SAFE      = "safe"       # Safe to disable on any gaming rig
CAUTION   = "caution"    # Disable with caution — read description first
LEAVE     = "leave"      # Leave alone — needed for system stability

# ── Startup types ─────────────────────────────────────────────────────────────
AUTO     = "Automatic"
AUTO_DEL = "Automatic (Delayed)"
MANUAL   = "Manual"
DISABLED = "Disabled"

# ─────────────────────────────────────────────────────────────────────────────
# Curated service list
# Format: (service_name, friendly_name, description, recommended, safety)
# ─────────────────────────────────────────────────────────────────────────────

ALIENCORE_MANAGED = [
    ("SysMain",
     "SysMain (Superfetch)",
     "Preloads frequently used apps into RAM. Unnecessary on NVMe systems and "
     "can cause stutters during gaming.",
     DISABLED, SAFE),

    ("DiagTrack",
     "Connected User Experiences & Telemetry",
     "Sends usage and diagnostic data to Microsoft. Periodically causes CPU "
     "and disk spikes.",
     DISABLED, SAFE),

    ("DoSvc",
     "Delivery Optimization",
     "Uses peer-to-peer bandwidth to distribute Windows Updates to other PCs on "
     "your network and the internet. Causes background disk I/O and upload traffic "
     "with no benefit on a direct connection.",
     DISABLED, SAFE),

    ("WSearch",
     "Windows Search",
     "Indexes files for Windows Search. Not needed if you use Everything search.",
     DISABLED, SAFE),
]

RECOMMENDED_SERVICES = [
    # ── Safe to disable ──
    ("Fax",
     "Fax",
     "Allows sending and receiving faxes. Almost nobody uses this.",
     DISABLED, SAFE),

    ("PrintSpooler",
     "Print Spooler",
     "Required for printing. Disable if you never print from this machine. "
     "Re-enable if you need to print.",
     MANUAL, SAFE),

    ("RemoteRegistry",
     "Remote Registry",
     "Allows remote users to modify registry settings on this computer. "
     "A security risk — should be disabled on personal machines.",
     DISABLED, SAFE),

    ("WerSvc",
     "Windows Error Reporting",
     "Sends error reports to Microsoft when apps crash. Provides no benefit "
     "to the user and uses background resources.",
     DISABLED, SAFE),

    ("wisvc",
     "Windows Insider Service",
     "Required only if enrolled in the Windows Insider Program. "
     "Safe to disable on stable release Windows.",
     DISABLED, SAFE),

    ("MapsBroker",
     "Downloaded Maps Manager",
     "Manages downloaded maps for offline use. Not needed on a gaming laptop.",
     DISABLED, SAFE),

    ("PhoneSvc",
     "Phone Service",
     "Manages telephony state on the device. Not needed on a laptop "
     "used for gaming/streaming.",
     DISABLED, SAFE),

    ("RetailDemo",
     "Retail Demo Service",
     "Used for retail store demo mode. Should never be running on a personal machine.",
     DISABLED, SAFE),

    ("XblAuthManager",
     "Xbox Live Auth Manager",
     "Provides authentication services for Xbox Live. Disable if you "
     "don't use Xbox Game Pass or Xbox Live features.",
     MANUAL, SAFE),

    ("XblGameSave",
     "Xbox Live Game Save",
     "Syncs Xbox Live game saves. Disable if you don't use Xbox Live.",
     MANUAL, SAFE),

    ("XboxNetApiSvc",
     "Xbox Live Networking Service",
     "Supports Xbox Live networking. Disable if you don't use Xbox Live.",
     MANUAL, SAFE),

    ("XboxGipSvc",
     "Xbox Accessory Management Service",
     "Manages Xbox accessories like controllers. Keep Manual if you use "
     "an Xbox controller, disable if not.",
     MANUAL, SAFE),

    ("lfsvc",
     "Geolocation Service",
     "Tracks device location. Disable for privacy and to stop background "
     "location polling.",
     DISABLED, SAFE),

    ("WMPNetworkSvc",
     "Windows Media Player Network Sharing",
     "Shares Windows Media Player libraries over the network. "
     "Not needed on a gaming machine.",
     DISABLED, SAFE),

    ("icssvc",
     "Windows Mobile Hotspot Service",
     "Provides mobile hotspot functionality. Disable if you never use "
     "your laptop as a hotspot.",
     MANUAL, SAFE),

    ("SharedAccess",
     "Internet Connection Sharing",
     "Provides NAT/ICS for sharing internet connections. Disable if you "
     "don't share your internet connection.",
     DISABLED, SAFE),

    # ── Disable with caution ──
    ("WbioSrvc",
     "Windows Biometric Service",
     "Supports fingerprint readers and Windows Hello facial recognition. "
     "Disable only if you don't use biometric login.",
     MANUAL, CAUTION),

    ("TabletInputService",
     "Touch Keyboard and Handwriting",
     "Enables touch keyboard and handwriting panel. Keep if you use "
     "touchscreen or stylus, disable if not.",
     MANUAL, CAUTION),

    ("Themes",
     "Themes",
     "Provides Windows visual theme management. Disabling gives a "
     "very basic Windows 2000-style appearance but saves a small amount of RAM.",
     AUTO, CAUTION),

    ("wuauserv",
     "Windows Update",
     "Manages Windows updates. Setting to Manual prevents background update "
     "downloads but you can still check for updates manually. "
     "NOT recommended to disable entirely.",
     MANUAL, CAUTION),

    ("BITS",
     "Background Intelligent Transfer Service",
     "Downloads updates and other files in the background. Used by Windows "
     "Update — set to Manual rather than Disabled.",
     MANUAL, CAUTION),

    # ── Leave alone ──
    ("AudioSrv",
     "Windows Audio",
     "Manages audio for Windows programs. Never disable this.",
     AUTO, LEAVE),

    ("Winmgmt",
     "Windows Management Instrumentation",
     "Core Windows system service. AlienCore depends on this. Never disable.",
     AUTO, LEAVE),

    ("RpcSs",
     "Remote Procedure Call (RPC)",
     "Core Windows communication service. Disabling will break Windows entirely.",
     AUTO, LEAVE),

    ("EventLog",
     "Windows Event Log",
     "Records system, security, and application events. AlienCore's log "
     "depends on this. Leave enabled.",
     AUTO, LEAVE),
]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_service_state(service_name: str) -> dict:
    """
    Query current state and startup type of a Windows service.
    Returns dict with keys: state, startup_type, exists
    """
    try:
        result = subprocess.run(
            ["sc", "query", service_name],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        state = "Unknown"
        if "RUNNING" in result.stdout:
            state = "Running"
        elif "STOPPED" in result.stdout:
            state = "Stopped"
        elif "PAUSED" in result.stdout:
            state = "Paused"

        # Get startup type from registry
        startup = _get_startup_type(service_name)

        return {
            "exists":       result.returncode == 0,
            "state":        state,
            "startup_type": startup,
        }
    except Exception as e:
        logger.debug("Service query error (%s): %s", service_name, e)
        return {"exists": False, "state": "Unknown", "startup_type": "Unknown"}


def _is_curated(service_name: str) -> bool:
    """Return True if service_name is on AlienCore's curated list — the only
    services any caller is allowed to mutate.  Belt-and-braces against
    untrusted callers (future AI tool, scripted automation) reaching this
    function with an arbitrary name like Winmgmt or RpcSs."""
    if not isinstance(service_name, str) or not service_name:
        return False
    for entry in ALIENCORE_MANAGED + RECOMMENDED_SERVICES:
        if entry and entry[0] == service_name:
            return True
    return False


def set_startup_type(service_name: str, startup_type: str,
                     cascade_state: bool = True) -> tuple[bool, str]:
    """
    Set service startup type.
    startup_type:  'Automatic' | 'Automatic (Delayed)' | 'Manual' | 'Disabled'
    cascade_state: if True, also stop (on Disabled) / start (on Automatic).
                   GUI pending-changes flow passes False and controls state
                   via explicit Start/Stop buttons.
    Returns (ok, message).
    """
    if not _is_curated(service_name):
        return False, f"Refusing to modify uncurated service '{service_name}'."
    type_map = {
        AUTO:     "auto",
        AUTO_DEL: "delayed-auto",
        MANUAL:   "demand",
        DISABLED: "disabled",
    }
    sc_type = type_map.get(startup_type, "demand")
    try:
        # sc.exe requires "start=" and the value as two separate argv tokens —
        # passing them as one "start= auto" string causes Windows to quote the
        # whole thing, which sc parses as a single field and rejects with
        # "Invalid start= field". Keep each as its own list element.
        result = subprocess.run(
            ["sc", "config", service_name, "start=", sc_type],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        if result.returncode == 0:
            logger.info("Service %s startup set to %s", service_name, startup_type)
            if cascade_state:
                if startup_type == DISABLED:
                    subprocess.run(["sc", "stop", service_name],
                                   capture_output=True, timeout=10,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                elif startup_type == AUTO:
                    subprocess.run(["sc", "start", service_name],
                                   capture_output=True, timeout=10,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
            return True, "Updated."
        msg = (result.stderr or result.stdout or "Unknown error").strip()
        logger.warning("Failed to set %s startup: %s", service_name, msg)
        return False, msg
    except Exception as e:
        logger.error("set_startup_type error (%s): %s", service_name, e)
        return False, str(e)


def start_service(service_name: str) -> tuple[bool, str]:
    """Start a service by name. Returns (ok, message)."""
    if not _is_curated(service_name):
        return False, f"Refusing to start uncurated service '{service_name}'."
    try:
        result = subprocess.run(
            ["sc", "start", service_name],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        if result.returncode == 0:
            logger.info("Started service %s", service_name)
            return True, "Started."
        msg = (result.stderr or result.stdout or "Unknown error").strip()
        logger.warning("Failed to start %s: %s", service_name, msg)
        return False, msg
    except Exception as e:
        logger.error("start_service error (%s): %s", service_name, e)
        return False, str(e)


def stop_service(service_name: str) -> tuple[bool, str]:
    """Stop a service by name. Returns (ok, message)."""
    if not _is_curated(service_name):
        return False, f"Refusing to stop uncurated service '{service_name}'."
    try:
        result = subprocess.run(
            ["sc", "stop", service_name],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        if result.returncode == 0:
            logger.info("Stopped service %s", service_name)
            return True, "Stopped."
        msg = (result.stderr or result.stdout or "Unknown error").strip()
        logger.warning("Failed to stop %s: %s", service_name, msg)
        return False, msg
    except Exception as e:
        logger.error("stop_service error (%s): %s", service_name, e)
        return False, str(e)


def get_all_curated_states() -> list:
    """
    Return all curated services with their current live state.
    Each entry: original tuple + {"state": ..., "startup_type": ..., "exists": ...}
    """
    results = []
    all_services = ALIENCORE_MANAGED + RECOMMENDED_SERVICES
    for svc in all_services:
        name = svc[0]
        live = get_service_state(name)
        results.append({
            "name":         svc[0],
            "friendly":     svc[1],
            "description":  svc[2],
            "recommended":  svc[3],
            "safety":       svc[4],
            "state":        live["state"],
            "startup_type": live["startup_type"],
            "exists":       live["exists"],
            "managed_by_aliencore": svc in ALIENCORE_MANAGED,
        })
    return results


def apply_all_recommended(dry_run: bool = False) -> int:
    """
    Apply recommended startup types to all SAFE services.
    Skips CAUTION and LEAVE services.
    Returns number of services changed.
    """
    changed = 0
    for svc in ALIENCORE_MANAGED + RECOMMENDED_SERVICES:
        name, _, _, recommended, safety = svc
        if safety != SAFE:
            continue
        live = get_service_state(name)
        if not live["exists"]:
            continue
        if live["startup_type"] == recommended:
            continue
        if dry_run:
            logger.info("[DRY RUN] Would set %s -> %s", name, recommended)
        else:
            ok, _msg = set_startup_type(name, recommended)
            if ok:
                changed += 1
    return changed


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_startup_type(service_name: str) -> str:
    """Read startup type from registry — more reliable than sc qc output parsing."""
    start_map = {0: AUTO, 1: "System", 2: AUTO, 3: MANUAL, 4: DISABLED}
    try:
        key_path = f"SYSTEM\\CurrentControlSet\\Services\\{service_name}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            val, _ = winreg.QueryValueEx(key, "Start")
            # Check for delayed auto
            try:
                delayed, _ = winreg.QueryValueEx(key, "DelayedAutostart")
                if delayed == 1 and val == 2:
                    return AUTO_DEL
            except Exception:
                pass
            return start_map.get(val, "Unknown")
    except Exception:
        return "Unknown"
