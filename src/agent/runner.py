"""Agent 主流程：LLM 驱动的 agent loop。

架构（用户=纯执行器视角）：

    用户输入
      ↓
    ┌─ loop（最多 max_rounds 轮）────────────────────────────┐
    │  1. 组装上下文（含上轮 tool_results）→ LLM              │
    │  2. LLM 输出 {answer, tool_calls, final}               │
    │  3. 通知 UI（answer）                                   │
    │  4. 若 final 或无 tool_calls → 取最终 answer，跳出      │
    │  5. 解析 tool_calls → 白名单/dry-run/审批门控 → 执行    │
    │  6. 工具结果回传 tool_results，继续 loop                │
    └─────────────────────────────────────────────────────────┘
      ↓
    本地推断 intent/safety_level（不信任 LLM）→ 组装 AgentResponse → trace

LLM 决定"调什么工具"；框架保留：工具白名单、execute_device_command 永远 dry-run、
危险动作用户审批、sources 真实性过滤、safety_level 本地定级。
ESC 打断 / 任何异常都输出合法 JSON，不崩溃。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .approval import ApprovalGate, ApprovalRequest, approval_key
from .config import AppConfig, load_app_config
from .interrupt import CancellationToken, CancelledError
from .intent import classify, has_device_signal
from .knowledge import KnowledgeBase, normalize
from .llm import enhance_prompt, make_llm, validate_loop_output
from .memory import MemoryConfig, WorkingMemory
from .models import AgentResponse, ErrorInfo, Intent, SafetyLevel, ToolCall, ToolCallStatus
from .safety import evaluate
from .tools import (
    TOOL_WHITELIST,
    ToolError,
    ToolTimeout,
    execute_device_command,
    get_device_status,
    search_knowledge,
)
from .trace import TraceRecorder, setup_logging

# 事件回调类型：(event_type: str, payload: Any) -> None
AgentEvent = Callable[[str, Any], None]

DEFAULT_MAX_ROUNDS = 5


def _intent_from_tools(intent_text: Intent, tool_names: list[str]) -> Intent:
    """根据实际调用的工具校正意图（结构化字段用）。"""

    if tool_names:
        if "execute_device_command" in tool_names:
            return Intent.DEVICE_ACTION
        if "get_device_status" in tool_names:
            return Intent.STATUS_CHECK
        if "search_knowledge" in tool_names and intent_text != Intent.UNSAFE_ACTION:
            return Intent.QA if intent_text == Intent.UNKNOWN else intent_text
    return intent_text


class Agent:
    def __init__(
        self,
        *,
        llm_mode: str = "mock",
        app_config: Optional[AppConfig] = None,
        kb: Optional[KnowledgeBase] = None,
        llm: Optional[object] = None,
        approval_gate: Optional[ApprovalGate] = None,
        memory: Optional[WorkingMemory] = None,
        simulate: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        tool_simulate: Optional[str] = None,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
    ) -> None:
        self.llm_mode = llm_mode
        self.cfg = app_config or load_app_config()
        self.kb = kb or KnowledgeBase.load()
        self.memory = memory or WorkingMemory()
        self.approval = approval_gate or ApprovalGate()
        llm_cfg = dict(self.cfg.llm)
        if model:
            llm_cfg["model"] = model
        self.llm = llm or make_llm(llm_mode, api_key=api_key, llm_cfg=llm_cfg, simulate=simulate)
        self.mem_cfg = MemoryConfig.from_dict(self.cfg.memory)
        self.tool_simulate = tool_simulate
        self.max_rounds = max_rounds
        self.logger = setup_logging()

    def handle(
        self,
        raw_input: str,
        *,
        cancel_token: Optional[CancellationToken] = None,
        responder=None,
        on_event: Optional[AgentEvent] = None,
    ) -> AgentResponse:
        trace = TraceRecorder()
        trace.set("raw_input", raw_input)
        trace.set("llm_mode", self.llm_mode)
        trace.add_warnings(self.cfg.warnings)
        trace.add_warnings(self.kb.warnings)
        token = cancel_token or CancellationToken()

        try:
            token.check()
            if not raw_input or not raw_input.strip():
                return self._fallback(trace, Intent.UNKNOWN, SafetyLevel.L2, "输入为空，无法判断意图。", "invalid_input:empty")

            tool_results: list[dict] = []
            executed_calls: list[ToolCall] = []
            final_answer = ""
            final_sources: list[str] = []
            llm_error: Optional[str] = None
            last_risk_hints: list[str] = []

            for _round in range(self.max_rounds):
                token.check()
                ctx = self._safe_enhance(raw_input, tool_results, trace)
                last_risk_hints = ctx.get("risk_hints") or []
                llm_result = self.llm.generate(ctx, cancel_token=token)
                trace.append("llm_rounds", {"raw": (llm_result.raw_output or "")[:300], "error": llm_result.error})
                if llm_result.error:
                    llm_error = llm_result.error
                    break  # LLM 故障：用已有结果收尾或 fallback

                parsed, _ = validate_loop_output(llm_result.parsed)
                if parsed is None:
                    llm_error = "model_invalid_json"
                    break
                if parsed.get("sources"):
                    final_sources = parsed["sources"]

                answer = parsed["answer"]
                if answer and on_event:
                    on_event("answer", answer)

                tool_calls = parsed["tool_calls"]
                if parsed["final"] or not tool_calls:
                    final_answer = answer
                    break

                # 执行 LLM 要求的工具（经门控）
                for tc in tool_calls:
                    if on_event:
                        on_event("tool_call", tc)
                    call = self._exec_tool(tc, responder, token, trace)
                    executed_calls.append(call)
                    tool_results.append(
                        {"tool": call.tool, "input": call.input, "output": call.output, "status": call.status.value}
                    )
                    if on_event:
                        on_event("tool_result", call)
            else:
                final_answer = "已达最大推理轮次，请细化你的问题。"

            if not final_answer:
                final_answer = "未能给出明确结论，请人工复核。"

            token.check()
            # 本地推断 intent / safety_level（结构化字段，不信任 LLM）
            tool_names = [c.tool for c in executed_calls]
            base_intent = classify(raw_input, risk_hints=last_risk_hints)
            intent = _intent_from_tools(base_intent, tool_names)
            assessment = evaluate(raw_input, intent, safety_cfg=self.cfg.safety)
            # 闲聊修正：未调任何工具 + 无设备信号 + 无风险 → 自由对话 L0
            if not executed_calls and not has_device_signal(raw_input) and not last_risk_hints and not llm_error:
                assessment.level = SafetyLevel.L0
                assessment.need_human_approval = False
                assessment.reasons.append("chat")
            # 工具失败 → 升 L2（风险信号，安全门控）
            if any(c.status == ToolCallStatus.FAILED for c in executed_calls) and assessment.level != SafetyLevel.L2:
                assessment.level = SafetyLevel.L2
                assessment.need_human_approval = True
                assessment.reasons.append("tool_failed")
            # 设备查询检索为空 → 升 L2（plan：无资料不臆测，需人工确认）
            search_empty = any(
                c.tool == "search_knowledge"
                and c.status == ToolCallStatus.SUCCESS
                and (c.output.get("count", 0) == 0)
                for c in executed_calls
            )
            if has_device_signal(raw_input) and search_empty and assessment.level != SafetyLevel.L2:
                assessment.level = SafetyLevel.L2
                assessment.need_human_approval = True
                assessment.reasons.append("knowledge_not_found")
            for hint in assessment.risk_hints:
                self.memory.add_safety_fact(f"{intent.value}:{hint}")

            sources = self.kb.filter_sources(final_sources)
            response = self._build_response(
                final_answer, intent, assessment, executed_calls, sources, llm_error
            )

            self.memory.add_turn("user", raw_input, trace.trace_id)
            self.memory.add_turn("assistant", response.answer, trace.trace_id)
            trace.set("memory_compress", self.memory.maybe_compress(self.mem_cfg))
            trace.set("final_json", response.model_dump(mode="json"))
            trace.save()
            self.logger.info("trace %s intent=%s level=%s rounds=%d", trace.trace_id, intent.value, assessment.level.value, len(tool_results))
            return response

        except CancelledError:
            return self._cancelled_response(trace)
        except Exception as exc:
            self.logger.exception("agent handle error")
            return self._fallback(trace, Intent.UNKNOWN, SafetyLevel.L2, f"内部错误：{exc}", f"internal_error:{type(exc).__name__}")

    # ---- 辅助 ----

    def _safe_enhance(self, raw_input: str, tool_results: list[dict], trace) -> dict:
        try:
            return enhance_prompt(
                raw_input,
                tool_results=tool_results,
                memory_ctx=self.memory.context_for_llm(self.mem_cfg),
                safety_cfg=self.cfg.safety,
            )
        except Exception as exc:
            trace.append("warnings", f"prompt_enhancement_error:{type(exc).__name__}")
            return {"raw_user_input": raw_input, "normalized_query": normalize(raw_input), "tool_results": tool_results, "risk_hints": []}

    def _exec_tool(self, tc: dict, responder, token, trace) -> ToolCall:
        name = tc.get("tool", "")
        inp = tc.get("input") or {}
        token.check()
        if name not in TOOL_WHITELIST:
            return ToolCall(tool=name, input=inp, output={"error": "not_in_whitelist"}, status=ToolCallStatus.SKIPPED)
        # 设备命令：强制 dry-run + 用户审批（安全门控，不可让渡）
        if name == "execute_device_command":
            approved = self._ask_approval(SafetyLevel.L2, name, inp, responder, trace)
            if not approved:
                return ToolCall(tool=name, input=inp, output={"rejected": True}, status=ToolCallStatus.SKIPPED)
            out = execute_device_command(inp.get("command", {}), dry_run=True)  # 永远 dry-run
            return ToolCall(tool=name, input=inp, output=out, status=ToolCallStatus.SUCCESS)
        try:
            if name == "search_knowledge":
                out = search_knowledge(inp.get("query", ""), self.kb, simulate=self.tool_simulate)
            elif name == "get_device_status":
                out = get_device_status()
            else:
                out = {}
            return ToolCall(tool=name, input=inp, output=out, status=ToolCallStatus.SUCCESS)
        except ToolTimeout:
            return ToolCall(tool=name, input=inp, output={"error": "timeout"}, status=ToolCallStatus.FAILED)
        except ToolError as exc:
            return ToolCall(tool=name, input=inp, output={"error": str(exc)}, status=ToolCallStatus.FAILED)

    def _ask_approval(self, level: SafetyLevel, tool_name: str, inp: dict, responder, trace) -> bool:
        cmd = normalize(inp.get("command")) if isinstance(inp.get("command"), str) else json_dumps(inp.get("command"))
        req = ApprovalRequest(level, tool_name, cmd, "device_action_dry_run")
        result = self.approval.request(req, responder=responder)
        trace.append("approvals", {"tool": tool_name, "decision": result.decision, "approved": result.approved})
        return result.approved

    def _build_response(self, answer, intent, assessment, tool_calls, sources, llm_error) -> AgentResponse:
        if llm_error:
            final = "model_error_fallback"
        elif any(tc.status == ToolCallStatus.SUCCESS and tc.tool == "execute_device_command" for tc in tool_calls):
            final = "dry_run_executed"
        elif any(tc.status == ToolCallStatus.SKIPPED and tc.tool == "execute_device_command" for tc in tool_calls):
            final = "action_skipped_by_approval"
        elif assessment.level == SafetyLevel.L2:
            final = "blocked_or_review_only"
        else:
            final = "answered"
        error = ErrorInfo(type=llm_error, message=f"LLM issue: {llm_error}") if llm_error else None
        return AgentResponse(
            answer=answer,
            intent=intent,
            sources=sources,
            confidence=self._confidence(tool_calls, assessment.level, llm_error),
            safety_level=assessment.level,
            need_human_approval=assessment.need_human_approval,
            tool_calls=tool_calls,
            final_action=final,
            error=error,
        )

    def _confidence(self, tool_calls, level, llm_error) -> float:
        if llm_error:
            return 0.3
        has_result = any(tc.status == ToolCallStatus.SUCCESS for tc in tool_calls)
        if level == SafetyLevel.L2:
            return 0.35 if has_result else 0.25
        if not has_result:
            return 0.5
        if level == SafetyLevel.L1:
            return 0.7
        return 0.85

    def _cancelled_response(self, trace) -> AgentResponse:
        trace.set("interrupted", True)
        resp = AgentResponse(
            answer="运行已被用户打断（ESC）。",
            intent=Intent.UNKNOWN, sources=[], confidence=0.0,
            safety_level=SafetyLevel.L2, need_human_approval=True,
            tool_calls=[], final_action="cancelled_by_user",
            error=ErrorInfo(type="cancelled", message="User interrupted the run with ESC."),
        )
        trace.set("final_json", resp.model_dump(mode="json"))
        trace.save()
        return resp

    def _fallback(self, trace, intent, level, answer, error_type) -> AgentResponse:
        etype = error_type.split(":")[0] if error_type else None
        resp = AgentResponse(
            answer=answer, intent=intent, sources=[], confidence=0.0,
            safety_level=level, need_human_approval=(level == SafetyLevel.L2),
            tool_calls=[], final_action="fallback",
            error=ErrorInfo(type=etype, message=error_type) if error_type else None,
        )
        trace.set("final_json", resp.model_dump(mode="json"))
        trace.save()
        return resp


def json_dumps(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)
