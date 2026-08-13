import { FormEvent, type ReactNode, useMemo, useState } from "react";
import {
  CalendarDays,
  ChevronRight,
  CircleDollarSign,
  Clock3,
  Compass,
  History,
  Map,
  MapPin,
  MessageSquareText,
  Plus,
  ReceiptText,
  Route,
  Send,
  Sparkles,
  Utensils,
} from "lucide-react";

import { createSession, sendMessage } from "./api";
import type { AgentResponse, Assumption, ChatMessage, Plan } from "./types";

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

function strategyLabel(strategy: string): string {
  const labels: Record<string, string> = {
    safe: "稳妥",
    rich: "体验",
    budget: "省钱",
  };
  return labels[strategy] ?? strategy;
}

function App() {
  // M1 在组件内只维护一个活跃会话。后端已经支持多个隔离会话，但“加载历史
  // 会话列表”属于后续前端切片，所以当前新建按钮只重置本地活跃状态。
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [response, setResponse] = useState<AgentResponse | null>(null);
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);
  const [rightTab, setRightTab] = useState<"trip" | "map" | "orders">("trip");

  const selectedPlan = useMemo(
    () => response?.plans.find((plan) => plan.plan_id === selectedPlanId) ?? response?.plans[0],
    [response, selectedPlanId],
  );

  async function submit(content: string) {
    const trimmed = content.trim();
    if (!trimmed || loading) return;
    setLoading(true);
    setError("");
    setMessages((current) => [
      ...current,
      { id: crypto.randomUUID(), role: "user", content: trimmed },
    ]);
    setInput("");
    try {
      // 懒创建后端会话：只打开页面不会生成空数据库记录。反问答案继续复用
      // activeSession，因此后端能通过同一个 thread_id 恢复 checkpoint。
      const activeSession = sessionId ?? (await createSession());
      if (!sessionId) setSessionId(activeSession);
      const nextResponse = await sendMessage(activeSession, trimmed);
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
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "请求失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void submit(input);
  }

  function resetSession() {
    // 这里只结束前端当前会话，不删除 SQLite 中的旧记录；未来历史列表可恢复它。
    setSessionId(null);
    setMessages([]);
    setResponse(null);
    setSelectedPlanId(null);
    setError("");
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
        <button className="session-row active" type="button">
          <MessageSquareText size={16} aria-hidden="true" />
          <span>
            <strong>{messages[0]?.content.slice(0, 16) || "新的周末计划"}</strong>
            <small>{sessionId ? "进行中" : "等待开始"}</small>
          </span>
          <ChevronRight size={15} aria-hidden="true" />
        </button>
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

        {response?.assumptions.length ? (
          <section className="assumption-strip" aria-label="当前规划假设">
            <span className="strip-title"><Sparkles size={15} aria-hidden="true" />本次假设</span>
            {response.assumptions.map((assumption) => (
              <span className="assumption-chip" key={assumption.field} title={assumption.reason}>
                <strong>{ASSUMPTION_LABELS[assumption.field] ?? assumption.field}</strong>
                {displayAssumption(assumption)}
              </span>
            ))}
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
              disabled={loading}
            />
            <button type="submit" disabled={loading || !input.trim()} aria-label="发送需求">
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
          <span><CircleDollarSign size={15} />¥{plan.total_price}</span>
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
      <div className="detail-title"><span className="eyebrow">当前查看</span><h2>{plan.title}</h2><p>总计 ¥{plan.total_price} · {plan.total_duration_minutes} 分钟</p></div>
      <ol className="timeline">
        {plan.stops.map((stop, index) => (
          <li key={stop.resource_id}>
            <div className="timeline-time">{stop.start}<span>{stop.end}</span></div>
            <div className="timeline-marker">{index + 1}</div>
            <div className="timeline-content"><span>{stop.type === "restaurant" ? "餐饮" : "活动"}</span><strong>{stop.name}</strong><small>{stop.duration_minutes} 分钟 · ¥{stop.price}</small></div>
            {plan.route_legs[index + 1] ? <div className="route-note"><Route size={14} />下一程 {plan.route_legs[index + 1].distance_km}km · 约 {plan.route_legs[index + 1].duration_minutes} 分钟</div> : null}
          </li>
        ))}
      </ol>
      {plan.tradeoffs.length ? <div className="tradeoff"><strong>需要留意</strong><p>{plan.tradeoffs.slice(0, 2).join("；")}</p></div> : null}
    </div>
  );
}

function MapPanel({ plan }: { plan?: Plan }) {
  return (
    <div className="map-detail">
      <div className="map-canvas" aria-label="M1 本地路线示意图">
        <div className="road road-a" /><div className="road road-b" /><div className="road road-c" />
        <div className="route-line" />
        <span className="map-marker start">起</span><span className="map-marker stop-one">1</span><span className="map-marker stop-two">2</span>
      </div>
      <div className="detail-title"><span className="eyebrow">路线来源</span><h2>本地估算</h2><p>{plan ? `${plan.route_legs.reduce((sum, leg) => sum + leg.distance_km, 0).toFixed(1)} km · 高德将在 M2 接入` : "生成方案后显示路线摘要"}</p></div>
      {plan?.route_legs.map((leg, index) => <div className="route-row" key={`${leg.destination_name}-${index}`}><Route size={17} /><span><strong>{leg.destination_name}</strong><small>{leg.distance_km}km · {leg.duration_minutes} 分钟 · 估算</small></span></div>)}
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
