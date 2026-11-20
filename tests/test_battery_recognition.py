"""Battery geometry recognizer: conservative verdict and cached parallel scan."""
import json
import sqlite3

from cst_material import battery_recognition as battery


def test_shape_accepts_flat_filled_rectangular_body():
    assert battery.shape_verdict(volume=10000, dimensions=[100, 50, 2],
                                 fill=1.0) == "BATTERY_SHAPE"


def test_shape_rejects_hollow_frame():
    assert battery.shape_verdict(volume=1000, dimensions=[100, 50, 2],
                                 fill=0.1) == "OTHER"


def test_shape_rejects_rod_and_extremely_thin_sheet():
    assert battery.shape_verdict(volume=100, dimensions=[100, 2, 2],
                                 fill=0.9) == "OTHER"
    assert battery.shape_verdict(volume=100, dimensions=[100, 50, 0.1],
                                 fill=0.9) == "OTHER"


def test_multiple_bodies_remain_unconfirmed():
    assert battery.shape_verdict(volume=100, dimensions=[100, 50, 2],
                                 fill=0.9, solid_count=2) == "UNKNOWN"


def test_scan_cache_and_failure_preserved(tmp_path, monkeypatch):
    db = tmp_path / "geometry_features.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE solids (solid_id TEXT, step TEXT, status TEXT, features_json TEXT, step_sha256 TEXT)")
        conn.executemany("INSERT INTO solids VALUES (?,?,?,?,?)", [
            ("battery-a", "a.step", "PASS", '{"volume_mm3":10000}', "a.step"),
            ("broken-b", "b.step", "PASS", '{"volume_mm3":9000}', "b.step")])
    (tmp_path / "a.step").write_text("a")
    (tmp_path / "b.step").write_text("b")
    monkeypatch.setattr(battery, "source_signature", lambda _: ("signature", 2))
    monkeypatch.setattr(battery, "sha256_file", lambda p: p.name)
    def fake(task):
        sid, _, digest = task
        if sid == "broken-b":
            return sid, {"status": "FAIL", "sha256": digest, "error": "invalid STEP"}
        return sid, {"status": "PASS", "sha256": digest, "verdict": "BATTERY_SHAPE"}
    monkeypatch.setattr(battery, "_one", fake)
    events = []
    result = battery.scan(tmp_path, workers=1, progress=events.append)
    assert result["summary"]["matched"] == 1
    assert result["summary"]["failed"] == 1
    report = json.loads((tmp_path / "battery_recognition.json").read_text(encoding="utf-8"))
    assert report["complete"]
    assert any(e["event"] == "match" for e in events)
    assert battery.complete_cached_report(tmp_path) is not None
    assert battery.scan(tmp_path, workers=1)["from_cache"]


def test_cache_invalidated_by_source_signature(tmp_path, monkeypatch):
    (tmp_path / "battery_recognition.json").write_text(json.dumps({
        "algorithm_version": battery.VERSION, "complete": True,
        "source_signature": "old", "summary": {"total": 0}, "results": {},
    }))
    monkeypatch.setattr(battery, "source_signature", lambda _: ("new", 0))
    assert battery.complete_cached_report(tmp_path) is None


def test_oriented_dimensions_on_real_occt_box():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    shape = BRepPrimAPI_MakeBox(100.0, 50.0, 5.0).Solid()
    dimensions = battery._oriented_dimensions([shape])
    assert len(dimensions) == 3
    assert dimensions[0] > dimensions[1] > dimensions[2]
    assert all(value > 0 for value in dimensions)


def test_top_ten_and_single_result_without_spatial_evidence(tmp_path, monkeypatch):
    db = tmp_path / "geometry_features.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE solids (solid_id TEXT, step TEXT, status TEXT, features_json TEXT, step_sha256 TEXT)")
        for i in range(15):
            name = f"solid-{i:02d}"
            (tmp_path / f"{name}.step").write_text(name)
            conn.execute("INSERT INTO solids VALUES (?,?,?,?,?)",
                         (name, f"{name}.step", "PASS",
                          json.dumps({"volume_mm3": 15000 - i * 1000}), name))
    monkeypatch.setattr(battery, "source_signature", lambda _: ("signature-15", 15))
    called = []
    def fake(task):
        sid, _, digest = task
        called.append(sid)
        return sid, {"status": "PASS", "sha256": digest, "verdict": "BATTERY_SHAPE",
                     "fill_ratio": 0.85, "length_width_ratio": 1.8,
                     "flatness": 0.10, "volume_mm3": 1000}
    monkeypatch.setattr(battery, "_one", fake)
    events = []
    result = battery.scan(tmp_path, workers=1, progress=events.append)
    assert len(called) == 15
    assert any(e.get("event") == "progress" and e.get("total") == 15 for e in events)
    assert result["summary"]["matched"] == 1
    report = json.loads((tmp_path / "battery_recognition.json").read_text(encoding="utf-8"))
    assert len(report["results"]) == 15
    assert sum(rec.get("verdict") == "BATTERY_SHAPE"
               for rec in report["results"].values()) == 1


def _cell(x, y, *, thickness=5, normal=(0, 0, 1)):
    return {"status": "PASS", "verdict": "BATTERY_SHAPE",
            "obb_dimensions_mm": [80, 40, thickness],
            "plane_normal": list(normal),
            "bbox_mm": [x, y, 0, x + 80, y + 40, thickness],
            "fill_ratio": 0.9, "length_width_ratio": 2,
            "flatness": 0.08, "volume_mm3": 16000}


def test_dual_cell_pair_adjacent_and_coplanar():
    assert battery.compatible_pair(_cell(0, 0), _cell(0, 43))


def test_dual_cell_rejects_distant_and_stacked_and_mismatched():
    assert not battery.compatible_pair(_cell(0, 0), _cell(0, 100))
    assert not battery.compatible_pair(_cell(0, 0), _cell(0, 43, thickness=12))
    assert not battery.compatible_pair(_cell(0, 0), _cell(0, 43, normal=(0, 1, 0)))


def test_scan_selects_two_adjacent_cells(tmp_path, monkeypatch):
    db = tmp_path / "geometry_features.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE solids (solid_id TEXT, step TEXT, status TEXT, features_json TEXT, step_sha256 TEXT)")
        for i in range(3):
            name = f"cell-{i}"
            (tmp_path / f"{name}.step").write_text(name)
            conn.execute("INSERT INTO solids VALUES (?,?,?,?,?)",
                         (name, f"{name}.step", "PASS",
                          json.dumps({"volume_mm3": 16000 - i}), name))
    monkeypatch.setattr(battery, "source_signature", lambda _: ("dual-signature", 3))
    def fake(task):
        sid, _, digest = task
        i = int(sid[-1])
        rec = _cell(0, 43 * i if i < 2 else 200)
        if i == 2:
            rec["fill_ratio"] = 0.75
        return sid, {"sha256": digest, **rec}
    monkeypatch.setattr(battery, "_one", fake)
    result = battery.scan(tmp_path)
    assert result["summary"]["matched"] == 2
    assert result["summary"]["cell_configuration"] == "dual"
    report = json.loads((tmp_path / "battery_recognition.json").read_text(encoding="utf-8"))
    assert {sid for sid, rec in report["results"].items()
            if rec.get("verdict") == "BATTERY_SHAPE"} == {"cell-0", "cell-1"}


def test_cavity_profile_detects_recess_but_not_solid_box():
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    outer = BRepPrimAPI_MakeBox(100, 60, 10).Solid()
    recess = BRepPrimAPI_MakeBox(gp_Pnt(20, 15, 5),
                                gp_Pnt(80, 45, 11)).Solid()
    hollowed = BRepAlgoAPI_Cut(outer, recess).Shape()
    bbox = [0, 0, 0, 100, 60, 10]
    plain = battery.cavity_profile([outer], bbox)
    pocket = battery.cavity_profile([hollowed], bbox)
    assert plain["status"] == "NOT_DETECTED"
    assert pocket["status"] == "DETECTED"
    assert pocket["max_regions"] >= 1


def test_recognize_uses_brep_aabb_when_mesh_pca_extent_is_inflated(monkeypatch):
    # Same true geometry, different tessellation sampling must not change fill.
    monkeypatch.setattr(battery, "read_step", lambda _: [object()])
    monkeypatch.setattr(battery, "geometry_metrics", lambda _: {
        "volume_mm3": 100 * 34 * 5 * 0.93,
        "dimensions_mm": [100, 34, 5],
        "bbox_mm": [0, 0, 0, 100, 34, 5],
    })
    monkeypatch.setattr(battery, "edge_groove_profile", lambda *_: {"status": "DETECTED"})
    for estimated in ([100, 34, 5], [100, 41, 7]):
        monkeypatch.setattr(battery, "_oriented_dimensions",
                            lambda *_, with_normal=False, d=estimated:
                            (d, [0, 0, 1]) if with_normal else d)
        result = battery.recognize_step("unused.step")
        assert result["verdict"] == "BATTERY_SHAPE"
        assert abs(result["fill_ratio"] - 0.93) < 1e-9
        assert result["fill_method"] == "brep_aabb"
        assert result["aabb_dimensions_mm"] == [100, 34, 5]


def test_pair_uses_aabb_thickness_not_inflated_mesh_pca():
    first = _cell(0, 0)
    second = _cell(0, 43)
    first["aabb_dimensions_mm"] = [80, 40, 5]
    second["aabb_dimensions_mm"] = [80, 40, 5]
    second["obb_dimensions_mm"] = [80, 41, 9]
    assert battery.compatible_pair(first, second)


def test_narrow_edge_detector_rejects_plain_cuboid():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    box = BRepPrimAPI_MakeBox(100, 40, 5).Solid()
    result = battery.edge_groove_profile([box], [0, 0, 0, 100, 40, 5])
    assert result["status"] == "NOT_DETECTED"
    assert result["method"] == "brep_bilateral_u_profile_faces"


def test_bilateral_u_grooves_detected_and_one_sided_rejected():
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    box = BRepPrimAPI_MakeBox(100, 40, 5).Solid()
    def cut_groove(shape, y):
        tool = BRepPrimAPI_MakeBox(
            gp_Pnt(0, y, 4), gp_Pnt(100, y + 0.3, 6)).Solid()
        return BRepAlgoAPI_Cut(shape, tool).Shape()

    bbox = [0, 0, 0, 100, 40, 5]
    single = cut_groove(box, 0.5)
    double = cut_groove(single, 39.2)
    assert battery.edge_groove_profile([single], bbox)["status"] == "NOT_DETECTED"
    result = battery.edge_groove_profile([double], bbox)
    assert result["status"] == "DETECTED"
    assert result["left_u_profiles"] >= 1
    assert result["right_u_profiles"] >= 1
