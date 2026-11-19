"""Fast analytic face inventory and sheet thickness hints for STEP solids.

Plane and cylinder pairing is a screening hint, not full material thickness proof.
"""
from __future__ import annotations

import math


def analyze_sheet_faces(solids: list, *, tolerance_mm: float = 0.03) -> dict:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    counts = {"plane": 0, "cylinder": 0, "other": 0}
    planes, cylinders = [], []
    for solid in solids:
        ex = TopExp_Explorer(solid, TopAbs_FACE)
        while ex.More():
            face = (getattr(TopoDS, "Face_s", None) or TopoDS.Face)(ex.Current())
            adaptor = BRepAdaptor_Surface(face)
            kind = adaptor.GetType()
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face, props)
            area = float(props.Mass())
            center = props.CentreOfMass()
            p = (center.X(), center.Y(), center.Z())
            if kind == GeomAbs_Plane:
                counts["plane"] += 1
                n = adaptor.Plane().Axis().Direction()
                sign = -1 if face.Orientation() == TopAbs_REVERSED else 1
                planes.append((p, (sign*n.X(), sign*n.Y(), sign*n.Z()), area))
            elif kind == GeomAbs_Cylinder:
                counts["cylinder"] += 1
                cyl = adaptor.Cylinder()
                axis = cyl.Axis()
                direction = axis.Direction()
                origin = axis.Location()
                cylinders.append((
                    (origin.X(), origin.Y(), origin.Z()),
                    (direction.X(), direction.Y(), direction.Z()),
                    float(cyl.Radius()), area,
                ))
            else:
                counts["other"] += 1
            ex.Next()
    def dot(a, b):
        return sum(x*y for x, y in zip(a, b))
    def sub(a, b):
        return tuple(x-y for x, y in zip(a, b))
    pairs = []
    for i, (pa, na, aa) in enumerate(planes):
        for pb, nb, ab in planes[i+1:]:
            if dot(na, nb) > -0.985:
                continue
            delta = sub(pb, pa)
            thickness = abs(dot(delta, na))
            lateral = math.sqrt(max(0., dot(delta, delta)-thickness*thickness))
            if thickness > 1e-5 and lateral <= 0.5*math.sqrt(min(aa, ab)):
                pairs.append({"kind": "plane", "thickness_mm": thickness,
                              "weight": min(aa, ab)})
    for i, (oa, da, ra, aa) in enumerate(cylinders):
        for ob, db, rb, ab in cylinders[i+1:]:
            if abs(dot(da, db)) < 0.999:
                continue
            delta = sub(ob, oa)
            perpendicular = sub(delta, tuple(dot(delta, da)*v for v in da))
            if math.sqrt(dot(perpendicular, perpendicular)) > tolerance_mm:
                continue
            thickness = abs(ra-rb)
            if thickness > 1e-5:
                pairs.append({"kind": "cylinder", "thickness_mm": thickness,
                              "weight": min(aa, ab)})
    if not pairs:
        consistency, dominant = 0., None
    else:
        dominant = max(
            pairs, key=lambda p: sum(q["weight"] for q in pairs
            if abs(q["thickness_mm"]-p["thickness_mm"]) <=
            max(tolerance_mm, 0.15*p["thickness_mm"]))
        )["thickness_mm"]
        consistency = sum(p["weight"] for p in pairs
            if abs(p["thickness_mm"]-dominant) <=
            max(tolerance_mm, 0.15*dominant)) / sum(p["weight"] for p in pairs)
    return {
        "face_count": sum(counts.values()), "surface_counts": counts,
        "plane_pair_count": sum(p["kind"] == "plane" for p in pairs),
        "cylinder_pair_count": sum(p["kind"] == "cylinder" for p in pairs),
        "dominant_thickness_mm": round(dominant, 5) if dominant is not None else None,
        "pair_consistency": round(consistency, 4),
        "warning": "解析曲面配对仅为等厚线索；未验证裁剪面重叠、实体材料路径和自由曲面厚度",
    }
