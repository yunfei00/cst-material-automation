"""P9 PCB volume-anchor tests with a synthetic P5 SQLite database."""
import json
import sqlite3

from cst_material.pcb_recognition import find_main_pcb


def _database(folder, records):
    connection = sqlite3.connect(folder / "geometry_features.sqlite3")
    connection.execute(
        "CREATE TABLE solids (solid_id TEXT, step TEXT, status TEXT, features_json TEXT)"
    )
    for sid, volume, bbox in records:
        connection.execute(
            "INSERT INTO solids VALUES (?, ?, 'PASS', ?)",
            (sid, sid + ".step", json.dumps({"volume_mm3": volume, "bbox_mm": bbox})),
        )
    connection.commit()
    connection.close()


def test_max_volume_wins_and_z_bounds(tmp_path):
    _database(tmp_path, [
        ("small", 1500, [0, 0, 0, 10, 10, 15]),
        ("pcb", 3000, [0, 0, 1, 100, 80, 2.6]),
        ("other", 2000, [0, 0, 0, 10, 10, 30]),
    ])
    result = find_main_pcb(tmp_path)
    assert result["eligible_count"] == 2
    assert result["candidate"]["solid_id"] == "pcb"
    assert result["candidate"]["z_min_mm"] == 1
    assert result["candidate"]["z_max_mm"] == 2.6
    assert result["candidate"]["z_thin_hint"]


def test_large_nonflat_piece_is_not_silently_replaced(tmp_path):
    _database(tmp_path, [
        ("housing", 4000, [0, 0, 0, 20, 20, 20]),
        ("pcb", 3000, [0, 0, 0, 100, 80, 1.6]),
    ])
    result = find_main_pcb(tmp_path)
    assert result["candidate"]["solid_id"] == "housing"
    assert not result["candidate"]["z_thin_hint"]


def test_no_eligible_volume(tmp_path):
    _database(tmp_path, [("small", 100, [0, 0, 0, 10, 10, 1])])
    assert find_main_pcb(tmp_path)["candidate"] is None
