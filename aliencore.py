"""
AlienCore - aliencore.py
Main entry point.

Usage:
  python aliencore.py --firstrun     Show first-run settings GUI then start
  python aliencore.py --settings     Open settings GUI only (service stays running)
  python aliencore.py --dryrun       Show what tweaks WOULD be applied, touch nothing
  python aliencore.py --install      Install as Windows service (run as Admin)
  python aliencore.py --uninstall    Remove Windows service (run as Admin)
  python aliencore.py --restore      Restore all system defaults and exit
  python aliencore.py                Run normally (foreground, for testing)
"""
# copykitten

import sys
import os
import argparse
import logging
import threading
import ctypes

# ── Bootstrap paths so all imports work regardless of CWD ────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from core import logger as log_setup
from core import config_manager as cfg
from core import hardware, sensors, tweaks, profiles, monitor
from core import elevation
from core.constants import APP_NAME, VERSION, LOG_PATH


# ─────────────────────────────────────────────────────────────────────────────
# Single-instance mutex
# ─────────────────────────────────────────────────────────────────────────────

_MUTEX_NAME = "AlienCore_SingleInstance_v1"
_mutex_handle = None   # kept alive for the lifetime of the process

def _acquire_single_instance_lock() -> bool:
    """
    Acquire a named Windows mutex.  Returns True if this is the only running
    instance; False if another process already holds the mutex.

    The handle is stored in _mutex_handle so it stays open (and the mutex
    stays owned) until the process exits, at which point Windows releases it
    automatically.
    """
    global _mutex_handle
    ERROR_ALREADY_EXISTS = 183
    handle = ctypes.windll.kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    err    = ctypes.windll.kernel32.GetLastError()
    if err == ERROR_ALREADY_EXISTS:
        ctypes.windll.kernel32.CloseHandle(handle)
        return False
    _mutex_handle = handle   # keep open — released on process exit
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    args = _parse_args()

    # Always load config first
    cfg.load()
    c = cfg.get()

    # Set up logging
    log_setup.setup(
        log_enabled=c["service"]["log_enabled"],
    )
    root_logger = logging.getLogger("aliencore")
    root_logger.info("=" * 60)
    root_logger.info("AlienCore v%s starting  (pid=%d, admin=%s)",
                     VERSION, os.getpid(), elevation.is_admin())
    root_logger.info("=" * 60)

    # ── Auto-elevate for main/firstrun runs ──────────────────────────────────
    # LHM (CPU temps via WinRing0 MSR), SMBus DIMM temps, and AWCC WMI all
    # require administrator rights.  Without them the sensor bar shows '---'.
    # Only main + firstrun need elevation; --settings is a subprocess that
    # inherits the parent's token, --dryrun/--restore/--install/--uninstall
    # have their own privilege semantics.
    if _should_auto_elevate(args) and not elevation.is_admin():
        root_logger.warning("Not running as admin — requesting UAC elevation "
                            "(pass --no-elevate to skip).")
        if elevation.relaunch_as_admin():
            sys.exit(0)   # elevated copy takes over; this instance exits
        root_logger.warning("UAC declined or failed — continuing without admin. "
                            "CPU temp, DIMM, NVMe sensors may show '---'.")

    # ── Service install / uninstall ───────────────────────────────────────────
    if args.install:
        _install_service()
        return
    if args.uninstall:
        _uninstall_service()
        return

    # ── Settings only ─────────────────────────────────────────────────────────
    if args.settings:
        cfg.load()
        from core import auth as _auth
        _auth.load_session()
        # Load cached hardware profile so boost_tracker has correct thresholds
        hw = hardware.build_profile(force_refresh=False)
        from core import boost_tracker as _bt
        _bt.configure(max_freq_mhz=hw.get("cpu", {}).get("max_freq_mhz", 0))
        # Start sensor thread so live panels (GPU boost, DIMM temps) have real data
        sensors.start()
        from gui.settings_gui import open_settings
        open_settings(is_first_run=False)
        sensors.stop()
        from core import lhm_manager
        lhm_manager.stop()
        return

    # ── AI chat only (subprocess) ────────────────────────────────────────────
    # Running the chat as its own process keeps it out of the main Tk
    # interpreter.  When the chat lived on a background thread with a second
    # tk.Tk() root, the sensor bar shrank and misbehaved any time the chat
    # window was open — two Tk roots in one process fight over geometry and
    # z-order.  Subprocess isolation makes the issue structurally impossible.
    if args.ai_chat:
        cfg.load()
        # The chat needs live sensor readings to build its system-context
        # snapshot; start sensors but skip the heavier monitor / tweaks stack.
        sensors.start()
        try:
            from gui.ai_chat import open_chat
            open_chat()
        finally:
            sensors.stop()
            from core import lhm_manager
            lhm_manager.stop()
        return

    # ── Restore defaults ──────────────────────────────────────────────────────
    if args.restore:
        tweaks.restore_defaults(dry_run=False)
        print("System defaults restored.")
        return

    # ── Dry run ───────────────────────────────────────────────────────────────
    if args.dryrun:
        hw = hardware.build_profile(force_refresh=True)
        print("\n[DRY RUN] The following tweaks WOULD be applied:\n")
        tweaks.apply_baseline(hw, dry_run=True)
        tweaks.apply_profile("idle", hw, dry_run=True)
        print("\n[DRY RUN] Done. Nothing was changed.")
        return

    # ── Single-instance guard ─────────────────────────────────────────────────
    if not _acquire_single_instance_lock():
        root_logger.warning("AlienCore is already running — exiting duplicate instance.")
        sys.exit(0)

    # ── Normal startup ────────────────────────────────────────────────────────
    _run(firstrun=args.firstrun or cfg.is_first_run())


def _run(firstrun: bool = False):
    """Full AlienCore startup — hardware scan, tweaks, sensors, monitor, tray."""

    # 0. Auth — load cached session, show login if needed
    from core import auth as _auth
    _auth.load_session()
    if not _auth.is_logged_in():
        _show_login()
        if not _auth.is_logged_in():
            # User closed the window without signing in — exit gracefully
            logging.getLogger("aliencore").info("No session — exiting.")
            return
    # Refresh license from server in the background (non-blocking)
    _auth.refresh_session_async()

    # 1. First-run GUI (blocks until user saves or closes)
    if firstrun:
        _show_first_run_gui()

    # 2. Sync startup registry entry with config
    c = cfg.get()
    from core import startup as _startup
    _startup.sync(c["service"].get("start_with_windows", True))

    # 3. Hardware fingerprint
    force_refresh = c["service"].get("hardware_refresh_on_startup", True)
    hw = hardware.build_profile(force_refresh=force_refresh)

    # 3b. Configure boost tracker with detected CPU max frequency
    from core import boost_tracker as _bt
    _bt.configure(max_freq_mhz=hw.get("cpu", {}).get("max_freq_mhz", 0))

    # 4. Apply baseline tweaks
    tweaks.apply_baseline(hw, dry_run=False)

    # 5. Apply initial profile tweaks (start at idle)
    tweaks.apply_profile("idle", hw, dry_run=False)

    # 6. Start sensor polling
    sensors.start()

    # 7. Start AI watchdog (no-op if no API key configured)
    from core import ai_manager
    if c.get("ai", {}).get("api_key", "").strip():
        ai_manager.start_watchdog()

    # 8. Start learning engine
    from core import learning
    learning.start()

    # 9. Start monitor loop
    monitor.start(hw)

    # 10. Start sensor bar (always-on-top floating display)
    import threading as _threading
    _threading.Thread(target=_start_bar, daemon=True, name="SensorBar").start()

    # 11. Start tray icon (blocks until Exit is chosen)
    _start_tray(hw)

    # ── Shutdown ──
    logging.getLogger("aliencore").info("AlienCore shutting down.")
    sensors.stop()
    monitor.stop()
    from core import ai_manager as _ai
    _ai.stop_watchdog()
    from core import lhm_manager
    lhm_manager.stop()


# ─────────────────────────────────────────────────────────────────────────────
# First-run GUI
# ─────────────────────────────────────────────────────────────────────────────

def _show_login():
    from gui.login_dialog import show as show_login
    show_login()


def _show_first_run_gui():
    from gui.settings_gui import open_settings
    open_settings(
        on_save_callback=None,
        is_first_run=True,
    )
    cfg.reload()


# ─────────────────────────────────────────────────────────────────────────────
# Tray
# ─────────────────────────────────────────────────────────────────────────────

def _start_bar():
    """Start the always-on-top sensor bar."""
    from gui.bar import start as bar_start
    bar_start()


def _start_tray(hw: dict):
    from gui import tray

    _settings_proc = None

    def on_settings_open():
        """Launch settings GUI as a separate subprocess — always gets clean environment."""
        nonlocal _settings_proc
        import subprocess
        # Don't open a second window if one is already running
        if _settings_proc is not None and _settings_proc.poll() is None:
            return
        _settings_proc = subprocess.Popen(
            [sys.executable,
             os.path.join(BASE_DIR, "aliencore.py"),
             "--settings"],
            cwd=BASE_DIR,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def on_quit():
        nonlocal _settings_proc
        if _settings_proc is not None and _settings_proc.poll() is None:
            try:
                _settings_proc.terminate()
            except Exception:
                pass
        # Sensor bar lives on its own thread with its own Tk mainloop — if we
        # don't tear it down explicitly its mainloop keeps the interpreter
        # alive after the tray exits and the whole process hangs.
        from gui import bar
        bar.stop()
        sensors.stop()
        monitor.stop()
        from core import lhm_manager
        lhm_manager.stop()
        tray.stop()

    tray.start(on_settings_open=on_settings_open, on_quit=on_quit)


def _on_settings_saved():
    """Called after user saves settings — reload config live."""
    monitor.request_config_reload()
    logging.getLogger("aliencore").info("Settings saved — config reloaded live.")


# ─────────────────────────────────────────────────────────────────────────────
# Windows Service wrapper
# ─────────────────────────────────────────────────────────────────────────────

def _install_service():
    """Register AlienCore as a Windows service using pywin32."""
    try:
        import win32serviceutil
        python_exe = sys.executable
        script     = os.path.abspath(__file__)
        svc_name   = "AlienCoreService"
        display    = "AlienCore Adaptive Optimizer"
        description = "Adaptive system optimizer — manages CPU, GPU, and system tweaks automatically."

        # Use sc.exe for reliability
        import subprocess
        _cnw = subprocess.CREATE_NO_WINDOW
        cmd = [
            "sc", "create", svc_name,
            "binPath=",  f'"{python_exe}" "{script}"',
            "DisplayName=", display,
            "start=", "auto",
            "type=", "own",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True,
                                creationflags=_cnw)
        if result.returncode == 0:
            # Add description
            subprocess.run(["sc", "description", svc_name, description],
                           capture_output=True, creationflags=_cnw)
            print(f"Service '{svc_name}' installed successfully.")
            print("To start it now: sc start AlienCoreService")
            print("Or reboot — it will start automatically.")
        else:
            print(f"Service install failed: {result.stderr}")
            print("Make sure you're running as Administrator.")
    except Exception as e:
        print(f"Service install error: {e}")


def _uninstall_service():
    import subprocess
    _cnw = subprocess.CREATE_NO_WINDOW
    svc_name = "AlienCoreService"
    subprocess.run(["sc", "stop",   svc_name], capture_output=True,
                   creationflags=_cnw)
    result = subprocess.run(["sc", "delete", svc_name],
                            capture_output=True, text=True,
                            creationflags=_cnw)
    if result.returncode == 0:
        print(f"Service '{svc_name}' removed.")
    else:
        print(f"Remove failed: {result.stderr}")
        print("Make sure you're running as Administrator.")


# ─────────────────────────────────────────────────────────────────────────────
# Argument parser
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(
        prog="aliencore",
        description=f"AlienCore v{VERSION} — Adaptive System Optimizer"
    )
    p.add_argument("--firstrun",  action="store_true", help="Force first-run settings GUI")
    p.add_argument("--settings",  action="store_true", help="Open settings GUI only")
    p.add_argument("--ai-chat",   action="store_true", help="Open AI chat window only (subprocess)")
    p.add_argument("--dryrun",    action="store_true", help="Show tweaks without applying")
    p.add_argument("--install",   action="store_true", help="Install as Windows service (Admin)")
    p.add_argument("--uninstall", action="store_true", help="Remove Windows service (Admin)")
    p.add_argument("--restore",   action="store_true", help="Restore system defaults")
    p.add_argument("--no-elevate", action="store_true",
                   help="Skip the automatic UAC prompt at launch")
    return p.parse_args()


def _should_auto_elevate(args) -> bool:
    """
    True when the current invocation is a main/firstrun run that needs admin.
    Excludes subprocess modes, diagnostic modes, and explicit opt-outs.
    """
    if getattr(args, "no_elevate", False):
        return False
    if args.settings or args.ai_chat or args.dryrun or args.restore:
        return False
    if args.install or args.uninstall:
        return False   # sc.exe fails loudly if not admin — let the user see that
    return True


if __name__ == "__main__":
    main()
