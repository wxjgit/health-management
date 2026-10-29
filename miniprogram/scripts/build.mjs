import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

const development = process.argv.includes("--development");
const result = spawnSync(process.execPath, [resolve("node_modules/@tarojs/cli/bin/taro"), "build", "--type", "weapp",
  ...(process.argv.includes("--watch") ? ["--watch"] : [])], {
  stdio: "inherit", env: { ...process.env, APP_ENV: development ? "development" : "production", NODE_ENV: development ? "development" : "production" },
});
process.exit(result.status ?? 1);
