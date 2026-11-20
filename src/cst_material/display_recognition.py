"""Conservative smartphone display-assembly candidate recognition.

The recognizer deliberately does not claim that one featureless plate is a
screen.  It combines scale-free plate geometry, exact B-rep face evidence,
layer relationships, and location near either exterior side of the complete
assembly.  A symmetric front/back pair remains ambiguous by design.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path

from cst_material.occt_geometry import geometry_metrics, read_step, sha256_file
from cst_material.phillips_screw_recognition import source_signature
from cst_material.sheet_thickness import analyze_sheet_faces

VERSION = "display-assembly-geometry-v1"
CACHE_NAME = "display_recognition.json"
DEFAULT_MAX_CANDIDATES = 200
PARAMETERS = {
    "coarse_plate_index_min": 7.0,
    "coarse_relative_surface_min": 0.03,
    "coarse_aspect_ratio_range": [1.0, 6.0],
    "coarse_flatness_max": 0.70,
    "exact_aspect_ratio_range": [1.10, 3.60],
    "exact_flatness_max": 0.18,
    "exact_fill_ratio_range": [0.30, 1.08],
    "major_plane_coverage_min": 0.42,
    "sheet_pair_consistency_min": 0.45,
    "layer_normal_alignment_min": 0.96,
    "layer_projection_coverage_min": 0.72,
    "front_back_footprint_ratio_min": 0.65,
    "front_back_projection_coverage_min": 0.60,
}
DISPLAY_VERDICTS = {
    "DISPLAY_CANDIDATE",
    "PROBABLE_DISPLAY_MODULE",
    "STRONG_DISPLAY_EVIDENCE",
    "AMBIGUOUS_FRONT_BACK",
}


def _finite(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("几何字段必须为数值")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("几何字段必须为有限数值")
    return result


def _unit(vector: list[float] | tuple[float, ...]) -> list[float]:
    length = math.sqrt(sum(float(value) ** 2 for value in vector))
    if not math.isfinite(length) or length <= 1e-12:
        raise ValueError("退化方向向量")
    result = [float(value) / length for value in vector]
    first = next((value for value in result if abs(value) > 1e-9), 1.0)
    return [-value for value in result] if first < 0 else result


def _dot(first: list[float], second: list[float]) -> float:
    return sum(a * b for a, b in zip(first, second, strict=True))


def _indexed_record(row: tuple) -> dict:
    solid_id, step, raw, digest = row
    feature = json.loads(raw)
    if not isinstance(feature, dict):
        raise TypeError("features_json 必须为对象")
    volume = _finite(feature["volume_mm3"])
    surface = _finite(feature["surface_mm2"])
    dimensions = [_finite(value) for value in feature["dimensions_mm"]]
    bbox = [_finite(value) for value in feature["bbox_mm"]]
    if volume <= 0 or surface <= 0 or len(dimensions) != 3 or len(bbox) != 6:
        raise ValueError("索引几何体积、面积、尺寸或包围盒无效")
    if min(dimensions) <= 0 or any(bbox[i + 3] <= bbox[i] for i in range(3)):
        raise ValueError("索引几何尺寸或包围盒退化")
    ordered = sorted(dimensions, reverse=True)
    plate_index = surface / (volume ** (2.0 / 3.0))
    return {
        "solid_id": solid_id,
        "step": step,
        "step_sha256": digest,
        "indexed_volume_mm3": volume,
        "indexed_surface_mm2": surface,
        "indexed_dimensions_mm": dimensions,
        "indexed_bbox_mm": bbox,
        "indexed_aspect_ratio": ordered[0] / ordered[1],
        "indexed_flatness": ordered[2] / math.sqrt(ordered[0] * ordered[1]),
        "plate_index": plate_index,
    }


def _aabb_corners(bbox: list[float]) -> list[list[float]]:
    return [
        [bbox[3] if mask & 1 else bbox[0],
         bbox[4] if mask & 2 else bbox[1],
         bbox[5] if mask & 4 else bbox[2]]
        for mask in range(8)
    ]


def _oriented_profile(solids: list) -> dict:
    """Return a mesh-directed OBB; exact B-rep metrics remain authoritative."""
    import numpy as np
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    points: list[tuple[float, float, float]] = []
    for solid in solids:
        BRepMesh_IncrementalMesh(solid, 0.3, False, 0.25, False)
        explorer = TopExp_Explorer(solid, TopAbs_FACE)
        while explorer.More():
            face = (getattr(TopoDS, "Face_s", None) or TopoDS.Face)(explorer.Current())
            location = face.Location()
            mesh = BRep_Tool.Triangulation_s(face, location)
            if mesh is not None:
                stride = max(1, mesh.NbNodes() // 2500)
                transform = location.Transformation()
                for index in range(1, mesh.NbNodes() + 1, stride):
                    point = mesh.Node(index).Transformed(transform)
                    points.append((point.X(), point.Y(), point.Z()))
            explorer.Next()
    if len(points) < 8:
        raise ValueError("没有足够表面点计算旋转包围盒")
    coordinates = np.asarray(points, dtype=float)
    centered = coordinates - coordinates.mean(axis=0)
    _, _, raw_axes = np.linalg.svd(centered, full_matrices=False)
    axes = [_unit(axis.tolist()) for axis in raw_axes]
    extents = []
    bounds = []
    for axis in axes:
        values = coordinates @ np.asarray(axis)
        low, high = float(values.min()), float(values.max())
        bounds.append([low, high])
        extents.append(high - low)
    order = sorted(range(3), key=lambda index: extents[index], reverse=True)
    axes = [axes[index] for index in order]
    bounds = [bounds[index] for index in order]
    dimensions = [bounds[index][1] - bounds[index][0] for index in range(3)]
    if not all(math.isfinite(value) and value > 0 for value in dimensions):
        raise ValueError("旋转包围盒退化")
    center = [
        sum(axes[axis][coordinate] * sum(bounds[axis]) / 2 for axis in range(3))
        for coordinate in range(3)
    ]
    return {
        "obb_dimensions_mm": dimensions,
        "obb_axes": axes,
        "obb_center_mm": center,
        "plane_normal": axes[2],
    }


def _planar_profile(solids: list, footprint_mm2: float) -> dict:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    planes = []
    cylinder_faces = 0
    inner_wires = 0
    for solid in solids:
        explorer = TopExp_Explorer(solid, TopAbs_FACE)
        while explorer.More():
            face = (getattr(TopoDS, "Face_s", None) or TopoDS.Face)(explorer.Current())
            surface = BRepAdaptor_Surface(face)
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face, props)
            area = float(props.Mass())
            wires = TopExp_Explorer(face, TopAbs_WIRE)
            wire_count = 0
            while wires.More():
                wire_count += 1
                wires.Next()
            inner_wires += max(0, wire_count - 1)
            if surface.GetType() == GeomAbs_Plane:
                direction = surface.Plane().Axis().Direction()
                sign = -1.0 if face.Orientation() == TopAbs_REVERSED else 1.0
                planes.append({
                    "area_mm2": area,
                    "normal": _unit([
                        sign * direction.X(), sign * direction.Y(), sign * direction.Z()
                    ]),
                    "wire_count": wire_count,
                })
            elif surface.GetType() == GeomAbs_Cylinder:
                cylinder_faces += 1
            explorer.Next()
    planes.sort(key=lambda item: item["area_mm2"], reverse=True)
    coverage = planes[0]["area_mm2"] / footprint_mm2 if planes and footprint_mm2 else 0.0
    opposed = False
    if len(planes) >= 2:
        opposed = any(
            abs(_dot(planes[0]["normal"], other["normal"])) >= 0.96
            and other["area_mm2"] >= planes[0]["area_mm2"] * 0.45
            for other in planes[1:]
        )
    return {
        "plane_face_count": len(planes),
        "cylinder_face_count": cylinder_faces,
        "inner_wire_count": inner_wires,
        "major_plane_coverage": round(coverage, 6),
        "opposed_major_planes": opposed,
    }


def evaluate_exact_geometry(
    *, volume_mm3: float, solid_body_count: int, profile: dict,
    planar: dict, thickness: dict,
) -> dict:
    """Classify local shape only; identity requires assembly context later."""
    dimensions = [_finite(value) for value in profile.get("obb_dimensions_mm", [])]
    if len(dimensions) != 3 or min(dimensions) <= 0 or volume_mm3 <= 0:
        return {"verdict": "OTHER", "reason": "精确几何尺寸无效", "evidence": []}
    length, width, thick = dimensions
    aspect = length / width
    flatness = thick / math.sqrt(length * width)
    fill = volume_mm3 / math.prod(dimensions)
    evidence = [
        f"OBB 长宽比 {aspect:.3f}",
        f"相对薄度 {flatness:.5f}",
        f"OBB 填充率 {fill:.3f}",
        f"最大平面覆盖率 {planar.get('major_plane_coverage', 0):.3f}",
        (
            "检测到对向大平面"
            if planar.get("opposed_major_planes") else "未检测到对向大平面"
        ),
        (
            f"等厚平面配对 {thickness.get('plane_pair_count', 0)} 组，"
            f"一致性 {thickness.get('pair_consistency', 0):.3f}"
        ),
    ]
    if planar.get("inner_wire_count", 0):
        evidence.append(f"检测到内部边界环 {planar['inner_wire_count']} 个")
    if planar.get("cylinder_face_count", 0):
        evidence.append(f"检测到圆柱侧面 {planar['cylinder_face_count']} 个")
    checks = {
        "single_body": solid_body_count == 1,
        "display_aspect": (
            PARAMETERS["exact_aspect_ratio_range"][0]
            <= aspect
            <= PARAMETERS["exact_aspect_ratio_range"][1]
        ),
        "thin_plate": 0 < flatness <= PARAMETERS["exact_flatness_max"],
        "substantial_fill": (
            PARAMETERS["exact_fill_ratio_range"][0]
            <= fill
            <= PARAMETERS["exact_fill_ratio_range"][1]
        ),
        "major_planes": (
            planar.get("major_plane_coverage", 0)
            >= PARAMETERS["major_plane_coverage_min"]
            and bool(planar.get("opposed_major_planes"))
        ),
        "sheet_pair": (
            thickness.get("plane_pair_count", 0) >= 1
            and thickness.get("pair_consistency", 0)
            >= PARAMETERS["sheet_pair_consistency_min"]
        ),
    }
    verdict = "DISPLAY_CANDIDATE" if all(checks.values()) else "OTHER"
    local_structure = bool(
        planar.get("inner_wire_count", 0) >= 1
        or planar.get("cylinder_face_count", 0) >= 4
    )
    return {
        "verdict": verdict,
        "reason": (
            "大面积等厚板状实体；单体几何不能确认屏幕身份"
            if verdict == "DISPLAY_CANDIDATE"
            else "未同时满足保守屏幕板状几何条件"
        ),
        "recognition_stages": checks,
        "local_structure_evidence": local_structure,
        "length_width_ratio": aspect,
        "flatness": flatness,
        "fill_ratio": fill,
        "footprint_mm2": length * width,
        "evidence": evidence,
    }


def recognize_step(path: str | Path) -> dict:
    solids = read_step(path)
    metrics = geometry_metrics(solids)
    profile = _oriented_profile(solids)
    planar = _planar_profile(solids, math.prod(profile["obb_dimensions_mm"][:2]))
    thickness = analyze_sheet_faces(solids)
    local = evaluate_exact_geometry(
        volume_mm3=float(metrics["volume_mm3"]),
        solid_body_count=len(solids),
        profile=profile,
        planar=planar,
        thickness=thickness,
    )
    return {
        **local,
        **profile,
        "bbox_mm": metrics["bbox_mm"],
        "volume_mm3": metrics["volume_mm3"],
        "surface_mm2": metrics["surface_mm2"],
        "solid_body_count": len(solids),
        "planar_profile": planar,
        "thickness_profile": thickness,
        "identity_confirmed": False,
    }


def _obb_corners(record: dict) -> list[list[float]]:
    center = record["obb_center_mm"]
    axes = record["obb_axes"]
    dimensions = record["obb_dimensions_mm"]
    return [
        [
            center[coordinate] + sum(
                (1 if mask & (1 << axis) else -1)
                * axes[axis][coordinate] * dimensions[axis] / 2
                for axis in range(3)
            )
            for coordinate in range(3)
        ]
        for mask in range(8)
    ]


def projected_overlap(first: dict, second: dict) -> dict:
    axes = first["obb_axes"][:2]
    intervals = []
    for record in (first, second):
        corners = _obb_corners(record)
        intervals.append([
            [min(_dot(corner, axis) for corner in corners),
             max(_dot(corner, axis) for corner in corners)]
            for axis in axes
        ])
    overlap = [
        max(0.0, min(intervals[0][axis][1], intervals[1][axis][1])
            - max(intervals[0][axis][0], intervals[1][axis][0]))
        for axis in range(2)
    ]
    areas = [
        math.prod(interval[axis][1] - interval[axis][0] for axis in range(2))
        for interval in intervals
    ]
    intersection = math.prod(overlap)
    union = areas[0] + areas[1] - intersection
    return {
        "coverage": intersection / min(areas) if min(areas) > 0 else 0.0,
        "iou": intersection / union if union > 0 else 0.0,
    }


def layer_compatibility(first: dict, second: dict) -> dict:
    try:
        normal_dot = abs(_dot(first["plane_normal"], second["plane_normal"]))
        overlap = projected_overlap(first, second)
        normal = first["plane_normal"]
        center_delta = [
            second["obb_center_mm"][axis] - first["obb_center_mm"][axis]
            for axis in range(3)
        ]
        separation = abs(_dot(center_delta, normal))
        first_thick = first["obb_dimensions_mm"][2]
        second_thick = second["obb_dimensions_mm"][2]
        gap = max(0.0, separation - (first_thick + second_thick) / 2)
        width = min(first["obb_dimensions_mm"][1], second["obb_dimensions_mm"][1])
        gap_limit = max(4 * max(first_thick, second_thick), 0.04 * width)
        distinct = separation >= 0.15 * min(first_thick, second_thick)
        compatible = (
            normal_dot >= PARAMETERS["layer_normal_alignment_min"]
            and overlap["coverage"] >= PARAMETERS["layer_projection_coverage_min"]
            and gap <= gap_limit
            and distinct
        )
        return {
            "compatible": compatible,
            "normal_alignment": normal_dot,
            "projection_coverage": overlap["coverage"],
            "projection_iou": overlap["iou"],
            "normal_gap_mm": gap,
            "gap_limit_mm": gap_limit,
        }
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return {"compatible": False, "reason": "层叠关系字段无效"}


def _device_interval(indexed: list[dict], axis: list[float]) -> tuple[float, float]:
    values = [
        _dot(corner, axis)
        for record in indexed
        for corner in _aabb_corners(record["indexed_bbox_mm"])
    ]
    return min(values), max(values)


def _exterior_evidence(record: dict, indexed: list[dict]) -> dict:
    axis = record["plane_normal"]
    low, high = _device_interval(indexed, axis)
    projections = [_dot(corner, axis) for corner in _obb_corners(record)]
    candidate_low, candidate_high = min(projections), max(projections)
    distance = min(abs(candidate_low - low), abs(high - candidate_high))
    depth = high - low
    limit = max(2.5 * record["obb_dimensions_mm"][2], 0.08 * depth)
    center = _dot(record["obb_center_mm"], axis)
    indexed_centers = [
        _dot([
            (item["indexed_bbox_mm"][coordinate]
             + item["indexed_bbox_mm"][coordinate + 3]) / 2
            for coordinate in range(3)
        ], axis)
        for item in indexed
    ]
    lower_rank = sum(value < center for value in indexed_centers)
    upper_rank = sum(value > center for value in indexed_centers)
    extreme_rank = min(lower_rank, upper_rank)
    rank_fraction = extreme_rank / max(1, len(indexed_centers) - 1)
    center_is_exterior = (
        extreme_rank == 0 if len(indexed_centers) <= 3 else rank_fraction <= 0.10
    )
    aabb_is_exterior = distance <= limit
    return {
        "is_exterior": aabb_is_exterior or center_is_exterior,
        "aabb_is_exterior": aabb_is_exterior,
        "distance_to_exterior_mm": distance,
        "exterior_limit_mm": limit,
        "center_is_exterior": center_is_exterior,
        "center_extreme_rank": extreme_rank,
        "center_extreme_fraction": rank_fraction,
        "indexed_center_count": len(indexed_centers),
        "side": "POSITIVE" if center >= (low + high) / 2 else "NEGATIVE",
        "device_depth_along_normal_mm": depth,
    }


def _candidate_groups(results: dict[str, dict]) -> list[list[str]]:
    candidates = sorted(
        solid_id for solid_id, record in results.items()
        if record.get("status") == "PASS"
        and record.get("verdict") == "DISPLAY_CANDIDATE"
    )
    neighbors = {solid_id: set() for solid_id in candidates}
    for index, first_id in enumerate(candidates):
        for second_id in candidates[index + 1:]:
            relation = layer_compatibility(results[first_id], results[second_id])
            if relation.get("compatible"):
                neighbors[first_id].add(second_id)
                neighbors[second_id].add(first_id)
    groups = []
    remaining = set(candidates)
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        stack = [seed]
        group = [seed]
        while stack:
            current = stack.pop()
            for neighbor in sorted(neighbors[current] & remaining):
                remaining.remove(neighbor)
                stack.append(neighbor)
                group.append(neighbor)
        groups.append(sorted(group))
    return groups


def assemble_candidates(results: dict[str, dict], indexed: list[dict]) -> list[dict]:
    """Promote local plates using exterior placement and nearby matching layers."""
    assemblies = []
    for sequence, members in enumerate(_candidate_groups(results), 1):
        for solid_id in members:
            results[solid_id]["exterior_evidence"] = _exterior_evidence(
                results[solid_id], indexed
            )
        exterior = any(
            results[solid_id]["exterior_evidence"]["is_exterior"]
            for solid_id in members
        )
        local_structure = any(
            results[solid_id].get("local_structure_evidence") for solid_id in members
        )
        if not exterior:
            verdict = "OTHER"
            reason = "板状实体位于装配内部，缺少屏幕外侧位置证据"
        elif len(members) >= 3 or (len(members) >= 2 and local_structure):
            verdict = "STRONG_DISPLAY_EVIDENCE"
            reason = "外侧多层共形总成，同时具备孔槽或圆角曲面等局部结构证据"
        elif len(members) >= 2:
            verdict = "PROBABLE_DISPLAY_MODULE"
            reason = "外侧存在投影高度重合、方向平行、间距较小的多层薄片"
        else:
            verdict = "DISPLAY_CANDIDATE"
            reason = "外侧大面积薄片；缺少多层总成证据，不能确认屏幕"
        assembly_id = f"display-assembly-{sequence:03d}"
        representative = max(members, key=lambda item: results[item]["footprint_mm2"])
        for solid_id in members:
            record = results[solid_id]
            record["verdict"] = verdict
            record["reason"] = reason
            record["assembly_id"] = assembly_id
            record["assembly_members"] = members
            record["layer_count"] = len(members)
            record["review_status"] = "NEEDS_REVIEW"
        assemblies.append({
            "assembly_id": assembly_id,
            "members": members,
            "representative": representative,
            "layer_count": len(members),
            "verdict": verdict,
            "exterior": exterior,
            "local_structure_evidence": local_structure,
        })
    _mark_front_back_ambiguity(assemblies, results, indexed)
    return assemblies


def _mark_front_back_ambiguity(
    assemblies: list[dict], results: dict[str, dict], indexed: list[dict],
) -> None:
    eligible = [
        assembly for assembly in assemblies
        if assembly["exterior"] and assembly["verdict"] in DISPLAY_VERDICTS
    ]
    for index, first in enumerate(eligible):
        for second in eligible[index + 1:]:
            a = results[first["representative"]]
            b = results[second["representative"]]
            if abs(_dot(a["plane_normal"], b["plane_normal"])) < 0.95:
                continue
            footprint_ratio = min(a["footprint_mm2"], b["footprint_mm2"]) / max(
                a["footprint_mm2"], b["footprint_mm2"]
            )
            if (
                footprint_ratio < PARAMETERS["front_back_footprint_ratio_min"]
                or projected_overlap(a, b)["coverage"]
                < PARAMETERS["front_back_projection_coverage_min"]
            ):
                continue
            low, high = _device_interval(indexed, a["plane_normal"])
            midpoint = (low + high) / 2
            side_a = _dot(a["obb_center_mm"], a["plane_normal"]) - midpoint
            side_b = _dot(b["obb_center_mm"], a["plane_normal"]) - midpoint
            if side_a * side_b >= 0:
                continue
            members = sorted(set(first["members"] + second["members"]))
            for solid_id in members:
                record = results[solid_id]
                record["base_verdict"] = record["verdict"]
                record["verdict"] = "AMBIGUOUS_FRONT_BACK"
                record["reason"] = "装配正反两侧存在近似对称候选，纯几何无法区分屏幕与后盖"
                record["ambiguous_with"] = [item for item in members if item != solid_id]
            first["verdict"] = second["verdict"] = "AMBIGUOUS_FRONT_BACK"


def _current_manifest_hash(root: Path) -> str | None:
    try:
        with sqlite3.connect(
            f"file:{(root / 'geometry_features.sqlite3').as_posix()}?mode=ro", uri=True
        ) as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key='manifest_sha256'"
            ).fetchone()
        return str(row[0]) if row else None
    except sqlite3.Error:
        return None


def _load_exclusion_context(root: Path) -> tuple[dict[str, str], str]:
    """Load only current, validated recognizer outputs and fingerprint them."""
    exclusions: dict[str, str] = {}
    context: dict[str, object] = {"schema_version": 1}
    try:
        from cst_material import battery_recognition

        report = battery_recognition.complete_cached_report(root)
        if report is not None:
            matched = []
            for solid_id, record in report.get("results", {}).items():
                if record.get("status") == "PASS" and record.get("verdict") == "BATTERY_SHAPE":
                    exclusions[solid_id] = "BATTERY_SHAPE"
                    matched.append(solid_id)
            context["battery"] = {
                "algorithm_version": report.get("algorithm_version"),
                "source_signature": report.get("source_signature"),
                "matched": sorted(matched),
            }
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        pass
    manifest_hash = _current_manifest_hash(root)
    shield_context = []
    for name in ("shield_refinement.json", "shield_refinement_all.json",
                 "shield_refinement_pcb.json"):
        path = root / name
        if not path.is_file():
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            if (
                report.get("schema_version") != 2
                or not isinstance(report.get("algorithm_version"), str)
                or not manifest_hash
                or report.get("manifest_sha256") != manifest_hash
            ):
                continue
            matched = []
            for solid_id, record in report.get("results", {}).items():
                if record.get("status") == "PASS" and record.get("verdict") in {
                    "REVIEW_STRONG", "SHIELD_REVIEW_STRONG"
                }:
                    exclusions.setdefault(solid_id, "SHIELD_STRONG")
                    matched.append(solid_id)
            shield_context.append({
                "file": name,
                "algorithm_version": report["algorithm_version"],
                "manifest_sha256": report["manifest_sha256"],
                "matched": sorted(matched),
            })
        except (OSError, ValueError, TypeError):
            continue
    if shield_context:
        context["shields"] = shield_context
    try:
        from cst_material.pcb_recognition import find_main_pcb

        pcb = find_main_pcb(root)
        if pcb.get("status") == "GEOMETRY_CANDIDATE" and pcb.get("candidate"):
            exclusions.setdefault(pcb["candidate"]["solid_id"], "MAIN_PCB_CANDIDATE")
        context["pcb"] = {
            "status": pcb.get("status"),
            "candidate": (
                pcb.get("candidate", {}).get("solid_id")
                if isinstance(pcb.get("candidate"), dict) else None
            ),
        }
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        pass
    context["effective_exclusions"] = sorted(exclusions.items())
    payload = json.dumps(
        context, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return exclusions, hashlib.sha256(payload).hexdigest()


def _load_exclusions(root: Path) -> dict[str, str]:
    """Compatibility helper for callers interested only in effective exclusions."""
    return _load_exclusion_context(root)[0]


def _one(task: tuple[str, str, str]) -> tuple[str, dict]:
    solid_id, path, digest = task
    try:
        return solid_id, {
            "status": "PASS", "sha256": digest, **recognize_step(Path(path))
        }
    except Exception as exc:  # noqa: BLE001 - preserve per-Solid diagnostics
        return solid_id, {
            "status": "FAIL", "sha256": digest,
            "verdict": "OTHER", "error": f"{type(exc).__name__}: {exc}",
        }


def complete_cached_report(
    root: Path, *, exclusion_signature: str | None = None,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> dict | None:
    cache = root / CACHE_NAME
    if not cache.is_file():
        return None
    try:
        if exclusion_signature is None:
            _, exclusion_signature = _load_exclusion_context(root)
        report = json.loads(cache.read_text(encoding="utf-8"))
        signature, total = source_signature(root)
        if (
            report.get("algorithm_version") != VERSION
            or report.get("source_signature") != signature
            or report.get("exclusion_signature") != exclusion_signature
            or report.get("scan_options") != {"max_candidates": max_candidates}
            or not report.get("complete")
            or report.get("summary", {}).get("total") != total
            or len(report.get("results", {})) != total
        ):
            return None
        return report
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return None


def scan(
    folder: str | Path, *, progress=None,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> dict:
    if type(max_candidates) is not int or not 1 <= max_candidates <= 1000:
        raise ValueError("max_candidates 必须在 1..1000")
    root = Path(folder).resolve(strict=True)
    db = root / "geometry_features.sqlite3"
    if not db.is_file():
        raise FileNotFoundError("缺少 P5 geometry_features.sqlite3")
    exclusions, exclusion_signature = _load_exclusion_context(root)
    cached = complete_cached_report(
        root, exclusion_signature=exclusion_signature,
        max_candidates=max_candidates,
    )
    if cached is not None:
        return {
            "summary": cached["summary"], "output": str(root / CACHE_NAME),
            "from_cache": True,
        }
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "SELECT solid_id,step,features_json,step_sha256 "
            "FROM solids WHERE status='PASS' ORDER BY solid_id"
        ).fetchall()
    signature, total = source_signature(root)
    indexed, results = [], {}
    summary = Counter({"total": total, "failed": 0, "matched": 0, "excluded": 0})
    for row in rows:
        solid_id = row[0]
        try:
            record = _indexed_record(row)
            indexed.append(record)
            results[solid_id] = {
                "status": "PASS", "verdict": "OTHER",
                "reason": "未进入 STEP 精查候选", **record,
            }
        except Exception as exc:  # noqa: BLE001 - malformed index is a per-row failure
            results[solid_id] = {
                "status": "FAIL", "verdict": "OTHER",
                "error": f"{type(exc).__name__}: {exc}",
            }
            summary["failed"] += 1
    if not indexed:
        assemblies = []
        selected = []
    else:
        max_surface = max(record["indexed_surface_mm2"] for record in indexed)
        ranked = [
            record for record in indexed
            if record["plate_index"] >= PARAMETERS["coarse_plate_index_min"]
            and record["indexed_surface_mm2"]
            >= max_surface * PARAMETERS["coarse_relative_surface_min"]
            and PARAMETERS["coarse_aspect_ratio_range"][0]
            <= record["indexed_aspect_ratio"]
            <= PARAMETERS["coarse_aspect_ratio_range"][1]
            and record["indexed_flatness"] <= PARAMETERS["coarse_flatness_max"]
        ]
        ranked.sort(key=lambda record: (
            -record["indexed_surface_mm2"], -record["plate_index"], record["solid_id"]
        ))
        selected = ranked[:max_candidates]
        if progress:
            progress({
                "event": "phase", "message": f"屏幕粗筛完成：精查 {len(selected)} 个 STEP",
                "total": total, "candidates": len(selected),
            })
        local_matches = 0
        for done, indexed_record in enumerate(selected, 1):
            solid_id = indexed_record["solid_id"]
            if solid_id in exclusions:
                results[solid_id].update({
                    "verdict": "OTHER",
                    "reason": f"已被其他识别器排除：{exclusions[solid_id]}",
                    "excluded_by": exclusions[solid_id],
                })
                summary["excluded"] += 1
            else:
                try:
                    path = (root / indexed_record["step"]).resolve(strict=True)
                    if not path.is_relative_to(root) or not path.is_file():
                        raise FileNotFoundError("STEP 路径越界或不存在")
                    digest = indexed_record["step_sha256"] or sha256_file(path)
                    _, exact = _one((solid_id, str(path), digest))
                    results[solid_id] = {**indexed_record, **exact}
                    if exact.get("status") == "FAIL":
                        summary["failed"] += 1
                    elif exact.get("verdict") == "DISPLAY_CANDIDATE":
                        local_matches += 1
                except Exception as exc:  # noqa: BLE001
                    results[solid_id] = {
                        **indexed_record, "status": "FAIL", "verdict": "OTHER",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    summary["failed"] += 1
            if progress:
                progress({
                    "event": "progress", "done": done, "total": len(selected),
                    "candidates": len(selected), "matched": local_matches,
                    "failed": summary["failed"],
                })
        assemblies = assemble_candidates(results, indexed)
    summary["coarse_candidates"] = len(selected)
    summary["assemblies"] = len(assemblies)
    summary["layered_assemblies"] = sum(
        item.get("layer_count", 0) >= 2 for item in assemblies
    )
    for record in results.values():
        verdict = record.get("verdict", "OTHER")
        summary[verdict] += 1
        if verdict in DISPLAY_VERDICTS:
            summary["matched"] += 1
    for solid_id, record in sorted(results.items()):
        if progress and record.get("verdict") in DISPLAY_VERDICTS:
            progress({
                "event": "match", "solid_id": solid_id,
                "verdict": record["verdict"], "assembly_id": record.get("assembly_id"),
            })
    cache = root / CACHE_NAME
    temporary = cache.with_suffix(".json.tmp")
    report = {
        "schema_version": 1,
        "algorithm_version": VERSION,
        "complete": True,
        "source_signature": signature,
        "exclusion_signature": exclusion_signature,
        "scan_options": {"max_candidates": max_candidates},
        "parameters": PARAMETERS,
        "summary": dict(summary),
        "assemblies": assemblies,
        "results": results,
        "warning": (
            "纯几何只能给出屏幕总成候选；电池、后盖、石墨片可能相似，"
            "AMBIGUOUS_FRONT_BACK 必须人工确认"
        ),
    }
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(cache)
    return {"summary": dict(summary), "output": str(cache), "from_cache": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="手机屏幕总成纯几何候选识别")
    parser.add_argument("folder", help="包含 P5 几何数据库和逐 Solid STEP 的导出目录")
    parser.add_argument(
        "--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES,
    )
    args = parser.parse_args(argv)

    def emit(event: dict) -> None:
        print(json.dumps(event, ensure_ascii=False), flush=True)

    result = scan(args.folder, progress=emit, max_candidates=args.max_candidates)
    emit({"event": "complete", **result})
    # Per-Solid failures are preserved in a complete report and must remain
    # visible to the GUI instead of making QProcess discard all partial results.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
