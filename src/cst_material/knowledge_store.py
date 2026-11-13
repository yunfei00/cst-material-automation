"""Explicit engineer approval of a Solid identity/material from the 3D viewer.

Approved mappings are bound to original CST SHA256, not just a repeatable
Component:Solid string. Material approval is a separate explicit action.
"""
from __future__ import annotations

import json
from pathlib import Path

from cst_material.solid_index import load_catalog
from cst_material.solid_recognition import load_knowledge


def store_confirmation(
    geometry_dir: str | Path,
    solid_id: str,
    part_type: str,
    *,
    material: str = "",
    approve_material: bool = False,
    knowledge_path: str | Path | None = None,
) -> Path:
    root = Path(geometry_dir).resolve(strict=True)
    catalog = load_catalog(root, verify_files=False)
    row = next((item for item in catalog["records"] if item["solid_id"] == solid_id), None)
    if row is None:
        raise ValueError("确认的 Solid ID 不在当前几何索引中")
    part_type = part_type.strip()
    material = material.strip()
    if not part_type:
        raise ValueError("必须明确填写真实零件/子结构类型")
    if approve_material and not material:
        raise ValueError("确认材料前必须填写 CST 中已有的材料名称")
    source_hash = catalog.get("original_source_sha256") or catalog.get("source_sha256")
    if not isinstance(source_hash, str) or not source_hash:
        raise ValueError("无法确认原 CST 工程指纹，拒绝保存不受范围约束的识别信息")

    target = (
        Path(knowledge_path).expanduser()
        if knowledge_path is not None
        else Path.home() / ".cst_material_automation" / f"approved_knowledge_{source_hash}.json"
    )
    if target.is_file():
        knowledge = load_knowledge(target)
    else:
        knowledge = {"schema_version": 1, "confirmed_solids": {}, "reference_shapes": []}
    knowledge["confirmed_solids"][solid_id] = {
        "approved": True,
        "source_sha256": source_hash,
        "part_type": part_type,
        "material_approved": bool(approve_material),
        "material": material if approve_material else None,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = target.with_name(target.name + ".pending")
    with pending.open("x", encoding="utf-8") as stream:
        json.dump(knowledge, stream, ensure_ascii=False, indent=2)
    pending.replace(target)
    return target
