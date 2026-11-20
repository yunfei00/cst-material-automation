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
    assert len(called) == 10
    assert any(e.get("event") == "progress" and e.get("total") == 10 for e in events)
    assert result["summary"]["matched"] == 1
    report = json.loads((tmp_path / "battery_recognition.json").read_text(encoding="utf-8"))
    assert len(report["results"]) == 10
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
        return sid, {"sha256": digest, **rec}
    monkeypatch.setattr(battery, "_one", fake)
    result = battery.scan(tmp_path)
    assert result["summary"]["matched"] == 2
    assert result["summary"]["cell_configuration"] == "dual"
    report = json.loads((tmp_path / "battery_recognition.json").read_text(encoding="utf-8"))
    assert {sid for sid, rec in report["results"].items()
            if rec.get("verdict") == "BATTERY_SHAPE"} == {"cell-0", "cell-1"}
