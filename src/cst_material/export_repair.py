"""Repair a completed per-Solid export without opening CST or re-exporting STEP.

Usage: uv run --extra cad python repair_export.py PATH_TO_EXPORT_FOLDER
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

from cst_material.occt_geometry import read_step, sha256_file


def inspect_existing_step(path: Path) -> tuple[str, int]:
    """Accept a surface only when STEP transfer and every FACE pass BRepCheck."""
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    try:
        bodies = read_step(path)
    except ValueError as exc:
        if "未包含封闭 Solid" not in str(exc):
            raise
    else:
        return "PASS", len(bodies)
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise ValueError("STEP 读取失败")
    if reader.TransferRoots() < 1:
        raise ValueError("STEP 没有可转换形状")
    shape = reader.OneShape()
    if shape.IsNull() or not BRepCheck_Analyzer(shape).IsValid():
        raise ValueError("STEP 拓扑无效")
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    count = 0
    while explorer.More():
        face = (getattr(TopoDS, "Face_s", None) or TopoDS.Face)(explorer.Current())
        if face.IsNull() or not BRepCheck_Analyzer(face).IsValid():
            raise ValueError("STEP 包含无效面")
        count += 1
        explorer.Next()
    if not count:
        raise ValueError("STEP 没有有效面")
    return "PASS_SURFACE", count


def repair_export(folder: str | Path, *, dry_run: bool = False) -> dict:
    root = Path(folder).resolve(strict=True)
    report_path = root / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("mode") != "PER_SOLID_COLON_V2":
        raise ValueError("只支持 PER_SOLID_COLON_V2 导出报告")
    items = report.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("报告没有导出条目")
    # Preserve the original report and all STEP files. Only previously failed
    # entries are inspected, so 8000 valid files do not require re-parsing.
    changed = 0
    for item in items:
        if item.get("status") != "FAIL":
            continue
        relative = item.get("step")
        if not isinstance(relative, str):
            continue
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or path.suffix.lower() not in {".step", ".stp"}:
            continue
        if not path.is_file() or path.stat().st_size < 100:
            continue
        try:
            status, count = inspect_existing_step(path)
        except Exception as exc:  # noqa: BLE001 - keep failures visible
            item["repair_error"] = f"{type(exc).__name__}: {exc}"
            continue
        item.update(status=status, sha256=sha256_file(path),
                    bytes=path.stat().st_size, repaired_from_existing_step=True)
        if status == "PASS":
            item["body_count"] = count
        else:
            item["body_count"] = 0
            item["face_count"] = count
        item.pop("error", None)
        item.pop("error_type", None)
        item.pop("traceback", None)
        changed += 1
    report["success"] = sum(item.get("status") in {"PASS", "PASS_SURFACE"} for item in items)
    report["failed"] = sum(item.get("status") not in {"PASS", "PASS_SURFACE"} for item in items)
    report["surface_count"] = sum(item.get("status") == "PASS_SURFACE" for item in items)
    report["status"] = "PASS" if report["failed"] == 0 else "PARTIAL"
    report["repaired_at"] = datetime.now().astimezone().isoformat()
    summary = {"recovered": changed, "success": report["success"],
               "surface": report["surface_count"], "failed": report["failed"],
               "dry_run": dry_run}
    if dry_run:
        return summary

    # v1 manifest is intentionally SOLID-only because build_catalog calls
    # analyze_step() on every entry. Surface entries stay in report.json.
    valid_solids = [item for item in items if item.get("status") == "PASS"]
    manifest = {
        "schema_version": 1, "unit": "mm",
        "coordinate_frame": "CST_GLOBAL_EXPORT_REQUESTED_NOT_YET_REAL_VERIFIED",
        "source": report.get("source"), "source_sha256": report.get("source_sha256"),
        "solid_count": len(valid_solids),
        "solids": [
            {key: item[key] for key in ("solid_id", "material", "step", "sha256")}
            for item in valid_solids
        ],
        "excluded_surface_count": report["surface_count"],
        "excluded_failed_count": report["failed"],
    }
    if not valid_solids:
        raise ValueError("没有有效封闭实体，不生成 manifest")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    for name in ("report.json", "manifest.json"):
        original = root / name
        if original.exists():
            shutil.copy2(original, root / f"{name}.backup-{stamp}")
    # Write manifest first; on a write error the original report is untouched.
    for filename, data in (("manifest.json", manifest), ("report.json", report)):
        destination = root / filename
        temporary = root / f".{filename}.{stamp}.tmp"
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="修复现有 STEP 导出报告（不启动 CST）")
    parser.add_argument("folder", help="*.solid_export_diagnostic 目录")
    parser.add_argument("--dry-run", action="store_true", help="只检查，不修改文件")
    args = parser.parse_args(argv)
    result = repair_export(args.folder, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
