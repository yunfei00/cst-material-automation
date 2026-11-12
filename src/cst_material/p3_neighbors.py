"""P3-D: bounded-memory, deterministic nearest loose-AABB candidate analysis.

No CST process is needed to analyze an existing P3-C report. This returns
geometric *lower bounds* between loose bounding boxes, NOT surface distances
or proven physical contact. Never outputs a complete N x N distance matrix.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from cst_material.domain import BoundingBox

if TYPE_CHECKING:
    from cst_material.p3_spatial import SpatialCatalog

MAX_NEIGHBOR_SOLIDS = 12000
DEFAULT_BATCH_SIZE = 32
SUPPORTED_UNITS = {"nm", "um", "mm", "cm", "m", "mil", "in", "ft"}


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} 必须是数值")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} 必须为有限数值")
    return result


def read_spatial_report(path: Path) -> tuple[SpatialCatalog, dict]:
    """Read only a validated existing P3-C PASS/PARTIAL JSON (including SKIPPED neighbors).

    A prior lock-cleanup PARTIAL does not invalidate successfully returned
    per-Solid bounds. A failed/incomplete spatial query is never accepted.
    """
    source = path.resolve(strict=True)
    if not source.is_file() or source.suffix.casefold() != ".json":
        raise ValueError("P3-D 输入必须是已有的 P3-C JSON 文件")
    raw = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict) or raw.get("task") != "spatial":
        raise ValueError("P3-D 只接受 P3-C spatial 报告")
    if raw.get("status") not in {"PASS", "PARTIAL"}:
        raise ValueError("P3-C 原始查询失败；不能利用不完整数据计算邻近关系")
    data = raw.get("spatial")
    if not isinstance(data, dict):
        raise TypeError("P3-C 报告没有空间查询结果")
    count = data.get("solid_count")
    features = data.get("features")
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or count <= 0
        or not isinstance(features, list)
        or len(features) != count
    ):
        raise ValueError("P3-C 报告的 Solid 数量与特征列表不一致")
    unit = data.get("length_unit")
    if unit not in SUPPORTED_UNITS:
        raise ValueError(f"P3-C 长度单位不支持：{unit!r}")
    to_m = _number(data.get("length_unit_to_m"), "单位转换系数")
    if to_m <= 0:
        raise ValueError("P3-C 单位转换系数必须大于 0")

    # Imported locally to avoid a circular dependency: P3-C delegates its
    # neighbor function to this module, but the types live in p3_spatial.py.
    from cst_material.p3_spatial import SpatialCatalog, SpatialFeature

    names: set[str] = set()
    results = []
    missing = 0
    for record in features:
        if not isinstance(record, dict):
            raise TypeError("P3-C Solid 特征记录必须是对象")
        name = record.get("identifier")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("P3-C Solid 标识为空或有重复")
        names.add(name)
        component = record.get("component")
        material = record.get("material")
        if component is not None and not isinstance(component, str):
            raise ValueError(f"Solid {name!r} 的 Component 类型异常")
        if material is not None and not isinstance(material, str):
            raise ValueError(f"Solid {name!r} 的 Material 类型异常")
        payload = record.get("loose_bounds")
        if payload is None:
            bounds = dims = mid = smallest = None
            missing += 1
        else:
            if not isinstance(payload, dict):
                raise ValueError(f"Solid {name!r} 的包围盒不是对象")
            keys = ("xmin", "ymin", "zmin", "xmax", "ymax", "zmax")
            if not all(key in payload for key in keys):
                raise ValueError(f"Solid {name!r} 的包围盒缺少坐标")
            coords = {key: _number(payload[key], key) for key in keys}
            bounds = BoundingBox(**coords)
            dims = bounds.dimensions
            mid = (
                (bounds.xmin + bounds.xmax) / 2.0,
                (bounds.ymin + bounds.ymax) / 2.0,
                (bounds.zmin + bounds.zmax) / 2.0,
            )
            if not all(math.isfinite(x) for x in (*dims, *mid)):
                raise ValueError(f"Solid {name!r} 的包围盒坐标溢出")
            smallest = min(dims)
        results.append(
            SpatialFeature(
                identifier=name, component=component, material=material,
                loose_bounds=bounds, dimensions_project_unit=dims,
                bbox_midpoint_project_unit=mid,
                min_bbox_extent_project_unit=smallest,
            )
        )
    if missing != data.get("missing_bbox_count"):
        raise ValueError("P3-C 报告的缺失包围盒数量不一致")
    catalog = SpatialCatalog(
        length_unit=unit, length_unit_to_m=to_m,
        declared_solid_count=count, features=tuple(results),
        missing_bbox_count=missing,
        cleanup_deferred=bool(data.get("cleanup_deferred", False)),
    )
    return catalog, {
        "source_query_status": raw["status"],
        "source_cleanup_deferred": catalog.cleanup_deferred,
        "source_missing_bbox_count": missing,
        "source_neighbor_status": (
            data.get("neighbors", {}).get("status")
            if isinstance(data.get("neighbors"), dict) else None
        ),
    }


def analyze_neighbors(
    catalog: SpatialCatalog, neighbor_limit: int, *, batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict:
    """Exact top-k loose-box *lower bounds*, with O(batch_size * N) memory.

    Pairwise operations are vectorized in small batches (not N x N).
    Tie-breaking is deterministic by solid identifier, including overlaps.
    Complexity is still O(N²) in time; 12k is a conservative explicit cap.
    """
    if not isinstance(neighbor_limit, int) or not 0 <= neighbor_limit <= 20:
        raise ValueError("邻居数量必须在 0 到 20 之间")
    if not isinstance(batch_size, int) or not 1 <= batch_size <= 128:
        raise ValueError("分批数量必须在 1 到 128 之间")
    valid = sorted(
        (item for item in catalog.features if item.loose_bounds is not None),
        key=lambda item: item.identifier,
    )
    total = len(valid)
    base = {
        "method": "numpy-batched-loose-aabb",
        "analyzed_solid_count": total,
        "batch_size": batch_size,
        "neighbors": [],
    }
    if neighbor_limit == 0:
        return {**base, "status": "SKIPPED", "reason": "用户指定跳过邻近计算"}
    if total == 0:
        return {**base, "status": "SKIPPED", "reason": "没有可用包围盒"}
    if total > MAX_NEIGHBOR_SOLIDS:
        return {
            **base, "status": "SKIPPED",
            "reason": f"有效 Solid {total} 超出 {MAX_NEIGHBOR_SOLIDS} 的时间安全上限",
        }

    low = np.array([
        [f.loose_bounds.xmin, f.loose_bounds.ymin, f.loose_bounds.zmin] for f in valid
    ], dtype=np.float64)
    high = np.array([
        [f.loose_bounds.xmax, f.loose_bounds.ymax, f.loose_bounds.zmax] for f in valid
    ], dtype=np.float64)
    if not np.isfinite(low).all() or not np.isfinite(high).all():
        raise ValueError("P3-D 遇到非有限包围盒坐标")
    count_nearest = min(neighbor_limit, total - 1)
    output: list[dict] = []
    if count_nearest == 0:
        return {
            **base, "status": "PASS", "neighbors_per_solid": 0,
            "neighbors": [{"identifier": valid[0].identifier, "nearest_bbox_candidates": []}],
        }

    for begin in range(0, total, batch_size):
        stop = min(begin + batch_size, total)
        batch_low = low[begin:stop]
        batch_high = high[begin:stop]

        # Distance between loose axis-aligned boxes is a lower bound on
        # the separation between original shapes.
        gap_x = np.maximum(
            0.0, np.maximum(
                batch_low[:, None, 0] - high[None, :, 0],
                low[None, :, 0] - batch_high[:, None, 0],
            ),
        )
        gap_y = np.maximum(
            0.0, np.maximum(
                batch_low[:, None, 1] - high[None, :, 1],
                low[None, :, 1] - batch_high[:, None, 1],
            ),
        )
        gap_z = np.maximum(
            0.0, np.maximum(
                batch_low[:, None, 2] - high[None, :, 2],
                low[None, :, 2] - batch_high[:, None, 2],
            ),
        )
        distance = np.hypot(np.hypot(gap_x, gap_y), gap_z)
        if not np.isfinite(distance).all():
            raise ValueError("P3-D 包围盒距离计算溢出")
        distance[np.arange(stop - begin), np.arange(begin, stop)] = np.inf

        # argpartition finds a threshold without a full O(N log N) sort.
        indices = np.argpartition(distance, kth=count_nearest - 1, axis=1)
        for offset in range(stop - begin):
            values = distance[offset]
            threshold = float(np.max(values[indices[offset, :count_nearest]]))
            smaller = np.flatnonzero(values < threshold)
            # Already alphabetically sorted, so index order resolves ties.
            strictly_better = sorted(
                (int(i) for i in smaller), key=lambda i: (float(values[i]), i)
            )
            tied = np.flatnonzero(values == threshold)
            chosen = strictly_better + [
                int(i) for i in tied[:count_nearest - len(strictly_better)]
            ]
            nearest = []
            for index in chosen:
                raw_distance = float(values[index])
                si = raw_distance * catalog.length_unit_to_m
                if not math.isfinite(si):
                    raise ValueError("P3-D 距离转换为米时溢出")
                nearest.append({
                    "identifier": valid[index].identifier,
                    "loose_bbox_distance_lower_bound_project_unit": raw_distance,
                    "loose_bbox_distance_lower_bound_m": si,
                    "loose_boxes_overlap_or_touch": raw_distance == 0.0,
                })
            output.append({
                "identifier": valid[begin + offset].identifier,
                "nearest_bbox_candidates": nearest,
            })
    return {
        **base, "status": "PASS", "neighbors_per_solid": count_nearest,
        "neighbors": output,
    }
