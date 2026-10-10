import { t } from "../i18n";
import type { Entry, Locale } from "../types";
import { entryDate, formatDateTime, formatFullDate, relativeTime } from "../utils";

export function EntryDate({ entry, locale, relative = false }: {
  entry: Entry;
  locale: Locale;
  relative?: boolean;
}) {
  const { value, source } = entryDate(entry);
  const label = source ? t(locale, source === "published" ? "datePublished" : source === "updated" ? "dateUpdated" : "dateCollected") : "";
  const date = relative && value ? relativeTime(value, locale) : formatFullDate(value, locale);
  return <time dateTime={value ?? undefined} title={value ? `${label} ${formatDateTime(value, locale)}` : undefined}>
    {source && source !== "published" ? `${label} ${date}` : date}
  </time>;
}
