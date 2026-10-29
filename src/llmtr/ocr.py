"""Vision-model OCR for individual poor-quality pages (never for whole scans)."""
from __future__ import annotations

import re

DEFAULTS = {"dpi": 170, "max_pages": 0, "mode": "auto", "min_chars": 120,
            "max_bad_ratio": 0.35, "layout_dpi": 200, "max_side": 1600,
            "spread": "auto", "judge": True, "judge_max": 40, "judge_dpi": 200,
            "judge_workers": 3, "judge_retries": 1}

MARK = re.compile(r"^\[(PAGE|H1|H2|P|N)\]\s*(.*)$", re.I | re.M)

# a scanned book is often captured two facing pages at a time; a single portrait
# page is never wider than tall, so a landscape full-page raster is a spread
SPREAD_RATIO = 1.2


def is_spread(page, mode: str = "auto") -> bool:
    """True when the page holds two facing book pages side by side."""
    if mode == "off":
        return False
    if mode == "force":
        return True
    r = page.rect
    if r.width / max(1.0, r.height) < SPREAD_RATIO:
        return False
    area = r.width * r.height
    for img in page.get_images(full=True):
        try:
            rects = page.get_image_rects(img[0])
        except Exception:                              # noqa: BLE001
            continue
        for ir in rects:
            if ir.width * ir.height > 0.8 * area:
                return True
    return False


def halves(page):
    """Left/right clip rectangles of a spread page."""
    import pymupdf

    r = page.rect
    mid = (r.x0 + r.x1) / 2
    return [pymupdf.Rect(r.x0, r.y0, mid, r.y1), pymupdf.Rect(mid, r.y0, r.x1, r.y1)]


def parse_marked(text: str):
    """'[PAGE] 12 / [H2] title / [P] para' -> (label, [(type, text), ...])."""
    label = None
    items: list[tuple[str, str]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = MARK.match(line)
        if not m:
            if items:                                  # wrapped continuation line
                typ, prev = items[-1]
                items[-1] = (typ, (prev + " " + line).strip())
            continue
        tag, body = m.group(1).upper(), m.group(2).strip()
        if tag == "PAGE":
            if re.fullmatch(r"\d{1,4}|[ivxlcdm]{1,7}", body, re.I):
                label = body.lower()
            continue
        if not body:
            continue
        if tag == "N" and len(re.findall(r"[A-Za-z]", body)) < 2:
            continue                       # "[?]" / a bare note number: nothing was read
        items.append(({"H1": "h1", "H2": "h2", "P": "p", "N": "note"}[tag], body))
    return label, items


def pages(pdf, client, cfg: dict, log=print, only=None, stop=None):
    """Transcribe the given page indices (or all when only=None)."""
    from pathlib import Path

    import pymupdf

    from . import vision
    from .translate import StopRequested

    doc = pymupdf.open(str(pdf))
    idx = list(only) if only is not None else list(range(len(doc)))
    if cfg.get("max_pages"):
        idx = idx[: int(cfg["max_pages"])]
    out = []
    tmp = Path(cfg.get("tmp_dir") or (Path(pdf).resolve().parent / "_ocr_tmp"))
    tmp.mkdir(parents=True, exist_ok=True)
    for n, i in enumerate(idx):
        if stop is not None and stop.is_set():
            raise StopRequested()
        page = doc[i]
        img = tmp / ("p%04d.png" % i)
        page.get_pixmap(dpi=cfg.get("dpi", 170)).save(str(img))
        try:
            text = vision.transcribe(client, img, stop=stop)
        except StopRequested:
            raise
        except Exception as exc:                     # noqa: BLE001
            log("ocr 第 %d 页失败：%s" % (i + 1, exc))
            text = ""
        out.append(text.strip())
        if (n + 1) % 5 == 0:
            log("ocr: %d/%d 页" % (n + 1, len(idx)))
        img.unlink(missing_ok=True)
    return out


def parse_review(text: str):
    """'[OK]' or '[FIX] + marked layout' -> (verdict, label, items)."""
    raw = (text or "").strip()
    if re.match(r"^\[?\s*OK\s*\]?", raw, re.I) and not MARK.search(raw):
        return "ok", None, []
    body = re.sub(r"^\[?\s*FIX\s*\]?\s*", "", raw, flags=re.I)
    label, items = parse_marked(body)
    return ("fix" if items else "unclear"), label, items


def review_pages(pdf, client, cfg: dict, texts, log=print, only=None, stop=None):
    """Ask the vision model to judge suspect pages and rewrite the bad ones.

    One call per page: the OCR text is shown next to the page image, so the model
    can reply ``[OK]`` cheaply or return a full structured transcription. Pages are
    rendered up front and the API calls run concurrently; results keep page order.
    """
    from pathlib import Path

    import pymupdf

    from . import vision
    from .translate import StopRequested, _parallel

    doc = pymupdf.open(str(pdf))
    idx = list(only) if only is not None else list(range(len(doc)))
    tmp = Path(cfg.get("tmp_dir") or (Path(pdf).resolve().parent / "_ocr_tmp"))
    tmp.mkdir(parents=True, exist_ok=True)
    results: dict[int, dict] = {}
    jobs = []
    for i in idx:
        page = doc[i]
        if is_spread(page, cfg.get("spread", "auto")):
            results[i] = {"src": i, "verdict": "skipped", "label": None, "items": [],
                          "note": "跨版页不做单页判读"}
            continue
        img = tmp / ("r%04d.png" % i)
        page.get_pixmap(dpi=int(cfg.get("judge_dpi", 200))).save(str(img))
        jobs.append((i, img))
    doc.close()

    def one(job) -> None:
        i, img = job
        try:
            raw = ""
            try:
                raw = vision.review_layout(
                    client, img, texts[i] if i < len(texts) else "",
                    max_side=int(cfg.get("max_side", 1600)), stop=stop)
            except StopRequested:
                raise
            except Exception as exc:                 # noqa: BLE001
                log("OCR 判读第 %d 页失败：%s" % (i + 1, exc))
            verdict, label, items = parse_review(raw)
            tries = max(0, int(cfg.get("judge_retries", 1)))
            while verdict == "unclear" and tries:
                tries -= 1
                log("OCR 判读第 %d 页输出无法解析，重试" % (i + 1))
                try:
                    raw = vision.review_layout(
                        client, img, texts[i] if i < len(texts) else "",
                        max_side=int(cfg.get("max_side", 1600)), stop=stop,
                        strict=True)
                except StopRequested:
                    raise
                except Exception as exc:             # noqa: BLE001
                    log("OCR 判读第 %d 页重试失败：%s" % (i + 1, exc))
                    break
                verdict, label, items = parse_review(raw)
            if verdict == "unclear":
                log("OCR 判读第 %d 页未定：%s"
                    % (i + 1, " ".join((raw or "").split())[:100] or "空响应"))
            results[i] = {"src": i, "verdict": verdict, "label": label,
                          "items": items, "note": ""}
        finally:
            img.unlink(missing_ok=True)

    def progress(done: int, total: int) -> None:
        if total and (done % 5 == 0 or done == total):
            log("OCR 判读：%d/%d 页" % (done, total))

    try:
        _parallel([lambda job=job: one(job) for job in jobs],
                  int(cfg.get("judge_workers", 3)), stop=stop,
                  progress=progress if jobs else None)
    finally:
        for _i, img in jobs:
            img.unlink(missing_ok=True)
    return [results[i] for i in idx]


def layout_pages(pdf, client, cfg: dict, log=print, only=None, progress=None, stop=None):
    """Structural OCR of whole pages.

    Returns one entry per *printed* page: ``{"label", "items", "src", "half"}``
    where ``src`` is the PDF page index and ``half`` is 0/1 for a spread (else
    None). Spread pages are transcribed half by half so text is not interleaved
    and each half keeps its own printed page number.
    """
    from pathlib import Path

    import pymupdf

    from . import vision
    from .translate import StopRequested

    doc = pymupdf.open(str(pdf))
    idx = list(only) if only is not None else list(range(len(doc)))
    if cfg.get("max_pages"):
        idx = idx[: int(cfg["max_pages"])]
    tmp = Path(cfg.get("tmp_dir") or (Path(pdf).resolve().parent / "_ocr_tmp"))
    tmp.mkdir(parents=True, exist_ok=True)
    spread_mode = cfg.get("spread", "auto")
    dpi = int(cfg.get("layout_dpi", 200))
    out = []
    for n, i in enumerate(idx):
        if stop is not None and stop.is_set():
            raise StopRequested()
        page = doc[i]
        clips = halves(page) if is_spread(page, spread_mode) else [None]
        for k, clip in enumerate(clips):
            tag = "" if clip is None else "_%d" % k
            img = tmp / ("l%04d%s.png" % (i, tag))
            page.get_pixmap(dpi=dpi, clip=clip).save(str(img))
            try:
                text = vision.transcribe_layout(client, img, max_side=int(cfg.get("max_side", 1600)),
                                                stop=stop)
            except StopRequested:
                raise
            except Exception as exc:                 # noqa: BLE001
                log("layout ocr 第 %d 页%s失败：%s" % (i + 1, "" if clip is None else "第%d半页" % (k + 1), exc))
                text = ""
            label, items = parse_marked(text)
            out.append({"label": label, "items": items, "src": i,
                        "half": None if clip is None else k})
            img.unlink(missing_ok=True)
        if progress:
            progress(n + 1, len(idx))
        elif (n + 1) % 5 == 0:
            log("图像识别：%d/%d 页" % (n + 1, len(idx)))
    return out
