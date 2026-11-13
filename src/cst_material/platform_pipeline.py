"""Single-business-entry orchestration for CST cleanup, recognition and materials.

The two file paths are mandatory; knowledge is optional and local. All
intermediate artifacts and logs are managed internally. Unknown identities
produce NEEDS_REVIEW, not invented material assignments.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
import traceback
import warnings
from pathlib import Path

from cst_material.cst_step_export import export_cst_solids
from cst_material.material_matching import match_materials
from cst_material.occt_geometry import sha256_file
from cst_material.simple_cleanup import clean_project
from cst_material.solid_index import build_catalog
from cst_material.solid_recognition import load_knowledge, recognize_catalog


def _default_knowledge_file(source: Path) -> Path | None:
    """Existing optional local knowledge file, no setup step required."""
    source_hash = sha256_file(source)
    project_file = source.parent / "approved_knowledge.json"
    per_project = (
        Path.home() / ".cst_material_automation"
        / f"approved_knowledge_{source_hash}.json"
    )
    legacy_global = Path.home() / ".cst_material_automation" / "approved_knowledge.json"
    for candidate in (project_file, per_project, legacy_global):
        if candidate.is_file():
            return candidate
    return None


def _apply_verified_assignments(
    input_cst: Path, output_cst: Path, mapping: dict[str, str]
) -> None:
    from cst_material.cst_api import assign_and_verify, connect
    from cst_material.cst_bindings import binding

    env = connect()
    try:
        assign_and_verify(env, input_cst, output_cst, mapping, binding())
    finally:
        env.close()


def process_project(
    input_cst: str | Path,
    output_cst: str | Path,
    *,
    knowledge_file: str | Path | None = None,
) -> dict:
    """Source CST -> R001 -> local geometry -> identity/materials -> new CST.

    The geometry database is saved beside output as output_stem.geometry/
    to support PySide6 3D browsing without reopening CST.
    """
    src = Path(input_cst).resolve(strict=True)
    dst = Path(output_cst).resolve()
    if src.suffix.lower() != ".cst" or not src.is_file():
        raise ValueError("输入必须为有效 CST 工程")
    if dst.suffix.lower() != ".cst" or src == dst:
        raise ValueError("输出必须为另一份新的 .cst 工程")
    cache = dst.with_suffix(".geometry")
    report_path = dst.with_suffix(".analysis.json")
    if any(path.exists() for path in (dst, cache, report_path)):
        raise FileExistsError("输出 CST、几何数据库或结果日志已经存在，禁止覆盖")
    if not dst.parent.is_dir():
        raise FileNotFoundError(dst.parent)
    started = time.perf_counter()
    original_hash = sha256_file(src)
    stage_dir = Path(tempfile.mkdtemp(prefix="cst_material_pipeline_"))
    staged_cst = stage_dir / "cleaned.cst"
    report: dict = {
        "schema_version": 1,
        "input_cst": str(src),
        "output_cst": str(dst),
        "geometry_directory": str(cache),
        "report_file": str(report_path),
        "status": "FAIL",
        "stage": "STARTED",
    }
    try:
        report["stage"] = "R001_CLEANUP"
        cleaned = clean_project(src, staged_cst)
        if cleaned.get("status") != "PASS":
            raise RuntimeError("R001 清理未通过完整验收")
        report["cleanup"] = {
            "deleted_count": cleaned["deleted_solid_count"],
            "deleted_ids": cleaned["deleted_ids"],
            "remaining_count": cleaned["remaining_solid_count_expected"],
        }
        report["stage"] = "CST_STEP_EXPORT"
        export_result = export_cst_solids(staged_cst, cache)
        report["step_export_count"] = export_result["solid_count"]
        # Keep approved Solid-ID labels scoped to the actual INPUT model.
        # A Component:Solid name alone may occur in unrelated projects.
        manifest_path = cache / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["original_source_sha256"] = original_hash
        manifest["original_source"] = str(src)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        report["stage"] = "OFFLINE_OCCT_INDEX"
        catalog = build_catalog(cache)
        if catalog["solid_count"] != cleaned["remaining_solid_count_expected"]:
            raise RuntimeError(
                "STEP CAD 索引 Solid 数量与 CST 已验证保留数量不一致；"
                "可能存在合并/丢失的实体或几何无效"
            )
        choice = Path(knowledge_file) if knowledge_file is not None else (
            _default_knowledge_file(src)
        )
        knowledge = load_knowledge(choice)
        report["knowledge_file"] = str(choice) if choice else None

        report["stage"] = "PART_IDENTITY_RECOGNITION"
        recognized = recognize_catalog(catalog, knowledge)
        report["recognition"] = recognized

        report["stage"] = "MATERIAL_MATCHING"
        planned = match_materials(catalog, recognized, knowledge)
        report["material_decisions"] = planned["decisions"]
        report["needs_review_ids"] = planned["unknown_ids"]
        report["material_change_count"] = len(planned["assignments"])

        # Keep the standalone 3D viewer useful without reopening CST or
        # loading the full analysis log. The viewer can show identity evidence.
        recognized_by_id = {x["solid_id"]: x for x in recognized}
        material_by_id = {x["solid_id"]: x for x in planned["decisions"]}
        for row in catalog["records"]:
            name = row["solid_id"]
            item = recognized_by_id[name]
            row["part_type"] = item["identity"]
            row["identity_status"] = item["status"]
            row["candidate"] = item["candidate"]
            row["identity_evidence"] = item["evidence"]
            row["material_decision"] = material_by_id[name]
        (cache / "catalog.json").write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        report["stage"] = "WRITE_AND_VERIFY_CST"
        if planned["assignments"]:
            _apply_verified_assignments(staged_cst, dst, planned["assignments"])
        else:
            # R001 already read back and verified the staged project. Byte copy
            # is sufficient if no material modifications were authorized.
            with staged_cst.open("rb") as source_stream, dst.open("xb") as out:
                shutil.copyfileobj(source_stream, out, length=4 * 1024 * 1024)
            if sha256_file(dst) != sha256_file(staged_cst):
                raise RuntimeError("无赋材变更时的 CST 新副本完整性校验失败")
        if sha256_file(src) != original_hash:
            raise RuntimeError("原 CST 在处理期间改变，禁止标记完成")
        report["stage"] = "FINISHED"
        report["status"] = "NEEDS_REVIEW" if planned["unknown_ids"] else "PASS"
        report["message"] = (
            "工程副本已输出，但仍有 Solid 身份/材料待确认"
            if planned["unknown_ids"] else "工程输出及材料核对成功"
        )
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
        raise
    finally:
        report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        try:
            with report_path.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
        except OSError as exc:
            warnings.warn(f"无法写本地验收报告：{exc}", RuntimeWarning, stacklevel=2)
        try:
            shutil.rmtree(stage_dir)
        except OSError as exc:
            warnings.warn(
                f"临时 CST 项目文件被锁定，未强制清理：{stage_dir}，{exc}",
                RuntimeWarning, stacklevel=2,
            )
    return report
