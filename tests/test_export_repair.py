import json

from cst_material import export_repair


def test_repair_existing_surfaces_without_reexport(tmp_path, monkeypatch):
    (tmp_path / "steps").mkdir()
    for name in ("solid.step", "sheet.step"):
        (tmp_path / "steps" / name).write_bytes(b"STEP" * 50)
    report = {
        "mode": "PER_SOLID_COLON_V2", "source": "original.cst",
        "source_sha256": "original-hash",
        "items": [
            {"solid_id": "a:solid", "material": "PEC", "step": "steps/solid.step",
             "status": "PASS", "sha256": export_repair.sha256_file(
                 tmp_path / "steps" / "solid.step")},
            {"solid_id": "b:sheet", "material": "PEC", "step": "steps/sheet.step",
             "status": "FAIL", "error": "STEP 未包含封闭 Solid"},
        ],
    }
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(export_repair, "inspect_existing_step",
                        lambda path: ("PASS_SURFACE", 2))
    preview = export_repair.repair_export(tmp_path, dry_run=True)
    assert preview["surface"] == 1
    assert not (tmp_path / "manifest.json").exists()
    assert json.loads((tmp_path / "report.json").read_text())["items"][1]["status"] == "FAIL"
    result = export_repair.repair_export(tmp_path)
    assert result["failed"] == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["solid_count"] == 1
    assert manifest["excluded_surface_count"] == 1
    assert [item["solid_id"] for item in manifest["solids"]] == ["a:solid"]
    updated = json.loads((tmp_path / "report.json").read_text())
    assert updated["items"][1]["status"] == "PASS_SURFACE"
    assert updated["items"][1]["body_count"] == 0
    assert list(tmp_path.glob("report.json.backup-*"))


def test_repair_missing_step_stays_failed(tmp_path):
    report = {"mode": "PER_SOLID_COLON_V2", "items": [
        {"solid_id": "a:missing", "status": "FAIL", "step": "steps/missing.step"}
    ]}
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    result = export_repair.repair_export(tmp_path, dry_run=True)
    assert result["failed"] == 1
    assert result["recovered"] == 0
