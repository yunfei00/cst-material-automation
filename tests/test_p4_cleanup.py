"""P4-C clean-up: review-only rules, zero-volume classification and safety."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cst_material import cli
from cst_material.p4_cleanup import build_cleanup_plan, validate_plan_for_apply


def samples(tmp_path: Path):
    source = tmp_path / "model.cst"
    source.write_bytes(b"synthetic-file-not-cst-data")
    names = (
        "main:USB座",
        "main:Sheet_PEC",
        "main:empty_import",
        "auxiliary:datum_plane",
        "module:alt_a",
        "module:alt_b",
        "main:antenna",
    )
    materials = ("Copper", "PEC", None, "Vacuum", "Plastic", "Plastic", "Copper")
    inventory = tmp_path / "P1_Report.json"
    inventory.write_text(
        json.dumps({
            "task": "inventory", "status": "PASS",
            "source": str(source.resolve()), "solid_count": len(names),
            "solids": [
                {"identifier": name, "material": material}
                for name, material in zip(names, materials, strict=True)
            ],
        }, ensure_ascii=False), encoding="utf-8",
    )
    geometry = tmp_path / "P3A_Report.json"
    volume_area = ((2, 10), (0, 18), (0, 0), (0, 2), (4, 4), (4, 4), (2, 3))
    geometry.write_text(
        json.dumps({
            "task": "geometry", "status": "PASS",
            "source": str(source.resolve()), "solid_count": len(names),
            "zero_volume_count": 3,
            "geometry": {
                "length_unit": "mm", "length_unit_to_m": 0.001,
                "features": [
                    {
                        "identifier": name, "material": material,
                        "volume_project_unit3": vol,
                        "area_project_unit2": area,
                        "zero_volume": vol == 0,
                    }
                    for name, material, (vol, area)
                    in zip(names, materials, volume_area, strict=True)
                ],
            },
        }, ensure_ascii=False), encoding="utf-8",
    )
    return source, inventory, geometry


def test_zero_volume_is_review_only_and_never_auto_deleted(tmp_path):
    source, inventory, geometry = samples(tmp_path)
    plan = build_cleanup_plan(source, inventory, geometry_report=geometry)
    assert plan["status"] == "PASS"
    assert plan["source_solid_count"] == 7
    assert plan["delete_count"] == 0
    assert plan["expected_remaining_count"] == 7
    assert plan["zero_volume_review_count"] == 3
    assert plan["zero_volume_positive_area_count"] == 2
    assert plan["zero_volume_zero_area_count"] == 1
    hints = {r["identifier"]: r for r in plan["review_suggestions"]}
    assert hints["main:Sheet_PEC"]["zero_volume_review"][
        "zero_volume_review_class"
    ] == "ZERO_VOLUME_POSITIVE_AREA_KEEP_REVIEW"
    assert hints["main:empty_import"]["zero_volume_review"][
        "zero_volume_review_class"
    ] == "ZERO_VOLUME_ZERO_AREA_DIAGNOSE"
    assert hints["main:USB座"]["sensitive_keep_by_default"] is True
    assert not any(r["decision"] == "DELETE" for r in plan["review_suggestions"])


def test_explicit_variant_group_keeps_chosen_member_and_keeps_usb(tmp_path):
    source, inventory, geometry = samples(tmp_path)
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        json.dumps({
            "schema_version": 1,
            "confirmed_delete_ids": ["auxiliary:datum_plane"],
            "protected_ids": ["main:USB座", "main:antenna"],
            "variant_groups": [{
                "group_id": "group_1",
                "members": ["module:alt_a", "module:alt_b"],
                "keep": "module:alt_b",
            }],
        }, ensure_ascii=False), encoding="utf-8",
    )
    plan = build_cleanup_plan(source, inventory, rules_path, geometry)
    assert plan["delete_count"] == 2
    assert plan["delete_ids"] == ["auxiliary:datum_plane", "module:alt_a"]
    assert "module:alt_b" in plan["protected_ids"]
    assert "main:USB座" not in plan["delete_ids"]
    assert "main:Sheet_PEC" not in plan["delete_ids"]
    assert validate_plan_for_apply(plan, source)[0] == plan["delete_ids"]


@pytest.mark.parametrize(
    ("bad_rules", "match"),
    [
        ({"confirmed_delete_ids": ["unknown:solid"]}, "不存在"),
        ({"confirmed_delete_ids": ["main:USB座"], "protected_ids": ["main:USB座"]}, "同时"),
        ({
            "variant_groups": [{
                "group_id": "v1", "members": ["module:alt_a", "module:alt_b"],
                "keep": "unknown:solid",
            }],
        }, "keep"),
    ],
)
def test_invalid_or_conflicting_rules_fail_closed(tmp_path, bad_rules, match):
    source, inventory, geometry = samples(tmp_path)
    file = tmp_path / "rules.json"
    rules = {
        "schema_version": 1, "confirmed_delete_ids": [],
        "protected_ids": [], "variant_groups": [],
    }
    rules.update(bad_rules)
    file.write_text(json.dumps(rules), encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        build_cleanup_plan(source, inventory, file, geometry)


def test_mismatched_geometry_material_or_zero_volume_flag_rejected(tmp_path):
    source, inventory, geometry = samples(tmp_path)
    payload = json.loads(geometry.read_text(encoding="utf-8"))
    payload["geometry"]["features"][0]["material"] = "Unknown"
    geometry.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="Material"):
        build_cleanup_plan(source, inventory, geometry_report=geometry)
    payload["geometry"]["features"][0]["material"] = "Copper"
    payload["geometry"]["features"][1]["zero_volume"] = False
    geometry.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="zero_volume"):
        build_cleanup_plan(source, inventory, geometry_report=geometry)


def test_cli_plan_does_not_call_cst_or_overwrite_original(tmp_path, monkeypatch):
    source, inventory, geometry = samples(tmp_path)
    output = tmp_path / "P4C_Preview.json"
    before = [p.read_bytes() for p in (source, inventory, geometry)]
    monkeypatch.setattr(cli, "connect", lambda: pytest.fail("Plan may not connect to CST"))
    result = cli.main([
        "cleanup-plan", "--source", str(source),
        "--inventory-report", str(inventory), "--geometry-report", str(geometry),
        "--report", str(output),
    ])
    assert result == 0
    plan = json.loads(output.read_text(encoding="utf-8"))
    assert plan["status"] == "PASS"
    assert plan["delete_count"] == 0
    assert plan["zero_volume_review_count"] == 3
    assert [p.read_bytes() for p in (source, inventory, geometry)] == before
    assert cli.main([
        "cleanup-plan", "--source", str(source),
        "--inventory-report", str(inventory), "--geometry-report", str(geometry),
        "--report", str(geometry),
    ]) == 2
    assert geometry.read_bytes() == before[2]
