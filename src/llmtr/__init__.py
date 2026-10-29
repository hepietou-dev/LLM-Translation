"""llmtr package."""
import os as _os
import sys as _sys

_vendor = _os.environ.get("LLMTR_PYLIBS")
if _vendor and _vendor not in _sys.path:
    _sys.path.insert(0, _vendor)

import logging as _logging
import warnings as _warnings

_warnings.filterwarnings("ignore")
_logging.getLogger("pypdf").setLevel(_logging.ERROR)
