"""Structure samples V1: recognition-only, no CST or material dependency."""
import pytest

from cst_material.structure_samples import (
    extract_shape_features,
    load_samples,
    save_confirmed_sample,
    search_similar_samples,
)


def metrics(dims=(0.2, 2.0, 10.0)):
    return {
        "dimensions_mm": list(dims),
        "volume_mm3": 2.0,
        "surface_mm2": 45.0,
        "face_count": 12,
        "edge_count": 30,
        "surface_types": {"GeomAbs_Plane": 8, "GeomAbs_Cylinder": 4},
    }


def test_shape_features_translation_and_axis_permutation_invariant():
    a = extract_shape_features(metrics((0.2, 2, 10)))
    b = extract_shape_features(metrics((10, 0.2, 2)))
    assert a == b
    assert a["dimension_ratios"] == pytest.approx([0.02, 0.2])


def test_confirmed_samples_persist_and_retrieve(tmp_path):
    db = tmp_path / "samples.json"
    save_confirmed_sample(db, "spring-001", "SPRING_CONTACT", metrics())
    save_confirmed_sample(
        db, "bracket-001", "BRACKET", metrics((4, 4, 4)),
    )
    library = load_samples(db)
    assert len(library["samples"]) == 2
    hits = search_similar_samples(metrics((0.21, 2.05, 10.1)), library)
    assert hits[0]["part_type"] == "SPRING_CONTACT"
    assert hits[0]["similarity"] > 0.9
    with pytest.raises(ValueError, match="已存在"):
        save_confirmed_sample(db, "spring-001", "OTHER", metrics())
    assert len(load_samples(db)["samples"]) == 2


def test_unapproved_samples_never_rank():
    lib = {"samples": [
        {"sample_id": "unverified", "part_type": "SPRING_CONTACT",
         "approved": False, "features": extract_shape_features(metrics())}
    ]}
    assert search_similar_samples(metrics(), lib) == []


def test_bad_dimensions_rejected():
    with pytest.raises(ValueError):
        extract_shape_features(metrics((0, 2, 10)))
