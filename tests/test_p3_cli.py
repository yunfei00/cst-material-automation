"""CLI-level P3-A tests, using only fake CST and disposable files."""
import json
from pathlib import Path

from cst_material import cli
from cst_material.cst_api import ProjectInventory
from cst_material.domain import Solid
from cst_material.p3_geometry import GeometryCatalog, GeometryFeature


class FakeProject:
    def __init__(self, path: str, original: Path):
        self.path = Path(path)
        self.original = original
        self.closed = False
        assert self.path != self.original
        assert self.path.read_bytes() == original.read_bytes()

    def close(self):
        self.closed = True

    def save(self, *args, **kwargs):
        raise AssertionError("P3 geometry is read-only and must never save")


class FakeEnvironment:
    def __init__(self, original: Path):
        self.original = original
        self.opened = []

    def open_project(self, path):
        project = FakeProject(path, self.original)
        self.opened.append(project)
        return project

    def close(self):
        pass


def test_geometry_cli_opens_copy_and_does_not_save(tmp_path, monkeypatch):
    source = tmp_path / "existing.cst"
    source.write_bytes(b"original-private-binary")
    report = tmp_path / "local-data" / "P3A_Geometry_Report.json"
    env = FakeEnvironment(source)
    monkeypatch.setattr(cli, "connect", lambda: env)
    monkeypatch.setattr(
        cli,
        "read_inventory",
        lambda project, bindings: ProjectInventory(
            (Solid("main:s", "main", "Copper", None),), ()
        ),
    )
    monkeypatch.setattr(
        cli,
        "read_geometry_features",
        lambda project, inventory: GeometryCatalog(
            "mm",
            0.001,
            (
                GeometryFeature(
                    "main:s", "main", "Copper", 1.0, 6.0, 1e-9,
                    6e-6, 36 * 3.141592653589793 / 216, False
                ),
            ),
        ),
    )
    status = cli.main(
        ["geometry", "--source", str(source), "--report", str(report)]
    )
    data = json.loads(report.read_text(encoding="utf-8"))
    assert status == 0
    assert data["status"] == "PASS"
    assert data["geometry"]["features"][0]["identifier"] == "main:s"
    assert data["solid_count"] == 1
    assert data["zero_volume_count"] == 0
    assert len(env.opened) == 1 and env.opened[0].closed
    assert source.read_bytes() == b"original-private-binary"
    assert not env.opened[0].path.exists()


def test_report_path_must_not_overwrite_cst(tmp_path):
    source = tmp_path / "existing.cst"
    source.write_bytes(b"original-critical-bytes")
    assert cli.main(
        ["geometry", "--source", str(source), "--report", str(source)]
    ) == 2
    assert source.read_bytes() == b"original-critical-bytes"
