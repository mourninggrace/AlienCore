"""
AlienCore - ai_advisor_ui.py
Config Advisor window — shows AI-proposed config changes for user review.

Layout:
  ┌──────────────────────────────────────────────────┐
  │  AlienCore Config Advisor            [Analyze]   │
  ├──────────────────────────────────────────────────┤
  │  Assessment text (scrolled)                      │
  ├──────────────────────────────────────────────────┤
  │  ☑  CPU warn temp        80° → 75°   reason...  │
  │  ☑  Dynamic CPU throttle ON  → OFF   reason...  │
  │  ☐  GPU power in gaming  100 → 85    reason...  │
  ├──────────────────────────────────────────────────┤
  │  [Apply Selected]  [Dismiss]   [Rollback]  log  │
  └──────────────────────────────────────────────────┘
"""

import tkinter as tk
from tkinter import scrolledtext, messagebox
import threading
import logging
from core import config_manager as cfg

logger = logging.getLogger("aliencore.advisor_ui")

# ── Palette ───────────────────────────────────────────────────────────────────
BG       = "#111318"
BG_ROW   = "#171a22"
BG_ROW_A = "#1a1e28"   # alternate row
BG_HDR   = "#1e2230"
FG       = "#e0e0e0"
FG_DIM   = "#666666"
FG_KEY   = "#aaaaaa"
FG_OLD   = "#888888"
FG_NEW   = "#7ec8ff"
FG_GOOD  = "#a8e090"
FG_ERR   = "#ff6060"
FG_WARN  = "#ffaa00"
ACCENT   = "#00aaff"
BORDER   = "#2a2d38"
FONT     = ("Segoe UI", 10)
FONT_SM  = ("Segoe UI", 8)
FONT_MONO= ("Consolas", 9)

_instance = None


def open_advisor():
    global _instance
    if _instance is not None:
        try:
            _instance.root.lift()
            _instance.root.focus_force()
            return
        except Exception:
            _instance = None
    _instance = AdvisorWindow()
    _instance.run()


def open_advisor_thread():
    t = threading.Thread(target=open_advisor, daemon=True, name="AdvisorThread")
    t.start()


# ─────────────────────────────────────────────────────────────────────────────

class AdvisorWindow:
    def __init__(self):
        global _instance
        _instance = self

        self.root = tk.Tk()
        self.root.title("AlienCore  —  AI Config Advisor")
        self.root.configure(bg=BG)
        from gui.tray import set_window_icon
        set_window_icon(self.root)
        self.root.geometry("760x620")
        self.root.minsize(600, 480)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._proposals   = []     # list[Proposal]
        self._check_vars  = []     # list[BooleanVar] — one per proposal
        self._busy        = False

        self._build()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self):
        # ── Header bar ────────────────────────────────────────────────────────
        hdr = tk.Frame(self.root, bg=BG_HDR, pady=6)
        hdr.pack(fill="x")

        tk.Label(hdr, text="AI Config Advisor", font=("Segoe UI", 12, "bold"),
                 bg=BG_HDR, fg=ACCENT).pack(side="left", padx=12)

        self._status_lbl = tk.Label(hdr, text="", font=FONT_SM,
                                    bg=BG_HDR, fg=FG_DIM)
        self._status_lbl.pack(side="left", padx=8)

        self._analyze_btn = tk.Button(
            hdr, text="  Analyze  ",
            bg="#1a3a5c", fg="#7ec8ff",
            activebackground="#1f4a73", activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"), relief="flat",
            padx=10, pady=4, cursor="hand2",
            command=self._start_analysis,
        )
        self._analyze_btn.pack(side="right", padx=10)

        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x")

        # ── Assessment area ───────────────────────────────────────────────────
        self._assessment = scrolledtext.ScrolledText(
            self.root, height=5, bg=BG_ROW, fg=FG,
            font=FONT, wrap="word", state="disabled",
            relief="flat", bd=0, padx=12, pady=8,
            insertbackground=FG,
        )
        self._assessment.pack(fill="x", padx=0)
        self._assessment.tag_config("dim",  foreground=FG_DIM,
                                    font=("Segoe UI", 9, "italic"))
        self._assessment.tag_config("text", foreground=FG, font=FONT)

        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x")

        # ── Proposals list ────────────────────────────────────────────────────
        list_outer = tk.Frame(self.root, bg=BG)
        list_outer.pack(fill="both", expand=True)

        # Column headers
        hrow = tk.Frame(list_outer, bg=BG_HDR, pady=4)
        hrow.pack(fill="x")
        tk.Label(hrow, text="  ✓", width=3, bg=BG_HDR, fg=FG_DIM,
                 font=FONT_SM).pack(side="left")
        tk.Label(hrow, text="Setting", width=28, anchor="w",
                 bg=BG_HDR, fg=FG_DIM, font=FONT_SM).pack(side="left")
        tk.Label(hrow, text="Current", width=10, anchor="w",
                 bg=BG_HDR, fg=FG_DIM, font=FONT_SM).pack(side="left")
        tk.Label(hrow, text="→  Proposed", width=14, anchor="w",
                 bg=BG_HDR, fg=FG_DIM, font=FONT_SM).pack(side="left")
        tk.Label(hrow, text="Reason", anchor="w",
                 bg=BG_HDR, fg=FG_DIM, font=FONT_SM).pack(side="left", padx=4)

        # Scrollable rows
        canvas = tk.Canvas(list_outer, bg=BG, highlightthickness=0)
        sb     = tk.Scrollbar(list_outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self._rows_frame = tk.Frame(canvas, bg=BG)
        self._rows_window = canvas.create_window((0, 0), window=self._rows_frame,
                                                  anchor="nw")

        self._rows_frame.bind("<Configure>",
                              lambda e: canvas.configure(
                                  scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(self._rows_window, width=e.width))
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        self._canvas = canvas
        self._show_placeholder("Click  Analyze  to get AI recommendations.")

        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x")

        # ── Footer ────────────────────────────────────────────────────────────
        foot = tk.Frame(self.root, bg=BG_HDR, pady=6)
        foot.pack(fill="x", side="bottom")

        self._apply_btn = tk.Button(
            foot, text="Apply Selected",
            bg="#1a4a1a", fg=FG_GOOD,
            activebackground="#1f5c1f", activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"), relief="flat",
            padx=12, pady=4, cursor="hand2",
            state="disabled",
            command=self._apply_selected,
        )
        self._apply_btn.pack(side="left", padx=(12, 4))

        tk.Button(foot, text="Select All",
                  bg="#252830", fg=FG_DIM,
                  activebackground="#333740", activeforeground=FG,
                  font=FONT_SM, relief="flat", padx=8, pady=4, cursor="hand2",
                  command=self._select_all).pack(side="left", padx=2)

        tk.Button(foot, text="Deselect All",
                  bg="#252830", fg=FG_DIM,
                  activebackground="#333740", activeforeground=FG,
                  font=FONT_SM, relief="flat", padx=8, pady=4, cursor="hand2",
                  command=self._deselect_all).pack(side="left", padx=2)

        # Rollback — only visible if backup exists
        self._rollback_btn = tk.Button(
            foot, text="Rollback Last",
            bg="#3a1a1a", fg=FG_WARN,
            activebackground="#4a2020", activeforeground="#ffffff",
            font=FONT_SM, relief="flat", padx=8, pady=4, cursor="hand2",
            command=self._rollback,
        )
        self._refresh_rollback_btn()

        # Change log button
        tk.Button(foot, text="Change Log",
                  bg="#252830", fg=FG_DIM,
                  activebackground="#333740", activeforeground=FG,
                  font=FONT_SM, relief="flat", padx=8, pady=4, cursor="hand2",
                  command=self._show_log).pack(side="right", padx=(2, 12))

    # ── Proposal rows ──────────────────────────────────────────────────────────

    def _show_placeholder(self, text: str):
        for w in self._rows_frame.winfo_children():
            w.destroy()
        tk.Label(self._rows_frame, text=text, font=("Segoe UI", 10, "italic"),
                 bg=BG, fg=FG_DIM, pady=20).pack()

    def _render_proposals(self, proposals: list):
        for w in self._rows_frame.winfo_children():
            w.destroy()
        self._check_vars = []

        if not proposals:
            self._show_placeholder("No changes suggested — configuration looks good.")
            self._apply_btn.config(state="disabled")
            return

        for i, p in enumerate(proposals):
            row_bg = BG_ROW if i % 2 == 0 else BG_ROW_A
            row = tk.Frame(self._rows_frame, bg=row_bg, pady=5)
            row.pack(fill="x")

            var = tk.BooleanVar(value=True)
            self._check_vars.append(var)

            tk.Checkbutton(row, variable=var, bg=row_bg,
                           activebackground=row_bg, selectcolor=row_bg,
                           fg=ACCENT, activeforeground=ACCENT,
                           width=2).pack(side="left", padx=(8, 0))

            tk.Label(row, text=p.label, width=28, anchor="w",
                     bg=row_bg, fg=FG, font=FONT).pack(side="left")

            tk.Label(row, text=self._fmt_val(p.current), width=10, anchor="w",
                     bg=row_bg, fg=FG_OLD, font=FONT_MONO).pack(side="left")

            tk.Label(row, text=f"→  {self._fmt_val(p.proposed)}", width=14, anchor="w",
                     bg=row_bg, fg=FG_NEW, font=("Consolas", 9, "bold")).pack(side="left")

            tk.Label(row, text=p.reason, anchor="w", wraplength=260,
                     bg=row_bg, fg=FG_DIM, font=FONT_SM,
                     justify="left").pack(side="left", padx=(4, 8), fill="x", expand=True)

        self._apply_btn.config(state="normal")

    @staticmethod
    def _fmt_val(v) -> str:
        if isinstance(v, bool):
            return "ON" if v else "OFF"
        if isinstance(v, float):
            return f"{v:.1f}"
        return str(v) if v is not None else "—"

    # ── Assessment area ────────────────────────────────────────────────────────

    def _set_assessment(self, text: str, tag: str = "text"):
        self._assessment.config(state="normal")
        self._assessment.delete("1.0", "end")
        self._assessment.insert("end", text, tag)
        self._assessment.config(state="disabled")

    # ── Analysis ──────────────────────────────────────────────────────────────

    def _start_analysis(self):
        if self._busy:
            return
        self._busy = True
        self._analyze_btn.config(state="disabled", text="  Analyzing…  ")
        self._status_lbl.config(text="Sending to AI…", fg=ACCENT)
        self._set_assessment("Analyzing system state…", "dim")
        self._show_placeholder("Waiting for response…")
        self._apply_btn.config(state="disabled")

        threading.Thread(target=self._run_analysis, daemon=True,
                         name="AdvisorAnalysis").start()

    def _run_analysis(self):
        try:
            from core import ai_advisor
            assessment, proposals = ai_advisor.analyze()
            self.root.after(0, self._on_analysis_done, assessment, proposals, None)
        except Exception as e:
            self.root.after(0, self._on_analysis_done, "", [], str(e))

    def _on_analysis_done(self, assessment: str, proposals: list, error: str):
        self._busy = False
        self._analyze_btn.config(state="normal", text="  Analyze  ")

        if error:
            self._set_assessment(f"Error: {error}", "dim")
            self._status_lbl.config(text="Analysis failed", fg=FG_ERR)
            self._show_placeholder("Analysis failed — check API key in Settings → AI.")
            return

        self._proposals = proposals
        self._set_assessment(assessment or "Analysis complete.")
        self._status_lbl.config(
            text=f"{len(proposals)} suggestion(s)" if proposals else "No changes needed",
            fg=FG_GOOD if proposals else FG_DIM,
        )
        self._render_proposals(proposals)

    # ── Apply ─────────────────────────────────────────────────────────────────

    def _apply_selected(self):
        selected = [p for p, v in zip(self._proposals, self._check_vars)
                    if v.get()]
        if not selected:
            messagebox.showinfo("Nothing selected",
                                "Check at least one change to apply.")
            return

        names = "\n".join(f"  • {p.label}  ({self._fmt_val(p.current)} → {self._fmt_val(p.proposed)})"
                          for p in selected)
        if not messagebox.askyesno(
                "Apply changes?",
                f"Apply {len(selected)} change(s)?\n\n{names}\n\n"
                "A backup will be saved automatically.",
                icon="question"):
            return

        try:
            from core import ai_advisor
            n = ai_advisor.apply_proposals(selected)
            self._status_lbl.config(text=f"{n} change(s) applied", fg=FG_GOOD)
            self._apply_btn.config(state="disabled")
            self._refresh_rollback_btn()
            messagebox.showinfo("Done",
                                f"{n} change(s) applied and saved.\n"
                                "AlienCore will use the new values on the next update cycle.")
        except Exception as e:
            messagebox.showerror("Apply failed", str(e))

    def _select_all(self):
        for v in self._check_vars:
            v.set(True)

    def _deselect_all(self):
        for v in self._check_vars:
            v.set(False)

    # ── Rollback ──────────────────────────────────────────────────────────────

    def _refresh_rollback_btn(self):
        from core import ai_advisor
        if ai_advisor.backup_exists():
            ts = ai_advisor.backup_timestamp()
            self._rollback_btn.config(text=f"Rollback  ({ts})")
            self._rollback_btn.pack(side="right", padx=4)
        else:
            try:
                self._rollback_btn.pack_forget()
            except Exception:
                pass

    def _rollback(self):
        from core import ai_advisor
        ts = ai_advisor.backup_timestamp()
        if not messagebox.askyesno(
                "Rollback",
                f"Restore config from backup saved at:\n{ts}\n\n"
                "This will undo all changes applied since that backup.",
                icon="warning"):
            return
        if ai_advisor.rollback():
            self._status_lbl.config(text="Rolled back", fg=FG_WARN)
            self._refresh_rollback_btn()
            messagebox.showinfo("Rolled back",
                                "Config restored from backup.")
        else:
            messagebox.showerror("Rollback failed",
                                 "Could not restore backup.")

    # ── Change log popup ─────────────────────────────────────────────────────

    def _show_log(self):
        from core import ai_advisor
        log_text = ai_advisor.get_change_log()

        win = tk.Toplevel(self.root)
        win.title("AI Change Log")
        win.configure(bg=BG)
        win.geometry("640x400")
        win.attributes("-topmost", True)

        txt = scrolledtext.ScrolledText(
            win, bg="#0d0d0d", fg=FG_DIM,
            font=FONT_MONO, wrap="none",
            relief="flat", bd=0, padx=10, pady=8,
        )
        txt.pack(fill="both", expand=True, padx=0, pady=0)
        txt.insert("end", log_text)
        txt.config(state="disabled")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def _on_close(self):
        global _instance
        _instance = None
        self.root.destroy()

    def run(self):
        self.root.mainloop()
