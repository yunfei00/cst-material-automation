"""Offline grouping regression tests (no CST or Qt required)."""
import json
import sqlite3

from cst_material.geometry_grouping import group_solids


def test_grouping_keeps_distinct_sizes_separate(tmp_path):
    db = sqlite3.connect(tmp_path / "geometry_features.sqlite3")
    db.execute("CREATE TABLE metadata (key TEXT, value TEXT)")
    db.execute("INSERT INTO metadata VALUES ('manifest_sha256', 'fixture')")
    db.execute("CREATE TABLE solids (solid_id TEXT, step_sha256 TEXT, features_json TEXT, status TEXT)")
    def features(size):
        return {"dimensions_mm": [size, size * 2, size * 3],
                "volume_mm3": size ** 3 * 6,
                "surface_mm2": size ** 2 * 22,
                "face_count": 6, "surface_types": {"Plane": 6}}
    for sid, size in [("A", 1), ("B", 1), ("C", 10)]:
        db.execute("INSERT INTO solids VALUES (?, ?, ?, 'PASS')",
                   (sid, sid, json.dumps(features(size))))
    db.commit()
    db.close()
    result = group_solids(tmp_path)
    assert result["total"] == 3
    assert result["group_count"] == 2
    assert result["groups"][0]["count"] == 2
    assert {m["solid_id"] for m in result["groups"][0]["members"]} == {"A", "B"}
    assert result["groups"][1]["members"][0]["solid_id"] == "C"
