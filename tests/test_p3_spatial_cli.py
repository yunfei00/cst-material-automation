"""P3-C command orchestration tests; fake COM, never touches real CST."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cst_material import cli
from cst_material import p3_spatial
from cst_material.p3_spatial import COM_RUNNER, MARKER


def test_com_runner_never_quits_global_cst():
    assert "New-Object -ComObject 'CSTStudio.Application.2025'" in COM_RUNNER
    assert "$project.RunScript" in COM_RUNNER
    assert "$project.Quit()" in COM_RUNNER
    assert "$app.Quit()" not in COM_RUNNER
    assert "AddToHistory" not in COM_RUNNER


@pytest.mark.skipif(os.name != "nt", reason="PowerShell COM path is Windows-only")
def test_com_probe_uses_disposable_copy_without_overwriting_source(tmp_path, monkeypatch):
    original = tmp_path / "Existing.cst"
    original.write_bytes(b"private-cst-binary-payload")
    called = []

    def pretend_subprocess(cmd, **kwargs):
        env = kwargs["env"]
        copy = Path(env["CST_P3_SPATIAL_COPY"])
        script = Path(env["CST_P3_SPATIAL_MACRO"])
        assert copy != original
        assert copy.read_bytes() == original.read_bytes()
        assert "GetLooseBoundingBoxOfShape" in script.read_text(encoding="mbcs")
        assert "powershell.exe" == cmd[0]
        assert kwargs["timeout"] == 240
        (copy.parent / "spatial-result.txt").write_text(
            "\n".join([
                MARKER, "mm", "0.001", "1",
                "main:solid_1", "Copper", "OK",
                "-1", "2", "1", "6", "0", "10",
            ]) + "\n",
            encoding="mbcs",
        )
        called.append(copy)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(p3_spatial.subprocess, "run", pretend_subprocess)
    result = p3_spatial.query_spatial_from_copy(original)
    assert result.declared_solid_count == 1
    assert result.features[0].material == "Copper"
    assert len(called) == 1 and not called[0].exists()
    assert original.read_bytes() == b"private-cst-binary-payload"


def test_cli_writes_spatial_report_from_stub_without_real_cst(tmp_path, monkeypatch):
    project = tmp_path / "model.cst"
    project.write_bytes(b"source")
    report = tmp_path / "local-data" / "P3C_Spatial_Report.json"
    rows = [
        MARKER, "mm", "0.001", "1", "main:solid", "Copper", "OK",
        "0", "3", "0", "2", "0", "1",
    ]
    monkeypatch.setattr(
        cli,
        "query_spatial_from_copy",
        lambda source, timeout: p3_spatial.parse_spatial_data(rows),
    )
    rc = cli.main([
        "spatial", "--source", str(project),
        "--report", str(report), "--neighbors", "3",
    ])
    data = json.loads(report.read_text(encoding="utf-8"))
    assert rc == 0
    assert data["status"] == "PASS"
    assert data["spatial"]["solid_count"] == 1
    assert data["spatial"]["features"][0]["dimensions_project_unit"] == [3, 2, 1]
    assert "不证明实体接触" in data["spatial"]["interpretation"]
    assert project.read_bytes() == b"source"


def test_cli_partial_report_when_cst_bbox_is_missing(tmp_path, monkeypatch):
    project = tmp_path / "model.cst"
    project.write_bytes(b"source")
    report = tmp_path / "local-data" / "P3C_Spatial_Report.json"
    monkeypatch.setattr(
        cli,
        "query_spatial_from_copy",
        lambda source, timeout: p3_spatial.parse_spatial_data(
            [MARKER, "cm", "0.01", "1", "main:solid", "Copper", "MISSING"]
        ),
    )
    assert cli.main(["spatial", "--source", str(project), "--report", str(report)]) == 1
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["status"] == "PARTIAL"
    assert data["spatial"]["missing_bbox_count"] == 1
    assert data["spatial"]["features"][0]["loose_bounds"] is None
