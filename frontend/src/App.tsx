import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
} from "react";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  Bell,
  BookOpen,
  CalendarDays,
  Check,
  ChevronDown,
  CircleHelp,
  Clock3,
  Leaf,
  LoaderCircle,
  MessageSquare,
  Plus,
  Send,
  Settings2,
  Sparkles,
  Target,
  Trash2,
  TrendingUp,
  X,
} from "lucide-react";
import { api, ApiError, requestId } from "./api";
import type {
  Card,
  Daily,
  DefaultTargets,
  Event,
  Evidence,
  Fact,
  Health,
  Insight,
  Message,
  Month,
  Notice,
  Plan,
  Preferences,
  ProcessStep,
  Profile,
  Review,
  Targets,
  Workflow,
} from "./types";

type Tab = "chat" | "history" | "plans" | "insights" | "settings";
const labels: Record<string, string> = {
  meal: "饮食",
  weight: "体重",
  sleep: "睡眠",
  exercise: "运动",
  cycle: "周期",
  kcal: "热量",
  protein_g: "蛋白质",
  carbs_g: "碳水",
  fat_g: "脂肪",
  exercise_minutes: "每周运动",
  energy: "热量适配",
  macros: "营养适配",
  satiety: "饱腹感",
  adherence: "可坚持性",
  snacking: "加餐风险",
  breakfast: "早餐",
  lunch: "午餐",
  dinner: "晚餐",
};
const value = (v: number | null | undefined, digits = 0) =>
  v == null ? "—" : Number(v.toFixed(digits)).toString();
const dateLabel = (s: string) =>
  new Date(s + "T12:00:00").toLocaleDateString("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "long",
  });
const examples = [
  "中午吃了一大碗牛肉面，加了一个卤蛋",
  "今天早上体重 65 公斤",
  "昨晚睡了 7 小时，睡得不错",
  "今天跑步 30 分钟，有点累",
];

const durationText = (milliseconds: number) => {
  const seconds = Math.max(0, Math.round(milliseconds / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
};

function ProcessTrace({
  steps,
  durationMs,
}: {
  steps: ProcessStep[];
  durationMs: number;
}) {
  if (!steps?.length) return null;
  return (
    <details className="process-trace" open>
      <summary>
        <span>
          <Sparkles size={12} />
          AI 处理过程
        </span>
        <small>
          用时 {durationText(durationMs)} · {steps.length} 个阶段
          <ChevronDown size={12} />
        </small>
      </summary>
      <p className="process-note">展示可验证的执行摘要，不包含模型私有思维链。</p>
      <ol>
        {steps.map((step, index) => (
          <li key={`${step.title}-${index}`}>
            <span className="process-check"><Check size={10} /></span>
            <div>
              <b>
                {step.title}
                <span className={`process-source ${step.source}`}>
                  {step.source === "model" ? "调用大模型" : "本地代码"}
                </span>
              </b>
              <p>{step.detail}</p>
            </div>
          </li>
        ))}
      </ol>
    </details>
  );
}

function EvidenceList({
  refs,
  navigate,
}: {
  refs: Evidence[];
  navigate: (id: string) => void;
}) {
  if (!refs.length) return null;
  return (
    <details className="evidence">
      <summary>
        <BookOpen size={13} />
        依据 · {refs.length} 条记录
        <ChevronDown size={13} />
      </summary>
      <div>
        {refs.map((r) => (
          <button key={r.id} onClick={() => navigate(r.id)}>
            <span>
              {r.date} · {labels[r.kind]}
            </span>
            {r.text}
            <ArrowRight size={12} />
          </button>
        ))}
      </div>
    </details>
  );
}

function MealCard({ card }: { card: Card }) {
  if (card.type !== "meal") return null;
  return (
    <div className="meal-card">
      <div className="meal-title">
        <span>
          <Leaf size={15} />
          餐食记录
        </span>
        <small>{card.date}</small>
      </div>
      <div className="nutrition-grid">
        {(["kcal", "protein_g", "carbs_g", "fat_g"] as const).map((key) => (
          <div key={key}>
            <strong>
              {value(card.totals[key], 1)}
              <small>{key === "kcal" ? "kcal" : "g"}</small>
            </strong>
            <span>
              {key === "kcal" && card.totals.unknown_items > 0
                ? "已知热量"
                : labels[key]}
            </span>
          </div>
        ))}
      </div>
      <details>
        <summary>
          估算来源与判断细节
          <ChevronDown size={13} />
        </summary>
        <div className="meal-details">
          {card.items.map((item, i) => (
            <p key={i}>
              <b>{item.name}</b> ·{" "}
              {item.quantity == null
                ? "分量未知"
                : `${item.quantity}${item.unit}`}
              <br />
              <small>
                {item.nutrition_source} · {item.uncertainty}
              </small>
              {item.nutrition_status === "estimated" && (
                <small>以上为粗估值。</small>
              )}
            </p>
          ))}
          <p>
            评分维度覆盖 {card.coverage}%；
            {card.score == null
              ? "个人信息不足，暂不给综合分。"
              : `综合分 ${card.score}。`}
          </p>
          {card.notes.map((note, i) => (
            <small key={i}>{note}</small>
          ))}
        </div>
      </details>
    </div>
  );
}

function Sparkline({
  days,
  metric,
}: {
  days: Daily[];
  metric: "weight_kg" | "sleep_minutes";
}) {
  const records = days.flatMap((d, i) =>
    d[metric] == null ? [] : [{ x: i, y: d[metric]! }],
  );
  if (records.length < 2)
    return (
      <div className="chart-empty">
        <TrendingUp size={24} />
        <p>记录至少两个日期后显示趋势</p>
      </div>
    );
  const min = Math.min(...records.map((r) => r.y)),
    max = Math.max(...records.map((r) => r.y));
  const pts = records
    .map(
      (r) =>
        `${12 + (r.x / Math.max(days.length - 1, 1)) * 556},${130 - ((r.y - min) / Math.max(max - min, 0.5)) * 100}`,
    )
    .join(" ");
  return (
    <div className="chart">
      <svg
        viewBox="0 0 580 158"
        role="img"
        aria-label={
          metric === "weight_kg"
            ? "已记录体重趋势，缺失日期未当作零"
            : "已记录睡眠趋势，缺失日期未当作零"
        }
      >
        <defs>
          <linearGradient id={`fill-${metric}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#5f9476" stopOpacity=".25" />
            <stop offset="1" stopColor="#5f9476" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[35, 80, 130].map((y) => (
          <line
            key={y}
            x1="12"
            x2="568"
            y1={y}
            y2={y}
            stroke="#e9ede5"
            strokeDasharray="4 4"
          />
        ))}
        <polyline
          points={pts}
          fill="none"
          stroke="#4e8267"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {records.map((r) => (
          <circle
            key={r.x}
            cx={12 + (r.x / Math.max(days.length - 1, 1)) * 556}
            cy={130 - ((r.y - min) / Math.max(max - min, 0.5)) * 100}
            r="3"
            fill="#4e8267"
          >
            <title>
              {days[r.x].date}: {r.y}
              {metric === "weight_kg" ? " kg" : " 分钟"}
            </title>
          </circle>
        ))}
      </svg>
      <div className="chart-axis">
        <span>{days[0]?.date.slice(5)}</span>
        <span>
          已记录 {records.length} 天 ·{" "}
          {metric === "weight_kg"
            ? `${min}–${max} kg`
            : `${value(min / 60, 1)}–${value(max / 60, 1)} 小时`}
        </span>
        <span>{days.at(-1)?.date.slice(5)}</span>
      </div>
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState<Tab>("chat");
  const [health, setHealth] = useState<Health | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [defaultTargets, setDefaultTargets] = useState<DefaultTargets | null>(null);
  const [today, setToday] = useState<Daily | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [events, setEvents] = useState<Event[]>([]);
  const [days, setDays] = useState<Daily[]>([]);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [insights, setInsights] = useState<Insight[]>([]);
  const [reviews, setReviews] = useState<Review[]>([]);
  const [notices, setNotices] = useState<Notice[]>([]);
  const [pending, setPending] = useState<Workflow[]>([]);
  const [preferences, setPreferences] = useState<Preferences | null>(null);
  const [facts, setFacts] = useState<Fact[]>([]);
  const [months, setMonths] = useState<Month[]>([]);
  const [input, setInput] = useState("");
  const [localOnly, setLocalOnly] = useState(() => localStorage.getItem("health-local-only") === "true");
  const [busy, setBusy] = useState(false);
  const [processingStartedAt, setProcessingStartedAt] = useState<number | null>(null);
  const [processingElapsed, setProcessingElapsed] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [editProfile, setEditProfile] = useState(false);
  const [showNotices, setShowNotices] = useState(false);
  const [selectedWorkflow, setSelectedWorkflow] = useState<string | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const lastRequest = useRef<{ key: string; id: string } | null>(null);
  const [confirmingPlans, setConfirmingPlans] = useState<Set<string>>(() => new Set());
  const [cancellingPlans, setCancellingPlans] = useState<Set<string>>(() => new Set());
  const planInFlight = useRef(new Set<string>());
  const planCancellationInFlight = useRef(new Set<string>());
  const planPayloads = useRef(new Map<string, NonNullable<Workflow["retry_request"]>>());
  const planReadSequence = useRef(0);
  const appliedPlanRead = useRef(0);
  const latestPlans = useRef<Plan[]>([]);

  const applyPlanStatus = useCallback((pl: Plan[], w: Workflow[], sequence: number) => {
    // A slow earlier refresh must not restore a draft over a newer active plan.
    if (sequence < appliedPlanRead.current) return;
    appliedPlanRead.current = sequence;
    latestPlans.current = pl;
    setPlans(pl.filter((p) => p.status !== "purged"));
    setPending(w);
    setConfirmingPlans((previous) => {
      const next = new Set([...previous].filter((id) => planInFlight.current.has(id)));
      return next.size === previous.size ? previous : next;
    });
  }, []);

  const refreshPlanStatus = useCallback(async () => {
    const sequence = ++planReadSequence.current;
    const [pl, w] = await Promise.all([api<Plan[]>("/plans"), api<Workflow[]>("/workflows")]);
    applyPlanStatus(pl, w, sequence);
  }, [applyPlanStatus]);

  const refresh = useCallback(async () => {
    const sequence = ++planReadSequence.current;
    const [h, p, d, m, e, t, pl, i, r, n, w, pr, f, mo, dt] = await Promise.all([
      api<Health>("/health"),
      api<Profile | null>("/profile"),
      api<Daily>("/dashboard"),
      api<Message[]>("/messages"),
      api<Event[]>("/events"),
      api<Daily[]>("/trends"),
      api<Plan[]>("/plans"),
      api<Insight[]>("/insights"),
      api<Review[]>("/reviews"),
      api<Notice[]>("/notifications"),
      api<Workflow[]>("/workflows"),
      api<Preferences>("/preferences"),
      api<Fact[]>("/facts"),
      api<Month[]>("/monthly-summaries"),
      api<DefaultTargets>("/targets/default"),
    ]);
    setHealth(h);
    setProfile(p);
    setDefaultTargets(dt);
    setToday(d);
    setMessages(m);
    setEvents(e);
    setDays(t);
    applyPlanStatus(pl, w, sequence);
    setInsights(i);
    setReviews(r);
    setNotices(n);
    setPreferences(pr);
    setFacts(f);
    setMonths(mo);
    return { plans: pl, workflows: w };
  }, [applyPlanStatus]);

  useEffect(() => {
    refresh()
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [refresh]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy]);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 3500);
    return () => clearTimeout(t);
  }, [toast]);
  useEffect(() => {
    if (processingStartedAt == null) {
      setProcessingElapsed(0);
      return;
    }
    const update = () => setProcessingElapsed(Date.now() - processingStartedAt);
    update();
    const timer = setInterval(update, 1000);
    return () => clearInterval(timer);
  }, [processingStartedAt]);
  useEffect(() => {
    const t = setInterval(() => {
      if (!document.hidden)
        Promise.all([
          api<Notice[]>("/notifications"),
          refreshPlanStatus(),
        ])
          .then(([n]) => {
            setNotices(n);
          })
          .catch(() => {});
    }, 60000);
    return () => clearInterval(t);
  }, [refreshPlanStatus]);

  const hasPlanInProgress = confirmingPlans.size > 0 || pending.some(
    (w) => w.pending_input?.kind === "plan_confirmation" && w.status === "resuming",
  );
  useEffect(() => {
    if (!hasPlanInProgress) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        if (!document.hidden) await refreshPlanStatus();
      } catch {
        // Keep the current state on a failed refresh and try again next tick.
      } finally {
        if (!stopped) timer = setTimeout(poll, 2000);
      }
    };
    timer = setTimeout(poll, 2000);
    return () => { stopped = true; clearTimeout(timer); };
  }, [hasPlanInProgress, refreshPlanStatus]);

  const act = async (task: () => Promise<unknown>, success = "") => {
    setBusy(true);
    setError("");
    try {
      await task();
      await refresh();
      if (success) setToast(success);
    } catch (e) {
      setError(e instanceof Error ? e.message : "请求失败");
      // An error can arrive after the server accepted an answer or activated a
      // plan. Refresh recovery information instead of offering another answer.
      await refresh().catch(() => {});
    } finally {
      setBusy(false);
    }
  };
  const currentPending =
    selectedWorkflow === "new"
      ? undefined
      : (pending.find((p) => p.thread_id === selectedWorkflow) ??
        pending.find((p) => p.pending_input?.kind === "clarification"));
  const activePlan = plans.find((p) => p.status === "active");
  const navigateEvidence = (id: string) => {
    setSelectedEvent(id);
    setTab("history");
  };
  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!input.trim() || busy || currentPending?.retry_request) return;
    const text = input.trim(),
      workflow = currentPending;
    const processingMode = localOnly || health?.local_only || workflow?.processing_mode === "local_only"
      ? "local_only" : "model_allowed";
    const key = `${workflow?.thread_id ?? selectedEvent ?? ""}:${processingMode}:${text}`;
    if (lastRequest.current?.key !== key)
      lastRequest.current = { key, id: requestId() };
    setProcessingStartedAt(Date.now());
    try {
      await act(async () => {
        if (workflow?.pending_input?.kind === "clarification") {
          await api(`/workflows/${workflow.thread_id}/resume`, "POST", {
            request_id: lastRequest.current!.id,
            interrupt_id: workflow.pending_input.interrupt_id,
            text,
            processing_mode: processingMode,
          });
        } else {
          await api("/chat", "POST", {
            request_id: lastRequest.current!.id,
            text,
            conversation_id: "main",
            event_id: selectedEvent,
            processing_mode: processingMode,
          });
        }
        lastRequest.current = null;
        setInput("");
        setSelectedEvent(null);
        setSelectedWorkflow(null);
      });
    } finally {
      setProcessingStartedAt(null);
    }
  };
  const runPlanDecision = async (workflow: Workflow, payload: NonNullable<Workflow["retry_request"]>) => {
    const id = workflow.thread_id;
    if (planInFlight.current.has(id) || planCancellationInFlight.current.has(id)) return;
    planInFlight.current.add(id);
    setConfirmingPlans((previous) => new Set(previous).add(id));
    setError("");
    let failure: unknown;
    let snapshot: Awaited<ReturnType<typeof refresh>> | undefined;
    try {
      await api(`/workflows/${id}/resume`, "POST", payload);
    } catch (e) {
      failure = e;
    }
    try {
      snapshot = await refresh();
    } catch (e) {
      failure ??= e;
    } finally {
      planInFlight.current.delete(id);
      // If refreshing failed, keep the uncertain state until polling succeeds.
      if (snapshot) setConfirmingPlans((previous) => {
        const next = new Set(previous); next.delete(id); return next;
      });
    }
    const planId = workflow.pending_input?.plan_id ?? id;
    const activated = latestPlans.current.some((p) => p.id === planId && p.status === "active");
    if (failure && !activated) setError(
      `${snapshot ? "计划确认失败" : "暂时无法获取确认结果，请刷新状态"}：${failure instanceof Error ? failure.message : "请稍后重试"}`,
    );
  };
  const decidePlan = (workflow: Workflow, approved: boolean) => {
    const previous = planPayloads.current.get(workflow.thread_id);
    const interruptId = workflow.pending_input!.interrupt_id;
    const payload = previous?.approved === approved && previous.interrupt_id === interruptId
      ? previous : { request_id: requestId(), interrupt_id: interruptId, approved };
    planPayloads.current.set(workflow.thread_id, payload);
    return runPlanDecision(workflow, payload);
  };
  const cancelPlan = async (workflow: Workflow) => {
    const id = workflow.thread_id;
    if (planCancellationInFlight.current.has(id)) return;
    planCancellationInFlight.current.add(id);
    setCancellingPlans((previous) => new Set(previous).add(id));
    setError("");
    try {
      await api(`/workflows/${id}`, "DELETE");
      setToast("已取消待处理计划");
    } catch (e) {
      setError(e instanceof ApiError && e.status === 409
        && (planInFlight.current.has(id) || e.message.includes("计划正在确认"))
        ? "计划正在确认，暂时无法取消"
        : e instanceof Error ? e.message : "取消失败，请刷新状态");
    } finally {
      await refresh().catch(() => {});
      planCancellationInFlight.current.delete(id);
      setCancellingPlans((previous) => { const next = new Set(previous); next.delete(id); return next; });
    }
  };
  const retryWorkflow = (workflow: Workflow) => {
    if (workflow.pending_input?.kind === "plan_confirmation" && workflow.retry_request)
      return runPlanDecision(workflow, workflow.retry_request);
    return act(async () => {
      if (!workflow.retry_request) return;
      if ((localOnly || health?.local_only) && workflow.processing_mode !== "local_only"
        && workflow.retry_request.processing_mode !== "local_only") {
        throw new Error("上次补充使用模型模式。若要改为纯本地处理，请取消后重新提交。");
      }
      await api(`/workflows/${workflow.thread_id}/resume`, "POST", workflow.retry_request);
      lastRequest.current = null;
      setInput("");
      setSelectedWorkflow(null);
      setSelectedEvent(null);
    });
  };
  const clearPending = (workflow: Workflow) => {
    if (workflow.pending_input?.kind === "plan_confirmation") return cancelPlan(workflow);
    return act(
      async () => {
        await api(`/workflows/${workflow.thread_id}`, "DELETE");
        lastRequest.current = null;
        setInput("");
        setSelectedWorkflow("new");
      },
      "已取消待处理事项",
    );
  };
  const removeEvent = (e: Event) => {
    if (
      !confirm(
        `删除“${e.raw_text}”？\n为清理数据副本，聊天上下文、关联分析与待处理事项也会清空，其他健康记录保留。`,
      )
    )
      return;
    act(() => api(`/events/${e.id}`, "DELETE"), "记录与相关上下文已删除");
  };

  const nav = [
    { id: "chat", icon: MessageSquare, label: "记录与对话" },
    { id: "history", icon: BookOpen, label: "历史与趋势" },
    { id: "plans", icon: CalendarDays, label: "我的计划" },
    { id: "insights", icon: Sparkles, label: "发现与复盘" },
    { id: "settings", icon: Settings2, label: "个人设置" },
  ] as const;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setTab("chat");
          }}
        >
          <span className="brand-mark">
            <Leaf size={22} />
          </span>
          <div>
            <b>轻记</b>
            <small>让习惯，有迹可循</small>
          </div>
        </a>
        <div className="nav-caption">你的健康空间</div>
        <nav>
          {nav.map(({ id, icon: Icon, label }) => (
            <button
              className={tab === id ? "nav-item active" : "nav-item"}
              key={id}
              onClick={() => {
                setTab(id);
                setSelectedEvent(null);
              }}
            >
              <Icon size={18} />
              <span>{label}</span>
              {tab === id && <i />}
            </button>
          ))}
        </nav>
        <div className="sidebar-note">
          <Leaf size={19} />
          <p>
            每一条记录，
            <br />
            都在帮助它更了解你。
          </p>
          <span>记忆保留一年 · 更早保留月度摘要</span>
        </div>
        <button className="user-area" onClick={() => setEditProfile(true)}>
          <span className="avatar">我</span>
          <div>
            <b>我的个人档案</b>
            <small>
              {profile
                ? `${profile.height_cm} cm · ${profile.goal_weight_kg} kg 目标`
                : "完成建档，开始记录"}
            </small>
          </div>
          <Settings2 size={15} />
        </button>
      </aside>
      <main className="main">
        <header>
          <div className="breadcrumb">
            个人健康 Agent <span>/</span> {nav.find((n) => n.id === tab)?.label}
          </div>
          <div className="header-right">
            <span
              className={`mode-badge ${health?.mode === "model" ? "model" : ""}`}
            >
              <i />
              {localOnly || health?.local_only ? "纯本地模式" : health?.mode === "model" ? "模型已连接" : "演示模式"}
            </span>
            <button
              className="icon-button notification-toggle"
              aria-label="查看通知"
              onClick={() => setShowNotices(!showNotices)}
            >
              <Bell size={19} />
              {notices.some((n) => !n.read) && <i />}
            </button>
          </div>
        </header>
        {error && (
          <div className="error-banner" role="alert">
            <CircleHelp size={17} />
            <span>{error}</span>
            <button aria-label="关闭错误" onClick={() => setError("")}>
              <X size={15} />
            </button>
          </div>
        )}
        {loading ? (
          <div className="loading">
            <LoaderCircle className="spin" />
            <p>正在读取你的健康档案…</p>
          </div>
        ) : !health ? (
          <div className="empty">
            <h2>暂时连接不到本地服务</h2>
            <p>请确认后端已启动，再重新连接。</p>
            <button className="primary" onClick={() => act(refresh)}>
              重新连接
            </button>
          </div>
        ) : (
          <>
            <div className="page-heading">
              <div>
                <div className="eyebrow">
                  {health.today.replaceAll("-", ".")} ·{" "}
                  {tab === "chat"
                    ? "TODAY, A LITTLE BETTER"
                    : "PERSONAL HEALTH JOURNAL"}
                </div>
                <h1>
                  {tab === "chat"
                    ? "今天，从一句话开始。"
                    : tab === "history"
                      ? "回看记录，读懂变化。"
                      : tab === "plans"
                        ? "适合你的，才能坚持。"
                        : tab === "insights"
                          ? "让过去，帮助下一步。"
                          : "你的习惯，你来定义。"}
                </h1>
                <p>
                  {tab === "chat"
                    ? "记下吃了什么、如何运动、睡得怎样。每一次建议，都有迹可循。"
                    : tab === "history"
                      ? "事实与估算分开保存，随时查询和纠正。"
                      : tab === "plans"
                        ? "目标明确，安排具体。重要调整由你确认。"
                        : tab === "insights"
                          ? "从完整记录中寻找模式，同时保留不确定性。"
                          : "管理基础资料、长期偏好、评分权重与提醒。"}
                </p>
              </div>
              {tab === "chat" && (
                <span className="date-chip">
                  <CalendarDays size={15} />
                  {dateLabel(health.today)}
                </span>
              )}
            </div>
            {!profile && (
              <div className="onboard-banner">
                <span className="onboard-icon">
                  <Leaf size={22} />
                </span>
                <div>
                  <b>先认识你，再积累记忆</b>
                  <p>填写基础资料即可。记录和历史查询现在也能使用。</p>
                </div>
                <button
                  className="primary"
                  onClick={() => setEditProfile(true)}
                >
                  建立档案
                  <ArrowRight size={15} />
                </button>
              </div>
            )}
            {tab === "chat" && (
              <div className="chat-layout">
                <section className="panel chat-panel">
                  <div className="panel-heading">
                    <div>
                      <span className="agent-dot">
                        <Sparkles size={15} />
                      </span>
                      <b>你的减脂 Agent</b>
                      <span className="small-label">长期记忆已开启</span>
                    </div>
                    <small>LangGraph · 本地持久化</small>
                  </div>
                  <div className="chat-scroll">
                    <div className="assistant-welcome">
                      <span className="assistant-avatar">
                        <Leaf size={19} />
                      </span>
                      <div>
                        <b>记录可以很简单。</b>
                        <p>
                          像发消息一样告诉我。我会保存日期与内容，必要时只追问关键细节。你也可以随时说“不是两碗，是一碗”。
                        </p>
                        <div className="welcome-chips">
                          <span>
                            <Check size={12} />
                            跨天记忆
                          </span>
                          <span>
                            <Check size={12} />
                            自然语言纠错
                          </span>
                          <span>
                            <Check size={12} />
                            建议附带依据
                          </span>
                        </div>
                      </div>
                    </div>
                    {messages.map((m) => (
                      <div className={`message ${m.role}`} key={m.id}>
                        {m.role === "assistant" && (
                          <span className="assistant-avatar">
                            <Leaf size={17} />
                          </span>
                        )}
                        <div className="message-body">
                          <p className="message-text">{m.text}</p>
                          {m.cards.map((c, i) => (
                            <MealCard key={i} card={c} />
                          ))}
                          {m.role === "assistant" && (
                            <>
                              <ProcessTrace
                                steps={m.process_trace ?? []}
                                durationMs={m.duration_ms ?? 0}
                              />
                              <EvidenceList
                                refs={m.evidence_refs}
                                navigate={navigateEvidence}
                              />
                            </>
                          )}
                        </div>
                        {m.role === "user" && (
                          <span className="avatar">我</span>
                        )}
                      </div>
                    ))}
                    {!messages.length && (
                      <div className="quick-start">
                        <small>可以试着这样描述 · 点击填入</small>
                        <div>
                          {examples.map((text, i) => (
                            <button key={text} onClick={() => setInput(text)}>
                              <span>{["饮食", "体重", "睡眠", "运动"][i]}</span>
                              {text}
                              <Plus size={14} />
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                    {busy && processingStartedAt != null && (
                      <div className="thinking process-running">
                        <span className="assistant-avatar">
                          <Leaf size={17} />
                        </span>
                        <div className="thinking-progress">
                          <div className="running-heading">
                            <b>AI 处理过程</b>
                            <small>已用时 {durationText(processingElapsed)}</small>
                          </div>
                          <div className="running-step active">
                            <LoaderCircle size={13} className="spin" />
                            <span><b>{localOnly || health?.local_only || currentPending?.processing_mode === "local_only"
                              ? "本地规则处理" : health?.mode === "model" ? `调用 ${health.model_name}` : "本地演示解析"}</b>
                              <small>正在理解输入并检查处理范围…</small></span>
                          </div>
                          <div className="running-step queued">
                            <span className="queued-dot" />
                            <span><b>等待本地处理</b><small>随后校验字段并执行保存或查询</small></span>
                          </div>
                        </div>
                      </div>
                    )}
                    <div ref={bottom} />
                  </div>
                  <div className="composer-area">
                    {currentPending && (
                      <div className="pending-strip">
                        <CircleHelp size={15} />
                        <span>
                          {currentPending.retry_request
                            ? currentPending.status === "resuming"
                              ? `正在处理上次补充“${currentPending.retry_request.text ?? ""}”，请稍候。`
                              : `上次补充“${currentPending.retry_request.text ?? ""}”未完成。请重试原答案，或取消后重新填写。已保存的记录保留；若历史已有该条，请纠正它。`
                            : <>{currentPending.pending_input?.optional ? "可选补充：" : "正在补充："}
                              {currentPending.pending_input?.prompt ?? currentPending.pending_input?.question}</>}
                        </span>
                        {currentPending.retry_request && (
                          <button disabled={busy || currentPending.status === "resuming"}
                            onClick={() => retryWorkflow(currentPending)}>重试原答案</button>
                        )}
                        <button
                          disabled={busy}
                          onClick={() => {
                            setSelectedWorkflow("new");
                            setInput("");
                          }}
                        >
                          另开一条
                        </button>
                        <button
                          disabled={busy}
                          onClick={() => clearPending(currentPending)}
                        >
                          取消
                        </button>
                      </div>
                    )}
                    {!currentPending &&
                      pending.some(
                        (w) => w.pending_input?.kind === "clarification",
                      ) && (
                        <div className="pending-strip">
                          <Clock3 size={15} />
                          <span>还有可继续的对话，可随时补充。</span>
                          <button onClick={() => setSelectedWorkflow(null)}>
                            继续补充
                          </button>
                        </div>
                      )}
                    {selectedEvent && (
                      <div className="pending-strip">
                        <BookOpen size={15} />
                        <span>纠正选中的历史记录，请描述正确内容</span>
                        <button onClick={() => setSelectedEvent(null)}>
                          退出纠错
                        </button>
                      </div>
                    )}
                    {currentPending?.pending_input?.options && !currentPending.retry_request && (
                      <div className="pending-options">
                        {currentPending.pending_input.options.map((o) => (
                          <button
                            key={o.id}
                            disabled={busy}
                            onClick={() =>
                              act(() =>
                                api(
                                  `/workflows/${currentPending.thread_id}/resume`,
                                  "POST",
                                  {
                                    request_id: requestId(),
                                    interrupt_id:
                                      currentPending.pending_input!
                                        .interrupt_id,
                                    event_id: o.id,
                                    text: o.label,
                                    processing_mode: localOnly || health?.local_only || currentPending.processing_mode === "local_only"
                                      ? "local_only" : "model_allowed",
                                  },
                                ),
                              )
                            }
                          >
                            {o.label}
                          </button>
                        ))}
                      </div>
                    )}
                    <div className="model-data-notice">
                      <label>
                        <input type="checkbox" aria-label="纯本地模式" checked={localOnly || !!health?.local_only || currentPending?.processing_mode === "local_only"}
                          disabled={busy || health?.local_only || currentPending?.processing_mode === "local_only"}
                          onChange={(e) => {
                            setLocalOnly(e.target.checked);
                            localStorage.setItem("health-local-only", String(e.target.checked));
                          }} />
                        纯本地模式
                      </label>
                      {localOnly || health?.local_only || currentPending?.processing_mode === "local_only" ? (
                        <p>不向外部模型发送数据。支持常见记录和本地查询；缺失营养保持未知，复杂表达请明确日期和内容。</p>
                      ) : health?.mode === "model" ? (
                        <details>
                          <summary>将发送给 {health.model_provider ?? "已配置供应商"} · {health.model_name}</summary>
                          <p>先发送本次输入、当前时间和操作状态，不附带历史记录。纠错仅发送选中或明确指代的单条记录；补充仅发送当前草稿。建议按问题发送单餐、相关日期饮食、近 7 天饮食汇总或近 28 天体重等结构化数据，饮食建议可能参考已确认偏好与限制，分量建议还可能参考基础档案。营养估算只发送本次食物与分量，回复润色只发送本次回复要点。历史聊天原文不发送。</p>
                        </details>
                      ) : <p>演示模式使用本地规则，营养为固定示例值。开启纯本地模式后不使用示例营养。</p>}
                    </div>
                    <form className="composer" onSubmit={submit}>
                      <textarea
                        aria-label="描述饮食或身体状态"
                        disabled={busy || !!currentPending?.retry_request}
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        placeholder={
                          currentPending
                            ? currentPending.pending_input?.optional
                              ? "可以补充，也可以说不知道或先不记…"
                              : "补充一句话，继续记录…"
                            : "今天吃了什么？也可以记录体重、睡眠或运动…"
                        }
                        rows={2}
                        maxLength={4000}
                        onKeyDown={(e) => {
                          if (
                            e.key === "Enter" &&
                            !e.shiftKey &&
                            !e.nativeEvent.isComposing
                          ) {
                            e.preventDefault();
                            submit();
                          }
                        }}
                      />
                      <button
                        className="send-button"
                        disabled={busy || !input.trim() || !!currentPending?.retry_request}
                        aria-label="发送记录"
                      >
                        {busy ? (
                          <LoaderCircle size={18} className="spin" />
                        ) : (
                          <Send size={18} />
                        )}
                      </button>
                    </form>
                    <div className="composer-caption">
                      <span>Enter 发送 · Shift + Enter 换行</span>
                      <span>估算会保留来源与不确定性</span>
                    </div>
                  </div>
                </section>
                <aside className="today-aside">
                  {today && (
                    <>
                      <TodayCard
                        today={today}
                        openPlans={() => setTab("plans")}
                        complete={() =>
                          act(() =>
                            api(`/days/${today.date}/complete`, "PUT", {
                              complete: !today.diet_complete,
                            }),
                          )
                        }
                        busy={busy}
                      />
                      <div className="panel state-panel">
                        <div className="panel-heading">
                          <b>今日身体状态</b>
                          <Activity size={17} />
                        </div>
                        <div className="state-row">
                          <span>体重</span>
                          <b>
                            {value(today.weight_kg, 1)} <small>kg</small>
                          </b>
                        </div>
                        <div className="state-row">
                          <span>睡眠 · 按入睡日期</span>
                          <b>
                            {value(
                              today.sleep_minutes == null
                                ? null
                                : today.sleep_minutes / 60,
                              1,
                            )}{" "}
                            <small>h</small>
                          </b>
                        </div>
                        <div className="state-row">
                          <span>运动</span>
                          <b>
                            {today.exercise_minutes || "—"} <small>min</small>
                          </b>
                        </div>
                        <button
                          className="text-button"
                          onClick={() => setInput("今天早上体重 ")}
                        >
                          补记身体状态
                          <ArrowRight size={14} />
                        </button>
                      </div>
                    </>
                  )}
                  <div className="memory-note">
                    <BookOpen size={18} />
                    <div>
                      <b>它记得，是因为有记录</b>
                      <p>
                        每条原始记录保留一年。更早的数据转为月度摘要；没有数据时，会明确告诉你。
                      </p>
                    </div>
                  </div>
                </aside>
              </div>
            )}
            {tab === "history" && (
              <History
                events={events}
                days={days}
                months={months}
                selected={selectedEvent}
                busy={busy}
                filter={async (query) =>
                  setEvents(await api<Event[]>("/events?" + query))
                }
                correct={(e) => {
                  setSelectedEvent(e.id);
                  setSelectedWorkflow("new");
                  setInput(e.raw_text);
                  setTab("chat");
                }}
                remove={removeEvent}
              />
            )}
            {tab === "plans" && (
              <Plans
                active={activePlan}
                defaults={defaultTargets}
                openProfile={() => setEditProfile(true)}
                plans={plans}
                pending={pending.filter(
                  (w) => w.pending_input?.kind === "plan_confirmation",
                )}
                busy={busy}
                confirming={confirmingPlans}
                cancelling={cancellingPlans}
                refreshStatus={() => refreshPlanStatus().catch((e) => setError(e.message))}
                generate={(body) =>
                  act(() =>
                    api("/plans/propose", "POST", {
                      request_id: requestId(),
                      ...body,
                    }),
                  )
                }
                decide={decidePlan}
                retry={retryWorkflow}
                cancel={clearPending}
                navigate={navigateEvidence}
              />
            )}
            {tab === "insights" && (
              <div className="insights-page">
                <div className="section-toolbar">
                  <span>近 28 天的模式 · 缺失数据不算零</span>
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() =>
                      act(
                        () =>
                          api("/reviews", "POST", {
                            request_id: requestId(),
                            period: "week",
                          }),
                        "复盘已更新",
                      )
                    }
                  >
                    <Sparkles size={15} />
                    生成周复盘
                  </button>
                </div>
                {!insights.length ? (
                  <div className="panel empty-insights">
                    <span className="soft-icon">
                      <Sparkles size={27} />
                    </span>
                    <h2>规律需要一点时间。</h2>
                    <p>
                      目前没有足够数据支持的规律。持续记录睡眠与饮食，并在记录完整时标记当天完整。
                      <br />
                      系统需要两组各至少 3 个完整样本才会给出初步观察。
                    </p>
                    <div className="principles">
                      <span>附带样本量</span>
                      <span>说明干扰因素</span>
                      <span>不把相关性当因果</span>
                    </div>
                  </div>
                ) : (
                  insights.map((i) => (
                    <div className="panel insight-card" key={i.id}>
                      <div className="insight-top">
                        <span className="tag">{i.data.confidence}</span>
                        <small>{i.data.window.join(" — ")}</small>
                      </div>
                      <h2>{i.data.title}</h2>
                      <p>{i.data.description}</p>
                      <div className="insight-facts">
                        <span>{i.data.sample_size} 个样本</span>
                        <span>组别 {i.data.groups.join(" / ")}</span>
                        <span>窗口覆盖 {i.data.coverage}%</span>
                      </div>
                      <div className="limitation">
                        <CircleHelp size={15} />
                        {i.data.limitations}
                      </div>
                      <EvidenceList
                        refs={i.data.evidence_refs}
                        navigate={navigateEvidence}
                      />
                      <div className="insight-feedback">
                        <span>这个观察符合你的实际情况吗？</span>
                        <button
                          disabled={busy || i.feedback === "accepted"}
                          onClick={() =>
                            act(
                              () =>
                                api(`/insights/${i.id}`, "PATCH", {
                                  feedback: "accepted",
                                }),
                              "已记录你的反馈",
                            )
                          }
                        >
                          {i.feedback === "accepted" ? "已认可" : "认可"}
                        </button>
                        <button
                          disabled={busy}
                          onClick={() =>
                            act(
                              () =>
                                api(`/insights/${i.id}`, "PATCH", {
                                  feedback: "rejected",
                                }),
                              "此结论不再用于建议",
                            )
                          }
                        >
                          不认可
                        </button>
                      </div>
                    </div>
                  ))
                )}
                <h2 className="section-title">每日与每周复盘</h2>
                {!reviews.length ? (
                  <div className="panel simple-empty">
                    还没有复盘。点击“生成周复盘”，或等待你设置的总结时间。
                  </div>
                ) : (
                  reviews.map((r) => (
                    <div className="panel review-card" key={r.id}>
                      <div className="review-heading">
                        <span>
                          <CalendarDays size={17} />
                          {r.data.period === "week" ? "周复盘" : "每日总结"}
                        </span>
                        <small>
                          {r.data.start} — {r.data.end}
                        </small>
                      </div>
                      <p>{r.data.summary}</p>
                      <div className="review-metrics">
                        <div>
                          <b>{r.data.record_count}</b>
                          <span>记录数</span>
                        </div>
                        <div>
                          <b>{r.data.complete_days}</b>
                          <span>完整饮食日</span>
                        </div>
                        <div>
                          <b>{value(r.data.avg_kcal)}</b>
                          <span>平均 kcal</span>
                        </div>
                        <div>
                          <b>{r.data.exercise_minutes}</b>
                          <span>运动 min</span>
                        </div>
                      </div>
                      <EvidenceList
                        refs={r.data.evidence_refs}
                        navigate={navigateEvidence}
                      />
                    </div>
                  ))
                )}
              </div>
            )}
            {tab === "settings" && preferences && (
              <Settings
                preferences={preferences}
                facts={facts}
                profile={profile}
                busy={busy}
                editProfile={() => setEditProfile(true)}
                save={(body) =>
                  act(() => api("/preferences", "PUT", body), "设置已保存")
                }
                removeFact={(f) => {
                  if (
                    confirm(
                      "删除这条长期信息也会清空相关聊天上下文和待处理事项，确定继续？",
                    )
                  )
                    act(
                      () => api(`/facts/${f.id}`, "DELETE"),
                      "长期信息已删除",
                    );
                }}
                health={{ ...health, local_only: localOnly || health.local_only }}
              />
            )}
          </>
        )}
        <footer>
          轻记 · 个人减脂 Agent <span>记录 → 理解 → 调整</span>
        </footer>
      </main>
      {showNotices && (
        <div className="notice-drawer">
          <div className="drawer-title">
            <h2>消息中心</h2>
            <button
              className="icon-button"
              aria-label="关闭通知"
              onClick={() => setShowNotices(false)}
            >
              <X size={20} />
            </button>
          </div>
          <p className="muted">站内提醒 · 网页关闭后，下次打开可查看</p>
          {notices.length ? (
            notices.map((n) => (
              <button
                key={n.id}
                className={`notice ${n.read ? "read" : ""}`}
                onClick={() =>
                  act(() => api(`/notifications/${n.id}`, "PATCH"))
                }
              >
                <b>
                  {!n.read && <i />}
                  {n.title}
                </b>
                <p>{n.body}</p>
                <small>
                  {new Date(n.created_at).toLocaleString("zh-CN", {
                    timeZone: "Asia/Shanghai",
                  })}
                </small>
              </button>
            ))
          ) : (
            <div className="empty">
              <Bell size={28} />
              <p>
                还没有通知。
                <br />
                在设置中安排你的提醒时间。
              </p>
            </div>
          )}
        </div>
      )}
      {editProfile && (
        <ProfileModal
          profile={profile}
          busy={busy}
          close={() => setEditProfile(false)}
          save={(body) =>
            act(async () => {
              await api("/profile", "PUT", body);
              setEditProfile(false);
            }, "个人档案已保存")
          }
        />
      )}
      {toast && (
        <div className="toast" role="status">
          <Check size={17} />
          {toast}
        </div>
      )}
    </div>
  );
}

function TodayCard({
  today,
  openPlans,
  complete,
  busy,
}: {
  today: Daily;
  openPlans: () => void;
  complete: () => void;
  busy: boolean;
}) {
  const percent = Math.min(
    100,
    Math.max(0, ((today.totals.kcal ?? 0) / (today.targets?.kcal || 1)) * 100),
  );
  return (
    <div className="panel today-card">
      <div className="panel-heading">
        <b>今日饮食</b>
        <span className="tiny-tag">{today.meal_count} 餐记录</span>
      </div>
      <div
        className="energy-ring"
        style={
          { "--progress": `${today.targets ? percent : 0}%` } as CSSProperties
        }
      >
        <div>
          <small>已记录摄入</small>
          <strong>{value(today.totals.kcal)}</strong>
          <span>kcal · 估算</span>
        </div>
      </div>
      {today.targets ? (
        <div className="energy-budget">
          <span>目标 {today.targets.kcal} kcal</span>
          <b>剩余 {value(today.remaining?.kcal)} kcal</b>
        </div>
      ) : (
        <button className="budget-empty" onClick={openPlans}>
          先确认个人目标
          <ArrowRight size={13} />
        </button>
      )}
      <div className="macro-list">
        {(["protein_g", "carbs_g", "fat_g"] as const).map((k, i) => (
          <div key={k}>
            <div>
              <span>
                <i className={`macro-dot color-${i}`} />
                {labels[k]}
              </span>
              <b>
                {value(today.totals[k], 1)}
                <small> / {today.targets?.[k] ?? "—"} g</small>
              </b>
            </div>
            <div className="progress-track">
              <i
                className={`color-${i}`}
                style={{
                  width: `${Math.min(100, ((today.totals[k] ?? 0) / (today.targets?.[k] || 9999)) * 100)}%`,
                }}
              />
            </div>
          </div>
        ))}
      </div>
      <button
        className={`complete-toggle ${today.diet_complete ? "checked" : ""}`}
        disabled={busy}
        onClick={complete}
      >
        <span>{today.diet_complete && <Check size={12} />}</span>
        {today.diet_complete ? "今日饮食已记录完整" : "确认今日饮食已记录完整"}
      </button>
      <small className="coverage-note">
        {today.totals.unknown_items
          ? `${today.totals.unknown_items} 项营养未知；汇总仅包含已知部分。`
          : "可能有漏记；剩余额度不代表必须吃完。"}
      </small>
    </div>
  );
}

function History({
  events,
  days,
  months,
  selected,
  filter,
  correct,
  remove,
  busy,
}: {
  events: Event[];
  days: Daily[];
  months: Month[];
  selected: string | null;
  filter: (query: URLSearchParams) => Promise<void>;
  correct: (e: Event) => void;
  remove: (e: Event) => void;
  busy: boolean;
}) {
  const [kind, setKind] = useState(""),
    [start, setStart] = useState(""),
    [end, setEnd] = useState(""),
    [search, setSearch] = useState(""),
    [filterError, setFilterError] = useState("");
  return (
    <div className="history-page">
      <div className="trend-grid">
        <div className="panel trend-panel">
          <div className="panel-heading">
            <b>体重趋势</b>
            <span>最近 28 天 · kg</span>
          </div>
          <Sparkline days={days} metric="weight_kg" />
        </div>
        <div className="panel trend-panel">
          <div className="panel-heading">
            <b>睡眠趋势</b>
            <span>按入睡日期 · h</span>
          </div>
          <Sparkline days={days} metric="sleep_minutes" />
        </div>
      </div>
      <section className="panel records-panel">
        <div className="panel-heading">
          <b>原始记录</b>
          <small>{events.length} 条 · 最近一年</small>
        </div>
        <form
          className="filter-form"
          onSubmit={(e) => {
            e.preventDefault();
            setFilterError("");
            const query = new URLSearchParams();
            if (kind) query.set("kind", kind);
            if (start) query.set("start", start);
            if (end) query.set("end", end);
            if (search) query.set("search", search);
            filter(query).catch((e) => setFilterError(e.message));
          }}
        >
          <select
            aria-label="记录类型"
            value={kind}
            onChange={(e) => setKind(e.target.value)}
          >
            <option value="">全部类型</option>
            {["meal", "weight", "sleep", "exercise", "cycle"].map((k) => (
              <option key={k} value={k}>
                {labels[k]}
              </option>
            ))}
          </select>
          <input
            type="date"
            aria-label="开始日期"
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
          <span>—</span>
          <input
            type="date"
            aria-label="结束日期"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
          />
          <input
            aria-label="搜索记录"
            placeholder="搜索食物或描述"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <button className="secondary" disabled={busy}>
            查询
          </button>
        </form>
        {filterError && <p className="form-error">{filterError}</p>}
        <div className="record-list">
          {events.length ? (
            events.map((e) => (
              <div
                key={e.id}
                id={`event-${e.id}`}
                className={`record-row ${selected === e.id ? "highlighted" : ""}`}
              >
                <span className={`record-icon kind-${e.kind}`}>
                  {e.kind === "meal" ? (
                    <Leaf size={17} />
                  ) : e.kind === "exercise" ? (
                    <Activity size={17} />
                  ) : e.kind === "sleep" ? (
                    <Clock3 size={17} />
                  ) : (
                    <CalendarDays size={17} />
                  )}
                </span>
                <div className="record-content">
                  <div>
                    <span>{labels[e.kind]}</span>
                    <small>
                      {e.day} · v{e.version}
                    </small>
                  </div>
                  <p>{e.raw_text}</p>
                  {e.kind === "meal" && (
                    <small>
                      {e.data.items
                        .map(
                          (i) => `${i.name} ${i.quantity ?? "约 1"}${i.unit}`,
                        )
                        .join(" · ")}
                    </small>
                  )}
                </div>
                <div className="record-actions">
                  <button disabled={busy} onClick={() => correct(e)}>
                    纠正
                  </button>
                  <button
                    className="icon-button"
                    disabled={busy}
                    aria-label={`删除${e.raw_text}`}
                    onClick={() => remove(e)}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>
            ))
          ) : (
            <div className="simple-empty">
              没有符合条件的记录。你可以用自然语言开始记录，也可以补记过去的日期。
            </div>
          )}
        </div>
      </section>
      {months.length > 0 && (
        <div className="panel monthly-panel">
          <h2>月度摘要</h2>
          <p>这些月份的原始记录已归档，不能还原逐日内容。</p>
          {months.map((m) => (
            <div className="state-row" key={m.month}>
              <b>{m.month}</b>
              <span>
                {m.data.record_count} 条记录 · {m.data.meal_count} 餐 · 平均体重{" "}
                {value(m.data.avg_weight_kg, 1)} kg
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Plans({
  active,
  defaults,
  openProfile,
  plans,
  pending,
  busy,
  confirming,
  cancelling,
  refreshStatus,
  generate,
  decide,
  retry,
  cancel,
  navigate,
}: {
  active?: Plan;
  defaults: DefaultTargets | null;
  openProfile: () => void;
  plans: Plan[];
  pending: Workflow[];
  busy: boolean;
  confirming: Set<string>;
  cancelling: Set<string>;
  refreshStatus: () => void;
  generate: (body: { targets?: Targets; note?: string; target_source?: "manual" | "system_default"; suggestion_fingerprint?: string }) => void;
  decide: (w: Workflow, approved: boolean) => void;
  retry: (w: Workflow) => void;
  cancel: (w: Workflow) => void;
  navigate: (id: string) => void;
}) {
  const [fields, setFields] = useState<Record<string, string>>({
    kcal: "",
    protein_g: "",
    carbs_g: "",
    fat_g: "",
    exercise_minutes: "",
  });
  const [dirty, setDirty] = useState(false);
  const [usingDefaults, setUsingDefaults] = useState(false);
  const appliedActive = useRef<string | undefined>(undefined);
  const fill = (targets: Targets) => Object.fromEntries(
    Object.entries(targets).map(([k, v]) => [k, String(v)]),
  );
  useEffect(() => {
    if (active) {
      if (appliedActive.current !== active.id) {
        setFields(fill(active.data.targets));
        setDirty(false);
        setUsingDefaults(false);
      }
      appliedActive.current = active.id;
      return;
    }
    appliedActive.current = undefined;
    if (!dirty) {
      if (defaults?.status === "ready" && defaults.targets) {
        setFields(fill(defaults.targets));
        setUsingDefaults(true);
      } else {
        setFields({ kcal: "", protein_g: "", carbs_g: "", fat_g: "", exercise_minutes: "" });
        setUsingDefaults(false);
      }
    }
  }, [active?.id, defaults?.fingerprint, defaults?.status, dirty]);
  return (
    <div className="plans-page">
      {pending.map((w) => {
        const stored = plans.find((p) => p.id === (w.pending_input?.plan_id ?? w.thread_id));
        if (stored && stored.status !== "pending") {
          // Business state wins even if workflow result finalization is delayed.
          if (stored.status === "active" && w.status === "resume_failed" && w.retry_request)
            return <div className="plan-confirm" key={w.thread_id}>
              <p>计划已生效，上次请求未完成。可以更新确认结果，无需再次采用计划。</p>
              <button className="secondary" disabled={busy || confirming.has(w.thread_id)}
                onClick={() => retry(w)}>{confirming.has(w.thread_id) ? "更新中…" : "更新确认结果"}</button>
            </div>;
          return null;
        }
        const isConfirming = confirming.has(w.thread_id) || w.status === "resuming";
        const isCancelling = cancelling.has(w.thread_id);
        return (
        <div className="plan-confirm" key={w.thread_id}>
          <div>
            <span className="tag">{isConfirming ? "确认中…" : w.status === "resume_failed" ? "确认失败" : "待你确认"}</span>
            <h2>新的计划已准备好</h2>
            <p>{w.pending_input?.question}</p>
            {w.status === "resume_failed" && <p className="small-note" role="status">
              计划确认失败。{w.retry_request
                ? `上次选择“${w.retry_request.approved ? "采用计划" : "保留原计划"}”未完成，请重试上次确认，或取消待处理计划。`
                : "请根据当前草案重新确认或取消。"}
            </p>}
            {w.pending_input?.target_source === "system_default" && (
              <p className="small-note">系统初始估算，可修改，不是医疗处方。{w.pending_input.target_warnings?.join(" ")}</p>
            )}
            <div className="target-chips">
              {Object.entries(w.pending_input?.targets ?? {}).map(([k, v]) => (
                <span key={k}>
                  {labels[k]} <b>{v}</b>
                  {k === "kcal"
                    ? " kcal"
                    : k === "exercise_minutes"
                      ? " min/周"
                      : " g"}
                </span>
              ))}
            </div>
            {Object.keys(w.pending_input?.changes ?? {}).length > 0 && (
              <small>
                相对当前计划：
                {Object.entries(w.pending_input!.changes!)
                  .map(([k, v]) => `${labels[k]} ${v > 0 ? "+" : ""}${v}%`)
                  .join(" · ")}
              </small>
            )}
          </div>
          <div className="confirm-actions">
            {w.retry_request ? <>
              <button className="primary" disabled={busy || isConfirming || isCancelling}
                onClick={() => retry(w)}>
                {isConfirming && <LoaderCircle size={15} className="spin" />}
                {isConfirming ? "确认中…" : "重试上次确认"}
              </button>
            </> : <>
            <button
              className="primary"
              disabled={busy || isConfirming || isCancelling}
              onClick={() => decide(w, true)}
            >
              {isConfirming ? <LoaderCircle size={15} className="spin" /> : <Check size={15} />}
              {isConfirming ? "确认中…" : "采用计划"}
            </button>
            <button
              className="secondary"
              disabled={busy || isConfirming || isCancelling}
              onClick={() => decide(w, false)}
            >
              保留原计划
            </button>
            </>}
            <button className="secondary" disabled={busy || isCancelling}
              onClick={() => cancel(w)}>{isCancelling ? "取消中…" : "取消待处理计划"}</button>
            {isConfirming && <button className="text-button" onClick={refreshStatus}>刷新状态</button>}
          </div>
        </div>
      ); })}
      <div className="plan-grid">
        <section className="panel target-panel">
          <div className="panel-heading">
            <b>个人目标</b>
            <Target size={18} />
          </div>
          <p className="muted">
            系统根据基础档案自动填入默认目标，你可以直接使用或自行修改。已有生效目标不会被重新估算覆盖。
          </p>
          <div className="target-estimate">
            <span className="tiny-tag">{usingDefaults ? "系统默认 · 可修改" : dirty ? "手动编辑" : active ? "当前生效目标" : "等待基础资料"}</span>
            {defaults?.status === "ready" ? (
              <>
                <button type="button" className="text-button" disabled={busy} onClick={() => {
                  if (!defaults.targets) return;
                  setFields(fill(defaults.targets));
                  setDirty(false);
                  setUsingDefaults(true);
                }}>恢复系统默认</button>
                <details>
                  <summary>查看默认估算依据</summary>
                  <p className="muted">{defaults.reason}。{defaults.basis}</p>
                  {defaults.warnings.map((warning) => <p className="small-note" key={warning}>{warning}</p>)}
                  <div className="target-sources">{defaults.sources.map((source) => (
                    <a key={source.url} href={source.url} target="_blank" rel="noreferrer">{source.title}</a>
                  ))}</div>
                </details>
              </>
            ) : (
              <>
                <p className="muted">{defaults?.reason ?? "正在读取默认目标…"}你也可以填写专业建议或自定目标。</p>
                <button type="button" className="text-button" disabled={busy} onClick={openProfile}>
                  {defaults?.status === "needs_profile" ? "建立或补全基础档案" : "检查基础档案"}
                </button>
              </>
            )}
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              generate({
                targets: active && !dirty && !usingDefaults ? undefined : Object.fromEntries(
                  Object.entries(fields).map(([k, v]) => [k, Number(v)]),
                ) as Targets,
                target_source: usingDefaults ? "system_default" : "manual",
                suggestion_fingerprint: usingDefaults ? defaults?.fingerprint : undefined,
              });
            }}
          >
            <div className="target-fields">
              {Object.keys(fields).map((k) => (
                <label key={k}>
                  {labels[k]}
                  <div>
                    <input
                      type="number"
                      aria-label={labels[k]}
                      min={k === "kcal" ? 1 : 0}
                      max={
                        k === "kcal"
                          ? 10000
                          : k === "carbs_g"
                            ? 1500
                            : k === "exercise_minutes"
                              ? 2000
                              : 1000
                      }
                      step="1"
                      required
                      value={fields[k]}
                      onChange={(e) => {
                        setFields({ ...fields, [k]: e.target.value });
                        setDirty(true);
                        setUsingDefaults(false);
                      }}
                    />
                    <span>
                      {k === "kcal"
                        ? "kcal/天"
                        : k === "exercise_minutes"
                          ? "min/周"
                          : "g/天"}
                    </span>
                  </div>
                </label>
              ))}
            </div>
            <button className="primary" disabled={busy}>
              生成计划草案
              <ArrowRight size={15} />
            </button>
          </form>
          <p className="small-note">
            首份计划或超过 5%
            的变化需确认；累计变化也参与判断。阈值为原型规则，可后续调整。
          </p>
        </section>
        <section className="panel current-plan">
          <div className="panel-heading">
            <b>当前生效计划</b>
            <span className="tiny-tag" role={active ? "status" : undefined}>{active ? "计划已生效" : "未设置"}</span>
          </div>
          {active ? (
            <>
              <div className="active-target">
                <strong>
                  {active.data.targets.kcal}
                  <small>kcal / 天</small>
                </strong>
                <span>
                  蛋白质 {active.data.targets.protein_g}g · 碳水{" "}
                  {active.data.targets.carbs_g}g · 脂肪{" "}
                  {active.data.targets.fat_g}g
                </span>
              </div>
              <h3>下一餐建议</h3>
              <p>{active.data.next_meal}</p>
              <h3>明日重点</h3>
              <p>{active.data.tomorrow_focus}</p>
              <EvidenceList
                refs={active.data.evidence_refs}
                navigate={navigate}
              />
              <button
                className="text-button"
                disabled={busy}
                onClick={() => generate({})}
              >
                根据最新记录刷新安排
                <ArrowRight size={15} />
              </button>
            </>
          ) : (
            <div className="empty">
              <span className="soft-icon">
                <Target size={28} />
              </span>
              <p>
                确认首份草案后，
                <br />
                这里会显示正在执行的目标。
              </p>
            </div>
          )}
        </section>
      </div>
      {active && (
        <section className="panel week-plan">
          <div className="panel-heading">
            <b>接下来的一周</b>
            <small>饮食重点与运动安排</small>
          </div>
          {active.data.schedule.map((d, i) => (
            <div className="schedule-row" key={d.date}>
              <div className="schedule-day">
                <span>DAY {i + 1}</span>
                <b>{d.date.slice(5)}</b>
              </div>
              <div>
                <h3>
                  <Leaf size={14} />
                  饮食
                </h3>
                <p>{d.food}</p>
              </div>
              <div>
                <h3>
                  <Activity size={14} />
                  运动
                </h3>
                <p>{d.exercise}</p>
              </div>
            </div>
          ))}
        </section>
      )}
      <details className="plan-history">
        <summary>
          计划版本记录 · {plans.length} 份<ChevronDown size={14} />
        </summary>
        {plans.map((p) => (
          <div className="state-row" key={p.id}>
            <span>
              {new Date(p.created_at).toLocaleString("zh-CN")} ·{" "}
              {p.data.targets.kcal} kcal
            </span>
            <b>
              {{
                active: "生效",
                pending: "待确认",
                rejected: "已拒绝",
                stale: "已过期",
                superseded: "已替换",
              }[p.status] ?? p.status}
            </b>
          </div>
        ))}
      </details>
    </div>
  );
}

function Settings({
  preferences,
  facts,
  profile,
  busy,
  editProfile,
  save,
  removeFact,
  health,
}: {
  preferences: Preferences;
  facts: Fact[];
  profile: Profile | null;
  busy: boolean;
  editProfile: () => void;
  save: (p: Preferences) => void;
  removeFact: (f: Fact) => void;
  health: Health;
}) {
  const [draft, setDraft] = useState(preferences);
  useEffect(() => setDraft(preferences), [preferences]);
  const [suggestion, setSuggestion] = useState<{
    weights: Record<string, number>;
    reason: string;
    limitations: string;
  } | null>(null);
  useEffect(() => {
    api<typeof suggestion>("/preferences/suggestion")
      .then(setSuggestion)
      .catch(() => {});
  }, [preferences]);
  return (
    <div className="settings-page">
      <div className="settings-grid">
        <section className="panel settings-panel">
          <div className="panel-heading">
            <b>基础资料</b>
            <button className="text-button" onClick={editProfile}>
              修改
              <ArrowRight size={13} />
            </button>
          </div>
          {profile ? (
            <>
              <div className="profile-metrics">
                <div>
                  <b>{profile.age}</b>
                  <span>年龄</span>
                </div>
                <div>
                  <b>{profile.height_cm}</b>
                  <span>身高 cm</span>
                </div>
                <div>
                  <b>{profile.weight_kg}</b>
                  <span>建档体重 kg</span>
                </div>
              </div>
              <div className="state-row">
                <span>目标体重</span>
                <b>{profile.goal_weight_kg} kg</b>
              </div>
              <div className="state-row">
                <span>期望日期</span>
                <b>{profile.goal_date}</b>
              </div>
              <small className="muted">建档体重与每日称重分开保存。</small>
            </>
          ) : (
            <p>尚未填写基础资料。</p>
          )}
        </section>
        <section className="panel settings-panel">
          <div className="panel-heading">
            <b>长期信息</b>
            <BookOpen size={17} />
          </div>
          <p className="muted">
            在聊天中告诉我饮食偏好、忌口或已知限制。这里只保存你明确表达的信息。
          </p>
          {facts.length ? (
            facts.map((f) => (
              <div className="fact-row" key={f.id}>
                <span>{f.content}</span>
                <button
                  className="icon-button"
                  disabled={busy}
                  aria-label="删除长期信息"
                  onClick={() => removeFact(f)}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))
          ) : (
            <div className="simple-empty small">
              还没有长期偏好。试着说“我不喜欢燕麦”。
            </div>
          )}
        </section>
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          save(draft);
        }}
      >
        <div className="settings-grid">
          <section className="panel settings-panel">
            <div className="panel-heading">
              <b>综合判断权重</b>
              <Target size={17} />
            </div>
            <p className="muted">
              权重总和必须为 100。没有个人数据的维度标为未知，不会自动给高分。
            </p>
            {Object.entries(draft.weights).map(([k, v]) => (
              <label className="weight-setting" key={k}>
                <span>{labels[k]}</span>
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={v}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      weights: {
                        ...draft.weights,
                        [k]: Number(e.target.value),
                      },
                    })
                  }
                />
                <input
                  aria-label={`${labels[k]}权重`}
                  type="number"
                  min="0"
                  max="100"
                  value={v}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      weights: {
                        ...draft.weights,
                        [k]: Number(e.target.value),
                      },
                    })
                  }
                />
                <span>%</span>
              </label>
            ))}
            <div className="weight-total">
              当前总和{" "}
              <b>{Object.values(draft.weights).reduce((a, b) => a + b, 0)}%</b>
            </div>
            {suggestion && (
              <div className="weight-suggestion">
                <b>根据历史的权重建议</b>
                <p>{suggestion.reason}</p>
                <small>{suggestion.limitations}</small>
                <button
                  type="button"
                  className="text-button"
                  onClick={() =>
                    setDraft({ ...draft, weights: suggestion.weights })
                  }
                >
                  填入建议（保存后生效）
                  <ArrowRight size={13} />
                </button>
              </div>
            )}
          </section>
          <section className="panel settings-panel">
            <div className="panel-heading">
              <b>提醒与复盘</b>
              <Bell size={17} />
            </div>
            <label className="check-label">
              <input
                type="checkbox"
                checked={draft.notifications_enabled}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    notifications_enabled: e.target.checked,
                  })
                }
              />
              开启站内提醒
            </label>
            {Object.entries(draft.meal_times).map(([k, v]) => (
              <label className="time-setting" key={k}>
                <span>{labels[k]}时间</span>
                <input
                  type="time"
                  required
                  value={v}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      meal_times: { ...draft.meal_times, [k]: e.target.value },
                    })
                  }
                />
              </label>
            ))}
            <label className="time-setting">
              <span>身体状态提醒</span>
              <input
                type="time"
                required
                value={draft.checkin_time}
                onChange={(e) =>
                  setDraft({ ...draft, checkin_time: e.target.value })
                }
              />
            </label>
            <label className="time-setting">
              <span>每日总结</span>
              <input
                type="time"
                required
                value={draft.summary_time}
                onChange={(e) =>
                  setDraft({ ...draft, summary_time: e.target.value })
                }
              />
            </label>
            <label className="time-setting">
              <span>每周复盘</span>
              <select
                value={draft.weekly_day}
                onChange={(e) =>
                  setDraft({ ...draft, weekly_day: Number(e.target.value) })
                }
              >
                {["周一", "周二", "周三", "周四", "周五", "周六", "周日"].map(
                  (d, i) => (
                    <option key={i} value={i}>
                      {d}
                    </option>
                  ),
                )}
              </select>
            </label>
            <p className="small-note">
              时区
              Asia/Shanghai。后台服务运行时生成；关闭网页后不会实时推送到设备。
            </p>
          </section>
        </div>
        <button className="primary" disabled={busy}>
          保存设置
          <Check size={15} />
        </button>
      </form>
      <section className="panel data-panel">
        <div>
          <h2>数据属于你</h2>
          <p>原始记录保留一年，更早的数据按月归档。导出内容不含模型密钥。</p>
        </div>
        <a className="secondary" href="/api/data/export" download>
          <ArrowDownToLine size={16} />
          导出数据
        </a>
      </section>
      <p className="small-note">
        当前运行：
        {health.local_only
          ? "纯本地模式（不向外部模型发送数据）"
          : health.mode === "demo"
          ? "演示模式（规则解析与固定示例营养）"
          : `真实模型 · ${health.model_name}`}{" "}
        · 模型配置由后端管理。
      </p>
    </div>
  );
}

function ProfileModal({
  profile,
  save,
  close,
  busy,
}: {
  profile: Profile | null;
  save: (p: Profile) => void;
  close: () => void;
  busy: boolean;
}) {
  const [form, setForm] = useState<Record<string, string>>({
    age: profile ? String(profile.age) : "",
    sex: profile?.sex ?? "",
    height_cm: profile ? String(profile.height_cm) : "",
    weight_kg: profile ? String(profile.weight_kg) : "",
    goal_weight_kg: profile ? String(profile.goal_weight_kg) : "",
    goal_date: profile?.goal_date ?? "",
    activity: profile?.activity ?? "light",
    nutrition_context: profile?.nutrition_context ?? "unspecified",
  });
  const field = (
    k: string,
    label: string,
    unit: string,
    min: number,
    max: number,
  ) => (
    <label>
      {label}
      <div className="input-with-unit">
        <input
          type="number"
          required
          min={min}
          max={max}
          step={k === "age" ? "1" : ".1"}
          value={form[k]}
          onChange={(e) => setForm({ ...form, [k]: e.target.value })}
        />
        <span>{unit}</span>
      </div>
    </label>
  );
  return (
    <div className="modal-backdrop" onClick={close}>
      <section
        className="profile-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="profile-title"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          className="modal-close icon-button"
          aria-label="关闭档案"
          onClick={close}
        >
          <X size={20} />
        </button>
        <span className="soft-icon">
          <Leaf size={28} />
        </span>
        <h2 id="profile-title">建立你的健康档案</h2>
        <p>先了解基础情况，后续再通过日常记录逐渐了解你。</p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save({
              age: Number(form.age),
              sex: form.sex as Profile["sex"],
              height_cm: Number(form.height_cm),
              weight_kg: Number(form.weight_kg),
              goal_weight_kg: Number(form.goal_weight_kg),
              goal_date: form.goal_date,
              activity: form.activity as Profile["activity"],
              nutrition_context: form.nutrition_context as Profile["nutrition_context"],
            });
          }}
        >
          <div className="profile-fields">
            {field("age", "年龄", "岁", 1, 120)}
            <label>
              性别
              <select
                required
                value={form.sex}
                onChange={(e) => setForm({ ...form, sex: e.target.value })}
              >
                <option value="" disabled>请选择，用于公式系数</option>
                <option value="female">女性</option>
                <option value="male">男性</option>
                <option value="other">其他</option>
              </select>
            </label>
            {field("height_cm", "身高", "cm", 80, 250)}
            {field("weight_kg", "当前体重", "kg", 20, 400)}
            {field("goal_weight_kg", "目标体重", "kg", 20, 400)}
            <label>
              期望日期
              <input
                type="date"
                required
                value={form.goal_date}
                onChange={(e) =>
                  setForm({ ...form, goal_date: e.target.value })
                }
              />
            </label>
          </div>
          <label>
            日常活动水平
            <select
              value={form.activity}
              onChange={(e) => setForm({ ...form, activity: e.target.value })}
            >
              <option value="sedentary">久坐为主</option>
              <option value="light">轻度活动</option>
              <option value="moderate">中等活动</option>
              <option value="active">活动较多</option>
            </select>
          </label>
          <label>
            营养估算适用情况
            <select value={form.nutrition_context} onChange={(e) => setForm({ ...form, nutrition_context: e.target.value })}>
              <option value="unspecified">尚未说明（采用前需核对适用范围）</option>
              <option value="general">一般情况，无特殊营养需求</option>
              <option value="pregnant">孕期</option>
              <option value="breastfeeding">哺乳期</option>
              <option value="clinical">有疾病或需专业营养指导</option>
            </select>
          </label>
          <button className="primary" disabled={busy}>
            保存档案，开始记录
            <ArrowRight size={16} />
          </button>
        </form>
        <small>基础档案和每天的实际记录分开保存，随时可以修改。</small>
      </section>
    </div>
  );
}
