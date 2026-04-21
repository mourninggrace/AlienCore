"""
AlienCore - login_dialog.py
Email + PIN authentication dialog.

Shown when AlienCore starts with no valid session. The user enters their
email → receives a 6-digit PIN → enters it to sign in. First-time users
start a free 30-day trial automatically; purchases happen from the
Account tab in Settings after signing in.
"""

import threading
import tkinter as tk

from core import auth
from core.constants import APP_NAME, VERSION


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
    w, h = 460, 540
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
        self.root           = root
        self.on_complete    = on_complete
        self._pin_mode      = False   # True after PIN has been sent
        self._yubikey_stop  = False
        self._yubikey_found = False

        self._build()
        self._start_yubikey_poll()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

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
                 text="Enter your email and we'll send a one-time PIN to sign in.\n"
                      "New users get a free 30-day trial — no card needed.",
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
        self.email_entry.pack(fill="x", ipady=7, pady=(3, 2))
        self.email_entry.focus_set()
        tk.Label(card, text="Press Enter",
                 font=("Segoe UI", 8, "italic"),
                 bg=BG_CARD, fg=FG_DIM).pack(anchor="w", pady=(0, 12))

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
        self.pin_entry.pack(fill="x", ipady=10, pady=(3, 2))
        tk.Label(self._pin_outer, text="Press Enter",
                 font=("Segoe UI", 8, "italic"),
                 bg=BG_CARD, fg=FG_DIM).pack(anchor="w", pady=(0, 0))

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

        # Send PIN is triggered by pressing Enter in the email field;
        # this button only appears after the first send, as a Resend affordance.
        self.send_btn   = self._btn(btn_row, "Resend PIN",
                                    self._send_pin, self.ACCENT, bold=True)

        self.verify_btn = self._btn(btn_row, "Verify & Sign In",
                                    self._verify_pin, self.GREEN, bold=True)
        # verify_btn and send_btn (as Resend) both appear after PIN is sent

        # Pricing teaser
        tk.Label(outer,
                 text="Free 30-day trial  ·  $19.99 lifetime afterward  ·  "
                      "Buy from Settings → Account after signing in",
                 font=("Segoe UI", 8), bg=BG, fg=FG_DIM,
                 justify="center", wraplength=380).pack(pady=(14, 0))

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
                if ok:
                    if not self._pin_mode:
                        self._pin_mode = True
                        self.email_entry.config(state="disabled")
                        self._pin_outer.pack(fill="x", pady=(0, 0),
                                             before=self.status_lbl)
                        self.verify_btn.pack(side="left", padx=(0, 8))
                        self.send_btn.pack(side="left")
                        self.pin_entry.focus_set()
                        self.send_btn.config(text="Resend PIN")
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
        self._yubikey_stop = True
        logged_in = auth.is_logged_in()
        self.root.destroy()
        if self.on_complete:
            self.on_complete(logged_in)

    def _on_close(self):
        self._yubikey_stop = True
        self.root.destroy()
        if self.on_complete:
            self.on_complete(auth.is_logged_in())

    # ── YubiKey dev-unlock polling ────────────────────────────────────────────
    # The dialog polls for a dev-allowlisted YubiKey in the background so that
    # plugging the key in AFTER the dialog is already visible still unlocks
    # (e.g. when the key isn't enumerated yet during cold boot, or when the
    # dialog was opened from Settings after signing out).

    def _start_yubikey_poll(self):
        threading.Thread(target=self._yubikey_worker, daemon=True,
                         name="LoginYubiKeyPoll").start()

    def _yubikey_worker(self):
        import time as _t
        while not self._yubikey_stop:
            try:
                if auth.try_dev_unlock():
                    self._yubikey_found = True
                    self.root.after(0, self._yubikey_unlocked)
                    return
            except Exception:
                pass
            # PowerShell PnP scan runs for 1–3 s; poll every ~4 s.
            for _ in range(40):
                if self._yubikey_stop:
                    return
                _t.sleep(0.1)

    def _yubikey_unlocked(self):
        self._status("Developer YubiKey detected — signing in...", self.GREEN)
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

