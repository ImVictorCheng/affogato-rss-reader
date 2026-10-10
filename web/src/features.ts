// Dormant auto-tag preview workflow; restoration: docs/DORMANT_AUTO_TAG_PREVIEW.md.
export function autoTagPreviewEnabled(): boolean {
  return import.meta.env.VITE_AUTO_TAG_PREVIEW_ENABLED === "true";
}
