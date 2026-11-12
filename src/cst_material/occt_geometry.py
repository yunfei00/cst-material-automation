"""Precise offline STEP geometry, characteristics and render-mesh generation.

OCP is optional at import time; methods import it only when called. Geometry
coordinates are standardized to millimetres by STEPControl_Reader. Do not use
the STL preview for precise point-in-solid classification.
"""
from __future__ import annotations

import hashlib
import math
import time
from collections import Counter
from pathlib import Path


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_step(path: str | Path) -> list:
    """Read all closed valid solids from one STEP file; reject loose surfaces."""
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    src = Path(path).resolve(strict=True)
    if src.suffix.lower() not in {".step", ".stp"}:
        raise ValueError("只支持 STEP/STP 精确 CAD 文件")
    reader = STEPControl_Reader()
    if reader.ReadFile(str(src)) != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise ValueError(f"无法导入 STEP：{src}")
    if reader.TransferRoots() < 1:
        raise ValueError(f"STEP 不含可转换的形状：{src}")
    explorer = TopExp_Explorer(reader.OneShape(), TopAbs_SOLID)
    solids = []
    while explorer.More():
        downcast_solid = getattr(TopoDS, "Solid_s", None) or TopoDS.Solid
        solid = downcast_solid(explorer.Current())
        # The TopoDS_Shape.Closed() flag may be False for a valid STEP-imported
        # B-rep solid. It is NOT a reliable closure test after STEP transfer.
        if solid.IsNull() or not BRepCheck_Analyzer(solid).IsValid():
            raise ValueError(f"STEP 存在无效实体：{src}")
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(solid, props)
        if not math.isfinite(props.Mass()) or props.Mass() <= 0:
            raise ValueError(f"STEP 实体体积非正或无效：{src}")
        solids.append(solid)
        explorer.Next()
    if not solids:
        raise ValueError(f"STEP 未包含封闭 Solid（可能只有曲面）：{src}")
    return solids


def classify_point(solids: list, point: tuple[float, float, float], tolerance: float = 1e-6):
    """Return INSIDE, ON_BOUNDARY, or OUTSIDE for a possibly multi-body shape."""
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN, TopAbs_ON, TopAbs_OUT

    if len(point) != 3 or not all(math.isfinite(float(v)) for v in point):
        raise ValueError("坐标必须为三个有限数值")
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("tolerance 必须是有限正数")
    if not solids:
        raise ValueError("没有可用的精确 CAD Solid")
    point3d = gp_Pnt(*(float(p) for p in point))
    on_boundary = False
    for solid in solids:
        classifier = BRepClass3d_SolidClassifier(solid)
        classifier.Perform(point3d, tolerance)
        state = classifier.State()
        if state == TopAbs_IN:
            return "INSIDE"
        if state == TopAbs_ON:
            on_boundary = True
        elif state != TopAbs_OUT:
            raise RuntimeError(f"无法识别的几何查询状态：{state}")
    return "ON_BOUNDARY" if on_boundary else "OUTSIDE"


def geometry_metrics(solids: list) -> dict:
    """Exact B-rep metrics; bbox limits can include OCCT tolerance padding."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    if not solids:
        raise ValueError("没有可用的实体")
    box = Bnd_Box()
    volume = 0.0
    area = 0.0
    edges = 0
    vertices = 0
    faces = 0
    kinds = Counter()
    start = time.perf_counter()
    for solid in solids:
        BRepBndLib.Add_s(solid, box)
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(solid, props)
        volume += abs(props.Mass())
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(solid, props)
        area += props.Mass()
        for typ in (TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX):
            ex = TopExp_Explorer(solid, typ)
            while ex.More():
                if typ == TopAbs_FACE:
                    faces += 1
                    name = str(BRepAdaptor_Surface(
                        (getattr(TopoDS, "Face_s", None) or TopoDS.Face)(ex.Current())
                    ).GetType())
                    kinds[name.rsplit(".", 1)[-1]] += 1
                elif typ == TopAbs_EDGE:
                    edges += 1
                else:
                    vertices += 1
                ex.Next()
    xyz = [float(v) for v in box.Get()]
    if any(not math.isfinite(v) for v in xyz) or any(
        xyz[i] > xyz[i + 3] for i in range(3)
    ):
        raise ValueError("几何包围盒无效")
    return {
        "solid_body_count": len(solids),
        "volume_mm3": volume,
        "surface_mm2": area,
        "bbox_mm": xyz,
        "dimensions_mm": [xyz[i + 3] - xyz[i] for i in range(3)],
        "face_count": faces,
        "edge_count": edges,
        "vertex_count": vertices,
        "surface_types": dict(sorted(kinds.items())),
        "metric_seconds": round(time.perf_counter() - start, 6),
    }


def export_preview_stl(solids: list, output: str | Path, *, deflection: float = 0.1) -> Path:
    """Tessellate B-rep and export render-only STL; do not reuse for geometry."""
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.StlAPI import StlAPI_Writer
    from OCP.TopoDS import TopoDS_Builder, TopoDS_Compound

    if not solids:
        raise ValueError("没有 Solid 可渲染")
    if not math.isfinite(deflection) or deflection <= 0:
        raise ValueError("三角化精度必须为正数")
    dest = Path(output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise FileExistsError(dest)
    builder = TopoDS_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for solid in solids:
        builder.Add(compound, solid)
    mesh = BRepMesh_IncrementalMesh(compound, deflection, False, 0.2, True)
    if not mesh.IsDone():
        raise RuntimeError("OCCT 三角化失败")
    if not StlAPI_Writer().Write(compound, str(dest)) or dest.stat().st_size < 84:
        raise RuntimeError("导出显示用 STL 失败")
    return dest


def analyze_step(path: str | Path, mesh_path: str | Path | None = None) -> dict:
    start = time.perf_counter()
    solids = read_step(path)
    metrics = geometry_metrics(solids)
    metrics["step_sha256"] = sha256_file(path)
    metrics["step_parse_seconds"] = round(time.perf_counter() - start, 6)
    if mesh_path is not None:
        export_preview_stl(solids, mesh_path)
    return metrics
