// components/panel-mine/panel-mine.ts — 我的面板 · 虚拟页
import { profile, insights, system, records } from '../../runtime/api';
import { getUsage, logout } from '../../runtime/auth';
import { notify, confirm, showApiError } from '../../runtime/feedback';

Component({
  options: { addGlobalClass: true },
  properties: { active: { type: Boolean, value: false } },
  observers: {
    active(v: boolean) { if (v) this.loadAll(); },
  },
  data: {
    userIdShort: '', days: 0, modelConsent: false,
    usage: { modelAttempts: 0, remaining: 0, dailyLimit: 0 },
    totalRecords: 0,
    profileSummary: '尚未建档',
    prefsSummary: '评分权重与提醒时间',
    conflictCount: 0, unread: 0,
  },
  methods: {
    loadAll() {
      const g = getApp().globalData;
      const uid = g.userId || '';
      this.setData({
        userIdShort: uid ? uid.slice(0, 4) + '…' + uid.slice(-3) : '本地',
        modelConsent: g.processingMode === 'model_allowed',
      });
      this.loadUsage();
      this.loadProfile();
      this.loadFacts();
      this.loadUnread();
    },

    async loadUsage() {
      try {
        const u = await getUsage();
        this.setData({ usage: { modelAttempts: u.model_attempts ?? 0, remaining: u.remaining ?? 0, dailyLimit: u.daily_limit ?? 0 } });
      } catch (e) {}
      try {
        const rows = await records.events({ limit: 1000 });
        this.setData({ totalRecords: (rows || []).length });
      } catch (e) {}
    },

    async loadProfile() {
      try {
        const p = await profile.get();
        if (p && (p.height_cm || p.age)) {
          this.setData({ profileSummary: `身高 ${p.height_cm || '?'}cm · ${p.age || '?'}岁 · ${p.sex === 'male' ? '男' : p.sex === 'female' ? '女' : '—'}` });
        }
      } catch (e) {}
    },

    async loadFacts() {
      try {
        const facts = await profile.facts(true);
        this.setData({ conflictCount: (facts || []).filter((f: any) => f.status === 'conflict' || f.conflict).length });
      } catch (e) {}
    },

    async loadUnread() {
      try {
        const list = await insights.notifications();
        this.setData({ unread: (list || []).filter((n: any) => !n.read && !n.read_at).length });
      } catch (e) {}
    },

    go(e: any) { wx.navigateTo({ url: e.currentTarget.dataset.url }); },

    async onExport() {
      const ok = await confirm({
        title: '导出我的数据',
        content: '将生成 health-records.json（含全部记录、修订与血缘说明，不含任何密钥）。生成后可通过微信分享或保存到手机。',
        confirmText: '生成导出',
      });
      if (!ok) return;
      notify.info('正在生成导出文件…');
      const url = system.exportUrl();
      wx.downloadFile({
        url,
        header: { Authorization: 'Bearer ' + getApp().globalData.token },
        success: (res: any) => {
          if (res.statusCode === 200 && res.tempFilePath) {
            notify.success('导出成功，可分享文件');
            if (wx.shareFileMessage) wx.shareFileMessage({ filePath: res.tempFilePath, fileName: 'health-records.json' });
          } else notify.error('导出失败：' + res.statusCode);
        },
        fail: () => notify.error('导出失败，请检查后端连接'),
      } as any);
    },

    async onLogout() {
      const ok = await confirm({
        title: '退出登录',
        content: '将撤销当前登录凭证。本地缓存的请求凭据会保留以便恢复，健康数据仍安全保存在服务端。',
        confirmText: '退出', destructive: true,
      });
      if (!ok) return;
      await logout();
      wx.reLaunch({ url: '/pages/login/login' });
    },
  },
});
