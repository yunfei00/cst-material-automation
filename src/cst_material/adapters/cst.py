"""CST adapter contract.

No undocumented API methods are assumed here. A real implementation must be
validated against the locally installed CST 2025 Python or macro interface.
"""
from pathlib import Path
from typing import Protocol

from cst_material.domain import BoundingBox, Material, Solid


class CSTAdapter(Protocol):
    def open_project(self, path: Path) -> None: ...
    def list_solids(self) -> list[Solid]: ...
    def list_materials(self) -> list[Material]: ...
    def get_bounds(self, solid_id: str) -> BoundingBox | None: ...
    def assign_material(self, solid_id: str, material_name: str) -> None: ...
    def save_as(self, path: Path) -> None: ...
    def close(self) -> None: ...


class UnavailableCSTAdapter:
    """Explicitly blocked placeholder, never presented as a real CST connection."""

    def __init__(self, reason: str = "CST 2025 real adapter has not been verified") -> None:
        self.reason = reason

    def __getattr__(self, name: str) -> object:
        raise RuntimeError(f"BLOCKED: {self.reason}; requested operation: {name}")
