# 轻记健康 Agent：架构与安全 Review 建议

> Review 日期：2026-10-20
> 范围：后端架构、LangGraph 工作流、记忆系统、模型边界、医疗安全、数据安全、运行沙箱、前端架构与可演进性。

## 1. 总体结论

这是一个方向正确、边界意识较强的单 Agent 工程。

当前实现中值得保留的设计包括：

- 使用 LangGraph 管理流程状态、追问和恢复，而不是让模型自行控制完整业务流程。
- `health.db` 是业务事实源，LangGraph checkpoint 只负责执行状态和暂停恢复。
- 模型主要生成候选结构、营养粗估和自然语言表达；持久化、统计、计划确认和数据版本检查由本地代码执行。
- 用户提供的营养值、模型估算值和未知值具有不同来源标记，没有把未知数据当作零。
- 计划采用显式确认、数据版本和过期检查，避免旧草案覆盖新状态。
- 普通咨询采用场景化、结构化和有界上下文，不把历史助手回复当作健康事实。

现阶段不建议为了“多 Agent”而拆分多个角色。项目当前本质上是一个具有多个确定性节点的单 Agent 工作流；继续强化数据边界、校验和可观测性，比增加 Agent 数量更有价值。

最需要优先解决的风险集中在四个边界：

1. 敏感数据进入 Git 和外部模型的边界。
2. 模型提取结果进入事实库的边界。
3. 网络调用、数据库事务和全局锁的边界。
4. 数据删除、派生结果和工作流副本之间的边界。

## 2. P0：应立即处理的问题

### 2.1 模型 API Key 已写入代码和 Git 历史

位置：[`backend/app/common/config.py`](../backend/app/common/config.py)

当前模型地址、模型名称和 API Key 都直接写在源代码中，`Settings.from_env()` 实际没有从环境变量读取这些模型配置。该密钥从初始提交开始已经存在于 Git 历史中，应当按“已经泄露”处理，而不能只删除当前文件中的字符串。

建议立即执行：

1. 在模型供应商平台撤销当前密钥并生成新密钥。
2. 从环境变量读取 `MODEL_API_KEY`、`MODEL_BASE_URL` 和 `MODEL_NAME`。
3. `.env.example` 只保留变量名和说明，不包含真实值。
4. 清理 Git 历史中的密钥；如果仓库曾推送到远端，还应检查远端缓存、Fork 和 CI 日志。
5. 在 CI 中增加 secret scanning，阻止类似密钥再次提交。
6. 修正前端“模型配置保存在后端 `.env`”的提示，因为它与当前实现不一致：[`frontend/src/App.tsx`](../frontend/src/App.tsx#L2077)。

### 2.2 健康数据库和 checkpoint 已被 Git 跟踪

当前以下文件已进入版本控制：

- `data/health.db`
- `data/health.db-wal`
- `data/health.db-shm`
- `data/checkpoints.db`
- `data/checkpoints.db-wal`
- `data/checkpoints.db-shm`

而当前 [`.gitignore`](../.gitignore) 没有忽略 `data/`。这些文件可能包含健康记录、原始聊天、长期偏好、派生分析和暂停中的工作流状态。

建议立即执行：

1. 将 `data/*.db*` 加入 `.gitignore`。
2. 从 Git 索引中移除这些数据库文件。
3. 如果其中出现过真实用户数据，应清理 Git 历史，而不仅是创建一个删除提交。
4. 自动化测试只使用运行时生成的临时数据库和脱敏合成数据。
5. 为数据目录设置最小文件权限；如果应用承载真实健康数据，评估 SQLCipher、操作系统密钥库或磁盘加密。

## 3. P1：核心架构问题

### 3.1 模型解析接收了范围过大的历史记录

位置：[`backend/app/modeling/parser.py`](../backend/app/modeling/parser.py)

`model_parse()` 每次都会发送最近 12 条完整记录，包括原始文本和事件数据。新增一条体重记录通常不需要历史餐食；这种做法会：

- 扩大健康数据向外部模型供应商暴露的范围。
- 增加历史文本 prompt injection 的影响面。
- 增加 token 成本和无关上下文干扰。
- 使用户难以理解一次请求究竟发送了哪些历史数据。

建议在模型解析前增加本地预路由，并按任务最小化上下文：

| 场景 | 建议发送给模型的数据 |
| --- | --- |
| 新增健康记录 | 当前用户输入、当前时间、结构 Schema |
| 补充追问 | 当前回答、checkpoint 中的目标草稿、允许修改的字段 |
| 纠错 | 用户选中的记录；未选择时只发送少量候选摘要 |
| 查询历史 | 当前问题和查询 Schema；查询由本地代码执行 |
| 普通建议 | `health/advice.py` 已筛选的结构化场景上下文 |

前端应明确说明外部模型供应商和发送范围，并提供纯本地模式或关闭外部模型的选项。

### 3.2 结构校验不能完全保证模型提取结果与原文一致

相关位置：

- [`backend/app/modeling/parser.py`](../backend/app/modeling/parser.py)
- [`backend/app/evidence/binding.py`](../backend/app/evidence/binding.py)
- [`backend/app/contracts/events.py`](../backend/app/contracts/events.py)

Pydantic 可以保证字段类型、范围和枚举合法，但不能证明食物、时间、分量、体重等内容确实来自用户原话。模型仍可能产生“范围合法但用户没有说过”的数据，并通过当前的持久化边界。

建议让关键字段携带可验证的来源信息：

- `source`: `user_text`、`user_confirmed`、`model_estimate`、`derived`。
- `evidence_span`: 对应原文片段及字符范围。
- `normalization`: 单位换算前后的值和规则版本。
- `ambiguity`: 是否存在多个解释。
- `policy_version`: 采用的本地解析或验证策略版本。

本地校验应验证数值和实体能否绑定到用户原文。以下信息无法绑定或存在歧义时，应先追问或展示确认卡片：

- 体重和运动/睡眠时长。
- 过敏、疾病、用药和医生要求。
- 对既有记录的纠错。
- 会影响计划目标的长期事实。

模型给出的“置信度”不能单独作为落库依据；应以原文证据和本地歧义规则为准。

### 3.3 全局锁跨越最长 45 秒的模型网络调用

相关位置：

- [`backend/app/workflows/runtime.py`](../backend/app/workflows/runtime.py)
- [`backend/app/modeling/parser.py`](../backend/app/modeling/parser.py)
- [`backend/app/jobs/scheduler.py`](../backend/app/jobs/scheduler.py)

`Engine.run()` 和 `Engine.resume()` 在整个 Graph 调用期间持有一个全局 `RLock`，而 Graph 内部可能执行超时为 45 秒的外部 HTTP 请求。这意味着一个慢模型请求可能阻塞其他聊天、计划确认、删除操作和调度任务。

建议改为：

1. 使用 `thread_id` 级别的工作流锁，避免同一流程被并发恢复。
2. 数据库锁只覆盖短事务，不覆盖模型 HTTP 请求。
3. SQLite 使用每请求连接或可靠的连接管理层，避免多个 FastAPI 线程共享同一连接且部分读取没有加锁。
4. 模型调用采用异步 HTTP 客户端、明确连接/读取超时和取消机制。
5. 长期可以把模型调用放到任务队列，通过任务状态或 SSE 更新前端进度。

推荐的请求阶段：

```text
读取快照 → 释放数据库连接 → 调用模型 → 本地校验
    → 开启短事务 → 检查数据版本/幂等键 → 写入 → 提交
```

### 3.4 删除数据时清理范围过大

相关位置：

- [`backend/app/infrastructure/repositories/maintenance.py`](../backend/app/infrastructure/repositories/maintenance.py)
- [`backend/app/workflows/runtime.py`](../backend/app/workflows/runtime.py)

删除一条 event 或 fact 会清空全部聊天消息、workflow、receipt、通知、规律、复盘和所有 checkpoint。隐私清理比较彻底，但也会误伤与目标记录无关的流程和用户上下文。

建议建立统一的 provenance/依赖关系：

- 每条消息、计划、规律、复盘和通知保存 `evidence_refs`。
- 派生对象保存 `source_event_ids`、`source_fact_ids`、`source_version`。
- 删除事件时通过依赖图精准删除或标记失效。
- 只有无法证明副本范围时才执行全局清理，并在 UI 中明确提示。

### 3.5 医疗安全边界依赖有限的关键词正则

位置：[`backend/app/chat/replies.py`](../backend/app/chat/replies.py)

当前 `medical_question()` 只覆盖少量疾病、症状和用药关键词。诸如头晕、低血糖、持续腹泻、催吐、进食障碍、未成年人、孕产期复杂问题等输入可能进入普通饮食建议链路。

建议在一般意图识别之前增加独立的安全路由：

```text
用户输入
  ├─ 紧急症状或立即危险
  ├─ 疾病、用药、特殊医疗营养
  ├─ 进食障碍或补偿行为风险
  ├─ 孕期、哺乳期、未成年人
  └─ 普通健康记录、查询与建议
```

高风险分支应采用本地固定策略，不进入普通热量优化、营养目标或计划生成。安全分类器应有独立测试集，并优先追求召回率；无法确定时走保守边界。

## 4. 记忆系统建议

### 4.1 建议的四层记忆模型

当前“事实库 + checkpoint + 对话 + 派生分析”分层是合理的，暂时不需要引入向量数据库。建议进一步明确为四层：

1. **工作记忆**：当前输入、事件草稿、待补字段、追问轮次和已保存 ID；使用短 TTL。
2. **事件账本**：饮食、体重、睡眠、运动和周期等已确认事实；保留追加式 revision。
3. **结构化长期事实**：偏好、过敏、限制和专业建议；支持确认、冲突、撤回与有效期。
4. **派生记忆**：规律、复盘、计划和月度摘要；必须携带证据引用、算法版本和失效条件。

### 4.2 将自由文本 facts 升级为结构化事实

当前 `facts.content` 主要保存自由文本，不利于处理：

- “我不喜欢燕麦”和“现在可以接受燕麦”的冲突。
- 临时偏好与长期限制的区别。
- 医生建议的适用时间和来源。
- 过敏、疾病、忌口之间不同的风险等级。

建议的事实结构示例：

```json
{
  "type": "food_preference",
  "subject": "oatmeal",
  "value": "dislike",
  "source": "user_explicit",
  "status": "confirmed",
  "valid_from": "2026-10-20",
  "valid_until": null,
  "supersedes": null,
  "evidence_message_id": "...",
  "schema_version": 1
}
```

对过敏、疾病、用药和医生要求，应使用更严格的确认和展示策略，不能仅依赖模型将一句话分类为 `fact`。

### 4.3 继续坚持结构化检索优先

项目目前没有向量数据库，这是合理选择。健康记录天然包含日期、类型、数值、来源和完整度，结构化过滤比语义相似度召回更可解释。

只有在以下条件满足后才建议增加 embedding：

- 自由文本事实和长对话数量显著增加。
- 已有脱敏的召回评测集。
- 能验证召回不会混淆否认、假设、他人经历和用户本人事实。
- embedding 数据具备独立删除、重建和版本管理机制。

即使引入向量检索，也应只用于候选召回，最终上下文仍需经过结构化权限、时效和来源过滤。

## 5. 模型与业务边界建议

### 5.1 明确模型输出始终是不可信候选

推荐坚持以下原则：

- 模型不能直接执行 SQL。
- 模型不能直接修改目标或生效计划。
- 模型不能直接写入长期事实。
- 模型营养估算必须标记来源和不确定性。
- 模型自然语言不能覆盖本地生成的日期、数值、保存状态和风险边界。

当前项目已经实现了其中大部分，后续应继续把这些规则放在独立的 policy/validator 层，而不是堆叠在工作流节点和 prompt 中。

### 5.2 将规则策略、模型适配和流程节点拆分

随着业务增长，建议把当前职责进一步拆开：

```text
domain/
  events.py          # 健康事件与修订规则
  memories.py        # 长期事实、冲突与撤回
  health/nutrition.py # 营养来源和一致性规则
  safety.py          # 医疗/高风险路由
  plans.py           # 目标、计划和确认策略

application/
  commands.py        # 写操作用例
  queries.py         # 查询用例
  workflows/         # LangGraph 节点和状态转换

infrastructure/
  repository.py
  model_gateway.py
  jobs/scheduler.py
```

LangGraph 节点应保持薄：读取状态、调用领域服务、返回状态增量；不要让一个节点同时负责上下文拼装、模型调用、业务校验、持久化和回复组装。

### 5.3 模型错误不应统一映射成 409

相关位置：

- [`backend/app/main.py`](../backend/app/main.py#L56)
- [`backend/app/modeling/parser.py`](../backend/app/modeling/parser.py)

模型网络失败、模型输出格式错误和业务冲突目前都可能表现为 `ValueError`，最终统一返回 HTTP 409。建议定义明确异常类型：

- `DomainValidationError` → 422。
- `ConflictError` → 409。
- `WorkflowStateError` → 409。
- `ModelUnavailableError` → 503。
- `UpstreamModelError` → 502。
- 未知异常 → 500。

错误响应应包含稳定的错误码和 request ID，但不包含健康原文、上游响应体或密钥。

## 6. 沙箱与未来工具能力

### 6.1 当前状态

当前模型没有 ToolNode、Shell、文件系统或原始 SQL 工具，唯一外部能力是调用固定模型 HTTP 接口。这使当前 prompt injection 的破坏范围相对有限，是值得保留的设计。

### 6.2 未来增加工具时使用 capability broker

如果以后增加联网搜索、可穿戴设备、日历、消息提醒或营养数据库，不要把通用工具直接暴露给模型。建议引入 capability broker：

1. 模型只能提出结构化工具调用建议。
2. 工具只接受预定义参数，不能接受原始 SQL、Shell 或任意 URL。
3. 工具按只读、写入、删除、医疗高风险分级。
4. 写操作必须携带幂等键、证据和目标对象版本。
5. 删除、计划激活、向外部发送健康数据等操作要求用户确认。
6. 网络访问使用域名 allowlist，限制请求和响应大小、超时、重试及重定向。
7. 每次调用记录调用原因、权限、读取范围、结果摘要和执行状态，但审计日志默认不保存健康原文。

### 6.3 运行环境最小权限

建议的运行沙箱包括：

- 应用使用独立低权限系统账户。
- 只允许写入指定数据目录和临时目录。
- 生产文件系统尽可能只读。
- 不挂载用户主目录、SSH 密钥、云凭据和开发环境配置。
- 模型工作进程不具备 Shell 权限。
- 外部网络默认拒绝，仅允许配置的模型和可信数据源。
- 对 CPU、内存、并发数、请求体和运行时间设置上限。

如果以后从 `127.0.0.1` 部署到公网，即使仍是单用户，也必须增加认证、TLS、CSRF 防护、访问速率限制和用户级数据隔离。CORS 不是身份认证或访问控制。

## 7. 调度、并发和可靠性

### 7.1 内置 scheduler 仅适合单进程本地运行

当前 scheduler 与 Web 服务运行在同一进程，符合本地原型需求。但在多 worker 或多实例部署中，每个实例都会启动调度循环，当前 job 状态转换不足以作为严格的跨进程租约。

未来建议：

- 将调度器拆为独立 worker。
- 使用原子 `pending → running` 条件更新和 lease 过期时间。
- 保存任务的 `started_at`、`heartbeat_at`、`finished_at` 和失败原因分类。
- 为通知和计划生成采用 outbox 模式，保证事务提交后再产生外部副作用。

### 7.2 加强可观测性但避免泄露健康数据

建议记录：

- request ID、thread ID 的哈希或内部标识。
- 工作流节点耗时。
- 模型请求耗时、状态码类别、超时和 fallback 次数。
- 数据库事务耗时和锁等待。
- 营养估算未知率、追问率、用户纠正率。
- 计划确认、拒绝和过期数量。

日志中不应记录：

- 用户健康原文。
- 完整 prompt 或模型响应。
- API Key、Authorization header。
- 数据库导出内容。

## 8. 前端架构建议

### 8.1 拆分超大 App 组件

位置：[`frontend/src/App.tsx`](../frontend/src/App.tsx#L319)

`App.tsx` 已超过 2200 行，同时承担全局状态、API 刷新、路由式页面切换、聊天、计划、历史、设置和弹窗。建议至少拆成：

- `pages/ChatPage.tsx`
- `pages/HistoryPage.tsx`
- `pages/PlansPage.tsx`
- `pages/InsightsPage.tsx`
- `pages/SettingsPage.tsx`
- `features/chat/`
- `features/plans/`
- `hooks/useHealthData.ts`
- `api/client.ts`

### 8.2 避免一次刷新依赖 15 个接口全部成功

位置：[`frontend/src/App.tsx`](../frontend/src/App.tsx#L350)

当前 `Promise.all()` 中任何一个请求失败都会导致整次刷新失败。建议选择以下一种方案：

- 增加 `/api/bootstrap` 聚合接口并返回分区错误。
- 使用查询缓存库，分别管理各资源的 loading/error/stale 状态。
- 使用 `Promise.allSettled()`，允许通知或月度摘要失败时继续展示核心聊天和仪表盘。
- mutation 成功后只失效受影响的资源，而不是刷新全部数据。

### 8.3 修复会话恢复的 conversation_id

位置：[`backend/app/workflows/runtime.py`](../backend/app/workflows/runtime.py)

首次聊天可以指定 `conversation_id`，但 resume 写入消息时没有传入原会话 ID，因此会回落到默认 `main`。当前前端固定使用 `main`，问题暂时不明显，但 API 已经暴露了多会话能力。

建议把 `conversation_id` 持久化在 workflow 行或可靠地从 checkpoint state 读取，并确保 resume 的用户消息与助手回复都写回原会话。

## 9. 测试与质量门禁

建议将测试分为：

1. **纯领域测试**：日期归属、单位换算、营养来源、计划差异、事实冲突。
2. **工作流状态测试**：首次请求、interrupt、resume、重试、取消、过期和删除。
3. **模型契约测试**：使用 MockTransport 验证 prompt 输入最小化、Schema、失败和 fallback。
4. **安全边界测试**：疾病、药物、进食障碍、孕期、未成年人和紧急症状。
5. **隐私删除测试**：删除后检查 health DB、checkpoint、WAL、导出和派生数据。
6. **并发测试**：同一 request ID、同一 thread、多 thread 和 scheduler 同时运行。
7. **前端部分失败测试**：某个非核心接口失败时页面仍可使用。

CI 建议至少执行：

```text
secret scan
Python lint/type-check
backend tests with timeout
frontend tests with timeout
frontend production build
dependency vulnerability scan
synthetic database privacy check
```

## 10. 推荐实施顺序

### 第一阶段：阻止敏感信息继续扩散

- 撤销并替换模型密钥。
- 配置改为环境变量注入。
- 停止跟踪数据库和 WAL/SHM。
- 检查并清理 Git 历史。
- 在 UI 增加外部模型和数据发送说明。

### 第二阶段：收紧模型与事实库边界

- 增加本地预路由和最小上下文策略。
- 为关键字段增加 evidence span 和来源。
- 为医疗/高风险输入增加独立安全路由。
- 将异常类型和 HTTP 状态码分开。

### 第三阶段：解决并发和数据生命周期

- 将全局锁缩小为 thread 锁和短数据库事务。
- 采用每请求数据库连接。
- 为派生对象增加 provenance，实现精准失效和删除。
- 修复 resume 的 conversation_id。

### 第四阶段：提高可维护性和可运营性

- 拆分 `graphs.py`、`repository.py` 和前端 `App.tsx`。
- 增加聚合 API 或查询缓存。
- 增加结构化指标、审计日志和超时。
- 在需要多进程部署时拆分 scheduler worker。

## 11. 最终目标

项目下一阶段的目标不应是“让模型做更多”，而应是：

- 模型看到更少但更相关的数据。
- 模型产生的每个关键事实都能追溯到用户证据。
- 每项写操作、计划变更和删除都具有明确权限与幂等边界。
- 派生记忆能够精准失效，而不是依赖全局清空。
- 高风险健康输入在进入普通建议链路前被可靠识别。
- 即使模型超时、输出错误或不可用，事实库仍保持一致且系统可恢复。

在这些基础完成之前，不建议引入多 Agent、开放式工具循环或向量数据库。当前“确定性业务代码 + 有界模型能力”的方向应继续保留并强化。
