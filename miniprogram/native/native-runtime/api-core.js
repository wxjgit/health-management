"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.createApi = createApi;
/** Shared request/identity protocol for all native pages. */
function createApi(platform, configuration) {
  const baseUrl = () => {
    const development = configuration.development;
    const configured = configuration.apiOrigin;
    if (development)
      return (
        platform.getStorageSync("api-base") ||
        configured ||
        "http://127.0.0.1:8000"
      );
    if (!configured.startsWith("https://"))
      throw new Error("正式服务地址未配置。");
    return configured;
  };
  const accountKey = () => {
    var _a;
    const session = platform.getStorageSync("auth-session");
    return (session === null || session === void 0 ? void 0 : session.base) ===
      baseUrl()
      ? ((_a = session.user) === null || _a === void 0 ? void 0 : _a.id) || ""
      : "";
  };
  const requestId = () =>
    `mini-${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
  const correlationId = (value) =>
    typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      value,
    )
      ? value
      : undefined;
  class ApiError extends Error {
    constructor(status, message, code, preview) {
      super(message);
      this.status = status;
      this.code = code;
      this.preview = preview;
    }
  }
  async function api(path, method = "GET", data) {
    var _a;
    const origin = baseUrl();
    const session = platform.getStorageSync("auth-session");
    const authenticated = ![
      "/auth/wechat/login",
      "/auth/status",
      "/health",
      "/policies",
    ].includes(path);
    let response;
    try {
      response = await platform.request({
        url: `${origin}/api${path}`,
        method,
        data,
        timeout: 125000,
        header: Object.assign(
          { "Content-Type": "application/json" },
          (session === null || session === void 0 ? void 0 : session.base) ===
            origin
            ? { Authorization: `Bearer ${session.token}` }
            : {},
        ),
      });
    } catch (_b) {
      throw new ApiError(
        0,
        "无法连接服务或请求超时，请检查地址；操作结果可能尚未返回，请刷新或使用原请求重试。",
      );
    }
    const activeSession = platform.getStorageSync("auth-session");
    if (
      baseUrl() !== origin ||
      (activeSession === null || activeSession === void 0
        ? void 0
        : activeSession.token) !==
        (session === null || session === void 0 ? void 0 : session.token)
    ) {
      throw new ApiError(
        409,
        "账号或服务已变化，请在当前账号下刷新。",
        "account_changed",
      );
    }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      const currentSession = platform.getStorageSync("auth-session");
      const sameSession =
        baseUrl() === origin &&
        (!currentSession ||
          (currentSession.base ===
            (session === null || session === void 0 ? void 0 : session.base) &&
            currentSession.token ===
              (session === null || session === void 0
                ? void 0
                : session.token)));
      if (
        response.statusCode === 401 &&
        sameSession &&
        (session === null || session === void 0 ? void 0 : session.base) ===
          origin &&
        (currentSession === null || currentSession === void 0
          ? void 0
          : currentSession.token) === session.token
      ) {
        platform.removeStorageSync("auth-session");
        platform.eventCenter.trigger("auth-cleared");
      }
      if (response.statusCode === 401 && authenticated && sameSession) {
        const pages = platform.getCurrentPages();
        if (
          ((_a = pages[pages.length - 1]) === null || _a === void 0
            ? void 0
            : _a.route) !== "pages/login/index"
        )
          void platform.reLaunch({ url: "/pages/login/index" });
      }
      const payload =
        response.data && typeof response.data === "object" ? response.data : {};
      if (payload.code === "privacy_consent_required") {
        void platform.reLaunch({ url: "/pages/consent/index" });
      }
      const identifier =
        correlationId(
          response.header["X-Request-ID"] || response.header["x-request-id"],
        ) || correlationId(payload.request_id);
      const detail =
        typeof payload.detail === "string"
          ? payload.detail
          : "请求失败，请检查输入或稍后重试";
      throw new ApiError(
        response.statusCode,
        `${detail}${identifier ? `（请求编号：${identifier}）` : ""}`,
        typeof payload.code === "string" ? payload.code : undefined,
        payload.preview,
      );
    }
    return response.data;
  }
  const errorText = (error) =>
    error instanceof Error ? error.message : "操作失败，请重试";
  const queryString = (values) =>
    Object.entries(values)
      .filter(([, value]) => value)
      .map(
        ([key, value]) =>
          `${encodeURIComponent(key)}=${encodeURIComponent(value)}`,
      )
      .join("&");
  return {
    api,
    ApiError,
    baseUrl,
    accountKey,
    requestId,
    errorText,
    queryString,
  };
}
