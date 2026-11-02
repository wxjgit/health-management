Component({
  options: { addGlobalClass: true },
  properties: { title: String },
  data: { open: false },
  methods: {
    toggle() {
      this.setData({ open: !this.data.open });
    },
  },
});
