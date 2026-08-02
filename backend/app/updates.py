from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session

from .backup import backup_database
from .config import Settings, get_settings
from .network_proxy import http_route_for_global
from .secrets import SecretKeyError


GITHUB_API_VERSION = "2026-03-10"
UPDATE_STATE_SCHEMA = 1
MAX_COMPOSE_ASSET_BYTES = 5 * 1024 * 1024
UPDATER_HEARTBEAT_MAX_AGE_SECONDS = 15
_VERSION_RE = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_STATE_LOCK = threading.RLock()
_CHECK_LOCK = threading.Lock()


class UpdateError(RuntimeError):
    pass


class UpdateInstallUnavailable(UpdateError):
    pass


def parse_version(value: str) -> tuple[int, int, int]:
    normalized = value.strip()
    if len(normalized) > 32:
        raise ValueError(f"Unsupported release version: {value}")
    match = _VERSION_RE.fullmatch(normalized)
    if not match:
        raise ValueError(f"Unsupported release version: {value}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso_now() -> str:
    return _utc_now().isoformat()


def _updates_dir(settings: Settings) -> Path:
    return settings.data_dir / "updates"


def _state_path(settings: Settings) -> Path:
    return _updates_dir(settings) / "state.json"


def _default_state(settings: Settings) -> dict[str, Any]:
    return {
        "schema_version": UPDATE_STATE_SCHEMA,
        "current_version": settings.version,
        "latest_version": settings.version,
        "status": "idle" if settings.update_check_enabled else "disabled",
        "release_url": None,
        "release_notes": None,
        "published_at": None,
        "last_checked_at": None,
        "downloaded_at": None,
        "install_requested_at": None,
        "installed_at": None,
        "asset_name": None,
        "asset_url": None,
        "asset_size": None,
        "asset_digest": None,
        "asset_path": None,
        "github_etag": None,
        "request_id": None,
        "download_request_id": None,
        "images_downloaded_version": None,
        "error": None,
    }


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    os.replace(temporary, path)


def _read_state(settings: Settings) -> dict[str, Any]:
    state = _default_state(settings)
    path = _state_path(settings)
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return state
    if not isinstance(stored, dict) or stored.get("schema_version") != UPDATE_STATE_SCHEMA:
        return state
    state.update(stored)
    state["current_version"] = settings.version
    if not settings.update_check_enabled:
        state["status"] = "disabled"
    return state


def _write_state(settings: Settings, state: dict[str, Any]) -> None:
    state["schema_version"] = UPDATE_STATE_SCHEMA
    state["current_version"] = settings.version
    _atomic_json(_state_path(settings), state)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_path_is_safe(settings: Settings, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value).resolve()
    root = _updates_dir(settings).resolve()
    return path if path.is_relative_to(root) else None


def _asset_is_valid(settings: Settings, state: dict[str, Any]) -> bool:
    path = _asset_path_is_safe(settings, state.get("asset_path"))
    expected = str(state.get("asset_digest") or "")
    if path is None or not path.is_file() or not expected.startswith("sha256:"):
        return False
    try:
        return _sha256(path) == expected.removeprefix("sha256:")
    except OSError:
        return False


def updater_is_available(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    heartbeat = settings.effective_update_control_dir / "heartbeat.json"
    try:
        return time.time() - heartbeat.stat().st_mtime <= UPDATER_HEARTBEAT_MAX_AGE_SECONDS
    except OSError:
        return False


def _consume_install_result(settings: Settings, state: dict[str, Any]) -> dict[str, Any]:
    result_path = settings.effective_update_control_dir / "install-result.json"
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return state
    if not isinstance(result, dict):
        return state
    requested_version = str(result.get("version") or "")
    request_id = str(result.get("request_id") or "")
    if state.get("request_id") and request_id != state.get("request_id"):
        return state
    if result.get("success"):
        try:
            installed = parse_version(requested_version)
            current = parse_version(settings.version)
        except ValueError:
            return state
        if current >= installed:
            state.update(
                status="up_to_date",
                latest_version=settings.version,
                installed_at=result.get("finished_at") or _iso_now(),
                error=None,
                request_id=None,
            )
    else:
        state.update(
            status="install_failed",
            error=str(result.get("error") or "The update helper could not install the release.")[:1000],
            request_id=None,
        )
    _write_state(settings, state)
    try:
        result_path.unlink()
    except OSError:
        pass
    return state


def _consume_download_result(settings: Settings, state: dict[str, Any]) -> dict[str, Any]:
    result_path = settings.effective_update_control_dir / "download-result.json"
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return state
    if not isinstance(result, dict):
        return state
    request_id = str(result.get("request_id") or "")
    if state.get("download_request_id") and request_id != state.get("download_request_id"):
        return state
    version = str(result.get("version") or "")
    if result.get("success"):
        try:
            valid_version = parse_version(version) > parse_version(settings.version)
        except ValueError:
            valid_version = False
        if valid_version and _asset_is_valid(settings, state):
            state.update(
                status="downloaded",
                images_downloaded_version=version,
                downloaded_at=result.get("finished_at") or _iso_now(),
                download_request_id=None,
                error=None,
            )
    else:
        state.update(
            status="download_failed",
            download_request_id=None,
            error=str(result.get("error") or "The update images could not be downloaded.")[:1000],
        )
    _write_state(settings, state)
    try:
        result_path.unlink()
    except OSError:
        pass
    return state


def _public_status(settings: Settings, state: dict[str, Any]) -> dict[str, Any]:
    try:
        newer = parse_version(str(state.get("latest_version") or settings.version)) > parse_version(
            settings.version
        )
    except ValueError:
        newer = False
    downloaded = (
        newer
        and _asset_is_valid(settings, state)
        and state.get("images_downloaded_version") == state.get("latest_version")
        and state.get("status") in {"downloaded", "installing"}
    )
    return {
        "current_version": settings.version,
        "latest_version": state.get("latest_version") or settings.version,
        "status": state.get("status") or "idle",
        "release_url": state.get("release_url"),
        "release_notes": state.get("release_notes"),
        "published_at": state.get("published_at"),
        "last_checked_at": state.get("last_checked_at"),
        "downloaded_at": state.get("downloaded_at"),
        "install_requested_at": state.get("install_requested_at"),
        "installed_at": state.get("installed_at"),
        "downloaded": downloaded,
        "downloaded_bytes": state.get("asset_size") if downloaded else None,
        "install_supported": updater_is_available(settings),
        "automatic_checks_enabled": settings.update_check_enabled,
        "check_hour": settings.update_check_hour,
        "error": state.get("error"),
    }


def update_status(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    with _STATE_LOCK:
        state = _consume_download_result(settings, _read_state(settings))
        state = _consume_install_result(settings, state)
        return _public_status(settings, state)


def _request_headers(state: dict[str, Any]) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "AffogatoRSSReader-UpdateChecker",
    }
    if state.get("github_etag"):
        headers["If-None-Match"] = str(state["github_etag"])
    return headers


def _download_asset(
    client: httpx.Client,
    settings: Settings,
    *,
    version: str,
    asset: dict[str, Any],
) -> Path:
    expected_digest = str(asset.get("digest") or "")
    expected_size = int(asset.get("size") or 0)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_digest):
        raise UpdateError("The release asset does not include a valid SHA-256 digest.")
    if not 1 <= expected_size <= MAX_COMPOSE_ASSET_BYTES:
        raise UpdateError("The release Compose asset has an unexpected size.")
    download_url = str(asset.get("browser_download_url") or "")
    expected_prefix = (
        f"https://github.com/{settings.update_github_repository}/releases/download/"
        f"v{version}/"
    )
    if not download_url.startswith(expected_prefix):
        raise UpdateError("The release asset has an unexpected download URL.")

    target_dir = _updates_dir(settings) / version
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "compose.yaml"
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    digest = hashlib.sha256()
    total = 0
    try:
        with client.stream("GET", download_url) as response:
            response.raise_for_status()
            with temporary.open("wb") as output:
                for chunk in response.iter_bytes():
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_COMPOSE_ASSET_BYTES or total > expected_size:
                        raise UpdateError("The release Compose asset exceeded its declared size.")
                    digest.update(chunk)
                    output.write(chunk)
        if total != expected_size:
            raise UpdateError("The release Compose asset size did not match GitHub metadata.")
        if digest.hexdigest() != expected_digest.removeprefix("sha256:"):
            raise UpdateError("The release Compose asset failed SHA-256 verification.")
        os.replace(temporary, target)
        return target
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def _safe_request_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"GitHub returned HTTP {exc.response.status_code} while checking for updates."
    if isinstance(exc, httpx.HTTPError):
        return f"The update request failed ({exc.__class__.__name__})."
    return str(exc)[:1000] or exc.__class__.__name__


def _request_image_download(
    settings: Settings,
    state: dict[str, Any],
    *,
    version: str,
    target: Path,
    previous: dict[str, Any],
    previous_status: str,
) -> None:
    images_ready = (
        state.get("images_downloaded_version") == version
        and _asset_is_valid(settings, state)
    )
    pending_request = settings.effective_update_control_dir / "download-request.json"
    if images_ready:
        state.update(status="downloaded", error=None)
    elif (
        previous_status == "downloading"
        and previous.get("latest_version") == version
        and previous.get("download_request_id")
        and pending_request.is_file()
    ):
        state.update(
            status="downloading",
            download_request_id=previous.get("download_request_id"),
            error=None,
        )
    elif updater_is_available(settings):
        compose_digest = str(state.get("asset_digest") or "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", compose_digest):
            raise UpdateError("The cached release asset digest is invalid.")
        download_request_id = uuid4().hex
        _atomic_json(
            pending_request,
            {
                "schema_version": 1,
                "request_id": download_request_id,
                "version": version,
                "source_repository": settings.update_github_repository,
                "image_repository": settings.update_image_repository,
                "compose_path": str(target),
                "compose_digest": compose_digest,
                "requested_at": _iso_now(),
            },
        )
        state.update(
            status="downloading",
            download_request_id=download_request_id,
            error=None,
        )
    else:
        state.update(
            status="available_manual",
            download_request_id=None,
            error=(
                "The update helper is not running, so the container images "
                "could not be downloaded automatically."
            ),
        )


def check_for_updates(
    db: Session,
    settings: Settings | None = None,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    if not settings.update_check_enabled:
        return update_status(settings)
    if not _CHECK_LOCK.acquire(blocking=False):
        return update_status(settings)
    owns_client = client is None
    with _STATE_LOCK:
        previous = _consume_download_result(settings, _read_state(settings))
        previous = _consume_install_result(settings, previous)
        previous_status = str(previous.get("status") or "idle")
        previous.update(status="checking", error=None)
        _write_state(settings, previous)
    try:
        route = http_route_for_global(db, settings)
        if client is None:
            client = httpx.Client(
                timeout=settings.request_timeout_seconds,
                follow_redirects=True,
                proxy=route.proxy,
                trust_env=route.trust_env,
            )
        api_url = (
            f"https://api.github.com/repos/{settings.update_github_repository}"
            "/releases/latest"
        )
        response = client.get(api_url, headers=_request_headers(previous))
        checked_at = _iso_now()
        if response.status_code == 304:
            with _STATE_LOCK:
                state = _read_state(settings)
                try:
                    already_current = parse_version(
                        str(state.get("latest_version") or settings.version)
                    ) <= parse_version(settings.version)
                except ValueError:
                    already_current = False
                state.update(last_checked_at=checked_at, error=None)
                if already_current:
                    state.update(status="up_to_date")
                elif _asset_is_valid(settings, state):
                    target = _asset_path_is_safe(settings, state.get("asset_path"))
                    if target is None:
                        raise UpdateError("The cached release asset path is invalid.")
                    _request_image_download(
                        settings,
                        state,
                        version=str(state.get("latest_version") or ""),
                        target=target,
                        previous=previous,
                        previous_status=previous_status,
                    )
                else:
                    # Force the next check to fetch complete release metadata and the
                    # asset again instead of repeating an unusable conditional request.
                    state.update(
                        status="check_failed",
                        github_etag=None,
                        error="The cached release asset is unavailable; a full check is required.",
                    )
                _write_state(settings, state)
                return _public_status(settings, state)
        response.raise_for_status()
        release = response.json()
        if not isinstance(release, dict):
            raise UpdateError("GitHub returned invalid release metadata.")
        tag = str(release.get("tag_name") or "")
        latest_tuple = parse_version(tag)
        latest = ".".join(str(part) for part in latest_tuple)
        current_tuple = parse_version(settings.version)
        release_url = str(release.get("html_url") or "")
        expected_release_prefix = (
            f"https://github.com/{settings.update_github_repository}/releases/"
        )
        if not release_url.startswith(expected_release_prefix):
            raise UpdateError("GitHub returned an unexpected release URL.")
        common = {
            "latest_version": latest,
            "release_url": release_url,
            "release_notes": str(release.get("body") or "")[:10_000] or None,
            "published_at": release.get("published_at"),
            "last_checked_at": checked_at,
            "github_etag": response.headers.get("etag"),
            "error": None,
        }
        if latest_tuple <= current_tuple:
            with _STATE_LOCK:
                state = _read_state(settings)
                state.update(common, status="up_to_date")
                _write_state(settings, state)
                return _public_status(settings, state)

        expected_name = f"affogato-rss-reader-compose-{latest}.yaml"
        assets = release.get("assets") if isinstance(release.get("assets"), list) else []
        asset = next(
            (item for item in assets if isinstance(item, dict) and item.get("name") == expected_name),
            None,
        )
        if asset is None:
            with _STATE_LOCK:
                state = _read_state(settings)
                for key in (
                    "asset_name",
                    "asset_url",
                    "asset_size",
                    "asset_digest",
                    "asset_path",
                    "downloaded_at",
                ):
                    state[key] = None
                state.update(
                    common,
                    status="available_manual",
                    error="This release does not contain a compatible automatic-update asset.",
                )
                _write_state(settings, state)
                return _public_status(settings, state)

        with _STATE_LOCK:
            state = _read_state(settings)
            state.update(
                common,
                asset_name=expected_name,
                asset_url=asset.get("browser_download_url"),
                asset_size=int(asset.get("size") or 0),
                asset_digest=asset.get("digest"),
            )
            existing_valid = state.get("latest_version") == latest and _asset_is_valid(settings, state)
        if existing_valid:
            target = _asset_path_is_safe(settings, state.get("asset_path"))
        else:
            target = _download_asset(client, settings, version=latest, asset=asset)
        with _STATE_LOCK:
            state = _read_state(settings)
            asset_state = dict(
                common,
                asset_name=expected_name,
                asset_url=asset.get("browser_download_url"),
                asset_size=int(asset.get("size") or 0),
                asset_digest=asset.get("digest"),
                asset_path=str(target),
            )
            state.update(asset_state)
            _request_image_download(
                settings,
                state,
                version=latest,
                target=target,
                previous=previous,
                previous_status=previous_status,
            )
            _write_state(settings, state)
            return _public_status(settings, state)
    except (
        httpx.HTTPError,
        ValueError,
        UpdateError,
        SecretKeyError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        with _STATE_LOCK:
            state = _read_state(settings)
            state.update(
                status="downloaded" if _asset_is_valid(settings, state) else "check_failed",
                last_checked_at=_iso_now(),
                error=_safe_request_error(exc),
            )
            _write_state(settings, state)
            return _public_status(settings, state)
    finally:
        if owns_client and client is not None:
            client.close()
        _CHECK_LOCK.release()


def update_check_due(
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
) -> bool:
    settings = settings or get_settings()
    if not settings.update_check_enabled:
        return False
    local_now = now or datetime.now(ZoneInfo(settings.timezone))
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=ZoneInfo(settings.timezone))
    if local_now.hour < settings.update_check_hour:
        return False
    with _STATE_LOCK:
        last_checked = _read_state(settings).get("last_checked_at")
    if not last_checked:
        return True
    try:
        checked = datetime.fromisoformat(str(last_checked)).astimezone(
            ZoneInfo(settings.timezone)
        )
    except ValueError:
        return True
    return checked.date() < local_now.date()


def request_update_install(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    with _STATE_LOCK:
        state = _read_state(settings)
        if state.get("status") != "downloaded" or not _asset_is_valid(settings, state):
            raise UpdateError("No verified update is ready to install.")
        if not updater_is_available(settings):
            raise UpdateInstallUnavailable(
                "The Docker Compose update helper is not running; install this release manually."
            )
        latest = str(state.get("latest_version") or "")
        if parse_version(latest) <= parse_version(settings.version):
            raise UpdateError("The downloaded release is not newer than the running version.")
        asset_path = _asset_path_is_safe(settings, state.get("asset_path"))
        if asset_path is None:
            raise UpdateError("The verified update asset is unavailable.")

        try:
            backup = backup_database(settings)
        except (OSError, RuntimeError, ValueError) as exc:
            raise UpdateError(
                "A verified database backup could not be created; the update was not started."
            ) from exc
        request_id = uuid4().hex
        requested_at = _iso_now()
        request = {
            "schema_version": 1,
            "request_id": request_id,
            "version": latest,
            "source_repository": settings.update_github_repository,
            "image_repository": settings.update_image_repository,
            "compose_path": str(asset_path),
            "compose_digest": state["asset_digest"],
            "release_url": state.get("release_url"),
            "backup_path": str(backup),
            "requested_at": requested_at,
        }
        _atomic_json(
            settings.effective_update_control_dir / "install-request.json",
            request,
        )
        state.update(
            status="installing",
            install_requested_at=requested_at,
            request_id=request_id,
            error=None,
        )
        _write_state(settings, state)
        return _public_status(settings, state)
