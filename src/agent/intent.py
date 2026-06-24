"""意图识别（本地规则链路）。

五分类：qa / status_check / device_action / unsafe_action / unknown。

规则顺序体现"多意图取最高风险，但能力询问优先于动作词"：

1. 空输入或无法归类 → unknown
2. 危险词或 risk_hints 非空 → unsafe_action
3. 能力/状态询问（"可以执行…吗""能否""是否在线"） → status_check
4. 动作命令（移动/抓取/复位…，疑问词之外的祈使句） → device_action
5. 知识问答 → qa
6. 其余 → unknown

LLM 候选由 llm.py 提供；本模块负责本地兜底，并以本地规则为准覆盖 LLM 的乐观判断。
"""

from __future__ import annotations

import re
from typing import Optional

from .knowledge import normalize
from .models import Intent

# 知识问答表达
QA_PATTERNS = [
    "是什么", "什么是", "说明", "规则", "怎么排查", "如何排查", "排查",
    "原理", "介绍", "报错", "故障", "怎么办",
    "怎么样", "如何", "哪些", "是啥", "啥样",  # 通用口语问法
]

# 能力/状态询问（优先于动作词，解决"可以执行抓取吗"应判 status_check）
STATUS_PATTERNS = [
    "可以执行", "能否执行", "能不能", "可以吗", "现在可以", "是否能",
    "是否在线", "在线吗", "可用吗", "状态", "当前状态", "是否可用",
]

# 动作命令（祈使句）
ACTION_PATTERNS = [
    "移动", "抓取", "夹取", "复位", "启动", "停止", "取件", "放置",
    "move", "pick", "place", "执行动作", "运行",
]

# 危险表达
DANGER_PATTERNS = [
    "最大速度", "绕过安全", "强制执行", "禁用保护", "急停解除",
    "超限", "直接执行", "最快", "全速", "解除保护",
]


def _any_in(text: str, patterns: list[str]) -> bool:
    return any(p in text for p in patterns)


def classify(raw_text: str, *, risk_hints: Optional[list[str]] = None) -> Intent:
    """根据文本与可选 risk_hints 分类意图。"""

    text = normalize(raw_text)
    if not text:
        return Intent.UNKNOWN
    # 危险优先：本地风险提示或危险词
    if risk_hints or _any_in(text, DANGER_PATTERNS):
        return Intent.UNSAFE_ACTION
    # 能力/状态询问优先于动作词
    if _any_in(text, STATUS_PATTERNS):
        return Intent.STATUS_CHECK
    if _any_in(text, ACTION_PATTERNS):
        return Intent.DEVICE_ACTION
    if _any_in(text, QA_PATTERNS):
        return Intent.QA
    return Intent.UNKNOWN


def extract_action(raw_text: str) -> Optional[str]:
    """从文本中抽取动作词，供参数解析与安全判断使用。"""

    text = normalize(raw_text)
    for action in ACTION_PATTERNS:
        if action in text:
            return action
    return None


# 设备相关信号词：区分"模糊设备输入"与"纯闲聊/问候"。
DEVICE_SIGNALS = [
    "机械臂", "机械手", "robot", "设备", "夹爪", "坐标", "速度", "力度",
    "抓取", "夹取", "移动", "复位", "启动", "停止", "急停", "安全",
    "故障", "报错", "排查", "维护", "气压", "传感器", "关节", "online", "offline",
]


def has_device_signal(raw_text: str) -> bool:
    """是否含设备相关信号；unknown 且无信号时视为自由对话（闲聊分流）。"""

    text = normalize(raw_text)
    if any(s in text for s in DEVICE_SIGNALS):
        return True
    if re.search(r"e\d+", text):  # 错误码
        return True
    if re.search(r"[xyz]\s*=", text):  # 坐标赋值
        return True
    return False
