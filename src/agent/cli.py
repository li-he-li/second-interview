"""CLI 入口：单轮命令 + 交互式 REPL，支持 ESC 打断当前运行。

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

from dotenv import load_dotenv

from .interrupt import CancellationToken, EscListener
from .runner import Agent


def _cli_responder(req) -> str:
    try:
        choice = input(
            f"\n[审批请求] 等级={req.safety_level.value} 工具={req.tool_name}\n"
            f"  风险原因：{req.risk_reason}\n"
            f"是否批准？[yes / no / allyes]: "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "no"  # 非交互或取消时安全拒绝
    return choice or "no"


def _run_once(agent: Agent, text: str) -> None:
    token = CancellationToken()
    listener = EscListener(token)
    listener.start()
    try:
        resp = agent.handle(text, cancel_token=token, responder=_cli_responder)
    finally:
        listener.stop()
    print(json.dumps(resp.model_dump(mode="json"), ensure_ascii=False, indent=2))


def _repl(agent: Agent) -> None:
    print("制造业设备安全操作 Agent（输入 exit 退出；运行中按 ESC 可打断）")
    while True:
        try:
            text = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower() in ("exit", "quit", "退出"):
            break
        _run_once(agent, text)


def main(argv=None) -> None:
    # 尽量以 UTF-8 输出，避免 Windows GBK 控制台中文乱码
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
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

    agent = Agent(llm_mode=args.llm)
    if args.session or args.input is None:
        _repl(agent)
    else:
        _run_once(agent, args.input)


if __name__ == "__main__":
    main()
