import Taro from "@tarojs/taro";
import type { DeletionPreview } from "./types";

export type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
export const baseUrl = () => {
  const development =
    typeof DEVELOPMENT_BUILD === "undefined"
      ? process.env.NODE_ENV === "test"
      : DEVELOPMENT_BUILD;
  const configured = typeof API_ORIGIN === "undefined" ? "" : API_ORIGIN;
  if (development)
    return (
      Taro.getStorageSync<string>("api-base") ||
      configured ||
      "http://127.0.0.1:8000"
    );
  if (!configured.startsWith("https://"))
    throw new Error("正式服务地址未配置。");
  return configured;
};
export const accountKey = () => {
  const session = Taro.getStorageSync<{ base: string; user?: { id: string } }>(
    "auth-session",
  );
  return session?.base === baseUrl() ? session.user?.id || "" : "";
};
export const requestId = () =>
  `mini-${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
const correlationId = (value: unknown) =>
  typeof value === "string" &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
    ? value
    : undefined;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code?: string,
    readonly preview?: DeletionPreview,
  ) {
    super(message);
  }
}

export async function api<Result>(
  path: string,
  method: Method = "GET",
  data?: object,
): Promise<Result> {
  const origin = baseUrl();
  const session = Taro.getStorageSync<{ base: string; token: string }>(
    "auth-session",
  );
  const authenticated = ![
    "/auth/wechat/login",
    "/auth/status",
    "/health",
    "/policies",
  ].includes(path);
  let response: Taro.request.SuccessCallbackResult;
  try {
    response = await Taro.request({
      url: `${origin}/api${path}`,
      method,
      data,
      timeout: 125000,
      header: {
        "Content-Type": "application/json",
        ...(session?.base === origin
          ? { Authorization: `Bearer ${session.token}` }
          : {}),
      },
    });
  } catch {
    throw new ApiError(
      0,
      "无法连接服务或请求超时，请检查地址；操作结果可能尚未返回，请刷新或使用原请求重试。",
    );
  }
  const activeSession = Taro.getStorageSync<{ token: string }>("auth-session");
  if (baseUrl() !== origin || activeSession?.token !== session?.token) {
    throw new ApiError(
      409,
      "账号或服务已变化，请在当前账号下刷新。",
      "account_changed",
    );
  }
  if (response.statusCode < 200 || response.statusCode >= 300) {
    const currentSession = Taro.getStorageSync<{ base: string; token: string }>(
      "auth-session",
    );
    const sameSession =
      baseUrl() === origin &&
      (!currentSession ||
        (currentSession.base === session?.base &&
          currentSession.token === session?.token));
    if (
      response.statusCode === 401 &&
      sameSession &&
      session?.base === origin &&
      currentSession?.token === session.token
    ) {
      Taro.removeStorageSync("auth-session");
      Taro.eventCenter.trigger("auth-cleared");
    }
    if (response.statusCode === 401 && authenticated && sameSession) {
      const pages = Taro.getCurrentPages();
      if (pages[pages.length - 1]?.route !== "pages/login/index")
        void Taro.reLaunch({ url: "/pages/login/index" });
    }
    const payload =
      response.data && typeof response.data === "object"
        ? (response.data as Record<string, unknown>)
        : {};
    if (payload.code === "privacy_consent_required") {
      void Taro.reLaunch({ url: "/pages/consent/index" });
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
      payload.preview as DeletionPreview | undefined,
    );
  }
  return response.data as Result;
}

export const errorText = (error: unknown) =>
  error instanceof Error ? error.message : "操作失败，请重试";
export const queryString = (values: Record<string, string>) =>
  Object.entries(values)
    .filter(([, value]) => value)
    .map(
      ([key, value]) =>
        `${encodeURIComponent(key)}=${encodeURIComponent(value)}`,
    )
    .join("&");
