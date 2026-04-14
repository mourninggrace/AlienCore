"""
AlienCore - sensors.py
Polls live system sensors on a background thread.

Sources:
  lhm_bridge exe  → CPU temp, GPU temp, NVMe temp, RAM temp, watts
                    via LibreHardwareMonitorLib (subprocess JSON)
  nvidia-smi      → GPU load, VRAM, GPU watts, GPU fan (more detailed)
  psutil          → RAM usage %, CPU load %

Note: Alienware fan RPM is not accessible via LHM — controlled by AWCC embedded
controller. Fan icon is replaced with RAM stick temperature instead.
"""

import logging
import subprocess
import threading
import time
import psutil
from core.constants import (
    SENSOR_POLL_INTERVAL,
    COLOR_COOL, COLOR_WARM, COLOR_HOT,
)

logger = logging.getLogger("aliencore.sensors")

_lock           = threading.Lock()
_readings       = {}
_thread         = None
_running        = False
_prev_net_io    = None
_prev_net_time  = None
_prev_disk_io   = None
_prev_disk_time = None


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def start():
    global _thread, _running
    _running = True
    _thread  = threading.Thread(target=_poll_loop, name="SensorThread", daemon=True)
    _thread.start()
    logger.info("Sensor polling started (interval: %ds)", SENSOR_POLL_INTERVAL)


def stop():
    global _running
    _running = False
    logger.info("Sensor polling stopped.")


def get_readings() -> dict:
    with _lock:
        return dict(_readings)


def color_for_temp(temp_c: float, warn: float, crit: float) -> str:
    if temp_c is None:
        return COLOR_COOL
    if temp_c >= crit:
        return COLOR_HOT
    if temp_c >= warn:
        return COLOR_WARM
    return COLOR_COOL


def fmt_temp(val_c: float) -> str:
    """
    Format a Celsius temperature value for display, respecting the user's
    chosen unit (celsius / fahrenheit). Always call with raw °C values.
    Returns a compact string like '72°' or '162°'.
    """
    from core import config_manager as cfg
    unit = cfg.get().get("display", {}).get("temp_unit", "celsius")
    if unit == "fahrenheit":
        return f"{int(val_c * 9 / 5 + 32)}°"
    return f"{int(val_c)}°"


# ─────────────────────────────────────────────────────────────────────────────
# Poll loop
# ─────────────────────────────────────────────────────────────────────────────

def _poll_loop():
    # COM must be initialized on every thread that uses WMI.
    # Doing it here ensures the AWCC WMI connection is created on this thread,
    # which is required by the COM STA apartment model.
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception as e:
        logger.warning("SensorThread CoInitialize failed: %s", e)

    while _running:
        try:
            flat = _get_lhm_sensors()

            data = {}
            data.update(_parse_cpu_temp(flat))
            data.update(_parse_gpu_temp(flat))
            data.update(_parse_nvme_temp(flat))
            data.update(_parse_ram_temp(flat))
            data.update(_parse_fan_rpm(flat))
            data.update(_parse_cpu_watts(flat))
            data.update(_read_gpu_nvidia_smi())
            data.update(_read_ram_usage())
            # Feed boost tracker with latest CPU freq + temp
            _update_boost_tracker(data)
            data.update(_read_cpu_load())
            data.update(_read_cpu_freq())
            data.update(_read_battery())
            data.update(_read_network_io())
            data.update(_read_disk_io())
            data.update(_read_awcc_data())

            with _lock:
                _readings.update(data)

        except Exception as e:
            logger.debug("Sensor poll error: %s", e)

        time.sleep(SENSOR_POLL_INTERVAL)


# ─────────────────────────────────────────────────────────────────────────────
# LHM bridge
# ─────────────────────────────────────────────────────────────────────────────

def _get_lhm_sensors() -> list:
    """Call lhm_bridge.exe and return its flat sensor list."""
    from core import lhm_manager
    return lhm_manager.get_sensors()


# ─────────────────────────────────────────────────────────────────────────────
# Individual sensor parsers — matched to exact LHM names on this machine
# Bridge format: {name, hardware, hardwareType, type, value}
# type = LHM SensorType string: Temperature, Power, Load, Fan, Clock, ...
# ─────────────────────────────────────────────────────────────────────────────

def _parse_cpu_temp(flat: list) -> dict:
    result = {"cpu_temp_avg": None, "cpu_temp_package": None, "cpu_temp_cores": []}

    temps = [s for s in flat if s["type"] == "Temperature"]

    # CPU Package — primary Intel thermal sensor, most accurate single value
    pkg = next((s for s in temps if s["name"] == "CPU Package"), None)
    if pkg:
        result["cpu_temp_package"] = pkg["value"]
        result["cpu_temp_avg"]     = pkg["value"]

    # Core Average — fallback if Package unavailable
    if result["cpu_temp_avg"] is None:
        avg = next((s for s in temps if s["name"] == "Core Average"), None)
        if avg:
            result["cpu_temp_avg"] = avg["value"]

    # Individual core temps — exclude "Distance to TjMax"
    cores = [s for s in temps
             if ("P-Core #" in s["name"] or "E-Core #" in s["name"])
             and "distance" not in s["name"].lower()
             and 0 < s["value"] < 120]
    if cores:
        result["cpu_temp_cores"] = [s["value"] for s in cores]

    return result


def _parse_gpu_temp(flat: list) -> dict:
    result = {
        "gpu_temp":         None,
        "gpu_temp_hotspot": None,
        "gpu_temp_memory":  None,
    }
    temps = [s for s in flat if s["type"] == "Temperature"]
    core = next((s for s in temps if s["name"] == "GPU Core"), None)
    if core:
        result["gpu_temp"] = core["value"]
    hot = next((s for s in temps if s["name"] == "GPU Hot Spot"), None)
    if hot:
        result["gpu_temp_hotspot"] = hot["value"]
    mem = next((s for s in temps if s["name"] == "GPU Memory Junction"), None)
    if mem:
        result["gpu_temp_memory"] = mem["value"]
    return result


def _parse_nvme_temp(flat: list) -> dict:
    result = {"nvme_temps": []}
    temps = [s for s in flat if s["type"] == "Temperature"]
    composites = [s for s in temps if s["name"] == "Composite Temperature"]
    for i, s in enumerate(composites):
        if 0 < s["value"] < 100:
            result["nvme_temps"].append({
                "name":   f"NVMe {i+1}",
                "temp_c": round(s["value"], 1),
            })
    return result


def _parse_ram_temp(flat: list) -> dict:
    result = {"ram_temps": []}
    temps = [s for s in flat if s["type"] == "Temperature"]
    dimms = [s for s in temps if s["name"].startswith("DIMM #")]
    for s in dimms:
        if 0 < s["value"] < 100:
            result["ram_temps"].append({
                "name":   s["name"],
                "temp_c": round(s["value"], 1),
            })
    return result


def _parse_fan_rpm(flat: list) -> dict:
    """
    Read fan RPMs from LHM via the Fan sensor type.
    LHM reads Alienware fan data through the Embedded Controller (EC) when the
    hardware exposes it.  Returns lhm_fan_rpms: list of {name, rpm} sorted by name,
    only including fans with RPM > 0.  Empty list if LHM reports no fan data.
    """
    result = {"lhm_fan_rpms": []}
    fans = [s for s in flat if s["type"] == "Fan"]
    for s in fans:
        rpm = s.get("value", 0)
        if rpm is not None and rpm > 0:
            result["lhm_fan_rpms"].append({
                "name": s.get("name", "Fan"),
                "rpm":  round(rpm),
            })
    result["lhm_fan_rpms"].sort(key=lambda f: f["name"])
    return result


def _parse_cpu_watts(flat: list) -> dict:
    result = {"cpu_watts": None}
    power = [s for s in flat if s["type"] == "Power"]
    pkg = next((s for s in power if s["name"] == "CPU Package"), None)
    if pkg:
        result["cpu_watts"] = pkg["value"]
    return result


# ─────────────────────────────────────────────────────────────────────────────
# GPU — nvidia-smi (more detailed than LHM for NVIDIA)
# ─────────────────────────────────────────────────────────────────────────────

def _read_gpu_nvidia_smi() -> dict:
    result = {
        "gpu_load":               None,
        "gpu_vram_used_mb":       None,
        "gpu_vram_total_mb":      None,
        "gpu_watts":              None,
        "gpu_fan_pct":            None,
        "gpu_clock_mhz":          None,
        "gpu_mem_clock_mhz":      None,
        "gpu_power_limit_w":      None,
        "gpu_throttle_reasons":   "0x0000000000000000",
        "gpu_throttle_perf_lost": False,
    }
    try:
        fields = ("utilization.gpu,memory.used,memory.total,"
                  "power.draw,fan.speed,clocks.gr,clocks.mem,"
                  "power.limit,clocks_throttle_reasons.active")
        proc = subprocess.run(
            ["nvidia-smi", f"--query-gpu={fields}",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        if proc.returncode == 0:
            parts = [p.strip() for p in proc.stdout.strip().split(",")]
            def _f(v):
                try:    return float(v)
                except: return None
            if len(parts) >= 5:
                result["gpu_load"]          = _f(parts[0])
                result["gpu_vram_used_mb"]  = _f(parts[1])
                result["gpu_vram_total_mb"] = _f(parts[2])
                result["gpu_watts"]         = _f(parts[3])
                result["gpu_fan_pct"]       = _f(parts[4])
            if len(parts) >= 7:
                result["gpu_clock_mhz"]     = _f(parts[5])
                result["gpu_mem_clock_mhz"] = _f(parts[6])
            if len(parts) >= 8:
                result["gpu_power_limit_w"] = _f(parts[7])
            if len(parts) >= 9:
                throttle_hex = parts[8].strip()
                result["gpu_throttle_reasons"] = throttle_hex
                # Record throttle events and compute efficiency
                from core import throttle_log
                throttle_log.record(
                    throttle_hex,
                    gpu_temp=None,   # temp comes from LHM — will be correlated in monitor
                    gpu_watts=result["gpu_watts"],
                )
                result["gpu_throttle_perf_lost"] = throttle_log.is_perf_limited(throttle_hex)
    except FileNotFoundError:
        logger.debug("nvidia-smi not found")
    except Exception as e:
        logger.debug("nvidia-smi error: %s", e)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# RAM usage and CPU load — psutil
# ─────────────────────────────────────────────────────────────────────────────

def _read_ram_usage() -> dict:
    try:
        vm = psutil.virtual_memory()
        return {
            "ram_used_gb":   round(vm.used  / 1024**3, 1),
            "ram_total_gb":  round(vm.total / 1024**3, 1),
            "ram_usage_pct": vm.percent,
        }
    except Exception:
        return {"ram_used_gb": None, "ram_total_gb": None, "ram_usage_pct": None}


def _read_cpu_load() -> dict:
    try:
        per_core = psutil.cpu_percent(percpu=True)
        avg      = round(sum(per_core) / len(per_core), 1) if per_core else None
        return {"cpu_load_pct": avg, "cpu_load_cores": per_core}
    except Exception:
        return {"cpu_load_pct": None, "cpu_load_cores": []}


def _read_cpu_freq() -> dict:
    try:
        freq = psutil.cpu_freq()
        if freq and freq.current:
            return {"cpu_freq_ghz": round(freq.current / 1000, 2)}
    except Exception:
        pass
    return {"cpu_freq_ghz": None}


def _read_battery() -> dict:
    try:
        bat = psutil.sensors_battery()
        if bat is not None:
            secs = bat.secsleft
            return {
                "battery_pct":      round(bat.percent, 1),
                "battery_charging": bat.power_plugged,
                "battery_secs":     None if secs in (-1, -2) else secs,
            }
    except Exception:
        pass
    return {"battery_pct": None, "battery_charging": False, "battery_secs": None}


def _read_network_io() -> dict:
    global _prev_net_io, _prev_net_time
    import time as _time
    try:
        now = _time.monotonic()
        cur = psutil.net_io_counters()
        if _prev_net_io is None:
            _prev_net_io   = cur
            _prev_net_time = now
            return {"net_up_mbps": 0.0, "net_down_mbps": 0.0}
        dt = now - _prev_net_time
        if dt < 0.1:
            return {"net_up_mbps": 0.0, "net_down_mbps": 0.0}
        up = (cur.bytes_sent - _prev_net_io.bytes_sent) / dt / 1_048_576
        dn = (cur.bytes_recv - _prev_net_io.bytes_recv) / dt / 1_048_576
        _prev_net_io   = cur
        _prev_net_time = now
        return {
            "net_up_mbps":   max(0.0, round(up, 2)),
            "net_down_mbps": max(0.0, round(dn, 2)),
        }
    except Exception as e:
        logger.debug("net_io error: %s", e)
        return {"net_up_mbps": None, "net_down_mbps": None}


def _read_disk_io() -> dict:
    global _prev_disk_io, _prev_disk_time
    import time as _time
    try:
        now = _time.monotonic()
        cur = psutil.disk_io_counters()
        if cur is None:
            return {"disk_read_mbps": None, "disk_write_mbps": None}
        if _prev_disk_io is None:
            _prev_disk_io   = cur
            _prev_disk_time = now
            return {"disk_read_mbps": 0.0, "disk_write_mbps": 0.0}
        dt = now - _prev_disk_time
        if dt < 0.1:
            return {"disk_read_mbps": 0.0, "disk_write_mbps": 0.0}
        rd = (cur.read_bytes  - _prev_disk_io.read_bytes)  / dt / 1_048_576
        wr = (cur.write_bytes - _prev_disk_io.write_bytes) / dt / 1_048_576
        _prev_disk_io   = cur
        _prev_disk_time = now
        return {
            "disk_read_mbps":  max(0.0, round(rd, 2)),
            "disk_write_mbps": max(0.0, round(wr, 2)),
        }
    except Exception as e:
        logger.debug("disk_io error: %s", e)
        return {"disk_read_mbps": None, "disk_write_mbps": None}


def _update_boost_tracker(data: dict):
    """Feed the latest CPU freq + package temp into boost_tracker."""
    try:
        from core import boost_tracker
        freq_ghz = data.get("cpu_freq_ghz")
        temp_c   = data.get("cpu_temp_package") or data.get("cpu_temp_avg")
        if freq_ghz is not None:
            boost_tracker.record(freq_ghz, temp_c)
    except Exception:
        pass


def _read_awcc_data() -> dict:
    """
    Poll AWCC WMI for fan RPMs and active thermal profile.
    Also drains the write command queue posted by other threads (profiles,
    turbo_cool, tray) so that all AWCC WMI calls happen on this thread only.
    Returns empty/False defaults if AWCC is unavailable so callers never KeyError.
    """
    _empty = {"awcc_available": False, "awcc_fans": [], "awcc_profile": None}
    try:
        from core import awcc
        # Drain write commands posted by other threads BEFORE reading state.
        # This keeps all WMI access on the SensorThread (COM STA requirement).
        awcc.drain_queue()
        if not awcc.is_available():
            return _empty
        fans    = awcc.get_fan_rpms()
        profile = awcc.get_active_profile()
        return {
            "awcc_available": True,
            "awcc_fans":      fans,      # list of {fan_id, rpm}
            "awcc_profile":   profile,   # int profile ID or None
        }
    except Exception as e:
        logger.debug("AWCC sensor read error: %s", e)
        return _empty
