"""P8 single-Solid EMI shield candidate screening using detached P5 features.

These rules only rank review candidates. No STEP, CST, or material assignment needed.
"""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path


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
