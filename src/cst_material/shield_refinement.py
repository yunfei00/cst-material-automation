"""P8-B3 two-stage shield screening: P5 shortlist followed by STEP evidence.

Conservative: planar pairing is only a thickness *hint*, not full sheet-metal
thickness verification. Unknown geometry stays REVIEW, never auto-confirmed.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path

from cst_material.occt_geometry import sha256_file
from cst_material.shield_brep import _dot, _face_evidence, analyze_shield_solids
from cst_material.shield_recognition import search_shield_candidates, search_pcb_related_shields
from cst_material.sheet_thickness import analyze_sheet_faces


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



def _verify_wall_segments(solids: list, faces: list[dict], thickness: dict) -> dict:
    """Verify whether a proposed plane gap contains actual CAD material.

    Surface centre separation alone can cross a cavity. Sample the exact BREP
    classifier at three interior locations of opposed, approximately aligned
    face pairs. This is a material-path check, NOT complete wall thickness.
    """
    from cst_material.occt_geometry import classify_point

    if not thickness.get("supported"):
        return {"supported": False, "reason": "没有候选厚度"}
    target = thickness["dominant_thickness_mm"]
    checked = 0
    material_paths = 0
    for i, a in enumerate(faces):
        for b in faces[i + 1:]:
            if _dot(a["normal"], b["normal"]) > -0.985:
                continue
            delta = [b["center"][k] - a["center"][k] for k in range(3)]
            projection = _dot(delta, a["normal"])
            gap = abs(projection)
            if abs(gap - target) > max(0.03, target * 0.15):
                continue
            lateral = math.sqrt(max(0, sum(x*x for x in delta) - gap*gap))
            if lateral > math.sqrt(min(a["area"], b["area"])) * 0.5:
                continue
            # Do not check arbitrarily many pair combinations on 8000+ solids.
            if checked >= 32:
                break
            checked += 1
            direction = 1 if projection > 0 else -1
            states = [
                classify_point(solids, tuple(
                    a["center"][k] + direction * a["normal"][k] * gap * fraction
                    for k in range(3)
                ), tolerance=1e-6)
                for fraction in (0.25, 0.5, 0.75)
            ]
            if all(state == "INSIDE" for state in states):
                material_paths += 1
        if checked >= 32:
            break
    return {
        "supported": checked > 0,
        "checked_pair_count": checked,
        "material_path_count": material_paths,
        "material_path_ratio": round(material_paths / checked, 4) if checked else 0,
        "warning": "仅验证部分平面间隙穿过实体材料，不代表完整等厚",
    }


def _cavity_cross_section(solids: list, bbox: list[float], axis: tuple[float, float, float],
                          wall_thickness_mm: float | None = None) -> dict:
    """Check cavity centre and four lateral positions at several axial depths.

    A single centre-line void is insufficient: a flat plate, U-channel, and
    disconnected walls can all produce one. This is still only a probe.
    """
    from cst_material.occt_geometry import classify_point

    if len(bbox) != 6 or not all(math.isfinite(v) for v in bbox):
        return {"supported": False, "reason": "缺少有效包围盒"}
    if len(axis) != 3 or not all(math.isfinite(v) for v in axis):
        return {'supported': False, 'reason': '无效主面法向'}
    principal = max(range(3), key=lambda i: abs(axis[i]))
    if abs(axis[principal]) < 0.94:
        return {"supported": False, "reason": "非轴对齐结构，暂不推断围合"}
    lateral = [i for i in range(3) if i != principal]
    lengths = [bbox[i + 3] - bbox[i] for i in range(3)]
    if min(lengths) <= 0:
        return {"supported": False, "reason": "退化包围盒"}
    # Fixed 8% probes miss 0.3 mm walls on a 20 mm shielding can.
    offsets = []
    for lateral_axis in lateral:
        extent = lengths[lateral_axis]
        if wall_thickness_mm and math.isfinite(wall_thickness_mm) and wall_thickness_mm > 0:
            offset = min(0.08, max(0.001, 0.5 * wall_thickness_mm / extent))
        else:
            offset = 0.08
        offsets.append(offset)
    profiles = []
    for axial_fraction in (0.25, 0.5, 0.75):
        def sample(u, v, axial_fraction=axial_fraction):
            point = [(bbox[i] + bbox[i + 3]) * 0.5 for i in range(3)]
            point[principal] = bbox[principal] + axial_fraction * lengths[principal]
            point[lateral[0]] = bbox[lateral[0]] + u * lengths[lateral[0]]
            point[lateral[1]] = bbox[lateral[1]] + v * lengths[lateral[1]]
            return classify_point(solids, tuple(point), tolerance=1e-6)

        center = sample(0.5, 0.5)
        u, v = offsets
        sides = [sample(u, 0.5), sample(1 - u, 0.5),
                 sample(0.5, v), sample(0.5, 1 - v)]
        profiles.append({"axial_fraction": axial_fraction, "center": center,
                         "side_states": sides,
                         "side_material_count": sides.count("INSIDE")})
    enclosed_sections = sum(
        p["center"] == "OUTSIDE" and p["side_material_count"] >= 3
        for p in profiles
    )
    all_solid_sections = sum(p["center"] == "INSIDE" for p in profiles)
    empty_sections = sum(
        p["center"] == "OUTSIDE" and p["side_material_count"] == 0
        for p in profiles
    )
    return {
        "supported": True,
        "profiles": profiles,
        "enclosed_section_count": enclosed_sections,
        "all_solid_section_count": all_solid_sections,
        "empty_section_count": empty_sections,
        "wall_enclosure_hint": enclosed_sections >= 2,
        "warning": "仅离散横截面采样，不证明连续壁或真实开口",
    }


def refine_step(path: str | Path) -> dict:
    from cst_material.occt_geometry import read_step
    solids = read_step(path)
    faces = _face_evidence(solids)
    thickness = planar_thickness_evidence(faces)
    sheet_faces = analyze_sheet_faces(solids)
    wall_paths = _verify_wall_segments(solids, faces, thickness)
    shape = analyze_shield_solids(solids)
    # The old B-rep score is supporting evidence only, not a positive verdict.
    cavity = shape.get("metrics", {}).get("axial_cavity_probe", {})
    bbox = shape.get("metrics", {}).get("bbox_mm")
    # Exact bbox is obtained from the already loaded solids, never the mesh.
    from cst_material.occt_geometry import geometry_metrics

    cap_faces = sorted(faces, key=lambda face: face["area"], reverse=True)
    bbox = bbox or (geometry_metrics(solids)["bbox_mm"] if cap_faces else None)
    enclosure = _cavity_cross_section(
        solids, bbox, cap_faces[0]["normal"],
        thickness.get("dominant_thickness_mm")
    ) if cap_faces else {
        "supported": False, "reason": "缺少平面主面"
    }
    walls = shape.get("metrics", {}).get("transverse_face_count", 0)
    strong = (
        thickness["supported"]
        and thickness["pair_consistency"] >= 0.75
        and wall_paths.get("material_path_count", 0) >= 2
        and wall_paths.get("material_path_ratio", 0) >= 0.6
        and walls >= 4
        and cavity.get("axial_void") is True
        and cavity.get("open_end_hint") is True
        and enclosure.get("wall_enclosure_hint") is True
    )
    if strong:
        verdict = "REVIEW_STRONG"
    elif (walls < 2 or (enclosure.get("supported") and
                        enclosure.get("all_solid_section_count") == 3)
          or (thickness["supported"] and thickness["pair_consistency"] < 0.35)):
        verdict = "LOW_EVIDENCE"
    else:
        verdict = "REVIEW"
    return {
        "verdict": verdict, "thickness": thickness,
        "sheet_faces": sheet_faces,
        "wall_paths": wall_paths,
        "cross_section": enclosure,
        "brep": shape,
        "warning": "几何证据不能证明屏蔽功能；未进行完整等厚及开口连通性验证",
    }


def classify_pcb_shield(row: dict, evidence: dict) -> dict:
    """Combine analytic sheet evidence with enclosure; do not promote flat contacts."""
    sheet = evidence.get("sheet_faces") or {}
    counts = sheet.get("surface_counts") or {}
    brep = (evidence.get("brep") or {}).get("metrics") or {}
    cross = evidence.get("cross_section") or {}
    paths = evidence.get("wall_paths") or {}
    consistency = float(sheet.get("pair_consistency") or 0)
    pair_count = int(sheet.get("plane_pair_count") or 0) + int(sheet.get("cylinder_pair_count") or 0)
    walls = int(brep.get("transverse_face_count") or 0)
    enclosure = bool(cross.get("wall_enclosure_hint"))
    cavity = bool((brep.get("axial_cavity_probe") or {}).get("axial_void"))
    # A stamped spring contact can be perfectly uniform thickness. Require
    # a cover/cavity or actual enclosure evidence before promoting it.
    wall_structure = walls >= 4 and (enclosure or cavity)
    sheet_supported = pair_count >= 2 and consistency >= 0.75 and (
        int(counts.get("plane") or 0) >= 2
    )
    material_supported = int(paths.get("material_path_count") or 0) >= 1
    supported = wall_structure and sheet_supported and material_supported
    if supported:
        verdict = "SHIELD_REVIEW_STRONG"
    elif row.get("name_hit"):
        verdict = "NAMED_REVIEW"
    elif wall_structure and sheet_supported:
        verdict = "GEOMETRY_REVIEW"
    else:
        verdict = "CONTACT_OR_OTHER"
    score = min(100, round(
        25 * min(1.0, consistency) +
        15 * min(1.0, pair_count / 3) +
        20 * min(1.0, walls / 4) +
        (20 if enclosure else 10 if cavity else 0) +
        (10 if material_supported else 0) +
        (10 if row.get("name_hit") else 0), 1))
    return {
        "verdict": verdict, "score": score,
        "sheet_supported": sheet_supported,
        "wall_structure": wall_structure, "material_supported": material_supported,
        "face_count": sheet.get("face_count"),
        "surface_counts": counts,
        "dominant_thickness_mm": sheet.get("dominant_thickness_mm"),
        "pair_consistency": consistency,
        "plane_pair_count": sheet.get("plane_pair_count", 0),
        "cylinder_pair_count": sheet.get("cylinder_pair_count", 0),
        "warning": "几何候选并非电磁屏蔽功能证明；无法确认者需人工复核",
    }


def _all_geometry_candidates(root: Path) -> list[dict]:
    """Recall-first geometry search: do not gate on P5 shield heuristic.

    Every successfully extracted positive-volume Solid with a STEP path is
    eligible for exact BREP analysis. No PCB, labels, BOM or sample is used.
    """
    db = root / "geometry_features.sqlite3"
    if not db.is_file():
        raise FileNotFoundError("缺少 P5 几何索引，请先导出结构并提取特征")
    connection = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT solid_id,step,features_json FROM solids WHERE status='PASS'"
        ).fetchall()
    finally:
        connection.close()
    result = []
    for sid, step, raw in rows:
        try:
            features = json.loads(raw)
            volume = float(features["volume_mm3"])
            if not math.isfinite(volume) or volume <= 0:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        result.append({"solid_id": sid, "step": step, "score": 0,
                       "volume_mm3": volume})
    # Larger bodies first for a predictable, resumable full scan.
    result.sort(key=lambda row: (-row["volume_mm3"], row["solid_id"]))
    return result


def refine_candidates(folder: str | Path, *, limit: int | None = None,
                      progress=None, exhaustive: bool = False,
                      pcb_related: bool = False) -> dict:
    root = Path(folder).resolve(strict=True)
    manifest = root / "manifest.json"
    if not manifest.is_file():
        raise FileNotFoundError("缺少 manifest.json")
    manifest_hash = sha256_file(manifest)
    algorithm_version = ("P8-B8-pcb-scored-v1-20261119" if pcb_related else
                         "P8-B6-sheet-faces-full-v1-20261119"
                         if exhaustive else "P8-B6-sheet-faces-shortlist-v1-20261119")
    rows = (search_pcb_related_shields(root)['results'] if pcb_related else
            _all_geometry_candidates(root) if exhaustive else search_shield_candidates(root))
    if limit is not None:
        if type(limit) is not int or limit < 1:
            raise ValueError("limit 必须为正整数")
        rows = rows[:limit]
    cache = root / ("shield_refinement_pcb.json" if pcb_related else
                    "shield_refinement_all.json" if exhaustive else "shield_refinement.json")
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
    summary = Counter({"total": len(rows), "reused": 0, "failed": 0,
                       "REVIEW_STRONG": 0, "REVIEW": 0, "LOW_EVIDENCE": 0})
    if not rows:
        cache.write_text(json.dumps({"schema_version": 2,
            "algorithm_version": algorithm_version, "manifest_sha256": manifest_hash,
            "summary": summary, "results": {}}, ensure_ascii=False, indent=2), encoding="utf-8")
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
                          "stage1_score": row.get("score", 0), **refine_step(step)}
            if pcb_related and record["status"] == "PASS":
                detail = classify_pcb_shield(row, record)
                record["pcb_shield"] = detail
                record["brep_verdict"] = record["verdict"]
                record["verdict"] = detail["verdict"]
                record["score"] = detail["score"]
            summary[record["verdict"]] += 1
        except Exception as exc:  # noqa: BLE001
            record = {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
            summary["failed"] += 1
        results[sid] = record
        # Batch checkpoint for thousands of solids; avoid O(N²) JSON rewrites.
        # A crash may redo at most 19 records, never lose completed batches.
        if index % 20 == 0 or index == len(rows):
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
    parser.add_argument("--pcb-related", action="store_true",
                        help="仅分析与主 PCB 空间关联的屏蔽罩候选")
    parser.add_argument("--exhaustive", action="store_true",
                        help="不依赖一级屏蔽罩筛选，逐一检查全部有效 Solid")
    args = parser.parse_args(argv)
    result = refine_candidates(args.folder, limit=args.limit,
                               exhaustive=args.exhaustive,
                               pcb_related=args.pcb_related,
                               progress=lambda i, n, s: print(f"{i}/{n} {s}", flush=True)
                               if i % 20 == 0 or i == n else None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["summary"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
