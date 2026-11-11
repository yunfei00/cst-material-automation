"""P1/P2 CST project operations.

The installed CST Python API is imported lazily so unit tests work without CST.
Enumeration and assignment methods are supplied by an API binding verified against
the installed CST 2025 SP1 documentation. No guessed CST methods are invoked.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cst_material.domain import Material, Solid


class CSTProject(Protocol):
    def save(self, path: str, allow_overwrite: bool = False) -> object: ...
    def close(self) -> object: ...


class CSTEnvironment(Protocol):
    def open_project(self, path: str) -> CSTProject: ...


@dataclass(frozen=True)
class ProjectInventory:
    solids: tuple[Solid, ...]
    materials: tuple[Material, ...]


@dataclass(frozen=True)
class CSTBindings:
    """Version-specific functions; bind only methods verified on CST 2025 SP1."""

    read_solids: Callable[[CSTProject], tuple[Solid, ...]]
    read_materials: Callable[[CSTProject], tuple[Material, ...]]
    assign_material: Callable[[CSTProject, str, str], None]


def connect() -> CSTEnvironment:
    import cst.interface

    return cst.interface.DesignEnvironment()


def read_inventory(project: CSTProject, bindings: CSTBindings) -> ProjectInventory:
    solids = tuple(bindings.read_solids(project))
    materials = tuple(bindings.read_materials(project))
    if len({s.identifier for s in solids}) != len(solids):
        raise ValueError("Solid identifiers must be unique (include component path)")
    if len({m.name for m in materials}) != len(materials):
        raise ValueError("Material names must be unique")
    return ProjectInventory(solids=solids, materials=materials)


def open_inventory(
    environment: CSTEnvironment, source: Path, bindings: CSTBindings
) -> ProjectInventory:
    if not source.is_file():
        raise FileNotFoundError(source)
    project = environment.open_project(str(source.resolve()))
    try:
        return read_inventory(project, bindings)
    finally:
        project.close()


def assign_and_verify(
    environment: CSTEnvironment,
    source: Path,
    destination: Path,
    assignments: dict[str, str],
    bindings: CSTBindings,
) -> ProjectInventory:
    """Write a *copy* and reopen to verify; never modify the original CST project.

    Requires a separate working copy created before opening CST: this protects
    the original even if CST autosaves when executing a history command.
    """
    import shutil

    source = source.resolve()
    destination = destination.resolve()
    if source == destination:
        raise ValueError("Source and destination must differ")
    if not source.is_file():
        raise FileNotFoundError(source)
    if destination.exists():
        raise FileExistsError(destination)
    if source.suffix.lower() != ".cst" or destination.suffix.lower() != ".cst":
        raise ValueError("Both paths must end in .cst")
    if not assignments:
        raise ValueError("No material assignments supplied")
    # CST projects can have associated directories: the caller must select a
    # self-contained project or stage its dependencies in the working directory.
    shutil.copy2(source, destination)
    project = None
    try:
        project = environment.open_project(str(destination))
        inventory = read_inventory(project, bindings)
        solids = {s.identifier: s for s in inventory.solids}
        materials = {m.name for m in inventory.materials}
        missing_solids = set(assignments) - set(solids)
        missing_materials = set(assignments.values()) - materials
        if missing_solids or missing_materials:
            raise ValueError(
                f"Unknown solids: {sorted(missing_solids)}; "
                f"unknown materials: {sorted(missing_materials)}"
            )
        for solid_id, material_name in assignments.items():
            bindings.assign_material(project, solid_id, material_name)
        project.save(str(destination), allow_overwrite=True)
        project.close()
        project = None
        reopened = environment.open_project(str(destination))
        try:
            result = read_inventory(reopened, bindings)
        finally:
            reopened.close()
        observed = {s.identifier: s.material for s in result.solids}
        mismatch = {
            solid_id: (material, observed.get(solid_id))
            for solid_id, material in assignments.items()
            if observed.get(solid_id) != material
        }
        if mismatch:
            raise RuntimeError(f"Material verification failed: {mismatch}")
        return result
    finally:
        if project is not None:
            project.close()
