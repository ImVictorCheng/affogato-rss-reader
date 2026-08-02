import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { Domain, Feed, Folder } from "../types";
import { FeedManager } from "./FeedManager";

const quantum: Domain = {
  id: 1,
  name: "Quantum physics",
  description: "",
  color: "#16a6a1",
  position: 0,
  feed_count: 1,
  entry_count: 4,
};
const ai: Domain = {
  id: 2,
  name: "Artificial intelligence",
  description: "",
  color: "#8568df",
  position: 1,
  feed_count: 0,
  entry_count: 0,
};
const feed: Feed = {
  id: 7,
  title: "Research Feed",
  url: "https://example.test/rss",
  folder: "Lab",
  position: 0,
  enabled: true,
  poll_interval_minutes: 45,
  status: "healthy",
  unread_count: 2,
  entry_count: 4,
  error_count: 0,
  domains: [quantum],
};
const secondFeed: Feed = {
  ...feed,
  id: 8,
  title: "Engineering Feed",
  url: "https://engineering.example.test/rss",
  position: 1,
  domains: [],
};
const folderCategory: Folder = {
  id: 4,
  name: "Lab",
  position: 0,
  sort_mode: "alpha",
  sort_direction: "asc",
  feed_count: 1,
};

function renderManager(managerFeeds: Feed[] = [feed]) {
  const onChanged = vi.fn().mockResolvedValue(undefined);
  const notify = vi.fn();
  render(<FeedManager
    locale="en"
    feeds={managerFeeds}
    folders={[folderCategory]}
    domains={[quantum, ai]}
    onClose={vi.fn()}
    onChanged={onChanged}
    notify={notify}
  />);
  return { onChanged, notify };
}

describe("FeedManager classification", () => {
  it("associates selected domains with multiple selected feeds in one action", async () => {
    const user = userEvent.setup();
    const associate = vi.spyOn(api, "associateFeedDomains").mockResolvedValue({
      feeds_updated: 2,
      associations_added: 2,
    });
    const { onChanged, notify } = renderManager([feed, secondFeed]);

    await user.click(screen.getByRole("checkbox", { name: "Select feed Research Feed" }));
    await user.click(screen.getByRole("checkbox", { name: "Select feed Engineering Feed" }));
    await user.click(screen.getByRole("checkbox", { name: "Associate domain Artificial intelligence" }));
    await user.click(screen.getByRole("button", { name: "Associate" }));

    expect(associate).toHaveBeenCalledWith([7, 8], [2]);
    expect(onChanged).toHaveBeenCalled();
    expect(notify).toHaveBeenCalledWith("Selected domains associated with 2 feeds.");
  });

  it("edits the folder and domain assignments of an existing feed", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(api, "updateFeed").mockResolvedValue(feed);
    renderManager();

    await user.click(screen.getByRole("button", { name: "Edit categories Research Feed" }));
    const folder = screen.getByRole("combobox", { name: "Folder" });
    await user.clear(folder);
    await user.type(folder, "Reading");
    await user.click(screen.getByRole("checkbox", { name: "Artificial intelligence" }));
    await user.click(screen.getByRole("button", { name: "Save categories" }));

    expect(update).toHaveBeenCalledWith(7, {
      folder: "Reading",
      domain_ids: [1, 2],
    });
  });

  it("renames folder and domain categories without recreating feeds", async () => {
    const user = userEvent.setup();
    const updateFolder = vi.spyOn(api, "updateFolder").mockResolvedValue({ ...folderCategory, name: "Research" });
    const updateDomain = vi.spyOn(api, "updateDomain").mockResolvedValue(quantum);
    renderManager();

    await user.click(screen.getByRole("button", { name: "Categories" }));
    await user.click(screen.getByRole("button", { name: "Rename" }));
    const folderName = screen.getByRole("textbox", { name: "Folder name" });
    await user.clear(folderName);
    await user.type(folderName, "Research");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(updateFolder).toHaveBeenCalledWith(4, { name: "Research" });

    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    const domainName = screen.getByRole("textbox", { name: "Domain name" });
    await user.clear(domainName);
    await user.type(domainName, "Quantum science");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(updateDomain).toHaveBeenCalledWith(1, {
      name: "Quantum science",
      color: "#16a6a1",
    });
  });

  it("creates an empty folder category from category management", async () => {
    const user = userEvent.setup();
    const createFolder = vi.spyOn(api, "createFolder").mockResolvedValue({
      id: 5,
      name: "Reading",
      position: 1,
      sort_mode: "alpha",
      sort_direction: "asc",
      feed_count: 0,
    });
    const { onChanged, notify } = renderManager();

    await user.click(screen.getByRole("button", { name: "Categories" }));
    await user.type(screen.getByRole("textbox", { name: "New folder name" }), "Reading");
    await user.click(screen.getAllByRole("button", { name: "+" })[0]);

    expect(createFolder).toHaveBeenCalledWith({ name: "Reading", position: 1 });
    expect(onChanged).toHaveBeenCalled();
    expect(notify).toHaveBeenCalledWith("Folder category created.");
  });
});
