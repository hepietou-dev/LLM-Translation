"""EPUB -> block IR.

The EPUBs we accept are page-image conversions: every printed page is an XHTML
document whose layout lives in nested tables, body text sits in ``<font size="3">``
cells, headings in size 4/5 and page labels in red size 2.  The PDF pipeline reads
words and font sizes from a content stream; here the same signals come from the
markup, so the IR (page labels, h1/h2/p/note/bib, figures) stays identical.
"""
from __future__ import annotations

import html
import posixpath
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path

from .ingest import NOTE_MARK_CLOSE, NOTE_MARK_OPEN

LABEL = re.compile(r"^page\s+([0-9]{1,4}|[ivxlcdm]{1,7})$", re.I)
NUM_NOTE = re.compile(r"^\d{1,3}[.)]\s+")
TOC_DOT = re.compile(r"(?:\.{4,}|\u2026{3,})\s*(?:\d{1,4}|[ivxlcdm]{1,7})\s*$", re.I)
END_PUNCT = re.compile(r"[.!?:;\u201d\u2019\"')\]]\s*$")
PART_HEAD = re.compile(r"^(?:part|book)\b", re.I)
CHAPTER_NUM = re.compile(r"^(?:[IVXLCDM]{1,7}|\d{1,2})[\s.:]+\S")
MATTER = re.compile(
    r"(?i)^(?:introduction|preface|foreword|prologue|epilogue|conclusion|afterword|"
    r"acknowledg(?:e)?ments?|contents|table of contents|list of (?:figures|maps|tables|"
    r"illustrations|plates)|reference notes|notes|endnotes|bibliography|references|"
    r"works cited|index|appendi(?:x|ces)|about the (?:editors?|authors?)|contributors)")
CONTENTS = re.compile(r"^(?:contents|table of contents|目\s*录)$", re.I)
LEGAL = re.compile(r"(?i)(ISBN|DOI|www\.|All rights reserved|Printed in|Copyright|\u00a9)")
HEAD_SIZE = ("4", "5", "6", "7")
BLOCK_TAG = ("p", "div", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6")
IMG_OK_MIN = (150, 50)


class _PageParser(HTMLParser):
    """Collect styled text runs grouped by table cell (or block element)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.groups: list[list[list]] = []
        self.images: list[dict] = []
        self._cells: list[list[list]] = []
        self._loose: list[list] = []
        self._fonts: list[tuple] = []
        self._skip = 0

    def _add(self, s: str) -> None:
        if self._skip or not s:
            return
        key = self._fonts[-1] if self._fonts else (None, None)
        target = self._cells[-1] if self._cells else self._loose
        if target and target[-1][0] == key:
            target[-1][1] += s
        else:
            target.append([key, s])

    def _flush_loose(self) -> None:
        if self._loose and not self._cells:
            self.groups.append(self._loose)
            self._loose = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        a = dict(attrs)
        if tag in ("head", "script", "style"):
            self._skip += 1
        elif tag in ("td", "th"):
            self._cells.append([])
        elif tag == "font":
            self._fonts.append((a.get("size"), (a.get("color") or "").upper() or None))
        elif tag == "sup":
            self._add(NOTE_MARK_OPEN)
        elif tag == "br":
            self._add(" ")
        elif tag == "img":
            self.images.append({"src": a.get("src") or "", "w": a.get("width"),
                                "h": a.get("height")})
        elif tag in BLOCK_TAG and not self._cells:
            self._flush_loose()

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("head", "script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("td", "th"):
            if self._cells:
                group = self._cells.pop()
                if group:
                    self.groups.append(group)
        elif tag == "font":
            if self._fonts:
                self._fonts.pop()
        elif tag == "sup":
            self._add(NOTE_MARK_CLOSE)
        elif tag in BLOCK_TAG and not self._cells:
            self._flush_loose()

    def handle_data(self, data):
        self._add(data)

    def finish(self) -> None:
        while self._cells:
            group = self._cells.pop()
            if group:
                self.groups.append(group)
        self._flush_loose()


def _segments(parser: _PageParser) -> list[dict]:
    """Text runs per cell -> [(dominant font size, text)] plus page labels."""
    segs = []
    for group in parser.groups:
        text = "".join(t for _, t in group)
        text = re.sub(r"\s+", " ", html.unescape(text).replace("\xa0", " ")).strip()
        if not text:
            continue
        counts: dict[str, int] = {}
        red = False
        for (size, color), t in group:
            n = len(re.sub(r"\s", "", t))
            if not n:
                continue
            if size in HEAD_SIZE or size in ("1", "2", "3"):
                counts[size] = counts.get(size, 0) + n
            if color == "#FF0000":
                red = True
        m = LABEL.match(text)
        if red and m:
            segs.append({"label": m.group(1).lower()})
            continue
        size = max(counts, key=counts.get) if counts else None
        segs.append({"size": size, "text": text})
    return segs


def _label_of(doc: str) -> str:
    stem = Path(doc).stem
    return stem[5:] if stem.lower().startswith("page_") else stem


def _opf_path(z: zipfile.ZipFile) -> str:
    try:
        s = z.read("META-INF/container.xml").decode("utf-8", "replace")
        m = re.search(r'full-path="([^"]+)"', s)
        if m:
            return m.group(1)
    except KeyError:
        pass
    for name in z.namelist():
        if name.lower().endswith(".opf"):
            return name
    raise ValueError("EPUB 缺少 content.opf")


def _spine(z: zipfile.ZipFile, opf: str) -> list[str]:
    data = z.read(opf).decode("utf-8", "replace")
    manifest = {}
    for m in re.finditer(r"<item\b([^>]*)/?>", data, re.I):
        a = m.group(1)
        mid = re.search(r'\bid="([^"]+)"', a)
        href = re.search(r'\bhref="([^"]+)"', a)
        if mid and href:
            manifest[mid.group(1)] = html.unescape(href.group(1))
    base = posixpath.dirname(opf)
    names = {n.lower(): n for n in z.namelist()}
    docs = []
    for m in re.finditer(r"<itemref\b([^>]*)>", data, re.I):
        mid = re.search(r'\bidref="([^"]+)"', m.group(1))
        if not mid or mid.group(1) not in manifest:
            continue
        href = manifest[mid.group(1)]
        path = posixpath.normpath(posixpath.join(base, href.lstrip("./")))
        docs.append(names.get(path.lower(), path))
    if not docs:
        docs = sorted(n for n in z.namelist() if re.search(r"page[_-].*\.x?html?$", n, re.I))
    return docs


def _resolve(doc: str, src: str) -> str:
    return posixpath.normpath(posixpath.join(posixpath.dirname(doc), src))


def _image_ok(im: dict) -> bool:
    def num(v):
        try:
            return int(float(str(v).strip().lower().rstrip("px")))
        except (TypeError, ValueError):
            return 0

    w, h = num(im.get("w")), num(im.get("h"))
    if (im.get("w") is not None and w == 0) or (im.get("h") is not None and h == 0):
        return False                       # spacer pixel / table filler
    if w and h:
        return (w >= IMG_OK_MIN[0] and h >= IMG_OK_MIN[1]) or \
               (h >= IMG_OK_MIN[0] and w >= IMG_OK_MIN[1])
    return True


def _cover_meta(z: zipfile.ZipFile, opf: str) -> dict:
    info = {"title": "", "subtitle": "", "author": "", "publisher": "", "year": ""}
    try:
        s = z.read(opf).decode("utf-8", "replace")
    except KeyError:
        s = ""
    for key, tag in (("title", "title"), ("author", "creator"), ("publisher", "publisher")):
        m = re.search(r"<dc:%s[^>]*>(.*?)</dc:%s>" % (tag, tag), s, re.S | re.I)
        if not m:
            continue
        val = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
        if val and val.lower() != "test":
            info[key] = val
    for name in z.namelist():
        if not re.search(r"(^|/)cover\.(?:x?html?|xml)$", name, re.I):
            continue
        data = z.read(name).decode("utf-8", "replace")
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", data, re.S | re.I):
            cells = [html.unescape(re.sub(r"<[^>]+>", " ", c))
                     for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)]
            vals = [re.sub(r"\s+", " ", c).strip() for c in cells]
            vals = [v for v in vals if v and v != ":"]
            if len(vals) < 2:
                continue
            key = vals[0].lower()
            val = " ".join(vals[1:]).strip()
            if key == "title" and not info["title"]:
                info["title"] = val
            elif key == "author" and not info["author"]:
                info["author"] = val
            elif key == "publisher" and not info["publisher"]:
                info["publisher"] = val
            elif key == "publication date" and not info["year"]:
                info["year"] = val
        break
    title = info["title"]
    m = re.match(r"^(.+?)\s*[:：]\s+(.+)$", title)
    if m and len(m.group(1)) > 3:
        info["title"], info["subtitle"] = m.group(1).strip(), m.group(2).strip()
    author = info["author"].strip().rstrip(".")
    if author.count(",") == 1 and not re.search(r"\d", author):
        last, first = (x.strip() for x in author.split(","))
        if last and first:
            author = "%s %s" % (first, last)
    info["author"] = author
    m = re.search(r"(?:19|20)\d{2}", info.get("year", ""))
    info["year"] = m.group(0) if m else info.get("year", "")
    return info


def _heading_type(text: str, has_parts: bool, mode: str) -> str | None:
    t = text.strip()
    if CONTENTS.match(t):
        return None
    if PART_HEAD.match(t):
        return "h2" if mode in ("notes", "bib") else "h1"
    if MATTER.match(t):
        return "h1"
    if CHAPTER_NUM.match(t):
        return "h2" if has_parts else "h1"
    return "h1" if t.isupper() else "h2"


def _join_text(prev: dict, cur: dict) -> str | None:
    a = (prev.get("text") or "").rstrip()
    b = (cur.get("text") or "").lstrip()
    if not a or not b or len(a) + len(b) > 6000:
        return None
    if TOC_DOT.search(a) or TOC_DOT.search(b):
        return None
    if a.endswith("-") and (b[:1].islower() or a.endswith(" -")):
        return a[:-1].rstrip() + b
    if a[-1].isalpha() and (len(a) == 1 or a[-2] == " ") and b[:1].islower():
        return a + b                       # page broke in the middle of a word
    if b[:1].islower() and not END_PUNCT.search(a):
        return a + " " + b
    if prev["type"] == cur["type"] and prev["type"] in ("p", "note", "bib") \
            and 2 <= len(a) <= 12 and " " not in a and not END_PUNCT.search(a) \
            and a.isupper() and re.search(r"[A-Za-z]", a):
        return a + " " + b                 # "BPP" + its expansion
    if cur["type"] == "note" and len(a) <= 8 and " " not in a \
            and re.fullmatch(r"[A-Za-z]+\W?", a):
        return a + " " + b                 # abbreviation such as "Out."
    if cur["type"] in ("p", "note") and not END_PUNCT.search(a) \
            and str(prev.get("page")) != str(cur.get("page")):
        return a + " " + b                 # sentence carried over a page break
    if cur["type"] == "bib" and b.startswith(". "):
        return a + " " + b                 # same author, repeated with a leading dot
    return None


def _merge(blocks: list[dict]) -> list[dict]:
    out: list[dict] = []
    for b in blocks:
        prev = out[-1] if out else None
        if (prev and prev["type"] == b["type"] and b["type"] in ("p", "note", "bib")):
            joined = _join_text(prev, b)
            if joined is not None:
                prev["text"] = joined
                if str(prev.get("page")) != str(b.get("page")):
                    prev["page_end"] = b["page"]
                continue
        out.append(b)
    return out


def extract(path: Path, assets: Path | None = None, log=print) -> dict:
    path = Path(path)
    z = zipfile.ZipFile(path)
    opf = _opf_path(z)
    docs = _spine(z, opf)
    meta = _cover_meta(z, opf)

    pages = []
    for doc in docs:
        data = z.read(doc).decode("utf-8", "replace")
        parser = _PageParser()
        parser.feed(data)
        parser.finish()
        segs = _segments(parser)
        label = next((s["label"] for s in segs if "label" in s), None) or _label_of(doc)
        pages.append({"doc": doc, "label": label, "segs": segs, "images": parser.images})

    if not meta.get("title"):
        for pg in pages:
            hit = next((s["text"] for s in pg["segs"] if s.get("size") == "5"), "")
            if hit:
                meta["title"] = hit
                break

    has_parts = any(s.get("size") in HEAD_SIZE and PART_HEAD.match(s.get("text", ""))
                    for pg in pages for s in pg["segs"])

    blocks: list[dict] = []
    labels: list[str] = []
    page_map: dict[str, int] = {}
    seen_prose = False
    mode = "body"
    notes_open = False
    seen_img: set[str] = set()
    nimg = 0

    for idx, pg in enumerate(pages):
        label = pg["label"]
        labels.append(label)
        page_map.setdefault(label, idx)
        for im in pg["images"]:
            src = im.get("src") or ""
            if not src or src in seen_img or not _image_ok(im):
                continue
            try:
                blob = z.read(_resolve(pg["doc"], src))
            except KeyError:
                continue
            seen_img.add(src)
            if assets is not None:
                nimg += 1
                assets.mkdir(parents=True, exist_ok=True)
                out = assets / ("epub_%02d%s" % (nimg, Path(src).suffix.lower() or ".img"))
                out.write_bytes(blob)
                blocks.append({"page": label, "type": "figure", "text": "", "zh": "",
                               "image": str(out), "caption": ""})

        text_segs = [s for s in pg["segs"] if s.get("text")]
        if not text_segs:
            continue
        head = next((s["text"] for s in text_segs if s.get("size") in HEAD_SIZE), "")
        if head and CONTENTS.match(head):
            continue                       # the printed contents (we rebuild it)
        if not seen_prose:
            prose = any(s.get("size") not in HEAD_SIZE and len(s["text"]) >= 250
                        for s in text_segs)
            start = any(s.get("size") in HEAD_SIZE
                        and (MATTER.match(s["text"]) or PART_HEAD.match(s["text"])
                             or CHAPTER_NUM.match(s["text"]))
                        for s in text_segs)
            legal = any(LEGAL.search(s["text"]) for s in text_segs)
            if not start and (legal or not prose):
                continue                   # cover / title / copyright / contents
            seen_prose = True

        for s in text_segs:
            text = s["text"]
            size = s.get("size")
            marker = re.match(r"^\u27e6(\d{1,3})[.)]?\u27e7\s*", text)
            if marker:
                text = marker.group(1) + ". " + text[marker.end():]
            if size in HEAD_SIZE:
                kind = _heading_type(text, has_parts, mode)
                if kind is None:
                    continue
                endnote = False
                upper = text.upper()
                if kind == "h1":
                    notes_open = False
                    if re.match(r"^(?:REFERENCE\s+)?(?:END)?NOTES\b", upper):
                        mode, endnote = "notes", True
                    elif re.match(r"^(?:BIBLIOGRAPHY|REFERENCES|WORKS CITED|INDEX)\b", upper):
                        mode = "bib"
                    else:
                        mode = "body"
                elif mode == "notes":
                    endnote = True
                block = {"page": label, "type": kind, "text": text}
                if endnote:
                    block["endnote"] = True
                blocks.append(block)
                continue

            if mode == "bib":
                typ = "bib"
            elif mode == "notes":
                if re.match(r"^chapter\s+(?:[IVXLCDM]+|\d+)\b", text, re.I) \
                        and len(text) <= 120:
                    blocks.append({"page": label, "type": "h3", "text": text})
                    continue
                typ = "note"
            elif size == "2" and NUM_NOTE.match(text):
                if not notes_open:
                    blocks.append({"page": label, "type": "h2", "text": "NOTES",
                                   "zh": "", "endnote": True})
                    notes_open = True
                typ = "note"
            else:
                typ = "p"
            blocks.append({"page": label, "type": typ, "text": text})

    z.close()
    blocks = _merge(blocks)
    for n, b in enumerate(blocks):
        b["id"] = "b%04d" % n
    chars = [sum(len(s["text"]) for s in pg["segs"] if s.get("text")) for pg in pages]
    probe = {"pages": len(pages),
             "good_pages": sum(1 for c in chars if c >= 200),
             "ratio": round(sum(1 for c in chars if c >= 200) / max(1, len(chars)), 3),
             "median_chars": sorted(chars)[len(chars) // 2] if chars else 0,
             "has_text_layer": bool(blocks)}
    log("epub: %s · %d pages · %d blocks" % (path.name, len(pages), len(blocks)))
    return {"ok": bool(blocks), "probe": probe, "labels": labels, "blocks": blocks,
            "page_map": page_map, "meta": meta}
