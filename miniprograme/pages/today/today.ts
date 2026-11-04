// pages/today/today.ts — 今日汇总 · Task
import { records } from '../../runtime/api';
import { eventVM } from '../../runtime/viewmodel';
import { notify, showApiError } from '../../runtime/feedback';
import { fmtDay, todayStr } from '../../runtime/format';

interface Ring { label: string; val: string; sub: string; pct: number; color: string; }

Page({
  data: {
    navH: 88,
    day: todayStr(),
    dateLabel: '',
    rings: [] as Ring[],
    targetKcal: '—',
    targetSource: '',
    remainingNote: '',
    weight: null as number | null,
    sleepH: null as number | null,
    exercise: 0,
    events: [] as any[],
    dietComplete: false,
    saving: false,
  },

  onLoad() {
    this.measure();
    this.setData({ dateLabel: fmtDay(this.data.day) });
  },
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
      const d = await records.dashboard(this.data.day);
      const totals = d.totals || {};
      const targets = d.targets || null;
      const remaining = d.remaining || null;
      this.setData({
        targetKcal: targets && targets.kcal != null ? String(Math.round(targets.kcal)) : '—',
        targetSource: targets ? '来自已确认计划 · 可修改' : '尚未设定目标',
        weight: d.weight_kg != null ? d.weight_kg : null,
        sleepH: d.sleep_minutes != null ? Math.round(d.sleep_minutes / 60 * 10) / 10 : null,
        exercise: d.exercise_minutes || 0,
        dietComplete: !!d.diet_complete,
        events: (d.events || []).map(eventVM),
        rings: this.buildRings(totals, targets),
        remainingNote: this.buildRemainingNote(remaining),
      });
    } catch (e) {
      showApiError(e);
    }
  },

  buildRings(totals: any, targets: any): Ring[] {
    const mk = (label: string, val: number | null, target: number | null, color: string, unit = ''): Ring => {
      const v = val != null ? Math.round(val) : 0;
      const pct = target ? Math.min(360, Math.round((v / target) * 360)) : 0;
      return { label, val: val != null ? String(v) : '—', sub: target ? '/' + Math.round(target) + unit : '未设', pct, color };
    };
    return [
      mk('热量', totals.kcal, targets && targets.kcal, '#4b9fe1'),
      mk('蛋白g', totals.protein_g, targets && targets.protein_g, '#09be4f'),
      mk('碳水g', totals.carbs_g, targets && targets.carbs_g, '#7ed8b5'),
      mk('脂肪g', totals.fat_g, targets && targets.fat_g, '#e8a33d'),
    ];
  },

  buildRemainingNote(remaining: any): string {
    if (!remaining || remaining.kcal == null) return '';
    const left = Math.round(remaining.kcal);
    if (left > 0) return `按已记录摄入，今天约剩 ${left} kcal（不含可能漏记的食物）。`;
    return `今日已记录摄入超过目标 ${Math.abs(left)} kcal，下一餐仍保持规律进餐。`;
  },

  async toggleComplete() {
    this.setData({ saving: true });
    try {
      const d = await records.markComplete(this.data.day, !this.data.dietComplete);
      this.setData({ dietComplete: !!d.diet_complete });
      notify.success(d.diet_complete ? '已标记完整' : '已撤销完整标记');
    } catch (e) {
      showApiError(e);
    } finally {
      this.setData({ saving: false });
    }
  },

  openEvent(e: any) {
    wx.navigateTo({ url: '/pages/event/event?id=' + e.currentTarget.dataset.id });
  },
});
