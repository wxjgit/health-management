// runtime/format.ts — 日期/数字格式化（全局唯一，禁止页面各自实现）

const WD = ['日', '一', '二', '三', '四', '五', '六'];

// '2026-11-04' → '11月4日 周三'
export function fmtDay(day: string): string {
  if (!day) return '';
  const [y, m, d] = day.split('-').map(Number);
  if (!y || !m || !d) return day;
  const wd = WD[new Date(y, m - 1, d).getDay()];
  return `${m}月${d}日 周${wd}`;
}

// '2026-11-04' → '11-04'
export function fmtDayShort(day: string): string {
  if (!day) return '';
  const p = day.split('-');
  return p.length >= 3 ? `${p[1]}-${p[2]}` : day;
}

export function isToday(day: string): boolean {
  return day === todayStr();
}

export function todayStr(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// 数字：千分位 + 保留位数
export function fmtNum(n: number | null | undefined, digits = 0): string {
  if (n === null || n === undefined || isNaN(n as number)) return '—';
  return Number(n).toFixed(digits).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

// '12:10:00' 或 ISO → '12:10'
export function fmtTime(t: string): string {
  if (!t) return '';
  const m = t.match(/(\d{2}):(\d{2})/);
  return m ? `${m[1]}:${m[2]}` : t;
}

// 相对时间（消息/修订）
export function fmtRelative(iso: string): string {
  if (!iso) return '';
  const t = new Date(iso.replace(/-/g, '/')).getTime();
  if (isNaN(t)) return iso;
  const diff = Date.now() - t;
  const min = Math.floor(diff / 60000);
  if (min < 1) return '刚刚';
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day} 天前`;
  return fmtDay(iso.slice(0, 10));
}

// 五类记录 → pyui-icon 图标名 + 语义色 + 类名（与 app.wxss 的 --qj-* 对应；禁 emoji）
export const KIND_META: Record<string, { icon: string; cls: string; label: string; color: string }> = {
  meal: { icon: 'bowl', cls: 'qj-ic--meal', label: '饮食', color: '#e8a33d' },
  weight: { icon: 'scale', cls: 'qj-ic--weight', label: '体重', color: '#4b9fe1' },
  sleep: { icon: 'moon', cls: 'qj-ic--sleep', label: '睡眠', color: '#9370db' },
  exercise: { icon: 'activity', cls: 'qj-ic--exercise', label: '运动', color: '#09be4f' },
  cycle: { icon: 'flower', cls: 'qj-ic--cycle', label: '周期', color: '#ff6b8a' },
};
export function kindMeta(k: string) {
  return KIND_META[k] || { icon: 'pin', cls: '', label: k || '记录', color: '#64748b' };
}
