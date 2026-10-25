import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { SessionNavigation } from "./SessionNavigation";
import type { AgentResponse, Plan, PlanningContextSummary, PlanningRunEvent, RecoveryAction } from "./types";

const api = vi.hoisted(() => ({
  createSession: vi.fn(),
  deleteSession: vi.fn(),
  getSession: vi.fn(),
  listSessions: vi.fn(),
  renameSession: vi.fn(),
  searchDepartureLocations: vi.fn(),
  updatePlanningContext: vi.fn(),
  replanPlanningContext: vi.fn(),
  sendMessage: vi.fn(),
  sendMessageStream: vi.fn(),
  selectPlan: vi.fn(),
}));

vi.mock("./api", () => ({
  ...api,
  getMapConfig: vi.fn(),
}));

vi.mock("./AmapPlanMap", () => ({
  AmapPlanMap: ({ activeLegIndex, onFocusRoute, onOpenPoi }: { activeLegIndex: number | null; onFocusRoute: (index: number) => void; onOpenPoi: (index: number) => void }) => (
    <div><button type="button" onClick={() => onFocusRoute(1)}>测试地图，当前路线 {activeLegIndex ?? "none"}</button><button type="button" onClick={() => onOpenPoi(0)}>测试地图标记，打开第一站</button></div>
  ),
}));

const source = {
  source_name: "测试目录",
  source_uri: "https://example.test/catalog",
  source_license: "test",
  collected_at: "2026-08-29T00:00:00Z",
  last_verified_at: "2026-08-29T00:00:00Z",
  verification_status: "verified" as const,
};

function plan(planId: string, title: string): Plan {
  return {
    plan_id: planId,
    composition_fingerprint: planId,
    skeleton_id: "test",
    title,
    strategy: "balanced",
    total_score: 10,
    total_price: 88,
    price_status: "known",
    total_duration_minutes: 180,
    stops: [
      {
        resource_id: `${planId}-activity`, type: "activity", role: "activity", name: "展览", start: "14:10", end: "15:10", duration_minutes: 60, price: 30, price_kind: "known", category_tags: ["展览"], image: null, source,
      },
      {
        resource_id: `${planId}-meal`, type: "restaurant", role: "meal", name: "晚餐", start: "15:20", end: "16:20", duration_minutes: 60, price: 58, price_kind: "known", category_tags: ["餐厅"], image: null, source,
      },
    ],
    route_legs: [
      { origin_name: "出发地", destination_name: "展览", start: "14:00", end: "14:10", mode: "taxi", distance_km: 1, duration_minutes: 10, source: "local_estimate", provider_mode: "mock", verified_at: null, cache_age_seconds: null, geometry: [], degraded: true, degraded_reason: "test" },
      { origin_name: "展览", destination_name: "晚餐", start: "15:10", end: "15:20", mode: "taxi", distance_km: 2, duration_minutes: 10, source: "local_estimate", provider_mode: "mock", verified_at: null, cache_age_seconds: null, geometry: [], degraded: true, degraded_reason: "test" },
      { origin_name: "晚餐", destination_name: "出发地", start: "16:20", end: "16:35", mode: "taxi", distance_km: 2, duration_minutes: 15, source: "local_estimate", provider_mode: "mock", verified_at: null, cache_age_seconds: null, geometry: [], degraded: true, degraded_reason: "test" },
    ],
    score_breakdown: [], highlights: [], tradeoffs: [],
  };
}

const response: AgentResponse = {
  status: "completed", reply: "已生成方案", question: null, assumptions: [], constraint_summary: [],
  plans: [plan("plan-one", "方案一"), plan("plan-two", "方案二")], conflict: null,
  provider_facts: [], catalog_violations: [], catalog_warnings: [], plan_version_id: "v1",
  poi_presentations: [],
};

function planningContext(overrides: Partial<PlanningContextSummary> = {}): PlanningContextSummary {
  const field = (displayValue: string) => ({ value: displayValue, display_value: displayValue, source: "default" as const, editable: true, status: "resolved" as const });
  return {
    request_revision: 1,
    request_view: "committed",
    committed_request_revision: 1,
    planned_request_revision: 1,
    has_active_plan: true,
    plan_stale: false,
    ready_for_planning: true,
    where: field("北京市朝阳区"),
    when: {
      date: field("2026-10-19"),
      start_at: field("14:00"),
      end_at: field("18:00"),
      start_kind: "trip_start",
      end_kind: "trip_end",
    },
    who: field("1 位成人"),
    budget: field("不限"),
    preferences: field("未设置"),
    exact_stop_count: field("未设置"),
    max_distance_km: field("未设置"),
    total_distance_km: field("未设置"),
    pending_field: null,
    ...overrides,
  };
}

function planningContextForArea(area: string, overrides: Partial<PlanningContextSummary> = {}): PlanningContextSummary {
  const base = planningContext(overrides);
  return {
    ...base,
    where: { ...base.where, value: { address: area }, display_value: area, source: "user" },
  };
}

function restoredSession(sessionId: string, title: string, latestResponse: AgentResponse) {
  const timestamp = "2026-08-20T02:00:00Z";
  return {
    session_id: sessionId,
    title,
    status: "completed" as const,
    created_at: "2026-08-20T00:00:00Z",
    updated_at: timestamp,
    messages: [{ id: `${sessionId}-message`, role: "assistant" as const, content: latestResponse.reply ?? "" }],
    plans: latestResponse.plans,
    latest_response: latestResponse,
    response_history: [latestResponse],
    active_plan_version_id: latestResponse.plan_version_id ?? null,
    selected_plan_id: null,
    planning_context: latestResponse.planning_context ?? null,
  };
}

function presentation(resourceId: string, name: string) {
  return {
    resource_id: resourceId, name, category_label: "展览", business_area: "朝阳区", address: "测试路 1 号", description: "一段可展开的 POI 详情说明。",
    scene_tags: ["慢慢逛"], facility_tags: ["休息区"], indoor: true, weather_suitability: "室内为主，阴雨天也适合", child_suitability: "适合亲子同行（演示规则）",
    reference_avg_price: 30, demo_rating: 4.6, demo_review_count: 1280, opening_hours_display: "周一至周日 09:30–17:30", opening_status: "已纳入本次营业时间校验",
    suggested_duration_minutes: 60, reservation_requirement: "无需预约", queue_profile: "周末下午可能有客流", risk_tips: ["闭馆前请预留入场时间"], booking_mode: "现场入场",
    gallery: [{ url: "/demo-illustrations/outing.svg", alt: "展览场景示意图（非门店实拍）", kind: "illustrative" as const, attribution: "test", license: "test" }],
    data_notice: "演示环境：POI 商业信息为模拟数据；路线来源和降级状态见各路线段。",
  };
}

describe("planning workspace", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
    window.history.replaceState({}, "", "/");
    api.listSessions.mockReset();
    api.createSession.mockReset();
    api.deleteSession.mockReset();
    api.getSession.mockReset();
    api.renameSession.mockReset();
    api.searchDepartureLocations.mockReset().mockResolvedValue({ city: "北京市", source: "mock", mode: "mock", degraded: false, results: [] });
    api.updatePlanningContext.mockReset();
    api.replanPlanningContext.mockReset();
    api.sendMessage.mockReset();
    api.sendMessageStream.mockReset();
    api.selectPlan.mockReset();
    api.listSessions.mockResolvedValue([]);
    api.deleteSession.mockResolvedValue(undefined);
    api.renameSession.mockResolvedValue(undefined);
    api.createSession.mockResolvedValue("session-test");
    api.updatePlanningContext.mockResolvedValue(response);
    api.replanPlanningContext.mockResolvedValue(response);
    api.sendMessage.mockResolvedValue(response);
    api.sendMessageStream.mockImplementation((sessionId: string, content: string, requestId: string, _onProgress: unknown, conversationCommand: unknown, clarificationReply: unknown, recoveryAction: unknown) => api.sendMessage(sessionId, content, requestId, conversationCommand, clarificationReply, recoveryAction));
    api.selectPlan.mockResolvedValue({ active_plan_version_id: "v1", selected_plan_id: "plan-one" });
  });

  it("switches plans and keeps timeline, map rows, and RouteLeg indices aligned", async () => {
    const user = userEvent.setup();
    render(<App />);
    expect(document.querySelector(".app-header-tagline")).toHaveTextContent("周末，轻松出发");
    expect(document.querySelector(".app-header-title")).not.toHaveTextContent("HappyFreeTime");
    expect(screen.getByText("YOUR WEEKEND, YOUR WAY")).toBeInTheDocument();
    expect(document.querySelector(".welcome-sun")).toBeInTheDocument();
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");
    expect(screen.queryByText("周末管家", { exact: true })).not.toBeInTheDocument();

    const tripTab = screen.getByRole("tab", { name: "行程" });
    tripTab.focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "地图" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "地图" })).toHaveFocus();
    await user.keyboard("{Home}");
    expect(tripTab).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{End}");
    const evidenceTab = screen.getByRole("tab", { name: "核验与来源" });
    expect(evidenceTab).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("tab", { name: "地图" })).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowLeft}");
    expect(tripTab).toHaveAttribute("aria-selected", "true");

    await user.click(screen.getByRole("button", { name: "预览方案二" }));
    expect(screen.getByRole("button", { name: "预览方案二" })).toHaveAttribute("aria-pressed", "true");
    expect(document.querySelector(".inspector-plan-header")).toHaveTextContent("方案二");
    expect(api.selectPlan).not.toHaveBeenCalled();
    expect(screen.getAllByRole("button", { name: /查看返程/ })).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: /查看出发路线：出发地 → 展览/ }));
    expect(screen.getByRole("tab", { name: "地图" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("测试地图，当前路线 0")).toBeInTheDocument();
    // Route source now also appears on each candidate card, so scope this
    // assertion to the map row the test is actually about.
    expect(screen.getByRole("button", { name: "聚焦第 1 段路线：出发地 到 展览" })).toHaveTextContent("本地估算");

    await user.click(screen.getByRole("button", { name: "测试地图，当前路线 0" }));
    expect(screen.getByRole("tab", { name: "地图" })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("button", { name: "聚焦第 2 段路线：展览 到 晚餐" }));
    expect(screen.getByRole("tab", { name: "地图" })).toHaveAttribute("aria-selected", "true");
    const routeSection = document.querySelector(".map-route-list");
    const stopSection = document.querySelector(".map-stop-list");
    expect(routeSection).toBeInTheDocument();
    expect(stopSection).toBeInTheDocument();
    expect(routeSection!.compareDocumentPosition(stopSection!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await user.click(screen.getAllByRole("button", { name: "在行程中定位" })[1]);
    expect(screen.getByRole("tab", { name: "行程" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: /查看下一程：2km/ })).toHaveAttribute("aria-pressed", "true");
  });

  it.each([false, true])("gates developer traces while keeping public evidence available enabled=%s", async (enabled) => {
    if (enabled) vi.stubEnv("VITE_SHOW_DEVELOPER_DETAILS", "true");
    const user = userEvent.setup();
    api.sendMessage.mockResolvedValueOnce({
      ...response,
      runtime_decisions: [{
        stage: "candidate_retrieval",
        adapter: "rule_based",
        model_invoked: false,
        model_name: null,
        attempts: 0,
        fallback_reason: null,
        latency_ms: 12,
        input_tokens: null,
        output_tokens: null,
      }, {
        stage: "recommendation_advisor",
        adapter: "llm",
        model_invoked: true,
        model_name: "test-model",
        attempts: 1,
        fallback_reason: null,
        latency_ms: 20,
        input_tokens: 12,
        output_tokens: 7,
      }],
      retrieval_mode: "向量检索",
      retrieval_index_version: "test-index",
      search_mode: "hybrid",
      search_expansions: 1,
      search_finalist_count: 3,
      run_trace: {
        schema_version: "planning-run-trace.v1",
        run_id: "run-ui-1",
        events: [
          { schema_version: "planning-run-event.v1", run_id: "run-ui-1", sequence: 1, stage: "understand", status: "completed", message_key: "understand.completed", public_message: "已完成需求理解", occurred_at: "2026-10-07T10:00:00Z", duration_ms: 3, public_details: {} },
          { schema_version: "planning-run-event.v1", run_id: "run-ui-1", sequence: 2, stage: "verify", status: "completed", message_key: "verify.completed", public_message: "路线和硬约束核对完成", occurred_at: "2026-10-07T10:00:01Z", duration_ms: 20, public_details: { plan_count: 2 } },
        ],
      },
    });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");
    expect(screen.getByText("规划完成 · 查看本轮处理过程")).toBeInTheDocument();
    await user.click(screen.getByText("规划完成 · 查看本轮处理过程"));
    await user.click(screen.getByRole("tab", { name: "核验与来源" }));
    expect(screen.getByRole("tab", { name: "核验与来源" })).toHaveAttribute("aria-selected", "true");
    expect(document.querySelector(".inspector-evidence-disclosure")).toBeNull();

    expect(screen.queryByText("本轮处理过程")).not.toBeInTheDocument();
    expect(screen.getByText("数据与证据")).toBeInTheDocument();
    const evidencePanel = document.querySelector(".evidence-panel") as HTMLElement;
    const riskGroup = evidencePanel.querySelector(".risk-group");
    expect(riskGroup).toBeInTheDocument();
    expect(screen.getByText("已完成需求理解")).toBeInTheDocument();
    expect(screen.getByText("路线和硬约束核对完成")).toBeInTheDocument();
    if (!enabled) {
      expect(screen.queryByText("模型调用")).not.toBeInTheDocument();
      expect(screen.queryByText("开发者详情")).not.toBeInTheDocument();
      return;
    }
    expect(screen.getByText("模型调用")).toBeInTheDocument();
    const runtimeGroup = evidencePanel.querySelector(".runtime-call-group") as HTMLDetailsElement;
    expect(riskGroup!.compareDocumentPosition(runtimeGroup) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(runtimeGroup.open).toBe(false);
    expect(screen.getByText("未调用模型 · 规则基线")).not.toBeVisible();
    await user.click(within(runtimeGroup).getByText("模型调用"));
    expect(runtimeGroup.open).toBe(true);
    expect(screen.getByText("未调用模型 · 规则基线")).toBeInTheDocument();
    expect(screen.getByText("调用模型 · test-model")).toBeInTheDocument();
    expect(screen.getByText(/20 ms · 尝试 1 次 · tokens 12\/7/)).toBeInTheDocument();
    expect([...document.querySelectorAll("details > summary")].every((summary) => summary.querySelector(".disclosure-chevron"))).toBe(true);
    await user.click(screen.getByText("开发者详情"));
    expect(screen.getByText("检索和搜索诊断")).toBeInTheDocument();
    const developerDetails = document.querySelector(".developer-details") as HTMLElement;
    expect(developerDetails.querySelectorAll(".evidence-row")).toHaveLength(0);
    expect(within(developerDetails).getByText(/向量检索 · test-index/)).toBeInTheDocument();
  });

  it("scopes verification evidence to the viewed plan and deduplicates risks", async () => {
    const user = userEvent.setup();
    api.sendMessage.mockResolvedValueOnce({
      ...response,
      retrieval_evidence: [
        { evidence_id: "poi.plan-one-activity.1", source_type: "poi_profile", source_field: "scene_tags", summary: "方案一安静", source_ref: "plan-one-activity", confidence: 0.9 },
        { evidence_id: "poi.plan-two-activity.1", source_type: "poi_profile", source_field: "scene_tags", summary: "方案二热闹", source_ref: "plan-two-activity", confidence: 0.9 },
      ],
      recommendation_advice: {
        recommended_plan_id: "plan-one",
        understood_needs: [],
        overall_reason: "方案一更匹配。",
        plans: [
          { plan_id: "plan-one", reason: "安静", matched_need_ids: [], supporting_evidence_ids: ["poi.plan-one-activity.1"], tradeoffs: [] },
          { plan_id: "plan-two", reason: "热闹", matched_need_ids: [], supporting_evidence_ids: ["poi.plan-two-activity.1"], tradeoffs: [] },
        ],
        adapter: "rule_based",
        fallback_reason: null,
        prompt_version: "test",
        model_name: null,
        model_invoked: false,
        attempts: 0,
        latency_ms: 0,
      },
      warnings: [
        { code: "route_fact_degraded", message: "路线使用估算。", plan_id: "plan-one", resource_id: null, route_leg_index: 0, source: null, degraded: true, stale: false },
        { code: "route_fact_degraded", message: "路线使用估算。", plan_id: "plan-one", resource_id: null, route_leg_index: 0, source: null, degraded: true, stale: false },
        { code: "route_fact_degraded", message: "方案二不应展示。", plan_id: "plan-two", resource_id: null, route_leg_index: 0, source: null, degraded: true, stale: false },
      ],
    });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");
    await user.click(screen.getByRole("tab", { name: "核验与来源" }));

    expect(screen.getByText("方案一安静")).not.toBeVisible();
    await user.click(screen.getByText("数据与证据"));
    expect(screen.getByText("方案一安静")).toBeVisible();
    expect(screen.queryByText("方案二热闹")).not.toBeInTheDocument();
    expect(screen.getAllByText("路线事实需要确认")).toHaveLength(1);
    expect(within(document.querySelector(".evidence-panel") as HTMLElement).queryByText("方案二不应展示。")).not.toBeInTheDocument();
  });

  it("does not present simulated POI ratings as facts and manages history", async () => {
    const user = userEvent.setup();
    api.listSessions.mockResolvedValue([{
      session_id: "history-to-manage",
      title: "周末出游规划",
      status: "completed",
      created_at: "2026-10-04T00:00:00Z",
      updated_at: "2026-10-04T01:00:00Z",
      last_message_preview: "已生成方案",
    }]);
    api.sendMessage.mockResolvedValue({
      ...response,
      poi_presentations: response.plans.flatMap((item) => item.stops.map((stop) => presentation(stop.resource_id, stop.name))),
    });
    render(<App />);

    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findByRole("heading", { name: "方案一", level: 3 });
    expect(document.querySelector(".plan-grid")).not.toHaveTextContent("演示评分");
    await user.click(screen.getByRole("button", { name: "打开方案一详情" }));
    await user.click(screen.getByRole("button", { name: "查看第 1 站 展览 详情" }));
    expect(screen.queryByText(/演示评分|演示评论/)).not.toBeInTheDocument();
    await user.click(screen.getByText("数据与来源说明"));
    expect(document.querySelector(".detail-panel")).toHaveTextContent("POI 商业信息为模拟数据");

    await screen.findByText("周末出游规划");
    const sessionRow = document.querySelector<HTMLButtonElement>(".session-row-shell .session-row");
    expect(sessionRow).not.toBeNull();
    fireEvent.contextMenu(sessionRow!);
    await user.click(screen.getByRole("menuitem", { name: "重命名" }));
    const titleInput = screen.getByRole("textbox", { name: "规划名称" });
    await user.clear(titleInput);
    await user.type(titleInput, "周末晚餐安排");
    await user.click(screen.getByRole("button", { name: "保存名称" }));
    await waitFor(() => expect(api.renameSession).toHaveBeenCalledWith("history-to-manage", "周末晚餐安排"));
    expect(await screen.findByRole("button", { name: /周末晚餐安排/ })).toBeInTheDocument();

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    await user.click(screen.getByRole("button", { name: "更多会话操作" }));
    await user.click(screen.getByRole("menuitem", { name: "删除规划" }));
    await waitFor(() => expect(api.deleteSession).toHaveBeenCalledWith("history-to-manage"));
    expect(confirmSpy).toHaveBeenCalled();
    expect(await screen.findByText("完成一次规划后，会话和方案会保留在这里。")).toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("keeps only the active conversation marker in the collapsed sidebar", async () => {
    const user = userEvent.setup();
    const onOpenHistory = vi.fn();
    const sessions = [
      { session_id: "current", title: "当前规划", status: "completed", created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-02T00:00:00Z", last_message_preview: "已生成方案" },
      { session_id: "older-1", title: "上周出游", status: "completed", created_at: "2026-09-28T00:00:00Z", updated_at: "2026-09-29T00:00:00Z", last_message_preview: "已生成方案" },
      { session_id: "older-2", title: "晚餐计划", status: "completed", created_at: "2026-09-20T00:00:00Z", updated_at: "2026-09-21T00:00:00Z", last_message_preview: "已生成方案" },
    ];
    render(<SessionNavigation recentSessions={sessions} sessionId="current" onNew={vi.fn()} onOpen={vi.fn()} onRename={vi.fn()} onDelete={vi.fn()} collapsed onOpenHistory={onOpenHistory} />);

    expect(screen.getByRole("button", { name: "当前规划" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("button", { name: "上周出游" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "晚餐计划" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开历史会话" }));
    expect(onOpenHistory).toHaveBeenCalledOnce();
  });

  it("shows total party place fees without transport and preserves price uncertainty", async () => {
    const user = userEvent.setup();
    api.sendMessage.mockResolvedValue({
      ...response,
      plans: [
        { ...plan("known-price", "已知费用"), total_price: 300, price_status: "known" },
        { ...plan("estimated-price", "估算费用"), total_price: 260, price_status: "estimated" },
        { ...plan("incomplete-price", "待确认费用"), total_price: 180, price_status: "incomplete" },
      ],
    });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));

    expect(await screen.findByLabelText("全程费用合计（不含交通）：¥300")).toBeInTheDocument();
    expect(screen.getByLabelText("全程费用合计（不含交通）：约 ¥260")).toBeInTheDocument();
    expect(screen.getByLabelText("全程费用合计（不含交通）：已知 ¥180，其余待确认")).toBeInTheDocument();
    expect(screen.queryByText(/\/ 人/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("快速调整方案")).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/餐厅保留|换近一点/)).not.toBeInTheDocument();
  });

  it("renders only the returned candidate with a missing-image placeholder and truthful metrics", async () => {
    const user = userEvent.setup();
    const longTitle = "适合慢慢逛的室内展览与安静晚餐组合方案";
    api.sendMessage.mockResolvedValue({
      ...response,
      plans: [{ ...plan("only-plan", longTitle), total_price: 180, price_status: "incomplete" }],
    });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));

    expect(await screen.findByRole("heading", { name: longTitle, level: 3 })).toHaveAttribute("title", longTitle);
    expect(document.querySelectorAll(".plan-card")).toHaveLength(1);
    expect(screen.getByText("暂无可靠图片")).toBeInTheDocument();
    expect(screen.getByLabelText("全程费用合计（不含交通）：已知 ¥180，其余待确认")).toBeInTheDocument();
    expect(screen.getByLabelText("累计路线距离 5 公里")).toBeInTheDocument();
    expect(api.selectPlan).not.toHaveBeenCalled();
  });

  it("uses a labeled category photo and shows the complete time range and party place-fee total", async () => {
    const user = userEvent.setup();
    const photoUrl = "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f8/Exterior_IMG_5130_Beijing_Museum_of_Natural_History.jpg/960px-Exterior_IMG_5130_Beijing_Museum_of_Natural_History.jpg";
    api.sendMessage.mockResolvedValue({
      ...response,
      plans: [plan("photo-plan", "展览 + 晚餐")],
      poi_presentations: [{
        ...presentation("photo-plan-activity", "展览"),
        gallery: [{
          url: photoUrl, alt: "博物馆同类场景参考图，非该地点实拍", kind: "reference",
          attribution: "Bjoertvedt", license: "CC BY-SA 4.0",
          source_uri: "https://commons.wikimedia.org/wiki/File:Exterior_IMG_5130_Beijing_Museum_of_Natural_History.jpg",
          author: "Bjoertvedt", license_uri: "https://creativecommons.org/licenses/by-sa/4.0/",
        }],
      }],
    });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));

    const card = (await screen.findByRole("heading", { name: "展览 + 晚餐", level: 3 })).closest(".plan-card") as HTMLElement;
    expect(card.querySelector(".plan-card-cover img")).toHaveAttribute("src", photoUrl);
    expect(card.querySelector(".stop-image-reference-badge")).toHaveTextContent("同类参考");
    expect(card).toHaveTextContent("14:00–16:35");
    expect(card).toHaveTextContent("全程费用合计 ¥88");
  });

  it("uses a short header tagline and hides a route already named in the plan title", async () => {
    const user = userEvent.setup();
    api.sendMessage.mockResolvedValue({
      ...response,
      plans: [plan("named-route", "展览 + 晚餐"), plan("unnamed-route", "轻松的一日安排")],
    });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "安排一个周末行程");
    await user.click(screen.getByRole("button", { name: "发送需求" }));

    expect(screen.getByText("周末，轻松出发")).toBeInTheDocument();
    expect(document.querySelector(".app-header")?.textContent).not.toContain("展览 + 晚餐");
    const namedRouteCard = screen.getByRole("heading", { name: "展览 + 晚餐", level: 3 }).closest(".plan-card");
    const unnamedRouteCard = screen.getByRole("heading", { name: "轻松的一日安排", level: 3 }).closest(".plan-card");
    expect(namedRouteCard?.querySelector(".plan-card-stops")).toBeNull();
    expect(within(unnamedRouteCard as HTMLElement).getByLabelText("行程站点")).toHaveTextContent("展览 → 晚餐");
    expect(screen.queryByText("本次条件")).not.toBeInTheDocument();
  });

  it("renders grounded recommendation advice and marks the recommended plan", async () => {
    const user = userEvent.setup();
    api.sendMessage.mockResolvedValue({
      ...response,
      recommendation_advice: {
        recommended_plan_id: "plan-two",
        understood_needs: [
          { need_id: "constraint.budget_per_person", text: "人均120元", user_evidence_ids: ["user.budget.1"] },
          { need_id: "need.user.preferences.1", text: "轻松约会", user_evidence_ids: ["user.preferences.1"] },
        ],
        overall_reason: "方案二更直接回应了轻松约会需求。",
        plans: [
          { plan_id: "plan-one", reason: "方案一回应了你的需求。", matched_need_ids: ["need.user.preferences.1"], supporting_evidence_ids: ["user.preferences.1"], tradeoffs: ["路线更长"] },
          { plan_id: "plan-two", reason: "方案二更适合轻松约会。", matched_need_ids: ["need.user.preferences.1"], supporting_evidence_ids: ["user.preferences.1"], tradeoffs: ["可选活动较少"] },
        ],
        adapter: "rule_based",
        fallback_reason: null,
        prompt_version: "rule-based.v1",
        model_name: null,
        model_invoked: false,
        attempts: 0,
        latency_ms: 0,
      },
      constraint_summary: [
        { field: "departure_at", value: "12:00", source: "user_explicit", evidence: "中午出发", confidence: 1, rule_id: "departure_at.clock.zh_cn.v1", user_editable: true },
        { field: "party", value: { adults: 2, children: 0 }, source: "user_explicit", evidence: "两位成人", confidence: 1, rule_id: "party.count.v1", user_editable: true },
        { field: "budget_per_person", value: 120, source: "user_explicit", evidence: "人均120元", confidence: 1, rule_id: "budget.per_person.v1", user_editable: true },
      ],
    });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "安排一个轻松的约会");
    await user.click(screen.getByRole("button", { name: "发送需求" }));

    expect(screen.queryByText("已生成方案")).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "管家首选：方案二", level: 2 })).toBeInTheDocument();
    expect(screen.getByText("轻松约会", { selector: ".recommendation-tags span" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "为您整理了 2 个可选方案", level: 2 })).toBeInTheDocument();
    expect(screen.getByText("先比较方案摘要；点击卡片可预览，详情区可查看逐站安排、地图与核验信息。预览不会改变正式选择。", { selector: ".plan-results-intro p" })).toBeInTheDocument();
    expect(screen.queryByText("人均120元", { selector: ".recommendation-tags span" })).not.toBeInTheDocument();
    const constraintGroup = screen.getByRole("group", { name: "本轮规划参考的硬约束" });
    expect(constraintGroup).toHaveTextContent("出发 12:00");
    expect(constraintGroup).toHaveTextContent("同行人 2 人");
    expect(constraintGroup).toHaveTextContent("预算 人均 120 元");
    expect(screen.getByText("方案二更直接回应了轻松约会需求。", { selector: ".recommendation-summary p" })).toBeInTheDocument();
    const recommendationSummary = document.querySelector(".recommendation-summary");
    const resultsIntro = document.querySelector(".plan-results-intro");
    const planGrid = document.querySelector(".plan-grid");
    expect(recommendationSummary!.compareDocumentPosition(planGrid!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(resultsIntro!.compareDocumentPosition(planGrid!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(document.querySelector(".rich-reply-body")?.firstElementChild).toBe(resultsIntro);
    expect(screen.queryByText("找到喜欢的节奏")).not.toBeInTheDocument();
    expect(screen.queryByText("方案二更适合轻松约会。")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "预览方案二" }));
    expect(await screen.findByText("方案二更适合轻松约会。")).toBeInTheDocument();
    expect(screen.getAllByText(/主要取舍与注意事项/).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "方案二", level: 3 }).closest(".plan-card")).toHaveClass("recommended");
  });

  it("keeps replacement recommendation copy brief and leaves exact changes to the diff card", async () => {
    const user = userEvent.setup();
    api.sendMessage.mockResolvedValueOnce({
      ...response,
      conversation_command: { operation: "replace" },
      recommendation_advice: {
        recommended_plan_id: "plan-two",
        understood_needs: [{ need_id: "need.user.preferences.1", text: "更安静", user_evidence_ids: ["user.preferences.1"] }],
        overall_reason: "更推荐方案二，因为它更符合安静的偏好。其他站点保持不变，全程增加 1.2 km。修改结果：本候选将“旧地点”替换为“新地点”；其他 2 站保持不变；全程增加 1.2 km。",
        plans: [
          { plan_id: "plan-one", reason: "路线更长。", matched_need_ids: [], supporting_evidence_ids: [], tradeoffs: [] },
          { plan_id: "plan-two", reason: "更安静。", matched_need_ids: [], supporting_evidence_ids: [], tradeoffs: [] },
        ],
        adapter: "rule_based",
        fallback_reason: null,
        prompt_version: "test",
        model_name: null,
        model_invoked: false,
        attempts: 0,
        latency_ms: 0,
      },
    });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "换一个安静的地点");
    await user.click(screen.getByRole("button", { name: "发送需求" }));

    const summary = await screen.findByRole("heading", { name: "管家首选：方案二", level: 2 });
    const recommendation = summary.closest(".recommendation-summary");
    expect(within(recommendation as HTMLElement).getByText("更推荐方案二，因为它更符合安静的偏好。")).toBeInTheDocument();
    expect(recommendation).not.toHaveTextContent("其他站点保持不变");
    expect(recommendation).not.toHaveTextContent("全程增加 1.2 km");
  });

  it("shows a return-to-top control after scrolling the desktop inspector", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findByRole("heading", { name: "方案一", level: 3 });

    const detailPanel = document.querySelector(".detail-panel") as HTMLElement;
    detailPanel.scrollTop = 180;
    fireEvent.scroll(detailPanel);
    const returnToTop = await screen.findByRole("button", { name: "返回详情顶部" });
    expect(returnToTop).toBeInTheDocument();

    await user.click(returnToTop);
    expect(detailPanel.scrollTop).toBe(0);
    expect(screen.queryByRole("button", { name: "返回详情顶部" })).not.toBeInTheDocument();
  });

  it("resets inspector scroll when previewing a different plan", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findByRole("heading", { name: "方案一", level: 3 });

    const detailPanel = document.querySelector(".detail-panel") as HTMLElement;
    detailPanel.scrollTop = 180;
    fireEvent.scroll(detailPanel);
    expect(await screen.findByRole("button", { name: "返回详情顶部" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "预览方案二" }));
    await waitFor(() => expect(detailPanel.scrollTop).toBe(0));
    expect(screen.queryByRole("button", { name: "返回详情顶部" })).not.toBeInTheDocument();
  });

  it("shows the locked stop and replacement evidence for a modified plan", async () => {
    const user = userEvent.setup();
    const modifiedPlan = plan("modified-plan", "修改后方案");
    modifiedPlan.stops[1] = { ...modifiedPlan.stops[1], resource_id: "restaurant-kept", name: "保留餐厅" };
    api.sendMessage.mockResolvedValue({
      ...response,
      reply: "已保留餐厅并替换活动。",
      plans: [modifiedPlan],
      plan_version_id: "version-2",
      plan_diff: {
        from_plan_version_id: "version-1",
        to_plan_version_id: "version-2",
        base_plan_id: "old-plan",
        new_plan_id: "modified-plan",
        locked_stops: [{ source_plan_id: "old-plan", stop_index: 1, resource_id: "restaurant-kept", role: "meal" }],
        replacements: [{ stop_index: 0, role: "activity", before_resource_id: "activity-old", before_name: "旧展览", after_resource_id: "activity-new", after_name: "新展览" }],
        route_distance_delta_km: -2.4,
        duration_delta_minutes: -15,
        price_delta: 0,
      },
    });
    render(<App />);

    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "餐厅保留，只把活动换近一点");
    await user.click(screen.getByRole("button", { name: "发送需求" }));

    expect(await screen.findByText("替换结果")).toBeInTheDocument();
    const evidence = screen.getByRole("region", { name: "方案修改明细：修改后方案" });
    expect(evidence).toHaveAttribute("data-plan-id", "modified-plan");
    expect(evidence).toHaveAttribute("data-plan-version-id", "version-2");
    expect(within(evidence).getByText("第 1 站 · 活动")).toBeInTheDocument();
    expect(within(evidence).getByText("旧展览 → 新展览")).toBeInTheDocument();
    expect(within(evidence).getByText("替换 1 站")).toBeVisible();
    expect(within(evidence).queryByText("锁定站点")).not.toBeInTheDocument();
    expect(within(evidence).queryByText("第 2 站 · 餐饮 · 保留餐厅")).not.toBeInTheDocument();
    expect(evidence.querySelector(".plan-diff-metrics")).toHaveTextContent("全程距离−2.4 km");
    expect(evidence.querySelector(".plan-diff-metrics")).toHaveTextContent("总时长−15 分钟");
    expect(evidence.querySelector(".plan-diff-metrics")).toHaveTextContent("地点费用不变");
    expect(evidence).not.toHaveTextContent("提前 15 分钟");
    expect(document.querySelector(".legacy-plan-diff")).toBeNull();
  });

  it("binds multiple replacement diffs to their own candidate and renders all replacement rows safely", async () => {
    const user = userEvent.setup();
    const first = plan("replacement-a", "候选方案甲");
    const second = plan("replacement-b", "候选方案乙");
    api.sendMessage.mockResolvedValueOnce({
      ...response,
      plan_version_id: "version-2",
      conversation_command: { operation: "replace", base_plan_id: "plan-one", base_plan_version_id: "v1" },
      plans: [first, second],
      plan_diffs: [
        {
          from_plan_version_id: "v1", to_plan_version_id: "version-2", base_plan_id: "plan-one", new_plan_id: "replacement-a",
          locked_stops: [], replacements: [], route_distance_delta_km: 0, duration_delta_minutes: 0, price_delta: 0,
        },
        {
          from_plan_version_id: "v1", to_plan_version_id: "version-2", base_plan_id: "plan-one", new_plan_id: "replacement-b",
          locked_stops: [{ source_plan_id: "plan-one", stop_index: 0, resource_id: "plan-one-activity", role: "activity" }],
          replacements: [
            { stop_index: 0, role: "activity", before_resource_id: "plan-one-activity", before_name: "展览", after_resource_id: "new-activity", after_name: "新活动" },
            { stop_index: 1, role: "meal", before_resource_id: "plan-one-meal", before_name: "晚餐", after_resource_id: "new-meal", after_name: "新餐厅" },
          ], route_distance_delta_km: 1.2, duration_delta_minutes: 0, price_delta: -12,
        },
      ],
    });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "先生成，再替换");
    await user.click(screen.getByRole("button", { name: "发送需求" }));

    const firstCard = screen.getByRole("heading", { name: "候选方案甲", level: 3 }).closest(".plan-card") as HTMLElement;
    const secondCard = screen.getByRole("heading", { name: "候选方案乙", level: 3 }).closest(".plan-card") as HTMLElement;
    expect(within(firstCard).getByText("未返回站点替换明细")).toBeVisible();
    expect(firstCard.querySelector(".plan-diff-metrics")).toHaveTextContent("全程距离不变");
    expect(within(firstCard).queryByText("展览 → 新活动")).not.toBeInTheDocument();
    expect(within(secondCard).getByText("替换 2 站")).toBeVisible();
    expect(within(secondCard).getByText("第 1 站 · 活动")).toBeVisible();
    expect(within(secondCard).getByText("第 2 站 · 餐饮")).toBeVisible();
    expect(within(secondCard).getByText("展览 → 新活动")).toBeVisible();
    expect(within(secondCard).getByText("晚餐 → 新餐厅")).toBeVisible();
    expect(secondCard.querySelector(".plan-diff-metrics")).toHaveTextContent("全程距离+1.2 km");
    expect(secondCard.querySelector(".plan-diff-metrics")).toHaveTextContent("总时长不变");
    expect(secondCard.querySelector(".plan-diff-metrics")).toHaveTextContent("地点费用−¥12");
  });

  it("shows a clear missing-diff state for a replacement response without diff data", async () => {
    const user = userEvent.setup();
    api.sendMessage.mockResolvedValueOnce({
      ...response,
      plan_version_id: "version-2",
      conversation_command: { operation: "replace" },
      plans: [plan("candidate-without-diff", "缺少差异方案")],
      plan_diffs: [],
      plan_diff: null,
    });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "替换一个地点");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    expect(await screen.findByRole("note")).toHaveTextContent("服务端未返回此候选的变更明细");
  });

  it("does not show a PlanDiff whose target version differs from the response", async () => {
    const user = userEvent.setup();
    const candidate = plan("candidate-stale-diff", "差异版本不匹配");
    api.sendMessage.mockResolvedValueOnce({
      ...response,
      plan_version_id: "version-2",
      conversation_command: { operation: "replace", base_plan_id: "plan-one" },
      plans: [candidate],
      plan_diffs: [{
        from_plan_version_id: "version-1",
        to_plan_version_id: "version-1",
        base_plan_id: "plan-one",
        new_plan_id: candidate.plan_id,
        locked_stops: [],
        replacements: [{
          stop_index: 0,
          role: "activity",
          before_resource_id: "plan-one-activity",
          before_name: "旧活动",
          after_resource_id: "stale-activity",
          after_name: "错版本活动",
        }],
        route_distance_delta_km: -4.2,
        duration_delta_minutes: -20,
        price_delta: -10,
      }],
    });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "替换一个地点");
    await user.click(screen.getByRole("button", { name: "发送需求" }));

    const card = screen.getByRole("heading", { name: "差异版本不匹配", level: 3 }).closest(".plan-card") as HTMLElement;
    expect(within(card).getByRole("note")).toHaveTextContent("服务端未返回此候选的变更明细");
    expect(within(card).queryByText("旧活动 → 错版本活动")).not.toBeInTheDocument();
    expect(within(card).queryByText("−4.2 km")).not.toBeInTheDocument();
  });

  it("does not render a return node when route legs contain only inbound legs", async () => {
    const user = userEvent.setup();
    const noReturnPlan = plan("no-return", "无返程方案");
    noReturnPlan.route_legs = noReturnPlan.route_legs.slice(0, noReturnPlan.stops.length);
    api.sendMessage.mockResolvedValue({ ...response, plans: [noReturnPlan] });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));

    await screen.findByRole("heading", { name: "无返程方案", level: 3 });
    expect(screen.queryByText("返")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /查看返程/ })).not.toBeInTheDocument();
  });

  it("shows hard planning constraints separately from recommendation preferences", async () => {
    const user = userEvent.setup();
    api.sendMessage.mockResolvedValue({
      ...response,
      provider_facts: [{
        kind: "weather", source: "mock", mode: "mock", observed_at: "2026-10-20T10:00:00Z", verified_at: "2026-10-20T10:00:00Z", degraded: false, degraded_reason: null,
        city: "北京市", district: "朝阳区", date: "2026-10-21", condition: "阴转小雨", temperature_c: 19, precipitation_mm: 1, is_adverse: true, cache_age_seconds: null,
      }],
      constraint_summary: [{
        field: "departure_at", value: "14:30", source: "user_explicit", evidence: "下午两点半准时出发", confidence: 1, rule_id: "departure_at.clock.zh_cn.v1", user_editable: true,
      }, {
        field: "party", value: { adults: 1, children: 0 }, source: "user_explicit", evidence: "一位成人", confidence: 1, rule_id: "party.count.v1", user_editable: true,
      }, {
        field: "budget_per_person", value: 120, source: "user_explicit", evidence: "人均120元", confidence: 1, rule_id: "budget.amount.v1", user_editable: true,
      }, {
        field: "max_distance_km", value: 8, source: "user_explicit", evidence: "8公里内", confidence: 1, rule_id: "distance.radius.v1", user_editable: true,
      }],
    });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "明天下午两点半准时出发");
    await user.click(screen.getByRole("button", { name: "发送需求" }));

    const planningReply = document.querySelector(".rich-planning-reply");
    expect(planningReply).toBeInTheDocument();
    expect(planningReply).not.toHaveTextContent("找到喜欢的节奏");
    const constraints = within(planningReply as HTMLElement).getByRole("group", { name: "本轮规划参考的硬约束" });
    expect(within(constraints).getByText("准时出发")).toBeInTheDocument();
    expect(within(constraints).getByText("14:30")).toBeInTheDocument();
    expect(within(constraints).getByText("同行人")).toBeInTheDocument();
    expect(within(constraints).getByText("人均 120 元")).toBeInTheDocument();
    expect(within(constraints).getByText("距离")).toBeInTheDocument();
    expect(constraints.querySelector(".planning-constraint-tag.weather")).toHaveTextContent("天气 模拟数据 · 非实时 · 阴转小雨 · 19°C · 需留意");
    expect(planningReply).not.toHaveTextContent("下午两点半准时出发");
    expect(planningReply?.querySelector(".plan-grid")).toBeInTheDocument();
  });

  it.each([
    { mode: "mock" as const, source: "cache" as const, verified: true, expected: "模拟数据 · 非实时" },
    { mode: "replay" as const, source: "replay" as const, verified: true, expected: "固定回放" },
    { mode: "live" as const, source: "amap_live" as const, verified: false, expected: "高德实时 · 待确认" },
  ])("shows $mode availability provenance before evidence is expanded", async ({ mode, source, verified, expected }) => {
    const user = userEvent.setup();
    api.sendMessage.mockResolvedValueOnce({
      ...response,
      provider_facts: [{
        kind: "availability", source, mode, verified, stale: false, status: "available", reason: null,
        resource_id: "plan-one-activity", observed_at: "2026-10-24T10:00:00Z", verified_at: "2026-10-24T10:00:00Z",
        degraded: false, degraded_reason: null,
      }],
    });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");
    await user.click(screen.getByRole("tab", { name: "核验与来源" }));
    const overview = screen.getByRole("region", { name: "核验概览" });
    expect(within(overview).getByText(expected)).toBeVisible();
    expect(overview).not.toHaveTextContent("已提供动态事实");
  });

  it("explains an empty condition-save reply instead of showing an empty assistant turn", async () => {
    const user = userEvent.setup();
    api.sendMessage.mockResolvedValueOnce({ ...response, status: "context_saved", reply: "", plans: [], planning_context: planningContext({ plan_stale: true }) });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "保存新条件");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    expect(await screen.findByText("条件已保存，现有方案需要重新核验；确认重新规划后才会更新行程。")).toBeVisible();
  });

  it("confirms a saved stale context with a dismissible toast", async () => {
    const user = userEvent.setup();
    const initialContext = planningContext();
    const staleContext = planningContext({
      request_revision: 2,
      committed_request_revision: 2,
      plan_stale: true,
      planned_request_revision: 1,
    });
    api.sendMessage.mockResolvedValueOnce({ ...response, planning_context: initialContext });
    api.updatePlanningContext.mockResolvedValueOnce({
      ...response,
      status: "context_saved",
      plans: [],
      planning_context: staleContext,
    });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findByRole("heading", { name: "方案一", level: 3 });
    await user.click(screen.getByRole("button", { name: /修改日期时间/ }));
    fireEvent.change(screen.getByLabelText("日期", { exact: true }), { target: { value: "2026-10-20" } });
    await user.click(screen.getByRole("button", { name: "保存条件" }));

    const toastMessage = await screen.findByText("条件已保存。当前预览仍是旧版本，建议重新规划。");
    expect(toastMessage.closest(".app-toast")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "关闭通知" }));
    expect(screen.queryByText("条件已保存。当前预览仍是旧版本，建议重新规划。")).not.toBeInTheDocument();
  });

  it("renders a dinner-only plan with one place, inbound route, and return route", async () => {
    const user = userEvent.setup();
    const dinnerOnly = plan("dinner-only", "晚餐 A");
    dinnerOnly.skeleton_id = "dinner-only-v1";
    dinnerOnly.stops = [{
      resource_id: "dinner-only-dinner", type: "restaurant", role: "dinner", name: "晚餐 A", start: "18:20", end: "19:20", duration_minutes: 60, price: 120, price_kind: "known", category_tags: ["餐厅"], image: null, source,
    }];
    dinnerOnly.route_legs = [
      { origin_name: "出发地", destination_name: "晚餐 A", start: "18:00", end: "18:20", mode: "taxi", distance_km: 2, duration_minutes: 20, source: "replay", provider_mode: "replay", verified_at: null, cache_age_seconds: null, geometry: [], degraded: false, degraded_reason: null },
      { origin_name: "晚餐 A", destination_name: "出发地", start: "19:20", end: "19:40", mode: "taxi", distance_km: 2, duration_minutes: 20, source: "replay", provider_mode: "replay", verified_at: null, cache_age_seconds: null, geometry: [], degraded: false, degraded_reason: null },
    ];
    api.sendMessage.mockResolvedValue({
      ...response,
      plans: [dinnerOnly],
      constraint_summary: [
        { field: "exact_stop_count", value: 1, source: "user_explicit", evidence: "只安排一家", confidence: 1, rule_id: "plan_structure.exact_stop_count.v1", user_editable: true },
        { field: "required_stop_roles", value: ["dinner"], source: "user_explicit", evidence: "晚饭", confidence: 1, rule_id: "plan_structure.required_roles.v1", user_editable: true },
      ],
    });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "只安排一家晚饭");
    await user.click(screen.getByRole("button", { name: "发送需求" }));

    expect(await screen.findByRole("heading", { name: "晚餐 A", level: 3 })).toBeInTheDocument();
    const constraintGroup = screen.getByRole("group", { name: "本轮规划参考的硬约束" });
    expect(within(constraintGroup).getByText("站点数")).toBeInTheDocument();
    expect(within(constraintGroup).getByText("1 站")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /查看返程/ })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /查看下一程/ })).not.toBeInTheDocument();
    expect(screen.queryByText("展览")).not.toBeInTheDocument();
  });

  it("shows a recoverable error when a provider request fails", async () => {
    const user = userEvent.setup();
    api.listSessions.mockRejectedValue(new Error("历史接口失败"));
    render(<App />);

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("历史接口失败"));
    await user.click(screen.getByRole("button", { name: "新建规划" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders only the returned dynamic warning in the existing plan workspace", async () => {
    const user = userEvent.setup();
    api.sendMessage.mockResolvedValue({
      ...response,
      warnings: [{
        code: "availability_unconfirmed",
        message: "晚餐的动态可用性尚未得到完整确认。",
        plan_id: "plan-one",
        resource_id: "plan-one-meal",
        route_leg_index: null,
        source: "mock",
        degraded: false,
        stale: false,
      }],
    });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));

    expect(await screen.findByRole("note", { name: "方案待确认信息" })).toHaveTextContent("动态可用性尚未得到完整确认");
  });

  it("renders presentation details from the separate resource-id keyed payload", async () => {
    const user = userEvent.setup();
    api.sendMessage.mockResolvedValue({
      ...response,
      poi_presentations: response.plans.flatMap((item) => item.stops.map((stop) => presentation(stop.resource_id, stop.name))),
    });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));

    await user.click(screen.getByRole("button", { name: "查看第 1 站 展览 详情" }));
    expect(await screen.findByText(/测试路 1 号/)).toBeInTheDocument();
    expect(screen.queryByText(/演示评分|演示评论/)).not.toBeInTheDocument();
    expect(document.querySelector(".plan-grid")).not.toHaveTextContent("POI 商业信息为模拟数据");
    expect(document.querySelector(".plan-results-notes")).toHaveTextContent("POI 商业信息为模拟数据");
    await user.click(screen.getByText("数据与来源说明"));
    expect(document.querySelector(".poi-data-disclosure p")).toBeVisible();
    expect(document.querySelector(".poi-data-disclosure p")).toHaveTextContent("POI 商业信息为模拟数据");
  });

  it("keeps every rich planning reply inside its original assistant turn", async () => {
    const user = userEvent.setup();
    const firstResponse = {
      ...response,
      reply: "第一轮方案已经整理好。",
      plans: [plan("first-plan", "第一轮方案")],
    };
    const secondResponse = {
      ...response,
      reply: "第二轮方案已经整理好。",
      plans: [plan("second-plan", "第二轮方案")],
    };
    api.sendMessage
      .mockResolvedValueOnce(firstResponse)
      .mockResolvedValueOnce(secondResponse);

    const { container } = render(<App />);
    const input = screen.getByLabelText("描述你的空闲时间和偏好");
    await user.type(input, "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findByRole("heading", { name: "第一轮方案", level: 3 });

    await user.type(input, "预算再低一点");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findByRole("heading", { name: "第二轮方案", level: 3 });

    const assistantTurns = [...container.querySelectorAll(".message.assistant")];
    expect(assistantTurns).toHaveLength(2);
    expect(assistantTurns[0]).toHaveTextContent("第一轮方案");
    expect(assistantTurns[0].querySelector(".message-content > p")).toBeNull();
    expect(assistantTurns[0].closest(".message-entry")?.previousElementSibling).toHaveClass("message-entry");
    expect(assistantTurns[0].closest(".message-entry")?.querySelector(".message-divider")).toBeInTheDocument();
    expect(assistantTurns[0].querySelector(".assistant-speaker")).toHaveTextContent("管家");
    expect(assistantTurns[0]).not.toHaveTextContent("第二轮方案");
    expect(assistantTurns[0].querySelector(".rich-planning-reply")).not.toBeNull();
    expect(assistantTurns[1]).toHaveTextContent("第二轮方案");
    expect(assistantTurns[1].querySelector(".message-content > p")).toBeNull();
    expect(assistantTurns[1].closest(".message-entry")?.previousElementSibling).toHaveClass("message-entry");
    expect(assistantTurns[1].closest(".message-entry")?.querySelector(".message-divider")).toBeInTheDocument();
    expect(assistantTurns[1].querySelector(".assistant-speaker")).toHaveTextContent("管家");
    expect(assistantTurns[1]).not.toHaveTextContent("第一轮方案");
    expect(assistantTurns[1].querySelector(".rich-planning-reply")).not.toBeNull();
    expect(container.querySelectorAll(".message-divider")).toHaveLength(3);
    expect(container.querySelectorAll(".message.assistant > .butler-avatar")).toHaveLength(0);
  });

  it("restores all persisted rich replies instead of only the latest one", async () => {
    const firstResponse = {
      ...response,
      reply: "第一轮方案已经整理好。",
      plans: [plan("restored-first", "恢复的第一轮方案")],
    };
    const secondResponse = {
      ...response,
      reply: "第二轮方案已经整理好。",
      plans: [plan("restored-second", "恢复的第二轮方案")],
    };
    api.listSessions.mockResolvedValue([{
      session_id: "history-session",
      title: "多轮历史会话",
      status: "completed",
      created_at: "2026-08-20T00:00:00Z",
      updated_at: "2026-08-20T02:00:00Z",
      last_message_preview: "第二轮方案已经整理好。",
    }]);
    api.getSession.mockResolvedValue({
      session_id: "history-session",
      title: "多轮历史会话",
      status: "completed",
      created_at: "2026-08-20T00:00:00Z",
      updated_at: "2026-08-20T02:00:00Z",
      messages: [
        { id: "user-1", role: "user", content: "今天下午出去玩" },
        { id: "assistant-1", role: "assistant", content: firstResponse.reply },
        { id: "user-2", role: "user", content: "预算再低一点" },
        { id: "assistant-2", role: "assistant", content: secondResponse.reply },
      ],
      plans: secondResponse.plans,
      latest_response: secondResponse,
      response_history: [firstResponse, secondResponse],
    });

    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /多轮历史会话/ }));

    expect(await screen.findByRole("heading", { name: "恢复的第一轮方案", level: 3 })).toBeVisible();
    expect(screen.getByRole("heading", { name: "恢复的第二轮方案", level: 3 })).toBeVisible();
  });

  it("restores a legacy session response that predates POI presentations", async () => {
    const user = userEvent.setup();
    const legacyResponse = { ...response } as Partial<AgentResponse>;
    delete legacyResponse.poi_presentations;
    api.listSessions.mockResolvedValue([{
      session_id: "legacy-session",
      title: "旧版历史会话",
      status: "completed",
      created_at: "2026-08-20T00:00:00Z",
      updated_at: "2026-08-20T01:00:00Z",
      last_message_preview: "已生成方案",
    }]);
    api.getSession.mockResolvedValue({
      session_id: "legacy-session",
      title: "旧版历史会话",
      status: "completed",
      created_at: "2026-08-20T00:00:00Z",
      updated_at: "2026-08-20T01:00:00Z",
      messages: [{ id: "legacy-message", role: "user", content: "今天下午出去玩" }],
      plans: response.plans,
      latest_response: legacyResponse as AgentResponse,
    });

    render(<App />);
    await user.click(await screen.findByRole("button", { name: /旧版历史会话/ }));

    expect(await screen.findByRole("heading", { name: "方案一", level: 3 })).toBeVisible();
    expect(screen.getByText("今天下午出去玩")).toBeInTheDocument();
  });

  it("defaults to viewing the first plan without marking it selected", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");

    expect(screen.getByRole("button", { name: "预览方案一" })).toHaveAttribute("aria-pressed", "true");
    const card = screen.getByRole("heading", { name: "方案一", level: 3 }).closest(".plan-card") as HTMLElement;
    const cardFooter = card.querySelector(".plan-card-footer");
    expect(cardFooter?.querySelector(".plan-metrics")).not.toBeNull();
    expect(within(cardFooter as HTMLElement).getByRole("button", { name: "打开方案一详情" })).toHaveTextContent("查看行程详情");
    expect(screen.queryByRole("button", { name: "已选择此方案" })).not.toBeInTheDocument();
    expect(document.querySelector(".inspector .recommendation-summary")).toBeNull();
    expect(document.querySelector(".trip-section-title > div > span")).toBeNull();
  });

  it("uses the adviser recommendation separately from each plan's strategy", async () => {
    const user = userEvent.setup();
    api.sendMessage.mockResolvedValueOnce({
      ...response,
      plans: [
        { ...plan("plan-one", "方案一"), strategy: "experience", total_score: 100 },
        { ...plan("plan-two", "方案二"), strategy: "low_cost", total_score: 1 },
      ],
      recommendation_advice: {
        recommended_plan_id: "plan-two",
        understood_needs: [],
        overall_reason: "方案二更符合你的预算。",
        plans: [
          { plan_id: "plan-one", reason: "体验更丰富。", matched_need_ids: [], supporting_evidence_ids: [], tradeoffs: [] },
          { plan_id: "plan-two", reason: "费用更符合预算。", matched_need_ids: [], supporting_evidence_ids: [], tradeoffs: [] },
        ],
      },
    });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "安排一个周末行程");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findByRole("heading", { name: "管家首选：方案二" });

    expect(screen.getByText("方案 01 · 体验优先")).toBeInTheDocument();
    expect(screen.getByText("方案 02 · 预算友好")).toBeInTheDocument();
    expect(document.querySelectorAll(".recommended-badge")).toHaveLength(1);
    expect(document.querySelector(".recommended-badge")).toHaveTextContent("管家首选");
    expect(screen.getByText("方案特点与取舍")).toBeInTheDocument();
  });

  it("does not jump to the newest response when the user has scrolled up", async () => {
    let resolveResponse!: (value: AgentResponse) => void;
    api.sendMessageStream.mockImplementationOnce(() => new Promise<AgentResponse>((resolve) => {
      resolveResponse = resolve;
    }));
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findByText("今天下午出去玩");

    const conversation = document.querySelector(".conversation") as HTMLElement;
    Object.defineProperties(conversation, {
      scrollHeight: { configurable: true, value: 1600 },
      clientHeight: { configurable: true, value: 500 },
    });
    conversation.scrollTop = 0;
    fireEvent.scroll(conversation);
    const endAnchor = conversation.querySelector(".message-list > div[aria-hidden='true']") as HTMLDivElement;
    const scrollToEnd = vi.fn();
    Object.defineProperty(endAnchor, "scrollIntoView", { configurable: true, value: scrollToEnd });

    await act(async () => { resolveResponse(response); });
    await screen.findByRole("heading", { name: "方案一", level: 3 });
    expect(scrollToEnd).not.toHaveBeenCalled();
  });

  it("marks a plan selected only after the backend confirms", async () => {
    const user = userEvent.setup();
    api.selectPlan.mockResolvedValue({ active_plan_version_id: "v1", selected_plan_id: "plan-two" });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");

    await user.click(screen.getByRole("button", { name: "预览方案二" }));
    await user.click(screen.getByRole("button", { name: "选择此方案" }));

    expect(await screen.findByRole("button", { name: "已选择此方案" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "预览方案二" })).toHaveAttribute("aria-pressed", "true");
    expect(document.querySelector('.plan-card.selected')).toHaveTextContent("已选");
    expect(api.selectPlan).toHaveBeenCalledWith("session-test", "plan-two");
  });

  it("keeps A selected while previewing B and requires confirmation before switching", async () => {
    const user = userEvent.setup();
    api.selectPlan
      .mockResolvedValueOnce({ active_plan_version_id: "v1", selected_plan_id: "plan-one" })
      .mockResolvedValueOnce({ active_plan_version_id: "v1", selected_plan_id: "plan-two" });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");
    await user.click(screen.getByRole("button", { name: "选择此方案" }));
    await screen.findByRole("button", { name: "已选择此方案" });

    await user.click(screen.getByRole("button", { name: "预览方案二" }));
    expect(document.querySelector(".plan-card.selected")).toHaveTextContent("方案一");
    expect(document.querySelector(".inspector-plan-header")).toHaveTextContent("当前预览");
    await user.click(screen.getByRole("button", { name: "返回已选方案：方案一" }));
    expect(screen.getByRole("button", { name: "预览方案一" })).toHaveAttribute("aria-pressed", "true");
    expect(document.querySelector(".inspector-plan-header")).toHaveTextContent("已正式选择");
    await user.click(screen.getByRole("button", { name: "预览方案二" }));
    const switchButton = screen.getByRole("button", { name: "选择此方案" });
    await user.click(switchButton);
    expect(await screen.findByRole("dialog", { name: "切换正式选择？" })).toHaveTextContent("方案一");
    expect(screen.getByRole("dialog", { name: "切换正式选择？" })).toHaveTextContent("方案二");
    expect(api.selectPlan).toHaveBeenCalledTimes(1);

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "切换正式选择？" })).not.toBeInTheDocument());
    expect(switchButton).toHaveFocus();
    expect(api.selectPlan).toHaveBeenCalledTimes(1);

    await user.click(switchButton);
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(api.selectPlan).toHaveBeenCalledTimes(1);
    expect(document.querySelector(".plan-card.selected")).toHaveTextContent("方案一");

    await user.click(switchButton);
    await user.click(screen.getByRole("button", { name: "确认切换" }));
    await waitFor(() => expect(api.selectPlan).toHaveBeenCalledTimes(2));
    expect(api.selectPlan).toHaveBeenLastCalledWith("session-test", "plan-two");
    expect(await screen.findByRole("button", { name: "已选择此方案" })).toBeDisabled();
    expect(document.querySelector(".plan-card.selected")).toHaveTextContent("方案二");
  });

  it("disables repeat selection while the backend request is pending", async () => {
    const user = userEvent.setup();
    let resolveSelection: ((value: { active_plan_version_id: string; selected_plan_id: string }) => void) | undefined;
    api.selectPlan.mockImplementation(() => new Promise((resolve) => { resolveSelection = resolve; }));
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");

    await user.click(screen.getByRole("button", { name: "选择此方案" }));
    const pendingButton = await screen.findByRole("button", { name: "正在保存…" });
    expect(pendingButton).toBeDisabled();
    fireEvent.click(pendingButton);
    expect(api.selectPlan).toHaveBeenCalledTimes(1);
    resolveSelection?.({ active_plan_version_id: "v1", selected_plan_id: "plan-one" });
    expect(await screen.findByRole("button", { name: "已选择此方案" })).toBeDisabled();
  });

  it("blocks formal choice for stale or draft conditions and pending recovery", async () => {
    const user = userEvent.setup();
    const recovery = {
      kind: "recovery_choice",
      interaction_id: "recovery-pending-selection",
      request_revision: 3,
      plan_version_id: "v1",
      reason: {
        code: "NO_CANDIDATES_WITHIN_SEARCH_RADIUS",
        kind: "no_feasible_plan",
        stage: "retrieval",
        fields: ["max_distance_km"],
        request_revision: 3,
        plan_version_id: "v1",
        public_summary: "当前条件下暂未找到可行方案。",
        diagnostics: {},
      },
      actions: [],
    } satisfies NonNullable<AgentResponse["recovery"]>;
    api.sendMessage
      .mockResolvedValueOnce({ ...response, planning_context: planningContext({ plan_stale: true }) })
      .mockResolvedValueOnce({ ...response, planning_context: planningContext({ request_view: "draft" }) })
      .mockResolvedValueOnce({ ...response, status: "needs_recovery", recovery, planning_context: planningContext() });
    render(<App />);
    const input = screen.getByLabelText("描述你的空闲时间和偏好");
    const send = screen.getByRole("button", { name: "发送需求" });

    await user.type(input, "第一轮方案");
    await user.click(send);
    const selectionButton = await screen.findByRole("button", { name: "选择此方案" });
    expect(selectionButton).toBeDisabled();
    expect(screen.getByText("规划条件已变化；请先按新条件重新规划。")).toBeInTheDocument();
    fireEvent.click(selectionButton);

    await user.type(input, "未提交的条件草稿");
    await user.click(send);
    expect(await screen.findByText("规划条件有未提交草稿；提交或取消草稿后再选择。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "选择此方案" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "选择此方案" }));

    await user.type(input, "等待处理恢复操作");
    await user.click(send);
    expect(await screen.findByText("有待处理的恢复操作，请先完成或取消。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "选择此方案" })).toBeDisabled();
    expect(api.selectPlan).not.toHaveBeenCalled();
  });

  it("invalidates a pending switch confirmation when browser navigation changes sessions", async () => {
    const user = userEvent.setup();
    api.listSessions.mockResolvedValue([{
      session_id: "other-session", title: "另一个规划", status: "completed",
      created_at: "2026-08-20T00:00:00Z", updated_at: "2026-08-20T02:00:00Z", last_message_preview: "另一个版本",
    }]);
    const otherResponse = { ...response, plans: [plan("other-plan", "另一个版本")], plan_version_id: "v2" };
    api.getSession.mockResolvedValue({
      session_id: "other-session", title: "另一个规划", status: "completed",
      created_at: "2026-08-20T00:00:00Z", updated_at: "2026-08-20T02:00:00Z",
      messages: [{ id: "other-assistant", role: "assistant", content: otherResponse.reply }],
      plans: otherResponse.plans, latest_response: otherResponse, response_history: [otherResponse],
      active_plan_version_id: "v2", selected_plan_id: null,
    });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");
    await user.click(screen.getByRole("button", { name: "选择此方案" }));
    await screen.findByRole("button", { name: "已选择此方案" });
    await user.click(screen.getByRole("button", { name: "预览方案二" }));
    await user.click(screen.getByRole("button", { name: "选择此方案" }));
    expect(await screen.findByRole("dialog", { name: "切换正式选择？" })).toBeInTheDocument();
    expect(api.selectPlan).toHaveBeenCalledTimes(1);

    window.history.replaceState({}, "", "/?session=other-session");
    fireEvent.popState(window);
    await screen.findByRole("heading", { name: "另一个版本", level: 3 });
    expect(screen.queryByRole("dialog", { name: "切换正式选择？" })).not.toBeInTheDocument();
    expect(api.selectPlan).toHaveBeenCalledTimes(1);
  });

  it("keeps an older response previewable but read-only when its version differs", async () => {
    const user = userEvent.setup();
    const oldResponse = { ...response, plans: [plan("same-plan-id", "旧版本方案")], plan_version_id: "v1" };
    const currentPlan = plan("same-plan-id", "当前版本方案");
    api.listSessions.mockResolvedValue([{
      session_id: "versioned-history", title: "有历史版本的会话", status: "completed",
      created_at: "2026-08-20T00:00:00Z", updated_at: "2026-08-20T02:00:00Z", last_message_preview: "旧版本方案",
    }]);
    api.getSession.mockResolvedValue({
      session_id: "versioned-history", title: "有历史版本的会话", status: "completed",
      created_at: "2026-08-20T00:00:00Z", updated_at: "2026-08-20T02:00:00Z",
      messages: [{ id: "old-assistant", role: "assistant", content: oldResponse.reply }],
      plans: [currentPlan], latest_response: oldResponse, response_history: [oldResponse],
      active_plan_version_id: "v2", selected_plan_id: "same-plan-id",
    });
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /有历史版本的会话/ }));
    await user.click(await screen.findByRole("button", { name: "预览旧版本方案" }));

    expect(document.querySelector(".plan-card.selected")).toBeNull();
    expect(document.querySelector(".inspector-plan-header")).toHaveTextContent("历史预览 · 只读");
    expect(screen.getByRole("button", { name: "选择此方案" })).toBeDisabled();
    expect(screen.getByText("此方案来自历史版本，仅可预览。")).toBeInTheDocument();
    const disabledReplaceButton = within(document.querySelector(".detail-panel") as HTMLElement).getAllByRole("button", { name: "替换此站" })[0];
    expect(disabledReplaceButton).toBeDisabled();
    expect(disabledReplaceButton.parentElement).toHaveAttribute("title", "先正式选择当前方案，才能修改单站");
    await user.click(screen.getByRole("button", { name: "返回已选方案：当前版本方案" }));
    expect(document.querySelector(".inspector-plan-header")).toHaveTextContent("已正式选择");
    expect(document.querySelector(".inspector-plan-header h2")).toHaveTextContent("当前版本方案");
    expect(api.selectPlan).not.toHaveBeenCalled();
  });

  it("does not infer a historical candidate version from a matching plan id", async () => {
    const user = userEvent.setup();
    const unanchoredResponse = { ...response, plans: [plan("same-plan-id", "同名历史方案")], plan_version_id: null };
    const activePlan = plan("same-plan-id", "当前活动方案");
    api.listSessions.mockResolvedValue([{
      session_id: "legacy-version-history", title: "缺少版本锚点的历史会话", status: "completed",
      created_at: "2026-08-20T00:00:00Z", updated_at: "2026-08-20T02:00:00Z", last_message_preview: "同名历史方案",
    }]);
    api.getSession.mockResolvedValue({
      session_id: "legacy-version-history", title: "缺少版本锚点的历史会话", status: "completed",
      created_at: "2026-08-20T00:00:00Z", updated_at: "2026-08-20T02:00:00Z",
      messages: [{ id: "unanchored-assistant", role: "assistant", content: unanchoredResponse.reply }],
      plans: [activePlan], latest_response: unanchoredResponse, response_history: [unanchoredResponse],
      active_plan_version_id: "v2", selected_plan_id: null,
    });
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /缺少版本锚点的历史会话/ }));
    await user.click(await screen.findByRole("button", { name: "预览同名历史方案" }));

    expect(document.querySelector(".inspector-plan-header")).toHaveTextContent("历史预览 · 只读");
    expect(screen.getByText("缺少方案版本归属信息；此方案仅可预览。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "选择此方案" })).toBeDisabled();
    expect(api.selectPlan).not.toHaveBeenCalled();
  });

  it("opens a POI subpage and returns to its original tab and keyboard entry point", async () => {
    const user = userEvent.setup();
    api.sendMessage.mockResolvedValueOnce({ ...response, poi_presentations: [presentation("plan-one-activity", "展览")] });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");

    const trigger = screen.getByRole("button", { name: "查看第 1 站 展览 详情" });
    await user.click(trigger);
    expect(await screen.findByRole("heading", { name: "展览", level: 2 })).toBeVisible();
    expect(screen.queryByRole("tablist", { name: "方案工作区" })).not.toBeInTheDocument();
    expect(screen.queryByText(/演示评分|演示评论/)).not.toBeInTheDocument();
    expect(screen.getByText("当前方案站点费用").parentElement).toHaveTextContent("¥30");
    expect(screen.getByText("展示参考人均").parentElement).toHaveTextContent("¥30（展示参考）");
    expect(screen.getByText("场景示意 · 非门店实拍")).toBeVisible();
    const mapLink = screen.getByRole("link", { name: "在高德地图中搜索" });
    expect(mapLink).toHaveAttribute("href", expect.stringContaining(encodeURIComponent("展览 测试路 1 号")));
    expect(mapLink).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByRole("button", { name: "替换此站" })).toBeDisabled();
    await user.click(screen.getByText("数据与来源说明"));
    expect(document.querySelector(".poi-data-disclosure p")).toBeVisible();
    expect(document.querySelector(".poi-data-disclosure p")).toHaveTextContent("POI 商业信息为模拟数据");
    await user.click(screen.getByRole("button", { name: "返回行程" }));
    const restoredTrigger = screen.getByRole("button", { name: "查看第 1 站 展览 详情" });
    await waitFor(() => expect(restoredTrigger).toHaveFocus());
    expect(screen.getByRole("tab", { name: "行程" })).toHaveAttribute("aria-selected", "true");
  });

  it("shows the Stop fallback when the current response has no POI presentation", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");
    await user.click(screen.getByRole("button", { name: "查看第 1 站 展览 详情" }));

    expect(await screen.findByText(/当前预览响应未附 POI 展示快照/)).toBeVisible();
    expect(screen.getByRole("heading", { name: "展览", level: 2 })).toBeInTheDocument();
    expect(screen.queryByText(/演示评分|演示评论/)).not.toBeInTheDocument();
    expect(screen.getByText("当前方案站点费用").parentElement).toHaveTextContent("¥30");
  });

  it("clears POI details when the preview plan changes", async () => {
    const user = userEvent.setup();
    api.sendMessage.mockResolvedValueOnce({ ...response, poi_presentations: [presentation("plan-one-activity", "展览")] });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");
    await user.click(screen.getByRole("button", { name: "查看第 1 站 展览 详情" }));
    expect(await screen.findByRole("heading", { name: "展览", level: 2 })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "预览方案二" }));
    expect(await screen.findByRole("heading", { name: "方案二", level: 2 })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "展览", level: 2 })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "行程" })).toHaveAttribute("aria-selected", "true");
  });

  it("sends an anchored structured command for one selected stop", async () => {
    const user = userEvent.setup();
    api.sendMessage.mockResolvedValueOnce({ ...response, plan_version_id: "v1" }).mockResolvedValueOnce({
      ...response,
      plan_version_id: "v2",
      plans: [plan("replacement-plan", "替换后方案")],
      plan_diffs: [{
        from_plan_version_id: "v1",
        to_plan_version_id: "v2",
        base_plan_id: "plan-one",
        new_plan_id: "replacement-plan",
        locked_stops: [{ source_plan_id: "plan-one", stop_index: 1, resource_id: "plan-one-meal", role: "meal" }],
        replacements: [{ stop_index: 0, role: "activity", before_resource_id: "plan-one-activity", before_name: "展览", after_resource_id: "new-activity", after_name: "新展览" }],
        route_distance_delta_km: -1,
        duration_delta_minutes: 0,
        price_delta: 0,
      }],
    });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");
    await user.click(screen.getByRole("button", { name: "选择此方案" }));

    const replaceButton = screen.getAllByRole("button", { name: "替换此站" })[0];
    expect(replaceButton).not.toBeDisabled();
    await user.click(replaceButton);
    expect(screen.getByRole("region", { name: "单站替换草稿" })).toHaveTextContent("第 1 站");
    expect(screen.getByRole("region", { name: "单站替换草稿" })).toHaveTextContent("快捷条件会立即提交");
    expect(screen.getByRole("region", { name: "单站替换草稿" })).toHaveTextContent("下方输入自定义偏好后点击发送");
    expect(screen.getByPlaceholderText("例如：想吃少辣的，环境安静一点（可选）")).toBeInTheDocument();
    await user.type(screen.getByLabelText("描述替换偏好（可选）"), "安静");
    await user.click(screen.getByRole("button", { name: "全程更近" }));

    const command = api.sendMessage.mock.calls.at(-1)?.[3];
    expect(command).toMatchObject({
      operation: "replace",
      base_plan_version_id: "v1",
      base_plan_id: "plan-one",
      target: { stop_index: 0, resource_id: "plan-one-activity" },
      replacement_criteria: [
        { kind: "route_objective", metric: "total_route_distance", direction: "decrease", strength: "required" },
        { kind: "semantic", text: "安静", strength: "preferred" },
      ],
    });
  });

  it("submits a replacement immediately when a preset is chosen", async () => {
    const user = userEvent.setup();
    api.sendMessage.mockResolvedValueOnce({ ...response, plan_version_id: "v1" }).mockResolvedValueOnce({
      ...response,
      plan_version_id: "v2",
      plans: [plan("replacement-plan", "替换后方案")],
      plan_diffs: [{
        from_plan_version_id: "v1",
        to_plan_version_id: "v2",
        base_plan_id: "plan-one",
        new_plan_id: "replacement-plan",
        locked_stops: [{ source_plan_id: "plan-one", stop_index: 1, resource_id: "plan-one-meal", role: "meal" }],
        replacements: [{ stop_index: 0, role: "activity", before_resource_id: "plan-one-activity", before_name: "展览", after_resource_id: "new-activity", after_name: "新展览" }],
        route_distance_delta_km: -1,
        duration_delta_minutes: 0,
        price_delta: 0,
      }],
    });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");
    await user.click(screen.getByRole("button", { name: "选择此方案" }));

    await user.click(screen.getAllByRole("button", { name: "替换此站" })[0]);

    await user.click(screen.getByRole("button", { name: "全程更近" }));

    await waitFor(() => expect(api.sendMessage.mock.calls.length).toBe(2));
    const command = api.sendMessage.mock.calls.at(-1)?.[3];
    expect(command).toMatchObject({
      operation: "replace",
      base_plan_version_id: "v1",
      base_plan_id: "plan-one",
      target: { stop_index: 0, resource_id: "plan-one-activity" },
      replacement_criteria: [
        { kind: "route_objective", metric: "total_route_distance", direction: "decrease", strength: "required" },
      ],
    });
  });

  it("cancels a replacement draft without a request or selection change", async () => {
    const user = userEvent.setup();
    api.sendMessage.mockResolvedValueOnce({ ...response, plan_version_id: "v1" });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");
    await user.click(screen.getByRole("button", { name: "选择此方案" }));
    await user.click(screen.getAllByRole("button", { name: "替换此站" })[0]);
    expect(screen.getByRole("region", { name: "单站替换草稿" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "取消修改" }));

    expect(screen.queryByRole("region", { name: "单站替换草稿" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "已选择此方案" })).toBeDisabled();
    expect(api.sendMessageStream).toHaveBeenCalledTimes(1);
    expect(api.sendMessage).toHaveBeenCalledTimes(1);
  });

  it("invalidates a replacement draft when the inspected candidate changes before submit", async () => {
    const user = userEvent.setup();
    api.sendMessage.mockResolvedValueOnce({ ...response, plan_version_id: "v1" });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");
    await user.click(screen.getByRole("button", { name: "选择此方案" }));
    await user.click(screen.getAllByRole("button", { name: "替换此站" })[0]);
    await user.click(screen.getByRole("button", { name: "预览方案二" }));

    expect(await screen.findByRole("status")).toHaveTextContent("原方案或规划条件已变化");
    expect(screen.queryByRole("region", { name: "单站替换草稿" })).not.toBeInTheDocument();
    expect(api.sendMessageStream).toHaveBeenCalledTimes(1);
  });

  it("prevents a rapid double click from submitting a replacement twice", async () => {
    const user = userEvent.setup();
    api.sendMessage.mockResolvedValueOnce({ ...response, plan_version_id: "v1" });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");
    await user.click(screen.getByRole("button", { name: "选择此方案" }));
    await user.click(screen.getAllByRole("button", { name: "替换此站" })[0]);
    let resolveReplacement: ((value: AgentResponse) => void) | undefined;
    api.sendMessageStream.mockImplementationOnce(() => new Promise((resolve) => { resolveReplacement = resolve; }));

    await user.dblClick(screen.getByRole("button", { name: "全程更近" }));
    await waitFor(() => expect(api.sendMessageStream).toHaveBeenCalledTimes(2));
    expect(api.sendMessage).toHaveBeenCalledTimes(1);
    resolveReplacement?.({ ...response, plan_version_id: "v2", plans: [plan("candidate", "候选方案")] });
    await screen.findByRole("heading", { name: "候选方案", level: 3 });
  });

  it("does not show a fake selection when the backend rejects it", async () => {
    const user = userEvent.setup();
    api.selectPlan.mockRejectedValue(new Error("该方案不属于当前版本"));
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");

    await user.click(screen.getByRole("button", { name: "选择此方案" }));

    expect(await screen.findByText("该方案不属于当前版本", { selector: ".inspector-selection-error" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "已选择此方案" })).not.toBeInTheDocument();
  });

  it("restores the persisted selected plan from the session snapshot", async () => {
    const user = userEvent.setup();
    api.listSessions.mockResolvedValue([{
      session_id: "selected-session", title: "已选方案会话", status: "completed",
      created_at: "2026-08-20T00:00:00Z", updated_at: "2026-08-20T02:00:00Z",
      last_message_preview: "已生成方案",
    }]);
    api.getSession.mockResolvedValue({
      session_id: "selected-session", title: "已选方案会话", status: "completed",
      created_at: "2026-08-20T00:00:00Z", updated_at: "2026-08-20T02:00:00Z",
      messages: [
        { id: "user-1", role: "user", content: "今天下午出去玩" },
        { id: "assistant-1", role: "assistant", content: "已生成方案" },
      ],
      plans: response.plans,
      latest_response: response,
      response_history: [response],
      active_plan_version_id: "v1",
      selected_plan_id: "plan-two",
    });
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /已选方案会话/ }));

    expect(await screen.findByRole("button", { name: "已选择此方案" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "预览方案二" })).toHaveAttribute("aria-pressed", "true");
  });

  it("keeps the active plan visible when the latest restored turn is a question", async () => {
    const user = userEvent.setup();
    const firstResponse = { ...response, plan_version_id: "v1", reply: "第一轮方案已经整理好。" };
    const questionResponse: Partial<AgentResponse> = {
      status: "needs_input",
      reply: "",
      question: { field: "budget", question: "预算是多少？", severity: "blocking", request_revision: 0 },
      plans: [],
      conflict: null,
      plan_version_id: null,
    };
    api.listSessions.mockResolvedValue([{
      session_id: "question-session", title: "反问后的会话", status: "needs_input",
      created_at: "2026-08-20T00:00:00Z", updated_at: "2026-08-20T02:00:00Z",
      last_message_preview: "预算是多少？",
    }]);
    api.getSession.mockResolvedValue({
      session_id: "question-session", title: "反问后的会话", status: "needs_input",
      created_at: "2026-08-20T00:00:00Z", updated_at: "2026-08-20T02:00:00Z",
      messages: [
        { id: "user-1", role: "user", content: "今天下午出去玩" },
        { id: "assistant-1", role: "assistant", content: firstResponse.reply },
        { id: "user-2", role: "user", content: "预算是多少" },
        { id: "assistant-2", role: "assistant", content: "预算是多少？" },
      ],
      plans: firstResponse.plans,
      latest_response: questionResponse,
      response_history: [firstResponse, questionResponse],
      active_plan_version_id: "v1",
      selected_plan_id: "plan-two",
    });

    render(<App />);
    await user.click(await screen.findByRole("button", { name: /反问后的会话/ }));

    const detailPanel = document.querySelector(".detail-panel");
    expect(detailPanel).not.toBeNull();
    expect(within(detailPanel as HTMLElement).queryByText("历史预览 · 只读")).not.toBeInTheDocument();
    expect(detailPanel?.querySelector(".inspector-plan-header")).toHaveTextContent("方案二");
  });

  it("uses the selected plan id returned by the backend", async () => {
    const user = userEvent.setup();
    api.selectPlan.mockResolvedValue({ active_plan_version_id: "v1", selected_plan_id: "plan-one" });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");

    await user.click(screen.getByRole("button", { name: "预览方案二" }));
    await user.click(screen.getByRole("button", { name: "选择此方案" }));

    expect(await screen.findByRole("button", { name: "已选择此方案" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "预览方案一" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "预览方案二" })).toHaveAttribute("aria-pressed", "false");
  });

  it("ignores a stale selection response after a newer plan version arrives", async () => {
    const user = userEvent.setup();
    let resolveSelection: ((value: { active_plan_version_id: string; selected_plan_id: string }) => void) | undefined;
    api.sendMessage
      .mockResolvedValueOnce({ ...response, plan_version_id: "v1" })
      .mockResolvedValueOnce({
        ...response,
        reply: "新一轮方案已整理。",
        plans: [plan("plan-new", "新一轮方案")],
        plan_version_id: "v2",
      });
    api.selectPlan.mockImplementation(() => new Promise((resolve) => {
      resolveSelection = resolve;
    }));
    render(<App />);
    const input = screen.getByLabelText("描述你的空闲时间和偏好");
    await user.type(input, "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");
    await user.click(screen.getByRole("button", { name: "选择此方案" }));

    await user.type(input, "重新生成一轮");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findByRole("heading", { name: "新一轮方案", level: 3 });
    expect(screen.queryByRole("button", { name: "已选择此方案" })).not.toBeInTheDocument();

    resolveSelection?.({ active_plan_version_id: "v1", selected_plan_id: "plan-one" });
    await waitFor(() => expect(screen.queryByRole("button", { name: "已选择此方案" })).not.toBeInTheDocument());
  });

  it("keeps the active plan details visible when a live turn asks a question", async () => {
    const user = userEvent.setup();
    api.sendMessage
      .mockResolvedValueOnce({ ...response, poi_presentations: [presentation("plan-one-activity", "展览")] })
      .mockResolvedValueOnce({
        status: "needs_input",
        reply: "",
        question: { field: "budget", question: "预算是多少？", severity: "blocking", request_revision: 0 },
        assumptions: [], constraint_summary: [], plans: [], conflict: null,
        provider_facts: [], catalog_violations: [], catalog_warnings: [], warnings: [], poi_presentations: [],
      });
    render(<App />);
    const input = screen.getByLabelText("描述你的空闲时间和偏好");
    await user.type(input, "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");
    await user.click(screen.getByRole("button", { name: "选择此方案" }));
    await screen.findByRole("button", { name: "已选择此方案" });
    await user.click(screen.getByRole("button", { name: "查看第 1 站 展览 详情" }));
    expect(await screen.findByRole("heading", { name: "展览", level: 2 })).toBeVisible();

    await user.type(input, "预算是多少");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findByText("预算是多少？");
    expect(screen.getByRole("heading", { name: "展览", level: 2 })).toBeVisible();
    expect(screen.getByRole("button", { name: "返回行程" })).toBeVisible();

    const detailPanel = document.querySelector(".detail-panel");
    expect(detailPanel).not.toBeNull();
    expect(within(detailPanel as HTMLElement).getByRole("heading", { name: "展览", level: 2 })).toBeVisible();
    await user.click(within(detailPanel as HTMLElement).getByRole("button", { name: "返回行程" }));
    expect(within(detailPanel as HTMLElement).getByText("已正式选择")).toBeVisible();
    expect(screen.getByRole("button", { name: "已选择此方案" })).toBeDisabled();
  });

  it("renders backend clarification options and sends a scoped default action", async () => {
    const user = userEvent.setup();
    const questionResponse: AgentResponse = {
      ...response,
      status: "needs_input",
      reply: "",
      plans: [],
      question: {
        field: "location",
        question: "我还不能确定这个位置，能提供更具体的地点或地标吗？",
        severity: "blocking",
        request_revision: 0,
        clarification_id: "clarification-location-1",
        attempt: 0,
        max_attempts: 2,
        allow_free_text: true,
        options: [
          { id: "use-default-location", label: "使用默认出发地", action: "use_default" },
          { id: "cancel", label: "取消本轮", action: "cancel" },
          { id: "new-request", label: "开始新需求", action: "new_request" },
        ],
      },
    };
    api.sendMessage.mockResolvedValueOnce(questionResponse).mockResolvedValueOnce(response);
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "去一个不确定的地方");
    await user.click(screen.getByRole("button", { name: "发送需求" }));

    const clarificationCard = await screen.findByRole("group", { name: "补充信息选项" });
    expect(screen.getAllByText("我还不能确定这个位置，能提供更具体的地点或地标吗？")).toHaveLength(1);
    expect(clarificationCard.closest(".message")).toHaveClass("structured-turn");
    expect(clarificationCard.closest(".message-entry")?.querySelector(".message-divider")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "使用默认出发地" }));
    await waitFor(() => expect(api.sendMessage).toHaveBeenCalledTimes(2));
    expect(api.sendMessage.mock.calls.at(-1)?.[4]).toEqual({
      clarification_id: "clarification-location-1",
      request_revision: 0,
      action: "use_default",
      value: null,
    });
  });

  it("keeps an earlier clarification readable but disables it after a newer question arrives", async () => {
    const user = userEvent.setup();
    const firstQuestion: AgentResponse = {
      ...response,
      status: "needs_input",
      reply: "",
      plans: [],
      question: {
        field: "location",
        question: "請補充出發區域。",
        severity: "blocking",
        request_revision: 1,
        clarification_id: "clarification-location-old",
        options: [{ id: "use-default", label: "使用默认出发地", action: "use_default" }],
      },
    };
    const secondQuestion: AgentResponse = {
      ...firstQuestion,
      question: {
        ...firstQuestion.question!,
        question: "请确认预算范围。",
        field: "budget_per_person",
        request_revision: 2,
        clarification_id: "clarification-budget-current",
        options: [{ id: "confirm-budget", label: "确认预算", action: "answer" }],
      },
    };
    api.sendMessage.mockResolvedValueOnce(firstQuestion).mockResolvedValueOnce(secondQuestion).mockResolvedValueOnce(response);

    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "安排出行");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findByRole("group", { name: "补充信息选项" });
    await user.type(screen.getByLabelText("补充这个信息后继续"), "北京市朝阳区");
    await user.click(screen.getByRole("button", { name: "发送需求" }));

    const oldQuestion = screen.getAllByRole("group", { name: "补充信息选项" })[0];
    const oldOption = within(oldQuestion).getByRole("button", { name: "使用默认出发地" });
    expect(oldOption).toBeDisabled();
    expect(oldQuestion).toHaveTextContent("这条问题可继续查看");
    await user.click(screen.getByRole("button", { name: "确认预算" }));
    await waitFor(() => expect(api.sendMessage).toHaveBeenCalledTimes(3));
    expect(api.sendMessage.mock.calls.at(-1)?.[4]).toEqual({
      clarification_id: "clarification-budget-current",
      request_revision: 2,
      action: "answer",
      value: null,
    });
  });

  it("answers a date clarification with the selected ISO date and scoped resume metadata", async () => {
    const user = userEvent.setup();
    const dateQuestion: AgentResponse = {
      ...response,
      status: "needs_input",
      reply: "",
      plans: [],
      question: {
        field: "date",
        question: "你说的日期已过去，请确认具体年份和日期。",
        severity: "blocking",
        request_revision: 4,
        clarification_id: "clarification-date-4",
        attempt: 0,
        max_attempts: 2,
        allow_free_text: true,
        options: [],
      },
    };
    api.sendMessage.mockResolvedValueOnce(dateQuestion).mockResolvedValueOnce(response);
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "9月30日出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));

    expect(await screen.findByRole("group", { name: "确认出行日期" })).toHaveTextContent("需要补充：出行日期");
    const datePicker = await screen.findByLabelText("选择出行日期");
    fireEvent.change(datePicker, { target: { value: "2026-10-17" } });
    await user.click(screen.getByRole("button", { name: "确认日期" }));

    await waitFor(() => expect(api.sendMessage).toHaveBeenCalledTimes(2));
    expect(api.sendMessage.mock.calls.at(-1)?.[4]).toEqual({
      clarification_id: "clarification-date-4",
      request_revision: 4,
      action: "answer",
      value: "2026-10-17",
    });
  });

  it("renders a structured conflict once instead of echoing it in a chat bubble", async () => {
    const user = userEvent.setup();
    const conflictMessage = "出发时间晚于返程时间，无法生成方案。";
    api.sendMessage.mockResolvedValue({
      ...response,
      reply: conflictMessage,
      plans: [],
      conflict: {
        code: "departure_after_return",
        message: conflictMessage,
      },
    });
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "安排今天出行");
    await user.click(screen.getByRole("button", { name: "发送需求" }));

    expect(await screen.findByRole("status")).toHaveTextContent(conflictMessage);
    expect(screen.getAllByText(conflictMessage)).toHaveLength(1);
    expect(screen.getByRole("status").closest(".message")).toHaveClass("structured-turn");
  });

  it("requires confirmation for a server patch proposal, and cancel sends no request", async () => {
    const user = userEvent.setup();
    const recoveryResponse: AgentResponse = {
      ...response,
      status: "needs_recovery",
      reply: "当前条件下暂未找到可行方案。",
      plans: [],
      planning_context: planningContext({
        max_distance_km: { value: 8, display_value: "8 km", source: "user", editable: true, status: "resolved" },
      }),
      recovery: {
        kind: "recovery_choice",
        interaction_id: "recovery-1",
        request_revision: 3,
        plan_version_id: null,
        reason: {
          code: "NO_CANDIDATES_WITHIN_SEARCH_RADIUS",
          kind: "no_feasible_plan",
          stage: "retrieval",
          fields: ["max_distance_km"],
          request_revision: 3,
          plan_version_id: null,
          public_summary: "当前条件下暂未找到可行方案。",
          diagnostics: { current_max_distance_km: 8 },
        },
        actions: [{
          kind: "apply_request_patch",
          action_id: "expand-distance-r3-12",
          label: "尝试扩大到 12 公里并重新规划",
          description: "这是一次尝试，不保证一定能找到方案。",
          request_revision: 3,
          plan_version_id: null,
          continuation: "plan",
          patch: { base_revision: 3, set_fields: { max_distance_km: 12 } },
        }],
      },
    };
    api.sendMessage.mockResolvedValueOnce(recoveryResponse).mockResolvedValueOnce(response);

    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "安排今天出行");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findByRole("group", { name: "规划恢复选项" });
    expect(screen.getByText("提案：检索及单段距离上限 → 12 km")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /修改出发地/ })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /尝试扩大到 12 公里/ }));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("确认调整条件");
    expect(dialog).toHaveTextContent("当前条件8 km");
    expect(dialog).toHaveTextContent("确认后12 km");
    expect(api.sendMessage).toHaveBeenCalledTimes(1);
    await user.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(api.sendMessage).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /尝试扩大到 12 公里/ }));
    const confirm = await screen.findByRole("button", { name: "确认并继续" });
    await user.dblClick(confirm);
    await waitFor(() => expect(api.sendMessage).toHaveBeenCalledTimes(2));
    expect(api.sendMessage.mock.calls.at(-1)?.[5]).toEqual({
      interaction_id: "recovery-1",
      action_id: "expand-distance-r3-12",
      request_revision: 3,
      plan_version_id: null,
    });
    expect(document.querySelectorAll(".message.user")).toHaveLength(1);
    expect(screen.getByText("调整已应用，新的方案已生成并重新核验。")).toBeInTheDocument();
    expect(screen.getByText("此恢复选项已结束，不能重复执行。")).toBeInTheDocument();
  });

  it("retries a failed network request with the same request id", async () => {
    const user = userEvent.setup();
    api.sendMessageStream.mockRejectedValueOnce(new Error("网络连接失败"));
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("网络连接失败");
    await user.click(within(alert).getByRole("button", { name: "重试本次请求" }));
    await screen.findAllByText("方案一");
    expect(api.sendMessageStream).toHaveBeenCalledTimes(2);
    expect(api.sendMessageStream.mock.calls[0]?.[2]).toBe(api.sendMessageStream.mock.calls[1]?.[2]);
  });

  it("expires an open recovery proposal when the user changes sessions", async () => {
    const user = userEvent.setup();
    const recoveryResponse: AgentResponse = {
      ...response,
      status: "needs_recovery",
      plans: [],
      recovery: {
        kind: "recovery_choice",
        interaction_id: "recovery-session-bound",
        request_revision: 2,
        plan_version_id: null,
        reason: {
          code: "NO_PLAN_WITHIN_DISTANCE",
          kind: "no_feasible_plan",
          stage: "retrieve",
          fields: ["max_distance_km"],
          request_revision: 2,
          plan_version_id: null,
          public_summary: "当前条件下没有找到可行方案。",
          diagnostics: {},
        },
        actions: [{
          kind: "apply_request_patch",
          action_id: "expand-distance-r2",
          label: "扩大单段距离后继续",
          description: "这是一次尝试。",
          request_revision: 2,
          plan_version_id: null,
          continuation: "plan",
          patch: { set_fields: { max_distance_km: 12 } },
        }],
      },
    };
    api.sendMessage.mockResolvedValueOnce(recoveryResponse);
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "安排出行");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findByRole("group", { name: "规划恢复选项" });
    await user.click(screen.getByRole("button", { name: /扩大单段距离后继续/ }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(api.sendMessage).toHaveBeenCalledTimes(1);

    window.history.pushState({}, "", "/");
    fireEvent.popState(window);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(api.sendMessage).toHaveBeenCalledTimes(1);
  });

  it("does not let an in-flight answer from a previous session replace the new session", async () => {
    const user = userEvent.setup();
    let resolveOldResponse: ((value: AgentResponse) => void) | undefined;
    let publishOldProgress: ((event: PlanningRunEvent) => void) | undefined;
    let resolveNewResponse: ((value: AgentResponse) => void) | undefined;
    let publishNewProgress: ((event: PlanningRunEvent) => void) | undefined;
    api.sendMessageStream.mockImplementationOnce((_sessionId, _content, _requestId, onProgress) => new Promise((resolve) => {
      resolveOldResponse = resolve;
      publishOldProgress = onProgress as (event: PlanningRunEvent) => void;
    }));
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "旧会话请求");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await waitFor(() => expect(api.sendMessageStream).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole("button", { name: "新建规划" }));
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "新会话请求");
    api.sendMessageStream.mockImplementationOnce((_sessionId, _content, _requestId, onProgress) => new Promise((resolve) => {
      resolveNewResponse = resolve;
      publishNewProgress = onProgress as (event: PlanningRunEvent) => void;
    }));
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await waitFor(() => expect(api.sendMessageStream).toHaveBeenCalledTimes(2));
    const progressEvent = (public_message: string, sequence: number, stage: PlanningRunEvent["stage"]): PlanningRunEvent => ({
      schema_version: "planning-run-event.v1",
      run_id: `run-${sequence}`,
      sequence,
      stage,
      status: "started",
      message_key: "test.progress",
      public_message,
      occurred_at: "2026-08-15T10:00:00+08:00",
      duration_ms: null,
      public_details: {},
    });
    publishNewProgress?.(progressEvent("新会话正在检索候选", 1, "retrieve"));
    expect(await screen.findAllByText("新会话正在检索候选")).not.toHaveLength(0);
    publishOldProgress?.(progressEvent("旧会话迟到进度", 2, "verify"));
    expect(screen.queryByText("旧会话迟到进度")).not.toBeInTheDocument();
    resolveOldResponse?.({ ...response, reply: "旧会话响应", plans: [] });
    await waitFor(() => expect(screen.queryByText("旧会话响应")).not.toBeInTheDocument());
    expect(screen.getAllByText("新会话正在检索候选")).not.toHaveLength(0);
    resolveNewResponse?.(response);
    await screen.findAllByText("方案一");
  });

  it("ignores an in-flight condition save after switching sessions and preserves the new session loading state", async () => {
    const user = userEvent.setup();
    const sourceContext = planningContextForArea("当前会话区域");
    const targetContext = planningContextForArea("目标会话区域");
    const targetResponse = {
      ...response,
      reply: "目标会话已恢复",
      plans: [plan("target-plan", "目标会话方案")],
      plan_version_id: "target-version",
      planning_context: targetContext,
    };
    api.sendMessage.mockResolvedValueOnce({ ...response, planning_context: sourceContext });
    api.searchDepartureLocations.mockResolvedValue({
      city: "北京市", source: "mock", mode: "mock", degraded: false,
      results: [{ kind: "provider_place", provider_id: "mock:old-session", name: "旧会话修改区域", city: "北京市", district: "朝阳区", address: "朝阳区旧会话路" }],
    });
    api.getSession.mockResolvedValue(restoredSession("target-session", "目标会话", targetResponse));
    let resolveOldSave: ((value: AgentResponse) => void) | undefined;
    api.updatePlanningContext.mockImplementationOnce(() => new Promise((resolve) => { resolveOldSave = resolve; }));

    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "初始需求");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");
    await user.click(screen.getByRole("button", { name: /修改出发地/ }));
    await user.type(screen.getByRole("combobox", { name: /搜索出发地点/ }), "旧会话");
    await user.click(await screen.findByRole("option", { name: /旧会话修改区域/ }));
    await user.click(screen.getByRole("button", { name: "保存条件" }));
    await waitFor(() => expect(api.updatePlanningContext).toHaveBeenCalledTimes(1));

    window.history.pushState({}, "", "/?session=target-session");
    fireEvent.popState(window);
    await screen.findByRole("heading", { name: "目标会话方案", level: 3 });
    let resolveCurrentMessage: ((value: AgentResponse) => void) | undefined;
    api.sendMessageStream.mockImplementationOnce(() => new Promise((resolve) => { resolveCurrentMessage = resolve; }));
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "目标会话中的新请求");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await waitFor(() => expect(api.sendMessageStream).toHaveBeenCalledTimes(2));
    expect(api.sendMessageStream.mock.calls[1]?.[0]).toBe("target-session");

    resolveOldSave?.({
      ...response,
      reply: "过期条件保存响应",
      plans: [plan("old-save-plan", "旧会话保存方案")],
      plan_version_id: "old-save-version",
      planning_context: planningContextForArea("旧会话区域"),
    });
    await waitFor(() => expect(screen.queryByRole("heading", { name: "旧会话保存方案", level: 3 })).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "目标会话方案", level: 3 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /修改出发地/ })).toHaveAccessibleName(/目标会话区域/);
    expect(screen.getByLabelText("描述你的空闲时间和偏好")).toBeDisabled();

    await act(async () => resolveCurrentMessage?.({ ...response, reply: "目标会话请求完成", planning_context: targetContext }));
    await waitFor(() => expect(screen.getByLabelText("描述你的空闲时间和偏好")).toBeEnabled());
  });

  it("ignores an in-flight replan after switching sessions without clearing the new request loading state", async () => {
    const user = userEvent.setup();
    const staleContext = planningContextForArea("旧方案区域", { plan_stale: true, planned_request_revision: 0 });
    const targetContext = planningContextForArea("新会话区域");
    const targetResponse = {
      ...response,
      reply: "新会话已恢复",
      plans: [plan("replan-target", "新会话原方案")],
      plan_version_id: "target-version",
      planning_context: targetContext,
    };
    api.sendMessage.mockResolvedValueOnce({ ...response, planning_context: staleContext });
    api.getSession.mockResolvedValue(restoredSession("replan-target-session", "新会话", targetResponse));
    let resolveOldReplan: ((value: AgentResponse) => void) | undefined;
    api.replanPlanningContext.mockImplementationOnce(() => new Promise((resolve) => { resolveOldReplan = resolve; }));

    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "生成旧条件方案");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");
    await user.click(screen.getByRole("button", { name: "按新条件重新规划" }));
    await waitFor(() => expect(api.replanPlanningContext).toHaveBeenCalledTimes(1));

    window.history.pushState({}, "", "/?session=replan-target-session");
    fireEvent.popState(window);
    await screen.findByRole("heading", { name: "新会话原方案", level: 3 });
    let resolveCurrentMessage: ((value: AgentResponse) => void) | undefined;
    api.sendMessageStream.mockImplementationOnce(() => new Promise((resolve) => { resolveCurrentMessage = resolve; }));
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "新会话中的请求");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await waitFor(() => expect(api.sendMessageStream).toHaveBeenCalledTimes(2));
    expect(api.sendMessageStream.mock.calls[1]?.[0]).toBe("replan-target-session");

    resolveOldReplan?.({
      ...response,
      reply: "过期重新规划响应",
      plans: [plan("old-replan-plan", "旧会话重新规划方案")],
      plan_version_id: "old-replan-version",
      planning_context: planningContextForArea("过期重新规划区域"),
    });
    await waitFor(() => expect(screen.queryByRole("heading", { name: "旧会话重新规划方案", level: 3 })).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "新会话原方案", level: 3 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /修改出发地/ })).toHaveAccessibleName(/新会话区域/);
    expect(screen.getByLabelText("描述你的空闲时间和偏好")).toBeDisabled();

    await act(async () => resolveCurrentMessage?.({ ...response, reply: "新会话请求完成", planning_context: targetContext }));
    await waitFor(() => expect(screen.getByLabelText("描述你的空闲时间和偏好")).toBeEnabled());
  });

  it("shows a completed recovery action separately from a later failed recovery", async () => {
    const user = userEvent.setup();
    const firstRecovery: AgentResponse = {
      ...response,
      status: "needs_recovery",
      reply: "当前条件下暂未找到可行方案。",
      plans: [],
      recovery: {
        kind: "recovery_choice",
        interaction_id: "recovery-still-fails-1",
        request_revision: 3,
        plan_version_id: null,
        reason: {
          code: "NO_PLAN_WITHIN_DISTANCE",
          kind: "no_feasible_plan",
          stage: "verify",
          fields: ["max_distance_km"],
          request_revision: 3,
          plan_version_id: null,
          public_summary: "当前条件下暂未找到可行方案。",
          diagnostics: {},
        },
        actions: [{
          kind: "apply_request_patch",
          action_id: "try-distance-15",
          label: "尝试扩大到 15 公里并重新规划",
          description: "可以尝试，但不保证一定有方案。",
          request_revision: 3,
          plan_version_id: null,
          continuation: "plan",
          patch: { base_revision: 3, set_fields: { max_distance_km: 15 } },
        }],
      },
    };
    const secondRecovery: AgentResponse = {
      ...firstRecovery,
      recovery: { ...firstRecovery.recovery!, interaction_id: "recovery-still-fails-2", request_revision: 4 },
    };
    api.sendMessage.mockResolvedValueOnce(firstRecovery).mockResolvedValueOnce(secondRecovery);

    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "安排今天出行");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findByRole("group", { name: "规划恢复选项" });
    await user.click(screen.getByRole("button", { name: /尝试扩大到 15 公里/ }));
    await user.click(await screen.findByRole("button", { name: "确认并继续" }));

    await waitFor(() => expect(api.sendMessage).toHaveBeenCalledTimes(2));
    expect(screen.getByText("调整已应用，但当前条件下仍未找到可行方案；请从新的选项继续。")).toBeInTheDocument();
    expect(screen.getAllByRole("group", { name: "规划恢复选项" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: /尝试扩大到 15 公里/ })).toHaveLength(1);
  });

  it("uses only server-provided choices for a request-field recovery action", async () => {
    const user = userEvent.setup();
    const recoveryResponse: AgentResponse = {
      ...response,
      status: "needs_recovery",
      plans: [],
      recovery: {
        kind: "recovery_choice",
        interaction_id: "recovery-request-field",
        request_revision: 5,
        plan_version_id: "v1",
        reason: {
          code: "MISSING_RETURN_TIME",
          kind: "hard_conflict",
          stage: "constraints",
          fields: ["end_at"],
          request_revision: 5,
          plan_version_id: "v1",
          public_summary: "还需要确认结束时间。",
          diagnostics: {},
        },
        actions: [{
          kind: "request_field",
          action_id: "choose-end-time",
          label: "选择结束时间",
          description: "请选择一个可用时间。",
          field: "end_at",
          input_type: "choice",
          choices: ["18:00", "20:00"],
          request_revision: 5,
          plan_version_id: "v1",
          continuation: "clarify_field",
        }],
      },
    };
    const awaitingResponse: AgentResponse = {
      ...response,
      status: "needs_input",
      plans: [],
      reply: "请确认结束时间。",
      question: {
        field: "end_at",
        question: "请确认结束时间。",
        severity: "blocking",
        request_revision: 5,
        clarification_id: "clarification-end-time",
        options: [],
      },
      recovery_outcome: {
        interaction_id: "recovery-request-field",
        action_id: "choose-end-time",
        kind: "request_field",
        label: "选择结束时间",
        status: "awaiting_input",
        changed_fields: [],
        summary: "已收到时间选项，请完成补充。",
      },
    };
    api.sendMessage.mockResolvedValueOnce(recoveryResponse).mockResolvedValueOnce(awaitingResponse);
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "安排今天出行");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findByRole("group", { name: "规划恢复选项" });
    expect(screen.getByRole("button", { name: "18:00" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "20:00" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "20:00" }));
    await waitFor(() => expect(api.sendMessage).toHaveBeenCalledTimes(2));
    expect(api.sendMessage.mock.calls.at(-1)?.[5]).toEqual({
      interaction_id: "recovery-request-field",
      action_id: "choose-end-time",
      request_revision: 5,
      plan_version_id: "v1",
      field_value: "20:00",
    });
    expect(screen.getByText("已收到时间选项，请完成补充。")).toBeInTheDocument();
  });

  it("keeps cancel, keep-current-plan, and start-new-request recovery outcomes distinct", async () => {
    const user = userEvent.setup();
    const choice = (interactionId: string, revision: number, kind: "cancel_turn" | "keep_current_plan" | "start_new_request", actionId: string, label: string): AgentResponse => ({
      ...response,
      status: "needs_recovery",
      plans: [],
      recovery: {
        kind: "recovery_choice",
        interaction_id: interactionId,
        request_revision: revision,
        plan_version_id: "v1",
        reason: {
          code: "NO_PLAN_WITHIN_DISTANCE",
          kind: "no_feasible_plan",
          stage: "verify",
          fields: ["max_distance_km"],
          request_revision: revision,
          plan_version_id: "v1",
          public_summary: "当前条件下没有可行方案。",
          diagnostics: {},
        },
        actions: [{
          kind,
          action_id: actionId,
          label,
          description: "由服务端返回的恢复选项。",
          request_revision: revision,
          plan_version_id: "v1",
          continuation: "finish",
        } as RecoveryAction],
      },
    });
    const cancelled: AgentResponse = {
      ...response,
      reply: "本轮已取消。",
      plans: [],
      recovery_resolution: "cancel_turn",
      recovery_outcome: {
        interaction_id: "recovery-cancel",
        action_id: "cancel-turn",
        kind: "cancel_turn",
        label: "取消本轮",
        status: "cancelled",
        changed_fields: [],
        summary: "本轮已取消，原方案和已确认条件未修改。",
      },
    };
    const kept: AgentResponse = {
      ...response,
      reply: "已保留当前方案。",
      plans: [],
      recovery_resolution: "keep_current_plan",
      recovery_outcome: {
        interaction_id: "recovery-keep",
        action_id: "keep-plan",
        kind: "keep_current_plan",
        label: "保留当前方案",
        status: "finished",
        changed_fields: [],
        summary: "已保留当前方案，原方案没有被修改。",
      },
    };
    const started: AgentResponse = {
      ...response,
      reply: "已开始新的规划需求。",
      plans: [],
      recovery_resolution: "start_new_request",
      recovery_outcome: {
        interaction_id: "recovery-new-request",
        action_id: "new-request",
        kind: "start_new_request",
        label: "开始新需求",
        status: "finished",
        changed_fields: [],
        summary: "已开始新的规划需求，上一轮条件不会继续继承。",
      },
    };
    api.sendMessage
      .mockResolvedValueOnce(response)
      .mockResolvedValueOnce(choice("recovery-cancel", 2, "cancel_turn", "cancel-turn", "取消本轮"))
      .mockResolvedValueOnce(cancelled)
      .mockResolvedValueOnce(choice("recovery-keep", 3, "keep_current_plan", "keep-plan", "保留当前方案"))
      .mockResolvedValueOnce(kept)
      .mockResolvedValueOnce(choice("recovery-new-request", 4, "start_new_request", "new-request", "开始新需求"))
      .mockResolvedValueOnce(started);

    render(<App />);
    const sendPrompt = async (content: string) => {
      await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), content);
      await user.click(screen.getByRole("button", { name: "发送需求" }));
    };
    await sendPrompt("第一轮需求");
    await screen.findAllByText("方案一");
    await user.click(screen.getByRole("button", { name: "选择此方案" }));
    await screen.findByRole("button", { name: "已选择此方案" });
    await sendPrompt("请求取消本轮");
    await screen.findAllByRole("group", { name: "规划恢复选项" });
    await user.click(screen.getByRole("button", { name: /^取消本轮/ }));
    await waitFor(() => expect(api.sendMessage).toHaveBeenCalledTimes(3));
    expect(screen.getByText("本轮已取消，原方案和已确认条件未修改。")).toBeInTheDocument();
    expect(screen.getAllByText("方案一").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "已选择此方案" })).toBeInTheDocument();

    await sendPrompt("请求保留旧方案");
    await screen.findAllByRole("group", { name: "规划恢复选项" });
    await user.click(screen.getByRole("button", { name: /^保留当前方案/ }));
    await waitFor(() => expect(api.sendMessage).toHaveBeenCalledTimes(5));
    expect(screen.getByText("已保留当前方案，原方案没有被修改。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "已选择此方案" })).toBeInTheDocument();

    await sendPrompt("开始新一轮需求");
    await screen.findAllByRole("group", { name: "规划恢复选项" });
    await user.click(screen.getByRole("button", { name: /^开始新需求/ }));
    await waitFor(() => expect(api.sendMessage).toHaveBeenCalledTimes(7));
    expect(screen.getByText("已开始新的规划需求，上一轮条件不会继续继承。")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("描述新的规划需求……")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "已选择此方案" })).not.toBeInTheDocument();
  });

  it("lets a pending clarification switch into an explicit new request", async () => {
    const user = userEvent.setup();
    const questionResponse: AgentResponse = {
      ...response,
      status: "needs_input",
      reply: "",
      plans: [],
      question: {
        field: "location",
        question: "请补充地点",
        severity: "blocking",
        request_revision: 0,
        clarification_id: "clarification-location-2",
        allow_free_text: false,
        options: [{ id: "new-request", label: "开始新需求", action: "new_request" }],
      },
    };
    api.sendMessage.mockResolvedValueOnce(questionResponse).mockResolvedValueOnce(response);
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "原始需求");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findByRole("button", { name: "开始新需求" });
    expect(screen.getByLabelText("补充这个信息后继续")).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "开始新需求" }));
    expect(screen.getByPlaceholderText("描述新的规划需求……")).toBeEnabled();
    await user.type(screen.getByPlaceholderText("描述新的规划需求……"), "今天下午去看展");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await waitFor(() => expect(api.sendMessage).toHaveBeenCalledTimes(2));
    expect(api.sendMessage.mock.calls.at(-1)?.[4]).toEqual({
      clarification_id: "clarification-location-2",
      request_revision: 0,
      action: "new_request",
      value: "今天下午去看展",
    });
  });

  it("clears the selection when a new plan version is generated", async () => {
    const user = userEvent.setup();
    api.sendMessage
      .mockResolvedValueOnce(response)
      .mockResolvedValueOnce({ ...response, reply: "新一轮方案已整理。", plans: [plan("plan-new", "新一轮方案")], plan_version_id: "v2" });
    render(<App />);
    const input = screen.getByLabelText("描述你的空闲时间和偏好");
    await user.type(input, "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");
    await user.click(screen.getByRole("button", { name: "选择此方案" }));
    await screen.findByRole("button", { name: "已选择此方案" });

    await user.type(input, "再来一轮");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findByRole("heading", { name: "新一轮方案", level: 3 });

    expect(screen.queryByRole("button", { name: "已选择此方案" })).not.toBeInTheDocument();
  });
});
