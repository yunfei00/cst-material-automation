"""PCB-relative shield screening: spatial and name regression tests."""
import json
import sqlite3

from cst_material.shield_recognition import search_pcb_related_shields


def test_pcb_relative_shields(tmp_path):
    with sqlite3.connect(tmp_path / "geometry_features.sqlite3") as db:
        db.execute("CREATE TABLE solids (solid_id TEXT, step TEXT, features_json TEXT, status TEXT)")
        cases = [
            ("assembly-main-pcb:MAIN PCB", [0, 0, 0, 100, 80, 1], 8000),
            ("assembly:shielding PMU BB frame", [10, 10, 1.05, 50, 18, 2.05], 100),
            ("assembly:unnamed hollow frame", [15, 15, 1.04, 55, 22, 2.04], 100),
            ("assembly:shield far above", [10, 10, 20, 50, 30, 21], 100),
            ("assembly:shield far below", [10, 10, -30, 50, 30, -29], 100),
            ("assembly:shield no XY overlap", [200, 200, 1.01, 240, 220, 2.01], 100),
        ]
        for sid, bbox, volume in cases:
            data = {"bbox_mm": bbox, "volume_mm3": volume}
            db.execute("INSERT INTO solids VALUES (?, ?, ?, 'PASS')",
                       (sid, "fixture.step", json.dumps(data)))
    result = search_pcb_related_shields(tmp_path)
    matches = {row["solid_id"]: row for row in result["results"]}
    assert len(matches) == 2
    assert matches["assembly:shielding PMU BB frame"]["verdict"] == "NAMED_GEOMETRY"
    assert matches["assembly:unnamed hollow frame"]["verdict"] == "GEOMETRY_REVIEW"
    assert all(row["side"] == "TOP" for row in matches.values())
