"""Two-path CST cleaning contract tests: synthetic projects, no CST installation."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cst_material.simple_cleanup import clean_project


class MockProject:
    def __init__(self, environment, location):
        self.environment = environment
        self.location = Path(location)
        self.closed = False

    def save(self, path, allow_overwrite=False):
        assert Path(path) == self.location
        assert allow_overwrite is True
        self.location.write_bytes(b"cleaned-synthetic-CST")

    def close(self):
        self.closed = True


class MockCstEnvironment:
    def __init__(self):
        self.catalog = {
            "aux:sheet_area": "PEC",
            "body:volume_5": "Copper",
            "aux:empty": None,
            "cam:body": "Plastic",
        }
        self.opened = []

    def open_project(self, path):
        instance = MockProject(self, path)
        self.opened.append(instance)
        return instance


def mock_catalog(project):
    return dict(project.environment.catalog)


def mock_geometry(project, shapes):
    assert len(shapes) == len(project.environment.catalog)
    return SimpleNamespace(
        features=tuple(
            SimpleNamespace(
                identifier=name,
                volume_project_unit3={
                    "aux:sheet_area": 0.0,
                    "body:volume_5": 5.0,
                    "aux:empty": 0.0,
                    "cam:body": 1.0,
                }[name],
                area_project_unit2={
                    "aux:sheet_area": 40,
                    "body:volume_5": 30,
                    "aux:empty": 0,
                    "cam:body": 9,
                }[name],
            )
            for name in shapes
        )
    )


def mock_delete(project, selected, batch_size):
    assert 1 <= batch_size <= 100
    assert project.location.suffix == ".cst"
    assert sorted(selected) == ["aux:empty", "aux:sheet_area"]
    assert "body:volume_5" not in selected
    for identifier in selected:
        del project.environment.catalog[identifier]


def test_two_paths_delete_exact_zero_volume_and_write_same_name_log(tmp_path):
    source = tmp_path / "raw.cst"
    destination = tmp_path / "clean.cst"
    source.write_bytes(b"original-synthetic-CST")
    environment = MockCstEnvironment()

    result = clean_project(
        source, destination,
        environment=environment,
        catalog_reader=mock_catalog,
        geometry_reader=mock_geometry,
        delete_executor=mock_delete,
    )
    assert result["status"] == "PASS"
    assert result["original_solid_count"] == 4
    assert result["deleted_solid_count"] == 2
    assert result["remaining_solid_count_expected"] == 2
    assert result["deleted_ids"] == ["aux:empty", "aux:sheet_area"]
    assert result["all_remaining_materials_unchanged"] is True
    assert source.read_bytes() == b"original-synthetic-CST"
    assert destination.read_bytes() == b"cleaned-synthetic-CST"
    assert len(environment.opened) == 3
    assert all(x.closed for x in environment.opened)
    log = destination.with_suffix(".cleanup.json")
    assert log.is_file()
    assert json.loads(log.read_text(encoding="utf-8"))["deleted_ids"] == result["deleted_ids"]


def test_when_no_zero_solid_output_is_identical_copy(tmp_path):
    src = tmp_path / "a.cst"
    dst = tmp_path / "b.cst"
    src.write_bytes(b"original")
    environment = MockCstEnvironment()

    def positive_only(project, shapes):
        return SimpleNamespace(features=tuple(
            SimpleNamespace(identifier=name, volume_project_unit3=1.0)
            for name in shapes
        ))

    result = clean_project(
        src, dst, environment=environment,
        catalog_reader=mock_catalog, geometry_reader=positive_only,
        delete_executor=lambda *args: pytest.fail("Nothing should be deleted"),
    )
    assert result["status"] == "PASS"
    assert result["deleted_solid_count"] == 0
    assert dst.read_bytes() == src.read_bytes()
    assert len(environment.opened) == 1


def test_malformed_geometry_fails_without_creating_output(tmp_path):
    src = tmp_path / "raw.cst"
    dest = tmp_path / "new.cst"
    src.write_bytes(b"original")
    environment = MockCstEnvironment()

    def malformed(project, shapes):
        return SimpleNamespace(features=(
            SimpleNamespace(identifier="aux:empty", volume_project_unit3=float("nan")),
        ))

    with pytest.raises(RuntimeError, match="数量与当前 Solid"):
        clean_project(
            src, dest, environment=environment,
            catalog_reader=mock_catalog, geometry_reader=malformed,
            delete_executor=mock_delete,
        )
    assert not dest.exists()
    assert src.read_bytes() == b"original"
    log = json.loads(dest.with_suffix(".cleanup.json").read_text(encoding="utf-8"))
    assert log["status"] == "FAIL"


def test_failed_delete_never_returns_success_and_preserves_original(tmp_path):
    src = tmp_path / "raw.cst"
    dest = tmp_path / "new.cst"
    src.write_bytes(b"original")
    env = MockCstEnvironment()

    def partial_delete(project, selected, batch_size):
        del env.catalog[selected[0]]

    with pytest.raises(RuntimeError, match="回读不一致"):
        clean_project(
            src, dest, environment=env,
            catalog_reader=mock_catalog, geometry_reader=mock_geometry,
            delete_executor=partial_delete,
        )
    assert src.read_bytes() == b"original"
    assert dest.exists()  # failed copy deliberately retained for local diagnosis
    log = json.loads(dest.with_suffix(".cleanup.json").read_text(encoding="utf-8"))
    assert log["status"] == "FAIL"
    assert log["deleted_solid_count"] == 2
    assert all(project.closed for project in env.opened)


def test_output_cannot_overwrite_input_or_existing_file(tmp_path):
    src = tmp_path / "raw.cst"
    src.write_bytes(b"original")
    with pytest.raises(ValueError, match="不能覆盖"):
        clean_project(src, src)
    destination = tmp_path / "exists.cst"
    destination.write_bytes(b"original-existing")
    with pytest.raises(FileExistsError):
        clean_project(src, destination)
    assert src.read_bytes() == b"original"
    assert destination.read_bytes() == b"original-existing"


def test_module_does_not_read_previous_p1_p3_json_reports():
    import inspect

    import cst_material.simple_cleanup as module

    code = inspect.getsource(module.clean_project)
    assert "P1_Report.json" not in code
    assert "P3A_Geometry_Report.json" not in code
    assert "build_cleanup_plan(" not in code
    assert "validate_plan_for_apply(" not in code
    assert "geometry.features" in code
    assert "volume == 0" in code
    assert "project.save(" in code
