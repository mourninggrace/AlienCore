"""
AlienCore - whats_new_dialog.py
Shows a "What's New in v{VERSION}" panel the first time AlienCore launches
after an update. Reads the current version's section out of CHANGELOG.md and
tracks the last-seen version in update_state.json so the dialog only fires
once per upgrade.

Fresh installs don't see this dialog — the first-run welcome covers that
experience. show_if_updated() initialises last_seen_version to the current
version on first run so the upgrade-only semantics hold.
"""

import json
import logging
import os
import re
import tkinter as tk

from core.constants import APP_NAME, BASE_DIR, VERSION

logger = logging.getLogger("aliencore.whatsnew")

_CHANGELOG_PATH = os.path.join(BASE_DIR, "CHANGELOG.md")
_STATE_PATH     = os.path.join(BASE_DIR, "update_state.json")


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def show_if_updated(is_first_run: bool = False):
    """
    Show the What's New dialog if the running version is newer than the last
    version we showed notes for. On a fresh install (is_first_run=True) just
    record the current version as seen without showing anything — the welcome
    dialog already handled introductions.
    """
    state = _load_state()
    last  = (state.get("last_seen_version") or "").strip()

    if is_first_run or not last:
        _mark_seen(state)
        return

    if _version_tuple(last) >= _version_tuple(VERSION):
        return   # user already saw notes for this version (or a newer one)

    notes = _load_notes_for_version(VERSION)
    if not notes:
        # No notes section for this version — still mark seen so we don't
        # keep trying on every launch.
        _mark_seen(state)
        return

    _show_dialog(VERSION, notes, previous=last)
    _mark_seen(state)


def mark_current_seen():
    """Record the current version as seen (used by the first-run flow)."""
    _mark_seen(_load_state())


# ─────────────────────────────────────────────────────────────────────────────
# Dialog UI
# ─────────────────────────────────────────────────────────────────────────────

BG      = "#141422"
BG_CARD = "#1c1c30"
FG      = "#E0E0E0"
FG_DIM  = "#8080A0"
FG_HEAD = "#FFFFFF"
ACCENT  = "#00CC66"
ACCENT2 = "#0099FF"
SEP     = "#2A2A3E"


def _show_dialog(version: str, notes: str, previous: str):
    root = tk.Tk()
    root.title(f"{APP_NAME}  —  What's New")
    root.configure(bg=BG)
    root.resizable(False, False)
    try:
        from gui.tray import set_window_icon
        set_window_icon(root)
    except Exception:
        pass

    # ── Header ────────────────────────────────────────────────────────────────
    hdr = tk.Frame(root, bg=BG, padx=28, pady=18)
    hdr.pack(fill="x")
    tk.Label(hdr, text=f"What's New in {APP_NAME} v{version}",
             font=("Segoe UI", 15, "bold"), bg=BG, fg=FG_HEAD
             ).pack(anchor="w")
    tk.Label(hdr, text=f"Updated from v{previous}",
             font=("Segoe UI", 9), bg=BG, fg=FG_DIM
             ).pack(anchor="w", pady=(4, 0))

    tk.Frame(root, bg=SEP, height=1).pack(fill="x")

    # ── Notes card ────────────────────────────────────────────────────────────
    card = tk.Frame(root, bg=BG_CARD, padx=18, pady=14)
    card.pack(fill="both", expand=True, padx=16, pady=(14, 0))

    txt = tk.Text(
        card, width=68, height=18,
        bg=BG_CARD, fg=FG, relief="flat",
        font=("Consolas", 9), wrap="word",
        borderwidth=0, highlightthickness=0,
        padx=2, pady=2, cursor="arrow",
    )
    txt.insert("1.0", notes)
    txt.config(state="disabled")
    txt.pack(fill="both", expand=True)

    tk.Frame(root, bg=SEP, height=1).pack(fill="x", pady=(14, 0))

    # ── Footer ────────────────────────────────────────────────────────────────
    foot = tk.Frame(root, bg=BG, padx=24, pady=14)
    foot.pack(fill="x")

    def _close():
        try:
            root.destroy()
        except Exception:
            pass

    tk.Label(foot,
             text="Full history in CHANGELOG.md",
             font=("Segoe UI", 8, "italic"),
             bg=BG, fg=FG_DIM).pack(side="left")

    tk.Button(
        foot, text="  Got it  ",
        command=_close,
        bg=ACCENT, fg="#000000",
        font=("Segoe UI", 10, "bold"),
        relief="flat", padx=16, pady=7, cursor="hand2",
        activebackground="#00AA55", activeforeground="#000000",
    ).pack(side="right")

    # Center
    root.update_idletasks()
    w  = root.winfo_reqwidth()
    h  = root.winfo_reqheight()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
    root.attributes("-topmost", True)
    root.after(200, lambda: root.attributes("-topmost", False))
    root.protocol("WM_DELETE_WINDOW", _close)
    root.mainloop()


# ─────────────────────────────────────────────────────────────────────────────
# Changelog parser
# ─────────────────────────────────────────────────────────────────────────────

# Matches:  ## [1.0.0] — 2026-04-21   or   ## [1.0.0]
_HEADER_RE = re.compile(r"^##\s*\[(?P<ver>[\d.]+)\](?:\s*[—\-–:]\s*(?P<date>.+))?\s*$")


def _load_notes_for_version(version: str) -> str:
    """Return the CHANGELOG.md section body for the given version (empty if missing)."""
    try:
        with open(_CHANGELOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        logger.debug("Could not read CHANGELOG.md: %s", e)
        return ""

    capturing = False
    buf: list[str] = []
    for line in lines:
        m = _HEADER_RE.match(line.rstrip())
        if m:
            if capturing:
                break   # next version header → stop
            if m.group("ver") == version:
                capturing = True
                continue
        if capturing:
            buf.append(line.rstrip())

    # Strip leading/trailing blank lines and reflow tight
    while buf and not buf[0].strip():
        buf.pop(0)
    while buf and not buf[-1].strip():
        buf.pop()
    return "\n".join(buf)


# ─────────────────────────────────────────────────────────────────────────────
# Version tracking (shared state file with updater.py)
# ─────────────────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict):
    try:
        with open(_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.debug("Failed to save update state: %s", e)


def _mark_seen(state: dict):
    state = dict(state)
    state["last_seen_version"] = VERSION
    _save_state(state)


def _version_tuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in str(v).split("."))
    except (ValueError, AttributeError):
        return (0,)
