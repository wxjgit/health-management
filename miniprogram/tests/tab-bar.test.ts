import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { beforeEach, expect, it, vi } from "vitest";

type TabBar = {
  data: { selected: number; items: { path: string }[] };
  switching?: boolean;
  setData: (value: { selected: number }, callback?: () => void) => void;
  syncLens: (selected: number) => void;
  selectComponent: (selector: string) => {
    syncSelection: (selected: number) => void;
  };
  syncRoute: () => void;
  setActiveRoute: (route: string) => void;
  switchPage: (event: { detail: { index: number } }) => void;
};
let route: string;
let bar: TabBar;
let show: () => void;
const switchTab = vi.fn();
const showToast = vi.fn();
const syncSelection = vi.fn();
beforeEach(() => {
  vi.useFakeTimers();
  route = "pages/chat/index";
  switchTab.mockReset();
  showToast.mockReset();
  syncSelection.mockReset();
  runInNewContext(
    readFileSync(
      new URL("../native/custom-tab-bar/index.js", import.meta.url),
      "utf8",
    ),
    {
      Component: (definition: {
        data: TabBar["data"];
        methods: Omit<TabBar, "data" | "setData">;
        lifetimes: { attached: () => void };
        pageLifetimes: { show: () => void };
      }) => {
        bar = {
          data: definition.data,
          ...definition.methods,
          setData: (value, callback) => {
            Object.assign(bar.data, value);
            callback?.();
          },
          selectComponent: () => ({ syncSelection }),
        };
        show = () => definition.pageLifetimes.show.call(bar);
        definition.lifetimes.attached.call(bar);
      },
      getCurrentPages: () => [{ route }],
      wx: { switchTab, showToast },
      setTimeout,
      clearTimeout,
    },
  );
});
const tap = (index: number) => {
  bar.switchPage({ detail: { index } });
  vi.advanceTimersByTime(220);
};

it("导航高亮跟随实际页面，重新显示时同步", () => {
  expect(bar.data.selected).toBe(0);
  route = "pages/settings/index";
  show();
  expect(bar.data.selected).toBe(3);
});
it("点击导航使用 switchTab，切换中阻止重复点击", () => {
  tap(2);
  tap(2);
  expect(switchTab).toHaveBeenCalledTimes(1);
  expect(switchTab.mock.calls[0][0].url).toBe("/pages/insights/index");
  route = "pages/insights/index";
  switchTab.mock.calls[0][0].complete();
  expect(bar.data.selected).toBe(2);
});
it("失败时恢复实际页面高亮，并允许重试", () => {
  tap(3);
  switchTab.mock.calls[0][0].fail();
  switchTab.mock.calls[0][0].complete();
  expect(showToast).toHaveBeenCalledOnce();
  expect(bar.data.selected).toBe(0);
  tap(3);
  expect(switchTab).toHaveBeenCalledTimes(2);
});
it("当前页和无效索引不发起切换", () => {
  tap(0);
  tap(-1);
  tap(20);
  expect(switchTab).not.toHaveBeenCalled();
});

it("页面栈暂时仍是复盘时，当前我的页面高亮由所属页面决定", () => {
  route = "pages/insights/index";
  bar.setActiveRoute("pages/settings/index");
  show();
  expect(bar.data.selected).toBe(3);
  bar.syncRoute();
  expect(bar.data.selected).toBe(3);
});
it("忽略非底部导航页面传入的路由", () => {
  bar.setActiveRoute("pages/settings/index");
  bar.setActiveRoute("pages/today/index");
  expect(bar.data.selected).toBe(3);
});

it("快速切换时拒绝的手势不会把透镜留在错误项", () => {
  tap(2);
  syncSelection.mockClear();
  tap(3);
  expect(switchTab).toHaveBeenCalledTimes(1);
  expect(syncSelection).toHaveBeenLastCalledWith(2);
});
it("页面选中值正确时仍会校正子组件的残留动画", () => {
  bar.setActiveRoute("pages/settings/index");
  syncSelection.mockClear();
  show();
  expect(syncSelection).toHaveBeenLastCalledWith(3);
});
