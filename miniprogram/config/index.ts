import { defineConfig } from "@tarojs/cli";

export default defineConfig({
  projectName: "qingji-miniprogram",
  date: "2026-10-27",
  designWidth: 750,
  deviceRatio: { 750: 1 },
  sourceRoot: "src",
  outputRoot: "dist",
  framework: "react",
  compiler: "webpack5",
  copy: { patterns: [{ from: "src/assets", to: "dist/assets" }], options: {} },
  mini: {},
});
