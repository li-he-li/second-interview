"""M6 人工审批测试（含 plan 样例 9/10/18/19）。"""

from __future__ import annotations

from agent.approval import ApprovalGate, ApprovalRequest, approval_key
from agent.models import SafetyLevel

REQ = ApprovalRequest(SafetyLevel.L2, "execute_device_command", "move x=100", "low_risk_device_action")


def test_yes_approves_but_does_not_cache():
    # yes 仅本次放行，下次同请求仍需询问（plan 样例 18）
    gate = ApprovalGate()
    calls = []

    def responder(_req):
        calls.append(_req)
        return "yes"

    r1 = gate.request(REQ, responder=responder)
    assert r1.approved and r1.decision == "yes" and not r1.session_global
    r2 = gate.request(REQ, responder=responder)
    assert r2.approved and r2.decision == "yes"
    assert len(calls) == 2  # 两次都真实询问


def test_no_rejects():
    gate = ApprovalGate()
    r = gate.request(REQ, responder=lambda _r: "no")
    assert r.approved is False
    assert r.decision == "no"


def test_no_responder_defaults_reject():
    gate = ApprovalGate()
    r = gate.request(REQ)  # 非交互
    assert r.approved is False
    assert r.decision == "auto_no"


def test_allyes_skips_subsequent_within_session():
    # allyes 后当前会话后续 L2 跳过弹窗（plan 样例 10/19）
    gate = ApprovalGate()
    calls = []

    def responder(_req):
        calls.append(_req)
        return "allyes"

    r1 = gate.request(REQ, responder=responder)
    assert r1.approved and r1.decision == "allyes" and r1.session_global
    assert gate.allyes_active is True
    # 后续请求不再调用 responder
    r2 = gate.request(REQ, responder=responder)
    assert r2.approved and r2.session_global and r2.decision == "auto_yes"
    assert len(calls) == 1


def test_allyes_does_not_downgrade_level():
    # allyes 只放行执行，不改变 level；level 由调用方持有，审批结果不含降级
    gate = ApprovalGate()
    gate.request(REQ, responder=lambda _r: "allyes")
    r = gate.request(REQ, responder=lambda _r: "yes")
    assert r.approved is True
    # 审批结果不携带任何 level 信息，无法降级
    assert not hasattr(r, "safety_level") or r.safety_level is None


def test_invalid_choice_treated_as_no():
    gate = ApprovalGate()
    r = gate.request(REQ, responder=lambda _r: "maybe")
    assert r.approved is False
    assert r.decision == "no"


def test_approval_key_format_and_stability():
    k1 = approval_key(SafetyLevel.L2, "execute_device_command", "move x=100", "low_risk")
    k2 = approval_key(SafetyLevel.L2, "execute_device_command", "move x=100", "low_risk")
    assert k1 == k2
    assert "L2" in k1 and "execute_device_command" in k1
