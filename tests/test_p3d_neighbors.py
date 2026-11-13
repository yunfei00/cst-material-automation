"""P3-D correctness, 7311-Solid scale, offline safety; synthetic data only."""
from __future__ import annotations

import json
import math
import random
from dataclasses import asdict
from pathlib import Path

import pytest

from cst_material import cli, p3_spatial
from cst_material.domain import BoundingBox
from cst_material.p3_neighbors import analyze_neighbors, read_spatial_report
from cst_material.p3_spatial import SpatialCatalog, SpatialFeature


def catalog_of(bounds: list[BoundingBox | None]) -> SpatialCatalog:
    features = []
    for i, bbox in enumerate(bounds):
        features.append(SpatialFeature(
            identifier=f"part:{i:05d}",
            component="part", material="TestMaterial",
            loose_bounds=bbox, dimensions_project_unit=bbox.dimensions if bbox else None,
            bbox_midpoint_project_unit=None, min_bbox_extent_project_unit=None,
        ))
    return SpatialCatalog(
        length_unit="mm", length_unit_to_m=0.001,
        declared_solid_count=len(features), features=tuple(features),
        missing_bbox_count=sum(b is None for b in bounds),
    )


def write_p3c_report(path: Path, catalog: SpatialCatalog, *, status="PASS"):
    payload = {
        "task": "spatial", "status": status,
        "spatial": {
            "length_unit": catalog.length_unit,
            "length_unit_to_m": catalog.length_unit_to_m,
            "solid_count": catalog.declared_solid_count,
            "missing_bbox_count": catalog.missing_bbox_count,
            "cleanup_deferred": status == "PARTIAL",
            "features": [asdict(f) for f in catalog.features],
            "neighbors": {
                "status": "SKIPPED",
                "reason": "有效 Solid 数量超过旧版 800 限制",
                "neighbors": [],
            },
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_nearest_matches_simple_reference_for_random_small_solids():
    rng = random.Random(326)
    boxes = []
    for _ in range(19):
        x, y, z = (rng.randint(-5, 5) for _ in range(3))
        boxes.append(BoundingBox(x, y, z, x + 2, y + 2, z + 2))
    result = analyze_neighbors(catalog_of(boxes), 5, batch_size=3)
    assert result["status"] == "PASS"
    for i, item in enumerate(result["neighbors"]):
        expected = sorted(
            (
                (p3_spatial._bbox_separation(boxes[i], boxes[j]), f"part:{j:05d}")
                for j in range(len(boxes)) if i != j
            ),
            key=lambda pair: (pair[0], pair[1]),
        )[:5]
        actual = item["nearest_bbox_candidates"]
        assert [(row["loose_bbox_distance_lower_bound_project_unit"],
                 row["identifier"]) for row in actual] == expected
        assert all(
            math.isclose(
                row["loose_bbox_distance_lower_bound_m"],
                row["loose_bbox_distance_lower_bound_project_unit"] * 0.001,
                rel_tol=1e-12,
            )
            for row in actual
        )


def test_all_overlapping_bounds_resolve_ties_by_solid_identifier():
    boxes = [BoundingBox(0, 0, 0, 1, 1, 1)] * 18
    result = analyze_neighbors(catalog_of(boxes), 5, batch_size=7)
    assert result["status"] == "PASS"
    first = result["neighbors"][0]
    assert [x["identifier"] for x in first["nearest_bbox_candidates"]] == [
        f"part:{i:05d}" for i in range(1, 6)
    ]
    tenth = result["neighbors"][10]
    assert [x["identifier"] for x in tenth["nearest_bbox_candidates"]] == [
        f"part:{i:05d}" for i in range(5)
    ]
    assert all(
        x["loose_boxes_overlap_or_touch"]
        for row in result["neighbors"] for x in row["nearest_bbox_candidates"]
    )


def test_7311_synthetic_solids_full_neighbor_analysis_not_skipped():
    count = 7311
    boxes = [
        BoundingBox(float(i * 3), 0, 0, float(i * 3 + 1), 1, 1)
        for i in range(count)
    ]
    result = analyze_neighbors(catalog_of(boxes), 5, batch_size=32)
    assert result["status"] == "PASS"
    assert result["method"] == "numpy-batched-loose-aabb"
    assert result["analyzed_solid_count"] == 7311
    assert len(result["neighbors"]) == 7311
    middle = result["neighbors"][4000]["nearest_bbox_candidates"]
    assert len(middle) == 5
    assert [item["identifier"] for item in middle[:2]] == [
        "part:03999", "part:04001"
    ]
    assert [item["loose_bbox_distance_lower_bound_project_unit"] for item in middle[:2]] == [
        2.0, 2.0
    ]


def test_invalid_neighbor_count_and_excessive_size_are_explicit():
    sample = catalog_of([BoundingBox(0, 0, 0, 1, 1, 1)])
    with pytest.raises(ValueError, match="邻居数量"):
        analyze_neighbors(sample, 21)
    with pytest.raises(ValueError, match="分批数量"):
        analyze_neighbors(sample, 1, batch_size=129)
    assert analyze_neighbors(sample, 0)["status"] == "SKIPPED"
    assert analyze_neighbors(sample, 1)["neighbors"][0]["nearest_bbox_candidates"] == []
    extra = catalog_of([BoundingBox(0, 0, 0, 1, 1, 1)] * 12001)
    assert analyze_neighbors(extra, 1)["status"] == "SKIPPED"


def test_offline_uses_existing_p3c_json_without_invoking_cst(tmp_path, monkeypatch):
    bbox = BoundingBox(0, 0, 0, 1, 1, 1)
    original = tmp_path / "P3C_Spatial_Report.json"
    destination = tmp_path / "P3D_Neighbors_Report.json"
    write_p3c_report(original, catalog_of([bbox, bbox, None]), status="PARTIAL")
    before = original.read_bytes()
    monkeypatch.setattr(
        cli, "query_spatial_from_copy",
        lambda *args, **kwargs: pytest.fail("P3-D must never connect to CST"),
    )
    assert cli.main([
        "spatial-offline", "--report-in", str(original),
        "--report", str(destination), "--neighbors", "2",
    ]) == 1  # one missing geometry -> PARTIAL
    data = json.loads(destination.read_text(encoding="utf-8"))
    assert data["status"] == "PARTIAL"
    assert data["input_summary"]["solid_count"] == 3
    assert data["input_summary"]["source_cleanup_deferred"] is True
    assert data["neighbors"]["status"] == "PASS"
    assert data["neighbors"]["analyzed_solid_count"] == 2
    assert original.read_bytes() == before


def test_prevent_overwriting_source_json(tmp_path):
    original = tmp_path / "P3C_Report.json"
    write_p3c_report(original, catalog_of([BoundingBox(0, 0, 0, 1, 1, 1)]))
    before = original.read_bytes()
    assert cli.main([
        "spatial-offline", "--report-in", str(original),
        "--report", str(original),
    ]) == 2
    assert original.read_bytes() == before


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(status="FAIL"),
    lambda d: d["spatial"].update(solid_count=100),
    lambda d: d["spatial"]["features"][0].update(loose_bounds={
        "xmin": 2, "xmax": 1, "ymin": 0, "ymax": 1, "zmin": 0, "zmax": 1
    }),
    lambda d: d["spatial"]["features"].append(d["spatial"]["features"][0]),
    lambda d: d["spatial"].update(missing_bbox_count=1),
])
def test_reject_corrupt_or_failed_p3c_reports(tmp_path, mutation):
    original = tmp_path / "P3C.json"
    write_p3c_report(original, catalog_of([BoundingBox(0, 0, 0, 1, 1, 1)]))
    data = json.loads(original.read_text(encoding="utf-8"))
    mutation(data)
    original.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises((ValueError, KeyError)):
        read_spatial_report(original)
