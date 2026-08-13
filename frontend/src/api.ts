import type { AgentResponse } from "./types";

// M1 使用固定 Demo 用户，但后端所有数据仍按 user_id 隔离。接入匿名身份或
// 登录后，只需在这一层替换身份获取方式，业务组件不需要散落认证逻辑。
const USER_HEADER = { "X-User-Id": "demo-user" };

async function readJson<T>(response: Response): Promise<T> {
  // 集中处理 HTTP 错误，App 只接收正常数据或一个可直接展示的 Error。
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? `请求失败 (${response.status})`);
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
): Promise<AgentResponse> {
  // 同一接口同时承载初始目标和反问答案；后端根据 checkpoint 判断语义。
  const response = await fetch(`/api/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { ...USER_HEADER, "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  const body = await readJson<{ data: AgentResponse }>(response);
  return body.data;
}
