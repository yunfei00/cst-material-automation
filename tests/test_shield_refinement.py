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
    assert saved["schema_version"] == 2
    assert saved["algorithm_version"] == "P8-B4-cross-section-v2-20261118"
    assert saved["results"]["b"]["status"] == "FAIL"
    assert saved["results"]["a"]["verdict"] == "REVIEW"


def test_same_facing_planes_not_thickness():
    faces = [
        {'normal': (0, 0, 1), 'center': (0, 0, 1), 'area': 100},
        {'normal': (0, 0, 1), 'center': (0, 0, 0.8), 'area': 100},
    ]
    assert not shield_refinement.planar_thickness_evidence(faces)['supported']


def test_corrupt_checkpoint_is_recovered(tmp_path, monkeypatch):
    (tmp_path / 'manifest.json').write_text('{}', encoding='utf-8')
    (tmp_path / 'shield_refinement.json').write_text('{broken', encoding='utf-8')
    monkeypatch.setattr(shield_refinement, 'search_shield_candidates', lambda _: [])
    result = shield_refinement.refine_candidates(tmp_path)
    assert result['summary']['total'] == 0


def test_refine_step_reads_step_once(monkeypatch):
    from cst_material import occt_geometry

    calls = []
    def read(path):
        calls.append(path)
        return [object()]

    monkeypatch.setattr(occt_geometry, 'read_step', read)
    monkeypatch.setattr(shield_refinement, '_face_evidence', lambda solids: [])
    monkeypatch.setattr(shield_refinement, 'analyze_shield_solids', lambda solids: {
        'metrics': {'transverse_face_count': 0}, 'evidence': []
    })
    result = shield_refinement.refine_step('dummy.step')
    assert len(calls) == 1
    assert result['verdict'] == 'LOW_EVIDENCE'

def test_cross_section_rejects_open_plate(monkeypatch):
    from cst_material import occt_geometry

    monkeypatch.setattr(occt_geometry, "classify_point",
                        lambda solids, point, tolerance: "OUTSIDE")
    result = shield_refinement._cavity_cross_section(
        [object()], [0, 0, 0, 10, 10, 10], (0, 0, 1)
    )
    assert result["supported"]
    assert result["enclosed_section_count"] == 0
    assert not result["wall_enclosure_hint"]


def test_cross_section_wall_hint(monkeypatch):
    from cst_material import occt_geometry

    def classify(solids, point, tolerance):
        x, y, _ = point
        return "INSIDE" if x < 1 or x > 9 or y < 1 or y > 9 else "OUTSIDE"

    monkeypatch.setattr(occt_geometry, "classify_point", classify)
    result = shield_refinement._cavity_cross_section(
        [object()], [0, 0, 0, 10, 10, 10], (0, 0, 1)
    )
    assert result["wall_enclosure_hint"]
    assert result["enclosed_section_count"] == 3

def test_cross_section_solid_block(monkeypatch):
    from cst_material import occt_geometry

    monkeypatch.setattr(occt_geometry, "classify_point",
                        lambda solids, point, tolerance: "INSIDE")
    result = shield_refinement._cavity_cross_section(
        [object()], [0, 0, 0, 10, 10, 10], (0, 0, 1)
    )
    assert result["all_solid_section_count"] == 3
    assert result["enclosed_section_count"] == 0


def test_cross_section_invalid_axis_is_unknown():
    result = shield_refinement._cavity_cross_section(
        [object()], [0, 0, 0, 10, 10, 10], (float("nan"), 0, 1)
    )
    assert not result["supported"]
