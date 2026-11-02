const { api, baseUrl } = require("./api");
const { call, notify } = require("./platform");

async function clearAccountData() {
  const root = `${wx.env.USER_DATA_PATH}/qingji-export-`;
  const fs = wx.getFileSystemManager();
  const fileCall = (method, options) =>
    new Promise((resolve, reject) =>
      fs[method]({ ...options, success: resolve, fail: reject }),
    );
  for (const path of wx.getStorageSync("export-files") || []) {
    if (!path.startsWith(root) || !/^\d+\.json$/.test(path.slice(root.length)))
      continue;
    try {
      await fileCall("unlink", { filePath: path });
    } catch {
      let exists = false;
      try {
        await fileCall("access", { path });
        exists = true;
      } catch {}
      if (exists) throw new Error("无法清理设备内导出副本，请先清理后重试。");
    }
  }
  for (const key of [
    "export-files",
    "auth-session",
    "pending-operation",
    "pending-submission",
    "deletion-state",
    "correction-event",
    "evidence-event",
  ])
    wx.removeStorageSync(key);
  notify("auth-cleared");
}

async function wechatLogin(consent) {
  await clearAccountData();
  const origin = baseUrl();
  const result = await call("login");
  if (!result.code) throw new Error("未取得微信登录凭证，请重试。");
  const session = await api("/auth/wechat/login", "POST", {
    code: result.code,
    ...consent,
  });
  if (baseUrl() !== origin) throw new Error("服务地址已变化，请重新登录。");
  wx.setStorageSync("auth-session", {
    base: origin,
    token: session.access_token,
    user: session.user,
    expiresAt: session.expires_at,
  });
}
module.exports = { wechatLogin, clearAccountData };
