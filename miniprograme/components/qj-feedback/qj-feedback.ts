// components/qj-feedback/qj-feedback.ts
// 薄壳组件：仅挂载 <pyui-overlay/>（命令式 toast/loading/modal 实体）。
// 业务统一走 runtime/feedback.ts（pyui.showToast / pyui.showModal），不直接操作本组件。
Component({
  options: { addGlobalClass: true },
});
