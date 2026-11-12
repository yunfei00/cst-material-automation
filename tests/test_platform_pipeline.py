"""Synthetic end-to-end 5-stage workflow including CST adapter mock."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cst_material import platform_pipeline
from cst_material.cst_step_export import export_cst_solids


class FakeProject:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeEnvironment:
    def __init__(self):
        self.opened = []

    def open_project(self, path):
        project = FakeProject()
        self.opened.append(project)
        return project


def test_m3_cst_exporter_exact_id_manifest_without_original_edit(tmp_path):
    pytest.importorskip("OCP")
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    original = tmp_path / "private.cst"
    original.write_bytes(b"synthetic-CST")
    output_dir = tmp_path / "geometry"
    env = FakeEnvironment()

    def export_sample(project, requested):
        for name, path in requested:
            assert name.startswith("main:")
            writer = STEPControl_Writer()
            assert writer.Transfer(
                BRepPrimAPI_MakeBox(1.0, 2.0, 3.0).Solid(), STEPControl_AsIs
            ) == IFSelect_ReturnStatus.IFSelect_RetDone
            assert writer.Write(str(path)) == IFSelect_ReturnStatus.IFSelect_RetDone

    manifest = export_cst_solids(
        original, output_dir, environment=env,
        catalog_reader=lambda project: {
            "main:leaf_spring": "Vacuum", "main:connector_metal": "PEC"
        },
        batch_exporter=export_sample, batch_size=1,
    )
    assert original.read_bytes() == b"synthetic-CST"
    assert manifest["solid_count"] == 2
    assert {x["solid_id"] for x in manifest["solids"]} == {
        "main:leaf_spring", "main:connector_metal"
    }
    assert all(item["step"].startswith("steps/") for item in manifest["solids"])
    assert all(p.closed for p in env.opened)


def _sample_pipeline_fakes(tmp_path, monkeypatch):
    pytest.importorskip("OCP")
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    src = tmp_path / "original.cst"
    src.write_bytes(b"original-data")
    dst = tmp_path / "cleaned.cst"

    def fake_cleanup(a, b):
        assert Path(a) == src
        Path(b).write_bytes(b"after-real-cleanup-simulation")
        return {"status": "PASS", "deleted_solid_count": 1,
                "deleted_ids": ["main:zero"], "remaining_solid_count_expected": 1}

    def fake_export(a, folder):
        assert Path(a).exists()
        folder = Path(folder)
        folder.mkdir()
        step = folder / "real_shape.step"
        writer = STEPControl_Writer()
        writer.Transfer(BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Solid(), STEPControl_AsIs)
        writer.Write(str(step))
        manifest = {
            "schema_version": 1, "unit": "mm",
            "source": str(a), "source_sha256": "synthetic",
            "solids": [{
                "solid_id": "main:remaining", "material": "Vacuum",
                "step": step.name,
            }],
        }
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return {"solid_count": 1}

    monkeypatch.setattr(platform_pipeline, "clean_project", fake_cleanup)
    monkeypatch.setattr(platform_pipeline, "export_cst_solids", fake_export)
    return src, dst


def test_m5_two_path_pipeline_outputs_review_state_when_identity_unknown(
    tmp_path, monkeypatch
):
    source, output = _sample_pipeline_fakes(tmp_path, monkeypatch)
    outcome = platform_pipeline.process_project(source, output)
    assert outcome["status"] == "NEEDS_REVIEW"
    assert outcome["cleanup"]["deleted_count"] == 1
    assert outcome["needs_review_ids"] == ["main:remaining"]
    assert outcome["material_change_count"] == 0
    assert output.read_bytes() == b"after-real-cleanup-simulation"
    assert source.read_bytes() == b"original-data"
    assert output.with_suffix(".analysis.json").exists()
    assert (output.with_suffix(".geometry") / "catalog.json").is_file()


def test_m5_two_path_pipeline_applies_only_verified_materials(tmp_path, monkeypatch):
    source, output = _sample_pipeline_fakes(tmp_path, monkeypatch)
    knowledge = tmp_path / "approved_knowledge.json"
    knowledge.write_text(json.dumps({
        "schema_version": 1, "confirmed_solids": {
            "main:remaining": {
                "approved": True, "material_approved": True,
                "part_type": "USB_CONNECTOR_HOUSING",
                "material": "PEC",
            },
        }, "reference_shapes": [],
    }), encoding="utf-8")
    changed = []

    def fake_assign(a, b, mapping):
        assert Path(a).exists()
        assert Path(b) == output
        changed.append(mapping)
        shutil.copy2(a, b)

    monkeypatch.setattr(platform_pipeline, "_apply_verified_assignments", fake_assign)
    result = platform_pipeline.process_project(source, output)
    assert changed == [{"main:remaining": "PEC"}]
    assert result["status"] == "PASS"
    assert result["material_change_count"] == 1
    assert result["needs_review_ids"] == []
    assert output.exists()
    assert source.read_bytes() == b"original-data"


def test_m2_viewer_module_loadable_without_gui_dependencies():
    from cst_material.solid_viewer import main

    assert callable(main)
