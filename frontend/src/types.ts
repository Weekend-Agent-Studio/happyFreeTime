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

export type PlanDiff = {
  from_plan_version_id: string | null;
  to_plan_version_id: string | null;
  base_plan_id: string;
  new_plan_id: string;
  locked_stops: Array<{
    source_plan_id: string;
    stop_index: number;
    resource_id: string;
    role: Stop["role"];
  }>;
  replacements: Array<{
    stop_index: number;
    role: Stop["role"];
    before_resource_id: string;
    before_name: string;
    after_resource_id: string;
    after_name: string;
  }>;
  route_distance_delta_km: number;
  duration_delta_minutes: number;
  price_delta: number;
};

export type EvidenceRef = {
  evidence_id: string;
  source_type: "user_message" | "poi_profile" | "fixture_aspect" | "provider";
  source_field: string;
  summary: string;
  source_ref: string | null;
  confidence: number;
};

export type NeedSummary = {
  need_id: string;
  text: string;
  user_evidence_ids: string[];
};

export type PlanAdvice = {
  plan_id: string;
  reason: string;
  matched_need_ids: string[];
  supporting_evidence_ids: string[];
  tradeoffs: string[];
};

export type RecommendationAdvice = {
  recommended_plan_id: string;
  understood_needs: NeedSummary[];
  overall_reason: string;
  plans: PlanAdvice[];
  adapter: "rule_based" | "llm" | "fallback";
  fallback_reason: string | null;
  prompt_version: string;
  model_name: string | null;
  model_invoked: boolean;
  attempts: number;
  latency_ms: number;
};

export type RuntimeDecision = {
  stage: "turn_interpreter" | "planning_intent" | "candidate_retrieval" | "recommendation_advisor";
  adapter: string;
  model_invoked: boolean;
  model_name: string | null;
  attempts: number;
  fallback_reason: string | null;
  latency_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
};

export type RunStage =
  | "understand"
  | "compile_request"
  | "clarify"
  | "structure"
  | "retrieve"
  | "construct"
  | "verify"
  | "modify"
  | "recovery"
  | "auto_recovery"
  | "recovery_action"
  | "advise";

export type RunEventStatus = "started" | "completed" | "fallback" | "failed" | "waiting_input";

export type PlanningRunEvent = {
  schema_version: "planning-run-event.v1";
  run_id: string;
  sequence: number;
  stage: RunStage;
  status: RunEventStatus;
  message_key: string;
  public_message: string;
  occurred_at: string;
  duration_ms: number | null;
  public_details: Record<string, string | number | boolean | null>;
};

export type PlanningRunTrace = {
  schema_version: "planning-run-trace.v1";
  run_id: string;
  events: PlanningRunEvent[];
};

export type ReplacementCriterion =
  | { kind: "semantic"; text: string; strength: "preferred" }
  | { kind: "route_objective"; metric: "total_route_distance"; direction: "decrease"; strength: "required" };

export type ConversationCommand = {
  operation: "replace";
  base_plan_version_id?: string;
  base_plan_id?: string;
  target: {
    stop_index: number;
    resource_id: string;
    role?: Stop["role"];
    raw_text: string;
  };
  replacement_criteria: ReplacementCriterion[];
};

export type ClarificationAction = "answer" | "use_default" | "cancel" | "new_request";

export type ClarificationOption = {
  id: string;
  label: string;
  action: ClarificationAction;
};

export type ClarificationReply = {
  clarification_id: string;
  action: ClarificationAction;
  request_revision: number;
  value?: string | null;
};

export type PlanningContextField = {
  value: unknown;
  display_value: string;
  source: "user" | "derived" | "default";
  editable: boolean;
  status: "resolved" | "assumed" | "pending";
};

export type PlanningContextSummary = {
  request_revision: number;
  planned_request_revision: number | null;
  has_active_plan: boolean;
  plan_stale: boolean;
  ready_for_planning: boolean;
  where: PlanningContextField;
  when: {
    date: PlanningContextField;
    start_at: PlanningContextField;
    end_at: PlanningContextField;
    start_kind: "trip_start" | "departure";
    end_kind: "trip_end" | "return_deadline";
  };
  who: PlanningContextField;
  budget: PlanningContextField;
  preferences: PlanningContextField;
  pending_field: string | null;
};

export type FieldEdit<T> = { operation: "set"; value: T } | { operation: "clear" };
export type PreferenceCategory = "preferences" | "diet_tags" | "scene_tags" | "avoid";
export type PreferenceTag = { category: PreferenceCategory; value: string };
export type PlanningContextPatch = {
  base_revision: number;
  where?: FieldEdit<string>;
  when?: {
    date?: FieldEdit<string>;
    start_at?: FieldEdit<string>;
    end_at?: FieldEdit<string>;
  };
  who?: {
    adults?: number;
    children?: number;
    child_age?: FieldEdit<number>;
    members?: FieldEdit<string[]>;
  };
  budget?: { mode: "unlimited" } | { mode: "per_person"; amount: number; strict: boolean };
  preferences?: { add?: PreferenceTag[]; remove?: PreferenceTag[] };
};

export type RecoveryAction = {
  action_id: string;
  label: string;
  description: string;
  request_revision: number;
  plan_version_id: string | null;
  continuation: "compile_patch" | "plan" | "modify" | "clarify_field" | "finish";
} & (
  | { kind: "request_field"; field: string; input_type: "text" | "number" | "clock" | "choice"; choices: string[] }
  | { kind: "apply_request_patch"; patch: unknown; auto_eligible?: boolean }
  | { kind: "replan_current_request" }
  | { kind: "cancel_turn" }
  | { kind: "start_new_request" }
  | { kind: "keep_current_plan" }
  | { kind: "open_constraint_editor" }
);

export type RecoveryChoice = {
  kind: "recovery_choice";
  interaction_id: string;
  request_revision: number;
  plan_version_id: string | null;
  reason: {
    code: string;
    kind: "hard_conflict" | "no_feasible_plan" | "modification_failed";
    stage: string;
    fields: string[];
    request_revision: number;
    plan_version_id: string | null;
    public_summary: string;
    diagnostics: Record<string, unknown>;
  };
  actions: RecoveryAction[];
};

export type RecoveryActionRequest = {
  interaction_id: string;
  action_id: string;
  request_revision: number;
  plan_version_id: string | null;
  field_value?: string | null;
};

export type AgentResponse = {
  /** needs_input 表示需要用户补充；规划字段可能暂停 Graph，修改引用可重新描述。 */
  status: "completed" | "needs_input" | "needs_recovery" | "context_saved";
  reply: string;
  question: {
    field: string | null;
    question: string;
    severity: string;
    request_revision: number;
    rule_id?: string;
    clarification_id?: string | null;
    attempt?: number;
    max_attempts?: number;
    allow_free_text?: boolean;
    options?: ClarificationOption[];
  } | null;
  recovery?: RecoveryChoice | null;
  recovery_resolution?: "cancel_turn" | "start_new_request" | "keep_current_plan" | "open_constraint_editor" | null;
  assumptions: Assumption[];
  constraint_summary: ConstraintSummaryItem[];
  plans: Plan[];
  conflict: {
    code: string;
    message: string;
  } | null;
  provider_facts: ProviderFact[];
  catalog_violations: CatalogViolation[];
  catalog_warnings: CatalogWarning[];
  warnings?: PlanWarning[];
  poi_presentations: PoiPresentation[];
  plan_version_id?: string | null;
  runtime_decisions?: RuntimeDecision[];
  run_trace?: PlanningRunTrace | null;
  retrieval_evidence?: EvidenceRef[];
  retrieval_mode?: string | null;
  retrieval_index_version?: string | null;
  search_mode?: string | null;
  search_finalist_count?: number | null;
  search_expansions?: number | null;
  conversation_command?: Record<string, unknown> | null;
  plan_diff?: PlanDiff | null;
  plan_diffs?: PlanDiff[];
  recommendation_advice?: RecommendationAdvice | null;
  planning_context?: PlanningContextSummary | null;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at?: string;
  /** The immutable structured payload produced by this assistant turn. */
  response?: AgentResponse;
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
  /** Older persisted sessions may predate fields added to AgentResponse. */
  latest_response: Partial<AgentResponse> | null;
  /** Optional for backward compatibility with sessions saved before rich-turn history. */
  response_history?: Partial<AgentResponse>[];
  active_plan_version_id?: string | null;
  selected_plan_id?: string | null;
  active_constraints?: unknown;
  planning_context?: PlanningContextSummary | null;
  plan_versions?: Array<{
    plan_version_id: string;
    planning_run_id: string;
    supersedes_version_id: string | null;
    created_at: string;
  }>;
};
