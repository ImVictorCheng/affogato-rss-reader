import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Entry, EntryTag, Tag } from "../types";
import { EntryDetail } from "./EntryDetail";

const tag: Tag = { id: 1, name: "Many-Body Physics", color: "#8064e8" };
const entry: Entry = {
  id: 1, title: "Tagged article", url: "https://example.test/paper", authors: [],
  tags: [tag], domains: [],
  state: { read: false, starred: false, later: false, archived: false },
};

describe("EntryDetail article tags", () => {
  it.each(["en", "zh-CN"] as const)("removes a tag only through its close button in %s", async (locale) => {
    const user = userEvent.setup();
    const removeTag = vi.fn();
    render(<EntryDetail
      locale={locale} entry={entry} loading={false} error="" languageMode="original"
      allTags={[tag]} allDomains={[]} onLanguageMode={vi.fn()} onState={vi.fn()}
      onAddTag={vi.fn()} onRemoveTag={removeTag} onCreateTag={vi.fn(async () => tag)}
      onDomains={vi.fn()} onBack={vi.fn()} onRetry={vi.fn()}
      onReorderTags={vi.fn(async () => {})}
    />);

    const label = screen.getByText(tag.name);
    await user.click(label);
    await user.click(label.parentElement!);
    expect(removeTag).not.toHaveBeenCalled();
    expect(label).toBeVisible();

    const remove = screen.getByRole("button", { name: `${locale === "zh-CN" ? "移除标签" : "Remove tag"} ${tag.name}` });
    expect(remove).toHaveTextContent("×");
    await user.click(remove);
    expect(removeTag).toHaveBeenCalledExactlyOnceWith(tag);
  });
});

const weightedTags: EntryTag[] = [
  { id: 2, name: "Zebra", weight: 0.8 },
  { id: 3, name: "beta", weight: 0.9 },
  { id: 4, name: "Alpha", weight: 0.9 },
];
function renderTags(onReorderTags = vi.fn(async (_ids: number[]) => {})) {
  render(<EntryDetail locale="en" entry={{ ...entry, tags: weightedTags }} loading={false} error="" languageMode="original"
    allTags={weightedTags} allDomains={[]} onLanguageMode={vi.fn()} onState={vi.fn()} onAddTag={vi.fn()}
    onRemoveTag={vi.fn()} onCreateTag={vi.fn(async () => tag)} onReorderTags={onReorderTags}
    onDomains={vi.fn()} onBack={vi.fn()} onRetry={vi.fn()} />);
  return onReorderTags;
}
const displayedNames = () => Array.from(document.querySelectorAll(".tag-editor__name"), (node) => node.textContent);

describe("article tag ordering", () => {
  beforeEach(() => {
    vi.stubGlobal("PointerEvent", MouseEvent);
    Object.defineProperty(document, "elementFromPoint", { configurable: true, value: vi.fn() });
  });
  afterEach(() => { vi.unstubAllGlobals(); Reflect.deleteProperty(document, "elementFromPoint"); });
  it("defaults to descending weight with alphabetical ties", () => {
    const reorder = renderTags();
    expect(displayedNames()).toEqual(["Alpha", "beta", "Zebra"]);
    expect(document.querySelector(".tag-editor__drag")).toBeNull();
    expect(reorder).not.toHaveBeenCalled();
  });
  it("saves keyboard moves and allows moving back to the original order", async () => {
    const user = userEvent.setup();
    const reorder = renderTags();
    const handle = screen.getByRole("group", { name: "Reorder tag Alpha" });
    handle.focus();
    await user.keyboard("{ArrowRight}");
    await screen.findByText("Tag order saved");
    expect(reorder).toHaveBeenLastCalledWith([3, 4, 2]);
    expect(displayedNames()).toEqual(["beta", "Alpha", "Zebra"]);
    handle.focus();
    await user.keyboard("{ArrowLeft}");
    await waitFor(() => expect(reorder).toHaveBeenLastCalledWith([4, 3, 2]));
  });
  it("saves a pointer drag only on release", async () => {
    const reorder = renderTags();
    const first = screen.getByText("Alpha").parentElement!;
    const last = screen.getByText("Zebra").parentElement!;
    vi.spyOn(document, "elementFromPoint").mockReturnValue(last);
    fireEvent.pointerDown(first, { button: 0, pointerId: 1, clientX: 10, clientY: 10 });
    fireEvent.pointerMove(first, { pointerId: 1, clientX: 200, clientY: 10 });
    expect(displayedNames()).toEqual(["beta", "Zebra", "Alpha"]);
    expect(reorder).not.toHaveBeenCalled();
    fireEvent.pointerUp(first, { pointerId: 1 });
    await waitFor(() => expect(reorder).toHaveBeenCalledExactlyOnceWith([3, 2, 4]));
  });
  it("restores the saved order when a drag is cancelled", () => {
    const reorder = renderTags();
    const first = screen.getByText("Alpha").parentElement!;
    vi.spyOn(document, "elementFromPoint").mockReturnValue(screen.getByText("Zebra").parentElement!);
    fireEvent.pointerDown(first, { button: 0, pointerId: 1, clientX: 10, clientY: 10 });
    fireEvent.pointerMove(first, { pointerId: 1, clientX: 200, clientY: 10 });
    fireEvent.pointerCancel(first, { pointerId: 1 });
    expect(displayedNames()).toEqual(["Alpha", "beta", "Zebra"]);
    expect(reorder).not.toHaveBeenCalled();
  });
  it("rolls back a failed save and keeps the delete button usable", async () => {
    const user = userEvent.setup();
    const reorder = renderTags(vi.fn(async () => { throw new Error("Connection lost"); }));
    screen.getByRole("group", { name: "Reorder tag Alpha" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Connection lost");
    expect(displayedNames()).toEqual(["Alpha", "beta", "Zebra"]);
    expect(reorder).toHaveBeenCalledExactlyOnceWith([3, 4, 2]);
    expect(screen.getByRole("button", { name: "Remove tag Alpha" })).toBeEnabled();
  });
});
