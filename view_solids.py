#!/usr/bin/env python
"""CST Solid Viewer: standalone STEP or auto-generated .geometry folder."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


if __name__ == "__main__":
    from cst_material.solid_viewer import main

    raise SystemExit(main())
