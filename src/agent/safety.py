"""安全策略：参数边界、危险词、设备状态拦截与 L0/L1/L2 定级。

定级原则（plan 第 11 节）：

- L0：只读问答或状态查询。
- L1：低风险动作，只允许 dry-run。
- L2：危险动作、越界参数、危险词、工具异常、检索失败、设备拦截、不确定输入；
      必须 need_human_approval=True，即使审批通过也不允许真实执行危险动作。

配置缺失时使用保守默认值（坐标 ±1000、拒绝最大速度、力度上限 50N、全部需审批）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .knowledge import normalize
from .models import Intent, SafetyLevel

DEFAULT_COORD_LIMITS = {"x": [-1000, 1000], "y": [-1000, 1000], "z": [-1000, 1000]}
DEFAULT_ALLOWED_SPEEDS = ["low", "normal", "safe"]
DEFAULT_MAX_FORCE = 50
DEFAULT_MAX_DISTANCE = 1000  # 单次相对移动距离上限（保守数值阈值；LLM 可能用 cm/mm/m，取保守）
DEFAULT_DANGER_KEYWORDS = ["最大速度", "强制执行", "绕过安全", "禁用保护", "急停解除", "超限", "直接执行"]


@dataclass
class SafetyAssessment:
    level: SafetyLevel
    reasons: list[str] = field(default_factory=list)
    risk_hints: list[str] = field(default_factory=list)
    need_human_approval: bool = False
    params: dict[str, Any] = field(default_factory=dict)
    danger_words: list[str] = field(default_factory=list)


def parse_params(raw_text: str) -> dict[str, Any]:
    """从文本抽取坐标 / 速度 / 力度。"""

    text = normalize(raw_text)
    coords: dict[str, int] = {}
    for axis in ("x", "y", "z"):
        m = re.search(rf"\b{axis}\s*=\s*(-?\d+)", text)
        if m:
            coords[axis] = int(m.group(1))
    speed: Optional[str] = None
    if any(w in text for w in ("最大速度", "全速", "最快")):
        speed = "max"
    else:
        m = re.search(r"速度\s*=\s*(\w+)", text)
        if m:
            speed = m.group(1)
    force: Optional[int] = None
    m = re.search(r"(?:力度|力|force|newton)\s*=\s*(\d+)", text)
    if m:
        force = int(m.group(1))
    distance: Optional[int] = None
    m = re.search(r"(?:distance|距离|移动)\s*[=:]?\s*(\d+)\s*(cm|mm|m|厘米|毫米|米|km|公里)?", text)
    if m:
        val = int(m.group(1))
        unit = (m.group(2) or "").lower()
        if unit in ("cm", "厘米"):
            val *= 10
        elif unit in ("m", "米"):
            val *= 1000
        elif unit in ("km", "公里"):
            val *= 1000000
        distance = val  # 归一化到 mm
    return {"coordinates": coords, "speed": speed, "force": force, "distance": distance}


def detect_danger_words(raw_text: str, safety_cfg: dict) -> list[str]:
    text = normalize(raw_text)
    keywords = safety_cfg.get("danger_keywords") or DEFAULT_DANGER_KEYWORDS
    return [w for w in keywords if w in text]


def check_param_risks(params: dict[str, Any], safety_cfg: dict) -> tuple[list[str], list[str]]:
    """返回 (risk_hints, reasons)。"""

    hints: list[str] = []
    reasons: list[str] = []
    limits = safety_cfg.get("coordinate_limits") or DEFAULT_COORD_LIMITS
    for axis, val in params["coordinates"].items():
        lo, hi = limits.get(axis, [-1000, 1000])
        if val < lo or val > hi:
            hints.append("coordinate_out_of_range")
            reasons.append(f"{axis}={val} out of [{lo}, {hi}]")
    if params["speed"] is not None:
        allowed = safety_cfg.get("allowed_speeds") or DEFAULT_ALLOWED_SPEEDS
        if params["speed"] not in allowed:
            hints.append("invalid_speed")
            reasons.append(f"speed={params['speed']} not allowed (allowed={allowed})")
    if params["force"] is not None:
        max_force = safety_cfg.get("max_force_newton", DEFAULT_MAX_FORCE)
        if params["force"] > max_force:
            hints.append("force_exceeds_limit")
            reasons.append(f"force={params['force']} > max {max_force}")
    if params.get("distance") is not None:
        max_distance = safety_cfg.get("max_distance", DEFAULT_MAX_DISTANCE)
        if params["distance"] > max_distance:
            hints.append("distance_out_of_range")
            reasons.append(f"distance={params['distance']} > max {max_distance}")
    return hints, reasons


def _device_blocks(device_status: Optional[dict], safety_cfg: dict) -> Optional[str]:
    """设备状态是否拦截动作；返回拦截原因或 None。"""

    if not device_status:
        return None
    status = device_status.get("status")
    if device_status.get("emergency_stop") and safety_cfg.get("block_when_emergency_stop", True):
        return "emergency_stop_active"
    if status == "offline" and safety_cfg.get("block_when_offline", True):
        return "device_offline"
    if status == "maintenance" and safety_cfg.get("block_when_maintenance", True):
        return "device_maintenance"
    if status == "error":
        return "device_error"
    return None


def _need_approval(level: SafetyLevel, safety_cfg: dict) -> bool:
    approval = safety_cfg.get("approval") or {}
    if level == SafetyLevel.L2:
        # L2 一律人工审批，不可被配置绕过（治理硬约束，见 plan 第 11 节）
        return True
    if level == SafetyLevel.L1:
        return bool(approval.get("l1_requires_approval", True))
    return False


def evaluate(
    raw_text: str,
    intent: Intent,
    *,
    device_status: Optional[dict] = None,
    safety_cfg: Optional[dict] = None,
) -> SafetyAssessment:
    """综合参数、危险词、设备状态与意图，输出安全等级评估。"""

    cfg = safety_cfg or {}
    params = parse_params(raw_text)
    hints, reasons = check_param_risks(params, cfg)
    danger_words = detect_danger_words(raw_text, cfg)
    if danger_words:
        hints.append("danger_keyword")
        reasons.append(f"danger_words={danger_words}")

    level: SafetyLevel
    if intent == Intent.UNSAFE_ACTION:
        level = SafetyLevel.L2
        reasons.append("intent=unsafe_action")
    elif hints:
        level = SafetyLevel.L2
    else:
        block = _device_blocks(device_status, cfg)
        if intent == Intent.DEVICE_ACTION:
            if block:
                level = SafetyLevel.L2
                reasons.append(f"device_status_blocks_action:{block}")
            else:
                level = SafetyLevel.L1
                reasons.append("low_risk_device_action")
        elif intent in (Intent.QA, Intent.STATUS_CHECK):
            level = SafetyLevel.L0
            reasons.append("read_only")
        else:  # unknown 或其他不确定
            level = SafetyLevel.L2
            reasons.append("uncertain_input")

    return SafetyAssessment(
        level=level,
        reasons=reasons,
        risk_hints=hints,
        need_human_approval=_need_approval(level, cfg),
        params=params,
        danger_words=danger_words,
    )
