import { type DragEvent, useState } from "react";
import type { Domain, DomainMatch, Feed, Folder, FolderSortMode, Locale, ReaderView, SortDirection, Tag } from "../types";
import { BRAND } from "../brand";
import { t } from "../i18n";
import { Brand, SegmentedControl, SelectMenu } from "./Common";

export const UNCATEGORIZED_FOLDER = "__uncategorized__";
type SourceSortValue = "alpha:asc" | "alpha:desc" | "updated:desc" | "updated:asc" | "manual:asc";

function sortFeeds(items: Feed[], mode: FolderSortMode, direction: SortDirection, locale: Locale) {
  const alpha = (first: Feed, second: Feed) => first.title.localeCompare(second.title, locale, { numeric: true, sensitivity: "base" });
  return [...items].sort((first, second) => {
    if (mode === "manual") return first.position - second.position || alpha(first, second);
    if (mode === "alpha") {
      const result = alpha(first, second);
      return direction === "asc" ? result : -result;
    }
    const firstTime = first.last_fetched_at ? Date.parse(first.last_fetched_at) : Number.NaN;
    const secondTime = second.last_fetched_at ? Date.parse(second.last_fetched_at) : Number.NaN;
    if (Number.isNaN(firstTime) && Number.isNaN(secondTime)) return alpha(first, second);
    if (Number.isNaN(firstTime)) return 1;
    if (Number.isNaN(secondTime)) return -1;
    const result = firstTime - secondTime;
    return result === 0 ? alpha(first, second) : direction === "asc" ? result : -result;
  });
}

export function Sidebar({
  locale, feeds, folders, domains, tags, activeView, activeFeedId, activeFolder, activeTagId,
  activeDomainIds, domainMatch, resultCount, authMode, sortMode, sortDirection, onSelectView, onSelectFeed,
  onSelectFolder, onSelectTag, onToggleDomain, onDomainMatch, onClearDomains,
  onSourceSort, onReorderFeeds, onManageFeeds, onOpenBriefs, onOpenSettings, onLogout,
  briefsActive = false,
}: {
  locale: Locale; feeds: Feed[]; folders: Folder[]; domains: Domain[]; tags: Tag[]; activeView: ReaderView;
  activeFeedId: number | null; activeFolder: string | null; activeTagId: number | null;
  activeDomainIds: number[]; domainMatch: DomainMatch; resultCount: number; authMode: "owner" | "none";
  sortMode: FolderSortMode; sortDirection: SortDirection;
  briefsActive?: boolean;
  onSelectView: (value: ReaderView) => void; onSelectFeed: (id: number) => void;
  onSelectFolder: (folder: string) => void; onSelectTag: (id: number) => void;
  onToggleDomain: (id: number) => void; onDomainMatch: (match: DomainMatch) => void;
  onSourceSort: (mode: FolderSortMode, direction: SortDirection) => void;
  onReorderFeeds: (folder: string | null, feedIds: number[]) => void;
  onClearDomains: () => void; onManageFeeds: () => void; onOpenBriefs: () => void;
  onOpenSettings: () => void; onLogout: () => void;
}) {
  const [collapsedFolders, setCollapsedFolders] = useState<Set<string>>(() => new Set());
  const [draggedFeedId, setDraggedFeedId] = useState<number | null>(null);
  const [dragOverFeedId, setDragOverFeedId] = useState<number | null>(null);
  const readerSelectionActive = !briefsActive;
  const views: { id: ReaderView; mark: string }[] = [
    { id: "all", mark: "≡" }, { id: "unread", mark: "●" }, { id: "starred", mark: "★" },
    { id: "later", mark: "◷" }, { id: "archived", mark: "□" },
  ];
  const groupedFeeds = new Map<string, Feed[]>(folders.map((folder): [string, Feed[]] => [folder.name, []]));
  feeds.forEach((feed) => {
    const name = feed.folder?.trim() || UNCATEGORIZED_FOLDER;
    groupedFeeds.set(name, [...(groupedFeeds.get(name) ?? []), feed]);
  });
  const folderGroups = Array.from(groupedFeeds);
  const toggleFolder = (folder: string) => {
    setCollapsedFolders((current) => {
      const next = new Set(current);
      next.has(folder) ? next.delete(folder) : next.add(folder);
      return next;
    });
  };
  const dropFeed = (event: DragEvent<HTMLDivElement>, folder: string | null, orderedItems: Feed[], targetId: number) => {
    event.preventDefault();
    if (draggedFeedId === null || draggedFeedId === targetId) {
      setDragOverFeedId(null);
      return;
    }
    const nextIds = orderedItems.map((feed) => feed.id);
    const sourceIndex = nextIds.indexOf(draggedFeedId);
    if (sourceIndex < 0) return;
    nextIds.splice(sourceIndex, 1);
    let targetIndex = nextIds.indexOf(targetId);
    const bounds = event.currentTarget.getBoundingClientRect();
    if (event.clientY > bounds.top + bounds.height / 2) targetIndex += 1;
    nextIds.splice(targetIndex, 0, draggedFeedId);
    setDraggedFeedId(null);
    setDragOverFeedId(null);
    onReorderFeeds(folder, nextIds);
  };
  const sortOptions: { value: SourceSortValue; label: string; meta: string }[] = [
    { value: "alpha:asc", label: locale === "zh-CN" ? "名称 A → Z" : "Name A → Z", meta: locale === "zh-CN" ? "顺序" : "Ascending" },
    { value: "alpha:desc", label: locale === "zh-CN" ? "名称 Z → A" : "Name Z → A", meta: locale === "zh-CN" ? "倒序" : "Descending" },
    { value: "updated:desc", label: locale === "zh-CN" ? "更新时间：新 → 旧" : "Updated: newest first", meta: locale === "zh-CN" ? "倒序" : "Descending" },
    { value: "updated:asc", label: locale === "zh-CN" ? "更新时间：旧 → 新" : "Updated: oldest first", meta: locale === "zh-CN" ? "顺序" : "Ascending" },
    { value: "manual:asc", label: locale === "zh-CN" ? "手动排序" : "Manual order", meta: locale === "zh-CN" ? "拖动订阅源" : "Drag sources" },
  ];
  const sortValue = `${sortMode}:${sortMode === "manual" ? "asc" : sortDirection}` as SourceSortValue;
  const sortValueLabel = sortValue === "alpha:asc" ? "A–Z" : sortValue === "alpha:desc" ? "Z–A" : sortValue === "updated:desc" ? "NEW" : sortValue === "updated:asc" ? "OLD" : "↕";
  return <aside id="reader-sidebar" className="sidebar" aria-label="Reader navigation"><header className="sidebar__brand"><Brand /></header><nav className="sidebar__scroll">
    <div className="nav-group"><span className="nav-group__label">LIBRARY</span>{views.map(({ id, mark }) => {
      const selected = readerSelectionActive && activeView === id && !activeFeedId && !activeFolder && !activeTagId;
      const showCount = activeView === id;
      const label = `${t(locale, id)}${showCount ? ` ${resultCount}` : ""}`;
      return <button key={id} className={`nav-item ${selected ? "is-active" : ""}`} aria-label={label} title={t(locale, id)} onClick={() => onSelectView(id)}><span className="nav-item__mark" aria-hidden="true">{mark}</span><span>{t(locale, id)}</span>{showCount && <span className="nav-item__count">{resultCount}</span>}</button>;
    })}<button
      className={`nav-item ${briefsActive ? "is-active" : ""}`}
      aria-label={t(locale, "briefs")}
      title={t(locale, "briefs")}
      onClick={onOpenBriefs}
    ><span className="nav-item__mark" aria-hidden="true">▤</span><span>{t(locale, "briefs")}</span></button></div>
    <div className="nav-group"><div className="nav-group__heading"><span className="nav-group__label">DOMAINS</span>{activeDomainIds.length > 0 && <button className="text-button" onClick={onClearDomains}>×</button>}</div>{domains.length === 0 ? <p className="sidebar__hint">{t(locale, "generalMode")}</p> : <><div className="sidebar-tags">{domains.map((domain) => <button className={readerSelectionActive && activeDomainIds.includes(domain.id) ? "is-active" : ""} key={domain.id} onClick={() => onToggleDomain(domain.id)}><span style={{ backgroundColor: domain.color || "#2bc7c3" }} />{domain.name}<small>{domain.entry_count}</small></button>)}</div>{activeDomainIds.length > 1 && <SegmentedControl label="Domain matching" value={domainMatch} onChange={onDomainMatch} options={[{ value: "any", label: "ANY" }, { value: "all", label: "ALL" }]} />}</>}</div>
    <div className="nav-group">
      <div className="nav-group__heading">
        <span className="nav-group__label">SOURCES</span>
        <div className="source-heading-actions">
          <SelectMenu
            compact
            value={sortValue}
            valueLabel={sortValueLabel}
            options={sortOptions}
            label={locale === "zh-CN" ? "订阅源排序" : "Source sorting"}
            onChange={(value) => {
              const [mode, direction] = value.split(":") as [FolderSortMode, SortDirection];
              onSourceSort(mode, direction);
            }}
          />
          <button className="text-button" onClick={onManageFeeds}>{t(locale, "manage")}</button>
        </div>
      </div>
      {folderGroups.length === 0 && <p className="sidebar__hint">{t(locale, "emptyHelp")}</p>}
      {folderGroups.map(([folder, items], index) => {
        const folderLabel = folder === UNCATEGORIZED_FOLDER ? (locale === "zh-CN" ? "未分类" : "Uncategorized") : folder;
        const sortedItems = sortFeeds(items, sortMode, sortDirection, locale);
        const manual = sortMode === "manual";
        const expanded = !collapsedFolders.has(folder);
        const contentId = `source-folder-${index}`;
        return <div className="feed-folder" key={folder}>
          <div className={`folder-heading ${readerSelectionActive && activeFolder === folder ? "is-active" : ""}`}>
            <button
              type="button"
              className="folder-caret-button"
              aria-expanded={expanded}
              aria-controls={contentId}
              aria-label={`${expanded ? (locale === "zh-CN" ? "收起" : "Collapse") : (locale === "zh-CN" ? "展开" : "Expand")} ${folderLabel}`}
              onClick={() => toggleFolder(folder)}
            >
              <span className={`folder-caret ${expanded ? "" : "is-collapsed"}`} aria-hidden="true">▾</span>
            </button>
            <button type="button" className="folder-button" onClick={() => onSelectFolder(folder)}>
              <span>{folderLabel}</span>
              <span>{items.reduce((sum, feed) => sum + feed.unread_count, 0)}</span>
            </button>
          </div>
          <div id={contentId} className="feed-folder__items" hidden={!expanded}>
            {sortedItems.map((feed) => <div
              key={feed.id}
              className={`feed-row ${manual ? "is-manual" : ""} ${draggedFeedId === feed.id ? "is-dragging" : ""} ${dragOverFeedId === feed.id ? "is-drag-over" : ""}`}
              onDragOver={(event) => {
                if (!manual || draggedFeedId === null) return;
                event.preventDefault();
                event.dataTransfer.dropEffect = "move";
                setDragOverFeedId(feed.id);
              }}
              onDrop={(event) => manual && dropFeed(event, folder === UNCATEGORIZED_FOLDER ? null : folder, sortedItems, feed.id)}
            >
              {manual && <button
                type="button"
                className="feed-drag-handle"
                draggable
                aria-label={`${locale === "zh-CN" ? "拖动排序" : "Drag to reorder"} ${feed.title}`}
                title={locale === "zh-CN" ? "按住此处拖动排序" : "Drag from this handle to reorder"}
                onDragStart={(event) => {
                  event.stopPropagation();
                  setDraggedFeedId(feed.id);
                  event.dataTransfer.effectAllowed = "move";
                  event.dataTransfer.setData("text/plain", String(feed.id));
                }}
                onDragEnd={() => { setDraggedFeedId(null); setDragOverFeedId(null); }}
              ><span aria-hidden="true">⠿</span></button>}
              <button
                type="button"
                className={`feed-button ${readerSelectionActive && activeFeedId === feed.id ? "is-active" : ""}`}
                onClick={() => onSelectFeed(feed.id)}
              >
                <span className={`feed-status feed-status--${feed.enabled ? (feed.status === "error" ? "error" : "healthy") : "paused"}`} />
                <span className="feed-button__title">{feed.title}</span>
                {feed.unread_count > 0 && <span className="feed-button__count">{feed.unread_count}</span>}
              </button>
            </div>)}
          </div>
        </div>;
      })}
    </div>
    {tags.length > 0 && <div className="nav-group"><span className="nav-group__label">TAGS</span><div className="sidebar-tags">{tags.map((tag) => <button className={readerSelectionActive && activeTagId === tag.id ? "is-active" : ""} key={tag.id} onClick={() => onSelectTag(tag.id)}><span style={{ backgroundColor: tag.color || undefined }} />{tag.name}<small>{tag.entry_count}</small></button>)}</div></div>}
  </nav><footer className="sidebar__footer"><button onClick={onOpenSettings}>⚙ {t(locale, "settings")}</button>{authMode === "owner" && <button onClick={onLogout}>↪ {t(locale, "logout")}</button>}<p>Affogato RSS Reader v{BRAND.version}</p></footer></aside>;
}
