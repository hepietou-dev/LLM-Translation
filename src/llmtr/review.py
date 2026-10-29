"""Block-level proof-reading GUI (tkinter), styled after the LLM-OCR app."""
from __future__ import annotations

import threading
from pathlib import Path

from . import proofread as pf

COLORS = {
    "bg": "#f2f5fb", "card": "#ffffff", "text": "#1f2937", "muted": "#64748b",
    "accent": "#2563eb", "accent_dark": "#1d4ed8", "danger": "#dc2626",
    "border": "#dbe4f0", "soft": "#e8eef9",
}


class ReviewState:
    """Holds the book, the issue queue and the save logic (GUI-independent)."""

    def __init__(self, book_path: Path):
        self.path = Path(book_path)
        self.book = pf.load(self.path)
        self.issues = pf.check(self.book)
        self.i = 0
        self.dirty = False
        self._by_id = {b.get("id"): b for b in self.book["blocks"]}

    # ---------------------------------------------------------------- queue
    def current(self):
        return self.issues[self.i] if self.issues else None

    def next(self):
        self.i = min(self.i + 1, max(0, len(self.issues) - 1))

    def prev(self):
        self.i = max(0, self.i - 1)

    def add_issues(self, items):
        seen = {(i["id"], i["reason"]) for i in self.issues}
        for it in items:
            if (it["id"], it["reason"]) in seen:
                continue
            self.issues.append(it)

    # ---------------------------------------------------------------- edits
    def block(self, block_id):
        return self._by_id.get(block_id)

    def set_zh(self, block_id, text: str) -> bool:
        b = self.block(block_id)
        if b is None:
            return False
        b["zh"] = text
        it = self.current()
        if it and it["id"] == block_id:
            it["zh"] = text
        self.dirty = True
        return True

    def suggestion(self):
        it = self.current()
        return (it or {}).get("suggest") or ""

    def apply_suggestion(self) -> bool:
        it = self.current()
        if not it or not it.get("suggest"):
            return False
        return self.set_zh(it["id"], it["suggest"])

    def save(self) -> Path:
        pf.save(self.book, self.path)
        self.dirty = False
        return self.path


def _styles(root):
    import tkinter as tk
    from tkinter import ttk

    c = COLORS
    root.configure(bg=c["bg"])
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    st.configure(".", font=("Microsoft YaHei UI", 10), background=c["card"], foreground=c["text"])
    st.configure("Root.TFrame", background=c["bg"])
    st.configure("TFrame", background=c["card"])
    st.configure("TLabel", background=c["card"], foreground=c["text"])
    st.configure("Title.TLabel", background=c["bg"], foreground=c["text"],
                 font=("Microsoft YaHei UI", 16, "bold"))
    st.configure("Muted.TLabel", background=c["card"], foreground=c["muted"])
    st.configure("MutedRoot.TLabel", background=c["bg"], foreground=c["muted"])
    st.configure("Badge.TLabel", background="#e0e7ff", foreground="#1e40af",
                 padding=(8, 2), font=("Microsoft YaHei UI", 9, "bold"))
    st.configure("Status.TLabel", background=c["bg"], foreground=c["accent"],
                 font=("Microsoft YaHei UI", 10, "bold"))
    st.configure("TLabelframe", background=c["card"], bordercolor=c["border"],
                 lightcolor=c["border"], darkcolor=c["border"], relief="solid", borderwidth=1)
    st.configure("TLabelframe.Label", background=c["card"], foreground=c["accent"],
                 font=("Microsoft YaHei UI", 10, "bold"))
    st.configure("TButton", background=c["soft"], foreground="#1e3a8a", padding=(12, 6),
                 borderwidth=0, relief="flat", focusthickness=0)
    st.map("TButton", background=[("active", "#dbe7fb"), ("disabled", "#eef1f6")],
           foreground=[("disabled", "#9aa4b2")])
    st.configure("Accent.TButton", background=c["accent"], foreground="white", padding=(18, 8),
                 borderwidth=0, relief="flat", font=("Microsoft YaHei UI", 10, "bold"),
                 focusthickness=0)
    st.map("Accent.TButton", background=[("active", c["accent_dark"]), ("disabled", "#9db8ea")])
    st.configure("Ghost.TButton", background=c["bg"], foreground="#1e3a8a", padding=(12, 6),
                 borderwidth=0, relief="flat")
    st.configure("TRadiobutton", background=c["bg"], foreground=c["text"])
    st.configure("Accent.Horizontal.TProgressbar", troughcolor="#e6ecf7", background=c["accent"],
                 lightcolor=c["accent"], darkcolor=c["accent"], bordercolor="#e6ecf7", thickness=10)


def launch(book_path: Path, out_docx: Path | None = None, preset: str = "print") -> None:
    import tkinter as tk
    from tkinter import ttk, messagebox

    state = ReviewState(Path(book_path))
    root = tk.Tk()
    root.title("LLM 译文校对 — %s" % state.path.parent.name)
    root.geometry("1280x940")
    root.minsize(1080, 760)
    c = COLORS
    _styles(root)

    # ---------------------------------------------------------------- header
    header = ttk.Frame(root, style="Root.TFrame", padding=(18, 14))
    header.pack(fill="x")
    ttk.Label(header, text="译文校对", style="Title.TLabel").pack(side="left")
    ttk.Label(header, text=state.path.parent.name, style="Badge.TLabel").pack(side="left", padx=(10, 0))
    stat = tk.StringVar(value=pf.summary(state.issues))
    ttk.Label(header, textvariable=stat, style="MutedRoot.TLabel").pack(side="left", padx=14)
    pos = tk.StringVar()
    ttk.Label(header, textvariable=pos, style="Status.TLabel").pack(side="right")

    # ---------------------------------------------------------------- body
    footer = ttk.Frame(root, style="Root.TFrame", padding=(18, 10))
    footer.pack(side="bottom", fill="x")
    content = ttk.Frame(root, style="Root.TFrame", padding=(18, 0))
    content.pack(fill="both", expand=True)
    content.columnconfigure(1, weight=1)
    content.rowconfigure(0, weight=1)

    side = ttk.LabelFrame(content, text="问题列表", padding=6)
    side.grid(row=0, column=0, sticky="ns", padx=(0, 12))
    lst = tk.Listbox(side, width=26, activestyle="none", borderwidth=0, highlightthickness=0,
                     bg="white", fg=c["text"], selectbackground="#dbeafe",
                     selectforeground="#1e3a8a", font=("Microsoft YaHei UI", 9))
    lst.pack(fill="both", expand=True)
    for it in state.issues:
        lst.insert("end", "p.%-4s %s" % (it["page"], it["reason"]))

    right = ttk.Frame(content, style="Root.TFrame")
    right.grid(row=0, column=1, sticky="nsew")
    for r, w in ((1, 3), (3, 4), (5, 2)):
        right.rowconfigure(r, weight=w)
    right.columnconfigure(0, weight=1)

    ttk.Label(right, text="原文", style="MutedRoot.TLabel").grid(row=0, column=0, sticky="w")
    f1 = ttk.LabelFrame(right, padding=6)
    f1.grid(row=1, column=0, sticky="nsew", pady=(2, 8))
    src = tk.Text(f1, wrap="word", font=("Consolas", 10), relief="flat", bg="#f8fafc",
                  padx=8, pady=6)
    src.pack(fill="both", expand=True)

    row2 = ttk.Frame(right, style="Root.TFrame")
    row2.grid(row=2, column=0, sticky="ew")
    ttk.Label(row2, text="译文（可直接编辑）", style="MutedRoot.TLabel").pack(side="left")
    dirty_lbl = ttk.Label(row2, text="", style="MutedRoot.TLabel")
    dirty_lbl.pack(side="right")

    f2 = ttk.LabelFrame(right, padding=6)
    f2.grid(row=3, column=0, sticky="nsew", pady=(2, 8))
    dst = tk.Text(f2, wrap="word", font=("Microsoft YaHei UI", 11), relief="flat",
                  bg="white", padx=8, pady=6, undo=True)
    dst.pack(fill="both", expand=True)

    row4 = ttk.Frame(right, style="Root.TFrame")
    row4.grid(row=4, column=0, sticky="ew")
    ttk.Label(row4, text="AI 建议", style="MutedRoot.TLabel").pack(side="left")
    apply_btn = ttk.Button(row4, text="采用建议", style="Ghost.TButton", state="disabled")
    apply_btn.pack(side="right")

    f3 = ttk.LabelFrame(right, padding=6)
    f3.grid(row=5, column=0, sticky="nsew")
    sug = tk.Text(f3, wrap="word", font=("Microsoft YaHei UI", 10), relief="flat",
                  bg="#fffdf5", padx=8, pady=6, height=3)
    sug.pack(fill="both", expand=True)
    sug.configure(state="disabled")

    # ---------------------------------------------------------------- footer
    hint = tk.StringVar(value="")
    ttk.Label(footer, textvariable=hint, style="MutedRoot.TLabel").pack(side="left")

    mode = tk.StringVar(value="suggest")
    ttk.Radiobutton(footer, text="人工校对（AI 建议）", value="suggest", variable=mode).pack(side="left", padx=(16, 4))
    ttk.Radiobutton(footer, text="AI 一键校对", value="auto", variable=mode).pack(side="left")

    ttk.Button(footer, text="开始 AI 校对", command=lambda: ai_pass()).pack(side="left", padx=12)
    ttk.Button(footer, text="重新排版", command=lambda: rebuild()).pack(side="right", padx=8)
    ttk.Button(footer, text="保存并下一条", style="Accent.TButton",
               command=lambda: save(True)).pack(side="right")

    # ---------------------------------------------------------------- actions
    def set_suggestion(text: str):
        sug.configure(state="normal")
        sug.delete("1.0", "end")
        sug.insert("1.0", text or "")
        sug.configure(state="disabled")
        apply_btn.configure(state="normal" if text else "disabled")

    def render():
        it = state.current()
        lst.selection_clear(0, "end")
        if it is None:
            pos.set("没有发现问题")
            src.delete("1.0", "end")
            dst.delete("1.0", "end")
            set_suggestion("")
            return
        lst.selection_set(state.i)
        lst.see(state.i)
        pos.set("第 %d / %d 条 · p.%s · %s" % (state.i + 1, len(state.issues), it["page"], it["reason"]))
        src.delete("1.0", "end")
        src.insert("1.0", it["source"])
        dst.delete("1.0", "end")
        dst.insert("1.0", it["zh"])
        set_suggestion(state.suggestion())
        dirty_lbl.config(text="已保存" if not state.dirty else "未保存")

    def save(go_next=True):
        it = state.current()
        if it is not None:
            state.set_zh(it["id"], dst.get("1.0", "end").strip())
        path = state.save()
        if go_next:
            state.next()
        render()
        hint.set("已写入 %s" % path.name)

    def step(d):
        if d > 0:
            save(False)
            state.next()
        else:
            state.prev()
        render()

    def jump(_evt=None):
        sel = lst.curselection()
        if sel:
            save(False)
            state.i = sel[0]
            render()

    def use_suggestion():
        if state.apply_suggestion():
            state.save()
            dst.delete("1.0", "end")
            dst.insert("1.0", state.suggestion())
            hint.set("已采用 AI 建议")
            render()
        else:
            hint.set("这条没有可用的建议")

    apply_btn.configure(command=use_suggestion)

    def rebuild():
        if out_docx is None:
            messagebox.showinfo("提示", "未指定输出路径，无法重排。")
            return
        save(False)
        hint.set("正在重新排版……")

        def work():
            try:
                from . import pipeline
                pipeline.rebuild(state.path, out_docx, preset)
                msg = "已重排：%s" % Path(out_docx).name
            except Exception as exc:                      # noqa: BLE001
                msg = "重排失败：%s" % exc
            root.after(0, lambda: hint.set(msg))

        threading.Thread(target=work, daemon=True).start()

    def ai_pass():
        import os
        from tkinter import simpledialog
        key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not key:
            key = simpledialog.askstring("AI 校对", "请输入 API Key：", show="*", parent=root) or ""
        if not key:
            return
        auto = mode.get() == "auto"
        hint.set("AI 一键校对中……" if auto else "AI 校对中（仅给建议）……")

        def work():
            try:
                from . import translate, proofread
                client = translate.Client("https://api.deepseek.com/v1", key, "deepseek-chat")
                if auto:
                    fixed, found = proofread.ai_fix(client, state.book["blocks"], limit=30)
                    state.save()
                    root.after(0, lambda: (render(), hint.set("AI 自动修正 %d 处并已保存" % fixed)))
                else:
                    found = proofread.ai_check(client, state.book["blocks"], limit=30)
                    state.add_issues(found)
                    for it in found:
                        root.after(0, lambda t=it: lst.insert("end", "p.%-4s %s" % (t["page"], t["reason"])))
                    root.after(0, lambda: (stat.set(pf.summary(state.issues)),
                                           hint.set("AI 建议 %d 条，点『采用建议』即可写入" % len(found))))
            except Exception as exc:                      # noqa: BLE001
                root.after(0, lambda e=exc: hint.set("AI 校对失败：%s" % e))

        threading.Thread(target=work, daemon=True).start()

    def on_close():
        if state.dirty:
            if not messagebox.askokcancel("退出", "有未保存的修改，保存后退出？"):
                return
        save(False)
        root.destroy()

    lst.bind("<<ListboxSelect>>", jump)
    root.bind("<Control-s>", lambda e: save())
    root.bind("<Control-Right>", lambda e: step(1))
    root.bind("<Control-Left>", lambda e: step(-1))
    root.protocol("WM_DELETE_WINDOW", on_close)
    render()
    root.mainloop()
