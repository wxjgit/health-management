# Chat Graph 模块化实现 v2.0

日期：2026-10-31
状态：已实施，后端完整回归验证通过
适用范围：后端 `chat_graph`、聊天状态、追问恢复、聊天数据操作与回复组装

## 0. 实施结果

2026-10-31 已完成直接替换式重构：

- `Engine` 不再通过 Chat Mixin 多重继承组装业务能力；
- 新增 `workflows/chat/`，安全、理解、追问、操作、回复和 Graph 组装分离；
- Chat 使用独立 `ChatState`，Plan 与 Analysis 使用各自状态；
- 旧 `pending_*` checkpoint 字段已由 `ClarificationTask` 替换；
- 保存、纠错、查询和长期事实写入已收敛为 `OperationCommand` / `OperationResult`；
- 原单体 `respond()` 已拆成查询、记录、事实、建议和最终组装节点；
- 旧 `chat_flow.py`、`chat_parsing.py`、`chat_response.py` 和 `workflows/policy.py` 已移除；
- 可观测性有限节点词表和相关测试已同步；
- 新增 `test_chat_graph_v2.py` 固定模块节点、状态契约和命令回执。
- Python 编译检查通过，后端完整回归 `660 passed`。

实现中保留一项有意调整：`ChatState` 的运行通道保持顶层 TypedDict 字段，而没有使用多层嵌套字典。原因是 LangGraph 默认会整体替换嵌套字典，多个模块更新同一分区时容易丢字段。模块边界改由独立 ChatState、严格 Pydantic 契约和节点权限控制实现。

默认本地数据目录下没有发现 `health.db` 或 `checkpoints.db`，因此本次没有实际删除文件；空目录初始化已经由测试覆盖。

## 1. 文档目的

本文定义 `chat_graph` 从当前共享状态、Mixin 组合和集中式回复节点，重构为边界清晰的模块化工作流的实施方案。

项目尚未上线且没有真实用户，因此本次采用直接替换方案：

- 不保留旧版 `chat_graph`；
- 不支持旧 checkpoint 恢复；
- 不增加 Graph 双版本路由；
- 不编写旧 `State` 到新 `ChatState` 的数据迁移；
- 开发时允许删除本地 `health.db` 和 checkpoint 数据后重新初始化。

本次重构不是把代码机械地拆成更多文件，而是让每个模块都能明确回答：

1. 它可以读取什么；
2. 它可以输出什么；
3. 它需要什么权限；
4. 它是否允许产生业务副作用；
5. 失败、重试或恢复时会发生什么。

## 2. 开发结论

采用“业务子流程模块化 + 统一状态契约 + 命令式数据操作 + 外层可靠运行时”的结构。

核心原则保持不变：

> 模型负责提出候选计划和生成受限表达，本地代码负责安全授权、数据范围、业务校验和最终写入。

目标结构分为六个职责域：

| 职责域 | 作用 | 是否属于 `chat_graph` |
| --- | --- | --- |
| 安全 | 判断输入能否继续处理，签发受限能力，生成风险回复 | 是，共享底层安全策略 |
| 理解 | 识别记录、纠错、查询、建议或长期事实，形成处理计划 | 是 |
| 追问 | 管理缺失信息、暂停、恢复和多轮补充 | 是 |
| 操作 | 执行保存、修改、查询和事实写入 | 是 |
| 回复 | 生成回执、建议、卡片、证据和最终回复 | 是 |
| 运行 | checkpoint、幂等、锁、取消、过期和删除写回保护 | 否，位于 Graph 外层 |

## 3. 当前实现与主要问题

### 3.1 当前主流程

当前 `chat_graph` 的主路径为：

```text
START
  → safety_gate
  → classify_safety（必要时）
  → parse_initial / patch_pending
  → normalize_extraction
  → validate_drafts
  → authorize_operation
  → prepare_nutrition
  → save_ready
  → prepare_clarification / persist / fact / respond
  → END
```

追问通过 `interrupt()` 暂停，恢复后重新进入 `safety_gate`。运行时使用 SQLite checkpoint、请求指纹、流程锁和会话锁处理恢复与幂等。

### 3.2 当前代码边界

| 文件 | 当前职责 |
| --- | --- |
| `workflows/chat_flow.py` | Graph 组装、追问、保存、长期事实写入 |
| `workflows/chat_parsing.py` | 初始解析、追问补丁、规范化、草稿校验、营养准备 |
| `workflows/chat_response.py` | 查询、建议、回执、卡片、额外动作和最终组装 |
| `workflows/policy.py` | 模型处理计划的本地审批、引用解析和数据视图限制 |
| `workflows/safety.py` | 三张 Graph 共享的安全分类、能力检查和安全回复 |
| `workflows/state.py` | chat、plan、analysis 共用的宽松 `State` |
| `workflows/runtime.py` | Graph 调用、checkpoint、请求重放和恢复 |
| `workflows/concurrency.py` | 执行代次、流程锁、会话锁和模型 I/O 边界 |

### 3.3 需要解决的问题

1. `State` 同时承载 chat、plan、analysis 字段，节点依赖关系不直观。
2. Mixin 通过同一个 `Engine` 隐式共享 `repo`、`parser`、`harness` 和大量方法，模块边界弱。
3. `respond()` 同时负责查询、模型建议、回执、卡片、事实写入和最终组装，读操作与副作用混杂。
4. `extra_actions` 在回复阶段执行，处理计划和执行计划没有形成清晰边界。
5. 追问依赖多个 `pending_*` 字段，只适合单个当前问题，扩展多事件追问成本较高。
6. 安全检查、处理计划审批和数据访问校验是正确的，但入口分散，新增节点时容易遗漏。
7. 节点没有统一声明输入、输出、能力要求、模型调用和数据库副作用。

## 4. 目标与非目标

### 4.1 目标

1. 将 Chat 专属状态与 Plan、Analysis 状态分离。
2. 将安全、理解、追问、操作和回复拆成独立模块。
3. 让模型只生成结构化候选，不直接执行数据库操作。
4. 将所有聊天数据读写统一收敛到操作模块。
5. 使用显式追问任务替换分散的 `pending_*` 字段。
6. 保留现有安全复核、最小数据视图、来源证据和本地兜底能力。
7. 保留请求幂等、checkpoint 恢复、并发保护、取消和删除写回屏障。
8. 通过路径测试和节点契约测试保证重构后业务语义正确。

### 4.2 非目标

1. 不重构 `plan_graph` 和 `analysis_graph` 的业务逻辑。
2. 不引入多 Agent 自主协作。
3. 不让模型直接选择数据库 ID 或执行任意工具。
4. 不引入 Redis、Celery、消息队列或多 Worker 架构。
5. 不改变现有聊天 API 的请求与响应格式，除非另有明确需求。
6. 不兼容旧 checkpoint、旧本地业务数据或正在等待回答的旧流程。
7. 不以减少安全复核次数为优化目标。

## 5. 目标架构

### 5.1 总体流程

```text
┌──────────────┐
│ Runtime 外层 │  请求幂等、锁、checkpoint、恢复、取消、过期
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  安全子流程  │  本地检查 → 独立分类 → 能力授权 / 安全回复
└──────┬───────┘
       │ authorized
       ▼
┌──────────────┐
│  理解子流程  │  处理计划 → 本地审批 → 草稿规范化与校验
└──────┬───────┘
       │
       ├──────── 缺少信息 ────────┐
       │                           ▼
       │                    ┌──────────────┐
       │                    │  追问子流程  │ interrupt → resume → 安全复核
       │                    └──────┬───────┘
       │                           │
       └───────────────────────────┘
       │ ready
       ▼
┌──────────────┐
│  操作子流程  │  Save / Correct / Query / SaveFact
└──────┬───────┘
       │ operation results
       ▼
┌──────────────┐
│  回复子流程  │  回执 / 查询结果 / 建议 / 卡片 / 证据 / 组装
└──────┬───────┘
       │
       ▼
      END
```

### 5.2 建议目录

```text
backend/app/workflows/
  chat/
    __init__.py
    graph.py
    state.py
    contracts.py
    safety_flow.py
    interpretation_flow.py
    clarification_flow.py
    operation_flow.py
    response_flow.py
  safety.py
  runtime.py
  concurrency.py
  planning.py
  state.py
  engine.py
```

说明：

- `workflows/safety.py` 继续保存三张 Graph 共用的安全政策和本地校验，不复制安全规则。
- `chat/safety_flow.py` 只负责 Chat 对共享安全能力的节点封装和路由。
- `chat/graph.py` 统一把服务方法包装为受保护的 LangGraph 节点，不在组装层放业务规则。
- `workflows/state.py` 只保留真正共享的基础字段，并分别定义或导出 `PlanState`、`AnalysisState`。
- Chat 专属字段全部进入 `chat/state.py`。

## 6. ChatState 设计

### 6.1 设计原则

1. State 保存流程事实，不保存可随时重新计算的临时对象。
2. State 中只保存 JSON 可序列化内容，确保 SQLite checkpoint 可写入。
3. 模块只读写自己负责的分区。
4. 节点返回完整的分区更新，避免嵌套字典被 LangGraph 默认覆盖时丢字段。
5. 数据库实体 ID、版本号和操作回执必须显式保存，不能只存在回复文本中。
6. checkpoint 是执行进度，不是永久授权；恢复后仍需重新校验安全与数据版本。

### 6.2 顶层结构

实现使用独立 TypedDict 表达 Chat Graph State，并使用 Pydantic 严格模型校验追问任务、操作命令和操作结果。为避免 LangGraph 默认替换嵌套字典造成丢字段，checkpoint 通道保持顶层字段：

```python
class ChatState(TypedDict, total=False):
    thread_id: str
    conversation_id: str
    processing_mode: str
    input_text: str
    source_message_id: str
    source_text: str
    safety: dict
    extraction: dict
    parsed: dict
    clarification_tasks: list[dict]
    active_clarification_id: str | None
    operation_commands: list[dict]
    operation_results: list[dict]
    response_draft: dict
    process_trace: list[dict]
```

`ClarificationTask`、`OperationCommand` 和 `OperationResult` 在进入节点时使用严格模型重新校验。Plan 与 Analysis 不再携带这些 Chat 专属字段。

### 6.3 输入通道

输入相关字段的语义：

- `input_text` 是当前轮需要处理的文字；
- `original` 是触发追问的原始输入；
- `source_text` 和 `source_message_id` 用于字段证据和数据血缘；
- `selected_id` 绑定用户明确选择的历史记录；
- `rounds` 和 `incremental_answer` 标记追问轮次和受限补丁模式；
- 追问恢复时不能覆盖原始输入证据。

### 6.4 安全通道

安全状态继续使用已有 `safety`、`safety_context`、`safety_fingerprint` 和 `safety_classification` 通道，以便复用三张 Graph 共用的安全政策。

仍保留当前语义：

- 本地安全检查优先；
- 本地无信号时才进行独立模型分类；
- 分类失败进入不确定安全回复，不自动放宽；
- 追问答案、写入、建议和 checkpoint 恢复均重新校验。

### 6.5 理解通道

理解模块继续使用严格的 `ProcessingPlan`、`PlannedAction`、`ExtractionResult` 和 `EventDraft`；checkpoint 保存它们的 JSON 投影。稳定的 `action_id` 在转换为 `OperationCommand` 时分配，主动作使用 `primary`，附加动作使用 `extra:<index>`。

模型只生成候选动作；本地策略负责：

- 检查动作文本是否来自当前输入；
- 解析选中记录或唯一的前序引用；
- 限制查询时间范围和数据视图；
- 禁止查询动作携带写入事件；
- 禁止模型自选数据库记录 ID；
- 将无法唯一定位的动作转换为追问任务；
- 对不合法计划最多重新规划一次。

### 6.6 ClarificationState

```python
class ClarificationTask(StrictModel):
    task_id: str
    action_id: str
    event_index: int | None = None
    item_index: int | None = None
    requested_fields: list[str]
    question: str
    required: bool
    attempts: int = 0
    status: Literal["pending", "answered", "skipped", "cancelled"]

```

队列保存在 `clarification_tasks`，当前任务 ID 保存在 `active_clarification_id`。

规则：

1. 必填任务排在可选任务之前。
2. 每次只允许一个 active task 进入 `interrupt()`。
3. 回答只可修改 `requested_fields` 指定的字段。
4. “不知道”可跳过可选任务；必填任务则放弃对应事件。
5. “取消记录”只取消对应 action，不取消已完成的独立 action。
6. 回答中的独立新意图不能通过 patch 直接执行，必须重新进入安全和理解流程。
7. 追问次数必须有上限，达到上限后按必填或可选规则结束该任务。

### 6.7 操作通道

模型计划与数据库执行之间增加命令层：

```python
class OperationCommand(StrictModel):
    operation_id: str
    action_id: str
    kind: Literal["save_event", "correct_event", "query_events", "save_fact"]
    payload: dict
    required_capability: str
    status: Literal["pending", "completed", "skipped", "failed"] = "pending"

class OperationResult(StrictModel):
    operation_id: str
    action_id: str
    kind: str
    entity_refs: list[str] = []
    records: list[dict] = []
    status: str
```

命令和回执分别保存在 `operation_commands`、`operation_results`。

`operation_id` 必须稳定，可由 `thread_id + action_id + kind` 生成。重复执行同一命令时复用已有业务结果，不产生重复记录。

### 6.8 回复通道

各回复节点先生成 `response_draft`，最终由 `compose_response` 统一输出 `reply`、`answer_disposition`、`cards`、`evidence_refs` 和 `process_trace`。

回复模块只消费已审批的 action 和 operation result。除经过独立节点执行的模型建议外，不允许直接访问任意历史数据，也不允许产生数据库写入。

## 7. 模块职责与节点设计

### 7.1 安全模块

已实现节点：

```text
safety_gate
classify_safety
safety_response
authorize_operation
```

职责：

- 构造最小安全上下文；
- 优先执行本地风险判断；
- 必要时执行独立模型分类；
- 生成并维护安全状态通道；
- 在敏感节点前重新校验 fingerprint、policy version 和 capability；
- 对风险或不确定输入生成固定边界回复。

禁止：

- 保存健康记录；
- 查询非安全判断所需的历史正文；
- 生成普通健康建议；
- 将模型分类结果直接当作数据库授权。

### 7.2 理解模块

已实现节点：

```text
parse_initial
patch_pending
normalize_extraction
validate_drafts
prepare_nutrition
prepare_operations
```

职责：

- 调用模型或本地解析器生成 `ProcessingPlan`；
- 通过本地策略审批计划；
- 解析纠错目标、查询窗口和 advice 数据视图；
- 将记录草稿建立字段证据；
- 校验事件范围、时间、单位、类型和必填字段；
- 生成追问任务或操作命令。

`PlanHarness` 已更名为 `ChatPlanPolicy`，避免“harness”掩盖其实际授权职责。`health/advice.py` 已改为从 Chat 策略模块导入固定视图方法；如后续出现第二个调用方，再单独提取 `AdviceViewPolicy`。

### 7.3 追问模块

已实现节点：

```text
prepare_clarification
clarify
```

职责：

- 从队列选择 active task；
- 生成经过边界检查的追问文案；
- 调用 `interrupt()` 保存流程进度；
- 恢复后验证 `interrupt_id` 和回答结构；
- 仅对允许字段应用 grounded patch；
- 将未完成任务重新排队；
- 结束后重新执行安全授权和草稿校验。

追问选项必须来自本地 Repository，例如纠错候选记录；不能把数据库 ID 列表交给模型后让模型选择。

### 7.4 操作模块

已实现节点：

```text
prepare_operations
save_ready
execute_operations
```

职责：

- 对餐食记录准备营养数据；
- 保存完整事件；
- 使用 expected version 执行纠错；
- 按获批范围查询历史；
- 保存经过结构和来源校验的长期事实；
- 返回统一 `OperationResult`。

每个写入节点执行前必须：

1. 调用当前执行代次检查；
2. 重新验证安全 capability；
3. 校验命令来源和 action 状态；
4. 检查幂等 operation ID；
5. 在 Repository 事务中完成业务写入和来源登记；
6. 返回实体 ID 和版本，不返回不可验证的自由文本结果。

部分成功继续保留：已经完整且相互独立的 action 可以先执行，未完成 action 进入追问队列；恢复时已完成命令不得重复执行。

### 7.5 回复模块

已实现节点：

```text
route_response
record_response
query_response
advice_response
fact_response
compose_response
```

职责：

- 将 OperationResult 转成固定事实回执；
- 为查询结果生成可追溯文本；
- 为 advice 构建获批的最小上下文；
- 调用模型生成建议并执行独立内容审核；
- 模型失败或输出不合法时使用同一上下文的本地兜底；
- 组装卡片、证据引用、answer disposition 和 process trace。

禁止：

- 在回复节点保存事实或事件；
- 在回复节点扩大查询范围；
- 根据模型回复反向修改业务数据；
- 将没有证据引用的数据描述为用户历史事实。

### 7.6 运行模块

`runtime.py` 和 `concurrency.py` 继续位于 Graph 外层，保留以下能力：

- `request_id + fingerprint` 请求幂等；
- `thread_id` checkpoint；
- 同一 workflow 串行执行；
- 同一 conversation 的 Chat 请求串行执行；
- 模型 I/O 期间释放 Engine 锁；
- 模型返回后检查实际读取集合和数据版本；
- 删除、取消、过期和 generation 变化后的写回阻断；
- resume request 与 interrupt ID 绑定；
- workflow result、receipt 和助手消息的事务提交。

运行模块不得理解具体聊天意图，也不得自行修改 Chat 业务状态。

## 8. Graph 路由

已实现 Graph 主路径：

```text
START
  → safety_gate
      ├─ risk/uncertain → safety_response → END
      └─ authorized
           → parse_initial / patch_pending
           → normalize_extraction
           → validate_drafts
           → authorize_operation
               ├─ denied → safety_response → END
               └─ approved
           → prepare_nutrition
           → prepare_operations
           → save_ready
               ├─ clarification_pending
               │    → prepare_clarification
               │    → clarify [interrupt]
               │    → safety_gate
               └─ operations_ready
                    → execute_operations
                    → route_response
                        ├─ record_response
                        ├─ query_response
                        ├─ advice_response
                        └─ fact_response
                    → compose_response
                    → END
```

规则：

1. `safety_gate` 是 START 后的唯一入口。
2. 追问恢复必须回到 `safety_gate`，不能直接进入 patch。
3. 所有业务写入只能从 `execute_operations` 分支到达。
4. 所有正常结束路径必须经过 `compose_response`。
5. 安全回复不执行任何未完成命令。
6. 查询和 advice 的历史访问范围必须来自已审批 action。

## 9. 权限与副作用矩阵

| 模块 | 可调用模型 | 可读当前输入 | 可读历史记录 | 可写业务库 | 可 interrupt |
| --- | --- | --- | --- | --- | --- |
| 安全 | 仅安全分类 | 是 | 仅受限安全事实 | 否 | 否 |
| 理解 | 处理计划、纠错、patch | 是 | 仅本地绑定后投影 | 否 | 否 |
| 追问 | 受限表达选择 | 是 | 仅本地候选标签 | 否 | 是 |
| 操作 | 营养估算 | 是 | 按命令范围 | 是 | 否 |
| 回复 | 个性化建议、受限表达 | 是 | 仅获批视图 | 否 | 否 |
| 运行 | 否 | 不解释正文 | checkpoint 所需 | workflow 元数据 | 恢复 interrupt |

任何新增节点都必须先在此矩阵中确定权限。不能因为节点位于已授权分支，就默认继承无限数据访问或写入能力。

## 10. API 与持久化边界

### 10.1 API 保持不变

继续支持现有：

- 创建聊天请求；
- 返回 `completed` 或 `waiting_input`；
- 使用 `thread_id + interrupt_id` 恢复；
- 取消等待中的 workflow；
- 返回 reply、cards、evidence refs、process trace 和 saved record IDs。

内部 State 和节点名称可以重构，对外响应结构不因模块化而变化。

### 10.2 数据库处理

项目未上线，本次不编写旧数据迁移。开始切换新 State 和 Graph 前，允许删除本地运行数据：

```text
health.db
health.db-wal
health.db-shm
checkpoints.db
checkpoints.db-wal
checkpoints.db-shm
```

执行要求：

1. 从实际 Settings 解析 `data_dir`；
2. 将目标解析为绝对路径；
3. 确认目标位于项目允许的数据目录内；
4. 关闭正在运行的 API、Engine 和数据库连接；
5. 只删除上述明确文件，不递归删除整个目录；
6. 重新启动后由数据库初始化和 checkpointer setup 建表；
7. 不删除测试 fixtures、评测样例、文档证据或源码示例。

本地开发数据不作为迁移验收对象。数据库重新初始化成功、Graph 可运行才是本阶段验收目标。

## 11. 实施步骤

### 阶段 A：行为基线

先补充或整理现有路径测试，不改变业务实现。

必须固定的行为：

1. 普通记录成功保存并返回证据；
2. 信息缺失时进入 `waiting_input`；
3. resume 后只补充指定字段；
4. 多事件输入允许完整事件先保存；
5. 纠错使用记录版本，版本变化时拒绝写入；
6. 查询不产生写操作；
7. advice 只使用获批数据视图；
8. 安全分类失败不进入普通建议；
9. 相同 request ID 重试不重复写入；
10. 取消、过期或删除后旧执行不能写回。

交付结果：现有行为测试全部通过，并形成 V2 重构的回归基线。

### 阶段 B：建立 Chat 包和状态契约

1. 创建 `workflows/chat/`；
2. 定义 `ChatState` 和各分区模型；
3. 定义 ChatAction、ClarificationTask、OperationCommand、OperationResult；
4. 将 Graph 节点输入输出统一为这些契约；
5. 将 `Engine` 中 Chat 依赖集中为明确服务对象，减少 Mixin 隐式调用。

交付结果：新状态和契约单元测试通过，尚不切换生产入口。

### 阶段 C：拆分回复模块

1. 将查询回复、建议回复、记录回执、事实回执分开；
2. 将 `extra_actions` 的事实写入移出回复阶段；
3. 建立统一 `compose_response`；
4. 保留现有 cards、evidence refs 和 process trace 结构；
5. 验证模型失败时本地兜底不扩大上下文。

交付结果：回复模块无数据库写副作用。

### 阶段 D：引入命令式操作模块

1. 将已审批 action 转换为 OperationCommand；
2. 统一记录、纠错、查询、事实写入入口；
3. 为写操作建立稳定 operation ID；
4. 保留字段证据、来源登记和 expected version；
5. 将营养准备限制在相关保存命令内；
6. 支持独立 action 的部分成功。

交付结果：业务库只允许由操作模块写入。

### 阶段 E：重做追问队列

1. 使用 ClarificationTask 生成缺失字段任务；
2. 实现必填、可选、跳过、取消和最大轮次；
3. 恢复后验证 active task 与 interrupt ID；
4. patch 只允许修改 requested fields；
5. 新意图重新经过安全和理解流程；
6. 验证多事件、多缺失字段和跨日恢复。

交付结果：删除旧 `pending_*` 状态字段。

### 阶段 F：组装并切换新 Graph

1. 在 `chat/graph.py` 组装完整 StateGraph；
2. `Engine` 改为构建新的 `chat_graph`；
3. `plan_graph`、`analysis_graph` 保持原行为；
4. 更新 `guarded_node()` 的节点能力映射；
5. 更新 observability 节点名称和处理轨迹；
6. 删除旧 `ChatFlowMixin`、`ChatParsingMixin`、`ChatResponseMixin` 及失效代码。

交付结果：代码库只保留一套 Chat Graph 实现。

### 阶段 G：重置数据并完整验证

1. 停止本地服务；
2. 核对并删除获准的数据库和 WAL/SHM 文件；
3. 启动服务完成新库初始化；
4. 运行后端完整测试；
5. 运行真实 Graph 的对话路径测试；
6. 验证小程序聊天、追问、查询和取消交互；
7. 更新 Agent 分析文档和架构说明。

交付结果：新环境从空数据库可完整运行。

## 12. 预计修改文件

### 12.1 新增

```text
backend/app/workflows/chat/__init__.py
backend/app/workflows/chat/graph.py
backend/app/workflows/chat/state.py
backend/app/workflows/chat/contracts.py
backend/app/workflows/chat/safety_flow.py
backend/app/workflows/chat/interpretation_flow.py
backend/app/workflows/chat/clarification_flow.py
backend/app/workflows/chat/operation_flow.py
backend/app/workflows/chat/response_flow.py
backend/app/workflows/chat/nodes.py
```

测试文件可按模块增加：

```text
backend/tests/test_chat_graph_v2.py
backend/tests/test_chat_interpretation.py
backend/tests/test_chat_clarification.py
backend/tests/test_chat_operations.py
backend/tests/test_chat_response.py
```

### 12.2 修改

```text
backend/app/workflows/engine.py
backend/app/workflows/runtime.py
backend/app/workflows/concurrency.py
backend/app/workflows/safety.py
backend/app/workflows/state.py
backend/app/contracts/workflows.py
backend/app/health/advice.py
backend/app/infrastructure/repositories/workflows.py
backend/tests/test_agent.py
backend/tests/test_harness.py
backend/tests/test_safety_routing.py
backend/tests/test_resume_recovery.py
backend/tests/test_workflow_concurrency.py
backend/tests/test_privacy_deletion.py
```

### 12.3 删除或并入新模块

```text
backend/app/workflows/chat_flow.py
backend/app/workflows/chat_parsing.py
backend/app/workflows/chat_response.py
backend/app/workflows/policy.py
```

只有在所有引用和测试已迁移后才删除旧文件，不在重构中途留下两个可执行 Chat 实现。

## 13. 测试方案

### 13.1 节点契约测试

每个节点至少验证：

- 只读取声明的 State 分区；
- 只返回声明的字段；
- 非法输入被严格模型拒绝；
- 无权限时不调用 Repository；
- 模型调用目的与 boundary grant 一致；
- 异常不会留下部分业务写入。

### 13.2 Graph 路径测试

| 场景 | 预期路径 |
| --- | --- |
| 明确健康记录 | 安全 → 理解 → 操作 → 记录回执 |
| 缺少必填字段 | 安全 → 理解 → 追问 → resume → 安全 → patch → 操作 |
| 多条记录部分完整 | 完整命令执行，未完整 action 进入追问 |
| 修改选中记录 | 绑定版本 → grounded correction → expected-version 写入 |
| 查询历史 | 获批查询命令 → 本地数据结果 → 查询回复 |
| 个性化建议 | 获批数据视图 → 最小上下文 → 模型审核 / 本地兜底 |
| 长期事实 | 来源校验 → 保存事实命令 → 固定回执 |
| 风险输入 | 安全回复，不进入理解和操作模块 |
| 安全分类失败 | uncertain 安全回复，不调用普通 advice |
| 重复请求 | 返回相同 result，不重复执行命令 |
| 取消或过期 | 后续模型返回和 checkpoint 写入被拒绝 |

### 13.3 恢复与并发测试

1. interrupt 前、checkpoint 后进程退出；
2. resume 开始后、节点执行前退出；
3. 写入成功后、workflow result 提交前退出；
4. 同一个 resume request 重试；
5. 同一 conversation 两个 Chat 请求并发；
6. Chat 模型调用期间发生数据删除；
7. 纠错目标在模型调用期间被其他操作更新；
8. 取消与 resume 同时发生；
9. 本地模式恢复时不能自动升级为模型模式。

### 13.4 安全和数据范围测试

1. `record_only` 不能执行 advice、fact 或额外 action；
2. `preference_only` 只能保存经过校验的长期事实；
3. “不要读取历史”必须得到 `data_view=none`；
4. 单餐引用不能扩展到当天其他餐食；
5. 饮食周视图最多 7 天，体重趋势最多 28 天；
6. 模型不能通过 action text 注入当前输入不存在的事实；
7. query action 不能夹带 event 写入；
8. advice 回复只能引用 context 中存在的事实；
9. checkpoint 中的旧授权不能绕过当前策略复核。

### 13.5 回归范围

至少运行现有：

```text
test_agent.py
test_harness.py
test_safety_routing.py
test_resume_recovery.py
test_workflow_concurrency.py
test_privacy_deletion.py
test_memory_layers.py
test_grounding.py
test_structured_facts.py
test_semantic_tool_policy.py
test_eval_harness.py
```

最终运行后端完整测试集，不能只运行新增 V2 测试。

## 14. 验收标准

### AC-01：模块边界

安全、理解、追问、操作和回复分别位于独立模块；模块间通过严格契约交接，不依赖对方内部函数。

### AC-02：状态边界

Chat 使用独立 `ChatState`；Plan 和 Analysis 不再共享 Chat 专属字段。旧 `pending_*` 字段不再存在。

### AC-03：模型权限

模型只能生成候选计划、补丁、营养估算或回复表达，不能直接执行 Repository 写入，也不能自行扩大历史读取范围。

### AC-04：副作用收敛

健康事件、纠错和长期事实只由操作模块写入；回复模块没有业务写副作用。

### AC-05：安全不可绕过

初始输入、追问答案、敏感操作和 checkpoint 恢复均经过安全复核。安全分类失败时不进入普通业务路径。

### AC-06：追问恢复

追问使用显式任务队列；resume 只能回答当前 active task；重复 resume 不产生重复数据。

### AC-07：部分成功

一句话中的独立完整 action 可以先完成；未完成 action 可继续追问；已完成命令在恢复时不会重复执行。

### AC-08：幂等与冲突

相同 request ID、operation ID 和 resume request 重试均复用既有结果；纠错版本变化、取消、过期或删除会阻止旧执行写回。

### AC-09：对外兼容

现有聊天 API 和小程序交互保持可用，仍能展示等待输入、回复、卡片、证据和处理轨迹。

### AC-10：空库启动

删除获准的本地数据库和 checkpoint 文件后，系统可以自动初始化并完成记录、追问恢复、查询、建议和取消的完整流程。

### AC-11：测试通过

新增模块测试、Graph 路径测试、恢复并发测试和后端完整回归全部通过，无跳过关键安全用例。

## 15. 风险与控制

| 风险 | 控制方式 |
| --- | --- |
| 嵌套 State 更新覆盖同分区其他字段 | 节点返回完整分区；为分区增加合并契约测试 |
| 拆分节点后安全复核遗漏 | 新节点必须声明 capability；`guarded_node` 默认拒绝未知副作用节点 |
| 操作命令重放导致重复写入 | 稳定 operation ID + Repository 幂等回执 |
| 追问回答被当作新记录 | patch 只修改 requested fields；独立新意图重新规划 |
| 部分成功后回复顺序混乱 | action ID、operation ID 和 response segment 维持稳定顺序 |
| advice 在新模块中扩大数据读取 | 继续使用本地审批后的 typed data view 和固定窗口 |
| 删除旧实现过早导致回归难定位 | 新 Graph 全路径通过后一次性切换，再删除旧文件 |
| 重置数据误删其他文件 | 从 Settings 解析绝对目录，只删除明确数据库及 WAL/SHM 文件 |
| Plan/Analysis 被共享状态改动误伤 | 先定义独立状态并运行三张 Graph 的回归测试 |

## 16. 完成定义

同时满足以下条件才视为 Chat Graph 模块化 v2.0 完成：

1. 新目录和模块边界落地；
2. `Engine` 只构建新 `chat_graph`；
3. 旧 Chat Mixin 和旧共享 Chat 字段删除；
4. 数据操作全部通过显式命令执行；
5. 追问使用 ClarificationTask 队列；
6. 回复模块不写业务库；
7. 安全与数据访问边界没有放宽；
8. 本地数据库和 checkpoint 重置后可从空环境启动；
9. 小程序主聊天路径验收通过；
10. 后端完整测试集通过；
11. 架构说明、Agent 分析页和相关开发文档同步更新。

本方案完成后的目标不是拥有更多文件或节点，而是形成一条可验证的责任链：

> 输入先获得安全授权，理解模块生成受审计划，追问模块补齐必要信息，操作模块执行唯一副作用，回复模块只解释已经发生且有证据的结果，运行模块保证整个过程可以安全重试和恢复。
