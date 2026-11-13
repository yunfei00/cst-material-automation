"""Synthetic real OCP geometry integration tests (no CST, no company files)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

OCP = pytest.importorskip("OCP")

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

from cst_material.occt_geometry import (
    analyze_step,
    classify_point,
    read_step,
)
from cst_material.solid_index import (
    build_catalog,
    ensure_preview_mesh,
    find_solids_containing_point,
    load_catalog,
)


def sample_step(path: Path, *, holed: bool = False) -> Path:
    shape = BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Solid()
    if holed:
        cylinder = BRepPrimAPI_MakeCylinder(
            gp_Ax2(gp_Pnt(5, 10, 0), gp_Dir(0, 0, 1)), 2.0, 30.0
        ).Solid()
        shape = BRepAlgoAPI_Cut(shape, cylinder).Shape()
    writer = STEPControl_Writer()
    assert writer.Transfer(shape, STEPControl_AsIs) == IFSelect_ReturnStatus.IFSelect_RetDone
    assert writer.Write(str(path)) == IFSelect_ReturnStatus.IFSelect_RetDone
    return path


def test_m1_step_geometry_and_precise_point_states(tmp_path):
    file = sample_step(tmp_path / "box.step")
    solids = read_step(file)
    features = analyze_step(file, tmp_path / "render.stl")
    assert features["solid_body_count"] == 1
    assert features["volume_mm3"] == pytest.approx(6000)
    assert features["surface_mm2"] == pytest.approx(2200)
    assert features["face_count"] == 6
    assert features["edge_count"] >= 12
    assert (tmp_path / "render.stl").stat().st_size > 84
    assert classify_point(solids, (5, 10, 15)) == "INSIDE"
    assert classify_point(solids, (15, 10, 15)) == "OUTSIDE"
    assert classify_point(solids, (0, 10, 15)) == "ON_BOUNDARY"


def test_m1_real_brep_void_is_outside_not_bbox_inside(tmp_path):
    solid = read_step(sample_step(tmp_path / "hole.step", holed=True))
    assert classify_point(solid, (5, 10, 15)) == "OUTSIDE"
    assert classify_point(solid, (1, 1, 15)) == "INSIDE"


def test_m3_manifest_keeps_exact_cst_solid_names(tmp_path):
    first = sample_step(tmp_path / "spring.step")
    second = sample_step(tmp_path / "other.step", holed=True)
    manifest = {
        "schema_version": 1,
        "unit": "mm",
        "source": "original-synthetic.cst",
        "source_sha256": "synthetic-sha",
        "solids": [
            {"solid_id": "assembly:spring", "material": "Metal", "step": first.name},
            {"solid_id": "assembly:shell", "material": "Plastic", "step": second.name},
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = build_catalog(tmp_path)
    assert [x["solid_id"] for x in result["records"]] == [
        "assembly:spring", "assembly:shell"
    ]
    loaded = load_catalog(tmp_path)
    assert loaded["solid_count"] == 2
    mesh = ensure_preview_mesh(tmp_path, loaded["records"][0])
    assert mesh.exists()
    assert find_solids_containing_point(tmp_path, (5, 10, 15)) == [
        {"solid_id": "assembly:spring", "state": "INSIDE"}
    ]
    with pytest.raises(FileExistsError):
        build_catalog(tmp_path)


def test_m3_manifest_rejects_path_traversal_and_colliding_identity(tmp_path):
    sample_step(tmp_path / "one.step")
    (tmp_path / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "unit": "mm", "solids": [
            {"solid_id": "x:y", "step": "../outside.step"},
        ],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="越过"):
        build_catalog(tmp_path)


def test_m1_rejects_open_or_non_step_files(tmp_path):
    p = tmp_path / "fake.stl"
    p.write_bytes(b"not a step file")
    with pytest.raises(ValueError, match="STEP/STP"):
        read_step(p)
    with pytest.raises(ValueError, match="三个"):
        classify_point([object()], (float("nan"), 1, 2))
