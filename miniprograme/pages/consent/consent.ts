// pages/consent/consent.ts — 隐私政策与模型授权 · Task
import { fetchPolicies, updateConsent, closeAccount, getConsent } from '../../runtime/auth';
import { notify, confirm, showApiError } from '../../runtime/feedback';

Page({
  data: {
    navH: 88,
    readonly: false,
    saving: false,
    modelConsent: false,
    versionShort: '',
    policies: {} as any,
  },

  onLoad(q: any) {
    this.measure();
    this.setData({ readonly: q.readonly === '1' });
  },
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
    try {
      const p = await fetchPolicies();
      const g = getApp().globalData;
      this.setData({
        policies: p,
        versionShort: p.revision || (p.version ? String(p.version).slice(0, 8) : ''),
        modelConsent: this.data.readonly ? g.modelConsent : g.modelConsent,
      });
      if (!this.data.readonly) this.loadConsent();
    } catch (e) { showApiError(e); }
  },

  async loadConsent() {
    // 以服务端授权状态为准；响应字段为 model_allowed（请求体才是 model_consent）
    try {
      const c = await getConsent();
      this.setData({ modelConsent: !!c.model_allowed });
    } catch (e) {}
  },

  async onToggleModel() {
    const target = !this.data.modelConsent;
    const version = this.data.policies.version;
    if (!version) { notify.error('隐私政策未就绪'); return; }
    if (!target) {
      const ok = await confirm({
        title: '撤回模型授权？',
        content: '撤回后将切换到纯本地模式，识别与建议能力受限，但基础记录不受影响。该限制随流程保存，重启后不会自动放宽。',
        confirmText: '确认撤回', destructive: true,
      });
      if (!ok) return;
    }
    this.setData({ saving: true });
    try {
      await updateConsent(target, version);
      this.setData({ modelConsent: target });
      notify.success(target ? '已同意模型处理' : '已撤回，切换纯本地模式');
    } catch (e) { showApiError(e); }
    finally { this.setData({ saving: false }); }
  },

  async onCloseAccount() {
    const ok = await confirm({
      title: '注销账号',
      content: '此操作不可逆。将立即撤销所有凭证并清理服务端数据，仅保留最小注销清单。确认继续？',
      confirmText: '继续注销', destructive: true,
    });
    if (!ok) return;
    this.setData({ saving: true });
    try {
      const res = await closeAccount();
      notify.success(res.state === 'completed' ? '账号已注销' : '注销已受理，数据清理中');
      setTimeout(() => wx.reLaunch({ url: '/pages/login/login' }), 900);
    } catch (e) {
      this.setData({ saving: false });
      showApiError(e);
    }
  },
});
