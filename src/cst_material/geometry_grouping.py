"""Conservative, offline grouping of previously extracted Solid geometry features.

Groups are review aids only. Never infer part identity or material.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path

from cst_material.occt_geometry import sha256_file
from cst_material.solid_recognition import compare_reference_geometry


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _bucket(features):
    dims = features.get("dimensions_mm")
    if not isinstance(dims, list) or len(dims) != 3 or not all(_positive(v) for v in dims):
        return None
    volume = features.get("volume_mm3")
    area = features.get("surface_mm2")
    if not _positive(volume) or not _positive(area):
        return None
    # Logarithmic bins restrict comparisons to similar scales; adjacent bins
    # are intentionally not merged, favouring false splits over false merges.
    return tuple(round(math.log(float(v)) / 0.12) for v in (*sorted(dims), volume, area))


def group_solids(folder: str | Path, *, threshold: float = 0.985) -> dict:
    if not 0.9 <= threshold <= 1.0:
        raise ValueError("threshold 必须在 0.9..1.0")
    root = Path(folder).resolve(strict=True)
    db_path = root / "geometry_features.sqlite3"
    if not db_path.is_file():
        raise FileNotFoundError("缺少 P5 几何特征数据库")
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as db:
        metadata = db.execute("SELECT value FROM metadata WHERE key='manifest_sha256'").fetchone()
        manifest = root / "manifest.json"
        if metadata is None or (manifest.is_file() and metadata[0] != sha256_file(manifest)):
            raise ValueError("P5 数据库与 manifest 不匹配")
        rows = db.execute(
            "SELECT solid_id,step_sha256,features_json FROM solids WHERE status='PASS' ORDER BY solid_id"
        ).fetchall()
    buckets = defaultdict(list)
    ungrouped = []
    for sid, digest, payload in rows:
        try:
            feat = json.loads(payload)
            key = _bucket(feat)
        except (ValueError, TypeError):
            key = None
        if key is None:
            ungrouped.append(sid)
        else:
            buckets[key].append((sid, digest, feat))
    groups = []
    for bucket in sorted(buckets):
        representatives = []
        for sid, digest, feat in buckets[bucket]:
            match = None
            for group in representatives:
                # Same exported file hash is strong evidence for identical geometry.
                similarity = 1.0 if digest and digest == group["digest"] else compare_reference_geometry(feat, group["features"])
                if similarity is not None and similarity >= threshold:
                    match = (group, similarity)
                    break
            if match is None:
                group = {"representative": sid, "digest": digest, "features": feat,
                         "members": [{"solid_id": sid, "similarity": 1.0}]}
                representatives.append(group)
            else:
                match[0]["members"].append({"solid_id": sid, "similarity": match[1]})
        for group in representatives:
            groups.append({"representative": group["representative"],
                           "count": len(group["members"]), "members": group["members"]})
    for sid in ungrouped:
        groups.append({"representative": sid, "count": 1,
                       "members": [{"solid_id": sid, "similarity": None}]})
    groups.sort(key=lambda g: (-g["count"], g["representative"]))
    for index, group in enumerate(groups, 1):
        group["group_id"] = f"G{index:04d}"
    return {"schema_version": 1, "total": len(rows), "group_count": len(groups),
            "threshold": threshold, "groups": groups,
            "warning": "仅按几何特征保守分组；同组不代表材料或零件身份相同，需人工复核"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线 Solid 几何自动分组")
    parser.add_argument("folder")
    parser.add_argument("--threshold", type=float, default=0.985)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    result = group_solids(args.folder, threshold=args.threshold)
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已分组：{result['total']} 个 Solid -> {result['group_count']} 组")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
