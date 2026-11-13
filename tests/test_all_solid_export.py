"""CST-free regression for all-Solid independent export."""
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


def test_all_solid_failure_does_not_stop_following(tmp_path):
    source = tmp_path / "sample.cst"
    source.write_bytes(b"source")
    attempted = []

    def export(project, items):
        name, target = items[0]
        attempted.append(name)
        if name == "broken:part":
            raise RuntimeError("Not an object reference")
        target.write_bytes(b"STEP" * 50)

    result = export_all_solids(
        source, connect_fn=FakeEnv,
        catalog_fn=lambda project: {
            "first:part": "Vacuum", "broken:part": "PEC", "last:part": "Vacuum"
        },
        export_fn=export,
    )
    assert attempted == ["first:part", "broken:part", "last:part"]
    assert (result["total"], result["success"], result["failed"]) == (3, 2, 1)
    assert result["status"] == "PARTIAL"
    assert result["items"][1]["error_type"] == "RuntimeError"
    assert "Write" in result["items"][1]["vba"]
    assert result["items"][2]["status"] == "PASS"
    assert source.read_bytes() == b"source"
    report = json.loads(
        (tmp_path / "sample.solid_export_diagnostic" / "report.json").read_text()
    )
    assert report["failed"] == 1
    assert len(list((tmp_path / "sample.solid_export_diagnostic" / "steps").glob("*.step"))) == 2


def test_all_solid_catalog_failure_logged(tmp_path):
    source = tmp_path / "sample.cst"
    source.write_bytes(b"source")

    def broken(project):
        raise RuntimeError("catalog error")

    result = export_all_solids(source, connect_fn=FakeEnv, catalog_fn=broken)
    assert result["status"] == "FAIL"
    assert "catalog error" in result["error"]
    assert (tmp_path / "sample.solid_export_diagnostic" / "report.json").exists()


def test_multiple_solids_in_leaf_export_once(tmp_path):
    source = tmp_path / "nested.cst"
    source.write_bytes(b"source")
    attempted = []

    def export(project, items):
        name, target = items[0]
        attempted.append(name)
        target.write_bytes(b"STEP" * 50)

    result = export_all_solids(
        source, connect_fn=FakeEnv,
        catalog_fn=lambda project: {
            "root/upper/leaf:a": "PEC",
            "root/upper/leaf:b": "Vacuum",
            "root/other:end": "PEC",
        },
        export_fn=export,
    )
    assert attempted == ["root/upper/leaf:a", "root/other:end"]
    assert result["solid_total"] == 3
    assert result["leaf_component_total"] == 2
    assert result["total"] == result["success"] == 2
    assert result["items"][0]["solid_ids"] == [
        "root/upper/leaf:a", "root/upper/leaf:b",
    ]
    tree = json.loads((tmp_path / "nested.solid_export_diagnostic" /
                       "component_tree.json").read_text())
    assert tree["components"]["root/upper"]["children"] == ["root/upper/leaf"]
