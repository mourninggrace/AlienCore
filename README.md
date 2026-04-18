# AlienCore

Adaptive system optimizer for Windows — runs as a background service, tweaks
CPU clocks, GPU power, RAM, network, storage, and privacy settings automatically
based on what you're doing. Replaces ThrottleStop's display with live temps in
the system tray and/or a floating overlay.

---

## Screenshots

### Floating sensor bar — idle

![Sensor bar at idle](assets/screenshots/sensor-bar-idle.png)

The sensor bar is the always-on-top heads-up display that lives at the top (or side) of your screen. This is what it looks like when the system is quiet: profile badge reads **IDLE**, CPU is loafing at 46°C and 5%, the GPU is barely awake, and AlienCore has dropped the CPU ceiling into its low-power band. Every cell is live — the sparkline under each number shows the last 90 seconds of history, color-shifting green/amber/red as values cross the warn and critical thresholds you set in Settings. The bar is fully resizable by dragging its bottom edge and can be toggled vertical.

### Floating sensor bar — under gaming load

![Sensor bar under gaming load](assets/screenshots/sensor-bar-gaming.png)

Same bar, seconds after launching a game. The profile badge has automatically flipped to **GAMING**, the fan cell is now showing **7.4K RPM in red** because the fans have ramped, and the NVM1/NVM2/DIMM cells are tracking the drives spinning up. The transition is automatic — you don't click anything. AlienCore detected the workload from the process list, GPU activity, and load signature, then swapped profiles and applied the matching power, scheduler, network, and GPU tweaks in under a second.

### Sparkline pop-outs

![Sparkline history windows](assets/screenshots/sensor-bar-sparklines.png)

Double-click any cell on the sensor bar to pop out a larger 90-second history graph. You can open as many as you want and drag them anywhere on the desktop — shown here with NVMe temp, VRAM usage, CPU load, and GPU temp all tracked side-by-side. Each window keeps updating in real time. Useful when you want to watch a specific metric during a benchmark or troubleshoot a spike without cluttering the main bar.

### Tray icon right-click menu

![Tray right-click menu](assets/screenshots/tray-menu.png)

The alien-head tray icon is the main entry point to everything AlienCore does. It color-shifts between green, amber, and red based on CPU package temperature, so a quick glance at your taskbar tells you how hot the system is. Right-click exposes the full menu: open Settings, override the profile manually, open the AI tools, toggle the overlay or bar visibility, view the log, send feedback, toggle Windows startup, or quit the app cleanly. The two AI menu items (**AI Config Advisor**, **Open AI Chat**) are Pro features and only show up when a valid AI provider is configured.

### Settings — CPU tab

![Settings CPU tab](assets/screenshots/settings-cpu.png)

The CPU tab detects your processor (Intel Core i9 14900HX here, 24 cores / 32 threads, 2.2 GHz base) and exposes every clock and power knob AlienCore manages for it. Dynamic throttle at idle with adjustable temp-and-load triggers, idle and throttle-trigger ceilings with live sliders, full-power toggles for gaming and streaming, and the **TVB Headroom & Thermal Velocity Boost Optimizer** — which watches your live CPU-package temp against the TVB threshold and grants extra boost headroom when you're running cool. At the bottom is the **Boost Clock Sustainability Score**, a rolling rating of how much of your CPU's theoretical boost budget you're actually getting (scored by frequency, thermal headroom, or core parking — three selectable formulas).

### Settings — GPU tab

![Settings GPU tab](assets/screenshots/settings-gpu.png)

GPU tuning is NVIDIA-first and hooks directly into NVML, the same API MSI Afterburner uses. You get VRAM idle-clock locking (drops memory to 405 MHz at idle to save power and heat, releases on gaming), NVIDIA driver feature toggles like **Hardware-Accelerated GPU Scheduling** and **Prefer Maximum Performance**, and a live **Thermal Throttle Event Log** that captures timestamped throttle events so you can verify whether your fan curve or power limit is actually holding. The **Power vs. Performance Efficiency** panel at the bottom gives you a %load/Watt score — a single number telling you how much work your GPU is doing per watt, averaged across a rolling sample window.

### Settings — RAM tab

![Settings RAM tab](assets/screenshots/settings-memory.png)

RAM management breaks down Windows memory into real categories (**In Use / Modified / Standby / Free**), shows **Unified Memory Pressure** across system RAM and VRAM together (important on laptops where the iGPU shares memory), and exposes the **Working Set Trimmer** — a per-process RAM reclaim tool. Select any background process and trim its working set without killing it; Windows will reload its pages on demand when the process needs them. At the bottom is the **Memory Leak Watchdog**, which monitors per-process RSS growth rate and alerts you when a process is leaking (a common Chrome/Slack/Electron failure mode that's otherwise invisible until you run out of RAM).

### Settings — Insights tab

![Insights tab showing learned patterns](assets/screenshots/settings-insights.png)

The Insights tab is where the **Learning Engine** surfaces what AlienCore has noticed about your system over time. Shown here: 7 days of data, 1537 events logged, 16 gaming sessions, 671 thermal warnings, peak gaming hour (17:00–18:00), peak streaming hour (10:00–11:00). Below that, actionable suggestions — "CPU getting warm at idle (CPU hit warning temp 523 times over 7 days — lower trigger to 70°C?)" with a one-click button to accept, and a behavioral pattern note that you tend to game around 17:00. All learning happens locally; nothing is sent to any server.

### Settings — Drivers tab

![Installed drivers tab](assets/screenshots/settings-drivers.png)

The Drivers tab scans your installed driver stack (WMI-backed, mirrors `pnputil /enum-drivers`), shows version + date + vendor for every driver that touches a real device, and highlights any driver with a newer version available upstream. Click the **Download** column link to jump straight to the vendor page for that driver — NVIDIA links go to their driver portal, Intel to DSA, Realtek to Realtek's audio page, etc. Saves the "which of my drivers is actually out of date" question that normally takes opening five websites to answer.

### AI Config Advisor

![AI Config Advisor window](assets/screenshots/ai-config-advisor.png)

The **AI Config Advisor** is a Pro feature that sends a snapshot of your current configuration plus recent sensor history to the AI provider of your choice (Claude, GPT, Gemini, Groq, Mistral, or a local Ollama / LM Studio instance) and asks it to propose tuned values. The result is a table of **Setting → Current → Proposed → Reason**, each row individually checkboxable — you review what the AI wants to change, select the ones you like, and click **Apply Selected**. Nothing gets applied without your explicit confirmation, and the **Rollback** button at the bottom right restores the config snapshot from the last time the Advisor was run.

### AI Chat

![AI chat window](assets/screenshots/ai-chat.png)

The **AI Chat** window is a direct pipe to any OpenAI-compatible provider, with AlienCore's live system context (temps, clocks, active profile, detected hardware) silently attached to every conversation. Ask *"why is my CPU at 85°C just browsing?"* and it can actually see your sensor readings and config when it answers. Clear and Context buttons in the top-right let you reset or inspect what's being sent. Provider selection happens on the Settings → AI tab — same chat window regardless of who's behind it.

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
│   ├── icon.png            ← Tray icon base image
│   └── screenshots/        ← Gallery images used in this README
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
