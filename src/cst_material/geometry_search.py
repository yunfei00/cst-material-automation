"""P6 offline shape search: ranking and morphology, never material assignment."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from cst_material.occt_geometry import sha256_file
from cst_material.solid_recognition import compare_reference_geometry, shape_candidate


def search_geometry(
    folder: str | Path, *, reference_id: str | None = None,
    spring_candidates: bool = False, limit: int = 30,
) -> list[dict]:
    """Search previously extracted exact features without reloading STEP/CST."""
    if (reference_id is None) == (not spring_candidates):
        raise ValueError("必须且只能指定 reference_id 或 spring_candidates")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise ValueError("limit 必须在 1..10000 之间")
    root = Path(folder).resolve(strict=True)
    db = root / "geometry_features.sqlite3"
    if not db.is_file():
        raise FileNotFoundError("请先运行 P5 几何特征提取")
    connection = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        metadata = connection.execute(
            "SELECT value FROM metadata WHERE key='manifest_sha256'"
        ).fetchone()
        manifest_path = root / "manifest.json"
        if metadata is None or (
            manifest_path.is_file()
            and metadata["value"] != sha256_file(manifest_path)
        ):
            raise ValueError("P5 数据库与 manifest 不匹配，请重新构建")
        records = connection.execute(
            "SELECT solid_id,step,step_sha256,features_json FROM solids WHERE status='PASS'"
        ).fetchall()
        reference = None
        if reference_id is not None:
            for row in records:
                if row["solid_id"] == reference_id:
                    reference = json.loads(row["features_json"])
                    break
            if reference is None:
                raise ValueError(f"未找到已成功提取的参考 Solid：{reference_id}")
        results = []
        for row in records:
            if reference_id == row["solid_id"]:
                continue
            features = json.loads(row["features_json"])
            hint = shape_candidate(features)
            if spring_candidates:
                if hint["candidate"] != "SPRING_CONTACT":
                    continue
                score = None
            else:
                score = compare_reference_geometry(features, reference)
                if score is None:
                    continue
            results.append({
                "solid_id": row["solid_id"],
                "step": row["step"],
                "shape": hint["shape"],
                "candidate": hint["candidate"],
                "similarity": score,
                "dimensions_mm": features["dimensions_mm"],
                "volume_mm3": features["volume_mm3"],
                "evidence": hint["evidence"],
                "review_status": "NEEDS_REVIEW",
            })
        results.sort(
            key=(lambda x: (-x["similarity"], x["solid_id"]))
            if reference_id is not None else
            (lambda x: (x["volume_mm3"], x["solid_id"]))
        )
        return results[:limit]
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P6 几何相似检索和弹片候选筛选")
    parser.add_argument("folder", help="P5 已完成的 STEP 导出目录")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--reference", help="参考 Solid 的完整 Component:Solid 名称")
    group.add_argument("--spring-candidates", action="store_true", help="筛选弹片形态候选")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--output", help="结果 JSON 文件路径（可选）")
    args = parser.parse_args(argv)
    rows = search_geometry(
        args.folder, reference_id=args.reference,
        spring_candidates=args.spring_candidates, limit=args.limit,
    )
    output = {"mode": "spring_candidates" if args.spring_candidates else "similarity",
              "reference_id": args.reference, "count": len(rows),
              "warning": "形态或相似度不是零件身份确认，禁止据此自动赋材料",
              "results": rows}
    payload = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        dest = Path(args.output)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(payload, encoding="utf-8")
        print(f"已保存 {len(rows)} 条候选：{dest}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
