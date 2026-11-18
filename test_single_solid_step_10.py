"""Fast CST 2025 SP1 experiment: export at most 10 actual Solids by slash path.

Do not claim a mapping unless a STEP contains exactly one geometric Solid.
The original CST project is never opened: only a temporary copy is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import time
import traceback
from pathlib import Path

from cst_material.cst_api import connect
from cst_material.cst_bindings import _modeler, _vba_string, read_shape_catalog
from cst_material.occt_geometry import read_step, sha256_file


def build_single_solid_vba(solid_id: str, target: str | Path) -> str:
    component, sep, solid = solid_id.rpartition(":")
    if not sep or not component or not solid:
        raise ValueError(f"无效 CST Solid ID: {solid_id!r}")
    # This slash syntax is experimental; CST 2025 SP1 must confirm it.
    object_path = component.rstrip("/") + "/" + solid
    return "\n".join([
        "With STEP",
        "    .Reset",
        f"    .FileName ({_vba_string(str(target))})",
        "    .ExportFromActiveCoordinateSystem (False)",
        f"    .Write ({_vba_string(object_path)})",
        "End With",
    ])


def export_ten_solids(source_file: str | Path, output_dir: str | Path,
                      *, limit: int = 10, connect_fn=None, catalog_fn=None,
                      execute_fn=None, geometry_fn=None) -> dict:
    if not 1 <= limit <= 10:
        raise ValueError("快速试验最多只能导出 10 个 Solid")
    source = Path(source_file).resolve(strict=True)
    if source.suffix.lower() != ".cst" or not source.is_file():
        raise ValueError("必须提供已有 CST 工程")
    root = Path(output_dir).resolve()
    if root.exists():
        raise FileExistsError(f"禁止覆盖已有目录：{root}")
    root.mkdir(parents=True)
    steps = root / "steps"
    steps.mkdir()
    report = {
        "schema_version": 1, "mode": "EXPERIMENTAL_SLASH_SOLID_PATH",
        "source": str(source), "limit": limit, "items": [], "status": "FAIL",
        "success": 0, "failed": 0,
    }
    start = time.perf_counter()
    def save():
        report["elapsed_seconds"] = round(time.perf_counter() - start, 3)
        (root / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    try:
        with tempfile.TemporaryDirectory(prefix="cst_step_10_") as tmp:
            env = None
            project = None
            try:
                staged = shutil.copy2(source, Path(tmp) / source.name)
                env = (connect_fn or connect)()
                project = env.open_project(str(staged))
                catalog = (catalog_fn or read_shape_catalog)(project)
                selected = list(catalog.items())[:limit]
                report["selected"] = len(selected)
                save()
                for index, (solid_id, material) in enumerate(selected, 1):
                    stem = hashlib.sha256(solid_id.encode("utf-8")).hexdigest()[:16]
                    target = steps / f"{index:02d}_{stem}.step"
                    code = build_single_solid_vba(solid_id, target)
                    row = {"index": index, "solid_id": solid_id, "material": material,
                           "step": target.relative_to(root).as_posix(),
                           "vba": code, "status": "FAIL"}
                    report["items"].append(row)
                    try:
                        if execute_fn is not None:
                            execute_fn(project, code)
                        else:
                            _modeler(project).add_to_history(
                                "CST STEP single Solid path experiment", code
                            )
                        if not target.is_file() or target.stat().st_size < 100:
                            raise RuntimeError("STEP 文件缺失或小于 100 字节")
                        bodies = (geometry_fn or read_step)(target)
                        row["body_count"] = len(bodies)
                        if len(bodies) != 1:
                            raise RuntimeError(
                                f"导出 STEP 有 {len(bodies)} 个几何实体，不是单 Solid"
                            )
                        row["sha256"] = sha256_file(target)
                        row["status"] = "PASS"
                        report["success"] += 1
                    except Exception as exc:  # noqa: BLE001 - preserve vendor failures
                        row["error"] = f"{type(exc).__name__}: {exc}"
                        row["traceback"] = traceback.format_exc()
                        report["failed"] += 1
                        # Stop on first failure: avoid 10 costly invalid CST operations.
                        save()
                        break
                    finally:
                        save()
            finally:
                if project is not None:
                    project.close()
                if env is not None:
                    env.close()
        report["status"] = (
            "PASS" if report["success"] == report.get("selected", 0)
            and report["success"] > 0 else "FAIL"
        )
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
    finally:
        save()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="只验证前 10 个 CST Solid 的独立 STEP 导出")
    parser.add_argument("project", help="原始 CST 工程")
    parser.add_argument("output", help="全新输出目录")
    args = parser.parse_args()
    report = export_ten_solids(args.project, args.output)
    print(json.dumps({k: v for k, v in report.items() if k != "items"},
                     ensure_ascii=False, indent=2))
    print("详细结果:", Path(args.output) / "report.json")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
