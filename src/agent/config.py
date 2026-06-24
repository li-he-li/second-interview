"""配置加载。

从 ``config/*.json`` 读取安全规则、真实 LLM 配置和工作记忆配置。
任何文件缺失或格式非法时，一律回退到保守默认值，并在 warnings 中记录
``config_warning``，绝不因配置问题导致流程崩溃。
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# 保守默认值：边界严格、危险词齐全、所有动作都需审批。
DEFAULT_SAFETY: dict = {
    "coordinate_limits": {"x": [-1000, 1000], "y": [-1000, 1000], "z": [-1000, 1000]},
    "allowed_speeds": ["low", "normal", "safe"],
    "max_force_newton": 50,
    "block_when_emergency_stop": True,
    "block_when_offline": True,
    "block_when_maintenance": True,
    "danger_keywords": ["最大速度", "强制执行", "绕过安全", "禁用保护", "急停解除", "超限", "直接执行"],
    "approval": {
        "l1_requires_approval": True,
        "l2_requires_approval": True,
        "allyes_scope": "session_global_l2",
    },
}

DEFAULT_LLM: dict = {
    "provider": "deepseek",
    "model": "deepseek-v4-pro",
    "base_url": "https://api.deepseek.com",
    "timeout_seconds": 180,
    "max_retries": 3,
    "stream": True,
    "retry_once_on_invalid_json": True,
}

DEFAULT_MEMORY: dict = {
    "max_memory_tokens": 64000,
    "compress_trigger_tokens": 48000,
    "keep_last_turns": 10,
    "summary_max_tokens": 4000,
    "hard_trim_tokens": 80000,
    "token_counter": "tiktoken",
    "compression_mode": "mock_template_first",
    "llm_compression_enabled": True,
}


@dataclass
class AppConfig:
    safety: dict
    llm: dict
    memory: dict
    warnings: list[str] = field(default_factory=list)


def _config_dir() -> Path:
    """配置目录，受 ``CONFIG_DIR`` 环境变量控制，默认 ``config``。"""

    return Path(os.getenv("CONFIG_DIR", "config"))


def _load_json(name: str, default: dict, warnings: list[str]) -> dict:
    path = _config_dir() / name
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("config root must be a JSON object")
        return data
    except FileNotFoundError:
        warnings.append(f"config_warning: {name} missing, using conservative default")
        return copy.deepcopy(default)
    except (json.JSONDecodeError, ValueError) as exc:
        warnings.append(f"config_warning: {name} invalid ({exc}), using conservative default")
        return copy.deepcopy(default)


def load_app_config() -> AppConfig:
    """加载三份配置；任何一份异常都回退默认并收集 warning。"""

    warnings: list[str] = []
    safety = _load_json("safety_rules.json", DEFAULT_SAFETY, warnings)
    llm = _load_json("llm_config.json", DEFAULT_LLM, warnings)
    memory = _load_json("memory_config.json", DEFAULT_MEMORY, warnings)
    return AppConfig(safety=safety, llm=llm, memory=memory, warnings=warnings)
