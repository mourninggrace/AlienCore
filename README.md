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

AlienCore v1 is designed to run on **any Intel + NVIDIA Windows machine**,
not just your Alienware.

On first run on a new machine, it fingerprints the hardware and calculates
appropriate tweak values for that machine's CPU, GPU, and RAM. Tuning
features (voltage offsets, GPU clock/power/fan control) require an
Intel CPU and an NVIDIA GPU. Generic OS tweaks (power plan, scheduler,
network, storage, privacy) apply to any Windows machine regardless of
silicon.

AMD Ryzen and Radeon tuning are planned for a future release.

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
- **CPU**: Intel 12th Gen or newer (Alder Lake / Raptor Lake / Meteor Lake).
  AMD Ryzen support is planned for a future release.
- **GPU**: NVIDIA GeForce (tuning via NVML / pynvml). AMD Radeon / Intel Arc
  tuning are planned for a future release; sensor readings still work for
  these through LibreHardwareMonitor, but runtime clock/power/fan control
  is NVIDIA-only in v1.
- LibreHardwareMonitor is bundled via `lhm_bridge.exe` and provides
  NVMe temps, fan RPM, DIMM temps, non-NVIDIA GPU readings, and CPU
  package temp/watts.

Python packages (installed by install_deps.py):
- psutil
- pywin32
- wmi
- pystray
- Pillow
- nvidia-ml-py (pynvml — NVIDIA GPU direct NVML calls)

## CPU Feature Matrix (v1)

| Feature                                  | Intel 12th+     |
|------------------------------------------|-----------------|
| Sensor bar (temp / load / watts)         | Yes             |
| Per-core temp / load                     | Yes (P/E split) |
| Thread Director / hetero-scheduling      | Yes             |
| TVB Optimizer                            | Yes (i9 Raptor) |
| AI voltage tool (read + write scaffolded)| MSR 0x150       |
