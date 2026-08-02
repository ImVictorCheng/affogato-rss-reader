import { createContext, type PropsWithChildren, useContext, useEffect, useId, useRef, useState } from "react";
import { BRAND } from "../brand";
import type { SiteIdentity } from "../types";

const BrandIdentityContext = createContext<SiteIdentity | null>(null);

export function BrandProvider({ identity, children }: PropsWithChildren<{ identity?: SiteIdentity | null }>) {
  return <BrandIdentityContext.Provider value={identity || null}>{children}</BrandIdentityContext.Provider>;
}

function LogoGlyph({ id }: { id: string }) {
  switch (id) {
    case "science":
      return <><path d="M18 10h12M21 10v10L13 35c-1 3 1 5 4 5h14c3 0 5-2 4-5l-8-15V10" /><path d="M17 31h14" /><circle cx="22" cy="35" r="1.8" /></>;
    case "physics":
      return <><path d="M8 25c6-16 10 16 16 0s10 16 16 0" /><path d="M8 15h32M8 35h32" opacity=".32" /></>;
    case "quantum-physics":
      return <><ellipse cx="24" cy="24" rx="17" ry="8" transform="rotate(32 24 24)" /><ellipse cx="24" cy="24" rx="17" ry="8" transform="rotate(-32 24 24)" /><circle cx="24" cy="24" r="3" fill="currentColor" stroke="none" /></>;
    case "computing":
      return <><rect x="10" y="10" width="11" height="11" rx="2" /><rect x="27" y="10" width="11" height="11" rx="2" /><rect x="10" y="27" width="11" height="11" rx="2" /><rect x="27" y="27" width="11" height="11" rx="2" /></>;
    case "artificial-intelligence":
      return <><circle cx="12" cy="15" r="3" /><circle cx="12" cy="33" r="3" /><circle cx="25" cy="11" r="3" /><circle cx="25" cy="24" r="3" /><circle cx="25" cy="37" r="3" /><circle cx="38" cy="18" r="3" /><circle cx="38" cy="31" r="3" /><path d="M15 15l7-4m-7 5 7 8m-7 8 7-8m-7 9 7 4m6-25 7 6m-7 6 7-6m-7 6 7 7m-7 6 7-6" /></>;
    case "mathematics":
      return <><path d="M36 10H14l12 14-12 14h22" strokeWidth="3" /><circle cx="37" cy="10" r="2" fill="currentColor" stroke="none" /><circle cx="37" cy="38" r="2" fill="currentColor" stroke="none" /></>;
    case "finance":
      return <><path d="M8 37h32M12 33V23m8 10V15m8 18V25m8 8V10" strokeWidth="3" /><path d="m10 19 9-8 9 7 10-11" /></>;
    case "engineering":
      return <><path d="M24 8v6m0 20v6M8 24h6m20 0h6m-5.3-11.3-4.2 4.2m-13 13-4.2 4.2m0-21.4 4.2 4.2m13 13 4.2 4.2" strokeWidth="3" /><circle cx="24" cy="24" r="10" /><circle cx="24" cy="24" r="4" /></>;
    case "semiconductor":
      return <><rect x="13" y="13" width="22" height="22" rx="3" /><rect x="19" y="19" width="10" height="10" rx="1" /><path d="M18 7v6m12-6v6M18 35v6m12-6v6M7 18h6m-6 12h6m22-12h6m-6 12h6" /></>;
    case "eda":
      return <><path d="M9 11h12v10h8v8h10M9 37h10V27h10" strokeWidth="2.5" /><circle cx="9" cy="11" r="3" fill="currentColor" stroke="none" /><circle cx="9" cy="37" r="3" fill="currentColor" stroke="none" /><circle cx="39" cy="29" r="3" fill="currentColor" stroke="none" /><rect x="20" y="18" width="12" height="12" rx="2" /></>;
    case "communications-signal-processing":
      return <><path d="M7 28c5-8 9-8 14 0s9 8 14 0 7-8 9-5" strokeWidth="2.5" /><path d="M12 17c8-7 16-7 24 0M17 11c5-3 9-3 14 0" opacity=".55" /><circle cx="24" cy="34" r="3" fill="currentColor" stroke="none" /></>;
    case "materials":
      return <><path d="m24 7 16 9-16 9L8 16l16-9Z" /><path d="m8 24 16 9 16-9M8 32l16 9 16-9" /><circle cx="24" cy="16" r="2.5" fill="currentColor" stroke="none" /></>;
    default:
      return <><circle cx="24" cy="24" r="14" /><path d="M10 24h28M24 10v28" /></>;
  }
}

function GeneratedLogo({ identity }: { identity: SiteIdentity }) {
  const secondary = identity.secondary_template;
  return <svg className={`generated-logo ${secondary ? "generated-logo--pair" : ""}`} viewBox="0 0 48 48" aria-hidden="true">
    <rect x="2" y="2" width="44" height="44" rx="13" className="generated-logo__plate" />
    {secondary ? <>
      <g className="generated-logo__primary" transform="translate(-3 -3) scale(.72)"><LogoGlyph id={identity.primary_template || ""} /></g>
      <path className="generated-logo__divider" d="M13 39 37 9" />
      <g className="generated-logo__secondary" transform="translate(17 17) scale(.72)"><LogoGlyph id={secondary} /></g>
    </> : <g className="generated-logo__primary"><LogoGlyph id={identity.primary_template || ""} /></g>}
  </svg>;
}

export function Brand({ compact = false, identity }: { compact?: boolean; identity?: SiteIdentity | null }) {
  const inherited = useContext(BrandIdentityContext);
  const active = identity || inherited || { name: BRAND.name, source: "default", logo_kind: "default" };
  const isDefault = active.source === "default";
  return (
    <div className={`brand ${compact ? "brand--compact" : ""}`} aria-label={active.name}>
      {active.logo_kind === "generated" ? <GeneratedLogo identity={active} /> : active.logo_kind === "upload" && active.logo_data_url
        ? <img className="uploaded-logo" src={active.logo_data_url} alt="" />
        : <div className="reader-mark" aria-hidden="true">
          <span className="reader-mark__core" />
          <span className="reader-mark__ring reader-mark__ring--one" />
          <span className="reader-mark__ring reader-mark__ring--two" />
        </div>}
      {!compact && <div className={`brand__wordmark ${isDefault ? "" : "brand__wordmark--custom"}`}>
        {isDefault ? <><strong>RSS</strong><span>READER</span></> : <><strong>{active.name}</strong><span>{active.secondary_template ? "CROSS-FIELD DIGEST" : "FIELD DIGEST"}</span></>}
      </div>}
    </div>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return <div className="spinner-wrap" role="status"><span className="spinner" /><span>{label}</span></div>;
}

export function SkeletonList() {
  return <div className="skeleton-list">{[0, 1, 2, 3].map((item) => <div className="skeleton-card" key={item}><span className="skeleton skeleton--short" /><span className="skeleton skeleton--title" /><span className="skeleton skeleton--body" /></div>)}</div>;
}

export function ErrorNotice({ message, retry, compact = false }: { message: string; retry?: () => void; compact?: boolean }) {
  return <div className={`error-notice ${compact ? "error-notice--compact" : ""}`} role="alert"><span className="error-notice__symbol">!</span><div><strong>Request failed</strong><p>{message}</p></div>{retry && <button className="button button--secondary button--small" onClick={retry}>Retry</button>}</div>;
}

export function EmptyState({ symbol = "∅", title, description, action }: { symbol?: string; title: string; description: string; action?: React.ReactNode }) {
  return <div className="empty-state"><span className="empty-state__symbol">{symbol}</span><h3>{title}</h3><p>{description}</p>{action}</div>;
}

export function Modal({ title, eyebrow, onClose, children, wide = false }: PropsWithChildren<{ title: string; eyebrow?: string; onClose: () => void; wide?: boolean }>) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const listener = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", listener);
    panel.current?.focus();
    return () => window.removeEventListener("keydown", listener);
  }, [onClose]);
  return <div className="modal-backdrop" onMouseDown={onClose}><div className={`modal ${wide ? "modal--wide" : ""}`} role="dialog" aria-modal="true" tabIndex={-1} ref={panel} onMouseDown={(event) => event.stopPropagation()}><header className="modal__header"><div>{eyebrow && <span className="eyebrow">{eyebrow}</span>}<h2>{title}</h2></div><button className="icon-button" onClick={onClose} aria-label="Close">×</button></header><div className="modal__body">{children}</div></div></div>;
}

export function SegmentedControl<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string }[]; onChange: (value: T) => void; label: string }) {
  return <div className="segmented" role="group" aria-label={label}>{options.map((option) => <button key={option.value} className={value === option.value ? "is-active" : ""} onClick={() => onChange(option.value)} aria-pressed={value === option.value}>{option.label}</button>)}</div>;
}

export interface DropdownOption<T extends string> {
  value: T;
  label: string;
  meta?: string;
}

/**
 * Shared dropdown primitives for the whole application.
 * Use SelectMenu for fixed choices and ComboBox for editable choices; product
 * code must not introduce native select/datalist controls with platform-specific styling.
 */
function DropdownMenu<T extends string>({ id, value, options, label, onChoose }: {
  id: string;
  value: T;
  options: DropdownOption<T>[];
  label: string;
  onChoose: (value: T) => void;
}) {
  return <div className="app-dropdown__menu" id={id} role="listbox" aria-label={label}>
    {options.map((option) => {
      const selected = option.value === value;
      return <button
        type="button"
        className={`app-dropdown__option ${selected ? "is-selected" : ""}`}
        role="option"
        aria-selected={selected}
        key={option.value || "__empty__"}
        onClick={() => onChoose(option.value)}
      >
        <span className="app-dropdown__check" aria-hidden="true">{selected ? "✓" : ""}</span>
        <span className="app-dropdown__option-label">{option.label}</span>
        {option.meta && <small>{option.meta}</small>}
      </button>;
    })}
  </div>;
}

export function SelectMenu<T extends string>({ value, options, onChange, label, placeholder, valueLabel, compact = false, disabled = false }: {
  value: T;
  options: DropdownOption<T>[];
  onChange: (value: T) => void;
  label: string;
  placeholder?: string;
  valueLabel?: string;
  compact?: boolean;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const menuId = `${useId()}-menu`;
  const selected = options.find((option) => option.value === value);
  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, [open]);
  return <div className={`app-dropdown ${compact ? "app-dropdown--compact" : ""} ${open ? "is-open" : ""}`} ref={root}>
    <button
      type="button"
      className="app-dropdown__trigger"
      role="combobox"
      aria-label={label}
      aria-haspopup="listbox"
      aria-expanded={open}
      aria-controls={menuId}
      disabled={disabled}
      onClick={() => setOpen((current) => !current)}
      onKeyDown={(event) => {
        if (event.key === "ArrowDown") { event.preventDefault(); setOpen(true); }
        if (event.key === "Escape") setOpen(false);
      }}
    >
      <span className={selected ? "" : "is-placeholder"}>{valueLabel || selected?.label || placeholder || label}</span>
      <span className="app-dropdown__arrow" aria-hidden="true" />
    </button>
    {open && <DropdownMenu id={menuId} value={value} options={options} label={label} onChoose={(next) => { onChange(next); setOpen(false); }} />}
  </div>;
}

export function ComboBox({ value, options, onChange, label, placeholder, disabled = false }: {
  value: string;
  options: DropdownOption<string>[];
  onChange: (value: string) => void;
  label: string;
  placeholder?: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const menuId = `${useId()}-menu`;
  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, [open]);
  return <div className={`app-dropdown app-combobox ${open ? "is-open" : ""}`} ref={root}>
    <div className="app-combobox__field">
      <input
        className="app-combobox__input"
        role="combobox"
        aria-label={label}
        aria-autocomplete="list"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={menuId}
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        maxLength={160}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown") { event.preventDefault(); setOpen(true); }
          if (event.key === "Escape") setOpen(false);
        }}
      />
      <button
        type="button"
        className="app-combobox__toggle"
        aria-label={`${open ? "Hide" : "Show"} ${label} options`}
        aria-expanded={open}
        aria-controls={menuId}
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
      ><span className="app-dropdown__arrow" aria-hidden="true" /></button>
    </div>
    {open && <DropdownMenu id={menuId} value={value} options={options} label={label} onChoose={(next) => { onChange(next); setOpen(false); }} />}
  </div>;
}

export function Toggle({ checked, onChange, label, disabled = false }: { checked: boolean; onChange: (checked: boolean) => void; label: string; disabled?: boolean }) {
  return <label className={`toggle ${disabled ? "is-disabled" : ""}`}><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} disabled={disabled} /><span className="toggle__track"><span /></span><span>{label}</span></label>;
}

export function Toast({ message, tone = "success", onDismiss }: { message: string; tone?: "success" | "error"; onDismiss: () => void }) {
  useEffect(() => {
    const timer = window.setTimeout(onDismiss, 3600);
    return () => window.clearTimeout(timer);
  }, [onDismiss]);
  return <div className={`toast toast--${tone}`} role="status"><span>{tone === "success" ? "✓" : "!"}</span><p>{message}</p><button onClick={onDismiss}>×</button></div>;
}
