from __future__ import annotations

import base64
import binascii
import re
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class OwnerOut(APIModel):
    name: str


class PasswordBody(BaseModel):
    password: str = Field(min_length=8, max_length=512)


class OwnerActivationBody(BaseModel):
    initial_password: str = Field(min_length=8, max_length=512)
    password: str = Field(min_length=8, max_length=512)


class AuthStatus(APIModel):
    setup_required: bool
    activation_required: bool = False
    authenticated: bool
    onboarding_required: bool = False
    mode: Literal["owner", "none"] = "owner"
    warning: str | None = None
    csrf_token: str | None = None
    owner: OwnerOut | None = None
    theme: dict[str, Any] | None = None


class SiteIdentity(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source: Literal["builtin", "custom", "default"] = "default"
    logo_kind: Literal["generated", "upload", "default"] = "default"
    primary_template: str | None = Field(
        default=None, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$"
    )
    secondary_template: str | None = Field(
        default=None, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$"
    )
    logo_data_url: str | None = Field(default=None, max_length=400_000)

    @model_validator(mode="after")
    def validate_logo(self) -> Self:
        if self.logo_kind == "generated" and not self.primary_template:
            raise ValueError("generated logos require primary_template")
        if self.logo_kind == "upload":
            match = self.logo_data_url and re.fullmatch(
                r"data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}",
                self.logo_data_url,
            )
            if not match:
                raise ValueError("uploaded logos must be PNG, JPEG, or WebP data URLs")
            payload = self.logo_data_url.split(",", 1)[1]
            try:
                decoded = base64.b64decode(payload, validate=True)
            except (ValueError, binascii.Error) as error:
                raise ValueError("uploaded logos must contain valid base64 data") from error
            if len(decoded) > 256 * 1024:
                raise ValueError("uploaded logos must be 256 KB or smaller")
        elif self.logo_data_url is not None:
            raise ValueError("logo_data_url is only allowed for uploaded logos")
        return self


class ThemeConfig(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    label: str = Field(min_length=1, max_length=120)
    accent: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    secondary: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    nav: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    paper: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    surface: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    ink: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    density: Literal["compact", "balanced", "relaxed"] = "balanced"
    typography: Literal["technical", "editorial", "balanced"] = "balanced"
    motif: Literal["orbit", "network", "market", "proof", "silicon", "circuit", "grid"] = "grid"
    source: Literal["builtin", "ai"] = "builtin"
    identity: SiteIdentity | None = None


class OnboardingProfile(APIModel):
    completed: bool
    selected_domains: list[str]
    primary_domain: str | None
    theme: ThemeConfig | None
    ai_personalized: bool
    ai_provider: str | None


class OnboardingComplete(BaseModel):
    selected_domains: list[str] = Field(min_length=1, max_length=12)
    primary_domain: str = Field(min_length=1, max_length=120)
    theme: ThemeConfig
    ai_personalized: bool = False
    ai_provider: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def normalize_domains(self) -> Self:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in self.selected_domains:
            name = value.strip()
            if not name or len(name) > 120:
                raise ValueError("domain names must contain 1 to 120 characters")
            folded = name.casefold()
            if folded not in seen:
                seen.add(folded)
                cleaned.append(name)
        primary = self.primary_domain.strip()
        if primary.casefold() not in seen:
            raise ValueError("primary_domain must be one of selected_domains")
        self.selected_domains = cleaned
        self.primary_domain = next(item for item in cleaned if item.casefold() == primary.casefold())
        return self


class AIThemeRequest(BaseModel):
    selected_domains: list[str] = Field(min_length=1, max_length=12)
    primary_domain: str = Field(min_length=1, max_length=120)
    base_url: HttpUrl
    api_key: str = Field(min_length=1, max_length=4096)
    model: str = Field(min_length=1, max_length=200)
    style_prompt: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_primary(self) -> Self:
        domains = [item.strip() for item in self.selected_domains if item.strip()]
        if not domains or self.primary_domain.strip().casefold() not in {
            item.casefold() for item in domains
        }:
            raise ValueError("primary_domain must be one of selected_domains")
        self.selected_domains = domains
        self.primary_domain = next(
            item for item in domains if item.casefold() == self.primary_domain.strip().casefold()
        )
        return self


class AIThemeResponse(BaseModel):
    theme: ThemeConfig


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    position: int = Field(default=0, ge=0, le=100000)
    sort_mode: Literal["alpha", "updated", "manual"] = "alpha"
    sort_direction: Literal["asc", "desc"] = "asc"

    @model_validator(mode="after")
    def normalize_name(self) -> Self:
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("folder name cannot be blank")
        return self


class FolderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    position: int | None = Field(default=None, ge=0, le=100000)
    sort_mode: Literal["alpha", "updated", "manual"] | None = None
    sort_direction: Literal["asc", "desc"] | None = None

    @model_validator(mode="after")
    def normalize_name(self) -> Self:
        if "name" in self.model_fields_set:
            self.name = self.name.strip() if self.name is not None else None
            if not self.name:
                raise ValueError("folder name cannot be blank")
        return self


class FeedCreate(BaseModel):
    url: HttpUrl
    title: str | None = Field(default=None, max_length=500)
    site_url: HttpUrl | None = None
    folder: str | None = Field(default=None, max_length=160)
    enabled: bool = True
    poll_interval_minutes: int = Field(default=45, ge=15, le=1440)
    domain_ids: list[int] = Field(default_factory=list, max_length=100)


class FeedUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    url: HttpUrl | None = None
    site_url: HttpUrl | None = None
    folder: str | None = Field(default=None, max_length=160)
    enabled: bool | None = None
    poll_interval_minutes: int | None = Field(default=None, ge=15, le=1440)
    domain_ids: list[int] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def required_values_cannot_be_cleared(self) -> Self:
        if "title" in self.model_fields_set and (self.title is None or not self.title.strip()):
            raise ValueError("title cannot be null or blank")
        if "url" in self.model_fields_set and self.url is None:
            raise ValueError("url cannot be null")
        return self


class FeedReorder(BaseModel):
    folder: str | None = Field(default=None, max_length=160)
    feed_ids: list[int] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def normalize_folder(self) -> Self:
        self.folder = self.folder.strip() or None if self.folder else None
        if len(set(self.feed_ids)) != len(self.feed_ids):
            raise ValueError("feed_ids must not contain duplicates")
        return self


class SourceSortSettings(BaseModel):
    sort_mode: Literal["alpha", "updated", "manual"] = "alpha"
    sort_direction: Literal["asc", "desc"] = "asc"


class FeedDomainAssociation(BaseModel):
    feed_ids: list[int] = Field(min_length=1, max_length=1000)
    domain_ids: list[int] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def ids_must_be_unique(self) -> Self:
        if len(set(self.feed_ids)) != len(self.feed_ids):
            raise ValueError("feed_ids must not contain duplicates")
        if len(set(self.domain_ids)) != len(self.domain_ids):
            raise ValueError("domain_ids must not contain duplicates")
        return self


class DiscoverBody(BaseModel):
    url: HttpUrl


class StatePatch(BaseModel):
    read: bool | None = None
    starred: bool | None = None
    later: bool | None = None
    archived: bool | None = None


class BulkState(BaseModel):
    entry_ids: list[int] = Field(min_length=1, max_length=1000)
    state: StatePatch


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class TranslationToggle(BaseModel):
    enabled: bool
    target_language: str | None = Field(default=None, min_length=2, max_length=35)
    provider: Literal["google-gtx", "custom-llm", "deepl", "google-cloud"] | None = None
    fallback_mode: Literal["automatic", "manual"] | None = None
    llm_connection_id: int | None = Field(default=None, ge=1)
    deepl_endpoint: HttpUrl | None = None
    deepl_api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    clear_deepl_api_key: bool = False
    google_cloud_api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    clear_google_cloud_api_key: bool = False


class TranslationRetry(BaseModel):
    entry_ids: list[int] | None = Field(default=None, max_length=1000)


class TranslationTest(BaseModel):
    provider: Literal["google-gtx", "custom-llm", "deepl", "google-cloud"]
    target_language: str = Field(default="zh-CN", min_length=2, max_length=35)
    sample_text: str = Field(
        default="Hello! This is a translation connectivity test.",
        min_length=1,
        max_length=1000,
    )
    llm_connection_id: int | None = Field(default=None, ge=1)
    deepl_endpoint: HttpUrl | None = None
    deepl_api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    google_cloud_api_key: str | None = Field(default=None, min_length=1, max_length=4096)


class DomainCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    position: int = Field(default=0, ge=0, le=100000)

    @model_validator(mode="after")
    def normalize_name(self) -> Self:
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("domain name cannot be blank")
        return self


class DomainUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    position: int | None = Field(default=None, ge=0, le=100000)

    @model_validator(mode="after")
    def normalize_name(self) -> Self:
        if "name" in self.model_fields_set:
            self.name = self.name.strip() if self.name is not None else None
            if not self.name:
                raise ValueError("domain name cannot be blank")
        return self


class EntryDomainsPatch(BaseModel):
    domain_ids: list[int] = Field(default_factory=list, max_length=100)


class BriefCreate(BaseModel):
    period: Literal["daily", "weekly", "monthly", "yearly"] = "daily"
    at: datetime | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    title: str | None = Field(default=None, max_length=1000)
    domain_ids: list[int] = Field(default_factory=list, max_length=100)
    feed_ids: list[int] = Field(default_factory=list, max_length=500)
    tag_ids: list[int] = Field(default_factory=list, max_length=100)
    domain_match: Literal["any", "all"] = "any"
    idempotency_key: str = Field(min_length=8, max_length=120)

    @model_validator(mode="after")
    def validate_time_range(self) -> Self:
        if (self.start_at is None) != (self.end_at is None):
            raise ValueError("start_at and end_at must be provided together")
        if (
            self.start_at is not None
            and self.end_at is not None
            and self.start_at >= self.end_at
        ):
            raise ValueError("start_at must be before end_at")
        return self


class BriefUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=1000)
    notes: str | None = None


class BriefConfigurationUpdate(BaseModel):
    llm_connection_id: int | None = Field(default=None, ge=1)


class BriefRuleUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)


class BriefScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    period: Literal["daily", "weekly", "monthly", "yearly"]
    timezone: str = Field(default="UTC", max_length=80)
    cutoff_time: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    weekday: int | None = Field(default=None, ge=0, le=6)
    month_day: int | None = Field(default=None, ge=1, le=31)
    year_month: int | None = Field(default=None, ge=1, le=12)
    domain_ids: list[int] = Field(default_factory=list, max_length=100)
    feed_ids: list[int] = Field(default_factory=list, max_length=500)
    tag_ids: list[int] = Field(default_factory=list, max_length=100)
    domain_match: Literal["any", "all"] = "any"
    enabled: bool = True


class BriefScheduleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    timezone: str | None = Field(default=None, max_length=80)
    cutoff_time: str | None = Field(
        default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"
    )
    weekday: int | None = Field(default=None, ge=0, le=6)
    month_day: int | None = Field(default=None, ge=1, le=31)
    year_month: int | None = Field(default=None, ge=1, le=12)
    domain_ids: list[int] | None = Field(default=None, max_length=100)
    feed_ids: list[int] | None = Field(default=None, max_length=500)
    tag_ids: list[int] | None = Field(default=None, max_length=100)
    domain_match: Literal["any", "all"] | None = None
    enabled: bool | None = None


class FeedOut(APIModel):
    id: int
    source_key: str | None
    title: str
    url: str
    site_url: str | None
    folder: str | None
    position: int
    enabled: bool
    poll_interval_minutes: int
    last_checked_at: datetime | None
    last_fetched_at: datetime | None
    next_fetch_at: datetime | None
    error_count: int
    last_error: str | None
    status: str
    unread_count: int
    entry_count: int
    domains: list["DomainOut"] = Field(default_factory=list)


class FeedListOut(APIModel):
    items: list[FeedOut]
    total: int


class FolderOut(APIModel):
    id: int
    name: str
    position: int
    sort_mode: Literal["alpha", "updated", "manual"]
    sort_direction: Literal["asc", "desc"]
    feed_count: int


class FolderListOut(APIModel):
    items: list[FolderOut]


class DiscoveredFeedOut(APIModel):
    url: str
    title: str
    site_url: str | None


class FeedDiscoveryOut(APIModel):
    items: list[DiscoveredFeedOut]


class OpmlImportOut(APIModel):
    imported: int
    skipped: int


class EntryStateOut(APIModel):
    read: bool = False
    starred: bool = False
    later: bool = False
    archived: bool = False


class TagOut(APIModel):
    id: int
    name: str
    color: str | None


class TagWithCountOut(TagOut):
    entry_count: int


class TagListOut(APIModel):
    items: list[TagWithCountOut]


class EntryOut(APIModel):
    id: int
    work_id: int
    title: str
    translated_title: str | None
    summary: str
    translated_summary: str | None
    content: str | None
    url: str
    canonical_url: str | None
    authors: list[str]
    categories: list[str]
    arxiv_id: str | None
    arxiv_version: int | None
    doi: str | None
    announce_type: str | None
    published_at: datetime | None
    updated_at: datetime
    feed_titles: list[str]
    feed_ids: list[int]
    state: EntryStateOut
    tags: list[TagOut]
    translation_status: str | None
    translation_error: str | None
    translation_language: str | None = None
    domains: list["DomainOut"] = Field(default_factory=list)


class EntriesPage(BaseModel):
    items: list[EntryOut]
    total: int
    page: int
    per_page: int


class TranslationStatusOut(APIModel):
    enabled: bool
    provider: str
    fallback_provider: str
    fallback_mode: Literal["automatic", "manual"]
    available_providers: list[str]
    healthy: bool
    provider_healthy: bool
    pending_count: int
    running_count: int
    failed_count: int
    completed_count: int
    last_success_at: datetime | None
    last_error: str | None
    target_language: str
    llm_connection_id: int | None
    llm_connection_name: str | None
    llm_connections: list["LLMConnectionOut"]
    llm_base_url: str
    llm_model: str
    llm_api_key_configured: bool
    deepl_endpoint: str
    deepl_api_key_configured: bool
    google_cloud_api_key_configured: bool


class TranslationTestOut(APIModel):
    provider: str
    translated_text: str
    elapsed_ms: int


class LLMConnectionOut(APIModel):
    id: int
    name: str
    base_url: str
    model: str
    api_key_configured: bool
    api_key_hint: str | None
    used_by: list[str]


class LLMConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: HttpUrl
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(min_length=1, max_length=4096)


class LLMConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: HttpUrl | None = None
    model: str | None = Field(default=None, min_length=1, max_length=200)
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    clear_api_key: bool = False


class LLMConnectionTest(BaseModel):
    connection_id: int | None = Field(default=None, ge=1)
    base_url: HttpUrl | None = None
    model: str | None = Field(default=None, min_length=1, max_length=200)
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)


class LLMConnectionTestOut(APIModel):
    model: str
    response_text: str
    elapsed_ms: int


class NetworkProxyOut(APIModel):
    enabled: bool
    url: str
    username: str | None
    password_configured: bool
    password_hint: str | None
    global_mode: Literal["custom", "system", "direct"]
    running_in_container: bool
    feed_modes: dict[int, Literal["custom", "system", "direct"]]
    llm_connection_modes: dict[int, Literal["custom", "system", "direct"]]
    translation_service_modes: dict[
        str, Literal["custom", "system", "direct"]
    ]


class NetworkProxyUpdate(BaseModel):
    enabled: bool
    url: str = Field(default="", max_length=1000)
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=1, max_length=4096)
    clear_password: bool = False
    global_mode: Literal["custom", "system", "direct"] = "direct"
    feed_modes: dict[int, Literal["custom", "system", "direct"]] = Field(
        default_factory=dict,
        max_length=10000,
    )
    llm_connection_modes: dict[
        int, Literal["custom", "system", "direct"]
    ] = Field(default_factory=dict, max_length=1000)
    translation_service_modes: dict[
        str, Literal["custom", "system", "direct"]
    ] = Field(default_factory=dict, max_length=20)

    @model_validator(mode="after")
    def normalize_proxy_fields(self) -> Self:
        self.url = self.url.strip()
        self.username = self.username.strip() if self.username else None
        if self.enabled and not self.url:
            raise ValueError("proxy URL is required when enabled")
        return self


class NetworkProxyTest(BaseModel):
    url: str = Field(min_length=1, max_length=1000)
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=1, max_length=4096)
    use_saved_password: bool = True


class NetworkProxyTargetTestOut(APIModel):
    target_url: str
    ok: bool
    status_code: int | None
    elapsed_ms: int
    final_url: str | None
    error: str | None


class NetworkProxyTestOut(APIModel):
    results: list[NetworkProxyTargetTestOut]


class JobOut(APIModel):
    id: int
    kind: str
    status: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    message: str | None
    payload: dict[str, Any]
    result: dict[str, Any]


class JobListOut(APIModel):
    items: list[JobOut]


class CallLogOut(APIModel):
    id: str
    timestamp: datetime
    category: Literal["llm", "translation"]
    operation: str
    feature: str | None
    provider: str | None
    model: str | None
    connection_id: int | None
    connection_name: str | None
    target_language: str | None
    status: Literal["success", "error"]
    duration_ms: int
    input_chars: int
    output_chars: int
    cached: bool
    error: str | None


class CallLogListOut(APIModel):
    items: list[CallLogOut]
    file_path: str
    host_path_hint: str


class SyncRunOut(APIModel):
    id: str
    kind: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    feed_title: str | None
    inserted_count: int
    updated_count: int
    error_count: int
    message: str | None
    http_status: int | None


class SyncRunListOut(APIModel):
    items: list[SyncRunOut]


class DomainOut(APIModel):
    id: int
    name: str
    description: str
    color: str | None
    position: int
    feed_count: int = 0
    entry_count: int = 0


class DomainListOut(APIModel):
    items: list[DomainOut]


class BriefOut(APIModel):
    id: int
    schedule_id: int | None
    period: str
    period_start: datetime
    period_end: datetime
    start_at: datetime
    end_at: datetime
    title: str
    notes: str
    stats: dict
    filters: dict
    item_count: int
    created_at: datetime
    updated_at: datetime
    status: str


class BriefGenerationProgressOut(APIModel):
    idempotency_key: str
    status: Literal["running", "completed", "failed"]
    stage: str
    completed: int
    total: int
    brief_id: int | None = None
    message: str | None = None
    can_retry: bool = False
    attempt: int = 1


class BriefDetailOut(BriefOut):
    markdown: str


class BriefListOut(APIModel):
    items: list[BriefOut]


class BriefConfigurationOut(APIModel):
    llm_connection_id: int | None
    llm_connection_name: str | None
    model: str | None
    configured: bool


class BriefRuleOut(APIModel):
    content: str
    is_custom: bool


class BriefScheduleOut(APIModel):
    id: int
    name: str
    period: str
    timezone: str
    cutoff_time: str
    weekday: int | None
    month_day: int | None
    year_month: int | None
    domain_ids: list[int]
    feed_ids: list[int]
    tag_ids: list[int]
    domain_match: str
    enabled: bool
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BriefScheduleListOut(APIModel):
    items: list[BriefScheduleOut]


class AppSettingsOut(APIModel):
    app_name: str
    version: str
    auth_mode: Literal["owner", "none"]
    debug: bool
    timezone: str
    translation_enabled: bool
    translation_target: str
    available_locales: list[str]


class UpdateStatusOut(APIModel):
    current_version: str
    latest_version: str
    status: str
    release_url: str | None
    release_notes: str | None
    published_at: datetime | None
    last_checked_at: datetime | None
    downloaded_at: datetime | None
    install_requested_at: datetime | None
    installed_at: datetime | None
    downloaded: bool
    downloaded_bytes: int | None
    install_supported: bool
    automatic_checks_enabled: bool
    check_hour: int
    error: str | None
