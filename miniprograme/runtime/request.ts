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
  try {
    clearToken();
    const pages = getCurrentPages();
    const cur = pages.length ? pages[pages.length - 1].route : '';
    if (cur !== 'pages/login/login') {
      wx.reLaunch({ url: '/pages/login/login' });
    }
  } catch (e) {}
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
        if (sc === 401 || sc === 403) {
          toLogin();
          reject(new ApiError(sc, 'unauthorized', '登录状态已失效，请重新登录', res.data));
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
