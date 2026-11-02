import { createRequire } from "node:module";
import { expect, it } from "vitest";
const require = createRequire(import.meta.url);
const { compileWeChatModule } = require("../scripts/wechat-scope.cjs");

it("复现微信注入 confirm 参数后与模块常量的命名冲突", () => {
  expect(() =>
    compileWeChatModule("const confirm = () => true;", "repro.js"),
  ).toThrow(/confirm.*already been declared/);
});

it("检查解构导入和其他微信注入变量的冲突", () => {
  expect(() =>
    compileWeChatModule('const { confirm } = require("./ui");', "page.js"),
  ).toThrow(/confirm.*already been declared/);
  expect(() =>
    compileWeChatModule('const { history } = require("./ui");', "page.js"),
  ).toThrow(/history.*already been declared/);
});

it("允许业务改名，同时保留微信返回值里的 confirm 属性", () => {
  expect(() =>
    compileWeChatModule(
      "const confirmAction = async () => (await wx.showModal({})).confirm;",
      "ui.js",
    ),
  ).not.toThrow();
});
