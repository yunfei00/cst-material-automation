"""Main PCB selection regression tests."""
import json
import sqlite3

from cst_material.pcb_recognition import find_main_pcb


def _fixture(tmp_path, records):
    with sqlite3.connect(tmp_path / "geometry_features.sqlite3") as db:
        db.execute("CREATE TABLE solids (solid_id TEXT, step TEXT, features_json TEXT, status TEXT)")
        for sid, dx, dy, dz in records:
            data = {"volume_mm3": dx * dy * dz,
                    "bbox_mm": [0, 0, 0, dx, dy, dz]}
            db.execute("INSERT INTO solids VALUES (?, ?, ?, 'PASS')",
                       (sid, "fixture.step", json.dumps(data)))


def test_prefers_thin_large_board_over_largest_thick_solid(tmp_path):
    _fixture(tmp_path, [
        ("assembly-main-pcb:thick", 30, 30, 10),
        ("assembly-main-pcb:board", 100, 80, 1),
        ("other:enclosure", 1000, 1000, 100),
    ])
    result = find_main_pcb(tmp_path)
    assert result["candidate"]["solid_id"] == "assembly-main-pcb:board"
    assert result["status"] == "GEOMETRY_CANDIDATE"
    assert result["domain_count"] == 2


def test_thick_only_returns_review_not_confirmed(tmp_path):
    _fixture(tmp_path, [("x-main-pcb:block", 20, 20, 20)])
    result = find_main_pcb(tmp_path)
    assert result["status"] == "NEEDS_REVIEW"


def test_missing_domain_does_not_select_global_largest(tmp_path):
    _fixture(tmp_path, [("other:board", 100, 100, 1)])
    result = find_main_pcb(tmp_path)
    assert result["status"] == "NOT_FOUND"
    assert result["candidate"] is None
