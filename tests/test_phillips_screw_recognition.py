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
    _solid_axis_core,
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


def test_noncoaxial_geometry_is_other():
    assert _screw_verdict(False, True, True, True) == "OTHER"


def test_missing_head_shaft_is_other():
    assert _screw_verdict(True, False, True, True) == "OTHER"


def test_symmetric_two_ends_are_other():
    assert _screw_verdict(True, True, False, True) == "OTHER"


def test_single_head_geometry_is_confirmed_without_thread_or_cross():
    assert _screw_verdict(True, True, True, True) == "PHILLIPS_SCREW"


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


def test_annular_through_hole_rejected_even_with_single_head_envelope():
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    outer = BRepPrimAPI_MakeCylinder(1.5, 10.0).Shape()
    bore = BRepPrimAPI_MakeCylinder(0.5, 10.0).Shape()
    annulus = BRepAlgoAPI_Cut(outer, bore).Shape()
    points = _axial_points([3.0] * 4 + [1.5] * 20)
    profile = {"verified": True, "left_head": True}
    result = _solid_axis_core([annulus], points, (0, 0, 1),
                              (0, 0, 0), profile)
    assert not result["verified"]
    assert result["axis_core_inside"] == 0


def test_solid_shaft_centerline_passes():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    solid = BRepPrimAPI_MakeCylinder(1.5, 10.0).Shape()
    points = [(radius * math.cos(2 * math.pi * j / 12),
               radius * math.sin(2 * math.pi * j / 12), float(i) * 10 / 23)
              for i, radius in enumerate([3.0] * 4 + [1.5] * 20)
              for j in range(12)]
    profile = {"verified": True, "left_head": True}
    result = _solid_axis_core([solid], points, (0, 0, 1),
                              (0, 0, 0), profile)
    assert result["verified"]
    assert result["axis_core_inside"] == 3


def test_hollow_shaft_never_passes_verdict():
    assert _screw_verdict(True, True, True, False) == "OTHER"


def test_scan_streams_matches_and_checkpoints_current_run_only(tmp_path, monkeypatch):
    import json
    import sqlite3

    from cst_material import phillips_screw_recognition as module

    with sqlite3.connect(tmp_path / "geometry_features.sqlite3") as db:
        db.execute("CREATE TABLE solids (solid_id TEXT, step TEXT, step_sha256 TEXT, status TEXT)")
        db.executemany("INSERT INTO solids (solid_id, step, status) VALUES (?, ?, 'PASS')",
                       [("s1", "one.step"), ("s2", "two.step")])
    (tmp_path / "one.step").write_text("one")
    (tmp_path / "two.step").write_text("two")
    cache = tmp_path / "phillips_screw_recognition.json"
    cache.write_text(json.dumps({
        "algorithm_version": "phillips-geometry-v24",
        "results": {"stale": {"status": "PASS", "verdict": "PHILLIPS_SCREW"}}
    }))
    monkeypatch.setattr(module, "sha256_file", lambda path: path.name)
    monkeypatch.setattr(module, "recognize_step", lambda path: {
        "verdict": "PHILLIPS_SCREW" if path.name == "one.step" else "OTHER",
        "score": 100 if path.name == "one.step" else 0,
    })
    events = []

    def progress(done, total, summary, *, matched_id=None, matched_verdict=None):
        events.append((done, total, matched_id))

    result = module.scan(tmp_path, progress=progress, workers=1)
    assert result["summary"]["PHILLIPS_SCREW"] == 1
    assert (0, 2, None) in events
    assert (1, 2, None) in events
    assert (1, 2, "s1") in events
    assert (2, 2, None) in events
    saved = json.loads(cache.read_text())
    assert set(saved["results"]) == {"s1", "s2"}


def test_complete_cache_skips_rescan_and_invalidates_on_index_change(tmp_path, monkeypatch):
    import json
    import sqlite3

    from cst_material import phillips_screw_recognition as module

    with sqlite3.connect(tmp_path / "geometry_features.sqlite3") as db:
        db.execute("CREATE TABLE solids (solid_id TEXT, step TEXT, step_sha256 TEXT, status TEXT)")
        db.execute("INSERT INTO solids VALUES ('s1', 'one.step', 'abc', 'PASS')")
    (tmp_path / "one.step").write_text("one")
    monkeypatch.setattr(module, "sha256_file", lambda path: "digest")
    monkeypatch.setattr(module, "recognize_step", lambda path: {
        "verdict": "PHILLIPS_SCREW", "score": 100,
    })
    module.scan(tmp_path, workers=1)
    def forbidden(_path):
        raise AssertionError("cache must skip hashing and recognition")
    monkeypatch.setattr(module, "sha256_file", forbidden)
    monkeypatch.setattr(module, "recognize_step", forbidden)
    reused = module.scan(tmp_path, workers=1)
    assert reused["from_cache"] is True
    cached = module.complete_cached_report(tmp_path)
    assert cached is not None
    assert cached["complete"]
    assert cached["summary"]["total"] == 1
    with sqlite3.connect(tmp_path / "geometry_features.sqlite3") as db:
        db.execute("UPDATE solids SET step_sha256='changed' WHERE solid_id='s1'")
    assert module.complete_cached_report(tmp_path) is None
    saved = json.loads((tmp_path / "phillips_screw_recognition.json").read_text())
    saved["complete"] = False
    (tmp_path / "phillips_screw_recognition.json").write_text(json.dumps(saved))
    assert module.complete_cached_report(tmp_path) is None


def test_tiny_offset_recess_cylinder_does_not_fail_common_axis():
    cylinders = [
        _cylinder(3.0, (0.0, 0.0, 0.0)),
        _cylinder(1.5, (0.0, 0.0, 5.0)),
        _cylinder(0.3, (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    ]
    result = _common_axis_cylinders(cylinders)
    assert result["verified"]
    assert result["checked"] == 2
    assert result["ignored_small_cylinders"] == 1
    assert result["minimum_radius"] == 0.6


def test_structural_offset_cylinder_still_fails_common_axis():
    cylinders = [
        _cylinder(3.0, (0.0, 0.0, 0.0)),
        _cylinder(1.5, (0.0, 0.0, 5.0)),
        _cylinder(0.7, (1.0, 0.0, 0.0)),
    ]
    result = _common_axis_cylinders(cylinders)
    assert not result["verified"]
    assert result["failed_indices"] == [2]
    assert result["ignored_small_cylinders"] == 0


def test_locating_pin_requires_three_substantial_radius_levels():
    from cst_material.phillips_screw_recognition import _locating_pin_steps

    faces = [
        _cylinder(3.0, (0.0, 0.0, 0.0)),
        _cylinder(2.0, (0.0, 0.0, 2.0)),
        _cylinder(1.2, (0.0, 0.0, 5.0)),
    ]
    for face in faces:
        face["area"] = 2 * math.pi * face["radius"] * 3
    evidence = _locating_pin_steps(faces, (0, 0, 1))
    assert evidence["verified"]
    assert len(evidence["levels"]) == 3
    faces[-1]["area"] = 0.01
    assert not _locating_pin_steps(faces, (0, 0, 1))["verified"]


def test_short_real_pin_shoulder_survives_lower_threshold():
    from cst_material.phillips_screw_recognition import _locating_pin_steps

    faces = [_cylinder(r, (0, 0, 0)) for r in (3.0, 2.0, 1.2)]
    for face in faces:
        face["area"] = 2 * math.pi * face["radius"] * 0.2
    # The shortest real shoulder is 0.11 mm: previously filtered by 0.15.
    faces[-1]["area"] = 2 * math.pi * faces[-1]["radius"] * 0.11
    evidence = _locating_pin_steps(faces, (0, 0, 1))
    assert evidence["verified"]


def test_true_constant_pitch_helix_detected():
    from cst_material.phillips_screw_recognition import _helix_from_curve_points

    points = [(2 * math.cos(t), 2 * math.sin(t), 0.5 * t / (2 * math.pi))
              for t in (i * 5 * math.pi / 96 for i in range(97))]
    result = _helix_from_curve_points(points, (0, 0, 1), (0, 0, 0), 2)
    assert result["verified"]
    assert abs(result["pitch_mm"] - 0.5) < 0.01


def test_circular_edge_and_straight_shaft_are_not_threads():
    from cst_material.phillips_screw_recognition import _helix_from_curve_points

    circle = [(2 * math.cos(t), 2 * math.sin(t), 0)
              for t in (i * 5 * math.pi / 96 for i in range(97))]
    assert not _helix_from_curve_points(circle, (0, 0, 1), (0, 0, 0), 2)["verified"]
    straight = [(2, 0, i / 96) for i in range(97)]
    assert not _helix_from_curve_points(straight, (0, 0, 1), (0, 0, 0), 2)["verified"]


def test_hardware_aware_worker_count():
    from cst_material.phillips_screw_recognition import recommended_workers

    gib = 1024**3
    assert recommended_workers(8000, cpu_count=16, available_memory=16 * gib) == 8
    assert recommended_workers(8000, cpu_count=4, available_memory=16 * gib) == 3
    assert recommended_workers(8000, cpu_count=16, available_memory=3 * gib) == 2
    assert recommended_workers(2, cpu_count=16, available_memory=16 * gib) == 2
    assert recommended_workers(8000, cpu_count=1, available_memory=gib) == 1


def test_helix_edge_does_not_require_shaft_radius_ratio():
    from cst_material.phillips_screw_recognition import _helix_from_curve_points

    points = [(3 * math.cos(t), 3 * math.sin(t), t / (2 * math.pi))
              for t in [4 * math.pi * i / 96 for i in range(97)]]
    result = _helix_from_curve_points(points, (0, 0, 1), (0, 0, 0), 1.5)
    assert result["verified"]
    assert result["pitch_mm"] > 0


def test_thread_section_rejects_smooth_cylinder():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    from cst_material.phillips_screw_recognition import _brep_thread_evidence

    solid = BRepPrimAPI_MakeCylinder(2.0, 8.0).Shape()
    result = _brep_thread_evidence(
        [solid], (0, 0, 1), (0, 0, 0), 2.0,
        profile={"verified": True, "left_head": True},
    )
    assert not result["verified"]


def test_head_shaft_accepts_small_but_distinct_radius_step():
    cylinders = [
        {"radius": 2.0, "area": 5.0, "axis_origin": (0, 0, 0)},
        {"radius": 1.9, "area": 50.0, "axis_origin": (0, 0, 4)},
    ]
    _, shaft, verified = _identify_head_shaft(cylinders, (0, 0, 1))
    assert verified
    assert shaft["radius"] == 1.9


def test_thread_phase_rejects_stationary_ring():
    from cst_material.phillips_screw_recognition import _thread_phase_consistency

    points = [(2 * math.cos(t), 2 * math.sin(t), 0.0)
              for t in [4 * math.pi * i / 96 for i in range(97)]]
    sections = [[(0.0, 2.0)]] * 4
    assert not _thread_phase_consistency(
        sections, points, (0, 0, 1), (0, 0, 0),
    )["verified"]


def test_thread_phase_rejects_unrelated_sections():
    from cst_material.phillips_screw_recognition import _thread_phase_consistency

    points = [(2 * math.cos(t), 2 * math.sin(t), t / (2 * math.pi))
              for t in [4 * math.pi * i / 96 for i in range(97)]]
    sections = [[(10.0, 2.0), (11.0, 1.5)]] * 4
    assert not _thread_phase_consistency(
        sections, points, (0, 0, 1), (0, 0, 0),
    )["verified"]


def test_thread_on_head_rejected_by_axial_envelope():
    from cst_material.phillips_screw_recognition import _helix_on_shaft

    mesh = []
    for z in range(12):
        radius = 4.0 if z < 3 else 2.0
        for i in range(16):
            theta = 2 * math.pi * i / 16
            mesh.append((radius * math.cos(theta), radius * math.sin(theta), float(z)))
    head_thread = [(4 * math.cos(t), 4 * math.sin(t), t / (2 * math.pi))
                   for t in [4 * math.pi * i / 96 for i in range(97)]]
    shaft_thread = [(2 * math.cos(t), 2 * math.sin(t), 4 + t / (2 * math.pi))
                    for t in [4 * math.pi * i / 96 for i in range(97)]]
    profile = {"verified": True, "left_head": True}
    assert not _helix_on_shaft(head_thread, (0, 0, 1), (0, 0, 0), profile, mesh)
    assert _helix_on_shaft(shaft_thread, (0, 0, 1), (0, 0, 0), profile, mesh)


def test_external_material_on_cylinder_surface():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    from cst_material.phillips_screw_recognition import _external_thread_material

    solid = BRepPrimAPI_MakeCylinder(2.0, 8.0).Shape()
    points = [(2 * math.cos(i * 0.2), 2 * math.sin(i * 0.2), 4.0)
              for i in range(32)]
    result = _external_thread_material([solid], points, (0, 0, 1), (0, 0, 0))
    assert result["verified"]
    assert result["internal_samples"] == 0


def test_internal_material_on_hollow_tube():
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    from cst_material.phillips_screw_recognition import _external_thread_material

    outer = BRepPrimAPI_MakeCylinder(4.0, 8.0).Shape()
    bore = BRepPrimAPI_MakeCylinder(2.0, 8.0).Shape()
    hollow = BRepAlgoAPI_Cut(outer, bore).Shape()
    points = [(2 * math.cos(i * 0.2), 2 * math.sin(i * 0.2), 4.0)
              for i in range(32)]
    result = _external_thread_material([hollow], points, (0, 0, 1), (0, 0, 0))
    assert not result["verified"]
    assert result["internal_samples"] > 0


def test_thread_tooth_flanks_requires_repeated_crests_and_roots():
    from cst_material.phillips_screw_recognition import _thread_tooth_flanks

    helix = [(2 * math.cos(t), 2 * math.sin(t), t / (2 * math.pi))
             for t in [8 * math.pi * i / 192 for i in range(193)]]
    smooth = [[(i / 100, 2.0) for i in range(401)] for _ in range(4)]
    assert not _thread_tooth_flanks(
        smooth, helix, (0, 0, 1), (0, 0, 0), 1.0,
    )["verified"]
    teeth = [[(i / 100, 2.0 + 0.2 * math.cos(2 * math.pi * i / 100))
              for i in range(401)] for _ in range(4)]
    assert _thread_tooth_flanks(
        teeth, helix, (0, 0, 1), (0, 0, 0), 1.0,
    )["verified"]


def test_single_head_ignores_thread_crest_above_nominal_shaft():
    from cst_material.phillips_screw_recognition import _single_head_profile

    points = []
    for i in range(240):
        z = i / 20
        radius = 1.4 if z < 2 else 0.7
        for angle in (0, math.pi / 2, math.pi, 3 * math.pi / 2):
            points.append((radius * math.cos(angle), radius * math.sin(angle), z))
    result = _single_head_profile(points, (0, 0, 1), (0, 0, 0), 1.4, 0.55)
    assert result["verified"]
    assert result["left_head"] and not result["right_head"]


def test_helix_rejects_large_lens_barrel_but_accepts_shaft_crest():
    from cst_material.phillips_screw_recognition import _helix_from_curve_points

    def helix(radius):
        return [(radius * math.cos(t), radius * math.sin(t), t / (2 * math.pi))
                for t in [4 * math.pi * i / 96 for i in range(97)]]

    screw = _helix_from_curve_points(
        helix(0.7), (0, 0, 1), (0, 0, 0), 0.55, head_radius=1.4,
    )
    lens = _helix_from_curve_points(
        helix(2.465), (0, 0, 1), (0, 0, 0), 0.55, head_radius=2.8,
    )
    assert screw["verified"]
    assert not lens["verified"]


def test_join_helix_segments_reconstructs_split_edge():
    from cst_material.phillips_screw_recognition import _join_helix_segments

    helix = [(0.7 * math.cos(t), 0.7 * math.sin(t), t / (2 * math.pi))
             for t in [4 * math.pi * i / 192 for i in range(193)]]
    pieces = [helix[i:i + 33] for i in range(0, 192, 32)]
    found = _join_helix_segments(
        pieces, (0, 0, 1), (0, 0, 0), 0.55, head_radius=1.4,
    )
    assert found
    assert found[0][1]["verified"]


def test_join_helix_segments_does_not_bridge_large_gap():
    from cst_material.phillips_screw_recognition import _join_helix_segments

    helix = [(0.7 * math.cos(t), 0.7 * math.sin(t), t / (2 * math.pi))
             for t in [4 * math.pi * i / 192 for i in range(193)]]
    found = _join_helix_segments(
        [helix[:40], helix[100:140]], (0, 0, 1), (0, 0, 0),
        0.55, head_radius=1.4,
    )
    assert not found
