import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Entry } from "../types";
import { EntryList } from "./EntryList";

const entry: Entry = {
  id: 9,
  title: "Visible research card",
  summary: "Summary",
  url: "https://example.test/article",
  authors: ["Researcher"],
  published_at: "2026-07-27T00:00:00Z",
  feed_titles: ["Example"],
  feed_ids: [3],
  state: { read: false, starred: false, later: false, archived: false },
  tags: [],
  domains: [],
};

afterEach(() => vi.unstubAllGlobals());

describe("EntryList read tracking", () => {
  it("marks an unread card after it was visible and then leaves the scroll viewport", () => {
    let callback: IntersectionObserverCallback | undefined;
    class Observer {
      readonly root = null;
      readonly rootMargin = "";
      readonly thresholds = [0, 0.5];
      constructor(value: IntersectionObserverCallback) { callback = value; }
      observe() {}
      unobserve() {}
      disconnect() {}
      takeRecords() { return []; }
    }
    vi.stubGlobal("IntersectionObserver", Observer);
    const onState = vi.fn();
    render(<EntryList
      locale="en"
      title="Unread"
      subtitle="LIBRARY"
      entries={[entry]}
      total={1}
      loading={false}
      error=""
      activeId={null}
      activeFeedId={null}
      languageMode="original"
      selectedIds={new Set()}
      query=""
      hasMore={false}
      canRefreshSource={false}
      refreshingSource={false}
      markingAllRead={false}
      unreadOnly
      onQuery={vi.fn()}
      onLanguageMode={vi.fn()}
      onOpen={vi.fn()}
      onSelect={vi.fn()}
      onSelectAll={vi.fn()}
      onClearSelection={vi.fn()}
      onState={onState}
      onBulkState={vi.fn()}
      onRefreshSource={vi.fn()}
      onToggleUnread={vi.fn()}
      onMarkAllRead={vi.fn()}
      onRetry={vi.fn()}
      onLoadMore={vi.fn()}
    />);
    const card = screen.getByRole("article");
    act(() => callback?.([{
      target: card,
      isIntersecting: true,
      intersectionRatio: 0.75,
    } as unknown as IntersectionObserverEntry], {} as IntersectionObserver));
    expect(onState).not.toHaveBeenCalled();
    act(() => callback?.([{
      target: card,
      isIntersecting: false,
      intersectionRatio: 0,
    } as unknown as IntersectionObserverEntry], {} as IntersectionObserver));
    expect(onState).toHaveBeenCalledWith(entry, { read: true });
  });

  it("returns to the top when navigating to a different feed", () => {
    const props = {
      locale: "en" as const,
      title: "Example",
      subtitle: "SOURCE",
      entries: [entry],
      total: 1,
      loading: false,
      error: "",
      activeId: null,
      languageMode: "original" as const,
      selectedIds: new Set<number>(),
      query: "",
      hasMore: false,
      canRefreshSource: true,
      refreshingSource: false,
      markingAllRead: false,
      unreadOnly: false,
      onQuery: vi.fn(),
      onLanguageMode: vi.fn(),
      onOpen: vi.fn(),
      onSelect: vi.fn(),
      onSelectAll: vi.fn(),
      onClearSelection: vi.fn(),
      onState: vi.fn(),
      onBulkState: vi.fn(),
      onRefreshSource: vi.fn(),
      onToggleUnread: vi.fn(),
      onMarkAllRead: vi.fn(),
      onRetry: vi.fn(),
      onLoadMore: vi.fn(),
    };
    const rendered = render(<EntryList {...props} activeFeedId={1} />);
    const list = rendered.container.querySelector(".entry-list") as HTMLDivElement;
    list.scrollTop = 420;

    rendered.rerender(<EntryList {...props} activeFeedId={2} />);

    expect(list.scrollTop).toBe(0);
  });
});
