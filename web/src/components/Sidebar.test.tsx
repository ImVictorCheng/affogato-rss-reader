import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Feed } from "../types";
import { Sidebar } from "./Sidebar";

const feeds: Feed[] = [
  {
    id: 1,
    title: "Quantum Journal",
    url: "https://example.test/quantum.xml",
    folder: "Physics",
    position: 0,
    enabled: true,
    poll_interval_minutes: 60,
    status: "healthy",
    unread_count: 3,
    entry_count: 10,
    error_count: 0,
    last_fetched_at: "2026-07-27T08:00:00Z",
    domains: [],
  },
  {
    id: 3,
    title: "Zeta Journal",
    url: "https://example.test/zeta.xml",
    folder: "Physics",
    position: 1,
    enabled: true,
    poll_interval_minutes: 60,
    status: "healthy",
    unread_count: 1,
    entry_count: 5,
    error_count: 0,
    last_fetched_at: "2026-07-28T08:00:00Z",
    domains: [],
  },
  {
    id: 2,
    title: "AI Journal",
    url: "https://example.test/ai.xml",
    folder: "Computing",
    position: 0,
    enabled: true,
    poll_interval_minutes: 60,
    status: "healthy",
    unread_count: 2,
    entry_count: 8,
    error_count: 0,
    domains: [],
  },
];

function renderSidebar(
  sortMode: "alpha" | "updated" | "manual" = "alpha",
  sortDirection: "asc" | "desc" = "asc",
  briefsActive = false,
  activeView: "all" | "unread" | "starred" | "later" | "archived" = "all",
) {
  const onSelectFolder = vi.fn();
  const onSourceSort = vi.fn();
  const onReorderFeeds = vi.fn();
  const onOpenBriefs = vi.fn();
  render(<Sidebar
    locale="zh-CN"
    feeds={feeds}
    folders={[
      { id: 1, name: "Physics", position: 0, sort_mode: sortMode, sort_direction: sortDirection, feed_count: 2 },
      { id: 2, name: "Computing", position: 1, sort_mode: "alpha", sort_direction: "asc", feed_count: 1 },
    ]}
    domains={[]}
    tags={[]}
    activeView={activeView}
    activeFeedId={null}
    activeFolder={null}
    activeTagId={null}
    activeDomainIds={[]}
    domainMatch="any"
    resultCount={18}
    authMode="none"
    sortMode={sortMode}
    sortDirection={sortDirection}
    briefsActive={briefsActive}
    onSelectView={vi.fn()}
    onSelectFeed={vi.fn()}
    onSelectFolder={onSelectFolder}
    onSelectTag={vi.fn()}
    onToggleDomain={vi.fn()}
    onDomainMatch={vi.fn()}
    onClearDomains={vi.fn()}
    onSourceSort={onSourceSort}
    onReorderFeeds={onReorderFeeds}
    onManageFeeds={vi.fn()}
    onOpenBriefs={onOpenBriefs}
    onOpenSettings={vi.fn()}
    onLogout={vi.fn()}
  />);
  return { onSelectFolder, onSourceSort, onReorderFeeds, onOpenBriefs };
}

describe("Sidebar source folders", () => {
  it("opens briefs from the library navigation", async () => {
    const user = userEvent.setup();
    const { onOpenBriefs } = renderSidebar();

    const button = screen.getByRole("button", { name: "简报" });
    expect(button).toBeEnabled();
    await user.click(button);
    expect(onOpenBriefs).toHaveBeenCalledOnce();
  });

  it("shows only the brief navigation item as active in the brief workspace", () => {
    renderSidebar("alpha", "asc", true, "archived");

    expect(screen.getByRole("button", { name: "归档 18" })).not.toHaveClass("is-active");
    expect(screen.getByRole("button", { name: "简报" })).toHaveClass("is-active");
  });

  it("collapses only the folder whose caret is clicked", async () => {
    const user = userEvent.setup();
    const { onSelectFolder } = renderSidebar();

    const physicsToggle = screen.getByRole("button", { name: "收起 Physics" });
    await user.click(physicsToggle);

    expect(physicsToggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("Quantum Journal").closest(".feed-folder__items")).not.toBeVisible();
    expect(screen.getByText("AI Journal").closest(".feed-folder__items")).toBeVisible();
    expect(onSelectFolder).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "展开 Physics" }));
    expect(screen.getByText("Quantum Journal").closest(".feed-folder__items")).toBeVisible();
  });

  it("keeps folder-name selection separate from caret toggling", async () => {
    const user = userEvent.setup();
    const { onSelectFolder } = renderSidebar();

    await user.click(screen.getByRole("button", { name: "Physics 4" }));

    expect(onSelectFolder).toHaveBeenCalledWith("Physics");
    expect(screen.getByRole("button", { name: "收起 Physics" })).toHaveAttribute("aria-expanded", "true");
  });

  it("applies one global sort strategy and persists choices from beside Manage", async () => {
    const user = userEvent.setup();
    const { onSourceSort } = renderSidebar("updated", "desc");
    const physicsItems = screen.getByText("Quantum Journal").closest(".feed-folder__items");
    expect(physicsItems).not.toBeNull();
    const titles = within(physicsItems as HTMLElement).getAllByRole("button").map((button) => button.textContent);
    expect(titles[0]).toContain("Zeta Journal");

    await user.click(screen.getByRole("combobox", { name: "订阅源排序" }));
    await user.click(screen.getByRole("option", { name: /名称 Z → A/ }));
    expect(onSourceSort).toHaveBeenCalledWith("alpha", "desc");
  });

  it("starts manual ordering only from the six-dot drag handle", async () => {
    const { onReorderFeeds } = renderSidebar("manual", "asc");
    const quantum = screen.getByRole("button", { name: /^Quantum Journal/ });
    const zeta = screen.getByRole("button", { name: /^Zeta Journal/ });
    const zetaHandle = screen.getByRole("button", { name: "拖动排序 Zeta Journal" });
    expect(quantum).not.toHaveAttribute("draggable");
    expect(zeta).not.toHaveAttribute("draggable");
    expect(zetaHandle).toHaveAttribute("draggable", "true");
    const dataTransfer = { effectAllowed: "", dropEffect: "", setData: vi.fn() };

    fireEvent.dragStart(zeta, { dataTransfer });
    expect(onReorderFeeds).not.toHaveBeenCalled();

    fireEvent.dragStart(zetaHandle, { dataTransfer });
    fireEvent.dragOver(quantum.closest(".feed-row") as HTMLElement, { dataTransfer });
    fireEvent.drop(quantum.closest(".feed-row") as HTMLElement, { dataTransfer });

    await waitFor(() => expect(onReorderFeeds).toHaveBeenCalledWith("Physics", [3, 1]));
  });
});
