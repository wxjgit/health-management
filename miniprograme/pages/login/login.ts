// pages/login/login.ts — 登录 / 隐私授权页（Immersive 层，无 tabbar）
import { fetchPolicies, wechatLogin, isLoggedIn } from '../../runtime/auth';
import { notify, showApiError } from '../../runtime/feedback';
import { homeTabUrl } from '../../runtime/routes';

Page({
  data: {
    policies: { privacy: [] as string[], model_provider: '', model_name: '', version: '' },
    versionShort: '',
    privacyAgreed: false,
    modelAgreed: false,
    loading: false,
  },

  onLoad() {
    // 已登录直接进入记录页
    if (isLoggedIn()) {
      wx.reLaunch({ url: homeTabUrl(0) });
      return;
    }
    this.loadPolicies();
  },

  async loadPolicies() {
    try {
      const p = await fetchPolicies();
      this.setData({
        policies: p,
        versionShort: p.version ? String(p.version).slice(0, 8) : '',
      });
      getApp().globalData.privacyVersion = p.version || '';
    } catch (e) {
      // 政策拉取失败不阻断：允许用户查看后登录时再校验版本
      notify.warn('隐私政策加载失败，请检查后端连接');
    }
  },

  togglePrivacy() {
    this.setData({ privacyAgreed: !this.data.privacyAgreed });
  },
  toggleModel() {
    this.setData({ modelAgreed: !this.data.modelAgreed });
  },

  goConsent() {
    // 未登录也能查看完整政策（consent 页做只读降级）
    wx.navigateTo({ url: '/pages/consent/consent?readonly=1' });
  },

  async onLogin() {
    if (!this.data.privacyAgreed) {
      notify.warn('请先阅读并同意隐私政策');
      return;
    }
    const version = this.data.policies.version || getApp().globalData.privacyVersion;
    if (!version) {
      notify.error('隐私政策尚未就绪，请稍后重试');
      this.loadPolicies();
      return;
    }
    this.setData({ loading: true });
    try {
      await wechatLogin({ privacyVersion: version, modelConsent: this.data.modelAgreed });
      notify.success('登录成功');
      wx.reLaunch({ url: homeTabUrl(0) });
    } catch (e: any) {
      showApiError(e);
      this.setData({ loading: false });
    }
  },
});
