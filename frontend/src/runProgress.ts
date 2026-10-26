import type { PlanningRunEvent, RunEventStatus, RunStage } from "./types";

export type RunStageViewModel = {
  id: string;
  parentId?: string;
  depth: number;
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
  recovery: "调整与恢复",
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

/** Pair each start with its terminal event, preserving retries and nesting. */
export function projectRunProgress(events: PlanningRunEvent[]): RunStageViewModel[] {
  const ordered = [...events].sort((left, right) => left.sequence - right.sequence);
  const rows: RunStageViewModel[] = [];
  const active = new Map<RunStage, number>();

  for (const event of ordered) {
    const existing = event.status === "started" ? undefined : active.get(event.stage);
    if (existing !== undefined) {
      rows[existing] = { ...rows[existing], status: statusForEvent(event.status), message: event.public_message, durationMs: event.duration_ms ?? undefined };
      active.delete(event.stage);
      continue;
    }
    const parentIndex = event.stage !== "modify" && active.has("modify")
      ? active.get("modify")
      : event.stage !== "recovery" ? active.get("recovery") : undefined;
    const parent = parentIndex !== undefined ? rows[parentIndex] : undefined;
    const index = rows.length;
    rows.push({
      id: `${event.run_id}:${event.sequence}`,
      parentId: parent?.id,
      depth: parent ? parent.depth + 1 : 0,
      stage: event.stage,
      label: STAGE_LABELS[event.stage],
      status: statusForEvent(event.status),
      message: event.public_message,
      durationMs: event.duration_ms ?? undefined,
    });
    if (event.status === "started") active.set(event.stage, index);
  }

  // Future placeholders apply only to a single ordinary create pass. Recovery
  // and retries use actual execution order instead of a fixed seven-stage list.
  const stages = new Set(rows.map((row) => row.stage));
  if (stages.has("structure") && stages.size === rows.length && rows.every((row) => CREATE_STAGES.includes(row.stage))) {
    return CREATE_STAGES.map((stage) => rows.find((row) => row.stage === stage) ?? {
      id: `pending:${stage}`,
      depth: 0,
      stage,
      label: STAGE_LABELS[stage],
      status: "pending",
    });
  }

  return rows;
}

export function currentRunStage(stages: RunStageViewModel[]): RunStageViewModel | undefined {
  return [...stages].reverse().find((stage) => stage.status === "active") ?? stages[stages.length - 1];
}

export function formatRunDuration(milliseconds: number): string {
  return milliseconds < 1000 ? `${milliseconds} 毫秒` : `${(milliseconds / 1000).toFixed(1)} 秒`;
}

