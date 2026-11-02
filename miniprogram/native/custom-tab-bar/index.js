Component({
  data: {
    selected: 0,
    hidden: false,
    items: [
      {
        path: "pages/chat/index",
        text: "记录",
        iconName: "edit",
        selectedIconName: "edit",
      },
      {
        path: "pages/history/index",
        text: "足迹",
        iconName: "clock",
        selectedIconName: "clock",
      },
      {
        path: "pages/insights/index",
        text: "复盘",
        iconName: "star",
        selectedIconName: "star",
      },
      {
        path: "pages/settings/index",
        text: "我的",
        iconName: "user",
        selectedIconName: "user",
      },
    ],
  },
  lifetimes: {
    detached() {
      clearTimeout(this.switchTimer);
    },
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
    syncLens(selected) {
      this.selectComponent?.("#pyui-tabs")?.syncSelection?.(selected);
    },
    setActiveRoute(route) {
      if (!this.data.items.some((item) => item.path === route)) return;
      this.ownerRoute = route;
      this.syncRoute();
    },
    syncRoute() {
      const pages = getCurrentPages();
      const route = this.ownerRoute || pages[pages.length - 1]?.route;
      const selected = this.data.items.findIndex((item) => item.path === route);
      if (selected < 0) return;
      if (selected !== this.data.selected) {
        this.setData({ selected }, () => this.syncLens(selected));
      } else {
        this.syncLens(selected);
      }
    },
    switchPage(event) {
      const selected = Number(event.detail.index);
      const item = this.data.items[selected];
      if (!item || this.data.hidden || this.switching) {
        this.syncLens(this.data.selected);
        return;
      }
      if (selected === this.data.selected) return;
      this.switching = true;
      this.setData({ selected });
      this.switchTimer = setTimeout(
        () =>
          wx.switchTab({
            url: `/${item.path}`,
            fail: () =>
              wx.showToast({ title: "暂时无法切换，请重试", icon: "none" }),
            complete: () => {
              this.switching = false;
              this.syncRoute();
            },
          }),
        220,
      );
    },
  },
});
