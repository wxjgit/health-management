import { useEffect, useRef, useState } from "react";
import Taro, { useDidHide, useDidShow, useReachBottom } from "@tarojs/taro";
import { Button, Text, Textarea, View } from "@tarojs/components";
import type { Daily, Event, Health, Message, Workflow } from "../../types";
import { api, errorText, queryString, requestId } from "../../api";
import {
  executeOperation,
  mutate,
  operation,
  privacyGeneration,
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
  Trace,
  Icon,
} from "../../components/common";
import FormModal from "../../components/form-modal";
import { DailyOverview } from "../../components/daily-overview";

const MESSAGE_PAGE_SIZE = 20;

export default function Chat() {
  const [olderMessages, setOlderMessages] = useState<Message[]>([]);
  const [olderLoading, setOlderLoading] = useState(false);
  const [olderError, setOlderError] = useState("");
  const [hasOlder, setHasOlder] = useState(false);
  const historyVersion = useRef(0);
  const historyBusy = useRef(false);
  const alive = useRef(true);
  function resetHistory() {
    historyVersion.current++;
    historyBusy.current = false;
    setOlderMessages([]);
    setOlderLoading(false);
    setOlderError("");
    setHasOlder(false);
  }
  useEffect(
    () => () => {
      alive.current = false;
      historyVersion.current++;
    },
    [],
  );
  useDidHide(() => {
    historyVersion.current++;
    historyBusy.current = false;
    setOlderLoading(false);
  });
  const resource = useResource(async () => {
    resetHistory();
    const version = historyVersion.current;
    const [messages, workflows, expired, health, daily] = await Promise.all([
      api<Message[]>(`/messages?limit=${MESSAGE_PAGE_SIZE}`),
      api<Workflow[]>("/workflows"),
      api<Workflow[]>("/workflows/expired"),
      api<Health>("/health"),
      api<Daily>("/dashboard"),
    ]);
    if (alive.current && version === historyVersion.current)
      setHasOlder(messages.length === MESSAGE_PAGE_SIZE);
    return { messages, workflows, expired, health, daily };
  });
  const action = useAction();
  const pending = useOperation();
  const [text, setText] = useState("");
  const [inputFocused, setInputFocused] = useState(false);
  const [overviewOpen, setOverviewOpen] = useState(false);
  useDidHide(() => setOverviewOpen(false));
  const [selectedId, setSelectedId] = useState("");
  const [correction, setCorrection] = useState<Event | null>(null);
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
    resetHistory();
    setText("");
    setSelectedId("");
    setCorrection(null);
    setOverviewOpen(false);
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
  const messageRounds: Message[][] = [];
  for (const message of [...olderMessages, ...(data?.messages || [])]) {
    if (message.role === "user" || !messageRounds.length) {
      messageRounds.push([]);
    }
    messageRounds[messageRounds.length - 1].push(message);
  }
  const newestMessages = messageRounds.reverse().flat();
  async function loadOlder() {
    if (
      !data ||
      resource.loading ||
      !hasOlder ||
      historyBusy.current ||
      pending
    )
      return;
    const first = olderMessages[0] || data.messages[0];
    if (!first) return;
    const version = historyVersion.current;
    const epoch = privacyGeneration();
    historyBusy.current = true;
    setOlderLoading(true);
    setOlderError("");
    try {
      const batch = await api<Message[]>(
        `/messages?${queryString({ limit: String(MESSAGE_PAGE_SIZE), before: first.id })}`,
      );
      if (
        !alive.current ||
        version !== historyVersion.current ||
        epoch !== privacyGeneration()
      )
        return;
      setOlderMessages((existing) => {
        const ids = new Set(
          [...existing, ...data.messages].map((row) => row.id),
        );
        return [...batch.filter((row) => !ids.has(row.id)), ...existing];
      });
      setHasOlder(batch.length === MESSAGE_PAGE_SIZE);
    } catch (error) {
      if (
        alive.current &&
        version === historyVersion.current &&
        epoch === privacyGeneration()
      )
        setOlderError(errorText(error));
    } finally {
      if (alive.current && version === historyVersion.current) {
        historyBusy.current = false;
        setOlderLoading(false);
      }
    }
  }
  useReachBottom(() => {
    if (!olderError) void loadOlder();
  });
  const selected = data?.workflows.find(
    (row) =>
      row.thread_id === selectedId &&
      row.pending_input?.kind === "clarification",
  );
  const effectiveLocal =
    data?.health.local_only || selected?.processing_mode === "local_only";
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
      void Taro.pageScrollTo({ scrollTop: 0, duration: 200 });
    });
  }
  return (
    <Page
      title="今天，记录一点好状态"
      error={action.error || resource.error}
      loading={resource.loading}
      refresh={resource.refresh}
    >
      <View className="page-intro">
        <Text>饮食、睡眠、运动，每一点都值得记下。</Text>
        <Button
          className="notice-button"
          size="mini"
          onClick={() => go("notifications")}
        >
          <Icon name="history" />
          站内消息
        </Button>
      </View>
      <View className="card composer">
        <View className="composer-heading">
          <View className="subtitle">记下此刻</View>
          <Text className="composer-caption">每一条，都有迹可循</Text>
        </View>
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
        {selected?.pending_input?.options?.map((option) => (
          <Button
            key={option.id}
            disabled={action.busy || !!pending || !!selected.retry_request}
            onClick={() => send(option)}
          >
            {option.label}
          </Button>
        ))}
        {data && (
          <View className="quick-records">
            {[
              { title: "饮食", mark: "食", text: "今天午餐吃了" },
              { title: "体重", mark: "kg", text: "今天早上体重" },
              { title: "睡眠", mark: "☾", text: "昨晚睡了" },
              { title: "运动", mark: "↗", text: "今天运动" },
            ].map((item) => (
              <Button
                className="quick-record"
                key={item.title}
                disabled={
                  action.busy || !!pending || !!selected || !!correction
                }
                onClick={() => setText(item.text)}
              >
                <View className="quick-mark">{item.mark}</View>
                <Text>{item.title}</Text>
                <Text className="quick-plus">＋</Text>
              </Button>
            ))}
          </View>
        )}
        <Textarea
          value={text}
          disabled={action.busy || !!pending || !!selected?.retry_request}
          maxlength={4000}
          placeholder={
            inputFocused ? "" : selected ? "补充所选问题…" : "用一句话开始记录…"
          }
          onFocus={() => setInputFocused(true)}
          onBlur={() => setInputFocused(false)}
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
      {data && (
        <>
          <Button
            className="daily-overview-trigger"
            onClick={() => setOverviewOpen(true)}
          >
            查看今日状态
          </Button>
          {overviewOpen && (
            <FormModal
              title="今日状态"
              className="daily-overview-modal"
              onClose={() => setOverviewOpen(false)}
              footer={
                <Button
                  className="primary"
                  onClick={() => {
                    setOverviewOpen(false);
                    void go("today");
                  }}
                >
                  今日汇总
                </Button>
              }
            >
              <DailyOverview daily={data.daily} />
            </FormModal>
          )}

          {data.health.mode === "demo" && (
            <View className="mode-note">
              演示数据仅供体验，营养信息不完整时保持未知。
            </View>
          )}
          {!data.messages.length && (
            <View className="card starter-card">
              <View className="section-kicker">A LITTLE EVERY DAY</View>
              <View className="subtitle">从第一条记录开始</View>
              <View className="muted">不用填表，像聊天一样记下今天。</View>
              <View className="starter-examples">
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
            </View>
          )}
          {newestMessages.map((message) => (
            <View
              key={message.id}
              className={`card message ${message.role === "user" ? "user" : ""}`}
            >
              {message.role !== "user" && (
                <View className="message-meta">
                  <View className="message-avatar">✦</View>
                  <Text>轻记 · 健康助手</Text>
                </View>
              )}
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
          {olderError && (
            <View className="muted">较早对话加载失败，请重试。</View>
          )}
          {hasOlder && (
            <Button
              className="chat-load-more"
              disabled={olderLoading || resource.loading || !!pending}
              loading={olderLoading}
              onClick={() => void loadOlder()}
            >
              {olderLoading
                ? "正在加载…"
                : olderError
                  ? "重新加载"
                  : "查看更早对话"}
            </Button>
          )}
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
                      。可以重新提交这份回答。
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
                      重新提交回答
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
                          "取消补充回答",
                          "已保存的记录保留，后续输入作为新记录处理。",
                        )
                      ) {
                        await mutate(`/workflows/${row.thread_id}`, "DELETE");
                        if (selectedId === row.thread_id) setSelectedId("");
                      }
                    })
                  }
                >
                  取消补充
                </Button>
              </View>
            ))}
          {data.workflows.some(
            (row) => row.pending_input?.kind === "plan_confirmation",
          ) && (
            <Button onClick={() => tab("settings")}>前往我的，确认计划</Button>
          )}
          {!!data.expired.length && (
            <Fold title={`已结束的问答 · ${data.expired.length}`}>
              <View className="muted">
                如需继续，请重新描述；已保存的记录可在历史中查看。
              </View>
              {data.expired.map((row) => (
                <View className="field" key={row.thread_id}>
                  {row.reply || row.pending_input?.question || "这次问答已结束"}
                </View>
              ))}
            </Fold>
          )}
        </>
      )}
    </Page>
  );
}
