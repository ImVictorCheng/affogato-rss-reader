from __future__ import annotations

import httpx
import json
from time import perf_counter
from typing import Callable
from sqlalchemy import select
from sqlalchemy.orm import Session

from .call_logging import safe_write_call_log
from .config import Settings, get_settings
from .models import AppSetting, LLMConnection, LLMFeatureBinding, NetworkProxyConfig
from .network_proxy import HttpRoute, http_route_for_llm_connection
from .secrets import SecretCipher, is_encrypted_secret, secret_hint

TRANSLATION_FEATURE = "translation"
BRIEF_FEATURE = "brief"
ENCRYPTED_SETTING_KEYS = (
    "translation_deepl_api_key",
    "translation_google_cloud_api_key",
)


class LLMConnectionError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class LLMConnectionInUseError(LLMConnectionError):
    pass


def _completion_text(payload: dict, *, require_complete: bool = False) -> str:
    choice = payload["choices"][0]
    finish_reason = choice.get("finish_reason")
    if require_complete and finish_reason in {
        "length",
        "max_tokens",
        "max_output_tokens",
    }:
        raise ValueError(
            f"LLM output was truncated by its output limit "
            f"(finish_reason={finish_reason})"
        )
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        text_parts = [
            part.get("text", "").strip()
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        if any(text_parts):
            return "\n".join(part for part in text_parts if part)
    legacy_text = choice.get("text")
    if isinstance(legacy_text, str) and legacy_text.strip():
        return legacy_text.strip()
    resolved_finish_reason = finish_reason or "unknown"
    raise ValueError(
        f"LLM returned no text (finish_reason={resolved_finish_reason})"
    )


def stream_completion_text(
    response: httpx.Response,
    progress_callback: Callable[[int], None] | None = None,
) -> str:
    """Read an OpenAI-compatible SSE response, with JSON fallback."""
    parts: list[str] = []
    plain_lines: list[str] = []
    last_payload: dict | None = None
    received_chars = 0
    for raw_line in response.iter_lines():
        line = raw_line.strip()
        if not line:
            continue
        if not line.startswith("data:"):
            plain_lines.append(line)
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        payload = json.loads(data)
        last_payload = payload
        choices = payload.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        finish_reason = choice.get("finish_reason")
        if finish_reason in {"length", "max_tokens", "max_output_tokens"}:
            raise ValueError(
                f"LLM output was truncated by its output limit "
                f"(finish_reason={finish_reason})"
            )
        message = choice.get("delta") or choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
            received_chars += len(content)
        elif isinstance(content, list):
            text_parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ]
            parts.extend(text_parts)
            received_chars += sum(len(part) for part in text_parts)
        if progress_callback and received_chars:
            progress_callback(received_chars)
    result = "".join(parts).strip()
    if result:
        return result
    if plain_lines:
        return _completion_text(
            json.loads("\n".join(plain_lines)),
            require_complete=True,
        )
    if last_payload is not None:
        return _completion_text(last_payload, require_complete=True)
    raise ValueError("LLM stream returned no data")


def _connection_context(connection_id: int) -> str:
    return f"llm_connection:{connection_id}:api_key"


def get_llm_connection(db: Session, connection_id: int) -> LLMConnection | None:
    return db.get(LLMConnection, connection_id)


def get_feature_connection(db: Session, feature_key: str) -> LLMConnection | None:
    binding = db.get(LLMFeatureBinding, feature_key)
    return binding.connection if binding else None


def llm_connection_summary(connection: LLMConnection) -> dict:
    return {
        "id": connection.id,
        "name": connection.name,
        "base_url": connection.base_url,
        "model": connection.model,
        "api_key_configured": bool(connection.api_key_encrypted),
        "api_key_hint": connection.api_key_hint,
        "used_by": sorted(binding.feature_key for binding in connection.bindings),
    }


def list_llm_connections(db: Session) -> list[dict]:
    return [
        llm_connection_summary(connection)
        for connection in db.scalars(
            select(LLMConnection).order_by(LLMConnection.name, LLMConnection.id)
        )
    ]


def decrypt_llm_api_key(
    connection: LLMConnection,
    settings: Settings | None = None,
) -> str | None:
    if not connection.api_key_encrypted:
        return None
    return SecretCipher(settings).decrypt(
        connection.api_key_encrypted,
        context=_connection_context(connection.id),
    )


def save_llm_connection(
    db: Session,
    *,
    name: str,
    base_url: str,
    model: str,
    api_key: str | None = None,
    connection_id: int | None = None,
    clear_api_key: bool = False,
    settings: Settings | None = None,
) -> LLMConnection:
    settings = settings or get_settings()
    connection = db.get(LLMConnection, connection_id) if connection_id else None
    if connection_id and connection is None:
        raise ValueError("LLM connection not found")
    if connection is None:
        connection = LLMConnection(
            name=name.strip(),
            base_url=base_url.rstrip("/"),
            model=model.strip(),
        )
        db.add(connection)
        db.flush()
    else:
        connection.name = name.strip()
        connection.base_url = base_url.rstrip("/")
        connection.model = model.strip()
    if clear_api_key:
        connection.api_key_encrypted = None
        connection.api_key_hint = None
    elif api_key is not None:
        connection.api_key_encrypted = SecretCipher(settings).encrypt(
            api_key,
            context=_connection_context(connection.id),
        )
        connection.api_key_hint = secret_hint(api_key)
    db.flush()
    return connection


def delete_llm_connection(db: Session, connection: LLMConnection) -> None:
    used_by = sorted(binding.feature_key for binding in connection.bindings)
    if used_by:
        raise LLMConnectionInUseError(
            f"LLM connection is used by: {', '.join(used_by)}"
        )
    db.delete(connection)
    db.flush()


def probe_llm_connection(
    *,
    base_url: str,
    model: str,
    api_key: str,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
    route: HttpRoute | None = None,
    connection_id: int | None = None,
    connection_name: str | None = None,
) -> str:
    """Make a small OpenAI-compatible chat-completions call."""

    settings = settings or get_settings()
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    owns_client = client is None
    route = route or HttpRoute(proxy=None, trust_env=False)
    client = client or httpx.Client(
        timeout=settings.request_timeout_seconds,
        proxy=route.proxy,
        trust_env=route.trust_env,
    )
    started_at = perf_counter()
    try:
        response = client.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "user", "content": "Reply exactly with OK."},
                ],
            },
        )
        response.raise_for_status()
        payload = response.json()
        try:
            result = _completion_text(payload)
        except ValueError as exc:
            choice = payload["choices"][0]
            message = choice.get("message") or {}
            reasoning_content = message.get("reasoning_content")
            if isinstance(reasoning_content, str) and reasoning_content.strip():
                result = "OK (reasoning response received)"
            else:
                raise exc
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError) as exc:
        error = LLMConnectionError(f"LLM connection test failed: {exc}")
        safe_write_call_log(
            category="llm",
            operation="connection_test",
            feature="connection_test",
            status="error",
            duration_ms=round((perf_counter() - started_at) * 1000),
            input_chars=len("Reply exactly with OK."),
            model=model,
            connection_id=connection_id,
            connection_name=connection_name,
            error=str(error),
            settings=settings,
        )
        raise error from exc
    else:
        safe_write_call_log(
            category="llm",
            operation="connection_test",
            feature="connection_test",
            status="success",
            duration_ms=round((perf_counter() - started_at) * 1000),
            input_chars=len("Reply exactly with OK."),
            output_chars=len(result),
            model=model,
            connection_id=connection_id,
            connection_name=connection_name,
            settings=settings,
        )
        return result
    finally:
        if owns_client:
            client.close()


def complete_feature_chat(
    db: Session,
    *,
    feature_key: str,
    system_prompt: str,
    user_prompt: str,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
    temperature: float = 0.2,
    timeout_seconds: float | None = None,
    stream_progress_callback: Callable[[int], None] | None = None,
) -> str:
    """Generate text with the LLM connection bound to a product feature."""

    settings = settings or get_settings()
    started_at = perf_counter()
    input_chars = len(system_prompt) + len(user_prompt)
    connection = get_feature_connection(db, feature_key)
    if connection is None:
        error = LLMConnectionError(
            "No LLM connection is configured for this feature"
        )
        safe_write_call_log(
            category="llm",
            operation="chat_completion",
            feature=feature_key,
            status="error",
            duration_ms=round((perf_counter() - started_at) * 1000),
            input_chars=input_chars,
            error=str(error),
            settings=settings,
        )
        raise error
    try:
        api_key = decrypt_llm_api_key(connection, settings)
    except Exception as exc:
        error = LLMConnectionError(f"Unable to read the LLM API key: {exc}")
        safe_write_call_log(
            category="llm",
            operation="chat_completion",
            feature=feature_key,
            status="error",
            duration_ms=round((perf_counter() - started_at) * 1000),
            input_chars=input_chars,
            model=connection.model,
            connection_id=connection.id,
            connection_name=connection.name,
            error=str(error),
            settings=settings,
        )
        raise error from exc
    if not api_key:
        error = LLMConnectionError(
            "The selected LLM connection does not have an API key"
        )
        safe_write_call_log(
            category="llm",
            operation="chat_completion",
            feature=feature_key,
            status="error",
            duration_ms=round((perf_counter() - started_at) * 1000),
            input_chars=input_chars,
            model=connection.model,
            connection_id=connection.id,
            connection_name=connection.name,
            error=str(error),
            settings=settings,
        )
        raise error

    endpoint = (
        connection.base_url
        if connection.base_url.endswith("/chat/completions")
        else f"{connection.base_url.rstrip('/')}/chat/completions"
    )
    route = http_route_for_llm_connection(db, connection, settings)
    owns_client = client is None
    read_timeout = timeout_seconds or settings.request_timeout_seconds
    client = client or httpx.Client(
        timeout=httpx.Timeout(
            connect=min(settings.request_timeout_seconds, read_timeout),
            read=read_timeout,
            write=settings.request_timeout_seconds,
            pool=settings.request_timeout_seconds,
        ),
        proxy=route.proxy,
        trust_env=route.trust_env,
    )
    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": connection.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if feature_key == BRIEF_FEATURE:
            with client.stream(
                "POST",
                endpoint,
                headers=headers,
                json={**payload, "stream": True},
            ) as response:
                response.raise_for_status()
                result = stream_completion_text(
                    response,
                    progress_callback=stream_progress_callback,
                )
        else:
            response = client.post(
                endpoint,
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            result = _completion_text(response.json(), require_complete=True)
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError) as exc:
        retryable = (
            isinstance(exc, httpx.RequestError)
            or (
                isinstance(exc, httpx.HTTPStatusError)
                and (
                    exc.response.status_code in {408, 409, 425, 429}
                    or exc.response.status_code >= 500
                )
            )
        )
        error = LLMConnectionError(
            f"LLM summary generation failed: {exc}",
            retryable=retryable,
        )
        safe_write_call_log(
            category="llm",
            operation="chat_completion",
            feature=feature_key,
            status="error",
            duration_ms=round((perf_counter() - started_at) * 1000),
            input_chars=input_chars,
            model=connection.model,
            connection_id=connection.id,
            connection_name=connection.name,
            error=str(error),
            settings=settings,
        )
        raise error from exc
    else:
        safe_write_call_log(
            category="llm",
            operation="chat_completion",
            feature=feature_key,
            status="success",
            duration_ms=round((perf_counter() - started_at) * 1000),
            input_chars=input_chars,
            output_chars=len(result),
            model=connection.model,
            connection_id=connection.id,
            connection_name=connection.name,
            settings=settings,
        )
        return result
    finally:
        if owns_client:
            client.close()


def bind_llm_connection(
    db: Session,
    *,
    feature_key: str,
    connection: LLMConnection,
) -> None:
    binding = db.get(LLMFeatureBinding, feature_key)
    if binding:
        binding.connection_id = connection.id
    else:
        db.add(
            LLMFeatureBinding(
                feature_key=feature_key,
                connection_id=connection.id,
            )
        )
    db.flush()


def unbind_llm_connection(db: Session, *, feature_key: str) -> None:
    binding = db.get(LLMFeatureBinding, feature_key)
    if binding:
        db.delete(binding)
        db.flush()


def save_feature_llm_connection(
    db: Session,
    *,
    feature_key: str,
    name: str,
    base_url: str,
    model: str,
    api_key: str | None = None,
    connection_id: int | None = None,
    create_new: bool = False,
    clear_api_key: bool = False,
    settings: Settings | None = None,
) -> LLMConnection:
    existing = None if create_new else get_feature_connection(db, feature_key)
    connection = save_llm_connection(
        db,
        name=name,
        base_url=base_url,
        model=model,
        api_key=api_key,
        connection_id=connection_id or (existing.id if existing else None),
        clear_api_key=clear_api_key,
        settings=settings,
    )
    bind_llm_connection(db, feature_key=feature_key, connection=connection)
    return connection


def encrypted_setting_value(
    db: Session,
    key: str,
    *,
    default: str | None = None,
    settings: Settings | None = None,
) -> str | None:
    row = db.get(AppSetting, key)
    value = row.value if row else default
    if not value:
        return None
    if is_encrypted_secret(value):
        return SecretCipher(settings).decrypt(value, context=f"app_setting:{key}")
    return value


def set_encrypted_setting(
    db: Session,
    key: str,
    value: str | None,
    *,
    settings: Settings | None = None,
) -> None:
    row = db.get(AppSetting, key)
    stored = (
        SecretCipher(settings).encrypt(value, context=f"app_setting:{key}")
        if value
        else ""
    )
    if row:
        row.value = stored
    else:
        db.add(AppSetting(key=key, value=stored))


def rotate_encrypted_secrets(
    db: Session,
    settings: Settings | None = None,
) -> int:
    """Re-encrypt all stored API keys with the active master key."""

    cipher = SecretCipher(settings)
    rotated = 0
    for connection in db.scalars(select(LLMConnection)):
        if connection.api_key_encrypted:
            connection.api_key_encrypted = cipher.rotate(
                connection.api_key_encrypted,
                context=_connection_context(connection.id),
            )
            rotated += 1
    for key in ENCRYPTED_SETTING_KEYS:
        row = db.get(AppSetting, key)
        if row and is_encrypted_secret(row.value):
            row.value = cipher.rotate(row.value, context=f"app_setting:{key}")
            rotated += 1
    proxy_config = db.get(NetworkProxyConfig, 1)
    if proxy_config and proxy_config.password_encrypted:
        proxy_config.password_encrypted = cipher.rotate(
            proxy_config.password_encrypted,
            context="network_proxy:1:password",
        )
        rotated += 1
    if rotated:
        db.flush()
    return rotated


def migrate_legacy_secrets(db: Session, settings: Settings | None = None) -> int:
    """Encrypt legacy plaintext settings and create the translation LLM connection."""

    settings = settings or get_settings()
    migrated = 0
    for key in ENCRYPTED_SETTING_KEYS:
        row = db.get(AppSetting, key)
        if row and row.value and not is_encrypted_secret(row.value):
            row.value = SecretCipher(settings).encrypt(
                row.value,
                context=f"app_setting:{key}",
            )
            migrated += 1

    legacy_key = db.get(AppSetting, "translation_llm_api_key")
    if legacy_key and legacy_key.value:
        connection = get_feature_connection(db, TRANSLATION_FEATURE)
        base_url_row = db.get(AppSetting, "translation_llm_base_url")
        model_row = db.get(AppSetting, "translation_llm_model")
        if connection is None:
            connection = save_feature_llm_connection(
                db,
                feature_key=TRANSLATION_FEATURE,
                name="Translation LLM",
                base_url=(
                    base_url_row.value
                    if base_url_row
                    else settings.translation_llm_base_url
                ),
                model=model_row.value if model_row else settings.translation_llm_model,
                api_key=legacy_key.value,
                settings=settings,
            )
        elif not connection.api_key_encrypted:
            connection.api_key_encrypted = SecretCipher(settings).encrypt(
                legacy_key.value,
                context=_connection_context(connection.id),
            )
            connection.api_key_hint = secret_hint(legacy_key.value)
        db.delete(legacy_key)
        migrated += 1
    if migrated:
        db.flush()
    return migrated
