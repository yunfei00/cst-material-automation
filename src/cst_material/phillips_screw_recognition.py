"""Independent STEP B-rep recognition of modeled Phillips-head threaded screws.

All geometry is examined without PCB location, labels, BOM, or sample library.
This is a conservative candidate recognizer, not proof of fastener identity.
"""
from __future__ import annotations

import argparse
import itertools
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
    """Detect a moving radial ridge in cylindrical coordinates.

    Requires a periodic angular phase that progresses along the screw axis.
    """
    if len(samples) < 120:
        return {"verified": False, "turns": 0, "reason": "螺纹采样不足"}
    zmin = min(p[0] for p in samples)
    zmax = max(p[0] for p in samples)
    span = zmax - zmin
    if span <= 1e-6:
        return {"verified": False, "turns": 0, "reason": "轴向范围不足"}
    nz = 96
    sums = [[0.0] * bins for _ in range(nz)]
    counts = [[0] * bins for _ in range(nz)]
    for z, angle, radius in samples:
        i = min(nz - 1, int((z - zmin) / span * nz))
        j = int((angle % (2 * math.pi)) / (2 * math.pi) * bins) % bins
        sums[i][j] += radius
        counts[i][j] += 1
    profiles = []
    for i in range(nz):
        observed = [(j, sums[i][j] / counts[i][j])
                    for j in range(bins) if counts[i][j]]
        if len(observed) < bins * 0.5:
            continue
        radii = [v for _, v in observed]
        if max(radii) - min(radii) < max(0.01, sum(radii) / len(radii) * 0.01):
            continue
        profiles.append((i, max(observed, key=lambda p: p[1])[0]))
    if len(profiles) < 24:
        return {"verified": False, "turns": 0, "reason": "没有足够螺纹截面"}
    # Peak phase must move coherently, not randomly jump between angular bins.
    steps = []
    for (_, previous), (_, current) in itertools.pairwise(profiles):
        delta = ((current - previous + bins / 2) % bins) - bins / 2
        if delta:
            steps.append(delta)
    positive = sum(step > 0 for step in steps)
    negative = sum(step < 0 for step in steps)
    dominant = max(positive, negative)
    total = abs(sum(steps))
    turns = total / bins
    verified = (turns >= min_turns and len(steps) >= 16
                and dominant / len(steps) >= 0.8)
    return {"verified": verified, "turns": round(turns, 2),
            "reason": "螺旋周期证据" if verified else "螺旋周期不充分"}


def _cross_recess_profile(points, *, head_radius):
    """Require four radial arms and reject a uniformly recessed disk."""
    if len(points) < 40 or head_radius <= 0:
        return {"verified": False, "reason": "头部凹槽采样不足"}
    deep = [(x, y) for x, y, depth in points
            if depth > head_radius * 0.025
            and head_radius * 0.12 < math.hypot(x, y) < head_radius * 0.85]
    if len(deep) < 20:
        return {"verified": False, "reason": "未检测到凹槽"}
    angles = [math.atan2(y, x) for x, y in deep]
    best = 0
    best_fraction = 0.0
    for k in range(45):
        rotation = k * math.pi / 90
        arms = [0, 0, 0, 0]
        hits = 0
        for angle in angles:
            relative = (angle - rotation) % (2 * math.pi)
            arm = round(relative / (math.pi / 2)) % 4
            distance = abs((relative - arm * math.pi / 2 + math.pi)
                           % (2 * math.pi) - math.pi)
            if distance < 0.16:
                arms[arm] += 1
                hits += 1
        if min(arms) > best:
            best = min(arms)
            best_fraction = hits / len(angles)
    verified = best >= 4 and best_fraction >= 0.7
    return {"verified": verified, "arm_min_support": best,
            "reason": "四向十字凹槽证据" if verified else "凹槽不是明确的四向十字"}


def _mesh_points(solids, *, deflection=0.035):
    """Extract exact B-rep tessellation vertices; no STL files required."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    points = []
    for solid in solids:
        BRepMesh_IncrementalMesh(solid, deflection, False, 0.15, True)
        explorer = TopExp_Explorer(solid, TopAbs_FACE)
        while explorer.More():
            downcast_face = getattr(TopoDS, "Face_s", None) or getattr(TopoDS, "Face", None)
            if downcast_face is None:
                raise RuntimeError("当前 OCP 版本不支持 TopoDS 面转换")
            face = downcast_face(explorer.Current())
            location = face.Location()
            mesh = BRep_Tool.Triangulation_s(face, location)
            if mesh is not None:
                transform = location.Transformation()
                for i in range(1, mesh.NbNodes() + 1):
                    point = mesh.Node(i).Transformed(transform)
                    points.append((point.X(), point.Y(), point.Z()))
            explorer.Next()
    return points


def _basis(axis):
    reference = (1.0, 0.0, 0.0) if abs(axis[0]) < 0.8 else (0.0, 1.0, 0.0)
    u = _unit((axis[1] * reference[2] - axis[2] * reference[1],
               axis[2] * reference[0] - axis[0] * reference[2],
               axis[0] * reference[1] - axis[1] * reference[0]))
    v = (axis[1] * u[2] - axis[2] * u[1],
         axis[2] * u[0] - axis[0] * u[2],
         axis[0] * u[1] - axis[1] * u[0])
    return u, v


def _sample_evidence(solids, axis, shaft_origin, shaft_radius, head_radius):
    points = _mesh_points(solids)
    u, v = _basis(axis)
    cylindrical = []
    projected = []
    for point in points:
        relative = tuple(point[i] - shaft_origin[i] for i in range(3))
        z = _dot(relative, axis)
        x, y = _dot(relative, u), _dot(relative, v)
        r = math.hypot(x, y)
        projected.append((z, x, y, r))
    if not projected:
        return ({"verified": False, "reason": "无网格顶点"},
                {"verified": False, "reason": "无网格顶点"})
    # Shaft is the region whose outer envelope is near the shaft radius.
    zs = sorted(p[0] for p in projected if p[3] < shaft_radius * 1.35)
    if len(zs) >= 20:
        low, high = zs[int(len(zs) * 0.05)], zs[int(len(zs) * 0.95)]
        for z, x, y, r in projected:
            if low <= z <= high and shaft_radius * 0.6 <= r <= shaft_radius * 1.35:
                cylindrical.append((z, math.atan2(y, x), r))
    thread = _thread_periodicity(cylindrical)
    # Head top is either axial extreme; pick the one with more wide-radius points.
    zlo = min(p[0] for p in projected)
    zhi = max(p[0] for p in projected)
    margin = max(0.02, head_radius * 0.08)
    ends = []
    for top, sign in ((zlo, -1), (zhi, 1)):
        nearby = [(x, y, sign * (top - z)) for z, x, y, r in projected
                  if abs(z - top) <= head_radius * 0.65 and r <= head_radius]
        outer = sum(math.hypot(x, y) > head_radius * 0.75
                    and depth < margin for x, y, depth in nearby)
        ends.append((outer, nearby))
    _, head = max(ends, key=lambda item: item[0])
    # Mesh points alone do not guarantee that the recess has an empty cross:
    # require evidence and leave ambiguous geometries unverified.
    cross = _cross_recess_profile(head, head_radius=head_radius)
    return thread, cross


def _identify_head_shaft(cylinders, axis, *, min_ratio=1.08):
    """Choose the largest-radius cylinder as head, then a smaller coaxial shaft.

    Never choose the smallest face as the shaft: fillets and recess walls may
    produce tiny cylindrical faces unrelated to the main shank.
    """
    if not cylinders:
        return None, None, False
    head = max(cylinders, key=lambda item: item["radius"])
    candidates = []
    for candidate in cylinders:
        if candidate is head or candidate["radius"] * min_ratio >= head["radius"]:
            continue
        delta = tuple(head["axis_origin"][i] - candidate["axis_origin"][i]
                      for i in range(3))
        axial = _dot(delta, axis)
        radial = math.sqrt(max(0.0, _dot(delta, delta) - axial * axial))
        if radial > max(0.15, candidate["radius"] * 0.2):
            continue
        candidates.append(candidate)
    # Prefer the largest plausible shaft, not a tiny screw-hole or fillet.
    shaft = max(candidates, key=lambda item: (item["radius"], item["area"])) if candidates else None
    return head, shaft, shaft is not None


def _common_axis_cylinders(cylinders, *, angular_tolerance=0.01,
                           radial_tolerance=0.02):
    """Check actual 3D axis lines, not merely parallel directions.

    Angular tolerance is in radians; radial tolerance is in model units (mm).
    Every cylindrical face must share the head's axis line. A parallel
    displaced hole/cylinder is therefore rejected.
    """
    if not cylinders:
        return {"verified": False, "reason": "无圆柱面", "checked": 0}
    head = max(cylinders, key=lambda item: item["radius"])
    reference = _unit(head["direction"])
    if reference is None:
        return {"verified": False, "reason": "头部轴向无效", "checked": 0}
    failures = []
    max_angle = 0.0
    max_offset = 0.0
    for index, candidate in enumerate(cylinders):
        direction = _unit(candidate["direction"])
        if direction is None:
            failures.append(index)
            continue
        cosine = min(1.0, abs(_dot(reference, direction)))
        angle = math.acos(cosine)
        delta = tuple(candidate["axis_origin"][i] - head["axis_origin"][i]
                      for i in range(3))
        projection = _dot(delta, reference)
        offset = math.sqrt(max(0.0, _dot(delta, delta) - projection * projection))
        max_angle = max(max_angle, angle)
        max_offset = max(max_offset, offset)
        if angle > angular_tolerance or offset > radial_tolerance:
            failures.append(index)
    return {
        "verified": not failures,
        "reason": "所有圆柱面共轴" if not failures else "存在偏轴或倾斜圆柱面",
        "checked": len(cylinders),
        "failed_indices": failures,
        "max_angle_deg": round(math.degrees(max_angle), 5),
        "max_offset_mm": round(max_offset, 6),
    }


def _single_head_profile(points, axis, origin, head_radius, shaft_radius):
    """Verify an asymmetric two-ended outer envelope, independent of face count.

    Sample the outer radial envelope in axial slices. The head must occupy
    exactly one end, while the opposite end must have shaft-sized radius.
    An hourglass/dumbbell with two large ends is rejected.
    """
    direction = _unit(axis)
    if not points or direction is None or not head_radius or not shaft_radius:
        return {"verified": False, "reason": "端部轮廓数据不足"}
    projected = []
    for point in points:
        delta = tuple(point[i] - origin[i] for i in range(3))
        z = _dot(delta, direction)
        radial = math.sqrt(max(0.0, _dot(delta, delta) - z * z))
        projected.append((z, radial))
    lo = min(z for z, _ in projected)
    hi = max(z for z, _ in projected)
    if hi - lo <= 1e-6:
        return {"verified": False, "reason": "轴向长度不足"}
    bins = 24
    radii = [[] for _ in range(bins)]
    for z, radius in projected:
        index = min(bins - 1, int((z - lo) / (hi - lo) * bins))
        radii[index].append(radius)
    envelope = [max(values) if values else None for values in radii]
    # Evaluate both ends over multiple slices; a single chamfered tip is not
    # enough evidence for a third head.
    left = [v for v in envelope[:4] if v is not None]
    right = [v for v in envelope[-4:] if v is not None]
    if len(left) < 2 or len(right) < 2:
        return {"verified": False, "reason": "两端截面采样不足"}
    large = shaft_radius + (head_radius - shaft_radius) * 0.55
    left_large = sum(v >= large for v in left) >= 2
    right_large = sum(v >= large for v in right) >= 2
    verified = left_large != right_large
    return {
        "verified": verified,
        "reason": "单侧大头、另一侧小杆" if verified else "两端轮廓不符合单头螺丝",
        "left_end_max_radius": max(left),
        "right_end_max_radius": max(right),
        "left_head": left_large,
        "right_head": right_large,
    }


def _screw_verdict(coaxial: bool, stepped: bool, profile: bool,
                   thread: bool, cross: bool) -> str:
    """Only positively verified single-head Phillips screws are selected."""
    if coaxial and stepped and profile and thread and cross:
        return "PHILLIPS_SCREW"
    return "OTHER"


def recognize_step(path: str | Path) -> dict:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Plane
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    solids = read_step(path)
    axes = {}
    faces = []
    for solid in solids:
        explorer = TopExp_Explorer(solid, TopAbs_FACE)
        while explorer.More():
            face_cast = getattr(TopoDS, 'Face_s', None) or getattr(TopoDS, 'Face', None)
            if face_cast is None:
                raise RuntimeError('OCP TopoDS 缺少 Face 转换接口')
            face = face_cast(explorer.Current())
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
            if key:
                origin = cylinder.Axis().Location()
                cylinders.append({"radius": cylinder.Radius(), "area": area,
                                  "direction": (direction.X(), direction.Y(), direction.Z()),
                                  "axis_origin": (origin.X(), origin.Y(), origin.Z())})
        elif kind == GeomAbs_Plane:
            plane = adaptor.Plane()
            normal = plane.Axis().Direction()
            if abs(_dot((normal.X(), normal.Y(), normal.Z()), axis)) > 0.95:
                head_planes.append({"area": area, "center": center})
    coaxial = _common_axis_cylinders(cylinders)
    head, shaft, stepped = _identify_head_shaft(cylinders, axis)
    head_radius = head["radius"] if head else None
    # Missing analytic cylinders is inconclusive for spline-threaded fasteners.
    thread = {"verified": False, "reason": "无同轴头杆"}
    cross = {"verified": False, "reason": "无同轴头杆"}
    profile = {"verified": False, "reason": "未确认单侧大头"}
    if coaxial["verified"] and stepped and shaft is not None and head_radius is not None:
        profile = _single_head_profile(_mesh_points(solids), axis,
                                       shaft["axis_origin"], head_radius,
                                       shaft["radius"])
    if profile["verified"]:
        thread, cross = _sample_evidence(
            solids, axis, shaft["axis_origin"], shaft["radius"], head_radius
        )
    score = (25 if shaft else 0) + (25 if stepped else 0)
    score += 25 if thread["verified"] else 0
    score += 25 if cross["verified"] else 0
    return {"verdict": _screw_verdict(
                coaxial["verified"], stepped, profile["verified"],
                thread["verified"], cross["verified"]
            ),
            "score": score, "evidence": [
                f"同轴圆柱面 {len(cylinders)} 个",
                "检测到同轴变径头杆结构" if stepped else "未确认同轴变径头杆结构",
                coaxial["reason"], profile["reason"], thread["reason"], cross["reason"]],
            "axis": axis, "cylinder_count": len(cylinders),
            "head_shaft_supported": stepped,
            "coaxial_verified": coaxial["verified"],
            "coaxial_evidence": coaxial,
            "single_head_verified": profile["verified"],
            "single_head_evidence": profile,
            "head_radius": head_radius,
            "shaft_radius": shaft["radius"] if shaft else None,
            "thread_verified": thread["verified"], "phillips_verified": cross["verified"],
            "thread_evidence": thread, "cross_evidence": cross,
            "warning": "仅同时满足头杆、螺旋周期、十字槽证据才认定为十字螺丝"}


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
            if old.get("algorithm_version") == "phillips-geometry-v8":
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
        except Exception as exc:  # noqa: BLE001 - isolate malformed individual STEP solids
            record = {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
            summary["failed"] += 1
        results[sid] = record
        if i % 20 == 0 or i == len(rows):
            tmp = cache.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"algorithm_version": "phillips-geometry-v8",
                "summary": summary, "results": {**previous, **results}},
                ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(cache)
        if progress and (i % 20 == 0 or i == len(rows)):
            progress(i, len(rows), summary)
    return {"summary": dict(summary), "output": str(cache)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="独立 STEP 十字螺丝几何识别")
    parser.add_argument("folder")
    args = parser.parse_args(argv)
    result = scan(args.folder, progress=lambda i,n,s: print(f"{i}/{n} {dict(s)}", flush=True))
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["summary"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
