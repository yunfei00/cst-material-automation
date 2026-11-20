"""P8 single-Solid EMI shield candidate screening using detached P5 features.

These rules only rank review candidates. No STEP, CST, or material assignment needed.
"""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

from cst_material.pcb_recognition import find_main_pcb


def evaluate_shield_candidate(features: dict) -> dict:
    """Return explainable geometric evidence, not an identity probability."""
    dims = features.get("dimensions_mm")
    volume = features.get("volume_mm3")
    area = features.get("surface_mm2")
    if (
        not isinstance(dims, (list, tuple)) or len(dims) != 3
        or any(isinstance(v, bool) or not isinstance(v, (int, float))
               or not math.isfinite(v) or v <= 0 for v in dims)
        or isinstance(volume, bool) or not isinstance(volume, (int, float))
        or not math.isfinite(volume) or volume <= 0
        or isinstance(area, bool) or not isinstance(area, (int, float))
        or not math.isfinite(area) or area <= 0
    ):
        return {"candidate": False, "score": 0, "evidence": ["几何特征不完整或无效"],
                "metrics": {}}
    small, middle, large = sorted(float(v) for v in dims)
    fill = float(volume) / (small * middle * large)
    slenderness = large / small
    flatness = middle / small
    # Open-bottom thin-wall cans often occupy a small fraction of their bounding box,
    # but this is also true of brackets, heat sinks and numerous other structures.
    evidence = []
    score = 0
    if 0 < fill <= 0.4:
        score += 35
        evidence.append(f"包围盒填充率较低 ({fill:.3f})，可能存在空腔")
    if flatness >= 3:
        score += 20
        evidence.append(f"至少两个尺寸明显大于最小尺寸 ({flatness:.2f})")
    if slenderness >= 5:
        score += 10
        evidence.append(f"长短尺寸比 {slenderness:.2f}")
    area_volume = float(area) / float(volume)
    if area_volume >= 0.5:
        score += 15
        evidence.append(f"面积体积比 {area_volume:.3f} mm⁻¹，提示薄壁可能")
    surfaces = features.get("surface_types", {})
    if isinstance(surfaces, dict):
        plane_faces = sum(
            value for key, value in surfaces.items()
            if isinstance(key, str) and "Plane" in key
            and isinstance(value, int) and not isinstance(value, bool) and value >= 0
        )
        if plane_faces >= 6:
            score += 20
            evidence.append(f"平面面数 {plane_faces}，具备板壁结构候选特征")
    return {
        "candidate": score >= 55,
        "score": score,
        "evidence": evidence,
        "metrics": {
            "bbox_fill_ratio": round(fill, 6),
            "aspect_ratio": round(flatness, 6),
            "surface_volume_ratio_mm_inv": round(area_volume, 6),
        },
        "review_status": "NEEDS_REVIEW",
    }


def search_shield_candidates(folder: str | Path, *, limit: int = 10000) -> list[dict]:
    """Read existing P5 database; do not depend on original STEP or project path."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise ValueError("limit 必须在 1..10000")
    db = Path(folder).resolve(strict=True) / "geometry_features.sqlite3"
    if not db.is_file():
        raise FileNotFoundError("请先构建 P5 几何特征数据库")
    connection = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        records = connection.execute(
            "SELECT solid_id,step,features_json FROM solids WHERE status='PASS'"
        ).fetchall()
    finally:
        connection.close()
    results = []
    for solid_id, step, raw in records:
        try:
            features = json.loads(raw)
            result = evaluate_shield_candidate(features)
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
        if result["candidate"]:
            results.append({"solid_id": solid_id, "step": step,
                            "similarity": None, "candidate_type": "SHIELD_CANDIDATE",
                            "shape": "THIN_WALL_POSSIBLE",
                            "volume_mm3": features["volume_mm3"],
                            "dimensions_mm": features["dimensions_mm"], **result})
    results.sort(key=lambda row: (-row["score"], row["solid_id"]))
    return results[:limit]


def search_pcb_related_shields(
    folder: str | Path, *, max_gap_mm: float = 0.1,
    max_height_mm: float = 1.5, max_fill_ratio: float = 0.45,
) -> dict:
    """Fast PCB-relative spatial search; candidates require human confirmation.

    The signed separation must be nonnegative to avoid falsely matching distant
    bodies with a large negative difference. Bounding boxes are coarse evidence.
    """
    if not (math.isfinite(max_gap_mm) and max_gap_mm >= 0):
        raise ValueError("Z 间隙必须是非负有限数")
    if not (math.isfinite(max_height_mm) and max_height_mm > 0):
        raise ValueError("候选高度必须是正有限数")
    if not (math.isfinite(max_fill_ratio) and 0 < max_fill_ratio < 1):
        raise ValueError("填充率阈值必须在 (0,1) 内")
    root = Path(folder).resolve(strict=True)
    pcb = find_main_pcb(root)
    anchor = pcb["candidate"]
    if anchor is None or pcb["status"] == "NOT_FOUND":
        return {"pcb": pcb, "results": [], "counts": {},
                "warning": "无法定位 PCB，未执行空间筛选"}
    with sqlite3.connect(f"file:{(root / 'geometry_features.sqlite3').as_posix()}?mode=ro",
                         uri=True) as db:
        rows = db.execute(
            "SELECT solid_id,step,features_json FROM solids WHERE status='PASS'"
        ).fetchall()
    pb = anchor["bbox_mm"]
    results = []
    counts = {"scanned": len(rows), "xy_overlap": 0, "z_near": 0,
              "named": 0, "geometry_supported": 0, "suspected": 0}
    for sid, step, raw in rows:
        if sid == anchor["solid_id"]:
            continue
        try:
            feat = json.loads(raw)
            bb = [float(v) for v in feat["bbox_mm"]]
            volume = float(feat["volume_mm3"])
            if len(bb) != 6 or not all(math.isfinite(v) for v in bb):
                continue
            dx, dy, dz = (bb[i + 3] - bb[i] for i in range(3))
            if min(dx, dy, dz) <= 0 or not math.isfinite(volume) or volume <= 0:
                continue
        except (ValueError, TypeError, KeyError, IndexError):
            continue
        if min(bb[3], pb[3]) <= max(bb[0], pb[0]) or min(bb[4], pb[4]) <= max(bb[1], pb[1]):
            continue
        counts["xy_overlap"] += 1
        # Positive signed separation means the candidate is outside the PCB.
        top_gap = bb[2] - pb[5]
        bottom_gap = pb[2] - bb[5]
        if 0 <= top_gap <= max_gap_mm:
            side, gap = "TOP", top_gap
        elif 0 <= bottom_gap <= max_gap_mm:
            side, gap = "BOTTOM", bottom_gap
        else:
            continue
        counts["z_near"] += 1
        leaf = sid.rsplit(":", 1)[-1].casefold()
        named = any(token in leaf for token in ("shielding", "shield", "屏蔽"))
        if named:
            counts["named"] += 1
        fill = volume / (dx * dy * dz)
        # A broad low-fill frame can be shallow, but a taller shield can
        # still be valid. These are supporting hints, not identity proof.
        broad = max(dx, dy) / min(dx, dy) >= 3
        shallow = dz <= max_height_mm
        hollow_hint = 0 < fill <= max_fill_ratio
        geometry = shallow and hollow_hint and broad
        if geometry:
            counts["geometry_supported"] += 1
        if not named and not geometry:
            continue
        if named and geometry:
            verdict, priority = "NAMED_GEOMETRY", 0
        elif named:
            verdict, priority = "NAMED_REVIEW", 1
        else:
            verdict, priority = "GEOMETRY_REVIEW", 2
            counts["suspected"] += 1
        results.append({
            "solid_id": sid, "step": step, "candidate_type": "SHIELD_CANDIDATE",
            "verdict": verdict, "side": side, "gap_mm": round(gap, 6),
            "bbox_mm": bb, "height_mm": round(dz, 6),
            "fill_ratio": round(fill, 6), "aspect_ratio_xy": round(max(dx, dy) / min(dx, dy), 6),
            "name_hit": named, "geometry_hint": geometry,
            "review_status": "NEEDS_REVIEW", "priority": priority,
            "evidence": ["PCB XY 重叠", f"PCB {side} 间隙 {gap:.4f} mm"]
            + (["叶层名称包含 shield/shielding/屏蔽"] if named else [])
            + (["低填充率、薄且长宽比大"] if geometry else []),
        })
    results.sort(key=lambda row: (row["priority"], row["gap_mm"], row["solid_id"]))
    return {"pcb": pcb, "results": results, "counts": counts,
            "warning": "仅空间、名称及粗几何候选；bbox 填充率不是空腔证明，不能自动赋材"}
