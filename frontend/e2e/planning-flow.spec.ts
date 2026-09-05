import { expect, test } from "@playwright/test";

const response = {
  status: "completed",
  reply: "已生成方案",
  question: null,
  assumptions: [],
  constraint_summary: [],
  conflict: null,
  provider_facts: [],
  catalog_violations: [],
  catalog_warnings: [],
  poi_presentations: ["0", "1"].flatMap((planIndex) => [
    { resource_id: `activity-${planIndex}`, name: "展览", category_label: "博物馆", business_area: "朝阳区", address: "测试路 1 号", description: "一段完整的 POI 演示详情。", scene_tags: ["慢慢逛"], facility_tags: ["休息区"], indoor: true, weather_suitability: "室内为主，阴雨天也适合", child_suitability: "适合亲子同行（演示规则）", reference_avg_price: 30, demo_rating: 4.6, demo_review_count: 1280, opening_hours_display: "周一至周日 09:30–17:30", opening_status: "已纳入本次营业时间校验", suggested_duration_minutes: 60, reservation_requirement: "无需预约", queue_profile: "周末下午可能有客流", risk_tips: ["闭馆前请预留入场时间"], booking_mode: "现场入场", gallery: [{ url: "/demo-illustrations/outing.svg", alt: "展览场景示意图（非门店实拍）", kind: "illustrative", attribution: "test", license: "test" }], data_notice: "演示环境：POI 商业信息为模拟数据，地图与路线来自高德。" },
    { resource_id: `meal-${planIndex}`, name: "晚餐", category_label: "餐厅", business_area: "朝阳区", address: "测试路 2 号", description: "一段完整的餐饮 POI 演示详情。", scene_tags: ["朋友聚餐"], facility_tags: ["可堂食"], indoor: true, weather_suitability: "室内为主，阴雨天也适合", child_suitability: "适合亲子同行（演示规则）", reference_avg_price: 58, demo_rating: 4.4, demo_review_count: 860, opening_hours_display: "周一至周日 10:30–22:30", opening_status: "已纳入本次营业时间校验", suggested_duration_minutes: 60, reservation_requirement: "无需预约", queue_profile: "高峰时段建议错峰", risk_tips: ["餐饮商业信息为演示数据"], booking_mode: "到店", gallery: [{ url: "/demo-illustrations/dining.svg", alt: "餐饮场景示意图（非门店实拍）", kind: "illustrative", attribution: "test", license: "test" }], data_notice: "演示环境：POI 商业信息为模拟数据，地图与路线来自高德。" },
  ]),
  plans: ["方案一", "方案二"].map((title, planIndex) => ({
    plan_id: `plan-${planIndex}`,
    composition_fingerprint: `plan-${planIndex}`,
    skeleton_id: "e2e",
    title,
    strategy: "balanced",
    total_score: 10,
    total_price: 88,
    price_status: "known",
    total_duration_minutes: 180,
    stops: [
      { resource_id: `activity-${planIndex}`, type: "activity", role: "activity", name: "展览", start: "14:10", end: "15:10", duration_minutes: 60, price: 30, price_kind: "known", category_tags: ["展览"], image: null, source: { source_name: "测试目录", source_uri: "https://example.test", source_license: "test", collected_at: "2026-08-29T00:00:00Z", last_verified_at: null, verification_status: "verified" } },
      { resource_id: `meal-${planIndex}`, type: "restaurant", role: "meal", name: "晚餐", start: "15:20", end: "16:20", duration_minutes: 60, price: 58, price_kind: "known", category_tags: ["餐厅"], image: null, source: { source_name: "测试目录", source_uri: "https://example.test", source_license: "test", collected_at: "2026-08-29T00:00:00Z", last_verified_at: null, verification_status: "verified" } },
    ],
    route_legs: [
      { origin_name: "出发地", destination_name: "展览", start: "14:00", end: "14:10", mode: "taxi", distance_km: 1, duration_minutes: 10, source: "local_estimate", provider_mode: "mock", verified_at: null, cache_age_seconds: null, geometry: [], degraded: true, degraded_reason: "offline" },
      { origin_name: "展览", destination_name: "晚餐", start: "15:10", end: "15:20", mode: "taxi", distance_km: 2, duration_minutes: 10, source: "local_estimate", provider_mode: "mock", verified_at: null, cache_age_seconds: null, geometry: [], degraded: true, degraded_reason: "offline" },
      { origin_name: "晚餐", destination_name: "出发地", start: "16:20", end: "16:35", mode: "taxi", distance_km: 2, duration_minutes: 15, source: "local_estimate", provider_mode: "mock", verified_at: null, cache_age_seconds: null, geometry: [], degraded: true, degraded_reason: "offline" },
    ],
    score_breakdown: [], highlights: [], tradeoffs: [],
  })),
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/sessions", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { data: { sessions: [] } } });
      return;
    }
    await route.fulfill({ json: { data: { session_id: "offline-session" } } });
  });
  await page.route("**/api/sessions/offline-session/messages", (route) => route.fulfill({ json: { data: response } }));
  await page.route("**/api/config/map", (route) => route.fulfill({ json: { data: { enabled: false, provider: "none", js_api_key: null, version: null, service_host_path: null } } }));
});

test("offline planning path keeps route and fallback map interactive", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("描述你的空闲时间和偏好").fill("今天下午出去玩");
  await page.getByRole("button", { name: "发送需求" }).click();
  await expect(page.getByRole("heading", { name: "方案一", level: 3 })).toBeVisible();

  await page.getByRole("button", { name: /方案二/ }).click();
  await page.getByRole("button", { name: /查看出发路线：出发地 → 展览/ }).click();
  await expect(page.getByText("未配置高德 JS API，当前仅显示路线文字摘要")).toBeVisible();
  await page.getByRole("button", { name: /展览 → 晚餐/ }).click();
  await expect(page.getByRole("button", { name: /查看下一程：2km/ })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: /查看返程/ })).toHaveCount(1);
});

test("POI details keep demo data disclosure on desktop and 375px", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");
  await page.getByLabel("描述你的空闲时间和偏好").fill("今天下午出去玩");
  await page.getByRole("button", { name: "发送需求" }).click();
  await page.locator(".poi-disclosure summary").first().click();
  await expect(page.getByText("演示评分").first()).toBeVisible();
  await expect(page.getByText("测试路 1 号").first()).toBeVisible();
  await expect(page.getByText("地图定位").first()).toBeVisible();
  await expect(page.getByText("演示环境：POI 商业信息为模拟数据，地图与路线来自高德。").first()).toBeVisible();
});

test("desktop keeps the workspace chrome fixed while the conversation scrolls", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-chromium", "desktop workspace contract");
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page.getByLabel("描述你的空闲时间和偏好").fill("今天下午出去玩");
  await page.getByRole("button", { name: "发送需求" }).click();
  await expect(page.getByRole("heading", { name: "方案一", level: 3 })).toBeVisible();

  const conversation = page.locator(".conversation");
  const navigation = page.locator(".session-sidebar");
  const composer = page.locator(".composer-dock");
  const inspector = page.locator(".detail-panel");
  const before = {
    navigation: await navigation.boundingBox(),
    composer: await composer.boundingBox(),
    inspector: await inspector.boundingBox(),
  };
  await expect.poll(() => conversation.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
  await conversation.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  const after = {
    navigation: await navigation.boundingBox(),
    composer: await composer.boundingBox(),
    inspector: await inspector.boundingBox(),
  };

  expect(await page.evaluate(() => window.scrollY)).toBe(0);
  expect(after.navigation?.y).toBeCloseTo(before.navigation?.y ?? 0, 0);
  expect(after.composer?.y).toBeCloseTo(before.composer?.y ?? 0, 0);
  expect(after.inspector?.y).toBeCloseTo(before.inspector?.y ?? 0, 0);
});
