"""pytest 配置：把临时目录固定到项目内 ``.temp`` 下的唯一子目录。

解决两个问题：

1. 系统临时目录权限不足导致 ``tmp_path`` 写入失败；
2. 固定单一 ``--basetemp`` 复跑时，pytest 清理旧 session 目录触发文件锁/权限错误。

策略：每次运行生成 ``.temp/pytest-<时间戳>-<pid>`` 唯一目录，互不清理；
若命令行显式指定 ``--basetemp``，则尊重用户选择。``.temp`` 已在 .gitignore。
"""

from __future__ import annotations

import os
import time
from pathlib import Path


def pytest_configure(config):
    if config.option.basetemp:
        return  # 尊重命令行显式指定的 basetemp
    basetemp = Path(".temp") / f"pytest-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    basetemp.parent.mkdir(exist_ok=True)
    config.option.basetemp = basetemp
