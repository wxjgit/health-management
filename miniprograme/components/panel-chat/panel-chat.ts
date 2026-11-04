// components/panel-chat/panel-chat.ts — 记录（对话）面板 · 虚拟页
// 契约：POST /api/chat → { thread_id, status, reply, pending_input?, process_trace, saved_record_ids }
// 追问：status=waiting_input 时保存 thread_id+interrupt_id，下条输入走 resume。
import { chat, insights } from '../../runtime/api';
import { genRequestId } from '../../runtime/request';
import { notify, showApiError } from '../../runtime/feedback';
import { fmtTime } from '../../runtime/format';
import * as voice from '../../runtime/voice';

interface Msg {
  id: number; role: 'user' | 'bot'; text: string; time: string;
  savedCount?: number; trace?: any[]; traceOpen?: boolean;
}
let msgSeq = 0;

Component({
  options: { addGlobalClass: true },
  properties: {
    active: { type: Boolean, value: false },   // 宿主切换时置真，触发恢复/刷新
  },
  observers: {
    active(v: boolean) { if (v) this.onPanelShow(); },
  },
  data: {
    messages: [] as Msg[],
    draft: '', sending: false, focus: false, scrollTo: '',
    mode: 'local_only', unread: 0, offline: false,
    recording: false, voiceText: '',
    pendingThread: '', pendingInterrupt: '', pendingQuestion: '', pendingRequired: true,
    quick: [
      { icon: 'bowl', text: '记一餐' },
      { icon: 'scale', text: '记体重' },
      { icon: 'moon', text: '记睡眠' },
      { icon: 'activity', text: '记运动' },
      { icon: 'flower', text: '记周期' },
      { icon: 'search', text: '查最近7天' },
    ],
  },
  lifetimes: {
    attached() {
      const g = getApp().globalData;
      this.setData({ mode: g.processingMode || 'local_only' });
      wx.onNetworkStatusChange((r: any) => this.setData({ offline: !r.isConnected }));
      wx.getNetworkType({ success: (r: any) => this.setData({ offline: r.networkType === 'none' }) });
      this.welcome();
    },
  },
  // 宿主页 onShow（如从授权页返回）时同步模式标记，避免标签滞留旧值
  pageLifetimes: {
    show() {
      const g = getApp().globalData;
      if (g.processingMode !== this.data.mode) this.setData({ mode: g.processingMode });
    },
  },
  methods: {
    onPanelShow() {
      const g = getApp().globalData;
      if (g.processingMode !== this.data.mode) this.setData({ mode: g.processingMode });
      this.restorePending();
      this.loadUnread();
    },

    async restorePending() {
      try {
        const list = await chat.waiting(false);
        const waiting = (list || []).find((w: any) => w.kind === 'chat' && w.status === 'waiting_input');
        if (waiting && waiting.result && waiting.result.pending_input) {
          const p = waiting.result.pending_input;
          this.setData({
            pendingThread: waiting.result.thread_id || waiting.thread_id || '',
            pendingInterrupt: p.interrupt_id || '',
            pendingQuestion: p.question || '有一条记录待补充',
            pendingRequired: p.required !== false,
          });
        }
      } catch (e) {}
    },

    async loadUnread() {
      try {
        const list = await insights.notifications();
        this.setData({ unread: (list || []).filter((n: any) => !n.read && !n.read_at).length });
      } catch (e) {}
    },

    welcome() {
      this.push('bot', '早上好，我是轻记\n直接用一句话记录就好，比如：\n· 「中午吃了一大碗牛肉面，加了一个卤蛋」\n· 「今天早上体重65公斤」\n· 「昨晚睡了7小时」\n\n分量不确定也没关系，我会追问；说「不知道」即可跳过。');
    },

    push(role: 'user' | 'bot', text: string, extra: Partial<Msg> = {}): Msg {
      const m: Msg = { id: ++msgSeq, role, text, time: fmtTime(new Date().toTimeString().slice(0, 8)), ...extra };
      const messages = this.data.messages.concat(m);
      this.setData({ messages }, () => this.setData({ scrollTo: 'm' + m.id }));
      return m;
    },

    onInput(e: any) { this.setData({ draft: e.detail.value }); },

    fillQuick(e: any) {
      const text = e.currentTarget.dataset.text;
      const map: Record<string, string> = {
        '记一餐': '中午吃了一大碗牛肉面，加了一个卤蛋',
        '记体重': '今天早上体重65公斤',
        '记睡眠': '昨晚睡了7小时',
        '记运动': '今天跑步30分钟，有点累',
        '记周期': '今天月经开始了',
        '查最近7天': '查看最近7天的饮食记录',
      };
      this.setData({ draft: map[text] || text, focus: true });
    },

    focusInput() { this.setData({ focus: true }); },

    noop() {},

    // 语音输入：长按输入框或按住麦克风键开始，松手结束并把识别文本填入输入框
    async onMicDown() {
      if (this.data.recording || this.data.sending) return;
      this.setData({ recording: true, voiceText: '' });
      const started = await voice.start({
        onPartial: (text) => this.setData({ voiceText: text }),
        onEnd: (text) => {
          this.setData({ recording: false, voiceText: '' });
          if (text) {
            this.setData({ draft: (this.data.draft ? this.data.draft + ' ' : '') + text, focus: true });
            notify.success('语音已填入，可直接发送或编辑');
          } else {
            notify.warn('没有听清，请再试一次');
          }
        },
        onError: (msg) => {
          this.setData({ recording: false, voiceText: '' });
          notify.error(msg);
        },
      });
      if (!started && this.data.recording) this.setData({ recording: false });
    },

    onMicUp() {
      if (!this.data.recording) return;
      voice.stop();   // onStop 回调里收尾（填文本/提示）
    },

    async onSend() {
      const text = (this.data.draft || '').trim();
      if (!text || this.data.sending) return;
      this.setData({ draft: '', sending: true });
      this.push('user', text);

      if (this.data.offline) {
        this.setData({ sending: false });
        this.push('bot', '已保存到本地队列（编号已固定），联网后按原编号重试，不会重复记录。', { trace: [{ title: '离线缓存', detail: 'request_id 已持久化，恢复网络后可重发', source: 'local' }] });
        return;
      }

      try {
        let res;
        if (this.data.pendingThread && this.data.pendingInterrupt) {
          res = await chat.resume(this.data.pendingThread, {
            interrupt_id: this.data.pendingInterrupt, text, requestId: genRequestId('resume'),
          });
        } else {
          res = await chat.send(text, { requestId: genRequestId('chat') });
        }
        this.renderResult(res);
      } catch (e: any) {
        if (e && e.status === 0) {
          this.setData({ offline: true });
          this.push('bot', '网络异常，本条已缓存，恢复后按原编号重试。');
        } else {
          showApiError(e);
          this.push('bot', '处理失败：' + (e.message || '未知错误') + '\n你可以重试或换一种说法。');
        }
      } finally {
        this.setData({ sending: false });
      }
    },

    renderResult(res: any) {
      const saved = (res.saved_record_ids || []).length;
      const trace = res.process_trace || [];
      if (res.status === 'waiting_input' && res.pending_input) {
        const p = res.pending_input;
        this.setData({
          pendingThread: res.thread_id || '', pendingInterrupt: p.interrupt_id || '',
          pendingQuestion: p.question || '', pendingRequired: p.required !== false,
        });
      } else {
        this.setData({ pendingThread: '', pendingInterrupt: '', pendingQuestion: '' });
      }
      this.push('bot', res.reply || '（无回复）', { savedCount: saved || undefined, trace });
    },

    toggleTrace(e: any) {
      const id = e.currentTarget.dataset.id;
      this.setData({ messages: this.data.messages.map((m: Msg) => m.id === id ? { ...m, traceOpen: !m.traceOpen } : m) });
    },

    goMessages() { wx.navigateTo({ url: '/pages/messages/messages' }); },
    goHistory() { this.triggerEvent('goto', { tab: 1 }); },
  },
});
