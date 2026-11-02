const { api } = require("../../native-runtime/api");
const { mutate } = require("../../native-runtime/state");
const { registerPage } = require("../../native-runtime/page");
const { labels, fieldRows } = require("../../native-runtime/ui");
const weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"].map(
  (label, value) => ({ label, value: String(value) }),
);
registerPage({
  data: {
    draft: null,
    weights: {},
    weightRows: [],
    timeRows: [],
    total: 0,
    status: "",
  },
  async load() {
    const [draft, suggestion] = await Promise.all([
      api("/preferences"),
      api("/preferences/suggestion"),
    ]);
    const weights = Object.fromEntries(
      Object.entries(draft.weights).map(([k, v]) => [k, String(v)]),
    );
    this._dirty = false;
    return { draft, suggestion, weights, ...this.view(draft, weights) };
  },
  view(draft, weights) {
    return {
      weightRows: Object.entries(weights).map(([key, value]) => ({
        key,
        label: `${labels[key] || key} %`,
        value,
      })),
      total: Object.values(weights).reduce((s, v) => s + Number(v || 0), 0),
      timeRows: [
        ...Object.entries(draft.meal_times).map(([key, value]) => ({
          key: `meal_times.${key}`,
          label: `${labels[key]}时间`,
          type: "time",
          value,
        })),
        {
          key: "checkin_time",
          label: "身体状态提醒",
          type: "time",
          value: draft.checkin_time,
        },
        {
          key: "summary_time",
          label: "每日总结",
          type: "time",
          value: draft.summary_time,
        },
        {
          key: "weekly_day",
          label: "每周复盘",
          type: "select",
          value: String(draft.weekly_day),
          options: weekdays,
        },
      ],
    };
  },
  onFieldsChanged() {
    this.setData(this.view(this.data.draft, this.data.weights));
  },
  onNotify(e) {
    this.setData({ "draft.notifications_enabled": !!e.detail.value });
  },
  onSuggestion() {
    if (this.data.suggestion) {
      const weights = Object.fromEntries(
        Object.entries(this.data.suggestion.weights).map(([k, v]) => [
          k,
          String(v),
        ]),
      );
      this.setData({ weights, ...this.view(this.data.draft, weights) });
    }
  },
  onSave() {
    return this.run(async () => {
      const weights = this.data.weights;
      if (
        Object.values(weights).some(
          (v) =>
            !String(v).trim() ||
            !Number.isFinite(Number(v)) ||
            Number(v) < 0 ||
            Number(v) > 100,
        )
      )
        throw new Error("权重必须是0到100的数值。");
      const values = Object.fromEntries(
        Object.entries(weights).map(([k, v]) => [k, Number(v)]),
      );
      if (
        Math.abs(Object.values(values).reduce((a, b) => a + b, 0) - 100) > 0.01
      )
        throw new Error("权重合计必须为100%。");
      await mutate("/preferences", "PUT", {
        ...this.data.draft,
        weekly_day: Number(this.data.draft.weekly_day),
        weights: values,
      });
      this.setData({ status: "设置已保存。" });
    });
  },
});
