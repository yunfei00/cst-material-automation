"""Export each CST Component:Solid to its own STEP; resume safely."""
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
from cst_material.occt_geometry import read_step, sha256_file


def export_all_solids(
    project_file: str | Path, *, connect_fn=None, catalog_fn=None, export_fn=None,
    geometry_fn=None, limit: int | None = None,
) -> dict:
    source = Path(project_file).resolve(strict=True)
    if not source.is_file() or source.suffix.lower() != ".cst":
        raise ValueError("请输入现有的 .cst 工程路径")
    if limit is not None and (not isinstance(limit, int) or limit < 1):
        raise ValueError("limit 必须是正整数")
    output = source.parent / (source.stem + ".solid_export_diagnostic")
    output.mkdir(exist_ok=True)
    steps = output / "steps"
    steps.mkdir(exist_ok=True)
    report_file = output / "report.json"
    manifest_file = output / "manifest.json"
    source_hash = sha256_file(source)
    previous = {}
    if report_file.exists():
        old = json.loads(report_file.read_text(encoding="utf-8"))
        if old.get("mode") != "PER_SOLID_COLON_V2":
            raise RuntimeError("输出目录包含旧版组件级数据，请先重命名或移走旧目录")
        if old.get("source_sha256") != source_hash:
            raise RuntimeError("原工程已改变，不能复用旧导出结果")
        previous = {x["solid_id"]: x for x in old.get("items", [])}
    started = time.perf_counter()
    report = {
        "mode": "PER_SOLID_COLON_V2", "source": str(source),
        "source_sha256": source_hash, "output": str(output),
        "started_at": datetime.now().astimezone().isoformat(),
        "status": "FAIL", "total": 0, "success": 0, "failed": 0,
        "items": [], "events": [],
    }

    def persist():
        report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    env = None
    project = None
    try:
        with tempfile.TemporaryDirectory(prefix="cst_export_solids_") as temp:
            try:
                staged = Path(temp) / source.name
                shutil.copy2(source, staged)
                env = (connect_fn or connect)()
                project = env.open_project(str(staged))
                catalog = (catalog_fn or read_shape_catalog)(project)
                if not isinstance(catalog, dict) or not catalog:
                    raise RuntimeError("CST 未枚举到 Solid")
                selected = list(catalog.items())
                if limit is not None:
                    selected = selected[:limit]
                report["total"] = len(selected)
                persist()
                for index, (solid_id, material) in enumerate(selected, 1):
                    if not isinstance(solid_id, str) or ":" not in solid_id:
                        raise ValueError(f"非法 Solid ID：{solid_id!r}")
                    file_id = hashlib.sha256(solid_id.encode("utf-8")).hexdigest()[:20]
                    relative = f"steps/{index:06d}_{file_id}.step"
                    target = output / relative
                    prior = previous.get(solid_id)
                    item = {
                        "index": index, "solid_id": solid_id,
                        "component": solid_id.rpartition(":")[0],
                        "material": material, "step": relative,
                        "status": "FAIL", "vba": build_step_export_vba(solid_id, target),
                    }
                    report["items"].append(item)
                    try:
                        if (prior and prior.get("status") == "PASS"
                            and prior.get("step") == relative
                            and prior.get("sha256")
                            and target.is_file()
                            and sha256_file(target) == prior["sha256"]):
                            item.update(status="PASS", sha256=prior["sha256"],
                                        bytes=target.stat().st_size,
                                        body_count=prior.get("body_count"),
                                        resumed=True)
                        else:
                            # Never treat stale or partial files as successful exports.
                            if target.exists():
                                target.unlink()
                            (export_fn or _export_step_batch)(project, [(solid_id, target)])
                            if not target.is_file() or target.stat().st_size < 100:
                                raise RuntimeError("STEP 文件不存在或小于 100 字节")
                            bodies = (geometry_fn or read_step)(target)
                            if not bodies:
                                raise RuntimeError("STEP 中没有有效的封闭几何实体")
                            item.update(status="PASS", bytes=target.stat().st_size,
                                        body_count=len(bodies), sha256=sha256_file(target))
                        report["success"] += 1
                    except Exception as exc:  # noqa: BLE001 - continue and log each Solid
                        item["error_type"] = type(exc).__name__
                        item["error"] = str(exc)
                        item["traceback"] = traceback.format_exc()
                        report["failed"] += 1
                    finally:
                        persist()
                        print(f"Solid STEP: {index}/{len(selected)} "
                              f"PASS={report['success']} FAIL={report['failed']}",
                              flush=True)
            finally:
                if project is not None:
                    try:
                        project.close()
                    except Exception as exc:  # noqa: BLE001 - CST vendor cleanup
                        report["events"].append({"stage": "CLOSE_ERROR", "error": str(exc)})
                if env is not None:
                    try:
                        env.close()
                    except Exception as exc:  # noqa: BLE001 - CST vendor cleanup
                        report["events"].append({"stage": "ENV_CLOSE_ERROR", "error": str(exc)})
        if sha256_file(source) != source_hash:
            raise RuntimeError("导出期间原工程发生变化")
        report["status"] = "PASS" if report["total"] and not report["failed"] else "PARTIAL"
        if report["status"] == "PASS":
            manifest = {
                "schema_version": 1, "unit": "mm",
                "coordinate_frame": "CST_GLOBAL_EXPORT_REQUESTED_NOT_YET_REAL_VERIFIED",
                "source": str(source), "source_sha256": source_hash,
                "solid_count": len(report["items"]),
                "solids": [
                    {k: item[k] for k in ("solid_id", "material", "step", "sha256")}
                    for item in report["items"]
                ],
            }
            manifest_file.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        else:
            manifest_file.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        report["status"] = "FAIL"
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
        manifest_file.unlink(missing_ok=True)
    finally:
        persist()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="逐 CST Solid 独立导出 STEP，支持断点续导")
    parser.add_argument("project", help=".cst 工程路径")
    parser.add_argument("--limit", type=int, default=None, help="可选：只导出前 N 个")
    args = parser.parse_args(argv)
    report = export_all_solids(args.project, limit=args.limit)
    print(f"结果：{report['status']}，总数={report['total']}，"
          f"成功={report['success']}，失败={report['failed']}")
    print(f"报告：{Path(report['output']) / 'report.json'}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
