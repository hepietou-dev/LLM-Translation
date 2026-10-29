"""llmtr - PDF/EPUB/DOCX -> translated DOCX."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_vendor = os.environ.get("LLMTR_PYLIBS")
if _vendor and _vendor not in sys.path:
    sys.path.insert(0, _vendor)

from . import build as bd          # noqa: E402
from . import ingest as ing        # noqa: E402
from . import pipeline as pl       # noqa: E402
from . import proofread as pf      # noqa: E402


def cmd_run(a) -> int:
    cfg_path = Path(a.config) if a.config else Path(__file__).resolve().parents[2] / "config" / "config.json"
    cfg = pl.load_config(cfg_path)
    for k in ("glossary", "names"):
        v = Path(cfg["paths"][k])
        cfg["paths"][k] = str(v if v.is_absolute() else cfg_path.parent / v)
    if a.api_key:
        cfg["api"]["key"] = a.api_key
    elif os.environ.get("DEEPSEEK_API_KEY"):
        cfg["api"]["key"] = os.environ["DEEPSEEK_API_KEY"]
    elif os.environ.get("OPENAI_API_KEY"):
        cfg["api"]["key"] = os.environ["OPENAI_API_KEY"]

    structure = None
    if a.structure:
        structure = json.loads(Path(a.structure).read_text(encoding="utf-8"))["chapters"]

    res = pl.run(Path(a.pdf), Path(a.out or Path("out") / Path(a.pdf).stem), cfg,
                 preset=bd.LABEL_TO_PRESET.get(a.preset, a.preset), dry_run=a.dry_run, pages_filter=a.pages,
                 structure=structure, use_toc=not a.no_toc)
    if not res["ok"]:
        print("该 PDF 没有可用文字层（很可能是扫描件）。\n"
              "请先做 OCR 得到「可搜索 PDF」，或直接改用原生电子版 PDF 后再上传。")
        return 2
    print("ok: %s | blocks=%d | toc_pages=%d" % (res["docx"], res["blocks"], res["toc_pages"]))
    return 0


def cmd_probe(a) -> int:
    info = ing.probe(ing.read_pages(Path(a.pdf)))
    print(json.dumps(info, ensure_ascii=False))
    return 0 if info["has_text_layer"] else 2


def cmd_rebuild(a) -> int:
    cfg = pl.load_config(Path(__file__).resolve().parents[2] / "config" / "config.json")
    structure = json.loads(Path(a.structure).read_text(encoding="utf-8"))["chapters"] if a.structure else None
    pl.rebuild(Path(a.book), Path(a.out), cfg, preset=bd.LABEL_TO_PRESET.get(a.preset, a.preset), structure=structure)
    return 0


def cmd_build(a) -> int:
    data = json.loads(Path(a.book).read_text(encoding="utf-8"))
    stats = {}
    preset = bd.LABEL_TO_PRESET.get(a.preset, a.preset)
    bd.build_doc(data, preset, Path(a.out), data["meta"], stats=stats)
    pf.write_report(Path(a.out).parent, data, stats=stats, preset=preset)
    print("built", a.out)
    return 0




def cmd_proof(a) -> int:
    book = pf.load(Path(a.book))
    issues = pf.check(book)
    if getattr(a, "ai", False):
        from . import translate as tr
        key = os.environ.get("DEEPSEEK_API_KEY", "")
        client = tr.Client("https://api.deepseek.com/v1", key, "deepseek-chat")
        if not key:
            print("缺少 DEEPSEEK_API_KEY，跳过 AI 校对")
        else:
            if getattr(a, "ai_apply", False):
                fixed, ai = pf.ai_fix(client, book["blocks"], limit=int(a.limit))
                pf.save(book, Path(a.book))
                print("ai fixed: %d 条已自动改写" % fixed)
            else:
                ai = pf.ai_check(client, book["blocks"], limit=int(a.limit))
            print("ai issues: %d" % len(ai))
            for it in ai:
                print("  p.%-4s %s\n    原文: %s\n    译文: %s\n    建议: %s" % (
                    it["page"], it["reason"], it["source"][:70], it["zh"][:70],
                    (it.get("suggest") or "")[:70]))
            issues = issues + ai
    print("issues: %d | %s" % (len(issues), pf.summary(issues)))
    for it in issues[: int(a.show)]:
        print("  p.%-4s %-6s %s\n    原文: %s\n    译文: %s" % (
            it["page"], it["reason"], it["id"], it["source"][:70], it["zh"][:70]))
    return 0


def cmd_review(a) -> int:
    from . import review
    review.launch(Path(a.book), Path(a.out) if a.out else None, preset=a.preset)
    return 0

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="llmtr")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe"); p.add_argument("pdf"); p.set_defaults(fn=cmd_probe)

    r = sub.add_parser("run"); r.add_argument("pdf")
    r.add_argument("--preset", default=bd.PRESET_LABELS["print"], choices=list(bd.PRESET_LABELS.values()))
    r.add_argument("--config"); r.add_argument("--out")
    r.add_argument("--api-key"); r.add_argument("--dry-run", action="store_true")
    r.add_argument("--pages"); r.add_argument("--structure"); r.add_argument("--no-toc", action="store_true")
    r.set_defaults(fn=cmd_run)

    rb = sub.add_parser("rebuild"); rb.add_argument("book")
    rb.add_argument("--out", required=True)
    rb.add_argument("--structure")
    rb.add_argument("--preset", default=bd.PRESET_LABELS["print"], choices=list(bd.PRESET_LABELS.values()))
    rb.set_defaults(fn=cmd_rebuild)

    pr = sub.add_parser("proof"); pr.add_argument("book")
    pr.add_argument("--show", default="20")
    pr.add_argument("--ai", action="store_true")
    pr.add_argument("--limit", default="30")
    pr.add_argument("--ai-apply", action="store_true")
    pr.set_defaults(fn=cmd_proof)

    rv = sub.add_parser("review"); rv.add_argument("book")
    rv.add_argument("--out"); rv.add_argument("--preset", default=bd.PRESET_LABELS["print"], choices=list(bd.PRESET_LABELS.values()))
    rv.set_defaults(fn=cmd_review)

    b = sub.add_parser("build"); b.add_argument("book")
    b.add_argument("--preset", default=bd.PRESET_LABELS["print"], choices=list(bd.PRESET_LABELS.values()))
    b.add_argument("--out", required=True); b.set_defaults(fn=cmd_build)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
