"""CST 2025 3D project adapter.

Python calls use model3d.add_to_history, but never get_tree_items because
some CST 2025 projects trigger a non-UTF-8 decoding error in that method.
VBA Solid API method names and signatures are verified against the supplied\nCST 2025 Online Help / VBA_3D / Solid Object reference. The actual runtime\nbehavior still requires a real CST 2025 SP1 integration test.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from cst_material.cst_api import CSTBindings
from cst_material.domain import Material, Solid


def _modeler(project):
    model = project.model3d
    if model is None:
        raise RuntimeError("CST 工程不包含三维 Model3D")
    return model


def read_solids(project) -> tuple[Solid, ...]:
    """Use VBA Solid enumeration to bypass get_tree_items() UTF-8 errors."""
    catalog = read_shape_catalog(project)
    if not catalog:
        raise RuntimeError("CST VBA 未发现任何 Solid，请确认工程包含三维实体")
    return tuple(
        Solid(
            identifier=name,
            component=name.rpartition(":")[0] or None,
            material=material or None,
            bounds=None,
        )
        for name, material in catalog.items()
    )


def read_materials(project) -> tuple[Material, ...]:
    """Read current-project materials directly via the documented VBA API."""
    names = read_material_catalog(project)
    if not names:
        raise RuntimeError("CST 工程没有枚举到 Material，无法执行材料赋值")
    if len(names) != len(set(names)):
        raise RuntimeError("CST VBA 返回重复的材料名称，拒绝继续")
    return tuple(Material(name=name) for name in names)


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
            # The official page does not specify whether the index is 0- or
            # 1-based; include both boundaries and ignore missing indexes.
            "For i = 0 To shapeCount",
            "shapeName = Solid.GetNameOfShapeFromIndex(i)",
            "If Len(shapeName) > 0 Then",
            "Print #fh, shapeName",
            "Print #fh, Solid.GetMaterialNameForShape(shapeName)",
            "End If",
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


def read_material_catalog(project) -> tuple[str, ...]:
    """Return material names using official VBA Material.GetNumberOfMaterials.

    VBA's Open...For Output uses the active Windows ANSI code page. Matching
    Python's 'mbcs' avoids interpreting GBK/CP936 names as UTF-8.
    """
    fd, tmp = tempfile.mkstemp(prefix="cst_material_catalog_", suffix=".txt")
    os.close(fd)
    result_file = Path(tmp)
    try:
        result_file.unlink()
        code = [
            "Dim fh As Integer",
            "Dim i As Long",
            "Dim materialCount As Long",
            "fh = FreeFile",
            f"Open {_vba_string(str(result_file))} For Output As #fh",
            "materialCount = Material.GetNumberOfMaterials",
            "Print #fh, CStr(materialCount)",
            "For i = 0 To materialCount - 1",
            "Print #fh, Material.GetNameOfMaterialFromIndex(i)",
            "Next i",
            "Close #fh",
        ]
        _modeler(project).add_to_history(
            "CST Material Automation: Read material catalog (discard history)",
            "\n".join(code),
        )
        if not result_file.is_file():
            raise RuntimeError("CST VBA 未生成材料清单文件")
        encoding = "mbcs" if os.name == "nt" else "utf-8"
        rows = result_file.read_text(encoding=encoding).splitlines()
        if not rows:
            raise RuntimeError("CST VBA 返回空材料清单文件")
        try:
            count = int(rows[0].strip())
        except ValueError as exc:
            raise RuntimeError("CST VBA 材料数量不是整数") from exc
        names = rows[1:]
        if count < 0 or len(names) != count:
            raise RuntimeError(
                f"CST 材料清单不完整：声明 {count}，实际读取 {len(names)}"
            )
        if any(not name for name in names):
            raise RuntimeError("CST VBA 返回空材料名称")
        return tuple(names)
    finally:
        result_file.unlink(missing_ok=True)



def material_exists(project, material_name: str) -> bool:
    """Confirm a material exists using the official Material.Exists VBA API.

    Must be called only on an unsaved project copy; the VBA query may leave
    a temporary History entry even though it never changes materials.
    """
    fd, tmp = tempfile.mkstemp(prefix="cst_material_exists_", suffix=".txt")
    os.close(fd)
    result_file = Path(tmp)
    try:
        result_file.unlink()
        code = [
            "Dim fh As Integer",
            "fh = FreeFile",
            f"Open {_vba_string(str(result_file))} For Output As #fh",
            f"Print #fh, CStr(Material.Exists({_vba_string(material_name)}))",
            "Close #fh",
        ]
        _modeler(project).add_to_history(
            "CST Material Automation: Verify material exists (discard history)",
            "\n".join(code),
        )
        if not result_file.is_file():
            raise RuntimeError("CST VBA Material.Exists 未生成验证结果")
        encoding = "mbcs" if os.name == "nt" else "utf-8"
        result = result_file.read_text(encoding=encoding).strip().lower()
        if result not in {"true", "false"}:
            raise RuntimeError(f"材料存在性查询结果异常：{result!r}")
        return result == "true"
    finally:
        result_file.unlink(missing_ok=True)


def binding() -> CSTBindings:
    return CSTBindings(
        read_solids=read_solids,
        read_materials=read_materials,
        assign_material=assign_material,
        read_assigned_materials=read_assigned_materials,
        enumerate_shapes=read_shape_catalog,
        material_exists=material_exists,
    )
