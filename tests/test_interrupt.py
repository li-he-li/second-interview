"""M8 ESC 打断测试（不真实按键，直接操作 token）。"""

from __future__ import annotations

import pytest

from agent.interrupt import CancelledError, CancellationToken, EscListener


def test_token_not_cancelled_by_default():
    token = CancellationToken()
    assert token.is_cancelled() is False
    token.check()  # 不抛


def test_cancel_sets_flag_and_check_raises():
    token = CancellationToken()
    token.cancel()
    assert token.is_cancelled() is True
    with pytest.raises(CancelledError):
        token.check()


def test_reset_clears_cancellation():
    token = CancellationToken()
    token.cancel()
    token.reset()
    assert token.is_cancelled() is False
    token.check()  # 不抛


def test_check_can_be_called_repeatedly_after_cancel():
    token = CancellationToken()
    token.cancel()
    with pytest.raises(CancelledError):
        token.check()
    with pytest.raises(CancelledError):
        token.check()  # 仍取消


def test_esc_listener_constructible_without_crash():
    # 仅验证可构造/启停；不真实按键（跨平台兼容）
    token = CancellationToken()
    listener = EscListener(token)
    listener.start()
    listener.stop()
    # 停止后 token 仍可控
    token.cancel()
    assert token.is_cancelled() is True


def test_runner_style_flow_uses_check_at_critical_points():
    # 模拟 runner 在关键阶段检查 token
    token = CancellationToken()

    def fake_long_task():
        for _ in range(3):
            token.check()  # 关键检查点
        return "done"

    assert fake_long_task() == "done"
    token.cancel()
    with pytest.raises(CancelledError):
        fake_long_task()
