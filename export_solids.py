#!/usr/bin/env python
"""One-argument, per-Solid fault-tolerant CST export diagnostic."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

if __name__ == "__main__":
    from cst_material.all_solid_export import main

    raise SystemExit(main())
