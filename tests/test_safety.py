"""M5 安全策略测试。"""

from __future__ import annotations

from agent.models import Intent, SafetyLevel
from agent.safety import (
    check_param_risks,
    detect_danger_words,
    evaluate,
    parse_params,
)


def test_parse_params_extracts_coords_speed_force():
    p = parse_params("移动到 x=100, y=50, z=20，速度=normal，力度=30")
    assert p["coordinates"] == {"x": 100, "y": 50, "z": 20}
    assert p["speed"] == "normal"
    assert p["force"] == 30


def test_parse_params_max_speed_detected():
    p = parse_params("以最大速度移动")
    assert p["speed"] == "max"


def test_check_param_risks_out_of_range():
    hints, reasons = check_param_risks({"coordinates": {"x": 9999}, "speed": None, "force": None}, {})
    assert "coordinate_out_of_range" in hints
    assert reasons


def test_check_param_risks_normal_coords_clean():
    hints, _ = check_param_risks({"coordinates": {"x": 100, "y": 50, "z": 20}, "speed": None, "force": None}, {})
    assert hints == []


def test_check_param_risks_force_exceeds():
    hints, _ = check_param_risks({"coordinates": {}, "speed": None, "force": 80}, {})
    assert "force_exceeds_limit" in hints


def test_evaluate_qa_is_l0():
    a = evaluate("设备报错 E42 怎么排查", Intent.QA)
    assert a.level == SafetyLevel.L0
    assert a.need_human_approval is False


def test_evaluate_low_risk_device_action_is_l1():
    a = evaluate("把机械臂移动到 x=100, y=50, z=20", Intent.DEVICE_ACTION)
    assert a.level == SafetyLevel.L1
    assert a.need_human_approval is True  # 配置 l1_requires_approval 默认 True


def test_evaluate_unsafe_action_is_l2():
    a = evaluate("以最大速度直接移动到 x=9999, y=9999, z=9999", Intent.UNSAFE_ACTION)
    assert a.level == SafetyLevel.L2
    assert a.need_human_approval is True
    assert "coordinate_out_of_range" in a.risk_hints


def test_evaluate_unknown_is_l2():
    a = evaluate("", Intent.UNKNOWN)
    assert a.level == SafetyLevel.L2
    assert a.need_human_approval is True


def test_evaluate_device_action_blocked_when_offline():
    a = evaluate("移动到 x=100", Intent.DEVICE_ACTION, device_status={"status": "offline"})
    assert a.level == SafetyLevel.L2
    assert any("device_status_blocks_action" in r for r in a.reasons)


def test_evaluate_device_action_blocked_when_emergency_stop():
    a = evaluate("抓取零件", Intent.DEVICE_ACTION, device_status={"emergency_stop": True, "status": "online"})
    assert a.level == SafetyLevel.L2


def test_evaluate_danger_word_upgrades_to_l2():
    a = evaluate("请强制执行复位", Intent.UNSAFE_ACTION)
    assert a.level == SafetyLevel.L2
    assert a.danger_words


def test_evaluate_normal_action_clean_when_online():
    a = evaluate("抓取零件", Intent.DEVICE_ACTION, device_status={"status": "online"})
    assert a.level == SafetyLevel.L1


def test_detect_danger_words_from_config():
    words = detect_danger_words("请绕过安全保护", {"danger_keywords": ["绕过安全"]})
    assert words == ["绕过安全"]


def test_evaluate_works_with_empty_config_conservative():
    # 配置缺失：坐标默认 ±1000，9999 仍越界
    a = evaluate("移动到 x=9999", Intent.DEVICE_ACTION, safety_cfg={})
    assert a.level == SafetyLevel.L2


def test_l2_approval_cannot_be_disabled_by_config():
    # P0 回归：即使配置 l2_requires_approval=False，L2 仍必须 need_human_approval=True
    a = evaluate(
        "以最大速度移动到 x=9999",
        Intent.UNSAFE_ACTION,
        safety_cfg={"approval": {"l2_requires_approval": False}},
    )
    assert a.level == SafetyLevel.L2
    assert a.need_human_approval is True


def test_parse_params_extracts_distance():
    p = parse_params("向前移动 3000000cm")
    assert p["distance"] == 3000000


def test_check_param_risks_distance_out_of_range():
    hints, _ = check_param_risks(
        {"coordinates": {}, "speed": None, "force": None, "distance": 3000000}, {}
    )
    assert "distance_out_of_range" in hints


def test_check_param_risks_distance_within_limit():
    hints, _ = check_param_risks(
        {"coordinates": {}, "speed": None, "force": None, "distance": 20}, {}
    )
    assert "distance_out_of_range" not in hints
