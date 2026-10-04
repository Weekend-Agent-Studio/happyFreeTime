import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PlanningContextBar } from "./PlanningContextBar";
import type { PlanningContextSummary } from "./types";

const context: PlanningContextSummary = {
  request_revision: 4,
  planned_request_revision: null,
  has_active_plan: false,
  plan_stale: false,
  ready_for_planning: false,
  where: {
    value: { address: "北京市朝阳区" }, display_value: "北京市朝阳区",
    source: "default", editable: true, status: "assumed",
  },
  when: {
    date: { value: "2026-08-15", display_value: "2026-08-15", source: "user", editable: true, status: "resolved" },
    start_at: { value: "14:00", display_value: "14:00", source: "derived", editable: true, status: "resolved" },
    end_at: { value: "18:00", display_value: "18:00", source: "derived", editable: true, status: "resolved" },
    start_kind: "trip_start", end_kind: "trip_end",
  },
  who: {
    value: { adults: 1, children: 0, child_age: null, members: [] },
    display_value: "1 位成人 · 0 位儿童", source: "default", editable: true, status: "assumed",
  },
  budget: {
    value: { mode: "unlimited", amount: null, strict: false },
    display_value: "不限", source: "default", editable: true, status: "assumed",
  },
  preferences: {
    value: { preferences: [], diet_tags: [], scene_tags: [], avoid: [] },
    display_value: "未设置", source: "default", editable: true, status: "assumed",
  },
  pending_field: null,
};

describe("PlanningContextBar typed patches", () => {
  it("emits a section-specific patch for each of the five controls", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PlanningContextBar
        context={context}
        busy={false}
        externalOpen={null}
        onExternalOpenHandled={() => undefined}
        onSave={onSave}
        onReplan={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    await user.click(screen.getByRole("button", { name: /修改Where/ }));
    await user.clear(screen.getByLabelText("出发地点"));
    await user.type(screen.getByLabelText("出发地点"), "国贸地铁站");
    await user.click(screen.getByRole("button", { name: "保存条件" }));

    await user.click(screen.getByRole("button", { name: /修改When/ }));
    await user.clear(screen.getByLabelText("开始时间"));
    await user.type(screen.getByLabelText("开始时间"), "09:30");
    await user.click(screen.getByRole("button", { name: "保存条件" }));

    await user.click(screen.getByRole("button", { name: /修改Who/ }));
    await user.clear(screen.getByLabelText("成人"));
    await user.type(screen.getByLabelText("成人"), "2");
    await user.clear(screen.getByLabelText("儿童"));
    await user.type(screen.getByLabelText("儿童"), "1");
    await user.type(screen.getByLabelText("儿童年龄（可选）"), "6");
    await user.click(screen.getByRole("button", { name: "保存条件" }));

    await user.click(screen.getByRole("button", { name: /修改Budget/ }));
    await user.click(screen.getByLabelText("设置人均预算"));
    expect(screen.getByLabelText("设置人均预算").closest(".context-budget-choice")).toHaveClass("selected");
    await user.type(screen.getByLabelText("人均金额（元）"), "300");
    await user.click(screen.getByRole("checkbox", { name: /严格控制，不超预算/ }));
    await user.click(screen.getByRole("button", { name: "保存条件" }));

    await user.click(screen.getByRole("button", { name: /修改Preferences/ }));
    await user.selectOptions(screen.getByLabelText("偏好类别"), "diet_tags");
    await user.type(screen.getByLabelText("添加偏好"), "清淡");
    await user.click(screen.getByRole("button", { name: "添加" }));
    await user.click(screen.getByRole("button", { name: "保存条件" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(5));
    expect(onSave.mock.calls.map(([patch]) => patch)).toEqual([
      { base_revision: 4, where: { operation: "set", value: "国贸地铁站" } },
      { base_revision: 4, when: { start_at: { operation: "set", value: "09:30" } } },
      { base_revision: 4, who: { adults: 2, children: 1, child_age: { operation: "set", value: 6 } } },
      { base_revision: 4, budget: { mode: "per_person", amount: 300, strict: true } },
      { base_revision: 4, preferences: { add: [{ category: "diet_tags", value: "清淡" }], remove: [] } },
    ]);
  });

  it("shows concise time scope labels and a persistent replan action when conditions are stale", async () => {
    const user = userEvent.setup();
    const onReplan = vi.fn().mockResolvedValue(undefined);
    const staleContext = {
      ...context,
      has_active_plan: true,
      plan_stale: true,
      planned_request_revision: 3,
      ready_for_planning: true,
      when: {
        ...context.when,
        start_kind: "departure" as const,
        end_kind: "return_deadline" as const,
      },
    };
    render(
      <PlanningContextBar
        context={staleContext}
        busy={false}
        externalOpen={null}
        onExternalOpenHandled={() => undefined}
        onSave={vi.fn().mockResolvedValue(undefined)}
        onReplan={onReplan}
      />,
    );

    expect(screen.getByText("2026-08-15 · 出发 14:00 · 18:00 前到家")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /按新条件重新规划/ }));
    await waitFor(() => expect(onReplan).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole("button", { name: /修改When/ }));
    expect(screen.getByLabelText("出发时间")).toBeInTheDocument();
    expect(screen.getByLabelText("最晚到家")).toBeInTheDocument();
  });

  it("does not submit a patch when a section is saved without any edit", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PlanningContextBar
        context={context}
        busy={false}
        externalOpen={null}
        onExternalOpenHandled={() => undefined}
        onSave={onSave}
        onReplan={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    // Saving an untouched section must not bump the request revision. A budget
    // save used to be submitted unconditionally, which marked a still-valid
    // plan as stale and pushed the user into a pointless re-plan.
    for (const trigger of [/修改Where/, /修改When/, /修改Who/, /修改Budget/, /修改Preferences/]) {
      await user.click(screen.getByRole("button", { name: trigger }));
      await user.click(screen.getByRole("button", { name: "保存条件" }));
    }

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
