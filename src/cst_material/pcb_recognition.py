"""Main PCB locator restricted to the -main-pcb component domain.

A thin, large-area board is preferred over an arbitrary high-volume body.
Geometry alone is not a confirmed PCB identity.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path


def _in_main_pcb_domain(solid_id: str) -> bool:
    # Component path precedes the final Solid name; do not infer from filename.
    component = solid_id.rsplit(":", 1)[0] if ":" in solid_id else ""
    return "-main-pcb" in component.casefold()


def find_main_pcb(
    folder: str | Path, *, min_volume_mm3: float = 100.0,
    max_thin_ratio: float = 0.15, min_relative_xy_area: float = 0.35,
) -> dict:
    if not math.isfinite(min_volume_mm3) or min_volume_mm3 < 0:
        raise ValueError("体积阈值必须是非负有限数")
    if not 0 < max_thin_ratio <= 1:
        raise ValueError("薄片比例必须在 (0,1] 内")
    if not 0 < min_relative_xy_area <= 1:
        raise ValueError("XY 面积比例必须在 (0,1] 内")
    root = Path(folder).resolve(strict=True)
    db = root / "geometry_features.sqlite3"
    if not db.is_file():
        raise FileNotFoundError("缺少 P5 geometry_features.sqlite3")
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "SELECT solid_id, step, features_json FROM solids WHERE status='PASS'"
        ).fetchall()
    domain_rows = [row for row in rows if _in_main_pcb_domain(row[0])]
    eligible = []
    invalid = 0
    for sid, step, raw in domain_rows:
        try:
            features = json.loads(raw)
            volume = float(features["volume_mm3"])
            bbox = features["bbox_mm"]
            if not math.isfinite(volume) or volume <= 0 or len(bbox) != 6:
                raise ValueError("无效几何指标")
            values = [float(v) for v in bbox]
            if not all(math.isfinite(v) for v in values):
                raise ValueError("无效包围盒")
            dx, dy, dz = (values[i + 3] - values[i] for i in range(3))
            if min(dx, dy, dz) <= 0:
                raise ValueError("包围盒退化")
        except (ValueError, TypeError, KeyError, IndexError):
            invalid += 1
            continue
        eligible.append({
            "solid_id": sid, "step": step, "volume_mm3": volume,
            "bbox_mm": values, "z_min_mm": values[2], "z_max_mm": values[5],
            "z_center_mm": (values[2] + values[5]) / 2,
            "z_thickness_mm": dz, "xy_min_mm": min(dx, dy),
            "xy_area_mm2": dx * dy, "z_thin_ratio": dz / min(dx, dy),
        })
    eligible.sort(key=lambda row: (-row["volume_mm3"], row["solid_id"]))
    largest = eligible[0] if eligible else None
    max_area = max((row["xy_area_mm2"] for row in eligible), default=0)
    for row in eligible:
        row["relative_xy_area"] = row["xy_area_mm2"] / max_area if max_area else 0
        row["z_thin_hint"] = row["z_thin_ratio"] <= max_thin_ratio
        row["area_hint"] = row["relative_xy_area"] >= min_relative_xy_area
    qualified = [
        row for row in eligible if row["z_thin_hint"] and row["area_hint"]
        and row["volume_mm3"] > min_volume_mm3
    ]
    # A PCB name is a tie-break priority among geometrically qualified boards,
    # not a substitute for geometry validation. Match the Solid leaf name only:
    # the parent component domain itself already contains "-main-pcb".
    named_qualified = [
        row for row in qualified
        if "pcb" in row["solid_id"].rsplit(":", 1)[-1].casefold()
    ]
    winner = (named_qualified[0] if named_qualified else
              qualified[0] if qualified else largest)
    if winner is None:
        reason = "未找到 -main-pcb 组件域" if not domain_rows else "域内没有有效几何数据"
        status = "NOT_FOUND"
    elif qualified:
        reason = (
            "Solid 名称含 PCB，且满足薄片、大面积条件，优先于更大体积的其他结构"
            if named_qualified else
            "域内最大体积 Solid 同时满足薄片和大面积条件"
            if winner is largest else
            "最大体积 Solid 不满足条件，已选择下一块薄而大的板"
        )
        status = "GEOMETRY_CANDIDATE"
    else:
        reason = "域内没有同时满足薄片、XY 面积和体积条件的 Solid，仅定位最大件供人工确认"
        status = "NEEDS_REVIEW"
    if winner is not None:
        winner = {**winner, "review_status": "NEEDS_REVIEW"}
    return {
        "candidate": winner, "status": status, "reason": reason,
        "domain_count": len(domain_rows), "eligible_count": len(eligible),
        "qualified_count": len(qualified), "named_qualified_count": len(named_qualified),
        "scanned_count": len(rows),
        "invalid_count": invalid, "min_volume_mm3_exclusive": min_volume_mm3,
        "max_thin_ratio": max_thin_ratio,
        "min_relative_xy_area": min_relative_xy_area,
        "warning": "仅在 -main-pcb 组件域内按薄片、大面积和体积选取候选；仍需人工确认身份。",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="-main-pcb 域内主 PCB 定位")
    parser.add_argument("folder")
    parser.add_argument("--min-volume", type=float, default=100.0)
    parser.add_argument("--max-thin-ratio", type=float, default=0.15)
    parser.add_argument("--min-relative-xy-area", type=float, default=0.35)
    args = parser.parse_args(argv)
    print(json.dumps(find_main_pcb(
        args.folder, min_volume_mm3=args.min_volume,
        max_thin_ratio=args.max_thin_ratio,
        min_relative_xy_area=args.min_relative_xy_area,
    ), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
