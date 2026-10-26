import { afterEach, expect, it, vi } from "vitest";
import { createClientId } from "./clientId";

afterEach(() => vi.unstubAllGlobals());

it("uses native UUID generation when available", () => {
  const randomUUID = vi.fn(() => "native-uuid");
  vi.stubGlobal("crypto", { randomUUID });
  expect(createClientId()).toBe("native-uuid");
  expect(randomUUID).toHaveBeenCalledTimes(1);
});

it("uses all 16 random bytes and sets UUID v4 version and variant without randomUUID", () => {
  const getRandomValues = vi.fn((bytes: Uint8Array) => {
    bytes.fill(0xff);
    bytes[0] = 0;
    return bytes;
  });
  vi.stubGlobal("crypto", { getRandomValues });
  expect(createClientId()).toBe("00ffffff-ffff-4fff-bfff-ffffffffffff");
  expect(getRandomValues).toHaveBeenCalledTimes(1);
  expect(getRandomValues.mock.calls[0][0]).toHaveLength(16);
});
