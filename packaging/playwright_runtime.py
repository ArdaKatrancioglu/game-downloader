from __future__ import annotations

import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    bundled_root = Path(sys._MEIPASS) / "playwright-browsers"
    if bundled_root.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled_root)
