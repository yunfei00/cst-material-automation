"""Command-line entry point for safe local diagnostics."""
from __future__ import annotations

import argparse

from cst_material.probe import probe, to_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CST 材料自动化：离线诊断工具")
    parser.add_argument("command", choices=["probe"], help="只读环境探测")
    args = parser.parse_args(argv)
    if args.command == "probe":
        print(to_json(probe()))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
