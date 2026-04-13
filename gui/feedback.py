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


def open_feedback():
    """Open the feedback window (singleton — raises existing if already open)."""
    global _instance
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
            hint = f"Email opens your mail client pre-filled  |  GitHub Issues requires a free account"
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
        import json, platform
        lines = [
            f"AlienCore: v{VERSION}",
            f"OS: Windows {platform.release()} (build {platform.version()})",
        ]
        try:
            if os.path.exists(HARDWARE_CACHE):
                with open(HARDWARE_CACHE, "r", encoding="utf-8") as f:
                    hw = json.load(f)
                cpu = hw.get("cpu", {})
                lines.append(
                    f"CPU: {cpu.get('name', '?')} — "
                    f"{cpu.get('physical_cores', '?')}P / {cpu.get('logical_cores', '?')}L cores"
                )
                for gpu in hw.get("gpu", []):
                    if not gpu.get("is_integrated"):
                        lines.append(
                            f"GPU: {gpu.get('name', '?')}  "
                            f"VRAM: {gpu.get('vram_mb', 0):,} MB"
                        )
                ram = hw.get("ram", {})
                lines.append(f"RAM: {ram.get('total_gb', '?')} GB")
        except Exception as e:
            lines.append(f"Hardware info unavailable: {e}")

        # Last 40 lines of log
        try:
            if os.path.exists(LOG_PATH):
                with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                    log_lines = f.readlines()
                lines.append("\n--- Recent log (last 40 lines) ---")
                lines.append("".join(log_lines[-40:]).strip())
            else:
                lines.append("\n--- Log file not found ---")
        except Exception as e:
            lines.append(f"\nLog unavailable: {e}")

        self._sysinfo = "\n".join(lines)
        self._sysinfo_ready = True
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
        import urllib.parse, webbrowser
        ftype   = self._type_var.get()
        subject = urllib.parse.quote(f"AlienCore {ftype}")
        # mailto body has a length limit in some clients — truncate log if needed
        report  = self._build_report()
        if len(report) > 1800:
            report = report[:1800] + "\n...(truncated — attach full report if needed)"
        body = urllib.parse.quote(report)
        webbrowser.open(f"mailto:{SUPPORT_EMAIL}?subject={subject}&body={body}")

    def _on_close(self):
        global _instance
        _instance = None
        self.root.destroy()

    def run(self):
        self.root.mainloop()
