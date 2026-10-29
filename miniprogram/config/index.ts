import { defineConfig } from "@tarojs/cli";

import { buildEnvironment } from "./environment";
const environment = buildEnvironment(process.env);

export default defineConfig({
  defineConstants: {
    API_ORIGIN: JSON.stringify(environment.apiOrigin),
    DEVELOPMENT_BUILD: JSON.stringify(environment.development),
  },
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
