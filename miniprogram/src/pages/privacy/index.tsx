import { useState } from "react";
import Taro, { useDidShow } from "@tarojs/taro";
import { Button, View } from "@tarojs/components";
import type { DeletionPreview, DeletionResult } from "../../types";
import { api, baseUrl, requestId } from "../../api";
import {
  changed,
  clearPrivateState,
  executeOperation,
  mutate,
  operation,
  pendingOperation,
  useAction,
  useChange,
} from "../../state";
import { deletionState, saveDeletionState } from "../../privacy";
import { confirm, Page } from "../../components/common";

const countNames: Record<string, string> = {
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
function countLabel(key: string) {
  const [kind, action] = key.split(":");
  return `${countNames[kind] || "其他关联数据"}${action === "minimize" ? "（清除共享原文）" : action === "keep_targets" ? "（清除安排，保留独立目标）" : "（删除）"}`;
}
export default function Privacy() {
  const [state, setState] = useState(deletionState);
  const action = useAction();
  const params = Taro.getCurrentInstance().router?.params || {};
  useDidShow(() => setState(deletionState()));
  useChange(() => setState(deletionState()));
  async function preview() {
    await action.run(async () => {
      if (pendingOperation())
        throw new Error("请先核对或重试上次请求，再生成删除预览。");
      const current = deletionState();
      if (current?.result?.state === "pending")
        throw new Error("请先完成上一份清理作业。");
      if (!params.id || !["event", "fact"].includes(params.type || ""))
        throw new Error("请从历史记录或长期信息选择要删除的对象。");
      const result = await api<DeletionPreview>("/deletions/preview", "POST", {
        target_type: params.type,
        target_id: params.id,
      });
      const next = { base: baseUrl(), preview: result, requestId: requestId() };
      saveDeletionState(next);
      setState(next);
    });
  }
  async function execute() {
    await action.run(async () => {
      if (!state || state.base !== baseUrl())
        throw new Error("请切回生成预览的服务。");
      if (
        !(await confirm(
          "确认隐私删除",
          "将按页面列出的实际影响范围删除记录及相关副本；操作无法撤销。范围变化会要求重新确认。",
        ))
      )
        return;
      const next = { ...state, requestId: state.requestId || requestId() };
      saveDeletionState(next);
      try {
        const result = await executeOperation<DeletionResult>(
          operation(
            "/deletions",
            { preview_id: next.preview.preview_id, request_id: next.requestId },
            "隐私删除",
          ),
        );
        setState({ ...next, result });
      } finally {
        setState(deletionState());
      }
    });
  }
  async function status(retry: boolean) {
    await action.run(async () => {
      if (!state?.result || state.base !== baseUrl())
        throw new Error("请切回原服务再继续清理。");
      clearPrivateState();
      try {
        const path = `/deletions/${state.result.deletion_id}${retry ? "/retry" : ""}`;
        const result = retry
          ? await mutate<DeletionResult>(path, "POST")
          : await api<DeletionResult>(path);
        const next = { ...state, result };
        saveDeletionState(next);
        setState(next);
      } finally {
        clearPrivateState();
        changed();
      }
    });
  }
  const previewState = state?.preview;
  return (
    <Page title="隐私删除" error={action.error}>
      <View className="card">
        <View>
          删除前请核对影响范围。相关消息和分析结果可能一起清理，其他独立记录会保留。
        </View>
        <View className="muted">
          应用内导出副本会同步清理；已经分享或保存到其他位置的文件需要自行删除。
        </View>
        <Button disabled={action.busy} onClick={preview}>
          查看删除范围
        </Button>
      </View>
      {previewState && (
        <View className="card">
          <View className="subtitle">影响范围</View>
          {state?.base !== baseUrl() && (
            <View className="warning">此预览属于另一服务，请切回原地址。</View>
          )}
          <View className="muted">
            对象：
            {previewState.target_type === "event"
              ? "健康记录"
              : "长期信息"}
          </View>
          {Object.entries(previewState.counts).map(([key, count]) => (
            <View className="field" key={key}>
              {countLabel(key)}：{count}
            </View>
          ))}
          {previewState.reasons.map((reason, index) => (
            <View className="warning" key={index}>
              {reason}
            </View>
          ))}
          {previewState.scope === "legacy_context" && (
            <View className="warning">部分旧记录的关联信息不完整，可能需要清理更多相关内容。</View>
          )}
          {state?.result ? (
            <>
              <View className="subtitle">
                {state.result.state === "completed"
                  ? "清理已完成"
                  : "清理尚未完成"}
              </View>
              {state.result.state === "pending" && (
                <Button disabled={action.busy} onClick={() => status(true)}>
                  继续清理
                </Button>
              )}
              <Button disabled={action.busy} onClick={() => status(false)}>
                查看清理进度
              </Button>
            </>
          ) : (
            <Button
              className="danger"
              disabled={action.busy || !!pendingOperation()}
              loading={action.busy}
              onClick={execute}
            >
              确认以上范围并删除
            </Button>
          )}
        </View>
      )}
    </Page>
  );
}
