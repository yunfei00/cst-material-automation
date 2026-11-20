"""Independent STEP B-rep recognition of modeled Phillips-head threaded screws.

All geometry is examined without PCB location, labels, BOM, or sample library.
This is a conservative candidate recognizer, not proof of fastener identity.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
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
                           radial_tolerance=0.02, min_radius_ratio=0.2):
    """Check actual 3D axis lines, not merely parallel directions.

    Angular tolerance is in radians; radial tolerance is in model units (mm).
    Ignore tiny cylindrical faces below 20% of head radius (recess/fillets).
    All remaining structural cylinders must share the head axis line.
    """
    if not cylinders:
        return {"verified": False, "reason": "无圆柱面", "checked": 0}
    head = max(cylinders, key=lambda item: item["radius"])
    reference = _unit(head["direction"])
    if reference is None:
        return {"verified": False, "reason": "头部轴向无效", "checked": 0}
    minimum_radius = head['radius'] * min_radius_ratio
    structural = [(index, candidate) for index, candidate in enumerate(cylinders)
                  if candidate['radius'] >= minimum_radius]
    failures = []
    max_angle = 0.0
    max_offset = 0.0
    for index, candidate in structural:
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
        "reason": "主体圆柱面共轴" if not failures else "主体存在偏轴或倾斜圆柱面",
        "checked": len(structural),
        "ignored_small_cylinders": len(cylinders) - len(structural),
        "minimum_radius": round(minimum_radius, 6),
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


def _solid_axis_core(solids, points, axis, origin, profile, *, tolerance=1e-5):
    """Require solid material on the shaft centerline, rejecting through-hole rings.

    A decorative annulus can have the same outer silhouette as a screw.
    Testing actual B-rep solid occupancy on the shaft axis distinguishes
    a solid shank from a coaxial through-hole without relying on mesh vertices.
    """
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN

    direction = _unit(axis)
    if not points or direction is None or not profile["verified"]:
        return {"verified": False, "reason": "无法确定杆部轴心"}
    axial = [_dot(tuple(p[i] - origin[i] for i in range(3)), direction)
             for p in points]
    low, high = min(axial), max(axial)
    if high - low <= 1e-6:
        return {"verified": False, "reason": "杆部轴向长度不足"}
    # Head is at one end; test multiple positions strictly inside the
    # opposite shaft portion, avoiding end faces, chamfers and recesses.
    head_at_low = profile["left_head"]
    fractions = (0.65, 0.75, 0.85)
    sample_z = [low + (high - low) * (f if head_at_low else 1.0 - f)
                for f in fractions]
    inside = []
    for z in sample_z:
        point = gp_Pnt(*(origin[i] + z * direction[i] for i in range(3)))
        occupied = False
        for solid in solids:
            classifier = BRepClass3d_SolidClassifier(solid)
            classifier.Perform(point, tolerance)
            if classifier.State() == TopAbs_IN:
                occupied = True
                break
        inside.append(occupied)
    verified = all(inside)
    return {"verified": verified,
            "reason": "杆部轴心为实体" if verified else "杆部轴心为空或存在贯通孔",
            "axis_core_samples": len(inside), "axis_core_inside": sum(inside)}


def _screw_verdict(coaxial: bool, stepped: bool, profile: bool,
                   solid_core: bool) -> str:
    """Classify by coaxial, asymmetric single-head geometry alone."""
    if coaxial and stepped and profile and solid_core:
        return "PHILLIPS_SCREW"
    return "OTHER"




def _helix_from_curve_points(points, axis, origin, shaft_radius):
    """Verify a sampled B-rep edge is a constant-pitch helix on the shaft."""
    direction = _unit(axis)
    if direction is None or len(points) < 24 or shaft_radius <= 0:
        return {"verified": False, "reason": "螺旋边采样不足"}
    u, v = _basis(direction)
    angles, heights, radii = [], [], []
    for point in points:
        delta = tuple(point[i] - origin[i] for i in range(3))
        z = _dot(delta, direction)
        x, y = _dot(delta, u), _dot(delta, v)
        radii.append(math.hypot(x, y))
        heights.append(z)
        angle = math.atan2(y, x)
        if angles:
            angle += 2 * math.pi * round((angles[-1] - angle) / (2 * math.pi))
        angles.append(angle)
    turns = abs(angles[-1] - angles[0]) / (2 * math.pi)
    mean_r = sum(radii) / len(radii)
    if (turns < 1.75 or not 0.55 * shaft_radius <= mean_r <= 1.45 * shaft_radius
            or max(radii) - min(radii) > max(0.02, mean_r * 0.08)):
        return {"verified": False, "turns": round(turns, 2),
                "reason": "不满足杆部恒半径多圈螺旋"}
    mean_a = sum(angles) / len(angles)
    mean_z = sum(heights) / len(heights)
    denominator = sum((a - mean_a) ** 2 for a in angles)
    if denominator < 1e-8:
        return {"verified": False, "reason": "螺旋角变化不足"}
    slope = sum((a - mean_a) * (z - mean_z) for a, z in zip(angles, heights)) / denominator
    pitch = abs(slope) * 2 * math.pi
    residual = math.sqrt(sum((z - mean_z - slope * (a - mean_a)) ** 2
                             for a, z in zip(angles, heights)) / len(heights))
    verified = pitch > 0.02 and residual < max(0.02, pitch * 0.08)
    return {"verified": verified, "turns": round(turns, 2),
            "pitch_mm": round(pitch, 5), "residual_mm": round(residual, 5),
            "reason": "B-rep 恒导程螺旋边" if verified else "螺距拟合不稳定"}


def _brep_thread_evidence(solids, axis, origin, shaft_radius):
    """Sample actual STEP edges, not a visually inferred mesh spiral."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    examined = 0
    for solid in solids:
        explorer = TopExp_Explorer(solid, TopAbs_EDGE)
        while explorer.More():
            edge_cast = getattr(TopoDS, "Edge_s", None) or getattr(TopoDS, "Edge", None)
            if edge_cast is None:
                raise RuntimeError("当前 OCP 不支持 Edge 转换")
            curve = BRepAdaptor_Curve(edge_cast(explorer.Current()))
            first, last = curve.FirstParameter(), curve.LastParameter()
            explorer.Next()
            if not math.isfinite(first) or not math.isfinite(last) or last <= first:
                continue
            examined += 1
            points = []
            for i in range(97):
                p = curve.Value(first + (last - first) * i / 96)
                points.append((p.X(), p.Y(), p.Z()))
            evidence = _helix_from_curve_points(points, axis, origin, shaft_radius)
            if evidence["verified"]:
                return {**evidence, "edges_examined": examined, "method": "brep_edge"}
    return {"verified": False, "edges_examined": examined,
            "reason": "未发现连续恒导程螺旋边", "method": "brep_edge"}


def _locating_pin_steps(cylinders, axis, *, minimum_levels=3):
    """Conservative multi-diameter pin evidence from long, coaxial faces.

    Small recess/fillet cylinders, short transition faces and near-equal radii
    cannot create a distinct locating shoulder.
    """
    if not cylinders:
        return {"verified": False, "levels": [], "reason": "无主体圆柱"}
    head = max(cylinders, key=lambda face: face["radius"])
    radius = head["radius"]
    reference = _unit(head["direction"])
    if reference is None:
        return {"verified": False, "levels": [], "reason": "轴向无效"}
    minimum = radius * 0.2
    levels = []
    for face in sorted(cylinders, key=lambda item: item["radius"], reverse=True):
        r = face["radius"]
        if r < minimum:
            continue
        direction = _unit(face["direction"])
        if direction is None or math.acos(min(1.0, abs(_dot(direction, reference)))) > 0.01:
            continue
        delta = tuple(face["axis_origin"][i] - head["axis_origin"][i] for i in range(3))
        projection = _dot(delta, reference)
        offset = math.sqrt(max(0.0, _dot(delta, delta) - projection * projection))
        if offset > 0.02:
            continue
        # A full cylindrical surface has area 2*pi*r*length; a partial
        # surface gives only a lower bound. This deliberately favors precision.
        supported_length = face["area"] / (2 * math.pi * r)
        if supported_length < 0.10:
            continue
        matching = next((level for level in levels
                         if abs(level["radius"] - r) <= radius * 0.06), None)
        if matching is None:
            levels.append({"radius": r, "supported_length": supported_length})
        else:
            matching["supported_length"] += supported_length
    levels.sort(key=lambda item: item["radius"], reverse=True)
    verified = len(levels) >= minimum_levels
    return {"verified": verified, "levels": levels,
            "reason": "存在三个及以上稳定同轴直径段" if verified else "稳定同轴直径段不足"}


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
    profile = {"verified": False, "reason": "未确认单侧大头"}
    core = {"verified": False, "reason": "未验证杆部实体轴心"}
    if coaxial["verified"] and stepped and shaft is not None and head_radius is not None:
        points = _mesh_points(solids)
        profile = _single_head_profile(points, axis, shaft["axis_origin"],
                                       head_radius, shaft["radius"])
        if profile["verified"]:
            core = _solid_axis_core(solids, points, axis, shaft["axis_origin"],
                                    profile)
    pin = _locating_pin_steps(cylinders, axis)
    thread = {"verified": False, "reason": "未满足头杆实体结构"}
    structural = coaxial["verified"] and stepped and profile["verified"] and core["verified"]
    if structural:
        thread = _brep_thread_evidence(solids, axis, shaft["axis_origin"], shaft["radius"])
    # A thread is positive screw evidence even for multi-shoulder screws.
    # A smooth two-level pin is ambiguous and must NOT default to screw.
    if structural and thread["verified"]:
        verdict = "PHILLIPS_SCREW"
    elif structural and pin["verified"]:
        verdict = "LOCATING_PIN"
    else:
        verdict = "OTHER"
    score = 100 if verdict != "OTHER" else 0
    return {"verdict": verdict,
            "score": score, "evidence": [
                f"同轴圆柱面 {len(cylinders)} 个",
                "检测到同轴变径头杆结构" if stepped else "未确认同轴变径头杆结构",
                coaxial["reason"], profile["reason"], core["reason"]],
            "axis": axis, "cylinder_count": len(cylinders),
            "head_shaft_supported": stepped,
            "coaxial_verified": coaxial["verified"],
            "coaxial_evidence": coaxial,
            "single_head_verified": profile["verified"],
            "single_head_evidence": profile,
            "solid_axis_core_verified": core["verified"],
            "solid_axis_core_evidence": core,
            "head_radius": head_radius,
            "shaft_radius": shaft["radius"] if shaft else None,
            "warning": "仅明确螺旋边确认螺丝；多台阶无螺纹确认定位销；证据不足不分类"}


def source_signature(root: Path) -> tuple[str, int]:
    """Fingerprint indexed STEP inputs without opening or hashing STEP files."""
    db = root / "geometry_features.sqlite3"
    digest = hashlib.sha256()
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "SELECT solid_id,step,step_sha256 FROM solids WHERE status='PASS' ORDER BY solid_id"
        ).fetchall()
    for sid, path, sha in rows:
        digest.update(json.dumps([sid, path, sha], ensure_ascii=False).encode("utf-8"))
        digest.update(b"\\n")
    return digest.hexdigest(), len(rows)


def complete_cached_report(root: Path) -> dict | None:
    """Return only a fully completed scan for the current indexed STEP inputs."""
    cache = root / "phillips_screw_recognition.json"
    if not cache.is_file():
        return None
    try:
        report = json.loads(cache.read_text(encoding="utf-8"))
        if (report.get("algorithm_version") != "phillips-geometry-v14"
                or not report.get("complete")):
            return None
        signature, total = source_signature(root)
        if (report.get("source_signature") != signature
                or len(report.get("results", {})) != total
                or report.get("summary", {}).get("total") != total):
            return None
        return report
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return None


def recommended_workers(task_count: int, *, cpu_count=None, available_memory=None) -> int:
    """Bound CPU-heavy OCP workers by CPU, free memory and pending work."""
    cpus = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    if available_memory is None:
        try:
            import psutil
            available_memory = psutil.virtual_memory().available
        except ImportError:
            available_memory = None
    # Reserve 1 GiB for the UI/OS and budget 1 GiB per independent STEP reader.
    memory_limit = (max(1, (available_memory - 1024**3) // 1024**3)
                    if available_memory is not None else 4)
    return max(1, min(task_count or 1, max(1, cpus - 1), memory_limit, 8))


def _recognize_one(task):
    """Worker only reads a single STEP; never touches the shared report."""
    sid, path, digest = task
    try:
        return sid, {"status": "PASS", "sha256": digest, **recognize_step(path)}
    except Exception as exc:  # noqa: BLE001
        return sid, {"status": "FAIL", "sha256": digest,
                     "error": f"{type(exc).__name__}: {exc}"}


def scan(folder: str | Path, *, progress=None, workers=None) -> dict:
    root = Path(folder).resolve(strict=True)
    db = root / "geometry_features.sqlite3"
    if not db.is_file():
        raise FileNotFoundError("缺少 P5 几何索引")
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as connection:
        rows = connection.execute("SELECT solid_id,step FROM solids WHERE status='PASS'").fetchall()
    signature, _ = source_signature(root)
    cache = root / "phillips_screw_recognition.json"
    complete = complete_cached_report(root)
    if complete is not None:
        summary = complete["summary"]
        if progress:
            progress(len(rows), len(rows), summary)
        return {"summary": summary, "output": str(cache), "from_cache": True}

    previous = {}
    if cache.is_file():
        try:
            old = json.loads(cache.read_text(encoding="utf-8"))
            if old.get("algorithm_version") == "phillips-geometry-v14":
                previous = old.get("results", {})
        except (OSError, ValueError, TypeError):
            pass
    summary = Counter({"total": len(rows), "failed": 0, "reused": 0})
    results = {}
    tasks = []
    if progress:
        progress(0, len(rows), summary)

    def save(complete_flag):
        tmp = cache.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({
            "algorithm_version": "phillips-geometry-v14",
            "summary": dict(summary), "results": results,
            "source_signature": signature, "complete": complete_flag,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(cache)

    def accept(sid, record):
        results[sid] = record
        if record.get("status") == "PASS":
            summary[record["verdict"]] += 1
        else:
            summary["failed"] += 1
        done = len(results)
        if progress and record.get("status") == "PASS" and record.get("verdict") in (
                "PHILLIPS_SCREW", "LOCATING_PIN"):
            progress(done, len(rows), summary, matched_id=sid,
                     matched_verdict=record["verdict"])
        if done % 20 == 0 or done == len(rows):
            save(done == len(rows))
        if progress and (done == 1 or done % 5 == 0 or done == len(rows)):
            progress(done, len(rows), summary)

    for sid, relpath in rows:
        try:
            path = (root / relpath).resolve()
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

    count = recommended_workers(len(tasks)) if workers is None else max(1, min(workers, len(tasks) or 1))
    if count == 1:
        for task in tasks:
            sid, record = _recognize_one(task)
            accept(sid, record)
    elif tasks:
        # Spawn-safe top-level worker. The parent alone updates cache/UI.
        with ProcessPoolExecutor(max_workers=count) as executor:
            futures = {executor.submit(_recognize_one, task): task[0] for task in tasks}
            for future in as_completed(futures):
                sid = futures[future]
                try:
                    _, record = future.result()
                except Exception as exc:  # noqa: BLE001
                    record = {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
                accept(sid, record)
    save(True)
    return {"summary": dict(summary), "output": str(cache), "workers": count}

def main(argv=None):
    parser = argparse.ArgumentParser(description="独立 STEP 螺丝几何识别")
    parser.add_argument("folder")
    parser.add_argument("--workers", type=int, default=None,
                        help="并行进程数，默认根据 CPU 和内存自动选择")
    args = parser.parse_args(argv)
    def emit(i, n, summary, *, matched_id=None, matched_verdict=None):
        event = {"event": "match" if matched_id is not None else "progress",
                 "done": i, "total": n, "matched": summary.get("PHILLIPS_SCREW", 0),
                 "failed": summary.get("failed", 0),
                 "pin_matched": summary.get("LOCATING_PIN", 0)}
        if matched_id is not None:
            event["solid_id"] = matched_id
            event["verdict"] = matched_verdict
        print(json.dumps(event, ensure_ascii=False), flush=True)

    result = scan(args.folder, progress=emit, workers=args.workers)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["summary"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
