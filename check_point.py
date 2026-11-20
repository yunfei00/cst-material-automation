#!/usr/bin/env python
"""CLI wrapper for offline STEP point classification."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


if __name__ == "__main__":
    from cst_material.check_point import main

    raise SystemExit(main())
