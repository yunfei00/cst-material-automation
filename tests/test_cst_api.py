from pathlib import Path

import pytest

from cst_material.cst_api import (
    CSTBindings,
    assign_and_verify,
    open_inventory,
)
from cst_material.domain import Material, Solid


class FakeProject:
    def __init__(self, state):
        self.state = state
        self.closed = False

    def save(self, path, allow_overwrite=False):
        assert allow_overwrite
        self.state["saved"] = True

    def close(self):
        self.closed = True


class FakeEnvironment:
    def __init__(self):
        self.states = {}
        self.opened = []

    def open_project(self, path):
        self.opened.append(path)
        return FakeProject(self.states.setdefault(path, {"material": "Vacuum"}))


def bindings():
    return CSTBindings(
        read_solids=lambda p: (Solid("component:solid", "component", p.state["material"], None),),
        read_materials=lambda p: (Material("Vacuum"), Material("Copper")),
        assign_material=lambda p, solid, material: p.state.__setitem__("material", material),
    )


def test_read_inventory_closes_project(tmp_path: Path):
    src = tmp_path / "source.cst"
    src.write_bytes(b"test")
    env = FakeEnvironment()
    inventory = open_inventory(env, src, bindings())
    assert inventory.solids[0].identifier == "component:solid"
    assert len(inventory.materials) == 2


def test_assign_and_reopen(tmp_path: Path):
    src = tmp_path / "source.cst"
    src.write_bytes(b"original")
    dst = tmp_path / "result.cst"
    env = FakeEnvironment()
    result = assign_and_verify(
        env, src, dst, {"component:solid": "Copper"}, bindings()
    )
    assert result.solids[0].material == "Copper"
    assert src.read_bytes() == b"original"
    assert dst.is_file()
    assert len(env.opened) == 2


def test_never_overwrite_existing_target(tmp_path: Path):
    src = tmp_path / "source.cst"
    dst = tmp_path / "result.cst"
    src.write_bytes(b"original")
    dst.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        assign_and_verify(
            FakeEnvironment(), src, dst, {"component:solid": "Copper"}, bindings()
        )
    assert dst.read_bytes() == b"keep"


def test_reject_unknown_material_before_write(tmp_path: Path):
    src = tmp_path / "source.cst"
    src.write_bytes(b"original")
    env = FakeEnvironment()
    with pytest.raises(ValueError, match="unknown materials"):
        assign_and_verify(
            env, src, tmp_path / "result.cst", {"component:solid": "Gold"}, bindings()
        )
    assert src.read_bytes() == b"original"


def test_reject_source_destination_identity(tmp_path: Path):
    src = tmp_path / "source.cst"
    src.write_bytes(b"original")
    with pytest.raises(ValueError, match="must differ"):
        assign_and_verify(
            FakeEnvironment(), src, src, {"component:solid": "Copper"}, bindings()
        )
