// pages/trends/trends.ts — 历史趋势 · Task
// 契约：GET /api/trends?days → [dashboard,...]，每个含 {date, totals:{kcal,...}, weight_kg, targets}
//   GET /api/monthly-summaries → [{month, data}]
import { records, insights } from '../../runtime/api';
import { showApiError, notify } from '../../runtime/feedback';
import { fmtDayShort } from '../../runtime/format';

const RANGES = [
  { key: '7', text: '近 7 天', days: 7 },
  { key: '28', text: '近 28 天', days: 28 },
  { key: '90', text: '近 90 天', days: 90 },
];

Page({
  data: {
    navH: 88,
    loading: true,
    rangeKey: '7',
    days: 7,
    rangeTabs: RANGES,
    kcalBars: [] as any[],
    targetKcal: 0,
    weightPoints: [] as any[],
    weightChange: null as number | null,
    summaries: [] as any[],
  },

  onLoad() { this.measure(); this.load(); this.loadSummaries(); },

  measure() {
    try {
      const wi: any = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
      const menu = wx.getMenuButtonBoundingClientRect();
      const navH = (menu.top - wi.statusBarHeight) * 2 + menu.height + wi.statusBarHeight;
      this.setData({ navH });
    } catch (e) {}
  },

  onRange(e: any) {
    const r = RANGES.find((x) => x.key === e.detail.key) || RANGES[0];
    this.setData({ rangeKey: r.key, days: r.days });
    this.load();
  },

  async load() {
    this.setData({ loading: true });
    try {
      const window = await records.trends(this.data.days);
      const rows = window || [];
      // 热量条形（缺失日不计零）
      const kcals = rows.map((d: any) => (d.totals && d.totals.kcal != null ? d.totals.kcal : null));
      const known = kcals.filter((v: number | null): v is number => v != null);
      const target = (rows.length && rows[rows.length - 1].targets && rows[rows.length - 1].targets.kcal) || 0;
      const max = Math.max(target, ...known, 1);
      const kcalBars = rows.map((d: any, i: number) => {
        const v = kcals[i];
        return {
          idx: i,
          miss: v == null,
          h: v == null ? 3 : Math.max(6, Math.round(v / max * 100)),
          lb: this.data.days > 28 ? (i % 5 === 0 ? fmtDayShort(d.date) : '') : fmtDayShort(d.date),
        };
      });
      // 体重
      const wp = rows.filter((d: any) => d.weight_kg != null).map((d: any) => ({
        date: d.date, dateLabel: fmtDayShort(d.date), value: Math.round(d.weight_kg * 10) / 10,
      }));
      let weightChange: number | null = null;
      if (wp.length >= 2) {
        weightChange = Math.round((wp[wp.length - 1].value - wp[0].value) * 10) / 10;
      }
      this.setData({ kcalBars, targetKcal: target ? Math.round(target) : 0, weightPoints: wp.slice().reverse(), weightChange, loading: false });
    } catch (e) {
      this.setData({ loading: false });
      showApiError(e);
    }
  },

  async loadSummaries() {
    try {
      const rows = await insights.summaries();
      const summaries = (rows || []).map((s: any) => {
        const d = s.data || {};
        return {
          month: s.month,
          monthLabel: s.month ? s.month.replace('-', ' 年 ') + ' 月' : '—',
          summary: d.summary || (d.record_count != null ? `${d.record_count} 条记录已归档` : '已归档'),
        };
      });
      this.setData({ summaries });
    } catch (e) {}
  },

  onSummary(e: any) {
    notify.info('归档摘要只读 · 不能承诺重新计算（' + e.currentTarget.dataset.m + '）');
  },
});
