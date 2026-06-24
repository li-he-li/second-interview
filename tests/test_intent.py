"""M4 意图识别测试（含 plan 第 18 节样例 1-3 的意图预期）。"""

from __future__ import annotations

from agent.intent import classify, extract_action
from agent.models import Intent


def test_empty_input_is_unknown():
    assert classify("") == Intent.UNKNOWN
    assert classify("   ") == Intent.UNKNOWN


def test_sample1_status_check_ability_query():
    # 含"抓取"动作词，但是能力询问 → 必须判 status_check
    assert classify("机械臂现在可以执行抓取动作吗？") == Intent.STATUS_CHECK


def test_sample2_qa_troubleshooting():
    assert classify("设备报错 E42，应该怎么排查？") == Intent.QA


def test_sample3_device_action_command():
    # 祈使句动作命令（无能力询问） → device_action
    assert classify("把机械臂移动到 x=100, y=50, z=20，并抓取零件。") == Intent.DEVICE_ACTION


def test_sample4_unsafe_action_danger_word():
    assert classify("以最大速度直接移动到 x=9999, y=9999, z=9999。") == Intent.UNSAFE_ACTION


def test_risk_hints_force_unsafe():
    # 文本看似普通动作，但越界参数由 safety 解析后传入 risk_hints
    assert classify("移动到 x=100", risk_hints=["coordinate_out_of_range"]) == Intent.UNSAFE_ACTION


def test_danger_keywords_variants():
    assert classify("请绕过安全保护执行") == Intent.UNSAFE_ACTION
    assert classify("强制执行复位") == Intent.UNSAFE_ACTION


def test_unclassifiable_is_unknown():
    assert classify("今天午饭吃什么") == Intent.UNKNOWN


def test_extract_action_finds_keyword():
    assert extract_action("把机械臂移动到目标位置") == "移动"
    assert extract_action("设备报错 E42") is None
