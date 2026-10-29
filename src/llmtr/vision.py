"""Vision helpers: classify a page image, caption a figure, transcribe a bad page."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import urllib.request
from pathlib import Path

MAX_SIDE = 1024


def _data_uri(path: Path, max_side: int = MAX_SIDE) -> str:
    from PIL import Image

    im = Image.open(path).convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def ask(client, image: Path, prompt: str, max_tokens: int = 160,
        max_side: int = MAX_SIDE, stop=None) -> str:
    """One image + one question -> short text answer (cached by prompt+image hash)."""
    from .translate import StopRequested

    if stop is not None and stop.is_set():
        raise StopRequested()
    uri = _data_uri(image, max_side)
    key = hashlib.sha1((client.model + prompt + uri[-4096:]).encode()).hexdigest()
    cache = Path(client.cache_dir) / ("v_" + key + ".json") if client.cache_dir else None
    if cache and cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))["text"]
        except Exception:                              # noqa: BLE001
            pass

    body = json.dumps({
        "model": client.model,
        "max_tokens": max_tokens,
        "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": uri}},
        ]}],
    }).encode("utf-8")
    req = urllib.request.Request(client.url, data=body, headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + client.key})
    text = ""
    for attempt in range(3):
        if stop is not None and stop.is_set():
            raise StopRequested()
        try:
            with urllib.request.urlopen(req, timeout=client.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            text = (data["choices"][0]["message"]["content"] or "").strip()
            if text:
                break
        except Exception:
            if attempt == 2:
                raise
        import time as _t
        _t.sleep(2)
    try:
        u = data.get("usage") or {}
        client.add_usage(u.get("prompt_tokens"), u.get("completion_tokens"), 1)
    except Exception:                                  # noqa: BLE001
        pass
    if cache and text:
        try:                                           # cache must not fail a run
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"text": text}, ensure_ascii=False),
                             encoding="utf-8")
        except Exception:                              # noqa: BLE001
            pass
    if stop is not None and stop.is_set():
        raise StopRequested()
    return text


CLASSIFY = ("这是一本英文书的页面图像。判断页面类型："
            "A=正常文字页；B=图表页（地图/照片/示意图，整页以图形为主）；C=表格页。"
            "只输出一个字母，可附不超过 10 字的理由。")


def classify(client, image: Path, stop=None) -> str:
    ans = ask(client, image, CLASSIFY, max_tokens=40, stop=stop).upper()
    for tag, name in (("B", "plate"), ("C", "table"), ("A", "text")):
        if ans.startswith(tag) or tag in ans[:3]:
            return name
    return "text"


def caption(client, image: Path, stop=None) -> str:
    ans = ask(client, image,
              "读这张书页图上的图题或图例，给出简洁的中文图注（不超过 30 字，不加书名号，不要解释）。"
              "若图上没有可读标题，只输出“图”。", max_tokens=60, stop=stop)
    ans = ans.strip().strip("《》\"'“”")
    return ans or "图"


def transcribe(client, image: Path, stop=None) -> str:
    return ask(client, image,
               "逐行转写这页英文文字，保留段落换行，不要翻译、不要解释、不要补全看不清的内容。",
               max_tokens=2000, stop=stop)


LAYOUT = (
    "这是一本英文书某页的扫描图。请按版面结构转写，只输出下列标记行，不要任何解释：\n"
    "[PAGE] 本页印刷页码（看不清或没有就写 ?）\n"
    "[H1] 章标题（只在本页印有章节大标题时输出）\n"
    "[H2] 节标题\n"
    "[P] 一个正文段落（整段写在一行内，不要按行断开）\n"
    "[N] 12 脚注内容（12 为原书注号）\n"
    "规则：不翻译；不合并两段；\n"
    "页面最顶部那一行（通常带页码和书名或章名）是页眉，绝对不要输出，即使它看起来像标题；\n"
    "页面底部的页码、扫描水印也不要输出；\n"
    "看不清的词用 [?] 代替，不要凭空补写；\n"
    "只有页面上真实印有脚注文字时才输出 [N] 行：正文里的上标注号不是脚注，"
    "本页底部没有脚注文字就不要输出任何 [N] 行，也不要输出只有注号的行。"
)


def transcribe_layout(client, image: Path, max_side: int = 1600, stop=None) -> str:
    """Full-page structural OCR: headings / paragraphs / footnotes, one per line."""
    return ask(client, image, LAYOUT, max_tokens=3000, max_side=max_side, stop=stop)


REVIEW = (
    "这是一页英文书扫描图，后面附着机器 OCR 文本。先判断这份 OCR 能否直接用于翻译：\n"
    "若文字完整、词间空格正确、脚注齐全（个别拼写或标点错误可以接受），只输出一行 [OK]，不要输出其他内容；\n"
    "若存在漏段、词间空格丢失（粘连）、明显错字、漏脚注或乱码，输出 [FIX]，然后严格按下面的标记行完整重写整页。\n"
    "\n" + LAYOUT + "\n"
    "\n机器 OCR 文本（可能有错，仅供对照，不要照抄）：\n"
)


def review_layout(client, image: Path, ocr_text: str, max_side: int = 1600,
                  stop=None, strict: bool = False) -> str:
    """Judge OCR text against the page image; return ``[OK]`` or a marked rewrite."""
    prompt = REVIEW + (ocr_text or "")[:4000]
    if strict:
        prompt += ("\n上次输出无法解析。本次只输出 [OK]，或 [FIX] 后严格按 "
                   "[PAGE]/[H1]/[H2]/[P]/[N] 标记行重写整页，不要任何解释。")
    return ask(client, image, prompt, max_tokens=3000, max_side=max_side, stop=stop)
