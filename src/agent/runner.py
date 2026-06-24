"""Agent 主流程：串联输入 → 记忆 → 检索 → 意图 → 安全 → 审批 → 工具 → 校验 → 输出 → trace。

关键原则（plan 第 5 节）：

- 检索不到不编造；sources 只用 search_knowledge 的真实来源（过滤 LLM 编造）。
- 安全等级以 ``safety.evaluate`` 为准，不信任 LLM 草稿的 level。
- L1 动作审批通过才 dry-run；L2 即使审批通过也不真实执行危险动作。
- LLM 草稿用 ``validate_draft`` 校验，非法则规则兜底，最终由 AgentResponse 组装。
- ESC 打断 / 任何异常都输出合法 JSON，不崩溃。
"""

from __future__ import annotations

from typing import Optional

from .approval import ApprovalGate, ApprovalRequest, approval_key
from .config import AppConfig, load_app_config
from .interrupt import CancellationToken, CancelledError
from .intent import classify, has_device_signal
from .knowledge import KnowledgeBase, normalize
from .llm import enhance_prompt, make_llm, validate_draft
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

_INTENT_RISK = {Intent.UNKNOWN: 0, Intent.QA: 1, Intent.STATUS_CHECK: 1, Intent.DEVICE_ACTION: 2, Intent.UNSAFE_ACTION: 3}


def _merge_intent(local: Intent, llm_intent_str: Optional[str]) -> Intent:
    """本地与 LLM 意图取最高风险；本地优先覆盖 LLM 的乐观判断。"""

    candidates = [local]
    if llm_intent_str:
        try:
            candidates.append(Intent(llm_intent_str))
        except ValueError:
            pass
    return max(candidates, key=lambda i: _INTENT_RISK[i])


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
        self.logger = setup_logging()

    def handle(
        self,
        raw_input: str,
        *,
        cancel_token: Optional[CancellationToken] = None,
        responder=None,
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

            token.check()
            # 意图驱动：闲聊/问候（unknown 且无设备信号）→ LLM 自由对话，不检索/不工具/不审批
            if classify(raw_input) == Intent.UNKNOWN and not has_device_signal(raw_input):
                return self._chat(raw_input, trace, token)

            token.check()
            matches, retrieval_call = self._search_knowledge(raw_input, trace, token)
            trace.set("retrieval", [{"source": m.source, "score": m.score} for m in matches])

            token.check()
            enhanced = self._safe_enhance(raw_input, matches, trace)

            token.check()
            llm_result = self.llm.generate(enhanced, cancel_token=token)
            trace.set("llm_raw_output", (llm_result.raw_output or "")[:500])
            trace.set("llm_error", llm_result.error)

            token.check()
            local_intent = classify(raw_input, risk_hints=enhanced.get("risk_hints"))
            llm_intent = (llm_result.parsed or {}).get("intent") if llm_result.parsed else None
            intent = _merge_intent(local_intent, llm_intent)

            token.check()
            device_status = None
            if intent in (Intent.STATUS_CHECK, Intent.DEVICE_ACTION, Intent.UNSAFE_ACTION):
                device_status = get_device_status()
                self.memory.update_device_state(device_status)

            token.check()
            assessment = evaluate(raw_input, intent, device_status=device_status, safety_cfg=self.cfg.safety)
            if not matches and intent in (Intent.QA, Intent.UNKNOWN) and assessment.level != SafetyLevel.L2:
                assessment.level = SafetyLevel.L2
                assessment.need_human_approval = True
                assessment.reasons.append("knowledge_not_found")
                assessment.risk_hints.append("knowledge_not_found")
            if retrieval_call.status == ToolCallStatus.FAILED and assessment.level != SafetyLevel.L2:
                assessment.level = SafetyLevel.L2
                assessment.need_human_approval = True
                assessment.reasons.append("tool_failed:search_knowledge")
                assessment.risk_hints.append("tool_failed")
            for hint in assessment.risk_hints:
                self.memory.add_safety_fact(f"{intent.value}:{hint}")

            token.check()
            tool_calls = [retrieval_call]
            tool_calls.extend(self._run_tools(intent, assessment, raw_input, device_status, responder, trace, token))

            token.check()
            response = self._build_response(llm_result, intent, assessment, matches, tool_calls)

            self.memory.add_turn("user", raw_input, trace.trace_id)
            self.memory.add_turn("assistant", response.answer, trace.trace_id)
            trace.set("memory_compress", self.memory.maybe_compress(self.mem_cfg))
            trace.set("memory_tokens", self.memory.token_count())

            trace.set("final_json", response.model_dump(mode="json"))
            trace.save()
            self.logger.info("trace %s intent=%s level=%s", trace.trace_id, intent.value, assessment.level.value)
            return response

        except CancelledError:
            return self._cancelled_response(trace)
        except Exception as exc:  # 兜底：任何未预期异常都输出合法 JSON
            self.logger.exception("agent handle error")
            return self._fallback(trace, Intent.UNKNOWN, SafetyLevel.L2, f"内部错误：{exc}", f"internal_error:{type(exc).__name__}")

    # ---- 辅助 ----

    def _safe_enhance(self, raw_input, matches, trace) -> dict:
        try:
            return enhance_prompt(
                raw_input,
                knowledge_matches=matches,
                memory_ctx=self.memory.context_for_llm(self.mem_cfg),
                safety_cfg=self.cfg.safety,
                intent_candidates=[classify(raw_input).value],
            )
        except Exception as exc:
            trace.append("warnings", f"prompt_enhancement_error:{type(exc).__name__}")
            return {"raw_user_input": raw_input, "normalized_query": normalize(raw_input), "risk_hints": []}

    def _run_tools(self, intent, assessment, raw_input, device_status, responder, trace, token) -> list[ToolCall]:
        calls: list[ToolCall] = []
        if intent == Intent.STATUS_CHECK:
            calls.append(self._exec("get_device_status", {}, device_status or get_device_status(), token))

        if intent == Intent.DEVICE_ACTION and assessment.level == SafetyLevel.L1:
            approved = self._ask_approval(assessment, "execute_device_command", raw_input, responder, trace)
            if approved:
                cmd = {"action": "move", **assessment.params.get("coordinates", {})}
                if assessment.params.get("speed"):
                    cmd["speed"] = assessment.params["speed"]
                calls.append(self._exec("execute_device_command", {"command": cmd, "dry_run": True}, None, token, dry_run=True))
            else:
                calls.append(ToolCall(tool="execute_device_command", input={}, output={}, status=ToolCallStatus.SKIPPED))

        if assessment.level == SafetyLevel.L2:
            # L2 一律走审批，但审批通过也只允许诊断/解释，不真实执行
            self._ask_approval(assessment, "l2_review", raw_input, responder, trace)
        return calls

    def _search_knowledge(self, raw_input, trace, token):
        token.check()
        inp = {"query": raw_input}
        try:
            out = search_knowledge(raw_input, self.kb, simulate=self.tool_simulate)
            source_ids = [m["source"] for m in out.get("matches", [])]
            by_source = {m.source: m for m in self.kb.search(raw_input)}
            matches = [by_source[s] for s in source_ids if s in by_source]
            return matches, ToolCall(
                tool="search_knowledge",
                input=inp,
                output=out,
                status=ToolCallStatus.SUCCESS,
            )
        except ToolTimeout:
            trace.append("warnings", "tool_timeout:search_knowledge")
            self.memory.add_safety_fact("tool_failed:search_knowledge_timeout")
            return [], ToolCall(
                tool="search_knowledge",
                input=inp,
                output={"error": "timeout"},
                status=ToolCallStatus.FAILED,
            )
        except ToolError as exc:
            trace.append("warnings", f"tool_error:search_knowledge:{exc}")
            self.memory.add_safety_fact("tool_failed:search_knowledge_error")
            return [], ToolCall(
                tool="search_knowledge",
                input=inp,
                output={"error": str(exc)},
                status=ToolCallStatus.FAILED,
            )

    def _ask_approval(self, assessment, tool_name, raw_input, responder, trace) -> bool:
        req = ApprovalRequest(assessment.level, tool_name, normalize(raw_input), ";".join(assessment.reasons))
        result = self.approval.request(req, responder=responder)
        trace.set(
            "approval",
            {
                "key": approval_key(req.safety_level, req.tool_name, req.command, req.risk_reason),
                "decision": result.decision,
                "approved": result.approved,
            },
        )
        return result.approved

    def _exec(self, name, inp, precomputed, token, **kw) -> ToolCall:
        token.check()
        if name not in TOOL_WHITELIST:
            return ToolCall(tool=name, input=inp, output={}, status=ToolCallStatus.SKIPPED)
        try:
            if name == "get_device_status":
                out = precomputed if precomputed is not None else get_device_status()
            elif name == "execute_device_command":
                out = execute_device_command(inp.get("command", {}), dry_run=inp.get("dry_run", True))
            else:
                out = {}
            return ToolCall(tool=name, input=inp, output=out, status=ToolCallStatus.SUCCESS)
        except ToolTimeout:
            return ToolCall(tool=name, input=inp, output={"error": "timeout"}, status=ToolCallStatus.FAILED)
        except ToolError as exc:
            return ToolCall(tool=name, input=inp, output={"error": str(exc)}, status=ToolCallStatus.FAILED)

    def _build_response(self, llm_result, intent, assessment, matches, tool_calls) -> AgentResponse:
        draft = llm_result.parsed
        validated, _ = validate_draft(draft) if draft else (None, "no_draft")

        real_sources = [m.source for m in matches]
        llm_sources = (validated.sources if validated else []) or (draft.get("sources", []) if draft else [])
        sources = self.kb.filter_sources(list(dict.fromkeys(real_sources + list(llm_sources))))

        if validated and validated.answer:
            answer = validated.answer
        elif matches:
            answer = "；".join(m.text[:50] for m in matches[:2])
        elif intent == Intent.STATUS_CHECK:
            answer = "已查询设备状态，详见 tool_calls。"
        else:
            answer = "知识库无可靠资料，无法确认，请人工复核。"

        confidence = self._confidence(matches, assessment.level, llm_result.error)

        if intent == Intent.UNSAFE_ACTION or assessment.level == SafetyLevel.L2:
            final = "blocked_or_review_only_dry_run"
        elif any(tc.status == ToolCallStatus.SUCCESS and tc.tool == "execute_device_command" for tc in tool_calls):
            final = "dry_run_executed"
        elif any(tc.status == ToolCallStatus.SKIPPED for tc in tool_calls):
            final = "action_skipped_by_approval"
        else:
            final = "answered"

        error = ErrorInfo(type=llm_result.error, message=f"LLM issue: {llm_result.error}") if llm_result.error else None

        return AgentResponse(
            answer=answer,
            intent=intent,
            sources=sources,
            confidence=confidence,
            safety_level=assessment.level,
            need_human_approval=assessment.need_human_approval,
            tool_calls=tool_calls,
            final_action=final,
            error=error,
        )

    def _confidence(self, matches, level, llm_error) -> float:
        if llm_error:
            return 0.3
        if not matches:
            return 0.2
        if level == SafetyLevel.L2:
            return 0.35
        if level == SafetyLevel.L1:
            return 0.7
        return 0.85

    def _cancelled_response(self, trace) -> AgentResponse:
        trace.set("interrupted", True)
        resp = AgentResponse(
            answer="运行已被用户打断（ESC）。",
            intent=Intent.UNKNOWN,
            sources=[],
            confidence=0.0,
            safety_level=SafetyLevel.L2,
            need_human_approval=True,
            tool_calls=[],
            final_action="cancelled_by_user",
            error=ErrorInfo(type="cancelled", message="User interrupted the run with ESC."),
        )
        trace.set("final_json", resp.model_dump(mode="json"))
        trace.save()
        return resp

    def _fallback(self, trace, intent, level, answer, error_type) -> AgentResponse:
        etype = error_type.split(":")[0] if error_type else None
        resp = AgentResponse(
            answer=answer,
            intent=intent,
            sources=[],
            confidence=0.0,
            safety_level=level,
            need_human_approval=(level == SafetyLevel.L2),
            tool_calls=[],
            final_action="fallback",
            error=ErrorInfo(type=etype, message=error_type) if error_type else None,
        )
        trace.set("final_json", resp.model_dump(mode="json"))
        trace.save()
        return resp

    def _chat(self, raw_input: str, trace, token) -> AgentResponse:
        """闲聊/自由对话：LLM 自由回答，L0，不检索/不工具/不审批。

        real 模式由 LLM 自由对话；mock 模式给友好默认回复。
        结构化 JSON / 工具链路是为"需要工具的场景"设计的，闲聊不进入该链路。
        """

        trace.set("branch", "chat")
        enhanced = self._safe_enhance(raw_input, [], trace)
        token.check()
        llm_result = self.llm.generate(enhanced, cancel_token=token)
        trace.set("llm_raw_output", (llm_result.raw_output or "")[:500])
        validated, _ = validate_draft(llm_result.parsed) if llm_result.parsed else (None, None)
        default = "你好，我是制造业设备安全操作 Agent，可以回答设备说明、故障排查、安全规则，或执行经审批的 dry-run 动作。"
        if self.llm_mode == "real" and validated and validated.answer:
            answer = validated.answer
        else:
            answer = default
        resp = AgentResponse(
            answer=answer,
            intent=Intent.UNKNOWN,
            sources=[],
            confidence=0.6,
            safety_level=SafetyLevel.L0,
            need_human_approval=False,
            tool_calls=[],
            final_action="chat",
            error=None,
        )
        self.memory.add_turn("user", raw_input, trace.trace_id)
        self.memory.add_turn("assistant", answer, trace.trace_id)
        trace.set("final_json", resp.model_dump(mode="json"))
        trace.save()
        self.logger.info("trace %s intent=chat level=L0", trace.trace_id)
        return resp
