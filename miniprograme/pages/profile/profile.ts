// pages/profile/profile.ts — 个人档案编辑 · Task
// 契约 Profile：age, sex(female/male/other), height_cm, weight_kg, goal_weight_kg,
//   goal_date, activity(sedentary/light/moderate/active), nutrition_context
import { profile, plans } from '../../runtime/api';
import { notify, showApiError } from '../../runtime/feedback';
import { todayStr } from '../../runtime/format';

interface Form {
  age: string; sex: string; height_cm: string; weight_kg: string;
  goal_weight_kg: string; goal_date: string; activity: string; nutrition_context: string;
}

const DEFAULT_FORM: Form = {
  age: '', sex: 'female', height_cm: '', weight_kg: '',
  goal_weight_kg: '', goal_date: todayStr(), activity: 'light', nutrition_context: 'unspecified',
};

Page({
  data: {
    navH: 88,
    saving: false,
    form: { ...DEFAULT_FORM } as Form,
    err: {} as Record<string, boolean>,
    defaultTargets: null as any,
    sexOptions: [
      { key: 'female', text: '女' }, { key: 'male', text: '男' }, { key: 'other', text: '不明确' },
    ],
    activityOptions: [
      { key: 'sedentary', text: '久坐' }, { key: 'light', text: '轻度' },
      { key: 'moderate', text: '中等' }, { key: 'active', text: '较多' },
    ],
    nutritionOptions: [
      { key: 'unspecified', text: '未说明' }, { key: 'general', text: '一般成人' },
      { key: 'pregnant', text: '孕期' }, { key: 'breastfeeding', text: '哺乳' }, { key: 'clinical', text: '临床' },
    ],
  },

  onLoad() { this.measure(); this.load(); },

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
      const p = await profile.get();
      if (p && (p.age || p.height_cm)) {
        this.setData({
          form: {
            age: p.age != null ? String(p.age) : '',
            sex: p.sex || 'female',
            height_cm: p.height_cm != null ? String(p.height_cm) : '',
            weight_kg: p.weight_kg != null ? String(p.weight_kg) : '',
            goal_weight_kg: p.goal_weight_kg != null ? String(p.goal_weight_kg) : '',
            goal_date: p.goal_date || todayStr(),
            activity: p.activity || 'light',
            nutrition_context: p.nutrition_context || 'unspecified',
          },
        });
      }
      this.loadDefaults();
    } catch (e) { /* 首次建档无档案属正常 */ }
  },

  async loadDefaults() {
    try {
      const def = await plans.defaultTargets();
      if (def && (def.targets || def.kcal != null)) {
        const t = def.targets || def;
        this.setData({
          defaultTargets: {
            kcal: Math.round(t.kcal), protein_g: Math.round(t.protein_g),
            carbs_g: Math.round(t.carbs_g), fat_g: Math.round(t.fat_g),
            reason: def.reason || def.note || def.basis || '依据当前档案本地估算，非医疗处方。',
          },
        });
      }
    } catch (e) { /* 未建档或命中安全限制时无默认目标 */ }
  },

  onField(e: any) {
    const k = e.currentTarget.dataset.k;
    this.setData({ [`form.${k}`]: e.detail.value, [`err.${k}`]: false });
  },
  onSex(e: any) { this.setData({ 'form.sex': e.detail.key }); },
  onActivity(e: any) { this.setData({ 'form.activity': e.detail.key }); },
  onNutrition(e: any) { this.setData({ 'form.nutrition_context': e.detail.key }); },
  onDate(e: any) { this.setData({ 'form.goal_date': e.detail.value, 'err.goal_date': false }); },

  validate(): boolean {
    const f = this.data.form;
    const err: Record<string, boolean> = {};
    const age = parseFloat(f.age);
    if (!(age >= 1 && age <= 120)) err.age = true;
    const h = parseFloat(f.height_cm);
    if (!(h >= 80 && h <= 250)) err.height_cm = true;
    const w = parseFloat(f.weight_kg);
    if (!(w >= 20 && w <= 400)) err.weight_kg = true;
    const gw = parseFloat(f.goal_weight_kg);
    if (!(gw >= 20 && gw <= 400)) err.goal_weight_kg = true;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(f.goal_date)) err.goal_date = true;
    this.setData({ err });
    return Object.keys(err).length === 0;
  },

  async onSave() {
    if (!this.validate()) { notify.warn('请修正标红的字段'); return; }
    const f = this.data.form;
    this.setData({ saving: true });
    try {
      await profile.update({
        age: parseInt(f.age, 10),
        sex: f.sex,
        height_cm: parseFloat(f.height_cm),
        weight_kg: parseFloat(f.weight_kg),
        goal_weight_kg: parseFloat(f.goal_weight_kg),
        goal_date: f.goal_date,
        activity: f.activity,
        nutrition_context: f.nutrition_context,
      });
      notify.success('档案已保存');
      setTimeout(() => wx.navigateBack(), 700);
    } catch (e: any) {
      showApiError(e);
    } finally {
      this.setData({ saving: false });
    }
  },

  onCancel() { wx.navigateBack(); },
});
