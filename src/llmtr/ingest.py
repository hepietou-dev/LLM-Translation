"""PDF ingest: probe -> page text -> printed page map -> block IR."""
from __future__ import annotations

import logging
import re
import warnings
from dataclasses import dataclass, asdict
from pathlib import Path

warnings.filterwarnings("ignore")
logging.getLogger("pypdf").setLevel(logging.ERROR)

from pypdf import PdfReader  # noqa: E402

ROMAN = re.compile(r"^[ivxlcdm]{1,7}$", re.I)
NUM_END = re.compile(r"(?:^|\s)(\d{1,4})\s*$")
NUM_START = re.compile(r"^\s*(\d{1,4})(?:\s|$)")
NOTE = re.compile(r"^(\d{1,3})[.:]\s")
H2NUM = re.compile(r"^(\d{1,2})(?:\.\d+)?\.?\s+(.*)$")
CHAPTER = re.compile(r"^chapter\b", re.I)
BACK = re.compile(r"^(bibliography|references|index)\b", re.I)
CAPTION = re.compile(r"(?:^|\s)(?:fig(?:ure)?\.?|map|plate|chart|table)\s+\d+(?:\.\d+)*(?!\d|\.\d)\s*[.:]", re.I)
LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
             "\ufb04": "ffl", "\ufb05": "ft", "\ufb06": "st"}

MIN_PAGE_CHARS = 200


def _prose(s: str) -> bool:
    toks = [t for t in s.split() if re.search(r"[A-Za-z]", t)]
    if not toks:
        return False
    good = sum(1 for t in toks if len(t) >= 3 and re.search(r"[aeiouAEIOU]", t))
    return good / len(toks) >= 0.55


def _caps(s: str) -> bool:
    letters = [c for c in s if c.isalpha()]
    return len(letters) >= 4 and all(c.isupper() for c in letters)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


SKIP_HEAD = re.compile(r"(?i)^(keywords?|abstract|journal|vol\b|issn|doi|copyright|received|accepted|published|word count|page count)", )

PIPE_LABEL = re.compile(r"\|\s*(\d{1,4})\s*$")
PAGE_LABEL = re.compile(r"(?i)\bpage\s*(?:no\.?\s*)?[:.]?\s*(\d{1,4})")
BOILER = re.compile(r"(downloaded from|about\.jstor\.org|all use subject|^\d{1,3}(\.\d{1,3}){3}\b|\butc\b)", re.I)

CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
WORD_TOK = re.compile(r"[A-Za-z][A-Za-z'\u2019\-]*")
GLUE_SUFFIX = ("of", "the", "and", "to", "in")
NAME_PREFIX = ("mc", "mac", "o'", "d'")


def _glued_token(tok: str) -> bool:
    """A token that merged two English words because OCR lost the space."""
    if len(tok) < 10:
        return False
    if re.search(r"[a-z]{3,}[A-Z]", tok) and not tok.lower().startswith(NAME_PREFIX):
        return True                     # "coercedJordan…", not "MacArthur"
    low = tok.lower()
    if len(low) >= 13:
        for w in GLUE_SUFFIX:           # "thebeginningof", "publishedoneof"
            if low.endswith(w) and len(low) - len(w) >= 8:
                return True
    return False


def glue_counts(text: str) -> tuple[int, int]:
    toks = WORD_TOK.findall(text or "")
    return sum(1 for t in toks if _glued_token(t)), len(toks)


def suspect_pages(pages: list[str], *, min_chars: int = 80,
                  exclude=None, max_pages: int = 0) -> list[int]:
    """Pages whose OCR contains word-merge artefacts worth an AI review."""
    skip = set(exclude or ())
    out = []
    for i, text in enumerate(pages or []):
        if i in skip or len(re.sub(r"\s", "", text or "")) < min_chars:
            continue
        glued, _toks = glue_counts(text)
        if glued:
            out.append(i)
    return out[:max_pages] if max_pages else out

TOC_DOT = re.compile(r"(\.{4,}|…{3,})\s*(\d{1,4}|[ivxlcdm]{1,7})\s*$", re.I)
LIST_HEAD = re.compile(
    r"(?i)^(contents|table of contents|list of (figures|maps|tables|illustrations|plates)|"
    r"figures|maps|tables|illustrations|plates|目\s*录|目\s*錄|目\s*次|总目录|總目錄)$")

# an author surname followed by initials, a first name, or a second author.
# The year lookahead keeps prose ("Figgis, Barker and Oakeshott were …") out.
_CAP = r"[A-Z\u00C0-\u00D6\u00D8-\u00DE]"
_LET = r"[^\W\d_]"
_WORD = _CAP + _LET + r"+(?:[-'\u2019]" + _LET + r"+)*"
_PART = r"(?:von|van|de|der|den|del|di|da|le|la|du|dos|ter|ten)"
_SUR = (r"(?:" + _PART + r"\s+)?" + _WORD
        + r"(?:\s+(?:" + _PART + r"|" + _WORD + r"))*")
_NAME = (_WORD + r"(?:\s+(?:[A-Z]\.|" + _PART + r"|" + _WORD + r"))*")
_REF_TAIL = (r",\s+(?:[A-Z]\.(?:\s*[A-Z]\.)*|"
             + _NAME + r"(?:[.,](?!\s+(?:and|&)\b)|(?<=\.))|"
             + _WORD + r"\s+(?:and|&)\s+" + _CAP + r")")
_REF_YEAR = r"(?=.{0,90}?(?:\d{4}(?:[a-c]|[-–—]\d{2,4})?[.,]|\bn\.d\.))"
REF_ENTRY = re.compile(r"^" + _SUR + _REF_TAIL + _REF_YEAR)


HYPHEN_KEEP = {
    "well", "self", "non", "pre", "post", "anti", "multi", "semi", "sub", "super",
    "inter", "intra", "over", "under", "cross", "half", "all", "ever", "low",
    "high", "long", "short", "mid", "vice", "ex", "great", "so", "ill", "far",
    "near", "new", "old", "first", "second", "third", "one", "two", "three",
    "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
    "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
    "eighty", "ninety", "hundred", "thousand", "socio", "politico", "anglo",
    "franco", "marxist", "leninist", "weberian", "kantian", "hegelian",
}


def sanitize(t: str) -> str:
    t = re.sub(r"/uni([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), t)
    t = CTRL.sub(" ", t)
    t = t.translate(str.maketrans(LIGATURES))
    return t.replace("\ufffd", "").replace("\u00a0", " ")


def read_pages(pdf: Path) -> list[str]:
    reader = PdfReader(str(pdf))
    return [sanitize(p.extract_text(extraction_mode="layout") or "") for p in reader.pages]


def page_index(label, labels=None, page_map=None):
    """0-based PDF page index for a printed page label.

    A spread scan puts two book pages on one PDF page, so the label list is
    longer than the document and ``labels.index`` would point at the wrong
    sheet; ``page_map`` records the true source page for every label.
    """
    if page_map:
        v = page_map.get(str(label))
        if v is not None:
            return int(v)
    try:
        return labels.index(str(label))
    except (AttributeError, ValueError):
        return None


def labels_for_src(labels, page_map, idx):
    """Printed labels printed on PDF page ``idx`` (two of them for a spread)."""
    if page_map:
        out = [str(l) for l, s in page_map.items() if int(s) == idx]
        if out:
            return out
    return [str(labels[idx])] if idx < len(labels) else []


def _ref_groups(lines: list[dict], vocab: set[str]) -> list[tuple]:
    """Rebuild hanging-indent reference entries from page-level lines.

    PyMuPDF text blocks split and reorder reference lists, so entry boundaries
    come from the flush-left position and continuity from the indent instead.
    """
    lines = sorted(lines, key=lambda l: (round(l["bbox"][1], 1), l["bbox"][0]))
    flush = min((l["bbox"][0] for l in lines if REF_ENTRY.match(l["text"])), default=None)
    out: list[dict] = []
    for ln in lines:
        t, sz, bb = ln["text"], ln["sz"], ln["bbox"]
        if flush is None or bb[0] <= flush + 2:
            out.append({"text": [t], "sz": sz, "bbox": bb})
        elif out:
            g = out[-1]
            g["text"].append(t)
            g["bbox"] = (min(g["bbox"][0], bb[0]), g["bbox"][1],
                         max(g["bbox"][2], bb[2]), bb[3])
        else:
            out.append({"text": [t], "sz": sz, "bbox": bb})
    info = []
    for g in out:
        txt = sanitize(_join([_norm(x) for x in g["text"]], vocab)).strip()
        if not txt:
            continue
        x0, y0, x1, y1 = g["bbox"]
        info.append((y0, x0, txt, g["sz"], y1 - y0))
    return info


NOTE_MARK_OPEN = "\u27e6"
NOTE_MARK_CLOSE = "\u27e7"


def _line_text(line: dict) -> str:
    """Line text with superscript note numbers kept as ⟦n⟧ markers.

    Without the marker the digit glues onto the preceding word ("none.1"), the
    translator has no way to know it is a note call, and the printed paragraph
    ends up with a stray number in the middle of a sentence.
    """
    out = []
    for sp in line["spans"]:
        t = sp["text"]
        core = t.strip()
        if core and (sp.get("flags", 0) & 1) and re.fullmatch(r"\d{1,3}", core):
            out.append(NOTE_MARK_OPEN + core + NOTE_MARK_CLOSE)
        else:
            out.append(t)
    return "".join(out).strip()


H3_RUN = re.compile(r"^(\d{1,2}\.\d{1,2}\.\d{1,2})\s+(.+)$")
H3_STOP = frozenset(
    "of and the in on for to a an as at by with from or into over under between "
    "after before against within without upon toward towards".split())


def split_runin_h3(t: str) -> tuple[str, str] | None:
    """Split a run-in subsection heading glued to its paragraph.

    '6.3.1 The Conjunctural On the conjunctural ...' -> ('6.3.1 The Conjunctural',
    'On the conjunctural ...'). A capitalised word only belongs to the heading
    when the next word is also capitalised, or is a small word followed by a
    capitalised word; this is what separates 'Science and Tradition Finally'
    from heading+body.
    """
    m = H3_RUN.match(t)
    if not m:
        return None
    words = m.group(2).split()
    if len(words) < 4:
        return None
    head: list[str] = []
    for i, w in enumerate(words):
        if i + 2 >= len(words):
            break
        nxt, aft = words[i + 1], words[i + 2]
        if not (w[:1].isupper() or w.lower() in H3_STOP):
            break
        if not (nxt[:1].isupper()
                or (nxt.lower() in H3_STOP and aft[:1].isupper())):
            break
        head.append(w)
    if not 1 <= len(head) <= 8 or len(head) >= len(words):
        return None
    title = m.group(1) + " " + " ".join(head)
    if len(title) > 70:
        return None
    body = " ".join(words[len(head):])
    if not body[:1].isupper():
        return None
    return title, body


def _bold_break(group: dict, line: dict, bb, left: float) -> bool:
    """A short bold line at body size is a section head, not the paragraph's first line."""
    if group.get("bold") == line.get("bold"):
        return False
    text = (line["text"] if line.get("bold") else " ".join(group["text"])).strip()
    if not text or len(text) > 80 or len(text.split()) > 10:
        return False
    return not re.search(r"[.!?;:,\u3002\uff1b\uff0c]\s*$", text)


def _head_key(t: str) -> str:
    """Normalised running-head text: folio and punctuation removed for comparison."""
    s = re.sub(r"\s+", " ", (t or "")).strip().lower()
    s = re.sub(r"^(?:\d{1,4}|[ivxlcdm]{1,7})\b\s*", "", s)
    s = re.sub(r"\b(?:\d{1,4}|[ivxlcdm]{1,7})$", "", s)
    s = re.sub(r"[^a-z\u4e00-\u9fff ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _is_top_head(t: str, label: str, repeat_keys: set[str]) -> bool:
    """Is a short line at the top of a page a running head / folio?

    The old rule dropped every short line above 60pt. A tightly cropped scan can
    put the continuation of a paragraph there, which silently cut a sentence in
    half at the page turn. Only lines that repeat, match the printed folio, are
    upper-case, or name a standard front-matter section are dropped now.
    """
    core = (t or "").strip()
    if not core:
        return True
    if _head_key(core) in repeat_keys:
        return True
    if label and re.search(r"(?<!\d)%s(?!\d)" % re.escape(str(label)), core):
        return True
    if re.match(r"(?i)^(contents|table of contents|list of (figures|maps|tables|"
                r"illustrations|plates))\b", core):
        return True
    letters = [c for c in core if c.isalpha()]
    if letters:
        upper = sum(1 for c in letters if c.isupper()) / len(letters)
        if upper > 0.6 and not core.endswith("."):
            return True
    return False


def page_paragraphs(pdf: Path, labels: list[str] | None = None):
    """Born-digital PDFs: real blocks plus font-size heading levels. None when unusable."""
    try:
        import pymupdf
    except ImportError:
        return None
    doc = pymupdf.open(str(pdf))
    # scan detection: most pages are a full-page raster image -> OCR font sizes are
    # unreliable, so font-size heading promotion must be disabled.
    scan_hits = 0
    sample = min(12, len(doc))
    for _page in doc[:sample]:
        _W, _H = _page.rect.width, _page.rect.height
        for _img in _page.get_images(full=True):
            for _r in _page.get_image_rects(_img[0]):
                if _r.width * _r.height > 0.9 * _W * _H:
                    scan_hits += 1
                    break
    is_scan = scan_hits >= max(2, int(sample * 0.5))
    sizes, pages, bw, fonts = [], [], [], {}
    vocab: set[str] = set()
    top_lines: list[list[str]] = []
    for page in doc:
        info = []
        raw_lines: list[dict] = []
        for b in page.get_text("dict")["blocks"]:
            if b.get("type") != 0:
                continue
            block_txt = " ".join(s["text"] for l in b["lines"] for s in l["spans"]).strip()
            if not block_txt:
                continue
            bw.append(len(block_txt.split()))
            for l in b["lines"]:
                for sp in l["spans"]:
                    fonts[sp["font"]] = fonts.get(sp["font"], 0) + len(sp["text"])
            # a chapter opener puts title and byline in one block but at different
            # sizes: group lines by size so each becomes its own paragraph
            lines = []
            for line in b["lines"]:
                text = _line_text(line)
                if not text:
                    continue
                by_size = {}
                bold_chars = total_chars = 0
                for sp in line["spans"]:
                    if sp["text"].strip():
                        by_size[round(sp["size"])] = by_size.get(round(sp["size"]), 0) + len(sp["text"])
                    n = len(sp["text"].strip())
                    total_chars += n
                    if sp["flags"] & 16 or "bold" in sp["font"].lower():
                        bold_chars += n
                lines.append({"text": text,
                              "sz": max(by_size, key=by_size.get) if by_size else 0,
                              "bbox": tuple(line["bbox"]),
                              # Word exports can carry bogus ascent metrics, so order by
                              # baseline: bbox tops of adjacent lines then overlap
                              "y": line["spans"][0]["origin"][1] if line["spans"] else line["bbox"][1],
                              "bold": bool(bold_chars * 2 > total_chars)})
                vocab.update(w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'\u2019\-]*", text))
            raw_lines.extend(lines)
            left = min(x["bbox"][0] for x in lines) if lines else 0
            # reference/bibliography entries use a hanging indent: each entry starts
            # at the left edge, its continuation lines are indented
            hanging = bool(lines) and lines[0]["bbox"][0] <= left + 2 \
                and any(x["bbox"][0] > left + 2 for x in lines[1:])
            groups = []
            for n, ln in enumerate(lines):
                bb = ln["bbox"]
                starts_ref = n > 0 and bb[0] <= left + 2 and bool(REF_ENTRY.match(ln["text"]))
                if (hanging and n and bb[0] <= left + 2) or starts_ref:
                    groups.append({"text": [ln["text"]], "sz": ln["sz"], "bbox": bb,
                                   "y": ln["y"], "bold": ln["bold"]})
                elif groups and abs(groups[-1]["sz"] - ln["sz"]) <= 0.6 \
                        and not _bold_break(groups[-1], ln, bb, left):
                    groups[-1]["text"].append(ln["text"])
                    groups[-1]["bbox"] = (min(groups[-1]["bbox"][0], bb[0]), groups[-1]["bbox"][1],
                                          max(groups[-1]["bbox"][2], bb[2]), bb[3])
                else:
                    groups.append({"text": [ln["text"]], "sz": ln["sz"], "bbox": bb,
                                   "y": ln["y"], "bold": ln["bold"]})
            for g in groups:
                txt = sanitize(_join([_norm(x) for x in g["text"]], vocab)).strip()
                if not txt:
                    continue
                x0, y0, x1, y1 = g["bbox"]
                info.append((g["y"], x0, txt, g["sz"], y1 - y0))
                sizes.extend([g["sz"]] * len(txt))
        top_lines.append([l["text"] for l in raw_lines
                          if l["bbox"][1] < 60 and len(l["text"]) < 60])
        entry_lines = [l for l in raw_lines
                       if not (l["bbox"][1] < 60 and len(l["text"]) < 60)
                       and REF_ENTRY.match(l["text"])]
        if len(entry_lines) >= 4:
            xs = [l["bbox"][0] for l in entry_lines]
            flush = min(xs)
            top = min(l["bbox"][1] for l in entry_lines)
            for l in raw_lines:
                if REF_HEAD.match(l["text"].strip()):
                    top = min(top, l["bbox"][1])
            flush_lines = [l for l in raw_lines
                           if l["bbox"][1] >= top
                           and not (l["bbox"][1] < 60 and len(l["text"]) < 60)
                           and l["bbox"][0] <= flush + 2]
            starts = [l for l in flush_lines if REF_ENTRY.match(l["text"])]
            # prose also has flush lines, but they continue sentences rather than
            # open "Surname, Initials. YYYY" entries
            if max(xs) - min(xs) <= 8 and len(starts) >= 4 \
                    and len(starts) >= 0.5 * len(flush_lines):
                info = [i for i in info if i[0] + i[4] <= top + 2] + _ref_groups(
                    [l for l in raw_lines if l["bbox"][1] >= top], vocab)
        pages.append(info)
    if not sizes:
        return None
    if fonts:
        top = max(fonts, key=fonts.get).lower()
        if "glyphless" in top:        # Tesseract/IA OCR layer -> per-line blocks
            return None
    bw.sort()
    if bw[len(bw) // 2] < 15:
        return None
    sizes.sort()
    if sizes[int(len(sizes) * 0.75)] < 40 or len(sizes) < 1000:
        pass
    # body size = most common rounded size
    import collections
    body = collections.Counter(sizes).most_common(1)[0][0]
    body_px = body * 1.2
    # a line that recurs at the top of several pages is a running head; a lone
    # short line there may be the tail of a paragraph broken by the page turn
    counts: collections.Counter = collections.Counter()
    for texts in top_lines:
        counts.update({_head_key(t) for t in texts if _head_key(t)})
    need = max(3, int(len(top_lines) * 0.12))
    repeat_heads = {k for k, v in counts.items() if v >= need}
    out = []
    for page_i, info in enumerate(pages):
        if not info:
            out.append([])
            continue
        label = str(labels[page_i]) if labels and page_i < len(labels) else ""
        H = max(y for y, *_ in info) + 1
        paras = []
        sep_y = None
        try:
            pg = doc[len(out)]
            for d in pg.get_drawings():
                rect = d.get("rect")
                if rect and rect.width > pg.rect.width * 0.15 and rect.height < 4 \
                        and rect.y0 > pg.rect.height * 0.55:
                    sep_y = rect.y0
                    break
        except Exception:
            sep_y = None
        H_page = 0
        try:
            H_page = doc[len(out)].rect.height
        except Exception:
            H_page = 800
        prev_y = None
        results = []
        for y, x, txt, sz, b_h in sorted(info):
            if y < 60 and len(txt) < 60 and _is_top_head(txt, label, repeat_heads):
                continue                          # running head / folio
            ratio = sz / body if body else 1
            lvl = 1 if (not is_scan and ratio >= 1.35) else 2 if (not is_scan and ratio >= 1.12) else 0
            # smaller type occupying roughly one line = footnote / marginal note
            left_margin = min((i[1] for i in info), default=0)
            bottom = y > H_page * 0.62
            indented = x > left_margin + 7
            numbered = bool(re.match(r"^\d{1,3}\b", txt))
            if sep_y is not None and y >= sep_y - 2 and len(txt) > 8:
                lvl = -1
            elif bottom and len(txt) > 10 and (indented or numbered) \
                    and ratio <= 1.02 and (ratio <= 0.96 or numbered):
                lvl = -1
            if ratio < 0.9 and b_h < body_px * 4 and len(txt) > 12:
                lvl = -1
            elif y > H_page * 0.70 and ratio <= 1.02 and b_h < body_px * 6 \
                    and re.match(r"^\d{1,3}\b", txt):
                lvl = -1      # bottom-of-page block starting with a reference number
            gap = y - prev_y if prev_y is not None else 0
            prev_y = y + b_h
            words = txt.split()
            if lvl == 0 and 1 <= len(words) <= 3 and len(txt) <= 60 \
                    and not txt.rstrip().endswith((".", "。", ",", "，", ";", ":")) \
                    and _caps(txt):
                lvl = 2
            if lvl > 0 and (len(txt) > 80 or txt.rstrip().endswith(("。", ".", "，", ","))):
                lvl = 0
            results.append([y, x, txt, lvl, bottom, indented, numbered, b_h, ratio])
        # page-tail footnote run: >=2 consecutive short indented blocks at the very bottom
        n = len(results)
        for i in range(n):
            if results[i][3] != 0 or not results[i][4] or results[i][1] <= left_margin + 10:
                continue
            if not results[i][6] and (results[i][7] >= body_px * 5 or results[i][8] > 0.96):
                continue
            j = i + 1
            run = 1
            while (j < n and results[j][3] == 0 and results[j][4]
                   and results[j][1] > left_margin + 10 and results[j][7] < body_px * 5
                   and (results[j][6] or results[j][8] <= 0.96)):
                run += 1
                j += 1
            if run >= 2:
                for k in range(i, j):
                    results[k][3] = -1
        paras = [(r[2], r[3]) for r in results]
        out.append(paras)
    return out


def probe(pages: list[str]) -> dict:
    chars = [len(re.sub(r"\s", "", t)) for t in pages]
    good = sum(1 for c in chars if c >= MIN_PAGE_CHARS)
    need = max(1, int(len(pages) * 0.6))
    if len(pages) >= 5:
        need = max(need, 3)
    return {
        "pages": len(pages),
        "good_pages": good,
        "ratio": round(good / max(1, len(pages)), 3),
        "median_chars": sorted(chars)[len(chars) // 2] if chars else 0,
        "has_text_layer": good >= need,
    }



_ROM = {"i":1,"v":5,"x":10,"l":50,"c":100,"d":500,"m":1000}


def _from_roman(t: str) -> int:
    total, prev = 0, 0
    for ch in reversed(t.lower()):
        v = _ROM[ch]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total


def _to_roman(n: int) -> str:
    vals = [(1000,"m"),(900,"cm"),(500,"d"),(400,"cd"),(100,"c"),(90,"xc"),
            (50,"l"),(40,"xl"),(10,"x"),(9,"ix"),(5,"v"),(4,"iv"),(1,"i")]
    out = ""
    for v, s2 in vals:
        while n >= v:
            out += s2
            n -= v
    return out


def _label_of(line: str) -> str | None:
    s = line.strip()
    if not s:
        return None
    if re.match(r"(?i)^part\b", s):     # "PART I" is a divider, not folio "i"
        return None
    if ROMAN.fullmatch(s):
        return s.lower()
    m = re.search(r"(?:^|\s)([ivxlcdm]{1,7})(?:\s|$)", s, re.I)
    if m and len(s) < 40:
        return m.group(1).lower()
    m = NUM_END.search(s)
    if m and not s.endswith("."):
        return m.group(1)
    m = NUM_START.match(s)
    if m:
        return m.group(1)
    return None


def page_labels(pages: list[str]) -> list[str]:
    raw: list[str | None] = []
    for t in pages:
        lines = [l for l in t.split("\n") if l.strip()]
        lab = _label_of(lines[0]) if lines else None
        if lab is None and lines and len(lines[-1].strip()) < 40:
            lab = _label_of(lines[-1])
        raw.append(lab)

    known = [(i, _from_roman(l)) for i, l in enumerate(raw)
             if l and re.fullmatch(r"[ivxlcdm]{1,7}", l)]
    for (i1, v1), (i2, v2) in zip(known, known[1:]):
        if i2 - i1 > 1 and v2 - v1 == i2 - i1:
            for k in range(i1 + 1, i2):
                raw[k] = _to_roman(v1 + (k - i1))
    if known:
        i0, v0 = known[0]
        for k in range(max(0, i0 - 2), i0):
            v = v0 - (i0 - k)
            if v > 0 and not raw[k]:
                raw[k] = _to_roman(v)

    deltas = []
    for i in range(len(raw) - 1):
        a, b = raw[i], raw[i + 1]
        if a and b and a.isdigit() and b.isdigit() and int(b) - int(a) == 1:
            deltas.append((i + 1) - int(a))
    delta = max(set(deltas), key=deltas.count) if deltas else None

    out = []
    for i, lab in enumerate(raw):
        ok = False
        if lab and lab.isdigit() and delta is not None:
            ok = (i + 1) - int(lab) == delta
        elif lab and ROMAN.fullmatch(lab):
            ok = True
        if ok:
            out.append(lab)
        elif delta is not None:
            n = (i + 1) - delta
            out.append(str(n) if n >= 1 else "")
        else:
            out.append("")
    return out


def _is_header(line: str, label: str) -> bool:
    s = line.strip()
    if not s or len(s) > 90:
        return False
    if label and re.search(r"(?<!\d)%s(?!\d)" % re.escape(label), s):
        return True
    if BACK.match(s) and len(s) <= 40:
        return False
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return False
    upper = sum(1 for c in letters if c.isupper()) / len(letters)
    return upper > 0.6 and not s.endswith(".")



def _fix_roman_outliers(labels):
    """A roman label inside an arabic run is a parsing artefact: interpolate it."""
    for i in range(1, len(labels) - 1):
        cur = str(labels[i])
        if not re.fullmatch(r"[ivxlcdm]{1,7}", cur):
            continue
        a, b = str(labels[i - 1]), str(labels[i + 1])
        if a.isdigit() and b.isdigit() and int(b) - int(a) == 2:
            labels[i] = str(int(a) + 1)
    return labels


def _fill_missing_labels(labels):
    """When page numbers are unreadable, number the pages sequentially so nothing is dropped."""
    empty = sum(1 for x in labels if not str(x).strip())
    if empty < max(3, len(labels) * 0.4):
        return labels
    return [str(i + 1) for i in range(len(labels))]


def bad_pages(pdf, pages, min_chars: int = 120, max_ratio: float = 0.35):
    """Pages whose own text is missing or garbled (image-only page or broken text layer)."""
    import pymupdf

    doc = pymupdf.open(str(pdf))
    bad = []
    for i, text in enumerate(pages):
        plain = re.sub(r"\s", "", text or "")
        alpha = sum(1 for c in plain if c.isalpha())
        ratio = alpha / max(1, len(plain))
        covered = 0.0
        if i < len(doc):
            page = doc[i]
            area = page.rect.width * page.rect.height
            for img in page.get_images(full=True):
                for r in page.get_image_rects(img[0]):
                    covered = max(covered, r.width * r.height / max(1.0, area))
        if (len(plain) < min_chars and covered > 0.5) or (len(plain) >= min_chars and ratio < 0.45):
            bad.append(i)
    if len(bad) > max(1, len(pages) * max_ratio):
        return None
    return bad


def text_quality(pages: list[str]) -> dict:
    """Judge whether the embedded text layer is good enough to translate as-is.

    A scan whose OCR glued words together still passes ``probe`` (plenty of
    characters), so spacing is measured per page: a native PDF scores 0, a good
    OCR layer stays near 0, a bad one glues words on most pages.
    """
    toks = WORD_TOK.findall(" ".join(pages or []))
    if len(toks) < 50:
        return {"ok": False, "median": 0, "glued": 0.0, "page_glue": 1.0,
                "anomaly": 0.0, "tokens": len(toks)}
    lens = sorted(len(t) for t in toks)
    median = lens[len(lens) // 2]
    glued = sum(1 for t in toks if len(t) >= 16) / len(toks)
    per_page = []
    for page in pages:
        tk = WORD_TOK.findall(page or "")
        if len(tk) >= 40:
            per_page.append(sum(1 for x in tk if len(x) >= 16) / len(tk))
    per_page.sort()
    page_glue = per_page[len(per_page) // 2] if per_page else 0.0
    flagged = counted = 0
    for page in pages or []:
        if len(re.sub(r"\s", "", page or "")) < 80:
            continue
        counted += 1
        if glue_counts(page)[0]:
            flagged += 1
    anomaly = flagged / max(1, counted)
    ok = median <= 9 and page_glue <= 0.010 and anomaly <= 0.25
    return {"ok": ok, "median": median, "glued": round(glued, 3),
            "page_glue": round(page_glue, 4), "anomaly": round(anomaly, 3),
            "tokens": len(toks)}


def blocks_from_layout(layout) -> list[dict]:
    """Turn structured page transcription into blocks.

    ``layout`` is ``[(page_label_or_None, [(type, text), ...]), ...]``.
    Blocks on a page whose printed number could not be read keep an empty page
    label; the caller decides whether to drop them (front matter) or fill them.
    """
    out: list[dict] = []
    for _i, (label, items) in enumerate(layout):
        page = str(label or "").strip().lower()
        for typ, text in items:
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) < 2 or re.fullmatch(r"[\[\]?\s.,;:'\-–—]+", text):
                continue
            b = {"page": page, "type": typ, "text": text}
            if typ == "note":
                m = re.match(r"^(\d{1,3})\s+(.*)$", text)
                if m:
                    b["text"] = "%s %s" % (m.group(1), m.group(2))
            out.append(b)
    return out


def _skip_when_stitching(b: dict) -> bool:
    """Figure labels / captions / notes may sit between the two halves of a sentence."""
    if b.get("type") == "note":
        return True
    return b.get("type") == "p" and (_is_figure_caption(b.get("text") or "")
                                     or _label_like(b.get("text") or ""))


def _merge_paragraphs(blocks, limit: int = 6000):
    """A paragraph broken by a page (or footnote) boundary is one paragraph.

    A figure caption sitting between the two halves must not stop the join: the
    text really is one sentence, and treating the second half as a new paragraph
    starts a translation mid-clause.
    """
    merged: list[dict] = []
    for b in blocks:
        if b["type"] == "p" and merged:
            j = len(merged) - 1
            while j >= 0 and _skip_when_stitching(merged[j]):
                j -= 1
            if j >= 0 and merged[j]["type"] == "p":
                prev = merged[j]["text"]
                if (len(prev) + len(b["text"]) <= limit
                        and not _is_figure_caption(b["text"])
                        and (b["text"][:1].islower()
                             or not re.search(r'[.!?:;"”’)]$', prev))):
                    merged[j]["text"] = _join([prev, b["text"]])
                    if str(b["page"]) != str(merged[j]["page"]):
                        merged[j]["page_end"] = b["page"]
                    continue
        merged.append(b)
    return merged


def _stitch_prose(blocks, limit: int = 6000):
    """Join prose fragments split by a page break, footnote run or figure caption.

    ``build_ir`` merges paragraph fragments while walking the pages; the walk
    stops at a figure caption, which left the second half of the sentence alone
    at the top of the next page ("peak associations, labor unions, ...").
    """
    merged: list[dict] = []
    for b in blocks:
        if b["type"] == "p":
            # the previous *body* block, even when notes or a figure sit between
            j = len(merged) - 1
            while j >= 0 and _skip_when_stitching(merged[j]):
                j -= 1
            if j >= 0 and merged[j]["type"] == "p":
                prev = merged[j]["text"]
                if (len(prev) + len(b["text"]) <= limit
                        and not _is_figure_caption(prev)
                        and not _is_figure_caption(b["text"])
                        and not _label_like(b["text"])
                        and not _label_like(prev)
                        and (b["text"][:1].islower()
                             or not re.search(r'[.!?:;"”’)]$', prev))):
                    merged[j]["text"] = _join([prev, b["text"]])
                    if str(b["page"]) != str(merged[j]["page"]):
                        merged[j]["page_end"] = b["page"]
                    continue
        merged.append(b)
    return merged


def _promote_short_titles(blocks: list[dict]) -> list[dict]:
    """A short title-case line at a page top / after prose is a section heading.

    Without this, subsection heads such as "Structure" or "Rationality" stay
    body paragraphs and are later dropped as OCR debris (under 25 characters).
    """
    from collections import Counter
    cont = frozenset(
        "a an the and or but of in on at to for with from by as that this these "
        "those it its we they he she i you was were is are had has have not no".split())
    # a page crowded with short fragments is a diagram/map or a contents list,
    # not prose with section heads
    busy = Counter(str(b.get("page")) for b in blocks
                   if b.get("type") in ("p", "note")
                   and len(b.get("text") or "") <= 60)
    for i, b in enumerate(blocks):
        if b.get("type") != "p":
            continue
        t = (b.get("text") or "").strip()
        words = t.split()
        if not (2 <= len(t) <= 60 and 1 <= len(words) <= 5):
            continue
        if re.search(r"[.!?;:,。，；：、]$", t) or busy[str(b.get("page"))] >= 8:
            continue
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        if not nxt or nxt.get("type") != "p":
            continue
        prev = blocks[i - 1] if i else None
        page_break = prev is not None and str(prev.get("page")) != str(b.get("page"))
        after_prose = prev is not None and len(prev.get("text") or "") >= 180
        if not (page_break or after_prose):
            continue
        if t[:1].isupper():
            b["type"] = "h3"
        elif (page_break and len(words) <= 7 and words[0].lower() not in cont
              and not _is_figure_caption(t)):
            # some books print section heads in lower case; a lower-case line at
            # a page top that is not a continuation word is such a head
            b["type"] = "h3"
    return blocks


def _layout_labels(layout) -> list[str]:
    """Printed page numbers read from page images; blanks are filled from the
    surrounding numeric (or roman) run, leading blanks stay empty (front matter)."""
    labs = []
    for label, _items in layout:
        s = str(label or "").strip().lower()
        labs.append(s if re.fullmatch(r"\d{1,4}|[ivxlcdm]{1,7}", s) else "")
    if not any(labs):
        return [str(i + 1) for i in range(len(layout))]
    prev_i, prev_v = None, None
    for i, l in enumerate(labs):
        if l.isdigit():
            prev_i, prev_v = i, int(l)
        elif prev_v is not None and not l:
            labs[i] = str(prev_v + (i - prev_i))
        elif l and ROMAN.fullmatch(l):
            prev_i, prev_v = i, _from_roman(l)
    # "vi" read as "6": a digit between roman numerals is a misread numeral
    known = [(i, l) for i, l in enumerate(labs) if l]
    for k, (i, l) in enumerate(known):
        if not l.isdigit() or not 0 < int(l) <= 60:
            continue
        prev = next((x for _j, x in reversed(known[:k]) if not x.isdigit()), "")
        nxt = next((x for _j, x in known[k + 1:] if not x.isdigit()), "")
        if prev and nxt and ROMAN.fullmatch(prev) and ROMAN.fullmatch(nxt):
            labs[i] = _to_roman(int(l))
    return labs


def _ocr_page_paras(text):
    """Split a transcribed page into paragraphs on blank lines."""
    out = []
    for para in re.split(r"\n\s*\n", text or ""):
        para = re.sub(r"\s*\n\s*", " ", para).strip()
        if len(para) > 1:
            out.append(para)
    return out


def _ocr_blocks(pages, labels):
    """OCR pages carry no coordinates: paragraphs are separated by blank lines."""
    out = []
    for i, text in enumerate(pages):
        label = labels[i] if i < len(labels) and labels[i] else str(i + 1)
        for para in re.split(r"\n\s*\n", text or ""):
            para = re.sub(r"\s*\n\s*", " ", para).strip()
            if len(para) < 2:
                continue
            typ = "p"
            if len(para) <= 80 and para.isupper():
                typ = "h2"
            out.append({"page": label, "type": typ, "text": para})
    return out


def _page_blocks(lines: list[str]) -> list[str]:
    cur: list[str] = []
    out: list[str] = []
    body = [l for l in lines if l.strip()]
    indents = [len(l) - len(l.lstrip(" ")) for l in body]
    widths = [len(l.rstrip()) for l in body]
    thr = 3
    prev_len = None
    for line in lines:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        text = _norm(line)
        new_para = (not cur) or indent >= thr
        if new_para and cur:
            out.append(_join(cur))
            cur = [text]
        elif new_para:
            cur = [text]
        else:
            cur.append(text)
        prev_len = len(line.rstrip())
    if cur:
        out.append(_join(cur))
    return out


def _join(parts: list[str], vocab: set | None = None) -> str:
    """Join PDF lines, undoing line-end hyphenation.

    ``vocab`` (lowercased words of the book) decides ambiguity: if the fused
    form occurs elsewhere the hyphen is typographic and dropped; if only the
    hyphenated form occurs it is a real compound and kept.
    """
    s = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if s and s[-1] in "-\u00ad\u2010" and len(s) > 1 and s[-2].isalpha():
            base, joined, kept = s[:-1], s[:-1] + p, s + p
            m = re.search(r"([A-Za-z]+)$", base)
            prefix = m.group(1) if m else ""
            if p[:1].isupper() or p[:1].isdigit() or (prefix.isupper() and len(prefix) > 1):
                s = kept
            elif vocab and joined.lower() in vocab:
                s = joined
            elif vocab and kept.lower() in vocab:
                s = kept
            else:
                s = kept if prefix.lower() in HYPHEN_KEEP else joined
        else:
            s = (s + " " + p) if s else p
    return s


BLOCKS = {"h1": "@@H1@@", "h2": "@@H2@@", "p": "@@P@@", "note": "@@N@@"}


def _is_figure_caption(t: str) -> bool:
    return bool(CAPTION.search((t or "")[:120]))


def _label_like(t: str) -> bool:
    """Short heading / diagram label / running head that must not be glued to prose."""
    t = (t or "").strip()
    if not t:
        return True
    if t[:1] in "•·◦▪-–—*":
        return True
    return len(t) <= 40 and len(t.split()) <= 5 and not re.search(r"[.!?:;,]\s*$", t)


def _absorb_figure_labels(blocks: list[dict]) -> list[dict]:
    """Diagram pages: drop the small labels printed above a figure caption.

    The caption stays behind as its own block so the figure cropper can anchor
    on it; labels are part of the artwork and would otherwise become footnotes.
    """
    out: list[dict] = []
    for b in blocks:
        t = b.get("text") or ""
        if b.get("type") in ("p", "h2", "note") and len(t) <= 400 and _is_figure_caption(t):
            page = str(b.get("page"))
            while (out and str(out[-1].get("page")) == page
                   and out[-1].get("type") in ("p", "note", "h2")
                   and len(out[-1].get("text") or "") <= 250
                   and (out[-1].get("type") == "note" or _label_like(out[-1].get("text") or ""))
                   and not _is_figure_caption(out[-1].get("text") or "")):
                out.pop()
            m = CAPTION.search(t[:120])
            if m is None:
                out.append(b)
                continue
            prefix = t[:m.start()].strip(" •·◦▪-–—*")
            if len(prefix) > 25:            # a real prose fragment before the caption
                out.append({"page": page, "type": "p", "text": prefix})
            out.append({"page": page, "type": "p", "text": t[m.start():].strip()})
            continue
        out.append(b)
    return out


def _merge_note_runs(blocks: list[dict]) -> list[dict]:
    """A footnote broken over several PDF blocks is one note."""
    out: list[dict] = []
    for b in blocks:
        if (b.get("type") == "note" and out and out[-1].get("type") == "note"
                and str(out[-1].get("page")) == str(b.get("page"))
                and not re.match(r"^\d{1,3}[.:]?\s", (b.get("text") or "").strip())):
            out[-1]["text"] = _join([out[-1]["text"], b["text"]])
            out[-1].pop("page_end", None)
            continue
        out.append(b)
    return out


def build_ir(pages: list[str], labels: list[str], promote: bool = True,
             paras: list[list[str]] | None = None) -> list[dict]:
    blocks: list[dict] = []
    mode = "body"
    for idx, raw in enumerate(pages):
        lines = raw.split("\n")
        i = 0
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i < len(lines) and _is_header(lines[i], labels[idx]):
            lines = lines[i + 1:]
        if paras and paras[idx]:
            for t, lvl in paras[idx]:
                if BOILER.search(t):
                    continue
                typ = "h1" if lvl in (1, 10) else "h2" if lvl == 2 else ("note" if lvl == -1 else "p")
                if lvl != 10 and typ == "h1" and re.match(r"^\d{1,2}\.\d", t):
                    typ = "h2"          # 2.1 / 3.4 are sections, not chapters
                elif lvl != 10 and typ == "h1" and not re.match(r"^\d{1,2}[\s.]", t):
                    typ = "h2"          # front matter headings are not chapters
                if typ == "h2" and re.match(r"^\d{1,2}(?:\.\s|\s)", t) and len(t.split()) <= 12:
                    typ = "h1"
                if typ == "p" and NOTE.match(t) and not _caps(t[1:3]):
                    typ = "note"
                if typ == "p":
                    sp = split_runin_h3(t)
                    if sp:
                        blocks.append({"page": labels[idx], "type": "h3", "text": sp[0]})
                        blocks.append({"page": labels[idx], "type": "p", "text": sp[1]})
                        continue
                    if (H3_RUN.match(t) and len(t) <= 70 and len(t.split()) <= 10
                            and not t.rstrip().endswith((".", ",", ";", ":"))):
                        typ = "h3"
                blocks.append({"page": labels[idx], "type": typ, "text": t})
            continue
        texts = [x for x in _page_blocks(lines) if not BOILER.search(x)]
        page = labels[idx]
        if not texts:
            continue
        words = sum(len(t.split()) for t in texts)
        lead = []
        for t in texts:
            if _caps(t) and len(t) < 90:
                lead.append(t)
            else:
                break
        if lead and (words < 30 or any(CHAPTER.match(t) for t in lead)):
            blocks.append({"page": page, "type": "h1", "text": " ".join(lead)})
            texts = texts[len(lead):]
        for t in texts:
            if mode != "body":
                if mode == "bib":
                    blocks.append({"page": page, "type": "bib", "text": t})
                continue
            if _caps(t) and BACK.match(t) and len(t) < 30:
                title = t.strip()
                blocks.append({"page": page, "type": "h1", "text": title})
                mode = "bib" if title.upper().startswith(("BIBLIO", "REFEREN")) else "skip"
                continue
            sp = split_runin_h3(t) if not _caps(t) else None
            if sp:
                blocks.append({"page": page, "type": "h3", "text": sp[0]})
                blocks.append({"page": page, "type": "p", "text": sp[1]})
                continue
            if (H3_RUN.match(t) and len(t) <= 70 and len(t.split()) <= 10
                    and not _caps(t) and not t.rstrip().endswith((".", ",", ";", ":"))):
                blocks.append({"page": page, "type": "h3", "text": t})
                continue
            m = NOTE.match(t)
            if m and not _caps(t[m.end():]):
                blocks.append({"page": page, "type": "note", "text": t})
            elif _caps(t) and len(t) < 90:
                blocks.append({"page": page, "type": "h2", "text": t})
            elif H2NUM.match(t) and _caps(H2NUM.match(t).group(2)):
                blocks.append({"page": page, "type": "h2", "text": t})
            else:
                blocks.append({"page": page, "type": "p", "text": t})

    merged = _stitch_prose(blocks)

    merged = _absorb_figure_labels(merged)
    merged = _merge_note_runs(merged)
    merged = _mark_bylines(merged)
    merged = _promote_chapters(merged) if promote else merged
    merged = [b for b in merged if not PAGENO.match(b["text"]) and not META.match(b["text"])]
    merged = _drop_running_heads(merged)
    merged = _strip_markers(merged)
    merged = _promote_short_titles(merged)
    merged = _guess_headings(merged)
    merged = _drop_bad_headings(merged)
    merged = _drop_heading_fragments(merged)
    merged = _detect_references(merged, pages, labels)
    merged = _mark_references(merged)
    merged = _merge_title_subtitle(merged)
    merged = _merge_label_titles(merged)
    merged = _demote_section_h1s(merged)
    merged = _mark_tail_references(merged)
    for n, b in enumerate(merged):
        b["id"] = "b%04d" % n
    return merged


PARTICLES = {"and", "&", "de", "van", "von", "da", "di", "del", "der", "la", "le",
             "du", "dos", "ter", "ten"}


def _person_line(t: str) -> bool:
    """A byline such as 'Cary J. Nederman and Guillaume Bogiaris'."""
    ws = t.split()
    if not 2 <= len(ws) <= 8 or _caps(t) or re.search(r"\d", t):
        return False
    if re.search(r"[.,;:!?]$", t):
        return False
    for w in ws:
        core = w.strip(".,")
        if not core:
            continue
        if w.lower() in PARTICLES:
            continue
        if not (core[0].isupper() or (len(core) == 1 and core.isalpha())):
            return False
    return True


def _mark_bylines(blocks: list[dict]) -> list[dict]:
    """A name line directly under a chapter/section title is the author byline."""
    for i, b in enumerate(blocks):
        if i == 0 or b["type"] not in ("h1", "h2", "p"):
            continue
        prev = blocks[i - 1]
        if prev["type"] not in ("h1", "h2") or _caps(prev["text"]):
            continue
        if str(prev.get("page")) != str(b.get("page")):
            continue
        t = b["text"].strip()
        if len(t) <= 80 and _person_line(t):
            b["type"] = "byline"
    return blocks


def _promote_chapters(blocks: list[dict]) -> list[dict]:
    """A chapter starts with an unnumbered all-caps run at page start, usually after notes."""
    out, i = [], 0
    while i < len(blocks):
        b = blocks[i]
        at_start = i == 0 or blocks[i - 1]["page"] != b["page"]
        after_notes = i > 0 and blocks[i - 1]["type"] == "note"
        if b["type"] == "h2" and at_start and not H2NUM.match(b["text"]) \
                and not REF_HEAD.match(b["text"].strip()):
            run, j = [b], i + 1
            while (j < len(blocks) and blocks[j]["type"] == "h2"
                   and blocks[j]["page"] == b["page"] and not H2NUM.match(blocks[j]["text"])):
                run.append(blocks[j])
                j += 1
            on_page = [x for x in blocks if x["page"] == b["page"]]
            heading_only = bool(on_page) and all(x["type"] in ("h1", "h2") for x in on_page)
            caps_run = all(_caps(x["text"]) for x in run)
            byline_run = j < len(blocks) and blocks[j].get("type") == "byline" \
                and str(blocks[j].get("page")) == str(b["page"])
            # a title-case heading at the top of a page is usually a section of
            # the chapter that just ended, not a new chapter
            if (any(CHAPTER.match(x["text"]) for x in run)
                    or byline_run
                    or (heading_only and len(run) >= 2)
                    or (caps_run and (after_notes or i == 0))):
                out.append({"page": b["page"], "type": "h1",
                            "text": " ".join(x["text"] for x in run)})
                i = j
                continue
        out.append(b)
        i += 1
    return out


def _sq(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def apply_structure(blocks: list[dict], chapters: list[dict]) -> list[dict]:
    """Insert chapter headings at the first block of the given printed page."""
    items = sorted(chapters, key=lambda c: _num(c["page"]) or 0)
    out, ci = [], 0
    for b in blocks:
        pg = _num(b["page"])
        while ci < len(items) and pg is not None and pg >= (_num(items[ci]["page"]) or 0):
            out.append({"page": items[ci]["page"], "type": "h1", "text": items[ci]["title"]})
            ci += 1
        out.append(b)
    while ci < len(items):
        out.append({"page": items[ci]["page"], "type": "h1", "text": items[ci]["title"]})
        ci += 1
    return out


def clean_blocks(blocks: list[dict]) -> list[dict]:
    """Drop front matter (no printed page) and short OCR debris."""
    out = []
    for b in blocks:
        pg = str(b["page"])
        if not ((pg.isdigit() and 1 <= int(pg) <= 999) or re.fullmatch(r"[ivxlcdm]{1,7}", pg)):
            continue
        if b["type"] == "p" and len(b["text"]) < 25:
            # keep a genuine short paragraph; drop only lines without real words
            t = b["text"].strip()
            if len(t) < 12 or not re.search(r"[A-Za-z\u4e00-\u9fff]{2}", t):
                continue
        out.append(b)
    for n, b in enumerate(out):
        b["id"] = "b%04d" % n
    return out


def _num(page: str):
    try:
        return int(page)
    except (TypeError, ValueError):
        return None


PAGENO = re.compile(r"^\s*(?:\d{1,3}|[ivxlcdm]{1,7})\s*$", re.I)
META = re.compile(r"^(word count|page count)\b", re.I)


def _drop_running_heads(blocks):
    """A short line repeated on many pages is a running head, not content."""
    from collections import Counter
    pages = {}
    for b in blocks:
        key = re.sub(r"\d+\s*$", "", b["text"].strip().lower())
        if len(key) < 80 and key:
            pages.setdefault(key, set()).add(str(b["page"]))
    total = max(1, len({str(b["page"]) for b in blocks}))
    heads = {k for k, v in pages.items() if len(v) >= max(3, total * 0.25)}
    if not heads:
        return blocks
    return [b for b in blocks if re.sub(r"\d+\s*$", "", b["text"].strip().lower()) not in heads]


def _drop_heading_fragments(blocks):
    """A title split by a page break can leave its first line behind as a heading."""
    def key(t):
        return re.sub(r"[^A-Z0-9]", "", t.upper())

    def page(b):
        try:
            return int(re.match(r"\d+", str(b.get("page", ""))).group(0))
        except (AttributeError, ValueError):
            return None

    heads = [b for b in blocks if b["type"] == "h1"]
    drop = set()
    for a, b in zip(heads, heads[1:]):
        ka, kb = key(a["text"]), key(b["text"])
        pa, pb = page(a), page(b)
        if not ka or not kb or (pa is not None and pb is not None and abs(pb - pa) > 1):
            continue
        if len(ka) >= 8 and len(ka) < len(kb) and kb.startswith(ka):
            short = a
        elif len(kb) >= 8 and len(kb) < len(ka) and ka.startswith(kb):
            short = b
        else:
            continue
        if re.match(r"(?i)^(?:part|book|chapter)\b", short["text"].strip()):
            continue                       # "PART ONE" may genuinely precede "PART ONE: ..."
        drop.add(id(short))
    if not drop:
        return blocks
    return [b for b in blocks if id(b) not in drop]


def _drop_bad_headings(blocks):
    """Reject journal mastheads / running heads used as the document title."""
    from collections import Counter
    seen = Counter(re.sub(r"\d+\s*$", "", b["text"].strip().lower())
                   for b in blocks if b["type"] == "h1")
    ok = []
    for b in blocks:
        if b["type"] == "h1":
            key = re.sub(r"\d+\s*$", "", b["text"].strip().lower())
            roman = bool(re.fullmatch(r"[ivxlcdm]{1,7}", str(b.get("page", ""))))
            if SKIP_HEAD.match(b["text"]) or seen[key] >= 3 or (roman and not re.match(r"^\d", b["text"])):
                b["type"] = "h2"
        ok.append(b)
    if not any(b["type"] == "h1" for b in ok):
        for b in ok[:15]:
            t = b["text"].strip()
            if (b["type"] == "p" and 25 <= len(t) <= 200 and len(t.split()) >= 3
                    and "|" not in t and t.count(",") <= 2 and not SKIP_HEAD.match(t)
                    and not _is_figure_caption(t)):
                b["type"] = "h1"
                break
    return ok


def _strip_markers(blocks):
    for b in blocks:
        if b["type"] != "p":
            continue
        # long glued digit strings are OCR page-number debris; never touch years
        b["text"] = re.sub(r"(?<!\d)\d{7,}(?!\d)", "", b["text"])
    return blocks


def _split_long_blocks(blocks, limit=1200):
    """A two-column PDF can hand us a whole column (or page) as one block."""
    out = []
    for b in blocks:
        text = b["text"]
        if b["type"] != "p" or len(text) <= limit:
            out.append(b)
            continue
        chunks, cur = [], ""
        for piece in re.split(r"(?<=[.!?。！？])\s+", text):
            if len(cur) + len(piece) > limit and cur:
                chunks.append(cur.strip())
                cur = piece
            else:
                cur = (cur + " " + piece).strip()
        if cur:
            chunks.append(cur.strip())
        for c in chunks:
            nb = dict(b)
            nb["text"] = c
            out.append(nb)
    return out


def _guess_headings(blocks: list[dict]) -> list[dict]:
    """No font signal (uniform-size PDFs): short standalone lines become headings."""
    heads = sum(1 for b in blocks if b["type"] in ("h1", "h2"))
    if heads >= 4:
        return blocks
    for i, b in enumerate(blocks):
        if b["type"] != "p" or b["text"].strip()[:1].isdigit():
            continue
        t = b["text"].strip()
        nxt = blocks[i + 1]["text"] if i + 1 < len(blocks) else ""
        at_top = i == 0 or blocks[i - 1].get("page") != b.get("page")
        if (i < 12 or at_top) and 15 <= len(t) <= 160 and 2 <= len(t.split()) <= 14 \
                and t[:1].isupper() and not t.endswith((".", ",", ";", ":", "：")) \
                and "|" not in t and t.count(",") <= 2 and not SKIP_HEAD.match(t) \
                and b["type"] == "p" and not _is_figure_caption(t):
            b["type"] = "h1"
        elif (1 <= len(t.split()) <= 3 and len(t) <= 60 and _caps(t)
              and not t.endswith((".", ",", ";"))):
            b["type"] = "h2"
    return blocks


_REF_BOUND = (r"(?:^|(?<=[.;:!?)\]]\s)|(?<=[.;:!?)\]][\u201d\u2019\"']\s)"
              r"|(?<=\n))")
REF_START = re.compile(_REF_BOUND + r"(?=" + _SUR + _REF_TAIL + _REF_YEAR + ")")
REF_HEAD = re.compile(r"^(references?|bibliography|works cited|reference list|literature cited|references cited)$", re.I)


REF_BACK = re.compile(r"^(bibliography|works cited)\b", re.I)


def _ref_entryish(t: str) -> bool:
    """A stand-alone bibliography entry, not a prose sentence that cites works."""
    t = (t or "").strip()
    if not (20 <= len(t) <= 500):
        return False
    if REF_ENTRY.match(t):
        return True
    if re.match(r"^(?:19|20)\d{2}[a-z]?\.\s", t):
        return True
    if not re.match(r"^[A-Z][\w\u2019'\-]+,\s", t):
        return False
    hits = 0
    if re.search(r"\b(?:19|20)\d{2}[a-z]?\b", t):
        hits += 1
    if re.search(r"\b(?:Press|University|Univ\.|Journal|Review|Publishers?|Books)\b", t):
        hits += 1
    if re.search(r"\bpp?\.\s?\d|\bVol\.\s?\d|\bdoi:", t):
        hits += 1
    if re.search(r"\bet al\.", t):
        hits += 1
    return hits >= 2


def _detect_references(blocks: list[dict], pages=None, labels=None) -> list[dict]:
    """Book-level bibliography: rebuild entries page by page, untranslated."""
    # the printed BIBLIOGRAPHY heading is the only reliable anchor: chapter-end
    # "References" headings are handled later, per chapter
    head = None
    for i in range(int(len(blocks) * 0.55), len(blocks)):
        b = blocks[i]
        if b["type"] in ("h1", "h2", "p") and REF_BACK.match(b["text"].strip()):
            head = i
            break
    if head is not None:
        hb = blocks[head]
        title = REF_BACK.match(hb["text"]).group(0)
        rest = hb["text"][len(title):].strip(" :.\u2014-")
        out = blocks[:head]
        out.append({"page": hb["page"], "type": "h1", "text": title, "book_bib": True})
        entries: list[dict] = []

        def add(text: str, page) -> None:
            for piece in REF_START.split(text):
                piece = piece.strip()
                if len(piece) <= 12 or re.match(r"^(https?://|www\.)", piece):
                    continue
                if entries and not REF_ENTRY.match(piece):
                    entries[-1]["text"] = entries[-1]["text"].rstrip() + " " + piece
                else:
                    entries.append({"page": page, "type": "bib", "text": piece})

        if rest:
            add(rest, hb["page"])
        index_pages = set()
        if pages is not None and labels is not None:
            for lbl, txt in zip(labels, pages):
                if re.match(r"(?i)^\s*index\b", (txt or "").strip()[:120]):
                    index_pages.add(str(lbl))
        tail: list[dict] = []
        body = blocks[head + 1:]

        def lines_entries(start_page) -> list[dict]:
            """Hanging-indent segmentation of the printed bibliography pages."""
            if pages is None or labels is None or str(start_page) not in labels:
                return []
            got, cur, cur_page = [], [], start_page
            for i in range(labels.index(str(start_page)) + 1, len(pages)):
                if str(labels[i]) in index_pages:
                    break
                lines = [x for x in pages[i].split("\n") if x.strip()]
                if lines and REF_BACK.match(lines[0].strip()[:60]):
                    lines = lines[1:]
                ind = [len(x) - len(x.lstrip()) for x in lines]
                if not ind:
                    continue
                base = min(ind)
                for ln, lead in zip(lines, ind):
                    t = _norm(sanitize(ln))
                    if lead <= base + 2 and cur:
                        got.append((cur_page, cur))
                        cur = []
                    if t:
                        cur.append(t)
                        cur_page = labels[i]
            if cur:
                got.append((cur_page, cur))
            out2: list[dict] = []
            for page, parts in got:
                text = _join(parts)
                if not text:
                    continue
                if len(text) < 90 and _caps(text):
                    out2.append({"page": page, "type": "h2", "text": text})
                elif len(text) > 12:
                    out2.append({"page": page, "type": "bib", "text": text})
            return out2

        page_entries = lines_entries(hb["page"])
        if page_entries:
            for k, x in enumerate(body):
                if str(x["page"]) in index_pages:
                    tail = body[k:]
                    break
            return out + entries + page_entries + tail

        for k, x in enumerate(body):
            xt = x["text"].strip()
            if str(x["page"]) in index_pages or (
                    x["type"] == "h1" and not REF_BACK.match(xt)):
                tail = body[k:]
                break
            if x["type"] not in ("p", "note", "bib"):
                out.append(x)
                continue
            xt = re.sub(r"^(?:BIBLIOGRAPHY|REFERENCES)\b\s*[^\w‘“(\[]*", "", xt, flags=re.I)
            add(xt, x["page"])
        return out + entries + tail

    # a book whose reference lists sit at the end of each chapter ends in
    # references, not in a bibliography: _mark_references handles those
    if any(b["type"] in ("h1", "h2") and REF_HEAD.match(b["text"].strip())
           for b in blocks[int(len(blocks) * 0.5):]):
        return blocks

    flags = [1 if _ref_entryish(b["text"]) and b["type"] == "p" else 0 for b in blocks]
    best_start, run = None, 0
    for i in range(len(blocks) - 1, -1, -1):
        if flags[i]:
            run += 1
            if run >= 8:
                best_start = i
        elif blocks[i]["type"] != "note":
            run = 0
    if best_start is None:
        return blocks
    while best_start > 0:
        prev = blocks[best_start - 1]
        if prev["type"] == "p" and _ref_entryish(prev["text"]):
            best_start -= 1
        elif prev["type"] == "note":
            best_start -= 1
        else:
            break

    out = blocks[:best_start]
    entries = []
    for b in blocks[best_start:]:
        if b["type"] in ("h1", "h2", "figure", "table"):
            out.append(b)
            continue
        if b["type"] not in ("p", "bib", "note"):
            out.append(b)
            continue
        for piece in REF_START.split(b["text"]):
            piece = piece.strip()
            if len(piece) > 15 and not re.match(r"^(https?://|www\.)", piece) \
                    and _ref_entryish(piece):
                entries.append({"page": b["page"], "type": "bib", "text": piece})
    # a false positive would leave prose as one monster "entry"
    if len(entries) < 4 or sum(len(e["text"]) for e in entries) < 600:
        return blocks
    if max(len(e["text"]) for e in entries) > 1500:
        return blocks
    merged = []
    for b in out:
        is_ref_line = (b["type"] == "p" and len(b["text"]) < 320
                       and re.search(r"https?://|doi\.org|\bet al\.|Press\.|University\b", b["text"]))
        if is_ref_line and merged and merged[-1]["type"] == "bib":
            merged[-1]["text"] = merged[-1]["text"].rstrip() + " " + b["text"].strip()
            continue
        if is_ref_line and not (merged and merged[-1]["type"] == "bib"):
            b["type"] = "bib"
        merged.append(b)
    return merged + entries


def _mark_references(blocks: list[dict]) -> list[dict]:
    """Chapter-end REFERENCES/BIBLIOGRAPHY sections: untranslated, one entry per block.

    A book with 40 chapters has 40 such sections, so scanning stops at the next
    chapter heading instead of swallowing the rest of the book.
    """
    out: list[dict] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        t = b["text"].strip()
        if b.get("book_bib"):
            out.append(b)
            i += 1
            continue
        if not (len(t) <= 40 and REF_HEAD.match(t) and b["type"] in ("h1", "h2", "p")):
            out.append(b)
            i += 1
            continue
        out.append({"page": b["page"], "type": "h2", "text": "References",
                    "chapter_refs": True})
        i += 1
        entries: list[str] = []
        while i < len(blocks):
            x = blocks[i]
            xt = x["text"].strip()
            if x["type"] in ("h1", "h2") and not (
                    x["type"] == "h2" and REF_HEAD.match(xt)):
                break                       # next chapter or unrelated section
            i += 1
            if x["type"] not in ("p", "bib", "note"):
                continue
            if entries and not REF_ENTRY.match(xt):
                entries[-1] = entries[-1].rstrip() + " " + xt
                continue
            for piece in REF_START.split(xt):
                piece = piece.strip()
                if len(piece) > 12:
                    entries.append(piece)
        out += [{"page": b["page"], "type": "bib", "text": e, "chapter_refs": True}
                for e in entries if not re.match(r"^(https?://|www\.)", e)]
    return out


CITE_HINT = re.compile(r"(\bPress\b|\bUniv|\bpp?\.\s?\d|\b(19|20)\d{2}\b|Presented at|\bEd\.|\bVol\.|\bInc\.|\bLtd\.)")


def _mark_tail_references(blocks):
    """Reference pages: entries crowd a page, unlike prose with incidental citations."""
    def cite(t):
        score = 0
        if re.match(r"^[A-Z][A-Za-z\-\u2019']+,?\s+[A-Z]{1,3}\b", t.strip()):
            score += 1
        if re.search(r"\b(19|20)\d{2}[a-z]?\.", t):
            score += 1
        if re.search(r"(\bPress\b|\bUniv|\bPubl|\bInc\.|\bLtd\.|pp?\.\s?\d)", t):
            score += 1
        return score >= 2

    pages, stats = [], {}
    for b in blocks:
        lbl = str(b["page"])
        if lbl not in stats:
            pages.append(lbl)
            stats[lbl] = [0, 0]
        stats[lbl][1] += 1
        if b["type"] == "p" and cite(b["text"]):
            stats[lbl][0] += 1

    if not pages:
        return blocks
    start_i = int(len(pages) * 0.5)

    def ref_page(i):
        good, total = stats[pages[i]]
        return total >= 6 and good >= 5 and good / total >= 0.6

    first = None
    for i in range(start_i, len(pages) - 1):
        if ref_page(i) and ref_page(i + 1):
            first = i
            break
    if first is None:
        return blocks

    seen = set(pages[first:])
    for b in blocks:
        if str(b["page"]) in seen and b["type"] == "p":
            b["type"] = "bib"
    return blocks


CHAP_MARK = re.compile(
    r"(?i)^(?:(?:part|book|chapter)\b|(?:\d{1,2}|[ivxlcdm]{1,7})[.)]?\s+\S)")


def _merge_label_titles(blocks):
    """'Chapter One' plus its title line is one heading, not two TOC entries."""
    out = []
    for b in blocks:
        prev = out[-1] if out else None
        if (prev and prev["type"] == "h1" and b["type"] in ("h1", "p")
                and str(prev.get("page")) == str(b.get("page"))
                and CHAP_MARK.match(prev["text"].strip())
                and not CHAP_MARK.match(b["text"].strip())
                and len(b["text"]) <= 140
                and (b["type"] != "p" or not re.search(r"[.!?;:,]\s*$", b["text"].strip()))):
            prev["text"] = prev["text"].strip() + " " + b["text"].strip()
            continue
        out.append(b)
    return out


def _demote_section_h1s(blocks):
    """Only numbered chapters are h1; later unlabelled headings are sections."""
    if sum(1 for b in blocks if b["type"] == "h1"
           and CHAP_MARK.match(b["text"].strip())) < 2:
        return blocks
    started, section = False, False
    for b in blocks:
        if b["type"] == "h1":
            if CHAP_MARK.match(b["text"].strip()):
                started, section = True, False
            elif started:
                b["type"] = "h2"
                section = True
            continue
        if section and b["type"] == "h2":
            b["type"] = "h3"
    return blocks


def _merge_title_subtitle(blocks):
    """A title ending with ':' swallows the following short line."""
    out = []
    for b in blocks:
        if out and out[-1]["type"] == "h1" and out[-1]["text"].rstrip().endswith(":") \
                and b["type"] in ("p", "h2") and len(b["text"]) <= 140:
            out[-1]["text"] = out[-1]["text"].rstrip() + " " + b["text"].strip()
            continue
        out.append(b)
    return out


def chunk(blocks: list[dict], max_words: int = 4200) -> list[list[dict]]:
    out, cur, w = [], [], 0
    for b in blocks:
        n = len(b["text"].split())
        if cur and w + n > max_words:
            out.append(cur)
            cur, w = [], 0
        cur.append(b)
        w += n
    if cur:
        out.append(cur)
    return out


def ingest(pdf: Path, structure: list[dict] | None = None,
           pages: list[str] | None = None, from_ocr: bool = False,
           ocr_pages=None, layout=None, labels: list[str] | None = None,
           style_heads: list[dict] | None = None,
           ocr_layouts: dict[int, list[tuple[str, str]]] | None = None) -> dict:
    if pages is None:
        pages = read_pages(pdf)
    info = probe(pages)
    if layout is None and not info["has_text_layer"]:
        return {"ok": False, "probe": info}
    labels = _fix_roman_outliers(list(labels) if labels is not None else page_labels(pages))
    for _i, _lbl in enumerate(labels):
        if str(_lbl).strip():
            continue
        prev = str(labels[_i - 1]).strip() if _i else ""
        if prev.isdigit():
            labels[_i] = str(int(prev) + 1)
        elif prev and ROMAN.fullmatch(prev):
            # blank leaf between roman front matter and page 1 stays roman
            labels[_i] = _to_roman(_from_roman(prev) + 1)
        elif prev:
            labels[_i] = ""            # trailing gap: leave for clean_blocks
        else:
            labels[_i] = ""            # cover / half-title carry no folio
    labels = _fill_missing_labels(labels)
    if layout is not None:
        entries = [e if isinstance(e, dict) else {"label": e[0], "items": e[1], "src": None}
                   for e in layout]
        # printed page numbers read off the page image are more reliable than the
        # broken OCR layer's running heads
        labels = _layout_labels([(e["label"], e["items"]) for e in entries])
        blocks = blocks_from_layout([(labels[i], e["items"]) for i, e in enumerate(entries)])
        blocks = [b for b in blocks if str(b["page"]).strip()]
        blocks = [b for b in blocks if not BOILER.search(b["text"])]
        blocks = [b for b in blocks if not PAGENO.match(b["text"]) and not META.match(b["text"])]
        # printed table-of-contents lines ("INTRODUCTION ........ vii") are not content
        blocks = [b for b in blocks if not TOC_DOT.search(b["text"])]
        blocks = _drop_repeated_headings(blocks)   # before merging: a running head
        blocks = _merge_paragraphs(blocks)         # must not block a page-broken paragraph
        blocks = _promote_short_titles(blocks)
        blocks = _split_long_blocks(blocks)
        # one PDF page may hold two facing pages, so label k does not imply PDF page k
        lists = set(_list_page_indexes(pdf))
        skip = {str(labels[k]) for k, e in enumerate(entries) if e.get("src") in lists}
        if skip:
            blocks = [b for b in blocks if str(b["page"]) not in skip]
        blocks = _merge_title_subtitle(blocks)
        blocks = _merge_label_titles(blocks)
        blocks = _demote_section_h1s(blocks)
        blocks = _dedupe_headings(blocks)
    elif from_ocr:
        blocks = clean_blocks(_ocr_blocks(pages, labels))
    else:
        paras = page_paragraphs(pdf, labels)
        if ocr_layouts:
            if paras is None:
                paras = [[] for _ in pages]
            levels = {"h1": 10, "h2": 2, "note": -1}
            for i, items in ocr_layouts.items():
                if 0 <= i < len(pages):
                    # h1 keeps its level even when unnumbered: the vision model saw
                    # the page, unlike the front-matter heuristic below
                    paras[i] = [(t, levels.get(typ, 0)) for typ, t in items if t]
        if ocr_pages and paras:
            for i in ocr_pages:
                if i < len(paras) and i < len(pages) \
                        and not (ocr_layouts and i in ocr_layouts):
                    paras[i] = [(t, 0) for t in _ocr_page_paras(pages[i])] or None
        blocks = build_ir(pages, labels, promote=not structure, paras=paras)
        if style_heads:
            from .docxin import apply_headings
            apply_headings(blocks, style_heads)   # before clean: short styled heads survive
        blocks = clean_blocks(blocks)
        blocks = _apply_contents(blocks, pdf)
        lists = _list_page_indexes(pdf)
        skip = {str(labels[i]) for i in lists if i < len(labels)}
        if skip:
            blocks = [b for b in blocks if str(b["page"]) not in skip]
        if lists:
            # title page, copyright page and blurbs precede the printed contents
            pre = {str(labels[i]) for i in range(min(lists)) if i < len(labels)}
            blocks = [b for b in blocks
                      if b["type"] in ("figure", "table") or str(b["page"]) not in pre]
    if structure:
        blocks = apply_structure(blocks, structure)
        blocks = _drop_heading_fragments(blocks)
    for n, b in enumerate(blocks):
        b["id"] = "b%04d" % n
    if layout is not None:
        page_map: dict[str, int] = {}
        if any(e.get("src") is not None for e in entries):
            for k, e in enumerate(entries):
                if e.get("src") is not None:
                    page_map.setdefault(str(labels[k]), int(e["src"]))
        else:
            page_map = {str(l): i for i, l in enumerate(labels)}
    else:
        page_map = {}
        for i, l in enumerate(labels):
            page_map.setdefault(str(l), i)
    return {"ok": True, "probe": info, "labels": labels, "blocks": blocks,
            "page_map": page_map}


CN_KEY = re.compile(r"(?i)^(introduction|conclusion|epilogue|prologue|preface|foreword|"
                    r"afterword|appendix|part\b|chapter\b)")


def _norm_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _word_lines(page):
    """Text lines rebuilt from word baselines.

    pymupdf's own line ids split a right-aligned folio onto its own line, so
    words are clustered by y instead; a tolerance of 4pt keeps title and folio
    together without merging neighbouring lines.
    """
    words = sorted(page.get_text("words"), key=lambda w: (w[1], w[0]))
    lines: list[list] = []
    for w in words:
        if lines and abs(w[1] - lines[-1][0]) <= 4.0:
            lines[-1][1].append(w)
        else:
            lines.append([w[1], [w]])
    for item in lines:
        item[1].sort(key=lambda w: w[0])
    return [item[1] for item in lines]


FOLIO = re.compile(r"(?:\d{1,4}|[ivxlcdm]{1,7})", re.I)


def _merge_orphan_lines(lines: list[list]) -> list[list]:
    """Re-attach folios and chapter numbers that OCR put on their own baseline.

    Scanned contents pages often place the right-aligned folio, or the chapter
    number at the left margin, a few points away from the title's baseline; a
    spread may also merge the contents column with body text on the right.
    """
    rows: list[list] = []
    for ws in lines:
        words = [w for w in ws if sanitize(w[4]).strip()]
        if not words:
            continue
        segs = [[words[0]]]
        for w in words[1:]:
            if w[0] - segs[-1][-1][2] > 60 and len(segs[-1]) >= 1:
                segs.append([w])
            else:
                segs[-1].append(w)
        # a gap this wide only happens when two columns share a baseline: keep
        # the contents title and folio, drop the body-text fragment
        if len(segs) > 1:
            keep = segs[:1]
            for seg in segs[1:]:
                if len(seg) >= 3 and (any(w[4][:1].islower() for w in seg)
                                      or len(seg) >= 5):
                    continue
                keep.append(seg)
            segs = keep
        rows.extend(segs)

    # body text sharing a contents page (a spread) is long, punctuated prose
    # without a trailing folio; it would otherwise wreck the right-margin maths
    kept: list[list] = []
    for seg in rows:
        tx = _norm(" ".join(sanitize(w[4]).strip() for w in seg))
        if (len(tx) > 75 and not FOLIO.fullmatch(sanitize(seg[-1][4]).strip())
                and re.search(r"\b(?:the|and|of|to|in|that|was|were|is|as|for|with)\b", tx, re.I)):
            continue
        kept.append(seg)
    rows = kept

    ys = sorted({round(ws[0][1], 1) for ws in lines if ws})
    gaps = [b - a for a, b in zip(ys, ys[1:]) if b - a > 7]
    tol = 12.0
    if gaps:
        gaps.sort()
        tol = max(10.0, min(45.0, 0.6 * gaps[len(gaps) // 2]))

    def _num_token(t: str) -> bool:
        return bool(re.fullmatch(r"(?:\d{1,2}\.?|[IVXLC]{1,6}\.)", t))

    def _text(item) -> str:
        return _norm(" ".join(sanitize(w[4]).strip() for w in item))

    def _titleish(item, allow_folio: bool = False) -> bool:
        words = item
        text = _text(item)
        last = sanitize(words[-1][4]).strip()
        if text.startswith("(") or re.match(r"(?i)^part\b", text):
            return False
        if not allow_folio and FOLIO.fullmatch(last):
            return False
        return not (len(words) == 1 and len(re.findall(r"[A-Za-z]", text)) < 4)

    def _nearest(rows: list[list], i: int, allow_folio: bool = False):
        best = None
        for j, item in enumerate(rows):
            if j == i or not _titleish(item, allow_folio):
                continue
            dy = abs(item[0][1] - rows[i][0][1])
            if dy <= tol and (best is None or dy < best[0]):
                best = (dy, j)
        return best[1] if best else None

    # pass 1: a bare right-aligned folio belongs to the nearest title line,
    # whether it sits above or below it
    drop: set[int] = set()
    for i, words in enumerate(rows):
        tokens = [sanitize(w[4]).strip() for w in words]
        if len(words) == 1 and FOLIO.fullmatch(tokens[0]):
            j = _nearest(rows, i)
            if j is not None:
                rows[j].append(words[0])
                drop.add(i)
    rows = [r for i, r in enumerate(rows) if i not in drop]

    # pass 2: a bare chapter number ("7." / "IV.") prefixes the nearest title
    drop = set()
    for i, words in enumerate(rows):
        tokens = [sanitize(w[4]).strip() for w in words]
        if len(words) == 1 and _num_token(tokens[0]):
            j = _nearest(rows, i, allow_folio=True)
            if j is not None:
                rows[j] = [words[0]] + rows[j]
                drop.add(i)
    rows = [r for i, r in enumerate(rows) if i not in drop]

    # pass 3: "4.   105" on one baseline (number and folio, title above)
    drop = set()
    for i, words in enumerate(rows):
        tokens = [sanitize(w[4]).strip() for w in words]
        if (len(words) == 2 and _num_token(tokens[0]) and FOLIO.fullmatch(tokens[1])
                and words[1][0] > words[0][0] + 30):
            j = _nearest(rows, i)
            if j is not None:
                rows[j] = [words[0]] + rows[j] + [words[1]]
                drop.add(i)
    rows = [r for i, r in enumerate(rows) if i not in drop]
    return rows


def _toc_line_stats(lines: list[list]) -> tuple[int, int, int]:
    """(right-aligned folios, dotted-leader lines, sentence-ending lines)."""
    if not lines:
        return 0, 0, 0
    x0 = min(w[0] for ws in lines for w in ws)
    x1 = max(w[2] for ws in lines for w in ws)
    right = x1 - 0.15 * max(1.0, x1 - x0)
    folios = dots = sentences = 0
    for ws in lines:
        text = " ".join(w[4] for w in ws).strip()
        if FOLIO.fullmatch(ws[-1][4].strip()) and ws[-1][0] > right:
            folios += 1
        if TOC_DOT.search(text) or (
                len(ws) >= 2 and FOLIO.fullmatch(ws[-1][4].strip())
                and re.fullmatch(r"[.…·]{2,}", ws[-2][4].strip())):
            dots += 1
        if text.endswith((".", "。", "！", "？")):
            sentences += 1
    return folios, dots, sentences


def _toc_pages(doc, limit: int = 30) -> list[int]:
    """PDF pages that print the table of contents, continuation pages included.

    The header ("Contents" / "目录") is the strongest signal, but many scanned
    contents pages lose it or never had one, so right-aligned folios and dotted
    leaders are accepted as well.  Prose pages are rejected by their sentence
    punctuation, which listing pages do not have.
    """
    out: list[int] = []
    prev = False
    for i in range(min(limit, len(doc))):
        lines = _word_lines(doc[i])
        if not lines:
            prev = False
            continue
        header = any(LIST_HEAD.match(" ".join(w[4] for w in ws).strip())
                     or re.search(r"(?i)\bcontents\b", " ".join(w[4] for w in ws))
                     or re.search(r"目\s*[录錄次]", " ".join(w[4] for w in ws))
                     for ws in lines[:3])
        folios, dots, sentences = _toc_line_stats(lines)
        prose = sentences > max(1, len(lines) * 0.25)
        listing = folios >= 4 and folios >= len(lines) * 0.25 and not prose
        dotted = dots >= 3 and dots >= len(lines) * 0.15 and not prose
        if header or listing or dotted or (prev and folios >= 2 and not prose):
            out.append(i)
            prev = True
        else:
            prev = False
    return out


def _parse_contents(pdf, limit: int = 30) -> list[dict]:
    """Printed Contents entries read from word coordinates.

    Titles sit at the left, the folio is right-aligned on the same line, and the
    chapter author is the unnumbered line underneath. Pairing bare lines instead
    (the old approach) attaches authors to the next chapter's number.
    """
    try:
        import pymupdf
        doc = pymupdf.open(str(pdf))
    except Exception:                                  # noqa: BLE001
        return []
    pages = _toc_pages(doc, limit)
    if not pages:
        return _parse_contents_text(pdf, limit)
    entries: list[dict] = []
    pending = False
    carry: list[tuple[float, str]] = []
    for pno in pages:
        lines = _merge_orphan_lines(_word_lines(doc[pno]))
        if not lines:
            continue
        carry = []
        head = [_norm(" ".join(w[4] for w in ws)) for ws in lines[:2]]
        if any(re.fullmatch(r"(?i)(?:list of\s+)?(?:figures|maps|tables|illustrations|plates)"
                            r"(?:\s+\(?cont(?:inued|\.)?\)?)?", h) for h in head):
            continue                        # a List of Figures/Maps page, not the TOC
        x0 = min(w[0] for ws in lines for w in ws)
        x1 = max(w[2] for ws in lines for w in ws)
        right = x1 - 0.15 * max(1.0, x1 - x0)
        for ws in lines:
            words = [(w[0], sanitize(w[4]).strip()) for w in ws]
            words = [w for w in words if w[1]]
            if not words:
                continue
            text = _norm(" ".join(w[1] for w in words))
            last = words[-1]
            if len(words) > 1 and FOLIO.fullmatch(last[1]) and last[0] > right:
                rest = words[:-1]
                if re.fullmatch(r"(?i)contents(?:\s+[ivxlcdm\d]+)?",
                                _norm(" ".join(w[1] for w in rest))):
                    carry = []
                    continue
                chapter = pending
                pending = False
                start = words[0][0]
                if len(rest) > 1 and re.fullmatch(r"\d{1,2}", rest[0][1]):
                    chapter, rest = True, rest[1:]
                title = _norm(" ".join(w[1] for w in rest)).strip()
                if carry and start > carry[0][0] + 4:
                    # the entry started on the previous line and wrapped; the
                    # folio sits at the end of the continuation
                    title = _norm(" ".join([t for _, t in carry] + [title])).strip()
                    start = carry[0][0]
                chapter = chapter or bool(re.match(
                    r"^(?:\d{1,2}[.)]?|[IVXLC]{1,6}[.)])(?:\s|$)", title))
                carry = []
                title = re.sub(r"(?i)\s+page$", "", title)
                if len(title) >= 2:
                    entries.append({"title": title, "page": last[1].lower(),
                                    "chapter": chapter, "level": 1, "x0": start})
                continue
            if re.fullmatch(r"(?i)(?:[ivxlcdm\d]+\s+)?contents", text):
                carry = []
                continue
            if re.fullmatch(r"(?i)page\s+[ivxlcdm]+", text):
                continue
            if re.fullmatch(r"\d{1,2}", text):
                pending = True          # bare chapter number above its title
                carry = []
                continue
            if re.match(r"(?i)^part\s+[ivxlcdm\d]+\b", text):
                entries.append({"title": text, "page": None, "chapter": True,
                                "level": 1, "part": True, "x0": words[0][0]})
                carry = []
                continue
            if (entries and entries[-1].get("part")
                    and entries[-1].get("page") is None
                    and (text.startswith("(") or _caps_line(text))
                    and len(text) <= 80):
                entries[-1]["title"] += " " + text
                continue
            if entries and entries[-1].get("page") and _person_line(text):
                entries[-1].setdefault("author", text)
                continue
            if (carry and not text.startswith("(")
                    and not re.match(r"(?i)^part\b", text)
                    and not re.match(r"^\d{1,2}[.)]?\s", text)
                    and len(text) <= 70 and len(text.split()) <= 9):
                carry.append((words[0][0], text))    # wrapped title continuation
                continue
            # a numbered / all-caps title whose folio is on the continued line
            if re.match(r"^\d{1,2}[.)]?\s+\S", text) or (_caps_line(text) and len(text) > 3):
                carry.append((words[0][0], text))
    return entries if len(entries) >= 2 else _parse_contents_text(pdf, limit)


def _caps_line(t: str) -> bool:
    return len(t) <= 80 and bool(re.fullmatch(r"[A-Z0-9 ,.'’\-]+", t))


def _parse_contents_text(pdf, limit: int = 30) -> list[dict]:
    """Line-pairing fallback for PDFs without usable coordinates."""
    try:
        import pymupdf
        doc = pymupdf.open(str(pdf))
    except Exception:                                  # noqa: BLE001
        return []
    entries: list[dict] = []
    for pno in range(min(limit, len(doc))):
        text = sanitize(doc[pno].get_text())
        if not re.search(r"(?mi)^\s*(?:contents|目\s*录|目\s*錄|目\s*次)\s*$", text):
            continue
        lines = [re.sub(r"[ \t]+", " ", l).strip() for l in text.splitlines()]
        lines = [l for l in lines if l]
        buf: list[str] = []
        chapter = False
        for line in lines:
            if re.fullmatch(r"(?i)(?:contents|目\s*录|目\s*錄|目\s*次)", line):
                continue
            if re.fullmatch(r"(?i)page\s+[ivxlcdm]+", line):
                continue
            m = re.fullmatch(r"(\d{1,3}|[ivxlcdm]{1,7})", line, re.I)
            if m:
                if buf:
                    entries.append({"title": " ".join(buf), "page": m.group(1).lower(),
                                    "chapter": chapter, "level": 1 if chapter else 2})
                    buf, chapter = [], False
                elif m.group(1).isdigit() and 0 < int(m.group(1)) <= 60:
                    chapter = True         # a bare chapter number precedes its title
                continue
            dm = re.match(r"^(.*?)[.…·]{3,}\s*(\d{1,4}|[ivxlcdm]{1,7})\s*$", line, re.I)
            if dm and len(dm.group(1).strip()) > 2:
                if buf:
                    entries.append({"title": " ".join(buf), "page": None,
                                    "chapter": chapter, "level": 1 if chapter else 2})
                    buf, chapter = [], False
                entries.append({"title": dm.group(1).strip(),
                                "page": dm.group(2).lower(),
                                "chapter": bool(re.match(r"^\d{1,3}\s", dm.group(1))),
                                "level": 1})
                continue
            buf.append(line)
        if entries:
            break
    return entries


def _list_page_indexes(pdf, limit: int = 30) -> list[int]:
    """PDF pages that are printed contents / list-of-figures pages.

    Their text is a listing of titles and page numbers; translating it produces
    pages of nonsense, so those pages are dropped from the body.
    """
    try:
        import pymupdf
        doc = pymupdf.open(str(pdf))
    except Exception:                                  # noqa: BLE001
        return _list_page_indexes_text(pdf, limit)
    return _toc_pages(doc, limit)


def _list_page_indexes_text(pdf, limit: int = 30) -> list[int]:
    try:
        import pymupdf
        doc = pymupdf.open(str(pdf))
    except Exception:                                  # noqa: BLE001
        return []
    out = []
    for i in range(min(limit, len(doc))):
        text = sanitize(doc[i].get_text())
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        head = any(LIST_HEAD.match(l) for l in lines[:3])
        if not head or len(lines) < 4:
            continue
        # listing lines carry no sentence punctuation; prose pages are full of it
        periods = sum(1 for l in lines if l.endswith((".", "。", "!", "?")))
        if periods <= max(1, len(lines) * 0.15):
            out.append(i)
    return out


def _page_near(page, target) -> bool:
    a, b = str(page or ""), str(target or "")
    if not a or not b:
        return False
    if a.isdigit() and b.isdigit():
        return abs(int(a) - int(b)) <= 1
    return bool(ROMAN.fullmatch(a) and ROMAN.fullmatch(b) and a == b)


def _strip_num(t: str) -> str:
    return re.sub(r"^\d{1,2}[.)]?\s+", "", (t or "").strip())


def _apply_contents(blocks, pdf):
    """Promote blocks that match a printed Contents entry to h1/h2."""
    entries = _parse_contents(pdf)
    if not entries:
        return blocks
    for e in entries:
        title = _strip_num(e["title"])
        if re.fullmatch(r"(?i)contents", title):
            continue
        alt = re.sub(r"(?i)^(?:list of|table of|index of)\s+", "", title)
        keys = {_norm_key(title), _norm_key(alt)}
        keys = {k for k in keys if len(k) >= 6}
        if not keys:
            continue
        want_h1 = (e.get("level", 1) == 1 or bool(e.get("chapter"))
                   or bool(CN_KEY.match(title)))
        for b in blocks:
            if b.get("type") not in ("p", "h2", "h1", "note"):
                continue
            if e.get("page") and not _page_near(b.get("page"), e["page"]):
                continue
            if e.get("part") and not re.match(r"(?i)^part\b", b["text"].strip()):
                continue
            bk = _norm_key(_strip_num(b["text"]))
            if len(bk) < 6 or len(bk) > 300:
                continue
            hit = False
            for key in keys:
                head = key[:48]
                if bk.startswith(head) or (head.startswith(bk) and len(bk) >= 12):
                    hit = True
                    break
            if not hit:
                continue
            if len(b.get("text", "")) > 180 or len(b["text"].split()) > 26:
                break                      # ran into body text, not a heading
            b["type"] = "h1" if want_h1 else "h2"
            b["toc_page"] = e["page"]
            break
    return _dedupe_headings(blocks)


def _dedupe_headings(blocks):
    """Front matter often repeats the title-page line in two columns."""
    seen, out = set(), []
    for b in blocks:
        if b["type"] in ("h1", "h2"):
            k = (b["type"], _norm_key(b["text"]), str(b.get("page")))
            if k in seen and _norm_key(b["text"]):
                continue
            seen.add(k)
        out.append(b)
    return out


def _drop_repeated_headings(blocks, min_pages: int = 3):
    """Vision transcription repeats the running head on every page: keep its first
    appearance (the section opener) and drop the rest."""
    from collections import Counter
    pages: dict[str, set] = {}
    for b in blocks:
        if b["type"] in ("h1", "h2") and len(b["text"]) <= 90:
            pages.setdefault(_norm_key(b["text"]), set()).add(str(b.get("page")))
    heads = {k for k, v in pages.items() if len(v) >= min_pages}
    if not heads:
        return blocks
    kept, out = set(), []
    for b in blocks:
        if b["type"] in ("h1", "h2"):
            k = _norm_key(b["text"])
            if k in heads:
                if k in kept:
                    continue
                kept.add(k)
        out.append(b)
    return out
