import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { AutoTagPreview, AutoTagProposal, AutoTagStatus, Tag } from "../types";
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
  beforeEach(() => vi.stubEnv("VITE_AUTO_TAG_PREVIEW_ENABLED", "true"));
  it("runs and approves a preview without calling the dormant cleanup workflow", async () => {
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
    const cleanup = vi.spyOn(api, "autoTagCleanupPreview");
    const createPreview = vi.spyOn(api, "createAutoTagPreview").mockResolvedValue({
      id: 7, status: "complete", sample_size: 50, entry_ids: [], results: [], metrics: {}, last_error: null,
    });
    const approve = vi.spyOn(api, "approveAutoTagPreview").mockResolvedValue({
      ...autoTagStatus, enabled: true, preview_required: false,
    });
    const notify = vi.fn();

    renderSettings(notify);

    await user.click(screen.getByRole("button", { name: /Content/ }));
    const runPreview = await screen.findByRole("button", { name: "Run 50-article preview" });
    await waitFor(() => expect(runPreview).toBeEnabled());

    await user.click(runPreview);

    await user.click(await screen.findByRole("button", { name: "Approve full run" }));
    expect(createPreview).toHaveBeenCalledWith({ sample_size: 50 });
    expect(approve).toHaveBeenCalledWith(7, { scope: "all" });
    expect(cleanup).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Confirm cleanup review" })).not.toBeInTheDocument();
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

function mockTagManager() {
  let tags: Tag[] = ["AI", "Physics", "Technology"].map((name, index) => ({
    id: index + 1, name, color: null, origin: "legacy", entry_count: 8,
  }));
  vi.spyOn(api, "autoTagStatus").mockResolvedValue(autoTagStatus);
  vi.spyOn(api, "llmConnections").mockResolvedValue([]);
  vi.spyOn(api, "feeds").mockResolvedValue([]);
  vi.spyOn(api, "folders").mockResolvedValue([]);
  vi.spyOn(api, "domains").mockResolvedValue([]);
  vi.spyOn(api, "tags").mockImplementation(async () => tags);
  vi.spyOn(api, "autoTagProposals").mockResolvedValue({ items: [], total: 0 });
  vi.spyOn(api, "autoTagPreviews").mockResolvedValue([]);
  return vi.spyOn(api, "deleteTags").mockImplementation(async (ids) => {
    tags = tags.filter((tag) => !ids.includes(tag.id));
  });
}

describe("SettingsModal tag selection", () => {
  beforeEach(() => vi.stubEnv("VITE_AUTO_TAG_PREVIEW_ENABLED", "false"));
  it("selects all, inverts a partial selection, and deletes only selected tags", async () => {
    const user = userEvent.setup();
    const remove = mockTagManager();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const notify = renderSettings();
    await user.click(screen.getByRole("button", { name: /Content/ }));
    const ai = await screen.findByRole("checkbox", { name: "Select tag AI" });
    await user.click(ai);
    await user.click(screen.getByRole("button", { name: "Invert selection" }));
    expect(ai).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Select tag Physics" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Select tag Technology" })).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Select all" }));
    expect(screen.getByRole("button", { name: "Delete selected (3)" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Invert selection" }));
    expect(screen.getByRole("button", { name: "Delete selected (0)" })).toBeDisabled();
    await user.click(ai);
    await user.click(screen.getByRole("button", { name: "Delete selected (1)" }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith([1]));
    expect(screen.queryByRole("checkbox", { name: "Select tag AI" })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Select tag Physics" })).toBeVisible();
    expect(notify).toHaveBeenCalledWith("Deleted 1 tags.");
  });

  it("keeps the selection and tag list when deletion is cancelled or rejected", async () => {
    const user = userEvent.setup();
    const remove = mockTagManager();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const notify = renderSettings();
    await user.click(screen.getByRole("button", { name: /Content/ }));
    const ai = await screen.findByRole("checkbox", { name: "Select tag AI" });
    await user.click(ai);
    await user.click(screen.getByRole("button", { name: "Delete selected (1)" }));
    expect(remove).not.toHaveBeenCalled();
    expect(ai).toBeChecked();
    confirm.mockReturnValue(true);
    remove.mockRejectedValue(new Error("Tag is used by an active brief schedule"));
    await user.click(screen.getByRole("button", { name: "Delete selected (1)" }));
    await waitFor(() => expect(notify).toHaveBeenCalledWith("Tag is used by an active brief schedule", "error"));
    expect(ai).toBeChecked();
    expect(screen.getAllByRole("checkbox", { name: /^Select tag/ })).toHaveLength(3);
  });
});

describe("SettingsModal dormant preview", () => {
  beforeEach(() => vi.stubEnv("VITE_AUTO_TAG_PREVIEW_ENABLED", "false"));

  it("enables tagging directly without fetching or showing previews", async () => {
    const user = userEvent.setup();
    mockTagManager();
    const previews = vi.mocked(api.autoTagPreviews);
    const update = vi.spyOn(api, "setAutoTagStatus").mockResolvedValue({
      ...autoTagStatus, enabled: true, preview_required: false,
    });
    renderSettings();
    await user.click(screen.getByRole("button", { name: /Content/ }));
    await screen.findByRole("checkbox", { name: "Select tag AI" });
    expect(screen.queryByRole("button", { name: "Run 50-article preview" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve full run" })).not.toBeInTheDocument();
    expect(previews).not.toHaveBeenCalled();
    await user.click(screen.getByRole("checkbox", { name: "Off" }));
    await waitFor(() => expect(update).toHaveBeenCalledWith(expect.objectContaining({ enabled: true })));
    expect(await screen.findByRole("checkbox", { name: "On" })).toBeChecked();
  });
});

describe("SettingsModal candidate promotion", () => {
  beforeEach(() => vi.stubEnv("VITE_AUTO_TAG_PREVIEW_ENABLED", "false"));
  const candidate: AutoTagProposal = {
    id: 7, name: "Quantum networking", description: "Quantum communication.",
    status: "active", support_count: 4, aliases: ["QNet"], promoted_tag_id: null,
  };

  it("shows candidates last and moves a promoted candidate into the formal tag list", async () => {
    const user = userEvent.setup();
    mockTagManager();
    let candidates = [candidate];
    let tags = await api.tags();
    vi.mocked(api.tags).mockImplementation(async () => tags);
    const load = vi.mocked(api.autoTagProposals).mockImplementation(async () => ({ items: candidates, total: candidates.length }));
    const promote = vi.spyOn(api, "promoteAutoTagProposal").mockImplementation(async () => {
      const tag: Tag = { id: 4, name: candidate.name, aliases: candidate.aliases, origin: "manual_promoted", color: null, entry_count: 4 };
      candidates = [];
      tags = [...tags, tag];
      return tag;
    });
    const notify = renderSettings();
    await user.click(screen.getByRole("button", { name: /Content/ }));
    const region = await screen.findByRole("region", { name: "Candidate topics" });
    const button = await within(region).findByRole("button", { name: `Promote ${candidate.name}` });
    expect(load).toHaveBeenCalledWith(0, 50, "active");
    expect(within(region).getByText("4 supporting Works")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Auto tagging" }).compareDocumentPosition(region) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await user.click(button);
    await waitFor(() => expect(promote).toHaveBeenCalledWith(candidate.id));
    expect(await screen.findByRole("checkbox", { name: `Select tag ${candidate.name}` })).toBeVisible();
    expect(within(region).queryByRole("button", { name: `Promote ${candidate.name}` })).not.toBeInTheDocument();
    expect(within(region).getByText("No candidate topics.")).toBeVisible();
    expect(notify).toHaveBeenCalledWith(`“${candidate.name}” promoted to a tag.`);
  });

  it("keeps a candidate when promotion fails and allows retry", async () => {
    const user = userEvent.setup();
    mockTagManager();
    vi.mocked(api.autoTagProposals).mockResolvedValue({ items: [candidate], total: 1 });
    const promote = vi.spyOn(api, "promoteAutoTagProposal").mockRejectedValue(new Error("Topic is unavailable"));
    const notify = renderSettings();
    await user.click(screen.getByRole("button", { name: /Content/ }));
    const button = await screen.findByRole("button", { name: `Promote ${candidate.name}` });
    await user.click(button);
    await waitFor(() => expect(notify).toHaveBeenCalledWith("Topic is unavailable", "error"));
    expect(button).toBeEnabled();
    expect(screen.queryByRole("checkbox", { name: `Select tag ${candidate.name}` })).not.toBeInTheDocument();
    await user.click(button);
    expect(promote).toHaveBeenCalledTimes(2);
  });

  it("returns to the previous candidate page when the final item is promoted", async () => {
    const user = userEvent.setup();
    mockTagManager();
    let candidates = Array.from({ length: 51 }, (_, index) => ({ ...candidate, id: index + 1, name: `Candidate ${index + 1}` }));
    let tags = await api.tags();
    vi.mocked(api.tags).mockImplementation(async () => tags);
    vi.mocked(api.autoTagProposals).mockImplementation(async (offset = 0, limit = 50) => ({ items: candidates.slice(offset, offset + limit), total: candidates.length }));
    vi.spyOn(api, "promoteAutoTagProposal").mockImplementation(async (id) => {
      const promoted = candidates.find((item) => item.id === id)!;
      const tag: Tag = { id: 99, name: promoted.name, color: null, entry_count: 4 };
      candidates = candidates.filter((item) => item.id !== id);
      tags = [...tags, tag];
      return tag;
    });
    renderSettings();
    await user.click(screen.getByRole("button", { name: /Content/ }));
    await user.click(await screen.findByRole("button", { name: "Next" }));
    await user.click(await screen.findByRole("button", { name: "Promote Candidate 51" }));
    expect(await screen.findByRole("button", { name: "Promote Candidate 1" })).toBeVisible();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument());
  });
});
