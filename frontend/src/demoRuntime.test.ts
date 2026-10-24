import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.resetModules(); });
describe("public demo capabilities", () => {
  it("bootstraps once per page and keeps credentials out of browser storage", async () => {
    vi.stubEnv("VITE_PUBLIC_DEMO", "true"); vi.resetModules();
    window.history.replaceState(null, "", "/?session=old-session");
    const store = vi.spyOn(Storage.prototype, "setItem");
    const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ data: { public_demo: true } })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: { token: "page-only-token" } })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: { mode: "llm" } })));
    vi.stubGlobal("fetch", fetch);
    const runtime = await import("./demoRuntime");
    await Promise.all([runtime.initializePublicDemo(), runtime.initializePublicDemo()]);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(runtime.requestIdentity()).toEqual({ "X-Demo-Token": "page-only-token" });
    expect(window.location.search).toBe("");
    await runtime.configureDemoModel("private-key");
    expect(runtime.getDemoMode()).toBe("llm");
    expect(store).not.toHaveBeenCalled(); store.mockRestore();
    vi.resetModules();
    const newPage = await import("./demoRuntime");
    expect(() => newPage.requestIdentity()).toThrow("尚未连接");
    expect(newPage.getDemoMode()).toBe("demo");
  });
  it("fails closed when runtime configuration is inconsistent", async () => {
    vi.stubEnv("VITE_PUBLIC_DEMO", "true"); vi.resetModules();
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: { public_demo: false } })));
    vi.stubGlobal("fetch", fetch);
    const runtime = await import("./demoRuntime");
    await expect(runtime.initializePublicDemo()).rejects.toThrow("配置不一致");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(() => runtime.requestIdentity()).toThrow();
  });
});
