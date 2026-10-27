import { Button, Text, View } from "@tarojs/components";
import type { Daily } from "../types";
import { go, Icon, numberText } from "./common";

export function DailyOverview({ daily }: { daily: Daily }) {
  const intake = daily.totals.kcal;
  const target = daily.targets?.kcal;
  const progress =
    intake != null && target
      ? Math.max(0, Math.min(100, (intake / target) * 100))
      : 0;
  return (
    <View className="health-overview">
      <View className="overview-top">
        <View>
          <View className="section-kicker">TODAY · 今日状态</View>
          <View className="overview-date">{daily.date.replace(/-/g, ".")}</View>
        </View>
        <Button className="overview-link" onClick={() => go("today")}>
          今日汇总 <Text>↗</Text>
        </Button>
      </View>
      <View className="overview-scene">
        <View className="scene-orbit orbit-outer" />
        <View className="scene-orbit orbit-inner" />
        <View className="health-pin pin-weight">
          <View className="pin-dot dot-mint" />
          <View>
            <View className="pin-label">体重</View>
            <View className="pin-value">
              {numberText(daily.weight_kg)} <Text>kg</Text>
            </View>
          </View>
        </View>
        <View className="intake-core">
          <View className="core-spark">✦</View>
          <View className="core-number">{numberText(intake, 0)}</View>
          <View className="core-label">已知摄入 · kcal</View>
        </View>
        <View className="health-pin pin-sleep">
          <View className="pin-dot dot-blue" />
          <View>
            <View className="pin-label">睡眠</View>
            <View className="pin-value">
              {numberText(
                daily.sleep_minutes == null ? null : daily.sleep_minutes / 60,
              )}{" "}
              <Text>h</Text>
            </View>
          </View>
        </View>
        <View className="health-pin pin-move">
          <Icon name="insights" active />
          <Text>运动 {daily.exercise_minutes ?? 0} 分钟</Text>
        </View>
      </View>
      <View className="overview-bottom">
        <View className="overview-summary">
          <View>
            <Text className="summary-number">{daily.record_count}</Text>
            <Text className="summary-caption"> 条记录，留住今天</Text>
          </View>
          <View
            className={`status-pill ${daily.diet_complete ? "is-complete" : ""}`}
          >
            {daily.diet_complete ? "饮食已记完整" : "持续记录中"}
          </View>
        </View>
        <View className="intake-track">
          <View className="intake-progress" style={{ width: `${progress}%` }} />
        </View>
        <View className="overview-footnote">
          <Text>
            {daily.totals.unknown_items
              ? `${daily.totals.unknown_items} 项营养未知 · 汇总仅含已知部分`
              : "依据已记录内容，不代表全天实际摄入"}
          </Text>
          <Text>{target ? `目标 ${numberText(target, 0)}` : "目标待设置"}</Text>
        </View>
      </View>
    </View>
  );
}
