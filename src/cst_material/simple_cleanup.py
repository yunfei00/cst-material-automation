r"""只需输入与输出 CST 路径的零体积 Solid 清理接口。

Python 用法:
    from cst_material.simple_cleanup import clean_project
    result = clean_project(r"D:\old.cst", r"D:\new.cst")

无需 P1/P3-A 报告、规则 JSON 或现有项目清理流水线。
在独立临时副本上现场读取 Solid 和体积，再在新输出工程副本中
执行 CST 2025 Solid.Delete、保存、回读。源工程始终只读。
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import tempfile
import time
import traceback
import warnings
from pathlib import Path

def _sha256_file(file: Path) -> str:
    checksum = hashlib.sha256()
    with file.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def _close_project(project) -> None:
    if project is not None:
        project.close()


def _discard_temp_copy(folder: Path) -> str | None:
    """Do not overwrite a real failure when CST delays releasing dp.parmap."""
    for attempt in range(20):
        try:
            shutil.rmtree(folder)
            return None
        except FileNotFoundError:
            return None
        except OSError as exc:
            if attempt == 19:
                return f"临时预检副本可能仍被 CST 占用：{folder}；{exc}"
            time.sleep(0.5)
    return None


def _default_delete(project, solid_names: list[str], batch_size: int) -> None:
    from cst_material.cst_bindings import _modeler, _vba_string

    for offset in range(0, len(solid_names), batch_size):
        chunk = solid_names[offset:offset + batch_size]
        commands = "\n".join(
            f"Solid.Delete {_vba_string(identifier)}" for identifier in chunk
        )
        try:
            _modeler(project).add_to_history(
                f"Clean zero-volume Solid {offset + 1}-{offset + len(chunk)}",
                commands,
            )
        except Exception as exc:
            raise RuntimeError(
                f"删除第 {offset + 1}～{offset + len(chunk)} 个 Solid 时 CST 报错，"
                "输出副本未通过验收"
            ) from exc
        print(f"已删除 {offset + len(chunk)} / {len(solid_names)}", flush=True)


def _existing_shapes(project) -> dict[str, str | None]:
    from cst_material.cst_bindings import read_shape_catalog

    catalog = read_shape_catalog(project)
    if not catalog:
        raise RuntimeError("CST 工程没有读取到 Solid")
    return {name: material or None for name, material in catalog.items()}


def _volume_features(project, shapes: dict[str, str | None]):
    """Reuse the real CST 2025 GetVolume query, NOT a previous report."""
    from cst_material.cst_api import ProjectInventory
    from cst_material.domain import Solid
    from cst_material.p3_geometry import read_geometry_features

    inventory = ProjectInventory(
        solids=tuple(
            Solid(
                identifier=name,
                component=name.rpartition(":")[0] or None,
                material=material,
                bounds=None,
            )
            for name, material in shapes.items()
        ),
        materials=(),
    )
    return read_geometry_features(project, inventory)


def clean_project(
    input_cst: str | Path,
    output_cst: str | Path,
    *,
    batch_size: int = 50,
    environment=None,
    catalog_reader=None,
    geometry_reader=None,
    delete_executor=None,
) -> dict:
    """CST 输入工程 -> 删除全部精确 V=0 Solid -> 新工程和日志。

    Only two required arguments. The injectable hooks are used by automated
    synthetic tests, not needed by a user running CST 2025 on Windows.

    A returned PASS proves saved output has *exactly* the expected remaining
    Solid identifiers and existing materials; it does not certify simulation
    settings or guarantee GUI rendering performance improvements.
    """
    from cst_material.cst_api import connect

    src = Path(input_cst).resolve(strict=True)
    dst = Path(output_cst).resolve()
    if not src.is_file() or src.suffix.casefold() != ".cst":
        raise ValueError("输入必须是已存在的 .cst 工程")
    if dst.suffix.casefold() != ".cst":
        raise ValueError("输出必须是 .cst 工程")
    if src == dst:
        raise ValueError("不能覆盖原始 CST 工程")
    if dst.exists():
        raise FileExistsError(f"输出文件已经存在，禁止覆盖：{dst}")
    if not dst.parent.is_dir():
        raise FileNotFoundError(f"输出目录不存在：{dst.parent}")
    if not isinstance(batch_size, int) or not 1 <= batch_size <= 100:
        raise ValueError("batch_size 必须在 1～100 之间")

    log_path = dst.with_suffix(".cleanup.json")
    if log_path.exists() or log_path == src:
        raise FileExistsError(f"删除日志已存在，禁止覆盖：{log_path}")

    get_catalog = catalog_reader or _existing_shapes
    get_geometry = geometry_reader or _volume_features
    remove = delete_executor or _default_delete
    report: dict = {
        "status": "FAIL",
        "input": str(src),
        "output": str(dst),
        "log": str(log_path),
        "rule": "R001: Solid.GetVolume == 0",
        "deleted_ids": [],
        "stage": "准备",
    }
    preflight_dir = Path(tempfile.mkdtemp(prefix="cst_zero_volume_"))
    preflight = preflight_dir / src.name
    env = None
    owned_env = environment is None
    project = None
    success = False
    try:
        before_hash = _sha256_file(src)
        shutil.copy2(src, preflight)
        env = environment if environment is not None else connect()

        report["stage"] = "CST 读取当前 Solid 和真实体积"
        project = env.open_project(str(preflight))
        try:
            original_shapes = get_catalog(project)
            if not isinstance(original_shapes, dict) or not original_shapes:
                raise RuntimeError("CST 没有读取到有效 Solid 清单")
            if len(original_shapes) != len(set(original_shapes)):
                raise RuntimeError("CST Solid 名称出现重复")
            geometry = get_geometry(project, original_shapes)
            features = geometry.features
            if len(features) != len(original_shapes):
                raise RuntimeError("读取体积数量与当前 Solid 数量不一致")
            volumes: dict[str, float] = {}
            for feature in features:
                name, volume = feature.identifier, feature.volume_project_unit3
                if name not in original_shapes or name in volumes:
                    raise RuntimeError("体积记录与当前 Solid 名称不一致或重复")
                if (
                    isinstance(volume, bool)
                    or not isinstance(volume, (int, float))
                    or not math.isfinite(volume)
                    or volume < 0
                ):
                    raise RuntimeError(f"Solid {name!r} 的体积不是有限非负数")
                volumes[name] = volume
            if len(volumes) != len(original_shapes):
                raise RuntimeError("体积查询漏掉了 Solid")
            delete_ids = sorted(
                name for name, volume in volumes.items() if volume == 0
            )
            report["original_solid_count"] = len(original_shapes)
            report["deleted_solid_count"] = len(delete_ids)
            report["deleted_ids"] = delete_ids
            report["remaining_solid_count_expected"] = (
                len(original_shapes) - len(delete_ids)
            )
            print(
                f"原 Solid：{len(original_shapes)}，"
                f"体积为零：{len(delete_ids)}，"
                f"预计保留：{len(original_shapes) - len(delete_ids)}",
                flush=True,
            )
        finally:
            _close_project(project)
            project = None

        if not delete_ids:
            # No edits needed: byte-for-byte same valid source project.
            with src.open("rb") as read, dst.open("xb") as write:
                shutil.copyfileobj(read, write, length=4 * 1024 * 1024)
            if _sha256_file(dst) != before_hash:
                raise RuntimeError("无删除项时输出副本 SHA256 校验失败")
            report["stage"] = "没有零体积 Solid，已复制原工程"
        else:
            if len(delete_ids) >= len(original_shapes):
                raise RuntimeError("零体积 Solid 覆盖全部工程；拒绝生成空工程")
            if _sha256_file(src) != before_hash:
                raise RuntimeError("处理期间源工程发生变化，停止执行")
            # Destination is created only after the complete CST volume read.
            with src.open("rb") as read, dst.open("xb") as write:
                shutil.copyfileobj(read, write, length=4 * 1024 * 1024)
            report["stage"] = "在新 CST 工程副本删除零体积 Solid"
            project = env.open_project(str(dst))
            remove(project, delete_ids, batch_size)
            project.save(str(dst), allow_overwrite=True)
            _close_project(project)
            project = None

            report["stage"] = "重新打开新工程核对剩余 Solid 与 Material"
            project = env.open_project(str(dst))
            after_shapes = get_catalog(project)
            expected = {
                name: material for name, material in original_shapes.items()
                if name not in delete_ids
            }
            if after_shapes != expected:
                missing = sorted(expected.keys() - after_shapes.keys())
                extras = sorted(after_shapes.keys() - expected.keys())
                changed_material = sorted(
                    name for name in expected.keys() & after_shapes.keys()
                    if expected[name] != after_shapes[name]
                )
                raise RuntimeError(
                    "删除后的 CST 回读不一致："
                    f"应保留却消失={missing[:5]}，"
                    f"意外新增/漏删={extras[:5]}，"
                    f"材料变化={changed_material[:5]}"
                )
        if _sha256_file(src) != before_hash:
            raise RuntimeError("原工程在处理过程中发生变化，停止报告 PASS")
        report["status"] = "PASS"
        report["stage"] = "完成：输出 CST 已保存并通过剩余实体及材料核对"
        report["all_remaining_materials_unchanged"] = True
        success = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
        raise
    finally:
        if project is not None:
            try:
                _close_project(project)
            except Exception as exc:  # noqa: BLE001 - CST vendor-specific exceptions
                warnings.warn(
                    f"CST 工程关闭时报告异常：{exc}", RuntimeWarning, stacklevel=2
                )
        if owned_env and env is not None:
            try:
                env.close()
            except Exception as exc:
                warnings.warn(
                    f"CST 接口关闭时报告异常：{exc}", RuntimeWarning, stacklevel=2
                )
        warning = _discard_temp_copy(preflight_dir)
        if warning:
            report["temp_cleanup_warning"] = warning
        # Log must describe failures too, but never obscure the original CST
        # traceback or delete a possibly locked/partially written CST output.
        try:
            with log_path.open("x", encoding="utf-8") as out:
                json.dump(report, out, ensure_ascii=False, indent=2)
        except OSError as exc:
            warnings.warn(
                f"无法写清理日志 {log_path}：{exc}", RuntimeWarning, stacklevel=2
            )
    if success:
        print(f"完成：{dst}", flush=True)
        print(f"删除记录：{log_path}", flush=True)
    return report
