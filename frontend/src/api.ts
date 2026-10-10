import type { AgentResponse, ClarificationReply, ConversationCommand, PlanningContextPatch, PlanningRunEvent, RecoveryActionRequest, SessionSummary, SessionView, WebMapConfig } from "./types";

// M1 使用固定 Demo 用户，但后端所有数据仍按 user_id 隔离。接入匿名身份或
// 登录后，只需在这一层替换身份获取方式，业务组件不需要散落认证逻辑。
const USER_HEADER = { "X-User-Id": "demo-user" };
let mapConfigRequest: Promise<WebMapConfig> | null = null;

function formatErrorDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => formatErrorDetail(item, "")).filter(Boolean);
    if (messages.length) return messages.join("；");
  }
  if (detail && typeof detail === "object") {
    const item = detail as Record<string, unknown>;
    if (typeof item.message === "string" && item.message.trim()) return item.message;
    if (typeof item.detail === "string" && item.detail.trim()) return item.detail;
    if (typeof item.msg === "string" && item.msg.trim()) {
      const location = Array.isArray(item.loc) ? item.loc.filter((part) => typeof part === "string").join(".") : "";
      return location ? `${location}: ${item.msg}` : item.msg;
    }
    try {
      const serialized = JSON.stringify(detail);
      if (serialized && serialized !== "{}") return serialized;
    } catch {
      // Fall through to the HTTP status instead of exposing [object Object].
    }
  }
  return fallback;
}

async function readJson<T>(response: Response): Promise<T> {
  // 集中处理 HTTP 错误，App 只接收正常数据或一个可直接展示的 Error。
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(formatErrorDetail(body?.detail, `请求失败 (${response.status})`));
  }
  return response.json() as Promise<T>;
}

function messagePayload(
  content: string,
  requestId: string,
  conversationCommand?: ConversationCommand,
  clarificationReply?: ClarificationReply,
  recoveryAction?: RecoveryActionRequest,
) {
  return {
    request_id: requestId,
    content,
    ...(conversationCommand ? { conversation_command: conversationCommand } : {}),
    ...(clarificationReply ? { clarification_reply: clarificationReply } : {}),
    ...(recoveryAction ? { recovery_action: recoveryAction } : {}),
  };
}

export async function createSession(): Promise<string> {
  // 会话采用懒创建：用户真正发送第一条消息时才写入数据库。
  const response = await fetch("/api/sessions", {
    method: "POST",
    headers: USER_HEADER,
  });
  const body = await readJson<{ data: { session_id: string } }>(response);
  return body.data.session_id;
}

export async function sendMessage(
  sessionId: string,
  content: string,
  requestId: string,
  conversationCommand?: ConversationCommand,
  clarificationReply?: ClarificationReply,
  recoveryAction?: RecoveryActionRequest,
): Promise<AgentResponse> {
  // 同一接口同时承载初始目标和反问答案；后端根据 checkpoint 判断语义。
  const response = await fetch(`/api/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { ...USER_HEADER, "Content-Type": "application/json" },
    body: JSON.stringify(messagePayload(content, requestId, conversationCommand, clarificationReply, recoveryAction)),
  });
  const body = await readJson<{ data: AgentResponse }>(response);
  return body.data;
}

export async function sendMessageStream(
  sessionId: string,
  content: string,
  requestId: string,
  onProgress: (event: PlanningRunEvent) => void,
  conversationCommand?: ConversationCommand,
  clarificationReply?: ClarificationReply,
  recoveryAction?: RecoveryActionRequest,
): Promise<AgentResponse> {
  const response = await fetch(`/api/sessions/${sessionId}/messages/stream`, {
    method: "POST",
    headers: { ...USER_HEADER, "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(messagePayload(content, requestId, conversationCommand, clarificationReply, recoveryAction)),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(formatErrorDetail(body?.detail, `请求失败 (${response.status})`));
  }
  if (!response.body) throw new Error("服务未返回规划进度流");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: AgentResponse | null = null;

  const consumeFrame = (frame: string) => {
    let eventName = "message";
    let data = "";
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (!data) return;
    const payload = JSON.parse(data) as Record<string, unknown>;
    if (eventName === "progress") {
      onProgress(payload as unknown as PlanningRunEvent);
    } else if (eventName === "result") {
      const envelope = payload as { data?: AgentResponse };
      if (envelope.data) result = envelope.data;
    } else if (eventName === "error") {
      throw new Error(typeof payload.detail === "string" ? payload.detail : "规划执行失败，请稍后重试");
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    let separator = buffer.indexOf("\n\n");
    while (separator >= 0) {
      consumeFrame(buffer.slice(0, separator));
      buffer = buffer.slice(separator + 2);
      separator = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
  if (buffer.trim()) consumeFrame(buffer);
  if (!result) throw new Error("规划服务未返回最终结果");
  return result;
}

export async function updatePlanningContext(
  sessionId: string,
  patch: PlanningContextPatch,
  requestId: string,
): Promise<AgentResponse> {
  const response = await fetch(`/api/sessions/${sessionId}/planning-context`, {
    method: "PATCH",
    headers: { ...USER_HEADER, "Content-Type": "application/json" },
    body: JSON.stringify({ request_id: requestId, patch }),
  });
  const body = await readJson<{ data: AgentResponse }>(response);
  return body.data;
}

export async function replanPlanningContext(
  sessionId: string,
  requestId: string,
): Promise<AgentResponse> {
  const response = await fetch(`/api/sessions/${sessionId}/planning-context/replan`, {
    method: "POST",
    headers: { ...USER_HEADER, "Content-Type": "application/json" },
    body: JSON.stringify({ request_id: requestId }),
  });
  const body = await readJson<{ data: AgentResponse }>(response);
  return body.data;
}

export async function listSessions(limit = 5): Promise<SessionSummary[]> {
  const response = await fetch(`/api/sessions?limit=${limit}`, {
    headers: USER_HEADER,
  });
  const body = await readJson<{ data: { sessions: SessionSummary[] } }>(response);
  return body.data.sessions;
}

export async function renameSession(sessionId: string, title: string): Promise<void> {
  const response = await fetch(`/api/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { ...USER_HEADER, "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  await readJson<{ data: { session_id: string; title: string } }>(response);
}

export async function deleteSession(sessionId: string): Promise<void> {
  const response = await fetch(`/api/sessions/${sessionId}`, {
    method: "DELETE",
    headers: USER_HEADER,
  });
  await readJson<{ data: { deleted: boolean } }>(response);
}

export async function getSession(sessionId: string): Promise<SessionView> {
  const response = await fetch(`/api/sessions/${sessionId}`, {
    headers: USER_HEADER,
  });
  const body = await readJson<{ data: SessionView }>(response);
  return body.data;
}

export async function selectPlan(
  sessionId: string,
  planId: string,
): Promise<{ active_plan_version_id: string | null; selected_plan_id: string | null }> {
  const response = await fetch(`/api/sessions/${sessionId}/plans/${planId}/select`, {
    method: "POST",
    headers: USER_HEADER,
  });
  const body = await readJson<{ data: { active_plan_version_id: string | null; selected_plan_id: string | null } }>(response);
  return body.data;
}

export function getMapConfig(): Promise<WebMapConfig> {
  if (!mapConfigRequest) {
    mapConfigRequest = fetch("/api/config/map")
      .then((response) => readJson<{ data: WebMapConfig }>(response))
      .then((body) => body.data)
      .catch((error) => {
        mapConfigRequest = null;
        throw error;
      });
  }
  return mapConfigRequest;
}
