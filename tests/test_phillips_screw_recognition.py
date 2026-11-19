"""Pure geometry helpers for independent screw scanning."""
from cst_material.phillips_screw_recognition import _axis_key, _dot, _unit


def test_axis_is_orientation_invariant():
    assert _axis_key((0, 0, 1)) == _axis_key((0, 0, -1))
    assert _axis_key((1, 0, 0)) == _axis_key((-1, 0, 0))


def test_axis_is_normalized():
    assert _axis_key((0, 0, 10)) == (0.0, 0.0, 1.0)
    assert _unit((0, 0, 0)) is None
    assert _dot((1, 0, 0), (0, 1, 0)) == 0


import math

from cst_material.phillips_screw_recognition import (
    _cross_recess_profile,
    _thread_periodicity,
)


def test_thread_periodicity_rejects_constant_cylinder():
    samples = [(z / 4, 2 * math.pi * angle / 24, 2.0)
               for z in range(96) for angle in range(24)]
    assert not _thread_periodicity(samples)["verified"]


def test_cross_recess_accepts_four_arms():
    samples = [(r * math.cos(a), r * math.sin(a), 0.2)
               for arm in range(4)
               for r in (0.3, 0.5, 0.7)
               for a in [arm * math.pi / 2 + offset
                         for offset in (-0.04, 0, 0.04, 0.08)]]
    assert _cross_recess_profile(samples, head_radius=1)["verified"]


def test_cross_recess_rejects_uniform_disk():
    samples = [(r * math.cos(a), r * math.sin(a), 0.2)
               for r in (0.3, 0.5, 0.7)
               for a in [2 * math.pi * i / 120 for i in range(120)]]
    assert not _cross_recess_profile(samples, head_radius=1)["verified"]
