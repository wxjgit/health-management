"""Standard interview knowledge for topics outside the repository's implementation."""
import re
from pathlib import Path
from pypdf import PdfReader

# Only topics backed by the health-management implementation retain project answers.
PROJECT_KEYS = set('''langgraph limits restart idempotent trace select_context deployment layers
provider product usage assets privacy framework_boundary capacity observability_access control_plane
value architecture badcase quality own_work memory_write timeout close_page lease platform_relation
business_modules decouple memory_policy domains engine memory_layers languages io_contract ground_truth
model_now developer_api project_pitch memory_api context_engineering validated_scope lifecycle
task_vs_model regression injection trust memory_conflict unknown_external differentiation shared_write
offtopic ontology nodes stage memory_categories runtime_or_console mandatory completion validation_failed
checkpoint_content idempotency_vs_lock fencing nutrition_node task_state real_code code_workflow
reliability_quality trigger entry_paths missing_step beyond_prompt history_storage create_update
out_of_scope artifact forget recover_context freshness manual_plan plan_state hitl user_steering
storage_choice missing_data metric_config windows aggregate recompute domain_modules
python_concurrency latency latency_metric workflow_io sync_async stability_perf success_metric
coding_lessons'''.split())
PROJECT_KEYS -= {'windows','storage_choice','plan_state','metric_config','missing_data','ontology'}
STANDARD_ONLY_KEYS = {'code_workflow_502','recompute_442'}

# Personal experience cannot be inferred from source code. These get answer templates.
PERSONAL_KEYS = set('''job_reason_detail personal_level intro location contribution collaboration
scale source_reading job_reason career company_opinion promotion_evidence team tracking job_process
ownership hobby backend_experience work_focus ai_coding coding_incomplete'''.split())

REFERENCES = [
 ('S1','ReAct 原始论文','https://arxiv.org/abs/2210.03629'),
 ('S2','RAG 原始论文','https://arxiv.org/abs/2005.11401'),
 ('S3','MCP 架构','https://modelcontextprotocol.io/docs/learn/architecture'),
 ('S4','Python 协程与任务','https://docs.python.org/3/library/asyncio-task.html'),
 ('S5','Python 线程与 GIL','https://docs.python.org/3/library/threading.html'),
 ('S6','LangGraph 持久化','https://docs.langchain.com/oss/python/langgraph/persistence'),
 ('S7','SSE 与 Last-Event-ID 标准','https://html.spec.whatwg.org/multipage/server-sent-events.html'),
 ('S8','SQLite 事务','https://www.sqlite.org/lang_transaction.html'),
 ('S9','OWASP Prompt Injection 防护','https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html'),
 ('S10','Kubernetes Pod 安全标准','https://kubernetes.io/docs/concepts/security/pod-security-standards/'),
 ('S11','RRF 排名融合公式','https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion'),
 ('S12','Java Thread.sleep 与 yield','https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/Thread.html'),
]

STANDARD = {}
def add(keys, answer, refs=''):
    for key in keys.split(): STANDARD[key]=(answer, refs)

add('react_exit',
 'ReAct 将决策、行动和观察交替执行，直到模型给出最终答案或命中终止条件。循环不结束常见于目标不明确、工具连续失败、重复查询、观察缺少关键信息以及上下文丢失目标。运行时应同时设轮次、总时长、Token/费用和重试预算，检测相同参数调用与连续无进展；超过限制进入明确失败、等待补充或人工处理。最终答案还需验证必需步骤和产物，不能只相信模型说“完成”。','S1')
add('single_multi patterns_difference',
 '单 Agent 用一个决策上下文调用工具，适合目标统一、工具可控的开放任务；Workflow 用预设节点和条件边控制流程，适合步骤稳定、合规或一致性要求高的业务；多 Agent 适合独立子目标可并行、上下文或权限需隔离的场景。选择时比较完成率、延迟、成本、可调试性和协调开销。任务复杂并不必然需要多个 Agent；常见组合是外层 Workflow 控制阶段，局部 Agent 自主探索。','S1,S6')
add('manager subagent parent_child handoff',
 'Manager 负责拆解目标、分配预算、跟踪依赖并汇总结果，Worker 处理独立子任务。任务契约包含 task_id、parent_id、目标、输入引用、权限、截止时间、版本和输出 Schema；结果返回状态、证据及产物引用。Worker 可预声明，也可受控动态创建，动态方式需限制数量、深度和权限。共享状态通过 reducer、事务或版本检查合并，副作用用幂等键；检测循环委派并传播取消，避免所有 Worker 同时修改同一业务对象。','S1,S6')
add('no_vector retrieval_choices',
 '向量存储按数据量、过滤能力、更新频率、一致性、延迟和运维条件选择。已有 PostgreSQL 且规模合适时，可评估向量扩展；搜索与关键词过滤需求强时，可评估搜索引擎的向量能力；需要专用向量服务时，再比较其索引、分片和混合检索能力。选型要用同一数据与查询集测试 Recall@K、P95、写入/删除传播和资源成本。保存 doc_id、chunk_id、权限、版本和来源；向量索引通常可重建，不能替代原文真值库。','S2')
add('rag_pipeline file_eval_extensions',
 'RAG 分离线入库和在线问答。入库先解析与清洗文档，保留结构、页码、来源和权限，再切分、生成向量并写入检索索引；文档更新与删除需同步处理版本。在线先理解问题并按需改写，执行权限过滤、关键词/向量召回、融合、重排与去重，再按 Token 预算组装证据，生成带引用的答案。证据不足时澄清或拒答。评测要把解析、召回、排序、引用和答案分别统计，便于定位故障。','S2,S11')
add('hybrid hybrid_evolution',
 'BM25 擅长实体、术语和精确词匹配，稠密向量检索擅长语义近邻，二者互补。分别召回后按文档/片段 ID 去重，用 RRF 等方法融合；RRF 的分数是各路 1/(k+rank) 之和，避免直接混加不同量纲的原始分数。随后用 Cross-Encoder 等重排器精排少量候选。结果高度重复时可用 MMR 在相关性与多样性间取舍。每增加一层都要验证 Recall、答案质量、延迟和成本，不能默认链路越长越好。','S2,S11')
add('mmr',
 'MMR 每轮选择“与查询相关、又与已选结果不太相似”的候选，常写为 λ×相关性 − (1−λ)×与已选结果的最大相似度。λ 越大越偏相关，越小越偏多样性。适用于重复段落很多、问题需要多方面证据的情况；对只需精确答案的任务可能稀释最相关证据。它不能代替召回和权限过滤，也不等同于重排模型。是否使用应通过证据覆盖率、重复率和最终回答质量验证。','S2')
add('chunk cross_chunk',
 '常见切分有固定长度加重叠、递归分隔符、标题层级、语义切分和父子块。固定切分简单稳定；结构切分适合章节明确文档；语义切分尝试保留主题完整性但成本较高。表格保留表头与单位，代码按函数/类及依赖处理。检索可用小块定位，再扩展相邻块或父章节，处理跨块证据。每块保存来源、位置、层级、版本和权限，重叠过多会增加重复与索引成本。','S2')
add('chunk_size',
 '块过小容易丢失定义、条件和指代，块过大则混入无关信息、降低向量区分度并占用上下文。可先按文档结构确定边界，再用 Token 上限控制长度，适度重叠；常见做法是小块召回、父块或相邻块扩展。没有适用于所有语料的固定最佳长度。实验中固定模型、问题集和检索预算，对比必要证据覆盖、Recall@K、引用准确率、延迟及 Token 成本。','S2')
add('ppl chunk_eval',
 'PPL 是语言模型对文本预测难度的度量，通常为平均负对数似然的指数。基于 PPL 的切分利用相邻句或局部窗口的预测难度变化，寻找可能的主题边界；不同实现的上下文、打分与边界规则并不统一。可对候选句边界计算条件负对数似然，平滑后取局部峰值，同时施加最小/最大块长及结构规则。分数高也可能来自术语、代码或 OCR 错误，不能直接等同语义断点。需与结构切分对照评测。','S2')
add('ppl_cost',
 '计算 PPL 需要能取得 Token 级对数概率的语言模型，按滑动窗口或句级条件上下文求负对数似然。模型大小、窗口重叠和重复前缀决定计算成本，可批处理、缓存和复用前缀，并先用小模型比较收益。阈值可按文档类型和分数分布调节，配合块长约束；固定阈值跨语料通常不稳。上线前同时比较检索质量、处理吞吐和入库成本，避免切分成本超过收益。','S2')
add('retrieval_eval ir_metrics retrieval_improvement',
 '先建立带相关证据标注的固定问题集，并按实体、同义表达、多跳、无答案和权限场景分层。Recall@K 衡量相关证据覆盖；MRR 是首个相关结果倒数排名的均值；NDCG 结合相关性等级与位置衡量排序。回答另测忠实性、完整性、引用正确率和拒答。对照实验固定语料版本、模型和预算，一次改变主要因素，报告样本、差值和不确定性；最终质量提升不应只由单个检索分数推断。','S2')
add('recall_drop',
 '召回下降先固定失败查询与历史基线，检查数据入库、解析/OCR、切分、索引数量和版本是否变化，再检查过滤条件、权限、查询改写、Embedding 版本、向量归一化与距离度量、ANN 搜索参数。区分“正确证据不在索引”“在索引但未召回”“召回后被过滤或重排丢失”。用少量查询做精确搜索或人工定位对照，逐层回放定位原因，再调整参数；不要一开始就扩大 TopK 掩盖问题。','S2')
add('embedding_stability',
 'Embedding 是否稳定要固定输入字节、分词/归一化、模型版本、推理配置和数值精度。即使输出有很小数值误差，也应比较余弦相似度与最近邻排名，不能只做逐浮点完全相等判断。模型版本或维度变化时，应重建匹配索引，避免新旧向量混用。向量稳定不等于检索稳定，ANN 参数、过滤与索引更新也会影响结果。测试同时覆盖重复调用、语义近邻、远离样本和业务 Recall。','S2')
add('reranker',
 'Reranker 对召回后的少量 query-document 对做更精细的相关性评分。常见 Cross-Encoder 联合编码问题和文档，效果通常比独立向量相似度更细，但计算量更高，因此放在召回之后。限制候选数和文本长度，批处理并设超时；失效时可回退融合排序。评测看 NDCG、MRR、必要证据覆盖与端到端延迟。重排不能找到召回阶段完全遗漏的文档，也不能承担权限授权。','S2,S11')
add('rewrite',
 'Query Rewrite 把口语、缩写、指代和多轮省略转换成适合检索的独立查询，也可拆成多个子问题或生成多查询。必须保留实体、时间、否定和业务约束，不能凭空补事实；通常同时保留原始查询，结合原查询与改写查询召回，避免单路漂移。对明确精确词可跳过改写。用意图保持率、证据覆盖和最终答案质量做对照，并检查改写带来的额外延迟与成本。','S2')
add('file_types multimodal',
 '解析先保留文档结构与来源，再做统一索引。PDF/扫描件需要版面分析、OCR 和阅读顺序；Word 按标题、段落及表格；PPT 保留页、标题和图文关系；Excel 保留工作表、表头、单位及行列坐标；图片可用 OCR 和视觉描述，必要时保留可回看的图像引用。表格与图片描述应绑定原页，避免单独文本失去条件。解析质量会限制召回上限，应检查缺页、乱码、表格错列及来源定位。','S2')
add('metadata',
 '每个片段至少保留 document_id、chunk_id、来源 URI、标题路径、页码或行号、版本、更新时间和访问控制元数据；还可加入语言、类型及父子关系。元数据支持检索过滤、引用定位、更新删除和故障回放。权限过滤必须来自服务端身份，不能让模型选择任意租户。文档更新时先构建新版本并验证，再切换可见版本，清理旧索引；保留版本能解释当时答案依据。','S2')
add('rag_tool',
 '固定 RAG 在回答前按固定流程检索，路径可控、延迟容易预算，适合多数知识问答。Agentic RAG 把检索封成工具，由模型决定是否检索、如何拆问题及是否再次检索，适合多跳和跨源探索，但调用次数、错误传播与评测更复杂。可采用混合设计：必需权限与证据检查由 Workflow 执行，局部检索策略由 Agent 决策，并设置轮次和预算。无论哪种方式，都要校验引用与证据不足场景。','S1,S2')
add('knowledge_graph',
 '知识图谱用实体、关系与属性表示结构化知识。查询先做实体链接和关系识别，再生成受限图查询或调用参数化图工具，执行权限与复杂度限制后返回相关子图。它适合多跳关系、路径解释和实体约束；向量检索适合非结构化语义，两者可结合。给模型的结果应包含节点/边标识、来源、时间和置信信息，避免无界遍历。图谱维护与实体消歧成本高，需用关系类问题验证收益。','S2')
add('mcp_skill',
 'Skill 描述某类任务何时执行、怎样组合步骤、规则与产物要求；MCP 提供 Host、Client、Server 之间的能力发现和调用协议，可暴露 tools、resources 和 prompts。两者可独立使用：Skill 可调用 CLI/OpenAPI，MCP 工具也可直接供宿主使用。MCP 的价值是跨宿主的统一契约、连接和能力协商，而非自动提供全部安全。凭据在环境变量也不解决工具发现、参数协议与授权；执行方仍需鉴权、限流、审计和幂等。','S3')
add('tool_contract registry',
 '工具注册表维护稳定 tool_id、版本、用途说明、输入/输出 JSON Schema、执行适配器、权限、超时、重试和幂等策略。模型只看经身份与场景过滤后的描述，返回工具名、参数和调用 ID；服务端查注册表，先校验参数、资源归属与风险，再调用真实实现，返回关联原调用 ID 的结构化结果。未知工具、额外字段和越权参数必须拒绝；模型可见性筛选不能替代执行端鉴权。','S3,S9')
add('tool_skill_interfaces manifest',
 'Tool 是可执行动作，契约是参数、结果与副作用；Skill 是任务方法及能力组合，包含适用条件、步骤和产物；Artifact 是持久化结果，需类型、版本、位置和访问权；Hook 在生命周期边界扩展观测、策略或记忆。清单应声明 ID、版本、入口、依赖、输入输出、权限、资源预算和兼容范围。描述用于发现，代码由受控执行器运行；版本需锁定到任务，升级不能静默改变运行中契约。','S3')
add('skill_route many_tools progressive',
 '先用租户、身份、任务阶段、权限和风险做确定性过滤，再按名称/描述/标签或语义检索召回少量能力，必要时让路由模型选择。渐进式披露先提供简短目录，选中后再加载详细说明、Schema 或资源，避免把所有工具全文塞进上下文。相似能力需明确适用与排除条件，歧义时询问或支持显式选择；执行前再次鉴权。用路由准确率、误选率、参数合法率、完成率和成本评测。','S3,S9')
add('workflow_skill freedom_cost',
 'Skill 可以是 Markdown 说明、配置、脚本或固定 Workflow 的组合，宿主负责加载和选择受控入口。Workflow-as-Skill 将验证过的流程封装为有版本、输入输出、权限和预算的能力，Agent 只需决定何时调用；内部仍按确定性节点执行。优点是复用、可审计、上下文短，代价是灵活性下降以及契约/版本维护。暂停、人工确认和异步等待必须返回明确状态，完成后再交回主决策循环。','S3,S6')
add('repeat_injection',
 '不应在每轮无条件重复注入整个 Skill。可在会话记录已激活的 Skill ID 与版本，保留最小规则摘要，只有切换能力、规则更新或关键边界需要重申时加载详细内容。每次实际调用仍独立做权限、参数和状态检查，不能把“曾注入过规则”当作授权。上下文压缩后要保留当前能力、关键约束、未完成步骤和版本；发现规则冲突时由明确优先级处理。','S3,S9')
add('dsl nl_dsl graph_validation',
 'DSL 应声明版本、节点 ID/类型、参数、类型化输入输出、边、条件、变量引用、超时与错误处理。编译流程是解析 Schema、检查节点和端口、校验引用及类型、分析可达性与依赖、验证循环边界，再生成执行图。自然语言先生成受限中间计划，再编译为 DSL，避免模型直接拼接任意代码。执行前做静态检查与沙箱测试，运行时仍检查权限和写入条件；静态合法不等于业务正确。','S6,S9')
add('compile_cache',
 '将 DSL 规范化后，按内容哈希、编译器版本、节点插件版本与策略版本缓存编译结果；配置变化使缓存失效。共享的是不可变图结构，每次执行创建独立 State、任务 ID 和访问上下文，不能复用有用户数据的运行对象。编译与执行延迟应分开测量；稳定高频流程可预编译，动态低频流程按需编译。缓存命中也不能跳过运行时鉴权和业务版本校验。','S6')
add('react_code react_core',
 '最小 ReAct Loop 的步骤是：构建受预算约束的上下文，调用模型，解析 final 或 tool_calls；若为工具调用，先做 Schema、权限及幂等检查，执行后把 Observation 反馈给下一轮，并保存检查点。循环检查取消、截止时间、最大轮数、重复调用和完成条件。Hook 可插在构建上下文前、模型前后、工具前后和任务结束时，用来接入 Memory、Skill、日志及策略。示例伪代码见附录 C。','S1')
add('plan_execute_code planner_executor step_refs',
 'Planner 将目标拆成有 ID、依赖、能力名、参数和输出 Schema 的步骤；Executor 按依赖拓扑执行就绪节点，对无数据或副作用冲突的步骤限并发；Summarizer 基于已验证结果汇总，不补造未完成结果。后续参数通过结构化引用绑定前序输出，禁止执行任意表达式。两城市天气可并行查询，温差步骤依赖两者，先统一单位再计算。计划有版本，失败按错误类型重试或重规划，并保存完成步骤。附录 D 给出参考实现。','S1,S6')
add('parallel',
 '无依赖、无共享写入冲突的工具调用可并行，例如读取两个独立城市天气；后一步依赖前一步结果、同一资源需顺序修改或调用间有配额限制时应串行。运行时用 DAG 表示依赖，对就绪节点限并发，并设置单次/总超时。并行结果按稳定调用 ID 关联，明确部分失败是取消、继续还是降级；共享状态通过显式合并和版本检查处理。并发可降低总耗时，但会提高峰值资源与失败处理复杂度。','S4,S8')
add('token_budget compression compression_when large_result preserve',
 '先按模型上下文窗口扣除输出预留及安全余量，再给系统规则、当前任务、近期消息、证据和工具结果分配预算。接近阈值时先去重和删除低价值日志，再将旧对话或长结果做结构化摘要；阈值依据模型和任务调优。必须保留目标、硬约束、已确认事实、未完成项、引用、工具副作用和授权状态。完整结果放外部存储，摘要带可回读引用；压缩后用回归任务验证约束与证据是否丢失。','S1,S6')
add('tenant domain_isolation shared_assets million_users',
 '身份与 tenant_id 由服务端认证生成，贯穿请求、任务、工具、存储和审计。每次操作按主体-资源-动作执行 RBAC/ABAC，并检查资源归属，模型不能选择任意租户。数据库行、缓存键、队列、工作目录和文件都需命名空间；强隔离场景采用独立库或执行环境。图和公共模板可复用不可变版本，私有记忆与文件按授权加载。海量用户采用无状态入口、持久任务队列及 Worker 池，逻辑 Agent 无需每用户常驻进程。','S9,S10')
add('sandbox container_isolation host_commands execution_location',
 '沙箱用于限制代码对文件、网络、进程和资源的影响。可组合独立进程/容器、非 root、只读文件系统、最小挂载、能力裁剪、系统调用约束、出口白名单与 CPU/内存/PID/时限配额；更强隔离可考虑虚拟机。宿主管授权、凭据、状态和审计，执行环境只获得短期最小能力。ps/kill 等动作需验证进程归属、稳定标识及允许范围，高影响操作需明确授权。容器共享内核，不能仅凭容器化宣称安全。','S9,S10')
add('sandbox_cost infra_choice',
 '按信任和风险分级隔离：低风险只读调用可共享服务，高风险代码使用独立受限环境。Serverless 通常便于弹性与托管，代价是冷启动、时限及运行环境限制；按任务创建 Pod 更便于镜像、资源和网络定制，但启动与运维成本更高。可用预热池和镜像缓存减延迟，同时严格清除上一任务状态。评估需比较冷/热启动 P95、资源单价、并发、隔离边界和清理可靠性，不能仅看平均启动时间。','S10')
add('sse_resume stream_nodes',
 'SSE 使用 HTTP 的 text/event-stream 单向推送，事件可包含 id、event 和 data。业务先持久化 task_id、递增 seq 和事件，再发送；客户端保存游标，重连携带 Last-Event-ID，服务端重放其后事件，客户端按 ID 去重。游标过期时返回快照并重新同步；心跳、代理缓冲、断开和背压需单独处理。节点可统一输出进度、内容增量、卡片、等待和完成事件。SSE 重连本身不持久化任务，也不保证副作用只执行一次。','S7')
add('async_tool resume_routing',
 '长工具调用可提交外部任务并返回 job_id，运行时持久保存 job_id 到 task_id/step_id、等待点和步骤版本的映射，标记 WAITING_EXTERNAL 后释放执行资源。外部结果通过认证回调或轮询到达，先校验签名、幂等事件 ID、任务归属和版本，再抢占恢复权继续执行。重复或过期结果不能推进旧流程，超时进入重试、取消或人工处理。通知丢失时可扫描等待任务并对账，不能靠最近一条聊天猜恢复位置。','S6,S9')
add('migration migration_compat',
 '迁移先固定旧任务的图、Schema、插件及模型策略版本，暂停新领取或让旧 Worker 排空，并确保任务已到可恢复边界。复制或转换检查点后，通过租约和递增 fencing token 转移执行权，旧 Worker 写入必须被拒绝。按稳定 task_id/幂等键恢复，核对外部副作用，使用小批量、可回滚切流和卡住任务扫描。新旧状态不兼容时保留旧运行版本执行完，不应强行让新代码解释旧快照。','S6,S8')
add('kafka',
 'Kafka 适合解耦高吞吐事件生产与消费，支持保留日志、按分区顺序读取和重放。可用于业务变更事件、状态通知或异步数据同步；不是所有低频任务都需要引入。生产端以事务 outbox 避免数据库提交与发消息之间的双写丢失，消费者用业务键幂等，明确分区键、重试和死信策略。消费位点提交不代表外部业务副作用已原子提交；跨系统的恰好一次需要限定边界并另行设计。','S8')
add('ray',
 'Ray 用于分布式任务、Actor 与资源调度，适合独立计算和需要状态服务的并行工作。业务输入、任务身份、重要检查点和结果引用应持久化，不能只存在某个 Worker 的内存中。任务重试、Actor 重启与控制面恢复有不同配置和前提，不能笼统保证 Head 重启后运行状态无损。设计时明确哪些计算可重放、哪些状态需恢复、哪些副作用需幂等，并对选定版本与部署逐项做故障测试。')
add('python_concurrency threadpool',
 '协程在事件循环中协作调度，适合大量可等待 I/O；线程适合阻塞 I/O 及可释放 GIL 的调用；多进程适合需要多核的 Python CPU 计算并提供内存隔离。常规有 GIL 的 CPython 线程不能同时执行 Python 字节码，某些无 GIL 构建及扩展另论。线程池由队列、Worker、Future、拒绝和关闭策略组成，Worker 阻塞取任务。限制队列与并发，避免同步阻塞事件循环或持锁跨网络等待。','S4,S5')
add('coroutine',
 'async def 定义协程函数，调用后得到协程对象，需 await、create_task 或由 asyncio.run 驱动。只有调度成任务才会与其他任务并发，连续 await 两个慢操作通常仍串行；可用 gather 或 TaskGroup 组织独立任务。asyncio.sleep(0) 会暂停当前任务，让其他就绪任务有机会运行，但不保证公平顺序，也不会把 CPU 密集代码变成并行。取消和超时需传播，finally 释放资源；示例见附录 I。','S4')
add('sleep0',
 'Thread.sleep(0) 指定零毫秒睡眠，不保证实际暂停，也不保证另一个线程获得执行机会，具体效果受 JVM 和系统调度影响。它不会释放当前持有的 monitor 锁，也不建立线程间的同步或可见性保证，不能用来修复竞态。Thread.yield() 同样只是可被调度器忽略的让步提示；Object.wait() 则在满足监视器要求时释放对应 monitor 并等待。等待条件应优先用锁、Condition、Latch 等同步机制，而不是 sleep(0) 自旋。','S12')
add('singleton',
 '枚举单例由类初始化机制保证单实例及线程安全，通常也能避免反序列化创建新实例。双重检查锁先在锁外判空，进入类级锁后再次判空，首次创建后复用；instance 必须声明 volatile，保证引用可见性和安全发布。只加锁而不处理不安全发布不能保证锁外读正确。两者通常限定在同一类加载器范围，不能当作跨进程全局单例；有依赖注入框架时可优先用容器管理生命周期。参考代码见附录 J。')
add('lru',
 'LRU 淘汰最久未使用的数据，可用哈希表定位节点、双向链表维护访问顺序。get 命中后移到最近端，put 更新或插入后也移到最近端，超容量删除最久端并同步哈希表，平均时间 O(1)，空间 O(capacity)。需处理容量为零、更新已有键和并发访问。LRU 不保证数据新鲜，业务缓存还需版本/TTL/主动失效；常见大量一次性扫描可能污染缓存。参考实现见附录 G。')
add('two_sum',
 '一次遍历，用哈希表保存已访问数值到下标。遍历 x 时先查 target-x 是否在表中，命中则返回两个下标，否则记录 x。先查询后插入可避免同一元素被重复使用，也能处理 [3,3]、target=6。平均时间 O(n)，空间 O(n)；若要求返回所有组合或输入已排序，需按题意调整。题面未明确约束时先确认返回下标还是数值、是否保证有解、能否修改输入。代码见附录 A。')
add('merge_sorted',
 '设第一个数组有效长度 m、第二个 n，第一数组总容量至少 m+n。用 i=m-1、j=n-1、k=m+n-1，从后向前把较大值写到 k，避免覆盖尚未读取的有效元素。循环直到第二数组耗尽；第一数组剩余元素已在正确位置。时间 O(m+n)，额外空间 O(1)。注意 m=0、n=0、相等元素及预留区不属于有效输入。代码见附录 B。')
add('bfs',
 '二叉树层序遍历用队列。根非空时入队，每轮先记录当前队列长度，只处理这一轮的节点，依次将左右孩子入队；这一轮的值构成一层。每个节点入队出队一次，时间 O(n)，辅助空间为最大层宽 O(w)，最坏 O(n)。空树返回空列表；不要在遍历中用不断增长的队列长度当本层边界。若需锯齿层序，可按层号调整输出方向。代码见附录 F。')
add('rearrange',
 '要重排数组使相邻元素不同，先统计频次；若最大频次大于 ceil(n/2)，则无解。可用最大堆，每次取频次最高且不同于上一项的值，输出后暂存上一项，下一轮再放回剩余次数，避免立刻重复。时间 O(n log k)，空间 O(k)，k 为不同值数量。还可按频次先填偶数位置再填奇数位置。必须保留原多重集合并验证相邻约束，空数组与单元素单独处理。代码见附录 E。')
add('provider_stream',
 '在模型适配层统一非流式结果、增量事件、结束原因、错误码和 usage，保留供应商特有能力而不强求虚假一致。按模型与租户设置并发、RPM/TPM 及费用预算，429/瞬时网络错误有限退避并尊重 Retry-After。降级前检查备用模型的工具/结构化能力与数据许可，防止切换破坏契约。成本按实际输入、输出和缓存用量配合价格版本计算，区分一次传输、一次模型调用和整个任务；流中断的重试还需处理重复内容。')
add('windows',
 '流处理按事件时间或处理时间分配窗口。滚动窗口互不重叠，滑动窗口按步长重复覆盖，会话窗口按无事件间隔分组。事件时间适合乱序数据，需水位线估计进度，配合允许迟到、触发器和状态清理；迟到数据可更新结果或进入侧输出。滑动窗口能连续观察变化，但状态与计算成本更高，可用增量聚合或小窗口复用。需明确时区、边界、去重、补数及更新/撤回语义，不能只用定时 SQL 等同完整流引擎。')
add('storage_choice',
 '时序存储按写入速率、指标基数、保留周期、查询模式和运维条件选择。监控指标常用专门指标存储；设备事件可选时序数据库或按时间分区的关系库；分析聚合可评估列式存储。模式通常含时间戳、实体/标签、指标值与质量标记，避免不受控高基数标签。结合分区、压缩、降采样和保留策略降低成本，热点查询可缓存；原始事件身份与版本需保留，便于迟到、纠错和重算。')
add('plan_state',
 '执行计划应持久保存为带版本的步骤 DAG，每步有 ID、依赖、能力版本、输入引用、状态、attempt、结果引用和错误。运行时从已提交边界找出依赖已满足的未完成步骤，领取执行权后恢复；等待人工或外部任务使用明确等待状态。成功副作用先查幂等结果，不重复执行；部分失败按策略重试、补偿或重规划。计划更新创建新版本，保留完成步骤的兼容结果，对旧回调和旧 Worker 做版本及 fencing 校验。','S6,S8')
add('metric_config recompute_442',
 '指标定义应采用受限 DSL 或配置，包含名称、输入事件、过滤条件、维度、窗口、聚合、单位与版本，先做类型和资源校验，避免直接运行任意表达式。结果绑定定义版本、输入快照和数据截止时间；新增、纠错或规则升级产生可幂等的重算任务。按依赖确定受影响范围，新旧版本并跑校验后切换，失败保留旧结果并标记过期。原始数据不足时明确不可重算，不能承诺从摘要还原全部细节。')
add('missing_data',
 '先定义上报链路和可追踪身份：设备/事件 ID、递增序号、发生时间、发送批次和接收回执。监控预期采样与实收数量、序号缺口、重复率、延迟和积压，分别核查设备缓存、网络重试、入口、队列、处理与数据库。通过端到端事件 ID 对账，丢失可请求补传，消费端幂等去重；未知缺失不能当作真实零值。补数保留原发生时间，并触发对应窗口重算。')
add('code_workflow_502',
 '迁移时可把原 Workflow 包装为有版本的能力或子图，保持原输入输出、分支、中断和错误处理语义。新运行时通过适配器调用，原流程内的工具也接统一权限、预算、观测和幂等契约。先用历史任务比较结果与合法轨迹，再灰度切换；运行中旧任务绑定旧版本执行完，或在兼容检查点处迁移。将其称为 Skill 只是封装，是否保留可靠性要靠语义映射与故障回归验证。','S6')

def load_original(root):
    source=root/'output/pdf/source/source_questions_original.pdf'
    text='\n'.join('\n'.join((p.extract_text() or '').splitlines()[2:]) for p in PdfReader(source).pages[7:88])
    pat=re.compile(r'(?m)^(?P<c>[一二三四五六七八九十]+、[^\n]+)\s*$|^(?P<r>(?:方向[12])?[一二三]面)\s*$|^(?P<n>\d+)\.\s+')
    matches=list(pat.finditer(text)); answers=[]
    for i,m in enumerate(matches):
        if m['n']:
            part=text[m.end():matches[i+1].start() if i+1<len(matches) else len(text)]
            q,a=part.split('参考答案：',1)
            a=re.sub(r'\s+',' ',a).strip()
            a=re.sub(r'(?<=[\u4e00-\u9fff]) (?=[\u4e00-\u9fff])','',a)
            # Repair English words split by the original PDF's line wrapping.
            for old,new in [('O bservation','Observation'),('T oken','Token'),('W orkflow','Workflow')]:a=a.replace(old,new)
            answers.append((re.sub(r'\s+','',q),a))
    assert len(answers)==642
    return answers

# Follow-up questions get a focused answer instead of repeating the full topic.
add('mcp_skill_626',
 '可以不用 MCP。Skill 可以直接调用受控 CLI、OpenAPI 或本地函数；MCP Server 也可直接向宿主提供工具而不经过 Skill。前者描述任务方法，后者统一能力接入，解决的问题不同。少量稳定内部接口直接适配即可；多个宿主需要复用工具、资源和连接管理时，引入 MCP 更有意义。是否采用取决于集成与运维成本，权限、审计和幂等无论使用什么协议都要落实。','S3')
add('mcp_skill_627',
 '技术上可以用 OpenAPI 或 CLI 完成同样的业务调用，MCP 并非唯一通道。MCP 的收益是把能力发现、Schema、调用/结果和连接生命周期统一，减少每个宿主分别适配命令和错误输出的成本。CLI 更贴近现有运维工具，但要处理参数、环境、返回值和进程权限；OpenAPI 擅长描述 HTTP 服务。临时生成代码还增加执行和审查成本。工具少且关系稳定可直接集成，跨宿主复用多时再比较协议收益。','S3')
add('mcp_skill_628',
 '环境变量只解决凭据如何注入，没有定义模型怎样发现能力、理解参数、处理结果和协商协议。MCP 可提供统一工具目录、输入输出契约、资源访问及客户端-服务端交互，使多个宿主复用同一服务。它不自动隐藏所有密钥或授予最小权限：凭据应由可信执行端持有，工具仍按用户和资源授权。若只有几条稳定命令且已有良好适配，MCP 带来的额外收益可能有限。','S3')
add('mcp_skill_629',
 'OpenAPI 主要描述 HTTP API 的路径、方法、参数、响应与安全方案；MCP 面向模型宿主与能力服务之间的交互，约定初始化/能力协商，以及 tools、resources、prompts 的发现与使用。可以把已有 OpenAPI 包装为 MCP 服务，二者不冲突。MCP 不是自动完成业务语义理解或权限治理的魔法层；客户端仍要选择能力、校验结果和执行授权。比较时看跨宿主复用与协议适配成本。','S3')
add('mcp_skill_630',
 '安全、审计、限流、幂等、版本管理和租户隔离都不是 MCP 独有能力，CLI 或 OpenAPI 接入也能实现。MCP 的主要价值是统一发现和交互契约，使治理能力更容易在宿主与服务间一致接入；实际策略仍由平台和服务执行。不能把“遵循 MCP”当成已经安全，也不能因治理可自行实现就否定协议标准化的价值。应分别评估协议互操作收益与平台已有治理能力。','S3,S9')
add('single_multi_151',
 '不一定。复杂性可能来自业务规则、状态恢复或一致性，这些往往更适合一个 Agent 加工具或受控 Workflow。多 Agent 只有在子目标可独立验证、上下文或权限需要隔离、并行收益明显时才值得引入。先用单 Agent 建基线，再比较拆分后的完成率、成本、延迟、通信次数和结果合并错误；角色数量增加本身不是架构改进。','S1')
add('single_multi_152',
 '至少需要清晰的职责边界、独立输入输出、可验证结果，以及可量化的并行或隔离收益。例如多个独立资料源调查可并行，最后由主流程核对证据；若角色都要反复共享全文并修改同一状态，协调成本可能更高。还需具备任务契约、预算、取消、失败恢复和合并策略。判断依据是同任务基准优于较简单方案，而不是任务描述看起来复杂。','S1')
add('subagent_116',
 '两种方式都可。预声明子 Agent 固定职责、工具、模型和输入输出，便于评测与权限审核；动态创建则根据当前任务生成受限配置，灵活但更难控制数量、成本和行为。生产中可采用预声明能力池加动态任务参数，先避免任意生成执行角色。动态方案也必须从已批准能力中选工具，限制深度、并发和总预算，记录父子关系并传播取消，禁止自行扩权。','S1,S6')
add('rearrange_400',
 '设数组长度 n、最高出现次数 f，存在相邻不同排列的充要条件是 f <= ceil(n/2)。必要性来自其他 n-f 个元素最多形成 n-f+1 个间隔，必须能分隔 f 个相同值；充分性可由按频率填偶数再填奇数位置的构造说明。空数组和单元素均可行。判断只需统计频次，时间 O(n)、空间 O(k)；若还要求输出一种排列，可用最大堆构造。')
add('ppl_cost_232',
 '不宜把一个固定阈值直接用于所有文档。可以在每篇或每类文档中对句级负对数似然做归一化，用分位数、局部峰值或滑动均值差形成自适应候选，再约束最小/最大块长。标题、列表、表格和代码边界应优先遵循结构规则。阈值越敏感通常产生更多小块与索引成本，过于迟钝又混合主题；在标注问题集上调参，并用独立测试集验证，防止只拟合入库样本。','S2')

def choose(row, original, project):
    key=row['key'];stem=re.sub(r'_\d+$','',key)
    if stem in PERSONAL_KEYS:
        return {**row,'text':original,'status':'个人经历答题模板','evidence':'按本人真实经历补充；不代填职级、规模、贡献或效果数字','answer_kind':'personal'}
    if stem in PROJECT_KEYS and key not in STANDARD_ONLY_KEYS:
        return {**project,'answer_kind':'project'}
    answer,refs=STANDARD.get(key,STANDARD.get(stem,(original,'')))
    return {**row,'text':answer,'status':'八股文 · 通用知识','evidence':('参考资料：'+refs if refs else '通用原理与答题方法；不表示项目已实现'),'answer_kind':'standard'}
