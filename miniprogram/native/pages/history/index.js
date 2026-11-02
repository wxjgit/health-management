const { api, queryString } = require("../../native-runtime/api");
const { registerPage } = require("../../native-runtime/page");
const { numberText } = require("../../native-runtime/ui");
registerPage({
  route: "pages/history/index",
  data: {
    section: "trends",
    metric: "weight_kg",
    recordDate: "",
    selected: "",
    events: [],
    days: [],
    months: [],
    records: [],
    bars: [],
    points: [],
    filter: { kind: "", search: "" },
  },
  onShow() {
    const id = wx.getStorageSync("evidence-event");
    if (id) {
      this.setData({
        section: "records",
        selected: id,
        recordDate: "",
        filter: { kind: "", search: "" },
      });
      wx.removeStorageSync("evidence-event");
    }
  },
  async load() {
    const [events, days, months] = await Promise.all([
      api(`/events?${queryString({ ...this.data.filter, limit: "1000" })}`),
      api("/trends?days=28"),
      api("/monthly-summaries"),
    ]);
    return {
      events,
      days,
      months: months.map((m) => ({
        ...m,
        weightText: numberText(m.data.avg_weight_kg),
      })),
      ...this.project(events, days),
    };
  },
  project(events = this.data.events, days = this.data.days) {
    const { metric, selected } = this.data;
    const date =
      this.data.recordDate ||
      events.find((e) => e.id === selected)?.day ||
      events
        .map((e) => e.day)
        .sort()
        .reverse()[0] ||
      "";
    const rows = days.filter((d) => d[metric] != null),
      maximum = Math.max(1, ...rows.map((d) => d[metric]));
    return {
      activeDate: date,
      records: events
        .filter((e) => e.day === date)
        .sort((a, b) => Number(b.id === selected) - Number(a.id === selected)),
      metricLabel:
        metric === "weight_kg"
          ? "体重趋势 · kg"
          : metric === "sleep_minutes"
            ? "睡眠趋势 · 小时"
            : "运动趋势 · 分钟",
      bars: days.map((d, i) => ({
        date: d.date,
        known: d[metric] != null,
        height:
          d[metric] == null ? 0 : Math.max(2, (d[metric] / maximum) * 100),
        dateLabel: i % 7 === 0 || i === days.length - 1 ? d.date.slice(5) : "",
      })),
      points: rows.map((d) => ({
        date: d.date,
        text: numberText(
          metric === "sleep_minutes" ? d[metric] / 60 : d[metric],
        ),
      })),
      recordedDays: rows.length,
    };
  },
  onSection(e) {
    this.setData({ section: e.currentTarget.dataset.section });
  },
  onMetric(e) {
    this.setData({ metric: e.currentTarget.dataset.metric });
    this.setData(this.project());
  },
  onDate(e) {
    this.setData({ recordDate: e.detail.value });
    this.setData(this.project());
  },
  onFieldsChanged() {},
});
