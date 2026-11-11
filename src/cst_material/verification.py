"""P0 verification state machine with explicit unverified states."""
from __future__ import annotations

from dataclasses import dataclass

from cst_material.domain import VerificationStatus


@dataclass(frozen=True)
class Check:
    name: str
    status: VerificationStatus
    explanation: str


def overall_status(checks: list[Check]) -> VerificationStatus:
    if not checks:
        return VerificationStatus.NOT_RUN
    statuses = {check.status for check in checks}
    if VerificationStatus.FAIL in statuses:
        return VerificationStatus.FAIL
    if VerificationStatus.BLOCKED in statuses:
        return VerificationStatus.BLOCKED
    if VerificationStatus.NOT_RUN in statuses:
        return VerificationStatus.NOT_RUN
    return VerificationStatus.PASS


def p0_checklist() -> list[Check]:
    return [
        Check(name, VerificationStatus.NOT_RUN, "需在获授权的 CST 2025 Windows 环境验证")
        for name in (
            "连接 CST 2025",
            "打开测试工程副本",
            "枚举 Solid",
            "枚举材料",
            "获取几何边界",
            "给测试 Solid 赋材料",
            "另存为新工程",
            "关闭并重新打开验证",
            "原始工程 SHA256 保持不变",
        )
    ]
