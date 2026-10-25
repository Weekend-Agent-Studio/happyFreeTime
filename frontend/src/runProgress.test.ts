import { describe, expect, it } from "vitest";

import { currentRunStage, formatRunDuration, projectRunProgress } from "./runProgress";
import type { PlanningRunEvent } from "./types";

function event(
  sequence: number,
  stage: PlanningRunEvent["stage"],
  status: PlanningRunEvent["status"],
  message = `${stage} ${status}`,
): PlanningRunEvent {
  return {
    schema_version: "planning-run-event.v1",
    run_id: "run-test",
    sequence,
    stage,
    status,
    message_key: `${stage}.${status}`,
    public_message: message,
    occurred_at: "2026-10-07T10:00:00Z",
    duration_ms: status === "started" ? null : 12,
    public_details: {},
  };
}

describe("projectRunProgress", () => {
  it("collapses started and terminal events into one row per stage", () => {
    const rows = projectRunProgress([
      event(2, "understand", "completed", "需求理解完成"),
      event(1, "understand", "started", "正在理解需求"),
      event(3, "structure", "started", "正在设计结构"),
    ]);

    expect(rows.map((row) => row.stage)).toEqual([
      "understand", "compile_request", "structure", "retrieve", "construct", "verify", "advise",
    ]);
    expect(rows[0]).toMatchObject({ status: "completed", message: "需求理解完成", durationMs: 12 });
    expect(rows[2]).toMatchObject({ status: "active", message: "正在设计结构" });
  });

  it("adds pending create stages only after structure planning begins", () => {
    const rows = projectRunProgress([event(1, "structure", "started", "正在设计结构")]);
    expect(rows.map((row) => row.stage)).toEqual([
      "understand", "compile_request", "structure", "retrieve", "construct", "verify", "advise",
    ]);
    expect(rows.find((row) => row.stage === "retrieve")?.status).toBe("pending");
  });

  it("keeps each retry in chronological order and resets its duration", () => {
    const rows = projectRunProgress([
      event(1, "structure", "started", "正在组织候选行程结构"),
      event(2, "structure", "completed", "行程结构已确定"),
      event(3, "structure", "started", "正在重新组织候选行程结构"),
    ]);

    const attempts = rows.filter((row) => row.stage === "structure");
    expect(attempts).toHaveLength(2);
    expect(attempts[0]).toMatchObject({ status: "completed", durationMs: 12 });
    expect(attempts[1]).toMatchObject({ status: "active", message: "正在重新组织候选行程结构" });
    expect(attempts[1].durationMs).toBeUndefined();
  });

  it("keeps clarification and modification flows sparse", () => {
    const clarification = projectRunProgress([
      event(1, "compile_request", "started"),
      event(2, "clarify", "started"),
      event(3, "clarify", "waiting_input"),
    ]);
    const modification = projectRunProgress([
      event(1, "modify", "started"),
      event(2, "retrieve", "started"),
      event(3, "retrieve", "fallback"),
    ]);

    expect(clarification.map((row) => row.stage)).toEqual(["compile_request", "clarify"]);
    expect(modification.map((row) => row.stage)).toEqual(["modify", "retrieve"]);
    expect(modification[1].status).toBe("fallback");
  });

  it("keeps recovery and repeated modification steps visible with parent links", () => {
    const rows = projectRunProgress([
      event(1, "modify", "started"), event(2, "retrieve", "started"), event(3, "retrieve", "fallback"),
      event(4, "verify", "started"), event(5, "verify", "completed"), event(6, "modify", "completed"),
      event(7, "recovery", "started"), event(8, "auto_recovery", "started"), event(9, "auto_recovery", "completed"),
      event(10, "modify", "started"), event(11, "retrieve", "started"),
    ]);
    expect(rows.map((row) => row.stage)).toEqual(["modify", "retrieve", "verify", "recovery", "auto_recovery", "modify", "retrieve"]);
    expect(rows[1]).toMatchObject({ parentId: rows[0].id });
    expect(rows[5]).toMatchObject({ parentId: rows[3].id });
    expect(rows[6]).toMatchObject({ parentId: rows[5].id });
    expect(new Set(rows.map((row) => row.id)).size).toBe(rows.length);
    expect(currentRunStage(rows)?.id).toBe(rows[6].id);
    expect(rows[1].status).toBe("fallback");
  });

  it("never invents completion for an unfinished recovery", () => {
    const rows = projectRunProgress([event(1, "recovery", "started"), event(2, "advise", "started"), event(3, "advise", "completed")]);
    expect(rows[0].status).toBe("active");
    expect(currentRunStage(rows)?.stage).toBe("recovery");
  });

  it.each([[0, "0 毫秒"], [83, "83 毫秒"], [1000, "1.0 秒"], [18123, "18.1 秒"]])("formats %s for people", (duration, text) => {
    expect(formatRunDuration(duration as number)).toBe(text);
  });
});
