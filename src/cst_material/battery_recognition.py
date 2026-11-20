"""Conservative, offline smartphone battery *shape* recognizer.

Geometry cannot distinguish a featureless battery from an identical OLED module.
BATTERY_SHAPE means geometrically compatible, NOT verified battery material.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from cst_material.occt_geometry import geometry_metrics, read_step, sha256_file
from cst_material.phillips_screw_recognition import recommended_workers, source_signature

VERSION = "battery-geometry-v1"


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


def _oriented_dimensions(solids):
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
            triangulation = BRep_Tool.Triangulation_s(face, face.Location())
            if triangulation is not None:
                nodes = triangulation.Nodes()
                # Bound memory for very detailed STEP parts.
                stride = max(1, len(nodes) // 2500)
                for i in range(1, len(nodes) + 1, stride):
                    p = nodes.Value(i)
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
    return sorted((float(x) for x in dims), reverse=True)


def recognize_step(path: Path) -> dict:
    solids = read_step(path)
    metrics = geometry_metrics(solids)
    dimensions = _oriented_dimensions(solids)
    volume = float(metrics["volume_mm3"])
    box_volume = math.prod(dimensions)
    fill = volume / box_volume if box_volume > 0 else 0.0
    verdict = shape_verdict(volume=volume, dimensions=dimensions, fill=fill,
                            solid_count=len(solids))
    return {
        "verdict": verdict,
        "volume_mm3": volume,
        "obb_dimensions_mm": dimensions,
        "length_width_ratio": dimensions[0] / dimensions[1],
        "flatness": dimensions[2] / math.sqrt(dimensions[0] * dimensions[1]),
        "fill_ratio": fill,
        "solid_body_count": len(solids),
        "identity_confirmed": False,
        "reason": ("扁平、较高填充率的长方体几何形态；不能仅凭形状确认电池"
                   if verdict == "BATTERY_SHAPE" else "不满足保守板状实体几何条件"),
    }


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
                or len(report.get("results", {})) != total
                or report.get("summary", {}).get("total") != total):
            return None
        return report
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return None


def scan(folder, *, workers=8, progress=None):
    root = Path(folder).resolve(strict=True)
    db = root / "geometry_features.sqlite3"
    if not db.is_file():
        raise FileNotFoundError("缺少 P5 几何索引")
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT solid_id,step FROM solids WHERE status='PASS'").fetchall()
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
    results, tasks = {}, []

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
                progress({"event": "match", "solid_id": sid, "verdict": "BATTERY_SHAPE",
                          "done": len(results), "total": len(rows)})
        if len(results) % 20 == 0 or len(results) == len(rows):
            save(len(results) == len(rows))
        if progress and (len(results) % 5 == 0 or len(results) == len(rows)):
            progress({"event": "progress", "done": len(results), "total": len(rows),
                      "matched": summary["matched"], "failed": summary["failed"]})

    for sid, relative in rows:
        try:
            path = (root / relative).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise FileNotFoundError("STEP 文件不存在或路径越界")
            digest = sha256_file(path)
            cached = previous.get(sid)
            if cached and cached.get("sha256") == digest and cached.get("status") == "PASS":
                summary["reused"] += 1
                accept(sid, cached)
            else:
                tasks.append((sid, str(path), digest))
        except Exception as exc:  # noqa: BLE001
            accept(sid, {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"})
    count = max(1, min(int(workers), len(tasks) or 1, os.cpu_count() or 1))
    if count == 1:
        for task in tasks:
            accept(*_one(task))
    elif tasks:
        with ProcessPoolExecutor(max_workers=count) as executor:
            futures = {executor.submit(_one, task): task[0] for task in tasks}
            for future in as_completed(futures):
                try:
                    accept(*future.result())
                except Exception as exc:  # noqa: BLE001
                    accept(futures[future], {"status": "FAIL",
                                            "error": f"{type(exc).__name__}: {exc}"})
    save(True)
    return {"summary": dict(summary), "output": str(cache), "workers": count}


def main(argv=None):
    parser = argparse.ArgumentParser(description="手机电池几何形态识别")
    parser.add_argument("folder")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    def emit(event):
        print(json.dumps(event, ensure_ascii=False), flush=True)
    result = scan(args.folder, workers=args.workers, progress=emit)
    emit({"event": "complete", **result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
