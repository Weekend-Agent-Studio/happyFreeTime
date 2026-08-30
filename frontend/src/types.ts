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
  collected_at: string;
  last_verified_at: string | null;
  verification_status: "verified" | "unverified" | "stale";
};

export type ImageRef = {
  url: string;
  source_uri: string;
  author: string | null;
  license: string;
  license_uri: string;
  attribution: string | null;
};

export type WebMapConfig = {
  enabled: boolean;
  provider: "none" | "amap";
  js_api_key: string | null;
  version: string | null;
  service_host_path: string | null;
};

export type StopRole = "activity" | "meal" | "lunch" | "dinner" | "break";

export type Stop = {
  resource_id: string;
  type: "activity" | "restaurant" | "cafe" | "dessert";
  role: StopRole | null;
  name: string;
  start: string;
  end: string;
  duration_minutes: number;
  price: number;
  price_kind: "known" | "estimated" | "free" | "unknown";
  category_tags: string[];
  image: ImageRef | null;
  source: CatalogSource;
};

export type PoiGalleryItem = {
  url: string;
  alt: string;
  kind: "source" | "illustrative";
  attribution: string | null;
  license: string | null;
};

/** Rich display data is deliberately separate from a planning Stop. */
export type PoiPresentation = {
  resource_id: string;
  name: string;
  category_label: string;
  business_area: string;
  address: string;
  description: string;
  scene_tags: string[];
  facility_tags: string[];
  indoor: boolean;
  weather_suitability: string;
  child_suitability: string;
  reference_avg_price: number | null;
  demo_rating: number;
  demo_review_count: number;
  opening_hours_display: string;
  opening_status: string;
  suggested_duration_minutes: number;
  reservation_requirement: string;
  queue_profile: string;
  risk_tips: string[];
  booking_mode: string;
  gallery: PoiGalleryItem[];
  data_notice: string;
};

export type CatalogViolation = {
  resource_id: string;
  resource_name: string;
  code: string;
  field: string;
  message: string;
};

export type CatalogWarning = CatalogViolation;

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

export type ScoreContribution = {
  rule_id: string;
  dimension: string;
  points: number;
  message: string;
  evidence: string[];
};

export type Plan = {
  plan_id: string;
  composition_fingerprint: string;
  skeleton_id: string | null;
  title: string;
  strategy: string;
  total_score: number;
  total_price: number;
  price_status: "known" | "estimated" | "incomplete";
  total_duration_minutes: number;
  stops: Stop[];
  route_legs: RouteLeg[];
  score_breakdown: ScoreContribution[];
  highlights: string[];
  tradeoffs: string[];
};

type ProviderFactBase = {
  source: "amap_live" | "cache" | "replay" | "mock" | "local_estimate";
  mode: "live" | "record" | "replay" | "mock";
  observed_at: string;
  verified_at: string;
  degraded: boolean;
  degraded_reason: string | null;
};

export type ProviderFact = ({
  kind: "weather";
  city: string;
  district: string;
  date: string;
  condition: string;
  temperature_c: number | null;
  precipitation_mm: number;
  is_adverse: boolean;
  cache_age_seconds: number | null;
} | {
  kind: "geocoding";
  request: { location_text: string; city: string | null };
  resolution: "resolved" | "not_found" | "ambiguous" | "unavailable";
  city: string | null;
  district: string | null;
  address: string | null;
  verified: boolean;
} | {
  kind: "availability";
  resource_id: string;
  status: "available" | "unavailable" | "unknown";
  verified: boolean;
  stale: boolean;
  reason: string | null;
}) & ProviderFactBase;

export type PlanWarning = {
  code: string;
  message: string;
  plan_id: string | null;
  resource_id: string | null;
  route_leg_index: number | null;
  source: string | null;
  degraded: boolean;
  stale: boolean;
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
  catalog_warnings: CatalogWarning[];
  warnings?: PlanWarning[];
  poi_presentations: PoiPresentation[];
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at?: string;
};

export type SessionSummary = {
  session_id: string;
  title: string;
  status: string;
  created_at: string;
  updated_at: string;
  last_message_preview: string;
};

export type SessionView = {
  session_id: string;
  title: string;
  status: string;
  created_at: string;
  updated_at: string;
  messages: ChatMessage[];
  plans: Plan[];
  latest_response: AgentResponse | null;
};
