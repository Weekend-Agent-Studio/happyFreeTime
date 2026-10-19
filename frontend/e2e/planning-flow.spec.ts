import { expect, test } from "@playwright/test";
import path from "node:path";

const response = {
  status: "completed",
  reply: "已生成方案",
  plan_version_id: "v1",
  planning_context: {
    request_revision: 1,
    planned_request_revision: 1,
    has_active_plan: true,
    plan_stale: false,
    ready_for_planning: true,
    where: { value: { city: "北京市", district: "朝阳区", address: "北京市朝阳区", latitude: 39.9, longitude: 116.4 }, display_value: "北京市朝阳区", source: "default", editable: true, status: "assumed" },
    when: {
      date: { value: "2026-08-15", display_value: "2026-08-15", source: "user", editable: true, status: "resolved" },
      start_at: { value: "14:00", display_value: "14:00", source: "derived", editable: true, status: "resolved" },
      end_at: { value: "18:00", display_value: "18:00", source: "derived", editable: true, status: "resolved" },
      start_kind: "trip_start",
      end_kind: "trip_end",
    },
    who: { value: { adults: 1, children: 0, child_age: null, members: [] }, display_value: "1 位成人 · 0 位儿童", source: "default", editable: true, status: "assumed" },
    budget: { value: { mode: "unlimited", amount: null, strict: false }, display_value: "不限", source: "default", editable: true, status: "assumed" },
    preferences: { value: { preferences: [], diet_tags: [], scene_tags: [], avoid: [] }, display_value: "未设置", source: "default", editable: true, status: "assumed" },
    exact_stop_count: { value: null, display_value: "未设置", source: "default", editable: true, status: "assumed" },
    max_distance_km: { value: null, display_value: "未设置", source: "default", editable: true, status: "assumed" },
    total_distance_km: { value: null, display_value: "未设置", source: "default", editable: true, status: "assumed" },
    pending_field: null,
  },
  question: null,
  assumptions: [],
  constraint_summary: [],
  conflict: null,
  provider_facts: [],
  catalog_violations: [],
  catalog_warnings: [],
  poi_presentations: ["0", "1"].flatMap((planIndex) => [
    { resource_id: `activity-${planIndex}`, name: "展览", category_label: "博物馆", business_area: "朝阳区", address: "测试路 1 号", description: "一段完整的 POI 演示详情。", scene_tags: ["慢慢逛"], facility_tags: ["休息区"], indoor: true, weather_suitability: "室内为主，阴雨天也适合", child_suitability: "适合亲子同行（演示规则）", reference_avg_price: 30, demo_rating: 4.6, demo_review_count: 1280, opening_hours_display: "周一至周日 09:30–17:30", opening_status: "已纳入本次营业时间校验", suggested_duration_minutes: 60, reservation_requirement: "无需预约", queue_profile: "周末下午可能有客流", risk_tips: ["闭馆前请预留入场时间"], booking_mode: "现场入场", gallery: [{ url: "/demo-illustrations/outing.svg", alt: "展览场景示意图（非门店实拍）", kind: "illustrative", attribution: "test", license: "test" }], data_notice: "演示环境：POI 商业信息为模拟数据；路线来源和降级状态见各路线段。" },
    { resource_id: `meal-${planIndex}`, name: "晚餐", category_label: "餐厅", business_area: "朝阳区", address: "测试路 2 号", description: "一段完整的餐饮 POI 演示详情。", scene_tags: ["朋友聚餐"], facility_tags: ["可堂食"], indoor: true, weather_suitability: "室内为主，阴雨天也适合", child_suitability: "适合亲子同行（演示规则）", reference_avg_price: 58, demo_rating: 4.4, demo_review_count: 860, opening_hours_display: "周一至周日 10:30–22:30", opening_status: "已纳入本次营业时间校验", suggested_duration_minutes: 60, reservation_requirement: "无需预约", queue_profile: "高峰时段建议错峰", risk_tips: ["餐饮商业信息为演示数据"], booking_mode: "到店", gallery: [{ url: "/demo-illustrations/dining.svg", alt: "餐饮场景示意图（非门店实拍）", kind: "illustrative", attribution: "test", license: "test" }], data_notice: "演示环境：POI 商业信息为模拟数据；路线来源和降级状态见各路线段。" },
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

function streamResponse(payload: unknown) {
  const progress = {
    schema_version: "planning-run-event.v1",
    run_id: "e2e-run",
    sequence: 1,
    stage: "understand",
    status: "completed",
    message_key: "understand.completed",
    public_message: "需求理解完成",
    occurred_at: "2026-08-15T00:00:00Z",
    duration_ms: 1,
    public_details: {},
  };
  const body = [
    `event: progress\ndata: ${JSON.stringify(progress)}\n\n`,
    `event: result\ndata: ${JSON.stringify({ data: payload })}\n\n`,
  ].join("");
  return { status: 200, headers: { "content-type": "text/event-stream" }, body };
}

test.beforeEach(async ({ page }) => {
  await page.route(/\/api\/sessions(?:\?[^/]*)?$/, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { data: { sessions: [] } } });
      return;
    }
    await route.fulfill({ json: { data: { session_id: "offline-session" } } });
  });
  await page.route("**/api/sessions/offline-session/messages/stream", (route) => route.fulfill(streamResponse(response)));
  await page.route("**/api/config/map", (route) => route.fulfill({ json: { data: { enabled: false, provider: "none", js_api_key: null, version: null, service_host_path: null } } }));
});

test("offline planning path keeps route and fallback map interactive", async ({ page }, testInfo) => {
  const mobile = testInfo.project.name === "mobile-375";
  await page.goto("/");
  await page.getByLabel("描述你的空闲时间和偏好").fill("今天下午出去玩");
  await page.getByRole("button", { name: "发送需求" }).click();
  await expect(page.getByRole("heading", { name: "方案一", level: 3 })).toBeVisible();

  await page.getByRole("button", { name: "预览方案二" }).click();
  if (mobile) await page.getByRole("button", { name: "打开方案二详情" }).click();
  await page.getByRole("button", { name: /查看出发路线：出发地 → 展览/ }).click();
  await expect(page.getByText("当前响应没有可绘制的有效路线坐标；可从下方站点列表查看 POI 与文字路线")).toBeVisible();
  await page.getByRole("button", { name: "聚焦第 2 段路线：展览 到 晚餐" }).click();
  await expect(page.getByRole("tab", { name: "地图" })).toHaveAttribute("aria-selected", "true");
  const scrollHost = page.locator(mobile ? ".sheet-content" : ".detail-panel");
  const firstRouteTimelineAction = page.getByRole("button", { name: "在行程中定位" }).first();
  await firstRouteTimelineAction.scrollIntoViewIfNeeded();
  const mapScrollBefore = await scrollHost.evaluate((element) => element.scrollTop);
  await firstRouteTimelineAction.click();
  await expect(page.getByRole("tab", { name: "行程" })).toHaveAttribute("aria-selected", "true");
  const focusedTimelineRoute = page.locator('.timeline-route[data-route-leg-index="0"]');
  await expect(focusedTimelineRoute).toHaveClass(/route-focused/);
  await expect.poll(() => focusedTimelineRoute.evaluate((element) => {
    const host = element.closest<HTMLElement>(".detail-panel, .sheet-content");
    if (!host) return false;
    const hostRect = host.getBoundingClientRect();
    const targetRect = element.getBoundingClientRect();
    const stickyBottom = host.querySelector<HTMLElement>(".inspector-sticky-header")?.getBoundingClientRect().bottom ?? hostRect.top;
    return targetRect.top >= Math.max(hostRect.top + host.clientTop, stickyBottom) && targetRect.bottom <= hostRect.bottom - host.clientTop;
  })).toBe(true);
  await expect.poll(() => scrollHost.evaluate((element) => element.scrollTop)).not.toBe(mapScrollBefore);
  await expect(page.getByRole("button", { name: /查看返程/ })).toHaveCount(1);
});

test("POI subpage keeps demo data boundaries on 375px", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");
  await page.getByLabel("描述你的空闲时间和偏好").fill("今天下午出去玩");
  await page.getByRole("button", { name: "发送需求" }).click();
  await page.getByRole("button", { name: "打开方案一详情" }).click();
  await page.getByRole("button", { name: "查看第 1 站 展览 详情" }).click();
  await expect(page.getByRole("heading", { name: "展览", level: 2 })).toBeVisible();
  await expect(page.getByText("测试路 1 号").first()).toBeVisible();
  await expect(page.getByRole("link", { name: "在高德地图中搜索" })).toHaveAttribute("rel", "noopener noreferrer");
  await expect(page.getByText(/演示评分|演示评论/)).toHaveCount(0);
  await page.locator(".poi-data-disclosure summary").click();
  await expect(page.locator(".poi-data-disclosure p")).toContainText("POI 商业信息为模拟数据");
});

test("POI return restores real scroll, originating tab, and focus", async ({ page }, testInfo) => {
  const mobile = testInfo.project.name === "mobile-375";
  await page.setViewportSize(mobile ? { width: 375, height: 812 } : { width: 1440, height: 900 });
  await page.goto("/");
  await page.getByLabel("描述你的空闲时间和偏好").fill("今天下午出去玩");
  await page.getByRole("button", { name: "发送需求" }).click();
  if (mobile) await page.getByRole("button", { name: "打开方案一详情" }).click();
  const scrollHost = page.locator(mobile ? ".sheet-content" : ".detail-panel");
  const secondTimelineEntry = page.getByRole("button", { name: "查看第 2 站 晚餐 详情" });
  await secondTimelineEntry.scrollIntoViewIfNeeded();
  const tripScrollBefore = await scrollHost.evaluate((element) => element.scrollTop);
  expect(tripScrollBefore).toBeGreaterThan(0);
  await secondTimelineEntry.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "晚餐", level: 2 })).toBeVisible();
  await page.getByRole("button", { name: "返回行程" }).click();
  await expect(page.getByRole("tab", { name: "行程" })).toHaveAttribute("aria-selected", "true");
  await expect.poll(() => scrollHost.evaluate((element) => element.scrollTop)).toBe(tripScrollBefore);
  await expect(page.locator(`[data-inspector-stop-index="1"].timeline-stop-open`)).toBeFocused();

  await page.getByRole("tab", { name: "地图" }).click();
  const secondMapEntry = page.locator(".map-stop-row[data-inspector-stop-index='1']");
  await secondMapEntry.scrollIntoViewIfNeeded();
  const mapScrollBefore = await scrollHost.evaluate((element) => element.scrollTop);
  expect(mapScrollBefore).toBeGreaterThan(0);
  await secondMapEntry.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "晚餐", level: 2 })).toBeVisible();
  await page.getByRole("button", { name: "返回地图" }).click();
  await expect(page.getByRole("tab", { name: "地图" })).toHaveAttribute("aria-selected", "true");
  await expect.poll(() => scrollHost.evaluate((element) => element.scrollTop)).toBe(mapScrollBefore);
  await expect(page.locator(".map-stop-row[data-inspector-stop-index='1']")).toBeFocused();

  if (mobile) {
    await page.locator(".map-stop-row[data-inspector-stop-index='1']").focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "晚餐", level: 2 })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "方案工作区" })).toBeHidden();
    await page.getByRole("button", { name: "打开方案一详情" }).click();
    await expect(page.getByRole("tab", { name: "行程" })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("heading", { name: "晚餐", level: 2 })).toHaveCount(0);
  }
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
    heading: await page.locator(".conversation-heading.compact").boundingBox(),
    conditions: await page.locator(".app-header").boundingBox(),
    navigation: await navigation.boundingBox(),
    composer: await composer.boundingBox(),
    inspector: await inspector.boundingBox(),
  };
  await expect.poll(() => conversation.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
  await conversation.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  const after = {
    heading: await page.locator(".conversation-heading.compact").boundingBox(),
    conditions: await page.locator(".app-header").boundingBox(),
    navigation: await navigation.boundingBox(),
    composer: await composer.boundingBox(),
    inspector: await inspector.boundingBox(),
  };

  expect(await page.evaluate(() => window.scrollY)).toBe(0);
  expect(after.navigation?.y).toBeCloseTo(before.navigation?.y ?? 0, 0);
  expect(after.composer?.y).toBeCloseTo(before.composer?.y ?? 0, 0);
  expect(after.inspector?.y).toBeCloseTo(before.inspector?.y ?? 0, 0);
  expect(after.heading?.y).toBeCloseTo(before.heading?.y ?? 0, 0);
  expect(after.heading?.height).toBe(44);
  expect(after.conditions?.height).toBe(before.conditions?.height);
  expect(after.heading!.y).toBeGreaterThanOrEqual(after.conditions!.y + after.conditions!.height);
});

test("shell keeps equal desktop panes and a keyboard-accessible persistent navigation rail", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-chromium", "desktop navigation contract");
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "给周末一点好心情" })).toBeVisible();
  await expect(page.getByText("YOUR WEEKEND, YOUR WAY")).toBeVisible();
  await expect(page.getByText("轻松规划，放心选择。")).toBeVisible();
  const nav = page.locator(".session-sidebar");
  const workspace = page.locator(".workspace");
  const inspector = page.locator(".detail-panel");
  const conditions = page.getByRole("navigation", { name: "规划条件" }).getByRole("button");
  await expect(conditions).toHaveCount(5);
  expect(await conditions.evaluateAll((nodes) => nodes.map((node) => node.getAttribute("aria-label")?.split("，")[0]))).toEqual([
    "修改日期时间", "修改出发区域", "修改同行人数", "修改人均预算", "修改出行偏好",
  ]);
  expect((await nav.boundingBox())!.width).toBe(220);
  expect((await workspace.boundingBox())!.width).toBeCloseTo((await inspector.boundingBox())!.width, 0);
  await page.screenshot({ path: path.resolve("../.impeccable/review/s-ui1b-welcome-desktop.png") });
  const collapse = page.getByRole("button", { name: "收起侧边栏" });
  await collapse.focus();
  await collapse.press("Enter");
  const expand = page.getByRole("button", { name: "展开侧边栏" });
  await expect(expand).toBeFocused();
  await expect.poll(async () => Math.round((await nav.boundingBox())!.width)).toBe(64);
  await page.screenshot({ path: path.resolve("../.impeccable/review/s-ui1b-collapsed-desktop.png") });
  await page.reload();
  await expect(page.locator(".app-shell")).toHaveClass(/sidebar-collapsed/);
  await page.getByRole("button", { name: "展开侧边栏" }).focus();
  await page.getByRole("button", { name: "展开侧边栏" }).press("Enter");
  await expect(page.getByRole("button", { name: "收起侧边栏" })).toBeFocused();
  await expect.poll(async () => Math.round((await nav.boundingBox())!.width)).toBe(220);
  await page.getByLabel("描述你的空闲时间和偏好").fill("今天下午出去玩");
  await page.getByRole("button", { name: "发送需求" }).click();
  await expect(page.locator(".conversation-heading.compact")).toBeVisible();
  await expect(page.getByText("YOUR WEEKEND, YOUR WAY")).toHaveCount(0);
  await page.screenshot({ path: path.resolve("../.impeccable/review/s-ui1b-chat-desktop.png") });
});

test("narrow shell keeps the composer visible and history sheet keyboard-dismissible", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "发送需求" })).toBeInViewport();
  await expect(page.getByRole("heading", { name: "给周末一点好心情" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(375);
  await page.screenshot({ path: path.resolve("../.impeccable/review/s-ui1b-welcome-mobile.png") });
  const history = page.getByRole("button", { name: "打开历史会话" });
  await history.click();
  await expect(page.getByRole("dialog", { name: "历史会话" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(history).toBeFocused();
  const chips = page.getByRole("navigation", { name: "规划条件" });
  await chips.evaluate((node) => { node.scrollLeft = node.scrollWidth; });
  await expect(chips.getByRole("button", { name: /修改出行偏好/ })).toBeInViewport();
});

test("planning condition dialog fits desktop and 375px layouts and restores focus without saving on dismiss", async ({ page }, testInfo) => {
  const mobile = testInfo.project.name === "mobile-375";
  await page.setViewportSize(mobile ? { width: 375, height: 812 } : { width: 1440, height: 900 });
  await page.goto("/");
  const trigger = page.getByRole("button", { name: /修改日期时间/ });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "日期时间", exact: true });
  await expect(dialog).toBeVisible();
  const bounds = await dialog.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(mobile ? 375 : 1440);
  expect(bounds!.y).toBeGreaterThanOrEqual(0);
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(900);

  await page.keyboard.press("Tab");
  await expect(dialog.locator(":focus")).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();

  await trigger.click();
  await page.locator(".planning-context-backdrop").click({ position: { x: 2, y: 2 } });
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(trigger).toBeFocused();

  if (mobile) {
    await page.setViewportSize({ width: 375, height: 560 });
    await page.getByRole("button", { name: /修改出行偏好/ }).click();
    const preferenceDialog = page.getByRole("dialog", { name: "出行偏好", exact: true });
    const addInput = preferenceDialog.getByLabel("添加偏好");
    for (let index = 0; index < 20; index += 1) {
      await addInput.fill(`体验标签${index + 1}`);
      await preferenceDialog.getByRole("button", { name: "添加", exact: true }).click();
    }
    const scrollable = preferenceDialog.locator(".context-form-content");
    const scrollMetrics = await scrollable.evaluate((element) => ({ height: element.clientHeight, content: element.scrollHeight }));
    expect(scrollMetrics.content).toBeGreaterThan(scrollMetrics.height);
    await scrollable.evaluate((element) => { element.scrollTop = element.scrollHeight; });
    expect(await scrollable.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
    await expect(preferenceDialog.getByRole("button", { name: "保存条件" })).toBeInViewport();
  }
});

test("last history actions stay reachable and rename cancellation is unobstructed", async ({ page }, testInfo) => {
  const sessions = Array.from({ length: 20 }, (_, index) => ({
    session_id: `history-${index}`, title: `历史规划 ${index + 1}`, status: "completed",
    created_at: "2026-08-15T00:00:00Z", updated_at: "2026-08-15T00:00:00Z",
    last_message_preview: "一个较长的历史会话标题与方案摘要",
  }));
  await page.route(/\/api\/sessions(?:\?[^/]*)?$/, (route) => route.fulfill({ json: { data: { sessions } } }));
  await page.goto("/");
  const mobile = testInfo.project.name === "mobile-375";
  if (mobile) await page.getByRole("button", { name: "打开历史会话" }).click();
  const navigation = mobile ? page.getByRole("dialog", { name: "历史会话" }) : page.locator(".session-sidebar");
  await expect(navigation.locator(".session-row-shell")).toHaveCount(20);
  const last = navigation.locator(".session-row-shell").last();
  await last.getByRole("button", { name: "更多会话操作" }).focus();
  await last.getByRole("button", { name: "更多会话操作" }).click();
  const remove = last.getByRole("menuitem", { name: "删除规划" });
  await remove.scrollIntoViewIfNeeded();
  await expect(remove).toBeInViewport({ ratio: 1 });
  await last.getByRole("menuitem", { name: "重命名" }).click();
  await expect(last.getByLabel("规划名称")).toBeFocused();
  await expect(last.getByRole("button", { name: "更多会话操作" })).toHaveCount(0);
  const cancel = last.getByRole("button", { name: "取消重命名" });
  await cancel.scrollIntoViewIfNeeded();
  await expect(cancel).toBeInViewport({ ratio: 1 });
  await page.screenshot({ path: path.resolve(`../.impeccable/review/s-ui1b-rename-${testInfo.project.name}.png`) });
  await cancel.click();
  await expect(last.getByLabel("规划名称")).toHaveCount(0);
  await expect(last.getByRole("button", { name: "历史规划 20", exact: true })).toBeVisible();
});

test("recovery editor destinations retain visible section names", async ({ page }, testInfo) => {
  await page.route("**/api/sessions/offline-session/messages/stream", (route) => route.fulfill(streamResponse({
    ...response, status: "needs_recovery", plans: [], recovery_resolution: "open_constraint_editor",
    reply: "选择要修改的条件。", recovery: null,
  })));
  await page.goto("/");
  await page.getByLabel("描述你的空闲时间和偏好").fill("调整当前条件");
  await page.getByRole("button", { name: "发送需求" }).click();
  const dialog = page.getByRole("dialog", { name: "手动调整条件" });
  await expect(dialog).toBeVisible();
  const labels = dialog.locator(".recovery-editor-sections button > span");
  await expect(labels).toHaveText(["日期时间", "出发区域", "同行人数", "人均预算", "出行偏好", "更多条件"]);
  for (const label of await labels.all()) expect((await label.boundingBox())!.width).toBeGreaterThan(20);
  await page.screenshot({ path: path.resolve(`../.impeccable/review/s-ui1b-recovery-editor-${testInfo.project.name}.png`) });
  await dialog.getByRole("button", { name: /日期时间/ }).click();
  await expect(page.getByRole("dialog", { name: "日期时间", exact: true })).toBeVisible();
});

test("selection persists across refresh and clears after a new plan", async ({ page }, testInfo) => {
  const mobile = testInfo.project.name === "mobile-375";
  let selectedPlanId: string | null = null;

  await page.route("**/api/sessions/offline-session/plans/*/select", async (route) => {
    const segments = new URL(route.request().url()).pathname.split("/");
    const planId = segments[segments.length - 2];
    selectedPlanId = planId;
    await route.fulfill({ json: { data: { active_plan_version_id: "v1", selected_plan_id: planId } } });
  });
  await page.route("**/api/sessions/offline-session", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    await route.fulfill({ json: { data: {
      session_id: "offline-session",
      title: "新规划",
      status: "completed",
      created_at: "2026-08-20T00:00:00Z",
      updated_at: "2026-08-20T00:00:00Z",
      messages: [
        { id: "user-1", role: "user", content: "今天下午出去玩" },
        { id: "assistant-1", role: "assistant", content: "已生成方案" },
      ],
      plans: response.plans,
      latest_response: response,
      response_history: [response],
      active_plan_version_id: "v1",
      selected_plan_id: selectedPlanId,
    } } });
  });

  await page.goto("/");
  await page.getByLabel("描述你的空闲时间和偏好").fill("今天下午出去玩");
  await page.getByRole("button", { name: "发送需求" }).click();
  await expect(page.getByRole("heading", { name: "方案一", level: 3 })).toBeVisible();

  await page.locator(".plan-card").first().getByRole("button", { name: "打开方案一详情" }).click();
  await page.getByRole("button", { name: "选择此方案" }).click();
  await expect(page.getByRole("button", { name: "已选择此方案" })).toBeDisabled();
  expect(selectedPlanId).toBe("plan-0");

  if (mobile) await page.getByRole("button", { name: "关闭" }).click();
  await page.getByRole("button", { name: "预览方案二" }).click();
  if (mobile) await page.getByRole("button", { name: "打开方案二详情" }).click();
  await expect(page.locator(".inspector-plan-header")).toContainText("当前预览 · 未改变正式选择");
  await page.getByRole("button", { name: "选择此方案" }).click();
  const confirmation = page.getByRole("dialog", { name: "切换正式选择？" });
  await expect(confirmation).toBeVisible();
  const cancelSwitch = confirmation.getByRole("button", { name: "取消" });
  const confirmSwitch = confirmation.getByRole("button", { name: "确认切换" });
  await expect(cancelSwitch).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(confirmSwitch).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(cancelSwitch).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(confirmation).toHaveCount(0);
  await expect(page.getByRole("button", { name: "选择此方案" })).toBeFocused();
  expect(selectedPlanId).toBe("plan-0");

  await page.getByRole("button", { name: "选择此方案" }).click();
  await page.getByRole("dialog", { name: "切换正式选择？" }).getByRole("button", { name: "取消" }).click();
  expect(selectedPlanId).toBe("plan-0");

  await page.getByRole("button", { name: "选择此方案" }).click();
  await page.getByRole("dialog", { name: "切换正式选择？" }).getByRole("button", { name: "确认切换" }).click();
  await expect(page.getByRole("button", { name: "已选择此方案" })).toBeDisabled();
  expect(selectedPlanId).toBe("plan-1");

  await page.reload();
  await expect(page.locator(".plan-card.selected .plan-preview-button")).toHaveAttribute("aria-pressed", "true");

  await page.getByLabel("描述你的空闲时间和偏好").fill("再来一轮");
  await page.getByRole("button", { name: "发送需求" }).click();
  await expect(page.getByRole("button", { name: "已选择此方案" })).toHaveCount(0);
});

test("When edits save without replanning until the user confirms", async ({ page }) => {
  let revision = 1;
  let savedDate = "2026-08-15";
  let submittedPatch: Record<string, unknown> | null = null;
  let replanSubmitted = false;
  await page.route("**/api/sessions/offline-session/planning-context", async (route) => {
    submittedPatch = route.request().postDataJSON().patch;
    const patch = submittedPatch as { base_revision: number; when?: { date?: { operation: string; value?: string } } };
    revision = patch.base_revision + 1;
    savedDate = patch.when?.date?.value ?? savedDate;
    const nextContext = structuredClone(response.planning_context);
    nextContext.request_revision = revision;
    nextContext.planned_request_revision = 1;
    nextContext.plan_stale = true;
    nextContext.when.date.value = savedDate;
    nextContext.when.date.display_value = savedDate;
    await route.fulfill({ json: { data: { ...response, status: "context_saved", reply: "", plans: [], planning_context: nextContext } } });
  });
  await page.route("**/api/sessions/offline-session/planning-context/replan", async (route) => {
    replanSubmitted = true;
    const nextContext = structuredClone(response.planning_context);
    nextContext.request_revision = revision;
    nextContext.planned_request_revision = revision;
    nextContext.when.date.value = savedDate;
    nextContext.when.date.display_value = savedDate;
    await route.fulfill({ json: { data: { ...response, planning_context: nextContext } } });
  });

  await page.goto("/");
  await page.getByLabel("描述你的空闲时间和偏好").fill("今天下午出去玩");
  await page.getByRole("button", { name: "发送需求" }).click();
  await expect(page.getByRole("heading", { name: "方案一", level: 3 })).toBeVisible();

  await page.getByRole("button", { name: "修改日期时间" }).click();
  await page.getByLabel("日期", { exact: true }).fill("2026-08-16");
  await page.getByRole("button", { name: "保存条件" }).click();

  await expect.poll(() => submittedPatch).not.toBeNull();
  expect(submittedPatch).toMatchObject({
    base_revision: 1,
    when: { date: { operation: "set", value: "2026-08-16" } },
  });
  await expect(page.getByRole("button", { name: /日期时间.*2026-08-16/ })).toBeVisible();
  const notice = await page.locator(".planning-context-notice").boundingBox();
  const heading = await page.locator(".conversation-heading.compact").boundingBox();
  expect(notice!.y).toBeGreaterThanOrEqual(heading!.y + heading!.height);
  await expect(page.getByRole("heading", { name: "方案一", level: 3 })).toBeVisible();
  expect(replanSubmitted).toBe(false);
  await page.getByRole("button", { name: "按新条件重新规划" }).click();
  await expect.poll(() => replanSubmitted).toBe(true);
});

test("clarification card opens When and resolves through the same topbar request state", async ({ page }) => {
  const pendingResponse = structuredClone(response);
  pendingResponse.status = "needs_input";
  pendingResponse.reply = "";
  pendingResponse.plans = [];
  pendingResponse.question = {
    field: "departure_at",
    question: "早上大概几点出发？",
    severity: "blocking",
    request_revision: 4,
    clarification_id: "clarification-departure-1",
    options: [{ id: "use-default", label: "使用默认", action: "use_default" }],
  };
  pendingResponse.planning_context.request_revision = 4;
  pendingResponse.planning_context.pending_field = "departure_at";
  let submittedPatch: Record<string, unknown> | null = null;

  await page.route("**/api/sessions/offline-session/messages/stream", (route) =>
    route.fulfill(streamResponse(pendingResponse)),
  );
  await page.route("**/api/sessions/offline-session/planning-context", async (route) => {
    submittedPatch = route.request().postDataJSON().patch;
    const nextContext = structuredClone(response.planning_context);
    nextContext.request_revision = 5;
    nextContext.pending_field = null;
    nextContext.when.start_at = {
      value: "09:30", display_value: "09:30", source: "user", editable: true, status: "resolved",
    };
    nextContext.when.start_kind = "departure";
    await route.fulfill({ json: { data: { ...response, planning_context: nextContext } } });
  });

  await page.goto("/");
  await page.getByLabel("描述你的空闲时间和偏好").fill("今天早上出发出去玩");
  await page.getByRole("button", { name: "发送需求" }).click();
  await expect(page.getByText("早上大概几点出发？")).toBeVisible();
  await page.getByRole("button", { name: "去顶部修改" }).click();
  await expect(page.getByRole("dialog", { name: "日期时间" })).toBeVisible();
  await page.getByLabel("出发时间").fill("09:30");
  await page.getByRole("button", { name: "确认并继续" }).click();

  await expect.poll(() => submittedPatch).not.toBeNull();
  expect(submittedPatch).toMatchObject({
    base_revision: 4,
    when: { start_at: { operation: "set", value: "09:30" } },
  });
  await expect(page.getByRole("heading", { name: "方案一", level: 3 })).toBeVisible();
});

test("recovery card submits a typed action and resumes planning", async ({ page }) => {
  const recoveryResponse = structuredClone(response) as Record<string, unknown>;
  recoveryResponse.status = "needs_recovery";
  recoveryResponse.reply = "";
  recoveryResponse.plans = [];
  recoveryResponse.conflict = {
    code: "NO_PLAN_WITHIN_STRICT_BUDGET",
    message: "当前严格预算内没有可行方案。",
  };
  recoveryResponse.recovery = {
    kind: "recovery_choice",
    interaction_id: "recovery-budget-1",
    request_revision: 2,
    plan_version_id: null,
    reason: {
      code: "NO_PLAN_WITHIN_STRICT_BUDGET",
      kind: "no_feasible_plan",
      stage: "verification",
      fields: ["budget_per_person"],
      request_revision: 2,
      plan_version_id: null,
      public_summary: "当前严格预算内没有可行方案。",
      diagnostics: {},
    },
    actions: [{
      kind: "apply_request_patch",
      action_id: "use-budget-as-preference-r2",
      label: "把预算改为参考条件并重新规划",
      description: "方案可能超过当前预算，生成后请核对价格。",
      request_revision: 2,
      plan_version_id: null,
      continuation: "plan",
      patch: { base_revision: 2, set_fields: { strict_budget: false } },
    }],
  };

  let submitted: Record<string, unknown> | null = null;
  await page.route("**/api/sessions/offline-session/messages/stream", async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    if (body.recovery_action) {
      submitted = body;
      await route.fulfill(streamResponse(response));
      return;
    }
    await route.fulfill(streamResponse(recoveryResponse));
  });

  await page.goto("/");
  await page.getByLabel("描述你的空闲时间和偏好").fill("今天下午出去玩，别超预算");
  await page.getByRole("button", { name: "发送需求" }).click();
  await expect(page.getByRole("group", { name: "规划恢复选项" })).toBeVisible();
  await page.getByRole("button", { name: /把预算改为参考条件并重新规划/ }).click();

  await expect.poll(() => submitted).not.toBeNull();
  expect(submitted).toMatchObject({
    recovery_action: {
      interaction_id: "recovery-budget-1",
      action_id: "use-budget-as-preference-r2",
      request_revision: 2,
      plan_version_id: null,
    },
  });
  expect(submitted).not.toHaveProperty("conversation_command");
  await expect(page.getByRole("heading", { name: "方案一", level: 3 })).toBeVisible();
  await expect(page.getByText("此恢复选项已结束，不能重复执行。")).toBeVisible();
});

test("recovery outcome and superseded card state persist after refresh", async ({ page }) => {
  const firstRecovery = structuredClone(response) as Record<string, any>;
  firstRecovery.status = "needs_recovery";
  firstRecovery.reply = "";
  firstRecovery.plans = [];
  firstRecovery.conflict = { code: "NO_PLAN_WITHIN_DISTANCE", message: "当前条件下暂未找到可行方案。" };
  firstRecovery.recovery = {
    kind: "recovery_choice",
    interaction_id: "recovery-distance-1",
    request_revision: 2,
    plan_version_id: null,
    reason: {
      code: "NO_PLAN_WITHIN_DISTANCE",
      kind: "no_feasible_plan",
      stage: "verification",
      fields: ["max_distance_km"],
      request_revision: 2,
      plan_version_id: null,
      public_summary: "当前条件下暂未找到可行方案。",
      diagnostics: {},
    },
    actions: [{
      kind: "apply_request_patch",
      action_id: "expand-distance-r2",
      label: "将范围扩大到 12km",
      description: "扩大范围后重新规划。",
      request_revision: 2,
      plan_version_id: null,
      continuation: "plan",
      patch: { base_revision: 2, set_fields: { max_distance_km: 12 } },
    }],
  };

  const stillUnsatisfied = structuredClone(firstRecovery);
  stillUnsatisfied.recovery_outcome = {
    interaction_id: "recovery-distance-1",
    action_id: "expand-distance-r2",
    action_fingerprint: "expand:max_distance=12",
    strategy_fingerprint: "expand:max_distance",
    kind: "apply_request_patch",
    label: "将范围扩大到 12km",
    status: "still_failed",
    changed_fields: ["max_distance_km"],
    field_changes: [{ field: "max_distance_km", before: "5", after: "12" }],
    previous_bottleneck: "distance_limit",
    current_bottleneck: "schedule_capacity",
    summary: "最大距离：5 → 12。调整已应用，但仍未找到方案。",
  };
  stillUnsatisfied.recovery = {
    ...firstRecovery.recovery,
    interaction_id: "recovery-schedule-2",
    request_revision: 3,
    reason: {
      ...firstRecovery.recovery.reason,
      code: "NO_PLAN_WITHIN_TIME_WINDOW",
      fields: ["planning_window"],
      request_revision: 3,
      public_summary: "扩大范围后，当前主要限制变为可用时间。",
    },
    actions: [{
      kind: "apply_request_patch",
      action_id: "reduce-stops-r3",
      label: "改为三站并重新规划",
      description: "减少站点数量后重新规划。",
      request_revision: 3,
      plan_version_id: null,
      continuation: "plan",
      patch: { base_revision: 3, set_fields: { exact_stop_count: 3 } },
    }],
  };
  stillUnsatisfied.conflict = {
    code: "NO_PLAN_WITHIN_TIME_WINDOW",
    message: "扩大范围后，当前主要限制变为可用时间。",
  };

  let responseHistory: Record<string, any>[] = [];
  await page.route("**/api/sessions/offline-session/messages/stream", async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    if (body.recovery_action) {
      responseHistory = [firstRecovery, stillUnsatisfied];
      await route.fulfill(streamResponse(stillUnsatisfied));
      return;
    }
    responseHistory = [firstRecovery];
    await route.fulfill(streamResponse(firstRecovery));
  });
  await page.route("**/api/sessions/offline-session", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    const assistantMessages = responseHistory.map((item, index) => ({
      id: `assistant-${index + 1}`,
      role: "assistant",
      content: item.conflict?.message ?? item.reply,
    }));
    await route.fulfill({ json: { data: {
      session_id: "offline-session",
      title: "恢复后仍无解",
      status: "needs_recovery",
      created_at: "2026-08-20T00:00:00Z",
      updated_at: "2026-08-20T00:00:00Z",
      messages: [
        { id: "user-1", role: "user", content: "四站，14点到18点，范围5km" },
        ...assistantMessages,
      ],
      plans: [],
      latest_response: responseHistory.at(-1) ?? null,
      response_history: responseHistory,
      selected_plan_id: null,
    } } });
  });

  await page.goto("/");
  await page.getByLabel("描述你的空闲时间和偏好").fill("四站，14点到18点，范围5km");
  await page.getByRole("button", { name: "发送需求" }).click();
  await page.getByRole("button", { name: /将范围扩大到 12km/ }).click();

  await expect(page.getByText("最大距离：5 → 12。调整已应用，但仍未找到方案。")).toBeVisible();
  await expect(page.getByText("当前主要限制：时间容量（原限制：距离范围）")).toBeVisible();
  await expect(page.getByRole("button", { name: /改为三站并重新规划/ })).toBeVisible();

  await page.reload();

  await expect(page.getByText("最大距离：5 → 12。调整已应用，但仍未找到方案。")).toBeVisible();
  await expect(page.getByText("当前主要限制：时间容量（原限制：距离范围）")).toBeVisible();
  await expect(page.getByText("此恢复选项已结束，不能重复执行。")).toBeVisible();
  await expect(page.getByRole("button", { name: /改为三站并重新规划/ })).toBeVisible();
});
