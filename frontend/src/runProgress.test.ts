import { describe, expect, it } from "vitest";

import { projectRunProgress } from "./runProgress";
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
});
