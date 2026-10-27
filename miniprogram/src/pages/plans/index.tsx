import { useEffect, useRef, useState } from "react";
import Taro from "@tarojs/taro";
import { Button, Textarea, View } from "@tarojs/components";
import type { DefaultTargets, Plan, Targets, Workflow } from "../../types";
import { api, requestId } from "../../api";
import {
  executeOperation,
  mutate,
  operation,
  useAction,
  useForegroundPoll,
  useOperation,
  usePrivateReset,
  useResource,
} from "../../state";
import {
  confirm,
  DerivedNote,
  EvidenceList,
  Field,
  Fold,
  labels,
  Page,
  tab,
  TargetValues,
} from "../../components/common";

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

export default function Plans() {
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
    <Page
      title="适合你的，才能坚持"
      loading={resource.loading}
      error={action.error || resource.error}
      refresh={resource.refresh}
    >
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
                  系统初始估算，不是医疗处方。{row.pending_input.target_basis}{" "}
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
                  {settled ? "更新确认结果" : "重试上次确认"}
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
      {data && (
        <View className="card">
          <View className="subtitle">个人目标</View>
          <View className="tag">
            {usingDefaults ? "系统默认 · 可修改" : "当前目标或手动编辑"}
          </View>
          <View className="muted">
            已有生效目标不会被默认估算覆盖。修改字段后按手动目标处理。
          </View>
          <View className="target-fields">
            {targetKeys.map((key) => (
              <Field
                key={key}
                label={`${labels[key]} ${key === "kcal" ? "kcal" : key === "exercise_minutes" ? "分钟/周" : "g"}`}
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
              恢复系统默认目标
            </Button>
          ) : (
            <>
              <View className="warning">{data.defaults.reason}</View>
              <Button onClick={() => tab("settings")}>
                补充或核对基础档案
              </Button>
            </>
          )}
          <Fold title="默认目标估算依据">
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
            placeholder="计划备注或需求"
            onInput={(event) => setNote(event.detail.value)}
          />
          <View className="muted">
            计划生成由服务端配置决定是否调用模型，聊天页本地开关仅用于聊天请求；强制不外发请在服务端配置纯本地模式。
          </View>
          <Button
            className="primary"
            disabled={action.busy || !!pending}
            loading={action.busy}
            onClick={propose}
          >
            生成或调整计划
          </Button>
        </View>
      )}
      {data?.plans.map((plan) => (
        <View className="card" key={plan.id}>
          <View className="subtitle">
            {(
              {
                active: "当前生效",
                pending: "待确认",
                superseded: "历史计划",
                rejected: "未采用",
                cancelled: "已取消",
              } as Record<string, string>
            )[plan.status] || plan.status}
          </View>
          <View className="muted">{plan.created_at}</View>
          <TargetValues targets={plan.data.targets} />
          <View className="field">下一餐：{plan.data.next_meal}</View>
          <View className="field">明日重点：{plan.data.tomorrow_focus}</View>
          <View className="muted">{plan.data.notes}</View>
          {plan.data.schedule.map((day) => (
            <View className="field" key={day.date}>
              {day.date}
              <View>饮食：{day.food}</View>
              <View>运动：{day.exercise}</View>
            </View>
          ))}
          <DerivedNote memory={plan.data.memory} />
          <EvidenceList rows={plan.data.evidence_refs} />
        </View>
      ))}
      {data && !data.plans.length && (
        <View className="card">尚无计划，可填写目标生成。</View>
      )}
    </Page>
  );
}
