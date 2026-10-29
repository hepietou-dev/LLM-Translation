"""Make the project importable in a bare Python (no installed wheel).

Preference order: the project's own ``src``, then vendored dependency folders
used on this machine (``_libs`` and the PyInstaller ``_internal`` dir).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src",
             ROOT.parent / "_libs",
             ROOT / "dist" / "LLMTranslation" / "_internal"):
    if path.exists() and str(path) not in sys.path:
        sys.path.insert(0, str(path))
