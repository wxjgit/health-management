"""Reviewed, project-grounded answer bank; each source question is explicitly assigned."""
ANSWERS = {}


def add(ids, key, text, evidence, status='工程现状'):
    for number in ids:
        if number in ANSWERS:
            raise ValueError(f'duplicate assignment: {number}')
        ANSWERS[number] = dict(key=key, text=text, evidence=evidence, status=status)


add([1,41,159,217,262,444,544,574], 'langgraph',
    '轻记需要两类等待：记录缺字段时等用户补充，计划变化较大时等用户确认。因此选择 LangGraph 的显式状态图、interrupt 和 SQLite checkpoint。图负责编排，草稿完整性、营养来源、请求去重和计划版本检查由自己的业务代码实现。当前没有统一任务基准证明它优于其他框架；选择主要依据是与这两类暂停恢复需求匹配。',
    'backend/app/graphs.py: Engine、build_chat、build_plan；backend/tests/test_agent.py')
add([2], 'react_exit',
    '轻记没有开放式 ReAct 循环；Chat 图唯一明显回环是 clarify 回到 parse。可选追问在 rounds 达到 2 后停止继续问，必需字段则不会因为达到轮数就强行保存，用户可以取消或回答不知道。若以后加入自主工具循环，应增加总轮数、时间和重复调用检测，防止模型反复查同一份饮食记录却不结束。',
    'backend/app/graphs.py: build_chat；backend/app/drafts.py: check_drafts')
add([3], 'limits',
    '当前限制是具体业务规则：可选追问最多两轮，输入最多 4000 字，模型 HTTP 请求超时 45 秒；解析提示词只携带最近 12 条候选事件，咨询按今天、7 天或 28 天构造上下文。尚未实现全局 Token 预算、工具权限注册表和总任务时长上限，不能把这些说成已有能力。',
    'backend/app/drafts.py；backend/app/parser.py: model_request；backend/app/schemas.py')
add([4,27], 'tool_contract',
    '轻记目前采用固定节点调用 Python 业务函数，没有给模型暴露原生 tools/function_call 协议。模型输出 ExtractionResult，程序根据 intent 路由，再调用 Repository 或服务函数。若抽象成工具，应把查询事件、提出计划等动作定义为有版本的参数和结果 Schema，执行端继续校验记录 ID、幂等键及计划确认，不能让模型绕过写入规则。',
    'backend/app/parser.py: model_parse；backend/app/graphs.py: route_chat', '现状 + 扩展设计')
add([5,390], 'restart',
    'Engine 启动时打开 checkpoints.db 并初始化 SqliteSaver。追问草稿、待补字段和节点进度以 thread_id 保存；恢复接口核对工作流和 interrupt_id，再用同一 thread_id 执行 Command(resume)。失败的初始请求可以用相同 request_id 重试，已有 checkpoint 时从图状态继续。健康事实以 health.db 为准，两库提交不是同一原子事务，所以仍需要业务幂等。',
    'backend/app/graphs.py: __init__、run、resume；test_clarification_survives_restart')
add([6,307,392], 'idempotent',
    '轻记把一次事件写入的操作键固定为 thread_id:event:事件下标，Repository 在事务内先查 operations，再写 events、revisions 和操作结果；重放时返回旧结果。HTTP 层另用 request_id 和请求指纹识别重试。它覆盖当前本地数据库副作用；若以后调用外部支付或推送服务，还必须由外部服务接受幂等键，checkpoint 本身不提供跨系统恰好一次。',
    'backend/app/graphs.py: persist、save_ready；backend/app/repository.py: save_events')
add([7,149,151,152], 'single_multi',
    '轻记选择单个业务系统内的三张工作流图，没有 Manager-Worker 或子 Agent。记录、计划和分析共享同一份健康数据，写入顺序和一致性比并行角色更重要。只有将来接入独立的专业知识检索、报告生成，且输入输出和权限能明确分开时，我才会增加子 Agent；先用函数或子图复用通常更简单。',
    'backend/app/graphs.py: chat_graph、plan_graph、analysis_graph')
add([8,218,279,380], 'manager',
    '当前工程没有 Manager-Worker。若扩展为多 Agent，我会让主流程负责用户意图与最终确认，把只读指南检索或报告草稿交给工作 Agent，传递任务 ID、数据版本、必要事件 ID、预算和期望 Schema。工作 Agent 只返回证据和建议，最终写入仍由主流程的校验节点执行，避免多个角色同时改同一份计划。',
    'backend/app/graphs.py: State、persist、activate_plan；此处多 Agent 为扩展方案', '扩展设计')
add([9,460], 'trace',
    '工程保存 process_trace 和 duration_ms，轨迹包含理解输入、结构校验、营养准备、保存与建议生成，并标记本地或模型来源。消息及轨迹存于 SQLite messages 表，异常响应避免回显健康原文和上游密钥。尚未接 OpenTelemetry 或集中监控，也没有节点级 P95、Token 费用和生产成功率报表。',
    'backend/app/graphs.py: traced、run、resume；backend/app/repository.py: message')
add([10,382,394], 'no_vector',
    '轻记没有使用向量存储、Embedding 或 Reranker。个人记录已经按日期、类型和事件 ID 结构化保存，SQLite 参数查询与关键词 LIKE 足以满足当前本地单用户规模。长期偏好存在 facts 表。将来引入大量营养指南或专业文档时，再为文档增加向量索引；原始健康事实仍应保存在关系库。',
    'backend/app/repository.py: events、facts；backend/app/advice.py')
add([11,169], 'select_context',
    '当前先识别咨询场景，再组装结构化上下文：当天餐食与剩余额度、近 7 天记录或近 28 天进展；限制最多 8 条、偏好最多 5 条。有指代时才补同会话最近两条用户消息，每条截到 400 字，助手历史不进入事实上下文。没有重排模型和自动摘要，数量限制也不等于严格 Token 上限。',
    'backend/app/advice.py: classify_scene、build_advice_context、_recent_user_dialogue')
add([12,13,73,404,478], 'deployment',
    '目前 start.ps1 启动一个本机 Uvicorn/FastAPI 服务，托管 React 构建文件，Engine 在进程内编译并复用三张图，每次请求用不同 thread_id 隔离状态。后台调度也是同进程协程，没有独立 Worker 池、容器或每用户实例。只要服务还运行，关闭网页不会停止后台提醒；停止服务后无法实时生成通知。',
    'start.ps1；backend/app/main.py: lifespan；backend/app/graphs.py: Engine')
add([14,23,48,62,100,133,155,203,303,329,370,464,568], 'layers',
    '在轻记中，入口是 main.py，负责 HTTP 参数校验、接口和生命周期；graphs.py 负责工作流状态、路由、暂停恢复与结果；drafts、nutrition、advice、targets 和 services 承担健康业务规则；repository 管 SQLite。它们装配在一个服务中，并非已拆成微服务。模型实例和数据库连接放在 Engine 中，State 只存可序列化任务数据。',
    'backend/app/main.py；graphs.py；repository.py；schemas.py')
add([15], 'provider',
    '当前通过 Parser.model_request 封装兼容 Chat Completions 的 HTTP 接口，上层只调用 parse、patch、estimate_nutrition 或 answer_advice。配置使用百炼兼容地址与 qwen3.8-max，输出经 Pydantic 校验。尚无多供应商网关；若要更换供应商，需要验证结构化输出、思考模式参数和错误行为。配置中的硬编码密钥应轮换并移到环境变量，不能把现状说成完善密钥管理。',
    'backend/app/parser.py: model_request；backend/app/config.py（密钥不在本资料展示）')
add([16], 'provider_stream',
    '轻记当前使用非流式 HTTP，45 秒超时，没有供应商级限流、备用模型自动切换或 Token 计费。解析失败明确报错；营养粗估失败保留未知；建议表达失败使用本地规则回复，三种降级语义不同。若接多个供应商，应统一增量事件和用量格式，再做能力匹配、限流和价格版本统计。',
    'backend/app/parser.py；nutrition.py；advice.py；replies.py', '现状 + 扩展设计')
add([17], 'two_sum',
    '这道算法并非轻记已使用的业务实现。标准解法是一次遍历，用字典保存此前数值到下标：先查 target-x，再放入 x，从而避免同一元素被用两次。时间 O(n)，空间 O(n)。它与工程里使用字典维护 prepared_events 的思路相通，但不能声称这是工程去重算法；工程去重依靠数据库唯一键与事务。可运行代码见附录 A。',
    '附录 A；backend/app/graphs.py: prepared_events', '算法知识 + 工程关联')
add([18], 'job_reason_detail',
    '项目能证明我关注自然语言业务落地、可靠性和数据边界，例如实现追问恢复与计划确认；它不能证明我的公司职级、所在城市或离职原因。面试时应先如实交代这些个人事实，再说明希望继续深入 Agent 工程化。不能把“轻记已落地原型”改写成在某家公司负责大型平台。',
    'README.md；个人任职、地点和求职动机须由本人确认', '个人事实待补')
add([19,183,213,258,541,612], 'product',
    '轻记面向希望用自然语言记录饮食和身体状态的个人用户。核心问题是表单记录成本高、补记和纠错容易出错、后续建议缺少可追溯依据。当前交付是本地网页原型，具备五类记录、关键追问、历史查询、目标确认、复盘与站内提醒；没有企业租户、客户应用发布市场或通用低代码平台。',
    'README.md；backend/app/main.py；frontend/src/App.tsx')
add([20,214,289], 'usage',
    '真实的产品使用路径是：建档后输入“昨天中午吃了一碗牛肉面”，补充分量，保存后在历史页纠错；计划页填写或采用默认目标并确认；饮食记录完整后标记完整，再生成复盘。代码证明这些流程存在，但仓库没有可核验的活跃用户量、使用频率或真实减重效果，不能补造运营数据。',
    'README.md“使用顺序”；frontend/src/App.tsx')
add([21,85], 'publish',
    '轻记当前不是用户创建应用的平台，发布方式是构建 React 后由 FastAPI 托管，通过 start.ps1 本地启动。已有 REST API 可以供其他前端接入；若要嵌入外部系统，应增加用户身份、访问控制、版本化 API 和限流，再明确健康数据外发边界。尚未实现 SDK、灰度发布或一键云端应用发布。',
    'frontend/package.json；backend/app/main.py；start.ps1', '现状 + 扩展设计')
add([22,498,505], 'assets',
    '这个工程可以沉淀的资产是五类事件 Schema、草稿到严格记录的校验流程、营养来源追踪、request_id/操作键幂等模板、计划差异确认和回归用例。它们比某个用户的饮食原文更适合复用。若交付给其他健康场景，应把目标计算、提醒和统计模板作为领域策略替换，同时保留恢复与验证接口。',
    'backend/app/schemas.py；drafts.py；nutrition.py；backend/tests/')
add([24,549,569], 'deliverable',
    '对轻记而言，交付物是可运行的本地网页应用、Python 后端、SQLite 数据模型、启动安装脚本、测试和开发文档。当前没有商业购买合同或客户定制交付，因此不能宣称销售了托管 Runtime。未来若商业化，需要另行约定部署、模型费用、数据所有权、升级与运维责任。',
    'README.md；setup.ps1；start.ps1；docs/开发文档.md')
add([25,259,542], 'cloud',
    '当前实际部署在个人电脑，监听 127.0.0.1:8000；健康数据在本地 SQLite，但正常自然语言处理会发送必要内容给外部模型。若做云部署，需先补鉴权、HTTPS、用户隔离和备份；若做内网私有部署，还要把模型替换成内网可访问且通过兼容测试的服务。二者都是后续方案。',
    'start.ps1；backend/app/parser.py；README.md', '现状 + 扩展设计')
add([26,550], 'privacy',
    '目前数据库在本地不代表数据完全不出内网：解析会发送本次文字和候选事件，建议会发送筛选后的结构化记录。若客户禁止外发，必须配置合规的内网模型，并校验请求链路、日志和备份；不能仅靠提示词或关闭前端上传保证。咨询链路已减少无关原文，但解析仍会携带近期事件，属于可进一步收紧的地方。',
    'backend/app/parser.py: model_parse；backend/app/advice.py: build_advice_context')
add([28,241,266], 'shared_execution',
    '轻记目前只有固定工作流，没有 ReAct 工具执行器；可复用的是 Repository、营养准备、目标计算等业务函数。如果以后增加 ReAct，应让它调用这些同一服务入口，而不是复制一套保存逻辑。无论调用方来自图节点还是模型决策，都应执行 PreparedEvent 校验、操作键去重和计划确认。',
    'backend/app/graphs.py；nutrition.py；repository.py', '现状 + 扩展设计')
add([29,306,445,480,510], 'framework_boundary',
    '当前 LangGraph 承担 StateGraph、条件路由、interrupt 和 checkpoint；Engine 另外管理请求指纹、回执、锁和 API 结果。营养准备、历史检索、计划版本、统计、归档都在普通 Python 模块中。边界仍有耦合：Engine 把图和业务函数连在一起，还不是可插拔通用 Runtime，但更换编排层不必重写所有健康算法。',
    'backend/app/graphs.py；services.py；repository.py')
add([30,44], 'capacity',
    '工程尚无标准负载测试和 QPS/P95 容量结论。模型调用是同步 httpx，且 Engine.run 在进程内 RLock 下执行，慢请求会让其他关键流程排队；SQLite 也适合当前单用户而非大量并发。应先测模拟模型与真实模型两套负载，分别观察锁等待、模型耗时和数据库耗时，再决定异步 I/O、任务队列或多进程改造。',
    'backend/app/graphs.py: run；backend/app/parser.py: model_request')
add([31,74,352,572], 'tenant',
    '当前是单用户系统，没有认证、角色或 tenant_id，不能声称已有多租户隔离。若扩展，身份必须由服务端认证得到并贯穿事件、消息、工作流、checkpoint 和任务表；每次查询写入都按用户限制，缓存和文件路径同样命名空间化。还要测试伪造 event_id 或 thread_id 不能读取其他人的健康数据。',
    'backend/app/main.py；repository.py：当前表结构无用户列', '扩展设计')
add([32], 'observability_access',
    '当前轨迹与耗时保存在本地 SQLite messages，后台仅输出不含健康正文的失败提示，没有独立日志平台、链路追踪服务或分角色查看权限。若变为云端产品，需把结构化日志、Trace 和指标分开存储，通过 request_id/thread_id 关联；普通用户只看自己的记录，运维只按授权访问脱敏信息。',
    'backend/app/repository.py: messages；backend/app/scheduler.py', '现状 + 扩展设计')
add([33,34], 'measure_gain',
    '轻记没有可核验的排障提效百分比。我会选同一组失败任务，例如模型超时、重复请求和过时计划，记录从报错到定位根因的时间、是否需要查库以及是否重现。比较增加 process_trace 前后的同一任务回放，并保持模型、输入和环境一致。样本不足时只展示案例，不报告夸大的平均提升。',
    'backend/app/graphs.py: process_trace；backend/tests/', '评估方案')
add([35,293], 'control_plane',
    '当前控制配置包括 Settings、档案、偏好、目标来源和规则版本，运行执行在 Engine 三张图里，均由同一个 FastAPI 服务承载。单用户和少量固定图还不需要独立控制面。以后有多模型、多用户、动态发布版本时，再把配置管理、发布、权限和评测服务拆出，运行任务只引用不可变版本。',
    'backend/app/config.py；main.py；graphs.py；targets.py')
add([36,59,83,131,472,543,601], 'contribution',
    '可以围绕轻记的具体模块说明职责：接口与事件模型、LangGraph 追问恢复、营养准备、幂等、计划确认、前端交互和测试。面试时只选择本人实际设计或验证过的部分，并能解释如 thread_id:event:index 的去重原因。仓库本身无法证明哪些代码由谁编写或 AI 占比，因此不能替本人认领全部贡献。',
    'backend/app/graphs.py；repository.py；backend/tests/；个人职责须本人确认', '个人事实待补')
add([37,328], 'value',
    '轻记解决的是个人健康记录从“随口说”到“可纠错、可统计”的转换问题。交付包括本地网页、五类事件数据、可恢复工作流、计划与复盘。工程价值可用字段正确率、重试零重复、追问完成率、过时计划拦截率和用户记录完整度衡量；当前没有生产付费客户和医学效果数据，职责也应以本人实际贡献为准。',
    'README.md；backend/tests/test_agent.py')
add([38,111], 'hosted_runtime',
    '可以把轻记演进为托管服务，但当前仍是本机原型。托管价值来自免安装、稳定运行、跨设备访问、可靠提醒和数据备份；为此先要补身份隔离、密钥管理、模型费用、队列、监控和可恢复部署。仅把现有 Uvicorn 放到云主机，不等于已经具备托管 Agent 平台能力。',
    'start.ps1；backend/app/main.py；README.md', '扩展设计')
add([39], 'merge_sorted',
    '标准做法是从两个有序数组尾部向前比较，将较大值填到第一个数组的预留区，最后补第二个数组剩余元素。时间 O(m+n)，额外空间 O(1)，见附录 B。轻记数据库排序由 SQL ORDER BY 完成，并未使用这个数组合并算法；不要把面试算法写成项目已有实现。',
    '附录 B；backend/app/repository.py: events', '算法知识 + 工程关联')
add([40,239,261,599], 'architecture',
    '轻记前端是 React、TypeScript 和 Vite，后端 FastAPI 提供 JSON API 并托管前端。Engine 编译对话、计划、分析三张 LangGraph；Parser 请求外部兼容模型；业务服务做草稿校验、营养准备和统计；Repository 写 health.db，SqliteSaver 写 checkpoints.db。后台每分钟检查任务，整体是单用户单进程应用，没有队列集群或微服务网关。',
    'README.md；backend/app/main.py；graphs.py；scheduler.py')
add([42,87,240,263,285,414], 'dsl',
    '轻记没有画布 DSL，也没有 YAML 到图的编译器。图在 build_chat/build_plan/build_analysis 中由 Python 显式 add_node/add_edge 编译，启动时构建。如果将来允许配置流程，应先把节点类型、参数 Schema、边和中断策略定义成受限 DSL，校验引用、可达性、写入前置条件与循环边界后再转换，不能直接执行模型生成代码。',
    'backend/app/graphs.py: build_chat、build_plan、build_analysis', '现状 + 扩展设计')
add([43,113,269,343], 'badcase',
    '可讲一个有测试证据的困难场景：用户说“昨天吃得有点咸”，其中没有食物名称。如果直接要求严格餐食 Schema，会校验失败；若猜一个食物又会污染数据。现在先保留宽松 ExtractionEvent，由本地 check_drafts 决定是否追问；补充只更新待补字段，保留首次日期。测试覆盖重启和跨午夜补充，不能据此虚构某次线上事故。',
    'backend/app/drafts.py；graphs.py；backend/tests/test_draft_clarification.py')
add([45,98,160,205,546], 'quality',
    '轻记把质量分为输入理解、业务结果和运行可靠性：事件类型/日期/分量是否正确，未知营养是否保留未知，确认前目标是否不变，重试是否重复写，建议是否超出证据或医疗边界。已有 mock 模型回归和前端交互测试；尚未建立生产 A/B、真实营养误差基准及统一 LLM Judge，不能用测试通过替代健康建议有效性。',
    'backend/tests/；frontend/src/App.test.tsx；advice.py: validate_advice')
add([46,54,291], 'evolution',
    '轻记应优先完善密钥管理、核验营养数据、节点级观测和回归集，再考虑多用户与任务队列。模型升级会影响解析和估算，因此应保留输入样本、期望 Schema、字段来源和规则版本，用相同数据回归后替换。框架或模型变强并不自动解决计划授权、未知值和健康数据删除这些业务约束。',
    'README.md；parser.py；nutrition.py；targets.py')
add([47,284], 'own_work',
    'LangGraph 之上，工程自行实现了请求指纹、恢复回执、事件操作键去重、宽松草稿到严格事件的边界、营养指纹与逐字段来源、计划数据版本检查、上下文筛选、建议表达校验和归档清理。没有自行开发完整模型网关、MCP、沙箱、多租户或 SSE，这些应明确作为后续能力。',
    'backend/app/graphs.py；drafts.py；nutrition.py；repository.py；advice.py')
add([49], 'react_code',
    '轻记实际是固定图，不能把简化 ReAct 伪代码当作现有 Engine。若扩展，可以在每轮前检索档案与相关事件，调用模型获取 final 或受限工具请求，工具执行端做参数校验与幂等，结果写 checkpoint，再检查轮数和取消状态。健康写操作应进入现有确定性子流程，见附录 C；Memory Hook 应返回数据而非直接改库。',
    '附录 C；backend/app/graphs.py', '工程编码扩展')
add([50,381,562], 'memory_write',
    '轻记的事实记忆保存在 SQLite。用户明确表达偏好、过敏或限制时才由 fact 路径保存；饮食事件在完整性和营养准备之后写入 events。模型每轮临时推理、助手建议和“偏咸”等主观反馈不会自动成为长期偏好。咨询开始时读取相关 facts 和统计，纠错后更新版本并使派生规律过期。',
    'backend/app/graphs.py: fact、persist；repository.py: save_fact、_bump')
add([51,355], 'timeout',
    '模型请求目前统一 45 秒超时，没有自动退避重试。解析失败会失败返回，原 request_id 可重试；营养估算失败保存食物但标明未知；表达失败使用本地兜底。数据库写入通过操作键去重。如果新增外部有副作用工具，超时但结果未知时应先查状态再重试，不能沿用“失败就重新执行”的策略。',
    'backend/app/parser.py: model_request；nutrition.py；graphs.py')
add([52,206,555], 'close_page',
    '轻记关闭网页后，只要 Python 服务仍在，scheduler_loop 就继续检查任务并将通知写库。重新打开页面可以读取 messages、notifications 和 waiting_input 工作流。当前聊天调用是普通同步请求，没有持久化流事件日志或 SSE 断点续传；连接异常时依靠相同 request_id 重试，不能宣称逐字输出可回放。',
    'backend/app/main.py；scheduler.py；graphs.py: run')
add([53,310], 'lease',
    '轻记当前只有进程内 RLock 与数据库唯一键，没有多 Worker 租约或 fencing token。如果改为共享 Worker，需要由数据库原子认领任务，记录租约到期时间和递增代次；提交状态时验证代次，拒绝过期 Worker 的写入。仅加分布式锁不能处理旧 Worker 恢复后继续写的问题。',
    'backend/app/graphs.py: Engine.lock；此处租约为扩展方案', '扩展设计')


def alias(ids, source):
    a = ANSWERS[source]
    add(ids, a['key'], a['text'], a['evidence'], a['status'])


add([55,122,123,125,257,280,476,491], 'personal_level',
    '轻记仓库只能支持技术能力举例，无法得出入职职级、晋升日期、绩效或薪酬。此题应由本人如实填写公司体系和时间线，再用实际参与的追问恢复、营养校验或测试工作解释职责范围。没有的晋升、管理经历不能借工程功能补写，也不应把代码规模换算成某个职级。',
    '个人任职资料待本人补充；工程技术证据可见 backend/app/graphs.py', '个人事实待补')
add([56,95,121,148,182,238,282,301,365,402,425,468,506,595], 'intro',
    '我近期可以重点介绍轻记这个个人减脂 Agent：它用自然语言记录五类健康事件，通过 LangGraph 处理缺字段追问与计划确认，以 SQLite 保存事实和工作流状态。我关注的是把模型放进可验证的业务流程，例如重试不重复写、未知营养不按零计算、过时计划不激活。任职年限、教育背景和本人职责需要结合真实经历补充。',
    'README.md；backend/app/graphs.py；个人履历需本人确认', '工程口述 + 个人事实待补')
add([57,147,180,300], 'location',
    '这个问题需要按本人真实的首选城市、可接受地点、出差频率和到岗时间回答。轻记可以在本机启动，说明部署和演示不依赖公司办公环境，但这不能推断个人是否能异地工作。不要用技术项目替代地点约束，也不要在没有确认时承诺搬迁。',
    '个人地点偏好待本人补充；start.ps1 仅说明本地运行', '个人事实待补')
add([58,130,202,296,548], 'platform_relation',
    '轻记是独立的垂直业务应用，代码中没有依赖某个原有 AI 平台或企业 SDK。它复用了 LangGraph、FastAPI 和兼容模型 API，自己补健康数据模型与可靠性规则。如果以后挂到已有平台，应让平台复用身份、模型接入和运维能力，轻记保留事件完整性、营养来源、计划版本这些领域约束，不能虚构已有平台共建经历。',
    'backend/requirements.txt；backend/app/main.py；graphs.py')
add([60,367,463,483,567], 'harness',
    '可以把轻记 Engine 看成业务运行环境的雏形：它把模型请求、状态图、checkpoint、请求去重、业务写入与结果轨迹连接起来。LangGraph 提供图编排抽象，轻记还要处理恢复参数、计划版本和数据库副作用。当前它仍嵌入健康业务，没有通用插件协议、租户隔离或沙箱，不能宣称已经开发完整企业 Harness。',
    'backend/app/graphs.py: Engine；backend/app/repository.py')
add([61], 'enterprise_gaps',
    '轻记已有持久化状态、幂等、人工确认、基础轨迹和回归测试。若面向企业，还缺认证授权、多租户、密钥服务、模型限流与成本、任务调度集群、集中观测、发布回滚和备份。健康场景还必须核验营养来源与建议边界。不能用 ReAct 或一个工具协议代替这些工程能力。',
    'README.md；backend/app/config.py；graphs.py', '现状 + 扩展设计')
add([63,134,273,368,484], 'business_modules',
    '当前业务模块包括 drafts 的缺字段检查和增量补丁、nutrition 的营养准备与来源、targets 的默认目标公式、advice/replies 的上下文与表达校验、services 的计划统计归档。运行控制由 Engine 管理，数据由 Repository 保存。它们是普通 Python 模块，并没有通过动态插件清单加载。',
    'backend/app/drafts.py；nutrition.py；targets.py；advice.py；services.py')
add([64,67], 'manifest',
    '轻记没有能力声明文件或热加载注册中心。节点和函数在启动时静态绑定，Python 依赖由 lock 文件固定，业务策略在结果中保存 formula_version、rule_version 或 strategy_version。若以后加插件清单，至少声明能力 ID、版本、输入输出 Schema、数据访问范围、是否有副作用和兼容状态版本，再做加载校验。',
    'backend/app/graphs.py；targets.py；services.py；backend/requirements.lock.txt', '现状 + 扩展设计')
add([65,70,256,465], 'decouple',
    '当前通过普通函数和 Repository 分离业务计算与图路由：营养和目标计算不需要直接操作图 API。新增饮食规则应放入业务模块，新增通用请求或恢复语义才改 Engine。这个边界尚未成为正式接口协议；若继续通用化，应抽出服务端口、节点结果契约和状态版本迁移，而不是把所有规则堆进图节点。',
    'backend/app/graphs.py；nutrition.py；targets.py；services.py')
add([66,137,236], 'memory_policy',
    '在轻记里，记什么由健康业务决定：过敏与明确偏好长期保留，主观偏咸只属于当前反馈，未知营养保留空值。Engine 负责把 State 存进 checkpoint，Repository 提供存取；不由运行框架自行提炼用户画像。以后复用到别的行业，应替换写入、检索和遗忘策略，保留存储与生命周期接口。',
    'backend/app/conversation.py；graphs.py: fact；advice.py；repository.py')
add([68,101], 'tool_skill_interfaces',
    '以轻记映射这些概念：Tool 可对应查询事件或提出计划的原子服务；Workflow 是带校验、追问和保存的执行图；Skill 可作为如何使用一段流程的说明和受限入口；Artifact 对应结构化卡片或计划；Hook 可用于节点前后观测。当前后三者并无统一注册协议，现有契约是 Pydantic 模型、Python 函数和 JSON 结果。',
    'backend/app/schemas.py；graphs.py: format_result；services.py', '现状 + 概念映射')
add([69,114,135,215,294,373,600], 'workflow_react',
    '轻记偏固定 Workflow：模型先给出 record/correct/query/advice/fact 意图，程序选固定分支。它没有自由反复选择工具的 ReAct 主循环。这样能保证营养准备在写入之前、计划确认在激活之前。如果以后开放知识探索，可以把只读查询交给 ReAct，记录保存和目标变更仍调用现有受约束流程。',
    'backend/app/graphs.py: build_chat、route_chat、build_plan')
add([71], 'domains',
    '工程覆盖一个健康领域中的五类记录：饮食、体重、睡眠、运动和生理周期，配套计划与分析三类工作流。它们证明了同领域不同事件的复用，没有证明跨金融、客服或工业场景的通用性。跨领域复用要用新的数据模型和真实任务验证，而不能把五种事件说成五个行业。',
    'backend/app/schemas.py: ExtractionEvent；README.md')
add([72,473], 'collaboration',
    '如果多人协作开发轻记，可按 Schema/数据层、图与模型适配、前端、测试拆分，先约定 API 和状态字段，再独立分支集成。涉及营养来源、数据库迁移或 checkpoint 字段的改动必须联调恢复场景。仓库无法证明团队规模或真实分工；AI 生成代码也要通过相同契约和审查，不能以能运行代替正确。',
    'backend/app/schemas.py；frontend/src/types.ts；backend/tests/', '协作方案 + 个人事实待补')
add([75,76,157,219,220,622], 'skill_route',
    '当前没有几十个 Skill 的发现和激活机制。轻记先由模型抽取五类 intent，再由 route_chat 按 question、events 和 fact_text 选择固定节点；普通咨询另用本地规则判场景。如果以后增加 Skill，应先按只读/写入和业务域筛选，再选择少量候选，最后由执行端校验权限与前置状态，不把全部说明灌给模型。',
    'backend/app/graphs.py: route_chat；backend/app/advice.py: classify_scene')
add([77,153,255,330,485,503,578], 'workflow_skill',
    '轻记没有 Skill 加载器；可把“记录并校验一餐”或“提出并确认目标”封装成 Workflow Skill，但实际执行仍是 Python 图和业务代码。Skill 的说明负责告诉调用方何时使用、传什么、会不会等待用户；它不替代图中的必需校验。当前需要研发修改代码，不能说已有纯配置低代码能力。',
    'backend/app/graphs.py: build_chat、build_plan；schemas.py')
add([78,166,353,376,515], 'sandbox',
    '轻记没有给模型开放 Python、Bash 或任意代码执行，所以没有实现代码沙箱；模型只能返回受限 JSON。若以后允许用户脚本，应放到独立隔离进程或容器，限制文件、网络、CPU、内存和执行时长，健康数据库与密钥不能直接挂入。隔离之外，写入仍要经过服务端授权和业务校验。',
    'backend/app/parser.py: model_request；当前无动态执行入口', '现状 + 扩展设计')
add([79,637], 'sandbox_cost',
    '当前轻记没有沙箱冷启动成本。若增加代码能力，我会把普通记录解析保留在服务进程，把不可信脚本放进受限执行环境，按任务回收；通过并发配额与预热减少等待。评估应同时看启动耗时、任务完成率、隔离强度和每任务资源，不能为了快而让脚本直接访问用户健康库。',
    'backend/app/main.py；未来脚本执行为扩展方案', '扩展设计')
add([80,377], 'infra_choice',
    '轻记当前本机进程足够，不使用 Kubernetes 或 Serverless。若提供云服务，常驻容器适合 API 和需要保留进程的调度服务；短时无状态任务可用 Serverless，但要验证时限、启动和存储；按任务创建 Pod 更灵活也增加调度与资源成本。checkpoint 应放共享持久存储，不能跟临时实例一起丢失。',
    'start.ps1；backend/app/main.py: lifespan', '扩展设计')
add([81,84,112,260,358,366,429,489,494,528,586,614], 'scale',
    '轻记当前定位为单用户本地原型。仓库没有生产客户数、DAU、调用量、QPS、同时在线数或真实减重成效的统计证据，因此这些数字不能从代码推断。可展示的是已有事件流程、恢复和幂等测试；若要证明规模，应另做负载测试与实际使用统计，并说明硬件、模型、时间窗口和失败口径。',
    'README.md；backend/app/graphs.py: Engine.lock', '工程现状；运营数据未提供')
add([82,268], 'resource',
    '当前请求与后台 tick 共用 Engine 及进程内锁，没有实时/离线 Worker 池或 Token 调度。模型调用慢时可能阻塞其他业务。若扩展，应将后台分析、归档和长任务放低优先级队列，实时记录单独限并发，并给模型请求总 Token 和费用配额；所有任务仍用持久状态和幂等键保证可重试。',
    'backend/app/main.py: lifespan；scheduler.py；graphs.py', '现状 + 扩展设计')
add([86,283,479], 'engine',
    '执行引擎基于 LangGraph，并非从零手写调度器。Engine 启动时构建 chat、plan、analysis 三张 StateGraph，再用同一个 SqliteSaver 按 thread_id 保存运行状态。FastAPI 接受请求，Engine 选择图，普通 Python 模块实现健康业务。自建部分是可靠性和领域规则，不是重新实现 LangGraph。',
    'backend/app/graphs.py: Engine、graph_for；backend/app/main.py')
add([88,96,110,184,242,265,287,327,492,507,508], 'compare_platform',
    '这个工程能直接证明的选型是 LangGraph 加 FastAPI，而不是对题中所有平台做过系统实测。轻记重点是健康事件校验、追问恢复、营养来源和计划确认；通用平台能否满足，需要用同一组任务验证暂停重启、幂等写入、未知值和数据清理。没有对照结果时，我会说明比较维度，不宣称某产品一定更差或功能缺失。',
    'README.md；backend/app/graphs.py；第三方产品能力未在本工程实测')
add([89,210,290,369], 'toc',
    '轻记更接近 ToC 的个人工具，重点是自然语言使用成本、长期个人记录、隐私和可理解的建议。ToB 扩展还会增加组织权限、审计、系统集成和交付运维。当前没有企业组织体系，也没有大规模消费者运营；个人偏好和过敏信息不能只存在聊天窗口，需要独立事实存储和删除入口。',
    'README.md；backend/app/repository.py: facts；backend/app/advice.py')
add([90,172,234,243,387], 'memory_layers',
    '轻记的工作记忆是 State，保存当前草稿、待补字段、已存事件和估算结果；短期交互记录在 messages，咨询只在有指代时选最近两条用户消息；长期事实在 events、facts、profile 和 active plan。它们都存在本地 SQLite，只有 checkpoint 独立一库。当前没有向量化记忆或统一自动摘要器。',
    'backend/app/graphs.py: State；repository.py；advice.py')
add([91,191,192,333,620], 'many_tools',
    '轻记当前没有大工具集，模型看到的是提取 Schema，实际动作由图路由。若接入很多营养库、设备和报告工具，应按当前意图只暴露少量相关能力，统一名称、单位和参数范围；同名能力用来源与用途区分。执行端仍严格校验，不依赖模型仅凭描述选对工具。',
    'backend/app/parser.py: model_parse；graphs.py: route_chat', '现状 + 扩展设计')
add([92,237], 'conflict_rules',
    '轻记没有独立 Skill 规则层，但有明确优先级：用户确认的过敏或专业限制优先于通用目标规则，事实优先于助手历史，未知值不能被建议补成确定值。限制会影响 default_targets 是否可生成。注意当前限制匹配仍是关键词和提示约束，并不能保证所有食物配料都经过完整过敏筛查。',
    'backend/app/targets.py: default_targets；services.py: meal_assessment')
add([93,609], 'languages',
    '轻记后端实际使用 Python，主要是 FastAPI、Pydantic、LangGraph 和 SQLite；前端使用 TypeScript/React。当前工程没有 Java 后端代码，因此只能把 Java 单例、线程池等作为知识题说明，不能声称在本项目做过 Java 微服务。其他语言经验需结合本人的真实履历。',
    'backend/app/；frontend/src/；frontend/package.json')
add([94], 'adapt_language',
    '如果团队采用其他语言，我会先稳定轻记的事件 Schema、REST API 和数据不变量，再逐步迁移 Repository 或服务层，用同一批幂等、追问恢复和计划确认案例验收。LangGraph 相关运行部分可以暂时保留 Python 服务，而不是一次性重写所有模块。迁移能力和已有语言经验应分别说明。',
    'backend/app/schemas.py；main.py；backend/tests/', '迁移方案')
add([97,185,592], 'source_reading',
    '仓库能证明我使用了 LangGraph 的 StateGraph、SqliteSaver、interrupt 和 Command，并为重放场景设计业务去重；它不能证明我读过某个 Coding Agent 的全部源码。面试时只讲实际阅读过的模块和具体结论，例如暂停节点重放导致副作用需要独立幂等，其他源码研究经历必须由本人确认。',
    'backend/app/graphs.py；backend/tests/test_agent.py', '工程证据 + 个人事实待补')
alias([99],60)
add([102], 'io_contract',
    '聊天入口输入 request_id、conversation_id、text 和可选 event_id；补充入口输入 thread_id 对应路径、interrupt_id 和文字或 approved。输出统一包含 thread_id、status、reply、cards、evidence_refs、process_trace 与 duration_ms；等待用户时增加 pending_input。内部 extraction 和数据库连接不会原样返回浏览器。',
    'backend/app/schemas.py: ChatRequest、ResumeRequest；graphs.py: format_result')
add([103,334,357], 'domain_isolation',
    '当前三张图共享一个健康域和 Repository，没有跨域权限系统。如果引入独立专业 Agent，需要为任务明确只读事件集合、允许动作和输出 Schema，各自保存状态；跨域请求必须由服务端核对用户授权。不能把“不同 thread_id”误认为用户身份隔离，thread_id 只标识运行任务。',
    'backend/app/graphs.py: State、Engine；跨域隔离为扩展方案', '扩展设计')
add([104,105,360], 'same_task_benchmark',
    '目前没有轻记与通用 Agent 在相同模型、工具和任务上的对照数据。可以构建同一批记录、纠错、重启、超时和计划确认任务，固定模型与输入，比较事实正确率、重复写入、追问次数、耗时和成本。轻记的确定性校验有明确设计优势，但没有实验就不能宣布整体效果一定胜出。',
    'backend/tests/；README.md；对照实验尚未提供', '评估方案')
add([106,161,225,582], 'ground_truth',
    '轻记的参考标准可直接来自业务不变量：某句话应形成哪些带日期事件、补丁只能改哪个字段、重试后应有几条记录、过时草案不能激活。现有测试用固定输入和 mock 模型响应断言这些结果。若评估真实模型，还需人工标注中文语料、允许合理多解并划分回归集，营养数值则需要可信标签或核验来源单独评估。',
    'backend/tests/test_agent.py；test_draft_clarification.py；test_nutrition_preparation.py')
add([107,608], 'model_now',
    '代码默认调用百炼兼容 Chat Completions 接口，模型名称为 qwen3.8-max，提取请求 temperature=0，并对这个模型名关闭思考模式。模型用于解析、补丁、缺失营养粗估与建议表达；统计和目标计算在本地。这里说的是仓库配置，不保证供应商当前服务可用，也没有模型训练或本地权重部署。',
    'backend/app/config.py（不展示密钥）；backend/app/parser.py')
add([108,606,607], 'training',
    '轻记没有模型后训练、NLP 研究实验或自训练权重，使用现成模型 API，加结构化 Schema 和本地校验约束输出。可讨论的数据工作是中文回归样本、字段标注和失败案例整理；它们还不等于微调。如果未来积累足够错误样本，应先评估提示和数据问题，再决定是否训练，并保护健康数据。',
    'backend/app/parser.py；backend/tests/；仓库无训练脚本')
add([109], 'developer_api',
    '在轻记里新增能力需要写 Pydantic 事件或请求 Schema、领域服务函数、图节点及路由，再补对应 API 和前端类型。开发者不是只上传提示词即可发布应用。已有清晰的接点包括 Parser 的结构化方法、Repository 的数据接口以及 Engine 的三张 build_* 图，但没有公开插件 SDK。',
    'backend/app/schemas.py；parser.py；graphs.py；frontend/src/types.ts')
add([115,116,388], 'subagent',
    '当前没有静态或动态 Sub-agent，也不存在父子任务调用树。若以后加入，可以先声明只读报告或知识检索子任务，传 parent_id、输入引用、数据版本和预算，并设最大深度、禁止回调祖先、独立状态与取消传播。最终健康写入仍回到主流程校验，避免循环委派和多处改库。',
    'backend/app/graphs.py：当前仅 chat/plan/analysis 三图', '扩展设计')
add([117,181,211,281,490,594], 'job_reason',
    '可以结合轻记说明希望继续深入 Agent 的状态管理、数据可信度和产品落地，但离职原因不能从工程推断。面试时用真实原因说明下一阶段目标，再展示自己能解释和验证的设计。不要把个人原型包装为当前公司的生产平台，也不要虚构客户规模或组织职责。',
    '个人求职动机待本人补充；README.md 仅支持工程背景', '个人事实待补')
add([118,120,146], 'career',
    '可以把轻记展示出的兴趣作为发展方向：继续做 Agent 应用工程，深入可恢复执行、上下文选择和事实校验，并补齐部署、评测与数据质量。选择公司的理由应结合真实岗位、业务和个人意愿；工程本身不能代替对公司了解，也不能证明已具备模型训练或大型平台经验。',
    'README.md；个人职业偏好须本人确认', '个人事实待补')
add([119], 'company_opinion',
    '轻记是讨论技术取舍的材料，无法支持对本人所在公司优劣的判断。可讲已观察到的工程取舍，例如原型开发快，但单进程锁和本地数据库限制扩展；对公司则应基于真实经历，具体说明协作和技术条件，不拿项目不足推断组织问题。',
    'README.md；公司事实待本人补充', '个人事实待补')
add([124], 'promotion_evidence',
    '如果本人确有晋升，可用实际承担范围和可核验结果说明；轻记可作为技术作品，展示追问恢复、幂等与计划版本设计。仓库不能证明晋升由哪些贡献带来，更不能把 124 个测试用例换算成绩效。没有晋升经历就直接说明，转向自己能证明的工程能力。',
    'backend/tests/；晋升信息待本人补充', '个人事实待补')
add([126,302,598], 'project_pitch',
    '我选轻记，因为它能完整讲清一条 Agent 业务闭环：自然语言输入拆成健康草稿，缺字段时追问，营养准备后写库，支持历史纠错，再用完整记录做计划和复盘。最有代表性的设计是 checkpoint 恢复配合幂等写入、未知营养不算零，以及计划确认时核对数据版本。它仍是单用户本地原型。',
    'README.md；backend/app/graphs.py；nutrition.py；repository.py')
add([127,277,364,496], 'fde',
    '以轻记类项目做现场落地，先让使用者确认记录习惯、必须追问字段、数据外发和计划确认边界，再用真实但经授权的样本验证。产品人员负责记录成本和建议是否可执行，专业人员核验健康内容，研发实现可恢复流程。当前没有真实 FDE 团队或客户交付数据，不能把这种方法说成已完成的企业项目。',
    'docs/开发文档.md；README.md', '交付方法；经历未提供')
add([128,129,132,470,475,477], 'team',
    '工程文件没有给出团队人数、用工类型、谁拍板或谁维护内核，不能据此虚构组织结构。可以按模块说明需要协同的职责：产品定义记录和确认体验，后端维护数据与恢复，前端展示状态，测试验证异常路径，专业人员审核健康依据。本人参与的设计决策应举具体代码并如实说明。',
    'backend/app/；frontend/src/；团队与个人分工待本人确认', '个人事实待补')
alias([136],47)
add([138,139], 'memory_api',
    '轻记没有独立 Memory 插件协议，实际接口是 Repository.events、facts、get_meta/save_fact，加上 advice.build_advice_context 按场景选数据。Engine 构造时持有 Repository 并把业务函数绑定为节点。若抽接口，可分 read_context、write_confirmed_fact、correct_fact、delete_fact，传任务目的和数据版本，避免通用内核擅自改变健康记忆策略。',
    'backend/app/repository.py；advice.py；graphs.py: Engine.__init__')
add([140,274,561], 'context_engineering',
    '轻记把上下文分成当前任务 State、消息、结构化事件、长期事实和生效目标。模型调用每次重建两条消息：system 规则与 user JSON，不累积完整聊天。解析只发近期 12 条事件；咨询按今天/7 天/28 天组装数据，仅在有指代时补最近两条用户消息。补充回答只携带待补草稿和字段，避免历史把本次日期食物改掉。',
    'backend/app/parser.py: model_request、patch；advice.py；graphs.py: State')
add([141,168,275,405], 'token_budget',
    '当前没有 Token 计数、按窗口比例触发的自动压缩或摘要回填。工程只用条数和场景限制输入：解析 12 条候选、咨询近期窗口、指代最多两条 400 字用户消息。当天餐食和长事件仍可能较大。若补预算，应先保留当前问题、硬限制、待补字段和证据 ID，再裁剪低相关数据，原文留库按需读取。',
    'backend/app/parser.py: model_parse；advice.py: build_advice_context', '现状 + 扩展设计')
add([142,194,406], 'compression',
    '轻记没有模型生成的对话压缩摘要。历史消息通过角色筛选、最近条数和每条 400 字截断控制；健康数据改用结构化餐食或日汇总，未选中的原记录仍在数据库。另有 30 天聊天清理和一年月度归档，这属于保留策略，尤其归档会删除逐餐细节，不能与无损上下文裁剪混为一谈。',
    'backend/app/advice.py；graphs.py: prune_context；services.py: archive')
add([143], 'validated_scope',
    '可核验的测试包括追问重启恢复、跨午夜日期不漂移、混合事件部分保存、提交后重放不重复估算、过时计划不生效、建议数字越界兜底和删除副本清理。当前后端收集到 124 个用例，前端 10 个交互测试；这些证明测试覆盖的逻辑，不等于真实模型长期效果、生产容量或临床营养准确率。',
    'backend/tests/；frontend/src/App.test.tsx；本轮未重新运行全套业务测试')
add([144,145,591], 'tracking',
    '可把关注点落到轻记的可验证改进：模型结构化输出兼容、checkpoint 的重放语义、长任务状态、工具权限和评测。具体“最近读了哪篇、哪款产品有何新变化”需要本人提供真实阅读记录并核对当期资料。这里不根据工程推断源码阅读或给新产品做未经实测的排名。',
    'backend/app/parser.py；graphs.py；个人阅读记录待补', '个人事实待补')
add([150], 'patterns_difference',
    '轻记中三张 Workflow 的节点顺序由代码决定，模型只填结构化结果；单 Agent 指统一完成一个任务的决策入口；多 Agent 则要有多个独立角色和状态。图节点多不代表 Agent 多。当前项目采用固定流程承接自然语言的不确定性，因此可以强制保存前校验和计划确认。',
    'backend/app/graphs.py: build_chat、build_plan、build_analysis')
add([154,403], 'lifecycle',
    '请求进入 POST /api/chat 后先校验参数和 request_id 指纹，创建或恢复 thread_id。图依次解析、检查草稿、准备营养，完整独立事件可先保存；缺字段则 interrupt。恢复后仅合并待补字段，最终保存、组装卡片与证据，将状态、耗时和消息落库。模型失败、取消和等待用户分别有不同结果，HTTP 返回并不必然代表新事件已保存。',
    'backend/app/main.py: chat；graphs.py: run、build_chat、format_result')
add([156], 'task_vs_model',
    '一条轻记任务可能包含多轮用户补充和多个模型请求。首次解析、缺失字段 patch、营养粗估、建议表达是不同调用，整个工作流由同一 thread_id 关联；事件写入又有独立操作键。普通记录不一定调用表达模型，查看历史也不会重新营养估算，因此不能把一次模型成功当作整个任务完成。',
    'backend/app/graphs.py；parser.py；nutrition.py')
add([158], 'parallel',
    '轻记当前主要串行执行，Engine.run/resume 在锁下调用图，营养准备先于保存、保存先于事实回复，这是数据依赖。若未来并行，只有互不修改状态的知识查询或多个独立估算才适合；最终合并必须按稳定事件下标和数据版本完成。不能把现有多条事件处理说成已经并行工具执行。',
    'backend/app/graphs.py: run、prepare_nutrition、persist')
add([162,200,201], 'judge',
    '轻记目前不使用 LLM-as-a-Judge，主要以程序断言验证字段、状态和写入结果。若引入 Judge，可评估建议是否回应问题、是否使用证据、是否表达未知和医疗边界；数值与计划状态仍用确定性校验。评测样本需人工复核并包含否定、假设和跨天输入，不能让同一模型出题、作答、自评分后就宣称有效。',
    'backend/tests/test_advice_generation.py；test_contextual_replies.py；advice.py', '现状 + 评估方案')
add([163,318], 'regression',
    '当前有 pytest、Vitest 和构建命令，但仓库没有完整发布流水线配置。可以把事件解析、恢复、幂等、营养来源和计划确认设为发布回归门槛，另用固定中文样本跑真实模型，记录模型名和规则版本。修改提示词也要回归，尤其检查“数据保存成功但回复表达失败”的结果是否仍正确。',
    'backend/tests/；frontend/package.json；README.md', '现状 + 发布方案')
add([164], 'rollback',
    '轻记尚未实现灰度或自动回滚。若上线后质量下降，先按 request_id 和 process_trace 区分解析、营养、建议校验和写入问题，固定失败输入重放；回滚代码、模型或提示版本时也要核对 checkpoint 与数据 Schema 的兼容性。已保存健康事实不能随代码回滚而直接覆盖，错误记录应走版本化纠错。',
    'backend/app/graphs.py；repository.py: revisions；扩展方案', '扩展设计')
add([165,396], 'injection',
    '轻记的模型提示明确把用户文字当数据，禁止执行其中指令，输出再经 Pydantic 和业务校验；模型没有 Bash、文件或任意 SQL 工具。即使输入要求跳过确认，图的固定边和 decide_plan 仍执行本地检查。这降低攻击面，但并非通用注入防护证明；未来接网页内容还要标记来源、隔离不可信文本并限制工具权限。',
    'backend/app/parser.py: model_parse、answer_advice；graphs.py；repository.py')
add([167,378,397], 'trust',
    '轻记当前仅本机单用户，没有身份鉴权，因此不能公网直接暴露。已有控制是模型只输出数据、服务器校验事件、首次或较大目标变更须确认、密钥不返回前端。实际配置仍有硬编码密钥，需轮换并移出源码；若云化再加最小权限、用户隔离和审计，提示词不能替代服务端授权。',
    'backend/app/main.py；schemas.py；repository.py: decide_plan；config.py')
add([170,248], 'large_result',
    '轻记查询回复最多取 100 条事件，咨询优先使用结构化记录与统计，没有通用工具结果压缩器。若返回内容继续增长，应给结果分页、保留事件 ID/日期/来源并按问题取必要字段，详情留数据库。不能截断后仍把局部合计描述成完整全天；营养未知和记录完整标记必须一同传递。',
    'backend/app/graphs.py: respond；advice.py；repository.py: events')
add([171], 'preserve',
    '若为轻记加上下文压缩，必须保留当前问题、首次发生日期、未解决字段、确认限制、已保存记录 ID、操作键、营养来源/未知原因和计划版本。原始健康事实放数据库，摘要只作导航。尤其不能把“没记录”压成“没吃”、把“偏咸”压成“钠超标”，也不能丢失用户明确否认。当前这些关键信息通过 State 和结构化字段保存。',
    'backend/app/graphs.py: State；schemas.py；conversation.py', '现状 + 扩展设计')
add([173,235,383,384,386], 'memory_conflict',
    '健康事件纠错保留相同 event_id 并增加 version，旧版本进入 revisions，数据版本递增使派生规律过期；用户可删除长期事实。偏咸等反馈有否认处理。但 facts 的新旧语义冲突目前没有自动合并，例如“不爱牛奶”后来改口不能保证自动替换，应通过删除旧事实或显式确认处理。不能声称已有完整记忆冲突引擎。',
    'backend/app/repository.py: save_events、save_fact；conversation.py；main.py: delete_fact')
add([174], 'plan_execute_code',
    '天气温差题用于展示通用 Plan-and-Execute，不是轻记现成功能。可把两个 weather 城市查询步骤标为只读工具，第三步 diff 引用两个已完成结果，Executor 校验依赖与数值后计算，Summarizer 只表达结果。映射到轻记则是先读记录与目标、再计算差异、最后解释；确定性计算不交给模型猜。代码见附录 D。',
    '附录 D；backend/app/services.py；工程未接天气 API', '工程编码扩展')
add([175], 'planner_executor',
    '轻记没有 Planner/Executor/Summarizer 三个独立 Agent。可类比为：模型提取意图和草稿，图节点执行本地校验与保存，respond 组装结果。计划页的 plan_draft 是目标和日程业务函数，并非自主拆任务 Planner。未来真正拆角色时，应让计划产生受限步骤、执行器核对状态，表达层不再修改数值。',
    'backend/app/graphs.py；services.py: plan_draft')
add([176], 'step_refs',
    '当前步骤是静态图节点，后续节点通过 State 中的 parsed、prepared_events、record_ids 和 plan_id 引用前序结果，事件用稳定下标关联。若扩展动态计划，步骤应带 step_id、依赖 ID、输入引用和输出 Schema，禁止用一段自由文本当作可执行依赖；失败状态要能阻止后续写入。',
    'backend/app/graphs.py: State、prepare_nutrition、persist', '现状 + 扩展设计')
add([177], 'node_type',
    '轻记直接用节点代码区分：parse 和营养 estimator 可能调用模型，validate_drafts、persist 和 approve 则执行本地逻辑或中断。节点不是靠模型在运行中随意改类型。将来动态 DSL 应声明 model/tool/compute/interrupt 等类型及副作用，执行器按类型选择适配器并校验输入输出。',
    'backend/app/graphs.py；parser.py；schemas.py')
add([178], 'registry',
    '当前工程没有工具注册表，图节点通过 getattr(self, name) 绑定 Engine 方法，模型不获取全部工具描述。若抽注册表，可先注册 read_events、prepare_meal、propose_plan 等稳定 ID，附参数和结果 Schema、只读/写入标记、幂等与超时策略。写健康事实的函数仍只能接受校验后的事件，不接受任意字典直写。',
    'backend/app/graphs.py: build_chat；schemas.py: PreparedEvent', '现状 + 扩展设计')
add([179,212], 'job_process',
    '其他面试流程属于本人当前事实，轻记工程无法提供。应简洁说明实际阶段和可协调的时间，不虚构 offer 或截止日期；若被问技术准备，可以说明正在用轻记梳理状态恢复、上下文筛选和可靠性用例。',
    '求职进展待本人补充', '个人事实待补')
add([186,500,512], 'graph_complexity',
    '轻记对话图目前 9 个节点，计划图 3 个，分析图 2 个，没有大型画布。复杂性主要来自追问与部分保存，所以把完整性、营养准备和回复分别抽成普通函数并测试。继续增长时可按稳定输入输出拆子图，但要明确中断归属和副作用，避免只是把一个巨大节点换成层层不可观测嵌套。',
    'backend/app/graphs.py: build_chat、build_plan、build_analysis')
add([187,188], 'nl_dsl',
    '轻记支持自然语言生成事件草稿，不支持自然语言生成任意 Workflow 或最终 DSL。模型输出是受限 ExtractionResult，节点顺序由代码固定。若以后允许生成流程，只让模型产出草案，编译前校验节点白名单、Schema、连线、循环上限和写入前置条件，再用隔离测试验证；不能直接执行模型提供的代码。',
    'backend/app/parser.py: model_parse；graphs.py: build_chat', '现状 + 扩展设计')
add([189], 'graph_validation',
    '当前图结构由研发代码构建，数据由 Pydantic 校验；没有通用 DSL 编译校验器。若为轻记加入动态流程，应检查节点存在、输入输出类型、边引用、可达性与循环边界，并强制任何保存路径都经过 PreparedEvent，任何激活路径都经过版本与确认。仅 JSON 合法还不足以证明流程安全。',
    'backend/app/graphs.py；schemas.py', '扩展设计')
add([190], 'auto_fix',
    '轻记现有测试可运行，但没有模型自动修复工作流的闭环。若加入，先在临时数据库与 mock 模型中跑候选流程，返回结构化的节点/字段错误，限制修复轮数并重新回归幂等与确认不变量。修复通过后仍应经代码审查和版本发布，不能让修复任务接触真实健康数据或直接改生产库。',
    'backend/tests/；当前未实现自动修图', '扩展设计')
add([193,335,621], 'progressive',
    '轻记已采用按任务披露数据：解析、补丁、估算和表达各用专门提示与 Schema，咨询按场景读取必要记录，补丁只拿当前草稿和待补字段。它没有通用 Skill 渐进加载机制。以后工具增多时，可先给简短能力索引，选中后再加载该能力的详细参数，不把全部工具、历史和规则每轮重复注入。',
    'backend/app/parser.py；advice.py', '现状 + 扩展设计')

add([195,393,422,447,517], 'rag_pipeline',
    '轻记当前没有文档 RAG，历史事实通过 SQLite 检索。如果扩展营养指南问答，我会先校验资料授权与版本，解析文档并按标题/表格结构切块，保存页码和来源，再做关键词与语义召回、融合去重、必要重排，最后让回答引用证据。个人健康事实和指南知识分库存放，指南文本不能覆盖用户已确认限制。',
    'backend/app/repository.py: events；advice.py；文档 RAG 尚未实现', '扩展设计')
add([196,250,411], 'file_types',
    '轻记目前只接文本和网页表单，没有上传 PDF、Word、PPT 或 Excel 的解析流水线。若扩展，PDF/Word 保留章节页码，PPT 保留页序和布局关系，表格保留列名、单位和行键，再生成带来源的规范数据。营养标签必须核对每份/每 100g，不能把 OCR 数字直接当成已确认食用量。',
    'backend/app/main.py：无文件上传接口；schemas.py: NutritionReference', '扩展设计')
add([197,230,451,520], 'ppl',
    '轻记没有实现 PPL 切分。它通常利用语言模型对上下文后续文本的预测难度变化寻找候选边界，但阈值、模型和上下文会影响结果，并非天然可靠的语义分段。若为健康指南尝试，我会先做标题和段落基线，再比较 PPL 候选边界是否破坏适用条件、剂量和表格脚注，只有回归有收益才采用。',
    '工程无文档切分模块；此处为候选方案', '扩展设计')
add([198,253,522,587], 'hybrid',
    '当前没有 BM25、Embedding、RRF、MMR 或 Reranker。若接健康指南，BM25 可覆盖食品名和专业术语精确匹配，向量召回补语义表达，RRF 融合候选名次，再按需要重排；重复内容多时可引入多样性选择。每一步都需保持来源版本和限制条件，用标注问题比较召回与答案质量，不默认把所有算法堆上去。',
    'backend/app/repository.py：当前 SQL 检索；检索算法属于扩展方案', '扩展设计')
add([199,224,589], 'retrieval_eval',
    '轻记当前可评估日期、类型、关键词过滤是否找对事件，以及回答是否引用正确 event_id。若增加文档 RAG，再标注每个问题的必要证据，分别统计 Recall@K、首条相关名次和引用正确率，最终检查回答是否保留健康适用边界。仓库没有正式 RAG 评测报表，也不能把 mock 回答通过当作真实召回提升。',
    'backend/tests/test_agent.py: 历史查询测试；advice.py', '现状 + 评估方案')
alias([204,272,374],65)
alias([207],6)
add([208,308], 'unknown_external',
    '当前健康写入在本地事务中，操作键可查已提交结果。若以后外部设备或通知调用中断，连接失败不能证明对方未执行，应先用稳定业务键查询状态；无法确定就保留 unknown 并等待对账或确认。不能一边靠 checkpoint 重放，一边对外部副作用盲目重试。当前没有这样的外部状态对账机制。',
    'backend/app/repository.py: operations、save_events；外部工具为扩展方案', '现状 + 扩展设计')
add([209], 'generalize',
    '从轻记抽通用运行时，应先把健康 Schema、营养和目标策略移到领域包，定义节点输入输出、服务端授权、幂等和状态版本接口，再把模型适配与存储做成可替换组件。用第二个真实场景验证，而不是仅改目录名。当前 graphs.py 仍直接调用健康函数，这种耦合应坦诚说明。',
    'backend/app/graphs.py 的领域导入；schemas.py', '扩展设计')
add([216,288,593], 'differentiation',
    '轻记的差异化集中在健康业务约束：宽松草稿追问、逐字段营养来源、未知值传播、累计目标变化确认、数据版本防旧计划和删除上下文副本。它不追求覆盖通用平台的画布、插件和多租户功能。这些设计与场景匹配，但是否比其他方案更好要看同任务对照，不能说优势绝对。',
    'backend/app/drafts.py；nutrition.py；services.py；repository.py')
alias([221,379],106)
add([222,389], 'handoff',
    '轻记目前没有 Agent 之间的 Handoff。若加入只读知识或报告子任务，我会传 parent/thread ID、目标、事件 ID、数据版本、允许读取字段、限制、预算和结果 Schema，返回证据引用和状态。健康数据库连接、密钥和完整聊天不直接传递，主流程负责验证结果与最终授权。',
    'backend/app/graphs.py: State；advice.py；Handoff 为扩展方案', '扩展设计')
add([223], 'shared_write',
    '当前 Engine 的 RLock 将关键流程串行化，计划激活还比较 data_version 与 base_plan_id；没有并行多 Agent 归并状态。若做并行，优先让各任务返回只读结果，主流程集中提交；必须并发更新时增加行版本和条件更新，冲突重新读取或要求确认，不能让最后写入覆盖用户新目标。',
    'backend/app/graphs.py: Engine.lock；repository.py: decide_plan', '现状 + 扩展设计')
add([226], 'hybrid_evolution',
    '轻记没有经历“向量到混合再到重排”的上线演进；当前是 SQL 结构化检索。未来只有在真实指南问题出现专有词漏召回、同义词表达不匹配或候选排序不佳时，才分别加入关键词、语义召回和重排。每次改变都应固定语料、标注和查询集，以阶段指标验证增益，不能编造演进历史。',
    'backend/app/repository.py: events', '扩展设计；非既有经历')
add([227], 'rewrite',
    '轻记没有独立 Query Rewrite 模型。自然语言历史查询由解析器提取 query_start、query_end、query_kind 和 query_search，再执行本地参数查询；咨询中的“那、刚才”只补近期用户消息帮助指代。若加文档改写，应保留疾病否定、时间、食物和人群条件，避免改写扩大健康问题范围。',
    'backend/app/parser.py: model_parse；graphs.py: respond；advice.py')
add([228], 'retrieval_improvement',
    '本工程没有 Query Rewrite 的线上提升数据。可以给同一查询建立“原句”和“改写句”的对照，检查必要 event_id 或指南证据是否召回。典型失败是将“我不吃牛奶”改成“牛奶营养推荐”，丢掉否定；还需检查相对日期改写是否漂移。未测量时只能讲验证方案，不报提升百分比。',
    'backend/tests/test_agent.py；新增改写评估为方案', '评估方案')
add([229,251,409], 'multimodal',
    '当前轻记不支持图片、语音、视频或图文文档输入。若接餐食照片或标签，可先识别文字和候选食物，带原图位置、单位和不确定性转成 ExtractionEvent，再让用户确认分量；图像识别不能可靠推出实际摄入量。图片表格还要保存行列关系、合并单元格和脚注，不可仅拼接 OCR 文本。',
    'backend/app/schemas.py: ChatRequest、ExtractionEvent；当前文本入口', '扩展设计')
add([231,232], 'ppl_cost',
    '工程未配置 PPL 模型或切分阈值，也无相应成本数据。若试验，应固定模型/Tokenizer/窗口和候选边界，统计每页推理耗时与 Token；先用验证集选择阈值，再按文档结构调整，不能为了提高召回率在测试集上调参。健康指南中的剂量、适用人群和禁忌需保持同段或可追溯关联。',
    '工程无 PPL 实现；候选实验需独立建立', '扩展设计')
add([233], 'offtopic',
    '轻记把每条新消息作为新的 thread_id 工作流，待追问流程单独保存，用户可以另开记录。恢复时只把答案用于 pending_fields，当前反馈不会自动继承到新流程。若用户转为咨询、取消或说不知道，代码按必需/可选信息分别处理。它没有通用语义任务栈，但已经避免把不同餐食的关注点混在一起。',
    'backend/app/graphs.py: run、parse；backend/app/conversation.py')
add([244,245,315,493], 'ontology',
    '轻记的数据本体是五类事件、餐食 item、营养来源、长期事实、目标计划和证据 ID；动作是查询、保存、纠错、确认和删除。它由本项目的 Pydantic 与表结构定义，不来自外部本体平台。模型只能提供受限字段，业务服务计算与执行动作。若接其他业务系统，应写适配器并保留来源和授权，不能把任意模型文本当数据库指令。',
    'backend/app/schemas.py；repository.py；graphs.py')
add([246], 'knowledge_graph',
    '当前没有知识图谱或图数据库。事件间主要通过 event_id、source_event、plan_id 和 evidence_refs 关联，足够追溯一条建议用到的记录。未来若处理食材、过敏原、营养和指南关系，可以增加显式图查询，但必须保留来源版本和适用条件，不能让图中一般关系自动变成个体医学结论。',
    'backend/app/repository.py；services.py: evidence', '现状 + 扩展设计')
add([247], 'rag_tool',
    '当前个人记录查询由固定程序执行，咨询按场景读取，不让模型决定是否跳过数据完整性检查。若增加指南 RAG，可作为只读 Tool 由意图触发；但用户要求查历史时，记录检索必须走确定分支。营养记录保存、目标确认等安全步骤不能作为可选检索动作任由模型跳过。',
    'backend/app/graphs.py: route_chat、respond；advice.py', '现状 + 扩展设计')
add([249,519], 'chunk',
    '工程没有文档切块。若为健康知识库选策略，我会先按标题、段落、表格等结构切分，较短独立说明用固定长度作为基线，跨章节内容用父子层级引用补全，语义切分只作经过评估的增强。每块必须有标题路径、来源和页码，避免把营养建议与人群限制拆开后分别召回。',
    '当前仅结构化事件检索；文档处理为扩展', '扩展设计')
add([252], 'chunk_size',
    '对轻记将来的指南库，小块可能匹配精准但丢适用条件，大块保留语境却增加噪声与 Token。应通过问答样本比较证据完整率、Recall@K、最终引用与回答，而不是只看向量分数。一个可测的办法是细粒度召回，按父章节或相邻块补齐限制；当前工程没有实测最优块大小。',
    '文档 RAG 尚未实现；以健康指南扩展为例', '扩展设计')
add([254], 'mmr',
    '当前没有采用 MMR，因为连向量候选集都未引入，结构化事件通常可以按 ID 去重。未来指南召回若大量返回近重复段落，可评估多样性选择，但不能把补充证据错误当重复删掉。先验证必要事实覆盖和健康限制完整性，再看结果是否更分散，而不是为了算法完整而使用。',
    'backend/app/repository.py: events；advice.py', '现状 + 扩展设计')
add([264,313,501], 'nodes',
    '节点是接收 State、返回字段更新的 Python 函数。Chat 图包含 parse、validate_drafts、prepare_nutrition、save_ready、prepare_clarification、clarify、persist、fact、respond；计划图是 prepare、approve、activate；分析图是 analyze、review。节点可以调模型、查库、计算或 interrupt，但没有通用用户代码节点和动态节点市场。',
    'backend/app/graphs.py: build_chat、build_plan、build_analysis')
add([267], 'agent_node',
    '技术上可把一个受限 Agent 或子图包装为父 Workflow 节点，但轻记当前没有这样装配。输入输出应明确，内部状态使用独立命名空间，遇到人工确认要把等待点传到 API；副作用仍遵守统一幂等。对本项目而言，先把只读知识查询封成节点比让 Agent 自行保存健康记录更稳妥。',
    'backend/app/graphs.py；子 Agent 节点为扩展方案', '扩展设计')
add([270], 'integration_cost',
    '接普通 API 需要鉴权、请求映射、超时与结果校验；接 MCP 还需客户端会话与工具映射；知识库要补解析、索引和评测；业务数据要做字段、权限及版本适配。轻记当前只接兼容模型 API 和本地 SQLite，无法给这些集成承诺固定人天，应按具体系统接口和健康数据边界拆任务估算。',
    'backend/app/parser.py；repository.py；无 MCP/知识库适配器')
add([271], 'kafka',
    '轻记没有 Kafka，当前通知和任务回执都在 SQLite，后台每分钟检查。若以后接设备高频事件或多服务数据同步，可以用消息队列传带 event_id、发生时间、版本和去重键的事件；消费者要处理重复、乱序和补偿。对于现在的单用户少量记录，新增 Kafka 会增加运维，缺乏必要性。',
    'backend/app/scheduler.py；repository.py: jobs、notifications')
add([276], 'stage',
    '当前应定位为可运行的单用户本地原型，具备前后端、持久化和自动化测试，已经超出只有提示词的演示，但还不能称为生产平台。缺少用户隔离、生产监控、标准容量测试和核验营养库，也没有可证明的商业客户使用数据。演示时应展示完整流程和异常恢复，不把原型测试当线上运营。',
    'README.md；start.ps1；backend/tests/')
add([278], 'memory_categories',
    '工程将用户档案、健康事件、明确长期偏好/限制、短期消息和工作流状态分开存储。长期事实只在用户明确表达时保存，事件纠错创建修订，删除会清除相关上下文副本；主观反馈不会自动长期化。当前没有自动从每次对话抽取多层用户画像，也没有通用事实去重、语义冲突归并或向量记忆。',
    'backend/app/repository.py；graphs.py: fact；conversation.py')
add([286], 'file_eval_extensions',
    '轻记没有文件处理和文档 RAG 扩展，已有的是自然语言事件解析、结构化上下文和业务回归测试。可真实说明的扩展是 Pydantic 草稿/严格事件分层、营养来源校验、建议边界验证；如果要加文件问答，需要另建解析与检索评测，不能把读取个人记录称作已实现完整文件 RAG。',
    'backend/app/schemas.py；nutrition.py；advice.py；backend/tests/')
add([292], 'runtime_or_console',
    '轻记网页直接承接个人记录、纠错和咨询，是业务应用入口，也包含档案与计划设置。它不是只管配置的通用管理台。运行执行在同一个 FastAPI 进程里的 Engine，设置页更新档案与偏好后影响数据版本；当前未拆控制面和运行面，也没有共享 Worker 服务。',
    'frontend/src/App.tsx；backend/app/main.py；graphs.py')
add([295], 'fit_agent',
    '本项目最适合 Agent 的环节是把自然语言拆成跨日期多事件、识别纠错和缺字段、理解饮食咨询。营养求和、版本检查、默认目标和是否保存更适合代码执行。这样使用模型的语义能力，同时让用户补记、改口和取消时有明确边界，而不是为了增加自主性把每一步都交给模型。',
    'backend/app/parser.py；drafts.py；graphs.py；targets.py')
add([297,320,341], 'mandatory',
    '轻记把强约束放进固定图和写入接口：所有新餐食要先经过草稿校验与营养准备，PreparedEvent 不通过就不能保存；计划激活调用 decide_plan 核对确认、数据版本和基准计划。模型即使说“已保存”或“跳过审批”也不能直接改库。将来开放 Agent 时同样需要执行端门禁，Skill 描述不能替代。',
    'backend/app/graphs.py: prepared_record、persist、activate_plan；repository.py: decide_plan')
add([298,321,583], 'completion',
    '轻记任务完成要看业务后置条件：记录 ID 能在 events 找到、营养未知被明确标识、卡片数值来自保存值；计划任务还要区分 active、rejected、stale。waiting_input 表示尚需用户，不能计为完整保存。未来可把这些条件声明成机器可校验契约；当前 completed 是图执行完毕，仍需结合结果语义判断业务是否达成。',
    'backend/app/graphs.py: format_result、respond、activate_plan；repository.py')
add([299,322], 'validation_failed',
    '严格事件校验失败时不允许写库；必需信息缺失则追问，用户取消则保留已完成独立记录。建议表达校验失败会本地兜底，记录可以仍然成功；过时计划返回 stale 且目标不变。评测要分记录成功、表达降级和目标未采用，不能把 HTTP 200 或图 completed 一律算作所有业务成功。',
    'backend/app/graphs.py；advice.py: validate_advice；repository.py: decide_plan')
add([304,305,579], 'checkpoint_content',
    '图快照由 LangGraph SqliteSaver 保存，包括 State 和执行进度：extraction、待补字段、事件下标、prepared_events、record_ids、计划数据等。自己维护的 health.db 保存事实、workflow API 结果、请求指纹、回执和操作去重。checkpoint 用于继续执行，Repository 决定业务事实是否已提交；两者不是同一种存储，也不是同一事务。',
    'backend/app/graphs.py: State、__init__；repository.py')
add([309,354], 'idempotency_vs_lock',
    '轻记 Engine 的锁防止当前进程内同时执行关键流程，Repository 的操作键和事务保证同一业务操作重放不重复；二者职责不同，锁释放后重试仍可能再次到达。运行层负责稳定传播键，写入服务负责原子去重。跨系统工具也要在副作用所在系统实现幂等，仅 checkpoint 或分布式锁无法保证。',
    'backend/app/graphs.py: run、persist；repository.py: save_events')
add([311,312], 'fencing',
    '工程目前没有 fencing token 或 Worker CAS。若引入多 Worker，应在持久任务表认领时由数据库发放单调递增代次，更新语句同时匹配任务 ID 和代次，影响行数为零就拒绝旧写入；外部资源也需支持相同约束。代次与 lease 过期时间一起使用，不能用进程内计数或客户端时间替代。',
    '当前仅 graphs.py: Engine.lock；分布式并发为扩展设计', '扩展设计')
add([314], 'nutrition_node',
    '较复杂的节点是 prepare_nutrition：先校验完整事件，根据稳定事件下标读取缓存或旧记录，比较食物、分量、做法指纹，保留有效用户营养，缺字段才调用粗估器，再验证数值范围、热量一致性和来源。结果先放 State.prepared_events，再由保存节点读取 PreparedEvent；失败时营养未知而非虚构零值。',
    'backend/app/graphs.py: prepare_nutrition；nutrition.py: prepare_nutrition')
add([316,371], 'task_state',
    'Engine 启动后复用已编译图，但每次新请求生成 UUID thread_id，checkpoint 按这个 ID 存独立 State；conversation_id 用于消息归组，不等同于图状态或身份。图中的模型和 Repository 由 Engine 持有，不能放进 State。当前仍共享单用户数据集，若做多用户还必须额外增加身份隔离。',
    'backend/app/graphs.py: run、State；repository.py: messages')
add([317], 'compile_cache',
    '轻记没有每请求动态 DSL 编译，三张图在 Engine.__init__ 时构建一次，后续复用，所以当前主要耗时不在编译。如果以后允许用户流程，应按不可变流程版本缓存编译结果，并区分图结构与每个 thread 的 State；升级时需要处理旧状态兼容，不能为了缓存复用错用户数据。',
    'backend/app/graphs.py: Engine.__init__、run')
add([319,461,462], 'trace_eval',
    '轨迹说明发生了什么，评测还需检查结果。轻记可以用 process_trace 定位是否走了营养准备和建议兜底，再直接查数据库验证事件数量、字段来源和计划状态。表达自然度可以人工或 Judge 评估，幂等和确认则必须用确定断言。多条合法路径应允许，不能要求所有正确任务都与一份固定轨迹逐字相同。',
    'backend/app/graphs.py: process_trace；backend/tests/；advice.py')
add([323], 'freedom_cost',
    '将轻记的健康写入封为受约束 Workflow 会限制模型自由跳步，但换来必需字段、来源和确认不变量。代价是研发维护 Schema、流程分支、测试与版本兼容，面对新场景灵活性较低。适合稳定高影响动作；知识探索可以更开放，但结果要回到同一写入边界，不能自由生成数据库操作。',
    'backend/app/graphs.py；schemas.py；backend/tests/')
add([324,325], 'migration',
    '当前没有运行任务跨 Worker 迁移工具。若升级轻记，应先停止接收新任务，备份并验证业务库与 checkpoint 库、记录旧代码和 Schema 版本，再在兼容新进程恢复；无法迁移的待确认流程应提示重新生成而非强行激活。还要有超时巡检和失败回执，避免任务永远 running。不能把复制单个 db 文件视作完整在线迁移方案。',
    'backend/app/graphs.py: run、resume；repository.py；迁移为扩展设计', '扩展设计')
add([326], 'ownership',
    '轻记是当前可检查的本地工程，应单独描述其流程和验证范围。若本人同时参与公司平台，要另列职责、团队资产与独立实现，不把原型的测试当公司生产指标，也不把团队已有能力认领为个人新增。代码、设计说明、提交和可复现测试可以辅助证明贡献，组织和客户信息需要本人提供。',
    'README.md；Git 提交；个人项目边界待本人确认', '个人事实待补')

add([331,504], 'real_code',
    '轻记的真正业务逻辑在 Python 代码中：检查缺失字段、合并限定补丁、营养单位换算、保存修订、计划变化比较和归档。图用 Python 装配，页面只是收集输入与展示结果。即使以后增加 YAML 或 Skill 描述，也只能声明接口与策略，状态恢复、事务和健康约束仍需要研发实现并测试。',
    'backend/app/drafts.py；nutrition.py；repository.py；services.py')
add([332,487,502], 'code_workflow',
    '轻记的 Workflow 是可执行 Python 图：Engine 启动时把节点函数绑定到 StateGraph，添加固定和条件边，compile 时注入 SqliteSaver。当前没有读取 Markdown 自动执行脚本的 Skill 运行器。若对外包装 Skill，描述文件说明用途和契约，执行入口则调用这张图，返回 completed、waiting_input 或 failed 结果。',
    'backend/app/graphs.py: build_chat、build_plan、graph_for')
alias([336],69)
add([337], 'reliability_quality',
    '轻记既需要运行可靠，也要保证输出不越过事实。前者有 checkpoint、幂等和计划版本；后者有 Schema、营养来源、结构化上下文及数字/完整性校验。例如记录已保存后表达模型失败，应保留事实并本地回复。本人实际负责哪部分需如实说明，不能因为了解架构就认领全部代码。',
    'backend/app/graphs.py；nutrition.py；advice.py；个人职责待确认')
add([338,488,603,624], 'workflow_control',
    '当前轻记没有外层 ReAct 交接控制权；API 选择 chat/plan/analysis 后直接执行对应图，等待时返回 pending_input。若增加外层 Agent，可将健康记录 Workflow 暴露为受限能力：调用后保存 child/thread 引用，等待用户时暂停父任务，完成后只返回事件 ID、状态和卡片；不能把等待误报成最终成功。',
    'backend/app/graphs.py: run、format_result、resume', '现状 + 扩展设计')
add([339], 'trigger',
    '轻记结合两类触发：模型理解文本产生 intent，程序根据必需信息、纠错目标和事件是否完整选节点；计划与复盘也可以通过明确 API 或后台任务触发。咨询场景由本地正则分类。模型不能决定跳过 PreparedEvent、累计变化确认或版本检查，所以语义路由和业务门禁分开。',
    'backend/app/graphs.py: route_chat；advice.py: classify_scene；main.py')
add([340], 'entry_paths',
    '自然语言走 POST /api/chat 与 Chat 图，计划页走 /api/plans/propose 与 Plan 图，复盘走 /api/reviews 与 Analysis 图，恢复统一走 workflows/{thread_id}/resume。它们共享 Engine、Repository 和领域服务，但并非所有页面动作都先进入同一 ReAct Loop，也没有显式 Skill 选择入口。',
    'backend/app/main.py；graphs.py: graph_for')
add([342], 'missing_step',
    '当前固定图保证营养准备排在两条保存路径之前，prepared_record 还要求状态里存在且通过 PreparedEvent 的结果，缺少就拒绝保存。未来动态 Agent 若遗漏步骤，应由后置条件检测并进入受限补救流程；没有可补证据就返回未完成，不能仅靠模型在回复中承诺“已检查”。',
    'backend/app/graphs.py: prepared_record、save_ready、persist')
add([344], 'beyond_prompt',
    '针对缺食物与追问漂移，工程措施是把 ExtractionEvent 与 EventDraft 分开、由本地检查必需字段、checkpoint 保存首次日期和草稿、merge_patch 只允许改 pending_fields。针对营养重放还保存 prepared_events 并用业务操作键去重。测试覆盖“昨天吃咸了”、跨午夜、混合记录和提交后重试，超出单纯修改提示词。',
    'backend/app/drafts.py；graphs.py；backend/tests/test_draft_clarification.py')
add([345,372], 'repeat_injection',
    '轻记没有“每时刻激活一个 Skill”的调度机制。每次模型请求独立构造规则与数据，不把整段系统提示追加进聊天历史；当前工作流用 guided_topics 记录已给过的反馈建议，避免追问时重复。若以后引入 Skill 多轮使用，可保存能力 ID/版本并按需要重建上下文，而不是每轮累加同一说明。',
    'backend/app/parser.py: model_request；graphs.py: guided_topics；replies.py')
add([346,347], 'history_storage',
    'messages 保存用户输入、助手回复、卡片、证据、轨迹和耗时；模型提示与完整 State 不直接作为聊天消息展示，State 另存 checkpoint。当前用户 raw_text 可能被保存，未做通用敏感字段预过滤，所以不能声称“所有隐私已脱敏”。若需不保存某类输入，应在首次入库前过滤并明确对恢复功能的影响。',
    'backend/app/repository.py: message；graphs.py: run、resume')
add([348], 'create_update',
    '新记录和纠错分开：新记录生成 UUID，纠错必须定位唯一已有 event_id，一次只更新一条完整事件，version 递增并保存 revisions。模型给出的 ID 必须在可用记录中，歧义先追问。修改食物、分量或做法时重新校验营养指纹，不能复制旧估算到新分量。',
    'backend/app/parser.py: model_parse；repository.py: save_events；nutrition.py')
add([349], 'out_of_scope',
    '轻记能处理记录、查询和普通饮食建议；医疗诊断、药物调整和未支持的外部动作应明确说明范围。当前代码对部分医疗词有边界提示，不能当成完整医学识别器。表达模型生成不受支持数字或诊疗结论时会被拒绝并本地兜底，不能为了显得有帮助而编造事实或保存空记录。',
    'backend/app/replies.py: medical_question；advice.py: validate_advice')
add([350,351], 'artifact',
    '轻记的结构化输出主要是餐食卡片、计划与复盘 JSON，分别由保存值和业务函数生成，不是任意文件/布局 Artifact。普通 reply 负责解释，卡片负责显示数值。用户要求修改不支持字段时，应说明当前可修改范围并走明确接口；不能只改变文字却让数据库保持旧值，也不应把未知字段直接写进 JSON。',
    'backend/app/graphs.py: respond、format_result；services.py: meal_assessment；schemas.py')
add([356,375], 'workspace',
    '轻记每个任务有独立 thread_id State，但没有为任务创建独立文件工作目录；所有本机用户数据共用 data 目录。这适合当前单用户，不能证明跨租户文件隔离。若引入上传与代码执行，应按用户和任务分目录、校验路径范围、禁止任意宿主路径访问，并将临时文件生命周期与业务事实分开。',
    'backend/app/graphs.py: run；config.py: data_dir', '现状 + 扩展设计')
add([359,361,495,613], 'buy_or_build',
    '轻记选择自定义流程，是为了把健康记录、来源与确认这些强约束直接实现并验证；通用 Agent 加领域工具也可能满足需求。是否值得自建，应比较同任务正确率、开发维护成本、可追溯性和用户记录体验。当前没有付费客户或对照商业数据，不能声称客户必然为自研买单；若通用方案更合适就应复用。',
    'README.md；backend/tests/；尚无付费和对照数据')
add([362], 'custom_cost',
    '避免把轻记做成高成本定制，应把事件 Schema、暂停恢复、幂等、营养来源和计划版本作为稳定资产，把目标公式、提醒和统计模板单独配置或替换。新增场景要有明确验收和适用边界；不应为每个用户复制整套图或堆提示词。当前仍有健康逻辑与 Engine 耦合，需要第二场景验证后再抽通用内核。',
    'backend/app/graphs.py；services.py；targets.py', '演进设计')
add([363,554], 'autonomy',
    '轻记的写健康事实和变更目标属于稳定且高影响动作，适合固定 Workflow、严格字段与用户确认；探索不同餐食选择可给模型一定表达空间，但必须使用当前证据。当前并无自由浏览器或代码 Agent。自主程度应由任务是否可验证、能否撤销和健康影响决定，而不是把所有场景都改成同一个开放循环。',
    'backend/app/graphs.py；advice.py；repository.py: decide_plan')
add([385], 'forget',
    '目前遗忘按保留期和用户删除执行，而非模型给记忆打价值分。完成流程等上下文超过 30 天清理，待补流程超过 366 天清理；超过一年的完整过期月份转月度摘要并删除细节。facts 没有自动语义衰减。将来要加遗忘，过敏和专业限制不能因使用频率低就自动删除，需要用户确认或明确失效依据。',
    'backend/app/graphs.py: prune_context；services.py: archive；repository.py: facts')
add([391,513,514], 'parent_child',
    '当前轻记三张图是独立入口，没有父子图依赖恢复。若加子流程，应持久 parent_id、child_thread_id、依赖步骤和等待原因，子流程 waiting_input 时父流程不能继续，完成后按结果 Schema 恢复父节点。重启要先恢复依赖关系，副作用仍按稳定操作键去重；子任务失败应明确终止、补救或等待处理。',
    'backend/app/graphs.py: 当前三图独立；父子流程为扩展设计', '扩展设计')
add([395,524,525], 'retrieval_choices',
    '轻记当前用结构化 SQL 查询，因为记录有日期、类型和事件 ID。检索代码时精确词可用文本搜索，文件路径用文件匹配；层级文档可以按目录/章节定位，语义表达复杂且规模较大时才考虑向量召回。没有一种索引适合所有数据，关键看能否找到完整证据；本工程未实现 PageIndex 或文档向量 RAG。',
    'backend/app/repository.py: events；文档与代码检索为设计比较')
add([398], 'sleep0',
    '这是 Java 基础题，轻记后端实际用 Python。Thread.sleep(0) 不会释放持有的监视器锁，也不能保证切换到某个线程，不能作为线程同步机制。轻记后台用 await asyncio.sleep(60) 暂停协程并让出事件循环；它与 Java 线程调度不是同一层概念，不能用 sleep 替代数据库锁或版本检查。',
    'backend/app/scheduler.py: scheduler_loop；Java 为知识题', '基础知识 + 工程关联')
add([399,400], 'rearrange',
    '用计数器统计频次，以最大堆每次取出当前最多且不是上一项的元素，前一项暂不放回，见附录 E。若最大频次超过 ceil(n/2) 则无解，否则可构造；复杂度约 O(n log k)，k 为不同元素数。轻记没有使用该算法，应作为独立编码题回答，不能称它为提醒任务调度实现。',
    '附录 E；工程未实现该排列逻辑', '算法知识 + 工程关联')
add([401], 'hobby',
    '兴趣和最近阅读需要本人如实补充，工程不能证明。若确实喜欢个人工具开发，可以用轻记说明如何从自然语言记录需求做出可运行原型，再讲一个实际阅读后应用的设计，例如 interrupt 重放需要业务幂等。没有读过的书、论文或源码不要为贴合项目而列入。',
    '个人兴趣与阅读记录待补', '个人事实待补')
add([407], 'recover_context',
    '分三种：本轮未选入提示词的健康事件仍在 SQLite，可按日期或 ID 再查，但不会自动全文回填；被 30 天清理掉的消息/checkpoint 无法恢复，已保存事件通常还在；一年月度归档删除逐餐原文后，只能读摘要，细节回不来。工程没有摘要自动反查原文的通用工具，不能说所有压缩信息都可恢复。',
    'backend/app/advice.py；graphs.py: prune_context；services.py: archive')
add([408], 'compression_when',
    '没有按 Token 水位触发的同步或异步压缩。每次模型调用前，程序同步筛选本次场景的数据；后台 tick 的每日维护任务调用 prune_context 和 archive，按时间清理或做月度归档。二者触发条件不同：筛选不删原数据，保留期清理和归档则会删除相应内容。后台服务不运行时维护不会实时执行。',
    'backend/app/advice.py；scheduler.py: tick；graphs.py: prune_context')
add([410], 'vision_switch',
    '轻记当前仅调用配置中的文本模型，没有视觉模型选择或自动切换。若接餐食图像，应在 API 层识别输入类型，交给经验证的视觉适配器，再转入同一草稿校验链路。识别到食物不等于知道食用量，用户确认和来源标记仍必要，模型选择还要考虑数据外发权限。',
    'backend/app/parser.py；schemas.py: ChatRequest', '扩展设计')
add([412], 'freshness',
    '轻记的实时数据来自用户新增或纠错后的 SQLite 记录，data_version 递增让旧规律失效；咨询每次从库重建上下文。计划草案确认时核对数据版本与基准计划，过时就拒绝激活。当前没有实时设备流或外部知识增量索引，历史月度摘要也不能用于精确的逐日复算。',
    'backend/app/repository.py: _bump、decide_plan；advice.py；services.py')
add([413], 'manual_plan',
    '轻记网页让用户编辑目标和确认计划，不提供流程画布。自然语言主要生成记录草稿，plan_draft 用本地规则生成目标差异与七天安排；它不是模型生成任意执行 DSL。将来人工编排强调明确节点和边，自然语言计划需要多一层受限结构校验与用户确认，两者最终仍应执行同样的门禁。',
    'frontend/src/App.tsx: Plans；backend/app/services.py: plan_draft')
add([415,417], 'async_tool',
    '当前无视频生成等异步外部工具，聊天模型调用在请求内同步完成。若接长耗时任务，应提交后保存外部 job_id、幂等键与回调/轮询状态，返回 waiting_external；后台核实完成后写结果并恢复图。回调需认证与去重，轮询需退避和截止时间，不能让前端连接承担任务生命周期。',
    'backend/app/graphs.py；scheduler.py；异步外部工具为扩展设计', '扩展设计')
add([416], 'plan_state',
    '轻记业务计划存在 health.db 的 plans 表，含目标、安排、证据、data_version 和 base_plan_id；Plan 图执行状态另存 checkpoint。等待确认后通过同一 thread_id 恢复；确认前发生数据变化会 stale。当前不是多步骤自主 Planner，也没有外部异步步骤依赖图，业务计划数据与执行计划要区分。',
    'backend/app/repository.py: plans、save_plan；graphs.py: build_plan')
add([418,421], 'resume_routing',
    'API 路径里的 thread_id 找到工作流，恢复请求带 interrupt_id 匹配当前等待点，request_id 和指纹用于恢复去重，再用 Command(resume=answer) 调相同图。文字补充与计划 approved 有不同校验。当前只恢复用户等待；若增加外部 job 回调，还需持久 job_id 到 thread/等待点的映射与回调认证。',
    'backend/app/graphs.py: resume；schemas.py: ResumeRequest')
add([419,559], 'hitl',
    '人工介入点是 clarify 与 approve_plan：前者补必需字段或消除纠错歧义，后者确认首份计划或超过 5% 的单次/累计目标变化。interrupt 返回问题并保存等待状态；恢复接口校验 thread_id、interrupt_id 和输入类型，确认时再次检查版本。尚未实现所有高风险外部动作的通用审批系统。',
    'backend/app/graphs.py: clarify、approve_plan、resume；services.py: plan_draft')
add([420], 'user_steering',
    '补充回答通过 merge_patch 只修改待补字段，原日期和已知食物保留；用户可取消未完成草稿，已保存独立记录保留；新独立记录走新 thread。计划等待时只能明确接受或拒绝，修改方向需要重新提出草案；当前没有任意重规划编辑器。这样能避免一句补充意外改掉另一餐或正在生效目标。',
    'backend/app/drafts.py: merge_patch；graphs.py: parse、resume；main.py: cancel')
add([423,448,518], 'large_sheet',
    '当前不支持 Excel 上传。若导入十几万行健康或设备数据，应分批解析，保留表头、单位、类型和行号，先写暂存表并做异常校验，用户确认后幂等导入。统计查询交给数据库，只有筛选后的汇总交给模型；不能把整个表拼进提示词，也不宜逐行向量化后让模型算总量。',
    'backend/app/main.py：无文件导入；repository.py 可作为后续落点', '扩展设计')
add([424], 'bfs',
    '二叉树层序遍历用队列，每轮取出当前队列长度个节点作为一层，按左右子节点入队；时间 O(n)，队列空间 O(w)，w 为最大层宽。代码见附录 F。轻记工作流虽然也是图，但 LangGraph 负责调度，不使用此二叉树算法；原题信息不完整时只给标准版本，不假造现场输入。',
    '附录 F；backend/app/graphs.py', '算法知识 + 工程关联')
add([426,433,605], 'backend_experience',
    '当前可用轻记证明的后端工作是 FastAPI API、Pydantic 数据约束、SQLite 事务与修订、幂等、调度任务和恢复测试。工程没有 Java 微服务、通信控制平台或工业大数据模块。若本人有其他项目，应单独提供真实职责与技术证据；不能把本地健康应用改写成企业历史经历。',
    'backend/app/main.py；repository.py；scheduler.py；其他履历待补', '工程证据 + 个人事实待补')
add([427,428,430,431,432], 'communication',
    '轻记没有 SIP、VoIP、坐席或通话状态服务，因此这题只能作为迁移设计。若给健康应用增加实时咨询，先明确文本、音视频和连接可靠性需求，通信组件管理连接/媒体，业务服务管理授权与会话状态；事件携带会话 ID、序号和版本，处理重复乱序并定期对账。不能从现有轮询网页推断已有实时通信平台。',
    '当前 main.py 仅 REST；通信链路为扩展方案', '扩展设计；非既有经历')
add([434], 'device_ingest',
    '轻记现在的健康事实由用户文字或表单输入，没有设备上报链路。若接体重秤或运动设备，应校验设备归属，事件带设备 ID、用户 ID、发生/接收时间、来源版本与去重键，先保存原始输入再规范单位。缺失、重复和乱序都要可观察，不能让设备新值悄悄替换用户档案或手动纠错。',
    'backend/app/schemas.py；repository.py；设备接入为扩展设计', '扩展设计')
add([435,443], 'storage_choice',
    '当前使用 SQLite，没有时序数据库或 Redis 中间计算。五类低频个人事件按 day/kind 查询，events_day 索引与本地计算满足原型需求。若设备高频数据增长，可再评估时序存储；Redis 可做缓存或临时聚合，但健康事实和可恢复状态不能只依赖易过期缓存，需保留持久真值。',
    'backend/app/repository.py: __init__、events；services.py')
add([436], 'missing_data',
    '轻记对漏记不当零，用户显式标记 diet_complete 后才用于完整摄入分析。若扩展设备上报，应比较设备序号、预期采样、接收记录和处理回执，按 device/event ID 定位入口、队列、处理或存储丢失；补数要幂等并保留发生时间。没有数据不能直接推断用户没有运动或没有进食。',
    'backend/app/services.py: analyze；repository.py: mark_complete；设备链路为扩展')
add([437], 'metric_config',
    '用户当前可配置目标、权重和提醒时间，统计逻辑由 services.py 的固定模板决定，没有可编辑计算表达式。若开放指标定义，应使用受限配置说明输入字段、过滤、窗口、单位和规则版本，不允许用户任意执行 Python；修改后保留旧版本并明确哪些历史结果要重算。',
    'backend/app/schemas.py: Preferences、Targets；services.py')
add([438,439], 'windows',
    '轻记用的是按上海时区日历日期构造的查询窗口：日汇总、7 天复盘、28 天规律和 14 天权重建议，不是流式引擎的滑动窗口。窗口读 SQLite 事件并排除不完整日；没有引入水位线或迟到流。补记会使派生结论失效，再生成分析；当前低频记录没有必要增加流式窗口系统。',
    'backend/app/services.py: history_window、analyze、review、suggest_weights；clock.py')
add([440], 'aggregate',
    '中间汇总把事件转成每日 totals、体重、睡眠、运动和完整性标记，供页面、建议和复盘复用，避免模型分别算出不一致数字。当前 dashboard 按需计算，没有独立物化汇总集群。每项营养未知时保留 None，只有满足完整性条件才算均值或剩余额度，这比单纯优化查询更重要。',
    'backend/app/services.py: dashboard、history_window；nutrition.py: meal_totals')
add([441,442], 'recompute',
    '当前新增或纠错会递增 data_version 并把 active insights 标 stale，后续分析重新计算；结果保存 rule_version、data_version，业务计划另核对基准版本。若改指标定义，应保存不可变规则版本和受影响日期范围，重算时验证统计口径，不能直接覆盖历史结论。归档后无原始细节，不能保证所有新指标都能追溯重算。',
    'backend/app/repository.py: _bump；services.py: analyze、archive')
add([446], 'framework_level',
    '轻记需要精确掌握何时暂停、哪些独立事件已保存、恢复时如何避免重复，以及计划确认的数据版本，所以选显式 StateGraph，而不是把业务边界藏在高度自动化的代理里。这是针对当前需求的判断，并没有做所有框架同任务实测；已有服务函数仍可复用，未来可依据维护成本调整编排方案。',
    'backend/app/graphs.py；drafts.py；repository.py')
add([449,523], 'cross_chunk',
    '轻记事件按 ID 关联而不是文档 Chunk。若未来指南答案跨章节，需要在索引保存父章节、表格脚注和引用关系，召回后沿关系补证据，再检查必要事实是否齐全。不能仅靠增大 TopK 把所有文档灌给模型，也不能把不相关邻段当证据；营养人群限制尤其要与建议一起返回。',
    '当前 services.py: evidence；文档关联为扩展设计', '扩展设计')
add([450], 'metadata',
    '轻记已有类似元数据过滤的参数化查询：day 起止、kind 和关键词，返回 event_id 与版本。未来文档检索还可过滤来源、发布日期、适用人群和授权范围；过滤必须在服务端实现，不能只写在提示词。限制过严可能漏证据，因此评测应同时检查权限正确性与召回覆盖。',
    'backend/app/repository.py: events；main.py: GET /api/events')
add([452,521], 'chunk_eval',
    'PPL 切分在轻记尚未实现，可能受模型、上下文和阈值影响，也可能破坏列表、表格或禁忌条件。要证明有效，应固定文档版本和问题集，与结构切分/固定切分对比必要证据覆盖、Recall@K、引用准确和耗时，人工抽查健康语义边界。没有实测不能宣称它一定优于标题切分。',
    '工程无 PPL 模块；此处为验证方案', '评估方案')
add([453,454], 'recall_drop',
    '轻记当前没有 Recall@5 监控。若扩展检索后突然下降，即使用户和配置未变，也先核对原文/索引版本、Embedding 实际模型与维度、服务端默认参数、过滤、删除同步和评测标注，再分阶段回放召回与排序。保留固定样本和中间候选，避免一开始只改 Prompt；本项目尚无这种线上事故证据。',
    '当前 repository.py 结构化检索；故障排查为扩展设计', '扩展设计')
add([455,456], 'embedding_stability',
    '工程没调用 Embedding。对于同输入是否稳定，需要固定文本归一化、模型版本、Tokenizer 与参数，重复请求比较维度、范数、相似度和最近邻排序；不同版本或服务更新可能改变结果。向量输出一致与业务幂等是不同问题：轻记的幂等由 request_id、操作键和数据库事务保证，不依赖模型输出重复相同。',
    'backend/app/graphs.py: fingerprint；repository.py: save_events；Embedding 为扩展')
add([457,458], 'reranker',
    '轻记没有使用任何 Reranker，不能填写一个未接入的模型名。若需要重排指南，先固定候选文档和版本，重复测分数与顺序，记录推理配置并测试并列分数的稳定规则。最终选择还要衡量证据覆盖、延迟和成本；重排稳定不等于答案一定正确，仍需来源与适用条件验证。',
    '当前 backend/app/advice.py 无重排器', '扩展设计')
add([459], 'format_constraints',
    '如果模型参与候选排序，应限定只能输出现有候选 ID，检查重复、数量、缺失和排序结果格式，失败回退可解释的原排序。轻记已经采用相同原则验证 correction_id 只能来自可用记录、输出必须符合 Pydantic；这是可复用方法，但尚未实现 Reranker 排序。',
    'backend/app/parser.py: model_parse；schemas.py；重排为扩展')
add([466,467], 'coding_compare',
    '轻记没有在相同任务下系统比较 Codex 与 Claude Code，也不能由仓库判断其当前产品差异。可说明行为同时受到模型、提示、可用工具、权限、上下文和恢复机制影响；若要归因，应固定模型/工具/任务，只改变一个因素。对本工程最有用的观察点是生成代码是否保持数据不变量、测试和可恢复性，而不是品牌排名。',
    '本工程未保存相关对照实验；不推断第三方产品能力')
add([469,596,597,540], 'work_focus',
    '轻记能展示近期 Agent 业务应用工程：模型接入、图编排、健康数据与前后端落地。它既不是模型训练项目，也不是独立通用框架产品。本人是否长期做 Agent、何时转型、是否为当前主业，需要结合真实任职时间线补充，不能把一个可运行项目等同于全部研发经历。',
    'README.md；个人工作时间线待本人补充', '工程口述 + 个人事实待补')
add([471,611,641,642], 'ai_coding',
    '当前对话能证明本项目可在 Codex 中检查、生成资料和协作，但无法证明整个工程最初由谁编写、团队用哪些其他工具或 AI 代码比例。回答时应如实说明本人设计、审查、修改和验证的部分，并能现场解释关键逻辑。AI 生成的代码仍需检查恢复、营养来源与删除行为，比例没有统计就不报数字。',
    '当前会话与工程代码；作者分工和工具使用数据未提供', '个人事实待补')
add([474], 'domain_modules',
    '轻记模块按业务职责划分而不是按 Agent 人设：drafts 管完整性，nutrition 管营养，targets 管目标，advice/replies 管咨询表达，services 管统计计划，repository 管数据。它们都服务同一个健康域；跨行业时才考虑独立领域包。这样的划分有利于单独验证数值、来源和恢复，不必每个模块都变成独立服务。',
    'backend/app/ 目录；README.md 结构说明')
add([481,618], 'react_core',
    '标准 ReAct 是模型决定最终回答或工具调用，执行工具并返回 Observation，直到完成或触及预算。轻记当前不采用这种开放循环：模型抽取意图后由固定图执行，只有澄清回到解析。若实现，可参考附录 C，但健康写入和计划变更必须调用已有受约束服务，不能把自由循环直接接数据库。',
    '附录 C；backend/app/graphs.py: build_chat', '概念回答 + 工程边界')
add([482], 'observation',
    '轻记没有统一的 ReAct Observation 消息，节点通过 State 传递 parsed、prepared_events、record_ids、cards 和 evidence_refs，轨迹另存 process_trace。若标准化工具结果，应含调用 ID、状态、结构化数据、证据 ID、版本和可重试错误，正文避免健康原文扩散。结果里“保存成功”必须来自事务结果而非模型声称。',
    'backend/app/graphs.py: State、format_result；repository.py')
add([486], 'adapter_layer',
    '工程当前没有 MCP 或外部业务适配层，模型 API 适配在 Parser，本地业务动作由 Repository 和服务函数完成。未来 MCP/OpenAPI 连接可放在能力适配层，身份和最小权限由入口及执行端共同校验，领域规则仍在业务服务。不能把安全完全交给协议或 Skill 描述，也不能把数据库连接直接暴露给模型。',
    'backend/app/parser.py；graphs.py；repository.py', '现状 + 扩展设计')
add([497], 'canvas_to_harness',
    '轻记没有从画布平台迁移的历史。就需求而言，缺字段后跨天恢复、数据库提交后重放去重、累计目标确认和删除多份上下文，都会推动运行层承担更细的状态语义。但是否另建 Harness，应先验证现有平台能否通过这些任务；能复用就复用，不能因为流程复杂就默认全部重写。',
    'backend/tests/test_agent.py；graphs.py；无画布平台迁移记录')
add([499], 'migration_compat',
    '工程没有原平台 Workflow 的导入器。若把旧流程迁入轻记式运行环境，需映射节点 Schema、变量引用、错误处理、异步等待和确认语义，尤其验证旧写入动作能否提供幂等键。先用同输入比较最终事实与合法轨迹，再逐步迁移；不能只把 DSL 转成图，就认为运行语义完全相同。',
    'backend/app/graphs.py；schemas.py；迁移为扩展方案', '扩展设计')
add([509], 'tech_position',
    '产品定位是自然语言驱动的个人减脂记录与复盘原型，技术采用 React/TypeScript 前端、FastAPI/Pydantic 后端、LangGraph 编排、SQLite 事实与 checkpoint，以及兼容模型 API。选型优先本地可运行和业务可靠性，暂未引入 SQLAlchemy、Alembic、Redis、Kafka、向量库或多用户平台。',
    'README.md；backend/requirements.txt；frontend/package.json')
add([511], 'canvas_difficulty',
    '轻记没有画布编辑器，已有难点是一个流程同时支持缺字段、独立事件先保存和重启继续。如果开发画布，最难的不只是拖线，而是保证变量类型、循环边界、分支合流和暂停恢复语义一致，并确保所有写入路径经过必需校验。应把这些不变量转为编译检查和回归用例。',
    'backend/app/graphs.py；drafts.py；backend/tests/', '工程关联 + 扩展设计')
add([516], 'container_isolation',
    '当前轻记不使用 Docker/Kubernetes 沙箱。容器通常通过进程、文件和资源隔离承载任务，但不自动等同完整安全边界；仍需非特权运行、只读挂载、网络出口限制、资源配额和执行时限。若运行健康脚本，尤其不能把真实数据库和模型密钥暴露给任意代码。',
    '当前无容器执行配置；此处是后续部署设计', '扩展设计')
add([526,527], 'stream_nodes',
    '轻记当前所有 API 以完整 JSON 返回，没有统一流式节点接口，也没有研究记录证明实现参考过 Dify 内部抽象。若增加流式，模型节点可以发内容增量，本地计算节点发状态事件，最终卡片只在业务校验完成后发；事件应带 task_id、seq 和 kind，使客户端区分过程文本、等待点和最终事实。',
    'backend/app/main.py；graphs.py: format_result', '现状 + 扩展设计')
add([529,571,632], 'worker_instances',
    '当前只有一个本地 FastAPI/Engine 进程，没有 Pod、共享 Worker 池或每用户常驻 Agent。逻辑任务以 thread_id 表示，不需要一任务一进程。若云化，可让 Worker 复用编译图并从持久存储加载任务状态，但用户数据必须独立授权隔离，不能仅靠不同 thread_id 防止串数据。',
    'start.ps1；backend/app/main.py: lifespan；graphs.py: run')
add([530,531], 'python_concurrency',
    '轻记的 FastAPI 同步 def 接口可由线程执行，后台 scheduler_loop 是协程并用 asyncio.to_thread 调 tick；模型用同步 httpx，关键流程还持有 RLock，所以并发首先受锁与模型等待影响。常规 CPython 的 GIL 限制 Python CPU 密集线程并行，但不等于 I/O 无并发；需要多核计算时应评估独立进程，而不是只改 async 关键字。',
    'backend/app/main.py；parser.py: model_request；scheduler.py；graphs.py')
add([532], 'latency',
    '轻记接口变慢时先分解：等待 Engine 锁、模型 45 秒超时、营养补估、表达请求、SQLite 锁或前端等待。现有 duration_ms 只有整次执行耗时，精确定位还需节点计时。用相同输入和 mock 模型隔离下游，再查服务 CPU/I/O 和数据库等待；不能一律归因 Python 慢，也不能先盲目增加进程破坏单进程假设。',
    'backend/app/graphs.py: run；parser.py；repository.py')
add([533], 'sql_explain',
    '轻记实际用 SQLite，可先 EXPLAIN QUERY PLAN 检查日期/类型条件是否使用 events_day 索引，关键词 LIKE 可能需要另行评估全文索引。如果将来迁 PostgreSQL，用 EXPLAIN 看计划，用 EXPLAIN ANALYZE 比较真实耗时与估计；后者会执行语句，写操作需谨慎。当前没有 PostgreSQL 慢查询事故或调优指标。',
    'backend/app/repository.py: events_day、events；PostgreSQL 官方 EXPLAIN 文档', '现状 + 基础知识')
add([534], 'lru',
    'LRU 可用哈希表加双向链表，实现 get/put 平均 O(1)，访问后移到最近端，容量满时删最久未用项，代码见附录 G。轻记当前没有使用 LRU 缓存；若缓存每日汇总，键必须包含 data_version，纠错后不能继续返回旧值。健康事实和待追问状态不能仅存会被淘汰的缓存。',
    '附录 G；backend/app/repository.py: version', '算法知识 + 工程关联')
add([535,536], 'threadpool',
    '线程池核心是任务队列、Worker、拒绝策略、生命周期和结果/异常回传，Worker 循环阻塞取任务执行，提交只是入队或唤醒，不是任务自己驱动线程。轻记没有自建线程池，FastAPI 与 asyncio.to_thread 使用运行环境能力；Engine 锁仍可能让业务串行。简化伪代码见附录 H，不能据此宣称已上线自研调度平台。',
    '附录 H；backend/app/scheduler.py；graphs.py', '基础知识 + 工程关联')
add([537,538,539], 'ray',
    '轻记没有 Ray，也没有 Head/Worker 集群。若未来批量离线分析或大量独立估算需要分布式计算，可把不可变输入、任务 ID 和结果写持久存储，计算 Worker 只处理受限任务；调度恢复还需结合具体 Ray 版本验证，不能假设 Head 故障后所有运行态自动无损恢复。当前单用户规模不需要增加这层。',
    'backend/requirements.txt 无 Ray；此处为扩展取舍', '扩展设计')
add([545], 'tool_layers',
    '在轻记中分层直接服务两个对象：模型只需理解记录与输出 Schema，研发需要保证校验、营养准备和写入不可跳过。把查询、估算和保存拆开后，能分别测试、限定数据和处理失败；用户最终看到可靠记录和一致卡片。模型看到的只是可用能力描述，并不决定底层如何鉴权、事务提交或恢复。',
    'backend/app/parser.py；graphs.py；nutrition.py；repository.py')
add([547,584,585], 'latency_metric',
    '当前轻记没有流式 Token 输出，所以不能把 duration_ms 叫模型 TTFT。它记录 Engine.run/resume 内部从处理开始到返回结果的时间，不包含全部浏览器网络与渲染时间，也没有 Token 使用量统计。若测用户首个可见响应，应从提交到首个状态/内容绘制另设指标，再单独统计模型首 Token、总任务耗时、等待用户时间和 P95。',
    'backend/app/graphs.py: run、resume；frontend/src/App.tsx')
add([551], 'engine_runtime_diff',
    '轻记没有两套平台引擎。可以区分的是 LangGraph 的节点执行与 checkpoint，和 Engine 自己的请求去重、恢复输入校验、数据库副作用及结果格式。健康业务又在普通模块中。若从固定流程平台演进为自主运行时，还要补预算、工具执行、权限和取消，不是简单给现有循环换个名称。',
    'backend/app/graphs.py；repository.py')
alias([552,553],77)
add([556], 'sse_resume',
    '当前没有 SSE，所以重新开页面只能读取持久消息、通知和工作流状态，不能续传模型字流。若实现，应持久保存 task_id、递增 event_seq、事件类型与内容，客户端保存已处理序号并用 Last-Event-ID 重连；服务重放其后事件，客户端按序号去重，最终成功与中断作为独立事件。传输恢复不代替业务幂等。',
    'backend/app/main.py；graphs.py；SSE 为扩展设计', '扩展设计')
add([557,558], 'workflow_io',
    '轻记图包含实际 I/O：模型 HTTP 请求、SQLite 写入、checkpoint 和通知记录。若包装成 Workflow Skill，与普通一次性 API 的区别是有多个持久步骤、等待用户和恢复状态；外部仍可以用 API 暴露。命名为 Skill 不会自动获得可靠性，I/O 副作用必须在对应服务用稳定操作键去重。',
    'backend/app/graphs.py；parser.py；repository.py')
add([560], 'redis_expire',
    '轻记没有 Redis，事实和 checkpoint 都持久化在 SQLite，因此不依赖 Redis TTL 续命。若未来用 Redis 缓存状态，过期后必须能从持久库重建任务与等待点；若唯一状态只在缓存中，过期就不能保证恢复。当前工程自身也会按保留期主动删除 checkpoint，删除后的未完成草稿无法找回。',
    'backend/app/graphs.py: __init__、prune_context')
add([563,564], 'sync_async',
    '轻记聊天图是 graph.invoke 同步执行，HTTP 在完成或等待用户后返回；后台提醒通过 asyncio 协程调度，并在线程里运行 tick。没有同步和异步两套 ReAct Loop，也没有外部任务回调恢复。若要支持长任务异步提交，需要额外持久任务队列、状态接口与 Worker，而不是把函数声明改为 async 就完成。',
    'backend/app/graphs.py: run；main.py: lifespan；scheduler.py')
add([565], 'coding_incomplete',
    '这道题没有完整题面，不能补造算法或声称现场作答成功。可以准备现场解释并实现轻记里的小逻辑，例如缺字段补丁只修改目标字段、请求指纹判断重复，写出输入输出、边界和最小测试。是否独立完成过现场 Coding 属于本人真实经历，需要本人确认。',
    'backend/app/drafts.py: merge_patch；graphs.py: fingerprint', '题面不完整；个人事实待补')
alias([566],126)
add([570,573,577,623], 'execution_location',
    '轻记的图节点和业务函数运行在本机 Python 进程，模型在外部 API 服务，前端在浏览器。没有 Browser Use、Bash、用户自定义脚本或 Skill 执行容器。若引入脚本，描述、代码和权限必须分开：运行环境只执行授权入口并受资源限制，不能让模型看到 Markdown 就自动在宿主机运行其中命令。',
    'start.ps1；backend/app/main.py；parser.py', '现状 + 扩展设计')
alias([575],264)
add([576], 'replace_langgraph',
    '可以替换，但影响的不只是 import：需要重建图路由、interrupt/Command 语义、checkpoint 格式及旧任务恢复。营养、目标、统计和 Repository 多数可保留；Engine 中的状态装配和恢复测试要重做。迁移时应先处理旧 waiting_input 工作流并版本化状态，不能让新引擎把旧草稿当新记录重复保存。',
    'backend/app/graphs.py；nutrition.py；repository.py；backend/tests/test_agent.py')
add([580], 'stability_perf',
    '轻记稳定性用幂等、重启恢复、模型失败、歧义纠错、营养未知和计划过期等故障场景验证；性能应另测并发、锁等待、模型耗时、数据库耗时与端到端延迟。当前有自动化逻辑测试，没有可核验生产负载基准或容量结论。前端 DOM 测试也不能替代真实浏览器视觉与网络体验。',
    'backend/tests/；frontend/src/App.test.tsx；README.md')
add([581], 'success_metric',
    '若轻记某次表达模型失败但本地兜底返回且记录正确保存，可计记录任务成功、表达调用失败、降级一次；若营养估算失败但保存未知，则按产品定义计记录完成且营养未齐全。需要同时报告节点失败率、任务完成率和降级率，不能只统计最终 200 掩盖上游不稳定，也不能把一切工具失败都算任务失败。',
    'backend/app/nutrition.py；replies.py；graphs.py: respond')
add([588], 'recall_hard',
    '对轻记当前结构化历史，难点是自然语言日期、食物别名和纠错指代能否映射到正确事件，而不是向量库大小。解析只发 12 条近期候选可能漏掉较早目标，历史页显式 event_id 更可靠。未来指南检索更难的是完整适用条件、多段证据和版本，提升 TopK 无法代替标注与错误分层。',
    'backend/app/graphs.py: recent；parser.py: model_parse；repository.py: events')
add([590], 'ir_metrics',
    '若为轻记增加指南检索，Recall@K 看必要相关证据覆盖，MRR 看首条相关结果排名，NDCG 适合多等级相关性和整体排序。多证据健康问题仅首条相关还不足，需额外测证据完整率与引用正确率。当前没有这些正式测量结果；现有 SQL 查询测试验证的是日期/类型/关键词返回是否正确。',
    'backend/tests/test_agent.py；文档检索评测为扩展设计', '评估方案')
add([602,604], 'coding_lessons',
    '可借鉴通用 Coding Agent 的是明确任务状态、受限能力、按需检索与验证，不必照搬开放 Bash。轻记可改进为节点级计时、Token 预算、原记录按 ID 二次读取、任务版本和更精确的数据血缘。具体例子是旧营养指纹变化才重估、过时计划拒绝激活；这些已有规则比泛泛“模型反思”更可检查。',
    'backend/app/nutrition.py；repository.py: decide_plan；advice.py')
add([610], 'compliance_stack',
    '仓库没有公司合规政策，不能推断被允许或禁止哪些语言与模型。轻记当前使用 Python/TypeScript 和外部模型，健康数据存在外发环节；若在组织内使用，应按真实数据要求选择内网模型、依赖来源和日志策略。技术栈选择需要兼顾部署、许可证与数据边界，具体限制必须以组织政策为准。',
    'backend/requirements.txt；frontend/package.json；parser.py', '组织事实待补')
add([615], 'commercial_stack',
    'LangGraph、FastAPI 和 Python 可以作为产品组件，但轻记当前只证明本地单用户流程可运行。商业化还要验证并发、共享存储、身份权限、队列、可观测、成本和运维；现有同步模型调用与全局锁是明显扩展限制。框架名称本身不能证明容量，应以真实任务负载、失败恢复和成本测试作依据。',
    'backend/app/graphs.py: run；parser.py；README.md')
add([616,617], 'code_nodes',
    '轻记的统计、目标和保存节点本来就可以不调 LLM，按确定性依赖执行即可。当前没有不可信代码节点；如果未来允许用户代码，死循环必须靠独立进程/容器的资源与时间限制中止，不能依赖同线程取消或让模型自己判断停止。CPU 密集批量计算可放独立任务，完成后再按版本提交结果。',
    'backend/app/services.py；targets.py；graphs.py；用户代码执行为扩展设计')
add([619], 'function_call_example',
    '题中的天气和家电不在轻记能力范围。用轻记等价说明：用户说“昨天中午吃面”，模型产生记录草稿，图校验后发现缺分量，interrupt 等待补充；补丁合并、营养准备、事务保存，最后返回卡片和事实回复。这里没有原生 Function Call/Observation 循环；若控制家电，要另外校验设备归属、动作风险和执行回执。',
    'backend/app/graphs.py: build_chat；parser.py；schemas.py')
add([625,626,627,628,629,630], 'mcp_skill',
    '轻记目前没有 MCP，也没有动态 Skill，证明固定健康流程可以直接调用 Python 服务和模型 API。概念上 Skill 描述如何完成任务，MCP 提供能力发现与调用的统一协议，两者可独立使用；OpenAPI/CLI 也可接能力。安全、幂等、权限与治理不是 MCP 自动或独有提供的，若以后接入仍由宿主和执行服务负责。API Key 在环境变量中也不代表模型已获任意命令权限。',
    'backend/app/graphs.py；parser.py；当前未实现 MCP/Skill 运行器', '现状 + 概念比较')
add([631], 'million_users',
    '轻记当前架构不能直接支撑千万用户。演进应拆无状态 API、持久任务队列、共享 Worker、关系数据库与 checkpoint 存储，按用户授权和命名空间隔离；模型并发与费用设配额，长任务和实时任务分优先级。百万量级之前也要容量测试和故障演练，不能简单为每用户启动一个 Engine 进程或复制当前 SQLite 文件。',
    '当前 start.ps1；graphs.py: Engine.lock；此处为云端扩展设计', '扩展设计')
add([633,634], 'shared_assets',
    '当前轻记只有一份本地数据，没有租户私有 Skill 或文件。若云化，公共提示与图按不可变版本共享，私有档案、事件、偏好、文件和 checkpoint 按服务端用户身份隔离；缓存键和检索过滤也带用户与版本。物理复用公共资产不等于可以共享私有上下文，任务 ID 不能替代身份授权。',
    'backend/app/repository.py 当前无租户列；graphs.py', '扩展设计')
add([635,636], 'host_commands',
    '轻记没有执行 ps、kill、Bash 或管理 Java 进程的能力。若将来增加运维工具，应由宿主在隔离环境外完成授权与调度，沙箱仅能操作分配给该任务的资源；进程选择需校验归属、稳定标识和允许动作，高影响终止操作需要明确确认。不能把用户一句命令直接交给模型在宿主机执行。',
    'backend/app/parser.py：模型仅返回受限 JSON；宿主命令为扩展设计', '扩展设计')
add([638], 'coroutine',
    'Python 协程可用 async def 定义，在 I/O 等待时 await，让其他任务推进；附录 I 给出 gather 并发等待示例。轻记真实用法是 scheduler_loop 每轮 await asyncio.to_thread(tick, engine)，然后 await asyncio.sleep(60)。同步 httpx 解析不会因为外层有协程就自动非阻塞，业务锁也仍限制并发。',
    '附录 I；backend/app/scheduler.py；parser.py', '编码知识 + 工程关联')
add([639,640], 'singleton',
    '轻记没有 Java 单例实现，附录 J 给出枚举单例和 volatile 双重检查锁：锁住类级初始化区，进入锁后再次判空；volatile 保障引用可见性并避免不安全发布相关重排序。Python 中当前每个 FastAPI lifespan 创建一个 Engine 是应用生命周期管理，不等于 Java 全局单例，更不能据此声称跨进程共享同一个数据库连接。',
    '附录 J；backend/app/main.py: lifespan', '编码知识 + 工程关联')


def refine(number, text, evidence=None):
    """Differentiate a follow-up from its main question without losing evidence."""
    previous = ANSWERS[number]
    ANSWERS[number] = {**previous, 'key': previous['key']+f'_{number}', 'text': text,
                       'evidence': evidence or previous['evidence']}


refine(13, '不需要。轻记三张图都运行在同一个 Engine 进程内，请求通过 thread_id 区分 State，并不为每条记录启动实例。如果以后增加 Worker，也可复用进程读取不同任务状态；只有运行不可信代码、需要特殊硬件或更强隔离时，才考虑专用执行环境。独立任务状态与独立操作系统进程是两个问题。')
refine(34, '当前没有排障提升的对照数据。要证明 process_trace 有收益，应选同一批解析失败、重复请求和过时计划案例，在相同模型与环境下比较有无轨迹的定位时间，并固定故障严重度和参与者条件。只有简单前后对比时只能说明相关，不能排除熟练程度和案例难度变化，更不能报一个未经测量的提升比例。')
refine(54, '从轻记看，模型可以变得更擅长理解食物和纠错，但状态恢复、事实来源、目标确认与删除仍需要运行环境负责。后续会优先改善可观察性、真实营养数据和状态契约，让模型或编排框架可替换，而不是不断增加角色。这里是基于工程的演进判断，不是对未来产品能力的确定预测。')
refine(67, '当前没有能力声明文件；若为轻记设计，应包含 capability_id、版本、说明、输入输出 Schema、可读数据范围、是否写入、超时/重试和幂等要求。比如 propose_plan 必须声明可能返回 waiting_input，不能只写“生成计划”。加载时还要检查代码入口和状态兼容，声明文件不能自行授予数据库权限。')
refine(70, '新增食物规则、默认目标公式或统计模板应改轻记业务模块；只有新增通用恢复状态、事件协议、取消或统一执行契约时才考虑改 Engine。当前图仍直接导入领域函数，边界不是完全插件化。我的判断标准是新需求是否对所有业务都成立，若只服务一类健康规则，就不把它硬编码成运行时通用行为。')
refine(76, '轻记用户通过对话意图和历史、计划等页面使用已有能力，没有 Skill 市场或搜索器。例如选历史记录纠错会显式传 event_id，比让用户记一个 Skill 名称更直接。若未来能力很多，可在页面给出按场景分类的入口与简单示例，再由服务器选择受限流程；发现入口与执行授权必须分别处理。')
refine(83, '围绕这份工程，可把本人职责细化到某些接口、图节点、前端组件或测试，例如是否亲自设计过 EventPatch 限定更新、营养指纹复用或计划 stale 检查。应提供对应提交与能解释的关键取舍，而不是把“了解全部流程”直接说成“负责整个平台”。当前未提供个人模块分工，最终表述需本人确认。')
refine(84, '当前资料只支持单用户本地原型，未提供已上线客户或活跃用户证据。能展示的是 start.ps1 本地运行、网页操作和测试样本；不能把测试构造的用户消息当生产使用量。若本人有真实试用记录，可另说明人数、时长和反馈，并区分内部试用、个人自用和付费生产。')
refine(96, '通过轻记可以具体讲 LangGraph 的 StateGraph、SqliteSaver 与 interrupt 恢复实践，尤其是暂停节点重放和数据库幂等。至于“最近还在读哪些框架”，需要本人补真实阅读清单、版本和读到的模块；代码依赖里出现某框架不能证明已研读源码，更不能补写并未完成的横向评测。')
refine(105, '目前没有在相同模型、工具和输入下与通用 Agent 做过系统对照的证据，应直接说明。后续可以复用轻记的中文记录、歧义纠错、重启、重复提交与过时计划用例，允许不同合法轨迹，以最终数据库事实、确认边界和耗时成本比较，而不是挑一个演示回答判断优劣。')
refine(110, '我能确认轻记采用显式 LangGraph 工作流，业务状态和校验由代码控制；没有在本工程实测 AgentScope，因此不列它的未经核对的当前能力。若比较，应让两套实现跑相同的缺字段追问、重启和副作用重放任务，比较实现复杂度、状态可见性及恢复结果。没有实验就不声称轻记优于某个框架。')
refine(116, '轻记既没有预声明的子 Agent，也不会运行中动态创建角色。若以后加入，初期会采用预先声明的只读知识检索或报告能力，固定输入输出与权限，更容易评测；动态创建只适合边界明确且受预算控制的探索任务。无论如何，子任务不能自行扩权或直接激活健康目标。')
refine(120, '结合轻记可以表达的技术规划是继续深入 Agent 应用可靠性、上下文选择和数据可信度，并补齐生产部署与质量评估。下一份岗位、管理或专家路线属于本人职业选择，需结合真实意愿填写。这个原型不能证明已经具备企业平台负责人或模型训练负责人经历，规划应区分想做与已经做过。')
refine(123, '是否晋升及最近时间属于个人事实，仓库没有这类记录。若有，应给出准确时间、职级体系和实际职责变化，再选择轻记或其他真实项目说明能力；若没有就如实回答。不要用一次代码迭代或技术选型包装成组织晋升，也不要把测试数量当晋升业绩。')
refine(125, '应按本人真实时间线说明先晋升还是先进入 AI 研发；轻记提交与文件日期只能证明工程迭代，不能证明任职转向或晋升发生时间。可以在履历中分别列岗位变化、承担职责和技术项目节点，再解释彼此关系。当前材料不足以替本人确定先后顺序。')
refine(129, '工程不能证明所有研发都参与内核。就合理分工而言，轻记前端可围绕已约定 API 开发，营养与目标策略可由业务模块维护，Engine 的恢复与事务边界则需要少数熟悉状态语义的人统一把关。实际团队谁参与了哪些部分必须按本人经历回答，不能由目录结构反推组织分工。')
refine(132, '可以用具体决策说明参与程度，例如为何把宽松草稿和严格事件分开、为何将营养准备放在两条保存路径之前、为何确认计划时检查版本。本人是否提出、评审还是实现这些决策需要如实区分；仓库本身不能认定最终架构由谁拍板，也不能替本人写成独立主导。')
refine(139, '具体记忆实现由本工程的 Repository 与业务上下文函数提供：Engine 初始化 Repository，节点直接调用 events、facts 和 get_meta，advice 负责筛选。当前没有依赖注入容器或 Memory Provider 注册协议。若以后要替换存储，可以注入同一接口实现，但健康事实写入策略和用户限制的处理仍由领域层决定。')
refine(145, '从轻记的需求可以讨论几个方向：结构化输出、持久状态、人机确认、证据检索和执行验证，但这属于工程观察，不等于已研究所有近期框架。真正声称“最近某方案改变了设计”应给出本人读过的版本和可复现实验，例如升级 checkpoint 后重跑恢复用例，而不是泛泛列流行名词。')
refine(151, '任务复杂不等于要多 Agent。轻记的复杂性主要是多事件缺字段、重启恢复和计划版本，拆成多个自主角色反而增加共享数据冲突。当前通过三张图和独立业务函数即可表达。只有任务能形成独立输入输出、各自可验证，且并行或权限隔离带来实际收益时，才值得加角色。')
refine(152, '我会要求至少有清楚的职责边界、独立上下文和可验证产物，再评估并行收益是否超过通信与合并成本。以轻记为例，未来指南检索可以只读运行并返回来源，最终记录和计划确认仍由主流程执行；两个人设都能改同一份营养记录，就不构成良好的拆分。当前项目尚未引入多 Agent。')
refine(188, '因为格式正确的 DSL 也可能绕过业务门禁。轻记目前只让模型产出事件草稿，固定图保证校验和营养准备先于保存；若模型直接产最终执行图，可能漏确认或引入无限循环。未来自然语言编排应先生成草案，再检查节点白名单、类型和必须路径，并在临时库中验证后发布。')
refine(192, '面向轻记这类个人用户，不要求用户理解工具注册表。当前文本进入意图提取，页面操作直接调用具体 API；模型不拿到全部工具。未来接更多设备和知识能力时，先按用户权限与场景提供少量候选，再加载必要参数说明。用户的过敏限制、当前任务和待确认状态应比无关工具描述更优先。')
refine(201, '模型可以帮助生成“少食物、否定、跨午夜”等候选测试，但不能自己定义轻记全部参考标准。事件日期、保存条数、营养来源和目标状态应由人工及业务规则核对；营养真值还需可信标签。候选集要去重、分训练与验证，并保留真实失败样本，防止模型用同一套假设出题又自评。')
refine(220, '未来多个健康 Agent 能力相近时确实可能误选。轻记目前只有受限 intent 和本地咨询场景，已经可以用歧义纠错测试路由；若增加角色，应区分只读咨询、事实记录和目标变更，返回任务 Schema 与权限标签。高影响动作不能仅凭相似描述路由，必须由执行端检查状态和用户授权。')
refine(221, '应标注每个输入的正确业务分支及允许的合理多解，例如“昨天吃咸了怎么办”应走咨询，明确要求记录但缺食物则需追问，纠错歧义不能直接更新。统计误路由类型和后果，比只给总体准确率更有用。轻记已有相关案例测试，尚无独立多 Agent 路由评测报表。')
refine(232, '当前没有切分阈值。若对健康指南尝试 PPL，应先固定模型与文档集，在验证集选择基线阈值，再按标题和表格结构限制候选边界；动态阈值也必须有可复现规则。不能在测试集不断调到好看，尤其不能把剂量与适用人群拆开后仅凭相似度重新拼接。')
refine(245, '轻记的业务本体由项目自己定义：五类事件、餐食项、营养引用、计划和证据。若将来做平台，通用层只提供 Schema、动作与关系的注册契约，具体健康概念、单位和适用限制仍由领域团队维护并审核。当前没有外部统一本体产品，也没有知识图谱服务，不能声称直接复用了企业数据平台。')
refine(305, 'checkpoint 快照直接使用 LangGraph SqliteSaver，未自行实现快照格式。自己补的是 health.db 中的请求指纹、workflow 结果、恢复 receipts 和 events 操作键，解决 API 重试及副作用重放。两者通过 thread_id 关联但并非一个事务；因此框架恢复和业务恰好一次不能混为一谈。')
refine(312, '当前 Worker 不使用 CAS，因为系统仍按单进程锁串行关键操作。若分布式化，应让 UPDATE task ... WHERE id=? AND generation=? 只接受当前持有者，并检查受影响行数；若还有业务行版本也应一起校验。代次失效立即停止提交，外部工具若不支持 fencing 仍需幂等或对账，不能只保护内部任务表。')
refine(325, '现有初始请求失败可按原 request_id 查状态重试，但没有完整的分布式卡住巡检。迁移方案中应持久化任务清单、等待原因和最后进度，区分等用户与执行超时；对 running 超时任务安全认领，对 waiting_input 保留可见入口。自动重跑前先查副作用结果，不能为消除卡住而生成重复记录。')
refine(347, '若某类内容不允许保留，需要在轻记首次 repo.message 和事件 raw_text 入库之前处理，并保证 checkpoint、日志和后续卡片不再携带副本。当前没有通用“仅本次使用不存档”字段，已有流程会保存用户文字。若新增 should_persist 或敏感字段策略，必须明确是否仍能跨进程恢复，不能只从 UI 隐藏消息就声称已删除。')
refine(351, '轻记当前卡片和计划字段由 Schema 与前端组件固定，不能任意改布局。对不支持的字段应说明可改范围，必要时转成用户备注或功能需求；已经支持的食物、分量修改则走 event_id 纠错，重新校验营养。不能仅在回复中说“已修改”却没有数据提交，也不应把任意字段写入结构化结果。')
refine(357, '当前只有一个健康域，不存在跨业务域授权。若未来健康记录 Agent 请求报告或设备能力，主机先根据已认证用户和动作范围授权，只传必要事件引用，子能力不能继承数据库全权限。不同域都要校验资源归属与字段可见性；用户的自然语言请求和 thread_id 不能独立作为跨域访问凭证。')
refine(361, '当前没有商业客户选择自研轻记的证据。如果同样模型、工具和约束下通用 Agent 更便宜且效果更好，应优先复用；自定义部分只保留营养来源、健康记录与确认等确有价值的差异。是否愿意付费需要实际验证隐私、可靠性、体验与运维收益，不能用“自研”本身当商业理由。')
refine(378, '轻记不提供任意文件、网络或系统命令工具，模型网络访问由后端固定请求控制；但服务本身运行在用户账户下，没有容器级文件隔离。若增加脚本，需在受限执行环境限制目录、出口网络和系统调用，密钥不能暴露其中。当前本机单用户假设也不能替代公网访问控制。')
refine(384, '轻记对健康事件可显式纠错并保留修订，对当前主观反馈可用否认撤回；但长期 facts 仍是简单确认条目，缺少自动语义冲突合并。例如旧偏好“我不吃鸡蛋”与新说法冲突时，应请求确认并删除或替代旧事实，而不是让模型任意选择。高影响限制还应保留来源和修改时间。')
refine(386, '若错误的是健康事件，通过唯一 event_id 纠错会产生新版本，并让依赖结论过期；若错误的是长期 fact，可以通过删除接口撤回，同时清理上下文与 checkpoint 副本。当前没有自动识别所有错误记忆的机制。删除后重新明确正确事实，比只让模型下一轮“别再提”更可靠。')
refine(394, '对轻记当前少量结构化个人记忆，没有必要默认上向量库和重排器。日期、事件 ID、类型和明确关键词已经可解释；facts 也直接按规则筛选。只有将来出现大量非结构化指南且现有检索确实漏证据，才用固定问题集验证语义召回的收益，同时计入成本与隐私影响。')
refine(400, '设出现最多的元素频次为 max_count，数组长 n；若 max_count 大于 ceil(n/2) 就无法隔开全部相同元素，否则最大堆延迟回放上一元素可构造。空数组自然返回空结果。此结论属于独立算法题，与轻记当前按时间去重的站内提醒不同，代码见附录 E，不应声称工程用它安排通知。')
refine(417, '轻记目前没有异步外部结果获取机制。若接视频或设备任务，有可靠回调则验证签名、任务归属和去重；否则轮询持久 job_id，设置退避与截止。两种方式都要先把完成结果落库，再恢复对应工作流，重复通知不能重复写。前端可以轮询显示状态，但不负责保证服务端任务完成。')
refine(418, '外部结果要先按 job_id 找回保存的 thread_id、等待点和步骤版本，确认这次结果仍有效，再以受限结果对象恢复图；不能靠“最近一条对话”猜任务。轻记已有用户恢复的 interrupt_id 校验可借鉴，但尚未实现外部 job 映射与回调认证，需作为新增机制设计。')
refine(428, '若为轻记增加实时咨询，底层通信组件管理连接、媒体、重连和协议状态，上层业务负责用户授权、咨询会话、记录关联和数据保存。两者通过会话 ID 与状态事件交互，不能让网络连接断开直接等同业务记录被取消。当前工程只提供 REST 和后台提醒，没有这套通信控制服务。')
refine(430, '实时系统的连接是否存活应由通信组件观测，业务服务保存可追踪的会话状态及其版本；前端在线状态只能作信号。若应用到轻记，断开网页不应删除已保存事件或待追问状态。当前没有音视频会话状态机，不能把消息轮询或 HTTP 请求成功率包装成通话连接管理经验。')
refine(431, '在拟议的健康实时咨询中，状态事件应带 session_id、序号、发生时间与版本，重复幂等处理，乱序不能覆盖新状态，并定期向连接真值源核对。轻记当前可复用的思路是记录 ID、版本和事务，但没有已实现的通话同步链路，也不能承诺跨服务天然强一致。')
refine(432, '这取决于延迟和解耦需求：本地短链路可回调，但要认证、去重和超时处理；跨服务需要缓冲与重放时可用持久队列，仍需处理乱序和重复。轻记当前两者都未用于通话状态，只在 SQLite 保存 jobs 与通知。若将来新增，先确定事件真值和重放口径，再选择传递方式。')
refine(433, '轻记可以作为低频个人数据处理的例子：事件规范化入 SQLite，按日汇总，支持复盘、规律和月度归档。它没有企业数据平台、设备流或时序集群。本人是否参与过其他数据处理平台需单独说明；个人职责可以具体到事件模型、汇总算法和回归测试，不能据当前功能虚构工业平台经历。')
refine(439, '当前用户低频记录更适合按日历日期查询，分析再读取 7 天或 28 天窗口，补记后重算。工程没有采用流式滑动窗口，并非验证过它无效。若未来有分钟级设备流与实时告警，再比较滑动、滚动和会话窗口，明确迟到与水位线；不要把普通数据库日期范围查询称作流处理引擎。')
refine(442, '我会在轻记现有 data_version、rule_version 之外增加不可变指标定义、受影响事件范围、重算任务状态与结果版本，用相同快照跑新旧口径对照后发布。重算失败保留旧结果并标明过期。归档后逐餐数据已删，某些指标无法补算，必须限制承诺；当前还没有完整指标版本管理平台。')
refine(443, '现阶段不需要 Redis：轻记日汇总按 SQLite 原记录计算，规模低且有纠错与删除要求。若以后缓存高频看板，可用用户/日期/data_version 作为键，失效后从持久库重建；未完成草稿和健康事实不能只存 Redis。不能因为缓存快就忽视旧营养和过时计划的版本问题。')
refine(454, '用户、场景和配置都未变时，优先核对实际依赖与数据：索引是否重建或漏写、模型/Tokenizer 服务是否更新、过滤权限是否变化、评测标签与文档版本是否错配。轻记当前 SQL 检索也应先查事件是否被纠错、删除或归档，而不是立即改 Prompt。这里是排查方法，工程没有真实 Recall@5 下降事故数据。')
refine(456, '若接 Embedding，应保留固定输入及其规范化版本，重复调用比较向量维度、数值误差、相似度和近邻排序，并在模型升级后回归。相同请求返回略有数值差异也未必影响召回，反之向量相同也不代表权限正确。轻记未使用向量，因此不能给出实测稳定性数字；业务幂等另由数据库保证。')
refine(458, '没有重排器就没有本工程稳定性结论。未来测试需固定查询、候选集合、模型版本与推理参数，重复比较排序和并列分数处理，监控候选数量与缺失 ID。即使顺序稳定，也要用标注评估相关性；不应把确定输出等同正确健康建议，引用与适用边界仍需验证。')
refine(462, '轻记最终输出评价看保存事实、卡片数字、目标状态和建议是否回应当前问题；轨迹评价看是否经过必需校验、是否有重复估算/写入、是否带无关原文。结果对但绕过安全路径不应通过，工具暂时失败却正确本地兜底可算降级成功。两层指标都需保留，不能只评一句回复是否自然。')
refine(467, '不能仅凭两款工具的一次回答归因。模型影响理解和生成，运行环境影响可见文件、工具权限、上下文与验证。以轻记为参照，给同一模型加入事件校验和计划确认就会改变行为；要区分影响，应固定任务与模型分别替换运行条件。当前没有保存这种产品对照实验，不能给确定比例。')
refine(475, '仓库未提供维护 Engine 的人数，所以应由本人填写实际情况。技术上内核改动涉及图恢复、API 回执和副作用，需要统一负责契约并做代码审查；不要求所有前端或业务策略开发者都改 Engine。维护人数不能从文件数量、提交数或测试数量推断。')
refine(477, '轻记资料没有正式员工、外包或其他用工类型的分工信息，不能代填。若讨论协作方法，应按能力和授权划分接口、数据与测试责任，对健康数据访问和密钥统一控制，所有代码采用同样审查与验收标准。真实组织职责需要本人确认，与项目模块划分应分开讲。')
refine(491, '职级、绩效、薪酬结构需要本人提供准确且可公开的信息；轻记工程无法证明这些。可以用具体设计解释技术范围，例如恢复幂等、计划版本和营养来源，但不要由这些功能推断收入或晋升。回答结构可先说明真实职级与职责，再按本人意愿解释固定、浮动和长期部分。')
refine(494, '这份工程没有客户或线上用户统计，定位仍是单用户本地原型。可以描述本人真实的自用或试用情况，但需另附人数、时间和反馈口径；测试构造的消息与事件不等于真实用户。若尚未商业化，直接说明当前在验证完整记录与可靠性流程，比补一个平台客户规模更可信。')
refine(503, '对于轻记，如果包装 Workflow Skill，描述部分可以说明何时记录一餐、输入字段和等待行为，执行部分则是 build_chat 及其服务函数。当前代码还没有 Skill 运行器，因此不能说已有上传描述文件就运行的功能。它的核心不是文件后缀，而是调用方能否按明确契约启动、等待和恢复。')
refine(502, '工程没有旧画布的迁移能力。若把原 Workflow 继续承载，应先保留可执行逻辑，通过适配层映射输入输出、中断和错误语义，再逐步将节点绑定为新的受限服务。数据写入与旧副作用必须补幂等，并用原样本回归；不能只改成“Workflow Skill”这个名称就认为迁移完成。')
refine(505, '运行控制之外可复用的公共资产包括五类事件 Schema、上海时区处理、营养标签基准、证据引用格式、计划差异展示、失败样本和前端待追问组件。复用时要区分通用工程契约与健康领域规则：默认目标公式和医疗边界需单独审核，不能直接当任意行业通用逻辑。')
refine(508, '可以从产品类别上理解这类 AI 应用平台，但轻记没有集成 Coze/Dify，也没有保存源码研究或横向实测记录。应围绕自身需求验证它们是否支持持久追问、提交后重放、营养来源和删除副本，而不随意断言其当前功能。若本人实际用过，应补版本、任务和具体遇到的限制。')
refine(514, '拟议的子流程遇到 interrupt，应把等待点和 child_thread_id 持久化并向主流程传递 waiting 状态，不能让主流程继续保存或发布；异常则按可重试和不可恢复分类。轻记目前是三张独立图，没有这一父子协调机制，但其 interrupt_id 校验与稳定操作键可复用。')
refine(525, '层级索引适合目录和章节关系清楚、需要定位整段语境的长文档。若轻记以后接健康指南，可以按章节定位后读取必要原文，同时保留页码与人群限制；查精确日期或某条餐食，现有 SQL 更直接。工程没有 PageIndex 实现或效果对照，不能声称层级方法一定胜过向量检索。')
refine(527, '当前没有资料证明本人研究或复用了 Dify 的流式节点抽象，轻记也没有 SSE。若要回答源码研究，应补真实版本与模块；从本工程出发，可以讨论如何统一进度、内容、卡片和等待事件，再用持久序号保证重连。概念设计与已读源码经历必须分别说明。')
refine(531, 'FastAPI 并发可以包含事件循环、线程和多进程，但轻记当前同步 def 接口和同步 httpx 调用，后台使用协程加 to_thread，关键操作还有进程内 RLock。因而不能简单说“FastAPI 用协程所以都是异步”。若增加 Uvicorn 进程数，需要先处理共享数据库、调度重复和进程间互斥。')
refine(536, 'Worker 自己运行取任务循环，队列条件满足时从阻塞等待被唤醒；提交者负责入队和必要时创建/唤醒线程，不是任务对象主动驱动执行。线程池还需处理停止、异常与结果 Future。轻记没有自建该线程池，当前 asyncio.to_thread 借用环境线程执行 tick，Engine 锁仍可能让实际业务串行。')
refine(538, '若将轻记的离线分析改用 Ray，业务任务不能依赖某个 Worker 的进程内字典，应通过持久任务 ID、输入快照和结果引用解耦，调度与计算节点只处理可重放步骤。当前没有 Ray 代码，也不承诺某个具体版本的 Head 故障语义；应按选定部署方案验证状态和结果是否可恢复。')
refine(539, '当前工程无 Ray 集群，不能报告 Head 故障恢复经验。拟议方案需先把任务输入、步骤完成和输出放到可靠存储，故障后核对已提交结果并按依赖继续，防止已执行营养或报告任务重复产生副作用。实际恢复能力取决于 Ray 版本与部署方式，必须通过故障演练，而不是假设重启 Head 就无损恢复。')
refine(553, '把轻记的记录或目标确认封装为 Workflow Skill，可让外层调用者只需知道用途、参数和结果状态，而不用重写追问与版本检查。代价是维护契约、代码和版本；收益是多个入口复用同一业务门禁。当前尚无 Skill 加载器，不能把这个封装方案描述成已完成平台能力。')
refine(558, '轻记的计划流程可以通过 REST 暴露，但内部包含持久步骤与等待确认；普通单次 API 也能实现这些语义，区别在契约和生命周期而非“Skill”名称。若包装 Workflow，应把 pending、resume、版本和幂等显式化。仅把一个 HTTP 调用写入描述文件，不会自动获得可恢复执行。')
refine(564, '不存在两套 ReAct Loop，因为当前轻记就没有开放 ReAct 主循环。聊天用同步 graph.invoke，后台调度用 asyncio 协程驱动同步 tick；这只是调用与调度方式不同。将来如增加异步外部任务，也应共享业务 Schema、幂等和恢复契约，不复制两套容易行为不一致的健康写入逻辑。')
refine(573, '目前不提供代码或 Bash 能力，模型只有受限 JSON 输出，不能在本机执行命令。若未来允许脚本，应单独提交到受限执行环境，限定可访问目录、网络、资源和有效期，结果再由服务端校验。不能让脚本直接读写 data/health.db，或把后端模型密钥继承给任意生成代码。')
refine(577, '轻记当前所谓业务能力都由开发者写成 Python 函数并静态绑定图节点，没有读取 Skill 文件后运行代码的机制。若增加，宿主应解析清单并选择已授权入口，脚本在受限环境执行，返回符合 Schema 的结果；说明文字不能直接成为可执行命令，副作用仍需幂等与审查。')
refine(585, '应先明确起止：用户提交到首个可见进度、模型请求到首 Token、还是整个图完成。轻记目前没有流式内容，duration_ms 也从获得 Engine 锁后的处理开始统计，不能直接代表全部用户等待。若觉得首响应慢，应另测排队、网络、模型和渲染，并区分即时状态提示与真实内容，不能靠一个平均耗时解释。')
refine(597, '轻记更偏 Agent 业务应用落地：用开源框架把自然语言记录、健康数据、计划和网页连起来，包含一些运行环境封装。它不是从零开发通用框架，也不涉及训练模型。本人在公司到底做平台研发还是业务交付仍需真实履历支持，不能把这个工程等同于企业平台产品。')
refine(604, '具体例子可以讲两项：食物或分量变了，营养指纹失效并重新准备；计划草案生成后用户又补记，data_version 不匹配就拒绝激活。二者都是轻记的本地验证，不依赖模型自觉反思。若参考 Coding Agent 改进，可进一步加入节点计时与按 ID 回读证据，但当前没有任意代码执行闭环。')
refine(607, '轻记没有训练、微调、偏好优化或自建模型权重，主要调用现成模型 API，并通过 Schema、本地校验和回归样本提高可靠性。本人工作中是否参加过训练，需要另提供真实职责与实验，不能把 prompt 调整或 mock 测试称作训练。如果没有，可直接转向自己做过的模型接入与业务评测。')
refine(611, '当前会话中使用 Codex 处理这个工程，后端配置的业务模型则是 qwen3.8-max，两者用途不同。团队是否还使用其他编码工具或模型、如何分配任务，工程无法证明，应由本人补充。也不能把应用所用模型名直接写成团队 AI Coding 工具或推断整个项目由它生成。')
refine(617, '如果未来轻记开放代码节点，死循环必须通过独立进程或容器的硬执行时限和资源配额中止，超时结果写入持久状态，后续节点停止或进入受限补救。同线程的协程取消不能可靠终止任意 CPU 死循环。当前只有受信任研发代码，没有此执行平台，因此不能说已具备沙箱级终止能力。')
refine(626, '可以不用，轻记当前就直接调用 Python 业务函数与模型 API，没有 MCP。若 Skill 只是调用少量稳定本地服务，这种方式更简单；当需要跨宿主复用大量外部工具、统一发现与连接时，再评估 MCP。Skill 解决任务方法，协议解决能力接入，是否引入取决于集成需求而不是概念必须成套。')
refine(627, '这些方式可以替代具体集成中的 MCP 调用，不能说绝对不可以。轻记目前直接使用 HTTP 与 Python，已经完成固定能力接入；代价是接口发现、Schema、结果映射和兼容性由自己维护。MCP 的价值在统一连接契约和互操作，若系统规模小，直接适配完全可行，安全治理仍需单独实现。')
refine(628, 'Key 在环境变量只解决凭证提供方式，模型仍需知道允许哪些能力及参数。MCP 可统一工具列表、输入输出 Schema、调用与结果交互，减少每个宿主对 CLI 的单独适配；它不自动保证授权或命令安全。轻记暂未接 MCP，且源码仍有硬编码密钥，不能把题中环境变量前提当工程现状。')
refine(629, 'OpenAPI 主要描述 HTTP 服务接口，MCP 面向宿主与能力服务的统一交互，包含能力协商，以及工具发现/调用、资源和提示等约定。它们可以互相适配，不是简单替代关系。轻记当前只有 REST 和模型兼容接口；是否增加 MCP，要看跨宿主集成收益，不必为了协议把已有健康逻辑重写。')
refine(630, '不是。鉴权、审计、限流、幂等和复用都可以在普通 API、网关或执行服务中建设，MCP 提供标准接入约定而不是自动安全保证。轻记的稳定操作键与计划确认完全由本地代码实现，未使用 MCP。将来若接协议，仍要在宿主和服务端核验健康数据权限，不能信任工具声明就放行写入。')
refine(634, '如果轻记云化，公共图和提示按不可变版本物理复用，私有事件、偏好和文件按认证用户逻辑隔离，访问时再校验归属；缓存和检索也带用户和版本。对强隔离需求再考虑独立库或执行环境。当前本地单用户没有这套实现，也不能拿 thread_id 当租户隔离键。')
refine(636, '宿主在沙箱外调度时可以集中管理权限、状态和密钥，沙箱只执行受限代码并返回结果；若整个 Agent 放沙箱内，需额外处理上下文、工具连接和凭证边界。轻记当前没有任何代码沙箱。未来更适合让健康真值库与授权服务留在宿主侧，避免临时脚本拿到完整数据和永久密钥。')
refine(640, '双重检查锁先在锁外判空减少已初始化时的锁开销，在类级锁内再判空防止同时创建；instance 使用 volatile 防止不安全发布并保证可见性。代码见附录 J。轻记是 Python，Engine 由 FastAPI 生命周期创建且每进程一份，不能把 Java 单例语义直接套成跨进程全局唯一实例。')
refine(641, '当前会话本身就是在本机工程上下文中使用 Codex 检查代码和生成文档的例子，可以说明这一点；但日常使用频率、具体工具偏好和此前项目方式仍需本人确认。面试时可讲一次真实的审查或修复过程，例如发现回复与存储值不一致后用测试核查，而不是只强调工具名称。')
refine(642, '目前没有足够证据划分全部代码作者或 AI Coding 比例，不能给一个估计百分比当事实。应由本人说明哪些需求、设计、代码修改和验证亲自完成，哪些使用 AI 辅助，并能解释幂等、营养来源和恢复测试。可通过提交、审查记录和实际模块举例，而不是把生成行数当独立研发贡献。')
