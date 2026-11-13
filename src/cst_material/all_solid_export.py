"""Enumerate and independently export every Solid in a disposable CST copy."""
from __future__ import annotations

import argparse
import hashlib
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


def export_all_solids(
    project_file: str | Path, *, connect_fn=None, catalog_fn=None, export_fn=None,
) -> dict:
    source = Path(project_file).resolve(strict=True)
    if not source.is_file() or source.suffix.lower() != ".cst":
        raise ValueError("请输入现有的 .cst 工程路径")
    output = source.parent / (source.stem + ".solid_export_diagnostic")
    if output.exists():
        raise FileExistsError(f"输出目录已存在，避免覆盖：{output}")
    output.mkdir()
    steps = output / "steps"
    steps.mkdir()
    report_file = output / "report.json"
    started = time.perf_counter()
    report = {
        "source": str(source), "output": str(output),
        "started_at": datetime.now().astimezone().isoformat(),
        "status": "FAIL", "total": 0, "success": 0, "failed": 0,
        "items": [], "events": [],
    }

    def log(stage, **fields):
        event = {"stage": stage, "elapsed_seconds": round(time.perf_counter() - started, 3)}
        event.update(fields)
        report["events"].append(event)
        print(json.dumps(event, ensure_ascii=False), flush=True)

    def persist():
        report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        report_file.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    env = None
    project = None
    try:
        with tempfile.TemporaryDirectory(prefix="cst_export_all_") as temp:
            try:
                staged = Path(temp) / source.name
                log("COPY_START", source_bytes=source.stat().st_size)
                shutil.copy2(source, staged)
                log("COPY_OK")
                env = (connect_fn or connect)()
                log("CONNECT_OK")
                project = env.open_project(str(staged))
                log("OPEN_OK")
                catalog = (catalog_fn or read_shape_catalog)(project)
                if not isinstance(catalog, dict) or not catalog:
                    raise RuntimeError("CST 未枚举到 Solid")
                report["total"] = len(catalog)
                log("CATALOG_OK", solid_count=len(catalog), solid_names=list(catalog))
                persist()
                for index, name in enumerate(catalog, 1):
                    began = time.perf_counter()
                    file_id = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
                    target = steps / f"{index:06d}_{file_id}.step"
                    item = {
                        "index": index, "solid_id": name, "step": str(target),
                        "status": "FAIL", "vba": None, "bytes": 0,
                    }
                    report["items"].append(item)
                    try:
                        item["vba"] = build_step_export_vba(name, target)
                        log("SOLID_START", index=index, solid_id=name, vba=item["vba"])
                        (export_fn or _export_step_batch)(project, [(name, target)])
                        if not target.is_file() or target.stat().st_size < 100:
                            raise RuntimeError("STEP 文件不存在或小于 100 字节")
                        item["bytes"] = target.stat().st_size
                        item["status"] = "PASS"
                        report["success"] += 1
                        log("SOLID_PASS", index=index, solid_id=name, bytes=item["bytes"])
                    except Exception as exc:  # noqa: BLE001 - continue after CST vendor errors
                        item["error_type"] = type(exc).__name__
                        item["error"] = str(exc)
                        item["traceback"] = traceback.format_exc()
                        report["failed"] += 1
                        log("SOLID_FAIL", index=index, solid_id=name,
                            error_type=type(exc).__name__, error=str(exc))
                    finally:
                        item["elapsed_seconds"] = round(time.perf_counter() - began, 3)
                        persist()
                report["status"] = "PASS" if not report["failed"] else "PARTIAL"
                log("FINISHED", total=report["total"], success=report["success"],
                    failed=report["failed"])
            finally:
                if project is not None:
                    try:
                        project.close()
                        log("PROJECT_CLOSED")
                    except Exception as exc:  # noqa: BLE001 - log vendor cleanup errors
                        log("PROJECT_CLOSE_FAILED", error=str(exc))
                if env is not None:
                    try:
                        env.close()
                        log("ENV_CLOSED")
                    except Exception as exc:  # noqa: BLE001 - log vendor cleanup errors
                        log("ENV_CLOSE_FAILED", error=str(exc))
    except Exception as exc:  # noqa: BLE001 - preserve full diagnostic on startup failure
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
        log("FATAL", error=report["error"])
    finally:
        persist()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="自动枚举并逐个导出 CST 全部 Solid")
    parser.add_argument("project", help="唯一参数：.cst 工程路径")
    args = parser.parse_args(argv)
    report = export_all_solids(args.project)
    print(f"结果：{report['status']}，总数={report['total']}，"
          f"成功={report['success']}，失败={report['failed']}")
    print(f"详细报告：{Path(report['output']) / 'report.json'}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
