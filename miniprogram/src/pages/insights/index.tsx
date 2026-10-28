import { useState } from "react";
import { Button, View } from "@tarojs/components";
import type { Insight, Review } from "../../types";
import { api } from "../../api";
import {
  mutate,
  useAction,
  useOperation,
  useResource,
} from "../../state";
import {
  DerivedNote,
  DateField,
  Select,
  Fold,
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
  const [section, setSection] = useState<"insights" | "reviews">("insights");
  const [filter, setFilter] = useState({ start: "", end: "", period: "" });
  const [appliedFilter, setAppliedFilter] = useState<typeof filter | null>(
    null,
  );
  const [filterError, setFilterError] = useState("");
  const today = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
  const weekStartDate = new Date(`${today}T00:00:00Z`);
  weekStartDate.setUTCDate(
    weekStartDate.getUTCDate() - ((weekStartDate.getUTCDay() + 6) % 7),
  );
  const weekStart = weekStartDate.toISOString().slice(0, 10);
  const reviews = resource.data?.reviews || [];
  const visibleReviews = appliedFilter
    ? reviews.filter(
        ({ data }) =>
          (!appliedFilter.period || data.period === appliedFilter.period) &&
          (!appliedFilter.start || data.end >= appliedFilter.start) &&
          (!appliedFilter.end || data.end <= appliedFilter.end),
      )
    : ["day", "week"].flatMap((period) => {
        const matches = reviews.filter(
          ({ data }) =>
            data.period === period &&
            (period === "day"
              ? data.end === today
              : data.end >= weekStart && data.end <= today),
        );
        matches.sort((a, b) => b.data.end.localeCompare(a.data.end));
        return matches.slice(0, 1);
      });
  const action = useAction();
  const pending = useOperation();
  return (
    <Page
      title="让过去，帮助下一步"
      loading={resource.loading}
      error={filterError || action.error || resource.error}
      refresh={resource.refresh}
    >
      <View className="discover-page">
        <View className="trend-tabs">
          <Button
            className={section === "insights" ? "is-active" : ""}
            onClick={() => setSection("insights")}
          >
            健康观察
          </Button>
          <Button
            className={section === "reviews" ? "is-active" : ""}
            onClick={() => setSection("reviews")}
          >
            日常复盘
          </Button>
        </View>
        {section === "insights" && (
          <>
            {resource.data?.insights.map((insight) => (
              <View className="card discovery-card" key={insight.id}>
                <View className="subtitle">{insight.data.title}</View>
                <View className="discovery-copy">
                  {insight.data.description}
                </View>
                <View className="discovery-note">
                  {insight.data.limitations}
                </View>
                <DerivedNote memory={insight.data.memory} />
                <Fold title="参考记录与反馈">
                  <View className="tag">
                    {insight.data.confidence} · 样本 {insight.data.sample_size}{" "}
                    · 覆盖 {insight.data.coverage}%
                  </View>

                  <View className="muted">
                    参考日期：{insight.data.window.join(" — ")} · 对比记录数：
                    {insight.data.groups.join(" / ")}
                  </View>

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
                </Fold>
                <EvidenceList rows={insight.data.evidence_refs} />
              </View>
            ))}
            {resource.data && !resource.data.insights.length && (
              <View className="card">
                还没有新的观察。继续记录，慢慢发现身体的变化。
              </View>
            )}
          </>
        )}
        {section === "reviews" && (
          <>
            <View className="card discovery-card review-filter">
              <Fold
                title={appliedFilter ? "筛选历史复盘 · 已筛选" : "筛选历史复盘"}
              >
                <DateField
                  label="开始日期"
                  value={filter.start}
                  onChange={(start) => setFilter({ ...filter, start })}
                />
                <DateField
                  label="结束日期"
                  value={filter.end}
                  onChange={(end) => setFilter({ ...filter, end })}
                />
                <Select
                  label="复盘类型"
                  value={filter.period}
                  options={{ "": "全部", day: "日复盘", week: "周复盘" }}
                  onChange={(period) => setFilter({ ...filter, period })}
                />
                <View className="row">
                  <Button
                    onClick={() => {
                      if (!filter.start && !filter.end) {
                        setFilterError("请选择查询日期。");
                        return;
                      }
                      if (
                        filter.start &&
                        filter.end &&
                        filter.start > filter.end
                      ) {
                        setFilterError("开始日期不能晚于结束日期。");
                        return;
                      }
                      setFilterError("");
                      setAppliedFilter({ ...filter });
                    }}
                  >
                    查询
                  </Button>
                  <Button
                    onClick={() => {
                      setAppliedFilter(null);
                      setFilter({ start: "", end: "", period: "" });
                      setFilterError("");
                    }}
                  >
                    返回今日与本周
                  </Button>
                </View>
              </Fold>
            </View>
            <View className="muted">
              {appliedFilter
                ? "历史复盘 · 按结束日期筛选"
                : "今日与本周"}
            </View>
            {visibleReviews.map((review) => (
              <View className="card discovery-card" key={review.id}>
                <View className="discovery-date">
                  {review.data.start}
                  {review.data.end !== review.data.start
                    ? ` — ${review.data.end}`
                    : ""}
                </View>
                <View className="subtitle">
                  {review.data.period === "week" ? "周复盘" : "日复盘"}
                </View>
                <View className="discovery-copy">{review.data.summary}</View>
                <DerivedNote memory={review.data.memory} />
                <Fold title="查看详细数据">
                  <View className="muted">
                    记录 {review.data.recorded_days} 天 / 完整{" "}
                    {review.data.complete_days} 天 · 共{" "}
                    {review.data.record_count} 条
                  </View>
                  <View className="field">
                    平均已知摄入：{numberText(review.data.avg_kcal)} kcal
                  </View>
                  <View className="field">
                    体重变化：{numberText(review.data.weight_change)} kg
                  </View>
                  <View className="field">
                    平均睡眠：{numberText(review.data.avg_sleep_minutes)} 分钟 ·
                    运动 {review.data.exercise_minutes} 分钟
                  </View>
                </Fold>
                <EvidenceList rows={review.data.evidence_refs} />
              </View>
            ))}
            {resource.data && !visibleReviews.length && (
              <View className="card discovery-empty">
                {appliedFilter
                  ? "所选日期没有复盘。"
                  : "今日和本周暂无复盘。"}
              </View>
            )}
          </>
        )}
      </View>
    </Page>
  );
}
