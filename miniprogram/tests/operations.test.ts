import { beforeEach, expect, it, vi } from "vitest";

const platform = vi.hoisted(() => ({
  storage: new Map<string, unknown>(),
  request: vi.fn(),
  unlink: vi.fn(),
}));
vi.mock("@tarojs/taro", () => ({
  default: {
    request: platform.request,
    getStorageSync: (key: string) => platform.storage.get(key),
    setStorageSync: (key: string, value: unknown) =>
      platform.storage.set(key, value),
    removeStorageSync: (key: string) => platform.storage.delete(key),
    env: { USER_DATA_PATH: "/app" },
    getFileSystemManager: () => ({ unlink: platform.unlink }),
  },
  useDidShow: vi.fn(),
  useDidHide: vi.fn(),
}));
beforeEach(() => {
  platform.storage.clear();
  platform.request.mockReset();
  platform.unlink.mockReset();
  vi.resetModules();
});
const success = (data: unknown = {}) => ({ statusCode: 200, data, header: {} });

it("首期版本待发请求迁移后保留原编号与参数", async () => {
  const state = await import("../src/state");
  const previous = {
    path: "/chat",
    body: {
      request_id: "p0-original",
      text: "今天体重65公斤",
      processing_mode: "local_only",
    },
  };
  platform.storage.set("pending-submission", previous);
  expect(state.pendingOperation()).toMatchObject(previous);
  expect(platform.storage.has("pending-submission")).toBe(false);
});

it("超时后持久化原参数，重启后重试仍使用同一业务编号", async () => {
  let state = await import("../src/state");
  const original = state.operation(
    "/chat",
    {
      request_id: "original",
      text: "今天体重65公斤",
      processing_mode: "local_only",
    },
    "记录",
  );
  platform.request.mockRejectedValueOnce(new Error("timeout"));
  await expect(state.executeOperation(original)).rejects.toThrow();
  vi.resetModules();
  state = await import("../src/state");
  expect(state.pendingOperation()).toEqual(original);
  platform.request.mockResolvedValueOnce(success());
  await state.executeOperation(state.pendingOperation()!);
  expect(platform.request.mock.calls[1][0].data).toEqual(original.body);
  expect(state.pendingOperation()).toBeNull();
});
it("不允许其他请求覆盖待核对请求", async () => {
  const state = await import("../src/state");
  platform.request.mockRejectedValue(new Error("timeout"));
  const original = state.operation(
    "/chat",
    { request_id: "original" },
    "原记录",
  );
  await expect(state.executeOperation(original)).rejects.toThrow();
  await expect(
    state.executeOperation(
      state.operation("/chat", { request_id: "new" }, "新记录"),
    ),
  ).rejects.toThrow("原请求");
  expect(platform.request).toHaveBeenCalledTimes(1);
});
it("并发点击只发送一个请求", async () => {
  const state = await import("../src/state");
  let finish!: (value: unknown) => void;
  platform.request.mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  const original = state.operation("/chat", { request_id: "same" }, "记录");
  const first = state.executeOperation(original);
  await expect(state.executeOperation(original)).rejects.toThrow("正在执行");
  finish(success());
  await first;
  expect(platform.request).toHaveBeenCalledTimes(1);
});
it("切换服务不会把旧请求发送到新服务", async () => {
  const state = await import("../src/state");
  const original = state.operation("/chat", { request_id: "same" }, "记录");
  platform.storage.set("api-base", "https://another.test");
  await expect(state.executeOperation(original)).rejects.toThrow("另一服务");
  expect(platform.request).not.toHaveBeenCalled();
});
it("纯本地偏好不会被恢复的模型请求绕过", async () => {
  const state = await import("../src/state");
  await expect(
    state.executeOperation(
      state.operation(
        "/workflows/thread/resume",
        { request_id: "same", processing_mode: "model_allowed" },
        "恢复",
      ),
    ),
  ).rejects.toThrow("模型模式");
  expect(platform.request).not.toHaveBeenCalled();
});
it("服务端强制纯本地也禁止重发模型请求", async () => {
  const state = await import("../src/state");
  platform.storage.set("local-only", false);
  platform.request.mockResolvedValue(success({ local_only: true }));
  await expect(
    state.executeOperation(
      state.operation(
        "/workflows/thread/resume",
        { request_id: "same", processing_mode: "model_allowed" },
        "恢复",
      ),
    ),
  ).rejects.toThrow("强制纯本地");
  expect(platform.request).toHaveBeenCalledTimes(1);
});
it("计划恢复保留上次拒绝选择与 interrupt_id", async () => {
  const state = await import("../src/state");
  const body = {
    request_id: "previous-decision",
    interrupt_id: "current-interrupt",
    approved: false,
  };
  platform.request.mockResolvedValue(success());
  await state.executeOperation(
    state.operation("/workflows/thread/resume", body, "恢复确认"),
  );
  expect(platform.request.mock.calls[0][0].data).toEqual(body);
});
it("校验失败允许修改输入，执行冲突则继续保留原请求", async () => {
  const state = await import("../src/state");
  const original = state.operation("/chat", { request_id: "same" }, "记录");
  platform.request.mockResolvedValueOnce({
    statusCode: 422,
    data: { detail: "参数不合法" },
    header: {},
  });
  await expect(state.executeOperation(original)).rejects.toThrow();
  expect(state.pendingOperation()).toBeNull();
  platform.request.mockResolvedValueOnce({
    statusCode: 409,
    data: { detail: "正在执行" },
    header: {},
  });
  await expect(state.executeOperation(original)).rejects.toThrow();
  expect(state.pendingOperation()).toEqual(original);
});
it("删除前后失效私有缓存，并持久化未完成作业以便重启恢复", async () => {
  const state = await import("../src/state");
  const preview = {
    preview_id: "preview",
    target_id: "event",
    target_type: "event",
    counts: {},
    scope: "dependencies",
    reasons: [],
  };
  platform.storage.set("deletion-state", {
    base: "http://127.0.0.1:8000",
    preview,
    requestId: "erase",
  });
  platform.storage.set("correction-event", { raw_text: "private" });
  const result = {
    ...preview,
    deletion_id: "job",
    state: "pending",
    deleted: false,
  };
  platform.request.mockResolvedValue(success(result));
  const epoch = state.privacyGeneration();
  await state.executeOperation(
    state.operation(
      "/deletions",
      { request_id: "erase", preview_id: "preview" },
      "删除",
    ),
  );
  expect(state.privacyGeneration()).toBeGreaterThan(epoch);
  expect(platform.storage.has("correction-event")).toBe(false);
  expect(platform.storage.get("deletion-state")).toMatchObject({
    result: { deletion_id: "job", state: "pending" },
  });
});
it("范围改变后保存新预览，旧删除请求不再自动重试", async () => {
  const state = await import("../src/state");
  const preview = {
    preview_id: "new",
    target_id: "event",
    target_type: "event",
    counts: { "event:delete": 2 },
    scope: "dependencies",
    reasons: [],
  };
  platform.storage.set("deletion-state", {
    base: "http://127.0.0.1:8000",
    preview: { ...preview, preview_id: "old" },
    requestId: "erase",
  });
  platform.request.mockResolvedValue({
    statusCode: 409,
    data: { detail: "范围变化", preview },
    header: {},
  });
  await expect(
    state.executeOperation(
      state.operation(
        "/deletions",
        { request_id: "erase", preview_id: "old" },
        "删除",
      ),
    ),
  ).rejects.toThrow();
  expect(state.pendingOperation()).toBeNull();
  expect(platform.storage.get("deletion-state")).toMatchObject({
    preview,
    requestId: "",
  });
});
