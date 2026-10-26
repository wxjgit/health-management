import { useEffect, useState } from "react";
import { api } from "./api";
import type { DerivedMemory, Fact, Workflow } from "./types";

const reasons: Record<string, string> = {
  source_changed: "依据的数据已变化，请重新生成后使用。",
  algorithm_changed: "计算方法已更新，这份结果保留原版本，需重新核对。",
  window_changed: "观察窗口已变化，请生成最新复盘。",
  legacy_version_unknown: "历史结果缺少方法版本，请结合原始记录核对。",
  analysis_refreshed: "本轮分析未产生可用的新结论，请继续积累完整记录。",
  plan_window_ended: "这份安排的日期已结束，请刷新安排；已确认目标仍保留。",
};

export function DerivedNote({ memory }: { memory?: DerivedMemory }) {
  if (!memory) return null;
  return (
    <div className="memory-note">
      {memory.status !== "valid" && (
        <p role="status" className="small-note">
          {memory.status === "needs_review" ? "需要复核：" : "结果需更新："}
          {reasons[memory.invalid_reason ?? ""] ?? "请重新核对依据。"}
        </p>
      )}
      <details>
        <summary>生成依据与版本</summary>
        {memory.window && <p>适用窗口：{memory.window.join(" — ")}</p>}
        <p>方法版本：{memory.algorithm_version ?? "历史版本未知"}</p>
        {memory.generated_at && (
          <p>
            生成时间：{new Date(memory.generated_at).toLocaleString("zh-CN")}
          </p>
        )}
        {memory.recomputable === false && (
          <p>原始明细已归档，保留原统计版本，无法重新计算。</p>
        )}
      </details>
    </div>
  );
}

type Revision = {
  version: number;
  raw_text: string;
  reason: string;
  created_at: string;
  changes?: { field: string; before: unknown; after: unknown }[];
};

function changeLabel(field: string) {
  const labels: Record<string, string> = {
    value: "数值",
    occurred_at: "发生时间",
    items: "食物与分量",
    quality: "状态",
    intensity: "强度",
    name: "食物",
    quantity: "分量",
    unit: "单位",
    size: "大小",
    cooking: "做法",
    kcal: "热量",
    protein_g: "蛋白质",
    carbs_g: "碳水",
    fat_g: "脂肪",
    meal_slot: "餐次",
    exercise_type: "运动类型",
  };
  const item = /^items\[(\d+)\](?:\.(.+))?$/.exec(field);
  return item
    ? `第 ${Number(item[1]) + 1} 项食物${item[2] ? ` · ${labels[item[2]] ?? item[2]}` : ""}`
    : (labels[field] ?? field);
}

export function RevisionHistory({
  eventId,
  version,
}: {
  eventId: string;
  version: number;
}) {
  const [rows, setRows] = useState<Revision[] | null>(null);
  const [error, setError] = useState("");
  const [opened, setOpened] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let current = true;
    setRows(null);
    setError("");
    if (opened)
      api<Revision[]>(`/events/${eventId}/revisions`).then(
        (data) => {
          if (current) setRows(data);
        },
        (e: Error) => {
          if (current) setError(e.message);
        },
      );
    return () => {
      current = false;
    };
  }, [eventId, version, opened, attempt]);
  return (
    <details
      className="revision-history"
      onToggle={(e) => setOpened(e.currentTarget.open)}
    >
      <summary>修订历史（{version} 个版本）</summary>
      {error && (
        <p role="alert">
          {error}{" "}
          <button
            type="button"
            onClick={() => setAttempt((value) => value + 1)}
          >
            重试
          </button>
        </p>
      )}
      {!rows && !error && <p>正在读取修订记录…</p>}
      {rows?.map((row) => (
        <div className="revision-entry" key={row.version}>
          <b>
            v{row.version} · {row.reason}
          </b>
          <small>{new Date(row.created_at).toLocaleString("zh-CN")}</small>
          <p>{row.raw_text}</p>
          {row.changes?.map((change) => (
            <p className="muted" key={change.field}>
              {changeLabel(change.field)}：
              {JSON.stringify(change.before) ?? "未知"} →{" "}
              {JSON.stringify(change.after) ?? "未知"}
            </p>
          ))}
        </div>
      ))}
    </details>
  );
}

export function ExpiredContexts({
  refreshKey,
  restart,
}: {
  refreshKey: number;
  restart: () => void;
}) {
  const [rows, setRows] = useState<Workflow[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let current = true;
    api<Workflow[]>("/workflows/expired").then(
      (data) => {
        if (current) {
          setRows(data);
          setError("");
        }
      },
      (e: Error) => {
        if (current) setError(e.message);
      },
    );
    return () => {
      current = false;
    };
  }, [refreshKey]);
  if (error) return <p className="small-note">过期事项读取失败：{error}</p>;
  if (!rows.length) return null;
  return (
    <details className="expired-contexts">
      <summary>最近有 {rows.length} 个已过期事项</summary>
      {rows.map((row) => (
        <p key={row.thread_id}>
          {row.reply}
          {!!row.saved_record_ids?.length && (
            <span> 已保留 {row.saved_record_ids.length} 条记录。</span>
          )}
        </p>
      ))}
      <button className="text-button" type="button" onClick={restart}>
        开始新的记录
      </button>
    </details>
  );
}

const factTypes: Record<Fact["type"], string> = {
  food_preference: "饮食偏好",
  restriction: "饮食限制",
  allergy: "自述过敏",
  professional_advice: "转述专业建议",
  legacy_text: "其他明确陈述",
};
const life: Record<string, string> = {
  active: "有效",
  needs_review: "到期待核对",
  expired: "已到期",
  retracted: "已撤回",
  superseded: "已被更新",
  scheduled: "待生效",
};
type FactForm = {
  content: string;
  type: Fact["type"];
  subject: string;
  value: string;
  valid_from: string;
  valid_until: string;
};
const emptyForm: FactForm = {
  content: "",
  type: "food_preference",
  subject: "",
  value: "",
  valid_from: "",
  valid_until: "",
};

export function FactManager({
  facts,
  busy,
  mutate,
  remove,
}: {
  facts: Fact[];
  busy: boolean;
  mutate: (path: string, method: string, body: unknown) => Promise<boolean>;
  remove: (fact: Fact) => void;
}) {
  const [rows, setRows] = useState(facts);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<Fact | "new" | null>(null);
  const [form, setForm] = useState<FactForm>(emptyForm);
  const [history, setHistory] = useState<{
    id: string;
    rows: (Fact & { change_reason: string })[];
  } | null>(null);
  useEffect(() => {
    let current = true;
    setRows(facts);
    setHistory(null);
    api<Fact[]>("/facts?include_inactive=true").then(
      (data) => {
        if (current) {
          setRows(data);
          setError("");
        }
      },
      (e: Error) => {
        if (current) setError(e.message);
      },
    );
    return () => {
      current = false;
    };
  }, [facts]);
  const open = (fact: Fact | "new") => {
    setEditing(fact);
    setForm(
      fact === "new"
        ? emptyForm
        : {
            content: fact.content,
            type: fact.type ?? "legacy_text",
            subject: fact.subject ?? "",
            value: fact.value ?? "",
            valid_from: fact.valid_from ?? "",
            valid_until: fact.valid_until ?? "",
          },
    );
  };
  return (
    <div className="fact-manager">
      <p className="muted">
        保存你明确表达的信息。自述限制与转述建议未经独立核验；撤回会保留历史，删除会清理相关副本。
      </p>
      {error && <p role="alert">{error}</p>}
      <button
        type="button"
        className="text-button"
        disabled={busy}
        onClick={() => open("new")}
      >
        新增长期信息
      </button>
      {editing && (
        <form
          className="fact-editor"
          onSubmit={async (e) => {
            e.preventDefault();
            const body = {
              ...form,
              valid_from: form.valid_from || null,
              valid_until: form.valid_until || null,
              ...(editing !== "new"
                ? { expected_version: editing.version }
                : {}),
            };
            const ok = await mutate(
              editing === "new" ? "/facts" : `/facts/${editing.id}`,
              editing === "new" ? "POST" : "PUT",
              body,
            );
            if (ok) setEditing(null);
          }}
        >
          <label>
            长期信息内容
            <textarea
              aria-label="长期信息内容"
              required
              maxLength={500}
              value={form.content}
              onChange={(e) => setForm({ ...form, content: e.target.value })}
            />
          </label>
          <label>
            信息类型
            <select
              aria-label="事实类型"
              value={form.type}
              onChange={(e) =>
                setForm({ ...form, type: e.target.value as Fact["type"] })
              }
            >
              {Object.entries(factTypes).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          {form.type !== "legacy_text" && (
            <>
              <label>
                食物或事项
                <input
                  aria-label="事实对象"
                  required
                  maxLength={100}
                  value={form.subject}
                  placeholder="例如：燕麦"
                  onChange={(e) =>
                    setForm({ ...form, subject: e.target.value })
                  }
                />
              </label>
              <label>
                具体偏好或限制
                {form.type === "food_preference" ? (
                  <select
                    aria-label="事实取值"
                    required
                    value={form.value}
                    onChange={(e) =>
                      setForm({ ...form, value: e.target.value })
                    }
                  >
                    <option value="">请选择</option>
                    <option value="dislike">不喜欢</option>
                    <option value="like">喜欢</option>
                    <option value="avoid">不吃</option>
                    <option value="accept">可以接受</option>
                    {form.value &&
                      !["dislike", "like", "avoid", "accept"].includes(
                        form.value,
                      ) && <option value={form.value}>{form.value}</option>}
                  </select>
                ) : (
                  <input
                    aria-label="事实取值"
                    required
                    maxLength={200}
                    value={form.value}
                    placeholder="例如：需要避免"
                    onChange={(e) =>
                      setForm({ ...form, value: e.target.value })
                    }
                  />
                )}
              </label>
            </>
          )}
          <label>
            生效日期
            <input
              aria-label="事实生效日期"
              type="date"
              value={form.valid_from}
              onChange={(e) => setForm({ ...form, valid_from: e.target.value })}
            />
          </label>
          <label>
            到期日期
            <input
              aria-label="事实到期日期"
              type="date"
              min={form.valid_from}
              value={form.valid_until}
              onChange={(e) =>
                setForm({ ...form, valid_until: e.target.value })
              }
            />
          </label>
          <div>
            <button type="submit" className="primary" disabled={busy}>
              保存长期信息
            </button>
            <button
              type="button"
              className="text-button"
              disabled={busy}
              onClick={() => setEditing(null)}
            >
              取消编辑
            </button>
          </div>
        </form>
      )}
      {!rows.length && (
        <div className="simple-empty small">
          还没有长期偏好。试着说“我不喜欢燕麦”。
        </div>
      )}
      {rows.map((fact) => (
        <div className="fact-entry" key={fact.id}>
          <p>{fact.content}</p>
          <small>
            {factTypes[fact.type] ?? "历史文本"} ·{" "}
            {fact.confirmation === "conflict"
              ? "存在冲突，待选择"
              : fact.confirmation === "candidate"
                ? "待确认"
                : (life[fact.lifecycle] ?? "有效")}{" "}
            · v{fact.version ?? 1}
          </small>
          <small>
            来源：
            {fact.source_kind === "user_reported_professional"
              ? "用户转述专业建议"
              : fact.source_kind === "legacy"
                ? "历史记录"
                : fact.source_kind === "unconfirmed_extraction"
                  ? "待用户核对的原文提取"
                  : "用户明确陈述"}
            {fact.valid_from && ` · 自 ${fact.valid_from}`}
            {fact.valid_until && ` · 至 ${fact.valid_until}`}
          </small>
          <div className="fact-actions">
            <button type="button" disabled={busy} onClick={() => open(fact)}>
              修改
            </button>
            {["conflict", "candidate"].includes(fact.confirmation) && (
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void mutate(`/facts/${fact.id}`, "PATCH", {
                    action: "confirm",
                    expected_version: fact.version,
                  })
                }
              >
                {fact.confirmation === "candidate" ? "确认这条" : "采用这条"}
              </button>
            )}
            {["active", "needs_review", "scheduled"].includes(
              fact.lifecycle,
            ) && (
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void mutate(`/facts/${fact.id}`, "PATCH", {
                    action: "retract",
                    expected_version: fact.version,
                  })
                }
              >
                撤回
              </button>
            )}
            <button
              type="button"
              disabled={busy}
              onClick={async () => {
                try {
                  setHistory({
                    id: fact.id,
                    rows: await api(`/facts/${fact.id}/revisions`),
                  });
                } catch (e) {
                  setError(e instanceof Error ? e.message : "历史读取失败");
                }
              }}
            >
              变更记录
            </button>
            <button
              type="button"
              disabled={busy}
              aria-label="删除长期信息"
              onClick={() => remove(fact)}
            >
              删除
            </button>
          </div>
          {history?.id === fact.id && (
            <div className="fact-history">
              {history.rows.length ? (
                history.rows.map((r) => (
                  <p key={r.version}>
                    v{r.version} · {r.content} · {r.change_reason}
                  </p>
                ))
              ) : (
                <p>历史文本未记录逐次变更；下一次修改起保留版本。</p>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
