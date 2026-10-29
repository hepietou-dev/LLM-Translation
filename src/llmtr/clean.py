"""Generic block cleaning: front matter, OCR debris, heading validation, duplicate headings."""
from __future__ import annotations

import re

CJK = re.compile(r"[\u4e00-\u9fff]")
CIRC = re.compile(r"[①-⑳]")
SPACED = re.compile(r"\s+([，。；：、！？])")
BANG = re.compile(r"[？！]{2,}")


def _num(page) -> int | None:
    s = str(page)
    return int(s) if s.isdigit() and 1 <= int(s) <= 5000 else None


FRONT_MARK = re.compile(r"(ISBN|DOI|www\.|All rights reserved|Printed in|\u00a9)", re.I)
TOC_DOT = re.compile(r"(\.{4,}|…{3,})\s*(\d{1,4}|[ivxlcdm]{1,7})\s*$", re.I)
LIST_HEAD = re.compile(r"(?i)^(contents|table of contents|list of (figures|maps|tables|"
                       r"illustrations|plates)|figures|maps|tables|illustrations)$")


def front_matter(blocks: list[dict]) -> list[dict]:
    """Drop cover/copyright/contents by text density; keep roman pages that read as prose."""
    pages = list(dict.fromkeys(b["page"] for b in blocks))

    def long_count(lbl):
        return sum(1 for b in blocks if b["page"] == lbl and len(b["text"]) >= 250)

    def legal(lbl):
        return any(FRONT_MARK.search(b["text"]) for b in blocks if b["page"] == lbl)

    body_i = next((i for i, lbl in enumerate(pages)
                   if long_count(lbl) >= 1 and not legal(lbl)), None)
    titles = {str(b["page"]) for b in blocks if b["type"] == "h1"}
    first_title = min((pages.index(t) for t in titles if t in pages), default=None)
    if first_title is not None and (body_i is None or first_title < body_i):
        body_i = first_title
    if body_i is None:
        return blocks
    keep = {lbl for lbl in pages[:body_i]
            if re.fullmatch(r"[ivxlcdm]{1,7}", str(lbl)) and long_count(lbl) >= 3}
    first = pages[body_i]
    start = next(i for i, b in enumerate(blocks) if b["page"] == first)
    head = [b for b in blocks[:start] if b["page"] in keep
            or b["type"] in ("h1", "h2", "figure", "table", "note")]
    return head + blocks[start:]


def _junk(text: str) -> bool:
    """True for printer slugs / barcode lines: long, almost no real words."""
    if len(text) < 25:
        return False
    words = re.findall(r"[A-Za-z\u4e00-\u9fff]{2,}", text)
    return len(words) <= 1


DUP_PAREN = re.compile(r"([\u4e00-\u9fff]{2,12})（\1）")


STAR = re.compile(r"\*(?=[\u4e00-\u9fffA-Za-z])([^*\n]{1,80}?)\*")


def _dedupe_paragraphs(blocks):
    """The same paragraph can arrive twice from repeated front-matter pages."""
    seen = set()
    out = []
    for b in blocks:
        key = (b.get("zh") or "").strip()
        if b["type"] == "p" and len(key) > 40 and key in seen:
            continue
        if b["type"] == "p" and len(key) > 40:
            seen.add(key)
        out.append(b)
    return out


def _dup_paren(blocks):
    for b in blocks:
        if b.get("zh"):
            b["zh"] = DUP_PAREN.sub(r"\1", b["zh"])
            b["zh"] = STAR.sub(r"\1", b["zh"])
    return blocks


def debris(blocks: list[dict]) -> list[dict]:
    """Drop empty translations, OCR fragments and printer slugs."""
    out = []
    for b in blocks:
        if TOC_DOT.search(b["text"]):        # printed contents line, not content
            continue
        if b["type"] in ("p", "note") and _junk(b["text"]):
            continue
        if b["type"] in ("p", "note") and not b.get("zh"):
            b["zh"] = b["text"]        # never lose content: keep the original until reviewed
            out.append(b)
            continue
        if b["type"] == "p" and len(b["text"]) < 25:
            continue
        out.append(b)
    return out


def denoise(blocks: list[dict]) -> list[dict]:
    for b in blocks:
        if b["type"] in ("p", "note"):
            z = CIRC.sub("", b.get("zh", ""))
            z = BANG.sub("", z)
            z = SPACED.sub(r"\1", z)
            b["zh"] = re.sub(r"[ \t]{2,}", " ", z)
            lab = str(b.get("page") or "")
            if lab and re.search(r"\s%s\s*$" % re.escape(lab), (b.get("text") or "")):
                # a running page number OCR'd onto the last line of the paragraph
                b["text"] = re.sub(r"\s%s\s*$" % re.escape(lab), "", b["text"])
                b["zh"] = re.sub(r"\s*%s\s*$" % re.escape(lab), "", b["zh"])
    return blocks


ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def _ord(page) -> int | None:
    s = str(page or "")
    if s.isdigit():
        return int(s)
    if re.fullmatch(r"[ivxlcdm]{1,7}", s):
        total, prev = 0, 0
        for ch in reversed(s):
            v = ROMAN[ch]
            total += -v if v < prev else v
            prev = max(prev, v)
        return total
    return None


def headings(blocks: list[dict]) -> list[dict]:
    """Keep only headings that look real: CJK text, not a chapter-title echo."""
    has_arabic_h1 = any(b["type"] == "h1" and str(b.get("page", "")).isdigit() for b in blocks)
    out, chapter = [], ""
    seen_h1: dict[str, int | None] = {}
    for b in blocks:
        if b["type"] in ("h1", "h2") and LIST_HEAD.match((b.get("text") or "").strip()):
            continue                       # the printed contents heading itself
        if (b["type"] == "h1" and has_arabic_h1
                and re.fullmatch(r"[ivxlcdm]{1,7}", str(b.get("page", "")))):
            b["type"] = "h2"        # front-matter headings are not chapters
        if b["type"] == "h1":
            z = re.sub(r"\s*\d{1,3}$", "", b.get("zh", "")).strip()
            if not CJK.search(z):
                continue
            key = re.sub(r"\s+", "", z)
            o = _ord(b.get("page"))
            if key in seen_h1:
                prev_o = seen_h1[key]
                # the same title a few pages apart is a running head, not a chapter
                if prev_o is None or o is None or abs(o - prev_o) <= 40:
                    continue
            seen_h1[key] = o
            b["zh"] = z
            chapter = re.sub(r"[^A-Za-z0-9]", "", b.get("text", "")).upper()
            out.append(b)
            continue
        if b["type"] == "h2" and b.get("zh", "").strip() != "注释":
            if re.fullmatch(r"[IVXLCDM]{1,6}|\d{1,3}", b.get("text", "").strip()):
                continue                       # a stray section numeral, not a title
            z = re.sub(r"\s*\d{1,3}$", "", b["zh"]).strip()
            core = re.sub(r"[^A-Za-z0-9]", "", b.get("text", "")).upper()
            if not CJK.search(z) or len(z) < 2:
                continue
            if chapter and core and (core in chapter or chapter in core):
                continue
            if out and out[-1]["type"] == "h1" and not z[:1].isdigit():
                continue
            b["zh"] = z
            if (out and out[-1]["type"] == "h2" and not out[-1]["zh"].endswith((".", ";"))
                    and len(out[-1]["text"]) + len(b["text"]) <= 90):
                out[-1]["text"] += " " + b["text"]
                out[-1]["zh"] += " " + z
                continue
        out.append(b)
    return out


def strip_dup_headings(blocks: list[dict]) -> list[dict]:
    """Remove the heading text the translator glued at the start of the next paragraph."""
    def core(s):
        return "".join(CJK.findall(s))

    for i, b in enumerate(blocks[:-1]):
        if b["type"] != "h2":
            continue
        nxt = blocks[i + 1]
        if nxt["type"] != "p" or not nxt.get("zh"):
            continue
        m = re.match(r"^(\d\.\s*[^ 　]{2,24})[ 　]", nxt["zh"])
        if not m:
            continue
        head, prefix = core(b["zh"]), core(m.group(1))
        lcp = 0
        for x, y in zip(prefix, head):
            if x != y:
                break
            lcp += 1
        if lcp >= 4 or (len(m.group(1)) <= 14):
            nxt["zh"] = nxt["zh"][m.end():].lstrip("　 ")
    return blocks


def renumber(blocks: list[dict]) -> list[dict]:
    for n, b in enumerate(blocks):
        b["id"] = "b%04d" % n
    return blocks


BIB_SPLIT = re.compile(
    r"(?<=[.!?])\s+(?=[A-Z][\w\u2019'\-]+,\s+[A-Z][A-Za-z.\- ]{1,40},\s+eds?\.)"
    r"|(?<=[.!?])\s+(?=[A-Z][\w\u2019'\-]+,\s+[A-Z][A-Za-z.\u2019'\- ]{1,40}[.,]\s+"
    r"(?:Reprinted\s+\d{4}\.\s+)?\d{4})"
    r"|(?<=\d)\s+(?=[A-Z][\w\u2019'\-]+,\s+[A-Z][A-Za-z.\u2019'\- ]{1,40}[.,]\s+"
    r"(?:Reprinted\s+\d{4}\.\s+)?\d{4})")


def _split_bib_entries(blocks: list[dict]) -> list[dict]:
    """Separate an edited volume / reprint entry glued onto the previous one."""
    out = []
    for b in blocks:
        if b.get("type") != "bib":
            out.append(b)
            continue
        tp = BIB_SPLIT.split(b.get("text") or "")
        zp = BIB_SPLIT.split(b.get("zh") or "")
        if len(tp) < 2:
            out.append(b)
            continue
        for i, part in enumerate(tp):
            nb = dict(b)
            nb["text"] = part.strip()
            nb["zh"] = zp[i].strip() if len(zp) == len(tp) else nb["text"]
            out.append(nb)
    return out


def run(blocks: list[dict]) -> list[dict]:
    blocks = _dedupe_paragraphs(blocks)
    blocks = _dup_paren(blocks)
    blocks = front_matter(blocks)
    blocks = debris(blocks)
    blocks = _split_bib_entries(blocks)
    blocks = denoise(blocks)
    blocks = headings(blocks)
    blocks = strip_dup_headings(blocks)
    return renumber(blocks)


INITIAL = re.compile(r"(?<=\u00b7)([A-Z])\.\s*")
FULL_GLOSS = re.compile(
    r"^\s*([\u4e00-\u9fff][\u4e00-\u9fff\u00b7A-Za-z. ]{1,24}?)"
    r"（([A-Z][A-Za-z\u2019' .-]{2,48})）")


def align_bylines(blocks: list[dict]) -> list[dict]:
    """Chapter bylines must use the same Chinese name as the contributor bios."""
    full: dict[str, str] = {}
    for b in blocks:
        if b.get("type") != "p":
            continue
        m = FULL_GLOSS.match(b.get("zh") or "")
        if m:
            full[m.group(2).strip()] = m.group(1).strip()
    if not full:
        return blocks
    for b in blocks:
        if b.get("type") != "byline":
            continue
        for en, zh in full.items():
            if re.search(r"(?<![A-Za-z])%s(?![A-Za-z])" % re.escape(en), b.get("text") or ""):
                b["zh"] = "%s（%s）" % (zh, en)
                break
    return blocks


def _drop_invented_initials(blocks: list[dict]) -> list[dict]:
    """Remove a middle initial the translation invented (not in the source)."""
    for b in blocks:
        if b.get("type") != "p":
            continue
        src, zh = b.get("text", ""), b.get("zh", "")
        if not src or not zh:
            continue

        def repl(m):
            if re.search(r"(?<![A-Za-z])%s(?![A-Za-z])" % m.group(1), src):
                return m.group(0)
            return ""

        b["zh"] = INITIAL.sub(repl, zh)
    return blocks


def finalize(blocks: list[dict], pairs: dict | None = None) -> list[dict]:
    """Drop dangling/duplicate headings, then (re)build the name index."""
    blocks = _drop_invented_initials(blocks)
    blocks = align_bylines(blocks)
    out = []
    for i, b in enumerate(blocks):
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        if b["type"] == "h1":
            if nxt is None:
                continue
            if out and out[-1]["type"] == "h1" and out[-1]["zh"] == b["zh"]:
                continue
        out.append(b)
    blocks = out

    # a reference list needs its own heading
    if any(b["type"] == "bib" and "　" not in b.get("text", "") for b in blocks):
        has_head = any(
            b["type"] in ("h1", "h2") and re.search(
                r"(参考文献|参考书目|BIBLIO|REFERENCES|WORKS CITED)",
                b.get("zh", "") + b.get("text", ""), re.I)
            for b in blocks)
        if not has_head:
            first = next(i for i, b in enumerate(blocks)
                         if b["type"] == "bib" and "　" not in b.get("text", ""))
            blocks[first:first] = [{"page": blocks[first]["page"], "type": "h1",
                                    "text": "References", "zh": "参考文献"}]

    if pairs:
        import collections
        pages = collections.defaultdict(set)
        for b in blocks:
            for en in pairs:
                if re.search(r"(?<![A-Za-z])%s(?![A-Za-z])" % re.escape(en), b.get("text", "")):
                    pages[en].add(str(b["page"]))
        rows = []
        for en, zh in pairs.items():
            ps = sorted({int(p) for p in pages.get(en, set()) if p.isdigit()})
            if ps:
                rows.append("%s\u3000%s\u3000%s" % (en, zh, ", ".join(str(p) for p in ps)))
        rows.sort(key=lambda s: s.lower())
        if rows and not any(b["type"] == "h1" and b["zh"] == "人名索引" for b in blocks):
            pos = len(blocks)
            blocks[pos:pos] = [{"page": "", "type": "h1", "text": "NAME INDEX", "zh": "人名索引"}] + \
                              [{"page": "", "type": "bib", "text": r, "zh": r} for r in rows]
    for n, b in enumerate(blocks):
        b["id"] = "b%04d" % n
    return blocks
