import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { AutoTagCleanupPreview, AutoTagPreview, AutoTagStatus } from "../types";
import { SettingsModal } from "./SettingsModal";

const autoTagStatus: AutoTagStatus = {
  enabled: false,
  create_new: true,
  growth_mode: "threshold",
  llm_connection_id: 1,
  llm_connection_name: "Test LLM",
  model: "test-model",
  configured: true,
  max_tags_per_entry: 3,
  promotion_threshold: 10,
  support_window_days: 365,
  canonical_language: "en",
  min_confidence: 0.8,
  preview_required: true,
  proposal_count: 0,
  promoted_count: 0,
  estimated_calls: 5,
  outdated_count: 0,
  needs_rebuild: false,
  pending_count: 0,
  running_count: 0,
  complete_count: 0,
  failed_count: 0,
};

function cleanupPreview(reviewed: boolean, token: string): AutoTagCleanupPreview {
  return {
    items: [],
    inferred_auto_association_count: 0,
    review_token: token.repeat(64),
    reviewed,
  };
}

function renderSettings(notify = vi.fn()) {
  render(<SettingsModal
    locale="en"
    auth={{ setup_required: false, authenticated: true, mode: "owner", csrf_token: "csrf" }}
    onLocale={vi.fn()}
    onClose={vi.fn()}
    onLogout={vi.fn()}
    onDebugReset={vi.fn()}
    onBrandChanged={vi.fn()}
    onInstallUpdate={vi.fn(async () => undefined)}
    notify={notify}
  />);
  return notify;
}

describe("SettingsModal governed auto-tag workflow", () => {
  it("relocks the trial run when creation fails after the review preflight", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.spyOn(api, "autoTagStatus").mockResolvedValue(autoTagStatus);
    vi.spyOn(api, "llmConnections").mockResolvedValue([{
      id: 1,
      name: "Test LLM",
      base_url: "https://example.test/v1",
      model: "test-model",
      api_key_configured: true,
      api_key_hint: "test…key",
      used_by: ["auto_tag"],
    }]);
    vi.spyOn(api, "feeds").mockResolvedValue([]);
    vi.spyOn(api, "folders").mockResolvedValue([]);
    vi.spyOn(api, "tags").mockResolvedValue([]);
    vi.spyOn(api, "domains").mockResolvedValue([]);
    vi.spyOn(api, "autoTagProposals").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(api, "autoTagPreviews").mockResolvedValue([]);
    const cleanup = vi.spyOn(api, "autoTagCleanupPreview")
      .mockResolvedValueOnce(cleanupPreview(false, "a"))
      .mockResolvedValueOnce(cleanupPreview(true, "a"))
      .mockResolvedValueOnce(cleanupPreview(true, "a"))
      .mockResolvedValueOnce(cleanupPreview(false, "b"));
    vi.spyOn(api, "cleanupAutoTags").mockResolvedValue({ removed_tag_ids: [], kept_tag_ids: [], removed_count: 0 });
    vi.spyOn(api, "createAutoTagPreview").mockRejectedValue(new Error("cleanup changed during creation"));
    const notify = vi.fn();

    renderSettings(notify);

    await user.click(screen.getByRole("button", { name: /Content/ }));
    await user.click(await screen.findByRole("button", { name: "Preview cleanup" }));
    await user.click(await screen.findByRole("button", { name: "Confirm cleanup review" }));
    const runPreview = screen.getByRole("button", { name: "Run 50-article preview" });
    await waitFor(() => expect(runPreview).toBeEnabled());

    await user.click(runPreview);

    await waitFor(() => expect(runPreview).toBeDisabled());
    expect(screen.getByRole("button", { name: "Confirm cleanup review" })).toBeVisible();
    expect(cleanup).toHaveBeenCalledTimes(4);
    expect(notify).toHaveBeenCalledWith("cleanup changed during creation", "error");
  });

  it("keeps a successful tag creation successful when both refreshes fail", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "autoTagStatus").mockResolvedValue(autoTagStatus);
    vi.spyOn(api, "llmConnections").mockResolvedValue([{
      id: 1,
      name: "Test LLM",
      base_url: "https://example.test/v1",
      model: "test-model",
      api_key_configured: true,
      api_key_hint: "test…key",
      used_by: ["auto_tag"],
    }]);
    vi.spyOn(api, "feeds").mockResolvedValue([]);
    vi.spyOn(api, "folders").mockResolvedValue([]);
    vi.spyOn(api, "domains").mockResolvedValue([]);
    vi.spyOn(api, "tags")
      .mockResolvedValueOnce([{
        id: 1,
        name: "Existing Topic",
        color: null,
        description: "",
        aliases: [],
        origin: "manual",
        auto_assignable: true,
        entry_count: 0,
      }])
      .mockRejectedValueOnce(new Error("tag refresh failed"));
    vi.spyOn(api, "autoTagProposals").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(api, "autoTagPreviews").mockResolvedValue([{
      id: 9,
      status: "complete",
      sample_size: 50,
      entry_ids: [],
      results: [],
      metrics: { policy_version: "old" },
      last_error: null,
    }]);
    vi.spyOn(api, "autoTagCleanupPreview").mockRejectedValue(new Error("cleanup refresh failed"));
    vi.spyOn(api, "createTag").mockResolvedValue({
      id: 2,
      name: "New Taxonomy Topic",
      color: null,
      description: "",
      aliases: [],
      origin: "manual",
      auto_assignable: true,
      entry_count: 0,
    });
    const notify = renderSettings();

    await user.click(screen.getByRole("button", { name: /Content/ }));
    await screen.findByText("Preview result", { exact: true });
    const createTagForm = screen.getByPlaceholderText("New tag").closest("form");
    expect(createTagForm).not.toBeNull();
    await user.type(screen.getByPlaceholderText("New tag"), "New Taxonomy Topic");
    await user.click(createTagForm!.querySelector("button")!);

    await screen.findByText("New Taxonomy Topic", { exact: true });
    expect(screen.queryByText("Preview result", { exact: true })).not.toBeInTheDocument();
    expect(notify).toHaveBeenCalledWith("Tag created.");
    expect(notify).not.toHaveBeenCalledWith("tag refresh failed", "error");
    expect(notify).not.toHaveBeenCalledWith("cleanup refresh failed", "error");
  });

  it("does not revive an old preview list response after a taxonomy mutation", async () => {
    const user = userEvent.setup();
    let resolvePreviews!: (previews: AutoTagPreview[]) => void;
    const previews = new Promise<AutoTagPreview[]>((resolve) => {
      resolvePreviews = resolve;
    });
    const previewRequest = vi.spyOn(api, "autoTagPreviews").mockReturnValue(previews);
    vi.spyOn(api, "autoTagStatus").mockResolvedValue(autoTagStatus);
    vi.spyOn(api, "llmConnections").mockResolvedValue([]);
    vi.spyOn(api, "feeds").mockResolvedValue([]);
    vi.spyOn(api, "folders").mockResolvedValue([]);
    vi.spyOn(api, "domains").mockResolvedValue([]);
    vi.spyOn(api, "tags").mockResolvedValue([]);
    vi.spyOn(api, "autoTagProposals").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(api, "autoTagCleanupPreview").mockRejectedValue(new Error("cleanup refresh failed"));
    vi.spyOn(api, "createTag").mockResolvedValue({
      id: 2,
      name: "New Taxonomy Topic",
      color: null,
      description: "",
      aliases: [],
      origin: "manual",
      auto_assignable: true,
      entry_count: 0,
    });
    const notify = renderSettings();

    await user.click(screen.getByRole("button", { name: /Content/ }));
    await waitFor(() => expect(previewRequest).toHaveBeenCalledTimes(1));
    const createTagForm = screen.getByPlaceholderText("New tag").closest("form");
    expect(createTagForm).not.toBeNull();
    await user.type(screen.getByPlaceholderText("New tag"), "New Taxonomy Topic");
    await user.click(createTagForm!.querySelector("button")!);
    await waitFor(() => expect(notify).toHaveBeenCalledWith("Tag created."));

    await act(async () => {
      resolvePreviews([{
        id: 9,
        status: "complete",
        sample_size: 50,
        entry_ids: [],
        results: [],
        metrics: { policy_version: "old" },
        last_error: null,
      }]);
      await previews;
    });

    expect(screen.queryByText("Preview result", { exact: true })).not.toBeInTheDocument();
  });

  it("does not let an older overlapping poll regress a newer preview status", async () => {
    const user = userEvent.setup();
    let poll!: () => void;
    vi.spyOn(window, "setInterval").mockImplementation((handler, timeout) => {
      if (timeout === 2000) poll = handler as () => void;
      return 1;
    });
    vi.spyOn(window, "clearInterval").mockImplementation(() => undefined);
    vi.spyOn(api, "autoTagStatus").mockResolvedValue(autoTagStatus);
    vi.spyOn(api, "llmConnections").mockResolvedValue([]);
    vi.spyOn(api, "feeds").mockResolvedValue([]);
    vi.spyOn(api, "folders").mockResolvedValue([]);
    vi.spyOn(api, "domains").mockResolvedValue([]);
    vi.spyOn(api, "tags").mockResolvedValue([]);
    vi.spyOn(api, "autoTagProposals").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(api, "autoTagPreviews").mockResolvedValue([{
      id: 9,
      status: "pending",
      sample_size: 50,
      entry_ids: [],
      results: [],
      metrics: {},
      last_error: null,
    }]);
    let resolveOlder!: (preview: AutoTagPreview) => void;
    let resolveNewer!: (preview: AutoTagPreview) => void;
    const older = new Promise<AutoTagPreview>((resolve) => { resolveOlder = resolve; });
    const newer = new Promise<AutoTagPreview>((resolve) => { resolveNewer = resolve; });
    vi.spyOn(api, "autoTagPreview")
      .mockReturnValueOnce(older)
      .mockReturnValueOnce(newer);

    renderSettings();
    await user.click(screen.getByRole("button", { name: /Content/ }));
    await screen.findByText("Pending · 50", { exact: true });
    await waitFor(() => expect(poll).toBeTypeOf("function"));
    act(() => poll());
    act(() => poll());

    await act(async () => {
      resolveNewer({
        id: 9,
        status: "complete",
        sample_size: 50,
        entry_ids: [],
        results: [],
        metrics: {},
        last_error: null,
      });
      await newer;
    });
    expect(screen.getByText("Complete · 50", { exact: true })).toBeVisible();

    await act(async () => {
      resolveOlder({
        id: 9,
        status: "pending",
        sample_size: 50,
        entry_ids: [],
        results: [],
        metrics: {},
        last_error: null,
      });
      await older;
    });
    expect(screen.getByText("Complete · 50", { exact: true })).toBeVisible();
    expect(screen.queryByText("Pending · 50", { exact: true })).not.toBeInTheDocument();
  });
});
