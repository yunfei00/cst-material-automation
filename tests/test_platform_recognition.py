"""M4 identity and M5 material policy tests: UNKNOWN never gets guessed."""
from __future__ import annotations

import pytest

from cst_material.material_matching import match_materials
from cst_material.solid_recognition import recognize_catalog, shape_candidate


def _catalog():
    return {"records": [
        {"solid_id": "assembly:spring", "material": "Vacuum", "features": {
            "step_sha256": "spring-sha", "dimensions_mm": [0.25, 2.0, 12.0],
            "face_count": 16, "surface_types": {"GeomAbs_Plane": 8, "GeomAbs_Cylinder": 8},
        }},
        {"solid_id": "assembly:plate", "material": "Vacuum", "features": {
            "step_sha256": "plate-sha", "dimensions_mm": [0.2, 3.0, 15.0],
            "face_count": 6, "surface_types": {"GeomAbs_Plane": 6},
        }},
    ]}


def test_m4_geometry_shape_is_not_real_part_identity():
    catalog = _catalog()
    results = recognize_catalog(catalog, {
        "schema_version": 1, "confirmed_solids": {}, "reference_shapes": []
    })
    assert results[0]["status"] == "CANDIDATE"
    assert results[0]["candidate"] == "SPRING_CONTACT"
    assert results[0]["identity"] == "UNKNOWN"
    assert results[1]["status"] == "UNKNOWN"
    assert results[1]["identity"] == "UNKNOWN"
    assert shape_candidate(catalog["records"][1]["features"])["candidate"] is None


def test_m5_material_only_from_approved_cst_solid_or_reference():
    catalog = _catalog()
    kb = {
        "schema_version": 1,
        "confirmed_solids": {
            "assembly:spring": {
                "approved": True, "material_approved": True,
                "part_type": "SPRING_CONTACT",
                "material": "PhosphorBronze",
            },
        },
        "reference_shapes": [],
    }
    recognized = recognize_catalog(catalog, kb)
    assert recognized[0]["status"] == "CONFIRMED"
    assert recognized[1]["status"] == "UNKNOWN"
    plan = match_materials(catalog, recognized, kb)
    assert plan["assignments"] == {"assembly:spring": "PhosphorBronze"}
    assert plan["unknown_ids"] == ["assembly:plate"]
    assert plan["approved_count"] == 1
    assert plan["needs_review_count"] == 1


def test_m5_unapproved_labels_and_incorrect_material_approvals_blocked():
    catalog = _catalog()
    kb = {
        "schema_version": 1, "confirmed_solids": {
            "assembly:spring": {
                "approved": True, "part_type": "SPRING_CONTACT",
                "material": "Copper", "material_approved": False,
            }
        }, "reference_shapes": [],
    }
    recognized = recognize_catalog(catalog, kb)
    planned = match_materials(catalog, recognized, kb)
    assert planned["assignments"] == {}
    assert planned["needs_review_count"] == 2


def test_m4_verified_reference_exact_hash_can_match_part():
    catalog = _catalog()
    kb = {
        "schema_version": 1, "confirmed_solids": {}, "reference_shapes": [
            {"approved": True, "step_sha256": "spring-sha",
             "part_type": "SPRING_CONTACT", "material": "SpringAlloy",
             "material_approved": True}
        ],
    }
    recognized = recognize_catalog(catalog, kb)
    assert recognized[0]["identity"] == "SPRING_CONTACT"
    assert recognized[0]["match_method"] == "EXACT_APPROVED_REFERENCE_SHA256"
    assert match_materials(catalog, recognized, kb)["assignments"] == {
        "assembly:spring": "SpringAlloy"
    }


def test_m4_duplicate_reference_with_conflicting_label_rejected():
    catalog = _catalog()
    kb = {
        "schema_version": 1, "confirmed_solids": {}, "reference_shapes": [
            {"approved": True, "step_sha256": "spring-sha", "part_type": "X"},
            {"approved": True, "step_sha256": "spring-sha", "part_type": "Y"},
        ],
    }
    with pytest.raises(ValueError, match="冲突"):
        recognize_catalog(catalog, kb)
