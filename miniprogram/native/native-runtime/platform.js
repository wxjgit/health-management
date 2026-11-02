function call(method, options = {}) {
  return new Promise((resolve, reject) =>
    wx[method]({ ...options, success: resolve, fail: reject }),
  );
}
function notify(name) {
  const state = require("./state");
  if (name === "auth-cleared") state.clearPrivateState();
  else if (name === "changed") state.changed();
}
module.exports = {
  call,
  notify,
  env: wx.env,
  request: (options) => call("request", options),
  reLaunch: (options) => call("reLaunch", options),
  shareFileMessage: (options) => call("shareFileMessage", options),
  getStorageSync: (key) => wx.getStorageSync(key),
  setStorageSync: (key, value) => wx.setStorageSync(key, value),
  removeStorageSync: (key) => wx.removeStorageSync(key),
  getCurrentPages: () => getCurrentPages(),
  eventCenter: { trigger: notify },
  getFileSystemManager() {
    const fs = wx.getFileSystemManager(),
      result = {};
    for (const method of ["unlink", "access", "writeFile", "copyFile"])
      result[method] = (options) =>
        new Promise((resolve, reject) =>
          fs[method]({ ...options, success: resolve, fail: reject }),
        );
    return result;
  },
};
