import { beforeEach, describe, expect, it, vi } from "vitest";

import { sendMessage } from "./api";

describe("frontend API errors", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders structured validation details instead of [object Object]", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        detail: [{ loc: ["body", "conversation_command"], msg: "字段不被允许", type: "extra_forbidden" }],
      }), { status: 422, headers: { "Content-Type": "application/json" } }),
    ));

    await expect(sendMessage("session", "更换第 1 站", "request-1234"))
      .rejects.toThrow("body.conversation_command: 字段不被允许");
  });

  it("prefers a business message from an object error detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: { code: "NO_REPLACEMENT_PLAN", message: "没有找到可行的替换方案" } }), { status: 409 }),
    ));

    await expect(sendMessage("session", "更换第 1 站", "request-1234"))
      .rejects.toThrow("没有找到可行的替换方案");
  });
});
