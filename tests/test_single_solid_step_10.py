"""Unit checks for bounded single-Solid STEP export experiment."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "cst_ten_solid_script", Path(__file__).resolve().parents[1] / "export_ten_solids.py"
)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
build_single_solid_vba = _module.build_single_solid_vba
export_ten_solids = _module.export_ten_solids


def test_colon_object_path():
    code = build_single_solid_vba("Main/Leaf:Solid A", Path("one.step"))
    assert '.Write ("Main/Leaf:Solid A")' in code
    assert '.Write ("Main/Leaf")' not in code


def test_invalid_identity():
    with pytest.raises(ValueError):
        build_single_solid_vba("Main/Leaf", "one.step")


def test_hard_limit_before_output(tmp_path):
    source = tmp_path / "input.cst"
    source.write_bytes(b"test")
    target = tmp_path / "result"
    with pytest.raises(ValueError, match="最多"):
        export_ten_solids(source, target, limit=11)
    assert not target.exists()
