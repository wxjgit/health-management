import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createHarness } from "./native-harness";
let h: any;
beforeEach(() => {
  h = createHarness();
});
afterEach(() => h.cleanup());
const event = (dataset: any) => ({ currentTarget: { dataset } });
const calls = (path: string) =>
  h.server.mock.calls
    .map((c: any[]) => c[0])
    .filter((o: any) => o.url.endsWith(path));
const workflow = {
  thread_id: "thread",
  status: "waiting",
  processing_mode: "local_only",
  pending_input: {
    kind: "clarification",
    question: "多少分钟？",
    interrupt_id: "interrupt-current",
  },
};
it("全部11个页面都能注册原生Page并读取数据", async () => {
  for (const name of [
    "login",
    "consent",
    "insights",
    "chat",
    "history",
    "settings",
    "today",
    "facts",
    "preferences",
    "notifications",
    "privacy",
  ]) {
    const p = await h.mount(name);
    expect(p.data.error, name).toBe("");
  }
});
it("登录默认不授权，必要同意缺失不调用微信登录", async () => {
  const p = await h.mount("login");
  expect(p.data.agreement).toEqual({
    privacy: false,
    health: false,
    model: false,
  });
  await p.onLogin();
  expect(h.wx.login).not.toHaveBeenCalled();
});
it("登录防重复提交，不默认允许模型并清理旧缓存", async () => {
  const reset = vi.fn();
  h.load("native-runtime/state").subscribeReset(reset);
  const p = await h.mount("login");
  p.onAgreement({ detail: { privacy: true, health: true, model: false } });
  await Promise.all([p.onLogin(), p.onLogin()]);
  expect(h.wx.login).toHaveBeenCalledTimes(1);
  expect(calls("/auth/wechat/login")[0].data).toMatchObject({
    code: "synthetic-code",
    privacy_version: "policy-v1",
    model_consent: false,
  });
  expect(h.storage.get("auth-session").token).toBe("new-token");
  expect(reset).toHaveBeenCalled();
});
it("授权读取原选择，撤回模型授权原样提交", async () => {
  h.fixtures["/auth/consent"].model_allowed = true;
  const p = await h.mount("consent");
  expect(p.data.agreement.model).toBe(true);
  p.onAgreement({ detail: { privacy: true, health: true, model: false } });
  await p.onSave();
  expect(
    calls("/auth/consent").find((o: any) => o.method === "PUT").data
      .model_consent,
  ).toBe(false);
});
it("未登录不读取健康内容，进入登录页", async () => {
  h.storage.clear();
  const p = await h.mount("chat", {}, false);
  p.onShow();
  expect(h.wx.reLaunch).toHaveBeenCalledWith(
    expect.objectContaining({ url: "/pages/login/index" }),
  );
  expect(h.server).not.toHaveBeenCalled();
});
it("复盘仅同步真实路由，不请求后端", async () => {
  const p = await h.mount("insights");
  p.onShow();
  p.onReady();
  expect(p.bar.setActiveRoute).toHaveBeenCalledWith("pages/insights/index");
  expect(h.server).not.toHaveBeenCalled();
});
it("延迟响应不跨账号返回", async () => {
  let done: any;
  h.server.mockImplementation(() => new Promise((r) => (done = r)));
  const promise = h.load("native-runtime/api").api("/profile");
  await h.flush();
  h.storage.set("auth-session", {
    base: "http://127.0.0.1:8000",
    token: "other",
    user: { id: "b" },
  });
  done({ statusCode: 200, data: { private: true }, header: {} });
  await expect(promise).rejects.toMatchObject({ code: "account_changed" });
});
it("卸载后延迟读取不恢复健康内容", async () => {
  const p = await h.mount("today");
  let done: any;
  h.server.mockImplementation(() => new Promise((r) => (done = r)));
  const promise = p.reload();
  await h.flush();
  p.onUnload();
  p.setData({ daily: null });
  done({ statusCode: 200, data: h.fixtures["/dashboard"], header: {} });
  await promise;
  expect(p.data.daily).toBeNull();
});
it("示例只填入不发送，新记录尊重模型授权", async () => {
  const p = await h.mount("chat");
  p.onExample(event({ text: "今天体重65公斤" }));
  expect(calls("/chat")).toHaveLength(0);
  await p.onSend();
  expect(calls("/chat")[0].data).toMatchObject({
    text: "今天体重65公斤",
    processing_mode: "local_only",
    conversation_id: "main",
  });
});
it("服务器强制本地模式优先于模型授权", async () => {
  h.fixtures["/auth/consent"].model_allowed = true;
  h.fixtures["/health"].local_only = true;
  const p = await h.mount("chat");
  p.setData({ text: "运动30分钟" });
  await p.onSend();
  expect(calls("/chat")[0].data.processing_mode).toBe("local_only");
});
it("选择追问携带当前中断编号", async () => {
  h.fixtures["/workflows"] = [workflow];
  const p = await h.mount("chat");
  p.onSelect(event({ id: "thread" }));
  p.setData({ text: "30分钟" });
  await p.onSend();
  expect(calls("/workflows/thread/resume")[0].data).toMatchObject({
    text: "30分钟",
    interrupt_id: "interrupt-current",
  });
});
it("恢复回答保留全部原参数和编号", async () => {
  const body = {
    request_id: "old",
    interrupt_id: "old-interrupt",
    text: "30分钟",
    processing_mode: "local_only",
  };
  h.fixtures["/workflows"] = [{ ...workflow, retry_request: body }];
  const p = await h.mount("chat");
  await p.onRetry(event({ id: "thread" }));
  expect(calls("/workflows/thread/resume")[0].data).toEqual(body);
});
it("纠错提交原事件编号", async () => {
  const p = await h.mount("chat");
  h.storage.set("correction-event", { id: "event-a", raw_text: "原文" });
  p.onShow();
  await h.flush();
  p.setData({ text: "不是大碗是小碗" });
  await p.onSend();
  expect(calls("/chat")[0].data.event_id).toBe("event-a");
});
it("分页沿用游标并保留轮次内顺序", async () => {
  h.fixtures["/messages?limit=20"] = Array.from({ length: 20 }, (_, i) => ({
    id: String(i),
    role: i % 2 ? "assistant" : "user",
    text: String(i),
  }));
  h.fixtures["/messages?limit=20&before=0"] = [
    { id: "old-user", role: "user" },
    { id: "old-answer", role: "assistant" },
  ];
  const p = await h.mount("chat");
  await p.loadOlder();
  expect(p.data.newestMessages.slice(-2).map((m: any) => m.id)).toEqual([
    "old-user",
    "old-answer",
  ]);
  expect(p.data.newestMessages[0].id).toBe("18");
});
it("证据跳转对应日期，缺失趋势不算零", async () => {
  h.storage.set("evidence-event", "event-a");
  h.fixtures["/events?limit=1000"] = [
    { id: "event-a", day: "2026-10-20", data: {} },
  ];
  h.fixtures["/trends?days=28"] = [
    { date: "2026-10-20", weight_kg: null },
    { date: "2026-10-21", weight_kg: 60 },
  ];
  const p = await h.mount("history", {}, false);
  p.onShow();
  await h.flush();
  expect(p.data.section).toBe("records");
  expect(p.data.activeDate).toBe("2026-10-20");
  expect(p.data.recordedDays).toBe(1);
});
it("饮食完整标记仅在明确点击后更新", async () => {
  const p = await h.mount("today");
  await p.onComplete();
  expect(calls("/days/2026-11-01/complete")[0].data).toEqual({
    complete: true,
  });
});
it("档案弹窗关闭不提交，错误留在弹窗", async () => {
  const p = await h.mount("settings");
  p.onOpen(event({ modal: "profile" }));
  expect(p.bar.setData).toHaveBeenLastCalledWith({ hidden: true });
  p.onClose();
  expect(p.bar.setData).toHaveBeenLastCalledWith({ hidden: false });
  expect(calls("/profile").some((o: any) => o.method === "PUT")).toBe(false);
  p.onOpen(event({ modal: "profile" }));
  p.setData({ "profileForm.age": "" });
  await p.onSaveProfile();
  expect(p.data.error).toContain("完整数值");
  expect(p.data.modal).toBe("profile");
});
it("默认草案保留估算版本，不自动确认", async () => {
  const p = await h.mount("settings");
  await p.onPropose();
  expect(calls("/plans/propose")[0].data).toMatchObject({
    target_source: "system_default",
    suggestion_fingerprint: "estimate-v1",
  });
  expect(
    h.server.mock.calls.some((c: any[]) => c[0].url.endsWith("/resume")),
  ).toBe(false);
});
it("手动目标刷新不覆盖，提交来源为manual", async () => {
  const p = await h.mount("settings");
  p.onField({ detail: { group: "planForm", name: "kcal", value: "1900" } });
  h.fixtures["/targets/default"].targets.kcal = 1700;
  await p.reload();
  expect(p.data.planForm.kcal).toBe("1900");
  await p.onPropose();
  expect(calls("/plans/propose")[0].data.target_source).toBe("manual");
});
it("恢复拒绝计划保留原选择和请求编号", async () => {
  const original = {
    request_id: "decision",
    interrupt_id: "interrupt",
    approved: false,
  };
  h.fixtures["/workflows"] = [
    {
      thread_id: "plan",
      status: "resume_failed",
      pending_input: { kind: "plan_confirmation" },
      retry_request: original,
    },
  ];
  const p = await h.mount("settings");
  await p.onDecision(event({ id: "plan", mode: "retry" }));
  expect(calls("/workflows/plan/resume")[0].data).toEqual(original);
});
it("已生效计划优先于未结束流程，仅显示七项当前目标", async () => {
  h.fixtures["/plans"] = [
    {
      id: "plan",
      status: "active",
      data: { targets: h.fixtures["/targets/default"].targets, schedule: [] },
    },
  ];
  h.fixtures["/workflows"] = [
    {
      thread_id: "plan",
      status: "resuming",
      pending_input: { kind: "plan_confirmation" },
    },
  ];
  const p = await h.mount("settings");
  expect(p.data.confirmations[0].settled).toBe(true);
  expect(p.data.activeRows).toHaveLength(7);
  await p.onDecision(event({ id: "plan", mode: "approve" }));
  expect(calls("/workflows/plan/resume")).toHaveLength(0);
});
it("保护性冲突和临时截止未知不能直接确认", async () => {
  h.fixtures["/facts?include_inactive=true"] = [
    {
      id: "f",
      type: "allergy",
      confirmation: "conflict",
      enforcement: "clinical_review",
      time_scope: "temporary_unknown",
      assertion: "affirmed",
      lifecycle: "needs_review",
      version: 1,
    },
  ];
  const p = await h.mount("facts");
  expect(p.data.facts[0].canConfirm).toBe(false);
  await p.onFactAction(event({ id: "f", action: "confirm" }));
  expect(calls("/facts/f")).toHaveLength(0);
});
it("偏好冲突确认携带关联版本", async () => {
  h.fixtures["/facts?include_inactive=true"] = [
    {
      id: "f",
      content: "不喜欢燕麦",
      type: "food_preference",
      subject: "燕麦",
      subject_kind: "food",
      confirmation: "conflict",
      enforcement: "soft",
      lifecycle: "active",
      version: 2,
      schema_version: 2,
    },
    {
      id: "peer",
      type: "food_preference",
      subject: "燕麦",
      subject_kind: "food",
      confirmation: "confirmed",
      lifecycle: "active",
      version: 4,
    },
  ];
  const p = await h.mount("facts");
  await p.onFactAction(event({ id: "f", action: "confirm" }));
  expect(calls("/facts/f")[0].data).toMatchObject({
    expected_version: 2,
    resolution: "permanent",
    peer_versions: { peer: 4 },
  });
});
it("权重合计错误时不保存", async () => {
  const p = await h.mount("preferences");
  p.onField({ detail: { group: "weights", name: "energy", value: "90" } });
  await p.onSave();
  expect(p.data.error).toContain("100%");
  expect(calls("/preferences").some((o: any) => o.method === "PUT")).toBe(
    false,
  );
});
it("站内消息点击后才标记已读", async () => {
  h.fixtures["/notifications"] = [{ id: "n", title: "提醒", read: 0 }];
  const p = await h.mount("notifications");
  await p.onRead(event({ id: "n" }));
  expect(calls("/notifications/n")[0].method).toBe("PATCH");
});
it("隐私删除先预览，取消确认不执行", async () => {
  const preview = {
    preview_id: "p",
    target_type: "event",
    target_id: "e",
    counts: { "event:delete": 1 },
    reasons: [],
    scope: "dependencies",
  };
  h.server.mockImplementation(async (o: any) => ({
    statusCode: 200,
    header: {},
    data: o.url.endsWith("/preview") ? preview : {},
  }));
  const p = await h.mount("privacy", { type: "event", id: "e" });
  await p.onPreview();
  h.wx.showModal.mockImplementation((o: any) => o.success({ confirm: false }));
  await p.onExecute();
  expect(calls("/deletions")).toHaveLength(0);
  expect(p.data.counts[0].count).toBe(1);
});
it("范围变化后更新预览，必须再次确认", async () => {
  h.load("native-runtime/privacy").saveDeletionState({
    base: "http://127.0.0.1:8000",
    preview: {
      preview_id: "old",
      target_type: "event",
      target_id: "e",
      counts: {},
      reasons: [],
    },
    requestId: "same",
  });
  h.server.mockResolvedValue({
    statusCode: 409,
    header: {},
    data: {
      detail: "范围变化",
      preview: {
        preview_id: "new",
        counts: { "event:delete": 2 },
        reasons: [],
      },
    },
  });
  const p = await h.mount("privacy");
  await p.onExecute();
  expect(p.data.state.preview.preview_id).toBe("new");
  expect(p.data.state.requestId).toBe("");
  expect(h.load("native-runtime/state").pendingOperation()).toBeNull();
});
it("清理作业未完成时禁止重新读取健康页面", async () => {
  h.load("native-runtime/privacy").saveDeletionState({
    base: "http://127.0.0.1:8000",
    preview: { counts: {} },
    requestId: "d",
    result: { state: "pending", deletion_id: "job" },
  });
  const p = await h.mount("today");
  expect(p.data.daily).toBeNull();
  expect(h.server).not.toHaveBeenCalled();
});
it("注销取消时保留账号与会话", async () => {
  const p = await h.mount("settings");
  h.wx.showModal.mockImplementation((o: any) => o.success({ confirm: false }));
  await p.onDeleteAccount();
  expect(calls("/auth/account")).toHaveLength(0);
  expect(h.storage.get("auth-session").token).toBe("synthetic-token");
});
