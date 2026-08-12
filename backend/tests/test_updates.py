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
    _install_after_manifest_verification,
    _load_request,
    _new_container_payload,
    _process_request,
    _validate_compose,
)
from backend.app.updates import (
    UpdateInstallUnavailable,
    check_for_updates,
    parse_version,
    request_update_install,
    update_check_due,
    update_status,
)


def release_compose(settings, version: str) -> bytes:
    root = Path(__file__).resolve().parents[2]
    template_version = (root / "VERSION").read_text(encoding="utf-8").strip()
    compose = (root / "compose.yaml").read_text(encoding="utf-8")
    compose = compose.replace(
        "ghcr.io/OWNER/affogato-rss-reader",
        settings.update_image_repository,
    )
    compose = compose.replace(f'version: "{template_version}"', f'version: "{version}"')
    compose = compose.replace(f":{template_version}@", f":{version}@")
    compose = compose.replace("READER_DIGEST", f"sha256:{'1' * 64}")
    return compose.encode()


def update_client(
    settings,
    version: str = "0.3.1",
    *,
    asset_name: str | None = None,
) -> httpx.Client:
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
                            "name": asset_name
                            or f"affogato-rss-reader-compose-v2-{version}.yaml",
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
                "schema_version": 1,
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


def test_digest_schema_requires_manual_migration_from_legacy_asset(db_factory, settings):
    enabled = settings.model_copy(update={"update_check_enabled": True, "version": "0.3.0"})
    legacy_name = "affogato-rss-reader-compose-0.3.1.yaml"
    with update_client(enabled, asset_name=legacy_name) as client, db_factory() as db:
        status = check_for_updates(db, enabled, client=client)

    assert status["status"] == "available_manual"
    assert "compatible automatic-update asset" in status["message"]
    assert status["error"] is None
    assert not (enabled.data_dir / "updates" / "0.3.1" / "compose.yaml").exists()


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


def test_manual_update_check_runs_when_automatic_checks_are_disabled(db_factory, settings):
    disabled = settings.model_copy(update={"update_check_enabled": False, "version": "0.3.0"})
    updates = disabled.data_dir / "updates"
    updates.mkdir(parents=True, exist_ok=True)
    (updates / "state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "current_version": "0.3.0",
                "latest_version": "0.3.0",
                "status": "disabled",
                "error": "The update request failed (ConnectError).",
            }
        ),
        encoding="utf-8",
    )

    with update_client(disabled, version="0.3.0") as client, db_factory() as db:
        status = check_for_updates(db, disabled, client=client)

    assert status["status"] == "up_to_date"
    assert status["automatic_checks_enabled"] is False
    assert status["error"] is None


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (httpx.ConnectError("[Errno 111] Connection refused"), "refused"),
        (httpx.ConnectError("[SSL: UNEXPECTED_EOF_WHILE_READING]"), "TLS handshake"),
        (httpx.ConnectTimeout("timed out"), "timed out"),
    ],
)
def test_update_network_errors_are_actionable(exc, expected):
    from backend.app.updates import _safe_request_error

    assert expected in _safe_request_error(exc)


def test_install_request_is_fail_closed_until_signed_manifests_exist(db_factory, settings):
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
                "schema_version": 1,
                "request_id": state["download_request_id"],
                "version": "0.3.1",
                "success": True,
                "finished_at": "2026-08-02T05:00:02Z",
            }
        ),
        encoding="utf-8",
    )
    assert update_status(enabled)["downloaded"] is True

    with pytest.raises(UpdateInstallUnavailable, match="signature-verified"):
        request_update_install(enabled)

    assert not (control / "install-request.json").exists()
    status = update_status(enabled)
    assert status["install_supported"] is False
    assert "signature-verified" in status["install_unavailable_reason"]


def test_update_runner_pulls_only_the_immutable_digest(settings, tmp_path):
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
                            "org.opencontainers.image.licenses": "MIT AND Apache-2.0",
                        }
                    },
                },
            )
        return httpx.Response(404)

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://docker"
    ) as docker:
        _download_images(configured, request, client=docker)

    assert ("POST", "/images/create") in calls
    assert not any(path.endswith("/tag") for _method, path in calls)


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
    original_compose = release_compose(configured, version)
    asset.write_bytes(original_compose)
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
                "AFFOGATO_RSS_READER_TIMEZONE=Asia/Shanghai",
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
            # The reader can still write the download directory while the
            # privileged helper is installing. Mutating it after validation
            # must not change the bytes installed into the workspace.
            asset.write_bytes(b"name: attacker-controlled\n")
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
                            "org.opencontainers.image.licenses": "MIT AND Apache-2.0",
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
        _install_after_manifest_verification(configured, request, client=docker)

    assert active.read_bytes() != original_compose
    assert yaml.safe_load(active.read_bytes()) == yaml.safe_load(original_compose)
    assert b"&reader-image" not in active.read_bytes()
    assert b"*reader-image" not in active.read_bytes()
    assert (workspace / "compose.previous.yaml").is_file()
    assert created_payload["Image"] == (
        f"{configured.update_image_repository}:{version}@sha256:{'1' * 64}"
    )
    assert created_payload["HostConfig"]["RestartPolicy"]["Name"] == "unless-stopped"
    assert created_payload["HostConfig"]["PortBindings"]["8787/tcp"][0]["HostPort"] == "9999"
    assert len(created_payload["HostConfig"]["Binds"]) == 4
    assert "AFFOGATO_RSS_READER_DATA_DIR=/app/data" in created_payload["Env"]
    assert "AFFOGATO_RSS_READER_TIMEZONE=Asia/Shanghai" in created_payload["Env"]
    assert not any(value.startswith("OLD_ONLY=") for value in created_payload["Env"])
    assert "Cmd" not in created_payload
    assert "Entrypoint" not in created_payload
    assert "WorkingDir" not in created_payload
    assert "User" not in created_payload
    assert "Healthcheck" not in created_payload
    assert "com.docker.compose.config-hash" not in created_payload["Labels"]
    assert "com.docker.compose.image" not in created_payload["Labels"]
    assert ("DELETE", "/containers/old-reader-id") in operations

    malicious = yaml.safe_load(original_compose.decode())
    malicious["services"]["shell"] = {"image": "alpine:latest", "privileged": True}
    asset.write_text(yaml.safe_dump(malicious), encoding="utf-8")
    request["compose_digest"] = f"sha256:{hashlib.sha256(asset.read_bytes()).hexdigest()}"
    with pytest.raises(UpdateRunnerError):
        _validate_compose(configured, request)


@pytest.mark.parametrize(
    "mutation",
    [
        "top-level-field",
        "log-init-field",
        "log-init-command",
        "reader-field",
        "reader-environment",
        "reader-port",
        "updater-security",
        "tag-only-image",
        "named-volume-options",
        "extra-named-volume",
        "duplicate-service-key",
        "alias",
        "merge-key",
        "recursive-alias",
    ],
)
def test_update_runner_rejects_every_unapproved_compose_shape(settings, mutation):
    configured = settings.model_copy(update={"version": "0.3.0"})
    version = "0.3.1"
    asset_dir = configured.data_dir / "updates" / version
    asset_dir.mkdir(parents=True, exist_ok=True)
    document = yaml.safe_load(release_compose(configured, version))

    if mutation == "top-level-field":
        document["networks"] = {"host": {"external": True}}
    elif mutation == "log-init-field":
        document["services"]["log-init"]["privileged"] = True
    elif mutation == "log-init-command":
        document["services"]["log-init"]["command"] = ["sh", "-c", "id"]
    elif mutation == "reader-field":
        document["services"]["reader"]["user"] = "0:0"
    elif mutation == "reader-environment":
        document["services"]["reader"]["environment"]["LD_PRELOAD"] = "/workspace/x.so"
    elif mutation == "reader-port":
        document["services"]["reader"]["ports"] = ["0.0.0.0:22:22"]
    elif mutation == "updater-security":
        document["services"]["updater"]["security_opt"] = []
    elif mutation == "tag-only-image":
        document["services"]["reader"]["image"] = (
            f"{configured.update_image_repository}:{version}"
        )
    elif mutation == "named-volume-options":
        document["volumes"]["affogato-rss-reader-data"] = {"external": True}
    elif mutation == "extra-named-volume":
        document["volumes"]["attacker"] = None

    if mutation == "duplicate-service-key":
        content = release_compose(configured, version).replace(
            b"  reader:\n",
            b"  reader:\n    restart: always\n",
            1,
        )
    elif mutation == "alias":
        image = f"{configured.update_image_repository}:{version}@sha256:{'1' * 64}"
        content = release_compose(configured, version).replace(
            f"x-reader-image: {image}".encode(),
            f"x-reader-image: &reader-image {image}".encode(),
            1,
        ).replace(
            f"    image: {image}".encode(),
            b"    image: *reader-image",
        )
    elif mutation == "merge-key":
        content = release_compose(configured, version).replace(
            b"  reader:\n",
            b"  reader:\n    <<: &reader-defaults {restart: always}\n",
            1,
        )
    elif mutation == "recursive-alias":
        content = b"name: affogato-rss-reader\nloop: &loop [*loop]\n"
    else:
        content = yaml.safe_dump(document, sort_keys=False).encode()

    asset = asset_dir / "compose.yaml"
    asset.write_bytes(content)
    request = {
        "schema_version": 1,
        "request_id": "a" * 32,
        "version": version,
        "source_repository": configured.update_github_repository,
        "image_repository": configured.update_image_repository,
        "compose_path": str(asset),
        "compose_digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
    }
    with pytest.raises(UpdateRunnerError):
        _validate_compose(configured, request)


def test_public_update_status_does_not_expose_download_paths(settings):
    status = update_status(settings)
    assert "asset_path" not in status
    assert "asset_url" not in status
    assert status["install_supported"] is False
    assert "signature-verified" in status["install_unavailable_reason"]


def test_update_runner_install_operation_is_always_fail_closed(settings):
    with pytest.raises(UpdateRunnerError, match="signature-verified"):
        _install(settings, {})


def test_update_runner_request_schema_is_exact_and_size_bounded(settings, tmp_path):
    request_path = tmp_path / "download-request.json"
    common = {
        "schema_version": 1,
        "request_id": "a" * 32,
        "version": "0.3.1",
        "source_repository": settings.update_github_repository,
        "image_repository": settings.update_image_repository,
        "compose_path": str(settings.data_dir / "updates" / "0.3.1" / "compose.yaml"),
        "compose_digest": f"sha256:{'1' * 64}",
        "requested_at": "2026-08-10T08:00:00+00:00",
    }
    request_path.write_text(json.dumps(common), encoding="utf-8")
    assert _load_request(settings, request_path, "download") == common

    request_path.write_text(json.dumps({**common, "unexpected": True}), encoding="utf-8")
    with pytest.raises(UpdateRunnerError, match="unexpected fields"):
        _load_request(settings, request_path, "download")

    install = {**common, "backup_path": str(settings.data_dir / "backups" / "before.db")}
    request_path.write_text(json.dumps(install), encoding="utf-8")
    assert _load_request(settings, request_path, "install") == install
    with pytest.raises(UpdateRunnerError, match="unexpected fields"):
        _load_request(settings, request_path, "download")

    request_path.write_bytes(b" " * (64 * 1024 + 1))
    with pytest.raises(UpdateRunnerError, match="small regular file"):
        _load_request(settings, request_path, "download")


def test_update_runner_claims_request_before_processing_and_preserves_replacement(
    settings,
):
    control = settings.effective_update_control_dir
    control.mkdir(parents=True, exist_ok=True)
    queue_path = control / "download-request.json"
    request = {
        "schema_version": 1,
        "request_id": "a" * 32,
        "version": "0.3.1",
        "source_repository": settings.update_github_repository,
        "image_repository": settings.update_image_repository,
        "compose_path": str(settings.data_dir / "updates" / "0.3.1" / "compose.yaml"),
        "compose_digest": f"sha256:{'1' * 64}",
        "requested_at": "2026-08-10T08:00:00+00:00",
    }
    queue_path.write_text(json.dumps(request), encoding="utf-8")

    def operation(_settings, loaded):
        assert loaded == request
        queue_path.write_text("replacement request", encoding="utf-8")

    _process_request(
        settings,
        queue_path,
        "download-result.json",
        operation,
        "download",
    )

    assert queue_path.read_text(encoding="utf-8") == "replacement request"
    assert not list(control.glob(".download-request.json.*.claimed"))


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

    install = client.post("/api/v1/updates/install", headers=headers)
    assert install.status_code == 503
    assert "signature-verified" in install.json()["detail"]
