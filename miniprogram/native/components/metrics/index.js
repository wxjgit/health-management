const { metrics } = require("../../native-runtime/ui");
Component({
  options: { addGlobalClass: true },
  properties: { targets: { type: Object, value: null } },
  data: { rows: [] },
  observers: {
    targets(value) {
      this.setData({ rows: metrics(value) });
    },
  },
});
