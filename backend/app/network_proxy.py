from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .models import Feed, LLMConnection, NetworkProxyConfig
from .secrets import SecretCipher, secret_hint

SUPPORTED_PROXY_SCHEMES = {"http", "https", "socks5"}
PROXY_MODES = {"custom", "system", "direct"}
TRANSLATION_PROXY_SERVICES = ("google-gtx", "deepl", "google-cloud")
PROXY_TEST_TARGETS = ("https://google.com/", "https://bing.com/")
_PASSWORD_CONTEXT = "network_proxy:1:password"


class NetworkProxyTestError(RuntimeError):
    pass


@dataclass(frozen=True)
class HttpRoute:
    proxy: str | None
    trust_env: bool


def validate_proxy_url(value: str) -> str:
    url = value.strip().rstrip("/")
    if not url:
        return ""
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in SUPPORTED_PROXY_SCHEMES:
        raise ValueError("Proxy URL must use http://, https://, or socks5://")
    if not parsed.hostname:
        raise ValueError("Proxy URL must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Enter proxy credentials in the separate username and password fields")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Proxy URL cannot include a path, query, or fragment")
    try:
        if parsed.port is not None and not (1 <= parsed.port <= 65535):
            raise ValueError("Proxy port must be between 1 and 65535")
    except ValueError as exc:
        raise ValueError("Proxy URL contains an invalid port") from exc
    return url


def get_network_proxy_config(db: Session) -> NetworkProxyConfig | None:
    return db.get(NetworkProxyConfig, 1)


def running_in_container() -> bool:
    if Path("/.dockerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8")
    except OSError:
        return False
    return any(marker in cgroup for marker in ("docker", "containerd", "kubepods"))


def network_proxy_summary(db: Session) -> dict:
    config = get_network_proxy_config(db)
    return {
        "enabled": bool(config and config.enabled),
        "url": config.url if config else "",
        "username": config.username if config else None,
        "password_configured": bool(config and config.password_encrypted),
        "password_hint": config.password_hint if config else None,
        "global_mode": config.global_mode if config else "direct",
        "running_in_container": running_in_container(),
        "feed_modes": {
            row.id: row.proxy_mode
            for row in db.scalars(select(Feed).order_by(Feed.id))
        },
        "llm_connection_modes": {
            row.id: row.proxy_mode
            for row in db.scalars(select(LLMConnection).order_by(LLMConnection.id))
        },
        "translation_service_modes": {
            service: (
                (config.translation_service_modes or {}).get(service, "direct")
                if config
                else "direct"
            )
            for service in TRANSLATION_PROXY_SERVICES
        },
    }


def _validate_modes(
    db: Session,
    model: type[Feed] | type[LLMConnection],
    modes: dict[int, str],
    target_name: str,
) -> None:
    invalid_modes = set(modes.values()) - PROXY_MODES
    if invalid_modes:
        raise ValueError(f"Unsupported proxy mode: {sorted(invalid_modes)[0]}")
    ids = set(modes)
    existing_ids = (
        set(db.scalars(select(model.id).where(model.id.in_(ids)))) if ids else set()
    )
    if existing_ids != ids:
        raise ValueError(f"One or more selected {target_name} do not exist")


def save_network_proxy_config(
    db: Session,
    *,
    enabled: bool,
    url: str,
    username: str | None,
    password: str | None,
    clear_password: bool,
    global_mode: str,
    feed_modes: dict[int, str],
    llm_connection_modes: dict[int, str],
    translation_service_modes: dict[str, str],
    settings: Settings | None = None,
) -> NetworkProxyConfig:
    settings = settings or get_settings()
    normalized_url = validate_proxy_url(url)
    if enabled and not normalized_url:
        raise ValueError("Proxy URL is required when the custom proxy is enabled")
    if global_mode not in PROXY_MODES:
        raise ValueError(f"Unsupported proxy mode: {global_mode}")
    _validate_modes(db, Feed, feed_modes, "feeds")
    _validate_modes(db, LLMConnection, llm_connection_modes, "LLM connections")
    unsupported_services = (
        set(translation_service_modes) - set(TRANSLATION_PROXY_SERVICES)
    )
    if unsupported_services:
        raise ValueError(
            f"Unsupported translation service: {sorted(unsupported_services)[0]}"
        )
    invalid_translation_modes = (
        set(translation_service_modes.values()) - PROXY_MODES
    )
    if invalid_translation_modes:
        raise ValueError(
            f"Unsupported proxy mode: {sorted(invalid_translation_modes)[0]}"
        )

    config = get_network_proxy_config(db)
    if config is None:
        config = NetworkProxyConfig(id=1)
        db.add(config)
    config.enabled = enabled
    config.url = normalized_url
    config.username = username.strip() if username and username.strip() else None
    config.global_mode = global_mode
    config.translation_service_modes = {
        service: translation_service_modes.get(service, "direct")
        for service in TRANSLATION_PROXY_SERVICES
    }
    if clear_password:
        config.password_encrypted = None
        config.password_hint = None
    elif password is not None:
        config.password_encrypted = SecretCipher(settings).encrypt(
            password,
            context=_PASSWORD_CONTEXT,
        )
        config.password_hint = secret_hint(password)
    if config.password_encrypted and not config.username:
        raise ValueError("A proxy username is required when a password is configured")

    db.execute(update(Feed).values(proxy_mode="direct"))
    for mode in PROXY_MODES - {"direct"}:
        ids = [target_id for target_id, value in feed_modes.items() if value == mode]
        if ids:
            db.execute(
                update(Feed).where(Feed.id.in_(ids)).values(proxy_mode=mode)
            )
    db.execute(update(LLMConnection).values(proxy_mode="direct"))
    for mode in PROXY_MODES - {"direct"}:
        ids = [
            target_id
            for target_id, value in llm_connection_modes.items()
            if value == mode
        ]
        if ids:
            db.execute(
                update(LLMConnection)
                .where(LLMConnection.id.in_(ids))
                .values(proxy_mode=mode)
            )
    db.flush()
    return config


def _stored_password(
    config: NetworkProxyConfig | None,
    settings: Settings | None = None,
) -> str | None:
    return (
        SecretCipher(settings).decrypt(
            config.password_encrypted,
            context=_PASSWORD_CONTEXT,
        )
        if config and config.password_encrypted
        else None
    )


def _compose_proxy_url(
    url: str,
    username: str | None,
    password: str | None,
) -> str:
    parsed = urlsplit(url)
    if not username and password:
        raise ValueError("A proxy username is required when a password is configured")
    if not username:
        return url
    credentials = quote(username, safe="")
    if password is not None:
        credentials += f":{quote(password, safe='')}"
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{credentials}@{host}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


def _custom_proxy_url(
    config: NetworkProxyConfig,
    settings: Settings | None = None,
) -> str:
    return _compose_proxy_url(
        config.url,
        config.username,
        _stored_password(config, settings),
    )


def http_route_for_mode(
    db: Session,
    mode: str,
    settings: Settings | None = None,
) -> HttpRoute:
    if mode == "system":
        return HttpRoute(proxy=None, trust_env=True)
    if mode == "custom":
        config = get_network_proxy_config(db)
        if config and config.enabled:
            return HttpRoute(
                proxy=_custom_proxy_url(config, settings),
                trust_env=False,
            )
    return HttpRoute(proxy=None, trust_env=False)


def http_route_for_feed(
    db: Session,
    feed: Feed,
    settings: Settings | None = None,
) -> HttpRoute:
    return http_route_for_mode(db, feed.proxy_mode, settings)


def http_route_for_global(
    db: Session,
    settings: Settings | None = None,
) -> HttpRoute:
    config = get_network_proxy_config(db)
    return http_route_for_mode(
        db,
        config.global_mode if config else "direct",
        settings,
    )


def http_route_for_llm_connection(
    db: Session,
    connection: LLMConnection | None,
    settings: Settings | None = None,
) -> HttpRoute:
    return http_route_for_mode(
        db,
        connection.proxy_mode if connection else "direct",
        settings,
    )


def http_route_for_translation_service(
    db: Session,
    service: str,
    settings: Settings | None = None,
) -> HttpRoute:
    if service not in TRANSLATION_PROXY_SERVICES:
        raise ValueError(f"Unsupported translation service: {service}")
    config = get_network_proxy_config(db)
    modes = config.translation_service_modes or {} if config else {}
    return http_route_for_mode(db, modes.get(service, "direct"), settings)


def _resolve_test_proxy_url(
    db: Session,
    *,
    url: str,
    username: str | None,
    password: str | None,
    use_saved_password: bool,
    settings: Settings | None = None,
) -> str:
    settings = settings or get_settings()
    normalized_url = validate_proxy_url(url)
    if not normalized_url:
        raise ValueError("Proxy URL is required")
    config = get_network_proxy_config(db)
    effective_password = (
        password
        if password is not None
        else (_stored_password(config, settings) if use_saved_password else None)
    )
    return _compose_proxy_url(
        normalized_url,
        username.strip() if username and username.strip() else None,
        effective_password,
    )


def _request_proxy_target(
    proxy_url: str,
    test_url: str,
    settings: Settings,
    client: httpx.Client | None = None,
) -> dict:
    owns_client = client is None
    client = client or httpx.Client(
        timeout=min(settings.request_timeout_seconds, 15),
        follow_redirects=True,
        proxy=proxy_url,
        trust_env=False,
    )
    try:
        started_at = perf_counter()
        response = client.get(
            test_url,
            headers={"User-Agent": "AffogatoRSSReader/0.1 (+self-hosted)"},
        )
        response.raise_for_status()
        return {
            "status_code": response.status_code,
            "elapsed_ms": max(0, round((perf_counter() - started_at) * 1000)),
            "final_url": str(response.url),
        }
    except httpx.HTTPStatusError as exc:
        raise NetworkProxyTestError(
            f"Proxy test target returned HTTP {exc.response.status_code}"
        ) from exc
    except httpx.HTTPError as exc:
        raise NetworkProxyTestError(
            f"Proxy test failed: {exc.__class__.__name__}"
        ) from exc
    finally:
        if owns_client:
            client.close()


def test_custom_proxy(
    db: Session,
    *,
    url: str,
    username: str | None,
    password: str | None,
    use_saved_password: bool,
    test_url: str,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> dict:
    settings = settings or get_settings()
    proxy_url = _resolve_test_proxy_url(
        db,
        url=url,
        username=username,
        password=password,
        use_saved_password=use_saved_password,
        settings=settings,
    )
    return _request_proxy_target(proxy_url, test_url, settings, client)


def test_custom_proxy_targets(
    db: Session,
    *,
    url: str,
    username: str | None,
    password: str | None,
    use_saved_password: bool,
    settings: Settings | None = None,
) -> dict:
    settings = settings or get_settings()
    proxy_url = _resolve_test_proxy_url(
        db,
        url=url,
        username=username,
        password=password,
        use_saved_password=use_saved_password,
        settings=settings,
    )

    def test_target(target_url: str) -> dict:
        started_at = perf_counter()
        try:
            result = _request_proxy_target(proxy_url, target_url, settings)
            return {
                "target_url": target_url,
                "ok": True,
                **result,
                "error": None,
            }
        except NetworkProxyTestError as exc:
            return {
                "target_url": target_url,
                "ok": False,
                "status_code": None,
                "elapsed_ms": max(
                    0, round((perf_counter() - started_at) * 1000)
                ),
                "final_url": None,
                "error": str(exc),
            }

    with ThreadPoolExecutor(max_workers=len(PROXY_TEST_TARGETS)) as executor:
        results = list(executor.map(test_target, PROXY_TEST_TARGETS))
    return {"results": results}
