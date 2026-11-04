// components/panel-discover/panel-discover.ts — 发现面板（洞察 + 复盘）· 虚拟页
import { insights } from '../../runtime/api';
import { notify, showApiError } from '../../runtime/feedback';
import { genRequestId } from '../../runtime/request';
import { fmtDay } from '../../runtime/format';

const TEMPLATE_TITLE: Record<string, string> = {
  sleep_intake: '睡眠与摄入相关', weekday_intake: '工作日/周末摄入差异',
  meal_regularity: '规律进餐', weight_trend: '体重趋势',
};

Component({
  options: { addGlobalClass: true },
  properties: { active: { type: Boolean, value: false } },
  observers: {
    active(v: boolean) {
      if (v) {
        // hidden→可见后 segmented 需重测槽宽（hidden 内 boundingClientRect 为 0 宽，
        // 负 slotW 会使透镜与手指方向相反）；nextTick + 100ms 双保险覆盖深链首帧
        const remeasure = () => {
          const seg: any = this.selectComponent('#seg');
          if (seg && seg.remeasure) seg.remeasure();
        };
        wx.nextTick(remeasure);
        setTimeout(remeasure, 100);
        this.loadInsights();
        this.loadReviews();
      }
    },
  },
  data: {
    tab: 'insights',
    tabs: [{ key: 'insights', text: '规律洞察' }, { key: 'reviews', text: '复盘报告' }],
    loadingI: true, loadingR: true, generating: false,
    insightsList: [] as any[], reviewsList: [] as any[],
  },
  methods: {
    onTab(e: any) { this.setData({ tab: e.detail.key }); },

    async loadInsights() {
      this.setData({ loadingI: true });
      try {
        const rows = await insights.list(true);
        this.setData({ insightsList: (rows || []).map((r: any) => this.insightVM(r)), loadingI: false });
      } catch (e) { this.setData({ loadingI: false }); showApiError(e); }
    },

    insightVM(r: any): any {
      const d = r.data || {};
      const mem = d.memory || {};
      const stale = mem.status === 'stale' || r.status !== 'active';
      const status = stale ? 'stale' : (r.status === 'rejected' ? 'rejected' : (d.feedback || r.feedback || 'pending'));
      const statusMap: Record<string, { label: string; cls: string }> = {
        pending: { label: '待反馈', cls: '' }, accepted: { label: '已认可', cls: 'is-on' },
        rejected: { label: '已否认', cls: '' }, stale: { label: '已过期', cls: '' },
      };
      const sm = statusMap[status] || statusMap.pending;
      const ev = [
        d.sample != null ? `样本 ${d.sample}` : null,
        d.window_days != null ? `窗口 ${d.window_days} 天` : (mem.window || null),
        d.coverage != null ? `覆盖率 ${Math.round(d.coverage * 100)}%` : null,
        d.method_version ? `方法 ${d.method_version}` : (mem.method || null),
        d.limitation || mem.limitation,
      ].filter(Boolean).join(' · ');
      return {
        id: r.id,
        title: d.title || TEMPLATE_TITLE[r.template] || r.template || '规律洞察',
        body: d.body || d.summary || d.text || '',
        evidence: ev || '（无附加说明）',
        status, statusLabel: sm.label, statusCls: sm.cls, stale,
      };
    },

    async onFb(e: any) {
      const { id, fb } = e.currentTarget.dataset;
      try {
        await insights.feedback(id, fb);
        notify.success(fb === 'accepted' ? '已认可该规律' : '已否认，将降低该模式权重');
        this.loadInsights();
      } catch (err) { showApiError(err); }
    },

    async loadReviews() {
      this.setData({ loadingR: true });
      try {
        const rows = await insights.reviews();
        this.setData({ reviewsList: (rows || []).map((r: any) => this.reviewVM(r)), loadingR: false });
      } catch (e) { this.setData({ loadingR: false }); }
    },

    reviewVM(r: any): any {
      const d = r.data || {};
      const metrics = (d.metrics || []).map((m: any) => ({
        label: m.label || m.name || '', value: m.value != null ? m.value : '—',
        unit: m.unit || '', cls: m.direction === 'up' ? 'qj-stat--up' : m.direction === 'down' ? 'qj-stat--down' : '',
      }));
      return {
        id: r.id,
        periodLabel: d.period_label || ((d.period === 'day' ? '日复盘' : '周复盘') + ' · ' + (d.window || fmtDay(String(r.created_at || '').slice(0, 10)))),
        metrics,
        body: d.summary || d.body || d.narrative || '',
        source: [d.source_version ? '来源 ' + d.source_version : '', d.method_version ? '方法 ' + d.method_version : '', r.created_at ? '生成于 ' + fmtDay(String(r.created_at).slice(0, 10)) : ''].filter(Boolean).join(' · '),
      };
    },

    async genReview() {
      this.setData({ generating: true });
      try {
        const res = await insights.generateReview('week', genRequestId('review'));
        if (res && res.status === 'waiting_input') notify.info('复盘生成中，稍后刷新查看');
        else notify.success('周复盘已生成');
        this.loadReviews();
      } catch (e: any) { showApiError(e); }
      finally { this.setData({ generating: false }); }
    },
  },
});
