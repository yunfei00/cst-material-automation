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
from cst_material.shield_brep import _dot, _face_evidence, analyze_shield_solids
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


def _cavity_cross_section(solids: list, bbox: list[float], axis: tuple[float, float, float]) -> dict:
    """Check cavity centre and four lateral positions at several axial depths.

    A single centre-line void is insufficient: a flat plate, U-channel, and
    disconnected walls can all produce one. This is still only a probe.
    """
    from cst_material.occt_geometry import classify_point

    if len(bbox) != 6 or not all(math.isfinite(v) for v in bbox):
        return {"supported": False, "reason": "缺少有效包围盒"}
    principal = max(range(3), key=lambda i: abs(axis[i]))
    if abs(axis[principal]) < 0.94:
        return {"supported": False, "reason": "非轴对齐结构，暂不推断围合"}
    lateral = [i for i in range(3) if i != principal]
    lengths = [bbox[i + 3] - bbox[i] for i in range(3)]
    if min(lengths) <= 0:
        return {"supported": False, "reason": "退化包围盒"}
    # Sampling at the central depth and two nearby sections avoids making a
    # verdict from a single lucky point. 0.08/0.92 probe possible sidewalls.
    profiles = []
    for axial_fraction in (0.25, 0.5, 0.75):
        def sample(u, v, axial_fraction=axial_fraction):
            point = [(bbox[i] + bbox[i + 3]) * 0.5 for i in range(3)]
            point[principal] = bbox[principal] + axial_fraction * lengths[principal]
            point[lateral[0]] = bbox[lateral[0]] + u * lengths[lateral[0]]
            point[lateral[1]] = bbox[lateral[1]] + v * lengths[lateral[1]]
            return classify_point(solids, tuple(point), tolerance=1e-6)

        center = sample(0.5, 0.5)
        sides = [sample(0.08, 0.5), sample(0.92, 0.5),
                 sample(0.5, 0.08), sample(0.5, 0.92)]
        profiles.append({"axial_fraction": axial_fraction, "center": center,
                         "side_states": sides,
                         "side_material_count": sides.count("INSIDE")})
    enclosed_sections = sum(
        p["center"] == "OUTSIDE" and p["side_material_count"] >= 3
        for p in profiles
    )
    return {
        "supported": True,
        "profiles": profiles,
        "enclosed_section_count": enclosed_sections,
        "wall_enclosure_hint": enclosed_sections >= 2,
        "warning": "仅离散横截面采样，不证明连续壁或真实开口",
    }


def refine_step(path: str | Path) -> dict:
    from cst_material.occt_geometry import read_step
    solids = read_step(path)
    faces = _face_evidence(solids)
    thickness = planar_thickness_evidence(faces)
    shape = analyze_shield_solids(solids)
    # The old B-rep score is supporting evidence only, not a positive verdict.
    cavity = shape.get("metrics", {}).get("axial_cavity_probe", {})
    bbox = shape.get("metrics", {}).get("bbox_mm")
    # Exact bbox is obtained from the already loaded solids, never the mesh.
    from cst_material.occt_geometry import geometry_metrics

    cap_faces = sorted(faces, key=lambda face: face["area"], reverse=True)
    bbox = bbox or (geometry_metrics(solids)["bbox_mm"] if cap_faces else None)
    enclosure = _cavity_cross_section(solids, bbox, cap_faces[0]["normal"]) if cap_faces else {
        "supported": False, "reason": "缺少平面主面"
    }
    walls = shape.get("metrics", {}).get("transverse_face_count", 0)
    strong = (
        thickness["supported"]
        and thickness["pair_consistency"] >= 0.75
        and walls >= 4
        and cavity.get("axial_void") is True
        and cavity.get("open_end_hint") is True
        and enclosure.get("wall_enclosure_hint") is True
    )
    if strong:
        verdict = "REVIEW_STRONG"
    elif (walls < 2 or (enclosure.get("supported") and
                        enclosure.get("enclosed_section_count") == 0)
          or (thickness["supported"] and thickness["pair_consistency"] < 0.35)):
        verdict = "LOW_EVIDENCE"
    else:
        verdict = "REVIEW"
    return {
        "verdict": verdict, "thickness": thickness,
        "cross_section": enclosure,
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
    algorithm_version = "P8-B4-cross-section-20261118"
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
            if (old.get("schema_version") == 2
                    and old.get("algorithm_version") == algorithm_version
                    and old.get("manifest_sha256") == manifest_hash):
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
            "schema_version": 2, "algorithm_version": algorithm_version,
            "manifest_sha256": manifest_hash,
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
