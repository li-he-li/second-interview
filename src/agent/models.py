"""核心数据模型（Pydantic v2）。

定义题目要求的结构化输出 schema。最终返回给用户的 JSON 由 ``AgentResponse``
序列化生成，不直接打印 LLM 原文。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Intent(str, Enum):
    """用户意图五分类。"""

    QA = "qa"
    STATUS_CHECK = "status_check"
    DEVICE_ACTION = "device_action"
    UNSAFE_ACTION = "unsafe_action"
    UNKNOWN = "unknown"


class SafetyLevel(str, Enum):
    """安全等级：L0 只读 / L1 低风险 dry-run / L2 必须人工审批。"""

    L0 = "L0"
    L1 = "L1"
    L2 = "L2"


class ToolCallStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class ToolCall(BaseModel):
    tool: str
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    status: ToolCallStatus


class ErrorInfo(BaseModel):
    type: str
    message: str


class AgentResponse(BaseModel):
    """最终返回给用户的结构化响应，字段严格对齐题目要求。"""

    answer: str
    intent: Intent
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    safety_level: SafetyLevel
    need_human_approval: bool
    tool_calls: list[ToolCall] = Field(default_factory=list)
    final_action: str
    error: Optional[ErrorInfo] = None
