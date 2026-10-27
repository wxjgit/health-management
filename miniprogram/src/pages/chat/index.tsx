import { useEffect, useState } from "react";
import Taro, { useDidShow } from "@tarojs/taro";
import { Button, Switch, Text, Textarea, View } from "@tarojs/components";
import type { Daily, Event, Health, Message, Workflow } from "../../types";
import { api, requestId } from "../../api";
import {
  executeOperation,
  mutate,
  operation,
  useAction,
  useOperation,
  usePrivateReset,
  useResource,
} from "../../state";
import {
  confirm,
  EvidenceList,
  Fold,
  go,
  MealCard,
  Page,
  tab,
  TargetValues,
  Trace,
} from "../../components/common";

export default function Chat() {
  const resource = useResource(async () => {
    const [messages, workflows, expired, health, daily] = await Promise.all([
      api<Message[]>("/messages"),
      api<Workflow[]>("/workflows"),
      api<Workflow[]>("/workflows/expired"),
      api<Health>("/health"),
      api<Daily>("/dashboard"),
    ]);
    return { messages, workflows, expired, health, daily };
  });
  const action = useAction();
  const pending = useOperation();
  const [text, setText] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [correction, setCorrection] = useState<Event | null>(null);
  const [localOnly, setLocalOnly] = useState(
    Taro.getStorageSync("local-only") !== false,
  );
  const [elapsed, setElapsed] = useState(0);
  useDidShow(() => {
    const event = Taro.getStorageSync<Event>("correction-event");
    if (event) {
      setCorrection(event);
      setSelectedId("");
      setText(event.raw_text);
      Taro.removeStorageSync("correction-event");
    }
  });
  usePrivateReset(() => {
    setText("");
    setSelectedId("");
    setCorrection(null);
  });
  useEffect(() => {
    if (!action.busy) {
      setElapsed(0);
      return;
    }
    const start = Date.now();
    const timer = setInterval(
      () => setElapsed(Math.round((Date.now() - start) / 1000)),
      1000,
    );
    return () => clearInterval(timer);
  }, [action.busy]);
  const data = resource.data;
  const selected = data?.workflows.find(
    (row) =>
      row.thread_id === selectedId &&
      row.pending_input?.kind === "clarification",
  );
  const effectiveLocal =
    localOnly ||
    data?.health.local_only ||
    selected?.processing_mode === "local_only";
  async function send(option?: { id: string; label: string }) {
    if (!data || pending || (!option && !text.trim())) return;
    await action.run(async () => {
      const body = {
        request_id: requestId(),
        text: option?.label || text.trim(),
        processing_mode: effectiveLocal ? "local_only" : "model_allowed",
        ...(selected
          ? {
              interrupt_id: selected.pending_input!.interrupt_id,
              ...(option ? { event_id: option.id } : {}),
            }
          : { conversation_id: "main", event_id: correction?.id || null }),
      };
      await executeOperation(
        operation(
          selected ? `/workflows/${selected.thread_id}/resume` : "/chat",
          body,
          selected ? "补充回答" : "健康记录",
        ),
      );
      setText("");
      setSelectedId("");
      setCorrection(null);
      void Taro.pageScrollTo({ scrollTop: 999999, duration: 200 });
    });
  }
  return (
    <Page
      title="今天，从一句话开始"
      error={action.error || resource.error}
      loading={resource.loading}
      refresh={resource.refresh}
    >
      <View className="row">
        <Button size="mini" onClick={() => go("today")}>
          今日汇总
        </Button>
        <Button size="mini" onClick={() => go("notifications")}>
          站内消息
        </Button>
      </View>
      {data && (
        <>
          <View className="card">
            <View>
              {data.health.today} · 今日已记录 {data.daily.record_count} 条
            </View>
            <TargetValues targets={data.daily.totals} />
            {!!data.daily.totals.unknown_items && (
              <View className="warning">营养尚不完整，未知不按零计。</View>
            )}
          </View>
          {data.health.mode === "demo" && (
            <View className="warning">
              演示服务：固定营养示例未经核验；纯本地模式下缺失营养保持未知。
            </View>
          )}
          {!data.messages.length && (
            <View className="card">
              <View>记下吃了什么、睡得怎样、如何运动。</View>
              {[
                "今天早上体重65公斤",
                "昨晚睡了7小时",
                "今天跑步30分钟",
                "中午吃了牛肉面",
              ].map((example) => (
                <Button key={example} onClick={() => setText(example)}>
                  {example}
                </Button>
              ))}
            </View>
          )}
          {data.messages.map((message) => (
            <View
              key={message.id}
              className={`card message ${message.role === "user" ? "user" : ""}`}
            >
              <View className="muted">
                {message.role === "user" ? "我" : "轻记"}
              </View>
              <Text userSelect>{message.text}</Text>
              {message.cards?.map((card, index) => (
                <MealCard key={index} card={card} />
              ))}
              <Trace
                rows={message.process_trace}
                duration={message.duration_ms}
              />
              <EvidenceList rows={message.evidence_refs} />
            </View>
          ))}
          {data.workflows
            .filter((row) => row.pending_input?.kind === "clarification")
            .map((row) => (
              <View className="card" key={row.thread_id}>
                <View className="tag">
                  {row.pending_input?.optional ? "可选补充" : "待补充"} ·{" "}
                  {row.status === "resuming"
                    ? "处理中"
                    : row.status === "resume_failed"
                      ? "上次补充失败"
                      : "等待回答"}
                </View>
                <View>
                  {row.pending_input?.prompt || row.pending_input?.question}
                </View>
                {row.retry_request ? (
                  <>
                    <View className="muted">
                      上次回答：{row.retry_request.text}
                      。重试将沿用原内容和处理模式。
                    </View>
                    <Button
                      disabled={
                        action.busy || !!pending || row.status === "resuming"
                      }
                      onClick={() =>
                        action.run(() =>
                          executeOperation(
                            operation(
                              `/workflows/${row.thread_id}/resume`,
                              row.retry_request!,
                              "恢复原回答",
                            ),
                          ),
                        )
                      }
                    >
                      重试原答案
                    </Button>
                  </>
                ) : (
                  <Button
                    disabled={
                      action.busy || !!pending || row.status === "resuming"
                    }
                    onClick={() => {
                      setSelectedId(row.thread_id);
                      setCorrection(null);
                    }}
                  >
                    {selectedId === row.thread_id
                      ? "已选择此追问"
                      : "回答此追问"}
                  </Button>
                )}
                <Button
                  disabled={action.busy || !!pending}
                  onClick={() =>
                    action.run(async () => {
                      if (
                        await confirm(
                          "取消待补充流程",
                          "已保存的记录保留，后续输入作为新记录处理。",
                        )
                      ) {
                        await mutate(`/workflows/${row.thread_id}`, "DELETE");
                        if (selectedId === row.thread_id) setSelectedId("");
                      }
                    })
                  }
                >
                  取消此流程
                </Button>
              </View>
            ))}
          {data.workflows.some(
            (row) => row.pending_input?.kind === "plan_confirmation",
          ) && (
            <Button onClick={() => tab("plans")}>查看并确认待处理计划</Button>
          )}
          {!!data.expired.length && (
            <Fold title={`已过期上下文 · ${data.expired.length}`}>
              <View className="muted">
                不能继续旧追问，可以重新描述；已保存记录仍可在历史中查看。
              </View>
              {data.expired.map((row) => (
                <View className="field" key={row.thread_id}>
                  {row.reply || row.pending_input?.question || "上下文已过期"}
                </View>
              ))}
            </Fold>
          )}
        </>
      )}
      <View className="card composer">
        {correction && (
          <View className="warning">正在纠正：{correction.raw_text}</View>
        )}
        {selected && (
          <View className="warning">
            正在回答：{selected.pending_input?.question}
          </View>
        )}
        {(selected || correction) && (
          <Button
            disabled={action.busy || !!pending}
            onClick={() => {
              setSelectedId("");
              setCorrection(null);
              setText("");
            }}
          >
            另开一条新记录
          </Button>
        )}
        <View>
          纯本地处理{" "}
          <Switch
            checked={!!effectiveLocal}
            disabled={
              action.busy ||
              !!pending ||
              !!data?.health.local_only ||
              selected?.processing_mode === "local_only"
            }
            onChange={(event) => {
              setLocalOnly(event.detail.value);
              Taro.setStorageSync("local-only", event.detail.value);
            }}
          />
        </View>
        {effectiveLocal ? (
          <View className="muted">
            本次不调用外部模型。缺失营养保持未知，复杂表达请明确日期和内容。
          </View>
        ) : (
          <Fold
            title={`外发说明：${data?.health.model_provider || "演示服务"} · ${data?.health.model_name || "无模型"}`}
          >
            <View className="muted">
              首次发送本次输入、当前时间和操作状态。纠错仅发送所选单条记录，补充仅发送当前草稿；建议按问题发送相关餐食、近7天饮食汇总、近28天体重和已确认偏好限制，分量建议可能使用基础档案。营养估算仅发送食物分量，表达任务仅发送批准的回复要点，历史聊天原文不发送。
            </View>
          </Fold>
        )}
        {selected?.pending_input?.options?.map((option) => (
          <Button
            key={option.id}
            disabled={action.busy || !!pending || !!selected.retry_request}
            onClick={() => send(option)}
          >
            {option.label}
          </Button>
        ))}
        <Textarea
          value={text}
          disabled={action.busy || !!pending || !!selected?.retry_request}
          maxlength={4000}
          placeholder={selected ? "补充所选问题…" : "用一句话开始记录…"}
          onInput={(event) => setText(event.detail.value)}
          adjustPosition
        />
        {action.busy && (
          <View className="muted">
            正在处理 · {elapsed} 秒，随后将校验并保存或查询。
          </View>
        )}
        <Button
          className="primary"
          loading={action.busy}
          disabled={
            action.busy ||
            !!pending ||
            !data ||
            !text.trim() ||
            !!selected?.retry_request
          }
          onClick={() => send()}
        >
          发送
        </Button>
      </View>
    </Page>
  );
}
