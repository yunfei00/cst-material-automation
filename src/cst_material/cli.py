"""Chinese command-line entry points for a pre-existing CST project."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import traceback
from dataclasses import asdict
from pathlib import Path

from cst_material.cst_api import assign_and_verify, connect, read_inventory
from cst_material.cst_bindings import binding
from cst_material.p3_geometry import read_geometry_features
from cst_material.p3_mtd_catalog import scan_mtd_library
from cst_material.p3_neighbors import analyze_neighbors, read_spatial_report
from cst_material.p3_spatial import query_spatial_from_copy, summarize_spatial


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
    output["stage"] = "复制测试工程副本"
    with tempfile.TemporaryDirectory(prefix="cst_inventory_") as tmp:
        copied = Path(tmp) / source.name
        shutil.copy2(source, copied)
        output["stage"] = "连接 CST Python 接口"
        env = connect()
        try:
            output["stage"] = "打开 CST 工程副本"
            project = env.open_project(str(copied))
            try:
                bindings = binding()
                model = project.model3d
                if model is None:
                    raise RuntimeError("CST 工程不包含三维模型")
                # CST's Python tree reader may raise UnicodeDecodeError for
                # Chinese GBK/CP936 names. Read both catalogs via documented
                # VBA APIs instead; no get_tree_items() call here.
                output["stage"] = "CST VBA 枚举 Solid 和 Material"
                inventory = read_inventory(project, bindings)
                output.update(_snapshot(inventory))
                output["vba_solid_count"] = len(inventory.solids)
                output["inventory_source"] = "CST VBA Solid/Material catalogs"
            finally:
                project.close()
        finally:
            env.close()
    output["status"] = "PARTIAL" if output.get("warnings") else "PASS"
    output["stage"] = "读取完成"


def _run_geometry(source: Path, output: dict) -> None:
    """P3-A. Only the disposable CST project copy is opened or queried."""
    if not source.is_file() or source.suffix.lower() != ".cst":
        raise ValueError("P3-A 输入必须是已有的 .cst 工程文件")
    output["stage"] = "创建 P3 几何查询临时工程副本"
    with tempfile.TemporaryDirectory(prefix="cst_p3_geometry_") as tmp:
        copied = Path(tmp) / source.name
        shutil.copy2(source, copied)
        output["stage"] = "连接 CST Python 接口"
        env = connect()
        try:
            output["stage"] = "打开 P3 临时工程副本"
            project = env.open_project(str(copied))
            try:
                output["stage"] = "CST VBA 读取 Solid 和 Material"
                inventory = read_inventory(project, binding())
                output["stage"] = "CST VBA 查询 Solid 体积与表面积"
                catalog = read_geometry_features(project, inventory)
                output["geometry"] = {
                    "length_unit": catalog.length_unit,
                    "length_unit_to_m": catalog.length_unit_to_m,
                    "features": [asdict(item) for item in catalog.features],
                }
                output["solid_count"] = len(catalog.features)
                output["zero_volume_count"] = sum(
                    item.zero_volume for item in catalog.features
                )
                output["geometry_scope"] = (
                    "仅体积、表面积和紧致度；CST 官方禁止在 History "
                    "中调用 GetLooseBoundingBoxOfShape，故未查询尺寸与位置"
                )
            finally:
                project.close()
        finally:
            env.close()
    output["status"] = "PASS"
    output["stage"] = "P3-A 几何数据读取完成"


def _run_mtd_scan(library_root: Path, report_path: Path, output: dict) -> None:
    """P3-B. File metadata only; never open CST or modify external materials."""
    output["stage"] = "扫描外部 MTD 材料库"
    root = library_root.resolve(strict=True)
    if report_path.resolve().is_relative_to(root):
        raise ValueError("报告路径不能放在外部 MTD 材料库内部，避免修改材料库")
    catalog = scan_mtd_library(root)
    output["root"] = catalog.root
    output["mtd_count"] = len(catalog.entries)
    output["files"] = [asdict(entry) for entry in catalog.entries]
    output["duplicate_candidate_names"] = catalog.duplicate_candidate_names
    output["skipped_symlinks"] = catalog.skipped_symlinks
    output["catalog_scope"] = (
        "仅文件名候选、相对路径、大小、修改时间与 SHA256；"
        "不解析 MTD 物性，不保证文件名等于 CST 内部材料名称，"
        "不会导入 CST 工程"
    )
    if catalog.duplicate_candidate_names:
        output["warnings"] = ["发现同名候选材料文件，后续匹配必须人工确认"]
    if catalog.skipped_symlinks:
        output.setdefault("warnings", []).append("已跳过符号链接，避免扫描到库外")
    output["status"] = "PASS"
    output["stage"] = "P3-B MTD 文件目录建立完成"


def _run_spatial(args: argparse.Namespace, output: dict) -> None:
    """P3-C: experiment with official OLE RunScript, never CST History."""
    output["stage"] = "CST P3-C 非 History OLE 命令宏空间查询"
    catalog = query_spatial_from_copy(Path(args.source), timeout=args.timeout)
    output["spatial"] = summarize_spatial(catalog, args.neighbors)
    output["status"] = (
        "PARTIAL"
        if catalog.missing_bbox_count or catalog.cleanup_deferred
        else "PASS"
    )
    if catalog.cleanup_deferred:
        output["warnings"] = [
            (
                "CST 可能仍占用临时工程：查询结果已读取，但临时文件清理未完成；"
                "请先关闭该临时工程，再根据 cleanup_path 在本机人工清理。"
            )
        ]
    output["stage"] = "P3-C 空间查询完成"


def _run_spatial_offline(args: argparse.Namespace, output: dict) -> None:
    """P3-D: reuse local P3-C JSON without starting CST or touching its project."""
    output["stage"] = "P3-D 读取并校验已有 P3-C JSON 报告"
    catalog, provenance = read_spatial_report(Path(args.report_in))
    output["input_summary"] = {
        **provenance,
        "solid_count": catalog.declared_solid_count,
        "valid_bbox_count": catalog.declared_solid_count - catalog.missing_bbox_count,
        "length_unit": catalog.length_unit,
        "length_unit_to_m": catalog.length_unit_to_m,
    }
    output["stage"] = "P3-D 分批计算非紧包围盒邻近候选"
    output["neighbors"] = analyze_neighbors(
        catalog, args.neighbors, batch_size=args.batch_size
    )
    if output["neighbors"]["status"] == "PASS":
        output["status"] = "PARTIAL" if catalog.missing_bbox_count else "PASS"
    else:
        output["status"] = "PARTIAL"
    output["scope"] = (
        "只根据已有非紧包围盒计算距离下界，不代表真实接触、"
        "精确实体间距、质量中心或材料类型。"
        "不启动 CST，不修改输入 JSON 或工程。"
    )
    output["stage"] = "P3-D 离线邻近分析完成"


def _mapping(args: argparse.Namespace) -> dict[str, str]:
    result: dict[str, str] = {}
    if args.mapping:
        try:
            data = json.loads(Path(args.mapping).read_text(encoding="utf-8-sig"))
        except UnicodeDecodeError as exc:
            raise ValueError(
                "材料映射 JSON 文件不是 UTF-8 编码；请在本机将该 JSON 另存为 UTF-8，"
                "不要直接使用 errors=ignore 丢弃中文字符"
            ) from exc
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
    output["stage"] = "读取 Solid→Material 映射"
    assignments = _mapping(args)
    output["stage"] = "连接 CST Python 接口"
    env = connect()
    try:
        output["stage"] = "CST 副本赋材与重新打开核对"
        inventory = assign_and_verify(
            env, Path(args.source), Path(args.dest), assignments, binding()
        )
    finally:
        env.close()
    output.update(_snapshot(inventory))
    output["assignments"] = assignments
    output["destination"] = str(Path(args.dest).resolve())
    output["status"] = "PASS"
    output["stage"] = "赋材与回读完成"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cst_material.cli", description="已有 CST 工程的结构读取与材料赋值"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    p1 = commands.add_parser("inventory", help="P1：读取已有工程的结构、材料及对应关系")
    p1.add_argument("--source", required=True, help="已有 CST 工程的绝对路径")
    p1.add_argument("--report", default="local-data/P1_Report.json", help="本地 JSON 报告路径")
    p2 = commands.add_parser("assign", help="P2：在工程副本中修改 Solid 材料并回读")
    p2.add_argument("--source", required=True)
    p2.add_argument("--dest", required=True, help="新输出路径，不能已存在")
    p2.add_argument("--solid", help="来自 P1 的 Solid.identifier")
    p2.add_argument("--material", help="来自 P1 的材料名称")
    p2.add_argument("--mapping", help="JSON 文件，键为 Solid.identifier，值为已有材料名称")
    p2.add_argument("--report", default="local-data/P2_Report.json")
    p3a = commands.add_parser(
        "geometry", help="P3-A：只读提取已有 CST 工程 Solid 体积/表面积"
    )
    p3a.add_argument("--source", required=True, help="已有 CST 工程的绝对路径")
    p3a.add_argument("--report", default="local-data/P3A_Geometry_Report.json")
    p3b = commands.add_parser(
        "scan-mtd", help="P3-B：只读扫描外部 .mtd 材料库文件信息（不导入 CST）"
    )
    p3b.add_argument("--library-root", required=True, help="外部材料库目录")
    p3b.add_argument("--report", default="local-data/P3B_MTD_Catalog.json")
    p3c = commands.add_parser(
        "spatial", help="P3-C（实验性）：用 CST OLE 命令宏查询非紧包围盒"
    )
    p3c.add_argument("--source", required=True, help="已有 CST 工程的绝对路径")
    p3c.add_argument("--report", default="local-data/P3C_Spatial_Report.json")
    p3c.add_argument("--timeout", type=int, default=240, help="COM/VBA 查询超时，秒")
    p3c.add_argument("--neighbors", type=int, default=5, help="每个 Solid 最近的包围盒候选数量 0～20")
    p3d = commands.add_parser(
        "spatial-offline",
        help="P3-D：直接从 P3-C JSON 分批计算最近邻，不启动 CST",
    )
    p3d.add_argument("--report-in", required=True, help="已有 P3-C spatial 报告")
    p3d.add_argument("--report", default="local-data/P3D_Neighbors_Report.json")
    p3d.add_argument("--neighbors", type=int, default=5, help="每个 Solid 最近的候选数量 0～20")
    p3d.add_argument("--batch-size", type=int, default=32, help="NumPy 单批处理 1～128 个 Solid")
    args = parser.parse_args(argv)
    # A typo in --report must never overwrite an engineering project or MTD.
    # This also covers the P1/P2 report commands without altering workflows.
    report_file = Path(args.report).resolve()
    if report_file.suffix.casefold() in {".cst", ".mtd", ".sat", ".brd", ".step", ".stp"}:
        print("拒绝将 JSON 报告写入工程/材料文件路径", file=sys.stderr)
        return 2
    # Prevent the JSON error reporter from overwriting the only source.
    if args.command == "spatial-offline" and report_file == Path(args.report_in).resolve():
        print("P3-D 输出报告不能覆盖输入 P3-C 报告", file=sys.stderr)
        return 2
    if args.command == "scan-mtd":
        # Reject unsafe output *before* the generic error reporter writes a
        # FAIL JSON to the same path. Never write into an MTD library.
        library_root = Path(args.library_root).resolve()
        report_file = Path(args.report).resolve()
        if report_file.is_relative_to(library_root):
            print("拒绝将报告写入材料库内部；请选择 local-data 目录", file=sys.stderr)
            return 2
    if args.command == "scan-mtd":
        source = args.library_root
    elif args.command == "spatial-offline":
        source = args.report_in
    else:
        source = args.source
    output: dict = {
        "task": args.command,
        "source": str(Path(source).resolve()),
        "status": "FAIL",
    }
    try:
        if args.command == "inventory":
            _run_inventory(Path(args.source), output)
        elif args.command == "assign":
            _run_assign(args, output)
        elif args.command == "geometry":
            _run_geometry(Path(args.source), output)
        elif args.command == "spatial":
            _run_spatial(args, output)
        elif args.command == "spatial-offline":
            _run_spatial_offline(args, output)
        else:
            _run_mtd_scan(Path(args.library_root), Path(args.report), output)
    except Exception as exc:  # noqa: BLE001 - CST raises vendor-specific exceptions
        output["error"] = f"{type(exc).__name__}: {exc}"
        # The full Python traceback is only written to the local report in
        # local-data/ (gitignored); do not upload it from company workstations.
        output["traceback"] = traceback.format_exc()
        if isinstance(exc, UnicodeDecodeError):
            output["encoding_error"] = {
                "encoding": exc.encoding,
                "invalid_bytes_hex": exc.object[exc.start : exc.end].hex(),
                "start": exc.start,
                "end": exc.end,
                "reason": exc.reason,
            }
        print(f"失败阶段：{output.get('stage', '未知')}", file=sys.stderr)
        print(f"失败：{output['error']}", file=sys.stderr)
    _report(Path(args.report), output)
    return 0 if output["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
