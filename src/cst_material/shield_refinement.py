"""P8-B3 two-stage shield screening: P5 shortlist followed by STEP evidence.

Conservative: planar pairing is only a thickness *hint*, not full sheet-metal
thickness verification. Unknown geometry stays REVIEW, never auto-confirmed.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from cst_material.occt_geometry import sha256_file
from cst_material.shield_brep import _dot, _face_evidence, analyze_shield_step
from cst_material.shield_recognition import search_shield_candidates


def planar_thickness_evidence(faces: list[dict]) -> dict:
    """Find area-weighted, opposed planar face pairs with matching thickness.

    This intentionally avoids claiming curved bends have been verified.
    """
    pairs = []
    for i, a in enumerate(faces):
        for b in faces[i + 1:]:
            dot = _dot(a["normal"], b["normal"])
            # Opposed face normals are expected for two sides of one sheet.
            if dot > -0.985:
                continue
            delta = [b["center"][k] - a["center"][k] for k in range(3)]
            separation = abs(_dot(delta, a["normal"]))
            lateral = math.sqrt(max(0.0, sum(v*v for v in delta) - separation**2))
            if separation <= 1e-5 or lateral > math.sqrt(min(a["area"], b["area"])) * 0.5:
                continue
            pairs.append((separation, min(a["area"], b["area"])))
    if not pairs:
        return {"supported": False, "reason": "未找到可信的相对平面面"}
    pairs.sort(key=lambda x: x[0])
    # Prefer the most strongly represented thickness, not the thinnest tiny feature.
    candidates = []
    for thickness, _ in pairs:
        weight = sum(area for t, area in pairs if abs(t-thickness) <= max(0.03, thickness*0.15))
        candidates.append((weight, thickness))
    _, dominant = max(candidates)
    matched = [(t, area) for t, area in pairs if abs(t-dominant) <= max(0.03, dominant*0.15)]
    coverage = sum(area for _, area in matched) / sum(area for _, area in pairs)
    return {
        "supported": True,
        "dominant_thickness_mm": round(dominant, 5),
        "pair_consistency": round(coverage, 4),
        "paired_face_count": len(pairs),
        "warning": "仅平面配对厚度线索；曲面折弯、真实壁厚及面重叠未验证",
    }


def refine_step(path: str | Path) -> dict:
    from cst_material.occt_geometry import read_step
    solids = read_step(path)
    faces = _face_evidence(solids)
    thickness = planar_thickness_evidence(faces)
    shape = analyze_shield_step(path)
    # The old B-rep score is supporting evidence only, not a positive verdict.
    cavity = shape.get("metrics", {}).get("axial_cavity_probe", {})
    walls = shape.get("metrics", {}).get("transverse_face_count", 0)
    strong = (
        thickness["supported"]
        and thickness["pair_consistency"] >= 0.75
        and walls >= 4
        and cavity.get("axial_void") is True
        and cavity.get("open_end_hint") is True
    )
    if strong:
        verdict = "REVIEW_STRONG"
    elif walls < 2 or (thickness["supported"] and thickness["pair_consistency"] < 0.35):
        verdict = "LOW_EVIDENCE"
    else:
        verdict = "REVIEW"
    return {
        "verdict": verdict, "thickness": thickness,
        "brep": shape,
        "warning": "几何证据不能证明屏蔽功能；未进行完整等厚及开口连通性验证",
    }


def refine_candidates(folder: str | Path, *, limit: int | None = None,
                      progress=None) -> dict:
    root = Path(folder).resolve(strict=True)
    manifest = root / "manifest.json"
    if not manifest.is_file():
        raise FileNotFoundError("缺少 manifest.json")
    manifest_hash = sha256_file(manifest)
    rows = search_shield_candidates(root)
    if limit is not None:
        if type(limit) is not int or limit < 1:
            raise ValueError("limit 必须为正整数")
        rows = rows[:limit]
    cache = root / "shield_refinement.json"
    previous = {}
    if cache.is_file():
        try:
            old = json.loads(cache.read_text(encoding="utf-8"))
            if old.get("schema_version") == 1 and old.get("manifest_sha256") == manifest_hash:
                previous = old.get("results", {})
        except (OSError, ValueError, TypeError):
            previous = {}
    results = {}
    summary = {"total": len(rows), "reused": 0, "failed": 0,
               "REVIEW_STRONG": 0, "REVIEW": 0, "LOW_EVIDENCE": 0}
    for index, row in enumerate(rows, 1):
        sid = row["solid_id"]
        step = (root / row["step"]).resolve()
        try:
            if not step.is_relative_to(root) or not step.is_file():
                raise FileNotFoundError("STEP 不存在或路径越界")
            digest = sha256_file(step)
            cached = previous.get(sid)
            if cached and cached.get("step_sha256") == digest and cached.get("status") == "PASS":
                record = cached
                summary["reused"] += 1
            else:
                record = {"status": "PASS", "step_sha256": digest,
                          "stage1_score": row["score"], **refine_step(step)}
            summary[record["verdict"]] += 1
        except Exception as exc:  # noqa: BLE001
            record = {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
            summary["failed"] += 1
        results[sid] = record
        # Atomic checkpoint: interrupted runs resume from the last completed item.
        temporary = cache.with_suffix(".json.tmp")
        temporary.write_text(json.dumps({
            "schema_version": 1, "manifest_sha256": manifest_hash,
            "summary": summary, "results": {**previous, **results},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(cache)
        if progress:
            progress(index, len(rows), summary)
    return {"summary": summary, "output": str(cache)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="P8-B3 屏蔽罩两级筛选")
    parser.add_argument("folder")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    result = refine_candidates(args.folder, limit=args.limit,
                               progress=lambda i, n, s: print(f"{i}/{n} {s}", flush=True)
                               if i % 20 == 0 or i == n else None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["summary"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
