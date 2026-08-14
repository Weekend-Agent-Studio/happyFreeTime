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

export type Stop = {
  resource_id: string;
  type: "activity" | "restaurant" | "cafe" | "dessert";
  name: string;
  start: string;
  end: string;
  duration_minutes: number;
  price: number;
};

export type RouteLeg = {
  origin_name: string;
  destination_name: string;
  mode: string;
  distance_km: number;
  duration_minutes: number;
  source: string;
  /** true 表示当前使用本地估算或其他降级来源，而不是真实路线服务。 */
  degraded: boolean;
  degraded_reason?: string;
};

export type Plan = {
  plan_id: string;
  title: string;
  strategy: string;
  total_score: number;
  total_price: number;
  total_duration_minutes: number;
  stops: Stop[];
  route_legs: RouteLeg[];
  highlights: string[];
  tradeoffs: string[];
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
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
};
