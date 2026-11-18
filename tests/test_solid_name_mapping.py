"""CST name mapping must fail closed."""
import json

import pytest

from cst_material.solid_name_mapping import (
    isolated_export_vba,
    load_verified_mapping,
)


def test_isolation_only_deletes_siblings(tmp_path):
    vba = isolated_export_vba(
        "root/leaf:A", ["root/leaf:A", "root/leaf:B"], tmp_path / "a.step"
    )
    assert 'Solid.Delete ("root/leaf:B")' in vba
    assert 'Solid.Delete ("root/leaf:A")' not in vba
    assert '.Write ("root/leaf:A")' in vba


def test_reject_other_component(tmp_path):
    with pytest.raises(ValueError, match="其他组件"):
        isolated_export_vba(
            "root/leaf:A", ["root/leaf:A", "root/other:B"], tmp_path / "a.step"
        )


def test_verified_mapping_requires_real_file_and_status(tmp_path):
    step = tmp_path / "a.step"
    step.write_bytes(b"STEP" * 30)
    mapping = tmp_path / "verified_solid_mapping.json"
    row = {"solid_id": "root/leaf:A", "step": "a.step",
           "verification": "ISOLATED_SINGLE_BODY"}
    mapping.write_text(json.dumps({"schema_version": 1, "solids": [row]}))
    assert load_verified_mapping(tmp_path)["root/leaf:A"]["step"] == "a.step"
    row["verification"] = "GUESSED_BY_ORDER"
    mapping.write_text(json.dumps({"schema_version": 1, "solids": [row]}))
    with pytest.raises(ValueError, match="未经"):
        load_verified_mapping(tmp_path)
