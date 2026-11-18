"""Offline recovery entrypoint; never connects to CST."""
from cst_material.export_repair import main


if __name__ == "__main__":
    raise SystemExit(main())
