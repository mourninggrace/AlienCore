"""
AlienCore - hardware.py
Detects and caches the machine's hardware profile on first run (and on request).
All other modules pull from this profile rather than re-querying hardware each time.
Portable: works on any Windows machine — Alienware-specific features flagged separately.
"""

import json
import logging
import os
import platform
import re
import subprocess
import wmi
import psutil
from core.constants import HARDWARE_CACHE

logger = logging.getLogger("aliencore.hardware")


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def build_profile(force_refresh: bool = False) -> dict:
    """
    Build (or load cached) hardware profile.
    Returns a dict describing the full system.
    """
    if not force_refresh and os.path.exists(HARDWARE_CACHE):
        try:
            with open(HARDWARE_CACHE, "r", encoding="utf-8") as f:
                profile = json.load(f)
            logger.info("Hardware profile loaded from cache.")
            return profile
        except Exception as e:
            logger.warning("Cache read failed (%s) — rebuilding.", e)

    logger.info("Building hardware profile — this takes a few seconds...")
    profile = {
        "os":       _get_os_info(),
        "cpu":      _get_cpu_info(),
        "gpu":      _get_gpu_info(),
        "ram":      _get_ram_info(),
        "drives":   _get_drive_info(),
        "network":  _get_network_info(),
        "platform": _get_platform_flags(),
    }

    _save_cache(profile)
    logger.info("Hardware profile built and cached.")
    return profile


def get_cached() -> dict:
    """Return cached profile or build if missing."""
    return build_profile(force_refresh=False)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _wmi_connect():
    try:
        return wmi.WMI()
    except Exception as e:
        logger.error("WMI connection failed: %s", e)
        return None


def _get_os_info() -> dict:
    return {
        "name":    platform.system(),
        "version": platform.version(),
        "release": platform.release(),
        "machine": platform.machine(),
    }


def _get_cpu_info() -> dict:
    info = {
        "name":           "Unknown",
        "physical_cores": psutil.cpu_count(logical=False) or 1,
        "logical_cores":  psutil.cpu_count(logical=True) or 1,
        "base_freq_mhz":  0,
        "max_freq_mhz":   0,
        "tdp_watts":      None,    # WMI rarely exposes this — populated if available
        "undervolt_capable": False, # locked on most modern laptops
        "is_intel":       False,
        "is_amd":         False,
        "family":         "Unknown",
    }
    try:
        c = _wmi_connect()
        if c:
            for cpu in c.Win32_Processor():
                info["name"]          = cpu.Name.strip()
                info["max_freq_mhz"]  = int(cpu.MaxClockSpeed or 0)
                info["base_freq_mhz"] = int(cpu.CurrentClockSpeed or 0)
                break
        freq = psutil.cpu_freq()
        if freq:
            info["max_freq_mhz"] = int(freq.max) or info["max_freq_mhz"]

        name_lower = info["name"].lower()
        info["is_intel"] = "intel" in name_lower
        info["is_amd"]   = "amd"   in name_lower

        # Rough family detection
        for family in ["i9", "i7", "i5", "i3", "ryzen 9", "ryzen 7", "ryzen 5"]:
            if family in name_lower:
                info["family"] = family
                break
    except Exception as e:
        logger.warning("CPU info partial: %s", e)

    logger.info("CPU: %s (%dP / %dL cores, max %d MHz)",
                info["name"], info["physical_cores"],
                info["logical_cores"], info["max_freq_mhz"])
    return info


def _get_gpu_info() -> list:
    """Returns a list — systems can have integrated + discrete GPUs."""
    gpus = []
    try:
        # nvidia-smi gives us the most reliable NVIDIA data
        nvidia = _query_nvidia_smi()
        if nvidia:
            gpus.extend(nvidia)

        # WMI as fallback / for integrated / AMD
        c = _wmi_connect()
        if c:
            for v in c.Win32_VideoController():
                name = (v.Name or "").strip()
                if not name:
                    continue
                # Skip if already captured via nvidia-smi
                if any(g["name"].lower() in name.lower() for g in gpus):
                    continue
                gpus.append({
                    "name":          name,
                    "vram_mb":       int((v.AdapterRAM or 0) / 1024 / 1024),
                    "driver":        v.DriverVersion or "Unknown",
                    "is_nvidia":     "nvidia" in name.lower(),
                    "is_amd":        "amd"    in name.lower() or "radeon" in name.lower(),
                    "is_integrated": any(x in name.lower() for x in ["intel", "uhd", "iris", "vega"]),
                    "nvidia_smi":    False,
                    "tdp_watts":     None,
                    "power_limit_w": None,
                })
    except Exception as e:
        logger.warning("GPU info partial: %s", e)

    for g in gpus:
        logger.info("GPU: %s (VRAM: %d MB, NVIDIA: %s)",
                    g["name"], g.get("vram_mb", 0), g.get("is_nvidia"))
    return gpus


def _query_nvidia_smi() -> list:
    """Query nvidia-smi for detailed NVIDIA GPU info."""
    try:
        fields = "name,memory.total,driver_version,power.limit,power.max_limit"
        result = subprocess.run(
            ["nvidia-smi", f"--query-gpu={fields}",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return []
        gpus = []
        for line in result.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 5:
                continue
            gpus.append({
                "name":          parts[0],
                "vram_mb":       int(float(parts[1])) if parts[1] != "[N/A]" else 0,
                "driver":        parts[2],
                "power_limit_w": float(parts[3]) if parts[3] != "[N/A]" else None,
                "tdp_watts":     float(parts[4]) if parts[4] != "[N/A]" else None,
                "is_nvidia":     True,
                "is_amd":        False,
                "is_integrated": False,
                "nvidia_smi":    True,
            })
        return gpus
    except FileNotFoundError:
        logger.warning("nvidia-smi not found — NVIDIA detail unavailable.")
        return []
    except Exception as e:
        logger.warning("nvidia-smi query failed: %s", e)
        return []


def _get_ram_info() -> dict:
    info = {
        "total_gb":  0,
        "slots":     [],
        "xmp_capable": False,   # can't detect via WMI reliably; noted for user
    }
    try:
        vm = psutil.virtual_memory()
        info["total_gb"] = round(vm.total / 1024**3, 1)

        c = _wmi_connect()
        if c:
            for stick in c.Win32_PhysicalMemory():
                slot_info = {
                    "capacity_gb": round(int(stick.Capacity or 0) / 1024**3, 1),
                    "speed_mhz":   int(stick.Speed or 0),
                    "manufacturer": (stick.Manufacturer or "Unknown").strip(),
                    "slot":        stick.DeviceLocator or "?",
                }
                info["slots"].append(slot_info)
    except Exception as e:
        logger.warning("RAM info partial: %s", e)

    logger.info("RAM: %.1f GB total, %d stick(s)", info["total_gb"], len(info["slots"]))
    return info


def _get_drive_info() -> list:
    drives = []
    try:
        c = _wmi_connect()
        if c:
            for disk in c.Win32_DiskDrive():
                name   = (disk.Model or "Unknown").strip()
                size   = int(disk.Size or 0)
                iface  = (disk.InterfaceType or "Unknown").strip()
                pnpid  = (disk.PNPDeviceID or "").strip().lower()
                is_ramdisk = _detect_ramdisk(name, pnpid)
                is_nvme    = not is_ramdisk and (
                    "nvme" in name.lower() or "nvme" in iface.lower()
                )
                is_ssd     = not is_ramdisk and any(
                    x in name.lower() for x in ["ssd", "nvme", "solid"]
                )
                is_hdd     = (
                    not is_ramdisk
                    and not is_nvme
                    and iface.lower() in ["ide", "sata"]
                    and not any(x in name.lower() for x in ["ssd", "nvme"])
                )
                drives.append({
                    "name":        name,
                    "size_gb":     round(size / 1024**3, 1),
                    "interface":   iface,
                    "is_nvme":     is_nvme,
                    "is_ssd":      is_ssd,
                    "is_hdd":      is_hdd,
                    "is_ramdisk":  is_ramdisk,
                })
    except Exception as e:
        logger.warning("Drive info partial: %s", e)

    for d in drives:
        dtype = "RAMDisk" if d["is_ramdisk"] else "NVMe" if d["is_nvme"] else "SSD" if d["is_ssd"] else "HDD"
        logger.info("Drive: %s (%.0f GB, %s, type=%s)",
                    d["name"], d["size_gb"], d["interface"], dtype)
    return drives


def _detect_ramdisk(name: str, pnpid: str) -> bool:
    """
    Detect RAM disk virtual drives by checking the model name and PNP device ID.
    RAM disk drivers from common software all leave identifiable strings in one
    or both of these fields.
    """
    name_lower = name.lower()

    # Keywords that appear in model/name for common RAM disk software
    name_keywords = [
        "ramdisk", "ram disk",
        "starwind",         # StarWind RAM Disk
        "imdisk",           # ImDisk Toolkit
        "osfmount",         # OSFMount
        "softperfect",      # SoftPerfect RAM Disk
        "primo",            # Primo Ramdisk
        "dataram",          # Dataram RAMDisk
        "amd ramdisk",      # AMD RAMDisk (for Ryzen platforms)
        "virtual disk",     # generic virtual disk (ImDisk, others)
        "filedisk",         # ImDisk file-backed disk
    ]
    if any(kw in name_lower for kw in name_keywords):
        return True

    # PNP device IDs registered by RAM disk drivers
    pnp_keywords = [
        "starwind", "imdisk", "ramdisk", "osfmount", "softperfect",
        "primoramdisk", "dataram", "filedisk",
    ]
    if any(kw in pnpid for kw in pnp_keywords):
        return True

    return False


def _get_network_info() -> list:
    adapters = []
    try:
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
        for name, stat in stats.items():
            adapters.append({
                "name":    name,
                "up":      stat.isup,
                "speed":   stat.speed,
                "addresses": [a.address for a in addrs.get(name, [])],
            })
    except Exception as e:
        logger.warning("Network info partial: %s", e)
    return adapters


def _get_platform_flags() -> dict:
    """Detect special platform capabilities."""
    flags = {
        "is_laptop":        False,
        "is_alienware":     False,
        "has_nvidia_smi":   False,
        "has_awcc":         False,     # Alienware Command Center
        "has_throttlestop": False,
    }
    try:
        c = _wmi_connect()
        if c:
            for cs in c.Win32_ComputerSystem():
                model = (cs.Model or "").lower()
                flags["is_laptop"]    = any(x in model for x in ["laptop", "notebook", "mobile"])
                flags["is_alienware"] = "alienware" in model or "alienware" in (cs.Manufacturer or "").lower()
                break
            # Also check chassis type
            for chassis in c.Win32_SystemEnclosure():
                chassis_type = chassis.ChassisTypes
                if chassis_type:
                    # 8=Portable, 9=Laptop, 10=Notebook, 14=Sub-Notebook
                    flags["is_laptop"] = any(t in [8, 9, 10, 14] for t in chassis_type)
                break

        # Check if nvidia-smi is accessible
        r = subprocess.run(["nvidia-smi", "-L"], capture_output=True, timeout=5)
        flags["has_nvidia_smi"] = r.returncode == 0

        # Check for AWCC — test both install path and live WMI accessibility
        awcc_paths = [
            r"C:\Program Files\Alienware\AWCC",
            r"C:\Program Files (x86)\Alienware\AWCC",
        ]
        awcc_installed = any(os.path.exists(p) for p in awcc_paths)
        awcc_wmi_live  = False
        if awcc_installed:
            try:
                from core import awcc as _awcc_mod
                awcc_wmi_live = _awcc_mod.is_available()
            except Exception:
                pass
        flags["has_awcc"]     = awcc_installed
        flags["has_awcc_wmi"] = awcc_wmi_live

        # Check for ThrottleStop
        ts_paths = [
            r"C:\Program Files\ThrottleStop",
            os.path.join(os.environ.get("USERPROFILE",""), "Desktop", "ThrottleStop"),
        ]
        flags["has_throttlestop"] = any(os.path.exists(p) for p in ts_paths)

    except Exception as e:
        logger.warning("Platform flags partial: %s", e)

    logger.info("Platform: laptop=%s, Alienware=%s, nvidia-smi=%s",
                flags["is_laptop"], flags["is_alienware"], flags["has_nvidia_smi"])
    return flags


def _save_cache(profile: dict):
    try:
        os.makedirs(os.path.dirname(HARDWARE_CACHE), exist_ok=True)
        with open(HARDWARE_CACHE, "w", encoding="utf-8") as f:
            json.dump(profile, f, indent=2)
    except Exception as e:
        logger.error("Failed to save hardware cache: %s", e)
