"""M7 工作记忆测试。"""

from __future__ import annotations

from agent.memory import MemoryConfig, WorkingMemory, count_tokens


def test_token_count_positive():
    assert count_tokens("hello world 设备安全") > 0


def test_add_turn_and_safety_fact():
    mem = WorkingMemory()
    mem.add_turn("user", "移动到 x=100")
    mem.add_turn("assistant", "已 dry-run")
    mem.add_safety_fact("coordinate_out_of_range x=9999")
    assert len(mem.turns) == 2
    assert mem.pinned_safety == ["coordinate_out_of_range x=9999"]
    # 重复安全事实不重复记录
    mem.add_safety_fact("coordinate_out_of_range x=9999")
    assert len(mem.pinned_safety) == 1


def test_compression_triggers_and_keeps_recent_turns():
    mem = WorkingMemory()
    cfg = MemoryConfig(compress_trigger_tokens=30, keep_last_turns=2, hard_trim_tokens=10_000_000)
    for i in range(8):
        mem.add_turn("user", f"第 {i} 条足够长的用户输入用于累积 token 计数 " * 4)
    mem.add_safety_fact("danger_keyword:最大速度")
    result = mem.maybe_compress(cfg)
    assert result["compressed"] is True
    assert result["tokens_after"] <= result["tokens_before"]
    # 保留最近 keep_last_turns 轮
    assert len(mem.turns) <= cfg.keep_last_turns
    # pinned safety 不丢
    assert "danger_keyword:最大速度" in mem.pinned_safety
    # summary 已生成
    assert mem.summary and "summary" in mem.summary


def test_no_compression_below_trigger():
    mem = WorkingMemory()
    cfg = MemoryConfig(compress_trigger_tokens=100_000)
    mem.add_turn("user", "短输入")
    result = mem.maybe_compress(cfg)
    assert result["compressed"] is False


def test_hard_trim_keeps_safety_and_at_least_one_turn():
    mem = WorkingMemory()
    # 触发硬裁剪：trigger 极高，hard_trim 极低
    cfg = MemoryConfig(compress_trigger_tokens=10_000_000, hard_trim_tokens=5, keep_last_turns=2)
    for i in range(10):
        mem.add_turn("user", f"第 {i} 条 较 长 的 用 户 输 入 内 容 填 充 token")
    mem.add_safety_fact("emergency_stop_active")
    mem.maybe_compress(cfg)
    assert len(mem.turns) >= 1  # 至少保留 1 轮
    assert "emergency_stop_active" in mem.pinned_safety  # safety 不裁


def test_memory_config_from_dict():
    cfg = MemoryConfig.from_dict(
        {"max_memory_tokens": 64000, "compress_trigger_tokens": 48000, "keep_last_turns": 10}
    )
    assert cfg.max_memory_tokens == 64000
    assert cfg.compress_trigger_tokens == 48000


def test_memory_config_from_dict_uses_defaults_for_missing():
    cfg = MemoryConfig.from_dict({})
    assert cfg.keep_last_turns == 10
    assert cfg.hard_trim_tokens == 80000


def test_context_for_llm_structure():
    mem = WorkingMemory()
    mem.add_turn("user", "E42 怎么排查")
    mem.add_safety_fact("tool_failed:search_knowledge")
    ctx = mem.context_for_llm()
    assert "summary" in ctx and "recent_turns" in ctx
    assert ctx["recent_turns"][0]["content"] == "E42 怎么排查"
    assert ctx["pinned_safety"] == ["tool_failed:search_knowledge"]


def test_context_for_llm_trims_to_budget_keeps_safety():
    # P1 回归：注入上下文按 max_memory_tokens 裁剪，pinned_safety 不裁
    mem = WorkingMemory()
    for i in range(20):
        mem.add_turn("user", f"第 {i} 条 较 长 的 用 户 输 入 内 容 用 于 填 充 token 计 数")
    mem.add_safety_fact("coordinate_out_of_range x=9999")
    cfg = MemoryConfig(max_memory_tokens=60)  # 极小预算
    ctx = mem.context_for_llm(cfg)
    assert ctx["pinned_safety"] == ["coordinate_out_of_range x=9999"]  # safety 不裁
    assert len(ctx["recent_turns"]) < 20  # recent_turns 被裁剪
    assert len(ctx["recent_turns"]) >= 1


def test_summary_trimmed_keeps_safety_facts():
    # 压缩后 summary 超 summary_max_tokens 时裁普通叙述，active_risks 保留
    mem = WorkingMemory()
    mem.add_safety_fact("danger:最大速度")
    cfg = MemoryConfig(compress_trigger_tokens=10, keep_last_turns=1, summary_max_tokens=20)
    for i in range(12):
        mem.add_turn("user", f"很长的历史对话内容第 {i} 条用于累积 token 计数 " * 4)
    mem.maybe_compress(cfg)
    assert "danger:最大速度" in mem.summary.get("active_risks", [])  # 安全事实保留
