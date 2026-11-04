// pages/prefs/prefs.ts — 评分权重与提醒 · Task
// 契约：GET/PUT /api/preferences → {weights:{energy,macros,satiety,adherence,snacking},
//   meal_times, summary_time, checkin_time, weekly_day(0-6), notifications_enabled}
//   GET /api/preferences/suggestion → {weights,reason,limitations} | null
import { profile } from '../../runtime/api';
import { notify, showApiError } from '../../runtime/feedback';

const WEIGHT_LABEL: Record<string, string> = {
  energy: '热量适配', macros: '营养均衡', satiety: '饱腹体验', adherence: '规律执行', snacking: '加餐控制',
};
const WEEK_LABEL = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

const DEFAULT_PREFS = {
  weights: { energy: 30, macros: 25, satiety: 20, adherence: 15, snacking: 10 },
  meal_times: { breakfast: '08:00', lunch: '12:00', dinner: '18:00' },
  summary_time: '21:00',
  checkin_time: '09:00',
  weekly_day: 6,
  notifications_enabled: true,
};

Page({
  data: {
    navH: 88,
    loading: true,
    saving: false,
    prefs: JSON.parse(JSON.stringify(DEFAULT_PREFS)),
    weights: [] as any[],
    weightSum: 100,
    weeklyDayLabel: '周六',
    suggestion: null as any,
    // 时间/日选择 popup
    timeShow: false,
    timeTitle: '',
    timeMode: 'summary' as 'summary' | 'checkin' | 'weekly',
    hours: Array.from({ length: 24 }, (_, i) => String(i).padStart(2, '0')),
    minutes: ['00', '15', '30', '45'],
    timePickerVal: [21, 0],
    // 周复盘日单列选择
    weekOptions: WEEK_LABEL.map((label, value) => ({ label, value })),
    weeklyPickerVal: [6],
  },

  onLoad() { this.measure(); },
  onShow() { this.load(); },

  measure() {
    try {
      const wi: any = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
      const menu = wx.getMenuButtonBoundingClientRect();
      const navH = (menu.top - wi.statusBarHeight) * 2 + menu.height + wi.statusBarHeight;
      this.setData({ navH });
    } catch (e) {}
  },

  async load() {
    try {
      const p = await profile.preferences();
      const prefs = { ...JSON.parse(JSON.stringify(DEFAULT_PREFS)), ...(p || {}) };
      if (p && p.weights) prefs.weights = { ...DEFAULT_PREFS.weights, ...p.weights };
      this.setData({ prefs, loading: false });
      this.syncWeights();
      this.loadSuggestion();
    } catch (e) {
      this.setData({ loading: false });
      showApiError(e);
    }
  },

  syncWeights() {
    const w = this.data.prefs.weights;
    const weights = Object.keys(WEIGHT_LABEL).map((k) => ({
      key: k, label: WEIGHT_LABEL[k], value: w[k] != null ? w[k] : 0,
    }));
    const weightSum = weights.reduce((s, x) => s + x.value, 0);
    this.setData({ weights, weightSum, weeklyDayLabel: WEEK_LABEL[this.data.prefs.weekly_day] || '周六' });
  },

  async loadSuggestion() {
    try {
      const s = await profile.weightSuggestion();
      if (s && s.weights) {
        const cur = this.data.prefs.weights;
        const rows = Object.keys(WEIGHT_LABEL).map((k) => ({
          key: k, label: WEIGHT_LABEL[k],
          from: cur[k] != null ? cur[k] : 0,
          to: s.weights[k] != null ? s.weights[k] : 0,
        }));
        this.setData({ suggestion: { rows, reason: s.reason || '', limitations: s.limitations || '', weights: s.weights } });
      } else {
        this.setData({ suggestion: null });
      }
    } catch (e) { this.setData({ suggestion: null }); }
  },

  onWeight(e: any) {
    const key = e.currentTarget.dataset.key;
    const value = e.detail.value;
    const prefs = { ...this.data.prefs, weights: { ...this.data.prefs.weights, [key]: value } };
    this.setData({ prefs });
    this.syncWeights();
  },

  onNotifyToggle(e: any) {
    this.setData({ 'prefs.notifications_enabled': e.detail.value });
  },

  acceptSuggestion() {
    const s = this.data.suggestion;
    if (!s) return;
    const prefs = { ...this.data.prefs, weights: { ...s.weights } };
    this.setData({ prefs, suggestion: null });
    this.syncWeights();
    notify.success('已采纳建议权重');
  },
  rejectSuggestion() {
    this.setData({ suggestion: null });
    notify.info('已保留当前权重');
  },

  // 时间选择（时:分）
  openTimePicker(mode: 'summary' | 'checkin', title: string) {
    const t = this.data.prefs[mode === 'summary' ? 'summary_time' : 'checkin_time'] || '09:00';
    const [h, m] = t.split(':');
    const mi = this.data.minutes.indexOf(m);
    this.setData({ timeShow: true, timeMode: mode, timeTitle: title, timePickerVal: [parseInt(h, 10), mi < 0 ? 0 : mi] });
  },
  pickSummaryTime() { this.openTimePicker('summary', '日报时间'); },
  pickCheckinTime() { this.openTimePicker('checkin', '记录提醒时间'); },
  pickWeeklyDay() {
    this.setData({
      timeShow: true, timeMode: 'weekly', timeTitle: '周复盘日',
      weeklyPickerVal: [this.data.prefs.weekly_day || 0],
    });
  },
  closeTime() { this.setData({ timeShow: false }); },
  onTimePick(e: any) { this.setData({ timePickerVal: e.detail.value }); },
  onWeeklyPick(e: any) { this.setData({ weeklyPickerVal: e.detail.value }); },

  confirmTime() {
    const mode = this.data.timeMode;
    if (mode === 'weekly') {
      const day = this.data.weeklyPickerVal[0] || 0;
      this.setData({ 'prefs.weekly_day': day });
    } else {
      const [hi, mi] = this.data.timePickerVal;
      const h = this.data.hours[hi] || '09';
      const m = this.data.minutes[mi] || '00';
      const key = mode === 'summary' ? 'summary_time' : 'checkin_time';
      this.setData({ [`prefs.${key}`]: `${h}:${m}` });
    }
    this.setData({ timeShow: false });
    this.syncWeights();
  },

  async onSave() {
    const sum = this.data.weightSum;
    if (Math.abs(sum - 100) > 0.01) {
      notify.warn(`权重合计须为 100%，当前 ${sum}%`);
      return;
    }
    this.setData({ saving: true });
    try {
      await profile.updatePreferences(this.data.prefs);
      notify.success('设置已保存');
      setTimeout(() => wx.navigateBack(), 700);
    } catch (e: any) {
      showApiError(e);
    } finally {
      this.setData({ saving: false });
    }
  },
});
