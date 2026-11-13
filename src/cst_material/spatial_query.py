"""Fast repeatable offline XYZ -> Solid lookup with vectorized 3D AABB indexing.

CST is never contacted during lookup. Only candidate STEP B-reps are loaded,
and their OCCT classifiers are cached across queries. AABB is a prefilter,
never the final point-in-solid decision.
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np

from cst_material.occt_geometry import read_step, sha256_file
from cst_material.solid_index import _inside_folder, load_catalog


class OfflineSolidIndex:
    def __init__(self, folder: str | Path):
        self.root = Path(folder).resolve(strict=True)
        self.records = load_catalog(self.root, verify_files=False)["records"]
        # Instance-local LRU avoids retaining every disposed index globally.
        self._classifiers = lru_cache(maxsize=128)(self._load_classifiers)
        self.bounds = np.array(
            [r["features"]["bbox_mm"] for r in self.records],
            dtype=np.float64,
        ).reshape(-1, 6)
        if not np.isfinite(self.bounds).all():
            raise ValueError("几何索引存在非有限包围盒")

    def _load_classifiers(self, idx: int):
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier

        entry = self.records[idx]
        step = _inside_folder(self.root, entry["step"])
        if sha256_file(step) != entry["features"]["step_sha256"]:
            raise ValueError(f"几何文件已更改，索引过期：{entry['solid_id']}")
        return tuple(BRepClass3d_SolidClassifier(solid) for solid in read_step(step))

    def classify(self, point: tuple[float, float, float], *, tolerance: float = 1e-6):
        from OCP.gp import gp_Pnt
        from OCP.TopAbs import TopAbs_IN, TopAbs_ON, TopAbs_OUT

        if len(point) != 3 or not all(math.isfinite(float(v)) for v in point):
            raise ValueError("三维点坐标必须为有限数值")
        if not math.isfinite(tolerance) or tolerance <= 0:
            raise ValueError("tolerance 必须是有限正数")
        xyz = np.asarray(point, dtype=np.float64)
        candidates = np.flatnonzero(np.all(
            (xyz >= self.bounds[:, :3] - tolerance)
            & (xyz <= self.bounds[:, 3:] + tolerance),
            axis=1,
        ))
        hit = []
        occ_point = gp_Pnt(float(xyz[0]), float(xyz[1]), float(xyz[2]))
        for idx in candidates:
            status = "OUTSIDE"
            for classifier in self._classifiers(int(idx)):
                classifier.Perform(occ_point, tolerance)
                state = classifier.State()
                if state == TopAbs_IN:
                    status = "INSIDE"
                    break
                if state == TopAbs_ON:
                    status = "ON_BOUNDARY"
                elif state != TopAbs_OUT:
                    raise RuntimeError("OCP 返回未识别的点分类状态")
            if status != "OUTSIDE":
                hit.append({"solid_id": self.records[int(idx)]["solid_id"],
                            "state": status})
        return hit

    def classify_many(self, points, *, tolerance: float = 1e-6):
        """Returns a result per point; classifiers persist in LRU cache."""
        return [self.classify(tuple(p), tolerance=tolerance) for p in points]
