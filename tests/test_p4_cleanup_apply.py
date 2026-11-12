"""R001: copy-only CST Solid.Delete verified against full catalog readback."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_p4_cleanup import samples

from cst_material import cli
from cst_material.p4_cleanup import build_cleanup_plan
from cst_material.p4_cleanup_apply import _delete_solids, apply_cleanup_plan


class FakeModel:
    def __init__(self):
        self.history = []

    def add_to_history(self, label, code):
        self.history.append((label, code))


class FakeProject:
    def __init__(self, env, path):
        self.env = env
        self.path = path
        self.model3d = FakeModel()
        self.closed = False

    def save(self, path, allow_overwrite=False):
        assert str(self.path) == str(path)
        assert allow_overwrite is True
        Path(path).write_bytes(b"new-private-simulated-model")

    def close(self):
        self.closed = True


class FakeEnv:
    def __init__(self):
        self.catalog = {
            "main:USB座": "Copper",
            "main:Sheet_PEC": "PEC",
            "main:empty_import": None,
            "auxiliary:datum_plane": "Vacuum",
            "module:alt_a": "Plastic",
            "module:alt_b": "Plastic",
            "main:antenna": "Copper",
        }
        self.opened = []

    def open_project(self, path):
        instance = FakeProject(self, path)
        self.opened.append(instance)
        return instance


def test_copy_only_apply_removes_all_zero_solids_and_preserves_original(tmp_path):
    source, inventory, geometry = samples(tmp_path)
    plan_path = tmp_path / "cleanup_plan.json"
    plan_path.write_text(
        json.dumps(build_cleanup_plan(source, inventory, geometry_report=geometry)),
        encoding="utf-8",
    )
    env = FakeEnv()
    before_source = source.read_bytes()
    dest = tmp_path / "cleaned_copy.cst"
    applied_batches = []

    def simulated_delete(project, ids, *, batch_size):
        assert project.path == dest
        applied_batches.append((ids, batch_size))
        assert ids == [
            "auxiliary:datum_plane", "main:Sheet_PEC", "main:empty_import"
        ]
        for name in ids:
            del env.catalog[name]

    result = apply_cleanup_plan(
        source, dest, plan_path, environment=env, delete_executor=simulated_delete,
        catalog_reader=lambda _project: dict(env.catalog),
    )
    assert result["status"] == "PASS"
    assert result["original_solid_count"] == 7
    assert result["rule_r001_zero_volume_count"] == 3
    assert result["deleted_solid_count"] == 3
    assert result["remaining_solid_count"] == 4
    assert source.read_bytes() == before_source
    assert dest.read_bytes() == b"new-private-simulated-model"
    assert "main:USB座" in env.catalog
    assert "main:antenna" in env.catalog
    assert len(env.opened) == 3
    assert all(project.closed for project in env.opened)
    assert len(applied_batches) == 1


def test_post_readback_mismatch_never_reports_success(tmp_path):
    source, inventory, geometry = samples(tmp_path)
    plan_path = tmp_path / "cleanup_plan.json"
    plan_path.write_text(
        json.dumps(build_cleanup_plan(source, inventory, geometry_report=geometry)),
        encoding="utf-8",
    )
    env = FakeEnv()
    before = source.read_bytes()

    def incomplete_delete(project, ids, *, batch_size):
        del env.catalog[ids[0]]
        # Intentionally fail to remove the other selected Solids.
    with pytest.raises(RuntimeError, match="回读不一致"):
        apply_cleanup_plan(
            source, tmp_path / "failed_copy.cst", plan_path,
            environment=env, delete_executor=incomplete_delete,
            catalog_reader=lambda project: dict(env.catalog),
        )
    assert source.read_bytes() == before
    assert all(project.closed for project in env.opened)


def test_apply_rejects_tampered_original_geometry_and_existing_destination(tmp_path):
    source, inventory, geometry = samples(tmp_path)
    plan_path = tmp_path / "cleanup_plan.json"
    plan_path.write_text(
        json.dumps(build_cleanup_plan(source, inventory, geometry_report=geometry)),
        encoding="utf-8",
    )
    target = tmp_path / "cleaned_copy.cst"
    target.write_bytes(b"existing-must-not-change")
    with pytest.raises(FileExistsError):
        apply_cleanup_plan(source, target, plan_path, environment=FakeEnv())
    assert target.read_bytes() == b"existing-must-not-change"

    geo = json.loads(geometry.read_text(encoding="utf-8"))
    geo["geometry"]["features"][0]["volume_project_unit3"] = 0
    geo["geometry"]["features"][0]["zero_volume"] = True
    geometry.write_text(json.dumps(geo), encoding="utf-8")
    with pytest.raises(ValueError, match="内容已变化"):
        apply_cleanup_plan(
            source, tmp_path / "fresh.cst", plan_path, environment=FakeEnv()
        )
    assert not (tmp_path / "fresh.cst").exists()


def test_delete_vba_uses_official_exact_solid_delete_with_safe_quoting():
    project = FakeProject(None, Path("copy.cst"))
    ids = ["部件:曲面", 'main:the "name"', "main:other"]
    _delete_solids(project, ids, batch_size=2)
    assert len(project.model3d.history) == 2
    first = project.model3d.history[0][1]
    second = project.model3d.history[1][1]
    assert 'Solid.Delete "部件:曲面"' in first
    assert 'Solid.Delete "main:the ""name"""' in first
    assert second == 'Solid.Delete "main:other"'
    assert "Component.Delete" not in first
    assert "ChangeMaterial" not in first


def test_cleanup_apply_cli_writes_local_report_from_stub(tmp_path, monkeypatch):
    source, inventory, geometry = samples(tmp_path)
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(
        json.dumps(build_cleanup_plan(source, inventory, geometry_report=geometry)),
        encoding="utf-8",
    )
    dest = tmp_path / "new.cst"
    report = tmp_path / "apply.json"

    def stub_apply(src, dst, plan, *, batch_size):
        assert src == source
        assert dst == dest
        assert plan == plan_file
        assert batch_size == 20
        return {
            "task": "cleanup-apply", "status": "PASS", "stage": "已验证",
            "deleted_solid_count": 3, "remaining_solid_count": 4,
        }

    monkeypatch.setattr(cli, "apply_cleanup_plan", stub_apply)
    assert cli.main([
        "cleanup-apply", "--source", str(source), "--dest", str(dest),
        "--plan", str(plan_file), "--report", str(report),
    ]) == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["status"] == "PASS"
    assert data["deleted_solid_count"] == 3
