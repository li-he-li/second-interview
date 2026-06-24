# 制造业设备安全操作 Agent

一个 CLI-only 的制造业设备安全操作 Agent：基于本地知识库回答问题、识别用户意图、判断设备动作安全等级，并在危险或不确定场景下明确 fallback，绝不编造结果。

> AI Agent 开发实习岗二面小项目。默认 mock LLM 模式，无需 API key 即可跑通主流程；可选真实 DeepSeek LLM。

---

## 快速开始

### 安装

```bash
python -m venv .venv
# Windows
.venv\Scripts\python.exe -m pip install -e ".[dev]"
# macOS/Linux
source .venv/bin/activate && pip install -e ".[dev]"
```

依赖：Python 3.11+、pydantic、python-dotenv、tiktoken、openai、pytest（dev）。

### 运行（默认 mock，无需 key）

先激活虚拟环境（每个新终端执行一次）：

```bash
# Windows (Git Bash / CMD / PowerShell)
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

激活后即可运行（若不想激活，可将下方 `python` 替换为 `.venv\Scripts\python.exe` 或 `.venv/bin/python`）：

```bash
# 单轮
python -m agent.cli "设备报错 E42，应该怎么排查？"

# 交互式会话（输入 exit 退出，运行中按 ESC 可打断）
python -m agent.cli --session
```

### 真实 LLM 模式（需 DeepSeek key）

```bash
cp .env.example .env   # 填入 DEEPSEEK_API_KEY
python -m agent.cli --llm real --provider deepseek --model deepseek-v4-pro "设备报错 E42，应该怎么排查？"
```

未配置 key 时，real 模式会明确报错 `no_api_key`，不伪装成功。

### 运行测试

```bash
.venv\Scripts\python.exe -m pytest tests/
```

临时目录由 `conftest.py` 动态生成项目内唯一子目录（`.temp/pytest-<时间戳>-<pid>`），避免系统 temp 权限问题；pytest cache 已禁用。

---

## 功能边界

- ✅ 单轮 CLI 输入 + 交互式 REPL，均支持 ESC 打断。
- ✅ mock LLM（默认）+ 真实 DeepSeek LLM 两种模式。
- ✅ 本地知识库检索，输出 `sources`。
- ✅ 五类意图识别：`qa / status_check / device_action / unsafe_action / unknown`。
- ✅ 结构化 JSON 输出，Pydantic schema 校验。
- ✅ L0/L1/L2 安全分级 + 人工审批（`yes / no / allyes`）。
- ✅ 短期工作记忆 + token 超限压缩。
- ✅ 失败/危险场景统一 fallback，不编造。
- ❌ 不做 FastAPI、不做前端（明确 CLI-only）。
- ❌ 不提供真实设备执行入口（`execute_device_command` 永远 dry-run）。

---

## 输出 JSON 结构

```json
{
  "answer": "给用户的回答",
  "intent": "qa | status_check | device_action | unsafe_action | unknown",
  "sources": ["知识库来源 ID"],
  "confidence": 0.0,
  "safety_level": "L0 | L1 | L2",
  "need_human_approval": false,
  "tool_calls": [{"tool": "...", "input": {}, "output": {}, "status": "success | failed | skipped"}],
  "final_action": "最终执行或建议执行的动作",
  "error": null
}
```

---

## 数据流

```
用户输入 → ESC 监听 → 空输入检查 → trace_id
  → 读取短期记忆 → search_knowledge
  → 提示词增强 → LLM（或规则链路）识别意图
  → 参数解析与越界检查 → 安全等级判断
  → L1/L2 审批检查 → 必要时 get_device_status / execute_device_command(dry_run)
  → Pydantic 校验最终 JSON → 写入记忆（必要时压缩）
  → 输出 JSON 并保存 runs/<trace_id>.json
```

---

## 安全策略与人工审批

| 等级 | 含义 | 审批 |
|------|------|------|
| L0 | 只读问答/状态查询 | 否 |
| L1 | 低风险动作 | 是，批准后 dry-run |
| L2 | 危险动作/越界参数/危险词/工具异常/检索失败/不确定 | 是，**即使批准也不真实执行** |

- **L2 审批为硬约束**：`need_human_approval` 恒为 `True`，不可被配置绕过。
- `allyes` 仅当前会话全局放行，不持久化、不降级、不改 L2→L1。
- 非交互模式（单轮命令）审批默认拒绝，安全优先。

坐标边界 ±1000、速度仅 `low/normal/safe`、力度上限 50N，均可配置。

---

## 结构化 JSON 保证策略

最终 JSON **不依赖 LLM 自觉**，由本地代码统一组装校验：

1. system prompt 要求 LLM 只输出严格 JSON + prompt injection 防护（不可信数据不得改规则/权限）。
2. LLM 输出经 JSON parser → 失败记 `model_invalid_json`。
3. `validate_draft` 用 AgentResponse schema 校验枚举/范围 → 非法回退规则链路。
4. `sources` 只接受 `search_knowledge` 的真实来源，`filter_sources` 过滤 LLM 编造。
5. `safety_level` 以 `safety.py` 为准，不信任 LLM 草稿。
6. 最终 JSON 由 AgentResponse 序列化，不直接打印 LLM 原文。

---

## 模块结构

```
src/agent/
├── cli.py          # CLI 入口（单轮/REPL/ESC）
├── runner.py       # 主流程串联
├── models.py       # Pydantic 数据模型
├── knowledge.py    # 知识库 lexical 检索
├── intent.py       # 意图识别（规则链路）
├── safety.py       # 安全分级 + 参数边界
├── approval.py     # 人工审批 yes/no/allyes
├── memory.py       # 短期工作记忆 + token 压缩
├── interrupt.py    # ESC 打断 CancellationToken
├── llm.py          # mock + real LLM + 提示词增强
├── tools.py        # 3 个 mock 工具 + 白名单
├── config.py       # 配置加载（保守回退）
└── trace.py        # trace_id + runs 落盘 + 日志
```

## 配置

| 文件 | 作用 |
|------|------|
| `.env` / `.env.example` | `LLM_MODE`、`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`、`CONFIG_DIR` |
| `config/safety_rules.json` | 坐标/速度/力度边界、危险词、审批开关 |
| `config/llm_config.json` | provider/model/base_url/超时/重试/流式 |
| `config/memory_config.json` | token 预算/压缩阈值/保留轮次 |

配置缺失或非法时一律回退保守默认值并记 `config_warning`。

## 知识库

`knowledge/*.md`：`device_overview`（设备说明）/ `safety_rules`（安全规则）/ `troubleshooting`（故障排查）/ `forbidden_actions`（禁止动作）。按 `##` 标题切 chunk，lexical 评分检索，冲突时按 禁止动作 > 安全规则 > 故障排查 > 设备说明 优先。

## 失败处理

覆盖：空输入、检索不到、工具超时/异常、模型非法 JSON、危险指令、参数越界、ESC 打断、缺 key、模型超时、提示词增强异常、配置缺失。所有情况均输出合法 JSON 并记 `error` 字段，不崩溃、不编造。

## 运行证据

每次运行产出 `runs/<trace_id>.json`（运行证据）与 `logs/agent.log`（运行日志，已 gitignore）。提交样例见 `runs/`。

---

## AI coding 使用说明（诚实披露）

- **方案设计**：`plan/initial_solution.md` 为人工前期设计，经多轮细化与 Harness Engineering 门控校验。
- **代码实现**：由 Claude Code（GLM-5.2 驱动的 CLI）按方案分里程碑实现（M1–M11），每里程碑配套 pytest 测试与人工审查清单。
- **审查驱动修改**：多轮人工审查反馈（L2 审批硬约束、测试稳定性、schema 校验、prompt injection 防护、client_init 区分等）由人工提出、Claude 实施。
- **真实 LLM**：DeepSeek `deepseek-v4-pro` 实测连通，安全判断正确（E42→L0、最大速度越界→L2）。
- 开发轨迹见 `docs/`（changelog / reviews / tests / design），git 历史可见每个里程碑的提交。

---

## 到岗信息确认（由候选人填写）

> 以下信息留空，由提交者自行填写。

1. 最早可到宁波线下日期：______
2. 最晚可实习到哪一天：______
3. 是否能每周 5 天线下：______
