import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Entry } from "../types";
import { formatArxivIdentifier } from "../utils";
import { EntryDetail } from "./EntryDetail";

describe("reader edge cases", () => {
  it("does not duplicate an arXiv version suffix", () => {
    expect(formatArxivIdentifier("2607.12345v2", 2)).toBe("arXiv:2607.12345v2");
  });
  it("keeps original reading available when translation fails", () => {
    const entry: Entry = {
      id: 1, title: "Original title", translated_title: null, summary: "Original remains readable.",
      translated_summary: null, url: "https://example.test/one", authors: ["Researcher"],
      published_at: "2026-07-26T00:00:00Z", feed_titles: ["Example"], state: { read: false, starred: false, later: false, archived: false },
      tags: [], domains: [], translation_status: "failed",
    };
    render(<EntryDetail locale="en" entry={entry} loading={false} error="" languageMode="bilingual" allTags={[]} allDomains={[]} onLanguageMode={vi.fn()} onState={vi.fn()} onAddTag={vi.fn()} onRemoveTag={vi.fn()} onCreateTag={vi.fn()} onDomains={vi.fn()} onBack={vi.fn()} onRetry={vi.fn()} />);
    expect(screen.getByText("Original remains readable.")).toBeInTheDocument();
    expect(screen.getByText(/Translation failed/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mark as read" })).toHaveAttribute("data-tooltip", "Mark as read");
    expect(screen.getByRole("button", { name: "Read later" })).toHaveAttribute("data-tooltip", "Read later");
    expect(screen.getByRole("button", { name: "Star" })).toHaveAttribute("data-tooltip", "Star");
    expect(screen.getByRole("button", { name: "Archive" })).toHaveAttribute("data-tooltip", "Archive");
  });
  it("distinguishes untranslated content from a missing original summary", () => {
    const entry: Entry = {
      id: 4, title: "Original title", translated_title: null, summary: "原始摘要存在。",
      translated_summary: null, url: "https://example.test/untranslated", authors: ["Researcher"],
      published_at: "2026-07-26T00:00:00Z", feed_titles: ["Example"], state: { read: false, starred: false, later: false, archived: false },
      tags: [], domains: [], translation_status: "pending",
    };
    render(<EntryDetail locale="zh-CN" entry={entry} loading={false} error="" languageMode="translated" allTags={[]} allDomains={[]} onLanguageMode={vi.fn()} onState={vi.fn()} onAddTag={vi.fn()} onRemoveTag={vi.fn()} onCreateTag={vi.fn()} onDomains={vi.fn()} onBack={vi.fn()} onRetry={vi.fn()} />);
    expect(screen.getByText("未翻译")).toBeInTheDocument();
    expect(screen.queryByText("这个订阅源没有提供摘要。")).not.toBeInTheDocument();
  });
  it("does not persistently emphasize the read action", () => {
    const entry: Entry = {
      id: 2, title: "Read article", translated_title: null, summary: "Already read.",
      translated_summary: null, url: "https://example.test/read", authors: ["Researcher"],
      published_at: "2026-07-27T00:00:00Z", feed_titles: ["Example"], state: { read: true, starred: false, later: true, archived: false },
      tags: [], domains: [], translation_status: "idle",
    };
    render(<EntryDetail locale="en" entry={entry} loading={false} error="" languageMode="original" allTags={[]} allDomains={[]} onLanguageMode={vi.fn()} onState={vi.fn()} onAddTag={vi.fn()} onRemoveTag={vi.fn()} onCreateTag={vi.fn()} onDomains={vi.fn()} onBack={vi.fn()} onRetry={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Mark as unread" })).not.toHaveClass("is-active");
    expect(screen.getByRole("button", { name: "Remove from read later" })).toHaveClass("is-active");
  });
  it("opens tag suggestions only from the chevron and uses a custom option grid", async () => {
    const user = userEvent.setup();
    const onAddTag = vi.fn();
    const entry: Entry = {
      id: 3, title: "Tagged article", translated_title: null, summary: "Tag picker.",
      translated_summary: null, url: "https://example.test/tags", authors: ["Researcher"],
      published_at: "2026-07-27T00:00:00Z", feed_titles: ["Example"], state: { read: false, starred: false, later: false, archived: false },
      tags: [], domains: [], translation_status: "idle",
    };
    const tags = [
      { id: 11, name: "Quantum", color: "#16a6a1", entry_count: 4 },
      { id: 12, name: "Review", color: "#8568df", entry_count: 2 },
    ];
    render(<EntryDetail locale="en" entry={entry} loading={false} error="" languageMode="original" allTags={tags} allDomains={[]} onLanguageMode={vi.fn()} onState={vi.fn()} onAddTag={onAddTag} onRemoveTag={vi.fn()} onCreateTag={vi.fn()} onDomains={vi.fn()} onBack={vi.fn()} onRetry={vi.fn()} />);
    const input = screen.getByRole("textbox", { name: "Add tag" });
    await user.click(input);
    await user.type(input, "Qua");
    expect(input).not.toHaveAttribute("list");
    expect(screen.queryByRole("listbox", { name: "Choose an existing tag" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Show tag suggestions" }));
    expect(screen.getByRole("listbox", { name: "Choose an existing tag" })).toBeInTheDocument();
    expect(screen.getAllByRole("option")).toHaveLength(1);
    await user.click(screen.getByRole("option", { name: /Quantum/ }));
    expect(onAddTag).toHaveBeenCalledWith(tags[0]);
    expect(screen.queryByRole("listbox", { name: "Choose an existing tag" })).not.toBeInTheDocument();
  });
});
