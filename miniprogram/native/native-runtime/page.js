const { errorText } = require("./api");
const state = require("./state");
const { go, confirmAction } = require("./ui");
function registerPage(options) {
  const {
    data = {},
    load,
    route,
    poll = 0,
    shouldPoll,
    publicPage = false,
    allowPrivacy = false,
    onLoad,
    onShow,
    onHide,
    onUnload,
    onReset,
    ...methods
  } = options;
  const initial = JSON.parse(
    JSON.stringify({
      loading: false,
      busy: false,
      error: "",
      saved: false,
      modal: "",
      pending: null,
      ...data,
    }),
  );
  Page({
    data: initial,
    ...methods,
    onLoad(params) {
      this._sequence = 0;
      this._unloaded = false;
      this._initial = JSON.parse(JSON.stringify(initial));
      this.params = params || {};
      this._unsubscribe = state.subscribe(() => {
        this.syncPending();
        if (this._visible && !this._actionBusy) void this.reload();
      });
      this._reset = state.subscribeReset(() => {
        this._sequence++;
        this._historyVersion = (this._historyVersion || 0) + 1;
        this.setData({
          ...JSON.parse(JSON.stringify(this._initial)),
          busy: !!this._actionBusy,
        });
        this.syncPending();
        this.syncModal();
        onReset?.call(this);
      });
      onLoad?.call(this, params || {});
    },
    onShow() {
      this._visible = true;
      if (!publicPage && !require("./api").accountKey()) {
        this._visible = false;
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      this.syncTab();
      this.syncPending();
      onShow?.call(this);
      if (load) void this.reload();
      if (poll)
        this._poll = setInterval(() => {
          if (
            this._visible &&
            !this._actionBusy &&
            (!shouldPoll || shouldPoll.call(this))
          )
            void this.reload();
        }, poll);
    },
    onReady() {
      this.syncTab();
    },
    onHide() {
      this._visible = false;
      this._sequence++;
      this._historyVersion = (this._historyVersion || 0) + 1;
      clearInterval(this._poll);
      this.setData({ modal: "", loading: false });
      this.syncModal();
      onHide?.call(this);
    },
    onUnload() {
      this._unloaded = true;
      this._sequence++;
      clearInterval(this._poll);
      this._unsubscribe?.();
      this._reset?.();
      this.syncModal(false);
      onUnload?.call(this);
    },
    syncTab() {
      if (route) this.getTabBar?.()?.setActiveRoute?.(route);
    },
    syncModal(open = !!this.data.modal) {
      this.getTabBar?.()?.setData?.({ hidden: open });
    },
    syncPending() {
      if (!this._unloaded) this.setData({ pending: state.pendingOperation() });
    },
    async reload() {
      if (!load || this._actionBusy || this._unloaded) return;
      if (!publicPage && !require("./api").accountKey()) return;
      const sequence = ++this._sequence,
        epoch = state.privacyGeneration();
      if (state.privacyBlocked() && !allowPrivacy) {
        this.setData({
          ...JSON.parse(JSON.stringify(this._initial)),
          error: "隐私清理结果尚待核对，请在我的 → 隐私管理中恢复。",
        });
        return;
      }
      this.setData({ loading: true, error: "" });
      try {
        const next = await load.call(this);
        if (
          this._visible &&
          sequence === this._sequence &&
          epoch === state.privacyGeneration()
        )
          this.setData(next || {});
      } catch (error) {
        if (
          this._visible &&
          sequence === this._sequence &&
          epoch === state.privacyGeneration()
        )
          this.setData({ error: errorText(error) });
      } finally {
        if (this._visible && sequence === this._sequence)
          this.setData({ loading: false });
      }
    },
    async run(task) {
      if (this._actionBusy) return false;
      this._actionBusy = true;
      this.setData({ busy: true, error: "" });
      let success = false;
      try {
        await task();
        success = true;
        return true;
      } catch (error) {
        if (!this._unloaded) this.setData({ error: errorText(error) });
        return false;
      } finally {
        this._actionBusy = false;
        if (!this._unloaded) {
          this.setData({ busy: false });
          this.syncPending();
          if (success && this._visible && load) void this.reload();
        }
      }
    },
    onGo(event) {
      return go(event.currentTarget.dataset.page);
    },
    onOpen(event) {
      this.setData({ modal: event.currentTarget.dataset.modal });
      this.syncModal();
    },
    onClose() {
      if (!this.data.busy) {
        this.setData({ modal: "" });
        this.syncModal();
      }
    },
    onField(event) {
      const { group = "form", name, value } = event.detail;
      this.setData({ [`${group}.${name}`]: value });
      this.onFieldsChanged?.(group, name);
    },
    onPendingRetry() {
      const pending = state.pendingOperation();
      if (pending) return this.run(() => state.executeOperation(pending));
    },
    onAcknowledge() {
      return this.run(async () => {
        if (state.pendingOperation()?.path === "/deletions")
          return go("privacy");
        if (
          await confirmAction(
            "确认操作结果",
            "这不会取消服务器操作，也不会撤销已保存数据。请先核对历史或流程结果；继续后再次提交可能创建新记录。",
          )
        )
          state.acknowledgeOperation();
      });
    },
  });
}
module.exports = { registerPage };
