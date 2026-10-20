export type Nutrient = "kcal" | "protein_g" | "carbs_g" | "fat_g";
export type Targets = Record<Nutrient, number> & { exercise_minutes: number };
export type Totals = Record<Nutrient, number | null> & {
  unknown_items: number;
  complete_nutrition: boolean;
  kcal_range: number[] | null;
};
export type Profile = {
  age: number;
  sex: "female" | "male" | "other";
  height_cm: number;
  weight_kg: number;
  goal_weight_kg: number;
  goal_date: string;
  activity: "sedentary" | "light" | "moderate" | "active";
  nutrition_context?: "unspecified" | "general" | "pregnant" | "breastfeeding" | "clinical";
};
export type DefaultTargets = {
  status: "ready" | "needs_profile" | "unavailable";
  targets: Targets | null;
  reason: string;
  basis: string;
  fingerprint: string;
  formula_version: string;
  warnings: string[];
  sources: { title: string; url: string }[];
};
export type Health = {
  mode: "model" | "demo";
  today: string;
  timezone: string;
  model_name: string | null;
  model_provider?: string | null;
  local_only?: boolean;
  scheduler_enabled: boolean;
};
export type Evidence = { id: string; date: string; kind: string; text: string };
export type ProcessStep = {
  title: string;
  detail: string;
  source: "model" | "local";
};
export type Item = {
  name: string;
  quantity: number | null;
  unit: string;
  kcal: number | null;
  protein_g: number | null;
  carbs_g: number | null;
  fat_g: number | null;
  nutrition_source: string;
  nutrition_status?: "estimated" | "user_provided" | "partial" | "unavailable";
  nutrition_reason?: string | null;
  uncertainty: string;
};
export type Event = {
  id: string;
  kind: "meal" | "weight" | "sleep" | "exercise" | "cycle";
  day: string;
  raw_text: string;
  occurred_at: string;
  version: number;
  data: {
    items: Item[];
    value: number | null;
    quality: string | null;
    exercise_type: string | null;
    intensity: string;
    meal_slot: string | null;
    cycle_action: string | null;
  };
};
export type Daily = {
  date: string;
  totals: Totals;
  targets: Targets | null;
  remaining: Record<Nutrient, number | null> | null;
  weight_kg: number | null;
  sleep_minutes: number | null;
  exercise_minutes: number;
  meal_count: number;
  record_count: number;
  diet_complete: boolean;
  events: Event[];
};
export type Card = {
  type: string;
  event_id: string;
  title: string;
  date: string;
  totals: Totals;
  items: Item[];
  notes: string[];
  coverage: number;
  score: number | null;
};
export type Message = {
  id: string;
  role: "user" | "assistant";
  text: string;
  cards: Card[];
  evidence_refs: Evidence[];
  process_trace: ProcessStep[];
  duration_ms: number;
  created_at: string;
};
export type Pending = {
  interrupt_id: string;
  kind: "clarification" | "plan_confirmation";
  question: string;
  prompt?: string;
  optional?: boolean;
  target_source?: "manual" | "system_default";
  target_basis?: string | null;
  target_warnings?: string[];
  targets?: Targets;
  changes?: Record<string, number>;
  options?: { id: string; label: string }[];
  saved_records?: number;
};
export type Workflow = {
  thread_id: string;
  status: string;
  reply: string;
  cards: Card[];
  evidence_refs: Evidence[];
  process_trace: ProcessStep[];
  duration_ms: number;
  pending_input?: Pending;
  processing_mode?: "model_allowed" | "local_only";
};
export type Plan = {
  id: string;
  status: string;
  created_at: string;
  data: {
    targets: Targets;
    schedule: { date: string; food: string; exercise: string }[];
    next_meal: string;
    tomorrow_focus: string;
    notes: string;
    evidence_refs: Evidence[];
    changes: Record<string, number>;
    data_version: number;
    needs_confirmation: boolean;
    target_source?: "manual" | "system_default";
    target_metadata?: DefaultTargets | null;
  };
};
export type Insight = {
  id: string;
  feedback: string | null;
  data: {
    title: string;
    description: string;
    sample_size: number;
    groups: number[];
    window: string[];
    coverage: number;
    confidence: string;
    limitations: string;
    evidence_refs: Evidence[];
    important: boolean;
  };
};
export type Review = {
  id: string;
  data: {
    period: string;
    start: string;
    end: string;
    summary: string;
    recorded_days: number;
    complete_days: number;
    record_count: number;
    avg_kcal: number | null;
    weight_change: number | null;
    avg_sleep_minutes: number | null;
    exercise_minutes: number;
    evidence_refs: Evidence[];
  };
};
export type Notice = {
  id: string;
  title: string;
  body: string;
  read: number;
  created_at: string;
};
export type Preferences = {
  weights: Record<string, number>;
  meal_times: Record<string, string>;
  checkin_time: string;
  summary_time: string;
  weekly_day: number;
  notifications_enabled: boolean;
};
export type Fact = { id: string; content: string; source: string };
export type Month = {
  month: string;
  data: {
    record_count: number;
    meal_count: number;
    avg_weight_kg: number | null;
    precision: string;
  };
};
