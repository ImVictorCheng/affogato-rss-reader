from __future__ import annotations

import hashlib
import json
import logging
import ntpath
import os
import posixpath
import re
import shutil
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx
import yaml
from dotenv import dotenv_values

from .config import Settings
from .updates import parse_version


logger = logging.getLogger(__name__)
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_PROJECT = "affogato-rss-reader"
_SERVICE = "reader"
_DOCKER_SOCKET = "/var/run/docker.sock"
_READER_UID = 10001
_READER_GID = 10001


class UpdateRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ValidatedCompose:
    path: Path
    reader_source: str
    reader_tag: str
    reader_service: dict[str, Any]


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    try:
        temporary.chmod(0o600)
        if hasattr(os, "chown"):
            os.chown(temporary, _READER_UID, _READER_GID)
    except OSError:
        pass
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_child(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise UpdateRunnerError("The update request referenced a path outside its allowed directory.")
    return resolved


def _load_request(settings: Settings, path: Path) -> dict[str, Any]:
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UpdateRunnerError("The update request is unreadable.") from exc
    if not isinstance(request, dict) or request.get("schema_version") != 1:
        raise UpdateRunnerError("The update request schema is unsupported.")
    required = {
        "request_id",
        "version",
        "source_repository",
        "image_repository",
        "compose_path",
        "compose_digest",
    }
    if not required.issubset(request):
        raise UpdateRunnerError("The update request is missing required fields.")
    if not re.fullmatch(r"[0-9a-f]{32}", str(request["request_id"])):
        raise UpdateRunnerError("The update request identifier is invalid.")
    parse_version(str(request["version"]))
    if request["source_repository"] != settings.update_github_repository:
        raise UpdateRunnerError("The update request referenced an untrusted source repository.")
    if request["image_repository"] != settings.update_image_repository:
        raise UpdateRunnerError("The update request referenced an untrusted image repository.")
    if not _DIGEST_RE.fullmatch(str(request["compose_digest"])):
        raise UpdateRunnerError("The update request contains an invalid digest.")
    return request


def _service_volumes(service: dict[str, Any], name: str) -> set[str]:
    volumes = service.get("volumes")
    if not isinstance(volumes, list) or not all(isinstance(value, str) for value in volumes):
        raise UpdateRunnerError(f"The {name} service volume configuration is invalid.")
    return set(volumes)


def _service_tmpfs(service: dict[str, Any], name: str) -> set[str]:
    tmpfs = service.get("tmpfs")
    if not isinstance(tmpfs, list) or not all(isinstance(value, str) for value in tmpfs):
        raise UpdateRunnerError(f"The {name} service tmpfs configuration is invalid.")
    return {value.split(":", 1)[0] for value in tmpfs}


def _validate_compose(settings: Settings, request: dict[str, Any]) -> ValidatedCompose:
    update_root = settings.data_dir / "updates"
    path = _safe_child(Path(str(request["compose_path"])), update_root)
    if path.name != "compose.yaml" or not path.is_file():
        raise UpdateRunnerError("The verified Compose update asset is unavailable.")
    if _sha256(path) != str(request["compose_digest"]).removeprefix("sha256:"):
        raise UpdateRunnerError("The Compose update asset failed its final digest check.")
    try:
        compose = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise UpdateRunnerError("The Compose update asset is invalid YAML.") from exc
    if not isinstance(compose, dict) or compose.get("name") != _PROJECT:
        raise UpdateRunnerError("The Compose update asset has an unexpected project name.")
    forbidden_top_level = {"include", "networks", "secrets", "configs"}
    if forbidden_top_level.intersection(compose):
        raise UpdateRunnerError("The Compose update asset contains unsupported top-level resources.")
    release = compose.get("x-affogato-release")
    if not isinstance(release, dict) or release.get("version") != str(request["version"]):
        raise UpdateRunnerError("The Compose update asset has invalid release metadata.")
    reader_digest = str(release.get("reader-digest") or "")
    if set(release) != {"version", "reader-digest"} or not _DIGEST_RE.fullmatch(reader_digest):
        raise UpdateRunnerError("The Compose update asset is missing its immutable image digest.")
    services = compose.get("services")
    if not isinstance(services, dict) or set(services) != {"log-init", "reader", "updater"}:
        raise UpdateRunnerError("The Compose update asset has unexpected services.")
    for name, service in services.items():
        if not isinstance(service, dict):
            raise UpdateRunnerError(f"The {name} service definition is invalid.")

    version = str(request["version"])
    reader_image = f"{settings.update_image_repository}:{version}"
    if any(services[name].get("image") != reader_image for name in services):
        raise UpdateRunnerError("A service image does not match the requested release.")
    if compose.get("x-reader-image") != reader_image:
        raise UpdateRunnerError("The shared image reference does not match the requested release.")

    forbidden = {"build", "privileged", "devices", "cap_add", "pid", "ipc", "entrypoint"}
    for name, service in services.items():
        found = forbidden.intersection(service)
        if found:
            raise UpdateRunnerError(
                f"The {name} service contains forbidden settings: {', '.join(sorted(found))}."
            )

    supported_reader_fields = {
        "image",
        "restart",
        "depends_on",
        "ports",
        "environment",
        "volumes",
        "user",
        "command",
        "working_dir",
        "read_only",
        "cap_drop",
        "security_opt",
        "tmpfs",
        "network_mode",
        "stop_grace_period",
    }
    unsupported_reader_fields = set(services["reader"]) - supported_reader_fields
    if unsupported_reader_fields:
        raise UpdateRunnerError(
            "The reader service contains settings that automatic installation cannot safely "
            f"apply: {', '.join(sorted(unsupported_reader_fields))}."
        )

    expected_volumes = {
        "log-init": {
            "./logs:/app/logs",
            "affogato-rss-reader-update-control:/app/update-control",
        },
        "reader": {
            "affogato-rss-reader-data:/app/data",
            "affogato-rss-reader-secrets:/app/secrets",
            "affogato-rss-reader-update-control:/app/update-control",
            "./logs:/app/logs",
        },
        "updater": {
            "/var/run/docker.sock:/var/run/docker.sock",
            "./:/workspace",
            "affogato-rss-reader-data:/app/data:ro",
            "affogato-rss-reader-update-control:/app/update-control",
        },
    }
    for name, expected in expected_volumes.items():
        if _service_volumes(services[name], name) != expected:
            raise UpdateRunnerError(f"The {name} service has unexpected mounts.")

    expected_tmpfs = {
        "log-init": {"/app/data", "/app/secrets"},
        "updater": {"/tmp", "/app/secrets"},
    }
    for name, expected in expected_tmpfs.items():
        if _service_tmpfs(services[name], name) != expected:
            raise UpdateRunnerError(f"The {name} service has unexpected tmpfs mounts.")

    updater = services["updater"]
    if updater.get("network_mode") != "none" or updater.get("ports"):
        raise UpdateRunnerError("The update helper must have networking disabled and no ports.")
    if updater.get("user") != "0:0" or updater.get("read_only") is not True:
        raise UpdateRunnerError("The update helper must use its restricted root configuration.")
    if updater.get("command") != ["python", "-m", "backend.app.update_runner"]:
        raise UpdateRunnerError("The update helper command is unexpected.")
    if "ALL" not in (updater.get("cap_drop") or []):
        raise UpdateRunnerError("The update helper must drop Linux capabilities.")
    if "no-new-privileges:true" not in (updater.get("security_opt") or []):
        raise UpdateRunnerError("The update helper must disable privilege escalation.")
    expected_updater_environment = {
        "AFFOGATO_RSS_READER_DATA_DIR": "/app/data",
        "AFFOGATO_RSS_READER_UPDATE_CONTROL_DIR": "/app/update-control",
        "AFFOGATO_RSS_READER_UPDATE_WORKSPACE_DIR": "/workspace",
        "AFFOGATO_RSS_READER_UPDATE_GITHUB_REPOSITORY": settings.update_github_repository,
        "AFFOGATO_RSS_READER_UPDATE_IMAGE_REPOSITORY": settings.update_image_repository,
    }
    if updater.get("environment") != expected_updater_environment:
        raise UpdateRunnerError("The update helper environment is unexpected.")

    declared_volumes = compose.get("volumes")
    expected_named_volumes = {
        "affogato-rss-reader-data",
        "affogato-rss-reader-secrets",
        "affogato-rss-reader-update-control",
    }
    if not isinstance(declared_volumes, dict) or set(declared_volumes) != expected_named_volumes:
        raise UpdateRunnerError("The Compose update asset has unexpected named volumes.")
    return ValidatedCompose(
        path=path,
        reader_source=f"{settings.update_image_repository}@{reader_digest}",
        reader_tag=reader_image,
        reader_service=deepcopy(services["reader"]),
    )


def _docker_client() -> httpx.Client:
    if not Path(_DOCKER_SOCKET).exists():
        raise UpdateRunnerError("The Docker Engine socket is unavailable.")
    return httpx.Client(
        transport=httpx.HTTPTransport(uds=_DOCKER_SOCKET),
        base_url="http://docker",
        timeout=httpx.Timeout(60, read=900),
        trust_env=False,
    )


def _engine_request(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    expected: tuple[int, ...] = (200, 201, 204),
    **kwargs: Any,
) -> httpx.Response:
    try:
        response = client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise UpdateRunnerError("The Docker Engine request failed.") from exc
    if response.status_code not in expected:
        raise UpdateRunnerError(
            f"The Docker Engine rejected an update operation (HTTP {response.status_code})."
        )
    return response


def _inspect_image(client: httpx.Client, image: str) -> dict[str, Any]:
    response = _engine_request(client, "GET", f"/images/{quote(image, safe='')}/json")
    payload = response.json()
    if not isinstance(payload, dict):
        raise UpdateRunnerError("The downloaded image metadata is invalid.")
    return payload


def _verify_image(
    client: httpx.Client,
    settings: Settings,
    image: str,
    version: str,
    *,
    expected_digest_ref: str | None = None,
) -> dict[str, Any]:
    payload = _inspect_image(client, image)
    config = payload.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if not isinstance(labels, dict):
        raise UpdateRunnerError("The downloaded image is missing OCI labels.")
    expected_source = f"https://github.com/{settings.update_github_repository}"
    if labels.get("org.opencontainers.image.version") != version:
        raise UpdateRunnerError("The downloaded image version label does not match the release.")
    if labels.get("org.opencontainers.image.source") != expected_source:
        raise UpdateRunnerError("The downloaded image source label is untrusted.")
    if labels.get("org.opencontainers.image.licenses") != "MIT":
        raise UpdateRunnerError("The downloaded image license label is unexpected.")
    if expected_digest_ref is not None:
        repo_digests = payload.get("RepoDigests")
        if not isinstance(repo_digests, list) or expected_digest_ref not in repo_digests:
            raise UpdateRunnerError("Docker did not retain the requested immutable image digest.")
    return payload


def _download_images(
    settings: Settings,
    request: dict[str, Any],
    *,
    client: httpx.Client | None = None,
) -> None:
    validated = _validate_compose(settings, request)
    owns_client = client is None
    client = client or _docker_client()
    try:
        try:
            with client.stream(
                "POST",
                "/images/create",
                params={"fromImage": validated.reader_source},
            ) as response:
                if response.status_code != 200:
                    raise UpdateRunnerError(
                        f"Docker could not pull the update image (HTTP {response.status_code})."
                    )
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise UpdateRunnerError("Docker returned an invalid image-pull response.") from exc
                    if isinstance(event, dict) and event.get("error"):
                        raise UpdateRunnerError("Docker could not pull the update image.")
        except httpx.HTTPError as exc:
            raise UpdateRunnerError("The Docker image download failed.") from exc
        _verify_image(
            client,
            settings,
            validated.reader_source,
            str(request["version"]),
            expected_digest_ref=validated.reader_source,
        )
        repository, tag = validated.reader_tag.rsplit(":", 1)
        _engine_request(
            client,
            "POST",
            f"/images/{quote(validated.reader_source, safe='')}/tag",
            params={"repo": repository, "tag": tag},
        )
        _verify_image(client, settings, validated.reader_tag, str(request["version"]))
    finally:
        if owns_client:
            client.close()


def _reader_container(client: httpx.Client) -> dict[str, Any]:
    filters = {
        "label": [
            f"com.docker.compose.project={_PROJECT}",
            f"com.docker.compose.service={_SERVICE}",
        ]
    }
    response = _engine_request(
        client,
        "GET",
        "/containers/json",
        params={"all": "1", "filters": json.dumps(filters, separators=(",", ":"))},
    )
    containers = response.json()
    if not isinstance(containers, list) or len(containers) != 1:
        raise UpdateRunnerError("Exactly one managed reader container must be running.")
    container_id = containers[0].get("Id") if isinstance(containers[0], dict) else None
    if not isinstance(container_id, str) or not container_id:
        raise UpdateRunnerError("The managed reader container identifier is invalid.")
    inspect = _engine_request(client, "GET", f"/containers/{container_id}/json").json()
    if not isinstance(inspect, dict):
        raise UpdateRunnerError("The managed reader container metadata is invalid.")
    labels = inspect.get("Config", {}).get("Labels", {})
    if not isinstance(labels, dict) or labels.get("com.docker.compose.project") != _PROJECT:
        raise UpdateRunnerError("The reader container is not managed by the expected Compose project.")
    if labels.get("com.docker.compose.service") != _SERVICE:
        raise UpdateRunnerError("The managed container is not the reader service.")
    return inspect


_COMPOSE_VARIABLE_RE = re.compile(
    r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:(?P<operator>:-|-)(?P<default>[^}]*))?\}"
)
_DURATION_RE = re.compile(r"^(?P<value>\d+)(?P<unit>ms|s|m|h)$")


def _current_environment(config: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    environment = config.get("Env")
    if isinstance(environment, list):
        for item in environment:
            if isinstance(item, str) and "=" in item:
                name, value = item.split("=", 1)
                values[name] = value
    return values


def _compose_variables(workspace: Path, current: dict[str, str]) -> dict[str, str]:
    variables: dict[str, str] = {}
    dotenv_path = workspace / ".env"
    try:
        loaded = dotenv_values(dotenv_path) if dotenv_path.is_file() else {}
    except (OSError, ValueError):
        loaded = {}
    for name, value in loaded.items():
        if isinstance(name, str) and value is not None:
            variables[name] = str(value)
    variables.update(current)
    return variables


def _expand_compose_value(value: Any, variables: dict[str, str]) -> str:
    text = str(value)

    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        operator = match.group("operator")
        current = variables.get(name)
        if current is not None and (operator != ":-" or current != ""):
            return current
        if operator in {":-", "-"}:
            return match.group("default") or ""
        return ""

    expanded = _COMPOSE_VARIABLE_RE.sub(replace, text)
    if "${" in expanded:
        raise UpdateRunnerError("The reader service contains unsupported variable interpolation.")
    return expanded


def _reader_environment(
    service: dict[str, Any],
    current: dict[str, str],
    variables: dict[str, str],
) -> list[str] | None:
    configured = service.get("environment")
    if configured is None:
        return None
    if not isinstance(configured, dict):
        raise UpdateRunnerError("The reader service environment is invalid.")
    resolved: list[str] = []
    for name, value in configured.items():
        if not isinstance(name, str) or not name:
            raise UpdateRunnerError("The reader service environment is invalid.")
        if isinstance(value, str) and "${" in value and name in current:
            resolved_value = current[name]
        elif value is None and name in variables:
            resolved_value = variables[name]
        elif value is None:
            resolved_value = ""
        else:
            resolved_value = _expand_compose_value(value, variables)
        resolved.append(f"{name}={resolved_value}")
    return resolved


def _parse_volume(value: str) -> tuple[str, str, str]:
    match = re.fullmatch(r"(?P<source>.+):(?P<target>/[^:]+)(?::(?P<mode>[^:]+))?", value)
    if match is None:
        raise UpdateRunnerError("The reader service contains an unsupported volume mount.")
    return match.group("source"), match.group("target"), match.group("mode") or "rw"


def _current_mounts(host_config: dict[str, Any]) -> dict[str, tuple[str, str]]:
    mounts: dict[str, tuple[str, str]] = {}
    binds = host_config.get("Binds")
    if not isinstance(binds, list):
        return mounts
    for bind in binds:
        if not isinstance(bind, str):
            continue
        try:
            source, target, mode = _parse_volume(bind)
        except UpdateRunnerError:
            continue
        mounts[target] = (source, mode)
    return mounts


def _host_workspace(current_mounts: dict[str, tuple[str, str]]) -> str | None:
    logs = current_mounts.get("/app/logs")
    if logs is None:
        return None
    source = logs[0]
    path_module = ntpath if re.match(r"^[A-Za-z]:[\\/]", source) else posixpath
    return path_module.dirname(source.rstrip("/\\"))


def _reader_binds(service: dict[str, Any], host_config: dict[str, Any]) -> list[str]:
    configured = service.get("volumes")
    if not isinstance(configured, list) or not all(isinstance(value, str) for value in configured):
        raise UpdateRunnerError("The reader service volume configuration is invalid.")
    current = _current_mounts(host_config)
    workspace = _host_workspace(current)
    binds: list[str] = []
    for value in configured:
        source, target, mode = _parse_volume(value)
        if target in current:
            resolved_source = current[target][0]
        elif source.startswith("./"):
            if workspace is None:
                raise UpdateRunnerError("A new reader bind mount cannot be resolved automatically.")
            path_module = ntpath if re.match(r"^[A-Za-z]:[\\/]", workspace) else posixpath
            resolved_source = path_module.normpath(path_module.join(workspace, source[2:]))
        elif source.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", source):
            resolved_source = source
        else:
            resolved_source = f"{_PROJECT}_{source}"
        binds.append(f"{resolved_source}:{target}:{mode}")
    return binds


def _reader_ports(
    service: dict[str, Any],
    host_config: dict[str, Any],
    variables: dict[str, str],
) -> tuple[dict[str, dict[str, str]], dict[str, list[dict[str, str]]]]:
    configured = service.get("ports")
    if configured is None:
        return {}, {}
    if not isinstance(configured, list):
        raise UpdateRunnerError("The reader service port configuration is invalid.")
    current = host_config.get("PortBindings")
    current_bindings = current if isinstance(current, dict) else {}
    exposed: dict[str, dict[str, str]] = {}
    bindings: dict[str, list[dict[str, str]]] = {}
    for value in configured:
        expanded = _expand_compose_value(value, variables)
        parts = expanded.rsplit(":", 2)
        if len(parts) == 1:
            host_ip, host_port, container_port = "", parts[0], parts[0]
        elif len(parts) == 2:
            host_ip, host_port, container_port = "", parts[0], parts[1]
        else:
            host_ip, host_port, container_port = parts
        container_key = container_port if "/" in container_port else f"{container_port}/tcp"
        if not re.fullmatch(r"\d+/(?:tcp|udp|sctp)", container_key):
            raise UpdateRunnerError("The reader service contains an invalid container port.")
        exposed[container_key] = {}
        existing = current_bindings.get(container_key)
        if isinstance(existing, list):
            bindings[container_key] = deepcopy(existing)
        else:
            bindings[container_key] = [{"HostIp": host_ip, "HostPort": host_port}]
    return exposed, bindings


def _duration_seconds(value: Any) -> int:
    match = _DURATION_RE.fullmatch(str(value))
    if match is None:
        raise UpdateRunnerError("The reader service contains an invalid stop grace period.")
    amount = int(match.group("value"))
    unit = match.group("unit")
    if unit == "ms":
        return max(1, (amount + 999) // 1000)
    return amount * {"s": 1, "m": 60, "h": 3600}[unit]


def _new_container_payload(
    inspect: dict[str, Any],
    image: str,
    reader_service: dict[str, Any],
    workspace: Path,
) -> dict[str, Any]:
    current_config = inspect.get("Config")
    current_host_config = inspect.get("HostConfig")
    if not isinstance(current_config, dict) or not isinstance(current_host_config, dict):
        raise UpdateRunnerError("The reader container configuration is incomplete.")
    current_environment = _current_environment(current_config)
    variables = _compose_variables(workspace, current_environment)
    exposed_ports, port_bindings = _reader_ports(reader_service, current_host_config, variables)

    labels = current_config.get("Labels")
    compose_labels = {
        key: value
        for key, value in (labels.items() if isinstance(labels, dict) else [])
        if isinstance(key, str)
        and isinstance(value, str)
        and key.startswith("com.docker.compose.")
        and key not in {"com.docker.compose.config-hash", "com.docker.compose.image"}
    }
    config: dict[str, Any] = {"Image": image, "Labels": compose_labels}
    environment = _reader_environment(reader_service, current_environment, variables)
    if environment is not None:
        config["Env"] = environment
    if exposed_ports:
        config["ExposedPorts"] = exposed_ports
    field_map = {"user": "User", "working_dir": "WorkingDir", "command": "Cmd"}
    for compose_key, engine_key in field_map.items():
        if compose_key in reader_service:
            config[engine_key] = deepcopy(reader_service[compose_key])
    if "stop_grace_period" in reader_service:
        config["StopTimeout"] = _duration_seconds(reader_service["stop_grace_period"])

    restart = str(reader_service.get("restart") or "no")
    host_config: dict[str, Any] = {
        "Binds": _reader_binds(reader_service, current_host_config),
        "PortBindings": port_bindings,
        "RestartPolicy": {"Name": restart, "MaximumRetryCount": 0},
        "ReadonlyRootfs": reader_service.get("read_only") is True,
    }
    for compose_key, engine_key in {
        "cap_drop": "CapDrop",
        "security_opt": "SecurityOpt",
    }.items():
        value = reader_service.get(compose_key)
        if value is not None:
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise UpdateRunnerError(f"The reader service {compose_key} setting is invalid.")
            host_config[engine_key] = deepcopy(value)
    tmpfs = reader_service.get("tmpfs")
    if tmpfs is not None:
        if not isinstance(tmpfs, list) or not all(isinstance(item, str) for item in tmpfs):
            raise UpdateRunnerError("The reader service tmpfs setting is invalid.")
        host_config["Tmpfs"] = {
            item.split(":", 1)[0]: (item.split(":", 1)[1] if ":" in item else "")
            for item in tmpfs
        }

    endpoints: dict[str, dict[str, Any]] = {}
    network_mode = reader_service.get("network_mode")
    if network_mode is None:
        existing_network_mode = current_host_config.get("NetworkMode")
        if isinstance(existing_network_mode, str):
            host_config["NetworkMode"] = existing_network_mode
        networks = inspect.get("NetworkSettings", {}).get("Networks", {})
    else:
        if not isinstance(network_mode, str):
            raise UpdateRunnerError("The reader service network mode is invalid.")
        host_config["NetworkMode"] = network_mode
        networks = {}
    if isinstance(networks, dict):
        for name, network in networks.items():
            if not isinstance(name, str) or not isinstance(network, dict):
                continue
            endpoint: dict[str, Any] = {}
            aliases = network.get("Aliases")
            if isinstance(aliases, list):
                endpoint["Aliases"] = [alias for alias in aliases if isinstance(alias, str)]
            driver_opts = network.get("DriverOpts")
            if isinstance(driver_opts, dict):
                endpoint["DriverOpts"] = deepcopy(driver_opts)
            endpoints[name] = endpoint
    payload: dict[str, Any] = {**config, "HostConfig": host_config}
    if endpoints:
        payload["NetworkingConfig"] = {"EndpointsConfig": endpoints}
    return payload


def _wait_until_ready(client: httpx.Client, container_id: str, timeout: float = 180) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        inspect = _engine_request(client, "GET", f"/containers/{container_id}/json").json()
        state = inspect.get("State") if isinstance(inspect, dict) else None
        if not isinstance(state, dict):
            raise UpdateRunnerError("The replacement reader state is invalid.")
        health = state.get("Health")
        if isinstance(health, dict):
            if health.get("Status") == "healthy":
                return
            if health.get("Status") == "unhealthy":
                raise UpdateRunnerError("The replacement reader failed its health check.")
        elif state.get("Running") is True:
            return
        if state.get("Status") in {"dead", "exited"}:
            raise UpdateRunnerError("The replacement reader stopped before becoming healthy.")
        time.sleep(2)
    raise UpdateRunnerError("The replacement reader did not become healthy in time.")


def _replace_compose_file(settings: Settings, asset: Path) -> tuple[Path, Path]:
    workspace = settings.update_workspace_dir.resolve()
    target = _safe_child(workspace / "compose.yaml", workspace)
    if not target.is_file():
        raise UpdateRunnerError("The active Compose file is not mounted into the update helper.")
    previous = _safe_child(workspace / "compose.previous.yaml", workspace)
    temporary_backup = _safe_child(workspace / f".compose.previous.{uuid4().hex}.tmp", workspace)
    shutil.copy2(target, temporary_backup)
    os.replace(temporary_backup, previous)
    temporary_target = _safe_child(workspace / f".compose.{uuid4().hex}.tmp", workspace)
    shutil.copy2(asset, temporary_target)
    os.replace(temporary_target, target)
    return target, previous


def _restore_compose_file(target: Path, previous: Path) -> None:
    if previous.is_file():
        temporary = target.with_name(f".compose.rollback.{uuid4().hex}.tmp")
        shutil.copy2(previous, temporary)
        os.replace(temporary, target)


def _verify_backup(settings: Settings, request: dict[str, Any]) -> None:
    backup_value = request.get("backup_path")
    if not isinstance(backup_value, str):
        raise UpdateRunnerError("The required pre-update backup is missing.")
    backup = _safe_child(Path(backup_value), settings.data_dir / "backups")
    if not backup.is_file() or backup.stat().st_size == 0:
        raise UpdateRunnerError("The required pre-update backup is unavailable.")


def _install(
    settings: Settings,
    request: dict[str, Any],
    *,
    client: httpx.Client | None = None,
) -> None:
    validated = _validate_compose(settings, request)
    _verify_backup(settings, request)
    if parse_version(str(request["version"])) <= parse_version(settings.version):
        raise UpdateRunnerError("The requested release is not newer than the installed version.")
    owns_client = client is None
    client = client or _docker_client()
    target: Path | None = None
    previous: Path | None = None
    old_id: str | None = None
    old_name: str | None = None
    rollback_name: str | None = None
    old_renamed = False
    new_id: str | None = None
    try:
        _verify_image(
            client,
            settings,
            validated.reader_tag,
            str(request["version"]),
            expected_digest_ref=validated.reader_source,
        )
        current = _reader_container(client)
        old_id = str(current.get("Id") or "")
        old_name = str(current.get("Name") or "").lstrip("/")
        if not old_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]+", old_name):
            raise UpdateRunnerError("The managed reader container name is invalid.")
        target, previous = _replace_compose_file(settings, validated.path)
        rollback_name = f"{old_name}-rollback-{str(request['request_id'])[:8]}"
        _engine_request(
            client,
            "POST",
            f"/containers/{old_id}/stop",
            params={"t": "30"},
            expected=(204, 304),
        )
        _engine_request(
            client,
            "POST",
            f"/containers/{old_id}/rename",
            params={"name": rollback_name},
        )
        old_renamed = True
        create = _engine_request(
            client,
            "POST",
            "/containers/create",
            params={"name": old_name},
            json=_new_container_payload(
                current,
                validated.reader_tag,
                validated.reader_service,
                settings.update_workspace_dir,
            ),
        ).json()
        new_id = create.get("Id") if isinstance(create, dict) else None
        if not isinstance(new_id, str) or not new_id:
            raise UpdateRunnerError("Docker did not return the replacement container identifier.")
        _engine_request(client, "POST", f"/containers/{new_id}/start")
        _wait_until_ready(client, new_id)
        _engine_request(
            client,
            "DELETE",
            f"/containers/{old_id}",
            params={"force": "true", "v": "false"},
        )
    except Exception:
        if new_id:
            try:
                _engine_request(
                    client,
                    "DELETE",
                    f"/containers/{new_id}",
                    params={"force": "true", "v": "false"},
                    expected=(204, 404),
                )
            except Exception:
                logger.exception("Could not remove the failed replacement reader")
        if old_id:
            try:
                if old_renamed and old_name:
                    _engine_request(
                        client,
                        "POST",
                        f"/containers/{old_id}/rename",
                        params={"name": old_name},
                    )
                _engine_request(
                    client,
                    "POST",
                    f"/containers/{old_id}/start",
                    expected=(204, 304),
                )
            except Exception:
                logger.exception("Automatic reader rollback failed")
        if target is not None and previous is not None:
            try:
                _restore_compose_file(target, previous)
            except Exception:
                logger.exception("Could not restore the previous Compose file")
        raise
    finally:
        if owns_client:
            client.close()


def _write_result(
    filename: str,
    control_dir: Path,
    request: dict[str, Any],
    *,
    success: bool,
    error: str | None,
) -> None:
    _atomic_json(
        control_dir / filename,
        {
            "schema_version": 1,
            "request_id": request.get("request_id"),
            "version": request.get("version"),
            "success": success,
            "error": error,
            "finished_at": _iso_now(),
        },
    )


def _process_request(
    settings: Settings,
    path: Path,
    result_filename: str,
    operation: Any,
) -> None:
    request: dict[str, Any] = {}
    try:
        request = _load_request(settings, path)
        logger.info("Processing Affogato RSS Reader %s update to %s", path.stem, request["version"])
        operation(settings, request)
        _write_result(result_filename, settings.effective_update_control_dir, request, success=True, error=None)
    except Exception as exc:
        logger.exception("Update helper operation failed")
        _write_result(
            result_filename,
            settings.effective_update_control_dir,
            request,
            success=False,
            error=str(exc)[:1000] or exc.__class__.__name__,
        )
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    control_dir = settings.effective_update_control_dir
    control_dir.mkdir(parents=True, exist_ok=True)
    install_path = control_dir / "install-request.json"
    download_path = control_dir / "download-request.json"
    logger.info("Affogato update helper started")
    while True:
        _atomic_json(
            control_dir / "heartbeat.json",
            {"schema_version": 1, "updated_at": _iso_now()},
        )
        if download_path.is_file():
            _process_request(settings, download_path, "download-result.json", _download_images)
        if install_path.is_file():
            _process_request(settings, install_path, "install-result.json", _install)
        time.sleep(settings.update_runner_poll_seconds)


if __name__ == "__main__":
    run()
