export type ReaderView = "all" | "unread" | "starred" | "later" | "archived";
export type LanguageMode = "original" | "translated" | "bilingual";
export type MobilePane = "navigation" | "list" | "detail";
export type Locale = "zh-CN" | "en";
export type DomainMatch = "any" | "all";
export type BriefPeriod = "daily" | "weekly" | "monthly" | "yearly";
export type FolderSortMode = "alpha" | "updated" | "manual";
export type SortDirection = "asc" | "desc";
export type TranslationProvider = "google-gtx" | "custom-llm" | "deepl" | "google-cloud";
export type TranslationFallbackMode = "automatic" | "manual";
export type ProxyMode = "custom" | "system" | "direct";
export type TranslationProxyService = "google-gtx" | "deepl" | "google-cloud";

export interface SourceSortSettings {
  sort_mode: FolderSortMode;
  sort_direction: SortDirection;
}

export interface Owner {
  name?: string;
}

export interface AuthStatus {
  setup_required: boolean;
  activation_required?: boolean;
  authenticated: boolean;
  onboarding_required?: boolean;
  mode: "owner" | "none";
  warning?: string | null;
  csrf_token?: string;
  owner?: Owner | null;
  theme?: ThemeConfig | null;
}

export type ThemeDensity = "compact" | "balanced" | "relaxed";
export type ThemeTypography = "technical" | "editorial" | "balanced";
export type ThemeMotif = "orbit" | "network" | "market" | "proof" | "silicon" | "circuit" | "grid";

export interface SiteIdentity {
  name: string;
  source: "builtin" | "custom" | "default";
  logo_kind: "generated" | "upload" | "default";
  primary_template?: string | null;
  secondary_template?: string | null;
  logo_data_url?: string | null;
}

export interface ThemeConfig {
  id: string;
  label: string;
  accent: string;
  secondary: string;
  nav: string;
  paper: string;
  surface: string;
  ink: string;
  density: ThemeDensity;
  typography: ThemeTypography;
  motif: ThemeMotif;
  source: "builtin" | "ai";
  identity?: SiteIdentity | null;
}

export interface OnboardingProfile {
  completed: boolean;
  selected_domains: string[];
  primary_domain?: string | null;
  theme?: ThemeConfig | null;
  ai_personalized: boolean;
  ai_provider?: string | null;
}

export interface EntryState {
  read: boolean;
  starred: boolean;
  later: boolean;
  archived: boolean;
}

export interface Tag {
  id: number;
  name: string;
  color?: string | null;
  entry_count?: number;
}

export interface Domain {
  id: number;
  name: string;
  description: string;
  color?: string | null;
  position: number;
  feed_count: number;
  entry_count: number;
}

export interface Folder {
  id: number;
  name: string;
  position: number;
  sort_mode: FolderSortMode;
  sort_direction: SortDirection;
  feed_count: number;
}

export interface Feed {
  id: number;
  title: string;
  url: string;
  site_url?: string | null;
  folder?: string | null;
  position: number;
  enabled: boolean;
  poll_interval_minutes: number;
  status: "healthy" | "syncing" | "error" | "paused" | string;
  unread_count: number;
  entry_count: number;
  error_count: number;
  last_checked_at?: string | null;
  last_fetched_at?: string | null;
  next_fetch_at?: string | null;
  last_error?: string | null;
  domains: Domain[];
}

export interface Entry {
  id: number;
  title: string;
  translated_title?: string | null;
  summary?: string | null;
  translated_summary?: string | null;
  content?: string | null;
  url: string;
  canonical_url?: string | null;
  authors: string[] | string;
  categories?: string[];
  published_at?: string | null;
  updated_at?: string | null;
  arxiv_id?: string | null;
  arxiv_version?: number | null;
  doi?: string | null;
  announce_type?: string | null;
  feed_titles?: string[];
  feed_ids?: number[];
  state: EntryState;
  tags: Tag[];
  domains: Domain[];
  translation_status?: string | null;
  translation_error?: string | null;
  translation_language?: string | null;
}

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  per_page: number;
}

export interface EntryFilters {
  page?: number;
  per_page?: number;
  view?: ReaderView;
  feed_id?: number | null;
  folder?: string | null;
  tag_id?: number | null;
  q?: string;
  domain_ids?: number[];
  domain_match?: DomainMatch;
}

export interface Job {
  id: number | string;
  kind?: string;
  status: string;
  started_at?: string | null;
  finished_at?: string | null;
  created_at?: string | null;
  message?: string | null;
  feed_title?: string | null;
  inserted_count?: number;
  updated_count?: number;
  error_count?: number;
}

export interface CallLog {
  id: string;
  timestamp: string;
  category: "llm" | "translation";
  operation: string;
  feature?: string | null;
  provider?: string | null;
  model?: string | null;
  connection_id?: number | null;
  connection_name?: string | null;
  target_language?: string | null;
  status: "success" | "error";
  duration_ms: number;
  input_chars: number;
  output_chars: number;
  cached: boolean;
  error?: string | null;
}

export interface CallLogList {
  items: CallLog[];
  file_path: string;
  host_path_hint: string;
}

export interface TranslationStatus {
  enabled: boolean;
  provider: TranslationProvider;
  fallback_provider: "google-gtx";
  fallback_mode: TranslationFallbackMode;
  available_providers: TranslationProvider[];
  provider_healthy: boolean;
  healthy: boolean;
  pending_count: number;
  running_count: number;
  failed_count: number;
  completed_count: number;
  last_success_at?: string | null;
  last_error?: string | null;
  target_language: string;
  llm_connection_id?: number | null;
  llm_connection_name?: string | null;
  llm_connections: LLMConnection[];
  llm_base_url: string;
  llm_model: string;
  llm_api_key_configured: boolean;
  deepl_endpoint: string;
  deepl_api_key_configured: boolean;
  google_cloud_api_key_configured: boolean;
}

export interface TranslationSettingsInput {
  enabled: boolean;
  target_language: string;
  provider: TranslationProvider;
  fallback_mode: TranslationFallbackMode;
  llm_connection_id?: number;
  deepl_endpoint?: string;
  deepl_api_key?: string;
  clear_deepl_api_key?: boolean;
  google_cloud_api_key?: string;
  clear_google_cloud_api_key?: boolean;
}

export interface TranslationTestInput {
  provider: TranslationProvider;
  target_language: string;
  sample_text?: string;
  llm_connection_id?: number;
  deepl_endpoint?: string;
  deepl_api_key?: string;
  google_cloud_api_key?: string;
}

export interface TranslationTestResult {
  provider: TranslationProvider;
  translated_text: string;
  elapsed_ms: number;
}

export interface LLMConnection {
  id: number;
  name: string;
  base_url: string;
  model: string;
  api_key_configured: boolean;
  api_key_hint?: string | null;
  used_by: string[];
}

export interface LLMConnectionCreateInput {
  name: string;
  base_url: string;
  model: string;
  api_key: string;
}

export interface LLMConnectionUpdateInput {
  name?: string;
  base_url?: string;
  model?: string;
  api_key?: string;
  clear_api_key?: boolean;
}

export interface LLMConnectionTestInput {
  connection_id?: number;
  base_url?: string;
  model?: string;
  api_key?: string;
}

export interface LLMConnectionTestResult {
  model: string;
  response_text: string;
  elapsed_ms: number;
}

export interface NetworkProxy {
  enabled: boolean;
  url: string;
  username?: string | null;
  password_configured: boolean;
  password_hint?: string | null;
  global_mode: ProxyMode;
  running_in_container: boolean;
  feed_modes: Record<number, ProxyMode>;
  llm_connection_modes: Record<number, ProxyMode>;
  translation_service_modes: Record<TranslationProxyService, ProxyMode>;
}

export interface NetworkProxyInput {
  enabled: boolean;
  url: string;
  username?: string;
  password?: string;
  clear_password?: boolean;
  global_mode: ProxyMode;
  feed_modes: Record<number, ProxyMode>;
  llm_connection_modes: Record<number, ProxyMode>;
  translation_service_modes: Record<TranslationProxyService, ProxyMode>;
}

export interface NetworkProxyTestInput {
  url: string;
  username?: string;
  password?: string;
  use_saved_password?: boolean;
}

export interface NetworkProxyTargetTestResult {
  target_url: string;
  ok: boolean;
  status_code?: number | null;
  elapsed_ms: number;
  final_url?: string | null;
  error?: string | null;
}

export interface NetworkProxyTestResult {
  results: NetworkProxyTargetTestResult[];
}

export interface Brief {
  id: number;
  schedule_id?: number | null;
  period: BriefPeriod;
  period_start: string;
  period_end: string;
  start_at: string;
  end_at: string;
  title: string;
  notes: string;
  stats: Record<string, number | string>;
  filters: Record<string, unknown>;
  item_count: number;
  created_at: string;
  updated_at: string;
  status: string;
  markdown?: string;
}

export interface BriefGenerationProgress {
  idempotency_key: string;
  status: "running" | "completed" | "failed";
  stage:
    | "preparing"
    | "summarizing_batches"
    | "consolidating"
    | "finalizing"
    | string;
  completed: number;
  total: number;
  brief_id?: number | null;
  message?: string | null;
  can_retry?: boolean;
  attempt?: number;
}

export interface BriefConfiguration {
  llm_connection_id?: number | null;
  llm_connection_name?: string | null;
  model?: string | null;
  configured: boolean;
}

export interface BriefRule {
  content: string;
  is_custom: boolean;
}

export interface BriefSchedule {
  id: number;
  name: string;
  period: BriefPeriod;
  timezone: string;
  cutoff_time: string;
  weekday?: number | null;
  month_day?: number | null;
  year_month?: number | null;
  domain_ids: number[];
  feed_ids: number[];
  tag_ids: number[];
  domain_match: DomainMatch;
  enabled: boolean;
  last_run_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AppSettings {
  app_name: string;
  version: string;
  auth_mode: "owner" | "none";
  debug: boolean;
  timezone: string;
  translation_enabled: boolean;
  translation_target: string;
  available_locales: string[];
}

export interface UpdateStatus {
  current_version: string;
  latest_version: string;
  status: string;
  release_url?: string | null;
  release_notes?: string | null;
  published_at?: string | null;
  last_checked_at?: string | null;
  downloaded_at?: string | null;
  install_requested_at?: string | null;
  installed_at?: string | null;
  downloaded: boolean;
  downloaded_bytes?: number | null;
  install_supported: boolean;
  automatic_checks_enabled: boolean;
  check_hour: number;
  error?: string | null;
}

export interface ApiErrorBody {
  detail?: string | { msg?: string }[];
  message?: string;
}
