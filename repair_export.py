#!/usr/bin/env python
"""Offline recovery entrypoint; never connects to CST."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


if __name__ == "__main__":
    from cst_material.export_repair import main

    raise SystemExit(main())
