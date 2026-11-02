const {
  api,
  requestId,
  queryString,
  errorText,
} = require("../../native-runtime/api");
const state = require("../../native-runtime/state");
const { registerPage } = require("../../native-runtime/page");
const { confirmAction, go, numberText } = require("../../native-runtime/ui");
const PAGE_SIZE = 20;
function rounds(messages) {
  const groups = [];
  for (const message of messages) {
    if (message.role === "user" || !groups.length) groups.push([]);
    groups[groups.length - 1].push(message);
  }
  return groups.reverse().flat();
}
function dailyView(d) {
  if (!d) return null;
  const intake = d.totals.kcal,
    target = d.targets?.kcal;
  return {
    ...d,
    dateText: d.date.replace(/-/g, "."),
    weightText: numberText(d.weight_kg),
    sleepText: numberText(
      d.sleep_minutes == null ? null : d.sleep_minutes / 60,
    ),
    intakeText: numberText(intake, 0),
    progress:
      intake != null && target
        ? Math.max(0, Math.min(100, (intake / target) * 100))
        : 0,
    targetText: target ? `目标 ${numberText(target, 0)}` : "目标待设置",
  };
}
registerPage({
  route: "pages/chat/index",
  data: {
    loaded: false,
    text: "",
    selectedId: "",
    selected: null,
    correction: null,
    local: true,
    messages: [],
    olderMessages: [],
    hasOlder: false,
    olderLoading: false,
    olderError: "",
    workflows: [],
    expired: [],
    examples: [
      "今天早上体重65公斤",
      "昨晚睡了7小时",
      "今天跑步30分钟",
      "中午吃了牛肉面",
    ],
    quick: [
      { label: "饮食", text: "今天午餐吃了" },
      { label: "体重", text: "今天早上体重" },
      { label: "睡眠", text: "昨晚睡了" },
      { label: "运动", text: "今天运动" },
    ],
  },
  onReset() {
    this._olderBusy = false;
  },
  onShow() {
    const event = wx.getStorageSync("correction-event");
    if (event) {
      this.setData({
        correction: event,
        text: event.raw_text,
        selectedId: "",
        selected: null,
      });
      wx.removeStorageSync("correction-event");
    }
  },
  onHide() {
    this._olderBusy = false;
    this.setData({ olderLoading: false });
  },
  async load() {
    this._historyVersion = (this._historyVersion || 0) + 1;
    this._olderBusy = false;
    const [messages, workflows, expired, health, daily, consent] =
      await Promise.all([
        api(`/messages?limit=${PAGE_SIZE}`),
        api("/workflows"),
        api("/workflows/expired"),
        api("/health"),
        api("/dashboard"),
        api("/auth/consent"),
      ]);
    const selected =
      workflows.find(
        (w) =>
          w.thread_id === this.data.selectedId &&
          w.pending_input?.kind === "clarification",
      ) || null;
    return {
      loaded: true,
      messages,
      olderMessages: [],
      newestMessages: rounds(messages),
      hasOlder: messages.length === PAGE_SIZE,
      olderLoading: false,
      olderError: "",
      workflows: workflows.filter(
        (w) => w.pending_input?.kind === "clarification",
      ),
      hasPlan: workflows.some(
        (w) => w.pending_input?.kind === "plan_confirmation",
      ),
      expired,
      health,
      daily: dailyView(daily),
      consent,
      selected,
      local:
        !consent.model_allowed ||
        health.local_only ||
        selected?.processing_mode === "local_only",
    };
  },
  onInput(e) {
    this.setData({ text: e.detail.value });
  },
  onFocus() {
    this.setData({ focused: true });
  },
  onBlur() {
    this.setData({ focused: false });
  },
  onExample(e) {
    if (
      !this.data.busy &&
      !state.pendingOperation() &&
      !this.data.selected &&
      !this.data.correction
    )
      this.setData({ text: e.currentTarget.dataset.text });
  },
  onNew() {
    if (!this.data.busy && !state.pendingOperation())
      this.setData({
        selectedId: "",
        selected: null,
        correction: null,
        text: "",
        local:
          !this.data.consent?.model_allowed || this.data.health?.local_only,
      });
  },
  onSelect(e) {
    const selected = this.data.workflows.find(
      (w) => w.thread_id === e.currentTarget.dataset.id,
    );
    if (
      !selected ||
      selected.status === "resuming" ||
      selected.retry_request ||
      state.pendingOperation()
    )
      return;
    this.setData({
      selected,
      selectedId: selected.thread_id,
      correction: null,
      local:
        !this.data.consent.model_allowed ||
        this.data.health.local_only ||
        selected.processing_mode === "local_only",
    });
    wx.pageScrollTo({ scrollTop: 0, duration: 200 });
  },
  onSend(e) {
    const selected = this.data.selected,
      option = (selected?.pending_input.options || []).find(
        (o) => o.id === e?.currentTarget?.dataset.option,
      );
    if (
      !this.data.loaded ||
      state.pendingOperation() ||
      (!option && !this.data.text.trim()) ||
      selected?.retry_request
    )
      return;
    return this.run(async () => {
      const local =
        !this.data.consent?.model_allowed ||
        this.data.health?.local_only ||
        selected?.processing_mode === "local_only";
      const body = {
        request_id: requestId(),
        text: option?.label || this.data.text.trim(),
        processing_mode: local ? "local_only" : "model_allowed",
        ...(selected
          ? {
              interrupt_id: selected.pending_input.interrupt_id,
              ...(option ? { event_id: option.id } : {}),
            }
          : {
              conversation_id: "main",
              event_id: this.data.correction?.id || null,
            }),
      };
      await state.executeOperation(
        state.operation(
          selected ? `/workflows/${selected.thread_id}/resume` : "/chat",
          body,
          selected ? "补充回答" : "健康记录",
        ),
      );
      this.setData({
        text: "",
        selectedId: "",
        selected: null,
        correction: null,
      });
      wx.pageScrollTo({ scrollTop: 0, duration: 200 });
    });
  },
  onRetry(e) {
    const row = this.data.workflows.find(
      (w) => w.thread_id === e.currentTarget.dataset.id,
    );
    if (row?.retry_request)
      return this.run(() =>
        state.executeOperation(
          state.operation(
            `/workflows/${row.thread_id}/resume`,
            row.retry_request,
            "恢复原回答",
          ),
        ),
      );
  },
  onCancel(e) {
    const id = e.currentTarget.dataset.id;
    return this.run(async () => {
      if (
        await confirmAction(
          "取消补充回答",
          "已保存的记录保留，后续输入作为新记录处理。",
        )
      ) {
        await state.mutate(`/workflows/${id}`, "DELETE");
        if (this.data.selectedId === id)
          this.setData({ selectedId: "", selected: null });
      }
    });
  },
  onSummary() {
    this.onClose();
    return go("today");
  },
  onReachBottom() {
    if (!this.data.olderError) void this.loadOlder();
  },
  async loadOlder() {
    if (
      !this.data.loaded ||
      this.data.loading ||
      !this.data.hasOlder ||
      this._olderBusy ||
      state.pendingOperation()
    )
      return;
    const first = this.data.olderMessages[0] || this.data.messages[0];
    if (!first) return;
    const sequence = this._historyVersion,
      epoch = state.privacyGeneration();
    this._olderBusy = true;
    this.setData({ olderLoading: true, olderError: "" });
    try {
      const batch = await api(
        `/messages?${queryString({ limit: String(PAGE_SIZE), before: first.id })}`,
      );
      if (
        !this._visible ||
        this._unloaded ||
        sequence !== this._historyVersion ||
        epoch !== state.privacyGeneration()
      )
        return;
      const ids = new Set(
          [...this.data.olderMessages, ...this.data.messages].map((r) => r.id),
        ),
        olderMessages = [
          ...batch.filter((r) => !ids.has(r.id)),
          ...this.data.olderMessages,
        ];
      this.setData({
        olderMessages,
        newestMessages: rounds([...olderMessages, ...this.data.messages]),
        hasOlder: batch.length === PAGE_SIZE,
      });
    } catch (e) {
      if (
        this._visible &&
        sequence === this._historyVersion &&
        epoch === state.privacyGeneration()
      )
        this.setData({ olderError: errorText(e) });
    } finally {
      if (this._visible && sequence === this._historyVersion) {
        this._olderBusy = false;
        this.setData({ olderLoading: false });
      }
    }
  },
});
