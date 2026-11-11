"""Pure geometry functions; never access company CAD data implicitly."""
from cst_material.domain import BoundingBox


def box_volume(bounds: BoundingBox) -> float:
    x, y, z = bounds.dimensions
    return x * y * z


def center(bounds: BoundingBox) -> tuple[float, float, float]:
    return (
        (bounds.xmin + bounds.xmax) / 2,
        (bounds.ymin + bounds.ymax) / 2,
        (bounds.zmin + bounds.zmax) / 2,
    )
