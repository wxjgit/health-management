import { useRef, useState } from "react";
import Taro, { useDidShow } from "@tarojs/taro";
import { Button, Picker, View } from "@tarojs/components";
import type { Daily, Event, Month } from "../../types";
import { api, queryString } from "../../api";
import { usePrivateReset, useResource } from "../../state";
import { DerivedNote, Fold, numberText, Page } from "../../components/common";
import { EventCard, Trend } from "../../components/records";

const emptyFilter = { kind: "", start: "", end: "", search: "" };
export default function History() {
  const [section, setSection] = useState<"trends" | "records">("trends");
  const [metric, setMetric] = useState<
    "weight_kg" | "sleep_minutes" | "exercise_minutes"
  >("weight_kg");
  const [recordDate, setRecordDate] = useState("");
  const applied = useRef(emptyFilter);
  const [selected, setSelected] = useState("");
  const resource = useResource(async () => {
    const [events, days, months] = await Promise.all([
      api<Event[]>(
        `/events?${queryString({ ...applied.current, limit: "1000" })}`,
      ),
      api<Daily[]>("/trends?days=28"),
      api<Month[]>("/monthly-summaries"),
    ]);
    return { events, days, months };
  });
  useDidShow(() => {
    const id = Taro.getStorageSync<string>("evidence-event");
    if (id) {
      setSection("records");
      setSelected(id);
      setRecordDate("");
      applied.current = emptyFilter;
      Taro.removeStorageSync("evidence-event");
      void resource.refresh();
    }
  });
  usePrivateReset(() => {
    setSection("trends");
    setSelected("");
    setRecordDate("");
    applied.current = emptyFilter;
  });
  const events = resource.data?.events || [];
  const activeDate =
    recordDate ||
    events.find((event) => event.id === selected)?.day ||
    [...events]
      .map((event) => event.day)
      .sort()
      .reverse()[0] ||
    "";
  const dailyEvents = events.filter((event) => event.day === activeDate);
  return (
    <Page
      title="回看记录，读懂变化"
      error={resource.error}
      loading={resource.loading}
      refresh={resource.refresh}
    >
      <View className="footprint-sections">
        <Button
          className={section === "trends" ? "is-active" : ""}
          onClick={() => setSection("trends")}
        >
          趋势
        </Button>
        <Button
          className={section === "records" ? "is-active" : ""}
          onClick={() => setSection("records")}
        >
          记录
        </Button>
      </View>
      {resource.data && (
        <>
          {section === "trends" && (
            <>
              <View className="trend-tabs">
                {(
                  [
                    ["weight_kg", "体重"],
                    ["sleep_minutes", "睡眠"],
                    ["exercise_minutes", "运动"],
                  ] as const
                ).map(([key, label]) => (
                  <Button
                    key={key}
                    className={metric === key ? "is-active" : ""}
                    onClick={() => setMetric(key)}
                  >
                    {label}
                  </Button>
                ))}
              </View>
              <Trend days={resource.data.days} metric={metric} />
            </>
          )}
          {section === "records" && (
            <>
              {selected &&
                !resource.data.events.some(
                  (event) => event.id === selected,
                ) && (
                  <View className="warning">
                    关联记录可能不在当前范围内或已归档、删除；请调整日期搜索。
                  </View>
                )}
              <View className="footprint-date-row">
                <Picker
                  mode="date"
                  value={activeDate}
                  onChange={(event) => {
                    const date = event.detail.value;
                    setRecordDate(date);
                    setSelected("");
                    applied.current = {
                      ...emptyFilter,
                      start: date,
                      end: date,
                    };
                    void resource.refresh();
                  }}
                >
                  <View className="footprint-date">
                    {activeDate || "选择日期"}{" "}
                    <View className="footprint-date-arrow">⌄</View>
                  </View>
                </Picker>
                <View className="muted">{dailyEvents.length} 条记录</View>
              </View>
              <View className="record-list">
                {[...dailyEvents]
                  .sort(
                    (left, right) =>
                      Number(right.id === selected) -
                      Number(left.id === selected),
                  )

                  .map((event) => (
                    <EventCard
                      key={`${event.id}-${event.version}`}
                      event={event}
                      highlight={event.id === selected}
                    />
                  ))}
                {!dailyEvents.length && (
                  <View className="card">这一天还没有记录，试试其他日期。</View>
                )}
              </View>
              {!!resource.data.months.length && (
                <Fold title="月度摘要">
                  {resource.data.months.map((month) => (
                    <View className="card" key={month.month}>
                      <View className="subtitle">{month.month} 月度摘要</View>
                      <View>
                        {month.data.record_count} 条记录 ·{" "}
                        {month.data.meal_count} 餐 · 平均体重{" "}
                        {numberText(month.data.avg_weight_kg)} kg
                      </View>
                      <View className="muted">逐日明细已归档，不能还原。</View>
                      <DerivedNote memory={month.data.memory} />
                    </View>
                  ))}
                </Fold>
              )}
            </>
          )}
        </>
      )}
    </Page>
  );
}
