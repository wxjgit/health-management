import Taro from "@tarojs/taro";
import type { DeletionPreview } from "./types";

export type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
export const baseUrl = () =>
  Taro.getStorageSync<string>("api-base") || "http://127.0.0.1:8000";
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
  let response: Taro.request.SuccessCallbackResult;
  try {
    response = await Taro.request({
      url: `${baseUrl()}/api${path}`,
      method,
      data,
      timeout: 125000,
      header: { "Content-Type": "application/json" },
    });
  } catch {
    throw new ApiError(
      0,
      "无法连接服务或请求超时，请检查地址；操作结果可能尚未返回，请刷新或使用原请求重试。",
    );
  }
  if (response.statusCode < 200 || response.statusCode >= 300) {
    const payload =
      response.data && typeof response.data === "object"
        ? (response.data as Record<string, unknown>)
        : {};
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
