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
    data_notice: "演示环境：POI 商业信息为模拟数据，地图与路线来自高德。",
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
    expect(screen.getByText("演示环境：POI 商业信息为模拟数据，地图与路线来自高德。")).toBeInTheDocument();
  });
});
