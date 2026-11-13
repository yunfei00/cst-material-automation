"""Fast, single-Solid CST STEP export diagnostic. Never modifies source CST."""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

from cst_material.cst_api import connect
from cst_material.cst_bindings import read_shape_catalog
from cst_material.cst_step_export import _export_step_batch, build_step_export_vba


def diagnose_one_solid(
    project_file: str | Path,
    solid_id: str,
    output_dir: str | Path,
    *,
    connect_fn=None,
    catalog_fn=None,
    export_fn=None,
) -> dict:
    """Copy input, enumerate shapes, export exactly one selected Solid, log every stage."""
    source = Path(project_file).resolve(strict=True)
    if source.suffix.lower() != ".cst" or not source.is_file():
        raise ValueError("输入必须是现有 .cst 工程")
    if not solid_id or ":" not in solid_id:
        raise ValueError("Solid 名称必须为 Component:Solid")
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise FileExistsError(f"诊断输出目录已存在：{destination}")
    if not destination.parent.is_dir():
        raise FileNotFoundError(destination.parent)
    destination.mkdir()
    target = destination / "selected_solid.step"
    report_path = destination / "diagnostic.json"
    started = time.perf_counter()
    report = {
        "status": "FAIL",
        "source": str(source),
        "requested_solid": solid_id,
        "output_step": str(target),
        "stage": "COPY",
        "started_at": datetime.now().astimezone().isoformat(),
        "events": [],
    }

    def record(stage: str, **details):
        report["stage"] = stage
        event = {"stage": stage, "seconds": round(time.perf_counter() - started, 3)}
        event.update(details)
        report["events"].append(event)
        print(json.dumps(event, ensure_ascii=False), flush=True)

    environment = None
    project = None
    with tempfile.TemporaryDirectory(prefix="cst_single_step_") as temp:
        try:
            staged = Path(temp) / source.name
            record("COPY_START", bytes=source.stat().st_size)
            shutil.copy2(source, staged)
            record("COPY_DONE")
            environment = (connect_fn or connect)()
            record("CST_CONNECT_OK")
            project = environment.open_project(str(staged))
            record("CST_OPEN_OK")
            catalog = (catalog_fn or read_shape_catalog)(project)
            record("CATALOG_OK", solid_count=len(catalog),
                   exact_match=solid_id in catalog)
            if solid_id not in catalog:
                # Do not auto-pick a different Solid: exact identity is required.
                candidates = [
                    name for name in catalog
                    if name.casefold() == solid_id.casefold()
                ][:10]
                record("SOLID_NOT_FOUND", case_insensitive_matches=candidates)
                raise ValueError("未找到完全匹配的 Solid；请检查 Component:Solid 全名")
            code = build_step_export_vba(solid_id, target)
            record("EXPORT_START", vba=code, step_path=str(target))
            (export_fn or _export_step_batch)(project, [(solid_id, target)])
            if not target.is_file():
                raise RuntimeError("STEP.Write 未创建目标文件")
            size = target.stat().st_size
            if size < 100:
                raise RuntimeError(f"STEP 文件过小：{size} 字节")
            record("EXPORT_OK", bytes=size)
            report["status"] = "PASS"
        except Exception as exc:
            record("ERROR", exception_type=type(exc).__name__, message=str(exc))
            report["error"] = f"{type(exc).__name__}: {exc}"
            report["traceback"] = traceback.format_exc()
        finally:
            if project is not None:
                try:
                    project.close()
                    record("CST_PROJECT_CLOSED")
                except Exception as exc:  # noqa: BLE001
                    record("CST_PROJECT_CLOSE_ERROR", message=str(exc))
            if environment is not None:
                try:
                    environment.close()
                    record("CST_ENV_CLOSED")
                except Exception as exc:  # noqa: BLE001
                    record("CST_ENV_CLOSE_ERROR", message=str(exc))
            report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="仅导出一个 CST Solid 的 STEP 诊断")
    parser.add_argument("project", help="现有 CST 工程文件路径")
    parser.add_argument("solid", help="完整 Solid ID，例如 component1:solid1")
    parser.add_argument("output_dir", help="新的诊断输出目录，不能已存在")
    args = parser.parse_args(argv)
    result = diagnose_one_solid(args.project, args.solid, args.output_dir)
    print(f"诊断结果：{result['status']}；阶段：{result['stage']}")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
