// components/panel-history/panel-history.ts — 历史面板 · 虚拟页
import { records } from '../../runtime/api';
import { eventVM, groupByDay } from '../../runtime/viewmodel';
import { showApiError } from '../../runtime/feedback';
import { todayStr } from '../../runtime/format';

const RANGES = [7, 28, 90];

Component({
  options: { addGlobalClass: true },
  properties: { active: { type: Boolean, value: false } },
  observers: {
    active(v: boolean) { if (v) this.load(); },
  },
  data: {
    loading: true, search: '', kind: '', range: 7, rangeIdx: 0,
    list: [] as any[], groups: [] as any[],
    kinds: [
      { k: '', icon: 'list', label: '全部' },
      { k: 'meal', icon: 'bowl', label: '饮食' },
      { k: 'weight', icon: 'scale', label: '体重' },
      { k: 'sleep', icon: 'moon', label: '睡眠' },
      { k: 'exercise', icon: 'activity', label: '运动' },
      { k: 'cycle', icon: 'flower', label: '周期' },
    ],
  },
  methods: {
    async load() {
      this.setData({ loading: true });
      const today = new Date(todayStr().replace(/-/g, '/'));
      const start = new Date(today.getTime());
      start.setDate(start.getDate() - (this.data.range - 1));
      const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
      try {
        const rows = await records.events({
          start: iso(start), end: todayStr(),
          kind: (this.data.kind || undefined) as any,
          search: this.data.search || undefined,
        });
        const list = (rows || []).map(eventVM);
        this.setData({ list, groups: groupByDay(list), loading: false });
      } catch (e) {
        this.setData({ loading: false });
        showApiError(e);
      }
    },

    onSearch(e: any) {
      this.setData({ search: (e.detail.value || '').trim() });
      if ((this as any)._st) clearTimeout((this as any)._st);
      (this as any)._st = setTimeout(() => this.load(), 400);
    },
    pickKind(e: any) { this.setData({ kind: e.currentTarget.dataset.k }); this.load(); },
    cycleRange() {
      const rangeIdx = (this.data.rangeIdx + 1) % RANGES.length;
      this.setData({ rangeIdx, range: RANGES[rangeIdx] });
      this.load();
    },
    openEvent(e: any) {
      wx.navigateTo({ url: '/pages/event/event?id=' + e.currentTarget.dataset.id });
    },
  },
});
