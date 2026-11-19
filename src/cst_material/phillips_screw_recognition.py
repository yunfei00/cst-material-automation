"""Independent STEP B-rep recognition of modeled Phillips-head threaded screws.

All geometry is examined without PCB location, labels, BOM, or sample library.
This is a conservative candidate recognizer, not proof of fastener identity.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path

from cst_material.occt_geometry import read_step, sha256_file


def _unit(v):
    n = math.sqrt(sum(x*x for x in v))
    return tuple(x/n for x in v) if n > 1e-9 else None


def _dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def _axis_key(direction):
    u = _unit(direction)
    if u is None:
        return None
    if next((v for v in u if abs(v) > 1e-6), 1) < 0:
        u = tuple(-v for v in u)
    return tuple(round(x, 2) for x in u)



def _thread_periodicity(samples, *, min_turns=2, bins=24):
    """Check that radius maxima advance in angle as axial position increases.

    samples: (axial coordinate, angle radians, radius) on shaft surface.
    A mere circular cylinder has no axial radius modulation and is rejected.
    """
    if len(samples) < 80:
        return {"verified": False, "turns": 0, "reason": "螺纹采样不足"}
    zmin = min(p[0] for p in samples)
    zmax = max(p[0] for p in samples)
    span = zmax - zmin
    if span <= 1e-6:
        return {"verified": False, "turns": 0, "reason": "轴向范围不足"}
    # Compute mean radii on an axial/azimuth grid and find a diagonal ridge.
    nz = 96
    sums = [[0.0] * bins for _ in range(nz)]
    counts = [[0] * bins for _ in range(nz)]
    for z, angle, radius in samples:
        i = min(nz - 1, int((z - zmin) / span * nz))
        j = int((angle % (2 * math.pi)) / (2 * math.pi) * bins) % bins
        sums[i][j] += radius
        counts[i][j] += 1
    # Sparse, nonuniform CAD tessellation cannot prove a thread.
    populated = sum(bool(counts[i][j]) for i in range(nz) for j in range(bins))
    if populated < nz * bins * 0.35:
        return {"verified": False, "turns": 0, "reason": "螺纹网格覆盖不足"}
    profiles = []
    for i in range(nz):
        observed = [(j, sums[i][j] / counts[i][j])
                    for j in range(bins) if counts[i][j]]
        if len(observed) < bins * 0.6:
            continue
        values = [v for _, v in observed]
        if max(values) - min(values) < max(0.015, sum(values) / len(values) * 0.012):
            continue
        profiles.append((i, max(observed, key=lambda p: p[1])[0]))
    if len(profiles) < 30:
        return {"verified": False, "turns": 0, "reason": "没有明显的螺旋牙形"}
    # Unwrap the peak angle and require persistent directional rotation.
    total = 0.0
    coherent = 0
    last = profiles[0][1]
    for _, current in profiles[1:]:
        step = ((current - last + bins / 2) % bins) - bins / 2
        if step:
            total += step
            coherent += 1
        last = current
    turns = abs(total) / bins
    return {"verified": turns >= min_turns and coherent >= 15,
            "turns": round(turns, 2), "reason": "螺旋周期证据" if turns >= min_turns else "未观察到足够螺旋圈数"}


def _cross_recess_profile(points, *, head_radius):
    """Conservative cross-slot check on planar head-face interior points.

    Input is local head-plane coordinates (x, y, recess_depth). A Phillips
    recess has four radial arms, plus a deeper central intersection.
    """
    if len(points) < 40 or head_radius <= 0:
        return {"verified": False, "reason": "头部凹槽采样不足"}
    deep = [(x, y) for x, y, depth in points
            if depth > head_radius * 0.025
            and head_radius * 0.08 < math.hypot(x, y) < head_radius * 0.85]
    if len(deep) < 20:
        return {"verified": False, "reason": "未检测到凹槽"}
    # Angle-independent detection: rotate cross candidate through 0..90 deg.
    angles = [math.atan2(y, x) % (2 * math.pi) for x, y in deep]
    best = 0
    for k in range(45):
        rotation = k * math.pi / 90
        arms = [0, 0, 0, 0]
        for angle in angles:
            relative = (angle - rotation) % (2 * math.pi)
            arm = round(relative / (math.pi / 2)) % 4
            distance = abs((relative - arm * math.pi / 2 + math.pi) % (2 * math.pi) - math.pi)
            if distance < 0.16:
                arms[arm] += 1
        best = max(best, min(arms))
    return {"verified": best >= 4, "arm_min_support": best,
            "reason": "四向十字凹槽证据" if best >= 4 else "凹槽缺少四向支持"}

def recognize_step(path: str | Path) -> dict:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Plane
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    solids = read_step(path)
    axes = {}
    faces = []
    for solid in solids:
        explorer = TopExp_Explorer(solid, TopAbs_FACE)
        while explorer.More():
            face = TopoDS.Face_s(explorer.Current())
            adaptor = BRepAdaptor_Surface(face)
            kind = adaptor.GetType()
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face, props)
            area = abs(props.Mass())
            center = props.CentreOfMass()
            xyz = (center.X(), center.Y(), center.Z())
            faces.append((face, adaptor, kind, area, xyz))
            if kind in (GeomAbs_Cylinder, GeomAbs_Cone):
                surface = adaptor.Cylinder() if kind == GeomAbs_Cylinder else adaptor.Cone()
                direction = surface.Axis().Direction()
                axis = _axis_key((direction.X(), direction.Y(), direction.Z()))
                if axis:
                    axes[axis] = axes.get(axis, 0) + area
            explorer.Next()
    if not axes:
        return {"verdict": "OTHER", "score": 0, "evidence": ["无圆柱/圆锥轴向结构"]}
    axis = max(axes, key=axes.get)
    cylinders = []
    head_planes = []
    for _, adaptor, kind, area, center in faces:
        if kind == GeomAbs_Cylinder:
            cylinder = adaptor.Cylinder()
            direction = cylinder.Axis().Direction()
            key = _axis_key((direction.X(), direction.Y(), direction.Z()))
            if key and abs(_dot(key, axis)) > 0.98:
                origin = cylinder.Axis().Location()
                cylinders.append({"radius": cylinder.Radius(), "area": area,
                                  "axis_origin": (origin.X(), origin.Y(), origin.Z())})
        elif kind == GeomAbs_Plane:
            plane = adaptor.Plane()
            normal = plane.Axis().Direction()
            if abs(_dot((normal.X(), normal.Y(), normal.Z()), axis)) > 0.95:
                head_planes.append({"area": area, "center": center})
    cylinders.sort(key=lambda c: c["radius"])
    shaft = max(cylinders, key=lambda c: c["area"]) if cylinders else None
    # Distinct concentric cylinders indicate head/shaft, but must share axis.
    stepped = False
    if shaft:
        for other in cylinders:
            if other["radius"] < shaft["radius"] * 1.25:
                continue
            delta = tuple(other["axis_origin"][i] - shaft["axis_origin"][i] for i in range(3))
            radial = math.sqrt(max(0, _dot(delta, delta) - _dot(delta, axis)**2))
            if radial <= max(0.1, shaft["radius"] * 0.12):
                stepped = True
                break
    # Real threads are frequently represented by spline surfaces, not cylinders.
    # A repeated helical pattern and cross recess require dedicated validation.
    score = (35 if shaft else 0) + (25 if stepped else 0)
    return {"verdict": "SCREW_REVIEW" if stepped else "OTHER",
            "score": score, "evidence": [
                f"同轴圆柱面 {len(cylinders)} 个",
                "检测到同轴变径头杆结构" if stepped else "未确认同轴变径头杆结构",
                "螺纹螺旋性未验证", "十字槽未验证"],
            "axis": axis, "cylinder_count": len(cylinders),
            "head_shaft_supported": stepped,
            "thread_verified": False, "phillips_verified": False,
            "warning": "仅为头杆几何候选，尚不能判定十字螺丝"}


def scan(folder: str | Path, *, progress=None) -> dict:
    root = Path(folder).resolve(strict=True)
    db = root / "geometry_features.sqlite3"
    if not db.is_file():
        raise FileNotFoundError("缺少 P5 几何索引")
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as connection:
        rows = connection.execute("SELECT solid_id,step FROM solids WHERE status='PASS'").fetchall()
    cache = root / "phillips_screw_recognition.json"
    results = {}
    previous = {}
    if cache.is_file():
        try:
            old = json.loads(cache.read_text(encoding="utf-8"))
            if old.get("algorithm_version") == "phillips-geometry-v2":
                previous = old.get("results", {})
        except (OSError, ValueError, TypeError):
            pass
    summary = Counter({"total": len(rows), "failed": 0, "reused": 0})
    for i, (sid, relpath) in enumerate(rows, 1):
        try:
            path = (root / relpath).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise FileNotFoundError("STEP 文件不存在或路径越界")
            digest = sha256_file(path)
            cached = previous.get(sid)
            if cached and cached.get("sha256") == digest and cached.get("status") == "PASS":
                record = cached
                summary["reused"] += 1
            else:
                record = {"status": "PASS", "sha256": digest, **recognize_step(path)}
            summary[record["verdict"]] += 1
        except Exception as exc:
            record = {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
            summary["failed"] += 1
        results[sid] = record
        if i % 20 == 0 or i == len(rows):
            tmp = cache.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"algorithm_version": "phillips-geometry-v1",
                "summary": summary, "results": {**previous, **results}},
                ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(cache)
        if progress and (i % 20 == 0 or i == len(rows)):
            progress(i, len(rows), summary)
    return {"summary": dict(summary), "output": str(cache)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="独立 STEP 螺丝头杆结构扫描（十字槽/螺纹尚未验证）")
    parser.add_argument("folder")
    args = parser.parse_args(argv)
    result = scan(args.folder, progress=lambda i,n,s: print(f"{i}/{n} {dict(s)}", flush=True))
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["summary"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
