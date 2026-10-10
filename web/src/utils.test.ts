import { afterEach, describe, expect, it, vi } from "vitest";
import type { Entry } from "./types";
import { entryDate, uniqueRequestKey } from "./utils";

describe("entryDate", () => {
  const entry: Entry = {
    id: 1, title: "Paper", url: "https://example.test/paper", authors: [], tags: [], domains: [],
    state: { read: false, starred: false, later: false, archived: false },
    published_at: "2026-10-08T10:00:00Z", source_updated_at: "2026-10-09T10:00:00Z",
    created_at: "2026-10-10T10:00:00Z", updated_at: "2026-10-11T10:00:00Z",
  };
  it("prefers source update, then publication, then collection", () => {
    expect(entryDate(entry)).toEqual({ value: entry.source_updated_at, source: "updated" });
    expect(entryDate({ ...entry, published_at: null })).toEqual({ value: entry.source_updated_at, source: "updated" });
    expect(entryDate({ ...entry, source_updated_at: null })).toEqual({ value: entry.published_at, source: "published" });
    expect(entryDate({ ...entry, published_at: null, source_updated_at: null })).toEqual({ value: entry.created_at, source: "collected" });
  });
  it("skips invalid dates and never uses an internal modification timestamp", () => {
    expect(entryDate({ ...entry, published_at: "invalid", source_updated_at: "invalid" })).toEqual({ value: entry.created_at, source: "collected" });
    expect(entryDate({ ...entry, published_at: null, source_updated_at: null, created_at: null })).toEqual({ value: null, source: null });
  });
});

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
