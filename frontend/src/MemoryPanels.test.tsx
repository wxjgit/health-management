import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  DerivedNote,
  ExpiredContexts,
  FactManager,
  RevisionHistory,
} from "./MemoryPanels";
import type { Fact } from "./types";

const fact: Fact = {
  id: "oat",
  content: "我不喜欢燕麦",
  source: "user",
  type: "food_preference",
  subject: "燕麦",
  value: "dislike",
  version: 3,
  source_kind: "user_explicit",
  confirmation: "confirmed",
  lifecycle: "active",
  valid_from: "2026-10-20",
  valid_until: null,
  supersedes: null,
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify([fact]), { status: 200 })),
  );
});

describe("长期信息管理", () => {
  it("修改时提交当前版本和有效期", async () => {
    const mutate = vi.fn(async () => true);
    render(
      <FactManager
        facts={[fact]}
        busy={false}
        mutate={mutate}
        remove={vi.fn()}
      />,
    );
    await userEvent.click(await screen.findByRole("button", { name: "修改" }));
    await userEvent.clear(screen.getByLabelText("长期信息内容"));
    await userEvent.type(
      screen.getByLabelText("长期信息内容"),
      "我现在可以接受燕麦",
    );
    await userEvent.selectOptions(screen.getByLabelText("事实取值"), "accept");
    await userEvent.click(screen.getByRole("button", { name: "保存长期信息" }));
    expect(mutate).toHaveBeenCalledWith(
      "/facts/oat",
      "PUT",
      expect.objectContaining({
        expected_version: 3,
        content: "我现在可以接受燕麦",
        value: "accept",
        valid_from: "2026-10-20",
        valid_until: null,
      }),
    );
    expect(screen.queryByLabelText("长期信息内容")).toBeNull();
  });

  it("版本冲突导致保存失败时保留编辑内容", async () => {
    render(
      <FactManager
        facts={[fact]}
        busy={false}
        mutate={vi.fn(async () => false)}
        remove={vi.fn()}
      />,
    );
    await userEvent.click(await screen.findByRole("button", { name: "修改" }));
    await userEvent.type(screen.getByLabelText("长期信息内容"), "，需要核对");
    await userEvent.click(screen.getByRole("button", { name: "保存长期信息" }));
    expect(
      (screen.getByLabelText("长期信息内容") as HTMLTextAreaElement).value,
    ).toContain("需要核对");
  });

  it("撤回提交状态变更，删除进入独立删除流程", async () => {
    const mutate = vi.fn(async () => true),
      remove = vi.fn();
    render(
      <FactManager
        facts={[fact]}
        busy={false}
        mutate={mutate}
        remove={remove}
      />,
    );
    await userEvent.click(await screen.findByRole("button", { name: "撤回" }));
    expect(mutate).toHaveBeenCalledWith("/facts/oat", "PATCH", {
      action: "retract",
      expected_version: 3,
    });
    expect(remove).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "删除长期信息" }));
    expect(remove).toHaveBeenCalledWith(fact);
  });

  it("冲突只能通过明确选择处理", async () => {
    const conflicted = { ...fact, confirmation: "conflict" };
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () => new Response(JSON.stringify([conflicted]), { status: 200 }),
      ),
    );
    const mutate = vi.fn(async () => true);
    render(
      <FactManager
        facts={[conflicted]}
        busy={false}
        mutate={mutate}
        remove={vi.fn()}
      />,
    );
    await screen.findByText(/存在冲突，待选择/);
    expect(mutate).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "采用这条" }));
    expect(mutate).toHaveBeenCalledWith("/facts/oat", "PATCH", {
      action: "confirm",
      expected_version: 3,
    });
  });
});

describe("版本与过期提示", () => {
  it("修订内容更新后，旧的异步响应不能恢复旧历史", async () => {
    let finish!: (response: Response) => void;
    const first = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockReturnValueOnce(first)
        .mockResolvedValueOnce(
          new Response(
            JSON.stringify([
              {
                version: 2,
                raw_text: "新版本内容",
                reason: "纠正记录",
                created_at: "2026-10-26T00:00:00Z",
              },
            ]),
            { status: 200 },
          ),
        ),
    );
    const view = render(<RevisionHistory eventId="meal" version={1} />);
    await userEvent.click(screen.getByText("修订历史（1 个版本）"));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    view.rerender(<RevisionHistory eventId="meal" version={2} />);
    await screen.findByText("新版本内容");
    await act(async () => {
      finish(
        new Response(
          JSON.stringify([
            {
              version: 1,
              raw_text: "旧版本内容",
              reason: "首次记录",
              created_at: "2026-10-25T00:00:00Z",
            },
          ]),
          { status: 200 },
        ),
      );
    });
    expect(screen.queryByText("旧版本内容")).toBeNull();
  });

  it("过期事项仅提供重新开始，并说明已保存记录", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify([
              {
                thread_id: "expired",
                reply: "此事项已过期",
                saved_record_ids: ["saved"],
                status: "expired",
              },
            ]),
            { status: 200 },
          ),
      ),
    );
    const restart = vi.fn();
    render(<ExpiredContexts refreshKey={0} restart={restart} />);
    await userEvent.click(await screen.findByText("最近有 1 个已过期事项"));
    expect(screen.getByText(/已保留 1 条记录/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "继续补充" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "开始新的记录" }));
    expect(restart).toHaveBeenCalledOnce();
  });

  it("派生结果展示失效原因和原计算版本", async () => {
    render(
      <DerivedNote
        memory={{
          status: "needs_review",
          invalid_reason: "source_changed",
          algorithm_version: "review-v1",
          window: ["2026-10-20", "2026-10-26"],
        }}
      />,
    );
    expect(screen.getByRole("status").textContent).toContain(
      "依据的数据已变化",
    );
    await userEvent.click(screen.getByText("生成依据与版本"));
    expect(screen.getByText("方法版本：review-v1")).toBeTruthy();
  });
});
