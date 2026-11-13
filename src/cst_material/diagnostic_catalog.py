"""Read component-scoped STEP diagnostic exports without inventing per-Solid mapping."""
from __future__ import annotations

import json
from pathlib import Path


def load_diagnostic_catalog(folder: str | Path) -> dict:
    root = Path(folder).resolve(strict=True)
    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    items = report.get("items")
    if not isinstance(items, list):
        raise ValueError("诊断报告缺少 items")
    records = []
    for item in items:
        if item.get("status") != "PASS":
            continue
        solid_id = item.get("solid_id")
        if not isinstance(solid_id, str) or ":" not in solid_id:
            raise ValueError("诊断报告 Solid ID 无效")
        step = Path(item["step"])
        resolved = step.resolve() if step.is_absolute() else (root / step).resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"STEP 路径超出诊断目录：{step}")
        if not resolved.is_file() or resolved.suffix.lower() not in {".step", ".stp"}:
            raise ValueError(f"诊断 STEP 文件不存在：{resolved}")
        records.append({
            "solid_id": solid_id,
            "step": str(resolved.relative_to(root)),
            "diagnostic_export": True,
            "component": solid_id.partition(":")[0],
            "part_type": "UNKNOWN",
            "candidate": "组件级导出（可能包含多个 Solid）",
            "identity_status": "UNREVIEWED",
            "material": None,
        })
    if not records:
        raise ValueError("诊断报告没有可加载的成功 STEP")
    return {"records": records}
