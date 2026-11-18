"""P9 PCB anchor: select the largest eligible Solid from P5 exact BREP metrics.

Volume is the primary ranking criterion; Z-thickness is a separate review
hint, not a filter that can silently replace the maximum-volume Solid.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path


def find_main_pcb(folder: str | Path, *, min_volume_mm3: float = 1500.0) -> dict:
    if not math.isfinite(min_volume_mm3) or min_volume_mm3 < 0:
        raise ValueError("体积阈值必须是非负有限数")
    root = Path(folder).resolve(strict=True)
    db = root / "geometry_features.sqlite3"
    if not db.is_file():
        raise FileNotFoundError("缺少 P5 geometry_features.sqlite3")
    connection = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT solid_id, step, features_json FROM solids WHERE status='PASS'"
        ).fetchall()
    finally:
        connection.close()
    eligible = []
    invalid = 0
    for sid, step, raw in rows:
        try:
            features = json.loads(raw)
            volume = float(features["volume_mm3"])
            bbox = features["bbox_mm"]
            if (not math.isfinite(volume) or len(bbox) != 6
                    or any(not math.isfinite(float(v)) for v in bbox)):
                raise ValueError("无效几何指标")
            z_min, z_max = float(bbox[2]), float(bbox[5])
            dz = z_max - z_min
            dx = float(bbox[3]) - float(bbox[0])
            dy = float(bbox[4]) - float(bbox[1])
            if min(dx, dy, dz) <= 0:
                raise ValueError("包围盒退化")
        except (ValueError, TypeError, KeyError, IndexError):
            invalid += 1
            continue
        if volume <= min_volume_mm3:
            continue
        eligible.append({
            "solid_id": sid, "step": step, "volume_mm3": volume,
            "bbox_mm": [float(x) for x in bbox],
            "z_min_mm": z_min, "z_max_mm": z_max,
            "z_center_mm": (z_min + z_max) / 2,
            "z_thickness_mm": dz,
            "xy_min_mm": min(dx, dy),
            "z_thin_ratio": dz / min(dx, dy),
        })
    eligible.sort(key=lambda row: (-row["volume_mm3"], row["solid_id"]))
    winner = eligible[0] if eligible else None
    if winner:
        winner = {**winner, "z_thin_hint": winner["z_thin_ratio"] <= 0.15,
                  "review_status": "NEEDS_REVIEW"}
    return {
        "candidate": winner,
        "eligible_count": len(eligible),
        "scanned_count": len(rows),
        "invalid_count": invalid,
        "min_volume_mm3_exclusive": min_volume_mm3,
        "warning": "按体积最大定位 PCB 候选；Z 薄片仅作校验。若模型坐标旋转或存在大机壳，需人工确认。",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="按体积最大定位主 PCB 候选")
    parser.add_argument("folder")
    parser.add_argument("--min-volume", type=float, default=1500.0)
    args = parser.parse_args(argv)
    print(json.dumps(find_main_pcb(args.folder, min_volume_mm3=args.min_volume),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
