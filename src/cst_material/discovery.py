"""Safe, local CST installation interface discovery.

Only directory listings and file names are inspected. No file content is read,
no DLL is loaded, no registry is changed, and CST is not launched.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

TARGET_NAMES = ("cst", "python", "python_cst_libraries", "libraries", "macros", "vba")
TARGET_EXTENSIONS = (".whl", ".pyd", ".tlb")
MAX_DEPTH = 5
MAX_ENTRIES = 20000


def inspect(root: Path) -> dict[str, object]:
    if not root.is_dir():
        return {"status": "BLOCKED", "reason": "安装目录不存在或无权访问"}
    hits: set[str] = set()
    visited = 0
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack and visited < MAX_ENTRIES:
        folder, depth = stack.pop()
        try:
            entries = list(folder.iterdir())
        except (PermissionError, OSError):
            continue
        for entry in entries:
            visited += 1
            if visited >= MAX_ENTRIES:
                break
            try:
                if entry.is_symlink():
                    continue
                is_dir = entry.is_dir()
            except OSError:
                continue
            name = entry.name.lower()
            if is_dir and name in TARGET_NAMES:
                hits.add(name)
            elif not is_dir and entry.suffix.lower() in TARGET_EXTENSIONS:
                hits.add("extension:" + entry.suffix.lower())
            if is_dir and depth < MAX_DEPTH:
                stack.append((entry, depth + 1))
    return {
        "status": "PASS",
        "scan_limited": visited >= MAX_ENTRIES,
        "entries_scanned": visited,
        "features": sorted(hits),
        "note": "仅返回文件类型和目录名称，不包含内部路径或文件内容",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只读发现 CST 安装目录内的接口线索")
    parser.add_argument("--cst-dir", required=True, help="本机 CST 安装根目录")
    args = parser.parse_args(argv)
    result = inspect(Path(os.path.expandvars(args.cst_dir)))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
