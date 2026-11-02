Component({
  options: { addGlobalClass: true, multipleSlots: true },
  properties: { show: Boolean, title: String, busy: Boolean },
  methods: {
    noop() {},
    close() {
      if (!this.data.busy) this.triggerEvent("close");
    },
  },
});
