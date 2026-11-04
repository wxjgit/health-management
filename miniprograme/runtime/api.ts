// runtime/api.ts — 业务接口服务层
// 按后端路由分组封装，页面只调用这里的方法，不直接拼路径。
// 契约对齐 backend/app/api/*.py 与 backend/app/contracts/*.py。
import { http, genRequestId, request } from './request';
import { currentMode } from './auth';

/* ══════════ 记录 records ══════════ */
export type EventKind = 'meal' | 'weight' | 'sleep' | 'exercise' | 'cycle';

export interface EventRow {
  id: string;
  kind: EventKind;
  occurred_at?: string;
  day?: string;
  time?: string;
  title?: string;
  detail?: string;
  summary?: string;
  kcal?: number | null;
  protein?: number | null;
  carbs?: number | null;
  fat?: number | null;
  value?: number | null;
  unit?: string;
  revision?: number;
  source?: string;
  foods?: Array<{ name: string; amount?: string; size?: string; kcal?: number | null; source?: string }>;
  feedback?: Array<{ type: string; label?: string; tag?: string; quote?: string }>;
  nutrition_status?: string;
  [k: string]: any;
}

export const records = {
  events(params: { start?: string; end?: string; kind?: EventKind; search?: string; limit?: number } = {}) {
    const q: string[] = [];
    if (params.start) q.push('start=' + params.start);
    if (params.end) q.push('end=' + params.end);
    if (params.kind) q.push('kind=' + params.kind);
    if (params.search) q.push('search=' + encodeURIComponent(params.search));
    q.push('limit=' + (params.limit || 200));
    return http.get<EventRow[]>('/api/events?' + q.join('&'));
  },
  dashboard(day?: string) {
    return http.get<any>('/api/dashboard' + (day ? '?date=' + day : ''));
  },
  markComplete(day: string, complete: boolean) {
    return http.put<any>('/api/days/' + day + '/complete', { complete });
  },
  trends(days = 28) {
    return http.get<any>('/api/trends?days=' + days);
  },
  revisions(eventId: string) {
    return http.get<any>('/api/events/' + eventId + '/revisions');
  },
  deleteEvent(eventId: string, requestId = genRequestId('del_evt')) {
    return request({ path: '/api/events/' + eventId, method: 'DELETE', requestId });
  },
};

/* ══════════ 聊天 / 工作流 ══════════ */
export interface ChatResult {
  thread_id?: string;
  reply?: string;
  cards?: any[];
  clarification?: any;
  question?: string | null;
  options?: any[];
  events?: EventRow[];
  saved?: any[];
  status?: string;
  interrupt_id?: string | null;
  processing_mode?: string;
  model_disclosures?: any[];
  process_trace?: any[];
  [k: string]: any;
}

export const chat = {
  send(text: string, opts: { requestId?: string; eventId?: string; conversationId?: string } = {}) {
    return http.post<ChatResult>('/api/chat', {
      request_id: opts.requestId || genRequestId('chat'),
      conversation_id: opts.conversationId || 'main',
      text,
      event_id: opts.eventId || null,
      processing_mode: currentMode(),
    });
  },
  resume(threadId: string, body: {
    interrupt_id: string; text?: string | null; approved?: boolean | null;
    event_id?: string | null; requestId?: string;
  }) {
    return http.post<ChatResult>('/api/workflows/' + threadId + '/resume', {
      request_id: body.requestId || genRequestId('resume'),
      interrupt_id: body.interrupt_id,
      text: body.text ?? null,
      approved: body.approved ?? null,
      event_id: body.event_id ?? null,
      processing_mode: currentMode(),
    });
  },
  waiting(includeExpired = false) {
    return http.get<any[]>('/api/workflows?include_expired=' + includeExpired);
  },
  cancel(threadId: string) {
    return request({ path: '/api/workflows/' + threadId, method: 'DELETE' });
  },
};

/* ══════════ 计划 planning ══════════ */
export const plans = {
  list() { return http.get<any[]>('/api/plans'); },
  propose(body: {
    targets?: any; target_source?: string; suggestion_fingerprint?: string; note?: string;
    requestId?: string;
  }) {
    return http.post<any>('/api/plans/propose', {
      request_id: body.requestId || genRequestId('plan'),
      targets: body.targets || null,
      target_source: body.target_source || 'default',
      suggestion_fingerprint: body.suggestion_fingerprint || null,
      note: body.note || null,
    });
  },
  defaultTargets() { return http.get<any>('/api/targets/default'); },
};

/* ══════════ 洞察 / 复盘 / 通知 / 摘要 ══════════ */
export const insights = {
  list(includeStale = false) { return http.get<any[]>('/api/insights?include_stale=' + includeStale); },
  feedback(id: string, fb: 'accepted' | 'rejected') {
    return http.patch('/api/insights/' + id, { feedback: fb });
  },
  reviews() { return http.get<any[]>('/api/reviews'); },
  generateReview(period: 'day' | 'week' = 'week', requestId = genRequestId('review')) {
    return http.post<any>('/api/reviews', { request_id: requestId, period });
  },
  summaries() { return http.get<any[]>('/api/monthly-summaries'); },
  notifications() { return http.get<any[]>('/api/notifications'); },
  readNotification(id: string) { return http.patch('/api/notifications/' + id); },
};

/* ══════════ 档案 / 偏好 / 长期事实 ══════════ */
export const profile = {
  get() { return http.get<any>('/api/profile'); },
  update(body: any) { return http.put<any>('/api/profile', body); },
  preferences() { return http.get<any>('/api/preferences'); },
  updatePreferences(body: any) { return http.put<any>('/api/preferences', body); },
  weightSuggestion() { return http.get<any>('/api/preferences/suggestion'); },

  facts(includeInactive = false) { return http.get<any[]>('/api/facts?include_inactive=' + includeInactive); },
  createFact(body: any) { return http.post<any>('/api/facts', body); },
  updateFact(id: string, body: any) { return http.put<any>('/api/facts/' + id, body); },
  decideFact(id: string, body: { action: string; expected_version?: number; resolution?: any; peer_versions?: any }) {
    return http.patch<any>('/api/facts/' + id, body);
  },
  factRevisions(id: string) { return http.get<any>('/api/facts/' + id + '/revisions'); },
  deleteFact(id: string, requestId = genRequestId('del_fact')) {
    return request({ path: '/api/facts/' + id, method: 'DELETE', requestId });
  },
};

/* ══════════ 隐私删除 deletions ══════════ */
export const deletions = {
  preview(targetType: 'event' | 'fact', targetId: string) {
    return http.post<any>('/api/deletions/preview', { target_type: targetType, target_id: targetId });
  },
  execute(previewId: string, requestId = genRequestId('del')) {
    return http.post<any>('/api/deletions', { preview_id: previewId, request_id: requestId });
  },
  status(deletionId: string) { return http.get<any>('/api/deletions/' + deletionId); },
  retry(deletionId: string) { return http.post<any>('/api/deletions/' + deletionId + '/retry'); },
};

/* ══════════ 系统 / 消息 / 导出 ══════════ */
export const system = {
  health() { return http.get<any>('/api/health', { auth: false }); },
  messages(limit = 100, before?: string) {
    return http.get<any[]>('/api/messages?limit=' + limit + (before ? '&before=' + before : ''));
  },
  exportUrl() { return getApp().globalData.apiOrigin + '/api/data/export'; },
  jobsStatus() { return http.get<any>('/api/jobs/status'); },
};
