"""Conservative display-module recognition without private phone data."""
from __future__ import annotations

import json
import math
import sqlite3

import pytest

from cst_material import display_recognition as display


def _exact_candidate(
    solid_id: str, z: float, *, structure: bool = False,
    axes: list[list[float]] | None = None,
) -> dict:
    axes = axes or [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    return {
        "status": "PASS",
        "verdict": "DISPLAY_CANDIDATE",
        "solid_id": solid_id,
        "obb_dimensions_mm": [100.0, 50.0, 0.5],
        "obb_axes": axes,
        "obb_center_mm": [50.0, 25.0, z],
        "plane_normal": axes[2],
        "footprint_mm2": 5000.0,
        "local_structure_evidence": structure,
    }


def _indexed(solid_id: str, bbox: list[float]) -> dict:
    return {"solid_id": solid_id, "indexed_bbox_mm": bbox}


def test_exact_geometry_requires_plate_planes_and_sheet_pair():
    profile = {"obb_dimensions_mm": [150.0, 70.0, 1.0]}
    planar = {"major_plane_coverage": 0.96, "opposed_major_planes": True,
              "inner_wire_count": 1, "cylinder_face_count": 0}
    thickness = {"plane_pair_count": 2, "pair_consistency": 0.9}
    result = display.evaluate_exact_geometry(
        volume_mm3=10000.0, solid_body_count=1,
        profile=profile, planar=planar, thickness=thickness,
    )
    assert result["verdict"] == "DISPLAY_CANDIDATE"
    assert result["local_structure_evidence"]

    block = display.evaluate_exact_geometry(
        volume_mm3=125000.0, solid_body_count=1,
        profile={"obb_dimensions_mm": [50.0, 50.0, 50.0]},
        planar=planar, thickness=thickness,
    )
    assert block["verdict"] == "OTHER"


def test_layer_compatibility_is_rotation_independent():
    angle = math.radians(37)
    axes = [
        [math.cos(angle), math.sin(angle), 0.0],
        [-math.sin(angle), math.cos(angle), 0.0],
        [0.0, 0.0, 1.0],
    ]
    first = _exact_candidate("front:glass", 9.0, axes=axes)
    second = _exact_candidate("front:oled", 9.8, axes=axes)
    relation = display.layer_compatibility(first, second)
    assert relation["compatible"]
    assert relation["projection_coverage"] > 0.99

    second["obb_center_mm"][0] += 90
    assert not display.layer_compatibility(first, second)["compatible"]


def test_external_layers_promote_but_internal_plate_is_rejected():
    results = {
        "front:glass": _exact_candidate("front:glass", 9.0),
        "front:oled": _exact_candidate("front:oled", 9.8),
        "inside:graphite": _exact_candidate("inside:graphite", 5.0),
    }
    indexed = [
        _indexed("back", [0, 0, 0, 100, 50, 0.5]),
        _indexed("inside", [0, 0, 4, 100, 50, 6]),
        _indexed("front", [0, 0, 9, 100, 50, 10]),
    ]
    assemblies = display.assemble_candidates(results, indexed)
    assert len(assemblies) == 2
    assert results["front:glass"]["verdict"] == "PROBABLE_DISPLAY_MODULE"
    assert results["front:oled"]["layer_count"] == 2
    assert results["inside:graphite"]["verdict"] == "OTHER"


def test_front_and_back_symmetric_stacks_remain_ambiguous():
    results = {
        "front:glass": _exact_candidate("front:glass", 9.2, structure=True),
        "front:oled": _exact_candidate("front:oled", 9.8),
        "back:glass": _exact_candidate("back:glass", 0.2),
        "back:film": _exact_candidate("back:film", 0.8),
    }
    indexed = [
        _indexed("back", [0, 0, 0, 100, 50, 1]),
        _indexed("body", [0, 0, 1, 100, 50, 9]),
        _indexed("front", [0, 0, 9, 100, 50, 10]),
    ]
    assemblies = display.assemble_candidates(results, indexed)
    assert len(assemblies) == 2
    assert all(item["verdict"] == "AMBIGUOUS_FRONT_BACK" for item in assemblies)
    assert all(record["verdict"] == "AMBIGUOUS_FRONT_BACK"
               for record in results.values())
    assert results["front:glass"]["base_verdict"] == "STRONG_DISPLAY_EVIDENCE"


def _database(root, records):
    with sqlite3.connect(root / "geometry_features.sqlite3") as connection:
        connection.execute(
            "CREATE TABLE solids (solid_id TEXT, step TEXT, status TEXT, "
            "features_json TEXT, step_sha256 TEXT)"
        )
        for solid_id, step, feature in records:
            (root / step).write_text(solid_id, encoding="utf-8")
            connection.execute(
                "INSERT INTO solids VALUES (?,?,?,?,?)",
                (solid_id, step, "PASS", json.dumps(feature), solid_id),
            )


def _indexed_feature(bbox, *, volume=5000.0, surface=10200.0):
    return {
        "volume_mm3": volume,
        "surface_mm2": surface,
        "dimensions_mm": [bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]],
        "bbox_mm": bbox,
    }


def test_scan_clusters_layers_writes_atomic_cache_and_reuses(tmp_path, monkeypatch):
    _database(tmp_path, [
        ("front:glass", "glass.step", _indexed_feature([0, 0, 9, 100, 50, 9.5])),
        ("front:oled", "oled.step", _indexed_feature([0, 0, 9.6, 100, 50, 10])),
        ("body:block", "body.step", _indexed_feature(
            [0, 0, 0, 100, 50, 8], volume=40000, surface=19600)),
    ])
    monkeypatch.setattr(display, "source_signature", lambda _: ("display-signature", 3))
    monkeypatch.setattr(display, "_load_exclusion_context", lambda _: ({}, "context-v1"))

    def fake_one(task):
        solid_id, _path, digest = task
        if solid_id == "body:block":
            return solid_id, {
                "status": "PASS", "sha256": digest,
                "verdict": "OTHER", "reason": "not a plate",
            }
        z = 9.25 if solid_id.endswith("glass") else 9.8
        return solid_id, {"sha256": digest, **_exact_candidate(solid_id, z)}

    monkeypatch.setattr(display, "_one", fake_one)
    events = []
    first = display.scan(tmp_path, progress=events.append)
    assert not first["from_cache"]
    assert first["summary"]["matched"] == 2
    assert any(event["event"] == "match" for event in events)
    report = json.loads((tmp_path / display.CACHE_NAME).read_text(encoding="utf-8"))
    assert report["complete"]
    assert report["parameters"] == display.PARAMETERS
    assert not (tmp_path / "display_recognition.json.tmp").exists()
    assert report["results"]["body:block"]["verdict"] == "OTHER"
    assert report["results"]["front:glass"]["verdict"] == "PROBABLE_DISPLAY_MODULE"

    second = display.scan(tmp_path)
    assert second["from_cache"]


def test_existing_battery_result_excludes_plate(tmp_path, monkeypatch):
    from cst_material import battery_recognition

    _database(tmp_path, [
        ("cell", "cell.step", _indexed_feature([0, 0, 0, 100, 50, 1])),
    ])
    monkeypatch.setattr(battery_recognition, "complete_cached_report", lambda _: {
        "algorithm_version": battery_recognition.VERSION,
        "source_signature": "battery-source",
        "results": {"cell": {"status": "PASS", "verdict": "BATTERY_SHAPE"}},
    })
    monkeypatch.setattr(display, "source_signature", lambda _: ("battery-exclusion", 1))
    called = []
    monkeypatch.setattr(display, "_one", lambda task: called.append(task))
    result = display.scan(tmp_path)
    assert result["summary"]["excluded"] == 1
    assert not called
    report = json.loads((tmp_path / display.CACHE_NAME).read_text(encoding="utf-8"))
    assert report["results"]["cell"]["excluded_by"] == "BATTERY_SHAPE"


def test_unvalidated_battery_json_is_never_trusted(tmp_path, monkeypatch):
    from cst_material import battery_recognition

    _database(tmp_path, [
        ("plate", "plate.step", _indexed_feature([0, 0, 0, 100, 50, 1])),
    ])
    (tmp_path / "battery_recognition.json").write_text(json.dumps({
        "results": {"plate": {"status": "PASS", "verdict": "BATTERY_SHAPE"}}
    }), encoding="utf-8")
    monkeypatch.setattr(battery_recognition, "complete_cached_report", lambda _: None)
    exclusions, _signature = display._load_exclusion_context(tmp_path)
    assert "plate" not in exclusions


def test_cache_invalidates_when_source_signature_changes(tmp_path, monkeypatch):
    (tmp_path / display.CACHE_NAME).write_text(json.dumps({
        "algorithm_version": display.VERSION,
        "complete": True,
        "source_signature": "old",
        "summary": {"total": 0},
        "results": {},
    }), encoding="utf-8")
    monkeypatch.setattr(display, "source_signature", lambda _: ("new", 0))
    assert display.complete_cached_report(tmp_path) is None


def test_cache_invalidates_when_exclusion_context_changes(tmp_path, monkeypatch):
    (tmp_path / display.CACHE_NAME).write_text(json.dumps({
        "algorithm_version": display.VERSION,
        "complete": True,
        "source_signature": "same-source",
        "exclusion_signature": "context-v1",
        "scan_options": {"max_candidates": display.DEFAULT_MAX_CANDIDATES},
        "summary": {"total": 0},
        "results": {},
    }), encoding="utf-8")
    monkeypatch.setattr(display, "source_signature", lambda _: ("same-source", 0))
    monkeypatch.setattr(
        display, "_load_exclusion_context", lambda _: ({}, "context-v1")
    )
    assert display.complete_cached_report(tmp_path) is not None
    monkeypatch.setattr(
        display, "_load_exclusion_context", lambda _: ({"cell": "BATTERY_SHAPE"}, "context-v2")
    )
    assert display.complete_cached_report(tmp_path) is None


def test_cache_invalidates_when_candidate_limit_changes(tmp_path, monkeypatch):
    (tmp_path / display.CACHE_NAME).write_text(json.dumps({
        "algorithm_version": display.VERSION,
        "complete": True,
        "source_signature": "same-source",
        "exclusion_signature": "same-context",
        "scan_options": {"max_candidates": 25},
        "summary": {"total": 0},
        "results": {},
    }), encoding="utf-8")
    monkeypatch.setattr(display, "source_signature", lambda _: ("same-source", 0))
    assert display.complete_cached_report(
        tmp_path, exclusion_signature="same-context", max_candidates=25,
    ) is not None
    assert display.complete_cached_report(
        tmp_path, exclusion_signature="same-context", max_candidates=200,
    ) is None


def test_exterior_center_rank_recovers_rotated_aabb_false_negative():
    candidate = _exact_candidate("front", 100.0)
    candidate["obb_center_mm"] = [0.0, 0.0, 100.0]
    # Loose/rotated AABBs extend far beyond the exact candidate, so the AABB
    # boundary distance alone is deliberately false here.
    indexed = [
        _indexed("low", [-100, -100, -100, 100, 100, 20]),
        _indexed("middle", [-100, -100, -50, 100, 100, 80]),
        _indexed("front", [-100, -100, 0, 100, 100, 200]),
    ]
    evidence = display._exterior_evidence(candidate, indexed)
    assert not evidence["aabb_is_exterior"]
    assert evidence["center_is_exterior"]
    assert evidence["is_exterior"]
    assert evidence["center_extreme_rank"] == 0


def test_coarse_filter_rejects_rod_even_with_high_plate_index(tmp_path, monkeypatch):
    _database(tmp_path, [
        ("screen", "screen.step", _indexed_feature([0, 0, 9, 100, 50, 10])),
        ("rod", "rod.step", _indexed_feature(
            [0, 0, 0, 100, 1, 1], volume=100, surface=500)),
    ])
    monkeypatch.setattr(display, "source_signature", lambda _: ("coarse-filter", 2))
    monkeypatch.setattr(display, "_load_exclusion_context", lambda _: ({}, "none"))
    called = []

    def fake_one(task):
        called.append(task[0])
        return task[0], {"status": "PASS", "verdict": "OTHER", "sha256": task[2]}

    monkeypatch.setattr(display, "_one", fake_one)
    display.scan(tmp_path)
    assert called == ["screen"]


def test_cli_returns_success_for_complete_partial_report(monkeypatch):
    monkeypatch.setattr(display, "scan", lambda *_args, **_kwargs: {
        "summary": {"total": 10, "failed": 2},
        "output": "display_recognition.json",
        "from_cache": False,
    })
    assert display.main(["unused-folder"]) == 0


def test_real_occt_rotated_plate_has_stable_obb_and_planar_evidence():
    pytest.importorskip("OCP")
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

    plate = BRepPrimAPI_MakeBox(100, 50, 1).Solid()
    transform = gp_Trsf()
    transform.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), math.radians(37))
    rotated = BRepBuilderAPI_Transform(plate, transform, True).Shape()
    profile = display._oriented_profile([rotated])
    assert profile["obb_dimensions_mm"] == pytest.approx([100, 50, 1], rel=0.03)
    planar = display._planar_profile([rotated], math.prod(profile["obb_dimensions_mm"][:2]))
    assert planar["major_plane_coverage"] > 0.95
    assert planar["opposed_major_planes"]


def test_recognize_step_end_to_end_on_synthetic_plate(tmp_path):
    pytest.importorskip("OCP")
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    path = tmp_path / "synthetic_display_plate.step"
    writer = STEPControl_Writer()
    assert writer.Transfer(
        BRepPrimAPI_MakeBox(100.0, 50.0, 1.0).Solid(), STEPControl_AsIs,
    ) == IFSelect_ReturnStatus.IFSelect_RetDone
    assert writer.Write(str(path)) == IFSelect_ReturnStatus.IFSelect_RetDone

    result = display.recognize_step(path)
    assert result["verdict"] == "DISPLAY_CANDIDATE"
    assert result["recognition_stages"]["major_planes"]
    assert result["recognition_stages"]["sheet_pair"]
    assert result["identity_confirmed"] is False


def test_recognize_step_records_real_brep_opening_evidence(tmp_path):
    pytest.importorskip("OCP")
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    plate = BRepPrimAPI_MakeBox(100.0, 50.0, 1.0).Solid()
    opening = BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(20.0, 25.0, -1.0), gp_Dir(0.0, 0.0, 1.0)),
        3.0,
        3.0,
    ).Solid()
    perforated = BRepAlgoAPI_Cut(plate, opening).Shape()
    path = tmp_path / "synthetic_perforated_panel.step"
    writer = STEPControl_Writer()
    assert writer.Transfer(
        perforated, STEPControl_AsIs,
    ) == IFSelect_ReturnStatus.IFSelect_RetDone
    assert writer.Write(str(path)) == IFSelect_ReturnStatus.IFSelect_RetDone

    result = display.recognize_step(path)
    assert result["verdict"] == "DISPLAY_CANDIDATE"
    assert result["local_structure_evidence"]
    assert result["planar_profile"]["inner_wire_count"] >= 1
