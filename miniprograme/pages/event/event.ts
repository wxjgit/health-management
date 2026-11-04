// pages/event/event.ts — 记录详情 · Task
import { records, chat } from '../../runtime/api';
import { eventVM } from '../../runtime/viewmodel';
import { notify, confirm, showApiError } from '../../runtime/feedback';
import { genRequestId } from '../../runtime/request';
import { homeTabUrl } from '../../runtime/routes';

Page({
  data: {
    navH: 88,
    id: '',
    vm: null as any,
    revisions: [] as any[],
    revLoading: false,
    corrShow: false,
    corrText: '',
    corrLoading: false,
  },

  onLoad(q: any) {
    this.measure();
    this.setData({ id: q.id || '' });
    if (q.id) this.load(q.id);
  },

  measure() {
    try {
      const wi: any = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
      const menu = wx.getMenuButtonBoundingClientRect();
      const navH = (menu.top - wi.statusBarHeight) * 2 + menu.height + wi.statusBarHeight;
      this.setData({ navH });
    } catch (e) {}
  },

  async load(id: string) {
    // events 列表按 id 取单条（后端无 GET /events/{id}，用列表过滤）
    try {
      const rows = await records.events({ limit: 1000 });
      const row = (rows || []).find((e: any) => e.id === id);
      if (!row) { notify.error('记录不存在或已归档'); return; }
      this.setData({ vm: eventVM(row) });
      this.loadRevisions(id);
    } catch (e) { showApiError(e); }
  },

  async loadRevisions(id: string) {
    this.setData({ revLoading: true });
    try {
      const revs = await records.revisions(id);
      const curVer = this.data.vm?.rev || 1;
      // 真实契约：[{version, created_at, data, changes:[{field,before,after}], source_request_id}]
      const list = (revs || []).map((r: any) => ({
        version: r.version,
        title: r.version === curVer ? '当前版本' : '历史修订',
        time: (r.created_at || '').replace('T', ' ').slice(0, 16),
        changes: (r.changes || []).slice(0, 6).map((c: any) => ({
          field: this.fieldLabel(c.field),
          from: this.fmtVal(c.before),
          to: this.fmtVal(c.after),
        })),
      })).sort((a: any, b: any) => b.version - a.version);
      this.setData({ revisions: list, revLoading: false });
    } catch (e) {
      this.setData({ revLoading: false });
      // 修订接口失败不阻断详情展示
    }
  },

  // 修订 diff 字段名 → 中文标签
  fieldLabel(field: string): string {
    if (!field) return '';
    const tail = field.split('.').pop() || field;
    const map: Record<string, string> = {
      kind: '类型', occurred_at: '发生时间', value: '数值', quality: '睡眠质量',
      exercise_type: '运动类型', intensity: '强度', meal_slot: '餐次', cycle_action: '周期',
      name: '食物', quantity: '数量', unit: '单位', size: '分量', cooking: '做法',
      kcal: '热量', protein_g: '蛋白质', carbs_g: '碳水', fat_g: '脂肪',
    };
    return map[tail] || tail;
  },
  fmtVal(v: any): string {
    if (v == null) return '空';
    if (typeof v === 'object') return JSON.stringify(v).slice(0, 30);
    return String(v);
  },

  onCorrect() { this.setData({ corrShow: true, corrText: '' }); },
  closeCorr() { this.setData({ corrShow: false }); },
  onCorrInput(e: any) { this.setData({ corrText: e.detail.value || '' }); },

  async submitCorrect() {
    const text = this.data.corrText.trim();
    if (!text) { notify.warn('请输入纠正内容'); return; }
    this.setData({ corrLoading: true });
    try {
      const res = await chat.send(text, { eventId: this.data.id, requestId: genRequestId('corr') });
      this.setData({ corrLoading: false, corrShow: false });
      if (res.status === 'waiting_input') {
        notify.info('需要补充信息，请到「记录」页继续');
        setTimeout(() => wx.reLaunch({ url: homeTabUrl(0) }), 800);
      } else {
        notify.success('纠正已提交');
        this.load(this.data.id);
      }
    } catch (e) {
      this.setData({ corrLoading: false });
      showApiError(e);
    }
  },

  async onDelete() {
    const ok = await confirm({
      title: '删除这条记录？',
      content: '将进入隐私删除流程：先预览实际影响范围（关联聊天、复盘、规律），确认后再执行。不会静默删除。',
      confirmText: '进入删除流程', destructive: true,
    });
    if (!ok) return;
    wx.navigateTo({ url: '/pages/deletion/deletion?type=event&id=' + this.data.id });
  },
});
