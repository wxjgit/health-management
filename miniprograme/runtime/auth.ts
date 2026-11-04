// runtime/auth.ts — 登录态与授权管理
// 封装 wx.login → code2session（POST /api/auth/wechat/login），token 持久化，
// 隐私/模型授权（GET/PUT /api/auth/consent），当前用户与用量。
import { http, setToken, clearToken, ApiError } from './request';

export interface LoginResult {
  // 后端 issue() 真实字段：access_token（非 token）
  access_token: string;
  token_type: string;
  expires_at: number;
  user: { id: string; [k: string]: any };
  consent?: ConsentState;
}
export interface ConsentState {
  // 后端 consent() 真实字段：version / privacy_accepted / model_allowed
  version: string;
  privacy_accepted: boolean;
  model_allowed: boolean;
  [k: string]: any;
}

const app = () => getApp();

// 读取隐私政策（无需登录）
export function fetchPolicies(): Promise<any> {
  return http.get('/api/policies', { auth: false });
}

// 微信登录：wx.login 拿 code → 后端换 token
export function wechatLogin(params: {
  privacyVersion: string;
  modelConsent: boolean;
}): Promise<LoginResult> {
  return new Promise((resolve, reject) => {
    wx.login({
      success: (r) => {
        if (!r.code) { reject(new ApiError(0, 'no_code', '微信登录失败：未获取到 code')); return; }
        http.post<LoginResult>('/api/auth/wechat/login', {
          privacy_version: params.privacyVersion,
          privacy_consent: true,
          health_consent: true,
          model_consent: params.modelConsent,
          code: r.code,
        }, { auth: false }).then((res) => {
          // 后端返回 access_token（非 token）；consent 用 model_allowed（非 model_consent）
          setToken(res.access_token);
          const g = app().globalData;
          g.token = res.access_token;
          g.userId = res.user?.id || '';
          g.modelConsent = !!(res.consent && res.consent.model_allowed);
          g.processingMode = g.modelConsent ? 'model_allowed' : 'local_only';
          try {
            wx.setStorageSync('qj_user_id', g.userId);
            wx.setStorageSync('qj_mode', g.processingMode);
          } catch (e) {}
          resolve(res);
        }).catch(reject);
      },
      fail: () => reject(new ApiError(0, 'wx_login_fail', '微信登录被取消')),
    });
  });
}

export function isLoggedIn(): boolean {
  return !!app().globalData.token;
}

export function currentMode(): 'model_allowed' | 'local_only' {
  return app().globalData.processingMode || 'local_only';
}

export async function logout(): Promise<void> {
  try { await http.post('/api/auth/logout'); } catch (e) { /* 忽略：本地仍清理 */ }
  clearToken();
  const g = app().globalData;
  g.token = ''; g.userId = ''; g.modelConsent = false; g.processingMode = 'local_only';
}

export function getConsent(): Promise<ConsentState> {
  return http.get('/api/auth/consent');
}

export async function updateConsent(modelConsent: boolean, privacyVersion: string): Promise<ConsentState> {
  const res = await http.put<ConsentState>('/api/auth/consent', {
    privacy_version: privacyVersion,
    privacy_consent: true,
    health_consent: true,
    model_consent: modelConsent,
  });
  const g = app().globalData;
  g.modelConsent = modelConsent;
  g.processingMode = modelConsent ? 'model_allowed' : 'local_only';
  try { wx.setStorageSync('qj_mode', g.processingMode); } catch (e) {}
  return res;
}

// 注销账号（不可逆）
export function closeAccount(): Promise<{ state: string }> {
  return http.del('/api/auth/account', { confirmation: '注销账号' });
}

export function getMe(): Promise<{ user: any }> {
  return http.get('/api/auth/me');
}
export function getUsage(): Promise<any> {
  return http.get('/api/auth/usage');
}
