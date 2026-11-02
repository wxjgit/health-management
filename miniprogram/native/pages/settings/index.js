const { api, requestId } = require("../../native-runtime/api");
const state = require("../../native-runtime/state");
const { registerPage } = require("../../native-runtime/page");
const {
  confirmAction,
  go,
  labels,
  numberText,
  fieldRows,
  schema,
} = require("../../native-runtime/ui");
const { call } = require("../../native-runtime/platform");
const { clearAccountData } = require("../../native-runtime/account");
const { exportMyData } = require("../../native-runtime/export");
const { removeExports } = require("../../native-runtime/privacy");
const keys = ["kcal", "protein_g", "carbs_g", "fat_g", "exercise_minutes"];
const emptyProfile = {
  age: "",
  height_cm: "",
  weight_kg: "",
  goal_weight_kg: "",
  goal_date: "",
  sex: "other",
  activity: "sedentary",
  nutrition_context: "unspecified",
};
const planView = (form) =>
  keys.map((key) => ({
    key,
    label: `${labels[key]} · ${key === "kcal" ? "kcal" : key === "exercise_minutes" ? "分钟/周" : "g"}`,
    value: form[key] || "",
  }));
registerPage({
  route: "pages/settings/index",
  poll: 2000,
  shouldPoll() {
    return this.data.confirmations.some((w) => w.status === "resuming");
  },
  data: {
    profile: null,
    profileForm: emptyProfile,
    profileFields: fieldRows(emptyProfile, schema.profile),
    planForm: {},
    planFields: [],
    usingDefaults: false,
    note: "",
    reply: "",
    plans: [],
    confirmations: [],
    active: null,
    activeRows: [],
    status: "",
    user: null,
    hubs: [
      {
        page: "facts",
        title: "长期信息",
        detail: "偏好、限制与变更历史",
        icon: "record",
      },
      {
        page: "preferences",
        title: "偏好与提醒",
        detail: "定义适合自己的节奏",
        icon: "plans",
      },
      {
        page: "notifications",
        title: "站内消息",
        detail: "复盘与提醒都在这里",
        icon: "history",
      },
      {
        page: "privacy",
        title: "隐私管理",
        detail: "删除范围与清理恢复",
        icon: "mine",
      },
    ],
  },
  onReset() {
    this._activeId = "";
    this._planDirty = false;
  },
  async load() {
    const [profile, health, plans, workflows, defaults, account] =
      await Promise.all([
        api("/profile"),
        api("/health"),
        api("/plans"),
        api("/workflows"),
        api("/targets/default"),
        api("/auth/me"),
      ]);
    const active = plans.find((p) => p.status === "active") || null;
    let profileForm = this.data.profileForm;
    if (this.data.modal !== "profile")
      profileForm = profile
        ? {
            ...profile,
            ...Object.fromEntries(
              ["age", "height_cm", "weight_kg", "goal_weight_kg"].map((k) => [
                k,
                String(profile[k]),
              ]),
            ),
            nutrition_context: profile.nutrition_context || "unspecified",
          }
        : { ...emptyProfile };
    let planForm = this.data.planForm,
      usingDefaults = this.data.usingDefaults;
    if (active && this._activeId !== active.id) {
      this._activeId = active.id;
      this._planDirty = false;
      usingDefaults = false;
      planForm = Object.fromEntries(
        keys.map((k) => [k, String(active.data.targets[k])]),
      );
    } else if ((!active || usingDefaults) && !this._planDirty) {
      if (!active) this._activeId = "";
      planForm = Object.fromEntries(
        keys.map((k) => [
          k,
          defaults.targets ? String(defaults.targets[k]) : "",
        ]),
      );
      usingDefaults = !!defaults.targets;
    }
    const confirmations = workflows
      .filter((w) => w.pending_input?.kind === "plan_confirmation")
      .map((w) => {
        const stored = plans.find(
            (p) => p.id === (w.pending_input.plan_id || w.thread_id),
          ),
          settled = !!stored && stored.status !== "pending";
        return {
          ...w,
          settled,
          title: settled
            ? stored.status === "active"
              ? "计划已生效"
              : "草案已结束"
            : w.status === "resuming"
              ? "计划正在确认"
              : "待你确认",
          changesText: Object.entries(w.pending_input.changes || {})
            .map(([k, v]) => `${labels[k] || k} ${v > 0 ? "+" : ""}${v}%`)
            .join(" · "),
        };
      });
    const activeRows = active
      ? [
          ["每日热量", "kcal", "kcal"],
          ["每周运动", "exercise_minutes", "分钟"],
          ["蛋白质", "protein_g", "g"],
          ["碳水", "carbs_g", "g"],
          ["脂肪", "fat_g", "g"],
          ["膳食纤维", "fiber_g", "g"],
        ].map(([label, k, unit]) => ({
          label,
          text:
            active.data.targets[k] == null
              ? "未设置"
              : `${numberText(active.data.targets[k])} ${unit}`,
        }))
      : [];
    if (active)
      activeRows.push({
        label: "每日运动",
        text:
          (active.data.schedule || []).find((d) => d.date === health.today)
            ?.exercise || "暂无当日安排",
      });
    return {
      profile,
      profileForm,
      profileFields: fieldRows(profileForm, schema.profile),
      health,
      plans,
      confirmations,
      defaults,
      active,
      activeRows,
      user: account.user,
      planForm,
      planFields: planView(planForm),
      usingDefaults,
    };
  },
  onFieldsChanged(group) {
    if (group === "planForm") {
      this._planDirty = true;
      this.setData({
        usingDefaults: false,
        planFields: planView(this.data.planForm),
      });
    } else if (group === "profileForm")
      this.setData({
        profileFields: fieldRows(this.data.profileForm, schema.profile),
      });
  },
  onNote(e) {
    this.setData({ note: e.detail.value });
  },
  onDefaults() {
    if (!this.data.defaults?.targets) return;
    this._planDirty = false;
    const planForm = Object.fromEntries(
      keys.map((k) => [k, String(this.data.defaults.targets[k])]),
    );
    this.setData({
      planForm,
      planFields: planView(planForm),
      usingDefaults: true,
    });
  },
  onSaveProfile() {
    return this.run(async () => {
      const form = this.data.profileForm;
      if (
        ["age", "height_cm", "weight_kg", "goal_weight_kg"].some(
          (k) => !String(form[k]).trim() || !Number.isFinite(Number(form[k])),
        ) ||
        !form.goal_date
      )
        throw new Error("请填写完整数值和目标日期。");
      await state.mutate("/profile", "PUT", {
        ...form,
        ...Object.fromEntries(
          ["age", "height_cm", "weight_kg", "goal_weight_kg"].map((k) => [
            k,
            Number(form[k]),
          ]),
        ),
      });
      this.setData({
        status: "档案已保存。建档体重与每日称重分开保存。",
        modal: "",
      });
      this.syncModal();
    });
  },
  onPropose() {
    return this.run(async () => {
      if (
        keys.some(
          (k) =>
            !String(this.data.planForm[k] ?? "").trim() ||
            !Number.isFinite(Number(this.data.planForm[k])),
        )
      )
        throw new Error("请填写完整的数值目标。");
      const targets = Object.fromEntries(
        keys.map((k) => [k, Number(this.data.planForm[k])]),
      );
      const result = await state.executeOperation(
        state.operation(
          "/plans/propose",
          {
            request_id: requestId(),
            targets,
            note: this.data.note,
            target_source: this.data.usingDefaults
              ? "system_default"
              : "manual",
            ...(this.data.usingDefaults
              ? { suggestion_fingerprint: this.data.defaults.fingerprint }
              : {}),
          },
          "生成计划",
        ),
      );
      this.setData({ reply: result.reply, modal: "" });
      this.syncModal();
    });
  },
  onDecision(e) {
    const row = this.data.confirmations.find(
      (w) => w.thread_id === e.currentTarget.dataset.id,
    );
    if (!row) return;
    const mode = e.currentTarget.dataset.mode;
    return this.run(async () => {
      if (mode === "cancel") {
        if (
          await confirmAction(
            "取消待处理计划",
            "已生效目标保留，只取消当前待处理草案。",
          )
        )
          await state.mutate(`/workflows/${row.thread_id}`, "DELETE");
        return;
      }
      let body;
      if (mode === "retry") {
        if (!row.retry_request) throw new Error("没有可恢复的原确认请求。");
        body = row.retry_request;
      } else {
        if (row.settled || row.retry_request || row.status === "resuming")
          throw new Error("请先核对当前计划确认结果。");
        const approved = mode === "approve";
        if (
          !(await confirmAction(
            approved ? "采用此计划" : "保留原计划",
            row.pending_input.question || "请核对当前草案。",
          ))
        )
          return;
        body = {
          request_id: requestId(),
          interrupt_id: row.pending_input.interrupt_id,
          approved,
        };
      }
      const result = await state.executeOperation(
        state.operation(`/workflows/${row.thread_id}/resume`, body, "计划确认"),
      );
      this.setData({ reply: result.reply });
    });
  },
  onSource(e) {
    return call("setClipboardData", { data: e.currentTarget.dataset.url });
  },
  onExport() {
    return this.run(exportMyData);
  },
  onLogout() {
    return this.run(async () => {
      await state.exclusive(() => api("/auth/logout", "POST"));
      await clearAccountData();
      await call("reLaunch", { url: "/pages/login/index" });
    });
  },
  onDeleteAccount() {
    return this.run(async () => {
      if (
        !(await confirmAction(
          "注销账号",
          "将撤销全部登录并清理本账号的档案、记录、聊天、计划和后台任务，无法撤销。运行中的操作结束后完成清理。备份按保留周期清理，自行分享的文件需自行删除。",
        ))
      )
        return;
      await removeExports();
      const result = await state.exclusive(() =>
        api("/auth/account", "DELETE", { confirmation: "注销账号" }),
      );
      await clearAccountData();
      await call("showModal", {
        title: result.state === "completed" ? "账号已注销" : "注销已受理",
        content:
          result.state === "completed"
            ? "服务端账号数据已清理。"
            : "全部登录已撤销，后台会在运行中的操作结束后继续清理。",
        showCancel: false,
      });
      await call("reLaunch", { url: "/pages/login/index" });
    });
  },
});
