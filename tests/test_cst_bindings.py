from pathlib import Path

import pytest

from cst_material.cst_api import read_inventory
from cst_material.cst_bindings import (
    _vba_string,
    assign_material,
    binding,
    material_exists,
    read_assigned_materials,
    read_material_catalog,
    read_shape_catalog,
)


class FakeModel:
    def __init__(self):
        self.scripts = []

    def get_tree_items(self):
        raise AssertionError("get_tree_items must not be called")

    def add_to_history(self, header, vba):
        self.scripts.append((header, vba))


class FakeProject:
    def __init__(self):
        self.model3d = FakeModel()


def test_vba_lists_existing_solids_and_materials(monkeypatch):
    p = FakeProject()
    import cst_material.cst_bindings as module

    monkeypatch.setattr(module, "read_shape_catalog", lambda project: {
        "main:solid1": "Copper",
        "main:solid2": "Vacuum",
    })
    monkeypatch.setattr(
        module, "read_material_catalog", lambda project: ("Copper", "Vacuum")
    )
    inv = read_inventory(p, binding())
    assert [solid.identifier for solid in inv.solids] == ["main:solid1", "main:solid2"]
    assert [solid.material for solid in inv.solids] == ["Copper", "Vacuum"]
    assert [mat.name for mat in inv.materials] == ["Copper", "Vacuum"]


def test_change_material_uses_cst_vba():
    p = FakeProject()
    assign_material(p, "main:solid1", "Copper")
    assert p.model3d.scripts == [
        (
            "CST Material Automation: Assign material",
            'Solid.ChangeMaterial "main:solid1", "Copper"',
        )
    ]


def test_vba_string_rejects_unsafe_names():
    assert _vba_string('A"B') == '"A""B"'
    with pytest.raises(ValueError):
        _vba_string("bad\nname")


def test_query_checks_written_file(monkeypatch):
    p = FakeProject()

    def add_history(header, text):
        path_line = next(line for line in text.splitlines() if line.startswith("Open "))
        import re

        match = re.match(r'Open "(.+)" For Output As #fh', path_line)
        assert match
        Path(match.group(1)).write_text("Copper\nVacuum\n", encoding="utf-8")

    monkeypatch.setattr(p.model3d, "add_to_history", add_history)
    assert read_assigned_materials(p, ("main:solid1", "main:solid2")) == {
        "main:solid1": "Copper",
        "main:solid2": "Vacuum",
    }


def test_vba_empty_solid_catalog_fails_closed(monkeypatch):
    p = FakeProject()
    import cst_material.cst_bindings as module

    monkeypatch.setattr(module, "read_shape_catalog", lambda project: {})
    with pytest.raises(RuntimeError, match="Solid"):
        read_inventory(p, binding())


def test_documented_vba_shape_catalog(monkeypatch):
    p = FakeProject()

    def write_catalog(header, script):
        path_line = next(line for line in script.splitlines() if line.startswith("Open "))
        import re

        match = re.match(r'Open "(.+)" For Output As #fh', path_line)
        assert match
        assert "Solid.GetNumberOfShapes" in script
        assert "Solid.GetNameOfShapeFromIndex(i)" in script
        assert "Solid.GetMaterialNameForShape(shapeName)" in script
        Path(match.group(1)).write_text(
            "2\nmain:solid1\nCopper\nmain:solid2\nVacuum\n", encoding="utf-8"
        )

    monkeypatch.setattr(p.model3d, "add_to_history", write_catalog)
    assert read_shape_catalog(p) == {
        "main:solid1": "Copper",
        "main:solid2": "Vacuum",
    }


def test_vba_shape_catalog_rejects_partial_file(monkeypatch):
    p = FakeProject()

    def write_catalog(header, script):
        path_line = next(line for line in script.splitlines() if line.startswith("Open "))
        import re

        match = re.match(r'Open "(.+)" For Output As #fh', path_line)
        assert match
        Path(match.group(1)).write_text("2\nmain:solid1\nCopper\n", encoding="utf-8")

    monkeypatch.setattr(p.model3d, "add_to_history", write_catalog)
    with pytest.raises(RuntimeError, match="不完整"):
        read_shape_catalog(p)


def test_material_exists_via_official_vba(monkeypatch):
    p = FakeProject()

    def write_result(header, script):
        path_line = next(line for line in script.splitlines() if line.startswith("Open "))
        import re

        match = re.match(r'Open "(.+)" For Output As #fh', path_line)
        assert match
        assert 'Material.Exists("Copper")' in script
        Path(match.group(1)).write_text("True\n", encoding="utf-8")

    monkeypatch.setattr(p.model3d, "add_to_history", write_result)
    assert material_exists(p, "Copper")


def test_material_exists_rejects_invalid_query_result(monkeypatch):
    p = FakeProject()

    def write_invalid(header, script):
        path_line = next(line for line in script.splitlines() if line.startswith("Open "))
        import re

        match = re.match(r'Open "(.+)" For Output As #fh', path_line)
        assert match
        Path(match.group(1)).write_text("UNKNOWN\n", encoding="utf-8")

    monkeypatch.setattr(p.model3d, "add_to_history", write_invalid)
    with pytest.raises(RuntimeError, match="异常"):
        material_exists(p, "Copper")


def test_documented_vba_material_catalog(monkeypatch):
    p = FakeProject()

    def write_materials(header, script):
        path_line = next(line for line in script.splitlines() if line.startswith("Open "))
        import re

        match = re.match(r'Open "(.+)" For Output As #fh', path_line)
        assert match
        assert "Material.GetNumberOfMaterials" in script
        assert "Material.GetNameOfMaterialFromIndex(i)" in script
        Path(match.group(1)).write_text("2\nCopper\nVacuum\n", encoding="utf-8")

    monkeypatch.setattr(p.model3d, "add_to_history", write_materials)
    assert read_material_catalog(p) == ("Copper", "Vacuum")


def test_vba_material_catalog_rejects_incomplete_output(monkeypatch):
    p = FakeProject()

    def write_partial(header, script):
        path_line = next(line for line in script.splitlines() if line.startswith("Open "))
        import re

        match = re.match(r'Open "(.+)" For Output As #fh', path_line)
        assert match
        Path(match.group(1)).write_text("2\nCopper\n", encoding="utf-8")

    monkeypatch.setattr(p.model3d, "add_to_history", write_partial)
    with pytest.raises(RuntimeError, match="不完整"):
        read_material_catalog(p)
