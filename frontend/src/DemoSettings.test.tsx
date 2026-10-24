import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DemoSettings } from "./DemoSettings";
import * as runtime from "./demoRuntime";

vi.mock("./demoRuntime", () => ({ publicDemoEnabled: true, configureDemoModel: vi.fn(), getDemoMode: () => "demo", subscribeDemoMode: () => () => {} }));

describe("page-only model settings", () => {
  beforeEach(() => vi.resetAllMocks());
  it("explains transmission and retention and clears the secret after configuration", async () => {
    vi.mocked(runtime.configureDemoModel).mockResolvedValue();
    const user = userEvent.setup();
    render(<DemoSettings />);
    await user.click(screen.getByRole("button", { name: "模型配置" }));
    expect(screen.getByText(/Key 会传给本站后端/)).toBeVisible();
    expect(screen.getByText(/不写入数据库/)).toBeVisible();
    const input = screen.getByLabelText("DeepSeek API Key");
    expect(input).toHaveAttribute("type", "password");
    await user.type(input, "sk-test-private-12345");
    await user.click(screen.getByRole("button", { name: "仅本次使用" }));
    expect(runtime.configureDemoModel).toHaveBeenCalledWith("sk-test-private-12345");
    expect(input).toHaveValue("");
    expect(await screen.findByRole("status")).toHaveTextContent("有效性将在实际调用时确认");
  });
  it("does not echo provider errors and removes an abandoned draft on close", async () => {
    vi.mocked(runtime.configureDemoModel).mockRejectedValue(new Error("配置未生效，请检查 Key 格式或稍后重试。"));
    const user = userEvent.setup(); render(<DemoSettings collapsed />);
    await user.click(screen.getByRole("button", { name: "模型配置" }));
    await user.type(screen.getByLabelText("DeepSeek API Key"), "sk-test-private-12345");
    await user.click(screen.getByRole("button", { name: "仅本次使用" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("配置未生效");
    expect(screen.getByLabelText("DeepSeek API Key")).toHaveValue("");
    await user.type(screen.getByLabelText("DeepSeek API Key"), "abandoned-key");
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "模型配置" }));
    expect(screen.getByLabelText("DeepSeek API Key")).toHaveValue("");
  });
});
