import type { Entry, LanguageMode, Locale } from "./types";

export function formatDateTime(value?: string | null, locale: Locale = "en"): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

export function formatFullDate(value?: string | null, locale: Locale = "en"): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(locale, { dateStyle: "full" }).format(date);
}

export function relativeTime(value?: string | null, locale: Locale = "en"): string {
  if (!value) return "";
  const seconds = Math.round((new Date(value).getTime() - Date.now()) / 1000);
  if (!Number.isFinite(seconds)) return "";
  const formatter = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  const units: [Intl.RelativeTimeFormatUnit, number][] = [["year", 31_536_000], ["month", 2_592_000], ["week", 604_800], ["day", 86_400], ["hour", 3_600], ["minute", 60]];
  const [unit, size] = units.find(([, size]) => Math.abs(seconds) >= size) ?? ["minute", 60];
  return formatter.format(Math.round(seconds / size), unit);
}

export function authors(entry: Entry): string[] {
  return Array.isArray(entry.authors) ? entry.authors : String(entry.authors || "").split(/,\s*|\s+and\s+/).filter(Boolean);
}

export function displayTitle(entry: Entry, mode: LanguageMode): string {
  return mode !== "original" && entry.translated_title ? entry.translated_title : entry.title;
}

export function formatArxivIdentifier(id?: string | null, version?: number | null): string {
  if (!id) return "";
  const normalized = id.replace(/^arxiv:\s*/i, "");
  return `arXiv:${/v\d+$/i.test(normalized) || !version ? normalized : `${normalized}v${version}`}`;
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "Unknown error";
}

export function uniqueRequestKey(prefix = "request"): string {
  const cryptoObject = globalThis.crypto;
  if (typeof cryptoObject?.randomUUID === "function") {
    return `${prefix}-${cryptoObject.randomUUID()}`;
  }
  if (typeof cryptoObject?.getRandomValues === "function") {
    const bytes = new Uint8Array(16);
    cryptoObject.getRandomValues(bytes);
    return `${prefix}-${Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("")}`;
  }
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}
