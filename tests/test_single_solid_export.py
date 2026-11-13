"""Standalone one-Solid exporter: mock CST, no CAD files required."""
import json
from pathlib import Path

from cst_material.single_solid_export import diagnose_one_solid


class FakeProject:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeEnvironment:
    def __init__(self):
        self.project = FakeProject()
        self.closed = False

    def open_project(self, path):
        assert Path(path).exists()
        return self.project

    def close(self):
        self.closed = True


def test_one_solid_success_and_original_untouched(tmp_path):
    source = tmp_path / "input.cst"
    source.write_bytes(b"original")
    env = FakeEnvironment()
    called = []

    def export(project, items):
        called.extend(items)
        items[0][1].write_bytes(b"STEP" * 50)

    output = tmp_path / "out"
    result = diagnose_one_solid(
        source, "main:spring", output,
        connect_fn=lambda: env,
        catalog_fn=lambda project: {"main:spring": "PEC", "main:other": "Vacuum"},
        export_fn=export,
    )
    assert result["status"] == "PASS"
    assert len(called) == 1
    assert called[0][0] == "main:spring"
    assert source.read_bytes() == b"original"
    assert (output / "selected_solid.step").stat().st_size == 200
    assert json.loads((output / "diagnostic.json").read_text())["status"] == "PASS"
    assert env.closed and env.project.closed


def test_missing_solid_does_not_export(tmp_path):
    source = tmp_path / "input.cst"
    source.write_bytes(b"original")
    output = tmp_path / "out"
    result = diagnose_one_solid(
        source, "main:Spring", output,
        connect_fn=FakeEnvironment,
        catalog_fn=lambda project: {"main:spring": "PEC"},
        export_fn=lambda project, items: (_ for _ in ()).throw(
            AssertionError("must not export")
        ),
    )
    assert result["status"] == "FAIL"
    assert any(e["stage"] == "SOLID_NOT_FOUND" for e in result["events"])
    assert not (output / "selected_solid.step").exists()


def test_vendor_failure_keeps_diagnostic(tmp_path):
    source = tmp_path / "input.cst"
    source.write_bytes(b"original")
    output = tmp_path / "out"

    def fail(project, items):
        raise RuntimeError("Not an object reference")

    result = diagnose_one_solid(
        source, "main:spring", output,
        connect_fn=FakeEnvironment,
        catalog_fn=lambda project: {"main:spring": "PEC"},
        export_fn=fail,
    )
    assert result["status"] == "FAIL"
    assert "Not an object reference" in result["error"]
    assert "Write" in json.loads(
        (output / "diagnostic.json").read_text()
    )["events"][4]["vba"]
