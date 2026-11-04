// runtime/feedback.ts — 全局反馈助手（PyUI 命令式）
// toast() 替代 wx.showToast、confirm() 替代 wx.showModal，实现走 PyUI 命令式管理器
// common/pyui.js：暗玻璃胶囊 toast / 三彩球 loading / 玻璃卡 modal（iOS 26 解剖）。
// 实体 = 每页挂载的 <pyui-overlay/>（attached 时注册进实例栈，栈顶分发）。
// pyui.js 为 vendor JS 模块（无 TS 声明），用 require 引入
const pyui = require('../vendor/pyui/common/pyui');

export type ToastTheme = 'info' | 'success' | 'warning' | 'error';

// 主题 → pyui-overlay toast icon（overlay 支持 none/success/error/loading）
function toIcon(theme: ToastTheme): string {
  if (theme === 'success') return 'success';
  if (theme === 'error') return 'error';
  return 'none';
}

export function toast(content: string, theme: ToastTheme = 'info', duration = 2200): void {
  pyui.showToast({ title: content, icon: toIcon(theme), duration });
}

// confirm 走 PyUI 玻璃卡 modal（参数与 wx.showModal 同构），返回 Promise<boolean>
export function confirm(opts: {
  title?: string; content: string; confirmText?: string; cancelText?: string;
  destructive?: boolean; showCancel?: boolean;
}): Promise<boolean> {
  return new Promise((resolve) => {
    pyui.showModal({
      title: opts.title || '',
      content: opts.content,
      confirmText: opts.confirmText || '确认',
      cancelText: opts.cancelText || '取消',
      showCancel: opts.showCancel !== false,
      destructive: !!opts.destructive,
      success: (res: any) => resolve(!!(res && res.confirm)),
      fail: () => resolve(false),
    });
  });
}

// 语义化快捷方法
export const notify = {
  success: (m: string) => toast(m, 'success'),
  error: (m: string) => toast(m, 'error'),
  warn: (m: string) => toast(m, 'warning'),
  info: (m: string) => toast(m, 'info'),
};

// 统一错误提示：从 ApiError 提取可读信息
export function showApiError(err: any): void {
  const msg = (err && err.message) ? err.message : '操作失败，请重试';
  const theme: ToastTheme = (err && err.status === 0) ? 'warning' : 'error';
  toast(msg, theme);
}
