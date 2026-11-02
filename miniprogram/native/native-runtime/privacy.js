"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.exportPaths =
  exports.saveDeletionState =
  exports.deletionState =
    void 0;
exports.removeExports = removeExports;
const native = require("./platform");
const api_1 = require("./api");
const deletionState = () => {
  const state = native.getStorageSync("deletion-state");
  return (0, api_1.accountKey)() &&
    (state === null || state === void 0 ? void 0 : state.owner) ===
      (0, api_1.accountKey)()
    ? state
    : null;
};
exports.deletionState = deletionState;
const saveDeletionState = (value) => {
  const owner = (0, api_1.accountKey)();
  if (!owner) throw new Error("请先微信登录。");
  native.setStorageSync(
    "deletion-state",
    Object.assign(Object.assign({}, value), { owner }),
  );
};
exports.saveDeletionState = saveDeletionState;
const exportPaths = () => native.getStorageSync("export-files") || [];
exports.exportPaths = exportPaths;
async function removeExports() {
  const root = `${native.env.USER_DATA_PATH}/qingji-export-`;
  for (const path of (0, exports.exportPaths)()) {
    if (!path.startsWith(root) || !/^\d+\.json$/.test(path.slice(root.length)))
      continue;
    try {
      await native.getFileSystemManager().unlink({ filePath: path });
    } catch (_a) {
      let exists = false;
      try {
        await native.getFileSystemManager().access({ path });
        exists = true;
      } catch (_b) {}
      if (exists)
        throw new Error("无法清理设备内导出副本，请先在我的页面清理后重试。");
    }
  }
  native.removeStorageSync("export-files");
}
