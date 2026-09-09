import { Dialog } from "@base-ui/react/dialog";
import { FormEvent, type MouseEvent, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bus,
  CalendarDays,
  Car,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
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
  Plus,
  ReceiptText,
  Route,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  Star,
  Store,
  Tag,
  Utensils,
  X,
} from "lucide-react";

import { createSession, getSession, listSessions, selectPlan, sendMessage } from "./api";
import { AmapPlanMap } from "./AmapPlanMap";
import type {
  AgentResponse,
  Assumption,
  ChatMessage,
  ConversationCommand,
  ConstraintSummaryItem,
  Plan,
  PlanWarning,
  PoiPresentation,
  ProviderFact,
  ReplacementCriterion,
  RouteLeg,
  SessionSummary,
  SessionView,
  Stop,
} from "./types";

const SUGGESTIONS = [
  "今天下午出去玩，别太远",
  "周六和朋友聚一下，人均150",
  "安排一个轻松的约会，想吃甜品",
];

const ASSUMPTION_LABELS: Record<string, string> = {
  date: "日期",
  time_window: "时间",
  departure_at: "准时出发",
  exact_stop_count: "停靠站数",
  required_stop_roles: "必选角色",
  max_distance_km: "距离",
  total_distance_km: "全程距离",
  return_by: "最晚到家",
  budget_per_person: "预算",
  party: "同行人",
  location: "出发地",
};

const SESSION_QUERY_KEY = "session";
type InspectorTab = "trip" | "map" | "orders" | "evidence";

type ReplacementDraft = {
  planVersionId: string;
  planId: string;
  stopIndex: number;
  resourceId: string;
  role: Stop["role"];
  rawText: string;
  criteria: ReplacementCriterion[];
};

type ReplacementPreset = "similar" | "shorter" | "quiet" | "chatty" | "family";

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

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    active: "等待开始",
    running: "规划中",
    needs_input: "待补充信息",
    completed: "已生成方案",
    failed: "上次失败",
  };
  return labels[status] ?? status;
}

function normalizeAgentResponse(response: Partial<AgentResponse>): AgentResponse {
  return {
    status: response.status ?? "completed",
    reply: response.reply ?? "",
    question: response.question ?? null,
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
    conversation_command: response.conversation_command ?? null,
    plan_diff: response.plan_diff ?? null,
    plan_diffs: response.plan_diffs ?? (response.plan_diff ? [response.plan_diff] : []),
  };
}

function responsesFromSession(view: SessionView): AgentResponse[] {
  if (view.response_history?.length) return view.response_history.map(normalizeAgentResponse);
  if (view.latest_response) return [normalizeAgentResponse(view.latest_response)];
  if (!view.plans.length) return [];
  return [{
    status: "completed", reply: "", question: null, assumptions: [], constraint_summary: [],
    plans: view.plans, conflict: null, provider_facts: [], catalog_violations: [],
    catalog_warnings: [], warnings: [], poi_presentations: [],
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

function constraintSourceLabel(item: ConstraintSummaryItem): string {
  if (item.source === "user_inferred") return item.evidence ? `根据“${item.evidence}”推断` : "根据需求推断";
  if (item.source === "default_rule") return "系统默认";
  if (item.source === "user_explicit") return "来自你的需求";
  if (item.source === "system_context") return "本次会话信息";
  if (item.source === "real_tool") return "外部服务确认";
  return "已确认信息";
}

function strategyLabel(strategy: string): string {
  const labels: Record<string, string> = {
    balanced: "管家首选",
    low_cost: "预算友好",
    low_travel: "少走路",
    experience: "体验优先",
    family_safe: "亲子稳妥",
    weather_safe: "雨天从容",
  };
  return labels[strategy] ?? strategy;
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

function planPriceLabel(plan: Plan): string {
  if (plan.price_status === "incomplete") return `全员已知费用 ¥${plan.total_price} + 未知价格`;
  if (plan.price_status === "estimated") return `全员约 ¥${plan.total_price}`;
  return `全员合计 ¥${plan.total_price}`;
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

function latestReturnConstraint(response: AgentResponse): ConstraintSummaryItem | null {
  return response.constraint_summary.find((item) => item.field === "return_by" && item.source !== "default_rule") ?? null;
}

function departureConstraint(response: AgentResponse): ConstraintSummaryItem | null {
  return response.constraint_summary.find((item) => item.field === "departure_at") ?? null;
}

function planRationale(plan: Plan): string[] {
  if (plan.highlights.length) return plan.highlights.slice(0, 3);
  return [`这套方案以“${strategyLabel(plan.strategy)}”为主要取向，时间线和已知硬约束已经过规划校验。`];
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

function App() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [recentSessions, setRecentSessions] = useState<SessionSummary[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [error, setError] = useState("");
  const [response, setResponse] = useState<AgentResponse | null>(null);
  const [inspectedResponse, setInspectedResponse] = useState<AgentResponse | null>(null);
  const [viewedPlanId, setViewedPlanId] = useState<string | null>(null);
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);
  const [focusedLegIndex, setFocusedLegIndex] = useState<number | null>(null);
  const [rightTab, setRightTab] = useState<InspectorTab>("trip");
  const [inspectorCollapsed, setInspectorCollapsed] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false);
  const isMobile = useMediaQuery("(max-width: 900px)");
  const [failedRequest, setFailedRequest] = useState<{ sessionId: string; content: string; requestId: string; conversationCommand?: ConversationCommand } | null>(null);
  const [replacementDraft, setReplacementDraft] = useState<ReplacementDraft | null>(null);
  const conversationRef = useRef<HTMLElement>(null);
  const conversationEndRef = useRef<HTMLDivElement>(null);
  const sessionIdRef = useRef<string | null>(null);
  const activePlanVersionIdRef = useRef<string | null>(null);

  const detailResponse = inspectedResponse ?? response;

  const inspectorPlan = useMemo(
    () => detailResponse?.plans.find((plan) => plan.plan_id === viewedPlanId) ?? detailResponse?.plans[0],
    [detailResponse, viewedPlanId],
  );

  const refreshRecentSessions = useCallback(async () => {
    try {
      setRecentSessions(await listSessions(20));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "历史会话加载失败");
    }
  }, []);

  const openSession = useCallback(async (nextSessionId: string, navigate = true) => {
    setRestoring(true);
    setError("");
    setFailedRequest(null);
    try {
      const view = await getSession(nextSessionId);
      const restoredResponses = responsesFromSession(view);
      const restoredResponse = restoredResponses[restoredResponses.length - 1] ?? null;
      const restoredActiveResponse = activePlanResponse(view, restoredResponses);
      const restoredSelectedId = view.selected_plan_id ?? null;
      sessionIdRef.current = view.session_id;
      activePlanVersionIdRef.current = view.active_plan_version_id ?? null;
      setSessionId(view.session_id);
      setMessages(attachResponsesToMessages(view.messages, restoredResponses));
      setResponse(restoredResponse);
      setInspectedResponse(restoredActiveResponse ?? restoredResponse);
       setSelectedPlanId(restoredSelectedId);
       setReplacementDraft(null);
      const restoredViewedId = restoredSelectedId && restoredActiveResponse?.plans.some(
        (plan) => plan.plan_id === restoredSelectedId,
      )
        ? restoredSelectedId
        : restoredActiveResponse?.plans[0]?.plan_id ?? null;
      setViewedPlanId(restoredViewedId);
      setFocusedLegIndex(null);
      setRightTab("trip");
      if (navigate && sessionIdFromUrl() !== view.session_id) updateSessionUrl(view.session_id);
    } catch (reason) {
      sessionIdRef.current = null;
      activePlanVersionIdRef.current = null;
      setSessionId(null);
      setMessages([]);
      setResponse(null);
      setInspectedResponse(null);
      setSelectedPlanId(null);
      setViewedPlanId(null);
      setReplacementDraft(null);
      setFocusedLegIndex(null);
      if (sessionIdFromUrl() === nextSessionId) updateSessionUrl(null, true);
      setError(reason instanceof Error ? reason.message : "会话恢复失败");
    } finally {
      setRestoring(false);
      setHistoryOpen(false);
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
        sessionIdRef.current = null;
        activePlanVersionIdRef.current = null;
        setSessionId(null);
        setMessages([]);
        setResponse(null);
        setInspectedResponse(null);
        setSelectedPlanId(null);
        setViewedPlanId(null);
        setReplacementDraft(null);
        setFocusedLegIndex(null);
        setFailedRequest(null);
        setError("");
      }
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, [openSession, refreshRecentSessions]);

  useEffect(() => {
    if (restoring) return;
    const latestMessage = messages[messages.length - 1];
    if (latestMessage?.role === "assistant") {
      const turns = conversationRef.current?.querySelectorAll<HTMLElement>(".message-list .message");
      turns?.[turns.length - 1]?.scrollIntoView?.({ block: "start", behavior: "smooth" });
      return;
    }
    conversationEndRef.current?.scrollIntoView?.({ block: "end", behavior: loading ? "smooth" : "auto" });
  }, [loading, messages, restoring]);

  async function submit(content: string, conversationCommand?: ConversationCommand) {
    const trimmed = content.trim() || (
      replacementDraft
        ? `更换第 ${replacementDraft.stopIndex + 1} 站`
        : ""
    );
    if (!trimmed || loading || restoring) return;
    setLoading(true);
    setError("");
    setInput("");
    let activeSession = sessionId;
    try {
      if (!activeSession) {
        activeSession = await createSession();
        sessionIdRef.current = activeSession;
        activePlanVersionIdRef.current = null;
        setSessionId(activeSession);
        updateSessionUrl(activeSession);
      }
      const isRetry = (
        failedRequest?.sessionId === activeSession
        && failedRequest.content === trimmed
        && JSON.stringify(failedRequest.conversationCommand ?? null)
          === JSON.stringify(conversationCommand ?? null)
      );
      const requestId = isRetry ? failedRequest.requestId : crypto.randomUUID();
      if (!isRetry) setMessages((current) => [...current, { id: crypto.randomUUID(), role: "user", content: trimmed }]);
      setFailedRequest({ sessionId: activeSession, content: trimmed, requestId, conversationCommand });
      const nextResponse = await sendMessage(activeSession, trimmed, requestId, conversationCommand);
      setFailedRequest(null);
      setResponse(nextResponse);
      if (nextResponse.plans.length) {
        activePlanVersionIdRef.current = nextResponse.plan_version_id ?? null;
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
      setError(reason instanceof Error ? reason.message : "请求失败，请稍后重试");
      if (activeSession) await refreshRecentSessions();
    } finally {
      setLoading(false);
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const command = replacementDraft
      ? replacementCommandFromDraft(replacementDraft, replacementDraft.criteria, input)
      : undefined;
    void submit(input, command);
  }

  function resetSession() {
    sessionIdRef.current = null;
    activePlanVersionIdRef.current = null;
    setSessionId(null);
    setMessages([]);
    setResponse(null);
    setInspectedResponse(null);
    setSelectedPlanId(null);
    setViewedPlanId(null);
    setReplacementDraft(null);
    setFocusedLegIndex(null);
    setFailedRequest(null);
    setError("");
    setHistoryOpen(false);
    updateSessionUrl(null);
  }

  const openRouteOnMap = useCallback((legIndex: number) => {
    setFocusedLegIndex(legIndex);
    setRightTab("map");
  }, []);

  const showRouteInTimeline = useCallback((legIndex: number) => {
    setFocusedLegIndex(legIndex);
    setRightTab("trip");
  }, []);

  const viewPlan = useCallback((targetResponse: AgentResponse, planId: string) => {
    setInspectedResponse(targetResponse);
    setViewedPlanId(planId);
    setFocusedLegIndex(null);
  }, []);

  const choosePlan = useCallback(async (planId: string) => {
    const requestSessionId = sessionIdRef.current;
    const requestVersionId = activePlanVersionIdRef.current;
    if (!requestSessionId) return;
    try {
      const selection = await selectPlan(requestSessionId, planId);
      if (
        sessionIdRef.current !== requestSessionId
        || activePlanVersionIdRef.current !== requestVersionId
      ) return;
      setSelectedPlanId(selection.selected_plan_id);
      if (selection.selected_plan_id) setViewedPlanId(selection.selected_plan_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "选择方案失败，请稍后重试");
    }
  }, []);

  const startReplacement = useCallback((plan: Plan, stopIndex: number) => {
    const requestSessionId = sessionIdRef.current;
    const requestVersionId = activePlanVersionIdRef.current;
    const stop = plan.stops[stopIndex];
    if (!requestSessionId || !requestVersionId || selectedPlanId !== plan.plan_id || !stop) {
      setError("请先选择当前方案，再修改其中的站点。");
      return;
    }
    setError("");
    setReplacementDraft({
      planVersionId: requestVersionId,
      planId: plan.plan_id,
      stopIndex,
      resourceId: stop.resource_id,
      role: stop.role,
      rawText: stop.name,
      criteria: [],
    });
    setInput("");
  }, [selectedPlanId]);

  const chooseReplacementMode = useCallback((mode: ReplacementPreset) => {
    if (loading || restoring || !replacementDraft) return;
    const criteria = REPLACEMENT_PRESET_CRITERIA[mode].map((criterion) => ({ ...criterion }));
    const command = replacementCommandFromDraft(replacementDraft, criteria, input);
    setReplacementDraft((current) => current ? { ...current, criteria } : current);
    // Presets are one-click actions. Free-text criteria can be combined by
    // typing first and then clicking a preset, or by using Send.
    void submit(input || `更换第 ${replacementDraft.stopIndex + 1} 站：${REPLACEMENT_PRESET_LABELS[mode]}`, command);
  }, [input, loading, replacementDraft, restoring]);

  const openInspectorForResponse = useCallback((targetResponse: AgentResponse, tab: InspectorTab, legIndex: number | null = null) => {
    setInspectedResponse(targetResponse);
    setViewedPlanId((current) => targetResponse.plans.some((plan) => plan.plan_id === current) ? current : targetResponse.plans[0]?.plan_id ?? null);
    setFocusedLegIndex(legIndex);
    setRightTab(tab);
    if (isMobile) setMobileDetailOpen(true);
  }, [isMobile]);

  return (
    <div className={`app-shell ${inspectorCollapsed ? "inspector-collapsed" : ""}`}>
      <a className="skip-link" href="#main-content">跳到主要内容</a>

      <aside className="session-sidebar" aria-label="会话导航">
        <SessionNavigation recentSessions={recentSessions} sessionId={sessionId} onNew={resetSession} onOpen={(id) => void openSession(id)} />
      </aside>

      <main className="workspace" id="main-content">
        <header className="mobile-topbar">
          <Brand compact />
          <div>
            <button type="button" onClick={() => setHistoryOpen(true)} aria-label="打开历史会话"><History size={19} /></button>
            <button type="button" onClick={() => setMobileDetailOpen(true)} aria-label="打开方案详情" disabled={!inspectorPlan}><PanelRight size={19} /></button>
          </div>
        </header>

        <section className="conversation" aria-live="polite" ref={conversationRef}>
          {messages.length === 0 ? <EmptyConversation onSelect={(suggestion) => void submit(suggestion)} /> : (
            <div className="message-list">
              {messages.map((message) => <ChatBubble
                key={message.id}
                message={message}
                selectedPlan={message.response === detailResponse ? inspectorPlan : message.response?.plans[0]}
                selectedPlanId={selectedPlanId}
                showMobileDetails={Boolean(isMobile && !mobileDetailOpen && message.response === detailResponse)}
                onViewPlan={(planId) => message.response && viewPlan(message.response, planId)}
                onChoosePlan={choosePlan}
                 onOpenInspector={() => message.response && openInspectorForResponse(message.response, "trip")}
                 onOpenRoute={(index) => message.response && openInspectorForResponse(message.response, "map", index)}
                 onReplaceStop={startReplacement}
                 onChooseConflict={setInput}
              />)}
              {loading ? <ThinkingRow /> : null}
              <div ref={conversationEndRef} aria-hidden="true" />
            </div>
          )}

          {error ? <div className="error-banner" role="alert"><CircleAlert size={17} />{error}</div> : null}
        </section>

        <div className="composer-dock">
          {replacementDraft ? (
            <section className="replacement-draft" aria-label="单站替换草稿">
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
                <button type="button" className="replacement-cancel" onClick={() => setReplacementDraft(null)} disabled={loading || restoring}>取消修改</button>
              </div>
            </section>
          ) : null}
          <form className="composer" onSubmit={onSubmit}>
            <label className="sr-only" htmlFor="planning-input">{response?.question ? "补充这个信息后继续" : replacementDraft ? "描述替换偏好（可选）" : "描述你的空闲时间和偏好"}</label>
            <div className="composer-row">
              <div className="composer-tools" aria-hidden="true"><Plus size={19} /><Compass size={18} /><Settings2 size={18} /></div>
              <input id="planning-input" value={input} onChange={(event) => setInput(event.target.value)} placeholder={response?.question?.question ?? (replacementDraft ? "例如：想吃少辣的，环境安静一点（可选）" : "继续描述新的规划需求……")} disabled={loading || restoring} />
              <button type="submit" disabled={loading || restoring || (!input.trim() && !replacementDraft)} aria-label={replacementDraft ? "发送替换偏好" : "发送需求"}><Send size={19} aria-hidden="true" /></button>
            </div>
          </form>
        </div>
      </main>

       <aside className="detail-panel" aria-label="方案详情" aria-hidden={inspectorCollapsed}>
         {!isMobile ? <Inspector response={detailResponse} plan={inspectorPlan} selectedPlanId={selectedPlanId} activeTab={rightTab} activeLegIndex={focusedLegIndex} onTabChange={setRightTab} onMapRoute={openRouteOnMap} onTimelineRoute={showRouteInTimeline} onReplaceStop={startReplacement} /> : null}
      </aside>

      <button className="inspector-toggle" type="button" onClick={() => setInspectorCollapsed((current) => !current)} aria-label={inspectorCollapsed ? "展开方案详情" : "收起方案详情"} aria-expanded={!inspectorCollapsed}>
        {inspectorCollapsed ? <ChevronLeft size={20} /> : <ChevronRight size={20} />}
      </button>

      <MobileSheet open={historyOpen} onOpenChange={setHistoryOpen} title="历史会话" description="查看或继续之前的完整规划对话。">
        <SessionNavigation recentSessions={recentSessions} sessionId={sessionId} onNew={resetSession} onOpen={(id) => void openSession(id)} compact />
      </MobileSheet>

      <MobileSheet open={mobileDetailOpen} onOpenChange={setMobileDetailOpen} title="方案工作区" description="查看当前方案的行程、地图、订单与可信依据。">
         <Inspector response={detailResponse} plan={inspectorPlan} selectedPlanId={selectedPlanId} activeTab={rightTab} activeLegIndex={focusedLegIndex} onTabChange={setRightTab} onMapRoute={openRouteOnMap} onTimelineRoute={showRouteInTimeline} onReplaceStop={startReplacement} />
      </MobileSheet>
    </div>
  );
}

function Brand({ compact = false }: { compact?: boolean }) {
  return <div className={`brand-block ${compact ? "compact" : ""}`}><span className="brand-mark"><Compass size={20} aria-hidden="true" /></span><span><strong>HappyFreeTime</strong>{compact ? null : <small>周末管家</small>}</span></div>;
}

function SessionNavigation({ recentSessions, sessionId, onNew, onOpen, compact = false }: {
  recentSessions: SessionSummary[];
  sessionId: string | null;
  onNew: () => void;
  onOpen: (sessionId: string) => void;
  compact?: boolean;
}) {
  return (
    <div className={`session-navigation ${compact ? "compact" : ""}`}>
      {!compact ? <Brand /> : null}
      <button className="new-session-button" type="button" aria-label="新建规划" onClick={onNew}><Plus size={18} aria-hidden="true" />新建出游方案</button>
      <div className="sidebar-section-label">历史会话</div>
      <div className="session-history-list">
        {recentSessions.map((session) => (
          <button className={`session-row ${session.session_id === sessionId ? "active" : ""}`} type="button" key={session.session_id} aria-current={session.session_id === sessionId ? "page" : undefined} onClick={() => onOpen(session.session_id)}>
            <span><strong>{session.title}</strong><small>{session.last_message_preview || statusLabel(session.status)}</small></span>
            <time>{new Date(session.updated_at).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" })}</time>
          </button>
        ))}
        {!recentSessions.length ? <p className="session-history-empty">完成一次规划后，会话和方案会保留在这里。</p> : null}
      </div>
      <div className="sidebar-spacer" />
      <button className="sidebar-utility" type="button"><ReceiptText size={17} />管理会话</button>
      <button className="sidebar-utility" type="button"><Settings2 size={17} />设置</button>
    </div>
  );
}

function EmptyConversation({ onSelect }: { onSelect: (suggestion: string) => void }) {
  return (
    <div className="empty-conversation">
      <div className="empty-icon"><Sparkles size={25} aria-hidden="true" /></div>
      <h2>把半天交给管家</h2>
      <p>不需要先想清楚所有条件。说一句大概想法，我会在必要时追问，再给出可比较、可解释的安排。</p>
      <div className="suggestion-list">{SUGGESTIONS.map((suggestion) => <button key={suggestion} type="button" onClick={() => onSelect(suggestion)}>{suggestion}<ChevronRight size={15} /></button>)}</div>
    </div>
  );
}

function ButlerAvatar() {
  return <span className="butler-avatar" aria-hidden="true"><Compass size={18} /></span>;
}

function ChatBubble({ message, selectedPlan, selectedPlanId, showMobileDetails, onViewPlan, onChoosePlan, onOpenInspector, onOpenRoute, onReplaceStop, onChooseConflict }: {
  message: ChatMessage;
  selectedPlan?: Plan;
  selectedPlanId: string | null;
  showMobileDetails: boolean;
  onViewPlan: (planId: string) => void;
  onChoosePlan: (planId: string) => void;
  onOpenInspector: () => void;
  onOpenRoute: (legIndex: number) => void;
  onReplaceStop: (plan: Plan, stopIndex: number) => void;
  onChooseConflict: (option: string) => void;
}) {
  return (
    <article className={`message ${message.role} ${message.response?.plans.length ? "rich-turn" : ""}`}>
      {message.role === "assistant" ? <ButlerAvatar /> : null}
      <div className="message-content">
        <span>{message.role === "user" ? "你" : "周末管家"}</span>
        {message.content ? <p>{message.content}</p> : null}
        {message.response?.conflict ? (
          <section className="conflict-panel" role="status">
            <strong>{message.response.conflict.message}</strong>
            <div>{message.response.conflict.relaxation_options.map((option) => <button type="button" key={option} onClick={() => onChooseConflict(option)}>{option}</button>)}</div>
          </section>
        ) : null}
        {message.response?.plans.length ? (
          <RichPlanningReply response={message.response} selectedPlan={selectedPlan} selectedPlanId={selectedPlanId} showMobileDetails={showMobileDetails} onViewPlan={onViewPlan} onChoosePlan={onChoosePlan} onOpenInspector={onOpenInspector} onOpenRoute={onOpenRoute} onReplaceStop={onReplaceStop} />
        ) : null}
      </div>
    </article>
  );
}

function ThinkingRow() {
  return <div className="thinking-row"><ButlerAvatar /><div><span /><span /><span /><strong>正在核对路线和可行性</strong></div></div>;
}

function RichPlanningReply({ response, selectedPlan, selectedPlanId, showMobileDetails, onViewPlan, onChoosePlan, onOpenInspector, onOpenRoute, onReplaceStop }: { response: AgentResponse; selectedPlan?: Plan; selectedPlanId: string | null; showMobileDetails: boolean; onViewPlan: (planId: string) => void; onChoosePlan: (planId: string) => void; onOpenInspector: () => void; onOpenRoute: (legIndex: number) => void; onReplaceStop: (plan: Plan, stopIndex: number) => void }) {
  const weather = response.provider_facts.find((fact): fact is Extract<ProviderFact, { kind: "weather" }> => fact.kind === "weather");
  const departureAt = departureConstraint(response);
  const returnConstraint = latestReturnConstraint(response);
  const shownConstraints = response.constraint_summary.filter((item) => ["exact_stop_count", "required_stop_roles", "budget_per_person", "max_distance_km", "party"].includes(item.field)).slice(0, 3);
  const headingId = `plan-heading-${response.plans[0]?.plan_id ?? "reply"}`;
  const diffs = response.plan_diffs ?? (response.plan_diff ? [response.plan_diff] : []);
  const hasModification = diffs.length > 0;
  return (
    <section className="rich-planning-reply" aria-labelledby={headingId}>
      <div className="rich-reply-body">
        <div className="planning-progress" aria-label="规划完成步骤"><span><CheckCircle2 size={14} />解析需求</span><ChevronRight size={13} /><span><CheckCircle2 size={14} />查询路线与景点</span><ChevronRight size={13} /><span><CheckCircle2 size={14} />评估与排序</span></div>
        <div className="plan-intro"><div><h2 id={headingId}>{hasModification ? "方案已定向更新" : `我整理了 ${response.plans.length} 个都可行的方案`}</h2><p>{hasModification ? "其他站点保持原位置；每个替换候选都已重新核验。" : "重要信息放在同一位置，先看路线和取舍，再选一个展开。"}</p></div><span>{response.plans.length} 个候选</span></div>
        {hasModification ? (
          <div className="plan-diff-list" role="status">
            {diffs.map((diff) => {
              const replacement = diff.replacements[0];
              const lockedText = diff.locked_stops.length ? `其他 ${diff.locked_stops.length} 站保持不变` : "其他站点保持原位置";
              const distanceText = diff.route_distance_delta_km < 0 ? `全程缩短 ${Math.abs(diff.route_distance_delta_km).toFixed(1)} km` : diff.route_distance_delta_km > 0 ? `全程增加 ${diff.route_distance_delta_km.toFixed(1)} km` : "全程距离不变";
              const durationText = diff.duration_delta_minutes < 0 ? `提前 ${Math.abs(diff.duration_delta_minutes)} 分钟` : diff.duration_delta_minutes > 0 ? `增加 ${diff.duration_delta_minutes} 分钟` : "总时长不变";
              const priceText = diff.price_delta < 0 ? `地点费用减少 ¥${Math.abs(diff.price_delta)}` : diff.price_delta > 0 ? `地点费用增加 ¥${diff.price_delta}` : "地点费用不变";
              return <div className="plan-diff-summary" key={diff.new_plan_id}><strong>{replacement.before_name} → {replacement.after_name}</strong><span>{lockedText} · {distanceText} · {durationText} · {priceText}</span></div>;
            })}
            {response.plan_diff ? <span className="legacy-plan-diff" aria-hidden="true">餐厅保持不变 · 通勤缩短 {Math.abs(response.plan_diff.route_distance_delta_km).toFixed(1)} km</span> : null}
          </div>
        ) : null}
        {response.poi_presentations.length ? <p className="demo-data-notice"><Database size={14} />POI 商业信息为模拟数据；路线来源和降级状态见各路线段。</p> : null}
        {(weather || departureAt || returnConstraint || shownConstraints.length) ? (
          <div className="shared-context" aria-label="本次规划的共享信息">
            {weather ? <span><CloudSun size={18} /><strong>预计天气</strong>{weather.condition}{weather.temperature_c !== null ? ` · ${weather.temperature_c}℃` : ""}{weather.is_adverse ? " · 需留意" : ""}</span> : null}
            {departureAt ? <span><Clock3 size={18} /><strong>准时出发</strong>{displayConstraint(departureAt)}</span> : null}
            {returnConstraint ? <span><Clock3 size={18} /><strong>返程约束</strong>{displayConstraint(returnConstraint)} · 已纳入规划</span> : null}
            {shownConstraints.map((item) => <span key={item.field} title={constraintSourceLabel(item)}><Info size={17} /><strong>{ASSUMPTION_LABELS[item.field] ?? item.field}</strong>{displayConstraint(item)}</span>)}
          </div>
        ) : null}
        {response.warnings?.some((warning) => warning.plan_id === null) ? <div className="shared-warning"><CircleAlert size={16} />{response.warnings.filter((warning) => warning.plan_id === null).map((warning) => warning.message).join("；")}</div> : null}
        <div className="plan-grid">
          {response.plans.map((plan, index) => <PlanCard key={plan.plan_id} plan={plan} presentations={response.poi_presentations} index={index} warning={planWarning(response, plan)} returnConstraint={returnConstraint} viewed={(selectedPlan?.plan_id ?? response.plans[0].plan_id) === plan.plan_id} selected={selectedPlanId === plan.plan_id} showMobileDetails={showMobileDetails} onView={() => onViewPlan(plan.plan_id)} onChoose={() => onChoosePlan(plan.plan_id)} onOpenInspector={onOpenInspector} onOpenRoute={onOpenRoute} onReplaceStop={onReplaceStop} />)}
        </div>
      </div>
    </section>
  );
}

function PlanCard({ plan, presentations, index, warning, returnConstraint, viewed, selected, showMobileDetails, onView, onChoose, onOpenInspector, onOpenRoute, onReplaceStop }: { plan: Plan; presentations: PoiPresentation[]; index: number; warning: PlanWarning | null; returnConstraint: ConstraintSummaryItem | null; viewed: boolean; selected: boolean; showMobileDetails: boolean; onView: () => void; onChoose: () => void; onOpenInspector: () => void; onOpenRoute: (legIndex: number) => void; onReplaceStop: (plan: Plan, stopIndex: number) => void }) {
  const heroStop = plan.stops[0];
  const returnLeg = plan.route_legs.length > plan.stops.length ? plan.route_legs[plan.route_legs.length - 1] : null;
  const notice = warning?.message ?? plan.tradeoffs[0] ?? "已通过当前硬约束校验";
  return (
    <article className={`plan-card ${viewed ? "viewed" : ""} ${selected ? "selected" : ""}`}>
      <div className="plan-visual">
        {heroStop ? <StopImage stop={heroStop} variant="hero" /> : <div className="stop-image hero placeholder"><ImageIcon size={22} /></div>}
        <div className="plan-visual-scrim" />
        <span className="plan-rank">{index + 1}</span>
        <div className="plan-title"><span>{strategyLabel(plan.strategy)}</span><h3>{plan.title}</h3></div>
      </div>
      <div className="plan-card-body">
        <div className="plan-metrics"><span><Clock3 size={15} />{Math.floor(plan.total_duration_minutes / 60)} 小时 {plan.total_duration_minutes % 60 || ""}{plan.total_duration_minutes % 60 ? " 分" : ""}</span><span title="地点费用，不含交通" aria-label={`地点费用，不含交通：${planPriceLabel(plan)}`}><CircleDollarSign size={15} />{planPriceLabel(plan)} · 地点费用，不含交通</span><span><Route size={15} />{totalDistance(plan).toFixed(1)} km</span></div>
        <div className={`plan-notice ${warning?.degraded ? "degraded" : ""}`} aria-label={warning ? "方案待确认信息" : undefined}><Info size={15} />{notice}</div>
        <div className="compact-itinerary">
          {plan.stops.map((stop, stopIndex) => (
            <div className="compact-stop" key={stop.resource_id}>
              <span className="compact-marker">{stopIndex + 1}</span>
              <span><strong>{stop.name}</strong><small>{stop.start} · {stop.role === "meal" || stop.type === "restaurant" ? "餐饮" : "活动"}</small></span>
              {plan.route_legs[stopIndex + 1] && stopIndex < plan.stops.length - 1 ? <span className="compact-route"><RouteModeIcon mode={plan.route_legs[stopIndex + 1].mode} />{routeModeLabel(plan.route_legs[stopIndex + 1].mode)} {plan.route_legs[stopIndex + 1].duration_minutes} 分钟</span> : null}
            </div>
          ))}
        </div>
        {returnConstraint && returnLeg ? <div className="return-meta">预计 {returnLeg.end} 到家 · 目标 {displayConstraint(returnConstraint)}</div> : null}
        <div className="route-source"><Navigation size={14} />路线来源：{[...new Set(plan.route_legs.map((leg) => routeSourceLabel(leg.source)))].join(" / ") || "待查询"}</div>
        <div className="plan-card-actions">
          <button className="plan-select" type="button" onClick={onChoose} aria-pressed={selected} aria-label={`选择${plan.title}`}>{selected ? "已选择这个方案" : "选择这个方案"}</button>
          <button className="plan-view" type="button" onClick={onView} aria-pressed={viewed} aria-label={`查看${plan.title}`}>{viewed ? "正在查看" : "查看详情"}<ChevronRight size={16} /></button>
        </div>
        {viewed && showMobileDetails ? <MobilePlanExpansion plan={plan} presentations={presentations} canReplace={selected} onReplaceStop={onReplaceStop} onOpenInspector={onOpenInspector} onOpenRoute={onOpenRoute} /> : null}
      </div>
    </article>
  );
}

function MobilePlanExpansion({ plan, presentations, canReplace, onReplaceStop, onOpenInspector, onOpenRoute }: { plan: Plan; presentations: PoiPresentation[]; canReplace: boolean; onReplaceStop: (plan: Plan, stopIndex: number) => void; onOpenInspector: () => void; onOpenRoute: (legIndex: number) => void }) {
  return <div className="mobile-plan-expansion"><RecommendationSummary plan={plan} compact /><div className="mobile-route-list">{plan.route_legs.map((leg, index) => { const kind = index === 0 ? "start" : index >= plan.stops.length ? "return" : "next"; return <RouteSummary key={`${leg.destination_name}-${index}`} leg={leg} label={kind === "start" ? "出发" : kind === "return" ? "返程" : "下一程"} actionLabel={routeActionLabel(leg, kind)} onClick={() => onOpenRoute(index)} />; })}</div><div className="mobile-poi-list">{plan.stops.map((stop, index) => <PoiDisclosure key={stop.resource_id} stop={stop} presentation={presentations.find((item) => item.resource_id === stop.resource_id)} canReplace={canReplace} onReplace={() => onReplaceStop(plan, index)} />)}</div><button className="open-inspector-button" type="button" onClick={onOpenInspector}>打开完整行程与地图<ChevronRight size={16} /></button></div>;
}

function Inspector({ response, plan, selectedPlanId, activeTab, activeLegIndex, onTabChange, onMapRoute, onTimelineRoute, onReplaceStop }: { response: AgentResponse | null; plan?: Plan; selectedPlanId: string | null; activeTab: InspectorTab; activeLegIndex: number | null; onTabChange: (tab: InspectorTab) => void; onMapRoute: (legIndex: number) => void; onTimelineRoute: (legIndex: number) => void; onReplaceStop: (plan: Plan, stopIndex: number) => void }) {
  return (
    <div className="inspector">
      <div className="detail-tabs" role="tablist" aria-label="详情视图"><TabButton icon={<CalendarDays size={16} />} label="行程" active={activeTab === "trip"} onClick={() => onTabChange("trip")} /><TabButton icon={<MapIcon size={16} />} label="地图" active={activeTab === "map"} onClick={() => onTabChange("map")} /><TabButton icon={<ReceiptText size={16} />} label="订单" active={activeTab === "orders"} onClick={() => onTabChange("orders")} /><TabButton icon={<ShieldCheck size={16} />} label="依据" active={activeTab === "evidence"} onClick={() => onTabChange("evidence")} /></div>
       {activeTab === "trip" ? <TripPanel plan={plan} presentations={response?.poi_presentations ?? []} canReplace={Boolean(plan && selectedPlanId === plan.plan_id && response?.plan_version_id)} onReplaceStop={onReplaceStop} activeLegIndex={activeLegIndex} onSelectRoute={onMapRoute} /> : null}
      {activeTab === "map" ? <MapPanel plan={plan} activeLegIndex={activeLegIndex} onSelectRoute={onTimelineRoute} /> : null}
      {activeTab === "orders" ? <OrdersPanel /> : null}
      {activeTab === "evidence" ? <EvidencePanel response={response} plan={plan} /> : null}
    </div>
  );
}

function TabButton({ icon, label, active, onClick }: { icon: ReactNode; label: string; active: boolean; onClick: () => void }) {
  return <button type="button" role="tab" aria-selected={active} className={active ? "active" : ""} onClick={onClick}>{icon}<span>{label}</span></button>;
}

function RecommendationSummary({ plan, compact = false }: { plan: Plan; compact?: boolean }) {
  return <section className={`recommendation-summary ${compact ? "compact" : ""}`}><h3>管家为什么推荐</h3><ul>{planRationale(plan).map((reason) => <li key={reason}>{reason}</li>)}</ul><div><strong>需要接受的取舍</strong><p>{plan.tradeoffs.length ? plan.tradeoffs.slice(0, 2).join("；") : "当前没有额外取舍提示；临近出发时仍需关注“依据”中的动态状态。"}</p></div></section>;
}

function TripPanel({ plan, presentations, canReplace, onReplaceStop, activeLegIndex, onSelectRoute }: { plan?: Plan; presentations: PoiPresentation[]; canReplace: boolean; onReplaceStop: (plan: Plan, stopIndex: number) => void; activeLegIndex: number | null; onSelectRoute: (legIndex: number) => void }) {
  if (!plan) return <DetailEmpty icon={<CalendarDays size={24} />} title="行程将在这里展开" text="生成方案后，可逐站查看 POI、时间、通勤和推荐取舍。" />;
  const returnLegIndex = plan.route_legs.length > plan.stops.length ? plan.route_legs.length - 1 : null;
  return (
    <div className="trip-detail">
      <div className="detail-title"><span>{strategyLabel(plan.strategy)}</span><h2>{plan.title}</h2><p>{planPriceLabel(plan)}（地点费用，不含交通）· {plan.total_duration_minutes} 分钟 · {totalDistance(plan).toFixed(1)} km</p></div>
      <RecommendationSummary plan={plan} />
      <div className="detail-section-heading"><h3>详细行程</h3><span>{plan.stops.length} 站</span></div>
      <ol className="timeline">
         {plan.stops.map((stop, index) => <li key={stop.resource_id}>{plan.route_legs[index] ? <TimelineRoute leg={plan.route_legs[index]} index={index} active={activeLegIndex === index} kind={index === 0 ? "start" : "next"} onSelect={onSelectRoute} /> : null}<div className="timeline-stop"><div className="timeline-stop-meta"><time>{stop.start}<small>{stop.end}</small></time><span className="timeline-marker">{index + 1}</span></div><PoiDisclosure stop={stop} presentation={presentations.find((item) => item.resource_id === stop.resource_id)} canReplace={canReplace} onReplace={() => onReplaceStop(plan, index)} /></div></li>)}
        {returnLegIndex !== null ? <li><TimelineRoute leg={plan.route_legs[returnLegIndex]} index={returnLegIndex} active={activeLegIndex === returnLegIndex} kind="return" onSelect={onSelectRoute} /></li> : null}
      </ol>
      <section className="trip-route-overview">
        <div><h3>路线预览</h3><span>{totalDistance(plan).toFixed(1)} km · 通勤约 {plan.route_legs.reduce((sum, leg) => sum + leg.duration_minutes, 0)} 分钟</span></div>
        <div className="route-track" aria-label="路线站点顺序"><span><i>出</i><small>出发地</small></span>{plan.stops.map((stop, index) => <span key={stop.resource_id}><i>{index + 1}</i><small>{stop.name}</small></span>)}{returnLegIndex !== null ? <span><i>返</i><small>出发地</small></span> : null}</div>
        <button type="button" onClick={() => onSelectRoute(activeLegIndex ?? 0)}>在地图中查看完整路线<ChevronRight size={15} /></button>
      </section>
    </div>
  );
}

function TimelineRoute({ leg, index, active, kind, onSelect }: { leg: RouteLeg; index: number; active: boolean; kind: "start" | "next" | "return"; onSelect: (legIndex: number) => void }) {
  const label = routeActionLabel(leg, kind);
  return <div className={`timeline-route ${active ? "route-focused" : ""}`}><span className="route-mode-icon"><RouteModeIcon mode={leg.mode} size={16} /></span><span><strong>{kind === "return" ? "返回出发地" : `${routeModeLabel(leg.mode)} ${leg.duration_minutes} 分钟`}</strong><small>{leg.origin_name} → {leg.destination_name} · {formatDistance(leg.distance_km)} km</small></span><button type="button" aria-pressed={active} onClick={() => onSelect(index)}>{label}</button></div>;
}

function RouteSummary({ leg, label, actionLabel, onClick }: { leg: RouteLeg; label: string; actionLabel: string; onClick: () => void }) {
  return <button type="button" className="route-summary" aria-label={actionLabel} onClick={onClick}><span><RouteModeIcon mode={leg.mode} size={15} /></span><div><strong>{label} · {routeModeLabel(leg.mode)} {leg.duration_minutes} 分钟</strong><small>{leg.origin_name} → {leg.destination_name} · {formatDistance(leg.distance_km)} km</small></div><ChevronRight size={15} /></button>;
}

function PoiDisclosure({ stop, presentation, canReplace = false, onReplace }: { stop: Stop; presentation?: PoiPresentation; canReplace?: boolean; onReplace?: () => void }) {
  const galleryImage = presentation?.gallery[0];
  const mapUrl = `https://ditu.amap.com/search?query=${encodeURIComponent(`${stop.name} ${presentation?.address ?? ""}`)}`;
  const onReplaceClick = (event: MouseEvent<HTMLButtonElement>) => {
    // A button inside <summary> would otherwise toggle the disclosure before
    // starting the replacement action.
    event.preventDefault();
    event.stopPropagation();
    onReplace?.();
  };
  return (
    <details className="poi-disclosure">
      <summary className="poi-summary">
        <StopImage stop={stop} galleryImage={galleryImage} variant="thumb" />
          <span className="poi-summary-copy">
          <span className="poi-summary-kicker"><span className="poi-type-icon">{stop.type === "restaurant" ? <Utensils size={14} /> : <MapPin size={14} />}</span><small>{presentation?.category_label ?? (stop.type === "restaurant" ? "餐饮" : "活动")} · {stop.duration_minutes} 分钟</small></span>
          <strong>{stop.name}</strong>
          <em>{priceLabel(stop)}{presentation ? ` · 演示评分 ${presentation.demo_rating.toFixed(1)}` : ""}</em>
          <small className="poi-summary-time">本次安排 {stop.start}–{stop.end}</small>
          {presentation ? <small className="poi-summary-address">{presentation.business_area} · {presentation.address}</small> : <small className="poi-summary-address">POI 详情待补充，当前安排仍已完成规划校验</small>}
        </span>
        <span className="poi-summary-side">
          <button className="poi-action poi-replace" type="button" disabled={!canReplace} onClick={onReplaceClick} title={canReplace ? "替换当前站点，其他站点保持不变" : "先选择方案，再修改站点"}>换这站</button>
          <span className="poi-summary-expand"><span>详情</span><ChevronDown size={16} aria-hidden="true" /></span>
        </span>
      </summary>
      <div className="poi-detail">
        {presentation ? <>
          <p className="poi-description">{presentation.description}</p>
          <div className="poi-tags">{[...presentation.scene_tags, ...presentation.facility_tags].slice(0, 7).map((tag) => <span key={tag}><Tag size={12} />{tag}</span>)}</div>
          <dl><div><dt><Clock3 size={14} />本次安排</dt><dd>{stop.start}–{stop.end} · 建议停留 {presentation.suggested_duration_minutes} 分钟</dd></div><div><dt><Star size={14} />演示评分</dt><dd>{presentation.demo_rating.toFixed(1)}（演示评论 {presentation.demo_review_count.toLocaleString()} 条）</dd></div><div><dt><Store size={14} />营业</dt><dd>{presentation.opening_status} · {presentation.opening_hours_display}</dd></div><div><dt><CircleDollarSign size={14} />参考人均</dt><dd>{presentation.reference_avg_price === 0 ? "免费" : `¥${presentation.reference_avg_price ?? "—"}`}</dd></div><div><dt><MapPin size={14} />位置</dt><dd>{presentation.business_area} · {presentation.address}</dd></div><div><dt><Info size={14} />到访提示</dt><dd>{presentation.reservation_requirement}；{presentation.queue_profile}</dd></div></dl>
          <div className="poi-risk-list">{[presentation.weather_suitability, presentation.child_suitability, ...presentation.risk_tips].slice(0, 4).map((tip) => <span key={tip}>{tip}</span>)}</div>
          <div className="poi-actions"><a className="poi-action" href={mapUrl} target="_blank" rel="noreferrer"><MapPin size={14} />地图定位</a><a className="poi-action" href={mapUrl} target="_blank" rel="noreferrer"><ExternalLink size={14} />在高德查看</a></div>
          <details className="poi-data-disclosure"><summary>数据说明</summary><p>{presentation.data_notice}{galleryImage?.kind === "illustrative" ? " 当前图片为场景示意，不代表门店实拍。" : ""}</p></details>
        </> : <p className="poi-unavailable-note"><Info size={14} />当前方案未附带 POI 详情快照；规划、路线和时间线仍可正常查看。</p>}
         <div className="poi-source"><Database size={14} />{catalogSourceLabel(stop.source)} · {stop.source.collected_at.slice(0, 10)}</div>
      </div>
    </details>
  );
}

function StopImage({ stop, galleryImage, variant = "detail" }: { stop: Stop; galleryImage?: PoiPresentation["gallery"][number]; variant?: "hero" | "detail" | "thumb" }) {
  const [failed, setFailed] = useState(false);
  const image = galleryImage?.url ?? stop.image?.url;
  if (!image || failed) {
    const label = stop.category_tags[0] || (stop.type === "restaurant" ? "餐饮" : "活动");
    return <div className={`stop-image ${variant} placeholder ${stop.type}`} aria-label={`${stop.name}暂无可靠图片`}><span>{stop.type === "restaurant" ? <Utensils size={21} /> : <ImageIcon size={21} />}{label}</span></div>;
  }
  return <img className={`stop-image ${variant}`} src={image} alt={galleryImage?.alt ?? stop.name} loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} />;
}

function MapPanel({ plan, activeLegIndex, onSelectRoute }: { plan?: Plan; activeLegIndex: number | null; onSelectRoute: (legIndex: number) => void }) {
  const sources = plan ? [...new Set(plan.route_legs.map((leg) => routeSourceLabel(leg.source)))] : [];
  const sourceSummary = sources.length === 1 ? sources[0] : sources.length > 1 ? "多来源路线" : "路线摘要";
  return <div className="map-detail">{plan ? <AmapPlanMap plan={plan} activeLegIndex={activeLegIndex} onSelectRoute={onSelectRoute} /> : <div className="map-canvas amap-map-status disabled">生成方案后显示地图路线</div>}<div className="detail-title"><span>路线预览</span><h2>{sourceSummary}</h2><p>{plan ? `${totalDistance(plan).toFixed(1)} km · 已按路线耗时重建时间线` : "生成方案后显示路线摘要"}</p></div>{plan?.route_legs.map((leg, index) => <button className={`route-row ${activeLegIndex === index ? "active" : ""}`} type="button" key={`${leg.destination_name}-${index}`} aria-pressed={activeLegIndex === index} onClick={() => onSelectRoute(index)}><RouteModeIcon mode={leg.mode} size={18} /><span><strong>{leg.origin_name} → {leg.destination_name}</strong><small>{leg.start}–{leg.end} · {formatDistance(leg.distance_km)}km · {leg.duration_minutes} 分钟 · {routeSourceLabel(leg.source)}{leg.degraded ? " · 降级估算" : ""}</small></span></button>)}</div>;
}

function EvidencePanel({ response, plan }: { response: AgentResponse | null; plan?: Plan }) {
  if (!response || !plan) return <DetailEmpty icon={<ShieldCheck size={24} />} title="依据将在这里汇总" text="天气、路线、营业与数据来源会集中展示，不挤占方案比较区。" />;
  const sources = [...new Map(plan.stops.map((stop) => [stop.source.source_uri, stop.source])).values()];
  return (
    <div className="evidence-panel">
      <div className="detail-title"><span>可信状态</span><h2>证据与数据</h2><p>只展示系统实际获得的事实、来源和降级状态。</p></div>
      <div className="evidence-list">
        {response.provider_facts.map((fact, index) => <ProviderEvidence key={`${fact.kind}-${index}`} fact={fact} />)}
        {plan.route_legs.map((leg, index) => <article className={`evidence-row ${leg.degraded ? "warning" : ""}`} key={`${leg.destination_name}-${index}`}><span className="evidence-icon"><Route size={17} /></span><div><strong>路线 · {leg.origin_name} → {leg.destination_name}</strong><p>{routeSourceLabel(leg.source)} · {leg.provider_mode} · {leg.distance_km} km / {leg.duration_minutes} 分钟</p><small>{leg.degraded ? `降级：${leg.degraded_reason ?? "原因未提供"}` : `核验于 ${safeDateTime(leg.verified_at)}`}</small></div></article>)}
        {sources.map((source) => <article className="evidence-row" key={source.source_uri}><span className="evidence-icon"><Database size={17} /></span><div><strong>POI 目录 · {source.source_name}</strong><p>{source.source_license} · {source.verification_status === "verified" ? "已核验" : source.verification_status === "stale" ? "需复核" : "未核验"}</p><small>采集于 {safeDateTime(source.collected_at)}</small></div></article>)}
        {response.warnings?.map((warning) => <article className="evidence-row warning" key={`${warning.code}-${warning.resource_id ?? "all"}`}><span className="evidence-icon"><CircleAlert size={17} /></span><div><strong>需要确认</strong><p>{warning.message}</p><small>{warning.source ? `来源：${warning.source}` : "来源未提供"}{warning.stale ? " · 数据可能过期" : ""}</small></div></article>)}
      </div>
    </div>
  );
}

function ProviderEvidence({ fact }: { fact: ProviderFact }) {
  const title = fact.kind === "weather" ? `天气 · ${fact.city}${fact.district}` : fact.kind === "geocoding" ? `地点解析 · ${fact.request.location_text}` : `动态可用性 · ${fact.resource_id}`;
  const description = fact.kind === "weather" ? `${fact.date} · ${fact.condition}${fact.temperature_c !== null ? ` · ${fact.temperature_c}℃` : ""} · 降水 ${fact.precipitation_mm}mm` : fact.kind === "geocoding" ? `${fact.resolution}${fact.address ? ` · ${fact.address}` : ""}` : `${fact.status}${fact.reason ? ` · ${fact.reason}` : ""}`;
  return <article className={`evidence-row ${fact.degraded ? "warning" : ""}`}><span className="evidence-icon">{fact.kind === "weather" ? <CloudSun size={17} /> : fact.kind === "geocoding" ? <MapPin size={17} /> : <Store size={17} />}</span><div><strong>{title}</strong><p>{description}</p><small>{providerSourceLabel(fact)} · 核验于 {safeDateTime(fact.verified_at)}{fact.degraded ? ` · 降级：${fact.degraded_reason}` : ""}</small></div></article>;
}

function OrdersPanel() {
  return <DetailEmpty icon={<ReceiptText size={24} />} title="尚无执行订单" text="当前版本支持规划与核验；确认方案、幂等下单和失败补偿将在后续执行阶段接入。" />;
}

function DetailEmpty({ icon, title, text }: { icon: ReactNode; title: string; text: string }) {
  return <div className="detail-empty"><div>{icon}</div><h2>{title}</h2><p>{text}</p></div>;
}

function MobileSheet({ open, onOpenChange, title, description, children }: { open: boolean; onOpenChange: (open: boolean) => void; title: string; description: string; children: ReactNode }) {
  return <Dialog.Root open={open} onOpenChange={onOpenChange}><Dialog.Portal><Dialog.Backdrop className="sheet-backdrop" /><Dialog.Popup className="mobile-sheet"><header className="sheet-header"><div><Dialog.Title>{title}</Dialog.Title><Dialog.Description>{description}</Dialog.Description></div><Dialog.Close className="sheet-close" aria-label="关闭"><X size={20} /></Dialog.Close></header><div className="sheet-content">{children}</div></Dialog.Popup></Dialog.Portal></Dialog.Root>;
}

export default App;
