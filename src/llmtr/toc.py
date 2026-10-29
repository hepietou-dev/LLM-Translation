"""Use the book's own CONTENTS pages as structural ground truth (chapters + sections)."""
from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader

NUM = re.compile(r"^(.*?)\s{2,}(\d{1,3})\s*$")
BACK = ("Preface", "Bibliography", "Index", "Contents")
BACK_HEAD = re.compile(
    r"(?i)^(?:(?:selected|general|annotated|classified)\s+)?"
    r"(?:bibliograph|references?\b|notes?\b|index\b|works cited|literature cited)")


def _lines(pdf: Path, scan: int = 20) -> list[str]:
    rd = PdfReader(str(pdf))
    out = []
    for p in rd.pages[:scan]:
        t = p.extract_text(extraction_mode="layout") or ""
        lines = [l.strip() for l in t.split("\n") if l.strip()]
        if lines and lines[0].upper().startswith("CONTENTS"):
            out += lines[1:]
    return out


def _entries_ok(entries: list[dict]) -> bool:
    """Reject a garbled contents parse instead of injecting wrong headings."""
    if len(entries) < 2:
        return False
    with_page = [e for e in entries if e.get("page")]
    if len(with_page) < max(2, len(entries) // 3):
        return False
    nums = [int(str(e["page"])) for e in with_page if str(e["page"]).isdigit()]
    if len(nums) >= 3:
        inversions = sum(1 for a, b in zip(nums, nums[1:]) if b < a - 2)
        if inversions > len(nums) * 0.4:
            return False
    return True


def _outline_entries(pdf: Path, labels: list[str] | None = None) -> list[dict]:
    """Fallback: the PDF's own bookmarks, mapped onto printed page labels."""
    try:
        import pymupdf
    except Exception:                                  # noqa: BLE001
        return []
    try:
        doc = pymupdf.open(str(pdf))
        marks = doc.get_toc(simple=True)
    except Exception:                                  # noqa: BLE001
        return []
    skip = re.compile(r"(?i)^(cover|title page|half[- ]title|copyright|contents|"
                      r"table of contents|目\s*录|目\s*錄|目\s*次)$")
    out = []
    for level, title, page in marks:
        t = re.sub(r"\s+", " ", (title or "")).strip()
        if not t or skip.match(t):
            continue
        pg = int(page or 0)
        printed = labels[pg - 1] if labels and 0 < pg <= len(labels) else ""
        out.append({"title": t, "page": str(printed or pg or ""),
                    "level": max(1, min(3, int(level or 1)))})
    return out if _entries_ok(out) else []


def find_entries(pdf: Path, labels: list[str] | None = None) -> list[dict]:
    """[{title, page, level}] parsed from the printed table of contents.

    Word-coordinate parsing (ingest._parse_contents) is tried first: it keeps the
    title/folio pairing of books where the author sits on an unnumbered line
    below the chapter. The old line-pairing parser remains as a fallback, then
    the PDF outline is used when the printed pages are unreadable.

    Level comes from numbering depth (1 → chapter, 1.1 → section, 1.1.1 →
    subsection) and, for unnumbered books, from the title's left indentation.
    """
    from .ingest import CN_KEY, _parse_contents
    entries = _parse_contents(pdf)
    out = _clean_entries(entries)
    if _entries_ok(out):
        return out
    outlined = _outline_entries(pdf, labels)
    if outlined:
        return outlined
    return out


def _clean_entries(entries: list[dict]) -> list[dict]:
    from .ingest import CN_KEY
    xs = [e["x0"] for e in entries
          if e.get("page") and e.get("x0") is not None]
    num_xs = [e["x0"] for e in entries
              if e.get("page") and e.get("x0") is not None
              and re.match(r"^(?:\d{1,3}(?:\.\d+)*[.)]?|[IVXLC]{1,6}[.)])\s",
                           e["title"].strip())]
    if num_xs:
        base = sorted(num_xs)[len(num_xs) // 2]     # numbered chapter titles
    else:
        base = min(xs) if xs else None
    out: list[dict] = []
    seen: set[str] = set()
    for e in entries:
        raw = e["title"].strip()
        t = re.sub(r"^(?:\d{1,3}(?:\.\d+)*[.)]?|[IVXLC]{1,6}[.)])\s+", "", raw).strip()
        t = re.sub(r"[.…·]{3,}.*$", "", t).strip()
        if len(t.split()) > 1:
            t = re.sub(r"\s+(?:Preface|Contents|Index|Notes|Bibliography|Appendix)$",
                       "", t).strip()
        if e.get("part"):
            t = re.sub(r"(?:\s+(?:\d{1,4}|[IVXLC]{1,6}[.)])){1,6}$", "", t).strip()
        if not t or re.fullmatch(r"(?i)(?:contents|table of contents|目\s*录|目\s*錄|目\s*次)", t):
            continue
        if re.fullmatch(r"(?i)(?:contents|tableofcontents|目录|目錄|目次|page)",
                        re.sub(r"\s+", "", t)):
            continue
        if len(re.findall(r"[A-Za-z\u4e00-\u9fff]", t)) < 2:
            continue                    # a bare "I." / "2." listing line
        if (len(t.split()) == 1 and len(t) >= 16
                and not re.search(r"[\u4e00-\u9fff]", t)):
            continue                    # OCR ran the words together: unusable title
        key = _key(t)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        level = 2 if e.get("level") == 2 else 1
        if re.match(r"^\d{1,3}\.\d+\.\d+", raw):
            level = 3
        elif re.match(r"^\d{1,3}\.\d+", raw):
            level = 2
        elif e.get("chapter") and e.get("page"):
            level = 1
        elif base is not None and e.get("x0") is not None and e["x0"] >= base + 6:
            level = 2
        page = str(e.get("page") or "")
        if re.fullmatch(r"[ivxlcdm]{1,7}", page, re.I) or CN_KEY.match(t) \
                or BACK_HEAD.match(t) or e.get("part"):
            level = 1                   # front/back matter / introduction / parts
        out.append({"title": t, "page": e.get("page"), "level": level})
    return out


def _sq(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


BACK_MATTER = re.compile(
    r"(?i)^(references?|bibliography|notes?|index|works cited|literature cited)\b")
FRONT_MATTER = re.compile(
    r"(?i)^(preface|foreword|acknowledg|introduction|conclusion|epilogue|prologue|"
    r"afterword|appendix|notes?\b|bibliograph|references?\b|index\b|list of|"
    r"about the|contributors|measures|abbreviations|glossary|coda|part\b|chapter\b)")
LEAD_NUM = re.compile(r"^\d{1,3}(?:\.\d+)*[.)]?\s+")
NUM3 = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\b")
NUM2 = re.compile(r"^\d{1,3}\.\d{1,3}\b")


def _key(s: str) -> str:
    return _sq(LEAD_NUM.sub("", (s or "").strip()))


def _matching(a: str, b: str) -> bool:
    ka, kb = _key(a), _key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    if len(kb) >= 8 and (kb in ka or ka in kb):
        return True
    n = min(len(ka), len(kb), 48)
    return n >= 12 and ka[:n] == kb[:n]


def _near(a: str, b: str) -> bool:
    if not a or not b:
        return True
    if a == b:
        return True
    return a.isdigit() and b.isdigit() and abs(int(a) - int(b)) <= 1


def _lookup(toc: dict, key: str, page: str):
    """TOC level for a body heading, tolerating truncated/OCR-damaged titles."""
    hit = toc.get(key)
    if hit and _near(hit[1], page):
        return hit[0]
    if len(key) >= 16:
        for k, (lvl, pg) in toc.items():
            if not _near(pg, page):
                continue
            if len(k) >= 16 and (k.startswith(key[:48]) or key.startswith(k[:48])):
                return lvl
    return None


def _normalize_levels(blocks: list[dict], entries: list[dict]) -> list[dict]:
    """TOC is ground truth: demote font-promoted subsections, apply numbering depth."""
    toc: dict[str, tuple[int, str]] = {}
    chapters = set()
    for e in entries:
        k = _key(e["title"])
        if k:
            toc.setdefault(k, (int(e.get("level") or 1), str(e.get("page") or "")))
        if int(e.get("level") or 1) == 1 and e.get("page"):
            chapters.add(str(e["page"]))
    multi = len(chapters) >= 4
    for b in blocks:
        if b.get("type") not in ("h1", "h2", "h3"):
            continue
        t = (b.get("text") or "").strip()
        if NUM3.match(t):
            b["type"] = "h3"
            continue
        if NUM2.match(t):
            b["type"] = "h2"
            continue
        lvl = _lookup(toc, _key(t), str(b.get("page") or ""))
        if lvl == 3:
            b["type"] = "h3"
        elif lvl == 2:
            b["type"] = "h2"
        elif lvl == 1:
            if b["type"] != "h1" and not BACK_MATTER.match(t):
                b["type"] = "h1"
        elif b["type"] == "h1" and multi and not FRONT_MATTER.match(t):
            b["type"] = "h2"          # a large-font subsection, not a chapter
    return blocks


def translate_titles(client, titles: list[str]) -> dict:
    prompt = ("把下列英文章节标题译为中文（学术书面语，保留年份与编号；不加书名号）。"
              "每行一条：英文<TAB>中文，不要解释。\n\n" + "\n".join(titles))
    out = {}
    for line in client.chat("你是学术译者。", prompt).splitlines():
        if "\t" in line:
            en, zh = [x.strip() for x in line.split("\t", 1)]
            if zh:
                out[en] = zh
    return out


def apply(blocks: list[dict], entries: list[dict], translate) -> list[dict]:
    """Insert h1 (by printed page) and h2 (by text prefix); strip glued heading text."""
    if not entries:
        return blocks
    try:
        zh = translate([e["title"] for e in entries])
    except Exception:
        zh = {}

    # chapters: place at the first block of the printed page given by the TOC
    for e in [x for x in entries if x["level"] == 1 and x["page"]]:
        page = str(e["page"])
        same = [b for b in blocks if _near(str(b["page"]), page)
                and b["type"] in ("h1", "h2")]
        if any(b["type"] == "h1" and _matching(b["text"], e["title"]) for b in same):
            continue
        # the chapter title is already present as a section heading: raise it
        # ("References" is often parsed as a chapter-level TOC line)
        raised = next((b for b in same if b["type"] == "h2"
                       and _matching(b["text"], e["title"])), None)
        if raised is not None:
            if not BACK_MATTER.match(raised["text"].strip()):
                raised["type"] = "h1"
            continue
        idx = next((i for i, b in enumerate(blocks) if str(b["page"]) == page), None)
        if idx is None:
            continue
        blocks.insert(idx, {"page": page, "type": "h1",
                            "text": e["title"], "zh": zh.get(e["title"], e["title"])})

    # chapter-level lines without a folio (parts, damaged OCR): promote the
    # matching heading already found in the body instead of inserting blindly
    for e in [x for x in entries if x["level"] == 1 and not x["page"]]:
        cand = next((b for b in blocks if b["type"] in ("h1", "h2")
                     and _matching(b["text"], e["title"])), None)
        if cand is not None and not BACK_MATTER.match(cand["text"].strip()):
            cand["type"] = "h1"

    # sections / subsections: locate by the first words of the title
    for e in [x for x in entries if x["level"] in (2, 3)]:
        want = "h3" if e["level"] == 3 else "h2"
        epage = str(e.get("page") or "")
        hit = next((b for b in blocks if b["type"] in ("h1", "h2", "h3")
                    and _matching(b["text"], e["title"])
                    and (not epage or _near(str(b["page"]), epage))), None)
        if hit is not None:
            hit["type"] = want          # already in the body: fix its level only
            if not hit.get("zh"):
                hit["zh"] = zh.get(e["title"], e["title"])
            continue
        idx = None
        body_text = ""
        for nw in (5, 3):
            key = _sq("".join(e["title"].split()[:nw]))
            idx = next((i for i, b in enumerate(blocks)
                        if b["type"] in ("p", "note")
                        and _sq(LEAD_NUM.sub("", b["text"])).startswith(key)), None)
            if idx is not None:
                body_text = LEAD_NUM.sub("", blocks[idx]["text"].strip())
                break
        if idx is None:
            continue
        title = zh.get(e["title"], e["title"])
        b = blocks[idx]
        rest = " ".join(body_text.split()[len(e["title"].split()):])
        if rest:
            b["text"], b["type"] = rest, "p"
            if b.get("zh", "").startswith(title):
                b["zh"] = b["zh"][len(title):].lstrip("　 ：:，。")
            blocks.insert(idx, {"page": b["page"], "type": want, "text": e["title"], "zh": title})
        else:
            b["type"], b["zh"] = want, title

    _normalize_levels(blocks, entries)
    for n, b in enumerate(blocks):
        b["id"] = "b%04d" % n
    return blocks
