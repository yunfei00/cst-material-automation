"""Pure geometry helpers for independent screw scanning."""
import math

from cst_material.phillips_screw_recognition import (
    _axis_key,
    _common_axis_cylinders,
    _cross_recess_profile,
    _dot,
    _identify_head_shaft,
    _screw_verdict,
    _single_head_profile,
    _thread_periodicity,
    _unit,
)


def test_axis_is_orientation_invariant():
    assert _axis_key((0, 0, 1)) == _axis_key((0, 0, -1))
    assert _axis_key((1, 0, 0)) == _axis_key((-1, 0, 0))


def test_axis_is_normalized():
    assert _axis_key((0, 0, 10)) == (0.0, 0.0, 1.0)
    assert _unit((0, 0, 0)) is None
    assert _dot((1, 0, 0), (0, 1, 0)) == 0



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


def test_head_is_largest_radius_and_shaft_is_next_smaller_coaxial():
    axis = (0.0, 0.0, 1.0)
    cylinders = [
        {"radius": 1.0, "area": 100.0, "axis_origin": (0.0, 0.0, 0.0)},
        {"radius": 3.0, "area": 5.0, "axis_origin": (0.0, 0.0, 4.0)},
        {"radius": 2.0, "area": 20.0, "axis_origin": (0.0, 0.0, 1.0)},
    ]
    head, shaft, supported = _identify_head_shaft(cylinders, axis)
    assert supported
    assert head["radius"] == 3.0
    assert shaft["radius"] == 2.0


def test_head_shaft_rejects_noncoaxial_cylinder():
    cylinders = [
        {"radius": 3.0, "area": 30.0, "axis_origin": (0.0, 0.0, 0.0)},
        {"radius": 2.0, "area": 30.0, "axis_origin": (3.0, 0.0, 0.0)},
    ]
    head, shaft, supported = _identify_head_shaft(cylinders, (0.0, 0.0, 1.0))
    assert head["radius"] == 3.0
    assert shaft is None
    assert not supported


def _cylinder(radius, origin, direction=(0.0, 0.0, 1.0)):
    return {"radius": radius, "area": 10.0,
            "axis_origin": origin, "direction": direction}


def test_all_cylinders_on_same_axis_line():
    cylinders = [
        _cylinder(3.0, (0.0, 0.0, 0.0)),
        _cylinder(2.0, (0.0, 0.0, 5.0)),
        _cylinder(1.8, (0.0, 0.0, -3.0), (0.0, 0.0, -1.0)),
    ]
    result = _common_axis_cylinders(cylinders)
    assert result["verified"]
    assert result["checked"] == 3


def test_parallel_offset_cylinder_fails_common_axis():
    result = _common_axis_cylinders([
        _cylinder(3.0, (0.0, 0.0, 0.0)),
        _cylinder(2.0, (0.2, 0.0, 4.0)),
    ])
    assert not result["verified"]
    assert result["failed_indices"] == [1]


def test_tilted_cylinder_fails_common_axis():
    result = _common_axis_cylinders([
        _cylinder(3.0, (0.0, 0.0, 0.0)),
        _cylinder(2.0, (0.0, 0.0, 4.0), (0.1, 0.0, 0.995)),
    ])
    assert not result["verified"]


def test_common_axis_accepts_small_numeric_noise():
    result = _common_axis_cylinders([
        _cylinder(3.0, (0.0, 0.0, 0.0)),
        _cylinder(2.0, (0.001, 0.0, 4.0)),
    ])
    assert result["verified"]


def test_noncoaxial_geometry_is_other_even_with_thread_and_cross():
    assert _screw_verdict(False, True, True, True, True) == "OTHER"


def test_missing_head_shaft_is_other_even_when_coaxial():
    assert _screw_verdict(True, False, True, True, True) == "OTHER"


def test_incomplete_evidence_is_other_not_review():
    assert _screw_verdict(True, True, True, False, True) == "OTHER"
    assert _screw_verdict(True, True, True, True, False) == "OTHER"
    assert _screw_verdict(True, True, False, True, True) == "OTHER"


def test_all_five_screw_criteria_confirmed():
    assert _screw_verdict(True, True, True, True, True) == "PHILLIPS_SCREW"


def _axial_points(radii):
    return [(float(i), radius * math.cos(2 * math.pi * j / 12),
             radius * math.sin(2 * math.pi * j / 12))
            for i, radius in enumerate(radii) for j in range(12)]


def test_single_head_profile_accepts_one_large_end():
    points = _axial_points([3.0] * 4 + [1.5] * 20)
    result = _single_head_profile(points, (1, 0, 0), (0, 0, 0), 3.0, 1.5)
    assert result["verified"]
    assert result["left_head"]
    assert not result["right_head"]


def test_single_head_profile_rejects_two_equal_large_ends():
    points = _axial_points([3.0] * 4 + [1.5] * 16 + [3.0] * 4)
    result = _single_head_profile(points, (1, 0, 0), (0, 0, 0), 3.0, 1.5)
    assert not result["verified"]
    assert result["left_head"] and result["right_head"]


def test_single_head_profile_rejects_uniform_rod():
    points = _axial_points([1.5] * 24)
    result = _single_head_profile(points, (1, 0, 0), (0, 0, 0), 3.0, 1.5)
    assert not result["verified"]
