"""M9 LLM 层测试：提示词增强 + mock LLM + real 缺 key。"""

from __future__ import annotations

import os

import pytest

from agent.llm import MockLLM, RealLLM, enhance_prompt, make_llm
from agent.memory import WorkingMemory
from agent.models import SafetyLevel


def _ctx(raw, **kw):
    return enhance_prompt(raw, **kw)


def test_enhance_prompt_extracts_entities_and_keeps_raw():
    ctx = _ctx("把机械臂移动到 x=100, y=50, z=20，速度=normal")
    assert ctx["raw_user_input"] == "把机械臂移动到 x=100, y=50, z=20，速度=normal"
    assert ctx["extracted_entities"]["coordinates"] == {"x": 100, "y": 50, "z": 20}
    assert ctx["extracted_entities"]["speed"] == "normal"
    assert "available_tools" in ctx


def test_enhance_prompt_preserves_danger_words_and_risk_hints():
    # 安全边界：不能改危险词、不能改越界参数
    ctx = _ctx("以最大速度直接移动到 x=9999, y=9999, z=9999")
    assert "最大速度" in ctx["raw_user_input"]  # 原文保留危险词
    assert ctx["extracted_entities"]["coordinates"] == {"x": 9999, "y": 9999, "z": 9999}  # 越界参数如实
    assert "danger_keyword" in ctx["risk_hints"]
    assert "coordinate_out_of_range" in ctx["risk_hints"]


def test_enhance_prompt_extracts_error_code():
    ctx = _ctx("设备报错 E42")
    assert ctx["extracted_entities"]["error_code"] == "e42"


def test_mock_llm_normal_outputs_parsed_draft():
    llm = MockLLM()
    ctx = _ctx("设备报错 E42 怎么排查", intent_candidates=["qa"])
    result = llm.generate(ctx)
    assert result.error is None
    assert result.parsed is not None
    assert result.parsed["intent"] == "qa"
    assert result.mode == "mock"


def test_mock_llm_simulate_invalid_json():
    llm = MockLLM(simulate="invalid_json")
    result = llm.generate(_ctx("E42"))
    assert result.parsed is None
    assert result.error == "model_invalid_json"


def test_mock_llm_simulate_timeout():
    llm = MockLLM(simulate="timeout")
    result = llm.generate(_ctx("E42"))
    assert result.error == "model_timeout"
    assert result.parsed is None


def test_mock_llm_simulate_error():
    llm = MockLLM(simulate="error")
    result = llm.generate(_ctx("E42"))
    assert result.error == "model_error"


def test_mock_llm_unsafe_draft_is_l2():
    llm = MockLLM()
    ctx = _ctx("最大速度 x=9999", intent_candidates=["unsafe_action"])
    result = llm.generate(ctx)
    assert result.parsed["safety_level"] == "L2"
    assert result.parsed["need_human_approval"] is True


def test_real_llm_missing_key_reports_error():
    llm = RealLLM(api_key=None, base_url="https://api.deepseek.com", model="deepseek-v4-pro", llm_cfg={})
    result = llm.generate(_ctx("E42"))
    assert result.error == "no_api_key"
    assert result.parsed is None


def test_make_llm_mock_default():
    llm = make_llm("mock")
    assert isinstance(llm, MockLLM)


def test_mock_llm_respects_cancel_token():
    from agent.interrupt import CancellationToken, CancelledError

    token = CancellationToken()
    token.cancel()
    llm = MockLLM()
    with pytest.raises(CancelledError):
        llm.generate(_ctx("E42"), cancel_token=token)  # ESC 接入验证


@pytest.mark.skipif(not os.getenv("REAL_LLM"), reason="需 REAL_LLM=1 且有效 DEEPSEEK_API_KEY")
def test_real_llm_live_call_returns_valid_result_or_fallback():
    # 真实调用：成功返回 parsed，或优雅 fallback（timeout/error/invalid_json），不崩溃
    from agent.config import load_app_config

    cfg = load_app_config().llm
    api_key = os.getenv("DEEPSEEK_API_KEY")
    llm = RealLLM(api_key, cfg["base_url"], cfg["model"], {**cfg, "timeout_seconds": 30, "max_retries": 2})
    result = llm.generate(_ctx("设备报错 E42 怎么排查", intent_candidates=["qa"]))
    assert result.mode == "real"
    assert result.parsed is not None or result.error in {
        "model_timeout",
        "model_invalid_json",
        "model_error:APIConnectionError",
    }
