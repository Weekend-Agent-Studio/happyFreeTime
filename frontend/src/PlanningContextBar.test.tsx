import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PlanningContextBar } from "./PlanningContextBar";
import type { PlanningContextSummary } from "./types";

const context: PlanningContextSummary = {
  request_revision: 4,
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
      />,
    );

    await user.click(screen.getByRole("button", { name: /修改Where/ }));
    await user.clear(screen.getByLabelText("出发地点"));
    await user.type(screen.getByLabelText("出发地点"), "国贸地铁站");
    await user.click(screen.getByRole("button", { name: "保存并重新规划" }));

    await user.click(screen.getByRole("button", { name: /修改When/ }));
    await user.clear(screen.getByLabelText("开始时间"));
    await user.type(screen.getByLabelText("开始时间"), "09:30");
    await user.click(screen.getByRole("button", { name: "保存并重新规划" }));

    await user.click(screen.getByRole("button", { name: /修改Who/ }));
    await user.clear(screen.getByLabelText("成人"));
    await user.type(screen.getByLabelText("成人"), "2");
    await user.clear(screen.getByLabelText("儿童"));
    await user.type(screen.getByLabelText("儿童"), "1");
    await user.type(screen.getByLabelText("儿童年龄（可选）"), "6");
    await user.click(screen.getByRole("button", { name: "保存并重新规划" }));

    await user.click(screen.getByRole("button", { name: /修改Budget/ }));
    await user.click(screen.getByLabelText("设置人均预算"));
    await user.type(screen.getByLabelText("人均金额（元）"), "300");
    await user.click(screen.getByLabelText("严格控制，不超预算"));
    await user.click(screen.getByRole("button", { name: "保存并重新规划" }));

    await user.click(screen.getByRole("button", { name: /修改Preferences/ }));
    await user.type(screen.getByLabelText("添加偏好"), "清淡");
    await user.click(screen.getByRole("button", { name: "添加" }));
    await user.click(screen.getByRole("button", { name: "保存并重新规划" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(5));
    expect(onSave.mock.calls.map(([patch]) => patch)).toEqual([
      { base_revision: 4, where: { operation: "set", value: "国贸地铁站" } },
      { base_revision: 4, when: { start_at: { operation: "set", value: "09:30" } } },
      { base_revision: 4, who: { adults: 2, children: 1, child_age: { operation: "set", value: 6 } } },
      { base_revision: 4, budget: { mode: "per_person", amount: 300, strict: true } },
      { base_revision: 4, preferences: { add: ["清淡"], remove: [] } },
    ]);
  });
});
