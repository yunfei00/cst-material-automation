"""P3-C: documented CST non-history spatial query and cautious AABB analysis.

CST 2025 official help:
- Solid.GetLooseBoundingBoxOfShape(name, xmin, xmax, ymin, ymax, zmin, zmax)
  returns a NON-TIGHT bounding box, designed for post-processing and forbidden
  during history rebuild/structural macro execution.
- Project.RunScript(path) executes a command script outside add_to_history.
- CSTStudio.Application.2025 supports OLE automation and OpenFile(path).

Actual COM + RunScript behavior must be validated on real CST 2025 SP1.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
import time
import warnings
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from cst_material.cst_bindings import _vba_string
from cst_material.domain import BoundingBox

MARKER = "CST_P3_SPATIAL_V1"
MAX_NEIGHBOR_SOLIDS = 12000
NEIGHBOR_BATCH_SIZE = 32


@dataclass(frozen=True)
class SpatialFeature:
    identifier: str
    component: str | None
    material: str | None
    loose_bounds: BoundingBox | None
    dimensions_project_unit: tuple[float, float, float] | None
    bbox_midpoint_project_unit: tuple[float, float, float] | None
    min_bbox_extent_project_unit: float | None


@dataclass(frozen=True)
class SpatialCatalog:
    length_unit: str
    length_unit_to_m: float
    declared_solid_count: int
    features: tuple[SpatialFeature, ...]
    missing_bbox_count: int
    cleanup_deferred: bool = False
    cleanup_path: str | None = None


def _number(value: str, what: str) -> float:
    try:
        result = float(value.strip())
    except ValueError as exc:
        raise RuntimeError(f"{what} 不是合法数值：{value!r}") from exc
    if not math.isfinite(result):
        raise RuntimeError(f"{what} 不能为 NaN 或 Inf")
    return result


def parse_spatial_data(rows: list[str]) -> SpatialCatalog:
    """Reject truncated, duplicate or out-of-order results. No guessed bounds."""
    if len(rows) < 4 or rows[0].strip() != MARKER:
        raise RuntimeError("CST P3-C 输出文件缺少正确的版本标识")
    unit = rows[1].strip()
    if unit not in {"nm", "um", "mm", "cm", "m", "mil", "in", "ft"}:
        raise RuntimeError(f"CST P3-C 未识别长度单位：{unit!r}")
    to_m = _number(rows[2], "长度单位转换系数")
    if to_m <= 0:
        raise RuntimeError("长度单位转换系数必须大于零")
    try:
        declared = int(rows[3].strip())
    except ValueError as exc:
        raise RuntimeError("CST P3-C Solid 数量不是整数") from exc
    if declared <= 0:
        raise RuntimeError("CST P3-C 未检测到 Solid，或返回了无效数量")
    cursor = 4
    records: list[SpatialFeature] = []
    for index in range(declared):
        if cursor + 2 > len(rows):
            raise RuntimeError(f"P3-C 第 {index + 1}/{declared} 个 Solid 记录不完整")
        if cursor + 3 > len(rows):
            raise RuntimeError(f"P3-C 第 {index + 1}/{declared} 个 Solid 材料记录不完整")
        name, material, state = rows[cursor], rows[cursor + 1], rows[cursor + 2].strip()
        cursor += 3
        if not name or "\x00" in name or "\r" in name:
            raise RuntimeError(f"P3-C 第 {index + 1} 个 Solid 名称无效")
        if state == "MISSING":
            bounds = dims = mid = smallest = None
        elif state == "OK":
            if cursor + 6 > len(rows):
                raise RuntimeError(f"Solid {name!r} 的包围盒坐标不足六项")
            xmin, xmax, ymin, ymax, zmin, zmax = [
                _number(value, "包围盒坐标") for value in rows[cursor : cursor + 6]
            ]
            cursor += 6
            if xmin > xmax or ymin > ymax or zmin > zmax:
                raise RuntimeError(f"Solid {name!r} 包围盒的最小值大于最大值")
            bounds = BoundingBox(xmin, ymin, zmin, xmax, ymax, zmax)
            dims = bounds.dimensions
            mid = (
                (xmin + xmax) / 2,
                (ymin + ymax) / 2,
                (zmin + zmax) / 2,
            )
            if not all(math.isfinite(v) for v in (*dims, *mid)):
                raise RuntimeError(f"Solid {name!r} 的尺寸或包围盒中点溢出")
            smallest = min(dims)
        else:
            raise RuntimeError(f"Solid {name!r} 返回未知状态：{state!r}")
        component = name.rpartition(":")[0] or None
        records.append(
            SpatialFeature(
                identifier=name,
                component=component,
                material=material or None,
                loose_bounds=bounds,
                dimensions_project_unit=dims,
                bbox_midpoint_project_unit=mid,
                min_bbox_extent_project_unit=smallest,
            )
        )
    if cursor != len(rows):
        raise RuntimeError(f"CST P3-C 结果有 {len(rows) - cursor} 行未解释数据")
    if len({rec.identifier for rec in records}) != declared:
        raise RuntimeError("CST P3-C 包围盒记录包含重复 Solid 名称")
    return SpatialCatalog(
        length_unit=unit,
        length_unit_to_m=to_m,
        declared_solid_count=declared,
        features=tuple(records),
        missing_bbox_count=sum(rec.loose_bounds is None for rec in records),
    )


def _bbox_separation(a: BoundingBox, b: BoundingBox) -> float:
    """Lower bound on true solid separation, NOT measured surface distance."""
    dx = max(0.0, a.xmin - b.xmax, b.xmin - a.xmax)
    dy = max(0.0, a.ymin - b.ymax, b.ymin - a.ymax)
    dz = max(0.0, a.zmin - b.zmax, b.zmin - a.zmax)
    return math.hypot(math.hypot(dx, dy), dz)


def analyze_neighbors(catalog: SpatialCatalog, neighbor_limit: int) -> dict:
    """Exact closest *loose AABB* candidates with bounded working memory.

    Brute-force Python sorting needed O(N) memory per Solid and repeated all
    comparisons. Here numpy calculates bounded blocks (32 x N) of pairwise
    AABB separation and selects only the nearest k without materializing the
    N x N distance matrix. Runtime remains O(N^2), but peak temporary memory
    is O(batch_size * N), making ~7,311 Solid practical on Windows.
    """
    if not 0 <= neighbor_limit <= 20:
        raise ValueError("邻居数量必须在 0 到 20 之间")
    # Stable alphabetical ordering for tied distances, including many overlaps.
    valid = sorted(
        (feature for feature in catalog.features if feature.loose_bounds is not None),
        key=lambda feature: feature.identifier,
    )
    count = len(valid)
    if neighbor_limit == 0:
        return {"status": "SKIPPED", "reason": "显式关闭邻近分析", "neighbors": []}
    if count > MAX_NEIGHBOR_SOLIDS:
        return {
            "status": "SKIPPED",
            "reason": (
                f"可用 Solid 数量 {count} 超过计算上限 {MAX_NEIGHBOR_SOLIDS}，"
                "请减少分析范围或使用 --neighbors 0；保留所有包围盒数据"
            ),
            "neighbors": [],
        }
    if count == 0:
        return {"status": "SKIPPED", "reason": "没有可用于邻近分析的包围盒", "neighbors": []}
    if count == 1:
        return {
            "status": "PASS",
            "algorithm": "batched-numpy-exact-loose-aabb",
            "analyzed_solid_count": 1,
            "neighbors": [
                {"identifier": valid[0].identifier, "nearest_bbox_candidates": []}
            ],
        }

    import numpy as np

    lower = np.asarray(
        [[f.loose_bounds.xmin, f.loose_bounds.ymin, f.loose_bounds.zmin] for f in valid],
        dtype=np.float64,
    )
    upper = np.asarray(
        [[f.loose_bounds.xmax, f.loose_bounds.ymax, f.loose_bounds.zmax] for f in valid],
        dtype=np.float64,
    )
    k = min(neighbor_limit, count - 1)
    results = []
    for start in range(0, count, NEIGHBOR_BATCH_SIZE):
        stop = min(count, start + NEIGHBOR_BATCH_SIZE)
        lo = lower[start:stop]
        hi = upper[start:stop]

        # Axis gap between each pair of disjoint intervals; zero on overlap.
        dx = np.maximum(
            0.0,
            np.maximum(lo[:, None, 0] - upper[None, :, 0], lower[None, :, 0] - hi[:, None, 0]),
        )
        dy = np.maximum(
            0.0,
            np.maximum(lo[:, None, 1] - upper[None, :, 1], lower[None, :, 1] - hi[:, None, 1]),
        )
        dz = np.maximum(
            0.0,
            np.maximum(lo[:, None, 2] - upper[None, :, 2], lower[None, :, 2] - hi[:, None, 2]),
        )
        np.hypot(dx, dy, out=dx)
        np.hypot(dx, dz, out=dx)
        # Do not treat numeric overflow in a CST bounding box as valid.
        if not np.isfinite(dx).all():
            raise RuntimeError("P3-C 包围盒邻近距离包含 NaN/Inf，拒绝输出不可靠结果")
        block_rows = np.arange(stop - start)
        dx[block_rows, np.arange(start, stop)] = np.inf

        # argpartition avoids sorting all N candidates for each Solid.
        subset = np.argpartition(dx, kth=k - 1, axis=1)[:, :k]
        for offset, nearest_indices in enumerate(subset):
            distances = dx[offset]
            # Include *all* candidates tied at the kth distance, so the
            # result is deterministic by identifier even for huge overlap sets.
            cutoff = float(np.max(distances[nearest_indices]))
            strictly_better = np.flatnonzero(distances < cutoff)
            better_sorted = sorted(
                (int(index) for index in strictly_better),
                key=lambda index: (float(distances[index]), index),
            )
            # valid[] is already ordered by identifier; equal-distance ties
            # can therefore be selected directly in index order.
            ties = np.flatnonzero(distances == cutoff)
            nearest = better_sorted + [
                int(index) for index in ties[: k - len(better_sorted)]
            ]
            candidates = []
            for index in nearest:
                distance = float(distances[index])
                distance_m = distance * catalog.length_unit_to_m
                if not math.isfinite(distance_m):
                    raise RuntimeError("P3-C 包围盒距离单位换算溢出")
                candidates.append(
                    {
                        "identifier": valid[index].identifier,
                        "loose_bbox_distance_lower_bound_project_unit": distance,
                        "loose_bbox_distance_lower_bound_m": distance_m,
                        "loose_boxes_overlap_or_touch": distance == 0.0,
                    }
                )
            results.append(
                {
                    "identifier": valid[start + offset].identifier,
                    "nearest_bbox_candidates": candidates,
                }
            )
    return {
        "status": "PASS",
        "algorithm": "batched-numpy-exact-loose-aabb",
        "analyzed_solid_count": count,
        "neighbors_per_solid": k,
        "neighbors": results,
    }



def load_spatial_catalog_from_report(path: Path) -> tuple[SpatialCatalog, dict]:
    """Reconstruct and revalidate bounds from an existing local P3-C JSON report.

    No CST/COM/file copying is involved. Only PASS/PARTIAL reports with a full
    spatial feature list are eligible. Old SKIPPED neighbor data is ignored.
    """
    path = path.resolve(strict=True)
    if path.suffix.casefold() != ".json" or not path.is_file():
        raise ValueError("P3-C 离线分析输入必须是已存在的 .json 报告")
    source = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(source, dict) or source.get("task") != "spatial":
        raise ValueError("输入不是 P3-C spatial 查询报告")
    if source.get("status") not in {"PASS", "PARTIAL"}:
        raise ValueError("P3-C 原始报告未成功读取完整包围盒，拒绝离线分析")
    payload = source.get("spatial")
    if not isinstance(payload, dict):
        raise ValueError("P3-C 报告缺少 spatial 结果")
    try:
        solid_count = payload["solid_count"]
        unit = payload["length_unit"]
        to_m = float(payload["length_unit_to_m"])
        items = payload["features"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("P3-C 报告缺少完整的长度单位或 Solid 字段") from exc
    if (
        not isinstance(solid_count, int)
        or isinstance(solid_count, bool)
        or solid_count <= 0
        or not isinstance(items, list)
        or len(items) != solid_count
    ):
        raise ValueError("P3-C 报告的 Solid 数量或数据列表不完整")
    if unit not in {"nm", "um", "mm", "cm", "m", "mil", "in", "ft"}:
        raise ValueError(f"P3-C 报告的长度单位未知：{unit!r}")
    if not math.isfinite(to_m) or to_m <= 0:
        raise ValueError("P3-C 报告长度单位换算系数不合法")
    features = []
    names = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("P3-C 报告包含格式不正确的 Solid 条目")
        name = item.get("identifier")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("P3-C 报告含空 Solid 名称或重复名称")
        names.add(name)
        component = item.get("component")
        material = item.get("material")
        if component is not None and not isinstance(component, str):
            raise ValueError(f"Solid {name!r} 的 Component 类型不正确")
        if material is not None and not isinstance(material, str):
            raise ValueError(f"Solid {name!r} 的 Material 类型不正确")
        raw_bounds = item.get("loose_bounds")
        if raw_bounds is None:
            bounds = dims = mid = smallest = None
        else:
            if not isinstance(raw_bounds, dict):
                raise ValueError(f"Solid {name!r} 的包围盒格式不正确")
            try:
                vals = {
                    key: _number(str(raw_bounds[key]), f"{name} 的 {key}")
                    for key in ("xmin", "ymin", "zmin", "xmax", "ymax", "zmax")
                }
            except KeyError as exc:
                raise ValueError(f"Solid {name!r} 的包围盒缺少坐标") from exc
            try:
                bounds = BoundingBox(**vals)
            except ValueError as exc:
                raise ValueError(f"Solid {name!r} 的包围盒坐标顺序错误") from exc
            dims = bounds.dimensions
            mid = (
                (bounds.xmin + bounds.xmax) / 2,
                (bounds.ymin + bounds.ymax) / 2,
                (bounds.zmin + bounds.zmax) / 2,
            )
            if not all(math.isfinite(v) for v in (*dims, *mid)):
                raise ValueError(f"Solid {name!r} 的包围盒数值超出范围")
            smallest = min(dims)
        features.append(
            SpatialFeature(
                identifier=name,
                component=component,
                material=material,
                loose_bounds=bounds,
                dimensions_project_unit=dims,
                bbox_midpoint_project_unit=mid,
                min_bbox_extent_project_unit=smallest,
            )
        )
    missing = sum(f.loose_bounds is None for f in features)
    if payload.get("missing_bbox_count") != missing:
        raise ValueError("P3-C 报告缺失包围盒数量与实际记录不一致")
    catalog = SpatialCatalog(
        length_unit=unit,
        length_unit_to_m=to_m,
        declared_solid_count=solid_count,
        features=tuple(features),
        missing_bbox_count=missing,
        cleanup_deferred=bool(payload.get("cleanup_deferred", False)),
        cleanup_path=payload.get("cleanup_path"),
    )
    return catalog, {
        "original_status": source["status"],
        "original_cleanup_deferred": catalog.cleanup_deferred,
        "original_missing_bbox_count": missing,
    }


def summarize_spatial(catalog: SpatialCatalog, neighbor_limit: int) -> dict:
    """JSON-ready metadata with explicit loose/non-tight geometry qualifiers."""
    return {
        "length_unit": catalog.length_unit,
        "length_unit_to_m": catalog.length_unit_to_m,
        "solid_count": catalog.declared_solid_count,
        "missing_bbox_count": catalog.missing_bbox_count,
        "cleanup_deferred": catalog.cleanup_deferred,
        "cleanup_path": catalog.cleanup_path,
        "bounds_type": "CST GetLooseBoundingBoxOfShape: non-tight AABB; upper extent, not exact geometry",
        "features": [
            {
                **asdict(item),
                "dimensions_m": tuple(
                    value * catalog.length_unit_to_m
                    for value in item.dimensions_project_unit
                ) if item.dimensions_project_unit is not None else None,
                "bbox_midpoint_m": tuple(
                    value * catalog.length_unit_to_m
                    for value in item.bbox_midpoint_project_unit
                ) if item.bbox_midpoint_project_unit is not None else None,
            }
            for item in catalog.features
        ],
        "neighbors": analyze_neighbors(catalog, neighbor_limit),
        "interpretation": (
            "尺寸来自非紧包围盒，仅为上界；bbox_midpoint 不是实体质心；"
            "包围盒间距只是实际几何间距的下界，0 不证明实体接触。"
            "min_bbox_extent 不是已验证的真实厚度。"
        ),
    }


def build_spatial_macro(result_path: Path) -> str:
    """A VBA command script for Project.RunScript, never add_to_history."""
    path_literal = _vba_string(str(result_path))
    # Source is ASCII except output path; write via Windows ANSI encoding.
    return "\n".join(
        [
            "Option Explicit",
            "Sub Main()",
            "Dim fh As Integer",
            "Dim i As Long",
            "Dim n As Long",
            "Dim shapeName As String",
            "Dim xmin As Double, xmax As Double",
            "Dim ymin As Double, ymax As Double",
            "Dim zmin As Double, zmax As Double",
            "Dim found As Boolean",
            "fh = FreeFile",
            f"Open {path_literal} For Output As #fh",
            f'Print #fh, "{MARKER}"',
            'Print #fh, Units.GetUnit("Length")',
            "Print #fh, Str(Units.GetGeometryUnitToSI)",
            "n = Solid.GetNumberOfShapes",
            "Print #fh, CStr(n)",
            "For i = 0 To n",
            "shapeName = Solid.GetNameOfShapeFromIndex(i)",
            "If Len(shapeName) > 0 Then",
            "Print #fh, shapeName",
            "Print #fh, Solid.GetMaterialNameForShape(shapeName)",
            "found = Solid.GetLooseBoundingBoxOfShape(shapeName, xmin, xmax, ymin, ymax, zmin, zmax)",
            "If found Then",
            'Print #fh, "OK"',
            "Print #fh, Str(xmin)",
            "Print #fh, Str(xmax)",
            "Print #fh, Str(ymin)",
            "Print #fh, Str(ymax)",
            "Print #fh, Str(zmin)",
            "Print #fh, Str(zmax)",
            "Else",
            'Print #fh, "MISSING"',
            "End If",
            "End If",
            "Next i",
            "Close #fh",
            "End Sub",
        ]
    ) + "\n"


# PowerShell OLE bridge avoids installing additional Python packages such as
# pywin32 on the locked-down CST workstation. Crucially, do NOT call app.Quit:
# COM may bind an existing user-run CST session. Quit only our temp project.
COM_RUNNER = r"""
$ErrorActionPreference = 'Stop'
$app = $null
$project = $null
try {
    $app = New-Object -ComObject 'CSTStudio.Application.2025'
    $project = $app.OpenFile($env:CST_P3_SPATIAL_COPY)
    if ($null -eq $project) { throw "CST OLE OpenFile returned null" }
    $null = $project.RunScript($env:CST_P3_SPATIAL_MACRO)
} finally {
    if ($null -ne $project) {
        try { $null = $project.Quit() } catch {
            Write-Warning "Could not close temporary CST project: $($_.Exception.Message)"
        }
        try {
            if ([System.Runtime.InteropServices.Marshal]::IsComObject($project)) {
                [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($project)
            }
        } catch {
            Write-Warning "Could not release temporary CST project COM reference: $($_.Exception.Message)"
        }
    }
    # Release only this PowerShell process's COM wrappers. NEVER app.Quit:
    # the global application might have the user's other CST projects.
    if ($null -ne $app) {
        try {
            if ([System.Runtime.InteropServices.Marshal]::IsComObject($app)) {
                [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($app)
            }
        } catch {
            Write-Warning "Could not release CST application COM reference: $($_.Exception.Message)"
        }
    }
    $project = $null
    $app = $null
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
"""


def _cleanup_workdir(work: Path) -> bool:
    """Best-effort cleanup after a CST COM query.

    A background CST process can still hold the copied .cst after the COM
    PowerShell host terminates. Never let WinError 32 overwrite the real CST
    query result or an earlier macro error. Never terminate CST.exe.
    """
    for attempt in range(4):
        try:
            shutil.rmtree(work)
            return True
        except FileNotFoundError:
            return True
        except OSError as exc:
            if attempt == 3:
                warnings.warn(
                    f"P3-C 临时目录仍被占用，已保留供本机稍后清理：{work} "
                    f"（{type(exc).__name__}: {exc}）。"
                    "请确认 CST 已关闭该临时工程，再人工删除；不要终止其他 CST 工程。",
                    RuntimeWarning,
                    stacklevel=2,
                )
                return False
            time.sleep(0.25 * (attempt + 1))
    return False


def query_spatial_from_copy(source: Path, timeout: int = 240) -> SpatialCatalog:
    """Use CST's OLE Project.RunScript as a non-history experimental bridge.

    Always copy the .cst first; never save it, call History, or quit the
    global application. A locked temporary file must not hide the original
    query failure or incorrectly produce a clean PASS.
    """
    if os.name != "nt":
        raise RuntimeError("P3-C OLE 仅支持已安装 CST 的 Windows 系统")
    if timeout < 30 or timeout > 1800:
        raise ValueError("P3-C 超时范围必须为 30～1800 秒")
    source = source.resolve(strict=True)
    if not source.is_file() or source.suffix.casefold() != ".cst":
        raise ValueError("P3-C 输入必须为已有 .cst 工程")
    # Do not use TemporaryDirectory: its context manager raises a second
    # WinError 32 if CST still holds the copied project at exit.
    work = Path(tempfile.mkdtemp(prefix="cst_p3_spatial_"))
    catalog: SpatialCatalog | None = None
    cleanup_ok = False
    try:
        copied = work / source.name
        try:
            shutil.copy2(source, copied)
        except PermissionError as exc:
            raise RuntimeError(
                "P3-C 复制源工程到临时目录时被 Windows 拒绝访问；"
                "请确认原工程没有被其他进程独占，未尝试修改源工程"
            ) from exc
        output = work / "spatial-result.txt"
        script = work / "spatial-query.bas"
        bridge = work / "run-spatial.ps1"
        script.write_text(build_spatial_macro(output), encoding="mbcs")
        bridge.write_text(COM_RUNNER, encoding="utf-8-sig")
        env = dict(os.environ)
        env["CST_P3_SPATIAL_COPY"] = str(copied)
        env["CST_P3_SPATIAL_MACRO"] = str(script)
        try:
            result = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-File", str(bridge),
                ],
                env=env, capture_output=True, text=True,
                errors="replace", timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            # Do not kill CST.exe; it could be a user's active session.
            raise RuntimeError(
                "P3-C COM 脚本超时；请检查 CST 是否有阻塞对话框或运行中的宏，"
                "不要直接终止用户的 CST 进程"
            ) from exc
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"CST P3-C OLE/RunScript 失败：{message[-1600:]}")
        if not output.is_file():
            raise RuntimeError(
                "CST OLE RunScript 没有生成空间查询文件；请确认 RunScript "
                "在当前 CST 2025 SP1 是非 History 命令宏，并查看 CST 消息窗口"
            )
        try:
            content = output.read_text(encoding="mbcs").splitlines()
        except PermissionError as exc:
            raise RuntimeError(
                "P3-C 读取宏输出文件时仍被 CST 占用；"
                "可能是 VBA 未关闭输出文件或尚未执行完毕"
            ) from exc
        catalog = parse_spatial_data(content)
    finally:
        # This cleanup cannot raise, so it never masks a previous error.
        cleanup_ok = _cleanup_workdir(work)
    assert catalog is not None
    if not cleanup_ok:
        return replace(
            catalog, cleanup_deferred=True, cleanup_path=str(work)
        )
    return catalog
