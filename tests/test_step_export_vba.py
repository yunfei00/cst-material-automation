"""STEP export VBA syntax regression; no CST required."""
import pytest

from cst_material.cst_step_export import build_step_export_vba


def test_step_export_uses_full_solid_id_and_parentheses():
    code = build_step_export_vba("component1:solid1", r"C:\Temp\part.step")
    assert '.Write ("component1:solid1")' in code
    assert "component1/solid1" not in code
    assert '.FileName ("C:\\Temp\\part.step")' in code
    assert ".ExportFromActiveCoordinateSystem (False)" in code
    assert code.splitlines()[0] == "With STEP"
    assert len(code.splitlines()) == 6


def test_step_export_preserves_escaped_solid_name():
    code = build_step_export_vba('main:contact "A"', "C:/Temp/a.step")
    assert '.Write ("main:contact ""A""")' in code


@pytest.mark.parametrize("name", ["missing", ":solid", "component:"])
def test_step_export_rejects_invalid_id(name):
    with pytest.raises(ValueError):
        build_step_export_vba(name, "part.step")
