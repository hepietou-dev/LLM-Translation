"""Book metadata: title / author / publisher, from PDF info, first pages and the model."""
from __future__ import annotations

import json
import re

FIELDS = ("title", "subtitle", "author", "publisher", "year")

SYSTEM = ("你是书目信息抽取器。只输出一行 JSON，键名固定为 "
          "title, subtitle, author, publisher, year；未知字段用空字符串。")

PROMPT = """从下面这几页书页文字中提取书目信息：
- title：主标题（不含副标题、不含“Contents”等）
- subtitle：副标题
- author：本书作者；若是 Edited by 的编著，填编者姓名（多人用分号分隔）。
  不要填丛书主编（Series Editor）、译者、作序者或机构名
- publisher：出版社
- year：出版年

文字：
%s"""

ROLE = re.compile(r"(?i)\b(series editor|general editor|editor|edited by|translated by|"
                  r"foreword|introduction by|contributors)\b")
AFFIL = re.compile(r"(?i)\b(universit|college|institute|department|school|academy|"
                   r"press|publishing|copyright|rights reserved)\b")
_NOT_NAME = {"east", "west", "north", "south", "asia", "europe", "africa", "america",
             "china", "japan", "korea", "modern", "early", "late", "history", "politics",
             "political", "global", "world", "new", "old", "great", "making", "borders",
             "state", "states", "empire", "empires", "war", "peace", "revolution", "rise"}


def _untrack(s: str) -> str:
    """Repair OCR letter-spacing such as 'R i s e o f t h e'."""
    toks = s.split()
    if len(toks) >= 6 and sum(1 for t in toks if len(t) == 1) > 0.6 * len(toks):
        return "".join(toks)
    return s


def _title_line(lines: list[str]) -> str:
    """Join a wrapped title, but stop before a subtitle, author or imprint line."""
    cand = [l for l in lines[:12] if 8 < len(l) < 130
            and not AFFIL.search(l)
            and not re.search(r"(ISBN|DOI|www\.|©|All rights)", l)
            and not (l.isupper() and len(l) > 30)]
    if not cand:
        return ""
    title = _untrack(cand[0])
    if re.search(r"[.,:;!?–—-]\s*$", title):
        return title
    for nxt in cand[1:]:
        nxt = _untrack(nxt)
        if _people(nxt) or len(title.split()) >= 8:
            break
        if len(nxt.split()) <= 5 and not re.search(r"[,\d]", nxt):
            title += " " + nxt
            if re.search(r"[.,:;!?–—-]\s*$", nxt):
                break
            continue
        break
    return title


def _people(s: str) -> list[str]:
    """Person names from a line such as 'Cary J. Nederman, University of Arizona, USA'."""
    s = re.sub(r"\([^)]*\)", "", s)
    out = []
    for chunk in re.split(r",|\band\b|&", s):
        c = chunk.strip(" .;")
        c = re.split(r"\b(?:University|Universit|College|School|Institute|Professor|"
                     r"Department|Faculty|Chair|Academy)\b", c)[0]
        c = c.strip(" ,.")
        if not c or c.isupper():
            continue
        if any(len(t) > 14 for t in re.findall(r"[A-Za-z]+", c)):
            continue                      # glued OCR line, not a person
        if any(w in _NOT_NAME for w in re.findall(r"[a-z]+", c.lower())):
            continue
        if re.match(r"^[A-Z][\w'\-\.]*(?:\s+(?:[A-Z]\.|[A-Z][\w'\-\.]+)){1,3}$", c):
            out.append(c)
    return out


def _clip(s: str, n: int = 120) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    if len(s) <= n:
        return s
    cut = s[:n]
    return cut[:cut.rfind(" ")] if " " in cut else cut


def _heuristic(text: str) -> dict:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out = {k: "" for k in FIELDS}
    for l in lines[:20]:
        if re.search(r"(University Press|Press|Publishing(?: Limited)?)$", l) and not out["publisher"]:
            out["publisher"] = l[:80]

    # A title subtitle ("1881–1919") must not become the publication year.
    for pat in (r"©[^\n]*?((?:19|20)\d{2})",
                r"(?:First published|Published)\D{0,12}((?:19|20)\d{2})"):
        m = re.search(pat, text)
        if m:
            out["year"] = m.group(1)
            break
    if not out["year"]:
        for l in lines[:20]:
            if re.fullmatch(r"\s*(?:19|20)\d{2}\s*", l):
                out["year"] = l.strip()
                break
    if not out["year"]:
        for l in lines[:20]:
            m = re.search(r"(?<![–-])(?:19|20)\d{2}(?![–-])", l)
            if m:
                out["year"] = m.group(0)
                break

    cand = [l for l in lines[:10] if 12 < len(l) < 120
            and not AFFIL.search(l)
            and not re.search(r"(ISBN|DOI|www\.|Press|All rights reserved|©|No part of)",
                              l)]
    title = _title_line(lines)
    if not title and cand:
        title = max(cand[:4], key=len)
    if title:
        out["title"] = title

    m = re.search(r"(?im)^\s*edited by\s+(.+)$", text)
    if m:
        names = _people(m.group(1))
        if names:
            out["author"] = " and ".join(names[:4])
    if not out["author"]:
        m = re.search(r"©\s*([^\n]+?)\s+(?:19|20)\d{2}", text)
        if m:
            names = _people(m.group(1))
            if names:
                out["author"] = " and ".join(names[:4])
    if not out["author"]:
        for l in lines[:14]:
            if ROLE.search(l) or re.search(r"(?i)univers|press|school|college|institut", l):
                continue
            if not l[:1].isupper():
                continue
            names = _people(l)
            if names:
                out["author"] = " and ".join(names[:4])
                break
    return out


def extract(pdf, client=None, pages=None, log=print, stop=None) -> dict:
    from .translate import StopRequested
    text = ""
    try:
        from pypdf import PdfReader
        rd = PdfReader(str(pdf))
        info = rd.metadata or {}
        meta = {k: "" for k in FIELDS}
        if info.get("/Title"):
            meta["title"] = str(info["/Title"]).strip()
        if info.get("/Author"):
            meta["author"] = str(info["/Author"]).strip()
        head = "\n".join((rd.pages[i].extract_text() or "") for i in range(min(6, len(rd.pages))))
        text = head[:6000]
    except Exception:
        meta = {k: "" for k in FIELDS}

    guess = _heuristic(text)
    for k in FIELDS:
        if not meta.get(k):
            meta[k] = guess.get(k, "")

    if client is not None and client.key and text:
        try:
            raw = client.chat(SYSTEM, PROMPT % text[:3500], stop=stop)
            i, j = raw.find("{"), raw.rfind("}")
            if i >= 0 and j > i:
                data = json.loads(raw[i:j + 1])
                for k in FIELDS:
                    v = str(data.get(k) or "").strip()
                    if v:
                        meta[k] = v
        except StopRequested:
            raise
        except Exception as exc:                        # noqa: BLE001
            log("metadata: 模型抽取失败（用启发式）：%s" % exc)

    meta["title"] = _clip(meta.get("title", ""))
    meta["author"] = _clip(meta.get("author", ""))
    meta["publisher"] = _clip(meta.get("publisher", ""))
    return meta


def translate_fields(client, meta: dict, stop=None) -> dict:
    """Chinese title/author/publisher for the cover page."""
    from .translate import StopRequested
    if client is None or not client.key:
        return meta
    asks = []
    keys = []
    for k in ("title", "subtitle", "author", "publisher"):
        if meta.get(k):
            keys.append(k)
            asks.append("%s: %s" % (k, meta[k]))
    if not asks:
        return meta
    try:
        raw = client.chat(
            "你是出版编辑。把下面书目信息译成规范中文，逐行输出“键: 译文”，不要解释。",
            "\n".join(asks), stop=stop)
        alias = {"title": "title", "书名": "title", "标题": "title",
                 "subtitle": "subtitle", "副标题": "subtitle",
                 "author": "author", "作者": "author",
                 "publisher": "publisher", "出版社": "publisher"}
        for line in raw.splitlines():
            m = re.match(r"\s*([A-Za-z]+|书名|标题|副标题|作者|出版社)\s*[:：]\s*(.+)", line)
            if not m:
                continue
            key = alias.get(m.group(1).strip().lower()) or alias.get(m.group(1).strip())
            if key and m.group(2).strip():
                meta["zh_" + key] = m.group(2).strip().strip('"“”')[:120]
    except StopRequested:
        raise
    except Exception:
        pass
    return meta
