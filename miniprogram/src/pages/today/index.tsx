import { Button, View } from "@tarojs/components";
import type { Daily } from "../../types";
import { api } from "../../api";
import { mutate, useAction, useResource } from "../../state";
import {
  labels,
  numberText,
  Page,
  TargetValues,
} from "../../components/common";

export default function Today() {
  const resource = useResource(() => api<Daily>("/dashboard"));
  const action = useAction();
  const daily = resource.data;
  return (
    <Page
      title="今日概览"
      error={action.error || resource.error}
      loading={resource.loading}
      refresh={resource.refresh}
    >
      {daily && (
        <>
          <View className="card">
            <View>
              {daily.date} · {daily.record_count} 条记录
            </View>
            <TargetValues targets={daily.totals} />
            <View className="muted">
              {daily.totals.unknown_items
                ? `${daily.totals.unknown_items} 项营养未知，汇总不完整`
                : "汇总依据已记录内容，不代表全天实际摄入"}
            </View>
            <View className="field">
              体重：{numberText(daily.weight_kg)} kg
            </View>
            <View className="field">
              睡眠：{numberText(daily.sleep_minutes)} 分钟
            </View>
            <View className="field">运动：{daily.exercise_minutes} 分钟</View>
          </View>
          {daily.targets && (
            <View className="card">
              <View className="subtitle">当前目标</View>
              <TargetValues targets={daily.targets} />
              <View className="subtitle">距离目标（仅依据已知摄入）</View>
              <TargetValues targets={daily.remaining} />
            </View>
          )}
          <View className="card">
            <View>
              当天饮食记录：{daily.diet_complete ? "已标记完整" : "未标记完整"}
            </View>
            <View className="muted">
              只有确实记录全天饮食后才标记完整；营养未知与记录完整是两回事。
            </View>
            <Button
              disabled={action.busy}
              onClick={() =>
                action.run(() =>
                  mutate(`/days/${daily.date}/complete`, "PUT", {
                    complete: !daily.diet_complete,
                  }),
                )
              }
            >
              {daily.diet_complete ? "撤销完整标记" : "标记当天饮食完整"}
            </Button>
          </View>
          {daily.events.map((event) => (
            <View className="card" key={event.id}>
              <View className="tag">
                {labels[event.kind]} · {event.occurred_at}
              </View>
              <View>{event.raw_text}</View>
            </View>
          ))}
          {!daily.events.length && (
            <View className="card">今天还没有记录，去记录页写下第一条吧。</View>
          )}
        </>
      )}
    </Page>
  );
}
