export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly preview?: import("./types").DeletionPreview;

  constructor(status: number, message: string, code?: string, preview?: import("./types").DeletionPreview) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.preview = preview;
  }
}

export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch("/api" + path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "无法连接本地服务" }));
    const detail =
      typeof error.detail === "string"
        ? error.detail
        : Array.isArray(error.detail)
          ? error.detail.map((item: { msg: string }) => item.msg).join("；")
          : "请求失败";
    throw new ApiError(response.status, detail, error.code, error.preview);
  }
  return response.json();
}
export const requestId = () => crypto.randomUUID();
