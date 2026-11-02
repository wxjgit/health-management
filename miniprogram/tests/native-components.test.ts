import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { createHarness } from "./native-harness";
let h: any;
beforeEach(() => (h = createHarness()));
afterEach(() => h.cleanup());
it("授权组件不会通过模型不可用的开关增加授权", () => {
  const c = h.component("agreements", {
    policy: { model_available: false },
    value: { privacy: true, health: true, model: false },
  });
  c.onToggle({
    currentTarget: { dataset: { key: "model" } },
    detail: { value: true },
  });
  expect(c.triggerEvent).not.toHaveBeenCalled();
  c.onToggle({
    currentTarget: { dataset: { key: "health" } },
    detail: { value: false },
  });
  expect(c.triggerEvent).toHaveBeenCalledWith("change", {
    privacy: true,
    health: false,
    model: false,
  });
});
it("原生字段将选择值回传给正确的表单分组", () => {
  const c = h.component("field", {
    name: "sex",
    group: "profileForm",
    type: "select",
    options: [
      { value: "female", label: "女性" },
      { value: "other", label: "其他" },
    ],
  });
  c.change({ detail: { value: 1 } });
  expect(c.triggerEvent).toHaveBeenCalledWith("change", {
    name: "sex",
    group: "profileForm",
    value: "other",
  });
});
it("弹窗忙碌时不派发关闭事件", () => {
  const c = h.component("sheet", { busy: true });
  c.close();
  expect(c.triggerEvent).not.toHaveBeenCalled();
  c.data.busy = false;
  c.close();
  expect(c.triggerEvent).toHaveBeenCalledWith("close");
});
it("记录纠错只保存选中事件并跳转记录页", () => {
  const c = h.component("event-card", {
    event: { id: "e", raw_text: "原文", kind: "weight", data: {} },
  });
  c.correct();
  expect(h.storage.get("correction-event").id).toBe("e");
  expect(h.wx.switchTab).toHaveBeenCalledWith(
    expect.objectContaining({ url: "/pages/chat/index" }),
  );
});
