import Taro from "@tarojs/taro";
import { accountKey } from "./api";
import type { DeletionPreview, DeletionResult } from "./types";

export type DeletionState = {
  owner?: string;
  base: string;
  preview: DeletionPreview;
  requestId: string;
  result?: DeletionResult;
};
export const deletionState = () => {
  const state = Taro.getStorageSync<DeletionState>("deletion-state");
  return accountKey() && state?.owner === accountKey() ? state : null;
};
export const saveDeletionState = (value: DeletionState) => {
  const owner = accountKey();
  if (!owner) throw new Error("请先微信登录。");
  Taro.setStorageSync("deletion-state", { ...value, owner });
};
export const exportPaths = () =>
  Taro.getStorageSync<string[]>("export-files") || [];
export async function removeExports() {
  const root = `${Taro.env.USER_DATA_PATH}/qingji-export-`;
  for (const path of exportPaths()) {
    if (!path.startsWith(root) || !/^\d+\.json$/.test(path.slice(root.length)))
      continue;
    try {
      await Taro.getFileSystemManager().unlink({ filePath: path });
    } catch {
      let exists = false;
      try {
        await Taro.getFileSystemManager().access({ path });
        exists = true;
      } catch {}
      if (exists)
        throw new Error("无法清理设备内导出副本，请先在我的页面清理后重试。");
    }
  }
  Taro.removeStorageSync("export-files");
}
