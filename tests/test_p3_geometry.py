"""P3 geometry tests use no CST installation or company project."""
import math
import re
from pathlib import Path

import pytest

from cst_material.cst_api import ProjectInventory
from cst_material.domain import Material, Solid
from cst_material.p3_geometry import read_geometry_features


def inventory():
    return ProjectInventory(
        solids=(
            Solid("housing:main_1", "housing", "Copper", None),
            Solid("housing:sheet_2", "housing", "Vacuum", None),
        ),
        materials=(Material("Copper"), Material("Vacuum")),
    )


class FakeModel:
    def __init__(self, values):
        self.values = values
        self.scripts = []

    def add_to_history(self, header, script):
        self.scripts.append((header, script))
        assert "Solid.GetVolume" in script
        assert "Solid.GetArea" in script
        assert 'Units.GetUnit("Length")' in script
        assert "Units.GetGeometryUnitToSI" in script
        assert "GetLooseBoundingBoxOfShape" not in script
        match = re.search(r'^Open "(.+)" For Output As #fh$', script, flags=re.M)
        assert match
        Path(match.group(1)).write_text(self.values, encoding="ascii")


class FakeProject:
    def __init__(self, values):
        self.model3d = FakeModel(values)


def test_read_geometry_uses_documented_vba_methods_and_si_conversion():
    project = FakeProject("mm\n0.001\n2\n12\n0\n8\n")
    result = read_geometry_features(project, inventory())
    assert result.length_unit == "mm"
    assert result.length_unit_to_m == pytest.approx(0.001)
    a, b = result.features
    assert a.identifier == "housing:main_1"
    assert a.volume_project_unit3 == 2
    assert a.area_project_unit2 == 12
    assert a.volume_m3 == pytest.approx(2e-9)
    assert a.area_m2 == pytest.approx(12e-6)
    assert a.compactness == pytest.approx(36 * math.pi * 4 / (12 ** 3))
    assert not a.zero_volume
    assert b.zero_volume
    assert b.volume_m3 == 0
    assert b.compactness is None


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("mm\n0.001\n2\n12\n0\n", "数量不匹配"),
        ("mm\n0.001\n-1\n12\n0\n8\n", "有限非负"),
        ("mm\n0.001\nnan\n12\n0\n8\n", "有限非负"),
        ("mm\n0.001\ninf\n12\n0\n8\n", "有限非负"),
        ("mm\n0.001\nfoo\n12\n0\n8\n", "不是有效数字"),
        ("mm\n0\n2\n12\n0\n8\n", "必须大于"),
        ("unknown\n0.001\n2\n12\n0\n8\n", "长度单位"),
    ],
)
def test_geometry_rejects_incomplete_or_invalid_values(text, message):
    project = FakeProject(text)
    with pytest.raises(RuntimeError, match=message):
        read_geometry_features(project, inventory())


def test_geometry_rejects_no_solids():
    empty = ProjectInventory(solids=(), materials=())
    with pytest.raises(RuntimeError, match="Solid"):
        read_geometry_features(FakeProject(""), empty)
