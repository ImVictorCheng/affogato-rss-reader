import type {
  ApiErrorBody,
  AppSettings,
  AuthStatus,
  Brief,
  BriefGenerationProgress,
  BriefConfiguration,
  BriefPeriod,
  BriefRule,
  BriefSchedule,
  CallLogList,
  Domain,
  Entry,
  EntryFilters,
  EntryState,
  Feed,
  Folder,
  Job,
  LLMConnection,
  LLMConnectionCreateInput,
  LLMConnectionTestInput,
  LLMConnectionTestResult,
  LLMConnectionUpdateInput,
  NetworkProxy,
  NetworkProxyInput,
  NetworkProxyTestInput,
  NetworkProxyTestResult,
  OnboardingProfile,
  Paginated,
  SourceSortSettings,
  Tag,
  ThemeConfig,
  TranslationSettingsInput,
  TranslationStatus,
  TranslationTestInput,
  TranslationTestResult,
  UpdateStatus,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "/api/v1").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function message(body: ApiErrorBody | null, fallback: string) {
  if (typeof body?.detail === "string") return body.detail;
  if (Array.isArray(body?.detail)) {
    return body.detail.map((item) => item.msg).filter(Boolean).join("; ") || fallback;
  }
  return body?.message || fallback;
}

class ApiClient {
  private csrf = "";

  setCsrfToken(value?: string | null) {
    if (value) this.csrf = value;
  }

  private async request<T>(
    path: string,
    init: RequestInit = {},
    responseType: "json" | "text" | "blob" = "json",
  ): Promise<T> {
    const method = (init.method || "GET").toUpperCase();
    const headers = new Headers(init.headers);
    if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    if (!["GET", "HEAD", "OPTIONS"].includes(method) && this.csrf) {
      headers.set("X-CSRF-Token", this.csrf);
    }
    headers.set("Accept", responseType === "json" ? "application/json" : "*/*");
    const response = await fetch(`${API_BASE}${path}`, { ...init, headers, credentials: "include" });
    this.setCsrfToken(response.headers.get("X-CSRF-Token"));
    if (!response.ok) {
      let body: ApiErrorBody | null = null;
      try {
        body = (await response.json()) as ApiErrorBody;
      } catch {
        // A reverse proxy may return plain text.
      }
      throw new ApiError(message(body, response.status === 401 ? "Session expired" : "Request failed"), response.status, body);
    }
    if (response.status === 204) return undefined as T;
    if (responseType === "blob") return (await response.blob()) as T;
    if (responseType === "text") return (await response.text()) as T;
    const payload = (await response.json()) as T & { csrf_token?: string };
    this.setCsrfToken(payload?.csrf_token);
    return payload;
  }

  async authStatus() {
    const value = await this.request<AuthStatus>("/auth/status");
    this.setCsrfToken(value.csrf_token);
    return value;
  }
  setup(password: string) {
    return this.request<AuthStatus>("/auth/setup", { method: "POST", body: JSON.stringify({ password }) });
  }
  activate(initialPassword: string, password: string) {
    return this.request<AuthStatus>("/auth/activate", {
      method: "POST",
      body: JSON.stringify({ initial_password: initialPassword, password }),
    });
  }
  login(password: string) {
    return this.request<AuthStatus>("/auth/login", { method: "POST", body: JSON.stringify({ password }) });
  }
  async logout() {
    await this.request<void>("/auth/logout", { method: "POST" });
    this.csrf = "";
  }
  async debugDeleteOwner() {
    await this.request<void>("/debug/owner", { method: "DELETE" });
    this.csrf = "";
  }
  settings() {
    return this.request<AppSettings>("/settings");
  }
  updateStatus() {
    return this.request<UpdateStatus>("/updates/status");
  }
  checkForUpdates() {
    return this.request<UpdateStatus>("/updates/check", { method: "POST" });
  }
  installUpdate() {
    return this.request<UpdateStatus>("/updates/install", { method: "POST" });
  }
  onboarding() {
    return this.request<OnboardingProfile>("/onboarding");
  }
  completeOnboarding(input: {
    selected_domains: string[];
    primary_domain: string;
    theme: ThemeConfig;
    ai_personalized: boolean;
    ai_provider?: string;
  }) {
    return this.request<OnboardingProfile>("/onboarding", {
      method: "PUT",
      body: JSON.stringify(input),
    });
  }
  generateAITheme(input: {
    selected_domains: string[];
    primary_domain: string;
    base_url: string;
    api_key: string;
    model: string;
    style_prompt?: string;
  }) {
    return this.request<{ theme: ThemeConfig }>("/onboarding/ai-theme", {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  async entries(filters: EntryFilters) {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (Array.isArray(value)) value.forEach((item) => params.append(key, String(item)));
      else if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
    });
    return this.request<Paginated<Entry>>(`/entries?${params}`);
  }
  entry(id: number) {
    return this.request<Entry>(`/entries/${id}`);
  }
  updateEntryState(id: number, state: Partial<EntryState>) {
    return this.request<Entry>(`/entries/${id}/state`, { method: "PATCH", body: JSON.stringify(state) });
  }
  bulkEntryState(entryIds: number[], state: Partial<EntryState>) {
    return this.request<void>("/entries/bulk-state", { method: "POST", body: JSON.stringify({ entry_ids: entryIds, state }) });
  }
  markAllRead(filters: EntryFilters) {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (Array.isArray(value)) value.forEach((item) => params.append(key, String(item)));
      else if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
    });
    return this.request<{ updated: number }>(`/entries/mark-all-read?${params}`, { method: "POST" });
  }
  setEntryDomains(id: number, domainIds: number[]) {
    return this.request<Entry>(`/entries/${id}/domains`, { method: "PUT", body: JSON.stringify({ domain_ids: domainIds }) });
  }

  async feeds() {
    const value = await this.request<{ items: Feed[] }>("/feeds");
    return value.items;
  }
  sourceSortSettings() {
    return this.request<SourceSortSettings>("/feeds/sort-settings");
  }
  updateSourceSortSettings(settings: SourceSortSettings) {
    return this.request<SourceSortSettings>("/feeds/sort-settings", {
      method: "PUT",
      body: JSON.stringify(settings),
    });
  }
  discoverFeed(url: string) {
    return this.request<{ items: Pick<Feed, "url" | "title" | "site_url">[] }>("/feeds/discover", { method: "POST", body: JSON.stringify({ url }) });
  }
  createFeed(input: { url: string; title?: string; folder?: string; poll_interval_minutes?: number; domain_ids?: number[] }) {
    return this.request<Feed>("/feeds", { method: "POST", body: JSON.stringify(input) });
  }
  updateFeed(id: number, patch: Partial<Feed> & { domain_ids?: number[] }) {
    return this.request<Feed>(`/feeds/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
  }
  associateFeedDomains(feedIds: number[], domainIds: number[]) {
    return this.request<{ feeds_updated: number; associations_added: number }>("/feeds/associate-domains", {
      method: "POST",
      body: JSON.stringify({ feed_ids: feedIds, domain_ids: domainIds }),
    });
  }
  deleteFeed(id: number) {
    return this.request<void>(`/feeds/${id}`, { method: "DELETE" });
  }
  refreshFeed(id: number) {
    return this.request<void>(`/feeds/${id}/refresh`, { method: "POST" });
  }
  downloadOpml() {
    return this.request<Blob>("/feeds/opml", {}, "blob");
  }
  importOpml(file: File) {
    const form = new FormData();
    form.append("file", file);
    return this.request<{ imported: number; skipped: number }>("/feeds/opml", { method: "POST", body: form });
  }

  async folders() {
    return (await this.request<{ items: Folder[] }>("/folders")).items;
  }
  createFolder(input: Pick<Folder, "name"> & Partial<Pick<Folder, "position">>) {
    return this.request<Folder>("/folders", { method: "POST", body: JSON.stringify(input) });
  }
  updateFolder(id: number, patch: Partial<Pick<Folder, "name" | "position" | "sort_mode" | "sort_direction">>) {
    return this.request<Folder>(`/folders/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
  }
  deleteFolder(id: number) {
    return this.request<void>(`/folders/${id}`, { method: "DELETE" });
  }
  reorderFeeds(folder: string | null, feedIds: number[]) {
    return this.request<void>("/feeds/reorder", { method: "PUT", body: JSON.stringify({ folder, feed_ids: feedIds }) });
  }

  async domains() {
    return (await this.request<{ items: Domain[] }>("/domains")).items;
  }
  createDomain(input: Pick<Domain, "name"> & Partial<Pick<Domain, "description" | "color" | "position">>) {
    return this.request<Domain>("/domains", { method: "POST", body: JSON.stringify(input) });
  }
  updateDomain(id: number, patch: Partial<Domain>) {
    return this.request<Domain>(`/domains/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
  }
  deleteDomain(id: number) {
    return this.request<void>(`/domains/${id}`, { method: "DELETE" });
  }

  async tags() {
    return (await this.request<{ items: Tag[] }>("/tags")).items;
  }
  createTag(name: string) {
    return this.request<Tag>("/tags", { method: "POST", body: JSON.stringify({ name }) });
  }
  addEntryTag(entryId: number, tagId: number) {
    return this.request<void>(`/entries/${entryId}/tags/${tagId}`, { method: "POST" });
  }
  removeEntryTag(entryId: number, tagId: number) {
    return this.request<void>(`/entries/${entryId}/tags/${tagId}`, { method: "DELETE" });
  }

  async jobs(limit = 20) {
    const [jobs, runs] = await Promise.all([
      this.request<{ items: Job[] }>(`/jobs?limit=${limit}`),
      this.request<{ items: Job[] }>(`/jobs/sync-runs?limit=${limit}`).catch(() => ({ items: [] })),
    ]);
    return [...jobs.items, ...runs.items].sort((a, b) => String(b.started_at || b.created_at || "").localeCompare(String(a.started_at || a.created_at || "")));
  }
  callLogs(filters: { category?: "llm" | "translation"; status?: "success" | "error"; limit?: number } = {}) {
    const params = new URLSearchParams();
    if (filters.category) params.set("category", filters.category);
    if (filters.status) params.set("status", filters.status);
    params.set("limit", String(filters.limit ?? 200));
    return this.request<CallLogList>(`/call-logs?${params.toString()}`);
  }
  translationStatus() {
    return this.request<TranslationStatus>("/translations/status");
  }
  setTranslation(settings: TranslationSettingsInput) {
    return this.request<TranslationStatus>("/translations/status", { method: "PATCH", body: JSON.stringify(settings) });
  }
  testTranslation(settings: TranslationTestInput) {
    return this.request<TranslationTestResult>("/translations/test", { method: "POST", body: JSON.stringify(settings) });
  }
  llmConnections() {
    return this.request<LLMConnection[]>("/llm/connections");
  }
  createLlmConnection(input: LLMConnectionCreateInput) {
    return this.request<LLMConnection>("/llm/connections", { method: "POST", body: JSON.stringify(input) });
  }
  updateLlmConnection(id: number, input: LLMConnectionUpdateInput) {
    return this.request<LLMConnection>(`/llm/connections/${id}`, { method: "PATCH", body: JSON.stringify(input) });
  }
  testLlmConnection(input: LLMConnectionTestInput) {
    return this.request<LLMConnectionTestResult>("/llm/connections/test", { method: "POST", body: JSON.stringify(input) });
  }
  deleteLlmConnection(id: number) {
    return this.request<void>(`/llm/connections/${id}`, { method: "DELETE" });
  }
  networkProxy() {
    return this.request<NetworkProxy>("/network-proxy");
  }
  setNetworkProxy(input: NetworkProxyInput) {
    return this.request<NetworkProxy>("/network-proxy", { method: "PATCH", body: JSON.stringify(input) });
  }
  testNetworkProxy(input: NetworkProxyTestInput) {
    return this.request<NetworkProxyTestResult>("/network-proxy/test", { method: "POST", body: JSON.stringify(input) });
  }
  retryTranslations(entryIds?: number[]) {
    return this.request<void>("/translations/retry", { method: "POST", body: JSON.stringify(entryIds?.length ? { entry_ids: entryIds } : {}) });
  }

  async briefs(period?: BriefPeriod) {
    const query = period ? `?period=${period}` : "";
    return (await this.request<{ items: Brief[] }>(`/briefs${query}`)).items;
  }
  brief(id: number) {
    return this.request<Brief>(`/briefs/${id}`);
  }
  briefConfiguration() {
    return this.request<BriefConfiguration>("/briefs/configuration");
  }
  setBriefConfiguration(llmConnectionId: number | null) {
    return this.request<BriefConfiguration>("/briefs/configuration", {
      method: "PATCH",
      body: JSON.stringify({ llm_connection_id: llmConnectionId }),
    });
  }
  briefRule() {
    return this.request<BriefRule>("/briefs/rule");
  }
  setBriefRule(content: string) {
    return this.request<BriefRule>("/briefs/rule", {
      method: "PATCH",
      body: JSON.stringify({ content }),
    });
  }
  resetBriefRule() {
    return this.request<BriefRule>("/briefs/rule", { method: "DELETE" });
  }
  createBrief(input: { period: BriefPeriod; idempotency_key: string; start_at?: string; end_at?: string; domain_ids?: number[]; feed_ids?: number[]; tag_ids?: number[]; domain_match?: "any" | "all" }) {
    return this.request<Brief>("/briefs", { method: "POST", body: JSON.stringify(input) });
  }
  briefGenerationProgress(idempotencyKey: string) {
    return this.request<BriefGenerationProgress>(
      `/briefs/generation-progress/${encodeURIComponent(idempotencyKey)}`,
    );
  }
  latestBriefGenerationProgress(period: BriefPeriod) {
    return this.request<BriefGenerationProgress | null>(
      `/briefs/generation-progress/latest?period=${encodeURIComponent(period)}`,
    );
  }
  retryBriefGeneration(idempotencyKey: string) {
    return this.request<Brief>(
      `/briefs/generation-progress/${encodeURIComponent(idempotencyKey)}/retry`,
      { method: "POST" },
    );
  }
  updateBrief(id: number, patch: { title?: string; notes?: string }) {
    return this.request<Brief>(`/briefs/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
  }
  deleteBrief(id: number) {
    return this.request<void>(`/briefs/${id}`, { method: "DELETE" });
  }
  exportBrief(id: number) {
    return this.request<Blob>(`/briefs/${id}/export`, {}, "blob");
  }
  async briefSchedules() {
    return (await this.request<{ items: BriefSchedule[] }>("/brief-schedules")).items;
  }
  createBriefSchedule(input: Omit<BriefSchedule, "id" | "last_run_at" | "created_at" | "updated_at">) {
    return this.request<BriefSchedule>("/brief-schedules", { method: "POST", body: JSON.stringify(input) });
  }
  updateBriefSchedule(id: number, patch: Partial<BriefSchedule>) {
    return this.request<BriefSchedule>(`/brief-schedules/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
  }
  runDueBriefs() {
    return this.request<{ items: Brief[] }>("/brief-schedules/run-due", { method: "POST" });
  }
}

export const api = new ApiClient();
