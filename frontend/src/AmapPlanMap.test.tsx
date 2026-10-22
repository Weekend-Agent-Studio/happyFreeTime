import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AmapPlanMap } from "./AmapPlanMap";
import * as api from "./api";
import type { Plan } from "./types";

vi.mock("./api", () => ({ getMapConfig: vi.fn() }));

const source = { source_name: "测试目录", source_uri: "https://example.test", source_license: "test", collected_at: "2026-08-29T00:00:00Z", last_verified_at: null, verification_status: "verified" as const };
const point = (latitude: number, longitude: number) => ({ latitude, longitude });

const plan = {
  plan_id: "map-plan", composition_fingerprint: "map-plan", skeleton_id: null, title: "地图测试", strategy: "balanced", total_score: 1, total_price: 0, price_status: "known", total_duration_minutes: 60,
  stops: [
    { resource_id: "stop-1", type: "activity", role: "activity", name: "第一站", start: "09:00", end: "10:00", duration_minutes: 60, price: 0, price_kind: "free", category_tags: [], image: null, source },
    { resource_id: "stop-2", type: "activity", role: "activity", name: "第二站", start: "10:00", end: "11:00", duration_minutes: 60, price: 0, price_kind: "unknown", category_tags: [], image: null, source },
    { resource_id: "stop-3", type: "activity", role: "activity", name: "第三站", start: "11:00", end: "12:00", duration_minutes: 60, price: 0, price_kind: "unknown", category_tags: [], image: null, source },
  ],
  route_legs: [
    { origin_name: "出发地", destination_name: "第一站", start: "08:45", end: "09:00", mode: "walk", distance_km: 1, duration_minutes: 15, source: "amap_live", provider_mode: "live", verified_at: null, cache_age_seconds: null, geometry: [point(39.9, 116.3), point(39.91, 116.31)], degraded: false, degraded_reason: null },
    { origin_name: "第一站", destination_name: "第二站", start: "10:00", end: "10:20", mode: "walk", distance_km: 1, duration_minutes: 20, source: "local_estimate", provider_mode: "mock", verified_at: null, cache_age_seconds: null, geometry: [], degraded: true, degraded_reason: "坐标缺失" },
    { origin_name: "第二站", destination_name: "第三站", start: "11:00", end: "11:20", mode: "walk", distance_km: 1, duration_minutes: 20, source: "amap_live", provider_mode: "live", verified_at: null, cache_age_seconds: null, geometry: [point(39.91, 116.31), point(39.92, 116.32)], degraded: false, degraded_reason: null },
    { origin_name: "第三站", destination_name: "出发地", start: "12:00", end: "12:30", mode: "walk", distance_km: 2, duration_minutes: 30, source: "amap_live", provider_mode: "live", verified_at: null, cache_age_seconds: null, geometry: [point(39.92, 116.32), point(39.9, 116.3)], degraded: false, degraded_reason: null },
  ],
  score_breakdown: [], highlights: [], tradeoffs: [],
} as Plan;

type FakeOverlay = { options: Record<string, unknown>; listeners: Map<string, () => void>; on: (event: string, listener: () => void) => void; click: () => void };
const makeOverlay = (options: Record<string, unknown>): FakeOverlay => ({
  options,
  listeners: new Map(),
  on(event, listener) { this.listeners.set(event, listener); },
  click() { this.listeners.get("click")?.(); },
});

describe("AmapPlanMap", () => {
  afterEach(() => {
    vi.clearAllMocks();
    delete window.AMap;
  });

  it("keeps textual route fallback when the browser key is disabled", async () => {
    vi.mocked(api.getMapConfig).mockResolvedValue({ enabled: false, provider: "none", js_api_key: null, version: null, service_host_path: null });
    render(<AmapPlanMap plan={plan} activeLegIndex={null} onFocusRoute={vi.fn()} onOpenPoi={vi.fn()} />);

    expect(await screen.findByText("未配置高德 JS API，当前仅显示路线文字摘要")).toBeInTheDocument();
  });

  it("renders a visible provider configuration error instead of failing silently", async () => {
    vi.mocked(api.getMapConfig).mockResolvedValue({ enabled: true, provider: "amap", js_api_key: null, version: "2.0", service_host_path: "/_AMapService" });
    render(<AmapPlanMap plan={plan} activeLegIndex={null} onFocusRoute={vi.fn()} onOpenPoi={vi.fn()} />);

    expect(await screen.findByText("高德地图浏览器配置不完整")).toBeInTheDocument();
  });

  it("keeps raw leg and stop indices when a middle leg has no geometry, and separates map actions", async () => {
    vi.mocked(api.getMapConfig).mockResolvedValue({ enabled: true, provider: "amap", js_api_key: "test-key", version: "2.0", service_host_path: "/_AMapService" });
    const routeCallbacks = vi.fn();
    const poiCallbacks = vi.fn();
    const polylines: FakeOverlay[] = [];
    const markers: FakeOverlay[] = [];
    const maps: Array<{ destroy: ReturnType<typeof vi.fn>; setFitView: ReturnType<typeof vi.fn> }> = [];
    class FakeMap {
      destroy = vi.fn();
      setFitView = vi.fn();
      constructor(_container: HTMLElement, _options: Record<string, unknown>) { maps.push(this); }
    }
    class FakePolyline {
      instance = makeOverlay({});
      constructor(options: Record<string, unknown>) { this.instance.options = options; polylines.push(this.instance); }
      on(event: string, listener: () => void) { this.instance.on(event, listener); }
      setOptions(options: Record<string, unknown>) { Object.assign(this.instance.options, options); }
    }
    class FakeMarker {
      instance: FakeOverlay;
      constructor(options: Record<string, unknown>) { this.instance = makeOverlay(options); markers.push(this.instance); }
      on(event: string, listener: () => void) { this.instance.on(event, listener); }
    }
    window.AMap = { Map: FakeMap, Polyline: FakePolyline, Marker: FakeMarker } as unknown as NonNullable<typeof window.AMap>;

    const view = render(<AmapPlanMap plan={plan} activeLegIndex={2} onFocusRoute={routeCallbacks} onOpenPoi={poiCallbacks} />);
    await waitFor(() => expect(maps).toHaveLength(1));
    expect(polylines).toHaveLength(3);
    expect(maps[0].setFitView).toHaveBeenCalledTimes(1);
    polylines[1].click();
    expect(routeCallbacks).toHaveBeenCalledWith(2);
    expect(poiCallbacks).not.toHaveBeenCalled();

    const markerContent = markers.map((marker) => String(marker.options.content));
    expect(markerContent).toEqual(expect.arrayContaining([
      expect.stringContaining(">出</div>"),
      expect.stringContaining(">1</div>"),
      expect.stringContaining(">2</div>"),
      expect.stringContaining(">3</div>"),
      expect.stringContaining(">返</div>"),
    ]));
    markers.find((marker) => marker.options.title === "第二站")?.click();
    expect(poiCallbacks).toHaveBeenCalledWith(1);
    markers.find((marker) => marker.options.title === "出发地")?.click();
    markers.find((marker) => marker.options.title === "返程终点：出发地")?.click();
    expect(poiCallbacks).toHaveBeenCalledTimes(1);

    const latestRouteCallback = vi.fn();
    const latestPoiCallback = vi.fn();
    view.rerender(<AmapPlanMap plan={plan} activeLegIndex={0} onFocusRoute={latestRouteCallback} onOpenPoi={latestPoiCallback} />);
    await waitFor(() => expect(polylines[0].options.strokeWeight).toBe(9));
    expect(polylines[1].options.strokeWeight).toBe(6);
    expect(maps).toHaveLength(1);
    expect(maps[0].destroy).not.toHaveBeenCalled();
    expect(maps[0].setFitView).toHaveBeenCalledTimes(1);
    polylines[1].click();
    markers.find((marker) => marker.options.title === "第二站")?.click();
    expect(latestRouteCallback).toHaveBeenCalledWith(2);
    expect(latestPoiCallback).toHaveBeenCalledWith(1);
    expect(routeCallbacks).toHaveBeenCalledTimes(1);
    expect(poiCallbacks).toHaveBeenCalledTimes(1);

    view.unmount();
    expect(maps[0].destroy).toHaveBeenCalledTimes(1);
  });

  it("does not load the map SDK when the response has no drawable geometry", async () => {
    const noGeometry = { ...plan, route_legs: plan.route_legs.map((leg) => ({ ...leg, geometry: [] })) };
    render(<AmapPlanMap plan={noGeometry} activeLegIndex={null} onFocusRoute={vi.fn()} onOpenPoi={vi.fn()} />);

    expect(await screen.findByText("当前响应没有可绘制的有效路线坐标；可从下方站点列表查看 POI 与文字路线")).toBeInTheDocument();
    expect(api.getMapConfig).not.toHaveBeenCalled();
  });
});
