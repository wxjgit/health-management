// utils/pyui.js — 全局自定义 overlay 管理器（toast / loading / modal）
// 页面经 pyui-container（或 index 宿主页显式）内嵌 <py-overlay/>，attached 时注册进栈；
// 本模块持栈顶实例分发调用。API 与 wx.showToast/showLoading/showModal **参数同构**，
// 机械替换调用点零改动（wx.xxx → pyui.xxx）。
//
// 为什么不用 wx 原生：原生 toast 图标不可定制、loading 转圈不可定制、modal 白底蓝钮
// 与玻璃设计语言冲突。自定义版：暗玻璃胶囊 toast / 三彩球 loading / 玻璃卡 modal。

const stack = [];

function register(inst) {
    if (stack.indexOf(inst) === -1) stack.push(inst);
}

function unregister(inst) {
    const i = stack.indexOf(inst);
    if (i > -1) stack.splice(i, 1);
}

function top() {
    return stack.length ? stack[stack.length - 1] : null;
}

// ── toast：{title, icon:'none'|'success'|'error'|'loading', duration, mask, success} ──
function showToast(opts) {
    const o = opts || {};
    const inst = top();
    if (inst) {
        inst.showToast(o);
    } else {
        wx.showToast(o); // 无 overlay 实例兜底（极端时序：页面 attach 前）
    }
    if (typeof o.success === 'function') o.success();
    if (typeof o.complete === 'function') o.complete();
}

function hideToast() {
    const inst = top();
    if (inst) inst.hideToast();
    wx.hideToast();
}

// ── loading：{title, mask} / hideLoading()  —— 三彩球动画 ──
function showLoading(opts) {
    const inst = top();
    if (inst) {
        inst.showLoading(opts || {});
    } else {
        wx.showLoading(opts || {});
    }
}

function hideLoading() {
    const inst = top();
    if (inst) inst.hideLoading();
    wx.hideLoading();
}

// ── modal：{title, content, showCancel, cancelText, confirmText, success, complete}
//    success({confirm, cancel}) 与 wx 同构 ──
function showModal(opts) {
    const inst = top();
    if (inst) {
        inst.showModal(opts || {});
    } else {
        // 无 overlay 实例兜底：回落原生，保证流程不断（stub 页/极端时序）
        wx.showModal(opts || {});
    }
}

module.exports = { register, unregister, showToast, hideToast, showLoading, hideLoading, showModal };
