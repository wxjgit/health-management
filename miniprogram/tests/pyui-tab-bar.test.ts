import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

// Run the native component without a WeChat renderer; exercise its actual gesture handlers.
function mount(tab = 0) {
  let definition: any;
  runInNewContext(
    readFileSync(
      new URL(
        "../native/vendor/pyui/pyui-tab-bar/pyui-tab-bar.js",
        import.meta.url,
      ),
      "utf8",
    ),
    {
      Component: (value: any) => {
        definition = value;
      },
      Date,
      setInterval,
      clearInterval,
    },
  );
  const instance: any = {
    ...definition.methods,
    data: { ...definition.data, tab, slotCount: 4 },
    triggerEvent: vi.fn(),
    setData(value: object) {
      Object.assign(this.data, value);
    },
    createSelectorQuery() {
      return {
        select() {
          return this;
        },
        boundingClientRect(callback: (rect: object) => void) {
          callback({ left: 12, width: 300 });
          return this;
        },
        exec() {},
      };
    },
  };
  definition.lifetimes.attached.call(instance);
  return instance;
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
});

it("进入我的页面时透镜从当前项开始，不从记录页滑入", () => {
  const bar = mount(3);
  expect(bar.data.progress).toBe(3);
});

it("四项导航拖到最右侧，抬手仅派发一次我的索引", () => {
  const bar = mount();
  bar.onTouchStart({ touches: [{ clientX: 12 + 7 + bar.slotW * 0.5 }] });
  bar.onTouchMove({ touches: [{ clientX: 12 + 7 + bar.slotW * 3.5 }] });
  bar.onTouchEnd();
  expect(
    bar.triggerEvent.mock.calls.filter(
      (call: unknown[]) => call[0] === "change",
    ),
  ).toEqual([["change", { index: 3 }]]);
});

it("拖动取消后不切换页面，透镜返回当前项", () => {
  const bar = mount(1);
  bar.onTouchStart({ touches: [{ clientX: 12 + 7 + bar.slotW * 1.5 }] });
  bar.onTouchMove({ touches: [{ clientX: 12 + 7 + bar.slotW * 3.5 }] });
  bar.onTouchCancel();
  vi.advanceTimersByTime(350);
  expect(bar.triggerEvent).not.toHaveBeenCalled();
  expect(bar.data.progress).toBe(1);
});

it("路由校正会中止旧透镜动画，后续帧不会覆盖我的高亮", () => {
  const bar = mount(3);
  bar.animateProgress(2, 450, true);
  vi.advanceTimersByTime(100);
  bar.syncSelection(3);
  vi.advanceTimersByTime(600);
  expect(bar.data.progress).toBe(3);
  expect(bar.data.pressScale).toBe(1);
});
