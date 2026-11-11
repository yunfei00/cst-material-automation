"""Chinese command-line entry points for a pre-existing CST project."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from cst_material.cst_api import assign_and_verify, connect, read_inventory
from cst_material.cst_bindings import binding


def _report(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告路径：{path}")
    print(f"执行结果：{data['status']}")


def _snapshot(inventory) -> dict:
    return {
        "solid_count": len(inventory.solids),
        "material_count": len(inventory.materials),
        "solids": [asdict(item) for item in inventory.solids],
        "materials": [asdict(item) for item in inventory.materials],
    }


def _run_inventory(source: Path, output: dict) -> None:
    if not source.is_file() or source.suffix.lower() != ".cst":
        raise ValueError("输入必须是已经存在的 .cst 工程文件")
    # Read-only guarantee: all model actions happen on a throwaway copy.
    with tempfile.TemporaryDirectory(prefix="cst_inventory_") as tmp:
        copied = Path(tmp) / source.name
        shutil.copy2(source, copied)
        env = connect()
        try:
            project = env.open_project(str(copied))
            try:
                bindings = binding()
                model = project.model3d
                if model is None:
                    raise RuntimeError("CST 工程不包含三维模型")
                output["tree_items"] = list(model.get_tree_items())
                inventory = read_inventory(project, bindings)
                output.update(_snapshot(inventory))
                try:
                    query = bindings.read_assigned_materials
                    assert query is not None
                    assigned = query(
                        project, tuple(item.identifier for item in inventory.solids)
                    )
                    for row in output["solids"]:
                        row["material"] = assigned.get(row["identifier"])
                except Exception as exc:
                    output["warnings"] = [
                        "三维实体当前材料查询失败；结构及材料清单已读取。"
                        f"请核对 CST 2025 的 Solid VBA 方法：{exc}"
                    ]
            finally:
                project.close()
        finally:
            env.close()
    output["status"] = "PARTIAL" if output.get("warnings") else "PASS"


def _mapping(args: argparse.Namespace) -> dict[str, str]:
    result: dict[str, str] = {}
    if args.mapping:
        data = json.loads(Path(args.mapping).read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in data.items()
        ):
            raise ValueError("映射 JSON 必须为 { \"Component:Solid\": \"材料名称\" }")
        result.update(data)
    if args.solid is not None or args.material is not None:
        if not args.solid or not args.material:
            raise ValueError("--solid 和 --material 必须同时提供")
        if args.solid in result and result[args.solid] != args.material:
            raise ValueError("同一 Solid 指定了冲突的材料")
        result[args.solid] = args.material
    if not result:
        raise ValueError("P2 必须提供 --solid/--material 或 --mapping")
    return result


def _run_assign(args: argparse.Namespace, output: dict) -> None:
    assignments = _mapping(args)
    env = connect()
    try:
        inventory = assign_and_verify(
            env, Path(args.source), Path(args.dest), assignments, binding()
        )
    finally:
        env.close()
    output.update(_snapshot(inventory))
    output["assignments"] = assignments
    output["destination"] = str(Path(args.dest).resolve())
    output["status"] = "PASS"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cst_material.cli", description="已有 CST 工程的结构读取与材料赋值"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    p1 = commands.add_parser("inventory", help="P1：读取已有工程的结构、材料及对应关系")
    p1.add_argument("--source", required=True, help="已有 CST 工程的绝对路径")
    p1.add_argument("--report", default="P1_Report.json", help="本地 JSON 报告路径")
    p2 = commands.add_parser("assign", help="P2：在工程副本中修改 Solid 材料并回读")
    p2.add_argument("--source", required=True)
    p2.add_argument("--dest", required=True, help="新输出路径，不能已存在")
    p2.add_argument("--solid", help="来自 P1 的 Solid.identifier")
    p2.add_argument("--material", help="来自 P1 的材料名称")
    p2.add_argument("--mapping", help="JSON 文件，键为 Solid.identifier，值为已有材料名称")
    p2.add_argument("--report", default="P2_Report.json")
    args = parser.parse_args(argv)
    output: dict = {
        "task": args.command,
        "source": str(Path(args.source).resolve()),
        "status": "FAIL",
    }
    try:
        if args.command == "inventory":
            _run_inventory(Path(args.source), output)
        else:
            _run_assign(args, output)
    except Exception as exc:
        output["error"] = f"{type(exc).__name__}: {exc}"
        print(f"失败：{output['error']}", file=sys.stderr)
    _report(Path(args.report), output)
    return 0 if output["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
