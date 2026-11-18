"""P8-B scoring checks with a mocked CAD face extractor."""
from cst_material import shield_brep


def test_shield_brep_evidence(monkeypatch):
    monkeypatch.setattr(shield_brep, "read_step", lambda path: [object()])
    from cst_material import occt_geometry

    monkeypatch.setattr(occt_geometry, "geometry_metrics", lambda solids: {
        "dimensions_mm": [2, 20, 30],
        "volume_mm3": 100,
    })
    faces = [
        {"area": 600, "normal": (0, 0, 1), "center": (0, 0, 2)},
        {"area": 580, "normal": (0, 0, -1), "center": (0, 0, 1)},
    ] + [
        {"area": 100, "normal": normal, "center": (0, 0, 0)}
        for normal in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0))
    ]
    monkeypatch.setattr(shield_brep, "_face_evidence", lambda solids: faces)
    result = shield_brep.analyze_shield_step("dummy.step")
    assert result["candidate"]
    assert result["score"] == 100
    assert result["review_status"] == "NEEDS_REVIEW"
    assert result["metrics"]["transverse_face_count"] == 4


def test_shield_brep_no_planar_faces(monkeypatch):
    monkeypatch.setattr(shield_brep, "read_step", lambda path: [object()])
    from cst_material import occt_geometry

    monkeypatch.setattr(occt_geometry, "geometry_metrics", lambda solids: {
        "dimensions_mm": [2, 20, 30],
        "volume_mm3": 100,
    })
    monkeypatch.setattr(shield_brep, "_face_evidence", lambda solids: [])
    assert not shield_brep.analyze_shield_step("dummy.step")["candidate"]


def test_axial_void_probe(monkeypatch):
    from cst_material import occt_geometry

    def classify(_solids, point, tolerance):
        assert tolerance > 0
        return "INSIDE" if point[2] > 9 else "OUTSIDE"

    monkeypatch.setattr(occt_geometry, "classify_point", classify)
    probe = shield_brep._sample_cavity_axis(
        [object()], [0, 0, 0, 10, 10, 10], (0, 0, 1)
    )
    assert probe["supported"]
    assert probe["axial_void"]
    assert probe["open_end_hint"]
    assert probe["opposite_end_material_hint"]
    assert len(probe["samples"]) == 7


def test_axial_probe_rejects_non_axis_aligned():
    probe = shield_brep._sample_cavity_axis(
        [object()], [0, 0, 0, 10, 10, 10], (0.7, 0.7, 0)
    )
    assert probe["supported"] is False
