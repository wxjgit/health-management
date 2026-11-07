// runtime/request.ts — 统一请求层
// 职责：拼 origin、带 Bearer token、生成/复用 request_id（幂等）、
//      离线检测、401 统一跳登录、业务错误统一抛结构化 ApiError。
import { ENV } from './environment';

export class ApiError extends Error {
  status: number;
  code: string;
  detail: any;
  constructor(status: number, code: string, message: string, detail?: any) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export interface RequestOptions {
  path: string;
  method?: Method;
  data?: any;
  // 写入类请求的幂等键：网络失败重试复用同一 id，服务端按 id 去重
  requestId?: string;
  // 是否需要登录态（默认 true；/api/auth/* 与 /api/policies 传 false）
  auth?: boolean;
  timeout?: number;
  // 内部：401 静默重登后的重试标记（防无限重试）
  _retried?: boolean;
}

let cachedToken = '';
export function setToken(t: string) { cachedToken = t; try { wx.setStorageSync('qj_token', t); } catch (e) {} }
export function getToken(): string {
  if (cachedToken) return cachedToken;
  try { cachedToken = wx.getStorageSync('qj_token') || ''; } catch (e) { cachedToken = ''; }
  return cachedToken;
}
export function clearToken() {
  cachedToken = '';
  try { wx.removeStorageSync('qj_token'); wx.removeStorageSync('qj_user_id'); } catch (e) {}
  // 同步清 globalData：否则登录页 isLoggedIn() 读到残留 token 会 reLaunch 回主页，
  // 与 401→toLogin 形成 login⇄home 乒乓循环（请求风暴）
  try {
    const g: any = getApp().globalData;
    g.token = ''; g.userId = '';
  } catch (e) {}
}

// 生成符合后端约束的 request_id（min_length=8）
export function genRequestId(prefix = 'req'): string {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

// 网络是否可用
export function isOnline(): Promise<boolean> {
  return new Promise((resolve) => {
    try {
      wx.getNetworkType({ success: (r) => resolve(r.networkType !== 'none'), fail: () => resolve(true) });
    } catch (e) { resolve(true); }
  });
}

function toLogin() {
  // 防抖：并发 401 风暴只跳一次，避免重复 reLaunch 打断页面栈
  const now = Date.now();
  if (now - lastLoginJump < 3000) return;
  lastLoginJump = now;
  try {
    clearToken();
    const pages = getCurrentPages();
    const cur = pages.length ? pages[pages.length - 1].route : '';
    if (cur !== 'pages/login/login') {
      wx.reLaunch({ url: '/pages/login/login' });
    }
  } catch (e) {}
}
let lastLoginJump = 0;

// 静默重登：wx.login 换 code → 后端换新 token（沿用既有授权状态）。
// 成功返回新 token；任何环节失败返回 null（调用方回落到跳登录页）。
let relogging: Promise<string | null> | null = null;
function silentRelogin(): Promise<string | null> {
  if (relogging) return relogging;   // 并发 401 共用同一次重登
  relogging = new Promise<string | null>((resolve) => {
    // 合规闸：静默续期仅限"本机曾显式同意隐私政策"的用户（会话过期场景）。
    // 分享链接在新设备打开时没有该标记 → 必须走登录页显式同意，不能代用户静默同意。
    // 会话闸：当前须确有凭据（冷启动带 token 或本会话登录过）；退出登录后凭据已清 → 不静默登回。
    let consented = false;
    try { consented = wx.getStorageSync('qj_consented') === '1'; } catch (e) {}
    if (!consented || !getToken()) { resolve(null); return; }
    const g: any = getApp().globalData;
    const doLogin = (version: string) => {
      if (!version) { resolve(null); return; }
      g.privacyVersion = version;
      wx.login({
        success: (r) => {
          if (!r.code) { resolve(null); return; }
          wx.request({
            url: ENV.apiOrigin + '/api/auth/wechat/login',
            method: 'POST',
            header: { 'Content-Type': 'application/json' },
            data: {
              privacy_version: version, privacy_consent: true,
              health_consent: true, model_consent: !!g.modelConsent, code: r.code,
            },
            success: (res: any) => {
              const tok = res && res.statusCode === 200 && res.data && res.data.access_token;
              if (tok) {
                setToken(tok);
                try {
                  g.userId = (res.data.user && res.data.user.id) || '';
                  wx.setStorageSync('qj_user_id', g.userId);
                } catch (e) {}
                resolve(tok);
              } else resolve(null);
            },
            fail: () => resolve(null),
          });
        },
        fail: () => resolve(null),
      });
    };
    try {
      if (g.privacyVersion) { doLogin(g.privacyVersion); return; }
      // 冷启动带 token 直入主页时 privacyVersion 未加载：先取政策版本（无需登录）
      wx.request({
        url: ENV.apiOrigin + '/api/policies', method: 'GET',
        success: (res: any) => doLogin(res && res.statusCode === 200 && res.data ? res.data.version : ''),
        fail: () => resolve(null),
      });
    } catch (e) { resolve(null); }
  });
  relogging.then(() => { relogging = null; });
  return relogging;
}

export function request<T = any>(opt: RequestOptions): Promise<T> {
  const method = opt.method || 'GET';
  const auth = opt.auth !== false;
  const url = ENV.apiOrigin + opt.path;

  return new Promise<T>((resolve, reject) => {
    const header: Record<string, string> = { 'Content-Type': 'application/json' };
    if (auth) {
      const t = getToken();
      if (t) header['Authorization'] = 'Bearer ' + t;
    }
    // 幂等键随请求体透传（后端 ChatRequest/ReviewRequest/PlanRequest 等要求 request_id 字段）
    let data = opt.data;
    if (opt.requestId && data && typeof data === 'object' && !('request_id' in data)) {
      data = { ...data, request_id: opt.requestId };
    }

    // 微信 wx.request 官方类型不含 PATCH（真机亦不支持）。
    // 本地开发者工具联调可透传 PATCH；若真机/网关拒绝，改用 POST + X-HTTP-Method-Override，
    // 需后端中间件识别该头（FastAPI 默认不识别，真机联调前需补一层 override）。
    const wxMethod = method as any;
    if (method === 'PATCH') {
      header['X-HTTP-Method-Override'] = 'PATCH';
    }

    wx.request({
      url, method: wxMethod, data, header,
      timeout: opt.timeout || 15000,
      success: (res) => {
        const sc = res.statusCode;
        if (sc >= 200 && sc < 300) {
          resolve(res.data as T);
          return;
        }
        if (sc === 401) {
          // 会话过期：静默重登一次并重试原请求；失败才跳登录页。
          // 403 不重登（权限/同意问题，重登无意义）。
          if (auth && !opt._retried) {
            silentRelogin().then((tok) => {
              if (tok) {
                request<T>({ ...opt, _retried: true }).then(resolve, reject);
              } else {
                toLogin();
                reject(new ApiError(sc, 'unauthorized', '登录状态已失效，请重新登录', res.data));
              }
            });
            return;
          }
          toLogin();
          reject(new ApiError(sc, 'unauthorized', '登录状态已失效，请重新登录', res.data));
          return;
        }
        if (sc === 403) {
          // 403 = 权限/同意问题（如 model_consent_required），非会话过期：不重登不跳页，
          // 交由调用方按业务错误提示
          const body3: any = res.data || {};
          reject(new ApiError(sc, String(body3.code || 'forbidden'),
            typeof body3.detail === 'string' ? body3.detail : '权限不足', body3));
          return;
        }
        const body: any = res.data || {};
        const code = body.code || body.error || ('http_' + sc);
        const msg = body.detail || body.message || ('请求失败（' + sc + '）');
        reject(new ApiError(sc, String(code), typeof msg === 'string' ? msg : '请求失败', body));
      },
      fail: (err) => {
        // 网络失败：区分离线与超时，交由上层决定排队/重试
        const offline = !err || /fail|timeout|network/i.test(String(err.errMsg || ''));
        reject(new ApiError(0, offline ? 'network' : 'unknown', '网络异常，请检查连接后重试', err));
      },
    });
  });
}

export const http = {
  get: <T = any>(path: string, opt?: Partial<RequestOptions>) => request<T>({ path, method: 'GET', ...opt }),
  post: <T = any>(path: string, data?: any, opt?: Partial<RequestOptions>) => request<T>({ path, method: 'POST', data, ...opt }),
  put: <T = any>(path: string, data?: any, opt?: Partial<RequestOptions>) => request<T>({ path, method: 'PUT', data, ...opt }),
  patch: <T = any>(path: string, data?: any, opt?: Partial<RequestOptions>) => request<T>({ path, method: 'PATCH', data, ...opt }),
  del: <T = any>(path: string, data?: any, opt?: Partial<RequestOptions>) => request<T>({ path, method: 'DELETE', data, ...opt }),
};
