import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import type { AgentResponse, Plan } from "./types";

const api = vi.hoisted(() => ({
  createSession: vi.fn(),
  getSession: vi.fn(),
  listSessions: vi.fn(),
  sendMessage: vi.fn(),
}));

vi.mock("./api", () => ({
  ...api,
  getMapConfig: vi.fn(),
}));

vi.mock("./AmapPlanMap", () => ({
  AmapPlanMap: ({ activeLegIndex, onSelectRoute }: { activeLegIndex: number | null; onSelectRoute: (index: number) => void }) => (
    <button type="button" onClick={() => onSelectRoute(1)}>测试地图，当前路线 {activeLegIndex ?? "none"}</button>
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
  provider_facts: [], catalog_violations: [], catalog_warnings: [],
  poi_presentations: [],
};

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
    window.history.replaceState({}, "", "/");
    api.listSessions.mockResolvedValue([]);
    api.createSession.mockResolvedValue("session-test");
    api.sendMessage.mockResolvedValue(response);
  });

  it("switches plans and keeps timeline, map rows, and RouteLeg indices aligned", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("描述你的空闲时间和偏好"), "今天下午出去玩");
    await user.click(screen.getByRole("button", { name: "发送需求" }));
    await screen.findAllByText("方案一");

    await user.click(screen.getByRole("button", { name: /方案二/ }));
    expect(screen.getByRole("button", { name: /方案二/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByRole("button", { name: /查看返程/ })).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: /查看出发路线：出发地 → 展览/ }));
    expect(screen.getByRole("tab", { name: "地图" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("测试地图，当前路线 0")).toBeInTheDocument();
    expect(screen.getByText("本地估算")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /展览 → 晚餐/ }));
    expect(screen.getByRole("tab", { name: "行程" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: /查看下一程：2km/ })).toHaveAttribute("aria-pressed", "true");
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

    expect(await screen.findByLabelText("地点费用，不含交通：全员合计 ¥300")).toBeInTheDocument();
    expect(screen.getByLabelText("地点费用，不含交通：全员约 ¥260")).toBeInTheDocument();
    expect(screen.getByLabelText("地点费用，不含交通：全员已知费用 ¥180 + 未知价格")).toBeInTheDocument();
    expect(screen.queryByText(/\/ 人/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("快速调整方案")).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/餐厅保留|换近一点/)).not.toBeInTheDocument();
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

    expect(await screen.findByLabelText("方案待确认信息")).toHaveTextContent("动态可用性尚未得到完整确认");
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

    expect((await screen.findAllByText("演示评分")).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/测试路 1 号/).length).toBeGreaterThan(0);
    expect(screen.getByText("POI 商业信息为模拟数据；路线来源和降级状态见各路线段。")).toBeInTheDocument();
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
    expect(assistantTurns[0]).toHaveTextContent("第一轮方案已经整理好");
    expect(assistantTurns[0].querySelector(".rich-planning-reply")).not.toBeNull();
    expect(assistantTurns[1]).toHaveTextContent("第二轮方案已经整理好");
    expect(assistantTurns[1].querySelector(".rich-planning-reply")).not.toBeNull();
    expect(container.querySelectorAll(".message.assistant > .butler-avatar")).toHaveLength(2);
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
});
