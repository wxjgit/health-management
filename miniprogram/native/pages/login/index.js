const { api } = require("../../native-runtime/api");
const { call } = require("../../native-runtime/platform");
const { wechatLogin } = require("../../native-runtime/account");
const { registerPage } = require("../../native-runtime/page");

registerPage({
  publicPage: true,
  data: {
    policy: null,
    agreement: { privacy: false, health: false, model: false },
  },
  async load() {
    return { policy: await api("/policies") };
  },
  onAgreement(event) {
    if (!this.data.busy) this.setData({ agreement: event.detail });
  },
  onLogin() {
    const { policy, agreement } = this.data;
    if (!policy || !policy.ready || !agreement.privacy || !agreement.health)
      return;
    return this.run(async () => {
      await wechatLogin({
        privacy_version: policy.version,
        privacy_consent: true,
        health_consent: true,
        model_consent: !!agreement.model,
      });
      await call("reLaunch", { url: "/pages/settings/index" });
    });
  },
});
