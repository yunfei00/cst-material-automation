"""Exact B-rep point grid scan; CSV rows are x_mm,y_mm,z_mm,value."""
from __future__ import annotations

import csv
import math
from pathlib import Path


def axis_points(low: float, high: float, count: int):
    """Exactly count evenly spaced coordinates, including both endpoints."""
    if not all(math.isfinite(v) for v in (low, high)) or high < low:
        raise ValueError("扫描范围无效")
    if isinstance(count, bool) or not isinstance(count, int) or count < 2:
        raise ValueError("每个方向至少需要 2 个扫描点")
    for index in range(count):
        yield low + (high - low) * index / (count - 1)


def scan_grid(solids, bbox, counts, output, *, classify=None, max_points=2_000_000, progress=None):
    """Scan exact requested X/Y/Z point counts and export classified CSV."""
    from cst_material.occt_geometry import classify_point

    if len(bbox) != 6:
        raise ValueError("bbox 应为 xmin,ymin,zmin,xmax,ymax,zmax")
    if len(counts) != 3 or any(
        isinstance(n, bool) or not isinstance(n, int) or n < 2 for n in counts
    ):
        raise ValueError("X/Y/Z 点数必须为不小于 2 的整数")
    total = math.prod(counts)
    if total > max_points:
        raise ValueError(f"预计扫描 {total:,} 点，超过安全上限 {max_points:,}；请减少点数")
    axes = [list(axis_points(bbox[i], bbox[i + 3], counts[i])) for i in range(3)]
    dest = Path(output)
    if dest.exists():
        raise FileExistsError(dest)
    if not dest.parent.is_dir():
        raise FileNotFoundError(dest.parent)
    fn = classify or classify_point
    values = {"OUTSIDE": -1, "ON_BOUNDARY": 0, "INSIDE": 1}
    counts = {-1: 0, 0: 0, 1: 0}
    try:
        with dest.open("x", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow(("x_mm", "y_mm", "z_mm", "value"))
            for x in axes[0]:
                for y in axes[1]:
                    for z in axes[2]:
                        state = fn(solids, (x, y, z))
                        if state not in values:
                            raise RuntimeError(f"无法识别的几何分类：{state}")
                        val = values[state]
                        writer.writerow((format(x, ".12g"), format(y, ".12g"), format(z, ".12g"), val))
                        counts[val] += 1
                if progress:
                    progress(sum(counts.values()), total)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return {"total": total, "outside": counts[-1], "boundary": counts[0], "inside": counts[1], "output": str(dest)}
