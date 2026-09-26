import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AmapPlanMap } from "./AmapPlanMap";
import * as api from "./api";
import type { Plan } from "./types";

vi.mock("./api", () => ({ getMapConfig: vi.fn() }));

const plan = {
  plan_id: "map-plan", composition_fingerprint: "map-plan", skeleton_id: null, title: "地图测试", strategy: "balanced", total_score: 1, total_price: 0, price_status: "known", total_duration_minutes: 60,
  stops: [],
  route_legs: [],
  score_breakdown: [], highlights: [], tradeoffs: [],
} as Plan;

describe("AmapPlanMap", () => {
  afterEach(() => vi.clearAllMocks());

  it("keeps textual route fallback when the browser key is disabled", async () => {
    vi.mocked(api.getMapConfig).mockResolvedValue({ enabled: false, provider: "none", js_api_key: null, version: null, service_host_path: null });
    render(<AmapPlanMap plan={plan} activeLegIndex={null} onSelectRoute={vi.fn()} />);

    expect(await screen.findByText("未配置高德 JS API，当前仅显示路线文字摘要")).toBeInTheDocument();
  });

  it("renders a visible provider configuration error instead of failing silently", async () => {
    vi.mocked(api.getMapConfig).mockResolvedValue({ enabled: true, provider: "amap", js_api_key: null, version: "2.0", service_host_path: "/_AMapService" });
    render(<AmapPlanMap plan={plan} activeLegIndex={null} onSelectRoute={vi.fn()} />);

    expect(await screen.findByText("高德地图浏览器配置不完整")).toBeInTheDocument();
  });
});
