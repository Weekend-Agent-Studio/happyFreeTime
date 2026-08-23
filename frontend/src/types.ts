/** 前端消费的 API 数据契约；字段与后端 Pydantic JSON 保持一致。 */
export type Assumption = {
  field: string;
  value: unknown;
  reason: string;
  rule_id: string;
};

export type ConstraintSummaryItem = {
  field: string;
  value: unknown;
  source: "user_explicit" | "user_inferred" | "session_confirmed" | "memory" | "system_context" | "real_tool" | "default_rule";
  evidence: string | null;
  confidence: number | null;
  rule_id: string | null;
  user_editable: boolean;
};

export type CatalogSource = {
  source_name: string;
  source_uri: string;
  source_license: string;
  coordinate_system: "WGS84" | "GCJ02";
  collected_at: string;
  last_verified_at: string | null;
  verification_status: "verified" | "unverified" | "stale";
  dynamic_fields_mock: string[];
  estimated_fields: string[];
  derived_fields: string[];
};

export type ImageRef = {
  url: string;
  source_uri: string;
  author: string | null;
  license: string;
  license_uri: string;
  attribution: string | null;
};

export type Stop = {
  resource_id: string;
  type: "activity" | "restaurant" | "cafe" | "dessert";
  name: string;
  start: string;
  end: string;
  duration_minutes: number;
  price: number;
  price_kind: "known" | "estimated" | "free" | "unknown";
  image: ImageRef | null;
  source: CatalogSource;
};

export type CatalogViolation = {
  resource_id: string;
  resource_name: string;
  code: string;
  field: string;
  message: string;
};

export type RouteLeg = {
  origin_name: string;
  destination_name: string;
  start: string;
  end: string;
  mode: string;
  distance_km: number;
  duration_minutes: number;
  source: string;
  provider_mode: "live" | "record" | "replay" | "mock";
  verified_at: string | null;
  cache_age_seconds: number | null;
  geometry: Array<{ latitude: number; longitude: number }>;
  /** true 表示当前使用本地估算或其他降级来源，而不是真实路线服务。 */
  degraded: boolean;
  degraded_reason: string | null;
};

export type Plan = {
  plan_id: string;
  title: string;
  strategy: string;
  total_score: number;
  total_price: number;
  price_status: "known" | "estimated" | "incomplete";
  total_duration_minutes: number;
  stops: Stop[];
  route_legs: RouteLeg[];
  highlights: string[];
  tradeoffs: string[];
};

export type ProviderFact = {
  kind: "weather";
  city: string;
  district: string;
  date: string;
  condition: string;
  temperature_c: number | null;
  precipitation_mm: number;
  is_adverse: boolean;
  source: "amap_live" | "cache" | "replay" | "mock" | "local_estimate";
  mode: "live" | "record" | "replay" | "mock";
  observed_at: string;
  verified_at: string;
  degraded: boolean;
  degraded_reason: string | null;
  cache_age_seconds: number | null;
};

export type AgentResponse = {
  /** needs_input 表示 Graph 已在 interrupt 暂停，下一条消息将恢复该会话。 */
  status: "completed" | "needs_input";
  reply: string;
  question: { field: string; question: string; severity: string } | null;
  assumptions: Assumption[];
  constraint_summary: ConstraintSummaryItem[];
  plans: Plan[];
  conflict: {
    code: string;
    message: string;
    relaxation_options: string[];
  } | null;
  provider_facts: ProviderFact[];
  catalog_violations: CatalogViolation[];
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
};
