import pytest

from cst_material.domain import BoundingBox
from cst_material.geometry import box_volume, center


def test_box_dimensions_and_center():
    box = BoundingBox(0, 2, 4, 2, 6, 10)
    assert box.dimensions == (2, 4, 6)
    assert center(box) == (1, 4, 7)
    assert box_volume(box) == 48


def test_reject_invalid_bounds():
    with pytest.raises(ValueError):
        BoundingBox(2, 0, 0, 1, 1, 1)
