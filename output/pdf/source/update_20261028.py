"""Build the current project edition without changing the supplied question PDF."""
from pathlib import Path
import sys, re, json, html, hashlib, textwrap
from collections import Counter
sys.path.insert(0, str(Path(__file__).parent))
import build_full_rewrite as base
import standard_answers_20261028 as standards
from pypdf import PdfReader
from reportlab.platypus import Paragraph, Spacer, PageBreak, KeepTogether, Preformatted
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm

ROOT = base.ROOT
SOURCE = ROOT/'docs/Agent开发面经-技术问题与备考建议.pdf'
OUT = ROOT/'output/pdf'
PDF = OUT/'Agent开发面经-轻记工程更新版-2026-10-28.pdf'
MD = OUT/'Agent开发面经-轻记工程更新版-2026-10-28.md'
UPDATES = {}
class CurrentDoc(base.InterviewDoc):
    def decorate(self, canv, doc):
        super().decorate(canv, doc)
        canv.saveState()
        canv.setFillColor(base.colors.white)
        canv.rect(20*mm, 8*mm, 145*mm, 5*mm, fill=1, stroke=0)
        canv.setFont('CN',7.6)
        canv.setFillColor(base.GRAY)
        canv.drawString(21*mm,10*mm,'轻记 · 个人健康记录 Agent | 2026-10-28 工程快照')
        canv.restoreState()
def put(keys, text, evidence):
    for key in keys.split():
        UPDATES[key] = (text, evidence)

put('limits react_exit token_budget',
    '轻记采用受控工作流而非开放式 ReAct。可选追问受轮次限制，必需字段不全不会强行保存。当前每个工作流最多预留 32 次模型调用、累计 400000 字节，其中规划最多 4 次、安全分类最多 8 次；发送前持久化计数，失败也占预算。单次外发限制 90000 字节。运行段默认 120 秒、并发槽 2，普通模型超时 45 秒，安全分类 8 秒。字节和次数预算不等于精确 Token 计费，也不代表已实现自动摘要压缩。',
    'backend/app/infrastructure/repositories/common.py; backend/app/modeling/boundary.py; backend/app/common/config.py; backend/app/common/execution_budget.py')
put('tool_contract io_contract',
    '当前不是模型直接调用任意 Python 工具，而是先返回 ProcessingPlan 等固定 Schema，再由本地审核、取数、路由和执行。模型任务包括规划、安全分类、单条纠错、草稿补丁、营养估算和受限建议选择。每项用途有固定请求字段与输出类型，外发还要消费一次性授权；模型不能提交 SQL、任意字段或记录 ID 来取得数据。业务写入继续经过证据、完整性、幂等和确认检查。',
    'backend/app/modeling/payloads.py; backend/app/modeling/boundary.py; backend/app/contracts/workflows.py; backend/app/workflows/planning.py')
put('trace observability_access trace_eval',
    '已有运行元数据白名单、HTTP 关联 ID、节点/模型/数据库/锁/任务分层耗时，以及 Prometheus Counter、Histogram、Gauge。模型传输成功、Schema 成功和业务完成分别计数。业务质量贡献在同一业务事务写入，按保留窗口聚合并参与删除。日志和 span 不采集健康原文、完整 prompt 或模型响应；业务 process_trace 不能直接复制到遥测。指标导出默认关闭，尚无完整集中观测平台或分角色运维权限。',
    'backend/app/observability/metrics.py; backend/app/observability/spans.py; backend/app/observability/contracts.py; backend/app/api/observability.py; backend/tests/test_observability.py')
put('select_context context_engineering',
    '上下文按任务目的构造。首轮规划只收到本次文字、时间和是否选中记录，不再默认携带最近 12 条历史。本地审核计划后，从固定视图取必要数据；纠错绑定单条记录，补充只发送当前草稿和待补字段，营养估算只发送本次食物与分量。建议使用白名单结构化投影，不发送历史 raw_text。目的、资源和实际 payload 都经授权核对，最终返回用途与数据类别说明。',
    'backend/app/modeling/payloads.py; backend/app/modeling/projections.py; backend/app/modeling/boundary.py; backend/tests/test_model.py')
put('deployment architecture publish',
    '当前入口是 Taro/React/TypeScript 微信小程序，连接独立 FastAPI JSON API；旧 Web 前端已废弃，根路径不再托管页面。Engine 复用聊天、计划、分析图，health.db 保存业务事实，checkpoints.db 保存图进度。开发启动用 start.ps1；仓库另有 Docker 加固部署配置。后台任务与 Web 仍共享唯一 Engine，数据目录所有者锁阻止第二实例，不是独立 Worker 集群。正式对外服务仍需 HTTPS、鉴权及相应部署验收。',
    'README.md; miniprogram/package.json; backend/app/main.py; backend/app/infrastructure/data_owner.py; compose.hardened.yaml; start.ps1')
put('layers architecture_layer',
    '接口在 api，工作流编排在 workflows，模型适配与外发边界在 modeling；health 承担营养、目标、分析和长期事实，privacy 管血缘删除，jobs 管任务，observability 管受控观测，infrastructure 管事务和仓储。LangGraph 提供状态图与暂停恢复，业务约束由这些模块共同执行。当前是一个应用内的模块化分层，不是已经独立部署的微服务或通用插件平台。',
    'backend/app/main.py; backend/app/workflows/engine.py; backend/app/modeling/parser.py; backend/app/infrastructure/repositories/repository.py')
put('provider model_now',
    '模型通过兼容 Chat Completions 的传输层接入，供应商地址、模型名和凭据由运行配置显式提供，不能把示例模型名当作运行时事实。源码不再硬编码密钥；支持仓库外密钥文件，开发模式也可用环境变量。配置全空进入演示模式，部分缺失会失败；模型失败不会悄悄切换演示营养。当前没有多供应商路由网关，换模型需回归结构化输出、错误映射和数据边界。',
    'backend/app/common/config.py; backend/app/modeling/parser.py; backend/app/modeling/transport.py; backend/tests/test_config.py')
put('provider_stream timeout',
    '当前模型接口非流式，普通调用默认 45 秒超时，安全分类默认 8 秒，并受运行段截止时间、响应大小和持久化调用预算约束。解析失败返回安全错误，营养失败保留未知，建议选择失败回退本地已批准表达；它们不是同一降级语义。后台可重试任务有独立退避与失败分类，不等于所有模型请求自动重试。尚无供应商级 Token 费用统计或自动备用模型切换。',
    'backend/app/modeling/transport.py; backend/app/modeling/errors.py; backend/app/common/config.py; backend/app/jobs/runner.py')
put('privacy cloud',
    '本地存储不等于模型模式下不外发。当前可由服务端或单次请求启用 local_only，统一禁止规划、纠错、估算与表达等外部调用，该约束随暂停流程保存，恢复不能放宽。模型模式按用途授权与最小字段投影发送，并披露实际调用类别。加固部署提供受限模型出口和密钥文件配置；若要求完全内网运行，需使用纯本地模式或经过验证的内网模型，并检查部署出口，不能只依赖提示词。',
    'backend/app/modeling/boundary.py; backend/app/modeling/payloads.py; backend/app/common/config.py; deploy/network-policy.md')
put('capacity python_concurrency commercial_stack',
    '同步模型 I/O 仍在线程内执行，但已不再在整个网络等待期间持有 Engine 全局锁：model_io 释放本地锁，返回后重取并检查执行代次及读依赖。相同工作流/会话通过键控锁协调，SQLite 写事务仍需互斥；RuntimeLimiter 默认允许两个运行段。数据目录仅允许一个 Engine，不能直接增加 Uvicorn workers 扩容。现有局部基准不代表生产 QPS，应分别测锁等待、模型、数据库和端到端负载。',
    'backend/app/workflows/concurrency.py; backend/app/common/execution_budget.py; backend/app/infrastructure/data_owner.py; backend/tests/test_workflow_concurrency.py')
put('resource',
    '交互与后台仍共享一个 Engine，但已有运行并发槽、执行截止时间、数据库与锁的预算检查。后台规划与执行分开，执行器本期并发为 1，任务通过持久状态恢复。模型网络期间释放全局锁，返回时复核执行权。尚无实时/离线独立 Worker 池、优先级公平调度或精确 Token 费用配额；这些需要按真实负载继续设计。',
    'backend/app/common/execution_budget.py; backend/app/jobs/runner.py; backend/app/workflows/concurrency.py')
put('lease fencing shared_write',
    '当前后台任务已有原子领取、lease_until、owner_token、递增 fencing_token 和 job generation。心跳续租；提交与失败更新必须匹配有效执行权，旧执行者不能覆盖新结果。业务效果用稳定幂等键，报告/通知等通过事务和 outbox 协调。该协议提高单 Engine 内的恢复可靠性，并不表示已支持分布式 Worker；独立运行进程和共享集群属于后续阶段，外部服务副作用还需自己的幂等/对账协议。',
    'backend/app/infrastructure/repositories/jobs.py; backend/app/infrastructure/repositories/outbox.py; backend/app/jobs/context.py; backend/tests/test_scheduler_reliability.py')
put('job_process parallel close_page',
    '后台规划器按时间生成持久任务，执行器负责领取、执行、心跳与重试，当前仍同进程共享唯一 Engine。关闭小程序后，只要服务存活就能生成站内消息；服务停机期间不会实时通知，重启按限定窗口补跑。失败区分可重试与终止，达到上限进入可处理的失败状态。没有设备推送、SSE 字流续传或独立分布式队列；交互请求的重试仍使用原 request_id。',
    'backend/app/jobs/scheduler.py; backend/app/jobs/planner.py; backend/app/jobs/runner.py; backend/app/jobs/manage.py; backend/tests/test_scheduler_operations.py')
put('memory_layers memory_categories',
    '记忆分四层：工作记忆保存当前 State、草稿、等待点和恢复凭据；事件账本保存健康事件及修订；结构化长期事实保存偏好、过敏、专业限制等及其来源和有效期；派生层保存规律、复盘、计划与归档摘要。派生结果记录窗口、方法版本和来源版本，不能当作原始事实。工作记忆可到期清理，已保存事件按独立保留策略管理；当前不依赖向量记忆或自动对话摘要。',
    'docs/四层记忆实现说明.md; backend/app/workflows/state.py; backend/app/health/facts.py; backend/app/health/derived.py')
put('memory_conflict conflict_rules',
    '事件纠错保留事件 ID、递增版本并保存修订；长期事实另有结构化主题、值、极性、来源、有效期和变更记录。明确改口的普通偏好可替代旧值，临时偏好不静默覆盖长期信息；冲突可让用户选择。过敏和专业限制到期进入待核对，继续保留保护，不因低使用频率遗忘。用户确认只是明确陈述，转述专业建议不等于独立核验；旧自由文本不自动猜成可靠结构。',
    'backend/app/health/fact_resolution.py; backend/app/health/facts.py; backend/app/contracts/facts.py; backend/tests/test_structured_facts.py')
put('memory_write memory_api',
    '记忆写入由业务边界控制：完整事件经校验、营养准备和事务入账；明确长期信息走结构化事实服务，带证据、有效期和修订。助手建议、模型推理或一次偏咸反馈不会自动升级为长期事实。读取使用与任务目的匹配的投影，纠错后让相关派生结果过期。管理接口支持新增、修改、撤回、冲突选择、历史与隐私删除；它仍是领域服务，不是动态 Memory 插件协议。',
    'backend/app/health/facts.py; backend/app/api/profile.py; backend/app/infrastructure/repositories/facts.py; backend/app/modeling/projections.py')
put('forget',
    '保留期与隐私删除是两套机制。待补草稿和待确认计划默认闲置 7 天过期，完成流程上下文默认 30 天清理，同时留下最小请求凭据避免旧请求重写；一年后完整过期月份归档为摘要。用户删除则按实际数据血缘预览并清理相关副本、派生结果与 checkpoint，失败可恢复。长期事实有有效期和冲突规则，过敏等到期仍保护并提示核对，不能因少用自动移除。',
    'backend/app/common/config.py; backend/app/health/retention.py; backend/app/privacy/deletion.py; backend/tests/test_privacy_deletion.py')
put('history_storage preserve',
    '业务聊天与工作流状态仍可能包含健康信息，不能说全部匿名化。当前新记录按已验证片段保留最小原文，记录来源、版本、查询范围和原文副本血缘。删除先预览实际影响；共享原文可重绑独立片段，无法证明独立的旧数据扩大预览范围，变化后重新确认。持久删除作业清理业务副本和 checkpoint，恢复前先完成清理；观测日志另用白名单，不能混用业务正文。',
    'backend/app/privacy/fragments.py; backend/app/privacy/provenance.py; backend/app/privacy/deletion.py; backend/tests/test_privacy_deletion.py')
put('create_update format_constraints recall_hard',
    '新增与纠错走不同路径。新事件生成 ID；纠错候选由本地查询并绑定，歧义时先让用户选择，模型只返回选中记录的受限修改，不能自由指定数据库 ID。提交复核来源和版本，保留修订历史。食物、分量或做法变更会使营养指纹失效并重新准备。检索仍是结构化 SQL，不存在向量重排器；如果以后加重排，也应只允许返回已批准候选并校验完整性。',
    'backend/app/modeling/payloads.py; backend/app/evidence/corrections.py; backend/app/workflows/chat_parsing.py; backend/tests/test_grounding.py')
put('container_isolation',
    '仓库已有 Docker 加固配置：非 root 用户、只读根文件系统、移除 capabilities、禁止提权、CPU/内存/PID 限额、受限挂载和模型出口代理，并配置健康检查及监督脚本。这用于隔离受信任的应用运行环境，不是允许用户任意执行脚本的沙箱。网络边界与重启恢复必须在目标部署验收，不能把配置存在说成已完成全部容器实测，更不能等同多租户安全。',
    'Dockerfile; compose.hardened.yaml; deploy/network-policy.md; scripts/verify-runtime.py; scripts/supervise-runtime.py')
put('evolution own_work enterprise_gaps',
    'LangGraph 之外，工程已经实现请求幂等、恢复回执、模型外发授权与预算、结构化事实冲突、数据血缘删除、受控输出、安全路由、可靠后台任务和分层观测。下一步优先补真实营养来源、真实模型评测及目标部署验收，再按需要做鉴权、多用户和进程拆分。没有文档 RAG、MCP、动态 Skill、任意代码执行或多 Agent 集群，不把这些当作既有成果。',
    'backend/app/workflows/runtime.py; backend/app/modeling/boundary.py; backend/app/privacy/deletion.py; backend/app/jobs/runner.py; backend/app/observability/service.py')
put('injection trust beyond_prompt repeat_injection',
    '把用户文本和模型输出视为待验证数据。系统不授予任意 SQL 或命令执行权，模型计划只能触发本地批准的固定视图与业务动作，外发按用途、资源、Schema 和一次性授权检查。输出策略只接受已批准的 point_id/variant_id，数字、日期、保存状态和追问由本地生成；未知标识或自由正文失败回退本地表达。输入安全路由与写入校验也在恢复路径执行。提示词只是辅助，执行端约束才是边界。',
    'backend/app/modeling/boundary.py; backend/app/modeling/payloads.py; backend/app/policy/output.py; backend/app/workflows/safety.py; backend/tests/test_output_policy.py')
put('quality ground_truth regression reliability_quality',
    '验证分输入理解、业务不变量、运行故障和安全四层：用合成案例与 mock 响应检查日期/分量、未知营养、证据、计划确认；用重放和故障注入检查幂等、租约、删除恢复、超时及旧结果拒写；用固定安全样例检查风险路由和受限输出。仓库有测试与历史验证报告，本次资料更新未重跑完整应用测试。测试通过不等于真实模型准确率、用户增长或医学效果，生产 A/B 和统一 Judge 仍需另建。',
    'backend/tests/test_harness.py; backend/tests/test_runtime_recovery.py; backend/tests/test_scheduler_reliability.py; backend/tests/test_safety_routing.py; docs/evidence/safety-evaluation-2026-10-26.json')
put('latency_metric latency',
    '当前非流式 JSON，不能把请求 duration_ms 称为 TTFT。已有 HTTP、节点、模型、数据库、锁等待和后台任务的分层计时，可区分上游慢、排队慢和本地事务慢；用户端网络与小程序渲染还需另测。模型超时和本地兜底要单独计数，恢复段耗时与跨用户等待的完整任务耗时也不同。仓库的局部观测基准不代表生产容量或真实模型成本。',
    'backend/app/observability/metrics.py; backend/app/api/request_context.py; backend/app/workflows/concurrency.py; scripts/benchmark-observability.py')
put('sync_async',
    '交互工作流仍用同步图调用，在完成或等待用户时返回 JSON。后台由 asyncio 调度，规划与任务执行分离，阻塞业务在线程中执行，并使用持久任务、租约和恢复。不存在同步/异步两套 ReAct 引擎；只是不同入口复用同一业务边界。未来若将交互也改为异步提交，需要明确任务状态和结果获取协议，不能把函数改成 async 就认为完成进程解耦。',
    'backend/app/workflows/runtime.py; backend/app/jobs/scheduler.py; backend/app/jobs/runner.py')

put('compression compression_when',
    '当前不做模型生成的对话压缩摘要，也没有按 Token 水位触发压缩。模型上下文按用途白名单重建，规划不带近期历史，建议读取最小结构化投影；调用次数与累计字节预算另外限制成本和失控。未选入上下文的事件可再查，但按保留策略删除的消息和一年归档后的逐餐细节不能恢复。裁剪、压缩和保留期删除应分别解释，不能把摘要说成无损记忆。',
    'backend/app/modeling/payloads.py; backend/app/modeling/projections.py; backend/app/health/retention.py')
put('validated_scope',
    '可核验测试已覆盖追问和提交恢复、运行限额、模型传输、证据与输出约束、结构化事实、血缘删除、调度租约和观测隐私等。当前还有微信小程序协议、恢复和交互测试，旧版的 124 个后端与 10 个 Web 测试数字已不适用。本次只做面试资料更新及文档校验，完整测试数和结果应引用对应日期的运行记录；合成或 mock 用例通过不代表真实模型、生产容量或医学效果。',
    'backend/tests; miniprogram; docs/evidence; README.md')
put('graph_complexity',
    '轻记将聊天、计划和分析分别编译为图。聊天的复杂性来自安全入口、多任务解析、关键追问、部分保存、纠错和受控回复；计划图负责准备、确认、激活，分析图负责规律与复盘。节点调用普通领域函数，业务校验可独立测试。规模增长时先按输入输出和副作用边界拆模块或子图，不用节点数证明能力，也不把多层嵌套当作已经解耦。',
    'backend/app/workflows/chat_flow.py; backend/app/workflows/planning.py; backend/app/workflows/engine.py')

PATHS = {
 'graphs.py':'workflows/engine.py', 'parser.py':'modeling/parser.py',
 'repository.py':'infrastructure/repositories/repository.py', 'schemas.py':'api/schemas.py',
 'drafts.py':'chat/drafts.py', 'nutrition.py':'health/nutrition.py', 'advice.py':'health/advice.py',
 'replies.py':'chat/replies.py', 'conversation.py':'chat/feedback.py',
 'services.py':'health/analysis.py', 'targets.py':'health/targets.py',
 'scheduler.py':'jobs/scheduler.py', 'config.py':'common/config.py',
}

def revised(row):
    key = row['key']
    stem = re.sub(r'_\d+$', '', key)
    if key in UPDATES or stem in UPDATES:
        row['text'], row['evidence'] = UPDATES.get(key, UPDATES.get(stem))
        row['status'] = '工程现状与边界'
    else:
        s = row['text']
        for old,new in {
          '本地单用户网页原型':'单用户微信小程序原型', '本地网页原型':'单用户微信小程序原型',
          '本地网页应用':'微信小程序与本地 API 应用', '本地网页':'微信小程序',
          '前端在浏览器':'前端在微信小程序', '关闭网页':'关闭小程序',
          '待补流程超过 366 天':'待补流程默认闲置超过 7 天',
          '待补超过 366 天':'待补默认闲置超过 7 天',
          '源码仍有硬编码密钥':'源码已改为显式运行配置与仓库外密钥文件',
          '可进一步加入节点计时与按 ID 回读证据':'已有节点计时，后续可继续完善证据回读体验',
          '还要补预算、工具执行、权限和取消':'还需扩展通用工具执行和权限体系，并复用现有预算及取消边界',
          '若以后增加 Worker':'若以后拆分独立 Worker 进程',
          '如果以后增加 Worker':'如果以后拆分独立 Worker 进程',
          'graphs.py':'workflows/engine.py',
          'services.py':'health/analysis.py 等领域模块',
          '轻记可改进为节点级计时、Token 预算、原记录按 ID 二次读取、任务版本和更精确的数据血缘':'轻记已有节点级计时、调用次数与字节预算、任务版本和实际读取血缘，后续可补精确 Token 预算及更完善的证据回读',
        }.items(): s=s.replace(old,new)
        row['text']=s
    # Evidence points to current modules; remove obsolete function-level anchors.
    e = re.sub(r'[:：][^；;|]*', '', row['evidence'])
    for old,new in PATHS.items():
        e=e.replace('backend/app/'+old, 'backend/app/'+new)
    e=e.replace('frontend/src/App.tsx','miniprogram/src/app.tsx').replace('frontend/package.json','miniprogram/package.json').replace('frontend/src/App.test.tsx','miniprogram').replace('frontend/src/','miniprogram/src/')
    paths = re.findall(r'(?:backend|frontend|miniprogram|docs|scripts|deploy)/[A-Za-z0-9_./-]+', e)
    # Existing file citations only: old shorthand function references were obsolete.
    paths = [p for p in paths if (ROOT/p).exists()]
    if 'backend/app/workflows/engine.py' in paths:
        paths += ['backend/app/workflows/chat_flow.py','backend/app/workflows/runtime.py']
    for p in ['README.md','Dockerfile','compose.hardened.yaml','start.ps1']:
        if p in e: paths.append(p)
    if not paths: paths=['README.md']
    row['evidence']='; '.join(dict.fromkeys(paths))
    return row

def original_questions():
    text='\n'.join('\n'.join((p.extract_text() or '').splitlines()[2:]) for p in PdfReader(SOURCE).pages[1:31])
    pattern=re.compile(r'(?m)^(?P<company>[一二三四五六七八九十]+、[^\n]+)\s*$|^(?P<round>(?:方向[12])?[一二三]面)\s*$|^(?P<num>\d+)\.\s+')
    ms=list(pattern.finditer(text)); out=[]; company=round_name=None
    for i,m in enumerate(ms):
        if m['company']: company=m['company'].strip()
        elif m['round']: round_name=m['round'].strip()
        else:
            part=text[m.end():ms[i+1].start() if i+1<len(ms) else len(text)]
            q=re.split(r'\n\s*\n|\n本轮小结',part)[0]
            out.append(dict(company=company,round=round_name,number=int(m['num']),question=re.sub(r'\s+',' ',q).strip()))
    return out

def main():
    rows=base.extract(); originals=original_questions()
    stock=standards.load_original(ROOT)
    assert len(originals)==len(rows)==642, (len(originals),len(rows))
    for r,o in zip(rows,originals):
        assert r['number']==o['number'] and r['company']==o['company'] and r['round']==o['round'], (r,o)
        assert re.sub(r'\s','',r['question']).startswith(re.sub(r'\s','',o['question'])), (r['gid'],r['question'],o['question'])
        r['question']=o['question']
        before=dict(r)
        revised(r)
        selected=standards.choose(before,stock[r['gid']-1][1],r)
        r.update(selected)
    (OUT/'source/updated_answers_20261028.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    P=base.P
    story=[Spacer(1,20*mm),P('Agent 开发面经','title'),P('工程实答 + 八股文','title'),P('2026-10-28 更新 · 642 题 · 11 家公司 · 23 轮','subtitle'),Spacer(1,12*mm)]
    intro=[
      '保留原 PDF 的公司、轮次、题号与全部问题。工程涉及的题根据当前源码回答；工程未涉及的题提供八股文答案，直接解释概念、实现流程、取舍、失败处理与常见追问。',
      '当前项目：微信小程序 + FastAPI + LangGraph + SQLite。面向单用户自然语言健康记录、纠错、计划确认与复盘；不是通用企业 Agent 平台。',
      '工程题重点：模型外发授权与预算、纯本地模式、四层记忆、血缘删除、受控输出、可靠调度、并发控制和分层观测；每题附当前源码依据。',
      '八股文重点：RAG、混合检索与重排、多 Agent、ReAct、Skill/MCP、DSL、分布式任务、SSE、沙箱、Python/Java 和算法。不再以“工程没有实现”代替知识回答；通用设计也不写成个人项目经历。',
      '验证口径：本次核对题目完整性、源码路径与 PDF 排版，未运行真实模型、未访问个人健康数据库、未重跑全量应用测试。历史报告只代表其记录的环境与时间。个人贡献、客户规模、薪酬与效果数字需本人据实补充。',
    ]
    for s in intro: story.append(P(s))
    story += [PageBreak(),P('两分钟项目介绍','h1'),P('我做的是“轻记”个人健康记录 Agent：用户在微信小程序用自然语言记录饮食、体重、睡眠、运动和生理周期，系统先理解输入，再由本地规则判断是否需要追问、能否保存和是否需要确认。LangGraph 管理暂停与恢复，SQLite 分别保存业务事实和工作流进度。'),P('工程重点是把模型的不确定性限制在可验证范围内：模型只产生结构化计划或选择已批准的表达，业务计算、证据校验、幂等写入和目标确认由代码执行。模型只看本次用途允许的数据，纯本地模式可以禁止外发。记录纠错和删除会沿数据血缘处理相关派生结果。'),P('可靠性方面有请求重放、运行预算、模型 I/O 期间释放锁、后台任务租约与 outbox，以及不采集健康原文的运行观测。目前仍是单用户、单 Engine 原型。个人负责的模块应按实际经历补充。'),Spacer(1,8*mm),P('答题方法','h2'),P('工程题按“实际机制、一次执行路径、验证与局限”讲；八股文按“定义、核心步骤、取舍、失败与验证”讲。追问恢复时同时讲 checkpoint 与业务幂等；问权限时同时讲可见范围与执行端授权；问指标时说明分母、窗口和失败/降级口径。算法代码见附录，个人经历按模板补充真实信息。'),PageBreak()]
    md=['# Agent 开发面经：轻记工程更新版','', '工程快照：2026-10-28','']+intro+['']
    pc=pr=None
    for r in rows:
        heads=[]
        if r['company']!=pc:
            if pc: story.append(PageBreak())
            heads.append(base.heading(r['company'],f"c-{r['gid']}")); md+=['## '+r['company'],'']; pc=r['company'];pr=None
        if r['round']!=pr:
            heads.append(base.heading(r['round'],f"r-{r['gid']}",1));md+=['### '+r['round'],''];pr=r['round']
        label=f"{r['number']}. {r['question']}"
        q=P(label,'q');q._gid=r['gid']
        # Keep each Q&A together; no question is orphaned at a page boundary.
        story.append(KeepTogether(heads+[q,P('<b>参考答案：</b>'+html.escape(r['text']),'answer',True),P(f"[Q{r['gid']:03d} · {r['status']}] 依据：{r['evidence']}",'evidence')]))
        md += ['#### '+label,'',r['text'],'',f"[Q{r['gid']:03d} · {r['status']}] 依据：{r['evidence']}",'']
    story += [PageBreak(),base.heading('编码题参考实现','appendices')]
    base.verify_algorithms()
    for letter,title,code,note in base.APPENDICES:
        note=note.split('轻记')[0].rstrip('；，。 ')+'。' if '轻记' in note else note
        story.append(KeepTogether([base.heading(f'附录 {letter}：{title}',f'a-{letter}',1),P(note),Preformatted(textwrap.dedent(code).strip(),base.ST['code'],maxLineLength=83),Spacer(1,4*mm)]))
        md += [f'## 附录 {letter}：{title}','',note,'','```',textwrap.dedent(code).strip(),'```','']
    story += [PageBreak(),base.heading('通用知识参考资料','standard-refs')]
    for sid,label,url in standards.REFERENCES:
        story.append(P(f'<b>{sid} · {html.escape(label)}</b><br/><link href="{html.escape(url)}" color="#0B66A3">{html.escape(url)}</link>','body',True))
        md += [f'- {sid} [{label}]({url})']
    doc=CurrentDoc(PDF);doc.multiBuild(story)
    MD.write_text('\n'.join(md),encoding='utf-8')
    result=PdfReader(PDF);text='\n'.join(p.extract_text() or '' for p in result.pages)
    ids=re.findall(r'Q(\d{3})',text)
    assert len(ids)==642 and len(set(ids))==642
    assert 'sk-' not in text
    assert all('轻记' not in r['text'] for r in rows if r['answer_kind']=='standard')
    summary={'source':str(SOURCE),'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'question_count':len(rows),'answer_kinds':dict(Counter(r['answer_kind'] for r in rows)),'company_count':len({r['company'] for r in rows}),'round_count':len({(r['company'],r['round']) for r in rows}),'pages':len(result.pages),'pdf':str(PDF),'markdown':str(MD),'original_questions_matched':True,'full_application_tests_rerun':False,'question_pages':doc.page_map}
    (OUT/'source/verification_20261028.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k!='question_pages'},ensure_ascii=False))

if __name__=='__main__':main()
