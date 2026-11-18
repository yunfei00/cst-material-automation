"""P6 search must preserve identities and never assign material."""
import json

import pytest

from cst_material.geometry_library import build_feature_library
from cst_material.geometry_search import search_geometry
from cst_material.occt_geometry import sha256_file


def prepare(tmp_path):
    (tmp_path / "steps").mkdir()
    entries = []
    for name in ("main:reference", "main:near", "main:other"):
        path = tmp_path / "steps" / f"{name.split(':')[1]}.step"
        path.write_bytes(name.encode())
        entries.append({"solid_id": name, "step": f"steps/{path.name}",
                        "sha256": sha256_file(path)})
    (tmp_path / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "unit": "mm", "solids": entries,
    }), encoding="utf-8")

    def analyze(path):
        dims = [0.2, 5, 20] if path.stem != "other" else [4, 5, 6]
        return {
            "step_sha256": sha256_file(path), "volume_mm3": 8.0,
            "surface_mm2": 100.0, "dimensions_mm": dims,
            "face_count": 8, "edge_count": 20, "vertex_count": 16,
            "solid_body_count": 1,
            "surface_types": {"GeomAbs_Plane": 6, "GeomAbs_Cylinder": 2},
        }
    assert build_feature_library(tmp_path, analyzer=analyze)["status"] == "PASS"


def test_similar_and_spring_candidates(tmp_path):
    prepare(tmp_path)
    result = search_geometry(tmp_path, reference_id="main:reference")
    assert [x["solid_id"] for x in result] == ["main:near", "main:other"]
    assert result[0]["similarity"] == 1.0
    assert all(x["review_status"] == "NEEDS_REVIEW" for x in result)
    spring = search_geometry(tmp_path, spring_candidates=True)
    assert {x["solid_id"] for x in spring} == {"main:reference", "main:near"}
    assert all(x["candidate"] == "SPRING_CONTACT" for x in spring)


def test_reject_missing_reference_and_ambiguous_mode(tmp_path):
    prepare(tmp_path)
    with pytest.raises(ValueError, match="参考 Solid"):
        search_geometry(tmp_path, reference_id="main:missing")
    with pytest.raises(ValueError, match="只能"):
        search_geometry(tmp_path, reference_id="main:near", spring_candidates=True)
    with pytest.raises(ValueError, match="只能"):
        search_geometry(tmp_path)
