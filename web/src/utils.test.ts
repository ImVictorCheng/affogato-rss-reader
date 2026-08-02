import { afterEach, describe, expect, it, vi } from "vitest";
import { uniqueRequestKey } from "./utils";

describe("uniqueRequestKey", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("falls back to getRandomValues when randomUUID is unavailable", () => {
    vi.stubGlobal("crypto", {
      getRandomValues(bytes: Uint8Array) {
        bytes.fill(10);
        return bytes;
      },
    });

    expect(uniqueRequestKey("brief")).toBe(
      "brief-0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a",
    );
  });
});
