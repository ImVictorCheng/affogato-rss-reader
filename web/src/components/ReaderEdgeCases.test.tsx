import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Entry } from "../types";
import { formatArxivIdentifier, safeHttpUrl } from "../utils";
import { EntryDetail } from "./EntryDetail";
import { resetMathJaxForTests } from "./MathJax";

describe("reader edge cases", () => {
  afterEach(() => {
    delete window.MathJax;
    resetMathJaxForTests();
  });
  it("does not duplicate an arXiv version suffix", () => {
    expect(formatArxivIdentifier("2607.12345v2", 2)).toBe("arXiv:2607.12345v2");
  });
  it("allows only HTTP(S) navigation targets", () => {
    expect(safeHttpUrl("https://example.test/article")).toBe("https://example.test/article");
    expect(safeHttpUrl("http://example.test/article")).toBe("http://example.test/article");
    expect(safeHttpUrl("file:///private/article")).toBeNull();
    expect(safeHttpUrl("ftp://example.test/article")).toBeNull();
    expect(safeHttpUrl("http://[")).toBeNull();
  });
  it("does not make a non-HTTP entry URL interactive", () => {
    const entry: Entry = {
      id: 6, title: "Blocked link", translated_title: null, summary: "Summary.",
      translated_summary: null, url: "file:///private/article", authors: ["Researcher"],
      published_at: "2026-07-26T00:00:00Z", feed_titles: ["Example"], state: { read: false, starred: false, later: false, archived: false },
      tags: [], domains: [], translation_status: "idle",
    };
    render(<EntryDetail locale="en" entry={entry} loading={false} error="" languageMode="original" allTags={[]} allDomains={[]} onLanguageMode={vi.fn()} onState={vi.fn()} onAddTag={vi.fn()} onRemoveTag={vi.fn()} onCreateTag={vi.fn()} onDomains={vi.fn()} onBack={vi.fn()} onRetry={vi.fn()} />);
    expect(screen.getByText("Open original")).toHaveAttribute("aria-disabled", "true");
    expect(screen.queryByRole("link", { name: /Open original/ })).not.toBeInTheDocument();
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
  it("typesets original and translated summaries in bilingual mode", async () => {
    const typesetPromise = vi.fn().mockResolvedValue(undefined);
    window.MathJax = {
      startup: { promise: Promise.resolve() },
      typesetClear: vi.fn(),
      typesetPromise,
    };
    const entry: Entry = {
      id: 5, title: "Formula article", translated_title: "公式文章",
      summary: "Energy is $E=mc^2$.", translated_summary: "能量满足 \\(E=mc^2\\)。",
      url: "https://example.test/formula", authors: ["Researcher"],
      published_at: "2026-07-27T00:00:00Z", feed_titles: ["Example"], state: { read: false, starred: false, later: false, archived: false },
      tags: [], domains: [], translation_status: "complete",
    };

    render(<EntryDetail locale="en" entry={entry} loading={false} error="" languageMode="bilingual" allTags={[]} allDomains={[]} onLanguageMode={vi.fn()} onState={vi.fn()} onAddTag={vi.fn()} onRemoveTag={vi.fn()} onCreateTag={vi.fn()} onDomains={vi.fn()} onBack={vi.fn()} onRetry={vi.fn()} />);

    expect(screen.getByText("Energy is $E=mc^2$.")).toBeInTheDocument();
    expect(screen.getByText("能量满足 \\(E=mc^2\\)。")).toBeInTheDocument();
    await waitFor(() => expect(typesetPromise).toHaveBeenCalledTimes(2));
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
