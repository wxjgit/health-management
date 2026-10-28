import { useEffect, useRef, useState } from "react";
import Taro from "@tarojs/taro";
import { Button, View } from "@tarojs/components";
import type { Daily, Event } from "../types";
import { api, errorText } from "../api";
import { privacyGeneration, usePrivateReset } from "../state";
import { Fold, go, labels, numberText, tab } from "./common";

type Revision = {
  version: number;
  raw_text: string;
  reason: string;
  created_at: string;
  changes?: { field: string; before: unknown; after: unknown }[];
};
export function RevisionHistory({ event }: { event: Event }) {
  const [rows, setRows] = useState<Revision[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);
  useEffect(
    () => () => {
      mounted.current = false;
    },
    [],
  );
  usePrivateReset(() => {
    setRows(null);
    setError("");
  });
  async function load() {
    const epoch = privacyGeneration();
    setBusy(true);
    try {
      const result = await api<Revision[]>(`/events/${event.id}/revisions`);
      if (mounted.current && epoch === privacyGeneration()) {
        setRows(result);
        setError("");
      }
    } catch (reason) {
      if (mounted.current && epoch === privacyGeneration())
        setError(errorText(reason));
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  return (
    <Fold title="修改记录">
      <Button loading={busy} disabled={busy} onClick={load}>
        读取修订历史
      </Button>
      {error && <View className="error">{error}</View>}
      {rows?.map((row) => (
        <View className="field" key={row.version}>
          <View>{row.created_at}</View>
          <View>{row.raw_text}</View>
          <View className="muted">{row.reason}</View>
          {row.changes?.map((change, index) => (
            <View key={index} className="muted">
              {labels[change.field] || "记录内容"}：
              {JSON.stringify(change.before)} → {JSON.stringify(change.after)}
            </View>
          ))}
        </View>
      ))}
    </Fold>
  );
}
export function EventCard({
  event,
  highlight = false,
}: {
  event: Event;
  highlight?: boolean;
}) {
  return (
    <View
      id={`event-${event.id}`}
      className={`card record-item ${highlight ? "highlight" : ""}`}
    >
      <View className="tag">
        {labels[event.kind]} · {event.day}
      </View>
      <View className="record-text">{event.raw_text}</View>
      <Fold title="查看详情与操作">
        {event.kind === "meal" &&
          event.data.items.map((item, index) => (
            <View className="muted" key={index}>
              {item.name} ·{" "}
              {item.quantity == null
                ? "分量未知"
                : `${item.quantity}${item.unit}`}{" "}
              · {numberText(item.kcal)} kcal
            </View>
          ))}
        <RevisionHistory event={event} />
        <View className="row">
          <Button
            size="mini"
            onClick={() => {
              Taro.setStorageSync("correction-event", event);
              void tab("chat");
            }}
          >
            纠正
          </Button>
          <Button
            size="mini"
            className="danger"
            onClick={() =>
              go(`privacy?type=event&id=${encodeURIComponent(event.id)}`)
            }
          >
            删除记录
          </Button>
        </View>
      </Fold>
    </View>
  );
}
export function Trend({
  days,
  metric,
}: {
  days: Daily[];
  metric: "weight_kg" | "sleep_minutes" | "exercise_minutes";
}) {
  const rows = days.filter((day) => day[metric] != null);
  const maximum = Math.max(1, ...rows.map((day) => day[metric]!));
  return (
    <View className="card">
      <View className="subtitle">
        {metric === "weight_kg"
          ? "体重趋势 · kg"
          : metric === "sleep_minutes"
            ? "睡眠趋势 · 小时"
            : "运动趋势 · 分钟"}
      </View>
      <View className="muted">
        最近28天 · 已记录 {rows.length} 天，缺失日期不算零。
      </View>
      {rows.length < 2 ? (
        <View>记录至少两个日期后显示趋势。</View>
      ) : (
        <View className="trend">
          {days.map((day, index) => (
            <View key={day.date} className="trend-column">
              <View className="trend-track">
                {day[metric] != null && (
                  <View
                    className="trend-bar"
                    style={{
                      height: `${Math.max(2, (day[metric]! / maximum) * 100)}%`,
                    }}
                  />
                )}
              </View>
              <View className="trend-date">
                {index % 7 === 0 || index === days.length - 1
                  ? day.date.slice(5)
                  : ""}
              </View>
            </View>
          ))}
        </View>
      )}
      <Fold title="逐日数值">
        {rows.map((day) => (
          <View className="field" key={day.date}>
            {day.date}：
            {numberText(
              metric === "sleep_minutes" ? day[metric]! / 60 : day[metric],
            )}{" "}
            {metric === "weight_kg"
              ? "kg"
              : metric === "sleep_minutes"
                ? "小时"
                : "分钟"}
          </View>
        ))}
      </Fold>
    </View>
  );
}
