import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Entry } from "../types";
import { formatFullDate } from "../utils";
import { EntryDate } from "./EntryDate";

const entry: Entry = {
  id: 1, title: "Journal paper", url: "https://example.test/paper", authors: [], tags: [], domains: [],
  state: { read: false, starred: false, later: false, archived: false },
  published_at: "2026-10-08T10:00:00Z", source_updated_at: "2026-10-09T10:00:00Z",
  created_at: "2026-10-10T10:00:00Z",
};

describe("EntryDate", () => {
  it("shows a source publication date in the detail header", () => {
    render(<EntryDate entry={{ ...entry, source_updated_at: null }} locale="zh-CN" />);
    expect(screen.getByText(formatFullDate(entry.published_at, "zh-CN"))).toHaveAttribute("datetime", entry.published_at);
  });
  it.each(["en", "zh-CN"] as const)("labels source-update fallback dates in %s", (locale) => {
    render(<EntryDate entry={entry} locale={locale} />);
    const date = screen.getByText(new RegExp(locale === "zh-CN" ? "更新于" : "Updated"));
    expect(date).toHaveAttribute("datetime", entry.source_updated_at);
    expect(date).toHaveTextContent(formatFullDate(entry.source_updated_at, locale));
  });
  it.each(["en", "zh-CN"] as const)("labels collection dates on list cards in %s", (locale) => {
    render(<EntryDate entry={{ ...entry, published_at: null, source_updated_at: null }} locale={locale} relative />);
    const date = screen.getByText(new RegExp(locale === "zh-CN" ? "收录于" : "Added"));
    expect(date).toHaveAttribute("datetime", entry.created_at);
    expect(date.textContent!.length).toBeGreaterThan(locale === "zh-CN" ? 3 : 5);
    expect(date).toHaveAttribute("title", expect.stringContaining(locale === "zh-CN" ? "收录于" : "Added"));
  });
});
