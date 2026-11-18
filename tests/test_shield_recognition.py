"""P8 candidate heuristics are conservative review-only signals."""
import json
import sqlite3

from cst_material.shield_recognition import (
    evaluate_shield_candidate,
    search_shield_candidates,
)


def test_shield_candidate_and_invalid_features():
    sample = {
        "dimensions_mm": [1, 20, 30],
        "volume_mm3": 80,
        "surface_mm2": 900,
        "surface_types": {"GeomAbs_Plane": 10},
    }
    result = evaluate_shield_candidate(sample)
    assert result["candidate"] is True
    assert result["score"] == 100
    assert result["review_status"] == "NEEDS_REVIEW"
    assert evaluate_shield_candidate({"dimensions_mm": [0, 2, 3]})["candidate"] is False


def test_search_shield_candidates_without_step(tmp_path):
    db = tmp_path / "geometry_features.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE solids (solid_id TEXT, step TEXT, features_json TEXT, status TEXT)"
        )
        for name, volume in (("a:shield", 80), ("b:block", 600)):
            conn.execute(
                "INSERT INTO solids VALUES (?,?,?,?)",
                (name, f"steps/{name}.step", json.dumps({
                    "dimensions_mm": [1, 20, 30],
                    "volume_mm3": volume,
                    "surface_mm2": 900 if volume == 80 else 150,
                    "surface_types": {"GeomAbs_Plane": 10 if volume == 80 else 6},
                }), "PASS"),
            )
    rows = search_shield_candidates(tmp_path)
    assert [row["solid_id"] for row in rows] == ["a:shield"]
    assert rows[0]["review_status"] == "NEEDS_REVIEW"
