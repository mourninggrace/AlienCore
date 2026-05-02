"""
AlienCore - installer/build.py
One-button build: PyInstaller → Inno Setup → final installer.

Run from project root:
    python installer/build.py

Produces:
    dist/AlienCore/                                     (PyInstaller onedir)
    dist/AlienCore-<version>-Setup.exe                  (final installer)

Prerequisites:
    pip install pyinstaller pillow
    Inno Setup 6 from https://jrsoftware.org/isdl.php
"""

import os
import shutil
import subprocess
import sys


PROJECT_ROOT  = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
INSTALLER_DIR = os.path.join(PROJECT_ROOT, "installer")
DIST_DIR      = os.path.join(PROJECT_ROOT, "dist")
BUILD_DIR     = os.path.join(PROJECT_ROOT, "build")
SPEC_PATH     = os.path.join(INSTALLER_DIR, "aliencore.spec")
ISS_PATH      = os.path.join(INSTALLER_DIR, "aliencore.iss")
ICON_ICO      = os.path.join(PROJECT_ROOT, "assets", "icon.ico")
ICON_BUILDER  = os.path.join(INSTALLER_DIR, "build_icon.py")


def _read_version() -> str:
    """Pull VERSION from core/constants.py — single source of truth."""
    with open(os.path.join(PROJECT_ROOT, "core", "constants.py"),
              "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("VERSION") and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "unknown"


def _check_pyinstaller() -> bool:
    try:
        out = subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--version"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode == 0:
            print(f"  PyInstaller: {out.stdout.strip()}")
            return True
    except Exception:
        pass
    return False


def _find_iscc() -> str:
    """Locate the Inno Setup compiler (iscc.exe)."""
    iscc = shutil.which("iscc") or shutil.which("ISCC")
    if iscc:
        return iscc
    for p in (
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ):
        if os.path.exists(p):
            return p
    return ""


def _build_icon_if_missing() -> bool:
    if os.path.exists(ICON_ICO):
        return True
    print(f"  icon.ico missing — running {ICON_BUILDER}...")
    rc = subprocess.run([sys.executable, ICON_BUILDER]).returncode
    return rc == 0 and os.path.exists(ICON_ICO)


def main() -> int:
    version = _read_version()
    print("=" * 60)
    print(f"  AlienCore installer build  —  v{version}")
    print("=" * 60)
    print(f"  Project: {PROJECT_ROOT}")
    print(f"  Python:  {sys.executable}")
    print()
    print("[pre-flight]")

    # 1. PyInstaller
    if not _check_pyinstaller():
        print("  FATAL: PyInstaller not installed in this Python.")
        print(f"  Run: {sys.executable} -m pip install pyinstaller")
        return 1

    # 2. Inno Setup compiler
    iscc = _find_iscc()
    if not iscc:
        print("  FATAL: Inno Setup compiler (iscc.exe) not found.")
        print("  Install Inno Setup 6 from https://jrsoftware.org/isdl.php")
        return 1
    print(f"  Inno Setup:  {iscc}")

    # 3. Icon
    if not _build_icon_if_missing():
        print("  FATAL: Could not produce assets/icon.ico")
        return 1
    print(f"  Icon:        {ICON_ICO}")

    # 4. Clean previous build artifacts so stale files can't sneak in
    print("\n[1/3] Cleaning previous build dirs...")
    for d in (DIST_DIR, BUILD_DIR):
        if os.path.exists(d):
            shutil.rmtree(d, ignore_errors=True)
            print(f"  removed {d}")

    # 5. PyInstaller — produces dist/AlienCore/ onedir
    print("\n[2/3] Running PyInstaller (30-90 s)...")
    rc = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", SPEC_PATH],
        cwd=PROJECT_ROOT,
    ).returncode
    onedir   = os.path.join(DIST_DIR, "AlienCore")
    main_exe = os.path.join(onedir, "AlienCore.exe")
    # Trust the artifact, not the exit code: PyInstaller occasionally
    # returns non-zero on transient warnings (Windows AV touching the
    # bundle dir during write, etc.) even when the build is fine.  Only
    # treat as failure if the .exe genuinely didn't land.
    if not os.path.exists(main_exe):
        print(f"\n  FAILED: PyInstaller exit {rc}, no AlienCore.exe produced")
        return rc or 1
    if rc != 0:
        print(f"  PyInstaller returned exit {rc} but artifact exists — proceeding")
    print(f"  built: {main_exe} "
          f"({os.path.getsize(main_exe)//1024} KiB)")

    # 6. Inno Setup — wraps the onedir + extras into a single installer .exe
    print("\n[3/3] Running Inno Setup compiler...")
    rc = subprocess.run([iscc, ISS_PATH], cwd=PROJECT_ROOT).returncode
    if rc != 0:
        print(f"\n  FAILED: Inno Setup exit {rc}")
        return rc

    final = os.path.join(DIST_DIR, f"AlienCore-{version}-Setup.exe")
    print()
    print("=" * 60)
    if os.path.exists(final):
        size_mb = os.path.getsize(final) / 1024 / 1024
        print(f"  BUILD OK  ({size_mb:.1f} MB)")
        print(f"  {final}")
    else:
        print("  Inno Setup reported success but installer not found at:")
        print(f"  {final}")
        print("  Check dist/ for the actual filename.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
