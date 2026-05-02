"""
AlienCore - update_dialog.py
Update notification dialog with three choices:
  • Update Now      — downloads and applies immediately, then restarts.
  • Not Now         — dismisses dialog; "Update Available" button appears in Settings.
  • Remind Me Later — suppresses dialog for 24 hours.

Call show(info, parent) from any thread; the dialog creates its own Tk root if
parent is None (standalone) or opens as a Toplevel on the given parent.
"""

import threading
import tkinter as tk
from tkinter import ttk

from core.constants import APP_NAME, VERSION

BG      = "#1A1A2E"
BG_CARD = "#16213E"
BG_BTN  = "#0D0D1A"
FG      = "#E0E0E0"
FG_DIM  = "#707080"
FG_HEAD = "#FFFFFF"
ACCENT  = "#00CC66"
ACCENT2 = "#0099FF"
WARN    = "#FFAA00"
DANGER  = "#FF3333"
SEP     = "#2A2A3E"


def show(info: dict, parent: tk.Misc = None) -> str:
    """
    Display the update dialog and block until the user makes a choice.

    Returns one of: "update_now", "not_now", "remind_later".
    info keys: version, notes, zipball_url, html_url
    """
    result = ["remind_later"]

    standalone = parent is None
    if standalone:
        root = tk.Tk()
        root.withdraw()
        dlg = tk.Toplevel(root)
    else:
        dlg = tk.Toplevel(parent)

    dlg.title(f"{APP_NAME}  —  Update Available")
    dlg.configure(bg=BG)
    dlg.resizable(False, False)
    dlg.attributes("-topmost", True)
    dlg.lift()
    dlg.focus_force()

    # ── Header ────────────────────────────────────────────────────────────────
    hdr = tk.Frame(dlg, bg=BG, padx=28, pady=18)
    hdr.pack(fill="x")
    tk.Label(hdr, text=f"  Update Available",
             font=("Segoe UI", 15, "bold"), bg=BG, fg=FG_HEAD).pack(anchor="w")
    tk.Label(hdr,
             text=f"  v{VERSION}  \u2192  v{info['version']}",
             font=("Segoe UI", 10), bg=BG, fg=FG_DIM).pack(anchor="w", pady=(4, 0))

    tk.Frame(dlg, bg=SEP, height=1).pack(fill="x")

    # ── Release notes card ────────────────────────────────────────────────────
    notes = (info.get("notes") or "").strip()
    if notes:
        card = tk.Frame(dlg, bg=BG_CARD, padx=20, pady=14)
        card.pack(fill="x", padx=16, pady=(14, 0))
        tk.Label(card, text="What's new",
                 font=("Segoe UI", 9, "bold"), bg=BG_CARD, fg=FG_HEAD
                 ).pack(anchor="w", pady=(0, 6))
        txt = tk.Text(card, height=7, width=58,
                      bg=BG_CARD, fg=FG, relief="flat",
                      font=("Consolas", 8), wrap="word",
                      state="normal", cursor="arrow", padx=0, pady=0,
                      borderwidth=0, highlightthickness=0)
        txt.insert("1.0", notes)
        txt.config(state="disabled")
        txt.pack(fill="x")

    # ── Progress area (hidden until update starts) ────────────────────────────
    prog_outer = tk.Frame(dlg, bg=BG, padx=16)
    prog_outer.pack(fill="x", pady=(12, 0))
    prog_var    = tk.IntVar(value=0)
    prog_msg    = tk.StringVar(value="")
    prog_bar    = ttk.Progressbar(prog_outer, variable=prog_var,
                                  maximum=100, length=460)
    prog_lbl    = tk.Label(prog_outer, textvariable=prog_msg,
                           font=("Segoe UI", 8, "italic"),
                           bg=BG, fg=FG_DIM, anchor="w")

    tk.Frame(dlg, bg=SEP, height=1).pack(fill="x", pady=(14, 0))

    # ── Footer buttons ────────────────────────────────────────────────────────
    foot = tk.Frame(dlg, bg=BG, padx=24, pady=16)
    foot.pack(fill="x")

    all_btns = []

    def _disable_buttons():
        for b in all_btns:
            try:
                b.config(state="disabled")
            except Exception:
                pass

    def _update_now():
        result[0] = "update_now"
        # Installer-distributed (PyInstaller-frozen) builds can't apply an
        # in-place update — the install dir doesn't contain loose .py files
        # for the updater to overlay.  Direct the user to download a new
        # installer from the GitHub releases page instead.
        from core import updater
        if updater.is_frozen():
            import webbrowser
            try:
                webbrowser.open(updater.RELEASES_URL, new=2)
            except Exception:
                pass
            updater.set_dismissed(info["version"])
            dlg.destroy()
            return

        _disable_buttons()
        prog_bar.pack(fill="x", pady=(0, 4))
        prog_lbl.pack(fill="x")

        def _on_progress(pct: int, msg: str):
            if dlg.winfo_exists():
                dlg.after(0, lambda: (prog_var.set(pct), prog_msg.set(msg)))

        def _worker():
            try:
                updater.download_and_apply(
                    info["zipball_url"],
                    on_progress=_on_progress,
                    expected_sha256=info.get("sha256"),
                    expected_version=info.get("version"))
            except Exception as exc:
                err = str(exc)
                if dlg.winfo_exists():
                    dlg.after(0, lambda: (
                        prog_msg.set(f"Update failed: {err}"),
                        prog_var.set(0),
                        [b.config(state="normal") for b in all_btns],
                    ))

        threading.Thread(target=_worker, daemon=True).start()

    def _not_now():
        result[0] = "not_now"
        from core import updater
        updater.set_dismissed(info["version"])
        dlg.destroy()

    def _remind_later():
        result[0] = "remind_later"
        from core import updater
        updater.set_remind_later()
        dlg.destroy()

    # Frozen-build installs can't auto-apply updates; the button instead
    # opens the GitHub releases page so the user can download a new installer.
    from core import updater as _updater_mod
    update_btn_label = ("  Download New Installer  " if _updater_mod.is_frozen()
                        else "  Update Now  ")

    btn_update = tk.Button(
        foot, text=update_btn_label,
        command=_update_now,
        bg=ACCENT, fg="#000000",
        font=("Segoe UI", 10, "bold"),
        relief="flat", padx=12, pady=7, cursor="hand2",
        activebackground="#00AA55", activeforeground="#000000",
    )
    btn_not_now = tk.Button(
        foot, text="Not Now",
        command=_not_now,
        bg=BG_BTN, fg=FG_DIM,
        font=("Segoe UI", 9), relief="flat",
        padx=10, pady=7, cursor="hand2",
        activebackground=BG_CARD, activeforeground=FG,
    )
    btn_remind = tk.Button(
        foot, text="Remind Me in 24h",
        command=_remind_later,
        bg=BG, fg=ACCENT2,
        font=("Segoe UI", 9), relief="flat",
        padx=10, pady=7, cursor="hand2",
        activebackground=BG_CARD, activeforeground=ACCENT2,
    )

    btn_update.pack(side="left")
    btn_not_now.pack(side="left", padx=(10, 0))
    btn_remind.pack(side="right")
    all_btns.extend([btn_update, btn_not_now, btn_remind])

    # Center on screen
    dlg.update_idletasks()
    w  = dlg.winfo_reqwidth()
    h  = dlg.winfo_reqheight()
    sw = dlg.winfo_screenwidth()
    sh = dlg.winfo_screenheight()
    dlg.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    dlg.protocol("WM_DELETE_WINDOW", _remind_later)
    dlg.grab_set()
    dlg.wait_window()

    if standalone:
        try:
            root.destroy()
        except Exception:
            pass

    return result[0]


def show_standalone(info: dict):
    """Convenience wrapper — creates its own Tk root. Safe to call from any thread."""
    show(info, parent=None)
