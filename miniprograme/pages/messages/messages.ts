// pages/messages/messages.ts — 站内消息 · Task
import { insights } from '../../runtime/api';
import { notify, showApiError } from '../../runtime/feedback';
import { fmtRelative } from '../../runtime/format';

const ICONS: Record<string, string> = {
  daily_report: 'chart', weekly_review: 'calendar', reminder: 'clock', archive: 'archive', plan: 'list',
};

Page({
  data: { navH: 88, loading: true, list: [] as any[] },

  onLoad() { this.measure(); },
  onShow() { this.load(); },

  measure() {
    try {
      const wi: any = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
      const menu = wx.getMenuButtonBoundingClientRect();
      const navH = (menu.top - wi.statusBarHeight) * 2 + menu.height + wi.statusBarHeight;
      this.setData({ navH });
    } catch (e) {}
  },

  async load() {
    this.setData({ loading: true });
    try {
      const rows = await insights.notifications();
      const list = (rows || []).map((n: any) => ({
        ...n,
        relTime: fmtRelative(n.created_at),
        icon: ICONS[n.key] || 'bell',
      }));
      this.setData({ list, loading: false });
    } catch (e) {
      this.setData({ loading: false });
      showApiError(e);
    }
  },

  async onTap(e: any) {
    const id = e.currentTarget.dataset.id;
    const item = this.data.list.find((x) => x.id === id);
    if (!item) return;
    if (!item.read) {
      try {
        await insights.readNotification(id);
        this.setData({ list: this.data.list.map((x) => x.id === id ? { ...x, read: 1 } : x) });
      } catch (err) { showApiError(err); }
    }
  },

  async readAll() {
    const unread = this.data.list.filter((x) => !x.read);
    if (!unread.length) { notify.info('没有未读消息'); return; }
    try {
      await Promise.all(unread.map((x) => insights.readNotification(x.id)));
      this.setData({ list: this.data.list.map((x) => ({ ...x, read: 1 })) });
      notify.success('已全部标记已读');
    } catch (e) { showApiError(e); }
  },
});
