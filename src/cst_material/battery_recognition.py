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

VERSION = "battery-geometry-v12"


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


def cavity_profile(solids, bbox, *, grid=13):
    """Coarse B-rep occupancy profile for internal voids and open recesses.

    Scan the three principal axis-aligned sections. An empty sample is a
    feature only when occupied samples surround it in the same section.
    This is evidence of a structural pocket, not proof of battery identity.
    """
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN, TopAbs_ON

    if len(bbox) != 6 or any(not math.isfinite(v) for v in bbox):
        return {"status": "UNAVAILABLE", "reason": "invalid bounding box"}
    spans = [bbox[i + 3] - bbox[i] for i in range(3)]
    if min(spans) <= 0:
        return {"status": "UNAVAILABLE", "reason": "degenerate bounding box"}
    classifiers = [BRepClass3d_SolidClassifier(s) for s in solids]
    axes = sorted(range(3), key=lambda i: spans[i], reverse=True)
    sections = []
    for depth_axis in axes:
        plane_axes = [i for i in range(3) if i != depth_axis]
        # Several depth sections avoid mistaking a shallow surface recess
        # for a through-hole, while keeping computation bounded.
        for depth in (0.25, 0.5, 0.75):
            occupied = []
            for row in range(grid):
                line = []
                for col in range(grid):
                    p = [bbox[i] + spans[i] * 0.5 for i in range(3)]
                    p[depth_axis] = bbox[depth_axis] + spans[depth_axis] * depth
                    for axis, fraction in zip(plane_axes,
                                              ((col + 0.5) / grid, (row + 0.5) / grid)):
                        p[axis] = bbox[axis] + spans[axis] * fraction
                    point = gp_Pnt(*p)
                    inside = False
                    for classifier in classifiers:
                        classifier.Perform(point, 1e-6)
                        if classifier.State() in (TopAbs_IN, TopAbs_ON):
                            inside = True
                            break
                    line.append(inside)
                occupied.append(line)
            voids = set()
            for y in range(1, grid - 1):
                for x in range(1, grid - 1):
                    if occupied[y][x]:
                        continue
                    if (any(occupied[y][:x]) and any(occupied[y][x + 1:])
                            and any(occupied[k][x] for k in range(y))
                            and any(occupied[k][x] for k in range(y + 1, grid))):
                        voids.add((x, y))
            if len(voids) < 3:
                continue
            components = []
            while voids:
                seed = voids.pop()
                stack = [seed]
                component = [seed]
                while stack:
                    x, y = stack.pop()
                    for neighbor in ((x - 1, y), (x + 1, y),
                                     (x, y - 1), (x, y + 1)):
                        if neighbor in voids:
                            voids.remove(neighbor)
                            stack.append(neighbor)
                            component.append(neighbor)
                if len(component) >= 3:
                    components.append(component)
            if components:
                sections.append({"axis": depth_axis, "depth": depth,
                                 "void_regions": len(components),
                                 "void_samples": sum(map(len, components))})
    return {"status": "DETECTED" if sections else "NOT_DETECTED",
            "sections": sections,
            "max_regions": max((v["void_regions"] for v in sections), default=0),
            "method": "axis_aligned_brep_occupancy_grid",
            "grid": grid}



def edge_groove_profile(solids, bbox, *, width_samples=33, length_sections=5):
    """Detect a U-profile at both longitudinal edges using exact B-rep faces.

    Two vertical walls and a connecting low bottom must occur on each side.
    Small radii are allowed: no minimum groove width or raster resolution.
    """
    from OCP.BRepBndLib import BRepBndLib
    from OCP.Bnd import Bnd_Box
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    spans = [float(bbox[i + 3] - bbox[i]) for i in range(3)]
    if any(not math.isfinite(v) or v <= 0 for v in spans):
        return {"status": "UNAVAILABLE", "reason": "invalid bbox"}
    length_axis, width_axis, height_axis = sorted(
        range(3), key=lambda i: spans[i], reverse=True)
    faces = []
    for solid in solids:
        explorer = TopExp_Explorer(solid, TopAbs_FACE)
        while explorer.More():
            face = (getattr(TopoDS, "Face_s", None) or TopoDS.Face)(
                explorer.Current())
            box = Bnd_Box()
            BRepBndLib.Add_s(face, box)
            bounds = box.Get()
            faces.append(([bounds[i] for i in range(3)],
                          [bounds[i + 3] for i in range(3)]))
            explorer.Next()

    # Identify pairs of long, approximately vertical faces bracketing a
    # narrow valley. The valley must have an independent low connecting face.
    # Use B-rep face extents rather than the previous fixed sampling grid.
    eps = max(1e-6, max(spans) * 1e-7)
    walls = []
    bottoms = []
    for lo, hi in faces:
        d = [hi[i] - lo[i] for i in range(3)]
        if d[length_axis] < spans[length_axis] * 0.5:
            continue
        if d[width_axis] <= eps and d[height_axis] > eps:
            walls.append((lo, hi))
        if d[height_axis] <= eps and d[width_axis] > eps:
            bottoms.append((lo, hi))

    side_hits = [0, 0]
    for i, (a, b) in enumerate(walls):
        for c, d in walls[i + 1:]:
            left = (a[width_axis] + b[width_axis]) / 2
            right = (c[width_axis] + d[width_axis]) / 2
            if left > right:
                left, right = right, left
                a, b, c, d = c, d, a, b
            separation = right - left
            if separation <= eps or separation >= spans[width_axis] * 0.15:
                continue
            # The two walls must overlap in the longitudinal direction.
            if min(b[length_axis], d[length_axis]) - max(
                    a[length_axis], c[length_axis]) < spans[length_axis] * 0.5:
                continue
            bottom_z = max(a[height_axis if False else height_axis],
                           c[height_axis])
            for low, high in bottoms:
                if low[width_axis] > left + eps or high[width_axis] < right - eps:
                    continue
                if abs(low[height_axis] - bottom_z) > max(eps, spans[height_axis] * 0.02):
                    continue
                if min(high[length_axis], b[length_axis], d[length_axis]) - max(
                        low[length_axis], a[length_axis], c[length_axis]
                ) < spans[length_axis] * 0.5:
                    continue
                center = (left + right) / 2
                position = (center - bbox[width_axis]) / spans[width_axis]
                if position < 0.20:
                    side_hits[0] += 1
                elif position > 0.80:
                    side_hits[1] += 1
                break

    detected = all(side_hits)
    return {
        "status": "DETECTED" if detected else "NOT_DETECTED",
        "method": "brep_bilateral_u_profile_faces",
        "left_u_profiles": side_hits[0],
        "right_u_profiles": side_hits[1],
        "feature_kind": "u_profile_with_two_walls_and_bottom",
    }



def recognize_step(path: Path) -> dict:
    solids = read_step(path)
    metrics = geometry_metrics(solids)
    dimensions, normal = _oriented_dimensions(solids, with_normal=True)
    volume = float(metrics["volume_mm3"])
    aabb_dimensions = sorted((float(v) for v in metrics["dimensions_mm"]), reverse=True)
    box_volume = math.prod(aabb_dimensions)
    fill = volume / box_volume if box_volume > 0 else 0.0
    # Broad rectangular envelope; ultra-thin cells are not rejected by a flatness floor.
    rectangular = (len(solids) == 1 and volume > 0 and
                   all(v > 0 for v in aabb_dimensions) and
                   aabb_dimensions[0] > aabb_dimensions[1] > aabb_dimensions[2] and
                   0.68 <= fill <= 1.01)
    verdict = "BATTERY_SHAPE" if rectangular else "OTHER"
    pocket = (edge_groove_profile(solids, metrics["bbox_mm"]) if rectangular
              else {"status": "NOT_CHECKED"})
    if verdict == "BATTERY_SHAPE" and pocket["status"] != "DETECTED":
        verdict = "OTHER"
    return {
        "verdict": verdict,
        "cavity_profile": pocket,
        "expansion_structure_shape": pocket["status"] == "DETECTED",
        "volume_mm3": volume,
        "obb_dimensions_mm": dimensions,
        "aabb_dimensions_mm": aabb_dimensions,
        "fill_method": "brep_aabb",
        "bbox_mm": metrics["bbox_mm"],
        "plane_normal": normal,
        "length_width_ratio": aabb_dimensions[0] / aabb_dimensions[1],
        "flatness": aabb_dimensions[2] / math.sqrt(aabb_dimensions[0] * aabb_dimensions[1]),
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
        a = first.get("aabb_dimensions_mm", first["obb_dimensions_mm"])
        b = second.get("aabb_dimensions_mm", second["obb_dimensions_mm"])
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
    limit = min(100, len(ranked))
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
    for index, task in enumerate(tasks):
        accept(*_one(task))
        if ((index + 1) % 10 == 0 and
                sum(rec.get("verdict") == "BATTERY_SHAPE" for rec in results.values()) >= 2):
            break
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
    summary["expansion_structures"] = sum(
        rec.get("status") == "PASS" and rec.get("expansion_structure_shape", False)
        for rec in results.values())
    summary["candidates"] = len(results)
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
