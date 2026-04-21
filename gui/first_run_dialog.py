"""
AlienCore - first_run_dialog.py
Welcome dialog shown on the very first launch after sign-in.

Auto-runs hardware fingerprinting + system scan in the background while the
user watches, then lights up a big green "Start AlienCore" button.  Replaces
the old flow where first-time users were dropped into the 17-tab settings
window with no guidance.

The user can always open Settings later from the tray to tweak anything.
"""

import logging
import threading
import time
import tkinter as tk

from core import config_manager as cfg
from core import fingerprint
from core import hardware
from core.constants import APP_NAME, VERSION

logger = logging.getLogger("aliencore.firstrun")


def show():
    """Block until the user clicks Start (or closes the window)."""
    root = tk.Tk()
    root.title(f"{APP_NAME}  —  Welcome")
    root.configure(bg="#111111")
    root.resizable(False, False)
    try:
        from gui.tray import set_window_icon
        set_window_icon(root)
    except Exception:
        pass

    _FirstRunDialog(root)

    root.update_idletasks()
    w, h = 540, 560
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
    root.mainloop()


class _FirstRunDialog:
    BG      = "#111111"
    BG_CARD = "#1a1a1a"
    FG      = "#e8e8e8"
    FG_DIM  = "#777777"
    ACCENT  = "#00aaff"
    GREEN   = "#00cc66"
    PENDING = "#444444"

    def __init__(self, root: tk.Tk):
        self.root        = root
        self._done       = False
        self._steps: list[tuple[tk.Label, tk.Label]] = []
        self._pulse_idx  = 0
        self._build()
        # Give the window a beat to settle before the scan starts
        self.root.after(250, self._start_work)

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self):
        outer = tk.Frame(self.root, bg=self.BG, padx=32, pady=26)
        outer.pack(fill="both", expand=True)

        tk.Label(outer, text=f"Welcome to {APP_NAME}",
                 font=("Segoe UI", 22, "bold"),
                 bg=self.BG, fg=self.ACCENT).pack(anchor="w")
        tk.Label(outer,
                 text=f"v{VERSION}   ·   Let's get your system set up",
                 font=("Segoe UI", 10),
                 bg=self.BG, fg=self.FG_DIM).pack(anchor="w", pady=(0, 6))
        tk.Frame(outer, bg="#2a2a2a", height=1).pack(fill="x", pady=(6, 16))

        tk.Label(outer,
                 text="AlienCore is scanning your hardware — this only happens "
                      "once.  Sit tight for a few seconds.",
                 font=("Segoe UI", 9), bg=self.BG, fg=self.FG_DIM,
                 wraplength=440, justify="left").pack(anchor="w", pady=(0, 14))

        card = tk.Frame(outer, bg=self.BG_CARD, padx=22, pady=18)
        card.pack(fill="x")

        tk.Label(card, text="Initializing",
                 font=("Segoe UI", 12, "bold"),
                 bg=self.BG_CARD, fg=self.FG).pack(anchor="w", pady=(0, 10))

        self._steps_frame = tk.Frame(card, bg=self.BG_CARD)
        self._steps_frame.pack(fill="x")

        self._btn_frame = tk.Frame(outer, bg=self.BG)
        self._btn_frame.pack(fill="x", pady=(24, 0))

        self._start_btn = tk.Button(
            self._btn_frame, text="   Start AlienCore   →   ",
            font=("Segoe UI", 14, "bold"),
            fg="#0d1a0d", bg=self.GREEN,
            activeforeground="#0d1a0d", activebackground="#00dd77",
            relief="flat", padx=22, pady=12,
            cursor="hand2", bd=0, highlightthickness=0,
            command=self._finish,
        )
        # Not packed yet — appears once the scan completes.

        tk.Label(outer,
                 text="You can tweak anything later from the system tray  →  Settings",
                 font=("Segoe UI", 9),
                 bg=self.BG, fg=self.FG_DIM).pack(pady=(22, 0))

    def _add_step(self, label: str) -> int:
        row = tk.Frame(self._steps_frame, bg=self.BG_CARD)
        row.pack(fill="x", pady=3)
        status = tk.Label(row, text="◌", font=("Segoe UI", 13),
                          bg=self.BG_CARD, fg=self.PENDING, width=2)
        status.pack(side="left")
        name = tk.Label(row, text=label, font=("Segoe UI", 10),
                        bg=self.BG_CARD, fg=self.FG_DIM, anchor="w")
        name.pack(side="left", fill="x", expand=True)
        self._steps.append((status, name))
        return len(self._steps) - 1

    def _mark_active(self, idx: int):
        if not self._alive():
            return
        status, name = self._steps[idx]
        status.config(text="●", fg=self.ACCENT)
        name.config(fg=self.FG)

    def _mark_done(self, idx: int):
        if not self._alive():
            return
        status, name = self._steps[idx]
        status.config(text="✓", fg=self.GREEN)
        name.config(fg=self.FG_DIM)

    def _alive(self) -> bool:
        try:
            return bool(self.root.winfo_exists())
        except Exception:
            return False

    # ── Work ──────────────────────────────────────────────────────────────────

    def _start_work(self):
        if not self._alive():
            return
        i_fp  = self._add_step("Reading hardware fingerprint")
        i_hw  = self._add_step("Scanning CPU / GPU / RAM / storage")
        i_prof= self._add_step("Building your system profile")

        def _ui(fn, *args):
            if self._alive():
                self.root.after(0, lambda: fn(*args))

        def _work():
            _ui(self._mark_active, i_fp)
            try:
                fingerprint.get()
            except Exception as e:
                logger.debug("Fingerprint failed: %s", e)
            time.sleep(0.4)
            _ui(self._mark_done, i_fp)

            _ui(self._mark_active, i_hw)
            try:
                hardware.build_profile(force_refresh=True)
            except Exception as e:
                logger.warning("Hardware scan failed: %s", e)
            _ui(self._mark_done, i_hw)

            _ui(self._mark_active, i_prof)
            time.sleep(0.4)
            _ui(self._mark_done, i_prof)

            if self._alive():
                self.root.after(250, self._reveal_button)

        threading.Thread(target=_work, daemon=True,
                         name="FirstRunScan").start()

    def _reveal_button(self):
        if not self._alive():
            return
        self._start_btn.pack(anchor="center")
        self._start_btn.focus_set()
        self.root.bind("<Return>", lambda e: self._finish())
        self._pulse()

    def _pulse(self):
        if self._done or not self._alive():
            return
        palette = [self.GREEN, "#00dd77", "#22ee88", "#00dd77"]
        self._pulse_idx = (self._pulse_idx + 1) % len(palette)
        try:
            self._start_btn.config(bg=palette[self._pulse_idx])
        except Exception:
            return
        self.root.after(550, self._pulse)

    def _finish(self):
        if self._done:
            return
        self._done = True
        try:
            cfg.set_value("first_run_complete", value=True)
        except Exception as e:
            logger.warning("Could not persist first_run_complete: %s", e)
        try:
            self.root.destroy()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Standalone entry point — launched by Settings > Service > Re-run Setup.
# Running as a subprocess keeps the Tk root isolated from any settings window
# the user has open, and lets the scan finish cleanly before they restart.
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-8s  %(message)s")
    # Reset caches so the scan actually re-detects new hardware
    try:
        import os
        from core.constants import HARDWARE_CACHE
        if os.path.exists(HARDWARE_CACHE):
            os.remove(HARDWARE_CACHE)
    except Exception as e:
        logger.warning("Could not clear hardware cache: %s", e)
    fingerprint._CACHE = None  # force re-hash so a CPU swap is picked up
    show()

