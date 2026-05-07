"""
AlienCore - paywall_dialog.py
Hard-lock paywall shown when the user is signed in but their 30-day trial
has expired and they haven't purchased a base license.

Replaces normal AlienCore startup entirely until the user either:
  • Purchases the AlienCore base license ($19.99) — dialog closes, app
    continues into normal startup.
  • Signs out — dialog closes, app exits; next launch shows the login
    dialog so they can sign in under a different (paid) email.
  • Quits — dialog closes, app exits; next launch re-shows this paywall
    until one of the above happens.

The Pro $4.99 add-on is rendered as a locked card — purchasable only after
base is owned, which by definition cannot happen from inside this paywall.

Subprocessed via aliencore.py --paywall for the same Tk-isolation reasons
as login_dialog: a tk.Tk() created and destroyed in the main process before
spawning gui/bar.py and gui/tray.py crashes the Tcl C runtime on Windows.
"""

import threading
import tkinter as tk
import urllib.parse
import webbrowser

from core import auth
from core.constants import (
    APP_NAME, VERSION,
    PAYPAL_BUSINESS_EMAIL, PAYPAL_CHECKOUT_URL, BACKEND_URL,
)


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def show(on_complete=None):
    """
    Show the paywall dialog and block until the user resolves it.
    on_complete(result: str) is called with one of:
      'purchased'  — base license activated; continue startup
      'signed_out' — user clicked Sign Out
      'yubikey'    — dev YubiKey unlock fired
      'quit'       — user clicked Quit or closed the window
    """
    root = tk.Tk()
    root.title(f"{APP_NAME}  —  Trial Expired")
    root.configure(bg="#111111")
    root.resizable(False, False)
    from gui.tray import set_window_icon
    set_window_icon(root)

    _PaywallDialog(root, on_complete)

    root.update_idletasks()
    w, h = 520, 660
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
    root.mainloop()


# ─────────────────────────────────────────────────────────────────────────────
# Dialog class
# ─────────────────────────────────────────────────────────────────────────────

class _PaywallDialog:
    BG       = "#111111"
    BG_CARD  = "#1a1a1a"
    BG_LOCK  = "#161616"
    FG       = "#e8e8e8"
    FG_DIM   = "#777777"
    FG_LOCK  = "#555555"
    ACCENT   = "#00aaff"
    ACCENT2  = "#00cc66"
    PRO      = "#cc44ff"
    WARN     = "#ffaa00"
    DANGER   = "#ff4444"
    BTN_BG   = "#2a2a2a"
    BTN_HOV  = "#383838"

    def __init__(self, root: tk.Tk, on_complete):
        self.root          = root
        self.on_complete   = on_complete
        self.result        = "quit"   # default if window is closed
        self._yubikey_stop = False
        self._poll_gen     = 0        # generation counter — newer Buy click
                                      # invalidates older poll workers

        self._build()
        self._start_yubikey_poll()
        self.root.protocol("WM_DELETE_WINDOW", self._on_quit)

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self):
        BG, BG_CARD, BG_LOCK = self.BG, self.BG_CARD, self.BG_LOCK
        FG, FG_DIM, FG_LOCK  = self.FG, self.FG_DIM, self.FG_LOCK
        ACCENT, ACCENT2, PRO = self.ACCENT, self.ACCENT2, self.PRO

        outer = tk.Frame(self.root, bg=BG, padx=28, pady=22)
        outer.pack(fill="both", expand=True)

        tk.Label(outer, text=APP_NAME, font=("Segoe UI", 28, "bold"),
                 bg=BG, fg=ACCENT).pack(anchor="w")
        tk.Label(outer, text=f"v{VERSION}   ·   System Optimizer",
                 font=("Segoe UI", 9), bg=BG, fg=FG_DIM
                 ).pack(anchor="w", pady=(0, 14))
        tk.Frame(outer, bg="#2a2a2a", height=1).pack(fill="x", pady=(0, 16))

        # Trial-expired banner
        banner = tk.Frame(outer, bg=BG_CARD, padx=20, pady=14)
        banner.pack(fill="x")
        tk.Label(banner, text="Your 30-day free trial has expired",
                 font=("Segoe UI", 13, "bold"),
                 bg=BG_CARD, fg=self.WARN).pack(anchor="w")
        email = auth.get_email() or "(unknown)"
        tk.Label(banner, text=f"Signed in as  {email}",
                 font=("Segoe UI", 9), bg=BG_CARD, fg=FG_DIM
                 ).pack(anchor="w", pady=(2, 8))
        tk.Label(banner,
                 text=("Purchase the AlienCore lifetime license below to "
                       "continue using the app. AlienCore is unavailable "
                       "until activation completes."),
                 font=("Segoe UI", 9), bg=BG_CARD, fg=FG,
                 wraplength=440, justify="left").pack(anchor="w")

        # ── Base license card ────────────────────────────────────────────────
        base = tk.Frame(outer, bg=BG_CARD, padx=22, pady=16)
        base.pack(fill="x", pady=(14, 0))

        head = tk.Frame(base, bg=BG_CARD)
        head.pack(fill="x")
        tk.Label(head, text=" BASE ", font=("Segoe UI", 8, "bold"),
                 bg=ACCENT2, fg="#000000", padx=4, pady=1
                 ).pack(side="left")
        tk.Label(head, text="  AlienCore  —  Lifetime License",
                 font=("Segoe UI", 12, "bold"),
                 bg=BG_CARD, fg=FG).pack(side="left")

        tk.Label(base, text="$19.99  ·  one-time payment",
                 font=("Segoe UI", 10), bg=BG_CARD, fg=ACCENT2
                 ).pack(anchor="w", pady=(4, 8))

        tk.Label(base,
                 text=("• Adaptive CPU / GPU / RAM tuning\n"
                       "• All advanced optimization panels\n"
                       "• Lifetime license — every future update included"),
                 font=("Segoe UI", 9), bg=BG_CARD, fg=FG_DIM,
                 justify="left").pack(anchor="w", pady=(0, 12))

        self.buy_btn = self._btn(base, "Purchase AlienCore  →",
                                 self._buy_base, ACCENT2, bold=True)
        self.buy_btn.pack(side="left")

        # ── Pro add-on card (locked) ────────────────────────────────────────
        pro = tk.Frame(outer, bg=BG_LOCK, padx=22, pady=14)
        pro.pack(fill="x", pady=(10, 0))

        prohead = tk.Frame(pro, bg=BG_LOCK)
        prohead.pack(fill="x")
        tk.Label(prohead, text=" PRO ", font=("Segoe UI", 8, "bold"),
                 bg=PRO, fg="#000000", padx=4, pady=1
                 ).pack(side="left")
        tk.Label(prohead, text="  AlienCore Pro Add-on",
                 font=("Segoe UI", 11, "bold"),
                 bg=BG_LOCK, fg=FG_LOCK).pack(side="left")

        tk.Label(pro, text="+$4.99  ·  AI chat, watchdog, advisor",
                 font=("Segoe UI", 9), bg=BG_LOCK, fg=FG_LOCK
                 ).pack(anchor="w", pady=(2, 8))

        tk.Label(pro,
                 text="Requires the base license — purchase AlienCore "
                      "above first.",
                 font=("Segoe UI", 9, "italic"),
                 bg=BG_LOCK, fg=FG_LOCK,
                 wraplength=440, justify="left").pack(anchor="w", pady=(0, 8))

        locked_btn = tk.Label(pro, text="  Locked  ✕  ",
                              font=("Segoe UI", 10, "bold"),
                              bg=self.BTN_BG, fg=FG_LOCK,
                              padx=14, pady=7)
        locked_btn.pack(side="left")

        # ── Status feed ─────────────────────────────────────────────────────
        self.status_var = tk.StringVar(value="")
        self.status_lbl = tk.Label(outer, textvariable=self.status_var,
                                   font=("Segoe UI", 9, "italic"),
                                   bg=BG, fg=FG_DIM,
                                   wraplength=460, justify="left")
        self.status_lbl.pack(anchor="w", pady=(14, 0))

        # ── Footer: Sign Out + Quit ─────────────────────────────────────────
        footer = tk.Frame(outer, bg=BG)
        footer.pack(fill="x", pady=(18, 0))
        tk.Frame(footer, bg="#2a2a2a", height=1).pack(fill="x", pady=(0, 12))

        self._btn(footer, "Sign Out", self._sign_out, FG_DIM
                  ).pack(side="left")
        self._btn(footer, "Quit", self._on_quit, FG_DIM
                  ).pack(side="right")

        tk.Label(outer,
                 text="Already paid? Click Refresh after a moment, or sign "
                      "out and back in with the email you used at checkout.",
                 font=("Segoe UI", 8), bg=BG, fg=FG_DIM,
                 wraplength=460, justify="left").pack(anchor="w", pady=(14, 4))

        self.refresh_btn = self._btn(outer, "Refresh License",
                                     self._manual_refresh, FG_DIM)
        self.refresh_btn.pack(anchor="w")

    # ── Actions ───────────────────────────────────────────────────────────────

    def _buy_base(self):
        email = auth.get_email()
        if not email:
            self._status("No email on file — sign out and sign in again.",
                         self.DANGER)
            return
        params = urllib.parse.urlencode({
            "cmd":           "_xclick",
            "business":      PAYPAL_BUSINESS_EMAIL,
            "item_name":     "AlienCore — Lifetime License",
            "item_number":   "AC_BASE",
            "amount":        "19.99",
            "currency_code": "USD",
            "custom":        email,
            "notify_url":    BACKEND_URL.rstrip("/") + "/paypal/ipn",
        })
        try:
            webbrowser.open(f"{PAYPAL_CHECKOUT_URL}?{params}")
        except Exception as e:
            self._status(f"Could not open PayPal: {e}", self.DANGER)
            return
        self._status(
            "Payment window opened in your browser. Waiting for PayPal "
            "confirmation — usually 10-30 seconds after checkout completes…",
            self.ACCENT,
        )
        self._poll_gen += 1
        my_gen = self._poll_gen
        threading.Thread(target=self._poll_for_activation,
                         args=(my_gen,), daemon=True,
                         name="PaywallPurchasePoll").start()

    def _poll_for_activation(self, my_gen: int):
        """Poll /auth/check every 3 s for up to 3 min waiting for has_base
        to flip True (PayPal IPN landed → server granted license)."""
        import time as _t
        for _ in range(60):
            if my_gen != self._poll_gen or self._yubikey_stop:
                return
            _t.sleep(3)
            try:
                auth.refresh_license()
            except Exception:
                pass
            if auth.is_licensed():
                self.root.after(0, lambda: self._activated(my_gen))
                return
        self.root.after(0, lambda: self._poll_timeout(my_gen))

    def _activated(self, my_gen: int):
        if my_gen != self._poll_gen:
            return
        self.result = "purchased"
        self._status("License activated — thanks for supporting AlienCore!",
                     self.ACCENT2)
        self.root.after(900, self._finish)

    def _poll_timeout(self, my_gen: int):
        if my_gen != self._poll_gen:
            return
        self._status(
            "No PayPal confirmation received after 3 minutes. Click "
            "Refresh License once your receipt arrives — or contact "
            "support if the license still doesn't appear.",
            self.WARN,
        )

    def _manual_refresh(self):
        self.refresh_btn.config(state="disabled", text="Refreshing…")
        def _work():
            try:
                changed, msg = auth.refresh_license()
            except Exception as e:
                changed, msg = False, f"Error: {e}"
            def _ui():
                self.refresh_btn.config(state="normal", text="Refresh License")
                if auth.is_licensed():
                    self.result = "purchased"
                    self._status("License found — activating…", self.ACCENT2)
                    self.root.after(700, self._finish)
                else:
                    self._status(msg or "No license on file yet.", self.WARN)
            self.root.after(0, _ui)
        threading.Thread(target=_work, daemon=True,
                         name="PaywallManualRefresh").start()

    def _sign_out(self):
        try:
            auth.logout()
        except Exception:
            pass
        self.result = "signed_out"
        self._finish()

    def _on_quit(self):
        self.result = "quit"
        self._finish()

    def _finish(self):
        self._yubikey_stop = True
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        if self.on_complete:
            self.on_complete(self.result)

    # ── YubiKey dev-unlock polling ────────────────────────────────────────────
    # Same pattern as login_dialog: dev YubiKey plugged in at any point during
    # the dialog activates the in-memory dev session and lets startup
    # continue without paying.

    def _start_yubikey_poll(self):
        threading.Thread(target=self._yubikey_worker, daemon=True,
                         name="PaywallYubiKeyPoll").start()

    def _yubikey_worker(self):
        import time as _t
        while not self._yubikey_stop:
            try:
                if auth.try_dev_unlock():
                    self.root.after(0, self._yubikey_unlocked)
                    return
            except Exception:
                pass
            for _ in range(40):
                if self._yubikey_stop:
                    return
                _t.sleep(0.1)

    def _yubikey_unlocked(self):
        self.result = "yubikey"
        self._status("Developer YubiKey detected — unlocking…", self.ACCENT2)
        self.root.after(600, self._finish)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _status(self, msg: str, color: str = None):
        self.status_var.set(msg)
        if color:
            self.status_lbl.config(fg=color)

    def _btn(self, parent, text, cmd, color, bold=False):
        return tk.Button(
            parent, text=text, command=cmd,
            font=("Segoe UI", 10, "bold" if bold else "normal"),
            fg=color, bg=self.BTN_BG,
            activeforeground=color, activebackground=self.BTN_HOV,
            relief="flat", padx=14, pady=7,
            cursor="hand2", bd=0, highlightthickness=0,
        )
