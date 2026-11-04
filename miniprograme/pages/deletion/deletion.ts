// pages/deletion/deletion.ts — 隐私删除 · Task（三步：选择→预览→完成）
// 契约：POST /api/deletions/preview {target_type,target_id} → {preview_id,target_type,target_id,counts,scope,reasons}
//   POST /api/deletions {preview_id,request_id} → 作业；GET /api/deletions/{id}；POST /api/deletions/{id}/retry
import { deletions } from '../../runtime/api';
import { notify, confirm, showApiError } from '../../runtime/feedback';
import { genRequestId } from '../../runtime/request';
import { homeTabUrl } from '../../runtime/routes';

// counts 表名 → 展示分组（删除 / 清除共享原文 / 保留独立目标）
const DEL_TABLES: Record<string, string> = {
  events: '健康记录（含全部修订）', facts: '长期信息', revisions: '修订版本',
  insights: '规律洞察', reviews: '复盘报告', plans: '计划草案', notifications: '站内通知',
  workflows: '工作流进度', checkpoints: '流程检查点', operations: '操作回执',
};
const CLEAR_TABLES: Record<string, string> = {
  provenance: '数据血缘副本', fragments: '共享原文片段', messages: '关联聊天原文',
};
const KEEP_TABLES: Record<string, string> = {
  kept_events: '可独立验证的记录', kept_facts: '独立长期信息', kept_plans: '独立生效目标',
};
const SCOPE_LABEL: Record<string, string> = {
  dependencies: '依赖范围', shared_source: '含共享原文', legacy_context: '扩大预览（历史缺血缘）',
};

Page({
  data: {
    navH: 88,
    step: 1,
    targetType: 'event' as 'event' | 'fact',
    targetId: '',
    targetTitle: '',
    previewing: false,
    deleting: false,
    retrying: false,
    previewId: '',
    deletionId: '',
    jobState: 'completed',
    preview: { scopeLabel: '', del: [], clear: [], keep: [] } as any,
  },

  onLoad(q: any) {
    this.measure();
    if (q && q.type && q.id) {
      this.setData({
        targetType: q.type === 'fact' ? 'fact' : 'event',
        targetId: q.id,
        targetTitle: q.title ? decodeURIComponent(q.title) : '（已选中对象）',
      });
    }
  },

  measure() {
    try {
      const wi: any = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
      const menu = wx.getMenuButtonBoundingClientRect();
      const navH = (menu.top - wi.statusBarHeight) * 2 + menu.height + wi.statusBarHeight;
      this.setData({ navH });
    } catch (e) {}
  },

  async doPreview() {
    if (!this.data.targetId) { notify.warn('请先选择删除对象'); return; }
    this.setData({ previewing: true });
    try {
      const p = await deletions.preview(this.data.targetType, this.data.targetId);
      const counts = p.counts || {};
      this.setData({
        previewId: p.preview_id || '',
        preview: {
          scopeLabel: SCOPE_LABEL[p.scope] || p.scope || '依赖范围',
          del: this.pick(counts, DEL_TABLES),
          clear: this.pick(counts, CLEAR_TABLES),
          keep: this.pick(counts, KEEP_TABLES),
        },
        step: 2,
      });
    } catch (e) {
      showApiError(e);
    } finally {
      this.setData({ previewing: false });
    }
  },

  pick(counts: Record<string, number>, map: Record<string, string>): Array<{ label: string; count: number }> {
    const rows: Array<{ label: string; count: number }> = [];
    Object.keys(map).forEach((k) => {
      if (counts[k] != null) rows.push({ label: map[k], count: counts[k] });
    });
    // 未映射但有值的表，归入对应组的"其他"（默认删除组，保守展示）
    return rows;
  },

  backToStep1() { this.setData({ step: 1, previewId: '' }); },

  async doDelete() {
    const ok = await confirm({
      title: '最终确认',
      content: '此操作不可逆。删除后将清理 SQLite 页与 WAL，旧请求不能重新生成已删除内容，正在运行的相关模型结果也不会写回。',
      confirmText: '确认删除', destructive: true,
    });
    if (!ok) return;
    this.setData({ deleting: true });
    try {
      const res = await deletions.execute(this.data.previewId, genRequestId('del'));
      const state = res.state || res.status || 'completed';
      this.setData({ deletionId: res.deletion_id || res.id || '', jobState: state, step: 3 });
      if (state === 'completed') notify.success('删除完成');
      else notify.info('删除作业进行中，可稍后继续清理');
    } catch (e: any) {
      showApiError(e);
    } finally {
      this.setData({ deleting: false });
    }
  },

  async retryJob() {
    if (!this.data.deletionId) return;
    this.setData({ retrying: true });
    try {
      const res = await deletions.retry(this.data.deletionId);
      const state = res.state || res.status || 'completed';
      this.setData({ jobState: state });
      notify.success(state === 'completed' ? '删除已完成' : '已继续清理');
    } catch (e) {
      showApiError(e);
    } finally {
      this.setData({ retrying: false });
    }
  },

  goBack() {
    // 删除后返回上级；若来自详情/长期信息页，回退两层避免看到已删对象
    const pages = getCurrentPages();
    if (pages.length > 1) wx.navigateBack({ delta: 1 });
    else wx.reLaunch({ url: homeTabUrl(1) });
  },
});
