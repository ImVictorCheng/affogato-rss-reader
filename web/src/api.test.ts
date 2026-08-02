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
});
