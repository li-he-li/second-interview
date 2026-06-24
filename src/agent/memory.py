"""短期工作记忆：结构化记忆 + 最近 N 轮原文 + 历史摘要。

策略（plan 第 13 节）：

- token 计数用 tiktoken（cl100k_base），不可用时降级为字符估算。
- 超过 ``compress_trigger_tokens``（默认 48000）触发压缩：保留最近 ``keep_last_turns``
  轮原文，更早的压缩进结构化 summary。
- 压缩后 summary 按 ``summary_max_tokens`` 裁剪普通叙述，安全事实不裁。
- 超过 ``hard_trim_tokens``（默认 80000）执行硬裁剪，只裁最旧普通对话，**不裁 pinned safety**。
- ``context_for_llm`` 注入时按 ``max_memory_tokens`` 预算裁剪 recent_turns，pinned safety 永不裁。
- mock 模式使用确定性摘要模板；记忆只在当前 CLI 会话内有效，不作为知识库来源。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return len(_ENC.encode(text))
except Exception:  # tiktoken 不可用时降级估算
    def count_tokens(text: str) -> int:  # type: ignore[misc]
        return max(1, len(text) // 3)


@dataclass
class MemoryConfig:
    max_memory_tokens: int = 64000
    compress_trigger_tokens: int = 48000
    keep_last_turns: int = 10
    summary_max_tokens: int = 4000
    hard_trim_tokens: int = 80000

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryConfig":
        return cls(
            max_memory_tokens=int(d.get("max_memory_tokens", 64000)),
            compress_trigger_tokens=int(d.get("compress_trigger_tokens", 48000)),
            keep_last_turns=int(d.get("keep_last_turns", 10)),
            summary_max_tokens=int(d.get("summary_max_tokens", 4000)),
            hard_trim_tokens=int(d.get("hard_trim_tokens", 80000)),
        )


@dataclass
class Turn:
    role: str  # user / assistant
    content: str
    trace_id: str = ""


@dataclass
class WorkingMemory:
    turns: list[Turn] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    pinned_safety: list[str] = field(default_factory=list)
    device_state: dict[str, Any] = field(default_factory=dict)

    def add_turn(self, role: str, content: str, trace_id: str = "") -> None:
        self.turns.append(Turn(role=role, content=content, trace_id=trace_id))

    def add_safety_fact(self, fact: str) -> None:
        """pinned 安全事实，压缩/裁剪均不丢弃。"""

        if fact and fact not in self.pinned_safety:
            self.pinned_safety.append(fact)

    def update_device_state(self, state: dict[str, Any]) -> None:
        self.device_state.update(state or {})

    def token_count(self) -> int:
        total = sum(count_tokens(t.content) for t in self.turns)
        total += count_tokens(json.dumps(self.summary, ensure_ascii=False))
        total += sum(count_tokens(f) for f in self.pinned_safety)
        return total

    def maybe_compress(self, cfg: MemoryConfig) -> dict[str, Any]:
        """按阈值压缩/硬裁剪，返回前后 token 数与是否压缩。"""

        before = self.token_count()
        compressed = False
        if before >= cfg.compress_trigger_tokens:
            self._compress(cfg)
            self._trim_summary(cfg)
            compressed = True
        after_compress = self.token_count()
        trimmed = False
        if after_compress >= cfg.hard_trim_tokens:
            self._hard_trim(cfg)
            trimmed = True
        after = self.token_count()
        return {
            "compressed": compressed,
            "hard_trimmed": trimmed,
            "tokens_before": before,
            "tokens_after": after,
        }

    def _compress(self, cfg: MemoryConfig) -> None:
        if len(self.turns) <= cfg.keep_last_turns:
            return
        old_turns = self.turns[: -cfg.keep_last_turns]
        self.turns = self.turns[-cfg.keep_last_turns:]
        self.summary = self._mock_summary(old_turns)

    def _mock_summary(self, old_turns: list[Turn]) -> dict[str, Any]:
        """确定性摘要模板，保留安全相关事实。"""

        return {
            "summary": f"已压缩 {len(old_turns)} 轮早期对话",
            "device_state": self.device_state,
            "active_risks": list(self.pinned_safety),
            "human_decisions": [],
            "tool_results": [],
            "open_questions": [],
            "knowledge_sources_used": [],
            "last_role": old_turns[-1].role if old_turns else "",
        }

    def _trim_summary(self, cfg: MemoryConfig) -> None:
        """summary 超 summary_max_tokens 时裁剪普通叙述，保留安全事实字段。

        可裁：summary 文本 / open_questions / tool_results / knowledge_sources_used。
        不裁：active_risks / device_state / human_decisions（安全与决策事实）。
        """

        list_keys = ("open_questions", "tool_results", "knowledge_sources_used")
        while count_tokens(json.dumps(self.summary, ensure_ascii=False)) > cfg.summary_max_tokens:
            trimmed_one = False
            for key in list_keys:
                if self.summary.get(key):
                    self.summary[key] = self.summary[key][:-1]
                    trimmed_one = True
                    break
            if trimmed_one:
                continue
            text = self.summary.get("summary", "")
            if len(text) > 8:
                self.summary["summary"] = text[: len(text) // 2]  # 截断普通叙述
            else:
                break  # 仅剩安全事实，停止

    def _hard_trim(self, cfg: MemoryConfig) -> None:
        """只裁最旧普通对话文本，pinned safety 不裁。"""

        while self.token_count() >= cfg.hard_trim_tokens and len(self.turns) > 1:
            self.turns.pop(0)

    def context_for_llm(self, cfg: Optional[MemoryConfig] = None) -> dict[str, Any]:
        """组装注入 LLM 的上下文；给定 cfg 时按 max_memory_tokens 裁剪 recent_turns。

        pinned_safety 永不裁剪；recent_turns 从最旧开始裁，至少保留 1 轮。
        """

        ctx = {
            "summary": self.summary,
            "recent_turns": [{"role": t.role, "content": t.content} for t in self.turns],
            "pinned_safety": list(self.pinned_safety),
            "device_state": self.device_state,
        }
        if cfg is None:
            return ctx
        while (
            count_tokens(json.dumps(ctx, ensure_ascii=False)) > cfg.max_memory_tokens
            and len(ctx["recent_turns"]) > 1
        ):
            ctx["recent_turns"].pop(0)  # 裁最旧普通对话，pinned_safety 保留
        return ctx
