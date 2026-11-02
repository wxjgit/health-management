"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.exportMyData = exportMyData;
const native = require("./platform");
const api_1 = require("./api");
const privacy_1 = require("./privacy");
async function exportMyData() {
  const owner = (0, api_1.accountKey)();
  if (!owner) throw new Error("请先登录。");
  const data = await (0, api_1.api)("/data/export");
  const filePath = `${native.env.USER_DATA_PATH}/qingji-export-${Date.now()}.json`;
  await native.getFileSystemManager().writeFile({
    filePath,
    data: JSON.stringify(data, null, 2),
    encoding: "utf8",
  });
  if (owner !== (0, api_1.accountKey)()) {
    await native.getFileSystemManager().unlink({ filePath });
    throw new Error("账号已变化，导出已取消。");
  }
  native.setStorageSync("export-files", [
    ...(0, privacy_1.exportPaths)(),
    filePath,
  ]);
  await native.shareFileMessage({
    filePath,
    fileName: "轻记-我的健康记录.json",
  });
}
