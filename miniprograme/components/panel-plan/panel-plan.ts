// components/panel-plan/panel-plan.ts — 计划面板 · 虚拟页
// 契约：POST /api/plans/propose → plan workflow；超 5% → waiting_input(plan_confirmation)；
//   确认/拒绝 → POST /api/workflows/{thread}/resume {interrupt_id, approved}。
import { plans, chat } from '../../runtime/api';
import { notify, confirm, showApiError } from '../../runtime/feedback';
import { genRequestId } from '../../runtime/request';
import { fmtDay } from '../../runtime/format';

const WD = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

Component({
  options: { addGlobalClass: true },
  properties: { active: { type: Boolean, value: false } },
  observers: {
    active(v: boolean) { if (v) this.load(); },
  },
  data: {
    loading: true, proposing: false, confirming: false,
    planVM: null as any, draft: null as any,
    basisOpen: false,
    basisText: '静息能量使用 Mifflin–St Jeor：10×体重kg + 6.25×身高cm − 5×年龄 + 系数（男+5/女−161）。活动系数久坐/轻度/中等/较多为 1.4/1.6/1.8/2.0（参考 NIDDK）。目标体重更低时初始缺口为维持估算的 10%、最多 300 kcal/天；宏量蛋白20%/碳水50%/脂肪30%（4/4/9 kcal/g）。参考：WHO 每周 150 分钟中等强度活动。',
    editShow: false,
    editForm: { kcal: '', exercise_minutes: '150', note: '' },
    editErr: {} as Record<string, boolean>,
  },
  methods: {
    async load() {
      this.setData({ loading: true });
      try {
        const list = await plans.list();
        const rows = list || [];
        const activeRow = rows.find((p: any) => p.status === 'active');
        this.setData({ planVM: activeRow ? this.toVM(activeRow) : null, loading: false });
        if (!this.data.draft) this.restorePendingDraft();
      } catch (e) {
        this.setData({ loading: false });
        showApiError(e);
      }
    },

    async restorePendingDraft() {
      try {
        const list = await chat.waiting(false);
        const w = (list || []).find((x: any) => x.kind === 'plan' && x.status === 'waiting_input');
        if (w && w.result && w.result.pending_input && w.result.pending_input.kind === 'plan_confirmation') {
          this.setDraftFromPending(w.result.thread_id || w.thread_id, w.result.pending_input);
        }
      } catch (e) {}
    },

    setDraftFromPending(threadId: string, p: any) {
      const t = p.targets || {};
      const changes = (p.changes || []).map((c: any) => typeof c === 'string' ? c : (c.text || c.detail || '')).filter(Boolean);
      this.setData({
        draft: {
          threadId, interruptId: p.interrupt_id,
          summary: `新目标：${Math.round(t.kcal || 0)} kcal/天 · 蛋白 ${Math.round(t.protein_g || 0)}g / 碳水 ${Math.round(t.carbs_g || 0)}g / 脂肪 ${Math.round(t.fat_g || 0)}g`,
          changes, warnings: p.target_warnings || [],
        },
      });
    },

    toVM(row: any): any {
      const d = row.data || {};
      const t = d.targets || {};
      const schedule = (d.schedule || []).map((s: any, i: number) => ({
        day: s.day || WD[i % 7], text: s.text || s.focus || s.note || '',
      })).filter((s: any) => s.text);
      return {
        targets: {
          kcal: Math.round(t.kcal || 0), protein_g: Math.round(t.protein_g || 0),
          carbs_g: Math.round(t.carbs_g || 0), fat_g: Math.round(t.fat_g || 0),
          exercise_minutes: t.exercise_minutes || 0,
        },
        sourceLabel: d.target_source === 'manual' ? '手动目标' : '系统默认',
        confirmedLabel: row.confirmed_at ? fmtDay(String(row.confirmed_at).slice(0, 10)) : fmtDay(String(row.created_at || '').slice(0, 10)),
        formula: d.formula_version || 'Mifflin–St Jeor',
        schedule,
      };
    },

    toggleBasis() { this.setData({ basisOpen: !this.data.basisOpen }); },
    goProfile() { wx.navigateTo({ url: '/pages/profile/profile' }); },

    async handleProposeResult(res: any) {
      if (res && res.status === 'waiting_input' && res.pending_input) {
        this.setDraftFromPending(res.thread_id, res.pending_input);
        notify.info('草案已生成，等待你确认');
      } else {
        this.setData({ draft: null });
        notify.success(res && res.reply ? res.reply : '目标已生效');
      }
      this.load();
    },

    async useDefault() {
      this.setData({ proposing: true });
      try {
        const def = await plans.defaultTargets();
        const t = def.targets || def;
        if (!t || t.kcal == null) {
          notify.warn(def.reason || '当前档案无法自动生成通用目标，请先完善档案');
          this.setData({ proposing: false });
          return;
        }
        const res = await plans.propose({
          targets: { kcal: t.kcal, protein_g: t.protein_g, carbs_g: t.carbs_g, fat_g: t.fat_g, exercise_minutes: t.exercise_minutes || 150 },
          target_source: 'system_default',
          suggestion_fingerprint: def.fingerprint || def.suggestion_fingerprint || null,
          requestId: genRequestId('plan'),
        });
        await this.handleProposeResult(res);
      } catch (e: any) { showApiError(e); }
      finally { this.setData({ proposing: false }); }
    },

    editTarget() {
      const a = this.data.planVM;
      this.setData({
        editShow: true,
        editForm: { kcal: a ? String(a.targets.kcal) : '', exercise_minutes: a ? String(a.targets.exercise_minutes) : '150', note: '' },
        editErr: {},
      });
    },
    closeEdit() { this.setData({ editShow: false }); },
    onEditField(e: any) {
      const k = e.currentTarget.dataset.k;
      this.setData({ [`editForm.${k}`]: e.detail.value, [`editErr.${k}`]: false });
    },
    onEditNote(e: any) { this.setData({ 'editForm.note': e.detail.value || '' }); },

    async submitTarget() {
      const f = this.data.editForm;
      const kcal = parseFloat(f.kcal);
      const ex = parseFloat(f.exercise_minutes || '0');
      const err: Record<string, boolean> = {};
      if (!(kcal >= 1500 && kcal <= 3500)) err.kcal = true;
      if (!(ex >= 0 && ex <= 300)) err.exercise_minutes = true;
      this.setData({ editErr: err });
      if (Object.keys(err).length) { notify.warn('请修正标红字段'); return; }
      const protein_g = Math.round(kcal * 0.20 / 4);
      const carbs_g = Math.round(kcal * 0.50 / 4);
      const fat_g = Math.round(kcal * 0.30 / 9);
      this.setData({ proposing: true });
      try {
        const res = await plans.propose({
          targets: { kcal, protein_g, carbs_g, fat_g, exercise_minutes: ex },
          target_source: 'manual', note: f.note || undefined, requestId: genRequestId('plan'),
        });
        this.setData({ editShow: false });
        await this.handleProposeResult(res);
      } catch (e: any) { showApiError(e); }
      finally { this.setData({ proposing: false }); }
    },

    async confirmDraft() {
      const d = this.data.draft;
      if (!d) return;
      this.setData({ confirming: true });
      try {
        await chat.resume(d.threadId, { interrupt_id: d.interruptId, approved: true, requestId: genRequestId('plan_ok') });
        this.setData({ draft: null });
        notify.success('目标已生效');
        this.load();
      } catch (e: any) { showApiError(e); }
      finally { this.setData({ confirming: false }); }
    },

    async cancelDraft() {
      const d = this.data.draft;
      if (!d) return;
      const ok = await confirm({ title: '拒绝这份草案？', content: '草案将被拒绝，当前生效目标保持不变。', confirmText: '拒绝草案', destructive: true });
      if (!ok) return;
      this.setData({ confirming: true });
      try {
        await chat.resume(d.threadId, { interrupt_id: d.interruptId, approved: false, requestId: genRequestId('plan_no') });
        this.setData({ draft: null });
        notify.info('已拒绝草案，原目标保持不变');
        this.load();
      } catch (e: any) { showApiError(e); }
      finally { this.setData({ confirming: false }); }
    },
  },
});
