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


def test_local_qa_not_overridden_by_llm_unknown():
    r = _agent().handle("设备报错 E42")
    assert r.intent == Intent.QA
    assert r.safety_level == SafetyLevel.L0
    assert r.need_human_approval is False


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
    assert r.final_action == "answered"


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


def test_invalid_json_falls_back_gracefully():
    r = _agent(simulate="invalid_json").handle("设备报错 E42，应该怎么排查？")
    assert r.error is not None
    assert r.error.type == "model_invalid_json"
    assert r.intent == Intent.QA  # 规则兜底仍分类
    assert r.sources == []  # LLM 故障无法检索，不编造来源


def test_search_knowledge_timeout_is_reported_as_failed_tool():
    r = Agent(llm_mode="mock", tool_simulate="timeout").handle("设备报错 E42")
    assert r.sources == []
    assert r.tool_calls[0].tool == "search_knowledge"
    assert r.tool_calls[0].status == ToolCallStatus.FAILED
    assert r.safety_level == SafetyLevel.L2


def test_trace_file_written_to_runs(tmp_path):
    # 验证 handle 产出 <trace_id>.json（conftest autouse 已把 RUNS_DIR 指向 tmp_path）
    before = len(list(tmp_path.glob("*.json")))
    _agent().handle("设备报错 E42")
    after = len(list(tmp_path.glob("*.json")))
    assert after > before


def test_distance_out_of_range_upgrades_to_l2():
    # direction/distance 类命令越界 → L2（修复 distance 不被 safety 检查的缺口）
    r = _agent().handle("驱动机械臂向前移动 3000000cm")
    assert r.safety_level == SafetyLevel.L2
    assert r.need_human_approval is True
