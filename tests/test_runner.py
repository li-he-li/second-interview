"""M10 Runner 端到端测试（mock 模式，覆盖 plan 第 18 节样例与失败场景）。"""

from __future__ import annotations

from agent.interrupt import CancellationToken
from agent.models import Intent, SafetyLevel, ToolCallStatus
from agent.runner import Agent


def _agent(simulate=None):
    return Agent(llm_mode="mock", simulate=simulate)


def test_sample1_status_check_calls_get_status():
    r = _agent().handle("机械臂现在可以执行抓取动作吗？")
    assert r.intent == Intent.STATUS_CHECK
    assert r.safety_level == SafetyLevel.L0
    assert any(tc.tool == "get_device_status" and tc.status == ToolCallStatus.SUCCESS for tc in r.tool_calls)


def test_sample2_qa_e42_has_sources():
    r = _agent().handle("设备报错 E42，应该怎么排查？")
    assert r.intent == Intent.QA
    assert r.safety_level == SafetyLevel.L0
    assert any("e42" in s.lower() for s in r.sources)
    assert any(tc.tool == "search_knowledge" and tc.status == ToolCallStatus.SUCCESS for tc in r.tool_calls)


def test_sample3_device_action_l1_approved_runs_dry_run():
    r = _agent().handle("把机械臂移动到 x=100, y=50, z=20，并抓取零件。", responder=lambda _req: "yes")
    assert r.intent == Intent.DEVICE_ACTION
    assert r.safety_level == SafetyLevel.L1
    execs = [tc for tc in r.tool_calls if tc.tool == "execute_device_command"]
    assert execs and execs[0].status == ToolCallStatus.SUCCESS
    assert execs[0].output.get("dry_run") is True  # 仅 dry-run


def test_sample4_unsafe_l2_no_real_execution():
    r = _agent().handle("以最大速度直接移动到 x=9999, y=9999, z=9999。")
    assert r.intent == Intent.UNSAFE_ACTION
    assert r.safety_level == SafetyLevel.L2
    assert r.need_human_approval is True
    assert not any(
        tc.tool == "execute_device_command" and tc.status == ToolCallStatus.SUCCESS for tc in r.tool_calls
    )


def test_sample5_chat_smalltalk_skips_tools():
    # 闲聊/问候（unknown 无设备信号）→ L0 自由对话，不检索/不工具/不审批
    r = _agent().handle("今天午饭吃什么呀")
    assert r.safety_level == SafetyLevel.L0
    assert r.need_human_approval is False
    assert r.tool_calls == []
    assert r.final_action == "chat"


def test_unknown_device_model_returns_l2_without_fabrication():
    r = _agent().handle("QX999 设备的专用润滑周期是什么？")
    assert r.sources == []
    assert r.safety_level == SafetyLevel.L2
    assert r.need_human_approval is True


def test_empty_input_unknown_l2():
    r = _agent().handle("")
    assert r.intent == Intent.UNKNOWN
    assert r.safety_level == SafetyLevel.L2
    assert r.need_human_approval is True


def test_l1_approval_no_skips_execution():
    r = _agent().handle("把机械臂移动到 x=100, y=50, z=20", responder=lambda _req: "no")
    skipped = [tc for tc in r.tool_calls if tc.tool == "execute_device_command"]
    assert skipped and skipped[0].status == ToolCallStatus.SKIPPED


def test_esc_cancel_returns_cancelled_json():
    token = CancellationToken()
    token.cancel()
    r = _agent().handle("设备报错 E42", cancel_token=token)
    assert r.final_action == "cancelled_by_user"
    assert r.error is not None and r.error.type == "cancelled"
    assert r.safety_level == SafetyLevel.L2


def test_invalid_json_falls_back_to_rules():
    r = _agent(simulate="invalid_json").handle("设备报错 E42，应该怎么排查？")
    assert r.intent == Intent.QA  # 规则兜底仍正确分类
    assert any("e42" in s.lower() for s in r.sources)  # 来源来自真实检索


def test_search_knowledge_timeout_is_reported_as_failed_tool():
    r = Agent(llm_mode="mock", tool_simulate="timeout").handle("设备报错 E42")
    assert r.sources == []
    assert r.tool_calls[0].tool == "search_knowledge"
    assert r.tool_calls[0].status == ToolCallStatus.FAILED
    assert r.safety_level == SafetyLevel.L2


def test_trace_file_written_to_runs():
    # 验证 handle 产出 runs/<trace_id>.json 运行证据
    from pathlib import Path

    runs = Path("runs")
    before = len(list(runs.glob("*.json"))) if runs.exists() else 0
    _agent().handle("设备报错 E42")
    after = len(list(runs.glob("*.json")))
    assert after > before
