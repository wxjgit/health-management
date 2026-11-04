// pages/facts/facts.ts — 长期信息 · Task
// 契约：GET /api/facts?include_inactive → [{id,type,subject,value,content,status,version,source_kind,...}]
//   PATCH /api/facts/{id} {action:confirm|retract, expected_version, resolution}
//   POST /api/facts (FactInput)；GET /api/facts/{id}/revisions；DELETE /api/facts/{id}
import { profile } from '../../runtime/api';
import { notify, confirm, showApiError } from '../../runtime/feedback';

const TYPE_ICON: Record<string, string> = {
  food_preference: 'heart', allergy: 'warning', restriction: 'ban',
  health_condition: 'medical', professional_advice: 'list', legacy_text: 'pin',
};
const TYPE_LABEL: Record<string, string> = {
  food_preference: '饮食偏好', allergy: '过敏', restriction: '限制',
  health_condition: '健康状况', professional_advice: '专业建议', legacy_text: '文本',
};
const SOURCE_LABEL: Record<string, string> = {
  user_explicit: '用户直接陈述', user_reported_professional: '转述专业建议（未独立核验）',
  legacy: '历史文本', unconfirmed_extraction: '待确认提取',
};

Page({
  data: {
    navH: 88,
    loading: true,
    seg: 'active',
    segTabs: [{ key: 'active', text: '生效中' }, { key: 'conflict', text: '冲突' }, { key: 'inactive', text: '已失效' }],
    all: [] as any[],
    list: [] as any[],
    emptyText: '暂无长期信息',
    // 新增
    addShow: false,
    addText: '',
    addErr: false,
    adding: false,
    // 修订
    revShow: false,
    revLoading: false,
    revList: [] as any[],
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
    this.setData({ loading: true });
    try {
      const rows = await profile.facts(true);
      const all = (rows || []).map((f: any) => this.factVM(f));
      this.setData({ all, loading: false });
      this.applyFilter();
    } catch (e) {
      this.setData({ loading: false });
      showApiError(e);
    }
  },

  factVM(f: any): any {
    const conflict = f.confirmation === 'conflict' || f.status === 'conflict';
    const inactive = f.lifecycle === 'superseded' || f.status === 'superseded' || f.lifecycle === 'needs_review';
    return {
      id: f.id,
      type: f.type || 'legacy_text',
      typeIcon: TYPE_ICON[f.type] || 'pin',
      typeLabel: TYPE_LABEL[f.type] || '文本',
      title: f.subject ? `${f.subject} · ${f.value || ''}`.trim() : (f.content || '').slice(0, 16),
      content: f.content || f.description || '',
      version: f.version || 1,
      conflict,
      inactive,
      statusLabel: conflict ? '存在冲突' : (f.time_scope === 'bounded' ? '限期' : inactive ? '已失效/待核对' : '生效中'),
      sourceLabel: SOURCE_LABEL[f.source_kind] || '用户陈述',
    };
  },

  applyFilter() {
    const seg = this.data.seg;
    let list = this.data.all;
    if (seg === 'conflict') list = list.filter((f: any) => f.conflict);
    else if (seg === 'inactive') list = list.filter((f: any) => f.inactive && !f.conflict);
    else list = list.filter((f: any) => !f.conflict && !f.inactive);
    const emptyMap: Record<string, string> = {
      active: '暂无生效中的长期信息', conflict: '没有待处理冲突', inactive: '暂无失效信息',
    };
    this.setData({ list, emptyText: emptyMap[seg] });
  },

  onSeg(e: any) { this.setData({ seg: e.detail.key }); this.applyFilter(); },

  async decide(e: any) {
    const { id, ver, action } = e.currentTarget.dataset;
    const isConfirm = action === 'confirm';
    const ok = await confirm({
      title: isConfirm ? '以新说法为准？' : '保留原有信息？',
      content: isConfirm
        ? '将采用本次输入替代旧偏好，原内容保留为替代历史（不会静默丢弃）。'
        : '将撤回本次冲突说法，保留原有长期信息不变。',
      confirmText: isConfirm ? '以新说法为准' : '保留原有',
      destructive: !isConfirm,
    });
    if (!ok) return;
    try {
      await profile.decideFact(id, { action, expected_version: ver, resolution: 'select' });
      notify.success(isConfirm ? '已以新说法为准' : '已保留原有信息');
      this.load();
    } catch (err) { showApiError(err); }
  },

  openAdd() { this.setData({ addShow: true, addText: '', addErr: false }); },
  closeAdd() { this.setData({ addShow: false }); },
  onAddInput(e: any) { this.setData({ addText: e.detail.value || '', addErr: false }); },

  async submitAdd() {
    const content = this.data.addText.trim();
    if (!content) { this.setData({ addErr: true }); return; }
    this.setData({ adding: true });
    try {
      // 手动新增用 legacy_text（最宽松契约，无需 subject/value 结构化字段）
      await profile.createFact({
        type: 'legacy_text', content, source_kind: 'user_explicit',
        subject: '', subject_kind: 'other', value: '',
        time_scope: 'persistent', assertion: 'affirmed', restriction_reason: 'unknown',
      });
      this.setData({ addShow: false });
      notify.success('已保存');
      this.load();
    } catch (e: any) {
      showApiError(e);
    } finally {
      this.setData({ adding: false });
    }
  },

  async showRevisions(e: any) {
    const id = e.currentTarget.dataset.id;
    this.setData({ revShow: true, revLoading: true, revList: [] });
    try {
      const rows = await profile.factRevisions(id);
      const revList = (rows || []).map((r: any) => ({
        version: r.version,
        title: r.version === (this.data.all.find((f: any) => f.id === id)?.version || 1) ? '当前版本' : '历史修订',
        content: r.content || '',
        time: (r.created_at || r.updated_at || '').slice(0, 10),
      })).sort((a: any, b: any) => b.version - a.version);
      this.setData({ revList, revLoading: false });
    } catch (err) {
      this.setData({ revLoading: false });
      showApiError(err);
    }
  },
  closeRev() { this.setData({ revShow: false }); },

  async onDelete(e: any) {
    const { id, title } = e.currentTarget.dataset;
    const ok = await confirm({
      title: '删除这条长期信息？',
      content: `「${title}」将进入隐私删除流程，先预览影响范围（关联建议、计划视图）再执行。`,
      confirmText: '进入删除流程', destructive: true,
    });
    if (!ok) return;
    wx.navigateTo({ url: '/pages/deletion/deletion?type=fact&id=' + id });
  },
});
