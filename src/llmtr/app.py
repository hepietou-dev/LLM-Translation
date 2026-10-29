"""Desktop entry for LLM Translation (tkinter, LLM-OCR style)."""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from llmtr import gloss as gloss_mod
from llmtr.stages import STAGES

# 时间占比权重：翻译是主项，其余阶段按经验值加权，用于更稳的剩余时间估计
STAGE_WEIGHTS = {"解析": 1, "结构": 1, "表格": 1, "翻译": 14, "修复": 2, "润色": 6,
                 "审校": 2, "图版": 4, "人名": 2, "排版": 1, "页码": 2}

PROVIDERS = {
    "DeepSeek": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat",
                 "price_in": 2.0, "price_out": 8.0},
    "OpenAI": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",
               "price_in": 1.1, "price_out": 4.3},
    "通义千问": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                 "model": "qwen-plus", "price_in": 0.8, "price_out": 2.0},
    "智谱 GLM": {"base_url": "https://open.bigmodel.cn/api/paas/v4",
                 "model": "glm-4-flash", "price_in": 0.0, "price_out": 0.0},
    "自定义（OpenAI 兼容）": {"base_url": "", "model": "", "price_in": 2.0, "price_out": 8.0},
}


def _settings_path() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "LLMTranslation" / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(_settings_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    try:
        p = _settings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass

COLORS = {
    "bg": "#f2f5fb", "card": "#ffffff", "text": "#1f2937", "muted": "#64748b",
    "accent": "#2563eb", "accent_dark": "#1d4ed8", "danger": "#dc2626", "border": "#dbe4f0",
}


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)          # noqa: SLF001
    return Path(__file__).resolve().parents[1]


def resolve_paths(cfg: dict, root: Path) -> dict:
    """Config-relative paths ('config/names.tsv') are relative to the app root."""
    paths = cfg.get("paths") or {}
    for k, v in paths.items():
        p = Path(v)
        paths[k] = str(p if p.is_absolute() else root / p)
    return cfg


def apply_theme(root) -> None:
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
    st.configure("Ok.TLabel", background=c["bg"], foreground="#059669",
                 font=("Microsoft YaHei UI", 10, "bold"))
    st.configure("Err.TLabel", background=c["bg"], foreground=c["danger"],
                 font=("Microsoft YaHei UI", 10, "bold"))
    st.configure("TLabelframe", background=c["card"], bordercolor=c["border"],
                 lightcolor=c["border"], darkcolor=c["border"], relief="solid", borderwidth=1)
    st.configure("TLabelframe.Label", background=c["card"], foreground=c["accent"],
                 font=("Microsoft YaHei UI", 10, "bold"))
    st.configure("TButton", background="#e8eef9", foreground="#1e3a8a", padding=(12, 6),
                 borderwidth=0, relief="flat", focusthickness=0)
    st.map("TButton", background=[("active", "#dbe7fb"), ("disabled", "#eef1f6")],
           foreground=[("disabled", "#9aa4b2")])
    st.configure("Accent.TButton", background=c["accent"], foreground="white", padding=(20, 9),
                 borderwidth=0, relief="flat", font=("Microsoft YaHei UI", 10, "bold"),
                 focusthickness=0)
    st.map("Accent.TButton", background=[("active", c["accent_dark"]), ("disabled", "#c9d8f2")],
           foreground=[("disabled", "#5f7499")])
    st.configure("Danger.TButton", background=c["danger"], foreground="white", padding=(16, 8),
                 borderwidth=0, relief="flat", font=("Microsoft YaHei UI", 10, "bold"),
                 focusthickness=0)
    st.map("Danger.TButton",
           background=[("active", "#b91c1c"), ("disabled", "#eef1f6")],
           foreground=[("disabled", "#aab4c2")])
    st.configure("Field.TEntry", fieldbackground="white", bordercolor=c["border"],
                 lightcolor=c["border"], darkcolor=c["border"], padding=(8, 6),
                 relief="flat")
    st.map("Field.TEntry",
           bordercolor=[("focus", c["accent"])],
           lightcolor=[("focus", c["accent"])],
           darkcolor=[("focus", c["accent"])])
    st.configure("Field.TCombobox", fieldbackground="white", background="white",
                 bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"],
                 arrowcolor=c["muted"], padding=(8, 5), relief="flat")
    st.map("Field.TCombobox",
           fieldbackground=[("readonly", "white"), ("disabled", "#f4f6fa")],
           background=[("readonly", "white"), ("active", "#eef4ff")],
           foreground=[("disabled", "#9aa4b2")],
           bordercolor=[("focus", c["accent"])],
           lightcolor=[("focus", c["accent"])],
           darkcolor=[("focus", c["accent"])],
           arrowcolor=[("active", c["accent"])])
    st.configure("Vertical.TScrollbar", background="#cfd9e8", troughcolor=c["card"],
                 bordercolor=c["card"], arrowcolor=c["muted"], arrowsize=10,
                 borderwidth=0, relief="flat")
    st.map("Vertical.TScrollbar", background=[("active", "#b7c6dd")])
    st.configure("Accent.Horizontal.TProgressbar", troughcolor="#edf1f8", background=c["accent"],
                 lightcolor=c["accent"], darkcolor=c["accent"], bordercolor="#edf1f8", thickness=10)
    root.option_add("*TCombobox*Listbox.font", ("Microsoft YaHei UI", 10))
    root.option_add("*TCombobox*Listbox.background", "white")
    root.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", "white")


def _rounded(cv: tk.Canvas, x1: float, y1: float, x2: float, y2: float, r: float, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
           x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


class CheckChip(tk.Frame):
    """Option pill with a hand-drawn √ box (ttk's clam theme paints an x)."""

    def __init__(self, parent, text: str, variable: tk.Variable, *, bg: str = COLORS["card"]):
        super().__init__(parent, bg=bg, padx=6, pady=3, cursor="hand2", takefocus=1)
        self.var = variable
        self.bg = bg
        self.enabled = True
        self._hover = False
        self._focus = False
        try:
            scale = float(self.tk.call("tk", "scaling")) / 1.3333
        except (tk.TclError, ValueError):
            scale = 1.0
        self._size = max(14, int(round(18 * scale)))
        self.canvas = tk.Canvas(self, width=self._size, height=self._size, bg=bg,
                                highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.pack(side="left", padx=(0, 6))
        self.label = tk.Label(self, text=text, bg=bg, fg=COLORS["text"],
                              font=("Microsoft YaHei UI", 10), cursor="hand2")
        self.label.pack(side="left")
        self._trace = variable.trace_add("write", lambda *_: self._draw())
        for w in (self, self.canvas, self.label):
            w.bind("<Button-1>", self._toggle)
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)
        self.bind("<space>", self._toggle)
        self.bind("<Return>", self._toggle)
        self.bind("<FocusIn>", self._on_focus)
        self.bind("<FocusOut>", self._on_focus)
        self._paint()

    def _on(self) -> bool:
        return bool(self.var.get())

    def _paint(self) -> None:
        col = self.bg if not (self.enabled and self._hover) else "#eef4ff"
        self.configure(bg=col)
        self.canvas.configure(bg=col)
        self.label.configure(bg=col, fg=COLORS["muted"] if not self.enabled else COLORS["text"])
        self._draw()

    def _draw(self) -> None:
        cv, s = self.canvas, self._size
        cv.delete("all")
        pad = max(1, round(s * 0.09))
        r = max(3, round(s * 0.26))
        on = self._on()
        if not self.enabled:
            fill = "#eef1f6" if on else "white"
            border, mark = "#d7dee8", "#a6b0bf"
        elif on:
            fill = border = COLORS["accent"]
            mark = "white"
        else:
            fill = "white"
            border = "#9db8ea" if self._hover else "#c3cfdf"
            mark = "#c7d9fb" if self._hover else ""
        if self._focus:
            _rounded(cv, 1, 1, s - 2, s - 2, r + 1, fill="",
                     outline=COLORS["accent"], width=1)
        _rounded(cv, pad, pad, s - pad, s - pad, r, fill=fill, outline=border,
                 width=max(1, round(s / 14)))
        if on or (self._hover and self.enabled):
            w = max(2, round(s * 0.11))
            cv.create_line(round(s * 0.29), round(s * 0.52),
                           round(s * 0.43), round(s * 0.67),
                           round(s * 0.72), round(s * 0.36),
                           fill=mark, width=w, capstyle="round", joinstyle="round")

    def _toggle(self, _event=None):
        if not self.enabled:
            return "break"
        self.focus_set()
        self.var.set(not self._on())
        return "break"

    def _enter(self, _event=None) -> None:
        if self.enabled and not self._hover:
            self._hover = True
            self._paint()

    def _leave(self, _event=None) -> None:
        if not self._hover:
            return
        w = self.winfo_containing(*self.winfo_pointerxy())
        while w is not None:
            if w is self:
                return
            w = getattr(w, "master", None)
        self._hover = False
        self._paint()

    def _on_focus(self, _event=None) -> None:
        w = self.focus_get()
        self._focus = w is self or (w is not None and str(w).startswith(str(self)))
        self._draw()

    def set_enabled(self, on: bool) -> None:
        self.enabled = bool(on)
        cur = "hand2" if on else "arrow"
        self.configure(cursor=cur, takefocus=1 if on else 0)
        self.canvas.configure(cursor=cur)
        self.label.configure(cursor=cur)
        self._hover = False
        self._paint()

    def destroy(self) -> None:
        try:
            self.var.trace_remove("write", self._trace)
        except (tk.TclError, ValueError):
            pass
        super().destroy()


class App:
    HINT = "提示：Ctrl+O 选择文件 · Ctrl+R 开始 · Esc 中止"

    def __init__(self, root: tk.Tk, initial: dict | None = None) -> None:
        self.root = root
        self.initial = initial or {}
        self.auto_start = bool(self.initial.get("start"))
        self.logq: queue.Queue[str] = queue.Queue()
        self.doneq: queue.Queue[bool] = queue.Queue()
        self._log_lines = 0
        self.worker = None
        self.book_json: Path | None = None
        self.out_dir: Path | None = None
        self.stop_event = threading.Event()
        self.watcher: threading.Thread | None = None
        self.result: dict | None = None
        self.run_t0: float | None = None
        self.stopping = False
        self.prog_lock = threading.Lock()
        self.prog = {"stage": "", "done": 0, "total": 1, "idx": 0,
                     "n": len(STAGES), "t": 0.0}
        self.eta_ema: float | None = None

        root.title("LLM Translation — 英文 PDF / EPUB / DOCX 书籍翻译")
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = min(1080, max(900, sw - 80)), min(880, max(620, sh - 120))
        root.geometry("%dx%d" % (w, h))
        root.minsize(min(900, w), min(620, h))
        try:
            ico = app_root() / "assets" / "app.ico"
            if ico.exists():
                root.iconbitmap(default=str(ico))
        except Exception:
            pass
        apply_theme(root)

        header = ttk.Frame(root, style="Root.TFrame", padding=(18, 14))
        header.pack(fill="x")
        ttk.Label(header, text="LLM Translation", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="PDF / EPUB / DOCX → 中文 DOCX", style="Badge.TLabel").pack(side="left", padx=(10, 0))
        ttk.Label(header, text="离线排版 · 仅调用翻译 API", style="MutedRoot.TLabel").pack(side="left", padx=14)

        card = ttk.LabelFrame(root, text="文件与选项", padding=14)
        card.pack(fill="x", padx=18, pady=(0, 12))
        card.columnconfigure(1, weight=1)

        ttk.Label(card, text="PDF / EPUB / DOCX 文件").grid(row=0, column=0, sticky="w")
        self.pdf_var = tk.StringVar()
        self.pdf_entry = ttk.Entry(card, textvariable=self.pdf_var, style="Field.TEntry")
        self.pdf_entry.grid(row=0, column=1, sticky="ew", padx=8)
        self.pdf_btn = ttk.Button(card, text="选择…", command=self.pick_pdf, cursor="hand2")
        self.pdf_btn.grid(row=0, column=2)

        ttk.Label(card, text="输出目录").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.out_var = tk.StringVar()
        self.out_entry = ttk.Entry(card, textvariable=self.out_var, style="Field.TEntry")
        self.out_entry.grid(row=1, column=1, sticky="ew", padx=8, pady=(8, 0))
        self.out_btn = ttk.Button(card, text="选择…", command=self.pick_out, cursor="hand2")
        self.out_btn.grid(row=1, column=2, pady=(8, 0))

        ttk.Label(card, text="排版预设").grid(row=2, column=0, sticky="w", pady=(8, 0))
        from llmtr.build import PRESET_LABELS
        self.preset = tk.StringVar(value=PRESET_LABELS["print"])
        self.preset_box = ttk.Combobox(card, textvariable=self.preset,
                                       values=list(PRESET_LABELS.values()),
                                       state="readonly", width=12, style="Field.TCombobox",
                                       cursor="hand2")
        self.preset_box.grid(row=2, column=1, sticky="w", padx=8, pady=(8, 0))

        opts = ttk.Frame(card)
        opts.grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))
        saved = load_settings()
        self.notes_var = tk.BooleanVar(value=saved.get("notes", True))
        self.bib_var = tk.BooleanVar(value=saved.get("bib", False))
        self.ocr_var = tk.BooleanVar(value=saved.get("ocr", True))
        self.judge_var = tk.BooleanVar(value=saved.get("judge", True))
        self.dry_var = tk.BooleanVar(value=False)
        self.preview_var = tk.BooleanVar(value=saved.get("preview", True))
        self.page_tags_var = tk.BooleanVar(value=saved.get("page_tags", False))
        self.polish_var = tk.BooleanVar(value=saved.get("polish", True))
        self.chips: dict[str, CheckChip] = {}
        layout = [  # (row, column, key, label, variable)
            (0, 0, "notes", "翻译注释", self.notes_var),
            (0, 1, "bib", "翻译参考文献", self.bib_var),
            (0, 2, "page_tags", "保留原书页码", self.page_tags_var),
            (0, 3, "preview", "保留 PDF 预览", self.preview_var),
            (1, 0, "ocr", "扫描件自动 OCR", self.ocr_var),
            (1, 1, "judge", "低质页 AI 判读", self.judge_var),
            (1, 2, "polish", "译文润色", self.polish_var),
        ]
        for row, col, key, text, var in layout:
            chip = CheckChip(opts, text, var)
            chip.grid(row=row, column=col, sticky="w", padx=(0, 16), pady=(0, 2))
            self.chips[key] = chip
        self.judge_chip = self.chips["judge"]
        self.dry_chip = CheckChip(opts, "试运行（不调用 API）", self.dry_var)
        self.dry_chip.grid(row=1, column=3, sticky="w", padx=(0, 16), pady=(0, 2))
        self.chips["dry"] = self.dry_chip

        gloss_row = ttk.Frame(opts)
        gloss_row.grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Label(gloss_row, text="英文原词").pack(side="left")
        saved_mode = saved.get("gloss", gloss_mod.DEFAULT_MODE)
        self.gloss_var = tk.StringVar(
            value=gloss_mod.LABELS.get(saved_mode, gloss_mod.LABELS[gloss_mod.DEFAULT_MODE]))
        self.gloss_box = ttk.Combobox(gloss_row, textvariable=self.gloss_var,
                                      values=[gloss_mod.LABELS[m] for m in gloss_mod.MODES],
                                      state="readonly", width=12,
                                      style="Field.TCombobox", cursor="hand2")
        self.gloss_box.pack(side="left", padx=(6, 8))
        ttk.Label(gloss_row, text="书名、人名、专名在括号里保留英文原文的程度",
                  style="Muted.TLabel").pack(side="left")

        ttk.Label(card, text="API 服务商").grid(row=4, column=0, sticky="w", pady=(8, 0))
        prov = saved.get("provider", "DeepSeek")
        self.provider_var = tk.StringVar(value=prov if prov in PROVIDERS else "DeepSeek")
        self.provider_box = ttk.Combobox(card, textvariable=self.provider_var,
                                         values=list(PROVIDERS), state="readonly",
                                         width=20, style="Field.TCombobox", cursor="hand2")
        self.provider_box.grid(row=4, column=1, sticky="w", padx=8, pady=(8, 0))
        self.provider_box.bind("<<ComboboxSelected>>", self.on_provider)
        ttk.Label(card, text="任意 OpenAI 兼容接口", style="Muted.TLabel").grid(
            row=4, column=2, sticky="w", pady=(8, 0))

        ttk.Label(card, text="API 地址").grid(row=5, column=0, sticky="w", pady=(8, 0))
        self.base_var = tk.StringVar(value=saved.get("base_url", PROVIDERS["DeepSeek"]["base_url"]))
        self.base_entry = ttk.Entry(card, textvariable=self.base_var, style="Field.TEntry")
        self.base_entry.grid(row=5, column=1, sticky="ew", padx=8, pady=(8, 0))
        mrow = ttk.Frame(card)
        mrow.grid(row=5, column=2, sticky="w", pady=(8, 0))
        ttk.Label(mrow, text="模型").pack(side="left")
        self.model_var = tk.StringVar(value=saved.get("model", PROVIDERS["DeepSeek"]["model"]))
        self.model_entry = ttk.Entry(mrow, textvariable=self.model_var, width=18,
                                     style="Field.TEntry")
        self.model_entry.pack(side="left", padx=(4, 0))

        ttk.Label(card, text="API Key").grid(row=6, column=0, sticky="w", pady=(8, 0))
        env_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        self.key_var = tk.StringVar(value=env_key)
        self.key_entry = ttk.Entry(card, textvariable=self.key_var, show="*", style="Field.TEntry")
        self.key_entry.grid(row=6, column=1, sticky="ew", padx=8, pady=(8, 0))
        self.key_hint = ttk.Label(card, text="仅存本机内存", style="Muted.TLabel")
        self.key_hint.grid(row=6, column=2, sticky="w", pady=(8, 0))

        prog = ttk.Frame(card)
        prog.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        prog.columnconfigure(0, weight=1)
        prog.columnconfigure(1, minsize=350)
        self.bar = ttk.Progressbar(prog, style="Accent.Horizontal.TProgressbar",
                                   mode="determinate", maximum=100, length=520)
        self.bar.grid(row=0, column=0, sticky="ew")
        self.meter = tk.StringVar(value="等待开始")
        ttk.Label(prog, textvariable=self.meter, style="Muted.TLabel",
                  anchor="w").grid(row=0, column=1, sticky="w", padx=(10, 0))

        self.stage_strip = ttk.Label(card, text=self.HINT, style="Muted.TLabel")
        self.stage_strip.grid(row=8, column=0, columnspan=3, sticky="w", pady=(8, 0))

        self._input_widgets = [(self.pdf_entry, "normal"), (self.pdf_btn, "normal"),
                               (self.out_entry, "normal"), (self.out_btn, "normal"),
                               (self.preset_box, "readonly"), (self.provider_box, "readonly"),
                               (self.gloss_box, "readonly"),
                               (self.base_entry, "normal"), (self.model_entry, "normal"),
                               (self.key_entry, "normal")]
        self._running = False

        footer = ttk.Frame(root, style="Root.TFrame", padding=(18, 12))
        footer.pack(side="bottom", fill="x")
        self.status = tk.StringVar(value="就绪")
        self.status_lbl = ttk.Label(footer, textvariable=self.status, style="MutedRoot.TLabel")
        self.status_lbl.pack(side="left")
        self.result_bar = ttk.Frame(footer, style="Root.TFrame")
        self.result_bar.pack(side="left", padx=(14, 0))
        self.start_btn = ttk.Button(footer, text="开始翻译", style="Accent.TButton",
                                    command=self.start, cursor="hand2")
        self.start_btn.pack(side="right")
        self.stop_btn = ttk.Button(footer, text="中止", style="Danger.TButton",
                                   command=self.abort, state="disabled")
        self.stop_btn.pack(side="right", padx=8)
        self.review_btn = None

        logf = ttk.LabelFrame(root, text="运行日志", padding=8)
        logf.pack(fill="both", expand=True, padx=18)
        logbox = ttk.Frame(logf)
        logbox.pack(fill="both", expand=True)
        self.log = tk.Text(logbox, wrap="word", relief="flat", background="white",
                           font=("Consolas", 9), padx=8, pady=6, state="disabled",
                           height=8, selectbackground="#cfe0ff", selectforeground=COLORS["text"])
        self.log.pack(side="left", fill="both", expand=True)
        log_sb = ttk.Scrollbar(logbox, orient="vertical", command=self.log.yview)
        log_sb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=log_sb.set)
        self.log.tag_configure("ok", foreground="#059669")
        self.log.tag_configure("err", foreground="#dc2626")

        if self.initial.get("pdf"):
            self.pdf_var.set(str(self.initial["pdf"]))
        if self.initial.get("out"):
            self.out_var.set(str(self.initial["out"]))
        preset = self.initial.get("preset")
        if preset:
            from llmtr.build import PRESET_LABELS
            self.preset.set(PRESET_LABELS.get(preset, preset))

        self.ocr_var.trace_add("write", lambda *_: self._sync_options())
        self.dry_var.trace_add("write", lambda *_: self._sync_options())
        self._sync_options()
        root.bind("<Control-o>", self._hotkey_pick)
        root.bind("<Control-r>", self._hotkey_start)
        root.bind("<Escape>", self._hotkey_abort)

        root.after(120, self.drain)
        root.after(300, self.check_tools)
        root.after(700, self._maybe_autostart)

    def _maybe_autostart(self) -> None:
        if self.auto_start and not (self.worker is not None and self.worker.is_alive()):
            self.start()

    def _hotkey_pick(self, _event=None):
        if not self._running:
            self.pick_pdf()
        return "break"

    def _hotkey_start(self, _event=None):
        if not self._running:
            self.start()
        return "break"

    def _hotkey_abort(self, _event=None):
        if self._running and not self.stopping:
            self.abort()
        return "break"

    def _status(self, text: str, kind: str = "muted") -> None:
        self.status.set(text)
        self.status_lbl.configure(style={"ok": "Ok.TLabel", "err": "Err.TLabel",
                                         "run": "Status.TLabel"}.get(kind, "MutedRoot.TLabel"))

    def _sync_options(self) -> None:
        """Keep dependent controls in step with the current option state."""
        self.judge_chip.set_enabled(not self._running and bool(self.ocr_var.get()))
        dry = bool(self.dry_var.get())
        self.key_entry.configure(state="disabled" if (self._running or dry) else "normal")
        self.key_hint.configure(text="试运行不调用 API" if dry else "仅存本机内存")

    def _set_running(self, on: bool) -> None:
        self._running = bool(on)
        for widget, normal in self._input_widgets:
            widget.configure(state="disabled" if on else normal)
        for chip in self.chips.values():
            chip.set_enabled(not on)
        if not on:
            self._sync_options()
        self.start_btn.configure(state="disabled" if on else "normal",
                                 text="翻译中…" if on else "开始翻译")

    # ------------------------------------------------------------------
    @staticmethod
    def fmt_clock(seconds: float) -> str:
        s = max(0, int(round(seconds)))
        if s < 3600:
            return "%02d:%02d" % (s // 60, s % 60)
        return "%d:%02d:%02d" % (s // 3600, (s % 3600) // 60, s % 60)

    def on_provider(self, *_event) -> None:
        preset = PROVIDERS.get(self.provider_var.get()) or {}
        if preset.get("base_url"):
            self.base_var.set(preset["base_url"])
        if preset.get("model"):
            self.model_var.set(preset["model"])

    def elapsed(self) -> float:
        return time.monotonic() - self.run_t0 if self.run_t0 is not None else 0.0

    def log_line(self, text: str, tag: str | None = None) -> None:
        prefix = "[%s] " % self.fmt_clock(self.elapsed()) if self.run_t0 is not None else ""
        at_end = self.log.yview()[1] >= 0.999
        self.log.configure(state="normal")
        self.log.insert("end", prefix + text + "\n", tag or "")
        self._log_lines += 1
        if self._log_lines > 1600:          # keep the widget light on long books
            self.log.delete("1.0", "601.0")
            self._log_lines -= 600
        if at_end:
            self.log.see("end")
        self.log.configure(state="disabled")

    def drain(self) -> None:
        while True:
            try:
                msg, tag = self.logq.get_nowait()
            except queue.Empty:
                break
            self.log_line(msg, tag)
        while True:
            try:
                ok = self.doneq.get_nowait()
            except queue.Empty:
                break
            self.finish(ok)
        self.render_progress()
        self.root.after(120, self.drain)

    def on_progress(self, stage: str, done: int, total: int, *_stage_pos) -> None:
        """Worker-thread callback: update state only, never touch Tk directly."""
        now = time.monotonic()
        idx = int(_stage_pos[0]) if _stage_pos else 0
        nstage = int(_stage_pos[1]) if len(_stage_pos) > 1 else len(STAGES)
        with self.prog_lock:
            if stage != self.prog["stage"]:
                self.prog["t"] = now
            self.prog.update({"stage": stage, "done": int(done or 0),
                              "total": int(total or 1), "idx": idx, "n": nstage})

    def render_progress(self) -> None:
        if self.run_t0 is None:
            return
        with self.prog_lock:
            p = dict(self.prog)
        idx, n = p["idx"], max(1, p["n"])
        frac = p["done"] / p["total"] if p["total"] else 0.0
        overall = (((idx - 1) + frac) / n) if idx else frac
        overall = max(0.0, min(1.0, overall))
        elapsed = self.elapsed()
        self.bar.configure(value=round(overall * 100, 1))
        if p["stage"]:
            self.stage_strip.configure(
                text="   ".join(("▶%d %s" % (i, name)) if i == idx else ("%d %s" % (i, name))
                                for i, name in enumerate(STAGES, 1)))
            step = "%d/%d" % (idx, n) if idx else "-"
            detail = "%d/%d" % (p["done"], p["total"]) if p["total"] > 1 else ""
            self.status.set("阶段 %s %s%s · 总进度 %d%%"
                            % (step, p["stage"], (" · " + detail) if detail else "",
                               round(overall * 100)))
        parts = ["已用 %s" % self.fmt_clock(elapsed)]
        before = sum(STAGE_WEIGHTS.get(x, 1) for x in STAGES[:max(0, idx - 1)])
        wsum = sum(STAGE_WEIGHTS.values())
        wfrac = (before + STAGE_WEIGHTS.get(p["stage"], 1) * frac) / wsum if idx else 0.0
        wfrac = max(0.0, min(1.0, wfrac))
        if wfrac >= 0.02 and elapsed > 3:
            raw = elapsed * (1 - wfrac) / wfrac
            prev = getattr(self, "eta_ema", None)
            self.eta_ema = raw if prev is None else 0.35 * raw + 0.65 * prev
            parts.append("预计剩余 ~%s" % self.fmt_clock(self.eta_ema))
        else:
            parts.append("预计剩余 计算中…")
        if p["stage"] and p["t"]:
            stage_time = "本阶段 %s" % self.fmt_clock(time.monotonic() - p["t"])
            if p["total"] > 1:
                stage_time += " (%d/%d)" % (p["done"], p["total"])
            parts.append(stage_time)
        if getattr(self, "stopping", False):
            parts.insert(0, "正在中止")
        self.meter.set(" · ".join(parts))

    def abort(self) -> None:
        self.stopping = True
        self.stop_event.set()
        self.stop_btn.configure(state="disabled", text="正在中止…")
        self._status("正在中止，等待当前请求返回…", "err")
        self.log_line("收到中止请求……", "err")

    def check_tools(self) -> None:
        """Report whether TOC page numbering is available on this machine."""
        try:
            from llmtr import pipeline
            from llmtr.app import app_root
            cfg_path = app_root() / "config" / "config.json"
            cfg = pipeline.load_config(cfg_path)
            kit = (cfg.get("figures") or {})
            has_kit = bool(kit.get("node") and kit.get("cli")
                           and Path(kit["node"]).exists() and Path(kit["cli"]).exists())
            soffice = pipeline.find_soffice(cfg)
            if has_kit or soffice:
                self.log_line("目录页码：可用（%s）" % ("LibreOffice Kit" if has_kit else "LibreOffice"),
                              "ok")
            else:
                self.log_line("目录页码：不可用 —— 未找到 LibreOffice，目录将不含页码", "err")
        except Exception as exc:                          # noqa: BLE001
            self.log_line("工具检查失败：%s" % exc, "err")

    def pick_pdf(self) -> None:
        p = filedialog.askopenfilename(
            filetypes=[("电子书/文稿", "*.pdf *.epub *.docx"), ("PDF", "*.pdf"),
                       ("EPUB", "*.epub"), ("Word 文档", "*.docx")])
        if p:
            self.pdf_var.set(p)
            if not self.out_var.get():
                self.out_var.set(str(Path(p).with_suffix("")) + "_out")

    def pick_out(self) -> None:
        d = filedialog.askdirectory()
        if d:
            self.out_var.set(d)

    def start(self) -> None:
        pdf = Path(self.pdf_var.get().strip())
        if not pdf.exists():
            messagebox.showwarning("提示", "请先选择 PDF、EPUB 或 DOCX 文件")
            return
        if self.worker is not None and self.worker.is_alive():
            return
        out = Path(self.out_var.get().strip() or (pdf.parent / (pdf.stem + "_out")))
        key = self.key_var.get().strip() or os.environ.get("DEEPSEEK_API_KEY", "") \
            or os.environ.get("OPENAI_API_KEY", "")
        if not key and not self.dry_var.get():
            messagebox.showwarning("提示", "请填写 API Key（或勾选试运行）")
            return
        base = self.base_var.get().strip()
        model = self.model_var.get().strip()
        if not self.dry_var.get() and (not base or not model):
            messagebox.showwarning("提示", "请填写 API 地址与模型名称")
            return
        self._set_running(True)
        self.stop_btn.configure(state="normal", text="中止")
        self.show_result_buttons(False)

        self.stop_event.clear()
        self.stopping = False
        self.result = None
        self.run_t0 = time.monotonic()
        self.eta_ema = None
        with self.prog_lock:
            self.prog.update({"stage": "", "done": 0, "total": 1, "idx": 0,
                              "n": len(STAGES), "t": 0.0})
        self.bar["value"] = 0
        self.meter.set("已用 00:00")
        self.stage_strip.configure(text="   ".join("%d %s" % (i, name)
                                                   for i, name in enumerate(STAGES, 1)))
        self._status("启动中…", "run")
        self.log_line("=" * 60)
        self.log_line("开始：%s" % pdf.name)
        prov = self.provider_var.get()
        preset_info = PROVIDERS.get(prov) or {}
        gloss_mode = {v: k for k, v in gloss_mod.LABELS.items()}.get(
            self.gloss_var.get(), gloss_mod.DEFAULT_MODE)
        cfg_over = {"tables": {"mode": "auto"},
                    "page_tags": bool(self.page_tags_var.get()),
                    "source_gloss": {"mode": gloss_mode},
                    "translate": {"notes": self.notes_var.get(),
                                  "bibliography": self.bib_var.get()},
                    "polish": {"enabled": bool(self.polish_var.get())},
                    "ocr": {"mode": "auto" if self.ocr_var.get() else "off",
                            "judge": bool(self.judge_var.get())},
                    "api": {"provider": prov, "base_url": base, "model": model,
                            "price_in": preset_info.get("price_in", 2.0),
                            "price_out": preset_info.get("price_out", 8.0)}}
        keep_pdf = bool(self.preview_var.get())
        from llmtr.build import LABEL_TO_PRESET
        preset = LABEL_TO_PRESET.get(self.preset.get(), "print")
        dry = bool(self.dry_var.get())
        save_settings({"provider": prov, "base_url": base, "model": model,
                       "preset": preset, "notes": self.notes_var.get(),
                       "bib": self.bib_var.get(), "ocr": self.ocr_var.get(),
                       "judge": bool(self.judge_var.get()),
                       "polish": bool(self.polish_var.get()),
                       "preview": keep_pdf, "page_tags": bool(self.page_tags_var.get()),
                       "gloss": gloss_mode})
        if dry:
            self.log_line("服务商 %s · 模型 %s · 英文原词 %s"
                          % (prov, model or "-", gloss_mod.LABELS[gloss_mode]))
        else:
            self.log_line("服务商 %s · 模型 %s · 地址 %s · 英文原词 %s"
                          % (prov, model, base, gloss_mod.LABELS[gloss_mode]))
        cache_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "LLMTranslation" / "cache"
        self.worker = threading.Thread(
            target=self.run_job,
            args=(pdf, out, key, cfg_over, keep_pdf, cache_dir, preset, dry),
            daemon=True)
        self.worker.start()

    def run_job(self, pdf: Path, out: Path, key: str, cfg_over: dict,
                keep_pdf: bool, cache_dir: Path, preset: str, dry: bool) -> None:
        try:
            from llmtr import pipeline
            root_dir = app_root()
            cfg_path = root_dir / "config" / "config.json"
            cfg = pipeline.load_config(cfg_path)
            resolve_paths(cfg, root_dir)
            cfg["api"]["key"] = key
            for k, v in cfg_over.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
            res = pipeline.run(pdf, out, cfg,
                               preset=preset,
                               dry_run=dry,
                               log=lambda s: self.logq.put((str(s), None)),
                               progress=self.on_progress, stop=self.stop_event,
                               keep_work=False, keep_pdf=keep_pdf,
                               cache_dir=cache_dir)
            self.result = res
            if not res.get("ok"):
                reason = res.get("reason")
                if reason == "stopped":
                    self.logq.put(("已中止：本次未完成，工作文件已保留，可再次运行继续。", "err"))
                elif reason == "docx-convert":
                    self.logq.put(("DOCX 转 PDF 失败：请安装 Microsoft Word、WPS 或 LibreOffice 后重试。", "err"))
                else:
                    self.logq.put(("该文件没有可用文字层，自动 OCR 也未成功。"
                                   "请检查 API Key，或先做 OCR 得到可搜索 PDF。", "err"))
                self.doneq.put(False)
                return
            self.book_json = out / "book.json"
            self.out_dir = out
            self.logq.put(("完成：%s（%d 块）" % (res["docx"].name, res["blocks"]), "ok"))
            if res.get("preview"):
                self.logq.put(("PDF 预览：%s" % res["preview"].name, "ok"))
            self.doneq.put(True)
        except Exception as exc:                      # noqa: BLE001
            import traceback
            self.logq.put((traceback.format_exc(limit=3), "err"))
            self.logq.put(("失败：%s" % exc, "err"))
            self.doneq.put(False)

    def finish(self, ok: bool) -> None:
        """Main-thread UI reset after the worker reports completion."""
        elapsed = self.elapsed()
        self._set_running(False)
        self.stop_btn.configure(state="disabled", text="中止")
        self.stopping = False
        self.stage_strip.configure(text=self.HINT)
        if ok:
            self.bar.configure(value=100)
        self._status(("完成 · 总用时 %s" if ok else "已停止 · 用时 %s")
                     % self.fmt_clock(elapsed), "ok" if ok else "err")
        u = (self.result or {}).get("usage") or {}
        if u:
            self.meter.set("总用时 %s · tokens 入 %s / 出 %s · 约 ¥%.3f" % (
                self.fmt_clock(elapsed), f"{u.get('prompt', 0):,}",
                f"{u.get('completion', 0):,}", (self.result or {}).get("cost", 0.0)))
        self.run_t0 = None
        self.eta_ema = None
        ocr = (self.result or {}).get("ocr") or {}
        if ok and ocr.get("suspect"):
            self.log_line("OCR 判读：可疑 %d 页 → 重排 %d，保留 %d，未定 %d"
                          % (len(ocr["suspect"]), len(ocr.get("fixed") or []),
                             len(ocr.get("kept") or []), len(ocr.get("unclear") or [])),
                          "ok")
        self.show_result_buttons(ok)

    def show_result_buttons(self, ok: bool) -> None:
        for w in self.result_bar.winfo_children():
            w.destroy()
        if not ok:
            return
        res = self.result or {}
        targets = [("打开 DOCX", res.get("docx")),
                   ("校对报告", res.get("report")),
                   ("输出目录", self.out_dir)]
        for text, path in targets:
            if path and Path(path).exists():
                ttk.Button(self.result_bar, text=text, cursor="hand2",
                           command=lambda p=path: self.open_path(p)).pack(side="left", padx=(0, 6))

    @staticmethod
    def open_path(path) -> None:
        try:
            os.startfile(str(path))                     # noqa: S606  (Windows shell)
        except Exception as exc:                        # noqa: BLE001
            messagebox.showwarning("无法打开", "%s\n%s" % (path, exc))

    def on_close(self) -> None:
        self.stopping = True
        self.stop_event.set()
        if self.worker is not None and self.worker.is_alive():
            self._status("正在中止，稍后自动退出…", "err")
            self.stop_btn.configure(state="disabled", text="正在中止…")
            self.root.after(200, self._wait_close)
        else:
            self.root.destroy()

    def _wait_close(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            self.root.after(200, self._wait_close)
        else:
            self.root.destroy()


def selftest(pdf: str) -> int:
    import tempfile
    import traceback
    log = Path(sys.executable).parent / "selftest.log"
    lines = ["exe: %s" % sys.executable]
    try:
        from llmtr import ingest, pipeline, proofread, tables, images
        cfg_path = app_root() / "config" / "config.json"
        cfg = pipeline.load_config(cfg_path)
        lines.append("config ok: %s" % cfg_path.exists())
        from types import SimpleNamespace
        from llmtr import app as app_mod
        stub = SimpleNamespace(prog={"stage": "", "done": 0, "total": 1, "idx": 0,
                                     "n": len(pipeline.STAGES), "t": 0.0},
                               prog_lock=threading.Lock())
        app_mod.App.on_progress(stub, "测试", 1, 2, 3, 4)
        lines.append("progress callback ok: %s" % (stub.prog,))
        p = Path(pdf)
        book = ingest.ingest(p)
        if not book.get("ok"):
            from llmtr import ocr, translate
            key = os.environ.get("DEEPSEEK_API_KEY", "")
            lines.append("no text layer: %s" % book.get("probe"))
            if key:
                client = translate.Client("https://api.deepseek.com/v1", key, "deepseek-chat")
                got = ocr.pages(p, client, {"dpi": 150, "max_pages": 1, "mode": "auto"})
                lines.append("ocr one page: %d chars" % (len(got[0]) if got else 0))
            log.write_text("\n".join(lines), encoding="utf-8")
            return 0
        lines.append("ingest ok: %d blocks" % len(book["blocks"]))
        lines.append("tables: %s" % [t[0] for t in tables.candidates(
            p, book["labels"], dict(tables.DEFAULTS), page_map=book.get("page_map"))])
        lines.append("figures: %s" % [f[0] for f in images.find(
            p, book["labels"], dict(images.DEFAULTS), page_map=book.get("page_map"))])
        lines.append("proof: %s" % proofread.summary(proofread.check(book)))
        code = 0
    except Exception:
        lines.append(traceback.format_exc())
        code = 1
    log.write_text("\n".join(lines), encoding="utf-8")
    return code


def _arg(name: str) -> str:
    argv = sys.argv
    if name in argv:
        i = argv.index(name)
        return argv[i + 1] if i + 1 < len(argv) else ""
    return ""


def main() -> None:
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        raise SystemExit(selftest(sys.argv[i + 1] if len(sys.argv) > i + 1 else ""))
    initial = {"pdf": _arg("--pdf"), "out": _arg("--out"),
               "preset": _arg("--preset"), "start": "--start" in sys.argv}
    try:
        root = tk.Tk()
        app = App(root, initial)
        root.protocol("WM_DELETE_WINDOW", app.on_close)
        root.mainloop()
    except Exception:
        import traceback
        import tempfile
        log = Path(tempfile.gettempdir()) / "llmtrans_error.log"
        log.write_text(traceback.format_exc(), encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
