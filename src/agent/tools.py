"""Mock 工具：search_knowledge / get_device_status / execute_device_command。

工具白名单在 ``AVAILABLE_TOOLS`` 中定义，供 LLM 三层注入使用。LLM 只有工具
调用建议权，是否真正执行由 runner、safety 和 approval 决定。

``execute_device_command`` 永远默认 dry-run，初版不提供真实执行入口。危险/越界
场景由上层控制，工具层只做形状校验和模拟返回。
"""

from __future__ import annotations

import os
from typing import Any, Optional


class ToolError(Exception):
    """工具内部异常（可被 runner 捕获并记录为 failed）。"""


class ToolTimeout(Exception):
    """工具调用超时（可被 runner 捕获并记录为 failed）。"""


# 工具白名单与说明，注入给 LLM 的 available_tools。
AVAILABLE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_knowledge",
        "description": "Search local equipment knowledge base for evidence.",
        "input_schema": {"query": "string"},
    },
    {
        "name": "get_device_status",
        "description": "Get mocked device status (online/offline/error/maintenance).",
        "input_schema": {},
    },
    {
        "name": "execute_device_command",
        "description": "Dry-run a validated device command; real execution is disabled.",
        "input_schema": {"command": "object", "dry_run": "boolean"},
    },
]

TOOL_WHITELIST: frozenset[str] = frozenset(t["name"] for t in AVAILABLE_TOOLS)


def search_knowledge(query: str, kb, *, simulate: Optional[str] = None) -> dict[str, Any]:
    """检索知识库；找不到返回空 matches，绝不编造。"""

    if simulate == "timeout":
        raise ToolTimeout("search_knowledge timed out")
    if simulate == "error":
        raise ToolError("search_knowledge internal error")
    matches = kb.search(query)
    return {
        "count": len(matches),
        "matches": [
            {
                "source": m.source,
                "title": m.title,
                "text": m.text,
                "context_before": m.context_before,
                "context_after": m.context_after,
                "score": m.score,
                "category": m.category,
            }
            for m in matches
        ],
    }


def get_device_status(*, override: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """返回 mock 设备状态；默认在线、空闲、未急停。

    支持通过 ``override`` 或环境变量模拟 offline/error/maintenance/急停，
    用于测试和故障演练。
    """

    override = override or {}
    status = override.get("status") or os.getenv("DEVICE_STATUS", "online")
    mode = override.get("mode") or os.getenv("DEVICE_MODE", "idle")
    emer_raw = override.get("emergency_stop")
    if emer_raw is None:
        emer_raw = os.getenv("DEVICE_EMERGENCY_STOP", "").lower() in ("1", "true", "yes")
    last_error = override.get("last_error")
    if last_error is None:
        last_error = os.getenv("DEVICE_LAST_ERROR") or None
    return {
        "status": status,
        "mode": mode,
        "emergency_stop": bool(emer_raw),
        "last_error": last_error,
    }


def execute_device_command(
    command: Any,
    *,
    dry_run: bool = True,
    simulate: Optional[str] = None,
) -> dict[str, Any]:
    """dry-run 校验命令；真实执行在初版被拒绝。"""

    if simulate == "timeout":
        raise ToolTimeout("execute_device_command timed out")
    if simulate == "error":
        raise ToolError("execute_device_command internal error")
    if not isinstance(command, dict):
        raise ToolError("command must be a JSON object")
    if not dry_run:
        # 初版安全边界：不提供真实执行入口。
        return {
            "dry_run": False,
            "accepted": False,
            "message": "Real execution is disabled in this version; only dry-run is allowed.",
        }
    return {
        "dry_run": True,
        "accepted": True,
        "message": "Command validated but not executed (dry-run).",
        "command": command,
    }
