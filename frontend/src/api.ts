import type { AgentResponse, ConversationCommand, SessionSummary, SessionView, WebMapConfig } from "./types";

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
): Promise<AgentResponse> {
  // 同一接口同时承载初始目标和反问答案；后端根据 checkpoint 判断语义。
  const response = await fetch(`/api/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { ...USER_HEADER, "Content-Type": "application/json" },
    body: JSON.stringify({
      request_id: requestId,
      content,
      ...(conversationCommand ? { conversation_command: conversationCommand } : {}),
    }),
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
