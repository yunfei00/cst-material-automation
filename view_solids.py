#!/usr/bin/env python
"""CST Solid Viewer: standalone STEP or auto-generated .geometry folder."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from cst_material.solid_viewer import main


if __name__ == "__main__":
    raise SystemExit(main())
