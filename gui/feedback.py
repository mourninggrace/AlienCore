"""
AlienCore - feedback.py
Send Feedback window.

  * Bug Report / Feature Request / Other
  * Auto-collects system info (version, OS, CPU, GPU, RAM, last 40 log lines)
  * "Open GitHub Issues" opens browser to the issues page
  * "Copy Report" copies the formatted report to clipboard
  * "Send via Email" opens mailto: if SUPPORT_EMAIL is configured in constants.py
"""

import os
import threading
import tkinter as tk
from tkinter import scrolledtext, messagebox
import logging

from core import config_manager as cfg
from core.constants import (
    APP_NAME, VERSION, HARDWARE_CACHE, LOG_PATH,
    SUPPORT_EMAIL, GITHUB_ISSUES_URL,
)

logger = logging.getLogger("aliencore.feedback")

# ── Palette (matches rest of app) ─────────────────────────────────────────────
BG       = "#111318"
BG_PANEL = "#1c1f27"
BG_INPUT = "#1a1d24"
BG_LOG   = "#0d0f14"
FG       = "#e0e0e0"
FG_DIM   = "#666666"
ACCENT   = "#00aaff"
ACCENT2  = "#00cc66"
BORDER   = "#2a2d38"

_instance = None
_instance_lock = threading.Lock()


def open_feedback():
    """Open the feedback window (singleton — raises existing if already open)."""
    global _instance
    with _instance_lock:
        if _instance is not None:
            try:
                _instance.root.lift()
                _instance.root.focus_force()
                return
            except Exception:
                _instance = None
        _instance = FeedbackWindow()
    _instance.run()


def open_feedback_thread():
    """Non-blocking: open the feedback window in its own thread."""
    threading.Thread(target=open_feedback, daemon=True,
                     name="FeedbackThread").start()


# ─────────────────────────────────────────────────────────────────────────────

class FeedbackWindow:
    def __init__(self):
        global _instance
        _instance = self

        self.root = tk.Tk()
        self.root.title(f"{APP_NAME}  —  Send Feedback")
        self.root.configure(bg=BG)
        from gui.tray import set_window_icon
        set_window_icon(self.root)
        self.root.geometry("660x580")
        self.root.minsize(520, 460)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.lift()
        self.root.focus_force()
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))

        self._sysinfo = ""
        self._sysinfo_ready = False

        self._build()
        threading.Thread(target=self._collect_sysinfo, daemon=True,
                         name="FeedbackSysInfo").start()

    def _build(self):
        # ── Toolbar ───────────────────────────────────────────────────────────
        bar = tk.Frame(self.root, bg=BORDER, pady=6)
        bar.pack(fill="x")
        tk.Label(bar, text=f"{APP_NAME}  —  Send Feedback",
                 font=("Segoe UI", 11, "bold"),
                 bg=BORDER, fg=ACCENT).pack(side="left", padx=12)

        # ── Body ──────────────────────────────────────────────────────────────
        body = tk.Frame(self.root, bg=BG, padx=16, pady=12)
        body.pack(fill="both", expand=True)

        # Feedback type
        type_row = tk.Frame(body, bg=BG)
        type_row.pack(fill="x", pady=(0, 12))
        tk.Label(type_row, text="Type:", font=("Segoe UI", 9),
                 bg=BG, fg=FG_DIM, width=8, anchor="w").pack(side="left")
        self._type_var = tk.StringVar(master=self.root, value="Bug Report")
        for val in ["Bug Report", "Feature Request", "Other"]:
            tk.Radiobutton(type_row, text=val, variable=self._type_var, value=val,
                           bg=BG, fg=FG, selectcolor=ACCENT,
                           activebackground=BG, activeforeground=ACCENT,
                           font=("Segoe UI", 9)).pack(side="left", padx=10)

        # Description
        tk.Label(body, text="Description", font=("Segoe UI", 9, "bold"),
                 bg=BG, fg=FG).pack(anchor="w")
        tk.Label(body,
                 text="Describe the issue or feature in as much detail as possible.",
                 font=("Segoe UI", 8, "italic"),
                 bg=BG, fg=FG_DIM).pack(anchor="w", pady=(2, 6))

        self.desc = tk.Text(body, height=10, bg=BG_INPUT, fg=FG,
                            font=("Segoe UI", 10), wrap="word", relief="flat",
                            insertbackground=FG, selectbackground="#2a4a6a",
                            padx=10, pady=8)
        self.desc.pack(fill="both", expand=True)
        self.desc.focus_set()

        # System info toggle
        info_row = tk.Frame(body, bg=BG)
        info_row.pack(fill="x", pady=(10, 0))
        self._show_info = tk.BooleanVar(master=self.root, value=True)
        tk.Checkbutton(info_row,
                       text="Include system info  (version, hardware, recent log lines)",
                       variable=self._show_info,
                       bg=BG, fg=FG_DIM, selectcolor=ACCENT,
                       activebackground=BG, activeforeground=ACCENT,
                       font=("Segoe UI", 8),
                       command=self._toggle_sysinfo).pack(side="left")
        self._info_status = tk.Label(info_row, text="collecting...",
                                     font=("Segoe UI", 8, "italic"),
                                     bg=BG, fg=FG_DIM)
        self._info_status.pack(side="left", padx=8)

        # System info preview (shown by default, collapsible)
        self._info_frame = tk.Frame(body, bg=BG)
        self._info_frame.pack(fill="x", pady=(4, 0))
        self._info_text = scrolledtext.ScrolledText(
            self._info_frame, height=5, bg=BG_LOG, fg=FG_DIM,
            font=("Consolas", 8), wrap="none", relief="flat",
            state="disabled", padx=8, pady=6)
        self._info_text.pack(fill="x")

        # ── Separator ─────────────────────────────────────────────────────────
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x")

        # ── Footer ────────────────────────────────────────────────────────────
        foot = tk.Frame(self.root, bg=BG, pady=10, padx=16)
        foot.pack(fill="x")

        if SUPPORT_EMAIL:
            hint = ("Send via Email delivers your message straight to Kyle. "
                    "Copy Report lets you paste it anywhere. GitHub Issues "
                    "requires a free account.")
        else:
            hint = "Open GitHub Issues, then paste your copied report."
        tk.Label(foot, text=hint, font=("Segoe UI", 8, "italic"),
                 bg=BG, fg=FG_DIM).pack(anchor="w", pady=(0, 8))

        btn_row = tk.Frame(foot, bg=BG)
        btn_row.pack(fill="x")

        btn = lambda parent, text, cmd, fg, bg2: tk.Button(
            parent, text=text, command=cmd,
            bg=bg2, fg=fg, activebackground="#333740", activeforeground=fg,
            relief="flat", padx=12, pady=6, cursor="hand2",
            font=("Segoe UI", 9, "bold"), bd=0, highlightthickness=0,
        )

        btn(btn_row, "Cancel", self._on_close,
            FG_DIM, "#2a2d38").pack(side="right", padx=(6, 0))

        btn(btn_row, "Copy Report", self._copy_report,
            ACCENT, "#1a2a3a").pack(side="right", padx=6)

        btn(btn_row, "Open GitHub Issues", self._open_github,
            ACCENT, "#1a2535").pack(side="right", padx=6)

        if SUPPORT_EMAIL:
            btn(btn_row, "Send via Email", self._send_email,
                ACCENT2, "#1a2e1a").pack(side="right", padx=6)

    # ── System info collection ─────────────────────────────────────────────────

    def _collect_sysinfo(self):
        import json, platform, sys, datetime
        lines = [
            f"AlienCore:  v{VERSION}",
            f"Report time:  {datetime.datetime.now().isoformat(timespec='seconds')}",
            f"Python:  {sys.version.split()[0]}  ({platform.python_implementation()})",
        ]

        # Windows edition + UBR build
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
                def _q(name, default=""):
                    try:
                        v, _ = winreg.QueryValueEx(k, name)
                        return v
                    except Exception:
                        return default
                edition   = _q("EditionID", "")
                prod_name = _q("ProductName", "")
                build     = _q("CurrentBuild", "")
                ubr       = _q("UBR", "")
            release = platform.release()
            full_build = f"{build}.{ubr}" if ubr else build
            os_line = f"OS:  Windows {release}"
            if edition:
                os_line += f" {edition}"
            if full_build:
                os_line += f"  (build {full_build})"
            if prod_name and prod_name not in os_line:
                os_line += f"  [{prod_name}]"
            lines.append(os_line)
        except Exception:
            lines.append(f"OS:  Windows {platform.release()} ({platform.version()})")

        # Admin / elevation
        try:
            from core.elevation import is_admin
            lines.append(f"Admin:  {'Yes' if is_admin() else 'No'}")
        except Exception:
            pass

        # Hardware cache snapshot
        hw = {}
        try:
            if os.path.exists(HARDWARE_CACHE):
                with open(HARDWARE_CACHE, "r", encoding="utf-8") as f:
                    hw = json.load(f)
                cpu = hw.get("cpu", {})
                lines.append(
                    f"CPU:  {cpu.get('name', '?')}  "
                    f"({cpu.get('physical_cores', '?')}P / {cpu.get('logical_cores', '?')}L)"
                )
                freq = cpu.get("max_frequency_mhz") or cpu.get("max_freq_mhz")
                if freq:
                    lines.append(f"CPU max freq:  {freq} MHz")
                for gpu in hw.get("gpu", []):
                    if not gpu.get("is_integrated"):
                        lines.append(
                            f"GPU:  {gpu.get('name', '?')}  "
                            f"VRAM: {gpu.get('vram_mb', 0):,} MB  "
                            f"driver: {gpu.get('driver_version', '?')}"
                        )
                ram = hw.get("ram", {})
                lines.append(
                    f"RAM:  {ram.get('total_gb', '?')} GB  "
                    f"{ram.get('speed_mhz', '?')} MHz  "
                    f"{ram.get('type', '?')}"
                )
                plat = hw.get("platform", {})
                if plat:
                    lines.append(
                        f"Platform:  AWCC={plat.get('has_awcc', False)}  "
                        f"LHM={plat.get('has_lhm', False)}  "
                        f"model={plat.get('model', '?')}"
                    )
        except Exception as e:
            lines.append(f"Hardware info unavailable:  {e}")

        # License + account state
        try:
            from core import auth
            sess = auth.get_session() or {}
            if sess.get("email"):
                lines.append(
                    f"Account:  {sess.get('email')}  "
                    f"base={bool(sess.get('has_base'))}  "
                    f"pro={bool(sess.get('has_pro'))}"
                )
            else:
                lines.append("Account:  (not signed in)")
        except Exception:
            pass

        # Active config snapshot (essential bits only)
        try:
            c = cfg.load()
            snap = {
                "service.log_enabled":     c.get("service", {}).get("log_enabled"),
                "ai.provider":             c.get("ai", {}).get("provider"),
                "ai.model":                c.get("ai", {}).get("model"),
                "profiles.enabled":        c.get("profiles", {}).get("enabled"),
                "cpu.power_mode":          c.get("cpu", {}).get("power_mode"),
                "gpu.transparent_when":    c.get("gpu", {}).get("transparent_when"),
            }
            lines.append("Config (key flags):")
            for k, v in snap.items():
                lines.append(f"  {k} = {v}")
        except Exception:
            pass

        # Last 60 lines of log (extended from 40)
        try:
            if os.path.exists(LOG_PATH):
                with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                    log_lines = f.readlines()
                lines.append("\n--- Recent log (last 60 lines) ---")
                lines.append("".join(log_lines[-60:]).strip())
            else:
                lines.append("\n--- Log file not found ---")
        except Exception as e:
            lines.append(f"\nLog unavailable:  {e}")

        self._sysinfo = "\n".join(lines)
        self._sysinfo_ready = True
        if self.root.winfo_exists():
            self.root.after(0, self._on_sysinfo_ready)

    def _on_sysinfo_ready(self):
        self._info_status.config(text="ready", fg=ACCENT2)
        self._info_text.config(state="normal")
        self._info_text.delete("1.0", "end")
        self._info_text.insert("1.0", self._sysinfo)
        self._info_text.config(state="disabled")

    def _toggle_sysinfo(self):
        if self._show_info.get():
            self._info_frame.pack(fill="x", pady=(4, 0))
        else:
            self._info_frame.pack_forget()

    # ── Report assembly ───────────────────────────────────────────────────────

    def _build_report(self) -> str:
        ftype = self._type_var.get()
        desc  = self.desc.get("1.0", "end").strip()
        report = f"[{ftype}]\n\n{desc}"
        if self._show_info.get() and self._sysinfo:
            report += f"\n\n---\nSystem Info:\n{self._sysinfo}"
        return report

    # ── Actions ───────────────────────────────────────────────────────────────

    def _copy_report(self):
        report = self._build_report()
        self.root.clipboard_clear()
        self.root.clipboard_append(report)
        self.root.update()
        messagebox.showinfo(
            "Copied",
            "Report copied to clipboard.\n\nPaste it into GitHub Issues or your email.",
            parent=self.root,
        )

    def _open_github(self):
        import webbrowser
        webbrowser.open(GITHUB_ISSUES_URL)

    def _send_email(self):
        """Send feedback via the backend relay (Brevo).
        Falls back to a mailto link if the backend is unreachable."""
        ftype = self._type_var.get()
        desc  = self.desc.get("1.0", "end").strip()
        if not desc:
            messagebox.showwarning(
                "Nothing to send",
                "Please enter a description before sending.",
                parent=self.root)
            return

        # Determine the sender email (signed-in user preferred)
        from_email = ""
        try:
            from core import auth
            from_email = (auth.get_email() or "").strip()
        except Exception:
            pass
        if not from_email:
            from_email = self._prompt_email()
            if not from_email:
                return

        sysinfo = self._sysinfo if (self._show_info.get() and self._sysinfo) else ""

        # Disable the button while sending; re-enable afterwards
        self._set_buttons_state("disabled")
        self._info_status.config(text="sending…", fg=ACCENT)

        def worker():
            ok, err = self._post_feedback(from_email, ftype, desc, sysinfo)
            self.root.after(0, lambda: self._after_send(ok, err, from_email,
                                                        ftype, desc, sysinfo))

        threading.Thread(target=worker, daemon=True,
                         name="FeedbackSend").start()

    def _post_feedback(self, from_email: str, ftype: str,
                       description: str, sysinfo: str):
        import json, urllib.request, urllib.error
        from core.constants import BACKEND_URL
        url = BACKEND_URL.rstrip("/") + "/support/submit"
        payload = json.dumps({
            "email":       from_email,
            "type":        ftype,
            "description": description,
            "sysinfo":     sysinfo,
        }).encode("utf-8")
        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST")
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                data = json.loads(body) if body else {}
                if data.get("ok"):
                    return True, ""
                return False, data.get("error") or f"HTTP {resp.status}"
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")
                data = json.loads(body) if body else {}
                return False, data.get("error") or f"HTTP {e.code}"
            except Exception:
                return False, f"HTTP {e.code}"
        except Exception as e:
            return False, str(e)

    def _after_send(self, ok: bool, err: str, from_email: str,
                    ftype: str, description: str, sysinfo: str):
        self._set_buttons_state("normal")
        if ok:
            self._info_status.config(text="sent", fg=ACCENT2)
            messagebox.showinfo(
                "Feedback sent",
                f"Thanks! Your {ftype.lower()} has been delivered to "
                f"{SUPPORT_EMAIL}.\n\nReplies will come back to "
                f"{from_email}.",
                parent=self.root)
            self.root.after(200, self._on_close)
            return
        self._info_status.config(text="failed", fg="#ff4444")
        # Offer fallback — mailto with current clipboard fill
        if messagebox.askyesno(
                "Couldn’t send feedback",
                f"The feedback server could not be reached:\n\n{err}\n\n"
                "Would you like to open your default mail client with the "
                "message pre-filled instead?",
                parent=self.root):
            import urllib.parse, webbrowser
            subject = urllib.parse.quote(f"AlienCore {ftype}")
            report = f"[{ftype}]\n\n{description}"
            if sysinfo:
                report += f"\n\n---\nSystem Info:\n{sysinfo}"
            if len(report) > 1800:
                report = report[:1800] + "\n...(truncated)"
            body = urllib.parse.quote(report)
            webbrowser.open(f"mailto:{SUPPORT_EMAIL}?subject={subject}&body={body}")

    def _prompt_email(self) -> str:
        """Small modal prompt for the user’s return email address."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Your email")
        dlg.configure(bg=BG)
        dlg.geometry("380x170")
        dlg.transient(self.root)
        dlg.grab_set()
        tk.Label(dlg, text="What email should we reply to?",
                 font=("Segoe UI", 10, "bold"),
                 bg=BG, fg=ACCENT).pack(anchor="w", padx=18, pady=(16, 4))
        tk.Label(dlg,
                 text="Used only so Kyle can answer your message.",
                 font=("Segoe UI", 8),
                 bg=BG, fg=FG_DIM).pack(anchor="w", padx=18, pady=(0, 8))
        var = tk.StringVar()
        tk.Entry(dlg, textvariable=var, font=("Consolas", 10),
                 bg=BG_INPUT, fg=FG, insertbackground=FG,
                 relief="flat").pack(fill="x", padx=18, ipady=4)
        result = {"email": ""}
        def ok():
            v = var.get().strip()
            if "@" not in v or "." not in v.split("@")[-1]:
                return
            result["email"] = v
            dlg.destroy()
        foot = tk.Frame(dlg, bg=BG); foot.pack(fill="x", padx=18, pady=14, side="bottom")
        tk.Button(foot, text="Cancel", command=dlg.destroy,
                  bg="#2a2d38", fg=FG_DIM, relief="flat",
                  padx=14, pady=4, bd=0).pack(side="right", padx=(6, 0))
        tk.Button(foot, text="OK", command=ok,
                  bg="#1a2e1a", fg=ACCENT2, relief="flat",
                  padx=14, pady=4, bd=0,
                  font=("Segoe UI", 9, "bold")).pack(side="right")
        self.root.wait_window(dlg)
        return result["email"]

    def _set_buttons_state(self, state: str):
        """Enable/disable all footer action buttons during a send."""
        def walk(w):
            for ch in w.winfo_children():
                if isinstance(ch, tk.Button):
                    try: ch.config(state=state)
                    except Exception: pass
                walk(ch)
        walk(self.root)

    def _on_close(self):
        global _instance
        _instance = None
        self.root.destroy()

    def run(self):
        self.root.mainloop()
