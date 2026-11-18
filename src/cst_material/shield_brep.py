"""P8-B exact STEP B-rep evidence for a single-piece EMI shielding can.

Review-only: a geometric shell is not proof of an EMI shielding function.
"""
from __future__ import annotations

import math
from pathlib import Path

from cst_material.occt_geometry import read_step


def _face_evidence(solids: list) -> list[dict]:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    result = []
    for solid in solids:
        explorer = TopExp_Explorer(solid, TopAbs_FACE)
        while explorer.More():
            face = (getattr(TopoDS, "Face_s", None) or TopoDS.Face)(explorer.Current())
            adaptor = BRepAdaptor_Surface(face)
            if adaptor.GetType() == GeomAbs_Plane:
                props = GProp_GProps()
                BRepGProp.SurfaceProperties_s(face, props)
                normal = adaptor.Plane().Axis().Direction()
                center = props.CentreOfMass()
                result.append({
                    "area": float(props.Mass()),
                    "normal": (normal.X(), normal.Y(), normal.Z()),
                    "center": (center.X(), center.Y(), center.Z()),
                })
            explorer.Next()
    return result


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def analyze_shield_step(path: str | Path) -> dict:
    """Check planar cap, transverse walls and hollow material distribution.

    Does not claim a verified opening or uniform wall thickness. A closed
    B-rep solid can still have an open-bottom cavity in its physical geometry.
    """
    from cst_material.occt_geometry import geometry_metrics

    solids = read_step(path)
    metrics = geometry_metrics(solids)
    faces = _face_evidence(solids)
    dims = sorted(metrics["dimensions_mm"])
    bbox_volume = math.prod(dims)
    if bbox_volume <= 0 or not faces:
        return {
            "candidate": False, "score": 0,
            "evidence": ["包围盒退化或没有平面面"],
            "review_status": "NEEDS_REVIEW",
        }
    fill = metrics["volume_mm3"] / bbox_volume
    total_planar_area = sum(face["area"] for face in faces)
    largest = max(faces, key=lambda face: face["area"])
    axis = largest["normal"]
    parallel = [
        face for face in faces if abs(_dot(face["normal"], axis)) >= 0.94
    ]
    transverse = [
        face for face in faces if abs(_dot(face["normal"], axis)) <= 0.35
    ]
    parallel_area = sum(face["area"] for face in parallel)
    transverse_area = sum(face["area"] for face in transverse)
    cap_share = largest["area"] / total_planar_area if total_planar_area else 0
    score = 0
    evidence = []
    if 0 < fill < 0.5:
        score += 25
        evidence.append(f"实体填充率 {fill:.3f}，存在薄壁或空腔可能")
    if len(transverse) >= 4:
        score += 30
        evidence.append(f"检测到 {len(transverse)} 个与主平面近似垂直的平面面")
    if len(parallel) >= 2:
        score += 15
        evidence.append(f"检测到 {len(parallel)} 个与主平面近似平行的平面面")
    if cap_share >= 0.15:
        score += 15
        evidence.append(f"最大平面面积占平面总面积 {cap_share:.2%}")
    if transverse_area > 0 and parallel_area > 0:
        score += 15
        evidence.append("存在相互垂直的平面面积分布")
    evidence.append("尚未验证底部开口、连续围合及局部壁厚，需人工确认")
    return {
        "candidate": score >= 70,
        "score": score,
        "evidence": evidence,
        "review_status": "NEEDS_REVIEW",
        "metrics": {
            "bbox_fill_ratio": round(fill, 6),
            "planar_face_count": len(faces),
            "parallel_face_count": len(parallel),
            "transverse_face_count": len(transverse),
            "largest_planar_face_area_mm2": round(largest["area"], 6),
            "cap_area_share": round(cap_share, 6),
            "solid_body_count": len(solids),
        },
    }
