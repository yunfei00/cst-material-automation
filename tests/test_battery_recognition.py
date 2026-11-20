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
        conn.execute("CREATE TABLE solids (solid_id TEXT, step TEXT, status TEXT)")
        conn.executemany("INSERT INTO solids VALUES (?,?,?)", [
            ("battery-a", "a.step", "PASS"), ("broken-b", "b.step", "PASS")])
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
    report = json.loads((tmp_path / "battery_recognition.json").read_text())
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
