# -*- mode: python ; coding: utf-8 -*-
"""
AlienCore PyInstaller spec — onedir build.

Compile from project root:
    pyinstaller --noconfirm installer/aliencore.spec

Output:
    dist/AlienCore/AlienCore.exe    (entry point)
    dist/AlienCore/_internal/...    (Python runtime + bundled deps + manual.html)

Inno Setup (installer/aliencore.iss) then takes dist/AlienCore/ and
ships it inside Program Files\\AlienCore\\, alongside lhm_bridge.exe
and the LICENSE / CHANGELOG / README.

DESIGN NOTES
============
- onedir (not onefile): faster cold-start, easier to inspect on disk.
- console=False: GUI app, no terminal window.
- uac_admin=False: AlienCore's Python startup logic does its own UAC
  relaunch via core/elevation.relaunch_as_admin(); the installer's
  Debug shortcut passes --no-elevate to skip that.  uac_admin=True at
  the manifest level would prevent --no-elevate from working at all.
- upx=False: UPX-packed binaries are a frequent AV false-positive
  vector; given AlienCore reads MSRs and queries WMI the AV/EDR
  surface is already touchy enough.
"""

import os

PROJECT_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))


a = Analysis(
    [os.path.join(PROJECT_ROOT, "aliencore.py")],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=[
        # gui/settings_gui.py reads manual.html via its own __file__-based
        # path resolution, which lands inside _internal/ when frozen.
        # Bundling here makes "Help → User Manual" work in installed builds.
        (os.path.join(PROJECT_ROOT, "docs", "manual.html"), "docs"),
    ],
    hiddenimports=[
        # pywin32 / WMI / COM — all dynamically loaded
        "win32com",
        "win32com.client",
        "win32api",
        "pywintypes",
        "pythoncom",
        "wmi",
        # NVIDIA management library (lazy-imported in core/sensors.py)
        "pynvml",
        # cryptography back-end — the asymmetric module imports are
        # PEP-562 lazy and PyInstaller's static analysis can miss them
        "cryptography",
        "cryptography.hazmat",
        "cryptography.hazmat.backends",
        "cryptography.hazmat.backends.openssl",
        "cryptography.hazmat.primitives",
        "cryptography.hazmat.primitives.serialization",
        "cryptography.hazmat.primitives.asymmetric",
        "cryptography.hazmat.primitives.asymmetric.ed25519",
        # AMD SMU fallback module is conditionally imported from sensors.py
        "core.amd_smu",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Server-side only — never reachable from the client, no point bundling
        "backend",
        "backend.server",
        "backend.config",
        "backend.db",
        "backend.mail",
        # Test + dev tooling
        "tests",
        "pytest",
        # Tools that are dev-only
        "tools.test_updater_e2e",
        "tools.generate_license_keypair",
    ],
    noarchive=False,
)


pyz = PYZ(a.pure, a.zipped_data)


exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AlienCore",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(PROJECT_ROOT, "assets", "icon.ico"),
    version=os.path.join(PROJECT_ROOT, "installer", "version_info.txt"),
    uac_admin=False,
    uac_uiaccess=False,
)


coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AlienCore",
)
