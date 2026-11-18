"""P8-B3 conservative evidence and resumable batch checks."""
import json

from cst_material import shield_refinement


def test_planar_thickness_pairs():
    faces = [
        {"normal": (0, 0, 1), "center": (0, 0, 1), "area": 100},
        {"normal": (0, 0, -1), "center": (0, 0, 0.8), "area": 100},
        {"normal": (1, 0, 0), "center": (5, 0, 0), "area": 30},
        {"normal": (-1, 0, 0), "center": (4.8, 0, 0), "area": 30},
    ]
    result = shield_refinement.planar_thickness_evidence(faces)
    assert result["supported"]
    assert abs(result["dominant_thickness_mm"] - 0.2) < 1e-6
    assert result["pair_consistency"] == 1


def test_no_planar_pair():
    assert not shield_refinement.planar_thickness_evidence([])["supported"]


def test_batch_resume_and_failure(tmp_path, monkeypatch):
    (tmp_path / "manifest.json").write_text('{"solids":[]}', encoding="utf-8")
    (tmp_path / "a.step").write_bytes(b"valid-placeholder")
    rows = [
        {"solid_id": "a", "step": "a.step", "score": 90},
        {"solid_id": "b", "step": "missing.step", "score": 80},
    ]
    monkeypatch.setattr(shield_refinement, "search_shield_candidates", lambda _: rows)
    calls = []
    def analyze(path):
        calls.append(path)
        return {"verdict": "REVIEW", "thickness": {"supported": False}}
    monkeypatch.setattr(shield_refinement, "refine_step", analyze)
    first = shield_refinement.refine_candidates(tmp_path)
    assert first["summary"]["failed"] == 1
    assert len(calls) == 1
    second = shield_refinement.refine_candidates(tmp_path)
    assert second["summary"]["reused"] == 1
    assert len(calls) == 1
    saved = json.loads((tmp_path / "shield_refinement.json").read_text(encoding="utf-8"))
    assert saved["results"]["b"]["status"] == "FAIL"
    assert saved["results"]["a"]["verdict"] == "REVIEW"
