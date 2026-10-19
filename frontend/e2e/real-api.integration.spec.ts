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
});
