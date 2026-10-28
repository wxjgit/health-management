import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { beforeEach, expect, it, vi } from "vitest";

type TabBar = {
  data: { selected: number; items: { path: string }[] };
  switching?: boolean;
  setData: (value: { selected: number }) => void;
  syncRoute: () => void;
  switchPage: (event: {
    currentTarget: { dataset: { index: number } };
  }) => void;
};
let route: string;
let bar: TabBar;
let show: () => void;
const switchTab = vi.fn();
const showToast = vi.fn();
beforeEach(() => {
  vi.useFakeTimers();
  route = "pages/chat/index";
  switchTab.mockReset();
  showToast.mockReset();
  runInNewContext(
    readFileSync(
      new URL("../src/custom-tab-bar/index.js", import.meta.url),
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
          setData: (value) => Object.assign(bar.data, value),
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
const tap = (index: number) =>
  {
    bar.switchPage({ currentTarget: { dataset: { index } } });
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
