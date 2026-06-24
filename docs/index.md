---
tags: [project, index, planning]
date: 2026-06-24
summary: 项目索引；M1 基础设施完成，进入模块实现阶段
status: in_progress
---

# 项目索引

## 当前状态

- 项目：制造业设备安全操作 Agent（AI Agent 实习二面小项目，CLI-only）。
- 方案：`plan/initial_solution.md`（v2，已通过 7 项门控校验）。
- 实现进度：
  - ✅ M1 基础设施 / ✅ M2 知识库 / ✅ M3 工具 / ✅ M4 意图 / ✅ M5 安全策略
  - ⏳ 进行中：M6–M11（审批/记忆/打断/LLM/runner+CLI/README）
- 测试：全量 55 passed（`pytest tests/`，临时目录由 `conftest.py` 动态生成唯一子目录）；详见 `docs/03-tests/2026-06-24.md`。
- 安全：真实 DeepSeek key 仅存 `.env`（已 gitignore）；`.env.example` 不含真实值；源码无硬编码。
- 尚未完成：审批、记忆、打断、LLM、runner、CLI、README。

## 模块地图

- `plan/`：项目方案与后续细化记录。
- `src/agent/`：源码包（`python -m agent.cli` 入口，editable install）。
  - `models.py`：Pydantic 数据模型（AgentResponse 等）。
  - `config.py`：三份配置加载与保守回退。
  - `trace.py`：trace_id / TraceRecorder / setup_logging。
  - `knowledge.py / intent.py / safety.py / approval.py / memory.py / interrupt.py / llm.py / tools.py / runner.py / cli.py`：待实现。
- `config/`：`safety_rules.json`、`llm_config.json`、`memory_config.json`。
- `tests/`：正式自动化测试（pytest）。
- `.temp/`：一次性验证、排障脚本（已 gitignore）。
- `experiments/`：实验功能。`benchmarks/`：性能测试。`replays/`：问题复现。
- `docs/01-changelog/`：功能变更记录。`docs/02-debug/`：调试记录。
- `docs/03-tests/`：测试报告。`docs/04-reviews/`：审查记录。`docs/05-design/`：设计决策。
- `logs/`：运行日志（运行产物，已 gitignore）。
- `runs/`：单次 trace（运行证据，**保留**为提交样例，不忽略）。

## 时间线

- 2026-06-24：读取二面题目并保存制造业设备安全操作 Agent 初版方案。
- 2026-06-24：方案 v2 多轮细化（DeepSeek V4 Pro、结构化 JSON、ESC 打断、工作记忆压缩、7 项门控）。
- 2026-06-24：按 `AGENTS.md` 补齐项目规范目录。
- 2026-06-24：**M1 完成** —— git init、pyproject + .venv 依赖、config 层、models.py、config.py、trace.py；`tests/test_infra.py` 11 passed；安全扫描通过。
- 2026-06-24：**M2 完成** —— knowledge/*.md 四文件 + knowledge.py lexical 检索；`tests/test_knowledge.py` 8 passed；全量 19 passed。
- 2026-06-24：**审查修复** —— pytest 临时目录固化为 `.temp/pytest`；config 默认值 deepcopy 防污染；补 `docs/03-tests/2026-06-24.md` 测试报告；更新索引。
- 2026-06-24：**M3 完成** —— tools.py 三 mock 工具 + 白名单；11 passed。
- 2026-06-24：**M4 完成** —— intent.py 五分类意图识别；9 passed。
- 2026-06-24：**M5 完成** —— safety.py 安全定级 L0/L1/L2 + 参数边界 + 设备拦截；15 passed；全量 54 passed。
- 2026-06-24：**P0/P1 修复** —— L2 审批硬编码不可被配置绕过（+回归测试）；测试改用 conftest 动态唯一 basetemp；全量 55 passed。

## 待确认

- 是否在基础功能完成后追加可选 LangGraph 编排版本（加分项，首版不做）。
- 测试与交付证据保留哪些 `runs/<trace_id>.json`。
- DeepSeek key 已在对话中暴露，建议后台轮换。
