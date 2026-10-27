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
    navigationBarBackgroundColor: "#f4f7f2",
    navigationBarTextStyle: "black",
  },
  tabBar: {
    color: "#758079",
    selectedColor: "#287653",
    backgroundColor: "#ffffff",
    list: [
      { pagePath: "pages/chat/index", text: "记录" },
      { pagePath: "pages/history/index", text: "历史" },
      { pagePath: "pages/plans/index", text: "计划" },
      { pagePath: "pages/insights/index", text: "发现" },
      { pagePath: "pages/settings/index", text: "我的" },
    ],
  },
});
