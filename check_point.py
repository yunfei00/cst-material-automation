"""离线判断 STEP/STP 文件中的一个精确 CAD Solid 是否包含指定三维坐标。

无需 CST、无需 P1/P3 报告，仅需 cadquery-ocp。

命令：
    python check_point.py "D:\\CAD\\spring.step" 1.5 2.0 0.7

Python：
    from check_point import check_point
    print(check_point("spring.step", 1.5, 2.0, 0.7))
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.gp import gp_Pnt
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_IN, TopAbs_ON, TopAbs_OUT, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS


def load_solid(step_path: str | Path):
    """读取单个 STEP Solid；拒绝把多个实体误当成一个实体。"""
    path = Path(step_path).resolve(strict=True)
    if path.suffix.casefold() not in {".stp", ".step"}:
        raise ValueError("请输入 STEP/STP 文件（不是 STL、SAB 或 CST）")
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise RuntimeError(f"Open CASCADE 无法读取 STEP 文件：{path}")
    if reader.TransferRoots() < 1:
        raise RuntimeError("STEP 文件没有可转换的几何实体")

    explorer = TopExp_Explorer(reader.OneShape(), TopAbs_SOLID)
    if not explorer.More():
        raise RuntimeError("STEP 文件中没有实体 Solid（可能仅有曲面）")
    solid = TopoDS.Solid_s(explorer.Current())
    explorer.Next()
    if explorer.More():
        raise ValueError("STEP 文件包含多个 Solid；请在 CST 中仅导出目标 Solid")
    return solid


def classify_point(solid, x: float, y: float, z: float, tolerance: float = 1e-6) -> str:
    """返回 INSIDE / ON_BOUNDARY / OUTSIDE；输入坐标须与几何坐标单位一致。"""
    if not all(math.isfinite(float(value)) for value in (x, y, z)):
        raise ValueError("坐标必须是有限数字")
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("tolerance 必须是有限正数")

    classifier = BRepClass3d_SolidClassifier(solid)
    classifier.Perform(gp_Pnt(float(x), float(y), float(z)), tolerance)
    state = classifier.State()
    if state == TopAbs_IN:
        return "INSIDE"
    if state == TopAbs_ON:
        return "ON_BOUNDARY"
    if state == TopAbs_OUT:
        return "OUTSIDE"
    raise RuntimeError(f"几何分类器返回未知状态：{state}")


def check_point(step_file: str | Path, x: float, y: float, z: float) -> str:
    """最简单的接口：STEP 文件 + 3D 坐标 → 判断结果。"""
    return classify_point(load_solid(step_file), x, y, z)


def main() -> int:
    parser = argparse.ArgumentParser(description="离线判断一个坐标是否位于 STEP Solid 内")
    parser.add_argument("step_file", help="从 CST 导出的单个 Solid 的 .step/.stp 文件")
    parser.add_argument("x", type=float)
    parser.add_argument("y", type=float)
    parser.add_argument("z", type=float)
    parser.add_argument("--tolerance", type=float, default=1e-6)
    args = parser.parse_args()
    result = classify_point(
        load_solid(args.step_file), args.x, args.y, args.z, args.tolerance
    )
    explanations = {
        "INSIDE": "在实体内部",
        "ON_BOUNDARY": "在实体表面或容差范围内",
        "OUTSIDE": "在实体外部",
    }
    print(f"结果：{result}（{explanations[result]}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
