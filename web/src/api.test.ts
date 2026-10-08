import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("API client", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));

  it("sends the auth CSRF token with mutations", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json({ authenticated: true, setup_required: false, mode: "owner", csrf_token: "token" })).mockResolvedValueOnce(json({}));
    await api.authStatus();
    await api.updateEntryState(8, { read: true });
    const headers = new Headers(vi.mocked(fetch).mock.calls[1][1]?.headers);
    expect(headers.get("X-CSRF-Token")).toBe("token");
  });

  it("encodes repeated domain filters and ALL matching", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json({ items: [], total: 0, page: 1, per_page: 40 }));
    await api.entries({ domain_ids: [2, 5], domain_match: "all" });
    const url = String(vi.mocked(fetch).mock.calls[0][0]);
    expect(url).toContain("domain_ids=2");
    expect(url).toContain("domain_ids=5");
    expect(url).toContain("domain_match=all");
  });

  it("surfaces FastAPI detail messages", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json({ detail: "Invalid feed" }, 422));
    await expect(api.createFeed({ url: "invalid" })).rejects.toThrow("Invalid feed");
  });

  it("sends the controlled auto-tag policy without enabling legacy immediate creation", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json({ enabled: true }));
    await api.setAutoTagStatus({
      enabled: true,
      create_new: false,
      growth_mode: "threshold",
      llm_connection_id: 9,
      max_tags_per_entry: 3,
      promotion_threshold: 10,
      support_window_days: 365,
      canonical_language: "en",
    });
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain("/auto-tag/status");
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(String(init?.body))).toEqual({
      enabled: true,
      create_new: false,
      growth_mode: "threshold",
      llm_connection_id: 9,
      max_tags_per_entry: 3,
      promotion_threshold: 10,
      support_window_days: 365,
      canonical_language: "en",
    });
  });

  it("uses the preview, cleanup, proposal, and merge workflow endpoints", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(json({ items: [] }))
      .mockResolvedValueOnce(json({ id: 4, status: "pending", results: [] }))
      .mockResolvedValueOnce(json({ id: 4, status: "completed", results: [] }))
      .mockResolvedValueOnce(json({ preview_required: false }))
      .mockResolvedValueOnce(json({ items: [], inferred_auto_association_count: 0, review_token: "a".repeat(64), reviewed: false }))
      .mockResolvedValueOnce(json({ removed_tag_ids: [2], kept_tag_ids: [3], removed_count: 1 }))
      .mockResolvedValueOnce(json({ items: [], total: 0 }))
      .mockResolvedValueOnce(json({ id: 3, name: "target" }));

    await api.autoTagPreviews();
    await api.createAutoTagPreview({ sample_size: 50 });
    await api.autoTagPreview(4);
    await api.approveAutoTagPreview(4, { scope: "all" });
    await api.autoTagCleanupPreview();
    await api.cleanupAutoTags({ remove_tag_ids: [2], keep_tag_ids: [3], review_token: "a".repeat(64) });
    await api.autoTagProposals(10, 20);
    await api.mergeTags(2, 3);

    const calls = vi.mocked(fetch).mock.calls;
    expect(String(calls[0][0])).toContain("/auto-tag/previews");
    expect(calls[1][1]?.method).toBe("POST");
    expect(JSON.parse(String(calls[1][1]?.body))).toEqual({ sample_size: 50 });
    expect(String(calls[2][0])).toContain("/auto-tag/previews/4");
    expect(String(calls[3][0])).toContain("/auto-tag/previews/4/approve");
    expect(JSON.parse(String(calls[3][1]?.body))).toEqual({ scope: "all" });
    expect(String(calls[4][0])).toContain("/auto-tag/cleanup-preview");
    expect(JSON.parse(String(calls[5][1]?.body))).toEqual({ remove_tag_ids: [2], keep_tag_ids: [3], review_token: "a".repeat(64) });
    expect(String(calls[6][0])).toContain("/auto-tag/proposals?offset=10&limit=20");
    expect(String(calls[7][0])).toContain("/tags/2/merge/3");
  });

  it("can explicitly confirm an empty cleanup review before the trial run", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json({ removed_tag_ids: [], kept_tag_ids: [], removed_count: 0 }));

    await api.cleanupAutoTags({
      remove_tag_ids: [],
      keep_tag_ids: [],
      review_token: "b".repeat(64),
    });

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain("/auto-tag/cleanup");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      remove_tag_ids: [],
      keep_tag_ids: [],
      review_token: "b".repeat(64),
    });
  });

  it("sends canonical tag metadata and aliases on create and partial update", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(json({ id: 4, name: "Quantum Networks" }))
      .mockResolvedValueOnce(json({ id: 4, name: "Quantum Networks" }));

    await api.createTag({
      name: "Quantum Networks",
      description: "Distributed quantum communication.",
      aliases: ["QN", "量子网络"],
      auto_assignable: true,
    });
    await api.updateTag(4, {
      description: "Entanglement distribution and quantum repeaters.",
      aliases: ["Quantum networking"],
      auto_assignable: false,
    });

    const calls = vi.mocked(fetch).mock.calls;
    expect(JSON.parse(String(calls[0][1]?.body))).toEqual({
      name: "Quantum Networks",
      description: "Distributed quantum communication.",
      aliases: ["QN", "量子网络"],
      auto_assignable: true,
    });
    expect(String(calls[1][0])).toContain("/tags/4");
    expect(calls[1][1]?.method).toBe("PATCH");
    expect(JSON.parse(String(calls[1][1]?.body))).toEqual({
      description: "Entanglement distribution and quantum repeaters.",
      aliases: ["Quantum networking"],
      auto_assignable: false,
    });
  });
});
