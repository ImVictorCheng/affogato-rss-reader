from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AFFOGATO_RSS_READER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Affogato RSS Reader"
    version: str = "0.3.1"
    api_prefix: str = "/api/v1"
    data_dir: Path = Field(default=BACKEND_DIR / "data")
    database_url: str | None = None
    timezone: str = "UTC"
    auth_mode: Literal["owner", "none"] = "owner"
    debug: bool = False
    session_days: int = Field(default=30, ge=1, le=3650)
    cookie_secure: bool = False
    scheduler_enabled: bool = True
    sync_on_startup: bool = False
    translation_enabled: bool = False
    translation_target: str = "zh-CN"
    translation_provider: Literal[
        "google-gtx", "custom-llm", "deepl", "google-cloud"
    ] = "google-gtx"
    translation_fallback_mode: Literal["automatic", "manual"] = "automatic"
    gtx_endpoint: str = "https://translate.googleapis.com/translate_a/single"
    translation_llm_base_url: str = "https://api.openai.com/v1"
    translation_llm_api_key: str | None = None
    translation_llm_model: str = "gpt-4o-mini"
    deepl_endpoint: str = "https://api-free.deepl.com/v2/translate"
    deepl_api_key: str | None = None
    google_cloud_translation_endpoint: str = (
        "https://translation.googleapis.com/language/translate/v2"
    )
    google_cloud_translation_api_key: str | None = None
    secret_key_file: Path | None = None
    secret_key_previous_files: str = ""
    translation_chunk_chars: int = Field(default=3500, ge=256, le=10_000)
    translation_concurrency: int = Field(default=2, ge=1, le=16)
    translation_request_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    translation_max_attempts: int = Field(default=4, ge=1, le=10)
    translation_retry_base_seconds: float = Field(default=2.0, ge=0, le=60)
    request_timeout_seconds: float = Field(default=25.0, gt=0, le=300)
    llm_summary_timeout_seconds: float = Field(default=30.0, gt=0, le=900)
    brief_batch_concurrency: int = Field(default=2, ge=1, le=8)
    brief_llm_max_attempts: int = Field(default=4, ge=1, le=10)
    brief_llm_retry_base_seconds: float = Field(default=2.0, ge=0, le=60)
    static_dir: Path = Field(default=BACKEND_DIR / "static")
    call_log_file: Path | None = None
    call_log_max_bytes: int = Field(
        default=10 * 1024 * 1024,
        ge=1,
        le=1024 * 1024 * 1024,
    )
    call_log_backups: int = Field(default=5, ge=0, le=100)
    backup_keep_days: int = Field(default=30, ge=1, le=3650)
    backup_max_count: int = Field(default=14, ge=2, le=1000)
    backup_min_count: int = Field(default=2, ge=1, le=100)
    backup_max_total_bytes: int = Field(
        default=2 * 1024 * 1024 * 1024,
        ge=1024 * 1024,
        le=1024 * 1024 * 1024 * 1024,
    )
    sqlite_wal_autocheckpoint_pages: int = Field(default=1000, ge=1, le=1_000_000)
    sqlite_journal_size_limit_bytes: int = Field(
        default=64 * 1024 * 1024,
        ge=0,
        le=16 * 1024 * 1024 * 1024,
    )
    default_poll_minutes: int = 45
    update_check_enabled: bool = True
    update_check_hour: int = Field(default=5, ge=0, le=23)
    update_github_repository: str = "ImVictorCheng/affogato-rss-reader"
    update_image_repository: str = "ghcr.io/imvictorcheng/affogato-rss-reader"
    update_control_dir: Path | None = None
    update_workspace_dir: Path = Field(default=Path("/workspace"))
    update_runner_poll_seconds: float = Field(default=2.0, ge=0.5, le=60)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator("default_poll_minutes")
    @classmethod
    def valid_poll(cls, value: int) -> int:
        if not 15 <= value <= 1440:
            raise ValueError("default_poll_minutes must be between 15 and 1440")
        return value

    @model_validator(mode="after")
    def valid_backup_counts(self) -> "Settings":
        if self.backup_min_count > self.backup_max_count:
            raise ValueError("backup_min_count must not exceed backup_max_count")
        return self

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'affogato-rss-reader.db').as_posix()}"

    @property
    def effective_secret_key_file(self) -> Path:
        return self.secret_key_file or self.data_dir / ".secrets" / "master.key"

    @property
    def effective_call_log_file(self) -> Path:
        return self.call_log_file or self.data_dir / "logs" / "llm-translation.jsonl"

    @property
    def effective_update_control_dir(self) -> Path:
        return self.update_control_dir or self.data_dir / "update-control"


@lru_cache
def get_settings() -> Settings:
    return Settings()
