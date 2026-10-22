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
    await expect(page.getByRole("region", { name: "当前预览方案" })).toContainText("当前预览");
    await expect(page.getByRole("button", { name: /返回已选方案/ })).toBeVisible();
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
  await expect(page.getByText("规划条件已更新，旧方案尚未重新验证。")).toBeVisible();
  const edited = await snapshot();
  expect(edited.planning_context.plan_stale).toBe(true);
  expect(edited.planning_context.request_revision).toBeGreaterThan(selected.planning_context.request_revision);
  expect(edited.active_plan_version_id).toBe(selected.active_plan_version_id);
  expect(edited.selected_plan_id).toBe(selected.selected_plan_id);
  await page.reload();
  await expect(page.getByText("规划条件已更新，旧方案尚未重新验证。")).toBeVisible();
  await expect(page.getByRole("button", { name: "按新条件重新规划" })).toBeVisible();

  const staleVersion = edited.active_plan_version_id;
  const advancedTrigger = page.getByRole("button", { name: /^更多条件/ });
  await advancedTrigger.click();
  await page.getByLabel("精确站数").fill("2");
  await page.getByLabel("检索及单段距离上限（km）").fill("100");
  await page.getByLabel("全程累计距离上限（km）").fill("250");
  await screenshot(`s-ui1e-advanced-${testInfo.project.name}`);
  await page.getByRole("button", { name: "保存条件" }).click();
  await expect(page.getByText("规划条件已更新，旧方案尚未重新验证。")).toBeVisible();
  const advancedSaved = await snapshot();
  expect(advancedSaved.planning_context.exact_stop_count.value).toBe(2);
  expect(advancedSaved.planning_context.max_distance_km.value).toBe(100);
  expect(advancedSaved.planning_context.total_distance_km.value).toBe(250);
  expect(advancedSaved.planning_context.plan_stale).toBe(true);
  expect(advancedSaved.active_plan_version_id).toBe(staleVersion);

  await page.reload();
  await expect(page.getByText("规划条件已更新，旧方案尚未重新验证。")).toBeVisible();
  await page.getByRole("button", { name: /^更多条件/ }).click();
  await expect(page.getByLabel("精确站数")).toHaveValue("2");
  await expect(page.getByLabel("检索及单段距离上限（km）")).toHaveValue("100");
  await expect(page.getByLabel("全程累计距离上限（km）")).toHaveValue("250");
  await page.getByRole("button", { name: "取消", exact: true }).click();

  await page.getByRole("button", { name: "按新条件重新规划" }).click();
  await expect(page.getByText("规划条件已更新，旧方案尚未重新验证。")).toHaveCount(0);
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
    if (/\/api\/sessions\/[^/]+\/messages(?:\/stream)?$/.test(new URL(apiRequest.url()).pathname) && apiRequest.method() === "POST") {
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

test("real deterministic replacement persists PlanDiff, selection, and failure recovery", async ({ page, request }, testInfo) => {
  const mobile = testInfo.project.name.startsWith("mobile");
  const apiRoot = "http://127.0.0.1:8008";
  const requestPaths: string[] = [];
  page.on("request", (browserRequest) => {
    const pathname = new URL(browserRequest.url()).pathname;
    if (browserRequest.method() === "POST" && /\/api\/sessions\/[^/]+\/messages(?:\/stream)?$/.test(pathname)) {
      requestPaths.push(pathname);
    }
  });
  const screenshot = (name: string) => page.screenshot({ path: path.resolve(`../.impeccable/review/${name}.png`) });
  await page.setViewportSize(mobile ? { width: 375, height: 812 } : { width: 1440, height: 900 });
  await page.goto("http://127.0.0.1:4175/");
  await page.getByLabel("描述你的空闲时间和偏好").fill("今天下午出去玩");
  await page.getByRole("button", { name: "发送需求" }).click();
  await expect(page.locator(".plan-card").first()).toBeVisible();
  const sessionId = new URL(page.url()).searchParams.get("session");
  expect(sessionId).toBeTruthy();
  const snapshot = async () => {
    const result = await request.get(`${apiRoot}/api/sessions/${sessionId}`);
    expect(result.ok()).toBe(true);
    return (await result.json()).data;
  };
  const before = await snapshot();
  const oldPlanIndex = before.plans.findIndex((plan: any) => plan.stops.some((stop: any) => stop.resource_id === "activity-old"));
  expect(oldPlanIndex).toBeGreaterThanOrEqual(0);
  const oldPlan = before.plans[oldPlanIndex];
  const targetIndex = oldPlan.stops.findIndex((stop: any) => stop.resource_id === "activity-old");
  expect(targetIndex).toBeGreaterThanOrEqual(0);
  const targetStop = oldPlan.stops[targetIndex];

  const oldPlanCard = page.locator(".plan-card").nth(oldPlanIndex);
  await oldPlanCard.getByRole("button", { name: /打开.+详情/ }).click();
  await page.getByRole("button", { name: "选择此方案" }).click();
  await expect.poll(async () => (await snapshot()).selected_plan_id).toBe(oldPlan.plan_id);
  const replaceButton = page.locator(".timeline-stop-content").nth(targetIndex).getByRole("button", { name: "替换此站" });
  await replaceButton.click();
  const draft = page.getByRole("region", { name: "单站替换草稿" });
  await expect(draft).toBeVisible();
  if (mobile) {
    await expect(page.getByRole("dialog", { name: "方案工作区" })).toBeHidden();
    await expect(draft).toBeFocused();
  }
  await screenshot(mobile ? "s-ui1f-mobile-real-draft" : "s-ui1f-desktop-real-draft");

  await page.getByRole("button", { name: "更安静" }).click();
  const diffSections = page.locator(".plan-diff-evidence");
  await expect(diffSections.first()).toBeVisible();
  await expect.poll(async () => (await snapshot()).active_plan_version_id).not.toBe(before.active_plan_version_id);
  const modifiedSnapshot = await snapshot();
  expect(modifiedSnapshot.selected_plan_id).toBeNull();
  const modifiedResponse = modifiedSnapshot.latest_response;
  expect(modifiedResponse.status).toBe("completed");
  expect(modifiedResponse.plans).toHaveLength(2);
  expect(modifiedResponse.plan_diffs).toHaveLength(2);
  expect(new Set(modifiedResponse.plan_diffs.map((diff: any) => diff.new_plan_id))).toEqual(new Set(modifiedResponse.plans.map((plan: any) => plan.plan_id)));
  for (const diff of modifiedResponse.plan_diffs) {
    const candidate = modifiedResponse.plans.find((plan: any) => plan.plan_id === diff.new_plan_id);
    expect(candidate).toBeTruthy();
    expect(diff.to_plan_version_id).toBe(modifiedSnapshot.active_plan_version_id);
    expect(diff.base_plan_id).toBe(oldPlan.plan_id);
    expect(diff.replacements).toHaveLength(1);
    expect(diff.replacements[0]).toMatchObject({
      stop_index: targetIndex,
      before_resource_id: targetStop.resource_id,
    });
    expect(diff.replacements[0].after_resource_id).not.toBe(targetStop.resource_id);
    const lockedIndices = diff.locked_stops.map((stop: any) => stop.stop_index).sort((left: number, right: number) => left - right);
    expect(lockedIndices).toEqual(oldPlan.stops.map((_stop: any, index: number) => index).filter((index: number) => index !== targetIndex));
    for (const locked of diff.locked_stops) {
      expect(candidate.stops[locked.stop_index]).toMatchObject({ resource_id: locked.resource_id, role: locked.role });
      expect(candidate.stops[locked.stop_index].resource_id).toBe(oldPlan.stops[locked.stop_index].resource_id);
    }
  }
  await expect(diffSections).toHaveCount(2);
  await screenshot(mobile ? "s-ui1f-mobile-real-diff" : "s-ui1f-desktop-real-diff");

  const selectedCandidate = modifiedSnapshot.plans[0];
  const modifiedReply = page.locator(".rich-planning-reply").last();
  if (mobile) await modifiedReply.locator(".plan-card").filter({ hasText: selectedCandidate.title }).getByRole("button", { name: /打开.+详情/ }).click();
  await page.getByRole("button", { name: "选择此方案" }).click();
  await expect(page.getByRole("button", { name: "已选择此方案" })).toBeDisabled();
  await expect.poll(async () => (await snapshot()).selected_plan_id).toBe(selectedCandidate.plan_id);
  await page.reload();
  await expect(page.getByRole("heading", { name: selectedCandidate.title, level: 3 }).last()).toBeVisible();
  const restored = await snapshot();
  expect(restored.active_plan_version_id).toBe(modifiedSnapshot.active_plan_version_id);
  expect(restored.selected_plan_id).toBe(selectedCandidate.plan_id);
  if (mobile) await page.locator(".rich-planning-reply").last().locator(".plan-card").filter({ hasText: selectedCandidate.title }).getByRole("button", { name: /打开.+详情/ }).click();

  const selectedPlan = restored.plans.find((plan: any) => plan.plan_id === selectedCandidate.plan_id);
  const secondTargetIndex = selectedPlan.stops.findIndex((stop: any) => stop.role === "activity");
  await page.locator(".timeline-stop-content").nth(secondTargetIndex).getByRole("button", { name: "替换此站" }).click();
  await page.getByRole("button", { name: "全程更近" }).click();
  const recoveryCard = page.getByRole("group", { name: "规划恢复选项" }).last();
  await expect(recoveryCard).toBeVisible();
  await expect(recoveryCard).toContainText("没有找到全程距离确实更短的替换方案");
  await expect.poll(async () => (await snapshot()).latest_response.status).toBe("needs_recovery");
  const failedSnapshot = await snapshot();
  expect(failedSnapshot.active_plan_version_id).toBe(modifiedSnapshot.active_plan_version_id);
  expect(failedSnapshot.selected_plan_id).toBe(selectedCandidate.plan_id);
  await screenshot(mobile ? "s-ui1f-mobile-real-recovery" : "s-ui1f-desktop-real-recovery");

  expect(requestPaths).toHaveLength(3);
  expect(requestPaths.every((pathname) => pathname.endsWith("/messages/stream"))).toBe(true);

  const directSessionResponse = await request.post(`${apiRoot}/api/sessions`);
  expect(directSessionResponse.ok()).toBe(true);
  const directSessionId = (await directSessionResponse.json()).data.session_id as string;
  const directMessagesPath = `/api/sessions/${directSessionId}/messages`;
  const directMessageResponse = await request.post(`${apiRoot}${directMessagesPath}`, {
    data: { request_id: `s-ui1f-non-stream-${Date.now()}`, content: "今天下午出去玩" },
  });
  expect(new URL(directMessageResponse.url()).pathname).toBe(directMessagesPath);
  expect(directMessageResponse.ok()).toBe(true);
  const directMessage = (await directMessageResponse.json()).data;
  expect(directMessage.plans.length).toBeGreaterThan(0);
  const directSnapshotResponse = await request.get(`${apiRoot}/api/sessions/${directSessionId}`);
  expect(directSnapshotResponse.ok()).toBe(true);
  expect((await directSnapshotResponse.json()).data.active_plan_version_id).toBe(directMessage.plan_version_id);
});
