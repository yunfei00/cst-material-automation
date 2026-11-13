"""Exact B-rep point grid scan; CSV rows are x_mm,y_mm,z_mm,value."""
from __future__ import annotations

import csv
import math
from pathlib import Path


def axis_points(low: float, high: float, step: float):
    """Include lower bound and upper bound, even when not step-aligned."""
    if not all(math.isfinite(v) for v in (low, high, step)) or step <= 0 or high < low:
        raise ValueError("扫描范围和步进必须是有效有限数值")
    n = math.floor((high - low) / step)
    for i in range(n + 1):
        yield low + i * step
    if low + n * step < high - max(1e-10, abs(high) * 1e-12):
        yield high


def scan_grid(solids, bbox, step, output, *, classify=None, max_points=2_000_000, progress=None):
    """Scan bounding cuboid with exact B-rep classifier, atomically write CSV.

    Returns counts. On failure removes partial output; never overwrites a file.
    """
    from cst_material.occt_geometry import classify_point

    if len(bbox) != 6:
        raise ValueError("bbox 应为 xmin,ymin,zmin,xmax,ymax,zmax")
    if not math.isfinite(step) or step <= 0:
        raise ValueError("步进必须是有限正数")
    axes = [list(axis_points(bbox[i], bbox[i + 3], step)) for i in range(3)]
    total = math.prod(len(a) for a in axes)
    if total > max_points:
        raise ValueError(f"预计扫描 {total:,} 点，超过安全上限 {max_points:,}；请增大步进")
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
