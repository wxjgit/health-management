import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";

let bodies: { path: string; body: Record<string, unknown> }[];
let failOnce: boolean;
let workflows: unknown[];
let records: unknown[];
let chatMessages: unknown[];
let storedPlans: unknown[];
let defaultTargets: Record<string, unknown>;
let healthOverride: Record<string, unknown> | null;

const suggestedValues = { kcal: 2100, protein_g: 105, carbs_g: 262, fat_g: 70, exercise_minutes: 150 };
const readyDefaults = () => ({
  status: "ready", targets: suggestedValues, reason: "温和减重起点", basis: "基于基础档案的本地公式估算。",
  fingerprint: "a".repeat(64), formula_version: "mifflin-editable-defaults-v1", warnings: [], sources: [],
});

const confirmationDraft = (status = "waiting_input") => ({
  thread_id: "state-plan", status,
  pending_input: { kind: "plan_confirmation", plan_id: "state-plan", interrupt_id: "state-question", question: "采用这份计划？" },
});
const confirmationPlan = (status = "pending") => ({
  id: "state-plan", status, created_at: "2026-10-23", data: {
    targets: { kcal: 1800, protein_g: 100, carbs_g: 215, fat_g: 60, exercise_minutes: 120 },
    schedule: [], next_meal: "按实际情况安排。", tomorrow_focus: "记录。", evidence_refs: [],
    notes: "", changes: {}, data_version: 1, needs_confirmation: true,
  },
});
function deferredResponse() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => { resolve = done; });
  return { promise, resolve };
}
const confirmedResponse = () => new Response(JSON.stringify({ status: "completed", thread_id: "state-plan" }), { status: 200 });

describe("计划确认的并发状态", () => {
  it("无法刷新服务端状态时保持确认中，恢复连接后再启用操作", async () => {
    workflows = [confirmationDraft()]; storedPlans = [confirmationPlan()];
    const originalFetch = fetch;
    let unavailable = false;
    vi.stubGlobal("fetch", vi.fn((path: string, options?: RequestInit) => {
      if (options?.method === "POST" && path.endsWith("/resume")) {
        unavailable = true;
        return Promise.resolve(new Response(JSON.stringify({ detail: "连接中断" }), { status: 503 }));
      }
      if (unavailable && (path === "/api/plans" || path === "/api/workflows"))
        return Promise.resolve(new Response(JSON.stringify({ detail: "暂时无法读取状态" }), { status: 503 }));
      return originalFetch(path, options);
    }));
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "我的计划" }));
    await userEvent.click(await screen.findByRole("button", { name: "采用计划" }));
    expect((await screen.findByRole("alert")).textContent).toContain("暂时无法获取确认结果");
    expect((screen.getByRole("button", { name: "确认中…" }) as HTMLButtonElement).disabled).toBe(true);
    unavailable = false;
    await userEvent.click(screen.getByRole("button", { name: "刷新状态" }));
    await waitFor(() => expect((screen.getByRole("button", { name: "采用计划" }) as HTMLButtonElement).disabled).toBe(false));
  });

  it("点击后立即显示确认中并阻止重复确认，成功后移除草案操作", async () => {
    workflows = [confirmationDraft()]; storedPlans = [confirmationPlan()];
    const response = deferredResponse();
    const originalFetch = fetch;
    vi.stubGlobal("fetch", vi.fn((path: string, options?: RequestInit) => {
      if (options?.method === "POST" && path.endsWith("/resume")) {
        bodies.push({ path, body: JSON.parse(options.body as string) });
        return response.promise;
      }
      return originalFetch(path, options);
    }));
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "我的计划" }));
    await userEvent.click(await screen.findByRole("button", { name: "采用计划" }));
    const confirming = screen.getByRole("button", { name: "确认中…" }) as HTMLButtonElement;
    expect(confirming.disabled).toBe(true);
    expect((screen.getByRole("button", { name: "取消待处理计划" }) as HTMLButtonElement).disabled).toBe(false);
    await userEvent.click(confirming);
    expect(bodies).toHaveLength(1);
    workflows = []; storedPlans = [confirmationPlan("active")];
    await act(async () => { response.resolve(confirmedResponse()); });
    await screen.findByText("计划已生效");
    expect(screen.queryByRole("button", { name: "采用计划" })).toBeNull();
    expect(screen.queryByRole("button", { name: "取消待处理计划" })).toBeNull();
  });

  it("确认期间取消冲突时先提示再刷新，仍在执行则保持确认中", async () => {
    workflows = [confirmationDraft()]; storedPlans = [confirmationPlan()];
    const response = deferredResponse();
    const originalFetch = fetch;
    let planReads = 0, workflowReads = 0;
    vi.stubGlobal("fetch", vi.fn((path: string, options?: RequestInit) => {
      if (path === "/api/plans") planReads++;
      if (path === "/api/workflows") workflowReads++;
      if (options?.method === "POST" && path.endsWith("/resume")) {
        const payload = JSON.parse(options.body as string);
        workflows = [{ ...confirmationDraft("resuming"), retry_request: payload }];
        return response.promise;
      }
      if (options?.method === "DELETE") return Promise.resolve(new Response(
        JSON.stringify({ detail: "计划正在确认，请稍后刷新状态，尚未取消。" }), { status: 409 },
      ));
      return originalFetch(path, options);
    }));
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "我的计划" }));
    await userEvent.click(await screen.findByRole("button", { name: "采用计划" }));
    await userEvent.click(screen.getByRole("button", { name: "取消待处理计划" }));
    expect((await screen.findByRole("alert")).textContent).toBe("计划正在确认，暂时无法取消");
    await waitFor(() => expect(workflowReads).toBeGreaterThan(1));
    expect(planReads).toBeGreaterThan(1);
    expect((screen.getByRole("button", { name: "确认中…" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByText("已取消待处理计划")).toBeNull();
    workflows = []; storedPlans = [confirmationPlan("active")];
    await userEvent.click(screen.getByRole("button", { name: "刷新状态" }));
    await screen.findByText("计划已生效");
    expect(screen.queryByRole("button", { name: "取消待处理计划" })).toBeNull();
    await act(async () => { response.resolve(confirmedResponse()); });
  });

  it("重新打开页面时根据后端显示确认中，并轮询至计划生效", async () => {
    workflows = [{ ...confirmationDraft("resuming"), retry_request: {
      request_id: "already-running", interrupt_id: "state-question", approved: true,
    } }]; storedPlans = [confirmationPlan()];
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "我的计划" }));
    expect((await screen.findByRole("button", { name: "确认中…" }) as HTMLButtonElement).disabled).toBe(true);
    expect(bodies).toHaveLength(0);
    workflows = []; storedPlans = [confirmationPlan("active")];
    await screen.findByText("计划已生效", {}, { timeout: 4000 });
    expect(screen.queryByRole("button", { name: "确认中…" })).toBeNull();
    expect(screen.queryByRole("button", { name: "取消待处理计划" })).toBeNull();
  });

  it("确认失败但后端仍等待输入时恢复确认和取消，同一选择重试沿用请求编号", async () => {
    workflows = [confirmationDraft()]; storedPlans = [confirmationPlan()];
    const originalFetch = fetch;
    vi.stubGlobal("fetch", vi.fn(async (path: string, options?: RequestInit) => {
      if (options?.method === "POST" && path.endsWith("/resume")) {
        bodies.push({ path, body: JSON.parse(options.body as string) });
        if (bodies.length === 1) return new Response(JSON.stringify({ detail: "尚未接收本次确认" }), { status: 500 });
        workflows = []; storedPlans = [confirmationPlan("active")];
        return confirmedResponse();
      }
      return originalFetch(path, options);
    }));
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "我的计划" }));
    await userEvent.click(await screen.findByRole("button", { name: "采用计划" }));
    expect((await screen.findByRole("alert")).textContent).toContain("计划确认失败");
    expect((screen.getByRole("button", { name: "采用计划" }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole("button", { name: "取消待处理计划" }) as HTMLButtonElement).disabled).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "采用计划" }));
    await screen.findByText("计划已生效");
    expect(bodies[0].body.request_id).toBe(bodies[1].body.request_id);
  });

  it("确认失败且答案已绑定时只允许重试上次确认", async () => {
    workflows = [confirmationDraft()]; storedPlans = [confirmationPlan()];
    const originalFetch = fetch;
    vi.stubGlobal("fetch", vi.fn(async (path: string, options?: RequestInit) => {
      if (options?.method === "POST" && path.endsWith("/resume")) {
        const payload = JSON.parse(options.body as string);
        bodies.push({ path, body: payload });
        if (bodies.length === 1) {
          workflows = [{ ...confirmationDraft("resume_failed"), retry_request: payload }];
          return new Response(JSON.stringify({ detail: "确认处理失败" }), { status: 409 });
        }
        workflows = []; storedPlans = [confirmationPlan("active")];
        return confirmedResponse();
      }
      return originalFetch(path, options);
    }));
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "我的计划" }));
    await userEvent.click(await screen.findByRole("button", { name: "采用计划" }));
    await screen.findByRole("alert");
    expect(screen.queryByRole("button", { name: "采用计划" })).toBeNull();
    expect(screen.queryByRole("button", { name: "保留原计划" })).toBeNull();
    await userEvent.click(await screen.findByRole("button", { name: "重试上次确认" }));
    await screen.findByText("计划已生效");
    expect(bodies[1].body).toEqual(bodies[0].body);
  });

  it("计划已生效优先于尚未完成的流程状态，草案按钮不再显示", async () => {
    workflows = [{ ...confirmationDraft("resuming"), retry_request: {
      request_id: "already-active", interrupt_id: "state-question", approved: true,
    } }]; storedPlans = [confirmationPlan("active")];
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "我的计划" }));
    await screen.findByText("计划已生效");
    expect(screen.queryByRole("button", { name: "确认中…" })).toBeNull();
    expect(screen.queryByRole("button", { name: "重试上次确认" })).toBeNull();
    expect(screen.queryByRole("button", { name: "取消待处理计划" })).toBeNull();
  });

  it("较早的慢刷新不会把已生效计划重新显示为待确认", async () => {
    const running = { ...confirmationDraft("resuming"), retry_request: {
      request_id: "refresh-order", interrupt_id: "state-question", approved: true,
    } };
    workflows = [running]; storedPlans = [confirmationPlan()];
    const originalFetch = fetch;
    const oldPlans = deferredResponse(), oldWorkflows = deferredResponse();
    let holdOld = false, remaining = 2;
    vi.stubGlobal("fetch", vi.fn((path: string, options?: RequestInit) => {
      if (holdOld && remaining > 0 && (path === "/api/plans" || path === "/api/workflows")) {
        remaining--;
        return path === "/api/plans" ? oldPlans.promise : oldWorkflows.promise;
      }
      return originalFetch(path, options);
    }));
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "我的计划" }));
    holdOld = true;
    await userEvent.click(await screen.findByRole("button", { name: "刷新状态" }));
    workflows = []; storedPlans = [confirmationPlan("active")];
    await userEvent.click(screen.getByRole("button", { name: "刷新状态" }));
    await screen.findByText("计划已生效");
    await act(async () => {
      oldPlans.resolve(new Response(JSON.stringify([confirmationPlan()]), { status: 200 }));
      oldWorkflows.resolve(new Response(JSON.stringify([running]), { status: 200 }));
    });
    expect(screen.getByText("计划已生效")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "确认中…" })).toBeNull();
    expect(screen.queryByRole("button", { name: "取消待处理计划" })).toBeNull();
  });
});

const emptyDay = {
  date: "2026-10-08",
  totals: {
    kcal: null,
    protein_g: null,
    carbs_g: null,
    fat_g: null,
    unknown_items: 0,
    complete_nutrition: false,
  },
  targets: null,
  remaining: null,
  weight_kg: null,
  sleep_minutes: null,
  exercise_minutes: 0,
  meal_count: 0,
  record_count: 0,
  diet_complete: false,
  events: [],
};

describe("隐私删除", () => {
  const preview = () => ({ preview_id: "preview-1", target_type: "event", target_id: "weight-1",
    counts: { "event:delete": 1, "message:delete": 2, "workflow:delete": 1 },
    scope: "dependencies", reasons: [] });
  const seedRecord = () => { records = [{ id: "weight-1", kind: "weight", day: "2026-10-08",
    occurred_at: "2026-10-08T07:00:00+08:00", raw_text: "今天体重65公斤", version: 1,
    data: { value: 65, items: [] } }]; };
  const jsonResponse = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

  it("先展示后端影响范围，确认后才执行删除", async () => {
    seedRecord();
    const original = fetch;
    const executed = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async (path: string, options?: RequestInit) => {
      if (path === "/api/deletions/preview") return jsonResponse(preview());
      if (path === "/api/deletions") {
        executed(JSON.parse(options!.body as string)); records = [];
        return jsonResponse({ ...preview(), deletion_id: "job-1", state: "completed", deleted: true });
      }
      return original(path, options);
    }));
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "历史与趋势" }));
    await userEvent.click(await screen.findByRole("button", { name: "删除今天体重65公斤" }));
    await screen.findByRole("dialog", { name: "确认删除范围" });
    expect(screen.getByText("聊天消息：2")).toBeTruthy();
    expect(executed).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await screen.findByText("记录及受影响的副本已删除，独立内容已保留");
    expect(executed).toHaveBeenCalledTimes(1);
    expect(executed.mock.calls[0][0].preview_id).toBe("preview-1");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("取消预览不执行删除", async () => {
    seedRecord(); const original = fetch; const execute = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async (path: string, options?: RequestInit) => {
      if (path === "/api/deletions/preview") return jsonResponse(preview());
      if (path === "/api/deletions") execute();
      return original(path, options);
    }));
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "历史与趋势" }));
    await userEvent.click(await screen.findByRole("button", { name: "删除今天体重65公斤" }));
    await userEvent.click(await screen.findByRole("button", { name: "暂不删除" }));
    expect(execute).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("范围变化时展示新范围，等待再次确认", async () => {
    seedRecord(); const original = fetch; let executions = 0;
    vi.stubGlobal("fetch", vi.fn(async (path: string, options?: RequestInit) => {
      if (path === "/api/deletions/preview") return jsonResponse(preview());
      if (path === "/api/deletions") {
        executions++;
        return jsonResponse({ code: "scope_changed", detail: "清理范围已变化，请重新查看影响范围。",
          preview: { ...preview(), preview_id: "preview-2", counts: { "message:delete": 3 },
            reasons: ["新增了一份相关副本。"] } }, 409);
      }
      return original(path, options);
    }));
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "历史与趋势" }));
    await userEvent.click(await screen.findByRole("button", { name: "删除今天体重65公斤" }));
    await userEvent.click(await screen.findByRole("button", { name: "确认删除" }));
    await screen.findByText("聊天消息：3");
    expect(screen.getByText("新增了一份相关副本。")).toBeTruthy();
    expect(executions).toBe(1);
    expect(screen.getByRole("button", { name: "确认删除" })).toBeTruthy();
  });

  it("清理未完成时保留任务，并能继续清理", async () => {
    seedRecord(); const original = fetch;
    vi.stubGlobal("fetch", vi.fn(async (path: string, options?: RequestInit) => {
      if (path === "/api/deletions/preview") return jsonResponse(preview());
      if (path === "/api/deletions") {
        records = [];
        return jsonResponse({ ...preview(), deletion_id: "job-pending", state: "pending", deleted: false });
      }
      if (path === "/api/deletions/job-pending/retry")
        return jsonResponse({ ...preview(), deletion_id: "job-pending", state: "completed", deleted: true });
      return original(path, options);
    }));
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "历史与趋势" }));
    await userEvent.click(await screen.findByRole("button", { name: "删除今天体重65公斤" }));
    await userEvent.click(await screen.findByRole("button", { name: "确认删除" }));
    await screen.findByRole("dialog", { name: "隐私清理进行中" });
    await userEvent.click(await screen.findByRole("button", { name: "继续清理" }));
    await screen.findByText("记录及受影响的副本已删除，独立内容已保留");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("其他标签页清理后，较早的慢响应不会恢复旧消息", async () => {
    let channel: { onmessage: ((event: { data: { state: string } }) => void) | null };
    vi.stubGlobal("BroadcastChannel", class {
      onmessage = null;
      constructor() { channel = this; }
      postMessage() {}
      close() {}
    });
    const oldMessages = [{ id: "old-copy", role: "assistant", text: "旧体重上下文65公斤",
      cards: [], evidence_refs: [], process_trace: [], duration_ms: 0, created_at: "2026-10-08" }];
    chatMessages = oldMessages;
    const original = fetch;
    const delayed = deferredResponse();
    let holdNext = false;
    vi.stubGlobal("fetch", vi.fn((path: string, options?: RequestInit) => {
      if (path === "/api/messages" && holdNext) { holdNext = false; return delayed.promise; }
      return original(path, options);
    }));
    render(<App />);
    await screen.findByText("旧体重上下文65公斤");
    holdNext = true;
    await act(async () => { channel.onmessage?.({ data: { state: "completed" } }); });
    chatMessages = [];
    await act(async () => { channel.onmessage?.({ data: { state: "completed" } }); });
    await act(async () => { delayed.resolve(jsonResponse(oldMessages)); });
    expect(screen.queryByText("旧体重上下文65公斤")).toBeNull();
  });
});

beforeEach(() => {
  localStorage.clear();
  healthOverride = null;
  bodies = [];
  failOnce = false;
  workflows = [];
  records = [];
  chatMessages = [];
  storedPlans = [];
  defaultTargets = { status: "needs_profile", targets: null, reason: "建立基础档案后自动填入目标。",
    basis: "", fingerprint: "0".repeat(64), warnings: [], sources: [] };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, options?: RequestInit) => {
      const route = path.replace("/api", "");
      if (options?.method === "POST" || options?.method === "PUT") {
        const body = JSON.parse(options.body as string);
        bodies.push({ path: route, body });
        if (failOnce) {
          failOnce = false;
          return new Response(
            JSON.stringify({ detail: "测试连接失败，请重试" }),
            { status: 500 },
          );
        }
        return new Response(
          JSON.stringify({ status: "completed", thread_id: "done" }),
          { status: 200 },
        );
      }
      const data: Record<string, unknown> = {
        "/health": healthOverride ?? {
          mode: "demo",
          today: "2026-10-08",
          timezone: "Asia/Shanghai",
          model_name: null,
          scheduler_enabled: true,
        },
        "/profile": null,
        "/dashboard": emptyDay,
        "/messages": chatMessages,
        "/events": records,
        "/trends": [],
        "/plans": storedPlans,
        "/targets/default": defaultTargets,
        "/insights": [],
        "/reviews": [],
        "/notifications": [],
        "/workflows": workflows,
        "/facts": [],
        "/monthly-summaries": [],
        "/preferences": {
          weights: {
            energy: 30,
            macros: 25,
            satiety: 20,
            adherence: 15,
            snacking: 10,
          },
          meal_times: { breakfast: "08:00", lunch: "12:00", dinner: "18:00" },
          checkin_time: "09:00",
          summary_time: "21:00",
          weekly_day: 6,
          notifications_enabled: true,
        },
        "/preferences/suggestion": null,
      };
      return new Response(JSON.stringify(data[route] ?? []), { status: 200 });
    }),
  );
});

describe("网页核心交互", () => {
  it("刷新后从服务端恢复原答案重试，不能直接输入不同答案", async () => {
    const payload = { request_id: "original-answer", interrupt_id: "q-retry", text: "65公斤",
      event_id: null, approved: null, processing_mode: "model_allowed" };
    workflows = [{ thread_id: "retry-weight", status: "resume_failed", processing_mode: "model_allowed",
      pending_input: { kind: "clarification", interrupt_id: "q-retry", question: "体重多少？" },
      retry_request: payload }];
    render(<App />);
    await screen.findByText(/上次补充“65公斤”未完成/);
    expect((screen.getByLabelText("描述饮食或身体状态") as HTMLTextAreaElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "发送记录" }) as HTMLButtonElement).disabled).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "重试原答案" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ path: "/workflows/retry-weight/resume", body: payload });
  });

  it("补充请求失败后刷新恢复状态并沿用原请求重试", async () => {
    const pending = { thread_id: "failed-weight", status: "waiting_input", processing_mode: "model_allowed",
      pending_input: { kind: "clarification", interrupt_id: "q-failed", question: "体重多少？" } };
    workflows = [pending];
    const originalFetch = fetch;
    let failedBody: Record<string, unknown> | undefined;
    vi.stubGlobal("fetch", vi.fn(async (path: string, options?: RequestInit) => {
      if (path.endsWith("/resume") && !failedBody) {
        failedBody = JSON.parse(options!.body as string);
        bodies.push({ path: path.replace("/api", ""), body: failedBody! });
        workflows = [{ ...pending, status: "resume_failed", retry_request: failedBody }];
        return new Response(JSON.stringify({ detail: "相关数据已变化" }), { status: 409 });
      }
      return originalFetch(path, options);
    }));
    render(<App />);
    await screen.findByText("正在补充：体重多少？");
    await userEvent.type(screen.getByLabelText("描述饮食或身体状态"), "65公斤");
    await userEvent.click(screen.getByRole("button", { name: "发送记录" }));
    await screen.findByText(/上次补充“65公斤”未完成/);
    await userEvent.click(screen.getByRole("button", { name: "重试原答案" }));
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[1].body).toEqual(failedBody);
  });

  it("原补充正在执行时禁用重试，但允许另开独立记录", async () => {
    workflows = [{ thread_id: "executing", status: "resuming",
      pending_input: { kind: "clarification", interrupt_id: "q-run", question: "体重多少？" },
      retry_request: { request_id: "run-answer", interrupt_id: "q-run", text: "65公斤" } }];
    render(<App />);
    await screen.findByText(/正在处理上次补充/);
    expect((screen.getByRole("button", { name: "重试原答案" }) as HTMLButtonElement).disabled).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "另开一条" }));
    expect((screen.getByLabelText("描述饮食或身体状态") as HTMLTextAreaElement).disabled).toBe(false);
  });

  it("纯本地偏好不会被恢复的模型模式请求静默绕过", async () => {
    localStorage.setItem("health-local-only", "true");
    workflows = [{ thread_id: "model-retry", status: "resume_failed", processing_mode: "model_allowed",
      pending_input: { kind: "clarification", interrupt_id: "q-mode", question: "体重多少？" },
      retry_request: { request_id: "mode-answer", interrupt_id: "q-mode", text: "65公斤", processing_mode: "model_allowed" } }];
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "重试原答案" }));
    await screen.findByText(/若要改为纯本地处理/);
    expect(bodies).toHaveLength(0);
  });

  it("计划恢复沿用上次的拒绝请求，不生成新的确认编号", async () => {
    const payload = { request_id: "previous-rejection", interrupt_id: "q-plan", approved: false };
    workflows = [{ thread_id: "retry-plan", status: "resume_failed",
      pending_input: { kind: "plan_confirmation", interrupt_id: "q-plan", question: "确认计划？" },
      retry_request: payload }];
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "我的计划" }));
    await userEvent.click(await screen.findByRole("button", { name: "重试上次确认" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0].body).toEqual(payload);
  });

  it("计划取消冲突后刷新状态，不显示取消成功", async () => {
    workflows = [{ thread_id: "active-plan", status: "resume_failed",
      pending_input: { kind: "plan_confirmation", interrupt_id: "q-active", question: "确认计划？" },
      retry_request: { request_id: "old-approval", interrupt_id: "q-active", approved: true } }];
    const originalFetch = fetch;
    vi.stubGlobal("fetch", vi.fn(async (path: string, options?: RequestInit) => {
      if (options?.method === "DELETE") {
        workflows = [];
        return new Response(JSON.stringify({ detail: "计划已经处理，无法取消，请刷新状态。" }), { status: 409 });
      }
      return originalFetch(path, options);
    }));
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "我的计划" }));
    await userEvent.click(await screen.findByRole("button", { name: "取消待处理计划" }));
    await screen.findByRole("alert");
    await waitFor(() => expect(screen.queryByRole("button", { name: "重试上次确认" })).toBeNull());
    expect(screen.queryByText("已取消待处理事项")).toBeNull();
  });

  it("发送前显示供应商、模型和各任务的数据范围", async () => {
    healthOverride = { mode: "model", today: "2026-10-20", timezone: "Asia/Shanghai",
      model_name: "test-model", model_provider: "model.test.invalid", local_only: false, scheduler_enabled: false };
    render(<App />);
    const summary = await screen.findByText("将发送给 model.test.invalid · test-model");
    await userEvent.click(summary);
    expect(screen.getByText(/先发送本次输入/).textContent).toContain("历史聊天原文不发送");
  });

  it("恢复的本地流程保持纯本地选择且不能升级", async () => {
    workflows = [{ thread_id: "local-paused", status: "waiting_input", processing_mode: "local_only",
      pending_input: { kind: "clarification", interrupt_id: "local-q", question: "分量多少？" } }];
    render(<App />);
    const toggle = await screen.findByRole("checkbox", { name: "纯本地模式" });
    expect((toggle as HTMLInputElement).checked).toBe(true);
    expect((toggle as HTMLInputElement).disabled).toBe(true);
    await userEvent.type(screen.getByLabelText("描述饮食或身体状态"), "100g");
    await userEvent.click(screen.getByRole("button", { name: "发送记录" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0].body.processing_mode).toBe("local_only");
  });

  it("纯本地选择随请求发送，切换模式后的重试使用新的请求ID", async () => {
    render(<App />);
    const input = await screen.findByLabelText("描述饮食或身体状态");
    await userEvent.type(input, "今天体重65公斤");
    failOnce = true;
    await userEvent.click(screen.getByRole("button", { name: "发送记录" }));
    await screen.findByRole("alert");
    await userEvent.click(screen.getByRole("checkbox", { name: "纯本地模式" }));
    expect(localStorage.getItem("health-local-only")).toBe("true");
    await userEvent.click(screen.getByRole("button", { name: "发送记录" }));
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[0].body.processing_mode).toBe("model_allowed");
    expect(bodies[1].body.processing_mode).toBe("local_only");
    expect(bodies[0].body.request_id).not.toBe(bodies[1].body.request_id);
  });

  it("展示可核验的 AI 处理阶段摘要", async () => {
    chatMessages = [
      {
        id: "assistant-1",
        role: "assistant",
        text: "已保存 1 条记录。",
        cards: [],
        evidence_refs: [],
        created_at: "2026-10-09T08:00:00+08:00",
        process_trace: [
          {
            title: "理解输入",
            detail: "识别为记录健康信息。",
            source: "model",
          },
          {
            title: "保存记录",
            detail: "由本地程序写入健康记录。",
            source: "local",
          },
        ],
        duration_ms: 7330,
      },
    ];
    render(<App />);
    await screen.findByText("AI 处理过程");
    expect(
      screen.getByText("展示可验证的执行摘要，不包含模型私有思维链。"),
    ).toBeTruthy();
    expect(screen.getByText("理解输入")).toBeTruthy();
    expect(screen.getByText("保存记录")).toBeTruthy();
    expect(screen.getByText("用时 7 秒 · 2 个阶段")).toBeTruthy();
  });

  it("展示真实空状态，点击示例只填入，不产生记录", async () => {
    render(<App />);
    await screen.findByText("今天，从一句话开始。");
    expect(screen.getAllByText("先确认个人目标")).toHaveLength(1);
    await userEvent.click(
      screen.getByRole("button", { name: /饮食.*中午吃了一大碗/ }),
    );
    expect(
      (screen.getByLabelText("描述饮食或身体状态") as HTMLTextAreaElement)
        .value,
    ).toContain("牛肉面");
    expect(bodies).toHaveLength(0);
  });

  it("发送失败后保留输入，重试沿用 request_id，避免重复保存", async () => {
    render(<App />);
    const input = await screen.findByLabelText("描述饮食或身体状态");
    await userEvent.type(input, "今天体重65公斤");
    failOnce = true;
    await userEvent.click(screen.getByRole("button", { name: "发送记录" }));
    await screen.findByRole("alert");
    expect((input as HTMLTextAreaElement).value).toBe("今天体重65公斤");
    await userEvent.click(screen.getByRole("button", { name: "发送记录" }));
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[0].body.request_id).toBe(bodies[1].body.request_id);
    await waitFor(() => expect((input as HTMLTextAreaElement).value).toBe(""));
  });

  it("有待追问时仍能另开独立记录，且旧问题保留", async () => {
    workflows = [
      {
        thread_id: "paused",
        status: "waiting_input",
        pending_input: {
          kind: "clarification",
          interrupt_id: "q1",
          question: "大约多少？",
        },
      },
    ];
    render(<App />);
    await screen.findByText("正在补充：大约多少？");
    await userEvent.click(screen.getByRole("button", { name: "另开一条" }));
    await userEvent.type(
      screen.getByLabelText("描述饮食或身体状态"),
      "今天体重65公斤",
    );
    await userEvent.click(screen.getByRole("button", { name: "发送记录" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0].path).toBe("/chat");
  });

  it("计划只有明确点击采用才发送确认", async () => {
    workflows = [
      {
        thread_id: "plan1",
        status: "waiting_input",
        pending_input: {
          kind: "plan_confirmation",
          interrupt_id: "q1",
          question: "确认计划？",
          targets: {
            kcal: 1800,
            protein_g: 100,
            carbs_g: 215,
            fat_g: 60,
            exercise_minutes: 120,
          },
        },
      },
    ];
    render(<App />);
    await screen.findByText("今天，从一句话开始。");
    await userEvent.click(screen.getByRole("button", { name: "我的计划" }));
    await screen.findByText("新的计划已准备好");
    expect(bodies).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "采用计划" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0].path).toBe("/workflows/plan1/resume");
    expect(bodies[0].body.approved).toBe(true);
  });

  it("饮食反馈的可选追问不显示成必须完成的记录", async () => {
    workflows = [{
      thread_id: "feedback",
      status: "waiting_input",
      pending_input: {
        kind: "clarification",
        interrupt_id: "optional1",
        optional: true,
        question: "你觉得口味偏咸。下一餐可以少放盐。如果想复盘，这餐吃了什么？",
        prompt: "如果想复盘，这餐吃了什么？",
      },
    }];
    render(<App />);
    await screen.findByText("可选补充：如果想复盘，这餐吃了什么？");
    expect(screen.queryByText(/可选补充：你觉得口味偏咸/)).toBeNull();
    expect(screen.getByLabelText("描述饮食或身体状态").getAttribute("placeholder")).toBe(
      "可以补充，也可以说不知道或先不记…",
    );
    await userEvent.type(screen.getByLabelText("描述饮食或身体状态"), "不知道");
    await userEvent.click(screen.getByRole("button", { name: "发送记录" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0].path).toBe("/workflows/feedback/resume");
  });

  it("自动填入系统默认并携带估算版本，不自动采用计划", async () => {
    defaultTargets = readyDefaults();
    render(<App />);
    await screen.findByText("今天，从一句话开始。");
    await userEvent.click(screen.getByRole("button", { name: "我的计划" }));
    await waitFor(() => expect((screen.getByLabelText("热量") as HTMLInputElement).value).toBe("2100"));
    expect((screen.getByLabelText("每周运动") as HTMLInputElement).value).toBe("150");
    expect(screen.getByText("系统默认 · 可修改")).toBeTruthy();
    expect(bodies).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "生成计划草案" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0].body.target_source).toBe("system_default");
    expect(bodies[0].body.suggestion_fingerprint).toBe("a".repeat(64));
    expect(bodies[0].body.targets).toEqual(suggestedValues);
  });

  it("用户编辑保留为手动目标，刷新估算不覆盖，并能恢复默认", async () => {
    defaultTargets = readyDefaults();
    render(<App />);
    await screen.findByText("今天，从一句话开始。");
    await userEvent.click(screen.getByRole("button", { name: "我的计划" }));
    const input = screen.getByLabelText("热量") as HTMLInputElement;
    await waitFor(() => expect(input.value).toBe("2100"));
    await userEvent.clear(input);
    await userEvent.type(input, "1900");
    defaultTargets = { ...readyDefaults(), fingerprint: "b".repeat(64), targets: { ...suggestedValues, kcal: 2200 } };
    await userEvent.click(screen.getByRole("button", { name: "生成计划草案" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0].body.target_source).toBe("manual");
    expect(bodies[0].body.suggestion_fingerprint).toBeUndefined();
    await waitFor(() => expect(screen.getByText("手动编辑")).toBeTruthy());
    expect(input.value).toBe("1900");
    await userEvent.click(screen.getByRole("button", { name: "恢复系统默认" }));
    await waitFor(() => expect(input.value).toBe("2200"));
  });

  it("已有生效的自定义目标不会被系统默认覆盖", async () => {
    defaultTargets = readyDefaults();
    storedPlans = [{ id: "custom", status: "active", created_at: "2026-10-10", data: {
      targets: { kcal: 1800, protein_g: 100, carbs_g: 215, fat_g: 60, exercise_minutes: 120 },
      schedule: [], next_meal: "按实际情况安排。", tomorrow_focus: "记录。", evidence_refs: [],
      notes: "手动目标", changes: {}, data_version: 1, needs_confirmation: false, target_source: "manual",
    } }];
    render(<App />);
    await screen.findByText("今天，从一句话开始。");
    await userEvent.click(screen.getByRole("button", { name: "我的计划" }));
    await waitFor(() => expect((screen.getByLabelText("热量") as HTMLInputElement).value).toBe("1800"));
    expect(screen.getByText("当前生效目标")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "生成计划草案" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0].body.targets).toBeUndefined();
    await userEvent.click(screen.getByRole("button", { name: "恢复系统默认" }));
    expect((screen.getByLabelText("热量") as HTMLInputElement).value).toBe("2100");
  });

  it("档案缺失时引导建档，不伪造默认额度", async () => {
    render(<App />);
    await screen.findByText("今天，从一句话开始。");
    await userEvent.click(screen.getByRole("button", { name: "我的计划" }));
    expect((screen.getByLabelText("热量") as HTMLInputElement).value).toBe("");
    await userEvent.click(screen.getByRole("button", { name: "建立或补全基础档案" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(bodies).toHaveLength(0);
  });
});
