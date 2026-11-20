"""Real OCP geometric checks with a synthetic box; no private CST models."""
from __future__ import annotations

import pytest

pytest.importorskip("OCP", reason="cadquery-ocp is an optional POC dependency")

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

from check_point import check_point, classify_point, load_solid


def _make_sample_step(tmp_path):
    path = tmp_path / "rectangular_box.step"
    writer = STEPControl_Writer()
    assert writer.Transfer(
        BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Solid(), STEPControl_AsIs
    ) == IFSelect_ReturnStatus.IFSelect_RetDone
    assert writer.Write(str(path)) == IFSelect_ReturnStatus.IFSelect_RetDone
    return path


def test_classifies_inside_outside_boundary_from_reloaded_step(tmp_path):
    step = _make_sample_step(tmp_path)
    assert check_point(step, 5, 10, 15) == "INSIDE"
    assert check_point(step, 15, 10, 15) == "OUTSIDE"
    assert check_point(step, 0, 10, 15) == "ON_BOUNDARY"
    assert check_point(step, 0, 0, 15) == "ON_BOUNDARY"


def test_load_once_and_classify_multiple_points(tmp_path):
    solid = load_solid(_make_sample_step(tmp_path))
    assert classify_point(solid, 5, 10, 15) == "INSIDE"
    assert classify_point(solid, 5, 10, 31) == "OUTSIDE"
    assert classify_point(solid, 10, 10, 15) == "ON_BOUNDARY"


def test_reject_non_step_and_invalid_coordinates(tmp_path):
    file = tmp_path / "not_step.stl"
    file.write_text("invalid")
    with pytest.raises(ValueError, match="STEP/STP"):
        load_solid(file)
    solid = load_solid(_make_sample_step(tmp_path))
    with pytest.raises(ValueError, match="有限"):
        classify_point(solid, float("nan"), 0, 0)
    with pytest.raises(ValueError, match="tolerance"):
        classify_point(solid, 1, 2, 3, tolerance=0)
