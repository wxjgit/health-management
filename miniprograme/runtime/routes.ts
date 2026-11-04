// runtime/routes.ts — Tab 槽位与路由标题映射（无副作用常量，供 app.ts / navbar 共用）
// 虚拟页面切换：5 个 Root Tab 共用宿主页 pages/home/home，?tab=N 定位面板。
// iconName 取自 PyUI SVG 几何图标集（pyui-icon，含轻记扩展图标）。

export const TAB_SLOTS = [
  { iconName: 'edit', selectedIconName: 'edit', text: '记录', pagePath: 'pages/home/home' },
  { iconName: 'clock', selectedIconName: 'clock', text: '历史', pagePath: 'pages/home/home' },
  { iconName: 'calendar', selectedIconName: 'calendar', text: '计划', pagePath: 'pages/home/home' },
  { iconName: 'star', selectedIconName: 'star', text: '发现', pagePath: 'pages/home/home' },
  { iconName: 'user', selectedIconName: 'user', text: '我的', pagePath: 'pages/home/home' },
];

// 深链到指定 Tab 面板（登录/删除返回等场景用；navbar indexPath 契约需无 query，故单独提供）
export function homeTabUrl(tab: number): string {
  return '/pages/home/home?tab=' + tab;
}

// 路由 → 导航栏标题（pyui-navbar 自动取用；home 页标题由宿主按 tab 动态设置）
export const PAGES_DATA: Record<string, string> = {
  'pages/login/login': '轻记',
  'pages/home/home': '记录',
  'pages/trends/trends': '历史趋势',
  'pages/event/event': '记录详情',
  'pages/today/today': '今日汇总',
  'pages/profile/profile': '个人档案',
  'pages/facts/facts': '长期信息',
  'pages/prefs/prefs': '评分权重与提醒',
  'pages/messages/messages': '站内消息',
  'pages/consent/consent': '隐私与模型授权',
  'pages/deletion/deletion': '隐私删除',
};

// 首页路径（pyui-navbar indexPath 契约：tabBar[0].pagePath）
export const INDEX_PATH = '/pages/home/home';
