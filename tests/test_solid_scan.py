"""Grid scan sampling and output tests without CST/OCCT runtime."""
import csv

import pytest

from cst_material.solid_scan import axis_points, scan_grid


def test_axis_count_and_endpoints():
    assert list(axis_points(-2, 4, 4)) == [-2, 0, 2, 4]
    assert list(axis_points(1, 1, 3)) == [1, 1, 1]


def test_scan_xyz_counts_and_values(tmp_path):
    dest = tmp_path / "scan.csv"

    def classify(_solids, point):
        return "INSIDE" if point == (0, 0, 0) else "OUTSIDE"

    result = scan_grid(
        [object()], [-1, -1, -1, 1, 1, 1],
        (3, 3, 3), dest, classify=classify,
    )
    assert result["total"] == 27
    assert result["inside"] == 1
    assert result["outside"] == 26
    with dest.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 27
    assert {row["value"] for row in rows} == {"-1", "1"}


def test_reject_excessive_count_before_allocating(tmp_path):
    with pytest.raises(ValueError, match="安全上限"):
        scan_grid([], [0, 0, 0, 1, 1, 1], (1000, 1000, 1000), tmp_path / "scan.csv")
    assert not (tmp_path / "scan.csv").exists()
