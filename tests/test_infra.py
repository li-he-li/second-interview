"""M1 基础设施测试：models / config / trace。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent import config as config_mod
from agent import trace as trace_mod
from agent.config import load_app_config
from agent.models import AgentResponse, Intent, SafetyLevel
from agent.trace import TraceRecorder, new_trace_id, setup_logging


# ---------------- models ----------------

def test_agent_response_valid_and_serializes():
    resp = AgentResponse(
        answer="设备当前在线，可执行低风险抓取。",
        intent=Intent.STATUS_CHECK,
        sources=["device_overview.md#status"],
        confidence=0.85,
        safety_level=SafetyLevel.L0,
        need_human_approval=False,
        final_action="answered_from_knowledge_base",
    )
    dumped = resp.model_dump(mode="json")
    # 题目要求的字段必须齐全
    for key in (
        "answer",
        "intent",
        "sources",
        "confidence",
        "safety_level",
        "need_human_approval",
        "tool_calls",
        "final_action",
        "error",
    ):
        assert key in dumped
    assert dumped["intent"] == "status_check"
    assert dumped["safety_level"] == "L0"
    assert dumped["error"] is None


@pytest.mark.parametrize("bad", [-0.01, 1.01, 2.0])
def test_confidence_out_of_range_rejected(bad):
    with pytest.raises(ValidationError):
        AgentResponse(
            answer="x",
            intent=Intent.UNKNOWN,
            confidence=bad,
            safety_level=SafetyLevel.L2,
            need_human_approval=True,
            final_action="rejected",
        )


def test_intent_and_safety_enum_values():
    assert {i.value for i in Intent} == {
        "qa",
        "status_check",
        "device_action",
        "unsafe_action",
        "unknown",
    }
    assert {s.value for s in SafetyLevel} == {"L0", "L1", "L2"}


# ---------------- config ----------------

def test_load_config_normal_has_expected_keys():
    cfg = load_app_config()
    assert "coordinate_limits" in cfg.safety
    assert cfg.safety["coordinate_limits"]["x"] == [-1000, 1000]
    assert cfg.llm["model"] == "deepseek-v4-pro"
    assert cfg.memory["compress_trigger_tokens"] == 48000
    assert cfg.warnings == []  # 正常加载无 warning


def test_load_config_missing_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))  # 空目录
    cfg = load_app_config()
    assert len(cfg.warnings) == 3  # 三份配置都 missing
    assert all("config_warning" in w for w in cfg.warnings)
    # 保守默认仍安全收紧
    assert cfg.safety["approval"]["l2_requires_approval"] is True
    assert cfg.safety["danger_keywords"]  # 非空


def test_load_config_invalid_falls_back(monkeypatch, tmp_path):
    (tmp_path / "safety_rules.json").write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    cfg = load_app_config()
    assert any("safety_rules.json invalid" in w for w in cfg.warnings)
    assert cfg.safety == config_mod.DEFAULT_SAFETY  # 回退默认


# ---------------- trace ----------------

def test_trace_id_unique():
    a, b = new_trace_id(), new_trace_id()
    assert a != b and a.startswith("trace-")


def test_trace_recorder_save(tmp_path):
    rec = TraceRecorder("trace-test-fixed")
    rec.set("raw_input", "设备报错 E42")
    rec.add_warnings(["config_warning: x"])
    path = rec.save(runs_dir=tmp_path)
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["trace_id"] == "trace-test-fixed"
    assert data["raw_input"] == "设备报错 E42"
    assert data["warnings"] == ["config_warning: x"]


def test_setup_logging_does_not_duplicate_handlers(monkeypatch, tmp_path):
    monkeypatch.setattr(trace_mod, "LOGS_DIR", tmp_path)
    logger = setup_logging()
    n = len(logger.handlers)
    setup_logging()  # 再次调用不应重复挂载
    assert len(logger.handlers) == n
