import { useEffect, useRef, useState } from "react";
import Taro, { useDidHide } from "@tarojs/taro";
import {
  Button,
  RootPortal,
  ScrollView,
  Textarea,
  View,
} from "@tarojs/components";
import type { DefaultTargets, Plan, Targets, Workflow } from "../types";
import { api, requestId } from "../api";
import {
  executeOperation,
  mutate,
  operation,
  useAction,
  useForegroundPoll,
  useOperation,
  usePrivateReset,
  useResource,
} from "../state";
import {
  confirm,
  Field,
  Fold,
  labels,
  numberText,
  tab,
  TargetValues,
} from "./common";

import FormModal from "./form-modal";

const targetKeys = [
  "kcal",
  "protein_g",
  "carbs_g",
  "fat_g",
  "exercise_minutes",
] as const;
const emptyFields = {
  kcal: "",
  protein_g: "",
  carbs_g: "",
  fat_g: "",
  exercise_minutes: "",
};
const fill = (targets: Targets) =>
  Object.fromEntries(
    targetKeys.map((key) => [key, String(targets[key])]),
  ) as typeof emptyFields;

export default function PlanSection({ today }: { today?: string }) {
  const resource = useResource(async () => {
    const [plans, workflows, defaults] = await Promise.all([
      api<Plan[]>("/plans"),
      api<Workflow[]>("/workflows"),
      api<DefaultTargets>("/targets/default"),
    ]);
    return { plans, workflows, defaults };
  });
  const action = useAction();
  const pending = useOperation();
  const [fields, setFields] = useState(emptyFields);
  const [note, setNote] = useState("");
  const [usingDefaults, setUsingDefaults] = useState(false);
  const [reply, setReply] = useState("");
  const [editorOpen, setEditorOpen] = useState(false);
  useDidHide(() => setEditorOpen(false));
  const dirty = useRef(false);
  const activeId = useRef("");
  const data = resource.data;
  const active = data?.plans.find((plan) => plan.status === "active");
  useEffect(() => {
    if (!data) return;
    if (active && activeId.current !== active.id) {
      activeId.current = active.id;
      dirty.current = false;
      setFields(fill(active.data.targets));
      setUsingDefaults(false);
    } else if ((!active || usingDefaults) && !dirty.current) {
      if (!active) activeId.current = "";
      setFields(
        data.defaults.targets ? fill(data.defaults.targets) : emptyFields,
      );
      setUsingDefaults(!!data.defaults.targets);
    }
  }, [active?.id, data?.defaults.fingerprint, data?.defaults.status]);
  usePrivateReset(() => {
    setFields(emptyFields);
    setNote("");
    setReply("");
    setEditorOpen(false);
    dirty.current = false;
    activeId.current = "";
    setUsingDefaults(false);
  });
  useForegroundPoll(
    !!data?.workflows.some((row) => row.status === "resuming"),
    resource.refresh,
  );
  async function propose() {
    await action.run(async () => {
      if (
        targetKeys.some(
          (key) => !fields[key].trim() || !Number.isFinite(Number(fields[key])),
        )
      )
        throw new Error("请填写完整的数值目标。");
      const targets = Object.fromEntries(
        targetKeys.map((key) => [key, Number(fields[key])]),
      ) as Targets;
      const result = await executeOperation<Workflow>(
        operation(
          "/plans/propose",
          {
            request_id: requestId(),
            targets,
            note,
            target_source: usingDefaults ? "system_default" : "manual",
            ...(usingDefaults
              ? { suggestion_fingerprint: data?.defaults.fingerprint }
              : {}),
          },
          "生成计划",
        ),
      );
      setReply(result.reply);
      setEditorOpen(false);
    });
  }
  async function decide(row: Workflow, approved: boolean) {
    await action.run(async () => {
      if (
        !(await confirm(
          approved ? "采用此计划" : "保留原计划",
          row.pending_input?.question || "请核对当前草案。",
        ))
      )
        return;
      const result = await executeOperation<Workflow>(
        operation(
          `/workflows/${row.thread_id}/resume`,
          {
            request_id: requestId(),
            interrupt_id: row.pending_input!.interrupt_id,
            approved,
          },
          "计划确认",
        ),
      );
      setReply(result.reply);
    });
  }
  return (
    <View className="plan-section">
      {(resource.error || (!editorOpen && action.error)) && (
        <View className="error">{resource.error || action.error}</View>
      )}
      {resource.loading && <View className="muted">正在读取计划…</View>}
      {reply && <View className="card message">{reply}</View>}
      {data?.workflows
        .filter((row) => row.pending_input?.kind === "plan_confirmation")
        .map((row) => {
          const stored = data.plans.find(
            (plan) => plan.id === (row.pending_input?.plan_id || row.thread_id),
          );
          const settled = stored && stored.status !== "pending";
          return (
            <View className="card warning" key={row.thread_id}>
              <View className="subtitle">
                {settled
                  ? stored.status === "active"
                    ? "计划已生效"
                    : "草案已结束"
                  : row.status === "resuming"
                    ? "计划正在确认"
                    : "待你确认"}
              </View>
              <View>{row.pending_input?.question}</View>
              <TargetValues targets={row.pending_input?.targets} />
              {row.pending_input?.target_source === "system_default" && (
                <View className="muted">
                  根据个人资料估算，仅供日常参考。
                  {row.pending_input.target_basis}{" "}
                  {row.pending_input.target_warnings?.join(" ")}
                </View>
              )}
              <View className="muted">
                {Object.entries(row.pending_input?.changes || {})
                  .map(
                    ([key, value]) =>
                      `${labels[key]} ${value > 0 ? "+" : ""}${value}%`,
                  )
                  .join(" · ")}
              </View>
              {row.retry_request ? (
                <Button
                  disabled={
                    action.busy || !!pending || row.status === "resuming"
                  }
                  onClick={() =>
                    action.run(async () => {
                      const result = await executeOperation<Workflow>(
                        operation(
                          `/workflows/${row.thread_id}/resume`,
                          row.retry_request!,
                          "重试计划确认",
                        ),
                      );
                      setReply(result.reply);
                    })
                  }
                >
                  {settled ? "查看确认结果" : "重试上次确认"}
                </Button>
              ) : (
                !settled && (
                  <View className="row">
                    <Button
                      className="primary"
                      disabled={
                        action.busy || !!pending || row.status === "resuming"
                      }
                      onClick={() => decide(row, true)}
                    >
                      采用计划
                    </Button>
                    <Button
                      disabled={
                        action.busy || !!pending || row.status === "resuming"
                      }
                      onClick={() => decide(row, false)}
                    >
                      保留原计划
                    </Button>
                  </View>
                )
              )}
              {!settled && (
                <Button
                  disabled={action.busy || !!pending}
                  onClick={() =>
                    action.run(async () => {
                      if (
                        await confirm(
                          "取消待处理计划",
                          "已生效目标保留，只取消当前待处理草案。",
                        )
                      )
                        await mutate(`/workflows/${row.thread_id}`, "DELETE");
                    })
                  }
                >
                  取消草案
                </Button>
              )}
            </View>
          );
        })}
      {data?.plans
        .filter((plan) => plan.status === "active")
        .map((plan) => (
          <View className="card" key={plan.id}>
            <View className="subtitle">当前生效计划</View>
            <View className="plan-targets">
              {[
                {
                  label: "每日热量",
                  value: plan.data.targets.kcal,
                  unit: "kcal",
                },
                {
                  label: "每周运动",
                  value: plan.data.targets.exercise_minutes,
                  unit: "分钟",
                },
                {
                  label: "蛋白质",
                  value: plan.data.targets.protein_g,
                  unit: "g",
                },
                { label: "碳水", value: plan.data.targets.carbs_g, unit: "g" },
                { label: "脂肪", value: plan.data.targets.fat_g, unit: "g" },
                {
                  label: "膳食纤维",
                  value: plan.data.targets.fiber_g,
                  unit: "g",
                },
              ].map(({ label, value, unit }) => (
                <View className="plan-target-row" key={label}>
                  <View className="plan-target-label">{label}</View>
                  <View className="plan-target-value">
                    {value == null ? "未设置" : `${numberText(value)} ${unit}`}
                  </View>
                </View>
              ))}
              <View className="plan-target-row">
                <View className="plan-target-label">每日运动</View>
                <View className="plan-target-value">
                  {plan.data.schedule.find((day) => day.date === today)
                    ?.exercise || "暂无当日安排"}
                </View>
              </View>
            </View>
          </View>
        ))}
      {data && (
        <Button
          className="plan-edit-trigger"
          onClick={() => setEditorOpen(true)}
        >
          {active ? "更改计划" : "设置目标，生成计划"}
        </Button>
      )}
      {data && editorOpen && (
        <FormModal
          title={active ? "更改计划" : "设置目标，生成计划"}
          className="plan-editor"
          busy={action.busy}
          onClose={() => setEditorOpen(false)}
          footer={
            <Button
              className="primary"
              disabled={action.busy || !!pending}
              loading={action.busy}
              onClick={propose}
            >
              生成计划草案
            </Button>
          }
        >
          {action.error && <View className="error">{action.error}</View>}
          {pending && (
            <View className="warning">
              上次请求结果待核对，请关闭弹窗后查看待核对操作。
            </View>
          )}
          <View className="muted">确认新计划后，目标才会更新。</View>
          <View className="target-fields">
            {targetKeys.map((key) => (
              <Field
                key={key}
                label={`${key === "exercise_minutes" ? "每周运动" : labels[key]} · ${key === "kcal" ? "kcal" : key === "exercise_minutes" ? "分钟" : "g"}`}
                numeric
                value={fields[key]}
                onChange={(value) => {
                  dirty.current = true;
                  setUsingDefaults(false);
                  setFields({ ...fields, [key]: value });
                }}
              />
            ))}
          </View>
          {data.defaults.status === "ready" && data.defaults.targets ? (
            <Button
              disabled={action.busy || !!pending}
              onClick={() => {
                if (!data.defaults.targets) return;
                setFields(fill(data.defaults.targets));
                setUsingDefaults(true);
                dirty.current = false;
              }}
            >
              使用建议目标
            </Button>
          ) : (
            <>
              <View className="warning">{data.defaults.reason}</View>
              <Button
                onClick={() => {
                  setEditorOpen(false);
                  void tab("settings");
                }}
              >
                补充或核对基础档案
              </Button>
            </>
          )}
          <Fold title="建议目标的参考说明">
            <View>{data.defaults.basis}</View>
            {data.defaults.warnings.map((warning, index) => (
              <View className="warning" key={index}>
                {warning}
              </View>
            ))}
            {data.defaults.sources.map((source) => (
              <View
                className="link field"
                key={source.url}
                onClick={() => Taro.setClipboardData({ data: source.url })}
              >
                {source.title} · 点击复制链接
              </View>
            ))}
          </Fold>
          <Textarea
            value={note}
            maxlength={500}
            placeholder="备注或需求（选填）"
            onInput={(event) => setNote(event.detail.value)}
          />
        </FormModal>
      )}
      {data && !data.plans.length && (
        <View className="card">尚无计划，可填写目标生成。</View>
      )}
    </View>
  );
}
