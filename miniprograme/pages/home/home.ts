// pages/home/home.ts — Root 宿主页 · 虚拟页面切换
// 5 个面板常驻（hidden 切换保留状态），tabbar 实例常驻：
// 切换只 setData tab → 透镜弹簧动画跟手；无 reLaunch，无白屏闪屏。
import { TAB_SLOTS } from '../../runtime/routes';

const NAV_TITLES = ['记录', '历史', '计划', '发现', '我的'];
// 分享文案：按落点 tab 定制（标题即卡片第一行，突出价值而非功能罗列）
const SHARE_TITLES = [
  '轻记 · 一句话说清吃了什么、睡得怎样',
  '轻记 · 把每天的摄入和体重看成一条线',
  '轻记 · 给减脂定一个够得着的目标',
  '轻记 · 从记录里看见自己的规律',
  '轻记 · 你的健康数据只属于你',
];
const SHARE_SLOGAN = '轻记 · 一句话说清吃了什么、睡得怎样';

Page({
  data: {
    tab: 0,
    mounted: false,   // 首帧只挂当前面板；onReady 后全量挂载（见 wxml 注释）
    tabSlots: TAB_SLOTS,
    navTitle: '记录',
    navH: 88,
    tabPad: 96,
  },

  onLoad(q: any) {
    this.measure();
    const t = parseInt(q && q.tab, 10);
    if (!isNaN(t) && t >= 0 && t <= 4) this.setData({ tab: t, navTitle: NAV_TITLES[t] });
  },

  onReady() {
    // 首帧上屏后再挂载其余面板：把首屏渲染成本降到最低，缩短白屏；
    // 让出两帧给渲染器，避免与首帧 setData 抢同一批任务
    wx.nextTick(() => {
      setTimeout(() => this.setData({ mounted: true }), 60);
    });
  },

  measure() {
    try {
      const wi: any = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
      const menu = wx.getMenuButtonBoundingClientRect();
      const navH = (menu.top - wi.statusBarHeight) * 2 + menu.height + wi.statusBarHeight;
      const safe = wi.safeArea ? Math.max(0, wi.screenHeight - wi.safeArea.bottom) : 0;
      // 悬浮 tabbar 占位 ≈ 自身(56) + 底偏移(10+safe) + 呼吸 gap
      this.setData({ navH, tabPad: 56 + 10 + safe + 16 });
    } catch (e) {}
  },

  onTabChange(e: any) {
    const idx = e && e.detail ? (e.detail.index != null ? e.detail.index : e.detail.tab) : 0;
    if (idx === this.data.tab) return;
    this.setData({ tab: idx, navTitle: NAV_TITLES[idx] || '记录' });
  },

  onTabSettle() { /* 透镜落位；虚拟页无需额外动作 */ },

  // 分享：仅公开落点页开启；路径带 tab 直达对应面板，分享图 5:4
  onShareAppMessage() {
    const t = this.data.tab;
    return {
      title: SHARE_TITLES[t] || SHARE_SLOGAN,
      path: '/pages/home/home?tab=' + t,
      imageUrl: '/assets/share.png',
    };
  },
  // 面板请求跳转其它 tab（如 chat 的"查看历史"）
  onGotoTab(e: any) {
    const idx = e && e.detail ? e.detail.tab : 0;
    this.setData({ tab: idx, navTitle: NAV_TITLES[idx] || '记录' });
  },
});
