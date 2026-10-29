"""End-to-end pipeline: PDF -> translated, formatted DOCX."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import build as bd
from . import clean as clean_mod
from . import docxin as docx_mod
from . import epub as epub_mod
from . import figures as fig_mod
from . import gloss as gloss_mod
from . import images as img_mod
from . import ingest as ing
from . import names as nm
from . import meta as meta_mod
from . import proofread
from . import ocr as ocr_mod
from . import procutil
from . import tables as tab_mod
from . import toc as toc_mod
from . import translate as tr

DEFAULTS = {
    "api": {"provider": "deepseek", "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat", "workers": 6, "timeout": 120,
            "price_in": 2.0, "price_out": 8.0, "headers": {}},
    "chunk_words": 1600,
    "preset": "print",
    "page_tags": False,
    "copyright_page": True,
    "odd_even_header": True,
    "translate": {"notes": True, "bibliography": False},
    "polish": {"enabled": True, "batch_chars": 6000, "max_blocks": 8},
    "ocr": {"mode": "auto", "dpi": 170, "max_pages": 0, "min_chars": 120,
            "max_bad_ratio": 0.35, "judge": True, "judge_max": 40},
    "proof": {"mode": "auto", "limit": 40},
    "names": {"enabled": True, "limit": 150},
    "source_gloss": {"mode": "strong", "max_per_block": 8},
    "figures": dict(fig_mod.DEFAULTS),
    "paths": {"glossary": "config/glossary.tsv", "names": "config/names.tsv"},
}


def load_config(path: Path | None) -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    if path and path.exists():
        user = json.loads(path.read_text(encoding="utf-8"))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


def apply_layout_flags(meta: dict, cfg: dict) -> None:
    meta["page_tags"] = bool(cfg.get("page_tags", False))
    meta["copyright_page"] = bool(cfg.get("copyright_page", True))
    meta["odd_even_header"] = bool(cfg.get("odd_even_header", True))


def load_tsv(path: Path) -> dict[str, str]:
    out = {}
    if path and path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            p = line.split("\t")
            if len(p) >= 2 and p[0] and not p[0].startswith("#"):
                out[p[0]] = p[1]
    return out


def _apply_gloss(blocks: list[dict], glossary: dict[str, str], cfg: dict, log) -> dict:
    stats = gloss_mod.apply(blocks, glossary, cfg)
    if stats["mode"] == "off":
        log("英文原词：关闭（书名与专名只保留中文）")
    elif stats["terms"] or stats["titles"]:
        log("英文原词：%s · 术语括注 %d 处 · 去重书名 %d 处"
            % (gloss_mod.LABELS[stats["mode"]], stats["terms"], stats["titles"]))
    return stats



def find_soffice(cfg: dict | None = None):
    """Locate a LibreOffice binary on this machine."""
    import os
    import shutil

    if cfg:
        explicit = (cfg.get("figures") or {}).get("soffice")
        if explicit and Path(explicit).exists():
            return explicit
    for name in ("soffice", "soffice.exe", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    for root in roots:
        for sub in (r"LibreOffice\program\soffice.exe", r"LibreOffice 7\program\soffice.exe"):
            cand = Path(root) / sub
            if cand.exists():
                return str(cand)
    return None


def _remap_index(book: dict, pdf_out: Path) -> bool:
    """Rewrite name-index page numbers with this edition's printed page numbers.

    The index rows carry the source book's pagination; after typesetting, each
    Chinese name is located in the proof PDF and its footer number recorded.
    """
    try:
        import fitz
    except ImportError:
        return False
    blocks = book.get("blocks") or []
    start = next((i for i, b in enumerate(blocks)
                  if b.get("type") == "h1" and "索引" in (b.get("zh") or "")), None)
    if start is None:
        return False
    rows = [b for b in blocks[start + 1:] if b.get("type") == "bib"]
    if not rows:
        return False
    doc = fitz.open(str(pdf_out))
    texts, raws, labels = [], [], []
    for pg in doc:
        t = pg.get_text()
        raws.append(t)
        texts.append(re.sub(r"\s+", "", t))
        m = re.findall(r"第\s+(\d+)\s+页", t) or re.findall(r"第\s*(\d+)\s*页", t)
        labels.append(int(m[0]) if m else None)
    idx_start = None
    try:
        for _lvl, title, page in doc.get_toc():
            if "人名索引" in (title or ""):
                idx_start = page - 1
                break
    except Exception:                                   # noqa: BLE001
        idx_start = None
    if idx_start is None:
        idx_start = next((i for i, t in enumerate(texts)
                          if i > 2 and "人名索引" in t), None)
    if idx_start is None:
        return False
    changed = False
    drop = []
    for b in rows:
        parts = (b.get("zh") or "").split("\u3000")
        if len(parts) < 3:
            continue
        zh = re.sub(r"\s+", "", parts[1])
        if not zh:
            continue
        found = []
        if len(zh) >= 2:
            found = [labels[i] for i, t in enumerate(texts[:idx_start])
                     if labels[i] is not None and zh in t]
        if not found and parts[0].strip():
            # names kept in Latin inside citations still deserve a pointer;
            # single-character surnames (金/斯) rely on this to avoid 金融 etc.
            pat = re.compile(r"(?<![A-Za-z])%s(?![A-Za-z])" % re.escape(parts[0].strip()))
            found = [labels[i] for i, t in enumerate(raws[:idx_start])
                     if labels[i] is not None and pat.search(t)]
        if not found:
            drop.append(b)
            continue
        new = ", ".join(str(n) for n in sorted(set(found)))
        if new != parts[2]:
            parts[2] = new
            b["zh"] = b["text"] = "\u3000".join(parts)
            changed = True
    if drop:
        book["blocks"] = [b for b in blocks if b not in drop]
        changed = True
    doc.close()
    return changed


def paginate(book: dict, docx: Path, pdf_out: Path, preset: str, cfg: dict,
             stop=None, stats: dict | None = None, log=print) -> dict:
    """Second pass: fill TOC page numbers from the PDF outline."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return {}

    def _kill(p):
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                           capture_output=True, **procutil.no_window())
        except Exception:                              # noqa: BLE001
            pass
        try:
            p.wait(3)
        except Exception:                              # noqa: BLE001
            p.kill()

    def _run(cmd, env=None, out_path=None, timeout=180):
        """Run a converter and stop waiting once its output file is complete.

        LibreOffice Kit can keep its helper process alive after a successful
        conversion, so waiting for the process would hang the pipeline.  The
        output file is written progressively, so success means its size has
        stopped changing for a moment.
        """
        logf = pdf_out.parent / "_tmp" / "convert.log"
        with open(logf, "wb") as fh:
            p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=fh,
                                 stderr=subprocess.STDOUT, env=env,
                                 **procutil.no_window())
            t0 = time.monotonic()
            last, stable = -1, 0
            while p.poll() is None:
                if stop is not None and stop.is_set():
                    _kill(p)
                    return -1, "已中止"
                if out_path is not None:
                    try:
                        size = out_path.stat().st_size
                    except OSError:
                        size = 0
                    if size > 0 and size == last:
                        stable += 1
                        if stable >= 6:                # ~1.2 s without growth
                            time.sleep(0.3)
                            _kill(p)
                            break
                    else:
                        stable = 0
                        last = size
                if time.monotonic() - t0 > timeout:
                    _kill(p)
                    return -1, "超时 %ds" % timeout
                time.sleep(0.2)
        try:
            tail = logf.read_text(encoding="utf-8", errors="ignore").strip()[-300:]
        except Exception:                              # noqa: BLE001
            tail = ""
        return p.returncode or 0, tail

    def convert():
        if pdf_out.exists():
            pdf_out.unlink()
        tmp = pdf_out.parent / "_tmp" / "convert"
        tmp.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update({"TEMP": str(tmp), "TMP": str(tmp), "TMPDIR": str(tmp)})
        kit_node = cfg["figures"].get("node")
        kit_cli = cfg["figures"].get("cli")
        err = ""
        if kit_node and kit_cli and Path(kit_node).exists() and Path(kit_cli).exists():
            rc, err = _run([kit_node, kit_cli, "convert", "--input", str(docx),
                            "--output", str(pdf_out)], env=env, out_path=pdf_out)
            if pdf_out.exists():
                return True
            err = "LibreOffice Kit rc=%s %s" % (rc, err)
        soffice = find_soffice(cfg)
        if soffice:
            produced = pdf_out.parent / (docx.stem + ".pdf")
            rc, err = _run([soffice, "--headless", "--norestore",
                            "--convert-to", "pdf", "--outdir", str(pdf_out.parent), str(docx)],
                           env=env, out_path=produced)
            if produced.exists():
                produced.replace(pdf_out)
                return True
            err = "LibreOffice rc=%s %s" % (rc, err)
        log("DOCX 转 PDF 失败：%s" % (err or "未找到 LibreOffice"))
        return False

    def mapping():
        rd = PdfReader(str(pdf_out))
        flat = []

        def walk(o):
            for it in o:
                if isinstance(it, list):
                    walk(it)
                else:
                    flat.append(it)

        walk(rd.outline)
        heads = bd.toc_heads(book["blocks"], book.get("meta"))
        pages = {}
        # sequential title matching: the outline also carries headings that the
        # printed contents omits (notes, references), so zip() would drift
        j = 0
        for h in heads:
            key = re.sub(r"\s+", "", (h.get("zh") or "").strip())
            hit = None
            for k in range(j, len(flat)):
                if re.sub(r"\s+", "", (getattr(flat[k], "title", "") or "")) == key:
                    hit = k
                    break
            if hit is None:
                continue
            j = hit + 1
            try:
                pn = rd.get_destination_page_number(flat[hit])
            except Exception:
                continue
            t = re.sub(r"\s+", "", rd.pages[pn].extract_text() or "")
            m = re.search(r"第(\d+)页", t)
            if m:
                pages[h["anchor"]] = int(m.group(1))
                continue
            # A section's first page may suppress its folio; read the next page
            # and subtract the offset.
            for off in (1, 2):
                if pn + off >= len(rd.pages):
                    break
                t2 = re.sub(r"\s+", "", rd.pages[pn + off].extract_text() or "")
                m2 = re.search(r"第(\d+)页", t2)
                if m2:
                    pages[h["anchor"]] = int(m2.group(1)) - off
                    break
        return pages

    if stop is not None and stop.is_set():
        return {}
    bd.build_doc(book, preset, docx, book["meta"], stats=stats)
    if not convert():
        return {}
    heads = bd.toc_heads(book["blocks"], book.get("meta"))
    if not heads:
        return {}
    pages = mapping()
    bd.build_doc(book, preset, docx, book["meta"], pages=pages, stats=stats)
    if not convert():
        return pages
    if _remap_index(book, pdf_out):
        pages = mapping()
        bd.build_doc(book, preset, docx, book["meta"], pages=pages, stats=stats)
        convert()
    return mapping()


from .stages import STAGES


def _fmt_dur(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm%02ds" % (s // 60, s % 60)
    return "%dh%02dm" % (s // 3600, (s % 3600) // 60)


def clean_outputs(outdir: Path, docx: Path, preview: Path, keep_pdf: bool,
                  keep_work: bool, stem: str = "") -> None:
    """Leave only the translated DOCX (and optional preview PDF) in the output dir."""
    if keep_work:
        if not keep_pdf:
            preview.unlink(missing_ok=True)
        return
    for name in ("book.json", "names.tsv"):
        (outdir / name).unlink(missing_ok=True)
    for name in ("cache", "assets", "_tmp", "_ocr_tmp", "_fig_tmp"):
        shutil.rmtree(outdir / name, ignore_errors=True)
    if not keep_pdf:
        preview.unlink(missing_ok=True)
    if stem:
        for old in outdir.glob(stem[:40] + "_zh_*.docx"):
            if old.resolve() != docx.resolve():
                old.unlink(missing_ok=True)
    for old in outdir.glob("preview*.pdf"):
        if old.resolve() != preview.resolve():
            old.unlink(missing_ok=True)


def run(pdf: Path, outdir: Path, cfg: dict, *, preset: str = "print", dry_run: bool = False,
        pages_filter: str | None = None, structure: list[dict] | None = None,
        use_toc: bool = True, log=print, progress=None, stop=None,
        keep_work: bool = True, keep_pdf: bool = True,
        cache_dir: Path | None = None) -> dict:
    try:
        suffix = Path(pdf).suffix.lower()
        if suffix == ".epub":
            return _run_epub(pdf, outdir, cfg, preset=preset, dry_run=dry_run,
                             log=log, progress=progress, stop=stop,
                             keep_work=keep_work, keep_pdf=keep_pdf, cache_dir=cache_dir)
        if suffix == ".docx":
            return _run_docx(pdf, outdir, cfg, preset=preset, dry_run=dry_run,
                             pages_filter=pages_filter,
                             log=log, progress=progress, stop=stop,
                             keep_work=keep_work, keep_pdf=keep_pdf, cache_dir=cache_dir)
        return _run_impl(pdf, outdir, cfg, preset=preset, dry_run=dry_run,
                         pages_filter=pages_filter, structure=structure, use_toc=use_toc,
                         log=log, progress=progress, stop=stop,
                         keep_work=keep_work, keep_pdf=keep_pdf, cache_dir=cache_dir)
    except tr.StopRequested:
        log("已中止：中间文件保留，可再次运行续译")
        return {"ok": False, "reason": "stopped"}


def _run_impl(pdf: Path, outdir: Path, cfg: dict, *, preset: str = "print", dry_run: bool = False,
              pages_filter: str | None = None, structure: list[dict] | None = None,
              use_toc: bool = True, log=print, progress=None, stop=None,
              keep_work: bool = True, keep_pdf: bool = True,
              cache_dir: Path | None = None, style_heads: list[dict] | None = None,
              input_meta: dict | None = None) -> dict:
    started = time.monotonic()
    current = {"name": None, "t": started}

    def check():
        if stop is not None and stop.is_set():
            raise tr.StopRequested()

    def stage(name, done=0, total=1):
        now = time.monotonic()
        if name != current["name"]:
            if current["name"]:
                idx = STAGES.index(current["name"]) + 1 if current["name"] in STAGES else 0
                log("[阶段 %d/%d %s] 完成 · %s"
                    % (idx, len(STAGES), current["name"], _fmt_dur(now - current["t"])))
            current["name"] = name
            current["t"] = now
            idx = STAGES.index(name) + 1 if name in STAGES else 0
            log("[阶段 %d/%d %s] 开始" % (idx, len(STAGES), name))
        if progress:
            idx = STAGES.index(name) + 1 if name in STAGES else 0
            progress(name, done, total, idx, len(STAGES))

    check()
    outdir.mkdir(parents=True, exist_ok=True)
    work_tmp = outdir / "_tmp"

    client = tr.Client(cfg["api"]["base_url"], cfg["api"].get("key", ""), cfg["api"]["model"],
                       timeout=int(cfg["api"].get("timeout", 120)),
                       cache_dir=cache_dir or (outdir / "cache"),
                       price_in=float(cfg["api"].get("price_in", 2.0)),
                       price_out=float(cfg["api"].get("price_out", 8.0)),
                       headers=cfg["api"].get("headers") or None)
    ocr_cfg = dict(ocr_mod.DEFAULTS)
    ocr_cfg.update(cfg.get("ocr", {}) or {})
    ocr_cfg["tmp_dir"] = str(work_tmp / "ocr")
    pages_text = ing.read_pages(pdf)
    info = ing.probe(pages_text)

    def ocr_all(reason: str):
        log("%s：整本视觉识别 %d 页（可随时中止）" % (reason, len(pages_text)))
        return ocr_mod.layout_pages(
            pdf, client, ocr_cfg, log=log,
            progress=(lambda d, n: stage("解析", d, n)) if progress else None,
            stop=stop)

    ocr_pages = []
    ocr_layouts: dict[int, list[tuple[str, str]]] = {}
    ocr_notes = {"vision": False, "severe": [], "suspect": [], "judged": 0,
                 "fixed": [], "kept": [], "unclear": [], "skipped": []}
    layout = None

    def merge_layout(entries):
        for e in entries:
            if e.get("items"):
                ocr_layouts.setdefault(int(e["src"]), []).extend(e["items"])

    def page_list(nums, limit=14):
        s = "、".join("p.%d" % (n + 1) for n in nums[:limit])
        return s + ("…" if len(nums) > limit else "")

    stage("解析")
    if not info["has_text_layer"]:
        if ocr_cfg.get("mode", "auto") == "auto" and client.key and not dry_run:
            layout = ocr_all("无可用文字层")
            ocr_notes["vision"] = True
            if len(layout) < 3:
                return {"ok": False, "reason": "no-text-layer", "probe": info}
        else:
            return {"ok": False, "reason": "no-text-layer", "probe": info}
    elif ocr_cfg.get("mode", "auto") == "auto" and client.key and not dry_run:
        quality = ing.text_quality(pages_text)
        if not quality["ok"]:
            log("文字层不可用（中位词长 %d，粘连词 %.1f%%）"
                % (quality["median"], quality["page_glue"] * 100))
            layout = ocr_all("文字层不可用")
            ocr_notes["vision"] = True
            got = sum(len(e["items"]) for e in layout)
            log("图像识别：%d 页 → %d 个书页（含跨版拆分）→ %d 块"
                % (len(pages_text), len(layout), got))
            if got < 10:
                return {"ok": False, "reason": "bad-text-layer", "probe": info}
        else:
            poor = ing.bad_pages(pdf, pages_text,
                                 min_chars=int(ocr_cfg.get("min_chars", 120)),
                                 max_ratio=float(ocr_cfg.get("max_bad_ratio", 0.35)))
            if poor is None:
                layout = ocr_all("文字层大面积不可用")
                ocr_notes["vision"] = True
                if sum(len(e["items"]) for e in layout) < 10:
                    return {"ok": False, "reason": "bad-text-layer", "probe": info}
            else:
                if poor:
                    log("文字层缺失/损坏的页：%s，逐页结构化重排" % page_list(poor, 8))
                    entries = ocr_mod.layout_pages(pdf, client, ocr_cfg, log=log,
                                                   only=poor, stop=stop)
                    merge_layout(entries)
                    ocr_notes["severe"] = [i + 1 for i in poor]
                    ocr_notes["fixed"] = [i + 1 for i in poor]
                    ocr_pages.extend(poor)
                sus = []
                if ocr_cfg.get("judge", True):
                    sus = ing.suspect_pages(pages_text, exclude=poor,
                                            max_pages=int(ocr_cfg.get("judge_max", 40)))
                if sus:
                    log("OCR 可疑页：%s，交 AI 判读" % page_list(sus, 10))
                    res = ocr_mod.review_pages(pdf, client, ocr_cfg, pages_text,
                                               log=log, only=sus, stop=stop)
                    ocr_notes["suspect"] = [i + 1 for i in sus]
                    for r in res:
                        n = int(r["src"]) + 1
                        if r["verdict"] == "fix":
                            merge_layout([r])
                            ocr_pages.append(int(r["src"]))
                            ocr_notes["fixed"].append(n)
                            ocr_notes["judged"] += 1
                        elif r["verdict"] == "ok":
                            ocr_notes["kept"].append(n)
                            ocr_notes["judged"] += 1
                        elif r["verdict"] == "skipped":
                            ocr_notes["skipped"].append(n)
                        else:
                            ocr_notes["unclear"].append(n)
                            ocr_notes["judged"] += 1
                    log("OCR 判读：%d 页 → 重排 %d，保留 %d，未定 %d，跳过 %d"
                        % (len(sus), len([x for x in res if x["verdict"] == "fix"]),
                           len(ocr_notes["kept"]),
                           len(ocr_notes["unclear"]), len(ocr_notes["skipped"])))
                if ocr_layouts:
                    log("OCR 局部修复：%d 页按图像重排" % len(ocr_layouts))
                ocr_pages = sorted(set(ocr_pages))

    docx_labels = None
    if style_heads is not None:
        # a converted DOCX has no printed folios unless the source carried them
        got = ing.page_labels(pages_text)
        if sum(1 for l in got if str(l).strip()) < len(got) * 0.6:
            got = [str(i + 1) for i in range(len(got))]
        docx_labels = got

    check()
    book = ing.ingest(pdf, structure, pages=pages_text, from_ocr=False,
                      ocr_pages=ocr_pages, layout=layout, labels=docx_labels,
                      style_heads=style_heads,
                      ocr_layouts=ocr_layouts or None)
    if not book["ok"]:
        return {"ok": False, "reason": "no-text-layer", "probe": book["probe"]}
    blocks, labels = book["blocks"], book["labels"]
    if input_meta:
        gone = docx_mod.drop_front_matter(blocks, input_meta)
        if gone:
            log("docx: 文首标题/署名 %d 块已并入封面，正文不再重复" % gone)
    page_map = book.get("page_map") or {}
    chars = sum(len(b.get("text") or "") for b in blocks)
    largest = max((len(b.get("text") or "") for b in blocks), default=0)
    log("ingest: %d blocks · %d chars · 最大块 %d 字"
        % (len(blocks), chars, largest))
    if book["probe"].get("pages") and len(blocks) < book["probe"]["pages"] * 0.15:
        log("提示：结构块偏少，可能有整章未切开；翻译仍继续，排版后请抽查段落")

    glossary = load_tsv(Path(cfg["paths"]["glossary"]))
    per_book_glossary = outdir / "glossary.tsv"
    if per_book_glossary.exists():
        glossary.update(load_tsv(per_book_glossary))
    gloss_mode = gloss_mod.mode_of(cfg)
    api = client if (client.key and not dry_run) else None

    stage("结构")
    if use_toc:
        entries = toc_mod.find_entries(pdf, labels=labels)
        if entries:
            translate_titles = (lambda ts: toc_mod.translate_titles(client, ts)) if api else (lambda ts: {})
            blocks = toc_mod.apply(blocks, entries, translate_titles)
            log("toc: 采用原书目录 %d 条（h1 %d · h2 %d · h3 %d）"
                % (len(entries), sum(1 for b in blocks if b["type"] == "h1"),
                   sum(1 for b in blocks if b["type"] == "h2"),
                   sum(1 for b in blocks if b["type"] == "h3")))
        else:
            log("toc: 未发现目录页，按版式推断标题层级（h1 %d · h2 %d · h3 %d）"
                % (sum(1 for b in blocks if b["type"] == "h1"),
                   sum(1 for b in blocks if b["type"] == "h2"),
                   sum(1 for b in blocks if b["type"] == "h3")))
    if style_heads:
        log("docx: 采用 Word 标题 %d 个（h1 %d · h2 %d · h3 %d）"
            % (len(style_heads), sum(1 for b in blocks if b["type"] == "h1"),
               sum(1 for b in blocks if b["type"] == "h2"),
               sum(1 for b in blocks if b["type"] == "h3")))

    table_boxes = []
    stage("表格")
    if cfg.get("tables", {}).get("mode", "off") == "auto":
        tcfg = dict(tab_mod.DEFAULTS)
        tcfg.update(cfg.get("tables", {}))
        found = tab_mod.candidates(pdf, labels, tcfg, page_map=page_map)
        table_boxes = [(str(p), bb) for p, bb, _rows in found]
        if found:
            blocks, tplates = tab_mod.replace(blocks, found, pdf, labels,
                                              outdir / "assets", tcfg,
                                              api,
                                              page_map=page_map, stop=stop)
            for _n, _b in enumerate(blocks):
                _b["id"] = "b%04d" % _n
            log("tables: %d candidate(s), embedded as images %s"
                % (len(found), tplates or "none"))

    _tcfg = cfg.get("translate", {}) or {}
    if not _tcfg.get("notes", True):
        for b in blocks:
            if b["type"] == "note" and not b.get("zh"):
                b["zh"] = b["text"]          # keep notes in the original language
    if _tcfg.get("bibliography", False):
        for b in blocks:
            if b["type"] == "bib":
                b["zh"] = ""
    for b in blocks:
        if b["type"] == "bib" and not b.get("zh") and not _tcfg.get("bibliography", False):
            b["zh"] = b["text"].strip()
        elif b["type"] == "byline" and not b.get("zh"):
            b["zh"] = b["text"].strip()      # author bylines stay in the original
    todo = [b for b in blocks if not b.get("zh")]
    if not _tcfg.get("notes", True):
        todo = [b for b in todo if b["type"] != "note"]

    stage("翻译")
    milestones = {"pct": -1}

    def translate_progress(done, total):
        pct = int(done * 100 / max(1, total))
        if pct >= milestones["pct"] + 10 or (done >= total and milestones["pct"] < 100):
            milestones["pct"] = pct
            log("[翻译] %d/%d 块 · %d%%" % (done, total, pct))
        if progress:
            stage("翻译", done, total)

    tr.translate_blocks(client, todo, glossary, workers=cfg["api"]["workers"],
                        chunk_size=cfg["chunk_words"], dry_run=dry_run or not client.key,
                        progress=translate_progress,
                        stop=stop, source_gloss=gloss_mode)

    stage("修复")
    if client.key and not dry_run:
        fixed = tr.fix_headings(client, blocks, stop=stop,
                                workers=cfg["api"].get("workers", 4),
                                progress=(lambda d, n: stage("修复", d, n)) if progress else None,
                                system=gloss_mod.system_prompt(gloss_mode))
        if fixed:
            log("headings repaired: %d" % fixed)
        fixed = tr.repair_blocks(client, blocks, stop=stop,
                                 workers=cfg["api"].get("workers", 4),
                                 progress=(lambda d, n: stage("修复", d, n)) if progress else None,
                                 system=gloss_mod.system_prompt(gloss_mode))
        if fixed:
            log("blocks repaired: %d" % fixed)
        fixed = tr.fix_english_terms(client, blocks, stop=stop,
                                     workers=cfg["api"].get("workers", 4),
                                     progress=(lambda d, n: stage("修复", d, n))
                                     if progress else None)
        if fixed:
            log("stray English translated: %d blocks" % fixed)
    missing = sum(1 for b in todo if not b.get("zh"))
    log("translate: %d blocks (%d unresolved)" % (len(todo), missing))

    stage("润色")
    if (cfg.get("polish") or {}).get("enabled", True) and client.key and not dry_run:
        pc = cfg.get("polish") or {}
        try:
            edited = tr.polish_blocks(
                client, blocks,
                batch_chars=int(pc.get("batch_chars", 6000)),
                max_blocks=int(pc.get("max_blocks", 8)),
                workers=cfg["api"].get("workers", 4), stop=stop,
                progress=(lambda d, n: stage("润色", d, n)) if progress else None)
            log("polish: %d blocks edited" % edited)
        except tr.StopRequested:
            raise
        except Exception as exc:                        # noqa: BLE001
            log("润色跳过：%s" % exc)

    stage("审校")
    ai_issues = []
    auto = (cfg.get("proof") or {}).get("mode", "auto")
    if auto == "auto" and client.key and not dry_run:
        try:
            fixed, ai_issues = proofread.ai_fix(
                client, blocks,
                limit=int((cfg.get("proof") or {}).get("limit", 40)),
                stop=stop, workers=cfg["api"].get("workers", 4),
                progress=(lambda d, n: stage("审校", d, n)) if progress else None)
            if fixed:
                log("ai 校对：自动修正 %d 处" % fixed)
        except tr.StopRequested:
            raise
        except Exception as exc:                        # noqa: BLE001
            log("ai 校对跳过：%s" % exc)

    stage("图版")
    if cfg["figures"].get("mode", "auto") != "off":
        cands = fig_mod.detect(blocks, cfg["figures"])
        force = [int(x) for x in cfg["figures"].get("force", [])]
        cands = sorted(set(cands) | set(force))
        vclient = api if cfg["figures"].get("vision", True) else None
        plates, kinds = fig_mod.select_and_replace(blocks, cands, pdf, labels,
                                                   outdir / "assets", cfg["figures"], vclient,
                                                   page_map=page_map, stop=stop,
                                                   progress=(lambda d, n: stage("图版", d, n))
                                                   if progress else None)
        log("figures: candidates=%s kinds=%s -> %s" % (cands, kinds, plates))

    icfg = dict(img_mod.DEFAULTS)
    icfg.update(cfg.get("images", {}) or {})
    icfg["tmp_dir"] = str(work_tmp / "fig")
    if icfg.get("mode", "auto") != "off":
        found_img = img_mod.find(pdf, labels, icfg, exclude=table_boxes,
                                 skip=set(ocr_pages), page_map=page_map, stop=stop,
                                 progress=(lambda d, n: stage("图版", d, n)) if progress else None)
        blocks, _cf = img_mod.caption_figures(pdf, labels, blocks, icfg, page_map=page_map,
                                              stop=stop)
        if _cf:
            log("图注锚点裁图：%d 张" % _cf)
        if found_img:
            blocks, added = img_mod.inject(blocks, found_img, pdf, labels, outdir / "assets", icfg,
                                           api, page_map=page_map,
                                           stop=stop)
            log("images: %d figure(s) %s" % (len(added), added[:8]))
        else:
            log("images: none")

    stage("人名")
    pairs = {}
    if cfg["names"].get("enabled", True) and not dry_run and client.key:
        per_book = outdir / "names.tsv"
        recs = nm.load_records(per_book) if per_book.exists() else {}
        if not recs:
            known = nm.load_records(Path(cfg["paths"]["names"]))
            cands = nm.candidates(blocks, cfg["names"].get("limit", 150))
            fresh = [c for c in cands if c not in known]
            recs = {k: v for k, v in known.items() if k in cands}
            if fresh:
                recs.update(nm.translate_records(client, fresh, stop=stop))
        cited = nm.cited_names(blocks)
        for k, v in recs.items():
            if k in cited:
                v["kind"] = nm.SURNAME
        nm.save_tsv(per_book, recs)
        pairs = {k: v["zh"] for k, v in recs.items()}
        given = {k for k, v in recs.items() if v.get("kind") == nm.GIVEN}
        if pairs:
            pages = nm.localise(blocks, pairs, mode=gloss_mode)
            rows = nm.build_index(pairs, pages, given)
            if rows:
                pos = len(blocks)          # the name index always goes last
                blocks[pos:pos] = [{"page": "", "type": "h1", "text": "NAME INDEX", "zh": "人名索引"}] + \
                                  [{"page": "", "type": "bib", "text": r, "zh": r} for r in rows]
            log("names: %d entries" % len(rows))
    if cfg["names"].get("enabled", True):
        per_book = outdir / "names.tsv"
        src = per_book if per_book.exists() else Path(cfg["paths"]["names"])
        known = nm.load_tsv(src)
        blob = " ".join(b["text"] for b in blocks)
        pairs = {k: v for k, v in known.items()
                 if k and re.search(r"(?<![A-Za-z])%s(?![A-Za-z])" % re.escape(k), blob)}
    blocks = clean_mod.finalize(clean_mod.run(blocks), pairs)
    if pages_filter:
        lo, hi = (int(x) for x in pages_filter.split("-"))
        blocks = [b for b in blocks if str(b["page"]).isdigit() and lo <= int(b["page"]) <= hi]
    _apply_gloss(blocks, glossary, cfg, log)

    stage("排版")
    check()
    info = dict(input_meta) if input_meta and input_meta.get("title") \
        else meta_mod.extract(pdf, api, log=log, stop=stop)
    if api:
        info = meta_mod.translate_fields(client, info, stop=stop)
        log("metadata: %s / %s / %s" % (info.get("title", "")[:40], info.get("author", "")[:30],
                                        info.get("publisher", "")[:30]))
    meta = {"title": pdf.stem, "pages": book["probe"]["pages"],
            "title_zh": info.get("zh_title") or info.get("title", ""),
            "title_en": info.get("title", ""),
            "subtitle": info.get("zh_subtitle") or info.get("subtitle", ""),
            "author": info.get("zh_author") or info.get("author", ""),
            "author_en": info.get("author", ""),
            "publisher": info.get("zh_publisher") or info.get("publisher", ""),
            "year": info.get("year", "")}
    apply_layout_flags(meta, cfg)
    book = {"meta": meta, "labels": labels, "blocks": blocks, "page_map": page_map}
    book["meta"].update(meta)
    (outdir / "book.json").write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")

    docx = outdir / (pdf.stem[:40] + "_zh_%s.docx" % preset)
    build_stats = {}
    bd.build_doc(book, preset, docx, meta, stats=build_stats)

    stage("页码")
    preview = outdir / "preview.pdf"
    pages_map = {}
    if not pages_filter:
        try:
            pages_map = paginate(book, docx, preview, preset, cfg, stop=stop,
                                 stats=build_stats, log=log)
        except tr.StopRequested:
            raise
        except Exception as _e:                      # LibreOffice/node missing: keep the docx usable
            log("paginate skipped: %s" % _e)
            pages_map = {}
        if not pages_map:
            log("目录页码：未找到 LibreOffice 或本书无目录，本次目录不含页码")
    log("build: %s (%d KB), 目录条目 %d 条" % (docx.name, docx.stat().st_size // 1024, len(pages_map)))
    report_stats = dict(build_stats)
    report_stats["ocr"] = ocr_notes
    report = proofread.write_report(outdir, book, stats=report_stats,
                                    ai_issues=ai_issues, preset=preset, log=log)
    clean_outputs(outdir, docx, preview, keep_pdf, keep_work, stem=pdf.stem)
    if not keep_work:
        log("输出：%s%s" % (docx.name, " + preview.pdf" if keep_pdf and preview.exists() else ""))
    if current["name"]:
        idx = STAGES.index(current["name"]) + 1 if current["name"] in STAGES else 0
        log("[阶段 %d/%d %s] 完成 · %s"
            % (idx, len(STAGES), current["name"],
               _fmt_dur(time.monotonic() - current["t"])))
    log("总用时 %s" % _fmt_dur(time.monotonic() - started))
    return {"ok": True, "docx": docx, "preview": preview if keep_pdf and preview.exists() else None,
            "blocks": len(blocks), "toc_pages": len(pages_map), "report": report,
            "ocr": ocr_notes,
            "usage": client.usage, "cost": client.cost(),
            "elapsed": time.monotonic() - started}


def _run_epub(path: Path, outdir: Path, cfg: dict, *, preset: str = "print",
              dry_run: bool = False, log=print, progress=None, stop=None,
              keep_work: bool = True, keep_pdf: bool = True,
              cache_dir: Path | None = None) -> dict:
    """EPUB input: the XHTML pages already carry structure; skip OCR and page imaging."""
    started = time.monotonic()
    current = {"name": None, "t": started}

    def check():
        if stop is not None and stop.is_set():
            raise tr.StopRequested()

    def stage(name, done=0, total=1):
        now = time.monotonic()
        if name != current["name"]:
            if current["name"]:
                idx = STAGES.index(current["name"]) + 1 if current["name"] in STAGES else 0
                log("[阶段 %d/%d %s] 完成 · %s"
                    % (idx, len(STAGES), current["name"], _fmt_dur(now - current["t"])))
            current["name"] = name
            current["t"] = now
            idx = STAGES.index(name) + 1 if name in STAGES else 0
            log("[阶段 %d/%d %s] 开始" % (idx, len(STAGES), name))
        if progress:
            idx = STAGES.index(name) + 1 if name in STAGES else 0
            progress(name, done, total, idx, len(STAGES))

    check()
    outdir.mkdir(parents=True, exist_ok=True)
    client = tr.Client(cfg["api"]["base_url"], cfg["api"].get("key", ""), cfg["api"]["model"],
                       timeout=int(cfg["api"].get("timeout", 120)),
                       cache_dir=cache_dir or (outdir / "cache"),
                       price_in=float(cfg["api"].get("price_in", 2.0)),
                       price_out=float(cfg["api"].get("price_out", 8.0)),
                       headers=cfg["api"].get("headers") or None)

    stage("解析")
    book_in = epub_mod.extract(path, outdir / "assets", log=log)
    if not book_in.get("ok"):
        return {"ok": False, "reason": "no-text-layer", "probe": book_in.get("probe")}
    blocks, labels = book_in["blocks"], book_in["labels"]
    page_map = book_in.get("page_map") or {}
    chars = sum(len(b.get("text") or "") for b in blocks)
    log("epub: %d blocks · %d chars · %d pages"
        % (len(blocks), chars, book_in["probe"]["pages"]))

    glossary = load_tsv(Path(cfg["paths"]["glossary"]))
    per_book_glossary = outdir / "glossary.tsv"
    if per_book_glossary.exists():
        glossary.update(load_tsv(per_book_glossary))
    gloss_mode = gloss_mod.mode_of(cfg)
    api = client if (client.key and not dry_run) else None

    stage("结构")
    log("epub 结构：原文标题层级直接采用（h1 %d · h2 %d）"
        % (sum(1 for b in blocks if b["type"] == "h1"),
           sum(1 for b in blocks if b["type"] == "h2")))

    stage("表格")
    log("epub: 表格随原书版面保留，无需单独处理")

    _tcfg = cfg.get("translate", {}) or {}
    if not _tcfg.get("notes", True):
        for b in blocks:
            if b["type"] == "note" and not b.get("zh"):
                b["zh"] = b["text"]
    if _tcfg.get("bibliography", False):
        for b in blocks:
            if b["type"] == "bib":
                b["zh"] = ""
    for b in blocks:
        if b["type"] == "bib" and not b.get("zh") and not _tcfg.get("bibliography", False):
            b["zh"] = b["text"].strip()
        elif b["type"] == "byline" and not b.get("zh"):
            b["zh"] = b["text"].strip()
    todo = [b for b in blocks if not b.get("zh")]
    if not _tcfg.get("notes", True):
        todo = [b for b in todo if b["type"] != "note"]

    stage("翻译")
    milestones = {"pct": -1}

    def translate_progress(done, total):
        pct = int(done * 100 / max(1, total))
        if pct >= milestones["pct"] + 10 or (done >= total and milestones["pct"] < 100):
            milestones["pct"] = pct
            log("[翻译] %d/%d 块 · %d%%" % (done, total, pct))
        if progress:
            stage("翻译", done, total)

    tr.translate_blocks(client, todo, glossary, workers=cfg["api"]["workers"],
                        chunk_size=cfg["chunk_words"], dry_run=dry_run or not client.key,
                        progress=translate_progress, stop=stop, source_gloss=gloss_mode)

    stage("修复")
    if client.key and not dry_run:
        fixed = tr.fix_headings(client, blocks, stop=stop,
                                workers=cfg["api"].get("workers", 4),
                                progress=(lambda d, n: stage("修复", d, n)) if progress else None,
                                system=gloss_mod.system_prompt(gloss_mode))
        if fixed:
            log("headings repaired: %d" % fixed)
        fixed = tr.repair_blocks(client, blocks, stop=stop,
                                 workers=cfg["api"].get("workers", 4),
                                 progress=(lambda d, n: stage("修复", d, n)) if progress else None,
                                 system=gloss_mod.system_prompt(gloss_mode))
        if fixed:
            log("blocks repaired: %d" % fixed)
        fixed = tr.fix_english_terms(client, blocks, stop=stop,
                                     workers=cfg["api"].get("workers", 4),
                                     progress=(lambda d, n: stage("修复", d, n))
                                     if progress else None)
        if fixed:
            log("stray English translated: %d blocks" % fixed)
    missing = sum(1 for b in todo if not b.get("zh"))
    log("translate: %d blocks (%d unresolved)" % (len(todo), missing))

    stage("润色")
    if (cfg.get("polish") or {}).get("enabled", True) and client.key and not dry_run:
        pc = cfg.get("polish") or {}
        try:
            edited = tr.polish_blocks(
                client, blocks,
                batch_chars=int(pc.get("batch_chars", 6000)),
                max_blocks=int(pc.get("max_blocks", 8)),
                workers=cfg["api"].get("workers", 4), stop=stop,
                progress=(lambda d, n: stage("润色", d, n)) if progress else None)
            log("polish: %d blocks edited" % edited)
        except tr.StopRequested:
            raise
        except Exception as exc:                        # noqa: BLE001
            log("润色跳过：%s" % exc)

    stage("审校")
    ai_issues = []
    auto = (cfg.get("proof") or {}).get("mode", "auto")
    if auto == "auto" and client.key and not dry_run:
        try:
            fixed, ai_issues = proofread.ai_fix(
                client, blocks,
                limit=int((cfg.get("proof") or {}).get("limit", 40)),
                stop=stop, workers=cfg["api"].get("workers", 4),
                progress=(lambda d, n: stage("审校", d, n)) if progress else None)
            if fixed:
                log("ai 校对：自动修正 %d 处" % fixed)
        except tr.StopRequested:
            raise
        except Exception as exc:                        # noqa: BLE001
            log("ai 校对跳过：%s" % exc)

    stage("图版")
    log("epub: 插图已按原书位置嵌入，无需再裁切")

    stage("人名")
    pairs = {}
    if cfg["names"].get("enabled", True) and not dry_run and client.key:
        per_book = outdir / "names.tsv"
        recs = nm.load_records(per_book) if per_book.exists() else {}
        if not recs:
            known = nm.load_records(Path(cfg["paths"]["names"]))
            cands = nm.candidates(blocks, cfg["names"].get("limit", 150))
            fresh = [c for c in cands if c not in known]
            recs = {k: v for k, v in known.items() if k in cands}
            if fresh:
                recs.update(nm.translate_records(client, fresh, stop=stop))
        cited = nm.cited_names(blocks)
        for k, v in recs.items():
            if k in cited:
                v["kind"] = nm.SURNAME
        nm.save_tsv(per_book, recs)
        pairs = {k: v["zh"] for k, v in recs.items()}
        if pairs:
            pages = nm.localise(blocks, pairs, mode=gloss_mode)
            log("names: %d entries localised" % len(pages))
    forced_h1 = {id(b) for b in blocks if b["type"] == "h1"}
    # the source book already carries its own INDEX; finalize must not append one
    blocks = clean_mod.finalize(clean_mod.run(blocks), None)
    for b in blocks:
        if id(b) in forced_h1 and b["type"] == "h2":
            b["type"] = "h1"          # front matter on roman pages is still top level
    _apply_gloss(blocks, glossary, cfg, log)

    stage("排版")
    check()
    info = dict(book_in.get("meta") or {})
    if api:
        info = meta_mod.translate_fields(client, info, stop=stop)
        log("metadata: %s / %s / %s" % (info.get("title", "")[:40], info.get("author", "")[:30],
                                        info.get("publisher", "")[:30]))
    meta = {"title": path.stem, "pages": book_in["probe"]["pages"],
            "title_zh": info.get("zh_title") or info.get("title", ""),
            "title_en": info.get("title", ""),
            "subtitle": info.get("zh_subtitle") or info.get("subtitle", ""),
            "author": info.get("zh_author") or info.get("author", ""),
            "author_en": info.get("author", ""),
            "publisher": info.get("zh_publisher") or info.get("publisher", ""),
            "year": info.get("year", "")}
    apply_layout_flags(meta, cfg)
    book = {"meta": meta, "labels": labels, "blocks": blocks, "page_map": page_map}
    book["meta"].update(meta)
    (outdir / "book.json").write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")

    docx = outdir / (path.stem[:40] + "_zh_%s.docx" % preset)
    build_stats = {}
    bd.build_doc(book, preset, docx, meta, stats=build_stats)

    stage("页码")
    preview = outdir / "preview.pdf"
    pages_map = {}
    try:
        pages_map = paginate(book, docx, preview, preset, cfg, stop=stop,
                             stats=build_stats, log=log)
    except tr.StopRequested:
        raise
    except Exception as _e:                       # LibreOffice/node missing: keep the docx usable
        log("paginate skipped: %s" % _e)
        pages_map = {}
    if not pages_map:
        log("目录页码：未找到 LibreOffice 或本书无目录，本次目录不含页码")
    log("build: %s (%d KB), 目录条目 %d 条" % (docx.name, docx.stat().st_size // 1024, len(pages_map)))
    report = proofread.write_report(outdir, book, stats=build_stats,
                                    ai_issues=ai_issues, preset=preset, log=log)
    clean_outputs(outdir, docx, preview, keep_pdf, keep_work, stem=path.stem)
    if not keep_work:
        log("输出：%s%s" % (docx.name, " + preview.pdf" if keep_pdf and preview.exists() else ""))
    if current["name"]:
        idx = STAGES.index(current["name"]) + 1 if current["name"] in STAGES else 0
        log("[阶段 %d/%d %s] 完成 · %s"
            % (idx, len(STAGES), current["name"], _fmt_dur(time.monotonic() - current["t"])))
    log("总用时 %s" % _fmt_dur(time.monotonic() - started))
    return {"ok": True, "docx": docx, "preview": preview if keep_pdf and preview.exists() else None,
            "blocks": len(blocks), "toc_pages": len(pages_map), "report": report,
            "usage": client.usage, "cost": client.cost(),
            "elapsed": time.monotonic() - started}


def _run_docx(path: Path, outdir: Path, cfg: dict, *, preset: str = "print",
              dry_run: bool = False, pages_filter: str | None = None,
              log=print, progress=None, stop=None,
              keep_work: bool = True, keep_pdf: bool = True,
              cache_dir: Path | None = None) -> dict:
    """DOCX input: headless PDF conversion, then the normal PDF pipeline.

    Word's own outline is applied on top of the font-size heuristics, so the
    translated book keeps the author's chapter/section hierarchy exactly.
    """
    started = time.monotonic()
    outdir.mkdir(parents=True, exist_ok=True)
    pdf = outdir / "_tmp" / (path.stem[:40] + ".pdf")
    if progress:
        progress("解析", 1, 3, 1, len(STAGES))
    log("docx: 转换 PDF（Word/WPS/LibreOffice）…")
    ok = docx_mod.convert(path, pdf, soffice=find_soffice(cfg),
                          node=(cfg.get("figures") or {}).get("node"),
                          cli=(cfg.get("figures") or {}).get("cli"),
                          log=log, stop=stop)
    if not ok:
        if stop is not None and stop.is_set():
            raise tr.StopRequested()
        return {"ok": False, "reason": "docx-convert"}
    heads = docx_mod.headings(path)
    meta = docx_mod.core_meta(path)
    log("docx: %s · Word 标题 %d 个 · 元数据 %s"
        % (path.name, len(heads), meta.get("title") or path.stem))
    res = _run_impl(pdf, outdir, cfg, preset=preset, dry_run=dry_run,
                    pages_filter=pages_filter, log=log,
                    progress=progress, stop=stop, keep_work=keep_work,
                    keep_pdf=keep_pdf, cache_dir=cache_dir, use_toc=False,
                    style_heads=heads, input_meta=meta)
    if res.get("ok"):
        res["elapsed"] = time.monotonic() - started
    return res


def rebuild(book_path: Path, docx: Path, cfg: dict, *, preset: str = "print",
            structure: list[dict] | None = None, log=print) -> dict:
    """Re-apply chapter structure and re-typeset an already translated book (no API needed)."""
    book = json.loads(Path(book_path).read_text(encoding="utf-8"))
    blocks = book["blocks"]

    if structure:
        keep = [b for b in blocks if b["type"] != "h1"]
        for ch in sorted(structure, key=lambda c: int(c["page"])):
            idx = next((i for i, b in enumerate(keep)
                        if str(b["page"]).isdigit() and int(b["page"]) >= int(ch["page"])), None)
            if idx is None:
                continue
            keep.insert(idx, {"page": str(ch["page"]), "type": "h1",
                              "text": ch["title"], "zh": ch.get("zh") or ch["title"]})
        j = next((i for i, b in enumerate(keep) if b["type"] == "bib" and "\u3000" in b.get("zh", "")), None)
        if j is not None:
            keep.insert(j, {"page": keep[j]["page"], "type": "h1",
                            "text": "NAME INDEX", "zh": "人名索引"})
        idx = next((i for i, b in enumerate(keep) if b["type"] == "bib"), None)
        if idx is not None and not any(b["type"] == "h1" and b["zh"].startswith("参考") for b in keep):
            keep.insert(idx, {"page": keep[idx]["page"], "type": "h1",
                              "text": "BIBLIOGRAPHY", "zh": "参考文献"})
        blocks = keep

    pairs = nm.load_tsv(Path(cfg["paths"]["names"])) if cfg["names"].get("enabled", True) else {}
    glossary = load_tsv(Path(cfg["paths"]["glossary"]))
    per_book_glossary = Path(book_path).parent / "glossary.tsv"
    if per_book_glossary.exists():
        glossary.update(load_tsv(per_book_glossary))
    blocks = clean_mod.finalize(clean_mod.run(blocks), pairs)
    _apply_gloss(blocks, glossary, cfg, log)
    book["blocks"] = blocks
    apply_layout_flags(book.setdefault("meta", {}), cfg)
    Path(book_path).write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
    build_stats = {}
    bd.build_doc(book, preset, docx, book["meta"], stats=build_stats)
    pages = paginate(book, docx, docx.parent / "preview.pdf", preset, cfg,
                     stats=build_stats, log=log)
    proofread.write_report(docx.parent, book, stats=build_stats, preset=preset, log=log)
    log("rebuild: %s (%d blocks, toc %d)" % (docx.name, len(blocks), len(pages)))
    return {"docx": docx, "blocks": len(blocks), "toc_pages": len(pages)}
