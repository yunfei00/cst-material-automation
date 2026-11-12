"""Copy-only CST 2025 deletion executor for confirmed P4-C cleanup plans.

CST 2025 offline VBA help, VBA_3D/Solid Object, documents:
    Solid.Delete(solidname)
This is a structural edit via Model3D.add_to_history, not the P3-C
non-History read-only bbox API. Never call it on the original .cst project.

This deliberately does not modify Component trees, ports, excitations,
meshes, boundaries or materials: it removes only the planned exact Solid IDs.
"""
from __future__ import annotations

import shutil
import tempfile
import warnings
from pathlib import Path

from cst_material.p4_cleanup import (
    _digest_catalog,
    _load_json,
    _sha256_file,
    validate_plan_for_apply,
)


def _delete_solids(project, ids: list[str], batch_size: int = 20) -> None:
    """Batch the confirmed CST Solid.Delete commands into structural history."""
    from cst_material.cst_bindings import _modeler, _vba_string

    if not 1 <= batch_size <= 50:
        raise ValueError("每批删除 Solid 数量须为 1～50")
    for start in range(0, len(ids), batch_size):
        group = ids[start:start + batch_size]
        code = "\n".join(f"Solid.Delete {_vba_string(name)}" for name in group)
        try:
            _modeler(project).add_to_history(
                f"CST cleanup R001 + explicit {start + 1}-{start + len(group)}",
                code,
            )
        except Exception as exc:
            raise RuntimeError(
                f"CST R001 删除批次 {start + 1}～{start + len(group)} 失败，"
                f"全部计划 {len(ids)} 个 Solid；副本尚未通过验收"
            ) from exc
        print(f"P4-C R001 删除进度：{start + len(group)}/{len(ids)}", flush=True)


def apply_cleanup_plan(
    source: Path,
    destination: Path,
    plan_path: Path,
    *,
    batch_size: int = 20,
    environment=None,
    catalog_reader=None,
    delete_executor=None,
) -> dict:
    """Apply all selected deletions on a new .cst copy and verify post-readback.

    A failed write or a mismatched post-readback NEVER reports success. The
    failed copy may remain on disk for diagnostic inspection; never delete
    potentially CST-locked files automatically.
    """
    from cst_material.cst_api import connect
    from cst_material.cst_bindings import read_shape_catalog

    if not 1 <= batch_size <= 50:
        raise ValueError("每批删除 Solid 数量须为 1～50")
    plan = _load_json(plan_path)
    selected, expected_before_count = validate_plan_for_apply(plan, source)
    original = source.resolve(strict=True)
    dest = destination.resolve()
    if dest == original or dest.suffix.casefold() != ".cst":
        raise ValueError("输出必须是另一个新的 .cst 文件，禁止覆盖原工程")
    if dest.exists():
        raise FileExistsError("目标工程已存在；为避免覆盖请使用新文件名")
    if not dest.parent.is_dir():
        raise FileNotFoundError("目标目录不存在")

    reader = catalog_reader or read_shape_catalog
    remove = delete_executor or _delete_solids
    env = environment if environment is not None else connect()
    owned_env = environment is None
    project = None
    # CST may release a closed project's Windows file handles asynchronously.
    # Read the preflight catalog from a disposable copy *separate* from the
    # eventual output. This avoids overwriting a CST-locked .cst file.
    preflight_dir = Path(tempfile.mkdtemp(prefix="cst_cleanup_preflight_"))
    preflight = preflight_dir / original.name
    cleanup_diagnostics: dict = {}
    result: dict | None = None
    try:
        shutil.copy2(original, preflight)
        project = env.open_project(str(preflight))
        try:
            before = {name: material or None for name, material in reader(project).items()}
        finally:
            project.close()
            project = None
        if len(before) != expected_before_count:
            raise RuntimeError("CST 当前实体数量与 P1 记录不一致，不执行删除")
        if _digest_catalog(before) != plan.get("inventory_catalog_sha256"):
            raise RuntimeError("CST 实际 Solid/材料清单与 P1 不一致，不执行删除")
        if not set(selected) <= before.keys():
            raise RuntimeError("删除列表引用了当前 CST 中不存在的实体")
        if _sha256_file(original) != plan.get("source_sha256"):
            raise RuntimeError("处理期间源工程发生变化，停止删除")

        # Only create the final deliverable after preflight passes.
        # Exclusive creation makes it impossible to overwrite an existing file.
        with original.open("rb") as src, dest.open("xb") as target:
            shutil.copyfileobj(src, target, length=4 * 1024 * 1024)
        print(f"P4-C 新工程副本已创建：{dest}", flush=True)
        project = env.open_project(str(dest))
        remove(project, selected, batch_size=batch_size)
        project.save(str(dest), allow_overwrite=True)
        project.close()
        project = None
        project = env.open_project(str(dest))
        observed = {name: material or None for name, material in reader(project).items()}
        expected = {name: mat for name, mat in before.items() if name not in selected}
        if observed != expected:
            missing = sorted(expected.keys() - observed.keys())
            not_deleted = sorted(set(selected) & set(observed))
            changed = sorted(
                name for name in expected.keys() & observed.keys()
                if expected[name] != observed[name]
            )
            raise RuntimeError(
                "CST 清理结果回读不一致："
                f"误删={missing[:5]}，未删除={not_deleted[:5]}，"
                f"材料变化={changed[:5]}；不标记 PASS"
            )
        result = {
            "task": "cleanup-apply",
            "status": "PASS",
            "stage": "CST 工程副本删除完成，剩余 Solid 和材料逐项回读一致",
            "source": str(original),
            "destination": str(dest),
            "original_solid_count": len(before),
            "deleted_solid_count": len(selected),
            "remaining_solid_count": len(observed),
            "rule_r001_zero_volume_count": plan.get("zero_volume_delete_count"),
            "deleted_ids": selected,
            "all_remaining_materials_unchanged": True,
            "note": (
                "只验证删除集合与材料清单，端口、网格、激励和仿真结果"
                "的有效性仍需在 CST 副本中检查；不能复用删除前的 P3 数据"
            ),
        }
    finally:
        # Cleanup failures must never conceal a more important VBA/COM error.
        if project is not None:
            try:
                project.close()
            except Exception as exc:  # noqa: BLE001 - CST vendor exception
                warnings.warn(f"P4-C 关闭临时 CST 工程失败：{exc}", RuntimeWarning, stacklevel=2)
        if owned_env:
            try:
                env.close()
            except Exception as exc:  # noqa: BLE001 - CST vendor exception
                warnings.warn(f"P4-C 关闭 CST 接口失败：{exc}", RuntimeWarning, stacklevel=2)
        from cst_material.p3_spatial import _cleanup_workdir

        cleaned = _cleanup_workdir(preflight_dir, cleanup_diagnostics)
        if result is not None:
            result["preflight_cleanup_deferred"] = not cleaned
            result["preflight_cleanup_diagnostics"] = cleanup_diagnostics
            if not cleaned:
                result["status"] = "PARTIAL"
                result["preflight_cleanup_path"] = str(preflight_dir)
    assert result is not None
    return result
