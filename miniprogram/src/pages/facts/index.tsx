import { useState } from "react";
import { Button, Textarea, View } from "@tarojs/components";
import type { Fact } from "../../types";
import { api } from "../../api";
import {
  mutate,
  privacyGeneration,
  useAction,
  usePrivateReset,
  useResource,
} from "../../state";
import {
  confirm,
  DateField,
  Field,
  Fold,
  go,
  Page,
  Select,
} from "../../components/common";

const factTypes = {
  food_preference: "饮食偏好",
  restriction: "限制",
  allergy: "过敏",
  health_condition: "健康状况",
  professional_advice: "转述专业建议",
  legacy_text: "历史文本",
};
const values: Record<string, Record<string, string>> = {
  food_preference: {
    dislike: "不喜欢",
    like: "喜欢",
    avoid: "不吃",
    accept: "可以接受",
  },
  restriction: { avoid: "避免", limit: "限制", required: "需要遵守" },
  allergy: {
    allergic: "自述过敏",
    denied: "否认（需核对）",
    uncertain: "不确定",
  },
  health_condition: {
    present: "自述目前存在",
    past: "自述既往存在",
    resolved: "自述已解除（需核对）",
    uncertain: "不确定",
  },
  professional_advice: {
    advice: "转述建议",
    avoid: "建议避免",
    limit: "建议限制",
    required: "建议遵守",
  },
};
const life: Record<string, string> = {
  active: "有效",
  needs_review: "待核对",
  retracted: "已撤回",
  expired: "已到期",
  superseded: "已被更新",
  scheduled: "待生效",
};
const emptyForm = {
  content: "",
  type: "food_preference" as Fact["type"],
  subject: "",
  value: "dislike",
  valid_from: "",
  valid_until: "",
  subject_kind: "food",
  time_scope: "unknown",
  assertion: "affirmed",
  restriction_reason: "unknown",
  reported_severity: "unknown",
  professional: "",
  institution: "",
  advised_on: "",
  document: "",
};

export default function Facts() {
  const resource = useResource(() =>
    api<Fact[]>("/facts?include_inactive=true"),
  );
  const action = useAction();
  const [editing, setEditing] = useState<Fact | "new" | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [history, setHistory] = useState<{
    id: string;
    rows: (Fact & { change_reason: string })[];
  } | null>(null);
  usePrivateReset(() => {
    setEditing(null);
    setForm(emptyForm);
    setHistory(null);
  });
  function open(fact: Fact | "new") {
    setEditing(fact);
    setForm(
      fact === "new"
        ? emptyForm
        : {
            ...emptyForm,
            content: fact.content,
            type: fact.type,
            subject: fact.subject,
            value: fact.value,
            valid_from: fact.valid_from || "",
            valid_until: fact.valid_until || "",
            subject_kind: fact.subject_kind || "other",
            time_scope: fact.time_scope || "unknown",
            assertion: fact.assertion || "affirmed",
            restriction_reason: fact.restriction_reason || "unknown",
            reported_severity: fact.reported_severity || "unknown",
            professional: fact.source_details?.professional || "",
            institution: fact.source_details?.institution || "",
            advised_on: fact.source_details?.advised_on || "",
            document: fact.source_details?.document || "",
          },
    );
  }
  async function save() {
    await action.run(async () => {
      if (!editing) return;
      const { professional, institution, advised_on, document, ...fields } =
        form;
      await mutate(
        editing === "new" ? "/facts" : `/facts/${editing.id}`,
        editing === "new" ? "POST" : "PUT",
        {
          ...fields,
          valid_from: form.valid_from || null,
          valid_until: form.valid_until || null,
          time_scope: form.valid_until ? "bounded" : form.time_scope,
          source_details:
            form.type === "professional_advice"
              ? {
                  professional,
                  institution,
                  advised_on: advised_on || null,
                  document,
                }
              : {},
          ...(editing === "new" ? {} : { expected_version: editing.version }),
        },
      );
      setEditing(null);
    });
  }
  return (
    <Page
      title="长期信息"
      error={action.error || resource.error}
      loading={resource.loading}
      refresh={resource.refresh}
    >
      <View className="muted">
        保存明确表达的信息。自述限制和转述建议未经独立核验；撤回保留历史，隐私删除清理相关副本。
      </View>
      <Button disabled={action.busy} onClick={() => open("new")}>
        新增长期信息
      </Button>
      {editing && (
        <View className="card">
          <View className="subtitle">
            {editing === "new" ? "新增" : "修改"}长期信息
          </View>
          <Textarea
            value={form.content}
            maxlength={500}
            placeholder="完整原文或描述"
            onInput={(event) =>
              setForm({ ...form, content: event.detail.value })
            }
          />
          <Select
            label="类型"
            value={form.type}
            options={factTypes}
            onChange={(type) =>
              setForm({
                ...form,
                type: type as Fact["type"],
                value: Object.keys(values[type] || {})[0] || "",
              })
            }
          />
          <Field
            label="对象"
            max={100}
            value={form.subject}
            onChange={(subject) => setForm({ ...form, subject })}
          />
          {values[form.type] ? (
            <Select
              label="内容或状态"
              value={form.value}
              options={values[form.type]}
              onChange={(value) => setForm({ ...form, value })}
            />
          ) : (
            <Field
              label="内容"
              max={200}
              value={form.value}
              onChange={(value) => setForm({ ...form, value })}
            />
          )}
          <Select
            label="对象类别"
            value={form.subject_kind}
            options={{
              food: "食物",
              condition: "健康状况",
              nutrient: "营养素",
              activity: "活动",
              other: "其他",
            }}
            onChange={(subject_kind) => setForm({ ...form, subject_kind })}
          />
          <Select
            label="期限"
            value={form.time_scope}
            options={{
              unknown: "未说明",
              persistent: "长期",
              bounded: "明确期限",
              temporary_unknown: "临时，截止待补",
            }}
            onChange={(time_scope) => setForm({ ...form, time_scope })}
          />
          <DateField
            label="开始日期"
            value={form.valid_from}
            onChange={(valid_from) => setForm({ ...form, valid_from })}
          />
          <DateField
            label="到期日期"
            value={form.valid_until}
            onChange={(valid_until) => setForm({ ...form, valid_until })}
          />
          <Select
            label="陈述状态"
            value={form.assertion}
            options={{
              affirmed: "肯定自述",
              denied: "否认",
              uncertain: "不确定",
              hypothetical: "假设",
            }}
            onChange={(assertion) => setForm({ ...form, assertion })}
          />
          <Select
            label="限制原因"
            value={form.restriction_reason}
            options={{
              unknown: "未说明",
              preference: "个人偏好",
              allergy: "过敏",
              health_condition: "健康状况",
              professional_advice: "专业建议",
              other: "其他",
            }}
            onChange={(restriction_reason) =>
              setForm({ ...form, restriction_reason })
            }
          />
          <Select
            label="自述严重程度"
            value={form.reported_severity}
            options={{
              unknown: "未说明",
              mild: "轻",
              moderate: "中",
              severe: "重",
            }}
            onChange={(reported_severity) =>
              setForm({ ...form, reported_severity })
            }
          />
          {form.type === "professional_advice" && (
            <>
              <Field
                label="专业人员"
                max={100}
                value={form.professional}
                onChange={(professional) => setForm({ ...form, professional })}
              />
              <Field
                label="机构"
                max={150}
                value={form.institution}
                onChange={(institution) => setForm({ ...form, institution })}
              />
              <DateField
                label="建议日期"
                value={form.advised_on}
                onChange={(advised_on) => setForm({ ...form, advised_on })}
              />
              <Field
                label="文档描述"
                max={200}
                value={form.document}
                onChange={(document) => setForm({ ...form, document })}
              />
            </>
          )}
          <Button
            className="primary"
            loading={action.busy}
            disabled={action.busy}
            onClick={save}
          >
            保存长期信息
          </Button>
          <Button disabled={action.busy} onClick={() => setEditing(null)}>
            取消编辑
          </Button>
        </View>
      )}
      {resource.data?.map((fact) => (
        <View className="card" key={fact.id}>
          <View>{fact.content}</View>
          <View className="tag">
            {factTypes[fact.type]} ·{" "}
            {fact.confirmation === "conflict"
              ? "存在冲突，待选择"
              : fact.confirmation === "candidate"
                ? "待确认"
                : life[fact.lifecycle] || fact.lifecycle}{" "}
            · v{fact.version}
          </View>
          <View className="muted">
            来源：
            {fact.source_kind === "user_reported_professional"
              ? "用户转述专业建议"
              : fact.source_kind === "unconfirmed_extraction"
                ? "待核对原文提取"
                : fact.source_kind === "legacy"
                  ? "历史记录"
                  : "用户明确陈述"}
          </View>
          <View className="muted">
            {fact.valid_from || "未说明开始"} —{" "}
            {fact.valid_until || "未说明截止"} ·{" "}
            {
              (
                {
                  soft: "普通偏好",
                  avoid: "需避免或遵守",
                  clinical_review: "需专业评估",
                  unknown: "含义待核对",
                } as Record<string, string>
              )[fact.enforcement || "unknown"]
            }
          </View>
          {fact.review_reason && (
            <View className="warning">{fact.review_reason}</View>
          )}
          {fact.temporarily_covered && (
            <View className="warning">
              当前被临时偏好覆盖，到期或撤回后恢复。
            </View>
          )}
          {!!fact.overrides?.length && (
            <View className="muted">
              到期后恢复：
              {fact.overrides
                .map(
                  (id) =>
                    resource.data?.find((row) => row.id === id)?.content ||
                    "原偏好",
                )
                .join("；")}
            </View>
          )}
          {fact.source_details && (
            <View className="muted">
              {Object.values(fact.source_details).filter(Boolean).join(" · ")}
            </View>
          )}
          {fact.confirmation === "conflict" && (
            <View className="warning">
              请核对同一对象的原文和适用期：
              {resource.data
                ?.filter(
                  (row) =>
                    row.id !== fact.id &&
                    row.confirmation === "conflict" &&
                    row.type === fact.type &&
                    row.subject_kind === fact.subject_kind &&
                    (row.subject_key || row.subject) ===
                      (fact.subject_key || fact.subject),
                )
                .map(
                  (row) =>
                    `${row.content}（${row.valid_from || "未说明"}至${row.valid_until || "未说明"}）`,
                )
                .join("；")}
            </View>
          )}
          <View className="row">
            <Button
              size="mini"
              disabled={action.busy}
              onClick={() => open(fact)}
            >
              修改
            </Button>
            {["candidate", "conflict"].includes(fact.confirmation) && (
              <Button
                size="mini"
                disabled={
                  action.busy ||
                  fact.time_scope === "temporary_unknown" ||
                  (!!fact.assertion && fact.assertion !== "affirmed") ||
                  (fact.confirmation === "conflict" &&
                    !!fact.enforcement &&
                    fact.enforcement !== "soft")
                }
                onClick={() =>
                  action.run(async () => {
                    if (!(await confirm("采用这条信息", fact.content))) return;
                    const peers =
                      resource.data?.filter(
                        (row) =>
                          row.id !== fact.id &&
                          row.type === fact.type &&
                          row.subject_kind === fact.subject_kind &&
                          (row.subject_key || row.subject) ===
                            (fact.subject_key || fact.subject) &&
                          ["active", "needs_review", "scheduled"].includes(
                            row.lifecycle,
                          ) &&
                          row.confirmation !== "candidate" &&
                          (!row.valid_from ||
                            !fact.valid_until ||
                            row.valid_from <= fact.valid_until) &&
                          (!fact.valid_from ||
                            !row.valid_until ||
                            fact.valid_from <= row.valid_until),
                      ) || [];
                    await mutate(`/facts/${fact.id}`, "PATCH", {
                      action: "confirm",
                      expected_version: fact.version,
                      ...(fact.schema_version === 2
                        ? {
                            resolution: fact.valid_until
                              ? "temporary"
                              : "permanent",
                            peer_versions: Object.fromEntries(
                              peers.map((row) => [row.id, row.version]),
                            ),
                          }
                        : {}),
                    });
                  })
                }
              >
                {fact.valid_until ? "仅在期限内采用" : "确认采用"}
              </Button>
            )}
            {["active", "needs_review", "scheduled"].includes(
              fact.lifecycle,
            ) && (
              <Button
                size="mini"
                disabled={action.busy}
                onClick={() =>
                  action.run(() =>
                    mutate(`/facts/${fact.id}`, "PATCH", {
                      action: "retract",
                      expected_version: fact.version,
                    }),
                  )
                }
              >
                撤回
              </Button>
            )}
            <Button
              size="mini"
              onClick={() =>
                go(`privacy?type=fact&id=${encodeURIComponent(fact.id)}`)
              }
            >
              隐私删除
            </Button>
          </View>
          <Fold title="变更历史">
            <Button
              disabled={action.busy}
              onClick={() =>
                action.run(async () => {
                  const epoch = privacyGeneration();
                  const rows = await api<(Fact & { change_reason: string })[]>(
                    `/facts/${fact.id}/revisions`,
                  );
                  if (epoch === privacyGeneration())
                    setHistory({ id: fact.id, rows });
                })
              }
            >
              读取历史
            </Button>
            {history?.id === fact.id &&
              history.rows.map((row, index) => (
                <View className="field" key={index}>
                  v{row.version} · {row.content}
                  <View className="muted">
                    {row.change_reason} · {life[row.lifecycle] || row.lifecycle}
                  </View>
                </View>
              ))}
          </Fold>
        </View>
      ))}
      {resource.data?.length === 0 && (
        <View className="card">
          暂无长期信息，也可以在记录页说“我不喜欢燕麦”。
        </View>
      )}
    </Page>
  );
}
