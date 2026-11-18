"""Conservative name-to-body mapping: only verified, explicitly named files.

Never infer CST identity from STEP body order, geometry count, or filenames.
"""
from __future__ import annotations

import json
from pathlib import Path


def load_verified_mapping(root: str | Path) -> dict[str, dict]:
    folder = Path(root).resolve(strict=True)
    path = folder / "verified_solid_mapping.json"
    if not path.is_file():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema_version") != 1:
        raise ValueError("不支持的 Solid 映射格式")
    result = {}
    for row in doc["solids"]:
        if row.get("verification") != "ISOLATED_SINGLE_BODY":
            raise ValueError("拒绝未经独立几何验证的 Solid 映射")
        sid = row["solid_id"]
        if not isinstance(sid, str) or ":" not in sid or sid in result:
            raise ValueError("Solid 名称为空或重复")
        step = (folder / row["step"]).resolve()
        if not step.is_relative_to(folder) or not step.is_file():
            raise ValueError(f"Solid 映射文件无效：{sid}")
        result[sid] = {**row, "step": str(step.relative_to(folder))}
    return result


def isolated_export_vba(solid_id: str, siblings: list[str], target: str | Path) -> str:
    """Experimental CST VBA: delete other leaf siblings on a disposable copy.

    Must never run on the original CST project. Real CST 2025 SP1 acceptance
    is required before accepting the resulting file as verified.
    """
    from cst_material.cst_bindings import _vba_string
    from cst_material.cst_step_export import build_step_export_vba

    if not isinstance(solid_id, str) or ":" not in solid_id:
        raise ValueError("Solid ID 无效")
    component = solid_id.rsplit(":", 1)[0]
    if not siblings or solid_id not in siblings or len(set(siblings)) != len(siblings):
        raise ValueError("需要完整、无重复的同组件 Solid 清单")
    if any(s.rsplit(":", 1)[0] != component for s in siblings):
        raise ValueError("禁止删除其他组件的 Solid")
    lines = [f"Solid.Delete ({_vba_string(s)})" for s in siblings if s != solid_id]
    lines.append(build_step_export_vba(solid_id, target))
    return "\n".join(lines)
