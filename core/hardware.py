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
import threading
import psutil
from core.constants import HARDWARE_CACHE

logger = logging.getLogger("aliencore.hardware")

# In-process cache for the observed CPU peak clock so we only touch the
# hardware_profile.json cache file when a new high water mark is reached.
# sensors.py calls record_observed_cpu_clock() every poll; almost all calls
# are no-ops (current ≤ cached peak) and return without I/O.
_peak_lock = threading.Lock()
_peak_cached_mhz: int = 0

# ─────────────────────────────────────────────────────────────────────────────
# CPU max-boost lookup table
#
# Neither WMI's Win32_Processor.MaxClockSpeed nor psutil.cpu_freq().max return
# the true Turbo Boost ceiling on modern laptops — both consistently report the
# base clock (2.2 GHz on the i9-14900HX, even though it boosts to 5.8 GHz).
# There's no user-mode API that hands you the boost max directly without
# reading MSRs, so we ship a static lookup keyed on a substring of the CPU
# product name.  Matched by the LONGEST matching key so more specific entries
# (e.g. "14900KS") beat "14900".
#
# Values in MHz.  Intel Turbo Boost Max 3.0 / Thermal Velocity Boost ceiling
# where applicable; AMD Ryzen max boost per AMD spec.  When the user's CPU
# isn't in this table we fall back to the observed peak from LHM.
# ─────────────────────────────────────────────────────────────────────────────
CPU_BOOST_MHZ = {
    # Intel Core Ultra (Arrow Lake H / HX / mobile)
    "core ultra 9 285hx": 5500,
    "core ultra 9 275hx": 5400,
    "core ultra 7 265hx": 5300,
    "core ultra 7 255hx": 5200,
    "core ultra 9 185h":  5100,
    "core ultra 7 155h":  4800,
    "core ultra 5 135h":  4600,

    # Intel 14th gen (Raptor Lake Refresh)
    "14900ks": 6200,
    "14900kf": 6000,
    "14900k":  6000,
    "14900hx": 5800,
    "14900":   5800,
    "14700kf": 5600,
    "14700k":  5600,
    "14700hx": 5500,
    "14700":   5400,
    "14600kf": 5300,
    "14600k":  5300,
    "14500":   5000,
    "14400":   4700,

    # Intel 13th gen (Raptor Lake)
    "13980hx": 5600,
    "13950hx": 5500,
    "13900ks": 6000,
    "13900kf": 5800,
    "13900k":  5800,
    "13900hx": 5400,
    "13900":   5600,
    "13700hx": 5000,
    "13700k":  5400,
    "13700":   5200,
    "13600kf": 5100,
    "13600k":  5100,
    "13500":   4800,

    # Intel 12th gen (Alder Lake)
    "12950hx": 5000,
    "12900ks": 5500,
    "12900kf": 5200,
    "12900k":  5200,
    "12900hx": 5000,
    "12900":   5100,
    "12700kf": 5000,
    "12700k":  5000,
    "12700":   4900,
    "12600k":  4900,

    # AMD Ryzen 9000 (Zen 5)
    "9950x3d": 5700,
    "9950x":   5700,
    "9900x3d": 5500,
    "9900x":   5600,
    "9800x3d": 5200,
    "9700x":   5500,
    "9600x":   5400,
    "hx 370":  5100,   # AI 9 HX 370
    "hx 365":  5000,

    # AMD Ryzen 7000 (Zen 4)
    "7950x3d": 5700,
    "7950x":   5700,
    "7900x3d": 5600,
    "7900x":   5600,
    "7900":    5400,
    "7800x3d": 5000,
    "7700x":   5400,
    "7700":    5300,
    "7600x":   5300,
    "7600":    5100,
    "7945hx3d": 5400,
    "7945hx":  5400,
    "7845hx":  5200,
    "7745hx":  5100,
    "7940hs":  5200,
    "7840hs":  5100,

    # AMD Ryzen 5000 (Zen 3)
    "5950x":  4900,
    "5900x":  4800,
    "5800x3d":4500,
    "5800x":  4700,
    "5700x":  4600,
    "5600x":  4600,
}


def _upgrade_cpu_max_freq(profile: dict):
    """Refresh max_freq_mhz on a cached profile so existing installs don't
    keep reporting the base clock after the lookup table ships.  Preserves
    the base in ``base_freq_mhz``; writes the cache back when the value
    actually changes."""
    cpu = profile.get("cpu") or {}
    name = cpu.get("name", "")
    cur  = int(cpu.get("max_freq_mhz", 0) or 0)
    base = int(cpu.get("base_freq_mhz", 0) or 0) or cur
    looked_up = _lookup_boost_from_name(name)
    observed  = int(cpu.get("observed_max_clock_mhz", 0) or 0)
    best = max(base, looked_up, observed)
    if best > cur or cpu.get("base_freq_mhz") is None:
        cpu["base_freq_mhz"] = base
        cpu["max_freq_mhz"]  = best
        profile["cpu"]       = cpu
        _save_cache(profile)


def _upgrade_awcc_detect(profile: dict):
    """Re-probe for AWCC on cache load so existing installs pick up broader
    path detection without requiring a manual re-scan. Only flips False→True;
    never un-sets a prior True (the user may have uninstalled and we don't
    want to surprise them on the next cache read)."""
    plat = profile.get("platform") or {}
    if plat.get("has_awcc"):
        return
    awcc_roots = [
        r"C:\Program Files\Alienware",
        r"C:\Program Files (x86)\Alienware",
    ]
    for root in awcc_roots:
        if not os.path.isdir(root):
            continue
        try:
            for entry in os.listdir(root):
                low = entry.lower()
                if "awcc" in low or "command center" in low:
                    plat["has_awcc"] = True
                    profile["platform"] = plat
                    _save_cache(profile)
                    return
        except Exception:
            continue


def _lookup_boost_from_name(name: str) -> int:
    """Return the max boost clock (MHz) for a known CPU name, or 0 if unknown.

    Matches by the longest substring present in ``name``, case-insensitive,
    so more-specific keys (e.g. "14900ks") beat less-specific ones ("14900").
    """
    if not name:
        return 0
    n = name.lower()
    best_len = 0
    best_mhz = 0
    for key, mhz in CPU_BOOST_MHZ.items():
        if key in n and len(key) > best_len:
            best_len = len(key)
            best_mhz = mhz
    return best_mhz


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
            _upgrade_cpu_max_freq(profile)
            _upgrade_awcc_detect(profile)
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
    # COM must be initialized on the calling thread before wmi.WMI() is used.
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass
    try:
        import wmi  # lazy — requires pywin32; guarded here so import failure is local
        return wmi.WMI()
    except Exception as e:
        logger.error("WMI connection failed: %s", e)
        return None


def _get_os_info() -> dict:
    edition = ""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
        edition, _ = winreg.QueryValueEx(key, "EditionID")
        winreg.CloseKey(key)
    except Exception:
        pass
    return {
        "name":    platform.system(),
        "version": platform.version(),
        "release": platform.release(),
        "edition": edition,
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

        # WMI + psutil both return the base clock on modern laptops, never
        # the Turbo Boost max.  Prefer (in order): a hardcoded lookup by CPU
        # name (known boost ceiling per Intel/AMD spec), then the observed
        # peak from LHM if it ever exceeds that, then whatever WMI gave us.
        info["base_freq_mhz"] = info["max_freq_mhz"]   # preserve the base
        looked_up = _lookup_boost_from_name(info["name"])
        if looked_up > info["max_freq_mhz"]:
            info["max_freq_mhz"] = looked_up
        observed = _load_observed_peak()
        info["observed_max_clock_mhz"] = observed
        if observed > info["max_freq_mhz"]:
            info["max_freq_mhz"] = observed

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
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW
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

    # Authoritative MediaType + BusType from MSFT_PhysicalDisk. This is what
    # Windows itself uses to classify drives, so it correctly identifies SATA
    # SSDs whose model name doesn't include "SSD" (e.g. ADATA SX900).
    # MediaType: 3=HDD, 4=SSD, 5=SCM.   BusType: 11=SATA, 17=NVMe.
    msft_by_index: dict = {}
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass
    try:
        import wmi
        storage = wmi.WMI(namespace=r"root\Microsoft\Windows\Storage")
        for pd in storage.MSFT_PhysicalDisk():
            try:
                msft_by_index[str(pd.DeviceId)] = {
                    "media": int(pd.MediaType or 0),
                    "bus":   int(pd.BusType   or 0),
                }
            except Exception:
                continue
    except Exception as e:
        logger.debug("MSFT_PhysicalDisk query failed: %s", e)

    try:
        c = _wmi_connect()
        if c:
            for disk in c.Win32_DiskDrive():
                name   = (disk.Model or "Unknown").strip()
                size   = int(disk.Size or 0)
                iface  = (disk.InterfaceType or "Unknown").strip()
                pnpid  = (disk.PNPDeviceID or "").strip().lower()
                index  = str(disk.Index) if disk.Index is not None else ""
                is_ramdisk = _detect_ramdisk(name, pnpid)

                mp         = msft_by_index.get(index, {})
                bus_type   = mp.get("bus",   0)
                media_type = mp.get("media", 0)

                if is_ramdisk:
                    is_nvme = is_ssd = is_hdd = False
                elif media_type or bus_type:
                    is_nvme = (bus_type == 17)
                    is_ssd  = (media_type == 4)
                    is_hdd  = (media_type == 3)
                else:
                    is_nvme = (
                        "nvme" in name.lower()
                        or "nvme" in iface.lower()
                        or "nvme" in pnpid
                    )
                    is_ssd  = any(x in name.lower() for x in ["ssd", "nvme", "solid"])
                    is_hdd  = (
                        not is_nvme
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
        r = subprocess.run(["nvidia-smi", "-L"], capture_output=True, timeout=5,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        flags["has_nvidia_smi"] = r.returncode == 0

        # Check for AWCC install path only.
        # NOTE: do NOT call awcc.is_available() here — this runs on the main thread
        # before the SensorThread starts, so calling into awcc._instance() would
        # claim the COM STA WMI connection on this thread, permanently locking the
        # SensorThread out.  Live WMI status is read from sensor readings instead.
        # Modern AWCC (6.x+) installs under "Alienware Command Center\AWCC";
        # legacy builds lived directly under "\AWCC".  We match either, and
        # also glob for any AWCC subfolder under the Alienware root to cover
        # unusual install layouts.
        awcc_roots = [
            r"C:\Program Files\Alienware",
            r"C:\Program Files (x86)\Alienware",
        ]
        awcc_exact = [
            r"C:\Program Files\Alienware\AWCC",
            r"C:\Program Files (x86)\Alienware\AWCC",
            r"C:\Program Files\Alienware\Alienware Command Center",
            r"C:\Program Files (x86)\Alienware\Alienware Command Center",
        ]
        awcc_installed = any(os.path.exists(p) for p in awcc_exact)
        if not awcc_installed:
            for root in awcc_roots:
                if not os.path.isdir(root):
                    continue
                try:
                    for entry in os.listdir(root):
                        low = entry.lower()
                        if "awcc" in low or "command center" in low:
                            awcc_installed = True
                            break
                except Exception:
                    pass
                if awcc_installed:
                    break
        flags["has_awcc"]     = awcc_installed
        flags["has_awcc_wmi"] = False   # populated at runtime from sensor readings

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


# ─────────────────────────────────────────────────────────────────────────────
# Observed CPU peak clock
#
# WMI's Win32_Processor.MaxClockSpeed and psutil.cpu_freq().max both report
# the CPU's base clock on modern Intel/AMD laptops (2200 MHz for the i9-14900HX,
# vs. a real Turbo Boost max of ~5.8–6.0 GHz).  There is no user-mode API that
# returns the boost ceiling directly without reading MSRs.  Instead, we track
# the highest per-core clock ever reported by LibreHardwareMonitor and treat
# that as max_freq_mhz once the machine has actually boosted.
# ─────────────────────────────────────────────────────────────────────────────

def record_observed_cpu_clock(mhz: float):
    """Called by the sensor thread with each cycle's peak per-core clock.

    Writes to hardware_profile.json only when a new high water mark is hit,
    so steady-state cost is a lock + integer compare.  Values below the
    existing peak are silently dropped.  ``mhz`` may be a float (LHM) or
    int; rounded down before comparison."""
    global _peak_cached_mhz
    if mhz is None:
        return
    try:
        mhz_int = int(mhz)
    except (TypeError, ValueError):
        return
    if mhz_int <= 0 or mhz_int > 10000:   # reject nonsense readings
        return

    with _peak_lock:
        # Lazy-load the persisted peak on first call this session.
        if _peak_cached_mhz == 0:
            _peak_cached_mhz = _load_observed_peak()
        if mhz_int <= _peak_cached_mhz:
            return
        _peak_cached_mhz = mhz_int
        new_peak = mhz_int

    _persist_observed_peak(new_peak)
    logger.info("CPU clock new peak: %d MHz", new_peak)


def observed_cpu_peak_mhz() -> int:
    """Return the highest CPU core clock ever recorded (0 if none yet)."""
    with _peak_lock:
        if _peak_cached_mhz == 0:
            return _load_observed_peak()
        return _peak_cached_mhz


def _load_observed_peak() -> int:
    try:
        with open(HARDWARE_CACHE, "r", encoding="utf-8") as f:
            profile = json.load(f)
        return int(profile.get("cpu", {}).get("observed_max_clock_mhz", 0) or 0)
    except Exception:
        return 0


def _persist_observed_peak(mhz: int):
    """Load, update, save — done under the in-process lock so two racing
    sensor cycles can't clobber each other."""
    try:
        if not os.path.exists(HARDWARE_CACHE):
            return   # nothing to update yet — next scan will pick it up
        with open(HARDWARE_CACHE, "r", encoding="utf-8") as f:
            profile = json.load(f)
        cpu = profile.setdefault("cpu", {})
        if mhz <= int(cpu.get("observed_max_clock_mhz", 0) or 0):
            return
        cpu["observed_max_clock_mhz"] = mhz
        # Keep max_freq_mhz (the UI-facing field) in sync with the true ceiling.
        if mhz > int(cpu.get("max_freq_mhz", 0) or 0):
            cpu["max_freq_mhz"] = mhz
        _save_cache(profile)
    except Exception as e:
        logger.debug("Failed to persist CPU peak clock: %s", e)
