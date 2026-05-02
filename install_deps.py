"""
AlienCore - install_deps.py
Run this ONCE as Administrator before first use:
    python install_deps.py

Installs all required Python packages and verifies system prerequisites.
Safe to re-run — skips already-installed packages.
"""

import subprocess
import sys
import os
import importlib

REQUIRED_PACKAGES = [
    # Package name (pip)       Import name to verify      Reason
    ("psutil",                 "psutil",                  "CPU/RAM/disk sensors"),
    ("pywin32",                "win32api",                "Windows service + registry"),
    ("wmi",                    "wmi",                     "Hardware WMI queries"),
    ("pystray",                "pystray",                 "System tray icon"),
    ("Pillow",                 "PIL",                     "Tray icon image rendering"),
    ("nvidia-ml-py",           "pynvml",                  "NVIDIA GPU sensors (no subprocess)"),
    ("cryptography",           "cryptography",            "Ed25519 license signature verification"),
    ("pyinstaller",            "PyInstaller",             "Optional: build standalone EXE"),
]

OPTIONAL_PACKAGES = [
    # AMD and Intel Arc GPU sensors are read through LibreHardwareMonitor
    # (bundled via lhm_bridge.exe), so no extra Python package is needed.
    # Leave this list empty — it's kept in place for future optional deps.
]


def pip_install(package_name: str) -> bool:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", package_name],
        capture_output=True, text=True
    )
    return result.returncode == 0


def check_import(import_name: str) -> bool:
    try:
        importlib.import_module(import_name)
        return True
    except ImportError:
        return False


def check_admin() -> bool:
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def main():
    print("=" * 60)
    print("  AlienCore Dependency Installer")
    print("=" * 60)

    if not check_admin():
        print("\n  WARNING: Not running as Administrator.")
        print("  Some packages (pywin32) require admin to register properly.")
        print("  Right-click install_deps.py → Run as Administrator\n")
        input("  Press Enter to continue anyway, or Ctrl+C to cancel...")

    print(f"\n  Python: {sys.version}")
    print(f"  Executable: {sys.executable}\n")

    failures = []

    print("  Installing required packages...\n")
    for pip_name, import_name, reason in REQUIRED_PACKAGES:
        already = check_import(import_name)
        if already:
            print(f"  ✓ {pip_name:<22} already installed  ({reason})")
            continue
        print(f"  ↓ {pip_name:<22} installing...       ({reason})", end="", flush=True)
        ok = pip_install(pip_name)
        if ok:
            print("  done")
        else:
            print("  FAILED")
            failures.append(pip_name)

    # pywin32 needs a post-install script on some systems
    if check_import("win32api"):
        try:
            import site
            scripts_dir = os.path.join(site.getsitepackages()[0], "Scripts")
            post_install = os.path.join(scripts_dir, "pywin32_postinstall.py")
            if os.path.exists(post_install):
                subprocess.run([sys.executable, post_install, "-install"],
                               capture_output=True)
        except Exception:
            pass

    if OPTIONAL_PACKAGES:
        print("\n  Optional packages (skip if not needed):\n")
        for pip_name, import_name, reason in OPTIONAL_PACKAGES:
            already = check_import(import_name)
            if already:
                print(f"  ✓ {pip_name:<22} already installed  ({reason})")
            else:
                ans = input(f"  ? Install {pip_name} ({reason})? [y/N]: ").strip().lower()
                if ans == "y":
                    ok = pip_install(pip_name)
                    print(f"    {'done' if ok else 'FAILED'}")
                else:
                    print("    skipped")

    print("\n" + "=" * 60)
    if failures:
        print(f"  FAILED packages: {', '.join(failures)}")
        print("  Try running as Administrator and re-running this script.")
        sys.exit(1)
    else:
        print("  All required packages installed successfully.")
        print("  Next step: python aliencore.py --firstrun")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
