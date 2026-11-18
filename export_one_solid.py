#!/usr/bin/env python
"""One Solid STEP export diagnostic; no cleanup, recognition or materials."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

if __name__ == "__main__":
    from cst_material.single_solid_export import main

    raise SystemExit(main())
