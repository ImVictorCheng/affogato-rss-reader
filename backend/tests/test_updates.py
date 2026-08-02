from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import yaml

from backend.app.update_runner import (
    UpdateRunnerError,
    _atomic_json,
    _download_images,
    _install,
    _new_container_payload,
    _validate_compose,
)
from backend.app.updates import (
    check_for_updates,
    parse_version,
    request_update_install,
    update_check_due,
    update_status,
)


def release_compose(settings, version: str) -> bytes:
    reader_image = f"{settings.update_image_repository}:{version}"
    return yaml.safe_dump(
        {
            "name": "affogato-rss-reader",
            "x-reader-image": reader_image,
            "x-affogato-release": {
                "version": version,
                "reader-digest": f"sha256:{'1' * 64}",
            },
            "services": {
                "log-init": {
                    "image": reader_image,
                    "tmpfs": ["/app/data", "/app/secrets"],
                    "volumes": [
                        "./logs:/app/logs",
                        "affogato-rss-reader-update-control:/app/update-control",
                    ],
                },
                "reader": {
                    "image": reader_image,
                    "restart": "unless-stopped",
                    "ports": ["${AFFOGATO_RSS_READER_BIND_ADDRESS:-0.0.0.0}:${AFFOGATO_RSS_READER_PORT:-8787}:8787"],
                    "environment": {
                        "AFFOGATO_RSS_READER_DATA_DIR": "/app/data",
                        "NEW_SETTING": "${NEW_SETTING:-from-new-compose}",
                    },
                    "volumes": [
                        "affogato-rss-reader-data:/app/data",
                        "affogato-rss-reader-secrets:/app/secrets",
                        "affogato-rss-reader-update-control:/app/update-control",
                        "./logs:/app/logs",
                    ],
                },
                "updater": {
                    "image": reader_image,
                    "user": "0:0",
                    "command": ["python", "-m", "backend.app.update_runner"],
                    "network_mode": "none",
                    "read_only": True,
                    "cap_drop": ["ALL"],
                    "security_opt": ["no-new-privileges:true"],
                    "tmpfs": ["/tmp", "/app/secrets"],
                    "environment": {
                        "AFFOGATO_RSS_READER_DATA_DIR": "/app/data",
                        "AFFOGATO_RSS_READER_UPDATE_CONTROL_DIR": "/app/update-control",
                        "AFFOGATO_RSS_READER_UPDATE_WORKSPACE_DIR": "/workspace",
                        "AFFOGATO_RSS_READER_UPDATE_GITHUB_REPOSITORY": settings.update_github_repository,
                        "AFFOGATO_RSS_READER_UPDATE_IMAGE_REPOSITORY": settings.update_image_repository,
                    },
                    "volumes": [
                        "/var/run/docker.sock:/var/run/docker.sock",
                        "./:/workspace",
                        "affogato-rss-reader-data:/app/data:ro",
                        "affogato-rss-reader-update-control:/app/update-control",
                    ],
                },
            },
            "volumes": {
                "affogato-rss-reader-data": None,
                "affogato-rss-reader-secrets": None,
                "affogato-rss-reader-update-control": None,
            },
        },
        sort_keys=False,
    ).encode()


def update_client(settings, version: str = "0.3.1") -> httpx.Client:
    compose = release_compose(settings, version)
    digest = hashlib.sha256(compose).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            return httpx.Response(
                200,
                headers={"ETag": '"release-etag"'},
                json={
                    "tag_name": f"v{version}",
                    "html_url": f"https://github.com/ImVictorCheng/affogato-rss-reader/releases/tag/v{version}",
                    "body": "Security and reliability improvements.",
                    "published_at": "2026-08-02T05:00:00Z",
                    "assets": [
                        {
                            "name": f"affogato-rss-reader-compose-{version}.yaml",
                            "browser_download_url": f"https://github.com/ImVictorCheng/affogato-rss-reader/releases/download/v{version}/compose.yaml",
                            "size": len(compose),
                            "digest": f"sha256:{digest}",
                        }
                    ],
                },
            )
        return httpx.Response(200, content=compose)

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_version_parser_and_daily_five_am_schedule(settings):
    enabled = settings.model_copy(update={"update_check_enabled": True, "version": "0.3.0"})
    timezone = ZoneInfo(enabled.timezone)
    assert parse_version("v1.2.3") == (1, 2, 3)
    with pytest.raises(ValueError):
        parse_version("v1.2.3-rc1")
    assert update_check_due(enabled, now=datetime(2026, 8, 2, 4, 59, tzinfo=timezone)) is False
    assert update_check_due(enabled, now=datetime(2026, 8, 2, 5, 0, tzinfo=timezone)) is True


def test_update_check_downloads_and_verifies_release_asset(db_factory, settings):
    enabled = settings.model_copy(update={"update_check_enabled": True, "version": "0.3.0"})
    control = enabled.effective_update_control_dir
    control.mkdir(parents=True, exist_ok=True)
    (control / "heartbeat.json").write_text("{}", encoding="utf-8")
    with update_client(enabled) as client, db_factory() as db:
        status = check_for_updates(db, enabled, client=client)

    assert status["status"] == "downloading"
    assert status["latest_version"] == "0.3.1"
    assert status["downloaded"] is False
    state = json.loads((enabled.data_dir / "updates" / "state.json").read_text(encoding="utf-8"))
    asset = Path(state["asset_path"])
    assert asset.read_bytes() == release_compose(enabled, "0.3.1")
    assert state["github_etag"] == '"release-etag"'
    request = json.loads((control / "download-request.json").read_text(encoding="utf-8"))
    (control / "download-result.json").write_text(
        json.dumps(
            {
                "request_id": request["request_id"],
                "version": "0.3.1",
                "success": True,
                "finished_at": "2026-08-02T05:00:02Z",
            }
        ),
        encoding="utf-8",
    )
    status = update_status(enabled)
    assert status["status"] == "downloaded"
    assert status["downloaded"] is True
    checked_local = datetime.fromisoformat(state["last_checked_at"]).astimezone(
        ZoneInfo(enabled.timezone)
    )
    assert update_check_due(
        enabled,
        now=checked_local.replace(hour=23, minute=0, second=0, microsecond=0),
    ) is False
    assert update_check_due(
        enabled,
        now=(checked_local + timedelta(days=1)).replace(
            hour=5,
            minute=0,
            second=0,
            microsecond=0,
        ),
    ) is True


def test_update_check_304_requeues_download_when_helper_recovers(db_factory, settings):
    enabled = settings.model_copy(update={"update_check_enabled": True, "version": "0.3.0"})
    with update_client(enabled) as client, db_factory() as db:
        first = check_for_updates(db, enabled, client=client)
    assert first["status"] == "available_manual"

    control = enabled.effective_update_control_dir
    control.mkdir(parents=True, exist_ok=True)
    (control / "heartbeat.json").write_text("{}", encoding="utf-8")

    def not_modified(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.github.com"
        assert request.headers["If-None-Match"] == '"release-etag"'
        return httpx.Response(304)

    with httpx.Client(transport=httpx.MockTransport(not_modified)) as client, db_factory() as db:
        retried = check_for_updates(db, enabled, client=client)

    assert retried["status"] == "downloading"
    request = json.loads((control / "download-request.json").read_text(encoding="utf-8"))
    assert request["version"] == "0.3.1"
    assert request["compose_digest"].startswith("sha256:")


def test_install_request_requires_helper_and_creates_backup(db_factory, settings):
    enabled = settings.model_copy(update={"update_check_enabled": True, "version": "0.3.0"})
    control = enabled.effective_update_control_dir
    control.mkdir(parents=True, exist_ok=True)
    (control / "heartbeat.json").write_text("{}", encoding="utf-8")
    with update_client(enabled) as client, db_factory() as db:
        check_for_updates(db, enabled, client=client)
    state = json.loads((enabled.data_dir / "updates" / "state.json").read_text(encoding="utf-8"))
    (control / "download-result.json").write_text(
        json.dumps(
            {
                "request_id": state["download_request_id"],
                "version": "0.3.1",
                "success": True,
                "finished_at": "2026-08-02T05:00:02Z",
            }
        ),
        encoding="utf-8",
    )
    assert update_status(enabled)["downloaded"] is True

    status = request_update_install(enabled)

    assert status["status"] == "installing"
    request = json.loads((control / "install-request.json").read_text(encoding="utf-8"))
    assert request["version"] == "0.3.1"
    assert request["image_repository"] == enabled.update_image_repository
    assert Path(request["backup_path"]).is_file()
    assert "password" not in json.dumps(request).lower()


def test_update_runner_uses_digest_pull_and_expected_tag(settings, tmp_path):
    configured = settings.model_copy(update={"version": "0.3.0"})
    version = "0.3.1"
    asset_dir = configured.data_dir / "updates" / version
    asset_dir.mkdir(parents=True, exist_ok=True)
    asset = asset_dir / "compose.yaml"
    asset.write_bytes(release_compose(configured, version))
    request = {
        "schema_version": 1,
        "request_id": "a" * 32,
        "version": version,
        "source_repository": configured.update_github_repository,
        "image_repository": configured.update_image_repository,
        "compose_path": str(asset),
        "compose_digest": f"sha256:{hashlib.sha256(asset.read_bytes()).hexdigest()}",
    }
    calls: list[tuple[str, str]] = []
    expected_source = f"{configured.update_image_repository}@sha256:{'1' * 64}"

    def handler(http_request: httpx.Request) -> httpx.Response:
        calls.append((http_request.method, http_request.url.path))
        if http_request.method == "POST" and http_request.url.path == "/images/create":
            assert http_request.url.params["fromImage"] == expected_source
            return httpx.Response(200, content=b'{"status":"downloaded"}\n')
        if http_request.method == "GET" and http_request.url.path.startswith("/images/"):
            return httpx.Response(
                200,
                json={
                    "RepoDigests": [expected_source],
                    "Config": {
                        "Labels": {
                            "org.opencontainers.image.version": version,
                            "org.opencontainers.image.source": (
                                f"https://github.com/{configured.update_github_repository}"
                            ),
                            "org.opencontainers.image.licenses": "MIT",
                        }
                    },
                },
            )
        if http_request.method == "POST" and http_request.url.path.endswith("/tag"):
            assert http_request.url.params["repo"] == configured.update_image_repository
            assert http_request.url.params["tag"] == version
            return httpx.Response(201)
        return httpx.Response(404)

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://docker"
    ) as docker:
        _download_images(configured, request, client=docker)

    assert ("POST", "/images/create") in calls
    assert any(method == "POST" and path.endswith("/tag") for method, path in calls)


def test_update_runner_accepts_only_expected_compose_and_replaces_atomically(
    settings,
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    active = workspace / "compose.yaml"
    active.write_text("name: affogato-rss-reader\nservices: {}\n", encoding="utf-8")
    configured = settings.model_copy(
        update={
            "version": "0.3.0",
            "update_workspace_dir": workspace,
        }
    )
    version = "0.3.1"
    asset_dir = configured.data_dir / "updates" / version
    asset_dir.mkdir(parents=True, exist_ok=True)
    asset = asset_dir / "compose.yaml"
    asset.write_bytes(release_compose(configured, version))
    request = {
        "schema_version": 1,
        "request_id": "a" * 32,
        "version": version,
        "source_repository": configured.update_github_repository,
        "image_repository": configured.update_image_repository,
        "compose_path": str(asset),
        "compose_digest": f"sha256:{hashlib.sha256(asset.read_bytes()).hexdigest()}",
    }
    backup_dir = configured.data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / "before-update.db"
    backup.write_bytes(b"sqlite backup")
    request["backup_path"] = str(backup)
    current = {
        "Id": "old-reader-id",
        "Name": "/affogato-rss-reader-reader-1",
        "Config": {
            "Env": [
                "AFFOGATO_RSS_READER_DATA_DIR=/app/data",
                "NEW_SETTING=preserved-user-value",
                "OLD_ONLY=must-disappear",
            ],
            "User": "9999:9999",
            "Cmd": ["uvicorn", "backend.app.main:app"],
            "Entrypoint": ["/old-entrypoint"],
            "WorkingDir": "/old-workdir",
            "Healthcheck": {"Test": ["CMD", "python", "-c", "pass"]},
            "Labels": {
                "com.docker.compose.project": "affogato-rss-reader",
                "com.docker.compose.service": "reader",
                "com.docker.compose.config-hash": "old-config-hash",
                "com.docker.compose.image": "sha256:old-image",
            },
        },
        "HostConfig": {
            "Binds": [
                "affogato-rss-reader-data:/app/data:rw",
                "affogato-rss-reader-secrets:/app/secrets:rw",
                "affogato-rss-reader-update-control:/app/update-control:rw",
                "/host/project/logs:/app/logs:rw",
            ],
            "PortBindings": {
                "8787/tcp": [{"HostIp": "127.0.0.1", "HostPort": "9999"}],
            },
            "NetworkMode": "affogato-rss-reader_default",
            "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
        },
        "NetworkSettings": {
            "Networks": {
                "affogato-rss-reader_default": {"Aliases": ["reader"]},
            }
        },
    }
    operations: list[tuple[str, str]] = []
    created_payload: dict = {}

    def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal created_payload
        path = http_request.url.path
        operations.append((http_request.method, path))
        if http_request.method == "GET" and path.startswith("/images/"):
            return httpx.Response(
                200,
                json={
                    "RepoDigests": [
                        f"{configured.update_image_repository}@sha256:{'1' * 64}"
                    ],
                    "Config": {
                        "Labels": {
                            "org.opencontainers.image.version": version,
                            "org.opencontainers.image.source": (
                                f"https://github.com/{configured.update_github_repository}"
                            ),
                            "org.opencontainers.image.licenses": "MIT",
                        }
                    }
                },
            )
        if http_request.method == "GET" and path == "/containers/json":
            return httpx.Response(200, json=[{"Id": "old-reader-id"}])
        if http_request.method == "GET" and path == "/containers/old-reader-id/json":
            return httpx.Response(200, json=current)
        if http_request.method == "POST" and path.endswith("/stop"):
            return httpx.Response(204)
        if http_request.method == "POST" and path.endswith("/rename"):
            return httpx.Response(204)
        if http_request.method == "POST" and path == "/containers/create":
            created_payload = json.loads(http_request.content)
            return httpx.Response(201, json={"Id": "new-reader-id"})
        if http_request.method == "POST" and path.endswith("/start"):
            return httpx.Response(204)
        if http_request.method == "GET" and path == "/containers/new-reader-id/json":
            return httpx.Response(
                200,
                json={"State": {"Running": True, "Health": {"Status": "healthy"}}},
            )
        if http_request.method == "DELETE" and path == "/containers/old-reader-id":
            return httpx.Response(204)
        return httpx.Response(404)

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://docker"
    ) as docker:
        _install(configured, request, client=docker)

    assert active.read_bytes() == asset.read_bytes()
    assert (workspace / "compose.previous.yaml").is_file()
    assert created_payload["Image"] == f"{configured.update_image_repository}:{version}"
    assert created_payload["HostConfig"]["RestartPolicy"]["Name"] == "unless-stopped"
    assert created_payload["HostConfig"]["PortBindings"]["8787/tcp"][0]["HostPort"] == "9999"
    assert len(created_payload["HostConfig"]["Binds"]) == 4
    assert "AFFOGATO_RSS_READER_DATA_DIR=/app/data" in created_payload["Env"]
    assert "NEW_SETTING=preserved-user-value" in created_payload["Env"]
    assert not any(value.startswith("OLD_ONLY=") for value in created_payload["Env"])
    assert "Cmd" not in created_payload
    assert "Entrypoint" not in created_payload
    assert "WorkingDir" not in created_payload
    assert "User" not in created_payload
    assert "Healthcheck" not in created_payload
    assert "com.docker.compose.config-hash" not in created_payload["Labels"]
    assert "com.docker.compose.image" not in created_payload["Labels"]
    assert ("DELETE", "/containers/old-reader-id") in operations

    malicious = yaml.safe_load(asset.read_text(encoding="utf-8"))
    malicious["services"]["shell"] = {"image": "alpine:latest", "privileged": True}
    asset.write_text(yaml.safe_dump(malicious), encoding="utf-8")
    request["compose_digest"] = f"sha256:{hashlib.sha256(asset.read_bytes()).hexdigest()}"
    with pytest.raises(UpdateRunnerError):
        _validate_compose(configured, request)


def test_public_update_status_does_not_expose_download_paths(settings):
    status = update_status(settings)
    assert "asset_path" not in status
    assert "asset_url" not in status


def test_recreated_container_payload_does_not_reuse_runtime_identity():
    payload = _new_container_payload(
        {
            "Config": {
                "Hostname": "old-id",
                "Env": ["A=B"],
                "Cmd": ["old-command"],
                "Healthcheck": {"Test": ["NONE"]},
                "Labels": {},
            },
            "HostConfig": {
                "ContainerIDFile": "/tmp/old.cid",
                "Binds": ["example-data:/app/data:rw"],
                "NetworkMode": "bridge",
            },
            "NetworkSettings": {"Networks": {}},
        },
        "ghcr.io/example/reader:2.0.0",
        {
            "image": "ghcr.io/example/reader:2.0.0",
            "restart": "unless-stopped",
            "environment": {"A": "${A:-new-default}"},
            "volumes": ["example-data:/app/data"],
        },
        Path("/workspace"),
    )
    assert payload["Image"] == "ghcr.io/example/reader:2.0.0"
    assert "Hostname" not in payload
    assert "Cmd" not in payload
    assert "Healthcheck" not in payload
    assert "ContainerIDFile" not in payload["HostConfig"]


def test_update_runner_result_file_is_handed_to_reader(tmp_path, monkeypatch):
    result = tmp_path / "download-result.json"
    ownership: list[tuple[Path, int, int]] = []
    monkeypatch.setattr(
        "backend.app.update_runner.os.chown",
        lambda path, uid, gid: ownership.append((Path(path), uid, gid)),
        raising=False,
    )
    _atomic_json(result, {"success": True})
    assert json.loads(result.read_text(encoding="utf-8")) == {"success": True}
    assert ownership and ownership[0][1:] == (10001, 10001)


def test_update_actions_require_owner_csrf(authenticated_client, settings, monkeypatch):
    client, _factory, headers = authenticated_client
    status = client.get("/api/v1/updates/status")
    assert status.status_code == 200
    assert status.json()["current_version"] == settings.version

    assert client.post("/api/v1/updates/check").status_code == 403
    monkeypatch.setattr(
        "backend.app.api.check_for_updates",
        lambda db, resolved_settings: update_status(resolved_settings),
    )
    checked = client.post("/api/v1/updates/check", headers=headers)
    assert checked.status_code == 200
    assert checked.json()["status"] == "disabled"
