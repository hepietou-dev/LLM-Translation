"""Figure regions: cluster vector drawings + images into chart-like areas and embed them."""
from __future__ import annotations

import re
from pathlib import Path

from .ingest import labels_for_src, page_index

PUNCT = ".,;:()[]{}\"\u201c\u201d\u2018\u2019'"

DEFAULTS = {"min_area_pct": 5.0, "min_side_pct": 20.0, "min_height_pct": 10.0, "inset": 3.0,
            "dpi": 300, "gap": 8.0, "confirm": False}


def _rects(page):
    out = []
    try:
        for d in page.get_drawings():
            r = d.get("rect")
            if r and r.width * r.height > 4:
                out.append((r.x0, r.y0, r.x1, r.y1))
    except Exception:
        pass
    for img in page.get_images(full=True):
        for r in page.get_image_rects(img[0]):
            out.append((r.x0, r.y0, r.x1, r.y1))
    return out


def _merge(rects, gap):
    boxes = list(rects)
    changed = True
    while changed:
        changed = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if (a[0] - gap < b[2] and b[0] - gap < a[2]
                        and a[1] - gap < b[3] and b[1] - gap < a[3]):
                    boxes[i] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                    boxes.pop(j)
                    changed = True
                    break
            if changed:
                break
    return boxes


def find(pdf, labels, cfg: dict, exclude=None, skip=None, page_map=None, stop=None, progress=None):
    import pymupdf

    from .translate import StopRequested

    doc = pymupdf.open(str(pdf))
    # scan detection: most pages are a full-page raster image -> the whole-page
    # image heuristic is meaningless (every page qualifies), so it is disabled.
    scan_hits = 0
    sample = min(12, len(doc))
    for _p in doc[:sample]:
        _W, _H = _p.rect.width, _p.rect.height
        for _img in _p.get_images(full=True):
            for _r in _p.get_image_rects(_img[0]):
                if _r.width * _r.height > 0.9 * _W * _H:
                    scan_hits += 1
                    break
    is_scan = scan_hits >= max(2, int(sample * 0.5))
    out = []
    for i, page in enumerate(doc):
        if stop is not None and stop.is_set():
            raise StopRequested()
        if progress and i % 10 == 0:
            progress(i, len(doc))
        on_page = labels_for_src(labels, page_map, i)
        label = on_page[0] if on_page else ""
        is_digit = str(label).isdigit()
        numbered = is_digit or re.fullmatch(r"[ivxlcdm]{1,7}", str(label), re.I)
        if not numbered:
            continue
        if skip and i in skip:
            continue
        W, H = page.rect.width, page.rect.height
        text_chars = len(re.sub(r"\s", "", page.get_text()))

        # vector artwork: dense drawing coverage with little text (maps/charts)
        # runs on any numbered page, incl. roman front matter (e.g. a map on p.ii)
        vrects = [d.get("rect") for d in page.get_drawings() if d.get("rect")]
        vrects = [r for r in vrects if r.width * r.height > 4]
        if vrects:
            vcover = min(1.0, sum(r.width * r.height for r in vrects) / (W * H))
            x0 = min(r.x0 for r in vrects); y0 = min(r.y0 for r in vrects)
            x1 = max(r.x1 for r in vrects); y1 = max(r.y1 for r in vrects)
            if (vcover > 0.25 and text_chars < 500
                    and (x1 - x0) > W * 0.3 and (y1 - y0) > H * 0.3):
                out.append((str(label), (x0, y0, x1, y1)))
                continue

        # everything below stays arabic-page only: scanned front matter (title/TOC)
        # is a full-page raster and must not be embedded as a figure.
        if not is_digit:
            continue
        if is_scan:
            continue
        # no-caption artwork: whole page is an image with little text (maps/plates)
        img_cov = 0.0
        for img in page.get_images(full=True):
            for r in page.get_image_rects(img[0]):
                img_cov = max(img_cov, r.width * r.height / (W * H))
        if img_cov > 0.35 and text_chars < 500:
            out.append((str(label), (8, 8, W - 8, H - 8)))
            continue
        rects = _rects(page)
        if exclude:
            keep = []
            for r in rects:
                bad = False
                for (lp, bb) in exclude:
                    if lp != str(label):
                        continue
                    ix = max(0.0, min(r[2], bb[2]) - max(r[0], bb[0]))
                    iy = max(0.0, min(r[3], bb[3]) - max(r[1], bb[1]))
                    if ix * iy > 0.8 * max(1e-6, (r[2] - r[0]) * (r[3] - r[1])):
                        bad = True
                        break
                if not bad:
                    keep.append(r)
            rects = keep
        boxes = _merge(rects, cfg["gap"])
        boxes.sort(key=lambda b: b[1])
        merged = []
        for b in boxes:
            if merged:
                a = merged[-1]
                if b[1] - a[3] < 25 and min(a[2], b[2]) - max(a[0], b[0]) > 0.4 * min(a[2] - a[0], b[2] - b[0]):
                    merged[-1] = (min(a[0], b[0]), a[1], max(a[2], b[2]), max(a[3], b[3]))
                    continue
            merged.append(b)
        for x0, y0, x1, y1 in merged:
            w, h = x1 - x0, y1 - y0
            if w * h / (W * H) * 100 < cfg["min_area_pct"]:
                continue
            if w / W * 100 < cfg["min_side_pct"] or h / H * 100 < cfg["min_height_pct"]:
                continue
            if w > W * 0.95 and h > H * 0.9:      # whole page = scan, handled elsewhere
                continue
            if h > H * 0.75 or w > W * 0.92:      # a full-height band is usually layout
                continue
            try:
                tabs = [t.bbox for t in page.find_tables(strategy="lines").tables]
            except Exception:
                tabs = []
            is_table = any(
                (min(x1, t[2]) - max(x0, t[0])) * (min(y1, t[3]) - max(y0, t[1]))
                > 0.5 * max(1e-6, (x1 - x0) * (y1 - y0)) for t in tabs)
            if is_table:
                continue
            words = [w for w in page.get_text("words")
                     if x0 <= (w[0] + w[2]) / 2 <= x1 and y0 <= (w[1] + w[3]) / 2 <= y1]
            inside = [r for r in rects
                      if x0 <= (r[0] + r[2]) / 2 <= x1 and y0 <= (r[1] + r[3]) / 2 <= y1]
            cover = sum(max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1]) for r in inside) / max(1e-6, w * h)
            text_chars = sum(len(w[4]) for w in words)
            if len(words) >= 25 or text_chars >= 180:
                continue                     # a block of prose, not artwork
            if cover < 0.10 and (len(words) > 25 or text_chars > 250):
                continue
            out.append((str(label), (x0, y0, x1, y1)))
    if progress:
        progress(len(doc), len(doc))
    return out


def _upright(client, path, stop=None) -> None:
    """Full-page plates are often printed sideways; ask the vision model which way is up."""
    from . import vision
    from .translate import StopRequested
    try:
        ans = vision.ask(client, path,
                         "这张书页插图里的文字是否正着读？若需要顺时针旋转才能正常阅读，"
                         "只回答 90、180 或 270；已经在正常方向就回答 0。只输出一个数字。", 8, stop=stop)
    except StopRequested:
        raise
    except Exception:                                  # noqa: BLE001
        return
    m = re.search(r"\b(0|90|180|270)\b", ans or "")
    if not m or m.group(1) == "0":
        return
    try:
        from PIL import Image
        Image.open(path).rotate(-int(m.group(1)), expand=True).save(path)
    except Exception:                                  # noqa: BLE001
        pass


def inject(blocks, found, pdf, labels, assets, cfg: dict, client=None, page_map=None,
           stop=None, progress=None):
    import pymupdf
    from . import vision
    from .translate import StopRequested

    doc = pymupdf.open(str(pdf))
    added = []
    for n, (page_label, bbox) in enumerate(found):
        if stop is not None and stop.is_set():
            raise StopRequested()
        if progress:
            progress(n, len(found))
        idx = page_index(page_label, labels, page_map)
        if idx is None:
            continue
        path = assets / ("f%s_%d.png" % (page_label, n))
        inset = cfg.get("inset", 3.0)
        page_rect = doc[idx].rect
        rect = pymupdf.Rect(bbox[0] + inset, bbox[1] + inset, bbox[2] - inset, bbox[3] - inset)
        rect = rect & page_rect                       # never crop beyond the page edge
        if not path.exists():
            pix = doc[idx].get_pixmap(clip=rect, dpi=cfg["dpi"])
            assets.mkdir(parents=True, exist_ok=True)
            pix.save(str(path))
        if client is not None:
            area = (rect.width * rect.height) / max(1.0, page_rect.width * page_rect.height)
            if area >= 0.3:
                _upright(client, path, stop=stop)
        if client is not None and cfg.get("confirm", True):
            try:
                if "否" in vision.ask(client, path, "这张图是图表、示意图或照片吗（不是表格、不是正文文字）？只回答 是 或 否。", 8, stop=stop):
                    continue
            except StopRequested:
                raise
            except Exception:
                pass
        end = next((i for i, b in enumerate(blocks) if str(b["page"]) == str(page_label)), None)
        if end is None:
            # a plate on a dropped front-matter page: place it before the first
            # block of any page that follows it in the document
            try:
                pos = labels.index(str(page_label))
            except ValueError:
                pos = -1
            later = {str(x) for x in labels[pos + 1:]} if pos >= 0 else set()
            end = next((i for i, b in enumerate(blocks) if str(b["page"]) in later), 0)
        while end < len(blocks) and str(blocks[end]["page"]) == str(page_label):
            end += 1
        inside = {w[4].lower() for w in doc[idx].get_text("words")
                  if rect.x0 <= (w[0] + w[2]) / 2 <= rect.x1 and rect.y0 <= (w[1] + w[3]) / 2 <= rect.y1}
        if inside:
            keep = []
            for b in blocks:
                if str(b["page"]) != str(page_label) or b["type"] in ("table", "figure"):
                    keep.append(b)
                    continue
                ws = [w.lower().strip(PUNCT) for w in b["text"].split()]
                ws = [w for w in ws if w]
                hit = sum(1 for w in ws if w in inside) / max(1, len(ws))
                if hit < 0.7 or len(b["text"]) > 200:
                    keep.append(b)
            blocks[:] = keep
            end = next((i for i, b in enumerate(blocks) if str(b["page"]) == str(page_label)), len(blocks))
            while end < len(blocks) and str(blocks[end]["page"]) == str(page_label):
                end += 1
        caption = "图　原书 p.%s" % page_label
        prev = end - 1
        while prev >= 0 and str(blocks[prev]["page"]) == str(page_label):
            prev -= 1
        if prev >= 0:
            prev_page = str(blocks[prev]["page"])
            for k in range(prev, max(-1, prev - 4), -1):
                x = blocks[k]
                if str(x["page"]) != prev_page:
                    break
                if x["type"] in ("table", "figure"):
                    continue
                t = (x.get("text") or "").strip()
                m = CAPTION_RE.search(t[:120]) if x["type"] in ("p", "h2", "note") \
                    and 8 <= len(t) <= 300 else None
                if not m or m.start() > 40:
                    break
                # pagination split a caption from its figure: the closing caption
                # of the previous page belongs under this image
                caption = ((x.get("zh") or "").strip() or t[m.start():].strip())[:160]
                del blocks[k]
                end -= 1
                break
        blocks.insert(end, {"page": str(page_label), "type": "figure", "text": "", "zh": "",
                            "image": str(path), "caption": caption})
        added.append(page_label)
    return blocks, added

CAPTION_RE = re.compile(r"(?:^|\s)(?:Figure|Fig\.|Map|Plate|Chart|Table|图|地图|表)\s+\d+(?:\.\d+)*(?!\d|\.\d)\s*[.:]", re.I)


def _caption_rect(page, caption: str):
    """Locate a caption on the page even when a running head was glued to its front."""
    lig = (caption.replace("ff", "\ufb00").replace("fi", "\ufb01")
           .replace("fl", "\ufb02"))
    for source in (caption, lig):
        for n in (80, 50, 30, 20):
            snippet = source[:n].strip()
            if len(snippet) < 8:
                continue
            try:
                rects = page.search_for(snippet)
            except Exception:
                rects = []
            if rects:
                return rects[0]
    # small-caps captions often defeat a phrase search; the figure number does not
    m = re.search(r"\d+(?:\.\d+)*", caption)
    if m:
        try:
            rects = page.search_for(m.group(0))
        except Exception:
            rects = []
        if rects:
            return sorted(rects, key=lambda r: r.y0)[0]
    return None


def caption_figures(pdf, labels, blocks, cfg, page_map=None, stop=None, progress=None):
    """A caption like 'Figure 2' anchors the nearest drawing/image region above it."""
    import pymupdf

    from .translate import StopRequested

    doc = pymupdf.open(str(pdf))
    changed = 0
    total = len(blocks)
    for idx, b in enumerate(list(blocks)):
        if stop is not None and stop.is_set():
            raise StopRequested()
        if progress and idx % 20 == 0:
            progress(idx, total)
        if b.get("type") not in ("p", "h2") or not b.get("text"):
            continue
        text = b["text"].strip()
        if len(text) > 400:
            continue                     # a prose paragraph that merely mentions a figure
        m = CAPTION_RE.search(text[:120])
        if not m or m.start() > 40:
            continue
        cap_text = text[m.start():].strip()
        page_label = str(b.get("page"))
        page_no = page_index(page_label, labels, page_map)
        if page_no is None:
            continue
        page = doc[page_no]
        r = _caption_rect(page, cap_text)
        if r is None:
            continue
        W, H = page.rect.width, page.rect.height
        areas = []
        for dr in page.get_drawings():
            rect = dr.get("rect")
            if not rect:
                continue
            if rect.y1 <= r.y0 + 4 and rect.y0 >= r.y0 - H * 0.5:
                areas.append((rect.x0, rect.y0, rect.x1, rect.y1))
        for img in page.get_images(full=True):
            for ir in page.get_image_rects(img[0]):
                if ir.y1 <= r.y0 + 4 and ir.y0 >= r.y0 - H * 0.5:
                    areas.append((ir.x0, ir.y0, ir.x1, ir.y1))
        if not areas:
            continue
        # only the artwork connected to the caption is the figure; a rule under a
        # running head (or a table row far above) must not stretch the crop over
        # the prose in between
        areas.sort(key=lambda a: a[3], reverse=True)
        x0, y0, x1, y1 = areas[0]
        grown = True
        while grown:
            grown = False
            for a in areas:
                if a[2] < x0 or a[0] > x1:
                    continue
                if a[3] < y0 - 24 or a[1] > y1 + 24:
                    continue
                if a[0] >= x0 and a[1] >= y0 and a[2] <= x1 and a[3] <= y1:
                    continue
                x0 = min(x0, a[0]); y0 = min(y0, a[1])
                x1 = max(x1, a[2]); y1 = max(y1, a[3])
                grown = True
        if y1 - y0 < 40 or x1 - x0 < 60:
            continue                   # a hairline rule or table edge, not a figure
        # diagram labels live just outside the vector strokes: take the words that
        # sit in the artwork band, never whole paragraphs above it
        for w in page.get_text("words"):
            if w[1] < y0 - 40 or w[1] > y1 + 6:
                continue
            if w[2] < x0 - 0.12 * W or w[0] > x1 + 0.12 * W:
                continue
            x0 = min(x0, w[0]); y0 = min(y0, w[1])
            x1 = max(x1, w[2]); y1 = max(y1, w[3])
        crop = pymupdf.Rect(x0 - 6, y0 - 6, x1 + 6, y1 + 6) & page.rect
        if crop.height > 0.72 * H or (crop.width * crop.height) < 0.02 * W * H:
            continue
        # labels inside the cropped artwork are part of the image, not footnotes
        inside = {w[4].lower().strip(PUNCT) for w in page.get_text("words")
                  if crop.x0 <= (w[0] + w[2]) / 2 <= crop.x1
                  and crop.y0 <= (w[1] + w[3]) / 2 <= crop.y1}
        prose = False
        for x in blocks:
            if x is b or str(x.get("page")) != page_label or x.get("type") != "p":
                continue
            if len(x.get("text") or "") < 120:
                continue
            ws = [w.lower().strip(PUNCT) for w in x["text"].split()]
            ws = [w for w in ws if w]
            if ws and sum(1 for w in ws if w in inside) / len(ws) >= 0.5:
                prose = True
                break
        if prose:
            continue                   # the crop would swallow a prose paragraph
        drop = set()
        if inside:
            for x in blocks:
                if x is b or str(x.get("page")) != page_label:
                    continue
                if x.get("type") not in ("p", "h2", "note"):
                    continue
                ws = [w.lower().strip(PUNCT) for w in (x.get("text") or "").split()]
                ws = [w for w in ws if w]
                if not ws or len(x["text"]) > 200:
                    continue
                if sum(1 for w in ws if w in inside) / len(ws) >= 0.7:
                    drop.add(id(x))
        if drop:
            blocks[:] = [x for x in blocks if id(x) not in drop]
        assets = Path(cfg.get("tmp_dir") or (Path(pdf).resolve().parent / "_fig_tmp"))
        assets.mkdir(parents=True, exist_ok=True)
        path = assets / ("cf_%s_%d.png" % (page_label, idx))
        page.get_pixmap(clip=crop, dpi=cfg.get("dpi", 200)).save(str(path))
        pos = next((i for i, x in enumerate(blocks) if x is b), None)
        if pos is None:
            continue
        blocks[pos] = {"page": page_label, "type": "figure", "text": "", "zh": "",
                       "image": str(path),
                       "caption": (b.get("zh") or cap_text).strip()[:120]}
        changed += 1
    if progress:
        progress(total, total)
    return blocks, changed

