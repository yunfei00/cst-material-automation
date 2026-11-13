#!/usr/bin/env python
r"""CST 工程零体积清理：只需要输入 .cst 和输出 .cst 两个参数。

    python clean_cst.py "D:\models\original.cst" "D:\models\cleaned.cst"

也可以 Python import 后直接调用:
    from clean_cst import clean_cst
    clean_cst(r"D:\models\original.cst", r"D:\models\cleaned.cst")
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Run from any working directory; do not require pip install, uv, or reports.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cst_material.simple_cleanup import clean_project


def clean_cst(input_cst: str | Path, output_cst: str | Path) -> dict:
    """唯一的业务接口：输入旧工程路径，输出新工程路径。"""
    return clean_project(input_cst, output_cst)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="CST 2025：删除所有体积为零的 Solid，保存为新的 CST 工程。"
    )
    parser.add_argument("input_cst", help="输入原工程 .cst")
    parser.add_argument("output_cst", help="输出新工程 .cst（不能已存在）")
    args = parser.parse_args(argv)
    try:
        result = clean_cst(args.input_cst, args.output_cst)
    except Exception as exc:  # noqa: BLE001 - CST vendor-specific exceptions
        print(f"失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        print("原工程未作为删除目标；请查看新工程同名 .cleanup.json 日志。", file=sys.stderr)
        return 1
    print(
        f"原 Solid {result['original_solid_count']}，"
        f"删除 {result['deleted_solid_count']}，"
        f"保留 {result['remaining_solid_count_expected']}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
