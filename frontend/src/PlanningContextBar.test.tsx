import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { clarificationSection, PlanningContextBar, PlanningContextNotice } from "./PlanningContextBar";
import type { PlanningContextSummary } from "./types";

const api = vi.hoisted(() => ({ searchDepartureLocations: vi.fn() }));
vi.mock("./api", () => api);

const departureSearch = {
  city: "北京市" as const,
  source: "mock" as const,
  mode: "mock" as const,
  degraded: false,
  results: [
    { kind: "provider_place" as const, provider_id: "mock:guomao-1", name: "国贸地铁站", city: "北京市" as const, district: "朝阳区", address: "北京市朝阳区建国门外大街" },
    { kind: "provider_place" as const, provider_id: "mock:guomao-2", name: "中国国际贸易中心", city: "北京市" as const, district: "朝阳区", address: "北京市朝阳区建国门外大街 1 号" },
  ],
};

beforeEach(() => {
  api.searchDepartureLocations.mockReset().mockResolvedValue(departureSearch);
});

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
  it("names the current origin and explains the fixed demo default in the editor", async () => {
    const user = userEvent.setup();
    render(<PlanningContextBar context={context} sessionId="session-1" busy={false} externalOpen={null} onExternalOpenHandled={() => undefined} onSave={vi.fn().mockResolvedValue(undefined)} />);

    const trigger = screen.getByRole("button", { name: "修改出发地，当前演示默认出发点" });
    expect(trigger).toBeInTheDocument();
    await user.click(trigger);

    expect(screen.getByRole("heading", { name: "从哪出发" })).toBeInTheDocument();
    const summary = screen.getByLabelText("当前出发地");
    expect(summary).toHaveTextContent("演示默认出发点");
    expect(summary).toHaveTextContent("北京市朝阳区");
    expect(summary).toHaveTextContent("固定位置，并非你的实时位置");
    expect(screen.getByText("仅支持北京市。出发地用于计算前往首站的交通；未指定其他活动范围时，也会影响附近候选。出发地搜索与规划 POI 候选目录相互独立。")).toBeInTheDocument();
  });

  it("labels a clarification-required origin as pending", async () => {
    const user = userEvent.setup();
    const pendingContext = {
      ...context,
      where: { ...context.where, status: "pending" as const },
      pending_field: "location",
    };
    render(<PlanningContextBar context={pendingContext} sessionId="session-1" busy={false} externalOpen={null} onExternalOpenHandled={() => undefined} onSave={vi.fn().mockResolvedValue(undefined)} />);

    await user.click(screen.getByRole("button", { name: /修改出发地/ }));
    expect(screen.getByLabelText("当前出发地")).toHaveTextContent("待确认 · 请核对当前出发地");
  });

  it("shows the selected origin and preserves it after save failure", async () => {
    const user = userEvent.setup();
    const selectedContext = {
      ...context,
      where: {
        value: { name: "国贸地铁站", address: "北京市朝阳区建国门外大街 1 号" },
        display_value: "北京市朝阳区建国门外大街 1 号",
        source: "user" as const,
        editable: true,
        status: "resolved" as const,
      },
    };
    const onSave = vi.fn().mockRejectedValue(new Error("地点无法确认，请重新选择。"));
    render(<PlanningContextBar context={selectedContext} sessionId="session-1" busy={false} externalOpen={null} onExternalOpenHandled={() => undefined} onSave={onSave} />);

    await user.click(screen.getByRole("button", { name: "修改出发地，当前国贸地铁站" }));
    const summary = screen.getByLabelText("当前出发地");
    expect(summary).toHaveTextContent("国贸地铁站");
    expect(summary).toHaveTextContent("北京市朝阳区建国门外大街 1 号");
    expect(summary).toHaveTextContent("用户已选择");

    await user.type(screen.getByLabelText("搜索出发地点 北京市"), "国贸");
    await user.click(await screen.findByRole("option", { name: /国贸地铁站/ }));
    await user.click(screen.getByRole("button", { name: "保存条件" }));
    expect(await screen.findByText("修改失败 · 当前出发地未变")).toBeInTheDocument();
    expect(summary).toHaveTextContent("国贸地铁站");
  });

  it("searches without saving, then lets the keyboard choose an identified place", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PlanningContextBar context={context} sessionId="session-1" busy={false} externalOpen={null} onExternalOpenHandled={() => undefined} onSave={onSave} />);

    await user.click(screen.getByRole("button", { name: /修改出发地/ }));
    const input = screen.getByRole("combobox", { name: /搜索出发地点/ });
    await user.type(input, "国贸");
    expect(onSave).not.toHaveBeenCalled();
    expect(await screen.findByRole("option", { name: /中国国际贸易中心/ })).toBeInTheDocument();
    const listbox = screen.getByRole("listbox", { name: "出发地候选" });
    expect(listbox.closest(".context-form-content")).toBeNull();
    expect(listbox.closest(".departure-search-panel")).toBeInTheDocument();
    expect(api.searchDepartureLocations).toHaveBeenCalledWith("国贸", expect.any(AbortSignal));
    expect(onSave).not.toHaveBeenCalled();

    await user.keyboard("{ArrowDown}{ArrowDown}{Enter}");
    expect(screen.getByLabelText("待保存出发地")).toHaveTextContent("中国国际贸易中心");
    expect(onSave).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "保存条件" }));
    expect(onSave).toHaveBeenCalledWith({
      base_revision: 4,
      where: { operation: "set", value: { kind: "provider_place", provider_id: "mock:guomao-2" } },
    });
  });

  it("does not save an unselected query and clearly asks for a search result", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PlanningContextBar context={context} sessionId="session-1" busy={false} externalOpen={null} onExternalOpenHandled={() => undefined} onSave={onSave} />);

    await user.click(screen.getByRole("button", { name: /修改出发地/ }));
    await user.type(screen.getByRole("combobox", { name: /搜索出发地点/ }), "国贸");
    await screen.findByRole("option", { name: /国贸地铁站/ });
    expect(screen.getByRole("button", { name: "保存条件" })).toBeDisabled();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("输入文字本身不会保存为出发地");
    expect(onSave).not.toHaveBeenCalled();
  });

  it("clears a pending result when the query changes and lets Escape close suggestions before the dialog", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PlanningContextBar context={context} sessionId="session-1" busy={false} externalOpen={null} onExternalOpenHandled={() => undefined} onSave={onSave} />);

    await user.click(screen.getByRole("button", { name: /修改出发地/ }));
    const input = screen.getByRole("combobox", { name: /搜索出发地点/ });
    await user.type(input, "国贸");
    await user.click(await screen.findByRole("option", { name: /国贸地铁站/ }));
    expect(screen.getByLabelText("待保存出发地")).toHaveTextContent("国贸地铁站");
    await user.type(input, "站");
    expect(screen.queryByLabelText("待保存出发地")).not.toBeInTheDocument();
    expect(screen.getByText("搜索内容已更改，请从新结果中重新选择出发地。")).toBeInTheDocument();

    await screen.findByRole("option", { name: /国贸地铁站/ });
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("option")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(onSave).not.toHaveBeenCalled();
  });

  it("restores the exact server default through a typed selection", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const selectedContext = {
      ...context,
      where: {
        value: { name: "国贸地铁站", address: "北京市朝阳区建国门外大街", provider_place_id: "amap:current" },
        display_value: "国贸地铁站", source: "user" as const, editable: true, status: "resolved" as const,
      },
    };
    render(<PlanningContextBar context={selectedContext} sessionId="session-1" busy={false} externalOpen={null} onExternalOpenHandled={() => undefined} onSave={onSave} />);

    await user.click(screen.getByRole("button", { name: /修改出发地/ }));
    await user.click(screen.getByRole("button", { name: "使用演示默认出发点" }));
    expect(screen.getByLabelText("待保存出发地")).toHaveTextContent("演示默认出发点");
    await user.click(screen.getByRole("button", { name: "保存条件" }));
    expect(onSave).toHaveBeenCalledWith({
      base_revision: 4,
      where: { operation: "set", value: { kind: "default" } },
    });
  });

  it("cancels stale searches when the query changes and ignores their late results", async () => {
    const user = userEvent.setup();
    let resolveFirst: ((value: typeof departureSearch) => void) | undefined;
    let firstSignal: AbortSignal | undefined;
    api.searchDepartureLocations.mockImplementation((query: string, signal: AbortSignal) => {
      if (query === "国贸") {
        firstSignal = signal;
        return new Promise((resolve) => { resolveFirst = resolve; });
      }
      return Promise.resolve({ ...departureSearch, results: [
        { kind: "provider_place", provider_id: "mock:huangzhuang", name: "海淀黄庄", city: "北京市", district: "海淀区", address: "北京市海淀区中关村大街" },
      ] });
    });
    render(<PlanningContextBar context={context} sessionId="session-1" busy={false} externalOpen={null} onExternalOpenHandled={() => undefined} onSave={vi.fn().mockResolvedValue(undefined)} />);

    await user.click(screen.getByRole("button", { name: /修改出发地/ }));
    const input = screen.getByRole("combobox", { name: /搜索出发地点/ });
    await user.type(input, "国贸");
    await waitFor(() => expect(api.searchDepartureLocations).toHaveBeenCalledWith("国贸", expect.any(AbortSignal)));
    await user.clear(input);
    await user.type(input, "海淀");
    expect(await screen.findByRole("option", { name: /海淀黄庄/ })).toBeInTheDocument();
    expect(firstSignal?.aborted).toBe(true);
    resolveFirst?.(departureSearch);
    await waitFor(() => expect(screen.queryByRole("option", { name: /国贸地铁站/ })).not.toBeInTheDocument());
    expect(screen.getByRole("option", { name: /海淀黄庄/ })).toBeInTheDocument();
  });

  it("shows a clear limited-demo notice when offline search returns demo places", async () => {
    const user = userEvent.setup();
    api.searchDepartureLocations.mockResolvedValue({ ...departureSearch, degraded: true });
    render(<PlanningContextBar context={context} sessionId="session-1" busy={false} externalOpen={null} onExternalOpenHandled={() => undefined} onSave={vi.fn().mockResolvedValue(undefined)} />);
    await user.click(screen.getByRole("button", { name: /修改出发地/ }));
    await user.type(screen.getByRole("combobox", { name: /搜索出发地点/ }), "国贸");
    expect(await screen.findByText(/在线搜索暂不可用，以下是有限的演示地点/)).toBeInTheDocument();
  });

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

    await user.click(screen.getByRole("button", { name: /修改出发地/ }));
    await user.type(screen.getByLabelText("搜索出发地点 北京市"), "国贸");
    await user.click(await screen.findByRole("option", { name: /国贸地铁站/ }));
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
      { base_revision: 4, where: { operation: "set", value: { kind: "provider_place", provider_id: "mock:guomao-1" } } },
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

    // A text query without a chosen result is never a departure change. A budget
    // save used to be submitted unconditionally, which marked a still-valid
    // plan as stale and pushed the user into a pointless re-plan.
    await user.click(screen.getByRole("button", { name: /修改出发地/ }));
    expect(screen.getByRole("button", { name: "保存条件" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /^取消$/ }));
    for (const trigger of [/修改日期时间/, /修改同行人数/, /修改人均预算/, /修改出行偏好/]) {
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
    const trigger = screen.getByRole("button", { name: /修改出发地/ });
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

    await user.click(screen.getByRole("button", { name: /修改出发地/ }));
    await user.type(screen.getByLabelText("搜索出发地点 北京市"), "国贸");
    await user.click(await screen.findByRole("option", { name: /国贸地铁站/ }));
    await user.click(screen.getByRole("button", { name: "保存条件" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("revision conflict");
    expect(screen.getByLabelText("搜索出发地点 北京市")).toHaveValue("国贸");
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
