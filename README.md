# AlienCore

Adaptive system optimizer for Windows — runs as a background service, tweaks
CPU clocks, GPU power, RAM, network, storage, and privacy settings automatically
based on what you're doing. Replaces ThrottleStop's display with live temps in
the system tray and/or a floating overlay.

---

## Project structure

```
aliencore/
├── aliencore.py            ← Main entry point (service + CLI)
├── install_deps.py         ← Run once as Admin to install dependencies
├── config.json             ← Auto-generated on first run (your settings)
├── hardware_profile.json   ← Auto-generated hardware fingerprint
│
├── core/
│   ├── constants.py        ← Paths, thresholds, default config schema
│   ├── config_manager.py   ← Load/save/reload config.json
│   ├── logger.py           ← Rotating log setup
│   ├── hardware.py         ← Hardware fingerprint (CPU/GPU/RAM/drives)
│   ├── sensors.py          ← Live sensor polling (temps, RPM, usage)
│   ├── tweaks.py           ← Applies all system tweaks
│   ├── profiles.py         ← Profile detection and switching logic
│   └── monitor.py          ← Resident monitor loop
│
├── gui/
│   └── settings_gui.py     ← Full settings UI (accessible any time)
│
├── assets/
│   └── icon.png            ← Tray icon base image
│
├── profiles/               ← Named profile snapshots (JSON)
└── logs/
    └── aliencore.log       ← Rolling log file
```

---

## Setup (first time)

1. **Install Python 3.10+** from python.org (add to PATH during install)

2. **Run the dependency installer as Administrator:**
   ```
   python install_deps.py
   ```

3. **First run (shows settings GUI):**
   ```
   python aliencore.py --firstrun
   ```
   Choose your tweaks, click Save. AlienCore initializes immediately.

4. **Install as a Windows service** (run as Administrator):
   ```
   python aliencore.py --install
   ```
   AlienCore will now start automatically at boot, before login.

---

## Daily use

- **System tray icon** shows current profile (Idle / Streaming / Gaming / Manual)
- **Tray → Open Settings** to change any option at any time
- **Tray → Override Profile** to manually lock a profile
- **Tray → View Log** to open aliencore.log
- **Tray → Exit** gracefully stops the service

---

## Portability

AlienCore is designed to run on **any Windows machine**, not just your Alienware.

On first run on a new machine, it fingerprints the hardware and calculates
appropriate tweak values for that machine's CPU, GPU, and RAM. Both Intel
and AMD CPUs are supported — Intel-specific tweaks (Thread Director,
hetero-scheduling, TVB) silently skip on AMD, and AMD-specific tools
(Curve Optimizer, PBO, CCD affinity) silently skip on Intel.

Alienware-specific features (AWCC fan curve integration, G-Mode) are
only activated if an Alienware chassis is detected.

---

## Sensor note (NVMe temps & fan RPM)

For the most complete sensor coverage, run **LibreHardwareMonitor** (the
actively maintained fork of OpenHardwareMonitor) in the background:

- Download from: https://github.com/LibreHardwareMonitor/LibreHardwareMonitor
- Enable: Options → Run On Windows Startup
- Enable: Options → Start Minimized

AlienCore connects to its WMI interface automatically if it's running.
Without it, NVMe temps and fan RPM may be unavailable or less accurate.

---

## Uninstall service

```
python aliencore.py --uninstall
```

All registry tweaks applied by AlienCore can be reversed via:
```
python aliencore.py --restore-defaults
```

---

## Requirements

- Windows 10 21H2 / 11
- Python 3.10+
- **CPU**: Intel 12th Gen+ (Alder Lake / Raptor Lake / Meteor Lake) **or**
  AMD Ryzen Zen 2 / Zen 3 / Zen 4 / Zen 5. Priority AMD coverage: Ryzen 9
  5950X and 5900X (Zen 3 Vermeer).
- **GPU**: NVIDIA GeForce, AMD Radeon, or Intel Arc — all three vendors
  supported.
  - NVIDIA sensors come from NVML (via pynvml) with nvidia-smi fallback
  - AMD Radeon and Intel Arc sensors come from LibreHardwareMonitor
- LibreHardwareMonitor is bundled via `lhm_bridge.exe` and provides
  NVMe temps, fan RPM, DIMM temps, AMD/Intel Arc GPU readings, and CPU
  package temp/watts on all supported platforms.

Python packages (installed by install_deps.py):
- psutil
- pywin32
- wmi
- pystray
- Pillow
- nvidia-ml-py (pynvml — NVIDIA GPU direct NVML calls)

No AMD-specific Python package is required — AMD GPU data is read through
the bundled LibreHardwareMonitor bridge.

## CPU Feature Matrix

| Feature                                  | Intel 12th+     | AMD Zen 3+                  | AMD Zen 2        |
|------------------------------------------|-----------------|-----------------------------|------------------|
| Sensor bar (temp / load / watts)         | Yes             | Yes                         | Yes              |
| Per-core temp / load                     | Yes (P/E split) | Yes (per CCD)               | Yes              |
| Thread Director / hetero-scheduling      | Yes             | N/A (not hybrid)            | N/A              |
| TVB Optimizer                            | Yes (i9 Raptor) | N/A                         | N/A              |
| CCD awareness (affinity hints)           | N/A             | Yes                         | Yes              |
| AI voltage tool (read + write scaffolded)| MSR 0x150       | Curve Optimizer + PBO       | PBO only         |
