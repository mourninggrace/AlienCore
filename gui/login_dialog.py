"""
AlienCore - login_dialog.py
Email + PIN authentication dialog.

Shown when AlienCore starts with no valid session.
The user enters their email → receives a 6-digit PIN → enters it to sign in.
First-time users are prompted to purchase a license.
"""

import threading
import tkinter as tk
import webbrowser
import urllib.parse

from core import auth
from core.constants import APP_NAME, VERSION, PAYPAL_BUSINESS_EMAIL, BACKEND_URL


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def show(on_complete=None):
    """
    Show the login dialog and block until the user signs in.
    on_complete(logged_in: bool) is called with the result.
    """
    root = tk.Tk()
    root.title(f"{APP_NAME}  —  Sign In")
    root.configure(bg="#111111")
    root.resizable(False, False)
    from gui.tray import set_window_icon
    set_window_icon(root)

    _LoginDialog(root, on_complete)

    # Center on screen
    root.update_idletasks()
    w, h = 480, 560
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
    root.mainloop()


# ─────────────────────────────────────────────────────────────────────────────
# Dialog class
# ─────────────────────────────────────────────────────────────────────────────

class _LoginDialog:
    BG      = "#111111"
    BG_CARD = "#1a1a1a"
    BG_FIELD= "#222222"
    FG      = "#e8e8e8"
    FG_DIM  = "#777777"
    ACCENT  = "#00aaff"
    GREEN   = "#00cc66"
    WARN    = "#ffaa00"
    DANGER  = "#ff4444"
    BTN_BG  = "#2a2a2a"
    BTN_HOV = "#383838"

    def __init__(self, root: tk.Tk, on_complete):
        self.root        = root
        self.on_complete = on_complete
        self._pin_mode   = False   # True after PIN has been sent

        self._build()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self):
        root = self.root
        BG, BG_CARD, FG, FG_DIM, ACCENT = (
            self.BG, self.BG_CARD, self.FG, self.FG_DIM, self.ACCENT)

        outer = tk.Frame(root, bg=BG, padx=28, pady=22)
        outer.pack(fill="both", expand=True)

        # Header
        tk.Label(outer, text=APP_NAME, font=("Segoe UI", 28, "bold"),
                 bg=BG, fg=ACCENT).pack(anchor="w")
        tk.Label(outer, text=f"v{VERSION}   ·   System Optimizer",
                 font=("Segoe UI", 9), bg=BG, fg=FG_DIM).pack(anchor="w",
                 pady=(0, 14))
        tk.Frame(outer, bg="#2a2a2a", height=1).pack(fill="x", pady=(0, 16))

        # Card
        card = tk.Frame(outer, bg=BG_CARD, padx=22, pady=20)
        card.pack(fill="x")

        tk.Label(card, text="Sign in with your email",
                 font=("Segoe UI", 13, "bold"), bg=BG_CARD, fg=FG).pack(anchor="w")
        tk.Label(card,
                 text="Enter your email and we'll send a one-time PIN to log in.\n"
                      "First time?  Purchase a license below, then sign in.",
                 font=("Segoe UI", 8), bg=BG_CARD, fg=FG_DIM,
                 justify="left").pack(anchor="w", pady=(4, 14))

        # Email field
        tk.Label(card, text="Email address", font=("Segoe UI", 9, "bold"),
                 bg=BG_CARD, fg=FG_DIM).pack(anchor="w")
        self.email_var   = tk.StringVar()
        self.email_entry = tk.Entry(
            card, textvariable=self.email_var, width=38,
            bg=self.BG_FIELD, fg=FG, insertbackground=FG,
            relief="flat", font=("Segoe UI", 11),
            disabledbackground=self.BG_FIELD, disabledforeground="#555555",
        )
        self.email_entry.pack(fill="x", ipady=7, pady=(3, 12))
        self.email_entry.focus_set()

        # PIN field (hidden until email is sent)
        self._pin_outer = tk.Frame(card, bg=BG_CARD)
        tk.Label(self._pin_outer, text="6-digit PIN",
                 font=("Segoe UI", 9, "bold"),
                 bg=BG_CARD, fg=FG_DIM).pack(anchor="w")
        self.pin_var   = tk.StringVar()
        self.pin_entry = tk.Entry(
            self._pin_outer, textvariable=self.pin_var, width=14,
            bg=self.BG_FIELD, fg=self.ACCENT, insertbackground=self.ACCENT,
            relief="flat", font=("Consolas", 22, "bold"), justify="center",
        )
        self.pin_entry.pack(fill="x", ipady=10, pady=(3, 0))

        # Status
        self.status_var = tk.StringVar(value="")
        self.status_lbl = tk.Label(card, textvariable=self.status_var,
                                   font=("Segoe UI", 8, "italic"),
                                   bg=BG_CARD, fg=FG_DIM,
                                   wraplength=400, justify="left")
        self.status_lbl.pack(anchor="w", pady=(10, 0))

        # Buttons
        btn_row = tk.Frame(card, bg=BG_CARD)
        btn_row.pack(anchor="w", pady=(14, 0))

        self.send_btn   = self._btn(btn_row, "Send PIN",
                                    self._send_pin, self.ACCENT, bold=True)
        self.send_btn.pack(side="left", padx=(0, 8))

        self.verify_btn = self._btn(btn_row, "Verify & Sign In",
                                    self._verify_pin, self.GREEN, bold=True)
        # verify_btn appears after PIN is sent

        # Purchase section
        tk.Frame(outer, bg="#2a2a2a", height=1).pack(fill="x", pady=(16, 12))

        tk.Label(outer, text="New to AlienCore?",
                 font=("Segoe UI", 10, "bold"), bg=BG, fg=FG).pack(anchor="w")
        tk.Label(outer,
                 text="One-time payments — no subscriptions, ever.  "
                      "Lifetime license includes all future updates.",
                 font=("Segoe UI", 8), bg=BG, fg=FG_DIM,
                 wraplength=420, justify="left").pack(anchor="w",
                 pady=(3, 10))

        purchase_row = tk.Frame(outer, bg=BG)
        purchase_row.pack(anchor="w")
        self._buy_btn(purchase_row, "Buy AlienCore   $20",
                      "AC_BASE",    "AlienCore — Lifetime License", "20.00",
                      self.GREEN).pack(side="left", padx=(0, 8))
        self._buy_btn(purchase_row, "Pro Add-on   +$5",
                      "AC_PRO",     "AlienCore Pro Add-on",          "5.00",
                      self.ACCENT).pack(side="left", padx=(0, 8))
        self._buy_btn(purchase_row, "Priority Support   $5",
                      "AC_SUPPORT", "AlienCore Priority Support (1x)","5.00",
                      self.WARN).pack(side="left")

        # Keybinds
        self.email_entry.bind("<Return>", lambda e: self._send_pin())
        self.pin_entry.bind("<Return>",   lambda e: self._verify_pin())

    # ── Actions ───────────────────────────────────────────────────────────────

    def _send_pin(self):
        email = self.email_var.get().strip()
        if not email or "@" not in email:
            self._status("Enter a valid email address.", self.DANGER)
            return
        self.send_btn.config(state="disabled",
                             text="Resend PIN" if self._pin_mode else "Sending...")
        self._status("")

        def _work():
            ok, msg = auth.send_pin(email)
            def _ui():
                self.send_btn.config(state="normal",
                                     text="Resend PIN" if self._pin_mode else "Send PIN")
                if not self._pin_mode:
                    self._pin_mode = True
                    self.email_entry.config(state="disabled")
                    self._pin_outer.pack(fill="x", pady=(0, 0))
                    self.verify_btn.pack(side="left")
                    self.pin_entry.focus_set()
                    self.send_btn.config(text="Resend PIN")
                if ok:
                    self._status("PIN sent — check your email.", self.GREEN)
                else:
                    self._status(msg, self.DANGER)
            self.root.after(0, _ui)
        threading.Thread(target=_work, daemon=True).start()

    def _verify_pin(self):
        email = self.email_var.get().strip()
        pin   = self.pin_var.get().strip()
        if len(pin) != 6 or not pin.isdigit():
            self._status("Enter the 6-digit PIN from your email.", self.DANGER)
            return
        self.verify_btn.config(state="disabled", text="Verifying...")
        self._status("")

        def _work():
            ok, msg = auth.verify_pin(email, pin)
            def _ui():
                self.verify_btn.config(state="normal", text="Verify & Sign In")
                if ok:
                    self._status("Signed in!", self.GREEN)
                    self.root.after(700, self._finish)
                else:
                    self._status(msg, self.DANGER)
            self.root.after(0, _ui)
        threading.Thread(target=_work, daemon=True).start()

    def _finish(self):
        logged_in = auth.is_logged_in()
        self.root.destroy()
        if self.on_complete:
            self.on_complete(logged_in)

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

    def _buy_btn(self, parent, label, item_number, item_name, amount, color):
        """Create a button that opens the PayPal payment page."""
        def _open():
            email = self.email_var.get().strip()
            params = urllib.parse.urlencode({
                "cmd":           "_xclick",
                "business":      PAYPAL_BUSINESS_EMAIL,
                "item_name":     item_name,
                "item_number":   item_number,
                "amount":        amount,
                "currency_code": "USD",
                "custom":        email,
                "notify_url":    BACKEND_URL.rstrip("/") + "/paypal/ipn",
            })
            webbrowser.open(f"https://www.paypal.com/cgi-bin/webscr?{params}")
        return self._btn(parent, label, _open, color)
