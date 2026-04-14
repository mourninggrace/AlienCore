"""
AlienCore - pagefile_advisor.py
Rule-based pagefile recommendation engine.

Analyzes total RAM, typical usage patterns, and whether NVMe drives
are present to recommend an optimal pagefile configuration.
"""

import logging
import os
import psutil

logger = logging.getLogger("aliencore.pagefile_advisor")


def get_advice(hardware: dict = None) -> dict:
    """
    Analyze system state and return pagefile recommendations.
    Returns:
    {
      recommendation: str   — "managed" | "custom" | "none"
      size_mb: int | None   — only set for "custom"
      reason: str           — human-readable explanation
      current_mb: int       — current configured pagefile size (0 = managed)
      min_recommended_mb: int
      max_recommended_mb: int
      has_nvme: bool
      total_ram_gb: float
      peak_usage_pct: float
    }
    """
    vm         = psutil.virtual_memory()
    total_gb   = vm.total / 1024**3
    usage_pct  = vm.percent
    has_nvme   = False
    total_mb   = int(vm.total / 1024**2)

    if hardware:
        has_nvme = any(d.get("is_nvme") for d in hardware.get("drives", []))
    else:
        # Try to detect NVMe from disk info
        try:
            for part in psutil.disk_partitions():
                if "nvme" in part.device.lower():
                    has_nvme = True
                    break
        except Exception:
            pass

    # Current pagefile size from Windows registry
    current_mb  = _query_current_pagefile_mb()

    # ── Rule engine ──────────────────────────────────────────────────────────

    # Rule 1: Lots of RAM + NVMe + low usage → small managed pagefile is fine
    if total_gb >= 32 and has_nvme and usage_pct < 60:
        rec = "managed"
        size_mb = None
        reason  = (
            f"You have {total_gb:.0f} GB RAM and an NVMe drive, with current usage at "
            f"{usage_pct:.0f}%. Windows-managed pagefile is optimal — it'll stay small "
            f"and the NVMe is fast enough to handle any spills without noticeable stutter."
        )
        min_mb  = int(total_mb * 0.125)   # 1/8 of RAM minimum
        max_mb  = int(total_mb * 0.5)     # 1/2 of RAM max

    # Rule 2: High RAM pressure on any drive
    elif usage_pct >= 80:
        rec    = "custom"
        # Set to 1.5× RAM or at minimum 8 GB
        size_mb = max(8192, int(total_mb * 1.5))
        reason  = (
            f"Memory pressure is high ({usage_pct:.0f}% used). A larger custom pagefile "
            f"({size_mb // 1024:.0f} GB) acts as a safety net and helps prevent out-of-memory "
            f"crashes during peak loads."
        )
        min_mb  = int(total_mb * 1.0)
        max_mb  = int(total_mb * 2.0)

    # Rule 3: Low RAM + any drive
    elif total_gb < 16:
        rec    = "custom"
        size_mb = max(4096, int(total_mb * 2.0))
        reason  = (
            f"With only {total_gb:.0f} GB RAM, a custom pagefile ({size_mb // 1024:.0f} GB) "
            f"is strongly recommended to handle memory spikes without crashing."
        )
        min_mb  = int(total_mb * 1.5)
        max_mb  = int(total_mb * 3.0)

    # Rule 4: Adequate RAM + HDD
    elif not has_nvme and total_gb < 32:
        rec    = "custom"
        size_mb = int(total_mb * 1.0)   # match RAM
        reason  = (
            f"Your primary drive is not NVMe. A custom pagefile equal to your RAM size "
            f"({size_mb // 1024:.0f} GB) ensures Windows allocates space upfront to avoid "
            f"fragmentation on rotational drives."
        )
        min_mb  = int(total_mb * 0.5)
        max_mb  = int(total_mb * 2.0)

    else:
        rec     = "managed"
        size_mb = None
        reason  = (
            f"Your system has {total_gb:.0f} GB RAM with {usage_pct:.0f}% typical usage. "
            f"Windows-managed pagefile is the best option — it adapts automatically."
        )
        min_mb  = int(total_mb * 0.125)
        max_mb  = int(total_mb * 0.5)

    return {
        "recommendation":      rec,
        "size_mb":             size_mb,
        "reason":              reason,
        "current_mb":          current_mb,
        "min_recommended_mb":  min_mb,
        "max_recommended_mb":  max_mb,
        "has_nvme":            has_nvme,
        "total_ram_gb":        round(total_gb, 1),
        "peak_usage_pct":      round(usage_pct, 1),
    }


def _query_current_pagefile_mb() -> int:
    """Read current pagefile size from Windows registry."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management",
        )
        val, _ = winreg.QueryValueEx(key, "PagingFiles")
        winreg.CloseKey(key)
        # Format: "C:\pagefile.sys MinMB MaxMB" (list, first entry)
        if isinstance(val, (list, tuple)):
            entry = str(val[0]) if val else ""
        else:
            entry = str(val)
        parts = entry.split()
        if len(parts) >= 3:
            return int(parts[2])
        return 0
    except Exception:
        return 0


def apply_recommendation(advice: dict) -> tuple:
    """
    Apply the pagefile recommendation via WMI.
    Returns (success: bool, message: str).
    """
    try:
        import wmi
        c = wmi.WMI()
        cs = c.Win32_ComputerSystem()[0]

        if advice["recommendation"] == "managed":
            cs.AutomaticManagedPagefile = True
            cs.Put_()
            return True, "Pagefile set to Windows-managed."

        elif advice["recommendation"] == "custom" and advice["size_mb"]:
            size = advice["size_mb"]
            cs.AutomaticManagedPagefile = False
            cs.Put_()
            # Set pagefile on system drive
            import subprocess
            result = subprocess.run(
                ["wmic", "pagefileset", "where", "name='C:\\\\pagefile.sys'",
                 "set", f"InitialSize={size}", f"MaximumSize={size}"],
                capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if result.returncode == 0:
                return True, f"Pagefile set to {size} MB. A reboot is required for full effect."
            return False, result.stderr.strip() or "Failed to configure pagefile."

        return False, "No recommendation to apply."
    except Exception as e:
        return False, str(e)
