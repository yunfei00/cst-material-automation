"""CAD-focused rendering for thin folded Solid geometry.

The STL preview is only a display mesh. Reconnect triangle vertices before
extracting *feature edges*; never render all triangulation edges as CAD edges.
Pure mesh transforms are separately testable without Qt or CST.
"""
from __future__ import annotations

from typing import Literal

RenderMode = Literal["工程模式", "平滑模式", "线框诊断"]
RENDER_MODES: tuple[RenderMode, ...] = ("工程模式", "平滑模式", "线框诊断")


def query_point_mesh(point):
    """Create PyVista-compatible 1x3 XYZ point geometry from Python coordinates.

    PyVista's plotter.add_points([tuple]) can raise NotImplementedError for
    a plain Python list; always pass a typed NumPy array / PolyData.
    """
    import numpy as np
    import pyvista as pv

    arr = np.asarray(point, dtype=np.float64)
    if arr.shape != (3,) or not np.isfinite(arr).all():
        raise ValueError("查询点必须恰好包含三个有限坐标")
    return pv.PolyData(arr.reshape(1, 3))


def prepare_display_mesh(mesh):
    """Merge exact duplicate vertices; preserve faces and dimensional scale."""
    import pyvista as pv

    if not isinstance(mesh, pv.PolyData):
        mesh = mesh.extract_surface()
    if mesh.n_cells == 0 or mesh.n_points == 0:
        raise ValueError("渲染网格为空")
    result = mesh.triangulate().clean(tolerance=0.0)
    # Reorient connected triangles consistently; avoid false dark patches.
    result = result.compute_normals(
        cell_normals=True, point_normals=True, consistent_normals=True,
        auto_orient_normals=False, split_vertices=False, inplace=False,
    )
    if result.n_cells == 0 or result.n_points == 0:
        raise ValueError("清理后网格为空")
    return result


def extract_cad_feature_edges(mesh, angle: float = 38.0):
    """Fold boundaries and visible sharp creases, not each STL triangle."""
    if not 0 < angle < 180:
        raise ValueError("特征边角度应在 0～180 度之间")
    return mesh.extract_feature_edges(
        feature_angle=angle,
        boundary_edges=True,
        feature_edges=True,
        manifold_edges=False,
        non_manifold_edges=False,
        clear_data=True,
    )


def render_selected_solid(plotter, mesh, mode: RenderMode = "工程模式"):
    """Draw one Solid, return (surface_actor, feature_count, info).

    Never use transparency for the default CAD view: translucent thin sheets
    are especially prone to apparent overlaps and depth-order ambiguity.
    """
    if mode not in RENDER_MODES:
        raise ValueError(f"不支持的三维显示模式：{mode}")
    pvmesh = prepare_display_mesh(mesh)
    plotter.clear()
    # Avoid coincident feature lines fighting with the surface depth buffer.
    # Polygon offset is applied to the surface actor only where supported.
    if mode == "线框诊断":
        actor = plotter.add_mesh(
            pvmesh,
            style="wireframe",
            color="#b8d2e6",
            line_width=1.0,
            opacity=1.0,
            lighting=False,
            pickable=True,
            name="selected_solid",
        )
        return actor, 0, f"线框诊断：显示 {pvmesh.n_cells} 个三角面，检查叠面/交叉结构"

    # Facet normals and hard-crease splitting make curved spring arms appear
    # rounded, while retaining the crisp edges of bent sheet metal.
    actor = plotter.add_mesh(
        pvmesh,
        color="#afc6d9",
        opacity=1.0,
        lighting=True,
        smooth_shading=True,
        split_sharp_edges=True,
        feature_angle=38.0,
        ambient=0.30,
        diffuse=0.70,
        specular=0.18,
        specular_power=20,
        silhouette={"color": "#e2eef7", "line_width": 1.5},
        pickable=True,
        name="selected_solid",
    )
    try:
        actor.GetProperty().SetInterpolationToPhong()
    except AttributeError:
        pass
    if mode == "平滑模式":
        return actor, 0, "平滑模式：曲面明暗 + 外轮廓（不显示折弯边）"

    # Show *only* topology-like sharp edges, not every triangulation edge.
    features = extract_cad_feature_edges(pvmesh)
    count = features.n_cells
    if 0 < count <= 35000:
        plotter.add_mesh(
            features,
            color="#34465a",
            line_width=1.1,
            lighting=False,
            pickable=False,
            name="cad_feature_edges",
        )
        return actor, count, f"工程模式：已强调 {count} 条轮廓/折弯边"
    if count > 35000:
        return actor, count, "折弯边数量过大，已自动关闭边线叠加以保持响应速度"
    return actor, 0, "未找到显著折弯特征边；仍显示光照与外轮廓"
