"""短期工作记忆：结构化记忆 + 最近 N 轮原文 + 历史摘要。

策略（plan 第 13 节）：

- token 计数用 tiktoken（cl100k_base），不可用时降级为字符估算。
- 超过 ``compress_trigger_tokens``（默认 48000）触发压缩：保留最近 ``keep_last_turns``
  轮原文，更早的压缩进结构化 summary。
- 超过 ``hard_trim_tokens``（默认 80000）执行硬裁剪，只裁最旧普通对话，**不裁 pinned safety**。
- mock 模式使用确定性摘要模板；安全事实（危险词、越界参数、急停、L2 决策、工具失败）pinned 不丢。
- 记忆只在当前 CLI 会话内有效，不作为知识库来源，不写入 sources。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

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

    def _hard_trim(self, cfg: MemoryConfig) -> None:
        """只裁最旧普通对话文本，pinned safety 不裁。"""

        while self.token_count() >= cfg.hard_trim_tokens and len(self.turns) > 1:
            self.turns.pop(0)

    def context_for_llm(self) -> dict[str, Any]:
        """组装注入 LLM 的工作记忆上下文（摘要 + 最近轮次 + 安全事实）。"""

        return {
            "summary": self.summary,
            "recent_turns": [{"role": t.role, "content": t.content} for t in self.turns],
            "pinned_safety": list(self.pinned_safety),
            "device_state": self.device_state,
        }
