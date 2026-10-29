import React, { useEffect } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const platform = vi.hoisted(() => {
  const tabBar = { setData: vi.fn() };
  return {
    storage: new Map<string, unknown>(),
    params: {} as Record<string, string>,
    showModal: vi.fn(),
    navigateTo: vi.fn().mockResolvedValue({}),
    api: vi.fn(),
    tabBar,
    page: { getTabBar: () => tabBar },
  };
});
vi.mock("@tarojs/taro", () => ({
  default: {
    getStorageSync: (key: string) => platform.storage.get(key),
    setStorageSync: (key: string, value: unknown) =>
      platform.storage.set(key, value),
    removeStorageSync: (key: string) => platform.storage.delete(key),
    showModal: platform.showModal,
    navigateTo: platform.navigateTo,
    login: vi.fn().mockResolvedValue({ code: "synthetic-login-code" }),
    reLaunch: vi.fn().mockResolvedValue({}),
    pageScrollTo: vi.fn().mockResolvedValue({}),
    getCurrentInstance: () => ({
      router: { params: platform.params },
      page: platform.page,
    }),
    env: { USER_DATA_PATH: "/app" },
  },
  useDidShow: (callback: () => void) => useEffect(callback, []),
  useDidHide: () => {},
  useReachBottom: () => {},
}));
vi.mock("@tarojs/components", () => ({
  View: "view",
  Text: "text",
  Button: "button",
  Input: "input",
  Textarea: "textarea",
  Switch: "switch",
  Image: "image",
  Picker: "picker",
  RootPortal: "root-portal",
  ScrollView: "scroll-view",
}));
vi.mock("../src/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api")>()),
  api: platform.api,
}));

import History from "../src/pages/history";
import Chat from "../src/pages/chat";
import Plans from "../src/components/plan-section";
import Settings from "../src/pages/settings";
import Login from "../src/pages/login";
import Facts from "../src/pages/facts";
import Privacy from "../src/pages/privacy";
import { ApiError } from "../src/api";
import { changed, clearPrivateState, useResource } from "../src/state";

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
  platform.storage.set("auth-session", {
    base: "http://127.0.0.1:8000",
    user: { id: "test-account" },
  });
  platform.api.mockReset();
  platform.navigateTo.mockClear();
  platform.tabBar.setData.mockClear();
  platform.showModal.mockResolvedValue({ confirm: true });
  platform.params = {};
  rows = {
    "/auth/consent": { model_allowed: true, privacy_accepted: true },
    "/auth/me": { user: { id: "test-account" } },
    "/plans": [],
    "/workflows": [],
    "/targets/default": defaults,
    "/messages?limit=20": [],
    "/workflows/expired": [],
    "/health": {
      local_only: false,
      mode: "model",
      model_name: "model",
      model_provider: "provider",
      today: "2026-10-27",
    },
    "/dashboard": {
      date: "2026-10-27",
      totals: { kcal: null, unknown_items: 1 },
      record_count: 0,
    },
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
      processing_mode: "model_allowed",
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
it("我的展示当前计划，并可弹窗更改计划生成待确认草案", async () => {
  rows["/plans"] = [
    {
      id: "active-plan",
      status: "active",
      data: {
        targets: defaults.targets,
        schedule: [
          { date: "2026-10-27", food: "均衡搭配", exercise: "散步30分钟" },
        ],
        evidence_refs: [],
        notes: "",
        next_meal: "均衡搭配",
        tomorrow_focus: "规律运动",
      },
    },
  ];
  await mount(<Settings />);
  expect(allText()).toContain("当前生效计划");
  expect(allText()).toContain("散步30分钟");
  expect(allText()).toContain("膳食纤维");
  expect(allText()).toContain("未设置");
  expect(allText()).not.toContain("均衡搭配");
  expect(allText()).not.toContain("明日重点");
  expect(allText()).not.toContain("查看一周安排");
  expect(allText()).not.toContain("计划依据");
  expect(buttons("生成计划草案")).toHaveLength(0);
  expect(
    renderer.root.findAll((node) => node.props.className === "page"),
  ).toHaveLength(1);
  await click("更改计划");
  expect(
    renderer.root.findAll((node) => String(node.type) === "root-portal"),
  ).toHaveLength(1);
  await click("生成计划草案");
  expect(platform.api).toHaveBeenCalledWith(
    "/plans/propose",
    "POST",
    expect.objectContaining({ targets: defaults.targets }),
  );
  expect(
    platform.api.mock.calls.some((call) => call[0].endsWith("/resume")),
  ).toBe(false);
});
it("计划弹窗关闭保留未提交内容，历史区域不再显示", async () => {
  rows["/plans"] = [
    { id: "old", status: "superseded", data: { notes: "旧计划备注" } },
  ];
  await mount(<Plans />);
  expect(allText()).not.toContain("其他计划与历史");
  expect(allText()).not.toContain("旧计划备注");
  await click("设置目标，生成计划");
  await act(async () => {
    renderer.root
      .findAllByType("input")[0]
      .props.onInput({ detail: { value: "1900" } });
  });
  expect(platform.tabBar.setData).toHaveBeenLastCalledWith({ hidden: true });
  await click("关闭");
  expect(platform.tabBar.setData).toHaveBeenLastCalledWith({ hidden: false });
  expect(
    renderer.root.findAll((node) => String(node.type) === "root-portal"),
  ).toHaveLength(0);
  expect(platform.api.mock.calls.some((call) => call[1] === "POST")).toBe(
    false,
  );
  await click("设置目标，生成计划");
  expect(renderer.root.findAllByType("input")[0].props.value).toBe("1900");
  await click("生成计划草案");
  expect(
    renderer.root.findAll((node) => String(node.type) === "root-portal"),
  ).toHaveLength(0);
});
it("今日状态默认收起，弹窗只读展示并可进入今日汇总", async () => {
  await mount(<Chat />);
  expect(
    renderer.root.findAll((node) => node.props.className === "health-overview"),
  ).toHaveLength(0);
  await click("查看今日状态");
  expect(
    renderer.root.findAll((node) => node.props.className === "health-overview"),
  ).toHaveLength(1);
  expect(allText()).toContain("未知");
  expect(platform.tabBar.setData).toHaveBeenLastCalledWith({ hidden: true });
  await click("关闭");
  expect(platform.tabBar.setData).toHaveBeenLastCalledWith({ hidden: false });
  expect(
    renderer.root.findAll((node) => node.props.className === "health-overview"),
  ).toHaveLength(0);
  await click("查看今日状态");
  await click("今日汇总");
  expect(platform.navigateTo).toHaveBeenCalledWith({
    url: "/pages/today/index",
  });
  expect(platform.tabBar.setData).toHaveBeenLastCalledWith({ hidden: false });
  expect(platform.api.mock.calls.some((call) => call[1] === "POST")).toBe(
    false,
  );
});
it("个人档案弹窗关闭不提交，保存成功后关闭并恢复导航", async () => {
  rows["/profile"] = {
    age: 30,
    height_cm: 168,
    weight_kg: 60,
    goal_weight_kg: 58,
    goal_date: "2027-01-01",
    sex: "female",
    activity: "light",
    nutrition_context: "general",
  };
  await mount(<Settings />);
  expect(buttons("保存档案")).toHaveLength(0);
  await click("个人档案");
  expect(platform.tabBar.setData).toHaveBeenLastCalledWith({ hidden: true });
  await act(async () => {
    renderer.root
      .findAllByType("input")[0]
      .props.onInput({ detail: { value: "31" } });
  });
  await click("关闭");
  expect(platform.tabBar.setData).toHaveBeenLastCalledWith({ hidden: false });
  expect(platform.api.mock.calls.some((call) => call[1] === "PUT")).toBe(false);
  await click("个人档案");
  expect(renderer.root.findAllByType("input")[0].props.value).toBe("31");
  await click("保存档案");
  expect(platform.api).toHaveBeenCalledWith(
    "/profile",
    "PUT",
    expect.objectContaining({ age: 31, height_cm: 168 }),
  );
  expect(buttons("保存档案")).toHaveLength(0);
  expect(allText()).toContain("档案已保存");
  expect(platform.tabBar.setData).toHaveBeenLastCalledWith({ hidden: false });
});
it("个人档案校验失败保持弹窗打开", async () => {
  await mount(<Settings />);
  await click("个人档案");
  await click("保存档案");
  expect(allText()).toContain("请填写完整数值和目标日期");
  expect(buttons("保存档案")).toHaveLength(1);
  expect(platform.tabBar.setData).toHaveBeenLastCalledWith({ hidden: true });
  expect(platform.api.mock.calls.some((call) => call[1] === "PUT")).toBe(false);
});
it("我的统一刷新读取档案与计划，不生成草案", async () => {
  await mount(<Settings />);
  expect(buttons("刷新计划")).toHaveLength(0);
  expect(buttons("刷新")).toHaveLength(1);
  platform.api.mockClear();
  await click("刷新");
  for (const path of ["/profile", "/plans", "/workflows", "/targets/default"]) {
    expect(platform.api).toHaveBeenCalledWith(path);
  }
  expect(platform.api.mock.calls.some((call) => call[1] === "POST")).toBe(
    false,
  );
});
it("计划弹窗卸载时恢复底部导航", async () => {
  await mount(<Plans />);
  await click("设置目标，生成计划");
  expect(platform.tabBar.setData).toHaveBeenLastCalledWith({ hidden: true });
  act(() => renderer.unmount());
  expect(platform.tabBar.setData).toHaveBeenLastCalledWith({ hidden: false });
});
it("计划弹窗校验失败保持打开并展示错误", async () => {
  await mount(<Plans />);
  await click("设置目标，生成计划");
  await act(async () => {
    renderer.root
      .findAllByType("input")[0]
      .props.onInput({ detail: { value: "" } });
  });
  await click("生成计划草案");
  expect(
    renderer.root.findAll((node) => String(node.type) === "root-portal"),
  ).toHaveLength(1);
  expect(allText()).toContain("请填写完整的数值目标");
  expect(
    platform.api.mock.calls.some((call) => call[0] === "/plans/propose"),
  ).toBe(false);
});
it("自动默认目标保留估算版本，只有明确确认才采用计划", async () => {
  await mount(<Plans />);
  await click(buttons("更改计划").length ? "更改计划" : "设置目标，生成计划");
  await click("生成计划草案");
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
  await click(buttons("更改计划").length ? "更改计划" : "设置目标，生成计划");
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
  await act(async () => changed());
  expect(renderer.root.findAllByType("input")[0].props.value).toBe("1900");
  await click("生成计划草案");
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
  await click(buttons("更改计划").length ? "更改计划" : "设置目标，生成计划");
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
  await click(buttons("更改计划").length ? "更改计划" : "设置目标，生成计划");
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
  await click(buttons("更改计划").length ? "更改计划" : "设置目标，生成计划");
  await click("生成计划草案");
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
    owner: "test-account",
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
  await click("查看删除范围");
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
  await click("查看删除范围");
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
    owner: "test-account",
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

it("足迹趋势不重复展示原始记录，记录可独立按日查看", async () => {
  rows["/events?limit=1000"] = [
    {
      id: "weight-entry",
      kind: "weight",
      day: "2026-10-28",
      version: 1,
      raw_text: "今天体重65公斤",
      data: {},
    },
  ];
  rows["/trends?days=28"] = [];
  rows["/monthly-summaries"] = [];
  await mount(<History />);
  expect(allText()).not.toContain("今天体重65公斤");
  await click("睡眠");
  expect(allText()).not.toContain("今天体重65公斤");
  await click("记录");
  expect(allText()).toContain("今天体重65公斤");
  expect(buttons("睡眠")).toHaveLength(0);
  await click("趋势");
  expect(allText()).not.toContain("今天体重65公斤");
});
it("从相关记录进入足迹时直接打开记录页", async () => {
  rows["/events?limit=1000"] = [
    {
      id: "linked-entry",
      kind: "weight",
      day: "2026-10-27",
      version: 1,
      raw_text: "关联体重记录",
      data: {},
    },
  ];
  rows["/trends?days=28"] = [];
  rows["/monthly-summaries"] = [];
  platform.storage.set("evidence-event", "linked-entry");
  await mount(<History />);
  expect(allText()).toContain("关联体重记录");
  expect(buttons("睡眠")).toHaveLength(0);
});

it("聊天首次仅加载最近一批，点击后加载更早对话并保留顺序", async () => {
  const message = (index: number) => ({
    id: `m${index}`,
    role: index % 2 ? "assistant" : "user",
    text: `分页消息${index}`,
    cards: [],
    evidence_refs: [],
    process_trace: [],
  });
  rows["/messages?limit=20"] = Array.from({ length: 20 }, (_, i) =>
    message(i + 20),
  );
  rows["/messages?limit=20&before=m20"] = Array.from({ length: 20 }, (_, i) =>
    message(i),
  );
  rows["/messages?limit=20&before=m0"] = [];
  await mount(<Chat />);
  expect(allText()).toContain("分页消息38");
  expect(allText()).not.toContain("分页消息0");
  expect(platform.api).not.toHaveBeenCalledWith(
    "/messages?limit=20&before=m20",
  );
  await click("查看更早对话");
  expect(allText()).toContain("分页消息0");
  expect(allText().indexOf("分页消息38")).toBeLessThan(
    allText().indexOf("分页消息0"),
  );
  await click("查看更早对话");
  expect(buttons("查看更早对话")).toHaveLength(0);
});
it("更早对话加载失败后可以重试", async () => {
  rows["/messages?limit=20"] = Array.from({ length: 20 }, (_, i) => ({
    id: `m${i}`,
    role: "user",
    text: `消息${i}`,
    cards: [],
    evidence_refs: [],
    process_trace: [],
  }));
  const original = platform.api.getMockImplementation()!;
  let failed = false;
  platform.api.mockImplementation(async (path: string, ...args: unknown[]) => {
    if (path.includes("before=")) {
      if (!failed) {
        failed = true;
        throw new Error("断网");
      }
      return [];
    }
    return original(path, ...args);
  });
  await mount(<Chat />);
  await click("查看更早对话");
  expect(allText()).toContain("较早对话加载失败");
  await click("重新加载");
  expect(buttons("重新加载")).toHaveLength(0);
});

it("登录要求隐私与健康信息两项同意，模型授权不默认勾选", async () => {
  rows["/policies"] = {
    ready: true,
    version: "current-policy",
    operator: "Synthetic operator",
    contact: "test@example.invalid",
    model_available: true,
    model_provider: "synthetic.invalid",
    model_name: "test",
    privacy: ["隐私说明"],
    terms: ["用户协议"],
    model_notice: ["可选模型说明"],
  };
  const previous = platform.api.getMockImplementation()!;
  platform.api.mockImplementation(
    (path: string, method: string, body: unknown) =>
      path === "/auth/wechat/login"
        ? Promise.resolve({
            access_token: "synthetic",
            user: { id: "logged-user" },
            expires_at: 9999999999,
          })
        : previous(path, method, body),
  );
  await mount(<Login />);
  const button = () =>
    renderer.root
      .findAllByType("button")
      .find((node) => node.children.includes("微信登录"))!;
  expect(button().props.disabled).toBe(true);
  const switches = () => renderer.root.findAllByType("switch");
  expect(switches().map((node) => node.props.checked)).toEqual([
    false,
    false,
    false,
  ]);
  await act(async () => {
    switches()[0].props.onChange({ detail: { value: true } });
  });
  expect(button().props.disabled).toBe(true);
  await act(async () => {
    switches()[1].props.onChange({ detail: { value: true } });
  });
  expect(button().props.disabled).toBe(false);
  await act(async () => {
    await button().props.onClick();
  });
  expect(platform.api).toHaveBeenCalledWith(
    "/auth/wechat/login",
    "POST",
    expect.objectContaining({
      privacy_consent: true,
      health_consent: true,
      model_consent: false,
      privacy_version: "current-policy",
    }),
  );
});
