"""Manifest-backed offline Solid index; identity is NEVER inferred from STEP order."""
from __future__ import annotations

import json
import math
from pathlib import Path

from cst_material.occt_geometry import analyze_step, classify_point, read_step, sha256_file


def _inside_folder(folder: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("缺少 STEP 文件路径")
    candidate = (folder / relative).resolve()
    if not candidate.is_relative_to(folder.resolve()):
        raise ValueError("STEP 文件路径不可越过索引文件夹")
    return candidate


def load_manifest(folder: str | Path) -> dict:
    root = Path(folder).resolve(strict=True)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("unit") != "mm":
        raise ValueError("只接受明确映射的 v1 清单及 mm 世界坐标")
    items = manifest.get("solids")
    if not isinstance(items, list) or not items:
        raise ValueError("manifest 缺少非空 solids 列表")
    names = []
    paths = []
    for entry in items:
        if not isinstance(entry, dict) or not isinstance(entry.get("solid_id"), str):
            raise ValueError("无效的 Solid 记录")
        name = entry["solid_id"]
        if not name or name in names or ":" not in name:
            raise ValueError("Solid ID 为空、重复或不是完整 Component:Solid")
        names.append(name)
        path = _inside_folder(root, entry.get("step"))
        if not path.is_file() or path.suffix.casefold() not in {".step", ".stp"}:
            raise ValueError(f"STEP 文件不存在：{path}")
        if path in paths:
            raise ValueError("不同 Solid 不可引用同一 STEP 文件")
        paths.append(path)
        expected_hash = entry.get("sha256")
        if expected_hash and expected_hash != sha256_file(path):
            raise ValueError(f"STEP 几何文件已发生变化：{name}")
    return manifest


def build_catalog(folder: str | Path) -> dict:
    """Build exact B-rep feature cache after checking original per-Solid mapping."""
    root = Path(folder).resolve(strict=True)
    manifest = load_manifest(root)
    catalog_path = root / "catalog.json"
    records = []
    for item in manifest["solids"]:
        geometry = _inside_folder(root, item["step"])
        feature = analyze_step(geometry)
        if feature["volume_mm3"] <= 0:
            # Zero-volume shapes belong to R001; no need to speculate identity.
            continue
        records.append({
            "solid_id": item["solid_id"],
            "component": item["solid_id"].rpartition(":")[0],
            "material": item.get("material"),
            "step": item["step"],
            "features": feature,
            "part_type": "UNKNOWN",
            "identity_status": "UNREVIEWED",
        })
    result = {
        "schema_version": 1,
        "unit": "mm",
        "source": manifest.get("source"),
        "source_sha256": manifest.get("source_sha256"),
        "solid_count": len(records),
        "records": records,
    }
    if catalog_path.exists():
        raise FileExistsError(f"已有索引，禁止意外覆盖：{catalog_path}")
    catalog_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def load_catalog(folder: str | Path) -> dict:
    root = Path(folder).resolve(strict=True)
    catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    manifest = load_manifest(root)
    if catalog.get("schema_version") != 1 or catalog.get("unit") != "mm":
        raise ValueError("无效的几何索引")
    records = catalog.get("records")
    if not isinstance(records, list) or len(records) != catalog.get("solid_count"):
        raise ValueError("几何索引数量不一致")
    allowed = {e["solid_id"]: e for e in manifest["solids"]}
    names = set()
    for rec in records:
        name = rec.get("solid_id")
        if name not in allowed or name in names or rec.get("step") != allowed[name]["step"]:
            raise ValueError("几何索引映射错误或存在重复")
        names.add(name)
        if rec["features"]["step_sha256"] != sha256_file(
            _inside_folder(root, rec["step"])
        ):
            raise ValueError(f"几何索引过期：{name}")
    if catalog.get("source_sha256") != manifest.get("source_sha256"):
        raise ValueError("源工程的索引版本不匹配")
    return catalog


def find_solids_containing_point(
    folder: str | Path, point: tuple[float, float, float], tolerance: float = 1e-6
) -> list[dict]:
    """AABB prefilter followed by exact OCP classification, no CST required."""
    if len(point) != 3 or not all(math.isfinite(float(x)) for x in point):
        raise ValueError("点坐标必须是三个有限数字")
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("tolerance 必须是正数")
    root = Path(folder).resolve(strict=True)
    matches = []
    for rec in load_catalog(root)["records"]:
        bbox = rec["features"]["bbox_mm"]
        if any(
            point[i] < bbox[i] - tolerance or point[i] > bbox[i + 3] + tolerance
            for i in range(3)
        ):
            continue
        shape = read_step(_inside_folder(root, rec["step"]))
        state = classify_point(shape, point, tolerance)
        if state != "OUTSIDE":
            matches.append({"solid_id": rec["solid_id"], "state": state})
    return matches


def ensure_preview_mesh(folder: str | Path, record: dict) -> Path:
    """Generate only the selected Solid's render mesh, not all 7,000 at startup."""
    from cst_material.occt_geometry import export_preview_stl

    root = Path(folder).resolve(strict=True)
    manifest = load_manifest(root)
    if not any(
        e["solid_id"] == record.get("solid_id") and e["step"] == record.get("step")
        for e in manifest["solids"]
    ):
        raise ValueError("所选 Solid 不在可信映射中")
    digest = record["features"]["step_sha256"]
    mesh = root / "meshes" / f"{digest}.stl"
    if not mesh.exists():
        export_preview_stl(read_step(_inside_folder(root, record["step"])), mesh)
    return mesh
