import { type FormEvent, useEffect, useRef, useState } from "react";
import type { Entry, EntryState, LanguageMode, Locale } from "../types";
import { t } from "../i18n";
import { authors, displayTitle, relativeTime } from "../utils";
import { EmptyState, ErrorNotice, SegmentedControl, SkeletonList } from "./Common";

export function EntryList({ locale, title, subtitle, entries, total, loading, error, activeId, activeFeedId, languageMode, selectedIds, query, hasMore, canRefreshSource, refreshingSource, markingAllRead, unreadOnly, onQuery, onLanguageMode, onOpen, onSelect, onSelectAll, onClearSelection, onState, onBulkState, onRefreshSource, onToggleUnread, onMarkAllRead, onRetry, onLoadMore }: {
  locale: Locale; title: string; subtitle: string; entries: Entry[]; total: number; loading: boolean; error: string;
  activeId: number | null; activeFeedId: number | null; languageMode: LanguageMode; selectedIds: Set<number>; query: string; hasMore: boolean;
  canRefreshSource: boolean; refreshingSource: boolean; markingAllRead: boolean; unreadOnly: boolean;
  onQuery: (value: string) => void; onLanguageMode: (mode: LanguageMode) => void; onOpen: (entry: Entry) => void;
  onSelect: (id: number, selected: boolean) => void; onSelectAll: () => void; onClearSelection: () => void;
  onState: (entry: Entry, state: Partial<EntryState>) => void; onBulkState: (state: Partial<EntryState>) => void;
  onRefreshSource: () => void; onToggleUnread: () => void; onMarkAllRead: () => void;
  onRetry: () => void; onLoadMore: () => void;
}) {
  const [search, setSearch] = useState(query);
  const listRef = useRef<HTMLDivElement>(null);
  const previousFeedId = useRef(activeFeedId);
  const seenIds = useRef<Set<number>>(new Set());
  const autoReadIds = useRef<Set<number>>(new Set());
  useEffect(() => setSearch(query), [query]);
  useEffect(() => {
    if (activeFeedId !== null && previousFeedId.current !== activeFeedId && listRef.current) {
      listRef.current.scrollTop = 0;
    }
    previousFeedId.current = activeFeedId;
  }, [activeFeedId]);
  useEffect(() => {
    seenIds.current.clear();
    autoReadIds.current.clear();
  }, [query, subtitle, title]);
  useEffect(() => {
    const root = listRef.current;
    if (!root || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((observations) => {
      observations.forEach((observation) => {
        const id = Number((observation.target as HTMLElement).dataset.entryId);
        const entry = entries.find((item) => item.id === id);
        if (!entry || entry.state.read) return;
        if (observation.isIntersecting && observation.intersectionRatio >= 0.5) {
          seenIds.current.add(id);
          return;
        }
        if (!observation.isIntersecting && seenIds.current.has(id) && !autoReadIds.current.has(id)) {
          autoReadIds.current.add(id);
          onState(entry, { read: true });
        }
      });
    }, { root, threshold: [0, 0.5] });
    root.querySelectorAll<HTMLElement>("[data-entry-id]").forEach((card) => observer.observe(card));
    return () => observer.disconnect();
  }, [entries, onState]);
  const submit = (event: FormEvent) => { event.preventDefault(); onQuery(search.trim()); };
  return <section className="entry-list-pane"><header className="list-header"><div className="list-header__title"><div><span className="eyebrow">{subtitle}</span><h1>{title}</h1></div><span className="result-count">{total.toLocaleString(locale)}</span></div><div className="timeline-actions"><button onClick={onRefreshSource} disabled={!canRefreshSource || refreshingSource} title={!canRefreshSource ? (locale === "zh-CN" ? "请先在左侧选择一个订阅源" : "Select a source in the sidebar first") : undefined}><span>↻</span>{refreshingSource ? "…" : t(locale, "refreshSource")}</button><button className={unreadOnly ? "is-active" : ""} aria-pressed={unreadOnly} onClick={onToggleUnread}><span>◉</span>{unreadOnly ? t(locale, "showAll") : t(locale, "unreadOnly")}</button><button onClick={onMarkAllRead} disabled={total === 0 || markingAllRead}><span className="timeline-actions__double-check">✓✓</span>{markingAllRead ? "…" : t(locale, "markAllRead")}</button></div><form className="search-box" role="search" onSubmit={submit}><span>⌕</span><input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder={t(locale, "search")} /><button type="button" onClick={() => { setSearch(""); onQuery(""); }} aria-label="Clear">×</button></form><div className="list-header__controls"><SegmentedControl label="Display language" value={languageMode} onChange={onLanguageMode} options={[{ value: "original", label: t(locale, "original") }, { value: "translated", label: t(locale, "translated") }, { value: "bilingual", label: t(locale, "bilingual") }]} />{entries.length > 0 && <button className="text-button" onClick={onSelectAll}>{selectedIds.size === entries.length ? "None" : "All"}</button>}</div></header>
    {selectedIds.size > 0 && <div className="bulk-bar"><strong>{selectedIds.size} {t(locale, "selected")}</strong><button onClick={() => onBulkState({ read: true })}>{t(locale, "markRead")}</button><button onClick={() => onBulkState({ starred: true })}>★</button><button onClick={() => onBulkState({ later: true })}>◷</button><button onClick={() => onBulkState({ archived: true })}>{t(locale, "archive")}</button><button onClick={onClearSelection}>×</button></div>}
    <div className="entry-list" ref={listRef}>{loading && entries.length === 0 ? <SkeletonList /> : error && entries.length === 0 ? <ErrorNotice message={error} retry={onRetry} /> : entries.length === 0 ? <EmptyState title={t(locale, "noEntries")} description={t(locale, "emptyHelp")} /> : <>{error && <ErrorNotice message={error} retry={onRetry} compact />}{entries.map((entry) => {
      const translated = languageMode === "bilingual" && entry.translated_title && entry.translated_title !== entry.title;
      return <article key={entry.id} data-entry-id={entry.id} className={`entry-card ${activeId === entry.id ? "is-active" : ""} ${entry.state.read ? "is-read" : ""}`} onClick={() => onOpen(entry)}><label className="entry-card__check" onClick={(event) => event.stopPropagation()}><input type="checkbox" checked={selectedIds.has(entry.id)} onChange={(event) => onSelect(entry.id, event.target.checked)} /></label><div className="entry-card__content"><div className="entry-card__meta"><span className="source-pill">{entry.feed_titles?.[0] || "RSS"}</span>{entry.announce_type && entry.announce_type !== "new" && <span className="version-pill">{entry.announce_type}</span>}<time>{relativeTime(entry.published_at, locale)}</time></div><h3>{displayTitle(entry, languageMode)}</h3>{translated && <p className="entry-card__original">{entry.title}</p>}<p className="entry-card__authors">{authors(entry).slice(0, 4).join(" · ")}</p><div className="entry-card__footer"><div className="entry-card__tags">{entry.domains?.slice(0, 2).map((domain) => <span key={domain.id}>{domain.name}</span>)}</div><div className="entry-card__actions"><button className={entry.state.later ? "is-active" : ""} onClick={(event) => { event.stopPropagation(); onState(entry, { later: !entry.state.later }); }}>◷</button><button className={entry.state.starred ? "is-active" : ""} onClick={(event) => { event.stopPropagation(); onState(entry, { starred: !entry.state.starred }); }}>{entry.state.starred ? "★" : "☆"}</button>{!entry.state.read && <span className="unread-dot" />}</div></div></div></article>;
    })}{hasMore && <button className="load-more" onClick={onLoadMore} disabled={loading}>{loading ? "…" : t(locale, "loadMore")}</button>}</>}</div>
  </section>;
}
