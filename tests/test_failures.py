"""M11 失败/边界场景补充测试（runner 层，验证安全治理不信任 LLM）。"""

from __future__ import annotations

import json

from agent.config import AppConfig
from agent.llm import LLMResult
from agent.models import AgentResponse, Intent, SafetyLevel
from agent.runner import Agent


class _FakeLLM:
    """可控 LLM：返回固定草稿，用于测试 runner 对 LLM 输出的安全复核。"""

    def __init__(self, draft, error=None):
        self.draft = draft
        self.error = error
        self.mode = "mock"

    def generate(self, ctx, cancel_token=None):
        raw = json.dumps(self.draft, ensure_ascii=False) if self.draft else ""
        return LLMResult(raw, self.draft, self.error, "mock")


def test_fabricated_sources_filtered_by_runner():
    fake = _FakeLLM(
        {
            "answer": "a",
            "intent": "qa",
            "sources": ["troubleshooting.md#e42-夹爪气压传感器异常", "FAKE.md#nonexistent", "made_up.md#x"],
            "confidence": 0.8,
            "safety_level": "L0",
            "need_human_approval": False,
            "final_action": "x",
        }
    )
    r = Agent(llm_mode="mock", llm=fake).handle("设备报错 E42")
    assert "FAKE.md#nonexistent" not in r.sources
    assert "made_up.md#x" not in r.sources
    assert any("e42" in s.lower() for s in r.sources)  # 真实来源保留


def test_llm_unsafe_level_overridden_by_safety():
    # LLM 草稿说 device_action/L0，但输入含最大速度+越界 → safety.py 覆盖为 L2
    fake = _FakeLLM(
        {"answer": "ok", "intent": "device_action", "sources": [], "confidence": 0.9,
         "safety_level": "L0", "need_human_approval": False, "final_action": "x"}
    )
    r = Agent(llm_mode="mock", llm=fake).handle("以最大速度移动到 x=9999")
    assert r.safety_level == SafetyLevel.L2  # 不信任 LLM 的 L0
    assert r.need_human_approval is True


def test_runner_works_with_empty_config():
    agent = Agent(llm_mode="mock", app_config=AppConfig(safety={}, llm={}, memory={}))
    r = agent.handle("以最大速度移动到 x=9999")
    assert r.safety_level == SafetyLevel.L2  # 配置缺失仍保守拦截
    r2 = agent.handle("设备报错 E42")
    assert r2.safety_level == SafetyLevel.L0


def test_llm_error_still_returns_valid_json():
    fake = _FakeLLM(None, error="model_invalid_json")
    r = Agent(llm_mode="mock", llm=fake).handle("设备报错 E42")
    assert r.intent is not None  # 规则兜底仍分类
    assert r.error is not None
    assert r.error.type == "model_invalid_json"


def test_cli_real_mode_passes_api_key_and_model(monkeypatch):
    from agent import cli

    seen = {}

    class _FakeAgent:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def handle(self, *_args, **_kwargs):
            from agent.models import AgentResponse, Intent

            return AgentResponse(
                answer="ok",
                intent=Intent.QA,
                sources=[],
                confidence=1.0,
                safety_level=SafetyLevel.L0,
                need_human_approval=False,
                tool_calls=[],
                final_action="answered",
                error=None,
            )

    monkeypatch.setattr(cli, "Agent", _FakeAgent)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
    cli.main(["--llm", "real", "--model", "deepseek-v4-pro", "E42"])

    assert seen["llm_mode"] == "real"
    assert seen["api_key"] == "fake-key"
    assert seen["model"] == "deepseek-v4-pro"


def test_cli_session_commands(monkeypatch, capsys):
    from agent import cli

    class _FakeMemory:
        device_state = {"status": "online"}
        pinned_safety = ["x"]
        turns = [1]
        summary = {"summary": "x"}

        def token_count(self):
            return 12

    class _FakeAgent:
        llm_mode = "mock"
        memory = _FakeMemory()

    inputs = iter(["/help", "/status", "/clear", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

    cli._repl(_FakeAgent())
    out = capsys.readouterr().out
    assert "device safety agent" in out
    assert "commands:" in out
    assert "memory_tokens" in out
    assert "session memory cleared" in out
    assert _FakeAgent.memory.turns == []


def test_cli_chat_output_hides_json_in_interactive_window(monkeypatch, capsys):
    from agent import cli

    class _FakeMemory:
        device_state = {}
        pinned_safety = []
        turns = []
        summary = {}

        def token_count(self):
            return 0

    class _FakeAgent:
        llm_mode = "mock"
        memory = _FakeMemory()

        def handle(self, *_args, on_event=None, **_kwargs):
            if on_event:  # 模拟 agent loop 的 answer 事件
                on_event("answer", "这是给用户看的回答")
            return AgentResponse(
                answer="这是给用户看的回答",
                intent=Intent.QA,
                sources=["troubleshooting.md#e42-夹爪气压传感器异常"],
                confidence=0.8,
                safety_level=SafetyLevel.L0,
                need_human_approval=False,
                tool_calls=[],
                final_action="answered",
                error=None,
            )

    inputs = iter(["设备报错 E42", "/trace", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    cli._repl(_FakeAgent())
    out = capsys.readouterr().out
    assert "这是给用户看的回答" in out
    assert '"answer"' not in out  # 交互窗口不直接渲染完整 JSON
    assert "runs/<trace_id>.json" in out


def test_cli_responder_accepts_yes_no_allyes(monkeypatch):
    from agent import cli
    from agent.approval import ApprovalRequest

    req = ApprovalRequest(SafetyLevel.L2, "execute_device_command", "x", "risk")
    for raw, expected in [("yes", "yes"), ("no", "no"), ("allyes", "allyes")]:
        monkeypatch.setattr("builtins.input", lambda _prompt="", raw=raw: raw)
        assert cli._cli_responder(req) == expected
