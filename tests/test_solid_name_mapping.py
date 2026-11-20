"""CST name mapping must fail closed."""
import json

import pytest

from cst_material.solid_name_mapping import load_verified_mapping


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
