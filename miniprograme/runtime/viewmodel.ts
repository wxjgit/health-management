// runtime/viewmodel.ts — 后端原始数据 → 视图模型归一化
// 后端 events 返回原始行 {id,kind,occurred_at,day,raw_text,data,version}；
// data 按 kind 不同：meal→{items:[{name,quantity,unit,size,kcal,protein_g,carbs_g,fat_g,nutrition_sources,nutrition_status}]}，
// weight/sleep/exercise→{value,...}，cycle→{cycle_action,...}。
// 本层统一转成页面直接可渲染的 VM，避免每页重复映射。
import { kindMeta, fmtDay, fmtDayShort, fmtTime } from './format';

export interface EventVM {
  id: string;
  kind: string;
  icon: string;
  iconColor: string;
  cls: string;
  label: string;
  day: string;
  dayLabel: string;
  time: string;
  title: string;
  detail: string;
  rev: number;
  source: string;
  kcal: number | null;
  protein: number | null;
  carbs: number | null;
  fat: number | null;
  value: number | null;
  unit: string;
  foods: Array<{ name: string; amount: string; kcal: number | null; source: string }>;
  feedback: Array<{ label: string; tag: string }>;
  nutritionStatus: string;
  raw: any;
}

const SOURCE_LABEL: Record<string, string> = {
  user: '用户提供',
  model_estimate: '模型粗估',
  demo_fixture: '演示示例',
  label: '包装标签',
};
function srcLabel(s: string): string {
  return SOURCE_LABEL[s] || (s ? '本地已知' : '未知');
}

function num(v: any): number | null {
  return v === null || v === undefined || v === '' ? null : Number(v);
}

export function eventVM(e: any): EventVM {
  const meta = kindMeta(e.kind);
  const data = e.data || {};
  const items = data.items || [];
  let title = e.raw_text || meta.label;
  let kcal: number | null = null;
  let protein: number | null = null;
  let carbs: number | null = null;
  let fat: number | null = null;
  const foods: EventVM['foods'] = [];

  if (e.kind === 'meal') {
    kcal = sumOrNull(items.map((i: any) => i.kcal));
    protein = sumOrNull(items.map((i: any) => i.protein_g));
    carbs = sumOrNull(items.map((i: any) => i.carbs_g));
    fat = sumOrNull(items.map((i: any) => i.fat_g));
    items.forEach((i: any) => {
      const amount = i.quantity != null ? `${i.quantity}${i.unit || ''}${i.size ? '·' + i.size : ''}` : (i.size || '分量未知');
      foods.push({
        name: i.name || '未知食物',
        amount,
        kcal: num(i.kcal),
        source: srcLabel((i.nutrition_sources && i.nutrition_sources.kcal) || ''),
      });
    });
    title = (data.meal_slot ? data.meal_slot + ' · ' : '') + (items.map((i: any) => i.name).filter(Boolean).join('、') || '一餐');
  } else if (e.kind === 'weight') {
    title = `体重 ${data.value ?? '?'} kg`;
  } else if (e.kind === 'sleep') {
    const hrs = data.value != null ? (data.value / 60).toFixed(1) : '?';
    title = `睡眠 ${hrs} 小时`;
  } else if (e.kind === 'exercise') {
    title = `${data.exercise_type || '运动'} ${data.value ?? '?'} 分钟`;
  } else if (e.kind === 'cycle') {
    const act: Record<string, string> = { start: '开始', end: '结束', note: '备注' };
    title = `生理周期 · ${act[data.cycle_action] || '记录'}`;
  }

  const fb = (data.feedback || []).map((f: any) => ({
    label: FEEDBACK_LABEL[f.type] || f.type,
    tag: f.stance === 'denied' ? '已否认' : f.stance === 'assumed' ? '推测' : '自述',
  }));

  return {
    id: e.id,
    kind: e.kind,
    icon: meta.icon,
    iconColor: meta.color,
    cls: meta.cls,
    label: meta.label,
    day: e.day,
    dayLabel: fmtDay(e.day),
    time: fmtTime(e.occurred_at || ''),
    title,
    detail: e.raw_text || '',
    rev: e.version || 1,
    source: sourceLabel(e),
    kcal, protein, carbs, fat,
    value: num(data.value),
    unit: data.unit || '',
    foods,
    feedback: fb,
    nutritionStatus: data.nutrition_status || (items.length ? (items.every((i: any) => i.kcal != null) ? 'complete' : 'partial') : ''),
    raw: e,
  };
}

const FEEDBACK_LABEL: Record<string, string> = {
  salty: '偏咸', oily: '油腻', overeating: '吃撑', hungry: '仍饿',
};

function sumOrNull(arr: Array<number | null>): number | null {
  if (!arr.length) return null;
  if (arr.every((x) => x === null)) return null;
  let s = 0, has = false;
  arr.forEach((x) => { if (x !== null) { s += x; has = true; } });
  return has ? Math.round(s * 10) / 10 : null;
}

function sourceLabel(e: any): string {
  // 后端未直接给 source 文案，用版本与来源推断
  if ((e.version || 1) > 1) return '已修订';
  return '对话记录';
}

// 按天分组（历史页）
export function groupByDay(events: EventVM[]): Array<{ day: string; dayLabel: string; kcal: number; items: EventVM[] }> {
  const map: Record<string, EventVM[]> = {};
  events.forEach((e) => { (map[e.day] = map[e.day] || []).push(e); });
  return Object.keys(map).sort().reverse().map((day) => {
    const items = map[day];
    const kcal = items.filter((i) => i.kind === 'meal' && i.kcal != null).reduce((s, i) => s + (i.kcal || 0), 0);
    return { day, dayLabel: fmtDay(day), kcal: Math.round(kcal), items };
  });
}

export { fmtDayShort };
