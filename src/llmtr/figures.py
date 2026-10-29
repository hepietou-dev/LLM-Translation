"""Figure / plate pages: detect candidates, confirm with vision, render, crop, embed."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .ingest import labels_for_src, page_index
from .procutil import no_window

KIT_DIR = "libreoffice-kit"
KIT_CLI = Path("node_modules") / "@deepseek-ai" / "libreoffice-kit" / "lib" / "cli.js"

DEFAULTS = {
    "mode": "auto",          # auto | off
    "dpi": 300,
    "min_blocks": 15,
    "max_median_words": 5,
    "max_chars": 900,        # a plate page has little prose; body-text pages must stay text
    "crop": [0.05, 0.04, 0.95, 0.96],
    "rotate": {},
    "captions": {},
    "vision": True,          # confirm candidates with the vision model when a key is set
    "node": "",              # explicit override; empty means use the bundled kit
    "cli": "",
}


def kit_roots() -> list[Path]:
    """Candidate kit folders, most preferred first.

    The native engine cannot start from a path containing non-ASCII characters,
    so the installer puts it under %PROGRAMDATA% (an ASCII path independent of
    the user name) and both the frozen app and source runs check there before
    the bundled copy.
    """
    shared = [os.environ.get("PROGRAMDATA") or r"C:\ProgramData",
              os.environ.get("PUBLIC") or r"C:\Users\Public"]
    roots = [Path(base) / "LLMTranslationKit" for base in shared]
    if getattr(sys, "frozen", False):
        roots.append(Path(sys._MEIPASS) / KIT_DIR)   # noqa: SLF001
    else:
        roots.append(Path(__file__).resolve().parents[2] / "vendor" / KIT_DIR)
    return roots


def _paths_at(root: Path) -> tuple[str, str] | None:
    node, cli = root / "node.exe", root / KIT_CLI
    if node.is_file() and cli.is_file():
        return str(node), str(cli)
    return None


def kit_paths() -> tuple[str, str] | None:
    """(node.exe, cli.js) from the first usable kit folder, else None."""
    for root in kit_roots():
        found = _paths_at(root)
        if found:
            return found
    return None


def resolve(cfg: dict | None = None) -> dict:
    """Prefer configured paths that exist; otherwise fall back to the installed kit."""
    cfg = cfg if cfg is not None else {}
    node, cli = str(cfg.get("node") or ""), str(cfg.get("cli") or "")
    if not (node and cli and Path(node).is_file() and Path(cli).is_file()):
        bundled = kit_paths()
        if bundled:
            node, cli = bundled
    cfg["node"], cfg["cli"] = node, cli
    return cfg


def detect(blocks: list[dict], cfg: dict) -> list[int]:
    """A plate page breaks into many 1-3 word blocks; a text page has long paragraphs."""
    pages = sorted({int(b["page"]) for b in blocks if str(b["page"]).isdigit()})
    out = []
    max_chars = int(cfg.get("max_chars", 900))
    for p in pages:
        on_page = [b for b in blocks if str(b["page"]) == str(p)]
        w = sorted(len(b["text"].split()) for b in on_page)
        chars = sum(len(b.get("text") or "") for b in on_page)
        if (len(w) >= cfg["min_blocks"] and w[len(w) // 2] <= cfg["max_median_words"]
                and chars <= max_chars):
            out.append(p)
    return out


def render(pdf: Path, page: int, labels: list[str], out_png: Path, cfg: dict,
           rotate: int | None = None, page_map: dict | None = None) -> Path | None:
    from PIL import Image

    idx = page_index(page, labels, page_map)
    if idx is None:
        return None
    png = out_png
    png.parent.mkdir(parents=True, exist_ok=True)
    raw = png.parent / ("_raw_%d.png" % page)
    try:
        import pymupdf
        doc = pymupdf.open(str(pdf))
        pix = doc[idx].get_pixmap(dpi=cfg["dpi"])
        pix.save(str(raw))
        doc.close()
    except Exception:
        if not (cfg.get("node") and cfg.get("cli")):
            return None
        raw_dir = png.parent / ("_raw_%d" % page)
        subprocess.run([cfg["node"], cfg["cli"], "render", "--input", str(pdf),
                        "--output-dir", str(raw_dir), "--pages", str(idx + 1),
                        "--dpi", str(cfg["dpi"])],
                       capture_output=True, **no_window())
        raw = next(raw_dir.glob("*.png"), None)
        if raw is None:
            return None
    im = Image.open(raw).convert("RGB")
    w, h = im.size
    on_page = labels_for_src(labels, page_map, idx)
    if len(on_page) > 1 and str(page) in on_page:      # spread: crop to this half only
        k = on_page.index(str(page))
        box = (0, 0, w // 2, h) if k == 0 else (w // 2, 0, w, h)
        im = im.crop(box)
        w, h = im.size
    c = cfg["crop"]
    im = im.crop((int(w * c[0]), int(h * c[1]), int(w * c[2]), int(h * c[3])))
    deg = cfg["rotate"].get(str(page), 0) if rotate is None else rotate
    if deg:
        im = im.rotate(deg, expand=True)
    im.save(out_png, "PNG")
    return out_png


def select_and_replace(blocks: list[dict], cands: list[int], pdf: Path, labels: list[str],
                       assets: Path, cfg: dict, client=None,
                       page_map: dict | None = None, stop=None, progress=None) -> tuple[list[int], dict]:
    """Confirm candidates (optionally via vision), caption them, replace their text by images."""
    from .translate import StopRequested

    assets.mkdir(parents=True, exist_ok=True)
    kinds, plates = {}, []
    for n, p in enumerate(cands):
        if stop is not None and stop.is_set():
            raise StopRequested()
        raw = render(pdf, p, labels, assets / ("_probe_%d.png" % p), cfg, rotate=0,
                     page_map=page_map)
        if raw is None:
            continue
        kind = "plate"
        if client is not None and cfg.get("vision", True):
            try:
                from . import vision
                kind = vision.classify(client, raw, stop=stop)
            except StopRequested:
                raise
            except Exception:
                kind = "plate"
        kinds[p] = kind
        if kind != "plate":
            if progress:
                progress(n + 1, len(cands))
            continue
        cap = cfg["captions"].get(str(p), "")
        if not cap and client is not None and cfg.get("vision", True):
            try:
                from . import vision
                cap = vision.caption(client, raw, stop=stop)
            except StopRequested:
                raise
            except Exception:
                cap = "图"
        png = render(pdf, p, labels, assets / ("p%d.png" % p), cfg, page_map=page_map)
        if png is None:
            continue
        caption = "图　%s（原书 p.%d）" % (cap or "图", p)
        first = next((i for i, b in enumerate(blocks) if str(b["page"]) == str(p)), None)
        if first is None:
            continue
        end = first
        while end < len(blocks) and str(blocks[end]["page"]) == str(p):
            end += 1
        blocks[first:end] = [{"page": str(p), "type": "figure", "text": "", "zh": "",
                              "image": str(png), "caption": caption}]
        plates.append(p)
        if progress:
            progress(n + 1, len(cands))
    return plates, kinds
