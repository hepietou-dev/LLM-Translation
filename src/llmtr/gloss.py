"""Source-term glosses: how much English survives inside the Chinese text."""
from __future__ import annotations

import re

MODES = ("off", "light", "strong")
DEFAULT_MODE = "strong"
LABELS = {"off": "关闭", "light": "适度", "strong": "较多（推荐）"}

CJK = re.compile(r"[\u4e00-\u9fff]")
TITLE_GLOSS = re.compile(
    r"《([^》\n]{1,80})》\s*[（(]\s*([A-Za-z][^）)\n]{0,120}?)\s*[）)]")

SYSTEM_HEAD = (
    "你是资深学术译者，为出版社翻译英文学术专著。译文要准确、克制、有书卷气，"
    "读起来像中文作者写的，而不是英文句子的逐字替换。\n"
    "格式：\n"
    "1) 每行以编号开头（如 [b0007]），译文保留同一编号，一行对应一段，不得合并、拆分、增删；\n"
    "2) 只输出“编号 + 空格 + 译文”，不要解释、不要 Markdown、不要空行；\n"
    "3) 注释行只译说明性文字；所引文献的作者、书名/篇名、期刊、出版社、年份、页码一律照抄原文，不译；"
    "注释叙述中出现的学者或历史人物（并非所引文献作者）仍按第5条译名并括注原文；ibid.=同上，op. cit.=前引书；\n"
    "4) 忠实原意与限定强度（may/suggests/to some extent 如实译），不概括、不改写；"
    "必须完整覆盖原文每一句，不得漏译任何分句；\n"
)

SYSTEM_MID = (
    "7) ⟦数字⟧ 是原文的注释号，必须原样保留在对应位置，不得删除、改写或翻译；"
    "扫描件上标残符（1° !™ ”° 等）直接删除。\n"
    "汉语表达：\n"
    "8) 按中文习惯重组语序：英文长句可以拆成几个短句，几个短句也可以合并；"
    "不要把 when/if/which/that 结构硬套成“当……的时候/如果……/其/该”；\n"
    "9) 少用“被”字句，少用“进行/作出/给予/加以”这类空动词，少用“一个/一种”凑量词，避免“的”字连缀；\n"
)

SYSTEM_TAIL = (
    "11) 段内指代要清楚：it/they/this 等按上下文还原为具体对象，不用一连串“它/这”。\n"
)

RULES = {
    "off": (
        "5) 术语按词表；人名一律译成中文，不加英文原文括号；\n",
        "6) 原文书名用《》，不附英文原名；保留 *斜体* 标记；数字、年份、页码照原文；\n",
        "10) 学术概念用学界通行译法；没有把握的术语也要译成中文；不口语化，"
        "不添加原文没有的评论、连接词或语气；除所引文献、拉丁/希腊文引文外，"
        "正文不得夹带英文单词——dramatic、successive、counterparts 这类普通词必须译出；\n",
    ),
    "light": (
        "5) 术语按词表；人名首次出现加原文括号，如 苏加诺（Sukarno）；\n",
        "6) 原文书名首次出现译为《中文书名》（English Title），再次出现只写《中文书名》；"
        "保留 *斜体* 标记；数字、年份、页码照原文；\n",
        "10) 学术概念用学界通行译法；没有把握的术语保留英文并首次括注；不口语化，"
        "不添加原文没有的评论、连接词或语气；除专名、书名、首次出现的术语及拉丁/希腊文引文外，"
        "正文不得夹带英文单词——dramatic、successive、counterparts 这类普通词必须译出；\n",
    ),
    "strong": (
        "5) 术语按词表；人名首次出现加原文括号（如 苏加诺（Sukarno）），每章重新计算首次；"
        "机构、地名、条约、法案、政党、军队、出版社等专名首次出现时也按“中文（English）”括注；\n",
        "6) 原文书名首次出现译为《中文书名》（English Title），每章第一次出现都括注原文，"
        "之后只写中文书名；保留 *斜体* 标记；数字、年份、页码照原文；\n",
        "10) 学术概念用学界通行译法；关键术语、核心概念首次出现时，即使已译成中文，"
        "也用“中文（English）”括注原文；宁可多保留书名、专名和术语的英文原词，不要漏注；"
        "不口语化，不添加原文没有的评论、连接词或语气；除专名、书名、术语、拉丁/希腊文引文外，"
        "正文不得夹带英文单词——dramatic、successive、counterparts 这类普通词必须译出；\n",
    ),
}


def mode_of(cfg: dict | None) -> str:
    raw = (cfg or {}).get("source_gloss", DEFAULT_MODE)
    if isinstance(raw, dict):
        raw = raw.get("mode", DEFAULT_MODE)
    return raw if raw in MODES else DEFAULT_MODE


def system_prompt(mode: str) -> str:
    mode = mode if mode in MODES else DEFAULT_MODE
    rule5, rule6, rule10 = RULES[mode]
    return SYSTEM_HEAD + rule5 + rule6 + SYSTEM_MID + rule10 + SYSTEM_TAIL


def _limit(cfg: dict, mode: str) -> int:
    raw = (cfg or {}).get("source_gloss", {})
    default = 8 if mode == "strong" else 3
    try:
        return max(0, int(raw.get("max_per_block", default))) if isinstance(raw, dict) else default
    except (TypeError, ValueError):
        return default


def _term_in(term: str, text: str) -> bool:
    if not term or not text:
        return False
    if re.fullmatch(r"[\x00-\x7f]+", term):
        return re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(term),
                         text, re.I) is not None
    return term in text


def _paren_spans(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in re.finditer(r"（[^（）]*）|\([^()]*\)", text)]


def _inside(spans: list[tuple[int, int]], pos: int) -> bool:
    return any(a <= pos < b for a, b in spans)


def _after_gloss(text: str, pos: int, en: str) -> bool:
    """A parenthetical right after a term already carries this English word."""
    m = re.match(r"\s*[（(]([^（）()]{0,60})[）)]", text[pos:])
    return bool(m and re.search(r"(?<![A-Za-z])%s(?![A-Za-z])" % re.escape(en),
                                m.group(1), re.I))


def _plain_hits(zh: str, term: str) -> list[re.Match]:
    spans = _paren_spans(zh)
    return [m for m in re.finditer(re.escape(term), zh) if not _inside(spans, m.start())]


def _glossed(zh: str, term: str, en: str) -> bool:
    """True when a plain occurrence already carries '（en）', here or after 《…》."""
    for m in _plain_hits(zh, term):
        if _after_gloss(zh, m.end(), en):
            return True
        op = zh.rfind("《", 0, m.start())
        cl = zh.find("》", m.start())
        if op != -1 and cl != -1 and op < m.start() < cl and _after_gloss(zh, cl + 1, en):
            return True
    return False


def _insert_gloss(zh: str, term: str, en: str) -> str:
    """Add '（en）' after the first plain occurrence, never inside another gloss."""
    if not term or not en or en.lower() == term.lower():
        return zh
    for m in _plain_hits(zh, term):
        # A title inside 《…》 takes its gloss after the closing bracket.
        op = zh.rfind("《", 0, m.start())
        cl = zh.find("》", m.start())
        if op != -1 and cl != -1 and op < m.start() < cl:
            return zh[:cl + 1] + "（%s）" % en + zh[cl + 1:]
        return zh[:m.end()] + "（%s）" % en + zh[m.end():]
    return zh


def apply_terms(blocks: list[dict], glossary: dict[str, str], cfg: dict | None) -> int:
    mode = mode_of(cfg)
    if mode == "off" or not glossary:
        return 0
    limit = _limit(cfg, mode)
    if limit == 0:
        return 0
    seen: set[str] = set()
    count = 0
    order = sorted(glossary, key=len, reverse=True)
    for b in blocks:
        if mode == "strong" and b.get("type") == "h1":
            seen.clear()
        if b.get("type") in ("bib", "table", "figure"):
            continue
        src, zh = b.get("text") or "", b.get("zh") or ""
        if not zh or not src:
            continue
        used = 0
        for en in order:
            zn = (glossary.get(en) or "").strip()
            # A one-character Chinese term matches inside unrelated words (金/金融).
            if en in seen or len(zn) < 2 or not CJK.search(zn):
                continue
            if not _term_in(en, src) or zn not in zh:
                continue
            if _glossed(zh, zn, en):
                seen.add(en)          # already glossed, possibly by the translator
                continue
            new = _insert_gloss(zh, zn, en)
            if new == zh:
                continue              # every occurrence sat inside a gloss: try later
            seen.add(en)
            zh = new
            count += 1
            used += 1
            if used >= limit:
                break
        b["zh"] = zh
    return count


def dedupe_titles(blocks: list[dict], cfg: dict | None) -> int:
    """Keep one English title gloss per scope; off strips all of them."""
    mode = mode_of(cfg)
    seen: set[str] = set()
    changed = 0

    def repl(m):
        nonlocal changed
        zh, en = m.group(1), m.group(2).strip()
        key = zh.strip()
        if mode == "off" or key in seen:
            changed += 1
            return "《%s》" % zh
        seen.add(key)
        return "《%s》（%s）" % (zh, en)

    for b in blocks:
        if mode == "strong" and b.get("type") == "h1":
            seen.clear()
        zh = b.get("zh") or ""
        if "《" not in zh or "（" not in zh and "(" not in zh:
            continue
        b["zh"] = TITLE_GLOSS.sub(repl, zh)
    return changed


def apply(blocks: list[dict], glossary: dict[str, str], cfg: dict | None) -> dict:
    mode = mode_of(cfg)
    terms = apply_terms(blocks, glossary, cfg)
    titles = dedupe_titles(blocks, cfg)
    return {"mode": mode, "terms": terms, "titles": titles}
