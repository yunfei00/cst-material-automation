"""Full-geometry shield discovery must not be gated by the P5 shield score."""
import json
import sqlite3

from cst_material.shield_refinement import _all_geometry_candidates


def test_full_scan_includes_low_score_and_large_solids(tmp_path):
    db = sqlite3.connect(tmp_path / "geometry_features.sqlite3")
    db.execute("CREATE TABLE solids (solid_id TEXT, step TEXT, status TEXT, features_json TEXT)")
    for sid, volume, status in (
        ("plain_sheet", 2.0, "PASS"),
        ("shield_missed_by_p5", 150.0, "PASS"),
        ("large_body", 5000.0, "PASS"),
        ("invalid", -1.0, "PASS"),
        ("failed", 300.0, "FAIL"),
    ):
        db.execute("INSERT INTO solids VALUES (?,?,?,?)",
                   (sid, sid + ".step", status, json.dumps({"volume_mm3": volume})))
    db.commit()
    db.close()
    rows = _all_geometry_candidates(tmp_path)
    assert [row["solid_id"] for row in rows] == [
        "large_body", "shield_missed_by_p5", "plain_sheet"
    ]
    assert all(row["score"] == 0 for row in rows)
