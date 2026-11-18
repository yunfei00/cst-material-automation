"""Offline regression for correct per-Solid STEP export and resume."""
import json

from cst_material.all_solid_export import export_all_solids


class FakeProject:
    def close(self):
        pass


class FakeEnv:
    def open_project(self, path):
        return FakeProject()

    def close(self):
        pass


def test_per_solid_export_and_manifest(tmp_path):
    source = tmp_path / "sample.cst"
    source.write_bytes(b"source")
    attempted = []

    def export(project, items):
        name, target = items[0]
        attempted.append(name)
        target.write_bytes(b"STEP" * 50)

    catalog = {
        "root/leaf:a": "PEC", "root/leaf:b": "Vacuum",
        "root/other:c": "PEC",
    }
    kw = dict(connect_fn=FakeEnv, catalog_fn=lambda _: catalog,
              export_fn=export, geometry_fn=lambda _: [object()])
    result = export_all_solids(source, **kw)
    assert attempted == list(catalog)
    assert (result["total"], result["success"], result["failed"]) == (3, 3, 0)
    assert result["status"] == "PASS"
    assert '.Write ("root/leaf:a")' in result["items"][0]["vba"]
    folder = tmp_path / "sample.solid_export_diagnostic"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert [item["solid_id"] for item in manifest["solids"]] == list(catalog)
    assert len(list((folder / "steps").glob("*.step"))) == 3
    assert source.read_bytes() == b"source"
    resumed = export_all_solids(source, **kw)
    assert resumed["status"] == "PASS"
    assert len(attempted) == 3
    assert all(item.get("resumed") for item in resumed["items"])


def test_failure_continues_and_resume_only_failed(tmp_path):
    source = tmp_path / "sample.cst"
    source.write_bytes(b"source")
    attempts = []
    broken = True

    def export(project, items):
        name, target = items[0]
        attempts.append(name)
        if name == "root:broken" and broken:
            raise RuntimeError("bad solid")
        target.write_bytes(b"STEP" * 50)

    kw = dict(connect_fn=FakeEnv, catalog_fn=lambda _: {
        "root:good": "PEC", "root:broken": "PEC", "root:last": "PEC",
    }, export_fn=export, geometry_fn=lambda _: [object()])
    first = export_all_solids(source, **kw)
    assert first["status"] == "PARTIAL"
    assert (first["success"], first["failed"]) == (2, 1)
    assert not (tmp_path / "sample.solid_export_diagnostic" / "manifest.json").exists()
    broken = False
    second = export_all_solids(source, **kw)
    assert second["status"] == "PASS"
    assert attempts == ["root:good", "root:broken", "root:last", "root:broken"]


def test_catalog_failure_logged(tmp_path):
    source = tmp_path / "sample.cst"
    source.write_bytes(b"source")

    def broken(_):
        raise RuntimeError("catalog error")

    result = export_all_solids(source, connect_fn=FakeEnv, catalog_fn=broken)
    assert result["status"] == "FAIL"
    assert "catalog error" in result["error"]
    assert (tmp_path / "sample.solid_export_diagnostic" / "report.json").exists()
