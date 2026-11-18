"""P7 structure sample batch management tests."""
import pytest

from cst_material.structure_samples import (
    delete_confirmed_samples,
    load_samples,
    save_confirmed_samples_batch,
)


def metrics():
    return {
        "dimensions_mm": [0.2, 5.0, 20.0],
        "volume_mm3": 8.0, "surface_mm2": 100.0,
        "face_count": 8, "edge_count": 20,
        "surface_types": {"Plane": 6, "Cylinder": 2},
    }


def test_batch_save_and_delete(tmp_path):
    target = tmp_path / "samples.json"
    entries = [
        {"sample_id": f"sha:item{i}", "part_type": "SPRING_CONTACT",
         "source_sha256": "sha", "metrics": metrics()}
        for i in range(3)
    ]
    result = save_confirmed_samples_batch(target, entries)
    assert result == {"added": 3, "total": 3}
    assert len(load_samples(target)["samples"]) == 3
    with pytest.raises(ValueError, match="已存在"):
        save_confirmed_samples_batch(target, entries[:1])
    assert len(load_samples(target)["samples"]) == 3
    assert delete_confirmed_samples(target, ["sha:item1"]) == {
        "deleted": 1, "total": 2,
    }
    assert {x["sample_id"] for x in load_samples(target)["samples"]} == {
        "sha:item0", "sha:item2",
    }


def test_batch_rejects_partial_invalid_input(tmp_path):
    target = tmp_path / "samples.json"
    with pytest.raises(ValueError, match="类别"):
        save_confirmed_samples_batch(target, [
            {"sample_id": "valid", "part_type": "SPRING_CONTACT", "metrics": metrics()},
            {"sample_id": "bad", "part_type": "", "metrics": metrics()},
        ])
    assert not target.exists()
    with pytest.raises(ValueError, match="不存在"):
        delete_confirmed_samples(target, ["missing"])
