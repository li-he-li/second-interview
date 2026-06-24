"""trace 与日志。

区分两类产物（见 plan 第 17 节）：

- ``logs/agent.log``：运行日志，运行时写入，进入 .gitignore。
- ``runs/<trace_id>.json``：单次调用 trace，运行证据，可挑选作为提交样例。
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Optional

RUNS_DIR = Path("runs")
LOGS_DIR = Path("logs")


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """配置 ``agent`` logger：同时写控制台和 ``logs/agent.log``。"""

    LOGS_DIR.mkdir(exist_ok=True)
    logger = logging.getLogger("agent")
    if logger.handlers:  # 避免重复挂载
        return logger
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    file_handler = logging.FileHandler(LOGS_DIR / "agent.log", encoding="utf-8")
    stream_handler = logging.StreamHandler()
    for handler in (file_handler, stream_handler):
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    return logger


def new_trace_id() -> str:
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"trace-{ts}-{short}"


class TraceRecorder:
    """收集单次运行的 trace 字段，最终落盘到 ``runs/<trace_id>.json``。"""

    def __init__(self, trace_id: str | None = None) -> None:
        self.trace_id = trace_id or new_trace_id()
        self.data: dict[str, Any] = {
            "trace_id": self.trace_id,
            "timestamp": _dt.datetime.now().isoformat(timespec="seconds"),
            "warnings": [],
        }

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def append(self, key: str, value: Any) -> None:
        self.data.setdefault(key, []).append(value)

    def add_warnings(self, messages: list[str]) -> None:
        for msg in messages:
            if msg:
                self.data["warnings"].append(msg)

    def save(self, runs_dir: Optional[Path] = None) -> Path:
        if runs_dir is None:
            runs_dir = RUNS_DIR  # 运行时读模块全局（支持测试 monkeypatch 隔离）
        runs_dir.mkdir(exist_ok=True)
        path = runs_dir / f"{self.trace_id}.json"
        with path.open("w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=2, default=str)
        return path
