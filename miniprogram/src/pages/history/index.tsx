import { useRef, useState } from "react";
import Taro, { useDidShow } from "@tarojs/taro";
import { Button, View } from "@tarojs/components";
import type { Daily, Event, Month } from "../../types";
import { api, queryString } from "../../api";
import { usePrivateReset, useResource } from "../../state";
import {
  DateField,
  DerivedNote,
  Field,
  numberText,
  Page,
  Select,
} from "../../components/common";
import { EventCard, Trend } from "../../components/records";

const emptyFilter = { kind: "", start: "", end: "", search: "" };
export default function History() {
  const [draft, setDraft] = useState(emptyFilter);
  const applied = useRef(emptyFilter);
  const [selected, setSelected] = useState("");
  const [filterError, setFilterError] = useState("");
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
      setSelected(id);
      applied.current = emptyFilter;
      setDraft(emptyFilter);
      Taro.removeStorageSync("evidence-event");
      void resource.refresh();
    }
  });
  usePrivateReset(() => {
    setSelected("");
    setDraft(emptyFilter);
    applied.current = emptyFilter;
  });
  return (
    <Page
      title="回看记录，读懂变化"
      error={filterError || resource.error}
      loading={resource.loading}
      refresh={resource.refresh}
    >
      <View className="card">
        <Select
          label="类型"
          value={draft.kind}
          options={{
            "": "全部类型",
            meal: "饮食",
            weight: "体重",
            sleep: "睡眠",
            exercise: "运动",
            cycle: "周期",
          }}
          onChange={(kind) => setDraft({ ...draft, kind })}
        />
        <DateField
          label="开始日期"
          value={draft.start}
          onChange={(start) => setDraft({ ...draft, start })}
        />
        <DateField
          label="结束日期"
          value={draft.end}
          onChange={(end) => setDraft({ ...draft, end })}
        />
        <Field
          label="搜索食物或描述"
          max={200}
          value={draft.search}
          onChange={(search) => setDraft({ ...draft, search })}
        />
        <Button
          disabled={resource.loading}
          onClick={() => {
            if (draft.start && draft.end && draft.start > draft.end) {
              setFilterError("开始日期不能晚于结束日期");
              return;
            }
            setFilterError("");
            applied.current = draft;
            void resource.refresh();
          }}
        >
          查询
        </Button>
        <Button
          onClick={() => {
            setDraft(emptyFilter);
            applied.current = emptyFilter;
            setSelected("");
            void resource.refresh();
          }}
        >
          重置筛选
        </Button>
      </View>
      {resource.data && (
        <>
          <Trend days={resource.data.days} metric="weight_kg" />
          <Trend days={resource.data.days} metric="sleep_minutes" />
          {selected &&
            !resource.data.events.some((event) => event.id === selected) && (
              <View className="warning">
                关联记录可能不在当前范围内或已归档、删除；请调整日期搜索。
              </View>
            )}
          <View className="muted">
            当前返回 {resource.data.events.length}{" "}
            条（最多1000条，可缩小日期范围）
          </View>
          {[...resource.data.events]
            .sort(
              (left, right) =>
                Number(right.id === selected) - Number(left.id === selected),
            )
            .map((event) => (
              <EventCard
                key={`${event.id}-${event.version}`}
                event={event}
                highlight={event.id === selected}
              />
            ))}
          {!resource.data.events.length && (
            <View className="card">没有符合条件的记录。</View>
          )}
          {resource.data.months.map((month) => (
            <View className="card" key={month.month}>
              <View className="subtitle">{month.month} 月度摘要</View>
              <View>
                {month.data.record_count} 条记录 · {month.data.meal_count} 餐 ·
                平均体重 {numberText(month.data.avg_weight_kg)} kg
              </View>
              <View className="muted">逐日明细已归档，不能还原。</View>
              <DerivedNote memory={month.data.memory} />
            </View>
          ))}
        </>
      )}
    </Page>
  );
}
