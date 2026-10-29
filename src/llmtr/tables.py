"""Whitespace-column table detection (born-digital and scan text layers)."""
from __future__ import annotations

import bisect
import collections
import re
import statistics

from .ingest import labels_for_src, page_index

FIG_CAP = re.compile(r"(?i)^\s*(?:figure|fig\.|map|plate|chart|图|地图)\s*\d")

DEFAULTS = {"min_gap": 14.0, "min_rows": 3, "max_rows": 40, "min_cols": 2, "max_cols": 8,
            "max_cell": 40, "max_pages": 30, "dpi": 220, "confirm": True}


def _lines(page):
    rows = collections.defaultdict(list)
    for w in page.get_text("words"):
        rows[round(w[1] / 5)].append(w)
    return [sorted(v, key=lambda x: x[0]) for _, v in sorted(rows.items())]


def _gaps(words, min_gap):
    return [(a[2] + b[0]) / 2.0 for a, b in zip(words, words[1:]) if b[0] - a[2] >= min_gap]


def _band_table(lines, gaps, band, cfg):
    pool = [round(x) for k in band for x in gaps[k]]
    common = sorted({b for b, n in collections.Counter(pool).items() if n >= len(band) - 1})
    ncols = len(common) + 1
    if not (cfg["min_cols"] <= ncols <= cfg["max_cols"]):
        return None
    rows = []
    for k in band:
        cells = [[] for _ in range(ncols)]
        for w in lines[k]:
            idx = min(bisect.bisect_right(common, w[0] - 4), ncols - 1)
            cells[idx].append(w[4])
        if any(not c for c in cells):
            return None
        rows.append([" ".join(c) for c in cells])
    flat = [c for r in rows for c in r]
    if not flat or statistics.median(len(c) for c in flat) > cfg["max_cell"]:
        return None
    xs = [w[0] for k in band for w in lines[k]]
    ys = [w[1] for k in band for w in lines[k]]
    xe = [w[2] for k in band for w in lines[k]]
    ye = [w[3] for k in band for w in lines[k]]
    return ((min(xs), min(ys), max(xe), max(ye)), rows)


def detect_page(page, cfg: dict):
    """Return (bbox, rows) for the largest table-like band on the page."""
    lines = [l for l in _lines(page) if len(l) >= 2]
    if len(lines) < cfg["min_rows"]:
        return None
    gaps = [_gaps(l, cfg["min_gap"]) for l in lines]
    best, band = None, []
    for i in range(len(lines) + 1):
        g = gaps[i] if i < len(lines) else None
        if g:
            band.append(i)
            continue
        if len(band) >= cfg["min_rows"]:
            cand = _band_table(lines, gaps, band, cfg)
            if cand and (best is None or len(cand[1]) > len(best[1])):
                best = cand
        band = []
    return best


def _find_tables(page, cfg):
    """PyMuPDF table finder with sanity filters (works on ruled and positioned tables)."""
    for strategy in ("lines", "text"):
        try:
            found = page.find_tables(strategy=strategy)
        except Exception:
            continue
        for tb in found.tables:
            if not (cfg["min_rows"] <= tb.row_count <= cfg["max_rows"]):
                continue
            if not (cfg["min_cols"] <= tb.col_count <= cfg["max_cols"]):
                continue
            rows = [[ (c or "").strip() for c in r ] for r in tb.extract()]
            flat = [c for r in rows for c in r]
            fill = sum(1 for c in flat if c) / max(1, len(flat))
            med = statistics.median(len(c) for c in flat)
            if fill < 0.9:
                continue
            if strategy != "lines" and (max(len(c) for c in flat) > 120
                                        or any(len(c.split()) > 15 for c in flat)):
                continue
            if rows and any(not c for c in rows[0]):
                continue                      # header row must be complete
            if any(all(not r[i] for r in rows) for i in range(len(rows[0]))):
                continue                      # an entirely empty column
            if tb.row_count > 20 and med < 25:
                continue                      # dense fragment grid = text columns
            if strategy != "lines" and med > cfg["max_cell"]:
                continue
            if sum(1 for r in rows if all(c for c in r)) < min(3, len(rows)):
                continue
            x0, y0, x1, y1 = tb.bbox
            area = (x1 - x0) * (y1 - y0) / (page.rect.width * page.rect.height)
            if area > 0.75:
                continue
            return ((x0, y0, x1, y1), rows)
    return None


def candidates(pdf, labels, cfg: dict, page_map=None):
    import pymupdf

    doc = pymupdf.open(str(pdf))
    out = []
    for i, page in enumerate(doc):
        on_page = labels_for_src(labels, page_map, i)
        label = next((l for l in on_page if str(l).isdigit()), "")
        if not str(label).isdigit():
            continue
        head = page.get_text()[:300].lower()
        if any(k in head for k in ("contents", "illustrations", "figures", "maps", "preface", "index")):
            continue
        got = _find_tables(page, cfg)
        if not got and len(page.get_text()) < 1200:
            got = detect_page(page, cfg)
        if got:
            bbox = got[0]
            # a spread holds two printed pages: tag the table with the half it sits in
            if len(on_page) > 1:
                mid = (page.rect.x0 + page.rect.x1) / 2
                label = on_page[0] if (bbox[0] + bbox[2]) / 2 < mid else on_page[-1]
            out.append((int(label), bbox, got[1]))
        if len(out) >= cfg["max_pages"]:
            break
    return out


def encode(rows) -> str:
    return " ||| ".join(" | ".join(c for c in r) for r in rows)


def _crop(pdf, page_label, labels, bbox, out, cfg, page_map=None, dpi=None):
    import pymupdf

    idx = page_index(page_label, labels, page_map)
    if idx is None:
        return None
    doc = pymupdf.open(str(pdf))
    pix = doc[idx].get_pixmap(clip=pymupdf.Rect(bbox), dpi=dpi or cfg["dpi"])
    out.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out))
    return out


def replace(blocks, found, pdf, labels, assets, cfg, client=None, page_map=None, stop=None):
    """Embed every detected table as a high-resolution image, like a figure.

    The old Word rebuilt table is gone: the crop keeps the book's original
    layout, rules and typeface intact. Only the blocks whose words fall inside
    the table region are removed; surrounding prose stays put.
    """
    import pymupdf

    from . import vision
    from .images import CAPTION_RE
    from .translate import StopRequested

    doc = pymupdf.open(str(pdf))
    dpi = max(300, int(cfg.get("dpi", 220)))
    done = []
    for n, (page_label, bbox, _rows) in enumerate(found):
        if stop is not None and stop.is_set():
            raise StopRequested()
        idx = page_index(page_label, labels, page_map)
        if idx is None:
            continue
        page = doc[idx]
        rect = pymupdf.Rect(bbox) & page.rect
        if rect.width < 20 or rect.height < 20:
            continue
        path = assets / ("t_%s_%d.png" % (page_label, n))
        assets.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            page.get_pixmap(clip=rect, dpi=dpi).save(str(path))
        if client is not None and cfg.get("confirm", True):
            try:
                if "否" in vision.ask(client, path, "这张图是表格或数据版面吗（不是正文段落）？只回答 是 或 否。",
                                       10, stop=stop):
                    path.unlink(missing_ok=True)
                    continue
            except StopRequested:
                raise
            except Exception:
                pass
        caption, cap_block = "", None
        for b in blocks:
            t = (b.get("text") or "").strip()
            # a figure caption on the same page does not belong to this table
            if (str(b["page"]) == str(page_label) and b["type"] in ("p", "h2", "note")
                    and len(t) <= 400 and CAPTION_RE.match(t) and not FIG_CAP.match(t)):
                caption = (b.get("zh") or t).strip()[:120]
                cap_block = b
                break
        inside = {w[4].lower().strip(".,;:()[]{}\"'“”‘’")
                  for w in page.get_text("words")
                  if rect.x0 <= (w[0] + w[2]) / 2 <= rect.x1
                  and rect.y0 <= (w[1] + w[3]) / 2 <= rect.y1}
        kept, pos = [], None
        for b in blocks:
            if str(b["page"]) != str(page_label) or b["type"] in ("table", "figure"):
                kept.append(b)
                continue
            if b is cap_block:
                continue                   # the caption moves under the image
            ws = [w.lower().strip(".,;:()[]{}\"'“”‘’")
                  for w in (b.get("text") or "").split()]
            ws = [w for w in ws if w]
            hit = sum(1 for w in ws if w in inside) / max(1, len(ws))
            if inside and ws and len(b.get("text") or "") <= 600 and hit >= 0.6:
                if pos is None:
                    pos = len(kept)
                continue
            kept.append(b)
        if pos is None:
            last = max((i for i, b in enumerate(kept)
                        if str(b["page"]) == str(page_label)), default=None)
            pos = len(kept) if last is None else last + 1
        kept.insert(pos, {"page": str(page_label), "type": "table", "text": "", "zh": "",
                          "image": str(path),
                          "caption": caption or ("表　原书 p.%s" % page_label)})
        blocks[:] = kept
        done.append(page_label)
    return blocks, done
