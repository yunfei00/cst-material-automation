"""P5 offline geometry feature library backed by the verified per-Solid manifest."""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from cst_material.occt_geometry import analyze_step, sha256_file
from cst_material.solid_index import load_manifest


SCHEMA_VERSION = 1


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS solids (
            solid_id TEXT PRIMARY KEY,
            component TEXT NOT NULL,
            material TEXT,
            step TEXT NOT NULL UNIQUE,
            step_sha256 TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('PASS', 'FAIL')),
            volume_mm3 REAL,
            surface_mm2 REAL,
            dx_mm REAL,
            dy_mm REAL,
            dz_mm REAL,
            face_count INTEGER,
            edge_count INTEGER,
            vertex_count INTEGER,
            body_count INTEGER,
            features_json TEXT,
            error TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_solids_status ON solids(status);
        CREATE INDEX IF NOT EXISTS idx_solids_volume ON solids(volume_mm3);
        CREATE INDEX IF NOT EXISTS idx_solids_dimensions ON solids(dx_mm,dy_mm,dz_mm);
    """)
    return connection


def build_feature_library(
    folder: str | Path, *, analyzer=None, limit: int | None = None,
    progress=None,
) -> dict:
    """Extract exact STEP metrics with resumable per-Solid transactions.

    Source identity is anchored to the manifest's full canonical content.
    Failed entries are retried. PASS entries are reused only when STEP SHA matches.
    """
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("limit 必须为正整数")
    root = Path(folder).resolve(strict=True)
    manifest = load_manifest(root, verify_files=False)
    manifest_path = root / "manifest.json"
    manifest_hash = sha256_file(manifest_path)
    db_path = root / "geometry_features.sqlite3"
    connection = _connect(db_path)
    started = time.perf_counter()
    try:
        old_hash = connection.execute(
            "SELECT value FROM metadata WHERE key='manifest_sha256'"
        ).fetchone()
        if old_hash and old_hash["value"] != manifest_hash:
            raise RuntimeError("manifest 已变化，请备份并移走旧 geometry_features.sqlite3 后重建")
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version',?)",
            (str(SCHEMA_VERSION),),
        )
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('manifest_sha256',?)",
            (manifest_hash,),
        )
        connection.commit()
        items = manifest["solids"][:limit] if limit else manifest["solids"]
        report = {"total": len(items), "success": 0, "failed": 0,
                  "resumed": 0, "database": str(db_path), "errors": []}
        for index, item in enumerate(items, 1):
            solid_id = item["solid_id"]
            step = (root / item["step"]).resolve()
            expected_hash = item.get("sha256")
            try:
                actual_hash = sha256_file(step)
                if expected_hash and expected_hash != actual_hash:
                    raise ValueError("STEP SHA256 与 manifest 不一致")
                cached = connection.execute(
                    "SELECT status,step_sha256,step FROM solids WHERE solid_id=?",
                    (solid_id,),
                ).fetchone()
                if (cached and cached["status"] == "PASS"
                    and cached["step_sha256"] == actual_hash
                    and cached["step"] == item["step"]):
                    report["success"] += 1
                    report["resumed"] += 1
                    continue
                feature = (analyzer or analyze_step)(step)
                dimensions = feature["dimensions_mm"]
                if (len(dimensions) != 3 or
                    not all(math.isfinite(float(v)) and v >= 0 for v in dimensions) or
                    not math.isfinite(float(feature["volume_mm3"])) or
                    feature["volume_mm3"] <= 0):
                    raise ValueError("STEP 几何尺寸或体积无效")
                if feature.get("step_sha256", actual_hash) != actual_hash:
                    raise ValueError("分析期间 STEP 文件发生变化")
                if sha256_file(step) != actual_hash:
                    raise ValueError("分析期间 STEP 文件发生变化")
                values = (
                    solid_id, solid_id.rpartition(":")[0], item.get("material"),
                    item["step"], actual_hash, "PASS", feature["volume_mm3"],
                    feature["surface_mm2"], *dimensions,
                    feature["face_count"], feature["edge_count"],
                    feature["vertex_count"], feature["solid_body_count"],
                    json.dumps(feature, ensure_ascii=False), None,
                    datetime.now(UTC).isoformat(),
                )
                report["success"] += 1
            except Exception as exc:  # noqa: BLE001 - record per-Solid failures
                values = (
                    solid_id, solid_id.rpartition(":")[0], item.get("material"),
                    item["step"], expected_hash or "", "FAIL",
                    None, None, None, None, None, None, None, None, None,
                    None, f"{type(exc).__name__}: {exc}",
                    datetime.now(UTC).isoformat(),
                )
                report["failed"] += 1
                report["errors"].append({"solid_id": solid_id, "error": values[-2]})
            finally:
                if "values" in locals():
                    connection.execute("""
                        INSERT OR REPLACE INTO solids(
                            solid_id,component,material,step,step_sha256,status,
                            volume_mm3,surface_mm2,dx_mm,dy_mm,dz_mm,
                            face_count,edge_count,vertex_count,body_count,
                            features_json,error,updated_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, values)
                    connection.commit()
                    del values
                if progress:
                    progress(index, len(items), report)
        report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        report["status"] = "PASS" if report["failed"] == 0 else "PARTIAL"
        (root / "geometry_features_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report
    finally:
        connection.close()


def query_features(folder: str | Path, *, keyword: str = "",
                   min_volume: float = 0, limit: int = 100) -> list[dict]:
    if limit < 1 or limit > 10000:
        raise ValueError("limit 范围为 1..10000")
    db = Path(folder).resolve(strict=True) / "geometry_features.sqlite3"
    if not db.is_file():
        raise FileNotFoundError("请先构建几何特征库")
    connection = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("""
            SELECT solid_id,component,material,step,volume_mm3,surface_mm2,
                   dx_mm,dy_mm,dz_mm,face_count,edge_count,vertex_count,body_count
            FROM solids WHERE status='PASS' AND volume_mm3>=?
              AND instr(lower(solid_id),lower(?))>0
            ORDER BY volume_mm3 DESC LIMIT ?
        """, (min_volume, keyword, limit)).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P5 CST Solid 离线几何特征库")
    parser.add_argument("folder", help="包含 manifest.json 的全量导出目录")
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 条（测试用）")
    args = parser.parse_args(argv)
    def show(index, total, report):
        if index == total or index % 100 == 0:
            print(f"{index}/{total} 成功={report['success']} "
                  f"失败={report['failed']} 复用={report['resumed']}", flush=True)
    result = build_feature_library(args.folder, limit=args.limit, progress=show)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
