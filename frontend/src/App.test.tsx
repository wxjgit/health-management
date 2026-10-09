import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";

let bodies: { path: string; body: Record<string, unknown> }[];
let failOnce: boolean;
let workflows: unknown[];
let records: unknown[];
let chatMessages: unknown[];

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

beforeEach(() => {
  bodies = [];
  failOnce = false;
  workflows = [];
  records = [];
  chatMessages = [];
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
        "/health": {
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
        "/plans": [],
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
});
