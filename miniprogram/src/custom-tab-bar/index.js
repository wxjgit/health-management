Component({
  data: {
    selected: 0,
    items: [
      { path: "pages/chat/index", label: "记录", icon: "record" },
      { path: "pages/history/index", label: "历史", icon: "history" },
      { path: "pages/plans/index", label: "计划", icon: "plans" },
      { path: "pages/insights/index", label: "发现", icon: "insights" },
      { path: "pages/settings/index", label: "我的", icon: "mine" },
    ],
  },
  lifetimes: {
    attached() {
      this.syncRoute();
    },
  },
  pageLifetimes: {
    show() {
      this.syncRoute();
    },
  },
  methods: {
    syncRoute() {
      const pages = getCurrentPages();
      const route = pages[pages.length - 1]?.route;
      const selected = this.data.items.findIndex((item) => item.path === route);
      if (selected >= 0 && selected !== this.data.selected)
        this.setData({ selected });
    },
    switchPage(event) {
      const selected = Number(event.currentTarget.dataset.index);
      const item = this.data.items[selected];
      if (!item || selected === this.data.selected || this.switching) return;
      this.switching = true;
      wx.switchTab({
        url: `/${item.path}`,
        fail: () =>
          wx.showToast({ title: "暂时无法切换，请重试", icon: "none" }),
        complete: () => {
          this.switching = false;
          this.syncRoute();
        },
      });
    },
  },
});
