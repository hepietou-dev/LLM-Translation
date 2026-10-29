"""Person names: candidates -> Chinese -> localise text -> name index."""
from __future__ import annotations

import collections
import re
from pathlib import Path

STOP = set("""The This That These Those There Their Then Than They Them Thus Though Through After
Before During Under Over Between Among Within Without Against About Above Below Since Until While
Where When Which What Whose Whom Why How However Moreover Furthermore Nevertheless Therefore Chapter
Part Volume See Ibid Press University Institute Department Government Army Forces Force Party Council
Committee Conference Report History Political Military Indonesian Indonesia Dutch Japan Netherlands
East Indies Java Sumatra Borneo Celebes Moluccas January February March April May June July August
September October November December General Colonel Major Lieutenant Captain Brigadier Commander
Chief Staff Minister President Prime""".split())

CJK = re.compile(r"[\u4e00-\u9fff]")

# Parenthetical glosses/citations and author-date citations keep their Latin
# form; translating a surname inside them produces "（Mark 沃伦，1989）".
YEAR = r"(?:1[5-9]\d{2}|20\d{2})[a-z]?"
AUTH = r"[A-Z][A-Za-z\u2019'.\-]*"
PAREN_EN = re.compile(r"[（(][^（）()]*[A-Za-z][^（）()]*[）)]")
CITE_SEQ = re.compile(
    r"%s(?:\s*,?\s*(?:and|&)\s+%s)*(?:\s+et\s+al\.?)?\s*,?\s*[（(]?\s*%s" % (AUTH, AUTH, YEAR))


def _shield(text: str) -> tuple[str, list[str]]:
    keep: list[str] = []

    def repl(m):
        keep.append(m.group(0))
        return "\u0004%d\u0005" % (len(keep) - 1)

    text = PAREN_EN.sub(repl, text)
    text = CITE_SEQ.sub(repl, text)
    return text, keep


def _unshield(text: str, keep: list[str]) -> str:
    for i, s in enumerate(keep):
        text = text.replace("\u0004%d\u0005" % i, s)
    return text


SURNAME = "姓"
GIVEN = "名"


def candidates(blocks: list[dict], limit: int = 150) -> list[str]:
    text = "\n".join(b["text"] for b in blocks if b["type"] in ("p", "note"))
    counts = collections.Counter(re.findall(r"\b[A-Z][a-z]{2,}\b", text))
    lower = collections.Counter(re.findall(r"\b[a-z]{3,}\b", text))
    out = []
    for w, c in counts.most_common(limit * 3):
        if w in STOP or c < 3:
            continue
        # `More than…` at a sentence start, `Green` as a colour: words whose
        # lowercase form dominates are ordinary vocabulary, not proper names.
        if lower.get(w.lower(), 0) > 3 * c:
            continue
        out.append(w)
    return out[:limit]


def cited_names(blocks: list[dict]) -> set[str]:
    """Tokens used in author-date citations or reference entries: definitely surnames."""
    text = "\n".join(b["text"] for b in blocks if b["type"] in ("p", "note", "bib"))
    pats = [r"(?<![A-Za-z])%s\s*\(?\d{4}[a-z]?", r"(?<![A-Za-z])%s,\s+[A-Z][a-z]+"]
    out = set()
    for w in set(re.findall(r"\b[A-Z][a-z]{2,}\b", text)):
        if any(re.search(p % re.escape(w), text) for p in pats):
            out.add(w)
    return out


def translate(client, cands: list[str]) -> dict[str, str]:
    return {en: r["zh"] for en, r in translate_records(client, cands).items()}


def translate_records(client, cands: list[str], stop=None) -> dict[str, dict]:
    prompt = ("下面是英文专著中的专名候选。逐条判断：人物姓氏或学界通称（family name），"
              "第三列写“姓”；只是名（given name，如 John、Mary），第三列写“名”；"
              "非人名（地名、机构、党名、书名、普通词、出版机构）中文列写 -。"
              "每行一条：英文<TAB>中文<TAB>姓|名，不要编号、不要解释。\n\n" + "\n".join(cands))
    out = {}
    for line in client.chat("你是资深学术译者。", prompt, stop=stop).splitlines():
        p = [x.strip() for x in line.split("\t")]
        if len(p) < 2:
            continue
        en, zh = p[0], p[1]
        if not zh or zh == "-" or not CJK.search(zh):
            continue
        kind = GIVEN if len(p) > 2 and p[2].startswith("名") else SURNAME
        out[en] = {"zh": zh.strip(" 。."), "kind": kind}
    return out


def load_tsv(path: Path) -> dict[str, str]:
    out = {}
    if path and path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            p = line.split("\t")
            if len(p) >= 2 and p[0] and p[1] and p[1] != "-" and not p[0].startswith("#"):
                out[p[0]] = p[1]
    return out


def load_records(path: Path) -> dict[str, dict]:
    out = {}
    if path and path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            p = line.split("\t")
            if len(p) >= 2 and p[0] and p[1] and p[1] != "-" and not p[0].startswith("#"):
                out[p[0]] = {"zh": p[1],
                             "kind": GIVEN if len(p) > 2 and p[2].startswith("名") else SURNAME}
    return out


def save_tsv(path: Path, pairs: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for k, v in sorted(pairs.items()):
        if isinstance(v, dict):
            zh, kind = v["zh"], GIVEN if v.get("kind") == GIVEN else SURNAME
        else:
            zh, kind = v, SURNAME
        lines.append("%s\t%s\t%s\t模型\t0" % (k, zh, kind))
    path.write_text("\n".join(lines), encoding="utf-8")


def _latin_adjacent(text: str, start: int, end: int) -> bool:
    """True when the match sits inside a Latin phrase (a title, a full name)."""
    before = text[max(0, start - 24):start]
    after = text[end:end + 24]
    if re.search(r"[A-Za-z][A-Za-z\u2019'.\-]*\s*$", before):
        return True
    if re.match(r"\s*[A-Za-z][A-Za-z\u2019'.\-]*", after):
        return True
    return False


def _sub_names(text: str, pat, en: str, zn: str, gloss: str | None) -> str:
    out, pos, used = [], 0, gloss is None
    for m in pat.finditer(text):
        out.append(text[pos:m.start()])
        if _latin_adjacent(text, m.start(), m.end()):
            out.append(m.group(0))
        else:
            out.append(zn if used else zn + "（" + en + "）")
            used = True
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def localise(blocks: list[dict], pairs: dict[str, str]) -> dict[str, set]:
    """Replace bare English names, keep exactly one '中文（English）' per name."""
    seen: set[str] = set()
    pages: dict[str, set] = collections.defaultdict(set)
    order = sorted(pairs, key=len, reverse=True)
    src_pat = {en: re.compile(r"(?<![A-Za-z])%s(?![A-Za-z])" % re.escape(en)) for en in order}
    name_pat = {en: re.compile(r"(?<![A-Za-z（(])%s(?:\u2019s|'s)?(?![A-Za-z）)])" % re.escape(en))
                for en in order}

    for b in blocks:
        src, zh = b.get("text", ""), b.get("zh", "")
        for en in order:
            if src_pat[en].search(src):
                pages[en].add(str(b["page"]))
        if not zh or b.get("type") == "bib":
            continue
        zh, keep = _shield(zh)
        for en in order:
            zn = pairs[en]
            if not CJK.search(zn):
                continue
            if any(("（%s）" % en) in s or ("(%s)" % en) in s for s in keep):
                seen.add(en)
                continue
            pat = name_pat[en]
            if not pat.search(zh):
                continue
            new = _sub_names(zh, pat, en, zn, None if en in seen else en)
            if new != zh:
                seen.add(en)
                zh = new
        b["zh"] = _unshield(zh, keep)

    # drop later duplicates of the parenthetical form
    kept: set[str] = set()
    for b in blocks:
        zh = b.get("zh", "")
        if not zh:
            continue
        for en in order:
            zn = pairs[en]
            forms = [zn + "（" + en + "）", zn + "(" + en + ")"]
            bare = ["（" + en + "）", "(" + en + ")"]
            if kept.__contains__(en):
                for f in forms:
                    zh = zh.replace(f, zn)
            elif any(f in zh for f in forms):
                kept.add(en)
                zh = zh.replace(forms[0], "\u0001").replace(forms[1], "\u0001")
            for f in bare:
                zh = zh.replace(f, "")
            zh = zh.replace("\u0001", forms[0])
        b["zh"] = zh
    return pages


def build_index(pairs: dict[str, str], pages: dict[str, set],
                given: set[str] | None = None) -> list[str]:
    """One entry per Chinese name, English variants merged, sorted by English."""
    given = given or set()
    merged: dict[str, dict] = {}
    for en, zh in pairs.items():
        if en in given or not pages.get(en):
            continue
        m = merged.setdefault(zh, {"en": set(), "pages": set()})
        m["en"].add(en)
        m["pages"] |= {int(p) for p in pages[en] if p.isdigit()}
    rows = []
    for zh, v in merged.items():
        ens = sorted(v["en"], key=len, reverse=True)
        label = ens[0] if len(ens) == 1 else "%s（%s）" % (ens[0], "、".join(ens[1:]))
        rows.append("%s\u3000%s\u3000%s" % (label, zh, ", ".join(str(p) for p in sorted(v["pages"]))))
    rows.sort(key=lambda s: s.lower())
    return rows
