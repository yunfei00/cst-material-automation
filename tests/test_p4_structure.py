"""P4-A: offline shape-hint integration, provenance, and no-material safeguards."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cst_material import cli
from cst_material.p3_neighbors import analyze_neighbors, read_spatial_report
from cst_material.p4_structure import build_structure_report


def _fixture_reports(
    tmp_path: Path, ranges: list[tuple[float, float, float]],
    *, with_neighbors: bool = True,
) -> tuple[Path, Path, Path | None]:
    project = r"C:\private\test.cst"
    a_path = tmp_path / "p3a.json"
    c_path = tmp_path / "p3c.json"
    d_path = tmp_path / "p3d.json"
    geom = []
    spatial = []
    for i, (sx, sy, sz) in enumerate(ranges):
        name = f"component:s{i:05d}"
        geom.append({
            "identifier": name, "component": "component", "material": "Vacuum",
            "volume_project_unit3": sx * sy * sz,
            "area_project_unit2": 2 * (sx * sy + sy * sz + sx * sz),
            "volume_m3": sx * sy * sz * 1e-9,
            "area_m2": 2 * (sx * sy + sy * sz + sx * sz) * 1e-6,
            "compactness": None,
        })
        spatial.append({
            "identifier": name, "component": "component", "material": "Vacuum",
            "loose_bounds": {
                "xmin": float(200 * i), "ymin": 0.0, "zmin": 0.0,
                "xmax": float(200 * i + sx), "ymax": sy, "zmax": sz,
            },
        })
    a = {
        "task": "geometry", "status": "PASS", "source": project,
        "solid_count": len(geom),
        "geometry": {
            "length_unit": "mm", "length_unit_to_m": 0.001,
            "features": geom,
        },
    }
    c = {
        "task": "spatial", "status": "PASS", "source": project,
        "spatial": {
            "length_unit": "mm", "length_unit_to_m": 0.001,
            "solid_count": len(spatial), "missing_bbox_count": 0,
            "features": spatial, "neighbors": {"status": "SKIPPED"},
        },
    }
    a_path.write_text(json.dumps(a), encoding="utf-8")
    c_path.write_text(json.dumps(c), encoding="utf-8")
    if with_neighbors:
        catalog, _ = read_spatial_report(c_path)
        output = analyze_neighbors(catalog, neighbor_limit=2, batch_size=2)
        d = {
            "task": "spatial-offline", "status": "PASS", "source": str(c_path.resolve()),
            "input_summary": {
                "solid_count": len(spatial), "valid_bbox_count": len(spatial)
            },
            "neighbors": output,
        }
        d_path.write_text(json.dumps(d), encoding="utf-8")
    return a_path, c_path, d_path if with_neighbors else None


def test_shape_hints_are_explainable_and_never_recommend_materials(tmp_path):
    a, c, d = _fixture_reports(
        tmp_path, [(100, 50, 2), (100, 5, 5), (10, 10, 10), (30, 20, 10)]
    )
    report = build_structure_report(a, c, d)
    assert report["status"] == "PASS"
    assert report["solid_count"] == 4
    hints = [item["shape_hint"] for item in report["records"]]
    assert hints == [
        "flat_box_candidate", "elongated_box_candidate",
        "near_equal_axes_box_candidate", "other_axis_ratio_box_candidate",
    ]
    assert report["shape_hint_counts"] == {item: 1 for item in hints}
    assert report["with_neighbor_data_count"] == 4
    assert report["source_summary"]["project_version_identity_verified"] is False
    for row in report["records"]:
        assert row["material_recommendation"] is None
        assert row["needs_engineer_review_for_material"] is True
        assert row["observed_existing_material"] == "Vacuum"
        assert row["loose_bbox_dimensions_m"] is not None
        assert row["reasons"]
        assert row["nearest_loose_bbox_candidate"] is not None


def test_p4a_cli_does_not_connect_to_cst_or_mutate_sources(tmp_path, monkeypatch):
    a, c, d = _fixture_reports(tmp_path, [(100, 70, 1), (5, 5, 5)])
    output = tmp_path / "P4A_Structure_Preview.json"
    original_bytes = [f.read_bytes() for f in (a, c, d)]
    monkeypatch.setattr(
        cli, "connect", lambda: pytest.fail("P4-A must never connect to CST")
    )
    rc = cli.main([
        "structure-preview", "--geometry-report", str(a),
        "--spatial-report", str(c), "--neighbors-report", str(d),
        "--report", str(output),
    ])
    content = json.loads(output.read_text(encoding="utf-8"))
    assert rc == 0
    assert content["status"] == "PASS"
    assert content["solid_count"] == 2
    assert content["shape_hint_counts"]["flat_box_candidate"] == 1
    assert content["shape_hint_counts"]["near_equal_axes_box_candidate"] == 1
    assert [f.read_bytes() for f in (a, c, d)] == original_bytes


def test_geometry_and_spatial_must_have_same_ids(tmp_path):
    a, c, d = _fixture_reports(tmp_path, [(10, 10, 10), (2, 2, 2)])
    data = json.loads(a.read_text(encoding="utf-8"))
    data["geometry"]["features"][1]["identifier"] = "unrelated:solid"
    a.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Solid 集合不一致"):
        build_structure_report(a, c, d)


def test_different_project_paths_or_mismatched_materials_rejected(tmp_path):
    a, c, _ = _fixture_reports(tmp_path, [(10, 10, 10)])
    data = json.loads(c.read_text(encoding="utf-8"))
    data["source"] = r"D:\other\model.cst"
    c.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="来源文件路径不同"):
        build_structure_report(a, c)
    data["source"] = r"C:\private\test.cst"
    data["spatial"]["features"][0]["material"] = "Copper"
    c.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="材料与 P3-A 不同"):
        build_structure_report(a, c)


def test_neighbor_report_incorrect_distance_rejected(tmp_path):
    a, c, d = _fixture_reports(tmp_path, [(10, 10, 10), (10, 10, 10)])
    data = json.loads(d.read_text(encoding="utf-8"))
    data["neighbors"]["neighbors"][0]["nearest_bbox_candidates"][0][
        "loose_bbox_distance_lower_bound_project_unit"
    ] = 999.0
    d.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="距离与 P3-C"):
        build_structure_report(a, c, d)


def test_p4a_output_must_not_overwrite_any_input(tmp_path):
    a, c, d = _fixture_reports(tmp_path, [(10, 10, 10)])
    original = c.read_bytes()
    assert cli.main([
        "structure-preview", "--geometry-report", str(a),
        "--spatial-report", str(c), "--neighbors-report", str(d),
        "--report", str(c),
    ]) == 2
    assert c.read_bytes() == original


def test_missing_bbox_is_unclassified_and_partial(tmp_path):
    a, c, _ = _fixture_reports(tmp_path, [(1, 1, 1), (10, 10, 10)])
    data = json.loads(c.read_text(encoding="utf-8"))
    data["spatial"]["features"][1]["loose_bounds"] = None
    data["spatial"]["missing_bbox_count"] = 1
    data["status"] = "PARTIAL"
    c.write_text(json.dumps(data), encoding="utf-8")
    report = build_structure_report(a, c)
    assert report["status"] == "PARTIAL"
    assert report["shape_hint_counts"]["unknown"] == 1
    assert report["records"][1]["material_recommendation"] is None


def test_many_solids_generate_only_hints_and_small_summary(tmp_path):
    a, c, _ = _fixture_reports(tmp_path, [(10, 5, 1)] * 7311, with_neighbors=False)
    report = build_structure_report(a, c)
    assert report["status"] == "PASS"
    assert report["solid_count"] == 7311
    assert sum(report["shape_hint_counts"].values()) == 7311
    assert report["with_neighbor_data_count"] == 0
    assert all(row["material_recommendation"] is None for row in report["records"])
