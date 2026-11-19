"""Pure geometry helpers for independent screw scanning."""
from cst_material.phillips_screw_recognition import _axis_key, _dot, _unit


def test_axis_is_orientation_invariant():
    assert _axis_key((0, 0, 1)) == _axis_key((0, 0, -1))
    assert _axis_key((1, 0, 0)) == _axis_key((-1, 0, 0))


def test_axis_is_normalized():
    assert _axis_key((0, 0, 10)) == (0.0, 0.0, 1.0)
    assert _unit((0, 0, 0)) is None
    assert _dot((1, 0, 0), (0, 1, 0)) == 0
