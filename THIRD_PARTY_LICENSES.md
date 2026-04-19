# Third-Party Licenses

## LibreHardwareMonitor

- **Repository:** https://github.com/LibreHardwareMonitor/LibreHardwareMonitor
- **License:** Mozilla Public License 2.0 (MPL-2.0)
- **Version used:** 0.9.6

AlienCore bundles `LibreHardwareMonitorLib.dll` inside `tools/lhm_bridge/dist/`
as part of the `lhm_bridge.exe` daemon. This library provides CPU, GPU, NVMe,
DIMM, and fan sensor readings via the Windows hardware monitoring APIs (MSR,
SMART, SuperIO/EC, SMBus).

The full text of the MPL-2.0 license is available at:
https://www.mozilla.org/en-US/MPL/2.0/

---

Other runtime dependencies (psutil, pywin32, pystray, Pillow, pynvml) are
installed separately via `install_deps.py` and carry their own licenses
(BSD, PSF, MIT, LGPL). They are not redistributed with this repository.
