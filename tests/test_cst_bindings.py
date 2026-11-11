from pathlib import Path

import pytest

from cst_material.cst_api import read_inventory
from cst_material.cst_bindings import (
    _vba_string,
    assign_material,
    binding,
    read_assigned_materials,
)


class FakeModel:
    def __init__(self):
        self.scripts = []

    def get_tree_items(self):
        return [
            "Components",
            r"Components\main",
            r"Components\main\solid1",
            "Materials",
            r"Materials\Copper",
            r"Materials\Vacuum",
        ]

    def add_to_history(self, header, vba):
        self.scripts.append((header, vba))


class FakeProject:
    def __init__(self):
        self.model3d = FakeModel()


def test_tree_lists_existing_solids_and_materials():
    inv = read_inventory(FakeProject(), binding())
    assert [s.identifier for s in inv.solids] == ["main:solid1"]
    assert [m.name for m in inv.materials] == ["Copper", "Vacuum"]


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


def test_tree_empty_fails_closed():
    p = FakeProject()
    p.model3d.get_tree_items = list
    with pytest.raises(RuntimeError, match="Solid"):
        read_inventory(p, binding())
