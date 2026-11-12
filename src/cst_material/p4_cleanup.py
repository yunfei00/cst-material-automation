"""Explicit-rule, fail-closed CST structure cleanup planner and copy-only executor.

The original engineering project is NEVER modified. Name hints are REVIEW-only.
No implicit deletion based on USB/headphone jack/motor/front camera names.
Only complete, engineer-confirmed Solid IDs and complete explicit variant groups
are eligible for the deletion plan.
"""
from __future__ import annotations

import hashlib
import json
import ntpath
from pathlib import Path
from typing import Any

RULE_VERSION = 1
MAX_DELETE_PER_APPLY = 100
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


def build_cleanup_plan(source: Path, inventory: Path, rules_path: Path) -> dict:
    """Build immutable explicit-ID decisions, plus non-actionable name hints."""
    original = source.resolve(strict=True)
    if not original.is_file() or original.suffix.casefold() != ".cst":
        raise ValueError("输入必须是现有 .cst 工程")
    rows, report_source = read_p1_inventory(inventory)
    if not _source_identity(str(original), report_source):
        raise ValueError("P1 inventory 来源工程路径与本次源 .cst 不一致")

    rules = _load_json(rules_path)
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
    delete = confirmed | variants
    if not delete:
        note = "当前无已确认删除项：只生成待人工检查的提示，未修改工程"
    else:
        note = "只包含明确 ID 和完整变体组选中的 Solid；仍需复核再在副本执行"
    if len(delete) >= len(rows):
        raise ValueError("拒绝清空所有 Solid")
    hints = []
    for name in sorted(rows):
        if name in delete:
            continue
        lowered = name.casefold()
        tags = [tag for tag, tokens in REVIEW_HINTS if any(token in lowered for token in tokens)]
        relevant = [token for token in RETAIN_BY_DEFAULT if token in lowered]
        if tags or relevant:
            hints.append({
                "identifier": name,
                "review_hints": tags,
                "sensitive_keep_by_default": bool(relevant),
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
        "inventory_catalog_sha256": _digest_catalog(rows),
        "source_solid_count": len(rows),
        "delete_count": len(delete),
        "expected_remaining_count": len(expected_remaining),
        "confirmed_delete_ids": sorted(confirmed),
        "variant_groups": normalized_groups,
        "protected_ids": sorted(protected | variant_keeps),
        "delete_ids": sorted(delete),
        "keep_ids_count": len(expected_remaining),
        "review_suggestions": hints,
        "review_suggestion_count": len(hints),
        "note": note,
        "warnings": [
            "名称提示仅供人工检查，不会自动删除",
            "USB/耳机/马达/前摄/连接器等真实器件默认保留，是否剔除取决于电磁仿真目标",
            "变体保留哪一个必须通过 variant_groups 显式指定，不按版本号自动猜测",
            "尚未执行任何实际删除；建议先用非生产副本做少量 Solid 实机验收",
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
        raise ValueError("清理方案 Solid 数量字段无效")
    if plan.get("delete_count") != len(ids) or original_count <= len(ids):
        raise ValueError("清理方案删除数量异常")
    if set(ids) & set(_str_array(plan.get("protected_ids"), "protected_ids")):
        raise ValueError("受保护 Solid 被列入删除清单")
    return ids, original_count
