Component({
  options: { addGlobalClass: true },
  properties: {
    policy: { type: Object, value: {} },
    value: {
      type: Object,
      value: { privacy: false, health: false, model: false },
    },
    disabled: { type: Boolean, value: false },
  },
  data: { policyOpen: false, modelOpen: false },
  methods: {
    togglePolicy() {
      this.setData({ policyOpen: !this.data.policyOpen });
    },
    toggleModel() {
      this.setData({ modelOpen: !this.data.modelOpen });
    },
    onToggle(event) {
      const key = event.currentTarget.dataset.key;
      if (this.data.disabled || !["privacy", "health", "model"].includes(key))
        return;
      if (key === "model" && !this.data.policy.model_available) return;
      this.triggerEvent("change", {
        ...this.data.value,
        [key]: !!event.detail.value,
      });
    },
  },
});
