"""Audited material decisions; unknown geometry is NEVER assigned by guess."""
from __future__ import annotations


def match_materials(
    catalog: dict, recognized: list[dict], knowledge: dict
) -> dict:
    manual = knowledge.get("confirmed_solids", {})
    refs = knowledge.get("reference_shapes", [])
    if not isinstance(manual, dict) or not isinstance(refs, list):
        raise ValueError("材料知识库数据类型无效")
    accepted_references: dict[str, dict] = {}
    for ref in refs:
        if not isinstance(ref, dict) or not ref.get("approved"):
            continue
        digest = ref.get("step_sha256")
        if not isinstance(digest, str):
            raise ValueError("材料参考模型缺少 SHA256")
        if digest in accepted_references and accepted_references[digest] != ref:
            raise ValueError("同一几何参考模型有冲突的确认信息")
        accepted_references[digest] = ref
    rec_by_name = {row["solid_id"]: row for row in recognized}
    assignments = {}
    decisions = []
    unknown = []
    for row in catalog["records"]:
        solid_id = row["solid_id"]
        identity = rec_by_name.get(solid_id)
        if identity is None:
            raise ValueError(f"缺少 Solid 身份识别记录：{solid_id}")
        matched = manual.get(solid_id)
        source = "APPROVED_SOLID_ID"
        if matched is None:
            matched = accepted_references.get(row["features"].get("step_sha256"))
            source = "EXACT_APPROVED_REFERENCE_SHA256"
        valid = (
            isinstance(matched, dict)
            and matched.get("approved") is True
            and matched.get("material_approved") is True
            and identity["status"] == "CONFIRMED"
            and isinstance(matched.get("material"), str)
            and bool(matched["material"])
        )
        if not valid:
            unknown.append(solid_id)
            decisions.append({
                "solid_id": solid_id,
                "status": "NEEDS_REVIEW",
                "reason": "没有经过明确批准的 Solid 身份与材料映射",
            })
            continue
        material = matched["material"]
        existing = row.get("material")
        if existing != material:
            assignments[solid_id] = material
        decisions.append({
            "solid_id": solid_id,
            "status": "APPROVED",
            "source": source,
            "identity": identity["identity"],
            "old_material": existing,
            "new_material": material,
            "change_needed": existing != material,
        })
    return {
        "assignments": assignments,
        "decisions": decisions,
        "unknown_ids": unknown,
        "approved_count": len(decisions) - len(unknown),
        "needs_review_count": len(unknown),
    }
