"""P3-C spatial analysis: synthetic data only; no CST or proprietary geometry."""
import math

import pytest

from cst_material.p3_spatial import (
    MARKER,
    analyze_neighbors,
    build_spatial_macro,
    parse_spatial_data,
    summarize_spatial,
)


def rows_for_two_solids():
    return [
        MARKER, "mm", "0.001", "2",
        "device:solid_1", "Copper", "OK",
        "0", "2", "-1", "1", "5", "9",
        "device:solid_2", "Vacuum", "OK",
        "5", "7", "0", "3", "8", "10",
    ]


def test_parse_loose_bounds_and_convert_dimensions():
    catalog = parse_spatial_data(rows_for_two_solids())
    assert catalog.declared_solid_count == 2
    assert catalog.missing_bbox_count == 0
    assert catalog.length_unit == "mm"
    a, b = catalog.features
    assert a.identifier == "device:solid_1"
    assert a.component == "device"
    assert a.material == "Copper"
    assert a.loose_bounds.xmin == 0
    assert a.loose_bounds.ymin == -1
    assert a.loose_bounds.zmax == 9
    assert a.dimensions_project_unit == pytest.approx((2, 2, 4))
    assert a.bbox_midpoint_project_unit == pytest.approx((1, 0, 7))
    assert a.min_bbox_extent_project_unit == 2
    summary = summarize_spatial(catalog, neighbor_limit=1)
    assert summary["features"][0]["dimensions_m"] == pytest.approx([0.002, 0.002, 0.004])
    assert summary["features"][1]["bbox_midpoint_m"] == pytest.approx(
        [0.006, 0.0015, 0.009]
    )
    neighbor = summary["neighbors"]["neighbors"][0]["nearest_bbox_candidates"][0]
    assert neighbor["identifier"] == b.identifier
    assert neighbor["loose_bbox_distance_lower_bound_project_unit"] == 3
    assert neighbor["loose_bbox_distance_lower_bound_m"] == pytest.approx(0.003)
    assert not neighbor["loose_boxes_overlap_or_touch"]


def test_loose_bbox_overlap_is_not_claim_of_real_contact():
    rows = rows_for_two_solids()
    rows[-6:] = ["1", "7", "0", "3", "8", "10"]
    catalog = parse_spatial_data(rows)
    neighbor = analyze_neighbors(catalog, 1)["neighbors"][0]["nearest_bbox_candidates"][0]
    assert neighbor["loose_boxes_overlap_or_touch"]
    assert neighbor["loose_bbox_distance_lower_bound_m"] == 0
    assert "不证明实体接触" in summarize_spatial(catalog, 1)["interpretation"]


def test_missing_bbox_remains_missing_not_synthesized():
    rows = [MARKER, "cm", "0.01", "1", "main:solid", "", "MISSING"]
    catalog = parse_spatial_data(rows)
    assert catalog.missing_bbox_count == 1
    feature = catalog.features[0]
    assert feature.loose_bounds is None
    assert feature.dimensions_project_unit is None
    assert feature.material is None


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda rows: rows.__setitem__(0, "wrong"), "版本标识"),
        (lambda rows: rows.__setitem__(1, "unknown"), "长度单位"),
        (lambda rows: rows.__setitem__(2, "0"), "大于零"),
        (lambda rows: rows.__setitem__(3, "-1"), "无效数量"),
        (lambda rows: rows.__setitem__(3, "3"), "不完整"),
        (lambda rows: rows.__setitem__(4, ""), "名称无效"),
        (lambda rows: rows.__setitem__(6, "UNKNOWN"), "未知状态"),
        (lambda rows: rows.__setitem__(7, "NaN"), "NaN"),
        (lambda rows: rows.__setitem__(7, "5"), "最小值"),
        (lambda rows: rows.__setitem__(16, "Inf"), "NaN"),
    ],
)
def test_spatial_fail_closed_on_corrupt_or_partial_data(mutator, message):
    rows = rows_for_two_solids()
    mutator(rows)
    with pytest.raises(RuntimeError, match=message):
        parse_spatial_data(rows)


def test_spatial_rejects_duplicate_name_and_extra_rows():
    rows = rows_for_two_solids()
    rows[13] = rows[4]
    with pytest.raises(RuntimeError, match="重复"):
        parse_spatial_data(rows)
    with pytest.raises(RuntimeError, match="未解释"):
        parse_spatial_data(rows_for_two_solids() + ["unknown"])


def test_command_macro_runs_outside_history_and_is_read_only(tmp_path):
    code = build_spatial_macro(tmp_path / "probe.txt")
    assert code.startswith("Option Explicit\nSub Main()")
    assert "Solid.GetLooseBoundingBoxOfShape(shapeName" in code
    assert 'Solid.GetMaterialNameForShape(shapeName)' in code
    assert "AddToHistory" not in code
    assert "Save" not in code
    assert "ChangeMaterial" not in code
    assert "Close #fh" in code


def test_nearest_neighbors_cap_and_zero_count():
    catalog = parse_spatial_data(rows_for_two_solids())
    with pytest.raises(ValueError, match="0 到 20"):
        analyze_neighbors(catalog, -1)
    with pytest.raises(ValueError, match="0 到 20"):
        analyze_neighbors(catalog, 21)
    assert analyze_neighbors(catalog, 0)["status"] == "SKIPPED"
    assert all(
        math.isfinite(row["loose_bbox_distance_lower_bound_m"])
        for item in analyze_neighbors(catalog, 1)["neighbors"]
        for row in item["nearest_bbox_candidates"]
    )
