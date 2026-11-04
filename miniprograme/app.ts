// app.ts — 轻记小程序入口
// globalData 契约需满足 PyUI metrics.js / navbar.js：tabBarData.tabBar[0].pagePath、pagesData(路由→标题)
import { ENV } from './runtime/environment';
import { TAB_SLOTS, PAGES_DATA } from './runtime/routes';

App({
  globalData: {
    // PyUI navbar 首页路径契约：tabBar[0].pagePath 作为 indexPath（不显示"首页"胶囊）
    tabBarData: { tabBar: TAB_SLOTS },
    pagesData: PAGES_DATA,
    // 登录态
    token: '' as string,
    userId: '' as string,
    modelConsent: false,
    processingMode: 'local_only' as 'model_allowed' | 'local_only',
    // 隐私政策版本（登录后由 /api/policies 刷新）
    privacyVersion: '',
    apiOrigin: ENV.apiOrigin,
  },

  onLaunch() {
    // 恢复本地登录态（token 持久化，冷启动免登录）
    try {
      const token = wx.getStorageSync('qj_token');
      const userId = wx.getStorageSync('qj_user_id');
      const mode = wx.getStorageSync('qj_mode');
      if (token) {
        this.globalData.token = token;
        this.globalData.userId = userId || '';
        this.globalData.processingMode = mode || 'local_only';
      }
    } catch (e) {
      // 存储读取失败不阻断启动，走登录页
    }
  },
});
