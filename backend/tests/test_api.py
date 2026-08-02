from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from backend.app import security
from backend.app.call_logging import write_call_log
from backend.app.config import get_settings
from backend.app.llm import LLMConnectionError
from backend.app.models import (
    AppSetting,
    Brief,
    BriefGenerationCheckpoint,
    Domain,
    Entry,
    EntryDomain,
    EntryFeed,
    EntryTag,
    Feed,
    Job,
    LLMConnection,
    SyncRun,
    Tag,
    Translation,
    Work,
)
from backend.app.opml import MAX_OPML_NESTING


def add_entry(factory, *, title: str = "Reader article") -> int:
    with factory() as db:
        work = Work(
            dedup_key=f"url:https://paper.test/{title}",
            canonical_url=f"https://paper.test/{title}",
        )
        db.add(work)
        db.flush()
        entry = Entry(
            work_id=work.id,
            version_key="default",
            title=title,
            summary="Original abstract",
            url=f"https://paper.test/{title}",
            authors=["Alice"],
            categories=["research"],
            source_hash="a" * 64,
            published_at=datetime(2026, 7, 25, 4),
        )
        db.add(entry)
        db.commit()
        return entry.id


def test_setup_login_session_and_csrf(api_client):
    client, _factory = api_client
    status = client.get("/api/v1/auth/status").json()
    assert status["setup_required"] is True
    assert status["mode"] == "owner"
    setup = client.post("/api/v1/auth/setup", json={"password": "reader88"})
    assert setup.status_code == 201
    token = setup.json()["csrf_token"]
    assert client.get("/api/v1/auth/status").json()["authenticated"] is True
    assert client.post("/api/v1/feeds", json={"url": "https://x.test/rss"}).status_code == 403
    created = client.post(
        "/api/v1/feeds",
        json={"url": "https://x.test/rss", "title": "X"},
        headers={"X-CSRF-Token": token},
    )
    assert created.status_code == 201
    assert client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": token}).status_code == 204
    assert client.get("/api/v1/feeds").status_code == 401
    assert client.post("/api/v1/auth/login", json={"password": "wrongpass"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"password": "reader88"}).status_code == 200


def test_folder_categories_are_persistent_and_manage_feed_assignments(authenticated_client):
    client, _factory, headers = authenticated_client
    assert client.get("/api/v1/feeds/sort-settings").json() == {
        "sort_mode": "alpha",
        "sort_direction": "asc",
    }
    global_sort = client.put(
        "/api/v1/feeds/sort-settings",
        json={"sort_mode": "updated", "sort_direction": "desc"},
        headers=headers,
    )
    assert global_sort.status_code == 200, global_sort.text
    assert client.get("/api/v1/feeds/sort-settings").json() == {
        "sort_mode": "updated",
        "sort_direction": "desc",
    }

    created = client.post(
        "/api/v1/folders",
        json={"name": "  Reading  ", "position": 0},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    folder = created.json()
    assert folder["name"] == "Reading"
    assert folder["feed_count"] == 0
    assert folder["sort_mode"] == "alpha"
    assert folder["sort_direction"] == "asc"

    feed = client.post(
        "/api/v1/feeds",
        json={"url": "https://folders.test/rss", "title": "Folder test", "folder": "Reading"},
        headers=headers,
    ).json()
    second_feed = client.post(
        "/api/v1/feeds",
        json={"url": "https://folders.test/second", "title": "Second", "folder": "Reading"},
        headers=headers,
    ).json()
    listed = client.get("/api/v1/folders").json()["items"]
    assert listed[0]["feed_count"] == 2

    reordered = client.put(
        "/api/v1/feeds/reorder",
        json={"folder": "Reading", "feed_ids": [second_feed["id"], feed["id"]]},
        headers=headers,
    )
    assert reordered.status_code == 204
    assert [
        item["id"] for item in client.get("/api/v1/feeds").json()["items"]
    ] == [second_feed["id"], feed["id"]]
    assert client.put(
        "/api/v1/feeds/reorder",
        json={"folder": "Reading", "feed_ids": [feed["id"]]},
        headers=headers,
    ).status_code == 422

    renamed = client.patch(
        f"/api/v1/folders/{folder['id']}",
        json={"name": "Research"},
        headers=headers,
    )
    assert renamed.status_code == 200, renamed.text
    assert client.get("/api/v1/feeds").json()["items"][0]["folder"] == "Research"

    deleted = client.delete(f"/api/v1/folders/{folder['id']}", headers=headers)
    assert deleted.status_code == 204
    assert client.get("/api/v1/feeds").json()["items"][0]["folder"] is None
    assert client.get("/api/v1/folders").json()["items"] == []

    auto_created = client.patch(
        f"/api/v1/feeds/{feed['id']}",
        json={"folder": "Ad hoc"},
        headers=headers,
    )
    assert auto_created.status_code == 200
    assert [item["name"] for item in client.get("/api/v1/folders").json()["items"]] == ["Ad hoc"]
    assert client.post("/api/v1/folders", json={"name": "   "}, headers=headers).status_code == 422


def test_login_failures_are_rate_limited_per_client(api_client):
    client, _factory = api_client
    with security._login_attempt_lock:
        security._login_attempts.clear()
    try:
        setup = client.post("/api/v1/auth/setup", json={"password": "reader88"})
        client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": setup.json()["csrf_token"]},
        )
        for _attempt in range(security._LOGIN_MAX_FAILURES):
            assert client.post(
                "/api/v1/auth/login", json={"password": "wrongpass"}
            ).status_code == 401
        limited = client.post("/api/v1/auth/login", json={"password": "reader88"})
        assert limited.status_code == 429
        assert int(limited.headers["Retry-After"]) > 0
    finally:
        with security._login_attempt_lock:
            security._login_attempts.clear()


def test_entry_states_search_tags_and_translation(authenticated_client):
    client, factory, headers = authenticated_client
    entry_id = add_entry(factory)
    assert client.patch(
        f"/api/v1/entries/{entry_id}/state", json={"read": True}
    ).status_code == 403
    updated = client.patch(
        f"/api/v1/entries/{entry_id}/state",
        json={"read": True, "starred": True},
        headers=headers,
    )
    assert updated.status_code == 200
    with factory() as db:
        tag = Tag(name="research")
        db.add(tag)
        db.flush()
        db.add(EntryTag(entry_id=entry_id, tag_id=tag.id))
        db.add(
            Translation(
                entry_id=entry_id,
                source_hash="a" * 64,
                language="zh-CN",
                provider="google-gtx",
                title="Translated article",
                summary="Translated summary",
                status="complete",
            )
        )
        db.commit()
    assert client.get(
        "/api/v1/entries", params={"q": "Translated summary"}
    ).json()["total"] == 1
    assert client.get("/api/v1/entries", params={"q": "research"}).json()["total"] == 1
    assert client.get("/api/v1/entries", params={"view": "starred"}).json()["total"] == 1


def test_translation_status_separates_queued_running_and_failed(
    authenticated_client,
):
    client, factory, _headers = authenticated_client
    entry_ids = [
        add_entry(factory, title=f"Translation state {status}")
        for status in ("pending", "running", "complete", "failed")
    ]
    with factory() as db:
        for entry_id, status in zip(
            entry_ids,
            ("pending", "running", "complete", "failed"),
            strict=True,
        ):
            db.add(
                Translation(
                    entry_id=entry_id,
                    source_hash="a" * 64,
                    language="zh-CN",
                    provider="translation-chain",
                    status=status,
                    last_error="provider unavailable" if status == "failed" else None,
                )
            )
        db.commit()

    payload = client.get("/api/v1/translations/status").json()
    assert payload["pending_count"] == 1
    assert payload["running_count"] == 1
    assert payload["completed_count"] == 1
    assert payload["failed_count"] == 1


def test_bulk_state(authenticated_client):
    client, factory, headers = authenticated_client
    ids = [add_entry(factory, title=f"Paper {index}") for index in range(2)]
    response = client.post(
        "/api/v1/entries/bulk-state",
        json={"entry_ids": ids, "state": {"later": True}},
        headers=headers,
    )
    assert response.status_code == 204
    assert client.get("/api/v1/entries", params={"view": "later"}).json()["total"] == 2


def test_mark_all_read_respects_current_timeline_filters(authenticated_client):
    client, factory, headers = authenticated_client
    first_feed = client.post(
        "/api/v1/feeds",
        json={"url": "https://first.test/rss", "title": "First feed"},
        headers=headers,
    ).json()
    second_feed = client.post(
        "/api/v1/feeds",
        json={"url": "https://second.test/rss", "title": "Second feed"},
        headers=headers,
    ).json()
    alpha = add_entry(factory, title="Alpha paper")
    beta = add_entry(factory, title="Beta paper")
    gamma = add_entry(factory, title="Gamma paper")
    with factory() as db:
        db.add_all(
            [
                EntryFeed(entry_id=alpha, feed_id=first_feed["id"]),
                EntryFeed(entry_id=beta, feed_id=first_feed["id"]),
                EntryFeed(entry_id=gamma, feed_id=second_feed["id"]),
            ]
        )
        db.commit()

    response = client.post(
        "/api/v1/entries/mark-all-read",
        params={"view": "unread", "feed_id": first_feed["id"], "q": "Alpha"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json() == {"updated": 1}
    assert client.get(
        "/api/v1/entries",
        params={"view": "unread", "feed_id": first_feed["id"]},
    ).json()["total"] == 1
    assert client.get(
        "/api/v1/entries",
        params={"view": "unread", "feed_id": second_feed["id"]},
    ).json()["total"] == 1


def test_domain_spaces_any_and_all_filters(authenticated_client):
    client, factory, headers = authenticated_client
    physics = client.post(
        "/api/v1/domains",
        json={"name": "Physics", "color": "#635BFF"},
        headers=headers,
    ).json()
    computing = client.post(
        "/api/v1/domains",
        json={"name": "Computing", "color": "#0EA5E9"},
        headers=headers,
    ).json()
    feed = client.post(
        "/api/v1/feeds",
        json={
            "url": "https://cross.test/rss",
            "title": "Cross-domain feed",
            "domain_ids": [physics["id"], computing["id"]],
        },
        headers=headers,
    ).json()
    inherited = add_entry(factory, title="Inherited cross-domain entry")
    direct = add_entry(factory, title="Direct domain entry")
    with factory() as db:
        db.add(EntryFeed(entry_id=inherited, feed_id=feed["id"]))
        db.add(EntryDomain(entry_id=direct, domain_id=physics["id"]))
        db.commit()
    any_view = client.get(
        "/api/v1/entries",
        params=[
            ("domain_ids", physics["id"]),
            ("domain_ids", computing["id"]),
            ("domain_match", "any"),
        ],
    )
    assert {item["id"] for item in any_view.json()["items"]} == {inherited, direct}
    all_view = client.get(
        "/api/v1/entries",
        params=[
            ("domain_ids", physics["id"]),
            ("domain_ids", computing["id"]),
            ("domain_match", "all"),
        ],
    )
    assert [item["id"] for item in all_view.json()["items"]] == [inherited]


def test_opml_round_trip_preserves_domains_and_skips_duplicates(authenticated_client):
    client, _factory, headers = authenticated_client
    domain = client.post(
        "/api/v1/domains", json={"name": "Science"}, headers=headers
    ).json()
    for index in range(2):
        response = client.post(
            "/api/v1/feeds",
            json={
                "url": f"https://feed{index}.test/rss",
                "title": f"F{index}",
                "folder": "Reading",
                "domain_ids": [domain["id"]],
            },
            headers=headers,
        )
        assert response.status_code == 201
    exported = client.get("/api/v1/feeds/opml")
    assert exported.status_code == 200
    assert exported.text.count('text="Reading"') == 1
    assert 'affogatoRssReaderDomains="Science"' in exported.text
    duplicate = b"""<?xml version="1.0"?><opml version="2.0"><body>
      <outline text="A" xmlUrl="https://new.test/rss" affogatoRssReaderDomains="News"/>
      <outline text="A duplicate" xmlUrl="https://new.test/rss"/>
    </body></opml>"""
    imported = client.post(
        "/api/v1/feeds/opml",
        files={"file": ("feeds.opml", duplicate, "text/x-opml")},
        headers=headers,
    )
    assert imported.json() == {"imported": 1, "skipped": 1}
    assert any(item["name"] == "News" for item in client.get("/api/v1/domains").json()["items"])


def test_opml_import_accepts_normal_nested_document(authenticated_client):
    client, _factory, headers = authenticated_client
    payload = b"""<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline text="Reading">
        <outline text="Example" xmlUrl="https://normal.test/rss"/>
      </outline>
    </body></opml>"""

    response = client.post(
        "/api/v1/feeds/opml",
        files={"file": ("normal.opml", payload, "text/x-opml")},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {"imported": 1, "skipped": 0}
    feeds = client.get("/api/v1/feeds").json()["items"]
    assert [(feed["title"], feed["folder"]) for feed in feeds] == [
        ("Example", "Reading")
    ]


def test_opml_import_rejects_entity_expansion(authenticated_client):
    client, _factory, headers = authenticated_client
    payload = b"""<?xml version="1.0"?>
    <!DOCTYPE opml [
      <!ENTITY value "entity-content">
      <!ENTITY expanded "&value;&value;&value;&value;">
    ]>
    <opml version="2.0"><body>
      <outline text="&expanded;" xmlUrl="https://entity.test/rss"/>
    </body></opml>"""

    response = client.post(
        "/api/v1/feeds/opml",
        files={"file": ("entities.opml", payload, "text/x-opml")},
        headers=headers,
    )

    assert response.status_code == 422
    assert "Invalid OPML document" in response.json()["detail"]
    assert client.get("/api/v1/feeds").json()["items"] == []


def test_opml_import_rejects_external_entities(authenticated_client):
    client, _factory, headers = authenticated_client
    payload = b"""<?xml version="1.0"?>
    <!DOCTYPE opml [<!ENTITY external SYSTEM "file:///etc/passwd">]>
    <opml version="2.0"><body>
      <outline text="&external;" xmlUrl="https://external.test/rss"/>
    </body></opml>"""

    response = client.post(
        "/api/v1/feeds/opml",
        files={"file": ("external.opml", payload, "text/x-opml")},
        headers=headers,
    )

    assert response.status_code == 422
    assert "Invalid OPML document" in response.json()["detail"]
    assert client.get("/api/v1/feeds").json()["items"] == []


def test_opml_import_rejects_excessive_nesting(authenticated_client):
    client, _factory, headers = authenticated_client
    opening = "".join(
        f'<outline text="Folder {depth}">' for depth in range(MAX_OPML_NESTING + 1)
    )
    closing = "</outline>" * (MAX_OPML_NESTING + 1)
    payload = (
        f'<?xml version="1.0"?><opml version="2.0"><body>{opening}'
        f'<outline text="Feed" xmlUrl="https://deep.test/rss"/>{closing}'
        "</body></opml>"
    ).encode()

    response = client.post(
        "/api/v1/feeds/opml",
        files={"file": ("deep.opml", payload, "text/x-opml")},
        headers=headers,
    )

    assert response.status_code == 422
    assert "nesting exceeds" in response.json()["detail"]
    assert client.get("/api/v1/feeds").json()["items"] == []


def test_openapi_declares_public_contracts(api_client):
    client, _factory = api_client
    document = client.get("/openapi.json").json()
    contracts = {
        ("/api/v1/auth/status", "get", "200"): "AuthStatus",
        ("/api/v1/entries", "get", "200"): "EntriesPage",
        ("/api/v1/feeds", "get", "200"): "FeedListOut",
        ("/api/v1/domains", "get", "200"): "DomainListOut",
        ("/api/v1/briefs", "get", "200"): "BriefListOut",
        ("/api/v1/briefs", "post", "201"): "BriefOut",
        ("/api/v1/briefs/{brief_id}", "get", "200"): "BriefDetailOut",
        ("/api/v1/brief-schedules", "get", "200"): "BriefScheduleListOut",
        ("/api/v1/settings", "get", "200"): "AppSettingsOut",
        ("/api/v1/updates/status", "get", "200"): "UpdateStatusOut",
    }
    for (path, method, status_code), model in contracts.items():
        schema = document["paths"][path][method]["responses"][status_code][
            "content"
        ]["application/json"]["schema"]
        assert schema == {"$ref": f"#/components/schemas/{model}"}
    assert set(
        document["paths"]["/api/v1/briefs/{brief_id}/export"]["get"]["responses"][
            "200"
        ]["content"]
    ) == {"text/markdown"}


def test_llm_connection_can_be_tested_without_saving_key(
    authenticated_client, monkeypatch
):
    client, factory, headers = authenticated_client
    missing = client.post(
        "/api/v1/llm/connections/test",
        json={
            "base_url": "https://llm.example.test/v1",
            "model": "general-model",
        },
        headers=headers,
    )
    assert missing.status_code == 400
    assert "API key" in missing.json()["detail"]

    monkeypatch.setattr(
        "backend.app.api.probe_llm_connection",
        lambda **kwargs: "OK",
    )
    tested = client.post(
        "/api/v1/llm/connections/test",
        json={
            "base_url": "https://llm.example.test/v1",
            "model": "general-model",
            "api_key": "temporary-unsaved-key",
        },
        headers=headers,
    )
    assert tested.status_code == 200
    assert tested.json()["model"] == "general-model"
    assert tested.json()["response_text"] == "OK"
    assert tested.json()["elapsed_ms"] >= 0
    with factory() as db:
        assert db.get(AppSetting, "translation_llm_api_key") is None
        assert db.scalar(select(func.count()).select_from(LLMConnection)) == 0


def test_jobs_translation_settings_and_brief_contract(
    authenticated_client, monkeypatch
):
    monkeypatch.setattr(
        "backend.app.briefs.complete_feature_chat",
        lambda *args, **kwargs: "## 今日概览\n\n接口测试综合总结。",
    )
    client, factory, headers = authenticated_client
    with factory() as db:
        feed = Feed(title="Contract feed", url="https://contract.test/rss")
        job = Job(kind="translation", status="queued", payload={"limit": 10}, result={})
        db.add_all([feed, job])
        db.flush()
        db.add(
            SyncRun(
                feed_id=feed.id,
                status="success",
                http_status=200,
                fetched_count=3,
                created_count=2,
                updated_count=1,
                finished_at=datetime(2026, 7, 26, 8),
            )
        )
        db.commit()
    translation = client.get("/api/v1/translations/status")
    assert translation.status_code == 200
    assert translation.json()["target_language"] == "zh-CN"
    assert translation.json()["pending_count"] == 0
    assert translation.json()["running_count"] == 0
    toggled = client.patch(
        "/api/v1/translations/status",
        json={"enabled": True, "target_language": "de"},
        headers=headers,
    )
    assert toggled.json()["target_language"] == "de"
    created_connection = client.post(
        "/api/v1/llm/connections",
        json={
            "name": "General LLM",
            "base_url": "https://llm.example.test/v1",
            "model": "translation-model",
            "api_key": "api-secret-must-not-be-returned",
        },
        headers=headers,
    )
    assert created_connection.status_code == 201
    configured = client.patch(
        "/api/v1/translations/status",
        json={
            "enabled": True,
            "target_language": "de",
            "provider": "custom-llm",
            "fallback_mode": "manual",
            "llm_connection_id": created_connection.json()["id"],
        },
        headers=headers,
    )
    assert configured.status_code == 200
    assert configured.json()["provider"] == "custom-llm"
    assert configured.json()["fallback_mode"] == "manual"
    assert configured.json()["llm_api_key_configured"] is True
    assert "api-secret-must-not-be-returned" not in configured.text
    assert configured.json()["fallback_provider"] == "google-gtx"
    connections = client.get("/api/v1/llm/connections")
    assert connections.status_code == 200
    assert len(connections.json()) == 1
    assert connections.json()[0]["used_by"] == ["translation"]
    assert connections.json()[0]["api_key_hint"] == "****rned"
    assert "api-secret-must-not-be-returned" not in connections.text
    blocked_delete = client.delete(
        f"/api/v1/llm/connections/{created_connection.json()['id']}",
        headers=headers,
    )
    assert blocked_delete.status_code == 409
    detached = client.patch(
        "/api/v1/translations/status",
        json={
            "enabled": True,
            "target_language": "de",
            "provider": "google-gtx",
            "fallback_mode": "manual",
        },
        headers=headers,
    )
    assert detached.status_code == 200
    assert detached.json()["llm_connection_id"] is None
    deleted = client.delete(
        f"/api/v1/llm/connections/{created_connection.json()['id']}",
        headers=headers,
    )
    assert deleted.status_code == 204
    assert client.get("/api/v1/llm/connections").json() == []
    assert client.get("/api/v1/jobs").json()["items"][0]["payload"] == {"limit": 10}
    assert client.get("/api/v1/jobs/sync-runs").json()["items"][0]["inserted_count"] == 2
    write_call_log(
        category="translation",
        operation="translate",
        feature="translation",
        provider="google-gtx",
        target_language="de",
        status="success",
        duration_ms=25,
        input_chars=10,
        output_chars=12,
        settings=client.app.dependency_overrides[get_settings](),
    )
    call_logs = client.get("/api/v1/call-logs", params={"category": "translation"})
    assert call_logs.status_code == 200
    assert call_logs.json()["items"][0]["provider"] == "google-gtx"
    assert call_logs.json()["host_path_hint"] == "logs/llm-translation.jsonl"

    brief = client.post(
        "/api/v1/briefs",
        json={
            "period": "daily",
            "at": "2026-07-26T15:00:00+08:00",
            "idempotency_key": "api-contract-brief",
        },
        headers=headers,
    )
    assert brief.status_code == 201
    progress = client.get(
        "/api/v1/briefs/generation-progress/api-contract-brief"
    )
    assert progress.status_code == 200
    assert progress.json() == {
        "idempotency_key": "api-contract-brief",
        "status": "completed",
        "stage": "finalizing",
        "completed": 1,
        "total": 1,
        "brief_id": brief.json()["id"],
        "message": None,
        "can_retry": False,
        "attempt": 1,
    }
    repeated = client.post(
        "/api/v1/briefs",
        json={
            "period": "daily",
            "at": "2026-07-26T15:00:00+08:00",
            "idempotency_key": "api-contract-brief",
        },
        headers=headers,
    )
    assert repeated.json()["id"] == brief.json()["id"]
    detail = client.get(f"/api/v1/briefs/{brief.json()['id']}")
    assert "markdown" in detail.json()
    deleted = client.delete(
        f"/api/v1/briefs/{brief.json()['id']}", headers=headers
    )
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/briefs/{brief.json()['id']}").status_code == 404


def test_brief_configuration_uses_saved_llm_and_exports_only_summary(
    authenticated_client, monkeypatch
):
    client, factory, headers = authenticated_client
    connection = client.post(
        "/api/v1/llm/connections",
        json={
            "name": "Brief LLM",
            "base_url": "https://llm.example.test/v1",
            "model": "summary-model",
            "api_key": "brief-secret",
        },
        headers=headers,
    ).json()
    initial = client.get("/api/v1/briefs/configuration")
    assert initial.json()["configured"] is False
    configured = client.patch(
        "/api/v1/briefs/configuration",
        json={"llm_connection_id": connection["id"]},
        headers=headers,
    )
    assert configured.status_code == 200
    assert configured.json() == {
        "llm_connection_id": connection["id"],
        "llm_connection_name": "Brief LLM",
        "model": "summary-model",
        "configured": True,
    }
    assert client.get("/api/v1/llm/connections").json()[0]["used_by"] == ["brief"]

    default_rule = client.get("/api/v1/briefs/rule")
    assert default_rule.status_code == 200
    assert default_rule.json()["is_custom"] is False
    custom_rule = client.patch(
        "/api/v1/briefs/rule",
        json={"content": "# 自定义规则\n\n只输出跨来源趋势。"},
        headers=headers,
    )
    assert custom_rule.json()["is_custom"] is True

    def summarize_with_custom_rule(*args, **kwargs):
        assert "# 自定义规则" in kwargs["user_prompt"]
        assert kwargs["timeout_seconds"] == 30
        return "## 今日概览\n\n这是模型生成的综合总结。"

    monkeypatch.setattr(
        "backend.app.briefs.complete_feature_chat",
        summarize_with_custom_rule,
    )
    generated = client.post(
        "/api/v1/briefs",
        json={
            "period": "daily",
            "start_at": "2026-07-26T00:00:00+08:00",
            "end_at": "2026-07-26T15:00:00+08:00",
            "idempotency_key": "configured-brief-test",
        },
        headers=headers,
    )
    assert generated.status_code == 201
    assert generated.json()["notes"].startswith("## 今日概览")
    exported = client.get(
        f"/api/v1/briefs/{generated.json()['id']}/export"
    )
    assert "这是模型生成的综合总结" in exported.text
    assert "## Entries" not in exported.text
    assert "Window:" not in exported.text
    with factory() as db:
        assert db.get(Brief, generated.json()["id"]).notes
    restored = client.delete("/api/v1/briefs/rule", headers=headers)
    assert restored.json()["is_custom"] is False


def test_failed_brief_generation_retries_from_durable_checkpoint(
    authenticated_client, monkeypatch
):
    client, factory, headers = authenticated_client
    calls = 0

    def resumable_generation(db, period, **kwargs):
        nonlocal calls
        calls += 1
        load_checkpoint = kwargs["checkpoint_loader"]
        save_checkpoint = kwargs["checkpoint_saver"]
        report_progress = kwargs["progress_callback"]
        if calls == 1:
            save_checkpoint("batch", "saved-batch", "completed observation")
            report_progress("summarizing_batches", 1, 2, None)
            raise LLMConnectionError("temporary 503", retryable=True)
        assert load_checkpoint("batch", "saved-batch") == "completed observation"
        brief = Brief(
            period=period,
            start_at=datetime(2026, 7, 27, 0),
            end_at=datetime(2026, 7, 28, 0),
            title="Resumed brief",
            notes="## Resumed\n\nOnly the missing batch was called.",
            stats={"entries": 2, "analyzed_entries": 2},
            filters={},
            idempotency_key=kwargs["idempotency_key"],
        )
        db.add(brief)
        db.commit()
        db.refresh(brief)
        return brief

    monkeypatch.setattr(
        "backend.app.api.create_manual_brief",
        resumable_generation,
    )
    failed = client.post(
        "/api/v1/briefs",
        json={
            "period": "daily",
            "start_at": "2026-07-27T00:00:00Z",
            "end_at": "2026-07-28T00:00:00Z",
            "idempotency_key": "resumable-api-brief",
        },
        headers=headers,
    )
    assert failed.status_code == 502
    progress = client.get(
        "/api/v1/briefs/generation-progress/resumable-api-brief"
    ).json()
    assert progress["status"] == "failed"
    assert progress["completed"] == 1
    assert progress["can_retry"] is True
    assert progress["attempt"] == 1
    assert client.get(
        "/api/v1/briefs/generation-progress/latest?period=daily"
    ).json()["idempotency_key"] == "resumable-api-brief"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(BriefGenerationCheckpoint)) == 1

    resumed = client.post(
        "/api/v1/briefs/generation-progress/resumable-api-brief/retry",
        headers=headers,
    )
    assert resumed.status_code == 200
    assert resumed.json()["title"] == "Resumed brief"
    assert calls == 2
    completed = client.get(
        "/api/v1/briefs/generation-progress/resumable-api-brief"
    ).json()
    assert completed["status"] == "completed"
    assert completed["attempt"] == 2
    assert completed["can_retry"] is False
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(BriefGenerationCheckpoint)) == 0


def test_uncategorized_filter_and_feed_url_reset(authenticated_client):
    client, factory, headers = authenticated_client
    uncategorized = client.post(
        "/api/v1/feeds",
        json={"url": "https://uncategorized.test/rss", "title": "Uncategorized"},
        headers=headers,
    ).json()
    categorized = client.post(
        "/api/v1/feeds",
        json={"url": "https://categorized.test/rss", "title": "Categorized", "folder": "Reading"},
        headers=headers,
    ).json()
    uncategorized_entry = add_entry(factory, title="Uncategorized paper")
    categorized_entry = add_entry(factory, title="Categorized paper")
    with factory() as db:
        db.add_all(
            [
                EntryFeed(entry_id=uncategorized_entry, feed_id=uncategorized["id"]),
                EntryFeed(entry_id=categorized_entry, feed_id=categorized["id"]),
            ]
        )
        feed = db.get(Feed, uncategorized["id"])
        feed.etag = '"old"'
        feed.last_modified = "Sat, 25 Jul 2026 00:00:00 GMT"
        feed.next_fetch_at = datetime(2026, 7, 27)
        feed.error_count = 4
        feed.last_error = "old failure"
        db.commit()
    filtered = client.get("/api/v1/entries", params={"folder": "__uncategorized__"})
    assert [item["id"] for item in filtered.json()["items"]] == [uncategorized_entry]
    updated = client.patch(
        f"/api/v1/feeds/{uncategorized['id']}",
        json={"url": "https://replacement.test/rss", "title": "  Replacement  "},
        headers=headers,
    )
    assert updated.json()["title"] == "Replacement"
    assert updated.json()["next_fetch_at"] is None
    for invalid_patch in ({"title": None}, {"title": "   "}, {"url": None}):
        assert client.patch(
            f"/api/v1/feeds/{uncategorized['id']}",
            json=invalid_patch,
            headers=headers,
        ).status_code == 422


def test_existing_feed_categories_and_domain_names_can_be_managed(authenticated_client):
    client, _factory, headers = authenticated_client
    physics = client.post(
        "/api/v1/domains",
        json={"name": "Physics"},
        headers=headers,
    ).json()
    computing = client.post(
        "/api/v1/domains",
        json={"name": "Computing"},
        headers=headers,
    ).json()
    feed = client.post(
        "/api/v1/feeds",
        json={
            "url": "https://classification.test/rss",
            "title": "Classification test",
            "folder": "Lab",
            "domain_ids": [physics["id"]],
        },
        headers=headers,
    ).json()

    updated = client.patch(
        f"/api/v1/feeds/{feed['id']}",
        json={"folder": "Research", "domain_ids": [physics["id"], computing["id"]]},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["folder"] == "Research"
    assert {item["name"] for item in updated.json()["domains"]} == {"Physics", "Computing"}

    renamed = client.patch(
        f"/api/v1/domains/{physics['id']}",
        json={"name": "  Physical sciences  ", "color": "#123456"},
        headers=headers,
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Physical sciences"
    assert renamed.json()["color"] == "#123456"
    assert client.patch(
        f"/api/v1/domains/{physics['id']}",
        json={"name": "   "},
        headers=headers,
    ).status_code == 422
    assert client.post(
        "/api/v1/domains",
        json={"name": "   "},
        headers=headers,
    ).status_code == 422

    uncategorized = client.patch(
        f"/api/v1/feeds/{feed['id']}",
        json={"folder": None, "domain_ids": []},
        headers=headers,
    )
    assert uncategorized.status_code == 200
    assert uncategorized.json()["folder"] is None
    assert uncategorized.json()["domains"] == []


def test_domains_can_be_associated_with_multiple_feeds_at_once(authenticated_client):
    client, _factory, headers = authenticated_client
    physics = client.post(
        "/api/v1/domains",
        json={"name": "Physics"},
        headers=headers,
    ).json()
    computing = client.post(
        "/api/v1/domains",
        json={"name": "Computing"},
        headers=headers,
    ).json()
    first = client.post(
        "/api/v1/feeds",
        json={
            "url": "https://bulk-domain-first.test/rss",
            "title": "First feed",
            "domain_ids": [physics["id"]],
        },
        headers=headers,
    ).json()
    second = client.post(
        "/api/v1/feeds",
        json={
            "url": "https://bulk-domain-second.test/rss",
            "title": "Second feed",
        },
        headers=headers,
    ).json()

    associated = client.post(
        "/api/v1/feeds/associate-domains",
        json={
            "feed_ids": [first["id"], second["id"]],
            "domain_ids": [computing["id"]],
        },
        headers=headers,
    )
    assert associated.status_code == 200, associated.text
    assert associated.json() == {"feeds_updated": 2, "associations_added": 2}

    by_id = {
        item["id"]: {domain["name"] for domain in item["domains"]}
        for item in client.get("/api/v1/feeds").json()["items"]
    }
    assert by_id[first["id"]] == {"Physics", "Computing"}
    assert by_id[second["id"]] == {"Computing"}

    repeated = client.post(
        "/api/v1/feeds/associate-domains",
        json={
            "feed_ids": [first["id"], second["id"]],
            "domain_ids": [computing["id"]],
        },
        headers=headers,
    )
    assert repeated.json() == {"feeds_updated": 2, "associations_added": 0}
    assert client.post(
        "/api/v1/feeds/associate-domains",
        json={"feed_ids": [first["id"], 999999], "domain_ids": [physics["id"]]},
        headers=headers,
    ).status_code == 404
