"""P5 feature database offline tests."""
import json
import sqlite3

from cst_material.geometry_library import build_feature_library, query_features
from cst_material.occt_geometry import sha256_file


def make_export(tmp_path):
    steps = tmp_path / "steps"
    steps.mkdir()
    entries = []
    for name in ("Main:A", "Main:B", "Other:C"):
        step = steps / f"{name.split(':')[1]}.step"
        step.write_bytes((name * 30).encode())
        entries.append({"solid_id": name, "material": "PEC", "step": f"steps/{step.name}",
                        "sha256": sha256_file(step)})
    (tmp_path / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "unit": "mm", "solids": entries,
    }), encoding="utf-8")
    return entries


def fake_analyzer(path):
    return {"step_sha256": sha256_file(path), "volume_mm3": 12.0,
            "surface_mm2": 20.0, "dimensions_mm": [2, 3, 4],
            "face_count": 6, "edge_count": 12, "vertex_count": 8,
            "solid_body_count": 1}


def test_build_query_and_resume(tmp_path):
    make_export(tmp_path)
    calls = []
    def analyze(path):
        calls.append(path)
        return fake_analyzer(path)
    first = build_feature_library(tmp_path, analyzer=analyze)
    assert first["status"] == "PASS"
    assert (first["total"], first["success"], first["failed"]) == (3, 3, 0)
    assert len(query_features(tmp_path, keyword="Main:")) == 2
    assert query_features(tmp_path, min_volume=20) == []
    again = build_feature_library(tmp_path, analyzer=analyze)
    assert again["resumed"] == 3
    assert len(calls) == 3
    with sqlite3.connect(tmp_path / "geometry_features.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM solids").fetchone()[0] == 3


def test_failure_retries_and_hash_mismatch(tmp_path):
    entries = make_export(tmp_path)
    failed = {"enabled": True}
    def analyze(path):
        if path.name == "B.step" and failed["enabled"]:
            raise RuntimeError("broken")
        return fake_analyzer(path)
    first = build_feature_library(tmp_path, analyzer=analyze)
    assert (first["status"], first["failed"]) == ("PARTIAL", 1)
    failed["enabled"] = False
    second = build_feature_library(tmp_path, analyzer=analyze)
    assert (second["status"], second["resumed"]) == ("PASS", 2)
    (tmp_path / entries[0]["step"]).write_bytes(b"tampered")
    third = build_feature_library(tmp_path, analyzer=analyze)
    assert third["failed"] == 1
    assert "SHA256" in third["errors"][0]["error"]


def test_reject_changed_manifest(tmp_path):
    entries = make_export(tmp_path)
    build_feature_library(tmp_path, analyzer=fake_analyzer)
    entries.pop()
    (tmp_path / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "unit": "mm", "solids": entries,
    }), encoding="utf-8")
    import pytest
    with pytest.raises(RuntimeError, match="manifest"):
        build_feature_library(tmp_path, analyzer=fake_analyzer)
