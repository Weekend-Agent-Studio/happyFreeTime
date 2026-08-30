import { expect, test } from "@playwright/test";
import path from "node:path";

const source = {
  source_name: "北京周末目录",
  source_uri: "https://example.test/catalog",
  source_license: "CC BY 4.0",
  collected_at: "2026-08-30T05:00:00Z",
  last_verified_at: "2026-08-30T05:00:00Z",
  verification_status: "verified",
};

const planSeeds = [
  { id: "balanced", title: "首都博物馆 · 功德林", strategy: "balanced", price: 168, distance: 4.1, highlight: "室内景点为主，降雨时体验更稳定。", tradeoff: "晚餐座位仍需在出发前确认。" },
  { id: "walk", title: "日坛公园 · Bleu Marine", strategy: "low_travel", price: 210, distance: 2.8, highlight: "站点集中，步行和通勤负担更轻。", tradeoff: "16:30 后可能有阵雨。" },
  { id: "budget", title: "北京警察博物馆 · 老石水饺", strategy: "low_cost", price: 92, distance: 3.6, highlight: "预算更低，仍保留完整活动与晚餐。", tradeoff: "返程路线当前采用估算。" },
];

const plans = planSeeds.map((seed, planIndex) => ({
  plan_id: seed.id,
  composition_fingerprint: seed.id,
  skeleton_id: "visual-capture",
  title: seed.title,
  strategy: seed.strategy,
  total_score: 90 - planIndex,
  total_price: seed.price,
  price_status: "known",
  total_duration_minutes: 210 - planIndex * 15,
  stops: [
    {
      resource_id: `${seed.id}-activity`,
      type: "activity",
      role: "activity",
      name: seed.title.split(" · ")[0],
      start: "14:20",
      end: "16:10",
      duration_minutes: 110,
      price: planIndex === 2 ? 0 : 68,
      price_kind: planIndex === 2 ? "free" : "known",
      category_tags: [planIndex === 1 ? "公园" : "博物馆", "适合半日"],
      image: null,
      source,
      rating: planIndex === 1 ? 4.5 : 4.7,
      rating_count: 1280 - planIndex * 230,
      address: "北京市朝阳区示例路 18 号",
      opening_hours: "09:00–17:00（16:00 停止入场）",
      description: "当前为离线验收数据，用于确认丰富 POI 字段的排版与缺失回退。",
    },
    {
      resource_id: `${seed.id}-meal`,
      type: "restaurant",
      role: "meal",
      name: seed.title.split(" · ")[1],
      start: "16:30",
      end: "17:30",
      duration_minutes: 60,
      price: seed.price - (planIndex === 2 ? 0 : 68),
      price_kind: "known",
      category_tags: ["晚餐", planIndex === 2 ? "家常菜" : "安静"],
      image: null,
      source,
      rating: 4.4,
      rating_count: 680,
      address: "北京市朝阳区示例街 6 号",
      opening_hours: "11:00–21:30",
    },
  ],
  route_legs: [
    { origin_name: "出发地", destination_name: seed.title.split(" · ")[0], start: "14:00", end: "14:20", mode: "taxi", distance_km: seed.distance * .35, duration_minutes: 20, source: planIndex === 2 ? "local_estimate" : "real_provider", provider_mode: planIndex === 2 ? "mock" : "live", verified_at: "2026-08-30T05:00:00Z", cache_age_seconds: null, geometry: [], degraded: planIndex === 2, degraded_reason: planIndex === 2 ? "离线验收" : null },
    { origin_name: seed.title.split(" · ")[0], destination_name: seed.title.split(" · ")[1], start: "16:10", end: "16:30", mode: planIndex === 1 ? "walking" : "taxi", distance_km: seed.distance * .25, duration_minutes: 20, source: "real_provider", provider_mode: "live", verified_at: "2026-08-30T05:00:00Z", cache_age_seconds: null, geometry: [], degraded: false, degraded_reason: null },
    { origin_name: seed.title.split(" · ")[1], destination_name: "出发地", start: "17:30", end: "17:48", mode: "taxi", distance_km: seed.distance * .4, duration_minutes: 18, source: planIndex === 2 ? "local_estimate" : "real_provider", provider_mode: planIndex === 2 ? "mock" : "live", verified_at: "2026-08-30T05:00:00Z", cache_age_seconds: null, geometry: [], degraded: planIndex === 2, degraded_reason: planIndex === 2 ? "离线验收" : null },
  ],
  score_breakdown: [],
  highlights: [seed.highlight, "返程时间留有余量。"],
  tradeoffs: [seed.tradeoff],
}));

const response = {
  status: "completed",
  reply: "好的，已为你规划适合半日出行的方案，兼顾景点质量与往返时间。",
  question: null,
  assumptions: [],
  constraint_summary: [
    { field: "return_by", value: "18:00", source: "user_explicit", evidence: "18:00 前回家", confidence: 1, rule_id: null, user_editable: true },
    { field: "budget_per_person", value: 220, source: "user_inferred", evidence: "别太贵", confidence: .82, rule_id: "budget-default", user_editable: true },
  ],
  conflict: null,
  provider_facts: [{ kind: "weather", city: "北京市", district: "朝阳区", date: "2026-08-30", condition: "多云转阵雨", temperature_c: 26, precipitation_mm: 2.3, is_adverse: true, cache_age_seconds: 120, source: "mock", mode: "mock", observed_at: "2026-08-30T05:00:00Z", verified_at: "2026-08-30T05:00:00Z", degraded: false, degraded_reason: null }],
  catalog_violations: [],
  catalog_warnings: [],
  poi_presentations: plans.flatMap((plan) => plan.stops.map((stop) => ({
    resource_id: stop.resource_id, name: stop.name, category_label: stop.category_tags[0], business_area: "朝阳区", address: "北京市朝阳区示例路 18 号",
    description: "当前为离线验收数据，用于确认完整 POI 详情的排版与数据模式说明。", scene_tags: ["慢慢逛"], facility_tags: ["休息区"], indoor: stop.type !== "activity" || stop.category_tags[0] !== "公园",
    weather_suitability: "室内为主，阴雨天也适合", child_suitability: "适合亲子同行（演示规则）", reference_avg_price: stop.price,
    demo_rating: 4.6, demo_review_count: 1280, opening_hours_display: "周一至周日 09:00–21:30", opening_status: "已纳入本次营业时间校验", suggested_duration_minutes: stop.duration_minutes,
    reservation_requirement: "无需预约", queue_profile: "周末下午可能有客流", risk_tips: ["演示信息，请以实际安排为准"], booking_mode: "现场到访",
    gallery: [{ url: stop.type === "restaurant" ? "/demo-illustrations/dining.svg" : "/demo-illustrations/outing.svg", alt: "场景示意图（非门店实拍）", kind: "illustrative", attribution: "test", license: "test" }],
    data_notice: "演示环境：POI 商业信息为模拟数据，地图与路线来自高德。",
  }))),
  warnings: [{ code: "availability_unconfirmed", message: "晚餐座位需在出发前再次确认。", plan_id: "balanced", resource_id: "balanced-meal", route_leg_index: null, source: "mock", degraded: false, stale: false }],
  plans,
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/sessions", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { data: { sessions: [0, 1, 2, 3, 4].map((index) => ({ session_id: `history-${index}`, title: ["今天下午想出去走走，别太远", "周末一日亲子游推荐", "北京小众博物馆", "下周六露营地推荐", "带老人轻松半日游"][index], status: "completed", created_at: "2026-08-29T04:00:00Z", updated_at: `2026-08-${29 - index}T05:00:00Z`, last_message_preview: "已生成可执行方案" })) } } });
      return;
    }
    await route.fulfill({ json: { data: { session_id: "visual-session" } } });
  });
  await page.route("**/api/sessions/visual-session/messages", (route) => route.fulfill({ json: { data: response } }));
  await page.route("**/api/config/map", (route) => route.fulfill({ json: { data: { enabled: false, provider: "none", js_api_key: null, version: null, service_host_path: null } } }));
});

test("captures the approved desktop and mobile product states", async ({ page }, testInfo) => {
  const mobile = testInfo.project.name === "mobile-375";
  await page.setViewportSize(mobile ? { width: 375, height: 812 } : { width: 1632, height: 963 });
  await page.goto("/");
  await page.getByLabel("描述你的空闲时间和偏好").fill("今天下午想出去走走，别太远，18:00 前回家");
  await page.getByRole("button", { name: "发送需求" }).click();
  await expect(page.getByRole("heading", { name: /首都博物馆/, level: 3 })).toBeVisible();
  await page.waitForTimeout(750);
  await expect(page.locator("body")).toHaveJSProperty("scrollWidth", await page.locator("body").evaluate((body) => body.clientWidth));
  const filename = mobile ? "mobile-repro.png" : "hero-repro.png";
  await page.screenshot({ path: path.resolve(process.cwd(), `../.impeccable/review/${filename}`), fullPage: false });
  if (mobile) {
    const openDetail = page.getByRole("button", { name: "打开完整行程与地图" });
    await openDetail.scrollIntoViewIfNeeded();
    await openDetail.click();
    await expect(page.getByRole("heading", { name: "方案工作区" })).toBeVisible();
    await page.screenshot({ path: path.resolve(process.cwd(), "../.impeccable/review/mobile-detail-repro.png"), fullPage: false });
  }
});
