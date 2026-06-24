"""CLI 入口：单轮命令 + Claude Code 风格交互式 REPL，支持 ESC 打断。

单轮命令输出结构化 JSON（适合管道/测试）；REPL 以聊天式为主：
    answer 为主要输出，元数据与工具调用以简要摘要呈现；完整 JSON 保存到 runs/。
    审批明确显示 yes/no/allyes，便于用户做安全确认。

用法：
    python -m agent.cli "设备报错 E42，应该怎么排查？"
    python -m agent.cli --session
    python -m agent.cli --llm real --provider deepseek --model deepseek-v4-pro "..."
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Optional

from dotenv import load_dotenv

from .interrupt import CancellationToken, EscListener
from .models import SafetyLevel
from .runner import Agent

# 上一轮结构化结果，仅用于交互层生成 trace 提示，不默认渲染给用户
_last_payload: Optional[dict[str, Any]] = None


def _color(text: str, code: str) -> str:
    if os.getenv("NO_COLOR") or not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def _rule(title: str = "") -> str:
    line = "─" * 60
    return f"╭─ {title} {line[len(title) + 3:]}" if title else f"╭{line}"


def _prompt() -> str:
    return _color("device-safety", "36;1") + _color(" › ", "37")


def _level_label(level: SafetyLevel | str) -> str:
    value = level.value if isinstance(level, SafetyLevel) else str(level)
    color = {"L0": "32;1", "L1": "33;1", "L2": "31;1"}.get(value, "37")
    return _color(value, color)


def _tool_brief(t: dict[str, Any]) -> str:
    """工具调用简要摘要：name(status, 关键结果)。"""

    name = t.get("tool", "?")
    status = t.get("status", "?")
    mark = {"success": "OK", "failed": "FAIL", "skipped": "SKIP"}.get(status, status)
    out = t.get("output") or {}
    detail = ""
    if name == "search_knowledge":
        detail = f", {out.get('count', 0)} 条命中"
    elif name == "get_device_status":
        detail = f", {out.get('status', '?')}"
    elif name == "execute_device_command":
        detail = ", dry-run" if out.get("dry_run") else ""
    st_color = {"success": "32", "failed": "31", "skipped": "33"}.get(status, "37")
    return f"{name}({_color(mark, st_color)}{detail})"


def _brief_input(inp: dict) -> str:
    if not inp:
        return ""
    if "query" in inp:
        q = str(inp.get("query", ""))[:40]
        return f'"{q}"'
    if "command" in inp:
        return json.dumps(inp.get("command"), ensure_ascii=False)[:50]
    return json.dumps(inp, ensure_ascii=False)[:40]


def _make_event_handler():
    """agent loop 事件 → Claude Code 式实时显示（LLM 回复 + 工具调用友好提示，不渲染 JSON）。"""

    def on_event(event: str, payload: Any) -> None:
        if event == "answer" and payload:
            print(payload)
        elif event == "tool_call":
            name = payload.get("tool", "?")
            brief = _brief_input(payload.get("input") or {})
            print(_color(f"● {name}", "36;1") + (f"  {brief}" if brief else ""))
        elif event == "tool_result":
            tdict = {"tool": payload.tool, "status": payload.status.value, "output": payload.output}
            print(_color(f"  ↳ {_tool_brief(tdict)}", "90"))

    return on_event


def _cli_responder(req) -> str:
    """审批：明确输入 yes/no/allyes；兼容 y/n/1/2/3 快捷输入。"""

    print()
    print(_rule("approval"))
    print(f"│ level: {_level_label(req.safety_level)}  tool: {req.tool_name}")
    print(f"│ risk : {req.risk_reason}")
    print("│  yes     批准本次")
    print("│  no      拒绝（工具记 skipped）")
    print("│  allyes  本会话全部放行（仍不真实执行危险动作）")
    try:
        choice = input(_color("╰─ 选择 [yes/no/allyes] (默认 no): ", "33;1")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return "no"  # 非交互或取消时安全拒绝
    return {
        "yes": "yes",
        "y": "yes",
        "1": "yes",
        "no": "no",
        "n": "no",
        "2": "no",
        "allyes": "allyes",
        "all": "allyes",
        "3": "allyes",
    }.get(choice, "no")


def _run_once(agent: Agent, text: str, *, interactive: bool = False) -> None:
    global _last_payload
    token = CancellationToken()
    listener = EscListener(token)
    listener.start()

    def responder(req):
        # 审批期间需要读取用户输入，暂停 ESC 后台监听，避免 Windows msvcrt 抢键盘字节。
        listener.stop()
        try:
            return _cli_responder(req)
        finally:
            if not token.is_cancelled():
                listener.start()

    on_event = _make_event_handler() if interactive else None
    try:
        resp = agent.handle(text, cancel_token=token, responder=responder, on_event=on_event)
    finally:
        listener.stop()
    payload = resp.model_dump(mode="json")
    _last_payload = payload
    if interactive:
        print()  # answer 已在 loop 中实时显示，这里仅输出元数据
        _print_meta(payload)
    else:
        # 单轮：打印结构化 JSON（适合管道与题目验收）
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def _print_banner(agent: Agent) -> None:
    print(_rule("device safety agent"))
    print("│ 制造业设备安全操作 CLI（聊天式，类 Claude Code）")
    print(f"│ llm={agent.llm_mode}  session-memory=on  esc=cancel")
    print("│ commands: /help /status /clear /trace /exit")
    print("╰" + "─" * 60)


def _print_help() -> None:
    print(_rule("help"))
    print("│ 直接输入问题或设备指令，回车提交（支持中文）")
    print("│ /status  查看会话/设备状态")
    print("│ /clear   清空短期工作记忆")
    print("│ /trace   提示上一轮结构化 JSON 保存位置")
    print("│ /exit    退出会话")
    print("│ ESC      打断当前运行")
    print("╰" + "─" * 60)


def _print_status(agent: Agent) -> None:
    device = agent.memory.device_state or {"status": "unknown"}
    print(_rule("status"))
    print(f"│ llm_mode      : {agent.llm_mode}")
    print(f"│ memory_tokens : {agent.memory.token_count()}")
    print(f"│ safety_facts  : {len(agent.memory.pinned_safety)} 条")
    print(f"│ turns         : {len(agent.memory.turns)}")
    print(f"│ device_state  : {json.dumps(device, ensure_ascii=False)}")
    print("╰" + "─" * 60)


def _print_meta(payload: dict[str, Any]) -> None:
    """agent loop 后的元数据摘要（answer 已在 loop 实时显示；fallback/cancelled 兜底）。"""

    global _last_payload
    _last_payload = payload
    action = payload.get("final_action", "")
    if action in ("fallback", "cancelled_by_user"):
        print(payload.get("answer", ""))  # loop 未实时显示，这里补
    level = _level_label(payload.get("safety_level", ""))
    meta = f"  {payload.get('intent', 'unknown')} · {level} · conf {payload.get('confidence', 0.0):.2f}"
    tools = payload.get("tool_calls") or []
    if tools:
        meta += " · " + " · ".join(_tool_brief(t) for t in tools)
    print(_color(meta, "90"))
    sources = payload.get("sources") or []
    if sources:
        print(_color(f"  sources: {', '.join(sources[:3])}", "90"))
    if payload.get("error"):
        print(_color(f"  error: {payload['error'].get('type')}", "31"))
    if payload.get("need_human_approval"):
        print(_color("  [需人工审批] 结构化 JSON 已保存到 runs/<trace_id>.json", "33"))
    else:
        print(_color("  结构化 JSON 已保存到 runs/<trace_id>.json（/trace 查看）", "90"))


def _repl(agent: Agent) -> None:
    global _last_payload
    _print_banner(agent)
    while True:
        try:
            text = input("\n" + _prompt())
        except (EOFError, KeyboardInterrupt):
            print()
            break
        text = text.strip()
        if not text:
            continue
        command = text.lower()
        if command in ("exit", "quit", "退出", "/exit", "/quit"):
            break
        if command in ("/help", "help", "?"):
            _print_help()
            continue
        if command == "/status":
            _print_status(agent)
            continue
        if command == "/clear":
            agent.memory.turns.clear()
            agent.memory.summary.clear()
            agent.memory.pinned_safety.clear()
            _last_payload = None
            print(_color("session memory cleared", "32"))
            continue
        if command == "/trace":
            if _last_payload:
                print(_color("上一轮结构化 JSON 已写入 runs/<trace_id>.json；单轮命令仍会直接输出 JSON。", "90"))
            else:
                print(_color("尚无运行结果，先输入一个问题吧", "90"))
            continue
        _run_once(agent, text, interactive=True)


def main(argv=None) -> None:
    # 仅重配输出流；不重配 stdin，避免 Windows 终端中文输入解码异常。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

    load_dotenv()
    parser = argparse.ArgumentParser(prog="agent.cli", description="制造业设备安全操作 Agent")
    parser.add_argument("input", nargs="?", help="单轮输入；省略且未指定 --session 时进入 REPL")
    parser.add_argument("--session", action="store_true", help="交互式会话")
    parser.add_argument(
        "--llm", default=os.getenv("LLM_MODE", "mock"), choices=["mock", "real"], help="LLM 模式，默认 mock"
    )
    parser.add_argument("--provider", default=os.getenv("LLM_PROVIDER", "deepseek"))
    parser.add_argument("--model", default=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro"))
    args = parser.parse_args(argv)

    agent = Agent(
        llm_mode=args.llm,
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        model=args.model,
    )
    if args.session or args.input is None:
        _repl(agent)
    else:
        _run_once(agent, args.input)


if __name__ == "__main__":
    main()
