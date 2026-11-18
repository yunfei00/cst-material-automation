"""Pilot exact CST Solid-name mapping for ONE leaf component.

Experimental Solid.Delete is executed only on disposable copies. Never guess
the mapping from STEP body order. No original CST file is modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from cst_material.cst_api import connect
from cst_material.cst_bindings import _modeler, read_shape_catalog
from cst_material.occt_geometry import read_step, sha256_file
from cst_material.solid_name_mapping import isolated_export_vba


def pilot(project_file: str | Path, component: str, output_dir: str | Path) -> dict:
    source = Path(project_file).resolve(strict=True)
    if source.suffix.lower() != ".cst":
        raise ValueError("需要 CST 工程")
    root = Path(output_dir).resolve()
    if root.exists():
        raise FileExistsError(root)
    root.mkdir(parents=True)
    (root / "steps").mkdir()
    report = {"schema_version": 1, "component": component, "source": str(source),
              "solids": [], "errors": [], "status": "INCOMPLETE"}
    try:
        # First read only the exact source Solid identities on a disposable copy.
        with tempfile.TemporaryDirectory(prefix="cst_mapping_catalog_") as tmp:
            env = connect()
            project = None
            try:
                project = env.open_project(str(shutil.copy2(source, Path(tmp) / source.name)))
                catalog = read_shape_catalog(project)
            finally:
                if project is not None:
                    project.close()
                env.close()
        siblings = [name for name in catalog if name.rsplit(":", 1)[0] == component]
        if not siblings:
            raise ValueError("未发现指定叶子组件的 Solid")
        report["expected"] = len(siblings)
        for index, sid in enumerate(siblings):
            name_hash = hashlib.sha256(sid.encode("utf-8")).hexdigest()[:16]
            step = root / "steps" / f"{index:04d}_{name_hash}.step"
            with tempfile.TemporaryDirectory(prefix="cst_mapping_isolate_") as tmp:
                env = None
                project = None
                try:
                    # Each target gets a fresh copy: deletes cannot accumulate.
                    copy = shutil.copy2(source, Path(tmp) / source.name)
                    env = connect()
                    project = env.open_project(str(copy))
                    _modeler(project).add_to_history(
                        "CST mapping pilot: isolate ONE Solid on disposable copy",
                        isolated_export_vba(sid, siblings, step),
                    )
                    bodies = read_step(step)
                    if len(bodies) != 1:
                        raise RuntimeError(
                            f"隔离后 STEP 仍有 {len(bodies)} 个实体；要求恰好一个"
                        )
                    # Verify actual source shape still exists in the isolated copy.
                    remaining = read_shape_catalog(project)
                    if sid not in remaining or any(
                        other in remaining for other in siblings if other != sid
                    ):
                        raise RuntimeError("CST 临时工程的隔离结果与目标名称不一致")
                    report["solids"].append({
                        "solid_id": sid, "step": step.relative_to(root).as_posix(),
                        "sha256": sha256_file(step),
                        "verification": "ISOLATED_SINGLE_BODY",
                    })
                except Exception as exc:  # noqa: BLE001 - pilot must preserve failure details
                    step.unlink(missing_ok=True)
                    report["errors"].append({
                        "solid_id": sid, "error": f"{type(exc).__name__}: {exc}"
                    })
                finally:
                    if project is not None:
                        project.close()
                    if env is not None:
                        env.close()
        report["status"] = ("PASS" if len(report["solids"]) == len(siblings)
                            and not report["errors"] else "PARTIAL")
        if report["status"] == "PASS":
            (root / "verified_solid_mapping.json").write_text(
                json.dumps({
                    "schema_version": 1, "solids": report["solids"],
                }, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    except Exception as exc:  # noqa: BLE001 - preserve failure
        report["errors"].append({"error": f"{type(exc).__name__}: {exc}"})
        report["status"] = "FAIL"
    finally:
        (root / "pilot_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="单叶子组件 Solid 精确名称隔离导出试验")
    parser.add_argument("project")
    parser.add_argument("component", help="完整 CST 组件路径")
    parser.add_argument("output", help="新建输出目录（不得已存在）")
    args = parser.parse_args()
    result = pilot(args.project, args.component, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
