import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api";

const headerId = "2de982e0-7bdc-4e8b-9f7d-af035a6e1ae1";
const bodyId = "9f4ec0de-f3b6-4c32-ab8a-171883ba27ec";
afterEach(() => vi.unstubAllGlobals());

describe("API error contract", () => {
  it("preserves the business payload and prefers the response correlation header", async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: "模型暂不可用",
          code: "model_unavailable",
          request_id: bodyId,
        }),
        { status: 503, headers: { "X-Request-ID": headerId } },
      ),
    );
    vi.stubGlobal("fetch", fetch);
    const payload = { request_id: "business-id", text: "用户内容" };
    const error = await api<never>("/chat", "POST", payload).catch((e: ApiError) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 503,
      code: "model_unavailable",
      requestId: headerId,
    });
    expect(error.message).toContain(headerId);
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual(payload);
  });

  it("uses a valid body correlation ID if no valid header exists", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: "冲突",
            code: "resource_version_conflict",
            request_id: bodyId,
          }),
          { status: 409, headers: { "X-Request-ID": "untrusted-value" } },
        ),
      ),
    );
    await expect(api("/chat")).rejects.toMatchObject({ requestId: bodyId });
  });

  it.each([
    null,
    { detail: [null, { msg: "类型不合法" }] },
    { detail: 42 },
    { request_id: "health-text" },
  ])(
    "handles malformed and legacy error bodies without echoing invalid IDs",
    async (body) => {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(
            new Response(JSON.stringify(body), { status: 422 }),
          ),
      );
      const error = await api<never>("/chat").catch((e: ApiError) => e);
      expect(error).toBeInstanceOf(ApiError);
      expect(error.requestId).toBeUndefined();
      expect(error.message).not.toContain("health-text");
    },
  );

  it("handles a non-JSON proxy response", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(new Response("upstream-private", { status: 502 })),
    );
    await expect(api("/chat")).rejects.toMatchObject({
      message: "服务响应异常，请稍后重试",
      status: 502,
    });
  });

  it("handles connection failure without inventing a correlation ID", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("private-network-error")),
    );
    await expect(api("/chat")).rejects.toMatchObject({
      message: "无法连接本地服务",
      status: 0,
      requestId: undefined,
    });
  });

  it("retains deletion preview metadata", async () => {
    const preview = { preview_id: "preview", scope: "dependencies" };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: "范围变化",
            code: "scope_changed",
            preview,
          }),
          { status: 409 },
        ),
      ),
    );
    await expect(api("/deletions", "POST")).rejects.toMatchObject({
      code: "scope_changed",
      preview,
    });
  });
});
