import { Button, View } from "@tarojs/components";
import type { Insight, Review, Workflow } from "../../types";
import { api, requestId } from "../../api";
import {
  executeOperation,
  mutate,
  operation,
  useAction,
  useOperation,
  useResource,
} from "../../state";
import {
  DerivedNote,
  EvidenceList,
  numberText,
  Page,
} from "../../components/common";

export default function Insights() {
  const resource = useResource(async () => {
    const [insights, reviews] = await Promise.all([
      api<Insight[]>("/insights?include_stale=true"),
      api<Review[]>("/reviews"),
    ]);
    return { insights, reviews };
  });
  const action = useAction();
  const pending = useOperation();
  return (
    <Page
      title="让过去，帮助下一步"
      loading={resource.loading}
      error={action.error || resource.error}
      refresh={resource.refresh}
    >
      <View className="card">
        <View>从完整记录中寻找规律，相关性不代表因果。</View>
        <View className="row">
          {(["day", "week"] as const).map((period) => (
            <Button
              key={period}
              disabled={action.busy || !!pending}
              loading={action.busy}
              onClick={() =>
                action.run(() =>
                  executeOperation<Workflow>(
                    operation(
                      "/reviews",
                      { request_id: requestId(), period },
                      `生成${period === "day" ? "日" : "周"}复盘`,
                    ),
                  ),
                )
              }
            >
              生成{period === "day" ? "日" : "周"}复盘
            </Button>
          ))}
        </View>
      </View>
      {resource.data?.insights.map((insight) => (
        <View className="card" key={insight.id}>
          <View className="subtitle">{insight.data.title}</View>
          <View className="tag">
            {insight.data.confidence} · 样本 {insight.data.sample_size} · 覆盖{" "}
            {insight.data.coverage}%
          </View>
          <View>{insight.data.description}</View>
          <View className="muted">
            窗口：{insight.data.window.join(" — ")} · 分组样本：
            {insight.data.groups.join(" / ")}
          </View>
          <View className="warning">{insight.data.limitations}</View>
          <DerivedNote memory={insight.data.memory} />
          <EvidenceList rows={insight.data.evidence_refs} />
          <View className="muted">
            我的反馈：
            {insight.feedback === "accepted"
              ? "认可"
              : insight.feedback === "rejected"
                ? "不认可"
                : "未反馈"}
          </View>
          <View className="row">
            <Button
              disabled={action.busy || !!pending}
              onClick={() =>
                action.run(() =>
                  mutate(`/insights/${insight.id}`, "PATCH", {
                    feedback: "accepted",
                  }),
                )
              }
            >
              认可观察
            </Button>
            <Button
              disabled={action.busy || !!pending}
              onClick={() =>
                action.run(() =>
                  mutate(`/insights/${insight.id}`, "PATCH", {
                    feedback: "rejected",
                  }),
                )
              }
            >
              不认可
            </Button>
          </View>
        </View>
      ))}
      {resource.data && !resource.data.insights.length && (
        <View className="card">
          目前没有足够数据支持的规律。继续记录睡眠与饮食，并在确实完整时标记当天完整；观察通常需要两组各至少3个完整样本。
        </View>
      )}
      {resource.data?.reviews.map((review) => (
        <View className="card" key={review.id}>
          <View className="subtitle">
            {review.data.period === "week" ? "周复盘" : "日复盘"} ·{" "}
            {review.data.start} — {review.data.end}
          </View>
          <View className="message">{review.data.summary}</View>
          <View className="muted">
            记录 {review.data.recorded_days} 天 / 完整{" "}
            {review.data.complete_days} 天 · 共 {review.data.record_count} 条
          </View>
          <View className="field">
            平均已知摄入：{numberText(review.data.avg_kcal)} kcal
          </View>
          <View className="field">
            体重变化：{numberText(review.data.weight_change)} kg
          </View>
          <View className="field">
            平均睡眠：{numberText(review.data.avg_sleep_minutes)} 分钟 · 运动{" "}
            {review.data.exercise_minutes} 分钟
          </View>
          <DerivedNote memory={review.data.memory} />
          <EvidenceList rows={review.data.evidence_refs} />
        </View>
      ))}
    </Page>
  );
}
