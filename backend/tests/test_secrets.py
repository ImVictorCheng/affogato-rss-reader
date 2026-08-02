from __future__ import annotations

from cryptography.fernet import Fernet
import httpx
import json
import pytest

from backend.app.call_logging import read_call_logs
from backend.app.config import Settings
from backend.app.llm import (
    BRIEF_FEATURE,
    LLMConnectionError,
    TRANSLATION_FEATURE,
    complete_feature_chat,
    decrypt_llm_api_key,
    get_feature_connection,
    migrate_legacy_secrets,
    probe_llm_connection,
    rotate_encrypted_secrets,
    save_feature_llm_connection,
)
from backend.app.models import AppSetting, LLMConnection
from backend.app.secrets import SecretCipher, SecretKeyError


def test_secret_cipher_round_trip_is_context_bound(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        secret_key_file=tmp_path / "keys" / "master.key",
    )
    cipher = SecretCipher(settings)
    token = cipher.encrypt("sk-test-secret", context="feature:one")

    assert token.startswith("fernet:v1:")
    assert "sk-test-secret" not in token
    assert cipher.decrypt(token, context="feature:one") == "sk-test-secret"
    with pytest.raises(SecretKeyError, match="context"):
        cipher.decrypt(token, context="feature:two")
    assert settings.effective_secret_key_file.exists()


def test_secret_cipher_supports_key_rotation(tmp_path):
    old_key = tmp_path / "old.key"
    active_key = tmp_path / "active.key"
    old_key.write_bytes(Fernet.generate_key())
    old_settings = Settings(data_dir=tmp_path, secret_key_file=old_key)
    old_token = SecretCipher(old_settings).encrypt("secret", context="connection:1")

    active_key.write_bytes(Fernet.generate_key())
    rotating_settings = Settings(
        data_dir=tmp_path,
        secret_key_file=active_key,
        secret_key_previous_files=str(old_key),
    )
    rotating_cipher = SecretCipher(rotating_settings)
    rotated_token = rotating_cipher.rotate(old_token, context="connection:1")

    assert rotating_cipher.decrypt(rotated_token, context="connection:1") == "secret"
    with pytest.raises(SecretKeyError):
        SecretCipher(old_settings).decrypt(rotated_token, context="connection:1")


def test_llm_connection_probe_uses_openai_compatible_endpoint(settings):
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://llm.example/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer probe-secret"
        payload = __import__("json").loads(request.content)
        assert payload["model"] == "general-model"
        assert "max_tokens" not in payload
        assert "temperature" not in payload
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "OK"}}]},
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert probe_llm_connection(
            base_url="https://llm.example/v1",
            model="general-model",
            api_key="probe-secret",
            settings=settings,
            client=client,
        ) == "OK"
    logs = read_call_logs(settings=settings)
    assert logs[0]["category"] == "llm"
    assert logs[0]["feature"] == "connection_test"
    assert logs[0]["status"] == "success"
    assert logs[0]["model"] == "general-model"
    assert "probe-secret" not in settings.effective_call_log_file.read_text(
        encoding="utf-8"
    )


def test_llm_connection_probe_accepts_reasoning_and_content_parts(settings):
    responses = iter(
        [
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": "The requested answer is OK.",
                        },
                        "finish_reason": "length",
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "output_text", "text": "O"},
                                {"type": "output_text", "text": "K"},
                            ]
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        ]
    )

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(responses))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        arguments = {
            "base_url": "https://llm.example/v1",
            "model": "reasoning-model",
            "api_key": "probe-secret",
            "settings": settings,
            "client": client,
        }
        assert probe_llm_connection(**arguments) == "OK (reasoning response received)"
        assert probe_llm_connection(**arguments) == "O\nK"


def test_llm_connection_probe_reports_finish_reason_for_empty_output(settings):
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": None},
                        "finish_reason": "content_filter",
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(
            LLMConnectionError,
            match=r"finish_reason=content_filter",
        ):
            probe_llm_connection(
                base_url="https://llm.example/v1",
                model="filtered-model",
                api_key="probe-secret",
                settings=settings,
                client=client,
            )


def test_feature_completion_rejects_output_limit_truncation(
    db_factory, settings
):
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "这是一段不完整的简报"},
                        "finish_reason": "length",
                    }
                ]
            },
        )

    with db_factory() as db:
        save_feature_llm_connection(
            db,
            feature_key=BRIEF_FEATURE,
            name="Brief LLM",
            base_url="https://llm.example/v1",
            model="summary-model",
            api_key="probe-secret",
            settings=settings,
        )
        db.commit()
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(
                LLMConnectionError,
                match=r"output was truncated.*finish_reason=length",
            ):
                complete_feature_chat(
                    db,
                    feature_key=BRIEF_FEATURE,
                    system_prompt="生成完整简报。",
                    user_prompt="输入材料。",
                    settings=settings,
                    client=client,
                )
    logs = read_call_logs(settings=settings)
    assert logs[0]["feature"] == BRIEF_FEATURE
    assert logs[0]["status"] == "error"
    assert "finish_reason=length" in logs[0]["error"]


def test_feature_completion_marks_503_as_retryable(db_factory, settings):
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request, text="temporarily unavailable")

    with db_factory() as db:
        save_feature_llm_connection(
            db,
            feature_key=BRIEF_FEATURE,
            name="Brief LLM",
            base_url="https://llm.example/v1",
            model="summary-model",
            api_key="probe-secret",
            settings=settings,
        )
        db.commit()
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(LLMConnectionError) as caught:
                complete_feature_chat(
                    db,
                    feature_key=BRIEF_FEATURE,
                    system_prompt="生成简报。",
                    user_prompt="输入材料。",
                    settings=settings,
                    client=client,
                )
    assert caught.value.retryable is True
    assert "503 Service Unavailable" in str(caught.value)


def test_brief_completion_uses_streaming_chunks(db_factory, settings):
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        return httpx.Response(
            200,
            request=request,
            headers={"Content-Type": "text/event-stream"},
            text=(
                'data: {"choices":[{"delta":{"content":"## 概览\\n\\n"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"持续返回内容。"},'
                '"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    with db_factory() as db:
        save_feature_llm_connection(
            db,
            feature_key=BRIEF_FEATURE,
            name="Streaming Brief LLM",
            base_url="https://llm.example/v1",
            model="summary-model",
            api_key="probe-secret",
            settings=settings,
        )
        db.commit()
        received: list[int] = []
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            result = complete_feature_chat(
                db,
                feature_key=BRIEF_FEATURE,
                system_prompt="生成简报。",
                user_prompt="输入材料。",
                settings=settings,
                client=client,
                timeout_seconds=30,
                stream_progress_callback=received.append,
            )
    assert result == "## 概览\n\n持续返回内容。"
    assert received[-1] == len(result)


def test_stored_connection_can_be_rotated_to_a_new_master_key(
    db_factory, settings, tmp_path
):
    old_key = tmp_path / "old-master.key"
    new_key = tmp_path / "new-master.key"
    old_settings = settings.model_copy(update={"secret_key_file": old_key})
    with db_factory() as db:
        connection = save_feature_llm_connection(
            db,
            feature_key=TRANSLATION_FEATURE,
            name="Rotating LLM",
            base_url="https://llm.example/v1",
            model="translator",
            api_key="rotating-secret",
            settings=old_settings,
        )
        db.commit()
        old_token = connection.api_key_encrypted

        new_settings = settings.model_copy(
            update={
                "secret_key_file": new_key,
                "secret_key_previous_files": str(old_key),
            }
        )
        assert rotate_encrypted_secrets(db, new_settings) == 1
        db.commit()
        assert connection.api_key_encrypted != old_token
        assert decrypt_llm_api_key(connection, new_settings) == "rotating-secret"
        with pytest.raises(SecretKeyError):
            decrypt_llm_api_key(connection, old_settings)


def test_llm_connections_encrypt_keys_and_can_be_reused(db_factory, settings):
    with db_factory() as db:
        connection = save_feature_llm_connection(
            db,
            feature_key=TRANSLATION_FEATURE,
            name="Shared LLM",
            base_url="https://llm.example/v1",
            model="translator",
            api_key="sk-shared-secret",
            settings=settings,
        )
        save_feature_llm_connection(
            db,
            feature_key="summaries",
            name=connection.name,
            base_url=connection.base_url,
            model=connection.model,
            connection_id=connection.id,
            settings=settings,
        )
        db.commit()

        stored = db.get(LLMConnection, connection.id)
        assert stored is not None
        assert stored.api_key_encrypted.startswith("fernet:v1:")
        assert "sk-shared-secret" not in stored.api_key_encrypted
        assert decrypt_llm_api_key(stored, settings) == "sk-shared-secret"
        assert sorted(binding.feature_key for binding in stored.bindings) == [
            "summaries",
            TRANSLATION_FEATURE,
        ]


def test_legacy_translation_keys_are_migrated_without_plaintext(
    db_factory, settings
):
    with db_factory() as db:
        db.add_all(
            [
                AppSetting(key="translation_llm_api_key", value="legacy-llm-key"),
                AppSetting(
                    key="translation_llm_base_url",
                    value="https://legacy.example/v1",
                ),
                AppSetting(key="translation_llm_model", value="legacy-model"),
                AppSetting(key="translation_deepl_api_key", value="legacy-deepl-key"),
            ]
        )
        db.commit()

        assert migrate_legacy_secrets(db, settings) == 2
        db.commit()

        assert db.get(AppSetting, "translation_llm_api_key") is None
        connection = get_feature_connection(db, TRANSLATION_FEATURE)
        assert connection is not None
        assert connection.base_url == "https://legacy.example/v1"
        assert connection.model == "legacy-model"
        assert decrypt_llm_api_key(connection, settings) == "legacy-llm-key"
        deepl = db.get(AppSetting, "translation_deepl_api_key")
        assert deepl is not None
        assert deepl.value.startswith("fernet:v1:")
        assert "legacy-deepl-key" not in deepl.value
