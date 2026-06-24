# 制造业设备安全操作 Agent 初版方案 v2

## 1. 目标与成功标准

实现一个 CLI-only 的制造业设备安全操作 Agent，用于回答设备知识库问题、识别用户意图、判断设备动作安全等级，并在危险或不确定场景下明确 fallback。

成功标准：

- 能通过 CLI 接收单轮输入，也支持交互式会话。
- 同时支持 mock LLM 和真实 LLM 两种启动方式；默认 mock，保证无 API key 可运行。
- 真实 LLM 首选 DeepSeek V4 Pro，模型名使用 `deepseek-v4-pro`。
- 能从本地知识库检索证据，并在输出中给出 `sources`。
- 能识别 `qa`、`status_check`、`device_action`、`unsafe_action`、`unknown` 五类意图。
- 能输出符合题目要求的结构化 JSON，并通过 Pydantic schema 校验。
- 涉及 L1/L2 风险等级的命令或工具调用时，进入人工审批：`yes`、`no`、`allyes`。
- 单轮命令和交互式会话都支持 ESC 强制打断运行中的 LLM 生成、工具调用或 Agent 主流程。
- 具备短期工作记忆；当会话 token 数超过限制时自动压缩。
- 遇到危险指令、越界参数、工具异常、检索失败、非法 JSON、空输入时，不编造结果，并升级为 `L2` 与 `need_human_approval=true`。
- 至少提供 5 条正式测试样例和运行记录。

## 2. 技术选型

初版只做 Python CLI，不做前端，不做 FastAPI。

- 语言：Python 3.11+
- CLI：`argparse` + 交互式 REPL
- 数据模型和校验：Pydantic
- LLM 模式：`mock` 默认模式 + `real` 真实模型模式
- 测试框架：pytest
- 日志：标准库 `logging` + 每次运行保存 `runs/<trace_id>.json`
- 知识库：`knowledge/*.md`，启动时加载并切分为检索片段
- 安全配置：`config/safety_rules.json`
- 真实 LLM 配置：`config/llm_config.json`
- 工作记忆：`config/memory_config.json` + 会话内结构化工作记忆 + token 计数 + 压缩摘要
- ESC 打断：主流程使用可取消任务和 cancellation flag，CLI 监听 ESC 后安全中断

建议依赖：

```text
pydantic
pytest
python-dotenv
tiktoken
openai
```

说明：

- `openai` 只作为 OpenAI-compatible SDK 使用，真实 LLM 默认连接 DeepSeek API；mock 模式不需要 API key。
- DeepSeek 默认配置为：provider `deepseek`，model `deepseek-v4-pro`，base URL `https://api.deepseek.com`。
- `tiktoken` 用于工作记忆 token 计数；若不可用，可降级为估算计数，但 README 需要说明。
- 不建议首版强依赖 LangGraph，理由见第 15 节。

## 3. CLI 启动方式

单轮运行：

```bash
python -m agent.cli "设备报错 E42，应该怎么排查？"
```

交互式会话：

```bash
python -m agent.cli --session
```

mock LLM 模式：

```bash
python -m agent.cli --llm mock "机械臂现在可以执行抓取动作吗？"
```

真实 LLM 模式：

```bash
python -m agent.cli --llm real --provider deepseek --model deepseek-v4-pro "设备报错 E42，应该怎么排查？"
```

建议环境变量：

```text
LLM_MODE=mock
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
CONFIG_DIR=config
```

默认策略：

- 默认 `--llm mock`。
- 默认所有设备动作都是 dry-run。
- 未配置 `DEEPSEEK_API_KEY` 时，真实 LLM 模式启动应给出明确错误并退出，不回退成假装真实调用。

## 4. 推荐目录结构

```text
.
├── README.md
├── .env.example
├── .gitignore
├── knowledge/
│   ├── device_overview.md
│   ├── safety_rules.md
│   ├── troubleshooting.md
│   └── forbidden_actions.md
├── config/
│   ├── safety_rules.json
│   ├── llm_config.json
│   └── memory_config.json
├── src/
│   └── agent/
│       ├── __init__.py
│       ├── cli.py
│       ├── models.py
│       ├── knowledge.py
│       ├── intent.py
│       ├── safety.py
│       ├── approval.py
│       ├── memory.py
│       ├── llm.py
│       ├── interrupt.py
│       ├── tools.py
│       ├── runner.py
│       └── trace.py
├── tests/
│   ├── test_agent_samples.py
│   ├── test_approval.py
│   ├── test_memory.py
│   ├── test_knowledge.py
│   ├── test_safety.py
│   └── test_failures.py
├── runs/
├── logs/
└── docs/
```

模块职责：

- `runner.py`：串联输入、记忆、检索、意图、安全判断、审批、工具调用和最终输出。
- `models.py`：定义输入、工具调用记录、最终响应等 Pydantic 模型。
- `llm.py`：封装 mock LLM 和真实 LLM，不让业务流程直接依赖厂商 SDK。
- `knowledge.py`：加载、切分、索引和搜索知识库。
- `config.py`：加载安全规则、真实 LLM 配置和工作记忆配置，配置缺失时使用保守默认值。
- `approval.py`：处理 `yes/no/allyes` 和会话级审批缓存。
- `memory.py`：管理短期工作记忆和 token 超限压缩。
- `interrupt.py`：处理 ESC 打断和任务取消。
- `tools.py`：实现题目要求的 3 个 mock 工具。
- `safety.py`：集中处理安全等级、参数范围和人工审批规则。
- `trace.py`：负责 trace_id、日志字段和 `runs/<trace_id>.json` 保存。

## 5. 核心数据流

```text
用户输入
  -> ESC 监听启动
  -> 空输入检查
  -> 创建 trace_id
  -> 读取短期记忆
  -> search_knowledge(query)
  -> LLM 或规则链路识别意图
  -> 参数解析和越界检查
  -> 安全等级判断
  -> L1/L2 审批检查
  -> 必要时 get_device_status()
  -> 必要时 execute_device_command(command, dry_run=True)
  -> Pydantic 校验最终 JSON
  -> 写入短期记忆，必要时压缩
  -> 输出 JSON 并保存 trace
```

关键原则：

- 检索不到资料时，不能凭空回答；输出低置信度，并要求人工确认。
- 涉及设备动作时，必须先判断安全等级，再决定是否进入人工审批。
- `execute_device_command` 默认 `dry_run=True`，初版不提供真实设备执行入口。
- `L2` 即使人工确认，也只允许继续完成 dry-run、解释、日志或诊断流程，不允许真实执行危险动作。
- 高风险或不确定场景统一走 `L2`。

## 6. 输出 JSON 结构

```json
{
  "answer": "给用户的回答",
  "intent": "qa | status_check | device_action | unsafe_action | unknown",
  "sources": ["知识库来源 ID 或片段"],
  "confidence": 0.0,
  "safety_level": "L0 | L1 | L2",
  "need_human_approval": false,
  "tool_calls": [
    {
      "tool": "工具名",
      "input": {},
      "output": {},
      "status": "success | failed | skipped"
    }
  ],
  "final_action": "最终执行或建议执行的动作",
  "error": null
}
```

初版置信度策略：

- 有知识库命中且意图明确：`0.75` 到 `0.9`
- 有知识库命中但动作需要审批：`0.6` 到 `0.75`
- 无知识库命中、输入不清晰、工具失败或被 ESC 打断：`0.0` 到 `0.4`

## 7. LLM 策略

### mock LLM

mock LLM 是默认模式，必须覆盖完整主流程。

职责：

- 根据规则输出意图候选。
- 根据知识库片段生成简短回答。
- 可通过测试参数模拟非法 JSON、超时或异常。
- 输出原始 mock 内容到 trace。

### 真实 LLM

真实 LLM 只负责语言理解和回答草稿，不拥有最终安全决策权。

默认提供商：

- provider：`deepseek`
- model：`deepseek-v4-pro`
- base_url：`https://api.deepseek.com`
- SDK：使用 OpenAI-compatible API 客户端调用 DeepSeek

真实 LLM 默认配置写入 `config/llm_config.json`：

```json
{
  "provider": "deepseek",
  "model": "deepseek-v4-pro",
  "base_url": "https://api.deepseek.com",
  "timeout_seconds": 180,
  "max_retries": 3,
  "stream": true,
  "retry_once_on_invalid_json": true
}
```

流程：

1. 将用户输入、短期记忆摘要、知识库 top-k 片段传给 LLM。
2. 要求 LLM 返回 JSON 草稿。
3. 使用 Pydantic 校验 JSON。
4. JSON 非法时，在真实 LLM 模式下重试一次。
5. 重试后仍非法则记录 `model_invalid_json`，回退到规则链路。
6. API 超时默认 180 秒，最多重试 3 次；两个值均由 `config/llm_config.json` 配置。
7. 真实 LLM 默认使用流式输出，但最终 JSON 仍必须完整解析和校验后才能返回。
8. 对 LLM 给出的意图、动作参数和安全建议进行本地规则复核。

约束：

- LLM 不能直接调用执行工具。
- LLM 不能覆盖本地安全策略。
- LLM 不能在没有来源时编造知识库证据。

## 8. 结构化输出和工具注入策略

结构化 JSON 不能只靠 prompt 保证，最终响应必须由本地代码统一组装和校验。

保证策略：

- system prompt 要求 LLM 只输出严格 JSON，不允许 Markdown、代码块或额外解释。
- LLM 输出先经过 JSON parser，解析失败则记录 `model_invalid_json`。
- 解析后的对象必须通过 Pydantic schema 校验。
- 校验通过后仍要经过本地安全规则复核。
- `sources` 只能来自 `search_knowledge` 返回的 source_id，不能由 LLM 编造。
- 最终返回给用户的 JSON 由 `AgentResponse` 模型序列化生成，不直接打印 LLM 原文。
- LLM 非法 JSON、缺字段、字段类型错误、枚举值错误时，回退到规则链路生成合法 JSON。

system prompt 应包含：

```text
你是制造业设备安全操作 Agent 的推理模块。
你的输出必须是严格 JSON，不允许 Markdown，不允许代码块，不允许额外解释文字。

你只能基于提供的 knowledge_context、tool_results、conversation_memory 和 user_input 回答。
不得编造知识库来源，不得编造设备状态，不得声称已经执行真实设备动作。

你的输出必须包含这些字段：
answer, intent, sources, confidence, safety_level, need_human_approval, tool_calls, final_action, error。

intent 只能是：
qa, status_check, device_action, unsafe_action, unknown。

safety_level 只能是：
L0, L1, L2。

工具规则：
- 你只能提出 tool_calls 草案，不能直接执行工具。
- 是否调用工具由本地 runner、安全策略和人工审批决定。
- 涉及设备动作前必须先做安全等级判断。
- 工具失败或资料不足时不能编造结果。

安全规则：
- 只读问答或状态查询为 L0。
- 低风险动作最多为 L1，且只能建议 dry-run。
- 高风险动作、参数异常、危险词、不确定输入、无资料来源、工具失败必须为 L2。
- L2 必须 need_human_approval=true。
- 涉及真实设备动作时，不得直接执行，只能输出建议或 dry-run 计划。
- sources 只能使用输入中提供的 source_id。
- 如果没有可靠资料，answer 要明确说明无法确认，不能猜测。
- 如果用户输入为空或无法判断，intent=unknown，safety_level=L2。
- 如果不确定，选择更高风险等级。
```

LLM 获取工具说明采用三层注入：

1. system prompt 注入工具边界和安全原则。
2. 每轮 LLM 输入注入结构化 `available_tools` 和 `tool_results`。
3. 代码层用工具白名单、Pydantic schema、`safety.py` 和 `approval.py` 决定是否真正执行。

每轮工具上下文示例：

```json
{
  "available_tools": [
    {
      "name": "search_knowledge",
      "description": "Search local equipment knowledge base.",
      "input_schema": {
        "query": "string"
      }
    },
    {
      "name": "get_device_status",
      "description": "Get mocked device status.",
      "input_schema": {}
    },
    {
      "name": "execute_device_command",
      "description": "Dry-run a validated device command.",
      "input_schema": {
        "command": "object",
        "dry_run": "boolean"
      }
    }
  ],
  "tool_results": []
}
```

工具调用权限链路：

```text
LLM 生成 tool_calls 草案
  -> Pydantic 校验 tool_calls
  -> runner 检查工具名是否在白名单
  -> safety.py 判断 L0/L1/L2
  -> approval.py 处理 yes/no/allyes
  -> tools.py 执行 mock 工具
  -> tool result 写入 trace，并注入后续 LLM 或最终响应
```

关键原则：

- LLM 不拥有工具执行权，只拥有工具调用建议权。
- LLM 不知道的工具不能调用。
- 本地未注册的工具必须记录为 failed 或 skipped。
- `execute_device_command` 永远默认 `dry_run=true`。
- `L2` 工具即使审批通过，也不能真实执行危险动作。

### 用户提示词增强机制

真实 LLM 模式建议引入轻量提示词增强机制，用于提高意图识别、参数抽取和知识库检索准确性。

提示词增强不是让 LLM 自由改写用户意图，而是由本地代码把原始输入补充为结构化上下文。

增强输入必须同时保留：

- `raw_user_input`：用户原始输入，禁止覆盖。
- `normalized_query`：规范化后的检索 query。
- `extracted_entities`：错误码、坐标、速度、力度、设备名、动作词。
- `risk_hints`：危险词、越界参数、缺失参数、冲突信息。
- `intent_candidates`：本地规则给出的候选意图。
- `knowledge_context`：知识库 top-k 片段和 source_id。
- `conversation_memory`：短期记忆摘要。
- `available_tools`：本轮可用工具说明。

增强策略：

1. 对用户输入做全角半角、空格、标点和大小写规范化。
2. 抽取强特征，例如 `E42`、`x=100`、`最大速度`、`急停`、`抓取`。
3. 做有限同义词扩展，例如机械臂/机械手、抓取/夹取、急停/E-stop。
4. 先检索知识库，再把命中的片段注入 LLM。
5. 把本地安全规则的风险提示显式注入 LLM。
6. 如果增强结果和原始输入冲突，以原始输入和本地安全规则为准。

安全边界：

- 提示词增强不能删除危险词。
- 提示词增强不能把越界参数改成安全参数。
- 提示词增强不能补造用户没有提供的坐标、速度或力度。
- 提示词增强不能生成新的知识库来源。
- 提示词增强结果必须写入 trace，方便复盘。

注入给真实 LLM 的用户上下文建议使用 JSON：

```json
{
  "raw_user_input": "以最大速度直接移动到 x=9999, y=9999, z=9999。",
  "normalized_query": "最大速度 移动 x=9999 y=9999 z=9999",
  "extracted_entities": {
    "action": "move",
    "coordinates": {
      "x": 9999,
      "y": 9999,
      "z": 9999
    },
    "speed": "max"
  },
  "risk_hints": [
    "contains_max_speed",
    "coordinate_out_of_range"
  ],
  "intent_candidates": [
    "unsafe_action"
  ],
  "knowledge_context": [
    {
      "source_id": "forbidden_actions.md#out-of-range-motion",
      "text": "坐标越界或最大速度移动需要人工审批，不能直接执行。"
    }
  ]
}
```

结论：真实 LLM 下应引入提示词增强机制，但它只增强上下文质量，不参与最终安全授权。

## 9. 知识库搜索策略

知识库使用 Markdown 文件，每个文件按标题和段落切分为 chunk。

推荐文件：

- `device_overview.md`：设备基础说明。
- `safety_rules.md`：安全操作规则。
- `troubleshooting.md`：常见故障和排查方法。
- `forbidden_actions.md`：禁止执行或需要人工确认的操作。

每个 chunk 建议包含：

```text
source_id: troubleshooting.md#E42
category: troubleshooting
risk_tags: [readonly, error-code]
text: ...
keywords: [E42, 夹爪, 传感器, 气压]
```

检索流程：

1. 规范化 query：大小写、全角半角、空格、标点。
2. 提取强特征：错误码如 `E42`、坐标、速度词、动作词、安全词。
3. 做同义词扩展：机械臂/机械手/robot arm，抓取/夹取/取件，急停/E-stop。
4. 对所有 chunk 进行 lexical scoring。
5. 精确错误码、禁止动作、安全规则命中时加权。
6. 返回 top-k，默认 `top_k=3`。
7. 低于阈值则返回空结果，不强行拼接不相关资料。

推荐评分：

- 错误码精确匹配：+5
- 标题或 source_id 匹配：+3
- 安全/禁止类关键词匹配：+3
- 普通关键词匹配：+1
- 同义词匹配：+0.5

资料补充流程：

1. 新增 Markdown 文件或追加新标题段落。
2. 每个新片段必须有清晰标题，便于生成稳定 `source_id`。
3. 启动时执行知识库加载校验：空文件、重复标题、缺少正文、重复 source_id 应给出 warning。
4. 后续可增加 `python -m agent.kb validate`，专门检查知识库质量。
5. 若资料冲突，优先级为：禁止动作 > 安全规则 > 故障排查 > 设备说明。
6. 不确定或冲突时，最终输出必须进入 `L2`，并提示人工确认。

不建议首版使用向量库：

- 题目规模小，lexical search 更透明、更容易测试。
- 面试重点是数据流、安全判断和 fallback，不是 RAG 技术堆叠。
- 后续可扩展 BM25 或向量检索，但不应影响首版可运行性。

## 10. 意图识别规则

初版采用“LLM 候选 + 本地规则兜底”的方式。

- `qa`：包含“是什么”“说明”“规则”“怎么排查”等知识问答表达。
- `status_check`：包含“现在可以”“状态”“是否在线”“能否执行”等状态查询表达。
- `device_action`：包含“移动”“抓取”“复位”“启动”“停止”等动作表达，且参数未明显危险。
- `unsafe_action`：包含“最大速度”“绕过安全”“强制执行”“禁用保护”等危险表达，或参数明显越界。
- `unknown`：空输入、无法归类或信息不足。

冲突处理：

- LLM 判断为安全，但本地规则发现危险词或越界参数，以本地规则为准。
- 知识库无来源时，不能把纯 LLM 回答当成事实答案。
- 多意图输入时，以最高风险意图为准。

## 11. 安全和人工审批策略

安全等级：

- `L0`：只读问答或状态查询，不涉及设备动作。
- `L1`：低风险动作，只允许 dry-run，并记录日志。
- `L2`：危险动作、越界参数、工具异常、检索失败、不确定输入，必须人工审批；即使审批通过也不能真实执行危险动作。

安全规则配置写入 `config/safety_rules.json`，坐标、速度、力度、急停、维护模式、离线状态等阈值均允许配置。

配置示例：

```json
{
  "coordinate_limits": {
    "x": [-1000, 1000],
    "y": [-1000, 1000],
    "z": [-1000, 1000]
  },
  "allowed_speeds": ["low", "normal", "safe"],
  "max_force_newton": 50,
  "block_when_emergency_stop": true,
  "block_when_offline": true,
  "block_when_maintenance": true,
  "danger_keywords": ["最大速度", "强制执行", "绕过安全", "禁用保护"],
  "approval": {
    "l1_requires_approval": true,
    "l2_requires_approval": true,
    "allyes_scope": "session_global_l2"
  }
}
```

配置缺失或格式错误时，系统必须使用保守默认值，并在 trace 中记录 warning。

初版参数边界建议：

- 坐标范围：`x/y/z` 均在 `-1000` 到 `1000` 之间。
- 速度范围：只接受 `low`、`normal`、`safe`，拒绝“最大速度”或超过配置上限的数值。
- 力度范围：只接受安全配置内的值；未提供力度时默认不执行真实动作，只 dry-run。

审批触发：

- `L0` 不触发审批。
- `L1` 涉及工具或动作命令时触发审批。
- `L2` 一律触发审批，但审批通过也不允许真实执行危险动作，只允许继续 dry-run、诊断、解释、日志记录或生成安全建议。

审批选项：

- `yes`：仅批准本次工具或命令。
- `no`：拒绝本次工具或命令，工具调用记录为 `skipped`。
- `allyes`：当前会话级全局自动放行，当前对话内所有后续 L2 调用跳过弹窗；会话结束自动失效，不持久保存权限。

审批缓存：

```text
approval_key = safety_level + tool_name + normalized_command + risk_reason
```

注意：

- `yes` 只放行当前这一条 L2 调用，下一次 L2 依旧弹窗确认。
- `allyes` 只在当前 CLI 会话有效，不写入长期文件。
- `allyes` 不能降低风险等级，也不能把 `L2` 改成 `L1`。
- `L2` 的最终 JSON 仍必须保留 `need_human_approval=true`。
- 被拒绝的工具调用必须进入 trace，方便复盘。
- L2 审批通过后的允许范围仅限 dry-run、诊断、解释、日志记录和安全建议。
- L2 审批通过后仍禁止真实设备执行、绕过参数边界、关闭安全保护或持久化权限。

## 12. ESC 强制打断

单轮命令和交互式 CLI 会话都必须支持 ESC 打断。

打断范围：

- LLM 流式生成中。
- 等待人工审批中。
- 工具调用中。
- Agent 主流程中。

实现策略：

- 为每次用户请求创建 `CancellationToken` 或 `threading.Event`。
- CLI 启动一个键盘监听任务，捕获 ESC 后设置取消标记。
- LLM streaming、工具调用和主流程关键阶段都定期检查取消标记。
- 被打断后输出合法 JSON，而不是直接崩溃。

被打断时的响应建议：

```json
{
  "intent": "unknown",
  "safety_level": "L2",
  "need_human_approval": true,
  "tool_calls": [],
  "final_action": "cancelled_by_user",
  "error": {
    "type": "cancelled",
    "message": "User interrupted the run with ESC."
  }
}
```

实现备注：

- Windows 可用 `msvcrt` 监听 ESC。
- macOS/Linux 可用 `termios` + `select`，或将 ESC 支持集中封装在 `interrupt.py`。
- 单元测试不真实按键，直接设置 cancellation flag 验证流程。

## 13. 短期工作记忆

Agent 必须有会话级短期工作记忆。

采用“结构化工作记忆 + 最近 10 轮原文 + 历史摘要”的混合方案。

记忆配置写入 `config/memory_config.json`：

```json
{
  "max_memory_tokens": 64000,
  "compress_trigger_tokens": 48000,
  "keep_last_turns": 10,
  "summary_max_tokens": 4000,
  "hard_trim_tokens": 80000,
  "token_counter": "deepseek_tokenizer_or_tiktoken_fallback",
  "compression_mode": "mock_template_first",
  "llm_compression_enabled": true
}
```

记忆内容：

- 最近用户输入。
- 最近 Agent 输出摘要。
- 最近工具调用结果。
- 当前设备状态快照。
- 已压缩的历史摘要。
- pinned safety state：危险词、越界参数、急停状态、离线状态、L2 审批、拒绝过的动作、工具失败结果。
- approval state：`yes/no/allyes` 审批状态；该状态由代码控制，不允许 LLM 摘要决定权限。

token 计数：

- 使用 `tiktoken` 按模型编码计算 token。
- DeepSeek tokenizer 可用时优先使用；不可用时使用 `tiktoken` 或近似计数兜底。
- `max_memory_tokens` 默认 64000。
- `compress_trigger_tokens` 默认 48000。
- `keep_last_turns` 默认 10。
- `hard_trim_tokens` 默认 80000。
- 超过 `compress_trigger_tokens` 时触发压缩。

压缩策略：

1. 保留最近 N 轮原文。
2. 将更早历史压缩为 summary。
3. summary 必须保留安全相关事实：设备状态、错误码、已拒绝/已批准命令、风险原因。
4. mock 模式使用确定性摘要模板。
5. real 模式可使用 LLM 压缩，但压缩结果仍要经过长度和字段校验。
6. 压缩后 summary 应低于 `summary_max_tokens`，默认 4000 token；否则继续删除普通叙述，但不能删除安全事实。
7. 超过 `hard_trim_tokens` 时执行硬裁剪兜底，只裁剪最旧的普通对话文本，不裁剪 pinned safety state。

压缩输出使用结构化 JSON：

```json
{
  "summary": "当前会话目标和已完成事项的简短摘要",
  "device_state": {},
  "active_error_codes": [],
  "active_risks": [],
  "human_decisions": [],
  "tool_results": [],
  "open_questions": [],
  "knowledge_sources_used": [],
  "last_updated_trace_id": ""
}
```

上下文优先级：

```text
当前用户输入
  > 当前工具结果
  > 安全配置
  > 知识库检索
  > 工作记忆摘要
```

冲突处理：

- 如果工作记忆和当前用户输入冲突，以当前用户输入为准。
- 如果工作记忆和当前工具结果冲突，以当前工具结果为准。
- 如果工作记忆和安全配置冲突，以安全配置为准。
- 冲突必须写入 trace，必要时升级为 `L2`。

边界：

- 审批缓存属于会话控制状态，不依赖 LLM 记忆。
- 记忆不作为知识库来源，不能写入 `sources`。
- trace 需要记录压缩前后 token 数。
- 工作记忆只在当前 CLI 会话内有效，首版不做跨会话长期记忆。

## 14. Mock 工具设计

### search_knowledge(query)

输入：用户问题字符串。

输出：

```json
{
  "matches": [
    {
      "source": "safety_rules.md#speed-limit",
      "text": "片段内容",
      "score": 0.82
    }
  ]
}
```

行为：

- 使用第 9 节的 lexical search。
- 找不到时返回空数组。
- 支持测试注入超时或异常。

### get_device_status()

输出：

```json
{
  "status": "online",
  "mode": "idle",
  "emergency_stop": false,
  "last_error": null
}
```

行为：

- 默认返回在线、空闲、未急停。
- 支持通过环境变量或测试参数模拟 `offline`、`error`、`maintenance`。
- 若设备离线、急停或维护中，动作类请求升级为 `L2`。

### execute_device_command(command, dry_run=True)

输入：规范化后的动作命令。

输出：

```json
{
  "dry_run": true,
  "accepted": true,
  "message": "Command validated but not executed."
}
```

行为：

- 默认 dry-run。
- `dry_run=False` 初版直接拒绝。
- `L2` 场景即使人工确认，也只能 dry-run 或 skipped，不能真实执行危险动作。

## 15. 是否引入 LangGraph

结论：首版不把 LangGraph 作为必需依赖；基础实现用显式 Python pipeline 完成。基础功能测试全通过后，可选增加 LangGraph 编排版本作为加分项。

原因：

- 题目强调 24-48 小时内完成，且“代码跑不起来会严重扣分”。
- 必做项可以用 Pydantic、显式 pipeline、approval gate 和 trace 清晰实现。
- LangGraph 的 human-in-the-loop、checkpoint、stateful graph 与本项目需求匹配，但会增加依赖、概念和调试成本。
- 面试评分更关注是否能运行、数据流是否清楚、失败处理是否真实；过早引入框架可能稀释核心交付。

可选增强方案：

- 增加 `--engine pipeline|langgraph`，默认 `pipeline`。
- LangGraph 节点映射为：`load_memory -> retrieve -> infer_intent -> safety_check -> approval_gate -> tool_call -> finalize -> save_memory`。
- 使用 checkpointer 保存会话状态。
- 使用 interrupt 机制承接 L1/L2 人工审批。
- README 明确说明 LangGraph 是可选增强，不影响 mock 模式主流程。

若时间紧，只实现 pipeline，不引入 LangGraph。

## 16. 异常和失败处理

必须覆盖的失败场景：

- 输入为空：返回 `unknown`、`L2`、需要人工确认。
- 检索不到资料：返回低置信度，不编造来源。
- 工具超时：记录失败工具调用，返回 `L2`。
- 工具异常：记录错误信息，返回 `L2`。
- 模型输出非法 JSON：使用规则 fallback，仍输出合法 JSON。
- 用户输入危险指令：返回 `unsafe_action`、`L2`、跳过真实执行。
- 坐标、速度、力度越界：返回 `unsafe_action`、`L2`、跳过真实执行。
- ESC 打断：返回合法 JSON，错误类型为 `cancelled`。
- 真实 LLM 缺少 `DEEPSEEK_API_KEY`：启动阶段明确报错，不伪装成功。
- 真实 LLM API 超时：按配置最多重试 3 次；仍失败则记录 `model_timeout`，回退到 mock/规则链路并输出合法 JSON。
- 真实 LLM 输出非法 JSON：按配置重试一次；仍失败则记录 `model_invalid_json`，回退到规则链路。
- 提示词增强异常：记录 `prompt_enhancement_error`，继续使用原始输入和规则链路。
- `config/safety_rules.json` 缺失或非法：使用保守默认值，记录 `config_warning`。
- `config/llm_config.json` 缺失或非法：使用默认 DeepSeek 配置，记录 `config_warning`。

错误字段建议：

- 无错误：`error=null`
- 可恢复失败：`error={"type":"tool_error","message":"..."}`
- 输入问题：`error={"type":"invalid_input","message":"..."}`
- 安全拦截：`error={"type":"safety_blocked","message":"..."}`
- 用户打断：`error={"type":"cancelled","message":"..."}`
- 模型输出异常：`error={"type":"model_invalid_json","message":"..."}`
- 模型超时：`error={"type":"model_timeout","message":"..."}`
- 提示词增强异常：`error={"type":"prompt_enhancement_error","message":"..."}`
- 配置警告：`error={"type":"config_warning","message":"..."}`

## 17. 日志和 trace

本项目必须区分“运行日志”和“开发日志”，避免后续实现时混用。

边界约定：

- `logs/`：程序运行日志目录，由 Agent 运行时写入，例如 `logs/agent.log`；属于运行产物，默认进入 `.gitignore`，不写开发过程说明。
- `runs/`：单次 Agent 调用 trace 目录，由 Agent 运行时写入，例如 `runs/<trace_id>.json`；属于运行证据，可用于本地调试和提交样例，但不替代 `docs/03-tests/` 测试报告。
- `docs/`：开发日志和项目文档目录，由开发者或 AI 在阶段完成、测试、调试、审查、设计决策时维护；必须使用 Markdown + YAML frontmatter。
- `docs/03-tests/`：测试报告目录，用于记录测试命令、样例数量、通过率、关键运行结果摘要，以及引用哪些 `runs/<trace_id>.json` 作为证据。

每次运行必须记录：

- `trace_id`
- 原始输入
- LLM 模式和模型名
- 真实 LLM 超时、重试、流式输出配置
- 安全规则配置版本或加载结果
- 短期记忆 token 数和压缩记录
- 原始用户输入和提示词增强后的结构化上下文
- 检索 query、检索结果和评分
- 工具调用输入输出
- 每轮注入给 LLM 的 `available_tools` 和 `tool_results`
- 审批请求、用户选择和审批缓存命中情况
- LLM 原始输出或 mock 输出
- 最终 JSON
- 错误信息
- 是否被 ESC 打断

建议保存：

```text
runs/<trace_id>.json
logs/agent.log
```

`runs/<trace_id>.json` 可作为提交时的运行结果证据；正式测试结论必须另写入 `docs/03-tests/YYYY-MM-DD.md`。

运行日志不得写入：

- 设计决策、任务计划、代码改动说明。
- 测试结论和通过率总结。
- AI 协作过程总结。

开发日志不得写入：

- 大量原始 LLM 输出全文。
- 未脱敏 API key、环境变量或本机路径敏感信息。
- 每次运行的完整低层 trace；只保留摘要和必要证据路径。

## 18. 测试样例

正式测试至少覆盖题目样例：

1. `机械臂现在可以执行抓取动作吗？`
   - 预期：`status_check`，调用 `get_device_status`，不执行动作。
2. `设备报错 E42，应该怎么排查？`
   - 预期：`qa`，命中故障排查知识，输出来源。
3. `把机械臂移动到 x=100, y=50, z=20，并抓取零件。`
   - 预期：`device_action`，`L1`，触发审批，批准后 dry-run。
4. `以最大速度直接移动到 x=9999, y=9999, z=9999。`
   - 预期：`unsafe_action`，`L2`，需要人工审批，跳过真实执行。
5. `知识库里没有这台设备的说明时，系统应该怎么返回？`
   - 预期：检索为空时不编造，低置信度，给出 fallback。

建议补充：

6. 空字符串输入。
   - 预期：`unknown`，`L2`。
7. 模拟 `search_knowledge` 超时。
   - 预期：工具调用 failed，最终响应仍是合法 JSON。
8. 模拟 LLM 返回非法 JSON。
   - 预期：fallback 到规则链路，最终响应仍通过 schema 校验。
9. L1 审批选择 `no`。
   - 预期：工具 skipped，final_action 说明用户拒绝。
10. L1 审批选择 `allyes` 后重复同一命令。
   - 预期：第二次命中审批缓存，不再询问。
11. ESC 打断 LLM 或工具调用。
   - 预期：返回 `cancelled` 错误，trace 记录 interrupt。
12. 多轮会话超过 token 限制。
  - 预期：超过 48000 token 后触发记忆压缩，保留最近 10 轮和安全摘要。
13. LLM 生成未注册工具名。
   - 预期：工具不执行，记录 failed 或 skipped，最终 JSON 合法。
14. LLM 编造不存在的 source_id。
   - 预期：sources 被本地过滤，必要时降级为 L2 fallback。
15. 提示词增强遇到危险输入。
   - 预期：增强结果保留危险词和越界参数，不降低风险等级。
16. 真实 LLM API 超时。
   - 预期：按配置重试 3 次后记录 `model_timeout`，回退到规则链路，最终 JSON 合法。
17. 提示词增强异常。
   - 预期：记录 `prompt_enhancement_error`，继续使用原始输入完成 fallback。
18. L2 审批选择 `yes` 后再次触发 L2。
   - 预期：第一次放行当前 dry-run/诊断流程，第二次仍弹窗确认。
19. L2 审批选择 `allyes` 后再次触发 L2。
   - 预期：当前会话后续 L2 跳过弹窗，但仍保持 L2 和 dry-run 限制。
20. 安全配置缺失或非法。
   - 预期：使用保守默认值，记录 `config_warning`，最终 JSON 合法。
21. 真实 LLM 非法 JSON。
   - 预期：重试一次；仍非法则 fallback 到规则链路。

## 19. README 交付说明要点

README 至少说明：

- 项目目标和功能边界。
- CLI 启动方式。
- mock LLM 和真实 LLM 两种模式。
- DeepSeek V4 Pro 作为真实 LLM 首选模型的配置方式。
- `.env.example` 配置方式。
- `config/safety_rules.json` 和 `config/llm_config.json` 配置方式。
- `config/memory_config.json` 配置方式。
- 如何运行测试。
- mock/dry-run 模式说明。
- 模块结构。
- 知识库结构和补充方式。
- 安全策略和人工审批逻辑。
- 结构化 JSON 保证策略。
- system prompt 的核心内容。
- 真实 LLM 下的用户提示词增强机制和安全边界。
- 工具说明如何注入给 LLM，以及 LLM 只有建议权没有执行权。
- ESC 打断方式。
- 短期记忆和 token 压缩策略。
- 失败处理策略。
- 哪些地方使用了 AI coding。
- 到岗信息确认占位。

提交前交付清单：

- README 写清启动方式、功能边界、技术选型、模块结构和 AI coding 使用说明。
- `.env.example` 只保留变量名和示例值，不提交真实 API key。
- 至少保存 5 条样例运行日志或 `runs/<trace_id>.json`。
- 在 `docs/03-tests/YYYY-MM-DD.md` 记录测试命令、样例数量、通过结果和失败说明。
- README 中保留到岗信息填写位：最早到宁波线下日期、最晚实习结束日期、是否每周 5 天线下，由用户自行填写。

## 20. 二面题目覆盖性审查

| 题目要求 | 方案覆盖情况 |
| --- | --- |
| 能根据设备知识库回答问题 | 覆盖：`knowledge/*.md` + lexical search + sources |
| 能识别用户意图 | 覆盖：LLM 候选 + 本地规则兜底 |
| 涉及设备动作必须安全等级判断 | 覆盖：`safety.py` + L0/L1/L2 |
| 工具失败、检索不到、危险输入不能编造 | 覆盖：失败处理和 L2 fallback |
| CLI 或 FastAPI | 覆盖：明确 CLI-only |
| 内置小型知识库 | 覆盖：四类 Markdown 知识文件 |
| 输入问题或操作指令 | 覆盖：单轮 CLI + REPL |
| 输出结构化 JSON | 覆盖：Pydantic schema |
| mock 工具 3 个 | 覆盖：search/status/execute |
| 检索不到资料 | 覆盖：低置信度 + L2 fallback |
| 工具超时 | 覆盖：测试注入 + tool failed |
| 工具异常 | 覆盖：tool_error |
| 模型非法 JSON | 覆盖：fallback 到规则链路 |
| 危险指令 | 覆盖：unsafe_action + L2 |
| 参数越界 | 覆盖：坐标、速度、力度边界 |
| 输入为空或意图未知 | 覆盖：unknown + L2 |
| 日志和 trace | 覆盖：console/logs/runs |
| 至少 5 条测试样例 | 覆盖：21 条测试建议 |
| mock/dry-run 模式 | 覆盖：默认 mock + dry-run |
| Pydantic/JSON Schema 加分 | 覆盖 |
| mock LLM 和真实 LLM 加分 | 覆盖：真实 LLM 首选 DeepSeek V4 Pro |
| runs/<trace_id>.json 加分 | 覆盖 |
| safety_rules.json 配置加分 | 覆盖：安全规则配置化 |
| FastAPI 加分 | 不做，因当前明确 CLI-only |

结论：v2 方案已覆盖题目必做项，并覆盖多个可选加分项。唯一明确不做的是 FastAPI，因为当前项目目标已确定为 CLI-only。
