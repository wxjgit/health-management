import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api";

const platform = vi.hoisted(() => ({
  request: vi.fn(),
  getStorageSync: vi.fn(),
}));
vi.mock("@tarojs/taro", () => ({ default: platform }));
beforeEach(() => {
  vi.clearAllMocks();
  platform.getStorageSync.mockReturnValue("https://api.example.test");
});

describe("小程序请求协议", () => {
  it("使用配置的绝对地址并原样保留业务编号和本地处理限制", async () => {
    const body = {
      request_id: "original-request",
      text: "今天体重65公斤",
      processing_mode: "local_only",
    };
    platform.request.mockResolvedValue({
      statusCode: 200,
      data: { saved: true },
      header: {},
    });
    expect(await api("/chat", "POST", body)).toEqual({ saved: true });
    expect(platform.request).toHaveBeenCalledWith(
      expect.objectContaining({
        url: "https://api.example.test/api/chat",
        data: body,
        timeout: 125000,
      }),
    );
  });
  it("网络异常不自动重试或编造请求编号", async () => {
    platform.request.mockRejectedValue(new Error("network internals"));
    await expect(api("/chat", "POST", {})).rejects.toMatchObject({ status: 0 });
    expect(platform.request).toHaveBeenCalledTimes(1);
  });
  it("优先采用合法响应关联编号并保留删除范围", async () => {
    const preview = {
      preview_id: "new-preview",
      target_type: "event",
      target_id: "event",
      counts: { "event:delete": 1 },
      scope: "dependencies",
      reasons: [],
    };
    platform.request.mockResolvedValue({
      statusCode: 409,
      data: {
        detail: "范围已变化",
        code: "preview_changed",
        preview,
        request_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      },
      header: { "X-Request-ID": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb" },
    });
    const error = await api("/deletions", "POST", {}).catch((reason) => reason);
    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) throw new Error("expected ApiError");
    expect(error.preview).toEqual(preview);
    expect(error.message).toContain("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb");
    expect(error.message).not.toContain("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
  });
  it("代理返回非 JSON 时给出通用错误", async () => {
    platform.request.mockResolvedValue({
      statusCode: 504,
      data: "<html>private upstream</html>",
      header: {},
    });
    await expect(api("/chat", "POST", {})).rejects.toThrow("请求失败");
    expect(platform.request).toHaveBeenCalledTimes(1);
  });
});
