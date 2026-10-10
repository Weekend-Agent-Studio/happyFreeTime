import type { PlanningRunEvent, RunEventStatus, RunStage } from "./types";

export type RunStageViewModel = {
  stage: RunStage;
  label: string;
  status: "pending" | "active" | "completed" | "fallback" | "failed" | "waiting_input";
  message?: string;
  durationMs?: number;
};

const STAGE_LABELS: Record<RunStage, string> = {
  understand: "理解需求",
  compile_request: "整理规划条件",
  clarify: "等待补充信息",
  structure: "设计行程结构",
  retrieve: "检索候选地点",
  construct: "组合候选行程",
  verify: "核对路线和硬约束",
  modify: "处理方案修改",
  recovery: "处理规划失败",
  auto_recovery: "尝试自动调整默认条件",
  recovery_action: "应用恢复操作",
  advise: "生成推荐说明",
};

const CREATE_STAGES: RunStage[] = [
  "understand",
  "compile_request",
  "structure",
  "retrieve",
  "construct",
  "verify",
  "advise",
];

function statusForEvent(status: RunEventStatus): RunStageViewModel["status"] {
  if (status === "started") return "active";
  return status;
}

/** Project low-level start/terminal events into one user-facing row per stage. */
export function projectRunProgress(events: PlanningRunEvent[]): RunStageViewModel[] {
  const ordered = [...events].sort((left, right) => left.sequence - right.sequence);
  const byStage = new Map<RunStage, RunStageViewModel>();

  for (const event of ordered) {
    const previous = byStage.get(event.stage);
    byStage.set(event.stage, {
      stage: event.stage,
      label: STAGE_LABELS[event.stage],
      status: statusForEvent(event.status),
      message: event.public_message,
      durationMs: event.duration_ms ?? previous?.durationMs,
    });
  }

  // Once structure planning has started we know this is the full create flow.
  // Add only genuinely applicable future stages; clarification and modification
  // flows intentionally stay sparse and never receive this seven-stage list.
  if (byStage.has("structure") && !byStage.has("clarify") && !byStage.has("modify")) {
    return CREATE_STAGES.map((stage) => byStage.get(stage) ?? {
      stage,
      label: STAGE_LABELS[stage],
      status: "pending",
    });
  }

  return [...byStage.values()];
}

