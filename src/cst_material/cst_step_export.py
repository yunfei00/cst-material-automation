"""CST 2025 STEP export adapter: exact per-Solid IDs, no ordering inference.

The VBA STEP.Write command is documented in the CST VBA import/export family,
but per-shape export and global coordinates still require actual CST 2025 SP1
validation. On any unexpected result the adapter FAILS instead of making
a fictitious geometry/Solid mapping.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
import warnings
from pathlib import Path

from cst_material.occt_geometry import sha256_file


def _export_step_batch(project, items: list[tuple[str, Path]]) -> None:
    from cst_material.cst_bindings import _modeler, _vba_string

    # One History operation per Solid: identify the exact failing exporter.
    # Do not silently skip failures or claim a complete geometry database.
    for name, target in items:
        component, sep, solid = name.partition(":")
        if not sep or not component or not solid:
            raise ValueError(f"Solid ID 必须为 Component:Solid：{name}")
        if target.exists():
            raise FileExistsError(target)
        code = "\\n".join([
            "With STEP",
            "    .Reset",
            f"    .FileName {_vba_string(str(target))}",
            "    .ExportFromActiveCoordinateSystem False",
            f"    .Write {_vba_string(component + '/' + solid)}",
            "End With",
        ])
        try:
            _modeler(project).add_to_history(
                "CST Material Automation: export one Solid STEP", code,
            )
        except Exception as exc:
            raise RuntimeError(
                f"STEP 导出失败：Solid={name!r}；目标={target}；"
                f"History 命令={code!r}；原始错误={type(exc).__name__}: {exc}"
            ) from exc
        if not target.is_file() or target.stat().st_size < 100:
            raise RuntimeError(
                f"STEP 导出命令执行后文件缺失或过小：Solid={name!r}；目标={target}"
            )


def export_cst_solids(
    source: str | Path,
    output_dir: str | Path,
    *,
    environment=None,
    catalog_reader=None,
    batch_exporter=None,
    batch_size: int = 20,
) -> dict:
    """Export one STEP per shape and generate verified name/path manifest.

    Two paths required; injections support CI only. Output is a directory.
    No source project is opened. No historical P1/P3 files are needed.
    """
    from cst_material.cst_api import connect
    from cst_material.cst_bindings import read_shape_catalog

    src = Path(source).resolve(strict=True)
    root = Path(output_dir).resolve()
    if not src.is_file() or src.suffix.lower() != ".cst":
        raise ValueError("必须输入现有的 CST .cst 工程")
    if root.exists():
        raise FileExistsError(f"几何数据库已存在，禁止覆盖：{root}")
    if not root.parent.is_dir():
        raise FileNotFoundError(root.parent)
    if not isinstance(batch_size, int) or not 1 <= batch_size <= 50:
        raise ValueError("批次大小必须在 1～50 之间")
    root.mkdir()
    (root / "steps").mkdir()
    start = time.perf_counter()
    project = None
    temp = Path(tempfile.mkdtemp(prefix="cst_step_export_"))
    environment_owned = environment is None
    env = None
    exported = []
    current_solid = None
    try:
        staged = temp / src.name
        src_hash = sha256_file(src)
        shutil.copy2(src, staged)
        env = environment if environment is not None else connect()
        project = env.open_project(str(staged))
        original = (catalog_reader or read_shape_catalog)(project)
        if not isinstance(original, dict) or not original:
            raise RuntimeError("CST Solid 目录为空")
        files: list[tuple[str, Path]] = []
        for idx, (name, material) in enumerate(original.items()):
            if not isinstance(name, str) or ":" not in name:
                raise ValueError(f"无法保留完整的 Solid 身份：{name!r}")
            stem = hashlib.sha256(name.encode("utf-8")).hexdigest()[:20]
            target = root / "steps" / f"{idx:06d}_{stem}.step"
            files.append((name, target))
        if len({path for _, path in files}) != len(files):
            raise RuntimeError("导出文件名称碰撞")
        run_exporter = batch_exporter or _export_step_batch
        for offset in range(0, len(files), batch_size):
            chunk = files[offset:offset + batch_size]
            for current_solid, current_target in chunk:
                run_exporter(project, [(current_solid, current_target)])
            for name, target in chunk:
                if not target.is_file() or target.stat().st_size < 100:
                    raise RuntimeError(f"CST 未正确导出 STEP：{name} → {target}")
                exported.append({
                    "solid_id": name,
                    "material": original[name],
                    "step": target.relative_to(root).as_posix(),
                    "sha256": sha256_file(target),
                })
            print(f"STEP 导出进度：{len(exported)}/{len(files)}", flush=True)
        if sha256_file(src) != src_hash:
            raise RuntimeError("CST 原工程在导出期间发生改变")
        manifest = {
            "schema_version": 1,
            "unit": "mm",
            "coordinate_frame": "CST_GLOBAL_EXPORT_REQUESTED_NOT_YET_REAL_VERIFIED",
            "source": str(src),
            "source_sha256": src_hash,
            "elapsed_seconds": round(time.perf_counter() - start, 3),
            "solid_count": len(exported),
            "solids": exported,
        }
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return manifest
    except Exception as exc:
        (root / "export_error.json").write_text(
            json.dumps({
                "status": "FAIL",
                "error": f"{type(exc).__name__}: {exc}",
                "exported_count": len(exported),
                "failed_solid": current_solid,
                "exception_type": type(exc).__name__,
                "note": "不要将残留 STEP 当作完整索引",
            }, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        raise
    finally:
        if project is not None:
            try:
                project.close()
            except Exception as exc:  # noqa: BLE001 - CST vendor errors
                warnings.warn(f"CST 工程关闭失败：{exc}", RuntimeWarning, stacklevel=2)
        if environment_owned and env is not None:
            try:
                env.close()
            except Exception as exc:  # noqa: BLE001 - vendor-specific CST errors
                warnings.warn(f"CST 环境关闭失败：{exc}", RuntimeWarning, stacklevel=2)
        try:
            shutil.rmtree(temp)
        except OSError as exc:
            warnings.warn(
                f"CST 临时工程仍占用，留在本地：{temp}：{exc}",
                RuntimeWarning, stacklevel=2,
            )
