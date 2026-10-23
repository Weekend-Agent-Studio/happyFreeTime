// Page-lifetime only. Never write the capability or API Key to browser storage.
export const publicDemoEnabled = import.meta.env.VITE_PUBLIC_DEMO === "true";
let visitorToken = "";
let bootstrap: Promise<void> | null = null;
let mode: "demo" | "llm" = "demo";
const listeners = new Set<() => void>();

export const subscribeDemoMode = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export const getDemoMode = () => mode;

export function requestIdentity(): Record<string, string> {
  if (!publicDemoEnabled) return { "X-User-Id": "demo-user" };
  if (!visitorToken) throw new Error("临时体验尚未连接，请刷新页面重试。");
  return { "X-Demo-Token": visitorToken };
}

export function initializePublicDemo(): Promise<void> {
  if (!publicDemoEnabled) return Promise.resolve();
  if (!bootstrap) bootstrap = (async () => {
    const config = await fetch("/api/config/runtime", { cache: "no-store" });
    if (!config.ok || !(await config.json()).data?.public_demo) throw new Error("公网体验配置不一致，请联系站点维护者。");
    const response = await fetch("/api/demo/visitors", { method: "POST", cache: "no-store" });
    if (!response.ok) throw new Error(response.status === 429 ? "体验人数较多，请稍后重新连接。" : "暂时无法连接体验服务，请重试。");
    const body = await response.json();
    visitorToken = body.data.token;
    // A restored URL belongs to the previous page's capability, not this visitor.
    const url = new URL(window.location.href);
    url.searchParams.delete("session");
    window.history.replaceState(null, "", url);
  })().catch((error) => { bootstrap = null; throw error; });
  return bootstrap;
}

export async function configureDemoModel(apiKey: string | null): Promise<void> {
  const response = await fetch("/api/demo/model", {
    method: "PUT", headers: { ...requestIdentity(), "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }), cache: "no-store",
  });
  if (!response.ok) {
    // Do not reflect arbitrary provider/validation responses into a credential UI.
    throw new Error(response.status === 401 ? "体验已过期，请刷新页面。" : response.status === 409 ? "请等待规划完成后再更换配置。" : response.status === 429 ? "操作频繁，请稍后再试。" : "配置未生效，请检查 Key 格式或稍后重试。");
  }
  const body = await response.json();
  mode = body.data.mode === "llm" ? "llm" : "demo";
  listeners.forEach((listener) => listener());
}
