from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from backend.app.llm import save_llm_connection
from backend.app.call_logging import read_call_logs
from backend.app.models import Entry, LLMConnection, Translation, Work
from backend.app.translation import (
    CustomLLMProvider,
    DeepLProvider,
    FallbackProvider,
    GoogleCloudProvider,
    TRANSLATION_RECORD_PROVIDER,
    TranslationError,
    build_translation_provider,
    cached_translate,
    configure_translation,
    queue_retry,
    set_translation_enabled,
    split_text,
    translate_with_log,
    translate_one,
    translation_status,
)


class FakeProvider:
    name = "fake"

    def __init__(self, settings, fail: bool = False):
        self.settings = settings
        self.fail = fail
        self.calls: list[str] = []

    def translate(self, text: str, target: str = "zh-CN") -> str:
        self.calls.append(text)
        if self.fail:
            raise RuntimeError("provider unavailable")
        return f"ZH:{text}"


def test_chunking_obeys_limit_and_preserves_text():
    value = ("Sentence one. Sentence two. " * 20).strip()
    chunks = split_text(value, 80)
    assert len(chunks) > 1
    assert all(len(chunk) <= 80 for chunk in chunks)
    assert " ".join(chunks).replace("  ", " ") == value


def test_translation_cache_avoids_duplicate_provider_calls(db_factory, settings):
    provider = FakeProvider(settings)
    with db_factory() as db:
        first = cached_translate(db, provider, "same source text", max_chars=100)
        second = cached_translate(db, provider, "same source text", max_chars=100)
        db.commit()
        assert first == second == "ZH:same source text"
        assert provider.calls == ["same source text"]
    logs = read_call_logs(settings=settings)
    assert [item["cached"] for item in logs] == [True, False]
    assert all(item["category"] == "translation" for item in logs)
    assert all(item["input_chars"] == len("same source text") for item in logs)


def test_provider_failure_is_recorded_and_does_not_change_entry(db_factory, settings):
    with db_factory() as db:
        work = Work(dedup_key="url:https://x.test/", canonical_url="https://x.test/")
        db.add(work)
        db.flush()
        entry = Entry(
            work_id=work.id,
            version_key="default",
            title="Original",
            summary="Abstract",
            url="https://x.test/",
            source_hash="x" * 64,
        )
        db.add(entry)
        db.flush()
        row = Translation(entry_id=entry.id, source_hash=entry.source_hash, status="pending")
        db.add(row)
        db.commit()
        result = translate_one(db, row, FakeProvider(settings, fail=True))
        assert result.status == "failed"
        assert "provider unavailable" in result.last_error
        assert result.next_retry_at is not None
        assert db.get(Entry, entry.id).title == "Original"


def test_enabling_translation_backfills_entries_created_while_disabled(db_factory):
    with db_factory() as db:
        work = Work(dedup_key="url:https://disabled.test/", canonical_url="https://disabled.test/")
        db.add(work)
        db.flush()
        entry = Entry(
            work_id=work.id,
            version_key="default",
            title="Queued after enable",
            summary="Summary",
            url="https://disabled.test/",
            source_hash="e" * 64,
        )
        db.add(entry)
        db.commit()
        assert db.scalar(select(Translation).where(Translation.entry_id == entry.id)) is None
        set_translation_enabled(db, True)
        queued = db.scalar(select(Translation).where(Translation.entry_id == entry.id))
        assert queued is not None
        assert queued.status == "pending"
        assert queued.source_hash == entry.source_hash
        assert queued.provider == TRANSLATION_RECORD_PROVIDER


def test_custom_llm_uses_openai_compatible_chat_completions(settings):
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://llm.test/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer secret"
        assert request.extensions["timeout"]["read"] == 30.0
        payload = json.loads(request.content)
        assert payload["model"] == "translator"
        assert payload["stream"] is True
        assert payload["messages"][-1]["content"] == "Original"
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=(
                'data: {"choices":[{"delta":{"content":"Trans"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"lated"}}]}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider = CustomLLMProvider(
            settings,
            base_url="https://llm.test/v1",
            api_key="secret",
            model="translator",
            client=client,
        )
        assert provider.translate("Original") == "Translated"


def test_transient_translation_errors_retry_with_exponential_policy(settings):
    retry_settings = settings.model_copy(
        update={
            "translation_max_attempts": 4,
            "translation_retry_base_seconds": 0,
        }
    )

    class FlakyProvider(FakeProvider):
        def translate(self, text: str, target: str = "zh-CN") -> str:
            self.calls.append(text)
            if len(self.calls) < 3:
                raise TranslationError("temporary timeout", retryable=True)
            return f"ZH:{text}"

    provider = FlakyProvider(retry_settings)
    assert translate_with_log(provider, "Original", "zh-CN") == "ZH:Original"
    assert provider.calls == ["Original", "Original", "Original"]


def test_http_503_is_retryable_for_llm_translation(settings):
    retry_settings = settings.model_copy(
        update={
            "translation_max_attempts": 4,
            "translation_retry_base_seconds": 0,
        }
    )
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503, text="temporarily unavailable")
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=(
                'data: {"choices":[{"delta":{"content":"Translated"}}]}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider = CustomLLMProvider(
            retry_settings,
            base_url="https://llm.test/v1",
            api_key="secret",
            model="translator",
            client=client,
        )
        assert translate_with_log(provider, "Original", "zh-CN") == "Translated"
    assert calls == 3


def test_chunk_cache_resumes_after_the_first_missing_chunk(db_factory, settings):
    source = "Alpha beta gamma delta epsilon zeta eta theta"
    chunks = split_text(source, 12)

    class FailsSecondChunkOnce(FakeProvider):
        def __init__(self, provider_settings):
            super().__init__(provider_settings)
            self.failed = False

        def translate(self, text: str, target: str = "zh-CN") -> str:
            self.calls.append(text)
            if text == chunks[1] and not self.failed:
                self.failed = True
                raise TranslationError("temporary failure")
            return f"ZH:{text}"

    provider = FailsSecondChunkOnce(settings)
    with db_factory() as db:
        with pytest.raises(TranslationError):
            cached_translate(db, provider, source, max_chars=12)
        translated = cached_translate(db, provider, source, max_chars=12)

    assert translated == "\n\n".join(f"ZH:{chunk}" for chunk in chunks)
    assert provider.calls.count(chunks[0]) == 1
    assert provider.calls.count(chunks[1]) == 2


def test_deepl_and_google_cloud_provider_response_parsing(settings):
    def deepl_handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "DeepL-Auth-Key deepl-secret"
        assert b"target_lang=ZH-HANS" in request.content
        return httpx.Response(200, json={"translations": [{"text": "深度翻译"}]})

    def google_handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-goog-api-key"] == "google-secret"
        return httpx.Response(
            200,
            json={"data": {"translations": [{"translatedText": "A &amp; B"}]}},
        )

    with (
        httpx.Client(transport=httpx.MockTransport(deepl_handle)) as deepl_client,
        httpx.Client(transport=httpx.MockTransport(google_handle)) as google_client,
    ):
        deepl = DeepLProvider(
            settings,
            endpoint="https://api-free.deepl.com/v2/translate",
            api_key="deepl-secret",
            client=deepl_client,
        )
        google = GoogleCloudProvider(
            settings,
            endpoint="https://translation.googleapis.com/language/translate/v2",
            api_key="google-secret",
            client=google_client,
        )
        assert deepl.translate("Original", "zh-CN") == "深度翻译"
        assert google.translate("Original", "en") == "A & B"


def test_fallback_provider_uses_next_provider_and_caches_result(db_factory, settings):
    primary = FakeProvider(settings, fail=True)
    fallback = FakeProvider(settings)
    fallback.name = "google-gtx"
    provider = FallbackProvider([primary, fallback], settings)

    with db_factory() as db:
        assert cached_translate(db, provider, "Original") == "ZH:Original"
        assert cached_translate(db, provider, "Original") == "ZH:Original"
    assert primary.calls == ["Original", "Original"]
    assert fallback.calls == ["Original"]


def test_translation_status_masks_saved_keys(db_factory, settings):
    with db_factory() as db:
        connection = save_llm_connection(
            db,
            name="Translation LLM",
            base_url="https://llm.test/v1",
            model="translator",
            api_key="never-return-this-secret",
            settings=settings,
        )
        configure_translation(
            db,
            enabled=False,
            provider="custom-llm",
            llm_connection_id=connection.id,
            settings=settings,
        )
        status = translation_status(db)
        assert status["provider"] == "custom-llm"
        assert status["llm_api_key_configured"] is True
        assert "never-return-this-secret" not in repr(status)
        connection = db.get(LLMConnection, status["llm_connection_id"])
        assert connection is not None
        assert connection.api_key_encrypted.startswith("fernet:v1:")
        assert "never-return-this-secret" not in connection.api_key_encrypted


def test_fallback_mode_controls_whether_gtx_is_in_the_provider_chain(
    db_factory, settings
):
    with db_factory() as db:
        connection = save_llm_connection(
            db,
            name="Translation LLM",
            base_url="https://llm.test/v1",
            model="translator",
            api_key="secret",
            settings=settings,
        )
        configure_translation(
            db,
            enabled=False,
            provider="custom-llm",
            fallback_mode="automatic",
            llm_connection_id=connection.id,
            settings=settings,
        )
        automatic = build_translation_provider(db, settings)
        assert [provider.name for provider in automatic.providers] == [
            "custom-llm",
            "google-gtx",
        ]

        configure_translation(
            db,
            enabled=False,
            fallback_mode="manual",
            settings=settings,
        )
        manual = build_translation_provider(db, settings)
        assert [provider.name for provider in manual.providers] == ["custom-llm"]
        assert translation_status(db)["fallback_mode"] == "manual"


def test_manual_retry_does_not_reset_completed_translations(db_factory):
    with db_factory() as db:
        rows: list[Translation] = []
        entry_ids: list[int] = []
        for index, status in enumerate(("complete", "failed")):
            work = Work(
                dedup_key=f"url:https://retry.test/{index}",
                canonical_url=f"https://retry.test/{index}",
            )
            db.add(work)
            db.flush()
            entry = Entry(
                work_id=work.id,
                version_key="default",
                title=f"Entry {index}",
                summary="Summary",
                url=work.canonical_url,
                source_hash=str(index) * 64,
            )
            db.add(entry)
            db.flush()
            row = Translation(
                entry_id=entry.id,
                source_hash=entry.source_hash,
                status=status,
            )
            db.add(row)
            rows.append(row)
            entry_ids.append(entry.id)
        db.commit()

        assert queue_retry(db, entry_ids=entry_ids) == 1
        assert db.get(Translation, rows[0].id).status == "complete"
        assert db.get(Translation, rows[1].id).status == "pending"
