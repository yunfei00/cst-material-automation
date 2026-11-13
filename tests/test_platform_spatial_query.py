"""Offline repeated point lookup reuses one geometry index and OCC classifiers."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("OCP")
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

from cst_material.solid_index import build_catalog
from cst_material.spatial_query import OfflineSolidIndex


def test_cached_classifier_many_points_and_point_boundary(tmp_path):
    file = tmp_path / "block.step"
    writer = STEPControl_Writer()
    assert writer.Transfer(
        BRepPrimAPI_MakeBox(2.0, 3.0, 4.0).Solid(), STEPControl_AsIs
    ) == IFSelect_ReturnStatus.IFSelect_RetDone
    assert writer.Write(str(file)) == IFSelect_ReturnStatus.IFSelect_RetDone
    manifest = {
        "schema_version": 1, "unit": "mm", "source_sha256": "synthetic",
        "solids": [{"solid_id": "body:solid", "step": file.name, "material": "PEC"}],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    build_catalog(tmp_path)
    index = OfflineSolidIndex(tmp_path)
    out = index.classify_many([(1, 1, 1), (5, 5, 5), (0, 1, 1)])
    assert out[0] == [{"solid_id": "body:solid", "state": "INSIDE"}]
    assert out[1] == []
    assert out[2] == [{"solid_id": "body:solid", "state": "ON_BOUNDARY"}]
    assert index._classifiers.cache_info().hits > 0
    assert len(index.classify_many([(20, 20, 20)] * 1000)) == 1000
