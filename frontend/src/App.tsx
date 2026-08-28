import { FormEvent, type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import {
  CalendarDays,
  ChevronRight,
  CircleDollarSign,
  Clock3,
  Compass,
  History,
  Image as ImageIcon,
  Map,
  MapPin,
  MessageSquareText,
  Plus,
  ReceiptText,
  Route,
  Send,
  Sparkles,
  Utensils,
  CloudRain,
} from "lucide-react";

import { createSession, getSession, listSessions, sendMessage } from "./api";
import { AmapPlanMap } from "./AmapPlanMap";
import type { AgentResponse, Assumption, ChatMessage, ConstraintSummaryItem, Plan, ProviderFact, SessionSummary, SessionView } from "./types";

const SUGGESTIONS = [
  "今天下午出去玩，别太远",
  "周六和朋友聚一下，人均150",
  "安排一个轻松的约会，想吃甜品",
];

const ASSUMPTION_LABELS: Record<string, string> = {
  date: "日期",
  time_window: "时间",
  max_distance_km: "距离",
  budget_per_person: "预算",
  party: "同行人",
};

const SESSION_QUERY_KEY = "session";

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

function responseFromSession(view: SessionView): AgentResponse | null {
  if (view.latest_response) return view.latest_response;
  if (!view.plans.length) return null;
  return {
    status: "completed",
    reply: "",
    question: null,
    assumptions: [],
    constraint_summary: [],
    plans: view.plans,
    conflict: null,
    provider_facts: [],
    catalog_violations: [],
    catalog_warnings: [],
  };
}

function displayAssumption(assumption: Assumption): string {
  // API 保留通用 value 类型；展示层在这里把已知领域字段格式化为用户语言。
  if (assumption.field === "budget_per_person") return `人均 ${assumption.value} 元`;
  if (assumption.field === "max_distance_km") return `${assumption.value} km 内`;
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
  return displayAssumption({
    field: item.field,
    value: item.value,
    reason: "",
    rule_id: item.rule_id ?? "",
  });
}

function constraintSourceLabel(item: ConstraintSummaryItem): string {
  if (item.source === "user_inferred") {
    return item.evidence ? `根据“${item.evidence}”推断` : "根据需求推断";
  }
  if (item.source === "default_rule") return "系统默认";
  if (item.source === "user_explicit") return "来自你的需求";
  if (item.source === "system_context") return "本次会话信息";
  if (item.source === "real_tool") return "外部服务确认";
  return "已确认信息";
}

function strategyLabel(strategy: string): string {
  const labels: Record<string, string> = {
    safe: "稳妥",
    rich: "体验",
    budget: "省钱",
  };
  return labels[strategy] ?? strategy;
}

function providerSourceLabel(fact: ProviderFact): string {
  const labels: Record<ProviderFact["source"], string> = {
    amap_live: "高德实时",
    cache: "缓存",
    replay: "固定回放",
    mock: "本地模拟",
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

function catalogSourceLabel(source: Plan["stops"][number]["source"]): string {
  const status = {
    verified: "已核验",
    unverified: "未核验",
    stale: "已过期",
  }[source.verification_status];
  return `${source.source_name} · ${source.source_license} · ${status}`;
}

function priceLabel(stop: Plan["stops"][number]): string {
  if (stop.price_kind === "unknown") return "价格未知";
  if (stop.price_kind === "free") return "免费";
  return `¥${stop.price}${stop.price_kind === "estimated" ? "（估算）" : ""}`;
}

function planPriceLabel(plan: Plan): string {
  if (plan.price_status === "incomplete") return `¥${plan.total_price} + 未知价格`;
  if (plan.price_status === "estimated") return `约 ¥${plan.total_price}`;
  return `¥${plan.total_price}`;
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
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);
  const [rightTab, setRightTab] = useState<"trip" | "map" | "orders">("trip");
  const [failedRequest, setFailedRequest] = useState<{
    sessionId: string;
    content: string;
    requestId: string;
  } | null>(null);

  const selectedPlan = useMemo(
    () => response?.plans.find((plan) => plan.plan_id === selectedPlanId) ?? response?.plans[0],
    [response, selectedPlanId],
  );

  const refreshRecentSessions = useCallback(async () => {
    try {
      setRecentSessions(await listSessions(5));
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
      const restoredResponse = responseFromSession(view);
      setSessionId(view.session_id);
      setMessages(view.messages);
      setResponse(restoredResponse);
      setSelectedPlanId(restoredResponse?.plans[0]?.plan_id ?? null);
      setRightTab("trip");
      if (navigate && sessionIdFromUrl() !== view.session_id) {
        updateSessionUrl(view.session_id);
      }
    } catch (reason) {
      setSessionId(null);
      setMessages([]);
      setResponse(null);
      setSelectedPlanId(null);
      if (sessionIdFromUrl() === nextSessionId) updateSessionUrl(null, true);
      setError(reason instanceof Error ? reason.message : "会话恢复失败");
    } finally {
      setRestoring(false);
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
        setSessionId(null);
        setMessages([]);
        setResponse(null);
        setSelectedPlanId(null);
        setFailedRequest(null);
        setError("");
      }
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, [openSession, refreshRecentSessions]);

  async function submit(content: string) {
    const trimmed = content.trim();
    if (!trimmed || loading || restoring) return;
    setLoading(true);
    setError("");
    setInput("");
    let activeSession = sessionId;
    try {
      // 懒创建后端会话：只打开页面不会生成空数据库记录。反问答案继续复用
      // activeSession，因此后端能通过同一个 thread_id 恢复 checkpoint。
      if (!activeSession) {
        activeSession = await createSession();
        setSessionId(activeSession);
        updateSessionUrl(activeSession);
      }
      const isRetry = failedRequest?.sessionId === activeSession && failedRequest.content === trimmed;
      const requestId = isRetry ? failedRequest.requestId : crypto.randomUUID();
      if (!isRetry) {
        setMessages((current) => [
          ...current,
          { id: crypto.randomUUID(), role: "user", content: trimmed },
        ]);
      }
      setFailedRequest({ sessionId: activeSession, content: trimmed, requestId });
      const nextResponse = await sendMessage(activeSession, trimmed, requestId);
      setFailedRequest(null);
      setResponse(nextResponse);
      // plans、question、conflict 共用一个响应契约。有新方案时默认选择第一项，
      // 保证右侧详情面板始终有确定内容；用户之后可以点击其他方案切换。
      if (nextResponse.plans.length) {
        setSelectedPlanId(nextResponse.plans[0].plan_id);
        setRightTab("trip");
      }
      const assistantText =
        nextResponse.question?.question ??
        nextResponse.reply ??
        nextResponse.conflict?.message;
      if (assistantText) {
        setMessages((current) => [
          ...current,
          { id: crypto.randomUUID(), role: "assistant", content: assistantText },
        ]);
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
    void submit(input);
  }

  function resetSession() {
    // 新建规划只切换活跃会话，不删除历史；空会话仍采用首次提交时懒创建。
    setSessionId(null);
    setMessages([]);
    setResponse(null);
    setSelectedPlanId(null);
    setFailedRequest(null);
    setError("");
    updateSessionUrl(null);
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <aside className="session-sidebar" aria-label="会话导航">
        <div className="brand-block">
          <div className="brand-mark"><Compass size={20} aria-hidden="true" /></div>
          <div>
            <strong>HappyFreeTime</strong>
            <span>北京周末规划</span>
          </div>
        </div>
        <button className="new-session-button" type="button" onClick={resetSession}>
          <Plus size={17} aria-hidden="true" />
          新建规划
        </button>
        <div className="sidebar-section-label"><History size={14} aria-hidden="true" />最近会话</div>
        <div className="session-history-list">
          {recentSessions.map((session) => (
            <button
              className={`session-row ${session.session_id === sessionId ? "active" : ""}`}
              type="button"
              key={session.session_id}
              aria-current={session.session_id === sessionId ? "page" : undefined}
              onClick={() => void openSession(session.session_id)}
            >
              <MessageSquareText size={16} aria-hidden="true" />
              <span>
                <strong>{session.title}</strong>
                <small>{statusLabel(session.status)} · {session.last_message_preview}</small>
              </span>
              <ChevronRight size={15} aria-hidden="true" />
            </button>
          ))}
          {!recentSessions.length ? <p className="session-history-empty">完成一次规划后会显示在这里</p> : null}
        </div>
        <div className="sidebar-lower-slot" aria-hidden="true" />
        <div className="sidebar-footer">
          <span className="status-dot" />
          本地演示模式
        </div>
      </aside>

      <main className="workspace" id="main-content">
        <header className="workspace-header">
          <div>
            <span className="eyebrow">规划工作区</span>
            <h1>把空闲时间安排得刚刚好</h1>
          </div>
          <div className="location-chip"><MapPin size={15} aria-hidden="true" />北京 · 朝阳区</div>
        </header>

        {response?.constraint_summary.length ? (
          <section className="assumption-strip" aria-label="当前规划约束">
            <span className="strip-title"><Sparkles size={15} aria-hidden="true" />当前约束</span>
            {response.constraint_summary.map((item) => (
              <span className="assumption-chip" key={item.field} title={constraintSourceLabel(item)}>
                <strong>{ASSUMPTION_LABELS[item.field] ?? item.field}</strong>
                {displayConstraint(item)} · {constraintSourceLabel(item)}{item.user_editable ? " · 可修改" : ""}
              </span>
            ))}
          </section>
        ) : null}

        {response?.provider_facts.map((fact) => (
          <section className={`provider-fact ${fact.degraded ? "degraded" : ""}`} aria-label="天气事实" key={`${fact.kind}-${fact.date}`}>
            <CloudRain size={16} aria-hidden="true" />
            <span><strong>{fact.city}{fact.district} · {fact.condition}</strong>{fact.temperature_c === null ? "" : ` · ${fact.temperature_c}℃`}</span>
            <small>{providerSourceLabel(fact)} · 核验于 {new Date(fact.verified_at).toLocaleString("zh-CN")}{fact.degraded ? ` · 已降级：${fact.degraded_reason}` : " · 未降级"}</small>
          </section>
        ))}

        {response?.catalog_violations.length ? (
          <section className="catalog-summary" aria-label="Catalog 单资源剪枝摘要">
            <strong>组合前已排除 {new Set(response.catalog_violations.map((item) => item.resource_id)).size} 个不满足单资源硬约束的地点</strong>
            <span>原因已结构化记录，未用低分或文案掩盖硬约束违反。</span>
          </section>
        ) : null}

        {response?.catalog_warnings.length ? (
          <section className="catalog-summary warning" aria-label="Catalog 待确认信息">
            <strong>最终方案有 {new Set(response.catalog_warnings.map((item) => item.resource_id)).size} 个地点的信息待确认</strong>
            <span>缺失事实保持未知，没有用 Mock 补齐；请在出发前核对营业时间或亲子适配。</span>
          </section>
        ) : null}

        <section className="conversation" aria-live="polite">
          {messages.length === 0 ? (
            <div className="empty-conversation">
              <div className="empty-icon"><Route size={26} aria-hidden="true" /></div>
              <h2>从一句自然语言开始</h2>
              <p>告诉我时间、同行人和大致偏好，缺少的非关键条件会以可修改假设补全。</p>
              <div className="suggestion-list">
                {SUGGESTIONS.map((suggestion) => (
                  <button key={suggestion} type="button" onClick={() => void submit(suggestion)}>
                    {suggestion}<ChevronRight size={15} aria-hidden="true" />
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="message-list">
              {messages.map((message) => (
                <div className={`message ${message.role}`} key={message.id}>
                  <span>{message.role === "user" ? "你" : "规划助手"}</span>
                  <p>{message.content}</p>
                </div>
              ))}
              {loading ? <div className="thinking-row"><span /><span /><span />正在整理可行方案</div> : null}
            </div>
          )}
        </section>

        {error ? <div className="error-banner" role="alert">{error}</div> : null}

        {response?.conflict ? (
          <section className="conflict-panel" role="status">
            <strong>{response.conflict.message}</strong>
            <div>{response.conflict.relaxation_options.map((option) => <span key={option}>{option}</span>)}</div>
          </section>
        ) : null}

        {response?.plans.length ? (
          <section className="plan-section" aria-labelledby="plan-heading">
            <div className="section-heading">
              <div><span className="eyebrow">候选方案</span><h2 id="plan-heading">都可行，取舍不同</h2></div>
              <span>{response.plans.length} 个结果</span>
            </div>
            <div className="plan-grid">
              {response.plans.map((plan, index) => (
                <PlanCard
                  key={plan.plan_id}
                  plan={plan}
                  index={index}
                  selected={(selectedPlan?.plan_id ?? response.plans[0].plan_id) === plan.plan_id}
                  onSelect={() => setSelectedPlanId(plan.plan_id)}
                />
              ))}
            </div>
          </section>
        ) : null}

        <form className="composer" onSubmit={onSubmit}>
          <label htmlFor="planning-input">
            {response?.question ? "补充这个信息后继续" : "描述你的空闲时间和偏好"}
          </label>
          <div className="composer-row">
            <input
              id="planning-input"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder={response?.question?.question ?? "例如：周六下午和朋友出去玩，人均150"}
              disabled={loading || restoring}
            />
            <button type="submit" disabled={loading || restoring || !input.trim()} aria-label="发送需求">
              <Send size={18} aria-hidden="true" />
            </button>
          </div>
        </form>
      </main>

      <aside className="detail-panel" aria-label="方案详情">
        <div className="detail-tabs" role="tablist" aria-label="详情视图">
          <TabButton icon={<CalendarDays size={16} />} label="行程" active={rightTab === "trip"} onClick={() => setRightTab("trip")} />
          <TabButton icon={<Map size={16} />} label="地图" active={rightTab === "map"} onClick={() => setRightTab("map")} />
          <TabButton icon={<ReceiptText size={16} />} label="订单" active={rightTab === "orders"} onClick={() => setRightTab("orders")} />
        </div>
        {rightTab === "trip" ? <TripPanel plan={selectedPlan} /> : null}
        {rightTab === "map" ? <MapPanel plan={selectedPlan} /> : null}
        {rightTab === "orders" ? <OrdersPanel /> : null}
      </aside>
    </div>
  );
}

function PlanCard({ plan, index, selected, onSelect }: { plan: Plan; index: number; selected: boolean; onSelect: () => void }) {
  return (
    <article className={`plan-card ${selected ? "selected" : ""}`}>
      <button className="plan-select" type="button" onClick={onSelect} aria-pressed={selected}>
        <span className="plan-index">0{index + 1}</span>
        <span className="strategy-badge">{strategyLabel(plan.strategy)}</span>
        <h3>{plan.title}</h3>
        <div className="plan-metrics">
          <span><CircleDollarSign size={15} />{planPriceLabel(plan)}</span>
          <span><Clock3 size={15} />{plan.total_duration_minutes} 分钟</span>
        </div>
        <div className="stop-preview">
          {plan.stops.map((stop) => (
            <span key={stop.resource_id}>{stop.type === "restaurant" ? <Utensils size={14} /> : <MapPin size={14} />}{stop.name}</span>
          ))}
        </div>
        <p>{plan.highlights.slice(0, 2).join(" · ") || "时间线与路线均已校验"}</p>
        <span className="select-label">{selected ? "已选中查看" : "查看详情"}<ChevronRight size={15} /></span>
      </button>
    </article>
  );
}

function TabButton({ icon, label, active, onClick }: { icon: ReactNode; label: string; active: boolean; onClick: () => void }) {
  return <button type="button" role="tab" aria-selected={active} className={active ? "active" : ""} onClick={onClick}>{icon}{label}</button>;
}

function TripPanel({ plan }: { plan?: Plan }) {
  if (!plan) return <DetailEmpty icon={<CalendarDays size={24} />} title="行程将在这里展开" text="生成方案后，可逐站查看时间、价格和路线来源。" />;
  return (
    <div className="trip-detail">
      <div className="detail-title"><span className="eyebrow">当前查看</span><h2>{plan.title}</h2><p>总计 {planPriceLabel(plan)} · {plan.total_duration_minutes} 分钟</p></div>
      <ol className="timeline">
        {plan.stops.map((stop, index) => (
          <li key={stop.resource_id}>
            <div className="timeline-time">{stop.start}<span>{stop.end}</span></div>
            <div className="timeline-marker">{index + 1}</div>
            <div className="timeline-content">
              <StopImage stop={stop} />
              <span>{stop.type === "restaurant" ? "餐饮" : "活动"}</span>
              <strong>{stop.name}</strong>
              <small>{stop.duration_minutes} 分钟 · {priceLabel(stop)}</small>
              <small>{catalogSourceLabel(stop.source)} · 采集于 {stop.source.collected_at.slice(0, 10)}</small>
              {stop.image ? <small>图片：{stop.image.attribution || stop.image.author || stop.image.license} · <a href={stop.image.license_uri} target="_blank" rel="noreferrer">许可</a></small> : null}
            </div>
            {plan.route_legs[index + 1] ? <div className="route-note"><Route size={14} />下一程 {plan.route_legs[index + 1].distance_km}km · 约 {plan.route_legs[index + 1].duration_minutes} 分钟</div> : null}
          </li>
        ))}
      </ol>
      {plan.tradeoffs.length ? <div className="tradeoff"><strong>需要留意</strong><p>{plan.tradeoffs.slice(0, 2).join("；")}</p></div> : null}
    </div>
  );
}

function StopImage({ stop }: { stop: Plan["stops"][number] }) {
  const [failed, setFailed] = useState(false);
  if (!stop.image || failed) {
    const label = stop.category_tags[0] || (stop.type === "restaurant" ? "餐饮" : "活动");
    return <div className={`stop-image placeholder ${stop.type}`} aria-label={`${stop.name}暂无可靠图片`}>
      {stop.type === "restaurant" ? <Utensils size={20} aria-hidden="true" /> : <ImageIcon size={20} aria-hidden="true" />}
      <span>{label}</span>
    </div>;
  }
  return (
    <a href={stop.image.source_uri} target="_blank" rel="noreferrer" aria-label={`查看${stop.name}图片来源`}>
      <img
        className="stop-image"
        src={stop.image.url}
        alt={stop.name}
        loading="lazy"
        referrerPolicy="no-referrer"
        onError={() => setFailed(true)}
      />
    </a>
  );
}

function MapPanel({ plan }: { plan?: Plan }) {
  const sources = plan ? [...new Set(plan.route_legs.map((leg) => routeSourceLabel(leg.source)))] : [];
  const sourceSummary = sources.length === 1 ? sources[0] : sources.length > 1 ? "多来源路线" : "路线摘要";
  return (
    <div className="map-detail">
      {plan ? <AmapPlanMap plan={plan} /> : <div className="map-canvas amap-map-status disabled">生成方案后显示地图路线</div>}
      <div className="detail-title"><span className="eyebrow">路线来源</span><h2>{sourceSummary}</h2><p>{plan ? `${plan.route_legs.reduce((sum, leg) => sum + leg.distance_km, 0).toFixed(1)} km · 已按路线耗时重建时间线` : "生成方案后显示路线摘要"}</p></div>
      {plan?.route_legs.map((leg, index) => <div className="route-row" key={`${leg.destination_name}-${index}`}><Route size={17} /><span><strong>{leg.destination_name}</strong><small>{leg.start}–{leg.end} · {leg.distance_km}km · {leg.duration_minutes} 分钟 · {routeSourceLabel(leg.source)}{leg.degraded ? ` · 已降级：${leg.degraded_reason}` : ""}</small></span></div>)}
    </div>
  );
}

function OrdersPanel() {
  return <DetailEmpty icon={<ReceiptText size={24} />} title="尚无执行订单" text="方案确认、幂等下单与失败补偿将在 M4 接入。" />;
}

function DetailEmpty({ icon, title, text }: { icon: ReactNode; title: string; text: string }) {
  return <div className="detail-empty"><div>{icon}</div><h2>{title}</h2><p>{text}</p></div>;
}

export default App;
