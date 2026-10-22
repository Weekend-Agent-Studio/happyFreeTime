import { expect, test } from "@playwright/test";
import path from "node:path";

test("real API searches, cancels, saves and replans from a verified departure place", async ({ page, request }, testInfo) => {
  const mobile = testInfo.project.name.startsWith("mobile");
  const apiRoot = "http://127.0.0.1:8007";
  const screenshot = (state: string) => page.screenshot({
    path: path.resolve(`../.impeccable/review/s-origin1-${mobile ? "mobile" : "desktop"}-${state}.png`),
  });
  await page.setViewportSize(mobile ? { width: 375, height: 812 } : { width: 1440, height: 900 });
  await page.goto("/");
  await page.getByLabel("描述你的空闲时间和偏好").fill("今天下午出去玩");
  await page.getByRole("button", { name: "发送需求" }).click();
  await expect(page.locator(".plan-card").first()).toBeVisible();
  const sessionId = new URL(page.url()).searchParams.get("session");
  expect(sessionId).toBeTruthy();
  const snapshot = async () => {
    const response = await request.get(`${apiRoot}/api/sessions/${sessionId}`);
    expect(response.ok()).toBe(true);
    return (await response.json()).data;
  };
  const before = await snapshot();
  expect(before.plans.length).toBeGreaterThan(0);
  await screenshot("default");

  let contextWrites = 0;
  page.on("request", (browserRequest) => {
    if (browserRequest.method() === "PATCH" && /\/api\/sessions\/[^/]+\/planning-context$/.test(new URL(browserRequest.url()).pathname)) {
      contextWrites += 1;
    }
  });
  const openDepartureEditor = async () => {
    await page.getByRole("button", { name: /修改出发地/ }).click();
    await expect(page.getByRole("heading", { name: "从哪出发" })).toBeVisible();
  };
  const searchForGuomao = async () => {
    await page.getByRole("combobox", { name: /搜索出发地点/ }).fill("国贸");
    await expect(page.getByRole("option", { name: /国贸/ }).first()).toBeVisible();
  };

  await openDepartureEditor();
  await searchForGuomao();
  await screenshot("results");
  await page.getByRole("option", { name: /国贸/ }).first().click();
  await expect(page.getByLabel("待保存出发地")).toContainText("国贸");
  await screenshot("pending");
  await page.getByRole("button", { name: /^取消$/ }).click();
  expect(contextWrites).toBe(0);
  const afterCancel = await snapshot();
  expect(afterCancel.planning_context.request_revision).toBe(before.planning_context.request_revision);
  expect(afterCancel.active_plan_version_id).toBe(before.active_plan_version_id);
  expect(afterCancel.selected_plan_id).toBe(before.selected_plan_id);

  await openDepartureEditor();
  await searchForGuomao();
  await page.getByRole("option", { name: /国贸/ }).first().click();
  await page.getByRole("button", { name: "保存条件" }).click();
  await expect(page.getByText("规划条件已更新，旧方案尚未重新验证。" )).toBeVisible();
  expect(contextWrites).toBe(1);
  const saved = await snapshot();
  expect(saved.planning_context.plan_stale).toBe(true);
  expect(saved.planning_context.request_revision).toBe(before.planning_context.request_revision + 1);
  expect(saved.active_plan_version_id).toBe(before.active_plan_version_id);
  expect(saved.selected_plan_id).toBe(before.selected_plan_id);
  expect(saved.plan_versions).toHaveLength(before.plan_versions.length);
  expect(saved.planning_context.where.value).toMatchObject({
    name: "国贸",
    provider_place_id: expect.stringMatching(/^mock:/),
    latitude: 39.9087,
    longitude: 116.4615,
  });
  await page.reload();
  await expect(page.getByRole("button", { name: /修改出发地，当前国贸/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "按新条件重新规划" })).toBeVisible();
  await screenshot("saved");

  await page.getByRole("button", { name: "按新条件重新规划" }).click();
  await expect.poll(async () => (await snapshot()).planning_context.plan_stale).toBe(false);
  const replanned = await snapshot();
  expect(replanned.active_plan_version_id).not.toBe(before.active_plan_version_id);
  expect(replanned.plan_versions).toHaveLength(before.plan_versions.length + 1);
  const firstRouteOrigin = replanned.plans[0].route_legs[0].geometry[0];
  expect(firstRouteOrigin).toMatchObject({ latitude: 39.9087, longitude: 116.4615 });
  // RouteLeg uses the stable role label; the selected identity is in the
  // saved location above, and its actual coordinates must drive this leg.
  expect(replanned.plans[0].route_legs[0].origin_name).toBe("出发地");
});
