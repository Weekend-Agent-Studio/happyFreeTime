import { Dialog } from "@base-ui/react/dialog";
import { FormEvent, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode, type RefObject, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeftRight,
  ArrowUpRight,
  Bus,
  CalendarDays,
  Car,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  CircleAlert,
  CircleDollarSign,
  Clock3,
  CloudSun,
  Compass,
  Database,
  ExternalLink,
  Footprints,
  History,
  Image as ImageIcon,
  Info,
  Map as MapIcon,
  MapPin,
  Navigation,
  PanelRight,
  Route,
  Send,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Store,
  Tag,
  Utensils,
  UsersRound,
  X,
} from "lucide-react";

import { createSession, deleteSession, getSession, listSessions, renameSession, replanPlanningContext, selectPlan, sendMessageStream, updatePlanningContext } from "./api";
import { SessionNavigation } from "./SessionNavigation";
import { ConversationHeading } from "./ConversationHeading";
import { AmapPlanMap } from "./AmapPlanMap";
import { clarificationSection, PlanningContextBar, PlanningContextNotice, type ContextOpenTarget } from "./PlanningContextBar";
import { projectRunProgress, type RunStageViewModel } from "./runProgress";
import type {
  AgentResponse,
  Assumption,
  ChatMessage,
  ClarificationOption,
  ClarificationReply,
  ConversationCommand,
  ConstraintSummaryItem,
  Plan,
  PlanningContextPatch,
  PlanningContextSummary,
  PlanAdvice,
  PlanDiff,
  PlanWarning,
  PoiPresentation,
  ProviderFact,
  RecoveryAction,
  RecoveryActionRequest,
  RecoveryChoice,
  RecommendationAdvice,
  ReplacementCriterion,
  RouteLeg,
  RuntimeDecision,
  PlanningRunEvent,
  PlanningRunTrace,
  SessionSummary,
  SessionView,
  Stop,
} from "./types";

const SUGGESTIONS = [
  "今天下午出去玩，别太远",
  "周六和朋友聚一下，人均150",
  "安排一个轻松的约会，想吃甜品",
];

const PANE_RESIZER_WIDTH = 12;
const MIN_PANE_WIDTH = 320;
const MIN_PANE_SHARE = 0.4;
const MAX_CHAT_PANE_SHARE = 0.65;

const CLARIFICATION_FIELD_LABELS: Record<string, string> = {
  date: "出行日期",
  location: "出发地",
  planning_area: "出发区域",
  budget_per_person: "人均预算",
  strict_budget: "预算约束方式",
  exact_stop_count: "精确站数",
  max_distance_km: "检索及单段距离上限",
  total_distance_km: "全程累计距离上限",
  start_at: "出发时间",
  departure_at: "出发时间",
  end_at: "结束时间",
  return_by: "最晚到家时间",
  party: "同行人",
  preferences: "出行偏好",
};

function clarificationFieldLabel(field: string | null | undefined): string {
  return field ? CLARIFICATION_FIELD_LABELS[field] ?? "相关信息" : "相关信息";
}

const SESSION_QUERY_KEY = "session";
type InspectorTab = "trip" | "map" | "evidence";

type PoiNavigation = {
  sessionId: string | null;
  planVersionId: string | null;
  response: AgentResponse;
  planId: string;
  stopIndex: number;
  resourceId: string;
  returnTab: InspectorTab;
  returnScrollTop: number;
  returnLegIndex: number | null;
  returnFocusElement: HTMLElement | null;
};

type TimelineRouteScrollTarget = {
  sessionId: string | null;
  response: AgentResponse | null;
  planId: string | null;
  legIndex: number;
  isMobile: boolean;
};

type ReplacementDraft = {
  sessionId: string;
  planVersionId: string;
  planId: string;
  stopIndex: number;
  resourceId: string;
  role: Stop["role"];
  contextRevision: number | null;
  rawText: string;
  criteria: ReplacementCriterion[];
};

type ReplacementFocusOrigin = {
  stopIndex: number;
  wasMobile: boolean;
  triggerElement: HTMLElement | null;
};

type PlanSelectionTarget = {
  sessionId: string;
  planVersionId: string;
  planId: string;
  planTitle: string;
};

type PendingRecoveryConfirmation = {
  sessionId: string;
  interaction: RecoveryChoice;
  action: Extract<RecoveryAction, { kind: "apply_request_patch" }>;
  contextRevision: number | null;
  context: PlanningContextSummary | null;
};

type ReplacementPreset = "similar" | "shorter" | "quiet" | "chatty" | "family";

type RecoveryLifecycleStatus =
  | "submitted"
  | "awaiting_input"
  | "succeeded"
  | "still_failed"
  | "cancelled"
  | "finished"
  | "retryable";

type RecoveryLifecycle = {
  actionId: string;
  label: string;
  status: RecoveryLifecycleStatus;
  summary?: string;
  fieldChanges?: { field: string; before: string | null; after: string | null }[];
  previousBottleneck?: string | null;
  currentBottleneck?: string | null;
};

const REPLACEMENT_PRESET_CRITERIA: Record<ReplacementPreset, ReplacementCriterion[]> = {
  similar: [],
  shorter: [{ kind: "route_objective", metric: "total_route_distance", direction: "decrease", strength: "required" }],
  quiet: [{ kind: "semantic", text: "安静", strength: "preferred" }],
  chatty: [{ kind: "semantic", text: "轻松聊天", strength: "preferred" }],
  family: [{ kind: "semantic", text: "亲子", strength: "preferred" }],
};

const REPLACEMENT_PRESET_LABELS: Record<ReplacementPreset, string> = {
  similar: "换个类似的",
  shorter: "全程更近",
  quiet: "更安静",
  chatty: "更适合聊天",
  family: "亲子友好",
};

function replacementCommandFromDraft(
  draft: ReplacementDraft,
  criteria: ReplacementCriterion[],
  freeText: string,
): ConversationCommand {
  const textCriteria: Array<Extract<ReplacementCriterion, { kind: "semantic" }>> = freeText
    .split(/[，,、\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
    .map((text) => ({ kind: "semantic", text, strength: "preferred" as const }));
  const mergedCriteria = [...criteria];
  for (const criterion of textCriteria) {
    if (!mergedCriteria.some((item) => isSemanticCriterion(item) && item.text === criterion.text)) {
      mergedCriteria.push(criterion);
    }
  }
  return {
    operation: "replace",
    base_plan_version_id: draft.planVersionId,
    base_plan_id: draft.planId,
    target: {
      stop_index: draft.stopIndex,
      resource_id: draft.resourceId,
      role: draft.role,
      raw_text: draft.rawText,
    },
    replacement_criteria: mergedCriteria,
  };
}

function isSemanticCriterion(criterion: ReplacementCriterion): criterion is Extract<ReplacementCriterion, { kind: "semantic" }> {
  return criterion.kind === "semantic";
}

function stopRoleLabel(role: Stop["role"] | null | undefined): string {
  if (role === "activity") return "活动";
  if (role === "meal" || role === "lunch" || role === "dinner") return "餐饮";
  if (role === "break") return "休息";
  return "站点";
}

function replacementName(name: unknown, resourceId: unknown): string {
  if (typeof name === "string" && name.trim()) return name;
  if (typeof resourceId === "string" && resourceId.trim()) return `地点 ${resourceId}`;
  return "地点信息未返回";
}

function planDiffDelta(value: unknown, kind: "distance" | "duration" | "price"): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "未返回";
  if (value === 0) return "不变";
  const sign = value < 0 ? "−" : "+";
  const amount = Math.abs(value);
  if (kind === "distance") return `${sign}${amount.toFixed(1)} km`;
  if (kind === "duration") return `${sign}${amount} 分钟`;
  return `${sign}¥${amount.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;
}

function PlanDiffEvidence({ diff, plan }: { diff: PlanDiff | null; plan: Plan }) {
  if (!diff) {
    return <div className="plan-diff-missing" role="note">服务端未返回此候选的变更明细</div>;
  }
  const safeDiff = diff as Partial<PlanDiff>;
  const replacements = Array.isArray(safeDiff.replacements) ? safeDiff.replacements.filter(Boolean) : null;
  return (
    <section
      className="plan-diff-evidence"
      aria-label={`方案修改明细：${plan.title}`}
      data-plan-id={diff.new_plan_id}
      data-plan-version-id={diff.to_plan_version_id ?? ""}
      data-base-plan-id={diff.base_plan_id}
    >
      <div className="plan-diff-heading">
        <span><ArrowLeftRight size={14} aria-hidden="true" />调整对比</span>
        {replacements?.length ? <span className="plan-diff-count">替换 {replacements.length} 站</span> : null}
      </div>
      <div className="plan-diff-replacements">
        {replacements?.length ? replacements.map((replacement, index) => (
          <div className="plan-diff-replacement" key={`${replacement.stop_index}-${replacement.after_resource_id}-${index}`}>
            <span>{typeof replacement.stop_index === "number" && Number.isInteger(replacement.stop_index) && replacement.stop_index >= 0 ? `第 ${replacement.stop_index + 1} 站` : "站序未返回"} · {stopRoleLabel(replacement.role)}</span>
            <strong>{replacementName(replacement.before_name, replacement.before_resource_id)} → {replacementName(replacement.after_name, replacement.after_resource_id)}</strong>
          </div>
        )) : <span className="plan-diff-unavailable">{replacements ? "未返回站点替换明细" : "未返回站点替换信息"}</span>}
      </div>
      <div className="plan-diff-metrics" aria-label="与原方案的全程变化">
        <span><strong>全程距离</strong><b>{planDiffDelta(safeDiff.route_distance_delta_km, "distance")}</b></span>
        <span><strong>总时长</strong><b>{planDiffDelta(safeDiff.duration_delta_minutes, "duration")}</b></span>
        <span title="地点费用，不含交通"><strong>地点费用</strong><b>{planDiffDelta(safeDiff.price_delta, "price")}</b></span>
      </div>
    </section>
  );
}

function sessionIdFromUrl(): string | null {
  return new URL(window.location.href).searchParams.get(SESSION_QUERY_KEY);
}

function updateSessionUrl(sessionId: string | null, replace = false) {
  const url = new URL(window.location.href);
  if (sessionId) url.searchParams.set(SESSION_QUERY_KEY, sessionId);
  else url.searchParams.delete(SESSION_QUERY_KEY);
  const method = replace ? "replaceState" : "pushState";
  window.history[method]({}, "", `${url.pathname}${url.search}${url.hash}`);
}

function normalizeAgentResponse(response: Partial<AgentResponse>): AgentResponse {
  return {
    status: response.status ?? "completed",
    reply: response.reply ?? "",
    question: response.question ?? null,
    recovery: response.recovery ?? null,
    recovery_resolution: response.recovery_resolution ?? null,
    recovery_outcome: response.recovery_outcome ?? null,
    assumptions: response.assumptions ?? [],
    constraint_summary: response.constraint_summary ?? [],
    plans: response.plans ?? [],
    conflict: response.conflict ?? null,
    provider_facts: response.provider_facts ?? [],
    catalog_violations: response.catalog_violations ?? [],
    catalog_warnings: response.catalog_warnings ?? [],
    warnings: response.warnings ?? [],
    poi_presentations: response.poi_presentations ?? [],
    plan_version_id: response.plan_version_id ?? null,
    retrieval_evidence: response.retrieval_evidence ?? [],
    runtime_decisions: response.runtime_decisions ?? [],
    run_trace: response.run_trace ?? null,
    failure: response.failure ?? null,
    retrieval_mode: response.retrieval_mode ?? null,
    retrieval_index_version: response.retrieval_index_version ?? null,
    search_mode: response.search_mode ?? null,
    search_finalist_count: response.search_finalist_count ?? null,
    search_expansions: response.search_expansions ?? null,
    conversation_command: response.conversation_command ?? null,
    plan_diff: response.plan_diff ?? null,
    plan_diffs: response.plan_diffs ?? (response.plan_diff ? [response.plan_diff] : []),
    recommendation_advice: response.recommendation_advice ?? null,
    planning_context: response.planning_context ?? null,
  };
}

function responsesFromSession(view: SessionView): AgentResponse[] {
  const history = (view.response_history ?? [])
    .map(normalizeAgentResponse)
    .filter((response) => response.status !== "context_saved");
  if (history.length) return history;
  if (view.latest_response) {
    const latest = normalizeAgentResponse(view.latest_response);
    if (latest.status !== "context_saved") return [latest];
  }
  if (!view.plans.length) return [];
  return [{
    status: "completed", reply: "", question: null, assumptions: [], constraint_summary: [],
    plans: view.plans, conflict: null, provider_facts: [], catalog_violations: [],
    catalog_warnings: [], warnings: [], poi_presentations: [],
    runtime_decisions: [],
  }];
}

function activePlanResponse(view: SessionView, responses: AgentResponse[]): AgentResponse | null {
  if (view.active_plan_version_id) {
    const matching = responses.find(
      (candidate) => candidate.plan_version_id === view.active_plan_version_id && candidate.plans.length > 0,
    );
    if (matching) return matching;
  }
  if (view.plans.length > 0) {
    return normalizeAgentResponse({
      status: "completed",
      plans: view.plans,
      plan_version_id: view.active_plan_version_id ?? null,
    });
  }
  return null;
}

function attachResponsesToMessages(messages: ChatMessage[], responses: AgentResponse[]): ChatMessage[] {
  let responseIndex = 0;
  const restored = messages.map((message) => {
    if (message.role !== "assistant" || !responses[responseIndex]) return message;
    return { ...message, response: responses[responseIndex++] };
  });
  while (responseIndex < responses.length) {
    const response = responses[responseIndex];
    restored.push({
      id: `restored-response-${responseIndex}`,
      role: "assistant",
      content: response.question?.question ?? response.reply ?? response.conflict?.message ?? "",
      response,
    });
    responseIndex += 1;
  }
  return restored;
}

function displayAssumption(assumption: Assumption): string {
  if (assumption.field === "budget_per_person") return `人均 ${assumption.value} 元`;
  if (assumption.field === "max_distance_km") return `${assumption.value} km 内`;
  if (assumption.field === "total_distance_km") return `全程 ${assumption.value} km 内`;
  if (assumption.field === "return_by") return `${assumption.value} 前到家`;
  if (assumption.field === "exact_stop_count") return `只安排 ${assumption.value} 站`;
  if (assumption.field === "required_stop_roles" && Array.isArray(assumption.value)) {
    return assumption.value.map((role) => ({ dinner: "晚餐", lunch: "午餐", meal: "餐饮", activity: "活动", break: "休息" }[String(role)] ?? String(role))).join("、");
  }
  if (assumption.field === "time_window" && typeof assumption.value === "object") {
    const value = assumption.value as { start?: string; end?: string };
    return `${value.start ?? ""}-${value.end ?? ""}`;
  }
  if (assumption.field === "party") return "1 位成人";
  return String(assumption.value);
}

function displayConstraint(item: ConstraintSummaryItem): string {
  if (["time_scope", "trip_time_scope", "activity_time_scope"].includes(item.field)) {
    return ({
      morning: "上午",
      afternoon: "下午",
      evening: "晚上",
      all_day: "全天",
      explicit_range: "明确时间段",
    } as Record<string, string>)[String(item.value)] ?? String(item.value);
  }
  if (item.field === "party" && typeof item.value === "object") {
    const value = item.value as { adults?: number; children?: number };
    const total = (value.adults ?? 0) + (value.children ?? 0);
    return `${total} 人`;
  }
  if (item.field === "location" && typeof item.value === "object") {
    const value = item.value as { district?: string; address?: string; city?: string };
    return value.district || value.address || value.city || "未解析地点";
  }
  if (item.field === "duration_minutes") return `${item.value} 分钟`;
  return displayAssumption({ field: item.field, value: item.value, reason: "", rule_id: item.rule_id ?? "" });
}

type PlanningConstraintTag = { field: string; label: string; value: string };

function planningConstraintTag(item: ConstraintSummaryItem): PlanningConstraintTag | null {
  if (item.value === null || item.value === undefined || item.source === "default_rule") return null;
  const value = displayConstraint(item);
  switch (item.field) {
    case "time_window": return { field: item.field, label: "时间", value };
    case "departure_at": return { field: item.field, label: "准时出发", value };
    case "return_by": return { field: item.field, label: "返程", value };
    case "trip_time_scope":
    case "time_scope": return { field: item.field, label: "时段", value };
    case "activity_time_scope": return { field: item.field, label: "活动时段", value };
    case "exact_stop_count": return { field: item.field, label: "站点数", value: `${item.value} 站` };
    case "required_stop_roles": return { field: item.field, label: "站点要求", value };
    case "duration_minutes": return { field: item.field, label: "停留", value };
    case "party": return { field: item.field, label: "同行人", value };
    case "budget_per_person": return { field: item.field, label: "预算", value };
    case "max_distance_km": return { field: item.field, label: "距离", value };
    case "total_distance_km": return { field: item.field, label: "全程距离", value: value.replace(/^全程\s*/, "") };
    default: return null;
  }
}

function planningConstraintTags(items: ConstraintSummaryItem[]): PlanningConstraintTag[] {
  const byField = new Map(items.map((item) => [item.field, item]));
  const windowItem = byField.get("time_window");
  const windowValue = windowItem && windowItem.value && typeof windowItem.value === "object"
    ? windowItem.value as { start?: unknown; end?: unknown }
    : null;
  const fields = [
    "time_window", "departure_at", "return_by", "trip_time_scope", "time_scope",
    "party", "budget_per_person", "max_distance_km", "total_distance_km",
    "exact_stop_count", "required_stop_roles", "activity_time_scope", "duration_minutes",
  ];
  const tags: PlanningConstraintTag[] = [];
  for (const field of fields) {
    const item = byField.get(field);
    if (!item) continue;
    if (windowItem && field !== "time_window") {
      if (["trip_time_scope", "time_scope"].includes(field)) continue;
      if (field === "departure_at" && windowValue?.start === item.value) continue;
      if (field === "return_by" && windowValue?.end === item.value) continue;
    }
    const tag = planningConstraintTag(item);
    if (tag && !tags.some((existing) => existing.field === tag.field)) tags.push(tag);
  }
  return tags;
}

function planningWeatherTag(facts: ProviderFact[]): PlanningConstraintTag | null {
  const weather = facts.find((fact) => fact.kind === "weather");
  if (!weather || weather.kind !== "weather") return null;
  const value = [
    weather.condition,
    weather.temperature_c === null ? null : `${weather.temperature_c}°C`,
    weather.degraded ? "待确认" : weather.is_adverse || weather.precipitation_mm > 0 ? "需留意" : null,
  ].filter(Boolean).join(" · ");
  return value ? { field: "weather", label: "天气", value } : null;
}

function PlanningConstraintIcon({ field }: { field: string }) {
  if (field === "weather") return <CloudSun size={15} aria-hidden="true" />;
  if (field === "party") return <UsersRound size={15} aria-hidden="true" />;
  if (field === "budget_per_person") return <CircleDollarSign size={15} aria-hidden="true" />;
  if (field === "max_distance_km" || field === "total_distance_km") return <Route size={15} aria-hidden="true" />;
  if (["time_window", "departure_at", "return_by", "trip_time_scope", "time_scope", "activity_time_scope", "duration_minutes"].includes(field)) return <Clock3 size={15} aria-hidden="true" />;
  return <Info size={15} aria-hidden="true" />;
}

function compactRecommendationReason(reason: string, isModification: boolean): string {
  const normalized = reason.trim();
  if (!isModification) return normalized;
  const sentenceEnd = normalized.search(/[。！？]/u);
  return sentenceEnd >= 0 ? normalized.slice(0, sentenceEnd + 1) : normalized;
}

function strategyLabel(strategy: string): string {
  const labels: Record<string, string> = {
    balanced: "综合平衡",
    low_cost: "预算友好",
    low_travel: "少奔波",
    experience: "体验优先",
    family_safe: "亲子稳妥",
    weather_safe: "天气稳妥",
  };
  return labels[strategy] ?? "";
}

function providerSourceLabel(fact: ProviderFact): string {
  const labels: Record<ProviderFact["source"], string> = {
    amap_live: "高德实时",
    cache: "缓存",
    replay: "固定回放",
    mock: "可信模拟",
    local_estimate: "本地估算",
  };
  return labels[fact.source];
}

function runtimeStageLabel(stage: RuntimeDecision["stage"]): string {
  if (stage === "turn_interpreter") return "需求理解";
  if (stage === "planning_intent") return "规划结构";
  if (stage === "candidate_retrieval") return "候选召回";
  return "推荐解释";
}

function runtimeAdapterLabel(decision: RuntimeDecision): string {
  const adapterLabels: Record<string, string> = {
    demo_rule: "离线规则 Demo",
    rule_based: "规则基线",
    llm: decision.model_name ? `LLM · ${decision.model_name}` : "LLM",
    bge_hybrid: "BGE 混合召回",
    fallback: "LLM 失败后规则回退",
    bypassed: "结构化操作直达",
    not_run: "未运行",
    custom: "自定义适配器",
  };
  return adapterLabels[decision.adapter] ?? decision.adapter;
}

function ProgressMarker({ status }: { status: RunStageViewModel["status"] }) {
  if (status === "active") return <span className="run-progress-spinner" aria-hidden="true" />;
  if (status === "fallback" || status === "failed") return <CircleAlert size={14} aria-hidden="true" />;
  if (status === "waiting_input") return <Clock3 size={14} aria-hidden="true" />;
  if (status === "completed") return <Check size={14} aria-hidden="true" />;
  return <span className="run-progress-pending-dot" aria-hidden="true" />;
}

function RunProgressRows({ stages }: { stages: RunStageViewModel[] }) {
  return (
    <ol className="run-progress-list">
      {stages.map((stage) => (
        <li className={`run-progress-item ${stage.status}`} key={stage.stage}>
          <span className="run-progress-marker"><ProgressMarker status={stage.status} /></span>
          <div>
            <strong>{stage.label}</strong>
            {stage.message ? <p>{stage.message}</p> : null}
            {stage.durationMs !== undefined ? <small>{stage.durationMs} ms</small> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

function RunProgressDisclosure({ trace }: { trace?: PlanningRunTrace | null }) {
  const stages = projectRunProgress(trace?.events ?? []);
  if (!stages.length) return null;
  return (
    <details className="run-progress-disclosure">
      <summary>
        <span><CheckCircle2 size={16} />规划完成 · 查看本轮处理过程</span>
        <small>{stages.length} 个阶段</small>
        <ChevronDown size={16} className="disclosure-chevron" aria-hidden="true" />
      </summary>
      <RunProgressRows stages={stages} />
    </details>
  );
}

function RuntimeCallSummary({ decision }: { decision: RuntimeDecision }) {
  const status = decision.model_invoked
    ? `调用模型${decision.model_name ? ` · ${decision.model_name}` : ""}`
    : `未调用模型 · ${runtimeAdapterLabel(decision)}`;
  const details = [
    decision.fallback_reason,
    decision.latency_ms !== null ? `${decision.latency_ms} ms` : null,
    `尝试 ${decision.attempts} 次`,
    decision.input_tokens !== null || decision.output_tokens !== null ? `tokens ${decision.input_tokens ?? "?"}/${decision.output_tokens ?? "?"}` : null,
  ].filter(Boolean).join(" · ");
  return <article className={`evidence-row runtime-call-row ${decision.fallback_reason ? "warning" : ""}`}>
    <span className="evidence-icon"><Sparkles size={17} aria-hidden="true" /></span>
    <div>
      <strong>{runtimeStageLabel(decision.stage)}</strong>
      <p>{status}</p>
      {details ? <small>{details}</small> : null}
    </div>
  </article>;
}

function routeSourceLabel(source: string): string {
  const labels: Record<string, string> = {
    real_provider: "高德路线",
    cache: "路线缓存",
    replay: "固定回放",
    local_estimate: "本地估算",
  };
  return labels[source] ?? source;
}

function catalogSourceLabel(source: Stop["source"]): string {
  const status = { verified: "已核验", unverified: "未核验", stale: "需复核" }[source.verification_status];
  return `${source.source_name} · ${status}`;
}

function priceLabel(stop: Stop): string {
  if (stop.price_kind === "unknown") return "价格待确认";
  if (stop.price_kind === "free") return "免费";
  return `¥${stop.price}${stop.price_kind === "estimated" ? "（估算）" : ""}`;
}

function compactPlanPriceLabel(plan: Plan): string {
  if (plan.price_status === "incomplete") return `已知 ¥${plan.total_price}，其余待确认`;
  if (plan.price_status === "estimated") return `约 ¥${plan.total_price}`;
  return `¥${plan.total_price}`;
}

function planTimeRangeLabel(plan: Plan): string | null {
  const startsAt = plan.route_legs[0]?.start ?? plan.stops[0]?.start;
  const returnLeg = plan.route_legs.length > plan.stops.length
    ? plan.route_legs[plan.route_legs.length - 1]
    : undefined;
  const endsAt = returnLeg?.end ?? plan.stops[plan.stops.length - 1]?.end;
  return startsAt && endsAt ? `${startsAt}–${endsAt}` : null;
}

function totalDistance(plan: Plan): number {
  return plan.route_legs.reduce((sum, leg) => sum + leg.distance_km, 0);
}

function formatDistance(distanceKm: number): string {
  return Number.isInteger(distanceKm) ? String(distanceKm) : distanceKm.toFixed(1);
}

function safeDateTime(value: string | null): string {
  if (!value) return "未提供核验时间";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function routeModeLabel(mode: string): string {
  const normalized = mode.toLowerCase();
  if (["walk", "walking", "foot"].includes(normalized)) return "步行";
  if (["bus", "transit", "public_transit"].includes(normalized)) return "公交";
  if (["taxi", "car", "driving", "drive"].includes(normalized)) return normalized === "taxi" ? "打车" : "驾车";
  return mode || "通勤";
}

function RouteModeIcon({ mode, size = 14 }: { mode: string; size?: number }) {
  const normalized = mode.toLowerCase();
  if (["walk", "walking", "foot"].includes(normalized)) return <Footprints size={size} aria-hidden="true" />;
  if (["bus", "transit", "public_transit"].includes(normalized)) return <Bus size={size} aria-hidden="true" />;
  if (["taxi", "car", "driving", "drive"].includes(normalized)) return <Car size={size} aria-hidden="true" />;
  return <Navigation size={size} aria-hidden="true" />;
}

function planWarning(response: AgentResponse, plan: Plan): PlanWarning | null {
  return response.warnings?.find((warning) => warning.plan_id === plan.plan_id) ?? null;
}

function planSelectionBlockReason({
  response,
  planId,
  sessionId,
  activePlanVersionId,
  busy,
  restoring,
  selectionPending,
  context,
  latestResponse,
  replacementDraft,
}: {
  response: AgentResponse | null;
  planId: string | null;
  sessionId: string | null;
  activePlanVersionId: string | null;
  busy: boolean;
  restoring: boolean;
  selectionPending: boolean;
  context: PlanningContextSummary | null;
  latestResponse: AgentResponse | null;
  replacementDraft: ReplacementDraft | null;
}): string | null {
  if (restoring) return "正在恢复会话，请稍后再选择。";
  if (busy) return "规划请求正在处理中，请稍后再选择。";
  if (selectionPending) return "正在保存正式选择，请稍后。";
  if (!sessionId || !activePlanVersionId) return "当前会话没有可确认的方案版本。";
  if (!response?.plan_version_id) return "缺少方案版本归属信息；此方案仅可预览。";
  if (response.plan_version_id !== activePlanVersionId) return "此方案来自历史版本，仅可预览。";
  if (!planId || !response.plans.some((plan) => plan.plan_id === planId)) return "当前预览不属于这个方案版本。";
  if (context?.request_view === "draft") return "规划条件有未提交草稿；提交或取消草稿后再选择。";
  if (context?.plan_stale) return "规划条件已变化；请先按新条件重新规划。";
  if (context?.pending_field) return "仍有待确认的规划条件，请先完成确认。";
  if (latestResponse?.status === "needs_recovery" && latestResponse.recovery) return "有待处理的恢复操作，请先完成或取消。";
  if (replacementDraft) return "请先完成或取消当前站点替换草稿。";
  return null;
}

function planRationale(plan: Plan): string[] {
  return plan.highlights.slice(0, 3);
}

function planAdviceFor(advice: RecommendationAdvice | null | undefined, planId: string): PlanAdvice | undefined {
  return advice?.plans.find((item) => item.plan_id === planId);
}

function routeActionLabel(leg: RouteLeg, kind: "start" | "next" | "return"): string {
  const distance = formatDistance(leg.distance_km);
  if (kind === "start") return `查看出发路线：${leg.origin_name} → ${leg.destination_name} · ${distance}km · 约 ${leg.duration_minutes} 分钟`;
  if (kind === "return") return `查看返程：${leg.origin_name} → ${leg.destination_name} · ${distance}km · 约 ${leg.duration_minutes} 分钟`;
  return `查看下一程：${distance}km · 约 ${leg.duration_minutes} 分钟`;
}

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia(query).matches : false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mediaQuery = window.matchMedia(query);
    const onChange = () => setMatches(mediaQuery.matches);
    onChange();
    mediaQuery.addEventListener("change", onChange);
    return () => mediaQuery.removeEventListener("change", onChange);
  }, [query]);
  return matches;
}

const RECOVERY_BOTTLENECK_LABELS: Record<string, string> = {
  temporal_conflict: "时间条件冲突",
  schedule_capacity: "时间容量",
  distance_limit: "距离范围",
  candidate_supply: "候选地点",
  availability: "营业可用性",
  route_feasibility: "路线可行性",
  search_budget: "搜索预算",
  unknown: "未确定的限制",
};

function recoveryLifecycleFromResponse(response: AgentResponse): Pick<RecoveryLifecycle, "status" | "summary" | "fieldChanges" | "previousBottleneck" | "currentBottleneck"> {
  if (response.recovery_outcome) {
    const outcome = response.recovery_outcome;
    return {
      status: outcome.status === "still_failed" ? "still_failed" : outcome.status === "awaiting_input" ? "awaiting_input" : outcome.status,
      summary: outcome.summary,
      fieldChanges: outcome.field_changes ?? [],
      previousBottleneck: outcome.previous_bottleneck,
      currentBottleneck: outcome.current_bottleneck,
    };
  }
  if (response.recovery_resolution === "cancel_turn") {
    return { status: "cancelled", summary: "本轮已取消，原方案和已确认条件未修改。" };
  }
  if (response.recovery_resolution === "start_new_request") {
    return { status: "finished", summary: "已开始新的规划需求，上一轮条件不会继续继承。" };
  }
  if (response.recovery_resolution === "keep_current_plan") {
    return { status: "finished", summary: "已保留当前方案，原方案没有被修改。" };
  }
  if (response.recovery_resolution === "open_constraint_editor") {
    return { status: "finished", summary: "已打开条件编辑器，请确认修改后再重新规划。" };
  }
  if (response.status === "needs_recovery") {
    return { status: "still_failed", summary: "调整已应用，但当前条件下仍未找到可行方案；请从新的选项继续。" };
  }
  if (response.status === "needs_input") {
    return { status: "awaiting_input", summary: response.question?.question ?? "调整已应用，还需要补充一个条件。" };
  }
  if (response.status === "completed" && response.plans.length > 0) {
    return { status: "succeeded", summary: "调整已应用，新的方案已生成并重新核验。" };
  }
  if (response.status === "failed") {
    return {
      status: response.failure?.retryable ? "retryable" : "finished",
      summary: response.failure?.public_message ?? "调整后本轮未能完成。",
    };
  }
  return { status: "finished", summary: "恢复操作已处理。" };
}

function isCurrentRecoveryAction(
  response: AgentResponse | null,
  interaction: RecoveryChoice,
  action: RecoveryAction,
): boolean {
  const current = response?.recovery;
  return response?.status === "needs_recovery"
    && current?.interaction_id === interaction.interaction_id
    && current.request_revision === interaction.request_revision
    && current.plan_version_id === interaction.plan_version_id
    && current.actions.some((candidate) => (
      candidate.action_id === action.action_id
      && candidate.kind === action.kind
      && candidate.request_revision === action.request_revision
      && candidate.plan_version_id === action.plan_version_id
    ));
}

function App() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [recentSessions, setRecentSessions] = useState<SessionSummary[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [progressEvents, setProgressEvents] = useState<PlanningRunEvent[]>([]);
  const [restoring, setRestoring] = useState(false);
  const [error, setError] = useState("");
  const [response, setResponse] = useState<AgentResponse | null>(null);
  const [planningContext, setPlanningContext] = useState<PlanningContextSummary | null>(null);
  const [contextToast, setContextToast] = useState<string | null>(null);
  const [contextFocus, setContextFocus] = useState<ContextOpenTarget | null>(null);
  const [inspectedResponse, setInspectedResponse] = useState<AgentResponse | null>(null);
  const [viewedPlanId, setViewedPlanId] = useState<string | null>(null);
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);
  const [selectionConfirmation, setSelectionConfirmation] = useState<PlanSelectionTarget | null>(null);
  const [selectionPendingPlanId, setSelectionPendingPlanId] = useState<string | null>(null);
  const [selectionError, setSelectionError] = useState("");
  const [pendingRecoveryConfirmation, setPendingRecoveryConfirmation] = useState<PendingRecoveryConfirmation | null>(null);
  const [focusedLegIndex, setFocusedLegIndex] = useState<number | null>(null);
  const [rightTab, setRightTab] = useState<InspectorTab>("trip");
  const [showInspectorScrollTop, setShowInspectorScrollTop] = useState(false);
  const [poiNavigation, setPoiNavigation] = useState<PoiNavigation | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try { return window.localStorage.getItem("hft.sidebar.collapsed") === "true"; }
    catch { return false; }
  });
  useEffect(() => {
    try { window.localStorage.setItem("hft.sidebar.collapsed", String(sidebarCollapsed)); }
    catch { /* Layout preferences remain usable when storage is unavailable. */ }
  }, [sidebarCollapsed]);
  const [conversationPaneWidth, setConversationPaneWidth] = useState<number | null>(null);
  const [isResizingPane, setIsResizingPane] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [newRequestMode, setNewRequestMode] = useState(false);
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false);
  const isMobile = useMediaQuery("(max-width: 900px)");
  const [failedRequest, setFailedRequest] = useState<{ sessionId: string; content: string; requestId: string; conversationCommand?: ConversationCommand; clarificationReply?: ClarificationReply; recoveryAction?: RecoveryActionRequest } | null>(null);
  const [recoveryLifecycle, setRecoveryLifecycle] = useState<Record<string, RecoveryLifecycle>>({});
  const [replacementDraft, setReplacementDraft] = useState<ReplacementDraft | null>(null);
  const [replacementDraftError, setReplacementDraftError] = useState("");
  const shouldFollowConversationRef = useRef(true);
  const appShellRef = useRef<HTMLDivElement>(null);
  const paneResizeActiveRef = useRef(false);
  const paneResizePointerOffsetRef = useRef(0);
  const paneResizeShareRef = useRef(0.5);
  const conversationEndRef = useRef<HTMLDivElement>(null);
  const replacementDraftRef = useRef<HTMLElement>(null);
  const replacementFocusOriginRef = useRef<ReplacementFocusOrigin | null>(null);
  const replacementReturnFocusPendingRef = useRef(false);
  const messageSubmitTokenRef = useRef<string | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const sessionEpochRef = useRef(0);
  const activePlanVersionIdRef = useRef<string | null>(null);
  const activePlanSnapshotRef = useRef<AgentResponse | null>(null);
  const selectionInFlightRef = useRef(false);
  const recoveryActionInFlightRef = useRef(false);
  const recoveryActionTokenRef = useRef(0);
  const selectionRequestTokenRef = useRef(0);
  const poiNavigationRef = useRef<PoiNavigation | null>(null);
  const timelineRouteScrollRef = useRef<TimelineRouteScrollTarget | null>(null);
  const inspectorScrollContextRef = useRef<{
    sessionId: string | null;
    response: AgentResponse | null;
    plan: Plan | undefined;
    tab: InspectorTab;
    legIndex: number | null;
    isMobile: boolean;
  }>({ sessionId: null, response: null, plan: undefined, tab: "trip", legIndex: null, isMobile: false });
  const desktopInspectorScrollRef = useRef<HTMLElement>(null);
  const mobileInspectorScrollRef = useRef<HTMLDivElement>(null);

  const getConversationPaneLimits = useCallback(() => {
    const shell = appShellRef.current;
    if (!shell || window.innerWidth <= 900) return null;
    const available = shell.clientWidth - (sidebarCollapsed ? 64 : 244) - PANE_RESIZER_WIDTH;
    if (available <= 0) return null;
    const min = Math.min(MIN_PANE_WIDTH, available * MIN_PANE_SHARE);
    const max = Math.min(available * MAX_CHAT_PANE_SHARE, available - min);
    return { available, min, max };
  }, [sidebarCollapsed]);

  const updateConversationPaneWidth = useCallback((clientX: number) => {
    const shell = appShellRef.current;
    const limits = getConversationPaneLimits();
    if (!shell || !limits) return;
    const sidebarWidth = sidebarCollapsed ? 64 : 244;
    const dividerCenter = shell.getBoundingClientRect().left + sidebarWidth + PANE_RESIZER_WIDTH / 2;
    const requested = clientX - paneResizePointerOffsetRef.current - dividerCenter;
    const next = Math.max(limits.min, Math.min(limits.max, requested));
    paneResizeShareRef.current = next / limits.available;
    setConversationPaneWidth(Math.round(next));
  }, [getConversationPaneLimits, sidebarCollapsed]);

  const startConversationPaneResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    const bounds = getConversationPaneLimits();
    if (!bounds) return;
    event.preventDefault();
    const handle = event.currentTarget.getBoundingClientRect();
    paneResizePointerOffsetRef.current = event.clientX - (handle.left + handle.width / 2);
    paneResizeActiveRef.current = true;
    setIsResizingPane(true);
    event.currentTarget.setPointerCapture(event.pointerId);
    updateConversationPaneWidth(event.clientX);
  };

  const moveConversationPaneResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (paneResizeActiveRef.current) updateConversationPaneWidth(event.clientX);
  };

  const endConversationPaneResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    paneResizeActiveRef.current = false;
    setIsResizingPane(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  const handleConversationPaneKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const limits = getConversationPaneLimits();
    if (!limits) return;
    const current = conversationPaneWidth ?? limits.available / 2;
    let requested: number;
    if (event.key === "ArrowLeft") requested = current - 24;
    else if (event.key === "ArrowRight") requested = current + 24;
    else if (event.key === "Home") requested = limits.min;
    else if (event.key === "End") requested = limits.max;
    else return;
    event.preventDefault();
    const next = Math.max(limits.min, Math.min(limits.max, requested));
    paneResizeShareRef.current = next / limits.available;
    setConversationPaneWidth(Math.round(next));
  };

  useEffect(() => {
    const clampConversationPane = () => {
      const limits = getConversationPaneLimits();
      if (!limits) return;
      setConversationPaneWidth((current) => {
        if (current === null) return null;
        const requested = paneResizeShareRef.current * limits.available;
        const next = Math.max(limits.min, Math.min(limits.max, requested));
        paneResizeShareRef.current = next / limits.available;
        return Math.round(next);
      });
    };
    clampConversationPane();
    window.addEventListener("resize", clampConversationPane);
    return () => window.removeEventListener("resize", clampConversationPane);
  }, [getConversationPaneLimits]);

  const conversationPaneLimits = getConversationPaneLimits();
  const conversationPaneValue = conversationPaneLimits
    ? Math.round(((conversationPaneWidth ?? conversationPaneLimits.available / 2) / conversationPaneLimits.available) * 100)
    : 50;
  const conversationPaneMinValue = conversationPaneLimits
    ? Math.round((conversationPaneLimits.min / conversationPaneLimits.available) * 100)
    : 40;
  const conversationPaneMaxValue = conversationPaneLimits
    ? Math.round((conversationPaneLimits.max / conversationPaneLimits.available) * 100)
    : 65;

  useEffect(() => {
    if (!contextToast) return;
    const timeout = window.setTimeout(() => setContextToast(null), 5000);
    return () => window.clearTimeout(timeout);
  }, [contextToast]);

  function scrollInspectorToTop() {
    const host = isMobile ? mobileInspectorScrollRef.current : desktopInspectorScrollRef.current;
    if (!host) return;
    host.scrollTop = 0;
    setShowInspectorScrollTop(false);
  }

  useEffect(() => {
    if (!pendingRecoveryConfirmation) return;
    const { sessionId: capturedSessionId, interaction, action, contextRevision } = pendingRecoveryConfirmation;
    if (
      sessionId !== capturedSessionId
      || (planningContext?.request_revision ?? null) !== contextRevision
      || !isCurrentRecoveryAction(response, interaction, action)
    ) {
      setPendingRecoveryConfirmation(null);
    }
  }, [pendingRecoveryConfirmation, planningContext?.request_revision, response, sessionId]);

  function invalidateSelectionFlow() {
    selectionRequestTokenRef.current += 1;
    selectionInFlightRef.current = false;
    setSelectionConfirmation(null);
    setSelectionPendingPlanId(null);
    setSelectionError("");
  }

  const detailResponse = inspectedResponse ?? response;

  const inspectorPlan = useMemo(
    () => detailResponse?.plans.find((plan) => plan.plan_id === viewedPlanId) ?? detailResponse?.plans[0],
    [detailResponse, viewedPlanId],
  );
  poiNavigationRef.current = poiNavigation;
  inspectorScrollContextRef.current = {
    sessionId,
    response: detailResponse,
    plan: inspectorPlan,
    tab: rightTab,
    legIndex: focusedLegIndex,
    isMobile,
  };

  const workspaceContextRef = useRef<{ sessionId: string | null; response: AgentResponse | null; planId: string | null }>({
    sessionId: null,
    response: null,
    planId: null,
  });
  useLayoutEffect(() => {
    const nextContext = { sessionId, response: detailResponse, planId: inspectorPlan?.plan_id ?? null };
    const previous = workspaceContextRef.current;
    if (previous.sessionId !== nextContext.sessionId || previous.response !== nextContext.response || previous.planId !== nextContext.planId) {
      workspaceContextRef.current = nextContext;
      const scrollHost = isMobile ? mobileInspectorScrollRef.current : desktopInspectorScrollRef.current;
      if (scrollHost) scrollHost.scrollTop = 0;
      setShowInspectorScrollTop(false);
      timelineRouteScrollRef.current = null;
      setPoiNavigation(null);
      setFocusedLegIndex(null);
      setRightTab("trip");
    }
  }, [detailResponse, inspectorPlan?.plan_id, isMobile, sessionId]);
  useLayoutEffect(() => {
    const pending = timelineRouteScrollRef.current;
    if (!pending) return;
    if (pending.sessionId !== sessionId || pending.response !== detailResponse || pending.planId !== inspectorPlan?.plan_id || pending.isMobile !== isMobile) {
      timelineRouteScrollRef.current = null;
      return;
    }
    if (rightTab !== "trip") return;

    timelineRouteScrollRef.current = null;
    const scrollHost = pending.isMobile ? mobileInspectorScrollRef.current : desktopInspectorScrollRef.current;
    const target = scrollHost?.querySelector<HTMLElement>(`[data-route-leg-index="${pending.legIndex}"]`);
    if (!scrollHost || !target) return;

    const hostRect = scrollHost.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const stickyHeaderBottom = scrollHost.querySelector<HTMLElement>(".inspector-view-switcher, .inspector-poi-context")?.getBoundingClientRect().bottom;
    const visibleTop = Math.max(hostRect.top + scrollHost.clientTop, stickyHeaderBottom ?? hostRect.top);
    const visibleBottom = hostRect.bottom - scrollHost.clientTop;
    const visibleHeight = Math.max(0, visibleBottom - visibleTop);
    const targetHeight = Math.min(targetRect.height, visibleHeight);
    const desiredTop = visibleTop + Math.max(0, (visibleHeight - targetHeight) / 2);
    scrollHost.scrollTop = Math.max(0, scrollHost.scrollTop + targetRect.top - desiredTop);
  }, [detailResponse, inspectorPlan?.plan_id, isMobile, rightTab, sessionId]);
  const previousIsMobileRef = useRef(isMobile);
  useEffect(() => {
    if (previousIsMobileRef.current === isMobile) return;
    previousIsMobileRef.current = isMobile;
    setMobileDetailOpen(isMobile && Boolean(inspectorPlan));
  }, [inspectorPlan, isMobile]);

  const activeSelectedResponse = useMemo(() => {
    const activeVersionId = activePlanVersionIdRef.current;
    if (!activeVersionId || !selectedPlanId) return null;
    const candidates = [
      inspectedResponse,
      response,
      activePlanSnapshotRef.current,
      ...messages.map((message) => message.response ?? null),
    ];
    return candidates.find((candidate) => (
      candidate?.plan_version_id === activeVersionId
      && candidate.plans.some((plan) => plan.plan_id === selectedPlanId)
    )) ?? null;
  }, [inspectedResponse, messages, response, selectedPlanId]);
  const activeSelectedPlan = activeSelectedResponse?.plans.find((plan) => plan.plan_id === selectedPlanId);
  const activePoiNavigation = poiNavigation
    && poiNavigation.sessionId === sessionId
    && poiNavigation.response === detailResponse
    && poiNavigation.planVersionId === (detailResponse?.plan_version_id ?? null)
    && poiNavigation.planId === inspectorPlan?.plan_id
    && inspectorPlan?.stops[poiNavigation.stopIndex]?.resource_id === poiNavigation.resourceId
    ? poiNavigation
    : null;
  const canReplaceInspectorPlan = Boolean(
    inspectorPlan
    && sessionId
    && activePlanVersionIdRef.current
    && detailResponse?.plan_version_id === activePlanVersionIdRef.current
    && selectedPlanId === inspectorPlan.plan_id
    && activeSelectedPlan?.plan_id === inspectorPlan.plan_id
    && !loading
    && !restoring
    && !selectionPendingPlanId
    && !selectionInFlightRef.current
    && !selectionConfirmation
    && !replacementDraft
    && !failedRequest
    && !response?.question
    && !response?.recovery
    && (!planningContext || (planningContext.request_view === "committed" && !planningContext.plan_stale && !planningContext.pending_field)),
  );

  useEffect(() => {
    if (!replacementDraft || loading || restoring) return;
    const currentPlan = activePlanSnapshotRef.current?.plans.find((plan) => plan.plan_id === replacementDraft.planId);
    const currentStop = currentPlan?.stops[replacementDraft.stopIndex];
    const invalid = replacementDraft.sessionId !== sessionIdRef.current
      || replacementDraft.planVersionId !== activePlanVersionIdRef.current
      || replacementDraft.planId !== selectedPlanId
      || replacementDraft.planId !== viewedPlanId
      || activeSelectedPlan?.plan_id !== replacementDraft.planId
      || detailResponse?.plan_version_id !== replacementDraft.planVersionId
      || currentStop?.resource_id !== replacementDraft.resourceId
      || currentStop?.role !== replacementDraft.role
      || replacementDraft.contextRevision !== (planningContext?.request_revision ?? null)
      || Boolean(planningContext && (planningContext.request_view !== "committed" || planningContext.plan_stale || planningContext.pending_field))
      || Boolean(response?.question || response?.recovery || pendingRecoveryConfirmation || selectionConfirmation || selectionInFlightRef.current);
    if (!invalid) return;
    setReplacementDraft(null);
    setInput("");
    setReplacementDraftError("原方案或规划条件已变化，已取消这份替换草稿。请重新打开当前正式方案的站点。");
  }, [activeSelectedPlan?.plan_id, detailResponse?.plan_version_id, loading, pendingRecoveryConfirmation, planningContext, replacementDraft, response?.question, response?.recovery, restoring, selectedPlanId, selectionConfirmation, viewedPlanId]);

  useLayoutEffect(() => {
    if (!replacementDraft || !isMobile || mobileDetailOpen) return;
    const target = replacementDraftRef.current;
    if (!target) return;
    target.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
    target.focus({ preventScroll: true });
  }, [isMobile, mobileDetailOpen, replacementDraft]);

  useLayoutEffect(() => {
    if (!replacementReturnFocusPendingRef.current || replacementDraft) return;
    const origin = replacementFocusOriginRef.current;
    if (!origin) {
      replacementReturnFocusPendingRef.current = false;
      return;
    }
    if (origin.wasMobile) {
      if (!mobileDetailOpen) {
        setMobileDetailOpen(true);
        return;
      }
      let firstFrame = 0;
      let secondFrame = 0;
      firstFrame = requestAnimationFrame(() => {
        secondFrame = requestAnimationFrame(() => {
          if (!replacementReturnFocusPendingRef.current) return;
          const target = mobileInspectorScrollRef.current?.querySelector<HTMLElement>(`[data-replacement-stop-index="${origin.stopIndex}"]`);
          target?.focus({ preventScroll: true });
          replacementReturnFocusPendingRef.current = false;
        });
      });
      return () => {
        cancelAnimationFrame(firstFrame);
        cancelAnimationFrame(secondFrame);
      };
    } else {
      origin.triggerElement?.focus({ preventScroll: true });
    }
    replacementReturnFocusPendingRef.current = false;
  }, [mobileDetailOpen, replacementDraft]);

  const openPoiDetail = useCallback((stopIndex: number, triggerElement: HTMLElement | null = null) => {
    const context = inspectorScrollContextRef.current;
    const plan = context.plan;
    const responseForPlan = context.response;
    const stop = plan?.stops[stopIndex];
    if (!plan || !responseForPlan || !stop) return;
    const scrollHost = context.isMobile ? mobileInspectorScrollRef.current : desktopInspectorScrollRef.current;
    setPoiNavigation({
      sessionId: context.sessionId,
      planVersionId: responseForPlan.plan_version_id ?? null,
      response: responseForPlan,
      planId: plan.plan_id,
      stopIndex,
      resourceId: stop.resource_id,
      returnTab: context.tab,
      returnScrollTop: scrollHost?.scrollTop ?? 0,
      returnLegIndex: context.legIndex,
      returnFocusElement: triggerElement,
    });
    requestAnimationFrame(() => {
      const current = inspectorScrollContextRef.current;
      const activeHost = current.isMobile ? mobileInspectorScrollRef.current : desktopInspectorScrollRef.current;
      if (activeHost) activeHost.scrollTop = 0;
    });
  }, []);

  const closePoiDetail = useCallback(() => {
    const navigation = poiNavigationRef.current;
    if (!navigation) return;
    poiNavigationRef.current = null;
    setPoiNavigation(null);
    setRightTab(navigation.returnTab);
    setFocusedLegIndex(navigation.returnLegIndex);
    requestAnimationFrame(() => {
      const current = inspectorScrollContextRef.current;
      const activeHost = current.isMobile ? mobileInspectorScrollRef.current : desktopInspectorScrollRef.current;
      if (activeHost) activeHost.scrollTop = navigation.returnScrollTop;
      requestAnimationFrame(() => {
        let focusTarget = navigation.returnFocusElement;
        if (!focusTarget?.isConnected) {
          focusTarget = activeHost?.querySelector<HTMLElement>(`[data-inspector-stop-index="${navigation.stopIndex}"]`) ?? null;
        }
        focusTarget?.focus({ preventScroll: true });
        if (activeHost) activeHost.scrollTop = navigation.returnScrollTop;
      });
    });
  }, []);
  const inspectorSelectionBlockReason = planSelectionBlockReason({
    response: detailResponse,
    planId: inspectorPlan?.plan_id ?? null,
    sessionId,
    activePlanVersionId: activePlanVersionIdRef.current,
    busy: loading,
    restoring,
    selectionPending: selectionPendingPlanId !== null,
    context: planningContext,
    latestResponse: response,
    replacementDraft,
  });

  const refreshRecentSessions = useCallback(async () => {
    try {
      setRecentSessions(await listSessions(20));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "历史会话加载失败");
    }
  }, []);

  const openSession = useCallback(async (nextSessionId: string, navigate = true) => {
    invalidateSelectionFlow();
    setContextToast(null);
    setShowInspectorScrollTop(false);
    const restoreEpoch = ++sessionEpochRef.current;
    messageSubmitTokenRef.current = null;
    recoveryActionTokenRef.current += 1;
    recoveryActionInFlightRef.current = false;
    sessionIdRef.current = null;
    activePlanVersionIdRef.current = null;
    activePlanSnapshotRef.current = null;
    setPendingRecoveryConfirmation(null);
    setLoading(false);
    setRestoring(true);
    setError("");
    setFailedRequest(null);
    setRecoveryLifecycle({});
    try {
      const view = await getSession(nextSessionId);
      if (restoreEpoch !== sessionEpochRef.current) return;
      const restoredResponses = responsesFromSession(view);
      const restoredResponse = restoredResponses[restoredResponses.length - 1] ?? null;
      const restoredActiveResponse = activePlanResponse(view, restoredResponses);
      const restoredSelectedId = view.selected_plan_id ?? null;
      sessionIdRef.current = view.session_id;
      activePlanVersionIdRef.current = view.active_plan_version_id ?? null;
      activePlanSnapshotRef.current = restoredActiveResponse;
      setSessionId(view.session_id);
      setMessages(attachResponsesToMessages(view.messages, restoredResponses));
      const restoredLifecycle = Object.fromEntries(
        restoredResponses
          .filter((item) => item.recovery_outcome?.interaction_id)
          .map((item) => [
            item.recovery_outcome!.interaction_id!,
            {
              actionId: item.recovery_outcome!.action_id,
              label: item.recovery_outcome!.label,
              ...recoveryLifecycleFromResponse(item),
            },
          ]),
      );
      setRecoveryLifecycle(restoredLifecycle);
      setProgressEvents([]);
      setResponse(restoredResponse);
      setPlanningContext(view.planning_context ?? restoredResponse?.planning_context ?? null);
      setInspectedResponse(restoredActiveResponse ?? restoredResponse);
      setSelectedPlanId(restoredSelectedId);
      setReplacementDraft(null);
      setReplacementDraftError("");
      replacementFocusOriginRef.current = null;
      replacementReturnFocusPendingRef.current = false;
      setNewRequestMode(false);
      const restoredViewedId = restoredSelectedId && restoredActiveResponse?.plans.some(
        (plan) => plan.plan_id === restoredSelectedId,
      )
        ? restoredSelectedId
        : restoredActiveResponse?.plans[0]?.plan_id ?? null;
      setViewedPlanId(restoredViewedId);
      setFocusedLegIndex(null);
      setRightTab("trip");
      setMobileDetailOpen(false);
      if (navigate && sessionIdFromUrl() !== view.session_id) updateSessionUrl(view.session_id);
    } catch (reason) {
      if (restoreEpoch !== sessionEpochRef.current) return;
      sessionIdRef.current = null;
      activePlanVersionIdRef.current = null;
      activePlanSnapshotRef.current = null;
      setSessionId(null);
      setMessages([]);
      setProgressEvents([]);
      setResponse(null);
      setPlanningContext(null);
      setInspectedResponse(null);
      setSelectedPlanId(null);
      setViewedPlanId(null);
      setReplacementDraft(null);
      setReplacementDraftError("");
      setRecoveryLifecycle({});
      setNewRequestMode(false);
      setFocusedLegIndex(null);
      if (sessionIdFromUrl() === nextSessionId) updateSessionUrl(null, true);
      setError(reason instanceof Error ? reason.message : "会话恢复失败");
    } finally {
      if (restoreEpoch === sessionEpochRef.current) {
        setRestoring(false);
        setHistoryOpen(false);
      }
    }
  }, []);

  useEffect(() => {
    void refreshRecentSessions();
    const initialSessionId = sessionIdFromUrl();
    if (initialSessionId) void openSession(initialSessionId, false);
    const handlePopState = () => {
      const nextSessionId = sessionIdFromUrl();
      if (nextSessionId) void openSession(nextSessionId, false);
      else {
        invalidateSelectionFlow();
        setContextToast(null);
        setShowInspectorScrollTop(false);
        sessionEpochRef.current += 1;
        messageSubmitTokenRef.current = null;
        recoveryActionTokenRef.current += 1;
        recoveryActionInFlightRef.current = false;
        sessionIdRef.current = null;
        activePlanVersionIdRef.current = null;
        activePlanSnapshotRef.current = null;
        setPendingRecoveryConfirmation(null);
        setLoading(false);
        setRestoring(false);
        setSessionId(null);
        setMessages([]);
        setProgressEvents([]);
        setResponse(null);
        setPlanningContext(null);
        setInspectedResponse(null);
        setSelectedPlanId(null);
        setViewedPlanId(null);
        setReplacementDraft(null);
        setReplacementDraftError("");
        setRecoveryLifecycle({});
        setNewRequestMode(false);
        setFocusedLegIndex(null);
        setFailedRequest(null);
        setError("");
      }
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, [openSession, refreshRecentSessions]);

  useEffect(() => {
    if (restoring || !shouldFollowConversationRef.current) return;
    conversationEndRef.current?.scrollIntoView?.({ block: "end", behavior: loading ? "smooth" : "auto" });
  }, [loading, messages, restoring]);

  async function submit(content: string, conversationCommand?: ConversationCommand, clarificationReply?: ClarificationReply, recoveryAction?: RecoveryActionRequest) {
    const trimmed = content.trim() || (recoveryAction ? "选择恢复操作" :
      replacementDraft
        ? `更换第 ${replacementDraft.stopIndex + 1} 站`
        : ""
    );
    if (!trimmed || loading || restoring || messageSubmitTokenRef.current) return;
    const activeSessionAtStart = sessionIdRef.current ?? sessionId;
    const effectiveClarificationReply = clarificationReply ?? (
      response?.question?.clarification_id && !conversationCommand && !recoveryAction
        ? {
          clarification_id: response.question.clarification_id,
          request_revision: response.question.request_revision,
          action: newRequestMode ? "new_request" : "answer",
            value: trimmed,
          } satisfies ClarificationReply
        : undefined
    );
    const isRetry = Boolean(
      activeSessionAtStart
      && failedRequest?.sessionId === activeSessionAtStart
      && failedRequest.content === trimmed
      && JSON.stringify(failedRequest.conversationCommand ?? null)
        === JSON.stringify(conversationCommand ?? null)
      && JSON.stringify(failedRequest.clarificationReply ?? null)
        === JSON.stringify(effectiveClarificationReply ?? null)
      && JSON.stringify(failedRequest.recoveryAction ?? null)
        === JSON.stringify(recoveryAction ?? null)
    );
    if (conversationCommand?.operation === "replace") {
      const target = conversationCommand.target;
      const activeSnapshot = activePlanSnapshotRef.current;
      const activePlan = activeSnapshot?.plans.find((plan) => plan.plan_id === conversationCommand.base_plan_id);
      const activeStop = activePlan?.stops[target.stop_index];
      const draftMatches = replacementDraft
        ? replacementDraft.sessionId === activeSessionAtStart
          && replacementDraft.planVersionId === conversationCommand.base_plan_version_id
          && replacementDraft.planId === conversationCommand.base_plan_id
          && replacementDraft.stopIndex === target.stop_index
          && replacementDraft.resourceId === target.resource_id
          && replacementDraft.role === target.role
          && replacementDraft.contextRevision === (planningContext?.request_revision ?? null)
        : isRetry;
      const validTarget = Boolean(
        activeSessionAtStart
        && conversationCommand.base_plan_version_id
        && conversationCommand.base_plan_id
        && conversationCommand.base_plan_version_id === activePlanVersionIdRef.current
        && selectedPlanId === conversationCommand.base_plan_id
        && viewedPlanId === conversationCommand.base_plan_id
        && activeSnapshot?.plan_version_id === conversationCommand.base_plan_version_id
        && activeStop?.resource_id === target.resource_id
        && activeStop.role === target.role
        && draftMatches
        && (!planningContext || (planningContext.request_view === "committed" && !planningContext.plan_stale && !planningContext.pending_field))
        && !response?.question
        && !response?.recovery
        && !pendingRecoveryConfirmation
        && !selectionConfirmation
        && !selectionInFlightRef.current
      );
      if (!validTarget) {
        setError("替换目标或方案条件已变化，本次未提交。请重新打开当前正式方案中的站点。");
        return;
      }
    }
    const requestEpoch = sessionEpochRef.current;
    invalidateSelectionFlow();
    const recoveryInteractionId = recoveryAction
      ? response?.recovery?.interaction_id ?? null
      : null;
    const recoveryActionLabel = recoveryAction
      ? response?.recovery?.actions.find((action) => action.action_id === recoveryAction.action_id)?.label
        ?? recoveryAction.action_id
      : null;
    if (recoveryAction && recoveryInteractionId) {
      setRecoveryLifecycle((current) => ({
        ...current,
        [recoveryInteractionId]: {
          actionId: recoveryAction.action_id,
          label: recoveryActionLabel ?? recoveryAction.action_id,
          status: "submitted",
          summary: "正在重新校验条件并规划方案……",
        },
      }));
    }
    const submitToken = crypto.randomUUID();
    messageSubmitTokenRef.current = submitToken;
    shouldFollowConversationRef.current = true;
    setLoading(true);
    setProgressEvents([]);
    setError("");
    setInput("");
    let activeSession = activeSessionAtStart;
    try {
      if (!activeSession) {
        activeSession = await createSession();
        if (requestEpoch !== sessionEpochRef.current) return;
        sessionIdRef.current = activeSession;
        activePlanVersionIdRef.current = null;
        activePlanSnapshotRef.current = null;
        setSessionId(activeSession);
        updateSessionUrl(activeSession);
      }
      const requestId = isRetry && failedRequest ? failedRequest.requestId : crypto.randomUUID();
      if (!isRetry && !recoveryAction) setMessages((current) => [...current, { id: crypto.randomUUID(), role: "user", content: trimmed }]);
      setFailedRequest({ sessionId: activeSession, content: trimmed, requestId, conversationCommand, clarificationReply: effectiveClarificationReply, recoveryAction });
      const nextResponse = await sendMessageStream(
        activeSession,
        trimmed,
        requestId,
        (event) => {
          if (requestEpoch !== sessionEpochRef.current || sessionIdRef.current !== activeSession) return;
          setProgressEvents((current) => {
            if (requestEpoch !== sessionEpochRef.current || sessionIdRef.current !== activeSession) return current;
            const next = current.filter((item) => item.sequence !== event.sequence);
            return [...next, event].sort((left, right) => left.sequence - right.sequence);
          });
        },
        conversationCommand,
        effectiveClarificationReply,
        recoveryAction,
      );
      if (requestEpoch !== sessionEpochRef.current || sessionIdRef.current !== activeSession) return;
      if (nextResponse.status === "failed" && nextResponse.failure?.retryable) {
        setFailedRequest({ sessionId: activeSession, content: trimmed, requestId, conversationCommand, clarificationReply: effectiveClarificationReply, recoveryAction });
      } else {
        setFailedRequest(null);
      }
      if (recoveryAction && recoveryInteractionId) {
        setRecoveryLifecycle((current) => ({
          ...current,
          [recoveryInteractionId]: {
            actionId: recoveryAction.action_id,
            label: recoveryActionLabel ?? recoveryAction.action_id,
            ...recoveryLifecycleFromResponse(nextResponse),
          },
        }));
      }
      setNewRequestMode(nextResponse.recovery_resolution === "start_new_request");
      if (nextResponse.recovery_resolution === "open_constraint_editor") setContextFocus("all");
      setResponse(nextResponse);
      setPlanningContext(nextResponse.planning_context ?? null);
      if (conversationCommand?.operation === "replace") {
        setReplacementDraft(null);
        setReplacementDraftError("");
        replacementFocusOriginRef.current = null;
      }
      if (nextResponse.recovery_resolution === "start_new_request") {
        activePlanVersionIdRef.current = null;
        activePlanSnapshotRef.current = null;
        setInspectedResponse(nextResponse);
        setSelectedPlanId(null);
        setViewedPlanId(null);
        setFocusedLegIndex(null);
        setReplacementDraft(null);
        setRightTab("trip");
      } else if (nextResponse.plans.length) {
        activePlanVersionIdRef.current = nextResponse.plan_version_id ?? null;
        activePlanSnapshotRef.current = nextResponse;
        setInspectedResponse(nextResponse);
        setSelectedPlanId(null);
        setReplacementDraft(null);
        setViewedPlanId(nextResponse.plans[0].plan_id);
        setFocusedLegIndex(null);
        setRightTab("trip");
      } else {
        setInspectedResponse((current) => current?.plans.length ? current : nextResponse);
      }
      const assistantText = nextResponse.question?.question ?? nextResponse.reply ?? nextResponse.conflict?.message;
      if (assistantText || nextResponse.plans.length || nextResponse.conflict) {
        setMessages((current) => [...current, {
          id: crypto.randomUUID(), role: "assistant", content: assistantText ?? "", response: nextResponse,
        }]);
      }
      await refreshRecentSessions();
    } catch (reason) {
      if (requestEpoch !== sessionEpochRef.current || (activeSession && sessionIdRef.current !== activeSession)) return;
      setError(reason instanceof Error ? reason.message : "请求失败，请稍后重试");
      if (recoveryAction && recoveryInteractionId) {
        setRecoveryLifecycle((current) => ({
          ...current,
          [recoveryInteractionId]: {
            actionId: recoveryAction.action_id,
            label: recoveryActionLabel ?? recoveryAction.action_id,
            status: "retryable",
            summary: "这次提交没有完成，当前恢复选项仍可重试。",
          },
        }));
      }
      if (activeSession) await refreshRecentSessions();
    } finally {
      if (messageSubmitTokenRef.current === submitToken) messageSubmitTokenRef.current = null;
      if (requestEpoch === sessionEpochRef.current) {
        setLoading(false);
        setProgressEvents([]);
      }
    }
  }

  async function updateContext(patch: PlanningContextPatch) {
    if (loading || restoring) return;
    const requestEpoch = sessionEpochRef.current;
    invalidateSelectionFlow();
    setLoading(true);
    setError("");
    let activeSession = sessionIdRef.current ?? sessionId;
    const isCurrentRequest = () => requestEpoch === sessionEpochRef.current
      && (!activeSession || sessionIdRef.current === activeSession);
    try {
      if (!activeSession) {
        activeSession = await createSession();
        if (requestEpoch !== sessionEpochRef.current) return;
        sessionIdRef.current = activeSession;
        activePlanVersionIdRef.current = null;
        activePlanSnapshotRef.current = null;
        setSessionId(activeSession);
        updateSessionUrl(activeSession);
      }
      const nextResponse = await updatePlanningContext(activeSession, patch, crypto.randomUUID());
      if (!isCurrentRequest()) return;
      setFailedRequest(null);
      setNewRequestMode(false);
      if (nextResponse.status === "context_saved") {
        const savedContext = nextResponse.planning_context ?? null;
        setPlanningContext(savedContext);
        setContextToast(savedContext?.has_active_plan && savedContext.plan_stale
          ? "条件已保存。当前预览仍是旧版本，建议重新规划。"
          : "条件已保存。");
        await refreshRecentSessions();
        return;
      }
      setResponse(nextResponse);
      setPlanningContext(nextResponse.planning_context ?? null);
      if (nextResponse.plans.length) {
        activePlanVersionIdRef.current = nextResponse.plan_version_id ?? null;
        activePlanSnapshotRef.current = nextResponse;
        setInspectedResponse(nextResponse);
        setSelectedPlanId(null);
        setReplacementDraft(null);
        setViewedPlanId(nextResponse.plans[0].plan_id);
        setFocusedLegIndex(null);
        setRightTab("trip");
      } else {
        setInspectedResponse((current) => current?.plans.length ? current : nextResponse);
      }
      const assistantText = nextResponse.question?.question ?? nextResponse.reply ?? nextResponse.conflict?.message;
      if (assistantText || nextResponse.plans.length || nextResponse.conflict) {
        setMessages((current) => [...current, {
          id: crypto.randomUUID(), role: "assistant", content: assistantText ?? "", response: nextResponse,
        }]);
      }
      await refreshRecentSessions();
    } catch (reason) {
      if (!isCurrentRequest()) return;
      const message = reason instanceof Error ? reason.message : "规划条件更新失败";
      setError(message);
      throw new Error(message);
    } finally {
      if (isCurrentRequest()) setLoading(false);
    }
  }

  async function replanContext() {
    if (loading || restoring) return;
    const activeSession = sessionIdRef.current ?? sessionId;
    if (!activeSession) throw new Error("请先保存规划条件");
    const requestEpoch = sessionEpochRef.current;
    const isCurrentRequest = () => requestEpoch === sessionEpochRef.current
      && sessionIdRef.current === activeSession;
    invalidateSelectionFlow();
    setLoading(true);
    setError("");
    try {
      const nextResponse = await replanPlanningContext(activeSession, crypto.randomUUID());
      if (!isCurrentRequest()) return;
      setFailedRequest(null);
      setResponse(nextResponse);
      setPlanningContext(nextResponse.planning_context ?? null);
      if (nextResponse.plans.length) {
        activePlanVersionIdRef.current = nextResponse.plan_version_id ?? null;
        activePlanSnapshotRef.current = nextResponse;
        setInspectedResponse(nextResponse);
        setSelectedPlanId(null);
        setReplacementDraft(null);
        setViewedPlanId(nextResponse.plans[0].plan_id);
        setFocusedLegIndex(null);
        setRightTab("trip");
      }
      const assistantText = nextResponse.question?.question ?? nextResponse.reply ?? nextResponse.conflict?.message;
      if (assistantText || nextResponse.plans.length || nextResponse.conflict) {
        setMessages((current) => [...current, {
          id: crypto.randomUUID(), role: "assistant", content: assistantText ?? "", response: nextResponse,
        }]);
      }
      await refreshRecentSessions();
    } catch (reason) {
      if (!isCurrentRequest()) return;
      const message = reason instanceof Error ? reason.message : "重新规划失败，请稍后重试";
      setError(message);
      throw new Error(message);
    } finally {
      if (isCurrentRequest()) setLoading(false);
    }
  }

  function openContextForClarification(field: string | null | undefined) {
    const section = clarificationSection(field);
    if (section) setContextFocus(section);
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const command = replacementDraft
      ? replacementCommandFromDraft(replacementDraft, replacementDraft.criteria, input)
      : undefined;
    void submit(input, command);
  }

  function onClarificationOption(option: ClarificationOption, question: NonNullable<AgentResponse["question"]>, answerValue: string | null = null) {
    const currentQuestion = response?.question;
    const isDatePickerAnswer = question.field === "date" && option.id === "answer-date" && option.action === "answer" && Boolean(answerValue);
    const optionIsCurrent = isDatePickerAnswer || currentQuestion?.options?.some((currentOption) => (
      currentOption.id === option.id && currentOption.action === option.action
    ));
    if (
      !question.clarification_id
      || loading
      || restoring
      || newRequestMode
      || response?.status !== "needs_input"
      || currentQuestion?.clarification_id !== question.clarification_id
      || currentQuestion.request_revision !== question.request_revision
      || !optionIsCurrent
    ) return;
    if (option.action === "new_request") {
      setNewRequestMode(true);
      setInput("");
      return;
    }
    void submit(
      option.label,
      undefined,
      {
        clarification_id: question.clarification_id,
        request_revision: question.request_revision,
        action: option.action,
        value: answerValue,
      },
    );
  }

  function onRecoveryAction(
    action: RecoveryAction,
    interaction: RecoveryChoice,
    fieldValue?: string,
  ) {
    if (loading || restoring || recoveryActionInFlightRef.current || pendingRecoveryConfirmation) return;
    if (!isCurrentRecoveryAction(response, interaction, action)) {
      setError("这项恢复建议已过期，请使用当前状态继续。");
      return;
    }
    if (action.kind === "apply_request_patch") {
      const activeSessionId = sessionIdRef.current ?? sessionId;
      const changes = recoveryPatchChanges(action, planningContext);
      if (!activeSessionId || !changes.length) {
        setError("服务器返回的恢复提案暂时无法安全展示，尚未提交。");
        return;
      }
      setError("");
      setPendingRecoveryConfirmation({
        sessionId: activeSessionId,
        interaction,
        action,
        contextRevision: planningContext?.request_revision ?? null,
        context: planningContext,
      });
      return;
    }
    dispatchRecoveryAction(action, interaction, fieldValue);
  }

  function dispatchRecoveryAction(action: RecoveryAction, interaction: RecoveryChoice, fieldValue?: string) {
    if (loading || restoring || recoveryActionInFlightRef.current) return;
    const actionToken = ++recoveryActionTokenRef.current;
    recoveryActionInFlightRef.current = true;
    void submit(action.label, undefined, undefined, {
      interaction_id: interaction.interaction_id,
      action_id: action.action_id,
      request_revision: action.request_revision,
      plan_version_id: action.plan_version_id,
      ...(fieldValue !== undefined ? { field_value: fieldValue } : {}),
    }).finally(() => {
      if (recoveryActionTokenRef.current === actionToken) recoveryActionInFlightRef.current = false;
    });
  }

  function confirmRecoveryProposal() {
    const pending = pendingRecoveryConfirmation;
    if (!pending || loading || restoring || recoveryActionInFlightRef.current) return;
    if (
      sessionIdRef.current !== pending.sessionId
      || sessionId !== pending.sessionId
      || (planningContext?.request_revision ?? null) !== pending.contextRevision
      || !isCurrentRecoveryAction(response, pending.interaction, pending.action)
    ) {
      setPendingRecoveryConfirmation(null);
      setError("当前条件或恢复建议已变化，请重新打开最新的建议。");
      return;
    }
    const actionToken = ++recoveryActionTokenRef.current;
    recoveryActionInFlightRef.current = true;
    setPendingRecoveryConfirmation(null);
    void submit(pending.action.label, undefined, undefined, {
      interaction_id: pending.interaction.interaction_id,
      action_id: pending.action.action_id,
      request_revision: pending.action.request_revision,
      plan_version_id: pending.action.plan_version_id,
    }).finally(() => {
      if (recoveryActionTokenRef.current === actionToken) recoveryActionInFlightRef.current = false;
    });
  }

  function resetSession() {
    invalidateSelectionFlow();
    setContextToast(null);
    setShowInspectorScrollTop(false);
    sessionEpochRef.current += 1;
    messageSubmitTokenRef.current = null;
    recoveryActionTokenRef.current += 1;
    recoveryActionInFlightRef.current = false;
    sessionIdRef.current = null;
    activePlanVersionIdRef.current = null;
    activePlanSnapshotRef.current = null;
    setSessionId(null);
    setMessages([]);
    setProgressEvents([]);
    setResponse(null);
    setPlanningContext(null);
    setContextFocus(null);
    setInspectedResponse(null);
    setSelectedPlanId(null);
    setViewedPlanId(null);
    setReplacementDraft(null);
    setReplacementDraftError("");
    replacementFocusOriginRef.current = null;
    replacementReturnFocusPendingRef.current = false;
    setPendingRecoveryConfirmation(null);
    setRecoveryLifecycle({});
    setFocusedLegIndex(null);
    setLoading(false);
    setFailedRequest(null);
    setError("");
    setHistoryOpen(false);
    updateSessionUrl(null);
  }

  async function renameHistorySession(targetSessionId: string, title: string) {
    await renameSession(targetSessionId, title);
    setRecentSessions((current) => current.map((item) => item.session_id === targetSessionId ? { ...item, title } : item));
  }

  async function deleteHistorySession(targetSessionId: string) {
    await deleteSession(targetSessionId);
    setRecentSessions((current) => current.filter((item) => item.session_id !== targetSessionId));
    if (sessionIdRef.current === targetSessionId) resetSession();
  }

  const openRouteOnMap = useCallback((legIndex: number) => {
    timelineRouteScrollRef.current = null;
    setFocusedLegIndex(legIndex);
    setRightTab("map");
  }, []);

  const focusRouteOnMap = useCallback((legIndex: number) => {
    setFocusedLegIndex(legIndex);
  }, []);

  const showRouteInTimeline = useCallback((legIndex: number) => {
    const context = inspectorScrollContextRef.current;
    timelineRouteScrollRef.current = {
      sessionId: context.sessionId,
      response: context.response,
      planId: context.plan?.plan_id ?? null,
      legIndex,
      isMobile: context.isMobile,
    };
    setFocusedLegIndex(legIndex);
    setRightTab("trip");
  }, []);

  const viewPlan = useCallback((targetResponse: AgentResponse, planId: string) => {
    timelineRouteScrollRef.current = null;
    setInspectedResponse(targetResponse);
    setViewedPlanId(planId);
    setPoiNavigation(null);
    setRightTab("trip");
    setFocusedLegIndex(null);
    setSelectionError("");
  }, []);

  const openPlanDetails = useCallback((targetResponse: AgentResponse, planId: string) => {
    viewPlan(targetResponse, planId);
    setRightTab("trip");
    if (isMobile) setMobileDetailOpen(true);
  }, [isMobile, viewPlan]);

  const selectConfirmedPlan = useCallback(async (target: PlanSelectionTarget) => {
    const targetResponse = detailResponse;
    const blockReason = planSelectionBlockReason({
      response: targetResponse,
      planId: target.planId,
      sessionId,
      activePlanVersionId: activePlanVersionIdRef.current,
      busy: loading,
      restoring,
      selectionPending: selectionInFlightRef.current,
      context: planningContext,
      latestResponse: response,
      replacementDraft,
    });
    if (
      blockReason
      || target.sessionId !== sessionIdRef.current
      || target.planVersionId !== activePlanVersionIdRef.current
      || targetResponse?.plan_version_id !== target.planVersionId
    ) {
      setSelectionConfirmation(null);
      const message = blockReason ?? "方案所在会话或版本已变化，请重新预览后选择。";
      setSelectionError(message);
      setError(message);
      return;
    }
    if (!targetResponse) return;
    if (selectionInFlightRef.current) return;
    selectionInFlightRef.current = true;
    const requestToken = ++selectionRequestTokenRef.current;
    setSelectionConfirmation(null);
    setSelectionPendingPlanId(target.planId);
    setSelectionError("");
    setError("");
    try {
      const selection = await selectPlan(target.sessionId, target.planId);
      if (requestToken !== selectionRequestTokenRef.current) return;
      if (
        sessionIdRef.current !== target.sessionId
        || activePlanVersionIdRef.current !== target.planVersionId
      ) return;
      if (selection.active_plan_version_id !== target.planVersionId) {
        const message = "方案版本已变化，正式选择未更新；请重新预览当前版本。";
        setSelectionError(message);
        setError(message);
        return;
      }
      setSelectedPlanId(selection.selected_plan_id);
      if (selection.selected_plan_id && targetResponse.plans.some((plan) => plan.plan_id === selection.selected_plan_id)) {
        setViewedPlanId(selection.selected_plan_id);
      }
    } catch (reason) {
      if (
        requestToken === selectionRequestTokenRef.current
        && sessionIdRef.current === target.sessionId
        && activePlanVersionIdRef.current === target.planVersionId
      ) {
        const message = reason instanceof Error ? reason.message : "选择方案失败，请稍后重试";
        setSelectionError(message);
        setError(message);
      }
    } finally {
      if (requestToken === selectionRequestTokenRef.current) {
        selectionInFlightRef.current = false;
        setSelectionPendingPlanId(null);
      }
    }
  }, [detailResponse, loading, planningContext, replacementDraft, response, restoring, sessionId]);

  const requestPlanSelection = useCallback((planId: string) => {
    const candidate = detailResponse?.plans.find((plan) => plan.plan_id === planId);
    const blockReason = planSelectionBlockReason({
      response: detailResponse,
      planId,
      sessionId,
      activePlanVersionId: activePlanVersionIdRef.current,
      busy: loading,
      restoring,
      selectionPending: selectionInFlightRef.current,
      context: planningContext,
      latestResponse: response,
      replacementDraft,
    });
    if (blockReason || !candidate) {
      const message = blockReason ?? "当前没有可选择的方案。";
      setSelectionError(message);
      setError(message);
      return;
    }
    const target = {
      sessionId: sessionIdRef.current!,
      planVersionId: activePlanVersionIdRef.current!,
      planId,
      planTitle: candidate.title,
    };
    if (selectedPlanId && selectedPlanId !== planId) {
      setSelectionConfirmation(target);
      return;
    }
    void selectConfirmedPlan(target);
  }, [detailResponse, loading, planningContext, replacementDraft, response, restoring, selectConfirmedPlan, selectedPlanId, sessionId]);

  const startReplacement = useCallback((plan: Plan, stopIndex: number, triggerElement: HTMLElement | null = null) => {
    const requestSessionId = sessionIdRef.current;
    const requestVersionId = activePlanVersionIdRef.current;
    const stop = plan.stops[stopIndex];
    const canModify = Boolean(
      requestSessionId
      && requestVersionId
      && detailResponse?.plan_version_id === requestVersionId
      && selectedPlanId === plan.plan_id
      && activeSelectedPlan?.plan_id === plan.plan_id
      && !loading
      && !restoring
      && !selectionPendingPlanId
      && !selectionInFlightRef.current
      && !selectionConfirmation
      && !replacementDraft
      && !failedRequest
      && !response?.question
      && !response?.recovery
      && (!planningContext || (planningContext.request_view === "committed" && !planningContext.plan_stale && !planningContext.pending_field))
      && stop,
    );
    if (!canModify || !requestSessionId || !requestVersionId || !stop) {
      setError("请先选择当前方案，再修改其中的站点。");
      return;
    }
    setError("");
    setReplacementDraftError("");
    replacementFocusOriginRef.current = { stopIndex, wasMobile: isMobile, triggerElement };
    replacementReturnFocusPendingRef.current = false;
    setReplacementDraft({
      sessionId: requestSessionId,
      planVersionId: requestVersionId,
      planId: plan.plan_id,
      stopIndex,
      resourceId: stop.resource_id,
      role: stop.role,
      contextRevision: planningContext?.request_revision ?? null,
      rawText: stop.name,
      criteria: [],
    });
    setInput("");
    if (isMobile) setMobileDetailOpen(false);
  }, [activeSelectedPlan?.plan_id, detailResponse?.plan_version_id, failedRequest, isMobile, loading, planningContext, replacementDraft, response?.question, response?.recovery, restoring, selectedPlanId, selectionConfirmation, selectionPendingPlanId]);

  const cancelReplacementDraft = useCallback(() => {
    if (loading || restoring || !replacementDraft) return;
    if (replacementFocusOriginRef.current?.wasMobile) setMobileDetailOpen(true);
    replacementReturnFocusPendingRef.current = true;
    setReplacementDraft(null);
    setReplacementDraftError("");
    setInput("");
    setError("");
  }, [loading, replacementDraft, restoring]);

  const chooseReplacementMode = useCallback((mode: ReplacementPreset) => {
    if (loading || restoring || !replacementDraft) return;
    const criteria = REPLACEMENT_PRESET_CRITERIA[mode].map((criterion) => ({ ...criterion }));
    const command = replacementCommandFromDraft(replacementDraft, criteria, input);
    setReplacementDraft((current) => current ? { ...current, criteria } : current);
    // Presets are one-click actions. Free-text criteria can be combined by
    // typing first and then clicking a preset, or by using Send.
    void submit(input || `更换第 ${replacementDraft.stopIndex + 1} 站：${REPLACEMENT_PRESET_LABELS[mode]}`, command);
  }, [input, loading, replacementDraft, restoring]);

  const activeClarification = response?.status === "needs_input" && !newRequestMode
    ? response.question
    : null;
  const clarificationBlocksFreeText = Boolean(
    response?.question
    && response.question.allow_free_text === false
    && !newRequestMode,
  );

  return (
    <div
      ref={appShellRef}
      style={conversationPaneWidth === null ? undefined : { "--conversation-pane-width": `${conversationPaneWidth}px` } as CSSProperties}
      className={`app-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""} ${isResizingPane ? "is-resizing-pane" : ""}`}
    >
      <a className="skip-link" href="#main-content">跳到主要内容</a>

      <aside className="session-sidebar" id="desktop-session-navigation" aria-label="会话导航">
        <SessionNavigation recentSessions={recentSessions} sessionId={sessionId} onNew={resetSession} onOpen={(id) => void openSession(id)} onRename={renameHistorySession} onDelete={deleteHistorySession} collapsed={sidebarCollapsed} onToggle={() => setSidebarCollapsed((current) => !current)} onOpenHistory={() => setHistoryOpen(true)} />
      </aside>

      <AppHeader
        context={planningContext}
        sessionId={sessionId}
        busy={loading || restoring || Boolean(response?.recovery)}
        externalOpen={contextFocus}
        onExternalOpenHandled={() => setContextFocus(null)}
        onSave={updateContext}
        inspectorAvailable={Boolean(inspectorPlan)}
        onOpenHistory={() => setHistoryOpen(true)}
        onOpenInspector={() => setMobileDetailOpen(true)}
      />

      <PlanningContextNotice context={planningContext} busy={loading || restoring || Boolean(response?.recovery)} onReplan={replanContext} />

      <main className="workspace" id="main-content">
        <section className="conversation" aria-live="polite" onScroll={(event) => {
          const { scrollHeight, scrollTop, clientHeight } = event.currentTarget;
          shouldFollowConversationRef.current = scrollHeight - scrollTop - clientHeight <= 120;
        }}>
          {messages.length === 0 ? <EmptyConversation onSelect={(suggestion) => void submit(suggestion)} /> : (
            <div className="message-list">
              {messages.map((message, index) => <div className="message-entry" key={message.id}>
                {index > 0 ? <div className="message-divider" aria-hidden="true" /> : null}
                <ChatBubble
                  message={message}
                  loading={loading || restoring}
                  currentPlanningContext={planningContext}
                  activeClarification={Boolean(activeClarification?.clarification_id
                    && message.response?.question?.clarification_id === activeClarification.clarification_id
                    && message.response.question.request_revision === activeClarification.request_revision)}
                  activeRecoveryInteractionId={response?.recovery?.interaction_id ?? null}
                  recoveryLifecycle={recoveryLifecycle}
                  selectedPlan={message.response === detailResponse ? inspectorPlan : message.response?.plans[0]}
                  selectedPlanId={message.response?.plan_version_id === activePlanVersionIdRef.current ? selectedPlanId : null}
                  onViewPlan={(planId) => message.response && viewPlan(message.response, planId)}
                  onOpenPlanDetails={(planId) => message.response && openPlanDetails(message.response, planId)}
                  onClarificationOption={(option, answerValue) => message.response?.question && onClarificationOption(option, message.response.question, answerValue)}
                  onRecoveryAction={(action, interaction, value) => onRecoveryAction(action, interaction, value)}
                  onOpenContext={() => openContextForClarification(message.response?.question?.field)}
                  onRetry={message.response?.failure?.retryable && failedRequest?.requestId === message.response.failure.request_id ? () => void submit(failedRequest.content, failedRequest.conversationCommand, failedRequest.clarificationReply, failedRequest.recoveryAction) : undefined}
                />
              </div>)}
              {loading ? <ThinkingRow events={progressEvents} /> : null}
              <div ref={conversationEndRef} aria-hidden="true" />
            </div>
          )}

          {error ? (
            <div className="error-banner" role="alert">
              <CircleAlert size={17} aria-hidden="true" />
              <span>{error}</span>
              {failedRequest && failedRequest.sessionId === sessionId ? (
                <button type="button" disabled={loading || restoring} onClick={() => void submit(
                  failedRequest.content,
                  failedRequest.conversationCommand,
                  failedRequest.clarificationReply,
                  failedRequest.recoveryAction,
                )}>重试本次请求</button>
              ) : null}
            </div>
          ) : null}
        </section>

        <div className="composer-dock" onKeyDown={(event) => {
          if (event.key === "Escape" && replacementDraft) {
            event.preventDefault();
            event.stopPropagation();
            cancelReplacementDraft();
          }
        }}>
          {replacementDraft ? (
            <section ref={replacementDraftRef} className="replacement-draft" aria-label="单站替换草稿" tabIndex={-1}>
              <div className="replacement-draft-copy">
                <strong>正在更换：第 {replacementDraft.stopIndex + 1} 站 · {replacementDraft.rawText}</strong>
                <span>其他站点将保持原位置和内容</span>
                <p className="replacement-draft-guide"><Info size={13} aria-hidden="true" />快捷条件会立即提交；也可以在下方输入自定义偏好后点击发送</p>
              </div>
              <div className="replacement-presets">
                <button type="button" className={replacementDraft.criteria.length === 0 ? "active" : ""} onClick={() => chooseReplacementMode("similar")} disabled={loading || restoring}>换个类似的</button>
                <button type="button" className={replacementDraft.criteria.some((item) => item.kind === "route_objective") ? "active" : ""} onClick={() => chooseReplacementMode("shorter")} disabled={loading || restoring}>全程更近</button>
                <button type="button" className={replacementDraft.criteria.some((item) => isSemanticCriterion(item) && item.text === "安静") ? "active" : ""} onClick={() => chooseReplacementMode("quiet")} disabled={loading || restoring}>更安静</button>
                <button type="button" className={replacementDraft.criteria.some((item) => isSemanticCriterion(item) && item.text === "轻松聊天") ? "active" : ""} onClick={() => chooseReplacementMode("chatty")} disabled={loading || restoring}>更适合聊天</button>
                <button type="button" className={replacementDraft.criteria.some((item) => isSemanticCriterion(item) && item.text === "亲子") ? "active" : ""} onClick={() => chooseReplacementMode("family")} disabled={loading || restoring}>亲子友好</button>
                <button type="button" className="replacement-cancel" onClick={cancelReplacementDraft} disabled={loading || restoring}>取消修改</button>
              </div>
            </section>
          ) : null}
          {replacementDraftError && !replacementDraft ? <div className="replacement-draft-error" role="status">{replacementDraftError}</div> : null}
          {response?.recovery ? <div className="clarification-status" role="status">请选择上方恢复操作；这些操作会按当前条件重新检查。</div> : response?.question || newRequestMode ? <div className="clarification-status" role="status">{newRequestMode ? "正在输入新的规划需求" : `正在回答：${clarificationFieldLabel(response?.question?.field)}`}</div> : null}
          <form className="composer" onSubmit={onSubmit}>
            <label className="sr-only" htmlFor="planning-input">{response?.recovery ? "请先选择恢复操作" : response?.question ? (newRequestMode ? "输入新的规划需求" : "补充这个信息后继续") : replacementDraft ? "描述替换偏好（可选）" : "描述你的空闲时间和偏好"}</label>
            <div className="composer-row">
              <div className="composer-tools" aria-hidden="true"><Compass size={20} /></div>
              <input id="planning-input" value={input} onChange={(event) => setInput(event.target.value)} placeholder={response?.recovery ? "请先选择上方的恢复方式" : newRequestMode ? "描述新的规划需求……" : clarificationBlocksFreeText ? "请使用上方选项补充信息" : response?.question?.question ?? (replacementDraft ? "例如：想吃少辣的，环境安静一点（可选）" : "继续描述新的规划需求……")} disabled={loading || restoring || Boolean(response?.recovery) || clarificationBlocksFreeText} />
              <button type="submit" disabled={loading || restoring || Boolean(response?.recovery) || clarificationBlocksFreeText || (!input.trim() && !replacementDraft)} aria-label={replacementDraft ? "发送替换偏好" : "发送需求"}><Send size={19} aria-hidden="true" /></button>
            </div>
          </form>
        </div>
      </main>

        <div
          className="pane-resizer"
          role="separator"
          aria-label="调整聊天和详情宽度"
          aria-orientation="vertical"
          aria-valuemin={conversationPaneMinValue}
          aria-valuemax={conversationPaneMaxValue}
          aria-valuenow={conversationPaneValue}
          aria-valuetext={`聊天区域 ${conversationPaneValue}%，详情区域 ${100 - conversationPaneValue}%`}
          tabIndex={0}
          onPointerDown={startConversationPaneResize}
          onPointerMove={moveConversationPaneResize}
          onPointerUp={endConversationPaneResize}
          onPointerCancel={endConversationPaneResize}
          onLostPointerCapture={() => { paneResizeActiveRef.current = false; setIsResizingPane(false); }}
          onKeyDown={handleConversationPaneKeyDown}
        ><span className="pane-resizer-grip" aria-hidden="true" /></div>

        <aside
          ref={desktopInspectorScrollRef}
          className="detail-panel"
          aria-label="方案详情"
          onScroll={(event) => setShowInspectorScrollTop(event.currentTarget.scrollTop > 120)}
        >
          {!isMobile ? <Inspector response={detailResponse} plan={inspectorPlan} selectedPlanId={selectedPlanId} isCurrentVersion={Boolean(detailResponse?.plan_version_id && detailResponse.plan_version_id === activePlanVersionIdRef.current)} selectionDisabledReason={inspectorSelectionBlockReason} selectionError={selectionError} selectionPending={selectionPendingPlanId === inspectorPlan?.plan_id} selectedPlan={activeSelectedPlan} canReplace={canReplaceInspectorPlan} poiNavigation={activePoiNavigation} onOpenPoi={openPoiDetail} onBackFromPoi={closePoiDetail} onSelectPlan={() => inspectorPlan && requestPlanSelection(inspectorPlan.plan_id)} onReturnToSelectedPlan={() => activeSelectedResponse && activeSelectedPlan && viewPlan(activeSelectedResponse, activeSelectedPlan.plan_id)} activeTab={rightTab} activeLegIndex={focusedLegIndex} onTabChange={setRightTab} onMapRoute={openRouteOnMap} onFocusMapRoute={focusRouteOnMap} onTimelineRoute={showRouteInTimeline} onReplaceStop={startReplacement} /> : null}
      </aside>

      {contextToast ? <div className="app-toast" role="status">
        <Info size={17} aria-hidden="true" />
        <span>{contextToast}</span>
        <button type="button" aria-label="关闭通知" onClick={() => setContextToast(null)}><X size={16} aria-hidden="true" /></button>
      </div> : null}
      {showInspectorScrollTop && !isMobile ? <button className="inspector-scroll-to-top" type="button" aria-label="返回详情顶部" title="返回顶部" onClick={scrollInspectorToTop}>
        <ChevronUp size={20} aria-hidden="true" />
      </button> : null}

      <MobileSheet open={historyOpen} onOpenChange={setHistoryOpen} title="历史会话" description="查看或继续之前的完整规划对话。">
        <SessionNavigation recentSessions={recentSessions} sessionId={sessionId} onNew={resetSession} onOpen={(id) => void openSession(id)} onRename={renameHistorySession} onDelete={deleteHistorySession} compact />
      </MobileSheet>

      <MobileSheet
        open={mobileDetailOpen}
        onOpenChange={(open) => { setMobileDetailOpen(open); if (!open) setShowInspectorScrollTop(false); }}
        title="方案工作区"
        description="查看行程、地图和地点详情；正式选择以方案上的确认操作为准。"
        compactHeader
        contentRef={mobileInspectorScrollRef}
        contentClassName="inspector-sheet-content"
        onContentScroll={() => setShowInspectorScrollTop((mobileInspectorScrollRef.current?.scrollTop ?? 0) > 120)}
        showScrollToTop={showInspectorScrollTop}
        onScrollToTop={scrollInspectorToTop}
      >
          {isMobile && mobileDetailOpen ? <Inspector response={detailResponse} plan={inspectorPlan} selectedPlanId={selectedPlanId} isCurrentVersion={Boolean(detailResponse?.plan_version_id && detailResponse.plan_version_id === activePlanVersionIdRef.current)} selectionDisabledReason={inspectorSelectionBlockReason} selectionError={selectionError} selectionPending={selectionPendingPlanId === inspectorPlan?.plan_id} selectedPlan={activeSelectedPlan} canReplace={canReplaceInspectorPlan} poiNavigation={activePoiNavigation} onOpenPoi={openPoiDetail} onBackFromPoi={closePoiDetail} onSelectPlan={() => inspectorPlan && requestPlanSelection(inspectorPlan.plan_id)} onReturnToSelectedPlan={() => activeSelectedResponse && activeSelectedPlan && viewPlan(activeSelectedResponse, activeSelectedPlan.plan_id)} activeTab={rightTab} activeLegIndex={focusedLegIndex} onTabChange={setRightTab} onMapRoute={openRouteOnMap} onFocusMapRoute={focusRouteOnMap} onTimelineRoute={showRouteInTimeline} onReplaceStop={startReplacement} /> : null}
      </MobileSheet>

      <Dialog.Root
        open={Boolean(pendingRecoveryConfirmation)}
        onOpenChange={(open) => { if (!open && !loading) setPendingRecoveryConfirmation(null); }}
      >
        <Dialog.Portal>
          <Dialog.Backdrop className="recovery-confirm-backdrop" />
          <Dialog.Popup className="selection-dialog recovery-confirm-dialog">
            <div className="selection-dialog-heading">
              <div className="selection-dialog-icon"><Info size={18} aria-hidden="true" /></div>
              <div>
                <Dialog.Title>确认调整条件</Dialog.Title>
                <Dialog.Description>这是服务端提出的条件变更。确认后才会提交并继续处理，结果以实际返回为准。</Dialog.Description>
              </div>
            </div>
            <div className="recovery-confirm-changes" aria-label="提案条件变化">
              {pendingRecoveryConfirmation && recoveryPatchChanges(pendingRecoveryConfirmation.action, pendingRecoveryConfirmation.context).map((change, index) => (
                <div className="recovery-confirm-change" key={`${change.field}-${index}`}>
                  <strong>{change.label}</strong>
                  <div><span>当前条件</span><span>{change.before}</span></div>
                  <div><span>确认后</span><span>{change.after}</span></div>
                </div>
              ))}
            </div>
            <div className="selection-dialog-actions">
              <Dialog.Close className="selection-dialog-cancel">取消</Dialog.Close>
              <button
                type="button"
                className="selection-dialog-confirm"
                disabled={!pendingRecoveryConfirmation || loading || restoring}
                onClick={confirmRecoveryProposal}
              >确认并继续</button>
            </div>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>

      <Dialog.Root open={Boolean(selectionConfirmation)} onOpenChange={(open) => { if (!open) setSelectionConfirmation(null); }}>
        <Dialog.Portal>
          <Dialog.Backdrop className="selection-dialog-backdrop" />
          <Dialog.Popup className="selection-dialog">
            <div className="selection-dialog-heading">
              <div className="selection-dialog-icon"><CircleAlert size={18} aria-hidden="true" /></div>
              <div>
                <Dialog.Title>切换正式选择？</Dialog.Title>
                <Dialog.Description>预览不会改变当前选择。确认后会让后端将本版本的正式方案切换为下面的候选。</Dialog.Description>
              </div>
            </div>
            <div className="selection-dialog-change">
              <div><span>当前正式方案</span><strong>{activeSelectedPlan?.title ?? (selectedPlanId ? `方案 ${selectedPlanId}` : "尚未选择")}</strong></div>
              <ChevronDown size={17} aria-hidden="true" />
              <div><span>准备切换为</span><strong>{selectionConfirmation?.planTitle}</strong></div>
            </div>
            <div className="selection-dialog-actions">
              <Dialog.Close className="selection-dialog-cancel">取消</Dialog.Close>
              <button type="button" className="selection-dialog-confirm" onClick={() => selectionConfirmation && void selectConfirmedPlan(selectionConfirmation)} disabled={!selectionConfirmation || loading || restoring || selectionPendingPlanId !== null}>
                确认切换
              </button>
            </div>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}

function AppHeader({ context, sessionId, busy, externalOpen, onExternalOpenHandled, onSave, inspectorAvailable, onOpenHistory, onOpenInspector }: {
  context: PlanningContextSummary | null;
  sessionId: string | null;
  busy: boolean;
  externalOpen: ContextOpenTarget | null;
  onExternalOpenHandled: () => void;
  onSave: (patch: PlanningContextPatch) => Promise<void>;
  inspectorAvailable: boolean;
  onOpenHistory: () => void;
  onOpenInspector: () => void;
}) {
  return <header className="app-header">
    <div className="app-header-title">
      <span className="app-header-tagline"><Sparkles size={15} aria-hidden="true" />周末，轻松出发</span>
    </div>
    <PlanningContextBar context={context} sessionId={sessionId} busy={busy} externalOpen={externalOpen} onExternalOpenHandled={onExternalOpenHandled} onSave={onSave} />
    <div className="app-header-mobile-actions">
      <button type="button" onClick={onOpenHistory} aria-label="打开历史会话"><History size={19} /></button>
      <button type="button" onClick={onOpenInspector} aria-label="打开方案详情" disabled={!inspectorAvailable}><PanelRight size={19} /></button>
    </div>
  </header>;
}

function EmptyConversation({ onSelect }: { onSelect: (suggestion: string) => void }) {
  return (
    <div className="empty-conversation">
      <ConversationHeading />
      <div className="suggestion-list">{SUGGESTIONS.map((suggestion) => <button key={suggestion} type="button" onClick={() => onSelect(suggestion)}>{suggestion}<ChevronRight size={15} /></button>)}</div>
    </div>
  );
}

const RECOVERY_FIELD_LABELS: Record<string, string> = {
  max_distance_km: "检索及单段距离上限",
  total_distance_km: "全程累计距离上限",
  exact_stop_count: "精确站数",
  required_stop_roles: "站点要求",
  budget_per_person: "人均预算",
  strict_budget: "预算约束方式",
  return_by: "最晚回家时间",
  start_at: "出发时间",
  end_at: "结束时间",
  "planning_window.end_at": "结束时间",
  "planning_window.start_at": "出发时间",
  planning_area: "出发区域",
  location: "出发地",
  trip_time_scope: "出行时段",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function recoveryFieldLabel(field: string): string {
  return RECOVERY_FIELD_LABELS[field] ?? "其他条件";
}

function recoveryDisplayValue(field: string, value: unknown): string {
  if (isRecord(value)) {
    if (Object.prototype.hasOwnProperty.call(value, "value")) return recoveryDisplayValue(field, value.value);
    if (["planning_area", "location"].includes(field) && typeof value.address === "string") return value.address;
    return "包含复合条件，具体内容由服务端校验";
  }
  if (Array.isArray(value)) {
    return value.length
      ? value.map((item) => recoveryDisplayValue(field, item)).join("、")
      : "无";
  }
  if (value === null || value === undefined) return "未设置";
  if (typeof value === "boolean") {
    if (field === "strict_budget") return value ? "严格上限" : "参考条件";
    return value ? "是" : "否";
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return "未提供有效数值";
    if (field === "max_distance_km" || field === "total_distance_km") return `${value} km`;
    if (field === "exact_stop_count") return `${value} 个站点`;
    if (field === "budget_per_person") return `¥${value}/人`;
    return String(value);
  }
  if (typeof value === "string") return value || "空值";
  return "包含复合条件，具体内容由服务端校验";
}

function currentRecoveryFieldValue(field: string, context: PlanningContextSummary | null): { available: boolean; value?: unknown } {
  if (!context) return { available: false };
  if (field === "max_distance_km") return { available: true, value: context.max_distance_km.value };
  if (field === "total_distance_km") return { available: true, value: context.total_distance_km.value };
  if (field === "exact_stop_count") return { available: true, value: context.exact_stop_count.value };
  if (field === "budget_per_person") {
    const budget = context.budget.value;
    return { available: isRecord(budget), value: isRecord(budget) ? budget.amount : undefined };
  }
  if (field === "strict_budget") {
    const budget = context.budget.value;
    return { available: isRecord(budget), value: isRecord(budget) ? budget.strict : undefined };
  }
  if (["start_at", "planning_window.start_at"].includes(field)) return { available: true, value: context.when.start_at.value };
  if (["end_at", "planning_window.end_at", "return_by"].includes(field)) return { available: true, value: context.when.end_at.value };
  if (["planning_area", "location"].includes(field)) return { available: true, value: context.where.value };
  return { available: false };
}

type RecoveryProposalChange = { field: string; label: string; before: string; after: string };

function recoveryPatchChanges(
  action: Extract<RecoveryAction, { kind: "apply_request_patch" }>,
  context: PlanningContextSummary | null,
): RecoveryProposalChange[] {
  if (!isRecord(action.patch)) return [];
  const patch = action.patch;
  const changes: RecoveryProposalChange[] = [];
  const currentValue = (field: string) => {
    const current = currentRecoveryFieldValue(field, context);
    return current.available ? recoveryDisplayValue(field, current.value) : "当前值未在摘要中返回";
  };
  const setFields = patch.set_fields;
  if (isRecord(setFields)) {
    for (const [field, value] of Object.entries(setFields)) {
      changes.push({ field, label: recoveryFieldLabel(field), before: currentValue(field), after: recoveryDisplayValue(field, value) });
    }
  }
  if (Array.isArray(patch.clear_fields)) {
    for (const field of patch.clear_fields) {
      if (typeof field !== "string") continue;
      changes.push({ field, label: recoveryFieldLabel(field), before: currentValue(field), after: "清除这项条件" });
    }
  }
  for (const [operation, verb] of [["add_to_fields", "增加"], ["remove_from_fields", "移除"]] as const) {
    const fields = patch[operation];
    if (!isRecord(fields)) continue;
    for (const [field, values] of Object.entries(fields)) {
      const valueList = Array.isArray(values) ? values : [values];
      changes.push({
        field,
        label: recoveryFieldLabel(field),
        before: currentValue(field),
        after: `${verb}：${valueList.map((value) => recoveryDisplayValue(field, value)).join("、")}`,
      });
    }
  }
  return changes;
}

function RecoveryCard({ interaction, loading, active, lifecycle, planningContext, onChoose }: {
  interaction: RecoveryChoice;
  loading: boolean;
  active: boolean;
  lifecycle?: RecoveryLifecycle;
  planningContext: PlanningContextSummary | null;
  onChoose: (action: RecoveryAction, interaction: RecoveryChoice, fieldValue?: string) => void;
}) {
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});
  const fallbackHeading = interaction.reason.kind === "hard_conflict"
    ? "当前条件存在冲突"
    : interaction.reason.kind === "modification_failed"
      ? "这次修改暂未成功"
      : "当前条件下暂未找到可行方案";
  const heading = interaction.reason.public_summary.trim().replace(/[。.!！?？]+$/u, "") || fallbackHeading;
  const safeActionKinds = new Set(["keep_current_plan", "start_new_request", "cancel_turn"]);
  const primaryAction = interaction.actions.find((action) => action.kind === "apply_request_patch")
    ?? interaction.actions.find((action) => action.kind === "replan_current_request")
    ?? interaction.actions.find((action) => action.kind !== "open_constraint_editor" && !safeActionKinds.has(action.kind))
    ?? null;
  const explanation = interaction.reason.primary_bottleneck?.evidence_summary || primaryAction?.description || "";
  const editorAction = interaction.actions.find((action) => action.kind === "open_constraint_editor" && action.action_id !== primaryAction?.action_id) ?? null;
  const safeActions = interaction.actions.filter((action) => safeActionKinds.has(action.kind) && action.action_id !== primaryAction?.action_id);
  const otherActions = interaction.actions.filter((action) => (
    action.action_id !== primaryAction?.action_id
    && action.action_id !== editorAction?.action_id
    && !safeActionKinds.has(action.kind)
  ));

  function renderAction(action: RecoveryAction, variant: "primary" | "secondary") {
    if (action.kind === "request_field") {
      return <div className={`recovery-field-action recovery-field-action-${variant}`} key={action.action_id}>
        <strong>{action.label}</strong>
        {action.description ? <small>{action.description}</small> : null}
        {action.input_type === "choice" ? (
          <div className="recovery-choice-options">
            {action.choices.map((choice) => (
              <button type="button" key={choice} disabled={loading || !active} onClick={() => onChoose(action, interaction, choice)}>{choice}</button>
            ))}
          </div>
        ) : (
          <div className="recovery-field-entry">
            <input
              aria-label={action.label}
              type={action.input_type === "number" ? "number" : action.input_type === "clock" ? "time" : "text"}
              value={fieldValues[action.action_id] ?? ""}
              onChange={(event) => setFieldValues((current) => ({ ...current, [action.action_id]: event.target.value }))}
              disabled={loading || !active}
            />
            <button type="button" disabled={loading || !active || !(fieldValues[action.action_id] ?? "").trim()} onClick={() => onChoose(action, interaction, fieldValues[action.action_id])}>{action.label}</button>
          </div>
        )}
      </div>;
    }
    const changes = action.kind === "apply_request_patch" ? recoveryPatchChanges(action, planningContext) : [];
    const changePreview = changes.length
      ? changes.map(({ label, after }) => `${label} → ${after}`).join("；")
      : null;
    return <button className={`recovery-action-button recovery-action-${variant} ${action.kind === "open_constraint_editor" ? "recovery-action-editor" : ""}`} type="button" key={action.action_id} disabled={loading || !active} onClick={() => onChoose(action, interaction)}>
      {variant === "secondary" && action.kind === "open_constraint_editor" ? <SlidersHorizontal size={16} aria-hidden="true" /> : null}
      <span>{action.label}</span>
      {variant === "primary" ? <ArrowUpRight size={17} aria-hidden="true" /> : null}
      {changePreview ? <small className="recovery-preview">提案：{changePreview}</small> : null}
      {changePreview ? <small className="recovery-confirmation-status">需要确认</small> : null}
      {action.description && variant !== "primary" ? <small>{action.description}</small> : null}
    </button>;
  }

  return (
    <section className="recovery-card" role="group" aria-label="规划恢复选项">
      <div className="recovery-eyebrow"><CircleAlert size={16} aria-hidden="true" /><span>需要你确认下一步</span></div>
      <h3 className="recovery-card-title">{heading}</h3>
      {explanation ? <p className="recovery-card-explanation">{explanation}</p> : null}
      <div className="recovery-safety-banner">
        <ShieldCheck size={18} aria-hidden="true" />
        <div>
          <strong>{planningContext?.has_active_plan ? "当前方案已安全保留" : "当前条件保持不变"}</strong>
          <small>每项调整都要确认并重新校验，不会静默覆盖已选择的方案。</small>
        </div>
      </div>
      {lifecycle?.summary ? (
        <p className={`recovery-lifecycle recovery-lifecycle-${lifecycle.status}`} role="status">
          {lifecycle.status === "submitted" ? "正在应用：" : null}{lifecycle.summary}
        </p>
      ) : null}
      {lifecycle?.fieldChanges?.length ? (
        <div className="recovery-change-list" aria-label="恢复后的条件变化">
          {lifecycle.fieldChanges.map((change) => (
            <span key={`${change.field}-${change.before}-${change.after}`}>
              {recoveryFieldLabel(change.field)}：{recoveryDisplayValue(change.field, change.before)} → {recoveryDisplayValue(change.field, change.after)}
            </span>
          ))}
        </div>
      ) : null}
      {lifecycle?.currentBottleneck ? (
        <small className="recovery-bottleneck">
          当前主要限制：{RECOVERY_BOTTLENECK_LABELS[lifecycle.currentBottleneck] ?? lifecycle.currentBottleneck}
          {lifecycle.previousBottleneck && lifecycle.previousBottleneck !== lifecycle.currentBottleneck
            ? `（原限制：${RECOVERY_BOTTLENECK_LABELS[lifecycle.previousBottleneck] ?? lifecycle.previousBottleneck}）`
            : ""}
        </small>
      ) : null}
      {!active ? <small className="recovery-resolved-note">此恢复选项已结束，不能重复执行。</small> : null}
      {active ? (
      <div className="recovery-prioritized-actions">
        {primaryAction ? <section className="recovery-primary-action"><strong className="recovery-section-label">优先尝试</strong>{renderAction(primaryAction, "primary")}</section> : null}
        {editorAction ? renderAction(editorAction, "secondary") : null}
        {otherActions.length ? <details className="recovery-alternative-disclosure">
          <summary><span>其他调整方式</span><small>{otherActions.length} 项</small><ChevronDown size={15} className="disclosure-chevron" aria-hidden="true" /></summary>
          <div className="recovery-action-list">{otherActions.map((action) => renderAction(action, "secondary"))}</div>
        </details> : null}
        {safeActions.length ? <div className="recovery-safe-actions" aria-label="其他操作">{safeActions.map((action) => (
          <button type="button" className="recovery-tertiary-action" key={action.action_id} disabled={loading || !active} title={action.description} onClick={() => onChoose(action, interaction)}>{action.label}</button>
        ))}</div> : null}
      </div>
      ) : null}
      {active ? <small className="recovery-safety-note">调整后的条件与行程仍需再次核验，系统不保证一定能生成方案。</small> : null}
    </section>
  );
}

function ChatBubble({ message, loading, currentPlanningContext, activeClarification, activeRecoveryInteractionId, recoveryLifecycle, selectedPlan, selectedPlanId, onViewPlan, onOpenPlanDetails, onClarificationOption, onRecoveryAction, onOpenContext, onRetry }: {
  message: ChatMessage;
  loading: boolean;
  currentPlanningContext: PlanningContextSummary | null;
  activeClarification: boolean;
  activeRecoveryInteractionId: string | null;
  recoveryLifecycle: Record<string, RecoveryLifecycle>;
  selectedPlan?: Plan;
  selectedPlanId: string | null;
  onViewPlan: (planId: string) => void;
  onOpenPlanDetails: (planId: string) => void;
  onClarificationOption: (option: ClarificationOption, answerValue?: string | null) => void;
  onRecoveryAction: (action: RecoveryAction, interaction: RecoveryChoice, fieldValue?: string) => void;
  onOpenContext: () => void;
  onRetry?: () => void;
}) {
  const usesStructuredResponse = Boolean(
    message.response && (
      message.response.plans.length > 0
      || message.response.conflict
      || message.response.question
      || message.response.recovery
      || message.response.failure
    ),
  );
  const recoveryIsActive = Boolean(
    message.response?.recovery
    && activeRecoveryInteractionId === message.response.recovery.interaction_id
    && message.response.status === "needs_recovery",
  );
  return (
    <article className={`message ${message.role} ${message.response?.plans.length ? "rich-turn" : ""} ${usesStructuredResponse ? "structured-turn" : ""}`}>
      <div className="message-content">
        <span className={message.role === "assistant" ? "assistant-speaker" : "user-speaker"}>{message.role === "assistant" ? "管家" : "你"}</span>
        {message.content && !usesStructuredResponse ? <p>{message.content}</p> : null}
        {message.response?.question ? (
          <section
            className={`clarification-card ${activeClarification ? "" : "clarification-card-readonly"}`}
            role="group"
            aria-label={message.response.question.field === "date" ? "确认出行日期" : message.response.question.options?.length ? "补充信息选项" : "补充信息"}
          >
            <strong>需要补充：{clarificationFieldLabel(message.response.question.field)}</strong>
            <p>{message.response.question.question}</p>
            {!activeClarification ? <small className="clarification-readonly-note">这条问题可继续查看，但已过期或属于历史记录，不能重复操作。</small> : null}
            {message.response.question.field === "date" ? (
              <ClarificationDatePicker
                key={`${message.id}-${message.response.question.clarification_id ?? "date"}`}
                disabled={loading || !activeClarification}
                onSubmit={(value) => onClarificationOption({ id: "answer-date", label: "确认日期", action: "answer" }, value)}
              />
            ) : null}
            {message.response.question.options?.length ? (
              <div className="clarification-options">
                {message.response.question.options.map((option) => (
                  <button type="button" key={option.id} disabled={loading || !activeClarification} onClick={() => onClarificationOption(option)}>{option.label}</button>
                ))}
              </div>
            ) : null}
            {activeClarification && clarificationSection(message.response.question.field) ? <button type="button" className="clarification-context-link" disabled={loading} onClick={onOpenContext}>去顶部修改</button> : null}
            {activeClarification && message.response.question.allow_free_text === false ? <small>请使用上面的选项继续，避免重复询问。</small> : activeClarification ? <small>也可以在下方直接输入。</small> : null}
          </section>
        ) : null}
        {message.response?.recovery ? (
          <RecoveryCard
            interaction={message.response.recovery}
            loading={loading}
            active={recoveryIsActive}
            lifecycle={recoveryLifecycle[message.response.recovery.interaction_id]}
            planningContext={recoveryIsActive ? currentPlanningContext : message.response.planning_context ?? null}
            onChoose={onRecoveryAction}
          />
        ) : null}
        {message.response?.conflict ? (
          <section className="conflict-panel" role="status">
            <strong>{message.response.conflict.message}</strong>
          </section>
        ) : null}
        {message.response?.failure ? (
          <section className="conflict-panel failure-panel" role="alert">
            <strong>{message.response.failure.public_message}</strong>
            <small>
              {message.response.failure.retryable
                ? "当前方案未被修改，可以重试本轮或开始新的规划。"
                : "当前方案未被修改，可以开始新的规划。"}
            </small>
            {message.response.failure.retryable && onRetry ? (
              <button type="button" onClick={onRetry} disabled={loading}>重试本轮</button>
            ) : null}
          </section>
        ) : null}
        {message.response?.plans.length ? (
          <RichPlanningReply response={message.response} selectedPlan={selectedPlan} selectedPlanId={selectedPlanId} onViewPlan={onViewPlan} onOpenPlanDetails={onOpenPlanDetails} />
        ) : null}
      </div>
    </article>
  );
}

function ClarificationDatePicker({ disabled, onSubmit }: { disabled: boolean; onSubmit: (value: string) => void }) {
  const inputId = useId();
  const [value, setValue] = useState("");
  return (
    <div className="clarification-date-picker">
      <label htmlFor={inputId}>选择出行日期</label>
      <div>
        <input
          id={inputId}
          type="date"
          value={value}
          disabled={disabled}
          onChange={(event) => setValue(event.target.value)}
        />
        <button type="button" disabled={disabled || !value} onClick={() => onSubmit(value)}>确认日期</button>
      </div>
    </div>
  );
}

function ThinkingRow({ events }: { events: PlanningRunEvent[] }) {
  const stages = projectRunProgress(events);
  const active = stages.find((stage) => stage.status === "active") ?? stages[stages.length - 1];
  return (
    <div className="thinking-row" role="status" aria-live="polite">
      <span className="assistant-speaker">管家</span>
      <div className="thinking-progress">
        <strong>{active?.message ?? "正在处理你的规划请求……"}</strong>
        {stages.length ? <RunProgressRows stages={stages} /> : <span className="thinking-dots"><span /><span /><span /></span>}
      </div>
    </div>
  );
}

function RichPlanningReply({ response, selectedPlan, selectedPlanId, onViewPlan, onOpenPlanDetails }: { response: AgentResponse; selectedPlan?: Plan; selectedPlanId: string | null; onViewPlan: (planId: string) => void; onOpenPlanDetails: (planId: string) => void }) {
  const headingId = `plan-heading-${response.plans[0]?.plan_id ?? "reply"}`;
  const recommendationHeadingId = `recommendation-heading-${response.plans[0]?.plan_id ?? "reply"}`;
  const diffs = Array.isArray(response.plan_diffs)
    ? response.plan_diffs
    : response.plan_diff ? [response.plan_diff] : [];
  const hasModification = response.conversation_command?.operation === "replace" || diffs.length > 0;
  const recommendation = response.recommendation_advice;
  const recommendedPlan = recommendation
    ? response.plans.find((plan) => plan.plan_id === recommendation.recommended_plan_id)
    : undefined;
  const preferenceTags = [...new Map((recommendation?.understood_needs ?? [])
    .filter((need) => need.need_id.startsWith("need."))
    .map((need) => need.text.trim())
    .filter(Boolean)
    .map((text) => [text.toLocaleLowerCase(), text] as const))]
    .map(([, text]) => text)
    .slice(0, 3);
  const recommendationReason = recommendation
    ? compactRecommendationReason(recommendation.overall_reason, hasModification)
    : "";
  const planResultsHeading = `为您整理了 ${response.plans.length} 个可选方案`;
  const weatherTag = planningWeatherTag(response.provider_facts ?? []);
  const constraintTags = [
    ...(weatherTag ? [weatherTag] : []),
    ...planningConstraintTags(response.constraint_summary ?? []),
  ].slice(0, 5);
  const showRecommendation = Boolean(recommendedPlan && (preferenceTags.length || recommendationReason));
  const diffForPlan = (plan: Plan): PlanDiff | null => {
    const basePlanId = typeof response.conversation_command?.base_plan_id === "string"
      ? response.conversation_command.base_plan_id
      : null;
    return diffs.find((diff) => (
      diff?.new_plan_id === plan.plan_id
      && Boolean(response.plan_version_id)
      && diff.to_plan_version_id === response.plan_version_id
      && (!basePlanId || diff.base_plan_id === basePlanId)
    )) ?? null;
  };
  const presentationByResourceId = new Map(response.poi_presentations.map((item) => [item.resource_id, item]));
  const imageCredits = response.plans.flatMap((plan) => {
    const stop = plan.stops[0];
    if (!stop) return [];
    const image = stop.image ?? presentationByResourceId.get(stop.resource_id)?.gallery[0];
    return image ? [{ planId: plan.plan_id, stop, image }] : [];
  });
  return (
    <section className="rich-planning-reply" aria-label={hasModification ? "替换结果" : "规划方案"}>
      <div className="rich-reply-body">
        {!hasModification && response.plans.length ? <header className="plan-results-intro">
          <div className="plan-results-intro-heading"><Sparkles size={19} aria-hidden="true" /><h2>{planResultsHeading}</h2></div>
          <p>先比较方案摘要；点击卡片可预览，详情区可查看逐站安排、地图与核验信息。预览不会改变正式选择。</p>
        </header> : null}
        {hasModification ? <div className="plan-intro"><div><h2 id={headingId}>替换结果</h2><p>先核对差异，再选择新方案。</p></div><span>{response.plans.length} 个候选</span></div> : null}
        <RunProgressDisclosure trace={response.run_trace} />
        {response.warnings?.some((warning) => warning.plan_id === null) ? <div className="shared-warning"><CircleAlert size={16} />{response.warnings.filter((warning) => warning.plan_id === null).map((warning) => warning.message).join("；")}</div> : null}
        {constraintTags.length ? <div className="planning-constraint-tags" role="group" aria-label="本轮规划参考的硬约束">{constraintTags.map((tag) => <span className={`planning-constraint-tag ${tag.field === "weather" ? "weather" : ""}`} key={tag.field}><PlanningConstraintIcon field={tag.field} /><strong>{tag.label}</strong>{" "}<span>{tag.value}</span></span>)}</div> : null}
        {showRecommendation && recommendedPlan ? (
          <section className="recommendation-summary" aria-labelledby={recommendationHeadingId}>
            <h2 id={recommendationHeadingId}><Sparkles size={15} aria-hidden="true" />管家首选：{recommendedPlan.title}</h2>
            {preferenceTags.length ? <div className="recommendation-tags" aria-label="偏好标签">{preferenceTags.map((tag) => <span key={tag}>{tag}</span>)}</div> : null}
            {recommendationReason ? <p>{recommendationReason}</p> : null}
          </section>
        ) : null}
        <div className="plan-grid" aria-label="方案比较列表">
          {response.plans.map((plan, index) => <PlanCard key={plan.plan_id} plan={plan} heroGalleryImage={plan.stops[0] ? presentationByResourceId.get(plan.stops[0].resource_id)?.gallery[0] : undefined} recommended={response.recommendation_advice?.recommended_plan_id === plan.plan_id} index={index} warning={planWarning(response, plan)} viewed={(selectedPlan?.plan_id ?? response.plans[0].plan_id) === plan.plan_id} selected={selectedPlanId === plan.plan_id} modificationDiff={hasModification ? diffForPlan(plan) : null} isModification={hasModification} onView={() => onViewPlan(plan.plan_id)} onOpenDetails={() => onOpenPlanDetails(plan.plan_id)} />)}
        </div>
        {(response.poi_presentations.length || response.plans.some((plan) => plan.stops[0]?.image)) ? (
          <div className="plan-results-notes" aria-label="方案数据与图片来源">
            {response.poi_presentations.length ? <p><Database size={13} aria-hidden="true" />POI 商业信息为模拟数据；路线来源和降级状态见各路线段。</p> : null}
            {imageCredits.length ? <p><ImageIcon size={13} aria-hidden="true" /><span>地点图片：</span>{imageCredits.map(({ planId, stop, image }, index) => {
              const sourceLink = /^https?:\/\//i.test(image.source_uri ?? "");
              const licenseLink = /^https?:\/\//i.test(image.license_uri ?? "");
              return <span key={`${planId}-image-credit`}>
                {index ? " · " : ""}{sourceLink ? <a href={image.source_uri!} target="_blank" rel="noreferrer noopener" title={image.attribution ?? undefined}>{stop.name}</a> : stop.name}
                {`（${image.author || image.attribution || stop.name} · `}{licenseLink ? <a href={image.license_uri!} target="_blank" rel="noreferrer noopener">{image.license}</a> : image.license}{"）"}
              </span>;
            })}</p> : null}
          </div>
        ) : null}
      </div>
    </section>
  );
}

function PlanCard({ plan, heroGalleryImage, recommended = false, index, warning, viewed, selected, modificationDiff, isModification, onView, onOpenDetails }: { plan: Plan; heroGalleryImage?: PoiPresentation["gallery"][number]; recommended?: boolean; index: number; warning: PlanWarning | null; viewed: boolean; selected: boolean; modificationDiff: PlanDiff | null; isModification: boolean; onView: () => void; onOpenDetails: () => void }) {
  const heroStop = plan.stops[0];
  const routeRepeatsTitle = plan.stops.length > 0 && plan.stops.every((stop) => stop.name.trim().length > 0 && plan.title.includes(stop.name));
  const routeIsDegraded = plan.route_legs.some((leg) => leg.degraded);
  const notice = warning?.message ?? "路线包含估算或降级路段；请查看路线来源与核验信息。";
  const timeRange = planTimeRangeLabel(plan);
  const durationHours = Math.floor(plan.total_duration_minutes / 60);
  const durationMinutes = plan.total_duration_minutes % 60;
  const durationLabel = durationHours ? `${durationHours} 小时${durationMinutes ? ` ${durationMinutes} 分钟` : ""}` : `${plan.total_duration_minutes} 分钟`;
  return (
    <article className={`plan-card ${viewed ? "viewed" : ""} ${selected ? "selected" : ""} ${recommended ? "recommended" : ""}`}>
      <div className="plan-card-layout">
        <button className="plan-card-cover-button" type="button" onClick={onView} aria-label={`预览${plan.title}图片`}>
          <div className="plan-card-cover">
            {heroStop ? <StopImage stop={heroStop} galleryImage={heroGalleryImage} variant="hero" /> : <div className="plan-image-empty"><ImageIcon size={22} aria-hidden="true" /><span>暂无可靠图片</span></div>}
          </div>
        </button>
        <button className="plan-preview-button" type="button" onClick={onView} aria-pressed={viewed} aria-label={`预览${plan.title}`}>
          <div className="plan-card-body">
            <div className="plan-card-status-row">
              <span className="plan-rank">方案 {String(index + 1).padStart(2, "0")}{strategyLabel(plan.strategy) ? ` · ${strategyLabel(plan.strategy)}` : ""}</span>
              <span className="plan-card-status-right">
                {recommended ? <span className="recommended-badge"><Sparkles size={12} aria-hidden="true" />管家首选</span> : null}
                {selected ? <span className="plan-selected-status"><Check size={13} aria-hidden="true" />已选</span> : null}
              </span>
            </div>
            <h3 title={plan.title}>{plan.title}</h3>
            {!routeRepeatsTitle ? <p className="plan-card-stops" aria-label="行程站点">{plan.stops.length ? plan.stops.map((stop) => stop.name).join(" → ") : "暂无站点信息"}</p> : null}
            {warning || routeIsDegraded ? <div className={`plan-notice ${warning?.degraded || routeIsDegraded ? "degraded" : ""} ${warning || routeIsDegraded ? "flagged" : ""}`} role={warning ? "note" : undefined} aria-label={warning ? "方案待确认信息" : undefined}>
              <CircleAlert size={14} aria-hidden="true" />{notice}
            </div> : null}
          </div>
        </button>
        <div className="plan-card-footer">
          <div className="plan-metrics">
            <span aria-label={timeRange ? `完整行程时间 ${timeRange}` : `行程时长 ${durationLabel}`} title={timeRange ? `完整行程时间 ${timeRange} · ${durationLabel}` : undefined}><Clock3 size={16} aria-hidden="true" />{timeRange ?? durationLabel}</span>
            <span aria-label={`累计路线距离 ${formatDistance(totalDistance(plan))} 公里`}><Route size={16} aria-hidden="true" />全程 {formatDistance(totalDistance(plan))} km</span>
            <span className="plan-metric-price" aria-label={`全程费用合计（不含交通）：${compactPlanPriceLabel(plan)}`} title="所有站点费用汇总，覆盖全体同行人，不含交通"><CircleDollarSign size={16} aria-hidden="true" />全程费用合计 {compactPlanPriceLabel(plan)}</span>
          </div>
          <button className="plan-card-details" type="button" onClick={onOpenDetails} aria-label={`打开${plan.title}详情`}>
            查看行程详情<ArrowUpRight size={14} aria-hidden="true" />
          </button>
        </div>
      </div>
      {isModification ? <PlanDiffEvidence diff={modificationDiff} plan={plan} /> : null}
    </article>
  );
}

function InspectorViewPicker({ activeTab, onTabChange }: { activeTab: InspectorTab; onTabChange: (tab: InspectorTab) => void }) {
  const buttons = useRef<Array<HTMLButtonElement | null>>([]);
  const tabs: Array<{ id: InspectorTab; label: string; icon: ReactNode }> = [
    { id: "trip", label: "行程", icon: <CalendarDays size={16} aria-hidden="true" /> },
    { id: "map", label: "地图", icon: <MapIcon size={16} aria-hidden="true" /> },
    { id: "evidence", label: "核验与来源", icon: <ShieldCheck size={16} aria-hidden="true" /> },
  ];
  const focusTab = (index: number) => {
    const nextIndex = (index + tabs.length) % tabs.length;
    const nextTab = tabs[nextIndex].id;
    onTabChange(nextTab);
    buttons.current[nextIndex]?.focus();
  };
  return <div className="detail-view-nav" role="tablist" aria-label="方案工作区">
    {tabs.map((tab, index) => <button
      type="button"
      role="tab"
      id={`inspector-tab-${tab.id}`}
      aria-controls={`inspector-panel-${tab.id}`}
      aria-selected={activeTab === tab.id}
      tabIndex={activeTab === tab.id ? 0 : -1}
      key={tab.id}
      ref={(element) => { buttons.current[index] = element; }}
      className={activeTab === tab.id ? "active" : ""}
      onClick={() => onTabChange(tab.id)}
      onKeyDown={(event) => {
        if (event.key === "ArrowRight") { event.preventDefault(); focusTab(index + 1); }
        else if (event.key === "ArrowLeft") { event.preventDefault(); focusTab(index - 1); }
        else if (event.key === "Home") { event.preventDefault(); focusTab(0); }
        else if (event.key === "End") { event.preventDefault(); focusTab(tabs.length - 1); }
      }}
    >{tab.icon}<span>{tab.label}</span></button>)}
  </div>;
}

function PlanInspectorHeader({ plan, selectedPlanId, selectedPlan, isCurrentVersion, selectionDisabledReason, selectionError, selectionPending, onSelectPlan, onReturnToSelectedPlan }: {
  plan: Plan;
  selectedPlanId: string | null;
  selectedPlan?: Plan;
  isCurrentVersion: boolean;
  selectionDisabledReason: string | null;
  selectionError: string;
  selectionPending: boolean;
  onSelectPlan: () => void;
  onReturnToSelectedPlan: () => void;
}) {
  const isSelected = isCurrentVersion && selectedPlanId === plan.plan_id;
  const distance = formatDistance(totalDistance(plan));
  const hours = Math.floor(plan.total_duration_minutes / 60);
  const minutes = plan.total_duration_minutes % 60;
  const duration = [hours ? `${hours} 小时` : "", minutes ? `${minutes} 分钟` : ""].filter(Boolean).join(" ") || `${plan.total_duration_minutes} 分钟`;
  const returnToSelection = selectedPlan && (!isCurrentVersion || selectedPlan.plan_id !== plan.plan_id) ? selectedPlan : null;
  return (
    <section className="inspector-plan-header" aria-label="当前预览方案">
      <div className={`inspector-plan-state-row ${isSelected ? "selected" : isCurrentVersion ? "preview" : "readonly"}`}>
        <span className="inspector-plan-state-pill"><i aria-hidden="true" />{isSelected ? "已正式选择" : isCurrentVersion ? "当前预览" : "历史预览 · 只读"}</span>
        {returnToSelection ? <button
          className="inspector-return-selection"
          type="button"
          aria-label={`返回已选方案：${returnToSelection.title}`}
          title={`返回已选方案：${returnToSelection.title}`}
          onClick={onReturnToSelectedPlan}
        ><ChevronLeft size={14} aria-hidden="true" /><span>返回已选</span></button> : null}
      </div>
      <div className="inspector-title-row">
        <h2 title={plan.title}>{plan.title}</h2>
        <button className="inspector-selection-action" type="button" aria-label={isSelected ? "已选择此方案" : undefined} onClick={onSelectPlan} disabled={isSelected || Boolean(selectionDisabledReason) || selectionPending}>
          {selectionPending ? "正在保存…" : isSelected ? "已选择" : "选择此方案"}{selectionPending || isSelected ? <Check size={14} aria-hidden="true" /> : <ChevronRight size={16} aria-hidden="true" />}
        </button>
      </div>
      <div className="inspector-plan-metrics">
        <span><Clock3 size={15} aria-hidden="true" />{duration}</span>
        <span><Route size={15} aria-hidden="true" />全程 {distance} km</span>
        <span title="所有站点费用汇总，覆盖全体同行人，不含交通"><CircleDollarSign size={15} aria-hidden="true" />全程费用合计 {compactPlanPriceLabel(plan)}</span>
      </div>
      {selectionDisabledReason && !isSelected ? <p className="inspector-selection-note" role="status">{selectionDisabledReason}</p> : null}
      {selectionError ? <p className="inspector-selection-error" role="alert">{selectionError}</p> : null}
    </section>
  );
}

function Inspector({ response, plan, selectedPlanId, isCurrentVersion, selectionDisabledReason, selectionError, selectionPending, selectedPlan, canReplace, poiNavigation, onOpenPoi, onBackFromPoi, onSelectPlan, onReturnToSelectedPlan, activeTab, activeLegIndex, onTabChange, onMapRoute, onFocusMapRoute, onTimelineRoute, onReplaceStop }: { response: AgentResponse | null; plan?: Plan; selectedPlanId: string | null; isCurrentVersion: boolean; selectionDisabledReason: string | null; selectionError: string; selectionPending: boolean; selectedPlan?: Plan; canReplace: boolean; poiNavigation: PoiNavigation | null; onOpenPoi: (stopIndex: number, triggerElement?: HTMLElement | null) => void; onBackFromPoi: () => void; onSelectPlan: () => void; onReturnToSelectedPlan: () => void; activeTab: InspectorTab; activeLegIndex: number | null; onTabChange: (tab: InspectorTab) => void; onMapRoute: (legIndex: number) => void; onFocusMapRoute: (legIndex: number) => void; onTimelineRoute: (legIndex: number) => void; onReplaceStop: (plan: Plan, stopIndex: number, triggerElement?: HTMLElement | null) => void }) {
  const advice = plan ? planAdviceFor(response?.recommendation_advice, plan.plan_id) : undefined;
  const isRecommended = Boolean(plan && response?.recommendation_advice?.recommended_plan_id === plan.plan_id);
  const poiStop = poiNavigation && plan?.plan_id === poiNavigation.planId
    ? plan.stops[poiNavigation.stopIndex]
    : undefined;
  const poiPresentation = poiStop
    ? response?.poi_presentations.find((item) => item.resource_id === poiStop.resource_id)
    : undefined;
  const showingPoi = Boolean(poiNavigation && poiStop && poiStop.resource_id === poiNavigation.resourceId);
  return (
    <div className="inspector">
      {showingPoi ? <div className="inspector-poi-context"><button className="poi-back" type="button" onClick={onBackFromPoi}><ChevronLeft size={17} aria-hidden="true" />返回{poiNavigation!.returnTab === "map" ? "地图" : poiNavigation!.returnTab === "evidence" ? "核验与来源" : "行程"}</button><span>第 {poiNavigation!.stopIndex + 1} 站 · 地点详情</span></div> : <>
        {plan ? <PlanInspectorHeader plan={plan} selectedPlanId={selectedPlanId} selectedPlan={selectedPlan} isCurrentVersion={isCurrentVersion} selectionDisabledReason={selectionDisabledReason} selectionError={selectionError} selectionPending={selectionPending} onSelectPlan={onSelectPlan} onReturnToSelectedPlan={onReturnToSelectedPlan} /> : null}
        <div className="inspector-view-switcher"><InspectorViewPicker activeTab={activeTab} onTabChange={onTabChange} /></div>
      </>}
      {showingPoi && plan && poiStop && poiNavigation ? <PoiDetailPage
        key={`${poiNavigation.sessionId ?? "history"}:${poiNavigation.planVersionId ?? "unanchored"}:${poiNavigation.planId}:${poiStop.resource_id}`}
        stop={poiStop}
        stopIndex={poiNavigation.stopIndex}
        routeLeg={plan.route_legs[poiNavigation.stopIndex]}
        presentation={poiPresentation}
        canReplace={canReplace}
        onReplace={(triggerElement) => onReplaceStop(plan, poiNavigation.stopIndex, triggerElement)}
      /> : <div className="inspector-tabpanel" role="tabpanel" id={`inspector-panel-${activeTab}`} aria-labelledby={`inspector-tab-${activeTab}`} tabIndex={0}>
        {activeTab === "trip" ? <TripPanel plan={plan} advice={advice} isRecommended={isRecommended} presentations={response?.poi_presentations ?? []} canReplace={canReplace} onReplaceStop={onReplaceStop} activeLegIndex={activeLegIndex} onOpenPoi={onOpenPoi} onMapRoute={onMapRoute} /> : null}
        {activeTab === "map" ? <MapPanel plan={plan} activeLegIndex={activeLegIndex} onOpenPoi={onOpenPoi} onFocusRoute={onFocusMapRoute} onTimelineRoute={onTimelineRoute} /> : null}
        {activeTab === "evidence" ? <EvidencePanel response={response} plan={plan} /> : null}
      </div>}
    </div>
  );
}

function RecommendationSummary({ plan, advice, isRecommended }: { plan: Plan; advice?: PlanAdvice; isRecommended: boolean }) {
  const reason = advice?.reason.trim();
  const reasons = reason ? [reason] : planRationale(plan);
  const tradeoffs = advice?.tradeoffs.length ? advice.tradeoffs : plan.tradeoffs;
  if (!reasons.length && !tradeoffs.length) return null;
  return <section className="recommendation-summary"><h3><Sparkles size={15} aria-hidden="true" />{isRecommended ? "为什么管家首选这条路线" : "方案特点与取舍"}</h3>{reasons.length ? <ul>{reasons.map((item) => <li key={item}>{item}</li>)}</ul> : null}{tradeoffs.length ? <details><summary><span>主要取舍与注意事项</span><ChevronDown size={15} className="disclosure-chevron" aria-hidden="true" /></summary><ul>{tradeoffs.slice(0, 3).map((tradeoff) => <li key={tradeoff}>{tradeoff}</li>)}</ul></details> : null}</section>;
}

function TripPanel({ plan, advice, isRecommended, presentations, canReplace, onReplaceStop, activeLegIndex, onOpenPoi, onMapRoute }: { plan?: Plan; advice?: PlanAdvice; isRecommended: boolean; presentations: PoiPresentation[]; canReplace: boolean; onReplaceStop: (plan: Plan, stopIndex: number, triggerElement?: HTMLElement | null) => void; activeLegIndex: number | null; onOpenPoi: (stopIndex: number, triggerElement?: HTMLElement | null) => void; onMapRoute: (legIndex: number) => void }) {
  if (!plan) return <DetailEmpty icon={<CalendarDays size={24} />} title="行程将在这里展开" text="生成方案后，可逐站查看 POI、时间、通勤和推荐取舍。" />;
  const returnLegIndex = plan.route_legs.length > plan.stops.length ? plan.route_legs.length - 1 : null;
  const routeDuration = plan.route_legs.reduce((sum, leg) => sum + leg.duration_minutes, 0);
  return (
    <div className="trip-detail">
      <RecommendationSummary plan={plan} advice={advice} isRecommended={isRecommended} />
      <div className="detail-section-heading trip-section-title"><h3>行程安排</h3><span>{plan.stops.length} 站</span></div>
      <ol className="timeline">
         {plan.stops.map((stop, index) => {
           const presentation = presentations.find((item) => item.resource_id === stop.resource_id);
           const tags = [...new Set([...(presentation?.scene_tags ?? []), ...(presentation?.facility_tags ?? []), ...stop.category_tags])].slice(0, 2);
           const detailFactsId = `timeline-stop-facts-${index}`;
           const detailHintId = `timeline-stop-detail-hint-${index}`;
           return <li key={stop.resource_id} className="timeline-item">
             {plan.route_legs[index] ? <TimelineRoute leg={plan.route_legs[index]} index={index} active={activeLegIndex === index} kind={index === 0 ? "start" : "next"} onSelect={onMapRoute} /> : null}
             <div className="timeline-stop">
               <div className="timeline-stop-marker-column"><span className="timeline-marker">{index + 1}</span></div>
               <div className="timeline-stop-content">
                 <div className="timeline-stop-card">
                   <div className="timeline-stop-meta"><span className="timeline-stop-time-range"><time>{stop.start}</time><span aria-hidden="true">–</span><time>{stop.end}</time></span><small className="timeline-stop-duration">停留 {stop.duration_minutes} 分钟</small></div>
                   <div className="timeline-stop-main">
                     <button className="timeline-stop-open" type="button" data-inspector-stop-index={index} aria-label={`查看第 ${index + 1} 站 ${stop.name} 详情`} aria-describedby={`${detailFactsId} ${detailHintId}`} onClick={(event) => onOpenPoi(index, event.currentTarget)}>
                       <span className="timeline-stop-copy">
                         <strong>{stop.name}<ChevronRight size={16} aria-hidden="true" /></strong>
                         <span className="timeline-stop-facts" id={detailFactsId}>
                           <span className="timeline-stop-chip">{presentation?.category_label ?? (stop.type === "restaurant" ? "餐饮" : "活动")}</span>
                           <span className="timeline-stop-chip">{priceLabel(stop)}</span>
                           {tags.map((tag) => <span className="timeline-stop-chip" key={tag}>{tag}</span>)}
                         </span>
                         <span className="timeline-stop-open-hint" id={detailHintId}>查看地点详情、营业信息与替换入口</span>
                       </span>
                       <StopImage stop={stop} galleryImage={presentation?.gallery[0]} variant="thumb" />
                     </button>
                   </div>
                   <div className="timeline-stop-actions"><span className={`poi-replace-hint${canReplace ? "" : " locked"}`} role="group" aria-label={!canReplace ? "先正式选择当前方案，才能替换单站" : undefined} tabIndex={!canReplace ? 0 : undefined} title={canReplace ? "替换此站，其他站点保持不变" : "先正式选择当前方案，才能修改单站"}><button className="poi-action poi-replace timeline-stop-replace" type="button" data-replacement-stop-index={index} disabled={!canReplace} onClick={(event) => onReplaceStop(plan, index, event.currentTarget)}><ArrowLeftRight size={14} aria-hidden="true" />替换此站</button></span></div>
                 </div>
               </div>
             </div>
           </li>;
         })}
         {returnLegIndex !== null ? <li className="timeline-item"><TimelineRoute leg={plan.route_legs[returnLegIndex]} index={returnLegIndex} active={activeLegIndex === returnLegIndex} kind="return" onSelect={onMapRoute} /></li> : null}
      </ol>
      <section className="trip-route-overview" aria-label="整段路线概览">
         <Route size={17} aria-hidden="true" />
         <div><h3>整段路线</h3><span>全程 {formatDistance(totalDistance(plan))} km · 交通约 {routeDuration} 分钟</span></div>
         <button type="button" onClick={() => onMapRoute(activeLegIndex ?? 0)} disabled={plan.route_legs.length === 0}>查看地图<ChevronRight size={15} /></button>
      </section>
    </div>
  );
}

function TimelineRoute({ leg, index, active, kind, onSelect }: { leg: RouteLeg; index: number; active: boolean; kind: "start" | "next" | "return"; onSelect: (legIndex: number) => void }) {
  const label = routeActionLabel(leg, kind);
  const source = `${routeProviderLabel(leg)}${leg.degraded && leg.degraded_reason ? ` · 降级：${leg.degraded_reason}` : ""}`;
  const direction = kind === "return" ? "返程" : kind === "start" ? "前往首站" : "前往下一站";
  return <div className={`timeline-route ${active ? "route-focused" : ""}`} data-route-leg-index={index}>
    <span className="route-mode-icon"><RouteModeIcon mode={leg.mode} size={15} /></span>
    <span className="timeline-route-copy" title={`${leg.start}–${leg.end} · ${leg.origin_name} → ${leg.destination_name} · ${formatDistance(leg.distance_km)} km · ${source}`}>
      <strong>{direction} · {routeModeLabel(leg.mode)}约 {leg.duration_minutes} 分钟 · {formatDistance(leg.distance_km)} km</strong>
      <small className="timeline-route-source">{leg.degraded ? "演示估算 · 出发前请复核" : routeProviderLabel(leg)}</small>
    </span>
    <button type="button" aria-label={label} aria-pressed={active} onClick={() => onSelect(index)}>地图<ChevronRight size={13} aria-hidden="true" /></button>
  </div>;
}

function displayIfKnown(value: string | null | undefined): string | null {
  const trimmed = value?.trim();
  if (!trimmed) return null;
  return /^(unknown|unk|未知|不详|n\/a)$/i.test(trimmed) ? "待确认" : trimmed;
}

function referencePriceLabel(value: number | null): string | null {
  if (value === null || !Number.isFinite(value)) return null;
  return `¥${value}（展示参考）`;
}

function PoiDetailPage({ stop, stopIndex, routeLeg, presentation, canReplace, onReplace }: { stop: Stop; stopIndex: number; routeLeg?: RouteLeg; presentation?: PoiPresentation; canReplace: boolean; onReplace: (triggerElement: HTMLElement) => void }) {
  const titleRef = useRef<HTMLHeadingElement>(null);
  const galleryImage = presentation?.gallery[0];
  const photoMetadata = galleryImage ?? stop.image;
  const photoKind = galleryImage?.kind ?? (stop.image ? "source" : undefined);
  const mapQuery = [stop.name, displayIfKnown(presentation?.address)].filter(Boolean).join(" ");
  const mapUrl = `https://ditu.amap.com/search?query=${encodeURIComponent(mapQuery)}`;
  const category = displayIfKnown(presentation?.category_label) ?? (stop.type === "restaurant" ? "餐饮" : stop.type === "cafe" ? "咖啡" : stop.type === "dessert" ? "甜品" : "活动");
  const businessAddress = [displayIfKnown(presentation?.business_area), displayIfKnown(presentation?.address)].filter(Boolean).join(" · ");
  const tags = [...new Set([...(presentation?.scene_tags ?? []), ...(presentation?.facility_tags ?? [])].filter(Boolean))];
  const guidanceNotes = [
    ...[presentation?.weather_suitability, presentation?.child_suitability].map((item) => ({ text: displayIfKnown(item), kind: "tip" as const })),
    ...(presentation?.risk_tips ?? []).map((item) => ({ text: displayIfKnown(item), kind: "risk" as const })),
  ].filter((item): item is { text: string; kind: "tip" | "risk" } => Boolean(item.text));
  const uniqueGuidanceNotes = [...new Map(guidanceNotes.map((item) => [item.text, item])).values()];
  const opening = [displayIfKnown(presentation?.opening_status), displayIfKnown(presentation?.opening_hours_display)].filter(Boolean).join(" · ");
  const reservation = [displayIfKnown(presentation?.reservation_requirement), displayIfKnown(presentation?.booking_mode)].filter(Boolean).join(" · ");
  const queue = displayIfKnown(presentation?.queue_profile);

  useLayoutEffect(() => { titleRef.current?.focus(); }, [stop.resource_id]);

  return <article className="poi-page">
    <div className="poi-page-hero">
      <StopImage stop={stop} galleryImage={galleryImage} variant="detail" />
      <span className="poi-hero-number">第 {stopIndex + 1} 站</span>
      {photoKind === "reference" ? <span className="poi-image-kind">同类实景参考 · 非该地点实拍</span> : photoKind === "illustrative" ? <span className="poi-image-kind">场景示意 · 非门店实拍</span> : null}
    </div>
    <p className="poi-image-attribution">
      {photoKind === "reference" ? "同类场景参考图片，不代表该地点实拍。" : photoKind === "illustrative" ? "当前图片为场景示意，不代表该地点实拍。" : photoKind === "source" ? "来源图片；具体拍摄与门店对应关系以来源页说明为准。" : "暂无可靠图片，未以其他地点照片替代。"}
      {photoMetadata ? <>
        {photoMetadata.source_uri && /^https?:\/\//i.test(photoMetadata.source_uri) ? <> · <a href={photoMetadata.source_uri} target="_blank" rel="noreferrer noopener">图片来源</a></> : null}
        {photoMetadata.author || photoMetadata.attribution ? ` · ${photoMetadata.author ?? photoMetadata.attribution}` : null}
        {photoMetadata.license ? <> · {photoMetadata.license_uri && /^https?:\/\//i.test(photoMetadata.license_uri) ? <a href={photoMetadata.license_uri} target="_blank" rel="noreferrer noopener">{photoMetadata.license}</a> : photoMetadata.license}</> : null}
      </> : null}
    </p>
    <div className="poi-page-title"><span className="poi-category-chip">{category}</span><h2 ref={titleRef} tabIndex={-1}>{stop.name}</h2><p>{businessAddress || "地址待确认"}</p></div>
    <div className="poi-visit-metrics"><span><Clock3 size={15} aria-hidden="true" />到达 {stop.start}</span><span>离开 {stop.end}</span><span>停留 {stop.duration_minutes} 分钟</span></div>

    {presentation && displayIfKnown(presentation.description) ? <section className="poi-detail-section"><h3>地点介绍</h3><p>{presentation.description}</p></section> : null}
    <section className="poi-facts" aria-label="到访信息"><h3>到访信息</h3><dl>
      <div><dt><CircleDollarSign size={14} aria-hidden="true" />当前方案站点费用</dt><dd>{priceLabel(stop)}</dd></div>
      {presentation?.reference_avg_price !== undefined ? <div><dt><CircleDollarSign size={14} aria-hidden="true" />展示参考人均</dt><dd>{referencePriceLabel(presentation.reference_avg_price) ?? "未提供"}</dd></div> : null}
      {opening ? <div><dt><Store size={14} aria-hidden="true" />营业展示</dt><dd>{opening}</dd></div> : null}
      {reservation ? <div><dt><Info size={14} aria-hidden="true" />预约与入场</dt><dd>{reservation}</dd></div> : null}
      {queue ? <div><dt><Info size={14} aria-hidden="true" />客流提示</dt><dd>{queue}</dd></div> : null}
      {routeLeg ? <div><dt><Route size={14} aria-hidden="true" />交通衔接</dt><dd><span>{routeModeLabel(routeLeg.mode)} · {routeLeg.duration_minutes} 分钟 · {formatDistance(routeLeg.distance_km)} km</span><small>{routeLeg.origin_name} 出发 · {routeLeg.degraded ? "估算路段，出发前请复核" : routeProviderLabel(routeLeg)}</small></dd></div> : null}
    </dl></section>
    {tags.length ? <section className="poi-detail-section"><h3>地点标签</h3><div className="poi-tags">{tags.slice(0, 10).map((tag) => <span key={tag}><Tag size={12} aria-hidden="true" />{tag}</span>)}</div></section> : null}
    {uniqueGuidanceNotes.length ? <section className="poi-detail-section"><h3>到访提示与风险</h3><ul className="poi-risk-list">{uniqueGuidanceNotes.map(({ text, kind }) => <li className={`poi-guidance-${kind}`} key={`${kind}-${text}`}>{kind === "tip" ? <Info size={14} aria-hidden="true" /> : <CircleAlert size={14} aria-hidden="true" />}<strong>{kind === "tip" ? "参考" : "留意"}</strong><span>{text}</span></li>)}</ul></section> : null}
    {!presentation ? <p className="poi-unavailable-note"><Info size={15} aria-hidden="true" />当前预览响应未附 POI 展示快照；以上仅使用方案站点与目录来源信息。</p> : null}
    <section className="poi-detail-actions" aria-label="地点操作">
      <a className="poi-action" href={mapUrl} target="_blank" rel="noopener noreferrer"><ExternalLink size={14} aria-hidden="true" />在高德地图中搜索</a>
      <span className={`poi-replace-hint${canReplace ? "" : " locked"}`} role="group" aria-label={!canReplace ? "先正式选择当前方案，才能替换单站" : undefined} tabIndex={!canReplace ? 0 : undefined} title={canReplace ? "替换此站，其他站点保持不变" : "先正式选择当前方案，才能修改单站"}><button className="poi-action poi-replace" type="button" data-replacement-stop-index={stopIndex} disabled={!canReplace} onClick={(event) => onReplace(event.currentTarget)}><ArrowLeftRight size={14} aria-hidden="true" />替换此站</button></span>
    </section>
    <p className="poi-map-link-note">地图链接按地点名称和当前地址构造；打开链接不代表位置已实时核实。</p>
    <details className="poi-data-disclosure"><summary><span>数据与来源说明</span><ChevronDown size={16} className="disclosure-chevron" aria-hidden="true" /></summary><p>{presentation ? presentation.data_notice : "当前预览响应未提供 PoiPresentation；详情字段仅来自方案 Stop 和目录来源。"}{galleryImage?.kind === "illustrative" ? " 图片为场景示意，不是门店实拍。" : ""}</p><div className="poi-source"><Database size={14} aria-hidden="true" />{catalogSourceLabel(stop.source)} · 收集于 {stop.source.collected_at.slice(0, 10)}</div></details>
  </article>;
}

function StopImage({ stop, galleryImage, variant = "detail" }: { stop: Stop; galleryImage?: PoiPresentation["gallery"][number]; variant?: "hero" | "detail" | "thumb" }) {
  const [failed, setFailed] = useState(false);
  const image = galleryImage?.url ?? stop.image?.url;
  if (!image || failed) {
    const label = stop.category_tags[0] || (stop.type === "restaurant" ? "餐饮" : "活动");
    return <div className={`stop-image ${variant} placeholder ${stop.type}`} aria-label={`${stop.name}暂无可靠图片`}><span>{stop.type === "restaurant" ? <Utensils size={21} /> : <ImageIcon size={21} />}{label}{variant === "hero" ? <small>暂无可靠图片</small> : null}</span></div>;
  }
  const imageElement = <img className={`stop-image ${variant}`} src={image} alt={galleryImage?.alt ?? stop.name} loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} />;
  return galleryImage?.kind === "reference"
    ? <span className={`stop-image-frame ${variant}`}>{imageElement}<span className="stop-image-reference-badge">同类参考</span></span>
    : imageElement;
}

function routeProviderLabel(leg: RouteLeg): string {
  const provider = { live: "实时模式", record: "记录模式", replay: "回放模式", mock: "模拟模式" }[leg.provider_mode];
  const verified = leg.verified_at ? ` · 核验于 ${safeDateTime(leg.verified_at)}` : "";
  return `${routeSourceLabel(leg.source)} · ${provider}${verified}`;
}

function MapPanel({ plan, activeLegIndex, onOpenPoi, onFocusRoute, onTimelineRoute }: { plan?: Plan; activeLegIndex: number | null; onOpenPoi: (stopIndex: number, triggerElement?: HTMLElement | null) => void; onFocusRoute: (legIndex: number) => void; onTimelineRoute: (legIndex: number) => void }) {
  const sources = plan ? [...new Set(plan.route_legs.map((leg) => routeSourceLabel(leg.source)))] : [];
  const sourceSummary = sources.length === 1 ? sources[0] : sources.length > 1 ? "多来源路线" : "路线摘要";
  if (!plan) return <DetailEmpty icon={<MapIcon size={24} />} title="路线将在这里展开" text="生成方案后，可查看地图状态、路线文字与站点详情。" />;
  const returnLegIndex = plan.route_legs.length > plan.stops.length ? plan.route_legs.length - 1 : null;
  return <div className="map-detail">
    <AmapPlanMap plan={plan} activeLegIndex={activeLegIndex} onFocusRoute={onFocusRoute} onOpenPoi={(stopIndex) => onOpenPoi(stopIndex, null)} />
    <div className="map-page-heading"><div><strong>地图与路线</strong><p>{sourceSummary} · {totalDistance(plan).toFixed(1)} km 全程路线</p></div><span className="map-mode-label">路线可信度见分段来源</span></div>
    <section className="map-route-list" aria-label="路线分段"><div className="detail-section-heading"><h3>路线分段</h3><span>{plan.route_legs.length} 段</span></div>
      {plan.route_legs.map((leg, index) => {
        const kind = returnLegIndex === index ? "return" : index === 0 ? "start" : "next";
        return <div className={`map-route-entry ${activeLegIndex === index ? "active" : ""}`} key={`${leg.origin_name}-${leg.destination_name}-${index}`}>
          <button className="map-route-focus" type="button" aria-pressed={activeLegIndex === index} aria-label={`聚焦第 ${index + 1} 段路线：${leg.origin_name} 到 ${leg.destination_name}`} onClick={() => onFocusRoute(index)}><RouteModeIcon mode={leg.mode} size={17} /><span><strong>{kind === "return" ? "返程" : kind === "start" ? "出发路线" : "站间路线"} · {leg.origin_name} → {leg.destination_name}</strong><small>{leg.start}–{leg.end} · {formatDistance(leg.distance_km)} km · {leg.duration_minutes} 分钟</small><small>{routeProviderLabel(leg)}{leg.degraded ? ` · 降级：${leg.degraded_reason || "非实时路线"}` : ""}</small></span></button>
          <button className="map-route-timeline" type="button" onClick={() => onTimelineRoute(index)}>在行程中定位</button>
        </div>;
      })}
      {!plan.route_legs.length ? <p className="map-fallback-note">响应没有提供路线段，未生成路线或坐标；可通过上方站点列表打开 POI。</p> : null}
    </section>
    <section className="map-stop-list" aria-label="途经地点"><div className="detail-section-heading"><h3>途经地点</h3><span>{plan.stops.length} 站</span></div>
      {plan.stops.length ? plan.stops.map((stop, index) => {
        const inbound = plan.route_legs[index];
        return <button className="map-stop-row" type="button" key={stop.resource_id} data-inspector-stop-index={index} aria-label={`从地图列表打开第 ${index + 1} 站 ${stop.name} 详情`} onClick={(event) => onOpenPoi(index, event.currentTarget)}><span className="map-stop-index">{index + 1}</span><span><strong>{stop.name}</strong><small>{stop.start}–{stop.end} · 停留 {stop.duration_minutes} 分钟{inbound ? ` · ${routeModeLabel(inbound.mode)} ${inbound.duration_minutes} 分钟` : ""}</small></span><ChevronRight size={17} aria-hidden="true" /></button>;
      }) : <p className="map-fallback-note">当前方案没有站点；路线文字仅按响应实际提供的路段显示。</p>}
    </section>
  </div>;
}

type RetrievalEvidence = NonNullable<AgentResponse["retrieval_evidence"]>[number];

function evidenceBelongsToPlan(evidence: RetrievalEvidence, resourceIds: Set<string>): boolean {
  if (evidence.source_type === "user_message") return true;
  const sourceRef = evidence.source_ref ?? "";
  return [...resourceIds].some((resourceId) => (
    sourceRef === resourceId
    || sourceRef === `profile:${resourceId}`
    || evidence.evidence_id.startsWith(`poi.${resourceId}.`)
  ));
}

function warningSourceLabel(warning: PlanWarning): string {
  if (warning.source) return `来源：${warning.source}`;
  if (warning.code.startsWith("route")) return "来源：路线核验";
  if (warning.code.startsWith("catalog")) return "来源：Catalog 约束";
  if (warning.code.includes("availability")) return "来源：营业/可用性核验";
  return "来源：后端核验结果";
}

function warningTitle(warning: PlanWarning): string {
  if (warning.code.startsWith("route")) return "路线事实需要确认";
  if (warning.code.startsWith("catalog")) return "地点数据需要确认";
  if (warning.code.includes("weather")) return "天气事实需要确认";
  return "需要确认";
}

function VerificationOverview({ response, plan, selectedFacts }: { response: AgentResponse; plan?: Plan; selectedFacts: ProviderFact[] }) {
  const budget = response.constraint_summary.find((item) => item.field === "budget_per_person");
  const hasAvailability = selectedFacts.some((fact) => fact.kind === "availability");
  const routeSources = plan ? [...new Set(plan.route_legs.map((leg) => routeSourceLabel(leg.source)))] : [];
  const routeDegraded = Boolean(plan?.route_legs.some((leg) => leg.degraded));
  return (
    <section className="evidence-group verification-overview" aria-label="核验概览">
      <div className="evidence-group-heading"><h3>核验概览</h3><span>只显示后端已有事实</span></div>
      <div className="verification-summary-grid">
        <div><strong>时间线</strong><span>{plan?.stops.length ? "已生成时间安排" : "未提供"}</span></div>
        <div><strong>路线</strong><span>{routeSources.length ? `${routeSources.join(" / ")}${routeDegraded ? " · 含降级" : ""}` : "未提供"}</span></div>
        <div><strong>预算约束</strong><span>{budget ? `${displayConstraint(budget)} · 已记录` : "未提供"}</span></div>
        <div><strong>营业与可用性</strong><span>{hasAvailability ? "已提供动态事实" : "未提供"}</span></div>
      </div>
    </section>
  );
}

function EvidencePanel({ response, plan }: { response: AgentResponse | null; plan?: Plan }) {
  if (!response) return <DetailEmpty icon={<ShieldCheck size={24} />} title="核验将在这里汇总" text="天气、路线、营业与数据来源会集中展示，不挤占方案比较区。" />;
  const resourceIds = new Set(plan?.stops.map((stop) => stop.resource_id) ?? []);
  const adviceForPlan = response.recommendation_advice?.plans.find((item) => item.plan_id === plan?.plan_id);
  const supportingIds = new Set(adviceForPlan?.supporting_evidence_ids ?? []);
  const evidence = (response.retrieval_evidence ?? []).filter((item) => (
    supportingIds.size ? supportingIds.has(item.evidence_id) : evidenceBelongsToPlan(item, resourceIds)
  ));
  const selectedFacts = response.provider_facts.filter((fact) => (
    fact.kind !== "availability" || resourceIds.has(fact.resource_id)
  ));
  const verifiedFacts = selectedFacts.filter((fact) => !fact.degraded);
  const degradedFacts = selectedFacts.filter((fact) => fact.degraded);
  const sources = plan ? [...new Map(plan.stops.map((stop) => [stop.source.source_uri, stop.source])).values()] : [];
  const warnings = [...new Map(
    (response.warnings ?? [])
      .filter((warning) => warning.plan_id === null || warning.plan_id === plan?.plan_id)
      .map((warning) => [`${warning.code}|${warning.resource_id ?? ""}|${warning.source ?? ""}`, warning] as const),
  ).values()];
  const degradedLegs = plan?.route_legs.filter((leg) => leg.degraded) ?? [];
  const hasRisks = degradedFacts.length || degradedLegs.length || warnings.length;
  const runtimeDecisions = response.runtime_decisions ?? [];
  return (
    <div className="evidence-panel">
      <div className="detail-title"><span>可信状态</span><h2>核验</h2><p>先看当前方案的核验概览，再查看待确认风险和事实依据。</p></div>
      <VerificationOverview response={response} plan={plan} selectedFacts={selectedFacts} />

      {hasRisks ? (
        <section className="evidence-group risk-group" aria-label="需要确认">
          <div className="evidence-group-heading"><h3>需要确认</h3><span>只显示当前方案相关风险</span></div>
          <div className="evidence-list">
            {degradedFacts.map((fact, index) => <ProviderEvidence key={`degraded-${fact.kind}-${index}`} fact={fact} />)}
            {degradedLegs.map((leg, index) => <article className="evidence-row warning" key={`degraded-leg-${leg.origin_name}-${leg.destination_name}-${index}`}><span className="evidence-icon"><Route size={17} /></span><div><strong>路线使用降级数据</strong><p>{leg.origin_name} → {leg.destination_name} · {leg.distance_km} km / {leg.duration_minutes} 分钟</p><small>来源：{routeSourceLabel(leg.source)} · {leg.degraded_reason ?? "原因未提供"}</small></div></article>)}
            {warnings.map((warning) => <article className="evidence-row warning" key={`${warning.code}-${warning.resource_id ?? "all"}-${warning.source ?? "none"}`}><span className="evidence-icon"><CircleAlert size={17} /></span><div><strong>{warningTitle(warning)}</strong><p>{warning.message}</p><small>{warningSourceLabel(warning)}{warning.stale ? " · 数据可能过期" : ""}{warning.resource_id ? ` · 资源 ${warning.resource_id}` : ""}</small></div></article>)}
          </div>
        </section>
      ) : null}

      {import.meta.env.VITE_SHOW_DEVELOPER_DETAILS === "true" ? <details className="evidence-group runtime-call-group">
        <summary className="evidence-group-heading"><h3>模型调用</h3><span>开发者 · 逐阶段后端记录</span><ChevronDown size={16} className="disclosure-chevron" aria-hidden="true" /></summary>
        <div className="evidence-list">
          {runtimeDecisions.map((decision, index) => <RuntimeCallSummary key={`runtime-call-${decision.stage}-${index}`} decision={decision} />)}
          {!runtimeDecisions.length ? <p className="evidence-empty">本轮未提供逐阶段模型调用记录。</p> : null}
        </div>
      </details> : null}

      <details className="evidence-group evidence-data-disclosure">
        <summary className="evidence-group-heading"><h3>数据与证据</h3><span>默认折叠 · 当前方案相关</span><ChevronDown size={16} className="disclosure-chevron" aria-hidden="true" /></summary>
        <div className="evidence-list">
          {evidence.map((item) => <article className="evidence-row" key={item.evidence_id}><span className="evidence-icon"><ShieldCheck size={17} /></span><div><strong>{item.summary}</strong><p>{item.source_type === "user_message" ? "用户关键需求 · 非外部事实" : item.source_type === "poi_profile" ? "来自 POI Profile" : "来自已核验事实"}</p><small>{item.source_field} · 置信度 {(item.confidence * 100).toFixed(0)}%</small></div></article>)}
          {verifiedFacts.map((fact, index) => <ProviderEvidence key={`${fact.kind}-${index}`} fact={fact} />)}
          {sources.map((source) => <article className="evidence-row" key={source.source_uri}><span className="evidence-icon"><Database size={17} /></span><div><strong>POI 目录 · {source.source_name}</strong><p>{source.source_license} · {source.verification_status === "verified" ? "已核验" : source.verification_status === "stale" ? "需复核" : "未核验"}</p><small>采集于 {safeDateTime(source.collected_at)}</small></div></article>)}
          {!evidence.length && !verifiedFacts.length && !sources.length ? <p className="evidence-empty">当前方案没有额外的事实依据快照。</p> : null}
        </div>
      </details>
      <DeveloperDetails response={response} />
    </div>
  );
}

function DeveloperDetails({ response }: { response: AgentResponse }) {
  if (import.meta.env.VITE_SHOW_DEVELOPER_DETAILS !== "true") return null;
  const hasSearchDetails = response.search_mode || response.search_expansions !== null && response.search_expansions !== undefined;
  if (!response.retrieval_mode && !hasSearchDetails) return null;
  return (
    <details className="developer-details">
      <summary><span>开发者详情</span><small>检索和搜索诊断</small><ChevronDown size={16} className="disclosure-chevron" aria-hidden="true" /></summary>
      <div className="developer-detail-list">
        {response.retrieval_mode ? <div className="developer-detail-line"><strong>检索</strong><span>{response.retrieval_mode} · {response.retrieval_index_version ?? "索引版本未提供"}</span></div> : null}
        {hasSearchDetails ? <div className="developer-detail-line"><strong>搜索</strong><span>{response.search_mode ?? "未提供"} · 扩展 {response.search_expansions ?? "—"} · finalists {response.search_finalist_count ?? "—"}</span></div> : null}
      </div>
    </details>
  );
}

function ProviderEvidence({ fact }: { fact: ProviderFact }) {
  const title = fact.kind === "weather" ? `天气 · ${fact.city}${fact.district}` : fact.kind === "geocoding" ? `地点解析 · ${fact.request.location_text}` : `动态可用性 · ${fact.resource_id}`;
  const description = fact.kind === "weather" ? `${fact.date} · ${fact.condition}${fact.temperature_c !== null ? ` · ${fact.temperature_c}℃` : ""} · 降水 ${fact.precipitation_mm}mm` : fact.kind === "geocoding" ? `${fact.resolution}${fact.address ? ` · ${fact.address}` : ""}` : `${fact.status}${fact.reason ? ` · ${fact.reason}` : ""}`;
  return <article className={`evidence-row ${fact.degraded ? "warning" : ""}`}><span className="evidence-icon">{fact.kind === "weather" ? <CloudSun size={17} /> : fact.kind === "geocoding" ? <MapPin size={17} /> : <Store size={17} />}</span><div><strong>{title}</strong><p>{description}</p><small>{providerSourceLabel(fact)} · 核验于 {safeDateTime(fact.verified_at)}{fact.degraded ? ` · 降级：${fact.degraded_reason}` : ""}</small></div></article>;
}

function DetailEmpty({ icon, title, text }: { icon: ReactNode; title: string; text: string }) {
  return <div className="detail-empty"><div>{icon}</div><h2>{title}</h2><p>{text}</p></div>;
}

function MobileSheet({ open, onOpenChange, title, description, children, contentRef, contentClassName, compactHeader = false, onContentScroll, showScrollToTop = false, onScrollToTop }: { open: boolean; onOpenChange: (open: boolean) => void; title: string; description: string; children: ReactNode; contentRef?: RefObject<HTMLDivElement | null>; contentClassName?: string; compactHeader?: boolean; onContentScroll?: () => void; showScrollToTop?: boolean; onScrollToTop?: () => void }) {
  return <Dialog.Root open={open} onOpenChange={onOpenChange}><Dialog.Portal><Dialog.Backdrop className="sheet-backdrop" /><Dialog.Popup className="mobile-sheet"><header className={`sheet-header ${compactHeader ? "sheet-header-compact" : ""}`}><div><Dialog.Title>{title}</Dialog.Title><Dialog.Description className={compactHeader ? "sr-only" : undefined}>{description}</Dialog.Description></div><Dialog.Close className="sheet-close" aria-label="关闭"><X size={20} /></Dialog.Close></header><div className={`sheet-content ${contentClassName ?? ""}`} ref={contentRef} onScroll={onContentScroll}>{children}</div>{showScrollToTop && onScrollToTop ? <button className="inspector-scroll-to-top mobile" type="button" aria-label="返回详情顶部" title="返回顶部" onClick={onScrollToTop}><ChevronUp size={20} aria-hidden="true" /></button> : null}</Dialog.Popup></Dialog.Portal></Dialog.Root>;
}

export default App;
