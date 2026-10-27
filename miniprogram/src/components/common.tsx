import { useState } from "react";
import Taro from "@tarojs/taro";
import { Button, Input, Picker, Text, View } from "@tarojs/components";
import type { PropsWithChildren } from "react";
import type {
  Card,
  DerivedMemory,
  Evidence,
  ProcessStep,
  Targets,
} from "../types";
import {
  acknowledgeOperation,
  executeOperation,
  useAction,
  useOperation,
} from "../state";

export const labels: Record<string, string> = {
  meal: "饮食",
  weight: "体重",
  sleep: "睡眠",
  exercise: "运动",
  cycle: "周期",
  kcal: "热量",
  protein_g: "蛋白质",
  carbs_g: "碳水",
  fat_g: "脂肪",
  exercise_minutes: "每周运动",
  energy: "热量适配",
  macros: "营养适配",
  satiety: "饱腹感",
  adherence: "可坚持性",
  snacking: "加餐风险",
  breakfast: "早餐",
  lunch: "午餐",
  dinner: "晚餐",
};
export const numberText = (value: number | null | undefined, digits = 1) =>
  value == null ? "未知" : String(Number(value.toFixed(digits)));
export const go = (page: string) => {
  const [name, query] = page.split("?");
  return Taro.navigateTo({
    url: `/pages/${name}/index${query ? `?${query}` : ""}`,
  });
};
export const tab = (page: string) =>
  Taro.switchTab({ url: `/pages/${page}/index` });
export const confirm = async (title: string, content: string) =>
  (
    await Taro.showModal({
      title,
      content,
      confirmText: "确认",
      cancelText: "取消",
    })
  ).confirm;

export function Fold({
  title,
  children,
}: PropsWithChildren<{ title: string }>) {
  const [open, setOpen] = useState(false);
  return (
    <View className="fold">
      <View className="fold-title" onClick={() => setOpen(!open)}>
        {title} {open ? "⌃" : "⌄"}
      </View>
      {open && <View className="fold-body">{children}</View>}
    </View>
  );
}
export function Field({
  label,
  value,
  onChange,
  numeric = false,
  max = 500,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  numeric?: boolean;
  max?: number;
}) {
  return (
    <View className="field">
      <View className="muted">{label}</View>
      <Input
        type={numeric ? "digit" : "text"}
        value={value}
        maxlength={max}
        onInput={(event) => onChange(event.detail.value)}
      />
    </View>
  );
}
export function Select({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: Record<string, string>;
  onChange: (value: string) => void;
}) {
  const keys = Object.keys(options);
  return (
    <Picker
      mode="selector"
      range={Object.values(options)}
      value={Math.max(0, keys.indexOf(value))}
      onChange={(event) => onChange(keys[Number(event.detail.value)])}
    >
      <View className="field">
        {label}：{options[value] || "请选择"}　⌄
      </View>
    </Picker>
  );
}
export function DateField({
  label,
  value,
  onChange,
  mode = "date",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  mode?: "date" | "time";
}) {
  return (
    <View className="field">
      <Picker
        mode={mode}
        value={value}
        onChange={(event) => onChange(event.detail.value)}
      >
        <View>
          {label}：{value || "请选择"}　⌄
        </View>
      </Picker>
      {value && (
        <Text className="link" onClick={() => onChange("")}>
          清空
        </Text>
      )}
    </View>
  );
}
export function Page({
  title,
  children,
  error,
  loading,
  refresh,
}: PropsWithChildren<{
  title: string;
  error?: string;
  loading?: boolean;
  refresh?: () => void;
}>) {
  return (
    <View className="page">
      <View className="page-heading">
        <View className="eyebrow">轻记 · 个人健康空间</View>
        <View className="title">{title}</View>
      </View>
      <OperationBanner />
      {error && <View className="error">{error}</View>}
      {loading && <View className="muted">正在读取…</View>}
      {children}
      {refresh && <Button onClick={refresh}>刷新状态</Button>}
    </View>
  );
}
function OperationBanner() {
  const pending = useOperation();
  const action = useAction();
  if (!pending) return null;
  return (
    <View className="card warning">
      <View>待核对：{pending.label}</View>
      <View className="muted">
        上次请求可能已经保存。重试沿用原参数和请求编号；先刷新核对结果。
      </View>
      {action.error && <View className="error">{action.error}</View>}
      <Button
        disabled={action.busy}
        loading={action.busy}
        onClick={() => action.run(() => executeOperation(pending))}
      >
        重试原请求
      </Button>
      <Button
        disabled={action.busy}
        onClick={() =>
          action.run(async () => {
            if (pending.path === "/deletions") {
              await go("privacy");
              return;
            }
            if (
              await confirm(
                "结束本地重试",
                "这不会取消服务器操作，也不会撤销已保存数据。请先核对历史或流程结果；继续后再次提交可能创建新记录。",
              )
            )
              acknowledgeOperation();
          })
        }
      >
        {pending.path === "/deletions"
          ? "查看隐私清理"
          : "已核对结果，结束本地重试"}
      </Button>
    </View>
  );
}
const reasons: Record<string, string> = {
  source_changed: "依据的数据已变化，请重新生成。",
  algorithm_changed: "计算方法已更新，需要重新核对。",
  window_changed: "观察窗口已变化。",
  legacy_version_unknown: "历史结果缺少方法版本。",
  analysis_refreshed: "本轮未形成可用新结论。",
  plan_window_ended: "安排日期已结束，已确认目标仍保留。",
  fact_date_changed: "适用日期已变化，请重新生成安排。",
};
export function DerivedNote({ memory }: { memory?: DerivedMemory }) {
  if (!memory) return null;
  return (
    <View className="muted">
      {memory.status !== "valid" && (
        <View className="warning">
          需要更新或复核：
          {reasons[memory.invalid_reason || ""] || "请重新核对依据。"}
        </View>
      )}
      <Fold title="生成依据与版本">
        <View>窗口：{memory.window?.join(" — ") || "未记录"}</View>
        <View>方法：{memory.algorithm_version || "历史版本未知"}</View>
        <View>生成时间：{memory.generated_at || "未记录"}</View>
        {memory.recomputable === false && (
          <View>原始明细已归档，无法重新计算。</View>
        )}
      </Fold>
    </View>
  );
}
export function EvidenceList({ rows = [] }: { rows?: Evidence[] }) {
  if (!rows.length) return null;
  return (
    <Fold title={`依据 · ${rows.length} 条记录`}>
      {rows.map((row) => (
        <View
          className="field"
          key={row.id}
          onClick={() => {
            Taro.setStorageSync("evidence-event", row.id);
            void tab("history");
          }}
        >
          <View>
            {row.date} · {labels[row.kind] || row.kind}
          </View>
          <View>{row.text}</View>
          <Text className="link">查看记录</Text>
        </View>
      ))}
    </Fold>
  );
}
export function Trace({
  rows = [],
  duration = 0,
}: {
  rows?: ProcessStep[];
  duration?: number;
}) {
  if (!rows.length) return null;
  return (
    <Fold title={`处理摘要 · ${Math.round(duration / 1000)} 秒`}>
      <View className="muted">可验证的执行摘要，不包含模型私有思维链。</View>
      {rows.map((row, index) => (
        <View className="field" key={`${row.title}-${index}`}>
          <View>
            {row.title} · {row.source === "model" ? "模型" : "本地"}
          </View>
          <View className="muted">{row.detail}</View>
        </View>
      ))}
    </Fold>
  );
}
export function MealCard({ card }: { card: Card }) {
  if (card.type !== "meal") return null;
  return (
    <View className="inset">
      <View>
        {card.title || "餐食记录"} · {card.date}
      </View>
      <TargetValues targets={card.totals} />
      {!!card.totals.unknown_items && (
        <View className="warning">
          {card.totals.unknown_items} 项营养未知，汇总仅含已知部分。
        </View>
      )}
      <Fold title="估算来源与判断细节">
        {card.items.map((item, index) => (
          <View key={index} className="field">
            {item.name} ·{" "}
            {item.quantity == null
              ? "分量未知"
              : `${item.quantity}${item.unit}`}
            <View className="muted">
              {item.nutrition_source} · {item.uncertainty}{" "}
              {item.nutrition_status === "estimated" ? "粗估值" : ""}
            </View>
          </View>
        ))}
        <View>
          评分覆盖 {card.coverage}% · 综合分 {numberText(card.score)}
        </View>
        {card.notes.map((note, index) => (
          <View className="muted" key={index}>
            {note}
          </View>
        ))}
      </Fold>
    </View>
  );
}
export function TargetValues({
  targets,
}: {
  targets?: Partial<Record<keyof Targets, number | null>> | null;
}) {
  if (!targets) return null;
  return (
    <View className="metrics">
      {Object.entries(targets)
        .filter(([key]) =>
          [
            "kcal",
            "protein_g",
            "carbs_g",
            "fat_g",
            "exercise_minutes",
          ].includes(key),
        )
        .map(([key, value]) => (
          <View className="metric" key={key}>
            <View className="metric-value">{numberText(value)}</View>
            <View className="muted">
              {labels[key]} ·{" "}
              {key === "kcal"
                ? "kcal"
                : key === "exercise_minutes"
                  ? "分钟/周"
                  : "g"}
            </View>
          </View>
        ))}
    </View>
  );
}
