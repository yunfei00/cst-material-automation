"""Conservative, offline smartphone battery *shape* recognizer.

Geometry cannot distinguish a featureless battery from an identical OLED module.
BATTERY_SHAPE means geometrically compatible, NOT verified battery material.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path

from cst_material.occt_geometry import geometry_metrics, read_step, sha256_file
from cst_material.phillips_screw_recognition import source_signature

VERSION = "battery-geometry-v5"


def shape_verdict(*, volume, dimensions, fill, solid_count=1):
    """A scale-invariant conservative geometric compatibility test.

    Ratios are morphological bounds, not battery identity claims.
    """
    if solid_count != 1 or not math.isfinite(volume) or volume <= 0:
        return "UNKNOWN"
    if len(dimensions) != 3 or any(not math.isfinite(x) or x <= 0 for x in dimensions):
        return "UNKNOWN"
    length, width, thick = sorted(dimensions, reverse=True)
    if not (1.05 <= length / width <= 3.5):
        return "OTHER"
    if not (0.025 <= thick / math.sqrt(length * width) <= 0.30):
        return "OTHER"
    if not (0.68 <= fill <= 1.01):
        return "OTHER"
    return "BATTERY_SHAPE"


def _oriented_dimensions(solids, *, with_normal=False):
    """Find a stable body orientation from principal axes of tessellated vertices.

    Mesh vertices are used only for direction; exact B-rep volume is authoritative.
    """
    import numpy as np
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    points = []
    for solid in solids:
        BRepMesh_IncrementalMesh(solid, 0.3, False, 0.25, False)
        explorer = TopExp_Explorer(solid, TopAbs_FACE)
        while explorer.More():
            face = (getattr(TopoDS, "Face_s", None) or TopoDS.Face)(explorer.Current())
            location = face.Location()
            triangulation = BRep_Tool.Triangulation_s(face, location)
            if triangulation is not None:
                count = triangulation.NbNodes()
                transform = location.Transformation()
                # Bound memory for very detailed STEP parts.
                stride = max(1, count // 2500)
                for i in range(1, count + 1, stride):
                    p = triangulation.Node(i).Transformed(transform)
                    points.append((p.X(), p.Y(), p.Z()))
            explorer.Next()
    if len(points) < 8:
        raise ValueError("没有足够的实体表面采样点")
    coords = np.asarray(points, dtype=float)
    centered = coords - coords.mean(axis=0)
    _, _, basis = np.linalg.svd(centered, full_matrices=False)
    projected = centered @ basis.T
    dims = (projected.max(axis=0) - projected.min(axis=0)).tolist()
    if not all(math.isfinite(v) and v > 0 for v in dims):
        raise ValueError("退化的旋转包围盒")
    ordered = sorted((float(x) for x in dims), reverse=True)
    if with_normal:
        normal = basis[int(np.argmin(dims))].tolist()
        return ordered, [float(v) for v in normal]
    return ordered


def recognize_step(path: Path) -> dict:
    solids = read_step(path)
    metrics = geometry_metrics(solids)
    dimensions, normal = _oriented_dimensions(solids, with_normal=True)
    volume = float(metrics["volume_mm3"])
    box_volume = math.prod(dimensions)
    fill = volume / box_volume if box_volume > 0 else 0.0
    verdict = shape_verdict(volume=volume, dimensions=dimensions, fill=fill,
                            solid_count=len(solids))
    return {
        "verdict": verdict,
        "volume_mm3": volume,
        "obb_dimensions_mm": dimensions,
        "bbox_mm": metrics["bbox_mm"],
        "plane_normal": normal,
        "length_width_ratio": dimensions[0] / dimensions[1],
        "flatness": dimensions[2] / math.sqrt(dimensions[0] * dimensions[1]),
        "fill_ratio": fill,
        "solid_body_count": len(solids),
        "identity_confirmed": False,
        "reason": ("扁平、较高填充率的长方体几何形态；不能仅凭形状确认电池"
                   if verdict == "BATTERY_SHAPE" else "不满足保守板状实体几何条件"),
    }


def compatible_pair(first, second):
    """Require neighboring, similarly thick, coplanar separate packages.

    A second plausible rectangle alone is not sufficient evidence of two cells.
    """
    try:
        a, b = first["obb_dimensions_mm"], second["obb_dimensions_mm"]
        na, nb = first["plane_normal"], second["plane_normal"]
        ba, bb = first["bbox_mm"], second["bbox_mm"]
        if any(len(v) != n for v, n in ((a, 3), (b, 3), (na, 3),
                                        (nb, 3), (ba, 6), (bb, 6))):
            return False
        values = [*a, *b, *na, *nb, *ba, *bb]
        if not all(math.isfinite(float(v)) for v in values):
            return False
        if min(a[2], b[2]) <= 0 or max(a[2], b[2]) / min(a[2], b[2]) > 1.35:
            return False
        dot = abs(sum(x * y for x, y in zip(na, nb)))
        if dot < 0.97:
            return False
        ca = [(ba[i] + ba[i + 3]) / 2 for i in range(3)]
        cb = [(bb[i] + bb[i + 3]) / 2 for i in range(3)]
        delta = [cb[i] - ca[i] for i in range(3)]
        normal_offset = abs(sum(delta[i] * na[i] for i in range(3)))
        if normal_offset > max(a[2], b[2]) * 1.5:
            return False
        gaps = [max(0.0, ba[i] - bb[i + 3], bb[i] - ba[i + 3])
                for i in range(3)]
        distance = math.sqrt(sum(g * g for g in gaps))
        if distance > 0.20 * min(a[0], b[0]):
            return False
        center_distance = math.sqrt(sum(v * v for v in delta))
        return center_distance >= 0.20 * min(a[1], b[1])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return False


def _one(task):
    sid, path, digest = task
    try:
        return sid, {"status": "PASS", "sha256": digest, **recognize_step(Path(path))}
    except Exception as exc:  # noqa: BLE001
        return sid, {"status": "FAIL", "sha256": digest,
                     "error": f"{type(exc).__name__}: {exc}"}


def complete_cached_report(root: Path):
    cache = root / "battery_recognition.json"
    if not cache.is_file():
        return None
    try:
        report = json.loads(cache.read_text(encoding="utf-8"))
        signature, total = source_signature(root)
        if (report.get("algorithm_version") != VERSION
                or report.get("source_signature") != signature
                or not report.get("complete")
                or report.get("summary", {}).get("total") != total):
            return None
        return report
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return None


def scan(folder, *, workers=1, progress=None):
    root = Path(folder).resolve(strict=True)
    db = root / "geometry_features.sqlite3"
    if not db.is_file():
        raise FileNotFoundError("缺少 P5 几何索引")
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT solid_id,step,features_json,step_sha256 FROM solids WHERE status='PASS'").fetchall()
    signature, _ = source_signature(root)
    cache = root / "battery_recognition.json"
    old = complete_cached_report(root)
    if old is not None:
        return {"summary": old["summary"], "output": str(cache), "from_cache": True}
    previous = {}
    if cache.is_file():
        try:
            report = json.loads(cache.read_text(encoding="utf-8"))
            if report.get("algorithm_version") == VERSION:
                previous = report.get("results", {})
        except (OSError, ValueError, TypeError):
            pass
    summary = Counter({"total": len(rows), "failed": 0, "matched": 0, "reused": 0})
    ranked = []
    for sid, relative, raw, sha in rows:
        try:
            volume = float((json.loads(raw) if raw else {}).get("volume_mm3", 0))
            if not math.isfinite(volume) or volume < 0:
                volume = 0
        except (ValueError, TypeError):
            volume = 0
        ranked.append((volume, sid, relative, sha))
    ranked.sort(reverse=True)
    limit = min(10, len(ranked))
    ranked = ranked[:limit]
    if progress:
        progress({"event": "phase", "message": f"体积排序完成：精查前 {limit} 个",
                  "total": len(rows), "candidates": limit})
    results, tasks = {}, []
    summary["candidates"] = limit

    def save(complete):
        tmp = cache.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({
            "algorithm_version": VERSION, "complete": complete,
            "source_signature": signature, "summary": dict(summary),
            "results": results,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(cache)

    def accept(sid, rec):
        results[sid] = rec
        if rec["status"] == "FAIL":
            summary["failed"] += 1
        elif rec.get("verdict") == "BATTERY_SHAPE":
            summary["matched"] += 1

        if progress:
            progress({"event": "progress", "done": len(results), "total": limit,
                      "matched": summary["matched"], "failed": summary["failed"]})

    for _volume, sid, relative, indexed_sha in ranked:
        try:
            path = (root / relative).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise FileNotFoundError("STEP 文件不存在或路径越界")
            digest = indexed_sha or sha256_file(path)
            cached = previous.get(sid)
            if cached and cached.get("sha256") == digest and cached.get("status") == "PASS":
                summary["reused"] += 1
                accept(sid, cached)
            else:
                tasks.append((sid, str(path), digest))
        except Exception as exc:  # noqa: BLE001
            accept(sid, {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"})
    if progress:
        progress({"event": "phase", "message": f"正在顺序精查 {len(tasks)} 个 STEP",
                  "done": len(results), "total": limit})
    count = 1
    for task in tasks:
        accept(*_one(task))
    matches = [(sid, rec) for sid, rec in results.items()
               if rec.get("status") == "PASS" and rec.get("verdict") == "BATTERY_SHAPE"]
    def geometry_key(item):
        sid, rec = item
        return (float(rec.get("fill_ratio", 0)),
                -abs(float(rec.get("length_width_ratio", 0)) - 1.8),
                -abs(float(rec.get("flatness", 0)) - 0.10),
                float(rec.get("volume_mm3", 0)), sid)
    winner = max(matches, key=geometry_key)[0] if matches else None
    selected = {winner} if winner else set()
    if winner:
        partner = [item for item in matches if item[0] != winner
                   and compatible_pair(results[winner], item[1])]
        if partner:
            selected.add(max(partner, key=geometry_key)[0])
    for sid, rec in matches:
        if sid not in selected:
            rec["verdict"] = "OTHER"
            rec["reason"] = "几何形态相符，但缺少与已识别电芯的可靠成组关系"
    summary["matched"] = len(selected)
    summary["cell_configuration"] = ("dual" if len(selected) == 2 else
                                     "single" if selected else "unknown")
    for sid in sorted(selected):
        if progress:
            progress({"event": "match", "solid_id": sid, "verdict": "BATTERY_SHAPE",
                      "done": len(results), "total": limit})
    save(True)
    return {"summary": dict(summary), "output": str(cache), "workers": count}


def main(argv=None):
    parser = argparse.ArgumentParser(description="手机电池几何形态识别")
    parser.add_argument("folder")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)
    def emit(event):
        print(json.dumps(event, ensure_ascii=False), flush=True)
    result = scan(args.folder, workers=args.workers, progress=emit)
    emit({"event": "complete", **result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
