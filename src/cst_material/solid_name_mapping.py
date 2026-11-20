"""Conservative name-to-body mapping: only verified, explicitly named files.

Never infer CST identity from STEP body order, geometry count, or filenames.
"""
from __future__ import annotations

import json
from pathlib import Path

from cst_material.occt_geometry import sha256_file


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
        if row.get("sha256") and sha256_file(step) != row["sha256"]:
            raise ValueError(f"STEP 校验和不一致：{sid}")
        result[sid] = {**row, "step": str(step.relative_to(folder))}
    return result
