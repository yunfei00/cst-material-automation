#!/usr/bin/env python
"""CST 自动识别与赋材：输入 .cst + 输出 .cst，仅需两个路径。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from cst_material.platform_pipeline import process_project


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CST 结构清理、识别、材料匹配与新工程输出")
    parser.add_argument("input_cst")
    parser.add_argument("output_cst")
    args = parser.parse_args(argv)
    try:
        result = process_project(args.input_cst, args.output_cst)
    except Exception as exc:  # noqa: BLE001 - CLI reports vendor errors
        print(f"执行失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        print("请查看输出文件同名的 .analysis.json 诊断报告", file=sys.stderr)
        return 1
    print(f"结果：{result['status']}")
    print(f"输出工程：{result['output_cst']}")
    print(f"几何数据库：{result['geometry_directory']}")
    print(f"报告：{result['report_file']}")
    if result["status"] == "NEEDS_REVIEW":
        print("注意：工程副本已保存，但有 Solid 身份/材料未经确认。")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
