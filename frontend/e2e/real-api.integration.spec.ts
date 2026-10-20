import { expect, test } from "@playwright/test";
import path from "node:path";

test("real API preserves selection, restores a session, and saves conditions without replanning", async ({ page, request }, testInfo) => {
  const mobile = testInfo.project.name.startsWith("mobile");
  // No page.route handlers: requests pass through Vite to FastAPI and temporary SQLite.
  await page.goto("/");
  await page.getByLabel("描述你的空闲时间和偏好").fill("今天下午出去玩");
  await page.getByRole("button", { name: "发送需求" }).click();
  await expect(page.locator(".plan-card").first()).toBeVisible();
  const sessionId = new URL(page.url()).searchParams.get("session");
  expect(sessionId).toBeTruthy();
  const snapshot = async () => {
    const result = await request.get(`/api/sessions/${sessionId}`);
    expect(result.ok()).toBe(true);
    return (await result.json()).data;
  };
  const before = await snapshot();
  expect(before.plans.length).toBeGreaterThan(0);
  expect(before.selected_plan_id).toBeNull();
  await page.locator(".plan-card").first().getByRole("button", { name: /打开.+详情/ }).click();
  await page.getByRole("button", { name: "选择此方案" }).click();
  await expect(page.getByRole("button", { name: "已选择此方案" })).toBeDisabled();
  const selected = await snapshot();
  expect(selected.selected_plan_id).toBe(before.plans[0].plan_id);
  expect(selected.active_plan_version_id).toBe(before.active_plan_version_id);

  // The D inspector is a read-only view of the response snapshot. Keep a
  // mutation counter around its trip, POI and map paths and compare the
  // session's selected plan/version/revision before continuing with C checks.
  let inspectorWrites = 0;
  page.on("request", (request) => {
    if (/\/api\//.test(request.url()) && ["POST", "PUT", "PATCH", "DELETE"].includes(request.method())) inspectorWrites += 1;
  });
  const inspectorSnapshot = {
    selectedPlanId: selected.selected_plan_id,
    activeVersionId: selected.active_plan_version_id,
    requestRevision: selected.planning_context.request_revision,
  };
  const screenshot = (name: string) => page.screenshot({ path: path.resolve(`../.impeccable/review/${name}.png`) });
  await expect(page.getByRole("tab", { name: "行程" })).toHaveAttribute("aria-selected", "true");
  await screenshot(mobile ? "s-ui1d-mobile-trip" : "s-ui1d-desktop-trip");
  const firstStop = page.locator(".timeline-stop-open").first();
  await expect(firstStop).toBeVisible();
  await firstStop.click();
  await expect(page.locator(".poi-page")).toBeVisible();
  await expect(page.locator(".poi-page-title h2")).toBeVisible();
  await screenshot(mobile ? "s-ui1d-mobile-poi" : "s-ui1d-desktop-poi");
  await page.getByRole("button", { name: "返回行程" }).click();
  await expect(page.getByRole("tab", { name: "行程" })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "地图" }).click();
  await expect(page.locator(".map-detail")).toBeVisible();
  await expect(page.locator(".map-stop-row").first()).toBeVisible();
  // This local API fixture may intentionally omit geometry or a browser key;
  // the visible fallback is still a valid map workspace state.
  const mapStatus = page.locator(".amap-map-status");
  if (await mapStatus.count()) await expect(mapStatus.first()).toBeVisible();
  if (!mobile) await screenshot("s-ui1d-desktop-map");
  await page.locator(".map-stop-row").first().click();
  await expect(page.locator(".poi-page")).toBeVisible();
  await page.getByRole("button", { name: "返回地图" }).click();
  await expect(page.getByRole("tab", { name: "地图" })).toHaveAttribute("aria-selected", "true");
  if (mobile) {
    await page.locator(".map-stop-row").first().click();
    await expect(page.locator(".poi-page")).toBeVisible();
    await page.getByRole("button", { name: "返回地图" }).click();
    await expect(page.getByRole("tab", { name: "地图" })).toHaveAttribute("aria-selected", "true");
    await screenshot("s-ui1d-mobile-return");
  }
  const afterInspector = await snapshot();
  expect(afterInspector.selected_plan_id).toBe(inspectorSnapshot.selectedPlanId);
  expect(afterInspector.active_plan_version_id).toBe(inspectorSnapshot.activeVersionId);
  expect(afterInspector.planning_context.request_revision).toBe(inspectorSnapshot.requestRevision);
  expect(inspectorWrites).toBe(0);

  await page.screenshot({ path: path.resolve(`../.impeccable/review/s-ui1c-selected-${testInfo.project.name}.png`) });
  if (before.plans.length > 1) {
    if (mobile) await page.getByRole("button", { name: "关闭" }).click();
    await page.locator(".plan-card .plan-preview-button").nth(1).click();
    if (mobile) await page.locator(".plan-card").nth(1).getByRole("button", { name: /打开.+详情/ }).click();
    await expect(page.locator(".inspector-plan-header")).toContainText("当前预览 · 未改变正式选择");
    expect((await snapshot()).selected_plan_id).toBe(selected.selected_plan_id);
  }
  await page.reload();
  await expect(page.locator(".plan-card.selected .plan-preview-button")).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: /修改人均预算/ }).click();
  await page.getByLabel("设置人均预算").check();
  await page.getByLabel("人均金额（元）").fill("180");
  await page.getByRole("button", { name: "取消", exact: true }).click();
  expect((await snapshot()).planning_context.request_revision).toBe(selected.planning_context.request_revision);
  await page.getByRole("button", { name: /修改日期时间/ }).click();
  await page.getByLabel("日期", { exact: true }).fill("2026-08-17");
  await page.getByRole("button", { name: "保存条件" }).click();
  await expect(page.getByText("条件已更新，当前方案基于旧条件。")).toBeVisible();
  const edited = await snapshot();
  expect(edited.planning_context.plan_stale).toBe(true);
  expect(edited.planning_context.request_revision).toBeGreaterThan(selected.planning_context.request_revision);
  expect(edited.active_plan_version_id).toBe(selected.active_plan_version_id);
  expect(edited.selected_plan_id).toBe(selected.selected_plan_id);
  await page.reload();
  await expect(page.getByText("条件已更新，当前方案基于旧条件。")).toBeVisible();
  await expect(page.getByRole("button", { name: "按新条件重新规划" })).toBeVisible();

  const staleVersion = edited.active_plan_version_id;
  const advancedTrigger = page.getByRole("button", { name: /^更多条件/ });
  await advancedTrigger.click();
  await page.getByLabel("精确站数").fill("2");
  await page.getByLabel("检索及单段距离上限（km）").fill("100");
  await page.getByLabel("全程累计距离上限（km）").fill("250");
  await screenshot(`s-ui1e-advanced-${testInfo.project.name}`);
  await page.getByRole("button", { name: "保存条件" }).click();
  await expect(page.getByText("条件已更新，当前方案基于旧条件。")).toBeVisible();
  const advancedSaved = await snapshot();
  expect(advancedSaved.planning_context.exact_stop_count.value).toBe(2);
  expect(advancedSaved.planning_context.max_distance_km.value).toBe(100);
  expect(advancedSaved.planning_context.total_distance_km.value).toBe(250);
  expect(advancedSaved.planning_context.plan_stale).toBe(true);
  expect(advancedSaved.active_plan_version_id).toBe(staleVersion);

  await page.reload();
  await expect(page.getByText("条件已更新，当前方案基于旧条件。")).toBeVisible();
  await page.getByRole("button", { name: /^更多条件/ }).click();
  await expect(page.getByLabel("精确站数")).toHaveValue("2");
  await expect(page.getByLabel("检索及单段距离上限（km）")).toHaveValue("100");
  await expect(page.getByLabel("全程累计距离上限（km）")).toHaveValue("250");
  await page.getByRole("button", { name: "取消", exact: true }).click();

  await page.getByRole("button", { name: "按新条件重新规划" }).click();
  await expect(page.getByText("条件已更新，当前方案基于旧条件。")).toHaveCount(0);
  const advancedReplanned = await snapshot();
  expect(advancedReplanned.planning_context.plan_stale).toBe(false);
  expect(advancedReplanned.active_plan_version_id).not.toBe(staleVersion);
  expect(advancedReplanned.planning_context.exact_stop_count.value).toBe(2);

  await page.getByRole("button", { name: /修改同行人数/ }).click();
  const petPreference = page.getByRole("checkbox", { name: /携带宠物/ });
  await expect(petPreference).not.toBeChecked();
  await petPreference.check();
  await screenshot(`s-ui1e-pet-${testInfo.project.name}`);
  await page.getByRole("button", { name: "保存条件" }).click();
  const petSaved = await snapshot();
  expect(petSaved.planning_context.preferences.value.preferences).toContain("携带宠物");

  await page.getByRole("button", { name: /修改出行偏好/ }).click();
  await page.getByRole("button", { name: "移除体验偏好 携带宠物" }).click();
  await page.getByRole("button", { name: "保存条件" }).click();
  const petRemoved = await snapshot();
  expect(petRemoved.planning_context.preferences.value.preferences).not.toContain("携带宠物");
  await page.getByRole("button", { name: /修改同行人数/ }).click();
  await expect(page.getByRole("checkbox", { name: /携带宠物/ })).not.toBeChecked();
  await page.getByRole("button", { name: "取消", exact: true }).click();
});

test("real API clarification requires confirmation before applying a recovery proposal", async ({ page, request }, testInfo) => {
  let messageWrites = 0;
  page.on("request", (apiRequest) => {
    if (/\/api\/sessions\/[^/]+\/messages$/.test(new URL(apiRequest.url()).pathname) && apiRequest.method() === "POST") {
      messageWrites += 1;
    }
  });
  const screenshot = (name: string) => page.screenshot({ path: path.resolve(`../.impeccable/review/${name}.png`) });
  await page.goto("/");
  await page.getByLabel("描述你的空闲时间和偏好").fill("今天下午出去玩，别超预算");
  await page.getByRole("button", { name: "发送需求" }).click();
  const question = page.getByRole("group", { name: "补充信息选项" });
  await expect(question).toBeVisible();
  await expect(question).toContainText("预算");
  await screenshot(`s-ui1e-clarification-${testInfo.project.name}`);
  const sessionId = new URL(page.url()).searchParams.get("session");
  expect(sessionId).toBeTruthy();

  await page.getByLabel("补充这个信息后继续").fill("人均1元，严格控制，不超预算");
  await page.getByRole("button", { name: "发送需求" }).click();
  const recoveryCard = page.getByRole("group", { name: "规划恢复选项" }).last();
  await expect(recoveryCard).toBeVisible();
  const proposal = page.getByRole("button", { name: "把预算改为参考条件并重新规划" });
  await expect(proposal).toBeVisible();
  const beforeCancel = messageWrites;
  await proposal.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("严格上限");
  await expect(dialog).toContainText("参考条件");
  await screenshot(`s-ui1e-recovery-proposal-${testInfo.project.name}`);
  await dialog.getByRole("button", { name: "取消" }).click();
  expect(messageWrites).toBe(beforeCancel);

  await proposal.click();
  await page.getByRole("button", { name: "确认并继续" }).click();
  await expect.poll(() => messageWrites).toBe(beforeCancel + 1);
  const outcome = page.locator(".recovery-lifecycle").last();
  await expect(outcome).toBeVisible();
  await expect(outcome).toHaveClass(/recovery-lifecycle-succeeded/);
  await expect(outcome).toContainText("新的方案已生成并重新核验");
  await expect.poll(async () => {
    const savedSession = await request.get(`/api/sessions/${sessionId}`);
    if (!savedSession.ok()) return false;
    const data = (await savedSession.json()).data;
    return data.active_constraints?.strict_budget === false
      && data.planning_context?.budget?.value?.strict === false;
  }).toBe(true);
  const savedSession = await request.get(`/api/sessions/${sessionId}`);
  expect(savedSession.ok()).toBe(true);
  const savedData = (await savedSession.json()).data;
  expect(savedData.active_constraints.strict_budget).toBe(false);
  expect(savedData.planning_context.budget.value.strict).toBe(false);
  await screenshot(`s-ui1e-recovery-outcome-${testInfo.project.name}`);
});
