"""P3-A: document-backed, read-only geometry measurements for existing CST solids.

CST 2025 VBA_3D Solid Object documents GetVolume and GetArea. The documentation
explicitly forbids GetLooseBoundingBoxOfShape in history rebuild / structural
macro execution, so this module deliberately DOES NOT call it through
Model3D.add_to_history. No dimensions, position, or material classification
are fabricated.
"""
from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cst_material.cst_api import ProjectInventory
from cst_material.cst_bindings import _modeler, _vba_string


@dataclass(frozen=True)
class GeometryFeature:
    identifier: str
    component: str | None
    material: str | None
    volume_project_unit3: float
    area_project_unit2: float
    volume_m3: float
    area_m2: float
    compactness: float | None
    zero_volume: bool


@dataclass(frozen=True)
class GeometryCatalog:
    length_unit: str
    length_unit_to_m: float
    features: tuple[GeometryFeature, ...]


def _nonnegative_number(value: str, label: str) -> float:
    try:
        number = float(value.strip())
    except ValueError as exc:
        raise RuntimeError(f"CST 几何查询的{label}不是有效数字：{value!r}") from exc
    if not math.isfinite(number) or number < 0:
        raise RuntimeError(f"CST 几何查询的{label}必须是有限非负数：{value!r}")
    return number


def read_geometry_features(project, inventory: ProjectInventory) -> GeometryCatalog:
    """Query Solid volume/area on an unsaved disposable CST project copy.

    GetVolume/GetArea are VBA query methods documented for Solid objects.
    Queries are written to a temporary ANSI VBA output file; only numeric
    results and the unit string are read back. The source .cst is never opened.
    """
    solids = inventory.solids
    if not solids:
        raise RuntimeError("P3 几何提取需要至少一个 Solid")
    fd, output_path = tempfile.mkstemp(prefix="cst_p3_geometry_", suffix=".txt")
    os.close(fd)
    result_file = Path(output_path)
    try:
        result_file.unlink()
        statements = [
            "Dim fh As Integer",
            "fh = FreeFile",
            f"Open {_vba_string(str(result_file))} For Output As #fh",
            'Print #fh, Units.GetUnit("Length")',
            "Print #fh, Str(Units.GetGeometryUnitToSI)",
        ]
        for solid in solids:
            escaped = _vba_string(solid.identifier)
            statements.append(f"Print #fh, Str(Solid.GetVolume({escaped}))")
            statements.append(f"Print #fh, Str(Solid.GetArea({escaped}))")
        statements.append("Close #fh")
        _modeler(project).add_to_history(
            "CST Material Automation: P3 geometry read-only (discard history)",
            "\n".join(statements),
        )
        if not result_file.is_file():
            raise RuntimeError("CST VBA 未生成 P3 几何特征查询结果")
        # Outputs contain only ASCII unit symbols and VBA Str() numeric values.
        rows = result_file.read_text(encoding="ascii").splitlines()
        if len(rows) != 2 + 2 * len(solids):
            raise RuntimeError(
                f"CST 几何结果数量不匹配：预期 {2 + 2 * len(solids)} 行，"
                f"实际 {len(rows)} 行"
            )
        unit = rows[0].strip()
        if unit not in {"nm", "um", "mm", "cm", "m", "mil", "in", "ft"}:
            raise RuntimeError(f"CST 工程长度单位不在官方支持列表：{unit!r}")
        conversion = _nonnegative_number(rows[1], "长度单位换算系数")
        if conversion <= 0:
            raise RuntimeError("CST 长度单位换算系数必须大于 0")
        features = []
        for index, solid in enumerate(solids):
            volume = _nonnegative_number(rows[2 + 2 * index], "体积")
            area = _nonnegative_number(rows[3 + 2 * index], "表面积")
            volume_m3 = volume * conversion ** 3
            area_m2 = area * conversion ** 2
            if not math.isfinite(volume_m3) or not math.isfinite(area_m2):
                raise RuntimeError(f"Solid {solid.identifier!r} 的 SI 换算结果不合法")
            compactness = None
            if volume > 0 and area > 0:
                compactness = 36 * math.pi * volume ** 2 / area ** 3
                if not math.isfinite(compactness):
                    raise RuntimeError(
                        f"Solid {solid.identifier!r} 的紧致度计算结果不合法"
                    )
            features.append(
                GeometryFeature(
                    identifier=solid.identifier,
                    component=solid.component,
                    material=solid.material,
                    volume_project_unit3=volume,
                    area_project_unit2=area,
                    volume_m3=volume_m3,
                    area_m2=area_m2,
                    compactness=compactness,
                    zero_volume=(volume == 0),
                )
            )
        return GeometryCatalog(
            length_unit=unit,
            length_unit_to_m=conversion,
            features=tuple(features),
        )
    finally:
        result_file.unlink(missing_ok=True)
