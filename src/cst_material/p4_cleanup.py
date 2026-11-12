"""CST structure cleanup: deterministic zero-volume rule plus explicit IDs.

Confirmed rule R001: ALL P3-A solids with an exactly zero measured volume are
selected for deletion, independent of area, name, component or existing material.
The planner does not modify CST. Remaining name hints stay REVIEW-only.
"""
from __future__ import annotations

import hashlib
import json
import ntpath
from pathlib import Path
from typing import Any

RULE_VERSION = 1
MAX_DELETE_PER_APPLY = 12000
REVIEW_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("辅助建模候选", ("辅助", "aux_", "auxiliary", "construction", "建模辅助")),
    ("参考/基准候选", ("基准", "参考线", "参考面", "datum", "reference_geometry")),
    ("工装/治具候选", ("治具", "工装", "fixture", "jig_")),
    ("重复变体候选", ("_variant", "_alt_", "方案a", "方案b", "变体")),
)
# These are *not* approved deletions. Visible for engineering review only.
RETAIN_BY_DEFAULT = (
    "usb", "earphone", "headphone", "headset", "motor", "front_camera",
    "摄像头", "前摄", "马达", "耳机", "连接器", "connector", "天线", "antenna",
    "fpc", "屏蔽", "shield",
)


def _load_json(path: Path) -> dict:
    source = path.resolve(strict=True)
    if not source.is_file() or source.suffix.casefold() != ".json":
        raise ValueError("必须提供已存在的 .json 文件")
    root = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(root, dict):
        raise TypeError("清理输入 JSON 的顶层必须为对象")
    return root


def _source_identity(a: str, b: str) -> bool:
    return ntpath.normcase(ntpath.normpath(a)) == ntpath.normcase(ntpath.normpath(b))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _digest_catalog(rows: dict[str, str | None]) -> str:
    encoded = json.dumps(
        sorted(rows.items()), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _str_array(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item or item != item.strip()
        for item in value
    ):
        raise TypeError(f"{label} 必须是非空、无多余空格的 Solid ID 字符串列表")
    if len(set(value)) != len(value):
        raise ValueError(f"{label} 存在重复 ID")
    return value


def read_p1_inventory(path: Path) -> tuple[dict[str, str | None], str]:
    payload = _load_json(path)
    if payload.get("task") != "inventory" or payload.get("status") != "PASS":
        raise ValueError("仅接受 PASS 的 P1 inventory 原始报告")
    source = payload.get("source")
    if not isinstance(source, str) or not source:
        raise ValueError("P1 报告缺少原工程路径")
    solids = payload.get("solids")
    if not isinstance(solids, list) or not solids:
        raise ValueError("P1 没有完整 Solid 清单")
    catalog: dict[str, str | None] = {}
    for row in solids:
        if not isinstance(row, dict):
            raise TypeError("P1 Solid 记录必须是对象")
        name, material = row.get("identifier"), row.get("material")
        if not isinstance(name, str) or not name or name in catalog:
            raise ValueError("P1 Solid 存在空标识或重复 ID")
        if material is not None and not isinstance(material, str):
            raise TypeError("P1 Solid 材料必须为字符串或空值")
        catalog[name] = material
    if payload.get("solid_count") not in (None, len(catalog)):
        raise ValueError("P1 Solid 数量与列表不一致")
    return catalog, source


def build_cleanup_plan(
    source: Path, inventory: Path, rules_path: Path | None = None,
    geometry_report: Path | None = None,
) -> dict:
    """R001 selects all V=0 P3-A solids; other rules require explicit IDs."""
    original = source.resolve(strict=True)
    if not original.is_file() or original.suffix.casefold() != ".cst":
        raise ValueError("输入必须是现有 .cst 工程")
    rows, report_source = read_p1_inventory(inventory)
    if not _source_identity(str(original), report_source):
        raise ValueError("P1 inventory 来源工程路径与本次源 .cst 不一致")
    if geometry_report is None:
        raise ValueError("规则 R001 必须提供通过的 P3-A geometry 报告，不能遗漏零体积实体")

    rules = _load_json(rules_path) if rules_path is not None else {
        "schema_version": RULE_VERSION,
        "confirmed_delete_ids": [],
        "protected_ids": [],
        "variant_groups": [],
    }
    if rules.get("schema_version") != RULE_VERSION:
        raise ValueError(f"清理规则版本必须为 {RULE_VERSION}")
    confirmed = set(_str_array(rules.get("confirmed_delete_ids"), "confirmed_delete_ids"))
    protected = set(_str_array(rules.get("protected_ids"), "protected_ids"))
    unknown = (confirmed | protected) - rows.keys()
    if unknown:
        raise ValueError(f"规则包含 P1 中不存在的 Solid ID（最多显示5个）：{sorted(unknown)[:5]}")
    if confirmed & protected:
        raise ValueError("同一个 Solid 不能同时要求删除与保护")

    groups = rules.get("variant_groups")
    if not isinstance(groups, list):
        raise TypeError("variant_groups 必须是数组（可为空）")
    group_ids: set[str] = set()
    occupied: set[str] = set()
    variants: set[str] = set()
    variant_keeps: set[str] = set()
    normalized_groups = []
    for group in groups:
        if not isinstance(group, dict):
            raise TypeError("每个 variant_group 必须是对象")
        group_id, keep = group.get("group_id"), group.get("keep")
        members = _str_array(group.get("members"), "variant_group.members")
        if not isinstance(group_id, str) or not group_id or group_id in group_ids:
            raise ValueError("variant_group.group_id 为空或重复")
        if len(members) < 2 or keep not in members:
            raise ValueError(f"变体组 {group_id!r} 至少需要2项，且 keep 必须在 members 里")
        if set(members) - rows.keys():
            raise ValueError(f"变体组 {group_id!r} 引用了不存在的 Solid")
        if set(members) & occupied:
            raise ValueError("Solid 不允许同时属于多个变体组")
        if set(members) & confirmed:
            raise ValueError("变体组与单点删除规则冲突，请单独处理")
        group_ids.add(group_id)
        occupied.update(members)
        variant_keeps.add(keep)
        variants.update(set(members) - {keep})
        normalized_groups.append({
            "group_id": group_id, "keep": keep, "members": sorted(members),
            "delete": sorted(set(members) - {keep}),
        })
    if protected & variants:
        raise ValueError("受保护 Solid 不能被变体组删除")
    # R001 below joins explicit and variant deletions after checking the full
    # P3-A catalog. Do not construct a delete list from names/areas.
    zero_volume: dict[str, dict] = {}
    if geometry_report is not None:
        geo = _load_json(geometry_report)
        if geo.get("task") != "geometry" or geo.get("status") != "PASS":
            raise ValueError("仅接受通过的 P3-A geometry 原始报告")
        if not _source_identity(geo.get("source", ""), str(original)):
            raise ValueError("P3-A 来源与当前 .cst 工程路径不同")
        payload = geo.get("geometry")
        if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
            raise TypeError("P3-A geometry 报告缺少完整 features")
        features = payload["features"]
        if len(features) != len(rows):
            raise ValueError("P3-A Solid 数量与 P1 不一致")
        observed = set()
        for rec in features:
            if not isinstance(rec, dict):
                raise TypeError("P3-A Solid 记录必须为对象")
            name = rec.get("identifier")
            if not isinstance(name, str) or name not in rows or name in observed:
                raise ValueError("P3-A 标识与 P1 不一致，或存在重复 ID")
            observed.add(name)
            if rec.get("material") != rows[name]:
                raise ValueError("P3-A 当前 Material 与 P1 不一致")
            volume = rec.get("volume_project_unit3")
            area = rec.get("area_project_unit2")
            if (
                isinstance(volume, bool) or isinstance(area, bool)
                or not isinstance(volume, (int, float))
                or not isinstance(area, (int, float))
                or volume < 0 or area < 0
            ):
                raise ValueError("P3-A 体积/面积类型或数值不合法")
            import math

            if not math.isfinite(volume) or not math.isfinite(area):
                raise ValueError("P3-A 体积/面积不是有限数字")
            if rec.get("zero_volume") is not (volume == 0):
                raise ValueError("P3-A zero_volume 标签与体积数据不一致")
            if volume == 0:
                category = (
                    "ZERO_VOLUME_POSITIVE_AREA_KEEP_REVIEW" if area > 0
                    else "ZERO_VOLUME_ZERO_AREA_DIAGNOSE"
                )
                zero_volume[name] = {
                    "identifier": name,
                    "volume_project_unit3": volume,
                    "area_project_unit2": area,
                    "zero_volume_review_class": category,
                    "decision": "DELETE_RULE_R001_ZERO_VOLUME",
                }
    if geo.get("zero_volume_count") not in (None, len(zero_volume)):
        raise ValueError("P3-A zero_volume_count 与实际 V=0 Solid 数量不一致")
    # R001 is user-approved and unconditional: A>0 and A=0 both qualify.
    zero_ids = set(zero_volume)
    if protected & zero_ids:
        raise ValueError("R001 零体积删除规则与 protected_ids 冲突")
    if variant_keeps & zero_ids:
        raise ValueError("R001 零体积删除规则与变体保留项 keep 冲突")
    delete = confirmed | variants | zero_ids
    if len(delete) >= len(rows):
        raise ValueError("规则会删除全部 Solid：拒绝生成空工程方案")
    note = (
        "R001：所有 P3-A 测得 V=0 的 Solid 均列入删除清单；"
        "其他删除仍只接受明确 ID/变体组。仅生成计划，尚未修改工程。"
    )
    delete_reasons = {
        name: (
            ["R001_ZERO_VOLUME"] if name in zero_ids else []
        ) + (
            ["EXPLICIT_ID"] if name in confirmed else []
        ) + (
            ["VARIANT_NOT_KEEP"] if name in variants else []
        )
        for name in sorted(delete)
    }
    hints = []
    for name in sorted(rows):
        if name in delete:
            continue
        lowered = name.casefold()
        tags = [tag for tag, tokens in REVIEW_HINTS if any(token in lowered for token in tokens)]
        relevant = [token for token in RETAIN_BY_DEFAULT if token in lowered]
        if tags or relevant or name in zero_volume:
            hints.append({
                "identifier": name,
                "review_hints": tags,
                "sensitive_keep_by_default": bool(relevant),
                "zero_volume_review": zero_volume.get(name),
                "decision": "KEEP_UNLESS_EXPLICITLY_CONFIRMED",
            })
    expected_remaining = set(rows) - delete
    return {
        "task": "cleanup-plan",
        "status": "PASS",
        "stage": "已生成显式规则的清理预览，未打开或修改 CST 工程",
        "schema_version": RULE_VERSION,
        "original_source": str(original),
        "source_sha256": _sha256_file(original),
        "geometry_report_sha256": _sha256_file(geometry_report),
        "geometry_report_path": str(geometry_report.resolve()),
        "inventory_catalog_sha256": _digest_catalog(rows),
        "source_solid_count": len(rows),
        "delete_count": len(delete),
        "expected_remaining_count": len(expected_remaining),
        "confirmed_delete_ids": sorted(confirmed),
        "variant_groups": normalized_groups,
        "protected_ids": sorted(protected | variant_keeps),
        "delete_ids": sorted(delete),
        "delete_reasons": delete_reasons,
        "zero_volume_delete_ids": sorted(zero_ids),
        "zero_volume_delete_count": len(zero_ids),
        "keep_ids_count": len(expected_remaining),
        "review_suggestions": hints,
        "review_suggestion_count": len(hints),
        "zero_volume_review_count": len(zero_volume),
        "zero_volume_positive_area_count": sum(
            obj["area_project_unit2"] > 0 for obj in zero_volume.values()
        ),
        "zero_volume_zero_area_count": sum(
            obj["area_project_unit2"] == 0 for obj in zero_volume.values()
        ),
        "geometry_review_supplied": geometry_report is not None,
        "note": note,
        "warnings": [
            "R001 已确定：所有 V=0 Solid 列入删除计划，不受表面积或对象名称影响",
            "名称提示不会删除非零体积对象；真实器件若 V=0 仍执行 R001",
            "变体保留哪一个必须通过 variant_groups 显式指定，不按版本号自动猜测",
            "当前为预览：未执行删除；未来只修改独立 CST 副本并回读验证",
        ],
    }


def validate_plan_for_apply(plan: dict, source: Path) -> tuple[list[str], int]:
    """Fail before opening CST if plan/source have drifted or are inconsistent."""
    if plan.get("task") != "cleanup-plan" or plan.get("status") != "PASS":
        raise ValueError("仅接受由 cleanup-plan 成功生成的报告")
    original = source.resolve(strict=True)
    if not _source_identity(str(original), plan.get("original_source", "")):
        raise ValueError("清理方案指定的原工程路径与本次输入不符")
    if _sha256_file(original) != plan.get("source_sha256"):
        raise ValueError("源 .cst 文件 SHA256 已变化，禁止使用过期清理方案")
    ids = _str_array(plan.get("delete_ids"), "delete_ids")
    if not ids:
        raise ValueError("无已确认删除项，不执行 CST 修改")
    if len(ids) > MAX_DELETE_PER_APPLY:
        raise ValueError(
            f"每次试运行最多删除 {MAX_DELETE_PER_APPLY} 个 Solid，"
            "请拆分方案并逐批验收"
        )
    original_count = plan.get("source_solid_count")
    if isinstance(original_count, bool) or not isinstance(original_count, int):
        raise TypeError("清理方案 Solid 数量字段无效")
    if plan.get("delete_count") != len(ids) or original_count <= len(ids):
        raise ValueError("清理方案删除数量异常")
    if set(ids) & set(_str_array(plan.get("protected_ids"), "protected_ids")):
        raise ValueError("受保护 Solid 被列入删除清单")
    geometry_path = plan.get("geometry_report_path")
    if not isinstance(geometry_path, str) or not geometry_path:
        raise ValueError("R001 缺少 P3-A 原始报告路径")
    geometry = Path(geometry_path).resolve(strict=True)
    if _sha256_file(geometry) != plan.get("geometry_report_sha256"):
        raise ValueError("P3-A 体积报告内容已变化，请重新生成清理方案")
    features = _load_json(geometry).get("geometry", {}).get("features")
    if not isinstance(features, list) or len(features) != original_count:
        raise ValueError("P3-A 原报告中的 Solid 数量与清理方案不一致")
    zero_from_report = set()
    for record in features:
        if not isinstance(record, dict):
            raise TypeError("P3-A Solid 记录不是对象")
        name = record.get("identifier")
        volume = record.get("volume_project_unit3")
        if not isinstance(name, str) or not name:
            raise ValueError("P3-A 含无效 Solid ID")
        if isinstance(volume, bool) or not isinstance(volume, (int, float)):
            raise TypeError("P3-A 体积不是数值")
        if volume == 0:
            zero_from_report.add(name)
    zero_from_plan = set(_str_array(plan.get("zero_volume_delete_ids"), "zero_volume_delete_ids"))
    if zero_from_report != zero_from_plan or not zero_from_report <= set(ids):
        raise ValueError("R001 原报告 V=0 全部删除规则与计划不一致，请重新生成方案")
    return ids, original_count


