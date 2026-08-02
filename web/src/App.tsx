import { type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "./api";
import { BRAND } from "./brand";
import { applyTheme } from "./domainThemes";
import { browserLocale, t } from "./i18n";
import { AuthScreen } from "./components/AuthScreen";
import { Brand, BrandProvider, ErrorNotice, Spinner, Toast } from "./components/Common";
import { EntryDetail } from "./components/EntryDetail";
import { EntryList } from "./components/EntryList";
import { FeedManager } from "./components/FeedManager";
import { LazyLoadBoundary } from "./components/LazyLoadBoundary";
import { OnboardingWizard } from "./components/OnboardingWizard";
import { SettingsModal } from "./components/SettingsModal";
import { Sidebar, UNCATEGORIZED_FOLDER } from "./components/Sidebar";
import type { AuthStatus, Domain, DomainMatch, Entry, EntryState, Feed, Folder, FolderSortMode, LanguageMode, Locale, MobilePane, ReaderView, SortDirection, SourceSortSettings, Tag, ThemeConfig, UpdateStatus } from "./types";
import { errorText } from "./utils";

type ModalName = "feeds" | "settings" | null;
type WorkspaceName = "reader" | "briefs";
type Notice = { message: string; tone: "success" | "error"; key: number };
type PaneWidths = { sidebar: number; list: number };

const BriefWorkspace = lazy(() =>
  import("./components/BriefWorkspace").then((module) => ({
    default: module.BriefWorkspace,
  })),
);

const PANE_STORAGE_KEY = `${BRAND.storagePrefix}:pane-widths`;
const SIDEBAR_COLLAPSED_STORAGE_KEY = `${BRAND.storagePrefix}:sidebar-collapsed`;
const DEFAULT_PANE_WIDTHS: PaneWidths = { sidebar: 264, list: 405 };
const SIDEBAR_MIN = 210;
const SIDEBAR_MAX = 420;
const LIST_MIN = 300;
const LIST_MAX = 720;
const DETAIL_MIN = 360;
const RESIZER_TOTAL = 12;
const MOBILE_LAYOUT_MAX = 900;

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(maximum, Math.max(minimum, value));
}

function BriefWorkspaceLoader({ locale, onBack, notify }: {
  locale: Locale;
  onBack: () => void;
  notify: (message: string, tone?: "success" | "error") => void;
}) {
  const copy = locale === "zh-CN"
    ? {
        title: "简报功能暂时无法加载",
        message: "阅读器仍可正常使用。请刷新应用以重新加载简报，或返回文章列表。",
        reload: "刷新应用",
        back: "返回阅读器",
        loading: "正在加载简报…",
      }
    : {
        title: "Briefs couldn't load",
        message: "The reader is still available. Reload the application to load briefs again, or return to your articles.",
        reload: "Reload application",
        back: "Back to reader",
        loading: "Loading briefs…",
      };

  return (
    <LazyLoadBoundary
      title={copy.title}
      message={copy.message}
      reloadLabel={copy.reload}
      backLabel={copy.back}
      onReload={() => window.location.reload()}
      onBack={onBack}
    >
      <Suspense
        fallback={(
          <main className="brief-workspace">
            <Spinner label={copy.loading} />
          </main>
        )}
      >
        <BriefWorkspace locale={locale} onBack={onBack} notify={notify} />
      </Suspense>
    </LazyLoadBoundary>
  );
}

function savedPaneWidths(): PaneWidths {
  try {
    const saved = JSON.parse(localStorage.getItem(PANE_STORAGE_KEY) || "null") as Partial<PaneWidths> | null;
    if (saved && Number.isFinite(saved.sidebar) && Number.isFinite(saved.list)) {
      return {
        sidebar: clamp(Number(saved.sidebar), SIDEBAR_MIN, SIDEBAR_MAX),
        list: clamp(Number(saved.list), LIST_MIN, LIST_MAX),
      };
    }
  } catch {
    // Ignore invalid local preferences and restore the balanced defaults.
  }
  return DEFAULT_PANE_WIDTHS;
}

function savedSidebarCollapsed() {
  return localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === "true";
}

function fitPaneWidths(widths: PaneWidths, availableWidth: number): PaneWidths {
  if (availableWidth <= MOBILE_LAYOUT_MAX) return widths;
  const budget = Math.max(SIDEBAR_MIN + LIST_MIN, availableWidth - DETAIL_MIN - RESIZER_TOTAL);
  let sidebar = clamp(widths.sidebar, SIDEBAR_MIN, SIDEBAR_MAX);
  let list = clamp(widths.list, LIST_MIN, LIST_MAX);
  let overflow = Math.max(0, sidebar + list - budget);
  const listReduction = Math.min(overflow, list - LIST_MIN);
  list -= listReduction;
  overflow -= listReduction;
  sidebar = Math.max(SIDEBAR_MIN, sidebar - overflow);
  return { sidebar, list };
}

function PaneResizer({ label, value, minimum, maximum, variant, onChange, onReset }: {
  label: string;
  value: number;
  minimum: number;
  maximum: number;
  variant: "navigation" | "list";
  onChange: (value: number) => void;
  onReset: () => void;
}) {
  const drag = useRef<{ pointerId: number; startX: number; startValue: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const stopDragging = (event?: ReactPointerEvent<HTMLDivElement>) => {
    if (event && event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    drag.current = null;
    setDragging(false);
  };
  const keydown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const step = event.shiftKey ? 32 : 12;
    onChange(value + (event.key === "ArrowRight" ? step : -step));
  };
  return <div
    className={`pane-resizer pane-resizer--${variant} ${dragging ? "is-dragging" : ""}`}
    role="separator"
    aria-label={label}
    aria-orientation="vertical"
    aria-valuemin={minimum}
    aria-valuemax={maximum}
    aria-valuenow={Math.round(value)}
    tabIndex={0}
    title={`${label} · ${Math.round(value)} px`}
    onDoubleClick={onReset}
    onKeyDown={keydown}
    onPointerDown={(event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      drag.current = { pointerId: event.pointerId, startX: event.clientX, startValue: value };
      event.currentTarget.setPointerCapture?.(event.pointerId);
      setDragging(true);
    }}
    onPointerMove={(event) => {
      const current = drag.current;
      if (!current || current.pointerId !== event.pointerId) return;
      onChange(current.startValue + event.clientX - current.startX);
    }}
    onPointerUp={(event) => stopDragging(event)}
    onPointerCancel={(event) => stopDragging(event)}
    onLostPointerCapture={() => {
      drag.current = null;
      setDragging(false);
    }}
  />;
}

function ReaderApp({ auth, locale, onLocale, onSignedOut, onDebugReset, onTheme }: { auth: AuthStatus; locale: Locale; onLocale: (locale: Locale) => void; onSignedOut: () => void; onDebugReset: () => void; onTheme: (theme: ThemeConfig) => void }) {
  const [feeds, setFeeds] = useState<Feed[]>([]);
  const [sourceFolders, setSourceFolders] = useState<Folder[]>([]);
  const [sourceSort, setSourceSort] = useState<SourceSortSettings>({ sort_mode: "alpha", sort_direction: "asc" });
  const [domains, setDomains] = useState<Domain[]>([]);
  const [tags, setTags] = useState<Tag[]>([]);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [detailEntry, setDetailEntry] = useState<Entry | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [view, setView] = useState<ReaderView>("unread");
  const [feedId, setFeedId] = useState<number | null>(null);
  const [folder, setFolder] = useState<string | null>(null);
  const [tagId, setTagId] = useState<number | null>(null);
  const [domainIds, setDomainIds] = useState<number[]>([]);
  const [domainMatch, setDomainMatch] = useState<DomainMatch>("any");
  const [query, setQuery] = useState("");
  const [languageMode, setLanguageModeState] = useState<LanguageMode>(() => {
    const saved = localStorage.getItem(`${BRAND.storagePrefix}:content-language`);
    return saved === "original" || saved === "translated" || saved === "bilingual" ? saved : "bilingual";
  });
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [mobilePane, setMobilePane] = useState<MobilePane>("list");
  const [modal, setModal] = useState<ModalName>(null);
  const [workspace, setWorkspace] = useState<WorkspaceName>("reader");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [refreshingSource, setRefreshingSource] = useState(false);
  const [markingAllRead, setMarkingAllRead] = useState(false);
  const [paneWidths, setPaneWidths] = useState<PaneWidths>(() => fitPaneWidths(savedPaneWidths(), window.innerWidth));
  const [sidebarCollapsed, setSidebarCollapsed] = useState(savedSidebarCollapsed);
  const [appUpdate, setAppUpdate] = useState<UpdateStatus | null>(null);
  const [installingUpdate, setInstallingUpdate] = useState(false);
  const readerShell = useRef<HTMLDivElement>(null);
  const requestId = useRef(0);
  const pendingReadIds = useRef<Set<number>>(new Set());
  const navigationRefreshTimer = useRef<number | null>(null);

  const notify = useCallback((message: string, tone: "success" | "error" = "success") => setNotice({ message, tone, key: Date.now() }), []);
  const refreshUpdateStatus = useCallback(async () => {
    try {
      setAppUpdate(await api.updateStatus());
    } catch {
      // Update status must never interrupt the reader during a transient outage.
    }
  }, []);
  useEffect(() => {
    void refreshUpdateStatus();
    const timer = window.setInterval(() => void refreshUpdateStatus(), 15_000);
    return () => window.clearInterval(timer);
  }, [refreshUpdateStatus]);
  const installAvailableUpdate = useCallback(async () => {
    if (!appUpdate?.downloaded || installingUpdate) return;
    const targetVersion = appUpdate.latest_version;
    setInstallingUpdate(true);
    try {
      setAppUpdate(await api.installUpdate());
      notify(locale === "zh-CN" ? "正在安装更新，应用将自动重启…" : "Installing the update; the application will restart…");
      for (let attempt = 0; attempt < 90; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 2_000));
        let status: UpdateStatus;
        try {
          status = await api.updateStatus();
        } catch {
          // Connection failures are expected while the reader container restarts.
          continue;
        }
        setAppUpdate(status);
        if (status.current_version === targetVersion && status.status !== "installing") {
          window.location.reload();
          return;
        }
        if (status.status === "install_failed") throw new Error(status.error || "Update installation failed");
      }
      throw new Error(locale === "zh-CN" ? "应用重启超时，请检查 Docker 日志。" : "The restart timed out; check the Docker logs.");
    } catch (caught) {
      notify(errorText(caught), "error");
      await refreshUpdateStatus();
    } finally {
      setInstallingUpdate(false);
    }
  }, [appUpdate, installingUpdate, locale, notify, refreshUpdateStatus]);
  const setLanguageMode = (value: LanguageMode) => {
    setLanguageModeState(value);
    localStorage.setItem(`${BRAND.storagePrefix}:content-language`, value);
  };
  const loadNavigation = useCallback(async () => {
    try {
      const [nextFeeds, nextFolders, nextSort, nextDomains, nextTags] = await Promise.all([api.feeds(), api.folders(), api.sourceSortSettings(), api.domains(), api.tags()]);
      setFeeds(nextFeeds); setSourceFolders(nextFolders); setSourceSort(nextSort); setDomains(nextDomains); setTags(nextTags);
    } catch (caught) { notify(errorText(caught), "error"); }
  }, [notify]);
  useEffect(() => { void loadNavigation(); }, [loadNavigation]);
  const scheduleNavigationRefresh = useCallback(() => {
    if (navigationRefreshTimer.current !== null) window.clearTimeout(navigationRefreshTimer.current);
    navigationRefreshTimer.current = window.setTimeout(() => {
      navigationRefreshTimer.current = null;
      void loadNavigation();
    }, 250);
  }, [loadNavigation]);
  useEffect(() => () => {
    if (navigationRefreshTimer.current !== null) window.clearTimeout(navigationRefreshTimer.current);
  }, []);
  useEffect(() => {
    localStorage.setItem(PANE_STORAGE_KEY, JSON.stringify(paneWidths));
  }, [paneWidths]);
  useEffect(() => {
    localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, String(sidebarCollapsed));
  }, [sidebarCollapsed]);
  useEffect(() => {
    const fitToWindow = () => setPaneWidths((current) => {
      const fitted = fitPaneWidths(current, readerShell.current?.clientWidth || window.innerWidth);
      return fitted.sidebar === current.sidebar && fitted.list === current.list ? current : fitted;
    });
    window.addEventListener("resize", fitToWindow);
    return () => window.removeEventListener("resize", fitToWindow);
  }, []);

  const filters = useMemo(() => ({ view, feed_id: feedId, folder, tag_id: tagId, q: query, domain_ids: domainIds, domain_match: domainMatch }), [view, feedId, folder, tagId, query, domainIds, domainMatch]);
  const loadEntries = useCallback(async (nextPage = 1, append = false) => {
    const id = ++requestId.current; setLoading(true); setListError("");
    try {
      const result = await api.entries({ ...filters, page: nextPage, per_page: 40 });
      if (id !== requestId.current) return;
      setEntries((current) => append ? [...current, ...result.items.filter((item) => !current.some((old) => old.id === item.id))] : result.items);
      setTotal(result.total); setPage(result.page);
      if (!append) { setSelectedIds(new Set()); setDetailEntry((current) => result.items.find((item) => item.id === current?.id) ?? result.items[0] ?? null); }
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) onSignedOut();
      else setListError(errorText(caught));
    } finally { if (id === requestId.current) setLoading(false); }
  }, [filters, onSignedOut]);
  useEffect(() => { void loadEntries(); }, [loadEntries]);
  useEffect(() => {
    if (!detailEntry?.id) return;
    let active = true; setDetailLoading(true);
    api.entry(detailEntry.id).then((entry) => {
      if (!active) return;
      const resolved = pendingReadIds.current.has(entry.id)
        ? { ...entry, state: { ...entry.state, read: true } }
        : entry;
      setDetailEntry(resolved);
      setEntries((items) => items.map((item) => item.id === resolved.id ? resolved : item));
    }).catch((caught) => notify(errorText(caught), "error")).finally(() => active && setDetailLoading(false));
    return () => { active = false; };
  }, [detailEntry?.id, notify]);

  const patch = useCallback((id: number, state: Partial<EntryState>) => {
    setEntries((items) => items.map((item) => item.id === id ? { ...item, state: { ...item.state, ...state } } : item));
    setDetailEntry((item) => item?.id === id ? { ...item, state: { ...item.state, ...state } } : item);
  }, []);
  const adjustUnreadCounts = useCallback((entry: Entry, delta: number) => {
    if (!delta) return;
    const feedIds = new Set(entry.feed_ids || []);
    setFeeds((items) => items.map((feed) => feedIds.has(feed.id)
      ? { ...feed, unread_count: Math.max(0, feed.unread_count + delta) }
      : feed));
    if (view === "unread") setTotal((value) => Math.max(0, value + delta));
  }, [view]);
  const updateState = useCallback(async (entry: Entry, state: Partial<EntryState>) => {
    if (state.read === true && (entry.state.read || pendingReadIds.current.has(entry.id))) return;
    const previous = entry.state;
    const unreadDelta = typeof state.read === "boolean" && state.read !== previous.read
      ? (state.read ? -1 : 1)
      : 0;
    if (state.read === true) pendingReadIds.current.add(entry.id);
    patch(entry.id, state);
    adjustUnreadCounts(entry, unreadDelta);
    try {
      await api.updateEntryState(entry.id, state);
      if (unreadDelta) scheduleNavigationRefresh();
    } catch (caught) {
      patch(entry.id, previous);
      adjustUnreadCounts(entry, -unreadDelta);
      notify(errorText(caught), "error");
    } finally {
      pendingReadIds.current.delete(entry.id);
    }
  }, [adjustUnreadCounts, notify, patch, scheduleNavigationRefresh]);
  const open = useCallback((entry: Entry) => { setDetailEntry(entry); setMobilePane("detail"); if (!entry.state.read) void updateState(entry, { read: true }); }, [updateState]);
  async function bulk(state: Partial<EntryState>) {
    const ids = [...selectedIds]; if (!ids.length) return;
    try { await api.bulkEntryState(ids, state); setSelectedIds(new Set()); await loadEntries(); await loadNavigation(); }
    catch (caught) { notify(errorText(caught), "error"); }
  }
  async function refreshCurrentSource() {
    if (!feedId || refreshingSource) return;
    setRefreshingSource(true);
    try {
      await api.refreshFeed(feedId);
      await Promise.all([loadEntries(), loadNavigation()]);
      notify(locale === "zh-CN" ? "订阅源已刷新" : "Source refreshed");
    } catch (caught) {
      notify(errorText(caught), "error");
    } finally {
      setRefreshingSource(false);
    }
  }
  async function updateSourceSort(sortMode: FolderSortMode, sortDirection: SortDirection) {
    const previous = sourceSort;
    const next = { sort_mode: sortMode, sort_direction: sortDirection };
    setSourceSort(next);
    try {
      await api.updateSourceSortSettings(next);
    } catch (caught) {
      setSourceSort(previous);
      notify(errorText(caught), "error");
    }
  }
  async function reorderSourceFeeds(folderName: string | null, feedIds: number[]) {
    const positions = new Map(feedIds.map((id, position) => [id, position]));
    setFeeds((items) => items.map((feed) => positions.has(feed.id)
      ? { ...feed, position: positions.get(feed.id) ?? feed.position }
      : feed));
    try {
      await api.reorderFeeds(folderName, feedIds);
    } catch (caught) {
      notify(errorText(caught), "error");
      await loadNavigation();
    }
  }
  async function markAllRead() {
    if (!total || markingAllRead) return;
    setMarkingAllRead(true);
    try {
      const result = await api.markAllRead(filters);
      await Promise.all([loadEntries(), loadNavigation()]);
      notify(locale === "zh-CN" ? `已将 ${result.updated} 篇文章标为已读` : `Marked ${result.updated} articles as read`);
    } catch (caught) {
      notify(errorText(caught), "error");
    } finally {
      setMarkingAllRead(false);
    }
  }
  async function createTag(name: string) { const tag = await api.createTag(name); await loadNavigation(); return tag; }
  async function addTag(tag: Tag) { if (!detailEntry) return; await api.addEntryTag(detailEntry.id, tag.id); setDetailEntry({ ...detailEntry, tags: [...detailEntry.tags, tag] }); await loadNavigation(); }
  async function removeTag(tag: Tag) { if (!detailEntry) return; await api.removeEntryTag(detailEntry.id, tag.id); setDetailEntry({ ...detailEntry, tags: detailEntry.tags.filter((item) => item.id !== tag.id) }); await loadNavigation(); }
  async function setEntryDomains(ids: number[]) { if (!detailEntry) return; const value = await api.setEntryDomains(detailEntry.id, ids); setDetailEntry(value); setEntries((items) => items.map((item) => item.id === value.id ? value : item)); await loadNavigation(); }

  useEffect(() => {
    function keydown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (modal || event.altKey || event.ctrlKey || event.metaKey || ["INPUT", "TEXTAREA", "SELECT"].includes(target?.tagName || "") || target?.isContentEditable) return;
      const key = event.key.toLowerCase();
      const index = entries.findIndex((entry) => entry.id === detailEntry?.id);
      if (key === "j" || key === "k") {
        event.preventDefault(); const next = Math.min(entries.length - 1, Math.max(0, (index < 0 ? 0 : index) + (key === "j" ? 1 : -1))); if (entries[next]) open(entries[next]); return;
      }
      if (!detailEntry) return;
      if (key === "m") void updateState(detailEntry, { read: !detailEntry.state.read });
      if (key === "s") void updateState(detailEntry, { starred: !detailEntry.state.starred });
      if (key === "l") void updateState(detailEntry, { later: !detailEntry.state.later });
      if (key === "a") void updateState(detailEntry, { archived: !detailEntry.state.archived });
      if (key === "o") window.open(detailEntry.url, "_blank", "noopener,noreferrer");
    }
    window.addEventListener("keydown", keydown); return () => window.removeEventListener("keydown", keydown);
  }, [detailEntry, entries, modal, open, updateState]);

  const activeFeed = feeds.find((feed) => feed.id === feedId);
  const activeTag = tags.find((tag) => tag.id === tagId);
  const activeDomainNames = domains.filter((domain) => domainIds.includes(domain.id)).map((domain) => domain.name);
  const heading = activeFeed ? { title: activeFeed.title, subtitle: "SOURCE" } : folder ? { title: folder === UNCATEGORIZED_FOLDER ? (locale === "zh-CN" ? "未分类" : "Uncategorized") : folder, subtitle: "FOLDER" } : activeTag ? { title: `# ${activeTag.name}`, subtitle: "TAG" } : activeDomainNames.length ? { title: activeDomainNames.join(domainMatch === "all" ? " ∩ " : " ∪ "), subtitle: `DOMAINS · ${domainMatch.toUpperCase()}` } : { title: t(locale, view), subtitle: "LIBRARY" };
  function clearSourceFilters() { setFeedId(null); setFolder(null); setTagId(null); setMobilePane("list"); }
  async function logout() { try { await api.logout(); } finally { onSignedOut(); } }
  const layoutWidth = () => readerShell.current?.clientWidth || window.innerWidth;
  const setSidebarWidth = (value: number) => setPaneWidths((current) => fitPaneWidths({ ...current, sidebar: clamp(value, SIDEBAR_MIN, SIDEBAR_MAX) }, layoutWidth()));
  const setListWidth = (value: number) => setPaneWidths((current) => fitPaneWidths({ ...current, list: clamp(value, LIST_MIN, LIST_MAX) }, layoutWidth()));
  const layoutStyle = {
    "--sidebar-pane-width": `${paneWidths.sidebar}px`,
    "--list-pane-width": `${paneWidths.list}px`,
  } as CSSProperties;

  const showReader = () => {
    setWorkspace("reader");
    setMobilePane("list");
  };

  return <div ref={readerShell} style={layoutStyle} className={`reader-shell mobile-pane--${mobilePane} ${sidebarCollapsed ? "is-sidebar-collapsed" : ""} ${workspace === "briefs" ? "is-brief-workspace" : ""}`}>
    {appUpdate && (appUpdate.downloaded || ["available_manual", "download_failed", "install_failed"].includes(appUpdate.status)) && <aside className={`update-banner update-banner--${appUpdate.status}`} role="status">
      <div><span className="eyebrow">UPDATE</span><strong>{appUpdate.downloaded ? (locale === "zh-CN" ? `版本 ${appUpdate.latest_version} 已准备好` : `Version ${appUpdate.latest_version} is ready`) : (locale === "zh-CN" ? "发现新版本" : "A new version is available")}</strong>{appUpdate.error && <small>{appUpdate.error}</small>}</div>
      <div className="update-banner__actions">
        {appUpdate.downloaded && <button type="button" className="button button--primary button--small" disabled={installingUpdate || !appUpdate.install_supported} onClick={() => void installAvailableUpdate()}>{installingUpdate ? (locale === "zh-CN" ? "正在重启…" : "Restarting…") : (locale === "zh-CN" ? "安装并重启" : "Install and restart")}</button>}
        {appUpdate.release_url && <a className="button button--secondary button--small" href={appUpdate.release_url} target="_blank" rel="noreferrer">{locale === "zh-CN" ? "查看版本" : "View release"}</a>}
        <button type="button" className="icon-button" aria-label={locale === "zh-CN" ? "暂时关闭更新提示" : "Dismiss update notice"} onClick={() => setAppUpdate(null)}>×</button>
      </div>
    </aside>}
    <header className="mobile-header"><button className="icon-button icon-button--dark" onClick={() => setMobilePane("navigation")}>☰</button><Brand compact /><button className="icon-button icon-button--dark" onClick={() => setModal("settings")}>⚙</button></header><Sidebar locale={locale} feeds={feeds} folders={sourceFolders} domains={domains} tags={tags} activeView={view} activeFeedId={feedId} activeFolder={folder} activeTagId={tagId} activeDomainIds={domainIds} domainMatch={domainMatch} resultCount={total} authMode={auth.mode} sortMode={sourceSort.sort_mode} sortDirection={sourceSort.sort_direction} briefsActive={workspace === "briefs"} onSelectView={(value) => { showReader(); setView(value); clearSourceFilters(); }} onSelectFeed={(id) => { showReader(); setFeedId(id); setFolder(null); setTagId(null); setView("all"); }} onSelectFolder={(value) => { showReader(); setFolder(value); setFeedId(null); setTagId(null); setView("all"); }} onSelectTag={(id) => { showReader(); setTagId(id); setFeedId(null); setFolder(null); setView("all"); }} onToggleDomain={(id) => { showReader(); setDomainIds((items) => items.includes(id) ? items.filter((item) => item !== id) : [...items, id]); }} onDomainMatch={setDomainMatch} onClearDomains={() => setDomainIds([])} onSourceSort={(mode, direction) => void updateSourceSort(mode, direction)} onReorderFeeds={(name, ids) => void reorderSourceFeeds(name, ids)} onManageFeeds={() => setModal("feeds")} onOpenBriefs={() => { setWorkspace("briefs"); setMobilePane("list"); }} onOpenSettings={() => setModal("settings")} onLogout={() => void logout()} />
    <button
      type="button"
      className="sidebar-collapse-toggle"
      aria-controls="reader-sidebar"
      aria-expanded={!sidebarCollapsed}
      aria-label={sidebarCollapsed ? (locale === "zh-CN" ? "展开订阅栏" : "Expand navigation") : (locale === "zh-CN" ? "收起订阅栏" : "Collapse navigation")}
      title={sidebarCollapsed ? (locale === "zh-CN" ? "展开订阅栏" : "Expand navigation") : (locale === "zh-CN" ? "收起订阅栏" : "Collapse navigation")}
      onClick={() => setSidebarCollapsed((current) => !current)}
    ><span aria-hidden="true" /></button>
    <PaneResizer variant="navigation" label={locale === "zh-CN" ? "调整导航栏宽度" : "Resize navigation pane"} value={paneWidths.sidebar} minimum={SIDEBAR_MIN} maximum={SIDEBAR_MAX} onChange={setSidebarWidth} onReset={() => setSidebarWidth(DEFAULT_PANE_WIDTHS.sidebar)} />
    {workspace === "briefs" ? (
      <BriefWorkspaceLoader locale={locale} onBack={showReader} notify={notify} />
    ) : <>
      <EntryList locale={locale} title={heading.title} subtitle={heading.subtitle} entries={entries} total={total} loading={loading} error={listError} activeId={detailEntry?.id ?? null} activeFeedId={feedId} languageMode={languageMode} selectedIds={selectedIds} query={query} hasMore={entries.length < total} canRefreshSource={Boolean(activeFeed)} refreshingSource={refreshingSource} markingAllRead={markingAllRead} unreadOnly={view === "unread"} onQuery={setQuery} onLanguageMode={setLanguageMode} onOpen={open} onSelect={(id, selected) => setSelectedIds((items) => { const next = new Set(items); selected ? next.add(id) : next.delete(id); return next; })} onSelectAll={() => setSelectedIds((items) => items.size === entries.length ? new Set() : new Set(entries.map((entry) => entry.id)))} onClearSelection={() => setSelectedIds(new Set())} onState={updateState} onBulkState={(state) => void bulk(state)} onRefreshSource={() => void refreshCurrentSource()} onToggleUnread={() => setView((current) => current === "unread" ? "all" : "unread")} onMarkAllRead={() => void markAllRead()} onRetry={() => void loadEntries()} onLoadMore={() => void loadEntries(page + 1, true)} />
      <PaneResizer variant="list" label={locale === "zh-CN" ? "调整文章列表宽度" : "Resize article list pane"} value={paneWidths.list} minimum={LIST_MIN} maximum={LIST_MAX} onChange={setListWidth} onReset={() => setListWidth(DEFAULT_PANE_WIDTHS.list)} />
      <EntryDetail locale={locale} entry={detailEntry} loading={detailLoading} error="" languageMode={languageMode} allTags={tags} allDomains={domains} onLanguageMode={setLanguageMode} onState={(state) => detailEntry && void updateState(detailEntry, state)} onAddTag={(tag) => void addTag(tag)} onRemoveTag={(tag) => void removeTag(tag)} onCreateTag={createTag} onDomains={(ids) => void setEntryDomains(ids)} onBack={() => setMobilePane("list")} onRetry={() => detailEntry && void api.entry(detailEntry.id).then(setDetailEntry)} />
    </>}
    {mobilePane === "navigation" && <button className="mobile-nav-scrim" onClick={() => setMobilePane("list")} aria-label="Close navigation" />}
    {modal === "feeds" && <FeedManager locale={locale} feeds={feeds} folders={sourceFolders} domains={domains} onClose={() => setModal(null)} onChanged={async () => { await loadNavigation(); await loadEntries(); }} notify={notify} />}
    {modal === "settings" && <SettingsModal locale={locale} auth={auth} onLocale={onLocale} onClose={() => setModal(null)} onLogout={() => void logout()} onDebugReset={onDebugReset} onBrandChanged={onTheme} onInstallUpdate={installAvailableUpdate} notify={notify} />}
    {notice && <Toast key={notice.key} message={notice.message} tone={notice.tone} onDismiss={() => setNotice(null)} />}
  </div>;
}

export default function App() {
  const [locale, setLocaleState] = useState<Locale>(() => {
    const value = localStorage.getItem(`${BRAND.storagePrefix}:locale`);
    return value === "zh-CN" || value === "en" ? value : browserLocale();
  });
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const setLocale = (value: Locale) => { setLocaleState(value); localStorage.setItem(`${BRAND.storagePrefix}:locale`, value); document.documentElement.lang = value; };
  useEffect(() => { document.documentElement.lang = locale; }, [locale]);
  useEffect(() => { applyTheme(status?.theme); }, [status?.theme]);
  useEffect(() => {
    let active = true; setError("");
    api.authStatus().then((value) => active && setStatus(value)).catch((caught) => active && setError(errorText(caught)));
    return () => { active = false; };
  }, [retry]);
  const identity = status?.theme?.identity;
  if (error) return <BrandProvider identity={identity}><main className="boot-page"><Brand /><ErrorNotice message={error} retry={() => setRetry((value) => value + 1)} /><p>{t(locale, "connectError")}</p></main></BrandProvider>;
  if (!status) return <BrandProvider><main className="boot-page"><Brand /><Spinner label="Connecting…" /></main></BrandProvider>;
  if (!status.authenticated) return <BrandProvider identity={identity}><AuthScreen status={status} locale={locale} onAuthenticated={setStatus} /></BrandProvider>;
  if (status.onboarding_required) {
    return <BrandProvider><OnboardingWizard locale={locale} onComplete={(profile) => setStatus({
      ...status,
      onboarding_required: false,
      theme: profile.theme,
    })} /></BrandProvider>;
  }
  return <BrandProvider identity={identity}><ReaderApp
    auth={status}
    locale={locale}
    onLocale={setLocale}
    onSignedOut={() => setStatus({ authenticated: false, setup_required: false, onboarding_required: false, mode: "owner", owner: status.owner })}
    onTheme={(theme) => setStatus({ ...status, theme })}
    onDebugReset={() => {
      applyTheme(null);
      setStatus({ authenticated: false, setup_required: true, onboarding_required: true, mode: "owner", owner: null, theme: null });
    }}
  /></BrandProvider>;
}
