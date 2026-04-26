"""
AlienCore - storage_info.py
Maps physical drive model names to a drive *kind* — "nvme", "ssd", or "hdd".

LibreHardwareMonitor lumps every storage device under HardwareType.Storage
and only differentiates NVMe vs SATA via sensor names ("Composite Temperature"
vs plain "Temperature").  It can't tell SATA SSDs from spinning HDDs, which
is exactly what the sensor bar needs to label cells correctly.

Windows already knows: Get-PhysicalDisk reports MediaType (SSD/HDD) and
BusType (NVMe/SATA/USB/...).  We invoke it once per process, cache the
result, and expose a fuzzy lookup keyed by drive friendly name.
"""

import json
import logging
import re
import subprocess

logger = logging.getLogger("aliencore.storage_info")

_drive_kinds: dict = {}   # normalized model name → "nvme"|"ssd"|"hdd"
_loaded = False


def _normalize(name: str) -> str:
    """Lowercase and strip non-alphanumerics so spacing/underscore differences
    between LHM hw.Name and Windows FriendlyName don't break matching."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _query_drive_kinds() -> dict:
    """Run Get-PhysicalDisk and return {normalized friendly name → kind}.
    Empty dict on any failure — callers fall back to heuristic classification.
    """
    cmd = [
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        "Get-PhysicalDisk | Select-Object FriendlyName, MediaType, BusType "
        "| ConvertTo-Json -Compress",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug("Get-PhysicalDisk invocation failed: %s", e)
        return {}

    if result.returncode != 0 or not result.stdout.strip():
        logger.debug("Get-PhysicalDisk returned %d / no output", result.returncode)
        return {}

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        logger.debug("Get-PhysicalDisk JSON parse failed: %s", e)
        return {}

    # Single drive yields a dict; multi-drive yields a list.
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return {}

    out = {}
    for d in data:
        if not isinstance(d, dict):
            continue
        name = (d.get("FriendlyName") or "").strip()
        if not name:
            continue
        bus   = str(d.get("BusType") or "").strip().lower()
        media = str(d.get("MediaType") or "").strip().lower()

        if bus == "nvme":
            kind = "nvme"
        elif media == "ssd":
            kind = "ssd"
        elif media == "hdd":
            kind = "hdd"
        else:
            kind = "unknown"
        out[_normalize(name)] = kind
    return out


def _ensure_loaded() -> None:
    global _drive_kinds, _loaded
    if _loaded:
        return
    _drive_kinds = _query_drive_kinds()
    _loaded = True


def get_drive_counts() -> dict:
    """Return {"nvme": N, "ssd": M, "hdd": K} from the cached Windows drive
    list.  Used by the sensor bar to decide which storage slots to show
    *before* LHM has produced any sensor data, so cells appear immediately
    at startup instead of materializing 5 s later when the first valid LHM
    Storage update arrives."""
    _ensure_loaded()
    counts = {"nvme": 0, "ssd": 0, "hdd": 0}
    for kind in _drive_kinds.values():
        if kind in counts:
            counts[kind] += 1
    return counts


def get_drive_kind(hw_name: str) -> str:
    """Return "nvme" | "ssd" | "hdd" | "unknown" for an LHM drive name.

    Tries exact normalized match first, then substring match in either
    direction (LHM and Windows can format the same model differently —
    e.g. "Crucial CT500MX500SSD1" vs "Crucial_CT500MX500SSD1").
    """
    _ensure_loaded()
    if not _drive_kinds:
        return "unknown"
    needle = _normalize(hw_name)
    if not needle:
        return "unknown"
    if needle in _drive_kinds:
        return _drive_kinds[needle]
    for win_name, kind in _drive_kinds.items():
        if needle in win_name or win_name in needle:
            return kind
    return "unknown"
