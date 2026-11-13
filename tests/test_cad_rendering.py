"""Regression for PyVista list-wrapper error + CAD-style thin-part rendering."""
from __future__ import annotations

import numpy as np
import pytest

pv = pytest.importorskip("pyvista")

from cst_material.cad_rendering import (
    RENDER_MODES,
    extract_cad_feature_edges,
    prepare_display_mesh,
    query_point_mesh,
    render_selected_solid,
)


def test_query_xyz_makes_pyvista_polydata_not_list():
    point = query_point_mesh((1.25, 2.5, -3.75))
    assert isinstance(point, pv.PolyData)
    assert point.n_points == 1
    assert point.points.shape == (1, 3)
    np.testing.assert_array_equal(point.points[0], [1.25, 2.5, -3.75])


@pytest.mark.parametrize("bad", [[1, 2], [1, 2, 3, 4], [0, float("nan"), 1]])
def test_query_xyz_rejects_invalid_values(bad):
    with pytest.raises(ValueError, match="三个有限"):
        query_point_mesh(bad)


def test_mesh_cleanup_preserves_bbox_and_detects_real_cad_crease():
    cube = pv.Cube(x_length=10, y_length=20, z_length=30).triangulate()
    mesh = prepare_display_mesh(cube)
    assert mesh.n_points > 0
    np.testing.assert_allclose(mesh.bounds, cube.bounds)
    edges = extract_cad_feature_edges(mesh)
    assert edges.n_cells > 0
    # A CAD cube has 12 outer edges, not all 12 triangle diagonals.
    assert edges.n_cells < mesh.n_cells * 3


class DummyActor:
    pass


class FakePlotter:
    def __init__(self):
        self.calls = []

    def clear(self):
        self.calls.append(("clear", {}))

    def add_mesh(self, mesh, **kwargs):
        assert isinstance(mesh, pv.PolyData)
        assert mesh.n_points
        self.calls.append(("mesh", kwargs))
        return DummyActor()


@pytest.mark.parametrize("mode", RENDER_MODES)
def test_cad_view_styles_do_not_need_qt(mode):
    plotter = FakePlotter()
    actor, count, desc = render_selected_solid(plotter, pv.Cube().triangulate(), mode)
    assert isinstance(actor, DummyActor)
    assert desc and count >= 0
    opts = plotter.calls[1][1]
    assert opts["opacity"] == 1.0
    assert opts["pickable"] is True
    if mode == "线框诊断":
        assert opts["style"] == "wireframe"
    else:
        assert opts["smooth_shading"] is True
        assert opts["split_sharp_edges"] is True
    if mode == "工程模式":
        assert any(
            kind == "mesh" and kw.get("name") == "cad_feature_edges"
            for kind, kw in plotter.calls
        )


def test_cad_render_rejects_wrong_mode_and_angle():
    with pytest.raises(ValueError, match="模式"):
        render_selected_solid(FakePlotter(), pv.Cube(), "invalid")
    with pytest.raises(ValueError, match="角度"):
        extract_cad_feature_edges(pv.Cube(), -1)
