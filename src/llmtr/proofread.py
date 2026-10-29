"""Proof-reading checks over a translated book (no API calls)."""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

CJK = re.compile(r"[\u4e00-\u9fff]")
DIGITS = re.compile(r"\d+")
LATIN_GLOSS = re.compile(r"（[^（）]*[A-Za-z][^（）]*）|\([^()]*[A-Za-z][^()]*\)|“[^”]*[A-Za-z][^”]*”")
LOWER_WORD = re.compile(r"(?<![A-Za-z])([a-z]{3,})(?![A-Za-z])(?![^\x00-\x7f])")
LOWER_RUN = re.compile(r"(?<![A-Za-z])[a-z]{2,}(?:\s+[a-z]+){2,}")
ASCII_TOKEN = re.compile(r"[A-Za-z][A-Za-z\u2019'\-]*")
URL = re.compile(r"(?:https?://|www\.)\S+|\S+@\S+")
TERM_STOP = {"ibid", "idem", "passim", "sic", "etc"}
# Function words occur in citations and journal titles, not as stray terms.
TERM_STOP |= {"and", "the", "for", "of", "in", "on", "to", "with", "from", "by",
              "or", "as", "at", "an", "a", "de", "la", "le", "der", "des", "du",
              "von", "van", "et", "und", "el", "los", "las"}
# Terms of art routinely kept in the original in Chinese scholarly prose.
FOREIGN_TERMS = {"scientia", "civilis", "fortuna", "doux", "commerce",
                 "polis", "politeia", "politeuma", "politeuesthai", "polites",
                 "politikos", "politicus", "politizare",
                 "respublica", "publica", "politia", "politiae", "democratia",
                 "aristocratia", "oligarchia", "tyrannia", "regia", "potestas",
                 "principatus", "civitas", "civitatis", "imperium", "signoria",
                 "signorie", "vivere", "civile", "politico", "bonum", "commune",
                 "patria", "patriae", "status", "forma", "species", "stasis",
                 "pragmata", "virtus", "bellica", "princeps", "antiquam",
                 "matrem", "orbis", "litteratus", "res", "publicae", "rei"}


def _plain(zh: str) -> str:
    return URL.sub(" ", LATIN_GLOSS.sub("", zh))


ENGLISH_FUNCTION = frozenset(
    "the of and to in that is was were for with on as by from this these those not "
    "are be been has have had it its or an a at which who what when where how but "
    "if then than also such may more most only".split())


def ascii_prose(zh: str) -> bool:
    """A run of three or more lower-case English words in running Chinese."""
    for m in LOWER_RUN.finditer(_plain(zh)):
        words = m.group(0).split()
        if not any(w in ENGLISH_FUNCTION for w in words):
            continue                       # Latin quotation, not leftover English
        if not all(w in TERM_STOP or w in FOREIGN_TERMS for w in words):
            return True
    return False


def suspect_terms(zh: str) -> list[str]:
    """Lower-case English words left in running Chinese (not in a gloss/title)."""
    body = _plain(zh)
    out = []
    for m in LOWER_WORD.finditer(body):
        w = m.group(1)
        if w in TERM_STOP or w in FOREIGN_TERMS:
            continue
        before, after = body[m.start() - 1:m.start()], body[m.end():m.end() + 1]
        if before in "、和及" or after in "、和及":     # enumerated catalogue
            continue
        lo = m.start()
        while lo and re.match(r"[A-Za-z0-9\u2019'\-\.,: ]", body[lo - 1]):
            lo -= 1
        hi = m.end()
        while hi < len(body) and re.match(r"[A-Za-z0-9\u2019'\-\.,: ]", body[hi]):
            hi += 1
        if any(t[:1].isupper() for t in ASCII_TOKEN.findall(body[lo:hi])):
            continue                                   # proper name / Latin title
        out.append(w)
    return out


def _cjk_ratio(s: str) -> float:
    letters = [c for c in s if not c.isspace()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if CJK.match(c)) / len(letters)


SRC_END = re.compile(r"[.!?][”’\")]?\s*$")
ZH_END = re.compile(r"[。！？；…”」』]\s*$")
ZH_STOP = re.compile(r"[。！？]")
DANGLING = re.compile(r"(?:的|和|与|或|及|而|但|把|被|以|从|对)$|"
                      r"(?:因为|如果|虽然|然而|并且|以及|即使)$")
ABBREV = re.compile(r"\b(?:e\.g|i\.e|cf|ibid|op\. cit|vol|pp?|no|ed|eds|trans|"
                    r"Mr|Mrs|Ms|Dr|St|Jr|Sr)\.", re.I)


def _sentences(t: str) -> int:
    t = ABBREV.sub(" ", t)
    t = re.sub(r"\b[A-Z]\.", " ", t)              # initials in names
    return max(1, len(re.findall(r"[.!?](?=\s|$)", t)))


def _truncated(src: str, zh: str) -> bool:
    """Source ends a sentence but the translation stops on a joining word."""
    if len(zh) < 30 or not SRC_END.search(src) or ZH_END.search(zh):
        return False
    return bool(DANGLING.search(zh.rstrip("，、, ")))


def _sent_gap(src: str, zh: str) -> bool:
    """Both sentence count and length fall far below the source paragraph."""
    ss = _sentences(src)
    zs = len(ZH_STOP.findall(zh))
    return (ss >= 6 and zs <= max(1, int(ss * 0.4))
            and len(zh) < 0.42 * len(src))


def coverage(blocks: list[dict]) -> dict:
    """Per-page translation coverage, so silent page drops cannot hide."""
    pages: dict[str, list[int]] = {}
    stitched = 0
    for b in blocks:
        if b.get("page_end") and str(b.get("page_end")) != str(b.get("page")):
            stitched += 1
        if b.get("type") in ("figure", "table"):
            continue
        p = str(b.get("page") or "").strip()
        if not p:
            continue
        slot = pages.setdefault(p, [0, 0])
        slot[0] += 1
        if (b.get("zh") or "").strip():
            slot[1] += 1
    empty = sorted((p for p, (n, k) in pages.items() if n and not k), key=_page_num)
    partial = sorted((p for p, (n, k) in pages.items() if n and 0 < k < n), key=_page_num)
    return {"pages": len(pages), "empty": empty, "partial": partial,
            "blocks": sum(n for n, _ in pages.values()),
            "done": sum(k for _, k in pages.values()), "stitched": stitched}


def _page_num(p: str):
    m = re.search(r"\d+", p)
    return (0, int(m.group(0))) if m else (1, 0)


def _cn_int(n: int) -> str:
    """Chinese numeral for 0-9999, enough to match translated years and counts."""
    cn = "零一二三四五六七八九"
    if n < 10:
        return cn[n]
    if n < 20:
        return "十" + (cn[n % 10] if n % 10 else "")
    if n < 100:
        return cn[n // 10] + "十" + (cn[n % 10] if n % 10 else "")
    if n < 1000:
        rest = n % 100
        tail = _cn_int(rest) if rest >= 10 else ("零" + cn[rest] if rest else "")
        return cn[n // 100] + "百" + tail
    if n < 10000:
        rest = n % 1000
        tail = _cn_int(rest) if rest >= 100 else ("零" + _cn_int(rest) if rest else "")
        return cn[n // 1000] + "千" + tail
    return str(n)


def _number_kept(d: str, zh: str) -> bool:
    """True when a source number survives as Arabic or Chinese numerals."""
    if d in zh:
        return True
    n = int(d)
    if _cn_int(n) in zh:
        return True
    if len(d) == 4 and d.endswith("0"):     # 1950 → 五十年代
        tens = int(d[2:])
        if 0 < tens < 100 and (_cn_int(tens) + "年代") in zh:
            return True
    return False


def check(book: dict) -> list[dict]:
    from .translate import _citation_only

    blocks = book["blocks"]
    zh_count = Counter(b.get("zh", "").strip() for b in blocks if b.get("zh", "").strip())
    issues = []
    for b in blocks:
        if b["type"] == "figure":
            continue
        src = (b.get("text") or "").strip()
        zh = (b.get("zh") or "").strip()
        if b["type"] in ("bib", "table"):
            continue
        if b["type"] == "note" and _citation_only(src):
            continue                            # kept verbatim on purpose
        if not zh or zh == src:
            issues.append(_issue(b, "未翻译", src, zh))
            continue
        if b["type"] in ("h1", "h2"):
            if len(zh) > 50:
                issues.append(_issue(b, "标题过长", src, zh))
            continue
        ratio = _cjk_ratio(zh)
        if len(src) > 60 and ratio < 0.2 and len(set(CJK.findall(zh))) < 8:
            issues.append(_issue(b, "中文过少", src, zh))
            continue
        if ascii_prose(zh):
            issues.append(_issue(b, "残留英文", src, zh))
            continue
        if suspect_terms(zh):
            issues.append(_issue(b, "未译术语", src, zh))
            continue
        sd, zd = DIGITS.findall(src), DIGITS.findall(zh)
        if len(sd) >= 2 and len(zd) == 0:
            missing = [d for d in sd if not _number_kept(d, zh)]
            if len(missing) == len(sd):
                issues.append(_issue(b, "数字不一致", src, zh))
                continue
        if len(src) > 150 and (len(zh) > 4 * len(src) or len(zh) < 0.12 * len(src)):
            issues.append(_issue(b, "篇幅异常", src, zh))
            continue
        if len(zh) > 30 and zh_count[zh] > 1:
            issues.append(_issue(b, "重复译文", src, zh))
    issues.extend(completeness(blocks))
    return issues


def completeness(blocks: list[dict]) -> list[dict]:
    """Paragraph-level checks that run independently of the wording checks.

    They are deliberately separate: a paragraph can contain an untranslated
    Latin term *and* be missing its last third, and the term check would
    otherwise mask the truncation.
    """
    issues = []
    for b in blocks:
        if b.get("type") != "p":
            continue
        src = (b.get("text") or "").strip()
        zh = (b.get("zh") or "").strip()
        if not zh or zh == src or len(src) < 150:
            continue
        if len(src) > 300 and len(zh) < 0.22 * len(src):
            issues.append(_issue(b, "译文偏短", src, zh))
            continue
        if _truncated(src, zh):
            issues.append(_issue(b, "疑似截断", src, zh))
            continue
        if _sent_gap(src, zh):
            issues.append(_issue(b, "疑似漏句", src, zh))
    return issues


def _issue(b: dict, reason: str, src: str, zh: str) -> dict:
    return {"id": b.get("id"), "page": b.get("page"), "type": b["type"],
            "reason": reason, "source": src, "zh": zh}


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(book: dict, path: Path) -> None:
    Path(path).write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")


def summary(issues: list[dict]) -> str:
    c = Counter(i["reason"] for i in issues)
    return "、".join("%s %d" % (k, v) for k, v in c.most_common()) or "无问题"

AI_SYSTEM = ("你是资深翻译审校。逐条比较英文原文与中文译文，只在确实存在漏译、误译、"
             "术语不一致、数字错误或语句不通时报告。若提示原文或译文为节选，"
             "不得仅因首尾不完整而判定增译或漏译。")

AI_PROMPT = """原文：
%s

译文：
%s

只输出一行 JSON：{"ok": true} 或 {"ok": false, "reason": "问题一句话", "fix": "修改后的译文"}"""


def ai_check(client, blocks, limit=40, min_len=40, progress=None, stop=None, workers=4):
    """LLM review of a sample of translated blocks (costs a few API calls)."""
    import json as _json

    from .translate import StopRequested, _parallel

    cands = [b for b in blocks
             if b.get("type") in ("p", "note") and len(b.get("text", "")) >= min_len
             and b.get("zh")]
    flagged = [b for b in cands if suspect_terms(b.get("zh") or "")]
    flagged_ids = {id(b) for b in flagged}
    rest = [b for b in cands if id(b) not in flagged_ids]
    slots = max(0, limit - len(flagged))
    if slots and rest:
        step = max(1, len(rest) // slots)
        rest = rest[::step][:slots]
    elif slots:
        rest = []
    cands = flagged[:limit] + rest
    issues = []

    def one(b: dict) -> None:
        src, zh = b["text"], b["zh"]
        note = ""
        if len(src) > 6000 or len(zh) > 6000:
            note = "（以下原文与译文均为节选，不得据首尾判断增译或漏译。）\n\n"
        try:
            raw = client.chat(AI_SYSTEM, note + AI_PROMPT % (src[:6000], zh[:6000]), stop=stop)
        except StopRequested:
            raise
        except Exception:
            return
        raw = raw.strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end < 0:
            return
        try:
            data = _json.loads(raw[start:end + 1])
        except Exception:
            return
        if data.get("ok"):
            return
        issues.append({"id": b.get("id"), "page": b.get("page"), "type": b["type"],
                       "reason": "AI:" + str(data.get("reason", "疑似问题"))[:80],
                       "source": b["text"], "zh": b["zh"],
                       "suggest": str(data.get("fix") or "")})

    _parallel([lambda b=b: one(b) for b in cands], workers, stop=stop, progress=progress)
    issues.sort(key=lambda it: str(it.get("id") or ""))
    return issues


def ai_fix(client, blocks, limit=40, min_len=40, progress=None, stop=None, workers=4):
    """One-click mode: ask the model for corrections and apply them.

    A "fix" that is much shorter than the current translation is a summary, not a
    correction — applying it silently deletes text, so it is rejected.
    """
    issues = ai_check(client, blocks, limit=limit, min_len=min_len,
                      progress=progress, stop=stop, workers=workers)
    by_id = {b.get("id"): b for b in blocks}
    fixed = 0
    for it in issues:
        fix = (it.get("suggest") or "").strip()
        b = by_id.get(it["id"])
        if not b or not fix or len(fix) < 8 or fix == b.get("zh"):
            continue
        orig = len(b.get("zh") or "")
        if orig and not (0.6 * orig <= len(fix) <= 2.0 * orig):
            continue
        b["zh"] = fix
        it["applied"] = True
        fixed += 1
    return fixed, issues


def _excerpt(text: str, n: int = 90) -> str:
    t = re.sub(r"\s+", " ", (text or "")).strip()
    return t if len(t) <= n else t[:n] + "…"


def _md_cell(text: str, n: int = 90) -> str:
    return _excerpt(text, n).replace("|", "\\|")


def write_report(outdir: Path, book: dict, *, stats: dict | None = None,
                 ai_issues: list[dict] | None = None,
                 heuristic: list[dict] | None = None, preset: str = "",
                 log=print) -> Path:
    """Write the auditable proof report (Markdown + CSV) next to the DOCX."""
    from datetime import datetime

    stats = stats or {}
    ai_issues = list(ai_issues or [])
    heuristic = check(book) if heuristic is None else list(heuristic)
    meta = book.get("meta") or {}
    blocks = book.get("blocks") or []
    total = len(blocks)
    untranslated = sum(1 for b in blocks
                       if not b.get("zh") and b.get("type") not in ("figure", "table"))
    notes_total = int(stats.get("notes_total") or 0)
    anchored = int(stats.get("notes_anchored") or 0)
    fallback = int(stats.get("notes_fallback") or 0)
    orphan = int(stats.get("notes_orphan") or 0)
    applied = sum(1 for it in ai_issues if it.get("applied"))
    dup_figs = stats.get("figure_dups") or []

    def pct(a, b):
        return "%d%%" % round(a * 100 / b) if b else "—"

    lines = [
        "# 校样报告",
        "",
        "- 书名：%s" % (meta.get("title_zh") or meta.get("title") or "（未命名）"),
        "- 原书名：%s" % (meta.get("title_en") or "—"),
        "- 作者：%s" % (meta.get("author") or "—"),
        "- 版式：%s" % (preset or "—"),
        "- 生成时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M"),
        "- 结构块：%d（未翻译 %d）" % (total, untranslated),
        "",
        "## 一、版面检查",
        "",
        "| 项目 | 状态 |",
        "| --- | --- |",
        "| 正文页码标记 | %s |" % ("已隐藏（校样默认）" if not stats.get("page_tags") else "保留"),
        "| 版权页 | %s |" % ("已生成占位页" if stats.get("copyright_page") else "未生成"),
        "| 奇偶页眉 | %s |" % ("章名 / 书名分置" if stats.get("odd_even_header") else "单页眉"),
        "| 图片分辨率 | 300 dpi |",
        "",
    ]

    ocr = stats.get("ocr") or {}
    fixed = list(ocr.get("fixed") or [])
    severe = set(ocr.get("severe") or [])

    def plist(nums):
        return "、".join("p.%s" % n for n in nums[:40]) or "—"

    if ocr:
        lines += [
            "## 二、OCR 质量",
            "",
            "| 项目 | 数量 |",
            "| --- | --- |",
            "| 整本视觉识别 | %s |" % ("是" if ocr.get("vision") else "否"),
            "| 缺失/损坏页（直接重排） | %d |" % len(severe),
            "| 可疑页（AI 判读） | %d |" % len(ocr.get("suspect") or []),
            "| 　判读后保留 | %d |" % len(ocr.get("kept") or []),
            "| 　判读后重排 | %d |" % len([p for p in fixed if p not in severe]),
            "| 　判读未定 | %d |" % len(ocr.get("unclear") or []),
            "| 跨版跳过 | %d |" % len(ocr.get("skipped") or []),
            "",
        ]
        if ocr.get("suspect"):
            lines.append("- AI 判读页：%s" % plist(ocr["suspect"]))
        if fixed:
            lines.append("- 已按图像重排：%s" % plist(fixed))
        if ocr.get("unclear"):
            lines.append("- 判读未定（沿用原 OCR，建议人工核对）：%s"
                         % plist(ocr["unclear"]))
        if ocr.get("suspect") or fixed or ocr.get("unclear"):
            lines.append("")
    else:
        lines += ["## 二、OCR 质量", "", "未检查（EPUB 或由已译稿重建）。", ""]

    lines += [
        "## 三、脚注锚定",
        "",
        "| 指标 | 数量 |",
        "| --- | --- |",
        "| 脚注条目 | %d |" % notes_total,
        "| 按注号锚定 | %d（%s） |" % (anchored, pct(anchored, notes_total)),
        "| 　其中同页 | %d |" % int(stats.get("notes_exact") or 0),
        "| 　其中邻页 | %d |" % int(stats.get("notes_near") or 0),
        "| 页末兜底 | %d |" % fallback,
        "| 未能挂接 | %d |" % orphan,
        "| 正文注号 | %d |" % int(stats.get("markers_total") or 0),
        "| 　指向章末注 | %d |" % int(stats.get("markers_endnote") or 0),
        "| 注号无对应条目 | %d |" % int(stats.get("markers_unresolved") or 0),
        "| 注号重复引用 | %d |" % int(stats.get("markers_duplicate") or 0),
        "",
    ]
    misses = stats.get("marker_misses") or []
    dups = stats.get("marker_dups") or []
    if misses:
        lines.append("- 缺条目的注号：%s" % "、".join(str(x) for x in misses[:40]))
    if dups:
        lines.append("- 重复引用的注号：%s" % "、".join(str(x) for x in dups[:40]))
    if misses or dups:
        lines.append("")
    lines += [
        "## 四、图与表",
        "",
        "| 指标 | 数量 |",
        "| --- | --- |",
        "| 图 | %d |" % int(stats.get("figures") or 0),
        "| 表 | %d |" % int(stats.get("tables") or 0),
        "| 重号 | %s |" % ("、".join(dup_figs) if dup_figs else "无"),
        "",
        "## 五、全文体检（规则）",
        "",
    ]
    if heuristic:
        lines.append("共 %d 处：%s" % (len(heuristic), summary(heuristic)))
        lines += ["", "| 页码 | 类型 | 原因 | 原文 | 译文 |", "| --- | --- | --- | --- | --- |"]
        for it in heuristic[:200]:
            lines.append("| %s | %s | %s | %s | %s |" % (
                it.get("page") or "—", it.get("type") or "", it.get("reason") or "",
                _md_cell(it.get("source") or "", 60), _md_cell(it.get("zh") or "", 60)))
    else:
        lines.append("未发现规则类问题。")

    lines += ["", "## 六、AI 审校", ""]
    if ai_issues:
        lines.append("共 %d 处，自动修正 %d 处，其余建议人工复核。" % (len(ai_issues), applied))
        lines += ["", "| 页码 | 原因 | 处理 | 建议/修改 |", "| --- | --- | --- | --- |"]
        for it in ai_issues[:200]:
            action = "已修正" if it.get("applied") else "待复核"
            lines.append("| %s | %s | %s | %s |" % (
                it.get("page") or "—", _md_cell(it.get("reason") or "", 80), action,
                _md_cell(it.get("suggest") or "", 80)))
    else:
        lines.append("未运行或未发现问题。")

    cov = coverage(blocks)
    cut = [it for it in heuristic
           if it.get("reason") in ("译文偏短", "疑似截断", "疑似漏句")]
    lines += ["", "## 七、译文完整性", "",
              "| 指标 | 数量 |", "| --- | --- |",
              "| 有原文的页 | %d |" % cov["pages"],
              "| 已译结构块 | %d / %d（%s） |" % (
                  cov["done"], cov["blocks"], pct(cov["done"], cov["blocks"])),
              "| 整页未译 | %d |" % len(cov["empty"]),
              "| 部分未译页 | %d |" % len(cov["partial"]),
              "| 跨页合并段落 | %d |" % cov["stitched"],
              ""]
    if cov["empty"]:
        lines.append("- 整页未译：%s" % plist(cov["empty"]))
    if cut:
        lines.append("- 疑似截断 / 漏句 / 译文偏短：%d 处（见第五节）" % len(cut))
    if cov["empty"] or cut:
        lines.append("")

    verdict = []
    if untranslated:
        verdict.append("%d 个结构块无译文" % untranslated)
    if cov["empty"]:
        verdict.append("%d 页整页未译" % len(cov["empty"]))
    if cut:
        verdict.append("译文完整性 %d 处待核" % len(cut))
    if orphan:
        verdict.append("%d 条脚注未能挂接" % orphan)
    if int(stats.get("markers_unresolved") or 0):
        verdict.append("%d 个注号缺少对应脚注" % int(stats.get("markers_unresolved") or 0))
    if dup_figs:
        verdict.append("图表重号 %d 处" % len(dup_figs))
    if heuristic:
        verdict.append("规则体检 %d 处待核" % len(heuristic))
    pending_ai = len(ai_issues) - applied
    if pending_ai:
        verdict.append("AI 审校 %d 处待复核" % pending_ai)
    if fixed:
        verdict.append("OCR 判读后重排 %d 页待抽查" % len(fixed))
    if ocr.get("unclear"):
        verdict.append("OCR 判读未定 %d 页待人工核对" % len(ocr["unclear"]))
    lines += ["", "## 八、结论", "",
              ("校样可交付人工通读；以下项目需复核：%s。" % "、".join(verdict))
              if verdict else "未发现阻断性排版或翻译问题，可进入人工通读。"]

    md = outdir / "proof_report.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    csv_path = outdir / "proof_report.csv"
    import csv
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["kind", "page", "id", "type", "reason", "source", "zh", "suggest", "applied"])
        for it in heuristic:
            wr.writerow(["heuristic", it.get("page"), it.get("id"), it.get("type"),
                         it.get("reason"), it.get("source"), it.get("zh"), "", ""])
        for it in ai_issues:
            wr.writerow(["ai", it.get("page"), it.get("id"), it.get("type"),
                         it.get("reason"), it.get("source"), it.get("zh"),
                         it.get("suggest"), "yes" if it.get("applied") else "no"])
        ocr_rows = ([(p, "缺失或损坏", "已重排") for p in sorted(severe)]
                    + [(p, "OCR 可疑", "判读后重排" if p in fixed else
                        "判读未定" if p in set(ocr.get("unclear") or [])
                        else "判读后保留") for p in (ocr.get("suspect") or [])
                       if p not in severe]
                    + [(p, "跨版", "跳过") for p in (ocr.get("skipped") or [])])
        for page, reason, action in ocr_rows:
            wr.writerow(["ocr", "p.%s" % page, "", "", reason, "", "", action, ""])
    log("校样报告：%s / %s" % (md.name, csv_path.name))
    return md
