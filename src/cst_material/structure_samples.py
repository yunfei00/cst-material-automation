"""Offline part-identity sample library; never infers or assigns materials."""
from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path

from cst_material.solid_recognition import compare_reference_geometry

SCHEMA_VERSION = 1


def extract_shape_features(metrics: dict) -> dict:
    """Extract portable, JSON-safe geometric descriptors from exact OCP metrics."""
    dims = metrics.get("dimensions_mm")
    if not isinstance(dims, (list, tuple)) or len(dims) != 3:
        raise ValueError("缺少有效的 XYZ 几何尺寸")
    if any(
        isinstance(x, bool) or not isinstance(x, (int, float))
        or not math.isfinite(x) or x <= 0 for x in dims
    ):
        raise ValueError("几何尺寸必须为三个有限正数")
    dimensions = sorted(float(x) for x in dims)
    volume = metrics.get("volume_mm3")
    area = metrics.get("surface_mm2")
    if any(
        isinstance(x, bool) or not isinstance(x, (int, float))
        or not math.isfinite(x) or x <= 0 for x in (volume, area)
    ):
        raise ValueError("缺少有效体积或面积")
    surfaces = metrics.get("surface_types", {})
    if not isinstance(surfaces, dict) or any(
        not isinstance(k, str) or isinstance(v, bool)
        or not isinstance(v, int) or v < 0 for k, v in surfaces.items()
    ):
        raise ValueError("曲面类型统计无效")
    return {
        "dimensions_mm": dimensions,
        "dimension_ratios": [
            dimensions[0] / dimensions[2], dimensions[1] / dimensions[2],
        ],
        "volume_mm3": float(volume),
        "surface_mm2": float(area),
        "bbox_fill_ratio": float(volume) / math.prod(dimensions),
        "surface_volume_ratio_mm_inv": float(area) / float(volume),
        "face_count": int(metrics.get("face_count", 0)),
        "edge_count": int(metrics.get("edge_count", 0)),
        "surface_types": dict(sorted(surfaces.items())),
    }


def load_samples(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "samples": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION
        or not isinstance(data.get("samples"), list)
    ):
        raise ValueError("结构样本库格式不受支持")
    return data


def save_confirmed_sample(
    path: str | Path, sample_id: str, part_type: str, metrics: dict,
    *, source_sha256: str = "",
) -> dict:
    """Persist an explicitly engineer-confirmed identity, never a prediction."""
    sample_id, part_type = sample_id.strip(), part_type.strip()
    if not sample_id or not part_type:
        raise ValueError("样本 ID 和零件类别不能为空")
    features = extract_shape_features(metrics)
    target = Path(path)
    data = load_samples(target)
    samples = data["samples"]
    if any(item.get("sample_id") == sample_id for item in samples):
        raise ValueError("样本 ID 已存在；请使用新 ID 记录不同结构")
    sample = {
        "sample_id": sample_id,
        "part_type": part_type,
        "approved": True,
        "source_sha256": source_sha256,
        "features": features,
    }
    samples.append(sample)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".samples-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, target)
    finally:
        Path(temp).unlink(missing_ok=True)
    return sample


def search_similar_samples(
    metrics: dict, library: dict, *, limit: int = 5,
) -> list[dict]:
    """Rank historical labels; score is similarity, NOT classification confidence."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit 必须为正整数")
    features = extract_shape_features(metrics)
    results = []
    for sample in library.get("samples", []):
        if not isinstance(sample, dict) or sample.get("approved") is not True:
            continue
        reference = sample.get("features")
        if not isinstance(reference, dict):
            continue
        score = compare_reference_geometry(features, reference)
        if score is not None:
            results.append({
                "sample_id": sample["sample_id"],
                "part_type": sample["part_type"],
                "similarity": score,
            })
    results.sort(key=lambda row: (-row["similarity"], row["sample_id"]))
    return results[:limit]
