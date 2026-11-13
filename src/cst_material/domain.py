"""Stable domain types independent of CST automation details."""
from dataclasses import dataclass


@dataclass(frozen=True)
class BoundingBox:
    xmin: float
    ymin: float
    zmin: float
    xmax: float
    ymax: float
    zmax: float

    def __post_init__(self) -> None:
        if self.xmin > self.xmax or self.ymin > self.ymax or self.zmin > self.zmax:
            raise ValueError("Invalid bounding box limits")

    @property
    def dimensions(self) -> tuple[float, float, float]:
        return (self.xmax - self.xmin, self.ymax - self.ymin, self.zmax - self.zmin)


@dataclass(frozen=True)
class Solid:
    identifier: str
    component: str | None
    material: str | None
    bounds: BoundingBox | None


@dataclass(frozen=True)
class Material:
    name: str
    category: str | None = None
