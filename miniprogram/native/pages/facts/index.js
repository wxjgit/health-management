const { api } = require("../../native-runtime/api");
const state = require("../../native-runtime/state");
const { registerPage } = require("../../native-runtime/page");
const { confirmAction, go, fieldRows } = require("../../native-runtime/ui");
const types = {
  food_preference: "饮食偏好",
  restriction: "限制",
  allergy: "过敏",
  health_condition: "健康状况",
  professional_advice: "转述专业建议",
  legacy_text: "历史文本",
};
const values = {
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
const life = {
  active: "有效",
  needs_review: "待核对",
  retracted: "已撤回",
  expired: "已到期",
  superseded: "已被更新",
  scheduled: "待生效",
};
const empty = {
  content: "",
  type: "food_preference",
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
const options = (o) =>
  Object.entries(o).map(([value, label]) => ({ value, label }));
const schema = {
  type: { label: "类型", type: "select", options: options(types) },
  subject: { label: "对象" },
  subject_kind: {
    label: "对象类别",
    type: "select",
    options: options({
      food: "食物",
      condition: "健康状况",
      nutrient: "营养素",
      activity: "活动",
      other: "其他",
    }),
  },
  time_scope: {
    label: "期限",
    type: "select",
    options: options({
      unknown: "未说明",
      persistent: "长期",
      bounded: "明确期限",
      temporary_unknown: "临时，截止待补",
    }),
  },
  valid_from: { label: "开始日期", type: "date" },
  valid_until: { label: "到期日期", type: "date" },
  assertion: {
    label: "这条信息是否确定",
    type: "select",
    options: options({
      affirmed: "肯定自述",
      denied: "否认",
      uncertain: "不确定",
      hypothetical: "假设",
    }),
  },
  restriction_reason: {
    label: "限制原因",
    type: "select",
    options: options({
      unknown: "未说明",
      preference: "个人偏好",
      allergy: "过敏",
      health_condition: "健康状况",
      professional_advice: "专业建议",
      other: "其他",
    }),
  },
  reported_severity: {
    label: "自述严重程度",
    type: "select",
    options: options({
      unknown: "未说明",
      mild: "轻",
      moderate: "中",
      severe: "重",
    }),
  },
};
function fields(form) {
  const rows = fieldRows(form, schema);
  rows.splice(2, 0, {
    key: "value",
    label: "具体内容",
    value: form.value,
    type: values[form.type] ? "select" : "text",
    options: options(values[form.type] || {}),
  });
  if (form.type === "professional_advice")
    rows.push(
      ...fieldRows(form, {
        professional: "专业人员",
        institution: "机构",
        advised_on: { label: "建议日期", type: "date" },
        document: "文档描述",
      }),
    );
  return rows;
}
function canConfirm(f) {
  return (
    ["candidate", "conflict"].includes(f.confirmation) &&
    f.time_scope !== "temporary_unknown" &&
    (!f.assertion || f.assertion === "affirmed") &&
    (f.confirmation !== "conflict" ||
      !f.enforcement ||
      f.enforcement === "soft")
  );
}
function peers(f, rows) {
  return rows.filter(
    (r) =>
      r.id !== f.id &&
      r.type === f.type &&
      r.subject_kind === f.subject_kind &&
      (r.subject_key || r.subject) === (f.subject_key || f.subject) &&
      ["active", "needs_review", "scheduled"].includes(r.lifecycle) &&
      r.confirmation !== "candidate" &&
      (!r.valid_from || !f.valid_until || r.valid_from <= f.valid_until) &&
      (!f.valid_from || !r.valid_until || f.valid_from <= r.valid_until),
  );
}
registerPage({
  data: {
    facts: [],
    editing: null,
    form: empty,
    fields: fields(empty),
    history: null,
  },
  async load() {
    const rows = await api("/facts?include_inactive=true");
    return {
      facts: rows.map((f) => ({
        ...f,
        typeLabel: types[f.type],
        stateLabel:
          f.confirmation === "conflict"
            ? "存在冲突，待选择"
            : f.confirmation === "candidate"
              ? "待确认"
              : life[f.lifecycle] || "待核对",
        canConfirm: canConfirm(f),
        hasConfirmation: ["candidate", "conflict"].includes(f.confirmation),
        canRetract: ["active", "needs_review", "scheduled"].includes(
          f.lifecycle,
        ),
        sourceLabel:
          f.source_kind === "user_reported_professional"
            ? "你记录的专业建议"
            : f.source_kind === "unconfirmed_extraction"
              ? "根据记录整理，待你确认"
              : f.source_kind === "legacy"
                ? "历史记录"
                : "你提供的信息",
        enforcementLabel: {
          soft: "普通偏好",
          avoid: "需避免或遵守",
          clinical_review: "需专业评估",
          unknown: "含义待核对",
        }[f.enforcement || "unknown"],
        sourceText: Object.values(f.source_details || {})
          .filter(Boolean)
          .join(" · "),
        restoreText: (f.overrides || [])
          .map((id) => rows.find((r) => r.id === id)?.content || "原偏好")
          .join("；"),
        conflictText: rows
          .filter(
            (r) =>
              r.id !== f.id &&
              r.confirmation === "conflict" &&
              r.type === f.type &&
              r.subject_kind === f.subject_kind &&
              (r.subject_key || r.subject) === (f.subject_key || f.subject),
          )
          .map(
            (r) =>
              `${r.content}（${r.valid_from || "未说明"}至${r.valid_until || "未说明"}）`,
          )
          .join("；"),
      })),
    };
  },
  onEdit(e) {
    const fact =
      this.data.facts.find((f) => f.id === e.currentTarget.dataset.id) || "new";
    const form =
      fact === "new"
        ? { ...empty }
        : {
            ...empty,
            ...Object.fromEntries(
              Object.keys(empty)
                .filter((k) => fact[k] != null)
                .map((k) => [k, fact[k]]),
            ),
            ...fact.source_details,
          };
    this.setData({ editing: fact, form, fields: fields(form) });
  },
  onContent(e) {
    this.setData({ "form.content": e.detail.value });
  },
  onFieldsChanged(group, name) {
    if (name === "type")
      this.setData({
        "form.value": Object.keys(values[this.data.form.type] || {})[0] || "",
      });
    this.setData({ fields: fields(this.data.form) });
  },
  onCancelEdit() {
    if (!this.data.busy) this.setData({ editing: null });
  },
  onSave() {
    return this.run(async () => {
      const editing = this.data.editing;
      if (!editing) return;
      const { professional, institution, advised_on, document, ...form } =
        this.data.form;
      await state.mutate(
        editing === "new" ? "/facts" : `/facts/${editing.id}`,
        editing === "new" ? "POST" : "PUT",
        {
          ...form,
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
      this.setData({ editing: null });
    });
  },
  onFactAction(e) {
    const fact = this.data.facts.find(
        (f) => f.id === e.currentTarget.dataset.id,
      ),
      action = e.currentTarget.dataset.action;
    if (!fact) return;
    if (action === "delete")
      return go(`privacy?type=fact&id=${encodeURIComponent(fact.id)}`);
    return this.run(async () => {
      if (action === "history") {
        const epoch = state.privacyGeneration(),
          rows = await api(`/facts/${fact.id}/revisions`);
        if (epoch === state.privacyGeneration())
          this.setData({
            history: {
              id: fact.id,
              rows: rows.map((r) => ({
                ...r,
                lifeLabel: life[r.lifecycle] || "待核对",
              })),
            },
          });
        return;
      }
      if (action === "confirm") {
        if (!canConfirm(fact))
          throw new Error("请先核对限制含义、冲突和适用期限，不能直接确认。");
        if (!(await confirmAction("采用这条信息", fact.content))) return;
        await state.mutate(`/facts/${fact.id}`, "PATCH", {
          action: "confirm",
          expected_version: fact.version,
          ...(fact.schema_version === 2
            ? {
                resolution: fact.valid_until ? "temporary" : "permanent",
                peer_versions: Object.fromEntries(
                  peers(fact, this.data.facts).map((r) => [r.id, r.version]),
                ),
              }
            : {}),
        });
      } else if (action === "retract")
        await state.mutate(`/facts/${fact.id}`, "PATCH", {
          action: "retract",
          expected_version: fact.version,
        });
    });
  },
});
