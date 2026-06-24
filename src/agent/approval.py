"""人工审批门控（会话级）。

选项（plan 第 11 节）：

- ``yes``：仅批准本次工具/命令，下一次 L2 仍需弹窗。
- ``no``：拒绝本次，工具调用记 skipped。
- ``allyes``：当前会话级全局放行，后续 L2 调用跳过弹窗；会话结束失效，不持久化。

硬约束（不可被审批改变）：

- allyes 不能降低风险等级，也不能把 L2 改成 L1（level 由 safety 决定，本模块不触碰）。
- L2 即使审批通过，最终 JSON 仍必须 ``need_human_approval=True``（由 runner 保证）。
- L2 审批通过后的允许范围仅限 dry-run / 诊断 / 解释 / 日志 / 安全建议；仍禁止真实执行。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .models import SafetyLevel

ApprovalResponder = Callable[["ApprovalRequest"], Optional[str]]


@dataclass
class ApprovalRequest:
    safety_level: SafetyLevel
    tool_name: str
    command: str  # normalized
    risk_reason: str


@dataclass
class ApprovalResult:
    approved: bool
    decision: str  # yes / no / allyes / auto_yes / auto_no
    session_global: bool = False  # 是否因 allyes 自动放行
    note: str = ""


def approval_key(level: SafetyLevel, tool_name: str, command: str, risk_reason: str) -> str:
    """生成稳定审批键，用于 trace 记录与请求识别。"""

    return f"{level.value}|{tool_name}|{command}|{risk_reason}"


class ApprovalGate:
    """会话级审批门控，仅持有 allyes 全局标志（yes 不缓存，天然单次）。"""

    def __init__(self) -> None:
        self._allyes = False

    @property
    def allyes_active(self) -> bool:
        return self._allyes

    def request(self, req: ApprovalRequest, *, responder: Optional[ApprovalResponder] = None) -> ApprovalResult:
        # allyes 已激活：会话级自动放行（仍保持原 level，不降级）
        if self._allyes:
            return ApprovalResult(
                approved=True,
                decision="auto_yes",
                session_global=True,
                note="auto-approved by session allyes",
            )
        if responder is None:
            # 非交互模式默认拒绝，安全优先
            return ApprovalResult(approved=False, decision="auto_no", note="no responder, default reject")
        choice = (responder(req) or "").strip().lower()
        return self._apply(choice)

    def _apply(self, choice: str) -> ApprovalResult:
        if choice == "allyes":
            self._allyes = True
            return ApprovalResult(approved=True, decision="allyes", session_global=True, note="session-global approval set")
        if choice == "yes":
            return ApprovalResult(approved=True, decision="yes", session_global=False, note="single approval")
        # no 或非法输入
        return ApprovalResult(approved=False, decision="no", session_global=False, note="rejected by user")
