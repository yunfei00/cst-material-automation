"""Relocatable P5 feature data and detached search tests."""
import json
import shutil

from cst_material.geometry_library import build_feature_library
from cst_material.geometry_search import search_geometry
from cst_material.occt_geometry import sha256_file
from cst_material.solid_index import load_manifest


def test_detached_features_work_without_step_or_manifest(tmp_path):
    source = tmp_path / "original"
    source.mkdir()
    step = source / "piece.step"
    step.write_bytes(b"dummy")
    (source / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "unit": "mm",
        "solids": [{"solid_id": "Part:One", "step": "piece.step",
                    "sha256": sha256_file(step)}],
    }), encoding="utf-8")

    def analyze(path):
        return {
            "step_sha256": sha256_file(path),
            "dimensions_mm": [1, 2, 3],
            "volume_mm3": 6, "surface_mm2": 22,
            "face_count": 6, "edge_count": 12,
            "vertex_count": 8, "solid_body_count": 1,
            "surface_types": {"Plane": 6},
        }

    assert build_feature_library(source, analyzer=analyze)["status"] == "PASS"
    relocated = tmp_path / "relocated"
    relocated.mkdir()
    shutil.copy2(source / "geometry_features.sqlite3",
                 relocated / "geometry_features.sqlite3")
    results = search_geometry(relocated, spring_candidates=True)
    assert isinstance(results, list)
    shutil.copy2(source / "manifest.json", relocated / "manifest.json")
    assert load_manifest(relocated, verify_files=False, require_files=False)["solids"]
