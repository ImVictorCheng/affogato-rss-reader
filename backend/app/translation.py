from __future__ import annotations

import hashlib
from datetime import timedelta
from html import unescape
from threading import BoundedSemaphore, Lock
from time import perf_counter, sleep
from typing import Protocol

import httpx
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .call_logging import safe_write_call_log
from .llm import (
    TRANSLATION_FEATURE,
    bind_llm_connection,
    decrypt_llm_api_key,
    encrypted_setting_value,
    get_feature_connection,
    get_llm_connection,
    list_llm_connections,
    set_encrypted_setting,
    stream_completion_text,
    unbind_llm_connection,
)
from .models import AppSetting, Entry, Translation, TranslationCache, utcnow
from .network_proxy import (
    HttpRoute,
    http_route_for_llm_connection,
    http_route_for_translation_service,
)
from .secrets import SecretKeyError

_semaphore_lock = Lock()
_provider_semaphores: dict[int, BoundedSemaphore] = {}

TRANSLATION_RECORD_PROVIDER = "translation-chain"
LEGACY_TRANSLATION_PROVIDER = "google-gtx"
AVAILABLE_TRANSLATION_PROVIDERS = (
    "custom-llm",
    "deepl",
    "google-cloud",
    "google-gtx",
)


def _translation_semaphore(limit: int) -> BoundedSemaphore:
    limit = max(1, min(limit, 16))
    with _semaphore_lock:
        return _provider_semaphores.setdefault(limit, BoundedSemaphore(limit))


class TranslationError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class TranslationConfigurationError(TranslationError):
    pass


class TranslationProvider(Protocol):
    name: str
    settings: Settings

    def translate(self, text: str, target: str = "zh-CN") -> str: ...


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_text(text: str, max_chars: int = 3500) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        candidates = [
            window.rfind("\n\n"),
            window.rfind(". "),
            window.rfind("? "),
            window.rfind("! "),
            window.rfind("。"),
            window.rfind(" "),
        ]
        cut = max(candidates)
        if cut < max_chars // 3:
            cut = max_chars
        elif window[cut : cut + 2] in {". ", "? ", "! "}:
            cut += 1
        chunk = remaining[:cut].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[cut:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _provider_error(name: str, exc: Exception) -> TranslationError:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return TranslationError(
            f"{name} request failed (HTTP {status_code})",
            retryable=(
                status_code in {408, 409, 425, 429}
                or 500 <= status_code <= 599
            ),
        )
    if isinstance(exc, httpx.RequestError):
        return TranslationError(
            f"{name} request failed: {exc.__class__.__name__}",
            retryable=True,
        )
    return TranslationError(f"{name} returned an invalid response")


def _translation_timeout(settings: Settings) -> httpx.Timeout:
    timeout = settings.translation_request_timeout_seconds
    return httpx.Timeout(
        connect=min(settings.request_timeout_seconds, timeout),
        read=timeout,
        write=settings.request_timeout_seconds,
        pool=settings.request_timeout_seconds,
    )


def _configuration_fingerprint(*values: str) -> str:
    return hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()[:12]


class GoogleGTXProvider:
    name = "google-gtx"

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.Client | None = None,
        route: HttpRoute | None = None,
    ):
        self.settings = settings or get_settings()
        self._client = client
        self.route = route or HttpRoute(proxy=None, trust_env=False)
        self._semaphore = _translation_semaphore(self.settings.translation_concurrency)

    def translate(self, text: str, target: str = "zh-CN") -> str:
        if not text.strip():
            return ""
        owned = self._client is None
        client = self._client or httpx.Client(
            timeout=_translation_timeout(self.settings),
            proxy=self.route.proxy,
            trust_env=self.route.trust_env,
        )
        try:
            with self._semaphore:
                response = client.get(
                    self.settings.gtx_endpoint,
                    params={"client": "gtx", "sl": "auto", "tl": target, "dt": "t", "q": text},
                    headers={"User-Agent": "AffogatoRSSReader/0.1 (+self-hosted)"},
                    timeout=_translation_timeout(self.settings),
                )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
                raise TranslationError("Unexpected Google GTX response")
            translated = "".join(
                str(segment[0])
                for segment in payload[0]
                if isinstance(segment, list) and segment and segment[0] is not None
            )
            if not translated:
                raise TranslationError("Google GTX returned an empty translation")
            return translated
        except TranslationError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise _provider_error("Google GTX", exc) from exc
        finally:
            if owned:
                client.close()


class CustomLLMProvider:
    name = "custom-llm"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        base_url: str,
        api_key: str,
        model: str,
        connection_id: int | None = None,
        connection_name: str | None = None,
        client: httpx.Client | None = None,
        route: HttpRoute | None = None,
    ):
        self.settings = settings or get_settings()
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.connection_id = connection_id
        self.connection_name = connection_name
        self._client = client
        self.route = route or HttpRoute(proxy=None, trust_env=False)
        self._semaphore = _translation_semaphore(self.settings.translation_concurrency)
        self.cache_name = f"llm-{_configuration_fingerprint(self.base_url, self.model)}"

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def translate(self, text: str, target: str = "zh-CN") -> str:
        if not text.strip():
            return ""
        owned = self._client is None
        client = self._client or httpx.Client(
            timeout=_translation_timeout(self.settings),
            proxy=self.route.proxy,
            trust_env=self.route.trust_env,
        )
        try:
            with self._semaphore:
                with client.stream(
                    "POST",
                    self.endpoint,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "temperature": 0,
                        "stream": True,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "You are a precise translation engine. Translate the user's "
                                    f"text into {target}. Preserve meaning, names, equations, "
                                    "citations, paragraph breaks, and formatting. Return only the "
                                    "translation, without commentary or Markdown fences."
                                ),
                            },
                            {"role": "user", "content": text},
                        ],
                    },
                    timeout=_translation_timeout(self.settings),
                ) as response:
                    response.raise_for_status()
                    content = stream_completion_text(response)
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty content")
            return content.strip()
        except (
            httpx.HTTPError,
            ValueError,
            TypeError,
            KeyError,
            IndexError,
        ) as exc:
            raise _provider_error("Custom LLM", exc) from exc
        finally:
            if owned:
                client.close()


class DeepLProvider:
    name = "deepl"
    _target_names = {
        "zh": "ZH-HANS",
        "zh-cn": "ZH-HANS",
        "zh-tw": "ZH-HANT",
        "en": "EN",
        "de": "DE",
        "fr": "FR",
        "ja": "JA",
    }

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        endpoint: str,
        api_key: str,
        client: httpx.Client | None = None,
        route: HttpRoute | None = None,
    ):
        self.settings = settings or get_settings()
        self.endpoint = endpoint
        self.api_key = api_key
        self._client = client
        self.route = route or HttpRoute(proxy=None, trust_env=False)
        self._semaphore = _translation_semaphore(self.settings.translation_concurrency)
        self.cache_name = f"deepl-{_configuration_fingerprint(self.endpoint)}"

    def translate(self, text: str, target: str = "zh-CN") -> str:
        if not text.strip():
            return ""
        owned = self._client is None
        client = self._client or httpx.Client(
            timeout=_translation_timeout(self.settings),
            proxy=self.route.proxy,
            trust_env=self.route.trust_env,
        )
        try:
            with self._semaphore:
                response = client.post(
                    self.endpoint,
                    headers={"Authorization": f"DeepL-Auth-Key {self.api_key}"},
                    data={
                        "text": text,
                        "target_lang": self._target_names.get(target.lower(), target.upper()),
                        "preserve_formatting": "1",
                    },
                    timeout=_translation_timeout(self.settings),
                )
            response.raise_for_status()
            translated = response.json()["translations"][0]["text"]
            if not isinstance(translated, str) or not translated:
                raise ValueError("empty translation")
            return translated
        except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError) as exc:
            raise _provider_error("DeepL", exc) from exc
        finally:
            if owned:
                client.close()


class GoogleCloudProvider:
    name = "google-cloud"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        endpoint: str,
        api_key: str,
        client: httpx.Client | None = None,
        route: HttpRoute | None = None,
    ):
        self.settings = settings or get_settings()
        self.endpoint = endpoint
        self.api_key = api_key
        self._client = client
        self.route = route or HttpRoute(proxy=None, trust_env=False)
        self._semaphore = _translation_semaphore(self.settings.translation_concurrency)
        self.cache_name = f"gcloud-{_configuration_fingerprint(self.endpoint)}"

    def translate(self, text: str, target: str = "zh-CN") -> str:
        if not text.strip():
            return ""
        owned = self._client is None
        client = self._client or httpx.Client(
            timeout=_translation_timeout(self.settings),
            proxy=self.route.proxy,
            trust_env=self.route.trust_env,
        )
        try:
            with self._semaphore:
                response = client.post(
                    self.endpoint,
                    headers={"X-goog-api-key": self.api_key},
                    json={"q": text, "target": target, "format": "text"},
                    timeout=_translation_timeout(self.settings),
                )
            response.raise_for_status()
            translated = response.json()["data"]["translations"][0]["translatedText"]
            if not isinstance(translated, str) or not translated:
                raise ValueError("empty translation")
            return unescape(translated)
        except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError) as exc:
            raise _provider_error("Google Cloud Translation", exc) from exc
        finally:
            if owned:
                client.close()


class FallbackProvider:
    name = TRANSLATION_RECORD_PROVIDER

    def __init__(self, providers: list[TranslationProvider], settings: Settings | None = None):
        if not providers:
            raise ValueError("At least one translation provider is required")
        self.providers = providers
        self.settings = settings or get_settings()
        identities = [getattr(item, "cache_name", item.name) for item in providers]
        self.cache_name = f"chain-{_configuration_fingerprint(*identities)}"

    def translate(self, text: str, target: str = "zh-CN") -> str:
        errors: list[str] = []
        retryable = False
        for provider in self.providers:
            try:
                return provider.translate(text, target)
            except Exception as exc:
                errors.append(f"{provider.name}: {exc}")
                retryable = retryable or bool(getattr(exc, "retryable", False))
        raise TranslationError(
            "All translation providers failed (" + "; ".join(errors) + ")",
            retryable=retryable,
        )


class UnavailableTranslationProvider:
    def __init__(self, name: str, settings: Settings, message: str):
        self.name = name
        self.settings = settings
        self.message = message
        self.cache_name = f"unavailable-{name}"

    def translate(self, text: str, target: str = "zh-CN") -> str:
        raise TranslationConfigurationError(self.message)


def _setting(db: Session, key: str, default: str | None = None) -> str | None:
    row = db.get(AppSetting, key)
    return row.value if row else default


def _set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))


def is_translation_enabled(db: Session, settings: Settings | None = None) -> bool:
    row = db.get(AppSetting, "translation_enabled")
    return (row.value.lower() == "true") if row else (settings or get_settings()).translation_enabled


def translation_target(db: Session, settings: Settings | None = None) -> str:
    row = db.get(AppSetting, "translation_target")
    return row.value if row else (settings or get_settings()).translation_target


def selected_translation_provider(db: Session, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    value = _setting(db, "translation_provider", settings.translation_provider)
    return value if value in AVAILABLE_TRANSLATION_PROVIDERS else "google-gtx"


def translation_fallback_mode(db: Session, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    value = _setting(
        db, "translation_fallback_mode", settings.translation_fallback_mode
    )
    return value if value in {"automatic", "manual"} else "automatic"


def translation_configuration(
    db: Session,
    settings: Settings | None = None,
    *,
    include_secrets: bool = False,
) -> dict:
    settings = settings or get_settings()
    connection = get_feature_connection(db, TRANSLATION_FEATURE)
    llm_api_key = (
        decrypt_llm_api_key(connection, settings)
        if include_secrets and connection
        else (settings.translation_llm_api_key if include_secrets else None)
    )
    deepl_row = db.get(AppSetting, "translation_deepl_api_key")
    google_cloud_row = db.get(AppSetting, "translation_google_cloud_api_key")
    deepl_configured = bool(
        (deepl_row and deepl_row.value) or settings.deepl_api_key
    )
    google_cloud_configured = bool(
        (google_cloud_row and google_cloud_row.value)
        or settings.google_cloud_translation_api_key
    )
    deepl_api_key = (
        encrypted_setting_value(
            db,
            "translation_deepl_api_key",
            default=settings.deepl_api_key,
            settings=settings,
        )
        if include_secrets
        else None
    )
    google_cloud_api_key = (
        encrypted_setting_value(
            db,
            "translation_google_cloud_api_key",
            default=settings.google_cloud_translation_api_key,
            settings=settings,
        )
        if include_secrets
        else None
    )
    return {
        "provider": selected_translation_provider(db, settings),
        "fallback_mode": translation_fallback_mode(db, settings),
        "llm_connection_id": connection.id if connection else None,
        "llm_connection_name": connection.name if connection else None,
        "llm_connections": list_llm_connections(db),
        "llm_base_url": (
            connection.base_url
            if connection
            else _setting(
                db, "translation_llm_base_url", settings.translation_llm_base_url
            )
        ),
        "llm_model": (
            connection.model
            if connection
            else _setting(db, "translation_llm_model", settings.translation_llm_model)
        ),
        "llm_api_key": llm_api_key,
        "llm_api_key_configured": bool(
            (connection and connection.api_key_encrypted)
            or settings.translation_llm_api_key
        ),
        "deepl_endpoint": _setting(
            db, "translation_deepl_endpoint", settings.deepl_endpoint
        ),
        "deepl_api_key": deepl_api_key if include_secrets else None,
        "deepl_api_key_configured": deepl_configured,
        "google_cloud_api_key": google_cloud_api_key if include_secrets else None,
        "google_cloud_api_key_configured": google_cloud_configured,
    }


def build_translation_provider(
    db: Session,
    settings: Settings | None = None,
) -> FallbackProvider:
    settings = settings or get_settings()
    try:
        config = translation_configuration(db, settings, include_secrets=True)
    except SecretKeyError as exc:
        raise TranslationConfigurationError(str(exc)) from exc
    selected = build_selected_translation_provider(db, settings, allow_unconfigured=True)
    providers: list[TranslationProvider] = []
    if selected is not None:
        providers.append(selected)
    elif config["fallback_mode"] == "manual":
        providers.append(
            UnavailableTranslationProvider(
                str(config["provider"]),
                settings,
                f"{config['provider']} credentials are not configured",
            )
        )
    if (
        config["fallback_mode"] == "automatic"
        and (selected is None or selected.name != GoogleGTXProvider.name)
    ):
        providers.append(
            GoogleGTXProvider(
                settings,
                route=http_route_for_translation_service(
                    db, GoogleGTXProvider.name, settings
                ),
            )
        )
    return FallbackProvider(providers, settings)


def build_selected_translation_provider(
    db: Session,
    settings: Settings | None = None,
    *,
    provider: str | None = None,
    llm_connection_id: int | None = None,
    llm_base_url: str | None = None,
    llm_model: str | None = None,
    llm_api_key: str | None = None,
    deepl_endpoint: str | None = None,
    deepl_api_key: str | None = None,
    google_cloud_api_key: str | None = None,
    allow_unconfigured: bool = False,
) -> TranslationProvider | None:
    settings = settings or get_settings()
    try:
        config = translation_configuration(db, settings, include_secrets=True)
    except SecretKeyError as exc:
        raise TranslationConfigurationError(str(exc)) from exc
    selected = provider or config["provider"]
    if selected == "custom-llm":
        requested_connection = (
            get_llm_connection(db, llm_connection_id) if llm_connection_id else None
        )
        if llm_connection_id and requested_connection is None:
            raise TranslationConfigurationError("LLM connection not found")
        effective_connection = requested_connection or get_feature_connection(
            db, TRANSLATION_FEATURE
        )
        try:
            stored_key = (
                decrypt_llm_api_key(effective_connection, settings)
                if effective_connection
                else None
            )
        except SecretKeyError as exc:
            raise TranslationConfigurationError(str(exc)) from exc
        key = llm_api_key or stored_key or config["llm_api_key"]
        if not key:
            if allow_unconfigured:
                return None
            raise TranslationConfigurationError("Custom LLM API key is not configured")
        return CustomLLMProvider(
            settings,
            base_url=(
                llm_base_url
                or (effective_connection.base_url if effective_connection else None)
                or str(config["llm_base_url"])
            ),
            api_key=str(key),
            model=(
                llm_model
                or (effective_connection.model if effective_connection else None)
                or str(config["llm_model"])
            ),
            connection_id=effective_connection.id if effective_connection else None,
            connection_name=effective_connection.name if effective_connection else None,
            route=http_route_for_llm_connection(
                db, effective_connection, settings
            ),
        )
    if selected == "deepl":
        key = deepl_api_key or config["deepl_api_key"]
        if not key:
            if allow_unconfigured:
                return None
            raise TranslationConfigurationError("DeepL API key is not configured")
        return DeepLProvider(
            settings,
            endpoint=deepl_endpoint or str(config["deepl_endpoint"]),
            api_key=str(key),
            route=http_route_for_translation_service(db, "deepl", settings),
        )
    if selected == "google-cloud":
        key = google_cloud_api_key or config["google_cloud_api_key"]
        if not key:
            if allow_unconfigured:
                return None
            raise TranslationConfigurationError(
                "Google Cloud Translation API key is not configured"
            )
        return GoogleCloudProvider(
            settings,
            endpoint=settings.google_cloud_translation_endpoint,
            api_key=str(key),
            route=http_route_for_translation_service(
                db, "google-cloud", settings
            ),
        )
    if selected == "google-gtx":
        return GoogleGTXProvider(
            settings,
            route=http_route_for_translation_service(db, "google-gtx", settings),
        )
    raise TranslationConfigurationError("Unsupported translation provider")


def ensure_translation_queue(db: Session, target: str | None = None) -> int:
    target = target or translation_target(db)
    existing: dict[int, Translation] = {}
    for row in db.scalars(
        select(Translation)
        .where(
            Translation.language == target,
            Translation.provider.in_(
                [TRANSLATION_RECORD_PROVIDER, LEGACY_TRANSLATION_PROVIDER]
            ),
        )
        .order_by(Translation.id)
    ):
        previous = existing.get(row.entry_id)
        if previous is None or row.provider == TRANSLATION_RECORD_PROVIDER:
            existing[row.entry_id] = row
    for row in existing.values():
        if row.provider == LEGACY_TRANSLATION_PROVIDER:
            row.provider = TRANSLATION_RECORD_PROVIDER
    entries = list(
        db.scalars(select(Entry).where(Entry.id.not_in(existing)).order_by(Entry.id))
    )
    for entry in entries:
        entry.translations.append(
            Translation(
                source_hash=entry.source_hash,
                status="pending",
                language=target,
                provider=TRANSLATION_RECORD_PROVIDER,
            )
        )
    if entries:
        db.flush()
    return len(entries)


def configure_translation(
    db: Session,
    *,
    enabled: bool,
    target: str | None = None,
    provider: str | None = None,
    fallback_mode: str | None = None,
    llm_connection_id: int | None = None,
    deepl_endpoint: str | None = None,
    deepl_api_key: str | None = None,
    clear_deepl_api_key: bool = False,
    google_cloud_api_key: str | None = None,
    clear_google_cloud_api_key: bool = False,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    _set_setting(db, "translation_enabled", "true" if enabled else "false")
    if target:
        _set_setting(db, "translation_target", target)
    if provider:
        if provider not in AVAILABLE_TRANSLATION_PROVIDERS:
            raise ValueError("Unsupported translation provider")
        _set_setting(db, "translation_provider", provider)
    if fallback_mode:
        if fallback_mode not in {"automatic", "manual"}:
            raise ValueError("Unsupported translation fallback mode")
        _set_setting(db, "translation_fallback_mode", fallback_mode)
    effective_provider = provider or selected_translation_provider(db, settings)
    if provider is not None and provider != "custom-llm":
        unbind_llm_connection(db, feature_key=TRANSLATION_FEATURE)
    elif llm_connection_id is not None:
        requested = get_llm_connection(db, llm_connection_id)
        if requested is None:
            raise ValueError("LLM connection not found")
        bind_llm_connection(
            db,
            feature_key=TRANSLATION_FEATURE,
            connection=requested,
        )
    if (
        effective_provider == "custom-llm"
        and get_feature_connection(db, TRANSLATION_FEATURE) is None
    ):
        raise ValueError("Select an existing LLM connection for translation")
    if deepl_endpoint is not None:
        _set_setting(db, "translation_deepl_endpoint", deepl_endpoint)
    if clear_deepl_api_key:
        set_encrypted_setting(
            db, "translation_deepl_api_key", None, settings=settings
        )
    elif deepl_api_key is not None:
        set_encrypted_setting(
            db, "translation_deepl_api_key", deepl_api_key, settings=settings
        )
    if clear_google_cloud_api_key:
        set_encrypted_setting(
            db, "translation_google_cloud_api_key", None, settings=settings
        )
    elif google_cloud_api_key is not None:
        set_encrypted_setting(
            db,
            "translation_google_cloud_api_key",
            google_cloud_api_key,
            settings=settings,
        )
    if enabled:
        ensure_translation_queue(db, target)
    db.commit()


def set_translation_enabled(db: Session, enabled: bool, target: str | None = None) -> None:
    configure_translation(db, enabled=enabled, target=target)


def _translate_once_with_log(
    provider: TranslationProvider,
    text: str,
    target: str,
    *,
    operation: str = "translate",
) -> str:
    started_at = perf_counter()
    model = provider.model if isinstance(provider, CustomLLMProvider) else None
    connection_id = (
        provider.connection_id if isinstance(provider, CustomLLMProvider) else None
    )
    connection_name = (
        provider.connection_name if isinstance(provider, CustomLLMProvider) else None
    )
    try:
        translated = provider.translate(text, target)
    except Exception as exc:
        duration_ms = round((perf_counter() - started_at) * 1000)
        safe_write_call_log(
            category="translation",
            operation=operation,
            feature="translation",
            provider=provider.name,
            model=model,
            connection_id=connection_id,
            connection_name=connection_name,
            target_language=target,
            status="error",
            duration_ms=duration_ms,
            input_chars=len(text),
            error=str(exc),
            settings=provider.settings,
        )
        if isinstance(provider, CustomLLMProvider):
            safe_write_call_log(
                category="llm",
                operation="chat_completion",
                feature="translation",
                provider=provider.name,
                model=provider.model,
                connection_id=connection_id,
                connection_name=connection_name,
                target_language=target,
                status="error",
                duration_ms=duration_ms,
                input_chars=len(text),
                error=str(exc),
                settings=provider.settings,
            )
        raise
    duration_ms = round((perf_counter() - started_at) * 1000)
    safe_write_call_log(
        category="translation",
        operation=operation,
        feature="translation",
        provider=provider.name,
        model=model,
        connection_id=connection_id,
        connection_name=connection_name,
        target_language=target,
        status="success",
        duration_ms=duration_ms,
        input_chars=len(text),
        output_chars=len(translated),
        settings=provider.settings,
    )
    if isinstance(provider, CustomLLMProvider):
        safe_write_call_log(
            category="llm",
            operation="chat_completion",
            feature="translation",
            provider=provider.name,
            model=provider.model,
            connection_id=connection_id,
            connection_name=connection_name,
            target_language=target,
            status="success",
            duration_ms=duration_ms,
            input_chars=len(text),
            output_chars=len(translated),
            settings=provider.settings,
        )
    return translated


def translate_with_log(
    provider: TranslationProvider,
    text: str,
    target: str,
    *,
    operation: str = "translate",
) -> str:
    attempts = provider.settings.translation_max_attempts
    for attempt in range(1, attempts + 1):
        try:
            return _translate_once_with_log(
                provider,
                text,
                target,
                operation=operation,
            )
        except Exception as exc:
            if (
                not bool(getattr(exc, "retryable", False))
                or attempt >= attempts
            ):
                raise
            sleep(
                provider.settings.translation_retry_base_seconds
                * (2 ** (attempt - 1))
            )
    raise AssertionError("translation retry loop exited unexpectedly")


def cached_translate(
    db: Session,
    provider: TranslationProvider,
    text: str,
    *,
    target: str = "zh-CN",
    max_chars: int | None = None,
) -> str:
    if not text.strip():
        return ""
    if isinstance(provider, FallbackProvider):
        errors: list[str] = []
        retryable = False
        for candidate in provider.providers:
            try:
                return cached_translate(
                    db,
                    candidate,
                    text,
                    target=target,
                    max_chars=max_chars,
                )
            except SQLAlchemyError as exc:
                db.rollback()
                errors.append(f"{candidate.name}: {exc}")
            except Exception as exc:
                errors.append(f"{candidate.name}: {exc}")
                retryable = retryable or bool(getattr(exc, "retryable", False))
        raise TranslationError(
            "All translation providers failed (" + "; ".join(errors) + ")",
            retryable=retryable,
        )

    cache_name = getattr(provider, "cache_name", provider.name)

    def find_cached(source: str) -> TranslationCache | None:
        return db.scalar(
            select(TranslationCache).where(
                TranslationCache.text_hash == text_hash(source),
                TranslationCache.language == target,
                TranslationCache.provider == cache_name,
            )
        )

    def log_cache_hit(source: str, translated_text: str) -> None:
        safe_write_call_log(
            category="translation",
            operation="translate",
            feature="translation",
            provider=provider.name,
            model=provider.model if isinstance(provider, CustomLLMProvider) else None,
            connection_id=(
                provider.connection_id
                if isinstance(provider, CustomLLMProvider)
                else None
            ),
            connection_name=(
                provider.connection_name
                if isinstance(provider, CustomLLMProvider)
                else None
            ),
            target_language=target,
            status="success",
            duration_ms=0,
            input_chars=len(source),
            output_chars=len(translated_text),
            cached=True,
            settings=provider.settings,
        )

    cached = find_cached(text)
    if cached:
        log_cache_hit(text, cached.translated_text)
        return cached.translated_text

    chunks = split_text(
        text,
        max_chars or provider.settings.translation_chunk_chars,
    )
    translated_parts: list[str] = []
    for chunk in chunks:
        cached_chunk = find_cached(chunk)
        if cached_chunk:
            log_cache_hit(chunk, cached_chunk.translated_text)
            translated_parts.append(cached_chunk.translated_text)
            continue
        translated_chunk = translate_with_log(provider, chunk, target)
        translated_parts.append(translated_chunk)
        db.add(
            TranslationCache(
                text_hash=text_hash(chunk),
                language=target,
                provider=cache_name,
                translated_text=translated_chunk,
            )
        )
        # Each completed chunk is a durable checkpoint. If a later chunk fails,
        # a background or manual retry resumes from the first missing chunk.
        db.commit()

    translated = "\n\n".join(translated_parts)
    if len(chunks) == 1:
        return translated
    db.add(
        TranslationCache(
            text_hash=text_hash(text),
            language=target,
            provider=cache_name,
            translated_text=translated,
        )
    )
    db.commit()
    return translated


def translate_one(
    db: Session,
    translation: Translation,
    provider: TranslationProvider | None = None,
) -> Translation:
    provider = provider or build_translation_provider(db)
    entry = db.get(Entry, translation.entry_id)
    if entry is None:
        translation.status = "failed"
        translation.last_error = "Entry no longer exists"
        db.commit()
        return translation
    translation.status = "running"
    translation.attempts += 1
    db.commit()
    intended_source_hash = translation.source_hash
    try:
        translated_title = cached_translate(
            db, provider, entry.title, target=translation.language
        )
        translated_summary = cached_translate(
            db, provider, entry.summary, target=translation.language
        )
        db.commit()
        db.refresh(entry)
        db.refresh(translation)
        if entry.source_hash != intended_source_hash or translation.source_hash != intended_source_hash:
            translation.status = "pending"
            translation.title = None
            translation.summary = None
            translation.last_error = "Source changed while translation was running"
            translation.next_retry_at = None
            db.commit()
            return translation
        translation.title = translated_title
        translation.summary = translated_summary
        translation.status = "complete"
        translation.last_error = None
        translation.next_retry_at = None
    except Exception as exc:
        db.rollback()
        translation = db.get(Translation, translation.id)
        assert translation is not None
        translation.status = "failed"
        translation.last_error = str(exc)[:4000]
        translation.next_retry_at = utcnow() + timedelta(
            minutes=min(2**translation.attempts * 5, 24 * 60)
        )
    db.commit()
    return translation


def translate_pending(
    db: Session,
    *,
    limit: int = 20,
    retry_failed: bool = False,
    provider: TranslationProvider | None = None,
    target: str | None = None,
) -> list[Translation]:
    if not is_translation_enabled(db):
        return []
    target = target or translation_target(db)
    ensure_translation_queue(db, target)
    db.commit()
    stale_running = utcnow() - timedelta(minutes=30)
    statuses = ["pending"] + (["failed"] if retry_failed else [])
    rows = list(
        db.scalars(
            select(Translation)
            .where(
                Translation.language == target,
                Translation.provider.in_(
                    [TRANSLATION_RECORD_PROVIDER, LEGACY_TRANSLATION_PROVIDER]
                ),
                or_(
                    Translation.status.in_(statuses),
                    and_(
                        Translation.status == "running",
                        Translation.updated_at <= stale_running,
                    ),
                ),
                or_(
                    Translation.next_retry_at.is_(None),
                    Translation.next_retry_at <= utcnow(),
                ),
            )
            .order_by(Translation.updated_at, Translation.id)
            .limit(limit)
        )
    )
    for row in rows:
        if row.status == "running":
            row.status = "pending"
            row.last_error = "Recovered an interrupted translation"
    db.commit()
    provider = provider or build_translation_provider(db)
    return [translate_one(db, row, provider) for row in rows]


def get_translation_record(db: Session, entry_id: int, target: str) -> Translation | None:
    rows = list(
        db.scalars(
            select(Translation)
            .where(
                Translation.entry_id == entry_id,
                Translation.language == target,
                Translation.provider.in_(
                    [TRANSLATION_RECORD_PROVIDER, LEGACY_TRANSLATION_PROVIDER]
                ),
            )
            .order_by(Translation.updated_at.desc(), Translation.id.desc())
        )
    )
    return next(
        (row for row in rows if row.provider == TRANSLATION_RECORD_PROVIDER),
        rows[0] if rows else None,
    )


def translation_status(db: Session) -> dict:
    target = translation_target(db)
    settings = get_settings()
    config = translation_configuration(db, settings)
    relevant = Translation.provider.in_(
        [TRANSLATION_RECORD_PROVIDER, LEGACY_TRANSLATION_PROVIDER]
    )
    counts = dict(
        db.execute(
            select(Translation.status, func.count())
            .where(Translation.language == target, relevant)
            .group_by(Translation.status)
        ).all()
    )
    last_success_at = db.scalar(
        select(func.max(Translation.updated_at)).where(
            Translation.status == "complete",
            Translation.language == target,
            relevant,
        )
    )
    failed = db.scalar(
        select(Translation)
        .where(Translation.status == "failed", Translation.language == target, relevant)
        .order_by(Translation.updated_at.desc())
        .limit(1)
    )
    selected = config["provider"]
    configured = (
        selected == "google-gtx"
        or (selected == "custom-llm" and config["llm_api_key_configured"])
        or (selected == "deepl" and config["deepl_api_key_configured"])
        or (selected == "google-cloud" and config["google_cloud_api_key_configured"])
    )
    return {
        "enabled": is_translation_enabled(db),
        "provider": selected,
        "fallback_provider": GoogleGTXProvider.name,
        "fallback_mode": config["fallback_mode"],
        "available_providers": list(AVAILABLE_TRANSLATION_PROVIDERS),
        "target_language": target,
        "counts": {
            name: int(counts.get(name, 0))
            for name in ("pending", "running", "complete", "failed")
        },
        "healthy": configured and failed is None,
        "last_success_at": last_success_at,
        "last_error": failed.last_error if failed else None,
        "llm_base_url": config["llm_base_url"],
        "llm_model": config["llm_model"],
        "llm_connection_id": config["llm_connection_id"],
        "llm_connection_name": config["llm_connection_name"],
        "llm_connections": config["llm_connections"],
        "llm_api_key_configured": config["llm_api_key_configured"],
        "deepl_endpoint": config["deepl_endpoint"],
        "deepl_api_key_configured": config["deepl_api_key_configured"],
        "google_cloud_api_key_configured": config[
            "google_cloud_api_key_configured"
        ],
    }


def queue_retry(
    db: Session,
    entry_ids: list[int] | None = None,
    target: str | None = None,
) -> int:
    target = target or translation_target(db)
    query = select(Translation).where(
        Translation.language == target,
        Translation.provider.in_(
            [TRANSLATION_RECORD_PROVIDER, LEGACY_TRANSLATION_PROVIDER]
        ),
        Translation.status == "failed",
    )
    if entry_ids:
        query = query.where(Translation.entry_id.in_(entry_ids))
    rows = list(db.scalars(query))
    for row in rows:
        row.status = "pending"
        row.last_error = None
        row.next_retry_at = None
    db.commit()
    return len(rows)
