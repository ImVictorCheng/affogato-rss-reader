from __future__ import annotations

import httpx
import pytest

from backend.app.llm import save_llm_connection
from backend.app.models import Feed, NetworkProxyConfig
from backend.app.network_proxy import (
    _resolve_test_proxy_url,
    http_route_for_feed,
    http_route_for_global,
    http_route_for_llm_connection,
    http_route_for_mode,
    http_route_for_translation_service,
    network_proxy_summary,
    save_network_proxy_config,
    test_custom_proxy as run_custom_proxy_test,
    test_custom_proxy_targets as run_custom_proxy_targets_test,
    validate_proxy_url,
)
from backend.app.secrets import ENCRYPTED_PREFIX


def test_proxy_configuration_encrypts_password_and_routes_only_selected_targets(
    db_factory,
    settings,
):
    with db_factory() as db:
        selected_feed = Feed(title="Selected", url="https://selected.test/rss")
        direct_feed = Feed(title="Direct", url="https://direct.test/rss")
        db.add_all([selected_feed, direct_feed])
        db.flush()
        selected_llm = save_llm_connection(
            db,
            name="Selected LLM",
            base_url="https://llm.test/v1",
            model="model-a",
            api_key="llm-secret",
            settings=settings,
        )
        direct_llm = save_llm_connection(
            db,
            name="Direct LLM",
            base_url="https://direct-llm.test/v1",
            model="model-b",
            api_key="llm-secret",
            settings=settings,
        )
        save_network_proxy_config(
            db,
            enabled=True,
            url="socks5://127.0.0.1:1080",
            username="reader@example.com",
            password="p@ss:/word",
            clear_password=False,
            global_mode="custom",
            feed_modes={selected_feed.id: "custom", direct_feed.id: "direct"},
            llm_connection_modes={
                selected_llm.id: "custom",
                direct_llm.id: "system",
            },
            translation_service_modes={
                "google-gtx": "custom",
                "deepl": "system",
                "google-cloud": "direct",
            },
            settings=settings,
        )
        db.commit()

        stored = db.get(NetworkProxyConfig, 1)
        assert stored is not None
        assert stored.password_encrypted.startswith(ENCRYPTED_PREFIX)
        assert "p@ss:/word" not in stored.password_encrypted
        summary = network_proxy_summary(db)
        assert summary["feed_modes"] == {
            selected_feed.id: "custom",
            direct_feed.id: "direct",
        }
        assert summary["llm_connection_modes"] == {
            selected_llm.id: "custom",
            direct_llm.id: "system",
        }
        assert summary["translation_service_modes"] == {
            "google-gtx": "custom",
            "deepl": "system",
            "google-cloud": "direct",
        }
        assert summary["global_mode"] == "custom"
        assert "password" not in summary
        assert summary["password_hint"] == "****word"
        assert http_route_for_global(db, settings).proxy is not None
        assert (
            http_route_for_feed(db, selected_feed, settings).proxy
            == "socks5://reader%40example.com:p%40ss%3A%2Fword@127.0.0.1:1080"
        )
        direct_route = http_route_for_feed(db, direct_feed, settings)
        assert direct_route.proxy is None
        assert direct_route.trust_env is False
        assert http_route_for_llm_connection(db, selected_llm, settings).proxy is not None
        system_route = http_route_for_llm_connection(db, direct_llm, settings)
        assert system_route.proxy is None
        assert system_route.trust_env is True
        assert (
            http_route_for_translation_service(db, "google-gtx", settings).proxy
            is not None
        )
        assert http_route_for_translation_service(
            db, "deepl", settings
        ).trust_env is True
        google_cloud_route = http_route_for_translation_service(
            db, "google-cloud", settings
        )
        assert google_cloud_route.proxy is None
        assert google_cloud_route.trust_env is False


def test_proxy_url_rejects_embedded_credentials_and_unsupported_schemes():
    for value in (
        "http://user:password@127.0.0.1:7890",
        "ftp://127.0.0.1:21",
        "http://127.0.0.1:7890/path",
    ):
        try:
            validate_proxy_url(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{value} should have been rejected")


def test_custom_proxy_routes_fail_closed_when_configuration_is_unavailable(
    db_factory, settings
):
    with db_factory() as db:
        with pytest.raises(ValueError, match="custom proxy is not enabled"):
            http_route_for_mode(db, "custom", settings)

        feed = Feed(
            title="Fail-closed feed",
            url="https://feed.test/rss",
            proxy_mode="custom",
        )
        connection = save_llm_connection(
            db,
            name="Fail-closed LLM",
            base_url="https://llm.test/v1",
            model="model",
            api_key="secret",
            settings=settings,
        )
        connection.proxy_mode = "custom"
        db.add(feed)
        db.commit()
        with pytest.raises(ValueError, match="custom proxy is not enabled"):
            http_route_for_feed(db, feed, settings)
        with pytest.raises(ValueError, match="custom proxy is not enabled"):
            http_route_for_llm_connection(db, connection, settings)

        save_network_proxy_config(
            db,
            enabled=False,
            url="http://disabled-proxy.test:7890",
            username=None,
            password=None,
            clear_password=False,
            global_mode="custom",
            feed_modes={feed.id: "custom"},
            llm_connection_modes={connection.id: "custom"},
            translation_service_modes={
                "google-gtx": "custom",
                "deepl": "custom",
                "google-cloud": "custom",
            },
            settings=settings,
        )
        db.commit()
        for route in (
            lambda: http_route_for_global(db, settings),
            lambda: http_route_for_feed(db, feed, settings),
            lambda: http_route_for_llm_connection(db, connection, settings),
            lambda: http_route_for_translation_service(db, "deepl", settings),
        ):
            with pytest.raises(ValueError, match="custom proxy is not enabled"):
                route()


def test_saved_proxy_password_is_bound_to_url_and_username(db_factory, settings):
    common = {
        "enabled": True,
        "global_mode": "direct",
        "feed_modes": {},
        "llm_connection_modes": {},
        "translation_service_modes": {},
        "settings": settings,
    }
    with db_factory() as db:
        save_network_proxy_config(
            db,
            url="http://saved-proxy.test:7890",
            username="saved-user",
            password="saved-password",
            clear_password=False,
            **common,
        )
        db.commit()

        same_target = _resolve_test_proxy_url(
            db,
            url="http://saved-proxy.test:7890",
            username="saved-user",
            password=None,
            use_saved_password=True,
            settings=settings,
        )
        assert same_target == (
            "http://saved-user:saved-password@saved-proxy.test:7890"
        )
        with pytest.raises(ValueError, match="different proxy URL or username"):
            _resolve_test_proxy_url(
                db,
                url="http://attacker.test:7890",
                username="saved-user",
                password=None,
                use_saved_password=True,
                settings=settings,
            )
        with pytest.raises(ValueError, match="different proxy URL or username"):
            _resolve_test_proxy_url(
                db,
                url="http://saved-proxy.test:7890",
                username="attacker-user",
                password=None,
                use_saved_password=True,
                settings=settings,
            )
        explicit_draft = _resolve_test_proxy_url(
            db,
            url="http://new-proxy.test:7890",
            username="new-user",
            password="new-password",
            use_saved_password=True,
            settings=settings,
        )
        assert explicit_draft == "http://new-user:new-password@new-proxy.test:7890"

        with pytest.raises(ValueError, match="requires a new password"):
            save_network_proxy_config(
                db,
                url="http://new-proxy.test:7890",
                username="new-user",
                password=None,
                clear_password=False,
                **common,
            )
        db.rollback()
        stored = db.get(NetworkProxyConfig, 1)
        assert stored is not None
        assert stored.url == "http://saved-proxy.test:7890"
        assert stored.username == "saved-user"
        assert stored.password_encrypted

        save_network_proxy_config(
            db,
            url="http://cleared-proxy.test:7890",
            username="cleared-user",
            password=None,
            clear_password=True,
            **common,
        )
        db.commit()
        stored = db.get(NetworkProxyConfig, 1)
        assert stored is not None
        assert stored.url == "http://cleared-proxy.test:7890"
        assert stored.password_encrypted is None

        save_network_proxy_config(
            db,
            url="http://cleared-proxy.test:7890",
            username="cleared-user",
            password="rebound-password",
            clear_password=False,
            **common,
        )
        db.commit()
        save_network_proxy_config(
            db,
            url="http://replacement-proxy.test:7890",
            username="replacement-user",
            password="replacement-password",
            clear_password=False,
            **common,
        )
        db.commit()
        rebound = _resolve_test_proxy_url(
            db,
            url="http://replacement-proxy.test:7890",
            username="replacement-user",
            password=None,
            use_saved_password=True,
            settings=settings,
        )
        assert rebound == (
            "http://replacement-user:replacement-password@replacement-proxy.test:7890"
        )


def test_network_proxy_api_does_not_return_password(
    authenticated_client,
    settings,
    monkeypatch,
):
    client, factory, headers = authenticated_client
    with factory() as db:
        feed = Feed(title="Proxy feed", url="https://proxy-feed.test/rss")
        db.add(feed)
        connection = save_llm_connection(
            db,
            name="Proxy LLM",
            base_url="https://proxy-llm.test/v1",
            model="proxy-model",
            api_key="llm-key",
            settings=settings,
        )
        db.commit()
        feed_id = feed.id
        connection_id = connection.id

    response = client.patch(
        "/api/v1/network-proxy",
        json={
            "enabled": True,
            "url": "http://127.0.0.1:7890",
            "username": "proxy-user",
            "password": "proxy-api-secret",
            "global_mode": "system",
            "feed_modes": {str(feed_id): "custom"},
            "llm_connection_modes": {str(connection_id): "system"},
            "translation_service_modes": {
                "google-gtx": "custom",
                "deepl": "system",
                "google-cloud": "direct",
            },
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["password_configured"] is True
    assert response.json()["global_mode"] == "system"
    assert response.json()["feed_modes"] == {str(feed_id): "custom"}
    assert response.json()["llm_connection_modes"] == {
        str(connection_id): "system"
    }
    assert response.json()["translation_service_modes"] == {
        "google-gtx": "custom",
        "deepl": "system",
        "google-cloud": "direct",
    }
    assert "proxy-api-secret" not in response.text
    assert "proxy-api-secret" not in client.get("/api/v1/network-proxy").text

    monkeypatch.setattr(
        "backend.app.api.test_custom_proxy_targets",
        lambda *args, **kwargs: {
            "results": [
                {
                    "target_url": "https://google.com/",
                    "ok": True,
                    "status_code": 200,
                    "elapsed_ms": 12,
                    "final_url": "https://www.google.com/",
                    "error": None,
                },
                {
                    "target_url": "https://bing.com/",
                    "ok": False,
                    "status_code": None,
                    "elapsed_ms": 15,
                    "final_url": None,
                    "error": "Proxy test failed: ConnectTimeout",
                },
            ]
        },
    )
    tested = client.post(
        "/api/v1/network-proxy/test",
        json={
            "url": "http://127.0.0.1:7890",
            "username": "proxy-user",
        },
        headers=headers,
    )
    assert tested.status_code == 200
    assert [result["target_url"] for result in tested.json()["results"]] == [
        "https://google.com/",
        "https://bing.com/",
    ]
    assert tested.json()["results"][1]["ok"] is False

    invalid = client.patch(
        "/api/v1/network-proxy",
        json={
            "enabled": True,
            "url": "http://127.0.0.1:7890",
            "feed_modes": {"999999": "custom"},
            "llm_connection_modes": {},
        },
        headers=headers,
    )
    assert invalid.status_code == 400


def test_custom_proxy_test_uses_form_values_and_disables_system_environment(
    db_factory,
    settings,
    monkeypatch,
):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def get(self, url, headers):
            request = httpx.Request("GET", url, headers=headers)
            return httpx.Response(204, request=request)

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr("backend.app.network_proxy.httpx.Client", FakeClient)
    with db_factory() as db:
        result = run_custom_proxy_test(
            db,
            url="http://127.0.0.1:7890",
            username="proxy user",
            password="proxy/password",
            use_saved_password=False,
            test_url="https://example.com/",
            settings=settings,
        )

    assert result["status_code"] == 204
    assert captured["proxy"] == "http://proxy%20user:proxy%2Fpassword@127.0.0.1:7890"
    assert captured["trust_env"] is False
    assert captured["closed"] is True

    with db_factory() as db:
        dual_result = run_custom_proxy_targets_test(
            db,
            url="http://127.0.0.1:7890",
            username=None,
            password=None,
            use_saved_password=False,
            settings=settings,
        )
    assert [result["target_url"] for result in dual_result["results"]] == [
        "https://google.com/",
        "https://bing.com/",
    ]
