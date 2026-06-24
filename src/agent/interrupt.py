"""ESC 强制打断：CancellationToken + 跨平台键盘监听。

设计（plan 第 12 节）：

- 每次用户请求创建一个 ``CancellationToken``，runner 在 LLM streaming、工具调用、
  主流程关键阶段调用 ``token.check()``，已取消则抛 ``CancelledError``。
- ``EscListener`` 在后台线程监听 ESC（Windows 用 msvcrt），按下后触发取消。
- 被打断时 runner 捕获 ``CancelledError`` 并输出合法 cancelled JSON，不直接崩溃。
- 单元测试不真实按键，直接 ``token.cancel()`` 验证流程。
"""

from __future__ import annotations

import threading


class CancelledError(Exception):
    """用户 ESC 打断。"""


class CancellationToken:
    """线程安全的取消标记，基于 threading.Event。"""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        """在关键阶段调用；若已取消则抛 CancelledError。"""

        if self._event.is_set():
            raise CancelledError("cancelled by user (ESC)")

    def reset(self) -> None:
        self._event.clear()


class EscListener:
    """后台监听 ESC 键，按下后取消给定 token。

    Windows 使用 msvcrt；非 Windows 平台跳过真实监听（单元测试通过 token.cancel() 控制）。
    """

    def __init__(self, token: CancellationToken) -> None:
        self.token = token
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        try:
            import msvcrt  # Windows only
        except ImportError:
            return  # 非 Windows：不进行真实按键监听
        while not self._stop.is_set():
            try:
                if msvcrt.kbhit() and msvcrt.getch() == b"\x1b":  # ESC
                    self.token.cancel()
                    break
            except OSError:
                break
            self._stop.wait(0.05)
