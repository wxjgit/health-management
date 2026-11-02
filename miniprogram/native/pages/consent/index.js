const { api } = require("../../native-runtime/api");
const { notify } = require("../../native-runtime/platform");
const { registerPage } = require("../../native-runtime/page");

registerPage({
  data: {
    policy: null,
    agreement: { privacy: false, health: false, model: false },
  },
  async load() {
    const [policy, consent] = await Promise.all([
      api("/policies"),
      api("/auth/consent"),
    ]);
    return {
      policy,
      agreement: {
        privacy: !!consent.privacy_accepted,
        health: !!consent.privacy_accepted,
        model: !!consent.model_allowed,
      },
    };
  },
  onAgreement(event) {
    if (!this.data.busy)
      this.setData({ agreement: event.detail, saved: false });
  },
  onSave() {
    const { policy, agreement } = this.data;
    if (!policy || !policy.ready || !agreement.privacy || !agreement.health)
      return;
    return this.run(async () => {
      await api("/auth/consent", "PUT", {
        privacy_version: policy.version,
        privacy_consent: true,
        health_consent: true,
        model_consent: !!agreement.model,
      });
      if (!this._unloaded) this.setData({ saved: true });
      notify("changed");
    });
  },
});
