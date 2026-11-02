const { api } = require("../../native-runtime/api");
const state = require("../../native-runtime/state");
const { go, labels, numberText } = require("../../native-runtime/ui");
Component({
  options: { addGlobalClass: true },
  properties: { event: { type: Object, value: null }, highlight: Boolean },
  data: { rows: [], error: "", busy: false, kindLabel: "", items: [] },
  observers: {
    event(e) {
      if (!e) return;
      this.setData({
        kindLabel: labels[e.kind] || e.kind,
        items: (e.data.items || []).map((i) => ({
          ...i,
          description: `${i.name} · ${i.quantity == null ? "分量未知" : i.quantity + i.unit} · ${numberText(i.kcal)} kcal`,
        })),
      });
    },
  },
  lifetimes: {
    attached() {
      this._alive = true;
      this._reset = state.subscribeReset(() =>
        this.setData({ rows: [], items: [], error: "" }),
      );
    },
    detached() {
      this._alive = false;
      this._reset?.();
    },
  },
  methods: {
    correct() {
      wx.setStorageSync("correction-event", this.data.event);
      void go("chat");
    },
    remove() {
      void go(
        `privacy?type=event&id=${encodeURIComponent(this.data.event.id)}`,
      );
    },
    async revisions() {
      if (this.data.busy) return;
      const epoch = state.privacyGeneration();
      this.setData({ busy: true, error: "" });
      try {
        const rows = await api(`/events/${this.data.event.id}/revisions`);
        if (this._alive && epoch === state.privacyGeneration())
          this.setData({
            rows: rows.map((r) => ({
              ...r,
              diff: (r.changes || [])
                .map(
                  (c) =>
                    `${labels[c.field] || "记录内容"}：${JSON.stringify(c.before)} → ${JSON.stringify(c.after)}`,
                )
                .join("\n"),
            })),
          });
      } catch (e) {
        if (this._alive && epoch === state.privacyGeneration())
          this.setData({ error: e.message });
      } finally {
        if (this._alive) this.setData({ busy: false });
      }
    },
  },
});
