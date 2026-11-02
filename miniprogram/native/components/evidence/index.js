const { go } = require("../../native-runtime/ui");
Component({
  options: { addGlobalClass: true },
  properties: { rows: { type: Array, value: [] } },
  methods: {
    open(e) {
      wx.setStorageSync("evidence-event", e.currentTarget.dataset.id);
      void go("history");
    },
  },
});
