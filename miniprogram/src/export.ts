import Taro from "@tarojs/taro";
import { accountKey, api } from "./api";
import { exportPaths } from "./privacy";

export async function exportMyData() {
  const owner = accountKey();
  if (!owner) throw new Error("请先登录。");
  const data = await api<object>("/data/export");
  const filePath = `${Taro.env.USER_DATA_PATH}/qingji-export-${Date.now()}.json`;
  await Taro.getFileSystemManager().writeFile({
    filePath,
    data: JSON.stringify(data, null, 2),
    encoding: "utf8",
  });
  if (owner !== accountKey()) {
    await Taro.getFileSystemManager().unlink({ filePath });
    throw new Error("账号已变化，导出已取消。");
  }
  Taro.setStorageSync("export-files", [...exportPaths(), filePath]);
  await Taro.shareFileMessage({ filePath, fileName: "轻记-我的健康记录.json" });
}
