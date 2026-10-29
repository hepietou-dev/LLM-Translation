"""Subprocess helpers: never flash a console window on Windows."""
from __future__ import annotations

import subprocess
import sys


def no_window() -> dict:
    """Keyword arguments that keep child processes invisible in a GUI app."""
    if sys.platform != "win32":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            "startupinfo": si}
