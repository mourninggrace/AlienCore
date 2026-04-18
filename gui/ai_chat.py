"""
AlienCore - ai_chat.py
Floating AI chat window.

  • Dark theme matching the rest of AlienCore
  • Prepends live sensor context with every message
  • API calls run in background thread — UI never freezes
  • Rolling history window (configurable in config.json → ai.chat_history_max)
  • "Clear" wipes history; "Context" shows the current system snapshot
"""

import tkinter as tk
from tkinter import scrolledtext
import json
import threading
import logging
from core import config_manager as cfg
from core import ai_manager, ai_tools

logger = logging.getLogger("aliencore.ai_chat")

# ── Palette ───────────────────────────────────────────────────────────────────
BG        = "#111318"
BG_INPUT  = "#1c1f27"
BG_MSG_U  = "#1a2a3a"   # user bubble
BG_MSG_A  = "#1e2218"   # assistant bubble
FG        = "#e0e0e0"
FG_DIM    = "#666666"
FG_USER   = "#7ec8ff"
FG_ASST   = "#a8e090"
FG_ERR    = "#ff6060"
FG_SYS    = "#888888"
ACCENT    = "#00aaff"
BORDER    = "#2a2d38"
FONT_CHAT = ("Segoe UI", 10)
FONT_MONO = ("Consolas", 9)

_instance = None   # module-level singleton so we don't open two windows


# ─────────────────────────────────────────────────────────────────────────────
# Confirm dialog — tiered by tool risk
# ─────────────────────────────────────────────────────────────────────────────

class _ConfirmDialog:
    """Modal dialog asking the user to approve an AI-initiated tool call.

    RISK_SOFT tools get a short description + Approve / Cancel.
    RISK_HIGH tools get the same, plus a damage warning, a liability
    disclaimer, and an 'I understand the risks' checkbox that must be
    ticked before Proceed becomes clickable.
    """

    _DISCLAIMER = (
        "WARNING — this action directly modifies hardware or Windows "
        "state and may cause system instability, application crashes, "
        "data loss, boot failure, or permanent damage to your hardware.\n\n"
        "By proceeding you accept full responsibility for any and all "
        "damage that results from this action. Neither AlienCore nor "
        "its developer accept any liability whatsoever for consequences "
        "arising from actions taken by the AI assistant on your behalf."
    )

    @classmethod
    def ask(cls, parent, tool: str, args: dict, risk: int) -> bool:
        meta = ai_tools.describe(tool)
        dlg  = cls(parent, tool, args, risk, meta)
        dlg.top.wait_window()
        return dlg.approved

    def __init__(self, parent, tool, args, risk, meta):
        self.approved = False
        is_high       = risk >= ai_tools.RISK_HIGH
        title         = "⚠  Risky AI Action" if is_high else "Confirm AI Action"
        accent        = "#ff6a4a" if is_high else ACCENT

        top = tk.Toplevel(parent)
        self.top = top
        top.title(title)
        top.configure(bg=BG)
        top.transient(parent)
        top.grab_set()
        top.resizable(False, False)
        try:
            from gui.tray import set_window_icon
            set_window_icon(top)
        except Exception:
            pass

        wrap = tk.Frame(top, bg=BG, padx=18, pady=14)
        wrap.pack(fill="both", expand=True)

        tk.Label(wrap, text=title, bg=BG, fg=accent,
                 font=("Segoe UI", 13, "bold")).pack(anchor="w")

        tk.Label(wrap, text="The AI assistant wants to run:",
                 bg=BG, fg=FG_DIM,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(10, 2))

        arg_str = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())
        tk.Label(wrap, text=f"  {tool}({arg_str})",
                 bg=BG, fg=FG, font=FONT_MONO,
                 wraplength=520, justify="left").pack(anchor="w")

        tk.Label(wrap, text=meta.get("description", ""),
                 bg=BG, fg=FG, font=("Segoe UI", 10),
                 wraplength=520, justify="left").pack(anchor="w", pady=(12, 0))

        extra = meta.get("warning") or ""
        if is_high:
            tk.Frame(wrap, bg="#3a1a1a", height=1).pack(fill="x", pady=(14, 10))
            if extra:
                tk.Label(wrap, text=extra, bg=BG, fg=FG,
                         font=("Segoe UI", 10, "bold"),
                         wraplength=520, justify="left").pack(anchor="w",
                                                              pady=(0, 8))
            tk.Label(wrap, text=self._DISCLAIMER,
                     bg=BG, fg="#ff9a80",
                     font=("Segoe UI", 9),
                     wraplength=520, justify="left").pack(anchor="w")

            self._ack_var = tk.BooleanVar(value=False)
            ack = tk.Checkbutton(
                wrap,
                text="I understand the risks and accept full responsibility.",
                variable=self._ack_var, command=self._update_proceed,
                bg=BG, fg=FG, activebackground=BG, activeforeground=FG,
                selectcolor=BG, font=("Segoe UI", 9, "bold"),
            )
            ack.pack(anchor="w", pady=(12, 4))

        # ── Buttons ──────────────────────────────────────────────────────────
        btns = tk.Frame(wrap, bg=BG)
        btns.pack(fill="x", pady=(16, 0))

        cancel_btn = tk.Button(
            btns, text="Cancel", command=self._cancel,
            bg="#2a2d38", fg=FG,
            activebackground="#353844", activeforeground=FG,
            font=("Segoe UI", 10), relief="flat",
            padx=18, pady=6, cursor="hand2",
        )
        cancel_btn.pack(side="right", padx=(8, 0))

        self._proceed_btn = tk.Button(
            btns, text="Proceed", command=self._approve,
            bg=accent, fg="#000000",
            activebackground=accent, activeforeground="#ffffff",
            font=("Segoe UI", 10, "bold"), relief="flat",
            padx=18, pady=6, cursor="hand2",
        )
        self._proceed_btn.pack(side="right")
        if is_high:
            self._proceed_btn.config(state="disabled",
                                     bg="#3a2a24", fg="#886055",
                                     cursor="arrow")

        top.protocol("WM_DELETE_WINDOW", self._cancel)
        top.bind("<Escape>", lambda e: self._cancel())

        # Center on parent
        top.update_idletasks()
        px = parent.winfo_rootx() + parent.winfo_width()  // 2 - top.winfo_width()  // 2
        py = parent.winfo_rooty() + parent.winfo_height() // 2 - top.winfo_height() // 2
        top.geometry(f"+{max(0, px)}+{max(0, py)}")
        top.focus_force()

    def _update_proceed(self):
        if getattr(self, "_ack_var", None) is None:
            return
        if self._ack_var.get():
            self._proceed_btn.config(state="normal",
                                     bg="#ff6a4a", fg="#000000",
                                     cursor="hand2")
        else:
            self._proceed_btn.config(state="disabled",
                                     bg="#3a2a24", fg="#886055",
                                     cursor="arrow")

    def _approve(self):
        self.approved = True
        self.top.destroy()

    def _cancel(self):
        self.approved = False
        self.top.destroy()


def open_chat():
    """Open the chat window (singleton — raises existing window if open)."""
    global _instance
    if _instance is not None:
        try:
            _instance.root.lift()
            _instance.root.focus_force()
            return
        except Exception:
            _instance = None

    _instance = AIChatWindow()
    _instance.run()   # blocks — call in a thread if needed from non-GUI context


def open_chat_thread():
    """Non-blocking: open the chat window in its own thread."""
    t = threading.Thread(target=open_chat, daemon=True, name="AIChatThread")
    t.start()


# ─────────────────────────────────────────────────────────────────────────────

class AIChatWindow:
    def __init__(self):
        global _instance
        _instance = self

        c        = cfg.get().get("ai", {})
        provider = c.get("provider", "anthropic")
        pname    = ai_manager.PROVIDERS.get(provider, provider)

        self.root = tk.Tk()
        self.root.title(f"AlienCore AI  —  {pname}")
        self.root.configure(bg=BG)
        from gui.tray import set_window_icon
        set_window_icon(self.root)
        self.root.geometry("680x560")
        self.root.minsize(480, 400)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Rolling conversation history passed to API
        self._history:  list  = []
        self._busy:     bool  = False
        self._max_hist: int   = cfg.get().get("ai", {}).get("chat_history_max", 20)

        self._build()
        self._append_system("AlienCore AI ready. Ask anything about your system, "
                            "settings, or performance.")

    def _build(self):
        # ── Toolbar ──────────────────────────────────────────────────────────
        bar = tk.Frame(self.root, bg=BORDER, pady=4)
        bar.pack(fill="x", side="top")

        tk.Label(bar, text="AlienCore AI", font=("Segoe UI", 11, "bold"),
                 bg=BORDER, fg=ACCENT).pack(side="left", padx=10)

        btn_style = {"bg": "#252830", "fg": FG_DIM, "relief": "flat",
                     "activebackground": "#333740", "activeforeground": FG,
                     "font": ("Segoe UI", 8), "padx": 8, "pady": 2, "cursor": "hand2"}

        tk.Button(bar, text="Context", command=self._show_context,
                  **btn_style).pack(side="right", padx=4)
        tk.Button(bar, text="Clear", command=self._clear_chat,
                  **btn_style).pack(side="right", padx=2)

        # Status label
        self._status = tk.Label(bar, text="", font=("Segoe UI", 8),
                                bg=BORDER, fg=FG_DIM)
        self._status.pack(side="right", padx=10)

        # ── Chat area ─────────────────────────────────────────────────────────
        chat_frame = tk.Frame(self.root, bg=BG)
        chat_frame.pack(fill="both", expand=True, padx=0, pady=0)

        self.chat = scrolledtext.ScrolledText(
            chat_frame,
            bg=BG, fg=FG,
            font=FONT_CHAT,
            wrap="word",
            state="disabled",
            relief="flat",
            bd=0,
            padx=12, pady=8,
            insertbackground=FG,
            selectbackground="#2a4a6a",
        )
        self.chat.pack(fill="both", expand=True)

        # Tag configuration
        self.chat.tag_config("user",   foreground=FG_USER, font=("Segoe UI", 10, "bold"))
        self.chat.tag_config("asst",   foreground=FG_ASST, font=FONT_CHAT)
        self.chat.tag_config("sys",    foreground=FG_SYS,  font=("Segoe UI", 9, "italic"))
        self.chat.tag_config("err",    foreground=FG_ERR,  font=FONT_CHAT)
        self.chat.tag_config("label",  foreground=FG_DIM,  font=("Segoe UI", 8))
        self.chat.tag_config("mono",   foreground=FG,      font=FONT_MONO)
        self.chat.tag_config("tool",   foreground="#b8a2ff",
                             font=("Consolas", 9, "bold"))
        self.chat.tag_config("tool_ok",  foreground="#90d890",  font=FONT_MONO)
        self.chat.tag_config("tool_err", foreground=FG_ERR,     font=FONT_MONO)
        self.chat.tag_config("tool_den", foreground="#d8b080",  font=FONT_MONO)

        # ── Separator ─────────────────────────────────────────────────────────
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x")

        # ── Input area ────────────────────────────────────────────────────────
        input_frame = tk.Frame(self.root, bg=BG_INPUT, pady=8)
        input_frame.pack(fill="x", side="bottom")

        self.entry = tk.Text(
            input_frame, height=3, bg=BG_INPUT, fg=FG,
            font=FONT_CHAT, wrap="word", relief="flat",
            insertbackground=FG, selectbackground="#2a4a6a",
            padx=10, pady=6,
        )
        self.entry.pack(fill="x", side="left", expand=True, padx=(10, 4), pady=2)
        self.entry.bind("<Return>",       self._on_return)
        self.entry.bind("<Shift-Return>", lambda e: None)   # allow newlines
        self.entry.focus_set()

        send_btn = tk.Button(
            input_frame, text="Send",
            bg=ACCENT, fg="#000000",
            activebackground="#0088cc", activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"),
            relief="flat", padx=14, pady=6, cursor="hand2",
            command=self._send,
        )
        send_btn.pack(side="right", padx=(0, 10), pady=2)

        hint = tk.Label(input_frame, text="Enter = send  ·  Shift+Enter = newline",
                        bg=BG_INPUT, fg=FG_DIM, font=("Segoe UI", 7))
        hint.place(relx=1.0, rely=1.0, anchor="se", x=-14, y=-2)

    # ── Chat helpers ──────────────────────────────────────────────────────────

    def _append(self, text: str, tag: str = "asst", newline: bool = True):
        self.chat.config(state="normal")
        if newline and self.chat.get("1.0", "end").strip():
            self.chat.insert("end", "\n")
        self.chat.insert("end", text, tag)
        self.chat.config(state="disabled")
        self.chat.see("end")

    def _append_system(self, text: str):
        self._append(f"  {text}", tag="sys")

    def _append_user(self, text: str):
        self._append("You\n", tag="label")
        self._append(text, tag="user")

    def _append_assistant(self, text: str):
        self._append("AlienCore AI\n", tag="label")
        # Detect code blocks
        if "```" in text:
            self._render_code_blocks(text)
        else:
            self._append(text, tag="asst", newline=False)

    def _render_code_blocks(self, text: str):
        """Render ``` code blocks in mono font, rest in normal."""
        parts = text.split("```")
        for i, part in enumerate(parts):
            if i % 2 == 0:
                if part.strip():
                    self._append(part, tag="asst", newline=False)
            else:
                # Strip language hint from first line
                lines = part.split("\n", 1)
                code  = lines[1] if len(lines) > 1 else lines[0]
                self._append(f"\n{code}\n", tag="mono", newline=False)

    def _set_status(self, text: str, color: str = FG_DIM):
        self._status.config(text=text, fg=color)

    # ── Events ────────────────────────────────────────────────────────────────

    def _on_return(self, event):
        # Shift+Return handled by binding above (does nothing → default insert)
        self._send()
        return "break"   # suppress default newline

    def _send(self):
        if self._busy:
            return
        text = self.entry.get("1.0", "end").strip()
        if not text:
            return

        self.entry.delete("1.0", "end")
        self._append_user(text)
        self._busy = True
        self._set_status("Thinking…", ACCENT)

        threading.Thread(
            target=self._call_api,
            args=(text,),
            daemon=True,
            name="AIChatCall",
        ).start()

    def _call_api(self, user_text: str):
        reply = ai_manager.send_chat_with_tools(
            user_text,
            list(self._history),
            confirm_fn=self._confirm_tool,
            on_event=self._dispatch_tool_event,
        )

        # Update rolling history — only the user text and the assistant's
        # final text are persisted across turns.  Tool-use blocks live
        # only inside the single turn that created them.
        self._history.append({"role": "user",      "content": user_text})
        self._history.append({"role": "assistant", "content": reply})
        if len(self._history) > self._max_hist * 2:
            self._history = self._history[-(self._max_hist * 2):]

        self.root.after(0, self._on_reply, reply)

    # ── Tool-use event plumbing ───────────────────────────────────────────────

    def _dispatch_tool_event(self, event: dict):
        """Called from the API thread — marshal onto the UI thread."""
        try:
            self.root.after(0, self._render_tool_event, event)
        except Exception:
            pass

    def _render_tool_event(self, event: dict):
        kind = event.get("kind")
        name = event.get("name", "?")
        if kind == "tool_call":
            args = event.get("args") or {}
            arg_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
            self._append(f"  ⚙  {name}({arg_str})", tag="tool")
        elif kind == "tool_result":
            msg = event.get("message", "")
            if event.get("declined"):
                self._append(f"       ✕ declined — {msg}", tag="tool_den")
            elif event.get("ok"):
                self._append(f"       ✓ {msg}", tag="tool_ok")
            else:
                self._append(f"       ✗ {msg}", tag="tool_err")

    def _confirm_tool(self, name: str, args: dict, risk: int) -> bool:
        """Called from the API thread — show a modal on the UI thread and
        block this thread until the user answers.

        Risk tiers:
          RISK_SOFT  → simple approve / cancel dialog
          RISK_HIGH  → full disclaimer dialog with damage warning,
                       liability clause, and an 'I understand' checkbox
                       that must be ticked before the Proceed button
                       becomes clickable.
        """
        evt    = threading.Event()
        result = {"approved": False}

        def ask():
            try:
                result["approved"] = _ConfirmDialog.ask(
                    parent=self.root, tool=name, args=args, risk=risk,
                )
            finally:
                evt.set()

        self.root.after(0, ask)
        evt.wait()
        return result["approved"]

    def _on_reply(self, reply: str):
        if reply.startswith("Error:"):
            self._append_assistant(reply)
            self._set_status("Error", FG_ERR)
        else:
            self._append_assistant(reply)
            self._set_status("", FG_DIM)
        self._busy = False

    def _show_context(self):
        ctx = ai_manager.get_system_context()
        self._append_system(f"Current system context:\n{ctx}")

    def _clear_chat(self):
        self._history = []
        self.chat.config(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.config(state="disabled")
        self._append_system("Chat cleared.")
        self._set_status("", FG_DIM)

    def _on_close(self):
        global _instance
        _instance = None
        self.root.destroy()

    def run(self):
        self.root.mainloop()
