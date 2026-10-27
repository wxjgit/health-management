export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  // Server correlation for this HTTP attempt; never use as a business request_id.
  readonly requestId?: string;
  readonly preview?: import("./types").DeletionPreview;

  constructor(
    status: number,
    message: string,
    code?: string,
    preview?: import("./types").DeletionPreview,
    requestId?: string,
  ) {
    super(requestId ? `${message}（请求编号：${requestId}）` : message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.preview = preview;
  }
}

function correlationId(value: unknown): string | undefined {
  return typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      value,
    )
    ? value
    : undefined;
}

export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const serialized = body === undefined ? undefined : JSON.stringify(body);
  let response: Response;
  try {
    response = await fetch("/api" + path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: serialized,
    });
  } catch {
    throw new ApiError(0, "无法连接本地服务");
  }
  if (!response.ok) {
    const parsed = await response
      .json()
      .catch(() => ({ detail: "服务响应异常，请稍后重试" }));
    const error = parsed && typeof parsed === "object" ? parsed : {};
    const detail =
      typeof error.detail === "string"
        ? error.detail
        : Array.isArray(error.detail)
          ? error.detail
              .filter(
                (item: unknown): item is { msg: string } =>
                  !!item &&
                  typeof item === "object" &&
                  "msg" in item &&
                  typeof item.msg === "string",
              )
              .map((item: { msg: string }) => item.msg)
              .join("；") || "请求参数不合法"
          : "请求失败";
    const id =
      correlationId(response.headers.get("X-Request-ID")) ??
      correlationId(error.request_id);
    throw new ApiError(
      response.status,
      detail,
      typeof error.code === "string" ? error.code : undefined,
      error.preview,
      id,
    );
  }
  return response.json();
}
export const requestId = () => crypto.randomUUID();
