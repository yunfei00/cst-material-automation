"""CST 2025 3D project adapter.

Python calls: cst.interface.Project.model3d.get_tree_items/add_to_history
are documented in the supplied CST Python Libraries HTML help.
VBA Solid API method names and signatures are verified against the supplied\nCST 2025 Online Help / VBA_3D / Solid Object reference. The actual runtime\nbehavior still requires a real CST 2025 SP1 integration test.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from cst_material.cst_api import CSTBindings
from cst_material.domain import Material, Solid


def _tree_parts(value: str) -> tuple[str, ...]:
    return tuple(part for part in value.replace("/", "\\").split("\\") if part)


def _section_leaves(paths: list[str], section: str) -> list[tuple[str, ...]]:
    all_parts = [_tree_parts(path) for path in paths]
    candidates: set[tuple[str, ...]] = set()
    for parts in all_parts:
        positions = [i for i, part in enumerate(parts) if part.lower() == section.lower()]
        if positions:
            segment = parts[positions[0] :]
            if len(segment) >= 2:
                candidates.add(segment)
    return sorted(
        (
            entry
            for entry in candidates
            if not any(len(other) > len(entry) and other[: len(entry)] == entry for other in candidates)
        ),
        key=lambda x: tuple(part.casefold() for part in x),
    )


def _modeler(project):
    model = project.model3d
    if model is None:
        raise RuntimeError("CST 工程不包含三维 Model3D")
    return model


def _items(project) -> list[str]:
    result = _modeler(project).get_tree_items()
    if not isinstance(result, (list, tuple)):
        raise TypeError("CST get_tree_items 未返回列表，不能安全识别模型")
    return [str(item) for item in result]


def read_solids(project) -> tuple[Solid, ...]:
    parts_list = _section_leaves(_items(project), "Components")
    solids = []
    for parts in parts_list:
        if len(parts) < 3:
            continue
        component = "/".join(parts[1:-1])
        name = parts[-1]
        full_name = name if ":" in name else f"{component}:{name}"
        solids.append(Solid(full_name, component, None, None))
    if not solids:
        raise RuntimeError("未从 CST 工程树识别出 Solid。请查看诊断中的原始树路径，确认版本和路径结构")
    if len({solid.identifier for solid in solids}) != len(solids):
        raise RuntimeError("Solid 名称不唯一，拒绝继续，避免错误赋值")
    return tuple(solids)


def read_materials(project) -> tuple[Material, ...]:
    entries = _section_leaves(_items(project), "Materials")
    materials = tuple(Material(name=parts[-1]) for parts in entries)
    if not materials:
        raise RuntimeError("未从 CST 工程树识别出 Materials，拒绝进行材料赋值")
    if len({mat.name for mat in materials}) != len(materials):
        raise RuntimeError("材料名称重复，可能存在不同的材料子文件夹，拒绝自动解析")
    return materials


def _vba_string(value: str) -> str:
    if not value or any(ch in value for ch in "\r\n\x00"):
        raise ValueError("不允许空字符串、换行或 NUL 字符进入 CST VBA")
    return '"' + value.replace('"', '""') + '"'


def assign_material(project, solid_id: str, material_name: str) -> None:
    # CST 3D VBA: Solid.ChangeMaterial(solidname, materialname)
    vba = f"Solid.ChangeMaterial {_vba_string(solid_id)}, {_vba_string(material_name)}"
    _modeler(project).add_to_history("CST Material Automation: Assign material", vba)


def read_assigned_materials(project, solid_ids: tuple[str, ...]) -> dict[str, str]:
    """Read materials via a temporary VBA output file.

    This adds a *non-saved* history command to the project. Invoke only on a
    disposable project copy, or after reopening the saved output for verification.
    Never call on the original design file.
    """
    if not solid_ids:
        return {}
    fd, tmp = tempfile.mkstemp(prefix="cst_materials_", suffix=".txt")
    os.close(fd)
    result_file = Path(tmp)
    try:
        result_file.unlink()
        # Read results in the same order as the input solid IDs: no delimiter
        # needed, and material names can contain commas/colons.
        lines = [
            "Dim fh As Integer",
            "fh = FreeFile",
            f"Open {_vba_string(str(result_file))} For Output As #fh",
        ]
        for solid in solid_ids:
            lines.append(
                f"Print #fh, Solid.GetMaterialNameForShape({_vba_string(solid)})"
            )
        lines.append("Close #fh")
        _modeler(project).add_to_history(
            "CST Material Automation: Read materials (discard this history)", "\n".join(lines)
        )
        if not result_file.is_file():
            raise RuntimeError("CST VBA 未创建材料查询结果文件，检查 CST 消息窗口和 VBA 权限")
        encoding = "mbcs" if os.name == "nt" else "utf-8"
        values = result_file.read_text(encoding=encoding).splitlines()
        if len(values) != len(solid_ids):
            raise RuntimeError(
                f"材料读取结果数量不一致：期待 {len(solid_ids)}，实际 {len(values)}"
            )
        return dict(zip(solid_ids, values, strict=True))
    finally:
        result_file.unlink(missing_ok=True)



def read_shape_catalog(project) -> dict[str, str]:
    """Enumerate actual CST shapes and their materials via documented VBA methods.

    This read-only VBA query is executed through a temporary history entry,
    so call it ONLY on disposable copies of CST projects. The history entry
    must never be saved to the original project.
    """
    fd, tmp = tempfile.mkstemp(prefix="cst_shapes_", suffix=".txt")
    os.close(fd)
    result_file = Path(tmp)
    try:
        result_file.unlink()
        code = [
            "Dim fh As Integer",
            "Dim i As Long",
            "Dim shapeName As String",
            "Dim shapeCount As Long",
            "fh = FreeFile",
            f"Open {_vba_string(str(result_file))} For Output As #fh",
            "shapeCount = Solid.GetNumberOfShapes",
            "Print #fh, CStr(shapeCount)",
            "For i = 0 To shapeCount - 1",
            "shapeName = Solid.GetNameOfShapeFromIndex(i)",
            "Print #fh, shapeName",
            "Print #fh, Solid.GetMaterialNameForShape(shapeName)",
            "Next i",
            "Close #fh",
        ]
        _modeler(project).add_to_history(
            "CST Material Automation: Shape catalog (discard history)", "\n".join(code)
        )
        if not result_file.is_file():
            raise RuntimeError("CST VBA 未生成实体清单文件")
        encoding = "mbcs" if os.name == "nt" else "utf-8"
        rows = result_file.read_text(encoding=encoding).splitlines()
        if not rows:
            raise RuntimeError("CST VBA 实体清单文件为空")
        try:
            count = int(rows[0].strip())
        except ValueError as exc:
            raise RuntimeError("CST VBA 实体数量不是整数") from exc
        if count < 0 or len(rows) != 1 + 2 * count:
            raise RuntimeError(
                f"CST 实体清单不完整：声明 {count}，实际返回 {len(rows) - 1} 行"
            )
        names = rows[1::2]
        materials = rows[2::2]
        if not all(names) or len(set(names)) != len(names):
            raise RuntimeError("CST VBA 返回空实体名称或重复名称")
        return dict(zip(names, materials, strict=True))
    finally:
        result_file.unlink(missing_ok=True)


def binding() -> CSTBindings:
    return CSTBindings(
        read_solids=read_solids,
        read_materials=read_materials,
        assign_material=assign_material,
        read_assigned_materials=read_assigned_materials,
    )
