"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.operation =
  exports.pendingOperation =
  exports.privacyGeneration =
  exports.subscribeReset =
  exports.subscribe =
  exports.changed =
    void 0;
exports.clearPrivateState = clearPrivateState;
exports.privacyBlocked = privacyBlocked;
exports.executeOperation = executeOperation;
exports.mutate = mutate;
exports.exclusive = exclusive;
exports.acknowledgeOperation = acknowledgeOperation;
const { api, ApiError, baseUrl, accountKey, errorText } = require("./api");
const {
  deletionState,
  removeExports,
  saveDeletionState,
} = require("./privacy");
const listeners = new Set(),
  privateListeners = new Set();
let generation = 0,
  writing = false;
const changed = () => listeners.forEach((fn) => fn());
exports.changed = changed;
const subscribe = (fn) => {
  listeners.add(fn);
  return () => listeners.delete(fn);
};
exports.subscribe = subscribe;
const subscribeReset = (fn) => {
  privateListeners.add(fn);
  return () => privateListeners.delete(fn);
};
exports.subscribeReset = subscribeReset;
function clearPrivateState() {
  generation++;
  wx.removeStorageSync("correction-event");
  wx.removeStorageSync("evidence-event");
  privateListeners.forEach((fn) => fn());
}
const privacyGeneration = () => generation;
exports.privacyGeneration = privacyGeneration;
function privacyBlocked() {
  var _a, _b;
  const d = deletionState();
  return (
    ((d === null || d === void 0 ? void 0 : d.base) === baseUrl() &&
      ((_a = d.result) === null || _a === void 0 ? void 0 : _a.state) ===
        "pending") ||
    ((_b = (0, exports.pendingOperation)()) === null || _b === void 0
      ? void 0
      : _b.path) === "/deletions"
  );
}
const operationKey = "pending-operation";
const pendingOperation = () => {
  const pending = wx.getStorageSync(operationKey);
  const owner = accountKey();
  return owner &&
    (pending === null || pending === void 0 ? void 0 : pending.owner) === owner
    ? pending
    : null;
};
exports.pendingOperation = pendingOperation;
const operation = (path, body, label) => {
  const owner = accountKey();
  if (!owner) throw new Error("请先微信登录。");
  return { base: baseUrl(), owner, path, body, label };
};
exports.operation = operation;
async function executeOperation(next) {
  if (next.base !== baseUrl())
    throw new Error("待重试请求属于另一服务，请切回原地址。");
  if (!accountKey() || next.owner !== accountKey())
    throw new Error("待处理请求属于另一账号，不能在当前账号下重试。");
  if (writing) throw new Error("已有操作正在执行，请稍候。");
  if (privacyBlocked() && next.path !== "/deletions")
    throw new Error("请先完成隐私清理。");
  const previous = (0, exports.pendingOperation)();
  if (previous && JSON.stringify(previous) !== JSON.stringify(next))
    throw new Error("请先处理待核对的原请求。");
  writing = true;
  try {
    if (next.body.processing_mode === "model_allowed") {
      const health = await api("/health");
      if (health.local_only)
        throw new Error(
          "服务已强制纯本地模式，不能重发原模型请求。请核对服务端结果。",
        );
    }
    wx.setStorageSync(operationKey, next);
    if (next.path === "/deletions") {
      await removeExports();
      clearPrivateState();
    }
    (0, exports.changed)();
    const result = await api(next.path, "POST", next.body);
    if (next.path === "/deletions") {
      const state = deletionState();
      if (state)
        saveDeletionState(
          Object.assign(Object.assign({}, state), { result: result }),
        );
      clearPrivateState();
    }
    wx.removeStorageSync(operationKey);
    return result;
  } catch (reason) {
    if (next.path === "/deletions") clearPrivateState();
    if (reason instanceof ApiError && reason.preview) {
      const state = deletionState();
      if (state)
        saveDeletionState(
          Object.assign(Object.assign({}, state), {
            preview: reason.preview,
            requestId: "",
            result: undefined,
          }),
        );
      wx.removeStorageSync(operationKey);
    }
    if (
      reason instanceof ApiError &&
      [400, 404, 410, 413, 415, 422, 428].includes(reason.status)
    )
      wx.removeStorageSync(operationKey);
    throw reason;
  } finally {
    writing = false;
    (0, exports.changed)();
  }
}
async function mutate(path, method, body) {
  return exclusive(
    () => api(path, method, body),
    path.startsWith("/deletions/"),
  );
}
async function exclusive(task, privacyRecovery = false) {
  if (writing || (0, exports.pendingOperation)())
    throw new Error("请先完成或核对待处理请求。");
  if (privacyBlocked() && !privacyRecovery)
    throw new Error("请先完成隐私清理。");
  writing = true;
  try {
    return await task();
  } finally {
    writing = false;
    (0, exports.changed)();
  }
}
function acknowledgeOperation() {
  var _a;
  if (writing) throw new Error("操作仍在执行，请稍候。");
  if (
    ((_a = (0, exports.pendingOperation)()) === null || _a === void 0
      ? void 0
      : _a.path) === "/deletions"
  )
    throw new Error("删除请求需要在隐私清理中核对结果。");
  wx.removeStorageSync(operationKey);
  (0, exports.changed)();
}
