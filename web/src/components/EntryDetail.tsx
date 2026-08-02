import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import type { Domain, Entry, EntryState, LanguageMode, Locale, Tag } from "../types";
import { t } from "../i18n";
import { authors, formatArxivIdentifier, formatFullDate } from "../utils";
import { EmptyState, ErrorNotice, SegmentedControl, Spinner } from "./Common";

function DetailActionIcon({ kind, filled = false }: {
  kind: "read" | "later" | "star" | "archive";
  filled?: boolean;
}) {
  return <svg className="detail-toolbar__icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    {kind === "read" && <><circle cx="12" cy="12" r="8.5" /><path d="m7.5 12.2 3 3 6-6.4" /></>}
    {kind === "later" && <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5h5" /></>}
    {kind === "star" && <path className={filled ? "is-filled" : ""} d="m12 2.8 2.85 5.78 6.38.93-4.62 4.5 1.09 6.36L12 17.36l-5.7 3 1.09-6.36-4.62-4.5 6.38-.93L12 2.8Z" />}
    {kind === "archive" && <rect x="3.5" y="3.5" width="17" height="17" rx="1.5" />}
  </svg>;
}

function TagPicker({ locale, allTags, selectedTags, onAddTag, onCreateTag }: {
  locale: Locale;
  allTags: Tag[];
  selectedTags: Tag[];
  onAddTag: (tag: Tag) => void;
  onCreateTag: (name: string) => Promise<Tag>;
}) {
  const [input, setInput] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const availableTags = useMemo(() => {
    const selectedIds = new Set(selectedTags.map((tag) => tag.id));
    const query = input.trim().toLocaleLowerCase();
    return allTags
      .filter((tag) => !selectedIds.has(tag.id))
      .filter((tag) => !query || tag.name.toLocaleLowerCase().includes(query))
      .sort((first, second) => first.name.localeCompare(second.name, locale));
  }, [allTags, input, locale, selectedTags]);

  useEffect(() => {
    if (!menuOpen) return;
    const closeOutside = (event: PointerEvent) => {
      if (event.target instanceof Node && !rootRef.current?.contains(event.target)) setMenuOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, [menuOpen]);

  async function addTypedTag() {
    const name = input.trim();
    if (!name || busy) return;
    setBusy(true);
    try {
      const existing = allTags.find((tag) => tag.name.toLocaleLowerCase() === name.toLocaleLowerCase());
      const tag = existing ?? await onCreateTag(name);
      if (!selectedTags.some((item) => item.id === tag.id)) onAddTag(tag);
      setInput("");
      setMenuOpen(false);
    } finally {
      setBusy(false);
    }
  }

  function chooseTag(tag: Tag) {
    onAddTag(tag);
    setInput("");
    setMenuOpen(false);
    inputRef.current?.focus();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      void addTypedTag();
    } else if (event.key === "Escape") {
      setMenuOpen(false);
    }
  }

  const menuLabel = locale === "zh-CN" ? "选择已有标签" : "Choose an existing tag";
  const toggleLabel = menuOpen
    ? (locale === "zh-CN" ? "收起标签选择菜单" : "Hide tag suggestions")
    : (locale === "zh-CN" ? "展开标签选择菜单" : "Show tag suggestions");
  return <div className="tag-input-wrap" ref={rootRef}>
    <div className={`tag-picker__field ${menuOpen ? "is-open" : ""}`}>
      <input
        ref={inputRef}
        value={input}
        onChange={(event) => setInput(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={`+ ${t(locale, "addTag")}`}
        aria-label={t(locale, "addTag")}
      />
      <button
        type="button"
        className="tag-picker__toggle"
        aria-label={toggleLabel}
        aria-expanded={menuOpen}
        aria-controls="tag-picker-menu"
        onClick={() => setMenuOpen((open) => !open)}
      ><span aria-hidden="true" /></button>
    </div>
    {menuOpen && <div className="tag-picker__menu" id="tag-picker-menu" role="listbox" aria-label={menuLabel}>
      <div className="tag-picker__menu-heading">
        <div><span className="eyebrow">TAGS</span><strong>{menuLabel}</strong></div>
        <span>{availableTags.length}</span>
      </div>
      {availableTags.length > 0 ? <div className="tag-picker__options">
        {availableTags.map((tag) => <button type="button" role="option" aria-selected="false" key={tag.id} onClick={() => chooseTag(tag)}>
          <span className="tag-picker__color" style={{ backgroundColor: tag.color || undefined }} />
          <span className="tag-picker__name">{tag.name}</span>
          {typeof tag.entry_count === "number" && <small>{tag.entry_count}</small>}
        </button>)}
      </div> : <div className="tag-picker__empty">
        <span>＋</span>
        <p>{locale === "zh-CN" ? "没有匹配的已有标签；按 Enter 创建新标签。" : "No matching tags. Press Enter to create a new one."}</p>
      </div>}
    </div>}
  </div>;
}

export function EntryDetail({ locale, entry, loading, error, languageMode, allTags, allDomains, onLanguageMode, onState, onAddTag, onRemoveTag, onCreateTag, onDomains, onBack, onRetry }: {
  locale: Locale; entry: Entry | null; loading: boolean; error: string; languageMode: LanguageMode; allTags: Tag[]; allDomains: Domain[];
  onLanguageMode: (mode: LanguageMode) => void; onState: (state: Partial<EntryState>) => void; onAddTag: (tag: Tag) => void;
  onRemoveTag: (tag: Tag) => void; onCreateTag: (name: string) => Promise<Tag>; onDomains: (ids: number[]) => void;
  onBack: () => void; onRetry: () => void;
}) {
  if (loading && !entry) return <section className="detail-pane detail-pane--center"><Spinner /></section>;
  if (error && !entry) return <section className="detail-pane detail-pane--center"><ErrorNotice message={error} retry={onRetry} /></section>;
  if (!entry) return <section className="detail-pane detail-pane--center"><EmptyState symbol="◫" title={t(locale, "selectArticle")} description={locale === "zh-CN" ? "摘要、元数据和标签会在这里展开。" : "Summary, metadata and tags appear here."} /><div className="keyboard-hint"><span><kbd>J</kbd>/<kbd>K</kbd></span><span><kbd>S</kbd> Star</span><span><kbd>M</kbd> Read</span></div></section>;
  const showOriginal = languageMode !== "translated";
  const showTranslation = languageMode !== "original";
  const arxivUsed = Boolean(entry.arxiv_id || entry.feed_titles?.some((name) => name.toLowerCase().includes("arxiv")));
  const readLabel = entry.state.read ? (locale === "zh-CN" ? "标为未读" : "Mark as unread") : (locale === "zh-CN" ? "标为已读" : "Mark as read");
  const laterLabel = entry.state.later ? (locale === "zh-CN" ? "移出稍后读" : "Remove from read later") : (locale === "zh-CN" ? "加入稍后读" : "Read later");
  const starLabel = entry.state.starred ? (locale === "zh-CN" ? "取消收藏" : "Unstar") : (locale === "zh-CN" ? "收藏" : "Star");
  const archiveLabel = entry.state.archived ? (locale === "zh-CN" ? "取消归档" : "Unarchive") : (locale === "zh-CN" ? "归档" : "Archive");
  const translationPlaceholder = entry.translation_status === "failed" ? t(locale, "translationFailed") : t(locale, "notTranslated");
  return <article className={`detail-pane ${entry.state.read ? "is-read" : ""}`}><header className="detail-toolbar"><button className="mobile-back" onClick={onBack}>←</button><SegmentedControl label="Summary language" value={languageMode} onChange={onLanguageMode} options={[{ value: "original", label: t(locale, "original") }, { value: "translated", label: t(locale, "translated") }, { value: "bilingual", label: t(locale, "bilingual") }]} /><div className="detail-toolbar__actions"><button aria-label={readLabel} data-tooltip={readLabel} onClick={() => onState({ read: !entry.state.read })}><DetailActionIcon kind="read" /></button><button aria-label={laterLabel} data-tooltip={laterLabel} className={entry.state.later ? "is-active" : ""} onClick={() => onState({ later: !entry.state.later })}><DetailActionIcon kind="later" /></button><button aria-label={starLabel} data-tooltip={starLabel} className={`star-button ${entry.state.starred ? "is-active" : ""}`} onClick={() => onState({ starred: !entry.state.starred })}><DetailActionIcon kind="star" filled={entry.state.starred} /></button><button aria-label={archiveLabel} data-tooltip={archiveLabel} className={entry.state.archived ? "is-active" : ""} onClick={() => onState({ archived: !entry.state.archived })}><DetailActionIcon kind="archive" /></button></div></header><div className="detail-scroll"><div className="article-meta-top"><div>{(entry.feed_titles ?? ["RSS"]).map((feed) => <span className="source-pill source-pill--large" key={feed}>{feed}</span>)}</div><time>{formatFullDate(entry.published_at, locale)}</time></div>{showTranslation && entry.translated_title && <h1 className="article-title article-title--translated">{entry.translated_title}</h1>}{(showOriginal || !entry.translated_title) && <h1 className={`article-title ${entry.translated_title && showTranslation ? "article-title--original" : ""}`}>{entry.title}</h1>}<div className="author-list">{authors(entry).map((author) => <span key={author}>{author}</span>)}</div><div className="article-actions"><a className="button button--primary" href={entry.url} target="_blank" rel="noreferrer">{t(locale, "openOriginal")} ↗</a>{entry.doi && <a className="button button--secondary" href={`https://doi.org/${entry.doi.replace(/^https?:\/\/doi\.org\//, "")}`} target="_blank" rel="noreferrer">DOI</a>}{entry.arxiv_id && <span className="identifier">{formatArxivIdentifier(entry.arxiv_id, entry.arxiv_version)}</span>}{entry.announce_type && <span className="announce-type">{entry.announce_type}</span>}</div><hr /><section className="abstract-section"><span className="eyebrow">SUMMARY</span>{showTranslation && <div className="abstract-block abstract-block--translated"><h2>{t(locale, "translatedSummary")}</h2><p>{entry.translated_summary || translationPlaceholder}</p></div>}{showOriginal && <div className="abstract-block"><h2>{t(locale, "originalSummary")}</h2><p>{entry.summary || t(locale, "noSummary")}</p></div>}</section>
    {allDomains.length > 0 && <section className="metadata-section"><span className="eyebrow">DOMAINS</span><div className="tag-editor">{allDomains.map((domain) => <button className={entry.domains.some((item) => item.id === domain.id) ? "is-active" : ""} key={domain.id} onClick={() => onDomains(entry.domains.some((item) => item.id === domain.id) ? entry.domains.filter((item) => item.id !== domain.id).map((item) => item.id) : [...entry.domains.map((item) => item.id), domain.id])}><span style={{ backgroundColor: domain.color || undefined }} />{domain.name}</button>)}</div></section>}
    {entry.categories && entry.categories.length > 0 && <section className="metadata-section"><span className="eyebrow">CATEGORIES</span><div className="category-list">{entry.categories.map((item) => <span key={item}>{item}</span>)}</div></section>}<section className="metadata-section"><span className="eyebrow">{t(locale, "yourTags")}</span><div className="tag-editor">{entry.tags.map((tag) => <button key={tag.id} onClick={() => onRemoveTag(tag)}><span style={{ backgroundColor: tag.color || undefined }} />{tag.name} ×</button>)}<TagPicker key={entry.id} locale={locale} allTags={allTags} selectedTags={entry.tags} onAddTag={onAddTag} onCreateTag={onCreateTag} /></div></section><footer className="article-footer"><p>{locale === "zh-CN" ? "内容来自所订阅的 RSS/Atom 源，版权归原作者及发布方所有。" : "Content comes from subscribed RSS/Atom sources and remains the property of its authors and publishers."}</p>{arxivUsed && <p>arXiv data courtesy of arXiv.org · <a href="https://info.arxiv.org/help/api/index.html" target="_blank" rel="noreferrer">Usage terms</a></p>}</footer></div></article>;
}
