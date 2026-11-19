from cst_material.shield_refinement import classify_pcb_shield


def _evidence(*, walls=0, enclosure=False, consistency=0.9, pairs=3):
    return {
        "sheet_faces": {"face_count": 12, "surface_counts": {"plane": 8, "cylinder": 4, "other": 0},
                        "plane_pair_count": pairs - 1, "cylinder_pair_count": 1,
                        "pair_consistency": consistency, "dominant_thickness_mm": 0.2},
        "brep": {"metrics": {"transverse_face_count": walls,
                            "axial_cavity_probe": {"axial_void": enclosure}}},
        "cross_section": {"wall_enclosure_hint": enclosure},
        "wall_paths": {"material_path_count": 2},
    }


def test_flat_contact_excluded_despite_uniform_sheet():
    result = classify_pcb_shield({"name_hit": False}, _evidence(walls=1))
    assert result["verdict"] == "CONTACT_OR_OTHER"


def test_named_contact_kept_for_review_not_promoted():
    result = classify_pcb_shield({"name_hit": True}, _evidence(walls=1))
    assert result["verdict"] == "NAMED_REVIEW"


def test_enclosed_sheet_supported():
    result = classify_pcb_shield({"name_hit": False}, _evidence(walls=5, enclosure=True))
    assert result["verdict"] == "SHIELD_REVIEW_STRONG"
    assert result["surface_counts"]["cylinder"] == 4


def test_enclosure_without_thickness_not_strong():
    result = classify_pcb_shield({"name_hit": False}, _evidence(walls=5, enclosure=True, consistency=0.2))
    assert result["verdict"] == "CONTACT_OR_OTHER"
