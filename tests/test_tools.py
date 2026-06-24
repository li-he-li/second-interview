"""M3 Mock 工具测试。"""

from __future__ import annotations

import pytest

from agent.knowledge import KnowledgeBase
from agent.tools import (
    AVAILABLE_TOOLS,
    TOOL_WHITELIST,
    ToolError,
    ToolTimeout,
    execute_device_command,
    get_device_status,
    search_knowledge,
)


def test_tool_whitelist_has_three_tools():
    assert TOOL_WHITELIST == {"search_knowledge", "get_device_status", "execute_device_command"}
    assert len(AVAILABLE_TOOLS) == 3


def test_search_knowledge_returns_e42_match():
    kb = KnowledgeBase.load()
    out = search_knowledge("设备报错 E42", kb)
    assert out["count"] >= 1
    assert "e42" in out["matches"][0]["source"].lower()


def test_search_knowledge_empty_for_irrelevant():
    kb = KnowledgeBase.load()
    out = search_knowledge("今天天气怎么样", kb)
    assert out["count"] == 0
    assert out["matches"] == []


def test_search_knowledge_simulate_timeout():
    kb = KnowledgeBase.load()
    with pytest.raises(ToolTimeout):
        search_knowledge("E42", kb, simulate="timeout")


def test_search_knowledge_simulate_error():
    kb = KnowledgeBase.load()
    with pytest.raises(ToolError):
        search_knowledge("E42", kb, simulate="error")


def test_get_device_status_default_online():
    out = get_device_status()
    assert out["status"] == "online"
    assert out["mode"] == "idle"
    assert out["emergency_stop"] is False
    assert out["last_error"] is None


def test_get_device_status_override_offline_and_emergency():
    out = get_device_status(override={"status": "offline", "emergency_stop": True, "last_error": "E30"})
    assert out["status"] == "offline"
    assert out["emergency_stop"] is True
    assert out["last_error"] == "E30"


def test_execute_device_command_dry_run_accepted():
    out = execute_device_command({"action": "move", "x": 100, "y": 50, "z": 20})
    assert out["dry_run"] is True
    assert out["accepted"] is True


def test_execute_device_command_real_execution_rejected():
    out = execute_device_command({"action": "move"}, dry_run=False)
    assert out["accepted"] is False
    assert "disabled" in out["message"].lower()


def test_execute_device_command_simulate_error():
    with pytest.raises(ToolError):
        execute_device_command({"action": "move"}, simulate="error")


def test_execute_device_command_validates_command_type():
    with pytest.raises(ToolError):
        execute_device_command("not-a-dict")
