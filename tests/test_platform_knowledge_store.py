"""Explicit GUI approval persistence and source-project identity isolation."""
from __future__ import annotations

import json

import pytest

from cst_material import knowledge_store
from cst_material.material_matching import match_materials
from cst_material.solid_recognition import recognize_catalog


def catalog(source_hash: str) -> dict:
    return {"original_source_sha256": source_hash, "records": [{
        "solid_id": "main:Spring1",
        "material": "Vacuum",
        "features": {
            "step_sha256": "geometry-fingerprint",
            "dimensions_mm": [0.2, 2.0, 10.0],
            "surface_types": {"GeomAbs_Plane": 8, "GeomAbs_Cylinder": 4},
            "face_count": 12,
        },
    }]}


def test_approval_tied_to_exact_original_cst_and_material_separately(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(knowledge_store, "load_catalog", lambda root, verify_files=False: catalog("project-sha-A"))
    db = tmp_path / "approved_knowledge.json"
    saved = knowledge_store.store_confirmation(
        tmp_path, "main:Spring1", "SPRING_CONTACT", knowledge_path=db,
    )
    assert saved == db
    kb = json.loads(db.read_text(encoding="utf-8"))
    record = kb["confirmed_solids"]["main:Spring1"]
    assert record["approved"] is True
    assert record["material_approved"] is False
    assert record["source_sha256"] == "project-sha-A"
    matched = recognize_catalog(catalog("project-sha-A"), kb)
    assert matched[0]["status"] == "CONFIRMED"
    assert match_materials(catalog("project-sha-A"), matched, kb)["assignments"] == {}
    # Same CST Solid ID in another model is not trusted!
    in_other_project = recognize_catalog(catalog("project-sha-B"), kb)
    assert in_other_project[0]["status"] != "CONFIRMED"

    knowledge_store.store_confirmation(
        tmp_path, "main:Spring1", "SPRING_CONTACT",
        material="Copper", approve_material=True, knowledge_path=db,
    )
    revised = json.loads(db.read_text(encoding="utf-8"))
    approved = recognize_catalog(catalog("project-sha-A"), revised)
    assert match_materials(catalog("project-sha-A"), approved, revised)["assignments"] == {
        "main:Spring1": "Copper"
    }


def test_material_approval_requires_explicit_name(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge_store, "load_catalog", lambda root, verify_files=False: catalog("sha"))
    with pytest.raises(ValueError, match="材料名称"):
        knowledge_store.store_confirmation(
            tmp_path, "main:Spring1", "SPRING_CONTACT", approve_material=True,
            knowledge_path=tmp_path / "approved.json",
        )
    with pytest.raises(ValueError, match="不在当前"):
        knowledge_store.store_confirmation(
            tmp_path, "other:missing", "SPRING_CONTACT",
            knowledge_path=tmp_path / "approved.json",
        )
