export default defineAppConfig({
  pages: [
    "pages/chat/index",
    "pages/history/index",
    "pages/plans/index",
    "pages/insights/index",
    "pages/settings/index",
    "pages/today/index",
    "pages/facts/index",
    "pages/preferences/index",
    "pages/notifications/index",
    "pages/privacy/index",
  ],
  window: {
    navigationBarTitleText: "轻记",
    navigationBarBackgroundColor: "#eef5f5",
    navigationBarTextStyle: "black",
    backgroundColor: "#eef5f5",
  },
  usingComponents: {},
  tabBar: {
    custom: true,
    color: "#7b899a",
    selectedColor: "#2854ea",
    backgroundColor: "#eef5f5",
    list: [
      { pagePath: "pages/chat/index", text: "记录" },
      { pagePath: "pages/history/index", text: "历史" },
      { pagePath: "pages/plans/index", text: "计划" },
      { pagePath: "pages/insights/index", text: "发现" },
      { pagePath: "pages/settings/index", text: "我的" },
    ],
  },
});
