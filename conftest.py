"""pytest 配置：临时目录固定到项目内 ``.temp``，trace 隔离到 tmp_path。

解决三个问题：

1. 系统临时目录权限不足导致 ``tmp_path`` 写入失败；
2. 固定单一 ``--basetemp`` 复跑时清理旧目录触发文件锁/权限错误；
3. 测试产生的 trace 污染真实 ``runs/``（每次跑测试堆积垃圾 trace）。

策略：basetemp 动态生成 ``.temp/pytest-<时间戳>-<pid>``；autouse fixture 把
``trace.RUNS_DIR`` 指向 ``tmp_path``，使所有测试的 trace 落在临时目录，不污染 runs/。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest


def pytest_configure(config):
    if config.option.basetemp:
        return  # 尊重命令行显式指定的 basetemp
    basetemp = Path(".temp") / f"pytest-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    basetemp.parent.mkdir(exist_ok=True)
    config.option.basetemp = basetemp


@pytest.fixture(autouse=True)
def _isolate_runs_dir(tmp_path, monkeypatch):
    """所有测试的 trace 写 tmp_path，不污染真实 runs/（根治测试垃圾 trace）。"""

    from agent import trace as trace_mod
    monkeypatch.setattr(trace_mod, "RUNS_DIR", tmp_path)
