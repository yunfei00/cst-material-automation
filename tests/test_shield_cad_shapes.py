"""Optional exact-CAD regression: run with `uv run --extra cad pytest -q tests/test_shield_cad_shapes.py`.

CI without CAD dependencies skips this module; it is not a substitute for
customer STEP acceptance.
"""
from pathlib import Path

import pytest

pytest.importorskip("OCP")

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.gp import gp_Pnt

from cst_material.shield_refinement import refine_step


def _write(shape, path: Path):
    writer = STEPControl_Writer()
    assert writer.Transfer(shape, STEPControl_AsIs) == IFSelect_ReturnStatus.IFSelect_RetDone
    assert writer.Write(str(path)) == IFSelect_ReturnStatus.IFSelect_RetDone


def test_solid_connector_block_not_strong(tmp_path):
    path = tmp_path / "connector.step"
    _write(BRepPrimAPI_MakeBox(20, 12, 5).Shape(), path)
    result = refine_step(path)
    assert result["verdict"] != "REVIEW_STRONG"


def test_open_bottom_shield_has_cavity_evidence(tmp_path):
    path = tmp_path / "shield.step"
    outer = BRepPrimAPI_MakeBox(20, 16, 5).Shape()
    # Pocket exits at z=0; top thickness 0.3 mm, sidewalls 0.3 mm.
    inner = BRepPrimAPI_MakeBox(gp_Pnt(0.3, 0.3, -0.1), 19.4, 15.4, 4.8).Shape()
    cut = BRepAlgoAPI_Cut(outer, inner)
    cut.Build()
    assert cut.IsDone()
    _write(cut.Shape(), path)
    result = refine_step(path)
    assert result["verdict"] in ("REVIEW", "REVIEW_STRONG")
    assert result["thickness"]["supported"]


def test_flat_sheet_not_strong(tmp_path):
    path = tmp_path / "sheet.step"
    _write(BRepPrimAPI_MakeBox(20, 16, 0.3).Shape(), path)
    result = refine_step(path)
    assert result["verdict"] != "REVIEW_STRONG"
