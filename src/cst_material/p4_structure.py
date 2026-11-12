"""P4-A: offline, auditable structure *shape hints* from verified CST reports.

Inputs: P3-A geometry, P3-C spatial, optional P3-D neighbor reports.
Output: candidate *loose AABB aspect-ratio classes*, NEVER a material
recommendation. There is no CST import, save, material load, assignment or
network call. Source identifiers must match to prevent cross-project mixing.
"""
from __future__ import annotations

import json
import math
import ntpath
from collections import Counter
from pathlib import Path

from cst_material.p3_neighbors import read_spatial_report
from cst_material.p3_spatial import _bbox_separation

FLAT_RATIO = 0.15
ELONGATED_RATIO = 4.0
NEAR_EQUAL_RATIO = 0.70


def _number(value: object, label: str, *, allow_zero: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} 必须是有限数值")
    number = float(value)
    if not math.isfinite(number) or number < 0 or (not allow_zero and number == 0):
        raise ValueError(f"{label} 必须是有限非负数")
    return number


def _load_json(path: Path, task: str, statuses: set[str]) -> dict:
    path = path.resolve(strict=True)
    if not path.is_file() or path.suffix.casefold() != ".json":
        raise ValueError(f"{task} 输入必须是本地 JSON 文件")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict) or value.get("task") != task:
        raise ValueError(f"输入报告不是 {task} 类型")
    if value.get("status") not in statuses:
        raise ValueError(f"{task} 原始报告不是可用状态，拒绝推测缺失数据")
    return value


def _same_source(a: object, b: object, title: str) -> None:
    """Check when both paths exist; same path alone does not prove same version."""
    if isinstance(a, str) and a and isinstance(b, str) and b:
        if ntpath.normcase(ntpath.normpath(a)) != ntpath.normcase(ntpath.normpath(b)):
            raise ValueError(f"{title} 来源文件路径不同，不能混合工程的特征")


def _read_geometry_report(path: Path) -> tuple[dict, dict]:
    root = _load_json(path, "geometry", {"PASS"})
    data = root.get("geometry")
    if not isinstance(data, dict):
        raise TypeError("P3-A 报告缺少 geometry 数据")
    unit = data.get("length_unit")
    coefficient = _number(data.get("length_unit_to_m"), "P3-A 单位系数", allow_zero=False)
    features = data.get("features")
    if unit not in {"nm", "um", "mm", "cm", "m", "mil", "in", "ft"}:
        raise ValueError("P3-A 工程长度单位无效")
    if not isinstance(features, list) or not features:
        raise ValueError("P3-A 报告没有完整 Solid 列表")
    by_name: dict[str, dict] = {}
    for record in features:
        if not isinstance(record, dict):
            raise TypeError("P3-A Solid 记录不是对象")
        name = record.get("identifier")
        if not isinstance(name, str) or not name or name in by_name:
            raise ValueError("P3-A 的 Solid 标识为空或重复")
        _number(record.get("volume_project_unit3"), f"{name} 体积")
        _number(record.get("area_project_unit2"), f"{name} 表面积")
        _number(record.get("volume_m3"), f"{name} SI 体积")
        _number(record.get("area_m2"), f"{name} SI 表面积")
        if record.get("component") is not None and not isinstance(
            record["component"], str
        ):
            raise TypeError(f"{name} 的组件名称类型错误")
        if record.get("material") is not None and not isinstance(record["material"], str):
            raise TypeError(f"{name} 的材料名称类型错误")
        by_name[name] = record
    if root.get("solid_count") not in (None, len(by_name)):
        raise ValueError("P3-A 报告的 Solid 数量不一致")
    return root, {"by_name": by_name, "unit": unit, "to_m": coefficient}


def _read_neighbors_report(path: Path, spatial_source: Path, names: set[str],
                           catalog) -> dict[str, list[dict]]:
    root = _load_json(path, "spatial-offline", {"PASS", "PARTIAL"})
    _same_source(root.get("source"), str(spatial_source.resolve()), "P3-D 与 P3-C")
    info = root.get("input_summary")
    payload = root.get("neighbors")
    if not isinstance(info, dict) or not isinstance(payload, dict):
        raise TypeError("P3-D 缺少输入校验摘要或邻近结果")
    if info.get("solid_count") != catalog.declared_solid_count:
        raise ValueError("P3-D 与 P3-C 的 Solid 数量不一致")
    if (
        info.get("valid_bbox_count")
        != catalog.declared_solid_count - catalog.missing_bbox_count
    ):
        raise ValueError("P3-D 与 P3-C 的有效包围盒数量不一致")
    if payload.get("status") != "PASS":
        raise ValueError("P3-D 邻近分析不是 PASS，不使用 SKIPPED 候选")
    rows = payload.get("neighbors")
    if not isinstance(rows, list) or len(rows) != len(names):
        raise ValueError("P3-D 的邻近数据数量与有效包围盒数量不一致")
    boxes = {
        feature.identifier: feature.loose_bounds for feature in catalog.features
        if feature.loose_bounds is not None
    }
    output = {}
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("P3-D 邻近记录不是对象")
        own = row.get("identifier")
        candidates = row.get("nearest_bbox_candidates")
        if own not in names or own in output or not isinstance(candidates, list):
            raise ValueError("P3-D 邻近记录包含未知、重复 Solid 或无效列表")
        if len(candidates) > 20:
            raise ValueError("P3-D 单个 Solid 邻近候选数量超过 20")
        seen = set()
        verified = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise TypeError("P3-D 邻近候选不是对象")
            other = candidate.get("identifier")
            if other not in names or other == own or other in seen:
                raise ValueError("P3-D 邻近候选存在无效、重复或自引用")
            seen.add(other)
            distance = _number(
                candidate.get("loose_bbox_distance_lower_bound_project_unit"),
                "P3-D 包围盒距离",
            )
            measured = _bbox_separation(boxes[own], boxes[other])
            if not math.isclose(distance, measured, rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError(f"P3-D 候选距离与 P3-C 包围盒数据不一致：{own}")
            distance_m = _number(
                candidate.get("loose_bbox_distance_lower_bound_m"),
                "P3-D 距离换算",
            )
            if not math.isclose(
                distance_m, distance * catalog.length_unit_to_m,
                rel_tol=1e-9, abs_tol=1e-12,
            ):
                raise ValueError("P3-D 距离单位换算与 P3-C 不一致")
            verified.append({
                "identifier": other,
                "loose_bbox_distance_lower_bound_m": distance_m,
            })
        output[own] = verified
    return output


def _shape_hint(dims: tuple[float, float, float] | None) -> tuple[str, list[str]]:
    """Ratios describe only the loose axis-aligned BOX, not the original solid."""
    if dims is None:
        return "unknown", ["包围盒缺失，无法提取轴向比例"]
    small, middle, large = sorted(dims)
    if small <= 0 or not all(math.isfinite(v) for v in dims):
        return "unknown", ["包围盒存在零厚度或无效轴向范围"]
    if large / middle >= ELONGATED_RATIO:
        return "elongated_box_candidate", [
            f"非紧包围盒最长轴/次长轴 ≥ {ELONGATED_RATIO:g}"
        ]
    if small / middle <= FLAT_RATIO:
        return "flat_box_candidate", [
            f"非紧包围盒最短轴/次长轴 ≤ {FLAT_RATIO:g}"
        ]
    if small / large >= NEAR_EQUAL_RATIO:
        return "near_equal_axes_box_candidate", [
            f"非紧包围盒最短轴/最长轴 ≥ {NEAR_EQUAL_RATIO:g}"
        ]
    return "other_axis_ratio_box_candidate", ["非紧包围盒三轴比例不满足其他候选规则"]


def build_structure_report(
    geometry_path: Path, spatial_path: Path, neighbor_path: Path | None = None,
) -> dict:
    """Combine existing, independently generated local reports; no CST calls."""
    geometry_root, geometry = _read_geometry_report(geometry_path)
    catalog, spatial_provenance = read_spatial_report(spatial_path)
    # P3-A and P3-C are allowed in different project length units if their
    # SI conversion is valid; ratios use one catalog at a time.
    _same_source(geometry_root.get("source"), spatial_provenance.get("source"), "P3-A/P3-C")
    # P3-C query stores source in its top-level JSON, not read_spatial_report().
    spatial_root = _load_json(spatial_path, "spatial", {"PASS", "PARTIAL"})
    _same_source(geometry_root.get("source"), spatial_root.get("source"), "P3-A/P3-C")

    a_names = set(geometry["by_name"])
    c_names = {feature.identifier for feature in catalog.features}
    if a_names != c_names:
        raise ValueError(
            f"P3-A/P3-C Solid 集合不一致，只有 P3-A 的 {len(a_names-c_names)} 项，"
            f"只有 P3-C 的 {len(c_names-a_names)} 项；拒绝跨工程推断"
        )
    if not math.isclose(
        geometry["to_m"], catalog.length_unit_to_m, rel_tol=1e-9
    ) or geometry["unit"] != catalog.length_unit:
        raise ValueError("P3-A/P3-C 单位或转换系数不一致；可能来自不同工程配置")
    valid_names = {
        f.identifier for f in catalog.features if f.loose_bounds is not None
    }
    neighbors = (
        _read_neighbors_report(neighbor_path, spatial_path, valid_names, catalog)
        if neighbor_path is not None else {}
    )
    records = []
    counts = Counter()
    warnings = []
    for solid in sorted(catalog.features, key=lambda f: f.identifier):
        geo = geometry["by_name"][solid.identifier]
        if solid.component is not None and geo.get("component") is not None:
            if solid.component != geo["component"]:
                raise ValueError(f"{solid.identifier} 的 Component 与 P3-A 不同")
        if solid.material is not None and geo.get("material") is not None:
            if solid.material != geo["material"]:
                raise ValueError(f"{solid.identifier} 的材料与 P3-A 不同，拒绝混用数据")
        hint, reason = _shape_hint(solid.dimensions_project_unit)
        counts[hint] += 1
        dims_m = (
            [d * catalog.length_unit_to_m for d in solid.dimensions_project_unit]
            if solid.dimensions_project_unit is not None else None
        )
        if dims_m is not None and not all(math.isfinite(d) for d in dims_m):
            raise ValueError(f"{solid.identifier} 的 SI 包围盒范围溢出")
        near = neighbors.get(solid.identifier, [])
        records.append({
            "identifier": solid.identifier,
            "component": solid.component or geo.get("component"),
            "observed_existing_material": solid.material or geo.get("material"),
            "volume_m3": geo["volume_m3"],
            "area_m2": geo["area_m2"],
            "compactness": geo.get("compactness"),
            "loose_bbox_dimensions_m": dims_m,
            "shape_hint": hint,
            "reasons": reason,
            "neighbor_candidate_count": len(near),
            "nearest_loose_bbox_candidate": near[0] if near else None,
            "material_recommendation": None,
            "needs_engineer_review_for_material": True,
        })
    if catalog.missing_bbox_count:
        warnings.append(f"有 {catalog.missing_bbox_count} 个 Solid 缺少包围盒，未分类")
    if neighbor_path is None:
        warnings.append("未提供 P3-D 邻近报告，本次未关联邻居候选")
    if catalog.cleanup_deferred:
        warnings.append("P3-C 原始查询曾出现临时文件清理延迟，不影响已校验几何")
    return {
        "status": "PARTIAL" if catalog.missing_bbox_count else "PASS",
        "scope": (
            "仅根据真实 P3-A 体积/表面积和 P3-C 非紧包围盒轴比，"
            "生成几何类别候选；P3-D 可选连接邻近候选。"
            "不证明零件真实形状/厚度/接触，不推断材料、不自动赋值。"
        ),
        "source_summary": {
            "geometry_report": str(geometry_path.resolve()),
            "spatial_report": str(spatial_path.resolve()),
            "neighbor_report": str(neighbor_path.resolve()) if neighbor_path else None,
            "project_path_match_checked": bool(
                geometry_root.get("source") and spatial_root.get("source")
            ),
            "project_version_identity_verified": False,
        },
        "solid_count": len(records),
        "valid_bbox_count": len(records) - catalog.missing_bbox_count,
        "with_neighbor_data_count": sum(bool(neighbors.get(r["identifier"])) for r in records),
        "shape_hint_counts": dict(sorted(counts.items())),
        "rules": {
            "longest_to_middle_at_least": ELONGATED_RATIO,
            "smallest_to_middle_at_most": FLAT_RATIO,
            "smallest_to_longest_at_least": NEAR_EQUAL_RATIO,
            "source": "heuristic_preview_not_material_validation",
        },
        "warnings": warnings,
        "records": records,
    }
