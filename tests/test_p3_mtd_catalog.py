"""P3-B external MTD catalog tests. Never parse or modify MTD bytes."""
import hashlib
import json
from pathlib import Path

import pytest

from cst_material import cli
from cst_material.p3_mtd_catalog import scan_mtd_library


def test_scan_mtd_metadata_only_and_duplicate_candidates(tmp_path: Path):
    root = tmp_path / "external-library"
    a = root / "Materials" / "Copper.mtd"
    b = root / "another" / "copper.MTD"
    a.parent.mkdir(parents=True)
    b.parent.mkdir(parents=True)
    a.write_bytes(b"\xff\x00 PROPRIETARY MTD \xb2")
    b.write_bytes(b"second")
    (root / "Materials" / "index.mal").write_text("unrelated", encoding="utf-8")

    result = scan_mtd_library(root)
    assert len(result.entries) == 2
    entries = {row.relative_path: row for row in result.entries}
    row = entries["Materials/Copper.mtd"]
    assert row.candidate_name == "Copper"
    assert row.name_verified is False
    assert row.properties_verified is False
    assert row.size_bytes == len(b"\xff\x00 PROPRIETARY MTD \xb2")
    assert row.sha256 == hashlib.sha256(a.read_bytes()).hexdigest()
    assert result.duplicate_candidate_names == {
        "copper": ["another/copper.MTD", "Materials/Copper.mtd"]
    }
    assert a.read_bytes() == b"\xff\x00 PROPRIETARY MTD \xb2"
    assert b.read_bytes() == b"second"


def test_scan_requires_real_mtd_files(tmp_path: Path):
    (tmp_path / "ignored.txt").write_text("nothing", encoding="utf-8")
    with pytest.raises(RuntimeError, match=".mtd"):
        scan_mtd_library(tmp_path)


def test_scan_mtd_cli_writes_only_local_report(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    (root / "test.mtd").write_bytes(b"\x00\x80\xff")
    report = tmp_path / "local-data" / "P3B_MTD_Catalog.json"
    code = cli.main(
        ["scan-mtd", "--library-root", str(root), "--report", str(report)]
    )
    data = json.loads(report.read_text(encoding="utf-8"))
    assert code == 0
    assert data["status"] == "PASS"
    assert data["mtd_count"] == 1
    assert data["files"][0]["properties_verified"] is False
    assert data["files"][0]["candidate_name"] == "test"
    assert (root / "test.mtd").read_bytes() == b"\x00\x80\xff"


def test_scan_refuses_report_inside_material_library(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    (root / "test.mtd").write_bytes(b"test")
    report = root / "report.json"
    assert cli.main(
        ["scan-mtd", "--library-root", str(root), "--report", str(report)]
    ) == 2  # explicit safety refusal before any report is written
    assert not report.exists()
