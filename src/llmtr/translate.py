"""Batching translation client (OpenAI-compatible), id-tagged, with disk cache."""
from __future__ import annotations

import hashlib
import inspect
import json
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from . import gloss as gloss_mod

PROMPT_VERSION = "v5"

SYSTEM = gloss_mod.system_prompt("strong")

TAG = re.compile(r"\[(b\d{4,})\]\s*")
DEFAULT_MAX_TOKENS = 8192
MAX_CHUNK_CHARS = 7000


class StopRequested(Exception):
    """Raised when the user aborts a run."""


class Client:
    def __init__(self, base_url: str, api_key: str, model: str = "deepseek-chat",
                 timeout: int = 120, cache_dir: Path | None = None,
                 price_in: float = 2.0, price_out: float = 8.0,
                 headers: dict | None = None, max_tokens: int = DEFAULT_MAX_TOKENS):
        base = base_url.rstrip("/")
        self.url = base if base.endswith("/chat/completions") else base + "/chat/completions"
        self.key = api_key
        self.model = model
        self.timeout = timeout
        self.cache_dir = cache_dir
        self.usage = {"prompt": 0, "completion": 0, "calls": 0, "cached": 0}
        self.price_in = float(price_in)          # currency per 1M input tokens
        self.price_out = float(price_out)        # currency per 1M output tokens
        self.max_tokens = int(max_tokens or DEFAULT_MAX_TOKENS)
        self.headers = dict(headers or {})
        self._lock = threading.Lock()
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def cost(self) -> float:
        return (self.usage["prompt"] * self.price_in
                + self.usage["completion"] * self.price_out) / 1_000_000

    def add_usage(self, prompt: int = 0, completion: int = 0, calls: int = 0) -> None:
        with self._lock:
            self.usage["prompt"] += int(prompt or 0)
            self.usage["completion"] += int(completion or 0)
            self.usage["calls"] += int(calls or 0)

    def _block_path(self, key: str):
        if not self.cache_dir:
            return None
        d = self.cache_dir / "blocks"
        d.mkdir(parents=True, exist_ok=True)
        return d / (key + ".json")

    def get_block(self, key: str):
        try:
            p = self._block_path(key) if key else None
            if p and p.exists():
                return json.loads(p.read_text(encoding="utf-8"))["zh"]
        except Exception:
            pass
        return None

    def put_block(self, key: str, zh: str) -> None:
        if not key or not zh:
            return
        try:
            p = self._block_path(key)
            if p:
                p.write_text(json.dumps({"zh": zh}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def chat(self, system: str, user: str, stop=None, temperature: float | None = None,
             max_tokens: int | None = None, meta: bool = False):
        """One completion. ``meta=True`` also returns the finish reason.

        Out-of-context models cap output at 4096 tokens by default; a large
        batch then ends mid-sentence and every id after the cut silently stays
        untranslated. The output budget is therefore explicit here, and the
        caller inspects ``finish_reason`` to retry a truncated tail.
        """
        if stop is not None and stop.is_set():
            raise StopRequested()
        payload = {
            "model": self.model,
            "temperature": 0.2 if temperature is None else float(temperature),
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        if max_tokens or self.max_tokens:
            payload["max_tokens"] = int(max_tokens or self.max_tokens)
        body = json.dumps(payload).encode("utf-8")
        ck = hashlib.sha1(self.model.encode() + b"\0" + body).hexdigest()
        f = self.cache_dir / (ck + ".json") if self.cache_dir else None
        if f and f.exists():
            try:
                text = json.loads(f.read_text(encoding="utf-8"))["text"]
                return (text, None) if meta else text
            except Exception:
                pass
        for attempt in range(3):
            if stop is not None and stop.is_set():
                raise StopRequested()
            try:
                text, finish = self._post(payload)
                _u = self._last_usage
                self.add_usage(_u.get("prompt_tokens"), _u.get("completion_tokens"), 1)
                if f:
                    try:                              # a cache miss must never fail a run
                        f.write_text(json.dumps({"text": text}, ensure_ascii=False),
                                     encoding="utf-8")
                    except Exception:
                        pass
                if stop is not None and stop.is_set():
                    raise StopRequested()
                return (text, finish) if meta else text
            except StopRequested:
                raise
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", "ignore")[:400]
                except Exception:                     # noqa: BLE001
                    pass
                if e.code == 400 and "max_tokens" in detail and "max_tokens" in payload:
                    payload.pop("max_tokens", None)   # provider has a lower ceiling
                    continue
                if attempt == 2:
                    raise RuntimeError("api failed: HTTP %s %s" % (e.code, detail)) from e
                time.sleep(2 * (attempt + 1))
            except Exception as e:  # network hiccups, partial reads, malformed JSON
                if attempt == 2:
                    raise RuntimeError("api failed: %s" % e) from e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError("unreachable")

    def chat_meta(self, system: str, user: str, stop=None, temperature: float | None = None,
                  max_tokens: int | None = None):
        return self.chat(system, user, stop=stop, temperature=temperature,
                         max_tokens=max_tokens, meta=True)

    def _post(self, payload: dict) -> tuple[str, str | None]:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self.key,
            **self.headers})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        self._last_usage = data.get("usage") or {}
        return text, choice.get("finish_reason")


def _call(fn, system: str, user: str, stop=None, temperature=None, max_tokens=None):
    """Call a client chat method, passing only the keywords it actually accepts.

    Test doubles and third-party clients predate ``temperature``/``max_tokens``;
    introspection keeps them working instead of forcing a signature change.
    """
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    kw = {}
    if "stop" in params:
        kw["stop"] = stop
    if temperature is not None and "temperature" in params:
        kw["temperature"] = temperature
    if max_tokens is not None and "max_tokens" in params:
        kw["max_tokens"] = max_tokens
    return fn(system, user, **kw)


def _chat(client, system: str, user: str, stop=None, temperature=None, max_tokens=None):
    return _call(client.chat, system, user, stop=stop,
                 temperature=temperature, max_tokens=max_tokens)


def _chat_meta(client, system: str, user: str, stop=None, temperature=None,
               max_tokens=None) -> tuple[str, str | None]:
    fn = getattr(client, "chat_meta", None)
    if fn is None:
        return _chat(client, system, user, stop=stop, temperature=temperature,
                     max_tokens=max_tokens), None
    return _call(fn, system, user, stop=stop, temperature=temperature,
                 max_tokens=max_tokens)


def _term_hit(term: str, blob: str) -> bool:
    t = term.lower()
    if any("\u4e00" <= c <= "\u9fff" for c in t):
        return t in blob
    return re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(t), blob) is not None


CJK = re.compile(r"[\u4e00-\u9fff]")
NUM_ONLY = re.compile(r"^[\d\s.,;:%()\[\]{}<>/\\|–—•·-]+$")
URL_ONLY = re.compile(r"^(?:https?://|www\.)\S+$", re.I)
CITE_CUES = re.compile(
    r"(?:\b(?:19|20)\d{2}[a-z]?\b|\bpp?\.\s?\d|\bVol\.\s?\d|"
    r"\b(?:Press|University|Univ\.|Journal|Review|Publishers?|Books)\b|"
    r"\bet al\.|\bdoi:|\bibid\.|\bop\. cit\.|https?://)")
STOP_WORDS = frozenset(
    "the a an of and or in on for to as at by with from this that these those is are was were be "
    "been being see cf also ibid idem vol pp p ed eds trans".split())


def _citation_only(t: str) -> bool:
    """A note that is almost entirely a bibliographic citation needs no translation."""
    stripped = re.sub(r"[“\"][^”\"]{8,}[”\"]", " ", t)
    stripped = re.sub(
        r"[A-Z][A-Za-z\u2019'\-]+"
        r"(?:\s*(?:,\s*(?:and\s+)?|and\s+|&\s*)[A-Z][A-Za-z\u2019'\-]+)*"
        r"(?:\s+et\s+al\.)?\s*\((?:19|20)\d{2}[a-z]?\)", " ", stripped)
    stripped = re.sub(r"(?:\b(?:19|20)\d{2}[a-z]?\b[.,;:]?\s*)+", " ", stripped)
    stripped = re.sub(r"\bpp?\.\s?\d+(?:\s*[–-]\s*\d+)?", " ", stripped)
    stripped = re.sub(r"\bVol\.\s?\d+(?:\(\d+\))?", " ", stripped)
    stripped = re.sub(r"https?://\S+", " ", stripped)
    stripped = re.sub(r"\b(?:ibid\.|op\. cit\.)", " ", stripped, flags=re.I)
    words = [w for w in re.findall(r"[A-Za-z]{2,}", stripped)
             if w.lower() not in STOP_WORDS]
    return len(CITE_CUES.findall(t)) >= 2 and len(words) <= 10


def _skip_translation(b: dict) -> bool:
    """Blocks the model must not spend tokens on."""
    t = (b.get("text") or "").strip()
    if not t:
        return True
    if NUM_ONLY.fullmatch(t) or URL_ONLY.match(t):
        return True
    if len(CJK.findall(t)) >= max(4, int(len(t) * 0.5)):
        return True
    if b.get("type") in ("note", "bib") and _citation_only(t):
        return True
    return False


def _block_key(client: Client, b: dict, glossary: dict[str, str], gloss_mode: str = "") -> str:
    blob = (b.get("text") or "").lower()
    terms = "\n".join("%s=%s" % (k, v) for k, v in sorted(glossary.items())
                      if k and _term_hit(k, blob))
    raw = "\0".join((PROMPT_VERSION, gloss_mode, str(getattr(client, "model", "") or ""),
                     b.get("type", ""), b.get("text", ""), terms))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _cache_get(client, key: str):
    fn = getattr(client, "get_block", None)
    if not fn:
        return None
    try:
        return fn(key)
    except Exception:
        return None


def _cache_put(client, key: str, zh: str) -> None:
    fn = getattr(client, "put_block", None)
    if not fn:
        return
    try:
        fn(key, zh)
    except Exception:
        pass


def build_prompt(blocks: list[dict], glossary: dict[str, str]) -> str:
    blob = " ".join(b["text"] for b in blocks).lower()
    used = {k: v for k, v in glossary.items() if k and _term_hit(k, blob)}
    head = ""
    if used:
        head = "词表（English=中文）：\n" + "\n".join(f"{k}={v}" for k, v in used.items()) + "\n\n"
    marks = {"h1": "@@H1@@", "h2": "@@H2@@", "h3": "@@H3@@", "p": "", "note": "@@N@@",
             "table": ""}
    lines = []
    for b in blocks:
        pre = marks[b["type"]]
        lines.append("[%s] %s%s" % (b["id"], pre + " " if pre else "", b["text"]))
    return head + "\n".join(lines)


def parse_ids(text: str) -> dict[str, str]:
    hits = list(TAG.finditer(text))
    out = {}
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        val = re.sub(r"^(@@(?:H1|H2|H3|P|N)@@\s*)+", "", text[m.end():end].strip())
        out[m.group(1)] = val
    return out

def _parallel(tasks: list, workers: int, stop=None, progress=None) -> None:
    """Run independent API tasks concurrently and abort without waiting for the pool."""
    if not tasks:
        return
    ex = ThreadPoolExecutor(max_workers=max(1, int(workers)))
    futs = [ex.submit(t) for t in tasks]
    pending = set(futs)
    done = 0
    try:
        while pending:
            if stop is not None and stop.is_set():
                raise StopRequested()
            finished, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
            for f in finished:
                f.result()
                done += 1
                if progress:
                    progress(done, len(tasks))
    except BaseException:
        for f in futs:
            f.cancel()
        ex.shutdown(wait=False, cancel_futures=True)
        raise
    ex.shutdown(wait=True)


def fix_headings(client, blocks, stop=None, workers: int = 4, progress=None,
                 system: str | None = None):
    """Headings whose translation came back as a sentence are re-requested alone."""
    bad = [b for b in blocks if b.get("type") in ("h1", "h2") and b.get("zh")
           and (len(b["zh"]) > 60 or b["zh"].rstrip().endswith(("。", ".")))]
    fixed = []

    def one(b: dict) -> None:
        try:
            out = _chat(client, system or SYSTEM,
                        "只把这个标题译成简洁中文（不超过 25 字），不要解释，只输出译文：\n" + b["text"],
                        stop=stop)
        except StopRequested:
            raise
        except Exception:
            return
        out = out.strip().split("\n")[0].strip()
        out = re.sub(r"^\[b?\d+\]\s*", "", out)
        if out and len(out) <= 60:
            b["zh"] = out
            fixed.append(1)

    _parallel([lambda b=b: one(b) for b in bad], workers, stop=stop, progress=progress)
    return len(fixed)


def repair_blocks(client, blocks, stop=None, workers: int = 4, progress=None,
                  system: str | None = None):
    """Re-request blocks whose translation looks shifted (length/heading anomalies)."""
    from . import proofread as pf
    bad = [i for i in pf.check({"blocks": blocks})
           if i["reason"] in ("篇幅异常", "标题过长", "未翻译",
                              "译文偏短", "疑似截断", "疑似漏句")]
    if not bad:
        return 0
    seen, picked = set(), []
    for it in bad:
        if it["id"] not in seen:
            seen.add(it["id"])
            picked.append(it)
    bad = picked
    by_id = {b.get("id"): b for b in blocks}
    fixed = []

    def one(it: dict) -> None:
        b = by_id.get(it["id"])
        if not b or not b.get("text"):
            return
        prompt = ("把下面这一段译成中文，只输出译文，不要编号、不要解释：\n" + b["text"])
        try:
            out = _chat(client, system or SYSTEM, prompt, stop=stop)
        except StopRequested:
            raise
        except Exception:
            return
        out = re.sub(r"^\[b?\d+\]\s*", "", out.strip()).strip()
        if out and out != b.get("zh"):
            b["zh"] = out
            fixed.append(1)

    _parallel([lambda it=it: one(it) for it in bad[:40]], workers, stop=stop, progress=progress)
    return len(fixed)


EN_SYSTEM = (
    "你是学术译稿的术语编辑。译文中夹带了未译的英文单词。"
    "只把这些英文单词或短语按上下文译成规范中文；专名、书名、拉丁/希腊术语、"
    "文献引文、数字和 ⟦注号⟧ 一律保持原样；其余文字不作改动，不增删内容。"
    "只输出修改后的完整段落，不要编号、不要解释。"
)


def _term_fix_ok(old: str, new: str) -> bool:
    if not new or new == old:
        return False
    from collections import Counter

    from . import proofread as pf
    if Counter(NUM_RE.findall(old)) != Counter(NUM_RE.findall(new)):
        return False
    if Counter(MARK_RE.findall(old)) != Counter(MARK_RE.findall(new)):
        return False
    if not (0.60 <= len(new) / max(1, len(old)) <= 1.40):
        return False
    before, after = pf.suspect_terms(old), pf.suspect_terms(new)
    return len(after) < len(before) or (not after and not pf.ascii_prose(new))


def _term_fix_key(client, b: dict, words: list[str]) -> str:
    raw = "\0".join(("english", PROMPT_VERSION, str(getattr(client, "model", "") or ""),
                     b.get("zh", ""), ",".join(words)))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def fix_english_terms(client, blocks, workers: int = 4, limit: int = 120,
                      stop=None, progress=None) -> int:
    """Translate stray English words left inside otherwise Chinese paragraphs."""
    from . import proofread as pf
    todo = []
    for b in blocks:
        if b.get("type") not in ("p", "note"):
            continue
        zh = (b.get("zh") or "").strip()
        if not zh:
            continue
        words = pf.suspect_terms(zh)
        if not words and not pf.ascii_prose(zh):
            continue
        todo.append((b, words))
    todo = todo[:max(1, int(limit))]
    if not todo:
        return 0

    lock = threading.Lock()
    state = {"done": 0, "fixed": 0}

    def work(b: dict, words: list[str]) -> None:
        pk = _term_fix_key(client, b, words)
        cached = _cache_get(client, pk)
        if cached and _term_fix_ok(b["zh"], cached):
            with lock:
                b["zh"] = cached
                state["fixed"] += 1
            return
        hint = ("未译的英文：%s。" % "、".join(words[:12])) if words else "夹带了英文。"
        prompt = hint + "请按上下文把这些词译成中文，其余文字保持不变，只输出修改后的整段：\n" + b["zh"]
        try:
            out = _chat(client, EN_SYSTEM, prompt, stop=stop)
        except StopRequested:
            raise
        except Exception:
            return
        out = re.sub(r"^\s*\[b\d{4,}\]\s*", "", out.strip()).strip()
        if _term_fix_ok(b["zh"], out):
            with lock:
                b["zh"] = out
                state["fixed"] += 1
            _cache_put(client, pk, out)

    def wrapped(item):
        try:
            work(*item)
        finally:
            with lock:
                state["done"] += 1
            if progress:
                progress(state["done"], len(todo))

    _parallel([lambda it=it: wrapped(it) for it in todo], workers, stop=stop)
    return state["fixed"]


def translate_blocks(client: Client, blocks: list[dict], glossary: dict[str, str],
                     workers: int = 4, chunk_size: int = 1600,
                     chunk_chars: int = MAX_CHUNK_CHARS, dry_run: bool = False,
                     progress=None, stop=None,
                     source_gloss: str = "strong") -> list[dict]:
    system = gloss_mod.system_prompt(source_gloss)
    skipped = 0
    seen: dict[str, dict] = {}
    dups: list[tuple[dict, dict]] = []
    todo: list[dict] = []
    for b in blocks:
        if b.get("zh"):
            continue
        if _skip_translation(b):
            b["zh"] = (b.get("text") or "").strip()
            skipped += 1
            continue
        ck = "" if dry_run else _block_key(client, b, glossary, source_gloss)
        cached = _cache_get(client, ck) if ck else None
        if cached:
            b["zh"] = cached
            skipped += 1
            continue
        t = b.get("text") or ""
        if t in seen:
            dups.append((b, seen[t]))
            continue
        seen[t] = b
        b["_ck"] = ck
        todo.append(b)

    groups, cur, w, chars = [], [], 0, 0
    for b in todo:
        n = len(b["text"].split())
        nc = len(b["text"]) + 10
        if cur and (w + n > chunk_size or chars + nc > chunk_chars):
            groups.append(cur)
            cur, w, chars = [], 0, 0
        cur.append(b)
        w += n
        chars += nc
    if cur:
        groups.append(cur)

    def work(group: list[dict], attempts: int = 4) -> None:
        if dry_run:
            for b in group:
                if stop is not None and stop.is_set():
                    raise StopRequested()
                b["zh"] = "【测试】" + b["text"]
            return
        todo = list(group)
        for attempt in range(attempts):
            if stop is not None and stop.is_set():
                raise StopRequested()
            prompt = build_prompt(todo, glossary)
            if attempt:
                prompt += "\n\n【重试 %d】上次输出缺行。本次必须输出 %d 行，编号与输入严格一一对应。" % (attempt, len(todo))
            try:
                text, finish = _chat_meta(client, system, prompt, stop=stop)
            except StopRequested:
                raise
            except Exception:
                time.sleep(2)
                continue
            got = parse_ids(text)
            if finish == "length" and got:
                # The model hit the output ceiling: its last line is cut off
                # mid-sentence, so drop it and let the retry regenerate it.
                got.pop(next(reversed(got)))
            still = []
            for b in todo:
                zh = (got.get(b["id"]) or "").strip()
                if zh:
                    b["zh"] = zh
                else:
                    still.append(b)
            if not still:
                return
            todo = still
        for b in todo:
            b["zh"] = ""

    lock = threading.Lock()
    state = {"done": skipped}

    def wrapped(g):
        work(g)
        if progress:
            with lock:
                state["done"] += len(g)
                progress(state["done"], len(blocks))

    ex = ThreadPoolExecutor(max_workers=max(1, int(workers)))
    futs = [ex.submit(wrapped, g) for g in groups]
    pending = set(futs)
    try:
        while pending:
            if stop is not None and stop.is_set():
                raise StopRequested()
            finished, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
            for f in finished:
                f.result()
    except BaseException:
        for f in futs:
            f.cancel()
        ex.shutdown(wait=False, cancel_futures=True)
        raise
    ex.shutdown(wait=True)

    # Last resort: a long book can still lose a batch to repeated truncation.
    # Retry every unresolved block alone, where the output ceiling is ample.
    missing = [b for b in todo if not (b.get("zh") or "").strip()]
    if missing and not dry_run:
        def rescue(b: dict) -> None:
            prompt = build_prompt([b], glossary)
            for _ in range(3):
                if stop is not None and stop.is_set():
                    raise StopRequested()
                try:
                    text, finish = _chat_meta(client, system, prompt, stop=stop)
                except StopRequested:
                    raise
                except Exception:
                    time.sleep(2)
                    continue
                got = parse_ids(text)
                zh = (got.get(b["id"]) or "").strip()
                if not zh:
                    zh = re.sub(r"^\s*\[b\d{4,}\]\s*", "", text.strip())
                if zh and not (finish == "length" and len(zh) < 20):
                    b["zh"] = zh
                    return
                time.sleep(1)
        _parallel([lambda b=b: rescue(b) for b in missing],
                  max(1, min(int(workers), 4)), stop=stop)

    for b in todo:
        ck = b.pop("_ck", "")
        if ck and b.get("zh"):
            _cache_put(client, ck, b["zh"])
    for b, rep in dups:
        b["zh"] = rep.get("zh") or (b.get("text") or "").strip()
    if dups and progress:
        progress(len(blocks), len(blocks))
    return blocks


POLISH_SYSTEM = (
    "你是出版社的中文编辑，负责清除学术译稿里的翻译腔。逐条改写下列译文：\n"
    "1) 保留编号与行数，只输出“编号 + 空格 + 润色后的译文”，不要解释、不要 Markdown；\n"
    "2) 把“对……加以/进行/作出/给予……”改成直接动词，拆开多层“的”字定语，"
    "把照搬英文的被动句、从句和语序改成中文主动短句，删去多余的“一个/一种/这种”；\n"
    "3) 意思、限定程度（may/suggests/to some extent 等）、逻辑关系、指代对象不变，"
    "不合并、不拆分、不压缩、不概括；\n"
    "4) 术语、人名、书名、数字、年份、页码、⟦注号⟧ 和所有外文原文一律原样保留；\n"
    "5) 遇到上述翻译腔必须改写；确已通顺的句子保持原样；不添加原文没有的评论或修辞，"
    "保持学术书面语的简洁准确。"
)

POLISH_PROMPT = ("逐条润色下列译文，编号与行数保持不变：\n\n%s")

NUM_RE = re.compile(r"\d+")
MARK_RE = re.compile(r"⟦\d+⟧")
ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z\u2019'\-]*")
SENT_END = re.compile(r"[。！？；…”」』]\s*$")
POLISH_VERSION = "p2"


def _polish_ok(old: str, new: str) -> bool:
    """A polish may change wording only: facts, citations and length must survive."""
    if not new or new == old:
        return False
    from collections import Counter
    if Counter(NUM_RE.findall(old)) != Counter(NUM_RE.findall(new)):
        return False
    if Counter(MARK_RE.findall(old)) != Counter(MARK_RE.findall(new)):
        return False
    if Counter(ASCII_WORD.findall(old)) != Counter(ASCII_WORD.findall(new)):
        return False
    oc = len(CJK.findall(old))
    nc = len(CJK.findall(new))
    if oc and not (0.65 <= nc / oc <= 1.45):
        return False
    if SENT_END.search(old) and not SENT_END.search(new):
        return False
    return True


def _polish_key(client, b: dict) -> str:
    raw = "\0".join(("polish", POLISH_VERSION, PROMPT_VERSION,
                     str(getattr(client, "model", "") or ""),
                     b.get("type", ""), b.get("text", ""), b.get("zh", "")))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def polish_blocks(client, blocks, batch_chars: int = 6000, max_blocks: int = 8,
                  workers: int = 4, stop=None, progress=None) -> int:
    """Second-pass editing of fluent Chinese; returns how many blocks changed."""
    cands = []
    for b in blocks:
        zh = (b.get("zh") or "").strip()
        if b.get("type") not in ("p", "note") or len(zh) < 40:
            continue
        if _skip_translation(b):
            continue
        pk = _polish_key(client, b)
        cached = _cache_get(client, pk)
        if cached:
            if _polish_ok(zh, cached):
                b["zh"] = cached
            continue
        b["_pk"] = pk
        cands.append(b)
    if not cands:
        return 0

    groups, cur, chars = [], [], 0
    for b in cands:
        nc = len(b["zh"]) + 8
        if cur and (len(cur) >= int(max_blocks) or chars + nc > int(batch_chars)):
            groups.append(cur)
            cur, chars = [], 0
        cur.append(b)
        chars += nc
    if cur:
        groups.append(cur)

    lock = threading.Lock()
    state = {"done": 0, "changed": 0}

    def work(g: list[dict]) -> None:
        body = "\n".join("[%s] %s" % (b["id"], b["zh"]) for b in g)
        try:
            text = _chat(client, POLISH_SYSTEM, POLISH_PROMPT % body, stop=stop,
                         temperature=0.3)
        except StopRequested:
            raise
        except Exception:
            return
        got = parse_ids(text)
        for b in g:
            new = (got.get(b["id"]) or "").strip()
            if _polish_ok(b["zh"], new):
                b["zh"] = new
                state["changed"] += 1

    def wrapped(g):
        work(g)
        with lock:
            state["done"] += len(g)
        if progress:
            progress(state["done"], len(cands))

    _parallel([lambda g=g: wrapped(g) for g in groups], workers, stop=stop)

    for b in cands:
        pk = b.pop("_pk", "")
        if pk:
            _cache_put(client, pk, b["zh"])
    return state["changed"]
