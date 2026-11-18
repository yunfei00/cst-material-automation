"""Diagnostic directory viewer adapter tests."""
import json

import pytest

from cst_material.diagnostic_catalog import load_diagnostic_catalog


def test_load_successful_records_only(tmp_path):
    steps = tmp_path / "steps"
    steps.mkdir()
    (steps / "a.step").write_bytes(b"STEP" * 50)
    report = {"items": [
        {"status": "PASS", "solid_id": "main:a", "step": str(steps / "a.step")},
        {"status": "FAIL", "solid_id": "main:b", "step": str(steps / "missing.step")},
    ]}
    (tmp_path / "report.json").write_text(json.dumps(report))
    records = load_diagnostic_catalog(tmp_path)["records"]
    assert len(records) == 1
    assert records[0]["solid_id"] == "main:a"
    assert records[0]["diagnostic_export"]


def test_reject_external_path(tmp_path):
    outside = tmp_path.parent / "external.step"
    outside.write_bytes(b"STEP" * 50)
    (tmp_path / "report.json").write_text(json.dumps({
        "items": [{"status": "PASS", "solid_id": "main:a", "step": str(outside)}]
    }))
    with pytest.raises(ValueError, match="超出"):
        load_diagnostic_catalog(tmp_path)
    outside.unlink()
