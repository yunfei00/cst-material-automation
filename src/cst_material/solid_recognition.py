"""Evidence-based component recognition. No geometry-only material guesses.

A geometric candidate is not a confirmed real-world part identity.
Only explicitly approved Solid mappings or exact, validated reference-shape
matches may become CONFIRMED. Unknown cases stay UNKNOWN.
"""
from __future__ import annotations

import json
import math
from pathlib import Path


def load_knowledge(path: str | Path | None = None) -> dict:
    if path is None:
        return {"schema_version": 1, "confirmed_solids": {}, "reference_shapes": []}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("识别知识库版本不受支持")
    if not isinstance(data.get("confirmed_solids"), dict):
        raise TypeError("缺少已确认 Solid 映射")
    if not isinstance(data.get("reference_shapes"), list):
        raise TypeError("参考 CAD 样本列表无效")
    return data


def shape_candidate(features: dict) -> dict:
    """Cheap, deterministic morphological hints, never identity proof."""
    dims = features.get("dimensions_mm", [])
    if (
        len(dims) != 3 or not all(
            isinstance(v, (float, int)) and math.isfinite(v) and v >= 0
            for v in dims
        )
    ):
        return {"shape": "UNKNOWN", "candidate": None, "evidence": ["尺寸无效"]}
    small, medium, large = sorted(dims)
    if small < 1e-8 or large < 1e-8:
        return {"shape": "DEGENERATE", "candidate": None, "evidence": []}
    thinness = large / small
    flatness = medium / small
    surfaces = features.get("surface_types", {})
    curved = sum(value for key, value in surfaces.items() if "Plane" not in key)
    if thinness > 10 and flatness > 4:
        if curved >= 2 and (features.get("face_count") or 0) >= 6:
            return {
                "shape": "THIN_BENT",
                "candidate": "SPRING_CONTACT",
                "evidence": [
                    "薄壁长宽比明显", "至少两个非平面曲面",
                    "可能是弹片、支架或其他弯折薄金属件",
                ],
            }
        return {"shape": "THIN_PLATE", "candidate": None, "evidence": ["薄板几何"]}
    if large / medium > 5:
        return {"shape": "SLENDER", "candidate": None, "evidence": ["细长几何"]}
    return {"shape": "OTHER", "candidate": None, "evidence": ["未命中特定形态规则"]}


def compare_reference_geometry(features: dict, reference_features: dict) -> float | None:
    """Bounded, interpretable shape similarity, NOT a proof of part identity.

    Translation and global-axis rotation do not affect the sorted dimensions.
    Size, volume, area, topology and curved-face fraction are compared.
    This deliberately does not claim invariance to CAD topology healing,
    different tolerances, changes of units, or heterogeneous assemblies.
    """
    dims = features.get("dimensions_mm")
    other = reference_features.get("dimensions_mm")
    if (
        not isinstance(dims, list) or not isinstance(other, list)
        or len(dims) != 3 or len(other) != 3
    ):
        return None
    values = dims + other
    if not all(
        isinstance(v, (int, float)) and not isinstance(v, bool)
        and math.isfinite(v) and v > 0 for v in values
    ):
        return None
    lhs = sorted(float(v) for v in dims)
    rhs = sorted(float(v) for v in other)
    deviation = sum(
        abs(math.log(a / b)) for a, b in zip(lhs, rhs, strict=True)
    ) / 3
    for feature, weight in (
        ("volume_mm3", 0.2), ("surface_mm2", 0.12), ("face_count", 0.08)
    ):
        x, y = features.get(feature), reference_features.get(feature)
        if (
            isinstance(x, (int, float)) and isinstance(y, (int, float))
            and not isinstance(x, bool) and not isinstance(y, bool)
            and math.isfinite(x) and math.isfinite(y) and x > 0 and y > 0
        ):
            deviation += weight * abs(math.log(x / y))
    for item in (features, reference_features):
        if not isinstance(item.get("surface_types", {}), dict):
            return None
    def curved_ratio(feat):
        surfaces = feat.get("surface_types", {})
        faces = sum(
            v for v in surfaces.values()
            if isinstance(v, int) and not isinstance(v, bool) and v >= 0
        )
        curved = sum(
            v for key, v in surfaces.items()
            if "Plane" not in key and isinstance(v, int)
            and not isinstance(v, bool) and v >= 0
        )
        return curved / faces if faces else None
    a, b = curved_ratio(features), curved_ratio(reference_features)
    if a is not None and b is not None:
        deviation += 0.5 * abs(a - b)
    return round(1 / (1 + deviation), 6)


def similar_reference_candidate(
    features: dict, references: list[dict], threshold: float = 0.85
) -> dict | None:
    """Return a review-only candidate if the best reference is unambiguous."""
    candidates = []
    for reference in references:
        if (
            not isinstance(reference, dict)
            or reference.get("approved") is not True
            or not isinstance(reference.get("features"), dict)
            or not isinstance(reference.get("part_type"), str)
            or not reference["part_type"]
        ):
            continue
        score = compare_reference_geometry(features, reference["features"])
        if score is not None and score >= threshold:
            candidates.append((score, reference["part_type"]))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    best_score, label = candidates[0]
    if any(
        score >= best_score - 0.02 and candidate_label != label
        for score, candidate_label in candidates[1:]
    ):
        return {"candidate": None, "score": best_score, "ambiguous": True}
    return {"candidate": label, "score": best_score, "ambiguous": False}


def recognize_catalog(catalog: dict, knowledge: dict) -> list[dict]:
    """Produce auditable labels while explicitly preserving UNKNOWN identity."""
    records = catalog.get("records")
    if not isinstance(records, list):
        raise TypeError("无效 Solid 目录")
    manual = knowledge.get("confirmed_solids", {})
    references = knowledge.get("reference_shapes", [])
    if not isinstance(manual, dict) or not isinstance(references, list):
        raise TypeError("识别知识结构无效")
    ref_by_hash = {}
    for row in references:
        if not isinstance(row, dict) or not row.get("approved"):
            continue
        digest = row.get("step_sha256")
        label = row.get("part_type")
        if not isinstance(digest, str) or not isinstance(label, str) or not label:
            raise ValueError("参考 CAD 记录无效")
        if digest in ref_by_hash and ref_by_hash[digest] != label:
            raise ValueError("参考样本相同几何指纹出现冲突类型")
        ref_by_hash[digest] = label
    source_hash = catalog.get("original_source_sha256") or catalog.get("source_sha256")
    results = []
    for rec in records:
        solid_id = rec["solid_id"]
        hint = shape_candidate(rec["features"])
        confirmed = manual.get(solid_id)
        identity = "UNKNOWN"
        evidence = list(hint["evidence"])
        status = "UNKNOWN"
        match_method = None
        if confirmed is not None:
            if (not isinstance(confirmed, dict) or confirmed.get("approved") is not True
                or not isinstance(source_hash, str) or not source_hash
                or confirmed.get("source_sha256") != source_hash):
                evidence.append("人工标签未经批准或来自其他 CST 工程，不能确认身份")
            else:
                identity = confirmed.get("part_type")
                if not isinstance(identity, str) or not identity:
                    raise ValueError(f"已确认 Solid 缺少有效类型：{solid_id}")
                status = "CONFIRMED"
                match_method = "APPROVED_SOLID_ID"
                evidence = ["人工确认 Solid ID 与真实零件身份"]
        elif rec["features"].get("step_sha256") in ref_by_hash:
            identity = ref_by_hash[rec["features"]["step_sha256"]]
            status = "CONFIRMED"
            match_method = "EXACT_APPROVED_REFERENCE_SHA256"
            evidence = ["与工程师确认的参考 CAD 文件字节一致"]
        else:
            nearest = similar_reference_candidate(rec["features"], references)
            if nearest is not None and nearest["ambiguous"]:
                evidence.append("相似参考件存在不同零件类别，不能自动确定")
            elif nearest is not None:
                hint["candidate"] = nearest["candidate"]
                status = "CANDIDATE"
                match_method = "APPROX_APPROVED_REFERENCE_FEATURES"
                evidence.append(
                    f"已批准参考形状特征相似度 {nearest['score']:.3f}；"
                    "此分数不是身份准确率，仍需人工确认"
                )
            elif hint["candidate"]:
                status = "CANDIDATE"
                evidence.append("形态相似不等于实际功能；不自动赋材料")
        results.append({
            "solid_id": solid_id,
            "identity": identity,
            "status": status,
            "candidate": hint["candidate"],
            "shape": hint["shape"],
            "match_method": match_method,
            "evidence": evidence,
        })
    return results
