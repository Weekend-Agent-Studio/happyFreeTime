import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { clarificationSection, PlanningContextBar, PlanningContextNotice } from "./PlanningContextBar";
import type { PlanningContextSummary } from "./types";

const context: PlanningContextSummary = {
  request_revision: 4,
  request_view: "committed",
  committed_request_revision: 4,
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
  exact_stop_count: { value: null, display_value: "未设置", source: "default", editable: true, status: "assumed" },
  max_distance_km: { value: null, display_value: "未设置", source: "default", editable: true, status: "assumed" },
  total_distance_km: { value: null, display_value: "未设置", source: "default", editable: true, status: "assumed" },
  pending_field: null,
};

describe("PlanningContextBar typed patches", () => {
  it("uses an icon and a quiet status dot for additional conditions", () => {
    const configuredContext = {
      ...context,
      max_distance_km: { value: 8, display_value: "8 km", source: "user" as const, editable: true, status: "resolved" as const },
    };
    const { container } = render(<PlanningContextBar context={configuredContext} sessionId="session-1" busy={false} externalOpen={null} onExternalOpenHandled={() => undefined} onSave={vi.fn().mockResolvedValue(undefined)} />);
    const button = screen.getByRole("button", { name: "更多条件，已设置" });
    expect(button.querySelector("svg")).toBeInTheDocument();
    expect(button.querySelector(".planning-context-more-indicator")).toBeInTheDocument();
    expect(button.querySelector(".context-edit-icon")).toBeNull();
    expect(container.querySelector(".planning-context-more")).toBe(button);
  });

  it("navigates the calendar without saving and submits the selected local date", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PlanningContextBar context={context} sessionId="session-1" busy={false} externalOpen={null} onExternalOpenHandled={() => undefined} onSave={onSave} />);
    await user.click(screen.getByRole("button", { name: /修改日期时间/ }));
    await user.click(screen.getByRole("button", { name: "下个月" }));
    expect(screen.getByLabelText("日期")).toHaveValue("2026-08-15");
    expect(onSave).not.toHaveBeenCalled();
    const first = screen.getByRole("button", { name: "2026年9月1日" });
    first.focus();
    await user.keyboard("{ArrowLeft}{Enter}");
    expect(screen.getByLabelText("日期")).toHaveValue("2026-08-31");
    await user.click(screen.getByRole("button", { name: "保存条件" }));
    expect(onSave).toHaveBeenCalledWith({ base_revision: 4, when: { date: { operation: "set", value: "2026-08-31" } } });
  });

  it("emits a section-specific patch for each of the five controls", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const { container } = render(
      <PlanningContextBar
        context={context}
        sessionId="session-1"
        busy={false}
        externalOpen={null}
        onExternalOpenHandled={() => undefined}
        onSave={onSave}
      />,
    );
    expect(container.querySelectorAll(".planning-context-trigger .context-edit-icon")).toHaveLength(5);

    await user.click(screen.getByRole("button", { name: /修改出发区域/ }));
    await user.clear(screen.getByLabelText("出发地点"));
    await user.type(screen.getByLabelText("出发地点"), "国贸地铁站");
    await user.click(screen.getByRole("button", { name: "保存条件" }));

    await user.click(screen.getByRole("button", { name: /修改日期时间/ }));
    await user.clear(screen.getByLabelText("开始时间"));
    await user.type(screen.getByLabelText("开始时间"), "09:30");
    await user.click(screen.getByRole("button", { name: "保存条件" }));

    await user.click(screen.getByRole("button", { name: /修改同行人数/ }));
    await user.clear(screen.getByLabelText("成人"));
    await user.type(screen.getByLabelText("成人"), "2");
    await user.clear(screen.getByLabelText("儿童"));
    await user.type(screen.getByLabelText("儿童"), "1");
    await user.type(screen.getByLabelText("儿童年龄（可选）"), "6");
    await user.click(screen.getByRole("button", { name: "保存条件" }));

    await user.click(screen.getByRole("button", { name: /修改人均预算/ }));
    await user.click(screen.getByLabelText("设置人均预算"));
    expect(screen.getByLabelText("设置人均预算").closest(".context-budget-choice")).toHaveClass("selected");
    await user.type(screen.getByLabelText("人均金额（元）"), "300");
    await user.click(screen.getByRole("checkbox", { name: /严格控制，不超预算/ }));
    await user.click(screen.getByRole("button", { name: "保存条件" }));

    await user.click(screen.getByRole("button", { name: /修改出行偏好/ }));
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
      <>
      <PlanningContextNotice context={staleContext} busy={false} onReplan={onReplan} />
      <PlanningContextBar
        context={staleContext}
        sessionId="session-1"
        busy={false}
        externalOpen={null}
        onExternalOpenHandled={() => undefined}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />
      </>,
    );

    expect(screen.getByText("2026-08-15 · 出发 14:00 · 18:00 前到家")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("规划条件已更新，旧方案尚未重新验证。");
    await user.click(screen.getByRole("button", { name: /按新条件重新规划/ }));
    await waitFor(() => expect(onReplan).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole("button", { name: /修改日期时间/ }));
    expect(screen.getByLabelText("出发时间")).toBeInTheDocument();
    expect(screen.getByLabelText("最晚到家")).toBeInTheDocument();
  });

  it("does not submit a patch when a section is saved without any edit", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PlanningContextBar
        context={context}
        sessionId="session-1"
        busy={false}
        externalOpen={null}
        onExternalOpenHandled={() => undefined}
        onSave={onSave}
      />,
    );

    // Saving an untouched section must not bump the request revision. A budget
    // save used to be submitted unconditionally, which marked a still-valid
    // plan as stale and pushed the user into a pointless re-plan.
    for (const trigger of [/修改出发区域/, /修改日期时间/, /修改同行人数/, /修改人均预算/, /修改出行偏好/]) {
      await user.click(screen.getByRole("button", { name: trigger }));
      await user.click(screen.getByRole("button", { name: "保存条件" }));
    }

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps date and time controls in one local value and rejects reverse time without submitting", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PlanningContextBar
        context={{ ...context, pending_field: "departure_at", when: { ...context.when, start_kind: "departure", end_kind: "return_deadline" } }}
        sessionId="session-1"
        busy={false}
        externalOpen={null}
        onExternalOpenHandled={() => undefined}
        onSave={onSave}
      />,
    );

    await user.click(screen.getByRole("button", { name: /修改日期时间/ }));
    expect(screen.getByLabelText("出发时间")).toHaveValue("14:00");
    expect(screen.getByLabelText("最晚到家")).toHaveValue("18:00");
    await user.click(screen.getByRole("button", { name: "上午 09:00–12:00" }));
    expect(screen.getByLabelText("出发时间")).toHaveValue("09:00");
    expect(screen.getByLabelText("最晚到家")).toHaveValue("12:00");
    const startSlider = screen.getByLabelText("时段开始时间快捷滑块");
    fireEvent.change(startSlider, { target: { value: "541" } });
    expect(screen.getByLabelText("出发时间")).toHaveValue("09:01");
    await user.click(screen.getByRole("button", { name: "上午 09:00–12:00" }));
    await user.click(screen.getByRole("button", { name: "2 小时" }));
    expect(screen.getByLabelText("时段开始时间快捷滑块")).toHaveAttribute("aria-valuetext", "09:00");
    expect(screen.getByLabelText("时段结束时间快捷滑块")).toHaveAttribute("aria-valuetext", "11:00");
    await user.clear(screen.getByLabelText("最晚到家"));
    await user.type(screen.getByLabelText("最晚到家"), "08:00");
    await user.click(screen.getByRole("button", { name: "确认并继续" }));

    expect(screen.getByRole("alert")).toHaveTextContent("结束时间需要晚于开始时间");
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("preserves exact budgets outside the slider convenience range", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const highBudgetContext = {
      ...context,
      budget: {
        ...context.budget,
        value: { mode: "per_person", amount: 1500, strict: true },
        display_value: "人均 ¥1500 · 严格",
        source: "user" as const,
      },
    };
    render(
      <PlanningContextBar
        context={highBudgetContext}
        sessionId="session-1"
        busy={false}
        externalOpen={null}
        onExternalOpenHandled={() => undefined}
        onSave={onSave}
      />,
    );

    await user.click(screen.getByRole("button", { name: /修改人均预算/ }));
    expect(screen.getByLabelText("人均金额（元）")).toHaveValue(1500);
    expect(screen.queryByLabelText("人均金额快捷滑块")).not.toBeInTheDocument();
    await user.clear(screen.getByLabelText("人均金额（元）"));
    await user.type(screen.getByLabelText("人均金额（元）"), "1750");
    await user.click(screen.getByRole("button", { name: "保存条件" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({
      base_revision: 4,
      budget: { mode: "per_person", amount: 1750, strict: true },
    }));
  });

  it("closes on Escape, restores focus, and invalidates an editor when the server revision changes", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const view = render(
      <PlanningContextBar
        context={context}
        sessionId="session-1"
        busy={false}
        externalOpen={null}
        onExternalOpenHandled={() => undefined}
        onSave={onSave}
      />,
    );
    const trigger = screen.getByRole("button", { name: /修改出发区域/ });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog");
    await user.keyboard("{Tab}");
    expect(dialog).toContainElement(document.activeElement as HTMLElement | null);
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
    expect(onSave).not.toHaveBeenCalled();

    await user.click(trigger);
    const backdrop = document.querySelector(".planning-context-backdrop");
    expect(backdrop).not.toBeNull();
    await user.click(backdrop as HTMLElement);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(onSave).not.toHaveBeenCalled();

    await user.click(trigger);
    view.rerender(
      <PlanningContextBar
        context={{ ...context, request_revision: 5 }}
        sessionId="session-1"
        busy={false}
        externalOpen={null}
        onExternalOpenHandled={() => undefined}
        onSave={onSave}
      />,
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(onSave).not.toHaveBeenCalled();

    await user.click(trigger);
    view.rerender(
      <PlanningContextBar
        context={context}
        sessionId="session-2"
        busy={false}
        externalOpen={null}
        onExternalOpenHandled={() => undefined}
        onSave={onSave}
      />,
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(onSave).not.toHaveBeenCalled();
  });

  it("keeps edited values visible when a save fails", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockRejectedValue(new Error("revision conflict"));
    render(
      <PlanningContextBar
        context={context}
        sessionId="session-1"
        busy={false}
        externalOpen={null}
        onExternalOpenHandled={() => undefined}
        onSave={onSave}
      />,
    );

    await user.click(screen.getByRole("button", { name: /修改出发区域/ }));
    await user.clear(screen.getByLabelText("出发地点"));
    await user.type(screen.getByLabelText("出发地点"), "国贸站");
    await user.click(screen.getByRole("button", { name: "保存条件" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("revision conflict");
    expect(screen.getByLabelText("出发地点")).toHaveValue("国贸站");
  });

  it("saves all advanced conditions together and routes their clarifications to the advanced editor", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PlanningContextBar
        context={context}
        sessionId="session-1"
        busy={false}
        externalOpen={null}
        onExternalOpenHandled={() => undefined}
        onSave={onSave}
      />,
    );

    expect(clarificationSection("exact_stop_count")).toBe("advanced");
    expect(clarificationSection("max_distance_km")).toBe("advanced");
    expect(clarificationSection("total_distance_km")).toBe("advanced");
    await user.click(screen.getByRole("button", { name: /^更多条件/ }));
    await user.type(screen.getByLabelText("精确站数"), "3");
    await user.type(screen.getByLabelText("检索及单段距离上限（km）"), "7.5");
    await user.type(screen.getByLabelText("全程累计距离上限（km）"), "18");
    await user.click(screen.getByRole("button", { name: "保存条件" }));

    expect(onSave).toHaveBeenCalledWith({
      base_revision: 4,
      advanced: {
        exact_stop_count: { operation: "set", value: 3 },
        max_distance_km: { operation: "set", value: 7.5 },
        total_distance_km: { operation: "set", value: 18 },
      },
    });
  });

  it("clears only the selected advanced limit and blocks invalid values", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const constrainedContext: PlanningContextSummary = {
      ...context,
      exact_stop_count: { value: 3, display_value: "3 个站点", source: "user", editable: true, status: "resolved" },
      max_distance_km: { value: 8, display_value: "8 km", source: "user", editable: true, status: "resolved" },
      total_distance_km: { value: 24, display_value: "24 km", source: "user", editable: true, status: "resolved" },
    };
    render(
      <PlanningContextBar
        context={constrainedContext}
        sessionId="session-1"
        busy={false}
        externalOpen={null}
        onExternalOpenHandled={() => undefined}
        onSave={onSave}
      />,
    );

    await user.click(screen.getByRole("button", { name: /^更多条件/ }));
    const maxDistanceInput = screen.getByLabelText("检索及单段距离上限（km）");
    await user.click(maxDistanceInput.closest(".context-advanced-field")!.querySelector("button")!);
    await user.click(screen.getByRole("button", { name: "保存条件" }));
    expect(onSave).toHaveBeenCalledWith({
      base_revision: 4,
      advanced: { max_distance_km: { operation: "clear" } },
    });

    await user.click(screen.getByRole("button", { name: /^更多条件/ }));
    await user.clear(screen.getByLabelText("检索及单段距离上限（km）"));
    await user.type(screen.getByLabelText("检索及单段距离上限（km）"), "0");
    await user.click(screen.getByRole("button", { name: "保存条件" }));
    expect(screen.getByRole("alert")).toHaveTextContent("距离上限需为有限且大于 0 的公里数");
    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it("keeps the pet preference in the ordinary preferences list across both editors", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const view = render(
      <PlanningContextBar
        context={context}
        sessionId="session-1"
        busy={false}
        externalOpen={null}
        onExternalOpenHandled={() => undefined}
        onSave={onSave}
      />,
    );

    await user.click(screen.getByRole("button", { name: /修改同行人数/ }));
    await user.click(screen.getByRole("checkbox", { name: /携带宠物/ }));
    expect(screen.getByText("作为出行偏好，地点是否允许携宠尚未核实。")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "保存条件" }));
    expect(onSave).toHaveBeenCalledWith({
      base_revision: 4,
      preferences: { add: [{ category: "preferences", value: "携带宠物" }], remove: [] },
    });

    const petContext: PlanningContextSummary = {
      ...context,
      preferences: {
        ...context.preferences,
        value: { preferences: ["携带宠物"], diet_tags: [], scene_tags: [], avoid: [] },
      },
    };
    view.rerender(
      <PlanningContextBar
        context={petContext}
        sessionId="session-1"
        busy={false}
        externalOpen={null}
        onExternalOpenHandled={() => undefined}
        onSave={onSave}
      />,
    );
    await user.click(screen.getByRole("button", { name: /修改出行偏好/ }));
    expect(screen.getByRole("button", { name: "移除体验偏好 携带宠物" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "移除体验偏好 携带宠物" }));
    await user.click(screen.getByRole("button", { name: "保存条件" }));
    expect(onSave).toHaveBeenLastCalledWith({
      base_revision: 4,
      preferences: { add: [], remove: [{ category: "preferences", value: "携带宠物" }] },
    });

    view.rerender(
      <PlanningContextBar
        context={context}
        sessionId="session-1"
        busy={false}
        externalOpen={null}
        onExternalOpenHandled={() => undefined}
        onSave={onSave}
      />,
    );
    await user.click(screen.getByRole("button", { name: /修改同行人数/ }));
    expect(screen.getByRole("checkbox", { name: /携带宠物/ })).not.toBeChecked();
  });
});
