"""制造业设备安全操作 Agent。

CLI-only，默认 mock LLM，支持真实 DeepSeek LLM。涉及设备动作必须经过安全等级
判断与人工审批，危险/不确定场景统一升级为 L2 并 fallback，绝不编造结果。
"""

__version__ = "0.1.0"
