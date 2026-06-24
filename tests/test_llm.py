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
    ctx = _ctx("设备报错 E42 怎么排查")
    result = llm.generate(ctx)
    assert result.error is None
    assert result.parsed is not None
    # E42 → MockLLM 决定调 search_knowledge（final=false）
    assert result.parsed["final"] is False
    assert result.parsed["tool_calls"][0]["tool"] == "search_knowledge"
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


def test_mock_llm_unsafe_input_skips_tools():
    # 危险输入：MockLLM 直接 final=true（不调工具，安全等级由 runner 本地定）
    llm = MockLLM()
    ctx = _ctx("最大速度 x=9999")
    result = llm.generate(ctx)
    assert result.parsed["final"] is True
    assert result.parsed["tool_calls"] == []


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
    result = llm.generate(_ctx("设备报错 E42 怎么排查"))
    assert result.mode == "real"
    assert result.parsed is not None or result.error in {
        "model_timeout",
        "model_invalid_json",
        "model_error:APIConnectionError",
    }


def test_real_llm_stream_cancel_interrupts_mid_stream():
    # P1：用 fake streaming client 证明 RealLLM 流式循环里 ESC 真能打断
    from agent.interrupt import CancelledError, CancellationToken

    class _Delta:
        def __init__(self, t):
            self.content = t

    class _Choice:
        def __init__(self, t):
            self.delta = _Delta(t)

    class _Chunk:
        def __init__(self, t):
            self.choices = [_Choice(t)]

    class _FakeStream:
        def __init__(self, token):
            self.token = token

        def __iter__(self):
            for i in range(20):
                if i == 3:
                    self.token.cancel()  # 第 4 个 chunk 后取消
                yield _Chunk(f"c{i}")

    class _Completions:
        def __init__(self, token):
            self._token = token

        def create(self, **kw):
            return _FakeStream(self._token)

    class _FakeClient:
        def __init__(self, token):
            self.chat = type("C", (), {"completions": _Completions(token)})()

    token = CancellationToken()
    llm = RealLLM("fake-key", "x", "m", {"stream": True, "max_retries": 1})
    llm.client = _FakeClient(token)
    with pytest.raises(CancelledError):
        llm.generate({"raw_user_input": "x"}, cancel_token=token)


def test_real_llm_client_init_failure_reported():
    # P2：key 存在但 SDK 初始化失败，应报 model_error:client_init 而非 no_api_key
    llm = RealLLM("some-key", "x", "m", {})
    llm.client = None
    llm._init_error = "ImportError"
    result = llm.generate({"raw_user_input": "x"})
    assert result.error == "model_error:client_init:ImportError"


def test_validate_loop_output_accepts_valid():
    from agent.llm import validate_loop_output

    out, err = validate_loop_output(
        {"answer": "a", "tool_calls": [{"tool": "search_knowledge", "input": {"query": "x"}}], "final": False}
    )
    assert err is None and out is not None
    assert out["tool_calls"][0]["tool"] == "search_knowledge"
    assert out["final"] is False


def test_validate_loop_output_final_defaults_when_no_tools():
    from agent.llm import validate_loop_output

    out, err = validate_loop_output({"answer": "hi"})
    assert err is None
    assert out["final"] is True  # 无工具默认 final
    assert out["tool_calls"] == []


def test_validate_loop_output_normalizes_bad_tool_calls():
    from agent.llm import validate_loop_output

    out, _ = validate_loop_output({"answer": "a", "tool_calls": ["not_a_dict", {"tool": 123}]})
    assert out["tool_calls"] == []  # 非法 tool_calls 被过滤


def test_validate_loop_output_rejects_non_dict():
    from agent.llm import validate_loop_output

    out, err = validate_loop_output("not a dict")
    assert out is None
    assert err == "schema_invalid:not_a_dict"


def test_try_parse_strips_markdown_code_fence():
    from agent.llm import _try_parse

    raw = '```json\n{"answer": "x", "tool_calls": [], "final": true}\n```'
    data, err = _try_parse(raw)
    assert err is None and data is not None
    assert data["answer"] == "x"


def test_try_parse_extracts_json_from_surrounding_noise():
    from agent.llm import _try_parse

    raw = '好的，结果如下：{"answer": "y", "final": true} 谢谢'
    data, err = _try_parse(raw)
    assert err is None and data is not None
    assert data["answer"] == "y"


def test_try_parse_rejects_pure_garbage():
    from agent.llm import _try_parse

    data, err = _try_parse("完全不是 JSON 的一段文字")
    assert data is None
    assert err == "model_invalid_json"
