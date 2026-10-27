import React, { useEffect } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const platform = vi.hoisted(() => ({
  storage: new Map<string, unknown>(),
  params: {} as Record<string, string>,
  showModal: vi.fn(),
  api: vi.fn(),
}));
vi.mock("@tarojs/taro", () => ({
  default: {
    getStorageSync: (key: string) => platform.storage.get(key),
    setStorageSync: (key: string, value: unknown) =>
      platform.storage.set(key, value),
    removeStorageSync: (key: string) => platform.storage.delete(key),
    showModal: platform.showModal,
    pageScrollTo: vi.fn().mockResolvedValue({}),
    getCurrentInstance: () => ({ router: { params: platform.params } }),
    env: { USER_DATA_PATH: "/app" },
  },
  useDidShow: (callback: () => void) => useEffect(callback, []),
  useDidHide: () => {},
}));
vi.mock("@tarojs/components", () => ({
  View: "view",
  Text: "text",
  Button: "button",
  Input: "input",
  Textarea: "textarea",
  Switch: "switch",
  Picker: "picker",
}));
vi.mock("../src/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api")>()),
  api: platform.api,
}));

import Chat from "../src/pages/chat";
import Plans from "../src/pages/plans";
import Facts from "../src/pages/facts";
import Privacy from "../src/pages/privacy";
import { ApiError } from "../src/api";
import { clearPrivateState, useResource } from "../src/state";

let renderer: ReactTestRenderer;
const allText = () => JSON.stringify(renderer.toJSON());
const buttons = (text: string) =>
  renderer.root.findAll(
    (node) => node.type === "button" && node.children.join("") === text,
  );
const click = async (text: string) => {
  await act(async () => {
    await buttons(text)[0].props.onClick();
  });
};
const defaults = {
  status: "ready",
  targets: {
    kcal: 1800,
    protein_g: 90,
    carbs_g: 225,
    fat_g: 60,
    exercise_minutes: 150,
  },
  fingerprint: "estimate-v1",
  basis: "依据",
  warnings: [],
  sources: [],
  reason: "",
  formula_version: "v1",
};
const workflow = {
  thread_id: "thread",
  status: "waiting",
  reply: "",
  pending_input: {
    kind: "plan_confirmation",
    interrupt_id: "interrupt",
    targets: defaults.targets,
    question: "是否采用？",
  },
};
let rows: Record<string, unknown>;
beforeEach(() => {
  platform.storage.clear();
  platform.api.mockReset();
  platform.showModal.mockResolvedValue({ confirm: true });
  platform.params = {};
  rows = {
    "/plans": [],
    "/workflows": [],
    "/targets/default": defaults,
    "/messages": [],
    "/workflows/expired": [],
    "/health": {
      local_only: false,
      mode: "model",
      model_name: "model",
      model_provider: "provider",
      today: "2026-10-27",
    },
    "/dashboard": { totals: { kcal: null, unknown_items: 1 }, record_count: 0 },
    "/facts?include_inactive=true": [],
  };
  platform.api.mockImplementation(async (path: string, method = "GET") =>
    method === "GET" ? rows[path] : { reply: "已处理" },
  );
});
afterEach(() => {
  if (renderer) act(() => renderer.unmount());
});
async function mount(element: React.ReactElement) {
  await act(async () => {
    renderer = create(element);
  });
}

it("空状态示例仅填入，不发送；新记录明确采用纯本地模式", async () => {
  await mount(<Chat />);
  await click("今天早上体重65公斤");
  expect(
    platform.api.mock.calls.filter((call) => call[1] === "POST"),
  ).toHaveLength(0);
  await click("发送");
  expect(platform.api).toHaveBeenCalledWith(
    "/chat",
    "POST",
    expect.objectContaining({
      text: "今天早上体重65公斤",
      processing_mode: "local_only",
      conversation_id: "main",
    }),
  );
});
it("选择追问才绑定流程，并携带当前中断编号", async () => {
  rows["/workflows"] = [
    {
      ...workflow,
      pending_input: {
        kind: "clarification",
        question: "多少分钟？",
        interrupt_id: "current",
      },
    },
  ];
  await mount(<Chat />);
  await click("回答此追问");
  await act(async () => {
    renderer.root
      .findByType("textarea")
      .props.onInput({ detail: { value: "30分钟" } });
  });
  await click("发送");
  expect(platform.api).toHaveBeenCalledWith(
    "/workflows/thread/resume",
    "POST",
    expect.objectContaining({ text: "30分钟", interrupt_id: "current" }),
  );
});
it("自动默认目标保留估算版本，只有明确确认才采用计划", async () => {
  await mount(<Plans />);
  await click("生成或调整计划");
  expect(platform.api).toHaveBeenCalledWith(
    "/plans/propose",
    "POST",
    expect.objectContaining({
      targets: defaults.targets,
      target_source: "system_default",
      suggestion_fingerprint: "estimate-v1",
    }),
  );
  expect(
    platform.api.mock.calls.some((call) => call[0].endsWith("/resume")),
  ).toBe(false);
});
it("手动编辑后默认估算刷新不会覆盖未提交内容", async () => {
  await mount(<Plans />);
  await act(async () => {
    renderer.root
      .findAllByType("input")[0]
      .props.onInput({ detail: { value: "1900" } });
  });
  rows["/targets/default"] = {
    ...defaults,
    fingerprint: "estimate-v2",
    targets: { ...defaults.targets, kcal: 1700 },
  };
  await click("刷新状态");
  expect(renderer.root.findAllByType("input")[0].props.value).toBe("1900");
  await click("生成或调整计划");
  expect(platform.api).toHaveBeenCalledWith(
    "/plans/propose",
    "POST",
    expect.objectContaining({
      target_source: "manual",
      targets: expect.objectContaining({ kcal: 1900 }),
    }),
  );
});
it("业务计划已生效优先于尚未完成的流程状态", async () => {
  rows["/workflows"] = [{ ...workflow, status: "resuming" }];
  rows["/plans"] = [
    {
      id: "thread",
      status: "active",
      data: {
        targets: defaults.targets,
        schedule: [],
        evidence_refs: [],
        notes: "",
        next_meal: "",
        tomorrow_focus: "",
      },
    },
  ];
  await mount(<Plans />);
  expect(buttons("采用计划")).toHaveLength(0);
  expect(allText()).toContain("计划已生效");
});
it("恢复的拒绝计划沿用服务端原选择和请求编号", async () => {
  const original = {
    request_id: "old-decision",
    interrupt_id: "interrupt",
    approved: false,
  };
  rows["/workflows"] = [
    { ...workflow, status: "resume_failed", retry_request: original },
  ];
  await mount(<Plans />);
  expect(buttons("采用计划")).toHaveLength(0);
  await click("重试上次确认");
  expect(platform.api).toHaveBeenCalledWith(
    "/workflows/thread/resume",
    "POST",
    original,
  );
});
it("安全路由拒绝计划时显示后端回复", async () => {
  platform.api.mockImplementation(async (path: string, method = "GET") =>
    method === "GET"
      ? rows[path]
      : {
          reply: "当前情况需要专业评估，不生成通用计划。",
          safety: { category: "medical" },
        },
  );
  await mount(<Plans />);
  await click("生成或调整计划");
  expect(allText()).toContain("当前情况需要专业评估");
  expect(buttons("采用计划")).toHaveLength(0);
});
it("隐私删除失效正在读取的旧响应，旧内容不能恢复", async () => {
  let resolveOld!: (value: string) => void;
  const old = new Promise<string>((resolve) => {
    resolveOld = resolve;
  });
  function Probe() {
    const resource = useResource(() => old);
    return <span>{resource.data}</span>;
  }
  await mount(<Probe />);
  act(() => clearPrivateState());
  await act(async () => {
    resolveOld("已删除的健康原文");
    await old;
  });
  expect(allText()).not.toContain("已删除的健康原文");
});

it("删除结果未知时不会重新读取健康内容", async () => {
  platform.storage.set("pending-operation", {
    base: "http://127.0.0.1:8000",
    path: "/deletions",
    body: { request_id: "erase" },
    label: "删除",
  });
  const load = vi.fn().mockResolvedValue("清理前的旧内容");
  function Probe() {
    const resource = useResource<string>(load);
    return (
      <span>
        {resource.data}
        {resource.error}
      </span>
    );
  }
  await mount(<Probe />);
  expect(load).not.toHaveBeenCalled();
  expect(allText()).toContain("隐私清理结果尚待核对");
});
it("保护性事实冲突和临时截止未知不能一键确认", async () => {
  rows["/facts?include_inactive=true"] = [
    {
      id: "fact",
      content: "自述过敏",
      type: "allergy",
      confirmation: "conflict",
      enforcement: "avoid",
      time_scope: "persistent",
      lifecycle: "needs_review",
      version: 1,
      source_details: {},
    },
  ];
  await mount(<Facts />);
  expect(buttons("确认采用")[0].props.disabled).toBe(true);
});

const deletionPreview = {
  preview_id: "preview",
  target_type: "event" as const,
  target_id: "event",
  counts: { "event:delete": 1 },
  scope: "dependencies" as const,
  reasons: [],
};
it("删除先预览并由用户确认，取消确认不执行删除", async () => {
  platform.params = { type: "event", id: "event" };
  platform.api.mockImplementation(async (path: string) =>
    path === "/deletions/preview" ? deletionPreview : {},
  );
  await mount(<Privacy />);
  expect(platform.api).not.toHaveBeenCalled();
  await click("获取所选对象的最新影响范围");
  expect(allText()).toContain("健康记录（删除）");
  platform.showModal.mockResolvedValue({ confirm: false });
  await click("确认以上范围并删除");
  expect(
    platform.api.mock.calls.filter((call) => call[0] === "/deletions"),
  ).toHaveLength(0);
});
it("范围变化停止旧请求并要求再次确认新范围", async () => {
  platform.params = { type: "event", id: "event" };
  const nextPreview = {
    ...deletionPreview,
    preview_id: "new-preview",
    counts: { "event:delete": 2 },
  };
  platform.api.mockImplementation(async (path: string) => {
    if (path === "/deletions/preview") return deletionPreview;
    if (path === "/deletions")
      throw new ApiError(
        409,
        "范围变化，请重新确认",
        "preview_changed",
        nextPreview,
      );
    return {};
  });
  await mount(<Privacy />);
  await click("获取所选对象的最新影响范围");
  await click("确认以上范围并删除");
  expect(
    platform.api.mock.calls.filter((call) => call[0] === "/deletions"),
  ).toHaveLength(1);
  expect(platform.storage.get("deletion-state")).toMatchObject({
    preview: nextPreview,
  });
  expect(buttons("重试原请求")).toHaveLength(0);
  platform.api.mockResolvedValue({
    ...nextPreview,
    state: "completed",
    deletion_id: "job",
    deleted: true,
  });
  await click("确认以上范围并删除");
  expect(platform.api).toHaveBeenLastCalledWith(
    "/deletions",
    "POST",
    expect.objectContaining({ preview_id: "new-preview" }),
  );
  expect(allText()).toContain("清理已完成");
});
it("清理作业在重启后可查询并继续", async () => {
  platform.storage.set("deletion-state", {
    base: "http://127.0.0.1:8000",
    preview: deletionPreview,
    requestId: "erase",
    result: {
      ...deletionPreview,
      deletion_id: "job",
      state: "pending",
      deleted: false,
    },
  });
  platform.api.mockResolvedValue({
    ...deletionPreview,
    deletion_id: "job",
    state: "completed",
    deleted: true,
  });
  await mount(<Privacy />);
  await click("继续清理");
  expect(platform.api).toHaveBeenCalledWith(
    "/deletions/job/retry",
    "POST",
    undefined,
  );
  expect(allText()).toContain("清理已完成");
});
