import { createRequire } from "node:module";
import { watch } from "node:fs";
import { resolve } from "node:path";
const require = createRequire(import.meta.url);
const { emitNativePages } = require("./native-pages.cjs");
const { buildEnvironment } = require("./environment.cjs");
const root = resolve(".");
const environment = buildEnvironment({
  ...process.env,
  APP_ENV: process.argv.includes("--development")
    ? "development"
    : "production",
});
function build() {
  emitNativePages({ root, environment });
  console.log("微信原生小程序构建完成：dist/");
}
build();
if (process.argv.includes("--watch")) {
  let timer;
  watch(resolve(root, "native"), { recursive: true }, () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      try {
        build();
      } catch (error) {
        console.error(error.message);
      }
    }, 150);
  });
  console.log("正在监听 native/ 的修改…");
}
