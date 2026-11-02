const { api, baseUrl, requestId } = require("../../native-runtime/api");
const state = require("../../native-runtime/state");
const {
  deletionState,
  saveDeletionState,
} = require("../../native-runtime/privacy");
const { registerPage } = require("../../native-runtime/page");
const { confirmAction } = require("../../native-runtime/ui");
const names = {
  event: "健康记录",
  fact: "长期信息",
  message: "聊天消息",
  workflow: "流程及进度",
  plan: "计划",
  insight: "规律",
  review: "复盘",
  notification: "通知",
  operation: "关联保存操作",
  summary: "月度摘要",
  checkpoint: "历史进度",
  receipts: "结果回执",
  resume_attempts: "补充回答副本",
};
function view() {
  const current = deletionState();
  return {
    state: current,
    counts: Object.entries(current?.preview.counts || {}).map(
      ([key, count]) => {
        const [k, a] = key.split(":");
        return {
          key,
          count,
          label: `${names[k] || "其他关联数据"}${a === "minimize" ? "（清除共享原文）" : a === "keep_targets" ? "（清除安排，保留独立目标）" : "（删除）"}`,
        };
      },
    ),
    wrongBase: !!current && current.base !== baseUrl(),
  };
}
registerPage({
  data: { state: null, counts: [] },
  allowPrivacy: true,
  async load() {
    return view();
  },
  onReset() {
    this.setData(view());
  },
  onPreview() {
    return this.run(async () => {
      if (state.pendingOperation())
        throw new Error("请先核对或重试上次请求，再生成删除预览。");
      if (deletionState()?.result?.state === "pending")
        throw new Error("请先完成上一份清理作业。");
      if (!this.params.id || !["event", "fact"].includes(this.params.type))
        throw new Error("请从足迹记录或长期信息选择要删除的对象。");
      const preview = await api("/deletions/preview", "POST", {
        target_type: this.params.type,
        target_id: this.params.id,
      });
      saveDeletionState({ base: baseUrl(), preview, requestId: requestId() });
      this.setData(view());
    });
  },
  onExecute() {
    return this.run(async () => {
      const current = deletionState();
      if (!current || current.base !== baseUrl())
        throw new Error("请切回生成预览的服务。");
      if (
        !(await confirmAction(
          "确认隐私删除",
          "将按页面列出的实际影响范围删除记录及相关副本；操作无法撤销。范围变化会要求重新确认。",
        ))
      )
        return;
      const next = { ...current, requestId: current.requestId || requestId() };
      saveDeletionState(next);
      try {
        await state.executeOperation(
          state.operation(
            "/deletions",
            { preview_id: next.preview.preview_id, request_id: next.requestId },
            "隐私删除",
          ),
        );
      } finally {
        this.setData(view());
      }
    });
  },
  onStatus(e) {
    return this.run(async () => {
      const current = deletionState();
      if (!current?.result || current.base !== baseUrl())
        throw new Error("请切回原服务再继续清理。");
      const retry = !!e.currentTarget.dataset.retry;
      state.clearPrivateState();
      try {
        const path = `/deletions/${current.result.deletion_id}${retry ? "/retry" : ""}`;
        const result = retry
          ? await state.mutate(path, "POST")
          : await api(path);
        saveDeletionState({ ...current, result });
      } finally {
        state.clearPrivateState();
        state.changed();
        this.setData(view());
      }
    });
  },
});
