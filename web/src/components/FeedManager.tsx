import { type ChangeEvent, type FormEvent, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { t } from "../i18n";
import type { Domain, Feed, Folder, Locale } from "../types";
import { downloadBlob, errorText, formatDateTime } from "../utils";
import { ComboBox, EmptyState, ErrorNotice, Modal, SelectMenu, Toggle } from "./Common";

type Tab = "feeds" | "add" | "opml" | "categories";

export function FeedManager({ locale, feeds, folders, domains, onClose, onChanged, notify }: {
  locale: Locale;
  feeds: Feed[];
  folders: Folder[];
  domains: Domain[];
  onClose: () => void;
  onChanged: () => Promise<void>;
  notify: (message: string, tone?: "success" | "error") => void;
}) {
  const zh = locale === "zh-CN";
  const [tab, setTab] = useState<Tab>("feeds");
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [folder, setFolder] = useState("");
  const [interval, setInterval] = useState(45);
  const [domainIds, setDomainIds] = useState<number[]>([]);
  const [folderName, setFolderName] = useState("");
  const [domainName, setDomainName] = useState("");
  const [discovered, setDiscovered] = useState<{ url: string; title: string; site_url?: string | null }[]>([]);
  const [editingFeedId, setEditingFeedId] = useState<number | null>(null);
  const [editFolder, setEditFolder] = useState("");
  const [editDomainIds, setEditDomainIds] = useState<number[]>([]);
  const [selectedFeedIds, setSelectedFeedIds] = useState<number[]>([]);
  const [bulkDomainIds, setBulkDomainIds] = useState<number[]>([]);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [editingFolder, setEditingFolder] = useState<string | null>(null);
  const [folderDraft, setFolderDraft] = useState("");
  const [editingDomainId, setEditingDomainId] = useState<number | null>(null);
  const [domainDraft, setDomainDraft] = useState("");
  const [domainColorDraft, setDomainColorDraft] = useState("#2bc7c3");
  const [busy, setBusy] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const file = useRef<HTMLInputElement>(null);
  const selectAllFeeds = useRef<HTMLInputElement>(null);
  const allFeedsSelected = feeds.length > 0 && selectedFeedIds.length === feeds.length;
  const folderOptions = [
    { value: "", label: zh ? "未分类" : "Uncategorized", meta: zh ? "不放入文件夹" : "No folder" },
    ...folders.map((item) => ({
      value: item.name,
      label: item.name,
      meta: `${item.feed_count} ${zh ? "个订阅源" : item.feed_count === 1 ? "feed" : "feeds"}`,
    })),
  ];

  useEffect(() => {
    const available = new Set(feeds.map((feed) => feed.id));
    setSelectedFeedIds((current) => current.filter((id) => available.has(id)));
  }, [feeds]);

  useEffect(() => {
    if (selectAllFeeds.current) {
      selectAllFeeds.current.indeterminate = selectedFeedIds.length > 0 && !allFeedsSelected;
    }
  }, [allFeedsSelected, selectedFeedIds.length]);

  async function create(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.createFeed({
        url,
        title: title || undefined,
        folder: folder || undefined,
        poll_interval_minutes: interval,
        domain_ids: domainIds,
      });
      await onChanged();
      setUrl("");
      setTitle("");
      setFolder("");
      setDomainIds([]);
      setTab("feeds");
      notify(zh ? "订阅已添加。" : "Feed added.");
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  async function discover() {
    setBusy(true);
    setError("");
    try {
      const result = await api.discoverFeed(url);
      setDiscovered(result.items);
      if (result.items[0]) {
        setUrl(result.items[0].url);
        setTitle(result.items[0].title);
      } else {
        setError(zh ? "没有发现 RSS/Atom 源，仍可直接尝试当前 URL。" : "No feed was discovered; you can still try this URL.");
      }
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  async function update(feed: Feed, patch: Partial<Feed> & { domain_ids?: number[] }) {
    setBusyId(feed.id);
    try {
      await api.updateFeed(feed.id, patch);
      await onChanged();
      return true;
    } catch (caught) {
      notify(errorText(caught), "error");
      return false;
    } finally {
      setBusyId(null);
    }
  }

  async function remove(feed: Feed) {
    if (!window.confirm(zh ? `删除订阅“${feed.title}”？已抓取文章仍会保留。` : `Delete “${feed.title}”? Existing articles are retained.`)) return;
    setBusyId(feed.id);
    try {
      await api.deleteFeed(feed.id);
      await onChanged();
    } catch (caught) {
      notify(errorText(caught), "error");
    } finally {
      setBusyId(null);
    }
  }

  function beginClassification(feed: Feed) {
    setEditingFeedId(feed.id);
    setEditFolder(feed.folder || "");
    setEditDomainIds(feed.domains.map((domain) => domain.id));
    setError("");
  }

  async function saveClassification(feed: Feed) {
    const saved = await update(feed, {
      folder: editFolder.trim() || null,
      domain_ids: editDomainIds,
    });
    if (saved) {
      setEditingFeedId(null);
      notify(zh ? "订阅分类已保存。" : "Feed categories saved.");
    }
  }

  async function associateSelectedDomains() {
    if (selectedFeedIds.length === 0 || bulkDomainIds.length === 0) return;
    setBulkBusy(true);
    try {
      const count = selectedFeedIds.length;
      await api.associateFeedDomains(selectedFeedIds, bulkDomainIds);
      await onChanged();
      setSelectedFeedIds([]);
      setBulkDomainIds([]);
      notify(zh ? `已为 ${count} 个订阅源关联所选领域。` : `Selected domains associated with ${count} feeds.`);
    } catch (caught) {
      notify(errorText(caught), "error");
    } finally {
      setBulkBusy(false);
    }
  }

  async function importOpml(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0];
    if (!selected) return;
    setBusy(true);
    try {
      const result = await api.importOpml(selected);
      await onChanged();
      notify(`OPML: +${result.imported}, ${result.skipped} skipped`);
      setTab("feeds");
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
      event.target.value = "";
    }
  }

  async function createDomain(event: FormEvent) {
    event.preventDefault();
    if (!domainName.trim()) return;
    setBusy(true);
    setError("");
    try {
      await api.createDomain({ name: domainName.trim(), position: domains.length });
      setDomainName("");
      await onChanged();
      notify(zh ? "领域分类已创建。" : "Domain category created.");
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  async function createFolderCategory(event: FormEvent) {
    event.preventDefault();
    if (!folderName.trim()) return;
    setBusy(true);
    setError("");
    try {
      await api.createFolder({ name: folderName.trim(), position: folders.length });
      setFolderName("");
      await onChanged();
      notify(zh ? "文件夹分类已创建。" : "Folder category created.");
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  function beginDomainEdit(domain: Domain) {
    setEditingDomainId(domain.id);
    setDomainDraft(domain.name);
    setDomainColorDraft(domain.color || "#2bc7c3");
  }

  async function saveDomain(domain: Domain) {
    if (!domainDraft.trim()) return;
    setBusyId(domain.id);
    try {
      await api.updateDomain(domain.id, { name: domainDraft.trim(), color: domainColorDraft });
      await onChanged();
      setEditingDomainId(null);
      notify(zh ? "领域分类已更新。" : "Domain category updated.");
    } catch (caught) {
      notify(errorText(caught), "error");
    } finally {
      setBusyId(null);
    }
  }

  async function removeDomain(domain: Domain) {
    const warning = zh
      ? `删除领域“${domain.name}”？它会从所有订阅源和文章中移除，但订阅源与文章会保留。`
      : `Delete domain “${domain.name}”? It will be removed from every feed and article, while feeds and articles remain.`;
    if (!window.confirm(warning)) return;
    setBusyId(domain.id);
    try {
      await api.deleteDomain(domain.id);
      await onChanged();
      notify(zh ? "领域分类已删除。" : "Domain category deleted.");
    } catch (caught) {
      notify(errorText(caught), "error");
    } finally {
      setBusyId(null);
    }
  }

  async function renameFolder(folderCategory: Folder) {
    const next = folderDraft.trim();
    if (!next) {
      setError(zh ? "文件夹名称不能为空。" : "Folder name cannot be empty.");
      return;
    }
    if (next === folderCategory.name) {
      setEditingFolder(null);
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api.updateFolder(folderCategory.id, { name: next });
      await onChanged();
      setEditingFolder(null);
      notify(zh ? "文件夹已重命名。" : "Folder renamed.");
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  async function removeFolder(folderCategory: Folder) {
    const warning = zh
      ? `移除文件夹“${folderCategory.name}”？其中 ${folderCategory.feed_count} 个订阅源会变为未分类，订阅源不会删除。`
      : `Remove folder “${folderCategory.name}”? Its ${folderCategory.feed_count} feeds will become uncategorized; no feed will be deleted.`;
    if (!window.confirm(warning)) return;
    setBusy(true);
    setError("");
    try {
      await api.deleteFolder(folderCategory.id);
      await onChanged();
      notify(zh ? "文件夹已移除，订阅源已归入未分类。" : "Folder removed; its feeds are now uncategorized.");
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  return <Modal title={t(locale, "feedManager")} eyebrow="SOURCE MANAGEMENT" onClose={onClose} wide>
    <div className="modal-tabs">
      {(["feeds", "add", "opml", "categories"] as Tab[]).map((item) => (
        <button
          key={item}
          className={tab === item ? "is-active" : ""}
          onClick={() => { setTab(item); setError(""); }}
        >
          {item === "feeds"
            ? `${t(locale, "myFeeds")} (${feeds.length})`
            : item === "add"
              ? t(locale, "addFeed")
              : item === "opml"
                ? "OPML"
                : (zh ? "分类管理" : "Categories")}
        </button>
      ))}
    </div>

    {tab === "feeds" && <div className="feed-manager-list">
      {feeds.length > 0 && <section className="feed-bulk-panel" aria-label={zh ? "批量关联领域" : "Bulk domain association"}>
        <div className="feed-bulk-panel__selection">
          <label>
            <input
              ref={selectAllFeeds}
              type="checkbox"
              checked={allFeedsSelected}
              disabled={bulkBusy}
              onChange={(event) => setSelectedFeedIds(event.target.checked ? feeds.map((feed) => feed.id) : [])}
              aria-label={zh ? "选择全部订阅源" : "Select all feeds"}
            />
            <span>{zh ? "全选" : "Select all"}</span>
          </label>
          <strong>{zh ? `已选择 ${selectedFeedIds.length} 个订阅源` : `${selectedFeedIds.length} feeds selected`}</strong>
          {selectedFeedIds.length > 0 && <button type="button" className="text-button" disabled={bulkBusy} onClick={() => setSelectedFeedIds([])}>{zh ? "清除" : "Clear"}</button>}
        </div>
        {domains.length > 0
          ? <div className="feed-bulk-panel__association">
            <fieldset disabled={bulkBusy}>
              <legend>{zh ? "选择要关联的领域" : "Choose domains to associate"}</legend>
              <div className="bulk-domain-picker">
                {domains.map((domain) => {
                  const selected = bulkDomainIds.includes(domain.id);
                  return <label className={selected ? "is-selected" : ""} key={domain.id}>
                    <input
                      type="checkbox"
                      checked={selected}
                      onChange={() => setBulkDomainIds((current) => current.includes(domain.id) ? current.filter((id) => id !== domain.id) : [...current, domain.id])}
                      aria-label={`${zh ? "关联领域" : "Associate domain"} ${domain.name}`}
                    />
                    <span className="bulk-domain-picker__color" style={{ backgroundColor: domain.color || "#2bc7c3" }} />
                    <span>{domain.name}</span>
                    <small aria-hidden="true">{selected ? "✓" : "+"}</small>
                  </label>;
                })}
              </div>
            </fieldset>
            <button
              type="button"
              className="button button--primary"
              disabled={bulkBusy || selectedFeedIds.length === 0 || bulkDomainIds.length === 0}
              onClick={() => void associateSelectedDomains()}
            >
              {bulkBusy ? (zh ? "关联中…" : "Associating…") : (zh ? "一键关联" : "Associate")}
            </button>
          </div>
          : <p className="feed-bulk-panel__empty">{zh ? "请先在“分类管理”中创建领域。" : "Create a domain under Categories first."}</p>}
      </section>}
      {feeds.length === 0
        ? <EmptyState title={t(locale, "noEntries")} description={t(locale, "emptyHelp")} action={<button className="button button--primary" onClick={() => setTab("add")}>{t(locale, "addFeed")}</button>} />
        : feeds.map((feed) => (
          <div className={`managed-feed ${editingFeedId === feed.id ? "is-editing" : ""} ${selectedFeedIds.includes(feed.id) ? "is-selected" : ""}`} key={feed.id}>
            <label className="managed-feed__select">
              <input
                type="checkbox"
                checked={selectedFeedIds.includes(feed.id)}
                disabled={bulkBusy}
                onChange={(event) => setSelectedFeedIds((current) => event.target.checked ? [...current, feed.id] : current.filter((id) => id !== feed.id))}
                aria-label={`${zh ? "选择订阅源" : "Select feed"} ${feed.title}`}
              />
            </label>
            <div className="managed-feed__status">
              <span className={`health-ring health-ring--${feed.status === "error" ? "error" : feed.enabled ? "healthy" : "paused"}`}>
                {feed.status === "error" ? "!" : feed.enabled ? "✓" : "Ⅱ"}
              </span>
            </div>
            <div className="managed-feed__main">
              <strong>{feed.title}</strong>
              <span>{feed.url}</span>
              <small>{feed.folder || (zh ? "未分类" : "Uncategorized")} · {feed.poll_interval_minutes} min · {formatDateTime(feed.last_fetched_at, locale)}</small>
              <div className="category-list">
                {feed.domains.map((domain) => <span key={domain.id}>{domain.name}</span>)}
                {feed.domains.length === 0 && <small>{zh ? "未关联领域" : "No domains"}</small>}
              </div>
              {feed.last_error && <p>{feed.last_error}</p>}
            </div>
            <div className="managed-feed__actions">
              <Toggle checked={feed.enabled} onChange={(enabled) => void update(feed, { enabled })} label={feed.enabled ? "On" : "Off"} disabled={busyId === feed.id} />
              <button className="button button--secondary button--small" aria-label={`${zh ? "编辑分类" : "Edit categories"} ${feed.title}`} onClick={() => beginClassification(feed)} disabled={busyId === feed.id}>{zh ? "分类" : "Categories"}</button>
              <button className="button button--secondary button--small" onClick={() => void api.refreshFeed(feed.id).then(onChanged).catch((caught) => notify(errorText(caught), "error"))} disabled={busyId === feed.id}>↻</button>
              <button className="button button--danger-quiet button--small" onClick={() => void remove(feed)} disabled={busyId === feed.id}>×</button>
            </div>
            {editingFeedId === feed.id && <div className="feed-classification-editor">
              <div>
                <span className="eyebrow">{zh ? "订阅源分类" : "FEED CATEGORIES"}</span>
                <p>{zh ? "文件夹控制侧栏分组；领域用于筛选和交叉阅读。" : "Folders group sources in the sidebar; domains power filtering and cross-field reading."}</p>
              </div>
              <div className="field">
                <span>{t(locale, "folder")}</span>
                <ComboBox value={editFolder} onChange={setEditFolder} options={folderOptions} label={t(locale, "folder")} placeholder={zh ? "输入或选择文件夹" : "Type or choose a folder"} />
              </div>
              {domains.length > 0
                ? <fieldset className="domain-picker domain-picker--compact">
                  <legend>{t(locale, "domains")}</legend>
                  {domains.map((domain) => <label key={domain.id}><input type="checkbox" checked={editDomainIds.includes(domain.id)} onChange={() => setEditDomainIds((current) => current.includes(domain.id) ? current.filter((id) => id !== domain.id) : [...current, domain.id])} />{domain.name}</label>)}
                </fieldset>
                : <p className="muted">{zh ? "尚未创建领域分类，可以在“分类管理”中创建。" : "No domain categories yet. Create one under Categories."}</p>}
              <div className="feed-classification-editor__actions">
                <button type="button" className="text-button" onClick={() => setEditingFeedId(null)}>{zh ? "取消" : "Cancel"}</button>
                <button type="button" className="button button--primary button--small" disabled={busyId === feed.id} onClick={() => void saveClassification(feed)}>{zh ? "保存分类" : "Save categories"}</button>
              </div>
            </div>}
          </div>
        ))}
    </div>}

    {tab === "add" && <form className="feed-form" onSubmit={create}>
      {error && <ErrorNotice message={error} compact />}
      <label className="field"><span>{t(locale, "url")}</span><div className="field-with-action"><input type="url" required value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://example.com/feed.xml" /><button type="button" onClick={() => void discover()} disabled={busy || !url}>{t(locale, "discover")}</button></div></label>
      {discovered.length > 1 && <div className="discovery-results">{discovered.map((candidate) => <button key={candidate.url} type="button" onClick={() => { setUrl(candidate.url); setTitle(candidate.title); }}>{candidate.title}</button>)}</div>}
      <div className="form-grid">
        <label className="field"><span>{t(locale, "displayName")}</span><input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
        <div className="field"><span>{t(locale, "folder")}</span><ComboBox value={folder} onChange={setFolder} options={folderOptions} label={t(locale, "folder")} placeholder={zh ? "输入或选择文件夹" : "Type or choose a folder"} /></div>
        <div className="field"><span>{t(locale, "interval")}</span><SelectMenu value={String(interval)} onChange={(value) => setInterval(Number(value))} label={t(locale, "interval")} options={[30, 45, 60, 120, 360].map((minutes) => ({ value: String(minutes), label: `${minutes} min` }))} /></div>
      </div>
      {domains.length > 0 && <fieldset className="domain-picker"><legend>{t(locale, "domains")}</legend>{domains.map((domain) => <label key={domain.id}><input type="checkbox" checked={domainIds.includes(domain.id)} onChange={() => setDomainIds((current) => current.includes(domain.id) ? current.filter((id) => id !== domain.id) : [...current, domain.id])} />{domain.name}</label>)}</fieldset>}
      <div className="form-actions"><button className="button button--primary" disabled={busy}>{t(locale, "addAndSync")}</button></div>
    </form>}

    {tab === "opml" && <div className="opml-panel">
      {error && <ErrorNotice message={error} compact />}
      <div className="opml-card"><span className="opml-card__mark">IN</span><div><h3>{t(locale, "importOpml")}</h3><p>{zh ? "文件夹和 Affogato RSS Reader 领域关联会一并导入。" : "Folders and Affogato RSS Reader domain associations are preserved."}</p><button className="button button--primary" onClick={() => file.current?.click()}>{t(locale, "importOpml")}</button><input ref={file} hidden type="file" accept=".opml,.xml" onChange={(event) => void importOpml(event)} /></div></div>
      <div className="opml-card"><span className="opml-card__mark">OUT</span><div><h3>{t(locale, "exportOpml")}</h3><button className="button button--secondary" onClick={() => void api.downloadOpml().then((blob) => downloadBlob(blob, "affogato-rss-reader-subscriptions.opml"))}>{t(locale, "exportOpml")}</button></div></div>
    </div>}

    {tab === "categories" && <div className="category-manager">
      {error && <ErrorNotice message={error} compact />}
      <section className="category-manager__section">
        <div className="category-manager__heading"><div><span className="eyebrow">FOLDERS</span><h3>{zh ? "文件夹分类" : "Folder categories"}</h3></div><p>{zh ? "文件夹只负责在侧栏中组织订阅源。" : "Folders organize feeds in the sidebar."}</p></div>
        <form className="field-with-action category-manager__create" onSubmit={createFolderCategory}><input value={folderName} onChange={(event) => setFolderName(event.target.value)} placeholder={zh ? "新建文件夹" : "Create folder"} maxLength={160} aria-label={zh ? "新文件夹名称" : "New folder name"} /><button className="button button--primary" disabled={busy || !folderName.trim()}>+</button></form>
        {folders.length === 0 && <p className="category-manager__empty">{zh ? "还没有文件夹分类。" : "No folder categories yet."}</p>}
        {folders.map((folderCategory) => {
          return <div className="category-manager__row" key={folderCategory.id}>
            {editingFolder === folderCategory.name
              ? <label className="field category-manager__edit"><span>{zh ? "文件夹名称" : "Folder name"}</span><input value={folderDraft} onChange={(event) => setFolderDraft(event.target.value)} maxLength={160} autoFocus /></label>
              : <div><strong>{folderCategory.name}</strong><small>{folderCategory.feed_count} {zh ? "个订阅源" : folderCategory.feed_count === 1 ? "feed" : "feeds"}</small></div>}
            <div>
              {editingFolder === folderCategory.name
                ? <><button className="button button--primary button--small" disabled={busy || !folderDraft.trim()} onClick={() => void renameFolder(folderCategory)}>{t(locale, "save")}</button><button className="text-button" onClick={() => setEditingFolder(null)}>{zh ? "取消" : "Cancel"}</button></>
                : <><button className="button button--secondary button--small" onClick={() => { setEditingFolder(folderCategory.name); setFolderDraft(folderCategory.name); }}>{zh ? "重命名" : "Rename"}</button><button className="button button--danger-quiet button--small" disabled={busy} onClick={() => void removeFolder(folderCategory)}>{zh ? "移除" : "Remove"}</button></>}
            </div>
          </div>;
        })}
      </section>

      <section className="category-manager__section">
        <div className="category-manager__heading"><div><span className="eyebrow">DOMAINS</span><h3>{zh ? "领域分类" : "Domain categories"}</h3></div><p>{zh ? "一个订阅源可以属于多个领域。" : "A feed can belong to multiple domains."}</p></div>
        <form className="field-with-action category-manager__create" onSubmit={createDomain}><input value={domainName} onChange={(event) => setDomainName(event.target.value)} placeholder={t(locale, "createDomain")} maxLength={120} /><button className="button button--primary" disabled={busy || !domainName.trim()}>+</button></form>
        {domains.length === 0 && <p className="category-manager__empty">{zh ? "领域是可选的；文件夹仍可独立使用。" : "Domains are optional; folders remain independent."}</p>}
        {domains.map((domain) => <div className="category-manager__row" key={domain.id}>
          {editingDomainId === domain.id
            ? <div className="category-manager__domain-edit"><input type="color" value={domainColorDraft} onChange={(event) => setDomainColorDraft(event.target.value)} aria-label={zh ? "领域颜色" : "Domain color"} /><label className="field category-manager__edit"><span>{zh ? "领域名称" : "Domain name"}</span><input value={domainDraft} onChange={(event) => setDomainDraft(event.target.value)} maxLength={120} autoFocus /></label></div>
            : <div className="category-manager__domain-name"><span style={{ backgroundColor: domain.color || "#2bc7c3" }} /><div><strong>{domain.name}</strong><small>{domain.feed_count} {zh ? "个订阅源" : "feeds"} · {domain.entry_count} {zh ? "篇文章" : "entries"}</small></div></div>}
          <div>
            {editingDomainId === domain.id
              ? <><button className="button button--primary button--small" disabled={busyId === domain.id || !domainDraft.trim()} onClick={() => void saveDomain(domain)}>{t(locale, "save")}</button><button className="text-button" onClick={() => setEditingDomainId(null)}>{zh ? "取消" : "Cancel"}</button></>
              : <><button className="button button--secondary button--small" onClick={() => beginDomainEdit(domain)}>{zh ? "编辑" : "Edit"}</button><button className="button button--danger-quiet button--small" disabled={busyId === domain.id} onClick={() => void removeDomain(domain)}>{zh ? "删除" : "Delete"}</button></>}
          </div>
        </div>)}
      </section>
    </div>}
  </Modal>;
}
