Component({
  options: { addGlobalClass: true },
  properties: {
    name: String,
    group: { type: String, value: "form" },
    label: String,
    value: { type: null, value: "" },
    type: { type: String, value: "text" },
    options: { type: Array, value: [] },
    disabled: Boolean,
    maxlength: { type: Number, value: 500 },
  },
  data: { display: "", selectedIndex: 0 },
  observers: {
    "value,options": function (value, options) {
      const selectedIndex = (options || []).findIndex(
        (o) => String(o.value) === String(value),
      );
      this.setData({
        selectedIndex: Math.max(0, selectedIndex),
        display:
          (options || []).find((o) => String(o.value) === String(value))
            ?.label || "请选择",
      });
    },
  },
  methods: {
    change(e) {
      if (this.data.disabled) return;
      let value = e.detail.value;
      if (this.data.type === "select")
        value = this.data.options[Number(value)]?.value ?? "";
      this.triggerEvent("change", {
        name: this.data.name,
        group: this.data.group,
        value: String(value),
      });
    },
  },
});
