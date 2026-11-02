const { api } = require("../../native-runtime/api");
const { mutate } = require("../../native-runtime/state");
const { registerPage } = require("../../native-runtime/page");
registerPage({
  data: { notices: [] },
  poll: 60000,
  async load() {
    return { notices: await api("/notifications") };
  },
  onRead(e) {
    return this.run(() =>
      mutate(`/notifications/${e.currentTarget.dataset.id}`, "PATCH"),
    );
  },
});
